from __future__ import annotations

import math
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest

from model_buk.scanner.engine import ScannerConfig, evaluate_legs_with_diagnostics, run_scan
from model_buk.scanner.register import ScannerRegister
from model_buk.scanner.settlement import single_profit

T0 = pd.Timestamp("2026-09-30 12:00", tz="UTC")
KICK = T0 + pd.Timedelta(hours=5)


def q(fixture: int, book: str = "Bet365", odd: float = 1.5, *, market: str = "1x2", selection: str = "home", line: float = np.nan, seen: pd.Timestamp = T0, kickoff: pd.Timestamp = KICK) -> dict:
    return {
        "fixture_id": fixture,
        "league_id": 39,
        "kickoff_utc": kickoff,
        "bookmaker": book,
        "bet_id": 1,
        "market": market,
        "period": "ft",
        "line": line,
        "selection": selection,
        "odd": odd,
        "fetched_at": seen - pd.Timedelta(minutes=5),
    }


def f(fixture: int, p: float = 0.7, *, market: str = "1x2", selection: str = "home", line: float = np.nan, se: float = 0.0) -> dict:
    return {
        "fixture_id": fixture,
        "market": market,
        "period": "ft",
        "line": line,
        "selection": selection,
        "p_fair": p,
        "p_se": se,
        "fair_source": "pinnacle",
        "devig_method": "shin",
        "n_books": 1,
        "book_margin": 0.03,
    }


def board(odds: dict[int, float] | None = None, *, seen: pd.Timestamp = T0, extra_quotes: list[dict] | None = None, extra_fair: list[dict] | None = None):
    """Three value legs (0.7 * 1.5 = 1.05) plus one leg without an edge."""
    odds = odds or {1: 1.5, 2: 1.5, 3: 1.5}
    quotes = [q(fixture, odd=odd, seen=seen) for fixture, odd in odds.items()]
    quotes.append(q(9, odd=1.2, seen=seen))  # 0.7 * 1.2 < 1: no raw edge, must not be logged
    fair = [f(fixture) for fixture in odds] + [f(9)]
    return pd.DataFrame(quotes + (extra_quotes or [])), pd.DataFrame(fair + (extra_fair or []))


def scan(register: ScannerRegister, as_of: pd.Timestamp = T0, *, odds: dict[int, float] | None = None, config: ScannerConfig | None = None, scan_id: str | None = None, **kw):
    quotes, fair = board(odds, seen=as_of)
    return run_scan(quotes, fair, None, pd.DataFrame(), config=config or ScannerConfig(), as_of=as_of, register=register, scan_id=scan_id, **kw)


@pytest.fixture
def reg(tmp_path) -> ScannerRegister:
    return ScannerRegister(tmp_path / "scanner.sqlite")


def count(reg: ScannerRegister, table: str) -> int:
    return int(reg.read(f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0])


# --------------------------------------------------------------------------- #
# Append-only and idempotent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", ["configs", "scans", "legs", "coupons", "coupon_legs", "settlements", "clv"])
def test_every_table_refuses_update_and_delete(reg: ScannerRegister, table: str) -> None:
    scan(reg)
    reg.append_settlements(pd.DataFrame({"leg_key": ["1|1x2|ft|na|home"], "outcome": ["win"]}), source="test")
    reg.record_clv(closing_fair({1: 0.72}), taus=ScannerConfig().taus, now=KICK + pd.Timedelta(minutes=1))
    with closing(sqlite3.connect(reg.path)) as con:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(f"DELETE FROM {table}")
        first_column = con.execute(f"SELECT name FROM pragma_table_info('{table}') LIMIT 1").fetchone()[0]
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(f"UPDATE {table} SET {first_column} = {first_column}")


def test_recording_the_same_scan_id_twice_is_a_no_op(reg: ScannerRegister) -> None:
    first = scan(reg)
    assert first.recorded is True
    before = {t: count(reg, t) for t in ("scans", "legs", "coupons", "coupon_legs", "configs")}
    second = scan(reg)  # same as_of -> same default scan_id
    assert second.recorded is False
    assert {t: count(reg, t) for t in before} == before


