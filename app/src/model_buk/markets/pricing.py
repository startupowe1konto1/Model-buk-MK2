from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Final


_BISECT_ITERATIONS: Final = 100


def _validated_odds(odds: Sequence[float]) -> tuple[float, ...]:
    try:
        values = tuple(float(odd) for odd in odds)
    except (TypeError, ValueError) as exc:
        raise ValueError("odds must be a sequence of finite decimal values") from exc
    if len(values) < 2:
        raise ValueError("odds must contain at least two prices")
    if any(not math.isfinite(odd) or odd <= 1.0 for odd in values):
        raise ValueError("decimal odds must be finite and greater than 1.0")
    return values


def devig_proportional(odds: Sequence[float]) -> tuple[float, ...]:
    """Remove the overround by normalising inverse decimal odds."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    total = sum(implied)
    return tuple(probability / total for probability in implied)


def devig_power(odds: Sequence[float]) -> tuple[float, ...]:
    """Fit the power exponent for which transformed probabilities sum to one."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    if abs(sum(implied) - 1.0) < 1e-12:
        return implied

    def total(exponent: float) -> float:
        return sum(probability**exponent for probability in implied)

    lower, upper = 0.0, 1.0
    while total(upper) > 1.0:
        upper *= 2.0
        if upper > 1e6:
            raise ValueError("could not solve the power de-vig exponent")
    for _ in range(_BISECT_ITERATIONS):
        midpoint = (lower + upper) / 2.0
        if total(midpoint) > 1.0:
            lower = midpoint
        else:
            upper = midpoint
    exponent = (lower + upper) / 2.0
    probabilities = tuple(probability**exponent for probability in implied)
    normalizer = sum(probabilities)
    return tuple(probability / normalizer for probability in probabilities)


def devig_shin(odds: Sequence[float]) -> tuple[float, ...]:
    """Remove the overround using Shin's informed-trader model."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    overround_total = sum(implied)
    if abs(overround_total - 1.0) < 1e-12:
        return implied

    def probabilities(informed_share: float) -> tuple[float, ...]:
        if informed_share <= 0.0:
            return tuple(probability / math.sqrt(overround_total) for probability in implied)
        return tuple(
            (
                math.sqrt(
                    informed_share**2
                    + 4.0
                    * (1.0 - informed_share)
                    * probability**2
                    / overround_total
                )
                - informed_share
            )
            / (2.0 * (1.0 - informed_share))
            for probability in implied
        )

    lower, upper = 0.0, 0.5
    while sum(probabilities(upper)) > 1.0:
        upper = (upper + 1.0) / 2.0
        if upper > 1.0 - 1e-12:
            break
    for _ in range(_BISECT_ITERATIONS):
        midpoint = (lower + upper) / 2.0
        if sum(probabilities(midpoint)) > 1.0:
            lower = midpoint
        else:
            upper = midpoint
    result = probabilities((lower + upper) / 2.0)
    normalizer = sum(result)
    return tuple(probability / normalizer for probability in result)


DEVIG_METHODS: Final[dict[str, Callable[[Sequence[float]], tuple[float, ...]]]] = {
    "proportional": devig_proportional,
    "power": devig_power,
    "shin": devig_shin,
}


def devig_probabilities(
    odds: Sequence[float],
    method: str = "proportional",
) -> tuple[float, ...]:
    """Return positive fair probabilities for a complete bookmaker book."""
    if method not in DEVIG_METHODS:
        choices = ", ".join(sorted(DEVIG_METHODS))
        raise ValueError(f"unknown de-vig method {method!r}; expected one of: {choices}")
    return DEVIG_METHODS[method](odds)


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    over, under = devig_proportional((over_odds, under_odds))
    return over, under


def fair_odds(probability: float) -> float:
    if not (0.0 < probability <= 1.0):
        return math.inf if probability <= 0.0 else 1.0
    return 1.0 / probability


def expected_value(probability: float, decimal_odds: float) -> float:
    """Gross EV before stake tax, commission, and execution costs."""
    return probability * decimal_odds - 1.0


def expected_value_net(
    probability: float,
    decimal_odds: float,
    tax_multiplier: float,
) -> float:
    """EV after a stake-tax multiplier (0.88 means a 12% stake tax)."""
    if not 0.0 < tax_multiplier <= 1.0:
        raise ValueError("tax_multiplier must be in (0, 1]")
    return probability * (tax_multiplier * decimal_odds) - 1.0
