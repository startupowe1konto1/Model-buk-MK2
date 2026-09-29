"""Run pi-ratings + gradient boosting under the fixed comparison protocol.

Offline only: reads ``history.sqlite`` (matches and team_match_stats, read-only) and writes the
out-of-sample forecasts of every goal market to ``predictions/pi_gbm.sqlite`` with the protocol's
``save_result``.  The protocol itself (test seasons 2022-2025, one model per league refit before every
matchday, hyper-parameters chosen on the season before each test season, pooled) is imported from
``model_buk.evaluation.protocol`` and is not changed here.

How the boosting model meets the protocol (details in ``model_buk/models/gbm_model.py``):

- features (pi-ratings, rolling form, rest days, league level, ...) are recomputed at every matchday from
  matches before that matchday's training cut-off;
- the pooled cross-league booster is fitted on matches before ``floor(cut-off / 28 days) * 28 days`` and
  reused for the matchdays of that 28-day bucket.  Boosters are fitted up front by up to three worker
  processes and cached on disk, so the run is resumable: run it again and only missing boosters are fitted.

Order: 1X2 and over/under 2.5 first (these have historical odds), then the other markets.  Before the real
run the look-ahead test is executed on a small pool of leagues (outcomes and match statistics of the
future are shuffled and mirrored; no earlier forecast may change).

Usage (from the app/ directory):

    .venv\\Scripts\\python -u scripts\\run_models_pi_gbm.py

    .venv\\Scripts\\python -u scripts\\run_models_pi_gbm.py --markets 1x2 over_under_2.5 --skip-leakage
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")  # one thread per process: the machine is shared

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation.framework import (  # noqa: E402
    Market,
    WalkForwardConfig,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
    league_frequency_spec,
    prepare_matches,
)
from model_buk.evaluation.protocol import (  # noqa: E402
    COMPARISON_CONFIG,
    GOAL_MARKETS,
    HISTORY_DB,
    PREDICTIONS_DIR,
    load_history_matches,
    load_predictions,
    save_result,
)
from model_buk.models.gbm_model import (  # noqa: E402
    DEFAULT_GRID,
    GbmSettings,
    PiGbmEngine,
    gain_importance,
    grid_combinations,
    permutation_importance,
    pi_gbm_spec,
    pi_logit_spec,
)

DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "runtime" / "pi_gbm_cache"
MARKET_ORDER = ("1x2", "over_under_2.5", "over_under_1.5", "over_under_3.5", "btts")
STATS_COLUMNS = ("fixture_id", "team_id", "total_shots", "shots_on_goal", "corners", "expected_goals")
_NS_PER_HOUR = 3_600_000_000_000


def load_team_stats(db_path: Path = HISTORY_DB) -> pd.DataFrame:
    """Per team and match statistics, read-only (missing values stay NULL -> NaN)."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as connection:
        return pd.read_sql_query(f"SELECT {', '.join(STATS_COLUMNS)} FROM team_match_stats", connection)


def matchday_starts(prep, seasons) -> np.ndarray:
    """First kickoff (ns) of every matchday of the given seasons."""
    frame = pd.DataFrame({"matchday": prep.matchday, "t": prep.t, "season": prep.season})
    frame = frame[frame["season"].isin(list(seasons))]
    return frame.groupby("matchday")["t"].min().to_numpy(dtype=np.int64)


def num(value: float, digits: int = 4) -> str:
    return "nan" if value is None or not np.isfinite(value) else f"{value:.{digits}f}"


# ---------------------------------------------------------------------------
# Look-ahead test on the real table (small pool of leagues, whole pipeline)
# ---------------------------------------------------------------------------