def test_a_failed_scan_leaves_nothing_behind(reg: ScannerRegister) -> None:
    quotes, fair = board()
    legs, diag = evaluate_legs_with_diagnostics(quotes, fair, None, ScannerConfig(), as_of=T0)
    broken = legs.drop(columns=["leg_class"])
    with pytest.raises(Exception):
        reg.record_scan(scan_id="bad", started_at=T0, as_of=T0, config=ScannerConfig(), legs=broken)
    assert count(reg, "scans") == 0 and count(reg, "legs") == 0 and count(reg, "configs") == 0
    assert not reg.has_scan("bad")


# --------------------------------------------------------------------------- #
# What is logged
# --------------------------------------------------------------------------- #


def test_legs_hold_the_near_miss_universe_and_the_value_legs(reg: ScannerRegister) -> None:
    extra_q = [q(4, "1xBet", 1.45), q(5, "1xBet", 1.45, market="ou", selection="over", line=2.5)]
    extra_f = [f(4), f(5, 0.7, market="ou", selection="over", line=2.5, se=0.05)]
    quotes, fair = board(extra_quotes=extra_q, extra_fair=extra_f)
    run_scan(quotes, fair, None, pd.DataFrame(), as_of=T0, register=reg)
    legs = reg.table("legs")
    # fixture 9 (raw edge below zero) is absent; fixture 5 is a near miss (positive edge, lower bound below break-even)
    assert set(legs["fixture_id"]) == {1, 2, 3, 4, 5}
    assert (legs["edge_raw"] > 0).all()
    assert dict(zip(legs["fixture_id"], legs["leg_class"]))[5] == "near_miss"
    assert {"ev_net_t088", "ev_net_lower_t088", "value_t088", "ev_net_t100", "p_fair", "fair_source", "p_model", "bookmaker", "odd", "fetched_at", "leg_class"} <= set(legs.columns)
    assert set(legs["config_id"]) == {ScannerConfig().config_id}


def test_near_misses_without_value_are_logged_too(reg: ScannerRegister) -> None:
    quotes = pd.DataFrame([q(1, odd=1.5)])
    fair = pd.DataFrame([f(1, 0.7, se=0.05)])  # 0.7 * 1.5 = 1.05 > 1 but the lower bound (0.618) is not
    run_scan(quotes, fair, None, pd.DataFrame(), as_of=T0, register=reg)
    legs = reg.table("legs")
    assert list(legs["leg_class"]) == ["near_miss"] and list(legs["is_value"]) == [0]
    assert reg.paper_legs().empty


def test_scan_row_carries_config_time_and_counts(reg: ScannerRegister) -> None:
    result = scan(reg)
    scans = reg.table("scans").iloc[0]
    assert scans["scan_id"] == result.scan_id == "20260930T120000Z"
    assert scans["as_of"] == "2026-09-30T12:00:00Z"
    assert scans["config_id"] == ScannerConfig().config_id
    assert '"z": 1.645' in scans["config_json"]
    assert scans["n_legs"] == 4 and scans["n_value"] == 3 and scans["n_legs_seen"] == 3 and scans["n_legs_logged"] == 3
    assert scans["n_picks"] == 3 and scans["n_fixtures"] == 4
    # one three-leg coupon passes under the full tax; the three two-leg coupons (1.05 ** 2 * 0.88 < 1) pass only
    # under a lower tax (1.05 ** 2 * 0.94 > 1) and are logged as the lower-tax group
    assert scans["n_coupons_passing"] == 1 and scans["n_coupons_logged"] == 4
    assert count(reg, "configs") == 1


def test_a_changed_config_is_a_new_config_id_row(reg: ScannerRegister) -> None:
    scan(reg, T0, config=ScannerConfig())
    scan(reg, T0 + pd.Timedelta(hours=1), config=ScannerConfig(z=1.0))
    configs = reg.table("configs")
    assert len(configs) == 2 and configs["config_id"].is_unique
    assert set(reg.table("scans")["config_id"]) == set(configs["config_id"])


def test_regime_columns_follow_the_config(reg: ScannerRegister) -> None:
    scan(reg, config=ScannerConfig(taus=(0.88, 0.90, 1.0)))
    legs = reg.table("legs")
    assert {"ev_net_t090", "ev_net_lower_t090", "value_t090"} <= set(legs.columns)
    assert "pass_t090" in reg.table("coupons").columns


# --------------------------------------------------------------------------- #
# Change-only logging
# --------------------------------------------------------------------------- #


