"""Run the corner / card / shot count models under the fixed comparison protocol.

Offline only: reads ``history.sqlite`` read-only and writes forecasts to
``Model-Buk-Data/predictions/count_*.sqlite`` plus the evaluation tables to
``count_evaluation.sqlite``.  No API calls, and no odds are used for fitting.

Per statistic (corners, yellow cards, shots, shots on target; booking points on
request) and per model kind (Poisson and NB2) it evaluates every over/under line
of :mod:`model_buk.evaluation.count_markets` with the goal protocol's config
(outer test seasons 2022-2025, one model per league refit before every matchday,
hyper-parameters chosen on the season before each test season) against the league
frequency baseline of the same line, then reports per league and pooled: log-loss
with matchday-bootstrap intervals, the paired difference to the baseline
(Diebold-Mariano, Holm and Benjamini-Hochberg across leagues), the CORP
``MCB - DSC + UNC`` decomposition and the reliability curve.

Work is split by (statistic, kind) across at most three worker processes, one
process per task, so a fit serves all lines of its statistic.  Every finished
market is written to ``<root>/count_partial`` right away and skipped on a rerun
(delete a file there to recompute that market).

Usage (from the app/ directory)::

    .venv/Scripts/python scripts/run_count_models.py run                      # the protocol run (about 30 minutes, 3 workers)
    .venv/Scripts/python scripts/run_count_models.py run --families corners --kinds nb2 --workers 1
    .venv/Scripts/python scripts/run_count_models.py recent                   # compare with the 365-day league frequency
    .venv/Scripts/python scripts/run_count_models.py ablation                 # feature ablation on 8 large leagues
    .venv/Scripts/python scripts/run_count_models.py report                   # Polish report in reports/
"""

from __future__ import annotations

import os

# One BLAS thread per process: the workers are the parallelism (the machine is shared).
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import pickle  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, as_completed  # noqa: E402
from contextlib import closing  # noqa: E402
from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation.count_markets import (  # noqa: E402
    DEFAULT_FAMILIES,
    FAMILIES,
    apply_coverage_rule,
    count_config,
    count_markets,
    leakage_market,
    load_count_matches,
)
from model_buk.evaluation.framework import (  # noqa: E402
    Market,
    WalkForwardConfig,
    WalkForwardResult,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
)
from model_buk.evaluation.protocol import PREDICTIONS_DIR, PROTOCOL_ID, save_result  # noqa: E402
from model_buk.evaluation.reliability import corp_diagram_table, corp_reliability  # noqa: E402
from model_buk.models.count_models import (  # noqa: E402
    DEFAULT_GRID,
    CountEngine,
    FeatureConfig,
    count_baseline_spec,
    count_recent_baseline_spec,
    count_spec,
)

MODEL_KINDS = ("poisson", "nb2")
EVALUATION_DB = "count_evaluation.sqlite"
LEAKAGE_LEAGUES = ("EPL", "LALIGA", "EREDIVISIE", "ELITESERIEN")
LEAKAGE_CUTOFF = "2024-03-01"


# ---------------------------------------------------------------------------
# Summaries of one market (worker side)
# ---------------------------------------------------------------------------


def _market_parts(market: Market) -> tuple[str, str, float]:
    family, scope, line = market.name.rsplit("_", 2)
    return family, scope, float(line)


def _corp_row(result: WalkForwardResult, league: str | None, score: str, n_sim: int, seed: int) -> dict[str, float]:
    row = result.corp(league=league, score=score, n_sim=n_sim, seed=seed).iloc[0]
    return {
        "n": int(row["n"]),
        "base_rate": float(row["base_rate"]),
        "score": float(row["score"]),
        "mcb": float(row["mcb"]),
        "dsc": float(row["dsc"]),
        "unc": float(row["unc"]),
        "mcb_p": float(row["mcb_pvalue"]),
    }


def summarize_market(
    result: WalkForwardResult,
    baseline: WalkForwardResult,
    *,
    n_resamples: int = 2000,
    n_sim: int = 200,
    seed: int = 0,
) -> dict[str, pd.DataFrame]:
    """Tables for one market and one model: pooled, per league, per season, reliability, selections."""
    market = result.market
    family, scope, line = _market_parts(market)
    ident = {"market": market.name, "family": family, "scope": scope, "line": line, "model": result.model}

    versus = compare_to_baseline(result, baseline, n_resamples=n_resamples, seed=seed)
    model_scores = result.scores(["log_loss"])
    base_scores = baseline.scores(["log_loss"])[["fixture_id", "log_loss"]].rename(columns={"log_loss": "ll_base"})
    both = (
        model_scores.rename(columns={"log_loss": "ll_model"})
        .merge(base_scores, on="fixture_id", how="inner")
        .dropna(subset=["ll_model", "ll_base"])
    )
    probability = result.predictions[["fixture_id", "league_code", "p_over", "y"]].dropna(subset=["p_over"])
    both = both.merge(probability[["fixture_id", "p_over", "y"]], on="fixture_id", how="left")
    league_mean = both.groupby("league_code")[["ll_model", "ll_base", "p_over", "y"]].mean()

    league_rows = []
    for _, row in versus.iterrows():
        code = row["league_code"]
        if code == "ALL":
            continue
        record = {
            **ident,
            "league_code": code,
            "n": int(row["n"]),
            "n_clusters": int(row["n_clusters"]),
            "ll_model": float(league_mean.loc[code, "ll_model"]),
            "ll_base": float(league_mean.loc[code, "ll_base"]),
            "mean_p_over": float(league_mean.loc[code, "p_over"]),
            "freq_over": float(league_mean.loc[code, "y"]),
            "diff": float(row["mean_diff"]),
            "diff_lo": float(row["ci_lo"]),
            "diff_hi": float(row["ci_hi"]),
            "dm_stat": float(row["dm_stat"]),
            "p_value": float(row["p_value"]),
            "p_holm": float(row["p_holm"]),
            "p_bh": float(row["p_bh"]),
        }
        model_corp = _corp_row(result, code, "brier", 0, seed)
        base_corp = _corp_row(baseline, code, "brier", 0, seed)
        record.update(
            mcb_model=model_corp["mcb"], dsc_model=model_corp["dsc"], mcb_base=base_corp["mcb"], dsc_base=base_corp["dsc"], unc=model_corp["unc"]
        )
        league_rows.append(record)
    leagues = pd.DataFrame(league_rows)

    pooled_row = versus[versus["league_code"] == "ALL"].iloc[0]
    table = result.score_table(n_resamples=n_resamples, seed=seed)
    base_table = baseline.score_table(n_resamples=n_resamples, seed=seed)
    pooled_scores = table[table["league_code"] == "ALL"].iloc[0]
    base_pooled = base_table[base_table["league_code"] == "ALL"].iloc[0]
    corp_model = _corp_row(result, None, "brier", n_sim, seed)
    corp_base = _corp_row(baseline, None, "brier", n_sim, seed)
    corp_model_log = _corp_row(result, None, "log_loss", 0, seed)
    corp_base_log = _corp_row(baseline, None, "log_loss", 0, seed)
    league_only = leagues if len(leagues) else pd.DataFrame(columns=["diff", "p_value", "p_holm", "p_bh"])
    pooled = pd.DataFrame(
        [
            {
                **ident,
                "n": int(pooled_row["n"]),
                "n_missing": int(pooled_scores["n_missing"]),
                "n_leagues": int(len(leagues)),
                "ll_model": float(pooled_scores["log_loss"]),
                "ll_model_lo": float(pooled_scores["log_loss_lo"]),
                "ll_model_hi": float(pooled_scores["log_loss_hi"]),
                "ll_base": float(base_pooled["log_loss"]),
                "ll_base_lo": float(base_pooled["log_loss_lo"]),
                "ll_base_hi": float(base_pooled["log_loss_hi"]),
                "diff": float(pooled_row["mean_diff"]),
                "diff_lo": float(pooled_row["ci_lo"]),
                "diff_hi": float(pooled_row["ci_hi"]),
                "dm_stat": float(pooled_row["dm_stat"]),
                "p_value": float(pooled_row["p_value"]),
                "leagues_better": int((league_only["diff"] > 0).sum()),
                "leagues_p05": int((league_only["p_value"] < 0.05).sum()),
                "leagues_better_holm": int(((league_only["diff"] > 0) & (league_only["p_holm"] < 0.05)).sum()),
                "leagues_worse_holm": int(((league_only["diff"] < 0) & (league_only["p_holm"] < 0.05)).sum()),
                "leagues_better_bh": int(((league_only["diff"] > 0) & (league_only["p_bh"] < 0.05)).sum()),
                "leagues_worse_bh": int(((league_only["diff"] < 0) & (league_only["p_bh"] < 0.05)).sum()),
                "freq_over": corp_model["base_rate"],
                "mean_p_over": float(probability["p_over"].mean()),
                "brier_model": corp_model["score"],
                "mcb_model": corp_model["mcb"],
                "dsc_model": corp_model["dsc"],
                "mcb_p_model": corp_model["mcb_p"],
                "brier_base": corp_base["score"],
                "mcb_base": corp_base["mcb"],
                "dsc_base": corp_base["dsc"],
                "mcb_p_base": corp_base["mcb_p"],
                "unc": corp_model["unc"],
                "mcb_log_model": corp_model_log["mcb"],
                "dsc_log_model": corp_model_log["dsc"],
                "mcb_log_base": corp_base_log["mcb"],
                "dsc_log_base": corp_base_log["dsc"],
                "elapsed_seconds": float(result.elapsed_seconds),
            }
        ]
    )

    scored = probability.dropna()
    curve = corp_diagram_table(corp_reliability(scored["p_over"].to_numpy(), scored["y"].to_numpy()), n_points=10)
    reliability = pd.DataFrame({**{k: [v] * len(curve) for k, v in ident.items()}, **curve.to_dict("list")})

    by_season_rows = []
    for season, frame in both.groupby("season"):
        by_season_rows.append(
            {**ident, "season": int(season), "n": len(frame), "ll_model": float(frame["ll_model"].mean()), "ll_base": float(frame["ll_base"].mean())}
        )
    selections = result.selections.copy()
    selections.insert(0, "market", market.name)
    selections.insert(1, "model", result.model)
    return {
        "pooled": pooled,
        "by_league": leagues,
        "reliability": reliability,
        "by_season": pd.DataFrame(by_season_rows),
        "selections": selections,
    }


