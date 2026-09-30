from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from model_buk.markets.pricing import (
    expected_value_net,
    expected_value_net_lower_bound,
    normalise_bookmaker_name,
)
from model_buk.markets.staking import effective_tax_multiplier
from model_buk.scanner import report_html
from model_buk.scanner.coupons import CouponSet, build_coupons, coupon_id
from model_buk.scanner.engine import (
    ScannerConfig,
    effective_tau,
    evaluate_legs,
    evaluate_legs_with_diagnostics,
    market_family,
    run_scan,
    select_model_only,
    select_value_picks,
)

AS_OF = pd.Timestamp("2026-09-30 12:00", tz="UTC")
KICK = AS_OF + pd.Timedelta(hours=5)
SEEN = AS_OF - pd.Timedelta(minutes=20)


def quote(
    fixture: int = 1,
    book: str = "Bet365",
    market: str = "1x2",
    selection: str = "home",
    odd: float = 2.2,
    *,
    period: str = "ft",
    line: float = np.nan,
    fetched: pd.Timestamp = SEEN,
    kickoff: pd.Timestamp = KICK,
) -> dict:
    return {
        "fixture_id": fixture,
        "league_id": 39,
        "kickoff_utc": kickoff,
        "bookmaker": book,
        "bet_id": 1,
        "market": market,
        "period": period,
        "line": line,
        "selection": selection,
        "odd": odd,
        "fetched_at": fetched,
    }


def fair_row(
    fixture: int = 1,
    market: str = "1x2",
    selection: str = "home",
    p: float = 0.5,
    se: float = 0.02,
    *,
    period: str = "ft",
    line: float = np.nan,
    source: str = "pinnacle",
) -> dict:
    return {
        "fixture_id": fixture,
        "market": market,
        "period": period,
        "line": line,
        "selection": selection,
        "p_fair": p,
        "p_se": se,
        "fair_source": source,
        "devig_method": "shin",
        "n_books": 1,
        "book_margin": 0.03,
    }


def model_row(fixture: int = 1, market: str = "1x2", selection: str = "home", p: float = 0.5, *, period: str = "ft", line: float = np.nan) -> dict:
    return {
        "fixture_id": fixture,
        "market": market,
        "period": period,
        "line": line,
        "selection": selection,
        "p_model": p,
        "model_name": "pool",
    }


def cfg(**changes) -> ScannerConfig:
    """A config for the arithmetic tests: their edges go up to 20%, above the 10% suspect cap of the default.

    The cap itself (and every other default) is tested explicitly with ``ScannerConfig()``.
    """
    changes.setdefault("max_edge", 0.25)
    return ScannerConfig(**changes)


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def legs_for(quotes: list[dict], fair: list[dict], model: list[dict] | None = None, config: ScannerConfig | None = None, **kw) -> pd.DataFrame:
    config = config or cfg()
    return evaluate_legs(frame(quotes), frame(fair), None if model is None else frame(model), config, as_of=kw.get("as_of", AS_OF))


# --------------------------------------------------------------------------- #
# EV arithmetic
# --------------------------------------------------------------------------- #


def test_ev_and_lower_bound_per_tax_regime() -> None:
    legs = legs_for([quote(odd=2.2)], [fair_row(p=0.5, se=0.02)])
    row = legs.iloc[0]
    p_lower = 0.5 - 1.645 * 0.02

    assert row["edge_raw"] == pytest.approx(0.5 * 2.2 - 1)
    for tau, label in ((0.88, "t088"), (0.94, "t094"), (1.0, "t100")):
        assert row[f"ev_net_{label}"] == pytest.approx(tau * 0.5 * 2.2 - 1)
        assert row[f"ev_net_{label}"] == pytest.approx(expected_value_net(0.5, 2.2, tau))
        assert row[f"ev_net_lower_{label}"] == pytest.approx(tau * p_lower * 2.2 - 1)
        assert row[f"ev_net_lower_{label}"] == pytest.approx(expected_value_net_lower_bound(p_lower, 2.2, tau))
    # 0.4671 * 2.2 = 1.0276: beats the fair price with confidence only where there is no stake tax
    assert bool(row["value_t100"]) and not bool(row["value_t094"]) and not bool(row["value_t088"])
    assert row["p_lower"] == pytest.approx(p_lower)
    assert row["fair_odd"] == pytest.approx(2.0)


def test_value_needs_a_strictly_positive_lower_bound() -> None:
    # p_lower * odd == 1 exactly: not an opportunity
    legs = legs_for([quote(odd=2.0)], [fair_row(p=0.5, se=0.0)])
    assert not bool(legs.iloc[0]["value_t100"])
    legs = legs_for([quote(odd=2.0)], [fair_row(p=0.5, se=0.0)], config=cfg(min_ev=0.0))
    assert legs.iloc[0]["ev_net_lower_t100"] == pytest.approx(0.0)
    legs = legs_for([quote(odd=2.01)], [fair_row(p=0.5, se=0.0)])
    assert bool(legs.iloc[0]["value_t100"])


def test_unknown_standard_error_never_gives_value() -> None:
    legs = legs_for([quote(odd=2.2)], [fair_row(p=0.5, se=np.nan)])
    row = legs.iloc[0]
    assert math.isnan(row["p_lower"]) and math.isnan(row["ev_net_lower_t100"])
    assert not bool(row["is_value"])
    assert row["edge_raw"] > 0  # the near-miss universe still sees it
    assert row["leg_class"] == "near_miss"


def test_nan_lines_join_and_ladder_lines_do_not_mix() -> None:
    quotes = [
        quote(market="1x2", selection="home", odd=2.3),
        quote(market="ou", selection="over", odd=2.05, line=2.5),
        quote(market="ou", selection="over", odd=3.4, line=3.5),
    ]
    fair = [
        fair_row(market="1x2", selection="home", p=0.5),
        fair_row(market="ou", selection="over", p=0.5, line=2.5),
        fair_row(market="ou", selection="over", p=0.30, line=3.5),
    ]
    legs = legs_for(quotes, fair)
    by = {(r.market, None if math.isnan(r.line) else r.line): r.p_fair for r in legs.itertuples(index=False)}
    assert by == {("1x2", None): 0.5, ("ou", 2.5): 0.5, ("ou", 3.5): 0.30}


def test_leg_key_and_market_family() -> None:
    legs = legs_for([quote(market="ou", selection="over", line=2.5, odd=2.1)], [fair_row(market="ou", selection="over", line=2.5)])
    assert legs.iloc[0]["leg_key"] == "1|ou|ft|2.50|over"
    assert legs.iloc[0]["market_family"] == "goals_ft"
    assert market_family("ah", "1h") == "result_1h"
    assert market_family("corners_ou", "ft") == "corners_ft"
    assert market_family("cards_ah", "ft") == "cards_ft"


# --------------------------------------------------------------------------- #
# Reference bookmakers, time and duplicates
# --------------------------------------------------------------------------- #


