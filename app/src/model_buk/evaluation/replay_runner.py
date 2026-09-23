"""Plan-driven CLI service for nested historical opportunity replay."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from typing import Any, Mapping

from model_buk.evaluation.historical_replay import (
    NestedReplayFold,
    NestedReplayResult,
    ReplayFold,
    ReplayStrategy,
    HistoricalOpportunity,
    nested_walk_forward_replay,
    replay_strategy_grid,
)
from model_buk.evaluation.gates import bootstrap_cluster_mean_interval
from model_buk.evaluation.replay_data import ReplayDataLoadResult, load_replay_data


def _object(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {', '.join(unknown)}")


def _fold(value: Any, *, field: str) -> ReplayFold:
    row = _object(value, field=field)
    _keys(row, {"label", "start", "end"}, field=field)
    try:
        return ReplayFold(label=row["label"], start=row["start"], end=row["end"])
    except KeyError as exc:
        raise ValueError(f"{field} is missing {exc.args[0]}") from None


def _strategy(value: Any, index: int) -> ReplayStrategy:
    field = f"strategies[{index}]"
    row = _object(value, field=field)
    allowed = {
        "name",
        "devig_method",
        "min_conservative_ev_net",
        "max_recommendations",
        "min_conservative_market_edge",
        "max_per_fixture",
        "max_per_correlation_group",
        "max_quote_age_seconds",
    }
    _keys(row, allowed, field=field)
    try:
        return ReplayStrategy(
            name=row["name"],
            devig_method=row["devig_method"],
            min_conservative_ev_net=row["min_conservative_ev_net"],
            max_recommendations=row["max_recommendations"],
            min_conservative_market_edge=row.get("min_conservative_market_edge", 0.0),
            max_per_fixture=row.get("max_per_fixture", 2),
            max_per_correlation_group=row.get("max_per_correlation_group", 1),
            max_quote_age=timedelta(seconds=row.get("max_quote_age_seconds", 1800)),
        )
    except KeyError as exc:
        raise ValueError(f"{field} is missing {exc.args[0]}") from None


def _nested_fold(value: Any, index: int) -> NestedReplayFold:
    field = f"folds[{index}]"
    row = _object(value, field=field)
    _keys(row, {"label", "outer_fold", "inner_folds"}, field=field)
    try:
        inner_values = row["inner_folds"]
        if not isinstance(inner_values, list):
            raise ValueError(f"{field}.inner_folds must be a JSON array")
        return NestedReplayFold(
            label=row["label"],
            outer_fold=_fold(row["outer_fold"], field=f"{field}.outer_fold"),
            inner_folds=tuple(
                _fold(item, field=f"{field}.inner_folds[{inner_index}]")
                for inner_index, item in enumerate(inner_values)
            ),
        )
    except KeyError as exc:
        raise ValueError(f"{field} is missing {exc.args[0]}") from None


def _parse_plan(path: str | Path) -> tuple[list[ReplayStrategy], list[NestedReplayFold], dict[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read replay plan: {exc}") from None
    plan = _object(value, field="plan")
    _keys(plan, {"strategies", "strategy_grid", "folds", "evaluation"}, field="plan")
    strategies_value = plan.get("strategies")
    grid_value = plan.get("strategy_grid")
    folds_value = plan.get("folds")
    evaluation_value = _object(plan.get("evaluation", {}), field="evaluation")
    _keys(
        evaluation_value,
        {"min_inner_bets", "confidence_level", "bootstrap_resamples", "seed"},
        field="evaluation",
    )
    if (strategies_value is None) == (grid_value is None):
        raise ValueError("plan must define exactly one of strategies or strategy_grid")
    if not isinstance(folds_value, list) or not folds_value:
        raise ValueError("plan.folds must be a non-empty JSON array")
    if strategies_value is not None:
        if not isinstance(strategies_value, list) or not strategies_value:
            raise ValueError("plan.strategies must be a non-empty JSON array")
        strategies = [_strategy(item, index) for index, item in enumerate(strategies_value)]
    else:
        grid = _object(grid_value, field="strategy_grid")
        _keys(
            grid,
            {
                "devig_methods",
                "ev_thresholds",
                "top_ns",
                "min_conservative_market_edge",
                "max_quote_age_seconds",
            },
            field="strategy_grid",
        )
        for field in ("devig_methods", "ev_thresholds", "top_ns"):
            if not isinstance(grid.get(field), list) or not grid[field]:
                raise ValueError(f"strategy_grid.{field} must be a non-empty JSON array")
        strategies = list(
            replay_strategy_grid(
                devig_methods=grid["devig_methods"],
                ev_thresholds=grid["ev_thresholds"],
                top_ns=grid["top_ns"],
                min_conservative_market_edge=grid.get("min_conservative_market_edge", 0.0),
                max_quote_age=timedelta(seconds=grid.get("max_quote_age_seconds", 1800)),
            )
        )
    folds = [_nested_fold(item, index) for index, item in enumerate(folds_value)]
    evaluation = {
        "min_inner_bets": evaluation_value.get("min_inner_bets", 1),
        "confidence_level": evaluation_value.get("confidence_level", 0.95),
        "bootstrap_resamples": evaluation_value.get("bootstrap_resamples", 2000),
        "seed": evaluation_value.get("seed", 0),
    }
    return strategies, folds, evaluation


def run_historical_replay(
    input_path: str | Path,
    plan_path: str | Path,
    outdir: str | Path,
    *,
    allow_quarantine: bool = False,
    by_stratum: bool = False,
) -> dict[str, Any]:
    """Run a nested replay and write tabular evidence plus a provenance manifest."""
    loaded: ReplayDataLoadResult = load_replay_data(input_path)
    if loaded.quarantine and not allow_quarantine:
        raise ValueError(
            f"input contains {len(loaded.quarantine)} quarantined rows; "
            "fix them or pass --allow-quarantine to continue"
        )
    strategies, folds, evaluation = _parse_plan(plan_path)
    replay_args = {
        "min_inner_bets": evaluation["min_inner_bets"],
        "confidence_level": evaluation["confidence_level"],
        "bootstrap_resamples": evaluation["bootstrap_resamples"],
        "seed": evaluation["seed"],
    }
    if by_stratum:
        if any(item.stratum_id is None for item in loaded.opportunities):
            raise ValueError("--by-stratum requires every input row to have a stratum_id")
        grouped: dict[str, list[HistoricalOpportunity]] = {}
        for item in loaded.opportunities:
            assert item.stratum_id is not None
            grouped.setdefault(item.stratum_id, []).append(item)
        group_results: list[tuple[str, NestedReplayResult]] = []
        skipped_strata: list[dict[str, str]] = []
        for index, (stratum, opportunities) in enumerate(sorted(grouped.items())):
            try:
                group_result = nested_walk_forward_replay(
                        opportunities,
                        strategies,
                        folds,
                        **{**replay_args, "seed": int(evaluation["seed"]) + index * 100_000},
                    )
            except ValueError as exc:
                skipped_strata.append({"stratum_id": stratum, "reason": str(exc)})
                continue
            group_results.append((stratum, group_result))
        if not group_results:
            reasons = "; ".join(f"{row['stratum_id']}: {row['reason']}" for row in skipped_strata)
            raise ValueError(f"no stratum produced a valid nested replay: {reasons}")
        import pandas as pd

        inner_scores = []
        selections = []
        outer_ledgers = []
        outer_summaries = []
        for stratum, group_result in group_results:
            for frame, bucket in (
                (group_result.inner_scores, inner_scores),
                (group_result.selections, selections),
                (group_result.outer_ledger, outer_ledgers),
                (group_result.outer_summary, outer_summaries),
            ):
                tagged = frame.copy()
                tagged["stratum_id"] = stratum
                bucket.append(tagged)
        combined_ledger = pd.concat(outer_ledgers, ignore_index=True)
        settled = combined_ledger[
            combined_ledger["selected"] & combined_ledger["settlement"].isin(["win", "loss"])
        ]
        combined_ci = None
        if not settled.empty:
            combined_ci = bootstrap_cluster_mean_interval(
                settled["net_return"].to_numpy(dtype=float),
                settled["cluster_id"].tolist(),
                confidence_level=evaluation["confidence_level"],
                n_resamples=evaluation["bootstrap_resamples"],
                seed=int(evaluation["seed"]) + 1_000_000,
            )
        result = NestedReplayResult(
            inner_scores=pd.concat(inner_scores, ignore_index=True),
            selections=pd.concat(selections, ignore_index=True),
            outer_ledger=combined_ledger,
            outer_summary=pd.concat(outer_summaries, ignore_index=True),
            outer_yield_ci=combined_ci,
        )
    else:
        skipped_strata = []
        result = nested_walk_forward_replay(loaded.opportunities, strategies, folds, **replay_args)

    output = Path(outdir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    result.inner_scores.to_csv(output / "inner_scores.csv", index=False)
    result.selections.to_csv(output / "strategy_selections.csv", index=False)
    result.outer_ledger.to_csv(output / "outer_ledger.csv", index=False)
    result.outer_summary.to_csv(output / "outer_summary.csv", index=False)
    quarantine_rows = [asdict(issue) for issue in loaded.quarantine]
    import pandas as pd

    pd.DataFrame(quarantine_rows, columns=("row_number", "code", "message")).to_csv(
        output / "quarantine.csv", index=False
    )
    pd.DataFrame(skipped_strata, columns=("stratum_id", "reason")).to_csv(
        output / "skipped_strata.csv", index=False
    )
    manifest = {
        "status": "completed",
        "data": dict(loaded.manifest()),
        "promotion_eligible": not bool(loaded.source_assumptions),
        "promotion_blockers": (
            ["source quote timestamp was assumed; use only as research replay"]
            if loaded.source_assumptions
            else []
        ),
        "plan_path": str(Path(plan_path).resolve()),
        "evaluation": evaluation,
        "by_stratum": by_stratum,
        "skipped_strata": skipped_strata,
        "strategies": [dict(strategy.as_dict()) for strategy in strategies],
        "folds": [
            {
                "label": fold.label,
                "outer_fold": {
                    "label": fold.outer_fold.label,
                    "start": fold.outer_fold.start.isoformat(),
                    "end": fold.outer_fold.end.isoformat(),
                },
                "inner_folds": [
                    {"label": item.label, "start": item.start.isoformat(), "end": item.end.isoformat()}
                    for item in fold.inner_folds
                ],
            }
            for fold in folds
        ],
        "selected_strategies": result.selections.to_dict(orient="records"),
        "outer_yield_ci": asdict(result.outer_yield_ci) if result.outer_yield_ci else None,
        "reports": [
            "inner_scores.csv",
            "strategy_selections.csv",
            "outer_ledger.csv",
            "outer_summary.csv",
            "quarantine.csv",
            "skipped_strata.csv",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return manifest


__all__ = ["run_historical_replay"]
