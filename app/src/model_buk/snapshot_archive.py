"""Append-only, integrity-checked storage for prospective snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from model_buk.current_pipeline import RunContext
from model_buk.snapshots import SnapshotRecord


SNAPSHOT_ARCHIVE_SCHEMA_VERSION = "snapshot-archive-v1"


class SnapshotArchiveError(RuntimeError):
    """An archive segment is incomplete, corrupt or conflicting."""


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise SnapshotArchiveError("archive timestamps must include a timezone")
        return timestamp.tz_convert("UTC").isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _cutoff_text(value: datetime) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise SnapshotArchiveError("archive cutoff must include a timezone")
    return timestamp.tz_convert("UTC").isoformat()


def _record_line(record: SnapshotRecord) -> str:
    return json.dumps(
        _json_value(record.to_dict()),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class SnapshotArchiveSegment:
    manifest: Mapping[str, Any]
    reused: bool


class SnapshotArchive:
    """Store one immutable JSONL segment per pipeline run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not str(self.root):
            raise ValueError("snapshot archive root must not be empty")

    @staticmethod
    def _segment_key(run_id: str) -> str:
        return sha256(run_id.encode("utf-8")).hexdigest()[:24]

    def _paths(self, run_id: str) -> tuple[Path, Path, Path]:
        key = self._segment_key(run_id)
        return (
            self.root / f"segment-{key}.jsonl",
            self.root / f"segment-{key}.manifest.json",
            self.root / f".segment-{key}.lock",
        )

    def write(
        self,
        context: RunContext,
        records: Iterable[SnapshotRecord],
        *,
        source_id: str = "unspecified",
    ) -> SnapshotArchiveSegment:
        normalized_run_id = str(context.run_id).strip()
        if not normalized_run_id:
            raise ValueError("run_id must not be blank")
        normalized_source_id = str(source_id).strip()
        if not normalized_source_id:
            raise ValueError("source_id must not be blank")
        materialized = tuple(records)
        lines = [_record_line(record) for record in materialized]
        data = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        data_hash = sha256(data).hexdigest()
        data_path, manifest_path, lock_path = self._paths(normalized_run_id)
        manifest = {
            "schema_version": SNAPSHOT_ARCHIVE_SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "cutoff": _cutoff_text(context.cutoff),
            "source_id": normalized_source_id,
            "record_count": len(materialized),
            "records_sha256": data_hash,
            "data_file": data_path.name,
        }

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise SnapshotArchiveError("snapshot archive segment is already being written") from exc
        os.close(descriptor)
        try:
            existing = self._load_existing(manifest_path, expected=manifest)
            if existing is not None:
                return SnapshotArchiveSegment(existing, reused=True)

            if data_path.exists():
                if sha256(data_path.read_bytes()).hexdigest() != data_hash:
                    raise SnapshotArchiveError("existing archive data conflicts with this run")
            else:
                self._atomic_write_bytes(data_path, data)
            self._atomic_write_json(manifest_path, manifest)
            return SnapshotArchiveSegment(manifest, reused=False)
        finally:
            lock_path.unlink(missing_ok=True)

    def load(
        self,
        run_id: str,
        *,
        cutoff: datetime | None = None,
        source_id: str | None = None,
    ) -> list[SnapshotRecord]:
        data_path, manifest_path, _ = self._paths(str(run_id))
        manifest = self._load_existing(manifest_path)
        if manifest is None:
            raise SnapshotArchiveError("snapshot archive segment does not exist")
        if manifest.get("run_id") != str(run_id):
            raise SnapshotArchiveError("snapshot archive run_id does not match its path")
        if cutoff is not None and manifest.get("cutoff") != _cutoff_text(cutoff):
            raise SnapshotArchiveError("snapshot archive cutoff conflicts with this run")
        if source_id is not None and manifest.get("source_id") != str(source_id):
            raise SnapshotArchiveError("snapshot archive source conflicts with this run")
        data = data_path.read_bytes() if data_path.is_file() else None
        if data is None or sha256(data).hexdigest() != manifest.get("records_sha256"):
            raise SnapshotArchiveError("snapshot archive data checksum mismatch")
        records: list[SnapshotRecord] = []
        try:
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    records.append(SnapshotRecord(**json.loads(line)))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SnapshotArchiveError("snapshot archive data is invalid") from exc
        if len(records) != manifest.get("record_count"):
            raise SnapshotArchiveError("snapshot archive record count mismatch")
        return records

    def load_if_exists(
        self,
        context: RunContext,
        *,
        source_id: str,
    ) -> list[SnapshotRecord] | None:
        _, manifest_path, _ = self._paths(context.run_id)
        if not manifest_path.exists():
            return None
        return self.load(
            context.run_id,
            cutoff=context.cutoff,
            source_id=source_id,
        )

    def manifests(self) -> list[dict[str, Any]]:
        """Return every verified segment manifest in chronological order."""
        if not self.root.exists():
            return []
        manifest_paths = sorted(self.root.glob("segment-*.manifest.json"))
        manifests: list[dict[str, Any]] = []
        expected_data_files: set[str] = set()
        run_ids: set[str] = set()
        for manifest_path in manifest_paths:
            manifest = self._load_existing(manifest_path)
            assert manifest is not None
            run_id = str(manifest.get("run_id") or "")
            if not run_id or run_id in run_ids:
                raise SnapshotArchiveError("snapshot archive contains duplicate or blank run_id")
            run_ids.add(run_id)
            expected_data_files.add(str(manifest["data_file"]))
            manifests.append(manifest)
        actual_data_files = {path.name for path in self.root.glob("segment-*.jsonl")}
        if actual_data_files != expected_data_files:
            raise SnapshotArchiveError("snapshot archive contains orphaned segment data")
        return sorted(manifests, key=lambda item: (str(item["cutoff"]), str(item["run_id"])))

    def _load_existing(
        self,
        manifest_path: Path,
        *,
        expected: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotArchiveError("snapshot archive manifest is invalid") from exc
        if manifest.get("schema_version") != SNAPSHOT_ARCHIVE_SCHEMA_VERSION:
            raise SnapshotArchiveError("snapshot archive schema version is unsupported")
        if expected is not None and manifest != dict(expected):
            raise SnapshotArchiveError("existing snapshot archive segment conflicts with this run")
        expected_data_file = manifest_path.name.replace(".manifest.json", ".jsonl")
        if manifest.get("data_file") != expected_data_file:
            raise SnapshotArchiveError("snapshot archive manifest has an invalid data path")
        data_path = manifest_path.with_name(expected_data_file)
        if not data_path.is_file():
            raise SnapshotArchiveError("snapshot archive manifest points to missing data")
        if sha256(data_path.read_bytes()).hexdigest() != manifest.get("records_sha256"):
            raise SnapshotArchiveError("snapshot archive data checksum mismatch")
        return manifest

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
        payload = json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class ArchivingSnapshotAdapter:
    """Persist successful adapter reads before returning them to the pipeline."""

    def __init__(self, delegate: Any, archive: SnapshotArchive) -> None:
        self.delegate = delegate
        self.archive = archive
        delegate_id = getattr(delegate, "component_id", type(delegate).__qualname__)
        digest = sha256(
            f"{delegate_id}|{SNAPSHOT_ARCHIVE_SCHEMA_VERSION}".encode("utf-8")
        ).hexdigest()[:16]
        self.component_id = f"archiving-snapshot-adapter-v1:{digest}"
        self._run_metadata: dict[str, Any] | None = None

    @property
    def run_metadata(self) -> Mapping[str, Any] | None:
        return self._run_metadata

    def fetch(self, context: RunContext) -> list[SnapshotRecord]:
        if not context.dry_run:
            archived = self.archive.load_if_exists(
                context,
                source_id=self.component_id,
            )
            if archived is not None:
                _, manifest_path, _ = self.archive._paths(context.run_id)
                manifest = self.archive._load_existing(manifest_path)
                assert manifest is not None
                self._run_metadata = self._metadata(manifest)
                return archived
        operation = getattr(self.delegate, "fetch", self.delegate)
        if not callable(operation):
            raise TypeError("archived adapter delegate is not callable")
        fetched = operation(context)
        if fetched is None:
            raise SnapshotArchiveError("archived adapter returned no iterable")
        records = [
            item if isinstance(item, SnapshotRecord) else SnapshotRecord(**dict(item))
            for item in fetched
        ]
        self._run_metadata = None
        if not context.dry_run:
            segment = self.archive.write(
                context,
                records,
                source_id=self.component_id,
            )
            self._run_metadata = self._metadata(segment.manifest)
        return records

    @staticmethod
    def _metadata(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "snapshot_archive": {
                key: manifest[key]
                for key in (
                    "schema_version",
                    "record_count",
                    "records_sha256",
                    "data_file",
                    "source_id",
                )
            }
        }


__all__ = [
    "ArchivingSnapshotAdapter",
    "SNAPSHOT_ARCHIVE_SCHEMA_VERSION",
    "SnapshotArchive",
    "SnapshotArchiveError",
    "SnapshotArchiveSegment",
]
