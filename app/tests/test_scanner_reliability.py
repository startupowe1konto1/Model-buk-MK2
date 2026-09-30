"""Scanner v1.0.1: market tiers, the suspect cap, timestamp consistency, deviating bet types, voids, the page
sections, replays and the config id."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from model_buk.scanner import bet_types as bt
from model_buk.scanner import markets
from model_buk.scanner.config import ScanConfig
from model_buk.scanner.coupons import build_coupons_by_tier
from model_buk.scanner.engine import ScannerConfig, evaluate_legs_with_diagnostics, run_scan, select_value_picks
from model_buk.scanner.fair import fair_prices
from model_buk.scanner.register import ScannerRegister
from model_buk.scanner.settlement import settle_coupon, single_profit, void_decision
from model_buk.scanner.tiers import fixture_tiers, name_reasons

AS_OF = pd.Timestamp("2026-09-30 12:00", tz="UTC")
KICK = AS_OF + pd.Timedelta(hours=5)
SEEN = AS_OF - pd.Timedelta(minutes=20)
BOOKS = ("Bet365", "1xBet", "Betano", "Marathonbet", "William Hill")


def quote(fixture=1, book="Bet365", market="1x2", selection="home", odd=2.2, *, line=np.nan, fetched=SEEN, api_update=None, bet_id=1, kickoff=KICK) -> dict:
    return {
        "fixture_id": fixture, "league_id": 39, "kickoff_utc": kickoff, "bookmaker": book, "bet_id": bet_id,
        "market": market, "period": "ft", "line": line, "selection": selection, "odd": odd, "fetched_at": fetched,
        "api_update": api_update,
    }  # fmt: skip


def fair_row(fixture=1, selection="home", p=0.5, se=0.0, *, board_at=None) -> dict:
    return {
        "fixture_id": fixture, "market": "1x2", "period": "ft", "line": np.nan, "selection": selection, "p_fair": p,
        "p_se": se, "fair_source": "pinnacle", "devig_method": "power", "n_books": 1, "book_margin": 0.03,
        "fair_board_at": board_at,
    }  # fmt: skip


def one_x_two(fixture, book, home, draw, away, **kw) -> list[dict]:
    return [quote(fixture, book, selection=s, odd=o, **kw) for s, o in (("home", home), ("draw", draw), ("away", away))]


def liquid_board(fixture=1, *, pin=(2.0, 3.6, 4.0), soft=(2.0, 3.4, 3.8), n_soft=5, **kw) -> list[dict]:
    """Pinnacle plus ``n_soft`` soft books quoting a full 1X2."""
    rows = one_x_two(fixture, "Pinnacle", *pin, **kw)
    for book in BOOKS[:n_soft]:
        rows += one_x_two(fixture, book, *soft, **kw)
    return rows


FIXTURES = pd.DataFrame(
    {
        "fixture_id": [1, 2, 3, 4, 5],
        "league_name": ["Premier League", "UEFA Europa Cup - Women", "U19 Bundesliga", "Friendlies Clubs", "Regionalliga - Bayern"],
        "country": ["England", "World", "Germany", "World", "Germany"],
        "home_team": ["Arsenal", "Malmö FF W", "Bayern U19", "Kalsdorf", "Bayern München II"],
        "away_team": ["Spurs", "St. Pölten W", "Dortmund U19", "Grazer AK", "Buchbach"],
    }
)


# --------------------------------------------------------------------------- #
# Market tiers
# --------------------------------------------------------------------------- #


def test_name_rules_mark_women_youth_friendlies_and_reserves_thin() -> None:
    assert name_reasons("Premier League", "England", "Arsenal", "Spurs") == []
    assert name_reasons("UEFA Nations League", "World", "Poland", "Portugal") == []
    assert name_reasons("FA WSL", "England", "Arsenal W", "Chelsea W") == ["women"]
    assert name_reasons("Liga MX Femenil", "Mexico") == ["women"]
    assert "youth" in name_reasons("UEFA U21 Championship - Qualification", "World")
    assert name_reasons("Primavera 1", "Italy") == ["youth"]
    assert name_reasons("Serie C - Girone A", "Italy", "Juventus U23", "Novara") == ["youth"]
    assert name_reasons("Friendlies Clubs", "World") == ["friendly"]
    assert name_reasons("Friendlies Women", "World") == ["women", "friendly"]
    assert name_reasons("First Amateur Division", "Belgium") == ["reserve"]
    assert name_reasons("Reserve League", "Argentina", "Aldosivi Res.", "Boca Res.") == ["reserve"]
    assert name_reasons("Segunda División RFEF - Group 2", "Spain", "Barcelona B", "Tarazona") == ["reserve"]
    assert name_reasons("Eerste Divisie", "Netherlands", "Ajax II", "Helmond") == ["reserve"]
    assert name_reasons(None, None, None, None) == []


def test_fixture_tiers_apply_the_data_rules() -> None:
    quotes = pd.DataFrame(
        liquid_board(1)  # six bookmakers, Pinnacle overround 1.0357: liquid
        + liquid_board(6, pin=(1.9, 3.2, 3.6))  # Pinnacle overround 1.12: thin
        + one_x_two(7, "Bet365", 2.0, 3.4, 3.8) + one_x_two(7, "1xBet", 2.0, 3.4, 3.8)  # no Pinnacle
        + liquid_board(8, n_soft=3)  # Pinnacle and three soft books: four bookmakers
    )
    tiers = fixture_tiers(quotes, None, ScannerConfig(), AS_OF).set_index("fixture_id")
    assert tiers.loc[1, "tier"] == "liquid" and tiers.loc[1, "tier_reason"] == "" and tiers.loc[1, "n_bookmakers"] == 6
    assert tiers.loc[1, "pinnacle_overround"] == pytest.approx(1 / 2.0 + 1 / 3.6 + 1 / 4.0)
    assert tiers.loc[6, "tier"] == "thin" and tiers.loc[6, "tier_reason"] == "pinnacle_margin"
    assert tiers.loc[7, "tier_reason"] == "no_pinnacle+few_bookmakers"
    assert tiers.loc[8, "tier_reason"] == "few_bookmakers"
    loose = fixture_tiers(quotes, None, ScannerConfig(tier_min_bookmakers=4, tier_max_pinnacle_overround=1.2), AS_OF)
    assert set(loose.loc[loose["tier"] == "liquid", "fixture_id"]) == {1, 6, 8}


def test_stale_boards_do_not_count_for_the_tier() -> None:
    old = SEEN - pd.Timedelta(hours=5)  # beyond the 3-hour limit of a match five hours away
    rows = one_x_two(1, "Pinnacle", 2.0, 3.6, 4.0, fetched=old)
    for book in BOOKS:
        rows += one_x_two(1, book, 2.0, 3.4, 3.8)
    tiers = fixture_tiers(pd.DataFrame(rows), None, ScannerConfig(), AS_OF)
    assert tiers.iloc[0]["tier_reason"] == "no_pinnacle+few_bookmakers"


def test_legs_carry_the_tier_of_their_fixture_and_the_names_decide_it() -> None:
    quotes = pd.DataFrame([row for fixture in (1, 2, 3, 4, 5) for row in liquid_board(fixture)])
    fair = fair_prices(quotes, as_of=AS_OF, max_age_minutes=None)
    legs, diag = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=AS_OF, fixtures=FIXTURES)
    tier = legs.drop_duplicates("fixture_id").set_index("fixture_id")
    assert tier.loc[1, "tier"] == "liquid"
    assert tier.loc[2, "tier_reason"] == "women" and tier.loc[3, "tier_reason"] == "youth"
    assert tier.loc[4, "tier_reason"] == "friendly" and tier.loc[5, "tier_reason"] == "reserve"
    assert diag.n_fixtures_liquid == 1 and diag.n_fixtures_thin == 4
    without_names = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=AS_OF)[0]
    assert set(without_names["tier"]) == {"liquid"}  # only the data rules without the fixture directory


def test_a_leg_without_a_pinnacle_fair_price_is_thin_even_in_a_liquid_fixture() -> None:
    quotes = pd.DataFrame(liquid_board(1) + [quote(1, "Bet365", "btts", "yes", 1.9)])
    fair = pd.DataFrame(
        [fair_row(1, s, p) for s, p in (("home", 0.49), ("draw", 0.27), ("away", 0.24))]
        + [{**fair_row(1, "yes", 0.55), "market": "btts", "fair_source": "consensus"}]
    )
    legs = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=AS_OF, fixtures=FIXTURES)[0]
    by_market = legs[legs["bookmaker"] == "Bet365"].set_index(["market", "selection"])
    assert by_market.loc[("1x2", "home"), "tier"] == "liquid"
    assert by_market.loc[("btts", "yes"), "tier"] == "thin" and by_market.loc[("btts", "yes"), "tier_reason"] == "soft_fair_price"
    relaxed = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(tier_sharp_fair_only=False), as_of=AS_OF, fixtures=FIXTURES)[0]
    assert set(relaxed["tier"]) == {"liquid"}


# --------------------------------------------------------------------------- #
# Suspect cap
# --------------------------------------------------------------------------- #


def test_the_default_suspect_cap_is_ten_percent() -> None:
    assert ScannerConfig().max_edge == pytest.approx(0.10)
    quotes = pd.DataFrame([quote(odd=2.24), quote(book="1xBet", odd=2.18), quote(book="Betano", odd=2.20)])
    legs, diag = evaluate_legs_with_diagnostics(quotes, pd.DataFrame([fair_row(p=0.5)]), None, ScannerConfig(), as_of=AS_OF)
    by_book = legs.set_index("bookmaker")
    assert by_book.loc["Bet365", "leg_class"] == "suspect" and not bool(by_book.loc["Bet365", "is_value"])  # +12%
    assert by_book.loc["1xBet", "leg_class"] == "value"  # +9%
    assert by_book.loc["Betano", "leg_class"] == "value"  # exactly +10% does not exceed the cap
    assert diag.n_suspect == 1


def test_a_suspect_leg_is_logged_but_never_a_coupon_leg(tmp_path) -> None:
    rows = []
    fair = []
    for fixture, odd in ((1, 2.5), (2, 2.18), (3, 2.18)):
        rows.append(quote(fixture, odd=odd))
        fair.append(fair_row(fixture, p=0.5))
    register = ScannerRegister(tmp_path / "r.sqlite")
    result = run_scan(pd.DataFrame(rows), pd.DataFrame(fair), None, pd.DataFrame(), as_of=AS_OF, register=register)
    logged = register.table("legs").set_index("fixture_id")
    assert logged.loc[1, "leg_class"] == "suspect" and logged.loc[1, "suspect"] == 1
    assert 1 not in set(result.coupons.legs["fixture_id"])


# --------------------------------------------------------------------------- #
# Timestamp consistency
# --------------------------------------------------------------------------- #


def test_a_quote_from_another_provider_snapshot_than_its_fair_price_is_unsynced() -> None:
    now_board = SEEN - pd.Timedelta(hours=3)
    old_board = now_board - pd.Timedelta(hours=4)
    quotes = pd.DataFrame(
        [
            quote(1, odd=2.18, api_update=now_board),  # fair board 4 hours older: unsynced
            quote(2, odd=2.18, api_update=now_board),  # same snapshot: fine
            quote(3, odd=2.18, api_update=now_board),  # fair board unknown: not judged
        ]
    )
    fair = pd.DataFrame([fair_row(1, board_at=old_board), fair_row(2, board_at=now_board + pd.Timedelta(minutes=30)), fair_row(3)])
    legs, diag = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=AS_OF)
    by_fixture = legs.set_index("fixture_id")
    assert bool(by_fixture.loc[1, "unsynced"]) and by_fixture.loc[1, "leg_class"] == "unsynced"
    assert not bool(by_fixture.loc[1, "is_value"]) and by_fixture.loc[1, "board_lag_min"] == pytest.approx(240.0)
    assert by_fixture.loc[2, "leg_class"] == "value" and by_fixture.loc[2, "board_lag_min"] == pytest.approx(-30.0)
    assert by_fixture.loc[3, "leg_class"] == "value" and np.isnan(by_fixture.loc[3, "board_lag_min"])
    assert diag.n_unsynced == 1
    wide = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(sync_window_minutes=300), as_of=AS_OF)[0]
    assert not wide["unsynced"].any()
    assert "unsynced" in select_value_picks(legs, ScannerConfig()).columns and 1 not in set(select_value_picks(legs, ScannerConfig())["fixture_id"])


def test_fair_prices_carry_the_board_time_of_their_source() -> None:
    pin_update = SEEN - pd.Timedelta(hours=3)
    soft_updates = {"Bet365": SEEN - pd.Timedelta(hours=2), "1xBet": SEEN - pd.Timedelta(hours=6), "Betano": SEEN - pd.Timedelta(hours=1)}
    rows = one_x_two(1, "Pinnacle", 2.0, 3.6, 4.0, api_update=pin_update)
    for book, update in soft_updates.items():
        rows += [quote(1, book, "btts", s, o, api_update=update) for s, o in (("yes", 1.8), ("no", 2.0))]
    fair = fair_prices(pd.DataFrame(rows), as_of=AS_OF, max_age_minutes=None, derive=False).set_index(["market", "selection"])
    assert fair.loc[("1x2", "home"), "fair_board_at"] == pin_update
    assert fair.loc[("btts", "yes"), "fair_source"] == "consensus"
    assert fair.loc[("btts", "yes"), "fair_board_at"] == min(soft_updates.values())  # the oldest board of the consensus
    no_times = fair_prices(pd.DataFrame(rows).drop(columns=["api_update", "fetched_at"]), max_age_minutes=None, derive=False)
    assert no_times["fair_board_at"].isna().all()


# --------------------------------------------------------------------------- #
# Deviating bet types
# --------------------------------------------------------------------------- #


def _deviation_board(n_fixtures: int = 30) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(3)
    for fixture in range(1, n_fixtures + 1):
        line = 2.5
        p = 0.45 + 0.1 * rng.random()
        rows += [quote(fixture, "Pinnacle", "ou", "over", 0.97 / p, line=line, bet_id=5), quote(fixture, "Pinnacle", "ou", "under", 0.97 / (1 - p), line=line, bet_id=5)]
        # bet 5 follows Pinnacle, bet 50 sits five points lower on the over side
        rows += [quote(fixture, "Bet365", "ou", "over", 0.95 / p, line=line, bet_id=5), quote(fixture, "Bet365", "ou", "under", 0.95 / (1 - p), line=line, bet_id=5)]
        q = p - 0.05
        rows += [quote(fixture, "Bet365", "ou", "over", 0.95 / q, line=line, bet_id=50), quote(fixture, "Bet365", "ou", "under", 0.95 / (1 - q), line=line, bet_id=50)]
    return pd.DataFrame(rows)


def test_board_deviations_measure_each_bet_type_separately() -> None:
    deviations = bt.board_deviations(_deviation_board())
    stats = bt.deviation_stats(deviations).set_index("bet_id")
    assert set(stats.index) == {5, 50} and set(stats["bookmaker"]) == {"Bet365"}  # Pinnacle is the reference
    assert abs(stats.loc[5, "median_dev"]) < 0.005
    assert stats.loc[50, "median_dev"] == pytest.approx(-0.05, abs=0.005)
    assert stats.loc[50, "n_legs"] == 30 and stats.loc[50, "n_fixtures"] == 30


def test_flag_bet_types_excludes_outliers_only_with_enough_legs() -> None:
    stats = pd.DataFrame(
        [
            {"bookmaker": "Bet365", "bet_id": 5, "bet": "Goals Over/Under", "market": "ou", "n_legs": 500, "n_fixtures": 100, "median_dev": 0.002, "mad_dev": 0.02, "p90_abs_dev": 0.04, "tail_share": 0.04},
            {"bookmaker": "Bet365", "bet_id": 27, "bet": "Clean Sheet - Home", "market": "team_ou_away", "n_legs": 300, "n_fixtures": 100, "median_dev": -0.025, "mad_dev": 0.02, "p90_abs_dev": 0.05, "tail_share": 0.08},
            {"bookmaker": "BetVictor", "bet_id": 6, "bet": "Goals Over/Under First Half", "market": "ou", "n_legs": 900, "n_fixtures": 100, "median_dev": 0.0, "mad_dev": 0.045, "p90_abs_dev": 0.05, "tail_share": 0.11},
            {"bookmaker": "SBO", "bet_id": 1, "bet": "Match Winner", "market": "1x2", "n_legs": 400, "n_fixtures": 100, "median_dev": -0.016, "mad_dev": 0.037, "p90_abs_dev": 0.07, "tail_share": 0.18},
            {"bookmaker": "Betano", "bet_id": 77, "bet": "Corners 1H", "market": "corners_ou", "n_legs": 40, "n_fixtures": 30, "median_dev": 0.05, "mad_dev": 0.05, "p90_abs_dev": 0.08, "tail_share": 0.5},
            {"bookmaker": "Pinnacle", "bet_id": 1, "bet": "Match Winner", "market": "1x2", "n_legs": 400, "n_fixtures": 100, "median_dev": 0.05, "mad_dev": 0.05, "p90_abs_dev": 0.08, "tail_share": 0.5},
        ]
    )  # fmt: skip
    flagged = bt.flag_bet_types(stats, **ScanConfig().bet_type_rule()).set_index(["bookmaker", "bet_id"])
    assert flagged["excluded"].to_dict() == {
        ("Bet365", 5): False, ("Bet365", 27): True, ("BetVictor", 6): True, ("SBO", 1): True, ("Betano", 77): False, ("Pinnacle", 1): False,
    }  # fmt: skip
    assert flagged.loc[("Bet365", 27), "reason"] == "median" and flagged.loc[("BetVictor", 6), "reason"] == "dispersion"
    assert flagged.loc[("SBO", 1), "reason"] == "tail" and flagged.loc[("Betano", 77), "reason"] == "too_few_legs"
    pairs = bt.excluded_pairs(flagged.reset_index())
    assert pairs == {("bet365", 27), ("betvictor", 6), ("sbo", 1)}
    assert bt.pair_labels(pairs, flagged.reset_index())[0] == "Bet365 27 (Clean Sheet - Home)"


def test_excluded_bet_types_are_dropped_before_the_legs_are_merged() -> None:
    raw = pd.DataFrame(
        [
            {"bookmaker": "Bet365", "bet_id": 50, "selection": "Over 2.5", "odd": 2.1},
            {"bookmaker": "Bet365", "bet_id": 5, "selection": "Over 2.5", "odd": 1.9},
            {"bookmaker": "bet 365", "bet_id": 50, "selection": "Under 2.5", "odd": 1.8},
            {"bookmaker": "1xBet", "bet_id": 50, "selection": "Over 2.5", "odd": 2.0},
        ]
    )
    kept, dropped = markets._drop_bet_types(raw, [("Bet365", 50)])
    assert dropped == 2 and list(zip(kept["bookmaker"], kept["bet_id"])) == [("Bet365", 5), ("1xBet", 50)]
    assert markets._drop_bet_types(raw, None)[1] == 0


def test_bet_type_checks_are_stored_and_refreshed_at_most_daily(tmp_path, monkeypatch) -> None:
    from model_buk.scanner import pipeline
    from model_buk.scanner.config import RunSettings

    calls = []
    stats = pd.DataFrame(
        [{"bookmaker": "Bet365", "bet_id": 27, "bet": "Clean Sheet - Home", "market": "team_ou_away", "n_legs": 300, "n_fixtures": 90, "median_dev": -0.03, "mad_dev": 0.02, "p90_abs_dev": 0.05, "tail_share": 0.08}]
    )  # fmt: skip

    def fake_measure(odds_db, as_of, **kw):
        calls.append(pd.Timestamp(as_of))
        return stats, {"snapshots": 3, "legs": 300, "fixtures": 90}

    monkeypatch.setattr(bt, "measure_bet_types", fake_measure)
    register = ScannerRegister(tmp_path / "r.sqlite")
    settings = RunSettings(data_root=tmp_path)
    config = ScanConfig()
    _, excluded, info = pipeline.bet_type_exclusions(register, settings, config, AS_OF)
    assert info["status"] == "measured" and excluded == {("bet365", 27)} and info["excluded"] == ["Bet365 27 (Clean Sheet - Home)"]
    _, again, info = pipeline.bet_type_exclusions(register, settings, config, AS_OF + pd.Timedelta(hours=23))
    assert info["status"] == "reused" and again == excluded and len(calls) == 1
    # the stored statistics are judged with the thresholds of the current config
    _, loose, _ = pipeline.bet_type_exclusions(register, settings, ScanConfig(bet_type_max_abs_median=0.05), AS_OF + pd.Timedelta(hours=1))
    assert loose == frozenset()
    pipeline.bet_type_exclusions(register, settings, config, AS_OF + pd.Timedelta(hours=25))
    assert len(calls) == 2
    # a replay never uses a measurement taken after its decision time
    _, _, info = pipeline.bet_type_exclusions(register, settings, config, AS_OF - pd.Timedelta(hours=1))
    assert info["status"] == "measured" and len(calls) == 3
    stored = register.table("bet_type_checks")
    assert len(stored) == 3 and set(stored["excluded"]) == {1}
    with pytest.raises(sqlite3.DatabaseError):  # append-only like every table of the register
        with closing(sqlite3.connect(register.path)) as con:
            con.execute("DELETE FROM bet_type_checks")
    _, none, info = pipeline.bet_type_exclusions(register, settings, ScanConfig(bet_type_checks=False), AS_OF)
    assert info["status"] == "disabled" and none == frozenset()


# --------------------------------------------------------------------------- #
# Voids
# --------------------------------------------------------------------------- #


def test_void_decision_follows_the_status_and_the_grace_time() -> None:
    kick = pd.Timestamp("2026-10-01 18:00", tz="UTC")
    assert void_decision("CANC", kick, kick, kick - pd.Timedelta(hours=5)) == "void:CANC"  # final: at once
    assert void_decision("ABD", kick, kick, kick + pd.Timedelta(hours=1)) == "void:ABD"
    assert void_decision("AWD", kick, kick, kick) == "void:AWD" and void_decision("WO", kick, kick, kick) == "void:WO"
    assert void_decision("PST", kick, kick, kick + pd.Timedelta(hours=47)) is None  # still inside the grace time
    assert void_decision("PST", kick, kick, kick + pd.Timedelta(hours=48)) == "void:PST"
    assert void_decision("SUSP", kick, kick, kick + pd.Timedelta(hours=49)) == "void:SUSP"
    assert void_decision("INT", kick, kick, kick + pd.Timedelta(hours=49)) == "void:INT"
    moved = kick + pd.Timedelta(days=5)
    assert void_decision("NS", kick, moved, kick + pd.Timedelta(hours=49)) == "void:rescheduled"
    assert void_decision("NS", kick, kick + pd.Timedelta(hours=20), kick + pd.Timedelta(hours=49)) is None  # played within the window
    assert void_decision("FT", kick, kick, kick + pd.Timedelta(days=3)) is None
    assert void_decision(None, kick, kick, kick + pd.Timedelta(days=3)) is None


def test_a_void_leg_returns_the_stake_and_drops_out_of_a_coupon() -> None:
    assert single_profit("void", 2.0, 0.88) == 0.0
    coupon = settle_coupon([("win", 2.0), ("void", 1.8)])
    assert coupon.outcome == "win" and coupon.factor == pytest.approx(2.0) and coupon.profit(0.88) == pytest.approx(0.88 * 2.0 - 1)
    assert settle_coupon([("void", 2.0), ("void", 1.8)]).outcome == "void" and settle_coupon([("void", 2.0), ("void", 1.8)]).profit(0.88) == 0.0
    assert settle_coupon([("void", 2.0), ("loss", 1.8)]).outcome == "loss"
    assert settle_coupon([("void", 2.0), (None, 1.8)]) is None


def test_the_register_voids_the_legs_of_matches_that_were_not_played(tmp_path) -> None:
    register = ScannerRegister(tmp_path / "r.sqlite")
    quotes = pd.DataFrame([quote(f, odd=2.18) for f in (1, 2, 3)])
    fair = pd.DataFrame([fair_row(f) for f in (1, 2, 3)])
    run_scan(quotes, fair, None, pd.DataFrame(), as_of=AS_OF, register=register)
    statuses = pd.DataFrame({"fixture_id": [1, 2, 3], "status_short": ["CANC", "PST", "NS"], "kickoff_utc": [KICK, KICK, KICK]})
    assert register.settle_voids(statuses, now=KICK + pd.Timedelta(hours=1)) == 1  # only the cancelled one so far
    assert register.settle_voids(statuses, now=KICK + pd.Timedelta(hours=49)) == 1  # the postponed one after 48 h
    settled = register.latest_settlements().set_index("fixture_id")
    assert settled.loc[1, "outcome"] == "void" and settled.loc[1, "source"] == "void:CANC"
    assert settled.loc[2, "source"] == "void:PST" and 3 not in settled.index


# --------------------------------------------------------------------------- #
# Coupons per tier and tax group
# --------------------------------------------------------------------------- #


def _picks(tier_of: dict[int, str], odd: float = 1.5, p: float = 0.7) -> pd.DataFrame:
    quotes = pd.DataFrame([quote(f, odd=odd) for f in tier_of])
    fair = pd.DataFrame([fair_row(f, p=p) for f in tier_of])
    legs = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=AS_OF)[0]
    legs["tier"] = legs["fixture_id"].map(tier_of)
    return select_value_picks(legs, ScannerConfig())


def test_coupons_never_mix_tiers_and_carry_their_tax_regime() -> None:
    picks = _picks({1: "liquid", 2: "liquid", 3: "liquid", 4: "thin", 5: "thin", 6: "thin"})
    coupons = build_coupons_by_tier(picks, ScannerConfig())
    frame = coupons.coupons
    legs = coupons.legs.merge(frame[["coupon_id", "tier"]], on="coupon_id", suffixes=("_leg", ""))
    assert (legs["tier_leg"] == legs["tier"]).all()  # every leg of a coupon has the coupon's tier
    assert set(frame["tier"]) == {"liquid", "thin"}
    # three legs at 5% pass the 12% tax (1.05 ** 3 * 0.88 > 1); two only under a 6% tax (1.05 ** 2 * 0.94 > 1)
    assert set(frame.loc[frame["n_legs"] == 3, "regime"]) == {"t088"} and set(frame.loc[frame["n_legs"] == 2, "regime"]) == {"t094"}
    assert len(coupons.proposed("liquid")) == 1 and len(coupons.proposed("liquid", lower_tax=True)) == 3
    assert coupons.n_passing == 2 and coupons.n_passing_lower == 6
    assert coupons.by_tier["liquid"] == {"passing": 1, "passing_lower": 3, "top": 1, "top_lower": 3}


# --------------------------------------------------------------------------- #
# Paper results, the recent table and the page
# --------------------------------------------------------------------------- #

RESULTS = pd.DataFrame({"fixture_id": [1, 2, 3], "home_goals": [2, 0, 1], "away_goals": [0, 1, 0]})


def test_paper_results_are_split_by_tier_and_regime_and_the_page_shows_wins_and_losses(tmp_path) -> None:
    register = ScannerRegister(tmp_path / "r.sqlite")
    rows, fair = [], []
    for fixture in (1, 2, 3):
        rows.append(quote(fixture, odd=1.5))
        fair.append(fair_row(fixture, p=0.7))
    first = run_scan(pd.DataFrame(rows), pd.DataFrame(fair), None, FIXTURES, as_of=AS_OF, register=register)
    config = first.config
    later = KICK + pd.Timedelta(hours=4)
    statuses = pd.DataFrame({"fixture_id": [3], "status_short": ["CANC"], "kickoff_utc": [KICK]})
    register.settle_pending(RESULTS[RESULTS["fixture_id"] != 3], now=later)
    register.settle_voids(statuses, now=later)
    results = register.paper_results(now=later, config_id=config.config_id)
    assert list(results.columns[:4]) == ["tier", "regime", "tau", "kind"] and set(results["tier"]) == {"liquid", "thin"}
    # the bare board (no Pinnacle, one bookmaker) is thin: every bet is in the thin rows
    thin_full = results[(results["tier"] == "thin") & (results["regime"] == "t088") & (results["window_days"] == 7)].set_index("kind")
    assert thin_full.loc["coupon", "n_bets"] == 1 and thin_full.loc["single", "n_bets"] == 0  # no single pays 12% tax at +5%
    thin_free = results[(results["tier"] == "thin") & (results["regime"] == "t100") & (results["window_days"] == 7)].set_index("kind")
    assert thin_free.loc["single", "n_bets"] == 3 and thin_free.loc["single", "n_settled"] == 3
    assert thin_free.loc["single", "hit_rate"] == pytest.approx(0.5)  # a win and a loss; the void is left out
    assert thin_free.loc["single", "yield"] == pytest.approx((0.5 + -1.0 + 0.0) / 3)
    assert (results.loc[results["tier"] == "liquid", "n_bets"] == 0).all()

    recent = register.recent_bets(now=later, config_id=config.config_id)
    singles = recent["singles"].set_index("fixture_id")
    assert singles.loc[1, "outcome"] == "win" and singles.loc[2, "outcome"] == "loss" and singles.loc[3, "outcome"] == "void"
    assert len(recent["coupons"]) == 4 and set(recent["coupons"]["outcome"]) == {"loss", "win"}

    page = run_scan(pd.DataFrame(rows).iloc[0:0], pd.DataFrame(fair), None, FIXTURES, as_of=later, register=register, config=config).html
    assert "Ostatnie 7 dni: propozycje i ich wynik" in page
    for word in ("wygrany", "przegrany", "unieważniony"):
        assert word in page
    assert "Rynki płynne" in page and "Rynki słabe (eksperyment)" in page


def test_page_status_box_lists_the_skipped_quotes_and_excluded_bet_types() -> None:
    from model_buk.scanner import report_html

    result = run_scan(pd.DataFrame([quote(odd=2.5)]), pd.DataFrame([fair_row()]), None, FIXTURES, as_of=AS_OF)
    status = {
        "counts": {"fixtures": 1, "quotes": 1},
        "model": {"status": "fit_stale"},
        "api_requests": 1,
        "api": {"quota_remaining": 2200},
        "bet_types": {"excluded": ["Bet365 27 (Clean Sheet - Home)"], "quote_rows_dropped": 12},
        "warnings": ["models skipped: the newest fit is 30 h old"],
    }
    page = report_html.render_page(result, FIXTURES, None, status=status)
    assert "Co pominięto i dlaczego" in page and "Bet365 27 (Clean Sheet - Home)" in page
    assert "podejrzanych kursów" in page and "trening za stary" in page and f"zostało {report_html.fmt_int(2200)}" in page
    assert page.index("Skaner wartości") < page.index("Single opłacalne") < page.index("Kupony 2–3 nogi")


# --------------------------------------------------------------------------- #
# Config id, replays, imports
# --------------------------------------------------------------------------- #


def test_every_new_threshold_is_part_of_the_config_id() -> None:
    base = ScanConfig()
    changes = {
        "max_edge": 0.12, "sync_window_minutes": 90.0, "tier_max_pinnacle_overround": 1.09, "tier_min_bookmakers": 5,
        "tier_name_rules": False, "tier_sharp_fair_only": False, "bet_type_checks": False, "bet_type_max_abs_median": 0.03, "bet_type_max_dispersion_ratio": 1.6,
        "bet_type_max_tail_share": 0.2, "bet_type_min_legs": 50, "bet_type_lookback_days": 3.0, "bet_type_step_hours": 6.0,
        "bet_type_refresh_hours": 12.0, "void_grace_hours": 24.0,
    }  # fmt: skip
    names = {f.name for f in fields(ScanConfig)}
    assert set(changes) <= names
    for name, value in changes.items():
        assert ScanConfig(**{name: value}).config_id != base.config_id, name
    for bad in ({"tier_max_pinnacle_overround": 0.9}, {"tier_min_bookmakers": 0}, {"bet_type_max_dispersion_ratio": 0.0},
                {"bet_type_step_hours": 0.0}, {"sync_window_minutes": -1.0}, {"bet_type_min_legs": 0}):  # fmt: skip
        with pytest.raises(ValueError):
            ScanConfig(**bad)


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_scanner.py"
    spec = importlib.util.spec_from_file_location("run_scanner_script_v101", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_replay_never_writes_the_live_register_without_the_explicit_flag(tmp_path, capsys) -> None:
    script = _script()
    parse = script.build_parser().parse_args
    replay = script.settings_from(parse(["--as-of", "2026-10-03T12:00:00Z", "--data-root", str(tmp_path)]))
    assert replay.out_dir == tmp_path / "scanner" / "replay" and replay.register_path == tmp_path / "scanner" / "replay" / "scanner.sqlite"
    assert replay.api_enabled is False
    live = script.settings_from(parse(["--as-of", "2026-10-03T12:00:00Z", "--live-register", "--data-root", str(tmp_path)]))
    assert live.register_path == tmp_path / "scanner" / "scanner.sqlite" and live.api_enabled is False
    offline = script.settings_from(parse(["--as-of", "2026-10-03T12:00:00Z", "--offline", "--data-root", str(tmp_path)]))
    assert offline.out_dir == tmp_path / "scanner" / "offline"
    for bad in (["--live-register"], ["--as-of", "2026-10-03T12:00:00Z", "--live-register", "--offline"]):
        with pytest.raises(SystemExit):
            script.main([*bad, "--data-root", str(tmp_path)])
    capsys.readouterr()


def test_importing_the_scanner_does_not_load_scikit_learn() -> None:
    code = (
        "import sys\n"
        "import model_buk.scanner.pipeline, model_buk.scanner.live_models, model_buk.scanner.bet_types, model_buk.scanner.tiers\n"
        "print(any(name == 'sklearn' or name.startswith('sklearn.') for name in sys.modules))\n"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, env={**os.environ, "PYTHONPATH": src})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "False"
    source = (Path(src) / "model_buk" / "scanner" / "live_models.py").read_text(encoding="utf-8")
    assert "SKLEARN_STANDIN" not in source and "_ensure_optional_sklearn" not in source
