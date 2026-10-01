"""Scanner v1.1: new markets end to end (mapping, fair price, half-split model, settlement, page labels)."""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from model_buk.ingestion.api_football_history import RequestBudget
from model_buk.ingestion.fixture_directory import (
    FixtureDirectory,
    FixtureDirectoryCollector,
    load_fixture_events,
    parse_fixture_events,
)
from model_buk.scanner import halves as hv
from model_buk.scanner.engine import ScannerConfig, evaluate_legs, market_family
from model_buk.scanner.fair import DEFAULT_SE_MODEL, fair_prices, matrix_leg_probability, score_matrix
from model_buk.scanner.markets import (
    BET_SPECS,
    CANONICAL_MARKETS,
    HALF_MARKETS_V11,
    LADDER_MARKETS,
    MATRIX_MARKETS_V11,
    NEW_MARKETS_V11,
    NO_LINE_MARKETS,
    SELECTIONS,
    YELLOW_MARKETS,
    canonicalize,
    map_bet,
    orient_handicaps,
)
from model_buk.scanner.pipeline import new_market_counts, statistics_needed
from model_buk.scanner.report_html import leg_label_pl
from model_buk.scanner.settlement import (
    MatchResult,
    event_results,
    load_directory_results,
    load_match_results,
    settle_leg,
)

NAN = float("nan")
NOW = pd.Timestamp("2026-10-03T09:00:00Z")
KICKOFF = NOW + pd.Timedelta(hours=5)


def same(got, expected):
    assert got is not None, expected
    assert got[0] == expected[0] and got[1] == expected[1] and got[3] == expected[3]
    assert (math.isnan(got[2]) and math.isnan(expected[2])) or got[2] == expected[2]


# --------------------------------------------------------------------------- #
# Mapping: real bet types and selection strings of the archive
# --------------------------------------------------------------------------- #

ARCHIVE_STRINGS = [
    (21, "Odd", ("odd_even", "ft", NAN, "odd")),
    (21, "Even", ("odd_even", "ft", NAN, "even")),
    (22, "Odd", ("odd_even", "1h", NAN, "odd")),
    (63, "Even", ("odd_even", "2h", NAN, "even")),
    (23, "Odd", ("odd_even_home", "ft", NAN, "odd")),
    (60, "Even", ("odd_even_away", "ft", NAN, "even")),
    (9, "Home -1", ("eh", "ft", -1.0, "home")),
    (9, "Draw -1", ("eh", "ft", -1.0, "draw")),
    (9, "Away -1", ("eh", "ft", -1.0, "away")),  # all three results of one line carry the HOME handicap
    (9, "Away +2", ("eh", "ft", 2.0, "away")),
    (18, "Draw +1", ("eh", "1h", 1.0, "draw")),
    (181, "Home -3", ("eh", "2h", -3.0, "home")),
    (29, "Yes", ("win_to_nil_home", "ft", NAN, "yes")),
    (29, "No", ("win_to_nil_home", "ft", NAN, "no")),
    (30, "Yes", ("win_to_nil_away", "ft", NAN, "yes")),
    (36, "Home", ("win_to_nil_home", "ft", NAN, "yes")),
    (36, "Away", ("win_to_nil_away", "ft", NAN, "yes")),
    (7, "Home/Home", ("ht_ft", "ft", NAN, "home/home")),
    (7, "Draw/Away", ("ht_ft", "ft", NAN, "draw/away")),
    (7, "Away/Draw", ("ht_ft", "ft", NAN, "away/draw")),
    (37, "Yes", ("win_both_halves_home", "ft", NAN, "yes")),
    (53, "No", ("win_both_halves_away", "ft", NAN, "no")),
    (32, "Home", ("win_both_halves_home", "ft", NAN, "yes")),
    (32, "Away", ("win_both_halves_away", "ft", NAN, "yes")),
    (39, "Home", ("win_either_half_home", "ft", NAN, "yes")),
    (39, "Away", ("win_either_half_away", "ft", NAN, "yes")),
    (184, "Yes", ("score_both_halves", "ft", NAN, "yes")),
    (184, "No", ("score_both_halves", "ft", NAN, "no")),
    (111, "Yes", ("score_both_halves_home", "ft", NAN, "yes")),
    (112, "No", ("score_both_halves_away", "ft", NAN, "no")),
    (48, "Home", ("score_both_halves_home", "ft", NAN, "yes")),
    (48, "Away", ("score_both_halves_away", "ft", NAN, "yes")),
    (113, "Yes", ("btts_both_halves", "ft", NAN, "yes")),
    (11, "1st Half", ("highest_half", "ft", NAN, "first_half")),
    (11, "2nd Half", ("highest_half", "ft", NAN, "second_half")),
    (11, "Draw", ("highest_half", "ft", NAN, "equal")),
    (192, "2nd Half", ("highest_half_home", "ft", NAN, "second_half")),
    (193, "Draw", ("highest_half_away", "ft", NAN, "equal")),
    (14, "Home", ("first_goal", "ft", NAN, "home")),
    (14, "No goal", ("first_goal", "ft", NAN, "none")),
    (15, "Away", ("last_goal", "ft", NAN, "away")),
    (15, "No goal", ("last_goal", "ft", NAN, "none")),
    (185, "Home", ("first_goal", "1h", NAN, "home")),
    (185, "No goal", ("first_goal", "1h", NAN, "none")),
    (155, "Over 0.5", ("yellow_ou", "1h", 0.5, "over")),
    (155, "Under 1.25", ("yellow_ou", "1h", 1.25, "under")),
    (309, "Over 1.5", ("yellow_home_ou", "1h", 1.5, "over")),
    (310, "Under 0.5", ("yellow_away_ou", "1h", 0.5, "under")),
]


@pytest.mark.parametrize(("bet_id", "selection", "expected"), ARCHIVE_STRINGS)
def test_archive_strings_of_the_new_bet_types_map_to_canonical_legs(bet_id, selection, expected):
    same(map_bet(bet_id, selection), expected)
    assert expected[0] in CANONICAL_MARKETS and expected[0] in NEW_MARKETS_V11
    assert expected[3] in SELECTIONS[expected[0]]


