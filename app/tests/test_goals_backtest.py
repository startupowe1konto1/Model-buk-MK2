from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from model_buk.goals_backtest import (
    GoalsFold,
    build_market_1x2_benchmark,
    calibrate_goal_predictions,
    compare_goal_model_predictions,
    compare_dixon_coles_configurations,
    logarithmic_pool_1x2,
    score_goals_predictions,
    tune_logarithmic_pool_1x2,
    walk_forward_dixon_coles,
    walk_forward_negative_binomial,
)


def _matches() -> pd.DataFrame:
    teams = ("A", "B", "C", "D")
    rows = []
    for index in range(80):
        home = teams[index % 4]
        away = teams[(index + 1 + index // 4) % 4]
        if home == away:
            away = teams[(teams.index(home) + 1) % 4]
        rows.append(
            {
                "league": "TEST",
                "kickoff": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 3),
                "home_team": home,
                "away_team": away,
                "home_goals": (index + teams.index(home)) % 4,
                "away_goals": (index + teams.index(away)) % 3,
            }
        )
    return pd.DataFrame(rows)


def test_walk_forward_predictions_use_only_matches_before_fold():
    matches = _matches()
    predictions = walk_forward_dixon_coles(
        matches,
        [GoalsFold("holdout", "2024-06-01", "2024-08-01")],
        min_train_matches=40,
        half_life_days=90,
    )

    assert not predictions.empty
    assert predictions["kickoff"].min() >= pd.Timestamp("2024-06-01")
    assert predictions["kickoff"].max() < pd.Timestamp("2024-08-01")
    assert np.allclose(predictions[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0)


def test_scores_probabilistic_forecasts():
    predictions = pd.DataFrame(
        {
            "p_home": [0.7, 0.2, 0.2],
            "p_draw": [0.2, 0.6, 0.2],
            "p_away": [0.1, 0.2, 0.6],
            "actual": ["home", "draw", "away"],
            "cold_start": [False, False, True],
        }
    )
    scores = score_goals_predictions(predictions)

    assert scores["n"] == 3
    assert scores["accuracy"] == 1.0
    assert 0.0 < scores["log_loss"] < 1.0
    assert 0.0 <= scores["rps"] < scores["brier"]
    assert scores["cold_start_rate"] == pytest.approx(1 / 3)


def test_fold_rejects_non_chronological_bounds():
    with pytest.raises(ValueError, match="later"):
        GoalsFold("bad", "2024-08-01", "2024-08-01").bounds()


def test_market_benchmark_requires_explicit_price_kind_and_devigs():
    rows = []
    for selection, price in (("home", 2.0), ("draw", 4.0), ("away", 4.0)):
        rows.append(
            {
                "source_row_id": "m1",
                "provider": "market_average",
                "odds_kind": "closing",
                "market": "1x2",
                "selection": selection,
                "odds": price,
            }
        )
    benchmark = build_market_1x2_benchmark(pd.DataFrame(rows), odds_kind="closing")

    assert benchmark.loc[0, "market_p_home"] == pytest.approx(0.5)
    assert benchmark.loc[0, "market_p_draw"] == pytest.approx(0.25)
    assert benchmark.loc[0, "market_p_away"] == pytest.approx(0.25)
    with pytest.raises(ValueError, match="opening.*closing"):
        build_market_1x2_benchmark(pd.DataFrame(rows), odds_kind="automatic")


def test_configuration_comparison_is_ranked_by_proper_score():
    comparison = compare_dixon_coles_configurations(
        _matches(),
        [GoalsFold("holdout", "2024-06-01", "2024-08-01")],
        half_life_grid=[60.0, 180.0],
        l2_grid=[0.0, 1.0],
        min_train_matches=40,
    )

    assert len(comparison) == 4
    assert comparison["log_loss"].is_monotonic_increasing
    assert set(comparison["half_life_days"]) == {60.0, 180.0}
    assert set(comparison["l2_strength"]) == {0.0, 1.0}


def test_logarithmic_pool_has_coherent_endpoints_and_tuning():
    frame = pd.DataFrame(
        {
            "model_p_home": [0.70, 0.20],
            "model_p_draw": [0.20, 0.20],
            "model_p_away": [0.10, 0.60],
            "market_p_home": [0.50, 0.30],
            "market_p_draw": [0.30, 0.30],
            "market_p_away": [0.20, 0.40],
            "actual": ["home", "away"],
        }
    )

    model_only = logarithmic_pool_1x2(frame, model_weight=1.0)
    market_only = logarithmic_pool_1x2(frame, model_weight=0.0)
    tuned = tune_logarithmic_pool_1x2(frame, model_weights=[0.0, 0.5, 1.0])

    assert model_only["p_home"].tolist() == pytest.approx(frame["model_p_home"])
    assert market_only["p_home"].tolist() == pytest.approx(frame["market_p_home"])
    assert np.allclose(model_only[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0)
    assert set(tuned["model_weight"]) == {0.0, 0.5, 1.0}
    assert tuned["log_loss"].is_monotonic_increasing


def test_negative_binomial_walk_forward_and_paired_comparison():
    fold = GoalsFold("holdout", "2024-06-01", "2024-08-01")
    dixon_coles = walk_forward_dixon_coles(
        _matches(), [fold], min_train_matches=40, half_life_days=90,
    )
    negative_binomial = walk_forward_negative_binomial(
        _matches(), [fold], min_train_matches=40, l2_strength=0.2,
    )

    comparison = compare_goal_model_predictions(
        {"dixon_coles": dixon_coles, "negative_binomial": negative_binomial}
    )

    assert set(comparison["model"]) == {"dixon_coles", "negative_binomial"}
    assert comparison["common_n"].nunique() == 1
    assert comparison["common_n"].iloc[0] > 0
    assert comparison["log_loss"].is_monotonic_increasing
    assert np.allclose(
        negative_binomial[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0
    )


def test_goal_calibration_uses_only_earlier_predictions_and_keeps_raw_values():
    probabilities = pd.DataFrame(
        {
            "league": ["TEST"] * 8,
            "kickoff": pd.date_range("2024-01-01", periods=8, tz="UTC"),
            "home_team": [f"H{i}" for i in range(8)],
            "away_team": [f"A{i}" for i in range(8)],
            "actual": ["home", "draw", "away", "home", "draw", "away", "home", "draw"],
            "p_home": [0.8, 0.7, 0.1, 0.7, 0.2, 0.1, 0.8, 0.2],
            "p_draw": [0.1, 0.2, 0.2, 0.2, 0.7, 0.2, 0.1, 0.7],
            "p_away": [0.1, 0.1, 0.7, 0.1, 0.1, 0.7, 0.1, 0.1],
        }
    )
    calibrated = calibrate_goal_predictions(
        probabilities,
        [GoalsFold("outer", "2024-01-05", "2024-01-09")],
        min_history=4,
    )

    assert calibrated.loc[:3, "calibration_status"].eq("outside_fold").all()
    assert calibrated.loc[4:, "calibration_status"].eq("calibrated").all()
    assert calibrated.loc[4:, "calibrated"].all()
    assert calibrated["p_home_raw"].tolist() == probabilities["p_home"].tolist()
    assert np.allclose(calibrated[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0)
