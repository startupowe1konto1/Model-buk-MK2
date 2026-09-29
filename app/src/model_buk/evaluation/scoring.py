"""Proper scoring rules for ordered outcome classes, per match and aggregated.

All scores are *losses* (lower is better) and are computed per match so that
they can be paired between models (Diebold-Mariano, cluster bootstrap):

- log-loss ``-log p[y]`` (primary metric; probabilities floored at 1e-15, the
  same floor as :mod:`model_buk.evaluation.gates`);
- multiclass Brier score ``sum_k (p_k - o_k) ** 2`` (sum over classes, the same
  convention as :func:`model_buk.evaluation.gates.multiclass_brier_score`);
- ranked probability score for *ordered* classes,
  ``1 / (K - 1) * sum_{k < K} (P_k - O_k) ** 2`` with cumulative ``P`` and ``O``
  (1X2 is ordered home < draw < away, over/under is under < over).

A forecast row that is NaN means "no forecast" and stays NaN in every score;
it is never scored as zero.  :func:`score_table` reports how many rows were
missing next to the mean losses, per group and pooled, with cluster bootstrap
intervals (whole matchdays are resampled).
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from model_buk.evaluation.significance import cluster_bootstrap_means

PROBABILITY_FLOOR = 1e-15
METRICS: tuple[str, ...] = ("log_loss", "brier", "rps")
_ROW_SUM_TOLERANCE = 1e-6


def check_probabilities(probabilities: Sequence[Sequence[float]] | np.ndarray, *, name: str = "probabilities") -> np.ndarray:
    """Validate a forecast matrix and return a float copy with exact row sums.

    Rows that are entirely NaN mean "no forecast" and are passed through.  Any
    other row must be finite, non-negative and sum to one within 1e-6 (tiny
    numerical drift is renormalised; anything larger is a model bug and raises).
    """
    matrix = np.array(probabilities, dtype=float, copy=True)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError(f"{name} must be a two-dimensional matrix with at least two classes")
    missing = np.isnan(matrix)
    partial = missing.any(axis=1) & ~missing.all(axis=1)
    if partial.any():
        raise ValueError(f"{name} has rows that are only partly NaN")
    present = ~missing.all(axis=1)
    live = matrix[present]
    if not np.isfinite(live).all():
        raise ValueError(f"{name} must be finite")
    if (live < 0.0).any():
        raise ValueError(f"{name} must be non-negative")
    mass = live.sum(axis=1)
    if np.any(np.abs(mass - 1.0) > _ROW_SUM_TOLERANCE):
        raise ValueError(f"{name} rows must sum to one")
    matrix[present] = live / mass[:, None]
    return matrix


def _outcome_indices(outcome: Sequence[int] | np.ndarray, rows: int, classes: int) -> np.ndarray:
    y = np.asarray(outcome)
    if y.ndim != 1 or len(y) != rows:
        raise ValueError("outcome must be one-dimensional and match the probability rows")
    if not np.issubdtype(y.dtype, np.integer):
        if not np.all(np.isfinite(y)) or np.any(y != np.floor(y)):
            raise ValueError("outcome must hold integer class indices")
        y = y.astype(np.int64)
    if len(y) and (y.min() < 0 or y.max() >= classes):
        raise ValueError("outcome class index is outside the probability matrix")
    return y


def log_loss_per_match(probabilities: np.ndarray, outcome: Sequence[int] | np.ndarray) -> np.ndarray:
    """``-log p[y]`` per match; NaN forecast rows give NaN."""
    matrix = np.asarray(probabilities, dtype=float)
    y = _outcome_indices(outcome, len(matrix), matrix.shape[1])
    chosen = matrix[np.arange(len(y)), y]
    return -np.log(np.clip(chosen, PROBABILITY_FLOOR, 1.0))


def brier_per_match(probabilities: np.ndarray, outcome: Sequence[int] | np.ndarray) -> np.ndarray:
    """Multiclass Brier score (sum over classes) per match."""
    matrix = np.asarray(probabilities, dtype=float)
    y = _outcome_indices(outcome, len(matrix), matrix.shape[1])
    target = np.zeros_like(matrix)
    target[np.arange(len(y)), y] = 1.0
    return ((matrix - target) ** 2).sum(axis=1)


def rps_per_match(probabilities: np.ndarray, outcome: Sequence[int] | np.ndarray) -> np.ndarray:
    """Ranked probability score per match for classes ordered by column."""
    matrix = np.asarray(probabilities, dtype=float)
    y = _outcome_indices(outcome, len(matrix), matrix.shape[1])
    k = matrix.shape[1]
    cumulative_forecast = np.cumsum(matrix, axis=1)[:, :-1]
    cumulative_outcome = (np.arange(k - 1)[None, :] >= y[:, None]).astype(float)
    return ((cumulative_forecast - cumulative_outcome) ** 2).sum(axis=1) / (k - 1)


_SCORE_FUNCTIONS = {
    "log_loss": log_loss_per_match,
    "brier": brier_per_match,
    "rps": rps_per_match,
}


def per_match_scores(
    probabilities: np.ndarray,
    outcome: Sequence[int] | np.ndarray,
    metrics: Iterable[str] = METRICS,
) -> pd.DataFrame:
    """Per-match losses as a frame with one column per metric."""
    matrix = np.asarray(probabilities, dtype=float)
    columns = {}
    for metric in metrics:
        if metric not in _SCORE_FUNCTIONS:
            raise ValueError(f"unknown metric: {metric}")
        columns[metric] = _SCORE_FUNCTIONS[metric](matrix, outcome)
    return pd.DataFrame(columns)


def score_table(
    scores: pd.DataFrame,
    *,
    group_col: str = "league_code",
    cluster_col: str = "cluster",
    metrics: Sequence[str] = METRICS,
    n_resamples: int = 2_000,
    confidence_level: float = 0.95,
    seed: int = 0,
    pooled_label: str = "ALL",
) -> pd.DataFrame:
    """Mean losses per group and pooled, with cluster bootstrap intervals.

    ``scores`` needs ``group_col``, ``cluster_col`` and one column per metric
    (NaN = no forecast; those rows are excluded and counted in ``n_missing``).
    The pooled row is last.  Intervals resample whole clusters (matchdays), and
    the same resamples are shared by all metrics of one group.
    """
    level = float(confidence_level)
    if not 0.0 < level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    tail = (1.0 - level) / 2.0
    metrics = tuple(metrics)
    rows: list[dict[str, object]] = []

    def summarise(label: object, frame: pd.DataFrame) -> dict[str, object]:
        valid = frame.dropna(subset=list(metrics))
        row: dict[str, object] = {
            group_col: label,
            "n": len(valid),
            "n_missing": len(frame) - len(valid),
            "n_clusters": int(valid[cluster_col].nunique()) if len(valid) else 0,
        }
        if len(valid) == 0:
            for metric in metrics:
                row[metric] = row[f"{metric}_lo"] = row[f"{metric}_hi"] = float("nan")
            return row
        matrix = valid.loc[:, list(metrics)].to_numpy(dtype=float)
        draws = cluster_bootstrap_means(
            matrix, valid[cluster_col].to_numpy(), n_resamples=n_resamples, seed=seed
        )
        lower, upper = np.quantile(draws, [tail, 1.0 - tail], axis=0)
        for j, metric in enumerate(metrics):
            row[metric] = float(matrix[:, j].mean())
            row[f"{metric}_lo"] = float(lower[j])
            row[f"{metric}_hi"] = float(upper[j])
        return row

    for label, frame in scores.groupby(group_col, sort=True):
        rows.append(summarise(label, frame))
    rows.append(summarise(pooled_label, scores))
    return pd.DataFrame(rows)


__all__ = [
    "METRICS",
    "PROBABILITY_FLOOR",
    "brier_per_match",
    "check_probabilities",
    "log_loss_per_match",
    "per_match_scores",
    "rps_per_match",
    "score_table",
]
