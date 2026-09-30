from __future__ import annotations

import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from model_buk.markets.pricing import closing_line_value
from model_buk.scanner import settlement as st
from model_buk.scanner.settlement import (
    MatchResult,
    clv,
    clv_columns,
    leg_key,
    load_match_results,
    return_factor,
    settle_coupon,
    settle_frame,
    settle_leg,
    single_profit,
    tau_label,
)

FULL = MatchResult(
    home_goals=2,
    away_goals=1,
    home_goals_1h=0,
    away_goals_1h=1,
    home_corners=7,
    away_corners=4,
    home_corners_1h=3,
    away_corners_1h=1,
    home_cards=1,
    away_cards=3,
    home_cards_1h=0,
    away_cards_1h=2,
)


def result(**kwargs) -> MatchResult:
    return MatchResult(**kwargs)


# --------------------------------------------------------------------------- #
# Result markets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("selection", "expected"),
    [("home", "win"), ("draw", "loss"), ("away", "loss")],
)
def test_1x2_full_time(selection: str, expected: str) -> None:
    assert settle_leg("1x2", "ft", float("nan"), selection, FULL) == expected


def test_1x2_first_and_second_half_use_period_scores() -> None:
    # 1st half 0:1, so the 2nd half is 2:0
    assert settle_leg("1x2", "1h", None, "away", FULL) == "win"
    assert settle_leg("1x2", "1h", None, "home", FULL) == "loss"
    assert settle_leg("1x2", "2h", None, "home", FULL) == "win"
    assert settle_leg("1x2", "2h", None, "draw", FULL) == "loss"


@pytest.mark.parametrize(
    ("home", "away", "selection", "expected"),
    [
        (2, 1, "1x", "win"),
        (1, 1, "1x", "win"),
        (0, 1, "1x", "loss"),
        (0, 1, "x2", "win"),
        (1, 1, "x2", "win"),
        (2, 1, "x2", "loss"),
        (2, 1, "12", "win"),
        (1, 1, "12", "loss"),
    ],
)
def test_double_chance(home: int, away: int, selection: str, expected: str) -> None:
    assert settle_leg("dc", "ft", None, selection, result(home_goals=home, away_goals=away)) == expected


def test_draw_no_bet_pushes_on_a_draw() -> None:
    draw = result(home_goals=1, away_goals=1)
    assert settle_leg("dnb", "ft", None, "home", draw) == "push"
    assert settle_leg("dnb", "ft", None, "away", draw) == "push"
    assert settle_leg("dnb", "ft", None, "home", FULL) == "win"
    assert settle_leg("dnb", "ft", None, "away", FULL) == "loss"


# --------------------------------------------------------------------------- #
# Asian handicap, including quarter lines
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "selection", "home", "away", "expected"),
    [
        # half lines never push
        (-0.5, "home", 1, 0, "win"),
        (-0.5, "home", 0, 0, "loss"),
        (-0.5, "away", 0, 0, "win"),
        (0.5, "away", 1, 0, "loss"),
        # whole lines push
        (-1.0, "home", 2, 1, "push"),
        (-1.0, "away", 2, 1, "push"),
        (-1.0, "home", 3, 1, "win"),
        (0.0, "home", 1, 1, "push"),
        # quarter lines: -0.25 = half on 0 and half on -0.5
        (-0.25, "home", 1, 0, "win"),
        (-0.25, "home", 1, 1, "half_loss"),
        (-0.25, "home", 0, 1, "loss"),
        (-0.25, "away", 1, 1, "half_win"),
        (0.25, "home", 1, 1, "half_win"),
        (0.25, "away", 1, 1, "half_loss"),
        # -0.75 = half on -0.5 and half on -1
        (-0.75, "home", 1, 0, "half_win"),
        (-0.75, "home", 2, 0, "win"),
        (-0.75, "home", 2, 1, "half_win"),
        (-0.75, "away", 2, 1, "half_loss"),
        (-0.75, "home", 1, 1, "loss"),
        (0.75, "home", 0, 1, "half_loss"),
        (0.75, "away", 1, 0, "loss"),
    ],
)
def test_asian_handicap_home_line(line: float, selection: str, home: int, away: int, expected: str) -> None:
    assert settle_leg("ah", "ft", line, selection, result(home_goals=home, away_goals=away)) == expected


