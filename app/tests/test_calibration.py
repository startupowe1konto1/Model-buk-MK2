from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from model_buk.evaluation.calibration import (
    CalibrationFold,
    TemperatureScaler,
    calibrate_chronologically,
)


def _probabilities() -> np.ndarray:
    return np.array(
        [
            [0.85, 0.10, 0.05], [0.75, 0.15, 0.10], [0.10, 0.75, 0.15],
            [0.15, 0.75, 0.10], [0.10, 0.15, 0.75], [0.20, 0.10, 0.70],
            [0.80, 0.10, 0.10], [0.10, 0.80, 0.10], [0.10, 0.10, 0.80],
        ]
    )


def test_temperature_scaler_is_immutable_normalised_and_deterministic() -> None:
    probabilities = _probabilities()[:6]
    actual = np.array([1, 1, 0, 2, 0, 1])
    first = TemperatureScaler.fit(probabilities, actual, fit_cutoff="2024-02-01")
    second = TemperatureScaler.fit(probabilities, actual, fit_cutoff="2024-02-01")

    assert first.temperature == pytest.approx(second.temperature)
    assert first.fit_cutoff == second.fit_cutoff
    assert first.n_samples == len(actual)
    assert np.allclose(first.apply(probabilities).sum(axis=1), 1.0)
    with pytest.raises(FrozenInstanceError):
        first.temperature = 1.0  # type: ignore[misc]


def test_chronological_calibration_never_fits_on_current_fold() -> None:
    probabilities = _probabilities()
    actual = np.array([1, 1, 0, 2, 0, 1, 0, 1, 2])
    kickoff = [f"2024-01-{day:02d}T12:00:00Z" for day in range(1, 10)]
    result = calibrate_chronologically(
        probabilities,
        actual,
        kickoff,
        [
            CalibrationFold("first", "2024-01-04", "2024-01-07"),
            CalibrationFold("second", "2024-01-07", "2024-01-10"),
        ],
        min_history=3,
    )

    first, second = result.folds
    assert first.status == "calibrated"
    assert first.calibration_rows == 3
    assert first.scaler is not None
    assert first.scaler.fit_cutoff.isoformat() == "2024-01-04T00:00:00+00:00"
    assert second.status == "calibrated"
    assert second.calibration_rows == 6
    assert result.calibrated.tolist() == [False, False, False, True, True, True, True, True, True]


def test_insufficient_history_keeps_raw_probabilities_with_explicit_status() -> None:
    probabilities = _probabilities()[:4]
    result = calibrate_chronologically(
        probabilities,
        [0, 1, 2, 0],
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        [CalibrationFold("holdout", "2024-01-03", "2024-01-05")],
        min_history=3,
    )

    record = result.folds[0]
    assert record.status == "insufficient_history"
    assert record.scaler is None
    assert not result.calibrated.any()
    assert np.array_equal(result.probabilities, probabilities)


@pytest.mark.parametrize(
    ("probabilities", "actual", "message"),
    [
        ([[0.5, -0.5]], [0], "non-negative"),
        ([[0.5, 0.5]], [2], "outside"),
        ([[0.5, 0.5]], ["home"], "classes"),
    ],
)
def test_fit_rejects_invalid_inputs(probabilities, actual, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TemperatureScaler.fit(probabilities, actual)


def test_chronological_inputs_reject_overlaps_bad_dates_and_zero_history_threshold() -> None:
    probabilities = _probabilities()[:3]
    with pytest.raises(ValueError, match="non-overlapping"):
        calibrate_chronologically(
            probabilities, [0, 1, 2], ["2024-01-01", "2024-01-02", "2024-01-03"],
            [
                CalibrationFold("a", "2024-01-02", "2024-01-04"),
                CalibrationFold("b", "2024-01-03", "2024-01-05"),
            ], min_history=1,
        )
    with pytest.raises(ValueError, match="valid timestamps"):
        calibrate_chronologically(
            probabilities, [0, 1, 2], ["bad", "2024-01-02", "2024-01-03"],
            [CalibrationFold("a", "2024-01-02", "2024-01-04")], min_history=1,
        )
    with pytest.raises(ValueError, match="positive"):
        calibrate_chronologically(
            probabilities, [0, 1, 2], ["2024-01-01", "2024-01-02", "2024-01-03"],
            [CalibrationFold("a", "2024-01-02", "2024-01-04")], min_history=0,
        )
