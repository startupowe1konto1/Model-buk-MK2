"""NumPy versions of the few scikit-learn functions the project used.

Windows Smart App Control on the project machine refuses to load
scikit-learn's compiled helper (``sklearn/_cyutility.pyd``, unsigned) since
2026-09-30, so ``import sklearn.metrics`` / ``sklearn.isotonic`` fail. The
functions below reproduce the scikit-learn semantics the call sites rely on
(binary targets, probabilities of the positive class) so the project no
longer depends on that binary; the security setting stays untouched.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def isotonic_regression(
    y: Sequence[float] | np.ndarray,
    *,
    sample_weight: Sequence[float] | np.ndarray | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    increasing: bool = True,
) -> np.ndarray:
    """Weighted least-squares isotonic fit (pool adjacent violators), like ``sklearn.isotonic.isotonic_regression``.

    ``y`` is taken in the given order; the result is non-decreasing (or
    non-increasing with ``increasing=False``) and then clipped to
    ``[y_min, y_max]``.
    """

    values = np.asarray(y, dtype=float)
    if values.ndim != 1:
        raise ValueError("y must be one-dimensional")
    n = values.size
    if n == 0:
        return values.copy()
    weights = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    if weights.shape != values.shape:
        raise ValueError("sample_weight must have the same length as y")
    if np.any(weights < 0) or not np.all(np.isfinite(weights)) or not np.all(np.isfinite(values)):
        raise ValueError("y and sample_weight must be finite, weights non-negative")
    if not increasing:
        return -isotonic_regression(
            -values,
            sample_weight=weights,
            y_min=None if y_max is None else -y_max,
            y_max=None if y_min is None else -y_min,
            increasing=True,
        )

    # Blocks as parallel stacks: weighted mean, total weight, length.
    means = np.empty(n)
    totals = np.empty(n)
    lengths = np.empty(n, dtype=np.int64)
    top = -1
    for i in range(n):
        top += 1
        means[top], totals[top], lengths[top] = values[i], weights[i], 1
        while top > 0 and means[top - 1] > means[top]:
            weight = totals[top - 1] + totals[top]
            merged = (
                (means[top - 1] * totals[top - 1] + means[top] * totals[top]) / weight
                if weight > 0
                else (means[top - 1] * lengths[top - 1] + means[top] * lengths[top]) / (lengths[top - 1] + lengths[top])
            )
            means[top - 1], totals[top - 1] = merged, weight
            lengths[top - 1] += lengths[top]
            top -= 1
    fitted = np.repeat(means[: top + 1], lengths[: top + 1])
    if y_min is not None or y_max is not None:
        fitted = np.clip(fitted, -np.inf if y_min is None else y_min, np.inf if y_max is None else y_max)
    return fitted


def _binary(y_true: Sequence[float] | np.ndarray) -> np.ndarray:
    y = np.asarray(y_true, dtype=float).ravel()
    if not np.all(np.isin(y, (0.0, 1.0))):
        raise ValueError("y_true must be binary 0/1")
    return y


def log_loss(
    y_true: Sequence[float] | np.ndarray,
    y_prob: Sequence[float] | np.ndarray,
    labels: Sequence[int] | None = None,
) -> float:
    """Mean binary cross-entropy of the positive-class probability, as ``sklearn.metrics.log_loss``.

    Probabilities are clipped to ``[eps, 1 - eps]`` with the float64 machine
    epsilon (scikit-learn's default). ``labels`` is accepted for call-site
    compatibility and must be ``[0, 1]`` when given.
    """

    if labels is not None and list(labels) != [0, 1]:
        raise ValueError("only binary labels [0, 1] are supported")
    y = _binary(y_true)
    p = np.asarray(y_prob, dtype=float).ravel()
    if p.shape != y.shape:
        raise ValueError("y_true and y_prob must have the same length")
    eps = np.finfo(np.float64).eps
    p = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log1p(-p)))


def brier_score_loss(y_true: Sequence[float] | np.ndarray, y_prob: Sequence[float] | np.ndarray) -> float:
    """Mean squared difference between the 0/1 outcome and the positive-class probability."""

    y = _binary(y_true)
    p = np.asarray(y_prob, dtype=float).ravel()
    if p.shape != y.shape:
        raise ValueError("y_true and y_prob must have the same length")
    return float(np.mean((y - p) ** 2))


def mean_absolute_error(y_true: Sequence[float] | np.ndarray, y_pred: Sequence[float] | np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float))))


def mean_squared_error(y_true: Sequence[float] | np.ndarray, y_pred: Sequence[float] | np.ndarray) -> float:
    return float(np.mean((np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)) ** 2))


__all__ = ["isotonic_regression", "log_loss", "brier_score_loss", "mean_absolute_error", "mean_squared_error"]
