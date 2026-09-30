"""Directory of every fixture of every league, plus match statistics for settlement.

The odds archive only knows fixtures that have odds; the scanner also needs the
*whole* calendar (league name, teams, kickoff, status) to name what it prices and,
after the match, the final score and the team statistics that decide corner,
card and shot legs.  ``/fixtures?date=YYYY-MM-DD`` returns every match of every
league for one UTC day in a single response, so covering all leagues costs one
request per day, not one per league.

Two jobs live here:

1. **Calendar** (:meth:`FixtureDirectoryCollector.refresh`): lists a window of
   days (default three back to seven ahead) and upserts each fixture to its latest
   known state.  Past days give final scores for settlement.  To keep a run at a
   handful of requests, today is listed every run, tomorrow and the far future
   only when their last listing is stale, and a past day is re-listed until one
   listing was taken after that day was surely over (``settle_delay_hours`` after
   midnight).
2. **Settlement data** (:meth:`FixtureDirectoryCollector.fetch_fixture_details`):
   ``/fixtures?ids=a-b-c`` (up to 20 ids per call) returns score, per-team
   statistics and events; parsed into ``fixture_stats`` (corners, cards, shots)
   with ``NULL`` wherever the provider gave nothing.  First-half and second-half
   splits need one ``/fixtures/statistics?fixture=ID&half=true`` call per match
   (:meth:`FixtureDirectoryCollector.fetch_half_stats`) and land in
   ``fixture_stats_half``; ask for them only for matches with half-time legs.

Score convention (important for settlement): ``home_goals``/``away_goals`` are the
**regular-time (90 minute) score**, which is what bookmakers settle on; goals
scored in extra time are excluded.  The provider's own ``goals`` field (which
includes extra time) is kept as ``total_home_goals``/``total_away_goals``.  While a
match is running they show the live score; look at ``status_short``.  Awarded,
walkover and abandoned matches have no score (``NULL``).

Statistics convention: corners, cards and shots are settled on the 90 minutes too,
but the provider's totals of a match that went to extra time (``AET``/``PEN``) include
it, so those matches get ``NULL`` statistics; card counts from the event list only
use minutes up to 90 and only complete logs (see :func:`parse_fixture_stats`).
Statistics of a match that is not finished are never stored.

Storage layout under ``root``::

    raw/fixtures/{YYYY-MM-DD}/{run_id}_{list_date}.jsonl.gz   # new or changed fixtures only
    raw/details/{YYYY-MM-DD}/{run_id}_{seq}.jsonl.gz          # /fixtures?ids responses, complete
    raw/half_stats/{YYYY-MM-DD}/{run_id}.jsonl.gz             # /fixtures/statistics?half=true
    directory.sqlite3                                          # fixtures, fixture_stats, ...

Raw files are append-only (an existing file is never rewritten; a name clash gets
a ``-N`` suffix); every line carries ``fetched_at``, taken after the response
arrived, which is the conservative "known at" time.  A missing or
``null`` provider value is stored as SQL ``NULL``, never as zero.  The daily
quota is shared with other jobs, so every run works under a
:class:`~model_buk.ingestion.api_football_history.RequestBudget` with a reserve
floor.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from model_buk.ingestion.api_football_history import (
    DEFAULT_BATCH_SIZE,
    HistoryHttpClient,
    RequestBudget,
)
from model_buk.ingestion.api_football_tables import (
    parse_int,
    parse_kickoff_utc,
    parse_number,
    parse_team_match_stats,
    team_side,
)

DIRECTORY_DB = "directory.sqlite3"

FINISHED_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN"})
# Statuses after which the provider will not change the listing any more
# (a postponed match reappears under its new date instead).
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "PST"}
)
LIVE_STATUSES: frozenset[str] = frozenset({"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"})
UPCOMING_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})
# The provider's statistics and events of these matches run through extra time (verified on the
# history archive: AET matches average 13.2 corners against 9.8 in FT matches), so they say nothing
# about the 90 minutes that bookmakers settle corner, card and shot legs on.
EXTRA_TIME_STATUSES: frozenset[str] = frozenset({"AET", "PEN"})
# Matches without a played result: an awarded score is not a score, and bets on them are void.
NO_SCORE_STATUSES: frozenset[str] = frozenset({"AWD", "WO", "ABD"})
REGULAR_TIME_MINUTES = 90

DEFAULT_DAYS_BACK = 3
DEFAULT_DAYS_AHEAD = 7
# A past day is settled once it was listed this many hours after its midnight end
# (last kickoffs of the day are over and the provider has filled in the finals).
DEFAULT_SETTLE_DELAY_HOURS = 6.0
DEFAULT_NEAR_REFRESH_MINUTES = 60.0
DEFAULT_FAR_REFRESH_HOURS = 6.0
# Statistics are filled in a while after the final whistle; an empty answer is
# retried later, a few times, then given up (many friendlies never get any).
DEFAULT_STATS_MIN_AGE_HOURS = 2.0
DEFAULT_STATS_RETRY_HOURS = 3.0
MAX_STATS_ATTEMPTS = 4
MAX_CONSECUTIVE_ERRORS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    league_name TEXT,
    country TEXT,
    season INTEGER,
    round TEXT,
    kickoff_utc TEXT NOT NULL,
    status_short TEXT,
    status_long TEXT,
    elapsed INTEGER,
    status_extra INTEGER,
    home_team_id INTEGER,
    home_team TEXT,
    away_team_id INTEGER,
    away_team TEXT,
    home_goals INTEGER,
    away_goals INTEGER,
    total_home_goals INTEGER,
    total_away_goals INTEGER,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    et_home_goals INTEGER,
    et_away_goals INTEGER,
    pen_home INTEGER,
    pen_away INTEGER,
    venue_id INTEGER,
    venue_name TEXT,
    venue_city TEXT,
    referee TEXT,
    list_date TEXT,
    first_seen_at TEXT NOT NULL,
    state_changed_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    state_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_dir_fixtures_kickoff ON fixtures (kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_dir_fixtures_status ON fixtures (status_short, kickoff_utc);
CREATE TABLE IF NOT EXISTS directory_fetches (
    run_id TEXT NOT NULL,
    list_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    pages INTEGER NOT NULL,
    fixtures_listed INTEGER NOT NULL,
    fixtures_new INTEGER NOT NULL,
    fixtures_changed INTEGER NOT NULL,
    raw_file TEXT,
    PRIMARY KEY (run_id, list_date)
);
CREATE INDEX IF NOT EXISTS idx_dir_fetches_date ON directory_fetches (list_date, fetched_at);
CREATE TABLE IF NOT EXISTS fixture_stats (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    side TEXT,
    corners INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    yellow_cards_events INTEGER,
    red_cards_events INTEGER,
    second_yellow_events INTEGER,
    events_count INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (fixture_id, team_id)
);
CREATE TABLE IF NOT EXISTS fixture_stats_half (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    side TEXT,
    half TEXT NOT NULL,
    corners INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (fixture_id, team_id, half)
);
CREATE TABLE IF NOT EXISTS stats_attempts (
    fixture_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    teams_found INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_attempts ON stats_attempts (fixture_id, kind);
CREATE TABLE IF NOT EXISTS odds_probes (
    fixture_id INTEGER NOT NULL,
    league_id INTEGER,
    probed_at TEXT NOT NULL,
    found INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_odds_probes ON odds_probes (fixture_id);
CREATE TABLE IF NOT EXISTS quota_memory (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    remaining INTEGER NOT NULL,
    seen_at TEXT NOT NULL
);
"""

