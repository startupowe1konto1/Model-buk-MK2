"""CORP reliability diagrams and score decomposition for binary forecasts.

Implements the CORP approach of Dimitriadis, Gneiting and Jordan (2021, PNAS,
"Stable reliability diagrams for probabilistic classifiers"):

- The *conditional event probability* (CEP) of a forecast is estimated by
  isotonic regression of the binary outcome on the forecast, computed with the
  pool-adjacent-violators algorithm (PAV).  This is the unique
  optimally-binned, tuning-free reliability curve; ties in the forecast values
  are pooled first (weighted average of the outcomes).
- For any Bregman-type proper score ``S`` (Brier and log-loss are provided) the
  mean score decomposes exactly as ``S = MCB - DSC + UNC`` where
  ``MCB = S(p) - S(CEP)`` (miscalibration, >= 0), ``DSC = S(base rate) -
  S(CEP)`` (discrimination, >= 0) and ``UNC = S(base rate)`` (uncertainty).

A perfectly reliable forecast has ``MCB = 0`` up to sampling noise.  Because
that noise is always positive, :func:`corp_reliability` can attach a Monte
Carlo p-value for "the forecast is reliable": outcomes are re-simulated as
``Bernoulli(p_i)`` (the null of calibration), the CORP curve and MCB are
recomputed and the p-value is the share of simulated MCB at least as large as
the observed one.  Optional pointwise consistency bands around the diagonal
come from the same simulations.

Use :func:`corp_by_class` for multiclass forecasts (1X2 is evaluated
one-vs-rest, over/under is a single binary event) and
:func:`corp_diagram_table` to print a compact reliability diagram.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
import pandas as pd
from model_buk.evaluation.np_metrics import isotonic_regression

from model_buk.evaluation.scoring import PROBABILITY_FLOOR

CorpScore = Literal["brier", "log_loss"]


def _elementary(score: CorpScore, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Scores of forecast ``x`` when the event happens (``s1``) and when not (``s0``)."""
    if score == "brier":
        return (1.0 - x) ** 2, x**2
    if score == "log_loss":
        clipped = np.clip(x, PROBABILITY_FLOOR, 1.0 - PROBABILITY_FLOOR)
        return -np.log(clipped), -np.log1p(-clipped)
    raise ValueError(f"unknown score: {score}")


def _mean_score(score: CorpScore, x: np.ndarray, events: np.ndarray, sizes: np.ndarray) -> float:
    """Mean score of forecasts ``x`` where ``events`` of ``sizes`` cases happened."""
    s1, s0 = _elementary(score, x)
    return float((events * s1 + (sizes - events) * s0).sum() / sizes.sum())


def _pav_curve(unique_x: np.ndarray, sizes: np.ndarray, events: np.ndarray) -> np.ndarray:
    return isotonic_regression(events / sizes, sample_weight=sizes, y_min=0.0, y_max=1.0, increasing=True)


def _decompose(score: CorpScore, unique_x: np.ndarray, sizes: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, float, float, float, float]:
    curve = _pav_curve(unique_x, sizes, events)
    base = np.array([events.sum() / sizes.sum()])
    total = np.array([sizes.sum()])
    total_events = np.array([events.sum()])
    mean_score = _mean_score(score, unique_x, events, sizes)
    recalibrated = _mean_score(score, curve, events, sizes)
    uncertainty = _mean_score(score, base, total_events, total)
    return curve, mean_score, max(mean_score - recalibrated, 0.0), uncertainty - recalibrated, uncertainty


@dataclass(frozen=True)
class CorpResult:
    """CORP reliability curve and score decomposition of one binary forecast.

    ``x`` are the distinct forecast values (sorted), ``cep`` the isotonic
    conditional event probabilities at those values and ``counts`` how many
    forecasts share each value.  ``mean_score == mcb - dsc + unc`` holds up to
    floating point error.  ``mcb_pvalue`` is NaN unless simulations were run.
    ``band_lower`` / ``band_upper`` are pointwise consistency bands (or None).
    """

    score: str
    n: int
    base_rate: float
    mean_score: float
    mcb: float
    dsc: float
    unc: float
    x: np.ndarray
    cep: np.ndarray
    counts: np.ndarray
    mcb_pvalue: float = float("nan")
    band_lower: np.ndarray | None = None
    band_upper: np.ndarray | None = None


