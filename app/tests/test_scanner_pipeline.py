"""Scanner pipeline end to end on synthetic archives: calendar -> quotes -> fair -> models -> value -> coupons ->
register -> settlement (with statistics fetched from a fake API) -> closing line value -> page, CSV and run log.

Everything lives in ``tmp_path``: an odds archive written by the real :class:`OddsArchive`, a fixture directory
refreshed through the real collector from a fake API client, and a model cache fitted by the real
:func:`fit_live_models` on a small synthetic league.  The real archives are never touched.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import model_buk.scanner.live_models as lm
from model_buk.ingestion.api_football_odds_archive import OddsArchive
from model_buk.ingestion.fixture_directory import FixtureDirectory
from model_buk.scanner.config import RunSettings, ScanConfig, value_fields
from model_buk.scanner.engine import ScannerConfig
from model_buk.scanner.pipeline import (
    ForecastLog,
    fetch_settlement_statistics,
    load_fixture_frame,
    PipelineResult,
    refresh_directory,
    run_pipeline,
    statistics_needed,
)
from model_buk.scanner.register import ScannerRegister

UTC = timezone.utc
LEAGUE_ID = 39
FIT_AS_OF = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
SCAN_1 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SCAN_2 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
KICKOFF_4 = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
TEAMS = [3900 + i for i in range(10)]
NAMES = {team: f"Klub {chr(65 + i)}" for i, team in enumerate(TEAMS)}
# fixture -> (home, away, final score, half-time score)
MATCHES = {
    501: (TEAMS[0], TEAMS[9], (2, 0), (1, 0)),
    502: (TEAMS[1], TEAMS[8], (0, 1), (0, 0)),
    503: (TEAMS[2], TEAMS[7], (1, 1), (1, 0)),
}
LATER = 504  # kicks off the next day: something to scan in the second run
PINNACLE, BET365 = (4, "Pinnacle"), (8, "Bet365")


# --------------------------------------------------------------------------- #
# Synthetic model cache
# --------------------------------------------------------------------------- #


def round_robin(ids: list[int], round_index: int) -> list[tuple[int, int]]:
    n = len(ids)
    rotating = ids[1:]
    shift = round_index % (n - 1)
    rotating = rotating[-shift:] + rotating[:-shift] if shift else rotating
    order = [ids[0], *rotating]
    pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
    return [(b, a) if (round_index + i) % 2 else (a, b) for i, (a, b) in enumerate(pairs)]


def synthetic_history(n_rounds: int = 60, seed: int = 11) -> pd.DataFrame:
    """Weekly rounds of one ten-team league with Poisson goals (the shape the live models read)."""
    rng = np.random.default_rng(seed)
    attack = dict(zip(TEAMS, rng.normal(0, 0.25, len(TEAMS)), strict=True))
    defence = dict(zip(TEAMS, rng.normal(0, 0.25, len(TEAMS)), strict=True))
    first = pd.Timestamp("2025-08-02T15:00:00Z")
    rows, fixture = [], 10_000
    for r in range(n_rounds):
        kickoff = first + pd.Timedelta(days=7 * r)
        for home, away in round_robin(TEAMS, r):
            fixture += 1
            hg = int(rng.poisson(math.exp(0.45 + attack[home] + defence[away])))
            ag = int(rng.poisson(math.exp(0.25 + attack[away] + defence[home])))
            rows.append(
                {
                    "fixture_id": fixture, "league_id": LEAGUE_ID, "league_code": "AAA",
                    "season": kickoff.year if kickoff.month >= 7 else kickoff.year - 1,
                    "round": f"Regular Season - {r % 18 + 1}", "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "status_short": "FT", "venue_id": 1, "referee": f"ref{int(rng.integers(0, 4))}",
                    "home_team_id": home, "home_team": NAMES[home], "away_team_id": away, "away_team": NAMES[away],
                    "home_goals": hg, "away_goals": ag, "ht_home_goals": min(hg, 1), "ht_away_goals": min(ag, 1),
                    "et_home_goals": np.nan, "et_away_goals": np.nan, "pen_home": np.nan, "pen_away": np.nan,
                    "home_shots": 12.0, "away_shots": 10.0, "home_sot": 4.0, "away_sot": 3.0,
                    "home_corners": 5.0, "away_corners": 4.0, "home_fouls": 11.0, "away_fouls": 11.0,
                    "home_xg": np.nan, "away_xg": np.nan, "home_yellow": 2.0, "away_yellow": 2.0,
                    "home_red": 0.0, "away_red": 0.0, "home_booking": np.nan, "away_booking": np.nan,
                }
            )  # fmt: skip
    return pd.DataFrame(rows)


GOAL_WEIGHTS = {market: {"dixon_coles": 1.0} for market in lm.GOAL_MARKET_NAMES}
GOAL_HYPERPARAMETERS = {"dixon_coles": {m: {"half_life_days": 365.0} for m in lm.GOAL_MARKET_NAMES}}


@pytest.fixture(scope="module")
def model_cache(tmp_path_factory) -> Path:
    cache = tmp_path_factory.mktemp("model_cache")
    config = lm.LiveConfig(
        cache_dir=cache,
        history_db=cache / "no_history.sqlite",
        comparison_db=cache / "no_comparison.sqlite",
        predictions_dir=cache / "no_predictions",
        legacy_booster_dir=None,
    )
    history = synthetic_history()
    stats = pd.DataFrame(columns=["fixture_id", "team_id", "total_shots", "shots_on_goal", "corners", "expected_goals"])
    lm.fit_live_models(
        FIT_AS_OF,
        config=config,
        matches=history,
        team_stats=stats,
        weights=GOAL_WEIGHTS,
        hyperparameters=GOAL_HYPERPARAMETERS,
        families=(),
        log=lambda text: None,
    )
    return cache


# --------------------------------------------------------------------------- #
# Synthetic odds archive
# --------------------------------------------------------------------------- #


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def odds_object(fixture_id: int, kickoff: datetime, update: datetime, books: dict) -> dict:
    """One provider odds object: ``books`` maps (id, name) to {bet_id: (bet_name, {selection: odd})}."""
    return {
        "fixture": {"id": fixture_id, "date": _iso(kickoff)},
        "league": {"id": LEAGUE_ID, "season": 2026},
        "update": _iso(update),
        "bookmakers": [
            {
                "id": book_id,
                "name": name,
                "bets": [
                    {"id": bet_id, "name": bet_name, "values": [{"value": s, "odd": f"{o:.2f}"} for s, o in values.items()]}
                    for bet_id, (bet_name, values) in bets.items()
                ],
            }
            for (book_id, name), bets in books.items()
        ],
    }


def match_winner(home: float, draw: float, away: float) -> tuple[str, dict]:
    return ("Match Winner", {"Home": home, "Draw": draw, "Away": away})


def corners(over: float, under: float) -> tuple[str, dict]:
    return ("Corners Over Under", {"Over 9.5": over, "Under 9.5": under})


def build_odds_archive(root: Path, close_minutes_before: int = 30) -> Path:
    archive = OddsArchive(root)
    early, late = SCAN_1 - timedelta(minutes=10), KICKOFF - timedelta(minutes=close_minutes_before)
    first_board, closing_board = [], []
    for fixture_id in MATCHES:
        pin = {1: match_winner(1.55, 4.30, 6.00)}
        soft = {1: match_winner(1.75, 4.20, 5.50)}  # the home price beats the fair one by about 10%
        if fixture_id == 501:
            pin[45] = corners(1.90, 1.90)
            soft[45] = corners(2.10, 1.75)
        first_board.append(odds_object(fixture_id, KICKOFF, early - timedelta(minutes=10), {PINNACLE: pin, BET365: soft}))
        # the close: Pinnacle shortens the home side, Bet365 is seen again unchanged
        closing_pin = {**pin, 1: match_winner(1.50, 4.50, 6.50)}
        closing_board.append(odds_object(fixture_id, KICKOFF, late - timedelta(minutes=10), {PINNACLE: closing_pin, BET365: soft}))
    archive.record("s1", early, first_board)
    archive.record("s2", late, closing_board)
    next_day = odds_object(
        LATER,
        KICKOFF_4,
        SCAN_2 - timedelta(minutes=20),
        {PINNACLE: {1: match_winner(2.10, 3.40, 3.60)}, BET365: {1: match_winner(2.05, 3.30, 3.50)}},
    )
    archive.record("s3", SCAN_2 - timedelta(minutes=10), [next_day])
    return archive.db_path


# --------------------------------------------------------------------------- #
# Fake API-Football
# --------------------------------------------------------------------------- #


def listing(fixture_id: int, home: int, away: int, kickoff: datetime, *, finished: bool = False,
            score: tuple[int, int] = (None, None), halftime: tuple[int, int] = (None, None)) -> dict:  # fmt: skip
    return {
        "fixture": {
            "id": fixture_id, "referee": None, "timezone": "UTC", "date": kickoff.isoformat(),
            "timestamp": int(kickoff.timestamp()), "venue": {"id": 10, "name": "Stadion", "city": "Miasto"},
            "status": {"long": "Match Finished" if finished else "Not Started", "short": "FT" if finished else "NS",
                       "elapsed": 90 if finished else None, "extra": None},
        },
        "league": {"id": LEAGUE_ID, "name": "Premier League", "country": "England", "season": 2026, "round": "Regular Season - 8"},
        "teams": {"home": {"id": home, "name": NAMES[home]}, "away": {"id": away, "name": NAMES[away]}},
        "goals": {"home": score[0], "away": score[1]},
        "score": {"halftime": {"home": halftime[0], "away": halftime[1]}, "fulltime": {"home": score[0], "away": score[1]},
                  "extratime": {"home": None, "away": None}, "penalty": {"home": None, "away": None}},
    }  # fmt: skip


def stat_block(team_id: int, corners_: int, yellow: int) -> dict:
    return {
        "team": {"id": team_id, "name": NAMES[team_id]},
        "statistics": [{"type": "Corner Kicks", "value": corners_}, {"type": "Yellow Cards", "value": yellow},
                       {"type": "Red Cards", "value": None}],
    }  # fmt: skip


class FakeApi:
    """Serves the calendar by date (before or after the matches), details by ids; counts calls and the quota."""

    def __init__(self, quota: int = 5000) -> None:
        self.quota = quota
        self.finished = False
        self.calls: list[tuple[str, dict]] = []

    def _listing(self, day: str) -> list[dict]:
        out = []
        for fid, (home, away, score, half) in MATCHES.items():
            if KICKOFF.date().isoformat() == day:
                out.append(listing(fid, home, away, KICKOFF, finished=self.finished, score=score if self.finished else (None, None),
                                   halftime=half if self.finished else (None, None)))  # fmt: skip
        if KICKOFF_4.date().isoformat() == day:
            out.append(listing(LATER, TEAMS[3], TEAMS[6], KICKOFF_4))
        return out

    def get(self, endpoint: str, params: dict) -> tuple[dict, dict]:
        self.calls.append((endpoint, dict(params)))
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if endpoint == "/fixtures" and "date" in params:
            return {"paging": {"current": 1, "total": 1}, "response": self._listing(params["date"])}, headers
        if endpoint == "/fixtures" and "ids" in params:
            response = []
            for fid in (int(i) for i in params["ids"].split("-")):
                home, away, score, half = MATCHES[fid]
                obj = listing(fid, home, away, KICKOFF, finished=True, score=score, halftime=half)
                obj["statistics"] = [stat_block(home, 6, 2), stat_block(away, 5, 1)]
                obj["events"] = []
                response.append(obj)
            return {"paging": {"current": 1, "total": 1}, "response": response}, headers
        if endpoint == "/fixtures/statistics":
            return {"paging": {"current": 1, "total": 1}, "response": []}, headers
        raise AssertionError(f"unexpected request {endpoint} {params}")


# --------------------------------------------------------------------------- #
# The full cycle
# --------------------------------------------------------------------------- #


def settings_for(tmp_path: Path, model_cache: Path, **overrides) -> RunSettings:
    values = dict(
        data_root=tmp_path,
        odds_db=tmp_path / "odds" / "odds.sqlite3",
        model_cache_dir=model_cache,
        directory_days_back=1,
        directory_days_ahead=2,
        api_sleep=0.0,
    )
    values.update(overrides)
    return RunSettings(**values)


def clock_at(moment: datetime):
    return lambda: moment


@pytest.fixture
def world(tmp_path, model_cache):
    build_odds_archive(tmp_path / "odds")
    return settings_for(tmp_path, model_cache), FakeApi()


def seed_calendar(settings: RunSettings, api: FakeApi) -> None:
    """List the calendar once (as a run at SCAN_1 would) and forget the calls."""
    refresh_directory(api, settings, pd.Timestamp(SCAN_1), clock=clock_at(SCAN_1))
    api.calls.clear()


def test_full_cycle_scan_value_coupons_settlement_clv_and_outputs(world) -> None:
    settings, api = world
    config = ScanConfig()

    # ---- first run: six hours before the matches -------------------------------------------------------------
    first = run_pipeline(config, as_of=SCAN_1, settings=settings, client=api, clock=clock_at(SCAN_1))
    assert first.warnings == []
    assert first.recorded and first.scan_id == "20261003T120000Z"
    # calendar: the whole (small) window was listed on the first run, one request per day
    listed = sorted(p["date"] for e, p in api.calls if e == "/fixtures" and "date" in p)
    assert listed == ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]
    assert first.api["requests_directory"] == 4 and first.api["requests_details"] == 0

    # quotes: only prices seen before the decision (the close at 17:30 and the next day's board must not leak)
    assert first.counts["fixtures"] == 3
    assert first.quotes["fetched_at"].max() <= pd.Timestamp(SCAN_1)
    pin_home = first.fair.set_index(["fixture_id", "market", "selection"]).loc[(501, "1x2", "home")]
    assert pin_home["fair_source"] == "pinnacle"
    assert 1 / 1.55 / (1 / 1.55 + 1 / 4.30 + 1 / 6.00) - 0.02 < pin_home["p_fair"] < 1 / 1.55

    # value legs: the Bet365 home price of every match; never a leg at Pinnacle
    scan = first.scan
    assert set(scan.legs["bookmaker"]) == {"Bet365"}
    home_picks = scan.picks[(scan.picks["market"] == "1x2") & (scan.picks["selection"] == "home")]
    assert sorted(home_picks["fixture_id"]) == [501, 502, 503]
    assert first.counts["value_by_tau"]["t100"] >= 3
    # coupons: two legs from different matches at one bookmaker, positive lower-bound EV after the 12% tax
    top = scan.coupons.top
    assert len(top) >= 1
    for record in top.itertuples(index=False):
        legs = scan.coupons.legs_of(record.coupon_id)
        assert legs["fixture_id"].nunique() == len(legs) == record.n_legs
        assert record.ev_net_lower_t088 > 0 and record.p_hit >= 0.30
        assert set(legs["leg_key"]) <= set(scan.picks["leg_key"])  # coupons are built from value legs only
    # models: the cached fit prices the 1X2 of all three matches; the forecasts are logged
    assert first.model_info["status"] == "ok" and first.model_info["fixtures_forecast"] == 3
    assert scan.legs.loc[scan.legs["market"] == "1x2", "p_model"].notna().all()
    logged = ForecastLog(settings.forecast_log_path).read()
    assert set(logged["fixture_id"]) == {501, 502, 503} and (logged["model_name"] == "pool_models").all()

    # register, page and CSV
    register = ScannerRegister(settings.register_path)
    assert register.has_scan(first.scan_id)
    scans = register.table("scans")
    pipeline_info = json.loads(scans.iloc[0]["diagnostics_json"])["pipeline"]
    assert pipeline_info["fair_by_source"]["pinnacle"] >= 9 and pipeline_info["model"]["status"] == "ok"
    page = (settings.out_dir / "latest.html").read_text(encoding="utf-8")
    assert "Klub A" in page and "Skaner wartości" in page
    assert (settings.out_dir / "html" / "2026-10-03.html").exists()
    value_csv = pd.read_csv(settings.out_dir / "latest_value_legs.csv", encoding="utf-8-sig")
    assert len(value_csv) == len(scan.picks) and value_csv["bet_pl"].str.contains("wygra").any()
    assert (value_csv["bookmaker"] == "Bet365").all() and value_csv["match"].str.contains("Klub A - Klub J").any()
    coupons_csv = pd.read_csv(settings.out_dir / "latest_coupons.csv", encoding="utf-8-sig")
    assert coupons_csv["coupon_rank"].min() == 1 and set(coupons_csv["coupon_id"]) == set(top["coupon_id"])
    assert (settings.out_dir / "csv" / "2026-10-03_value_legs.csv").exists()

    # ---- second run: the next day, the matches are over ------------------------------------------------------
    api.finished = True
    second = run_pipeline(config, as_of=SCAN_2, settings=settings, client=api, clock=clock_at(SCAN_2))
    # the calendar gives the scores; the corner leg needs the statistics: one details request for match 501 only
    assert ("/fixtures", {"ids": "501"}) in api.calls
    assert second.api["requests_details"] == 1 and second.api["requests_halves"] == 0
    settled = register.latest_settlements().set_index("leg_key")["outcome"]
    assert settled["501|1x2|ft|na|home"] == "win"  # 2:0
    assert settled["502|1x2|ft|na|home"] == "loss"  # 0:1
    assert settled["503|1x2|ft|na|home"] == "loss"  # 1:1
    assert settled["501|corners_ou|ft|9.50|over"] == "win"  # 6 + 5 corners
    assert second.settlement["settled"] == len(settled)
    # closing line value from the last board before kickoff: Pinnacle shortened the home side, so it was beaten
    clv = register.latest_clv().set_index("leg_key")
    assert second.settlement["clv_rows"] == len(clv) >= 3
    home_clv = clv.loc["501|1x2|ft|na|home"]
    assert home_clv["odd_taken"] == pytest.approx(1.75) and home_clv["clv_raw"] > 0
    assert home_clv["closing_p_fair"] > pin_home["p_fair"]
    # the fit is now 30 hours old: the models are skipped with a warning, the scan still runs on market prices
    assert second.model_info["status"] == "fit_stale"
    assert any("models skipped" in w for w in second.warnings)
    assert second.counts["fixtures"] == 1 and second.recorded
    summary = register.paper_summary(now=SCAN_2, config_id=config.config_id).set_index(["kind", "window_days"])
    assert summary.loc[("single", 7), "n_settled"] >= 3
    assert "Wyniki papierowe" in second.scan.html and "ostatnie 7 dni" in second.scan.html

    # the run log holds one JSON line per run with the headline numbers
    lines = settings.run_log_path.read_text(encoding="utf-8").strip().splitlines()
    runs = [json.loads(line) for line in lines]
    assert [r["scan_id"] for r in runs] == [first.scan_id, second.scan_id]
    assert runs[0]["counts"]["picks"] == len(scan.picks) and runs[1]["settlement"]["settled"] == second.settlement["settled"]
    assert runs[1]["api"]["requests_details"] == 1

    # a third run finds nothing new to fetch and nothing new to settle (statistics are not asked twice)
    calls_before = [c for c in api.calls if "ids" in c[1]]
    third = run_pipeline(config, as_of=SCAN_2 + timedelta(hours=1), settings=settings, client=api, clock=clock_at(SCAN_2 + timedelta(hours=1)))
    assert [c for c in api.calls if "ids" in c[1]] == calls_before
    assert third.settlement["settled"] == 0 and third.settlement.get("clv_rows", 0) == 0


def test_matches_far_ahead_may_use_an_older_board_than_near_ones(tmp_path, model_cache) -> None:
    """The odds collector refreshes the days beyond the next two only every six hours: a six-hour-old board of a
    match three days ahead is current, the same board of a match tonight is stale (in the quotes and the fair price)."""
    archive = OddsArchive(tmp_path / "odds")
    seen = SCAN_1 - timedelta(hours=6)
    boards = [
        odds_object(
            fixture_id,
            SCAN_1 + timedelta(hours=lead),
            seen - timedelta(minutes=10),
            {PINNACLE: {1: match_winner(1.55, 4.30, 6.00)}, BET365: {1: match_winner(1.75, 4.20, 5.50)}},
        )
        for fixture_id, lead in ((601, 10), (602, 72))
    ]
    archive.record("s1", seen, boards)
    settings = settings_for(tmp_path, model_cache, api_enabled=False, write_page=False, write_csv=False, write_run_log=False)

    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=settings)
    assert result.counts["fixtures"] == 2 and result.counts["fixtures_evaluated"] == 1
    assert set(result.scan.legs["fixture_id"]) == {602} and set(result.fair["fixture_id"]) == {602}
    assert result.scan.diagnostics.n_stale > 0 and "evaluated=1" in result.summary_line()

    # without the far allowance both matches are stale at six hours, in the quotes and in the fair price
    strict = replace(ScanConfig(), max_quote_age_far_hours=3.0, fair_max_age_far_minutes=180.0)
    other = replace(settings, register_path=settings.out_dir / "strict.sqlite")
    result = run_pipeline(strict, as_of=SCAN_1, settings=other)
    assert result.counts["fixtures_evaluated"] == 0 and len(result.fair) == 0


def test_the_far_bounds_may_not_be_tighter_than_the_near_ones() -> None:
    with pytest.raises(ValueError):
        ScanConfig(max_quote_age_far_hours=1.0)
    with pytest.raises(ValueError):
        ScanConfig(fair_max_age_far_minutes=60.0)
    with pytest.raises(ValueError):
        ScanConfig(fair_max_age_far_minutes=0.0)
    assert ScanConfig(fair_max_age_far_minutes=None).fair_kwargs()["max_age_far_minutes"] is None


def test_without_an_api_client_the_scan_still_runs_on_the_archives(world) -> None:
    settings, _ = world
    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=settings, client=None)
    assert result.recorded and result.api_requests == 0
    assert any("API off" in w for w in result.warnings)
    assert result.counts["fixtures"] == 3 and len(result.scan.picks) >= 3
    # no calendar in the directory: the page falls back to neutral names instead of failing, and says so
    assert "Gospodarz #501" in result.scan.html
    assert result.counts["fixtures_without_calendar"] == 3
    assert any("3 of 3 fixtures with odds are in neither" in w for w in result.warnings)
    assert result.model_info["status"] == "no_fixtures"  # the models need the teams from the calendar


def test_api_switched_off_makes_no_request_and_no_warning(world) -> None:
    settings, api = world
    seed_calendar(settings, api)
    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=replace(settings, api_enabled=False), client=api)
    assert api.calls == [] and result.warnings == [] and result.api["enabled"] is False


def test_the_quota_reserve_stops_every_request(world) -> None:
    settings, api = world
    FixtureDirectory(settings.directory_root).save_quota(1400, SCAN_1 - timedelta(hours=1))  # already below 1500 today
    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=settings, client=api, clock=clock_at(SCAN_1))
    assert api.calls == []
    assert result.directory["stopped_reason"] == "budget_exhausted"
    assert any("budget_exhausted" in w for w in result.warnings)
    assert result.recorded  # the scan itself does not need the API


def test_missing_model_fit_is_a_warning_not_a_failure(world, tmp_path) -> None:
    settings, api = world
    seed_calendar(settings, api)
    result = run_pipeline(
        ScanConfig(), as_of=SCAN_1, settings=replace(settings, model_cache_dir=tmp_path / "empty_cache", api_enabled=False)
    )
    assert result.model_info["status"] == "no_fit" and any("no model fit" in w for w in result.warnings)
    assert result.recorded and result.scan.legs["p_model"].isna().all()


def test_a_run_with_no_upcoming_match_records_an_empty_scan_and_renders(tmp_path, model_cache) -> None:
    build_odds_archive(tmp_path / "odds")
    settings = settings_for(tmp_path, model_cache, api_enabled=False)
    result = run_pipeline(ScanConfig(), as_of=datetime(2026, 10, 6, tzinfo=UTC), settings=settings)
    assert result.recorded and result.counts["quotes"] == 0 and result.counts["legs"] == 0
    assert "Brak kuponów" in result.scan.html
    assert result.model_info["status"] in {"fit_stale", "no_fixtures"}


def test_models_are_used_only_as_a_signal_and_never_change_the_value_list(world) -> None:
    settings, api = world
    seed_calendar(settings, api)
    base = replace(settings, api_enabled=False, write_page=False, write_csv=False, write_run_log=False)
    with_models = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=replace(base, register_path=base.out_dir / "a.sqlite"))
    without = run_pipeline(ScanConfig(use_models=False), as_of=SCAN_1, settings=replace(base, register_path=base.out_dir / "b.sqlite"))
    assert with_models.model_info["status"] == "ok" and without.model_info["status"] == "disabled"
    key = ["fixture_id", "bookmaker", "market", "period", "selection"]
    pd.testing.assert_frame_equal(
        with_models.scan.picks[key].reset_index(drop=True), without.scan.picks[key].reset_index(drop=True)
    )


def test_closing_line_value_needs_no_fresh_closing_board(tmp_path, model_cache) -> None:
    """The last board before kickoff is often hours old (the provider refreshes a fixture every few hours): it is
    still the close, so the staleness bound of the decision must not apply to it."""
    build_odds_archive(tmp_path / "odds", close_minutes_before=240)
    settings = settings_for(tmp_path, model_cache, api_enabled=False)
    run_pipeline(ScanConfig(), as_of=SCAN_1, settings=settings)
    second = run_pipeline(ScanConfig(), as_of=SCAN_2, settings=settings)
    clv = ScannerRegister(settings.register_path).latest_clv().set_index("leg_key")
    assert second.settlement["clv_rows"] >= 3 and clv.loc["501|1x2|ft|na|home", "clv_raw"] > 0


def test_a_suspect_price_is_logged_but_never_counted_as_value(tmp_path, model_cache) -> None:
    archive = OddsArchive(tmp_path / "odds")
    seen = SCAN_1 - timedelta(minutes=10)
    board = odds_object(
        701,
        KICKOFF,
        seen - timedelta(minutes=10),
        {PINNACLE: {1: match_winner(1.55, 4.30, 6.00)}, BET365: {1: match_winner(3.00, 4.20, 5.50)}},  # +89% on the home side
    )
    archive.record("s1", seen, [board])
    settings = settings_for(tmp_path, model_cache, api_enabled=False, write_page=False, write_csv=False, write_run_log=False)
    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=settings)
    legs = result.scan.legs
    home = legs[(legs["market"] == "1x2") & (legs["selection"] == "home")].iloc[0]
    assert home["suspect"] and not home["is_value"] and home["value_t100"]  # the arithmetic alone would call it value
    assert result.counts["suspect"] >= 1
    assert result.counts["value_by_tau"]["t100"] == int(legs["is_value"].sum()) == len(result.scan.picks)
    assert ScannerRegister(settings.register_path).table("legs").query("leg_class == 'suspect'").shape[0] >= 1


def test_fixture_names_come_from_the_directory_first_then_the_prematch_archive(world, tmp_path) -> None:
    settings, api = world
    seed_calendar(settings, api)  # the directory knows 501-503 and 504
    prematch = tmp_path / "prematch.sqlite3"
    with closing(sqlite3.connect(prematch)) as con:
        con.execute(
            "CREATE TABLE fixtures (fixture_id INTEGER, league_id INTEGER, season INTEGER, round TEXT, kickoff_utc TEXT, "
            "status_short TEXT, home_team_id INTEGER, home_team TEXT, away_team_id INTEGER, away_team TEXT)"
        )
        con.executemany(
            "INSERT INTO fixtures VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (501, 39, 2026, "R8", _iso(KICKOFF), "NS", 1, "Z archiwum przedmeczowego", 2, "Nieużywany"),
                (909, 40, 2026, "R1", _iso(KICKOFF), "NS", 7, "Tylko w archiwum", 8, "Gość z archiwum"),
            ],
        )
        con.commit()
    frame = load_fixture_frame([501, 909, 12345], replace(settings, prematch_db=prematch)).set_index("fixture_id")
    assert frame.loc[501, "home_team"] == NAMES[TEAMS[0]]  # the directory wins
    assert frame.loc[909, "home_team"] == "Tylko w archiwum" and frame.loc[909, "away_team_id"] == 8
    assert 12345 not in frame.index  # a fixture found nowhere is absent, not invented
    assert load_fixture_frame([], settings).empty


def test_half_time_statistics_are_not_requested_for_matches_that_are_not_over(world) -> None:
    settings, api = world
    seed_calendar(settings, api)  # the directory lists the matches as not started (status NS)
    out = fetch_settlement_statistics(api, settings, pd.Timestamp(SCAN_2), [], [501], clock=clock_at(SCAN_2))
    assert api.calls == [] and out["requests_halves"] == 0


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


def test_statistics_needed_asks_only_for_what_the_settlement_lacks() -> None:
    now = pd.Timestamp("2026-10-04T12:00:00Z")
    kick = "2026-10-03T18:00:00+00:00"
    pending = pd.DataFrame(
        [
            {"fixture_id": 1, "market": "1x2", "period": "ft", "kickoff_utc": kick},  # score missing
            {"fixture_id": 2, "market": "corners_ou", "period": "ft", "kickoff_utc": kick},  # corners missing
            {"fixture_id": 3, "market": "cards_ou", "period": "1h", "kickoff_utc": kick},  # first-half cards missing
            {"fixture_id": 4, "market": "corners_ah", "period": "2h", "kickoff_utc": kick},  # needs both
            {"fixture_id": 5, "market": "ou", "period": "ft", "kickoff_utc": kick},  # complete
            {"fixture_id": 6, "market": "corners_ou", "period": "ft", "kickoff_utc": "2026-10-04T11:00:00+00:00"},  # too recent
            {"fixture_id": 7, "market": "ou", "period": "1h", "kickoff_utc": kick},  # half-time score missing
        ]
    )
    results = pd.DataFrame(
        [
            {"fixture_id": 2, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 3, "home_goals": 1, "away_goals": 0, "home_cards": 3, "away_cards": 2},
            {"fixture_id": 4, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 5, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 6, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 7, "home_goals": 1, "away_goals": 0},
        ]
    )
    details, halves = statistics_needed(pending, results, now, min_age_hours=2.0)
    assert details == [1, 2, 4, 7]
    assert halves == [3, 4]
    assert statistics_needed(pending.iloc[0:0], results, now) == ([], [])


def test_statistics_requests_put_value_legs_first_and_respect_the_per_step_caps(world) -> None:
    settings, api = world
    api.finished = True
    refresh_directory(api, settings, pd.Timestamp(SCAN_2), clock=clock_at(SCAN_2))  # the directory knows they are over
    api.calls.clear()
    capped = replace(settings, max_requests_halves=1, max_requests_details=1)
    out = fetch_settlement_statistics(api, capped, pd.Timestamp(SCAN_2), [], [501, 502, 503], priority={503}, clock=clock_at(SCAN_2))
    assert api.calls == [("/fixtures/statistics", {"fixture": 503, "half": "true"})]
    assert out["requests_halves"] == 1 and out["halves_asked"] == 1
    api.calls.clear()
    out = fetch_settlement_statistics(api, capped, pd.Timestamp(SCAN_2), [501, 502, 503], [], clock=clock_at(SCAN_2))
    assert api.calls == [("/fixtures", {"ids": "501-502-503"})] and out["details_with_stats"] == 3
    # nothing is asked twice: the statistics are there now
    api.calls.clear()
    fetch_settlement_statistics(api, settings, pd.Timestamp(SCAN_2), [501, 502, 503], [], clock=clock_at(SCAN_2))
    assert api.calls == []


def test_forecast_log_is_append_only_and_keeps_one_row_per_fit_and_leg(tmp_path) -> None:
    log = ForecastLog(tmp_path / "f.sqlite")
    model = pd.DataFrame(
        [
            {"fixture_id": 1, "market": "1x2", "period": "ft", "line": np.nan, "selection": "home", "p_model": 0.5, "model_name": "pool_models"},
            {"fixture_id": 1, "market": "corners_ou", "period": "ft", "line": 9.5, "selection": "over", "p_model": 0.4, "model_name": "count_nb2"},
            {"fixture_id": 1, "market": "corners_ou", "period": "ft", "line": 10.5, "selection": "over", "p_model": 0.3, "model_name": "count_nb2"},
        ]
    )
    quoted = pd.DataFrame([{"fixture_id": 1, "market": "corners_ou", "period": "ft", "line": 9.5, "selection": "over"}])
    assert log.append(model, fit_as_of="2026-10-03T06:00:00Z", scan_id="a", as_of="2026-10-03T12:00:00Z", quoted=quoted) == 2
    assert log.append(model, fit_as_of="2026-10-03T06:00:00Z", scan_id="b", as_of="2026-10-03T13:00:00Z", quoted=quoted) == 0
    assert log.append(model, fit_as_of="2026-10-04T06:00:00Z", scan_id="c", as_of="2026-10-04T12:00:00Z", quoted=quoted) == 2
    frame = log.read()
    assert set(frame["line_key"]) == {"na", "9.50"} and set(frame["first_scan_id"]) == {"a", "c"}
    with closing(sqlite3.connect(log.path)) as con, pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM model_forecasts")


def test_scan_config_hashes_every_threshold_including_fair_and_model_settings() -> None:
    base = ScanConfig()
    assert isinstance(base, ScannerConfig)
    assert base.config_id != ScannerConfig().config_id  # the fair and model settings are part of the definition
    for change in (
        {"fair_method": "shin"},
        {"fair_min_consensus_books": 4},
        {"fair_max_age_minutes": 120.0},
        {"model_max_fit_age_hours": 48.0},
        {"z": 2.0},
        {"min_hit": 0.4},
        {"max_days_ahead": 2.0},
    ):
        assert replace(base, **change).config_id != base.config_id, change
    assert replace(base).config_id == base.config_id
    assert {"fair_method", "model_max_fit_age_hours", "taus", "top_k_legs", "top_n_per_bookmaker"} <= set(value_fields())
    assert base.closing_fair_kwargs()["max_age_minutes"] is None and base.fair_kwargs()["max_age_minutes"] == 180.0
    assert base.fair_kwargs()["max_age_far_minutes"] == 480.0 and base.fair_kwargs()["far_lead_hours"] == 48.0
    assert replace(base, max_quote_age_far_hours=10.0).config_id != base.config_id


@pytest.mark.parametrize(
    "bad",
    [{"fair_method": "median"}, {"fair_betfair_commission": 1.0}, {"fair_min_consensus_books": 0},
     {"fair_max_age_minutes": 0.0}, {"fair_score_model": "nb"}, {"model_max_fit_age_hours": 0.0}, {"taus": ()}],
)  # fmt: skip
def test_invalid_scan_config_is_rejected(bad) -> None:
    with pytest.raises(ValueError):
        ScanConfig(**bad)


def test_run_settings_default_to_the_standard_layout(tmp_path) -> None:
    settings = RunSettings(data_root=tmp_path)
    assert settings.odds_db == tmp_path / "api-football-odds" / "odds.sqlite3"
    assert settings.directory_db == tmp_path / "fixture-directory" / "directory.sqlite3"
    assert settings.register_path == tmp_path / "scanner" / "scanner.sqlite"
    assert settings.run_log_path == tmp_path / "scanner" / "runs.jsonl"
    assert settings.api_reserve == 1500
    with pytest.raises(ValueError):
        RunSettings(data_root=tmp_path, max_requests_halves=-1)


def test_summary_line_is_one_line_with_the_headline_numbers(world) -> None:
    settings, api = world
    seed_calendar(settings, api)
    result = run_pipeline(ScanConfig(), as_of=SCAN_1, settings=replace(settings, api_enabled=False))
    line = result.summary_line()
    assert "\n" not in line and isinstance(result, PipelineResult)
    for token in ("scan=20261003T120000Z", "fixtures=3", "value_t100=", "coupons=", "model=ok(3fx)", "api=0"):
        assert token in line


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_scanner.py"
    spec = importlib.util.spec_from_file_location("run_scanner_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_offline_run_writes_only_to_the_offline_folder(tmp_path, model_cache, capsys, monkeypatch) -> None:
    script = _script()
    build_odds_archive(tmp_path / "api-football-odds")
    monkeypatch.setattr(script, "make_client", lambda key: pytest.fail("no client may be built offline"))
    code = script.main(["--offline", "--as-of", "2026-10-03T12:00:00Z", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0 and len(out) == 1 and out[0].startswith("scan=20261003T120000Z")
    assert (tmp_path / "scanner" / "offline" / "scanner.sqlite").exists()
    assert (tmp_path / "scanner" / "offline" / "latest.html").exists()
    assert not (tmp_path / "scanner" / "scanner.sqlite").exists()  # the live register is untouched


def test_cli_as_of_implies_no_api_and_days_ahead_sets_the_horizon(tmp_path, capsys, monkeypatch) -> None:
    script = _script()
    args = script.build_parser().parse_args(["--as-of", "2026-10-03T12:00:00Z", "--days-ahead", "2"])
    settings = script.settings_from(args)
    assert settings.api_enabled is False
    live = script.settings_from(script.build_parser().parse_args(["--data-root", str(tmp_path)]))
    assert live.api_enabled is True and live.out_dir == tmp_path / "scanner" and live.api_reserve == 1500
    monkeypatch.setattr(script, "api_key", lambda: None)
    with pytest.raises(SystemExit):  # a zero horizon is an error, not "scan everything"
        script.main(["--days-ahead", "0", "--data-root", str(tmp_path)])
    capsys.readouterr()
    # a failing run prints one error line and exits 1 (the odds archive does not exist here)
    code = script.main(["--no-api", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 1 and len(out) == 1 and out[0].startswith("error: scan failed")
    assert (tmp_path / "logs" / "scanner_errors.log").exists()
