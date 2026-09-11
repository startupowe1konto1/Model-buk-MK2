"""CLI backtestu CLV.

Uzycie:
    python3 -m backtest.run_backtest
    python3 -m backtest.run_backtest --tau betclic_notax --ev-min 0.05
    python3 -m backtest.run_backtest --devig shin --out reports/backtest-clv
"""
import argparse
from typing import Optional, Sequence

from backtest.baseline import restrict_to_betting_seasons
from backtest.devig import METHODS
from backtest.gate import TAU
from backtest.loader import load_matches
from backtest.report_devig import best_method, score_method
from backtest.report_io import write_or_print
from backtest.walkforward import run, score_predictions, summarize

# Sezon zaczyna liczyc sie jako "testowy" (model juz obstawia) od tego samego
# progu, ktorego `run()` uzywa domyslnie. Trzymany tu jako nazwana stala, a
# nie powielony literal "2", bo restrict_to_betting_seasons() ponizej MUSI
# wskazywac dokladnie te same mecze, na ktorych `run()` zbiera `predictions` -
# inaczej n modelu i n rynku w tabeli kalibracji przestalyby byc porownywalne.
MIN_TRAIN_SEASONS = 2


def _markdown(summary, tau_name, devig_method, ev_min, model_score, market_score) -> str:
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
    lines.append("\n## Kalibracja: model kontra rynek (zamkniecie)\n")
    lines.append(
        "ECE (blad kalibracji) mowi, o ile srednio przewidywane prawdopodobienstwo "
        "roznilo sie od tego, jak czesto dany wynik naprawde sie zdarzal. Liczony tu "
        "na **{0}** meczach - kazdym meczu sezonow testowych, nie tylko na tych "
        "{1}, ktore przeszly bramke EV powyzej. Liczenie samej bramki dawaloby "
        "wynik obciazony w strone przypadkow, w ktorych model i tak juz roznil "
        "sie od rynku.\n".format(model_score.n, summary["n_bets"])
    )
    lines.append("| kto | n | log-loss | Brier | RPS | ECE |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    lines.append(
        "| model | {0} | {1:.5f} | {2:.5f} | {3:.5f} | {4:.5f} |".format(
            model_score.n, model_score.log_loss, model_score.brier, model_score.rps, model_score.ece
        )
    )
    lines.append(
        "| rynek (`{0}`, zamkniecie) | {1} | {2:.5f} | {3:.5f} | {4:.5f} | {5:.5f} |".format(
            devig_method, market_score.n, market_score.log_loss, market_score.brier, market_score.rps, market_score.ece
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

    # JEDNO przejscie: run() zbiera i selekcje (do CLV), i predictions dla
    # kazdego meczu sezonu testowego (do kalibracji) w tym samym przebiegu -
    # patrz WalkForwardResult w walkforward.py. Drugie wywolanie run() tylko
    # po to, zeby zebrac predykcje, powtorzyloby dokladnie ten zbedny przebieg,
    # ktory poprzedni review zlapal w report_devig.
    result = run(matches, devig_method, TAU[args.tau], ev_min=args.ev_min, min_train_seasons=MIN_TRAIN_SEASONS)
    summary = summarize(result.selections)
    model_score = score_predictions(result.predictions)

    # Rynek liczony na TYCH SAMYCH meczach co predictions (sezony testowe),
    # zeby n po obu stronach tabeli bylo porownywalne - patrz MIN_TRAIN_SEASONS.
    test_matches = restrict_to_betting_seasons(matches, MIN_TRAIN_SEASONS)
    market_score = score_method(test_matches, devig_method, "close")

    text = _markdown(summary, args.tau, devig_method, args.ev_min, model_score, market_score)

    json_out = dict(summary)
    json_out["model_calibration"] = model_score._asdict()
    json_out["market_calibration"] = market_score._asdict()

    write_or_print(text, json_out, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
