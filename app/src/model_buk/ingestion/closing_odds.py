"""Near-kickoff odds polling: turns "last quote before kickoff" into a real closing line.

The hourly :class:`~model_buk.ingestion.api_football_odds_archive.OddsCollector`
sees a match's odds at best 60 minutes before kickoff, so its last stored quote can
be an hour old.  This module runs every 15 minutes and re-fetches the odds of
fixtures that kick off within the next ``window_minutes`` (default 150), appending
the result to the *same* odds archive through the existing
:class:`~model_buk.ingestion.api_football_odds_archive.OddsArchive` writer:
change-only ``quotes``, one ``observations`` row per (scan, fixture, bookmaker),
raw files for the fixtures whose provider ``update`` moved.  Its scans are marked
``kind = 'near_kickoff'`` (hourly scans have ``kind IS NULL``) and carry the id
prefix ``nk-``.  :func:`closing_quotes` reads the result back.

What the provider allows (probed 2026-09-30):

- ``/odds?fixture=ID`` returns one fixture with every bookmaker and bet type;
  several ids in one call are rejected.
- ``/odds?league=L&season=S&date=D`` returns all of a league's fixtures that day,
  10 per page, so it costs ``ceil(n/10)`` requests for the whole league-day.
  The season is the *league's* season, which for some competitions differs from
  the calendar year (UEFA U21 qualification 2026-09 is season 2027).
- The provider refreshes a fixture's odds board only every few hours (the
  ``update`` field), sometimes more often close to kickoff.  Polling faster than
  that finds nothing new, so each run reports how many boards actually changed.

Request planning (:func:`plan_requests`): fixtures due in the same league on the
same UTC day are fetched with one league-day request when that is cheaper than one
request per fixture.  A fixture is *due* when the time since its last observation
(hourly or near-kickoff) exceeds a gap that shrinks as kickoff approaches
(:data:`DEFAULT_CADENCE`): every run inside the last 20 minutes, about every
half hour inside the last hour, hourly before that.  Requests go out in kickoff
order, so when the budget runs out the matches that are about to start were served
first.

Fixtures that the fixture directory lists but the odds archive has never seen are
probed too, because some competitions only get odds a few hours before kickoff:
at most ``max_unseen`` per run, twice per fixture, only in leagues the odds archive
already knows from other fixtures, and not in leagues whose probes keep coming
back empty.  A first real run (2026-09-30) found no odds for any of 20 such
fixtures, so this is a cheap safety net, not a main source; ``unseen_found`` in the
run report shows whether it ever pays.  Their outcome is kept in the directory
database (``odds_probes``).

A response's ``fetched_at`` is taken when it arrives.  Fixtures whose kickoff is not
after that moment are dropped: a price seen after kickoff is not pre-match.
"""

from __future__ import annotations

import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from model_buk.ingestion.api_football_history import HistoryHttpClient, RequestBudget
from model_buk.ingestion.api_football_odds_archive import OddsArchive, OddsScanReport
from model_buk.ingestion.fixture_directory import FixtureDirectory, UPCOMING_STATUSES

NEAR_KICKOFF_KIND = "near_kickoff"
SCAN_ID_PREFIX = "nk-"
DEFAULT_WINDOW_MINUTES = 150.0
# (minutes to kickoff up to, minimum minutes since the last observation of the fixture)
DEFAULT_CADENCE: tuple[tuple[float, float], ...] = ((20.0, 10.0), (60.0, 25.0), (150.0, 55.0))
DENSE_CADENCE: tuple[tuple[float, float], ...] = ((10_000.0, 0.0),)
ODDS_PAGE_SIZE = 10
DEFAULT_MAX_UNSEEN = 10
MAX_EMPTY_PROBES = 2
REPROBE_MINUTES = 45.0
# A league is skipped for probing after this many probes in three days without a single hit.
LEAGUE_GIVE_UP_PROBES = 4
MAX_CONSECUTIVE_ERRORS = 3


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def min_gap_minutes(minutes_to_kickoff: float, cadence: Sequence[tuple[float, float]] = DEFAULT_CADENCE) -> float:
    """Minimum minutes since the last observation before a fixture is fetched again."""

    for limit, gap in cadence:
        if minutes_to_kickoff <= limit:
            return gap
    return cadence[-1][1]