def corp_reliability(
    probability: Sequence[float] | np.ndarray,
    event: Sequence[int] | np.ndarray,
    *,
    score: CorpScore = "brier",
    n_sim: int = 0,
    seed: int = 0,
    bands: bool = False,
    band_level: float = 0.95,
) -> CorpResult:
    """CORP curve and ``MCB - DSC + UNC`` decomposition of a binary forecast.

    Pairs with a NaN forecast are dropped (no forecast, not a zero forecast).
    ``n_sim > 0`` adds the Monte Carlo reliability p-value described in the
    module docstring; ``bands=True`` additionally keeps pointwise consistency
    bands from those simulations (memory ``n_sim x distinct forecasts``).
    """
    p = np.asarray(probability, dtype=float)
    e = np.asarray(event, dtype=float)
    if p.ndim != 1 or e.shape != p.shape:
        raise ValueError("probability and event must be one-dimensional and equally long")
    keep = np.isfinite(p)
    p, e = p[keep], e[keep]
    if len(p) == 0:
        raise ValueError("no forecasts to evaluate")
    if np.any((p < 0.0) | (p > 1.0)):
        raise ValueError("probability must lie in [0, 1]")
    if not np.isin(e, (0.0, 1.0)).all():
        raise ValueError("event must be 0 or 1")
    unique_x, inverse = np.unique(p, return_inverse=True)
    sizes = np.bincount(inverse).astype(float)
    events = np.bincount(inverse, weights=e, minlength=len(unique_x))
    curve, mean_score, mcb, dsc, unc = _decompose(score, unique_x, sizes, events)
    p_value = float("nan")
    lower = upper = None
    if n_sim > 0:
        rng = np.random.default_rng(seed)
        counts_int = sizes.astype(np.int64)
        exceed = 0
        keep_curves = np.empty((n_sim, len(unique_x))) if bands else None
        for i in range(n_sim):
            simulated = rng.binomial(counts_int, unique_x).astype(float)
            sim_curve, _, sim_mcb, _, _ = _decompose(score, unique_x, sizes, simulated)
            exceed += int(sim_mcb >= mcb - 1e-15)
            if keep_curves is not None:
                keep_curves[i] = sim_curve
        p_value = (1.0 + exceed) / (1.0 + n_sim)
        if keep_curves is not None:
            tail = (1.0 - band_level) / 2.0
            lower, upper = np.quantile(keep_curves, [tail, 1.0 - tail], axis=0)
    return CorpResult(
        score=score,
        n=len(p),
        base_rate=float(e.mean()),
        mean_score=mean_score,
        mcb=mcb,
        dsc=dsc,
        unc=unc,
        x=unique_x,
        cep=curve,
        counts=sizes,
        mcb_pvalue=p_value,
        band_lower=lower,
        band_upper=upper,
    )


def corp_by_class(
    probabilities: np.ndarray,
    outcome: Sequence[int] | np.ndarray,
    classes: Sequence[str],
    *,
    score: CorpScore = "brier",
    n_sim: int = 0,
    seed: int = 0,
    binary_single: bool = False,
) -> pd.DataFrame:
    """One-vs-rest CORP decomposition of a multiclass forecast, one row per class.

    ``binary_single=True`` evaluates only the last class (the natural event for
    a two-class market such as over/under, where the other class is its
    complement and would repeat the same numbers).
    """
    matrix = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcome)
    if matrix.ndim != 2 or matrix.shape[1] != len(classes) or len(y) != len(matrix):
        raise ValueError("probabilities, outcome and classes do not line up")
    chosen = [len(classes) - 1] if binary_single else range(len(classes))
    rows = []
    for k in chosen:
        result = corp_reliability(matrix[:, k], (y == k).astype(float), score=score, n_sim=n_sim, seed=seed)
        rows.append(
            {
                "class": classes[k],
                "n": result.n,
                "base_rate": result.base_rate,
                "score": result.mean_score,
                "mcb": result.mcb,
                "dsc": result.dsc,
                "unc": result.unc,
                "mcb_pvalue": result.mcb_pvalue,
            }
        )
    return pd.DataFrame(rows)


def corp_diagram_table(result: CorpResult, n_points: int = 10) -> pd.DataFrame:
    """Compact CORP diagram: forecast value versus estimated event probability.

    Picks ``n_points`` forecast quantiles (by forecast count, so dense regions
    get the points) and returns ``forecast``, ``cep`` and the share of forecasts
    at or below that value.
    """
    if n_points < 2:
        raise ValueError("n_points must be at least 2")
    cumulative = np.cumsum(result.counts) / result.counts.sum()
    targets = (np.arange(1, n_points + 1)) / n_points
    positions = np.unique(np.minimum(np.searchsorted(cumulative, targets - 1e-12), len(result.x) - 1))
    return pd.DataFrame(
        {
            "forecast": result.x[positions],
            "cep": result.cep[positions],
            "share_at_or_below": cumulative[positions],
        }
    )


__all__ = [
    "CorpResult",
    "CorpScore",
    "corp_by_class",
    "corp_diagram_table",
    "corp_reliability",
]