def test_unchanged_legs_and_coupons_are_not_logged_again(reg: ScannerRegister) -> None:
    scan(reg, T0)
    scan(reg, T0 + pd.Timedelta(hours=1))
    assert count(reg, "scans") == 2 and count(reg, "legs") == 3 and count(reg, "coupons") == 4
    second = reg.table("scans").iloc[1]
    assert second["n_legs_seen"] == 3 and second["n_legs_logged"] == 0
    assert second["n_coupons_passing"] == 1 and second["n_coupons_logged"] == 0


def test_a_moved_price_is_logged_and_the_first_value_row_stays_the_paper_bet(reg: ScannerRegister) -> None:
    scan(reg, T0)
    scan(reg, T0 + pd.Timedelta(hours=1), odds={1: 1.6, 2: 1.5, 3: 1.5})
    legs = reg.table("legs")
    assert len(legs) == 4
    moved = legs[legs["fixture_id"] == 1].sort_values("as_of")
    assert list(moved["odd"]) == [1.5, 1.6]
    paper = reg.paper_legs().set_index("fixture_id")
    assert paper.loc[1, "odd"] == 1.5 and paper.loc[1, "scan_id"] == "20260930T120000Z"  # first price, not the best


def test_the_heartbeat_writes_an_unchanged_leg_once_the_last_row_is_old_enough(reg: ScannerRegister) -> None:
    config = ScannerConfig(log_heartbeat_hours=0.5)
    scan(reg, T0, config=config)
    scan(reg, T0 + pd.Timedelta(minutes=20), config=config)
    assert count(reg, "legs") == 3  # too soon
    scan(reg, T0 + pd.Timedelta(hours=1), config=config)
    assert count(reg, "legs") == 6  # all three legs again: the last row is older than the heartbeat


def test_a_leg_that_becomes_value_is_logged_as_a_change(reg: ScannerRegister) -> None:
    near = pd.DataFrame([q(1, odd=1.5)])
    run_scan(near, pd.DataFrame([f(1, 0.7, se=0.05)]), None, pd.DataFrame(), as_of=T0, register=reg)
    later = T0 + pd.Timedelta(hours=1)
    run_scan(pd.DataFrame([q(1, odd=1.5, seen=later)]), pd.DataFrame([f(1, 0.7, se=0.0)]), None, pd.DataFrame(), as_of=later, register=reg)
    legs = reg.table("legs").sort_values("as_of")
    assert list(legs["leg_class"]) == ["near_miss", "value"]
    assert len(reg.paper_legs()) == 1


# --------------------------------------------------------------------------- #
# Coupons in the register
# --------------------------------------------------------------------------- #


def test_coupons_and_their_legs_are_logged(reg: ScannerRegister) -> None:
    scan(reg)
    everything = reg.table("coupons")
    assert len(everything) == 4 and sorted(everything["regime"]) == ["t088", "t094", "t094", "t094"]
    assert set(everything["tier"]) == {"thin"}  # a bare board without Pinnacle or names: the data rules make it thin
    coupons = everything[everything["regime"] == "t088"]
    legs = reg.table("coupon_legs")
    legs = legs[legs["coupon_id"].isin(coupons["coupon_id"])]
    assert len(coupons) == 1 and len(legs) == 3 and set(legs["tier"]) == {"thin"}
    row = coupons.iloc[0]
    assert row["n_legs"] == 3 and row["is_top"] == 1
    assert row["odds_total"] == pytest.approx(1.5**3) and row["p_hit"] == pytest.approx(0.343)
    assert row["ev_net_t088"] == pytest.approx(0.88 * 1.05**3 - 1)
    assert row["ev_net_lower_t100"] == pytest.approx(1.05**3 - 1)
    assert set(legs["coupon_id"]) == {row["coupon_id"]}
    assert sorted(legs["leg_no"]) == [1, 2, 3]
    assert legs["bookmaker"].nunique() == 1 and legs["fixture_id"].is_unique
    assert len(reg.paper_coupons()) == 1


# --------------------------------------------------------------------------- #
# Settlement
# --------------------------------------------------------------------------- #

RESULTS = pd.DataFrame({"fixture_id": [1, 2, 3], "home_goals": [2, 0, 1], "away_goals": [0, 1, 0]})


