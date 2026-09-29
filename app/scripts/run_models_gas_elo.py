"""Run GAS-Poisson (maximum likelihood) and Elo-Davidson under the fixed comparison protocol.

Offline only: reads the API-Football ``matches`` table read-only and writes the
out-of-sample forecasts to ``predictions/gas_poisson.sqlite`` (all five goal
markets) and ``predictions/elo_davidson.sqlite`` (1X2 only) through
``model_buk.evaluation.protocol.save_result``.  The protocol itself
(``COMPARISON_CONFIG``: test seasons 2022-2025, one model per league refit before
every matchday, hyper-parameters chosen on the season before each test season,
pooled) is not touched: the final numbers come from an unmodified
``evaluate_model(spec, all_matches, market, COMPARISON_CONFIG)``.

Two stages make it fast without changing what is computed:

1. **Fit stage** (parallel, at most ``--workers`` processes, one league per task).  The
   framework enumerates every (league, training set) it will ask for; the fits are
   computed once and stored in a fit cache keyed by ``(model, league, parameters,
   digest of the exact training set)``.  Each league's fits are pickled to
   ``--work-dir`` as soon as they exist, so an interrupted run resumes where it stopped.
   GAS needs one walk-forward per league (no hyper-parameters); Elo runs every
   ``k_factor`` of the grid over the seasons 2021-2025 (2021-2024 are the inner
   seasons, 2022-2025 the outer ones).
2. **Evaluate stage** (main process).  The unmodified protocol runs on all leagues
   and all markets with the merged cache: every fit is a hit for the identical
   training set, one GAS fit serves all five markets, and the pooled
   hyper-parameter choice of Elo is the framework's own.  A cache miss falls back
   to fitting on the spot, so the result never depends on the cache being complete;
   the number of misses is reported.

Then: score tables against the league-frequency baseline (log-loss, pooled and per
league, with Diebold-Mariano / Holm / Benjamini-Hochberg and matchday-bootstrap
intervals), estimated parameters, and the look-ahead test on real leagues with the
cache disabled.  The summary is printed and saved as JSON in ``--work-dir``.

Usage (from the app/ directory)::

    .venv\\Scripts\\python scripts\\run_models_gas_elo.py                  # everything
    .venv\\Scripts\\python scripts\\run_models_gas_elo.py --models gas --leagues EPL,LALIGA   # dry run: never saved
    .venv\\Scripts\\python scripts\\run_models_gas_elo.py --skip-fit         # reuse pickled fits

The fit cache in ``--work-dir`` is keyed by the training data and the parameters, not by the code
version: delete the pickles after changing gas_poisson.py, elo_davidson.py or dynamic_adapters.py.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pickle
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", "")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model_buk.evaluation.framework import (  # noqa: E402
    MARKET_1X2,
    ModelSpec,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
    league_frequency_spec,
)
from model_buk.evaluation.protocol import (  # noqa: E402
    COMPARISON_CONFIG,
    GOAL_MARKETS,
    PREDICTIONS_DIR,
    load_history_matches,
    save_result,
)
from model_buk.models.dynamic_adapters import (  # noqa: E402
    ELO_K_GRID,
    EloDavidsonForecaster,
    FitCache,
    elo_davidson_spec,
    gas_poisson_spec,
)

DEFAULT_WORK_DIR = PREDICTIONS_DIR / "_work_gas_elo"
MODEL_MARKETS = {"gas_poisson": GOAL_MARKETS, "elo_davidson": (MARKET_1X2,)}
LEAK_CHECKS = (("EPL", "2023-11-01"), ("ALLSVENSKAN", "2024-06-15"))


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def fit_pickle(work_dir: Path, model: str, league: str) -> Path:
    return work_dir / f"fits_{model}_{league}.pkl"


# ---------------------------------------------------------------------------
# Stage 1: fits per league (worker processes)
# ---------------------------------------------------------------------------


def fit_league(model: str, league: str, matches: pd.DataFrame, work_dir: str) -> dict:
    """Compute and pickle every fit the protocol needs for one league; returns timing and cache statistics."""
    sys.path.insert(0, str(SRC))
    started = time.perf_counter()
    cache = FitCache()
    if model == "gas_poisson":
        evaluate_model(gas_poisson_spec(MARKET_1X2, cache=cache), matches, MARKET_1X2, COMPARISON_CONFIG)
    else:
        # Inner seasons (2021-2024) and outer seasons (2022-2025) for every k of the grid.
        wide = dataclasses.replace(COMPARISON_CONFIG, test_seasons=range(2021, 2026))
        for k in ELO_K_GRID:
            spec = ModelSpec(
                model, lambda k_factor=k: EloDavidsonForecaster(k_factor, cache=cache)
            )
            evaluate_model(spec, matches, MARKET_1X2, wide)
    path = fit_pickle(Path(work_dir), model, league)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "wb") as handle:
        pickle.dump(cache.items, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, path)
    return {"model": model, "league": league, "fits": len(cache), "seconds": time.perf_counter() - started}


def run_fit_stage(models: list[str], leagues: list[str], matches: pd.DataFrame, work_dir: Path, workers: int) -> dict:
    from joblib import Parallel, delayed

    size = matches.groupby("league_code").size()
    tasks = []
    for model in models:
        for league in sorted(leagues, key=lambda name: -int(size.get(name, 0))):  # longest first
            if fit_pickle(work_dir, model, league).exists():
                continue
            tasks.append((model, league))
    log(f"fit stage: {len(tasks)} league fits to compute with {workers} workers "
        f"({sum(1 for m in models for l in leagues) - len(tasks)} already on disk)")
    started = time.perf_counter()
    done = []
    if tasks:
        stream = Parallel(n_jobs=workers, backend="loky", return_as="generator_unordered")(
            delayed(fit_league)(model, league, matches[matches["league_code"] == league], str(work_dir))
            for model, league in tasks
        )
        for info in stream:
            done.append(info)
            log(f"  {info['model']:13s} {info['league']:15s} {info['fits']:5d} fits in {info['seconds']:6.1f}s "
                f"({len(done)}/{len(tasks)})")
    return {"seconds": time.perf_counter() - started, "n_league_fits": len(done)}


# ---------------------------------------------------------------------------
# Stage 2: the unmodified protocol on all leagues, from the cache
# ---------------------------------------------------------------------------


def load_cache(models: list[str], leagues: list[str], work_dir: Path) -> FitCache:
    cache = FitCache()
    for model in models:
        for league in leagues:
            path = fit_pickle(work_dir, model, league)
            if path.exists():
                with open(path, "rb") as handle:
                    cache.update(pickle.load(handle))
    return cache


def gas_parameter_table(cache: FitCache) -> pd.DataFrame:
    rows = []
    for key, fitted in cache.items.items():
        if key[0] != "gas_poisson":
            continue
        fit = fitted.fit
        row = {"league": key[1], "n_matches": fit.n_matches, "converged": fit.converged,
               "evaluations": fit.n_evaluations, "log_likelihood": fit.log_likelihood}
        row.update(fit.parameters())
        rows.append(row)
    return pd.DataFrame(rows)


def elo_parameter_table(cache: FitCache) -> pd.DataFrame:
    rows = []
    for key, fitted in cache.items.items():
        if key[0] != "elo_davidson":
            continue
        fit = fitted.fit
        rows.append({"league": key[1], "n_matches": fit.n_matches, "k_factor": fit.k_factor,
                     "home_advantage_points": fit.home_advantage_points, "draw_parameter": fit.draw_parameter,
                     "entrant_shift": fit.entrant_shift})
    return pd.DataFrame(rows)


def score_summary(result, baseline, resamples: int) -> dict:
    table = result.score_table(n_resamples=resamples)
    base_table = baseline.score_table(n_resamples=resamples)
    versus = compare_to_baseline(result, baseline, n_resamples=resamples)
    pooled = table.iloc[-1]
    base_pooled = base_table.iloc[-1]
    leagues = versus.iloc[:-1]
    versus_pooled = versus.iloc[-1]
    return {
        "n_forecasts": int(pooled["n"]),
        "n_missing": int(pooled["n_missing"]),
        "log_loss": float(pooled["log_loss"]),
        "log_loss_ci": [float(pooled["log_loss_lo"]), float(pooled["log_loss_hi"])],
        "brier": float(pooled["brier"]),
        "rps": float(pooled["rps"]),
        "baseline_log_loss": float(base_pooled["log_loss"]),
        "baseline_log_loss_ci": [float(base_pooled["log_loss_lo"]), float(base_pooled["log_loss_hi"])],
        "baseline_n": int(base_pooled["n"]),
        "gain_vs_baseline": float(versus_pooled["mean_diff"]),
        "gain_ci": [float(versus_pooled["ci_lo"]), float(versus_pooled["ci_hi"])],
        "dm_p_value": float(versus_pooled["p_value"]),
        "leagues_better_holm": int(((leagues["mean_diff"] > 0) & (leagues["p_holm"] < 0.05)).sum()),
        "leagues_worse_holm": int(((leagues["mean_diff"] < 0) & (leagues["p_holm"] < 0.05)).sum()),
        "leagues_better_raw": int(((leagues["mean_diff"] > 0) & (leagues["p_value"] < 0.05)).sum()),
        "leagues_tested": int(leagues["p_value"].notna().sum()),
        "per_league": {
            str(row["league_code"]): {"n": int(row["n"]), "log_loss": float(row["log_loss"])}
            for _, row in table.iloc[:-1].iterrows()
        },
        "per_league_gain": {
            str(row["league_code"]): {"mean_diff": float(row["mean_diff"]), "p_holm": float(row["p_holm"])}
            for _, row in leagues.iterrows()
        },
    }


def run_evaluate_stage(models: list[str], matches: pd.DataFrame, cache: FitCache, resamples: int, save: bool) -> dict:
    summary: dict = {}
    for model in models:
        summary[model] = {}
        for market in MODEL_MARKETS[model]:
            started = time.perf_counter()
            spec = gas_poisson_spec(market, cache=cache) if model == "gas_poisson" else elo_davidson_spec(cache=cache)
            result = evaluate_model(spec, matches, market, COMPARISON_CONFIG)
            baseline = evaluate_model(league_frequency_spec(market), matches, market, COMPARISON_CONFIG)
            if save:
                save_result(result)
            entry = score_summary(result, baseline, resamples)
            entry["seconds"] = time.perf_counter() - started
            entry["selections"] = result.selections.astype(str).to_dict("records")
            summary[model][market.name] = entry
            log(f"{model:13s} {market.name:15s} log-loss {entry['log_loss']:.4f} "
                f"(baseline {entry['baseline_log_loss']:.4f}, gain {entry['gain_vs_baseline']:+.4f} "
                f"[{entry['gain_ci'][0]:+.4f}; {entry['gain_ci'][1]:+.4f}]) n={entry['n_forecasts']} "
                f"missing={entry['n_missing']}  cache misses so far {cache.misses}  {entry['seconds']:.0f}s")
    return summary


# ---------------------------------------------------------------------------
# Look-ahead test on real leagues (cache disabled: the whole fit is re-run on the disturbed table)
# ---------------------------------------------------------------------------


def run_leakage_checks(models: list[str], matches: pd.DataFrame) -> dict:
    out: dict = {}
    for model in models:
        market = MARKET_1X2
        spec = gas_poisson_spec(market, cache=None) if model == "gas_poisson" else elo_davidson_spec(cache=None)
        out[model] = {}
        for league, cutoff in LEAK_CHECKS:
            subset = matches[matches["league_code"] == league]
            if subset.empty:
                continue
            started = time.perf_counter()
            report = check_no_lookahead(
                lambda frame: evaluate_model(spec, frame, market, COMPARISON_CONFIG), subset, market, cutoff
            )
            out[model][league] = {
                "passed": bool(report.passed),
                "cutoff": str(cutoff),
                "n_compared": int(report.n_compared),
                "modes": [
                    {"mode": m.mode, "future_outcomes_changed": int(m.n_future_rows_changed),
                     "forecasts_changed": int(m.n_forecasts_changed), "max_abs_difference": float(m.max_abs_difference)}
                    for m in report.modes
                ],
                "seconds": time.perf_counter() - started,
            }
            log(f"leakage {model} {league}: {'passed' if report.passed else 'FAILED'} "
                f"({report.n_compared} forecasts compared, {time.perf_counter() - started:.0f}s)")
    return out


def describe(frame: pd.DataFrame, columns: list[str]) -> dict:
    out = {}
    for column in columns:
        values = frame[column].to_numpy(dtype=float)
        out[column] = {"min": float(np.min(values)), "p05": float(np.quantile(values, 0.05)),
                       "median": float(np.median(values)), "p95": float(np.quantile(values, 0.95)),
                       "max": float(np.max(values))}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="gas,elo", help="comma list of: gas, elo")
    parser.add_argument("--leagues", default="", help="comma list of league codes (default: all); implies a dry run (nothing is saved)")
    parser.add_argument("--workers", type=int, default=3, help="worker processes for the fit stage (at most 3 on this machine)")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--skip-fit", action="store_true", help="only merge fits already on disk (misses are fitted on the spot)")
    parser.add_argument("--skip-leakage", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="do not write predictions/*.sqlite (dry run)")
    parser.add_argument("--resamples", type=int, default=500, help="bootstrap resamples for intervals and tests")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.workers > 3:
        raise SystemExit("--workers is capped at 3: the machine is shared")
    aliases = {"gas": "gas_poisson", "elo": "elo_davidson"}
    models = [aliases[name.strip()] for name in args.models.split(",") if name.strip()]
    args.work_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    matches = load_history_matches()
    if args.leagues:
        wanted = [name.strip() for name in args.leagues.split(",") if name.strip()]
        matches = matches[matches["league_code"].isin(wanted)]
    leagues = sorted(matches["league_code"].unique())
    log(f"{len(matches)} matches, {len(leagues)} leagues, models {models}")

    summary: dict = {"protocol": dataclasses.asdict(COMPARISON_CONFIG) | {"test_seasons": list(COMPARISON_CONFIG.test_seasons)},
                     "models": models, "n_leagues": len(leagues)}
    if not args.skip_fit:
        summary["fit_stage"] = run_fit_stage(models, leagues, matches, args.work_dir, args.workers)
    cache = load_cache(models, leagues, args.work_dir)
    log(f"merged {len(cache)} cached fits")
    stage_started = time.perf_counter()
    # A subset of the leagues must never replace the stored all-league forecasts (that would silently
    # shorten the protocol, and Elo's pooled k choice would differ): subset runs are dry runs.
    save = not args.no_save and not args.leagues
    if args.leagues and not args.no_save:
        log("--leagues given: dry run, predictions/*.sqlite are not overwritten")
    summary["evaluate"] = run_evaluate_stage(models, matches, cache, args.resamples, save=save)
    summary["evaluate_seconds"] = time.perf_counter() - stage_started
    summary["cache"] = cache.stats()
    if "gas_poisson" in models:
        table = gas_parameter_table(cache)
        columns = ["intercept", "home_advantage", "attack_step", "defence_step", "persistence",
                   "entrant_attack", "entrant_defence"]
        summary["gas_parameters_all_refits"] = describe(table, columns)
        last = table.sort_values("n_matches").groupby("league").tail(1)
        summary["gas_parameters_last_fit_per_league"] = describe(last, columns)
        summary["gas_fits"] = {"n": int(len(table)), "not_converged": int((~table["converged"]).sum()),
                               "mean_evaluations": float(table["evaluations"].mean())}
    if "elo_davidson" in models:
        table = elo_parameter_table(cache)
        summary["elo_parameters_all_fits"] = describe(
            table, ["home_advantage_points", "draw_parameter", "entrant_shift"])
        summary["elo_fits"] = {"n": int(len(table))}
    if not args.skip_leakage:
        summary["leakage"] = run_leakage_checks(models, matches)
    summary["total_seconds"] = time.perf_counter() - started
    out = args.work_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log(f"done in {summary['total_seconds']:.0f}s; summary: {out}")


if __name__ == "__main__":
    main()
