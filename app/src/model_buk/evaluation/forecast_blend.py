"""Build an auditable fixed-weight blend of aligned nested OOS forecasts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from model_buk.artifact_identity import sha256_file


_IDENTITY_COLUMNS = (
    "source_row_id",
    "league_code",
    "outer_fold",
    "kickoff",
    "home_team",
    "away_team",
    "actual",
)
_PROBABILITY_COLUMNS = ("p_home", "p_draw", "p_away")
_RAW_PROBABILITY_COLUMNS = ("p_home_raw", "p_draw_raw", "p_away_raw")


def _normalised_weights(
    inputs: Mapping[str, str | Path], weights: Mapping[str, float]
) -> dict[str, float]:
    if len(inputs) < 2:
        raise ValueError("a forecast blend requires at least two inputs")
    if set(inputs) != set(weights):
        raise ValueError("input and weight labels must match exactly")
    numeric = {name: float(weights[name]) for name in inputs}
    if not all(np.isfinite(value) and value >= 0 for value in numeric.values()):
        raise ValueError("blend weights must be finite and non-negative")
    total = sum(numeric.values())
    if total <= 0:
        raise ValueError("at least one blend weight must be positive")
    return {name: value / total for name, value in numeric.items()}


def blend_nested_predictions(
    inputs: Mapping[str, str | Path],
    weights: Mapping[str, float],
    outdir: str | Path,
) -> dict[str, object]:
    """Blend already-held-out forecasts using externally fixed weights.

    This function deliberately does not optimise weights. Selecting weights from
    the same outer outcomes would contaminate the held-out evaluation.
    """
    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    normalised = _normalised_weights(inputs, weights)

    frames: dict[str, pd.DataFrame] = {}
    for name, path in inputs.items():
        frame = pd.read_csv(path)
        required = set(_IDENTITY_COLUMNS + _PROBABILITY_COLUMNS)
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"input {name!r} is missing columns: {', '.join(missing)}")
        if frame["source_row_id"].duplicated().any():
            raise ValueError(f"input {name!r} contains duplicate source_row_id values")
        frames[name] = frame.sort_values("source_row_id").reset_index(drop=True)

    reference_name = next(iter(inputs))
    reference = frames[reference_name]
    for name, frame in frames.items():
        if len(frame) != len(reference) or not frame.loc[:, _IDENTITY_COLUMNS].equals(
            reference.loc[:, _IDENTITY_COLUMNS]
        ):
            raise ValueError(f"input {name!r} is not row-aligned with {reference_name!r}")
        probabilities = frame.loc[:, _PROBABILITY_COLUMNS].to_numpy(dtype=float)
        if not np.isfinite(probabilities).all() or (probabilities < 0).any():
            raise ValueError(f"input {name!r} contains invalid probabilities")
        if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-10):
            raise ValueError(f"input {name!r} probabilities are not normalised")

    blended = reference.copy()
    probability_matrix = sum(
        normalised[name]
        * frames[name].loc[:, _PROBABILITY_COLUMNS].to_numpy(dtype=float)
        for name in inputs
    )
    blended.loc[:, _PROBABILITY_COLUMNS] = probability_matrix
    if all(set(_RAW_PROBABILITY_COLUMNS) <= set(frame.columns) for frame in frames.values()):
        raw_matrix = sum(
            normalised[name]
            * frames[name].loc[:, _RAW_PROBABILITY_COLUMNS].to_numpy(dtype=float)
            for name in inputs
        )
        blended.loc[:, _RAW_PROBABILITY_COLUMNS] = raw_matrix
    blended["selected_candidate"] = "fixed-weight-blend"
    blended["selected_family"] = "forecast_blend"
    parameters = {"weights": normalised, "components": list(inputs)}
    blended["selected_parameters"] = [parameters] * len(blended)
    blended["calibrated"] = True
    blended["calibration_status"] = "component-calibrated-fixed-blend"

    output.mkdir(parents=True, exist_ok=True)
    predictions_path = output / "nested_oos_predictions.csv"
    blended.to_csv(predictions_path, index=False)
    manifest: dict[str, object] = {
        "status": "completed",
        "method": "fixed_weight_probability_blend",
        "weight_selection": "external_not_optimised_by_runner",
        "weights": normalised,
        "prediction_rows": len(blended),
        "inputs": [
            {
                "name": name,
                "path": str(Path(path).resolve()),
                "sha256": sha256_file(path),
            }
            for name, path in inputs.items()
        ],
        "reports": ["nested_oos_predictions.csv"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def walk_forward_blend_nested_predictions(
    inputs: Mapping[str, str | Path],
    outdir: str | Path,
    *,
    weight_grid: tuple[float, ...] = tuple(np.linspace(0.0, 1.0, 21)),
) -> dict[str, object]:
    """Select a two-model blend weight only from completed outer folds.

    The first chronological outer fold is used solely as meta-training history.
    Every later fold receives the weight minimising log loss on all earlier
    outer-fold predictions, so its own outcomes cannot affect its weight.
    """
    if len(inputs) != 2:
        raise ValueError("walk-forward blending currently requires exactly two inputs")
    grid = tuple(float(value) for value in weight_grid)
    if not grid or len(set(grid)) != len(grid):
        raise ValueError("weight_grid must contain unique values")
    if not all(np.isfinite(value) and 0 <= value <= 1 for value in grid):
        raise ValueError("weight_grid values must be finite and between zero and one")

    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    names = tuple(inputs)
    frames: dict[str, pd.DataFrame] = {}
    for name, path in inputs.items():
        frame = pd.read_csv(path)
        required = set(_IDENTITY_COLUMNS + _PROBABILITY_COLUMNS)
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"input {name!r} is missing columns: {', '.join(missing)}")
        if frame["source_row_id"].duplicated().any():
            raise ValueError(f"input {name!r} contains duplicate source_row_id values")
        frames[name] = frame.sort_values("source_row_id").reset_index(drop=True)
    reference = frames[names[0]]
    for name, frame in frames.items():
        if len(frame) != len(reference) or not frame.loc[:, list(_IDENTITY_COLUMNS)].equals(
            reference.loc[:, list(_IDENTITY_COLUMNS)]
        ):
            raise ValueError(f"input {name!r} is not row-aligned with {names[0]!r}")

    fold_starts = reference.assign(
        _kickoff=pd.to_datetime(reference["kickoff"], errors="raise", utc=True)
    ).groupby("outer_fold", sort=False)["_kickoff"].min()
    fold_order = tuple(fold_starts.sort_values().index.astype(str))
    if len(fold_order) < 2:
        raise ValueError("walk-forward blending requires at least two outer folds")
    actual_index = reference["actual"].map({"home": 0, "draw": 1, "away": 2})
    if actual_index.isna().any():
        raise ValueError("actual must contain only home, draw or away")
    actual = actual_index.to_numpy(dtype=int)
    matrices = {
        name: frames[name].loc[:, list(_PROBABILITY_COLUMNS)].to_numpy(dtype=float)
        for name in names
    }
    for name, matrix in matrices.items():
        if not np.isfinite(matrix).all() or (matrix < 0).any():
            raise ValueError(f"input {name!r} contains invalid probabilities")
        if not np.allclose(matrix.sum(axis=1), 1.0, atol=1e-10):
            raise ValueError(f"input {name!r} probabilities are not normalised")

    output_frames: list[pd.DataFrame] = []
    selections: list[dict[str, object]] = []
    for fold_index, fold in enumerate(fold_order[1:], start=1):
        train = reference["outer_fold"].astype(str).isin(fold_order[:fold_index]).to_numpy()
        test = reference["outer_fold"].astype(str).eq(fold).to_numpy()
        scores: list[tuple[float, float, float]] = []
        for weight in grid:
            probabilities = weight * matrices[names[0]] + (1 - weight) * matrices[names[1]]
            loss = -np.log(
                np.clip(probabilities[np.arange(len(reference)), actual], 1e-15, 1.0)
            )
            scores.append((float(loss[train].mean()), abs(weight - 0.5), weight))
        selection_loss, _distance, selected_weight = min(scores)
        probabilities = (
            selected_weight * matrices[names[0]]
            + (1 - selected_weight) * matrices[names[1]]
        )
        blended = reference.loc[test].copy()
        blended.loc[:, _PROBABILITY_COLUMNS] = probabilities[test]
        weights = {names[0]: selected_weight, names[1]: 1 - selected_weight}
        blended["selected_candidate"] = "walk-forward-blend"
        blended["selected_family"] = "forecast_blend"
        blended["selected_parameters"] = [
            {"weights": weights, "trained_on_outer_folds": list(fold_order[:fold_index])}
        ] * len(blended)
        blended["calibrated"] = True
        blended["calibration_status"] = "component-calibrated-walk-forward-blend"
        output_frames.append(blended)
        selections.append(
            {
                "outer_fold": fold,
                "selected_weight_first_input": selected_weight,
                "first_input": names[0],
                "second_input": names[1],
                "selection_log_loss": selection_loss,
                "training_outer_folds": tuple(fold_order[:fold_index]),
                "training_rows": int(train.sum()),
                "prediction_rows": int(test.sum()),
            }
        )

    combined = pd.concat(output_frames, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output / "nested_oos_predictions.csv", index=False)
    pd.DataFrame(selections).to_csv(output / "blend_selections.csv", index=False)
    manifest: dict[str, object] = {
        "status": "completed",
        "method": "walk_forward_log_loss_probability_blend",
        "weight_grid": list(grid),
        "warmup_outer_fold": fold_order[0],
        "evaluated_outer_folds": list(fold_order[1:]),
        "prediction_rows": len(combined),
        "inputs": [
            {"name": name, "path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for name, path in inputs.items()
        ],
        "reports": ["nested_oos_predictions.csv", "blend_selections.csv"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


__all__ = ["blend_nested_predictions", "walk_forward_blend_nested_predictions"]
