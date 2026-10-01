"""Keep ``odds.sqlite3`` small: archive the full change history of fixtures that kicked off long enough ago.

The odds archive (:mod:`model_buk.ingestion.api_football_odds_archive`) stores every price change of every
bookmaker, bet and selection.  On the server only the recent part is ever read:

- the scanner prices fixtures that have not kicked off (``markets.load_current_quotes``);
- closing line value is computed for paper legs of matches that kicked off at most 7 days ago
  (``ScannerRegister.paper_legs_without_clv``; ``markets.load_closing_quotes`` reads the last quote before kickoff
  of those fixtures);
- the daily bet-type check (``bet_types.measure_bet_types``) rebuilds board snapshots every 12 hours over the last
  7 days, each one from the fixtures that had not kicked off at that moment.

So every fixture with ``kickoff >= now - 7 days`` must keep its full history, and nothing older is read.
:func:`run_retention` therefore takes the fixtures whose kickoff is older than ``keep_days`` (default 7) and, in
batches, inside one write transaction per batch:

1. computes compact snapshots of their prices and upserts them into ``odds_snapshots.sqlite3`` (see
   :data:`SNAPSHOT_POINTS`; about 75 KB per fixture, i.e. ~10 MB a day with every league, so the server keeps
   them ``snapshot_keep_days`` while the PC rebuilds and keeps them forever from the archive);
2. exports their ``quotes`` and ``observations`` rows (plus the ``fixtures`` and ``scans`` rows they refer to, for a
   self-contained archive) to ``<archive>/odds/exports/<YYYY-MM>/<export_id>/`` (:mod:`model_buk.maintenance.exports`)
   and re-reads the files to verify the row counts and checksums;
3. records the export in the ledger table ``archive_exports`` and deletes the exported ``quotes``,
   ``observations`` and ``quote_state`` rows, then commits; only after the commit is the export directory
   finalised (manifest written, ``.partial`` renamed away).  A crash at any point leaves either the rows in the
   database (the partial directory is discarded on the next run) or a committed ledger row (the next run
   finalises the directory from it) -- never both copies missing and never a duplicate export.

``fixtures`` and ``scans`` rows stay in the database (they are tiny and keep the scan log complete).
``quote_state`` (the change detector's last price per key) is only consulted for fixtures that have not kicked
off; if a fixture is rescheduled into the future after its rows were archived, the collector simply records its
board again as new.

Raw scan files (``raw/<YYYY-MM-DD>/``) of days before the cut-off are moved to ``<archive>/odds/raw/`` (same
names) for the off-box copy.  Afterwards the database is vacuumed when enough of it is free pages and the disk has
room for the rebuild (``vacuum="auto"``).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from model_buk.maintenance.exports import ExportWriter, Manifest, export_id_of, finalize_partial, iso, partial_exports, utc_now

KIND = "odds"
DEFAULT_KEEP_DAYS = 7.0
DEFAULT_BATCH_FIXTURES = 100
SNAPSHOT_DB_NAME = "odds_snapshots.sqlite3"

SNAPSHOT_POINTS: tuple[tuple[int, str, int | None], ...] = (
    (0, "open", None),
    (1, "h72", 72 * 60),
    (2, "h48", 48 * 60),
    (3, "h24", 24 * 60),
    (4, "h12", 12 * 60),
    (5, "h6", 6 * 60),
    (6, "h3", 3 * 60),
    (7, "h2", 2 * 60),
    (8, "h1", 60),
    (9, "close", 0),
)
"""``(point, name, minutes before kickoff)`` of the compact snapshots.

