from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, mean_squared_error


def count_metrics(y: np.ndarray, mu: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    mu = np.asarray(mu, dtype=float)
    return {
        "mae": float(mean_absolute_error(y, mu)),
        "rmse": float(mean_squared_error(y, mu) ** 0.5),
        "bias": float(np.mean(mu - y)),
    }


def probability_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return {
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "mean_prediction": float(p.mean()),
        "base_rate": float(y.mean()),
    }


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    frame = pd.DataFrame({"y": np.asarray(y, dtype=int), "p": np.asarray(p, dtype=float)})
    edges = np.linspace(0, 1, bins + 1)
    frame["bin"] = pd.cut(frame.p, bins=edges, include_lowest=True, duplicates="drop")
    out = frame.groupby("bin", observed=False).agg(
        n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")
    ).reset_index()
    out["abs_gap"] = (out.predicted - out.observed).abs()
    return out


def expected_calibration_error(table: pd.DataFrame) -> float:
    if table.empty or table.n.sum() == 0:
        return float("nan")
    return float((table.n * table.abs_gap).sum() / table.n.sum())