def test_the_market_dictionary_is_consistent():
    assert len(BET_SPECS) == 87  # 57 before v1.1, 28 seen in the archive and 2 from the catalogue (309, 310) added
    assert NEW_MARKETS_V11 <= set(CANONICAL_MARKETS) and set(SELECTIONS) == set(CANONICAL_MARKETS) - {"cs"}
    assert MATRIX_MARKETS_V11 | HALF_MARKETS_V11 | YELLOW_MARKETS == NEW_MARKETS_V11
    for market in NEW_MARKETS_V11 - {"eh"} - YELLOW_MARKETS:
        assert market in NO_LINE_MARKETS and market not in LADDER_MARKETS
    assert {"yellow_ou", "yellow_home_ou", "yellow_away_ou"} <= set(LADDER_MARKETS)
    # yellow cards of the whole match are a different statistic and stay unmapped
    assert map_bet(153, "Over 2.5") is None and map_bet(156, "Over 1.5") is None


def test_canonicalize_completes_the_team_of_bet_types_that_name_it_in_the_selection():
    raw = pd.DataFrame(
        {
            "fixture_id": [1] * 4,
            "bookmaker_id": [8] * 4,
            "bookmaker": ["Bet365"] * 4,
            "bet_id": [36, 36, 48, 9],
            "selection": ["Home", "Away", "Away", "Draw +1"],
            "odd": [3.2, 7.0, 4.5, 4.0],
        }
    )
    out = canonicalize(raw)
    assert out["market"].tolist() == ["win_to_nil_home", "win_to_nil_away", "score_both_halves_away", "eh"]
    assert out["selection"].tolist() == ["yes", "yes", "yes", "draw"]
    assert out["line"].isna().tolist() == [True, True, True, False] and out.loc[3, "line"] == 1.0


def test_a_reversed_fixture_flips_the_european_handicap_with_the_asian_one():
    rows = []
    # Asian handicap home prices that RISE with the label: the fixture arrives reversed (two ladders, five lines)
    for book in (1, 2):
        for line, odd in zip((-1.0, -0.5, 0.0, 0.5, 1.0), (1.30, 1.55, 1.90, 2.40, 3.10), strict=True):
            rows.append({"fixture_id": 7, "bookmaker_id": book, "bookmaker": f"B{book}", "market": "ah", "period": "ft",
                         "line": line, "selection": "home", "odd": odd})  # fmt: skip
    # a short European ladder (no evidence of its own) of the same fixture
    for line, odd in ((-1.0, 1.5), (1.0, 6.0)):
        rows.append({"fixture_id": 7, "bookmaker_id": 1, "bookmaker": "B1", "market": "eh", "period": "ft", "line": line,
                     "selection": "home", "odd": odd})  # fmt: skip
    # corners of the same fixture vote on their own and stay as labelled
    rows.append({"fixture_id": 7, "bookmaker_id": 1, "bookmaker": "B1", "market": "corners_ah", "period": "ft", "line": 1.5,
                 "selection": "home", "odd": 1.8})  # fmt: skip
    out = orient_handicaps(pd.DataFrame(rows))
    eh = out[out["market"] == "eh"]
    assert eh["handicap_flipped"].all() and sorted(eh["line"]) == [-1.0, 1.0] and eh.loc[eh["odd"] == 1.5, "line"].item() == 1.0
    assert out[out["market"] == "ah"]["handicap_flipped"].all()
    assert not out[out["market"] == "corners_ah"]["handicap_flipped"].any()


# --------------------------------------------------------------------------- #
# Full-time matrix markets: probabilities sum to one and agree with the matrix
# --------------------------------------------------------------------------- #

MATRIX = score_matrix(1.55, 1.05, rho=-0.06)
X = np.arange(MATRIX.shape[0])[:, None]
Y = np.arange(MATRIX.shape[0])[None, :]


def test_odd_even_and_win_to_nil_are_sums_of_matrix_cells():
    odd = matrix_leg_probability(MATRIX, "odd_even", "odd")
    assert odd + matrix_leg_probability(MATRIX, "odd_even", "even") == pytest.approx(1.0)
    assert odd == pytest.approx(MATRIX[(X + Y) % 2 == 1].sum())
    assert matrix_leg_probability(MATRIX, "odd_even_home", "even") == pytest.approx(MATRIX[(X % 2 == 0) & (Y >= 0)].sum())
    assert matrix_leg_probability(MATRIX, "odd_even_away", "odd") == pytest.approx(MATRIX[(Y % 2 == 1) & (X >= 0)].sum())
    nil = matrix_leg_probability(MATRIX, "win_to_nil_home", "yes")
    assert nil == pytest.approx(MATRIX[1:, 0].sum())
    assert nil + matrix_leg_probability(MATRIX, "win_to_nil_home", "no") == pytest.approx(1.0)
    assert matrix_leg_probability(MATRIX, "win_to_nil_away", "yes") == pytest.approx(MATRIX[0, 1:].sum())


@pytest.mark.parametrize("line", [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0])
def test_european_handicap_is_a_three_way_split_of_the_goal_difference(line):
    probs = [matrix_leg_probability(MATRIX, "eh", s, line) for s in ("home", "draw", "away")]
    assert sum(probs) == pytest.approx(1.0)
    assert probs[1] == pytest.approx(MATRIX[(X - Y) == -line].sum())
    if line == 0.0:
        assert probs == pytest.approx([matrix_leg_probability(MATRIX, "1x2", s) for s in ("home", "draw", "away")])
    assert math.isnan(matrix_leg_probability(MATRIX, "eh", "home", 0.5))  # whole goals only


# --------------------------------------------------------------------------- #
# The half split
# --------------------------------------------------------------------------- #