# --------------------------------------------------------------------------
# Planning (pure)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A fixture kicking off inside the window."""

    fixture_id: int
    league_id: int
    season: int | None
    kickoff: datetime
    in_odds_archive: bool
    last_observed: datetime | None = None


@dataclass(frozen=True)
class OddsRequest:
    """One provider request: a single fixture, or a whole league-day (paged)."""

    params: tuple[tuple[str, Any], ...]
    fixture_ids: tuple[int, ...]
    earliest_kickoff: datetime
    estimated_requests: int

    @property
    def is_league_day(self) -> bool:
        return dict(self.params).keys() >= {"league", "season", "date"}


def select_due(
    candidates: Sequence[Candidate],
    now: datetime,
    cadence: Sequence[tuple[float, float]] = DEFAULT_CADENCE,
) -> list[Candidate]:
    """Known fixtures whose last observation is older than the gap for their time to kickoff."""

    due = []
    for candidate in candidates:
        if not candidate.in_odds_archive:
            continue
        minutes_to_kickoff = (candidate.kickoff - now).total_seconds() / 60.0
        if candidate.last_observed is None:
            due.append(candidate)
            continue
        since = (now - candidate.last_observed).total_seconds() / 60.0
        if since >= min_gap_minutes(minutes_to_kickoff, cadence):
            due.append(candidate)
    return due


def plan_requests(
    due: Sequence[Candidate],
    league_day_fixtures: Mapping[tuple[int, str], int],
) -> list[OddsRequest]:
    """Group due fixtures into the fewest provider requests, nearest kickoff first.

    ``league_day_fixtures`` maps ``(league_id, 'YYYY-MM-DD')`` to how many fixtures
    that league plays that day (an upper bound of what a league-day request
    returns).  Fixtures of one league-day become a single paged request when
    ``ceil(n / 10)`` is smaller than the number of due fixtures; otherwise each is
    fetched on its own with ``fixture=ID``.
    """

    groups: dict[tuple[int, int | None, str], list[Candidate]] = {}
    for candidate in due:
        key = (candidate.league_id, candidate.season, candidate.kickoff.date().isoformat())
        groups.setdefault(key, []).append(candidate)

    requests: list[OddsRequest] = []
    for (league_id, season, day), members in groups.items():
        total = max(league_day_fixtures.get((league_id, day), len(members)), len(members))
        pages = max(1, math.ceil(total / ODDS_PAGE_SIZE))
        if season is not None and pages < len(members):
            requests.append(OddsRequest(
                params=(("league", league_id), ("season", season), ("date", day)),
                fixture_ids=tuple(sorted(m.fixture_id for m in members)),
                earliest_kickoff=min(m.kickoff for m in members),
                estimated_requests=pages,
            ))
        else:
            for member in members:
                requests.append(OddsRequest(
                    params=(("fixture", member.fixture_id),),
                    fixture_ids=(member.fixture_id,),
                    earliest_kickoff=member.kickoff,
                    estimated_requests=1,
                ))
    return sorted(requests, key=lambda r: (r.earliest_kickoff, r.fixture_ids))


# --------------------------------------------------------------------------
# Reading the closing line back
# --------------------------------------------------------------------------


