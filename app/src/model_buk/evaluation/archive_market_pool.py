"""Walk-forward pooling of model forecasts with pre-match opening prices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from model_buk.artifact_identity import sha256_file
from model_buk.goals_backtest import (
    build_market_1x2_benchmark,
    logarithmic_pool_1x2,
    tune_logarithmic_pool_1x2,
)
from model_buk.ingestion.football_data_uk import load_football_data_uk_archives


_REQUIRED = {
    "source_row_id",
    "league_code",
    "outer_fold",
    "kickoff",
    "actual",
    "p_home",
    "p_draw",
    "p_away",
}


def run_archive_market_pool(
    archives: list[str | Path] | tuple[str | Path, ...],
    predictions_path: str | Path,
    outdir: str | Path,
    *,
    model_weights: Iterable[float] = tuple(np.linspace(0.0, 1.0, 21)),
    market_provider: str = "market_average",
    assume_opening_available_at_cutoff: bool = False,
) -> dict[str, Any]:
    """Tune a log-pool weight on completed outer folds and forecast the next.

    Only opening prices are accepted. football-data.co.uk does not provide quote
    timestamps, so callers must explicitly acknowledge the research assumption
    that an opening book was available at the decision cutoff.
    """
    if not assume_opening_available_at_cutoff:
        raise ValueError(
            "archive opening prices have no quote timestamps; pass "
            "assume_opening_available_at_cutoff=True for research-only analysis"
        )
    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    grid = tuple(float(value) for value in model_weights)
    if not grid or len(set(grid)) != len(grid):
        raise ValueError("model_weights must contain unique values")
    if not all(np.isfinite(value) and 0 <= value <= 1 for value in grid):
        raise ValueError("model_weights must be finite and between zero and one")

    predictions = pd.read_csv(predictions_path)
    missing = sorted(_REQUIRED - set(predictions.columns))
    if missing:
        raise ValueError(f"predictions are missing columns: {', '.join(missing)}")
    if predictions["source_row_id"].duplicated().any():
        raise ValueError("predictions contain duplicate source_row_id values")
    batch = load_football_data_uk_archives(archives)
    market = build_market_1x2_benchmark(
        batch.opening_odds,
        odds_kind="opening",
        provider=market_provider,
    )
    source = predictions.merge(market, on="source_row_id", how="inner", validate="one_to_one")
    if source.empty:
        raise ValueError("no predictions matched a complete opening 1X2 market")
    source = source.rename(
        columns={
            "p_home": "model_p_home",
            "p_draw": "model_p_draw",
            "p_away": "model_p_away",
        }
    )
    kickoff = pd.to_datetime(source["kickoff"], errors="raise", utc=True, format="mixed")
    fold_order = tuple(
        source.assign(_kickoff=kickoff)
        .groupby("outer_fold", sort=False)["_kickoff"]
        .min()
        .sort_values()
        .index.astype(str)
    )
    if len(fold_order) < 2:
        raise ValueError("market pooling requires at least two chronological outer folds")

    frames: list[pd.DataFrame] = []
    selections: list[dict[str, Any]] = []
    fold_values = source["outer_fold"].astype(str)
    for fold_index, fold in enumerate(fold_order[1:], start=1):
        train = source.loc[fold_values.isin(fold_order[:fold_index])].copy()
        test = source.loc[fold_values.eq(fold)].copy()
        scores = tune_logarithmic_pool_1x2(train, model_weights=grid)
        winner = scores.iloc[0]
        selected_weight = float(winner["model_weight"])
        pooled = logarithmic_pool_1x2(test, model_weight=selected_weight)
        pooled["p_home_raw"] = pooled["p_home"]
        pooled["p_draw_raw"] = pooled["p_draw"]
        pooled["p_away_raw"] = pooled["p_away"]
        pooled["selected_candidate"] = "walk-forward-opening-market-pool"
        pooled["selected_family"] = "opening_market_log_pool"
        parameters = {
            "model_weight": selected_weight,
            "market_weight": 1.0 - selected_weight,
            "market_provider": market_provider,
            "market_odds_kind": "opening",
            "trained_on_outer_folds": list(fold_order[:fold_index]),
        }
        pooled["selected_parameters"] = [parameters] * len(pooled)
        pooled["calibrated"] = True
        pooled["calibration_status"] = "walk-forward-opening-market-log-pool"
        pooled = pooled.rename(
            columns={
                "model_p_home": "component_p_home",
                "model_p_draw": "component_p_draw",
                "model_p_away": "component_p_away",
                "market_p_home": "opening_market_p_home",
                "market_p_draw": "opening_market_p_draw",
                "market_p_away": "opening_market_p_away",
            }
        )
        frames.append(pooled)
        selections.append(
            {
                "outer_fold": fold,
                "model_weight": selected_weight,
                "market_weight": 1.0 - selected_weight,
                "selection_log_loss": float(winner["log_loss"]),
                "selection_brier": float(winner["brier"]),
                "training_outer_folds": tuple(fold_order[:fold_index]),
                "training_rows": len(train),
                "prediction_rows": len(test),
            }
        )

    combined = pd.concat(frames, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output / "nested_oos_predictions.csv", index=False)
    pd.DataFrame(selections).to_csv(output / "market_pool_selections.csv", index=False)
    manifest: dict[str, Any] = {
        "status": "completed",
        "method": "walk_forward_opening_market_log_pool",
        "promotion_eligible": False,
        "promotion_blockers": [
            "football-data.co.uk opening prices have no point-in-time quote timestamps"
        ],
        "source_assumptions": [
            "opening price explicitly assumed available at the forecast cutoff"
        ],
        "market_provider": market_provider,
        "model_weight_grid": list(grid),
        "warmup_outer_fold": fold_order[0],
        "evaluated_outer_folds": list(fold_order[1:]),
        "prediction_rows": len(combined),
        "predictions": {
            "path": str(Path(predictions_path).resolve()),
            "sha256": sha256_file(predictions_path),
        },
        "archives": [
            {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for path in archives
        ],
        "reports": ["nested_oos_predictions.csv", "market_pool_selections.csv"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


__all__ = ["run_archive_market_pool"]
