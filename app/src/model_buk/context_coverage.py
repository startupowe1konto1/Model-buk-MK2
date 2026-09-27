"""Auditable availability states for point-in-time match context."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import pandas as pd


CONTEXT_AVAILABILITY_FEATURE_GROUP = "context_availability"
CONTEXT_AVAILABILITY_STATUSES = frozenset({
    "available",
    "observed_empty",
    "missing",
    "provider_error",
    "not_requested",
})


def context_availability_payload(statuses: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Validate and normalize one fixture's context availability states."""
    normalized: dict[str, str] = {}
    for feature_group, status in statuses.items():
        group = str(feature_group).strip()
        value = str(status).strip()
        if not group:
            raise ValueError("context availability feature groups must not be blank")
        if value not in CONTEXT_AVAILABILITY_STATUSES:
            allowed = ", ".join(sorted(CONTEXT_AVAILABILITY_STATUSES))
            raise ValueError(f"invalid context availability status {value!r}; expected one of: {allowed}")
        normalized[group] = value
    if not normalized:
        raise ValueError("context availability must contain at least one feature group")
    return {"features": dict(sorted(normalized.items()))}


def summarize_context_coverage(snapshots: pd.DataFrame) -> dict[str, Any] | None:
    """Return sanitized status counts without persisting provider payloads."""
    if "feature_group" not in snapshots.columns:
        return None
    rows = snapshots[
        snapshots["feature_group"].eq(CONTEXT_AVAILABILITY_FEATURE_GROUP)
    ]
    if rows.empty:
        return None

    counts: dict[str, Counter[str]] = {}
    incomplete_fixtures: set[str] = set()
    provider_error_fixtures: set[str] = set()
    for row in rows.itertuples(index=False):
        payload = getattr(row, "payload", None)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("features"), Mapping):
            raise ValueError("context availability snapshot has an invalid payload")
        normalized = context_availability_payload(payload["features"])["features"]
        entity_id = str(getattr(row, "entity_id"))
        for feature_group, status in normalized.items():
            counts.setdefault(feature_group, Counter())[status] += 1
            if status in {"missing", "provider_error"}:
                incomplete_fixtures.add(entity_id)
            if status == "provider_error":
                provider_error_fixtures.add(entity_id)

    return {
        "fixture_count": int(rows["entity_id"].astype(str).nunique()),
        "incomplete_fixture_count": len(incomplete_fixtures),
        "provider_error_fixture_count": len(provider_error_fixtures),
        "status_counts": {
            group: dict(sorted(group_counts.items()))
            for group, group_counts in sorted(counts.items())
        },
    }


__all__ = [
    "CONTEXT_AVAILABILITY_FEATURE_GROUP",
    "CONTEXT_AVAILABILITY_STATUSES",
    "context_availability_payload",
    "summarize_context_coverage",
]
