"""Dixon-Coles and NB2 under the fixed goal-comparison protocol (all goal markets, all leagues).

Offline: reads the API-Football ``matches`` table read-only and writes only
``predictions/dixon_coles.sqlite`` and ``predictions/nb2.sqlite`` (via
``model_buk.evaluation.protocol.save_result``) plus a fit cache under
``app/runtime`` (git-ignored).  The protocol itself is not touched:
``COMPARISON_CONFIG`` (outer test seasons 2022-2025, one model per league refit
before every matchday, half-life chosen on the season before each test season,
pooled across leagues) and the five ``GOAL_MARKETS``.

Stages (run one, or ``all`` = warm + evaluate + leakage):

- ``pilot``    one-off estimation of the two constants of the models from seasons up to 2021 only
               (the newcomer default strength from seasons <= 2020, the ridge strength on the
               season 2021).  Prints the values that are set in ``goal_adapters.py``.
- ``warm``     fits every (model, league, half-life, matchday) needed by the inner folds in worker
               processes (at most ``--jobs``); resumable, one file per model and league.
- ``evaluate`` sequential, in one process: for every model and market runs the framework's nested
               walk-forward (all fits are cache hits or computed on demand), saves the forecasts and
               prints pooled log-loss against the league-frequency baseline with confidence intervals,
               the selected half-lives and the run time.
- ``leakage``  the framework's look-ahead test (shuffle and mirror the outcomes of the future) on
               chosen leagues, with the fit cache cleared inside every run so a cache hit cannot hide
               a leak.

Usage (from the app/ directory)::

    .venv\\Scripts\\python scripts\\run_models_dc_nb2.py --stage all --jobs 3 > runtime\\dc_nb2.log
    .venv\\Scripts\\python scripts\\run_models_dc_nb2.py --stage evaluate --models nb2 --markets 1x2
"""

from __future__ import annotations

import os

# The fits are small; extra BLAS threads only fight the other jobs for cores.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation import protocol  # noqa: E402
from model_buk.evaluation.framework import (  # noqa: E402
    MARKET_1X2,
    Market,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
    league_frequency_spec,
    prepare_matches,
)
from model_buk.evaluation.protocol import (  # noqa: E402
    COMPARISON_CONFIG,
    GOAL_MARKETS,
    load_history_matches,
    save_result,
)
from model_buk.models import goal_adapters as ga  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = APP_DIR / "runtime" / "goal_fit_cache"
DEFAULT_SUMMARY = APP_DIR / "runtime" / "dc_nb2_summary.json"
MODELS = ("dixon_coles", "nb2")
# 1X2 and totals 2.5 first: they have historical odds and matter most if time runs out.
MARKET_ORDER = ("1x2", "over_under_2.5", "over_under_1.5", "over_under_3.5", "btts")
INNER_SEASONS_TO_WARM = (2021, 2022, 2023, 2024)  # inner folds of the outer seasons 2022-2025
# A cutoff in the middle of a season of the league, so that whole test seasons precede it and the
# shuffled future still contains matchdays of the same season.
LEAKAGE_CUTOFFS = {"EPL": "2024-03-01", "ELITESERIEN": "2024-07-01"}


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def markets_by_name(names: list[str]) -> list[Market]:
    known = {market.name: market for market in GOAL_MARKETS}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SystemExit(f"unknown market(s) {unknown}; choose from {list(known)}")
    return [known[name] for name in MARKET_ORDER if name in names]


# ---------------------------------------------------------------------------
# pilot: constants estimated on seasons that precede every test season
# ---------------------------------------------------------------------------


