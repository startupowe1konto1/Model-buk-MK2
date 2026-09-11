"""Baseline zerowy CLV: co pokazuje CLV, kiedy nie ma modelu i nie ma bramki.

Kazdy mecz ma trzy nogi (H, D, A) i kazda z nich liczy sie tak, jakby zawsze
padal na niej zaklad - bez P_model, bez EV_net, bez selekcji. To punkt zero,
wzgledem ktorego trzeba czytac wynik modelu: jesli model daje sredni CLV
nizszy niz to, co dostaje sie za nic, model nie ma przewagi.

Zakres meczow jest ograniczony do sezonow, w ktorych walk-forward
(`backtest.walkforward.run`) w ogole obstawia - patrz `betting_seasons`.
Liczenie po wszystkich meczach dodaje sezony czysto treningowe, ktorych model
nigdy nie widzi jako kandydatow do zakladu, a ktore majac inny (zazwyczaj
lepszy) profil overroundu, zniekstalcaja punkt odniesienia.
"""
import argparse
import statistics
from typing import Dict, List, NamedTuple, Sequence

from backtest.clv import beat_close, clv_from_probs, group_by
from backtest.devig import METHODS
from backtest.loader import Match, load_matches, season_of
from backtest.metrics import OUTCOMES
from backtest.report_io import write_or_print
from backtest.walkforward import seasons as _all_seasons


class Leg(NamedTuple):
    match: Match
    outcome: str
    p_open: float
    p_close: float
    clv: float


def betting_seasons(matches: Sequence[Match], min_train_seasons: int = 2) -> List[str]:
    """Sezony, w ktorych walk-forward zaczyna obstawiac.

    Musi wolac te sama funkcje (`walkforward.seasons`), ktora porzadkuje
    sezony wewnatrz `walkforward.run` - tam pierwsze `min_train_seasons`
    sezonow jest czysto treningowe (patrz petla `if i < min_train_seasons`),
    a obstawianie zaczyna sie od indeksu `min_train_seasons`. Ten modul nie
    powtarza tej granicy jako literalu, zeby zmiana `min_train_seasons` w
    jednym miejscu nie rozjechala baseline'u z tym, co faktycznie obstawia
    walk-forward.
    """
    return _all_seasons(matches)[min_train_seasons:]


def restrict_to_betting_seasons(matches: Sequence[Match], min_train_seasons: int = 2) -> List[Match]:
    keep = set(betting_seasons(matches, min_train_seasons))
    return [m for m in matches if season_of(m.date) in keep]


def legs(matches: Sequence[Match], devig_method: str) -> List[Leg]:
    """CLV na kazdej z trzech nog kazdego meczu, bez modelu i bez bramki."""
    devig = METHODS[devig_method]
    out: List[Leg] = []
    for m in matches:
        p_open = devig(m.o_open)
        p_close = devig(m.o_close)
        for idx, outcome in enumerate(OUTCOMES):
            out.append(
                Leg(
                    match=m,
                    outcome=outcome,
                    p_open=p_open[idx],
                    p_close=p_close[idx],
                    clv=clv_from_probs(p_open[idx], p_close[idx]),
                )
            )
    return out


def summarize(legs_: Sequence[Leg]) -> Dict[str, object]:
    """Ten sam format co `walkforward.summarize` (bez `hit_rate` - baseline nie
    ma modelu, ktory mogloby cos "trafic")."""
    if not legs_:
        return {
            "n_legs": 0,
            "mean_clv": 0.0,
            "median_clv": 0.0,
            "beat_close_rate": 0.0,
            "by_season": {},
            "by_league": {},
        }
    clvs = [l.clv for l in legs_]

    def _group(key_fn):
        return group_by(legs_, key_fn, lambda l: l.p_open, lambda l: l.p_close)

    return {
        "n_legs": len(legs_),
        "mean_clv": statistics.mean(clvs),
        "median_clv": statistics.median(clvs),
        "beat_close_rate": sum(1 for l in legs_ if beat_close(l.p_open, l.p_close)) / len(legs_),
        "by_season": _group(lambda l: season_of(l.match.date)),
        "by_league": _group(lambda l: l.match.div),
    }


def _markdown(min_train_seasons: int, n_total: int, b_seasons: Sequence[str], n_test: int, per_method: Dict[str, Dict[str, object]]) -> str:
    lines = ["# Baseline zerowy CLV (bez modelu)\n"]
    lines.append(
        "Baseline liczy CLV dla kazdej z trzech nog (H/D/A) kazdego meczu, bez\n"
        "modelu i bez bramki EV - jakby kazda noga byla zakladem. Ograniczony do\n"
        "sezonow, w ktorych walk-forward w ogole obstawia (`min_train_seasons={0}`):\n"
        "**{1}** ({2} sezonow, {3} z {4} meczow ogolem).\n".format(
            min_train_seasons, ", ".join(b_seasons), len(b_seasons), n_test, n_total
        )
    )
    lines.append(
        "> Liczenie na wszystkich meczach dokladalyby sezony czysto treningowe,\n"
        "> ktorych model nigdy nie widzi jako kandydatow do zakladu.\n"
    )
    lines.append("\n## Wynik zbiorczy\n")
    lines.append("| metoda | nog | sredni CLV | mediana CLV | pobito zamkniecie |")
    lines.append("|---|---:|---:|---:|---:|")
    for name in sorted(METHODS):
        s = per_method[name]
        lines.append(
            "| {0} | {1} | {2:+.4%} | {3:+.4%} | {4:.2%} |".format(
                name, s["n_legs"], s["mean_clv"], s["median_clv"], s["beat_close_rate"]
            )
        )
    for name in sorted(METHODS):
        s = per_method[name]
        for title, key in (("sezonach", "by_season"), ("ligach", "by_league")):
            lines.append("\n## {0} - po {1}\n".format(name, title))
            lines.append("| klucz | n | sredni CLV | pobito zamkniecie |")
            lines.append("|---|---:|---:|---:|")
            for k, v in s[key].items():
                lines.append(
                    "| {0} | {1} | {2:+.4%} | {3:.2%} |".format(
                        k, v["n"], v["mean_clv"], v["beat_close_rate"]
                    )
                )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Baseline zerowy CLV (bez modelu, bez bramki)")
    ap.add_argument("--min-train-seasons", type=int, default=2)
    ap.add_argument("--out", default=None, help="prefiks sciezki, bez rozszerzenia")
    args = ap.parse_args(argv)

    matches = load_matches()
    b_seasons = betting_seasons(matches, args.min_train_seasons)
    test_matches = restrict_to_betting_seasons(matches, args.min_train_seasons)
    print(
        "mecze testowe: {0} (z {1} ogolem) | sezony: {2}".format(
            len(test_matches), len(matches), ", ".join(b_seasons)
        )
    )

    per_method = {name: summarize(legs(test_matches, name)) for name in sorted(METHODS)}
    text = _markdown(args.min_train_seasons, len(matches), b_seasons, len(test_matches), per_method)

    json_out = {
        "min_train_seasons": args.min_train_seasons,
        "betting_seasons": b_seasons,
        "n_matches_total": len(matches),
        "n_matches_test": len(test_matches),
        "methods": per_method,
    }

    write_or_print(text, json_out, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
