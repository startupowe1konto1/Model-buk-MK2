from datetime import datetime, timezone

import pandas as pd
import pytest

from model_buk.snapshots import SnapshotRecord, latest_as_of, snapshots_frame


def _record(*, version: str, available_at: str, ingested_at: str, status: str) -> SnapshotRecord:
    return SnapshotRecord(
        entity_id="fixture:123",
        feature_group="lineup",
        event_time="2026-09-20T18:00:00+00:00",
        available_at=available_at,
        ingested_at=ingested_at,
        provider="example",
        provider_version=version,
        payload={"status": status},
        source_record_id=f"lineup-{version}",
    )


def test_latest_as_of_excludes_information_published_after_cutoff():
    frame = snapshots_frame([
        _record(
            version="predicted",
            available_at="2026-09-20T12:00:00+00:00",
            ingested_at="2026-09-20T12:01:00+00:00",
            status="predicted",
        ),
        _record(
            version="confirmed",
            available_at="2026-09-20T16:45:00+00:00",
            ingested_at="2026-09-20T16:46:00+00:00",
            status="confirmed",
        ),
    ])

    selected = latest_as_of(
        frame,
        "2026-09-20T15:00:00+00:00",
        known_by="2026-09-21T00:00:00+00:00",
    )

    assert len(selected) == 1
    assert selected.iloc[0]["payload"]["status"] == "predicted"


def test_latest_as_of_can_reconstruct_what_was_actually_ingested():
    frame = snapshots_frame([
        _record(
            version="v1",
            available_at="2026-09-20T12:00:00+00:00",
            ingested_at="2026-09-20T17:00:00+00:00",
            status="late-ingest",
        )
    ])

    selected = latest_as_of(
        frame,
        "2026-09-20T15:00:00+00:00",
        known_by="2026-09-20T16:00:00+00:00",
    )

    assert selected.empty


def test_future_event_is_allowed_when_information_is_already_available():
    record = _record(
        version="v1",
        available_at="2026-09-19T10:00:00+00:00",
        ingested_at="2026-09-19T10:00:05+00:00",
        status="scheduled",
    )
    selected = latest_as_of(
        snapshots_frame([record]),
        "2026-09-19T12:00:00+00:00",
        known_by="2026-09-19T12:00:00+00:00",
    )
    assert len(selected) == 1


def test_snapshot_rejects_timezone_free_timestamps():
    with pytest.raises(ValueError, match="timezone"):
        SnapshotRecord(
            entity_id="fixture:123",
            feature_group="odds",
            event_time=datetime(2026, 9, 20, 18, 0),
            available_at=datetime.now(timezone.utc),
            ingested_at=datetime.now(timezone.utc),
            provider="example",
            provider_version="v1",
            payload={},
        )


def test_latest_as_of_keeps_provider_specific_versions_separate():
    base = _record(
        version="v1",
        available_at="2026-09-20T12:00:00+00:00",
        ingested_at="2026-09-20T12:01:00+00:00",
        status="provider-a",
    ).to_dict()
    other = dict(base)
    other.update({"provider": "other", "payload": {"status": "provider-b"}})
    frame = pd.DataFrame([base, other])

    selected = latest_as_of(
        frame,
        "2026-09-20T13:00:00+00:00",
        known_by="2026-09-20T13:00:00+00:00",
    )
    assert set(selected["provider"]) == {"example", "other"}
