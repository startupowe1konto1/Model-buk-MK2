"""Compare the 1X2 and goal models under the fixed protocol and select the best one without peeking.

Offline: reads the stored forecasts in ``predictions/<model>.sqlite`` (written by the model builders
through ``model_buk.evaluation.protocol.save_result``) and the API-Football ``matches`` table
(read-only, only for the league-frequency baseline).  Writes

- ``predictions/baseline_league_freq.sqlite``  the league-frequency baseline under ``COMPARISON_CONFIG``,
- ``predictions/selected_best.sqlite``         per market, the model with the best log-loss on the
                                               previous season (scored on the next one),
- ``predictions/pool_models.sqlite``           the log-linear pool with weights fitted on the previous season,
- ``predictions/model_comparison.sqlite``      every table of the comparison (scores, tests, CORP, selection),
- ``reports/Modele 1X2 i gole - pytanie 1.md`` the Polish report.

Stages (``all`` runs baseline, compare and report):

- ``baseline``  evaluate ``league_frequency_spec`` per market and store it as ``baseline_league_freq``
                (skipped when the file already exists unless ``--force``).
- ``compare``   panel of stored forecasts on identical matches, scores, paired tests, CORP, selection
                and pool, the no-look-ahead check of the selection, storing of all tables.
- ``calendar``  only the ``calendar_year_check`` table (strict selection sample, see
                ``model_selection.calendar_year_check``) added to an existing comparison file; ``compare`` writes it too.
- ``report``    the Polish report from the stored comparison tables.

Usage (from the app/ directory)::

    .venv\\Scripts\\python scripts\\compare_goal_models.py --stage all > runtime\\compare_goal_models.log
    .venv\\Scripts\\python scripts\\compare_goal_models.py --stage report
"""

from __future__ import annotations

import os

# Small fits and array work: extra BLAS threads only fight the other jobs for cores.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from contextlib import closing  # noqa: E402
from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation import model_selection as ms  # noqa: E402
from model_buk.evaluation.framework import Market, evaluate_model, league_frequency_spec, prepare_matches  # noqa: E402
from model_buk.evaluation.protocol import (  # noqa: E402
    COMPARISON_CONFIG,
    GOAL_MARKETS,
    PREDICTIONS_DIR,
    PROTOCOL_ID,
    load_history_matches,
    save_result,
)
from model_buk.evaluation.significance import benjamini_hochberg_adjust, holm_adjust  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = APP_DIR.parent
DEFAULT_SUMMARY = APP_DIR / "runtime" / "compare_goal_models_summary.json"
DEFAULT_REPORT = REPO_DIR / "reports" / "Modele 1X2 i gole - pytanie 1.md"
BASELINE_NAME = ms.BASELINE_NAME
COMPARISON_DB = "model_comparison.sqlite"
MARKET_ORDER = ("1x2", "over_under_2.5", "over_under_1.5", "over_under_3.5", "btts")
DISPLAY_ORDER = ("1x2", "over_under_1.5", "over_under_2.5", "over_under_3.5", "btts")


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def markets_in_order() -> list[Market]:
    known = {market.name: market for market in GOAL_MARKETS}
    return [known[name] for name in MARKET_ORDER]


# ---------------------------------------------------------------------------
# baseline: league frequencies under the fixed protocol, stored like a model
# ---------------------------------------------------------------------------


def stage_baseline(args: argparse.Namespace) -> dict:
    """Evaluate the league-frequency baseline for every goal market and store it as ``baseline_league_freq``."""
    target = Path(args.predictions_dir) / f"{BASELINE_NAME}.sqlite"
    if target.exists() and not args.force:
        say(f"baseline: {target.name} exists, skipped (use --force to recompute)")
        return {"skipped": True}
    matches = load_history_matches()
    say(f"baseline: {len(matches)} matches of {matches['league_code'].nunique()} leagues loaded")
    timings: dict[str, float] = {}
    for market in markets_in_order():
        started = time.perf_counter()
        prepared = prepare_matches(matches, market, COMPARISON_CONFIG)
        result = evaluate_model(league_frequency_spec(market), prepared, market, COMPARISON_CONFIG)
        result = replace(result, model=BASELINE_NAME)
        save_result(result, args.predictions_dir)
        timings[market.name] = time.perf_counter() - started
        scored = result.predictions.dropna(subset=result.probability_columns)
        say(f"baseline {market.name:15s} {len(result.predictions)} rows, {len(scored)} forecast, {timings[market.name]:.0f}s")
    return {"skipped": False, "seconds": timings}


# ---------------------------------------------------------------------------
# compare: tables, selection, pools, look-ahead check
# ---------------------------------------------------------------------------


def tag(frame: pd.DataFrame, market: str, sample: str) -> pd.DataFrame:
    frame = frame.copy()
    frame.insert(0, "sample", sample)
    frame.insert(0, "market", market)
    return frame


