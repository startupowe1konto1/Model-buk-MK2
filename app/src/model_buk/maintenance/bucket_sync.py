"""Off-box copy of ``<data>/archive``: upload to an S3 bucket (server), pull and merge back (PC).

Credentials come from the environment, under the names Railway's Storage Buckets use:

========================  =====================================================  ============================
variable                   meaning                                                Railway bucket variable
========================  =====================================================  ============================
``AWS_S3_BUCKET_NAME``     bucket name for the S3 API                             ``BUCKET``
``AWS_ENDPOINT_URL``       S3 endpoint, e.g. ``https://t3.storageapi.dev``        ``ENDPOINT``
``AWS_ACCESS_KEY_ID``      key id                                                 ``ACCESS_KEY_ID``
``AWS_SECRET_ACCESS_KEY``  secret key                                             ``SECRET_ACCESS_KEY``
``AWS_DEFAULT_REGION``     region (``auto``)                                      ``REGION``
``AWS_S3_URL_STYLE``       ``virtual`` (default) or ``path``                      (credentials tab / CLI)
========================  =====================================================  ============================

(the ``AWS_*`` names are what ``railway bucket credentials`` prints and what the bucket's "AWS SDK" variable
preset injects; the plain Railway names in the last column are accepted as a fallback.)

Upload (:func:`upload_archive`, the server's daily job): every final file under the archive -- not ``*.tmp``, not
hidden, not inside a ``*.partial`` export directory -- is uploaded once to ``<prefix><relative path>`` with its
SHA-256 in the object metadata, then checked with a HEAD request (size and checksum).  A local state database
(``<archive>/.upload_state.sqlite``) remembers verified uploads, so unchanged files cost nothing on later runs.
Local files older than ``keep_local_days`` are deleted only when the state says they were verified *and* a fresh
HEAD confirms the object is still there with the same size and checksum.

Pull (:func:`pull_archive`, on the PC): downloads every object under the prefix that the local archive lacks
(atomic temp-file rename, size and checksum verified), then :func:`merge_exports` merges the exports into the
PC's full databases: odds exports into ``api-football-odds/odds.sqlite3`` (duplicates skipped; the PC's compact
snapshot table is recomputed for those fixtures from the merged rows, so it is kept forever there), scanner leg
and forecast-log exports into ``archive/scanner/register_archive.sqlite``.  Every merged export is recorded in an
``archive_imports`` table of its target, so a re-run merges nothing twice.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from model_buk.maintenance.exports import (
    MANIFEST,
    PARTIAL_SUFFIX,
    Manifest,
    final_exports,
    iso,
    read_jsonl_gz,
    sha256_file,
    utc_now,
)

DEFAULT_PREFIX = "archive/"
STATE_DB = ".upload_state.sqlite"
ENV_NAMES: dict[str, tuple[str, ...]] = {
    "bucket": ("AWS_S3_BUCKET_NAME", "BUCKET"),
    "endpoint": ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "ENDPOINT"),
    "access_key": ("AWS_ACCESS_KEY_ID", "ACCESS_KEY_ID"),
    "secret_key": ("AWS_SECRET_ACCESS_KEY", "SECRET_ACCESS_KEY"),
    "region": ("AWS_DEFAULT_REGION", "AWS_REGION", "REGION"),
    "url_style": ("AWS_S3_URL_STYLE",),
}


class S3Client(Protocol):  # the subset of the boto3 S3 client that is used (tests pass a fake)
    def put_object(self, **kwargs: Any) -> Any: ...
    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def list_objects_v2(self, **kwargs: Any) -> Mapping[str, Any]: ...


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def _user_env_windows(name: str) -> str | None:
    """A user-level environment variable from the Windows registry (set with SetEnvironmentVariable(..., "User"))."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            value, _ = winreg.QueryValueEx(handle, name)
        return str(value).strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class BucketConfig:
    bucket: str
    endpoint: str | None
    access_key: str
    secret_key: str
    region: str = "auto"
    url_style: str = "virtual"
    prefix: str = DEFAULT_PREFIX

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, prefix: str = DEFAULT_PREFIX, windows_user_env: bool = True) -> BucketConfig:
        source = os.environ if env is None else env
        values: dict[str, str | None] = {}
        for field_name, names in ENV_NAMES.items():
            value = next((source.get(n, "").strip() for n in names if source.get(n, "").strip()), None)
            if value is None and windows_user_env and env is None:
                value = next((v for v in (_user_env_windows(n) for n in names) if v), None)
            values[field_name] = value
        missing = [ENV_NAMES[k][0] for k in ("bucket", "access_key", "secret_key") if not values[k]]
        if missing:
            raise RuntimeError(f"bucket credentials missing: {', '.join(missing)} (see model_buk.maintenance.bucket_sync)")
        style = (values["url_style"] or "virtual").lower()
        if style not in ("virtual", "path"):
            raise RuntimeError(f"AWS_S3_URL_STYLE must be 'virtual' or 'path', not {style!r}")
        normalised = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        return cls(
            bucket=str(values["bucket"]),
            endpoint=values["endpoint"],
            access_key=str(values["access_key"]),
            secret_key=str(values["secret_key"]),
            region=values["region"] or "auto",
            url_style=style,
            prefix=normalised,
        )

    def describe(self) -> str:
        """For logs: never the keys."""
        return f"bucket={self.bucket} endpoint={self.endpoint or '-'} region={self.region} style={self.url_style} prefix={self.prefix}"


