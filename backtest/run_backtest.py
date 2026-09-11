"""CLI backtestu CLV.

Uzycie:
    python3 -m backtest.run_backtest
    python3 -m backtest.run_backtest --tau betclic_notax --ev-min 0.05
    python3 -m backtest.run_backtest --devig shin --out reports/backtest-clv
"""
import argparse
from typing import Optional, Sequence

from backtest.baseline import legs as baseline_legs
from backtest.baseline import restrict_to_betting_seasons
from backtest.baseline import summarize as baseline_summarize
from backtest.devig import METHODS
from backtest.gate import TAU
from backtest.loader import load_matches
from backtest.report_devig import best_method, score_method
from backtest.report_io import write_or_print
from backtest.walkforward import Selection, run, score_predictions, summarize

# Sezon zaczyna liczyc sie jako "testowy" (model juz obstawia) od tego samego
# progu, ktorego `run()` uzywa domyslnie. Trzymany tu jako nazwana stala, a
# nie powielony literal "2", bo restrict_to_betting_seasons() ponizej MUSI
# wskazywac dokladnie te same mecze, na ktorych `run()` zbiera `predictions` -
# inaczej n modelu i n rynku w tabeli kalibracji przestalyby byc porownywalne.
MIN_TRAIN_SEASONS = 2


def distinct_match_count(selections: Sequence[Selection]) -> int:
    """Liczba ODREBNYCH meczow wsrod selekcji - NIE liczba selekcji.

    `Selection.match` niesie caly obiekt meczu, wiec dwie selekcje
    wyprodukowane przez ten sam mecz (dwa rozne wyniki, oba przechodzace
    bramke tego samego dnia) maja identyczny `.match` i licza sie tu raz.
    Bez tego rozroznienia `len(selections)` zawyza efektywna probe - patrz
    komentarz przy odpowiednim wierszu w `_markdown`."""
    return len({s.match for s in selections})


