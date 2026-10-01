"""Evaluate the league-frequency baseline on 1X2 with the common evaluation framework.

Offline only: reads the SQLite match table built by scripts/build_history_tables.py
(read-only) and makes no API calls.  It runs the nested walk-forward test bench of
``model_buk.evaluation.framework`` for the seasons 2021-2025 in all leagues:

- baseline = outcome frequencies of the league, estimated on matches that finished
  before each matchday (refit before every matchday);
- per league and pooled: log-loss, Brier, RPS with matchday-cluster bootstrap
  confidence intervals;
- pooled CORP reliability decomposition (MCB - DSC + UNC) per outcome class;
- a demonstration of the significance machinery on real data: league frequencies
  versus one global set of frequencies (Diebold-Mariano with HAC variance, Holm and
  Benjamini-Hochberg across leagues);
- the look-ahead test on the real table (shuffling / mirroring the outcomes of the
  future must not change earlier forecasts).

The result is written as a short Polish report.

Usage (from the app/ directory):

    .venv\\Scripts\\python scripts\\run_eval_baseline.py

    .venv\\Scripts\\python scripts\\run_eval_baseline.py --resamples 500 --skip-leakage
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation.framework import (  # noqa: E402
    MARKET_1X2,
    WalkForwardConfig,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
    global_frequency_spec,
    league_frequency_spec,
    prepare_matches,
)
from model_buk.evaluation.reliability import corp_diagram_table, corp_reliability  # noqa: E402
from model_buk.paths import data_root  # noqa: E402

DEFAULT_DB = data_root() / "api-football" / "tables" / "history.sqlite"
DEFAULT_REPORT = Path(__file__).resolve().parents[2] / "reports" / "Rama ewaluacji - baseline.md"
MATCH_COLUMNS = (
    "fixture_id, league_code, season, round, kickoff_utc, status_short, home_goals, away_goals"
)
CLASS_NAMES_PL = {"home": "gospodarz", "draw": "remis", "away": "gość"}


def load_matches(db_path: Path) -> pd.DataFrame:
    """Read the ``matches`` table without ever writing to the database."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as connection:
        return pd.read_sql_query(f"SELECT {MATCH_COLUMNS} FROM matches", connection)


def num(value: float, digits: int = 4) -> str:
    """Polish decimal comma; NaN shows as a dash (a missing value is never a zero)."""
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def with_interval(row: pd.Series, metric: str, digits: int = 4) -> str:
    return f"{num(row[metric], digits)} [{num(row[metric + '_lo'], digits)}; {num(row[metric + '_hi'], digits)}]"


