from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from model_buk.context_coverage import context_availability_payload
from model_buk.context_features import (
    CONTEXT_FEATURE_SCHEMA_VERSION,
    build_match_context_features,
)
from model_buk.snapshots import SnapshotRecord, snapshots_frame


CUTOFF = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
KICKOFF = CUTOFF + timedelta(hours=6)


def record(group, payload, *, available_at=None, version="v1"):
    return SnapshotRecord(
        entity_id="fixture:11",
        feature_group=group,
        event_time=KICKOFF,
        available_at=available_at or CUTOFF - timedelta(minutes=5),
        ingested_at=CUTOFF + timedelta(seconds=1),
        provider="test-provider",
        provider_version=version,
        payload=payload,
    )


def fixture_payload():
    return {
        "fixture_id": 11,
        "home": {"id": 1, "name": "Home", "rest_days": 6},
        "away": {
            "id": 2,
            "name": "Away",
            "rest_days": 4,
            "travel_distance_km": 320,
        },
        "referee": "Referee Name",
    }


def test_builds_versioned_context_features_and_explicit_status_masks():
    starters = [{"id": value} for value in range(11)]
    snapshots = snapshots_frame([
        record("fixture", fixture_payload()),
        record(
            "context_availability",
            context_availability_payload({
                "injuries": "available",
                "lineups": "available",
            }),
        ),
        record(
            "injuries",
            {"items": [
                {"team_id": 1, "player_id": 10, "reason": "Muscle Injury"},
                {"team_id": 2, "player_id": 20, "reason": "Red Card"},
            ]},
        ),
        record(
            "lineups",
            {"items": [
                {"team_id": 1, "formation": "4-3-3", "startXI": starters},
                {"team_id": 2, "formation": None, "startXI": starters[:10]},
            ]},
        ),
    ])

    row = build_match_context_features(
        snapshots,
        CUTOFF,
        known_by=CUTOFF + timedelta(minutes=1),
    ).iloc[0]

    assert row["feature_schema_version"] == CONTEXT_FEATURE_SCHEMA_VERSION
    assert row["absence_home_count"] == 1
    assert row["absence_away_count"] == 1
    assert row["suspension_home_count"] == 0
    assert row["suspension_away_count"] == 1
    assert row["lineup_home_confirmed"] == 1
    assert row["lineup_away_confirmed"] == 0
    assert row["lineup_home_formation_known"] == 1
    assert row["lineup_away_formation_known"] == 0
    assert row["referee_known"] == 1
    assert row["rest_days_diff"] == 2
    assert row["away_travel_km"] == 320
    assert row["injuries_status_available"] == 1
    assert row["injuries_status_missing"] == 0


def test_unknown_context_stays_nan_instead_of_becoming_neutral_zero():
    snapshots = snapshots_frame([
        record("fixture", {
            "fixture_id": 11,
            "home": {"id": 1, "name": "Home"},
            "away": {"id": 2, "name": "Away"},
        }),
        record(
            "context_availability",
            context_availability_payload({
                "injuries": "provider_error",
                "lineups": "observed_empty",
            }),
        ),
        record("lineups", {"items": []}),
    ])

    row = build_match_context_features(
        snapshots,
        CUTOFF,
        known_by=CUTOFF + timedelta(minutes=1),
    ).iloc[0]

    assert pd.isna(row["absence_home_count"])
    assert pd.isna(row["lineup_home_starters"])
    assert pd.isna(row["home_rest_days"])
    assert pd.isna(row["away_travel_km"])
    assert row["injuries_status_provider_error"] == 1
    assert row["lineups_status_observed_empty"] == 1
    assert row["referee_status_missing"] == 1
    assert row["rest_status_missing"] == 1
    assert row["travel_status_missing"] == 1


def test_post_cutoff_context_cannot_replace_earlier_known_version():
    snapshots = snapshots_frame([
        record("fixture", fixture_payload()),
        record(
            "context_availability",
            context_availability_payload({"injuries": "observed_empty"}),
            version="known",
        ),
        record(
            "context_availability",
            context_availability_payload({"injuries": "available"}),
            available_at=CUTOFF + timedelta(seconds=1),
            version="future",
        ),
        record(
            "injuries",
            {"items": [{"team_id": 1, "player_id": 10}]},
            available_at=CUTOFF + timedelta(seconds=1),
            version="future",
        ),
    ])

    row = build_match_context_features(
        snapshots,
        CUTOFF,
        known_by=CUTOFF + timedelta(minutes=1),
    ).iloc[0]

    assert row["injuries_status_observed_empty"] == 1
    assert row["injuries_status_available"] == 0
    assert pd.isna(row["absence_home_count"])


def test_available_status_without_feature_payload_fails_closed():
    snapshots = snapshots_frame([
        record("fixture", fixture_payload()),
        record(
            "context_availability",
            context_availability_payload({"injuries": "available"}),
        ),
    ])

    with pytest.raises(ValueError, match="marked available but absent"):
        build_match_context_features(
            snapshots,
            CUTOFF,
            known_by=CUTOFF + timedelta(minutes=1),
        )
