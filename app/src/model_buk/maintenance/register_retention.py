"""Keep the scanner register (``scanner.sqlite``) small without losing anything the scanner reads.

What grows in the register is the near-miss universe: every leg priced above fair at some bookmaker is logged
(change-only, with a heartbeat), and almost all of those rows are ``near_miss`` / ``suspect`` / ``model_only``
legs that never became a paper bet.  What the scanner reads (``model_buk.scanner.register``):

- paper bets: the first ``is_value = 1`` observation of a leg (``paper_legs``), with ``value_t###`` per regime;
- coupons and their legs (``coupons``, ``coupon_legs``), settlements, CLV, bet-type checks, scans and configs;
- ``unsettled_legs``: every distinct leg of ``legs`` and ``coupon_legs`` without a settlement (to settle it);
- ``_changed_legs``: the latest row of a leg still on offer (only fixtures that have not kicked off).

:func:`run_register_retention` archives and deletes ``legs`` rows that are *all* of:

- of a fixture that kicked off more than ``keep_days`` ago (and observed before that);
- not value in any sense: ``is_value``, ``is_model_only_value`` and every ``value_t###`` are 0 or NULL and
  ``leg_class`` is not ``value``;
- not a coupon leg (no ``coupon_legs`` row with the same ``leg_key``) and without a CLV row;
- settled already (a ``settlements`` row for the ``leg_key``), or kicked off more than ``unsettled_grace_days``
  ago (a result that has not arrived by then will not; such legs stay unsettled in the archive).

Nothing in ``scans``, ``configs``, ``coupons``, ``coupon_legs``, ``settlements``, ``clv`` or ``bet_type_checks`` is
touched.  The register's tables are append-only by trigger; the delete runs in one transaction that drops the
``legs`` delete trigger, deletes exactly the exported rows and re-creates the trigger with its stored SQL, so
the guard is never absent outside that transaction.  Exports use :mod:`model_buk.maintenance.exports` (kind
``scanner``, ledger table ``archive_exports`` inside the register), with the same crash rules as the odds
retention.

The same machinery optionally archives the model forecast log (``model_forecasts.sqlite``, append-only too) for
fixtures older than ``forecasts_keep_days`` (kind ``forecasts``), and :func:`weekly_backup` writes a gzip copy of
the whole register once per ISO week to ``<archive>/scanner/backups/`` (the volume itself is not backed up).
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from model_buk.maintenance.exports import ExportWriter, export_id_of, finalize_partial, iso, partial_exports, utc_now
from model_buk.maintenance.odds_retention import maybe_vacuum

KIND_REGISTER = "scanner"
KIND_FORECASTS = "forecasts"
DEFAULT_KEEP_DAYS = 7.0
DEFAULT_UNSETTLED_GRACE_DAYS = 30.0
DEFAULT_BATCH_ROWS = 50_000

_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS archive_exports (
    export_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    source_table TEXT NOT NULL,
    rows INTEGER NOT NULL,
    directory TEXT NOT NULL,
    manifest_json TEXT NOT NULL
)
"""


@dataclass
class RegisterRetentionReport:
    cutoff: str
    exports: list[str] = field(default_factory=list)
    legs_archived: int = 0
    legs_kept_old: int = 0
    forecasts_archived: int = 0
    recovered: int = 0
    discarded: int = 0
    backup: str | None = None
    vacuumed: bool = False
    vacuum_note: str = ""
    db_bytes_before: int = 0
    db_bytes_after: int = 0
    dry_run: bool = False

    def summary_line(self) -> str:
        mb = 1024 * 1024
        return (
            f"cutoff={self.cutoff} exports={len(self.exports)} legs_archived={self.legs_archived} "
            f"old_legs_kept={self.legs_kept_old} forecasts_archived={self.forecasts_archived} "
            f"recovered={self.recovered} discarded={self.discarded} backup={self.backup or '-'} "
            f"vacuum={'yes' if self.vacuumed else 'no'}{(' (' + self.vacuum_note + ')') if self.vacuum_note else ''} "
            f"db={self.db_bytes_before / mb:.1f}->{self.db_bytes_after / mb:.1f}MiB{' DRY-RUN' if self.dry_run else ''}"
        )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=300, isolation_level=None)
    connection.execute(_LEDGER_SQL)
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def _recover(connection: sqlite3.Connection, archive_root: Path, kind: str) -> tuple[int, int]:
    ledger = {row[0]: row[1] for row in connection.execute("SELECT export_id, manifest_json FROM archive_exports")}
    recovered = discarded = 0
    for partial in partial_exports(archive_root, kind):
        export_id = export_id_of(partial)
        if export_id in ledger:
            finalize_partial(partial, ledger[export_id])
            recovered += 1
        else:
            shutil.rmtree(partial, ignore_errors=True)
            discarded += 1
    return recovered, discarded


