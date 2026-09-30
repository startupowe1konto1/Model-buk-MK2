"""Live model forecasts: no look-ahead in the fit, exact pooling, derived markets, count lines, coverage rules.

Everything runs offline on small synthetic leagues (Poisson goals and counts with team strengths); the real
archives are not touched.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest
from scipy import stats

import model_buk.scanner.live_models as lm
from model_buk.evaluation.framework import MARKET_1X2, WalkForwardConfig, evaluate_model
from model_buk.evaluation.count_markets import load_count_matches
from model_buk.evaluation.model_selection import apply_log_linear_pool
from model_buk.evaluation.protocol import GOAL_MARKETS
from model_buk.models.goal_adapters import dixon_coles_spec, nb2_spec
from model_buk.models.gbm_model import GbmSettings

# ---------------------------------------------------------------------------
# Synthetic history
# ---------------------------------------------------------------------------

AS_OF = pd.Timestamp("2026-09-21T12:00:00Z")
CUTOFF = AS_OF - pd.Timedelta(hours=3)
FIRST_ROUND = pd.Timestamp("2024-08-03T15:00:00Z")
N_TEAMS = 10
LEAGUES = {"AAA": 39, "BBB": 140}

GBM = GbmSettings(iterations=25, early_stopping_rounds=5, min_train_rows=100, border_count=16)


def round_robin(ids: list[int], round_index: int) -> list[tuple[int, int]]:
    """Circle-method pairings of one round (home/away alternate by round)."""
    n = len(ids)
    rotating = ids[1:]
    shift = round_index % (n - 1)
    rotating = rotating[-shift:] + rotating[:-shift] if shift else rotating
    order = [ids[0], *rotating]
    pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
    return [(b, a) if (round_index + i) % 2 else (a, b) for i, (a, b) in enumerate(pairs)]


def make_history(n_rounds: int = 130, seed: int = 3, leagues: dict[str, int] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly rounds of two leagues with Poisson goals, corners, shots, fouls, yellow cards; plus team stats."""
    rng = np.random.default_rng(seed)
    leagues = leagues or LEAGUES
    rows = []
    stat_rows = []
    fixture = 1000
    for l_index, (code, league_id) in enumerate(leagues.items()):
        ids = [league_id * 100 + i for i in range(N_TEAMS)]
        attack = dict(zip(ids, rng.normal(0, 0.25, N_TEAMS)))
        defence = dict(zip(ids, rng.normal(0, 0.25, N_TEAMS)))
        for r in range(n_rounds):
            kickoff = FIRST_ROUND + pd.Timedelta(days=7 * r + l_index, hours=2 * l_index)
            season = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
            for home, away in round_robin(ids, r):
                fixture += 1
                mu_h = math.exp(0.25 + 0.2 + attack[home] + defence[away])
                mu_a = math.exp(0.25 + attack[away] + defence[home])
                hg, ag = int(rng.poisson(mu_h)), int(rng.poisson(mu_a))
                hs, as_ = int(rng.poisson(12 * mu_h / 1.5)), int(rng.poisson(10 * mu_a / 1.3))
                row = {
                    "fixture_id": fixture,
                    "league_id": league_id,
                    "league_code": code,
                    "season": season,
                    "round": f"Regular Season - {r % 18 + 1}",
                    "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "status_short": "FT",
                    "venue_id": 1,
                    "referee": f"ref{int(rng.integers(0, 6))}",
                    "home_team_id": home,
                    "home_team": f"T{home}",
                    "away_team_id": away,
                    "away_team": f"T{away}",
                    "home_goals": hg,
                    "away_goals": ag,
                    "ht_home_goals": min(hg, 1),
                    "ht_away_goals": min(ag, 1),
                    "et_home_goals": np.nan,
                    "et_away_goals": np.nan,
                    "pen_home": np.nan,
                    "pen_away": np.nan,
                    "home_shots": float(hs),
                    "away_shots": float(as_),
                    "home_sot": float(hs // 3),
                    "away_sot": float(as_ // 3),
                    "home_corners": float(rng.poisson(0.4 * hs)),
                    "away_corners": float(rng.poisson(0.4 * as_)),
                    "home_fouls": float(rng.poisson(11)),
                    "away_fouls": float(rng.poisson(11)),
                    "home_xg": np.nan,
                    "away_xg": np.nan,
                    "home_yellow": float(rng.poisson(1.8)),
                    "away_yellow": float(rng.poisson(1.9)),
                    "home_red": 0.0,
                    "away_red": 0.0,
                    "home_booking": np.nan,
                    "away_booking": np.nan,
                }
                rows.append(row)
                for side, team, shots, sot, corners in (
                    ("home", home, hs, hs // 3, row["home_corners"]),
                    ("away", away, as_, as_ // 3, row["away_corners"]),
                ):
                    stat_rows.append(
                        {"fixture_id": fixture, "team_id": team, "total_shots": float(shots), "shots_on_goal": float(sot),
                         "corners": corners, "expected_goals": np.nan}
                    )  # fmt: skip
    return pd.DataFrame(rows), pd.DataFrame(stat_rows)


WEIGHTS = {
    "1x2": {"baseline_league_freq": 0.1, "dixon_coles": 0.3, "nb2": 0.1, "gas_poisson": 0.1, "elo_davidson": 0.1, "pi_gbm": 0.2, "pi_ratings": 0.1},
    "over_under_1.5": {"dixon_coles": 0.4, "gas_poisson": 0.2, "pi_gbm": 0.4},
    "over_under_2.5": {"dixon_coles": 0.3, "gas_poisson": 0.2, "pi_gbm": 0.4, "pi_ratings": 0.1},
    "over_under_3.5": {"baseline_league_freq": 0.05, "dixon_coles": 0.25, "gas_poisson": 0.2, "pi_gbm": 0.5},
    "btts": {"dixon_coles": 0.2, "gas_poisson": 0.2, "pi_gbm": 0.6},
}
HYPERPARAMETERS = {
    "dixon_coles": {m: {"half_life_days": 365.0 if m == "1x2" else 730.0} for m in lm.GOAL_MARKET_NAMES},
    "nb2": {m: {"half_life_days": 365.0} for m in lm.GOAL_MARKET_NAMES},
    "elo_davidson": {"1x2": {"k_factor": 20.0}},
    "pi_gbm": {m: {"learning_rate": 0.08, "depth": 3} for m in lm.GOAL_MARKET_NAMES},
    "count": {
        "corners": {s: {"ridge": 1000.0, "half_life_days": None} for s in lm.COUNT_SCOPES},
        "yellow": {"total": {"ridge": 1000.0, "half_life_days": None}, "home": {"ridge": 5000.0, "half_life_days": 1000.0}, "away": {"ridge": 1000.0, "half_life_days": None}},
    },
}


def config_for(tmp_path) -> lm.LiveConfig:
    return lm.LiveConfig(
        cache_dir=tmp_path / "cache",
        history_db=tmp_path / "no_history.sqlite",
        comparison_db=tmp_path / "no_comparison.sqlite",
        predictions_dir=tmp_path / "no_predictions",
        legacy_booster_dir=None,
        gbm_settings=GBM,
    )


@pytest.fixture(scope="module")
def history() -> tuple[pd.DataFrame, pd.DataFrame]:
    return make_history()


def fit(config: lm.LiveConfig, history, *, as_of=AS_OF, matches=None, weights=WEIGHTS, **kwargs) -> dict:
    matches_frame, team_stats = history
    return lm.fit_live_models(
        as_of,
        config=config,
        matches=matches_frame if matches is None else matches,
        team_stats=team_stats,
        weights=weights,
        hyperparameters=HYPERPARAMETERS,
        log=lambda text: None,
        **kwargs,
    )


def fixtures_for(history, *, n_per_league: int = 3, kickoff="2026-09-26T15:00:00+00:00") -> pd.DataFrame:
    matches_frame, _ = history
    rows = []
    fixture = 90_000
    for code, league_id in LEAGUES.items():
        ids = sorted(set(matches_frame.loc[matches_frame["league_code"] == code, "home_team_id"]))
        for i in range(n_per_league):
            fixture += 1
            rows.append(
                {"fixture_id": fixture, "league_id": league_id, "kickoff_utc": kickoff, "home_team_id": ids[i], "away_team_id": ids[-1 - i],
                 "status_short": "NS", "season": 2026}
            )  # fmt: skip
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted(tmp_path_factory, history):
    config = config_for(tmp_path_factory.mktemp("live"))
    manifest = fit(config, history)
    return config, manifest


# ---------------------------------------------------------------------------
# Cut-off and leakage
# ---------------------------------------------------------------------------


def test_train_cutoff_is_as_of_minus_latency():
    assert lm.train_cutoff(AS_OF) == AS_OF - pd.Timedelta(hours=3)
    assert lm.train_cutoff("2026-09-21T12:00:00", 0) == AS_OF


def test_season_label_follows_the_european_calendar():
    assert lm.season_label("2026-09-30T00:00:00Z") == 2026
    assert lm.season_label("2027-03-01T00:00:00Z") == 2026
    assert lm.season_label("2027-07-01T00:00:00Z") == 2027


def test_fit_uses_only_matches_before_the_cutoff(tmp_path, history):
    matches, _ = history
    config = config_for(tmp_path)
    boundary = matches.copy()
    # One match exactly at the cutoff (must be left out) and one a second before it (must be used).
    boundary.loc[boundary.index[-1], "kickoff_utc"] = CUTOFF.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    boundary.loc[boundary.index[-2], "kickoff_utc"] = (CUTOFF - pd.Timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    manifest = fit(config, history, matches=boundary)
    assert pd.Timestamp(manifest["max_train_kickoff"]) == CUTOFF - pd.Timedelta(seconds=1)
    assert pd.Timestamp(manifest["train_cutoff"]) == CUTOFF
    for status in manifest["leagues"].values():
        assert pd.Timestamp(status["max_train_kickoff"]) < CUTOFF


def _forecast_dict(config, fixtures) -> dict:
    frame = lm.forecast_fixtures(fixtures, AS_OF, config=config, include_components=True)
    frame = frame.assign(line=frame["line"].fillna(-999.0))  # NaN != NaN would make the keys unequal
    return frame.set_index(["fixture_id", "market", "line", "selection", "model_name"])["p_model"].to_dict()


def test_forecasts_do_not_depend_on_matches_after_the_cutoff(tmp_path, history):
    """Rewrite every result from the cutoff on: no forecast may change (the fit never sees them)."""
    matches, _ = history
    stamp = pd.to_datetime(matches["kickoff_utc"], utc=True)
    future = (stamp >= CUTOFF).to_numpy()
    assert 50 < future.sum() < len(matches)  # the table really holds matches after the cutoff
    fixtures = fixtures_for(history)
    baseline_config = config_for(tmp_path / "a")
    fit(baseline_config, history)
    reference = _forecast_dict(baseline_config, fixtures)

    disturbed = matches.copy()
    rng = np.random.default_rng(99)
    for column in ("home_goals", "away_goals", "home_corners", "away_corners", "home_yellow", "away_yellow", "home_shots", "away_shots"):
        disturbed.loc[future, column] = rng.integers(0, 9, future.sum()).astype(float)
    stats_frame = history[1]
    disturbed_config = config_for(tmp_path / "b")
    fit(disturbed_config, (disturbed, stats_frame), matches=disturbed)
    changed = _forecast_dict(disturbed_config, fixtures)
    assert reference.keys() == changed.keys()
    assert len(reference) > 100
    np.testing.assert_array_equal(np.array(list(reference.values())), np.array([changed[k] for k in reference]))


def test_the_leakage_test_detects_a_cutoff_that_is_too_late(tmp_path, history, monkeypatch):
    """Mutation: a cut-off 6 days too late lets future results into the fit, and the forecasts move."""
    matches, _ = history
    stamp = pd.to_datetime(matches["kickoff_utc"], utc=True)
    future = (stamp >= CUTOFF).to_numpy()
    fixtures = fixtures_for(history, kickoff="2026-10-10T15:00:00+00:00")
    disturbed = matches.copy()
    rng = np.random.default_rng(5)
    for column in ("home_goals", "away_goals", "home_corners", "away_corners", "home_yellow", "away_yellow"):
        disturbed.loc[future, column] = rng.integers(0, 9, future.sum()).astype(float)
    # Sanity: with the right cut-off the disturbance is invisible (the previous test), so any change below is the leak.
    monkeypatch.setattr(lm, "train_cutoff", lambda as_of, latency_hours=3.0: lm.utc(as_of) + pd.Timedelta(days=6))
    clean_config, dirty_config = config_for(tmp_path / "a"), config_for(tmp_path / "b")
    manifest = fit(clean_config, history)
    assert pd.Timestamp(manifest["max_train_kickoff"]) >= CUTOFF  # the broken cut-off is visible in the manifest
    fit(dirty_config, (disturbed, history[1]), matches=disturbed)
    reference, changed = _forecast_dict(clean_config, fixtures), _forecast_dict(dirty_config, fixtures)
    assert reference and reference.keys() == changed.keys()
    assert any(abs(reference[k] - changed[k]) > 1e-6 for k in reference)


def test_forecast_refuses_a_fit_that_is_newer_than_the_fixture_allows(tmp_path, history, monkeypatch):
    """A fit trained on matches within 3 h of a fixture's kickoff is not used for that fixture."""
    monkeypatch.setattr(lm, "train_cutoff", lambda as_of, latency_hours=3.0: lm.utc(as_of) + pd.Timedelta(days=6))
    config = config_for(tmp_path)
    fit(config, history)
    soon = fixtures_for(history, kickoff="2026-09-26T15:00:00+00:00")  # before the (broken) cut-off + latency
    late = fixtures_for(history, kickoff="2026-10-10T15:00:00+00:00")
    _, report = lm.forecast_with_report(soon, AS_OF, config=config)
    assert set(report["status"]) == {"fit_too_new"}
    _, report = lm.forecast_with_report(late, AS_OF, config=config)
    assert set(report["status"]) == {"ok"}


def test_fit_is_valid_only_for_fixtures_after_the_cutoff_plus_latency(fitted, history):
    config, _ = fitted
    fixtures = fixtures_for(history, n_per_league=1)
    early = fixtures.copy()
    early["kickoff_utc"] = "2026-09-21T13:00:00+00:00"  # 1 h after as_of: its training set is fine
    _, report = lm.forecast_with_report(early, AS_OF, config=config)
    assert set(report["status"]) == {"ok"}
    # Asking as of an earlier moment than the fit: the fit does not exist yet for that decision time.
    _, report = lm.forecast_with_report(fixtures, AS_OF - pd.Timedelta(days=1), config=config)
    assert set(report["status"]) == {"no_fit"}


# ---------------------------------------------------------------------------
# Pool, derived markets
# ---------------------------------------------------------------------------


def test_log_linear_pool_is_applied_exactly_and_renormalised():
    rng = np.random.default_rng(1)
    components = {name: rng.dirichlet(np.ones(3), size=20) for name in ("a", "b", "c", "d")}
    weights = {"a": 0.5, "b": 0.0, "c": 0.3, "d": 0.2}
    pooled = lm.pool_probabilities(components, weights)
    manual = np.exp(0.5 * np.log(components["a"]) + 0.3 * np.log(components["c"]) + 0.2 * np.log(components["d"]))
    manual /= manual.sum(axis=1, keepdims=True)
    np.testing.assert_allclose(pooled, manual, atol=1e-12)
    np.testing.assert_allclose(pooled.sum(axis=1), 1.0, atol=1e-12)
    np.testing.assert_allclose(pooled, apply_log_linear_pool([components["a"], components["c"], components["d"]], np.array([0.5, 0.3, 0.2])))


def test_pool_leaves_a_row_missing_when_a_positive_weight_component_has_no_forecast():
    rng = np.random.default_rng(2)
    a, b = rng.dirichlet(np.ones(3), size=5), rng.dirichlet(np.ones(3), size=5)
    b[2] = np.nan
    pooled = lm.pool_probabilities({"a": a, "b": b, "unused": np.full((5, 3), np.nan)}, {"a": 0.6, "b": 0.4, "unused": 0.0})
    assert np.isnan(pooled[2]).all() and not np.isnan(np.delete(pooled, 2, axis=0)).any()
    assert np.isnan(lm.pool_probabilities({"a": a}, {"a": 0.5, "b": 0.5})).all()  # a missing component: no forecast
    with pytest.raises(ValueError):
        lm.pool_probabilities({"a": a}, {"a": 0.0})


def test_pooled_forecast_of_the_fit_equals_the_weighted_components(fitted, history):
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config, include_components=True)
    one = frame[(frame["market"] == "1x2") & (frame["fixture_id"] == 90_001)].pivot(index="model_name", columns="selection", values="p_model")
    weights = WEIGHTS["1x2"]
    logs = sum(w * np.log(one.loc[m, ["home", "draw", "away"]].to_numpy(dtype=float)) for m, w in weights.items())
    expected = np.exp(logs) / np.exp(logs).sum()
    np.testing.assert_allclose(one.loc["pool_models", ["home", "draw", "away"]].to_numpy(dtype=float), expected, atol=1e-10)
    assert set(one.index) == set(weights) | {"pool_models"}  # 1X2 is the market where all components are fitted
    for market_name, weights in WEIGHTS.items():  # the components in the frame are exactly the ones with weight
        if market_name.startswith("over_under"):
            line = float(market_name.removeprefix("over_under_"))
            sub = frame[(frame["market"] == "ou") & (frame["line"] == line) & (frame["fixture_id"] == 90_001) & (frame["selection"] == "over")]
            assert set(weights) | {"pool_models"} <= set(sub["model_name"])  # every weighted component is in the frame


def test_double_chance_and_derived_markets_are_sums_of_the_pooled_1x2(fitted, history):
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config)
    pooled = frame[frame["model_name"] == "pool_models"]
    assert set(pooled["market"]) >= {"1x2", "dc", "dnb", "ah", "ou", "btts"}

    def get(fixture, market, selection, line=None):
        sub = pooled[(pooled["fixture_id"] == fixture) & (pooled["market"] == market) & (pooled["selection"] == selection)]
        sub = sub[sub["line"].isna()] if line is None else sub[sub["line"] == line]
        assert len(sub) == 1
        return float(sub["p_model"].iloc[0])

    for fixture in pooled["fixture_id"].unique():
        h, d, a = (get(fixture, "1x2", s) for s in ("home", "draw", "away"))
        assert h + d + a == pytest.approx(1.0, abs=1e-12)
        assert get(fixture, "dc", "1x") == pytest.approx(h + d)
        assert get(fixture, "dc", "x2") == pytest.approx(d + a)
        assert get(fixture, "dc", "12") == pytest.approx(h + a)
        assert get(fixture, "dnb", "home") + get(fixture, "dnb", "away") == pytest.approx(1.0)
        assert get(fixture, "dnb", "home") == pytest.approx(h / (h + a))
        assert get(fixture, "ah", "home", 0.0) == pytest.approx(h / (h + a))  # the line 0 is draw no bet
        assert get(fixture, "ah", "away", 0.0) == pytest.approx(a / (h + a))
        assert get(fixture, "ah", "home", -0.5) == pytest.approx(h)
        assert get(fixture, "ah", "away", -0.5) == pytest.approx(d + a)
        assert get(fixture, "ah", "home", 0.5) == pytest.approx(h + d)
        assert get(fixture, "ah", "away", 0.5) == pytest.approx(a)
        for line in (1.5, 2.5, 3.5):
            assert get(fixture, "ou", "over", line) + get(fixture, "ou", "under", line) == pytest.approx(1.0)
        assert get(fixture, "btts", "yes") + get(fixture, "btts", "no") == pytest.approx(1.0)
        # Over lines are ordered: more goals needed, lower probability.
        assert get(fixture, "ou", "over", 1.5) > get(fixture, "ou", "over", 2.5) > get(fixture, "ou", "over", 3.5)


def test_derived_markets_of_a_1x2_forecast():
    derived = lm.derived_1x2_markets(np.array([[0.5, 0.3, 0.2]]))
    values = {(m, s, None if math.isnan(line) else line): v[0] for (m, line, s), v in derived.items()}
    assert values[("dc", "1x", None)] == pytest.approx(0.8)
    assert values[("dc", "x2", None)] == pytest.approx(0.5)
    assert values[("dc", "12", None)] == pytest.approx(0.7)
    assert values[("dnb", "home", None)] == pytest.approx(0.5 / 0.7)
    assert values[("ah", "home", 0.0)] == pytest.approx(0.5 / 0.7) and values[("ah", "away", 0.0)] == pytest.approx(0.2 / 0.7)
    assert values[("ah", "away", 0.5)] == pytest.approx(0.2)
    assert values[("ah", "home", -0.5)] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Count distributions
# ---------------------------------------------------------------------------


def _nb_pmf(k: np.ndarray, mean: float, alpha: float) -> np.ndarray:
    r = 1.0 / alpha
    return stats.nbinom.pmf(k, r, r / (r + mean))


@pytest.mark.parametrize("line", [8.5, 9.0, 9.25, 9.5, 9.75, 10.0])
def test_count_line_probability_matches_the_settlement_of_the_distribution(line):
    mean, alpha = 9.7, 0.09
    k = np.arange(0, 80)
    pmf = _nb_pmf(k, mean, alpha)
    # Whole and half lines settle on themselves; a quarter line stakes half on each neighbouring half-step.
    halves = [(line, 1.0)] if line * 2 == int(line * 2) else [(line - 0.25, 0.5), (line + 0.25, 0.5)]
    win = sum(weight * pmf[k > c].sum() for c, weight in halves)
    loss = sum(weight * pmf[k < c].sum() for c, weight in halves)
    got = lm.count_over_probability(np.array([mean]), alpha, line, "nb2")[0]
    assert got == pytest.approx(win / (win + loss), abs=2e-6)
    # a push takes stake back: the plain probability of beating a whole line is lower than q
    if line == 10.0:
        assert got > pmf[k > 10].sum()


def test_count_lines_are_consistent_across_the_grid():
    mean = np.array([9.0, 10.5, 12.0])
    lines = lm.count_lines("corners", "total")
    over = np.column_stack([lm.count_over_probability(mean, 0.08, float(line), "nb2") for line in lines])
    assert (np.diff(over, axis=1) < 0).all()  # a higher line is always less likely to be beaten
    assert ((over > 0) & (over < 1)).all()
    np.testing.assert_allclose(lines[:3], [6.0, 6.25, 6.5])
    # a quarter line sits between its neighbours
    assert (over[:, 1] < over[:, 0]).all() and (over[:, 1] > over[:, 2]).all()
    poisson = lm.count_over_probability(mean, 0.0, 10.5, "poisson")
    np.testing.assert_allclose(poisson, stats.poisson.sf(10, mean), atol=2e-6)
    with pytest.raises(ValueError):
        lm.count_over_probability(mean, 0.1, 9.6, "nb2")


def test_count_rows_of_a_fit_price_every_line_and_are_complete(fitted, history):
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config)
    counts = frame[frame["model_name"] == "count_nb2"]
    assert set(counts["market"]) == set(lm.COUNT_MARKET_NAMES.values())
    for (fixture, market), group in counts.groupby(["fixture_id", "market"]):
        stat, scope = next(k for k, v in lm.COUNT_MARKET_NAMES.items() if v == market)
        lines = lm.count_lines(stat, scope)
        over = group[group["selection"] == "over"].sort_values("line")
        under = group[group["selection"] == "under"].sort_values("line")
        np.testing.assert_allclose(over["line"].to_numpy(), lines)
        np.testing.assert_allclose(over["p_model"].to_numpy() + under["p_model"].to_numpy(), 1.0, atol=1e-12)
        assert over["p_model"].is_monotonic_decreasing
        assert (group["period"] == "ft").all()


def test_home_and_away_totals_of_the_count_model_add_up_in_the_mean(fitted, history):
    """The match total's median line sits between the team medians: a sanity check of the scopes' wiring."""
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config)
    row = frame[(frame["fixture_id"] == 90_001) & (frame["market"] == "corners_ou") & (frame["selection"] == "over")].set_index("line")["p_model"]
    home = frame[(frame["fixture_id"] == 90_001) & (frame["market"] == "corners_home_ou") & (frame["selection"] == "over")].set_index("line")["p_model"]
    away = frame[(frame["fixture_id"] == 90_001) & (frame["market"] == "corners_away_ou") & (frame["selection"] == "over")].set_index("line")["p_model"]
    total_median = row.index[np.argmin(np.abs(row.to_numpy() - 0.5))]
    home_median = home.index[np.argmin(np.abs(home.to_numpy() - 0.5))]
    away_median = away.index[np.argmin(np.abs(away.to_numpy() - 0.5))]
    assert home_median + away_median - 1.5 <= total_median <= home_median + away_median + 1.5


# ---------------------------------------------------------------------------
# Coverage rules, cache, report
# ---------------------------------------------------------------------------


def test_fixtures_of_unknown_or_short_leagues_get_no_rows(tmp_path, history):
    matches, stats_frame = history
    short = matches[matches["league_code"] == "BBB"].iloc[:30]  # fewer than min_train_matches
    partial = pd.concat([matches[matches["league_code"] == "AAA"], short], ignore_index=True)
    config = config_for(tmp_path)
    manifest = fit(config, (partial, stats_frame), matches=partial)
    assert "BBB" not in manifest["leagues"] and "AAA" in manifest["leagues"]
    fixtures = fixtures_for(history, n_per_league=1)
    fixtures = pd.concat([fixtures, fixtures.assign(fixture_id=fixtures["fixture_id"] + 100, league_id=99999)], ignore_index=True)
    frame, report = lm.forecast_with_report(fixtures, AS_OF, config=config)
    status = dict(zip(report["fixture_id"], report["status"]))
    assert status[90_001] == "ok"
    assert status[90_002] == "no_league_history"  # a league of the archive without enough matches
    assert status[90_101] == "no_league_history"
    assert status[90_102] == "no_league_history"  # a league that is not in the archive
    assert set(frame["fixture_id"]) == {90_001}
    assert frame.attrs["report"].equals(report)


def test_fixtures_that_are_not_upcoming_get_no_rows(fitted, history):
    config, _ = fitted
    fixtures = fixtures_for(history, n_per_league=1)
    fixtures.loc[0, "status_short"] = "1H"
    fixtures.loc[1, "kickoff_utc"] = "2026-09-21T11:00:00+00:00"  # already started at as_of
    frame, report = lm.forecast_with_report(fixtures, AS_OF, config=config)
    assert list(report["status"]) == ["not_upcoming", "not_upcoming"]
    assert frame.empty and list(frame.columns) == list(lm.MODEL_FRAME_COLUMNS)


def test_a_fixture_is_upcoming_only_strictly_after_as_of(fitted, history):
    config, _ = fitted
    fixtures = fixtures_for(history, n_per_league=1)
    fixtures["kickoff_utc"] = [AS_OF.isoformat(), (AS_OF + pd.Timedelta(seconds=1)).isoformat()]
    _, report = lm.forecast_with_report(fixtures, AS_OF, config=config)
    assert list(report["status"]) == ["not_upcoming", "ok"]


def test_probabilities_are_valid_and_the_frame_has_the_contract_columns(fitted, history):
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config)
    assert list(frame.columns) == list(lm.MODEL_FRAME_COLUMNS)
    assert frame["p_model"].between(0.0, 1.0).all() and not frame["p_model"].isna().any()
    assert frame["fixture_id"].dtype == np.int64 and frame["line"].dtype == np.float64
    one = frame[(frame["market"] == "1x2") & (frame["model_name"] == "pool_models")]
    np.testing.assert_allclose(one.groupby("fixture_id")["p_model"].sum().to_numpy(), 1.0, atol=1e-12)
    assert set(frame["model_name"]) == {"pool_models", "count_nb2"}
    assert not frame.duplicated(["fixture_id", "market", "period", "line", "selection", "model_name"]).any()