# ---------------------------------------------------------------------------
# Worker tasks
# ---------------------------------------------------------------------------


def _log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def run_family_task(task: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every line of one statistic for one model kind; skip markets that are already done."""
    family, kind = task["family"], task["kind"]
    partial = Path(task["partial_dir"])
    log = partial / "logs" / f"{family}__{kind}.log"
    matches = pd.read_pickle(partial / "matches.pkl")
    if task.get("leagues"):
        matches = matches[matches["league_code"].isin(task["leagues"])]
    config = count_config()
    if task.get("test_seasons"):
        config = replace(config, test_seasons=tuple(task["test_seasons"]))
    engine = CountEngine(family, kind)
    grid = task.get("grid") or dict(DEFAULT_GRID)
    started = time.perf_counter()
    done = 0
    for market in count_markets(family):
        target = partial / f"{family}__{kind}__{market.name}.pkl"
        if target.exists():
            done += 1
            continue
        _, scope, line = _market_parts(market)
        tick = time.perf_counter()
        baseline = evaluate_model(count_baseline_spec(market), matches, market, config)
        result = evaluate_model(count_spec(engine, scope, line, grid=grid), matches, market, config)
        tables = summarize_market(result, baseline, n_resamples=task["resamples"], n_sim=task["corp_sims"])
        payload = {"result": result, "baseline": baseline, "tables": tables, "fits": engine.n_fits, "hits": engine.n_hits}
        temporary = target.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        temporary.replace(target)
        done += 1
        _log(
            log,
            f"{market.name}: {time.perf_counter() - tick:.0f}s, fits {engine.n_fits}, cache hits {engine.n_hits}, "
            f"pooled log-loss model {tables['pooled']['ll_model'].iloc[0]:.4f} vs baseline {tables['pooled']['ll_base'].iloc[0]:.4f}",
        )
    return {"family": family, "kind": kind, "markets": done, "seconds": time.perf_counter() - started}


def run_leakage_task(task: dict[str, Any]) -> dict[str, Any]:
    """Look-ahead test on real data: shuffle every statistic of the future, earlier forecasts must not move."""
    family = task["family"]
    partial = Path(task["partial_dir"])
    matches = pd.read_pickle(partial / "matches.pkl")
    leagues = [league for league in LEAKAGE_LEAGUES if league in set(matches["league_code"])]
    matches = matches[matches["league_code"].isin(leagues)]
    config = count_config()
    fam = FAMILIES[family]
    line = fam.total_lines[len(fam.total_lines) // 2]
    market = [m for m in count_markets(fam) if m.name == f"{family}_total_{line}"][0]
    grid = task.get("grid") or dict(DEFAULT_GRID)
    started = time.perf_counter()

    def run(frame: pd.DataFrame) -> WalkForwardResult:
        return evaluate_model(count_spec(CountEngine(family, "nb2"), "total", line, grid=grid), frame, market, config)

    report = check_no_lookahead(run, matches, leakage_market(fam), LEAKAGE_CUTOFF, modes=("shuffle",))
    out = {
        "family": family,
        "leagues": leagues,
        "cutoff": LEAKAGE_CUTOFF,
        "passed": bool(report.passed),
        "n_compared": int(report.n_compared),
        "n_forecasts_changed": int(report.n_forecasts_changed),
        "max_abs_difference": float(report.max_abs_difference),
        "n_future_rows_changed": int(report.modes[0].n_future_rows_changed),
        "seconds": time.perf_counter() - started,
    }
    (partial / f"leakage__{family}.json").write_text(json.dumps(out), encoding="utf-8")
    return out


def _dispatch(task: dict[str, Any]) -> dict[str, Any]:
    try:
        return run_leakage_task(task) if task["kind"] == "leakage" else run_family_task(task)
    except Exception:  # keep the traceback: a bare exception from a worker hides where it happened
        raise RuntimeError(traceback.format_exc()) from None


# ---------------------------------------------------------------------------
# Drift-aware benchmark: league frequency over the last 365 days
# ---------------------------------------------------------------------------


def _versus_rows(
    result: WalkForwardResult, benchmark: WalkForwardResult, *, n_resamples: int, seed: int = 0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pooled and per-league paired comparison of a model with a benchmark (log-loss)."""
    family, scope, line = _market_parts(result.market)
    ident = {"market": result.market.name, "family": family, "scope": scope, "line": line, "model": result.model}
    versus = compare_to_baseline(result, benchmark, n_resamples=n_resamples, seed=seed)
    both = (
        result.scores(["log_loss"])[["fixture_id", "league_code", "log_loss"]]
        .merge(benchmark.scores(["log_loss"])[["fixture_id", "log_loss"]], on="fixture_id", suffixes=("_model", "_bench"))
        .dropna()
    )
    leagues = versus[versus["league_code"] != "ALL"]
    total = versus[versus["league_code"] == "ALL"].iloc[0]
    pooled = {
        **ident,
        "n": int(total["n"]),
        "ll_model": float(both["log_loss_model"].mean()),
        "ll_bench": float(both["log_loss_bench"].mean()),
        "diff": float(total["mean_diff"]),
        "diff_lo": float(total["ci_lo"]),
        "diff_hi": float(total["ci_hi"]),
        "p_value": float(total["p_value"]),
        "n_leagues": int(len(leagues)),
        "leagues_better": int((leagues["mean_diff"] > 0).sum()),
        "leagues_better_holm": int(((leagues["mean_diff"] > 0) & (leagues["p_holm"] < 0.05)).sum()),
        "leagues_worse_holm": int(((leagues["mean_diff"] < 0) & (leagues["p_holm"] < 0.05)).sum()),
        "leagues_better_bh": int(((leagues["mean_diff"] > 0) & (leagues["p_bh"] < 0.05)).sum()),
        "leagues_worse_bh": int(((leagues["mean_diff"] < 0) & (leagues["p_bh"] < 0.05)).sum()),
    }
    league_rows = [
        {
            **ident,
            "league_code": row["league_code"],
            "n": int(row["n"]),
            "diff": float(row["mean_diff"]),
            "diff_lo": float(row["ci_lo"]),
            "diff_hi": float(row["ci_hi"]),
            "p_value": float(row["p_value"]),
            "p_holm": float(row["p_holm"]),
            "p_bh": float(row["p_bh"]),
        }
        for _, row in leagues.iterrows()
    ]
    return pooled, league_rows


def run_recent_task(task: dict[str, Any]) -> dict[str, Any]:
    """Compare the saved forecasts of every line of one statistic with the 365-day league frequency."""
    family = task["family"]
    partial = Path(task["partial_dir"])
    matches = pd.read_pickle(partial / "matches.pkl")
    config = count_config()
    pooled_rows: list[dict[str, Any]] = []
    league_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for market in count_markets(family):
        benchmark = evaluate_model(count_recent_baseline_spec(market), matches, market, config)
        with (partial / f"recent__{family}__{market.name}.pkl").open("wb") as handle:
            pickle.dump(benchmark, handle, protocol=pickle.HIGHEST_PROTOCOL)
        for kind in MODEL_KINDS:
            path = partial / f"{family}__{kind}__{market.name}.pkl"
            if not path.exists():
                continue
            with path.open("rb") as handle:
                result = pickle.load(handle)["result"]
            pooled, leagues = _versus_rows(result, benchmark, n_resamples=task["resamples"])
            pooled_rows.append(pooled)
            league_rows.extend(leagues)
    pd.DataFrame(pooled_rows).to_pickle(partial / f"recent_pooled__{family}.pkl")
    pd.DataFrame(league_rows).to_pickle(partial / f"recent_by_league__{family}.pkl")
    return {"family": family, "markets": len(pooled_rows), "seconds": time.perf_counter() - started}


def cmd_recent(args: argparse.Namespace) -> None:
    root = Path(args.root)
    partial = root / "count_partial"
    families = [f for f in args.families.split(",") if f]
    tasks = [{"family": f, "partial_dir": str(partial), "resamples": args.resamples} for f in families]
    with ProcessPoolExecutor(max_workers=max(1, min(int(args.workers), 3))) as pool:
        for outcome in pool.map(run_recent_task, tasks):
            print(f"recent baseline done: {outcome}", flush=True)
    pooled = pd.concat([pd.read_pickle(partial / f"recent_pooled__{f}.pkl") for f in families], ignore_index=True)
    leagues = pd.concat([pd.read_pickle(partial / f"recent_by_league__{f}.pkl") for f in families], ignore_index=True)
    with closing(sqlite3.connect(root / EVALUATION_DB)) as connection:
        pooled.to_sql("pooled_recent", connection, if_exists="replace", index=False)
        leagues.to_sql("by_league_recent", connection, if_exists="replace", index=False)
        connection.commit()
    saved = 0
    for path in sorted(partial.glob("recent__*__*.pkl")):
        with path.open("rb") as handle:
            save_result(pickle.load(handle), root)
        saved += 1
    print(f"recent baseline tables written; {saved} forecast tables saved to count_recent_frequencies.sqlite")


# ---------------------------------------------------------------------------
# Feature ablation (exploratory: one hyper-parameter setting, a subset of big leagues)
# ---------------------------------------------------------------------------

ABLATION_LEAGUES = ("EPL", "LALIGA", "EREDIVISIE", "CHAMPIONSHIP", "BUNDESLIGA", "SERIEA", "LIGUE1", "SUPERLIG")
ABLATION_PARAMS = {"ridge": (1_000.0,), "half_life_days": (None,)}


def ablation_variants(family: str) -> dict[str, FeatureConfig]:
    """Feature sets compared with the final one (same protocol, one fixed hyper-parameter setting)."""
    aux = FAMILIES[family].aux
    variants = {
        "final": FeatureConfig(),
        "with_xg": FeatureConfig(aux=(*aux, "xg")),
        "with_xg_shrunk": FeatureConfig(aux=(*aux, "xg"), aux_shrink=12.0),
        "no_aux": FeatureConfig(aux=()),
        "no_referee": FeatureConfig(use_referee=False),
        "no_rest": FeatureConfig(use_rest=False),
        "windows_5_10_30": FeatureConfig(windows=(5, 10, 30)),
        "own_side_only": FeatureConfig(both_sides=False),
    }
    return variants


def run_ablation_task(task: dict[str, Any]) -> pd.DataFrame:
    family, variant = task["family"], task["variant"]
    matches = pd.read_pickle(Path(task["partial_dir"]) / "matches.pkl")
    matches = matches[matches["league_code"].isin(ABLATION_LEAGUES)]
    config = count_config()
    fam = FAMILIES[family]
    features = ablation_variants(family)[variant]
    engine = CountEngine(family, "nb2", features=features)
    rows = []
    for scope, line in (("total", fam.total_lines[len(fam.total_lines) // 2]), ("home", fam.team_lines[len(fam.team_lines) // 2]), ("away", fam.team_lines[len(fam.team_lines) // 2])):
        market = [m for m in count_markets(fam) if m.name == f"{family}_{scope}_{line}"][0]
        baseline = evaluate_model(count_baseline_spec(market), matches, market, config)
        result = evaluate_model(count_spec(engine, scope, line, grid=ABLATION_PARAMS), matches, market, config)
        both = (
            result.scores(["log_loss"])[["fixture_id", "log_loss"]]
            .merge(baseline.scores(["log_loss"])[["fixture_id", "log_loss"]], on="fixture_id", suffixes=("_model", "_base"))
            .dropna()
        )
        rows.append(
            {
                "family": family,
                "variant": variant,
                "scope": scope,
                "line": line,
                "n": len(both),
                "ll_model": float(both["log_loss_model"].mean()),
                "ll_base": float(both["log_loss_base"].mean()),
                "diff": float(both["log_loss_base"].mean() - both["log_loss_model"].mean()),
            }
        )
    return pd.DataFrame(rows)


def cmd_ablation(args: argparse.Namespace) -> None:
    root = Path(args.root)
    partial = root / "count_partial"
    prepare_data(partial, False)
    families = [f for f in args.families.split(",") if f]
    wanted = {v for v in args.variants.split(",") if v}
    tasks = [
        {"family": f, "variant": v, "partial_dir": str(partial)}
        for f in families
        for v in ablation_variants(f)
        if not wanted or v in wanted
    ]
    workers = max(1, min(int(args.workers), 3))
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_ablation_task, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            frame = future.result()
            parts.append(frame)
            print(f"ablation {task['family']}/{task['variant']}: " + ", ".join(f"{r.scope} {r.diff:+.4f}" for r in frame.itertuples()), flush=True)
    table = pd.concat(parts, ignore_index=True)
    with closing(sqlite3.connect(root / EVALUATION_DB)) as connection:
        existing = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "ablation" in existing and wanted:  # a partial rerun replaces only the variants it recomputed
            old = pd.read_sql_query("SELECT * FROM ablation", connection)
            keep = ~old.set_index(["family", "variant"]).index.isin(table.set_index(["family", "variant"]).index)
            table = pd.concat([old[keep], table], ignore_index=True)
        table.to_sql("ablation", connection, if_exists="replace", index=False)
        connection.commit()
    print(f"ablation table written ({len(table)} rows)")


# ---------------------------------------------------------------------------
# Parent side: run, finalize, report
# ---------------------------------------------------------------------------


def prepare_data(partial: Path, refresh: bool) -> pd.DataFrame:
    """Load the joined match table, apply the coverage rule, keep it for the workers."""
    target = partial / "matches.pkl"
    coverage_path = partial / "coverage.pkl"
    if target.exists() and coverage_path.exists() and not refresh:
        return pd.read_pickle(target)
    started = time.perf_counter()
    matches = load_count_matches()
    matches, coverage = apply_coverage_rule(matches, FAMILIES)
    partial.mkdir(parents=True, exist_ok=True)
    matches.to_pickle(target)
    coverage.to_pickle(coverage_path)
    print(f"Loaded {len(matches)} matches and applied the coverage rule in {time.perf_counter() - started:.0f}s", flush=True)
    return matches


def count_statistics(matches: pd.DataFrame, families: list[str], seasons: tuple[int, ...]) -> pd.DataFrame:
    """Per family and league: means, variances and the home-away correlation of the counts in the test seasons."""
    done = matches[(matches["status_short"] == "FT") & matches["season"].isin(seasons)]
    rows = []
    for family in families:
        home, away = f"home_{family}", f"away_{family}"
        known = done[done[home].notna() & done[away].notna()]
        for league, group in known.groupby("league_code"):
            h, a = group[home].to_numpy(dtype=float), group[away].to_numpy(dtype=float)
            corr = float(np.corrcoef(h, a)[0, 1]) if len(h) > 2 and h.std() > 0 and a.std() > 0 else float("nan")
            rows.append(
                {
                    "family": family,
                    "league_code": league,
                    "n": len(h),
                    "mean_home": float(h.mean()),
                    "mean_away": float(a.mean()),
                    "var_home": float(h.var(ddof=1)),
                    "var_away": float(a.var(ddof=1)),
                    "mean_total": float((h + a).mean()),
                    "var_total": float((h + a).var(ddof=1)),
                    "corr_home_away": corr,
                }
            )
    return pd.DataFrame(rows)


def _market_files(partial: Path) -> list[Path]:
    """Finished ``<family>__<kind>__<market>.pkl`` payloads (the ``recent__*`` benchmark files are not payloads)."""
    return [path for path in sorted(partial.glob("*__*__*.pkl")) if not path.name.startswith("recent__")]


def finalize(root: Path, partial: Path, wall_seconds: float | None = None, workers: int | None = None) -> dict[str, int]:
    """Write forecasts (save_result) and the evaluation tables from the finished markets."""
    frames: dict[str, list[pd.DataFrame]] = {}
    saved = {"forecast_tables": 0}
    baseline_saved: set[str] = set()
    for path in _market_files(partial):
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        result: WalkForwardResult = payload["result"]
        save_result(result, root)
        saved["forecast_tables"] += 1
        if result.market.name not in baseline_saved:
            save_result(payload["baseline"], root)
            baseline_saved.add(result.market.name)
        for name, table in payload["tables"].items():
            frames.setdefault(name, []).append(table)
    database = root / EVALUATION_DB
    with closing(sqlite3.connect(database)) as connection:
        for name, parts in frames.items():
            pd.concat(parts, ignore_index=True).to_sql(name, connection, if_exists="replace", index=False)
        coverage_path = partial / "coverage.pkl"
        if coverage_path.exists():
            pd.read_pickle(coverage_path).to_sql("coverage", connection, if_exists="replace", index=False)
        matches_path = partial / "matches.pkl"
        if matches_path.exists() and frames:
            families_done = sorted(set(pd.concat(frames["pooled"])["family"]))
            config = count_config()
            count_statistics(pd.read_pickle(matches_path), families_done, tuple(config.test_seasons)).to_sql(
                "count_stats", connection, if_exists="replace", index=False
            )
        leakage = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(partial.glob("leakage__*.json"))]
        if leakage:
            frame = pd.DataFrame(leakage)
            frame["leagues"] = frame["leagues"].map(", ".join)
            frame.to_sql("leakage", connection, if_exists="replace", index=False)
        pd.DataFrame(
            [
                {
                    "protocol_id": PROTOCOL_ID,
                    "written_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "wall_seconds": wall_seconds,
                    "workers": workers,
                }
            ]
        ).to_sql("run_info", connection, if_exists="replace", index=False)
        connection.commit()
    saved["markets"] = len({p.stem.split("__", 2)[2] for p in _market_files(partial)})
    return saved


def cmd_run(args: argparse.Namespace) -> None:
    root = Path(args.root)
    partial = root / "count_partial"
    started = time.perf_counter()
    prepare_data(partial, args.refresh_data)
    families = [f for f in args.families.split(",") if f]
    kinds = [k for k in args.kinds.split(",") if k]
    for family in families:
        if family not in FAMILIES:
            raise SystemExit(f"unknown family: {family}")
    for kind in kinds:
        if kind not in MODEL_KINDS:
            raise SystemExit(f"unknown kind: {kind}")
    common = {
        "partial_dir": str(partial),
        "resamples": args.resamples,
        "corp_sims": args.corp_sims,
        "leagues": [x for x in args.leagues.split(",") if x],
        "test_seasons": [int(x) for x in args.test_seasons.split(",") if x],
    }
    tasks = [{"family": f, "kind": k, **common} for f in families for k in kinds]
    if not args.no_leakage:
        tasks += [{"family": f, "kind": "leakage", **common} for f in families]
    workers = max(1, min(int(args.workers), 3))
    print(f"{len(tasks)} tasks on {workers} worker(s); partial results in {partial}", flush=True)
    failures = 0
    if workers == 1:
        outcomes = ((task, _dispatch(task)) for task in tasks)
        for task, outcome in outcomes:
            print(f"done {task['family']}/{task['kind']}: {outcome}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_dispatch, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    print(f"done {task['family']}/{task['kind']}: {future.result()}", flush=True)
                except Exception as error:
                    failures += 1
                    print(f"FAILED {task['family']}/{task['kind']}: {error}", flush=True)
    saved = finalize(root, partial, wall_seconds=time.perf_counter() - started, workers=workers)
    print(f"Saved {saved} to {root} in {time.perf_counter() - started:.0f}s total", flush=True)
    if failures:
        raise SystemExit(f"{failures} task(s) failed; finished markets were kept, rerun to resume")


def cmd_finalize(args: argparse.Namespace) -> None:
    root = Path(args.root)
    print(finalize(root, root / "count_partial"))


# ---------------------------------------------------------------------------
# Polish report
# ---------------------------------------------------------------------------

REPORT_PATH = Path(__file__).resolve().parents[2] / "reports" / "Modele rozne kartki strzaly - pytanie 1.md"
FAMILY_TITLES = {
    "corners": "Rzuty rożne",
    "yellow": "Żółte kartki",
    "shots": "Strzały",
    "sot": "Strzały celne",
}
SCOPE_LABELS = {"total": "mecz", "home": "gospodarz", "away": "gość"}
SCOPE_SHORT = {"total": "M", "home": "G", "away": "Go"}


def _num(value: Any, digits: int = 4, sign: bool = False) -> str:
    """Polish decimal comma; NaN is a dash (a missing value is never a zero)."""
    if value is None or not np.isfinite(value):
        return "—"
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.replace(".", ",")


def _int(value: Any) -> str:
    return f"{int(value):,}".replace(",", " ")


def _pct(value: float, digits: int = 1) -> str:
    return _num(100.0 * value, digits) + "%"


def _line(value: float) -> str:
    return _num(value, 1)


def load_evaluation(root: Path) -> dict[str, pd.DataFrame]:
    with closing(sqlite3.connect(f"file:{(root / EVALUATION_DB).as_posix()}?mode=ro", uri=True)) as connection:
        names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: pd.read_sql_query(f"SELECT * FROM {name}", connection) for name in names}


def _ordered_markets(frame: pd.DataFrame) -> list[str]:
    order = {"total": 0, "home": 1, "away": 2}
    unique = frame.drop_duplicates("market")[["market", "scope", "line"]].copy()
    unique["o"] = unique["scope"].map(order)
    return unique.sort_values(["o", "line"])["market"].tolist()


def _market_label(row: pd.Series) -> str:
    return f"{SCOPE_LABELS[row['scope']]} {_line(row['line'])}"


def _significance_mark(p_holm: float, diff: float) -> str:
    if not np.isfinite(p_holm) or p_holm >= 0.05:
        return ""
    return "*" if diff > 0 else "†"


def _middle(markets: list[str]) -> str:
    return sorted(markets, key=lambda m: float(m.rsplit("_", 1)[1]))[len(markets) // 2]


def build_report(tables: dict[str, pd.DataFrame], config: WalkForwardConfig, *, n_resamples: int, n_sim: int) -> str:
    """The Polish report (short sentences, numbers with context) from the evaluation tables."""
    pooled = tables["pooled"]
    by_league = tables["by_league"]
    coverage = tables.get("coverage", pd.DataFrame())
    families = [f for f in FAMILY_TITLES if f in set(pooled["family"])]
    nb2 = pooled[pooled["model"] == "count_nb2"]
    poisson = pooled[pooled["model"] == "count_poisson"]
    seasons = f"{config.test_seasons[0]}–{config.test_seasons[-1]}"
    lines: list[str] = []
    add = lines.append
    team_rows = nb2[nb2["scope"] != "total"]
    total_rows = nb2[nb2["scope"] == "total"]

    def relative(frame: pd.DataFrame) -> float:
        return float((frame["diff"] / frame["ll_base"]).mean()) if len(frame) else float("nan")

    def n_better(frame: pd.DataFrame) -> int:
        return int((frame["diff_lo"] > 0).sum())

    # ---- answer ---------------------------------------------------------
    def size_word(share: float) -> str:
        return "duża" if share >= 0.03 else ("umiarkowana" if share >= 0.01 else "mała")

    recent_pooled = tables.get("pooled_recent", pd.DataFrame())
    recent_nb2 = recent_pooled[recent_pooled["model"] == "count_nb2"] if len(recent_pooled) else recent_pooled

    def relative_recent(frame: pd.DataFrame) -> float:
        return float((frame["diff"] / frame["ll_bench"]).mean()) if len(frame) else float("nan")

    n_beats = n_better(nb2)
    n_recent = int((recent_nb2["diff_lo"] > 0).sum()) if len(recent_nb2) else None
    verdict = "bije" if n_beats > len(nb2) / 2 else "nie bije wyraźnie"
    add("# Rzuty rożne, kartki i strzały: czy model bije częstość ligową? (pytanie 1)")
    add("")
    recent_sentence = (
        f" i w {n_recent} z {len(recent_nb2)} linii względem baseline'u z ostatnich 365 dni" if n_recent is not None else ""
    )
    add(
        f"Model NB2 z formą drużyn, sędzią i odpoczynkiem {verdict} częstość ligową (baseline) w prognozowaniu liczników meczowych. "
        f"Sprawdziliśmy linie over/under w {len(families)} statystykach (razem {len(nb2)} linii), sezony testowe {seasons}, ten sam protokół co dla goli. "
        f"Przedział ufności różnicy log-loss leży nad zerem w {n_beats} z {len(nb2)} linii względem baseline'u z całej historii ligi{recent_sentence}. "
        "Wielkość przewagi zależy od statystyki i od tego, czy prognozujemy jedną drużynę, czy sumę obu."
    )
    add("")
    add(
        "Średnia względna poprawa log-loss (strata baseline'u minus strata NB2, podzielona przez stratę baseline'u), średnia po liniach:"
    )
    add("")
    add("| Statystyka | Drużyna vs cała historia | Drużyna vs 365 dni | Suma vs cała historia | Suma vs 365 dni |")
    add("|---|---:|---:|---:|---:|")
    extremes: list[tuple[float, str]] = []
    for family in families:
        sub = nb2[nb2["family"] == family]
        rsub = recent_nb2[recent_nb2["family"] == family] if len(recent_nb2) else recent_nb2
        cells = []
        for scope_is_total in (False, True):
            hist = relative(sub[(sub["scope"] == "total") == scope_is_total])
            rec = relative_recent(rsub[(rsub["scope"] == "total") == scope_is_total]) if len(rsub) else float("nan")
            cells += [_pct(hist), _pct(rec)]
            extremes.append((hist, f"{FAMILY_TITLES[family].lower()}, {'suma obu drużyn' if scope_is_total else 'jedna drużyna'}"))
        add(f"| {FAMILY_TITLES[family]} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |")
    add("")
    extremes.sort()
    add(
        f"Największa poprawa: {extremes[-1][1]} ({_pct(extremes[-1][0])}), najmniejsza: {extremes[0][1]} ({_pct(extremes[0][0])}). "
        "Baseline z całej historii jest słaby tam, gdzie poziom statystyki dryfuje (strzały i kartki zmieniają się z sezonu na sezon), dlatego "
        "dodatkowo porównujemy z częstością z ostatnich 365 dni ligi; ta liczba jest uczciwszą miarą umiejętności."
    )
    add("")
    add(
        "Jak to czytać: różnica log-loss to strata baseline'u minus strata modelu, więc dodatnia liczba oznacza przewagę modelu. "
        "Model jest gotowy do zestawienia z kursami z kolektora, ale ten raport kursów nie używa."
    )
    add("")

    # ---- protocol -------------------------------------------------------
    add("## Jak mierzyliśmy")
    add("")
    add(
        f"- **Ten sam protokół co dla goli** (`evaluation/protocol.py`): sezony testowe {seasons}, jeden model na ligę, dopasowywany od nowa "
        f"przed każdą kolejką na meczach zakończonych co najmniej {_num(config.result_latency_hours, 0)} godziny przed pierwszym gwizdkiem "
        "kolejki. Hiperparametry wybieramy na jednym sezonie przed sezonem testowym (łącznie dla wszystkich lig, osobno dla każdej linii)."
    )
    add(
        "- **Co model widzi.** Przy prognozie meczu wszystkie liczniki tego meczu (rożne, kartki, strzały, faule, xG) są ukryte, nie tylko "
        "ten, który prognozujemy. Do formy używamy wyłącznie wcześniejszych meczów."
    )
    add(
        "- **Baseline.** Dla każdej linii osobno: częstość przekroczenia tej linii w danej lidze, policzona na danych treningowych "
        "(wygładzanie Laplace'a). To odpowiednik częstości ligowych z bramek."
    )
    add(
        f"- **Metryki.** Główna to log-loss. Przedziały ufności: bootstrap po kolejkach ({_int(n_resamples)} losowań). Test Diebolda–Mariano "
        "z wariancją HAC po kolejkach oraz korekty Holma i Benjaminiego–Hochberga liczone między ligami, osobno dla każdej linii. "
        f"Kalibracja: rozkład CORP (MCB − DSC + UNC), p-wartość zbiorcza z {_int(n_sim)} symulacji."
    )
    add(
        "- **Uwaga o wyborze konstrukcji modelu.** Siatka hiperparametrów (kara, półokres) jest wybierana zgodnie z protokołem, na sezonie przed "
        "sezonem testowym. Sama konstrukcja modelu (okna 5/15/45/90, cechy pomocnicze, forma obu stron) została natomiast wybrana w przebiegach "
        "roboczych na ligach i sezonach testowych 2022–2025, czyli na tych samych wynikach, które oceniamy. Tabela ablacji niżej pokazuje, że "
        "alternatywne konstrukcje różnią się od końcowej o około 0,001–0,005 w log-loss (bez xG i bez cech pomocniczych też biją baseline). "
        "Przewaga nad baseline'em jest zwykle o rząd wielkości większa, ale dla sum rożnych (około +0,005) jest tego samego rzędu, więc tę liczbę "
        "traktujemy jako lekko zawyżoną."
    )
    add("")
    add("## Statystyki, linie i model")
    add("")
    add("| Statystyka | Definicja | Linie: mecz | Linie: drużyna |")
    add("|---|---|---|---|")
    from model_buk.evaluation.count_markets import FAMILIES as families_by_name

    definitions = {
        "corners": "rzuty rożne z `team_match_stats`",
        "yellow": "żółte kartki z `team_match_stats`; przy pustym polu liczba kartek z feedu zdarzeń, ale tylko w liga-sezonach, w których feed zapisuje kartki (niżej)",
        "shots": "wszystkie strzały (`total_shots`)",
        "sot": "strzały celne (`shots_on_goal`)",
    }
    for family in families:
        fam = families_by_name[family]
        add(
            f"| {FAMILY_TITLES[family]} | {definitions[family]} | {', '.join(_line(x) for x in fam.total_lines)} | "
            f"{', '.join(_line(x) for x in fam.team_lines)} (gospodarz i gość osobno) |"
        )
    add("")
    add(
        "**Model.** Każdy mecz to dwa wiersze (drużyna, rywal, gospodarz albo gość). Logarytm oczekiwanej liczby zależy liniowo od: "
        "formy własnej i rywala (średnia z ostatnich 5, 15, 45 i 90 meczów, osobno zdobyte i stracone, wygładzona do średniej ligi), "
        "formy pomocniczej (strzały dla rożnych i strzałów celnych, faule dla kartek, gole dla wszystkich statystyk), tendencji sędziego "
        "(średnia sumy z jego wcześniejszych meczów w lidze, wygładzona) oraz dni odpoczynku obu drużyn. Współczynniki są karane grzbietowo "
        "i opcjonalnie ważone czasem (półokres 1 000 dni albo brak zaniku). Wariant **Poisson** zakłada rozkład Poissona, wariant **NB2** rozkład "
        "ujemny dwumianowy z jedną dyspersją na ligę (największa wiarygodność). Suma meczu to NB2 ze średnią równą sumie średnich i własną "
        "dyspersją sumy."
    )
    add("")
    add(
        "**Żółte kartki.** Dostawca zostawia pole `yellow_cards` puste u większości drużyn bez kartki. Odrzucenie takich meczów usunęłoby właśnie "
        "mecze o małej liczbie kartek, więc przy pustym polu bierzemy liczbę zdarzeń „Yellow Card” z feedu zdarzeń meczu (gdy feed istnieje; "
        "bez feedu wartość zostaje pusta). Tam, gdzie puste pole oznacza prawdziwą lukę, feed podaje kartki. Tam, gdzie istnieją oba źródła, "
        "zgadzają się w 98% przypadków. **Poprawka po recenzji:** w części liga-sezonów (głównie 2015–2018, ale też 2020–2022 w SUPERLIGA) feed "
        "istnieje, lecz nie zapisuje kartek, więc „feed bez kartki” oznaczałoby tam zero kartek w każdym meczu (np. EERSTEDIVISIE 2017: średnio 0,0 "
        "kartki na mecz). Feed uznajemy za zapisujący kartki tylko wtedy, gdy co najmniej 90% zakończonych meczów liga-sezonu z feedem ma choć jedno "
        "zdarzenie kartkowe (w prawdziwych liga-sezonach jest to 93–99%). W pozostałych liga-sezonach zdarzenia są ignorowane, a wartość to pole "
        "dostawcy albo brak. Zero fauli obu drużyn naraz to znacznik braku danych (prawdziwy mecz ma faule), więc jest traktowane jako brak."
    )
    add("")

    # ---- inclusion rule -------------------------------------------------
    add("## Które ligi i sezony weszły do oceny")
    add("")
    add(
        "Reguła włączenia: liga-sezon wchodzi do statystyki tylko wtedy, gdy co najmniej 90% zakończonych meczów ma wartość dla obu drużyn "
        "(ten sam próg 90% co lista „Gaps” w `quality_report.md`, tu liczony dla sezonu, nie dla przedziału czteroletniego). W pozostałych "
        "liga-sezonach statystyka zostaje pusta i nigdy nie jest liczona jako zero. Mecz z brakiem u jednej drużyny odpada z całej rodziny "
        "linii, więc wszystkie linie jednej statystyki oceniamy na tych samych meczach."
    )
    add("")
    add("| Statystyka | Ligi w ocenie | Mecze testowe | Wykluczone liga-sezony w latach testowych | Ligi bez żadnego sezonu testowego |")
    add("|---|---:|---:|---|---|")
    protocol_leagues = sorted(set(coverage["league_code"])) if len(coverage) else []
    for family in families:
        sub = by_league[(by_league["family"] == family) & (by_league["model"] == "count_nb2")]
        first = sub[sub["market"] == sub["market"].iloc[0]]
        used = set(first["league_code"])
        excluded_text = missing_text = "—"
        if len(coverage):
            cov = coverage[(coverage["family"] == family) & coverage["season"].between(config.test_seasons[0], config.test_seasons[-1])]
            bad = cov[~cov["included"].astype(bool)]
            excluded_text = f"{len(bad)} z {len(cov)}"
            missing = sorted(set(protocol_leagues) - used)
            missing_text = ", ".join(missing) if missing else "—"
        add(f"| {FAMILY_TITLES[family]} | {len(used)} | {_int(first['n'].sum())} | {excluded_text} | {missing_text} |")
    add("")

    # ---- pooled results -------------------------------------------------
    add("## Wynik zbiorczy")
    add("")
    add(
        "Log-loss baseline'u z całej historii (Hist.), baseline'u z ostatnich 365 dni (365 d), Poissona i NB2 na tych samych meczach. "
        "Różnica to strata baseline'u minus strata NB2 (dodatnia = model lepszy) z przedziałem 95% (bootstrap po kolejkach). "
        "Ostatnia kolumna: liczba lig, w których NB2 ma niższą stratę niż baseline z historii, w nawiasie ile z nich istotnie po korekcie Holma (5%)."
    )
    add("")
    recent_by_market = {}
    if len(recent_nb2):
        recent_by_market = {r["market"]: r for _, r in recent_nb2.iterrows()}
    for family in families:
        add(f"### {FAMILY_TITLES[family]}")
        add("")
        add("| Linia | Mecze | Hist. | 365 d | Poisson | NB2 | Różnica vs Hist. [95% CI] | Różnica vs 365 d [95% CI] | Ligi lepsze (Holm) |")
        add("|---|---:|---:|---:|---:|---:|---|---|---|")
        sub = nb2[nb2["family"] == family]
        for market in _ordered_markets(sub):
            row = sub[sub["market"] == market].iloc[0]
            prow = poisson[poisson["market"] == market]
            plain = _num(prow["ll_model"].iloc[0]) if len(prow) else "—"
            rec = recent_by_market.get(market)
            rec_ll = _num(rec["ll_bench"]) if rec is not None else "—"
            rec_diff = (
                f"{_num(rec['diff'], 4, True)} [{_num(rec['diff_lo'], 4, True)}; {_num(rec['diff_hi'], 4, True)}]" if rec is not None else "—"
            )
            add(
                f"| {_market_label(row)} | {_int(row['n'])} | {_num(row['ll_base'])} | {rec_ll} | {plain} | {_num(row['ll_model'])} | "
                f"{_num(row['diff'], 4, True)} [{_num(row['diff_lo'], 4, True)}; {_num(row['diff_hi'], 4, True)}] | {rec_diff} | "
                f"{int(row['leagues_better'])} z {int(row['n_leagues'])} ({int(row['leagues_better_holm'])}) |"
            )
        add("")
    if len(recent_nb2):
        add(
            f"Względem baseline'u z 365 dni NB2 jest istotnie lepszy po korekcie Holma w {int(recent_nb2['leagues_better_holm'].sum())} z "
            f"{int(recent_nb2['n_leagues'].sum())} par liga-linia, a istotnie gorszy w {int(recent_nb2['leagues_worse_holm'].sum())}. "
            "Baseline z 365 dni ma mniej danych (jeden sezon), więc jest szumniejszy, ale nie ma dryfu."
        )
        add("")
    both = nb2.merge(poisson[["market", "ll_model"]], on="market", suffixes=("", "_poisson"))
    if len(both):
        nb2_better = int((both["ll_model"] < both["ll_model_poisson"]).sum())
        gap = (both["ll_model_poisson"] - both["ll_model"]).groupby(both["scope"]).mean()
        add(
            f"**Poisson czy NB2.** NB2 ma niższy log-loss niż Poisson w {nb2_better} z {len(both)} linii. Średnia przewaga NB2: "
            + ", ".join(f"{SCOPE_LABELS[k]} {_num(v, 4, True)}" for k, v in gap.items())
            + ". Liczniki są bardziej rozrzucone, niż zakłada Poisson (wariancja większa od średniej, tabela niżej), "
            "więc Poisson zawyża prawdopodobieństwo „over” na liniach poniżej średniej."
        )
        add("")

    stats_table = tables.get("count_stats", pd.DataFrame())
    if len(stats_table):
        add("### Dlaczego suma jest trudniejsza niż drużyny")
        add("")
        add(
            "Liczniki obu drużyn w jednym meczu nie są niezależne. Tabela pokazuje średnie ważone po ligach w sezonach testowych: "
            "średnią sumy, stosunek wariancji do średniej (dla Poissona równy 1) i korelację liczby gospodarza z liczbą gościa."
        )
        add("")
        add("| Statystyka | Średnia sumy | Wariancja / średnia (suma) | Wariancja / średnia (gospodarz) | Korelacja gospodarz–gość |")
        add("|---|---:|---:|---:|---:|")
        for family in families:
            sub = stats_table[stats_table["family"] == family]
            if sub.empty:
                continue
            w = sub["n"].to_numpy(dtype=float)

            def weighted(values: pd.Series) -> float:
                return float(np.average(values.to_numpy(dtype=float), weights=w))

            add(
                f"| {FAMILY_TITLES[family]} | {_num(weighted(sub['mean_total']), 2)} | "
                f"{_num(weighted(sub['var_total'] / sub['mean_total']), 2)} | {_num(weighted(sub['var_home'] / sub['mean_home']), 2)} | "
                f"{_num(weighted(sub['corr_home_away'].fillna(0.0)), 2)} |"
            )
        add("")
        correlations = {
            family: float(np.average(g["corr_home_away"].fillna(0.0), weights=g["n"].to_numpy(dtype=float)))
            for family, g in stats_table.groupby("family")
        }
        negative = [FAMILY_TITLES[f].lower() for f in families if correlations.get(f, 0.0) < -0.05]
        positive = [FAMILY_TITLES[f].lower() for f in families if correlations.get(f, 0.0) > 0.05]
        sentences = []
        if negative:
            sentences.append(
                f"Ujemna korelacja ({', '.join(negative)}) oznacza, że gdy jedna drużyna dominuje, druga ma mniej. Model wie, kto dominuje, "
                "więc dobrze przewiduje każdą drużynę osobno, ale suma zmienia się dużo mniej niż jej składniki."
            )
        if positive:
            sentences.append(
                f"Dodatnia korelacja ({', '.join(positive)}) oznacza, że ostry albo szybki mecz podnosi oba liczniki naraz; "
                "model łapie to przez tendencję sędziego i formę obu drużyn."
            )
        if sentences:
            add(" ".join(sentences))
            add("")

    # ---- per league -----------------------------------------------------
    add("## Wynik w ligach")
    add("")
    add(
        "Różnica log-loss baseline minus NB2 w każdej lidze i linii (dodatnia = model lepszy). Gwiazdka oznacza istotną przewagę modelu "
        "po korekcie Holma (5%, korekta liczona między ligami dla danej linii), krzyżyk istotną przewagę baseline'u. Kolumny: M to suma meczu, "
        "G gospodarz, Go gość. Pełne tabele z przedziałami ufności, korektą Benjaminiego–Hochberga i statystyką DM są w "
        "`predictions/count_evaluation.sqlite` (tabela `by_league`)."
    )
    add("")
    for family in families:
        sub = by_league[(by_league["family"] == family) & (by_league["model"] == "count_nb2")]
        markets = _ordered_markets(pooled[(pooled["family"] == family) & (pooled["model"] == "count_nb2")])
        labels = {}
        for market in markets:
            row = sub[sub["market"] == market]
            if len(row):
                labels[market] = f"{SCOPE_SHORT[row.iloc[0]['scope']]} {_line(row.iloc[0]['line'])}"
        markets = [m for m in markets if m in labels]
        add(f"### {FAMILY_TITLES[family]}")
        add("")
        add("| Liga | Mecze | " + " | ".join(labels[m] for m in markets) + " |")
        add("|---|---:|" + "---:|" * len(markets))
        for league in sorted(set(sub["league_code"])):
            n = int(sub[sub["league_code"] == league]["n"].iloc[0])
            cells = []
            for market in markets:
                row = sub[(sub["market"] == market) & (sub["league_code"] == league)]
                if row.empty:
                    cells.append("—")
                else:
                    r = row.iloc[0]
                    cells.append(_num(r["diff"], 3, True) + _significance_mark(r["p_holm"], r["diff"]))
            add(f"| {league} | {_int(n)} | " + " | ".join(cells) + " |")
        add("")
    add(
        f"Łącznie po korekcie Holma NB2 jest istotnie lepszy od baseline'u w {int(nb2['leagues_better_holm'].sum())} z {int(nb2['n_leagues'].sum())} "
        f"par liga-linia, a istotnie gorszy w {int(nb2['leagues_worse_holm'].sum())}. Po korekcie Benjaminiego–Hochberga: lepszy w "
        f"{int(nb2['leagues_better_bh'].sum())}, gorszy w {int(nb2['leagues_worse_bh'].sum())}."
    )
    add("")

    # ---- calibration ----------------------------------------------------
    add("## Kalibracja (CORP)")
    add("")
    add(
        "Rozkład CORP dzieli wynik Briera na MCB (miskalibracja, cena za prognozy niezgodne z częstością), DSC (zdolność odróżniania meczów) i UNC "
        "(niepewność samego wyniku): Brier = MCB − DSC + UNC. Wartości pomnożone przez 1000. Dobry model ma DSC dużo większe niż MCB. "
        "„Średnia p” to średnie prawdopodobieństwo „over”, „częstość” to odsetek meczów z „over”, obie pooled po wszystkich ligach."
    )
    add("")
    for family in families:
        add(f"### {FAMILY_TITLES[family]}")
        add("")
        add("| Linia | Częstość | Średnia p (NB2) | MCB baseline | DSC baseline | MCB NB2 | DSC NB2 | p(MCB) NB2 |")
        add("|---|---:|---:|---:|---:|---:|---:|---:|")
        sub = nb2[nb2["family"] == family]
        for market in _ordered_markets(sub):
            r = sub[sub["market"] == market].iloc[0]
            add(
                f"| {_market_label(r)} | {_num(r['freq_over'], 3)} | {_num(r['mean_p_over'], 3)} | {_num(1000 * r['mcb_base'], 2)} | "
                f"{_num(1000 * r['dsc_base'], 2)} | {_num(1000 * r['mcb_model'], 2)} | {_num(1000 * r['dsc_model'], 2)} | {_num(r['mcb_p_model'], 3)} |"
            )
        add("")
    leagues_nb2 = by_league[by_league["model"] == "count_nb2"]
    median_model, median_base = leagues_nb2["mcb_model"].median(), leagues_nb2["mcb_base"].median()
    comparison = (
        "Model jest więc lepiej skalibrowany niż baseline z całej historii (który nie nadąża za dryfem poziomu), a do tego odróżnia mecze."
        if median_model < median_base
        else "Model jest więc trochę gorzej skalibrowany niż stała częstość, ale za to odróżnia mecze."
    )
    detectable = int((nb2["mcb_p_model"] < 0.05).sum())
    gaps = (nb2["mean_p_over"] - nb2["freq_over"]).abs()
    worst = nb2.loc[gaps.idxmax()]
    add(
        f"W {int((leagues_nb2['dsc_model'] > leagues_nb2['mcb_model']).sum())} z {len(leagues_nb2)} par liga-linia DSC modelu jest większe niż jego MCB. "
        f"Mediana MCB NB2 w ligach wynosi {_num(1000 * median_model, 2)} (×1000), mediana MCB baseline'u {_num(1000 * median_base, 2)}. {comparison} "
        f"Miskalibracja jest jednak wykrywalna statystycznie (p(MCB) poniżej 0,05 zbiorczo) w {detectable} z {len(nb2)} linii. "
        f"Największa różnica między średnią prognozą a częstością to {_num(100 * gaps.max(), 1)} pkt proc. "
        f"({FAMILY_TITLES[worst['family']].lower()}, {_market_label(worst)}: {_num(worst['mean_p_over'], 3)} wobec {_num(worst['freq_over'], 3)})."
    )
    add("")
    add("Krzywa niezawodności CORP dla środkowej linii meczowej każdej statystyki (pooled NB2): prognoza i oszacowana częstość.")
    add("")
    reliability = tables["reliability"]
    for family in families:
        sub = reliability[(reliability["family"] == family) & (reliability["model"] == "count_nb2") & (reliability["scope"] == "total")]
        if sub.empty:
            continue
        market = _middle(sorted(set(sub["market"])))
        curve = sub[sub["market"] == market]
        add(f"**{FAMILY_TITLES[family]}, {SCOPE_LABELS['total']} {_line(float(market.rsplit('_', 1)[1]))}**")
        add("")
        add("| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |")
        add("|---:|---:|---:|")
        for _, r in curve.iterrows():
            add(f"| {_num(r['forecast'], 3)} | {_num(r['cep'], 3)} | {_num(r['share_at_or_below'], 2)} |")
        add("")
    add("Krzywe dla pozostałych linii są w tabeli `reliability`, a dla dowolnej ligi da się je policzyć z prognoz w `count_nb2.sqlite`.")
    add("")
    add("### Kalibracja w ligach")
    add("")
    add(
        "Dla środkowej linii meczowej i środkowej linii drużynowej gospodarza każdej statystyki: średnie prawdopodobieństwo „over” i częstość "
        "oraz MCB i DSC modelu ×1000 w każdej lidze. Pozostałe linie są w tabeli `by_league`."
    )
    add("")
    for family in families:
        sub = by_league[(by_league["family"] == family) & (by_league["model"] == "count_nb2")]
        totals = sorted(set(sub[sub["scope"] == "total"]["market"]))
        homes = sorted(set(sub[sub["scope"] == "home"]["market"]))
        if not totals or not homes:
            continue
        picked = [_middle(totals), _middle(homes)]
        add(f"**{FAMILY_TITLES[family]}**")
        add("")
        header = ["Liga"]
        for market in picked:
            label = _market_label(sub[sub["market"] == market].iloc[0])
            header += [f"{label}: średnia p / częstość", f"{label}: MCB / DSC"]
        add("| " + " | ".join(header) + " |")
        add("|---|" + "---:|" * (len(header) - 1))
        for league in sorted(set(sub["league_code"])):
            cells = []
            for market in picked:
                row = sub[(sub["market"] == market) & (sub["league_code"] == league)]
                if row.empty:
                    cells += ["—", "—"]
                else:
                    r = row.iloc[0]
                    cells.append(f"{_num(r['mean_p_over'], 3)} / {_num(r['freq_over'], 3)}")
                    cells.append(f"{_num(1000 * r['mcb_model'], 1)} / {_num(1000 * r['dsc_model'], 1)}")
            add(f"| {league} | " + " | ".join(cells) + " |")
        add("")

    # ---- hyper-parameters, ablation, leakage ---------------------------
    selections = tables.get("selections", pd.DataFrame())
    if len(selections):
        add("## Wybrane hiperparametry")
        add("")
        add(
            "Siatka: kara grzbietowa 1 000 lub 5 000, półokres zaniku 1 000 dni lub brak zaniku. Wybór na sezonie przed sezonem testowym, osobno "
            "dla każdej linii i modelu (tak działa rama). Liczba wyborów (linie × modele × sezony testowe):"
        )
        add("")
        counts = selections[selections["league"] == "ALL"].groupby("params").size().sort_values(ascending=False)
        add("| Parametry | Wyborów |")
        add("|---|---:|")
        for params, count in counts.items():
            parsed = json.loads(params)
            half = "brak zaniku" if parsed.get("half_life_days") is None else f"półokres {_int(parsed['half_life_days'])} dni"
            add(f"| kara {_int(parsed['ridge'])}, {half} | {_int(count)} |")
        add("")
    ablation = tables.get("ablation", pd.DataFrame())
    if len(ablation):
        add("## Co wnoszą poszczególne cechy")
        add("")
        add(
            "Ablacja na 8 dużych ligach (EPL, LALIGA, EREDIVISIE, CHAMPIONSHIP, BUNDESLIGA, SERIEA, LIGUE1, SUPERLIG), ten sam protokół, NB2, jedno "
            "ustawienie hiperparametrów (kara 1 000, bez zaniku), środkowe linie. Wartość to przewaga nad baseline'em w log-loss (ta sama miara co "
            "wyżej). To badanie pomocnicze, nie część głównego przebiegu."
        )
        add("")
        variant_names = {
            "final": "model końcowy",
            "with_xg": "+ forma xG (tam, gdzie jest)",
            "with_xg_shrunk": "+ forma xG, mocniejsze wygładzenie cech pomocniczych",
            "no_aux": "bez cech pomocniczych",
            "no_referee": "bez sędziego",
            "no_rest": "bez odpoczynku",
            "windows_5_10_30": "krótsze okna (5, 10, 30)",
            "own_side_only": "tylko własne zdobyte i stracone rywala",
        }
        for family in families:
            sub = ablation[ablation["family"] == family]
            if sub.empty:
                continue
            add(f"**{FAMILY_TITLES[family]}**")
            add("")
            add("| Wariant | Mecz | Gospodarz | Gość |")
            add("|---|---:|---:|---:|")
            for variant, name in variant_names.items():
                v = sub[sub["variant"] == variant]
                if v.empty:
                    continue
                cells = [
                    _num(v[v["scope"] == s]["diff"].iloc[0], 4, True) if (v["scope"] == s).any() else "—" for s in ("total", "home", "away")
                ]
                add(f"| {name} | " + " | ".join(cells) + " |")
            add("")
        xg_variants = ablation[ablation["variant"].isin(["with_xg", "with_xg_shrunk"])]
        final = ablation[ablation["variant"] == "final"].set_index(["family", "scope"])["diff"]
        worse = [
            (r["family"], r["scope"]) for _, r in xg_variants.iterrows() if r["diff"] < final.get((r["family"], r["scope"]), float("inf"))
        ]
        if len(xg_variants):
            add(
                f"**xG.** Wariant z formą xG jest gorszy od modelu końcowego w {len(worse)} z {len(xg_variants)} porównań (rodzina × zakres × wariant). "
                "Powód: xG jest dostępne od 2023 roku i tylko w części lig, więc przez większość historii cecha jest pusta, a uczenie współczynnika "
                "opiera się na małej próbie. Dlatego xG nie wchodzi do modelu końcowego. To nie dowód, że xG jest bezużyteczne: po kilku sezonach "
                "danych warto to sprawdzić ponownie."
            )
            add("")
        referee = ablation[ablation["variant"] == "no_referee"].set_index(["family", "scope"])["diff"]
        if ("yellow", "total") in referee.index:
            add(
                f"**Sędzia.** Dla żółtych kartek przewaga na sumie spada z {_num(final[('yellow', 'total')], 4, True)} do "
                f"{_num(referee[('yellow', 'total')], 4, True)} po usunięciu sędziego; dla rożnych i strzałów sędzia nic nie zmienia."
            )
            add("")
    leakage = tables.get("leakage", pd.DataFrame())
    add("## Test przecieku")
    add("")
    if leakage.empty:
        add("Nie uruchomiony w tym przebiegu (`--no-leakage`).")
    else:
        add(
            "Wyniki i wszystkie liczniki (rożne, kartki, strzały, faule, xG, gole) meczów od daty granicznej zostały przetasowane między meczami. "
            "Prognozy z kolejek rozpoczętych do tej daty włącznie nie mogą się zmienić. Test przeszedł całą procedurę z wyborem hiperparametrów."
        )
        add("")
        add("| Statystyka | Ligi | Data graniczna | Porównane prognozy | Zmienione prognozy | Wynik |")
        add("|---|---|---|---:|---:|---|")
        for _, r in leakage.iterrows():
            verdict = "przeszedł" if r["passed"] else "**NIE PRZESZEDŁ**"
            add(
                f"| {FAMILY_TITLES.get(r['family'], r['family'])} | {r['leagues']} | {r['cutoff']} | {_int(r['n_compared'])} | "
                f"{_int(r['n_forecasts_changed'])} | {verdict} |"
            )
    add("")

    # ---- time -----------------------------------------------------------
    add("## Czas")
    add("")
    seconds = pooled.groupby(["family", "model"])["elapsed_seconds"].sum()
    kind_names = {"count_nb2": "NB2", "count_poisson": "Poisson"}
    run_info = tables.get("run_info", pd.DataFrame())
    wall = (
        float(run_info["wall_seconds"].iloc[0])
        if len(run_info) and "wall_seconds" in run_info.columns and pd.notna(run_info["wall_seconds"].iloc[0])
        else float("nan")
    )
    wall_text = (
        f"Cały przebieg protokołu (34 linie, dwa modele, test przecieku) trwał {_num(wall / 60, 0)} minut na 3 procesach roboczych. "
        if np.isfinite(wall)
        else ""
    )
    rerun = tables.get("rerun_info", pd.DataFrame())
    if len(rerun):
        wall_text += (
            f"Żółte kartki przeliczono ponownie po poprawce reguły feedu kartek ({_num(float(rerun['wall_seconds'].iloc[0]) / 60, 0)} minut, "
            f"{int(rerun['workers'].iloc[0])} procesy robocze); pozostałe statystyki pochodzą z pierwszego przebiegu. "
        )
    add(
        wall_text
        + "Jedno dopasowanie obsługuje wszystkie linie danej statystyki (pamięć podręczna po dokładnym zbiorze treningowym), więc pierwsza linia "
        "kosztuje najwięcej, a kolejne niewiele. Czas ram ewaluacyjnych na statystykę i model (suma po liniach), w minutach: "
        + "; ".join(f"{FAMILY_TITLES.get(f, f)} {kind_names.get(m, m)} {_num(v / 60, 1)}" for (f, m), v in seconds.items())
        + ". Dopasowań w jednym zadaniu: 24 000 do 35 000 (kilkanaście milisekund każde, rozwiązanie zaczyna od poprzedniego rozwiązania tej samej ligi)."
    )
    add("")

    # ---- limits ---------------------------------------------------------
    add("## Ograniczenia i luki")
    add("")
    add("- **Kursy.** Nie użyliśmy żadnych kursów ani do dopasowania, ani do oceny. Następny krok to zestawienie z kursami z kolektora (`api-football-odds`).")
    add("- **Reguły rozliczenia.** Liczymy mecze w regulaminowym czasie (status FT). Bukmacherzy mogą inaczej liczyć kartki (druga żółta jako osobna kartka) i rożne po 90. minucie.")
    add("- **Punkty kartkowe** (10 za żółtą, 25 za czerwoną) są policzone w danych (`home_booking`), ale nie modelowane: to wartości kratowe, których modele liczbowe nie dopasowują.")
    add("- **Podróż** pominięta: tabela meczów nie ma współrzędnych stadionów.")
    add("- **xG** jest dostępne głównie od 2023 roku i tylko w części lig, więc dla większości meczów treningowych jest puste (puste zostaje puste, nie zero).")
    add("- **Sędzia.** Nazwy sędziów w API-Football nie są ujednolicone; inny zapis tego samego sędziego rozbija jego historię.")
    if len(coverage):
        yellow_cov = coverage[coverage["family"] == "yellow"]
        add(
            f"- **Żółte kartki: jakość danych historycznych.** Po poprawce reguły feedu kartek {int((~yellow_cov['included'].astype(bool)).sum())} z "
            f"{len(yellow_cov)} liga-sezonów nie wchodzi do żółtych kartek (głównie 2015–2018; w latach testowych tylko SUPERLIGA 2022). "
            "W ligach, w których feed zapisuje kartki tylko w 90–95% meczów, część meczów bez kartek może być luką zapisaną jako zero; "
            "próg 90% jest kompromisem między utratą meczów z zerem kartek a wpuszczeniem fałszywych zer."
        )
    add(
        "- **Rożne i strzały: pojedyncze zera.** W danych jest 13 meczów z sumą 0 rożnych (8 w CZECHLIGA 2022, gdzie strzały i faule są puste, "
        "więc to najpewniej luka zapisana jako zero) i 12 meczów z sumą 0 strzałów. To poniżej 0,04% meczów; nie usuwaliśmy ich i nie sprawdzaliśmy wpływu."
    )
    add(
        "- **Siatka hiperparametrów.** Kara grzbietowa 5 000 (górny brzeg siatki) jest wybierana najczęściej, więc szersza siatka mogłaby dać "
        "nieco lepsze wyniki; protokół pozwala to zmienić w kolejnym przebiegu."
    )
    add("- **Dryf poziomu.** W kilku ligach średnia liczba rożnych zmienia się z sezonu na sezon (EPL: 6,1 na gospodarza w 2023, 5,4 w 2024). Model nie ma osobnego członu na poziom ligi (próba z takim członem nie poprawiła wyniku), więc część dryfu zostaje w kalibracji.")
    add("")
    add("## Jak użyć prognoz")
    add("")
    add(
        "Prognozy leżą w `Model-Buk-Data/predictions/count_nb2.sqlite` i `count_poisson.sqlite` (tabela `forecasts_<statystyka>_<mecz|home|away>_<linia>`, "
        "kolumny `p_under`, `p_over`, klucz `fixture_id` z API-Football), baseline w `count_league_frequencies.sqlite`, ocena w `count_evaluation.sqlite`. "
        "Nowy mecz prognozuje się przez `CountEngine(statystyka, 'nb2').forecast(historia, mecze, ridge=..., half_life_days=...)` z `models/count_models.py`: "
        "`historia` to zakończone mecze ligi z licznikami, `mecze` to mecze do prognozy bez liczników; wynik zawiera oczekiwane liczby i dyspersje, "
        "z których `over(zakres, linia)` daje prawdopodobieństwo dla dowolnej linii."
    )
    add("")
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> None:
    root = Path(args.root)
    tables = load_evaluation(root)
    report = build_report(tables, count_config(), n_resamples=args.resamples, n_sim=args.corp_sims)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"Report: {out} ({len(report)} characters)")



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="evaluate the count models (resumable)")
    run.add_argument("--root", default=str(PREDICTIONS_DIR), help="output directory (predictions/)")
    run.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    run.add_argument("--kinds", default=",".join(MODEL_KINDS))
    run.add_argument("--workers", type=int, default=3, help="worker processes (at most 3)")
    run.add_argument("--resamples", type=int, default=2000, help="bootstrap resamples for intervals")
    run.add_argument("--corp-sims", type=int, default=200, help="Monte Carlo simulations for the pooled CORP p-value")
    run.add_argument("--leagues", default="", help="comma separated league codes (smoke tests only)")
    run.add_argument("--test-seasons", default="", help="override the protocol's test seasons (smoke tests only)")
    run.add_argument("--no-leakage", action="store_true")
    run.add_argument("--refresh-data", action="store_true", help="rebuild the cached match table")
    run.set_defaults(handler=cmd_run)
    rec = sub.add_parser("recent", help="compare the saved forecasts with the 365-day league frequency")
    rec.add_argument("--root", default=str(PREDICTIONS_DIR))
    rec.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    rec.add_argument("--workers", type=int, default=3)
    rec.add_argument("--resamples", type=int, default=2000)
    rec.set_defaults(handler=cmd_recent)
    abl = sub.add_parser("ablation", help="feature ablation on a subset of leagues (exploratory)")
    abl.add_argument("--root", default=str(PREDICTIONS_DIR))
    abl.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    abl.add_argument("--workers", type=int, default=3)
    abl.add_argument("--variants", default="", help="comma separated variants to (re)compute; default all")
    abl.set_defaults(handler=cmd_ablation)
    rep = sub.add_parser("report", help="write the Polish report from count_evaluation.sqlite")
    rep.add_argument("--root", default=str(PREDICTIONS_DIR))
    rep.add_argument("--out", default=str(REPORT_PATH))
    rep.add_argument("--resamples", type=int, default=2000)
    rep.add_argument("--corp-sims", type=int, default=200)
    rep.set_defaults(handler=cmd_report)
    fin = sub.add_parser("finalize", help="write forecasts and evaluation tables from finished markets")
    fin.add_argument("--root", default=str(PREDICTIONS_DIR))
    fin.set_defaults(handler=cmd_finalize)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args.handler(args)


if __name__ == "__main__":
    main()