SPLIT = hv.HalfSplit(MATRIX, 0.44, 0.46)
JOINT_SETS = {
    "ht_ft": SELECTIONS["ht_ft"],
    "highest_half": SELECTIONS["highest_half"],
    "highest_half_home": SELECTIONS["highest_half"],
    "highest_half_away": SELECTIONS["highest_half"],
    "first_goal": ("home", "away", "none"),
    "last_goal": ("home", "away", "none"),
    **{m: ("yes", "no") for m in HALF_MARKETS_V11 if SELECTIONS[m] == ("yes", "no")},
}


@pytest.mark.parametrize("market", sorted(JOINT_SETS))
def test_every_half_market_is_a_probability_distribution(market):
    total = sum(hv.half_leg_probability(SPLIT, market, "ft", s) for s in JOINT_SETS[market])
    assert total == pytest.approx(1.0, abs=1e-12)


def test_the_joint_keeps_the_full_time_matrix_and_the_rates_add_up():
    joint = SPLIT.joint()
    assert joint.sum() == pytest.approx(1.0)
    size = MATRIX.shape[0]
    # full-time cell (x, y) = sum over the splits of x and y
    full = np.zeros_like(MATRIX)
    for a in range(size):
        for b in range(size):
            for c in range(size - a):
                for d in range(size - b):
                    full[a + c, b + d] += joint[a, b, c, d]
    assert np.abs(full - MATRIX).max() < 1e-12
    rates = SPLIT.rates()
    for side in (0, 1):
        assert rates["1h"][side] + rates["2h"][side] == pytest.approx(rates["ft"][side])
    first_means = hv.HalfSplit(MATRIX, 0.44, 0.46).first_half()
    goals = np.arange(size)
    assert (first_means.sum(axis=1) * goals).sum() == pytest.approx(rates["1h"][0])
    assert (first_means.sum(axis=0) * goals).sum() == pytest.approx(rates["1h"][1])


def test_poisson_halves_are_independent_poisson_with_the_split_rates():
    poisson = score_matrix(1.6, 1.2)
    split = hv.HalfSplit(poisson, 0.45, 0.40)
    assert np.abs(split.first_half() - score_matrix(1.6 * 0.45, 1.2 * 0.40)).max() < 1e-9
    assert np.abs(split.second_half() - score_matrix(1.6 * 0.55, 1.2 * 0.60)).max() < 1e-9


def test_ht_ft_marginals_are_the_first_half_and_full_time_results():
    first = SPLIT.first_half()
    for half_result in ("home", "draw", "away"):
        marginal = sum(hv.half_leg_probability(SPLIT, "ht_ft", "ft", f"{half_result}/{final}") for final in ("home", "draw", "away"))
        assert marginal == pytest.approx(matrix_leg_probability(first, "1x2", half_result), abs=1e-12)
        assert marginal == pytest.approx(hv.half_leg_probability(SPLIT, "1x2", "1h", half_result), abs=1e-12)
    for final in ("home", "draw", "away"):
        marginal = sum(hv.half_leg_probability(SPLIT, "ht_ft", "ft", f"{half_result}/{final}") for half_result in ("home", "draw", "away"))
        assert marginal == pytest.approx(matrix_leg_probability(MATRIX, "1x2", final), abs=1e-12)


def test_half_markets_agree_with_the_halves_of_the_joint():
    first, second = SPLIT.first_half(), SPLIT.second_half()
    nobody = MATRIX[0, 0]
    assert hv.half_leg_probability(SPLIT, "first_goal", "ft", "none") == pytest.approx(nobody)
    assert hv.half_leg_probability(SPLIT, "last_goal", "ft", "none") == pytest.approx(nobody)
    assert hv.half_leg_probability(SPLIT, "first_goal", "1h", "none") == pytest.approx(first[0, 0])
    # home and away of the first goal split the rest in proportion to the rates when the shares are equal
    equal = hv.HalfSplit(score_matrix(1.5, 1.0), 0.45, 0.45)
    home = hv.half_leg_probability(equal, "first_goal", "ft", "home")
    assert home / (1.0 - equal.full_time[0, 0]) == pytest.approx(1.5 / 2.5, rel=1e-6)
    # to score in both halves (by anyone) = 1 - P(goalless first half) - P(goalless second half) + P(0:0)
    both = hv.half_leg_probability(SPLIT, "score_both_halves", "ft", "yes")
    joint = SPLIT.joint()
    assert both == pytest.approx(1.0 - joint[0, 0].sum() - joint[:, :, 0, 0].sum() + joint[0, 0, 0, 0])
    assert first.sum() == pytest.approx(1.0) and second.sum() == pytest.approx(1.0)
    # second-half legs of the old matrices' markets come from the second half of the split
    assert hv.half_leg_probability(SPLIT, "odd_even", "2h", "odd") == pytest.approx(matrix_leg_probability(second, "odd_even", "odd"))
    assert sum(hv.half_leg_probability(SPLIT, "eh", "1h", s, -1.0) for s in ("home", "draw", "away")) == pytest.approx(1.0)


def test_calibration_recovers_the_shares_behind_first_half_prices():
    from model_buk.scanner.fair import FitTarget

    first = hv.first_half_matrix(MATRIX, 0.41, 0.48)
    targets = [
        FitTarget("1x2", "home", NAN, matrix_leg_probability(first, "1x2", "home")),
        FitTarget("1x2", "draw", NAN, matrix_leg_probability(first, "1x2", "draw")),
        FitTarget("ou", "over", 0.5, matrix_leg_probability(first, "ou", "over", 0.5)),
        FitTarget("ou", "over", 1.5, matrix_leg_probability(first, "ou", "over", 1.5)),
        FitTarget("ah", "home", 0.0, matrix_leg_probability(first, "ah", "home", 0.0)),
    ]
    fit = hv.calibrate_shares(MATRIX, targets, (0.44, 0.44))
    assert fit is not None and fit.n_targets == 5
    assert fit.s_home == pytest.approx(0.41, abs=0.01) and fit.s_away == pytest.approx(0.48, abs=0.01)
    # a lone first-half total fixes the sum, the prior the split
    lone = hv.calibrate_shares(MATRIX, targets[3:4], (0.44, 0.44))
    assert abs(lone.s_home - lone.s_away) < 0.05
    split = hv.split_fixture(MATRIX, (0.44, 0.44, "league"), targets)
    assert split.source == "pinnacle_1h" and split.rmse_1h < 0.01
    assert hv.split_fixture(MATRIX, (0.44, 0.45, "league")).source == "league"


