"""Significance tools for comparing forecast losses fairly.

Everything here works on *per-match loss differences* ``d_i = loss_baseline_i -
loss_model_i`` (positive means the model is better) and on the matchday
clusters those matches belong to:

- :func:`diebold_mariano` -- Diebold-Mariano test with a Newey-West / HAC
  variance.  Matches of one matchday are correlated (shared weather, shared
  refereeing trends, shared shocks), so the loss differences are summed inside
  each cluster first and the Bartlett-kernel long-run variance is computed on
  the chronological series of cluster sums.  With one match per cluster this is
  exactly the textbook Newey-West DM test.  The Harvey-Leybourne-Newbold small
  sample correction (Student-t with ``G - 1`` degrees of freedom) is on by
  default.
- :func:`holm_adjust`, :func:`benjamini_hochberg_adjust` -- multiple testing
  corrections across leagues (family-wise error and false discovery rate).
- :func:`cluster_bootstrap_mean_ci` -- percentile confidence interval for a
  mean loss (difference) after resampling whole matchdays with replacement.
  Fully vectorised (a resample is a row of cluster indices), so pooled 130k
  match samples with thousands of resamples take seconds.

A missing value stays missing: functions never fill NaN with zero.  Loss
vectors must be finite (callers drop unscored rows first) and adjusted
p-values keep NaN where the input p-value was NaN.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Hashable, Iterable, Literal

import numpy as np
import pandas as pd
from scipy import stats

from model_buk.evaluation.gates import BootstrapInterval

# Upper bound on the size of one (resamples x clusters) index block.
_BOOTSTRAP_BLOCK_CELLS = 1_000_000


def _finite_vector(values: Iterable[float] | np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or len(array) == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional sample")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must be finite; drop unscored rows first")
    return array


def _cluster_codes(clusters: Iterable[Hashable] | np.ndarray | None, n: int) -> tuple[np.ndarray, int]:
    """Integer cluster codes numbered in order of first appearance."""
    if clusters is None:
        return np.arange(n), n
    labels = np.asarray(clusters)
    if labels.ndim != 1 or len(labels) != n:
        raise ValueError("clusters must be one-dimensional and match the sample length")
    if pd.isna(labels).any():
        raise ValueError("clusters must contain non-missing labels")
    codes, uniques = pd.factorize(labels, sort=False)
    return codes.astype(np.int64), len(uniques)


# ---------------------------------------------------------------------------
# Diebold-Mariano
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DieboldMarianoResult:
    """Outcome of one Diebold-Mariano test of ``E[d] = 0``.

    ``mean_diff`` is the mean of ``baseline_loss - model_loss``: positive means
    the model is better.  ``p_value`` is two-sided; ``p_greater`` is the
    one-sided p-value for "the model is better" (H1: ``E[d] > 0``).
    """

    n: int
    n_clusters: int
    lags: int
    mean_diff: float
    std_error: float
    statistic: float
    p_value: float
    p_greater: float
    distribution: str


def newey_west_lags(n_clusters: int) -> int:
    """Newey-West (1994) plug-in bandwidth ``floor(4 (G / 100) ** (2 / 9))``."""
    if n_clusters < 1:
        raise ValueError("n_clusters must be positive")
    return int(math.floor(4.0 * (n_clusters / 100.0) ** (2.0 / 9.0)))


def diebold_mariano(
    loss_diff: Iterable[float] | np.ndarray,
    clusters: Iterable[Hashable] | np.ndarray | None = None,
    *,
    order: Iterable[float] | np.ndarray | None = None,
    lags: int | None = None,
    small_sample: bool = True,
) -> DieboldMarianoResult:
    """Diebold-Mariano test of equal predictive accuracy with a HAC variance.

    Parameters
    ----------
    loss_diff:
        Per-match ``baseline_loss - model_loss`` (finite).
    clusters:
        Matchday label of every match.  ``None`` treats each match as its own
        cluster (the classic single time series DM test).
    order:
        Sort key (for example the kickoff time).  Observations are put in this
        order before the serial-dependence lags are applied; without it the
        input order is taken as chronological.
    lags:
        Bartlett truncation lag on the cluster-sum series; default is the
        Newey-West plug-in rule.  ``0`` gives the plain cluster-robust variance.
    small_sample:
        Apply the Harvey-Leybourne-Newbold correction and use a Student-t
        reference with ``G - 1`` degrees of freedom (``G`` = clusters).  With
        ``False`` the reference is standard normal.

    Degenerate samples (fewer than three clusters or a zero variance estimate)
    return NaN statistics: no evidence either way, never a fabricated p-value.
    """
    d = _finite_vector(loss_diff, "loss_diff")
    n = len(d)
    label_array = None if clusters is None else np.asarray(clusters)
    if order is not None:
        keys = np.asarray(order)
        if len(keys) != n:
            raise ValueError("order must match the sample length")
        sorter = np.argsort(keys, kind="stable")
        d = d[sorter]
        if label_array is not None:
            label_array = label_array[sorter]
    codes, n_clusters = _cluster_codes(label_array, n)
    mean = float(d.mean())
    sums = np.bincount(codes, weights=d - mean, minlength=n_clusters)
    max_lag = min(newey_west_lags(n_clusters) if lags is None else int(lags), max(n_clusters - 1, 0))
    if max_lag < 0:
        raise ValueError("lags must be non-negative")
    long_run = float(sums @ sums)
    for lag in range(1, max_lag + 1):
        weight = 1.0 - lag / (max_lag + 1.0)
        long_run += 2.0 * weight * float(sums[lag:] @ sums[:-lag])
    variance_of_mean = long_run / (n * n)
    nan = float("nan")
    # A variance of pure floating point noise (e.g. identical losses) is no evidence either way.
    negligible = variance_of_mean <= 1e-20 * float(np.mean(d * d)) / n
    if n_clusters < 3 or not np.isfinite(variance_of_mean) or negligible:
        return DieboldMarianoResult(n, n_clusters, max_lag, mean, nan, nan, nan, nan, "t" if small_sample else "normal")
    std_error = math.sqrt(variance_of_mean)
    statistic = mean / std_error
    if small_sample:
        statistic *= math.sqrt((n_clusters - 1.0) / n_clusters)
        p_two = float(2.0 * stats.t.sf(abs(statistic), n_clusters - 1))
        p_greater = float(stats.t.sf(statistic, n_clusters - 1))
        distribution = "t"
    else:
        p_two = float(2.0 * stats.norm.sf(abs(statistic)))
        p_greater = float(stats.norm.sf(statistic))
        distribution = "normal"
    return DieboldMarianoResult(n, n_clusters, max_lag, mean, std_error, float(statistic), p_two, p_greater, distribution)


# ---------------------------------------------------------------------------
# Multiple testing
# ---------------------------------------------------------------------------


def _adjust(p_values: Iterable[float] | np.ndarray, method: Literal["holm", "bh"]) -> np.ndarray:
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1:
        raise ValueError("p_values must be one-dimensional")
    finite = np.isfinite(p)
    if np.any((p[finite] < 0.0) | (p[finite] > 1.0)):
        raise ValueError("p_values must lie in [0, 1]")
    adjusted = np.full(len(p), np.nan)
    m = int(finite.sum())
    if m == 0:
        return adjusted
    values = p[finite]
    order = np.argsort(values, kind="stable")
    ranked = values[order]
    if method == "holm":
        step = (m - np.arange(m)) * ranked
        step = np.maximum.accumulate(step)
    else:
        step = ranked * m / (np.arange(m) + 1.0)
        step = np.minimum.accumulate(step[::-1])[::-1]
    step = np.minimum(step, 1.0)
    restored = np.empty(m)
    restored[order] = step
    adjusted[finite] = restored
    return adjusted


def holm_adjust(p_values: Iterable[float] | np.ndarray) -> np.ndarray:
    """Holm step-down adjusted p-values (family-wise error rate); NaN stays NaN."""
    return _adjust(p_values, "holm")


def benjamini_hochberg_adjust(p_values: Iterable[float] | np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (false discovery rate); NaN stays NaN."""
    return _adjust(p_values, "bh")


