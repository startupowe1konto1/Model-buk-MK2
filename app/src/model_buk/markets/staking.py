"""Stake sizing under the Polish tax regime: taxed Kelly and the winnings-PIT cliff.

Two taxes shape a Polish single bet:

* the 12% gaming tax on the stake, which bookmakers pass on as a payout
  multiplier ``tau`` (0.88 by default; it differs per bookmaker), so a win pays
  ``stake * tau * odds``;
* a 10% PIT on a single win above 2 280 zl, charged on the *whole* payout, not
  on the excess. A payout just above the threshold therefore pays less than one
  just below it (a cliff), and the break-even ``p * odds`` jumps from
  ``1 / 0.88 = 1.136`` to ``1 / 0.792 = 1.263``.

Kelly is computed on ``b = tau * odds - 1`` and on a *lower bound* of the win
probability, never on the point estimate. Stakes are capped so the payout stays
at or below the PIT threshold; :func:`effective_tax_multiplier` and
:func:`net_payout` give the exact switch (``tau -> 0.9 * tau``) for evaluating
or settling a stake that does cross it.

The definition of a "win" for the PIT threshold (whole payout including the
stake, as operators apply it) comes from secondary sources; see the research
note "weryfikacja_matematyki_rynku.md" Q3 (Gaps). The rule is configurable
through :class:`PitRule` in case the threshold or the rate changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from model_buk.markets.pricing import (
    require_decimal_odds,
    require_probability,
    require_tax_multiplier,
)


PIT_THRESHOLD_PLN: Final = 2280.0
PIT_RATE: Final = 0.10
STAKE_STEP_PLN: Final = 0.01


@dataclass(frozen=True)
class PitRule:
    """Winnings-PIT rule: ``rate`` of the whole payout above ``threshold_pln``."""

    threshold_pln: float = PIT_THRESHOLD_PLN
    rate: float = PIT_RATE

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.threshold_pln)) or self.threshold_pln <= 0.0:
            raise ValueError("threshold_pln must be finite and positive")
        if not math.isfinite(float(self.rate)) or not 0.0 <= self.rate < 1.0:
            raise ValueError("rate must be in [0, 1)")


DEFAULT_PIT_RULE: Final = PitRule()


def _positive_amount(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive amount") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a finite positive amount")
    return number


def round_stake_down(amount: float, step: float = STAKE_STEP_PLN) -> float:
    """Round ``amount`` down to a multiple of ``step`` (never up: it is a stake)."""
    unit = _positive_amount(step, "step")
    units = math.floor(float(amount) / unit + 1e-9)
    return round(max(units, 0) * unit, 8)


# --------------------------------------------------------------------------- #
# PIT threshold
# --------------------------------------------------------------------------- #


def gross_payout(stake: float, decimal_odds: float, tax_multiplier: float) -> float:
    """Payout owed by the bookmaker on a win, before the winnings PIT."""
    amount = _positive_amount(stake, "stake")
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    return amount * tau * odds


def pit_applies(
    stake: float,
    decimal_odds: float,
    tax_multiplier: float,
    rule: PitRule = DEFAULT_PIT_RULE,
) -> bool:
    """True when the payout is strictly above the PIT threshold."""
    return gross_payout(stake, decimal_odds, tax_multiplier) > rule.threshold_pln


def effective_tax_multiplier(
    stake: float,
    decimal_odds: float,
    tax_multiplier: float,
    rule: PitRule = DEFAULT_PIT_RULE,
) -> float:
    """``tau`` below the threshold, ``(1 - rate) * tau`` (0.792 at 0.88) above it."""
    if pit_applies(stake, decimal_odds, tax_multiplier, rule):
        return tax_multiplier * (1.0 - rule.rate)
    return float(tax_multiplier)


def net_payout(
    stake: float,
    decimal_odds: float,
    tax_multiplier: float,
    rule: PitRule = DEFAULT_PIT_RULE,
) -> float:
    """Amount received on a win after both the stake tax and the winnings PIT."""
    payout = gross_payout(stake, decimal_odds, tax_multiplier)
    if payout > rule.threshold_pln:
        return payout * (1.0 - rule.rate)
    return payout


def expected_value_net_at_stake(
    probability: float,
    decimal_odds: float,
    tax_multiplier: float,
    *,
    stake: float,
    rule: PitRule = DEFAULT_PIT_RULE,
) -> float:
    """Net EV per unit stake including the PIT switch at this stake size."""
    p = require_probability(probability)
    tau_effective = effective_tax_multiplier(stake, decimal_odds, tax_multiplier, rule)
    return p * tau_effective * float(decimal_odds) - 1.0


def max_stake_below_pit(
    decimal_odds: float,
    tax_multiplier: float,
    rule: PitRule = DEFAULT_PIT_RULE,
    step: float = STAKE_STEP_PLN,
) -> float:
    """Largest stake (in ``step`` units) whose payout stays at or below the threshold."""
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    unit = _positive_amount(step, "step")
    units = math.floor(rule.threshold_pln / (tau * odds) / unit + 1e-9)
    # Guard against floating-point error in the division above.
    while units > 0 and units * unit * tau * odds > rule.threshold_pln:
        units -= 1
    return round(max(units, 0) * unit, 8)


# --------------------------------------------------------------------------- #
# Kelly with the stake tax
# --------------------------------------------------------------------------- #


def full_kelly_fraction(p_lower: float, decimal_odds: float, tax_multiplier: float) -> float:
    """Full Kelly fraction of the bankroll on ``b = tau * odds - 1``.

    ``f* = (p * b - (1 - p)) / b = EV_net / (tau * odds - 1)``, evaluated at
    ``p_lower`` (a lower bound of the win probability), and ``0.0`` whenever the
    net edge is not strictly positive. Without the tax term the textbook
    formula stakes 20.8% of the bankroll on MB-0002 (p 0.70, odds 1.61) whose
    taxed EV is -0.82%; the taxed formula stakes nothing.
    """
    p = require_probability(p_lower, "p_lower")
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    b = tau * odds - 1.0
    if b <= 0.0:
        return 0.0
    edge = p * b - (1.0 - p)
    if edge <= 0.0:
        return 0.0
    return edge / b


def fractional_kelly_fraction(
    p_lower: float,
    decimal_odds: float,
    tax_multiplier: float,
    kelly_multiplier: float,
) -> float:
    """``kelly_multiplier`` times the full taxed Kelly fraction (0 < multiplier <= 1).

    The multiplier has no default: shrinking Kelly under estimation error is a
    risk decision (a quarter Kelly is a common starting point).
    """
    multiplier = _kelly_multiplier(kelly_multiplier)
    return multiplier * full_kelly_fraction(p_lower, decimal_odds, tax_multiplier)


def _kelly_multiplier(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("kelly_multiplier must be in (0, 1]") from exc
    if not 0.0 < number <= 1.0:
        raise ValueError("kelly_multiplier must be in (0, 1]")
    return number


# --------------------------------------------------------------------------- #
# Stake plan: fractional Kelly on p_lower, capped below the PIT threshold
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StakePlan:
    """Result of :func:`plan_stake`.

    ``stake`` is in PLN, rounded down to the stake step. ``applied_fraction`` is
    ``stake / bankroll`` after every cap. ``reason`` is ``"ok"``, ``"no_edge"``
    (Kelly is zero) or ``"below_stake_step"`` (the Kelly stake rounds to zero).
    """

    stake: float
    full_kelly: float
    applied_fraction: float
    effective_tax_multiplier: float
    pit_capped: bool
    reason: str


def plan_stake(
    *,
    bankroll: float,
    p_lower: float,
    decimal_odds: float,
    tax_multiplier: float,
    kelly_multiplier: float,
    max_bankroll_fraction: float | None = None,
    pit_rule: PitRule = DEFAULT_PIT_RULE,
    stake_step: float = STAKE_STEP_PLN,
) -> StakePlan:
    """Fractional taxed Kelly on ``p_lower``, capped so the payout stays under the PIT threshold.

    The payout is discontinuous at the PIT threshold, so a stake that would
    cross it is cut back to :func:`max_stake_below_pit` rather than re-sized at
    ``0.9 * tau`` (at 0.792 the break-even needs ``p * odds >= 1.263``, which a
    realistic edge never reaches).
    """
    capital = _positive_amount(bankroll, "bankroll")
    multiplier = _kelly_multiplier(kelly_multiplier)
    tau = require_tax_multiplier(tax_multiplier)
    full = full_kelly_fraction(p_lower, decimal_odds, tau)
    if full <= 0.0:
        return StakePlan(0.0, 0.0, 0.0, tau, False, "no_edge")

    fraction = multiplier * full
    if max_bankroll_fraction is not None:
        cap_fraction = float(max_bankroll_fraction)
        if not 0.0 < cap_fraction <= 1.0:
            raise ValueError("max_bankroll_fraction must be in (0, 1]")
        fraction = min(fraction, cap_fraction)

    stake = round_stake_down(capital * fraction, stake_step)
    if stake <= 0.0:
        return StakePlan(0.0, full, 0.0, tau, False, "below_stake_step")

    pit_capped = False
    if pit_applies(stake, decimal_odds, tau, pit_rule):
        stake = min(stake, max_stake_below_pit(decimal_odds, tau, pit_rule, stake_step))
        pit_capped = True
        if stake <= 0.0:
            return StakePlan(0.0, full, 0.0, tau, True, "below_stake_step")

    return StakePlan(
        stake=stake,
        full_kelly=full,
        applied_fraction=stake / capital,
        effective_tax_multiplier=effective_tax_multiplier(stake, decimal_odds, tau, pit_rule),
        pit_capped=pit_capped,
        reason="ok",
    )