# --------------------------------------------------------------------------- #
# League shares: point in time
# --------------------------------------------------------------------------- #


def _history(rows):
    return pd.DataFrame(rows, columns=["league_id", "kickoff_utc", "home_goals", "away_goals", "ht_home_goals", "ht_away_goals"])


def test_league_shares_use_only_matches_before_the_cutoff_and_shrink_to_the_global_share():
    cutoff = pd.Timestamp("2026-06-01T00:00:00Z")
    before = [(1, "2026-05-01T18:00:00+00:00", 2, 1, 1, 0)] * 50 + [(2, "2026-05-02T18:00:00+00:00", 2, 2, 0, 1)] * 50
    after = [(1, "2026-06-01T00:00:00+00:00", 9, 9, 9, 9), (2, "2026-07-01T00:00:00+00:00", 5, 0, 0, 0), (3, "2026-08-01T00:00:00+00:00", 3, 3, 3, 3)]
    clean = hv.shares_from_matches(_history(before), cutoff, prior_strength=100.0)
    leaked = hv.shares_from_matches(_history(before + after), cutoff, prior_strength=100.0)
    assert clean == leaked  # a match at or after the cutoff changes nothing
    assert 3 not in leaked.leagues
    g_home = 50 * 1 / (50 * 2 + 50 * 2)
    assert clean.global_home == pytest.approx(g_home)
    assert clean.leagues[1][0] == pytest.approx((50 + 100 * g_home) / (100 + 100))  # shrunk towards the global share
    assert clean.for_league(1)[2] == "league" and clean.for_league(99)[2] == "global"
    # the window
    old = [(1, "2019-05-01T18:00:00+00:00", 3, 0, 3, 0)] * 500
    assert hv.shares_from_matches(_history(before + old), cutoff, window_years=4.0, prior_strength=100.0) == clean


def test_estimate_half_shares_reads_the_history_database_point_in_time(tmp_path):
    path = tmp_path / "history.sqlite"
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE matches (fixture_id INTEGER, league_id INTEGER, kickoff_utc TEXT, status_short TEXT, home_goals INTEGER, "
            "away_goals INTEGER, ht_home_goals INTEGER, ht_away_goals INTEGER)"
        )
        rows = [(i, 39, f"2026-09-{1 + i % 20:02d}T15:00:00+00:00", "FT", 2, 1, 1, 0) for i in range(40)]
        rows += [(100, 39, "2026-09-30T15:00:00+00:00", "FT", 4, 0, 4, 0), (101, 39, "2026-09-01T15:00:00+00:00", "PST", 0, 0, 0, 0)]
        con.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
    shares = hv.estimate_half_shares(path, "2026-09-25T00:00:00Z")
    assert shares.cutoff == pd.Timestamp("2026-09-25T00:00:00Z") and shares.n_matches[39] == 40
    assert shares.global_home == pytest.approx(0.5) and shares.global_away == pytest.approx(0.0)
    assert hv.estimate_half_shares(tmp_path / "missing.sqlite", NOW) is hv.DEFAULT_HALF_SHARES


# --------------------------------------------------------------------------- #
# Fair prices of the new markets
# --------------------------------------------------------------------------- #


def _rows(fixture, book, market, period, line, probs_or_odds, *, odds=False, league=39):
    out = []
    for selection, value in probs_or_odds.items():
        out.append(
            {
                "fixture_id": fixture, "league_id": league, "bookmaker": book, "market": market, "period": period, "line": line,
                "selection": selection, "odd": float(value) if odds else 1.0 / float(value), "kickoff_utc": KICKOFF,
                "fetched_at": NOW - pd.Timedelta(minutes=10), "age_minutes": 10.0, "three_way": False,
            }
        )  # fmt: skip
    return out


def _pinnacle_board(fixture, matrix, first=None):
    rows = _rows(fixture, "Pinnacle", "1x2", "ft", NAN, {s: matrix_leg_probability(matrix, "1x2", s) for s in ("home", "draw", "away")})
    for line in (1.5, 2.5, 3.5):
        p = matrix_leg_probability(matrix, "ou", "over", line)
        rows += _rows(fixture, "Pinnacle", "ou", "ft", line, {"over": p, "under": 1 - p})
    for line in (-0.5, 0.0, 0.5):
        p = matrix_leg_probability(matrix, "ah", "home", line)
        rows += _rows(fixture, "Pinnacle", "ah", "ft", line, {"home": p, "away": 1 - p})
    if first is not None:
        rows += _rows(fixture, "Pinnacle", "1x2", "1h", NAN, {s: matrix_leg_probability(first, "1x2", s) for s in ("home", "draw", "away")})
        for line in (0.5, 1.5):
            p = matrix_leg_probability(first, "ou", "over", line)
            rows += _rows(fixture, "Pinnacle", "ou", "1h", line, {"over": p, "under": 1 - p})
    return rows


def _pick(fair, fixture, market, selection, period="ft", line=NAN):
    mask = (fair["fixture_id"] == fixture) & (fair["market"] == market) & (fair["period"] == period) & (fair["selection"] == selection)
    mask &= fair["line"].isna() if math.isnan(line) else (fair["line"] == line)
    return fair[mask].iloc[0]