def test_pending_legs_are_settled_once_and_only_when_a_result_exists(reg: ScannerRegister) -> None:
    scan(reg)
    assert reg.settle_pending(RESULTS.iloc[:1], now=KICK + pd.Timedelta(hours=3)) == 1
    outcomes = reg.latest_settlements().set_index("leg_key")["outcome"]
    assert outcomes["1|1x2|ft|na|home"] == "win"
    assert reg.settle_pending(RESULTS.iloc[:1], now=KICK + pd.Timedelta(hours=4)) == 0  # idempotent
    assert reg.settle_pending(RESULTS, now=KICK + pd.Timedelta(hours=5)) == 2
    outcomes = reg.latest_settlements().set_index("leg_key")["outcome"]
    assert outcomes["2|1x2|ft|na|home"] == "loss" and outcomes["3|1x2|ft|na|home"] == "win"
    assert "9|1x2|ft|na|home" not in outcomes.index  # fixture 9 has no result and is no value leg
    assert reg.unsettled_legs().empty


def test_a_statistic_that_is_missing_keeps_the_leg_unsettled(reg: ScannerRegister) -> None:
    quotes = pd.DataFrame([q(1, odd=1.6, market="corners_ou", selection="over", line=9.5)])
    fair = pd.DataFrame([f(1, 0.7, market="corners_ou", selection="over", line=9.5)])
    run_scan(quotes, fair, None, pd.DataFrame(), as_of=T0, register=reg)
    assert reg.settle_pending(RESULTS.iloc[:1]) == 0  # no corner counts in the result: never guessed
    with_corners = RESULTS.iloc[:1].assign(home_corners=6, away_corners=5)
    assert reg.settle_pending(with_corners) == 1
    assert reg.latest_settlements().iloc[0]["outcome"] == "win"


def test_a_correction_is_a_new_row_and_the_latest_wins(reg: ScannerRegister) -> None:
    scan(reg)
    key = "1|1x2|ft|na|home"
    row = {"leg_key": [key], "fixture_id": [1], "market": ["1x2"], "period": ["ft"], "line": [np.nan], "selection": ["home"]}
    reg.append_settlements(pd.DataFrame({**row, "outcome": ["loss"]}), source="first", settled_at=T0)
    reg.append_settlements(pd.DataFrame({**row, "outcome": ["win"]}), source="corrected", settled_at=T0 + pd.Timedelta(hours=1))
    assert count(reg, "settlements") == 2
    assert reg.latest_settlements().set_index("leg_key").loc[key, "outcome"] == "win"
    assert reg.append_settlements(pd.DataFrame({**row, "outcome": ["win"]}), source="corrected") == 0


# --------------------------------------------------------------------------- #
# CLV
# --------------------------------------------------------------------------- #


def closing_fair(p: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame([f(fixture, prob) | {"fair_source": "pinnacle"} for fixture, prob in p.items()])


def test_clv_is_computed_on_the_price_taken_after_kickoff_only(reg: ScannerRegister) -> None:
    scan(reg)
    taus = ScannerConfig().taus
    assert reg.record_clv(closing_fair({1: 0.72}), taus=taus, now=T0 + pd.Timedelta(hours=1)) == 0  # not started yet
    assert reg.record_clv(closing_fair({1: 0.72, 2: 0.60}), taus=taus, now=KICK + pd.Timedelta(minutes=1)) == 2
    clv = reg.latest_clv().set_index("leg_key")
    one, two = clv.loc["1|1x2|ft|na|home"], clv.loc["2|1x2|ft|na|home"]
    assert one["odd_taken"] == 1.5 and one["closing_p_fair"] == 0.72
    assert one["clv_raw"] == pytest.approx(1.5 * 0.72 - 1)
    assert one["clv_net_t088"] == pytest.approx(0.88 * 1.5 * 0.72 - 1)
    assert two["clv_raw"] == pytest.approx(1.5 * 0.60 - 1)
    assert "3|1x2|ft|na|home" not in clv.index  # no closing probability: missing stays missing, no row
    # the missing leg is retried later, the finished ones are not repeated
    assert reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.65}), taus=taus, now=KICK + pd.Timedelta(minutes=2)) == 1


