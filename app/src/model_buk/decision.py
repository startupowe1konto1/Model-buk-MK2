from __future__ import annotations

from dataclasses import dataclass

from model_buk.markets.pricing import (
    TaxProfiles,
    devig_two_way,
    expected_value,
    expected_value_net,
    expected_value_net_lower_bound,
    fair_odds,
    probability_lower_bound,
    require_decimal_odds,
    require_non_negative,
    require_probability,
    tax_multiplier_for,
)
from model_buk.markets.staking import fractional_kelly_fraction, full_kelly_fraction


@dataclass(frozen=True)
class MarketDecision:
    side: str
    probability: float
    fair_odds: float
    market_probability: float
    edge: float
    ev: float
    ev_gross: float
    ev_net: float
    decision: str
    # Net EV at the lower probability bound; None unless an EvGateConfig was used.
    ev_net_lower: float | None = None


@dataclass(frozen=True)
class EvGateConfig:
    """Parameters of the EV gate that replaces a fixed percentage-point haircut.

    ``z`` is the number of standard errors subtracted from the probability
    (1.645 for a one-sided 95% bound); ``min_ev_lower`` is the minimum net EV the
    lower bound must strictly exceed. Both are explicit: there are no defaults
    for ``z`` because the size of the haircut is a modelling decision.
    """

    z: float
    min_ev_lower: float = 0.0

    def __post_init__(self) -> None:
        require_non_negative(self.z, "z")
        require_non_negative(self.min_ev_lower, "min_ev_lower")


@dataclass(frozen=True)
class EvGateResult:
    passed: bool
    ev_net: float
    ev_net_lower: float


def evaluate_ev_gate(
    *,
    probability: float,
    probability_lower_bound: float,
    decimal_odds: float,
    tax_multiplier: float,
    min_ev_lower: float,
    execution_cost: float = 0.0,
) -> EvGateResult:
    """Gate on the net EV of the lower probability bound, not on the point estimate.

    ``SE(EV_net) = tau * odds * SE(p)``, so a flat cut on the point EV admits far
    more noise at long odds than at short ones; gating on the lower bound scales
    the haircut with the odds automatically. The comparison is strict: a lower
    bound exactly at ``min_ev_lower`` (zero by default) is not an opportunity.
    """
    p = require_probability(probability, "probability")
    p_lower = require_probability(probability_lower_bound, "probability_lower_bound")
    if p_lower > p:
        raise ValueError("probability_lower_bound must not exceed probability")
    odds = require_decimal_odds(decimal_odds)
    threshold = require_non_negative(min_ev_lower, "min_ev_lower")
    ev_point = expected_value_net(p, odds, tax_multiplier) - require_non_negative(
        execution_cost, "execution_cost"
    )
    ev_lower = expected_value_net_lower_bound(p_lower, odds, tax_multiplier, execution_cost)
    return EvGateResult(passed=ev_lower > threshold, ev_net=ev_point, ev_net_lower=ev_lower)


@dataclass(frozen=True)
class BetAssessment:
    """Gate result and taxed Kelly fractions for one bookmaker quote."""

    bookmaker: str
    tax_multiplier: float
    ev_net: float
    ev_net_lower: float
    gate_passed: bool
    kelly_full: float
    kelly_applied: float