``open`` is the first quote ever seen of a (fixture, bookmaker, bet, selection); ``hN`` the price in effect N hours
before kickoff (the last quote seen at or before that instant); ``close`` the last quote seen strictly before
kickoff.  Rows are stored *change-only* along that sequence: a point gets a row only when its price differs from
the previous stored row of the same key, and a point before the key's first quote gets none.  The price at point
``p`` is therefore the row with the largest ``point <= p`` (the opening price is ``point = 0``, the closing price
the row with the largest ``point``).  ``seen_at`` is when that price was first seen (epoch seconds)."""

QUOTE_COLUMNS: tuple[str, ...] = (
    "fixture_id", "bookmaker_id", "bookmaker", "bet_id", "bet", "selection", "odd", "api_update", "fetched_at", "scan_id",
)  # fmt: skip
OBSERVATION_COLUMNS: tuple[str, ...] = ("scan_id", "fixture_id", "bookmaker_id", "fetched_at", "api_update")

_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS archive_exports (
    export_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    fixtures INTEGER NOT NULL,
    quotes INTEGER NOT NULL,
    observations INTEGER NOT NULL,
    quote_state INTEGER NOT NULL,
    directory TEXT NOT NULL,
    manifest_json TEXT NOT NULL
)
"""

_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_points (
    point INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    minutes_before INTEGER
);
CREATE TABLE IF NOT EXISTS selections (
    selection_id INTEGER PRIMARY KEY,
    selection TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS bookmakers (
    bookmaker_id INTEGER PRIMARY KEY,
    bookmaker TEXT
);
CREATE TABLE IF NOT EXISTS bets (
    bet_id INTEGER PRIMARY KEY,
    bet TEXT
);
CREATE TABLE IF NOT EXISTS snapshot_fixtures (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER,
    season INTEGER,
    kickoff_utc TEXT NOT NULL,
    n_keys INTEGER NOT NULL,
    n_quotes INTEGER NOT NULL,
    n_rows INTEGER NOT NULL,
    export_id TEXT
);
CREATE TABLE IF NOT EXISTS quote_snapshots (
    fixture_id INTEGER NOT NULL,
    bookmaker_id INTEGER NOT NULL,
    bet_id INTEGER NOT NULL,
    selection_id INTEGER NOT NULL,
    point INTEGER NOT NULL,
    odd REAL NOT NULL,
    seen_at INTEGER NOT NULL,
    PRIMARY KEY (fixture_id, bookmaker_id, bet_id, selection_id, point)
) WITHOUT ROWID;
CREATE VIEW IF NOT EXISTS opening_quotes AS
    SELECT q.fixture_id, q.bookmaker_id, b.bookmaker, q.bet_id, t.bet, s.selection, q.odd, q.seen_at
    FROM quote_snapshots q
    JOIN selections s USING (selection_id)
    LEFT JOIN bookmakers b USING (bookmaker_id)
    LEFT JOIN bets t USING (bet_id)
    WHERE q.point = 0;
CREATE VIEW IF NOT EXISTS closing_quotes AS
    SELECT q.fixture_id, q.bookmaker_id, b.bookmaker, q.bet_id, t.bet, s.selection, q.odd, q.seen_at
    FROM quote_snapshots q
    JOIN (SELECT fixture_id, bookmaker_id, bet_id, selection_id, MAX(point) AS point FROM quote_snapshots
          GROUP BY fixture_id, bookmaker_id, bet_id, selection_id) last
      USING (fixture_id, bookmaker_id, bet_id, selection_id, point)
    JOIN selections s USING (selection_id)
    LEFT JOIN bookmakers b USING (bookmaker_id)
    LEFT JOIN bets t USING (bet_id);
"""


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def parse_utc(value: str | None) -> datetime | None:
    """Parse the archive's ISO timestamps (``+00:00`` or ``Z``); None when unparsable."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


# --------------------------------------------------------------------------- #
# Compact snapshots
# --------------------------------------------------------------------------- #


def snapshot_rows(quotes: Sequence[tuple[float, datetime]], kickoff: datetime) -> list[tuple[int, float, int]]:
    """``(point, odd, seen_at)`` rows of one key from its quotes ``(odd, fetched_at)`` sorted by time.

    See :data:`SNAPSHOT_POINTS`.  Quotes at or after kickoff are ignored (the archive never stores them, but a
    rescheduled fixture could carry some); a key without a quote before kickoff gives no rows.
    """
    before = [(odd, seen) for odd, seen in quotes if seen < kickoff]
    if not before:
        return []
    rows: list[tuple[int, float, int]] = []
    last_odd: float | None = None
    for point, _name, minutes in SNAPSHOT_POINTS:
        if minutes is None:  # open
            odd, seen = before[0]
        else:
            limit = kickoff - timedelta(minutes=minutes)
            in_effect = None
            for candidate in before:
                if (candidate[1] <= limit) if minutes > 0 else (candidate[1] < limit):
                    in_effect = candidate
                else:
                    break
            if in_effect is None:
                continue
            odd, seen = in_effect
        if last_odd is not None and odd == last_odd:
            continue
        rows.append((point, float(odd), _epoch(seen)))
        last_odd = odd
    return rows


def open_snapshot_db(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=120)
    connection.executescript(_SNAPSHOT_SCHEMA)
    connection.executemany(
        "INSERT OR IGNORE INTO snapshot_points (point, name, minutes_before) VALUES (?, ?, ?)", SNAPSHOT_POINTS
    )
    connection.commit()
    return connection


def _selection_ids(connection: sqlite3.Connection, selections: Iterable[str]) -> dict[str, int]:
    wanted = sorted(set(selections))
    connection.executemany("INSERT OR IGNORE INTO selections (selection) VALUES (?)", [(s,) for s in wanted])
    out: dict[str, int] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start : start + 500]
        marks = ",".join("?" * len(chunk))
        out.update(dict(connection.execute(f"SELECT selection, selection_id FROM selections WHERE selection IN ({marks})", chunk)))
    return out


