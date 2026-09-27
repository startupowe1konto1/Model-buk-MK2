from __future__ import annotations

import json

import pandas as pd
import pytest

from model_buk.cli import build_parser
from model_buk.evaluation.forecast_blend import (
    blend_nested_predictions,
    walk_forward_blend_nested_predictions,
)


def _predictions(p_home: float, p_draw: float, p_away: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_row_id": "row-1",
                "league_code": "E0",
                "outer_fold": "outer",
                "kickoff": "2025-01-01T12:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
                "actual": "home",
                "p_home": p_home,
                "p_draw": p_draw,
                "p_away": p_away,
                "p_home_raw": p_home,
                "p_draw_raw": p_draw,
                "p_away_raw": p_away,
            }
        ]
    )


def test_fixed_blend_writes_weighted_predictions_and_provenance(tmp_path) -> None:
    elo = tmp_path / "elo.csv"
    gas = tmp_path / "gas.csv"
    _predictions(0.5, 0.3, 0.2).to_csv(elo, index=False)
    _predictions(0.3, 0.3, 0.4).to_csv(gas, index=False)

    manifest = blend_nested_predictions(
        {"elo": elo, "gas": gas}, {"elo": 1, "gas": 3}, tmp_path / "blend"
    )

    result = pd.read_csv(tmp_path / "blend" / "nested_oos_predictions.csv")
    assert result.loc[0, ["p_home", "p_draw", "p_away"]].tolist() == pytest.approx(
        [0.35, 0.3, 0.35]
    )
    assert result.loc[0, "selected_family"] == "forecast_blend"
    assert manifest["weights"] == {"elo": 0.25, "gas": 0.75}
    saved = json.loads((tmp_path / "blend" / "manifest.json").read_text())
    assert saved["weight_selection"] == "external_not_optimised_by_runner"


def test_fixed_blend_rejects_misaligned_inputs(tmp_path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _predictions(0.5, 0.3, 0.2).to_csv(first, index=False)
    changed = _predictions(0.3, 0.3, 0.4)
    changed.loc[0, "actual"] = "away"
    changed.to_csv(second, index=False)

    with pytest.raises(ValueError, match="not row-aligned"):
        blend_nested_predictions(
            {"first": first, "second": second},
            {"first": 1, "second": 1},
            tmp_path / "blend",
        )


def test_blend_cli_accepts_named_inputs_and_weights() -> None:
    args = build_parser().parse_args(
        [
            "blend-nested-goals",
            "--prediction", "elo=elo.csv",
            "--prediction", "gas=gas.csv",
            "--weight", "elo=1",
            "--weight", "gas=1",
            "--outdir", "out",
        ]
    )
    assert args.command == "blend-nested-goals"
    assert args.prediction == ["elo=elo.csv", "gas=gas.csv"]


def test_walk_forward_blend_selects_weight_from_prior_fold_only(tmp_path) -> None:
    first_rows = []
    second_rows = []
    for index, (fold, actual) in enumerate(
        [("fold-1", "home"), ("fold-2", "away"), ("fold-3", "draw")], start=1
    ):
        common = {
            "source_row_id": f"row-{index}",
            "league_code": "E0",
            "outer_fold": fold,
            "kickoff": f"202{index + 1}-01-01T12:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "actual": actual,
        }
        first_rows.append({**common, "p_home": 0.8, "p_draw": 0.1, "p_away": 0.1})
        second_rows.append({**common, "p_home": 0.1, "p_draw": 0.1, "p_away": 0.8})
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    pd.DataFrame(first_rows).to_csv(first, index=False)
    pd.DataFrame(second_rows).to_csv(second, index=False)

    manifest = walk_forward_blend_nested_predictions(
        {"first": first, "second": second},
        tmp_path / "adaptive",
        weight_grid=(0.0, 0.5, 1.0),
    )

    selections = pd.read_csv(tmp_path / "adaptive" / "blend_selections.csv")
    predictions = pd.read_csv(tmp_path / "adaptive" / "nested_oos_predictions.csv")
    assert selections["selected_weight_first_input"].tolist() == [1.0, 0.5]
    assert predictions["outer_fold"].tolist() == ["fold-2", "fold-3"]
    assert manifest["warmup_outer_fold"] == "fold-1"


def test_walk_forward_blend_cli_accepts_two_named_inputs() -> None:
    args = build_parser().parse_args(
        [
            "blend-nested-goals-walk-forward",
            "--prediction", "elo=elo.csv",
            "--prediction", "gas=gas.csv",
            "--outdir", "out",
        ]
    )
    assert args.command == "blend-nested-goals-walk-forward"
    assert args.prediction == ["elo=elo.csv", "gas=gas.csv"]
