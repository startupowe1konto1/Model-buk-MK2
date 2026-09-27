"""Nested global evaluation for the opening-market residual model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_buk.artifact_identity import sha256_file
from model_buk.goals_backtest import build_market_1x2_benchmark
from model_buk.ingestion.football_data_uk import load_football_data_uk_archives
from model_buk.models.market_residual import (
    MarketResidualConfig,
    RESIDUAL_FEATURE_COLUMNS,
    build_market_residual_features,
    fit_market_residual,
)


@dataclass(frozen=True)
class MarketResidualFold:
    label: str
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp
    outer_start: pd.Timestamp
    outer_end: pd.Timestamp


def _timestamp(value: Any, *, field: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{field} must be a valid timestamp")
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _object(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _load_residual_plan(
    path: str | Path,
) -> tuple[tuple[MarketResidualConfig, ...], tuple[MarketResidualFold, ...]]:
    try:
        plan = _object(json.loads(Path(path).read_text(encoding="utf-8")), field="plan")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read residual plan: {exc}") from None
    unknown = sorted(set(plan) - {"candidates", "folds"})
    if unknown:
        raise ValueError(f"plan contains unknown fields: {', '.join(unknown)}")
    candidate_values = plan.get("candidates")
    fold_values = plan.get("folds")
    if not isinstance(candidate_values, list) or not candidate_values:
        raise ValueError("plan.candidates must be a non-empty JSON array")
    if not isinstance(fold_values, list) or not fold_values:
        raise ValueError("plan.folds must be a non-empty JSON array")
    allowed_config = set(MarketResidualConfig.__dataclass_fields__)
    candidates: list[MarketResidualConfig] = []
    for index, value in enumerate(candidate_values):
        row = _object(value, field=f"candidates[{index}]")
        extra = sorted(set(row) - allowed_config)
        if extra:
            raise ValueError(f"candidates[{index}] contains unknown fields: {', '.join(extra)}")
        candidates.append(MarketResidualConfig(**row))
    if len({json.dumps(asdict(item), sort_keys=True) for item in candidates}) != len(candidates):
        raise ValueError("residual candidates must be unique")
    folds: list[MarketResidualFold] = []
    prior_outer_end: pd.Timestamp | None = None
    for index, value in enumerate(fold_values):
        row = _object(value, field=f"folds[{index}]")
        expected = {"label", "validation_start", "validation_end", "outer_start", "outer_end"}
        if set(row) != expected:
            raise ValueError(f"folds[{index}] must contain exactly {', '.join(sorted(expected))}")
        fold = MarketResidualFold(
            label=str(row["label"]).strip(),
            validation_start=_timestamp(row["validation_start"], field="validation_start"),
            validation_end=_timestamp(row["validation_end"], field="validation_end"),
            outer_start=_timestamp(row["outer_start"], field="outer_start"),
            outer_end=_timestamp(row["outer_end"], field="outer_end"),
        )
        if not fold.label or not (
            fold.validation_start < fold.validation_end <= fold.outer_start < fold.outer_end
        ):
            raise ValueError("residual folds must have validation strictly before outer")
        if prior_outer_end is not None and fold.outer_start < prior_outer_end:
            raise ValueError("residual outer folds must be chronological and non-overlapping")
        folds.append(fold)
        prior_outer_end = fold.outer_end
    return tuple(candidates), tuple(folds)


def _loss(actual: np.ndarray, probabilities: np.ndarray) -> tuple[float, float]:
    targets = np.eye(3)[actual]
    chosen = probabilities[np.arange(len(actual)), actual]
    return (
        float(-np.log(np.clip(chosen, 1e-15, 1.0)).mean()),
        float(np.square(probabilities - targets).sum(axis=1).mean()),
    )


def run_archive_market_residual(
    archives: list[str | Path] | tuple[str | Path, ...],
    plan_path: str | Path,
    outdir: str | Path,
    *,
    market_provider: str = "market_average",
    assume_opening_available_at_cutoff: bool = False,
) -> dict[str, Any]:
    """Fit residual corrections globally and score untouched seasonal holdouts."""
    if not assume_opening_available_at_cutoff:
        raise ValueError(
            "archive opening prices have no quote timestamps; pass "
            "assume_opening_available_at_cutoff=True for research-only analysis"
        )
    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    candidates, folds = _load_residual_plan(plan_path)
    league_modes = {candidate.include_league_effects for candidate in candidates}
    if len(league_modes) != 1:
        raise ValueError("all residual candidates must agree on include_league_effects")
    batch = load_football_data_uk_archives(archives)
    features = build_market_residual_features(batch.matches)
    market = build_market_1x2_benchmark(
        batch.opening_odds, odds_kind="opening", provider=market_provider
    )
    data = features.merge(market, on="source_row_id", how="inner", validate="one_to_one")
    data = data.dropna(subset=["home_goals", "away_goals"]).copy()
    data["actual"] = np.where(
        data["home_goals"] > data["away_goals"],
        "home",
        np.where(data["home_goals"] == data["away_goals"], "draw", "away"),
    )
    data["actual_index"] = data["actual"].map({"home": 0, "draw": 1, "away": 2}).astype(int)
    market_columns = ["market_p_home", "market_p_draw", "market_p_away"]
    market_matrix = data[market_columns].to_numpy(dtype=float)
    data["opening_entropy"] = -np.sum(market_matrix * np.log(market_matrix), axis=1)
    data["opening_favourite_probability"] = market_matrix.max(axis=1)
    feature_columns = RESIDUAL_FEATURE_COLUMNS
    if next(iter(league_modes)):
        league_dummies = pd.get_dummies(
            data["league"].astype(str), prefix="league_", prefix_sep="_", dtype=float
        )
        data = pd.concat([data, league_dummies], axis=1)
        league_columns = tuple(sorted(column for column in league_dummies if column.startswith("league__")))
        feature_columns = (*RESIDUAL_FEATURE_COLUMNS, *league_columns)
    kickoff = pd.to_datetime(data["kickoff"], errors="raise", utc=True, format="mixed")

    score_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    for fold in folds:
        train_mask = kickoff < fold.validation_start
        validation_mask = (kickoff >= fold.validation_start) & (kickoff < fold.validation_end)
        outer_mask = (kickoff >= fold.outer_start) & (kickoff < fold.outer_end)
        if min(int(train_mask.sum()), int(validation_mask.sum()), int(outer_mask.sum())) == 0:
            raise ValueError(f"fold {fold.label!r} has an empty train, validation or outer interval")
        validation_scores: list[tuple[float, float, int, MarketResidualConfig]] = []
        for candidate_index, config in enumerate(candidates):
            model = fit_market_residual(
                data.loc[train_mask],
                data.loc[train_mask, market_columns].to_numpy(dtype=float),
                data.loc[train_mask, "actual_index"].to_numpy(dtype=int),
                config=config,
                feature_columns=feature_columns,
            )
            probabilities = model.predict_proba(
                data.loc[validation_mask],
                data.loc[validation_mask, market_columns].to_numpy(dtype=float),
            )
            log_loss, brier = _loss(
                data.loc[validation_mask, "actual_index"].to_numpy(dtype=int), probabilities
            )
            validation_scores.append((log_loss, brier, candidate_index, config))
            score_rows.append(
                {
                    "outer_fold": fold.label,
                    "candidate_index": candidate_index,
                    **asdict(config),
                    "validation_log_loss": log_loss,
                    "validation_brier": brier,
                    "training_rows": int(train_mask.sum()),
                    "validation_rows": int(validation_mask.sum()),
                }
            )
        selected_loss, selected_brier, selected_index, selected_config = min(validation_scores)
        refit_mask = kickoff < fold.outer_start
        model = fit_market_residual(
            data.loc[refit_mask],
            data.loc[refit_mask, market_columns].to_numpy(dtype=float),
            data.loc[refit_mask, "actual_index"].to_numpy(dtype=int),
            config=selected_config,
            feature_columns=feature_columns,
        )
        probabilities = model.predict_proba(
            data.loc[outer_mask], data.loc[outer_mask, market_columns].to_numpy(dtype=float)
        )
        outer = data.loc[outer_mask].copy()
        outer[["p_home", "p_draw", "p_away"]] = probabilities
        outer = outer.rename(
            columns={
                "market_p_home": "opening_market_p_home",
                "market_p_draw": "opening_market_p_draw",
                "market_p_away": "opening_market_p_away",
            }
        )
        outer["league_code"] = outer["league"].astype(str)
        outer["outer_fold"] = fold.label
        outer["selected_candidate"] = f"market-residual-{selected_index}"
        outer["selected_family"] = "opening_market_residual"
        outer["selected_parameters"] = [asdict(selected_config)] * len(outer)
        outer["calibrated"] = True
        outer["calibration_status"] = "market-offset-residual"
        prediction_frames.append(outer)
        selection_rows.append(
            {
                "outer_fold": fold.label,
                "selected_candidate_index": selected_index,
                **asdict(selected_config),
                "selection_log_loss": selected_loss,
                "selection_brier": selected_brier,
                "refit_rows": int(refit_mask.sum()),
                "prediction_rows": int(outer_mask.sum()),
            }
        )

    predictions = pd.concat(prediction_frames, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output / "nested_oos_predictions.csv", index=False)
    pd.DataFrame(score_rows).to_csv(output / "candidate_scores.csv", index=False)
    pd.DataFrame(selection_rows).to_csv(output / "model_selections.csv", index=False)
    manifest: dict[str, Any] = {
        "status": "completed",
        "method": "global_nested_opening_market_residual",
        "promotion_eligible": False,
        "promotion_blockers": [
            "football-data.co.uk opening prices have no point-in-time quote timestamps"
        ],
        "source_assumptions": [
            "opening price explicitly assumed available at the forecast cutoff"
        ],
        "feature_columns": list(feature_columns),
        "source_rows": len(batch.matches),
        "model_rows": len(data),
        "prediction_rows": len(predictions),
        "plan_path": str(Path(plan_path).resolve()),
        "archives": [
            {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for path in archives
        ],
        "reports": ["nested_oos_predictions.csv", "candidate_scores.csv", "model_selections.csv"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


__all__ = ["run_archive_market_residual"]
