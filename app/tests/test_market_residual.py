from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from model_buk.cli import build_parser
from model_buk.evaluation.archive_residual_runner import _load_residual_plan
from model_buk.models.market_residual import (
    MarketResidualConfig,
    build_market_residual_features,
    fit_market_residual,
)


def _matches() -> pd.DataFrame:
    rows = []
    for index in range(7):
        rows.append(
            {
                "source_row_id": f"row-{index}",
                "league": "E0",
                "kickoff": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=7 * index),
                "home_team": "Home",
                "away_team": "Away",
                "home_goals": 1 + index % 2,
                "away_goals": index % 2,
                "home_shots": 10 + index,
                "away_shots": 8 + index,
                "home_sot": 4 + index % 3,
                "away_sot": 2 + index % 2,
                "home_corners": 5 + index % 2,
                "away_corners": 3 + index % 2,
            }
        )
    return pd.DataFrame(rows)


def test_current_match_statistics_cannot_change_its_residual_features() -> None:
    matches = _matches()
    original = build_market_residual_features(matches)
    changed = matches.copy()
    changed.loc[6, ["home_goals", "home_shots", "home_sot", "home_corners"]] = 99
    rebuilt = build_market_residual_features(changed)
    feature_columns = [column for column in original if column.endswith("_diff")]

    assert original.loc[6, feature_columns].tolist() == rebuilt.loc[6, feature_columns].tolist()


def test_market_residual_probabilities_are_normalised_and_learn_signal() -> None:
    rng = np.random.default_rng(7)
    signal = np.linspace(-2, 2, 300)
    features = pd.DataFrame({"signal": signal})
    actual = np.where(signal > 0.5, 0, np.where(signal < -0.5, 2, 1))
    market = np.full((len(features), 3), 1 / 3)
    model = fit_market_residual(
        features,
        market,
        actual,
        config=MarketResidualConfig(
            l2_strength=0.01,
            learning_rate=0.03,
            epochs=80,
            batch_size=64,
            seed=int(rng.integers(0, 100)),
        ),
        feature_columns=("signal",),
    )

    probabilities = model.predict_proba(features, market)
    baseline_loss = -np.log(market[np.arange(len(actual)), actual]).mean()
    model_loss = -np.log(probabilities[np.arange(len(actual)), actual]).mean()
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert model_loss < baseline_loss


def test_missing_features_are_imputed_from_training_values() -> None:
    features = pd.DataFrame({"signal": [1.0, np.nan, -1.0, 0.5]})
    market = np.full((4, 3), 1 / 3)
    model = fit_market_residual(
        features,
        market,
        np.array([0, 1, 2, 0]),
        config=MarketResidualConfig(epochs=2, batch_size=2),
        feature_columns=("signal",),
    )
    probabilities = model.predict_proba(pd.DataFrame({"signal": [np.nan]}), np.full((1, 3), 1 / 3))
    assert np.isfinite(probabilities).all()


def test_example_residual_plan_is_valid_and_cli_accepts_explicit_assumption() -> None:
    plan = Path(__file__).parents[1] / "config" / "market_residual_plan.example.json"
    candidates, folds = _load_residual_plan(plan)
    args = build_parser().parse_args(
        [
            "nested-opening-market-residual",
            "--archives", "source.zip",
            "--plan", str(plan),
            "--outdir", "out",
            "--assume-opening-available",
        ]
    )
    assert len(candidates) == 3
    assert len(folds) == 3
    assert args.assume_opening_available is True


def test_league_residual_plan_uses_partial_pooling_penalties() -> None:
    plan = Path(__file__).parents[1] / "config" / "market_residual_league_plan.example.json"
    candidates, folds = _load_residual_plan(plan)
    assert len(folds) == 3
    assert len(candidates) == 4
    assert all(candidate.include_league_effects for candidate in candidates)
    assert {candidate.league_l2_multiplier for candidate in candidates} == {5.0, 10.0, 20.0, 100.0}
