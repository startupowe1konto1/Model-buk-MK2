from __future__ import annotations

import math


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    if over_odds <= 1.0 or under_odds <= 1.0:
        raise ValueError("Decimal odds must be greater than 1.0")
    qo, qu = 1.0 / over_odds, 1.0 / under_odds
    denom = qo + qu
    return qo / denom, qu / denom


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