def _delete_guarded(connection: sqlite3.Connection, table: str, rowids: Sequence[int]) -> int:
    """Delete ``rowids`` of an append-only table inside the caller's transaction: drop its delete trigger, delete,
    re-create the trigger from its stored SQL.  Returns the rows deleted."""
    trigger = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? AND sql LIKE '%BEFORE DELETE%'",
        (table,),
    ).fetchall()
    for name, _sql in trigger:
        connection.execute(f'DROP TRIGGER "{name}"')
    deleted = 0
    for start in range(0, len(rowids), 900):
        chunk = list(rowids[start : start + 900])
        marks = ",".join("?" * len(chunk))
        deleted += connection.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", chunk).rowcount
    for _name, sql in trigger:
        connection.execute(sql)
    return deleted


def archive_rows(
    db_path: Path,
    *,
    table: str,
    where_sql: str,
    params: Mapping[str, Any] | Sequence[Any],
    kind: str,
    archive_root: Path,
    export_id: str,
    now: datetime,
    cutoff: datetime,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    prepare_sql: Sequence[str] = (),
    meta: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> tuple[list[str], int]:
    """Export and delete the rows of ``table`` matching ``where_sql`` (alias ``t``), in batches of ``batch_rows``.

    Every batch is one transaction: select (``rowid`` order), write + verify the export, insert the ledger row,
    delete exactly those rowids (append-only triggers handled by :func:`_delete_guarded`), commit, finalise the
    export directory.  ``prepare_sql`` runs first inside each transaction (temp tables).  Returns ``(export ids,
    rows archived)``; with ``dry_run`` only the count.
    """
    exports: list[str] = []
    total = 0
    bound = dict(params) if isinstance(params, Mapping) else list(params)
    with closing(_connect(db_path)) as connection:
        columns = _columns(connection, table)
        number = 0
        while True:
            number += 1
            connection.execute("BEGIN IMMEDIATE")
            writer: ExportWriter | None = None
            committed = False
            try:
                for statement in prepare_sql:
                    connection.execute(statement)
                rows = connection.execute(
                    f"SELECT t.rowid, {', '.join('t.' + c for c in columns)} FROM {table} t WHERE {where_sql} "
                    f"ORDER BY t.rowid LIMIT {int(batch_rows)}",
                    bound,
                ).fetchall()
                if not rows or dry_run:
                    if dry_run:
                        total = connection.execute(f"SELECT COUNT(*) FROM {table} t WHERE {where_sql}", bound).fetchone()[0]
                    connection.execute("ROLLBACK")
                    break
                batch_id = f"{export_id}-{number:03d}"
                rowids = [row[0] for row in rows]
                writer = ExportWriter(archive_root, kind, batch_id, now)
                writer.write_table(table, columns, (row[1:] for row in rows))
                writer.verify({table: len(rows)})
                manifest = writer.manifest_json({"cutoff": iso(cutoff), "source_table": table, "source": db_path.name, **(meta or {})})
                connection.execute(
                    "INSERT INTO archive_exports VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, iso(now), iso(cutoff), table, len(rows), writer.final_dir.relative_to(archive_root).as_posix(), manifest),
                )
                deleted = _delete_guarded(connection, table, rowids)
                if deleted != len(rows):
                    raise RuntimeError(f"{batch_id}: deleted {deleted} rows of {table}, exported {len(rows)}")
                connection.execute("COMMIT")
                committed = True
            except BaseException:
                if not committed:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    if writer is not None:
                        writer.discard()
                raise
            writer.finalize()
            exports.append(batch_id)
            total += len(rows)
            if len(rows) < batch_rows:
                break
    return exports, total


def _value_columns(columns: Sequence[str]) -> list[str]:
    return [c for c in columns if c.startswith("value_t")]


def legs_where(columns: Sequence[str]) -> str:
    """The SQL condition (alias ``t``) of the legs that may leave the register (see the module docstring).

    Parameters: ``:cutoff`` (ISO ``...Z``) and ``:grace`` (ISO, the unsettled grace cut-off).  Needs the temp
    tables ``keep_coupon_keys`` and ``keep_clv`` (see :data:`LEGS_PREPARE_SQL`).
    """
    value_flags = " AND ".join(f"COALESCE(t.{c}, 0) = 0" for c in ("is_value", "is_model_only_value", *_value_columns(columns)) if c in columns)
    parts = [
        "t.kickoff_utc IS NOT NULL",
        "t.kickoff_utc < :cutoff",
        "t.as_of < :cutoff",
        value_flags or "1",
        "COALESCE(t.leg_class, '') <> 'value'",
        "NOT EXISTS (SELECT 1 FROM temp.keep_coupon_keys k WHERE k.leg_key = t.leg_key)",
        "NOT EXISTS (SELECT 1 FROM temp.keep_clv c WHERE c.scan_id = t.scan_id AND c.bookmaker = t.bookmaker AND c.leg_key = t.leg_key)",
        "(EXISTS (SELECT 1 FROM settlements s WHERE s.leg_key = t.leg_key) OR t.kickoff_utc < :grace)",
    ]
    return " AND ".join(f"({p})" for p in parts)


LEGS_PREPARE_SQL: tuple[str, ...] = (
    "DROP TABLE IF EXISTS temp.keep_coupon_keys",
    "CREATE TEMP TABLE keep_coupon_keys (leg_key TEXT PRIMARY KEY) WITHOUT ROWID",
    "INSERT OR IGNORE INTO temp.keep_coupon_keys SELECT leg_key FROM coupon_legs WHERE leg_key IS NOT NULL",
    "DROP TABLE IF EXISTS temp.keep_clv",
    "CREATE TEMP TABLE keep_clv (scan_id TEXT, bookmaker TEXT, leg_key TEXT, PRIMARY KEY (scan_id, bookmaker, leg_key)) WITHOUT ROWID",
    "INSERT OR IGNORE INTO temp.keep_clv SELECT scan_id, bookmaker, leg_key FROM clv",
)


def weekly_backup(register_path: str | Path, archive_root: str | Path, now: datetime | None = None) -> Path | None:
    """A gzip copy of the register once per ISO week (``scanner-<YYYY>W<WW>.sqlite.gz``); None if it exists.

    Uses SQLite's online backup (consistent while the scanner may write), into a temp file next to the target.
    """
    stamp = (now or utc_now()).astimezone(timezone.utc)
    year, week, _ = stamp.isocalendar()
    target_dir = Path(archive_root) / KIND_REGISTER / "backups"
    target = target_dir / f"scanner-{year}W{week:02d}.sqlite.gz"
    if target.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(prefix=".backup-", suffix=".sqlite", dir=target_dir)
    os.close(fd)
    raw = Path(raw_name)
    partial = target.with_name(target.name + ".tmp")
    try:
        with closing(sqlite3.connect(f"file:{Path(register_path).as_posix()}?mode=ro", uri=True, timeout=120)) as source:
            with closing(sqlite3.connect(raw)) as destination:
                source.backup(destination)
        with open(raw, "rb") as src, open(partial, "wb") as out:
            with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as zipped:
                shutil.copyfileobj(src, zipped, 1 << 20)
            out.flush()
            os.fsync(out.fileno())
        os.replace(partial, target)
    finally:
        raw.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return target


def run_register_retention(
    register_path: str | Path,
    archive_root: str | Path,
    *,
    keep_days: float = DEFAULT_KEEP_DAYS,
    unsettled_grace_days: float = DEFAULT_UNSETTLED_GRACE_DAYS,
    forecast_log: str | Path | None = None,
    forecasts_keep_days: float | None = None,
    now: datetime | None = None,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    backup: bool = True,
    vacuum: str = "auto",
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> RegisterRetentionReport:
    """Archive old non-value legs of the register (and optionally old forecast-log rows); weekly backup."""
    if keep_days < DEFAULT_KEEP_DAYS:
        raise ValueError(f"keep_days must be >= {DEFAULT_KEEP_DAYS:g}: CLV is retried for 7 days after kickoff")
    if unsettled_grace_days < keep_days:
        raise ValueError("unsettled_grace_days must be >= keep_days")
    register_path = Path(register_path)
    archive_root = Path(archive_root)
    if not register_path.exists():
        raise FileNotFoundError(f"scanner register not found: {register_path}")
    say = log or (lambda _text: None)
    stamp = (now or utc_now()).astimezone(timezone.utc)
    cutoff = stamp - timedelta(days=float(keep_days))
    grace = stamp - timedelta(days=float(unsettled_grace_days))
    report = RegisterRetentionReport(cutoff=iso(cutoff), dry_run=dry_run, db_bytes_before=register_path.stat().st_size)

    with closing(_connect(register_path)) as connection:
        if not dry_run:
            report.recovered, report.discarded = _recover(connection, archive_root, KIND_REGISTER)
        columns = _columns(connection, "legs")
    if not columns:
        raise ValueError(f"{register_path} has no legs table (not a scanner register?)")
    exports, archived = archive_rows(
        register_path,
        table="legs",
        where_sql=legs_where(columns),
        params={"cutoff": iso(cutoff), "grace": iso(grace)},
        kind=KIND_REGISTER,
        archive_root=archive_root,
        export_id=f"scanner-legs-{stamp.strftime('%Y%m%dT%H%M%SZ')}",
        now=stamp,
        cutoff=cutoff,
        batch_rows=batch_rows,
        prepare_sql=LEGS_PREPARE_SQL,
        meta={"rule": "non-value, non-coupon, no CLV, settled or past grace", "grace": iso(grace)},
        dry_run=dry_run,
    )
    report.exports.extend(exports)
    report.legs_archived = archived
    with closing(sqlite3.connect(f"file:{register_path.as_posix()}?mode=ro", uri=True)) as connection:
        report.legs_kept_old = connection.execute(
            "SELECT COUNT(*) FROM legs WHERE kickoff_utc < ?", (iso(cutoff),)
        ).fetchone()[0] - (archived if dry_run else 0)
    say(f"legs: {archived} archived in {len(exports)} export(s); {report.legs_kept_old} older legs kept (value / coupon / CLV / unsettled)")

    if forecast_log is not None and forecasts_keep_days is not None and Path(forecast_log).exists():
        if forecasts_keep_days < keep_days:
            raise ValueError("forecasts_keep_days must be >= keep_days")
        forecast_path = Path(forecast_log)
        forecast_cutoff = stamp - timedelta(days=float(forecasts_keep_days))
        with closing(_connect(forecast_path)) as connection:
            if not dry_run:
                recovered, discarded = _recover(connection, archive_root, KIND_FORECASTS)
                report.recovered += recovered
                report.discarded += discarded
        f_exports, f_rows = archive_rows(
            forecast_path,
            table="model_forecasts",
            where_sql="t.kickoff_utc IS NOT NULL AND t.kickoff_utc < ?",
            params=[iso(forecast_cutoff)],
            kind=KIND_FORECASTS,
            archive_root=archive_root,
            export_id=f"forecasts-{stamp.strftime('%Y%m%dT%H%M%SZ')}",
            now=stamp,
            cutoff=forecast_cutoff,
            batch_rows=batch_rows,
            dry_run=dry_run,
        )
        report.exports.extend(f_exports)
        report.forecasts_archived = f_rows
        say(f"forecast log: {f_rows} rows archived")

    if not dry_run:
        if backup:
            written = weekly_backup(register_path, archive_root, stamp)
            report.backup = None if written is None else written.name
        if archived or vacuum == "always":
            report.vacuumed, report.vacuum_note = maybe_vacuum(register_path, vacuum)
    report.db_bytes_after = register_path.stat().st_size
    return report


__all__ = [
    "DEFAULT_KEEP_DAYS",
    "DEFAULT_UNSETTLED_GRACE_DAYS",
    "KIND_FORECASTS",
    "KIND_REGISTER",
    "LEGS_PREPARE_SQL",
    "RegisterRetentionReport",
    "archive_rows",
    "legs_where",
    "run_register_retention",
    "weekly_backup",
]