def test_predict_only_run_needs_no_history_and_is_repeatable(fitted, history):
    config, manifest = fitted
    assert not config.history_db.exists()  # nothing to read but the cache
    fixtures = fixtures_for(history)
    first = lm.forecast_fixtures(fixtures, AS_OF + pd.Timedelta(hours=5), config=config)
    second = lm.forecast_fixtures(fixtures.sample(frac=1.0, random_state=1), AS_OF + pd.Timedelta(hours=9), config=config)
    key = ["fixture_id", "market", "line", "selection", "model_name"]
    a = first.sort_values(key, ignore_index=True)
    b = second.sort_values(key, ignore_index=True)
    pd.testing.assert_frame_equal(a, b)
    assert manifest["seconds"]["total"] > 0


def test_stale_and_missing_fits_give_no_rows(fitted, history, tmp_path):
    config, _ = fitted
    fixtures = fixtures_for(history, n_per_league=1)
    frame, report = lm.forecast_with_report(fixtures, AS_OF + pd.Timedelta(days=10), config=config)
    assert frame.empty and set(report["status"]) == {"fit_stale"}
    frame, report = lm.forecast_with_report(fixtures, AS_OF, config=config_for(tmp_path / "empty"))
    assert frame.empty and set(report["status"]) == {"no_fit"}