def write_snapshots(
    snapshot_db: str | Path,
    fixtures: dict[int, tuple[Any, Any, str]],
    quotes: Sequence[tuple[Any, ...]],
    export_id: str,
) -> int:
    """Compute and upsert the snapshots of ``fixtures`` (``{id: (league_id, season, kickoff_utc)}``) from ``quotes``
    (rows in :data:`QUOTE_COLUMNS` order).  Upserts by ``(fixture, bookmaker, bet, selection, point)``, so a re-run
    over the same rows writes the same table (idempotent).  Returns the number of snapshot rows written."""
    grouped: dict[tuple[int, int, int, str], list[tuple[float, datetime]]] = {}
    names_book: dict[int, str] = {}
    names_bet: dict[int, str] = {}
    per_fixture_quotes: dict[int, int] = {}
    for row in quotes:
        fixture_id, bookmaker_id, bookmaker, bet_id, bet, selection, odd, _api, fetched_at, _scan = row
        seen = parse_utc(fetched_at)
        if seen is None:
            continue
        grouped.setdefault((int(fixture_id), int(bookmaker_id), int(bet_id), str(selection)), []).append((float(odd), seen))
        if bookmaker is not None:
            names_book[int(bookmaker_id)] = str(bookmaker)
        if bet is not None:
            names_bet[int(bet_id)] = str(bet)
        per_fixture_quotes[int(fixture_id)] = per_fixture_quotes.get(int(fixture_id), 0) + 1
    with closing(open_snapshot_db(snapshot_db)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            ids = _selection_ids(connection, (key[3] for key in grouped))
            connection.executemany("INSERT OR REPLACE INTO bookmakers VALUES (?, ?)", sorted(names_book.items()))
            connection.executemany("INSERT OR REPLACE INTO bets VALUES (?, ?)", sorted(names_bet.items()))
            rows: list[tuple[int, int, int, int, int, float, int]] = []
            keys_per_fixture: dict[int, int] = {}
            rows_per_fixture: dict[int, int] = {}
            for (fixture_id, bookmaker_id, bet_id, selection), series in grouped.items():
                kickoff = parse_utc(fixtures.get(fixture_id, (None, None, None))[2])
                if kickoff is None:
                    continue
                series.sort(key=lambda item: item[1])
                points = snapshot_rows(series, kickoff)
                if not points:
                    continue
                keys_per_fixture[fixture_id] = keys_per_fixture.get(fixture_id, 0) + 1
                rows_per_fixture[fixture_id] = rows_per_fixture.get(fixture_id, 0) + len(points)
                sid = ids[selection]
                rows.extend((fixture_id, bookmaker_id, bet_id, sid, point, odd, seen) for point, odd, seen in points)
            connection.executemany("INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.executemany(
                "INSERT OR REPLACE INTO snapshot_fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fixture_id,
                        league_id,
                        season,
                        kickoff_utc,
                        keys_per_fixture.get(fixture_id, 0),
                        per_fixture_quotes.get(fixture_id, 0),
                        rows_per_fixture.get(fixture_id, 0),
                        export_id,
                    )
                    for fixture_id, (league_id, season, kickoff_utc) in fixtures.items()
                ],
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return len(rows)


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