def test_no_leg_is_ever_produced_at_pinnacle_or_betfair() -> None:
    quotes = [quote(book=name, odd=9.0) for name in ("Pinnacle", "Betfair", "pinnacle", "BETFAIR", "Bet365")]
    legs, diag = evaluate_legs_with_diagnostics(frame(quotes), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert list(legs["bookmaker"]) == ["Bet365"]
    assert diag.n_reference_dropped == 4
    assert {normalise_bookmaker_name(b) for b in legs["bookmaker"]}.isdisjoint({"pinnacle", "betfair"})


def test_reference_bookmakers_cannot_be_emptied() -> None:
    with pytest.raises(ValueError):
        cfg(reference_bookmakers=())


def test_stale_future_and_started_quotes_are_skipped() -> None:
    quotes = [
        quote(book="Fresh"),
        quote(book="Stale", fetched=AS_OF - pd.Timedelta(hours=4)),
        quote(book="Future", fetched=AS_OF + pd.Timedelta(minutes=1)),
        quote(book="Started", kickoff=AS_OF - pd.Timedelta(minutes=1)),
        quote(book="TooClose", kickoff=AS_OF + pd.Timedelta(minutes=5)),
    ]
    legs, diag = evaluate_legs_with_diagnostics(frame(quotes), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert list(legs["bookmaker"]) == ["Fresh"]
    assert (diag.n_stale, diag.n_untimed_or_future, diag.n_started) == (1, 1, 2)
    assert legs.iloc[0]["quote_age_min"] == pytest.approx(20.0)


def test_max_quote_age_is_configurable() -> None:
    old = quote(fetched=AS_OF - pd.Timedelta(hours=4))
    assert legs_for([old], [fair_row()]).empty
    assert len(legs_for([old], [fair_row()], config=cfg(max_quote_age_hours=6))) == 1


def test_duplicate_quotes_keep_the_latest_observation() -> None:
    quotes = [
        quote(odd=2.5, fetched=AS_OF - pd.Timedelta(minutes=50)),
        quote(odd=2.3, fetched=AS_OF - pd.Timedelta(minutes=10)),
    ]
    legs, diag = evaluate_legs_with_diagnostics(frame(quotes), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert list(legs["odd"]) == [2.3]
    assert diag.n_duplicate == 1


def test_bad_odds_and_unmapped_markets_are_counted_and_dropped() -> None:
    quotes = [quote(odd=1.0), quote(odd=np.nan, book="B"), quote(market="other", selection="x", book="C"), quote(book="D")]
    legs, diag = evaluate_legs_with_diagnostics(frame(quotes), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert list(legs["bookmaker"]) == ["D"]
    assert diag.n_bad_odds == 2 and diag.n_unmapped == 1


def test_empty_inputs_give_an_empty_frame_with_the_full_column_set() -> None:
    quotes = frame([quote()]).iloc[0:0]
    legs = evaluate_legs(quotes, frame([fair_row()]), None, cfg(), as_of=AS_OF)
    full = evaluate_legs(frame([quote()]), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert legs.empty
    assert list(legs.columns) == list(full.columns)
    assert select_value_picks(legs, cfg()).empty


# --------------------------------------------------------------------------- #
# Plausibility guards


def test_an_implausibly_large_edge_is_suspect_not_value() -> None:
    legs = legs_for([quote(odd=3.0), quote(odd=2.2, book="1xBet")], [fair_row(p=0.5, se=0.0)])
    by_book = legs.set_index("bookmaker")
    assert by_book.loc["Bet365", "leg_class"] == "suspect"  # +50% against the sharp price
    assert bool(by_book.loc["Bet365", "suspect"]) and not bool(by_book.loc["Bet365", "is_value"])
    assert by_book.loc["Bet365", "edge_raw"] == pytest.approx(0.5) and bool(by_book.loc["Bet365", "value_t100"])  # arithmetic untouched
    assert by_book.loc["1xBet", "leg_class"] == "value"
    picks = select_value_picks(legs, cfg())
    assert list(picks["bookmaker"]) == ["1xBet"]
    relaxed = legs_for([quote(odd=3.0)], [fair_row(p=0.5, se=0.0)], config=cfg(max_edge=1.0))
    assert relaxed.iloc[0]["leg_class"] == "value"


def test_a_very_uncertain_fair_price_is_suspect() -> None:
    legs = legs_for([quote(odd=2.0)], [fair_row(p=0.6, se=0.06, source="pinnacle_derived")])
    assert legs.iloc[0]["leg_class"] == "suspect" and not bool(legs.iloc[0]["is_value"])  # se 0.06 > 0.05
    _, diag = evaluate_legs_with_diagnostics(frame([quote(odd=2.0)]), frame([fair_row(p=0.6, se=0.06)]), None, cfg(), as_of=AS_OF)
    assert diag.n_suspect == 1 and diag.n_value == 0
    assert legs_for([quote(odd=2.0)], [fair_row(p=0.6, se=0.06)], config=cfg(max_p_se=0.1)).iloc[0]["leg_class"] == "value"


def test_negative_edges_are_never_suspect() -> None:
    legs = legs_for([quote(odd=1.5)], [fair_row(p=0.5, se=0.2)])
    assert legs.iloc[0]["leg_class"] == "none" and not bool(legs.iloc[0]["suspect"])


def test_an_edge_exactly_at_the_limit_is_not_suspect() -> None:
    config = cfg(max_edge=0.25)
    legs = legs_for([quote(odd=2.5)], [fair_row(p=0.5, se=0.0)], config=config)  # 0.5 * 2.5 - 1 == 0.25 exactly
    assert legs.iloc[0]["edge_raw"] == 0.25
    assert legs.iloc[0]["leg_class"] == "value" and not bool(legs.iloc[0]["suspect"])
    assert legs_for([quote(odd=2.52)], [fair_row(p=0.5, se=0.0)], config=config).iloc[0]["leg_class"] == "suspect"


def ou_pair(odd_over: float, odd_under: float, *, book: str = "Bet365", fixture: int = 1, line: float = 2.5) -> list[dict]:
    return [
        quote(fixture=fixture, book=book, market="ou", selection="over", odd=odd_over, line=line),
        quote(fixture=fixture, book=book, market="ou", selection="under", odd=odd_under, line=line),
    ]


def ou_fair(p_over: float = 0.5, *, fixture: int = 1, line: float = 2.5, se: float = 0.0) -> list[dict]:
    return [
        fair_row(fixture=fixture, market="ou", selection="over", p=p_over, se=se, line=line),
        fair_row(fixture=fixture, market="ou", selection="under", p=1.0 - p_over, se=se, line=line),
    ]


def test_book_shift_is_the_books_own_devigged_probability_minus_the_fair_one() -> None:
    legs = legs_for(ou_pair(2.2, 1.8), ou_fair(0.5)).set_index("selection")
    over = (1 / 2.2) / (1 / 2.2 + 1 / 1.8)
    assert legs.loc["over", "book_shift"] == pytest.approx(over - 0.5)
    assert legs.loc["under", "book_shift"] == pytest.approx(-(over - 0.5))  # the two sides mirror each other


def test_a_pair_that_sits_far_from_the_sharp_price_is_suspect_even_with_a_modest_edge() -> None:
    # over 2.2 against a fair 0.5 is a 10% edge, but the same book prices the under at 1.6: the whole pair moved
    # (a stale board or a mislabelled ladder), the price cannot be taken
    shifted = legs_for(ou_pair(2.2, 1.6), ou_fair(0.5)).set_index("selection")
    over = shifted.loc["over"]
    assert over["book_shift"] < -0.06 and over["edge_raw"] == pytest.approx(0.1)
    assert over["leg_class"] == "suspect" and not bool(over["is_value"]) and bool(over["value_t100"])
    # the same 10% edge with a normally priced other side is value
    normal = legs_for(ou_pair(2.2, 1.85), ou_fair(0.5)).set_index("selection")
    assert normal.loc["over", "leg_class"] == "value" and abs(normal.loc["over", "book_shift"]) < 0.06
    # the guard is configurable and a relaxed limit lets the leg through
    relaxed = legs_for(ou_pair(2.2, 1.6), ou_fair(0.5), config=cfg(max_book_shift=0.2)).set_index("selection")
    assert relaxed.loc["over", "leg_class"] == "value"


def test_a_pair_with_a_negative_margin_is_suspect() -> None:
    # over 3.0 and under 3.0 sum to 0.67 in inverse odds: no bookmaker quotes that, the shift is positive and large
    legs = legs_for(ou_pair(3.0, 3.0), ou_fair(0.36)).set_index("selection")
    over = legs.loc["over"]
    assert over["edge_raw"] == pytest.approx(0.08) and over["book_shift"] == pytest.approx(0.5 - 0.36)
    assert over["leg_class"] == "suspect"


def test_a_model_only_leg_on_an_integer_line_has_no_refund_share() -> None:
    quotes = [quote(market="team_ou_home", selection="over", line=2.0, odd=2.0)]
    model = [model_row(market="team_ou_home", selection="over", line=2.0, p=0.55)]
    row = evaluate_legs(frame(quotes), frame([fair_row()]), frame(model), cfg(), as_of=AS_OF).iloc[0]
    assert bool(row["is_model_only"]) and row["refund_share"] == 0.0  # no ladder to read it from: the model's probability is used as it is
    assert row["ev_net_t100"] == pytest.approx(0.55 * 2.0 - 1)


def test_book_shift_is_missing_without_a_complete_set_and_then_does_not_guard() -> None:
    legs = legs_for([quote(market="ou", selection="over", odd=2.2, line=2.5)], ou_fair(0.5))  # the under is not quoted
    assert math.isnan(legs.iloc[0]["book_shift"])
    assert legs.iloc[0]["leg_class"] == "value"
    # a 1X2 needs all three outcomes; double chance never has a set
    two_of_three = legs_for([quote(selection="home", odd=2.2), quote(selection="draw", odd=3.4)], [fair_row(selection="home"), fair_row(selection="draw", p=0.3)])
    assert two_of_three["book_shift"].isna().all()
    dc = legs_for([quote(market="dc", selection="1x", odd=1.5)], [fair_row(market="dc", selection="1x", p=0.7)])
    assert math.isnan(dc.iloc[0]["book_shift"])


def test_book_shift_of_a_three_way_market() -> None:
    quotes = [quote(selection="home", odd=2.1), quote(selection="draw", odd=3.5), quote(selection="away", odd=3.8)]
    fair = [fair_row(selection="home", p=0.5), fair_row(selection="draw", p=0.28), fair_row(selection="away", p=0.22)]
    legs = legs_for(quotes, fair).set_index("selection")
    total = 1 / 2.1 + 1 / 3.5 + 1 / 3.8
    assert legs.loc["home", "book_shift"] == pytest.approx((1 / 2.1) / total - 0.5)
    assert legs.loc["draw", "book_shift"] == pytest.approx((1 / 3.5) / total - 0.28)


def test_fair_rows_of_one_leg_keep_the_best_informed_one() -> None:
    fair = [fair_row(p=0.40, source="betfair") | {"n_books": 1}, fair_row(p=0.50, source="consensus") | {"n_books": 5}]
    legs = legs_for([quote(odd=2.2)], fair)
    assert len(legs) == 1 and legs.iloc[0]["p_fair"] == 0.5 and legs.iloc[0]["fair_source"] == "consensus"


def test_lines_of_one_ladder_never_collide_in_the_join() -> None:
    quotes = [quote(market="ou", selection="over", odd=2.1, line=line) for line in (2.0, 2.25, 2.5)]
    fair = [fair_row(market="ou", selection="over", p=p, line=line) for line, p in ((2.0, 0.6), (2.25, 0.55), (2.5, 0.5))]
    legs = legs_for(quotes, fair).sort_values("line")
    assert list(legs["p_fair"]) == [0.6, 0.55, 0.5]


# --------------------------------------------------------------------------- #
# Model: tie-break, veto, model-only class
# --------------------------------------------------------------------------- #


def test_model_agrees_uses_delta_and_is_nan_without_a_model() -> None:
    quotes = [quote(selection="home", odd=2.3), quote(selection="draw", odd=3.4), quote(selection="away", odd=4.4)]
    fair = [fair_row(selection="home", p=0.5), fair_row(selection="draw", p=0.3), fair_row(selection="away", p=0.2)]
    model = [model_row(selection="home", p=0.49), model_row(selection="draw", p=0.25)]
    legs = legs_for(quotes, fair, model).set_index("selection")
    assert legs.loc["home", "model_agrees"] == 1.0  # 0.49 >= 0.50 - 0.02
    assert legs.loc["draw", "model_agrees"] == 0.0  # 0.25 <  0.30 - 0.02
    assert math.isnan(legs.loc["away", "model_agrees"])
    tight = legs_for(quotes, fair, model, cfg(model_delta=0.0)).set_index("selection")
    assert tight.loc["home", "model_agrees"] == 0.0


def test_model_never_overrides_the_market_price_by_default() -> None:
    quotes = [quote(odd=2.4)]
    fair = [fair_row(p=0.5, se=0.01)]
    contradicting = [model_row(p=0.30)]
    legs = legs_for(quotes, fair, contradicting)
    assert bool(legs.iloc[0]["is_value"])  # the Pinnacle price decides
    assert legs.iloc[0]["p_fair"] == 0.5 and legs.iloc[0]["p_used"] == 0.5


def test_legacy_model_veto_rejects_legs_the_model_contradicts() -> None:
    quotes = [quote(odd=2.4)]
    fair = [fair_row(p=0.5, se=0.01)]
    legs = legs_for(quotes, fair, [model_row(p=0.30)], cfg(model_veto=True))
    row = legs.iloc[0]
    assert bool(row["vetoed"]) and not bool(row["is_value"])
    assert row["leg_class"] == "near_miss"
    # a model without an opinion does not veto, and an agreeing model does not either
    assert bool(legs_for(quotes, fair, None, cfg(model_veto=True)).iloc[0]["is_value"])
    assert bool(legs_for(quotes, fair, [model_row(p=0.5)], cfg(model_veto=True)).iloc[0]["is_value"])


def test_model_only_legs_are_a_separate_flagged_class() -> None:
    quotes = [quote(market="team_ou_home", selection="over", line=1.5, odd=2.0)]
    model = [model_row(market="team_ou_home", selection="over", line=1.5, p=0.55)]
    legs = evaluate_legs(frame(quotes), frame([fair_row()]), frame(model), cfg(), as_of=AS_OF)
    row = legs.iloc[0]
    assert row["fair_source"] == "model" and bool(row["is_model_only"])
    assert math.isnan(row["p_fair"]) and row["p_used"] == pytest.approx(0.55)
    assert row["leg_class"] == "model_only"
    assert not bool(row["is_value"])
    assert bool(row["is_model_only_value"])  # 0.55 - 1.645 * 0.03 = 0.5006; 0.5006 * 2.0 > 1
    assert select_value_picks(legs, cfg()).empty
    assert len(select_model_only(legs, cfg())) == 1
    assert build_coupons(legs, cfg()).coupons.empty


def test_model_only_can_be_switched_off_and_needs_a_model() -> None:
    quotes = [quote(market="team_ou_home", selection="over", line=1.5, odd=2.0)]
    model = [model_row(market="team_ou_home", selection="over", line=1.5, p=0.55)]
    off = evaluate_legs(frame(quotes), frame([fair_row()]), frame(model), cfg(include_model_only=False), as_of=AS_OF)
    assert off.empty
    none = evaluate_legs(frame(quotes), frame([fair_row()]), None, cfg(), as_of=AS_OF)
    assert none.empty  # neither market fair nor model: unpriced, never guessed


# --------------------------------------------------------------------------- #
# Value list
# --------------------------------------------------------------------------- #


def test_one_pick_per_fixture_bookmaker_and_market_family() -> None:
    quotes = [
        quote(market="ou", selection="over", line=2.5, odd=2.10),
        quote(market="ou", selection="over", line=3.5, odd=3.60),
        quote(market="btts", selection="yes", odd=2.05),
        quote(market="ou", selection="over", line=2.5, odd=2.10, book="1xBet"),
        quote(market="corners_ou", selection="over", line=9.5, odd=2.10),
    ]
    fair = [
        fair_row(market="ou", selection="over", line=2.5, p=0.5, se=0.0),
        fair_row(market="ou", selection="over", line=3.5, p=0.30, se=0.0),
        fair_row(market="btts", selection="yes", p=0.5, se=0.0),
        fair_row(market="corners_ou", selection="over", line=9.5, p=0.5, se=0.0),
    ]
    legs = legs_for(quotes, fair)
    picks = select_value_picks(legs, cfg())
    per_group = picks.groupby(["fixture_id", "bookmaker", "market_family"]).size()
    assert (per_group == 1).all()
    bet365_goals = picks[(picks["bookmaker"] == "Bet365") & (picks["market_family"] == "goals_ft")].iloc[0]
    # the 3.5 line has the higher lower-bound EV (0.3 * 3.6 = 1.08) than 2.5 (0.5 * 2.1 = 1.05) and btts (1.025)
    assert bet365_goals["line"] == 3.5
    assert set(picks["market_family"]) == {"goals_ft", "corners_ft"}
    assert list(picks["rank"]) == list(range(1, len(picks) + 1))
    ev = picks["ev_net_lower_t100"].to_list()
    assert ev == sorted(ev, reverse=True)


def test_higher_win_probability_breaks_ties_between_equal_value() -> None:
    quotes = [quote(fixture=1, odd=1.6), quote(fixture=2, odd=3.2)]
    fair = [fair_row(fixture=1, p=0.7, se=0.0), fair_row(fixture=2, p=0.35, se=0.0)]
    # both have p * odd = 1.12
    picks = select_value_picks(legs_for(quotes, fair), cfg())
    assert list(picks["fixture_id"]) == [1, 2]


# --------------------------------------------------------------------------- #
# Refunds on integer and quarter lines
# --------------------------------------------------------------------------- #


def ladder_fair(market: str, selection: str, points: dict[float, float], *, fixture: int = 1, period: str = "ft") -> list[dict]:
    """Fair rows of one side of a ladder: ``points`` maps a line to that side's probability."""
    return [fair_row(fixture=fixture, market=market, selection=selection, p=p, line=line, period=period) for line, p in points.items()]


HANDICAP_LADDER = ladder_fair("ah", "home", {-1.5: 0.25, -1.0: 0.30, -0.5: 0.50, 0.5: 0.75})
TOTAL_LADDER = ladder_fair("ou", "over", {1.5: 0.75, 2.0: 0.6, 2.5: 0.50})


def refunds(rows: list[dict], legs: list[dict]) -> dict[tuple[str, float, str], float]:
    """``refund_share`` per (market, line, selection) of ``legs`` given the fair ``rows``."""
    from model_buk.scanner.engine import normalise_keys, refund_share

    frame = pd.DataFrame(legs)
    values = refund_share(normalise_keys(frame), normalise_keys(pd.DataFrame(rows)))
    return {(m, ln, s): v for m, ln, s, v in zip(frame["market"], frame["line"], frame["selection"], values)}


def leg(market: str, line: float, selection: str, *, fixture: int = 1, period: str = "ft") -> dict:
    return {"fixture_id": fixture, "market": market, "period": period, "line": line, "selection": selection}


def test_refund_share_of_an_integer_handicap_is_the_push_probability() -> None:
    # home wins by two or more with 0.25, by one or more with 0.50: the push at -1 has probability 0.25
    out = refunds(HANDICAP_LADDER, [leg("ah", -1.0, "home"), leg("ah", -1.0, "away")])
    assert out[("ah", -1.0, "home")] == pytest.approx(0.25) and out[("ah", -1.0, "away")] == pytest.approx(0.25)


def test_refund_share_of_a_quarter_line_is_half_the_push_probability_of_its_integer_side() -> None:
    out = refunds(HANDICAP_LADDER, [leg("ah", -1.25, "home"), leg("ah", -0.75, "away")])
    assert out[("ah", -1.25, "home")] == pytest.approx(0.125)  # half on -1.5, half on -1.0
    assert out[("ah", -0.75, "away")] == pytest.approx(0.125)  # half on -1.0, half on -0.5


def test_refund_share_of_a_handicap_next_to_zero() -> None:
    rows = ladder_fair("ah", "home", {-0.5: 0.5, 0.5: 0.8, -0.25: 0.55, 0.0: 0.65})
    out = refunds(rows, [leg("ah", 0.0, "home"), leg("ah", 0.25, "home"), leg("ah", -0.25, "home")])
    assert out[("ah", 0.0, "home")] == pytest.approx(0.3)  # the draw: 0.8 - 0.5
    assert out[("ah", 0.25, "home")] == pytest.approx(0.15)  # half of it
    assert out[("ah", -0.25, "home")] == pytest.approx(0.15)


def test_refund_share_of_a_total_and_of_team_and_corner_totals() -> None:
    out = refunds(TOTAL_LADDER, [leg("ou", 2.0, "over"), leg("ou", 2.0, "under"), leg("ou", 2.25, "over"), leg("ou", 1.75, "under")])
    assert out[("ou", 2.0, "over")] == pytest.approx(0.25)  # exactly two goals: over 1.5 minus over 2.5
    assert out[("ou", 2.0, "under")] == pytest.approx(0.25)
    assert out[("ou", 2.25, "over")] == pytest.approx(0.125)
    corner_rows = ladder_fair("corners_ou", "over", {9.5: 0.55, 10.5: 0.45})
    assert refunds(corner_rows, [leg("corners_ou", 10.0, "over")])[("corners_ou", 10.0, "over")] == pytest.approx(0.10)
    team_rows = ladder_fair("team_ou_home", "under", {0.5: 0.3, 1.5: 0.6})  # only the under side is priced: over = 1 - under
    assert refunds(team_rows, [leg("team_ou_home", 1.0, "over")])[("team_ou_home", 1.0, "over")] == pytest.approx(0.3)


def test_no_refund_on_half_lines_and_on_markets_without_lines() -> None:
    out = refunds(
        HANDICAP_LADDER + TOTAL_LADDER,
        [leg("ah", -0.5, "home"), leg("ou", 2.5, "over"), leg("1x2", np.nan, "home"), leg("btts", np.nan, "yes"), leg("dc", np.nan, "1x"), leg("cs", np.nan, "1:0")],
    )
    assert set(out.values()) == {0.0}


def test_refund_share_is_missing_when_the_ladder_cannot_give_it() -> None:
    lonely = refunds(ladder_fair("ou", "over", {2.5: 0.5}), [leg("ou", 3.0, "over")])  # no 2.5-and-3.5 neighbours
    assert math.isnan(lonely[("ou", 3.0, "over")])
    incoherent = refunds(ladder_fair("ou", "over", {1.5: 0.40, 2.5: 0.50}), [leg("ou", 2.0, "over")])  # over 1.5 below over 2.5
    assert math.isnan(incoherent[("ou", 2.0, "over")])
    other_fixture = refunds(HANDICAP_LADDER, [leg("ah", -1.0, "home", fixture=2)])
    assert math.isnan(other_fixture[("ah", -1.0, "home")])
    other_period = refunds(HANDICAP_LADDER, [leg("ah", -1.0, "home", period="1h")])
    assert math.isnan(other_period[("ah", -1.0, "home")])


def test_draw_no_bet_refunds_the_draw() -> None:
    rows = [fair_row(selection="home", p=0.45), fair_row(selection="draw", p=0.28), fair_row(selection="away", p=0.27)]
    from model_buk.scanner.engine import normalise_keys, refund_share

    legs = normalise_keys(pd.DataFrame([leg("dnb", np.nan, "home"), leg("dnb", np.nan, "away", period="1h"), leg("dnb", np.nan, "home", fixture=2)]))
    values = refund_share(legs, normalise_keys(pd.DataFrame(rows)))
    assert values[0] == pytest.approx(0.28)  # the 1X2 draw of the same fixture and period
    assert math.isnan(values[1]) and math.isnan(values[2])  # no first-half 1X2 and no such fixture: unknown


def test_ev_of_a_line_with_refunds_is_scaled_by_the_share_that_does_not_come_back() -> None:
    # break-even probability 0.60 at odds 1.8 looks like +8% but a quarter of the stake is refunded on a push
    ladder = ladder_fair("ou", "over", {1.5: 0.80, 2.0: 0.60, 2.5: 0.55})  # push probability at 2.0: 0.80 - 0.55 = 0.25
    legs = legs_for([quote(market="ou", selection="over", line=2.0, odd=1.8)], ladder, config=cfg(max_book_shift=1.0))
    row = legs.iloc[0]
    assert row["refund_share"] == pytest.approx(0.25)
    assert row["edge_raw"] == pytest.approx(0.6 * 1.8 - 1)  # the price against the fair price is unscaled
    assert row["ev_net_t100"] == pytest.approx(0.75 * (0.6 * 1.8 - 1))
    assert row["ev_net_t088"] == pytest.approx(0.75 * (0.88 * 0.6 * 1.8 - 1))
    p_lower = 0.6 - 1.645 * 0.02
    assert row["ev_net_lower_t088"] == pytest.approx(0.75 * (0.88 * p_lower * 1.8 - 1))  # the lower bound is scaled too
    assert row["ev_net_lower_t100"] == pytest.approx(0.75 * (p_lower * 1.8 - 1))
    # the same probability on a half line has no refund and the full EV
    plain = legs_for([quote(market="ou", selection="over", line=2.5, odd=1.8)], ladder, config=cfg(max_book_shift=1.0))
    assert plain.iloc[0]["refund_share"] == 0.0 and plain.iloc[0]["ev_net_t100"] == pytest.approx(0.55 * 1.8 - 1)


def test_an_unknown_refund_share_leaves_the_leg_a_single_value_leg_but_not_a_coupon_leg() -> None:
    legs = legs_for([quote(market="ou", selection="over", line=3.0, odd=2.4)], ladder_fair("ou", "over", {3.0: 0.5}))
    row = legs.iloc[0]
    assert math.isnan(row["refund_share"]) and bool(row["is_value"])
    assert row["ev_net_t100"] == pytest.approx(0.5 * 2.4 - 1)  # unscaled: only the sign is exact
    unknown = select_value_picks(legs, cfg())
    others = picks_from([value_leg(i) for i in (2, 3, 4)])
    result = build_coupons(pd.concat([unknown, others], ignore_index=True), cfg())
    assert not result.legs.empty  # the other three legs still make coupons
    assert row["leg_key"] not in set(result.legs["leg_key"])  # 1.2 * 1.05 * 0.88 would have passed had it been used


# --------------------------------------------------------------------------- #
# PIT and config
# --------------------------------------------------------------------------- #


def test_the_pit_threshold_is_strictly_above() -> None:
    # 1140 * 1.0 * 2.0 = 2280 exactly: the payout does not exceed the threshold, so no PIT
    assert list(effective_tau(1.0, np.array([2.0, 2.0001]), 1140.0)) == [1.0, 0.9]
    assert effective_tax_multiplier(1140.0, 2.0, 1.0) == 1.0


def test_paper_stake_switches_on_the_winnings_pit() -> None:
    odd = np.array([2.0, 4.0])
    tau_row = effective_tau(0.88, odd, 1000.0)
    assert list(tau_row) == pytest.approx([0.88, 0.88 * 0.9])  # 1000 * 0.88 * 4 = 3520 > 2280
    for o, t in zip(odd, tau_row):
        assert t == pytest.approx(effective_tax_multiplier(1000.0, float(o), 0.88))
    assert list(effective_tau(0.88, odd, None)) == [0.88, 0.88]
    legs = legs_for([quote(odd=4.0)], [fair_row(p=0.3, se=0.0)], config=cfg(stake_pln=1000.0))
    assert legs.iloc[0]["ev_net_t088"] == pytest.approx(0.88 * 0.9 * 0.3 * 4.0 - 1)
    assert legs.iloc[0]["ev_net_t100"] == pytest.approx(0.9 * 0.3 * 4.0 - 1)


def test_every_config_change_is_a_new_config_id() -> None:
    base = ScannerConfig()
    assert base.config_id == ScannerConfig().config_id
    for change in ({"z": 1.0}, {"min_hit": 0.25}, {"top_k_legs": 10}, {"model_veto": True}, {"taus": (0.88, 1.0), "leg_tau": 1.0}):
        assert ScannerConfig(**change).config_id != base.config_id


@pytest.mark.parametrize(
    "bad",
    [{"z": -1.0}, {"taus": (0.0,)}, {"leg_tau": 0.5}, {"coupon_keep_tau": 0.9}, {"min_hit": 1.5}, {"coupon_sizes": (1,)}, {"stake_pln": -5.0}],
)
def test_invalid_config_is_rejected(bad: dict) -> None:
    with pytest.raises(ValueError):
        ScannerConfig(**bad)


# --------------------------------------------------------------------------- #
# Coupons
# --------------------------------------------------------------------------- #


def value_leg(fixture: int, book: str = "Bet365", *, p: float = 0.7, odd: float = 1.5, se: float = 0.0, selection: str = "home") -> tuple[dict, dict]:
    return quote(fixture=fixture, book=book, odd=odd, selection=selection), fair_row(fixture=fixture, p=p, se=se, selection=selection)


def picks_from(pairs: list[tuple[dict, dict]], config: ScannerConfig | None = None) -> pd.DataFrame:
    config = config or cfg()
    legs = evaluate_legs(frame([q for q, _ in pairs]), frame([f for _, f in pairs]), None, config, as_of=AS_OF)
    return select_value_picks(legs, config)


def test_three_legs_with_five_percent_edge_are_positive_after_the_12_percent_tax() -> None:
    picks = picks_from([value_leg(i) for i in (1, 2, 3)])  # 0.7 * 1.5 = 1.05 each
    result = build_coupons(picks, cfg())
    assert len(result.coupons) == 1
    coupon = result.coupons.iloc[0]
    assert coupon["n_legs"] == 3
    assert coupon["odds_total"] == pytest.approx(1.5**3)
    assert coupon["p_hit"] == pytest.approx(0.7**3)
    assert 1.05**3 * 0.88 - 1 == pytest.approx(0.01871, abs=1e-5)
    assert coupon["ev_net_t088"] == pytest.approx(1.05**3 * 0.88 - 1)
    assert coupon["ev_net_lower_t088"] == pytest.approx(1.05**3 * 0.88 - 1)
    assert coupon["ev_net_t094"] == pytest.approx(1.05**3 * 0.94 - 1)
    assert coupon["ev_net_t100"] == pytest.approx(1.05**3 - 1)
    assert bool(coupon["pass_t088"])


def test_two_legs_with_five_percent_edge_are_not_enough() -> None:
    picks = picks_from([value_leg(i) for i in (1, 2)])
    assert 1.05**2 * 0.88 - 1 < 0
    assert build_coupons(picks, cfg()).coupons.empty


def test_coupon_lower_bound_uses_p_lower_of_every_leg() -> None:
    config = cfg(z=1.645)
    picks = picks_from([value_leg(i, p=0.7, odd=1.65, se=0.02) for i in (1, 2, 3)], config)
    result = build_coupons(picks, config)
    p_lower = 0.7 - 1.645 * 0.02
    coupon = result.coupons.iloc[0]
    assert coupon["ev_net_t088"] == pytest.approx(0.88 * (0.7 * 1.65) ** 3 - 1)
    assert coupon["ev_net_lower_t088"] == pytest.approx(0.88 * (p_lower * 1.65) ** 3 - 1)
    assert coupon["p_hit_lower"] == pytest.approx(p_lower**3)


def test_coupon_legs_come_from_different_fixtures_at_one_bookmaker() -> None:
    pairs = [value_leg(f, book) for f in (1, 2, 3, 4) for book in ("Bet365", "1xBet")]
    # a second leg of fixture 1 at Bet365 in another market family
    extra_q = quote(fixture=1, book="Bet365", market="btts", selection="yes", odd=1.5)
    extra_f = fair_row(fixture=1, market="btts", selection="yes", p=0.7, se=0.0)
    pairs.append((extra_q, extra_f))
    config = cfg()
    picks = picks_from(pairs, config)
    assert (picks["fixture_id"] == 1).sum() == 3  # Bet365 in two market families, 1xBet in one
    result = build_coupons(picks, config)
    assert len(result.coupons) > 0
    assert result.n_evaluated >= result.n_passing == len(result.coupons)
    for cid, group in result.legs.groupby("coupon_id"):
        assert group["fixture_id"].is_unique  # at most one leg per fixture
        assert group["bookmaker"].nunique() == 1  # one bookmaker
        assert 2 <= len(group) <= 3
        assert cid == coupon_id(group["bookmaker"].iloc[0], list(group["leg_key"]))
    assert set(result.coupons["bookmaker"]) == {"Bet365", "1xBet"}


def test_coupons_are_ranked_capped_per_bookmaker_but_all_passing_are_returned() -> None:
    pairs = [value_leg(f, "Bet365", p=0.75, odd=1.5 + 0.01 * f) for f in range(1, 9)]
    config = cfg(top_n_per_bookmaker=5, top_max_leg_reuse=100)
    result = build_coupons(picks_from(pairs, config), config)
    coupons = result.coupons
    assert len(coupons) > 5  # every passing coupon is kept for the log (no cap is hit here)
    assert int(coupons["is_top"].sum()) == 5 and len(result.top) == 5
    assert list(coupons["rank"]) == list(range(1, len(coupons) + 1))
    ev = coupons["ev_net_lower_t088"].to_list()
    assert ev == sorted(ev, reverse=True)
    assert set(coupons.loc[coupons["is_top"], "rank"]) == {1, 2, 3, 4, 5}


def test_log_cap_limits_the_logged_coupons_only() -> None:
    pairs = [value_leg(f, "Bet365", p=0.75, odd=1.5 + 0.01 * f) for f in range(1, 9)]
    config = cfg(top_n_per_bookmaker=3, log_cap_per_bookmaker=4, top_max_leg_reuse=100)
    result = build_coupons(picks_from(pairs, config), config)
    assert result.n_passing > len(result.coupons) == 4  # the full count survives, only 4 rows are materialised
    assert int(result.coupons["is_logged"].sum()) == 4
    assert set(result.legs["coupon_id"]) == set(result.coupons["coupon_id"])
    uncapped = build_coupons(picks_from(pairs, config), cfg(log_cap_per_bookmaker=None, top_max_leg_reuse=100))
    assert uncapped.n_passing == len(uncapped.coupons) == result.n_passing
    assert list(uncapped.coupons["coupon_id"][:4]) == list(result.coupons["coupon_id"])  # the same best coupons


def test_proposed_coupons_do_not_reuse_the_same_legs_too_often() -> None:
    pairs = [value_leg(f, "Bet365", p=0.75, odd=1.5 + 0.01 * f) for f in range(1, 10)]
    config = cfg(top_n_per_bookmaker=10, top_max_leg_reuse=1)
    result = build_coupons(picks_from(pairs, config), config)
    proposed = result.legs[result.legs["coupon_id"].isin(result.top["coupon_id"])]
    assert proposed["leg_key"].is_unique  # with a reuse limit of one the proposals share no leg
    assert len(result.top) == 3  # nine legs make three disjoint three-leg coupons
    loose = cfg(top_n_per_bookmaker=10, top_max_leg_reuse=100)
    assert len(build_coupons(picks_from(pairs, loose), loose).top) == 10


def test_minimum_hit_probability_filters_coupons() -> None:
    picks = picks_from([value_leg(i, p=0.34, odd=3.2) for i in (1, 2, 3)])  # p*odd = 1.088, hit = 0.039
    assert not picks.empty
    assert build_coupons(picks, cfg(min_hit=0.30)).coupons.empty
    assert len(build_coupons(picks, cfg(min_hit=0.02)).coupons) >= 1


def test_pruning_keeps_the_number_of_combinations_bounded() -> None:
    pairs = [value_leg(f, "Bet365", p=0.75, odd=1.5 + 0.001 * f) for f in range(1, 121)]
    config = cfg(top_k_legs=10, top_k_hit_legs=5)
    picks = picks_from(pairs, config)
    result = build_coupons(picks, config)
    assert result.n_evaluated <= 15 * 14 // 2 + 15 * 14 * 13 // 6


def test_coupons_ignore_model_only_and_reference_legs() -> None:
    picks = picks_from([value_leg(i) for i in (1, 2, 3)])
    tainted = picks.copy()
    tainted.loc[tainted.index[0], "bookmaker"] = "Pinnacle"
    tainted.loc[tainted.index[1], "is_model_only"] = True
    assert build_coupons(tainted, cfg()).coupons.empty


def test_reference_bookmaker_legs_never_enter_a_coupon() -> None:
    # 0.7 * 1.7 = 1.19 per leg: two legs are a good coupon wherever they are quoted
    picks = picks_from([value_leg(i, p=0.7, odd=1.7) for i in (1, 2, 3, 4)])
    picks.loc[picks["fixture_id"].isin([3, 4]), "bookmaker"] = "Pinnacle"
    result = build_coupons(picks, cfg())
    assert set(result.coupons["bookmaker"]) == {"Bet365"}
    assert set(result.legs["fixture_id"]) == {1, 2}


def test_model_only_legs_never_enter_a_coupon() -> None:
    picks = picks_from([value_leg(i, p=0.7, odd=1.7) for i in (1, 2, 3, 4)])
    picks["is_model_only"] = picks["fixture_id"].isin([3, 4])
    result = build_coupons(picks, cfg())
    assert len(result.coupons) == 1
    assert set(result.legs["fixture_id"]) == {1, 2}


def test_a_fixture_never_appears_twice_in_a_coupon_even_when_two_of_its_legs_survive_the_pruning() -> None:
    # fixture 1 has a leg with the best EV (3.0 * 0.37 = 1.11) and a different one with the best win probability
    # (0.85), so both go into the candidate pool; together they would make a passing coupon of one match
    pairs = [
        (quote(fixture=1, selection="home", odd=3.0), fair_row(fixture=1, p=0.37, se=0.0)),
        (
            quote(fixture=1, market="btts", selection="yes", odd=1.3),
            fair_row(fixture=1, market="btts", selection="yes", p=0.85, se=0.0),
        ),
        value_leg(2, p=0.7, odd=1.5),
        value_leg(3, p=0.7, odd=1.5),
    ]
    config = cfg()
    picks = picks_from(pairs, config)
    assert (picks["fixture_id"] == 1).sum() == 2
    result = build_coupons(picks, config)
    assert len(result.coupons) > 0
    for _, group in result.legs.groupby("coupon_id"):
        assert group["fixture_id"].is_unique


def coupon_picks(refund: float | None, *, n: int = 2, p: float = 0.7, odd: float = 1.7) -> pd.DataFrame:
    rows = [
        {
            "fixture_id": i,
            "bookmaker": "Bet365",
            "market": "ah",
            "period": "ft",
            "line": -1.0,
            "selection": "home",
            "odd": odd,
            "p_fair": p,
            "p_lower": p,
            "leg_key": f"{i}|ah|ft|-1.00|home",
            "kickoff_utc": KICK,
        }
        for i in range(1, n + 1)
    ]
    frame = pd.DataFrame(rows)
    if refund is not None:
        frame["refund_share"] = refund
    return frame


def test_coupon_ev_and_hit_probability_allow_for_refunds() -> None:
    config = cfg(min_hit=0.2)
    coupon = build_coupons(coupon_picks(0.25), config).coupons.iloc[0]
    factor = 1 + 0.75 * (0.7 * 1.7 - 1)  # mean return factor of a leg: a quarter of the stake comes back on a push
    assert coupon["ev_net_t088"] == pytest.approx(0.88 * factor**2 - 1)
    assert coupon["ev_net_t100"] == pytest.approx(factor**2 - 1)
    assert coupon["p_hit"] == pytest.approx((0.75 * 0.7) ** 2)  # a leg wins with probability (1 - b) * p
    assert coupon["p_hit_lower"] == pytest.approx((0.75 * 0.7) ** 2)
    assert coupon["odds_total"] == pytest.approx(1.7**2)
    naive = build_coupons(coupon_picks(0.0), config).coupons.iloc[0]
    assert naive["ev_net_t088"] == pytest.approx(0.88 * (0.7 * 1.7) ** 2 - 1) and naive["p_hit"] == pytest.approx(0.49)
    assert naive["ev_net_t088"] > coupon["ev_net_t088"]
    # a frame without the column behaves as no refunds
    assert build_coupons(coupon_picks(None), config).coupons.iloc[0]["ev_net_t088"] == pytest.approx(naive["ev_net_t088"])
    assert list(build_coupons(coupon_picks(0.25), config).legs["refund_share"]) == [0.25, 0.25]


def test_refunds_can_make_a_coupon_fail_that_the_plain_product_passes() -> None:
    # three legs at +5% would give +1.87% at tau 0.88; with a 20% refund share each the mean factor is 1.04
    picks = coupon_picks(0.2, n=3, p=0.7, odd=1.5)
    assert 0.88 * 1.05**3 - 1 > 0 > 0.88 * 1.04**3 - 1
    assert build_coupons(picks, cfg(min_hit=0.0)).coupons.empty
    assert len(build_coupons(coupon_picks(0.0, n=3, p=0.7, odd=1.5), cfg(min_hit=0.0)).coupons) == 1


def test_empty_value_list_gives_an_empty_coupon_set() -> None:
    result = build_coupons(pd.DataFrame(), cfg())
    assert isinstance(result, CouponSet) and result.coupons.empty and result.legs.empty and result.top.empty


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

FIXTURES = pd.DataFrame(
    {
        "fixture_id": [1, 2, 3, 4],
        "league_id": [39] * 4,
        "league_name": ["Premier League"] * 4,
        "country": ["England"] * 4,
        "season": [2026] * 4,
        "round": ["R7"] * 4,
        "kickoff_utc": [KICK] * 4,
        "status_short": ["NS"] * 4,
        "home_team_id": [10, 12, 14, 16],
        "home_team": ["Arsenal", "Chelsea <b>", "Leeds", "Fulham"],
        "away_team_id": [11, 13, 15, 17],
        "away_team": ["Spurs", "Everton & Co", "Wolves", "Brentford"],
        "home_goals": [np.nan] * 4,
        "away_goals": [np.nan] * 4,
    }
)


def full_scan(**kwargs):
    pairs = [value_leg(i, p=0.7, odd=1.5) for i in (1, 2, 3, 4)]
    quotes = frame([q for q, _ in pairs])
    quotes.loc[2, "kickoff_utc"] = KICK + pd.Timedelta(days=1)
    return run_scan(
        quotes,
        frame([f for _, f in pairs]),
        None,
        FIXTURES,
        config=cfg(),
        as_of=AS_OF,
        **kwargs,
    )


def test_page_renders_with_full_data(tmp_path) -> None:
    result = full_scan(out_dir=tmp_path)
    page = result.html
    sections = (
        "Skaner wartości",
        "Single opłacalne same w sobie przy podatku 12%",
        "Kupony 2–3 nogi przy podatku 12%",
        "Tylko przy niższym podatku",
        "Eksperymentalne: rynki słabe",
        "Wyniki papierowe",
        "Jak czytać",
    )
    positions = [page.index(text) for text in sections]
    assert positions == sorted(positions)  # the sections come in this order
    # only Bet365 quotes and no Pinnacle: every fixture is a thin market, shown in the experimental section
    assert "Dziś nic nie opłaca się po podatku 12%" in page
    assert "brak Pinnacle" in page
    assert "Arsenal" in page and "Spurs" in page
    assert "Dziś" in page and "Jutro" in page  # grouped by match day, today first
    assert page.index("Dziś") < page.index("Jutro")
    assert "Chelsea &lt;b&gt;" in page and "Chelsea <b>" not in page  # data is escaped
    assert "Everton &amp; Co" in page
    assert "Arsenal wygra" in page
    assert 'lang="pl"' in page and 'name="viewport"' in page
    assert "tryb papierowy" in page


def test_page_is_self_contained() -> None:
    page = full_scan().html
    assert "<link" not in page
    assert "src=" not in page and "href=" not in page
    assert "http://" not in page and "https://" not in page
    assert "@import" not in page and "url(" not in page


def test_page_renders_with_empty_data() -> None:
    result = run_scan(frame([quote()]).iloc[0:0], frame([fair_row()]), None, FIXTURES, as_of=AS_OF)
    page = result.html
    assert "Kupony 2–3 nogi przy podatku 12%" in page and "Dziś nic nie opłaca się po podatku 12%" in page
    assert "Single opłacalne same w sobie" in page and "Żaden pojedynczy kurs" in page
    assert "Na słabych rynkach nie ma dziś nóg z wartością" in page
    assert "Wyniki papierowe" in page and "Jeszcze nic do podsumowania" in page
    assert "Jak czytać" in page


def test_page_is_written_as_latest_and_dated_file(tmp_path) -> None:
    result = full_scan(out_dir=tmp_path)
    assert {p.name for p in result.html_paths} == {"latest.html", "2026-09-30.html"}
    assert (tmp_path / "latest.html").read_text(encoding="utf-8") == result.html
    assert (tmp_path / "html" / "2026-09-30.html").exists()
    again = full_scan(out_dir=tmp_path)  # overwriting is fine
    assert again.html_paths == result.html_paths


def test_leg_labels_are_plain_polish() -> None:
    label = report_html.leg_label_pl
    assert label("1x2", "ft", np.nan, "draw", "A", "B") == "Remis"
    assert label("ou", "ft", 2.5, "over", "A", "B") == "Gole: powyżej 2,5"
    assert label("ou", "1h", 0.5, "under", "A", "B") == "1. połowa: Gole: poniżej 0,5"
    assert label("ah", "ft", -0.5, "away", "A", "B") == "Handicap B +0,5"
    assert label("ah", "ft", -0.25, "home", "A", "B") == "Handicap A -0,25"
    assert label("btts", "ft", np.nan, "yes", "A", "B") == "Obie strzelą: tak"
    assert label("dc", "ft", np.nan, "1x", "A", "B") == "A lub remis"
    assert label("corners_ou", "ft", 9.5, "over", "A", "B") == "Rożne: powyżej 9,5"
    assert label("cards_ah", "ft", 1.5, "home", "A", "B") == "Kartki, handicap A +1,5"
    assert report_html.fmt_pct(0.0523, 1, signed=True) == "+5,2%"
    assert report_html.fmt_num(float("nan")) == "—"
    assert report_html.fmt_line(2.0) == "2"
    assert report_html.fmt_int(45113) == "45 113"


def test_run_scan_reports_diagnostics_and_leaves_inputs_untouched() -> None:
    pairs = [value_leg(i) for i in (1, 2, 3)]
    quotes, fair = frame([q for q, _ in pairs]), frame([f for _, f in pairs])
    quotes_before, fair_before = quotes.copy(), fair.copy()
    result = run_scan(quotes, fair, None, FIXTURES, as_of=AS_OF)
    pd.testing.assert_frame_equal(quotes, quotes_before)
    pd.testing.assert_frame_equal(fair, fair_before)
    assert result.diagnostics.n_quotes == 3 and result.diagnostics.n_value == 3
    assert result.scan_id == "20260930T120000Z"
    assert len(result.coupons.proposed()) == 1  # plus the two-leg coupons that pass only under a lower tax
    assert set(result.coupons.coupons["regime"]) == {"t088", "t094"}


# --------------------------------------------------------------------------- #
# The daily scan on (synthetic) archives
# --------------------------------------------------------------------------- #


def _synthetic_archives(tmp_path):
    import sqlite3

    odds = tmp_path / "odds.sqlite3"
    kickoff = "2026-09-30T20:00:00+00:00"
    with sqlite3.connect(odds) as con:
        con.executescript(
            """
            CREATE TABLE fixtures (fixture_id INTEGER PRIMARY KEY, league_id INTEGER NOT NULL, season INTEGER,
                kickoff_utc TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_api_update TEXT);
            CREATE TABLE observations (scan_id TEXT NOT NULL, fixture_id INTEGER NOT NULL, bookmaker_id INTEGER NOT NULL,
                fetched_at TEXT NOT NULL, api_update TEXT, PRIMARY KEY (scan_id, fixture_id, bookmaker_id));
            CREATE TABLE quotes (fixture_id INTEGER NOT NULL, bookmaker_id INTEGER NOT NULL, bookmaker TEXT, bet_id INTEGER NOT NULL,
                bet TEXT, selection TEXT NOT NULL, odd REAL NOT NULL, api_update TEXT, fetched_at TEXT NOT NULL, scan_id TEXT NOT NULL);
            """
        )
        con.execute("INSERT INTO fixtures VALUES (1, 39, 2026, ?, '2026-09-30T10:00:00+00:00', '2026-09-30T18:00:00+00:00', NULL)", (kickoff,))
        seen = "2026-09-30T18:00:00+00:00"
        boards = {
            (4, "Pinnacle"): {"Home": 2.00, "Draw": 3.50, "Away": 4.00},
            (8, "Bet365"): {"Home": 2.18, "Draw": 3.40, "Away": 3.90},
        }
        for (book_id, book), prices in boards.items():
            con.execute("INSERT INTO observations VALUES ('s1', 1, ?, ?, NULL)", (book_id, seen))
            for selection, odd in prices.items():
                con.execute(
                    "INSERT INTO quotes VALUES (1, ?, ?, 1, 'Match Winner', ?, ?, NULL, ?, 's1')", (book_id, book, selection, odd, seen)
                )
        # a later board before kickoff (the close): Bet365 is seen again unchanged (no new quote rows), Pinnacle moved
        later = "2026-09-30T19:30:00+00:00"
        for book_id in (4, 8):
            con.execute("INSERT INTO observations VALUES ('s2', 1, ?, ?, NULL)", (book_id, later))
        for selection, odd in {"Home": 2.05, "Draw": 3.45, "Away": 3.85}.items():
            con.execute("INSERT INTO quotes VALUES (1, 4, 'Pinnacle', 1, 'Match Winner', ?, ?, NULL, ?, 's2')", (selection, odd, later))
    directory = tmp_path / "directory.sqlite3"
    with sqlite3.connect(directory) as con:
        con.execute(
            "CREATE TABLE fixtures (fixture_id INTEGER, league_id INTEGER, league_name TEXT, country TEXT, season INTEGER, round TEXT, "
            "kickoff_utc TEXT, status_short TEXT, home_team_id INTEGER, home_team TEXT, away_team_id INTEGER, away_team TEXT, "
            "home_goals INTEGER, away_goals INTEGER, ht_home_goals INTEGER, ht_away_goals INTEGER)"
        )
        con.execute(
            "INSERT INTO fixtures VALUES (1, 39, 'Premier League', 'England', 2026, 'R7', ?, 'FT', 10, 'Arsenal', 11, 'Spurs', 2, 0, 1, 0)",
            (kickoff,),
        )
        con.execute("CREATE TABLE fixture_stats (fixture_id INTEGER, team_id INTEGER, side TEXT, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER)")
        con.execute(
            "CREATE TABLE fixture_stats_half (fixture_id INTEGER, team_id INTEGER, side TEXT, half TEXT, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER)"
        )
    return odds, directory


def test_daily_scan_finds_the_value_leg_then_settles_it_and_measures_clv(tmp_path) -> None:
    from model_buk.scanner.engine import run_daily_scan
    from model_buk.scanner.register import ScannerRegister

    odds, directory = _synthetic_archives(tmp_path)
    register_path = tmp_path / "scanner.sqlite"
    common = dict(
        odds_db=odds,
        directory_db=directory,
        history_db=tmp_path / "no-history.sqlite",
        prematch_db=tmp_path / "no-prematch.sqlite",
        register_path=register_path,
        out_dir=tmp_path / "out",
    )

    first = run_daily_scan(as_of="2026-09-30T18:30:00Z", **common)
    assert first.recorded
    picks = first.picks
    assert list(picks["bookmaker"]) == ["Bet365"] and list(picks["selection"]) == ["home"]
    assert set(first.legs["bookmaker"]) == {"Bet365"}  # Pinnacle is a price source only: no leg is produced there
    assert "Arsenal" in first.html and "Premier League" in first.html  # names from the fixture directory
    assert (tmp_path / "out" / "latest.html").exists()

    register = ScannerRegister(register_path)
    assert len(register.paper_legs()) == 1

    later = run_daily_scan(as_of="2026-09-30T23:00:00Z", **common)  # the match is over
    assert later.settled >= 1 and later.clv_rows == 1
    settled = register.latest_settlements().set_index("leg_key")["outcome"]
    assert settled["1|1x2|ft|na|home"] == "win"  # Arsenal won 2:0
    clv = register.latest_clv().iloc[0]
    assert clv["odd_taken"] == 2.18 and 0.0 < clv["closing_p_fair"] < 1.0
    assert clv["clv_raw"] == pytest.approx(2.18 * clv["closing_p_fair"] - 1)
    summary = register.paper_summary(now="2026-09-30T23:00:00Z").set_index(["kind", "window_days"])
    assert summary.loc[("single", 7), "n_settled"] == 1 and summary.loc[("single", 7), "hit_rate"] == 1.0
    assert summary.loc[("single", 7), "yield_t088"] == pytest.approx(0.88 * 2.18 - 1)


def test_daily_scan_without_fixture_names_still_renders(tmp_path) -> None:
    from model_buk.scanner.engine import run_daily_scan

    odds, _ = _synthetic_archives(tmp_path)
    result = run_daily_scan(
        as_of="2026-09-30T18:30:00Z",
        odds_db=odds,
        directory_db=tmp_path / "missing-directory.sqlite3",
        history_db=tmp_path / "missing-history.sqlite",
        prematch_db=tmp_path / "missing-prematch.sqlite3",
        register_path=tmp_path / "scanner.sqlite",
        out_dir=None,
    )
    assert "Gospodarz #1" in result.html  # a fallback name, not a crash
    assert result.html_paths == []


def test_refund_share_of_a_lined_market_without_a_line_is_unknown() -> None:
    out = refunds(TOTAL_LADDER, [leg("ou", np.nan, "over")])
    assert all(math.isnan(v) for v in out.values())


def test_refund_share_prefers_the_covering_side_where_both_sides_are_priced() -> None:
    # the two sides of the -0.5 line disagree (0.50 and 0.60 for home / away): the home rows decide
    rows = ladder_fair("ah", "home", {-0.5: 0.50, -1.5: 0.25}) + ladder_fair("ah", "away", {-0.5: 0.60, -1.5: 0.90})
    out = refunds(rows, [leg("ah", -1.0, "home")])
    assert out[("ah", -1.0, "home")] == pytest.approx(0.25)  # 0.50 - 0.25, not 0.40 - 0.10


def test_the_coupon_card_shows_the_win_share_not_the_break_even_probability() -> None:
    assert report_html._win_share(0.6, 0.25) == pytest.approx(0.45)
    assert report_html._win_share(0.6, float("nan")) == pytest.approx(0.6)
    assert math.isnan(report_html._win_share(float("nan"), 0.1))


def test_a_book_shift_exactly_at_the_limit_is_not_suspect() -> None:
    config = cfg(max_edge=1.0, max_book_shift=0.125)
    legs = legs_for(ou_pair(2.0, 2.0), ou_fair(0.625), config=config).set_index("selection")
    assert legs.loc["over", "book_shift"] == -0.125  # 0.5 - 0.625, exact in binary
    assert legs.loc["over", "leg_class"] == "value"
    tighter = legs_for(ou_pair(2.0, 2.0), ou_fair(0.625), config=cfg(max_edge=1.0, max_book_shift=0.12)).set_index("selection")
    assert tighter.loc["over", "leg_class"] == "suspect"
