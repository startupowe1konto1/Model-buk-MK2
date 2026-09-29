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


# Second wave: the rest of the 65 European leagues with confirmed fixture
# statistics from the 2026-09-28 catalog probe, minus the 20 leagues above,
# minus women's competitions and minus leagues with fewer than 3 seasons of
# stats coverage (too little history to be useful for training). Generated
# from app/runtime/api_catalog/leagues_2026-09-28.json by
# app/runtime/api_catalog/gen_expansion_leagues.py; season_to is capped at
# 2025 (last fully completed season) for the same reason as TOP20_LEAGUES.
# Includes the Russian top two divisions: they are excluded from most
# European bookmakers' current odds coverage (sanctions since 2022), but
# their historical match statistics remain legitimate training data.
WAVE2_LEAGUES: tuple[LeagueSeason, ...] = tuple(
    LeagueSeason(league_id, code, name, country, season)
    for league_id, code, name, country, season_from, season_to in (
        (219, "2LIGA", "2. Liga", "Austria", 2015, 2025),
        (116, "PREMIERLEAGUE", "Premier League", "Belarus", 2020, 2025),
        (145, "CHALLENGERPROL", "Challenger Pro League", "Belgium", 2023, 2025),
        (172, "FIRSTLEAGUE", "First League", "Bulgaria", 2018, 2025),
        (210, "HNL", "HNL", "Croatia", 2019, 2025),
        (318, "1DIVISION", "1. Division", "Cyprus", 2018, 2025),
        (345, "CZECHLIGA", "Czech Liga", "Czech-Republic", 2019, 2025),
        (41, "LEAGUEONE", "League One", "England", 2015, 2025),
        (42, "LEAGUETWO", "League Two", "England", 2017, 2025),
        (244, "VEIKKAUSLIIGA", "Veikkausliiga", "Finland", 2015, 2025),
        (80, "3LIGA", "3. Liga", "Germany", 2017, 2025),
        (271, "NBI", "NB I", "Hungary", 2019, 2025),
        (357, "PREMIERDIVISIO", "Premier Division", "Ireland", 2023, 2025),
        (383, "LIGATHAAL", "Ligat Ha'al", "Israel", 2021, 2025),
        (89, "EERSTEDIVISIE", "Eerste Divisie", "Netherlands", 2016, 2025),
        (103, "ELITESERIEN", "Eliteserien", "Norway", 2016, 2025),
        (95, "SEGUNDALIGA", "Segunda Liga", "Portugal", 2019, 2025),
        (283, "LIGAI", "Liga I", "Romania", 2016, 2025),
        (235, "PREMIERLEAGUE2", "Premier League", "Russia", 2016, 2025),
        (236, "FIRSTLEAGUE2", "First League", "Russia", 2021, 2025),
        (286, "SUPERLIGA", "Super Liga", "Serbia", 2019, 2025),
        (332, "SUPERLIGA2", "Super Liga", "Slovakia", 2019, 2025),
        (373, "1SNL", "1. SNL", "Slovenia", 2019, 2023),
        (113, "ALLSVENSKAN", "Allsvenskan", "Sweden", 2016, 2025),
        (114, "SUPERETTAN", "Superettan", "Sweden", 2020, 2025),
        (204, "1LIG", "1. Lig", "Turkey", 2016, 2025),
        (333, "PREMIERLEAGUE3", "Premier League", "Ukraine", 2016, 2025),
    )
    for season in range(season_from, season_to + 1)
)


def wave2_league_seasons() -> tuple[LeagueSeason, ...]:
    """Second-wave scope: the rest of the covered European leagues."""

    return WAVE2_LEAGUES