def thousands(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def build_report(
    *,
    db_path: Path,
    config: WalkForwardConfig,
    baseline,
    table: pd.DataFrame,
    by_season: pd.DataFrame,
    corp_tables: dict[str, pd.DataFrame],
    corp_diagram: pd.DataFrame,
    versus: pd.DataFrame,
    leakage,
    global_seconds: float,
    total_seconds: float,
    n_resamples: int,
    n_sim: int,
) -> str:
    leagues = table.iloc[:-1]
    pooled = table.iloc[-1]
    league_rows = versus.iloc[:-1]
    versus_pooled = versus.iloc[-1]
    n_leagues = len(leagues)
    seasons = f"{config.test_seasons[0]}–{config.test_seasons[-1]}"
    dropped = baseline.dropped
    corp = corp_tables["brier"].set_index("class")
    corp_log = corp_tables["log_loss"].set_index("class")

    lines: list[str] = []
    add = lines.append
    add("# Rama ewaluacji: wynik baseline na 1X2")
    add("")
    add(
        f"Wspólna rama ewaluacji jest gotowa i zmierzyła pierwszy punkt odniesienia: częstości wyników "
        f"w lidze. Na {thousands(pooled['n'])} meczach z {n_leagues} lig (sezony {seasons}) baseline ma "
        f"log-loss **{num(pooled['log_loss'])}** (przedział 95%: {num(pooled['log_loss_lo'])}–"
        f"{num(pooled['log_loss_hi'])}), Brier {num(pooled['brier'])} i RPS {num(pooled['rps'])}. "
        f"Każdy kolejny model (Dixon–Coles, NB2, GAS-Poisson, Elo–Davidson, pi-ratingi z boostingiem) "
        f"będzie oceniany tą samą procedurą i porównywany z tymi liczbami."
    )
    add("")
    add("## Jak mierzymy")
    add("")
    add(
        "- **Walk-forward po sezonach.** Sezony testowe to "
        f"{', '.join(str(s) for s in config.test_seasons)}. Model jest dopasowywany od nowa przed każdą "
        "kolejką (rundą; jeśli mecze rundy rozrzucone są na więcej niż "
        f"{num(config.max_round_span_days, 0)} dni, np. po przełożeniu meczu, dzielimy je na tygodnie kalendarzowe)."
    )
    add(
        "- **Tylko przeszłość.** Do prognozy kolejki trafiają wyłącznie mecze, które skończyły się przed "
        f"pierwszym gwizdkiem tej kolejki (kickoff co najmniej {num(config.result_latency_hours, 0)} godziny "
        "wcześniej, żeby wynik był znany). Wyniki samej kolejki są przed modelem ukryte."
    )
    add(
        "- **Baseline** to częstości 1X2 w danej lidze policzone na danych treningowych (z wygładzeniem "
        "Laplace'a, jeden pseudo-mecz na klasę). Dopasowanie wymaga co najmniej "
        f"{config.min_train_matches} meczów historii ligi."
    )
    add(
        "- **Metryki.** Log-loss jest główną (im niżej, tym lepiej), Brier i RPS pomocniczo (RPS uwzględnia, "
        "że wyniki są uporządkowane: gospodarz, remis, gość). Przedziały ufności to bootstrap po kolejkach "
        f"({thousands(n_resamples)} losowań, całe kolejki losowane ze zwracaniem), bo mecze jednej kolejki "
        "nie są niezależne."
    )
    add(
        f"- **Dane.** Tabela `matches` z API-Football (`{db_path.name}`): "
        f"{thousands(dropped['input'])} wierszy, z czego do oceny weszły tylko zakończone w regulaminowym "
        f"czasie (status FT). Odpadło {thousands(dropped['not_finished'])} meczów (odwołane, przełożone, "
        f"walkowery, dogrywki i karne) oraz {thousands(dropped['missing_outcome'])} bez wyniku."
    )
    add("")
    add("## Wynik zbiorczy")
    add("")
    add("| | Mecze | Log-loss [95% CI] | Brier [95% CI] | RPS [95% CI] |")
    add("|---|---:|---|---|---|")
    add(
        f"| Wszystkie ligi | {thousands(pooled['n'])} | {with_interval(pooled, 'log_loss')} | "
        f"{with_interval(pooled, 'brier')} | {with_interval(pooled, 'rps')} |"
    )
    add("")
    if pooled["n_missing"]:
        gaps = baseline.predictions[baseline.predictions[baseline.probability_columns].isna().any(axis=1)]
        gap_leagues = ", ".join(sorted(gaps["league_code"].unique()))
        add(
            f"Bez prognozy zostało {thousands(pooled['n_missing'])} meczów sezonów testowych: pierwsze "
            f"kolejki lig, których dane w archiwum zaczynają się dopiero w sezonie testowym ({gap_leagues}), "
            f"gdy historia liczy mniej niż {config.min_train_matches} meczów. Nie są liczone jako zero, "
            "po prostu ich nie oceniamy."
        )
        add("")
    add("Po sezonach (te same mecze, bez przedziałów):")
    add("")
    add("| Sezon | Mecze | Log-loss | Brier | RPS |")
    add("|---|---:|---:|---:|---:|")
    for _, row in by_season.iterrows():
        add(f"| {int(row['season'])} | {thousands(row['n'])} | {num(row['log_loss'])} | {num(row['brier'])} | {num(row['rps'])} |")
    add("")
    add(f"## Wynik w {n_leagues} ligach")
    add("")
    add("| Liga | Mecze | Log-loss [95% CI] | Brier [95% CI] | RPS [95% CI] |")
    add("|---|---:|---|---|---|")
    for _, row in leagues.iterrows():
        add(
            f"| {row['league_code']} | {thousands(row['n'])} | {with_interval(row, 'log_loss')} | "
            f"{with_interval(row, 'brier')} | {with_interval(row, 'rps')} |"
        )
    add("")
    ranked = leagues.dropna(subset=["log_loss"]).sort_values("log_loss")
    worst = ranked.iloc[-1]
    best = ranked.iloc[0]
    covering = int(((leagues["log_loss_lo"] <= pooled["log_loss"]) & (pooled["log_loss"] <= leagues["log_loss_hi"])).sum())
    add(
        f"Najniższy log-loss ma {best['league_code']} ({num(best['log_loss'])}), najwyższy {worst['league_code']} "
        f"({num(worst['log_loss'])}). W {covering} z {n_leagues} lig przedział ufności zawiera wartość zbiorczą, "
        "więc większość różnic między ligami mieści się w szumie. Do oceny modelu liczy się różnica wobec "
        "baseline'u w tej samej lidze, nie poziom bezwzględny."
    )
    add("")
    add("## Niezawodność (CORP)")
    add("")
    add(
        "Diagram niezawodności CORP (Dimitriadis, Gneiting, Jordan 2021) dopasowuje do prognoz regresję "
        "izotoniczną i rozkłada wynik na trzy części: S = MCB − DSC + UNC. MCB to miskalibracja (cena za "
        "prognozy, które nie mówią prawdy o częstości), DSC to zdolność rozróżniania meczów, UNC to "
        "nieunikniona niepewność samego wyniku. Liczone jednoklasowo (klasa kontra reszta) na wszystkich "
        f"prognozach razem; p(MCB) to test Monte Carlo ({n_sim} symulacji) hipotezy, że prognozy są "
        "wiarygodne."
    )
    add("")
    add("Brier:")
    add("")
    add("| Klasa | Częstość | Wynik | MCB | DSC | UNC | p(MCB) |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for name, row in corp.iterrows():
        add(
            f"| {CLASS_NAMES_PL.get(name, name)} | {num(row['base_rate'], 3)} | {num(row['score'], 5)} | "
            f"{num(row['mcb'], 5)} | {num(row['dsc'], 5)} | {num(row['unc'], 5)} | {num(row['mcb_pvalue'], 3)} |"
        )
    add("")
    add("Log-loss:")
    add("")
    add("| Klasa | Wynik | MCB | DSC | UNC | p(MCB) |")
    add("|---|---:|---:|---:|---:|---:|")
    for name, row in corp_log.iterrows():
        add(
            f"| {CLASS_NAMES_PL.get(name, name)} | {num(row['score'], 5)} | {num(row['mcb'], 5)} | "
            f"{num(row['dsc'], 5)} | {num(row['unc'], 5)} | {num(row['mcb_pvalue'], 3)} |"
        )
    add("")
    add("Krzywa CORP dla klasy „gospodarz” (prognoza i oszacowane prawdopodobieństwo warunkowe w punktach kwantylowych):")
    add("")
    add("| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |")
    add("|---:|---:|---:|")
    for _, row in corp_diagram.iterrows():
        add(f"| {num(row['forecast'], 3)} | {num(row['cep'], 3)} | {num(row['share_at_or_below'], 2)} |")
    add("")
    add(
        "Baseline zna tylko średnie ligi i nie odróżnia meczów między sobą, więc prawie cały wynik to UNC. "
        f"Największe MCB w tabeli Briera to {num(corp['mcb'].max(), 5)}, największe DSC {num(corp['dsc'].max(), 5)}. "
        "Model, który ma coś do powiedzenia o konkretnych meczach, musi mieć DSC wyraźnie wyższe, a MCB "
        "nadal bliskie zera."
    )
    n_detectable = int((corp["mcb_pvalue"] < 0.05).sum())
    if n_detectable:
        add("")
        add(
            f"Dla {n_detectable} z {len(corp)} klas w tabeli Briera p(MCB) jest poniżej 0,05, czyli nawet ten prosty "
            "baseline ma statystycznie wykrywalną miskalibrację. Jest ona bardzo mała: MCB "
            f"{num(corp['mcb'].max(), 5)} przy UNC od {num(corp['unc'].min(), 3)}. MCB kolejnych "
            "modeli będzie mierzone względem tej wartości."
        )
    add("")
    add("## Test istotności na prawdziwych danych")
    add("")
    n_raw = int((league_rows["p_value"] < 0.05).sum())
    n_holm = int((league_rows["p_holm"] < 0.05).sum())
    n_bh = int((league_rows["p_bh"] < 0.05).sum())
    n_tested = int(league_rows["p_value"].notna().sum())
    add(
        "Żeby sprawdzić działanie testów na prawdziwych danych, porównaliśmy baseline z jeszcze prostszym "
        "punktem odniesienia: jedne częstości 1X2 dla wszystkich lig naraz (pooled, odświeżane co tydzień "
        "kalendarzowy). Różnica strat to strata globalnych częstości minus strata częstości ligowych, więc "
        "dodatnia liczba oznacza, że ligowe są lepsze."
    )
    add("")
    significant = versus_pooled["p_value"] < 0.05
    add(
        f"- Wszystkie ligi razem: średnia różnica log-loss {num(versus_pooled['mean_diff'], 5)} "
        f"[{num(versus_pooled['ci_lo'], 5)}; {num(versus_pooled['ci_hi'], 5)}], statystyka DM "
        f"{num(versus_pooled['dm_stat'], 2)}, p = {num(versus_pooled['p_value'], 4)}: różnica "
        f"{'jest' if significant else 'nie jest'} istotna statystycznie na poziomie 5%."
    )
    add(
        f"- Liga po lidze (test Diebolda–Mariano z wariancją HAC po kolejkach, {n_tested} lig): p < 0,05 bez "
        f"korekty w {n_raw}, po korekcie Holma w {n_holm}, po korekcie Benjaminiego–Hochberga w {n_bh}."
    )
    add("")
    add("## Test przecieku")
    add("")
    if leakage is None:
        add("Pominięty w tym przebiegu (`--skip-leakage`).")
    else:
        details = "; ".join(
            f"{mode.mode}: zmieniono wyniki {thousands(mode.n_future_rows_changed)} przyszłych meczów, "
            f"zmienionych prognoz {mode.n_forecasts_changed}, maksymalna różnica {num(mode.max_abs_difference, 3)}"
            for mode in leakage.modes
        )
        verdict = "przeszedł" if leakage.passed else "**NIE PRZESZEDŁ**"
        add(
            f"Wyniki meczów od {leakage.cutoff:%Y-%m-%d} zostały przetasowane (i osobno odbite gospodarz–gość). "
            f"Prognozy {thousands(leakage.n_compared)} meczów z kolejek rozpoczętych do tej daty włącznie "
            f"porównano z oryginałem: test {verdict} ({details})."
        )
    add("")
    add("## Czas")
    add("")
    fitted = baseline.predictions[baseline.predictions["n_train"] >= config.min_train_matches]
    add(
        f"Baseline na {thousands(pooled['n'] + pooled['n_missing'])} meczach testowych "
        f"({thousands(baseline.predictions['matchday'].nunique())} kolejek, {thousands(fitted['matchday'].nunique())} "
        f"dopasowań): {num(baseline.elapsed_seconds, 1)} s. Punkt odniesienia z globalnymi częstościami: "
        f"{num(global_seconds, 1)} s. Cały przebieg skryptu razem z bootstrapem, CORP i testem przecieku: "
        f"{num(total_seconds, 1)} s."
    )
    add("")
    add("## Jak podłączyć kolejny model")
    add("")
    add(
        "Model to obiekt z metodami `fit(train)` i `predict(frame)`, który zwraca macierz prawdopodobieństw "
        "(wiersze sumują się do 1, kolumny w kolejności klas rynku: gospodarz, remis, gość albo poniżej, "
        "powyżej). Opakowuje się go w `ModelSpec` z fabryką i opcjonalną siatką hiperparametrów (np. półokres "
        "zaniku 180–1 200 dni), a wybór z siatki robi rama na foldach wewnętrznych sprzed sezonu testowego. "
        "Opis interfejsu jest w docstringu modułu `app/src/model_buk/evaluation/framework.py`; skrypt "
        "`app/scripts/run_eval_baseline.py` jest gotowym wzorem użycia. Test `check_no_lookahead` "
        "sprawdza każdy nowy model pod kątem podglądania przyszłości."
    )
    add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="history.sqlite built by build_history_tables.py (read-only)")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="Markdown report to write")
    parser.add_argument("--first-season", type=int, default=2021)
    parser.add_argument("--last-season", type=int, default=2025)
    parser.add_argument("--resamples", type=int, default=2000, help="Bootstrap resamples for confidence intervals")
    parser.add_argument("--corp-sims", type=int, default=200, help="Monte Carlo simulations for the CORP MCB p-value")
    parser.add_argument("--leakage-cutoff", default="2023-11-01", help="Outcomes from this date on are shuffled in the leakage test")
    parser.add_argument("--skip-leakage", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.perf_counter()
    matches = load_matches(args.db)
    print(f"Loaded {len(matches)} matches from {args.db} in {time.perf_counter() - started:.1f}s", flush=True)

    seasons = range(args.first_season, args.last_season + 1)
    config = WalkForwardConfig(test_seasons=seasons)
    pooled_config = WalkForwardConfig(test_seasons=seasons, scope="pooled")
    market = MARKET_1X2

    prepared = prepare_matches(matches, market, config)
    baseline_spec = league_frequency_spec(market)
    baseline = evaluate_model(baseline_spec, prepared, market, config)
    print(f"Baseline walk-forward: {len(baseline.predictions)} forecasts in {baseline.elapsed_seconds:.1f}s", flush=True)

    scores = baseline.scores()
    table = baseline.score_table(n_resamples=args.resamples, seed=args.seed)
    valid = scores.dropna(subset=["log_loss", "brier", "rps"])
    by_season = valid.groupby("season")[["log_loss", "brier", "rps"]].mean()
    by_season["n"] = valid.groupby("season").size()
    by_season = by_season.reset_index()

    corp_tables = {
        score: baseline.corp(score=score, n_sim=args.corp_sims, seed=args.seed)
        for score in ("brier", "log_loss")
    }
    scored = baseline.predictions.dropna(subset=baseline.probability_columns)
    home = corp_reliability(scored["p_home"].to_numpy(), (scored["y"].to_numpy() == 0).astype(float))
    corp_diagram = corp_diagram_table(home, n_points=10)

    global_started = time.perf_counter()
    global_baseline = evaluate_model(global_frequency_spec(market), prepared, market, pooled_config)
    global_seconds = time.perf_counter() - global_started
    versus = compare_to_baseline(baseline, global_baseline, n_resamples=args.resamples, seed=args.seed)

    leakage = None
    if not args.skip_leakage:
        leak_started = time.perf_counter()
        leakage = check_no_lookahead(
            lambda frame: evaluate_model(baseline_spec, frame, market, config),
            matches,
            market,
            args.leakage_cutoff,
            seed=args.seed,
        )
        print(f"Leakage test {'passed' if leakage.passed else 'FAILED'} in {time.perf_counter() - leak_started:.1f}s", flush=True)

    total_seconds = time.perf_counter() - started
    report = build_report(
        db_path=args.db,
        config=config,
        baseline=baseline,
        table=table,
        by_season=by_season,
        corp_tables=corp_tables,
        corp_diagram=corp_diagram,
        versus=versus,
        leakage=leakage,
        global_seconds=global_seconds,
        total_seconds=total_seconds,
        n_resamples=args.resamples,
        n_sim=args.corp_sims,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    pooled = table.iloc[-1]
    print(
        f"Pooled 1X2 baseline: n={int(pooled['n'])} log_loss={pooled['log_loss']:.4f} "
        f"brier={pooled['brier']:.4f} rps={pooled['rps']:.4f}  total {total_seconds:.1f}s"
    )
    print(f"Report: {args.out}")
    if leakage is not None and not leakage.passed:
        raise SystemExit("leakage test failed")


if __name__ == "__main__":
    main()
