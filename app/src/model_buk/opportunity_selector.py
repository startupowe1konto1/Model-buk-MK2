from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import math
from typing import Iterable

from model_buk.markets.pricing import TaxProfiles, UnknownBookmakerError


@dataclass(frozen=True)
class OpportunityCandidate:
    """One executable market observation considered by the recommendation gate.

    ``probability_lower_bound`` is deliberately separate from the point estimate.
    Selection is based on the lower bound so that uncertainty cannot be hidden by
    a high point estimate.

    ``tax_multiplier`` defaults to 1.0 (no tax) only for backward compatibility.
    Pass ``tax_profiles`` to :func:`select_opportunities` to have the selector
    take each candidate's multiplier from its bookmaker instead (unknown
    bookmakers are then rejected rather than assumed).
    """

    fixture_id: str
    market_key: str
    selection: str
    decimal_odds: float
    probability: float
    probability_lower_bound: float
    observed_at: datetime
    snapshot_id: str
    bookmaker: str = "consensus"
    tax_multiplier: float = 1.0
    execution_cost: float = 0.0
    correlation_group: str | None = None
    market_probability: float | None = None

    def __post_init__(self) -> None:
        if not self.fixture_id.strip():
            raise ValueError("fixture_id must not be empty")
        if not self.market_key.strip():
            raise ValueError("market_key must not be empty")
        if not self.selection.strip():
            raise ValueError("selection must not be empty")
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id must not be empty")
        if not self.bookmaker.strip():
            raise ValueError("bookmaker must not be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not math.isfinite(float(self.decimal_odds)) or self.decimal_odds <= 1.0:
            raise ValueError("decimal_odds must be finite and greater than 1.0")
        if not 0.0 <= self.probability_lower_bound <= self.probability <= 1.0:
            raise ValueError(
                "probabilities must satisfy 0 <= probability_lower_bound <= probability <= 1"
            )
        if not 0.0 < self.tax_multiplier <= 1.0:
            raise ValueError("tax_multiplier must be in (0, 1]")
        if self.execution_cost < 0.0:
            raise ValueError("execution_cost must be non-negative")
        if self.market_probability is not None and not 0.0 <= self.market_probability <= 1.0:
            raise ValueError("market_probability must be in [0, 1]")

    @property
    def logical_key(self) -> tuple[str, str, str]:
        """Stable outcome identity shared by quotes from different bookmakers."""
        return self.fixture_id, self.market_key, self.selection

    @property
    def quote_key(self) -> tuple[str, str, str, str]:
        """Stable quote identity across repeated snapshots of one bookmaker."""
        return (*self.logical_key, self.bookmaker)

    @property
    def conservative_ev_net(self) -> float:
        return (
            self.probability_lower_bound * self.tax_multiplier * self.decimal_odds
            - 1.0
            - self.execution_cost
        )

    @property
    def conservative_market_edge(self) -> float | None:
        if self.market_probability is None:
            return None
        return self.probability_lower_bound - self.market_probability


@dataclass(frozen=True)
class SelectedOpportunity:
    candidate: OpportunityCandidate
    conservative_ev_net: float


@dataclass(frozen=True)
class RejectedOpportunity:
    candidate: OpportunityCandidate
    reason: str


@dataclass(frozen=True)
class OpportunitySelection:
    selected: tuple[SelectedOpportunity, ...]
    rejected: tuple[RejectedOpportunity, ...]

    @property
    def rejection_counts(self) -> dict[str, int]:
        return dict(Counter(item.reason for item in self.rejected))


def _latest_snapshot_candidates(
    candidates: Iterable[OpportunityCandidate],
) -> tuple[list[OpportunityCandidate], list[RejectedOpportunity]]:
    """Keep only the freshest observation for each bookmaker quote.

    If two rows have the same timestamp, the higher executable price wins. This
    is deterministic and prevents an older, apparently better quote from being
    recommended after the market has moved.
    """
    current: dict[tuple[str, str, str, str], OpportunityCandidate] = {}
    rejected: list[RejectedOpportunity] = []
    for candidate in candidates:
        existing = current.get(candidate.quote_key)
        if existing is None:
            current[candidate.quote_key] = candidate
            continue
        candidate_rank = (candidate.observed_at, candidate.decimal_odds, candidate.snapshot_id)
        existing_rank = (existing.observed_at, existing.decimal_odds, existing.snapshot_id)
        if candidate_rank > existing_rank:
            rejected.append(RejectedOpportunity(existing, "superseded_snapshot"))
            current[candidate.quote_key] = candidate
        else:
            rejected.append(RejectedOpportunity(candidate, "superseded_snapshot"))
    return list(current.values()), rejected


def _apply_tax_profiles(
    candidates: Iterable[OpportunityCandidate],
    tax_profiles: TaxProfiles,
) -> tuple[list[OpportunityCandidate], list[RejectedOpportunity]]:
    """Set each candidate's tax multiplier from its bookmaker; drop unknown books."""
    resolved: list[OpportunityCandidate] = []
    rejected: list[RejectedOpportunity] = []
    for candidate in candidates:
        try:
            multiplier = tax_profiles.tax_multiplier(candidate.bookmaker)
        except UnknownBookmakerError:
            rejected.append(RejectedOpportunity(candidate, "unknown_bookmaker_tax"))
            continue
        if multiplier == candidate.tax_multiplier:
            resolved.append(candidate)
        else:
            resolved.append(replace(candidate, tax_multiplier=multiplier))
    return resolved, rejected


def _best_executable_prices(
    candidates: Iterable[OpportunityCandidate],
) -> tuple[list[OpportunityCandidate], list[RejectedOpportunity]]:
    """Keep one executable bookmaker quote for each market outcome."""
    best: dict[tuple[str, str, str], OpportunityCandidate] = {}
    rejected: list[RejectedOpportunity] = []
    for candidate in candidates:
        existing = best.get(candidate.logical_key)
        rank = (candidate.conservative_ev_net, candidate.decimal_odds, candidate.bookmaker)
        if existing is None:
            best[candidate.logical_key] = candidate
            continue
        existing_rank = (existing.conservative_ev_net, existing.decimal_odds, existing.bookmaker)
        if rank > existing_rank:
            rejected.append(RejectedOpportunity(existing, "inferior_price"))
            best[candidate.logical_key] = candidate
        else:
            rejected.append(RejectedOpportunity(candidate, "inferior_price"))
    return list(best.values()), rejected


def select_opportunities(
    candidates: Iterable[OpportunityCandidate],
    *,
    max_recommendations: int = 10,
    min_conservative_ev_net: float = 0.0,
    min_conservative_market_edge: float | None = None,
    max_decimal_odds: float = 2.0,
    max_per_fixture: int = 1,
    max_per_correlation_group: int = 1,
    decision_time: datetime | None = None,
    max_quote_age: timedelta | None = None,
    tax_profiles: TaxProfiles | None = None,
) -> OpportunitySelection:
    """Select at most N independent opportunities; never pad to a target count.

    Candidates first pass a strictly-positive conservative net-EV gate (net EV
    of the probability *lower bound*, so the gate scales with estimation
    uncertainty instead of using a fixed haircut). They are then ranked by that
    EV and greedily constrained by fixture and explicit correlation-group
    exposure. Correlation groups are scoped to a fixture.

    One pick per match is the default (``max_per_fixture=1``): the candidate
    ranked first for a fixture is kept and the rest are rejected as
    ``fixture_limit``. The ranking is total and deterministic: highest
    conservative net EV, then highest probability lower bound, then the freshest
    observation, then the smallest ``(fixture_id, market_key, selection)``.

    When ``tax_profiles`` is given, every candidate's ``tax_multiplier`` is taken
    from its bookmaker's profile (overriding the value on the candidate) and a
    candidate whose bookmaker is not registered is rejected as
    ``unknown_bookmaker_tax`` (fail closed). Without it the multiplier already
    on each candidate is used.
    """
    if max_recommendations < 0:
        raise ValueError("max_recommendations must be non-negative")
    if max_per_fixture < 1:
        raise ValueError("max_per_fixture must be at least 1")
    if max_per_correlation_group < 1:
        raise ValueError("max_per_correlation_group must be at least 1")
    if min_conservative_ev_net < 0.0:
        raise ValueError("min_conservative_ev_net must be non-negative")
    if min_conservative_market_edge is not None and min_conservative_market_edge < 0.0:
        raise ValueError("min_conservative_market_edge must be non-negative")
    if not math.isfinite(float(max_decimal_odds)) or max_decimal_odds <= 1.0:
        raise ValueError("max_decimal_odds must be finite and greater than 1.0")
    if (decision_time is None) != (max_quote_age is None):
        raise ValueError("decision_time and max_quote_age must be provided together")
    if decision_time is not None:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        if max_quote_age is None or max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")

    materialized = list(candidates)
    if decision_time is not None:
        future = [candidate for candidate in materialized if candidate.observed_at > decision_time]
        if future:
            raise ValueError("candidate observed_at cannot be after decision_time")

    unique, rejected = _latest_snapshot_candidates(materialized)
    if tax_profiles is not None:
        unique, unpriced = _apply_tax_profiles(unique, tax_profiles)
        rejected.extend(unpriced)
    within_odds_limit: list[OpportunityCandidate] = []
    for candidate in unique:
        if candidate.decimal_odds > max_decimal_odds:
            rejected.append(RejectedOpportunity(candidate, "decimal_odds_above_limit"))
        else:
            within_odds_limit.append(candidate)
    priced, inferior = _best_executable_prices(within_odds_limit)
    rejected.extend(inferior)
    eligible: list[OpportunityCandidate] = []
    for candidate in priced:
        if (
            decision_time is not None
            and max_quote_age is not None
            and decision_time - candidate.observed_at > max_quote_age
        ):
            rejected.append(RejectedOpportunity(candidate, "stale_quote"))
            continue
        if min_conservative_market_edge is not None:
            market_edge = candidate.conservative_market_edge
            if market_edge is None:
                rejected.append(RejectedOpportunity(candidate, "market_probability_unavailable"))
                continue
            if market_edge <= min_conservative_market_edge:
                rejected.append(
                    RejectedOpportunity(candidate, "conservative_market_edge_below_threshold")
                )
                continue
        # A threshold of zero still means strictly positive EV; break-even is not
        # an opportunity after estimation and execution uncertainty.
        if candidate.conservative_ev_net <= min_conservative_ev_net:
            rejected.append(RejectedOpportunity(candidate, "conservative_ev_below_threshold"))
        else:
            eligible.append(candidate)

    eligible.sort(
        key=lambda candidate: (
            -candidate.conservative_ev_net,
            -candidate.probability_lower_bound,
            -candidate.observed_at.timestamp(),
            candidate.logical_key,
        )
    )

    selected: list[SelectedOpportunity] = []
    fixture_counts: Counter[str] = Counter()
    group_counts: Counter[tuple[str, str]] = Counter()
    for candidate in eligible:
        if len(selected) >= max_recommendations:
            rejected.append(RejectedOpportunity(candidate, "portfolio_limit"))
            continue
        if fixture_counts[candidate.fixture_id] >= max_per_fixture:
            rejected.append(RejectedOpportunity(candidate, "fixture_limit"))
            continue
        if candidate.correlation_group is not None:
            group_key = (candidate.fixture_id, candidate.correlation_group)
            if group_counts[group_key] >= max_per_correlation_group:
                rejected.append(RejectedOpportunity(candidate, "correlation_limit"))
                continue
            group_counts[group_key] += 1
        fixture_counts[candidate.fixture_id] += 1
        selected.append(SelectedOpportunity(candidate, candidate.conservative_ev_net))

    return OpportunitySelection(tuple(selected), tuple(rejected))