def test_new_markets_are_derived_from_pinnacle_with_a_wider_standard_error():
    truth = score_matrix(1.45, 1.10, rho=-0.05)
    first = hv.first_half_matrix(truth, 0.43, 0.46)
    rows = _pinnacle_board(1, truth, first) + _pinnacle_board(2, truth)
    soft = []
    for fixture in (1, 2):
        soft += _rows(fixture, "Bet365", "ht_ft", "ft", NAN, {s: 9.0 for s in SELECTIONS["ht_ft"]}, odds=True)
        soft += _rows(fixture, "Bet365", "odd_even", "ft", NAN, {"odd": 1.9, "even": 1.9}, odds=True)
        soft += _rows(fixture, "Bet365", "eh", "1h", -1.0, {"home": 6.0, "draw": 3.4, "away": 1.4}, odds=True)
        soft += _rows(fixture, "Bet365", "first_goal", "ft", NAN, {"home": 1.9, "away": 2.3, "none": 11.0}, odds=True)
    shares = hv.HalfShares(0.44, 0.44, {39: (0.45, 0.45)})
    fair = fair_prices(pd.DataFrame(rows + soft), as_of=NOW, half_shares=shares)

    calibrated = _pick(fair, 1, "ht_ft", "draw/home")
    league = _pick(fair, 2, "ht_ft", "draw/home")
    assert calibrated["fair_source"] == league["fair_source"] == "pinnacle_derived"
    assert calibrated["fair_detail"] == "half:pinnacle_1h" and league["fair_detail"] == "half:league"
    # fixture 1 recovers the true split, so its HT/FT price is the truth's
    assert calibrated["p_fair"] == pytest.approx(hv.half_leg_probability(hv.HalfSplit(truth, 0.43, 0.46), "ht_ft", "ft", "draw/home"), abs=0.004)
    assert league["p_fair"] == pytest.approx(hv.half_leg_probability(hv.HalfSplit(truth, 0.45, 0.45), "ht_ft", "ft", "draw/home"), abs=0.004)
    assert league["p_se"] > calibrated["p_se"]  # league shares only: wider
    total = sum(_pick(fair, 1, "ht_ft", s)["p_fair"] for s in SELECTIONS["ht_ft"])
    assert total == pytest.approx(1.0, abs=1e-6)
    odd = _pick(fair, 1, "odd_even", "odd")
    assert odd["fair_detail"] == "matrix" and odd["p_fair"] + _pick(fair, 1, "odd_even", "even")["p_fair"] == pytest.approx(1.0)
    eh = [_pick(fair, 1, "eh", s, "1h", -1.0) for s in ("home", "draw", "away")]
    assert sum(r["p_fair"] for r in eh) == pytest.approx(1.0) and {r["fair_detail"] for r in eh} == {"half:pinnacle_1h"}
    # a half-split leg is less certain than a plain matrix leg of the same probability and lead time
    matrix_se = DEFAULT_SE_MODEL.p_se(np.array([calibrated["p_fair"]]), 5.0, ["derived_matrix"], abs_err=0.0)[0]
    assert calibrated["p_se"] > matrix_se
    goal = [_pick(fair, 1, "first_goal", s) for s in ("home", "away", "none")]
    assert sum(r["p_fair"] for r in goal) == pytest.approx(1.0) and goal[2]["p_fair"] == pytest.approx(truth[0, 0], abs=0.003)


def test_first_half_yellow_cards_are_priced_only_by_pinnacle_or_a_consensus_of_three():
    lone = _rows(5, "Betfair", "yellow_ou", "1h", 1.5, {"over": 1.9, "under": 1.85}, odds=True)
    lone += _rows(5, "1xBet", "yellow_ou", "1h", 1.5, {"over": 1.95, "under": 1.8}, odds=True)
    assert fair_prices(pd.DataFrame(lone), as_of=NOW).empty  # Betfair alone is not enough here
    three = lone + _rows(5, "Bet365", "yellow_ou", "1h", 1.5, {"over": 1.9, "under": 1.9}, odds=True)
    fair = fair_prices(pd.DataFrame(three), as_of=NOW)
    assert set(fair["fair_source"]) == {"consensus"} and fair["n_books"].iloc[0] == 3
    # the same Betfair board for a full-time card total stays a valid single-book fallback
    cards = _rows(6, "Betfair", "cards_ou", "ft", 4.5, {"over": 1.9, "under": 1.85}, odds=True)
    assert set(fair_prices(pd.DataFrame(cards), as_of=NOW)["fair_source"]) == {"betfair"}


def test_new_legs_are_evaluated_with_families_book_sets_and_the_fair_detail():
    truth = score_matrix(1.45, 1.10)
    quotes = pd.DataFrame(_pinnacle_board(1, truth) + _rows(1, "Bet365", "ht_ft", "ft", NAN, {s: 9.0 for s in SELECTIONS["ht_ft"]}, odds=True))
    fair = fair_prices(quotes, as_of=NOW)
    legs = evaluate_legs(quotes, fair, None, ScannerConfig(), as_of=NOW)
    ht = legs[legs["market"] == "ht_ft"]
    assert len(ht) == 9 and set(ht["fair_detail"]) == {"half:global"} and set(ht["market_family"]) == {"result_ft"}
    assert ht["book_shift"].notna().all()  # the complete nine-way set gives the bookmaker's own probability
    assert (ht["refund_share"] == 0).all()
    assert market_family("yellow_ou", "1h") == "cards_1h" and market_family("highest_half", "ft") == "goals_ft"


# --------------------------------------------------------------------------- #
# Settlement
# --------------------------------------------------------------------------- #

# 1st half 1:0, full time 2:3 -> 2nd half 1:3; home scored first, away last
GAME = MatchResult(home_goals=2, away_goals=3, home_goals_1h=1, away_goals_1h=0, first_goal_side=1, last_goal_side=2,
                   home_yellow_1h=2, away_yellow_1h=0, home_yellow=3, away_yellow=2)  # fmt: skip


