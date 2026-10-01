"""The archive format shared by the retention jobs, the bucket upload and the PC merge.

One *export* is a directory ``<archive>/<kind>/exports/<YYYY-MM>/<export_id>/`` holding

- one ``<table>.jsonl.gz`` per exported table: the first line is a header object
  ``{"format": ..., "table": ..., "columns": [...]}``, every further line one row as a JSON array in that column
  order (``null`` is SQL NULL; floats round-trip exactly);
- ``manifest.json``: export id, kind, creation time, per table the file name, columns, row count, byte size and
  SHA-256, plus free-form metadata of the job that wrote it.

An export is written into ``<export_id>.partial/`` and renamed to ``<export_id>/`` only when it is complete (the
rename is atomic on one file system), so anything without the ``.partial`` suffix is final and immutable.  The
uploader skips ``.partial`` directories; the job that owns an export decides on its next run whether a left-over
``.partial`` directory is finalised (its database already committed the export) or discarded (it did not).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "model-buk-archive-v1"
PARTIAL_SUFFIX = ".partial"
MANIFEST = "manifest.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry (rename durability) where the platform allows it."""
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass
class ExportedTable:
    table: str
    file: str
    columns: list[str]
    rows: int
    bytes: int
    sha256: str


@dataclass
class Manifest:
    export_id: str
    kind: str
    created_at: str
    tables: dict[str, ExportedTable] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    format: str = FORMAT

    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        payload = json.loads(text)
        tables = {name: ExportedTable(**entry) for name, entry in (payload.get("tables") or {}).items()}
        return cls(
            export_id=payload["export_id"],
            kind=payload["kind"],
            created_at=payload["created_at"],
            tables=tables,
            meta=payload.get("meta") or {},
            format=payload.get("format", FORMAT),
        )

    @classmethod
    def read(cls, directory: str | Path) -> Manifest:
        return cls.from_json((Path(directory) / MANIFEST).read_text(encoding="utf-8"))


