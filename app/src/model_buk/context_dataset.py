"""Build a versioned context-feature dataset from prospective snapshots."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from model_buk.context_features import (
    CONTEXT_FEATURE_SCHEMA_VERSION,
    CONTEXT_OUTPUT_COLUMNS,
    build_match_context_features,
)
from model_buk.snapshot_archive import SnapshotArchive
from model_buk.snapshots import snapshots_frame


CONTEXT_DATASET_SCHEMA_VERSION = "context-feature-dataset-v1"
CONTEXT_DATASET_COLUMNS = (
    "archive_run_id",
    "archive_records_sha256",
    "archive_source_id",
    *CONTEXT_OUTPUT_COLUMNS,
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_context_feature_dataset(archive: SnapshotArchive) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build unlabeled, point-in-time feature rows from all verified segments."""
    manifests = archive.manifests()
    frames: list[pd.DataFrame] = []
    for manifest in manifests:
        records = archive.load(
            str(manifest["run_id"]),
            cutoff=pd.Timestamp(manifest["cutoff"]).to_pydatetime(),
            source_id=str(manifest["source_id"]),
        )
        if not records:
            continue
        snapshot_frame = snapshots_frame(records)
        features = build_match_context_features(
            snapshot_frame,
            manifest["cutoff"],
            known_by=snapshot_frame["ingested_at"].max(),
        )
        if features.empty:
            continue
        features.insert(0, "archive_source_id", str(manifest["source_id"]))
        features.insert(0, "archive_records_sha256", str(manifest["records_sha256"]))
        features.insert(0, "archive_run_id", str(manifest["run_id"]))
        frames.append(features)

    dataset = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=CONTEXT_DATASET_COLUMNS)
    )
    dataset = dataset.loc[:, list(CONTEXT_DATASET_COLUMNS)]
    manifest = {
        "schema_version": CONTEXT_DATASET_SCHEMA_VERSION,
        "feature_schema_version": CONTEXT_FEATURE_SCHEMA_VERSION,
        "archive_segment_count": len(manifests),
        "feature_row_count": len(dataset),
        "fixture_count": int(dataset["fixture_id"].nunique()) if not dataset.empty else 0,
        "source_archive_sha256": _canonical_digest(manifests),
        "contains_outcomes": False,
        "promotion_eligible": False,
    }
    return dataset, manifest


def export_context_feature_dataset(
    archive_dir: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write a CSV and checksum manifest without overwriting prior evidence."""
    output = Path(output_path)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"output already exists: {output}")
    dataset, manifest = build_context_feature_dataset(SnapshotArchive(archive_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        dataset.to_csv(temporary, index=False)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    completed = {
        **manifest,
        "output_file": output.name,
        "output_sha256": sha256(output.read_bytes()).hexdigest(),
    }
    temporary_manifest = manifest_path.with_name(
        f".{manifest_path.name}.{os.getpid()}.tmp"
    )
    try:
        temporary_manifest.write_text(
            json.dumps(completed, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, manifest_path)
    finally:
        temporary_manifest.unlink(missing_ok=True)
    return completed


__all__ = [
    "CONTEXT_DATASET_COLUMNS",
    "CONTEXT_DATASET_SCHEMA_VERSION",
    "build_context_feature_dataset",
    "export_context_feature_dataset",
]
