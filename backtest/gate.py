"""Bramka decyzyjna z [[22 - Zasady modelu]] paragraf 4-5.

Polski podatek 12% pobierany jest OD STAWKI, wiec wyswietlany kurs jest brutto:
100 zl po kursie 2.00 zwraca 176 zl, nie 200. Konsekwencja: bramka MUSI
dzialac na EV_net, nigdy na EV_gross.

Historia tej pomylki: MB-0001 byl jedynym kandydatem, ktory przeszedl przez
bramke w calym pilocie 27.08.2026. Mial EV_gross +6,15% i EV_net -6,59%.
Luka strukturalna polegala na tym, ze nota 22 paragraf 5 uzywala terminu
EV_net, ktorego paragraf 4 nigdzie nie definiowal - implementujacy musial
sobie dopowiedziec i dopowiedzial EV_gross.
"""
from typing import Dict, NamedTuple

TAU: Dict[str, float] = {
    "pl_standard": 0.88,      # Polska, podatek 12% od stawki
    "betclic_degraded": 0.94, # Betclic po degradacji promocji
    "betclic_notax": 1.00,    # Betclic bez podatku na SOLO
}


def ev_gross(p: float, odds: float) -> float:
    return p * odds - 1.0


def ev_net(p: float, odds: float, tau: float) -> float:
    return p * (tau * odds) - 1.0


def break_even_po(tau: float) -> float:
    """Iloczyn p*o, przy ktorym EV_net wynosi zero."""
    return 1.0 / tau


def threshold_po(tau: float, ev_min: float = 0.05) -> float:
    """Iloczyn p*o wymagany, zeby EV_net osiagnal prog."""
    return (1.0 + ev_min) / tau


class Decision(NamedTuple):
    bet: bool
    ev_net: float
    edge: float
    reason: str


def decide(
    p_model: float,
    odds: float,
    tau: float,
    p_market: float,
    ev_min: float = 0.05,
    edge_min: float = 0.0,
) -> Decision:
    edge = p_model - p_market
    value = ev_net(p_model, odds, tau)
    if edge <= edge_min:
        return Decision(False, value, edge, "edge {0:.4f} <= edge_min {1:.4f}".format(edge, edge_min))
    if value < ev_min:
        return Decision(False, value, edge, "ev_net {0:.4f} < ev_min {1:.4f}".format(value, ev_min))
    return Decision(True, value, edge, "ok")
