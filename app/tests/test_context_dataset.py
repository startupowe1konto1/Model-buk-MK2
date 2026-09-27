from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pandas as pd
import pytest

from model_buk.context_coverage import context_availability_payload
from model_buk.context_dataset import (
    build_context_feature_dataset,
    export_context_feature_dataset,
)
from model_buk.current_pipeline import RunContext
from model_buk.snapshot_archive import SnapshotArchive
from model_buk.snapshots import SnapshotRecord


def records(cutoff, *, fixture_id=11):
    common = {
        "event_time": cutoff + timedelta(hours=8),
        "available_at": cutoff - timedelta(minutes=2),
        "ingested_at": cutoff + timedelta(seconds=1),
        "provider": "test-provider",
        "provider_version": "v1",
    }
    return [
        SnapshotRecord(
            **common,
            entity_id=f"fixture:{fixture_id}",
            feature_group="fixture",
            payload={
                "fixture_id": fixture_id,
                "kickoff": (cutoff + timedelta(hours=8)).isoformat(),
                "league_code": "LL",
                "home": {"id": 1, "name": "Home"},
                "away": {"id": 2, "name": "Away"},
            },
        ),
        SnapshotRecord(
            **common,
            entity_id=f"fixture:{fixture_id}",
            feature_group="context_availability",
            payload=context_availability_payload({
                "injuries": "provider_error",
                "lineups": "observed_empty",
            }),
        ),
    ]


def test_builds_unlabeled_dataset_from_verified_archive_segments(tmp_path):
    archive = SnapshotArchive(tmp_path / "archive")
    first_cutoff = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(hours=1)
    archive.write(RunContext("run-1", first_cutoff), records(first_cutoff), source_id="source-v1")
    archive.write(
        RunContext("run-2", second_cutoff),
        records(second_cutoff, fixture_id=12),
        source_id="source-v1",
    )

    dataset, manifest = build_context_feature_dataset(archive)

    assert dataset["archive_run_id"].tolist() == ["run-1", "run-2"]
    assert dataset["fixture_id"].tolist() == ["11", "12"]
    assert dataset["injuries_status_provider_error"].tolist() == [1.0, 1.0]
    assert dataset["lineups_status_observed_empty"].tolist() == [1.0, 1.0]
    assert pd.isna(dataset.loc[0, "absence_home_count"])
    assert manifest["archive_segment_count"] == 2
    assert manifest["feature_row_count"] == 2
    assert manifest["contains_outcomes"] is False
    assert manifest["promotion_eligible"] is False


def test_export_writes_checksummed_csv_and_never_overwrites(tmp_path):
    archive = SnapshotArchive(tmp_path / "archive")
    cutoff = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    archive.write(RunContext("run-1", cutoff), records(cutoff), source_id="source-v1")
    output = tmp_path / "dataset" / "context.csv"

    manifest = export_context_feature_dataset(archive.root, output)
    saved_manifest = json.loads(
        output.with_suffix(".csv.manifest.json").read_text(encoding="utf-8")
    )

    assert manifest == saved_manifest
    assert manifest["output_sha256"] == sha256(output.read_bytes()).hexdigest()
    assert len(pd.read_csv(output)) == 1
    with pytest.raises(FileExistsError, match="already exists"):
        export_context_feature_dataset(archive.root, output)
