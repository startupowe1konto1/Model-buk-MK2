"""Metryki probabilistyczne wymagane przez [[12 - Ewaluacja i backtesting]].

Trzymamy i Briera, i RPS, bo mierza rozne rzeczy. Brier traktuje H, D, A jako
trzy niezalezne etykiety. RPS wie, ze remis lezy miedzy zwyciestwem gospodarza
a zwyciestwem goscia, wiec karze mocniej za pomylke H<->A niz H<->D. Dla 1X2
RPS jest metryka wlasciwa, Brier - pomocnicza.
"""
import math
from typing import List, NamedTuple, Sequence, Tuple

OUTCOMES: Tuple[str, str, str] = ("H", "D", "A")

_EPS = 1e-15


def outcome_index(ftr: str) -> int:
    try:
        return OUTCOMES.index(ftr)
    except ValueError:
        raise ValueError("nieznany wynik: {0!r}".format(ftr)) from None


def brier(probs: Sequence[float], ftr: str) -> float:
    k = outcome_index(ftr)
    return sum((p - (1.0 if i == k else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss(probs: Sequence[float], ftr: str) -> float:
    k = outcome_index(ftr)
    return -math.log(max(probs[k], _EPS))


def rps(probs: Sequence[float], ftr: str) -> float:
    """Ranked Probability Score dla uporzadkowanych wynikow H < D < A."""
    k = outcome_index(ftr)
    cum_p = 0.0
    cum_a = 0.0
    total = 0.0
    for i in range(len(probs) - 1):
        cum_p += probs[i]
        cum_a += 1.0 if i == k else 0.0
        total += (cum_p - cum_a) ** 2
    return total / (len(probs) - 1)


class Bucket(NamedTuple):
    lo: float
    hi: float
    n: int
    mean_pred: float
    mean_obs: float


def calibration_buckets(
    pairs: Sequence[Tuple[float, int]], n_buckets: int = 20
) -> List[Bucket]:
    """pairs to (przewidziane prawdopodobienstwo, 1 jesli zdarzenie zaszlo)."""
    sums = [0.0] * n_buckets
    hits = [0] * n_buckets
    counts = [0] * n_buckets
    for p, y in pairs:
        # p == 1.0 idzie do ostatniego koszyka; p poza [0, 1] jest przypiecie
        # do krajca, zamiast (dla p < 0) trafiac przez idx=-1 w ostatni koszyk.
        idx = min(max(int(p * n_buckets), 0), n_buckets - 1)
        sums[idx] += p
        hits[idx] += y
        counts[idx] += 1
    out: List[Bucket] = []
    for i in range(n_buckets):
        n = counts[i]
        out.append(
            Bucket(
                lo=i / n_buckets,
                hi=(i + 1) / n_buckets,
                n=n,
                mean_pred=(sums[i] / n) if n else 0.0,
                mean_obs=(hits[i] / n) if n else 0.0,
            )
        )
    return out


def ece(pairs: Sequence[Tuple[float, int]], n_buckets: int = 20) -> float:
    """Expected Calibration Error - srednia wazona odchylen w koszykach."""
    buckets = calibration_buckets(pairs, n_buckets)
    total = sum(b.n for b in buckets)
    if not total:
        return 0.0
    return sum(b.n * abs(b.mean_pred - b.mean_obs) for b in buckets) / total