def _pilot_newcomers(league: str, frame: pd.DataFrame, through_season: int) -> dict:
    train = frame[(frame["status_short"] == "FT") & (frame["season"] <= through_season)].copy()
    if train.empty:
        return {"league": league, "n": 0}
    train["kickoff"] = pd.to_datetime(train["kickoff_utc"], utc=True, format="mixed").dt.as_unit("ns")
    train = train.sort_values(["kickoff", "fixture_id"])
    fit = ga.fit_goal_model("dixon_coles", train, half_life_days=730, cache=None)
    n = fit.n_newcomer_teams
    if n == 0:
        return {"league": league, "n": 0}
    k = ga.NEWCOMER_PRIOR_WEIGHT
    raw_attack = (fit.newcomer_attack * (n + k) - k * ga.NEWCOMER_PRIOR_ATTACK) / n
    raw_defence = (fit.newcomer_defence * (n + k) - k * ga.NEWCOMER_PRIOR_DEFENCE) / n
    return {"league": league, "n": n, "attack": raw_attack, "defence": raw_defence}


def _pilot_ridge(league: str, frame: pd.DataFrame, half_lives: list[float], l2_values: list[float], season: int) -> list[dict]:
    """Walk-forward the season ``season`` of one league (only data up to it is passed) for each (half-life, ridge)."""
    visible = frame[frame["season"] <= season]
    config = replace(COMPARISON_CONFIG, test_seasons=(season,))
    rows = []
    for half_life in half_lives:
        for l2 in l2_values:
            cache = ga.FitCache()
            spec = ga.dixon_coles_spec(MARKET_1X2, half_lives=[half_life], l2_strength=l2, cache=cache)
            result = evaluate_model(spec, visible, MARKET_1X2, config)
            scores = result.scores()["log_loss"].dropna()
            rows.append({"league": league, "half_life": half_life, "l2": l2, "loss_sum": float(scores.sum()), "n": int(len(scores))})
    return rows