# ---------------------------------------------------------------------------
# Cluster bootstrap
# ---------------------------------------------------------------------------


def cluster_bootstrap_means(
    values: Iterable[float] | np.ndarray,
    clusters: Iterable[Hashable] | np.ndarray,
    *,
    n_resamples: int = 2_000,
    seed: int = 0,
) -> np.ndarray:
    """Bootstrap distribution of the observation-level mean over resampled clusters.

    ``values`` may be one-dimensional (returns ``(n_resamples,)``) or a
    ``(n, m)`` matrix of ``m`` paired statistics that share the same resamples
    (returns ``(n_resamples, m)``).  A resample draws ``G`` clusters with
    replacement and returns ``sum of their value sums / sum of their sizes``,
    so the statistic is still the match-weighted mean.
    """
    array = np.asarray(values, dtype=float)
    single = array.ndim == 1
    matrix = array.reshape(-1, 1) if single else array
    if matrix.ndim != 2 or len(matrix) == 0:
        raise ValueError("values must be a non-empty sample")
    if not np.isfinite(matrix).all():
        raise ValueError("values must be finite; drop unscored rows first")
    if isinstance(n_resamples, (bool, np.bool_)) or int(n_resamples) < 1:
        raise ValueError("n_resamples must be positive")
    codes, n_clusters = _cluster_codes(clusters, len(matrix))
    counts = np.bincount(codes, minlength=n_clusters).astype(float)
    sums = np.column_stack(
        [np.bincount(codes, weights=matrix[:, j], minlength=n_clusters) for j in range(matrix.shape[1])]
    )
    rng = np.random.default_rng(seed)
    out = np.empty((int(n_resamples), matrix.shape[1]))
    block = max(1, _BOOTSTRAP_BLOCK_CELLS // n_clusters)
    for start in range(0, int(n_resamples), block):
        size = min(block, int(n_resamples) - start)
        draw = rng.integers(0, n_clusters, size=(size, n_clusters))
        denominator = counts[draw].sum(axis=1)
        for j in range(matrix.shape[1]):
            out[start : start + size, j] = sums[:, j][draw].sum(axis=1) / denominator
    return out[:, 0] if single else out


def cluster_bootstrap_mean_ci(
    values: Iterable[float] | np.ndarray,
    clusters: Iterable[Hashable] | np.ndarray,
    *,
    confidence_level: float = 0.95,
    n_resamples: int = 2_000,
    seed: int = 0,
) -> BootstrapInterval:
    """Percentile interval for the mean after resampling whole matchdays."""
    sample = _finite_vector(values, "values")
    level = float(confidence_level)
    if not 0.0 < level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    draws = cluster_bootstrap_means(sample, clusters, n_resamples=n_resamples, seed=seed)
    tail = (1.0 - level) / 2.0
    lower, upper = np.quantile(draws, [tail, 1.0 - tail])
    return BootstrapInterval(
        estimate=float(sample.mean()),
        lower=float(lower),
        upper=float(upper),
        confidence_level=level,
        n_resamples=int(n_resamples),
        seed=int(seed),
    )


__all__ = [
    "DieboldMarianoResult",
    "benjamini_hochberg_adjust",
    "cluster_bootstrap_mean_ci",
    "cluster_bootstrap_means",
    "diebold_mariano",
    "holm_adjust",
    "newey_west_lags",
]
