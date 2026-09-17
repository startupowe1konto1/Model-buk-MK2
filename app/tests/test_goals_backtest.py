from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from model_buk.goals_backtest import (
    GoalsFold,
    build_market_1x2_benchmark,
    compare_dixon_coles_configurations,
    logarithmic_pool_1x2,
    score_goals_predictions,
    tune_logarithmic_pool_1x2,
    walk_forward_dixon_coles,
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
