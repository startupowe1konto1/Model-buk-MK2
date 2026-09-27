"""Chronological multiclass temperature scaling.

Temperature scaling changes only the sharpness of a forecast.  The
``calibrate_chronologically`` helper deliberately fits a separate scaler for
each holdout fold from predictions whose kickoff is *strictly* before that
fold's start.  It therefore never learns from outcomes in the fold it applies
to (nor from simultaneous fixtures at the fold boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable, Sequence

import numpy as np
import pandas as pd


_EPSILON = 1e-15


def _bounded_golden_minimum(
    function,
    lower: float,
    upper: float,
    *,
    iterations: int = 96,
) -> float:
    """Return a deterministic bounded scalar minimum without native dependencies."""
    ratio = (np.sqrt(5.0) - 1.0) / 2.0
    left = float(lower)
    right = float(upper)
    c = right - ratio * (right - left)
    d = left + ratio * (right - left)
    fc = float(function(c))
    fd = float(function(d))
    for _ in range(iterations):
        if fc <= fd:
            right, d, fd = d, c, fc
            c = right - ratio * (right - left)
            fc = float(function(c))
        else:
            left, c, fc = c, d, fd
            d = left + ratio * (right - left)
            fd = float(function(d))
    return (left + right) / 2.0


def _probability_matrix(probabilities: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    matrix = np.asarray(probabilities, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("probabilities must be a two-dimensional matrix with at least two classes")
    if len(matrix) == 0:
        raise ValueError("probabilities must not be empty")
    if not np.isfinite(matrix).all():
        raise ValueError("probabilities must be finite")
    if np.any(matrix < 0.0):
        raise ValueError("probabilities must be non-negative")
    mass = matrix.sum(axis=1)
    if np.any(mass <= 0.0):
        raise ValueError("each probability row must have positive mass")
    return matrix / mass[:, None]


def _label_indices(
    actual: Sequence[Hashable] | np.ndarray,
    *,
    rows: int,
    classes: Sequence[Hashable] | None,
    columns: int,
) -> np.ndarray:
    labels = np.asarray(actual)
    if labels.ndim != 1 or len(labels) != rows:
        raise ValueError("actual must be one-dimensional and match probability rows")
    if classes is None:
        if not np.issubdtype(labels.dtype, np.integer):
            raise ValueError("classes must be supplied for non-integer labels")
        indices = labels.astype(int)
    else:
        if len(classes) != columns or len(set(classes)) != len(classes):
            raise ValueError("classes must be unique and match probability columns")
        mapping = {label: index for index, label in enumerate(classes)}
        try:
            indices = np.asarray([mapping[label] for label in labels], dtype=int)
        except KeyError as exc:
            raise ValueError(f"unknown actual class: {exc.args[0]}") from exc
    if np.any(indices < 0) or np.any(indices >= columns):
        raise ValueError("actual class index is outside the probability matrix")
    return indices


def _utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("fold bounds must be valid timestamps")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _kickoff_index(kickoff: Sequence[object] | pd.Series | np.ndarray, rows: int) -> pd.DatetimeIndex:
    if len(kickoff) != rows:
        raise ValueError("kickoff must contain one timestamp per probability row")
    timestamps = pd.DatetimeIndex(pd.to_datetime(kickoff, errors="coerce", utc=True, format="mixed"))
    if timestamps.isna().any():
        raise ValueError("kickoff must contain only valid timestamps")
    return timestamps


@dataclass(frozen=True)
class TemperatureScaler:
    """A fitted scalar temperature and auditable fit metadata.

    The object is immutable so its fit cutoff cannot silently change between
    an offline evaluation and a later report.
    """

    temperature: float
    fit_cutoff: pd.Timestamp | None = None
    n_samples: int = 0

    def __post_init__(self) -> None:
        if not np.isfinite(self.temperature) or self.temperature <= 0.0:
            raise ValueError("temperature must be finite and positive")
        if self.n_samples < 0:
            raise ValueError("n_samples must be non-negative")
        if self.fit_cutoff is not None:
            object.__setattr__(self, "fit_cutoff", _utc_timestamp(self.fit_cutoff))

    @classmethod
    def fit(
        cls,
        probabilities: Sequence[Sequence[float]] | np.ndarray,
        actual: Sequence[Hashable] | np.ndarray,
        *,
        classes: Sequence[Hashable] | None = None,
        fit_cutoff: str | pd.Timestamp | None = None,
    ) -> "TemperatureScaler":
        """Fit a deterministic NLL-minimising multiclass temperature."""
        matrix = _probability_matrix(probabilities)
        indices = _label_indices(
            actual, rows=len(matrix), classes=classes, columns=matrix.shape[1]
        )
        log_probabilities = np.log(np.clip(matrix, _EPSILON, 1.0))

        def loss(log_temperature: float) -> float:
            logits = log_probabilities / np.exp(log_temperature)
            logits -= logits.max(axis=1, keepdims=True)
            normalizer = np.log(np.exp(logits).sum(axis=1))
            return float(np.mean(normalizer - logits[np.arange(len(indices)), indices]))

        # Optimising log(T) makes positivity structural.  The finite range is
        # intentionally broad while preventing useless numerical extremes.
        log_temperature = _bounded_golden_minimum(loss, -6.0, 6.0)
        if not np.isfinite(loss(log_temperature)):
            raise RuntimeError("temperature scaling optimisation failed")
        cutoff = _utc_timestamp(fit_cutoff) if fit_cutoff is not None else None
        return cls(
            temperature=float(np.exp(log_temperature)),
            fit_cutoff=cutoff,
            n_samples=len(matrix),
        )

    def apply(self, probabilities: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
        """Apply the fitted temperature and return a row-normalised matrix."""
        matrix = _probability_matrix(probabilities)
        logits = np.log(np.clip(matrix, _EPSILON, 1.0)) / self.temperature
        logits -= logits.max(axis=1, keepdims=True)
        calibrated = np.exp(logits)
        return calibrated / calibrated.sum(axis=1, keepdims=True)


def fit_temperature_scaler(
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    actual: Sequence[Hashable] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
    fit_cutoff: str | pd.Timestamp | None = None,
) -> TemperatureScaler:
    """Convenience wrapper for :meth:`TemperatureScaler.fit`."""
    return TemperatureScaler.fit(
        probabilities, actual, classes=classes, fit_cutoff=fit_cutoff
    )


def apply_temperature_scaler(
    scaler: TemperatureScaler,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
) -> np.ndarray:
    """Convenience wrapper which refuses objects other than a fitted scaler."""
    if not isinstance(scaler, TemperatureScaler):
        raise TypeError("scaler must be a TemperatureScaler")
    return scaler.apply(probabilities)


@dataclass(frozen=True)
class CalibrationFold:
    """Half-open chronological holdout interval ``[start, end)``."""

    label: str
    start: str | pd.Timestamp
    end: str | pd.Timestamp

    def bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        start = _utc_timestamp(self.start)
        end = _utc_timestamp(self.end)
        if end <= start:
            raise ValueError("fold end must be later than fold start")
        return start, end


@dataclass(frozen=True)
class FoldCalibration:
    """Audit record for one fold; ``scaler=None`` means raw fallback."""

    fold: CalibrationFold
    status: str
    calibration_rows: int
    scored_rows: int
    scaler: TemperatureScaler | None


@dataclass(frozen=True)
class ChronologicalCalibrationResult:
    """OOF calibration output plus one immutable audit record per fold."""

    probabilities: np.ndarray
    calibrated: np.ndarray
    folds: tuple[FoldCalibration, ...]


def calibrate_chronologically(
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    actual: Sequence[Hashable] | np.ndarray,
    kickoff: Sequence[object] | pd.Series | np.ndarray,
    folds: Iterable[CalibrationFold],
    *,
    classes: Sequence[Hashable] | None = None,
    min_history: int = 100,
) -> ChronologicalCalibrationResult:
    """Calibrate each fold using only predictions before its kickoff boundary.

    Rows in a fold with fewer than ``min_history`` prior predictions retain the
    raw (normalised) probabilities and get ``insufficient_history`` status.
    This is deliberate: the current fold is never included to make a scaler.
    """
    if min_history < 1:
        raise ValueError("min_history must be positive")
    matrix = _probability_matrix(probabilities)
    indices = _label_indices(
        actual, rows=len(matrix), classes=classes, columns=matrix.shape[1]
    )
    timestamps = _kickoff_index(kickoff, len(matrix))
    frozen_folds = tuple(folds)
    if not frozen_folds:
        raise ValueError("folds must not be empty")
    if not all(isinstance(fold, CalibrationFold) for fold in frozen_folds):
        raise TypeError("folds must contain CalibrationFold values")

    checked: list[tuple[CalibrationFold, pd.Timestamp, pd.Timestamp]] = []
    previous_end: pd.Timestamp | None = None
    for fold in frozen_folds:
        start, end = fold.bounds()
        if previous_end is not None and start < previous_end:
            raise ValueError("folds must be chronological and non-overlapping")
        checked.append((fold, start, end))
        previous_end = end

    output = matrix.copy()
    calibrated = np.zeros(len(matrix), dtype=bool)
    reports: list[FoldCalibration] = []
    for fold, start, end in checked:
        score_mask = (timestamps >= start) & (timestamps < end)
        history_mask = timestamps < start
        history_rows = int(history_mask.sum())
        scored_rows = int(score_mask.sum())
        if scored_rows == 0:
            reports.append(FoldCalibration(fold, "empty_fold", history_rows, 0, None))
            continue
        if history_rows < min_history:
            reports.append(
                FoldCalibration(fold, "insufficient_history", history_rows, scored_rows, None)
            )
            continue
        scaler = TemperatureScaler.fit(
            matrix[history_mask], indices[history_mask], fit_cutoff=start
        )
        output[score_mask] = scaler.apply(matrix[score_mask])
        calibrated[score_mask] = True
        reports.append(FoldCalibration(fold, "calibrated", history_rows, scored_rows, scaler))
    return ChronologicalCalibrationResult(output, calibrated, tuple(reports))


# Verbose alias makes the time-safety guarantee visible at call sites.
calibrate_folds_chronologically = calibrate_chronologically
