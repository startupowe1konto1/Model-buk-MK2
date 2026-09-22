from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from model_buk.opportunity_selector import OpportunityCandidate, select_opportunities


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def candidate(
    fixture_id: str,
    market_key: str,
    *,
    lower: float = 0.60,
    probability: float | None = None,
    odds: float = 2.0,
    observed_at: datetime = NOW,
    snapshot_id: str = "snapshot-1",
    correlation_group: str | None = None,
    tax_multiplier: float = 1.0,
    bookmaker: str = "book-a",
    market_probability: float | None = None,
) -> OpportunityCandidate:
    return OpportunityCandidate(
        fixture_id=fixture_id,
        market_key=market_key,
        selection="over",
        decimal_odds=odds,
        probability=max(lower, 0.63) if probability is None else probability,
        probability_lower_bound=lower,
        observed_at=observed_at,
        snapshot_id=snapshot_id,
        bookmaker=bookmaker,
        correlation_group=correlation_group,
        tax_multiplier=tax_multiplier,
        market_probability=market_probability,
    )


def test_returns_at_most_n_and_never_pads_to_n() -> None:
    result = select_opportunities(
        [
            candidate("m1", "goals.over.2.5"),
            candidate("m2", "corners.over.9.5", lower=0.40, odds=2.0),
        ],
        max_recommendations=10,
    )
    assert len(result.selected) == 1
    assert result.selected[0].candidate.fixture_id == "m1"
    assert result.rejection_counts == {"conservative_ev_below_threshold": 1}


def test_ranks_by_conservative_net_ev_and_applies_portfolio_limit() -> None:
    result = select_opportunities(
        [
            candidate("m1", "a", lower=0.56, odds=2.0),
            candidate("m2", "b", lower=0.62, odds=2.0),
            candidate("m3", "c", lower=0.59, odds=2.0),
        ],
        max_recommendations=2,
    )
    assert [item.candidate.fixture_id for item in result.selected] == ["m2", "m3"]
    assert result.rejection_counts == {"portfolio_limit": 1}


def test_net_ev_gate_uses_probability_lower_bound_and_tax() -> None:
    result = select_opportunities(
        [candidate("m1", "a", lower=0.55, probability=0.70, odds=1.93, tax_multiplier=0.88)]
    )
    assert result.selected == ()
    assert result.rejection_counts == {"conservative_ev_below_threshold": 1}


def test_fresh_snapshot_supersedes_stale_better_looking_quote() -> None:
    stale = candidate(
        "m1",
        "goals.over.2.5",
        lower=0.60,
        odds=2.2,
        observed_at=NOW - timedelta(hours=3),
        snapshot_id="old",
    )
    fresh = candidate(
        "m1",
        "goals.over.2.5",
        lower=0.60,
        odds=1.6,
        observed_at=NOW,
        snapshot_id="new",
    )
    result = select_opportunities([stale, fresh])
    assert result.selected == ()
    assert result.rejection_counts == {
        "superseded_snapshot": 1,
        "conservative_ev_below_threshold": 1,
    }


def test_same_fixture_and_correlation_group_keeps_only_best_candidate() -> None:
    result = select_opportunities(
        [
            candidate("m1", "goals.over.2.5", lower=0.62, correlation_group="goals_high"),
            candidate("m1", "btts.yes", lower=0.58, correlation_group="goals_high"),
            candidate("m1", "cards.over.4.5", lower=0.57, correlation_group="cards_high"),
        ],
        max_per_fixture=2,
    )
    assert [item.candidate.market_key for item in result.selected] == [
        "goals.over.2.5",
        "cards.over.4.5",
    ]
    assert result.rejection_counts == {"correlation_limit": 1}


def test_fixture_limit_caps_otherwise_distinct_groups() -> None:
    result = select_opportunities(
        [
            candidate("m1", "a", lower=0.64, correlation_group="g1"),
            candidate("m1", "b", lower=0.62, correlation_group="g2"),
            candidate("m1", "c", lower=0.60, correlation_group="g3"),
        ],
        max_per_fixture=2,
    )
    assert len(result.selected) == 2
    assert result.rejection_counts == {"fixture_limit": 1}


def test_invalid_probability_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="probabilities must satisfy"):
        candidate("m1", "a", lower=0.71, probability=0.70)


@pytest.mark.parametrize("market_probability", [-0.01, 1.01])
def test_invalid_market_probability_is_rejected(market_probability: float) -> None:
    with pytest.raises(ValueError, match="market_probability must be in"):
        candidate("m1", "a", market_probability=market_probability)


def test_market_edge_gate_rejects_candidate_without_market_probability() -> None:
    result = select_opportunities(
        [candidate("m1", "a")],
        min_conservative_market_edge=0.01,
    )

    assert result.selected == ()
    assert result.rejection_counts == {"market_probability_unavailable": 1}


def test_market_edge_gate_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError, match="min_conservative_market_edge must be non-negative"):
        select_opportunities([], min_conservative_market_edge=-0.01)


def test_market_edge_gate_is_strict_and_uses_probability_lower_bound() -> None:
    result = select_opportunities(
        [
            candidate("m1", "a", lower=0.60, probability=0.80, market_probability=0.55),
            candidate("m2", "b", lower=0.61, probability=0.61, market_probability=0.55),
        ],
        min_conservative_market_edge=0.05,
    )

    assert [item.candidate.fixture_id for item in result.selected] == ["m2"]
    assert result.selected[0].candidate.conservative_market_edge == pytest.approx(0.06)
    assert result.rejection_counts == {"conservative_market_edge_below_threshold": 1}


def test_keeps_latest_quote_per_bookmaker_then_best_executable_price():
    candidates = [
        candidate(
            "m1",
            "goals.over.2.5",
            odds=2.10,
            bookmaker="book-a",
            observed_at=NOW - timedelta(minutes=10),
            snapshot_id="a-old",
        ),
        candidate(
            "m1",
            "goals.over.2.5",
            odds=2.00,
            bookmaker="book-a",
            observed_at=NOW,
            snapshot_id="a-new",
        ),
        candidate(
            "m1",
            "goals.over.2.5",
            odds=2.05,
            bookmaker="book-b",
            observed_at=NOW - timedelta(minutes=2),
            snapshot_id="b-new",
        ),
    ]

    result = select_opportunities(candidates)

    assert len(result.selected) == 1
    assert result.selected[0].candidate.bookmaker == "book-b"
    assert result.rejection_counts == {"superseded_snapshot": 1, "inferior_price": 1}


def test_freshness_gate_rejects_old_quote() -> None:
    result = select_opportunities(
        [candidate("m1", "a", observed_at=NOW - timedelta(minutes=31))],
        decision_time=NOW,
        max_quote_age=timedelta(minutes=30),
    )
    assert result.selected == ()
    assert result.rejection_counts == {"stale_quote": 1}


def test_quote_from_future_relative_to_decision_time_is_invalid() -> None:
    with pytest.raises(ValueError, match="observed_at cannot be after decision_time"):
        select_opportunities(
            [candidate("m1", "a", observed_at=NOW + timedelta(seconds=1))],
            decision_time=NOW,
            max_quote_age=timedelta(minutes=30),
        )


def test_freshness_gate_is_disabled_by_default() -> None:
    result = select_opportunities(
        [candidate("m1", "a", observed_at=NOW - timedelta(days=30))]
    )
    assert len(result.selected) == 1