def test_asian_handicap_home_and_away_are_complementary() -> None:
    flip = {"win": "loss", "loss": "win", "push": "push", "half_win": "half_loss", "half_loss": "half_win"}
    for line in (-1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0):
        for home in range(5):
            for away in range(5):
                res = result(home_goals=home, away_goals=away)
                assert settle_leg("ah", "ft", line, "away", res) == flip[settle_leg("ah", "ft", line, "home", res)]


def test_first_half_handicap_uses_half_time_score() -> None:
    # half time 0:1
    assert settle_leg("ah", "1h", 0.0, "away", FULL) == "win"
    assert settle_leg("ah", "1h", -0.5, "home", FULL) == "loss"


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "selection", "goals", "expected"),
    [
        (2.5, "over", 3, "win"),
        (2.5, "over", 2, "loss"),
        (2.5, "under", 2, "win"),
        (3.0, "over", 3, "push"),
        (3.0, "under", 3, "push"),
        (3.0, "over", 4, "win"),
        (2.25, "over", 2, "half_loss"),
        (2.25, "over", 3, "win"),
        (2.25, "under", 2, "half_win"),
        (2.75, "over", 3, "half_win"),
        (2.75, "over", 2, "loss"),
        (2.75, "under", 3, "half_loss"),
        (2.75, "under", 2, "win"),
    ],
)
def test_goal_totals(line: float, selection: str, goals: int, expected: str) -> None:
    assert settle_leg("ou", "ft", line, selection, result(home_goals=goals, away_goals=0)) == expected


def test_team_totals_use_the_team_goals_only() -> None:
    assert settle_leg("team_ou_home", "ft", 1.5, "over", FULL) == "win"  # home scored 2
    assert settle_leg("team_ou_away", "ft", 1.5, "over", FULL) == "loss"  # away scored 1
    assert settle_leg("team_ou_away", "ft", 1.0, "under", FULL) == "push"


def test_period_totals() -> None:
    # 1st half 0:1 -> 1 goal, 2nd half 2:0 -> 2 goals
    assert settle_leg("ou", "1h", 0.5, "over", FULL) == "win"
    assert settle_leg("ou", "1h", 1.5, "over", FULL) == "loss"
    assert settle_leg("ou", "2h", 1.5, "over", FULL) == "win"
    assert settle_leg("ou", "2h", 2.0, "over", FULL) == "push"


def test_both_teams_to_score() -> None:
    assert settle_leg("btts", "ft", None, "yes", FULL) == "win"
    assert settle_leg("btts", "ft", None, "no", FULL) == "loss"
    assert settle_leg("btts", "ft", None, "no", result(home_goals=2, away_goals=0)) == "win"
    assert settle_leg("btts", "1h", None, "yes", FULL) == "loss"  # only the away team scored before the break
    assert settle_leg("btts", "2h", None, "no", FULL) == "win"


def test_exact_score() -> None:
    assert settle_leg("cs", "ft", None, "2:1", FULL) == "win"
    assert settle_leg("cs", "ft", None, "1:2", FULL) == "loss"
    assert settle_leg("cs", "1h", None, "0:1", FULL) == "win"
    assert settle_leg("cs", "2h", None, "2:0", FULL) == "win"
    assert settle_leg("cs", "ft", None, "other", FULL) is None


# --------------------------------------------------------------------------- #
# Corners and cards
# --------------------------------------------------------------------------- #


def test_corner_markets() -> None:
    assert settle_leg("corners_ou", "ft", 10.5, "over", FULL) == "win"  # 7 + 4 = 11
    assert settle_leg("corners_ou", "ft", 11.0, "over", FULL) == "push"
    assert settle_leg("corners_home_ou", "ft", 6.5, "over", FULL) == "win"
    assert settle_leg("corners_away_ou", "ft", 4.0, "under", FULL) == "push"
    assert settle_leg("corners_ah", "ft", -2.5, "home", FULL) == "win"  # 7 - 4 = +3
    assert settle_leg("corners_ah", "ft", -3.0, "home", FULL) == "push"
    assert settle_leg("corners_ah", "ft", -3.25, "home", FULL) == "half_loss"
    assert settle_leg("corners_1x2", "ft", None, "home", FULL) == "win"
    assert settle_leg("corners_1x2", "ft", None, "draw", FULL) == "loss"