def _markdown(
    summary,
    tau_name,
    devig_method,
    ev_min,
    model_score,
    market_score,
    n_matches,
    baseline_summary,
) -> str:
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
    lines.append("| meczow (odrebnych) | {0} |".format(n_matches))
    lines.append("| sredni CLV | {0:+.4%} |".format(summary["mean_clv"]))
    lines.append("| mediana CLV | {0:+.4%} |".format(summary["median_clv"]))
    lines.append("| pobito zamkniecie | {0:.2%} |".format(summary["beat_close_rate"]))
    lines.append("| trafien | {0:.2%} |".format(summary["hit_rate"]))
    lines.append(
        "\n> Liczba zakladow to nie liczba odrebnych meczow (patrz wiersz\n"
        "> \"meczow (odrebnych)\" wyzej). Jeden mecz moze dac dwie selekcje:\n"
        "> model ocenia trzy mozliwe wyniki (gospodarz / remis / gosc), a jego\n"
        "> roznice zdania z rynkiem na tych trzech wynikach w przyblizeniu\n"
        "> sumuja sie do zera, wiec czesto dwie z trzech ocen wychodza jako\n"
        "> \"warte zakladu\" jednoczesnie. Te dwie selekcje z tego samego meczu\n"
        "> nie sa niezaleznymi obserwacjami - ich CLV z definicji porusza sie\n"
        "> w przeciwne strony (kurs na jeden wynik tanieje dokladnie wtedy, gdy\n"
        "> na inny drozeje). Liczac sama liczbe zakladow, zawyzamy wiec to,\n"
        "> jak duza naprawde jest probka.\n"
    )
    lines.append(
        "\n> Dodatnia srednia CLV przy ujemnej medianie (widoczne w tabeli\n"
        "> wyzej) to wlasciwosc samego sposobu liczenia, nie dowod przewagi.\n"
        "> CLV to iloraz cen (cena po ruchu rynku / cena, po ktorej wzieto\n"
        "> zaklad, minus jeden): ma dolna granice na -100% (cena nie moze\n"
        "> spasc bardziej), ale nie ma gornej granicy (cena moze urosnac\n"
        "> dowolnie duzo). Garstka duzych, korzystnych ruchow ceny wystarczy,\n"
        "> zeby podciagnac srednia w gore, mimo ze typowy pojedynczy zaklad\n"
        "> traci troche na cenie. Dlatego mediana - a nie srednia - pokazuje,\n"
        "> co spotyka typowy zaklad.\n"
    )
    lines.append("\n## Model kontra baseline (bez modelu)\n")
    lines.append(
        "Baseline to CLV policzone na WSZYSTKICH trzech nogach (H/D/A) kazdego\n"
        "meczu sezonow testowych, bez modelu i bez bramki EV - punkt zero,\n"
        "wzgledem ktorego czyta sie wynik modelu. Pelne rozbicie po wszystkich\n"
        "trzech metodach de-vigu, sezonach i ligach jest w\n"
        "`reports/baseline-null.md`; tutaj ten sam de-vig (`{0}`), co uzyty dla\n"
        "modelu powyzej, zestawiony wprost z wynikiem modelu w jednej tabeli,\n"
        "zeby nie trzeba bylo otwierac drugiego pliku.\n".format(devig_method)
    )
    lines.append(
        "To zestawienie rozstrzygaja `mediana CLV` i `pobito zamkniecie`, nie\n"
        "`sredni CLV` (patrz notka o artefakcie ilorazowym wyzej). Na tych\n"
        "danych model wypada gorzej niz baseline na obu tych miarach - gorzej\n"
        "niz losowe branie wszystkich trzech nog kazdego meczu.\n"
    )
    lines.append("| kto | n (zakladow / nog) | sredni CLV | mediana CLV | pobito zamkniecie |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        "| model | {0} | {1:+.4%} | {2:+.4%} | {3:.2%} |".format(
            summary["n_bets"], summary["mean_clv"], summary["median_clv"], summary["beat_close_rate"]
        )
    )
    lines.append(
        "| baseline (bez modelu) | {0} | {1:+.4%} | {2:+.4%} | {3:.2%} |".format(
            baseline_summary["n_legs"],
            baseline_summary["mean_clv"],
            baseline_summary["median_clv"],
            baseline_summary["beat_close_rate"],
        )
    )
    for title, key in (("sezonach", "by_season"), ("ligach", "by_league")):
        lines.append("\n## Po {0}\n".format(title))
        if key == "by_season":
            lines.append(
                "> Patrzac na tabele ponizej latwo pomyslec: CLV z roku na rok\n"
                "> systematycznie spada, wiec rynek z czasem nauczyl sie wyceniac\n"
                "> mecze lepiej. To jest wniosek bledny. Ten sam spadek wychodzi\n"
                "> identycznie, gdy z backtestu wyjmie sie caly model i cala\n"
                "> bramke zakladow - wiec to nie jest cecha naszych typow, tylko\n"
                "> czegos w samych danych wejsciowych. Kurs, po ktorym tu liczymy\n"
                "> wejscie w zaklad (kolumny `PSH/PSD/PSA`), to zdjecie robione raz\n"
                "> w tygodniu przy budowie pliku przez football-data.co.uk, a to,\n"
                "> jak dokladnie to zdjecie jest robione, zmienilo sie na\n"
                "> przestrzeni lat: srednia marza wliczona w te kolumny urosla z\n"
                "> 2.46% w sezonie 2015/16 do 5.08% w sezonie 2024/25, podczas gdy\n"
                "> marza na zamknieciu przez caly ten czas trzyma sie okolo 3.9%.\n"
                "> Im pozniejszy sezon, tym gorszy (bardziej zamarzowany) jest wiec\n"
                "> kurs startowy pomiaru - i dlatego ruch do zamkniecia wyglada na\n"
                "> mniejszy, chociaz rynek wcale nie zrobil sie bardziej efektywny.\n"
                "> Wniosek: liczb z tej tabeli nie wolno czytac jako trendu\n"
                "> efektywnosci rynku miedzy sezonami. W obrebie jednego, pojedynczego\n"
                "> sezonu ponizsze liczby sa jak najbardziej wiarygodne.\n"
            )
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
    n_matches = distinct_match_count(result.selections)

    # Rynek liczony na TYCH SAMYCH meczach co predictions (sezony testowe),
    # zeby n po obu stronach tabeli bylo porownywalne - patrz MIN_TRAIN_SEASONS.
    test_matches = restrict_to_betting_seasons(matches, MIN_TRAIN_SEASONS)
    market_score = score_method(test_matches, devig_method, "close")

    # Baseline zerowy (Task 1) na TYCH SAMYCH meczach co rynek powyzej i tym
    # samym de-vigu co model, zeby wiersz baseline w tabeli ponizej byl
    # naprawde porownywalny z wierszem modelu, a nie z innym zakresem danych.
    baseline_summary = baseline_summarize(baseline_legs(test_matches, devig_method))

    text = _markdown(
        summary, args.tau, devig_method, args.ev_min, model_score, market_score, n_matches, baseline_summary
    )

    json_out = dict(summary)
    json_out["n_matches"] = n_matches
    json_out["model_calibration"] = model_score._asdict()
    json_out["market_calibration"] = market_score._asdict()
    json_out["baseline"] = {
        "devig_method": devig_method,
        "n_legs": baseline_summary["n_legs"],
        "mean_clv": baseline_summary["mean_clv"],
        "median_clv": baseline_summary["median_clv"],
        "beat_close_rate": baseline_summary["beat_close_rate"],
    }

    write_or_print(text, json_out, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