@pytest.mark.parametrize(
    ("market", "period", "line", "selection", "expected"),
    [
        ("odd_even", "ft", NAN, "odd", "win"),
        ("odd_even", "1h", NAN, "even", "loss"),
        ("odd_even", "2h", NAN, "even", "win"),
        ("odd_even_home", "ft", NAN, "even", "win"),
        ("odd_even_away", "ft", NAN, "even", "loss"),
        ("eh", "ft", 1.0, "draw", "win"),  # 2 + 1 = 3
        ("eh", "ft", 1.0, "away", "loss"),
        ("eh", "ft", -1.0, "away", "win"),
        ("eh", "1h", -1.0, "draw", "win"),  # 1 - 1 = 0
        ("eh", "2h", 2.0, "home", "loss"),  # 1 + 2 = 3, a draw
        ("win_to_nil_home", "ft", NAN, "no", "win"),
        ("win_to_nil_away", "ft", NAN, "yes", "loss"),
        ("win_to_nil_home", "1h", NAN, "yes", "win"),
        ("ht_ft", "ft", NAN, "home/away", "win"),
        ("ht_ft", "ft", NAN, "home/home", "loss"),
        ("win_both_halves_away", "ft", NAN, "yes", "loss"),
        ("win_either_half_home", "ft", NAN, "yes", "win"),
        ("win_either_half_away", "ft", NAN, "no", "loss"),
        ("score_both_halves", "ft", NAN, "yes", "win"),
        ("score_both_halves_home", "ft", NAN, "yes", "win"),
        ("score_both_halves_away", "ft", NAN, "no", "win"),
        ("btts_both_halves", "ft", NAN, "yes", "loss"),
        ("highest_half", "ft", NAN, "second_half", "win"),
        ("highest_half_home", "ft", NAN, "equal", "win"),
        ("highest_half_away", "ft", NAN, "first_half", "loss"),
        ("first_goal", "ft", NAN, "home", "win"),
        ("first_goal", "1h", NAN, "home", "win"),
        ("last_goal", "ft", NAN, "away", "win"),
        ("last_goal", "ft", NAN, "none", "loss"),
        ("yellow_ou", "1h", 1.5, "over", "win"),
        ("yellow_ou", "1h", 2.0, "over", "push"),
        ("yellow_home_ou", "1h", 1.75, "under", "half_loss"),
        ("yellow_away_ou", "1h", 0.5, "under", "win"),
        ("yellow_ou", "2h", 2.5, "under", "loss"),  # 2nd half: 5 - 2 = 3
    ],
)
def test_every_new_market_settles(market, period, line, selection, expected):
    assert settle_leg(market, period, line, selection, GAME) == expected


def test_goalless_matches_and_halves_settle_first_and_last_goal_as_none_without_events():
    goalless = MatchResult(home_goals=0, away_goals=0, home_goals_1h=0, away_goals_1h=0)
    assert settle_leg("first_goal", "ft", NAN, "none", goalless) == "win"
    assert settle_leg("last_goal", "ft", NAN, "home", goalless) == "loss"
    late = MatchResult(home_goals=1, away_goals=0, home_goals_1h=0, away_goals_1h=0)
    assert settle_leg("first_goal", "1h", NAN, "none", late) == "win"
    assert settle_leg("first_goal", "ft", NAN, "home", late) is None  # goals but no goal order: missing
    assert settle_leg("highest_half", "ft", NAN, "equal", goalless) == "win"


def test_missing_statistics_never_settle_a_new_leg():
    no_half = MatchResult(home_goals=2, away_goals=1)
    for market in ("ht_ft", "highest_half", "score_both_halves", "win_both_halves_home"):
        selection = "home/home" if market == "ht_ft" else "equal" if market == "highest_half" else "yes"
        assert settle_leg(market, "ft", NAN, selection, no_half) is None
    assert settle_leg("odd_even", "1h", NAN, "odd", no_half) is None
    assert settle_leg("yellow_ou", "1h", 1.5, "over", no_half) is None  # no card events: never zero cards
    contradicting = MatchResult(home_goals=0, away_goals=2, home_goals_1h=0, away_goals_1h=1, first_goal_side=1)
    assert settle_leg("first_goal", "ft", NAN, "home", contradicting) is None
    assert settle_leg("eh", "ft", 0.5, "home", GAME) is None
    assert settle_leg("ht_ft", "1h", NAN, "home/home", GAME) is None


# --------------------------------------------------------------------------- #
# Event log: cards by minute and goal order
# --------------------------------------------------------------------------- #

FIXTURES = pd.DataFrame(
    [
        {"fixture_id": 1, "home_team_id": 10, "away_team_id": 20, "home_goals": 2, "away_goals": 1},
        {"fixture_id": 2, "home_team_id": 30, "away_team_id": 40, "home_goals": 1, "away_goals": 0},
        {"fixture_id": 3, "home_team_id": 50, "away_team_id": 60, "home_goals": 1, "away_goals": 1},
    ]
)


def _event(fixture, seq, elapsed, extra, team, kind, detail):
    return {"fixture_id": fixture, "seq": seq, "elapsed": elapsed, "extra": extra, "team_id": team, "type": kind, "detail": detail}


def test_event_results_count_first_half_cards_by_minute_and_keep_missing_missing():
    events = pd.DataFrame(
        [
            _event(1, 0, 20, None, 10, "Card", "Yellow Card"),
            _event(1, 1, 45, 3, 20, "Card", "Yellow Card"),  # first-half stoppage time: still the first half
            _event(1, 2, 46, None, 10, "Card", "Yellow Card"),  # second half
            _event(1, 3, 60, None, 10, "subst", "Substitution 1"),
            _event(1, 4, 70, None, 20, "Card", "Second Yellow Card"),
            _event(1, 5, 80, None, 20, "Card", "Red Card"),
            _event(1, 6, 105, None, 10, "Card", "Yellow Card"),  # extra time: not the 90 minutes
            _event(1, 7, 30, None, 20, "Card", "Red Card"),
            # fixture 2: a log of goals only says nothing about cards
            _event(2, 0, 10, None, 30, "Goal", "Normal Goal"),
        ]
    )
    out = event_results(events, FIXTURES).set_index("fixture_id")
    one = out.loc[1]
    assert (one["home_cards_1h"], one["away_cards_1h"]) == (1, 2)
    assert (one["home_yellow_1h"], one["away_yellow_1h"]) == (1, 1)
    assert (one["home_yellow"], one["away_yellow"]) == (2, 2)
    two = out.loc[2]
    assert two[["home_cards_1h", "away_cards_1h", "home_yellow_1h", "home_yellow"]].isna().all()
    assert out.loc[3].isna().all()  # no events at all


