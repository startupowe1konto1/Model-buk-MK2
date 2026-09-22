from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model_buk.evaluation import nested_goals
from model_buk.evaluation.nested_goals import (
    GoalModelCandidate,
    NestedGoalsFold,
    nested_walk_forward_goals,
)
from model_buk.goals_backtest import GoalsFold


def _matches() -> pd.DataFrame:
    teams = ("A", "B", "C", "D")
    rows = []
    for index in range(70):
        home = teams[index % len(teams)]
        away = teams[(index + 1) % len(teams)]
        rows.append(
            {
                "league": "TEST",
                "kickoff": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index),
                "home_team": home,
                "away_team": away,
                "home_goals": (index + 1) % 4,
                "away_goals": index % 3,
            }
        )
    return pd.DataFrame(rows)


def _nested_fold() -> NestedGoalsFold:
    return NestedGoalsFold(
        "outer-1",
        GoalsFold("outer", "2024-02-10", "2024-02-20"),
        (
            GoalsFold("inner-1", "2024-01-20", "2024-01-30"),
            GoalsFold("inner-2", "2024-01-30", "2024-02-10"),
        ),
    )


def _prediction(label: str, kickoff: str, probabilities: tuple[float, float, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fold": [label],
            "league": ["TEST"],
            "kickoff": [pd.Timestamp(kickoff)],
            "home_team": [f"H-{label}"],
            "away_team": [f"A-{label}"],
            "actual": ["home"],
            "cold_start": [False],
            "p_home": [probabilities[0]],
            "p_draw": [probabilities[1]],
            "p_away": [probabilities[2]],
        }
    )


def test_selection_uses_only_inner_oof_and_runs_only_winner_on_outer(monkeypatch):
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_walk(candidate, _matches, folds, **_kwargs):
        labels = tuple(fold.label for fold in folds)
        calls.append((candidate.name, labels))
        if "outer" in labels:
            assert candidate.name == "alpha"  # beta must never see the outer holdout.
            return _prediction("outer", "2024-02-11", (0.8, 0.1, 0.1))
        probability = (0.8, 0.1, 0.1) if candidate.name == "alpha" else (0.2, 0.4, 0.4)
        return pd.concat(
            [
                _prediction("inner-1", "2024-01-21", probability),
                _prediction("inner-2", "2024-02-01", probability),
            ],
            ignore_index=True,
        )

    monkeypatch.setattr(nested_goals, "_walk_forward", fake_walk)
    result = nested_walk_forward_goals(
        pd.DataFrame(),
        [
            GoalModelCandidate("beta", "negative_binomial"),
            GoalModelCandidate("alpha", "negative_binomial"),
        ],
        [_nested_fold()],
        min_train_matches=1,
        calibration_min_history=1,
    )

    assert result.selections.loc[0, "selected_candidate"] == "alpha"
    assert calls == [
        ("beta", ("inner-1", "inner-2")),
        ("alpha", ("inner-1", "inner-2")),
        ("alpha", ("outer",)),
    ]
    assert result.inner_scores["outer_fold"].eq("outer-1").all()
    assert result.outer_predictions["kickoff"].min() >= pd.Timestamp("2024-02-10")
    assert result.outer_predictions["selected_parameters"].iloc[0] == {}
    assert "calibration_status" in result.outer_predictions


def test_ties_are_broken_deterministically_by_candidate_name(monkeypatch):
    def fake_walk(candidate, _matches, folds, **_kwargs):
        label = folds[0].label
        kickoff = "2024-02-11" if label == "outer" else "2024-01-21"
        return _prediction(label, kickoff, (0.6, 0.2, 0.2))

    monkeypatch.setattr(nested_goals, "_walk_forward", fake_walk)
    result = nested_walk_forward_goals(
        pd.DataFrame(),
        [
            GoalModelCandidate("zeta", "negative_binomial"),
            GoalModelCandidate("alpha", "negative_binomial"),
        ],
        [_nested_fold()],
        min_train_matches=1,
        calibration_min_history=1,
    )

    assert result.selections.loc[0, "selected_candidate"] == "alpha"


def test_nested_fold_and_candidate_validation_reject_leakage_overlap_and_invalid_parameters():
    with pytest.raises(ValueError, match="end on or before"):
        NestedGoalsFold(
            "bad",
            GoalsFold("outer", "2024-02-01", "2024-02-10"),
            (GoalsFold("inner", "2024-01-25", "2024-02-02"),),
        )
    with pytest.raises(ValueError, match="non-overlapping"):
        NestedGoalsFold(
            "overlap",
            GoalsFold("outer", "2024-02-10", "2024-02-20"),
            (
                GoalsFold("inner-1", "2024-01-20", "2024-01-31"),
                GoalsFold("inner-2", "2024-01-30", "2024-02-05"),
            ),
        )
    with pytest.raises(ValueError, match="unsupported parameters"):
        GoalModelCandidate("bad", "negative_binomial", {"half_life_days": 90})
    with pytest.raises(ValueError, match="unique"):
        nested_walk_forward_goals(
            _matches(),
            [
                GoalModelCandidate("same", "negative_binomial"),
                GoalModelCandidate("same", "dixon_coles"),
            ],
            [_nested_fold()],
            min_train_matches=10,
        )


def test_real_nested_walk_forward_returns_normalised_calibrated_outer_probabilities():
    result = nested_walk_forward_goals(
        _matches(),
        [
            GoalModelCandidate("dc", "dixon_coles", {"half_life_days": 60.0}),
            GoalModelCandidate("gas", "gas_poisson"),
        ],
        [_nested_fold()],
        min_train_matches=12,
        calibration_min_history=1,
    )

    assert not result.inner_scores.empty
    assert not result.outer_predictions.empty
    assert np.allclose(
        result.outer_predictions[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0
    )
    assert result.outer_predictions["selected_candidate"].nunique() == 1
    assert result.selections.loc[0, "calibration_status"] == ("calibrated",)
