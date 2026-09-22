"""Leakage-safe historical replay of the production opportunity selector.

The unit of a replay decision is a cutoff, not a fixture.  Every candidate
visible at the cutoff is passed to :func:`select_opportunities` in one call so
that portfolio, fixture and correlation limits match the paper-scan path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import math
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

import numpy as np
import pandas as pd

from model_buk.evaluation.gates import BootstrapInterval, bootstrap_cluster_mean_interval
from model_buk.markets.pricing import DEVIG_METHODS, devig_probabilities
from model_buk.opportunity_selector import OpportunityCandidate, select_opportunities


Settlement = Literal["win", "loss", "void", "unsettled"]
ExecutionPriceKind = Literal["snapshot", "opening"]
_TOP_N_GRID = frozenset({1, 3, 5, 10})


def _aware_utc(value: datetime | str | pd.Timestamp, *, field: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field} must be a valid timezone-aware timestamp")
    return timestamp.tz_convert("UTC").to_pydatetime()


@dataclass(frozen=True, slots=True)
class HistoricalOpportunity:
    """One candidate as it was knowable at a historical decision cutoff.

    ``closing_odds`` is diagnostic metadata only.  It is never copied into the
    production candidate and therefore cannot affect selection or realised ROI.
    """

    cutoff: datetime
    kickoff: datetime
    candidate: OpportunityCandidate
    settlement: Settlement
    market_group_id: str
    market_size: int
    execution_price_kind: ExecutionPriceKind = "snapshot"
    closing_odds: float | None = None
    cluster_id: str | None = None

    def __post_init__(self) -> None:
        cutoff = _aware_utc(self.cutoff, field="cutoff")
        kickoff = _aware_utc(self.kickoff, field="kickoff")
        if cutoff >= kickoff:
            raise ValueError("cutoff must be earlier than kickoff")
        if self.candidate.observed_at > cutoff:
            raise ValueError("candidate observed_at cannot be after cutoff")
        if self.execution_price_kind not in {"snapshot", "opening"}:
            raise ValueError("execution_price_kind must be 'snapshot' or 'opening'; closing is evaluation-only")
        if self.settlement not in {"win", "loss", "void", "unsettled"}:
            raise ValueError("settlement must be win, loss, void or unsettled")
        group = str(self.market_group_id).strip()
        if not group:
            raise ValueError("market_group_id must not be blank")
        if not isinstance(self.market_size, int) or isinstance(self.market_size, bool) or self.market_size < 2:
            raise ValueError("market_size must be an integer of at least 2")
        if self.closing_odds is not None:
            closing = float(self.closing_odds)
            if not math.isfinite(closing) or closing <= 1.0:
                raise ValueError("closing_odds must be finite and greater than 1")
            object.__setattr__(self, "closing_odds", closing)
        cluster = str(self.cluster_id).strip() if self.cluster_id is not None else cutoff.isoformat()
        if not cluster:
            raise ValueError("cluster_id must not be blank")
        object.__setattr__(self, "cutoff", cutoff)
        object.__setattr__(self, "kickoff", kickoff)
        object.__setattr__(self, "market_group_id", group)
        object.__setattr__(self, "cluster_id", cluster)


@dataclass(frozen=True, slots=True)
class ReplayStrategy:
    """A deployable selector/de-vig policy chosen only on inner folds."""

    name: str
    devig_method: str
    min_conservative_ev_net: float
    max_recommendations: int
    min_conservative_market_edge: float | None = 0.0
    max_per_fixture: int = 2
    max_per_correlation_group: int = 1
    max_quote_age: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("strategy name must not be blank")
        if self.devig_method not in DEVIG_METHODS:
            raise ValueError(f"unknown devig_method: {self.devig_method}")
        if not isinstance(self.max_recommendations, int) or isinstance(self.max_recommendations, bool):
            raise ValueError("max_recommendations must be one of 1, 3, 5 or 10")
        if self.max_recommendations not in _TOP_N_GRID:
            raise ValueError("max_recommendations must be one of 1, 3, 5 or 10")
        if not math.isfinite(float(self.min_conservative_ev_net)) or self.min_conservative_ev_net < 0:
            raise ValueError("min_conservative_ev_net must be finite and non-negative")
        if self.min_conservative_market_edge is not None and (
            not math.isfinite(float(self.min_conservative_market_edge))
            or self.min_conservative_market_edge < 0
        ):
            raise ValueError("min_conservative_market_edge must be finite and non-negative or None")
        if self.max_per_fixture < 1 or self.max_per_correlation_group < 1:
            raise ValueError("portfolio limits must be positive")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        object.__setattr__(self, "name", name)

    def as_dict(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "name": self.name,
                "devig_method": self.devig_method,
                "min_conservative_ev_net": float(self.min_conservative_ev_net),
                "max_recommendations": self.max_recommendations,
                "min_conservative_market_edge": self.min_conservative_market_edge,
                "max_per_fixture": self.max_per_fixture,
                "max_per_correlation_group": self.max_per_correlation_group,
                "max_quote_age_seconds": self.max_quote_age.total_seconds(),
            }
        )


@dataclass(frozen=True, slots=True)
class ReplayResult:
    strategy: ReplayStrategy
    ledger: pd.DataFrame
    cutoff_summary: pd.DataFrame
    rejection_counts: Mapping[str, int]
    selected: int
    settled_bets: int
    void_bets: int
    unsettled_bets: int
    total_profit: float
    yield_rate: float
    yield_ci: BootstrapInterval | None
    market_log_loss: float


@dataclass(frozen=True, slots=True)
class ReplayFold:
    label: str
    start: datetime | str | pd.Timestamp
    end: datetime | str | pd.Timestamp

    def __post_init__(self) -> None:
        label = self.label.strip()
        if not label:
            raise ValueError("fold label must not be blank")
        start = _aware_utc(self.start, field="fold start")
        end = _aware_utc(self.end, field="fold end")
        if end <= start:
            raise ValueError("fold end must be later than fold start")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, cutoff: datetime) -> bool:
        return self.start <= cutoff < self.end


@dataclass(frozen=True, slots=True)
class NestedReplayFold:
    label: str
    outer_fold: ReplayFold
    inner_folds: tuple[ReplayFold, ...]

    def __post_init__(self) -> None:
        label = self.label.strip()
        inner = tuple(self.inner_folds)
        if not label:
            raise ValueError("nested fold label must not be blank")
        if not inner:
            raise ValueError("inner_folds must not be empty")
        previous_end: datetime | None = None
        labels: set[str] = set()
        for fold in inner:
            if fold.label in labels:
                raise ValueError("inner fold labels must be unique")
            if previous_end is not None and fold.start < previous_end:
                raise ValueError("inner folds must be chronological and non-overlapping")
            if fold.end > self.outer_fold.start:
                raise ValueError("every inner fold must end on or before the outer fold start")
            labels.add(fold.label)
            previous_end = fold.end
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "inner_folds", inner)


@dataclass(frozen=True, slots=True)
class NestedReplayResult:
    inner_scores: pd.DataFrame
    selections: pd.DataFrame
    outer_ledger: pd.DataFrame
    outer_summary: pd.DataFrame
    outer_yield_ci: BootstrapInterval | None


def replay_strategy_grid(
    *,
    devig_methods: Iterable[str] = ("proportional", "power", "shin"),
    ev_thresholds: Iterable[float] = (0.0, 0.02, 0.05),
    top_ns: Iterable[int] = (1, 3, 5, 10),
    min_conservative_market_edge: float | None = 0.0,
    max_quote_age: timedelta = timedelta(minutes=30),
) -> tuple[ReplayStrategy, ...]:
    """Build the auditable de-vig × EV × top-N search grid."""
    strategies: list[ReplayStrategy] = []
    for method in devig_methods:
        for threshold in ev_thresholds:
            for top_n in top_ns:
                name = f"devig={method}|ev={float(threshold):g}|top_n={int(top_n)}"
                strategies.append(
                    ReplayStrategy(
                        name=name,
                        devig_method=method,
                        min_conservative_ev_net=float(threshold),
                        max_recommendations=int(top_n),
                        min_conservative_market_edge=min_conservative_market_edge,
                        max_quote_age=max_quote_age,
                    )
                )
    if not strategies:
        raise ValueError("strategy grid must not be empty")
    names = [strategy.name for strategy in strategies]
    if len(names) != len(set(names)):
        raise ValueError("strategy grid contains duplicate configurations")
    return tuple(strategies)


def _devig_candidates(
    opportunities: tuple[HistoricalOpportunity, ...], method: str
) -> tuple[tuple[OpportunityCandidate, ...], dict[int, HistoricalOpportunity], dict[int, float]]:
    grouped: dict[tuple[datetime, str, str], list[HistoricalOpportunity]] = {}
    for item in opportunities:
        key = (item.cutoff, item.candidate.bookmaker, item.market_group_id)
        grouped.setdefault(key, []).append(item)

    market_probability_by_item: dict[int, float] = {}
    for group in grouped.values():
        expected_sizes = {item.market_size for item in group}
        if len(expected_sizes) != 1:
            raise ValueError("market_size must be consistent within a market group")
        expected_size = next(iter(expected_sizes))
        latest: dict[str, HistoricalOpportunity] = {}
        for item in group:
            selection = item.candidate.selection
            existing = latest.get(selection)
            rank = (
                item.candidate.observed_at,
                item.candidate.decimal_odds,
                item.candidate.snapshot_id,
            )
            if existing is None:
                latest[selection] = item
            else:
                existing_rank = (
                    existing.candidate.observed_at,
                    existing.candidate.decimal_odds,
                    existing.candidate.snapshot_id,
                )
                if rank > existing_rank:
                    latest[selection] = item
        if len(latest) != expected_size:
            continue
        ordered = sorted(latest.values(), key=lambda item: item.candidate.selection)
        probabilities = devig_probabilities(
            [item.candidate.decimal_odds for item in ordered], method=method
        )
        for item, probability in zip(ordered, probabilities, strict=True):
            market_probability_by_item[id(item)] = probability

    candidates: list[OpportunityCandidate] = []
    item_by_candidate: dict[int, HistoricalOpportunity] = {}
    for item in opportunities:
        probability = market_probability_by_item.get(id(item))
        candidate = replace(item.candidate, market_probability=probability)
        candidates.append(candidate)
        item_by_candidate[id(candidate)] = item
    return tuple(candidates), item_by_candidate, market_probability_by_item


def _return_for(item: HistoricalOpportunity) -> float:
    candidate = item.candidate
    if item.settlement == "win":
        return candidate.tax_multiplier * candidate.decimal_odds - 1.0 - candidate.execution_cost
    if item.settlement == "loss":
        return -1.0 - candidate.execution_cost
    if item.settlement == "void":
        return -candidate.execution_cost
    return float("nan")


def _market_log_loss(
    opportunities: tuple[HistoricalOpportunity, ...], probabilities: Mapping[int, float]
) -> float:
    grouped: dict[tuple[datetime, str, str], list[HistoricalOpportunity]] = {}
    for item in opportunities:
        if id(item) in probabilities:
            key = (item.cutoff, item.candidate.bookmaker, item.market_group_id)
            grouped.setdefault(key, []).append(item)
    losses: list[float] = []
    for group in grouped.values():
        latest_by_selection: dict[str, HistoricalOpportunity] = {}
        for item in group:
            current = latest_by_selection.get(item.candidate.selection)
            if current is None or (
                item.candidate.observed_at,
                item.candidate.decimal_odds,
                item.candidate.snapshot_id,
            ) > (
                current.candidate.observed_at,
                current.candidate.decimal_odds,
                current.candidate.snapshot_id,
            ):
                latest_by_selection[item.candidate.selection] = item
        winners = [item for item in latest_by_selection.values() if item.settlement == "win"]
        if len(winners) == 1:
            losses.append(-math.log(max(probabilities[id(winners[0])], 1e-15)))
    return float(np.mean(losses)) if losses else float("nan")


def replay_opportunities(
    opportunities: Iterable[HistoricalOpportunity],
    strategy: ReplayStrategy,
    *,
    confidence_level: float = 0.95,
    bootstrap_resamples: int = 2_000,
    seed: int = 0,
) -> ReplayResult:
    """Replay one frozen strategy, calling the production selector once per cutoff."""
    frozen = tuple(opportunities)
    if not frozen:
        raise ValueError("opportunities must not be empty")
    if not all(isinstance(item, HistoricalOpportunity) for item in frozen):
        raise TypeError("opportunities must contain HistoricalOpportunity values")
    candidates, item_by_candidate, market_probabilities = _devig_candidates(
        frozen, strategy.devig_method
    )
    ledger_rows: list[dict[str, object]] = []
    rejection_counts: Counter[str] = Counter()

    by_cutoff: dict[datetime, list[OpportunityCandidate]] = {}
    for candidate in candidates:
        item = item_by_candidate[id(candidate)]
        by_cutoff.setdefault(item.cutoff, []).append(candidate)

    for cutoff in sorted(by_cutoff):
        selection = select_opportunities(
            by_cutoff[cutoff],
            max_recommendations=strategy.max_recommendations,
            min_conservative_ev_net=strategy.min_conservative_ev_net,
            min_conservative_market_edge=strategy.min_conservative_market_edge,
            max_per_fixture=strategy.max_per_fixture,
            max_per_correlation_group=strategy.max_per_correlation_group,
            decision_time=cutoff,
            max_quote_age=strategy.max_quote_age,
        )
        selected_ids = {id(selected.candidate) for selected in selection.selected}
        reason_by_id = {id(rejected.candidate): rejected.reason for rejected in selection.rejected}
        rejection_counts.update(reason_by_id.values())
        rank_by_id = {
            id(selected.candidate): rank
            for rank, selected in enumerate(selection.selected, start=1)
        }
        for candidate in by_cutoff[cutoff]:
            item = item_by_candidate[id(candidate)]
            is_selected = id(candidate) in selected_ids
            reason = None if is_selected else reason_by_id.get(id(candidate), "not_selected")
            net_return = _return_for(item) if is_selected else float("nan")
            ledger_rows.append(
                {
                    "strategy": strategy.name,
                    "cutoff": cutoff,
                    "cluster_id": item.cluster_id,
                    "kickoff": item.kickoff,
                    "fixture_id": candidate.fixture_id,
                    "market_key": candidate.market_key,
                    "market_group_id": item.market_group_id,
                    "selection": candidate.selection,
                    "bookmaker": candidate.bookmaker,
                    "snapshot_id": candidate.snapshot_id,
                    "observed_at": candidate.observed_at,
                    "execution_price_kind": item.execution_price_kind,
                    "decimal_odds": candidate.decimal_odds,
                    "closing_odds": item.closing_odds,
                    "probability": candidate.probability,
                    "probability_lower_bound": candidate.probability_lower_bound,
                    "market_probability": candidate.market_probability,
                    "conservative_ev_net": candidate.conservative_ev_net,
                    "conservative_market_edge": candidate.conservative_market_edge,
                    "selected": is_selected,
                    "selection_rank": rank_by_id.get(id(candidate)),
                    "rejection_reason": reason,
                    "settlement": item.settlement,
                    "net_return": net_return,
                }
            )

    ledger = pd.DataFrame(ledger_rows).sort_values(
        ["cutoff", "selected", "selection_rank", "fixture_id", "market_key"],
        ascending=[True, False, True, True, True],
        na_position="last",
        ignore_index=True,
    )
    chosen = ledger[ledger["selected"]].copy()
    settled = chosen[chosen["settlement"].isin(["win", "loss"])]
    void = chosen[chosen["settlement"].eq("void")]
    unsettled = chosen[chosen["settlement"].eq("unsettled")]
    realised = chosen[chosen["settlement"].isin(["win", "loss", "void"])]
    total_profit = float(realised["net_return"].sum()) if not realised.empty else 0.0
    yield_rate = total_profit / len(settled) if len(settled) else float("nan")
    yield_ci = None
    if len(settled):
        yield_ci = bootstrap_cluster_mean_interval(
            settled["net_return"].to_numpy(dtype=float),
            settled["cluster_id"].tolist(),
            confidence_level=confidence_level,
            n_resamples=bootstrap_resamples,
            seed=seed,
        )
    cutoff_rows: list[dict[str, object]] = []
    for cutoff, rows in ledger.groupby("cutoff", sort=True):
        selected_rows = rows[rows["selected"]]
        settled_rows = selected_rows[selected_rows["settlement"].isin(["win", "loss"])]
        realised_rows = selected_rows[selected_rows["settlement"].isin(["win", "loss", "void"])]
        profit = float(realised_rows["net_return"].sum()) if not realised_rows.empty else 0.0
        cutoff_rows.append(
            {
                "strategy": strategy.name,
                "cutoff": cutoff,
                "candidates": int(len(rows)),
                "selected": int(len(selected_rows)),
                "settled_bets": int(len(settled_rows)),
                "void_bets": int(selected_rows["settlement"].eq("void").sum()),
                "unsettled_bets": int(selected_rows["settlement"].eq("unsettled").sum()),
                "total_profit": profit,
                "yield_rate": profit / len(settled_rows) if len(settled_rows) else float("nan"),
            }
        )
    return ReplayResult(
        strategy=strategy,
        ledger=ledger,
        cutoff_summary=pd.DataFrame(cutoff_rows),
        rejection_counts=MappingProxyType(dict(rejection_counts)),
        selected=int(len(chosen)),
        settled_bets=int(len(settled)),
        void_bets=int(len(void)),
        unsettled_bets=int(len(unsettled)),
        total_profit=total_profit,
        yield_rate=float(yield_rate),
        yield_ci=yield_ci,
        market_log_loss=_market_log_loss(frozen, market_probabilities),
    )


def _in_folds(
    opportunities: tuple[HistoricalOpportunity, ...], folds: tuple[ReplayFold, ...]
) -> tuple[HistoricalOpportunity, ...]:
    return tuple(item for item in opportunities if any(fold.contains(item.cutoff) for fold in folds))


def nested_walk_forward_replay(
    opportunities: Iterable[HistoricalOpportunity],
    strategies: Iterable[ReplayStrategy],
    folds: Iterable[NestedReplayFold],
    *,
    min_inner_bets: int = 1,
    confidence_level: float = 0.95,
    bootstrap_resamples: int = 2_000,
    seed: int = 0,
) -> NestedReplayResult:
    """Tune replay policies on inner cutoffs and evaluate frozen policies on outer cutoffs."""
    frozen_opportunities = tuple(opportunities)
    frozen_strategies = tuple(strategies)
    frozen_folds = tuple(folds)
    if not frozen_opportunities or not frozen_strategies or not frozen_folds:
        raise ValueError("opportunities, strategies and folds must not be empty")
    if min_inner_bets < 1:
        raise ValueError("min_inner_bets must be positive")
    names = [strategy.name for strategy in frozen_strategies]
    if len(names) != len(set(names)):
        raise ValueError("strategy names must be unique")

    inner_rows: list[dict[str, object]] = []
    selections: list[dict[str, object]] = []
    outer_ledgers: list[pd.DataFrame] = []
    outer_summaries: list[pd.DataFrame] = []
    for fold_index, nested_fold in enumerate(frozen_folds):
        inner = _in_folds(frozen_opportunities, nested_fold.inner_folds)
        outer = _in_folds(frozen_opportunities, (nested_fold.outer_fold,))
        if not inner or not outer:
            raise ValueError(f"nested fold {nested_fold.label!r} has no inner or outer opportunities")
        candidates: list[tuple[ReplayStrategy, ReplayResult]] = []
        for strategy_index, strategy in enumerate(frozen_strategies):
            result = replay_opportunities(
                inner,
                strategy,
                confidence_level=confidence_level,
                bootstrap_resamples=bootstrap_resamples,
                seed=seed + fold_index * 10_000 + strategy_index,
            )
            lower = result.yield_ci.lower if result.yield_ci is not None else float("-inf")
            eligible = result.settled_bets >= min_inner_bets
            inner_rows.append(
                {
                    "outer_fold": nested_fold.label,
                    "strategy": strategy.name,
                    "devig_method": strategy.devig_method,
                    "min_conservative_ev_net": strategy.min_conservative_ev_net,
                    "min_conservative_market_edge": strategy.min_conservative_market_edge,
                    "max_recommendations": strategy.max_recommendations,
                    "settled_bets": result.settled_bets,
                    "yield_rate": result.yield_rate,
                    "yield_ci_lower": lower if result.yield_ci is not None else np.nan,
                    "yield_ci_upper": result.yield_ci.upper if result.yield_ci is not None else np.nan,
                    "market_log_loss": result.market_log_loss,
                    "eligible": eligible,
                    "inner_fold_labels": tuple(item.label for item in nested_fold.inner_folds),
                }
            )
            if eligible:
                candidates.append((strategy, result))
        if not candidates:
            raise ValueError(f"no strategy has at least {min_inner_bets} inner settled bets for {nested_fold.label!r}")

        def rank(item: tuple[ReplayStrategy, ReplayResult]) -> tuple[float, float, int, float, str]:
            strategy, result = item
            assert result.yield_ci is not None
            market_loss = result.market_log_loss if math.isfinite(result.market_log_loss) else float("inf")
            return (
                -result.yield_ci.lower,
                -result.yield_rate,
                -result.settled_bets,
                market_loss,
                strategy.name,
            )

        winner, inner_result = min(candidates, key=rank)
        selections.append(
            {
                "outer_fold": nested_fold.label,
                "selected_strategy": winner.name,
                "selected_parameters": dict(winner.as_dict()),
                "inner_settled_bets": inner_result.settled_bets,
                "inner_yield_rate": inner_result.yield_rate,
                "inner_yield_ci_lower": inner_result.yield_ci.lower if inner_result.yield_ci else np.nan,
                "inner_market_log_loss": inner_result.market_log_loss,
                "inner_fold_labels": tuple(item.label for item in nested_fold.inner_folds),
            }
        )
        outer_result = replay_opportunities(
            outer,
            winner,
            confidence_level=confidence_level,
            bootstrap_resamples=bootstrap_resamples,
            seed=seed + 500_000 + fold_index,
        )
        ledger = outer_result.ledger.copy()
        ledger.insert(0, "outer_fold", nested_fold.label)
        outer_ledgers.append(ledger)
        summary = pd.DataFrame(
            [
                {
                    "outer_fold": nested_fold.label,
                    "selected_strategy": winner.name,
                    "selected": outer_result.selected,
                    "settled_bets": outer_result.settled_bets,
                    "void_bets": outer_result.void_bets,
                    "unsettled_bets": outer_result.unsettled_bets,
                    "total_profit": outer_result.total_profit,
                    "yield_rate": outer_result.yield_rate,
                    "yield_ci_lower": outer_result.yield_ci.lower if outer_result.yield_ci else np.nan,
                    "yield_ci_upper": outer_result.yield_ci.upper if outer_result.yield_ci else np.nan,
                    "market_log_loss": outer_result.market_log_loss,
                }
            ]
        )
        outer_summaries.append(summary)

    combined_ledger = pd.concat(outer_ledgers, ignore_index=True)
    combined_settled = combined_ledger[
        combined_ledger["selected"] & combined_ledger["settlement"].isin(["win", "loss"])
    ]
    combined_ci = None
    if not combined_settled.empty:
        combined_ci = bootstrap_cluster_mean_interval(
            combined_settled["net_return"].to_numpy(dtype=float),
            combined_settled["cluster_id"].tolist(),
            confidence_level=confidence_level,
            n_resamples=bootstrap_resamples,
            seed=seed + 1_000_000,
        )
    return NestedReplayResult(
        inner_scores=pd.DataFrame(inner_rows),
        selections=pd.DataFrame(selections),
        outer_ledger=combined_ledger,
        outer_summary=pd.concat(outer_summaries, ignore_index=True),
        outer_yield_ci=combined_ci,
    )


__all__ = [
    "HistoricalOpportunity",
    "NestedReplayFold",
    "NestedReplayResult",
    "ReplayFold",
    "ReplayResult",
    "ReplayStrategy",
    "nested_walk_forward_replay",
    "replay_opportunities",
    "replay_strategy_grid",
]
