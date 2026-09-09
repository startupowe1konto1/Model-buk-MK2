"""CLI backtestu CLV.

Uzycie:
    python3 -m backtest.run_backtest
    python3 -m backtest.run_backtest --tau betclic_notax --ev-min 0.05
    python3 -m backtest.run_backtest --devig shin --out reports/backtest-clv
"""
import argparse
import json
import os
from typing import Optional, Sequence

from backtest.devig import METHODS
from backtest.gate import TAU
from backtest.loader import load_matches
from backtest.report_devig import best_method
from backtest.walkforward import run, summarize


def _markdown(summary, tau_name, devig_method, ev_min) -> str:
    lines = ["# Backtest CLV\n"]
    lines.append(
        "Rezim `{0}` (tau={1}), de-vig `{2}`, prog EV_net >= {3:.2f}\n".format(
            tau_name, TAU[tau_name], devig_method, ev_min
        )
    )
    lines.append(
        "> Cena wejscia to kolumny `PSH/PSD/PSA`, czyli zdjecie z budowy pliku\n"
        "> tygodniowego (zwykle piatek), a nie kurs otwarcia rynku. Mierzony CLV\n"
        "> to ruch piatek -> zamkniecie: konserwatywny podzbior prawdziwego CLV.\n"
    )
    lines.append("\n## Wynik zbiorczy\n")
    lines.append("| miara | wartosc |")
    lines.append("|---|---:|")
    lines.append("| zakladow | {0} |".format(summary["n_bets"]))
    lines.append("| sredni CLV | {0:+.4%} |".format(summary["mean_clv"]))
    lines.append("| mediana CLV | {0:+.4%} |".format(summary["median_clv"]))
    lines.append("| pobito zamkniecie | {0:.2%} |".format(summary["beat_close_rate"]))
    lines.append("| trafien | {0:.2%} |".format(summary["hit_rate"]))
    for title, key in (("sezonach", "by_season"), ("ligach", "by_league")):
        lines.append("\n## Po {0}\n".format(title))
        lines.append("| klucz | n | sredni CLV | pobito zamkniecie |")
        lines.append("|---|---:|---:|---:|")
        for k, v in summary[key].items():
            lines.append(
                "| {0} | {1} | {2:+.4%} | {3:.2%} |".format(
                    k, v["n"], v["mean_clv"], v["beat_close_rate"]
                )
            )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest CLV na 1X2")
    ap.add_argument("--tau", default="pl_standard", choices=sorted(TAU))
    ap.add_argument("--devig", default=None, choices=sorted(METHODS),
                    help="domyslnie metoda o najnizszym log-lossie na zamknieciu")
    ap.add_argument("--ev-min", type=float, default=0.05)
    ap.add_argument("--out", default=None, help="prefiks sciezki, bez rozszerzenia")
    args = ap.parse_args(argv)

    matches = load_matches()
    devig_method = args.devig or best_method(matches)
    print("mecze: {0} | de-vig: {1} | tau: {2}".format(len(matches), devig_method, args.tau))

    selections = run(matches, devig_method, TAU[args.tau], ev_min=args.ev_min)
    summary = summarize(selections)
    text = _markdown(summary, args.tau, devig_method, args.ev_min)

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out + ".md", "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(args.out + ".json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
        print("zapisano {0}.md i {0}.json".format(args.out))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