def run_leakage_check(matches: pd.DataFrame, stats: pd.DataFrame, args, settings: GbmSettings) -> dict:
    """Shuffle / mirror the outcomes and statistics of the future: no earlier forecast may change."""
    leagues = [name.strip() for name in args.leakage_leagues.split(",")]
    season = args.leakage_season
    sub = matches[matches["league_code"].isin(leagues)].reset_index(drop=True)
    sub_stats = stats[stats["fixture_id"].isin(set(sub["fixture_id"]))].reset_index(drop=True)
    market = GOAL_MARKETS[0]
    config = WalkForwardConfig(test_seasons=[season], scope="per_league", inner_seasons=1, select_per="pooled")
    prep = prepare_matches(sub, market, config)
    starts = matchday_starts(prep, [season])
    wanted = pd.Timestamp(args.leakage_cutoff, tz="UTC").value
    cutoff_ns = int(starts[starts >= wanted].min())  # a matchday start: the boundary case
    cutoff = pd.Timestamp(cutoff_ns, tz="UTC")
    future_ids = set(sub.loc[pd.to_datetime(sub["kickoff_utc"], utc=True, format="mixed") >= cutoff, "fixture_id"])
    print(f"leakage check: leagues {leagues}, season {season}, cut-off {cutoff.isoformat()} ({len(sub)} matches)", flush=True)

    def run(frame: pd.DataFrame):
        engine_stats = sub_stats
        if frame is not sub:  # the disturbed copy: scramble the statistics of the future as well
            engine_stats = sub_stats.copy()
            mask = engine_stats["fixture_id"].isin(future_ids).to_numpy()
            columns = [c for c in STATS_COLUMNS if c not in ("fixture_id", "team_id")]
            order = np.random.default_rng(1).permutation(int(mask.sum()))
            for column in columns:
                engine_stats.loc[mask, column] = engine_stats.loc[mask, column].to_numpy()[order]
        engine = PiGbmEngine(frame, engine_stats, settings=settings)
        return evaluate_model(pi_gbm_spec(engine, market, args.grid), frame, market, config)

    started = time.perf_counter()
    report = check_no_lookahead(run, sub, market, cutoff, seed=0)
    seconds = time.perf_counter() - started
    details = [
        {
            "mode": mode.mode,
            "future_outcomes_changed": mode.n_future_rows_changed,
            "forecasts_changed": mode.n_forecasts_changed,
            "max_abs_difference": mode.max_abs_difference,
        }
        for mode in report.modes
    ]
    print(f"leakage check {'PASSED' if report.passed else 'FAILED'} in {seconds:.0f}s: {report.n_compared} forecasts compared, {details}", flush=True)
    return {
        "passed": bool(report.passed),
        "cutoff": cutoff.isoformat(),
        "leagues": leagues,
        "season": season,
        "n_compared": int(report.n_compared),
        "modes": details,
        "seconds": seconds,
    }


# ---------------------------------------------------------------------------
# One market
# ---------------------------------------------------------------------------


def importance_report(engine: PiGbmEngine, market: Market, params: dict, cuts, *, top: int = 15, n_recent: int = 6) -> dict:
    """Feature importances averaged over the boosters of the most recent 28-day buckets.

    ``gain`` is CatBoost's prediction-values-change importance (share of the total, in percent);
    ``permutation`` is the increase of the log-loss when one feature column is shuffled, measured out of
    sample: each booster is scored on the matches of the 28 days after its own cut (features as of each
    match's kickoff minus the latency, so no row was seen in training).
    """
    bank = engine.bank
    names = engine.feature_names
    matrix = engine.matrix
    bucket = bank.settings.bucket_days * 24 * _NS_PER_HOUR
    gains, permutations, rows = [], [], 0
    for cut in sorted((c for c in cuts if bank.usable(c)), reverse=True):
        lo = int(np.searchsorted(matrix.t_ns, cut, side="left"))
        hi = int(np.searchsorted(matrix.t_ns, cut + bucket, side="left"))
        if hi - lo < 1500:
            continue
        booster = bank.model(market, params, cut)
        if booster is None:
            continue
        x, y = matrix.X[lo:hi], bank.labels(market)[lo:hi]

        def loss(z: np.ndarray, booster=booster, y=y) -> float:
            p = np.clip(np.asarray(booster.predict_proba(z), dtype=float), 1e-9, 1.0)
            p = p / p.sum(axis=1, keepdims=True)
            return float(-np.mean(np.log(p[np.arange(len(y)), y])))

        gains.append(gain_importance(booster, names))
        permutations.append(permutation_importance(loss, x, names, n_repeats=2, seed=len(gains)))
        rows += len(y)
        if len(gains) == n_recent:
            break
    if not gains:
        return {}
    gain = pd.concat(gains, axis=1).mean(axis=1).sort_values(ascending=False).head(top)
    permutation = pd.concat(permutations, axis=1).mean(axis=1).sort_values(ascending=False).head(top)
    return {
        "params": params,
        "boosters_averaged": len(gains),
        "rows_scored": int(rows),
        "gain": {name: round(float(value), 3) for name, value in gain.items()},
        "permutation_log_loss_increase": {name: round(float(value), 5) for name, value in permutation.items()},
    }


