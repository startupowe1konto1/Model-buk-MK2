"""Generate per-league nested OOS forecasts from football-data.co.uk archives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from model_buk.evaluation.nested_goals import (
    GoalModelCandidate,
    NestedGoalsFold,
    nested_walk_forward_goals,
)
from model_buk.goals_backtest import GoalsFold
from model_buk.models.gas_poisson import GasPoissonConfig
from model_buk.models.elo_davidson import EloDavidsonConfig
from model_buk.artifact_identity import sha256_file
from model_buk.ingestion.football_data_uk import load_football_data_uk_archives


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _fold(value: Any, *, name: str) -> GoalsFold:
    row = _mapping(value, name=name)
    if set(row) != {"label", "start", "end"}:
        raise ValueError(f"{name} must contain exactly label, start and end")
    return GoalsFold(label=str(row["label"]), start=str(row["start"]), end=str(row["end"]))


def _load_plan(path: str | Path) -> tuple[tuple[GoalModelCandidate, ...], tuple[NestedGoalsFold, ...], dict[str, int]]:
    try:
        plan = _mapping(json.loads(Path(path).read_text(encoding="utf-8")), name="plan")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read nested forecast plan: {exc}") from None
    if set(plan) - {"candidates", "folds", "training"}:
        raise ValueError(f"plan contains unknown fields: {', '.join(sorted(set(plan) - {'candidates', 'folds', 'training'}))}")
    candidates_value = plan.get("candidates")
    folds_value = plan.get("folds")
    training_value = _mapping(plan.get("training", {}), name="training")
    if set(training_value) - {"min_train_matches", "max_goals", "calibration_min_history"}:
        raise ValueError("training contains unknown fields")
    if not isinstance(candidates_value, list) or len(candidates_value) < 2:
        raise ValueError("plan.candidates must be a JSON array with at least two candidates")
    if not isinstance(folds_value, list) or not folds_value:
        raise ValueError("plan.folds must be a non-empty JSON array")
    candidates: list[GoalModelCandidate] = []
    for index, value in enumerate(candidates_value):
        row = _mapping(value, name=f"candidates[{index}]")
        if set(row) - {"name", "family", "parameters"}:
            raise ValueError(f"candidates[{index}] contains unknown fields")
        family = str(row.get("family", ""))
        parameters = dict(
            _mapping(row.get("parameters", {}), name=f"candidates[{index}].parameters")
        )
        if family == "gas_poisson" and "config" in parameters:
            config = _mapping(
                parameters["config"], name=f"candidates[{index}].parameters.config"
            )
            allowed_config = set(GasPoissonConfig.__dataclass_fields__)
            unknown_config = sorted(set(config) - allowed_config)
            if unknown_config:
                raise ValueError(
                    f"candidates[{index}].parameters.config contains unknown fields: "
                    f"{', '.join(unknown_config)}"
                )
            parameters["config"] = GasPoissonConfig(**config)
        if family == "elo_davidson" and "config" in parameters:
            config = _mapping(
                parameters["config"], name=f"candidates[{index}].parameters.config"
            )
            allowed_config = set(EloDavidsonConfig.__dataclass_fields__)
            unknown_config = sorted(set(config) - allowed_config)
            if unknown_config:
                raise ValueError(
                    f"candidates[{index}].parameters.config contains unknown fields: "
                    f"{', '.join(unknown_config)}"
                )
            parameters["config"] = EloDavidsonConfig(**config)
        candidates.append(
            GoalModelCandidate(
                name=str(row.get("name", "")),
                family=family,
                parameters=parameters,
            )
        )
    folds: list[NestedGoalsFold] = []
    for index, value in enumerate(folds_value):
        row = _mapping(value, name=f"folds[{index}]")
        if set(row) != {"label", "outer_fold", "inner_folds"}:
            raise ValueError(f"folds[{index}] must contain exactly label, outer_fold and inner_folds")
        if not isinstance(row["inner_folds"], list):
            raise ValueError(f"folds[{index}].inner_folds must be a JSON array")
        folds.append(
            NestedGoalsFold(
                label=str(row["label"]),
                outer_fold=_fold(row["outer_fold"], name=f"folds[{index}].outer_fold"),
                inner_folds=tuple(
                    _fold(item, name=f"folds[{index}].inner_folds[{inner_index}]")
                    for inner_index, item in enumerate(row["inner_folds"])
                ),
            )
        )
    training = {
        "min_train_matches": training_value.get("min_train_matches", 100),
        "max_goals": training_value.get("max_goals", 10),
        "calibration_min_history": training_value.get("calibration_min_history", 100),
    }
    return tuple(candidates), tuple(folds), training


def run_nested_archive_forecasts(
    archives: list[str | Path] | tuple[str | Path, ...],
    plan_path: str | Path,
    outdir: str | Path,
    *,
    leagues: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Fit and score one independently selected nested goals model per league."""
    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    batch = load_football_data_uk_archives(archives)
    candidates, folds, training = _load_plan(plan_path)
    available_leagues = sorted(batch.matches["league"].dropna().astype(str).unique())
    selected_leagues = sorted(set(leagues)) if leagues else available_leagues
    unknown = sorted(set(selected_leagues) - set(available_leagues))
    if unknown:
        raise ValueError(f"requested leagues are absent from the archive: {', '.join(unknown)}")

    score_frames: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    prediction_frames: list[pd.DataFrame] = []
    skipped: list[dict[str, str]] = []
    for league in selected_leagues:
        league_matches = batch.matches.loc[batch.matches["league"].astype("string").eq(league)].copy()
        try:
            result = nested_walk_forward_goals(
                league_matches,
                candidates,
                folds,
                **training,
            )
        except (ValueError, RuntimeError) as exc:
            skipped.append({"league": league, "reason": str(exc)})
            continue
        scores = result.inner_scores.copy()
        scores.insert(0, "league", league)
        selections = result.selections.copy()
        selections.insert(0, "league", league)
        predictions = result.outer_predictions.copy()
        predictions.insert(0, "league_code", league)
        score_frames.append(scores)
        selection_frames.append(selections)
        prediction_frames.append(predictions)
    if not prediction_frames:
        raise ValueError("no league produced nested OOS forecasts; see training history and fold plan")

    output.mkdir(parents=True, exist_ok=True)
    scores = pd.concat(score_frames, ignore_index=True)
    selections = pd.concat(selection_frames, ignore_index=True)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    scores.to_csv(output / "inner_model_scores.csv", index=False)
    selections.to_csv(output / "model_selections.csv", index=False)
    predictions.to_csv(output / "nested_oos_predictions.csv", index=False)
    pd.DataFrame(skipped, columns=("league", "reason")).to_csv(output / "skipped_leagues.csv", index=False)
    manifest = {
        "status": "completed",
        "source_archives": [
            {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for path in archives
        ],
        "plan_path": str(Path(plan_path).resolve()),
        "source_match_rows": len(batch.matches),
        "source_odds_rows": len(batch.odds),
        "requested_leagues": selected_leagues,
        "successful_leagues": sorted(predictions["league_code"].unique().tolist()),
        "skipped_leagues": skipped,
        "prediction_rows": len(predictions),
        "model_candidates": [
            {"name": item.name, "family": item.family, "parameters": item.audit_parameters()}
            for item in candidates
        ],
        "training": training,
        "reports": [
            "inner_model_scores.csv",
            "model_selections.csv",
            "nested_oos_predictions.csv",
            "skipped_leagues.csv",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return manifest


__all__ = ["run_nested_archive_forecasts"]