def assess_bet(
    *,
    bookmaker: str,
    probability: float,
    probability_lower_bound: float,
    decimal_odds: float,
    min_ev_lower: float,
    kelly_multiplier: float,
    tax_profiles: TaxProfiles | None = None,
    execution_cost: float = 0.0,
) -> BetAssessment:
    """Per-bookmaker tau, lower-bound EV gate and taxed Kelly for one quote.

    Raises :class:`~model_buk.markets.pricing.UnknownBookmakerError` for a
    bookmaker without a registered tax multiplier. Kelly is computed on the
    lower bound and is zero whenever the gate fails, so a rejected bet carries
    no stake fraction.
    """
    tau = tax_multiplier_for(bookmaker, tax_profiles)
    gate = evaluate_ev_gate(
        probability=probability,
        probability_lower_bound=probability_lower_bound,
        decimal_odds=decimal_odds,
        tax_multiplier=tau,
        min_ev_lower=min_ev_lower,
        execution_cost=execution_cost,
    )
    kelly_full = full_kelly_fraction(probability_lower_bound, decimal_odds, tau)
    kelly_applied = (
        fractional_kelly_fraction(probability_lower_bound, decimal_odds, tau, kelly_multiplier)
        if gate.passed
        else 0.0
    )
    return BetAssessment(
        bookmaker=bookmaker,
        tax_multiplier=tau,
        ev_net=gate.ev_net,
        ev_net_lower=gate.ev_net_lower,
        gate_passed=gate.passed,
        kelly_full=kelly_full if gate.passed else 0.0,
        kelly_applied=kelly_applied,
    )


def _side_decision(
    *,
    side: str,
    probability: float,
    odds: float,
    market_probability: float,
    edge_threshold: float,
    ev_threshold: float,
    tax_multiplier: float,
    ev_gate: EvGateConfig | None,
    probability_se: float | None,
) -> MarketDecision:
    edge = probability - market_probability
    ev_gross = expected_value(probability, odds)
    ev_net = expected_value_net(probability, odds, tax_multiplier)
    ev_net_lower: float | None = None
    passes_gate = True
    if ev_gate is not None:
        assert probability_se is not None  # checked by the caller
        p_lower = probability_lower_bound(probability, probability_se, ev_gate.z)
        ev_net_lower = expected_value_net_lower_bound(p_lower, odds, tax_multiplier)
        passes_gate = ev_net_lower > ev_gate.min_ev_lower
    is_bet = edge >= edge_threshold and ev_net >= ev_threshold and passes_gate
    return MarketDecision(
        side=side,
        probability=probability,
        fair_odds=fair_odds(probability),
        market_probability=market_probability,
        edge=edge,
        ev=ev_net,
        ev_gross=ev_gross,
        ev_net=ev_net,
        decision="BET" if is_bet else "NO BET",
        ev_net_lower=ev_net_lower,
    )


def price_two_way_market(
    *,
    p_over: float,
    over_odds: float,
    under_odds: float,
    edge_threshold: float,
    ev_threshold: float,
    tax_multiplier: float,
    ev_gate: EvGateConfig | None = None,
    p_over_se: float | None = None,
) -> tuple[MarketDecision, MarketDecision]:
    """Price both sides of a two-way market.

    Without ``ev_gate`` the decision is the original one: point edge and point
    net EV against fixed thresholds. With ``ev_gate`` a side must in addition
    have a net EV lower bound (``p - z * SE`` at its own odds) strictly above
    ``ev_gate.min_ev_lower``; ``p_over_se`` is then required. Pass
    ``ev_threshold=0.0`` to drop the fixed EV cut and let the gate decide.
    """
    if ev_gate is not None and p_over_se is None:
        raise ValueError("p_over_se is required when ev_gate is given")
    if ev_gate is None and p_over_se is not None:
        # A standard error with no gate would be silently ignored.
        raise ValueError("ev_gate is required when p_over_se is given")
    if ev_gate is not None:
        require_non_negative(p_over_se, "p_over_se")  # type: ignore[arg-type]
    p_market_over, p_market_under = devig_two_way(over_odds, under_odds)
    p_under = 1.0 - p_over

    over = _side_decision(
        side="OVER",
        probability=p_over,
        odds=over_odds,
        market_probability=p_market_over,
        edge_threshold=edge_threshold,
        ev_threshold=ev_threshold,
        tax_multiplier=tax_multiplier,
        ev_gate=ev_gate,
        probability_se=p_over_se,
    )
    under = _side_decision(
        side="UNDER",
        probability=p_under,
        odds=under_odds,
        market_probability=p_market_under,
        edge_threshold=edge_threshold,
        ev_threshold=ev_threshold,
        tax_multiplier=tax_multiplier,
        ev_gate=ev_gate,
        probability_se=p_over_se,
    )
    return over, under
