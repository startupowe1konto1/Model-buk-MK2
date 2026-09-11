from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

import pandas as pd

from model_buk.config import CornerV02Config
from model_buk.constants import CAT_FEATURES, CORE_FEATURES, MODEL_PARAMS, TRAIN_START
from model_buk.model import fit_total_model
from model_buk.models.corner_dual_v02 import fit_dual_model
from model_buk.strengths import add_corner_strength_features


def fit_live_corner_models(
    processed_path: str | Path,
    cfg: CornerV02Config,
    *,
    eval_v01_metadata_path: str | Path,
    eval_v02_metadata_path: str | Path,
    model_root: str | Path,
) -> dict:
    """Fit current-use models on every completed match available locally.

    This function is intentionally separate from backtesting. Once a historical
    reference period has been opened it can be used for current fitting, but it
    cannot be reused as untouched evidence of edge.
    """
    frame = pd.read_csv(processed_path)
    frame["date"] = pd.to_datetime(frame["date"], format="mixed")
    completed = frame[
        (frame["date"] >= pd.Timestamp(TRAIN_START))
        & frame["home_corners"].notna()
        & frame["away_corners"].notna()
        & frame["total_corners"].notna()
    ].copy()
    if completed.empty:
        raise ValueError("No completed matches available for live fitting")

    eval_v01_meta = json.loads(Path(eval_v01_metadata_path).read_text(encoding="utf-8"))
    eval_v02_meta = json.loads(Path(eval_v02_metadata_path).read_text(encoding="utf-8"))

    root = Path(model_root)
    v01_dir = root / "live" / "v01"
    v02_dir = root / "live" / "v02"
    v01_dir.mkdir(parents=True, exist_ok=True)
    v02_dir.mkdir(parents=True, exist_ok=True)

    total_model = fit_total_model(completed)
    total_model.save_model(str(v01_dir / "corner_total_v0_1.cbm"))
    v01_live_meta = {
        "model_version": "corner-total-v0.1-livefit",
        "specification_version": "corner-total-v0.1",
        "purpose": "current inference; not an untouched backtest artifact",
        "trained_through": str(completed["date"].max()),
        "n_completed_matches": int(len(completed)),
        "cat_features": CAT_FEATURES,
        "core_features": CORE_FEATURES,
        "model_params": MODEL_PARAMS,
        "nb_alpha": float(eval_v01_meta["nb_alpha"]),
    }
    (v01_dir / "metadata.json").write_text(json.dumps(v01_live_meta, indent=2), encoding="utf-8")

    completed_v02 = add_corner_strength_features(
        completed,
        shrinkage_games=cfg.shrinkage_games,
        min_rate=cfg.min_rate,
        max_rate=cfg.max_rate,
    )
    dual = fit_dual_model(completed_v02, cfg.catboost_params)
    v02_live_meta = {
        "version": "corner-dual-v0.2-livefit",
        "specification_version": cfg.version,
        "purpose": "current inference/team-corners challenger; not untouched evidence",
        "trained_through": str(completed["date"].max()),
        "n_completed_matches": int(len(completed)),
        "alphas": eval_v02_meta["alphas"],
        "chosen_total_distribution": eval_v02_meta["chosen_total_distribution"],
        "config": asdict(cfg),
    }
    dual.save(v02_dir, metadata=v02_live_meta)

    registry = {
        "as_of": str(completed["date"].max()),
        "total_corners": {
            "champion": "corner-total-v0.1-livefit",
            "path": "live/v01",
            "reason": "v0.1 beats v0.2 dual model on pre-2025 total-corners OOF MAE/RMSE and is also better on the already-open 2025/26 reference probability metrics.",
        },
        "team_corners": {
            "challenger": "corner-dual-v0.2-livefit",
            "path": "live/v02",
            "status": "experimental; no historical executable team-corners price validation yet",
        },
        "deployment_status": "research/paper only",
    }
    (root / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return registry
