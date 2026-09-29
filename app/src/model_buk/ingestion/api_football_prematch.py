"""Prospective, point-in-time archive of what was known *before* kickoff.

Injuries, announced line-ups and the provider's own predictions are only
useful as model inputs if we know when we learned them.  The historical
archive cannot tell us that (it is built after the fact), and API-Football
does not keep timestamped pre-match snapshots, so this module records what
the feed shows *now* with the time we saw it.  Every row carries a
``fetched_at`` (taken after the response arrived, so it is a conservative
"known at" time) and a rule of thumb applies to all four record types:
anything first seen at or after kickoff is dropped, never stored as if it were
pre-match information.

One run (meant to be scheduled hourly) does, in this order, because line-ups
are the most time-critical and predictions can always wait for the next run:

1. ``/fixtures?date=`` for today and tomorrow (UTC): the upcoming fixtures of
   the tracked leagues (``NS``/``TBD``; finished, live and postponed fixtures
   are not new upcoming matches).  Only ``NS`` fixtures with a known kickoff
   are acted on below; ``TBD`` kickoffs are placeholders.
2. Line-ups: tracked fixtures kicking off within the next 0-100 minutes that
   have no complete line-up yet are fetched with ``/fixtures?ids=a-b-c`` (up to
   20 ids per call).  A team's line-up is stored once, at the first fetch that
   shows a starting XI for it, with ``minutes_before_kickoff``; an empty answer
   is logged in ``lineup_attempts`` and retried on the next run.  With an
   hourly cadence the capture therefore lands roughly 5-100 minutes before
   kickoff (line-ups are normally published about an hour ahead, so a typical
   capture is 5-60 minutes before the whistle).  A fixture whose line-up is
   still unpublished at the last run before kickoff is simply not captured, so
   running every 20-30 minutes instead of hourly raises the capture rate at a
   small request cost (everything else in a run is stored once).
3. Injuries: ``/injuries?date=`` for the same two dates (all pages).  Per
   ``(fixture_id, player_id, type, reason)`` the row keeps ``first_seen_at``
   and ``last_seen_at`` plus the fixture's ``kickoff_utc``, so "reported
   before kickoff" is ``first_seen_at < kickoff_utc``.  A change of ``type``
   or ``reason`` (for example *Questionable* -> *Missing Fixture*) adds a row
   instead of overwriting, so the earlier report stays dated.  Entries for
   fixtures that have already kicked off are ignored.
4. Predictions: ``/predictions?fixture=`` once per tracked fixture kicking off
   within the next 30 hours.  These are a *benchmark* to compare our model
   against, not a feature; the provider's blend is opaque.  An empty answer is
   retried on later runs up to :data:`MAX_PREDICTION_ATTEMPTS` times.

Storage layout under ``root``::

    raw/{kind}/{YYYY-MM-DD}/{run_id}.jsonl.gz   # immutable provider objects + fetched_at
    prematch.sqlite3                             # runs, fixtures, injuries, lineups, predictions

``kind`` is ``fixtures``, ``lineups``, ``injuries`` (only entries that are new
or changed since the previous snapshot) or ``predictions``.  Raw files are
never overwritten: a second write to the same path raises
:class:`FileExistsError`.  A provider ``null`` or missing field is stored as
SQL ``NULL``, never as zero.

The daily quota is shared with other collectors, so a run works under a
:class:`~model_buk.ingestion.api_football_history.RequestBudget` with a
reserve floor and a per-run cap.  A failing endpoint does not stop the others;
the run reports ``api_error`` and lists what failed.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from model_buk.ingestion.api_football_history import (
    DEFAULT_BATCH_SIZE,
    HistoryHttpClient,
    RequestBudget,
    all_planned_league_seasons,
)
from model_buk.ingestion.api_football_tables import (
    parse_flag,
    parse_float,
    parse_int,
    parse_kickoff_utc,
    parse_lineup_players,
    parse_lineups,
)

# The leagues of the historical archive, so pre-match data joins on fixture_id.
TRACKED_LEAGUE_IDS: frozenset[int] = frozenset(ls.league_id for ls in all_planned_league_seasons())

# Statuses of a fixture that has not started; only NS has a reliable kickoff.
UPCOMING_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})
LINEUP_WINDOW_MINUTES = 100
PREDICTION_WINDOW_HOURS = 30
MAX_PREDICTION_ATTEMPTS = 3
# Stop a step (not the run) after this many failed calls in a row.
MAX_CONSECUTIVE_ERRORS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    dates TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    fixtures_seen INTEGER NOT NULL DEFAULT 0,
    injuries_new INTEGER NOT NULL DEFAULT 0,
    injuries_seen INTEGER NOT NULL DEFAULT 0,
    lineups_tried INTEGER NOT NULL DEFAULT 0,
    lineups_captured INTEGER NOT NULL DEFAULT 0,
    predictions_stored INTEGER NOT NULL DEFAULT 0,
    stopped_reason TEXT,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    season INTEGER,
    round TEXT,
    kickoff_utc TEXT NOT NULL,
    status_short TEXT,
    home_team_id INTEGER,
    home_team TEXT,
    away_team_id INTEGER,
    away_team TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prematch_fixtures_kickoff ON fixtures (kickoff_utc);
CREATE TABLE IF NOT EXISTS injuries (
    injury_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    league_id INTEGER,
    team_id INTEGER,
    player_id INTEGER NOT NULL,
    player_name TEXT,
    type TEXT,
    reason TEXT,
    kickoff_utc TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_injuries_key ON injuries (fixture_id, player_id);
CREATE TABLE IF NOT EXISTS lineup_attempts (
    fixture_id INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    minutes_before_kickoff REAL,
    teams_found INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lineup_attempts_fixture ON lineup_attempts (fixture_id);
CREATE TABLE IF NOT EXISTS lineups (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    formation TEXT,
    coach_id INTEGER,
    coach_name TEXT,
    fetched_at TEXT NOT NULL,
    minutes_before_kickoff REAL,
    raw_file TEXT,
    PRIMARY KEY (fixture_id, team_id)
);
CREATE TABLE IF NOT EXISTS lineup_players (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER,
    player_name TEXT,
    number INTEGER,
    position TEXT,
    grid TEXT,
    is_starter INTEGER NOT NULL,
    formation TEXT,
    fetched_at TEXT NOT NULL,
    minutes_before_kickoff REAL
);
CREATE INDEX IF NOT EXISTS idx_lineup_players_fixture ON lineup_players (fixture_id, team_id);
CREATE TABLE IF NOT EXISTS prediction_attempts (
    fixture_id INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prediction_attempts_fixture ON prediction_attempts (fixture_id);
CREATE TABLE IF NOT EXISTS predictions (
    fixture_id INTEGER PRIMARY KEY,
    kickoff_utc TEXT,
    fetched_at TEXT NOT NULL,
    minutes_before_kickoff REAL,
    percent_home REAL,
    percent_draw REAL,
    percent_away REAL,
    advice TEXT,
    under_over TEXT,
    goals_home REAL,
    goals_away REAL,
    winner_team_id INTEGER,
    winner_comment TEXT,
    win_or_draw INTEGER,
    raw_file TEXT
);
"""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    """Non-blank text or ``None``; an empty provider string carries no information."""

    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _minutes_before(kickoff_utc: str, fetched_at: datetime) -> float:
    """Minutes from ``fetched_at`` to kickoff (positive = before kickoff), to 0.01 minute."""

    return round((_parse_utc(kickoff_utc) - fetched_at).total_seconds() / 60.0, 2)


