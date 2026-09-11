"""Etap 1 backtestu: czy zdevigowane kursy Pinnacle sa skalibrowane?

Ten krok nie uzywa zadnego modelu. Sprawdza tylko, czy rynek po zdjeciu marzy
mowi prawde o czestosci zdarzen. Zamkniecie Pinnacle jest najostrzejsza
publicznie dostepna cena, wiec ECE powinno wyjsc ponizej 0,01. Wyraznie gorszy
wynik oznacza blad w loaderze albo w de-vigu, a nie odkrycie naukowe.

Uzycie:
    python3 -m backtest.report_devig
    python3 -m backtest.report_devig --out reports/devig-2026-09-09.md
"""
import argparse
import sys
from typing import List, NamedTuple, Optional, Sequence, Tuple

from backtest.devig import METHODS, overround
from backtest.loader import Match, load_matches
from backtest.metrics import aggregate_scores, calibration_buckets


class MethodScore(NamedTuple):
    method: str
    n: int
    log_loss: float
    brier: float
    rps: float
    ece: float


def _odds_for(m: Match, which: str):
    if which == "open":
        return m.o_open
    if which == "close":
        return m.o_close
    raise ValueError("which musi byc 'open' albo 'close', dostalem {0!r}".format(which))


def _score_with_pairs(matches: Sequence[Match], method: str, which: str) -> Tuple[MethodScore, List[Tuple[float, int]]]:
    """Jeden przebieg po meczach: wydobywa (probs, ftr) dla kazdego meczu i
    oddaje akumulacje log-lossu/Briera/RPS oraz budowe par
    `backtest.metrics.aggregate_scores` - to samo, co robi
    `walkforward.score_predictions` dla P_model, wiec petla akumulujaca
    istnieje raz, nie dwa razy.

    score_method korzysta z tego i odrzuca pary. _render (dla kolumny
    "close") korzysta z tego i zachowuje pary per metoda, zeby przy wyborze
    zwyciezcy nie trzeba bylo przechodzic po meczach jeszcze raz.
    """
    fn = METHODS[method]
    totals = aggregate_scores((fn(_odds_for(m, which)), m.ftr) for m in matches)
    return (
        MethodScore(method, totals.n, totals.log_loss, totals.brier, totals.rps, totals.ece),
        totals.pairs,
    )


def score_method(matches: Sequence[Match], method: str, which: str) -> MethodScore:
    score, _pairs_unused = _score_with_pairs(matches, method, which)
    return score


def best_method(matches: Sequence[Match]) -> str:
    """Metoda o najnizszym log-lossie na zamknieciu."""
    scored = [score_method(matches, name, "close") for name in sorted(METHODS)]
    return min(scored, key=lambda s: s.log_loss).method


def _render(matches: Sequence[Match]) -> str:
    if not matches:
        raise ValueError("_render: pusta lista meczy, nie ma czego raportowac")
    lines: List[str] = []
    lines.append("# Kalibracja rynku po de-vigu\n")
    lines.append("Mecze: **{0}**\n".format(len(matches)))
    oo = sum(overround(m.o_open) for m in matches) / len(matches)
    oc = sum(overround(m.o_close) for m in matches) / len(matches)
    lines.append("Overround: {0:.2f}% na PS* -> {1:.2f}% na zamknieciu\n".format(oo * 100, oc * 100))
    lines.append(
        "> `PSH/PSD/PSA` to zdjecie z budowy pliku tygodniowego (zwykle piatek),\n"
        "> nie kurs otwarcia rynku. Ponizsze 'open' czytaj jako 'piatek'.\n"
    )
    close_pairs = {}
    close_scores = {}
    for which in ("open", "close"):
        lines.append("\n## {0}\n".format(which))
        lines.append("| metoda | log-loss | Brier | RPS | ECE |")
        lines.append("|---|---:|---:|---:|---:|")
        for name in sorted(METHODS):
            if which == "close":
                s, pairs_for_name = _score_with_pairs(matches, name, which)
                close_pairs[name] = pairs_for_name
                close_scores[name] = s
            else:
                s = score_method(matches, name, which)
            lines.append(
                "| {0} | {1:.5f} | {2:.5f} | {3:.5f} | {4:.5f} |".format(
                    s.method, s.log_loss, s.brier, s.rps, s.ece
                )
            )
    # Zwyciezca pochodzi z wynikow juz policzonych powyzej w petli "close",
    # nie z osobnego wywolania best_method(matches) - dwie niezalezne sciezki
    # liczace to samo moglyby rozjechac sie na remisie.
    winner = min(close_scores.values(), key=lambda s: s.log_loss).method
    lines.append("\n**Metoda wybrana do dalszych etapow: `{0}`** (najnizszy log-loss na zamknieciu).\n".format(winner))
    lines.append("\n## Krzywa kalibracji zamkniecia ({0})\n".format(winner))
    pairs = close_pairs[winner]
    lines.append("| przedzial | n | sr. przewidziane | sr. zaobserwowane | roznica |")
    lines.append("|---|---:|---:|---:|---:|")
    for b in calibration_buckets(pairs, n_buckets=20):
        if not b.n:
            continue
        lines.append(
            "| {0:.2f}-{1:.2f} | {2} | {3:.4f} | {4:.4f} | {5:+.4f} |".format(
                b.lo, b.hi, b.n, b.mean_pred, b.mean_obs, b.mean_obs - b.mean_pred
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kalibracja rynku po de-vigu")
    ap.add_argument("--out", default=None, help="sciezka pliku markdown")
    args = ap.parse_args(argv)
    matches = load_matches()
    text = _render(matches)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("zapisano {0}".format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