def test_corner_markets_by_half() -> None:
    assert settle_leg("corners_ou", "1h", 3.5, "over", FULL) == "win"  # 3 + 1 = 4
    assert settle_leg("corners_ou", "2h", 6.5, "over", FULL) == "win"  # 11 - 4 = 7
    assert settle_leg("corners_ou", "2h", 7.0, "under", FULL) == "push"


def test_card_markets() -> None:
    assert settle_leg("cards_ou", "ft", 3.5, "over", FULL) == "win"  # 1 + 3 = 4
    assert settle_leg("cards_ou", "ft", 4.0, "over", FULL) == "push"
    assert settle_leg("cards_home_ou", "ft", 0.5, "over", FULL) == "win"
    assert settle_leg("cards_away_ou", "ft", 3.5, "under", FULL) == "win"
    assert settle_leg("cards_ah", "ft", 0.5, "home", FULL) == "loss"  # 1 - 3 + 0.5 < 0
    assert settle_leg("cards_ah", "ft", 2.25, "home", FULL) == "half_win"  # -2 + 2.0 is a push, -2 + 2.5 a win


# --------------------------------------------------------------------------- #
# Missing data is never guessed
# --------------------------------------------------------------------------- #


def test_missing_statistics_give_none() -> None:
    only_ft = result(home_goals=1, away_goals=0)
    assert settle_leg("1x2", "1h", None, "home", only_ft) is None
    assert settle_leg("1x2", "2h", None, "home", only_ft) is None
    assert settle_leg("corners_ou", "ft", 9.5, "over", only_ft) is None
    assert settle_leg("cards_ou", "ft", 3.5, "over", only_ft) is None
    assert settle_leg("ou", "ft", 2.5, "over", MatchResult()) is None
    assert settle_leg("1x2", "ft", None, "home", {"home_goals": float("nan"), "away_goals": 1}) is None
    assert settle_leg("1x2", "ft", None, "home", pd.Series({"home_goals": None, "away_goals": 1})) is None


def test_line_markets_without_a_line_and_unknown_markets_give_none() -> None:
    assert settle_leg("ou", "ft", float("nan"), "over", FULL) is None
    assert settle_leg("ah", "ft", None, "home", FULL) is None
    assert settle_leg("other", "ft", None, "x", FULL) is None
    assert settle_leg("ou", "3h", 2.5, "over", FULL) is None
    assert settle_leg("ou", "ft", 2.3, "over", FULL) is None  # not a multiple of a quarter
    assert settle_leg("1x2", "ft", None, "nobody", FULL) is None


def test_settlement_accepts_mappings_and_series() -> None:
    mapping = {"home_goals": 2, "away_goals": 1}
    series = pd.Series(mapping)
    assert settle_leg("1x2", "ft", None, "home", mapping) == "win"
    assert settle_leg("1x2", "ft", None, "home", series) == "win"


def test_inconsistent_half_scores_are_not_settled() -> None:
    bad = result(home_goals=1, away_goals=0, home_goals_1h=2, away_goals_1h=0)
    assert settle_leg("ou", "2h", 0.5, "over", bad) is None


def test_settle_frame_matches_scalar_settlement() -> None:
    legs = pd.DataFrame(
        {
            "fixture_id": [1, 1, 2, 3],
            "market": ["1x2", "ou", "ah", "1x2"],
            "period": ["ft", "ft", "ft", "ft"],
            "line": [np.nan, 2.5, -0.25, np.nan],
            "selection": ["home", "over", "home", "home"],
        }
    )
    results = pd.DataFrame(
        {"fixture_id": [1, 2], "home_goals": [2, 1], "away_goals": [1, 1]}
    )
    outcomes = settle_frame(legs, results)
    assert list(outcomes) == ["win", "win", "half_loss", None]  # 1: 3 goals > 2.5; fixture 3 has no result


# --------------------------------------------------------------------------- #
# Money: single bets and coupons
# --------------------------------------------------------------------------- #


