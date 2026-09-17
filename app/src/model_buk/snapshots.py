from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd


SNAPSHOT_REQUIRED_COLUMNS = {
    "entity_id",
    "feature_group",
    "event_time",
    "available_at",
    "ingested_at",
    "provider",
    "provider_version",
}


def _utc_timestamp(value: str | datetime | pd.Timestamp, *, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid timestamp") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{field} must be a valid timestamp")
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.tz_convert("UTC")


@dataclass(frozen=True)
class SnapshotRecord:
    """One immutable point-in-time observation from an external provider.

    ``event_time`` describes when the observed fact applies. ``available_at``
    is the earliest time at which the provider exposed it, while
    ``ingested_at`` records when Model Buk received this exact version.  A
    future fixture may therefore have ``event_time > available_at``.
    """

    entity_id: str
    feature_group: str
    event_time: str | datetime | pd.Timestamp
    available_at: str | datetime | pd.Timestamp
    ingested_at: str | datetime | pd.Timestamp
    provider: str
    provider_version: str
    payload: Mapping[str, Any]
    source_record_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("entity_id", "feature_group", "provider", "provider_version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be blank")
        event_time = _utc_timestamp(self.event_time, field="event_time")
        available_at = _utc_timestamp(self.available_at, field="available_at")
        ingested_at = _utc_timestamp(self.ingested_at, field="ingested_at")
        if ingested_at < available_at:
            raise ValueError("ingested_at cannot be earlier than available_at")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "ingested_at", ingested_at)
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshots_frame(records: Iterable[SnapshotRecord]) -> pd.DataFrame:
    rows = [record.to_dict() for record in records]
    return pd.DataFrame(rows, columns=[
        "entity_id",
        "feature_group",
        "event_time",
        "available_at",
        "ingested_at",
        "provider",
        "provider_version",
        "payload",
        "source_record_id",
    ])


def validate_snapshots(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and return a UTC-normalized copy of a snapshot table."""
    missing = sorted(SNAPSHOT_REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"snapshots are missing required columns: {', '.join(missing)}")

    output = frame.copy()
    for column in ("event_time", "available_at", "ingested_at"):
        parsed = pd.to_datetime(output[column], errors="coerce", utc=True, format="mixed")
        if parsed.isna().any():
            raise ValueError(f"snapshots contain invalid {column} values")
        output[column] = parsed

    blank_fields = ("entity_id", "feature_group", "provider", "provider_version")
    for column in blank_fields:
        if output[column].isna().any() or output[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"snapshots contain blank {column} values")
    if (output["ingested_at"] < output["available_at"]).any():
        raise ValueError("snapshots contain ingested_at earlier than available_at")
    return output


def latest_as_of(
    frame: pd.DataFrame,
    cutoff: str | datetime | pd.Timestamp,
    *,
    known_by: str | datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return the newest usable version per entity, feature group and provider.

    ``cutoff`` is the forecast decision time. ``known_by`` optionally recreates
    what this installation had actually ingested at an earlier time.  No filter
    is applied to ``event_time`` because pre-match facts legitimately refer to
    a fixture scheduled after the forecast cutoff.
    """
    data = validate_snapshots(frame)
    cutoff_ts = _utc_timestamp(cutoff, field="cutoff")
    known_by_ts = _utc_timestamp(known_by or datetime.now(timezone.utc), field="known_by")
    usable = data[
        (data["available_at"] <= cutoff_ts)
        & (data["ingested_at"] <= known_by_ts)
    ].copy()
    if usable.empty:
        return usable.reset_index(drop=True)

    key = ["entity_id", "feature_group", "provider"]
    usable = usable.sort_values(key + ["available_at", "ingested_at", "provider_version"])
    return usable.groupby(key, as_index=False, sort=False).tail(1).reset_index(drop=True)
