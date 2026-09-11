"""Closing Line Value.

Definicja z handoffu: CLV = fair_open / fair_close - 1, liczone na CENACH po
zdjeciu marzy. Poniewaz fair_odds = 1/p, jest to rownowazne p_close / p_open - 1.
Dodatni CLV znaczy, ze rynek ruszyl sie w strone selekcji, ktora wzielismy.

CLV, a nie zysk, jest metryka pierwszorzedna, bo przy kilkuset zakladach zysk
jest zdominowany przez wariancje, a CLV mierzy jakosc ceny natychmiast.
"""
import statistics
from collections import defaultdict
from typing import Callable, Dict, List, Sequence, TypeVar

T = TypeVar("T")


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


def group_by(
    items: Sequence[T],
    key_fn: Callable[[T], str],
    p_open_fn: Callable[[T], float],
    p_close_fn: Callable[[T], float],
) -> Dict[str, Dict[str, object]]:
    """Grupuje `items` (kazdy z atrybutem `.clv`) wg `key_fn` i liczy dla kazdej
    grupy n / sredni CLV / odsetek pobicia zamkniecia.

    Wspolny szkielet dla `walkforward.summarize` (grupuje `Selection` po
    sezonie/lidze) i `baseline.summarize` (to samo dla `Leg`) - obie strony
    roznily sie tylko nazwami pol, z ktorych czyta sie p_open/p_close
    (`p_market_open`/`p_market_close` kontra `p_open`/`p_close`), wiec te dwa
    dostepy sa jedynym, co wolajacy musi podac.
    """
    buckets: Dict[str, List[T]] = defaultdict(list)
    for it in items:
        buckets[key_fn(it)].append(it)
    return {
        k: {
            "n": len(v),
            "mean_clv": statistics.mean(x.clv for x in v),
            "beat_close_rate": sum(1 for x in v if beat_close(p_open_fn(x), p_close_fn(x))) / len(v),
        }
        for k, v in sorted(buckets.items())
    }