def test_manifest_records_the_fit(fitted):
    config, manifest = fitted
    assert manifest["version"] == lm.LIVE_VERSION
    assert set(manifest["leagues"]) == {"AAA", "BBB"}
    assert manifest["weights"]["1x2"]["dixon_coles"] == 0.3
    assert set(manifest["models_needed"]) == {"baseline_league_freq", "dixon_coles", "nb2", "gas_poisson", "elo_davidson", "pi_gbm", "pi_ratings"}
    assert manifest["pi"]["usable"] and set(manifest["pi"]["boosters"]) == set(lm.GOAL_MARKET_NAMES)
    assert manifest["count_stats"] == ["corners", "cards"]
    on_disk = json.loads((config.fits_dir / "20260921" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["fit_as_of"] == manifest["fit_as_of"]
    files = {p.name for p in (config.fits_dir / "20260921").glob("*.pkl")}
    assert {"dixon_coles__AAA.pkl", "gas_poisson__BBB.pkl", "count_corners__AAA.pkl", "count_cards__BBB.pkl", "pi_ratings__pooled.pkl", "pi_inputs.pkl"} <= files
    found = lm.latest_fit(config, AS_OF + pd.Timedelta(hours=1))
    assert found is not None and found[1]["fit_as_of"] == manifest["fit_as_of"]
    assert lm.latest_fit(config, AS_OF - pd.Timedelta(seconds=1)) is None


def test_baseline_component_equals_the_framework_baseline(history):
    from model_buk.evaluation.framework import LeagueFrequencyBaseline, prepare_matches

    matches, _ = history
    prep = prepare_matches(matches, MARKET_1X2, lm.LiveConfig().walk_forward())
    train = prep.frame[(prep.frame["league_code"] == "AAA") & (prep.frame["kickoff"] < CUTOFF)]
    mine = lm._baseline_probabilities(train)
    for market in GOAL_MARKETS:
        reference = LeagueFrequencyBaseline(market).fit(train).predict(train.iloc[:1])[0]
        np.testing.assert_allclose(mine[market.name], reference, atol=1e-15)


# ---------------------------------------------------------------------------
# Weights, hyper-parameters, loaders
# ---------------------------------------------------------------------------


def _comparison_db(path, rows):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE pool_weights (market TEXT, sample TEXT, scored_season INTEGER, fit_season INTEGER, kind TEXT, model TEXT, weight REAL, objective REAL, converged INTEGER)"
        )
        connection.executemany("INSERT INTO pool_weights VALUES (?,?,?,?,?,?,?,?,?)", rows)
        connection.commit()


def test_pool_weights_come_from_the_comparison_table(tmp_path):
    db = tmp_path / "cmp.sqlite"
    rows = []
    for season, w in ((2025, 0.3), (2026, 0.6)):
        rows += [
            ("1x2", "selection", season, season - 1, "log_linear", "dixon_coles", w, 1.0, 1),
            ("1x2", "selection", season, season - 1, "log_linear", "pi_gbm", 1 - w, 1.0, 1),
            ("1x2", "selection", season, season - 1, "linear", "dixon_coles", 0.9, 1.0, 1),
            ("1x2", "selection", season, season - 1, "linear", "pi_gbm", 0.1, 1.0, 1),
        ]
    _comparison_db(db, rows)
    weights, meta = lm.load_pool_weights(2026, db)
    assert weights == {"1x2": {"dixon_coles": 0.6, "pi_gbm": 0.4}}
    assert meta["season_used"] == 2026 and meta["fit_season"] == 2025
    weights, meta = lm.load_pool_weights(2027, db)  # not stored yet: the latest stored season
    assert meta["season_used"] == 2026 and meta["requested_season"] == 2027
    weights, meta = lm.load_pool_weights(2025, db)
    assert weights["1x2"]["dixon_coles"] == 0.3
    with pytest.raises(ValueError):
        lm.load_pool_weights(2024, db)
    assert lm.models_needed({"1x2": {"dixon_coles": 0.6, "pi_gbm": 0.4, "nb2": 0.0}}) == ("dixon_coles", "pi_gbm")


def test_weights_of_the_real_comparison_are_a_distribution_when_present():
    path = lm.PREDICTIONS_DIR / "model_comparison.sqlite"
    if not path.exists():
        pytest.skip("comparison tables not available")
    weights, meta = lm.load_pool_weights(2026, path)
    assert set(weights) == set(lm.GOAL_MARKET_NAMES)
    for market, values in weights.items():
        assert sum(values.values()) == pytest.approx(1.0, abs=1e-6)
    assert meta["season_used"] == 2026


def test_hyperparameter_selection_is_the_frameworks_inner_fold(history):
    matches, _ = history
    config = lm.LiveConfig(legacy_booster_dir=None).walk_forward()
    chosen = lm._select_dc_nb2("dixon_coles", matches, 2026, config)
    outer = WalkForwardConfig(test_seasons=[2026], scope="per_league", inner_seasons=1, select_per="pooled")
    for market in GOAL_MARKETS:
        result = evaluate_model(dixon_coles_spec(market), matches, market, outer)
        picked = json.loads(result.selections[result.selections["outer_season"] == 2026]["params"].iloc[0])
        assert chosen[market.name]["params"] == picked
    chosen_nb2 = lm._select_dc_nb2("nb2", matches, 2026, config)
    result = evaluate_model(nb2_spec(MARKET_1X2), matches, MARKET_1X2, outer)
    assert chosen_nb2["1x2"]["params"] == json.loads(result.selections["params"].iloc[0])


def test_select_hyperparameters_returns_every_grid_and_stores_the_choice(tmp_path, history):
    matches, stats_frame = history
    config = config_for(tmp_path)
    out = lm.select_hyperparameters(
        2026, matches, stats_frame, config=config, models=("dixon_coles", "pi_gbm", "elo_davidson"), families=("corners",), log=lambda t: None
    )
    assert set(out["dixon_coles"]) == set(lm.GOAL_MARKET_NAMES)
    assert all(p["half_life_days"] in {180.0, 365.0, 730.0, 1200.0} for p in out["dixon_coles"].values())
    assert all(p["depth"] in {4, 6} and p["learning_rate"] == 0.08 for p in out["pi_gbm"].values())
    assert out["elo_davidson"]["1x2"]["k_factor"] in {10.0, 20.0, 30.0, 40.0}
    assert set(out["count"]["corners"]) == {"total", "home", "away"}
    assert lm.load_hyperparameters(config.cache_dir, 2026)["season"] == 2026
    assert lm.load_hyperparameters(config.cache_dir, 2027) is None


def _history_db(path, matches, stats_frame, *, extra_league: bool):
    columns = [
        "fixture_id", "league_id", "league_code", "season", "round", "kickoff_utc", "status_short", "venue_id", "referee",
        "home_team_id", "home_team", "away_team_id", "away_team", "home_goals", "away_goals", "ht_home_goals", "ht_away_goals",
        "et_home_goals", "et_away_goals", "pen_home", "pen_away",
    ]  # fmt: skip
    base = matches[columns].copy()
    if extra_league:
        other = base[base["league_code"] == "BBB"].copy()
        other["league_id"] = 9999
        other["league_code"] = "ZZZ"
        other["fixture_id"] += 500_000
        base = pd.concat([base, other], ignore_index=True)
    with closing(sqlite3.connect(path)) as connection:
        base.to_sql("matches", connection, index=False)
        side = matches[["fixture_id", "home_team_id", "away_team_id", "home_shots", "away_shots", "home_sot", "away_sot",
                        "home_corners", "away_corners", "home_fouls", "away_fouls", "home_yellow", "away_yellow"]]  # fmt: skip
        team_rows = []
        for r in side.itertuples():
            for s, team in (("home", r.home_team_id), ("away", r.away_team_id)):
                team_rows.append(
                    {"fixture_id": r.fixture_id, "team_id": team, "side": s, "shots_on_goal": getattr(r, f"{s}_sot"), "shots_off_goal": 0.0,
                     "total_shots": getattr(r, f"{s}_shots"), "corners": getattr(r, f"{s}_corners"), "fouls": getattr(r, f"{s}_fouls"),
                     "yellow_cards": getattr(r, f"{s}_yellow") if r.fixture_id % 3 else None, "expected_goals": None}
                )  # fmt: skip
        pd.DataFrame(team_rows).to_sql("team_match_stats", connection, index=False)
        events = []
        for r in side.iloc[:200].itertuples():
            events.append({"fixture_id": r.fixture_id, "seq": 1, "team_id": r.home_team_id, "player_id": 5, "type": "Card", "detail": "Yellow Card"})
        pd.DataFrame(events).to_sql("events", connection, index=False)
        connection.execute("CREATE TABLE build_info (built_at TEXT)")
        connection.execute("INSERT INTO build_info VALUES ('2026-09-21T03:00:00+00:00')")
        connection.commit()


def test_load_live_matches_equals_the_protocol_loader_and_keeps_more_leagues(tmp_path, history):
    matches, stats_frame = history
    path = tmp_path / "history.sqlite"
    _history_db(path, matches, stats_frame, extra_league=True)
    mine = lm.load_live_matches(path)
    protocol = load_count_matches(path)
    assert set(mine["league_code"]) == {"AAA", "BBB", "ZZZ"} and set(protocol["league_code"]) == {"AAA", "BBB"}
    both = mine[mine["league_code"].isin(["AAA", "BBB"])].reset_index(drop=True)
    pd.testing.assert_frame_equal(both, protocol.reset_index(drop=True))
    only = lm.load_live_matches(path, league_ids=frozenset({39}))
    assert set(only["league_code"]) == {"AAA"}
    assert lm.history_built_at(path) == "2026-09-21T03:00:00+00:00"
    assert lm.history_built_at(tmp_path / "missing.sqlite") is None
    assert len(lm.load_team_stats(path)) == len(stats_frame)


def test_prematch_fixtures_loader_returns_the_fixtures_frame_layout(tmp_path):
    path = tmp_path / "prematch.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE fixtures (fixture_id INTEGER, league_id INTEGER, season INTEGER, round TEXT, kickoff_utc TEXT, status_short TEXT, "
            "home_team_id INTEGER, home_team TEXT, away_team_id INTEGER, away_team TEXT, first_seen_at TEXT, last_seen_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 39, 2026, "R1", "2026-09-30T18:00:00+00:00", "NS", 10, "A", 11, "B", "x", "y"),
                (2, 39, 2026, "R1", "2026-10-05T18:00:00+00:00", "NS", 12, "C", 13, "D", "x", "y"),
            ],
        )
        connection.commit()
    frame = lm.load_prematch_fixtures(path, "2026-09-30T00:00:00Z", "2026-10-02T00:00:00Z", league_names={39: ("Premier League", "England")})
    assert list(frame["fixture_id"]) == [1]
    assert list(frame.columns) == [
        "fixture_id", "league_id", "league_name", "country", "season", "round", "kickoff_utc", "status_short",
        "home_team_id", "home_team", "away_team_id", "away_team", "home_goals", "away_goals",
    ]  # fmt: skip
    assert frame["league_name"].iloc[0] == "Premier League" and frame["home_goals"].isna().all()


