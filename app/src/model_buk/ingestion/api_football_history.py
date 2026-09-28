"""Bulk historical archive builder for API-Football.

This is deliberately separate from :mod:`model_buk.live_provider`, which is
tuned for cost-aware *live* scanning (short TTL caching, one fixture at a
time).  Building a multi-season historical archive has different needs:
requests must survive process restarts and even multi-day pauses, raw
payloads are kept forever (not cached with a TTL), and the daily API quota
must be tracked and respected across runs.

Design:

- :class:`RequestBudget` stops issuing requests once either an explicit
  per-run cap or a live quota reserve would be crossed.
- :class:`HistoryStore` persists raw provider payloads as gzip-compressed
  JSON on disk and tracks per-fixture progress in a small SQLite database,
  so an interrupted or resumed run never re-fetches already-downloaded
  matches and never loses track of what is still pending.
- :class:`HistoryDownloader` orchestrates the two API calls needed per
  league-season: one to list fixture ids, then one ``/fixtures?ids=...``
  call per batch of up to 20 fixtures (the documented maximum), which
  returns full match detail (events, lineups, statistics, player stats) in
  a single request.

Nothing here talks to the live paper/shadow pipeline or the app's
production ``LEAGUES`` catalog; it only writes raw archives to disk for a
later, separate table-building step.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_BATCH_SIZE = 20


@dataclass(frozen=True)
class LeagueSeason:
    league_id: int
    code: str
    name: str
    country: str
    season: int


# Top-20 European leagues by combined competitiveness and confirmed
# fixture-statistics coverage in the API-Football catalog (checked live on
# 2026-09-28; see app/runtime/api_catalog/). This is the project's existing
# 16-league set (model_buk.catalog.LEAGUES) plus four additional
# well-covered top flights. It is intentionally independent of
# model_buk.catalog.LEAGUES: that catalog drives the live app, this one
# drives a one-off bulk historical archive and can be edited freely without
# touching production behaviour.
#
# season_from reflects the *empirically verified* first season with
# populated fixture statistics, not just the catalog's coverage flag: a
# live spot-check found several leagues advertise one season earlier than
# their statistics are actually filled in (e.g. Premier League's catalog
# entry says 2014, but 2014/15 fixture stats are empty; 2015/16 is the
# first season with real data).
TOP20_LEAGUES: tuple[LeagueSeason, ...] = tuple(
    LeagueSeason(league_id, code, name, country, season)
    for league_id, code, name, country, season_from, season_to in (
        (39, "EPL", "Premier League", "England", 2015, 2025),
        (40, "CHAMPIONSHIP", "Championship", "England", 2015, 2025),
        (140, "LALIGA", "LaLiga", "Spain", 2015, 2025),
        (141, "LALIGA2", "LaLiga 2", "Spain", 2016, 2025),
        (78, "BUNDESLIGA", "Bundesliga", "Germany", 2015, 2025),
        (79, "BUNDESLIGA2", "2. Bundesliga", "Germany", 2015, 2025),
        (135, "SERIEA", "Serie A", "Italy", 2015, 2025),
        (136, "SERIEB", "Serie B", "Italy", 2016, 2025),
        (61, "LIGUE1", "Ligue 1", "France", 2015, 2025),
        (62, "LIGUE2", "Ligue 2", "France", 2015, 2025),
        (94, "PRIMEIRA", "Primeira Liga", "Portugal", 2015, 2025),
        (88, "EREDIVISIE", "Eredivisie", "Netherlands", 2015, 2025),
        (144, "BELGIUM", "Belgian Pro League", "Belgium", 2015, 2025),
        (203, "SUPERLIG", "Süper Lig", "Turkey", 2015, 2025),
        (179, "SCOTLAND", "Scottish Premiership", "Scotland", 2015, 2025),
        (106, "EKSTRAKLASA", "Ekstraklasa", "Poland", 2018, 2025),
        (218, "AUSTRIA", "Austrian Bundesliga", "Austria", 2015, 2025),
        (207, "SWITZERLAND", "Swiss Super League", "Switzerland", 2016, 2025),
        (197, "GREECE", "Greek Super League 1", "Greece", 2016, 2025),
        (119, "DENMARK", "Danish Superliga", "Denmark", 2015, 2025),
    )
    for season in range(season_from, season_to + 1)
)


def top20_league_seasons() -> tuple[LeagueSeason, ...]:
    """Return the planned (league, season) scope, oldest season first per league."""

    return TOP20_LEAGUES


class HistoryHttpClient(Protocol):
    def get(
        self, endpoint: str, params: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], Mapping[str, str]]: ...


@dataclass
class RequestBudget:
    """Stops issuing requests before a per-run cap or a live quota reserve.

    ``reserve`` keeps a floor of untouched daily requests for other jobs
    (e.g. same-day odds collection). ``remaining_quota`` starts unknown and
    is only trusted once at least one live response has reported it via
    :meth:`record`; until then only ``max_requests`` applies.
    """

    max_requests: int
    reserve: int = 0
    spent: int = 0
    remaining_quota: int | None = None

    def allow(self) -> bool:
        if self.spent >= self.max_requests:
            return False
        if self.remaining_quota is not None and self.remaining_quota <= self.reserve:
            return False
        return True

    def record(self, headers: Mapping[str, str]) -> None:
        self.spent += 1
        raw = headers.get("x-ratelimit-requests-remaining")
        if raw is None:
            return
        try:
            self.remaining_quota = int(raw)
        except (TypeError, ValueError):
            pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS league_season_status (
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    code TEXT NOT NULL,
    fixtures_total INTEGER NOT NULL DEFAULT 0,
    ids_fetched_at TEXT,
    PRIMARY KEY (league_id, season)
);
CREATE TABLE IF NOT EXISTS fixture_status (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    batch_file TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fixture_status_pending
    ON fixture_status (league_id, season, status);
"""