def test_event_results_give_the_goal_order_only_when_it_adds_up_to_the_score():
    events = pd.DataFrame(
        [
            _event(1, 1, 30, None, 20, "Goal", "Normal Goal"),
            _event(1, 0, 30, None, 10, "Goal", "Own Goal"),  # same minute, earlier in the list: first; credited to 10
            _event(1, 2, 90, 4, 10, "Goal", "Penalty"),
            _event(1, 3, 50, None, 20, "Goal", "Missed Penalty"),
            _event(1, 4, 112, None, 20, "Goal", "Normal Goal"),  # extra time
            _event(2, 0, 10, None, 40, "Goal", "Normal Goal"),  # credited to the away team, but the score is 1:0
        ]
    )
    out = event_results(events, FIXTURES).set_index("fixture_id")
    assert (out.loc[1, "first_goal_side"], out.loc[1, "last_goal_side"]) == (1, 1)
    assert out.loc[2, ["first_goal_side", "last_goal_side"]].isna().all()
    assert out.loc[3, ["first_goal_side", "last_goal_side"]].isna().all()


def test_history_results_carry_the_event_fields(tmp_path):
    path = tmp_path / "history.sqlite"
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE matches (fixture_id INTEGER, status_short TEXT, home_goals INTEGER, away_goals INTEGER, "
            "ht_home_goals INTEGER, ht_away_goals INTEGER, home_team_id INTEGER, away_team_id INTEGER)"
        )
        con.execute("CREATE TABLE team_match_stats (fixture_id INTEGER, team_id INTEGER, side TEXT, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER)")
        con.execute("CREATE TABLE events (fixture_id INTEGER, seq INTEGER, elapsed INTEGER, extra INTEGER, team_id INTEGER, type TEXT, detail TEXT)")
        con.execute("INSERT INTO matches VALUES (1, 'FT', 1, 0, 1, 0, 10, 20)")
        con.executemany(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            [(1, 0, 12, None, 10, "Goal", "Normal Goal"), (1, 1, 44, None, 20, "Card", "Yellow Card"), (1, 2, 70, None, 10, "subst", "Substitution 1")],
        )
    row = load_match_results([1], path).set_index("fixture_id").loc[1]
    assert row["first_goal_side"] == 1 and row["last_goal_side"] == 1
    assert (row["home_cards_1h"], row["away_cards_1h"], row["away_yellow_1h"]) == (0, 1, 1)
    assert settle_leg("first_goal", "ft", NAN, "home", row) == "win"


# --------------------------------------------------------------------------- #
# Fixture directory: events with minutes
# --------------------------------------------------------------------------- #

DNOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _detail(fixture_id, events, *, status="FT", goals=(1, 1), halftime=(1, 0)):
    kickoff = DNOW - timedelta(days=1)
    home, away = fixture_id * 10 + 1, fixture_id * 10 + 2
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat(), "timestamp": int(kickoff.timestamp()), "referee": None,
                    "timezone": "UTC", "venue": {"id": 1, "name": "S", "city": "C"},
                    "status": {"long": "Match Finished", "short": status, "elapsed": 90, "extra": None}},
        "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026, "round": "R 8"},
        "teams": {"home": {"id": home, "name": "Home FC"}, "away": {"id": away, "name": "Away FC"}},
        "goals": {"home": goals[0], "away": goals[1]},
        "score": {"halftime": {"home": halftime[0], "away": halftime[1]}, "fulltime": {"home": goals[0], "away": goals[1]},
                  "extratime": {"home": None, "away": None}, "penalty": {"home": None, "away": None}},
        "statistics": [],
        "events": events,
    }  # fmt: skip


def _api_event(team, kind, detail, elapsed, extra=None):
    return {"time": {"elapsed": elapsed, "extra": extra}, "team": {"id": team}, "type": kind, "detail": detail, "player": {"id": 1}}


EVENTS_7 = [
    _api_event(71, "Goal", "Normal Goal", 20),
    _api_event(72, "Card", "Yellow Card", 45, 2),
    _api_event(71, "Var", "Goal cancelled", 50),
    _api_event(72, "subst", "Substitution 1", 60),
    _api_event(72, "Goal", "Normal Goal", 88),
    _api_event(71, "Card", "Yellow Card", 47),
]


class _Client:
    def __init__(self, details):
        self.details = details
        self.calls = 0

    def get(self, endpoint, params):
        self.calls += 1
        ids = [int(i) for i in params["ids"].split("-")]
        return {"response": [self.details[i] for i in ids if i in self.details]}, {"x-ratelimit-requests-remaining": "5000"}


def test_parse_fixture_events_keeps_goals_cards_and_substitutions_with_minutes():
    rows = parse_fixture_events(_detail(7, EVENTS_7), "2026-10-03T12:00:00+00:00")
    assert [r["type"] for r in rows] == ["goal", "card", "subst", "goal", "card"]  # the VAR event is not kept
    assert [r["seq"] for r in rows] == [0, 1, 3, 4, 5]
    card = rows[1]
    assert (card["elapsed"], card["extra"], card["side"], card["detail"]) == (45, 2, "away", "Yellow Card")


