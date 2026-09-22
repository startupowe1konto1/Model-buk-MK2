"""Bridge point-in-time snapshots to the existing PURE multi-market engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Protocol

import pandas as pd

from model_buk.current_pipeline import PredictionBatch, RunContext
from model_buk.live_adjustment import apply_current_observations
from model_buk.markets.pricing import devig_probabilities
from model_buk.multimarket import rebuild_markets_from_expected
from model_buk.odds_parser import normalize_odds
from model_buk.opportunity_selector import OpportunityCandidate


PREDICTOR_VERSION = "multimarket-snapshot-predictor-v1"


class PureMultiMarketEngine(Protocol):
    def analyze(
        self,
        league_code: str,
        date: str | pd.Timestamp,
        home_team: str,
        away_team: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SnapshotPredictorPolicy:
    """Conservative provisional uncertainty and execution assumptions.

    ``probability_margin`` is not a calibrated confidence interval.  It is an
    explicit research haircut used until league×market residual calibration is
    available.  Model promotion must still pass ``evaluation.gates``.
    """

    probability_margin: float = 0.05
    tax_multiplier: float = 0.88
    execution_cost: float = 0.0
    min_data_quality: float = 60.0
    devig_method: str = "proportional"
    allowed_market_prefixes: tuple[str, ...] = (
        "result.",
        "btts.",
        "goals.",
        "corners.",
        "shots.",
        "sot.",
        "cards.",
    )

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability_margin < 1.0:
            raise ValueError("probability_margin must be in [0, 1)")
        if not 0.0 < self.tax_multiplier <= 1.0:
            raise ValueError("tax_multiplier must be in (0, 1]")
        if self.execution_cost < 0.0:
            raise ValueError("execution_cost must be non-negative")
        if not 0.0 <= self.min_data_quality <= 100.0:
            raise ValueError("min_data_quality must be between 0 and 100")
        if self.devig_method not in {"proportional", "power", "shin"}:
            raise ValueError("devig_method must be one of: proportional, power, shin")
        if any(not prefix for prefix in self.allowed_market_prefixes):
            raise ValueError("allowed_market_prefixes must not contain blanks")


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _payload(row: pd.Series) -> dict[str, Any]:
    value = row.get("payload")
    if not isinstance(value, dict):
        raise ValueError("snapshot payload must be a mapping")
    return value


def _aware_datetime(value: Any, *, field: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _correlation_group(market_key: str) -> str:
    parts = market_key.split(".")
    if parts[0] in {"result", "btts"}:
        return parts[0]
    if len(parts) >= 3:
        return ".".join(parts[:2])
    return market_key


def _book_definition(market_key: str) -> tuple[str, tuple[str, ...]] | None:
    """Return a logical book id and its ordered, mutually exclusive outcomes."""
    if market_key.startswith("result."):
        return "result", ("result.home", "result.draw", "result.away")
    if market_key.startswith("btts."):
        return "btts", ("btts.yes", "btts.no")
    parts = market_key.split(".")
    side_index = next((index for index, part in enumerate(parts) if part in {"over", "under"}), None)
    if side_index is not None and 0 < side_index < len(parts) - 1:
        prefix = ".".join(parts[:side_index])
        line = ".".join(parts[side_index + 1 :])
        return (
            f"{prefix}:{line}",
            (f"{prefix}.over.{line}", f"{prefix}.under.{line}"),
        )
    return None


def _bookmaker_key(quote: dict[str, Any]) -> str:
    bookmaker_id = quote.get("bookmaker_id")
    if bookmaker_id is not None and str(bookmaker_id).strip():
        return f"id:{bookmaker_id}"
    return f"name:{str(quote.get('bookmaker') or 'unknown')}"


def _market_probabilities(
    quotes: list[dict[str, Any]],
    *,
    method: str,
) -> dict[tuple[str, str], float]:
    """De-vig complete books without combining prices from bookmakers."""
    books: dict[tuple[str, str], dict[str, float]] = {}
    definitions: dict[tuple[str, str], tuple[str, ...]] = {}
    for quote in quotes:
        market_key = str(quote["market_key"])
        definition = _book_definition(market_key)
        if definition is None:
            continue
        logical_market, ordered_keys = definition
        book_key = (_bookmaker_key(quote), logical_market)
        books.setdefault(book_key, {})[market_key] = float(quote["odd"])
        definitions[book_key] = ordered_keys

    probabilities: dict[tuple[str, str], float] = {}
    for (bookmaker, logical_market), prices_by_key in books.items():
        ordered_keys = definitions[(bookmaker, logical_market)]
        if not all(key in prices_by_key for key in ordered_keys):
            continue
        fair = devig_probabilities(
            tuple(prices_by_key[key] for key in ordered_keys),
            method,
        )
        for market_key, probability in zip(ordered_keys, fair, strict=True):
            probabilities[(bookmaker, market_key)] = float(probability)
    return probabilities


class MultiMarketSnapshotPredictor:
    """Generate paper/shadow candidates while keeping odds outside PURE inputs."""

    def __init__(
        self,
        engine: PureMultiMarketEngine,
        *,
        policy: SnapshotPredictorPolicy | None = None,
        engine_id: str | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy or SnapshotPredictorPolicy()
        resolved_engine_id = engine_id or getattr(engine, "model_id", None)
        if not resolved_engine_id or not str(resolved_engine_id).strip():
            raise ValueError(
                "engine_id is required when the PURE engine has no versioned model_id"
            )
        self.engine_id = str(resolved_engine_id).strip()

    @property
    def model_id(self) -> str:
        version = _stable_digest({"engine": self.engine_id, "policy": asdict(self.policy)})
        return f"{PREDICTOR_VERSION}:{version}"

    component_id = model_id

    def predict(self, snapshots: pd.DataFrame, context: RunContext) -> PredictionBatch:
        required = {"entity_id", "feature_group", "available_at", "provider_version", "payload"}
        missing = sorted(required - set(snapshots.columns))
        if missing:
            raise ValueError(f"snapshots are missing columns: {', '.join(missing)}")

        fixtures = snapshots[snapshots["feature_group"].eq("fixture")].copy()
        candidates: list[OpportunityCandidate] = []
        for _, fixture_row in fixtures.sort_values("entity_id", kind="stable").iterrows():
            fixture = _payload(fixture_row)
            fixture_id = str(fixture.get("fixture_id") or "").strip()
            kickoff = fixture.get("kickoff")
            league_code = str(fixture.get("league_code") or "").strip()
            home = fixture.get("home") or {}
            away = fixture.get("away") or {}
            if not fixture_id or not kickoff or not league_code or not home.get("name") or not away.get("name"):
                continue

            # PURE analysis is completed before any bookmaker payload is read.
            analysis = self.engine.analyze(
                league_code,
                kickoff,
                str(home["name"]),
                str(away["name"]),
            )
            quality = float((analysis.get("data_quality") or {}).get("score") or 0.0)
            if quality < self.policy.min_data_quality:
                continue

            home_observations = self._team_form(snapshots, home.get("id"))
            away_observations = self._team_form(snapshots, away.get("id"))
            if home_observations or away_observations:
                analysis = apply_current_observations(
                    analysis,
                    home_observations,
                    away_observations,
                )
                analysis["markets"] = rebuild_markets_from_expected(analysis)
            model_markets = {
                str(market["market_key"]): market
                for market in analysis.get("markets") or []
                if "market_key" in market and "probability" in market
            }

            prefix = f"fixture:{fixture_id}:quote:"
            quote_rows = snapshots[
                snapshots["feature_group"].eq("odds")
                & snapshots["entity_id"].astype(str).str.startswith(prefix)
            ]
            normalized_quotes: list[tuple[pd.Series, dict[str, Any]]] = []
            for _, quote_row in quote_rows.iterrows():
                raw_quote = _payload(quote_row)
                normalized = normalize_odds([raw_quote])
                if not normalized:
                    continue
                normalized_quotes.append((quote_row, normalized[0]))
            market_probabilities = _market_probabilities(
                [quote for _, quote in normalized_quotes],
                method=self.policy.devig_method,
            )
            for quote_row, quote in normalized_quotes:
                market_key = str(quote["market_key"])
                if not market_key.startswith(self.policy.allowed_market_prefixes):
                    continue
                market = model_markets.get(market_key)
                if market is None:
                    continue
                probability = float(market["probability"])
                lower = max(0.0, probability - self.policy.probability_margin)
                odds = float(quote["odd"])
                if odds <= 1.0:
                    continue
                observed_at = _aware_datetime(quote_row["available_at"], field="quote available_at")
                candidates.append(
                    OpportunityCandidate(
                        fixture_id=fixture_id,
                        market_key=market_key,
                        selection=str(quote.get("value") or market_key),
                        decimal_odds=odds,
                        probability=probability,
                        probability_lower_bound=lower,
                        observed_at=observed_at,
                        snapshot_id=f"{quote_row['entity_id']}:{quote_row['provider_version']}",
                        bookmaker=str(quote.get("bookmaker") or "unknown"),
                        tax_multiplier=self.policy.tax_multiplier,
                        execution_cost=self.policy.execution_cost,
                        correlation_group=_correlation_group(market_key),
                        market_probability=market_probabilities.get(
                            (_bookmaker_key(quote), market_key)
                        ),
                    )
                )
        return PredictionBatch(tuple(candidates), model_id=self.model_id)

    @staticmethod
    def _team_form(snapshots: pd.DataFrame, team_id: Any) -> list[dict[str, Any]]:
        if team_id is None:
            return []
        entity_id = f"team:{team_id}:recent"
        rows = snapshots[
            snapshots["feature_group"].eq("team_form")
            & snapshots["entity_id"].eq(entity_id)
        ]
        if rows.empty:
            return []
        payload = _payload(rows.iloc[-1])
        matches = payload.get("matches") or []
        return [dict(match) for match in matches if isinstance(match, dict)]


__all__ = [
    "MultiMarketSnapshotPredictor",
    "PREDICTOR_VERSION",
    "SnapshotPredictorPolicy",
]
