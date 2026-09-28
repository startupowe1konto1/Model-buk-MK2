"""Prospective, point-in-time archive of API-Football pre-match odds.

API-Football keeps pre-match odds for only about a week, and historical
bookmaker odds with quote timestamps are otherwise unavailable to the
project.  Every day that is not archived is lost for good, so this module
records what the feed shows *now*, with the time we saw it, for later
closing-line and value analysis.

Design:

- One scan pages through ``/odds?date=YYYY-MM-DD`` for a few upcoming dates.
  A single date call returns every league at once, so covering 47 leagues
  costs the same as covering one; fixtures outside the tracked leagues and
  fixtures that have already kicked off are dropped.
- Every scan writes an immutable raw file (the filtered provider objects plus
  ``fetched_at``) and records, per fixture and bookmaker, that the board was
  observed.  Individual prices are stored *change-only*: a quote row is added
  when a selection first appears or its price moves.  Together with the
  observation rows this answers "what price was available at time T" without
  storing an unchanged board every hour.
- ``fetched_at`` is when this installation saw the price; it is the
  conservative ``available_at`` for any point-in-time use.  The provider's own
  ``update`` field is kept alongside for reference but never trusted as the
  availability time.

The daily quota is shared with other jobs, so every scan runs under a
:class:`RequestBudget` with a reserve floor.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from model_buk.ingestion.api_football_history import (
    TOP20_LEAGUES,
    WAVE2_LEAGUES,
    HistoryHttpClient,
    RequestBudget,
)

# Every league already covered by the historical archive; the odds archive
# tracks the same set so the two can be joined on fixture_id.
TRACKED_LEAGUE_IDS: frozenset[int] = frozenset(
    ls.league_id for ls in TOP20_LEAGUES + WAVE2_LEAGUES
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    dates TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    fixtures_seen INTEGER NOT NULL DEFAULT 0,
    quotes_changed INTEGER NOT NULL DEFAULT 0,
    stopped_reason TEXT,
    raw_file TEXT
);
CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    season INTEGER,
    kickoff_utc TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    scan_id TEXT NOT NULL,
    fixture_id INTEGER NOT NULL,
    bookmaker_id INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    api_update TEXT,
    PRIMARY KEY (scan_id, fixture_id, bookmaker_id)
);
CREATE TABLE IF NOT EXISTS quotes (
    fixture_id INTEGER NOT NULL,
    bookmaker_id INTEGER NOT NULL,
    bookmaker TEXT,
    bet_id INTEGER NOT NULL,
    bet TEXT,
    selection TEXT NOT NULL,
    odd REAL NOT NULL,
    api_update TEXT,
    fetched_at TEXT NOT NULL,
    scan_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_key
    ON quotes (fixture_id, bookmaker_id, bet_id, selection, fetched_at);
CREATE TABLE IF NOT EXISTS quote_state (
    fixture_id INTEGER NOT NULL,
    bookmaker_id INTEGER NOT NULL,
    bet_id INTEGER NOT NULL,
    selection TEXT NOT NULL,
    odd REAL NOT NULL,
    PRIMARY KEY (fixture_id, bookmaker_id, bet_id, selection)
);
CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON fixtures (kickoff_utc);
"""

# Columns added after the first release; applied to existing databases.
_MIGRATIONS = (("fixtures", "last_api_update", "TEXT"),)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def scan_dates(now: datetime, days_ahead: int, wide_days: int = 0) -> list[date]:
    """Dates to page through: today..today+days_ahead, optionally extended."""

    today = now.astimezone(timezone.utc).date()
    horizon = days_ahead + max(wide_days, 0)
    return [today + timedelta(days=offset) for offset in range(horizon + 1)]


@dataclass
class OddsScanReport:
    scan_id: str
    requests: int = 0
    fixtures_seen: int = 0
    quotes_changed: int = 0
    stopped_reason: str = "completed"
    dates: list[str] = field(default_factory=list)