def export_dir(archive_root: str | Path, kind: str, export_id: str, created: datetime) -> Path:
    """Final directory of an export: ``<archive>/<kind>/exports/<YYYY-MM>/<export_id>``."""
    return Path(archive_root) / kind / "exports" / created.astimezone(timezone.utc).strftime("%Y-%m") / export_id


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def write_jsonl_gz(path: Path, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    """Write ``rows`` as gzip JSONL (header line first); returns the number of data rows.  Flushed to disk."""
    count = 0
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as zipped:
            text = io.TextIOWrapper(zipped, encoding="utf-8", newline="\n")
            header = {"format": FORMAT, "table": table, "columns": list(columns)}
            text.write(json.dumps(header, ensure_ascii=False) + "\n")
            for row in rows:
                if len(row) != len(columns):
                    raise ValueError(f"{table}: row has {len(row)} values, expected {len(columns)}")
                text.write(json.dumps([_json_value(v) for v in row], ensure_ascii=False, allow_nan=False) + "\n")
                count += 1
            text.flush()
            text.detach()
        raw.flush()
        os.fsync(raw.fileno())
    return count


def read_jsonl_gz(path: str | Path) -> tuple[dict[str, Any], Iterator[list[Any]]]:
    """``(header, rows)`` of a table file; ``rows`` is a generator (the file stays open until it is exhausted)."""
    handle = gzip.open(path, "rt", encoding="utf-8")
    first = handle.readline()
    if not first:
        handle.close()
        raise ValueError(f"{path}: empty archive file")
    header = json.loads(first)

    def rows() -> Iterator[list[Any]]:
        try:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
        finally:
            handle.close()

    return header, rows()


def count_rows(path: str | Path) -> int:
    header, rows = read_jsonl_gz(path)
    return sum(1 for _ in rows)


class ExportWriter:
    """Write one export atomically (see the module docstring).

    Usage::

        writer = ExportWriter(archive_root, "odds", export_id, created)
        writer.write_table("quotes", columns, rows)
        writer.verify({"quotes": expected})      # re-reads every file
        ... commit the source database ...
        writer.finalize(meta)                    # manifest + rename .partial -> final
    """

    def __init__(self, archive_root: str | Path, kind: str, export_id: str, created: datetime | None = None) -> None:
        self.created = created or utc_now()
        self.kind = kind
        self.export_id = export_id
        self.final_dir = export_dir(archive_root, kind, export_id, self.created)
        self.partial_dir = self.final_dir.with_name(self.final_dir.name + PARTIAL_SUFFIX)
        if self.final_dir.exists():
            raise FileExistsError(f"export {self.final_dir} already exists")
        if self.partial_dir.exists():
            shutil.rmtree(self.partial_dir)
        self.partial_dir.mkdir(parents=True)
        self.manifest = Manifest(export_id=export_id, kind=kind, created_at=iso(self.created))

    def write_table(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> ExportedTable:
        name = f"{table}.jsonl.gz"
        path = self.partial_dir / name
        count = write_jsonl_gz(path, table, columns, rows)
        entry = ExportedTable(
            table=table, file=name, columns=list(columns), rows=count, bytes=path.stat().st_size, sha256=sha256_file(path)
        )
        self.manifest.tables[table] = entry
        return entry

    def verify(self, expected: dict[str, int]) -> None:
        """Re-read every file: row counts must equal ``expected`` (and the manifest), checksums must match."""
        for table, want in expected.items():
            entry = self.manifest.tables.get(table)
            if entry is None:
                raise ValueError(f"export {self.export_id}: table {table} was not written")
            path = self.partial_dir / entry.file
            if sha256_file(path) != entry.sha256:
                raise ValueError(f"export {self.export_id}: {entry.file} changed after it was written")
            got = count_rows(path)
            if got != want or entry.rows != want:
                raise ValueError(f"export {self.export_id}: {table} has {got} rows on disk, {entry.rows} written, {want} expected")

    def manifest_json(self, meta: dict[str, Any] | None = None) -> str:
        if meta:
            self.manifest.meta.update(meta)
        return self.manifest.to_json()

    def finalize(self, meta: dict[str, Any] | None = None) -> Path:
        text = self.manifest_json(meta)
        finalize_partial(self.partial_dir, text)
        return self.final_dir

    def discard(self) -> None:
        shutil.rmtree(self.partial_dir, ignore_errors=True)


def finalize_partial(partial_dir: str | Path, manifest_text: str) -> Path:
    """Write the manifest into a ``.partial`` directory and rename it to its final name (idempotent)."""
    partial = Path(partial_dir)
    final = partial.with_name(partial.name[: -len(PARTIAL_SUFFIX)]) if partial.name.endswith(PARTIAL_SUFFIX) else partial
    if final.exists() and not partial.exists():
        return final
    temporary = partial / (MANIFEST + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(manifest_text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, partial / MANIFEST)
    os.replace(partial, final)
    _fsync_dir(final.parent)
    return final


def partial_exports(archive_root: str | Path, kind: str) -> list[Path]:
    """Left-over ``.partial`` export directories of ``kind`` (from an interrupted run)."""
    base = Path(archive_root) / kind / "exports"
    if not base.exists():
        return []
    return sorted(p for p in base.glob(f"*/*{PARTIAL_SUFFIX}") if p.is_dir())


def final_exports(archive_root: str | Path, kind: str) -> list[Path]:
    """Complete export directories of ``kind`` (those with a manifest), oldest first."""
    base = Path(archive_root) / kind / "exports"
    if not base.exists():
        return []
    return sorted(p.parent for p in base.glob(f"*/*/{MANIFEST}") if not p.parent.name.endswith(PARTIAL_SUFFIX))


def export_id_of(directory: str | Path) -> str:
    name = Path(directory).name
    return name[: -len(PARTIAL_SUFFIX)] if name.endswith(PARTIAL_SUFFIX) else name


__all__ = [
    "FORMAT",
    "MANIFEST",
    "PARTIAL_SUFFIX",
    "ExportWriter",
    "ExportedTable",
    "Manifest",
    "count_rows",
    "export_dir",
    "export_id_of",
    "final_exports",
    "finalize_partial",
    "iso",
    "partial_exports",
    "read_jsonl_gz",
    "sha256_file",
    "utc_now",
    "write_jsonl_gz",
]