# ---------------------------------------------------------------------------
# League scope: protocol leagues, extra leagues, cups
# ---------------------------------------------------------------------------


def test_extra_leagues_are_modelled_but_flagged_and_cups_are_left_out(tmp_path):
    leagues = {"AAA": 39, "BBB": 140, "CCC": 253, "CUP": 45}  # two protocol leagues, MLS (extra), FA Cup (cup)
    matches, stats_frame = make_history(n_rounds=100, leagues=leagues)
    config = config_for(tmp_path)
    manifest = fit(config, (matches, stats_frame), matches=matches)
    assert set(manifest["leagues"]) == {"AAA", "BBB", "CCC"}  # the cup is never modelled
    protocol_rows = matches[matches["league_id"].isin([39, 140])]
    stamp = pd.to_datetime(protocol_rows["kickoff_utc"], utc=True)
    cut = pd.Timestamp(manifest["pi"]["cut_ns"], tz="UTC")
    assert manifest["pi"]["n_booster_rows"] == int(((stamp < cut) & (stamp < CUTOFF)).sum())  # boosters: protocol leagues only
    fixtures = pd.DataFrame(
        {
            "fixture_id": [1, 2, 3],
            "league_id": [39, 253, 45],
            "kickoff_utc": "2026-09-26T15:00:00+00:00",
            "home_team_id": [3900, 25300, 4500],
            "away_team_id": [3901, 25301, 4501],
            "status_short": "NS",
        }
    )
    frame, report = lm.forecast_with_report(fixtures, AS_OF, config=config)
    by_fixture = report.set_index("fixture_id")
    assert list(by_fixture["status"]) == ["ok", "ok", "no_league_history"]
    assert list(by_fixture["validated"]) == [True, False, False]
    assert set(frame["fixture_id"]) == {1, 2}
    assert (frame[frame["fixture_id"] == 2]["market"] == "1x2").any()  # the extra league gets the full set of markets