def test_details_store_the_events_and_the_directory_results_use_them(tmp_path):
    directory = FixtureDirectory(tmp_path)
    collector = FixtureDirectoryCollector(_Client({7: _detail(7, EVENTS_7)}), directory, RequestBudget(max_requests=5), clock=lambda: DNOW)
    collector.fetch_fixture_details([7], DNOW)
    events = load_fixture_events(directory.db_path, [7])
    assert len(events) == 5
    row = load_directory_results([7], directory.db_path).set_index("fixture_id").loc[7]
    assert (row["home_cards_1h"], row["away_cards_1h"]) == (0, 1)  # 45+2 is the first half, 47 the second
    assert (row["first_goal_side"], row["last_goal_side"]) == (1, 2)
    assert settle_leg("yellow_away_ou", "1h", 0.5, "over", row) == "win"
    assert settle_leg("last_goal", "ft", NAN, "away", row) == "win"


def test_first_half_cards_fall_back_to_the_half_statistics_without_a_complete_log(tmp_path):
    directory = FixtureDirectory(tmp_path)
    goals_only = [_api_event(81, "Goal", "Normal Goal", 10), _api_event(82, "Goal", "Normal Goal", 60)]
    collector = FixtureDirectoryCollector(_Client({8: _detail(8, goals_only)}), directory, RequestBudget(max_requests=5), clock=lambda: DNOW)
    collector.fetch_fixture_details([8], DNOW)
    row = load_directory_results([8], directory.db_path).set_index("fixture_id").loc[8]
    assert pd.isna(row["home_cards_1h"]) and pd.isna(row["home_yellow_1h"])  # never zero
    directory.upsert_half_stats(
        [
            {"fixture_id": 8, "team_id": 81, "side": "home", "half": "1h", "corners": 2, "yellow_cards": 1, "red_cards": None,
             "shots": None, "shots_on_target": None, "fetched_at": "2026-10-03T12:00:00+00:00"},
            {"fixture_id": 8, "team_id": 82, "side": "away", "half": "1h", "corners": 1, "yellow_cards": 2, "red_cards": None,
             "shots": None, "shots_on_target": None, "fetched_at": "2026-10-03T12:00:00+00:00"},
        ]
    )  # fmt: skip
    row = load_directory_results([8], directory.db_path).set_index("fixture_id").loc[8]
    assert (row["home_cards_1h"], row["away_cards_1h"]) == (1, 2)
    assert pd.isna(row["home_yellow_1h"])  # yellow cards alone come from the events only
    assert (row["first_goal_side"], row["last_goal_side"]) == (1, 2)


def test_an_existing_directory_is_backfilled_from_the_raw_details_once(tmp_path):
    raw = tmp_path / "raw" / "details" / "2026-10-02"
    raw.mkdir(parents=True)
    records = [{"fetched_at": "2026-10-02T10:00:00+00:00", "fixture": _detail(7, EVENTS_7)},
               {"fetched_at": "2026-10-02T10:00:00+00:00", "fixture": _detail(9, EVENTS_7, status="2H")}]  # fmt: skip
    (raw / "run_001.jsonl.gz").write_bytes(gzip.compress("\n".join(json.dumps(r) for r in records).encode()))
    directory = FixtureDirectory(tmp_path)  # the table is new: filled from the raw files
    assert sorted(load_fixture_events(directory.db_path, [7, 9])["fixture_id"].unique()) == [7]  # a running match is skipped
    assert directory.backfill_events() == 0  # nothing left to fill


# --------------------------------------------------------------------------- #
# Pipeline helpers and page labels
# --------------------------------------------------------------------------- #


def test_statistics_needed_asks_for_the_details_of_new_legs():
    now = pd.Timestamp("2026-10-04T12:00:00Z")
    kick = "2026-10-03T18:00:00+00:00"
    pending = pd.DataFrame(
        [
            {"fixture_id": 1, "market": "ht_ft", "period": "ft", "kickoff_utc": kick},  # half-time score missing
            {"fixture_id": 2, "market": "first_goal", "period": "ft", "kickoff_utc": kick},  # goal order missing
            {"fixture_id": 3, "market": "first_goal", "period": "ft", "kickoff_utc": kick},  # goalless: nothing needed
            {"fixture_id": 4, "market": "yellow_ou", "period": "1h", "kickoff_utc": kick},  # yellow cards missing
            {"fixture_id": 5, "market": "odd_even", "period": "ft", "kickoff_utc": kick},  # complete
            {"fixture_id": 6, "market": "first_goal", "period": "1h", "kickoff_utc": kick},  # goalless first half
        ]
    )
    results = pd.DataFrame(
        [
            {"fixture_id": 1, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 2, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 3, "home_goals": 0, "away_goals": 0},
            {"fixture_id": 4, "home_goals": 1, "away_goals": 0, "home_goals_1h": 0, "away_goals_1h": 0},
            {"fixture_id": 5, "home_goals": 1, "away_goals": 0},
            {"fixture_id": 6, "home_goals": 1, "away_goals": 0, "home_goals_1h": 0, "away_goals_1h": 0},
        ]
    )
    details, halves = statistics_needed(pending, results, now, min_age_hours=2.0)
    assert details == [1, 2, 4] and halves == []


def test_new_market_counts_log_unpriced_first_half_cards():
    quotes = pd.DataFrame(
        _rows(5, "1xBet", "yellow_ou", "1h", 1.5, {"over": 1.9, "under": 1.85}, odds=True)
        + _rows(5, "Pinnacle", "1x2", "ft", NAN, {"home": 2.0, "draw": 3.5, "away": 4.0}, odds=True)
    )
    counts = new_market_counts(quotes, fair_prices(quotes, as_of=NOW), None, ScannerConfig())
    assert counts["quotes"] == {"yellow_ou": 2} and counts["unpriced"] == {"yellow_ou": 2}


@pytest.mark.parametrize("market", sorted(NEW_MARKETS_V11))
def test_every_new_market_has_a_polish_label(market):
    selection = SELECTIONS[market][0]
    line = 1.5 if market in YELLOW_MARKETS else -1.0 if market == "eh" else NAN
    label = leg_label_pl(market, "ft", line, selection, "Arsenal", "Chelsea")
    assert market not in label and selection not in label.split(":")[0]