def test_return_factors_and_single_profit() -> None:
    assert return_factor("win", 2.0) == 2.0
    assert return_factor("half_win", 2.0) == pytest.approx(1.5)
    assert return_factor("push", 2.0) == 1.0
    assert return_factor("half_loss", 2.0) == 0.5
    assert return_factor("loss", 2.0) == 0.0
    assert return_factor(None, 2.0) is None
    assert single_profit("win", 2.0, 0.88) == pytest.approx(0.76)
    assert single_profit("loss", 2.0, 0.88) == pytest.approx(-1.0)
    assert single_profit("push", 2.0, 0.88) == 0.0
    assert single_profit("half_win", 2.0, 1.0) == pytest.approx(0.5)
    assert single_profit("half_loss", 2.0, 1.0) == pytest.approx(-0.5)
    assert single_profit(None, 2.0, 1.0) is None


def test_coupon_with_a_pushed_leg_drops_the_leg_out() -> None:
    settled = settle_coupon([("win", 1.5), ("push", 2.0), ("win", 1.8)])
    assert settled is not None
    assert settled.outcome == "win"
    assert settled.factor == pytest.approx(1.5 * 1.8)
    assert settled.n_active == 2
    assert settled.profit(0.88) == pytest.approx(0.88 * 1.5 * 1.8 - 1.0)


def test_coupon_half_results_multiply() -> None:
    settled = settle_coupon([("half_win", 2.0), ("win", 1.6)])
    assert settled is not None
    assert settled.factor == pytest.approx(1.5 * 1.6)
    settled = settle_coupon([("half_loss", 2.0), ("win", 1.6)])
    assert settled is not None
    assert settled.factor == pytest.approx(0.5 * 1.6)
    assert settled.outcome == "half_loss"  # returns less than the stake


def test_coupon_with_a_lost_leg_is_lost_even_when_others_are_open() -> None:
    settled = settle_coupon([("loss", 1.5), (None, 2.0)])
    assert settled is not None
    assert settled.outcome == "loss"
    assert settled.profit(0.88) == pytest.approx(-1.0)


def test_coupon_is_unsettled_while_a_leg_is_open() -> None:
    assert settle_coupon([("win", 1.5), (None, 2.0)]) is None


def test_coupon_of_pushes_is_refunded() -> None:
    settled = settle_coupon([("push", 1.5), ("push", 2.0)])
    assert settled is not None
    assert settled.outcome == "push"
    assert settled.profit(0.88) == 0.0


def test_empty_coupon_is_rejected() -> None:
    with pytest.raises(ValueError):
        settle_coupon([])


# --------------------------------------------------------------------------- #
# CLV
# --------------------------------------------------------------------------- #


def test_clv_is_price_taken_against_the_fair_closing_price() -> None:
    value = clv(2.10, 0.50, 0.88)
    assert value.raw == pytest.approx(2.10 / (1 / 0.50) - 1)
    assert value.net == pytest.approx(0.88 * 2.10 / (1 / 0.50) - 1)
    missing = clv(2.10, None, 0.88)
    assert missing.raw is None and missing.net is None


def test_clv_columns_match_the_scalar_function_and_keep_missing_missing() -> None:
    frame = pd.DataFrame({"odd_taken": [2.10, 1.80, 3.0], "closing_p_fair": [0.50, np.nan, 0.30]})
    out = clv_columns(frame, (0.88, 1.0))
    assert out["clv_raw"].iloc[0] == pytest.approx(closing_line_value(2.10, 0.50, 0.88).raw)
    assert out["clv_net_t088"].iloc[0] == pytest.approx(closing_line_value(2.10, 0.50, 0.88).net)
    assert out["clv_net_t100"].iloc[2] == pytest.approx(3.0 * 0.30 - 1.0)
    assert math.isnan(out["clv_raw"].iloc[1])  # missing stays missing, never zero


def test_leg_key_and_tau_label_are_stable() -> None:
    assert leg_key(123, "ou", "ft", 2.5, "over") == "123|ou|ft|2.50|over"
    assert leg_key(123.0, "1x2", "ft", float("nan"), "home") == "123|1x2|ft|na|home"
    assert tau_label(0.88) == "t088"
    assert tau_label(1.0) == "t100"
    assert tau_label(0.94) == "t094"


# --------------------------------------------------------------------------- #
# Results from the history database
# --------------------------------------------------------------------------- #


