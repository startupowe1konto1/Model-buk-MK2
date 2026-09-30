"""Fair prices of the scanner: de-vig, source order, derived legs, uncertainty."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from model_buk.ingestion.api_football_odds_archive import OddsArchive
from model_buk.markets.pricing import devig_probabilities
from model_buk.scanner.fair import (
    DEFAULT_SE_MODEL,
    FAIR_COLUMNS,
    FitTarget,
    SeModel,
    betfair_net_odds,
    devig_set,
    fair_candidates,
    fair_prices,
    fit_score_matrix,
    leg_masks,
    matrix_leg_probability,
    overround,
    score_matrix,
)
from model_buk.scanner.markets import load_closing_quotes, load_current_quotes

NOW = pd.Timestamp("2026-10-03T09:00:00Z")
KICKOFF = NOW + pd.Timedelta(hours=5)


def quote_rows(fixture, bookmaker, market, period, line, prices, *, age=10.0, three_way=False):
    """One complementary set as quote rows: ``prices`` maps selection -> odd."""
    return [
        {
            "fixture_id": fixture,
            "bookmaker": bookmaker,
            "market": market,
            "period": period,
            "line": line,
            "selection": selection,
            "odd": odd,
            "kickoff_utc": KICKOFF,
            "fetched_at": NOW - pd.Timedelta(minutes=age),
            "age_minutes": age,
            "three_way": three_way,
        }
        for selection, odd in prices.items()
    ]


def frame(*groups):
    return pd.DataFrame([row for group in groups for row in group])


def pick(fair, market, selection, period="ft", line=float("nan"), fixture=1):
    mask = (fair["fixture_id"] == fixture) & (fair["market"] == market) & (fair["period"] == period) & (fair["selection"] == selection)
    mask &= fair["line"].isna() if math.isnan(line) else np.isclose(fair["line"], line)
    rows = fair[mask]
    assert len(rows) == 1, f"expected one row for {market} {selection} {period} {line}, got {len(rows)}"
    return rows.iloc[0]


# --------------------------------------------------------------------------- #
# De-vig of one set
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
@pytest.mark.parametrize(
    ("market", "odds"),
    [("1x2", [2.10, 3.40, 3.60]), ("ou", [1.87, 1.98]), ("btts", [1.72, 2.10]), ("ah", [1.52, 2.47]), ("dnb", [1.55, 2.45])],
)
def test_devig_sums_to_one_and_matches_the_pricing_module(market, odds, method):
    fair = devig_set(market, odds, method)
    assert sum(fair) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 < p < 1.0 for p in fair)
    assert fair == pytest.approx(devig_probabilities(odds, method))
    assert overround(market, odds) > 1.0


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
def test_double_chance_devig_sums_to_two(method):
    odds = [1.25, 1.66, 1.27]  # 1x, x2, 12
    fair = devig_set("dc", odds, method)
    assert sum(fair) == pytest.approx(2.0, abs=1e-9)
    assert all(0.0 < p < 1.0 for p in fair)
    assert overround("dc", odds) == pytest.approx(sum(1 / o for o in odds) / 2)
    if method == "proportional":
        raw = [1 / o for o in odds]
        assert fair == pytest.approx([2 * r / sum(raw) for r in raw])


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="unknown de-vig method"):
        devig_set("1x2", [2.0, 3.4, 3.6], "nope")
    with pytest.raises(ValueError, match="unknown de-vig method"):
        fair_prices(frame(quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.9, "under": 1.95})), method="nope")


def test_betfair_net_odds_takes_commission_off_the_winnings():
    assert betfair_net_odds(3.0, 0.05) == pytest.approx(1 + 2.0 * 0.95)
    assert betfair_net_odds(3.0, 0.0) == 3.0
    with pytest.raises(ValueError):
        betfair_net_odds(3.0, 1.0)


# --------------------------------------------------------------------------- #
# Pinnacle first
# --------------------------------------------------------------------------- #


def test_pinnacle_complete_group_is_devigged_with_the_power_method():
    quotes = frame(quote_rows(1, "Pinnacle", "1x2", "ft", np.nan, {"home": 2.10, "draw": 3.40, "away": 3.60}))
    fair = fair_prices(quotes, as_of=NOW)
    assert list(fair.columns) == list(FAIR_COLUMNS)
    expected = devig_probabilities([2.10, 3.40, 3.60], "power")
    for selection, p in zip(["home", "draw", "away"], expected, strict=True):
        row = pick(fair, "1x2", selection)
        assert row["p_fair"] == pytest.approx(p)
        assert row["fair_source"] == "pinnacle" and row["devig_method"] == "power" and row["n_books"] == 1
        assert row["book_margin"] == pytest.approx(1 / 2.10 + 1 / 3.40 + 1 / 3.60 - 1)
        assert row["p_se"] == pytest.approx(float(DEFAULT_SE_MODEL.c(5.0)) * math.sqrt(p * (1 - p)))
    assert fair["p_fair"].sum() == pytest.approx(1.0)


@pytest.mark.parametrize("method", ["proportional", "shin", "power"])
def test_method_option_changes_the_de_vig(method):
    quotes = frame(quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.60, "under": 2.45}))
    fair = fair_prices(quotes, method=method, as_of=NOW)
    p_over = pick(fair, "ou", "over", line=2.5)
    assert p_over["p_fair"] == pytest.approx(devig_probabilities([1.60, 2.45], method)[0])
    assert p_over["devig_method"] == method
    assert pick(fair, "ou", "under", line=2.5)["p_fair"] == pytest.approx(1 - p_over["p_fair"])


def test_incomplete_or_improper_groups_get_no_fair_price():
    groups = [
        quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.90}),  # one side only
        quote_rows(1, "Pinnacle", "1x2", "ft", np.nan, {"home": 2.10, "draw": 3.40}),  # no away price
        quote_rows(1, "Pinnacle", "ou", "ft", 3.5, {"over": 2.10, "under": 2.10}),  # overround 0.95: crossed
        quote_rows(1, "Pinnacle", "btts", "ft", np.nan, {"yes": 1.0, "no": 1.9}),  # a price of 1.0 is not a price
        quote_rows(1, "Pinnacle", "ah", "ft", -0.5, {"home": 1.9, "away": 1.95}),  # the one complete group
    ]
    fair = fair_prices(frame(*groups), as_of=NOW)
    assert set(fair["market"]) == {"ah"}
    assert len(fair) == 2


def test_a_set_with_an_uninformative_overround_gets_no_fair_price():
    heavy = frame(quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.20, "under": 1.20}))  # overround 1.67
    assert fair_prices(heavy, as_of=NOW).empty
    fine = frame(quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.80, "under": 1.80}))  # 1.11
    assert len(fair_prices(fine, as_of=NOW)) == 2


def test_empty_input_and_other_only_input_give_an_empty_frame_with_the_contract_columns():
    empty = pd.DataFrame(columns=["fixture_id", "bookmaker", "market", "period", "line", "selection", "odd"])
    for quotes in (empty, frame(quote_rows(1, "Pinnacle", "other", "na", np.nan, {"Some Player": 3.0, "Other": 2.0}))):
        fair = fair_prices(quotes, as_of=NOW)
        assert fair.empty and list(fair.columns) == list(FAIR_COLUMNS)
    with pytest.raises(ValueError, match="missing columns"):
        fair_prices(pd.DataFrame({"fixture_id": [1], "odd": [2.0]}))


def test_a_leg_quoted_by_one_soft_book_stays_missing():
    quotes = frame(quote_rows(1, "Bet365", "ou", "ft", 2.5, {"over": 1.90, "under": 1.95}))
    assert fair_prices(quotes, as_of=NOW).empty


def test_stale_boards_are_ignored_unless_the_age_filter_is_off():
    stale = frame(quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.90, "under": 1.95}, age=500.0))
    assert fair_prices(stale, as_of=NOW).empty
    assert len(fair_prices(stale, as_of=NOW, max_age_minutes=None)) == 2
    assert len(fair_prices(stale, as_of=NOW, max_age_minutes=600.0)) == 2


def test_three_way_total_rows_are_not_used_as_a_complementary_pair():
    rows = quote_rows(1, "Pinnacle", "corners_ou", "ft", 10.5, {"over": 1.90, "under": 1.95}, three_way=True)
    assert fair_prices(frame(rows), as_of=NOW).empty


# --------------------------------------------------------------------------- #
# Source order, consensus, Betfair
# --------------------------------------------------------------------------- #

SOFT = {
    "Bet365": {"home": 2.05, "draw": 3.30, "away": 3.50},
    "1xBet": {"home": 2.10, "draw": 3.25, "away": 3.40},
    "Marathonbet": {"home": 2.00, "draw": 3.35, "away": 3.60},
    "Betano": {"home": 2.08, "draw": 3.20, "away": 3.55},
}


def one_x_two(book, prices):
    return quote_rows(1, book, "1x2", "ft", np.nan, prices)


def test_pinnacle_beats_every_other_source_and_keep_all_returns_them_all():
    quotes = frame(
        one_x_two("Pinnacle", {"home": 2.12, "draw": 3.40, "away": 3.60}),
        *(one_x_two(book, prices) for book, prices in list(SOFT.items())[:3]),
    )
    best = fair_prices(quotes, as_of=NOW)
    assert (best["fair_source"] == "pinnacle").all() and len(best) == 3
    everything = fair_prices(quotes, as_of=NOW, keep_all=True)
    assert set(everything["fair_source"]) == {"pinnacle", "consensus"} and len(everything) == 6
    assert (everything.loc[everything["fair_source"] == "consensus", "n_books"] == 3).all()  # the three soft books only
    assert not best.duplicated(["fixture_id", "market", "period", "line", "selection"]).any()


def test_consensus_is_the_median_of_the_de_vigged_books_and_needs_three_of_them():
    quotes = frame(*(one_x_two(book, prices) for book, prices in list(SOFT.items())[:3]))
    fair = fair_prices(quotes, as_of=NOW)
    per_book = np.array([devig_probabilities(list(p.values()), "power") for p in list(SOFT.values())[:3]])
    median = np.median(per_book, axis=0)
    median = median / median.sum()
    for selection, expected in zip(["home", "draw", "away"], median, strict=True):
        row = pick(fair, "1x2", selection)
        assert row["p_fair"] == pytest.approx(expected)
        assert row["fair_source"] == "consensus" and row["n_books"] == 3
    assert fair["p_fair"].sum() == pytest.approx(1.0)
    two_books = frame(*(one_x_two(book, prices) for book, prices in list(SOFT.items())[:2]))
    assert fair_prices(two_books, as_of=NOW).empty
    assert len(fair_prices(two_books, as_of=NOW, min_consensus_books=2)) == 3


def test_consensus_medians_from_different_books_are_renormalised_to_one():
    # zero-margin boards, so the de-vig is the identity; the home median comes from book C, the draw and away ones from A/B
    boards = {
        "BookA": {"home": 1 / 0.50, "draw": 1 / 0.25, "away": 1 / 0.25},
        "BookB": {"home": 1 / 0.40, "draw": 1 / 0.35, "away": 1 / 0.25},
        "BookC": {"home": 1 / 0.45, "draw": 1 / 0.20, "away": 1 / 0.35},
    }
    fair = fair_prices(frame(*(one_x_two(book, prices) for book, prices in boards.items())), method="proportional", as_of=NOW)
    medians = {"home": 0.45, "draw": 0.25, "away": 0.25}  # sum 0.95
    for selection, median in medians.items():
        assert pick(fair, "1x2", selection)["p_fair"] == pytest.approx(median / 0.95)
    assert fair["p_fair"].sum() == pytest.approx(1.0)


def test_an_improper_pinnacle_group_falls_through_to_the_consensus_without_pinnacle():
    quotes = frame(
        quote_rows(1, "Pinnacle", "1x2", "ft", np.nan, {"home": 2.10, "draw": 3.40}),  # incomplete
        *(one_x_two(book, prices) for book, prices in list(SOFT.items())[:3]),
    )
    fair = fair_prices(quotes, as_of=NOW)
    assert (fair["fair_source"] == "consensus").all() and (fair["n_books"] == 3).all()


def test_lines_that_differ_by_float_noise_are_the_same_line():
    quotes = frame(
        quote_rows(1, "Bet365", "ou", "ft", 2.5, {"over": 1.90, "under": 1.95}),
        quote_rows(1, "1xBet", "ou", "ft", 2.5 + 1e-11, {"over": 1.92, "under": 1.93}),
        quote_rows(1, "Marathonbet", "ou", "ft", 2.5 - 1e-11, {"over": 1.88, "under": 1.97}),
    )
    fair = fair_prices(quotes, as_of=NOW)
    assert len(fair) == 2 and (fair["n_books"] == 3).all() and (fair["fair_source"] == "consensus").all()


def test_consensus_of_double_chance_sums_to_two():
    boards = [{"1x": 1.28, "x2": 1.68, "12": 1.30}, {"1x": 1.30, "x2": 1.62, "12": 1.31}, {"1x": 1.27, "x2": 1.70, "12": 1.29}]
    quotes = frame(*(quote_rows(1, f"Book{i}", "dc", "ft", np.nan, prices) for i, prices in enumerate(boards)))
    fair = fair_prices(quotes, as_of=NOW)
    assert len(fair) == 3 and fair["p_fair"].sum() == pytest.approx(2.0)
    assert (fair["fair_source"] == "consensus").all()


def test_betfair_is_a_single_book_fallback_and_joins_the_consensus_pool():
    board = {"home": 2.05, "draw": 3.30, "away": 3.50}
    alone = frame(one_x_two("Betfair", board), one_x_two("Bet365", SOFT["Bet365"]))
    fair = fair_prices(alone, as_of=NOW)
    assert (fair["fair_source"] == "betfair").all() and (fair["n_books"] == 1).all()
    assert pick(fair, "1x2", "home")["p_fair"] == pytest.approx(devig_probabilities(list(board.values()), "power")[0])
    pooled = frame(one_x_two("Betfair", board), one_x_two("Bet365", SOFT["Bet365"]), one_x_two("1xBet", SOFT["1xBet"]))
    assert (fair_prices(pooled, as_of=NOW)["fair_source"] == "consensus").all()


def test_betfair_commission_is_taken_off_before_the_de_vig():
    board = {"home": 2.05, "draw": 3.30, "away": 3.50}
    quotes = frame(one_x_two("Betfair", board), one_x_two("Bet365", SOFT["Bet365"]))
    plain = fair_prices(quotes, as_of=NOW)
    taxed = fair_prices(quotes, as_of=NOW, betfair_commission=0.05)
    net = [float(betfair_net_odds(o, 0.05)) for o in board.values()]
    assert pick(taxed, "1x2", "home")["p_fair"] == pytest.approx(devig_probabilities(net, "power")[0])
    assert pick(taxed, "1x2", "away")["p_fair"] != pytest.approx(pick(plain, "1x2", "away")["p_fair"], abs=1e-6)
    assert taxed["p_fair"].sum() == pytest.approx(1.0)
    # the reported margin is that of the quoted prices
    assert pick(taxed, "1x2", "home")["book_margin"] == pytest.approx(sum(1 / o for o in board.values()) - 1)


def test_correct_score_is_never_priced_from_bookmaker_boards():
    scores = {f"{h}:{a}": 6.0 + h + a for h in range(4) for a in range(4)}
    quotes = frame(*(quote_rows(1, book, "cs", "ft", np.nan, scores) for book in ("Bet365", "1xBet", "Marathonbet", "Betano")))
    assert fair_prices(quotes, as_of=NOW).empty


# --------------------------------------------------------------------------- #
# Derived legs
# --------------------------------------------------------------------------- #


def test_double_chance_and_draw_no_bet_are_exact_functions_of_the_pinnacle_1x2():
    prices = {"home": 2.10, "draw": 3.40, "away": 3.60}
    quotes = frame(
        one_x_two("Pinnacle", prices),
        quote_rows(1, "Bet365", "dc", "ft", np.nan, {"1x": 1.30, "x2": 1.75, "12": 1.33}),
        quote_rows(1, "Bet365", "dnb", "ft", np.nan, {"home": 1.55, "away": 2.45}),
        # three other books quote double chance: the exact algebra still wins over their consensus
        quote_rows(1, "1xBet", "dc", "ft", np.nan, {"1x": 1.31, "x2": 1.72, "12": 1.34}),
        quote_rows(1, "Betano", "dc", "ft", np.nan, {"1x": 1.29, "x2": 1.74, "12": 1.32}),
    )
    fair = fair_prices(quotes, as_of=NOW)
    h, d, a = devig_probabilities(list(prices.values()), "power")
    assert pick(fair, "dc", "1x")["p_fair"] == pytest.approx(h + d, abs=1e-12)
    assert pick(fair, "dc", "x2")["p_fair"] == pytest.approx(d + a, abs=1e-12)
    assert pick(fair, "dc", "12")["p_fair"] == pytest.approx(h + a, abs=1e-12)
    assert pick(fair, "dnb", "home")["p_fair"] == pytest.approx(h / (h + a))
    assert pick(fair, "dnb", "away")["p_fair"] == pytest.approx(a / (h + a))
    row = pick(fair, "dc", "1x")
    assert row["fair_source"] == "pinnacle_derived" and row["n_books"] == 1 and math.isnan(row["book_margin"])
    assert row["p_se"] == pytest.approx(float(DEFAULT_SE_MODEL.c(5.0)) * math.sqrt(row["p_fair"] * (1 - row["p_fair"])))


def test_leg_probability_of_a_quarter_line_is_the_stake_weighted_break_even():
    rng = np.random.default_rng(0)
    matrix = rng.random((16, 16))
    matrix /= matrix.sum()
    x, y = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")
    total = x + y
    p = lambda mask: float(matrix[mask].sum())  # noqa: E731

    # integer line: the push is refunded and drops out of the denominator
    assert matrix_leg_probability(matrix, "ou", "over", 2.0) == pytest.approx(p(total >= 3) / (1 - p(total == 2)))
    # quarter line 2.25 = half on 2.0 (push at 2) and half on 2.5
    expected = p(total >= 3) / (p(total >= 3) + 0.5 * p(total <= 1) + 0.5 * p(total <= 2))
    assert matrix_leg_probability(matrix, "ou", "over", 2.25) == pytest.approx(expected)
    # a half line is a plain win probability and its two sides are complements
    assert matrix_leg_probability(matrix, "ou", "over", 2.5) == pytest.approx(p(total >= 3))
    for market, sel_a, sel_b, line in [("ou", "over", "under", 2.25), ("ou", "over", "under", 3.0), ("ah", "home", "away", -0.25), ("ah", "home", "away", 0.75)]:
        assert matrix_leg_probability(matrix, market, sel_a, line) + matrix_leg_probability(matrix, market, sel_b, line) == pytest.approx(1.0)
    margin = x - y
    assert matrix_leg_probability(matrix, "ah", "home", -0.5) == pytest.approx(p(margin >= 1))
    assert matrix_leg_probability(matrix, "ah", "home", 0.5) == pytest.approx(p(margin >= 0))
    assert matrix_leg_probability(matrix, "ah", "home", 0.0) == pytest.approx(p(margin >= 1) / (1 - p(margin == 0)))
    # home -0.25: half on 0 (a draw refunds), half on -0.5 (a draw loses)
    expected = p(margin >= 1) / (p(margin >= 1) + 0.5 * p(margin <= -1) + 0.5 * p(margin <= 0))
    assert matrix_leg_probability(matrix, "ah", "home", -0.25) == pytest.approx(expected)
    assert matrix_leg_probability(matrix, "btts", "yes") == pytest.approx(p((x > 0) & (y > 0)))
    assert matrix_leg_probability(matrix, "cs", "2:1") == pytest.approx(matrix[2, 1])
    assert matrix_leg_probability(matrix, "team_ou_home", "over", 1.5) == pytest.approx(p(x >= 2))
    assert math.isnan(matrix_leg_probability(matrix, "corners_ou", "over", 9.5))
    assert leg_masks("ou", "sideways", 2.5, 16) is None


def test_leg_masks_agree_with_the_settlement_of_every_score():
    """The break-even masks and ``settle_leg`` are two implementations of one rulebook (lines, pushes, AH sign)."""
    from model_buk.scanner.settlement import settle_leg

    price = 2.7
    profit = {"win": price - 1, "loss": -1.0, "push": 0.0, "half_win": (price - 1) / 2, "half_loss": -0.5}
    cases = [("1x2", s, math.nan) for s in ("home", "draw", "away")] + [("dc", s, math.nan) for s in ("1x", "x2", "12")]
    cases += [("dnb", s, math.nan) for s in ("home", "away")] + [("btts", s, math.nan) for s in ("yes", "no")] + [("cs", "2:1", math.nan)]
    cases += [("ah", s, line) for s in ("home", "away") for line in np.arange(-2.5, 2.51, 0.25)]
    cases += [(m, s, line) for m in ("ou", "team_ou_home", "team_ou_away") for s in ("over", "under") for line in np.arange(0.5, 4.01, 0.25)]
    for market, selection, line in cases:
        numerator, denominator = leg_masks(market, selection, float(line), 16)
        for home in range(6):
            for away in range(6):
                outcome = settle_leg(market, "ft", line, selection, {"home_goals": home, "away_goals": away})
                assert price * numerator[home, away] - denominator[home, away] == pytest.approx(profit[outcome]), (market, selection, line, home, away)


def test_score_matrix_is_a_pmf_with_the_requested_means():
    for kwargs in ({}, {"rho": -0.08}, {"common": 0.2}, {"rho": 0.05, "common": 0.1}):
        matrix = score_matrix(1.6, 1.1, **kwargs)
        assert matrix.sum() == pytest.approx(1.0)
        assert (matrix >= 0).all()
    plain = score_matrix(1.6, 1.1)
    goals = np.arange(plain.shape[0])
    assert (plain.sum(axis=1) * goals).sum() == pytest.approx(1.6, abs=1e-6)
    assert (plain.sum(axis=0) * goals).sum() == pytest.approx(1.1, abs=1e-6)
    shocked = score_matrix(1.6, 1.1, common=0.3)
    assert (shocked.sum(axis=1) * goals).sum() == pytest.approx(1.6, abs=1e-4)
    with pytest.raises(ValueError):
        score_matrix(0.0, 1.0)


def test_dixon_coles_correction_multiplies_the_four_low_score_cells():
    rho, l1, l2 = -0.08, 1.6, 1.1
    plain, corrected = score_matrix(l1, l2), score_matrix(l1, l2, rho=rho)
    ratio = corrected / plain  # each cell's factor divided by the common normaliser
    assert ratio[0, 0] / ratio[3, 3] == pytest.approx(1 - l1 * l2 * rho)
    assert ratio[0, 1] / ratio[3, 3] == pytest.approx(1 + l1 * rho)  # home 0, away 1: the HOME rate scales it
    assert ratio[1, 0] / ratio[3, 3] == pytest.approx(1 + l2 * rho)
    assert ratio[1, 1] / ratio[3, 3] == pytest.approx(1 - rho)
    assert ratio[2, 2] == pytest.approx(ratio[3, 3]) and ratio[0, 2] == pytest.approx(ratio[3, 1])  # nothing else moves


def test_second_half_matrix_needs_positive_second_half_means():
    from model_buk.scanner.fair import ScoreMatrixFit, second_half_matrix

    def fit(mu_home, mu_away):
        return ScoreMatrixFit(score_matrix(mu_home, mu_away), mu_home, mu_away, 0.0, 0.0, 0.0, 0.0, 5, "poisson")

    assert second_half_matrix(fit(0.5, 0.5), fit(0.8, 0.8)) is None  # the first half cannot have more goals than the match
    assert second_half_matrix(fit(1.6, 1.1), fit(0.7, 0.5)) is not None


TRUTH = score_matrix(1.6, 1.1, rho=-0.06)
OU_LINES = (1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5)
AH_LINES = (-1.0, -0.5, 0.0, 0.5, 1.0)


def pinnacle_board(matrix, *, fixture=1, period="ft", margin=0.04, ou_lines=OU_LINES, ah_lines=AH_LINES, team_lines=(0.5, 1.5)):
    groups = []
    q = [matrix_leg_probability(matrix, "1x2", s) for s in ("home", "draw", "away")]
    groups.append(quote_rows(fixture, "Pinnacle", "1x2", period, np.nan, dict(zip(("home", "draw", "away"), 1 / (np.array(q) * (1 + margin))))))
    for line in ou_lines:
        q = matrix_leg_probability(matrix, "ou", "over", line)
        groups.append(quote_rows(fixture, "Pinnacle", "ou", period, line, {"over": 1 / (q * (1 + margin)), "under": 1 / ((1 - q) * (1 + margin))}))
    for line in ah_lines:
        q = matrix_leg_probability(matrix, "ah", "home", line)
        groups.append(quote_rows(fixture, "Pinnacle", "ah", period, line, {"home": 1 / (q * (1 + margin)), "away": 1 / ((1 - q) * (1 + margin))}))
    for market in ("team_ou_home", "team_ou_away"):
        for line in team_lines:
            q = matrix_leg_probability(matrix, market, "over", line)
            groups.append(quote_rows(fixture, "Pinnacle", market, period, line, {"over": 1 / (q * (1 + margin)), "under": 1 / ((1 - q) * (1 + margin))}))
    return groups


def test_fit_recovers_the_parameters_of_a_matrix_it_was_generated_from():
    targets = []
    for selection in ("home", "draw", "away"):
        targets.append(FitTarget("1x2", selection, float("nan"), matrix_leg_probability(TRUTH, "1x2", selection)))
    for line in OU_LINES:
        targets.append(FitTarget("ou", "over", line, matrix_leg_probability(TRUTH, "ou", "over", line)))
    for line in AH_LINES:
        targets.append(FitTarget("ah", "home", line, matrix_leg_probability(TRUTH, "ah", "home", line)))
    fit = fit_score_matrix(targets)
    assert fit is not None and fit.n_targets == len(targets)
    assert fit.mu_home == pytest.approx(1.6, abs=0.02) and fit.mu_away == pytest.approx(1.1, abs=0.02)
    assert fit.rho == pytest.approx(-0.06, abs=0.02)
    assert fit.rmse < 1e-4
    assert np.abs(fit.matrix - TRUTH).max() < 1e-3
    # not identified without a total or without a result/handicap price
    assert fit_score_matrix(targets[:3]) is None
    assert fit_score_matrix([t for t in targets if t.market == "ou"]) is None
    assert fit_score_matrix(targets, model="poisson") is not None
    assert fit_score_matrix(targets, model="bivariate") is not None
    with pytest.raises(ValueError):
        fit_score_matrix(targets, model="nope")


def test_derived_legs_are_consistent_with_the_fitted_matrix():
    soft = [
        quote_rows(1, "Bet365", "btts", "ft", np.nan, {"yes": 1.80, "no": 2.00}),
        quote_rows(1, "Bet365", "cs", "ft", np.nan, {"2:1": 11.0, "0:0": 9.5, "1:1": 6.5}),
        quote_rows(1, "Bet365", "team_ou_home", "ft", 2.5, {"over": 3.4, "under": 1.3}),
        quote_rows(1, "Bet365", "ou", "ft", 3.25, {"over": 2.5, "under": 1.5}),
        quote_rows(1, "Bet365", "ou", "ft", 2.5, {"over": 1.9, "under": 1.9}),  # quoted by Pinnacle too: Pinnacle wins
        quote_rows(1, "Bet365", "dc", "ft", np.nan, {"1x": 1.3, "x2": 1.7, "12": 1.3}),
    ]
    quotes = frame(*pinnacle_board(TRUTH), *soft)
    fair = fair_prices(quotes, method="proportional", as_of=NOW)
    derived = fair[fair["fair_source"] == "pinnacle_derived"]

    def truth(market, selection, line=float("nan")):
        return matrix_leg_probability(TRUTH, market, selection, line)

    assert pick(fair, "btts", "yes")["p_fair"] == pytest.approx(truth("btts", "yes"), abs=2e-3)
    assert pick(fair, "btts", "no")["p_fair"] == pytest.approx(truth("btts", "no"), abs=2e-3)
    assert pick(fair, "btts", "yes")["p_fair"] + pick(fair, "btts", "no")["p_fair"] == pytest.approx(1.0)
    for score in ("2:1", "0:0", "1:1"):
        assert pick(fair, "cs", score)["p_fair"] == pytest.approx(TRUTH[int(score[0]), int(score[2])], abs=1e-3)
    assert pick(fair, "team_ou_home", "over", line=2.5)["p_fair"] == pytest.approx(truth("team_ou_home", "over", 2.5), abs=2e-3)
    assert pick(fair, "ou", "over", line=3.25)["p_fair"] == pytest.approx(truth("ou", "over", 3.25), abs=2e-3)
    assert pick(fair, "ou", "over", line=2.5)["fair_source"] == "pinnacle"
    assert pick(fair, "ou", "over", line=2.5)["p_fair"] == pytest.approx(truth("ou", "over", 2.5), abs=1e-6)
    assert not derived.empty and (derived["n_books"] == 1).all() and derived["book_margin"].isna().all()
    # only quoted legs are derived: no correct score that nobody quotes
    assert set(derived.loc[derived["market"] == "cs", "selection"]) == {"2:1", "0:0", "1:1"}
    # the matrix costs certainty: wider than a direct Pinnacle price at the same probability
    btts = pick(fair, "btts", "yes")
    direct_se = float(DEFAULT_SE_MODEL.c(5.0)) * math.sqrt(btts["p_fair"] * (1 - btts["p_fair"]))
    assert btts["p_se"] > direct_se
    # double chance from the matrix-free algebra agrees with the matrix
    assert pick(fair, "dc", "1x")["p_fair"] == pytest.approx(truth("dc", "1x"), abs=2e-3)


def test_second_half_legs_come_from_the_full_time_and_first_half_matrices():
    full = score_matrix(1.6, 1.1)
    first = score_matrix(0.7, 0.5)
    second = score_matrix(0.9, 0.6)
    quotes = frame(
        *pinnacle_board(full),
        *pinnacle_board(first, period="1h", ou_lines=(0.5, 0.75, 1.0, 1.5), ah_lines=(-0.5, 0.0, 0.5), team_lines=(0.5,)),
        quote_rows(1, "Bet365", "1x2", "2h", np.nan, {"home": 2.9, "draw": 2.6, "away": 4.4}),
        quote_rows(1, "Bet365", "btts", "2h", np.nan, {"yes": 2.7, "no": 1.45}),
        quote_rows(1, "Bet365", "ou", "2h", 1.5, {"over": 2.2, "under": 1.65}),
    )
    fair = fair_prices(quotes, method="proportional", as_of=NOW)
    for selection in ("home", "draw", "away"):
        row = pick(fair, "1x2", selection, period="2h")
        assert row["p_fair"] == pytest.approx(matrix_leg_probability(second, "1x2", selection), abs=6e-3)
        assert row["fair_source"] == "pinnacle_derived"
    assert pick(fair, "btts", "yes", period="2h")["p_fair"] == pytest.approx(matrix_leg_probability(second, "btts", "yes"), abs=6e-3)
    assert pick(fair, "ou", "over", period="2h", line=1.5)["p_fair"] == pytest.approx(matrix_leg_probability(second, "ou", "over", 1.5), abs=6e-3)
    # second-half legs carry the extra halves-independence uncertainty
    row = pick(fair, "btts", "yes", period="2h")
    without_extra = SeModel(extra_second_half=0.0).p_se(np.array([row["p_fair"]]), 5.0, ["derived_matrix"])[0]
    assert row["p_se"] > without_extra


def test_a_consensus_set_with_a_tail_leg_ranks_after_the_score_matrix():
    def soft_pair(line, q, margin):
        return [
            quote_rows(1, book, "ou", "ft", line, {"over": 1 / (q * (1 + margin)), "under": 1 / ((1 - q) * (1 + margin))})
            for book in ("Bet365", "1xBet", "Marathonbet")
        ]

    central = matrix_leg_probability(TRUTH, "ou", "over", 3.25)
    tail = matrix_leg_probability(TRUTH, "ou", "over", 7.5)
    assert 0.2 < central < 0.5 and tail < 0.01
    quotes = frame(*pinnacle_board(TRUTH), *soft_pair(3.25, central, 0.05), *soft_pair(7.5, tail, 0.0))
    fair = fair_prices(quotes, method="proportional", as_of=NOW)
    assert pick(fair, "ou", "over", line=3.25)["fair_source"] == "consensus"  # central: the books' median is used
    assert pick(fair, "ou", "over", line=7.5)["fair_source"] == "pinnacle_derived"  # tail: the matrix is
    assert pick(fair, "ou", "under", line=7.5)["fair_source"] == "pinnacle_derived"  # the whole set moves together
    without_matrix = fair_prices(frame(*soft_pair(7.5, tail, 0.0)), method="proportional", as_of=NOW)
    assert (without_matrix["fair_source"] == "consensus").all()  # nothing better exists: the tail consensus stays


def test_a_stale_pinnacle_line_keeps_its_price_but_gets_a_wide_standard_error():
    groups = pinnacle_board(TRUTH)
    # replace the +1.0 handicap by a copy of the +0.5 prices: the provider's stale-line pattern
    stale_line = 1.0
    original = [g for g in groups if g[0]["market"] == "ah" and g[0]["line"] == 0.5][0]
    groups = [g for g in groups if not (g[0]["market"] == "ah" and g[0]["line"] == stale_line)]
    groups.append([{**row, "line": stale_line} for row in original])
    quotes = frame(*groups)
    fair = fair_prices(quotes, method="proportional", as_of=NOW)
    stale = pick(fair, "ah", "home", line=stale_line)
    fresh = pick(fair, "ah", "home", line=0.5)
    assert stale["fair_source"] == "pinnacle" and stale["p_fair"] == pytest.approx(fresh["p_fair"], abs=1e-9)
    assert stale["p_se"] > 3 * fresh["p_se"]
    switched_off = fair_prices(quotes, method="proportional", as_of=NOW, coherence=False)
    assert pick(switched_off, "ah", "home", line=stale_line)["p_se"] == pytest.approx(fresh["p_se"], rel=0.3)


def test_candidates_expose_every_source_with_its_kind():
    quotes = frame(
        *pinnacle_board(TRUTH),
        one_x_two("Bet365", SOFT["Bet365"]),
        one_x_two("1xBet", SOFT["1xBet"]),
        one_x_two("Betano", SOFT["Betano"]),
        quote_rows(1, "Bet365", "dc", "ft", np.nan, {"1x": 1.3, "x2": 1.7, "12": 1.3}),
    )
    candidates = fair_candidates(quotes, method="proportional")
    assert {"pinnacle", "consensus", "derived_exact"} <= set(candidates["kind"])
    assert {"line_key", "abs_err", "second_half"} <= set(candidates.columns)


# --------------------------------------------------------------------------- #
# Uncertainty model
# --------------------------------------------------------------------------- #


def test_drift_scale_grows_with_the_square_root_of_the_time_to_kickoff():
    model = SeModel()
    assert float(model.c(48.0)) == pytest.approx(0.06)
    assert float(model.c(12.0)) == pytest.approx(0.03)
    assert float(model.c(0.5)) == pytest.approx(model.c_min)  # floored
    assert float(model.c(1000.0)) == pytest.approx(model.c_max)  # capped
    assert float(model.c(float("nan"))) == pytest.approx(model.c_ref)  # unknown lead: the reference scale
    leads = np.array([2.0, 6.0, 24.0, 48.0, 96.0])
    assert (np.diff(model.c(leads)) >= 0).all()
    tighter = SeModel(c_ref=0.03)
    assert float(tighter.c(48.0)) == pytest.approx(0.03)


def test_standard_error_reflects_source_tail_and_second_half():
    model = SeModel()
    p = np.array([0.45, 0.45, 0.45, 0.45, 0.05, 0.05])
    sources = ["pinnacle", "consensus", "consensus", "derived_matrix", "pinnacle", "consensus"]
    n_books = np.array([1, 3, 8, 1, 1, 3])
    se = model.p_se(p, 24.0, sources, n_books=n_books)
    assert se[0] == pytest.approx(float(model.c(24.0)) * math.sqrt(0.45 * 0.55))
    assert se[1] > se[2] > se[0]  # more books, less spread; the sharp price is the tightest
    assert se[3] > se[0]
    assert se[5] > se[4]  # a tail of a soft source carries the favourite-longshot term, Pinnacle does not
    # the tail term is explicit: sqrt((c^2 + (0.07 / sqrt(n))^2) p (1 - p) + (0.18 m (1 - 2m))^2) for a consensus leg
    c, n, p_tail = float(model.c(24.0)), 3.0, 0.05
    expected = math.sqrt((c**2 + (0.07 / math.sqrt(n)) ** 2) * p_tail * (1 - p_tail) + (0.18 * p_tail * (1 - 2 * p_tail)) ** 2)
    assert se[5] == pytest.approx(expected)
    exact = model.p_se(np.array([0.45]), 24.0, ["derived_exact"])[0]
    assert exact == pytest.approx(se[0])
    later = model.p_se(np.array([0.45]), 24.0, ["derived_matrix"], second_half=np.array([True]))[0]
    assert later > se[3]
    with_error = model.p_se(np.array([0.45]), 24.0, ["pinnacle"], abs_err=np.array([0.05]))[0]
    assert with_error == pytest.approx(math.sqrt(se[0] ** 2 + 0.05**2))


def test_lead_time_to_kickoff_narrows_p_se():
    rows = quote_rows(1, "Pinnacle", "ou", "ft", 2.5, {"over": 1.9, "under": 1.95})
    near = fair_prices(frame(rows), as_of=KICKOFF - pd.Timedelta(hours=2))
    far = fair_prices(frame(rows), as_of=KICKOFF - pd.Timedelta(hours=40))
    assert (far["p_se"].to_numpy() > near["p_se"].to_numpy()).all()
    custom = fair_prices(frame(rows), as_of=KICKOFF - pd.Timedelta(hours=40), se_model=SeModel(c_ref=0.12))
    assert (custom["p_se"].to_numpy() > far["p_se"].to_numpy()).all()


# --------------------------------------------------------------------------- #
# Against a real archive file: point in time, closing prices
# --------------------------------------------------------------------------- #

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def pinnacle_object(fixture_id, kickoff, home, draw=3.6, away=3.9):
    values = [{"value": "Home", "odd": str(home)}, {"value": "Draw", "odd": str(draw)}, {"value": "Away", "odd": str(away)}]
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": 39, "season": 2026},
        "update": T0.isoformat(),
        "bookmakers": [{"id": 4, "name": "Pinnacle", "bets": [{"id": 1, "name": "Match Winner", "values": values}]}],
    }


def test_fair_prices_use_only_what_was_known_at_the_decision_time(tmp_path):
    archive = OddsArchive(tmp_path)
    kickoff = T0 + timedelta(hours=8)
    archive.record("s1", T0, [pinnacle_object(1, kickoff, 2.10)])
    archive.record("s2", T0 + timedelta(hours=2), [pinnacle_object(1, kickoff, 1.80, 3.9, 4.6)])
    early = fair_prices(load_current_quotes(archive.db_path, T0 + timedelta(hours=1)), as_of=T0 + timedelta(hours=1))
    late = fair_prices(load_current_quotes(archive.db_path, T0 + timedelta(hours=3)), as_of=T0 + timedelta(hours=3))
    assert pick(early, "1x2", "home")["p_fair"] == pytest.approx(devig_probabilities([2.10, 3.6, 3.9], "power")[0])
    assert pick(late, "1x2", "home")["p_fair"] == pytest.approx(devig_probabilities([1.80, 3.9, 4.6], "power")[0])
    assert pick(late, "1x2", "home")["p_fair"] > pick(early, "1x2", "home")["p_fair"]


def test_closing_fair_price_is_the_last_price_before_kickoff(tmp_path):
    archive = OddsArchive(tmp_path)
    kickoff = T0 + timedelta(hours=8)
    archive.record("s1", kickoff - timedelta(hours=6), [pinnacle_object(1, kickoff, 2.20)])
    archive.record("s2", kickoff - timedelta(hours=1), [pinnacle_object(1, kickoff, 1.95, 3.7, 4.2)])
    archive.record("s3", kickoff + timedelta(minutes=5), [pinnacle_object(1, kickoff, 1.10, 8.0, 15.0)])
    closing_quotes = load_closing_quotes(archive.db_path, [1])
    # the close was last seen an hour before kickoff: the default 3-hour age filter keeps it; a tight one drops it
    fair = fair_prices(closing_quotes, max_age_minutes=None)
    assert pick(fair, "1x2", "home")["p_fair"] == pytest.approx(devig_probabilities([1.95, 3.7, 4.2], "power")[0])
    assert fair_prices(closing_quotes, max_age_minutes=30.0).empty