# Third wave (added 2026-09-29): every men's league outside Europe with at
# least 3 seasons of fixture statistics in the 2026-09-28 catalog probe.
# Calendar-year leagues (MLS, Brazil, Japan, ...) use the year as the season,
# so 2025 is their last completed season just as for the European ones; the
# running 2026 season is handled by CurrentSeasonRefresher. season_from is
# the first season with statistics, never earlier than 2015.
WAVE3_LEAGUES: tuple[LeagueSeason, ...] = tuple(
    LeagueSeason(league_id, code, name, country, season)
    for league_id, code, name, country, season_from, season_to in (
        (128, "ARG", "Liga Profesional Argentina", "Argentina", 2015, 2025),
        (188, "AUS", "A-League", "Australia", 2015, 2025),
        (344, "BOL", "Primera Division", "Bolivia", 2023, 2025),
        (71, "BRA", "Serie A", "Brazil", 2015, 2025),
        (72, "BRA2", "Serie B", "Brazil", 2017, 2025),
        (475, "PAULISTA", "Paulista - A1", "Brazil", 2020, 2025),
        (479, "CAN", "Canadian Premier League", "Canada", 2020, 2025),
        (265, "CHI", "Primera Division", "Chile", 2018, 2025),
        (169, "CHN", "Super League", "China", 2016, 2025),
        (239, "COL", "Primera A", "Colombia", 2016, 2025),
        (242, "ECU", "Liga Pro", "Ecuador", 2018, 2025),
        (233, "EGY", "Premier League", "Egypt", 2021, 2025),
        (323, "IND", "Indian Super League", "India", 2018, 2025),
        (98, "JPN", "J1 League", "Japan", 2015, 2025),
        (262, "MEX", "Liga MX", "Mexico", 2016, 2025),
        (200, "MAR", "Botola Pro", "Morocco", 2022, 2025),
        (281, "PER", "Primera Division", "Peru", 2017, 2025),
        (305, "QAT", "Stars League", "Qatar", 2023, 2025),
        (307, "KSA", "Pro League", "Saudi-Arabia", 2016, 2025),
        (288, "RSA", "Premier Soccer League", "South-Africa", 2019, 2025),
        (292, "KOR", "K League 1", "South-Korea", 2020, 2025),
        (296, "THA", "Thai League 1", "Thailand", 2023, 2025),
        (253, "MLS", "Major League Soccer", "USA", 2015, 2025),
        (301, "UAE", "Pro League", "United-Arab-Emirates", 2018, 2025),
        (268, "URU", "Primera Division", "Uruguay", 2021, 2025),
        (270, "URUCLAUSURA", "Primera Division - Clausura", "Uruguay", 2020, 2023),
    )
    for season in range(season_from, season_to + 1)
)

# Club cups (added 2026-09-29): the daily scanner needs the midweek European
# and South American cups and the main domestic cups of the covered
# countries. National-team competitions, super cups and friendlies are left
# out on purpose (few matches, little history per team).
CUP_COMPETITIONS: tuple[LeagueSeason, ...] = tuple(
    LeagueSeason(league_id, code, name, country, season)
    for league_id, code, name, country, season_from, season_to in (
        (2, "UCL", "UEFA Champions League", "World", 2015, 2025),
        (3, "UEL", "UEFA Europa League", "World", 2015, 2025),
        (848, "UECL", "UEFA Europa Conference League", "World", 2021, 2025),
        (13, "LIBERTADORES", "CONMEBOL Libertadores", "World", 2019, 2025),
        (11, "SUDAMERICANA", "CONMEBOL Sudamericana", "World", 2019, 2025),
        (45, "FACUP", "FA Cup", "England", 2015, 2025),
        (48, "EFLCUP", "League Cup", "England", 2015, 2025),
        (81, "DFBPOKAL", "DFB Pokal", "Germany", 2015, 2025),
        (137, "COPPAITALIA", "Coppa Italia", "Italy", 2016, 2025),
        (143, "COPADELREY", "Copa del Rey", "Spain", 2018, 2025),
        (66, "COUPEDEFRANCE", "Coupe de France", "France", 2015, 2025),
        (90, "KNVBBEKER", "KNVB Beker", "Netherlands", 2019, 2025),
        (96, "TACAPORTUGAL", "Taca de Portugal", "Portugal", 2019, 2025),
        (206, "TURKCUP", "Turkiye Kupasi", "Turkey", 2016, 2025),
        (108, "PUCHARPOLSKI", "Puchar Polski", "Poland", 2019, 2024),
        (73, "COPADOBRASIL", "Copa Do Brasil", "Brazil", 2016, 2025),
    )
    for season in range(season_from, season_to + 1)
)


def wave3_league_seasons() -> tuple[LeagueSeason, ...]:
    """Third-wave scope: covered men's leagues outside Europe."""

    return WAVE3_LEAGUES