def run_market(engine: PiGbmEngine, matches: pd.DataFrame, market: Market, args, seasons_needed, *, logit: bool = False) -> dict:
    """One market: fit the boosters (in parallel, cached), run the protocol, save, score against the baseline."""
    started = time.perf_counter()
    config = COMPARISON_CONFIG
    if engine.latency_ns != int(config.result_latency_hours * _NS_PER_HOUR):
        # The engine's as-of cut-off must be the framework's own (first kickoff minus the result latency).
        raise SystemExit("the engine's result latency differs from the protocol's; refusing to run")
    prep = prepare_matches(matches, market, config)
    model_name = "pi_ratings" if logit else args.model_name
    if logit:
        spec = pi_logit_spec(engine, market, name=model_name)
    else:
        combos = grid_combinations(args.grid)
        cuts = engine.required_cuts(matchday_starts(prep, seasons_needed))
        print(f"[{market.name}] {len(cuts)} booster buckets x {len(combos)} parameter sets", flush=True)
        engine.bank.prefetch([market], combos, cuts, n_jobs=args.n_jobs)
        spec = pi_gbm_spec(engine, market, args.grid, name=model_name)
    fit_seconds = time.perf_counter() - started

    evaluation_started = time.perf_counter()
    result = evaluate_model(spec, prep, market, config)
    evaluation_seconds = time.perf_counter() - evaluation_started
    path = save_result(result, args.out_root)
    stored = load_predictions(model_name, market, args.out_root)
    assert len(stored) == len(result.predictions), "stored forecasts must round-trip"

    baseline = evaluate_model(league_frequency_spec(market), prep, market, config)
    table = result.score_table(n_resamples=args.resamples)
    base_table = baseline.score_table(n_resamples=args.resamples)
    versus = compare_to_baseline(result, baseline, n_resamples=args.resamples)
    pooled, base_pooled, diff = table.iloc[-1], base_table.iloc[-1], versus.iloc[-1]
    leagues = versus.iloc[:-1]
    scores = result.scores()
    base_scores = baseline.scores()[["fixture_id", "log_loss"]].rename(columns={"log_loss": "baseline_log_loss"})
    both = scores.merge(base_scores, on="fixture_id").dropna(subset=["log_loss", "baseline_log_loss"])
    by_season = both.groupby("season")[["log_loss", "baseline_log_loss"]].mean().round(4)
    missing = result.predictions[result.predictions[result.probability_columns].isna().any(axis=1)]
    corp = result.corp(score="brier")
    summary = {
        "model": model_name,
        "market": market.name,
        "n_scored": int(pooled["n"]),
        "n_missing": int(pooled["n_missing"]),
        "missing_leagues": {k: int(v) for k, v in missing["league_code"].value_counts().items()},
        "log_loss": float(pooled["log_loss"]),
        "log_loss_ci": [float(pooled["log_loss_lo"]), float(pooled["log_loss_hi"])],
        "brier": float(pooled["brier"]),
        "rps": float(pooled["rps"]) if market.name == "1x2" else None,
        "baseline_log_loss": float(base_pooled["log_loss"]),
        "baseline_n": int(base_pooled["n"]),
        "mean_gain_vs_baseline": float(diff["mean_diff"]),
        "gain_ci": [float(diff["ci_lo"]), float(diff["ci_hi"])],
        "dm_p_value": float(diff["p_value"]),
        "leagues_better_than_baseline": int((leagues["mean_diff"] > 0).sum()),
        "leagues_total": int(len(leagues)),
        "leagues_holm_significant": int((leagues["p_holm"] < 0.05).sum()),
        "by_season": {int(k): {c: float(v) for c, v in row.items()} for k, row in by_season.iterrows()},
        "selected_params": {int(r["outer_season"]): r["params"] for _, r in result.selections.iterrows()},
        "corp_brier": {str(r["class"]): {"mcb": float(r["mcb"]), "dsc": float(r["dsc"]), "unc": float(r["unc"])} for _, r in corp.iterrows()},
        "seconds_fit_boosters": fit_seconds,
        "seconds_evaluation": evaluation_seconds,
        "seconds_total": time.perf_counter() - started,
        "dropped": {k: int(v) for k, v in result.dropped.items()},
        "predictions_file": str(path),
    }
    if not logit:
        last_season = int(result.selections["outer_season"].max())
        chosen = json.loads(result.selections[result.selections["outer_season"] == last_season]["params"].iloc[0])
        summary["importance"] = importance_report(engine, market, chosen, cuts)
    print(
        f"[{model_name}/{market.name}] pooled log-loss {num(summary['log_loss'])} vs baseline {num(summary['baseline_log_loss'])} "
        f"(n={summary['n_scored']}, missing {summary['n_missing']}); gain {num(summary['mean_gain_vs_baseline'], 5)} "
        f"CI [{num(summary['gain_ci'][0], 5)}, {num(summary['gain_ci'][1], 5)}]; "
        f"{summary['leagues_better_than_baseline']}/{summary['leagues_total']} leagues better; "
        f"{summary['seconds_total']:.0f}s (boosters {fit_seconds:.0f}s, evaluation {evaluation_seconds:.0f}s)",
        flush=True,
    )
    return summary