def test_loader_keeps_only_matches_before_the_cutoff_when_asked(tmp_path, history):
    matches, stats_frame = history
    path = tmp_path / "history.sqlite"
    _history_db(path, matches, stats_frame, extra_league=False)
    everything = lm.load_live_matches(path)
    early = lm.load_live_matches(path, before=CUTOFF)
    stamp = pd.to_datetime(everything["kickoff_utc"], utc=True, format="mixed")
    assert len(early) == int((stamp < CUTOFF).sum()) < len(everything)


def test_parallel_fit_gives_the_same_forecasts_as_the_sequential_fit(tmp_path, history):
    """Worker processes (which import the module afresh) must fit exactly what the main process fits."""
    fixtures = fixtures_for(history)
    sequential = config_for(tmp_path / "seq")
    parallel = config_for(tmp_path / "par")
    fit(sequential, history)
    fit(parallel, history, n_jobs=2)
    a, b = _forecast_dict(sequential, fixtures), _forecast_dict(parallel, fixtures)
    assert a.keys() == b.keys() and len(a) > 100
    np.testing.assert_allclose([a[k] for k in a], [b[k] for k in a], atol=1e-12)


def test_live_forecasts_equal_the_protocol_forecasts_at_the_same_cutoff(tmp_path, history):
    """The live path is the protocol's model: same training cut-off, same forecasts (DC, NB2, GAS, pi models, corners)."""
    from model_buk.evaluation.count_markets import FAMILIES, count_config, count_market
    from model_buk.evaluation.framework import over_under
    from model_buk.models.count_models import CountEngine, count_spec
    from model_buk.models.dynamic_adapters import gas_poisson_spec
    from model_buk.models.gbm_model import PiGbmEngine, pi_gbm_spec, pi_logit_spec

    matches, stats_frame = history
    round_start = pd.Timestamp("2026-08-01T15:00:00Z")  # a Saturday round of league AAA
    stamp = pd.to_datetime(matches["kickoff_utc"], utc=True)
    rows = matches[(stamp == round_start) & (matches["league_code"] == "AAA")]
    assert len(rows) == N_TEAMS // 2
    fixtures = pd.DataFrame(
        {
            "fixture_id": rows["fixture_id"], "league_id": rows["league_id"], "kickoff_utc": rows["kickoff_utc"],
            "home_team_id": rows["home_team_id"], "away_team_id": rows["away_team_id"], "status_short": "NS", "season": 2026,
            "referee": rows["referee"],  # the protocol knows the referee of a fixture; a live fixture often does not
        }
    )  # fmt: skip
    as_of = round_start - pd.Timedelta(minutes=1)  # cut-off = the protocol's matchday start - 3 h, to the minute
    config = config_for(tmp_path)
    lm.fit_live_models(as_of, config=config, matches=matches, team_stats=stats_frame, weights=WEIGHTS, hyperparameters=HYPERPARAMETERS, log=lambda t: None)
    live = lm.forecast_fixtures(fixtures, as_of, config=config, include_components=True)

    def live_probs(model: str, market: str, selections: tuple[str, ...], line: float | None = None) -> np.ndarray:
        sub = live[(live["model_name"] == model) & (live["market"] == market)]
        sub = sub[sub["line"].isna()] if line is None else sub[sub["line"] == line]
        return np.column_stack([sub[sub["selection"] == s].set_index("fixture_id")["p_model"].reindex(rows["fixture_id"]).to_numpy() for s in selections])

    def protocol_probs(result, columns: tuple[str, ...]) -> np.ndarray:
        frame = result.predictions.set_index("fixture_id").reindex(rows["fixture_id"])
        return frame[list(columns)].to_numpy(dtype=float)

    config_wf = WalkForwardConfig(test_seasons=[2026], scope="per_league", inner_seasons=1, select_per="pooled")
    one = ("home", "draw", "away")
    cols_1x2 = ("p_home", "p_draw", "p_away")
    cases = [
        ("dixon_coles", live_probs("dixon_coles", "1x2", one), protocol_probs(evaluate_model(dixon_coles_spec(MARKET_1X2, half_lives=[365.0]), matches, MARKET_1X2, config_wf), cols_1x2)),
        ("dixon_coles ou2.5", live_probs("dixon_coles", "ou", ("under", "over"), 2.5), protocol_probs(evaluate_model(dixon_coles_spec(over_under(2.5), half_lives=[730.0]), matches, over_under(2.5), config_wf), ("p_under", "p_over"))),
        ("nb2", live_probs("nb2", "1x2", one), protocol_probs(evaluate_model(nb2_spec(MARKET_1X2, half_lives=[365.0]), matches, MARKET_1X2, config_wf), cols_1x2)),
        ("gas_poisson", live_probs("gas_poisson", "1x2", one), protocol_probs(evaluate_model(gas_poisson_spec(MARKET_1X2), matches, MARKET_1X2, config_wf), cols_1x2)),
    ]  # fmt: skip
    grid = {"learning_rate": [0.08], "depth": [3]}
    engine = PiGbmEngine(matches, stats_frame, settings=GBM, cache_dir=tmp_path / "protocol_boosters")
    cases.append(("pi_gbm", live_probs("pi_gbm", "1x2", one), protocol_probs(evaluate_model(pi_gbm_spec(engine, MARKET_1X2, grid), matches, MARKET_1X2, config_wf), cols_1x2)))
    logit_engine = PiGbmEngine(matches, stats_frame, settings=GBM, kind="logit")
    cases.append(("pi_ratings", live_probs("pi_ratings", "1x2", one), protocol_probs(evaluate_model(pi_logit_spec(logit_engine, MARKET_1X2), matches, MARKET_1X2, config_wf), cols_1x2)))
    for name, mine, theirs in cases:
        assert not np.isnan(mine).any() and not np.isnan(theirs).any(), name
        np.testing.assert_allclose(mine, theirs, atol=1e-6, err_msg=name)

    # Corners, match total 9.5: the same regression, the same cut-off.
    family = FAMILIES["corners"]
    market = count_market(family, "total", 9.5)
    spec = count_spec(CountEngine("corners", "nb2"), "total", 9.5, grid={"ridge": [1000.0], "half_life_days": [None]})
    result = evaluate_model(spec, matches, market, count_config(config_wf))
    theirs = protocol_probs(result, ("p_over",))
    mine = live[(live["model_name"] == "count_nb2") & (live["market"] == "corners_ou") & (live["line"] == 9.5) & (live["selection"] == "over")]
    mine = mine.set_index("fixture_id")["p_model"].reindex(rows["fixture_id"]).to_numpy()
    # the protocol run warm-starts the IRLS from the previous matchday: same optimum up to the solver tolerance
    np.testing.assert_allclose(mine, theirs[:, 0], atol=1e-5)