def cup_seasons() -> tuple[LeagueSeason, ...]:
    """Club cup competitions."""

    return CUP_COMPETITIONS


def all_planned_league_seasons() -> tuple[LeagueSeason, ...]:
    """Full planned scope: top-20, second wave, third wave, then cups.

    Safe to hand straight to :meth:`HistoryDownloader.run` even after the
    earlier waves are already fully archived: ``HistoryStore.has_ids``
    makes each already-completed league-season a zero-request no-op, so a
    run only ever spends budget on genuinely pending work.
    """

    return TOP20_LEAGUES + WAVE2_LEAGUES + WAVE3_LEAGUES + CUP_COMPETITIONS


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

    def add_finished_ids(
        self,
        league_id: int,
        season: int,
        code: str,
        fixture_ids: Sequence[int],
        raw_payload: Mapping[str, Any] | None,
    ) -> int:
        """Register newly finished fixtures of an in-progress season.

        Unlike :meth:`save_ids` this is incremental: existing rows (done or
        missing) are left alone and only unseen ids become ``pending``.
        Returns how many ids were new.
        """

        if raw_payload is not None:
            season_dir = self._season_dir(league_id, season)
            season_dir.mkdir(parents=True, exist_ok=True)
            (season_dir / "ids_finished.json.gz").write_bytes(
                gzip.compress(json.dumps(raw_payload, ensure_ascii=False).encode("utf-8"))
            )
        with closing(self._connect()) as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO fixture_status (fixture_id, league_id, season, status) VALUES (?, ?, ?, 'pending')",
                [(fid, league_id, season) for fid in fixture_ids],
            )
            added = conn.total_changes - before
            total = conn.execute(
                "SELECT COUNT(*) FROM fixture_status WHERE league_id=? AND season=?",
                (league_id, season),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO league_season_status (league_id, season, code, fixtures_total, ids_fetched_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(league_id, season) DO UPDATE SET
                     fixtures_total=excluded.fixtures_total, ids_fetched_at=excluded.ids_fetched_at""",
                (league_id, season, code, total, _utc_now_iso()),
            )
            conn.commit()
        return added

    def next_free_batch_sequence(self, league_id: int, season: int) -> int:
        """One past the highest existing batch file number (safe for incremental runs)."""

        batches = self._season_dir(league_id, season) / "batches"
        numbers = [int(p.name.split(".")[0]) for p in batches.glob("*.json.gz")] if batches.is_dir() else []
        return max(numbers, default=0) + 1

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


FINISHED_STATUSES = "FT-AET-PEN"
CURRENT_SEASON = 2026
# Provider statistics are often filled in a few hours after the final whistle;
# fetching a match too early would archive it with empty stats for good.
DEFAULT_MIN_AGE_HOURS = 6.0


def current_season_scope(season: int = CURRENT_SEASON) -> tuple[LeagueSeason, ...]:
    """The planned leagues, each for one (in-progress) ``season``."""

    seen: dict[int, LeagueSeason] = {}
    for ls in all_planned_league_seasons():
        seen.setdefault(ls.league_id, LeagueSeason(ls.league_id, ls.code, ls.name, ls.country, season))
    return tuple(seen.values())


class CurrentSeasonRefresher(HistoryDownloader):
    """Incrementally archive finished matches of a season that is still running.

    One list call per league (finished statuses only) plus one detail call per
    20 not-yet-archived matches, so a daily run costs roughly one request per
    league plus a handful of batches. Matches that finished less than
    ``min_age_hours`` ago are skipped and picked up by a later run.
    """

    def __init__(self, *args: Any, min_age_hours: float = DEFAULT_MIN_AGE_HOURS, now: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.min_age_hours = min_age_hours
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _old_enough(self, row: Mapping[str, Any]) -> bool:
        stamp = (row.get("fixture") or {}).get("timestamp")
        if stamp is None:
            return True
        age_hours = (self._now().timestamp() - float(stamp)) / 3600.0
        return age_hours >= self.min_age_hours

    def run(self, league_seasons: Iterable[LeagueSeason]) -> DownloadReport:
        completed: list[str] = []
        stopped_reason = "completed"
        for ls in league_seasons:
            if not self.budget.allow():
                stopped_reason = "budget_exhausted"
                break
            payload = self._request(
                "/fixtures", {"league": ls.league_id, "season": ls.season, "status": FINISHED_STATUSES}
            )
            fixture_ids = [
                row["fixture"]["id"]
                for row in payload.get("response", [])
                if (row.get("fixture") or {}).get("id") is not None and self._old_enough(row)
            ]
            self.store.add_finished_ids(ls.league_id, ls.season, ls.code, fixture_ids, payload)

            if self._download_pending(ls):
                completed.append(f"{ls.code}:{ls.season}")
            else:
                stopped_reason = "budget_exhausted"
                break

        return DownloadReport(
            requests_spent=self.budget.spent,
            league_seasons_completed=completed,
            stopped_reason=stopped_reason,
        )

    def run_dates(self, dates: Iterable[Any], scope: Iterable[LeagueSeason]) -> DownloadReport:
        """Cheap variant for frequent runs: one list call per calendar day.

        ``/fixtures?date=`` returns finished matches of every league at once,
        so a run costs a couple of requests plus the detail batches, instead
        of one list request per league.
        """

        by_league = {ls.league_id: ls for ls in scope}
        completed: list[str] = []
        stopped_reason = "completed"
        touched: dict[int, LeagueSeason] = {}
        for day in dates:
            if not self.budget.allow():
                stopped_reason = "budget_exhausted"
                break
            payload = self._request("/fixtures", {"date": day.isoformat(), "status": FINISHED_STATUSES})
            ids_by_league: dict[int, list[int]] = {}
            for row in payload.get("response", []):
                league = row.get("league") or {}
                ls = by_league.get(league.get("id"))
                fixture_id = (row.get("fixture") or {}).get("id")
                if ls is None or fixture_id is None:
                    continue
                if league.get("season") != ls.season or not self._old_enough(row):
                    continue
                ids_by_league.setdefault(ls.league_id, []).append(fixture_id)
            for league_id, ids in ids_by_league.items():
                ls = by_league[league_id]
                self.store.add_finished_ids(ls.league_id, ls.season, ls.code, ids, None)
                touched[league_id] = ls
        if stopped_reason == "completed":
            # Also drain anything left pending by an earlier interrupted run.
            for ls in by_league.values():
                if self.store.pending_fixture_ids(ls.league_id, ls.season, 1):
                    touched[ls.league_id] = ls
        for ls in touched.values():
            if self._download_pending(ls):
                completed.append(f"{ls.code}:{ls.season}")
            else:
                stopped_reason = "budget_exhausted"
                break
        return DownloadReport(
            requests_spent=self.budget.spent,
            league_seasons_completed=completed,
            stopped_reason=stopped_reason,
        )

    def _download_pending(self, ls: LeagueSeason) -> bool:
        """Fetch detail for every pending match of ``ls``; False if the budget ran out."""

        while True:
            pending = self.store.pending_fixture_ids(ls.league_id, ls.season, self.batch_size)
            if not pending:
                return True
            if not self.budget.allow():
                return False
            response = self._request("/fixtures", {"ids": "-".join(str(fid) for fid in pending)})
            returned_ids = [
                row["fixture"]["id"]
                for row in response.get("response", [])
                if (row.get("fixture") or {}).get("id") is not None
            ]
            sequence = self.store.next_free_batch_sequence(ls.league_id, ls.season)
            batch_file = self.store.write_batch(ls.league_id, ls.season, response, sequence)
            self.store.mark_batch_done(ls.league_id, ls.season, pending, returned_ids, batch_file)


__all__ = [
    "BASE_URL",
    "DEFAULT_BATCH_SIZE",
    "LeagueSeason",
    "TOP20_LEAGUES",
    "top20_league_seasons",
    "WAVE2_LEAGUES",
    "wave2_league_seasons",
    "WAVE3_LEAGUES",
    "wave3_league_seasons",
    "CUP_COMPETITIONS",
    "cup_seasons",
    "all_planned_league_seasons",
    "HistoryHttpClient",
    "RequestBudget",
    "HistoryStore",
    "DownloadReport",
    "HistoryDownloader",
    "CurrentSeasonRefresher",
    "current_season_scope",
    "CURRENT_SEASON",
    "FINISHED_STATUSES",
    "DEFAULT_MIN_AGE_HOURS",
]