@dataclass
class RetentionReport:
    cutoff: str
    exports: list[str] = field(default_factory=list)
    fixtures: int = 0
    quotes: int = 0
    observations: int = 0
    quote_state: int = 0
    snapshot_rows: int = 0
    recovered: int = 0
    discarded: int = 0
    raw_days_moved: list[str] = field(default_factory=list)
    snapshots_pruned: int = 0
    vacuumed: bool = False
    vacuum_note: str = ""
    db_bytes_before: int = 0
    db_bytes_after: int = 0
    dry_run: bool = False

    def summary_line(self) -> str:
        mb = 1024 * 1024
        return (
            f"cutoff={self.cutoff} exports={len(self.exports)} fixtures={self.fixtures} quotes={self.quotes} "
            f"observations={self.observations} quote_state={self.quote_state} snapshots={self.snapshot_rows} "
            f"recovered={self.recovered} discarded={self.discarded} raw_days={len(self.raw_days_moved)} "
            f"snapshots_pruned={self.snapshots_pruned} "
            f"vacuum={'yes' if self.vacuumed else 'no'}{(' (' + self.vacuum_note + ')') if self.vacuum_note else ''} "
            f"db={self.db_bytes_before / mb:.1f}->{self.db_bytes_after / mb:.1f}MiB{' DRY-RUN' if self.dry_run else ''}"
        )


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=300, isolation_level=None)
    connection.execute(_LEDGER_SQL)
    return connection