def fixture_dates(now: datetime, days_ahead: int = 1) -> list[date]:
    """Dates to list: today (UTC) through ``days_ahead`` days later."""

    today = now.astimezone(timezone.utc).date()
    return [today + timedelta(days=offset) for offset in range(max(days_ahead, 0) + 1)]


# --------------------------------------------------------------------------
# Pure parsers: one provider object in, one plain row (or None) out
# --------------------------------------------------------------------------


def parse_listed_fixture(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    """Identity, kickoff and status of a ``/fixtures`` object; ``None`` if it cannot be keyed."""

    fixture, league, teams = _obj(obj.get("fixture")), _obj(obj.get("league")), _obj(obj.get("teams"))
    fixture_id = parse_int(fixture.get("id"))
    kickoff = parse_kickoff_utc(fixture)
    league_id = parse_int(league.get("id"))
    if fixture_id is None or kickoff is None or league_id is None:
        return None
    home, away = _obj(teams.get("home")), _obj(teams.get("away"))
    return {
        "fixture_id": fixture_id,
        "league_id": league_id,
        "season": parse_int(league.get("season")),
        "round": _text(league.get("round")),
        "kickoff_utc": kickoff,
        "status_short": _text(_obj(fixture.get("status")).get("short")),
        "home_team_id": parse_int(home.get("id")),
        "home_team": _text(home.get("name")),
        "away_team_id": parse_int(away.get("id")),
        "away_team": _text(away.get("name")),
    }


def parse_injury(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    """One ``/injuries`` entry; ``None`` without a fixture, player or kickoff to key it on."""

    player, team, fixture, league = (_obj(obj.get(k)) for k in ("player", "team", "fixture", "league"))
    fixture_id = parse_int(fixture.get("id"))
    player_id = parse_int(player.get("id"))
    kickoff = parse_kickoff_utc(fixture)
    if fixture_id is None or player_id is None or kickoff is None:
        return None
    return {
        "fixture_id": fixture_id,
        "league_id": parse_int(league.get("id")),
        "team_id": parse_int(team.get("id")),
        "player_id": player_id,
        "player_name": _text(player.get("name")),
        "type": _text(player.get("type")),
        "reason": _text(player.get("reason")),
        "kickoff_utc": kickoff,
    }


def parse_prediction(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    """The ``predictions`` block of a ``/predictions`` object; ``None`` if it has none.

    Percentages are kept on the provider's 0-100 scale.  ``goals_*`` and
    ``under_over`` keep the provider's sign convention (for example ``"-1.5"``);
    they are not interpreted here.
    """

    block = _obj(obj.get("predictions"))
    if not block:
        return None
    percent, goals, winner = _obj(block.get("percent")), _obj(block.get("goals")), _obj(block.get("winner"))
    return {
        "percent_home": parse_float(percent.get("home")),
        "percent_draw": parse_float(percent.get("draw")),
        "percent_away": parse_float(percent.get("away")),
        "advice": _text(block.get("advice")),
        "under_over": _text(block.get("under_over")),
        "goals_home": parse_float(goals.get("home")),
        "goals_away": parse_float(goals.get("away")),
        "winner_team_id": parse_int(winner.get("id")),
        "winner_comment": _text(winner.get("comment")),
        "win_or_draw": parse_flag(block.get("win_or_draw")),
    }


def parse_lineup_blocks(obj: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Per team with at least one starter: ``{team_id: {"team": row, "players": [rows]}}``.

    A team block without a starting XI is not a published line-up and is left
    out, so an empty ``lineups`` list (the usual state well before kickoff)
    yields ``{}``.
    """

    players = parse_lineup_players(obj)
    blocks: dict[int, dict[str, Any]] = {}
    for team in parse_lineups(obj):
        rows = [p for p in players if p["team_id"] == team["team_id"]]
        if any(p["is_starter"] == 1 for p in rows):
            blocks[team["team_id"]] = {"team": team, "players": rows}
    return blocks


# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------


@dataclass
class PrematchRunReport:
    run_id: str
    dates: list[str] = field(default_factory=list)
    requests: int = 0
    fixtures_seen: int = 0
    injuries_new: int = 0
    injuries_seen: int = 0
    lineups_tried: int = 0
    lineups_captured: int = 0
    predictions_stored: int = 0
    stopped_reason: str = "completed"
    errors: list[str] = field(default_factory=list)


class PrematchArchive:
    """SQLite + raw-file store for timestamped pre-match information."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "prematch.sqlite3"
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60)

    # -- raw ---------------------------------------------------------------

    def write_raw(self, kind: str, run_id: str, moment: datetime, records: Sequence[Mapping[str, Any]]) -> str:
        """Write one immutable ``raw/{kind}/{day}/{run_id}.jsonl.gz``; refuse to overwrite."""

        day_dir = self.root / "raw" / kind / moment.astimezone(timezone.utc).strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{run_id}.jsonl.gz"
        if path.exists():
            raise FileExistsError(f"raw snapshot {path} already exists; raw archives are append-only")
        lines = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
        with path.open("xb") as handle:
            handle.write(gzip.compress(lines.encode("utf-8")))
        return path.relative_to(self.root).as_posix()

    # -- runs --------------------------------------------------------------

    def start_run(self, run_id: str, started_at: datetime, dates: Sequence[str]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, dates) VALUES (?, ?, ?)",
                (run_id, _iso(started_at), ",".join(dates)),
            )
            conn.commit()

    def finish_run(self, report: PrematchRunReport, finished_at: datetime) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE runs SET finished_at=?, requests=?, fixtures_seen=?, injuries_new=?, injuries_seen=?,
                          lineups_tried=?, lineups_captured=?, predictions_stored=?, stopped_reason=?, errors=?
                   WHERE run_id=?""",
                (_iso(finished_at), report.requests, report.fixtures_seen, report.injuries_new,
                 report.injuries_seen, report.lineups_tried, report.lineups_captured, report.predictions_stored,
                 report.stopped_reason, "; ".join(report.errors) or None, report.run_id),
            )
            conn.commit()

    # -- fixtures ----------------------------------------------------------

    def known_fixture_ids(self) -> set[int]:
        with closing(self._connect()) as conn:
            return {row[0] for row in conn.execute("SELECT fixture_id FROM fixtures")}

    def upsert_fixtures(self, rows: Sequence[tuple[datetime, Mapping[str, Any]]]) -> None:
        """Insert new fixtures and refresh kickoff/status of known ones; ``first_seen_at`` never moves."""

        with closing(self._connect()) as conn:
            for fetched_at, row in rows:
                seen = _iso(fetched_at)
                conn.execute(
                    """INSERT INTO fixtures (fixture_id, league_id, season, round, kickoff_utc, status_short,
                                             home_team_id, home_team, away_team_id, away_team,
                                             first_seen_at, last_seen_at)
                       VALUES (:fixture_id, :league_id, :season, :round, :kickoff_utc, :status_short,
                               :home_team_id, :home_team, :away_team_id, :away_team, :seen, :seen)
                       ON CONFLICT(fixture_id) DO UPDATE SET
                         league_id=excluded.league_id, season=excluded.season, round=excluded.round,
                         kickoff_utc=excluded.kickoff_utc, status_short=excluded.status_short,
                         home_team_id=excluded.home_team_id, home_team=excluded.home_team,
                         away_team_id=excluded.away_team_id, away_team=excluded.away_team,
                         last_seen_at=excluded.last_seen_at""",
                    {**row, "seen": seen},
                )
            conn.commit()

    def lineup_candidates(self, now: datetime, listed_since: datetime, window_minutes: int) -> list[dict[str, Any]]:
        """``NS`` fixtures listed in this run that kick off within the window and lack a complete line-up."""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT f.fixture_id, f.kickoff_utc FROM fixtures f
                   WHERE f.status_short='NS' AND f.last_seen_at>=? AND f.kickoff_utc>? AND f.kickoff_utc<=?
                     AND (SELECT COUNT(*) FROM lineups l WHERE l.fixture_id=f.fixture_id) < 2
                   ORDER BY f.kickoff_utc, f.fixture_id""",
                (_iso(listed_since), _iso(now), _iso(now + timedelta(minutes=window_minutes))),
            ).fetchall()
        return [{"fixture_id": r[0], "kickoff_utc": r[1]} for r in rows]

    def prediction_candidates(self, now: datetime, listed_since: datetime, window_hours: int) -> list[dict[str, Any]]:
        """``NS`` fixtures listed in this run within the window with no stored or exhausted prediction."""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT f.fixture_id, f.kickoff_utc FROM fixtures f
                   WHERE f.status_short='NS' AND f.last_seen_at>=? AND f.kickoff_utc>? AND f.kickoff_utc<=?
                     AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.fixture_id=f.fixture_id)
                     AND (SELECT COUNT(*) FROM prediction_attempts a
                          WHERE a.fixture_id=f.fixture_id AND a.ok=0) < ?
                   ORDER BY f.kickoff_utc, f.fixture_id""",
                (_iso(listed_since), _iso(now), _iso(now + timedelta(hours=window_hours)),
                 MAX_PREDICTION_ATTEMPTS),
            ).fetchall()
        return [{"fixture_id": r[0], "kickoff_utc": r[1]} for r in rows]

    # -- injuries ----------------------------------------------------------

    def _find_injury(self, conn: sqlite3.Connection, row: Mapping[str, Any]) -> tuple | None:
        """The stored state matching (fixture, player, type, reason); ``IS`` so a NULL type/reason matches."""

        return conn.execute(
            """SELECT injury_id FROM injuries
               WHERE fixture_id=? AND player_id=? AND type IS ? AND reason IS ?""",
            (row["fixture_id"], row["player_id"], row["type"], row["reason"]),
        ).fetchone()

    def unseen_injuries(self, rows: Sequence[Mapping[str, Any]]) -> list[int]:
        """Positions in ``rows`` whose (fixture, player, type, reason) is not stored yet."""

        with closing(self._connect()) as conn:
            return [i for i, row in enumerate(rows) if self._find_injury(conn, row) is None]

    def record_injuries(self, rows: Sequence[tuple[datetime, Mapping[str, Any]]]) -> tuple[int, int]:
        """Insert new injury states, refresh ``last_seen_at`` of known ones; return (new, seen)."""

        new = seen = 0
        with closing(self._connect()) as conn:
            for fetched_at, row in rows:
                stamp = _iso(fetched_at)
                existing = self._find_injury(conn, row)
                if existing is None:
                    conn.execute(
                        """INSERT INTO injuries (fixture_id, league_id, team_id, player_id, player_name, type,
                                                 reason, kickoff_utc, first_seen_at, last_seen_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row["fixture_id"], row["league_id"], row["team_id"], row["player_id"], row["player_name"],
                         row["type"], row["reason"], row["kickoff_utc"], stamp, stamp),
                    )
                    new += 1
                else:
                    conn.execute(
                        "UPDATE injuries SET last_seen_at=?, kickoff_utc=?, player_name=COALESCE(?, player_name) "
                        "WHERE injury_id=?",
                        (stamp, row["kickoff_utc"], row["player_name"], existing[0]),
                    )
                    seen += 1
            conn.commit()
        return new, seen

    def injuries_as_of(self, fixture_id: int, as_of: datetime) -> list[dict[str, Any]]:
        """Injury reports of a fixture first seen at or before ``as_of``.

        A row is a report *state*; ``last_seen_at`` tells whether it was still
        listed later.  Nothing first seen after ``as_of`` can leak in.
        """

        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM injuries WHERE fixture_id=? AND first_seen_at<=? ORDER BY first_seen_at, injury_id",
                (fixture_id, _iso(as_of)),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- lineups -----------------------------------------------------------

    def captured_lineup_teams(self, fixture_id: int) -> set[int]:
        with closing(self._connect()) as conn:
            return {row[0] for row in conn.execute("SELECT team_id FROM lineups WHERE fixture_id=?", (fixture_id,))}

    def record_lineup_attempt(
        self, fixture_id: int, fetched_at: datetime, minutes_before_kickoff: float | None, teams_found: int
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO lineup_attempts VALUES (?, ?, ?, ?)",
                (fixture_id, _iso(fetched_at), minutes_before_kickoff, teams_found),
            )
            conn.commit()

    def record_lineup_team(
        self,
        fixture_id: int,
        block: Mapping[str, Any],
        fetched_at: datetime,
        minutes_before_kickoff: float,
        raw_file: str | None,
    ) -> None:
        """Store one team's line-up once; a team already stored is left untouched."""

        team, players = block["team"], block["players"]
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO lineups VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fixture_id, team["team_id"], team["formation"], team["coach_id"], team["coach_name"],
                 _iso(fetched_at), minutes_before_kickoff, raw_file),
            )
            if cursor.rowcount:
                conn.executemany(
                    "INSERT INTO lineup_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (fixture_id, team["team_id"], p["player_id"], p["player_name"], p["number"], p["position"],
                         p["grid"], p["is_starter"], team["formation"], _iso(fetched_at), minutes_before_kickoff)
                        for p in players
                    ],
                )
            conn.commit()

    def lineup_as_of(self, fixture_id: int, as_of: datetime) -> list[dict[str, Any]]:
        """Line-up rows fetched at or before ``as_of`` (a later fetch can never leak in)."""

        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM lineup_players WHERE fixture_id=? AND fetched_at<=?
                   ORDER BY team_id, is_starter DESC, number""",
                (fixture_id, _iso(as_of)),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- predictions -------------------------------------------------------

    def record_prediction_attempt(self, fixture_id: int, fetched_at: datetime, ok: bool) -> None:
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO prediction_attempts VALUES (?, ?, ?)", (fixture_id, _iso(fetched_at), int(ok)))
            conn.commit()

    def record_prediction(
        self,
        fixture_id: int,
        kickoff_utc: str,
        parsed: Mapping[str, Any],
        fetched_at: datetime,
        minutes_before_kickoff: float,
        raw_file: str | None,
    ) -> bool:
        """Store the prediction once per fixture; ``False`` if one is already stored."""

        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO predictions
                   (fixture_id, kickoff_utc, fetched_at, minutes_before_kickoff, percent_home, percent_draw,
                    percent_away, advice, under_over, goals_home, goals_away, winner_team_id, winner_comment,
                    win_or_draw, raw_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fixture_id, kickoff_utc, _iso(fetched_at), minutes_before_kickoff, parsed["percent_home"],
                 parsed["percent_draw"], parsed["percent_away"], parsed["advice"], parsed["under_over"],
                 parsed["goals_home"], parsed["goals_away"], parsed["winner_team_id"], parsed["winner_comment"],
                 parsed["win_or_draw"], raw_file),
            )
            conn.commit()
            return bool(cursor.rowcount)

    def prediction_as_of(self, fixture_id: int, as_of: datetime) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM predictions WHERE fixture_id=? AND fetched_at<=?", (fixture_id, _iso(as_of))
            ).fetchone()
        return dict(row) if row else None

    def summary(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "runs": one("SELECT COUNT(*) FROM runs"),
                "fixtures": one("SELECT COUNT(*) FROM fixtures"),
                "injuries": one("SELECT COUNT(*) FROM injuries"),
                "lineup_teams": one("SELECT COUNT(*) FROM lineups"),
                "lineup_fixtures": one("SELECT COUNT(DISTINCT fixture_id) FROM lineups"),
                "predictions": one("SELECT COUNT(*) FROM predictions"),
            }


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------


class _BudgetStop(Exception):
    """The request budget refused another call."""


class _ApiFailure(Exception):
    """A call failed (after the client's own retries)."""


class PrematchCollector:
    """One budget-aware pre-match run: fixtures, line-ups, injuries, predictions.

    ``clock`` supplies the time a response was received; it is what
    ``fetched_at`` and ``minutes_before_kickoff`` are based on.  ``now`` given
    to :meth:`run` only fixes the windows and the run id.
    """

    def __init__(
        self,
        client: HistoryHttpClient,
        archive: PrematchArchive,
        budget: RequestBudget,
        *,
        league_ids: Iterable[int] | None = TRACKED_LEAGUE_IDS,
        days_ahead: int = 1,
        lineup_window_minutes: int = LINEUP_WINDOW_MINUTES,
        prediction_window_hours: int = PREDICTION_WINDOW_HOURS,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.archive = archive
        self.budget = budget
        self.league_ids = None if league_ids is None else frozenset(league_ids)
        self.days_ahead = days_ahead
        self.lineup_window_minutes = lineup_window_minutes
        self.prediction_window_hours = prediction_window_hours
        self.clock = clock or _utc_now

    # -- plumbing ----------------------------------------------------------

    def _tracked(self, league_id: int | None) -> bool:
        return league_id is not None and (self.league_ids is None or league_id in self.league_ids)

    def _call(self, report: PrematchRunReport, endpoint: str, params: Mapping[str, Any]) -> tuple[Mapping[str, Any], datetime]:
        """One budgeted request; returns the payload and the time it arrived."""

        if not self.budget.allow():
            raise _BudgetStop()
        try:
            payload, headers = self.client.get(endpoint, params)
        except Exception as exc:  # the client has already retried; a failed call still cost quota
            self.budget.record({})
            report.requests += 1
            raise _ApiFailure(f"{endpoint} {dict(params)}: {str(exc)[:200]}") from exc
        fetched_at = self.clock()
        self.budget.record(headers)
        report.requests += 1
        return payload, fetched_at

    # -- run ---------------------------------------------------------------

    def run(self, now: datetime) -> PrematchRunReport:
        now = now.astimezone(timezone.utc)
        run_id = now.strftime("%Y%m%dT%H%M%SZ")
        dates = [d.isoformat() for d in fixture_dates(now, self.days_ahead)]
        report = PrematchRunReport(run_id=run_id, dates=dates)
        self.archive.start_run(run_id, now, dates)

        steps = (
            ("fixtures", lambda: self._step_fixtures(report, now, dates)),
            ("lineups", lambda: self._step_lineups(report, now)),
            ("injuries", lambda: self._step_injuries(report, now, dates)),
            ("predictions", lambda: self._step_predictions(report, now)),
        )
        budget_stop = False
        for name, step in steps:
            if budget_stop:
                break
            try:
                step()
            except _BudgetStop:
                budget_stop = True
            except _ApiFailure as exc:
                report.errors.append(f"{name}: {exc}")
        if budget_stop:
            report.stopped_reason = "budget_exhausted"
        elif report.errors:
            report.stopped_reason = "api_error"
        self.archive.finish_run(report, self.clock())
        return report

    # -- 1. fixtures -------------------------------------------------------

    def _step_fixtures(self, report: PrematchRunReport, now: datetime, dates: Sequence[str]) -> None:
        known = self.archive.known_fixture_ids()
        kept: list[tuple[datetime, Mapping[str, Any], dict[str, Any]]] = []
        try:
            for day in dates:
                payload, fetched_at = self._call(report, "/fixtures", {"date": day})
                for obj in payload.get("response") or []:
                    row = parse_listed_fixture(obj)
                    if row is None or not self._tracked(row["league_id"]):
                        continue
                    # New fixtures only while they are still upcoming; a fixture we already
                    # follow is refreshed whatever its status (postponed, started, ...).
                    if row["status_short"] in UPCOMING_STATUSES or row["fixture_id"] in known:
                        kept.append((fetched_at, obj, row))
        finally:
            if kept:
                self.archive.write_raw(
                    "fixtures", report.run_id, now,
                    [{"fetched_at": _iso(t), "fixture": obj} for t, obj, _ in kept],
                )
                self.archive.upsert_fixtures([(t, row) for t, _, row in kept])
            report.fixtures_seen = sum(
                1 for _, _, row in kept
                if row["status_short"] in UPCOMING_STATUSES and _parse_utc(row["kickoff_utc"]) > now
            )

    # -- 2. lineups --------------------------------------------------------

    def _step_lineups(self, report: PrematchRunReport, now: datetime) -> None:
        candidates = self.archive.lineup_candidates(now, now, self.lineup_window_minutes)
        ids = [c["fixture_id"] for c in candidates]
        for chunk_no, start in enumerate(range(0, len(ids), DEFAULT_BATCH_SIZE), start=1):
            chunk = ids[start:start + DEFAULT_BATCH_SIZE]
            payload, fetched_at = self._call(report, "/fixtures", {"ids": "-".join(str(i) for i in chunk)})
            returned = {}
            for obj in payload.get("response") or []:
                fixture_id = parse_int(_obj(obj.get("fixture")).get("id"))
                if fixture_id is not None:
                    returned[fixture_id] = obj
            self._store_lineups(report, chunk_no, chunk, returned, fetched_at, now)

    def _store_lineups(
        self,
        report: PrematchRunReport,
        chunk_no: int,
        requested: Sequence[int],
        returned: Mapping[int, Mapping[str, Any]],
        fetched_at: datetime,
        now: datetime,
    ) -> None:
        with_lineup: list[tuple[int, Mapping[str, Any], dict[str, Any], dict[int, dict[str, Any]]]] = []
        refreshed: list[tuple[datetime, dict[str, Any]]] = []
        attempts: list[tuple[int, float | None, int]] = []
        for fixture_id in requested:
            obj = returned.get(fixture_id)
            row = parse_listed_fixture(obj) if obj is not None else None
            if row is None:
                attempts.append((fixture_id, None, 0))
                continue
            refreshed.append((fetched_at, row))  # kickoff and status as the provider now reports them
            minutes = _minutes_before(row["kickoff_utc"], fetched_at)
            blocks = parse_lineup_blocks(obj) if minutes > 0 else {}
            attempts.append((fixture_id, minutes, len(blocks)))
            if blocks:
                with_lineup.append((fixture_id, obj, row, blocks))
        report.lineups_tried += len(requested)

        raw_file = None
        if with_lineup:
            raw_file = self.archive.write_raw(
                "lineups", f"{report.run_id}_{chunk_no:02d}", now,
                [{"fetched_at": _iso(fetched_at), "fixture": obj} for _, obj, _, _ in with_lineup],
            )
        self.archive.upsert_fixtures(refreshed)
        for fixture_id, minutes, teams_found in attempts:
            self.archive.record_lineup_attempt(fixture_id, fetched_at, minutes, teams_found)
        for fixture_id, _, row, blocks in with_lineup:
            before = self.archive.captured_lineup_teams(fixture_id)
            for team_id, block in blocks.items():
                if team_id not in before:
                    self.archive.record_lineup_team(
                        fixture_id, block, fetched_at, _minutes_before(row["kickoff_utc"], fetched_at), raw_file
                    )
            after = self.archive.captured_lineup_teams(fixture_id)
            if len(before) < 2 <= len(after):
                report.lineups_captured += 1

    # -- 3. injuries -------------------------------------------------------

    def _step_injuries(self, report: PrematchRunReport, now: datetime, dates: Sequence[str]) -> None:
        # The feed lists every injury twice (identical entries), so one snapshot is
        # reduced to one entry per state; otherwise "seen" would count the duplicate.
        snapshot: dict[tuple, tuple[datetime, Mapping[str, Any], dict[str, Any]]] = {}
        try:
            for day in dates:
                page, total_pages = 1, 1
                while page <= total_pages:
                    params: dict[str, Any] = {"date": day}
                    if page > 1:
                        params["page"] = page
                    payload, fetched_at = self._call(report, "/injuries", params)
                    total_pages = int((payload.get("paging") or {}).get("total") or 1)
                    for obj in payload.get("response") or []:
                        row = parse_injury(obj)
                        if row is None or not self._tracked(row["league_id"]):
                            continue
                        # Pre-match only: a report first seen after kickoff says nothing about
                        # what was known before it.
                        if _parse_utc(row["kickoff_utc"]) <= fetched_at:
                            continue
                        state = (row["fixture_id"], row["player_id"], row["type"], row["reason"])
                        snapshot.setdefault(state, (fetched_at, obj, row))
                    page += 1
        finally:
            collected = list(snapshot.values())
            if collected:
                fresh = self.archive.unseen_injuries([row for _, _, row in collected])
                if fresh:
                    self.archive.write_raw(
                        "injuries", report.run_id, now,
                        [{"fetched_at": _iso(collected[i][0]), "injury": collected[i][1]} for i in fresh],
                    )
                new, seen = self.archive.record_injuries([(t, row) for t, _, row in collected])
                report.injuries_new += new
                report.injuries_seen += seen

    # -- 4. predictions ----------------------------------------------------

    def _step_predictions(self, report: PrematchRunReport, now: datetime) -> None:
        candidates = self.archive.prediction_candidates(now, now, self.prediction_window_hours)
        stored: list[tuple[int, str, Mapping[str, Any], dict[str, Any], datetime]] = []
        attempts: list[tuple[int, datetime, bool]] = []
        errors_in_a_row = 0
        try:
            for candidate in candidates:
                fixture_id = candidate["fixture_id"]
                try:
                    payload, fetched_at = self._call(report, "/predictions", {"fixture": fixture_id})
                except _ApiFailure as exc:
                    report.errors.append(f"predictions: {exc}")
                    errors_in_a_row += 1
                    if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                        break
                    continue
                errors_in_a_row = 0
                response = payload.get("response") or []
                obj = response[0] if response and isinstance(response[0], dict) else {}
                parsed = parse_prediction(obj)
                attempts.append((fixture_id, fetched_at, parsed is not None))
                if parsed is not None and _minutes_before(candidate["kickoff_utc"], fetched_at) > 0:
                    stored.append((fixture_id, candidate["kickoff_utc"], obj, parsed, fetched_at))
        finally:
            raw_file = None
            if stored:
                raw_file = self.archive.write_raw(
                    "predictions", report.run_id, now,
                    [{"fetched_at": _iso(t), "fixture_id": fid, "prediction": obj} for fid, _, obj, _, t in stored],
                )
            for fixture_id, fetched_at, ok in attempts:
                self.archive.record_prediction_attempt(fixture_id, fetched_at, ok)
            for fixture_id, kickoff, _, parsed, fetched_at in stored:
                if self.archive.record_prediction(
                    fixture_id, kickoff, parsed, fetched_at, _minutes_before(kickoff, fetched_at), raw_file
                ):
                    report.predictions_stored += 1


__all__ = [
    "LINEUP_WINDOW_MINUTES",
    "MAX_PREDICTION_ATTEMPTS",
    "PREDICTION_WINDOW_HOURS",
    "TRACKED_LEAGUE_IDS",
    "UPCOMING_STATUSES",
    "PrematchArchive",
    "PrematchCollector",
    "PrematchRunReport",
    "fixture_dates",
    "parse_injury",
    "parse_lineup_blocks",
    "parse_listed_fixture",
    "parse_prediction",
]