def test_a_team_the_league_has_never_seen_follows_the_models_prior(fitted, history):
    """A promoted / new team gets forecasts from each model's newcomer prior, never a hole or a made-up number."""
    config, _ = fitted
    fixtures = fixtures_for(history, n_per_league=1).iloc[:1].copy()
    known = lm.forecast_fixtures(fixtures, AS_OF, config=config)
    fixtures["home_team_id"] = 987_654  # not in the training data of any league
    unseen = lm.forecast_fixtures(fixtures, AS_OF, config=config, include_components=True)
    pooled = unseen[unseen["model_name"] == "pool_models"]
    assert not pooled.empty and pooled["p_model"].between(0, 1).all()
    assert {"1x2", "ou", "btts", "dc"} <= set(pooled["market"])
    assert {"corners_ou", "cards_ou"} <= set(unseen["market"])  # the count regressions fall back on the league level

    def one(frame: pd.DataFrame) -> pd.Series:
        return frame[(frame["model_name"] == "pool_models") & (frame["market"] == "1x2")].set_index("selection")["p_model"]

    assert abs(one(unseen)["home"] - one(known)["home"]) > 1e-6  # the forecast really is another team's
    assert one(unseen).sum() == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Review additions: labels, per-market pooling, count scopes, fit choice, provenance, API budget
