from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from model_buk.current_pipeline import CurrentDataPipeline, RunContext
from model_buk.snapshot_archive import (
    ArchivingSnapshotAdapter,
    SnapshotArchive,
    SnapshotArchiveError,
)
from model_buk.snapshots import SnapshotRecord


CUTOFF = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def snapshot(*, version="v1", payload=None):
    return SnapshotRecord(
        entity_id="fixture:11",
        feature_group="fixture",
        event_time=CUTOFF + timedelta(hours=8),
        available_at=CUTOFF - timedelta(minutes=2),
        ingested_at=CUTOFF + timedelta(seconds=1),
        provider="test-provider",
        provider_version=version,
        payload=payload or {
            "fixture_id": 11,
            "kickoff": "2026-09-21T18:00:00Z",
            "league_code": "LL",
            "home": {"id": 1, "name": "Home"},
            "away": {"id": 2, "name": "Away"},
        },
    )


def test_archive_segment_is_immutable_reusable_and_loadable(tmp_path):
    archive = SnapshotArchive(tmp_path / "archive")
    context = RunContext("scheduled/run-1", CUTOFF)

    first = archive.write(context, [snapshot()])
    second = archive.write(context, [snapshot()])
    restored = archive.load(context.run_id)

    assert first.reused is False
    assert second.reused is True
    assert first.manifest == second.manifest
    assert first.manifest["record_count"] == 1
    assert restored == [snapshot()]
    assert sorted(path.suffix for path in archive.root.iterdir()) == [".json", ".jsonl"]


def test_archive_rejects_conflict_and_detects_tampering(tmp_path):
    archive = SnapshotArchive(tmp_path / "archive")
    context = RunContext("run-1", CUTOFF)
    segment = archive.write(context, [snapshot()])

    with pytest.raises(SnapshotArchiveError, match="conflicts"):
        archive.write(context, [snapshot(version="v2")])

    data_path = archive.root / segment.manifest["data_file"]
    data_path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(SnapshotArchiveError, match="checksum"):
        archive.load(context.run_id)


def test_archiving_adapter_skips_dry_run_and_exposes_sanitized_metadata(tmp_path):
    class Delegate:
        component_id = "delegate-v1"

        def __init__(self):
            self.calls = 0

        def fetch(self, context):
            self.calls += 1
            return [snapshot()]

    archive = SnapshotArchive(tmp_path / "archive")
    delegate = Delegate()
    adapter = ArchivingSnapshotAdapter(delegate, archive)

    assert adapter.fetch(RunContext("dry", CUTOFF, dry_run=True)) == [snapshot()]
    assert adapter.run_metadata is None
    assert not archive.root.exists()

    records = adapter.fetch(RunContext("live", CUTOFF))
    restored = adapter.fetch(RunContext("live", CUTOFF))

    assert records == [snapshot()]
    assert restored == records
    assert delegate.calls == 2
    metadata = adapter.run_metadata["snapshot_archive"]
    assert metadata["record_count"] == 1
    assert metadata["schema_version"] == "snapshot-archive-v1"
    assert str(tmp_path) not in repr(metadata)


def test_archiving_adapter_rejects_reuse_with_different_cutoff(tmp_path):
    adapter = ArchivingSnapshotAdapter(
        lambda context: [snapshot()],
        SnapshotArchive(tmp_path / "archive"),
    )
    adapter.fetch(RunContext("same-run", CUTOFF))

    with pytest.raises(SnapshotArchiveError, match="cutoff conflicts"):
        adapter.fetch(RunContext("same-run", CUTOFF + timedelta(minutes=1)))


def test_archive_listing_rejects_orphaned_segment_data(tmp_path):
    archive = SnapshotArchive(tmp_path / "archive")
    archive.write(RunContext("run-1", CUTOFF), [snapshot()])
    (archive.root / "segment-orphan.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SnapshotArchiveError, match="orphaned"):
        archive.manifests()


def test_pipeline_records_archive_identity_without_raw_payloads(tmp_path):
    class Delegate:
        component_id = "delegate-v1"

        def fetch(self, context):
            return [snapshot(payload={**snapshot().payload, "private": "do-not-copy"})]

    adapter = ArchivingSnapshotAdapter(
        Delegate(),
        SnapshotArchive(tmp_path / "archive"),
    )
    pipeline = CurrentDataPipeline(
        adapters={"provider": adapter},
        predictor=lambda frame, context: [],
        output_dir=tmp_path / "runs",
    )

    run = pipeline.run("live-run", cutoff=CUTOFF)

    metadata = run.manifest["adapter_metadata"]["provider"]["snapshot_archive"]
    assert metadata["record_count"] == 1
    assert "private" not in repr(run.manifest)
    assert "do-not-copy" not in repr(run.manifest)
