"""Build replay rows from football-data.co.uk prices and nested OOF forecasts."""

from __future__ import annotations

from datetime import timedelta
import math

import pandas as pd

from model_buk.evaluation.historical_replay import HistoricalOpportunity
from model_buk.ingestion.football_data_uk import FootballDataUKBatch
from model_buk.opportunity_selector import OpportunityCandidate


PREDICTION_COLUMNS = frozenset(
    {"source_row_id", "outer_fold", "actual", "p_home", "p_draw", "p_away"}
)


def build_football_data_replay_frame(
    batch: FootballDataUKBatch,
    outer_predictions: pd.DataFrame,
    *,
    assume_opening_available_at_cutoff: bool = False,
    cutoff_lead: timedelta = timedelta(minutes=60),
    probability_haircut: float = 0.05,
    tax_multiplier: float = 0.88,
    execution_cost: float = 0.0,
) -> pd.DataFrame:
    """Join nested OOF 1X2 forecasts to opening and diagnostic closing prices.

    football-data.co.uk does not expose quote timestamps.  The default is
    therefore fail-closed; callers must explicitly accept the opening-price
    availability assumption. Generated rows are research proxies and are not
    eligible evidence for production promotion.
    """
    if not assume_opening_available_at_cutoff:
        raise ValueError(
            "source has no quote timestamps; explicitly set "
            "assume_opening_available_at_cutoff=True for research-only replay"
        )
    if cutoff_lead <= timedelta(0):
        raise ValueError("cutoff_lead must be positive")
    if not math.isfinite(probability_haircut) or not 0.0 <= probability_haircut < 1.0:
        raise ValueError("probability_haircut must be finite and in [0, 1)")
    missing_predictions = sorted(PREDICTION_COLUMNS - set(outer_predictions.columns))
    if missing_predictions:
        raise ValueError(f"outer predictions are missing columns: {', '.join(missing_predictions)}")
    if outer_predictions["source_row_id"].duplicated().any():
        raise ValueError("outer predictions must contain at most one OOS row per source_row_id")
    required_matches = {"source_row_id", "kickoff", "home_goals", "away_goals"}
    missing_matches = sorted(required_matches - set(batch.matches.columns))
    if missing_matches:
        raise ValueError(f"canonical matches are missing columns: {', '.join(missing_matches)}")
    required_odds = {"source_row_id", "provider", "odds_kind", "market", "selection", "odds"}
    missing_odds = sorted(required_odds - set(batch.odds.columns))
    if missing_odds:
        raise ValueError(f"canonical odds are missing columns: {', '.join(missing_odds)}")

    opening = batch.odds.loc[
        batch.odds["odds_kind"].eq("opening") & batch.odds["market"].eq("1x2")
    ].copy()
    closing = batch.odds.loc[
        batch.odds["odds_kind"].eq("closing") & batch.odds["market"].eq("1x2"),
        ["source_row_id", "provider", "selection", "odds"],
    ].rename(columns={"odds": "closing_odds"})
    if opening.duplicated(["source_row_id", "provider", "selection"]).any():
        raise ValueError("opening odds contain duplicate quote identities")
    if closing.duplicated(["source_row_id", "provider", "selection"]).any():
        raise ValueError("closing odds contain duplicate quote identities")

    matches = batch.matches.copy()
    matches["kickoff"] = pd.to_datetime(matches["kickoff"], errors="coerce", utc=True, format="mixed")
    matches = matches.dropna(subset=["source_row_id", "kickoff", "home_goals", "away_goals"])
    matches["actual"] = [
        "home" if home > away else "draw" if home == away else "away"
        for home, away in zip(matches["home_goals"], matches["away_goals"])
    ]
    forecasts = outer_predictions.copy()
    for column in ("p_home", "p_draw", "p_away"):
        forecasts[column] = pd.to_numeric(forecasts[column], errors="coerce")
    if not forecasts[["p_home", "p_draw", "p_away"]].apply(lambda col: col.map(math.isfinite)).all().all():
        raise ValueError("outer forecasts contain non-finite probabilities")
    probability_sum = forecasts[["p_home", "p_draw", "p_away"]].sum(axis=1)
    if ((forecasts[["p_home", "p_draw", "p_away"]] < 0).any().any()
            or (forecasts[["p_home", "p_draw", "p_away"]] > 1).any().any()
            or not probability_sum.between(0.999999, 1.000001).all()):
        raise ValueError("outer forecast probabilities must be in [0, 1] and sum to one")
    forecasts = forecasts.merge(
        matches[["source_row_id", "kickoff", "actual"]],
        on="source_row_id",
        how="inner",
        validate="one_to_one",
        suffixes=("_prediction", "_match"),
    )
    if forecasts.empty:
        raise ValueError("no nested OOS forecasts match the supplied historical archive")
    if forecasts["actual_prediction"].ne(forecasts["actual_match"]).any():
        raise ValueError("forecast settlements do not match canonical historical results")
    kickoff_column = "kickoff_match" if "kickoff_match" in forecasts.columns else "kickoff"
    forecasts["kickoff"] = pd.to_datetime(
        forecasts[kickoff_column], errors="coerce", utc=True, format="mixed"
    )
    if forecasts["kickoff"].isna().any():
        raise ValueError("matched OOS forecasts contain invalid kickoff timestamps")

    joined = opening.merge(forecasts, on="source_row_id", how="inner", validate="many_to_one")
    joined = joined.merge(
        closing,
        on=["source_row_id", "provider", "selection"],
        how="left",
        validate="one_to_one",
    )
    probability_column = {"home": "p_home", "draw": "p_draw", "away": "p_away"}
    source_assumption = (
        "research_only: source has no quote timestamp; opening price assumed available "
        f"{cutoff_lead.total_seconds():g}s before kickoff; kickoff timezone localized to UTC"
    )
    rows: list[dict[str, object]] = []
    for row in joined.itertuples(index=False):
        selection = str(row.selection)
        if selection not in probability_column:
            continue
        kickoff = pd.Timestamp(row.kickoff).to_pydatetime()
        cutoff = kickoff - cutoff_lead
        observed_at = cutoff - timedelta(seconds=1)
        probability = float(getattr(row, probability_column[selection]))
        raw_stratum = getattr(row, "league_code", getattr(row, "league", None))
        stratum_id = str(raw_stratum).strip() if raw_stratum is not None and not pd.isna(raw_stratum) else None
        candidate = OpportunityCandidate(
            fixture_id=str(row.source_row_id),
            market_key="1x2",
            selection=selection,
            decimal_odds=float(row.odds),
            probability=probability,
            probability_lower_bound=max(0.0, probability - probability_haircut),
            observed_at=observed_at,
            snapshot_id=f"{row.source_row_id}:{row.provider}:{selection}:opening",
            bookmaker=str(row.provider),
            tax_multiplier=tax_multiplier,
            execution_cost=execution_cost,
            correlation_group="match-result",
        )
        opportunity = HistoricalOpportunity(
            cutoff=cutoff,
            kickoff=kickoff,
            candidate=candidate,
            settlement="win" if selection == row.actual_match else "loss",
            market_group_id=f"{row.source_row_id}:1x2",
            market_size=3,
            execution_price_kind="opening",
            closing_odds=None if pd.isna(row.closing_odds) else float(row.closing_odds),
            cluster_id=str(pd.Timestamp(kickoff).date()),
            stratum_id=stratum_id,
        )
        rows.append(
            {
                "cutoff": opportunity.cutoff.isoformat(),
                "kickoff": opportunity.kickoff.isoformat(),
                "fixture_id": candidate.fixture_id,
                "market_key": candidate.market_key,
                "selection": candidate.selection,
                "decimal_odds": candidate.decimal_odds,
                "probability": candidate.probability,
                "probability_lower_bound": candidate.probability_lower_bound,
                "observed_at": candidate.observed_at.isoformat(),
                "snapshot_id": candidate.snapshot_id,
                "bookmaker": candidate.bookmaker,
                "tax_multiplier": candidate.tax_multiplier,
                "execution_cost": candidate.execution_cost,
                "correlation_group": candidate.correlation_group,
                "settlement": opportunity.settlement,
                "market_group_id": opportunity.market_group_id,
                "market_size": opportunity.market_size,
                "execution_price_kind": opportunity.execution_price_kind,
                "closing_odds": opportunity.closing_odds,
                "cluster_id": opportunity.cluster_id,
                "stratum_id": opportunity.stratum_id,
                "source_assumption": source_assumption,
                "outer_fold": str(row.outer_fold),
            }
        )
    if not rows:
        raise ValueError("no opening 1X2 odds matched the nested OOS forecasts")
    return pd.DataFrame(rows)


__all__ = ["build_football_data_replay_frame"]
