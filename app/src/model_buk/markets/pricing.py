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
    """EV BRUTTO, liczone na wyswietlanym kursie bukmachera.

    Uwaga: w Polsce podatek od zakladow (12%) jest pobierany OD STAWKI, wiec
    ten wynik nie jest tym, co realnie wraca na konto - 100 zl przy kursie
    2.00 daje 176 zl, nie 200. Ta funkcja NIE nadaje sie do bramki bet/no-bet
    w tym rynku. Do decyzji uzywaj `expected_value_net`, ktora bierze mnoznik
    podatkowy jako jawny argument.
    """
    return probability * decimal_odds - 1.0


def expected_value_net(probability: float, decimal_odds: float, tax_multiplier: float) -> float:
    """EV PO PODATKU od stawki - to ta wartosc ma decydowac o bet/no-bet.

    `tax_multiplier` musi byc podany jawnie przez wywolujacego (np. 0.88 dla
    polskiego standardu: podatek 12% od stawki). Ten projekt mial juz raz
    bug polegajacy na tym, ze brak jawnej definicji "EV_net" w dokumentacji
    pozwolil komus podstawic pod ta nazwe EV brutto - realny zaklad z pilotu
    (p=0.550, kurs=1.93) mial EV brutto +6,15%, a EV netto -6,59%. Dlatego
    ta funkcja nie ma wartosci domyslnej dla mnoznika: brak jawnej decyzji
    o rezimie podatkowym ma wywalic blad, a nie po cichu zalozyc 1.0.
    """
    return probability * (tax_multiplier * decimal_odds) - 1.0