def make_client(config: BucketConfig) -> S3Client:  # pragma: no cover - needs boto3 and a bucket
    """A boto3 S3 client for the bucket (boto3 is imported here only; the server image installs it)."""
    import boto3
    from botocore.config import Config

    options: dict[str, Any] = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": config.url_style},
        "retries": {"max_attempts": 5, "mode": "standard"},
        "connect_timeout": 20,
        "read_timeout": 120,
    }
    try:  # botocore >= 1.36 adds CRC checksums to every request by default; S3-compatible stores may refuse them
        client_config = Config(**options, request_checksum_calculation="when_required", response_checksum_validation="when_required")
    except TypeError:  # older botocore: no such options (and no default checksums either)
        client_config = Config(**options)
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        region_name=config.region,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        config=client_config,
    )


# --------------------------------------------------------------------------- #
# Files of the archive
# --------------------------------------------------------------------------- #


def archive_files(archive_root: str | Path) -> Iterator[tuple[str, Path]]:
    """``(relative posix path, path)`` of every final file of the archive, sorted."""
    root = Path(archive_root)
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part.startswith(".") for part in parts):
            continue
        if any(part.endswith(PARTIAL_SUFFIX) for part in parts[:-1]):
            continue
        if path.name.endswith((".tmp", ".partial")):
            continue
        yield relative.as_posix(), path


def _not_found(error: Exception) -> bool:
    response = getattr(error, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404 or isinstance(error, (KeyError, FileNotFoundError))


def remote_matches(client: S3Client, bucket: str, key: str, size: int, sha256: str) -> bool:
    """True when the object exists with this size and SHA-256 metadata."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:  # noqa: BLE001 - botocore raises ClientError; a fake raises KeyError
        if _not_found(error):
            return False
        raise
    metadata = {str(k).lower(): str(v) for k, v in (head.get("Metadata") or {}).items()}
    return int(head.get("ContentLength", -1)) == int(size) and metadata.get("sha256") == sha256


class UploadState:
    """``<archive>/.upload_state.sqlite``: what was uploaded and verified, and what was pruned locally."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(path, timeout=60)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS uploads (rel_path TEXT PRIMARY KEY, key TEXT NOT NULL, size INTEGER NOT NULL, "
                "mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, verified_at TEXT NOT NULL, pruned_at TEXT)"
            )
            connection.commit()

    def get(self, rel_path: str) -> tuple[str, int, int, str, str, str | None] | None:
        with closing(sqlite3.connect(self.path, timeout=60)) as connection:
            return connection.execute(
                "SELECT key, size, mtime_ns, sha256, verified_at, pruned_at FROM uploads WHERE rel_path = ?", (rel_path,)
            ).fetchone()

    def record(self, rel_path: str, key: str, size: int, mtime_ns: int, sha256: str, when: datetime) -> None:
        with closing(sqlite3.connect(self.path, timeout=60)) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO uploads VALUES (?, ?, ?, ?, ?, ?, NULL)", (rel_path, key, size, mtime_ns, sha256, iso(when))
            )
            connection.commit()

    def mark_pruned(self, rel_path: str, when: datetime) -> None:
        with closing(sqlite3.connect(self.path, timeout=60)) as connection:
            connection.execute("UPDATE uploads SET pruned_at = ? WHERE rel_path = ?", (iso(when), rel_path))
            connection.commit()