def update_importances(matches, stats, settings, args, summary: dict, summary_path: Path) -> None:
    """Recompute ``importance`` of every finished market from the cached boosters (no fitting)."""
    engine = PiGbmEngine(matches, stats, settings=settings, cache_dir=args.cache_dir)
    seasons_needed = range(min(COMPARISON_CONFIG.test_seasons) - COMPARISON_CONFIG.inner_seasons, max(COMPARISON_CONFIG.test_seasons) + 1)
    by_name = {market.name: market for market in GOAL_MARKETS}
    for name, entry in summary["markets"].items():
        market = by_name[name]
        prep = prepare_matches(matches, market, COMPARISON_CONFIG)
        cuts = engine.required_cuts(matchday_starts(prep, seasons_needed))
        params = json.loads(entry["selected_params"][str(max(COMPARISON_CONFIG.test_seasons))])
        entry["importance"] = importance_report(engine, market, params, cuts)
        print(f"[{name}] importances from {entry['importance'].get('boosters_averaged', 0)} boosters: {list(entry['importance'].get('gain', {}))[:5]}", flush=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markets", nargs="*", default=list(MARKET_ORDER), help="market names in the order to run")
    parser.add_argument("--n-jobs", type=int, default=3, help="worker processes for fitting boosters (at most 3 on the shared machine)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help="fitted boosters (resumable)")
    parser.add_argument("--out-root", type=Path, default=PREDICTIONS_DIR)
    parser.add_argument("--model-name", default="pi_gbm")
    parser.add_argument("--summary", type=Path, default=None, help="JSON summary (default: <cache-dir>/summary.json)")
    parser.add_argument("--resamples", type=int, default=1000, help="bootstrap resamples for confidence intervals")
    parser.add_argument("--skip-leakage", action="store_true")
    parser.add_argument("--skip-pi-ratings", action="store_true", help="do not also evaluate the ratings-only logit (model 'pi_ratings')")
    parser.add_argument("--leakage-only", action="store_true", help="run only the look-ahead test and stop")
    parser.add_argument("--importance-only", action="store_true", help="recompute the feature importances of finished markets from the cached boosters")
    parser.add_argument("--leakage-leagues", default="EPL,BUNDESLIGA,LALIGA")
    parser.add_argument("--leakage-season", type=int, default=2023)
    parser.add_argument("--leakage-cutoff", default="2024-01-20", help="the first matchday start on or after this date is the cut-off")
    args = parser.parse_args()
    args.grid = DEFAULT_GRID
    if args.n_jobs > 3:
        raise SystemExit("at most 3 worker processes on the shared machine")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    summary_path = args.summary or (args.cache_dir / "summary.json")
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    matches = load_history_matches()
    stats = load_team_stats()
    settings = GbmSettings()
    print(f"loaded {len(matches)} matches and {len(stats)} team-match statistic rows in {time.perf_counter() - started:.1f}s", flush=True)
    summary = {"protocol": "goals-protocol-v1", "grid": {k: list(v) for k, v in args.grid.items()}, "markets": {}}
    if summary_path.exists():
        previous = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["markets"].update(previous.get("markets", {}))
        if "pi_ratings" in previous:
            summary["pi_ratings"] = previous["pi_ratings"]
        if "leakage" in previous:
            summary["leakage"] = previous["leakage"]

    if not args.skip_leakage:
        summary["leakage"] = run_leakage_check(matches, stats, args, settings)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        if not summary["leakage"]["passed"]:
            raise SystemExit("look-ahead test failed; not running the protocol")
    if args.leakage_only:
        return
    if args.importance_only:
        update_importances(matches, stats, settings, args, summary, summary_path)
        return

    engine_started = time.perf_counter()
    engine = PiGbmEngine(matches, stats, settings=settings, cache_dir=args.cache_dir)
    print(f"engine ready in {time.perf_counter() - engine_started:.1f}s ({engine.matrix.X.shape[0]} training rows, {engine.matrix.X.shape[1]} features)", flush=True)
    seasons_needed = range(min(COMPARISON_CONFIG.test_seasons) - COMPARISON_CONFIG.inner_seasons, max(COMPARISON_CONFIG.test_seasons) + 1)
    by_name = {market.name: market for market in GOAL_MARKETS}
    for name in args.markets:
        summary["markets"][name] = run_market(engine, matches, by_name[name], args, seasons_needed)
        summary["seconds_total"] = time.perf_counter() - started
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if not args.skip_pi_ratings:
        logit_engine = PiGbmEngine(matches, stats, settings=settings, kind="logit")
        summary.setdefault("pi_ratings", {})
        for name in args.markets:
            summary["pi_ratings"][name] = run_market(logit_engine, matches, by_name[name], args, seasons_needed, logit=True)
            summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    summary["seconds_total"] = time.perf_counter() - started
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"done in {time.perf_counter() - started:.0f}s; summary: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
