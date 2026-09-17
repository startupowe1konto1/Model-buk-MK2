from __future__ import annotations

import pandas as pd

from model_buk.goals_backtest import GoalsFold, walk_forward_gas_poisson
from model_buk.models.gas_poisson import GasPoissonConfig, GasPoissonModel, fit_gas_poisson


def _history() -> pd.DataFrame:
    teams = ("A", "B", "C", "D")
    rows = []
    for index in range(60):
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 3),
                "home_team": teams[index % 4],
                "away_team": teams[(index + 1) % 4],
                "home_goals": index % 3,
                "away_goals": index % 2,
            }
        )
    return pd.DataFrame(rows)


def test_positive_attack_surprise_raises_next_expected_goals():
    model = GasPoissonModel(
        intercept=0.0,
        home_advantage=0.0,
        attack={"A": 0.0, "B": 0.0},
        defence={"A": 0.0, "B": 0.0},
        config=GasPoissonConfig(attack_step=0.1, defence_step=0.1, persistence=1.0),
    )
    before, _ = model.expected_goals("A", "B")
    model.observe_match("A", "B", 4, 0)
    after, _ = model.expected_goals("A", "B")

    assert after > before


def test_fit_replays_history_and_centers_states():
    model = fit_gas_poisson(_history())

    assert abs(sum(model.attack.values())) < 1e-10
    assert abs(sum(model.defence.values())) < 1e-10
    assert all(rate > 0.0 for rate in model.expected_goals("A", "B"))


def test_walk_forward_gas_outputs_probabilities():
    history = _history().rename(columns={"date": "kickoff"})
    history["league"] = "TEST"
    predictions = walk_forward_gas_poisson(
        history,
        [GoalsFold("test", "2024-05-01", "2024-07-01")],
        min_train_matches=30,
    )

    assert not predictions.empty
    assert (predictions[["p_home", "p_draw", "p_away"]].sum(axis=1) - 1.0).abs().max() < 1e-12