def store_tables(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Write every table into a fresh SQLite file (a stale table from an older run must not survive)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as connection:
        for name, frame in tables.items():
            frame.to_sql(name, connection, if_exists="replace", index=False)
        connection.commit()


def read_tables(path: Path) -> dict[str, pd.DataFrame]:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {name: pd.read_sql_query(f'SELECT * FROM "{name}"', connection) for name in names}


def family_adjust(frame: pd.DataFrame) -> pd.DataFrame:
    """Holm, Benjamini-Hochberg and a Bonferroni interval over all pooled model-versus-baseline tests of one sample."""
    pooled = frame[frame["league_code"] == ms.POOLED_LABEL].copy()
    pooled["p_holm_family"] = holm_adjust(pooled["p_value"].to_numpy())
    pooled["p_bh_family"] = benjamini_hochberg_adjust(pooled["p_value"].to_numpy())
    size = int(pooled["p_value"].notna().sum())
    pooled["family_size"] = size
    pooled["ci_fam_lo"], pooled["ci_fam_hi"] = ms.bonferroni_interval(
        pooled["mean_diff"].to_numpy(dtype=float),
        pooled["std_error"].to_numpy(dtype=float),
        pooled["dm_clusters"].to_numpy(dtype=float),
        size,
    )
    return pooled


def stage_calendar(args: argparse.Namespace) -> pd.DataFrame:
    """Add or refresh only the ``calendar_year_check`` table of an existing comparison file (no other table is touched)."""
    path = Path(args.predictions_dir) / COMPARISON_DB
    if not path.exists():
        raise SystemExit(f"{path} does not exist; run --stage compare first")
    frames = []
    for market in markets_in_order():
        panel, _ = ms.load_panel(market, [BASELINE_NAME, *ms.FIXED_MODELS], Path(args.predictions_dir))
        candidates = [BASELINE_NAME, *[m for m in ms.FIXED_MODELS if m in panel.models]]
        seasons = sorted(int(s) for s in np.unique(panel.season))
        frames.append(tag(ms.calendar_year_check(panel, candidates, seasons[1:]), market.name, "selection"))
    table = pd.concat(frames, ignore_index=True)
    with closing(sqlite3.connect(path)) as connection:
        table.to_sql("calendar_year_check", connection, if_exists="replace", index=False)
        connection.commit()
    say(f"calendar_year_check: {len(table)} rows written to {path.name}")
    return table


def stage_compare(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    root = Path(args.predictions_dir)
    collected: dict[str, list[pd.DataFrame]] = {}

    def add(name: str, frame: pd.DataFrame) -> None:
        collected.setdefault(name, []).append(frame)

    summary: dict = {
        "protocol_id": PROTOCOL_ID,
        "markets": {},
        "n_resamples": args.resamples,
        "corp_sims": args.corp_sims,
        "seed": args.seed,
    }
    for market in markets_in_order():
        market_started = time.perf_counter()
        panel, skipped = ms.load_panel(market, [BASELINE_NAME, *ms.FIXED_MODELS], root)
        if BASELINE_NAME not in panel.models:
            raise SystemExit(f"{BASELINE_NAME}.sqlite has no {market.name} table; run --stage baseline first")
        models = [m for m in ms.FIXED_MODELS if m in panel.models]
        say(f"{market.name}: {len(panel)} fixtures, models {models}, skipped {skipped}")

        # (A) all fixed models and the baseline on their common matches, all test seasons
        first = ms.compare_models(panel, models, n_resamples=args.resamples, seed=args.seed, corp_sims=args.corp_sims)
        seasons = sorted(int(s) for s in np.unique(panel.season))
        sample_a = f"{seasons[0]}-{seasons[-1]}"
        for key, frame in first.items():
            add(key, tag(frame, market.name, sample_a))
        say(f"  compare {sample_a} done ({time.perf_counter() - market_started:.0f}s)")

        # (B) ex-ante selection and pools from the previous season's forecasts
        scored = tuple(seasons[1:]) + (seasons[-1] + 1,)  # the last one has no matches: only the coming choice is recorded
        sample_b = f"{scored[0]}-{scored[-2]}"
        candidates = [BASELINE_NAME, *models]
        run = ms.run_selection(panel, candidates, scored)
        with_derived = panel
        for name, probs in run.forecasts().items():
            with_derived = with_derived.with_model(name, probs)
        second = ms.compare_models(
            with_derived,
            [*models, *run.forecasts()],
            best_pool=models,
            seasons=scored[:-1],
            n_resamples=args.resamples,
            seed=args.seed,
            corp_sims=args.corp_sims,
        )
        for key, frame in second.items():
            add(key, tag(frame, market.name, sample_b))
        add("selection_choices", tag(run.choices, market.name, "selection"))
        add("pool_weights", tag(run.weights, market.name, "selection"))
        add("league_choices", tag(run.league_choices, market.name, "selection"))
        say(f"  selection: {dict(zip(run.choices['scored_season'], run.choices['chosen']))}")

        # the two derived forecasts in the protocol's storage format
        elapsed = time.perf_counter() - market_started
        selections, inner = ms.selection_tables(run, "selected")
        save_result(
            ms.derived_result(
                panel, run.selected, model=ms.SELECTED_NAME, params=ms.selected_params(panel, run),
                selections=selections, inner_scores=inner, elapsed_seconds=elapsed,
            ),
            root,
        )
        selections, inner = ms.selection_tables(run, "log_linear")
        save_result(
            ms.derived_result(
                panel, run.pool, model=ms.POOL_NAME, params=ms.pool_params(panel, run, "log_linear"),
                selections=selections, inner_scores=inner, elapsed_seconds=elapsed,
            ),
            root,
        )

        # (C) look-ahead check of the selection and pools on the real forecasts
        report = ms.assert_selection_no_lookahead(panel, candidates, scored[:-1])
        add(
            "selection_leakage",
            pd.DataFrame(
                [
                    {
                        "market": market.name,
                        "disturbed_from_season": r.disturbed_from,
                        "n_compared": r.n_compared,
                        "n_changed": r.n_changed,
                        "n_later_changed": r.n_later_changed,
                        "max_abs_difference": r.max_abs_difference,
                    }
                    for r in report.seasons
                ]
            ),
        )
        add("calendar_year_check", tag(ms.calendar_year_check(panel, candidates, scored[:-1]), market.name, "selection"))
        # (D) the same check with a deliberately broken rule (selection on the scored season) must fail
        original_rule = ms.selection_season
        ms.selection_season = lambda season: season
        try:
            broken = ms.check_selection_no_lookahead(panel, candidates, scored[:-1])
        finally:
            ms.selection_season = original_rule
        if broken.passed:
            raise SystemExit(f"{market.name}: the look-ahead check did not catch a selection on the scored season")
        add(
            "selection_leakage_mutation",
            pd.DataFrame(
                [
                    {
                        "market": market.name,
                        "disturbed_from_season": r.disturbed_from,
                        "n_compared": r.n_compared,
                        "n_changed": r.n_changed,
                    }
                    for r in broken.seasons
                ]
            ),
        )
        summary["markets"][market.name] = {
            "models": models,
            "skipped": skipped,
            "scored_seasons": list(scored[:-1]),
            "seconds": time.perf_counter() - market_started,
            "leakage_passed": report.passed,
        }
        say(f"  look-ahead check passed={report.passed}; market done in {time.perf_counter() - market_started:.0f}s")

    tables = {name: pd.concat(parts, ignore_index=True) for name, parts in collected.items()}
    tables["pooled_tests"] = pd.concat(
        [family_adjust(group) for _, group in tables["versus_reference"].groupby("sample")], ignore_index=True
    )
    path = root / COMPARISON_DB
    store_tables(path, tables)
    summary["seconds"] = time.perf_counter() - started
    summary["file"] = str(path)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    say(f"compare done in {summary['seconds']:.0f}s; tables in {path}")
    return summary


# ---------------------------------------------------------------------------
# report: plain Polish, every number from the stored comparison tables
# ---------------------------------------------------------------------------

MARKET_PL = {
    "1x2": "1X2 (wynik meczu)",
    "over_under_2.5": "Gole powyżej 2,5",
    "over_under_1.5": "Gole powyżej 1,5",
    "over_under_3.5": "Gole powyżej 3,5",
    "btts": "Obie drużyny strzelą (BTTS)",
}
MARKET_SHORT = {"1x2": "1X2", "over_under_2.5": "Gole 2,5", "over_under_1.5": "Gole 1,5", "over_under_3.5": "Gole 3,5", "btts": "BTTS"}
CLASS_PL = {"home": "gospodarz", "draw": "remis", "away": "gość", "over": "powyżej", "yes": "tak"}
MODEL_PL = {
    "baseline_league_freq": "częstości wyników w lidze, liczone na danych treningowych (punkt odniesienia)",
    "dixon_coles": "Dixon–Coles: siły ataku i obrony drużyn z ważeniem czasu, półokres z folda wewnętrznego",
    "nb2": "jak Dixon–Coles, ale z rozkładem ujemnym dwumianowym",
    "gas_poisson": "Poisson z siłami drużyn, które zmieniają się po każdym meczu (GAS)",
    "elo_davidson": "rating Elo z rozkładem Davidsona (tylko 1X2)",
    "pi_ratings": "pi-ratingi i poziom ligi w regresji logistycznej",
    "pi_gbm": "pi-ratingi, forma (gole, strzały, rzuty rożne) i poziom ligi w gradient boostingu",
    "selected_best": "model z najlepszym log-lossem na poprzednim sezonie",
    "selected_best_league": "jak wyżej, ale osobno dla każdej ligi, ściągnięte do wyboru zbiorczego (kontrola)",
    "pool_models": "pula liniowo-logarytmiczna wszystkich modeli i baseline'u, wagi z poprzedniego sezonu",
    "pool_models_linear": "pula liniowa (zwykła średnia ważona), wagi z poprzedniego sezonu (kontrola)",
}
FIXED_ORDER = ("dixon_coles", "nb2", "gas_poisson", "elo_davidson", "pi_ratings", "pi_gbm")
DERIVED_ORDER = ("selected_best", "pool_models", "selected_best_league", "pool_models_linear")


def num(value, digits: int = 4) -> str:
    """Polish decimal comma; NaN shows as a dash (a missing value is never a zero)."""
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def signed(value, digits: int = 4) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:+.{digits}f}".replace(".", ",")


def pct(value, digits: int = 1) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.{digits}f}".replace(".", ",") + "%"


