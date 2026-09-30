from __future__ import annotations

import math

import numpy as np
import pytest

from model_buk.evaluation.np_metrics import (
    brier_score_loss,
    isotonic_regression,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
)


def _reference_isotonic(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Closed form of the weighted isotonic fit: f_i = max_{j<=i} min_{k>=i} mean_w(y[j..k])."""

    n = len(y)
    out = np.empty(n)
    for i in range(n):
        best = -math.inf
        for j in range(i + 1):
            worst = math.inf
            for k in range(i, n):
                worst = min(worst, float(np.dot(w[j : k + 1], y[j : k + 1]) / w[j : k + 1].sum()))
            best = max(best, worst)
        out[i] = best
    return out


@pytest.mark.parametrize("seed", range(20))
def test_pav_matches_the_closed_form_on_random_weighted_data(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 25))
    y = rng.normal(size=n)
    w = rng.uniform(0.1, 5.0, size=n)
    assert np.allclose(isotonic_regression(y, sample_weight=w), _reference_isotonic(y, w), atol=1e-12)


def test_pav_decreasing_and_clipping():
    y = np.array([0.1, 0.9, 0.5, 1.4, -0.2])
    up = isotonic_regression(y, y_min=0.0, y_max=1.0)
    assert np.all(np.diff(up) >= 0) and up.min() >= 0.0 and up.max() <= 1.0
    down = isotonic_regression(y, increasing=False)
    assert np.all(np.diff(down) <= 0)
    assert np.allclose(down, -isotonic_regression(-y))


def test_pav_keeps_monotone_input_and_handles_empty():
    y = np.array([0.0, 0.2, 0.2, 0.7])
    assert np.array_equal(isotonic_regression(y), y)
    assert isotonic_regression(np.array([])).size == 0


def test_log_loss_and_brier_hand_values():
    y = [1, 0, 1, 0]
    p = [0.8, 0.3, 0.6, 0.1]
    expected = -np.mean([math.log(0.8), math.log(0.7), math.log(0.6), math.log(0.9)])
    assert log_loss(y, p) == pytest.approx(expected)
    assert log_loss(y, p, labels=[0, 1]) == pytest.approx(expected)
    assert brier_score_loss(y, p) == pytest.approx(np.mean([0.04, 0.09, 0.16, 0.01]))
    # Certain wrong forecasts are clipped, not infinite.
    assert math.isfinite(log_loss([1], [0.0]))
    with pytest.raises(ValueError):
        log_loss([2, 0], [0.5, 0.5])


def test_regression_errors():
    assert mean_absolute_error([1, 2, 3], [2, 2, 5]) == pytest.approx(1.0)
    assert mean_squared_error([1, 2, 3], [2, 2, 5]) == pytest.approx(5 / 3)
