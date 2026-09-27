from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from model_buk.evaluation import historical_replay
from model_buk.evaluation.historical_replay import (
    HistoricalOpportunity,
    NestedReplayFold,
    ReplayFold,
    ReplayStrategy,
    nested_walk_forward_replay,
    replay_opportunities,
)
from model_buk.opportunity_selector import OpportunityCandidate
from model_buk.opportunity_selector import select_opportunities as production_select


BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def candidate(
    fixture: str,
    market: str,
    selection: str,
    *,
    lower: float,
    probability: float | None = None,
    odds: float = 2.0,
    cutoff: datetime = BASE,
    bookmaker: str = "Book",
    tax_multiplier: float = 1.0,
    execution_cost: float = 0.0,
) -> OpportunityCandidate:
    return OpportunityCandidate(
        fixture_id=fixture,
        market_key=market,
        selection=selection,
        decimal_odds=odds,
        probability=probability if probability is not None else lower,
        probability_lower_bound=lower,
        observed_at=cutoff - timedelta(minutes=2),
        snapshot_id=f"{fixture}:{market}:{selection}:{cutoff.isoformat()}",
        bookmaker=bookmaker,
        tax_multiplier=tax_multiplier,
        execution_cost=execution_cost,
        correlation_group=market,
    )


def opportunity(
    fixture: str,
    market: str,
    selection: str,
    *,
    lower: float,
    settlement: str,
    cutoff: datetime = BASE,
    odds: float = 2.0,
    tax_multiplier: float = 1.0,
    execution_cost: float = 0.0,
    closing_odds: float | None = None,
) -> HistoricalOpportunity:
    return HistoricalOpportunity(
        cutoff=cutoff,
        kickoff=cutoff + timedelta(hours=5),
        candidate=candidate(
            fixture,
            market,
            selection,
            lower=lower,
            odds=odds,
            cutoff=cutoff,
            tax_multiplier=tax_multiplier,
            execution_cost=execution_cost,
        ),
        settlement=settlement,  # type: ignore[arg-type]
        market_group_id=f"{fixture}:{market}",
        market_size=2,
        closing_odds=closing_odds,
        cluster_id=cutoff.isoformat(),
    )


def two_way_market(
    fixture: str,
    market: str,
    *,
    cutoff: datetime,
    value_lower: float,
    value_settlement: str,
    other_settlement: str = "loss",
    closing_odds: float | None = None,
) -> tuple[HistoricalOpportunity, HistoricalOpportunity]:
    return (
        opportunity(
            fixture,
            market,
            "over",
            lower=value_lower,
            settlement=value_settlement,
            cutoff=cutoff,
            closing_odds=closing_odds,
        ),
        opportunity(
            fixture,
            market,
            "under",
            lower=0.30,
            settlement=other_settlement,
            cutoff=cutoff,
            closing_odds=closing_odds,
        ),
    )


def test_replay_calls_production_selector_once_per_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime] = []

    def spy(candidates, **kwargs):
        calls.append(kwargs["decision_time"])
        return production_select(candidates, **kwargs)

    monkeypatch.setattr(historical_replay, "select_opportunities", spy)
    first = BASE
    second = BASE + timedelta(days=1)
    opportunities = (
        *two_way_market("f1", "totals-25", cutoff=first, value_lower=0.56, value_settlement="win"),
        *two_way_market("f2", "totals-25", cutoff=second, value_lower=0.57, value_settlement="loss"),
    )
    strategy = ReplayStrategy(
        name="top1",
        devig_method="proportional",
        min_conservative_ev_net=0.0,
        max_recommendations=1,
    )

    result = replay_opportunities(opportunities, strategy, bootstrap_resamples=25)

    assert calls == [first, second]
    assert result.selected == 2
    assert result.rejection_counts["conservative_market_edge_below_threshold"] == 2
    assert result.ledger[result.ledger["selected"]]["selection"].tolist() == ["over", "over"]


def test_replay_strategy_enforces_max_decimal_odds_before_selection() -> None:
    opportunities = (
        opportunity(
            "f1", "totals-25", "over", lower=0.60, settlement="win", odds=2.01
        ),
        opportunity(
            "f1", "totals-25", "under", lower=0.30, settlement="loss", odds=1.60
        ),
    )
    strategy = ReplayStrategy(
        "capped", "proportional", 0.0, 3, max_decimal_odds=2.0
    )

    result = replay_opportunities(opportunities, strategy, bootstrap_resamples=25)

    assert result.selected == 0
    assert result.rejection_counts["decimal_odds_above_limit"] == 1


