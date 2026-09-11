from __future__ import annotations

import numpy as np
import pandas as pd

from model_buk.distributions import (
    convolved_total_over_probability,
    convolved_total_pmf,
    nb_over_probability,
)
from model_buk.markets.pricing import devig_two_way, expected_value, fair_odds
from model_buk.strengths import add_corner_strength_features


def test_convolved_distribution_is_valid() -> None:
    pmf = convolved_total_pmf(5.5, 4.3, 0.05, 0.08, max_count=40)
    assert np.isclose(pmf.sum(), 1.0)
    assert np.all(pmf >= 0)


def test_total_over_probability_decreases_with_line() -> None:
    probs = [
        convolved_total_over_probability(5.5, 4.3, line, 0.05, 0.08, max_count=40)
        for line in (7.5, 8.5, 9.5, 10.5)
    ]
    assert probs == sorted(probs, reverse=True)
    assert all(0 <= p <= 1 for p in probs)


def test_team_probability_decreases_with_line() -> None:
    probs = [nb_over_probability(5.0, line, 0.05) for line in (2.5, 3.5, 4.5, 5.5)]
    assert probs == sorted(probs, reverse=True)


def test_market_math() -> None:
    po, pu = devig_two_way(1.90, 1.90)
    assert np.isclose(po, 0.5)
    assert np.isclose(pu, 0.5)
    assert np.isclose(fair_odds(0.5), 2.0)
    assert np.isclose(expected_value(0.55, 2.0), 0.10)


def test_strength_shrinkage_starts_near_one() -> None:
    frame = pd.DataFrame(
        {
            "league_home_corners_r100": [5.5, 5.5],
            "league_away_corners_r100": [4.5, 4.5],
            "home_corners_for_venue5": [8.0, 8.0],
            "away_corners_for_venue5": [2.0, 2.0],
            "home_corners_against_venue5": [7.0, 7.0],
            "away_corners_against_venue5": [7.0, 7.0],
            "home_corners_for_ewm8": [8.0, 8.0],
            "away_corners_for_ewm8": [2.0, 2.0],
            "home_corners_against_ewm8": [7.0, 7.0],
            "away_corners_against_ewm8": [7.0, 7.0],
            "home_games_prior": [0, 30],
            "away_games_prior": [0, 30],
        }
    )
    out = add_corner_strength_features(frame, shrinkage_games=8)
    assert np.isclose(out.loc[0, "home_corner_attack_strength"], 1.0)
    assert out.loc[1, "home_corner_attack_strength"] > 1.0
    assert np.isclose(out.loc[0, "home_mu_strength_baseline"], 5.5)

from model_buk.decision import price_two_way_market
from model_buk.schema import validate_raw_history


def test_decision_gate_uses_devig_edge_and_ev() -> None:
    over, under = price_two_way_market(
        p_over=0.60,
        over_odds=2.00,
        under_odds=1.80,
        edge_threshold=0.05,
        ev_threshold=0.05,
    )
    assert over.edge > 0.05
    assert over.ev > 0.05
    assert over.decision == "BET"
    assert under.decision == "NO BET"


def test_raw_history_schema_rejects_duplicate_match_ids() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "home_team": ["A", "B"],
            "away_team": ["B", "A"],
            "home_corners": [1, 2],
            "away_corners": [2, 1],
            "home_shots": [10, 11],
            "away_shots": [8, 7],
            "home_sot": [3, 4],
            "away_sot": [2, 2],
            "home_goals": [1, 1],
            "away_goals": [0, 0],
            "home_xg": [1.1, 1.2],
            "away_xg": [0.8, 0.7],
            "home_ppda": [10, 10],
            "away_ppda": [11, 11],
            "home_deep": [5, 5],
            "away_deep": [4, 4],
            "source": ["historical", "historical"],
            "internal_match_id": [1, 1],
        }
    )
    try:
        validate_raw_history(frame)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate internal_match_id should fail validation")

from model_buk.inference import attach_total_market_price
from model_buk.config import CornerV02Config


def _test_cfg() -> CornerV02Config:
    return CornerV02Config(
        version="test",
        train_start="2020-01-01",
        reference_start="2025-01-01",
        max_count=40,
        random_seed=42,
        catboost_params={},
        shrinkage_games=8.0,
        min_rate=0.35,
        max_rate=2.5,
        edge_threshold=0.05,
        ev_threshold=0.05,
        max_bets_per_match=1,
        folds=(),
    )


def test_market_is_attached_only_after_prediction() -> None:
    prediction = {"total_markets": [{"line": 9.5, "p_over": 0.60}]}
    out = attach_total_market_price(
        prediction,
        _test_cfg(),
        line=9.5,
        over_odds=2.0,
        under_odds=1.8,
    )
    assert "market_check" not in prediction
    assert out["market_check"]["over"]["decision"] == "BET"