# ---------------------------------------------------------------------------


def test_goal_market_labels_follow_the_class_order_of_each_market(fitted, history):
    """Row labels (home/draw/away, under/over, no/yes) must match each market's outcome definition.

    The baseline component is the Laplace-smoothed league frequency of the raw results, so its rows can be
    checked against a count of the training matches: a swapped label (BTTS yes/no, over/under) cannot pass.
    """
    config, _ = fitted
    matches, _ = history
    frame = lm.forecast_fixtures(fixtures_for(history, n_per_league=1), AS_OF, config=config, include_components=True)
    baseline = frame[(frame["model_name"] == lm.BASELINE_NAME) & (frame["fixture_id"] == 90_001)]  # league AAA
    train = matches[(matches["league_code"] == "AAA") & (pd.to_datetime(matches["kickoff_utc"], utc=True) < CUTOFF)]
    home, away = train["home_goals"].to_numpy(), train["away_goals"].to_numpy()
    n = len(train)
    expected = {
        ("1x2", None, "home"): ((home > away).sum() + 1) / (n + 3),
        ("1x2", None, "draw"): ((home == away).sum() + 1) / (n + 3),
        ("1x2", None, "away"): ((home < away).sum() + 1) / (n + 3),
        ("btts", None, "yes"): (((home > 0) & (away > 0)).sum() + 1) / (n + 2),
        ("btts", None, "no"): (n - ((home > 0) & (away > 0)).sum() + 1) / (n + 2),
        ("ah", -0.5, "home"): ((home > away).sum() + 1) / (n + 3),
        ("ah", 0.5, "away"): ((home < away).sum() + 1) / (n + 3),
    }
    for line in (1.5, 2.5, 3.5):
        over = ((home + away) > line).sum()
        expected[("ou", line, "over")] = (over + 1) / (n + 2)
        expected[("ou", line, "under")] = (n - over + 1) / (n + 2)
    for (market, line, selection), value in expected.items():
        sub = baseline[(baseline["market"] == market) & (baseline["selection"] == selection)]
        sub = sub[sub["line"].isna()] if line is None else sub[sub["line"] == line]
        assert len(sub) == 1, (market, line, selection)
        assert float(sub["p_model"].iloc[0]) == pytest.approx(value, abs=1e-12), (market, line, selection)


def test_every_goal_market_is_pooled_with_its_own_weights(fitted, history):
    """The pooled rows of every market, line and fixture equal the normalised weighted geometric mean of its components."""
    config, _ = fitted
    frame = lm.forecast_fixtures(fixtures_for(history), AS_OF, config=config, include_components=True)
    layout = {"1x2": ("1x2", math.nan, ("home", "draw", "away")), "btts": ("btts", math.nan, ("no", "yes"))}
    for line in (1.5, 2.5, 3.5):
        layout[f"over_under_{line}"] = ("ou", line, ("under", "over"))

    def matrix(model: str, market: str, line: float, selections: tuple[str, ...]) -> pd.DataFrame:
        sub = frame[(frame["model_name"] == model) & (frame["market"] == market)]
        sub = sub[sub["line"].isna()] if math.isnan(line) else sub[sub["line"] == line]
        return sub.pivot(index="fixture_id", columns="selection", values="p_model")[list(selections)].sort_index()

    assert set(layout) == set(WEIGHTS)
    for market_name, weights in WEIGHTS.items():
        market, line, selections = layout[market_name]
        pooled = matrix("pool_models", market, line, selections)
        assert len(pooled) == len(fixtures_for(history))
        score = sum(w * np.log(matrix(model, market, line, selections).loc[pooled.index].to_numpy()) for model, w in weights.items())
        expected = np.exp(score - score.max(axis=1, keepdims=True))
        expected /= expected.sum(axis=1, keepdims=True)
        np.testing.assert_allclose(pooled.to_numpy(), expected, atol=1e-10, err_msg=market_name)