def stage_pilot(matches: pd.DataFrame, args: argparse.Namespace) -> None:
    from joblib import Parallel, delayed

    say("pilot 1/2: newcomer default strength from seasons <= 2020")
    groups = [(league, frame) for league, frame in matches.groupby("league_code")]
    outcomes = Parallel(n_jobs=args.jobs)(delayed(_pilot_newcomers)(league, frame, 2020) for league, frame in groups)
    table = pd.DataFrame(outcomes)
    used = table[table["n"] > 0]
    total = used["n"].sum()
    attack = float((used["attack"] * used["n"]).sum() / total)
    defence = float((used["defence"] * used["n"]).sum() / total)
    say(f"  {int(total)} newcomer team-seasons in {len(used)} leagues: attack {attack:+.4f}, defence {defence:+.4f}")
    say(f"  per league (n, attack, defence): " + "; ".join(f"{r.league} {int(r.n)} {r.attack:+.2f} {r.defence:+.2f}" for r in used.itertuples()))

    say("pilot 2/2: ridge strength on the season 2021 (data up to 2021 only), Dixon-Coles 1X2")
    half_lives = [365.0, 1200.0]
    l2_values = [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    started = time.perf_counter()
    rows = Parallel(n_jobs=args.jobs)(delayed(_pilot_ridge)(league, frame, half_lives, l2_values, 2021) for league, frame in groups)
    frame = pd.DataFrame([row for part in rows for row in part])
    pooled = frame.groupby(["half_life", "l2"]).agg(loss_sum=("loss_sum", "sum"), n=("n", "sum"))
    pooled["log_loss"] = pooled["loss_sum"] / pooled["n"]
    say(f"  ({time.perf_counter() - started:.0f}s)")
    print(pooled[["n", "log_loss"]].to_string(), flush=True)
    both = pooled.groupby("l2")["loss_sum"].sum() / pooled.groupby("l2")["n"].sum()
    say(f"  best ridge over both half-lives: {both.idxmin()} (log-loss {both.min():.5f})")


# ---------------------------------------------------------------------------
# warm: parallel fits of the inner folds
# ---------------------------------------------------------------------------


def stage_warm(matches: pd.DataFrame, args: argparse.Namespace) -> None:
    started = time.perf_counter()
    finished = ga.warm_fit_cache(
        matches,
        args.models,
        args.cache_dir,
        config=COMPARISON_CONFIG,
        seasons=INNER_SEASONS_TO_WARM,
        n_jobs=args.jobs,
        leagues=args.leagues or None,
        log=say,
    )
    ran = [row for row in finished if not row.get("skipped")]
    say(f"warm done: {len(ran)} jobs run, {sum(r.get('fits', 0) for r in ran)} fits, {time.perf_counter() - started:.0f}s wall clock")


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def _half_life_of(params: str) -> float:
    return float(json.loads(params)["half_life_days"])


def unseen_team_fixtures(prep) -> set:
    """Fixture ids in which a team plays that had no earlier match in the league's data (the newcomer prior is used).

    A team is unseen on a matchday when its first match in the data is not before that matchday's start.
    """
    frame = prep.frame
    long = pd.concat(
        [
            pd.DataFrame({"league": prep.league, "team": frame["home_team_id"].to_numpy(), "t": prep.t}),
            pd.DataFrame({"league": prep.league, "team": frame["away_team_id"].to_numpy(), "t": prep.t}),
        ]
    )
    first = long.groupby(["league", "team"])["t"].min()
    keyed = pd.DataFrame({"league": prep.league, "fixture_id": frame["fixture_id"].to_numpy()})
    for side in ("home", "away"):
        keyed[side] = first.reindex(pd.MultiIndex.from_arrays([prep.league, frame[f"{side}_team_id"].to_numpy()])).to_numpy()
    return keyed


def newcomer_diagnostics(result, baseline, keyed: pd.DataFrame) -> dict:
    """Share of test matches that use the newcomer prior and the model's and baseline's log-loss on them."""
    from model_buk.evaluation.scoring import log_loss_per_match

    columns = result.probability_columns
    predictions = result.predictions.merge(keyed[["fixture_id", "home", "away"]], on="fixture_id", how="left")
    unseen = ((predictions["home"] >= predictions["group_start_ns"]) | (predictions["away"] >= predictions["group_start_ns"])).to_numpy()
    have = predictions[columns].notna().all(axis=1).to_numpy()
    model_loss = log_loss_per_match(predictions[columns].to_numpy(dtype=float), predictions["y"].to_numpy(dtype=np.int64))
    base = baseline.predictions[["fixture_id", *columns]].rename(columns=dict(zip(columns, [f"b_{c}" for c in columns])))
    joined = predictions[["fixture_id", "y"]].merge(base, on="fixture_id", how="left")
    base_loss = log_loss_per_match(joined[[f"b_{c}" for c in columns]].to_numpy(dtype=float), joined["y"].to_numpy(dtype=np.int64))
    both = unseen & have & np.isfinite(base_loss)
    rest = ~unseen & have & np.isfinite(base_loss)
    return {
        "share_unseen_team": float(unseen[have].mean()) if have.any() else float("nan"),
        "n_unseen_team": int(both.sum()),
        "log_loss_unseen_team": float(model_loss[both].mean()) if both.any() else float("nan"),
        "baseline_log_loss_unseen_team": float(base_loss[both].mean()) if both.any() else float("nan"),
        "log_loss_seen_teams": float(model_loss[rest].mean()) if rest.any() else float("nan"),
        "baseline_log_loss_seen_teams": float(base_loss[rest].mean()) if rest.any() else float("nan"),
    }


def stage_evaluate(matches: pd.DataFrame, args: argparse.Namespace) -> dict:
    ga.FIT_CACHE.clear()
    loaded = ga.load_fit_cache(args.cache_dir) if Path(args.cache_dir).exists() else 0
    say(f"fit cache: {loaded} fits loaded from {args.cache_dir}")
    config = COMPARISON_CONFIG
    summary: dict = {"protocol_id": protocol.PROTOCOL_ID, "models": {}}
    all_leagues = sorted(matches["league_code"].unique())
    markets = markets_by_name(args.markets)
    baselines = {}
    prepared = {}
    keyed = {}
    for market in markets:
        prepared[market.name] = prepare_matches(matches, market, config)
        keyed[market.name] = unseen_team_fixtures(prepared[market.name])
        baselines[market.name] = evaluate_model(league_frequency_spec(market), prepared[market.name], market, config)
    started_all = time.perf_counter()
    for model in args.models:
        builder = ga.SPEC_BUILDERS[model]
        summary["models"][model] = {}
        for market in markets:
            before = ga.FIT_CACHE.stats()
            started = time.perf_counter()
            result = evaluate_model(builder(market), prepared[market.name], market, config)
            elapsed = time.perf_counter() - started
            path = save_result(result, args.predictions_dir)
            after = ga.FIT_CACHE.stats()
            baseline = baselines[market.name]
            table = result.score_table(n_resamples=args.resamples)
            base_table = baseline.score_table(n_resamples=args.resamples)
            versus = compare_to_baseline(result, baseline, n_resamples=args.resamples)
            pooled = table.iloc[-1]
            base_pooled = base_table.iloc[-1]
            versus_pooled = versus.iloc[-1]
            league_rows = versus.iloc[:-1]
            selected = {
                int(row["outer_season"]): _half_life_of(row["params"])
                for _, row in result.selections.iterrows()
                if row["league"] == "ALL"
            }
            leagues_with_forecasts = sorted(result.predictions.loc[result.predictions[result.probability_columns[0]].notna(), "league_code"].unique())
            missing_leagues = [name for name in all_leagues if name not in leagues_with_forecasts]
            entry = {
                "n": int(pooled["n"]),
                "n_missing": int(pooled["n_missing"]),
                "log_loss": float(pooled["log_loss"]),
                "log_loss_ci": [float(pooled["log_loss_lo"]), float(pooled["log_loss_hi"])],
                "baseline_log_loss": float(base_pooled["log_loss"]),
                "baseline_n": int(base_pooled["n"]),
                "improvement": float(versus_pooled["mean_diff"]),
                "improvement_ci": [float(versus_pooled["ci_lo"]), float(versus_pooled["ci_hi"])],
                "dm_p": float(versus_pooled["p_value"]),
                "leagues_better": int((league_rows["mean_diff"] > 0).sum()),
                "leagues_better_holm": int(((league_rows["mean_diff"] > 0) & (league_rows["p_holm"] < 0.05)).sum()),
                "leagues_worse_holm": int(((league_rows["mean_diff"] < 0) & (league_rows["p_holm"] < 0.05)).sum()),
                "leagues_compared": int(len(league_rows)),
                "selected_half_life": selected,
                "seconds": elapsed,
                "cache_hits": after["hits"] - before["hits"],
                "cache_misses": after["misses"] - before["misses"],
                "fit_failures": after["failures"] - before["failures"],
                "missing_leagues": missing_leagues,
                "newcomer": newcomer_diagnostics(result, baseline, keyed[market.name]),
                "file": str(path),
            }
            summary["models"][model][market.name] = entry
            say(
                f"{model:11s} {market.name:15s} n={entry['n']:6d} log-loss {entry['log_loss']:.4f} "
                f"[{entry['log_loss_ci'][0]:.4f}, {entry['log_loss_ci'][1]:.4f}] vs baseline {entry['baseline_log_loss']:.4f} "
                f"(gain {entry['improvement']:+.4f} [{entry['improvement_ci'][0]:+.4f}, {entry['improvement_ci'][1]:+.4f}]) "
                f"half-lives {selected} {elapsed:.0f}s hits {entry['cache_hits']} misses {entry['cache_misses']} "
                f"fail {entry['fit_failures']} leagues better {entry['leagues_better']}/{entry['leagues_compared']} "
                f"(Holm {entry['leagues_better_holm']}) worse-Holm {entry['leagues_worse_holm']} missing {missing_leagues}"
            )
            say(f"    unseen-team matches: {json.dumps(entry['newcomer'], default=float)}")
            # Keep partial results: the summary is rewritten after every market.
            Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
            Path(args.summary).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    summary["evaluate_seconds"] = time.perf_counter() - started_all
    say(f"evaluate done in {summary['evaluate_seconds']:.0f}s; cache {ga.FIT_CACHE.stats()}")
    Path(args.summary).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# leakage
# ---------------------------------------------------------------------------


def stage_leakage(matches: pd.DataFrame, args: argparse.Namespace) -> list[dict]:
    reports = []
    for league in args.leakage_leagues:
        frame = matches[matches["league_code"] == league].reset_index(drop=True)
        if frame.empty:
            say(f"leakage: no matches for {league}")
            continue
        for model in args.models:
            for market in markets_by_name(args.leakage_markets):
                spec = ga.SPEC_BUILDERS[model](market)

                def run(data: pd.DataFrame, spec=spec, market=market):
                    # An independent run: no fit or warm start may leak from one run to the next.
                    ga.FIT_CACHE.clear()
                    return evaluate_model(spec, data, market, COMPARISON_CONFIG)

                started = time.perf_counter()
                cutoff = args.leakage_cutoff or LEAKAGE_CUTOFFS.get(league, "2024-03-01")
                report = check_no_lookahead(run, frame, market, cutoff)
                say(
                    f"leakage {model} {league} {market.name}: {'PASSED' if report.passed else 'FAILED'} "
                    f"({report.n_compared} forecasts compared, "
                    + ", ".join(
                        f"{m.mode}: {m.n_future_rows_changed} future outcomes changed, {m.n_forecasts_changed} forecasts changed, max diff {m.max_abs_difference:.2e}"
                        for m in report.modes
                    )
                    + f") {time.perf_counter() - started:.0f}s"
                )
                reports.append(
                    {
                        "model": model,
                        "league": league,
                        "market": market.name,
                        "passed": bool(report.passed),
                        "n_compared": int(report.n_compared),
                        "modes": [
                            {
                                "mode": m.mode,
                                "future_outcomes_changed": int(m.n_future_rows_changed),
                                "forecasts_changed": int(m.n_forecasts_changed),
                                "max_abs_difference": float(m.max_abs_difference),
                            }
                            for m in report.modes
                        ],
                    }
                )
    ga.FIT_CACHE.clear()
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["pilot", "warm", "evaluate", "leakage", "all"], default="all")
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--markets", nargs="+", default=[m.name for m in GOAL_MARKETS])
    parser.add_argument("--leagues", nargs="*", default=[], help="Restrict the warm stage to these league codes")
    parser.add_argument("--jobs", type=int, default=3, help="Worker processes for pilot and warm (at most 3 on the shared machine)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--predictions-dir", type=Path, default=protocol.PREDICTIONS_DIR)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--resamples", type=int, default=1000, help="Bootstrap resamples for the confidence intervals")
    parser.add_argument("--leakage-leagues", nargs="+", default=["EPL", "ELITESERIEN"])
    parser.add_argument("--leakage-markets", nargs="+", default=["1x2", "over_under_2.5"])
    parser.add_argument("--leakage-cutoff", default=None, help="Override the per-league default cutoff")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 3:
        raise SystemExit("--jobs must be between 1 and 3")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.perf_counter()
    matches = load_history_matches()
    say(f"loaded {len(matches)} matches, {matches['league_code'].nunique()} leagues")
    if args.stage == "pilot":
        stage_pilot(matches, args)
        return
    if args.stage in ("warm", "all"):
        stage_warm(matches, args)
    summary = None
    if args.stage in ("evaluate", "all"):
        summary = stage_evaluate(matches, args)
    if args.stage in ("leakage", "all"):
        reports = stage_leakage(matches, args)
        if summary is None and Path(args.summary).exists():
            summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))  # add to the last evaluation summary
        if summary is not None:
            summary["leakage"] = reports
            Path(args.summary).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        if not all(report["passed"] for report in reports):
            raise SystemExit("leakage test failed")
    say(f"total {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
