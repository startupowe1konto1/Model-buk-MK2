from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from model_buk.backtest import build_summary, holdout_predictions, run_oof, select_bets
from model_buk.constants import CAT_FEATURES, CORE_FEATURES, MODEL_PARAMS, TRAIN_START
from model_buk.model import estimate_nb_alpha, fit_total_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/processed/epl_total_corners_v01.csv.gz")
    parser.add_argument("--outdir", default="outputs")
    parser.add_argument("--modeldir", default="models")
    args = parser.parse_args()

    frame = pd.read_csv(args.data, parse_dates=["date"])
    oof = run_oof(frame)
    alpha = estimate_nb_alpha(oof.total_corners.to_numpy(), oof.mu_total.to_numpy())
    predictions = holdout_predictions(frame, alpha)
    bets = select_bets(predictions)
    summary = build_summary(oof, predictions, bets, alpha)

    outdir = Path(args.outdir)
    modeldir = Path(args.modeldir)
    outdir.mkdir(parents=True, exist_ok=True)
    modeldir.mkdir(parents=True, exist_ok=True)

    oof.to_csv(outdir / "oof_predictions.csv", index=False)
    predictions.to_csv(outdir / "holdout_predictions.csv", index=False)
    bets.to_csv(outdir / "holdout_bets.csv", index=False)
    (outdir / "backtest_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    final_train = frame[(frame.source == "historical") & (frame.date >= TRAIN_START)].copy()
    model = fit_total_model(final_train)
    model.save_model(str(modeldir / "corner_total_v0_1.cbm"))
    metadata = {
        "model_version": "corner-total-v0.1",
        "cat_features": CAT_FEATURES,
        "core_features": CORE_FEATURES,
        "model_params": MODEL_PARAMS,
        "nb_alpha": alpha,
    }
    (modeldir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
