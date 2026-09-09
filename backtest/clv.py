"""Closing Line Value.

Definicja z handoffu: CLV = fair_open / fair_close - 1, liczone na CENACH po
zdjeciu marzy. Poniewaz fair_odds = 1/p, jest to rownowazne p_close / p_open - 1.
Dodatni CLV znaczy, ze rynek ruszyl sie w strone selekcji, ktora wzielismy.

CLV, a nie zysk, jest metryka pierwszorzedna, bo przy kilkuset zakladach zysk
jest zdominowany przez wariancje, a CLV mierzy jakosc ceny natychmiast.
"""


def _check(p: float, name: str) -> None:
    if not (0.0 < p < 1.0):
        raise ValueError("{0} musi lezec w (0,1), dostalem {1!r}".format(name, p))


def clv_from_probs(p_open: float, p_close: float) -> float:
    _check(p_open, "p_open")
    _check(p_close, "p_close")
    return p_close / p_open - 1.0


def clv_from_fair_odds(fair_open: float, fair_close: float) -> float:
    if fair_open <= 1.0 or fair_close <= 1.0:
        raise ValueError("fair odds musza byc > 1")
    return fair_open / fair_close - 1.0


def beat_close(p_open: float, p_close: float) -> bool:
    return clv_from_probs(p_open, p_close) > 0.0
