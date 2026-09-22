from __future__ import annotations

import math

import pytest

from model_buk.markets.pricing import (
    DEVIG_METHODS,
    devig_probabilities,
    devig_proportional,
    devig_two_way,
)


@pytest.mark.parametrize("method", sorted(DEVIG_METHODS))
def test_every_method_returns_positive_normalised_probabilities(method: str) -> None:
    probabilities = devig_probabilities((1.55, 4.20, 7.50), method)

    assert len(probabilities) == 3
    assert all(math.isfinite(value) and value > 0.0 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.0, abs=1e-12)


def test_proportional_matches_known_inverse_odds_normalisation() -> None:
    expected = (6 / 13, 4 / 13, 3 / 13)

    assert devig_proportional((2.0, 3.0, 4.0)) == pytest.approx(expected)
    assert devig_probabilities((2.0, 3.0, 4.0), "proportional") == pytest.approx(
        expected
    )


def test_shin_differs_from_proportional_for_an_asymmetric_book() -> None:
    odds = (1.55, 4.20, 7.50)

    proportional = devig_probabilities(odds, "proportional")
    shin = devig_probabilities(odds, "shin")

    assert shin != pytest.approx(proportional, abs=1e-6)
    assert sum(shin) == pytest.approx(1.0, abs=1e-12)


def test_two_way_helper_remains_proportional_and_compatible() -> None:
    assert devig_two_way(1.80, 2.05) == pytest.approx(
        devig_probabilities((1.80, 2.05), "proportional")
    )


@pytest.mark.parametrize(
    "odds",
    [(), (2.0,), (1.0, 2.0), (float("nan"), 2.0), (float("inf"), 2.0)],
)
def test_invalid_books_are_rejected(odds: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        devig_probabilities(odds, "proportional")


def test_unknown_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown de-vig method"):
        devig_probabilities((2.0, 2.0), "mystery")