def test_load_match_results_reads_finished_regular_time_matches_only(tmp_path) -> None:
    db = tmp_path / "history.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE matches (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER,
                                  ht_home_goals INTEGER, ht_away_goals INTEGER);
            CREATE TABLE team_match_stats (fixture_id INTEGER, side TEXT, corners INTEGER,
                                           yellow_cards INTEGER, red_cards INTEGER);
            INSERT INTO matches VALUES (1, 'FT', 2, 1, 1, 0), (2, 'AET', 2, 1, 1, 0), (3, 'FT', 0, 0, NULL, NULL);
            INSERT INTO team_match_stats VALUES (1, 'home', 6, 2, 0), (1, 'away', 3, 1, 1),
                                                (3, 'home', NULL, NULL, NULL), (3, 'away', 4, 0, 0);
            """
        )
    out = load_match_results([1, 2, 3, 4], db).set_index("fixture_id")
    assert sorted(out.index) == [1, 3]  # the AET match is left out: its 90-minute score is unknown
    assert out.loc[1, "home_goals"] == 2 and out.loc[1, "home_goals_1h"] == 1
    assert out.loc[1, "home_corners"] == 6 and out.loc[1, "away_cards"] == 2
    assert math.isnan(out.loc[3, "home_goals_1h"])
    assert math.isnan(out.loc[3, "home_corners"])
    assert math.isnan(out.loc[3, "home_cards"])  # missing, not zero
    assert settle_leg("1x2", "1h", None, "home", out.loc[3]) is None


def test_load_match_results_without_a_database_is_empty(tmp_path) -> None:
    out = load_match_results([1, 2], tmp_path / "missing.sqlite")
    assert out.empty
    assert list(out.columns) == ["fixture_id", *st.RESULT_FIELDS]


def _directory(tmp_path):
    db = tmp_path / "directory.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE fixtures (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER,
                                   ht_home_goals INTEGER, ht_away_goals INTEGER);
            CREATE TABLE fixture_stats (fixture_id INTEGER, team_id INTEGER, side TEXT, corners INTEGER,
                                        yellow_cards INTEGER, red_cards INTEGER);
            CREATE TABLE fixture_stats_half (fixture_id INTEGER, team_id INTEGER, side TEXT, half TEXT, corners INTEGER,
                                             yellow_cards INTEGER, red_cards INTEGER);
            INSERT INTO fixtures VALUES (1, 'FT', 2, 1, 1, 0), (2, 'PEN', 1, 1, 0, 0), (3, 'NS', NULL, NULL, NULL, NULL),
                                        (4, 'FT', 3, 0, NULL, NULL);
            INSERT INTO fixture_stats VALUES (1, 10, 'home', 6, 2, 0), (1, 11, 'away', 3, 1, 1), (2, 12, 'home', 5, 0, 0);
            INSERT INTO fixture_stats_half VALUES (1, 10, 'home', '1h', 2, 1, 0), (1, 11, 'away', '1h', 1, 0, 0);
            """
        )
    return db


def test_directory_results_use_the_regular_time_score_and_first_half_statistics(tmp_path) -> None:
    out = st.load_directory_results([1, 2, 3, 4, 5], _directory(tmp_path)).set_index("fixture_id")
    assert sorted(out.index) == [1, 2, 4]  # 3 has not been played
    assert out.loc[1, "home_corners"] == 6 and out.loc[1, "away_cards"] == 2
    assert out.loc[1, "home_corners_1h"] == 2 and out.loc[1, "away_corners_1h"] == 1
    assert out.loc[2, "home_goals"] == 1  # PEN: the directory holds the 90-minute score
    assert math.isnan(out.loc[4, "home_corners"]) and math.isnan(out.loc[4, "home_goals_1h"])
    assert settle_leg("corners_ou", "1h", 2.5, "over", out.loc[1]) == "win"  # 2 + 1 = 3
    assert settle_leg("corners_ou", "2h", 5.5, "over", out.loc[1]) == "win"  # 9 - 3 = 6
    assert settle_leg("1x2", "1h", None, "home", out.loc[4]) is None