def test_closing_price_at_the_bookmaker_is_stored_when_given(reg: ScannerRegister) -> None:
    scan(reg)
    closing_quotes = pd.DataFrame([q(1, "Bet365", 1.42, seen=KICK)])
    reg.record_clv(closing_fair({1: 0.72}), taus=ScannerConfig().taus, closing_quotes=closing_quotes, now=KICK + pd.Timedelta(minutes=1))
    assert reg.latest_clv().iloc[0]["closing_odd"] == 1.42


# --------------------------------------------------------------------------- #
# Paper results
# --------------------------------------------------------------------------- #


def test_paper_summary_before_anything_happened(reg: ScannerRegister) -> None:
    summary = reg.paper_summary(now=T0)
    assert len(summary) == 4  # two windows for singles, two for coupons
    assert (summary["n_bets"] == 0).all() and summary["hit_rate"].isna().all()


def test_paper_summary_singles_coupons_and_clv(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=4)
    reg.settle_pending(RESULTS, now=later)
    reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.70}), taus=ScannerConfig().taus, now=later)
    summary = reg.paper_summary(now=later).set_index(["kind", "window_days"])

    single = summary.loc[("single", 7)]
    assert single["n_bets"] == 3 and single["n_settled"] == 3
    assert single["hit_rate"] == pytest.approx(2 / 3)  # fixtures 1 and 3 won, fixture 2 lost
    for tau, label in ((0.88, "t088"), (0.94, "t094"), (1.0, "t100")):
        profits = [single_profit(o, 1.5, tau) for o in ("win", "loss", "win")]
        assert single[f"yield_{label}"] == pytest.approx(sum(profits) / 3)
    assert single["n_clv"] == 3
    assert single["median_clv_raw"] == pytest.approx(1.5 * 0.70 - 1)  # clv: +0.08, -0.10, +0.05 -> median +0.05
    assert single["median_clv_net_t088"] == pytest.approx(0.88 * 1.5 * 0.70 - 1)
    assert single["share_beaten"] == pytest.approx(2 / 3)
    assert summary.loc[("single", 30), "n_bets"] == 3

    coupon = summary.loc[("coupon", 7)]
    assert coupon["n_bets"] == 1 and coupon["n_settled"] == 1
    assert coupon["hit_rate"] == 0.0  # the second leg lost, so the coupon lost
    assert coupon["yield_t088"] == pytest.approx(-1.0)


def test_a_winning_coupon_pays_the_product_of_the_odds_after_tax(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=4)
    wins = pd.DataFrame({"fixture_id": [1, 2, 3], "home_goals": [1, 2, 3], "away_goals": [0, 0, 0]})
    reg.settle_pending(wins, now=later)
    reg.record_clv(closing_fair({1: 0.72, 2: 0.70, 3: 0.68}), taus=ScannerConfig().taus, now=later)
    coupon = reg.paper_summary(now=later).set_index(["kind", "window_days"]).loc[("coupon", 30)]
    assert coupon["hit_rate"] == 1.0
    assert coupon["yield_t088"] == pytest.approx(0.88 * 1.5**3 - 1)
    assert coupon["yield_t100"] == pytest.approx(1.5**3 - 1)
    assert coupon["median_clv_raw"] == pytest.approx(1.5**3 * 0.72 * 0.70 * 0.68 - 1)
    assert coupon["share_beaten"] == 1.0


def test_windows_only_count_recent_matches(reg: ScannerRegister) -> None:
    scan(reg)
    now = KICK + pd.Timedelta(days=10)
    reg.settle_pending(RESULTS, now=now)
    summary = reg.paper_summary(now=now).set_index(["kind", "window_days"])
    assert summary.loc[("single", 7), "n_bets"] == 0
    assert summary.loc[("single", 30), "n_bets"] == 3


def test_page_shows_paper_results_from_the_register(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=4)
    reg.settle_pending(RESULTS, now=later)
    reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.70}), taus=ScannerConfig().taus, now=later)
    quotes, fair = board(seen=later)
    result = run_scan(quotes, fair, None, pd.DataFrame(), as_of=later, register=reg, scan_id="later")
    assert "Jeszcze nic do podsumowania" not in result.html
    assert "ostatnie 7 dni" in result.html and "ostatnie 30 dni" in result.html
    assert "pojedyncze" in result.html and "kupony" in result.html
    assert math.isfinite(reg.paper_summary(now=later)["yield_t088"].dropna().iloc[0])


