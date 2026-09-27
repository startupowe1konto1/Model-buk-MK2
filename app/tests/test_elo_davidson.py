from __future__ import annotations

import numpy as np
import pandas as pd

from model_buk.goals_backtest import GoalsFold, walk_forward_elo_davidson
from model_buk.models.elo_davidson import (
    EloDavidsonConfig,
    EloDavidsonModel,
    fit_elo_davidson,
)


def _history() -> pd.DataFrame:
    teams = ("A", "B", "C", "D")
    rows = []
    for index in range(80):
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 2),
                "home_team": teams[index % 4],
                "away_team": teams[(index + 1) % 4],
                "home_goals": (index + 1) % 3,
                "away_goals": index % 2,
            }
        )
    return pd.DataFrame(rows)


def test_probabilities_are_normalised_and_respect_rating_order() -> None:
    model = EloDavidsonModel(
        ratings={"A": 1600.0, "B": 1400.0},
        config=EloDavidsonConfig(),
        home_advantage_points=0.0,
        draw_parameter=0.7,
    )
    probabilities = model.probabilities("A", "B")

    np.testing.assert_allclose(sum(probabilities), 1.0)
    assert probabilities[0] > probabilities[2]


def test_surprise_result_moves_ratings_in_the_expected_direction() -> None:
    model = EloDavidsonModel(
        ratings={"A": 1600.0, "B": 1400.0},
        config=EloDavidsonConfig(k_factor=20.0),
        home_advantage_points=0.0,
        draw_parameter=0.7,
    )
    before = dict(model.ratings)
    model.observe_match("A", "B", 0, 1)

    assert model.ratings["A"] < before["A"]
    assert model.ratings["B"] > before["B"]
    assert np.isclose(sum(model.ratings.values()), sum(before.values()))


def test_fit_and_walk_forward_generate_valid_probabilities() -> None:
    model = fit_elo_davidson(_history())
    assert model.teams == ("A", "B", "C", "D")
    assert model.draw_parameter > 0.0

    history = _history().rename(columns={"date": "kickoff"})
    history["league"] = "TEST"
    predictions = walk_forward_elo_davidson(
        history,
        [GoalsFold("test", "2024-04-01", "2024-07-01")],
        min_train_matches=30,
    )

    assert not predictions.empty
    assert np.allclose(predictions[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0)