def test_load_results_fills_missing_cells_from_the_history(tmp_path) -> None:
    history = tmp_path / "history.sqlite"
    with sqlite3.connect(history) as con:
        con.executescript(
            """
            CREATE TABLE matches (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER,
                                  ht_home_goals INTEGER, ht_away_goals INTEGER);
            CREATE TABLE team_match_stats (fixture_id INTEGER, side TEXT, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER);
            INSERT INTO matches VALUES (4, 'FT', 3, 0, 2, 0), (7, 'FT', 0, 0, 0, 0);
            INSERT INTO team_match_stats VALUES (4, 'home', 8, 0, 0), (4, 'away', 2, 1, 0);
            """
        )
    out = st.load_results([1, 4, 7], _directory(tmp_path), history).set_index("fixture_id")
    assert sorted(out.index) == [1, 4, 7]
    assert out.loc[4, "home_goals_1h"] == 2 and out.loc[4, "home_corners"] == 8  # filled from the history
    assert out.loc[1, "home_corners"] == 6  # the directory wins where it has a value
    assert st.load_results([1], tmp_path / "no.sqlite", tmp_path / "none.sqlite").empty


# --------------------------------------------------------------------------- #
# Review additions
# --------------------------------------------------------------------------- #


def test_impossible_statistics_are_treated_as_missing() -> None:
    assert settle_leg("1x2", "ft", None, "home", {"home_goals": -1, "away_goals": 0}) is None  # negative count
    assert settle_leg("1x2", "ft", None, "home", {"home_goals": 1.5, "away_goals": 0}) is None  # fractional count
    assert settle_leg("corners_ou", "ft", 9.5, "over", {"home_corners": -2, "away_corners": 13}) is None
    assert settle_leg("1x2", "ft", None, "home", {"home_goals": "x", "away_goals": 0}) is None


def test_hit_value_weights_and_excludes_pushes() -> None:
    assert st.hit_value("win") == 1.0 and st.hit_value("loss") == 0.0
    assert st.hit_value("half_win") == 0.75 and st.hit_value("half_loss") == 0.25
    assert st.hit_value("push") is None and st.hit_value(None) is None


def test_clv_columns_are_missing_for_an_impossible_closing_probability_or_price() -> None:
    frame = pd.DataFrame({"odd_taken": [2.0, 2.0, 2.0, 1.0, 2.0], "closing_p_fair": [0.0, 1.2, -0.1, 0.5, 1.0]})
    out = clv_columns(frame, (0.88,))
    assert out["clv_raw"].isna().tolist() == [True, True, True, True, False]
    assert out["clv_raw"].iloc[4] == pytest.approx(1.0)  # a closing probability of one is a valid closing price


def test_a_negative_zero_line_has_the_same_leg_key_as_zero() -> None:
    assert leg_key(7, "ah", "ft", -0.0, "home") == leg_key(7, "ah", "ft", 0.0, "home") == "7|ah|ft|0.00|home"
    assert leg_key(7, "ah", "ft", -0.25, "home") == "7|ah|ft|-0.25|home"


def test_a_directory_value_wins_over_the_history_value_but_a_gap_is_filled(tmp_path) -> None:
    directory = tmp_path / "directory.sqlite3"
    with sqlite3.connect(directory) as con:
        con.executescript(
            """
            CREATE TABLE fixtures (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER,
                                   ht_home_goals INTEGER, ht_away_goals INTEGER);
            CREATE TABLE fixture_stats (fixture_id INTEGER, team_id INTEGER, side TEXT, corners INTEGER,
                                        yellow_cards INTEGER, red_cards INTEGER);
            CREATE TABLE fixture_stats_half (fixture_id INTEGER, team_id INTEGER, side TEXT, half TEXT, corners INTEGER,
                                             yellow_cards INTEGER, red_cards INTEGER);
            INSERT INTO fixtures VALUES (1, 'FT', 2, 1, NULL, NULL);
            """
        )
    history = tmp_path / "history.sqlite"
    with sqlite3.connect(history) as con:
        con.executescript(
            """
            CREATE TABLE matches (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER,
                                  ht_home_goals INTEGER, ht_away_goals INTEGER);
            CREATE TABLE team_match_stats (fixture_id INTEGER, side TEXT, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER);
            INSERT INTO matches VALUES (1, 'FT', 9, 9, 1, 0);
            """
        )
    out = st.load_results([1], directory, history).set_index("fixture_id").loc[1]
    assert out["home_goals"] == 2 and out["away_goals"] == 1  # the directory decides where it has a value
    assert out["home_goals_1h"] == 1 and out["away_goals_1h"] == 0  # the gap is filled from the history
