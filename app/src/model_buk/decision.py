from __future__ import annotations

from dataclasses import dataclass

from model_buk.markets.pricing import devig_two_way, expected_value, expected_value_net, fair_odds


@dataclass(frozen=True)
class MarketDecision:
    side: str
    probability: float
    fair_odds: float
    market_probability: float
    edge: float
    ev_gross: float
    ev_net: float
    decision: str


def price_two_way_market(
    *,
    p_over: float,
    over_odds: float,
    under_odds: float,
    edge_threshold: float,
    ev_threshold: float,
    tax_multiplier: float,
) -> tuple[MarketDecision, MarketDecision]:
    """Wycenia obie strony rynku i stosuje bramke bet/no-bet.

    `tax_multiplier` nie ma wartosci domyslnej celowo: bramka decyduje na
    EV_net (po podatku), nigdy na EV_gross (przed podatkiem) - patrz
    docstring `expected_value_net`. Kazdy wywolujacy musi jawnie podac,
    jaki rezim podatkowy stosuje.
    """
    p_market_over, p_market_under = devig_two_way(over_odds, under_odds)
    p_under = 1.0 - p_over

    over_edge = p_over - p_market_over
    under_edge = p_under - p_market_under
    over_ev_gross = expected_value(p_over, over_odds)
    under_ev_gross = expected_value(p_under, under_odds)
    over_ev_net = expected_value_net(p_over, over_odds, tax_multiplier)
    under_ev_net = expected_value_net(p_under, under_odds, tax_multiplier)

    over = MarketDecision(
        side="OVER",
        probability=p_over,
        fair_odds=fair_odds(p_over),
        market_probability=p_market_over,
        edge=over_edge,
        ev_gross=over_ev_gross,
        ev_net=over_ev_net,
        decision="BET" if over_edge >= edge_threshold and over_ev_net >= ev_threshold else "NO BET",
    )
    under = MarketDecision(
        side="UNDER",
        probability=p_under,
        fair_odds=fair_odds(p_under),
        market_probability=p_market_under,
        edge=under_edge,
        ev_gross=under_ev_gross,
        ev_net=under_ev_net,
        decision="BET" if under_edge >= edge_threshold and under_ev_net >= ev_threshold else "NO BET",
    )
    return over, under