def closing_quotes(
    db_path: Any,
    *,
    fixture_ids: Sequence[int] | None = None,
    kickoff_from: datetime | None = None,
    kickoff_to: datetime | None = None,
    max_minutes_before_kickoff: float | None = None,
) -> Any:
    """The last price of every (fixture, bookmaker, bet, selection) at or before kickoff.

    Returns a DataFrame with ``fixture_id, kickoff_utc, bookmaker_id, bookmaker,
    bet_id, bet, selection, odd, quote_fetched_at, last_observed_at,
    minutes_observed_before_kickoff``.

    ``odd`` is the latest stored price (the archive keeps price *changes*);
    ``quote_fetched_at`` is when that price was first seen and ``last_observed_at``
    the last time the bookmaker's board for the fixture was looked at before
    kickoff.  With near-kickoff polling ``minutes_observed_before_kickoff`` is
    small and the price really was on offer then; without it the value can be an
    hour old.  ``max_minutes_before_kickoff`` keeps only rows observed that close
    to kickoff.  A selection that disappeared from the board before the last
    observation still shows its last price (the archive stores no removals).

    Closing prices are for closing-line value only, never an input of a decision:
    a decision at time ``T`` reads
    :meth:`~model_buk.ingestion.api_football_odds_archive.OddsArchive.quotes_as_of`.
    Bet ids, bet names and selections are the provider's raw ones; mapping them to
    market, period, line and selection is the caller's job.
    """

    import pandas as pd

    conditions: list[str] = []
    params: list[Any] = []
    if fixture_ids is not None:
        ids = [int(i) for i in fixture_ids]
        conditions.append(f"f.fixture_id IN ({','.join('?' * len(ids))})" if ids else "0")
        params.extend(ids)
    if kickoff_from is not None:
        conditions.append("f.kickoff_utc >= ?")
        params.append(_iso(kickoff_from))
    if kickoff_to is not None:
        conditions.append("f.kickoff_utc < ?")
        params.append(_iso(kickoff_to))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        WITH k AS (SELECT f.fixture_id, f.kickoff_utc FROM fixtures f {where}),
        obs AS (
            SELECT o.fixture_id, o.bookmaker_id, MAX(o.fetched_at) AS last_observed_at
            FROM observations o JOIN k ON k.fixture_id = o.fixture_id
            WHERE o.fetched_at <= k.kickoff_utc
            GROUP BY o.fixture_id, o.bookmaker_id
        )
        SELECT q.fixture_id, k.kickoff_utc, q.bookmaker_id, q.bookmaker, q.bet_id, q.bet, q.selection, q.odd,
               q.fetched_at AS quote_fetched_at, obs.last_observed_at
        FROM quotes q
        JOIN k ON k.fixture_id = q.fixture_id
        JOIN obs ON obs.fixture_id = q.fixture_id AND obs.bookmaker_id = q.bookmaker_id
        WHERE q.fetched_at <= k.kickoff_utc
          AND q.rowid = (
              SELECT q2.rowid FROM quotes q2
              WHERE q2.fixture_id = q.fixture_id AND q2.bookmaker_id = q.bookmaker_id
                AND q2.bet_id = q.bet_id AND q2.selection = q.selection AND q2.fetched_at <= k.kickoff_utc
              ORDER BY q2.fetched_at DESC, q2.rowid DESC LIMIT 1)
        ORDER BY q.fixture_id, q.bookmaker_id, q.bet_id, q.selection
    """
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        frame = pd.read_sql_query(sql, conn, params=params)
    if frame.empty:
        frame["minutes_observed_before_kickoff"] = pd.Series(dtype="float64")
        return frame
    kickoff = pd.to_datetime(frame["kickoff_utc"], utc=True, format="ISO8601")
    observed = pd.to_datetime(frame["last_observed_at"], utc=True, format="ISO8601")
    frame["minutes_observed_before_kickoff"] = (kickoff - observed).dt.total_seconds() / 60.0
    if max_minutes_before_kickoff is not None:
        frame = frame[frame["minutes_observed_before_kickoff"] <= max_minutes_before_kickoff].reset_index(drop=True)
    return frame


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------


@dataclass
class ClosingReport:
    scan_id: str
    window_fixtures: int = 0
    due_fixtures: int = 0
    unseen_probed: int = 0
    unseen_found: int = 0
    planned_requests: int = 0
    requests: int = 0
    fixtures_recorded: int = 0
    boards_changed: int = 0
    quotes_changed: int = 0
    stopped_reason: str = "completed"
    errors: list[str] = field(default_factory=list)


class ClosingOddsCollector:
    """One near-kickoff run: select what is due, fetch it, append it to the odds archive.

    ``directory`` provides the fixture calendar (extra candidates, league-day sizes)
    and the small persistent state of this job (probe outcomes, quota memory).
    """

    def __init__(
        self,
        client: HistoryHttpClient,
        archive: OddsArchive,
        directory: FixtureDirectory,
        budget: RequestBudget,
        *,
        window_minutes: float = DEFAULT_WINDOW_MINUTES,
        cadence: Sequence[tuple[float, float]] = DEFAULT_CADENCE,
        max_unseen: int = DEFAULT_MAX_UNSEEN,
        sleep_between_requests: float = 0.0,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.archive = archive
        self.directory = directory
        self.budget = budget
        self.window_minutes = window_minutes
        self.cadence = tuple(cadence)
        self.max_unseen = max_unseen
        self.sleep_between_requests = sleep_between_requests
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- selection ------------------------------------------------------

    def candidates(self, now: datetime) -> list[Candidate]:
        """Every fixture kicking off within the window, from the odds archive and the directory.

        For a fixture in both, the fresher of the two records decides its kickoff time
        and whether it is still upcoming (a postponed or already running match is
        dropped).  Directory fixtures the odds archive has never seen are returned
        with ``in_odds_archive=False``.
        """

        now = now.astimezone(timezone.utc)
        lo, hi = _iso(now), _iso(now + timedelta(minutes=self.window_minutes))
        with closing(sqlite3.connect(self.archive.db_path, timeout=60)) as conn:
            archive_rows = conn.execute(
                "SELECT fixture_id, league_id, season, kickoff_utc, last_seen_at FROM fixtures "
                "WHERE kickoff_utc > ? AND kickoff_utc <= ?", (lo, hi)
            ).fetchall()
        with closing(self.directory._connect()) as conn:
            directory_rows = conn.execute(
                "SELECT fixture_id, league_id, season, kickoff_utc, status_short, fetched_at FROM fixtures "
                "WHERE kickoff_utc > ? AND kickoff_utc <= ?", (lo, hi)
            ).fetchall()
            archive_ids = [r[0] for r in archive_rows]
            fresher: dict[int, tuple[str, str, str]] = {}
            for start in range(0, len(archive_ids), 500):
                chunk = archive_ids[start:start + 500]
                for fid, kickoff, status, fetched in conn.execute(
                    f"SELECT fixture_id, kickoff_utc, status_short, fetched_at FROM fixtures "
                    f"WHERE fixture_id IN ({','.join('?' * len(chunk))})", chunk
                ):
                    fresher[fid] = (kickoff, status, fetched)

        found: dict[int, Candidate] = {}
        for fid, league_id, season, kickoff, last_seen in archive_rows:
            kickoff_at = _parse_utc(kickoff)
            info = fresher.get(fid)
            if info is not None and info[2] >= (last_seen or ""):
                if info[1] not in UPCOMING_STATUSES:
                    continue  # postponed, cancelled or already running per the newer record
                kickoff_at = _parse_utc(info[0])
                if not (now < kickoff_at <= now + timedelta(minutes=self.window_minutes)):
                    continue
            # ``last_seen_at`` is the time of the latest scan (hourly or near-kickoff) that saw the fixture.
            found[fid] = Candidate(fid, league_id, season, kickoff_at, True, _parse_utc(last_seen) if last_seen else None)
        for fid, league_id, season, kickoff, status, _ in directory_rows:
            if fid in found or status != "NS":  # TBD has no reliable kickoff
                continue
            found[fid] = Candidate(fid, league_id, season, _parse_utc(kickoff), False, None)
        return sorted(found.values(), key=lambda c: (c.kickoff, c.fixture_id))

    def league_day_sizes(self, candidates: Sequence[Candidate]) -> dict[tuple[int, str], int]:
        """How many fixtures each league plays on the UTC days of ``candidates`` (directory and archive, larger wins)."""

        days = sorted({c.kickoff.date() for c in candidates})
        sizes: dict[tuple[int, str], int] = {}
        if not days:
            return sizes
        for source, conn_factory in (("directory", self.directory._connect), ("archive", lambda: sqlite3.connect(self.archive.db_path, timeout=60))):
            with closing(conn_factory()) as conn:
                for day in days:
                    start = _iso(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc))
                    end = _iso(datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
                    for league_id, n in conn.execute(
                        "SELECT league_id, COUNT(*) FROM fixtures WHERE kickoff_utc >= ? AND kickoff_utc < ? GROUP BY league_id",
                        (start, end),
                    ):
                        key = (league_id, day.isoformat())
                        sizes[key] = max(sizes.get(key, 0), n)
        return sizes

    def select_unseen(self, candidates: Sequence[Candidate], now: datetime) -> list[Candidate]:
        """Directory-only fixtures worth one probe of the odds feed."""

        unseen = [c for c in candidates if not c.in_odds_archive]
        if not unseen or self.max_unseen <= 0:
            return []
        with closing(sqlite3.connect(self.archive.db_path, timeout=60)) as conn:
            odds_leagues = {r[0] for r in conn.execute("SELECT DISTINCT league_id FROM fixtures")}
        unseen = [c for c in unseen if c.league_id in odds_leagues]
        history = self.directory.probe_history([c.fixture_id for c in unseen])
        leagues = self.directory.league_probe_stats(now - timedelta(days=3))
        chosen: list[Candidate] = []
        for candidate in unseen:
            n_empty, last = history.get(candidate.fixture_id, (0, None))
            if n_empty >= MAX_EMPTY_PROBES:
                continue
            if last is not None and _parse_utc(last) > now - timedelta(minutes=REPROBE_MINUTES):
                continue
            probes, hits = leagues.get(candidate.league_id, (0, 0))
            if probes >= LEAGUE_GIVE_UP_PROBES and hits == 0:
                continue
            chosen.append(candidate)
            if len(chosen) >= self.max_unseen:
                break
        return chosen

    # ---- run ------------------------------------------------------------

    def _unique_scan_id(self, now: datetime) -> str:
        base = SCAN_ID_PREFIX + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with closing(sqlite3.connect(self.archive.db_path, timeout=60)) as conn:
            taken = {r[0] for r in conn.execute("SELECT scan_id FROM scans WHERE scan_id LIKE ?", (base + "%",))}
        scan_id, counter = base, 1
        while scan_id in taken:
            counter += 1
            scan_id = f"{base}-{counter}"
        return scan_id

    def _get(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            payload, headers = self.client.get("/odds", params)
        except Exception:
            self.budget.record({})  # a failed call still costs quota
            raise
        self.budget.record(headers)
        if self.budget.remaining_quota is not None:
            self.directory.save_quota(self.budget.remaining_quota, self.clock())
        if self.sleep_between_requests:
            time.sleep(self.sleep_between_requests)
        return payload

    def run(self, now: datetime) -> ClosingReport:
        now = now.astimezone(timezone.utc)
        scan_id = self._unique_scan_id(now)
        report = ClosingReport(scan_id=scan_id)
        self.directory.seed_budget(self.budget, now)

        candidates = self.candidates(now)
        report.window_fixtures = len(candidates)
        due = select_due(candidates, now, self.cadence)
        probes = self.select_unseen(candidates, now)
        report.due_fixtures = len(due)
        report.unseen_probed = len(probes)
        work = due + probes
        plan = plan_requests(work, self.league_day_sizes(work))
        report.planned_requests = sum(r.estimated_requests for r in plan)

        dates = sorted({c.kickoff.date().isoformat() for c in work}) or [now.date().isoformat()]
        self.archive.start_scan(scan_id, now, dates, kind=NEAR_KICKOFF_KIND)

        probe_ids = {c.fixture_id: c for c in probes}
        raw_entries: list[tuple[datetime, Mapping[str, Any]]] = []
        answered: set[int] = set()  # fixtures whose request got an answer
        with_odds: set[int] = set()  # fixtures the answers actually carried odds for
        consecutive_errors = 0
        for request in plan:
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                break
            if self._fetch(request, scan_id, report, raw_entries, answered, with_odds):
                consecutive_errors = 0
                continue
            if report.stopped_reason == "budget_exhausted":
                break
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                report.stopped_reason = "api_error"
                break

        # A probe only counts once the request that covered it was actually answered.
        probe_rows = [(fid, probe_ids[fid].league_id, fid in with_odds) for fid in sorted(probe_ids) if fid in answered]
        if probe_rows:
            self.directory.log_probes(self.clock(), probe_rows)
            report.unseen_found = sum(1 for _, _, hit in probe_rows if hit)

        raw_file = self.archive.write_raw_entries(scan_id, now, raw_entries) if raw_entries else None
        self.archive.finish_scan(
            OddsScanReport(
                scan_id=scan_id, requests=report.requests, fixtures_seen=report.fixtures_recorded,
                quotes_changed=report.quotes_changed, stopped_reason=report.stopped_reason, dates=dates,
            ),
            self.clock(), raw_file,
        )
        return report

    def _fetch(
        self,
        request: OddsRequest,
        scan_id: str,
        report: ClosingReport,
        raw_entries: list[tuple[datetime, Mapping[str, Any]]],
        answered: set[int],
        with_odds: set[int],
    ) -> bool:
        """Fetch every page of ``request`` and record it; False on an API error or budget stop."""

        params = dict(request.params)
        page, total_pages = 1, 1
        while page <= total_pages:
            if page > 1:
                if not self.budget.allow():
                    report.stopped_reason = "budget_exhausted"
                    return False
                params["page"] = page
            report.requests += 1
            try:
                payload = self._get(params)
            except Exception as exc:  # noqa: BLE001 - one failing request must not lose the rest
                report.errors.append(f"{dict(request.params)}: {exc}")
                return False
            fetched_at = self.clock()
            total_pages = int((payload.get("paging") or {}).get("total") or 1)
            kept = []
            for obj in payload.get("response") or []:
                fixture = obj.get("fixture") or {}
                if fixture.get("id") is None or not fixture.get("date"):
                    continue
                if _parse_utc(fixture["date"]) <= fetched_at:
                    continue  # already kicked off: not a pre-match price
                kept.append(obj)
                if obj.get("bookmakers"):
                    with_odds.add(int(fixture["id"]))
            fresh = self.archive.changed_since_last_scan(kept)
            raw_entries.extend((fetched_at, obj) for obj in fresh)
            recorded, changed = self.archive.record(scan_id, fetched_at, kept)
            report.fixtures_recorded += recorded
            report.boards_changed += len(fresh)
            report.quotes_changed += changed
            page += 1
        answered.update(request.fixture_ids)
        return True


__all__ = [
    "NEAR_KICKOFF_KIND",
    "SCAN_ID_PREFIX",
    "DEFAULT_WINDOW_MINUTES",
    "DEFAULT_CADENCE",
    "DENSE_CADENCE",
    "Candidate",
    "ClosingOddsCollector",
    "ClosingReport",
    "OddsRequest",
    "closing_quotes",
    "min_gap_minutes",
    "plan_requests",
    "select_due",
]