FIXTURE_COLUMNS: tuple[str, ...] = (
    "fixture_id", "league_id", "league_name", "country", "season", "round", "kickoff_utc",
    "status_short", "status_long", "elapsed", "status_extra",
    "home_team_id", "home_team", "away_team_id", "away_team",
    "home_goals", "away_goals", "total_home_goals", "total_away_goals",
    "ht_home_goals", "ht_away_goals", "et_home_goals", "et_away_goals", "pen_home", "pen_away",
    "venue_id", "venue_name", "venue_city", "referee",
)
# The columns of the shared "fixtures frame" contract, in order.
FIXTURES_FRAME_COLUMNS: tuple[str, ...] = (
    "fixture_id", "league_id", "league_name", "country", "season", "round", "kickoff_utc",
    "status_short", "home_team_id", "home_team", "away_team_id", "away_team",
    "home_goals", "away_goals",
)
_STATS_COLUMNS: tuple[str, ...] = (
    "fixture_id", "team_id", "side", "corners", "yellow_cards", "red_cards", "shots", "shots_on_target",
    "yellow_cards_events", "red_cards_events", "second_yellow_events", "events_count", "fetched_at",
)
_STAT_VALUE_COLUMNS: tuple[str, ...] = ("corners", "yellow_cards", "red_cards", "shots", "shots_on_target")
# Columns added after the first release; applied to existing databases.
_MIGRATIONS = (("fixture_stats", "events_count", "INTEGER"),)


def _has_stat_data_sql(alias: str = "") -> str:
    """SQL condition: some column carries a value from the provider's statistics block."""

    prefix = f"{alias}." if alias else ""
    return "(" + " OR ".join(f"{prefix}{c} IS NOT NULL" for c in _STAT_VALUE_COLUMNS) + ")"
