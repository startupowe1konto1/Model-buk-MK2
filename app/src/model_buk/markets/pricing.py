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
    return probability * decimal_odds - 1.0