def test_run_scan_settles_and_records_clv_from_earlier_scans(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=4)
    quotes, fair = board(seen=later, odds={1: 1.5, 2: 1.5, 3: 1.5})
    quotes["kickoff_utc"] = later + pd.Timedelta(days=1)  # nothing left to bet on except tomorrow's board
    result = run_scan(
        quotes,
        fair,
        None,
        pd.DataFrame(),
        as_of=later,
        register=reg,
        results=RESULTS,
        closing=closing_fair({1: 0.72, 2: 0.60, 3: 0.70}),
    )
    assert result.settled == 3 and result.clv_rows == 3


# --------------------------------------------------------------------------- #
# Review additions: suspect legs, per-config paper results, older registers, summary details
# --------------------------------------------------------------------------- #


def test_suspect_legs_are_logged_for_research_but_are_no_paper_bets(reg: ScannerRegister) -> None:
    quotes = pd.DataFrame([q(1, odd=2.0), q(2, odd=1.5)])  # 0.7 * 2.0 = +40%: implausible, so suspect
    fair = pd.DataFrame([f(1), f(2)])
    run_scan(quotes, fair, None, pd.DataFrame(), as_of=T0, register=reg)
    legs = reg.table("legs").set_index("fixture_id")
    assert legs.loc[1, "leg_class"] == "suspect" and legs.loc[1, "is_value"] == 0
    assert legs.loc[2, "leg_class"] == "value"
    assert list(reg.paper_legs()["fixture_id"]) == [2]


def test_refund_share_and_book_shift_are_stored_with_the_leg(reg: ScannerRegister) -> None:
    ladder = [
        f(1, 0.80, market="ou", selection="over", line=1.5),
        f(1, 0.60, market="ou", selection="over", line=2.0),
        f(1, 0.55, market="ou", selection="over", line=2.5),
    ]
    quotes = pd.DataFrame([q(1, odd=1.8, market="ou", selection="over", line=2.0)])
    run_scan(quotes, pd.DataFrame(ladder), None, pd.DataFrame(), as_of=T0, register=reg)
    row = reg.table("legs").iloc[0]
    assert row["refund_share"] == pytest.approx(0.25) and pd.isna(row["book_shift"])


def test_paper_legs_and_results_are_taken_per_config(reg: ScannerRegister) -> None:
    config_a, config_b = ScannerConfig(), ScannerConfig(min_hit=0.25)  # same value legs, another coupon rule
    scan(reg, T0, config=config_a)
    scan(reg, T0 + pd.Timedelta(hours=1), config=config_b)
    legs = reg.table("legs")
    assert legs.groupby("config_id").size().to_dict() == {config_a.config_id: 3, config_b.config_id: 3}  # b logs its universe once
    assert len(reg.paper_legs()) == 3 and len(reg.paper_legs(config_a.config_id)) == 3
    assert set(reg.paper_legs(config_b.config_id)["scan_id"]) == {"20260930T130000Z"}  # b's bets start from what b saw first
    assert reg.paper_legs("no-such-config").empty and reg.paper_coupons("no-such-config").empty
    later = KICK + pd.Timedelta(hours=4)
    reg.settle_pending(RESULTS, now=later)
    for cfg in (config_a, config_b):
        summary = reg.paper_summary(now=later, config_id=cfg.config_id).set_index(["kind", "window_days"])
        assert summary.loc[("single", 7), "n_bets"] == 3 and summary.loc[("single", 7), "n_settled"] == 3
    empty = reg.paper_summary(now=later, config_id="no-such-config")
    assert (empty["n_bets"] == 0).all() and empty["yield_t088"].isna().all()
    # the page shows the results of its own config only
    result = run_scan(*board(seen=later), None, pd.DataFrame(), config=ScannerConfig(z=1.0), as_of=later, register=reg, scan_id="z1")
    assert "Jeszcze nic do podsumowania" in result.html  # the settled bets belong to two other configs