def _ledger(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {row[0]: (row[1], row[2]) for row in connection.execute("SELECT export_id, directory, manifest_json FROM archive_exports")}


def recover_partials(connection: sqlite3.Connection, archive_root: Path) -> tuple[int, int]:
    """Finalise left-over partial exports the ledger committed; discard the others.  ``(recovered, discarded)``."""
    ledger = _ledger(connection)
    recovered = discarded = 0
    for partial in partial_exports(archive_root, KIND):
        export_id = export_id_of(partial)
        if export_id in ledger:
            finalize_partial(partial, ledger[export_id][1])
            recovered += 1
        else:
            shutil.rmtree(partial, ignore_errors=True)
            discarded += 1
    return recovered, discarded


def old_fixtures(connection: sqlite3.Connection, cutoff: datetime) -> dict[int, tuple[Any, Any, str]]:
    """Fixtures that kicked off before ``cutoff`` and still have rows in ``quotes``, ``observations`` or
    ``quote_state``: ``{fixture_id: (league_id, season, kickoff_utc)}``."""
    with_rows: set[int] = set()
    for table in ("quotes", "observations", "quote_state"):
        with_rows.update(row[0] for row in connection.execute(f"SELECT DISTINCT fixture_id FROM {table}"))
    out: dict[int, tuple[Any, Any, str]] = {}
    for fixture_id, league_id, season, kickoff_utc in connection.execute(
        "SELECT fixture_id, league_id, season, kickoff_utc FROM fixtures"
    ):
        if fixture_id not in with_rows:
            continue
        kickoff = parse_utc(kickoff_utc)
        if kickoff is not None and kickoff < cutoff:
            out[int(fixture_id)] = (league_id, season, kickoff_utc)
    return out


def _in_clause(ids: Sequence[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def _export_batch(
    connection: sqlite3.Connection,
    batch: dict[int, tuple[Any, Any, str]],
    *,
    archive_root: Path,
    snapshot_db: Path,
    cutoff: datetime,
    now: datetime,
    export_id: str,
    report: RetentionReport,
    dry_run: bool,
) -> None:
    ids = sorted(batch)
    inside = _in_clause(ids)
    connection.execute("BEGIN IMMEDIATE")
    writer: ExportWriter | None = None
    committed = False
    try:
        fixture_cols = [row[1] for row in connection.execute("PRAGMA table_info(fixtures)")]
        scan_cols = [row[1] for row in connection.execute("PRAGMA table_info(scans)")]
        fixtures_rows = connection.execute(f"SELECT {', '.join(fixture_cols)} FROM fixtures WHERE fixture_id IN ({inside}) ORDER BY fixture_id").fetchall()
        quotes = connection.execute(
            f"SELECT {', '.join(QUOTE_COLUMNS)} FROM quotes WHERE fixture_id IN ({inside}) "
            "ORDER BY fixture_id, bookmaker_id, bet_id, selection, fetched_at, rowid"
        ).fetchall()
        observations = connection.execute(
            f"SELECT {', '.join(OBSERVATION_COLUMNS)} FROM observations WHERE fixture_id IN ({inside}) ORDER BY fetched_at, scan_id, fixture_id, bookmaker_id"
        ).fetchall()
        n_state = connection.execute(f"SELECT COUNT(*) FROM quote_state WHERE fixture_id IN ({inside})").fetchone()[0]
        scan_ids = sorted({row[9] for row in quotes} | {row[0] for row in observations})
        scans_rows: list[tuple] = []
        for start in range(0, len(scan_ids), 500):
            chunk = scan_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            scans_rows.extend(connection.execute(f"SELECT {', '.join(scan_cols)} FROM scans WHERE scan_id IN ({marks})", chunk).fetchall())
        scans_rows.sort(key=lambda row: str(row[0]))

        if dry_run:
            connection.execute("ROLLBACK")
            report.fixtures += len(ids)
            report.quotes += len(quotes)
            report.observations += len(observations)
            report.quote_state += int(n_state)
            return

        report.snapshot_rows += write_snapshots(snapshot_db, batch, quotes, export_id)

        writer = ExportWriter(archive_root, KIND, export_id, now)
        writer.write_table("fixtures", fixture_cols, fixtures_rows)
        writer.write_table("scans", scan_cols, scans_rows)
        writer.write_table("quotes", QUOTE_COLUMNS, quotes)
        writer.write_table("observations", OBSERVATION_COLUMNS, observations)
        writer.verify({"fixtures": len(fixtures_rows), "scans": len(scans_rows), "quotes": len(quotes), "observations": len(observations)})
        meta = {
            "cutoff": iso(cutoff),
            "fixture_ids": ids,
            "kickoff_min": min(batch[i][2] for i in ids),
            "kickoff_max": max(batch[i][2] for i in ids),
            "quote_state_rows_deleted": int(n_state),
            "snapshot_db": snapshot_db.name,
            "source": "api-football-odds/odds.sqlite3",
        }
        manifest_text = writer.manifest_json(meta)
        connection.execute(
            "INSERT INTO archive_exports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                export_id,
                iso(now),
                iso(cutoff),
                len(ids),
                len(quotes),
                len(observations),
                int(n_state),
                str(writer.final_dir.relative_to(archive_root).as_posix()),
                manifest_text,
            ),
        )
        deleted_q = connection.execute(f"DELETE FROM quotes WHERE fixture_id IN ({inside})").rowcount
        deleted_o = connection.execute(f"DELETE FROM observations WHERE fixture_id IN ({inside})").rowcount
        deleted_s = connection.execute(f"DELETE FROM quote_state WHERE fixture_id IN ({inside})").rowcount
        if deleted_q != len(quotes) or deleted_o != len(observations) or deleted_s != n_state:
            raise RuntimeError(
                f"{export_id}: deleted {deleted_q}/{deleted_o}/{deleted_s} rows, exported {len(quotes)}/{len(observations)}/{n_state}"
            )
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
    report.exports.append(export_id)
    report.fixtures += len(ids)
    report.quotes += len(quotes)
    report.observations += len(observations)
    report.quote_state += int(n_state)


def move_raw_days(odds_root: Path, archive_root: Path, cutoff: datetime, *, dry_run: bool = False) -> list[str]:
    """Move ``raw/<YYYY-MM-DD>/`` directories of days before the cut-off day to ``<archive>/odds/raw/``."""
    raw = odds_root / "raw"
    if not raw.exists():
        return []
    limit = cutoff.date()
    moved: list[str] = []
    for day_dir in sorted(p for p in raw.iterdir() if p.is_dir()):
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day >= limit:
            continue
        moved.append(day_dir.name)
        if dry_run:
            continue
        target = archive_root / KIND / "raw" / day_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(day_dir.iterdir()):
            destination = target / item.name
            if destination.exists():
                if destination.stat().st_size == item.stat().st_size:
                    item.unlink()
                    continue
                destination = target / (item.stem + ".dup" + item.suffix)
            temporary = destination.with_name(destination.name + ".tmp")
            shutil.copy2(item, temporary)
            os.replace(temporary, destination)
            item.unlink()
        try:
            day_dir.rmdir()
        except OSError:
            pass
    return moved


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def maybe_vacuum(db_path: Path, mode: str = "auto", *, min_free_share: float = 0.2) -> tuple[bool, str]:
    """VACUUM when ``mode == "always"``, or ``"auto"`` and at least ``min_free_share`` of the pages are free.

    Needs about the database size of free space (the temp copy may live on another file system, the rewrite of
    the original needs a journal); skipped with a note when the disk is too full.
    """
    if mode == "never":
        return False, "disabled"
    with closing(sqlite3.connect(db_path, timeout=300)) as connection:
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        share = free_pages / page_count if page_count else 0.0
        if mode == "auto" and share < min_free_share:
            return False, f"free pages {share:.0%} < {min_free_share:.0%}"
        size = db_path.stat().st_size
        if _free_bytes(db_path.parent) < 1.1 * size:
            return False, f"not enough disk for VACUUM ({_free_bytes(db_path.parent) >> 20} MiB free, db {size >> 20} MiB)"
        connection.execute("VACUUM")
    return True, f"free pages were {share:.0%}"


def run_retention(
    odds_root: str | Path,
    archive_root: str | Path,
    *,
    keep_days: float = DEFAULT_KEEP_DAYS,
    now: datetime | None = None,
    batch_fixtures: int = DEFAULT_BATCH_FIXTURES,
    snapshot_db: str | Path | None = None,
    move_raw: bool = True,
    vacuum: str = "auto",
    snapshot_keep_days: float | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> RetentionReport:
    """Archive and delete the change history of fixtures that kicked off more than ``keep_days`` ago.

    ``keep_days`` below 7 raises: the scanner's CLV and the daily bet-type check read 7 days back (see the module
    docstring).  ``dry_run`` only counts what would be archived (nothing is written, moved or deleted).
    ``snapshot_keep_days`` (default: keep forever) prunes the compact snapshots of older fixtures afterwards
    (:func:`prune_snapshots`); the server uses 30 days, the PC keeps everything.
    """
    if keep_days < DEFAULT_KEEP_DAYS:
        raise ValueError(f"keep_days must be >= {DEFAULT_KEEP_DAYS:g}: the scanner reads 7 days of full change history")
    if batch_fixtures < 1:
        raise ValueError("batch_fixtures must be positive")
    odds_root = Path(odds_root)
    archive_root = Path(archive_root)
    db_path = odds_root / "odds.sqlite3"
    if not db_path.exists():
        raise FileNotFoundError(f"odds archive not found: {db_path}")
    snapshot_path = Path(snapshot_db) if snapshot_db is not None else odds_root / SNAPSHOT_DB_NAME
    stamp = (now or utc_now()).astimezone(timezone.utc)
    cutoff = stamp - timedelta(days=float(keep_days))
    report = RetentionReport(cutoff=iso(cutoff), dry_run=dry_run, db_bytes_before=db_path.stat().st_size)
    say = log or (lambda _text: None)

    with closing(_connect(db_path)) as connection:
        if not dry_run:
            report.recovered, report.discarded = recover_partials(connection, archive_root)
        candidates = old_fixtures(connection, cutoff)
        ids = sorted(candidates)
        say(f"{len(ids)} fixtures kicked off before {iso(cutoff)} still have rows")
        for number, start in enumerate(range(0, len(ids), batch_fixtures), start=1):
            batch = {i: candidates[i] for i in ids[start : start + batch_fixtures]}
            export_id = f"odds-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{number:03d}"
            _export_batch(
                connection,
                batch,
                archive_root=archive_root,
                snapshot_db=snapshot_path,
                cutoff=cutoff,
                now=stamp,
                export_id=export_id,
                report=report,
                dry_run=dry_run,
            )
            say(f"{export_id}: {len(batch)} fixtures")
    if move_raw:
        report.raw_days_moved = move_raw_days(odds_root, archive_root, cutoff, dry_run=dry_run)
    if not dry_run and (report.exports or vacuum == "always"):
        report.vacuumed, report.vacuum_note = maybe_vacuum(db_path, vacuum)
    elif vacuum == "never":
        report.vacuum_note = "disabled"
    if snapshot_keep_days is not None and not dry_run and snapshot_path.exists():
        report.snapshots_pruned = prune_snapshots(snapshot_path, stamp - timedelta(days=float(snapshot_keep_days)))
    report.db_bytes_after = db_path.stat().st_size
    return report


def prune_snapshots(snapshot_db: str | Path, before: datetime) -> int:
    """Delete the snapshots of fixtures that kicked off before ``before``; returns the fixtures pruned.

    Used on the server, where the snapshot table would otherwise grow by about 10 MB a day; the full history is
    in the archive (bucket and PC) and the PC rebuilds its own snapshot table from it (``pull_archive.py``).
    Freed pages are reused by the next inserts, so the file stops growing instead of shrinking.
    """
    with closing(open_snapshot_db(snapshot_db)) as connection:
        old = [
            int(fixture_id)
            for fixture_id, kickoff_utc in connection.execute("SELECT fixture_id, kickoff_utc FROM snapshot_fixtures")
            if (parse_utc(kickoff_utc) or before) < before
        ]
        connection.execute("BEGIN IMMEDIATE")
        for start in range(0, len(old), 500):
            inside = _in_clause(old[start : start + 500])
            connection.execute(f"DELETE FROM quote_snapshots WHERE fixture_id IN ({inside})")
            connection.execute(f"DELETE FROM snapshot_fixtures WHERE fixture_id IN ({inside})")
        connection.execute("COMMIT")
    return len(old)


def ledger_rows(odds_root: str | Path) -> list[dict[str, Any]]:
    """The export ledger of an odds archive (for reports and tests)."""
    db_path = Path(odds_root) / "odds.sqlite3"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as connection:
        try:
            cursor = connection.execute("SELECT * FROM archive_exports ORDER BY export_id")
        except sqlite3.OperationalError:
            return []
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def manifest_of(directory: str | Path) -> Manifest:
    return Manifest.read(directory)


__all__ = [
    "DEFAULT_BATCH_FIXTURES",
    "DEFAULT_KEEP_DAYS",
    "KIND",
    "OBSERVATION_COLUMNS",
    "QUOTE_COLUMNS",
    "SNAPSHOT_DB_NAME",
    "SNAPSHOT_POINTS",
    "RetentionReport",
    "ledger_rows",
    "manifest_of",
    "maybe_vacuum",
    "move_raw_days",
    "old_fixtures",
    "open_snapshot_db",
    "parse_utc",
    "prune_snapshots",
    "recover_partials",
    "run_retention",
    "snapshot_rows",
    "write_snapshots",
]