def test_count_scopes_and_their_own_parameters_equal_the_protocol_forecasts(tmp_path, history):
    """Home / away / total scopes and the per-scope (ridge, half-life) of both count families, against the framework."""
    from model_buk.evaluation.count_markets import FAMILIES, count_config, count_market
    from model_buk.models.count_models import CountEngine, count_spec

    matches, stats_frame = history
    round_start = pd.Timestamp("2026-08-01T15:00:00Z")
    stamp = pd.to_datetime(matches["kickoff_utc"], utc=True)
    rows = matches[(stamp == round_start) & (matches["league_code"] == "AAA")]
    fixtures = pd.DataFrame(
        {
            "fixture_id": rows["fixture_id"], "league_id": rows["league_id"], "kickoff_utc": rows["kickoff_utc"],
            "home_team_id": rows["home_team_id"], "away_team_id": rows["away_team_id"], "status_short": "NS", "season": 2026,
            "referee": rows["referee"],
        }
    )  # fmt: skip
    # Very different settings per scope, so that a scope wired to another scope's parameters cannot pass.
    varied = {
        "corners": {
            "total": {"ridge": 1000.0, "half_life_days": None},
            "home": {"ridge": 100000.0, "half_life_days": 400.0},
            "away": {"ridge": 10.0, "half_life_days": None},
        },
        "yellow": {
            "total": {"ridge": 1000.0, "half_life_days": None},
            "home": {"ridge": 100000.0, "half_life_days": None},
            "away": {"ridge": 10.0, "half_life_days": 400.0},
        },
    }
    as_of = round_start - pd.Timedelta(minutes=1)
    config = config_for(tmp_path)
    lm.fit_live_models(
        as_of, config=config, matches=matches, team_stats=stats_frame, weights=WEIGHTS,
        hyperparameters={**HYPERPARAMETERS, "count": varied}, leagues=["AAA"], log=lambda t: None,
    )  # fmt: skip
    live = lm.forecast_fixtures(fixtures, as_of, config=config)
    config_wf = count_config(WalkForwardConfig(test_seasons=[2026], scope="per_league", inner_seasons=1, select_per="pooled"))
    upto = matches[stamp <= round_start]  # the protocol run needs nothing after the round it forecasts
    cases = [
        ("corners", "corners", "total", 9.5), ("corners", "corners", "home", 4.5), ("corners", "corners", "away", 4.5),
        ("yellow", "cards", "total", 4.5), ("yellow", "cards", "home", 1.5), ("yellow", "cards", "away", 2.5),
    ]  # fmt: skip
    distinct = set()
    for family, stat, scope, line in cases:
        params = varied[family][scope]
        grid = {"ridge": [params["ridge"]], "half_life_days": [params["half_life_days"]]}
        spec = count_spec(CountEngine(family, "nb2"), scope, line, grid=grid)
        result = evaluate_model(spec, upto, count_market(FAMILIES[family], scope, line), config_wf)
        theirs = result.predictions.set_index("fixture_id").reindex(rows["fixture_id"])["p_over"].to_numpy(dtype=float)
        mine = live[(live["market"] == lm.COUNT_MARKET_NAMES[(stat, scope)]) & (live["line"] == line) & (live["selection"] == "over")]
        mine = mine.set_index("fixture_id")["p_model"].reindex(rows["fixture_id"]).to_numpy()
        assert not np.isnan(mine).any() and not np.isnan(theirs).any(), (family, scope)
        np.testing.assert_allclose(mine, theirs, atol=1e-5, err_msg=f"{family} {scope} {line}")
        distinct.add(tuple(np.round(mine, 4)))
    assert len(distinct) == len(cases)  # every scope gives its own numbers (not a copy of another scope)


def test_fit_from_the_history_database_uses_the_cutoff_and_records_the_build(tmp_path, history, monkeypatch):
    """The real path (no frames handed in): the loader is asked for matches before the cut-off; the build time is recorded."""
    matches, stats_frame = history
    db = tmp_path / "history.sqlite"
    _history_db(db, matches, stats_frame, extra_league=False)
    seen: dict = {}
    real_loader = lm.load_live_matches

    def spy(path, league_ids=None, **kwargs):
        seen.update(kwargs)
        return real_loader(path, league_ids, **kwargs)

    monkeypatch.setattr(lm, "load_live_matches", spy)
    config = lm.LiveConfig(
        cache_dir=tmp_path / "cache", history_db=db, comparison_db=tmp_path / "none.sqlite", predictions_dir=tmp_path / "none",
        legacy_booster_dir=None, gbm_settings=GBM,
    )  # fmt: skip
    messages: list[str] = []
    manifest = lm.fit_live_models(AS_OF, config=config, weights=WEIGHTS, hyperparameters=HYPERPARAMETERS, leagues=["AAA"], log=messages.append)
    assert lm.utc(seen["before"]) == CUTOFF
    assert set(manifest["leagues"]) == {"AAA"} and pd.Timestamp(manifest["max_train_kickoff"]) < CUTOFF
    assert manifest["history_built_at"] == "2026-09-21T03:00:00+00:00"
    assert not any("WARNING: the history tables" in m for m in messages)  # built 9 h before the fit: fresh
    stale = lm.fit_live_models(AS_OF + pd.Timedelta(days=3), config=config, weights=WEIGHTS, hyperparameters=HYPERPARAMETERS, leagues=["AAA"], families=(), log=messages.append)
    assert stale["history_built_at"] == "2026-09-21T03:00:00+00:00"
    assert any("WARNING: the history tables" in m for m in messages)  # a fit three days after the build says so


def test_latest_fit_is_the_newest_one_at_or_before_as_of(tmp_path, history):
    config = config_for(tmp_path)
    first = fit(config, history, leagues=["AAA"], families=())
    second = fit(config, history, as_of=AS_OF + pd.Timedelta(days=1), leagues=["AAA"], families=())
    assert first["fit_as_of"] != second["fit_as_of"]
    assert lm.latest_fit(config, AS_OF + pd.Timedelta(hours=5))[1]["fit_as_of"] == first["fit_as_of"]
    assert lm.latest_fit(config, AS_OF + pd.Timedelta(days=1, seconds=1))[1]["fit_as_of"] == second["fit_as_of"]
    assert lm.latest_fit(config, AS_OF + pd.Timedelta(days=5))[1]["fit_as_of"] == second["fit_as_of"]


def test_forecast_frame_names_the_fit_that_produced_it(fitted, history):
    config, manifest = fitted
    frame = lm.forecast_fixtures(fixtures_for(history, n_per_league=1), AS_OF + pd.Timedelta(hours=2), config=config)
    assert frame.attrs["fit_as_of"] == manifest["fit_as_of"] and frame.attrs["train_cutoff"] == manifest["train_cutoff"]
    assert "history_built_at" in frame.attrs and "report" in frame.attrs


def _fit_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "fit_live_models.py"
    spec = importlib.util.spec_from_file_location("fit_live_models_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _listed(fixture_id: int, kickoff: str) -> dict:
    return {
        "fixture": {"id": fixture_id, "date": kickoff, "status": {"short": "NS"}},
        "league": {"id": 39, "season": 2026, "round": "Regular Season - 8", "name": "Premier League", "country": "England"},
        "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
    }


def test_listing_fixtures_from_the_api_respects_the_quota_reserve_and_the_day_cap(monkeypatch):
    script = _fit_script()
    import download_history

    calls: list[str] = []
    quota = iter([5000, 1400, 1300])  # the second answer is below the reserve: no third request may follow
    listing = {"empty": False}

    class FakeClient:
        def __init__(self, key: str) -> None:
            pass

        def get(self, endpoint: str, params: dict):
            calls.append(params["date"])
            body = {"response": [] if listing["empty"] else [_listed(len(calls), f"{params['date']}T18:00:00+00:00")]}
            return body, {"x-ratelimit-requests-remaining": str(next(quota))}

    monkeypatch.setattr(download_history, "RealHistoryClient", FakeClient)
    monkeypatch.setenv("API_FOOTBALL_KEY", "dummy")
    as_of = pd.Timestamp("2026-09-30T10:00:00Z")
    frame = script.fixtures_from_api(as_of, 4)
    assert calls == ["2026-09-30", "2026-10-01"]  # stopped at the reserve, one request per UTC day
    assert list(frame["fixture_id"]) == [1, 2] and frame["kickoff_utc"].notna().all()
    with pytest.raises(SystemExit):
        script.fixtures_from_api(as_of, script.MAX_API_DAYS + 1)
    calls.clear()
    quota = iter([9000, 8900])
    # one day from 10:00 UTC reaches into the next UTC date (two requests); only the fixture before the end is kept
    assert script.fixtures_from_api(as_of, 1).shape[0] == 1 and calls == ["2026-09-30", "2026-10-01"]
    calls.clear()
    listing["empty"] = True
    quota = iter([9000, 8900])
    empty = script.fixtures_from_api(as_of, 1)  # nothing listed: an empty frame with the fixtures columns, not a crash
    assert empty.empty and list(empty.columns) == script.FIXTURE_COLUMNS