_HALF_COLUMNS: tuple[str, ...] = (
    "fixture_id", "team_id", "side", "half", "corners", "yellow_cards", "red_cards", "shots",
    "shots_on_target", "fetched_at",
)
# Provider statistic label -> column of the half-time table.
_HALF_STAT_TYPES: dict[str, str] = {
    "Corner Kicks": "corners",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Total Shots": "shots",
    "Shots on Goal": "shots_on_target",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _run_id(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _write_raw(path: Path, root: Path, records: Sequence[Mapping[str, Any]]) -> str:
    """Append-only gzip-JSONL write; returns the path relative to ``root``.

    An existing file is never touched: if the name is taken (two runs in the same
    second) the records go to the next free ``-N`` suffix instead.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    stem = path.name[: -len(".jsonl.gz")]
    candidate, counter = path, 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{stem}-{counter}.jsonl.gz")
    lines = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    candidate.write_bytes(gzip.compress(lines.encode("utf-8")))
    return str(candidate.relative_to(root))


# --------------------------------------------------------------------------
# Pure parsers
# --------------------------------------------------------------------------


def regular_time_score(
    status_short: str | None, goals: Mapping[str, Any], score: Mapping[str, Any]
) -> tuple[int | None, int | None]:
    """The 90-minute score of a fixture object (what 1X2, totals and handicaps settle on).

    The provider's ``goals`` include extra time, so a match decided in extra time
    would settle 90-minute legs wrongly.  ``score.fulltime`` is the 90-minute
    score; without it an ``AET``/``PEN`` match falls back to ``goals`` minus the
    ``extratime`` goals, and stays unknown if that is not available either.
    Awarded, walkover and abandoned matches (``NO_SCORE_STATUSES``) have no played
    result, so both goals stay unknown.  Matches in any other state use ``goals``
    (the live score while running).
    """

    if status_short in NO_SCORE_STATUSES:
        return None, None
    fulltime = _obj(score.get("fulltime"))
    home, away = parse_int(fulltime.get("home")), parse_int(fulltime.get("away"))
    if home is not None and away is not None:
        return home, away
    if status_short in ("AET", "PEN"):
        extra = _obj(score.get("extratime"))
        total_h, total_a = parse_int(goals.get("home")), parse_int(goals.get("away"))
        extra_h, extra_a = parse_int(extra.get("home")), parse_int(extra.get("away"))
        if None not in (total_h, total_a, extra_h, extra_a):
            return total_h - extra_h, total_a - extra_a
        return None, None
    return parse_int(goals.get("home")), parse_int(goals.get("away"))


def parse_directory_fixture(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    """One ``/fixtures`` object as a ``fixtures`` row; ``None`` if it cannot be keyed."""

    fixture, league, teams = _obj(obj.get("fixture")), _obj(obj.get("league")), _obj(obj.get("teams"))
    fixture_id = parse_int(fixture.get("id"))
    kickoff = parse_kickoff_utc(fixture)
    league_id = parse_int(league.get("id"))
    if fixture_id is None or kickoff is None or league_id is None:
        return None
    status = _obj(fixture.get("status"))
    home, away = _obj(teams.get("home")), _obj(teams.get("away"))
    goals, score = _obj(obj.get("goals")), _obj(obj.get("score"))
    halftime, extratime, penalty = (_obj(score.get(k)) for k in ("halftime", "extratime", "penalty"))
    venue = _obj(fixture.get("venue"))
    status_short = _text(status.get("short"))
    reg_home, reg_away = regular_time_score(status_short, goals, score)
    return {
        "fixture_id": fixture_id,
        "league_id": league_id,
        "league_name": _text(league.get("name")),
        "country": _text(league.get("country")),
        "season": parse_int(league.get("season")),
        "round": _text(league.get("round")),
        "kickoff_utc": kickoff,
        "status_short": status_short,
        "status_long": _text(status.get("long")),
        "elapsed": parse_int(status.get("elapsed")),
        "status_extra": parse_int(status.get("extra")),
        "home_team_id": parse_int(home.get("id")),
        "home_team": _text(home.get("name")),
        "away_team_id": parse_int(away.get("id")),
        "away_team": _text(away.get("name")),
        "home_goals": reg_home,
        "away_goals": reg_away,
        "total_home_goals": parse_int(goals.get("home")),
        "total_away_goals": parse_int(goals.get("away")),
        "ht_home_goals": parse_int(halftime.get("home")),
        "ht_away_goals": parse_int(halftime.get("away")),
        "et_home_goals": parse_int(extratime.get("home")),
        "et_away_goals": parse_int(extratime.get("away")),
        "pen_home": parse_int(penalty.get("home")),
        "pen_away": parse_int(penalty.get("away")),
        "venue_id": parse_int(venue.get("id")),
        "venue_name": _text(venue.get("name")),
        "venue_city": _text(venue.get("city")),
        "referee": _text(fixture.get("referee")),
    }


def state_hash(row: Mapping[str, Any]) -> str:
    """Fingerprint of every provider-supplied column; equal hash = nothing to archive."""

    payload = json.dumps([row[c] for c in FIXTURE_COLUMNS], ensure_ascii=False, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


_CARD_EVENT_COLUMNS = {
    "yellow card": "yellow_cards_events",
    "red card": "red_cards_events",
    "second yellow card": "second_yellow_events",
}


def _elapsed(event: Mapping[str, Any]) -> int | None:
    return parse_int(_obj(event.get("time")).get("elapsed"))


def _card_event_counts(obj: Mapping[str, Any]) -> tuple[dict[int, dict[str, int]] | None, int]:
    """Cards per team id counted from the events list, and the number of events.

    Only events of the regular 90 minutes count (extra-time events carry a minute
    above 90; bookmakers settle cards on the 90 minutes).  ``None`` counts unless
    the log is demonstrably complete: a match without events, or with a log that
    holds nothing but goals (the provider's thin coverage of lower leagues, where
    a 4-goal log says nothing about cards), gives no basis for claiming zero cards.
    A complete log always contains substitutions, so at least one regular-time
    substitution is the completeness test; with it, a team without card events
    really had none.  ``events_count`` (all events, extra time included) is
    returned either way.
    """

    events = [e for e in _list(obj.get("events")) if isinstance(e, dict)]
    n_events = len(events)
    regular = [e for e in events if (_elapsed(e) or 0) <= REGULAR_TIME_MINUTES]
    if not any(str(e.get("type") or "").lower() == "subst" for e in regular):
        return None, n_events
    counts: dict[int, dict[str, int]] = {}
    for event in regular:
        if str(event.get("type") or "").lower() != "card":
            continue
        column = _CARD_EVENT_COLUMNS.get(str(event.get("detail") or "").strip().lower())
        team_id = parse_int(_obj(event.get("team")).get("id"))
        if column is None or team_id is None:
            continue
        counts.setdefault(team_id, {}).setdefault(column, 0)
        counts[team_id][column] += 1
    return counts, n_events


def has_stat_data(row: Mapping[str, Any]) -> bool:
    """True if the row carries any value from the provider's statistics block."""

    return any(row.get(c) is not None for c in _STAT_VALUE_COLUMNS)


def parse_fixture_stats(obj: Mapping[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    """``fixture_stats`` rows of one ``/fixtures?ids`` object.

    ``corners``/``yellow_cards``/``red_cards``/``shots``/``shots_on_target`` come from
    the provider statistics block and stay ``None`` where the provider sent nothing
    (the provider often sends ``null`` for "none", which is *not* turned into zero).

    The ``*_events`` columns count regular-time cards from the event list and are
    ``None`` unless the log is complete (see :func:`_card_event_counts`), so they can
    settle a card leg where the statistics block is silent; ``events_count`` is the
    number of events of the match, goals and substitutions included.  Where a match
    has a complete log but no statistics block, a statistics-free row per team is
    still emitted so the card counts are kept.

    Matches that went to extra time (``EXTRA_TIME_STATUSES``) get no statistics-block
    values: the provider's totals include the extra time, and the 90-minute numbers
    cannot be recovered.  Their columns stay ``NULL``.
    """

    fixture_id = parse_int(_obj(obj.get("fixture")).get("id"))
    if fixture_id is None:
        return []
    teams = _obj(obj.get("teams"))
    home_id, away_id = parse_int(_obj(teams.get("home")).get("id")), parse_int(_obj(teams.get("away")).get("id"))
    events, n_events = _card_event_counts(obj)
    status = _text(_obj(_obj(obj.get("fixture")).get("status")).get("short"))
    by_team: dict[int, dict[str, Any]] = {}
    for stat in [] if status in EXTRA_TIME_STATUSES else parse_team_match_stats(obj):
        by_team[stat["team_id"]] = {
            "side": stat.get("side"),
            "corners": stat.get("corners"),
            "yellow_cards": stat.get("yellow_cards"),
            "red_cards": stat.get("red_cards"),
            "shots": stat.get("total_shots"),
            "shots_on_target": stat.get("shots_on_goal"),
        }
    if events is not None:
        for team_id in (home_id, away_id):
            if team_id is not None and team_id not in by_team:
                by_team[team_id] = dict.fromkeys(_STAT_VALUE_COLUMNS)
    rows: list[dict[str, Any]] = []
    for team_id, values in by_team.items():
        row: dict[str, Any] = {
            "fixture_id": fixture_id,
            "team_id": team_id,
            "side": values.get("side") or team_side(team_id, home_id, away_id),
            **{c: values.get(c) for c in _STAT_VALUE_COLUMNS},
            "yellow_cards_events": None,
            "red_cards_events": None,
            "second_yellow_events": None,
            "events_count": n_events or None,
            "fetched_at": fetched_at,
        }
        if events is not None:
            per_team = events.get(team_id, {})
            for column in ("yellow_cards_events", "red_cards_events", "second_yellow_events"):
                row[column] = per_team.get(column, 0)
        rows.append(row)
    return rows


def parse_half_stats(
    payload_rows: Sequence[Mapping[str, Any]],
    fixture_id: int,
    fetched_at: str,
    home_id: int | None,
    away_id: int | None,
    status_short: str | None = None,
) -> list[dict[str, Any]]:
    """``fixture_stats_half`` rows from ``/fixtures/statistics?half=true`` (per team and half).

    For a match that went to extra time (``status_short`` in ``EXTRA_TIME_STATUSES``)
    only the first half is kept: the second-half figures may include the extra time.
    """

    halves = (("1h", "statistics_1h"),) if status_short in EXTRA_TIME_STATUSES else (
        ("1h", "statistics_1h"), ("2h", "statistics_2h"))
    rows: list[dict[str, Any]] = []
    for entry in payload_rows:
        entry = _obj(entry)
        team_id = parse_int(_obj(entry.get("team")).get("id"))
        if team_id is None:
            continue
        for half, key in halves:
            items = [i for i in _list(entry.get(key)) if isinstance(i, dict)]
            if not items:
                continue
            row: dict[str, Any] = dict.fromkeys(_HALF_COLUMNS)
            row.update(
                fixture_id=fixture_id,
                team_id=team_id,
                side=team_side(team_id, home_id, away_id),
                half=half,
                fetched_at=fetched_at,
            )
            for item in items:
                column = _HALF_STAT_TYPES.get(item.get("type"))
                if column is not None and row[column] is None:
                    value = parse_number(item.get("value"))
                    row[column] = None if value is None else int(value)
            rows.append(row)
    return rows


def plan_dates(
    now: datetime,
    last_fetch: Mapping[str, str],
    *,
    days_back: int = DEFAULT_DAYS_BACK,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    near_refresh_minutes: float = DEFAULT_NEAR_REFRESH_MINUTES,
    far_refresh_hours: float = DEFAULT_FAR_REFRESH_HOURS,
    settle_delay_hours: float = DEFAULT_SETTLE_DELAY_HOURS,
    force: bool = False,
) -> list[date]:
    """Dates to list this run, most useful first.

    ``last_fetch`` maps ``YYYY-MM-DD`` to the ISO time of that day's latest listing.
    Today is always listed (live scores).  Tomorrow is listed when its last listing
    is older than ``near_refresh_minutes``, later days when older than
    ``far_refresh_hours``.  A past day is listed until one listing was taken at
    least ``settle_delay_hours`` after its midnight end.  ``force`` lists all.
    """

    now = now.astimezone(timezone.utc)
    today = now.date()
    all_days = [today + timedelta(days=o) for o in range(-max(days_back, 0), max(days_ahead, 0) + 1)]
    if force:
        ordered = [today] + [today - timedelta(days=o) for o in range(1, days_back + 1)]
        return ordered + [d for d in all_days if d > today]

    def age_minutes(day: date) -> float | None:
        stamp = last_fetch.get(day.isoformat())
        return None if stamp is None else (now - _parse_utc(stamp)).total_seconds() / 60.0

    due: list[date] = [today]
    for back in range(1, max(days_back, 0) + 1):
        day = today - timedelta(days=back)
        stamp = last_fetch.get(day.isoformat())
        day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        if stamp is None or _parse_utc(stamp) < day_end + timedelta(hours=settle_delay_hours):
            due.append(day)
    for ahead in range(1, max(days_ahead, 0) + 1):
        day = today + timedelta(days=ahead)
        age = age_minutes(day)
        limit = near_refresh_minutes if ahead == 1 else far_refresh_hours * 60.0
        if age is None or age >= limit:
            due.append(day)
    return due


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@dataclass
class UpsertResult:
    listed: int = 0
    new: int = 0
    changed: int = 0
    raw_file: str | None = None


class FixtureDirectory:
    """SQLite + raw-file store of the fixture calendar and settlement statistics."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / DIRECTORY_DB
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            for table, column, sql_type in _MIGRATIONS:
                if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60)

    # ---- calendar -------------------------------------------------------

    def last_fetches(self) -> dict[str, str]:
        """``{list_date: latest fetched_at}`` over all recorded listings."""

        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT list_date, MAX(fetched_at) FROM directory_fetches GROUP BY list_date").fetchall()
        return dict(rows)

    def upsert_fixtures(
        self,
        run_id: str,
        raw_kind: str,
        raw_tag: str,
        fetched_at: datetime,
        objects: Sequence[Mapping[str, Any]],
        list_date: str | None = None,
        *,
        archive_raw: bool = True,
    ) -> UpsertResult:
        """Upsert fixture objects to their latest state; archive new or changed ones raw.

        A state older than the stored one (``fetched_at`` guard) never overwrites it,
        so two overlapping runs cannot move a fixture back in time.  Unchanged
        fixtures only get their ``fetched_at`` refreshed and are not written to the
        raw archive again; ``archive_raw=False`` skips the raw file (used when the
        caller archives the complete response itself).
        """

        fetched = _iso(fetched_at)
        by_id: dict[int, tuple[dict[str, Any], Mapping[str, Any]]] = {}
        for obj in objects:
            parsed = parse_directory_fixture(obj)
            if parsed is not None:
                by_id[parsed["fixture_id"]] = (parsed, obj)
        result = UpsertResult(listed=len(by_id))
        raw_records: list[Mapping[str, Any]] = []
        with closing(self._connect()) as conn:
            existing: dict[int, tuple[str | None, str]] = {}
            ids = list(by_id)
            for chunk in _chunks(ids, 500):
                marks = ",".join("?" * len(chunk))
                for fid, digest, stamp in conn.execute(
                    f"SELECT fixture_id, state_hash, fetched_at FROM fixtures WHERE fixture_id IN ({marks})", list(chunk)
                ):
                    existing[fid] = (digest, stamp)
            for fixture_id, (row, obj) in by_id.items():
                digest = state_hash(row)
                day = list_date or row["kickoff_utc"][:10]
                known = existing.get(fixture_id)
                if known is None:
                    result.new += 1
                    values = {**row, "list_date": day, "first_seen_at": fetched, "state_changed_at": fetched,
                              "fetched_at": fetched, "state_hash": digest}
                    cols = list(values)
                    conn.execute(
                        f"INSERT INTO fixtures ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [values[c] for c in cols],
                    )
                    raw_records.append({"fetched_at": fetched, "fixture": obj})
                    continue
                old_digest, old_fetched = known
                if old_fetched > fetched:
                    continue  # a newer state is already stored
                if old_digest == digest:
                    conn.execute("UPDATE fixtures SET fetched_at=?, list_date=? WHERE fixture_id=?",
                                 (fetched, day, fixture_id))
                    continue
                result.changed += 1
                assignments = ", ".join(f"{c}=?" for c in FIXTURE_COLUMNS[1:])
                conn.execute(
                    f"UPDATE fixtures SET {assignments}, list_date=?, state_changed_at=?, fetched_at=?, state_hash=? "
                    "WHERE fixture_id=?",
                    [row[c] for c in FIXTURE_COLUMNS[1:]] + [day, fetched, fetched, digest, fixture_id],
                )
                raw_records.append({"fetched_at": fetched, "fixture": obj})
            conn.commit()
        if raw_records and archive_raw:
            day_dir = fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
            path = self.root / "raw" / raw_kind / day_dir / f"{run_id}_{raw_tag}.jsonl.gz"
            result.raw_file = _write_raw(path, self.root, raw_records)
        return result

    def record_fetch(self, run_id: str, list_date: str, fetched_at: datetime, pages: int, result: UpsertResult) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO directory_fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, list_date, _iso(fetched_at), pages, result.listed, result.new, result.changed, result.raw_file),
            )
            conn.commit()

    # ---- settlement statistics ------------------------------------------

    def upsert_stats(self, rows: Sequence[Mapping[str, Any]]) -> None:
        with closing(self._connect()) as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO fixture_stats ({','.join(_STATS_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(_STATS_COLUMNS))})",
                [[row[c] for c in _STATS_COLUMNS] for row in rows],
            )
            conn.commit()

    def upsert_half_stats(self, rows: Sequence[Mapping[str, Any]]) -> None:
        with closing(self._connect()) as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO fixture_stats_half ({','.join(_HALF_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(_HALF_COLUMNS))})",
                [[row[c] for c in _HALF_COLUMNS] for row in rows],
            )
            conn.commit()

    def log_attempts(self, kind: str, fetched_at: datetime, teams_found: Mapping[int, int]) -> None:
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO stats_attempts VALUES (?, ?, ?, ?)",
                [(fid, kind, _iso(fetched_at), int(n)) for fid, n in teams_found.items()],
            )
            conn.commit()

    def _fixture_lookup(self, fixture_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        found: dict[int, dict[str, Any]] = {}
        with closing(self._connect()) as conn:
            for chunk in _chunks(list(fixture_ids), 500):
                marks = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT fixture_id, kickoff_utc, status_short, home_team_id, away_team_id "
                    f"FROM fixtures WHERE fixture_id IN ({marks})", list(chunk)
                ):
                    found[row[0]] = {"kickoff_utc": row[1], "status_short": row[2],
                                     "home_team_id": row[3], "away_team_id": row[4]}
        return found

    def _attempt_state(self, kind: str, fixture_ids: Sequence[int]) -> dict[int, tuple[int, str]]:
        """``{fixture_id: (empty attempts, latest attempt time)}`` for attempts without data."""

        state: dict[int, tuple[int, str]] = {}
        with closing(self._connect()) as conn:
            for chunk in _chunks(list(fixture_ids), 500):
                marks = ",".join("?" * len(chunk))
                for fid, n, last in conn.execute(
                    f"SELECT fixture_id, COUNT(*), MAX(fetched_at) FROM stats_attempts "
                    f"WHERE kind=? AND teams_found=0 AND fixture_id IN ({marks}) GROUP BY fixture_id",
                    [kind, *chunk],
                ):
                    state[fid] = (n, last)
        return state

    def ids_with_stats(self, fixture_ids: Sequence[int], *, halves: bool = False) -> set[int]:
        table = "fixture_stats_half" if halves else "fixture_stats"
        condition = "1" if halves else _has_stat_data_sql()
        have: set[int] = set()
        with closing(self._connect()) as conn:
            for chunk in _chunks(list(fixture_ids), 500):
                marks = ",".join("?" * len(chunk))
                have.update(r[0] for r in conn.execute(
                    f"SELECT DISTINCT fixture_id FROM {table} WHERE {condition} AND fixture_id IN ({marks})",
                    list(chunk)))
        return have

    def stats_candidates(
        self,
        fixture_ids: Sequence[int],
        now: datetime,
        *,
        halves: bool = False,
        min_age_hours: float = DEFAULT_STATS_MIN_AGE_HOURS,
        retry_hours: float = DEFAULT_STATS_RETRY_HOURS,
        max_attempts: int = MAX_STATS_ATTEMPTS,
        force: bool = False,
    ) -> list[int]:
        """Which of ``fixture_ids`` are worth a statistics request right now.

        Skipped: matches that already have the statistics (unless ``force``), matches
        the directory knows kicked off less than ``min_age_hours`` ago (still running
        or statistics not yet filled in), matches whose earlier attempts came back
        empty ``max_attempts`` times, and ones retried less than ``retry_hours``
        ago.  A match the directory does not know is tried, since its state is
        unknown.
        """

        ids = sorted({int(i) for i in fixture_ids})
        if force:
            return ids
        have = self.ids_with_stats(ids, halves=halves)
        lookup = self._fixture_lookup(ids)
        attempts = self._attempt_state("half" if halves else "details", ids)
        now = now.astimezone(timezone.utc)
        out: list[int] = []
        for fid in ids:
            if fid in have:
                continue
            known = lookup.get(fid)
            if known is not None and _parse_utc(known["kickoff_utc"]) > now - timedelta(hours=min_age_hours):
                continue
            n_empty, last = attempts.get(fid, (0, None))
            if n_empty >= max_attempts:
                continue
            if last is not None and _parse_utc(last) > now - timedelta(hours=retry_hours):
                continue
            out.append(fid)
        return out

    def pending_stats_ids(
        self,
        now: datetime,
        *,
        limit: int = 100,
        since_days: int = 7,
        min_age_hours: float = DEFAULT_STATS_MIN_AGE_HOURS,
        retry_hours: float = DEFAULT_STATS_RETRY_HOURS,
        max_attempts: int = MAX_STATS_ATTEMPTS,
    ) -> list[int]:
        """Finished fixtures of the last ``since_days`` days that still lack statistics, oldest first."""

        now = now.astimezone(timezone.utc)
        marks = ",".join("?" * len(FINISHED_STATUSES))
        with closing(self._connect()) as conn:
            ids = [r[0] for r in conn.execute(
                f"""SELECT f.fixture_id FROM fixtures f
                    WHERE f.status_short IN ({marks}) AND f.kickoff_utc >= ? AND f.kickoff_utc <= ?
                      AND NOT EXISTS (SELECT 1 FROM fixture_stats s WHERE s.fixture_id = f.fixture_id
                                      AND {_has_stat_data_sql('s')})
                    ORDER BY f.kickoff_utc""",
                [*sorted(FINISHED_STATUSES), _iso(now - timedelta(days=since_days)),
                 _iso(now - timedelta(hours=min_age_hours))],
            )]
        eligible = self.stats_candidates(ids, now, min_age_hours=min_age_hours, retry_hours=retry_hours,
                                         max_attempts=max_attempts)
        order = {fid: i for i, fid in enumerate(ids)}
        return sorted(eligible, key=order.__getitem__)[:limit]

    # ---- shared state of the collectors ---------------------------------

    def save_quota(self, remaining: int, seen_at: datetime) -> None:
        """Remember the latest daily-quota reading (``x-ratelimit-requests-remaining``)."""

        with closing(self._connect()) as conn:
            conn.execute("INSERT OR REPLACE INTO quota_memory VALUES (1, ?, ?)", (int(remaining), _iso(seen_at)))
            conn.commit()

    def load_quota(self, now: datetime) -> int | None:
        """The remembered quota reading, only if it was taken on the same UTC day as ``now``.

        The quota resets at 00:00 UTC and only falls during a day, so a reading
        from today is an upper bound of what is left; yesterday's says nothing.
        """

        with closing(self._connect()) as conn:
            row = conn.execute("SELECT remaining, seen_at FROM quota_memory WHERE id=1").fetchone()
        if row is None or _parse_utc(row[1]).date() != now.astimezone(timezone.utc).date():
            return None
        return int(row[0])

    def seed_budget(self, budget: RequestBudget, now: datetime) -> None:
        """Start ``budget`` from today's remembered quota, so a run below the reserve makes no request.

        Without this the budget only learns the quota from its first response, which
        would cost one request per run even when the day's quota is already spent.
        """

        remembered = self.load_quota(now)
        if remembered is not None and (budget.remaining_quota is None or remembered < budget.remaining_quota):
            budget.remaining_quota = remembered

    def log_probes(self, probed_at: datetime, rows: Sequence[tuple[int, int | None, bool]]) -> None:
        """Record ``(fixture_id, league_id, found)`` for fixtures asked from the odds feed."""

        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO odds_probes VALUES (?, ?, ?, ?)",
                [(fid, league, _iso(probed_at), int(found)) for fid, league, found in rows],
            )
            conn.commit()

    def probe_history(self, fixture_ids: Sequence[int]) -> dict[int, tuple[int, str]]:
        """``{fixture_id: (probes that found no odds, latest probe time)}``."""

        out: dict[int, tuple[int, str]] = {}
        with closing(self._connect()) as conn:
            for chunk in _chunks(list(fixture_ids), 500):
                marks = ",".join("?" * len(chunk))
                for fid, n, last in conn.execute(
                    f"SELECT fixture_id, SUM(1 - found), MAX(probed_at) FROM odds_probes "
                    f"WHERE fixture_id IN ({marks}) GROUP BY fixture_id", list(chunk)
                ):
                    out[fid] = (int(n or 0), last)
        return out

    def league_probe_stats(self, since: datetime) -> dict[int, tuple[int, int]]:
        """``{league_id: (probes, probes that found odds)}`` since ``since``."""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT league_id, COUNT(*), SUM(found) FROM odds_probes WHERE probed_at >= ? AND league_id IS NOT NULL "
                "GROUP BY league_id", (_iso(since),)
            ).fetchall()
        return {league: (int(n), int(found or 0)) for league, n, found in rows}

    def summary(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "fixtures": one("SELECT COUNT(*) FROM fixtures"),
                "leagues": one("SELECT COUNT(DISTINCT league_id) FROM fixtures"),
                "fixtures_with_stats": one(
                    f"SELECT COUNT(DISTINCT fixture_id) FROM fixture_stats WHERE {_has_stat_data_sql()}"),
                "fixtures_with_half_stats": one("SELECT COUNT(DISTINCT fixture_id) FROM fixture_stats_half"),
            }


# --------------------------------------------------------------------------
# Readers for the scanner (pandas frames)
# --------------------------------------------------------------------------


def _read_frame(db_path: str | Path, sql: str, params: Sequence[Any]) -> Any:
    import pandas as pd

    uri = "file:" + str(Path(db_path).resolve()).replace("\\", "/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        return pd.read_sql_query(sql, conn, params=list(params))


def load_fixtures(
    db_path: str | Path,
    *,
    kickoff_from: datetime | None = None,
    kickoff_to: datetime | None = None,
    fixture_ids: Sequence[int] | None = None,
    statuses: Iterable[str] | None = None,
    league_ids: Iterable[int] | None = None,
    full: bool = False,
) -> Any:
    """Fixtures as a DataFrame with the shared fixtures-frame columns.

    Contract columns: ``fixture_id, league_id, league_name, country, season, round,
    kickoff_utc, status_short, home_team_id, home_team, away_team_id, away_team,
    home_goals, away_goals`` (goals are the 90-minute score, NaN when unknown).
    ``full=True`` returns every stored column (status text, half-time and extra-time
    scores, venue, referee, ``fetched_at`` ...) instead.  Filters are ANDed;
    ``kickoff_from`` is inclusive and ``kickoff_to`` exclusive.
    """

    columns = "*" if full else ", ".join(FIXTURES_FRAME_COLUMNS)
    clauses: list[str] = []
    params: list[Any] = []
    if kickoff_from is not None:
        clauses.append("kickoff_utc >= ?")
        params.append(_iso(kickoff_from))
    if kickoff_to is not None:
        clauses.append("kickoff_utc < ?")
        params.append(_iso(kickoff_to))
    for column, values in (("fixture_id", fixture_ids), ("status_short", statuses), ("league_id", league_ids)):
        if values is not None:
            values = list(values)
            clauses.append(f"{column} IN ({','.join('?' * len(values))})" if values else "0")
            params.extend(values)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_frame(db_path, f"SELECT {columns} FROM fixtures{where} ORDER BY kickoff_utc, fixture_id", params)


def load_fixture_stats(db_path: str | Path, fixture_ids: Sequence[int] | None = None, *, halves: bool = False) -> Any:
    """``fixture_stats`` (or ``fixture_stats_half`` with ``halves=True``) as a DataFrame."""

    table = "fixture_stats_half" if halves else "fixture_stats"
    where, params = "", []
    if fixture_ids is not None:
        params = [int(i) for i in fixture_ids]
        where = f" WHERE fixture_id IN ({','.join('?' * len(params))})" if params else " WHERE 0"
    return _read_frame(db_path, f"SELECT * FROM {table}{where} ORDER BY fixture_id, team_id", params)


def read_pending_ids(db_path: str | Path, table: str | None = None) -> tuple[list[int], list[int]]:
    """Fixture ids to settle from a small SQLite file written by the caller.

    Reads ``table`` (default: ``pending_fixture_ids`` if present, otherwise the
    first table that has a ``fixture_id`` column).  An optional ``need_halves``
    column marks fixtures with first/second-half corner, card or shot legs.
    Returns ``(all ids, ids that need half statistics)``, sorted and unique.
    """

    uri = "file:" + str(Path(db_path).resolve()).replace("\\", "/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")]
        if table is None:
            table = "pending_fixture_ids" if "pending_fixture_ids" in names else next(
                (n for n in names if "fixture_id" in {c[1] for c in conn.execute(f'PRAGMA table_info("{n}")')}), None
            )
        if table is None or table not in names:
            raise ValueError(f"no table with a fixture_id column in {db_path}")
        columns = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')}
        if "fixture_id" not in columns:
            raise ValueError(f"table {table} has no fixture_id column")
        halves_sql = ", need_halves" if "need_halves" in columns else ", 0"
        rows = conn.execute(f'SELECT fixture_id{halves_sql} FROM "{table}" WHERE fixture_id IS NOT NULL').fetchall()
    ids = sorted({int(r[0]) for r in rows})
    halves = sorted({int(r[0]) for r in rows if r[1] not in (None, 0, "0", "")})
    return ids, halves


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------


@dataclass
class DirectoryReport:
    run_id: str
    requests: int = 0
    dates_fetched: list[str] = field(default_factory=list)
    dates_skipped: list[str] = field(default_factory=list)
    fixtures_listed: int = 0
    fixtures_new: int = 0
    fixtures_changed: int = 0
    stopped_reason: str = "completed"
    errors: list[str] = field(default_factory=list)


@dataclass
class DetailsReport:
    run_id: str
    requested: int = 0
    skipped: int = 0
    requests: int = 0
    fixtures_returned: int = 0
    fixtures_with_stats: int = 0
    fixtures_without_stats: int = 0
    stopped_reason: str = "completed"
    errors: list[str] = field(default_factory=list)


class FixtureDirectoryCollector:
    """Budget-aware calendar refresh and settlement-statistics download."""

    def __init__(
        self,
        client: HistoryHttpClient,
        directory: FixtureDirectory,
        budget: RequestBudget,
        *,
        sleep_between_requests: float = 0.0,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.directory = directory
        self.budget = budget
        self.sleep_between_requests = sleep_between_requests
        # ``fetched_at`` is read from this clock right after each response arrives.
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _get(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            payload, headers = self.client.get(endpoint, params)
        except Exception:
            self.budget.record({})  # a failed call still costs quota
            raise
        self.budget.record(headers)
        if self.budget.remaining_quota is not None:
            self.directory.save_quota(self.budget.remaining_quota, self.clock())
        if self.sleep_between_requests:
            time.sleep(self.sleep_between_requests)
        return payload

    # ---- calendar -------------------------------------------------------

    def refresh(
        self,
        now: datetime,
        *,
        days_back: int = DEFAULT_DAYS_BACK,
        days_ahead: int = DEFAULT_DAYS_AHEAD,
        near_refresh_minutes: float = DEFAULT_NEAR_REFRESH_MINUTES,
        far_refresh_hours: float = DEFAULT_FAR_REFRESH_HOURS,
        settle_delay_hours: float = DEFAULT_SETTLE_DELAY_HOURS,
        force: bool = False,
    ) -> DirectoryReport:
        """List the days that are due (see :func:`plan_dates`) and upsert every fixture."""

        run_id = _run_id(now)
        self.directory.seed_budget(self.budget, now)
        report = DirectoryReport(run_id=run_id)
        due = plan_dates(
            now, self.directory.last_fetches(), days_back=days_back, days_ahead=days_ahead,
            near_refresh_minutes=near_refresh_minutes, far_refresh_hours=far_refresh_hours,
            settle_delay_hours=settle_delay_hours, force=force,
        )
        window = {now.astimezone(timezone.utc).date() + timedelta(days=o) for o in range(-days_back, days_ahead + 1)}
        report.dates_skipped = sorted(d.isoformat() for d in window - set(due))
        consecutive_errors = 0
        for day in due:
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                break
            day_iso = day.isoformat()
            listing = self._list_day(day_iso, report)
            if listing is None:
                if report.stopped_reason == "budget_exhausted":
                    break
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped_reason = "api_error"
                    break
                continue
            consecutive_errors = 0
            objects, pages = listing
            fetched_at = self.clock()
            result = self.directory.upsert_fixtures(run_id, "fixtures", day_iso, fetched_at, objects, list_date=day_iso)
            self.directory.record_fetch(run_id, day_iso, fetched_at, pages, result)
            report.dates_fetched.append(day_iso)
            report.fixtures_listed += result.listed
            report.fixtures_new += result.new
            report.fixtures_changed += result.changed
        return report

    def _list_day(self, day_iso: str, report: DirectoryReport) -> tuple[list[Mapping[str, Any]], int] | None:
        """Every fixture of one UTC day (all pages); ``None`` on an API error or budget stop.

        A partial listing is discarded rather than upserted, so a day is either fully
        refreshed or left as it was.
        """

        objects: list[Mapping[str, Any]] = []
        page, total_pages = 1, 1
        while page <= total_pages:
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                return None
            params: dict[str, Any] = {"date": day_iso}
            if page > 1:  # /fixtures?date is normally one page; only ask for more if told to
                params["page"] = page
            report.requests += 1
            try:
                payload = self._get("/fixtures", params)
            except Exception as exc:  # noqa: BLE001 - one failing day must not stop the others
                report.errors.append(f"{day_iso}: {exc}")
                return None
            total_pages = int((payload.get("paging") or {}).get("total") or 1)
            objects.extend(o for o in _list(payload.get("response")) if isinstance(o, dict))
            page += 1
        return objects, total_pages

    # ---- settlement statistics ------------------------------------------

    def fetch_fixture_details(
        self,
        fixture_ids: Iterable[int],
        now: datetime | None = None,
        *,
        force: bool = False,
        min_age_hours: float = DEFAULT_STATS_MIN_AGE_HOURS,
        retry_hours: float = DEFAULT_STATS_RETRY_HOURS,
        max_attempts: int = MAX_STATS_ATTEMPTS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> DetailsReport:
        """Score and per-team statistics for finished fixtures via ``/fixtures?ids=a-b-c``.

        Up to ``batch_size`` (20, the provider maximum) ids per request.  Ids that
        already have statistics, ran too recently, or failed to yield statistics too
        often are skipped (see :meth:`FixtureDirectory.stats_candidates`; ``force``
        overrides).  Each returned fixture also refreshes its ``fixtures`` row, so the
        final score is up to date.  A match returned without any statistics is logged
        as an attempt and gets no ``fixture_stats`` row (missing stays missing).
        """

        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        requested = sorted({int(i) for i in fixture_ids})
        ids = self.directory.stats_candidates(
            requested, now, min_age_hours=min_age_hours, retry_hours=retry_hours,
            max_attempts=max_attempts, force=force,
        )
        run_id = _run_id(now)
        self.directory.seed_budget(self.budget, now)
        report = DetailsReport(run_id=run_id, requested=len(requested), skipped=len(requested) - len(ids))
        consecutive_errors = 0
        for seq, batch in enumerate(_chunks(ids, max(1, min(batch_size, DEFAULT_BATCH_SIZE))), start=1):
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                break
            report.requests += 1
            try:
                payload = self._get("/fixtures", {"ids": "-".join(str(i) for i in batch)})
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"batch {seq}: {exc}")
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped_reason = "api_error"
                    break
                continue
            consecutive_errors = 0
            fetched_at = self.clock()
            fetched = _iso(fetched_at)
            objects = [o for o in _list(payload.get("response")) if isinstance(o, dict)]
            # The complete response (statistics, events, lineups) is archived raw once;
            # the fixtures rows are refreshed without a second raw copy.
            path = self.directory.root / "raw" / "details" / fetched_at.strftime("%Y-%m-%d") / f"{run_id}_{seq:03d}.jsonl.gz"
            _write_raw(path, self.directory.root, [{"fetched_at": fetched, "fixture": o} for o in objects])
            self.directory.upsert_fixtures(run_id, "details", f"{seq:03d}", fetched_at, objects, archive_raw=False)
            teams_found: dict[int, int] = {fid: 0 for fid in batch}
            stat_rows: list[dict[str, Any]] = []
            for obj in objects:
                fid = parse_int(_obj(obj.get("fixture")).get("id"))
                if fid is None:
                    continue
                # Statistics of a match that is still running (or was interrupted) are partial and,
                # once stored, would never be fetched again: keep them out, retry after the match.
                finished = _text(_obj(_obj(obj.get("fixture")).get("status")).get("short")) in FINISHED_STATUSES
                rows = parse_fixture_stats(obj, fetched) if finished else []
                report.fixtures_returned += 1
                teams_found[fid] = sum(1 for r in rows if has_stat_data(r))
                stat_rows.extend(rows)
                if teams_found[fid]:
                    report.fixtures_with_stats += 1
                else:
                    report.fixtures_without_stats += 1
            if stat_rows:
                self.directory.upsert_stats(stat_rows)
            self.directory.log_attempts("details", fetched_at, teams_found)
        return report

    def fetch_half_stats(
        self,
        fixture_ids: Iterable[int],
        now: datetime | None = None,
        *,
        force: bool = False,
        min_age_hours: float = DEFAULT_STATS_MIN_AGE_HOURS,
        retry_hours: float = DEFAULT_STATS_RETRY_HOURS,
        max_attempts: int = MAX_STATS_ATTEMPTS,
    ) -> DetailsReport:
        """First- and second-half corners, cards and shots, one request per match.

        ``/fixtures/statistics?fixture=ID&half=true`` is the only source of half
        splits for corners and cards (the events list has no corners).  It costs a
        request per fixture, so pass only matches that have a 1H or 2H corner, card
        or shot leg to settle.  A match the directory does not know to be finished is
        skipped without a request (run :meth:`fetch_fixture_details` or :meth:`refresh`
        first); for one that went to extra time only the first half is kept.
        """

        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        requested = sorted({int(i) for i in fixture_ids})
        ids = self.directory.stats_candidates(
            requested, now, halves=True, min_age_hours=min_age_hours, retry_hours=retry_hours,
            max_attempts=max_attempts, force=force,
        )
        run_id = _run_id(now)
        self.directory.seed_budget(self.budget, now)
        report = DetailsReport(run_id=run_id, requested=len(requested), skipped=len(requested) - len(ids))
        lookup = self.directory._fixture_lookup(ids)
        # The statistics response carries no match status, so only a match the directory knows to be
        # finished is worth a request: a running one would leave partial half figures that are never refreshed.
        ids = [i for i in ids if lookup.get(i, {}).get("status_short") in FINISHED_STATUSES]
        report.skipped = len(requested) - len(ids)
        raw_records: list[Mapping[str, Any]] = []
        consecutive_errors = 0
        for fid in ids:
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                break
            report.requests += 1
            try:
                payload = self._get("/fixtures/statistics", {"fixture": fid, "half": "true"})
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"fixture {fid}: {exc}")
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped_reason = "api_error"
                    break
                continue
            consecutive_errors = 0
            fetched_at = self.clock()
            response = [r for r in _list(payload.get("response")) if isinstance(r, dict)]
            known = lookup.get(fid, {})
            rows = parse_half_stats(
                response, fid, _iso(fetched_at), known.get("home_team_id"), known.get("away_team_id"),
                known.get("status_short"),
            )
            raw_records.append({"fetched_at": _iso(fetched_at), "fixture_id": fid, "response": response})
            report.fixtures_returned += 1
            if rows:
                self.directory.upsert_half_stats(rows)
                report.fixtures_with_stats += 1
            else:
                report.fixtures_without_stats += 1
            self.directory.log_attempts("half", fetched_at, {fid: len({r["team_id"] for r in rows})})
        if raw_records:
            path = self.directory.root / "raw" / "half_stats" / now.strftime("%Y-%m-%d") / f"{run_id}.jsonl.gz"
            _write_raw(path, self.directory.root, raw_records)
        return report


__all__ = [
    "DIRECTORY_DB",
    "FINISHED_STATUSES",
    "TERMINAL_STATUSES",
    "LIVE_STATUSES",
    "UPCOMING_STATUSES",
    "EXTRA_TIME_STATUSES",
    "NO_SCORE_STATUSES",
    "FIXTURES_FRAME_COLUMNS",
    "DirectoryReport",
    "DetailsReport",
    "FixtureDirectory",
    "FixtureDirectoryCollector",
    "UpsertResult",
    "load_fixtures",
    "load_fixture_stats",
    "parse_directory_fixture",
    "has_stat_data",
    "parse_fixture_stats",
    "parse_half_stats",
    "plan_dates",
    "read_pending_ids",
    "regular_time_score",
]
