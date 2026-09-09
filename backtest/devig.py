"""Zdejmowanie marzy bukmacherskiej z kursow.

Trzy metody, bo wybor jest hipoteza badawcza, nie detalem technicznym:

- proportional: p_i = (1/o_i) / suma(1/o). Najprostsza, zaklada ze marza jest
  rozlozona rownomiernie. Zawyza dlugie strzaly.
- power:        p_i = (1/o_i)^k, gdzie k dobrane tak, ze suma = 1. Zdejmuje
  wiecej z dlugich strzalow.
- shin:         model Shina (1993) z handlem insiderow. Parametr z to ulamek
  obrotu pochodzacy od poinformowanych; wiekszy z = wiecej marzy zdjete
  z dlugich strzalow.

Oba nieliniowe rozwiazania idziemy bisekcja - 100 iteracji daje precyzje
ponizej 1e-15 i nie wymaga pochodnych ani numpy.
"""
import math
from typing import Callable, Dict, Sequence, Tuple

_BISECT_ITERATIONS = 100


def overround(odds: Sequence[float]) -> float:
    """Nadwyzka ksiazki ponad 1. Dla 3,44% zwraca 0.0344."""
    return sum(1.0 / o for o in odds) - 1.0


def devig_proportional(odds: Sequence[float]) -> Tuple[float, ...]:
    q = [1.0 / o for o in odds]
    s = sum(q)
    return tuple(x / s for x in q)


def devig_power(odds: Sequence[float]) -> Tuple[float, ...]:
    """Szuka k takiego, ze suma (1/o_i)^k == 1."""
    q = [1.0 / o for o in odds]
    if abs(sum(q) - 1.0) < 1e-12:
        return tuple(q)

    def total(k: float) -> float:
        return sum(x ** k for x in q)

    # Suma jest malejaca w k (kazde q_i < 1). Znajdz gorna granice.
    lo, hi = 0.0, 1.0
    while total(hi) > 1.0:
        hi *= 2.0
        if hi > 1e6:  # nie powinno wystapic dla realnych kursow
            raise ValueError("nie znaleziono wykladnika dla odds={0}".format(tuple(odds)))
    for _ in range(_BISECT_ITERATIONS):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    p = [x ** k for x in q]
    s = sum(p)
    return tuple(x / s for x in p)  # domkniecie bledu numerycznego


def devig_shin(odds: Sequence[float]) -> Tuple[float, ...]:
    """Model Shina. Szuka z w [0,1) takiego, ze suma prawdopodobienstw == 1."""
    q = [1.0 / o for o in odds]
    s = sum(q)
    if abs(s - 1.0) < 1e-12:
        return tuple(q)

    def probs(z: float) -> Tuple[float, ...]:
        if z <= 0.0:
            return tuple(x / math.sqrt(s) for x in q)
        return tuple(
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (x * x) / s) - z) / (2.0 * (1.0 - z))
            for x in q
        )

    # Suma maleje wraz ze wzrostem z. Przy z=0 wynosi sqrt(s) > 1.
    lo, hi = 0.0, 0.5
    while sum(probs(hi)) > 1.0:
        hi = (hi + 1.0) / 2.0
        if hi > 1.0 - 1e-12:
            break
    for _ in range(_BISECT_ITERATIONS):
        mid = (lo + hi) / 2.0
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2.0)
    total = sum(p)
    return tuple(x / total for x in p)


METHODS: Dict[str, Callable[[Sequence[float]], Tuple[float, ...]]] = {
    "proportional": devig_proportional,
    "power": devig_power,
    "shin": devig_shin,
}