class OddsArchive:
    """SQLite + raw-file store for prospective odds snapshots.

    Layout under ``root``::

        raw/{YYYY-MM-DD}/{scan_id}.jsonl.gz   # immutable filtered provider objects
        odds.sqlite3                           # scans, fixtures, observations, change-only quotes
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "odds.sqlite3"
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            for table, column, sql_type in _MIGRATIONS:
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60)

    def changed_since_last_scan(self, objects: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """Objects that are new or whose provider ``update`` differs from the last one stored.

        With every league tracked, most fixtures are unchanged between hourly
        scans; writing only these keeps the raw archive close to change-only
        while every scan still records its observations.
        """

        if not objects:
            return []
        with closing(self._connect()) as conn:
            last = dict(conn.execute("SELECT fixture_id, last_api_update FROM fixtures").fetchall())
        return [
            obj for obj in objects
            if last.get(int(obj["fixture"]["id"]), object()) != obj.get("update")
        ]

    def write_raw(self, scan_id: str, fetched_at: datetime, objects: Sequence[Mapping[str, Any]]) -> str:
        day_dir = self.root / "raw" / fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{scan_id}.jsonl.gz"
        if path.exists():
            raise FileExistsError(f"raw snapshot {path} already exists; scans are immutable")
        lines = "\n".join(
            json.dumps({"fetched_at": _iso(fetched_at), "odds": obj}, ensure_ascii=False)
            for obj in objects
        )
        path.write_bytes(gzip.compress(lines.encode("utf-8")))
        return str(path.relative_to(self.root))

    def record(self, scan_id: str, fetched_at: datetime, objects: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
        """Store observations and changed quotes; return (fixtures, quotes_changed)."""

        fetched = _iso(fetched_at)
        changed = 0
        with closing(self._connect()) as conn:
            for obj in objects:
                fixture = obj.get("fixture") or {}
                league = obj.get("league") or {}
                fixture_id = int(fixture["id"])
                api_update = obj.get("update")
                conn.execute(
                    """INSERT INTO fixtures (fixture_id, league_id, season, kickoff_utc, first_seen_at, last_seen_at,
                                             last_api_update)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(fixture_id) DO UPDATE SET
                         kickoff_utc=excluded.kickoff_utc, last_seen_at=excluded.last_seen_at,
                         last_api_update=excluded.last_api_update""",
                    (fixture_id, league.get("id"), league.get("season"), fixture.get("date"), fetched, fetched,
                     api_update),
                )
                for bookmaker in obj.get("bookmakers") or []:
                    bookmaker_id = int(bookmaker["id"])
                    conn.execute(
                        "INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?, ?)",
                        (scan_id, fixture_id, bookmaker_id, fetched, api_update),
                    )
                    for bet in bookmaker.get("bets") or []:
                        for value in bet.get("values") or []:
                            try:
                                odd = float(value.get("odd"))
                            except (TypeError, ValueError):
                                continue
                            selection = str(value.get("value"))
                            key = (fixture_id, bookmaker_id, int(bet["id"]), selection)
                            row = conn.execute(
                                "SELECT odd FROM quote_state WHERE fixture_id=? AND bookmaker_id=? AND bet_id=? AND selection=?",
                                key,
                            ).fetchone()
                            if row is not None and row[0] == odd:
                                continue
                            conn.execute(
                                "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (*key[:2], bookmaker.get("name"), key[2], bet.get("name"), selection, odd,
                                 api_update, fetched, scan_id),
                            )
                            conn.execute(
                                "INSERT OR REPLACE INTO quote_state VALUES (?, ?, ?, ?, ?)",
                                (*key, odd),
                            )
                            changed += 1
            conn.commit()
        return len(objects), changed

    def start_scan(self, scan_id: str, started_at: datetime, dates: Sequence[str]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO scans (scan_id, started_at, dates) VALUES (?, ?, ?)",
                (scan_id, _iso(started_at), ",".join(dates)),
            )
            conn.commit()

    def finish_scan(self, report: OddsScanReport, finished_at: datetime, raw_file: str | None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE scans SET finished_at=?, requests=?, fixtures_seen=?, quotes_changed=?,
                          stopped_reason=?, raw_file=? WHERE scan_id=?""",
                (_iso(finished_at), report.requests, report.fixtures_seen, report.quotes_changed,
                 report.stopped_reason, raw_file, report.scan_id),
            )
            conn.commit()

    def quotes_as_of(self, fixture_id: int, as_of: datetime) -> list[dict[str, Any]]:
        """Latest known price per (bookmaker, bet, selection) seen at or before ``as_of``.

        Point-in-time safe: only rows with ``fetched_at <= as_of`` are used, so a
        price first seen after the cutoff can never leak into an earlier decision.
        """

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT q.bookmaker_id, q.bookmaker, q.bet_id, q.bet, q.selection, q.odd, q.fetched_at
                   FROM quotes q
                   JOIN (SELECT bookmaker_id, bet_id, selection, MAX(fetched_at) AS ts
                         FROM quotes WHERE fixture_id=? AND fetched_at<=?
                         GROUP BY bookmaker_id, bet_id, selection) last
                     ON q.bookmaker_id=last.bookmaker_id AND q.bet_id=last.bet_id
                    AND q.selection=last.selection AND q.fetched_at=last.ts
                   WHERE q.fixture_id=?
                   ORDER BY q.bookmaker_id, q.bet_id, q.selection""",
                (fixture_id, _iso(as_of), fixture_id),
            ).fetchall()
        keys = ("bookmaker_id", "bookmaker", "bet_id", "bet", "selection", "odd", "fetched_at")
        return [dict(zip(keys, row)) for row in rows]

    def summary(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "scans": one("SELECT COUNT(*) FROM scans"),
                "fixtures": one("SELECT COUNT(*) FROM fixtures"),
                "quotes": one("SELECT COUNT(*) FROM quotes"),
                "observations": one("SELECT COUNT(*) FROM observations"),
                "bookmakers": one("SELECT COUNT(DISTINCT bookmaker_id) FROM observations"),
            }


class OddsCollector:
    """One budget-aware scan of upcoming pre-match odds.

    ``league_ids=None`` keeps every league the feed returns. A date call
    returns all leagues anyway, so this costs no extra requests; only storage
    grows.
    """

    def __init__(
        self,
        client: HistoryHttpClient,
        archive: OddsArchive,
        budget: RequestBudget,
        *,
        league_ids: Iterable[int] | None = TRACKED_LEAGUE_IDS,
    ):
        self.client = client
        self.archive = archive
        self.budget = budget
        self.league_ids = None if league_ids is None else frozenset(league_ids)

    def run(self, now: datetime, dates: Sequence[date]) -> OddsScanReport:
        scan_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        date_strings = [d.isoformat() for d in dates]
        report = OddsScanReport(scan_id=scan_id, dates=date_strings)
        self.archive.start_scan(scan_id, now, date_strings)

        kept: list[Mapping[str, Any]] = []
        for day in date_strings:
            page, total_pages = 1, 1
            while page <= total_pages:
                if not self.budget.allow():
                    report.stopped_reason = "budget_exhausted"
                    break
                payload, headers = self.client.get("/odds", {"date": day, "page": page})
                self.budget.record(headers)
                report.requests += 1
                total_pages = int((payload.get("paging") or {}).get("total") or 1)
                for obj in payload.get("response") or []:
                    league_id = (obj.get("league") or {}).get("id")
                    kickoff = (obj.get("fixture") or {}).get("date")
                    if not kickoff or (self.league_ids is not None and league_id not in self.league_ids):
                        continue
                    # Pre-match only: a price seen after kickoff is not a
                    # pre-match quote and must never enter this archive.
                    if _parse_utc(kickoff) <= now:
                        continue
                    kept.append(obj)
                page += 1
            if report.stopped_reason != "completed":
                break

        # Raw provenance only for fixtures the provider actually refreshed;
        # observations and change-only quotes are still recorded for all.
        fresh = self.archive.changed_since_last_scan(kept)
        raw_file = self.archive.write_raw(scan_id, now, fresh) if fresh else None
        report.fixtures_seen, report.quotes_changed = self.archive.record(scan_id, now, kept)
        self.archive.finish_scan(report, datetime.now(timezone.utc), raw_file)
        return report


__all__ = [
    "TRACKED_LEAGUE_IDS",
    "OddsArchive",
    "OddsCollector",
    "OddsScanReport",
    "scan_dates",
]
