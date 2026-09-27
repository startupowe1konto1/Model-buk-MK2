"""Point-in-time snapshots built from the existing API-Football client.

The adapter deliberately excludes API-Football's own prediction endpoint.  Its
output may be used as an external benchmark, but never as a PURE model feature.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Protocol

import pandas as pd

from model_buk.context_coverage import (
    CONTEXT_AVAILABILITY_FEATURE_GROUP,
    context_availability_payload,
)
from model_buk.current_pipeline import RunContext
from model_buk.snapshots import SnapshotRecord


ADAPTER_VERSION = "api-football-snapshots-v1"


class ApiFootballSnapshotError(RuntimeError):
    """The provider response cannot be represented safely as snapshots."""


class ApiFootballLike(Protocol):
    connected: bool

    def fixtures(self, date: str, league_codes: list[str] | None = None) -> list[dict[str, Any]]: ...

    def match_context(
        self,
        fixture_id: int,
        deep: bool = True,
        *,
        include_prediction_benchmark: bool = True,
        fixture_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ApiFootballSnapshotPolicy:
    """Quota and scope controls for one scheduled provider read."""

    day_offsets: tuple[int, ...] = (0, 1)
    league_codes: tuple[str, ...] = ()
    upcoming_statuses: tuple[str, ...] = ("NS", "TBD")
    include_context: bool = True
    include_recent_observations: bool = True
    max_fixtures: int = 20

    def __post_init__(self) -> None:
        if not self.day_offsets or any(
            not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
            for offset in self.day_offsets
        ):
            raise ValueError("day_offsets must contain non-negative integers")
        if len(set(self.day_offsets)) != len(self.day_offsets):
            raise ValueError("day_offsets must not contain duplicates")
        if self.max_fixtures < 1:
            raise ValueError("max_fixtures must be positive")
        if not self.upcoming_statuses or any(not str(status).strip() for status in self.upcoming_statuses):
            raise ValueError("upcoming_statuses must contain non-blank values")


def _utc(value: Any, *, field: str) -> datetime:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ApiFootballSnapshotError(f"{field} must be a valid timestamp") from exc
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ApiFootballSnapshotError(f"{field} must include a timezone")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _version(payload: Any) -> str:
    return f"{ADAPTER_VERSION}:{sha256(_canonical(payload).encode('utf-8')).hexdigest()[:16]}"


def _quote_entity_id(fixture_id: int, quote: Mapping[str, Any]) -> str:
    parts = (
        str(quote.get("bookmaker_id") or quote.get("bookmaker") or "unknown"),
        str(quote.get("bet_id") or quote.get("bet") or "unknown"),
        str(quote.get("value") or "unknown"),
    )
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"fixture:{fixture_id}:quote:{digest}"


class ApiFootballSnapshotAdapter:
    """Convert current API-Football responses into immutable snapshot records.

    Provider rows without a source publication timestamp are stamped at the run
    cutoff.  That policy means "observed during this run", not a claim about
    their earlier historical availability.  Odds carrying a provider ``update``
    after the cutoff are skipped because the API does not expose the superseded
    price required to reconstruct that point in time.
    """

    def __init__(
        self,
        client: ApiFootballLike,
        *,
        policy: ApiFootballSnapshotPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.policy = policy or ApiFootballSnapshotPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def component_id(self) -> str:
        config = asdict(self.policy)
        return f"{ADAPTER_VERSION}:{sha256(_canonical(config).encode('utf-8')).hexdigest()[:16]}"

    def fetch(self, context: RunContext) -> list[SnapshotRecord]:
        if not getattr(self.client, "connected", False):
            raise ApiFootballSnapshotError("API-Football client is not connected")
        cutoff = _utc(context.cutoff, field="cutoff")
        ingested_at = _utc(self.clock(), field="clock")
        if ingested_at < cutoff:
            raise ApiFootballSnapshotError("clock cannot be earlier than the run cutoff")

        fixtures: dict[int, dict[str, Any]] = {}
        league_codes = list(self.policy.league_codes) or None
        for offset in self.policy.day_offsets:
            query_date = (cutoff + timedelta(days=offset)).date().isoformat()
            for fixture in self.client.fixtures(query_date, league_codes):
                try:
                    fixture_id = int(fixture.get("fixture_id"))
                except (TypeError, ValueError) as exc:
                    raise ApiFootballSnapshotError("fixture is missing a numeric fixture_id") from exc
                fixtures[fixture_id] = dict(fixture)

        eligible: list[tuple[datetime, int, dict[str, Any]]] = []
        allowed_statuses = set(self.policy.upcoming_statuses)
        for fixture_id, fixture in fixtures.items():
            kickoff = _utc(fixture.get("kickoff"), field=f"fixture {fixture_id} kickoff")
            if kickoff <= cutoff or str(fixture.get("status") or "") not in allowed_statuses:
                continue
            eligible.append((kickoff, fixture_id, fixture))
        eligible.sort(key=lambda item: (item[0], item[1]))

        records: list[SnapshotRecord] = []
        for kickoff, fixture_id, fixture in eligible[: self.policy.max_fixtures]:
            records.append(
                self._record(
                    entity_id=f"fixture:{fixture_id}",
                    feature_group="fixture",
                    event_time=kickoff,
                    available_at=cutoff,
                    ingested_at=ingested_at,
                    payload=fixture,
                    source_record_id=str(fixture_id),
                )
            )
            if self.policy.include_context:
                context_payload = self.client.match_context(
                    fixture_id,
                    deep=self.policy.include_recent_observations,
                    include_prediction_benchmark=False,
                    fixture_override=fixture,
                )
                records.extend(
                    self._context_records(
                        fixture_id=fixture_id,
                        kickoff=kickoff,
                        cutoff=cutoff,
                        ingested_at=ingested_at,
                        context_payload=context_payload,
                    )
                )
        return records

    def _context_records(
        self,
        *,
        fixture_id: int,
        kickoff: datetime,
        cutoff: datetime,
        ingested_at: datetime,
        context_payload: Mapping[str, Any],
    ) -> list[SnapshotRecord]:
        records: list[SnapshotRecord] = []
        errors = tuple(str(error) for error in (context_payload.get("errors") or []))
        failed = {error.partition(":")[0].strip() for error in errors}
        statuses: dict[str, str] = {}

        odds = context_payload.get("odds")
        statuses["odds"] = self._availability_status(
            present="odds" in context_payload,
            value=odds,
            failed="odds" in failed,
        )
        for quote in [] if statuses["odds"] in {"missing", "provider_error"} else (odds or []):
            available_at = cutoff
            if quote.get("update"):
                available_at = _utc(quote["update"], field="odds update")
                if available_at > cutoff:
                    continue
            records.append(
                self._record(
                    entity_id=_quote_entity_id(fixture_id, quote),
                    feature_group="odds",
                    event_time=kickoff,
                    available_at=available_at,
                    ingested_at=ingested_at,
                    payload=dict(quote),
                    source_record_id=f"{fixture_id}:{quote.get('bookmaker_id')}:{quote.get('bet_id')}:{quote.get('value')}",
                )
            )

        for feature_group in ("injuries", "lineups"):
            value = context_payload.get(feature_group)
            statuses[feature_group] = self._availability_status(
                present=feature_group in context_payload,
                value=value,
                failed=feature_group in failed,
            )
            if statuses[feature_group] in {"missing", "provider_error"}:
                continue
            payload = list(value or [])
            records.append(
                self._record(
                    entity_id=f"fixture:{fixture_id}",
                    feature_group=feature_group,
                    event_time=kickoff,
                    available_at=cutoff,
                    ingested_at=ingested_at,
                    payload={"items": payload},
                    source_record_id=str(fixture_id),
                )
            )

        recent_failed = any(name.startswith("recent_") for name in failed)
        recent = context_payload.get("recent_observations")
        if not self.policy.include_recent_observations:
            statuses["team_form"] = "not_requested"
        elif recent_failed:
            statuses["team_form"] = "provider_error"
        else:
            statuses["team_form"] = self._team_form_availability_status(
                present="recent_observations" in context_payload,
                value=recent,
            )
        if statuses["team_form"] in {"available", "observed_empty"}:
            recent = recent or {}
            fixture = context_payload.get("fixture") or {}
            for side in ("home", "away"):
                team = fixture.get(side) or {}
                team_id = team.get("id")
                if team_id is None:
                    continue
                records.append(
                    self._record(
                        entity_id=f"team:{team_id}:recent",
                        feature_group="team_form",
                        event_time=cutoff,
                        available_at=cutoff,
                        ingested_at=ingested_at,
                        payload={"side": side, "team": team, "matches": list(recent.get(side) or [])},
                        source_record_id=f"{fixture_id}:{side}",
                    )
                )
        records.append(
            self._record(
                entity_id=f"fixture:{fixture_id}",
                feature_group=CONTEXT_AVAILABILITY_FEATURE_GROUP,
                event_time=kickoff,
                available_at=cutoff,
                ingested_at=ingested_at,
                payload=context_availability_payload(statuses),
                source_record_id=str(fixture_id),
            )
        )
        # external_prediction_benchmark is intentionally not copied.
        return records

    @staticmethod
    def _availability_status(*, present: bool, value: Any, failed: bool) -> str:
        if failed:
            return "provider_error"
        if not present or value is None:
            return "missing"
        return "observed_empty" if not value else "available"

    @staticmethod
    def _team_form_availability_status(*, present: bool, value: Any) -> str:
        if not present or not isinstance(value, Mapping):
            return "missing"
        if "home" not in value or "away" not in value:
            return "missing"
        return "available" if value["home"] or value["away"] else "observed_empty"

    @staticmethod
    def _record(
        *,
        entity_id: str,
        feature_group: str,
        event_time: datetime,
        available_at: datetime,
        ingested_at: datetime,
        payload: Mapping[str, Any],
        source_record_id: str,
    ) -> SnapshotRecord:
        return SnapshotRecord(
            entity_id=entity_id,
            feature_group=feature_group,
            event_time=event_time,
            available_at=available_at,
            ingested_at=ingested_at,
            provider="api-football",
            provider_version=_version(payload),
            payload=payload,
            source_record_id=source_record_id,
        )


__all__ = [
    "ADAPTER_VERSION",
    "ApiFootballSnapshotAdapter",
    "ApiFootballSnapshotError",
    "ApiFootballSnapshotPolicy",
]