class HistoryStore:
    """Filesystem + SQLite archive for the historical API-Football pull.

    Layout under ``root``::

        raw/{league_id}/{season}/ids.json.gz       # raw /fixtures?league&season response
        raw/{league_id}/{season}/batches/00001.json.gz  # raw /fixtures?ids=... response
        state.sqlite3                               # per-fixture progress, resumable
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.db_path = self.root / "state.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _season_dir(self, league_id: int, season: int) -> Path:
        return self.raw_dir / str(league_id) / str(season)

    def has_ids(self, league_id: int, season: int) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM league_season_status WHERE league_id=? AND season=?",
                (league_id, season),
            ).fetchone()
        return row is not None

    def save_ids(
        self,
        league_id: int,
        season: int,
        code: str,
        fixture_ids: Sequence[int],
        raw_payload: Mapping[str, Any],
    ) -> None:
        season_dir = self._season_dir(league_id, season)
        season_dir.mkdir(parents=True, exist_ok=True)
        ids_path = season_dir / "ids.json.gz"
        ids_path.write_bytes(gzip.compress(json.dumps(raw_payload, ensure_ascii=False).encode("utf-8")))
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO league_season_status (league_id, season, code, fixtures_total, ids_fetched_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(league_id, season) DO UPDATE SET
                     fixtures_total=excluded.fixtures_total, ids_fetched_at=excluded.ids_fetched_at""",
                (league_id, season, code, len(fixture_ids), _utc_now_iso()),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO fixture_status (fixture_id, league_id, season, status) VALUES (?, ?, ?, 'pending')",
                [(fid, league_id, season) for fid in fixture_ids],
            )
            conn.commit()

    def pending_fixture_ids(self, league_id: int, season: int, limit: int) -> list[int]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT fixture_id FROM fixture_status WHERE league_id=? AND season=? AND status='pending' LIMIT ?",
                (league_id, season, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def write_batch(
        self, league_id: int, season: int, payload: Mapping[str, Any], sequence: int
    ) -> str:
        season_dir = self._season_dir(league_id, season) / "batches"
        season_dir.mkdir(parents=True, exist_ok=True)
        path = season_dir / f"{sequence:05d}.json.gz"
        path.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        return str(path.relative_to(self.root))

    def mark_batch_done(
        self,
        league_id: int,
        season: int,
        requested_ids: Sequence[int],
        returned_ids: Sequence[int],
        batch_file: str,
    ) -> None:
        returned = set(returned_ids)
        fetched_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.executemany(
                "UPDATE fixture_status SET status='done', batch_file=?, fetched_at=? WHERE fixture_id=?",
                [(batch_file, fetched_at, fid) for fid in requested_ids if fid in returned],
            )
            # Requested but not present in the response: flagged rather than
            # retried forever, so a persistently missing id cannot spend the
            # whole daily budget in a retry loop. A future run can requeue
            # 'missing' rows deliberately if needed.
            conn.executemany(
                "UPDATE fixture_status SET status='missing', fetched_at=? WHERE fixture_id=? AND status='pending'",
                [(fetched_at, fid) for fid in requested_ids if fid not in returned],
            )
            conn.commit()

    def next_batch_sequence(self, league_id: int, season: int) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM fixture_status WHERE league_id=? AND season=? AND status IN ('done','missing')",
                (league_id, season),
            ).fetchone()
        done_and_missing = row[0] if row else 0
        return done_and_missing // DEFAULT_BATCH_SIZE + 1

    def progress(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT ls.league_id, ls.code, ls.season, ls.fixtures_total,
                          SUM(CASE WHEN fs.status='done' THEN 1 ELSE 0 END) AS done,
                          SUM(CASE WHEN fs.status='missing' THEN 1 ELSE 0 END) AS missing,
                          SUM(CASE WHEN fs.status='pending' THEN 1 ELSE 0 END) AS pending
                   FROM league_season_status ls
                   LEFT JOIN fixture_status fs
                     ON fs.league_id = ls.league_id AND fs.season = ls.season
                   GROUP BY ls.league_id, ls.season
                   ORDER BY ls.league_id, ls.season"""
            ).fetchall()
        return [
            {
                "league_id": r[0],
                "code": r[1],
                "season": r[2],
                "fixtures_total": r[3],
                "done": r[4] or 0,
                "missing": r[5] or 0,
                "pending": r[6] or 0,
            }
            for r in rows
        ]


@dataclass
class DownloadReport:
    requests_spent: int
    league_seasons_completed: list[str] = field(default_factory=list)
    stopped_reason: str = "completed"


class HistoryDownloader:
    """Resumable, budget-aware orchestration of the two-call-per-batch pull."""

    def __init__(
        self,
        client: HistoryHttpClient,
        store: HistoryStore,
        budget: RequestBudget,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        sleep_between_requests: float = 0.0,
    ):
        self.client = client
        self.store = store
        self.budget = budget
        self.batch_size = batch_size
        self.sleep_between_requests = sleep_between_requests

    def _request(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        payload, headers = self.client.get(endpoint, params)
        self.budget.record(headers)
        if self.sleep_between_requests:
            time.sleep(self.sleep_between_requests)
        return payload

    def run(self, league_seasons: Iterable[LeagueSeason]) -> DownloadReport:
        completed: list[str] = []
        stopped_reason = "completed"
        for ls in league_seasons:
            label = f"{ls.code}:{ls.season}"
            if not self.store.has_ids(ls.league_id, ls.season):
                if not self.budget.allow():
                    stopped_reason = "budget_exhausted"
                    break
                payload = self._request("/fixtures", {"league": ls.league_id, "season": ls.season})
                fixture_ids = [
                    row["fixture"]["id"]
                    for row in payload.get("response", [])
                    if (row.get("fixture") or {}).get("id") is not None
                ]
                self.store.save_ids(ls.league_id, ls.season, ls.code, fixture_ids, payload)

            season_fully_done = True
            while True:
                pending = self.store.pending_fixture_ids(ls.league_id, ls.season, self.batch_size)
                if not pending:
                    break
                if not self.budget.allow():
                    stopped_reason = "budget_exhausted"
                    season_fully_done = False
                    break
                ids_param = "-".join(str(fid) for fid in pending)
                payload = self._request("/fixtures", {"ids": ids_param})
                returned_ids = [
                    row["fixture"]["id"]
                    for row in payload.get("response", [])
                    if (row.get("fixture") or {}).get("id") is not None
                ]
                sequence = self.store.next_batch_sequence(ls.league_id, ls.season)
                batch_file = self.store.write_batch(ls.league_id, ls.season, payload, sequence)
                self.store.mark_batch_done(ls.league_id, ls.season, pending, returned_ids, batch_file)

            if season_fully_done:
                completed.append(label)
            else:
                break

        return DownloadReport(
            requests_spent=self.budget.spent,
            league_seasons_completed=completed,
            stopped_reason=stopped_reason,
        )


__all__ = [
    "BASE_URL",
    "DEFAULT_BATCH_SIZE",
    "LeagueSeason",
    "TOP20_LEAGUES",
    "top20_league_seasons",
    "HistoryHttpClient",
    "RequestBudget",
    "HistoryStore",
    "DownloadReport",
    "HistoryDownloader",
]