@dataclass
class UploadReport:
    files: int = 0
    uploaded: int = 0
    already_remote: int = 0
    unchanged: int = 0
    bytes_uploaded: int = 0
    pruned: int = 0
    prune_refused: int = 0
    failed: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"files={self.files} uploaded={self.uploaded} ({self.bytes_uploaded / 2**20:.1f} MiB) "
            f"already_remote={self.already_remote} unchanged={self.unchanged} pruned_local={self.pruned} "
            f"prune_refused={self.prune_refused} failed={len(self.failed)}"
            + (f" first_failure={self.failed[0][:160]}" if self.failed else "")
        )


def upload_archive(
    archive_root: str | Path,
    client: S3Client,
    config: BucketConfig,
    *,
    keep_local_days: float | None = 14.0,
    now: datetime | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> UploadReport:
    """Upload new archive files, verify them, prune verified local files older than ``keep_local_days``."""
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    say = log or (lambda _text: None)
    stamp = (now or utc_now()).astimezone(timezone.utc)
    state = UploadState(root / STATE_DB)
    report = UploadReport()
    for rel, path in list(archive_files(root)):
        report.files += 1
        key = config.prefix + rel
        try:
            info = path.stat()
            known = state.get(rel)
            if known is not None and known[1] == info.st_size and known[2] == info.st_mtime_ns:
                report.unchanged += 1
            else:
                digest = sha256_file(path)
                if remote_matches(client, config.bucket, key, info.st_size, digest):
                    report.already_remote += 1
                elif dry_run:
                    say(f"would upload {key} ({info.st_size} bytes)")
                    continue
                else:
                    with open(path, "rb") as body:
                        client.put_object(
                            Bucket=config.bucket, Key=key, Body=body, ContentLength=info.st_size, Metadata={"sha256": digest}
                        )
                    if not remote_matches(client, config.bucket, key, info.st_size, digest):
                        raise RuntimeError(f"verification failed after upload of {key}")
                    report.uploaded += 1
                    report.bytes_uploaded += info.st_size
                    say(f"uploaded {key}")
                if not dry_run:
                    state.record(rel, key, info.st_size, info.st_mtime_ns, digest, stamp)
            if keep_local_days is None or dry_run:
                continue
            age = stamp - datetime.fromtimestamp(info.st_mtime, timezone.utc)
            if age < timedelta(days=float(keep_local_days)):
                continue
            known = state.get(rel)
            if known is None or known[1] != info.st_size or known[2] != info.st_mtime_ns:
                report.prune_refused += 1
                continue
            if not remote_matches(client, config.bucket, key, info.st_size, known[3]):
                report.prune_refused += 1
                say(f"not pruning {rel}: the bucket copy is missing or different")
                continue
            path.unlink()
            state.mark_pruned(rel, stamp)
            report.pruned += 1
            _remove_empty_parents(path.parent, root)
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the others
            report.failed.append(f"{rel}: {type(error).__name__}: {error}")
    return report


def _remove_empty_parents(directory: Path, root: Path) -> None:
    current = directory
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


# --------------------------------------------------------------------------- #
# Pull to the PC
# --------------------------------------------------------------------------- #


def list_remote(client: S3Client, bucket: str, prefix: str) -> Iterator[tuple[str, int]]:
    """``(key, size)`` of every object under ``prefix`` (paginated)."""
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        for item in page.get("Contents") or []:
            yield str(item["Key"]), int(item.get("Size", 0))
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken")
        if not token:
            return


@dataclass
class PullReport:
    remote: int = 0
    downloaded: int = 0
    present: int = 0
    bytes_downloaded: int = 0
    merged: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        merged = " ".join(f"{k}={v}" for k, v in sorted(self.merged.items())) or "none"
        return (
            f"remote={self.remote} downloaded={self.downloaded} ({self.bytes_downloaded / 2**20:.1f} MiB) present={self.present} "
            f"merged: {merged} failed={len(self.failed)}" + (f" first_failure={self.failed[0][:160]}" if self.failed else "")
        )


def download_object(client: S3Client, bucket: str, key: str, target: Path, size: int) -> int:
    """Stream one object to ``target`` (temp file + rename); checks the size and the SHA-256 metadata if present."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    digest = hashlib.sha256()
    written = 0
    with open(temporary, "wb") as handle:
        while True:
            chunk = body.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            written += len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    expected = {str(k).lower(): str(v) for k, v in (response.get("Metadata") or {}).items()}.get("sha256")
    if written != size or (expected and expected != digest.hexdigest()):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"download of {key} incomplete or corrupt ({written} of {size} bytes)")
    os.replace(temporary, target)
    return written


def pull_archive(
    archive_root: str | Path,
    client: S3Client,
    config: BucketConfig,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> PullReport:
    """Download every object under the prefix that the local archive does not hold with the same size."""
    root = Path(archive_root)
    say = log or (lambda _text: None)
    report = PullReport()
    for key, size in list_remote(client, config.bucket, config.prefix):
        report.remote += 1
        relative = key[len(config.prefix) :] if key.startswith(config.prefix) else key
        if not relative or relative.endswith("/") or ".." in Path(relative).parts:
            continue
        target = root / Path(*relative.split("/"))
        if target.exists() and target.stat().st_size == size:
            report.present += 1
            continue
        if dry_run:
            say(f"would download {key}")
            continue
        try:
            report.bytes_downloaded += download_object(client, config.bucket, key, target, size)
            report.downloaded += 1
            say(f"downloaded {key}")
        except Exception as error:  # noqa: BLE001
            report.failed.append(f"{key}: {type(error).__name__}: {error}")
    return report


# --------------------------------------------------------------------------- #
# Merging exports into the PC's databases
# --------------------------------------------------------------------------- #

_IMPORTS_SQL = (
    "CREATE TABLE IF NOT EXISTS archive_imports (export_id TEXT PRIMARY KEY, kind TEXT NOT NULL, merged_at TEXT NOT NULL, "
    "rows_json TEXT NOT NULL)"
)


def _imported(connection: sqlite3.Connection) -> set[str]:
    connection.execute(_IMPORTS_SQL)
    return {row[0] for row in connection.execute("SELECT export_id FROM archive_imports")}


def _table_rows(directory: Path, manifest: Manifest, table: str) -> tuple[list[str], Iterator[list[Any]]]:
    entry = manifest.tables[table]
    header, rows = read_jsonl_gz(directory / entry.file)
    return list(header["columns"]), rows


def merge_odds_export(directory: Path, odds_root: Path, *, snapshots: bool = True) -> dict[str, int]:
    """Merge one odds export into the odds archive at ``odds_root`` (created if missing); returns rows added."""
    from model_buk.ingestion.api_football_odds_archive import OddsArchive
    from model_buk.maintenance.odds_retention import QUOTE_COLUMNS, SNAPSHOT_DB_NAME, write_snapshots

    manifest = Manifest.read(directory)
    archive = OddsArchive(odds_root)  # creates / migrates the schema
    added = {"fixtures": 0, "scans": 0, "observations": 0, "quotes": 0}
    with closing(sqlite3.connect(archive.db_path, timeout=300, isolation_level=None)) as connection:
        if manifest.export_id in _imported(connection):
            return {}
        connection.execute("BEGIN IMMEDIATE")
        try:
            target_cols = {t: [r[1] for r in connection.execute(f"PRAGMA table_info({t})")] for t in ("fixtures", "scans")}
            for table in ("fixtures", "scans"):
                if table not in manifest.tables:
                    continue
                columns, rows = _table_rows(directory, manifest, table)
                keep = [i for i, c in enumerate(columns) if c in target_cols[table]]
                names = [columns[i] for i in keep]
                sql = f"INSERT OR IGNORE INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
                for row in rows:
                    added[table] += connection.execute(sql, [row[i] for i in keep]).rowcount
                if table == "fixtures":
                    # a newer sighting of a fixture we already had: keep its latest kickoff and sighting times
                    columns_f, rows_f = _table_rows(directory, manifest, "fixtures")
                    index = {c: i for i, c in enumerate(columns_f)}
                    for row in rows_f:
                        connection.execute(
                            "UPDATE fixtures SET kickoff_utc = CASE WHEN ? > last_seen_at THEN ? ELSE kickoff_utc END, "
                            "first_seen_at = MIN(first_seen_at, ?), last_seen_at = MAX(last_seen_at, ?) WHERE fixture_id = ?",
                            (row[index["last_seen_at"]], row[index["kickoff_utc"]], row[index["first_seen_at"]], row[index["last_seen_at"]], row[index["fixture_id"]]),
                        )
            columns, rows = _table_rows(directory, manifest, "observations")
            sql = f"INSERT OR IGNORE INTO observations ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
            for row in rows:
                added["observations"] += connection.execute(sql, row).rowcount
            columns, rows = _table_rows(directory, manifest, "quotes")
            if tuple(columns) != QUOTE_COLUMNS:
                raise ValueError(f"{manifest.export_id}: unexpected quote columns {columns}")
            exists = (
                "SELECT 1 FROM quotes WHERE fixture_id = ? AND bookmaker_id = ? AND bet_id = ? AND selection = ? "
                "AND fetched_at = ? AND scan_id = ? LIMIT 1"
            )
            insert = f"INSERT INTO quotes ({', '.join(QUOTE_COLUMNS)}) VALUES ({', '.join('?' * len(QUOTE_COLUMNS))})"
            for row in rows:
                key = (row[0], row[1], row[3], row[5], row[8], row[9])
                if connection.execute(exists, key).fetchone() is None:
                    connection.execute(insert, row)
                    added["quotes"] += 1
            connection.execute(
                "INSERT INTO archive_imports VALUES (?, ?, ?, ?)",
                (manifest.export_id, manifest.kind, iso(utc_now()), _json(added)),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        fixture_ids = [int(i) for i in manifest.meta.get("fixture_ids") or []]
        if snapshots and fixture_ids:
            inside = ",".join(str(i) for i in fixture_ids)
            fixtures = {
                int(r[0]): (r[1], r[2], r[3])
                for r in connection.execute(f"SELECT fixture_id, league_id, season, kickoff_utc FROM fixtures WHERE fixture_id IN ({inside})")
            }
            quotes = connection.execute(
                f"SELECT {', '.join(QUOTE_COLUMNS)} FROM quotes WHERE fixture_id IN ({inside}) "
                "ORDER BY fixture_id, bookmaker_id, bet_id, selection, fetched_at, rowid"
            ).fetchall()
            write_snapshots(Path(odds_root) / SNAPSHOT_DB_NAME, fixtures, quotes, manifest.export_id)
    return added


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def merge_table_export(directory: Path, target_db: Path, primary_key: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    """Merge the tables of a scanner / forecasts export into ``target_db`` (one table per exported table, columns
    added as they appear, ``INSERT OR IGNORE`` on the given primary keys); returns rows added per table."""
    manifest = Manifest.read(directory)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    added: dict[str, int] = {}
    with closing(sqlite3.connect(target_db, timeout=300, isolation_level=None)) as connection:
        if manifest.export_id in _imported(connection):
            return {}
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, entry in manifest.tables.items():
                columns = list(entry.columns)
                existing = [r[1] for r in connection.execute(f"PRAGMA table_info({table})")]
                if not existing:
                    key = primary_key.get(table)
                    pk = f", PRIMARY KEY ({', '.join(key)})" if key else ""
                    connection.execute(f"CREATE TABLE {table} ({', '.join(columns)}{pk})")
                else:
                    for column in columns:
                        if column not in existing:
                            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                header, rows = read_jsonl_gz(directory / entry.file)
                sql = f"INSERT OR IGNORE INTO {table} ({', '.join(header['columns'])}) VALUES ({', '.join('?' * len(header['columns']))})"
                count = 0
                for row in rows:
                    count += connection.execute(sql, row).rowcount
                added[table] = count
            connection.execute(
                "INSERT INTO archive_imports VALUES (?, ?, ?, ?)", (manifest.export_id, manifest.kind, iso(utc_now()), _json(added))
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return added


REGISTER_ARCHIVE_KEYS: dict[str, tuple[str, ...]] = {
    "legs": ("scan_id", "bookmaker", "leg_key"),
    "model_forecasts": ("fit_as_of", "fixture_id", "market", "period", "line_key", "selection", "model_name"),
}


def merge_exports(
    archive_root: str | Path,
    *,
    odds_root: str | Path,
    register_archive: str | Path | None = None,
    snapshots: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Merge every downloaded export not merged yet; returns the number of exports merged per kind."""
    root = Path(archive_root)
    say = log or (lambda _text: None)
    register_db = Path(register_archive) if register_archive is not None else root / "scanner" / "register_archive.sqlite"
    merged: dict[str, int] = {}
    for kind in ("odds", "scanner", "forecasts"):
        for directory in final_exports(root, kind):
            if not (directory / MANIFEST).exists():
                continue
            started = time.perf_counter()
            if kind == "odds":
                rows = merge_odds_export(directory, Path(odds_root), snapshots=snapshots)
            else:
                rows = merge_table_export(directory, register_db, REGISTER_ARCHIVE_KEYS)
            if rows:
                merged[kind] = merged.get(kind, 0) + 1
                say(f"merged {directory.name}: {rows} in {time.perf_counter() - started:.1f}s")
    return merged


__all__ = [
    "DEFAULT_PREFIX",
    "ENV_NAMES",
    "REGISTER_ARCHIVE_KEYS",
    "BucketConfig",
    "PullReport",
    "UploadReport",
    "archive_files",
    "download_object",
    "list_remote",
    "make_client",
    "merge_exports",
    "merge_odds_export",
    "merge_table_export",
    "pull_archive",
    "remote_matches",
    "upload_archive",
]
