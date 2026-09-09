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
from typing import List, NamedTuple, Optional, Sequence

from backtest.devig import METHODS, overround
from backtest.loader import Match, load_matches
from backtest.metrics import (
    brier,
    calibration_buckets,
    ece,
    log_loss,
    outcome_index,
    rps,
)


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


def score_method(matches: Sequence[Match], method: str, which: str) -> MethodScore:
    fn = METHODS[method]
    ll = br = rp = 0.0
    pairs = []
    for m in matches:
        probs = fn(_odds_for(m, which))
        ll += log_loss(probs, m.ftr)
        br += brier(probs, m.ftr)
        rp += rps(probs, m.ftr)
        k = outcome_index(m.ftr)
        for i, p in enumerate(probs):
            pairs.append((p, 1 if i == k else 0))
    n = len(matches)
    if not n:
        return MethodScore(method, 0, 0.0, 0.0, 0.0, 0.0)
    return MethodScore(method, n, ll / n, br / n, rp / n, ece(pairs))


def best_method(matches: Sequence[Match]) -> str:
    """Metoda o najnizszym log-lossie na zamknieciu."""
    scored = [score_method(matches, name, "close") for name in sorted(METHODS)]
    return min(scored, key=lambda s: s.log_loss).method


def _render(matches: Sequence[Match]) -> str:
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
    for which in ("open", "close"):
        lines.append("\n## {0}\n".format(which))
        lines.append("| metoda | log-loss | Brier | RPS | ECE |")
        lines.append("|---|---:|---:|---:|---:|")
        for name in sorted(METHODS):
            s = score_method(matches, name, which)
            lines.append(
                "| {0} | {1:.5f} | {2:.5f} | {3:.5f} | {4:.5f} |".format(
                    s.method, s.log_loss, s.brier, s.rps, s.ece
                )
            )
    winner = best_method(matches)
    lines.append("\n**Metoda wybrana do dalszych etapow: `{0}`** (najnizszy log-loss na zamknieciu).\n".format(winner))
    lines.append("\n## Krzywa kalibracji zamkniecia ({0})\n".format(winner))
    fn = METHODS[winner]
    pairs = []
    for m in matches:
        probs = fn(m.o_close)
        k = outcome_index(m.ftr)
        for i, p in enumerate(probs):
            pairs.append((p, 1 if i == k else 0))
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