def thousands(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def pval(value) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    if value < 1e-4:
        return "< 0,0001"
    return num(value, 4)


def p_text(value) -> str:
    """``p < 0,0001`` or ``p = 0,0408``; a missing p-value shows a dash."""
    if value is None or not np.isfinite(value):
        return "p —"
    return "p < 0,0001" if value < 1e-4 else f"p = {num(value, 4)}"


def dash(label: str) -> str:
    """Season ranges are stored with a hyphen; show them with an en dash."""
    return label.replace("-", "–")


def ci(lo, hi, digits: int = 4, plus: bool = False) -> str:
    fmt = signed if plus else num
    return f"[{fmt(lo, digits)}; {fmt(hi, digits)}]"


class Comparison:
    """Read access to the stored comparison tables."""

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self.t = tables
        samples = sorted(s for s in tables["ranking"]["sample"].unique() if s != "selection")
        self.a, self.b = samples[0], samples[-1]
        self.markets = [m for m in DISPLAY_ORDER if m in set(tables["ranking"]["market"])]

    def _pick(self, name: str, market: str, sample: str) -> pd.DataFrame:
        frame = self.t[name]
        return frame[(frame["market"] == market) & (frame["sample"] == sample)]

    def models(self, market: str, sample: str) -> list[str]:
        return list(self._pick("ranking", market, sample)["model"])

    def fixed(self, market: str) -> list[str]:
        present = set(self.models(market, self.a))
        return [m for m in FIXED_ORDER if m in present]

    def ranking(self, market: str, sample: str) -> pd.Series:
        frame = self._pick("ranking", market, sample)
        return frame.set_index("model")["log_loss"]

    def pooled(self, market: str, sample: str) -> pd.DataFrame:
        frame = self._pick("scores", market, sample)
        return frame[frame["league_code"] == ms.POOLED_LABEL].set_index("model")

    def leagues(self, market: str, sample: str) -> pd.DataFrame:
        frame = self._pick("scores", market, sample)
        return frame[frame["league_code"] != ms.POOLED_LABEL]

    def versus(self, market: str, sample: str, model: str, kind: str = "versus_reference") -> pd.DataFrame:
        frame = self._pick(kind, market, sample)
        return frame[frame["model"] == model]

    def pooled_test(self, market: str, sample: str, model: str) -> pd.Series | None:
        frame = self._pick("pooled_tests", market, sample)
        row = frame[frame["model"] == model]
        return None if row.empty else row.iloc[0]

    def best_fixed(self, market: str, sample: str) -> str:
        ranking = self.ranking(market, sample)
        return str(ranking[[m for m in ranking.index if m in FIXED_ORDER]].idxmin())

    def by_season(self, market: str, sample: str) -> pd.DataFrame:
        return self._pick("by_season", market, sample)

    def corp(self, market: str, sample: str) -> pd.DataFrame:
        return self._pick("corp", market, sample)

    def gain(self, market: str, sample: str, model: str) -> float:
        ranking = self.ranking(market, sample)
        return float(ranking[BASELINE_NAME] - ranking[model])


VERDICT_ADVERB = {"wyraźnie lepszy": "Tak", "lekko lepszy": "Skromnie", "minimalnie lepszy": "Minimalnie", "brak przewagi": "Nie"}


def verdict(gain: float, baseline: float, p_family: float, better_holm: int, n_leagues: int) -> str:
    """Words for the market table: clearly, slightly or minimally better than the baseline, or no visible edge.

    Better means a pooled gain above zero that stays significant after Holm over all model-market pairs.
    Clearly better needs the edge in at least half of the leagues after Holm; below 0.5% of the baseline's
    log-loss it is called minimal.
    """
    if not np.isfinite(p_family) or p_family >= 0.05 or gain <= 0:
        return "brak przewagi"
    if gain / baseline < 0.005:
        return "minimalnie lepszy"
    return "wyraźnie lepszy" if better_holm >= 0.5 * n_leagues else "lekko lepszy"


def market_table(cmp: Comparison, market: str) -> list[str]:
    a = cmp.a
    pooled = cmp.pooled(market, a)
    ranking = cmp.ranking(market, a)
    lines = [
        "| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |",
        "|---|---|---:|---:|---|---|---:|---|---:|",
    ]
    for model in [*[m for m in ranking.index if m != BASELINE_NAME]]:
        row = pooled.loc[model]
        test = cmp.pooled_test(market, a, model)
        versus = cmp.versus(market, a, model)
        counts = ms.significance_counts(versus)
        lines.append(
            f"| `{model}` | {num(row['log_loss'])} {ci(row['log_loss_lo'], row['log_loss_hi'])} | {num(row['brier'])} | {num(row['rps'])} | "
            f"{signed(test['mean_diff'])} {ci(test['ci_lo'], test['ci_hi'], plus=True)} | {ci(test['ci_fam_lo'], test['ci_fam_hi'], plus=True)} | "
            f"{pval(test['p_holm_family'])} | {counts['better_raw']} / {counts['better_holm']} / {counts['better_bh']} z {counts['n_tested']} | {counts['worse_holm']} |"
        )
    base = pooled.loc[BASELINE_NAME]
    lines.append(
        f"| `{BASELINE_NAME}` | {num(base['log_loss'])} {ci(base['log_loss_lo'], base['log_loss_hi'])} | {num(base['brier'])} | {num(base['rps'])} | — | — | — | — | — |"
    )
    return lines


def selection_tables(cmp: Comparison, market: str) -> list[str]:
    b = cmp.b
    choices = cmp.t["selection_choices"]
    choices = choices[choices["market"] == market]
    seasons = cmp.by_season(market, b)
    fixed = cmp.fixed(market)
    lines = [
        "| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |",
        "|---|---|---|---|---:|",
    ]
    for _, row in choices.iterrows():
        season = int(row["scored_season"])
        if int(row["n_scored"]) == 0:
            lines.append(
                f"| {season} (następny) | `{row['chosen']}` | {num(row['margin'], 4)} ± {num(row['margin_se'], 4)} (`{row['runner_up']}`) | — | — |"
            )
            continue
        here = seasons[(seasons["season"] == season) & seasons["model"].isin(fixed)].set_index("model")["log_loss"]
        best = str(here.idxmin())
        chosen = str(row["chosen"])
        loss = num(here[chosen] - here[best], 4) if chosen in here.index else "—"
        lines.append(
            f"| {season} | `{chosen}` | {num(row['margin'], 4)} ± {num(row['margin_se'], 4)} (`{row['runner_up']}`) | `{best}` | {loss} |"
        )
    return lines


def derived_table(cmp: Comparison, market: str) -> list[str]:
    b = cmp.b
    pooled = cmp.pooled(market, b)
    ranking = cmp.ranking(market, b)
    best = cmp.best_fixed(market, b)
    names = [BASELINE_NAME, best, *[m for m in DERIVED_ORDER if m in ranking.index]]
    lines = [
        f"| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `{best}` (najlepszy po fakcie) [95% CI] | p (DM) |",
        "|---|---|---|---|---|---:|",
    ]
    for model in names:
        row = pooled.loc[model]
        if model == BASELINE_NAME:
            lines.append(f"| `{model}` | {num(row['log_loss'])} {ci(row['log_loss_lo'], row['log_loss_hi'])} | — | — | — | — |")
            continue
        versus = cmp.versus(market, b, model)
        head = versus[versus["league_code"] == ms.POOLED_LABEL].iloc[0]
        counts = ms.significance_counts(versus)
        if model == best:
            other = "—"
            p = "—"
        else:
            vb = cmp.versus(market, b, model, "versus_best")
            vb = vb[vb["league_code"] == ms.POOLED_LABEL]
            if vb.empty or not np.isfinite(vb.iloc[0]["p_value"]):
                other = "identyczne prognozy" if not vb.empty and abs(vb.iloc[0]["mean_diff"]) < 1e-12 else "—"
                p = "—"
            else:
                other = f"{signed(vb.iloc[0]['mean_diff'])} {ci(vb.iloc[0]['ci_lo'], vb.iloc[0]['ci_hi'], plus=True)}"
                p = pval(vb.iloc[0]["p_value"])
        lines.append(
            f"| `{model}` | {num(row['log_loss'])} {ci(row['log_loss_lo'], row['log_loss_hi'])} | "
            f"{signed(head['mean_diff'])} {ci(head['ci_lo'], head['ci_hi'], plus=True)} | {counts['better_holm']} z {counts['n_leagues']} | {other} | {p} |"
        )
    return lines


def weight_table(cmp: Comparison, market: str) -> list[str]:
    weights = cmp.t["pool_weights"]
    weights = weights[(weights["market"] == market) & (weights["kind"] == "log_linear")]
    seasons = sorted(weights["scored_season"].unique())
    models = list(dict.fromkeys(weights["model"]))
    lines = ["| Model | " + " | ".join(f"{s}" + (" (następny)" if s == max(seasons) else "") for s in seasons) + " |", "|---|" + "---:|" * len(seasons)]
    for model in models:
        cells = []
        for season in seasons:
            value = weights[(weights["scored_season"] == season) & (weights["model"] == model)]["weight"]
            cells.append(num(float(value.iloc[0]), 2) if len(value) else "—")
        if any(c not in ("0,00", "—") for c in cells):
            lines.append(f"| `{model}` | " + " | ".join(cells) + " |")
    return lines


def calibration_table(cmp: Comparison, market: str) -> list[str]:
    b = cmp.b
    corp = cmp.corp(market, b)
    ranking = cmp.ranking(market, b)
    best = cmp.best_fixed(market, b)
    names = [BASELINE_NAME, *[m for m in FIXED_ORDER if m in ranking.index], *[m for m in ("selected_best", "pool_models") if m in ranking.index]]
    lines = [
        "| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in names:
        rows = corp[corp["model"] == model]
        if rows.empty:
            continue
        gap = (rows["mean_p"] - rows["base_rate"]) * 100.0
        worst = gap.iloc[int(np.argmax(np.abs(gap.to_numpy())))]
        label = f"{signed(worst, 1)}" if len(rows) > 1 else f"{signed(worst, 1)} ({num(rows['mean_p'].iloc[0] * 100, 1)}% wobec {num(rows['base_rate'].iloc[0] * 100, 1)}%)"
        mark = " (najlepszy pojedynczy)" if model == best else ""
        lines.append(
            f"| `{model}`{mark} | {label} | {num(rows['mcb'].sum() * 1000, 2)} | {num(rows['dsc'].sum() * 1000, 2)} | {num(rows['mcb_pvalue'].min(), 3)} |"
        )
    return lines


def season_gains(cmp: Comparison) -> pd.DataFrame:
    """Gain over the baseline per market, model and season (baseline log-loss minus model log-loss, common sample)."""
    rows = []
    for market in cmp.markets:
        for sample in (cmp.a, cmp.b):
            table = cmp.by_season(market, sample).set_index(["model", "season"])["log_loss"]
            for (model, season), loss in table.items():
                if model == BASELINE_NAME or (sample == cmp.b and model in cmp.fixed(market)):
                    continue
                rows.append({"market": market, "model": model, "season": int(season), "gain": float(table[(BASELINE_NAME, season)] - loss)})
    return pd.DataFrame(rows)


def season_gain_table(cmp: Comparison, gains: pd.DataFrame) -> list[str]:
    seasons = sorted(gains["season"].unique())
    lines = ["| Rynek | Model | " + " | ".join(str(s) for s in seasons) + " |", "|---|---|" + "---:|" * len(seasons)]
    for market in cmp.markets:
        for model in [*cmp.fixed(market), "selected_best", "pool_models"]:
            here = gains[(gains["market"] == market) & (gains["model"] == model)].set_index("season")["gain"]
            cells = [signed(here[s]) if s in here.index else "—" for s in seasons]
            lines.append(f"| {MARKET_SHORT[market]} | `{model}` | " + " | ".join(cells) + " |")
    return lines


def season_remarks(cmp: Comparison, gains: pd.DataFrame) -> list[str]:
    """Data-driven remarks under the season table: sign stability and the trend of the 1X2 gain."""
    remarks = []
    cells = gains[gains["model"].isin(FIXED_ORDER)]
    best_models = {m: cmp.best_fixed(m, cmp.a) for m in cmp.markets}
    top = cells[[best_models[m] == mod for m, mod in zip(cells["market"], cells["model"])]]
    remarks.append(
        f"Najlepszy pojedynczy model każdego rynku ma dodatni zysk w {int((top['gain'] > 0).sum())} z {len(top)} par rynek–sezon."
    )
    goal_only = cells[cells["model"].isin(["dixon_coles", "nb2", "gas_poisson"])]
    negative = goal_only[goal_only["gain"] < 0]
    if len(negative):
        listing = ", ".join(f"{MARKET_SHORT[r.market]} {r.season} (`{r.model}`)" for r in negative.itertuples())
        remarks.append(f"Modele oparte tylko na bramkach mają ujemny zysk w {len(negative)} z {len(goal_only)} par rynek–model–sezon: {listing}.")
    else:
        remarks.append("Modele oparte tylko na bramkach mają dodatni zysk w każdym rynku i sezonie.")
    one = cells[cells["market"] == "1x2"]
    first, last = int(one["season"].min()), int(one["season"].max())
    by_model = one.pivot(index="model", columns="season", values="gain")
    if (by_model[last] < by_model[first]).all():
        best = best_models["1x2"]
        remarks.append(
            f"W 1X2 zysk każdego modelu jest w {last} niższy niż w {first} (`{best}`: {signed(by_model.loc[best, first])} w {first}, {signed(by_model.loc[best, last])} w {last}). "
            "Przyczyny nie badaliśmy (może to dryf poziomu ligi albo zmiana samych sezonów)."
        )
    return remarks


def league_tables(cmp: Comparison) -> list[str]:
    """Gain over the baseline per league: 1X2 for every model, the other markets for the pool and pi_gbm."""
    b = cmp.b

    def cell(market: str, model: str, league: str) -> str:
        versus = cmp.versus(market, b, model)
        row = versus[versus["league_code"] == league]
        if row.empty or not np.isfinite(row.iloc[0]["mean_diff"]):
            return "—"
        row = row.iloc[0]
        mark = "*" if row["p_holm"] < 0.05 and row["mean_diff"] > 0 else "†" if row["p_holm"] < 0.05 and row["mean_diff"] < 0 else ""
        return f"{signed(row['mean_diff'], 3)}{mark}"

    lines: list[str] = []
    market = "1x2"
    models = [*cmp.fixed(market), "pool_models"]
    leagues = sorted(cmp.leagues(market, b)["league_code"].unique())
    n_by = cmp.leagues(market, b).drop_duplicates("league_code").set_index("league_code")["n"]
    lines += [
        "### 1X2",
        "",
        "| Liga | Mecze | " + " | ".join(f"`{m}`" for m in models) + " |",
        "|---|---:|" + "---:|" * len(models),
    ]
    for league in leagues:
        lines.append(f"| {league} | {thousands(n_by[league])} | " + " | ".join(cell(market, m, league) for m in models) + " |")
    lines += ["", "### Gole i BTTS", "", "Zysk puli `pool_models` względem baseline'u; w nawiasie `pi_gbm` (najlepszy pojedynczy model)."]
    others = [m for m in cmp.markets if m != "1x2"]
    lines += ["", "| Liga | Mecze | " + " | ".join(MARKET_SHORT[m] for m in others) + " |", "|---|---:|" + "---:|" * len(others)]
    for league in leagues:
        cells = [f"{cell(m, 'pool_models', league)} ({cell(m, 'pi_gbm', league)})" for m in others]
        lines.append(f"| {league} | {thousands(n_by[league])} | " + " | ".join(cells) + " |")
    return lines


def build_report(cmp: Comparison, summary: dict, args: argparse.Namespace) -> str:
    a, b = cmp.a, cmp.b
    lines: list[str] = []
    add = lines.append
    market0 = cmp.markets[0]
    coverage = cmp.t["coverage"]
    cov_a = coverage[(coverage["market"] == market0) & (coverage["sample"] == a)]
    n_common = int(cov_a["n_common"].iloc[0])
    n_fixtures = int(cov_a["n_fixtures"].iloc[0])
    n_leagues = int(cmp.leagues(market0, a)["league_code"].nunique())
    fixed_all = cmp.fixed(market0)

    # ---- headline ----------------------------------------------------------------------------
    facts = {}
    for market in cmp.markets:
        best = cmp.best_fixed(market, a)
        test = cmp.pooled_test(market, a, best)
        counts = ms.significance_counts(cmp.versus(market, a, best))
        pool_test = cmp.pooled_test(market, b, "pool_models")
        facts[market] = {
            "best": best,
            "ll": float(cmp.ranking(market, a)[best]),
            "base": float(cmp.ranking(market, a)[BASELINE_NAME]),
            "gain": float(test["mean_diff"]),
            "ci_fam": (float(test["ci_fam_lo"]), float(test["ci_fam_hi"])),
            "p": float(test["p_holm_family"]),
            "counts": counts,
            "verdict": verdict(float(test["mean_diff"]), float(cmp.ranking(market, a)[BASELINE_NAME]), float(test["p_holm_family"]), counts["better_holm"], counts["n_tested"]),
            "pool_gain": float(cmp.gain(market, b, "pool_models")),
            "pool_ll": float(cmp.ranking(market, b)["pool_models"]),
            "base_b": float(cmp.ranking(market, b)[BASELINE_NAME]),
            "pool_p": float(pool_test["p_holm_family"]) if pool_test is not None else float("nan"),
            "pool_vs_best": float(cmp.ranking(market, b)[cmp.best_fixed(market, b)] - cmp.ranking(market, b)["pool_models"]),
        }
    one = facts["1x2"]
    add("# Modele 1X2 i gole: który jest najlepszy i czy bije częstość ligową? (pytanie 1)")
    add("")
    only_1x2 = sorted(set(fixed_all) - {m for market in cmp.markets if market != "1x2" for m in cmp.fixed(market)})
    n_models_txt = f"{len(fixed_all)} modeli" + (f" (w rynkach goli bez {', '.join(f'`{m}`' for m in only_1x2)}, który ma tylko 1X2)" if only_1x2 else "")
    groups: dict[str, list[str]] = {}
    for market in cmp.markets:
        groups.setdefault(facts[market]["verdict"], []).append(market)
    phrases = []
    for label in ("wyraźnie lepszy", "lekko lepszy", "minimalnie lepszy", "brak przewagi"):
        members = groups.get(label)
        if not members:
            continue
        shares = [facts[m]["gain"] / facts[m]["base"] for m in members]
        size = pct(shares[0]) if len(shares) == 1 else f"{pct(min(shares))}–{pct(max(shares))}"
        phrases.append(f"{VERDICT_ADVERB[label]}: {', '.join(MARKET_SHORT[m] for m in members)} (zysk {size} log-lossu baseline'u)")
    add(f"**Odpowiedź na pytanie 1. {'. '.join(phrases)}.**")
    add("")
    add(
        f"Porównaliśmy {n_models_txt} z baseline'em (częstości wyników w lidze) tą samą procedurą na {thousands(n_common)} meczach "
        f"z {n_leagues} lig, sezony {dash(a)}. W 1X2 najlepszy pojedynczy model (`{one['best']}`) ma log-loss {num(one['ll'])} wobec "
        f"{num(one['base'])} u baseline'u, czyli {num(one['gain'])} lepiej. Po korekcie Holma jest istotnie lepszy "
        f"w {one['counts']['better_holm']} z {one['counts']['n_tested']} lig. W rynkach goli przewaga jest o rząd wielkości mniejsza, "
        f"a modele oparte tylko na bramkach mają w BTTS zysk bliski zera. To jest filtr, nie dowód: o sensie modelu rozstrzyga pytanie 2 "
        f"(model kontra kursy zamknięcia Pinnacle), a o pieniądzach pytanie 3."
    )
    add("")
    add("## Odpowiedź w skrócie")
    add("")
    add(
        f"Zysk to log-loss baseline'u minus log-loss modelu, więc dodatnia liczba oznacza przewagę modelu. Przedział „po korekcie” to przedział "
        f"Bonferroniego dla całej rodziny testów (wszystkie pary model–rynek względem baseline'u, {int(cmp.pooled_test(market0, a, fixed_all[0])['family_size'])} testów), "
        f"więc obowiązuje łącznie, nie dla pojedynczego porównania."
    )
    add("")
    add(f"| Rynek | Werdykt | Najlepszy pojedynczy model ({dash(a)}) | Log-loss modelu / baseline'u | Zysk [CI po korekcie] | Ligi istotnie lepsze po Holmie | Pula `pool_models` ({dash(b)}): zysk vs baseline / vs najlepszy pojedynczy |")
    add("|---|---|---|---|---|---|---|")
    for market in cmp.markets:
        f = facts[market]
        add(
            f"| {MARKET_PL[market]} | {f['verdict']} | `{f['best']}` | {num(f['ll'])} / {num(f['base'])} | "
            f"{signed(f['gain'])} {ci(f['ci_fam'][0], f['ci_fam'][1], plus=True)} ({pct(f['gain'] / f['base'])}) | "
            f"{f['counts']['better_holm']} z {f['counts']['n_tested']} | {signed(f['pool_gain'])} ({pct(f['pool_gain'] / f['base_b'])}) / {signed(f['pool_vs_best'])} |"
        )
    add("")
    geometric_base = float(np.exp(-one["base"]))
    geometric_model = float(np.exp(-one["ll"]))
    add(
        f"Jak daleko od baseline'u? W 1X2 średnie (geometryczne) prawdopodobieństwo, które model przypisał temu, co się naprawdę stało, "
        f"to {pct(geometric_model)} wobec {pct(geometric_base)} u baseline'u. To realna, ale niewielka przewaga: częstości ligowe już same "
        f"dają log-loss {num(one['base'])}, a idealna wiedza o wyniku dałaby 0. W rynkach goli i BTTS zysk to "
        f"{pct(min(facts[m]['gain'] / facts[m]['base'] for m in cmp.markets if m != '1x2'))}–{pct(max(facts[m]['gain'] / facts[m]['base'] for m in cmp.markets if m != '1x2'))} "
        f"log-lossu baseline'u."
    )
    add("")
    for market in cmp.markets:
        f = facts[market]
        holm = {m: ms.significance_counts(cmp.versus(market, a, m))["better_holm"] for m in cmp.fixed(market)}
        ordered = ", ".join(f"`{m}` {n}" for m, n in sorted(holm.items(), key=lambda kv: -kv[1]))
        goal_models = [m for m in ("dixon_coles", "nb2", "gas_poisson") if m in cmp.fixed(market)]
        goal_txt = ""
        if market != "1x2":
            gains = {m: cmp.pooled_test(market, a, m) for m in goal_models}
            not_sig = [m for m, t in gains.items() if not (t["p_holm_family"] < 0.05 and t["mean_diff"] > 0)]
            listing = ", ".join(f"`{m}` {signed(gains[m]['mean_diff'])}" for m in goal_models)
            if not_sig:
                goal_txt = f" Modele oparte tylko na bramkach: {listing}; bez istotnej przewagi po korekcie: {', '.join(f'`{m}`' for m in not_sig)}."
            else:
                goal_txt = f" Modele oparte tylko na bramkach: {listing}, istotne po korekcie, ale mniejsze niż u `{f['best']}`."
        add(
            f"- **{MARKET_PL[market]}: {f['verdict']}.** Ligi istotnie lepsze od baseline'u po Holmie (z {f['counts']['n_tested']}): {ordered}. "
            f"Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: {f['counts']['worse_holm']}.{goal_txt}"
        )
    add("")

    # ---- best model chosen on inner folds ---------------------------------------------------------
    choices = cmp.t["selection_choices"]
    add("**Najlepszy model wybrany tylko na foldach wewnętrznych** (na sezonie przed ocenianym; wybór na kolejny sezon, "
        f"{int(choices['scored_season'].max())}, opiera się na sezonie {int(choices['fit_season'].max())}):")
    add("")
    add("| Rynek | " + " | ".join(str(int(s)) for s in sorted(choices["scored_season"].unique())) + " |")
    add("|---|" + "---|" * choices["scored_season"].nunique())
    for market in cmp.markets:
        rows = choices[choices["market"] == market].sort_values("scored_season")
        add(f"| {MARKET_SHORT[market]} | " + " | ".join(f"`{c}`" for c in rows["chosen"]) + " |")
    add("")
    vs_best = {}
    for market in cmp.markets:
        row = cmp.versus(market, b, "pool_models", "versus_best")
        row = row[row["league_code"] == ms.POOLED_LABEL]
        vs_best[market] = None if row.empty else row.iloc[0]
    better = [m for m in cmp.markets if facts[m]["pool_vs_best"] > 0]
    significant = [m for m in better if vs_best[m] is not None and vs_best[m]["p_value"] < 0.05]
    pool_line = ", ".join(f"{MARKET_SHORT[m]} {signed(facts[m]['pool_vs_best'])}" for m in cmp.markets)
    if len(better) == len(cmp.markets):
        head = f"Pula `pool_models` (średnia geometryczna prognoz z wagami dobranymi na poprzednim sezonie) jest w każdym rynku lepsza od najlepszego pojedynczego modelu po fakcie ({dash(b)})"
    else:
        head = f"Pula `pool_models` (średnia geometryczna prognoz z wagami dobranymi na poprzednim sezonie) jest lepsza od najlepszego pojedynczego modelu po fakcie ({dash(b)}) w {len(better)} z {len(cmp.markets)} rynków"
    add(
        f"{head}: {pool_line} (różnica log-lossu; test Diebolda–Mariano p < 0,05 w {len(significant)} z {len(cmp.markets)} rynków). "
        "Szczegóły i przedziały niżej. " + (
            "Do pytania 2 proponuję użyć `pool_models`; `selected_best` zostaje jako prostsza alternatywa."
            if len(better) == len(cmp.markets) else "Do pytania 2 wybór między `pool_models` a `selected_best` trzeba rozstrzygnąć osobno dla każdego rynku."
        )
    )
    add("")

    # ---- method ---------------------------------------------------------------------------------
    add("## Jak mierzyliśmy")
    add("")
    add(
        f"- **Ten sam protokół dla wszystkich modeli** (`evaluation/protocol.py`): sezony testowe {dash(a)}, jeden model na ligę, dopasowywany od nowa przed "
        "każdą kolejką na meczach zakończonych co najmniej 3 godziny przed pierwszym gwizdkiem kolejki; hiperparametry wybierane na jednym sezonie przed "
        "sezonem testowym. Etykieta sezonu to rok startu według dostawcy (2025 = sezon 2025/26; w ligach z rokiem kalendarzowym po prostu rok). "
        "Prognozy modeli pochodzą z zapisanych plików `predictions/<model>.sqlite`, niczego nie liczyliśmy od nowa poza baseline'em."
    )
    add(
        f"- **Te same mecze.** W każdym rynku porównujemy modele na przecięciu prognoz wszystkich modeli i baseline'u: {thousands(n_common)} z "
        f"{thousands(n_fixtures)} meczów (niżej, ile odpadło i dlaczego). Brak prognozy zostaje brakiem, nigdy nie jest liczony jako zero."
    )
    add(
        "- **Metryki.** Główna to log-loss, pomocniczo Brier i RPS (uporządkowane klasy). Przedziały 95% to bootstrap po kolejkach "
        f"({thousands(summary.get('n_resamples', 0))} losowań, ziarno {summary.get('seed', 0)}; całe kolejki ligi losowane ze zwracaniem, bo mecze jednej kolejki nie są niezależne)."
    )
    add(
        "- **Testy.** Test Diebolda–Mariano z wariancją HAC po kolejkach: każdy model kontra baseline i kontra najlepszy z pozostałych. Korekta Holma i "
        "Benjaminiego–Hochberga liczona między ligami (rodzina 47 lig osobno dla modelu i rynku). Przedziały po korekcie to przedziały Bonferroniego z tą samą "
        "statystyką co test (przedział wyklucza zero dokładnie wtedy, gdy p < 0,05 / liczba porównań). Wyniki zbiorcze mają dodatkową korektę Holma "
        "po wszystkich parach model–rynek."
    )
    add(
        "- **Wybór bez podglądania.** Dla sezonu S wybieramy model o najlepszym log-lossie na prognozach sezonu S − 1 (same są poza próbą) "
        "i oceniamy ten wybór na S. Sezon 2022 służy tylko do wyboru dla 2023, więc `selected_best` i `pool_models` mają prognozy od 2023. "
        "Pula ma wagi z S − 1 (nieujemne, suma 1, delikatna kara 1e-4 ku równym wagom, żeby prawie identyczne modele dzieliły wagę). W puli jest także baseline, "
        "więc pula może się cofnąć do częstości ligowych, gdy modele nic nie wnoszą. Ten sam wybór z sezonu S − 1 obowiązuje wszystkie ligi."
    )
    add("- **Modele w tabelach:**")
    add("")
    for model in [BASELINE_NAME, *FIXED_ORDER, *DERIVED_ORDER]:
        add(f"  - `{model}`: {MODEL_PL[model]}.")
    add("")

    # ---- coverage -----------------------------------------------------------------------------------
    add("## Które mecze weszły do porównania")
    add("")
    add(
        f"W archiwum jest {thousands(n_fixtures)} meczów sezonów testowych {dash(a)} w {n_leagues} ligach. Do wspólnej próby weszło {thousands(n_common)} "
        f"({pct(n_common / n_fixtures, 2)}). Odpadło {thousands(n_fixtures - n_common)}:"
    )
    add("")
    shared = cov_a[["n_missing_shared", "leagues_shared"]].iloc[0]
    add(
        f"- **{int(shared['n_missing_shared'])} meczów bez prognozy u wszystkich modeli i baseline'u** ({shared['leagues_shared']}). To pierwsze kolejki lig, "
        "których archiwum zaczyna się dopiero w sezonie testowym: rama wymaga co najmniej 50 meczów historii ligi przed kolejką."
    )
    specific = coverage[(coverage["sample"] == a) & (coverage["n_missing_specific"] > 0)]
    for (model, count, where), group in specific.groupby(["model", "n_missing_specific", "leagues_specific"], sort=False):
        markets_txt = ", ".join(MARKET_SHORT[m] for m in group["market"])
        add(
            f"- `{model}` nie ma prognozy dla {int(count)} meczów, które baseline ma ({where}); dotyczy rynków: {markets_txt}. Według opisu modelu "
            f"brakuje tam poziomu ligi z ostatnich 730 dni (początek archiwum tych lig). Ponieważ porównujemy na przecięciu, wszystkie modele tracą "
            f"te mecze ({pct(int(count) / n_fixtures, 2)} próby)."
        )
    skipped = sorted({m for market in cmp.markets for m in FIXED_ORDER if m not in cmp.fixed(market)})
    if skipped:
        add(f"- `{', '.join(skipped)}` ma prognozy tylko dla 1X2, więc w rynkach goli go nie ma.")
    add("")
    sizes = coverage[(coverage["sample"] == a)].groupby("market")["n_common"].first()
    if sizes.nunique() == 1:
        add(f"We wszystkich rynkach wspólna próba to {thousands(sizes.iloc[0])} meczów. Liczby dla każdego modelu ({MARKET_SHORT[market0]}; w rynkach goli te same, bez `elo_davidson`):")
    else:
        add("Rozmiar wspólnej próby: " + ", ".join(f"{MARKET_SHORT[m]} {thousands(sizes[m])}" for m in cmp.markets) + f". Liczby dla każdego modelu ({MARKET_SHORT[market0]}):")
    add("")
    add("| Model | Ma prognozę | Bez prognozy | w tym też bez prognozy u baseline'u | bez prognozy tylko u tego modelu | Utracone przez brak u innych | Wspólna próba |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for _, row in cov_a.iterrows():
        add(
            f"| `{row['model']}` | {thousands(row['n_forecast'])} | {thousands(row['n_missing'])} | {thousands(row['n_missing_shared'])} | "
            f"{thousands(row['n_missing_specific'])} | {thousands(row['n_lost_to_others'])} | {thousands(row['n_common'])} |"
        )
    add("")

    # ---- per market results ------------------------------------------------------------------------
    add(f"## Wynik zbiorczy, sezony {dash(a)}")
    add("")
    add(
        "Modele od najlepszego. „p po Holmie” to p-wartość testu Diebolda–Mariano dla wyniku zbiorczego po korekcie Holma na wszystkie pary model–rynek. "
        "„Ligi lepsze” to liczba lig, w których model jest istotnie lepszy od baseline'u na poziomie 5% bez korekty, po korekcie Holma i po korekcie "
        "Benjaminiego–Hochberga (z liczby lig w ocenie); „Ligi gorsze (Holm)” to liczba lig, w których baseline jest istotnie lepszy. Pełne tabele per liga "
        f"(log-loss z przedziałami, testy, przedziały po korekcie Bonferroniego) są w `predictions/{COMPARISON_DB}` (tabele `scores`, `versus_reference`, `versus_best`)."
    )
    for market in cmp.markets:
        add("")
        add(f"### {MARKET_PL[market]}")
        add("")
        lines += market_table(cmp, market)
        best = cmp.best_fixed(market, a)
        vb = cmp.versus(market, a, best, "versus_best")
        vb_counts = ms.significance_counts(vb) if not vb.empty else None
        vb_pooled = vb[vb["league_code"] == ms.POOLED_LABEL]
        if vb_counts and not vb_pooled.empty:
            other = vb_pooled.iloc[0]["reference"]
            add("")
            add(
                f"`{best}` kontra najlepszy z pozostałych (`{other}`): różnica {signed(vb_pooled.iloc[0]['mean_diff'])} "
                f"{ci(vb_pooled.iloc[0]['ci_lo'], vb_pooled.iloc[0]['ci_hi'], plus=True)}, {p_text(vb_pooled.iloc[0]['p_value'])}; "
                f"lepszy w {vb_counts['better_raw']} z {vb_counts['n_tested']} lig na poziomie 5%, po Holmie w {vb_counts['better_holm']}. "
                "Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru."
            )

    # ---- selection ---------------------------------------------------------------------------------------
    add("")
    add(f"## Wybór modelu bez podglądania, sezony {dash(b)}")
    add("")
    add(
        f"Dla każdego sezonu ocenianego wybieramy model na poprzednim sezonie i oceniamy go na tym sezonie. Kolumna „najlepszy po fakcie” pokazuje, "
        "kogo wybralibyśmy, gdybyśmy znali wynik sezonu, a „strata wyboru” ile log-lossu kosztuje niewiedza. „Przewaga nad drugim” to różnica log-lossu "
        "zwycięzcy i drugiego modelu w próbie wyboru; gdy jest mniejsza niż jej błąd standardowy, wybór jest w praktyce losowaniem między bliskimi modelami."
    )
    for market in cmp.markets:
        add("")
        add(f"### {MARKET_PL[market]}")
        add("")
        lines += selection_tables(cmp, market)
        add("")
        lines += derived_table(cmp, market)
        add("")
        add("Wagi puli `pool_models` (pominięte modele z wagą 0):")
        add("")
        lines += weight_table(cmp, market)
    add("")
    sel_l = {m: cmp.gain(m, b, "selected_best_league") - cmp.gain(m, b, "selected_best") for m in cmp.markets}
    add(
        "**Wybór osobno dla każdej ligi** (`selected_best_league`, ze ściągnięciem do wyboru zbiorczego metodą empirycznego Bayesa) nie pomaga: różnica log-lossu "
        "względem wyboru zbiorczego to " + ", ".join(f"{MARKET_SHORT[m]} {signed(v)}" for m, v in sel_l.items()) + ". Różnice między modelami w jednej lidze są "
        "za małe względem szumu, żeby dało się je wybierać ligą po lidze. **Pula liniowa** (`pool_models_linear`) daje praktycznie to samo co logarytmiczna."
    )
    add("")

    # ---- calibration -------------------------------------------------------------------------------------------
    add(f"## Kalibracja (CORP), sezony {dash(b)}")
    add("")
    add(
        "Rozkład CORP dzieli wynik Briera na MCB (miskalibracja), DSC (zdolność odróżniania meczów) i UNC (nieusuwalna niepewność): Brier = MCB − DSC + UNC. "
        "Wartości ×1000; dla 1X2 suma po trzech klasach, dla rynków dwuklasowych zdarzenie „powyżej” albo „tak”. Dobry model ma DSC dużo większe od MCB. "
        f"„Średnia prognoza vs częstość” to różnica w punktach procentowych (dla 1X2 klasa z największą różnicą). p(MCB) to najmniejsza p-wartość testu Monte Carlo "
        f"({summary.get('corp_sims', 0)} symulacji) hipotezy, że prognozy są wiarygodne."
    )
    for market in cmp.markets:
        add("")
        add(f"### {MARKET_PL[market]}")
        add("")
        lines += calibration_table(cmp, market)
    add("")
    goal_gaps, base_gaps, ratios = {}, {}, {}
    for market in cmp.markets:
        if market == "1x2":
            continue
        corp = cmp.corp(market, b)
        rows = corp[corp["model"].isin(["dixon_coles", "nb2", "gas_poisson"])]
        goal_gaps[market] = float((rows["mean_p"] - rows["base_rate"]).mean() * 100)
        base = corp[corp["model"] == BASELINE_NAME].iloc[0]
        base_gaps[market] = float((base["mean_p"] - base["base_rate"]) * 100)
        best_mcb = float(corp[corp["model"] == cmp.best_fixed(market, b)]["mcb"].iloc[0])
        ratios[market] = float(rows["mcb"].mean() / best_mcb)
    one = cmp.corp("1x2", b)
    one_models = one[one["model"] != BASELINE_NAME].groupby("model").agg(mcb=("mcb", "sum"), dsc=("dsc", "sum"), p=("mcb_pvalue", "min"))
    add(
        "**Rynki goli i BTTS.** Modele oparte tylko na bramkach (Dixon–Coles, NB2, GAS) zaniżają prawdopodobieństwo goli („powyżej” albo „tak”): średnia różnica "
        "względem częstości (w punktach procentowych) to " + ", ".join(f"{MARKET_SHORT[m]} {signed(v, 1)}" for m, v in goal_gaps.items())
        + ". Baseline też zaniża (" + ", ".join(f"{MARKET_SHORT[m]} {signed(v, 1)}" for m, v in base_gaps.items())
        + "), prawdopodobnie dlatego, że częstości z przeszłości są niższe niż w kolejnych sezonach (poziom goli rośnie). "
        f"MCB modeli z samych bramek jest {num(min(ratios.values()), 1)}–{num(max(ratios.values()), 1)} razy większe niż u najlepszego pojedynczego modelu, "
        "który uczy się poziomu ligi i formy wprost."
    )
    add("")
    add(
        f"**1X2.** Miskalibracja jest mała: MCB {num(one_models['mcb'].min() * 1000, 2)}–{num(one_models['mcb'].max() * 1000, 2)} przy DSC "
        f"{num(one_models['dsc'].min() * 1000, 1)}–{num(one_models['dsc'].max() * 1000, 1)} (×1000). Test Monte Carlo wykrywa ją u {int((one_models['p'] < 0.05).sum())} z {len(one_models)} modeli i wariantów "
        "(najmniejsza z trzech p-wartości klas poniżej 0,05, bez korekty na trzy klasy); przy tylu meczach wykrywa się także drobne odchylenia."
    )
    add("")

    # ---- seasons -----------------------------------------------------------------------------------------------
    add("## Stabilność po sezonach")
    add("")
    add("Zysk względem baseline'u (log-loss baseline'u minus modelu) na wspólnej próbie, po sezonach. `selected_best` i `pool_models` nie mają prognoz na 2022.")
    add("")
    gains = season_gains(cmp)
    lines += season_gain_table(cmp, gains)
    add("")
    for remark in season_remarks(cmp, gains):
        add(remark)
        add("")

    # ---- leagues -----------------------------------------------------------------------------------------------
    add(f"## Wynik w ligach, sezony {dash(b)}")
    add("")
    add(
        "Zysk względem baseline'u w każdej lidze (dodatni = model lepszy). Gwiazdka oznacza istotną przewagę modelu po korekcie Holma (5%, korekta między ligami "
        "dla danego modelu i rynku), krzyżyk istotną przewagę baseline'u. Próba to sezony " + dash(b) + " (te same mecze dla wszystkich modeli)."
    )
    add("")
    lines += league_tables(cmp)
    add("")

    # ---- leakage ---------------------------------------------------------------------------------------------------
    add("## Test przecieku wyboru")
    add("")
    leak = cmp.t["selection_leakage"]
    add(
        "Test analogiczny do testu przecieku ramy: wyniki sezonu T i wszystkich późniejszych zostają zmienione (numer klasy przesunięty o jeden), a potem "
        "sprawdzamy, czy jakakolwiek prognoza `selected_best`, `pool_models`, puli liniowej i wyboru per liga z sezonów do T włącznie się zmieniła. "
        "Nie może się zmienić, bo wybór i wagi dla sezonu S widzą tylko wyniki z S − 1. Prognozy późniejszych sezonów muszą się zmienić, "
        "co pokazuje, że test coś wykrywa."
    )
    add("")
    add("| Rynek | Zmieniony od sezonu T | Prognoz porównanych (4 rodzaje, sezony do T) | Zmienionych (musi być 0) | Zmienionych w sezonach po T (oczekiwane; po 2025 nic nie ma) |")
    add("|---|---|---:|---:|---:|")
    for _, row in leak.iterrows():
        add(
            f"| {MARKET_SHORT[row['market']]} | {int(row['disturbed_from_season'])} | {thousands(row['n_compared'])} | {int(row['n_changed'])} | {thousands(row['n_later_changed'])} |"
        )
    add("")
    mutation = cmp.t.get("selection_leakage_mutation")
    if mutation is not None and len(mutation):
        add(
            "**Próba mutacji.** Ten sam test z celowo zepsutą regułą (wybór i wagi z sezonu S zamiast S − 1) zmienia prognozy w każdym rynku i przy każdym T: "
            f"od {thousands(mutation['n_changed'].min())} do {thousands(mutation['n_changed'].max())} zmienionych prognoz na prawdziwych danych. "
            "Skrypt przerywa pracę, gdyby test tego nie wykrył. Osobno `app/tests/test_model_selection.py` sprawdza to samo na danych syntetycznych."
        )
    else:
        add("Test jest sprawdzony próbą mutacji w `app/tests/test_model_selection.py`: wybór na sezonie S zamiast S − 1 (celowo zepsuty kod) jest wychwytywany.")
    add("")
    add("Same modele mają własne testy przecieku opisane przy ich budowie (na wybranych ligach); tu sprawdzamy tylko warstwę wyboru i puli.")
    add("")

    # ---- caveats --------------------------------------------------------------------------------------------------
    add("## Ograniczenia")
    add("")
    add("- **To filtr, nie dowód.** Pytanie 1 mówi, że model przewiduje lepiej niż częstość ligowa. Rynek kursów wie więcej niż baseline, więc to, czy model wie więcej niż rynek, pokaże dopiero pytanie 2 (kursy zamknięcia Pinnacle na połączonych danych).")
    add(f"- **Ranking „najlepszy po fakcie” jest opisowy.** Liczymy go na sezonach ocenianych, więc przy porównaniu z nim wygrywający model ma przewagę. Uczciwym testem ex ante są `selected_best` i `pool_models`, wybrane na poprzednim sezonie.")
    calendar = cmp.t.get("calendar_year_check")
    calendar_txt = ""
    if calendar is not None and len(calendar):
        kept = calendar["n_fit_strict"] / calendar["n_fit_main"]
        pool_drift = float((calendar["pool_log_loss_main"] - calendar["pool_log_loss_strict"]).abs().max())
        selected_drift = float((calendar["selected_log_loss_main"] - calendar["selected_log_loss_strict"]).abs().max())
        margins = calendar.merge(cmp.t["selection_choices"][["market", "scored_season", "margin", "margin_se"]], on=["market", "scored_season"])
        changed = margins[margins["chosen_main"] != margins["chosen_strict"]]
        close = int((changed["margin"] < changed["margin_se"]).sum())
        calendar_txt = (
            f" Sprawdziliśmy, ile to zmienia: wybór tylko na meczach S − 1 rozegranych przed pierwszym meczem S (zostaje {pct(kept.min(), 0)}–{pct(kept.max(), 0)} próby wyboru) "
            f"daje ten sam wybór modelu w {len(calendar) - len(changed)} z {len(calendar)} przypadków rynek–sezon"
            + (f" (ze zmienionych wyborów {close} z {len(changed)} miało w próbie wyboru przewagę mniejszą niż błąd standardowy)" if len(changed) else "")
            + f". Log-loss puli zmienia się najwyżej o {num(pool_drift, 4)}, log-loss wyboru o {num(selected_drift, 4)}. Tabela `calendar_year_check`."
        )
    add(f"- **Sezony.** `selected_best` i `pool_models` oceniamy na {dash(b)}, bo {a.split('-')[0]} jest sezonem wyboru. W ligach z rokiem kalendarzowym (etykieta sezonu = rok) sezon S − 1 kończy się przed startem S, ale w ligach europejskich ostatnie kolejki S − 1 (marzec–maj) rozgrywają się już po starcie lig kalendarzowych sezonu S. Wybór zbiorczy może więc widzieć kilka wyników innych lig, które zapadły po pierwszym meczu takiej ligi w S. Wyników sezonu S nie widzi nigdy.{calendar_txt}")
    add("- **Modele goli nie są niezależne od siebie.** Dixon–Coles, NB2 i GAS mają prawie identyczne prognozy (NB2 jest blisko Poissona), więc ich zgodność nie jest trzema niezależnymi potwierdzeniami. Modele bazujące na bramkach zaniżają liczbę bramek (patrz kalibracja); poprawka poziomu goli albo warstwa reszt względem rynku (pytanie 2) może to naprawić.")
    goal_markets = [m for m in cmp.markets if m != "1x2"]
    shares = [facts[m]["gain"] / facts[m]["base"] for m in goal_markets]
    holm_counts = [facts[m]["counts"]["better_holm"] for m in goal_markets]
    add(
        f"- **Małe zyski w rynkach goli i BTTS.** Zysk {pct(min(shares))}–{pct(max(shares))} log-lossu baseline'u jest istotny statystycznie zbiorczo, ale po korekcie Holma "
        f"wychodzi w {min(holm_counts)}–{max(holm_counts)} z {facts['1x2']['counts']['n_tested']} lig. Czy to wystarczy wobec kursów, pokaże pytanie 2."
    )
    add("- **Zależność meczów.** Bootstrap i test DM po kolejkach zakładają, że kolejki są mniej więcej niezależne. Te same drużyny grają w wielu kolejkach, więc przedziały mogą być odrobinę za wąskie.")
    add("- **Zakres.** 47 lig europejskich, sezony 2022–2025; wnioski nie obejmują innych lig ani rozgrywek pucharowych.")
    add("")

    # ---- files and time ----------------------------------------------------------------------------------------------
    add("## Pliki i czas")
    add("")
    add(f"- `predictions/{COMPARISON_DB}`: wszystkie tabele (pokrycie, ranking, wyniki per liga, testy z korektami, CORP, wybory, wagi puli, test przecieku).")
    add("- `predictions/baseline_league_freq.sqlite`: baseline przeliczony pod `COMPARISON_CONFIG`. `predictions/selected_best.sqlite` i `predictions/pool_models.sqlite`: wybrany model i pula w formacie `save_result`; kolumna `params` mówi, co wybrano dla meczu; wiersze sezonu 2022 mają NaN (sezon wyboru).")
    add("- Kod: `app/src/model_buk/evaluation/model_selection.py`, `app/scripts/compare_goal_models.py`, testy `app/tests/test_model_selection.py`.")
    seconds = summary.get("seconds")
    if seconds:
        add(f"- Czas: porównanie i wybór dla wszystkich rynków {num(seconds / 60, 1)} min (jeden proces).")
    add("")
    return "\n".join(lines)


def stage_report(args: argparse.Namespace) -> Path:
    tables = read_tables(Path(args.predictions_dir) / COMPARISON_DB)
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8")) if Path(args.summary).exists() else {}
    text = build_report(Comparison(tables), summary, args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    say(f"report written: {args.out} ({len(text.splitlines())} lines)")
    return args.out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("baseline", "compare", "calendar", "report", "all"), default="all")
    parser.add_argument("--predictions-dir", type=Path, default=PREDICTIONS_DIR)
    parser.add_argument("--force", action="store_true", help="recompute the baseline even if its file exists")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="Markdown report to write")
    parser.add_argument("--resamples", type=int, default=2000, help="bootstrap resamples for the intervals")
    parser.add_argument("--corp-sims", type=int, default=200, help="Monte Carlo simulations of the CORP MCB p-value")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.stage in ("baseline", "all"):
        stage_baseline(args)
    if args.stage in ("compare", "all"):
        stage_compare(args)
    if args.stage == "calendar":
        stage_calendar(args)
    if args.stage in ("report", "all"):
        stage_report(args)


if __name__ == "__main__":
    main()