def test_a_register_made_before_columns_were_added_gets_them(tmp_path, monkeypatch) -> None:
    from model_buk.scanner import register as register_module

    path = tmp_path / "old.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(register_module, "_LEG_COLUMNS", {k: v for k, v in register_module._LEG_COLUMNS.items() if k not in ("refund_share", "book_shift")})
        patch.setattr(register_module, "_COUPON_LEG_COLUMNS", {k: v for k, v in register_module._COUPON_LEG_COLUMNS.items() if k != "refund_share"})
        ScannerRegister(path)
    with closing(sqlite3.connect(path)) as con:
        assert "refund_share" not in {row[1] for row in con.execute("PRAGMA table_info(legs)")}
    reg = ScannerRegister(path)
    scan(reg)
    assert {"refund_share", "book_shift"} <= set(reg.table("legs").columns)
    assert "refund_share" in reg.table("coupon_legs").columns
    assert reg.table("legs")["refund_share"].notna().all()


def test_only_settled_bets_enter_the_yield(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=4)
    reg.settle_pending(RESULTS.iloc[:1], now=later)  # only fixture 1 (a win) has a result
    single = reg.paper_summary(now=later).set_index(["kind", "window_days"]).loc[("single", 7)]
    assert single["n_bets"] == 3 and single["n_settled"] == 1
    assert single["yield_t088"] == pytest.approx(single_profit("win", 1.5, 0.88))  # the two open bets are not zeros
    assert single["hit_rate"] == 1.0


def test_a_closing_line_that_equals_the_price_taken_is_not_beaten(reg: ScannerRegister) -> None:
    scan(reg)
    later = KICK + pd.Timedelta(hours=1)
    reg.record_clv(closing_fair({1: 1 / 1.5, 2: 0.72, 3: 0.60}), taus=ScannerConfig().taus, now=later)
    clv = reg.latest_clv().set_index("leg_key")
    assert clv.loc["1|1x2|ft|na|home", "clv_raw"] == 0.0
    single = reg.paper_summary(now=later).set_index(["kind", "window_days"]).loc[("single", 7)]
    assert single["n_clv"] == 3 and single["share_beaten"] == pytest.approx(1 / 3)  # only fixture 2 (+8%) beat its close


def test_clv_is_not_retried_for_matches_older_than_the_retry_window(reg: ScannerRegister) -> None:
    scan(reg)
    taus = ScannerConfig().taus
    late = KICK + pd.Timedelta(days=8)
    assert reg.paper_legs_without_clv(late).empty  # 8 days after the kickoff: given up
    assert len(reg.paper_legs_without_clv(late, max_age_days=None)) == 3
    assert reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.70}), taus=taus, now=late) == 0
    assert len(reg.paper_legs_without_clv(KICK + pd.Timedelta(days=6))) == 3
    assert reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.70}), taus=taus, now=KICK + pd.Timedelta(days=6)) == 3


def test_a_closing_board_that_is_the_decision_board_is_no_closing_price(reg: ScannerRegister) -> None:
    scan(reg)  # the decision boards were seen at T0 - 5 minutes
    taus = ScannerConfig().taus
    same_board = pd.DataFrame([q(fixture, "Bet365", 1.5, seen=T0) for fixture in (1, 2)])  # last board before kickoff: the same one
    later_board = pd.DataFrame([q(3, "Bet365", 1.5, seen=T0 + pd.Timedelta(hours=2))])
    closing_quotes = pd.concat([same_board, later_board], ignore_index=True)
    now = KICK + pd.Timedelta(minutes=1)
    assert reg.record_clv(closing_fair({1: 0.72, 2: 0.60, 3: 0.70}), taus=taus, closing_quotes=closing_quotes, now=now) == 3
    clv = reg.latest_clv().set_index("leg_key")
    for key in ("1|1x2|ft|na|home", "2|1x2|ft|na|home"):
        assert pd.isna(clv.loc[key, "closing_p_fair"]) and pd.isna(clv.loc[key, "clv_raw"])
        assert clv.loc[key, "source"].endswith(":no_later_board")
    assert clv.loc["3|1x2|ft|na|home", "clv_raw"] == pytest.approx(1.5 * 0.70 - 1)
    assert not clv.loc["3|1x2|ft|na|home", "source"].endswith(":no_later_board")
    assert reg.paper_legs_without_clv(now).empty  # nothing is retried: the boards before kickoff cannot change
    single = reg.paper_summary(now=now).set_index(["kind", "window_days"]).loc[("single", 7)]
    assert single["n_clv"] == 1 and single["share_beaten"] == 1.0  # only the leg with a later board counts