def test_replay_rejects_closing_as_execution_price_and_ignores_closing_odds() -> None:
    with pytest.raises(ValueError, match="closing is evaluation-only"):
        HistoricalOpportunity(
            cutoff=BASE,
            kickoff=BASE + timedelta(hours=5),
            candidate=candidate("f1", "totals-25", "over", lower=0.56),
            settlement="win",
            market_group_id="f1:totals-25",
            market_size=2,
            execution_price_kind="closing",  # type: ignore[arg-type]
        )

    strategy = ReplayStrategy(
        name="top1",
        devig_method="proportional",
        min_conservative_ev_net=0.0,
        max_recommendations=1,
    )
    base_market = two_way_market(
        "f1",
        "totals-25",
        cutoff=BASE,
        value_lower=0.56,
        value_settlement="win",
        closing_odds=1.50,
    )
    moved_closing = tuple(replace(item, closing_odds=5.00) for item in base_market)

    first = replay_opportunities(base_market, strategy, bootstrap_resamples=25)
    second = replay_opportunities(moved_closing, strategy, bootstrap_resamples=25)

    assert first.selected == second.selected == 1
    assert first.total_profit == second.total_profit == pytest.approx(1.0)
    assert first.ledger.loc[first.ledger["selected"], "decimal_odds"].tolist() == [2.0]
    assert second.ledger.loc[second.ledger["selected"], "decimal_odds"].tolist() == [2.0]


def test_replay_settlement_uses_execution_odds_tax_and_cost() -> None:
    strategy = ReplayStrategy(
        name="top1",
        devig_method="proportional",
        min_conservative_ev_net=0.0,
        max_recommendations=1,
    )
    opportunities = (
        opportunity(
            "f1",
            "totals-25",
            "over",
            lower=0.59,
            settlement="win",
            cutoff=BASE,
            odds=2.0,
            tax_multiplier=0.88,
            execution_cost=0.02,
        ),
        opportunity(
            "f1",
            "totals-25",
            "under",
            lower=0.30,
            settlement="loss",
            cutoff=BASE,
            odds=2.0,
            tax_multiplier=0.88,
            execution_cost=0.02,
        ),
    )

    result = replay_opportunities(opportunities, strategy, bootstrap_resamples=25)

    assert result.selected == 1
    assert result.total_profit == pytest.approx(0.74)
    assert result.yield_rate == pytest.approx(0.74)


def test_nested_replay_tunes_top_n_only_on_inner_folds() -> None:
    inner_one = BASE
    inner_two = BASE + timedelta(days=1)
    outer = BASE + timedelta(days=2)
    opportunities = (
        *two_way_market("i1a", "totals-25", cutoff=inner_one, value_lower=0.60, value_settlement="win"),
        *two_way_market("i1b", "totals-25", cutoff=inner_one, value_lower=0.59, value_settlement="loss"),
        *two_way_market("i1c", "totals-25", cutoff=inner_one, value_lower=0.58, value_settlement="loss"),
        *two_way_market("i2a", "totals-25", cutoff=inner_two, value_lower=0.60, value_settlement="win"),
        *two_way_market("i2b", "totals-25", cutoff=inner_two, value_lower=0.59, value_settlement="loss"),
        *two_way_market("i2c", "totals-25", cutoff=inner_two, value_lower=0.58, value_settlement="loss"),
        *two_way_market("o1a", "totals-25", cutoff=outer, value_lower=0.60, value_settlement="loss"),
        *two_way_market("o1b", "totals-25", cutoff=outer, value_lower=0.59, value_settlement="win"),
        *two_way_market("o1c", "totals-25", cutoff=outer, value_lower=0.58, value_settlement="win"),
    )
    strategies = (
        ReplayStrategy("top1", "proportional", 0.0, 1),
        ReplayStrategy("top3", "proportional", 0.0, 3),
    )
    folds = (
        NestedReplayFold(
            "outer-a",
            ReplayFold("outer", outer, outer + timedelta(days=1)),
            (
                ReplayFold("inner-1", inner_one, inner_one + timedelta(hours=1)),
                ReplayFold("inner-2", inner_two, inner_two + timedelta(hours=1)),
            ),
        ),
    )

    result = nested_walk_forward_replay(
        opportunities,
        strategies,
        folds,
        bootstrap_resamples=50,
        min_inner_bets=1,
    )

    assert result.selections["selected_strategy"].tolist() == ["top1"]
    assert result.outer_summary["selected"].tolist() == [1]
    assert result.outer_summary["total_profit"].tolist() == pytest.approx([-1.0])
    assert set(result.inner_scores["strategy"]) == {"top1", "top3"}
