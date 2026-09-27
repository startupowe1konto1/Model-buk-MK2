"""Point-in-time match-context features with explicit missingness masks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
from typing import Any

import pandas as pd

from model_buk.context_coverage import (
    CONTEXT_AVAILABILITY_FEATURE_GROUP,
    CONTEXT_AVAILABILITY_STATUSES,
    context_availability_payload,
)
from model_buk.snapshots import latest_as_of


CONTEXT_FEATURE_SCHEMA_VERSION = "match-context-features-v1"
CONTEXT_FEATURE_GROUPS = ("injuries", "lineups", "referee", "rest", "travel")
CONTEXT_STATUS_COLUMNS = tuple(
    f"{group}_status_{status}"
    for group in CONTEXT_FEATURE_GROUPS
    for status in sorted(CONTEXT_AVAILABILITY_STATUSES)
)
CONTEXT_VALUE_COLUMNS = (
    "absence_home_count",
    "absence_away_count",
    "absence_unassigned_count",
    "suspension_home_count",
    "suspension_away_count",
    "lineup_home_starters",
    "lineup_away_starters",
    "lineup_home_confirmed",
    "lineup_away_confirmed",
    "lineup_home_formation_known",
    "lineup_away_formation_known",
    "referee_known",
    "home_rest_days",
    "away_rest_days",
    "rest_days_diff",
    "away_travel_km",
)
CONTEXT_FEATURE_COLUMNS = (*CONTEXT_VALUE_COLUMNS, *CONTEXT_STATUS_COLUMNS)
CONTEXT_OUTPUT_COLUMNS = (
    "fixture_id",
    "prediction_cutoff",
    "feature_schema_version",
    *CONTEXT_FEATURE_COLUMNS,
)


def _payload(row: pd.Series, *, feature_group: str) -> Mapping[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError(f"{feature_group} snapshot payload must be a mapping")
    return payload


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _availability_by_group(rows: pd.DataFrame, entity_id: str) -> dict[str, str]:
    availability = rows[
        rows["feature_group"].eq(CONTEXT_AVAILABILITY_FEATURE_GROUP)
        & rows["entity_id"].eq(entity_id)
    ]
    statuses: dict[str, str] = {}
    for _, row in availability.iterrows():
        payload = _payload(row, feature_group=CONTEXT_AVAILABILITY_FEATURE_GROUP)
        features = payload.get("features")
        if not isinstance(features, Mapping):
            raise ValueError("context availability snapshot has an invalid payload")
        normalized = context_availability_payload(features)["features"]
        for group, status in normalized.items():
            existing = statuses.get(group)
            if existing is not None and existing != status:
                raise ValueError(
                    f"conflicting context availability statuses for {entity_id} and {group}"
                )
            statuses[group] = status
    return statuses


def _items(rows: pd.DataFrame, entity_id: str, feature_group: str) -> list[dict[str, Any]]:
    selected = rows[
        rows["feature_group"].eq(feature_group)
        & rows["entity_id"].eq(entity_id)
    ]
    items: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        payload = _payload(row, feature_group=feature_group)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError(f"{feature_group} snapshot payload must contain an items list")
        items.extend(dict(item) for item in raw_items if isinstance(item, Mapping))
    return items


def _is_suspension(item: Mapping[str, Any]) -> bool:
    text = " ".join(str(item.get(field) or "") for field in ("type", "reason")).lower()
    markers = ("suspend", "suspension", "red card", "disciplinary")
    return any(marker in text for marker in markers)


def _status_masks(statuses: Mapping[str, str]) -> dict[str, float]:
    return {
        f"{group}_status_{status}": float(statuses[group] == status)
        for group in CONTEXT_FEATURE_GROUPS
        for status in sorted(CONTEXT_AVAILABILITY_STATUSES)
    }


def _fixture_features(rows: pd.DataFrame, fixture_row: pd.Series) -> dict[str, Any]:
    fixture = _payload(fixture_row, feature_group="fixture")
    fixture_id = str(fixture.get("fixture_id") or "").strip()
    if not fixture_id:
        raise ValueError("fixture snapshot is missing fixture_id")
    entity_id = str(fixture_row["entity_id"])
    if entity_id != f"fixture:{fixture_id}":
        raise ValueError("fixture snapshot entity_id does not match fixture_id")
    home = fixture.get("home") if isinstance(fixture.get("home"), Mapping) else {}
    away = fixture.get("away") if isinstance(fixture.get("away"), Mapping) else {}
    home_id = home.get("id")
    away_id = away.get("id")
    availability = _availability_by_group(rows, entity_id)

    referee_known = float(bool(str(fixture.get("referee") or "").strip()))
    home_rest = _number(home.get("rest_days"))
    away_rest = _number(away.get("rest_days"))
    travel = _number(away.get("travel_distance_km", away.get("travel_km")))
    statuses = {
        "injuries": availability.get("injuries", "missing"),
        "lineups": availability.get("lineups", "missing"),
        "referee": "available" if referee_known else "missing",
        "rest": "available" if home_rest is not None and away_rest is not None else "missing",
        "travel": "available" if travel is not None else "missing",
    }

    values = {column: float("nan") for column in CONTEXT_VALUE_COLUMNS}
    values["referee_known"] = referee_known
    if statuses["rest"] == "available":
        values["home_rest_days"] = home_rest
        values["away_rest_days"] = away_rest
        values["rest_days_diff"] = home_rest - away_rest
    if statuses["travel"] == "available":
        values["away_travel_km"] = travel

    injury_items = _items(rows, entity_id, "injuries")
    if statuses["injuries"] == "available":
        if not injury_items:
            raise ValueError(f"injuries are marked available but absent for {entity_id}")
        team_ids_are_usable = home_id is not None and away_id is not None and home_id != away_id
        known_team_ids = {home_id, away_id} if team_ids_are_usable else set()
        unassigned = [item for item in injury_items if item.get("team_id") not in known_team_ids]
        values["absence_unassigned_count"] = float(len(unassigned))
        if team_ids_are_usable and not unassigned:
            home_items = [item for item in injury_items if item.get("team_id") == home_id]
            away_items = [item for item in injury_items if item.get("team_id") == away_id]
            values["absence_home_count"] = float(len(home_items))
            values["absence_away_count"] = float(len(away_items))
            values["suspension_home_count"] = float(sum(_is_suspension(item) for item in home_items))
            values["suspension_away_count"] = float(sum(_is_suspension(item) for item in away_items))

    lineup_items = _items(rows, entity_id, "lineups")
    if statuses["lineups"] == "available":
        if not lineup_items:
            raise ValueError(f"lineups are marked available but absent for {entity_id}")
        lineups_by_team = {item.get("team_id"): item for item in lineup_items}
        for side, team_id in (("home", home_id), ("away", away_id)):
            lineup = lineups_by_team.get(team_id) if team_id is not None else None
            if lineup is None:
                continue
            starters = lineup.get("startXI")
            if not isinstance(starters, list):
                continue
            values[f"lineup_{side}_starters"] = float(len(starters))
            values[f"lineup_{side}_confirmed"] = float(len(starters) == 11)
            values[f"lineup_{side}_formation_known"] = float(
                bool(str(lineup.get("formation") or "").strip())
            )

    return {**values, **_status_masks(statuses), "fixture_id": fixture_id}


def build_match_context_features(
    snapshots: pd.DataFrame,
    cutoff: str | datetime | pd.Timestamp,
    *,
    known_by: str | datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build one leakage-safe context row per fixture at ``cutoff``.

    Unknown values remain ``NaN``. Status masks are always present so a future
    model can distinguish absent, empty, failed and deliberately skipped reads.
    """
    as_of = latest_as_of(snapshots, cutoff, known_by=known_by)
    fixture_rows = as_of[as_of["feature_group"].eq("fixture")]
    cutoff_ts = pd.Timestamp(cutoff)
    if cutoff_ts.tzinfo is None:
        raise ValueError("cutoff must include a timezone")
    prediction_cutoff = cutoff_ts.tz_convert("UTC").isoformat()
    output: list[dict[str, Any]] = []
    for _, fixture_row in fixture_rows.sort_values("entity_id", kind="stable").iterrows():
        features = _fixture_features(as_of, fixture_row)
        output.append({
            "fixture_id": features.pop("fixture_id"),
            "prediction_cutoff": prediction_cutoff,
            "feature_schema_version": CONTEXT_FEATURE_SCHEMA_VERSION,
            **features,
        })
    return pd.DataFrame(output, columns=CONTEXT_OUTPUT_COLUMNS)


__all__ = [
    "CONTEXT_FEATURE_COLUMNS",
    "CONTEXT_FEATURE_GROUPS",
    "CONTEXT_FEATURE_SCHEMA_VERSION",
    "CONTEXT_OUTPUT_COLUMNS",
    "build_match_context_features",
]
