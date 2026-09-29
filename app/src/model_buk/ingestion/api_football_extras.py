"""Historical "extras" from API-Football: injuries, coaches, transfers, sidelined.

:mod:`model_buk.ingestion.api_football_history` archives match detail. This
module archives four further endpoints that the match archive does not have,
with the same design: raw provider payloads are kept forever as gzip JSON
(append-only, timestamped, never overwritten), a small SQLite state database
records what has been fetched, and a separate offline step turns the raw
archive into analysis tables. Every run is safe to stop and rerun and never
spends a request on something that is already archived.

.. warning::

    **HISTORICAL INJURIES ARE RETROSPECTIVE.** ``/injuries`` lists who
    actually missed (or was doubtful for) a match, as the provider recorded it
    for that fixture. It does NOT tell what was known before kickoff: the
    provider gives no timestamp for when an entry was added, and entries can
    be added or changed after the match. A feature built from these rows for a
    past match therefore *leaks* the future unless it is restricted to
    information that provably existed before the decision time (for example
    only rows for earlier fixtures, never the fixture being predicted). The
    same caution applies to every ``end`` date in ``/coachs`` and
    ``/sidelined`` (a spell's end is only known once it has happened) and to
    ``fetched_at`` everywhere: it is when WE downloaded a row, never when the
    fact became known. The built tables repeat this warning in ``build_info``
    and ``table_notes``.

Endpoints and scope:

- ``/injuries?league=L&season=S``: the whole league-season in one request
  (plus one per extra page when ``paging.total > 1``). Coverage starts around
  2021. The running season can be refreshed later; a refresh is a NEW
  timestamped raw file and the old one is kept.
- ``/coachs?team=T`` and ``/transfers?team=T``: one request per team id found
  in the match archive. ``/coachs?team=T`` only returns coaches whose CURRENT
  team is ``T`` (with their whole career), so a coach who moved on to a club
  outside the fetched teams is missing from the spells of the clubs he left.
  ``/transfers?team=T`` lists only the moves that involve ``T``: a player's
  full history exists only in the union of the responses of all his clubs.
- ``/sidelined``: the endpoint accepts up to 20 player ids per request
  (``players=id-id-...``) and simply omits players it has no records for, so
  ~45k players cost ~2.3k requests instead of one request each. Players are
  queued from ``lineup_players`` in priority order: top-20 leagues first, most
  recent season first, then by number of lineup appearances. A player without
  records is stored as ``empty`` and never asked again; that means "the
  provider has nothing", not "never injured".

Missing data policy (see ``docs/DATA_CONTRACT.md``): a provider ``null`` or an
absent field becomes SQL ``NULL`` in the tables and is never coerced to ``0``
or an empty string. An ``end`` of ``NULL`` in a spell means "ongoing or
unknown". A date field that is not a real ISO date (the provider sometimes
sends junk such as ``Winner``) also becomes ``NULL``; the raw file keeps it.

Nothing here talks to the live paper/shadow pipeline; it only writes raw
archives (and a rebuildable SQLite database) to disk.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from model_buk.ingestion.api_football_history import (
    CURRENT_SEASON,
    TOP20_LEAGUES,
    HistoryHttpClient,
    LeagueSeason,
    RequestBudget,
    all_planned_league_seasons,
    current_season_scope,
)
from model_buk.ingestion.api_football_tables import parse_int, parse_kickoff_utc

SCHEMA_VERSION = "api-football-extras-tables-v1"

INJURIES = "injuries"
COACHES = "coaches"
TRANSFERS = "transfers"
SIDELINED = "sidelined"
KINDS: tuple[str, ...] = (INJURIES, COACHES, TRANSFERS, SIDELINED)

# API-Football's own endpoint names for the keyed (one team / league-season) kinds.
ENDPOINTS: dict[str, str] = {INJURIES: "/injuries", COACHES: "/coachs", TRANSFERS: "/transfers", SIDELINED: "/sidelined"}

# Injuries are only recorded from about 2021 (a 2019 probe came back empty).
INJURY_FIRST_SEASON = 2021
# The documented maximum of ``/sidelined?players=id-id-...``.
SIDELINED_BATCH_SIZE = 20
# Only players seen in a lineup since this date are queued for /sidelined.
SIDELINED_DEFAULT_SINCE = "2023-01-01"
# A key (team, league-season, player batch) that failed this many times is left
# alone by later runs so one bad key cannot burn the daily budget.
MAX_ATTEMPTS = 3
# Consecutive failures that abort a kind: a systemic problem, not a bad key.
MAX_CONSECUTIVE_ERRORS = 5
# Safety net against a bogus ``paging.total``.
MAX_PAGES = 100

RETROSPECTIVE_WARNING = (
    "RETROSPECTIVE DATA: injuries list who actually missed (or was doubtful for) a match as recorded by "
    "the provider, which is NOT what was known before kickoff. The provider gives no timestamp for when "
    "an entry was added and entries may be added or changed after the match, so features built from these "
    "rows leak the future unless restricted to information that provably existed before the decision time "
    "(e.g. only earlier fixtures, never the fixture being predicted). fetched_at is when the row was "
    "downloaded, not when the fact became known."
)

TABLE_NOTES: dict[str, str] = {
    "injuries": RETROSPECTIVE_WARNING,
    "coach_spells": (
        "end_date is only known once the spell has ended (NULL means still in charge at fetched_at, or "
        "unknown); using an end_date later than the decision time leaks the departure. Only use spells with "
        "start_date <= decision time. The provider returns a coach only under his CURRENT team, so spells at "
        "clubs of coaches who now work outside the fetched teams are missing."
    ),
    "transfers": (
        "transfer_date is the effective date of a move; use only rows with transfer_date <= decision time. "
        "The provider may register a move some time after its date, so rows close to the decision time are "
        "weaker evidence. type is free text (a fee, Loan, Free, N/A) and NULL where the provider gave none. "
        "Moves are the union over the fetched clubs' responses; a move between two clubs outside the "
        "fetched teams is absent."
    ),
    "sidelined": (
        "Periods are complete as of fetched_at. end_date is only known once the player is back: for a "
        "decision at time t use only periods with start_date <= t and treat any end_date > t (or NULL) as "
        "unknown at t. A player without rows has NO provider records, which is not the same as never injured; "
        "players never queried (not in the sidelined scope) are absent entirely. NULL stays NULL."
    ),
}

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "injuries": (
        "fixture_id",
        "league_id",
        "season",
        "team_id",
        "player_id",
        "player_name",
        "type",
        "reason",
        "fixture_date_utc",
        "fetched_at",
    ),
    "coach_spells": ("coach_id", "coach_name", "team_id", "start_date", "end_date", "fetched_at"),
    "transfers": (
        "player_id",
        "player_name",
        "transfer_date",
        "type",
        "team_in_id",
        "team_out_id",
        "fetched_at",
    ),
    "sidelined": ("player_id", "type", "start_date", "end_date", "fetched_at"),
}
DATA_TABLES: tuple[str, ...] = tuple(TABLE_COLUMNS)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds")


_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_STAMP_FORMAT)


def _stamp_to_iso(stamp: str) -> str:
    return datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str | None:
    """Non-blank text or ``None``; an empty provider string carries no information."""

    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _date_text(value: Any) -> str | None:
    """Provider date text when it starts with a real ISO date, else ``None``.

    The provider occasionally sends junk in date fields (a coach spell that
    "starts" on ``Winner``); such a value carries no date, so it becomes NULL
    instead of a string that would break date parsing downstream. The raw
    archive keeps the original text.
    """

    text = _text(value)
    if text is None:
        return None
    try:
        date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _response_rows(payload: Any) -> list[Any]:
    return _list(_obj(payload).get("response"))


def _paging_total(payload: Any) -> int:
    total = parse_int(_obj(_obj(payload).get("paging")).get("total"))
    return max(total, 1) if total is not None else 1


def read_gz_json(path: str | Path) -> Any:
    return json.loads(gzip.decompress(Path(path).read_bytes()).decode("utf-8"))


# --------------------------------------------------------------------------
# Scope: what to download
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerPriority:
    """One queued player for ``/sidelined`` with the fields that rank the queue."""

    player_id: int
    top20: bool
    last_season: int | None
    appearances: int


def injury_scope(
    *,
    first_season: int = INJURY_FIRST_SEASON,
    current_season: int = CURRENT_SEASON,
    current_season_league_ids: Iterable[int] | None = None,
) -> tuple[LeagueSeason, ...]:
    """League-seasons to fetch injuries for: the planned scope from ``first_season`` on.

    The planned scope only reaches the last finished season, so the running
    ``current_season`` is added for every planned league, or only for
    ``current_season_league_ids`` when given (leagues that actually have
    matches in the running season). Ordered league by league in plan order
    (top-20 first), newest season first within a league.
    """

    planned = all_planned_league_seasons()
    by_key: dict[tuple[int, int], LeagueSeason] = {
        (ls.league_id, ls.season): ls for ls in planned if ls.season >= first_season
    }
    active = None if current_season_league_ids is None else set(current_season_league_ids)
    for ls in current_season_scope(current_season):
        if ls.season < first_season or (active is not None and ls.league_id not in active):
            continue
        by_key.setdefault((ls.league_id, ls.season), ls)
    rank: dict[int, int] = {}
    for ls in planned:
        rank.setdefault(ls.league_id, len(rank))
    return tuple(sorted(by_key.values(), key=lambda ls: (rank.get(ls.league_id, len(rank)), -ls.season)))


def _open_history(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection: the extras step must never modify the match tables."""

    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)


_TOP20_IDS = tuple(ls.league_id for ls in TOP20_LEAGUES)
_TOP20_SQL = ", ".join("?" for _ in _TOP20_IDS)


def team_ids_from_history(db_path: str | Path) -> list[int]:
    """Every distinct team id in ``matches``: top-20 teams first, then most recently active."""

    with closing(_open_history(db_path)) as conn:
        rows = conn.execute(
            f"""WITH sides AS (
                    SELECT home_team_id AS team_id, league_id, kickoff_utc FROM matches WHERE home_team_id IS NOT NULL
                    UNION ALL
                    SELECT away_team_id, league_id, kickoff_utc FROM matches WHERE away_team_id IS NOT NULL
                )
                SELECT team_id,
                       MAX(CASE WHEN league_id IN ({_TOP20_SQL}) THEN 1 ELSE 0 END) AS top20,
                       MAX(kickoff_utc) AS last_kickoff
                FROM sides GROUP BY team_id
                ORDER BY top20 DESC, last_kickoff DESC, team_id""",
            _TOP20_IDS,
        ).fetchall()
    return [int(row[0]) for row in rows]


def current_season_league_ids_from_history(db_path: str | Path, season: int) -> set[int]:
    with closing(_open_history(db_path)) as conn:
        rows = conn.execute("SELECT DISTINCT league_id FROM matches WHERE season = ? AND league_id IS NOT NULL", (season,))
        return {int(row[0]) for row in rows}


def player_scope_from_history(
    db_path: str | Path, since: str = SIDELINED_DEFAULT_SINCE
) -> list[PlayerPriority]:
    """Players seen in a lineup since ``since`` (ISO date), best-first for ``/sidelined``.

    Priority: has an appearance in a top-20 league, then the most recent
    season of any appearance, then the number of lineup appearances (starters
    and bench alike), then the player id so the order is deterministic.
    """

    with closing(_open_history(db_path)) as conn:
        rows = conn.execute(
            f"""SELECT lp.player_id,
                       MAX(CASE WHEN m.league_id IN ({_TOP20_SQL}) THEN 1 ELSE 0 END) AS top20,
                       MAX(m.season) AS last_season,
                       COUNT(*) AS appearances
                FROM lineup_players lp
                JOIN matches m ON m.fixture_id = lp.fixture_id
                WHERE lp.player_id IS NOT NULL AND m.kickoff_utc >= ?
                GROUP BY lp.player_id
                ORDER BY top20 DESC, last_season DESC, appearances DESC, lp.player_id""",
            (*_TOP20_IDS, since),
        ).fetchall()
    return [
        PlayerPriority(int(pid), bool(top20), None if last_season is None else int(last_season), int(appearances))
        for pid, top20, last_season, appearances in rows
    ]


# --------------------------------------------------------------------------
# Parsers (pure: one raw entry in, plain row dicts out)
# --------------------------------------------------------------------------


def parse_injury_row(
    row: Mapping[str, Any],
    *,
    fetched_at: str | None,
    default_league_id: int | None = None,
    default_season: int | None = None,
) -> dict[str, Any]:
    """One ``/injuries`` response row -> an ``injuries`` table row.

    ``league_id`` and ``season`` fall back to the values that were requested
    when the row itself does not carry them; everything else that is missing
    stays ``None``.
    """

    player = _obj(row.get("player"))
    team = _obj(row.get("team"))
    fixture = _obj(row.get("fixture"))
    league = _obj(row.get("league"))
    league_id = parse_int(league.get("id"))
    season = parse_int(league.get("season"))
    return {
        "fixture_id": parse_int(fixture.get("id")),
        "league_id": league_id if league_id is not None else default_league_id,
        "season": season if season is not None else default_season,
        "team_id": parse_int(team.get("id")),
        "player_id": parse_int(player.get("id")),
        "player_name": _text(player.get("name")),
        "type": _text(player.get("type")),
        "reason": _text(player.get("reason")),
        "fixture_date_utc": parse_kickoff_utc(fixture),
        "fetched_at": fetched_at,
    }


def parse_coach_spells(entry: Mapping[str, Any], *, fetched_at: str | None) -> list[dict[str, Any]]:
    """One ``/coachs`` entry -> one row per ``career[]`` spell (none without a coach id)."""

    coach_id = parse_int(entry.get("id"))
    if coach_id is None:
        return []
    coach_name = _text(entry.get("name"))
    return [
        {
            "coach_id": coach_id,
            "coach_name": coach_name,
            "team_id": parse_int(_obj(spell.get("team")).get("id")),
            "start_date": _date_text(spell.get("start")),
            "end_date": _date_text(spell.get("end")),
            "fetched_at": fetched_at,
        }
        for spell in _list(entry.get("career"))
        if isinstance(spell, Mapping)
    ]


def parse_transfers(entry: Mapping[str, Any], *, fetched_at: str | None) -> list[dict[str, Any]]:
    """One ``/transfers`` entry (a player) -> one row per ``transfers[]`` move."""

    player = _obj(entry.get("player"))
    player_id = parse_int(player.get("id"))
    if player_id is None:
        return []
    player_name = _text(player.get("name"))
    rows = []
    for move in _list(entry.get("transfers")):
        if not isinstance(move, Mapping):
            continue
        teams = _obj(move.get("teams"))
        rows.append(
            {
                "player_id": player_id,
                "player_name": player_name,
                "transfer_date": _date_text(move.get("date")),
                "type": _text(move.get("type")),
                "team_in_id": parse_int(_obj(teams.get("in")).get("id")),
                "team_out_id": parse_int(_obj(teams.get("out")).get("id")),
                "fetched_at": fetched_at,
            }
        )
    return rows


def _sidelined_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": _text(record.get("type")),
        "start": _date_text(record.get("start")),
        "end": _date_text(record.get("end")),
    }


def parse_sidelined_payload(payload: Any, requested_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    """Map player id -> sidelined records for one ``/sidelined`` response.

    A batch response (``players=a-b-c``) is a list of ``{"id", "sidelined":
    [...]}``; players the provider has nothing for are simply absent. A
    single-player response is a bare list of records, which can only be
    attributed when exactly one id was requested.
    """

    rows = [row for row in _response_rows(payload) if isinstance(row, Mapping)]
    batch_rows = [row for row in rows if "sidelined" in row]
    if batch_rows:
        out: dict[int, list[dict[str, Any]]] = {}
        for row in batch_rows:
            player_id = parse_int(row.get("id"))
            if player_id is None:
                player_id = parse_int(_obj(row.get("player")).get("id"))
            if player_id is None:
                continue
            out[player_id] = [_sidelined_record(r) for r in _list(row.get("sidelined")) if isinstance(r, Mapping)]
        return out
    if rows and len(requested_ids) == 1:
        return {int(requested_ids[0]): [_sidelined_record(r) for r in rows]}
    return {}


def _requested_ids(payload: Any) -> list[int]:
    """Player ids a raw ``/sidelined`` payload was asked for (the API echoes its parameters)."""

    params = _obj(_obj(payload).get("parameters"))
    raw = params.get("players", params.get("player"))
    ids = [parse_int(part) for part in str(raw).split("-")] if raw not in (None, "") else []
    return [i for i in ids if i is not None]


# --------------------------------------------------------------------------
# State + raw archive
# --------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_runs (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    stamp TEXT NOT NULL,
    pages_total INTEGER,
    pages_done INTEGER NOT NULL DEFAULT 0,
    results INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (kind, key, stamp)
);
CREATE TABLE IF NOT EXISTS fetch_errors (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_at TEXT,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS sidelined_players (
    player_id INTEGER PRIMARY KEY,
    top20 INTEGER NOT NULL DEFAULT 0,
    last_season INTEGER,
    appearances INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    records INTEGER,
    batch_file TEXT,
    fetched_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sidelined_queue
    ON sidelined_players (status, top20 DESC, last_season DESC, appearances DESC, player_id);
"""


@dataclass(frozen=True)
class FetchRun:
    """One (possibly multi-page) fetch of a key; identified by its start stamp."""

    kind: str
    key: str
    stamp: str
    pages_total: int | None
    pages_done: int
    results: int
    complete: bool
    completed_at: str | None


_PAGE_NAME = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)_p(?P<page>\d{3})of(?P<total>\d{3})(?:-(?P<dup>\d+))?\.json\.gz$")
_BATCH_NAME = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)_(?P<first>\d+)(?:-(?P<dup>\d+))?\.json\.gz$")


def _free_path(path: Path) -> Path:
    """First non-existing sibling of ``path``: raw files are append-only, never overwritten."""

    if not path.exists():
        return path
    stem = path.name[: -len(".json.gz")]
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}-{counter}.json.gz")
        if not candidate.exists():
            return candidate
        counter += 1


class ExtrasStore:
    """Filesystem + SQLite archive for the extras endpoints.

    Layout under ``root``::

        raw/injuries/{league_id}/{season}/{stamp}_p001of001.json.gz
        raw/coaches/{team_id}/{stamp}_p001of001.json.gz
        raw/transfers/{team_id}/{stamp}_p001of001.json.gz
        raw/sidelined/{stamp}_{first_player_id}.json.gz     # one file per batch of <=20 players
        state.sqlite3                                       # progress, resumable
        tables/extras.sqlite                                # built by build_extras_tables

    ``stamp`` is the UTC start time of a fetch (``20260929T120000Z``); every
    page of one fetch shares it and the file name carries ``pNNNofMMM``, so
    the offline table build can tell complete fetches from interrupted ones
    without the state database.
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

    # -- keyed kinds (injuries, coaches, transfers) -------------------------

    def key_dir(self, kind: str, key: str) -> Path:
        return self.raw_dir / kind / key.replace(":", "/")

    def page_path(self, kind: str, key: str, stamp: str, page: int, total: int) -> Path:
        return self.key_dir(kind, key) / f"{stamp}_p{page:03d}of{total:03d}.json.gz"

    def find_page_file(self, kind: str, key: str, stamp: str, page: int) -> Path | None:
        directory = self.key_dir(kind, key)
        if not directory.is_dir():
            return None
        matches = sorted(directory.glob(f"{stamp}_p{page:03d}of*.json.gz"))
        return matches[-1] if matches else None

    def write_raw(self, path: Path, payload: Mapping[str, Any]) -> Path:
        """Write ``payload`` as gzip JSON without ever replacing an existing file."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path = _free_path(path)
        temp = path.with_name(path.name + ".tmp")
        temp.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        os.replace(temp, path)
        return path

    @staticmethod
    def _run_from_row(row: Sequence[Any]) -> FetchRun:
        return FetchRun(
            kind=row[0],
            key=row[1],
            stamp=row[2],
            pages_total=row[3],
            pages_done=row[4],
            results=row[5],
            complete=bool(row[6]),
            completed_at=row[7],
        )

    _RUN_COLUMNS = "kind, key, stamp, pages_total, pages_done, results, complete, completed_at"

    def latest_runs(self, kind: str) -> dict[str, FetchRun]:
        """Newest fetch of every key of ``kind`` (complete or not)."""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM fetch_runs WHERE kind = ? ORDER BY stamp", (kind,)
            ).fetchall()
        return {row[1]: self._run_from_row(row) for row in rows}

    def open_run(self, kind: str, key: str, now: datetime) -> FetchRun:
        """Resume the key's unfinished fetch, or start a new one with a fresh stamp."""

        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM fetch_runs WHERE kind = ? AND key = ? ORDER BY stamp DESC LIMIT 1",
                (kind, key),
            ).fetchone()
            if row is not None and not row[6]:
                return self._run_from_row(row)
            stamp = _stamp(now)
            if row is not None and stamp <= row[2]:
                # Never reuse or precede an existing fetch's stamp (two fetches inside one second).
                stamp = _stamp(datetime.strptime(row[2], _STAMP_FORMAT).replace(tzinfo=timezone.utc) + timedelta(seconds=1))
            conn.execute(
                "INSERT INTO fetch_runs (kind, key, stamp, started_at) VALUES (?, ?, ?, ?)",
                (kind, key, stamp, now.astimezone(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()
        return FetchRun(kind, key, stamp, None, 0, 0, False, None)

    def record_page(self, run: FetchRun, page: int, total: int, results: int, complete: bool, now: datetime) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE fetch_runs SET pages_total = ?, pages_done = ?, results = results + ?,
                       complete = ?, completed_at = ?
                   WHERE kind = ? AND key = ? AND stamp = ?""",
                (
                    total,
                    page,
                    results,
                    int(complete),
                    now.astimezone(timezone.utc).isoformat(timespec="seconds") if complete else None,
                    run.kind,
                    run.key,
                    run.stamp,
                ),
            )
            conn.commit()

    def error_attempts(self, kind: str) -> dict[str, int]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT key, attempts FROM fetch_errors WHERE kind = ?", (kind,)).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def record_error(self, kind: str, key: str, message: str, now: datetime) -> int:
        """Count one failed attempt for ``key``; returns the attempts so far."""

        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO fetch_errors (kind, key, attempts, last_error, last_at) VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(kind, key) DO UPDATE SET attempts = attempts + 1,
                       last_error = excluded.last_error, last_at = excluded.last_at""",
                (kind, key, message[:500], now.astimezone(timezone.utc).isoformat(timespec="seconds")),
            )
            attempts = conn.execute("SELECT attempts FROM fetch_errors WHERE kind = ? AND key = ?", (kind, key)).fetchone()[0]
            conn.commit()
        return int(attempts)

    def clear_errors(self, kind: str, key: str) -> None:
        """Forget earlier failures of ``key`` once it succeeded, so old blips never add up over time."""

        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM fetch_errors WHERE kind = ? AND key = ?", (kind, key))
            conn.commit()

    # -- sidelined ----------------------------------------------------------

    def register_players(self, players: Iterable[PlayerPriority]) -> None:
        """Queue players; known players keep their status and only get fresh ranking fields."""

        with closing(self._connect()) as conn:
            conn.executemany(
                """INSERT INTO sidelined_players (player_id, top20, last_season, appearances) VALUES (?, ?, ?, ?)
                   ON CONFLICT(player_id) DO UPDATE SET top20 = excluded.top20,
                       last_season = excluded.last_season, appearances = excluded.appearances""",
                [(p.player_id, int(p.top20), p.last_season, p.appearances) for p in players],
            )
            conn.commit()

    def pending_players(self, limit: int, exclude: Iterable[int] = ()) -> list[int]:
        """Best-ranked pending players (fewer than MAX_ATTEMPTS failures), skipping ``exclude``.

        ``exclude`` is filtered in Python from an over-fetched window rather
        than with ``NOT IN (...)``, which would hit SQLite's variable limit
        for a large set.
        """

        excluded = set(exclude)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT player_id FROM sidelined_players
                   WHERE status = 'pending' AND attempts < ?
                   ORDER BY top20 DESC, last_season DESC, appearances DESC, player_id
                   LIMIT ?""",
                (MAX_ATTEMPTS, limit + len(excluded)),
            ).fetchall()
        return [int(row[0]) for row in rows if int(row[0]) not in excluded][:limit]

    def write_sidelined_batch(self, stamp: str, first_player_id: int, payload: Mapping[str, Any]) -> str:
        path = self.write_raw(self.raw_dir / SIDELINED / f"{stamp}_{first_player_id}.json.gz", payload)
        return path.relative_to(self.root).as_posix()

    def record_sidelined_batch(
        self,
        requested_ids: Sequence[int],
        records: Mapping[int, Sequence[Any]],
        batch_file: str,
        fetched_at: str,
    ) -> None:
        """Mark a batch done: players with records ``done``, the rest ``empty`` (never asked again)."""

        with closing(self._connect()) as conn:
            conn.executemany(
                """UPDATE sidelined_players SET status = ?, records = ?, batch_file = ?, fetched_at = ?
                   WHERE player_id = ?""",
                [
                    (
                        "done" if records.get(pid) else "empty",
                        len(records.get(pid) or ()),
                        batch_file,
                        fetched_at,
                        pid,
                    )
                    for pid in requested_ids
                ],
            )
            conn.commit()

    def record_sidelined_failure(self, requested_ids: Sequence[int], message: str) -> None:
        with closing(self._connect()) as conn:
            conn.executemany(
                "UPDATE sidelined_players SET attempts = attempts + 1, last_error = ? WHERE player_id = ?",
                [(message[:500], pid) for pid in requested_ids],
            )
            conn.commit()

    def sidelined_counts(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            rows = dict(conn.execute("SELECT status, COUNT(*) FROM sidelined_players GROUP BY status").fetchall())
            failed = conn.execute(
                "SELECT COUNT(*) FROM sidelined_players WHERE status = 'pending' AND attempts >= ?", (MAX_ATTEMPTS,)
            ).fetchone()[0]
        pending = int(rows.get("pending", 0)) - int(failed)
        return {
            "done": int(rows.get("done", 0)),
            "empty": int(rows.get("empty", 0)),
            "pending": pending,
            "failed": int(failed),
        }


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------


@dataclass
class KindReport:
    """Outcome of one kind in one run (counts are keys: league-seasons, teams or players)."""

    kind: str
    requests_spent: int = 0
    targets_total: int = 0
    already_done: int = 0
    completed: int = 0
    failed: int = 0
    remaining: int = 0
    stopped_reason: str = "completed"

    def summary_line(self) -> str:
        return (
            f"{self.kind:10s} requests={self.requests_spent:5d}  targets={self.targets_total:6d}  "
            f"already archived={self.already_done:6d}  fetched now={self.completed:6d}  "
            f"failed={self.failed:4d}  remaining={self.remaining:6d}  stopped: {self.stopped_reason}"
        )


class ExtrasDownloader:
    """Resumable, budget-aware download of injuries, coaches, transfers and sidelined."""

    def __init__(
        self,
        client: HistoryHttpClient,
        store: ExtrasStore,
        budget: RequestBudget,
        *,
        sleep_between_requests: float = 0.0,
        now: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.store = store
        self.budget = budget
        self.sleep_between_requests = sleep_between_requests
        self._now = now or _utc_now

    def _request(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        payload, headers = self.client.get(endpoint, params)
        self.budget.record(headers)
        if self.sleep_between_requests:
            time.sleep(self.sleep_between_requests)
        return payload

    # -- keyed kinds --------------------------------------------------------

    def _fetch_key(self, kind: str, key: str, params: Mapping[str, Any]) -> bool:
        """Fetch (or resume) every page of ``key``; ``False`` if the budget ran out first."""

        run = self.store.open_run(kind, key, self._now())
        page = run.pages_done + 1
        while True:
            if page > MAX_PAGES:
                raise RuntimeError(f"{kind} {key}: more than {MAX_PAGES} pages")
            existing = self.store.find_page_file(kind, key, run.stamp, page)
            if existing is not None:
                # A crash left this page on disk without a state entry: reuse it, spend nothing.
                payload = read_gz_json(existing)
                total = _paging_total(payload)
            else:
                if not self.budget.allow():
                    return False
                page_params = dict(params)
                if page > 1:
                    page_params["page"] = page
                payload = self._request(ENDPOINTS[kind], page_params)
                total = _paging_total(payload)
                self.store.write_raw(self.store.page_path(kind, key, run.stamp, page, total), payload)
            complete = page >= total
            self.store.record_page(run, page, total, len(_response_rows(payload)), complete, self._now())
            if complete:
                return True
            page += 1

    def _run_keyed(
        self,
        kind: str,
        targets: Sequence[tuple[str, Mapping[str, Any], timedelta | None]],
    ) -> KindReport:
        """Fetch each ``(key, params, refresh_after)`` target once; skip what is archived."""

        spent_before = self.budget.spent
        report = KindReport(kind, targets_total=len(targets))
        runs = self.store.latest_runs(kind)
        attempts = self.store.error_attempts(kind)
        now = self._now()
        consecutive_errors = 0
        pending: list[tuple[str, Mapping[str, Any]]] = []
        for key, params, refresh_after in targets:
            run = runs.get(key)
            if attempts.get(key, 0) >= MAX_ATTEMPTS:
                report.failed += 1
            elif run is None or not run.complete:
                pending.append((key, params))
            elif refresh_after is not None and (
                run.completed_at is None or now - datetime.fromisoformat(run.completed_at) >= refresh_after
            ):
                pending.append((key, params))
            else:
                report.already_done += 1

        for index, (key, params) in enumerate(pending):
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                report.remaining += len(pending) - index
                break
            try:
                done = self._fetch_key(kind, key, params)
            except Exception as exc:  # noqa: BLE001 - a bad key must not kill the whole run
                made = self.store.record_error(kind, key, f"{type(exc).__name__}: {exc}", self._now())
                if made >= MAX_ATTEMPTS:
                    report.failed += 1
                else:
                    report.remaining += 1
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped_reason = "errors"
                    report.remaining += len(pending) - index - 1
                    break
                continue
            consecutive_errors = 0
            if done:
                if attempts.get(key):
                    self.store.clear_errors(kind, key)
                report.completed += 1
            else:
                report.stopped_reason = "budget_exhausted"
                report.remaining += len(pending) - index
                break
        report.requests_spent = self.budget.spent - spent_before
        return report

    def run_injuries(
        self,
        scope: Iterable[LeagueSeason],
        *,
        refresh_after_days: float | None = None,
        current_season: int = CURRENT_SEASON,
    ) -> KindReport:
        """One ``/injuries`` fetch per league-season (extra requests only for extra pages).

        With ``refresh_after_days`` the running ``current_season`` is fetched
        again once its newest complete fetch is that old, as a new timestamped
        raw file. Finished seasons are never refetched.
        """

        refresh = None if refresh_after_days is None else timedelta(days=refresh_after_days)
        targets = []
        seen: set[str] = set()
        for ls in scope:
            key = f"{ls.league_id}:{ls.season}"
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                (key, {"league": ls.league_id, "season": ls.season}, refresh if ls.season == current_season else None)
            )
        return self._run_keyed(INJURIES, targets)

    def run_coaches(self, team_ids: Iterable[int]) -> KindReport:
        return self._run_keyed(COACHES, [(str(t), {"team": t}, None) for t in dict.fromkeys(team_ids)])

    def run_transfers(self, team_ids: Iterable[int]) -> KindReport:
        return self._run_keyed(TRANSFERS, [(str(t), {"team": t}, None) for t in dict.fromkeys(team_ids)])

    # -- sidelined ----------------------------------------------------------

    def run_sidelined(self, players: Iterable[PlayerPriority], *, batch_size: int = SIDELINED_BATCH_SIZE) -> KindReport:
        """Queue ``players`` and fetch the pending ones, best-ranked first, ``batch_size`` per request.

        The queue lives in the state database, so a run also finishes players
        queued by earlier runs, and a player already marked ``done`` or
        ``empty`` is never requested again.
        """

        batch_size = max(1, min(batch_size, SIDELINED_BATCH_SIZE))
        spent_before = self.budget.spent
        self.store.register_players(players)
        counts = self.store.sidelined_counts()
        report = KindReport(
            SIDELINED,
            targets_total=sum(counts.values()),
            already_done=counts["done"] + counts["empty"],
        )
        # Players of a failed batch stay pending (one attempt counted) but are not retried within this
        # run; finished players leave the pending queue by themselves.
        failed_now: set[int] = set()
        consecutive_errors = 0
        while True:
            batch = self.store.pending_players(batch_size, exclude=failed_now)
            if not batch:
                break
            if not self.budget.allow():
                report.stopped_reason = "budget_exhausted"
                break
            try:
                payload = self._request(ENDPOINTS[SIDELINED], {"players": "-".join(str(pid) for pid in batch)})
            except Exception as exc:  # noqa: BLE001 - keep the queue intact, count the attempt
                self.store.record_sidelined_failure(batch, f"{type(exc).__name__}: {exc}")
                failed_now.update(batch)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped_reason = "errors"
                    break
                continue
            consecutive_errors = 0
            now = self._now()
            batch_file = self.store.write_sidelined_batch(_stamp(now), batch[0], payload)
            records = parse_sidelined_payload(payload, batch)
            self.store.record_sidelined_batch(batch, records, batch_file, now.astimezone(timezone.utc).isoformat(timespec="seconds"))
            report.completed += len(batch)
        after = self.store.sidelined_counts()
        report.remaining = after["pending"]
        report.failed = after["failed"]
        report.requests_spent = self.budget.spent - spent_before
        return report


# --------------------------------------------------------------------------
# Offline table build
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RawFile:
    kind: str
    key: str
    stamp: str
    page: int
    total: int
    path: Path


@dataclass(frozen=True)
class ExtrasBuildReport:
    out_path: Path
    source_root: Path
    built_at: str
    row_counts: dict[str, int]
    duplicates_skipped: dict[str, int]
    raw_files: dict[str, int]
    incomplete_runs: int
    entries_without_id: int
    seconds: float


def _keyed_files(root: Path, kind: str) -> list[RawFile]:
    """Every raw page file of a keyed kind, keyed like the downloader's state."""

    base = root / "raw" / kind
    if not base.is_dir():
        return []
    depth = 2 if kind == INJURIES else 1
    pattern = "/".join(["*"] * depth) + "/*.json.gz"
    files = []
    for path in sorted(base.glob(pattern)):
        match = _PAGE_NAME.match(path.name)
        if match is None:
            continue
        key = ":".join(path.relative_to(base).parts[:depth])
        files.append(RawFile(kind, key, match["stamp"], int(match["page"]), int(match["total"]), path))
    return files


def _sidelined_files(root: Path) -> list[RawFile]:
    base = root / "raw" / SIDELINED
    if not base.is_dir():
        return []
    files = []
    for path in sorted(base.glob("*.json.gz")):
        match = _BATCH_NAME.match(path.name)
        if match is not None:
            files.append(RawFile(SIDELINED, match["first"], match["stamp"], 1, 1, path))
    return files


def _complete_runs(files: Sequence[RawFile]) -> tuple[dict[tuple[str, str], list[RawFile]], int]:
    """Group page files into fetches; return the complete ones and how many were incomplete.

    A fetch is complete when every page ``1..total`` is on disk. Should a page
    exist more than once (a re-download after a crash), the newest copy wins.
    """

    grouped: dict[tuple[str, str], dict[int, RawFile]] = {}
    totals: dict[tuple[str, str], int] = {}
    for raw in files:
        pages = grouped.setdefault((raw.key, raw.stamp), {})
        if raw.page not in pages or raw.path.name > pages[raw.page].path.name:
            pages[raw.page] = raw
        totals[(raw.key, raw.stamp)] = raw.total
    complete: dict[tuple[str, str], list[RawFile]] = {}
    incomplete = 0
    for run_id, pages in grouped.items():
        if set(pages) == set(range(1, totals[run_id] + 1)):
            complete[run_id] = [pages[p] for p in sorted(pages)]
        else:
            incomplete += 1
    return complete, incomplete


def _ordered(complete: Mapping[tuple[str, str], Sequence[RawFile]]) -> list[RawFile]:
    """Files of complete fetches, oldest first (ties by key), so later files override earlier ones."""

    return [raw for run_id in sorted(complete, key=lambda r: (r[1], r[0])) for raw in complete[run_id]]


_TABLES_DDL = """
CREATE TABLE injuries (
    fixture_id INTEGER,
    league_id INTEGER,
    season INTEGER,
    team_id INTEGER,
    player_id INTEGER,
    player_name TEXT,
    type TEXT,
    reason TEXT,
    fixture_date_utc TEXT,
    fetched_at TEXT
);
CREATE TABLE coach_spells (
    coach_id INTEGER,
    coach_name TEXT,
    team_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    fetched_at TEXT
);
CREATE TABLE transfers (
    player_id INTEGER,
    player_name TEXT,
    transfer_date TEXT,
    type TEXT,
    team_in_id INTEGER,
    team_out_id INTEGER,
    fetched_at TEXT
);
CREATE TABLE sidelined (
    player_id INTEGER,
    type TEXT,
    start_date TEXT,
    end_date TEXT,
    fetched_at TEXT
);
CREATE TABLE table_notes (
    table_name TEXT PRIMARY KEY,
    note TEXT NOT NULL
);
CREATE TABLE build_info (
    schema_version TEXT NOT NULL,
    built_at TEXT NOT NULL,
    source_root TEXT NOT NULL,
    injuries_rows INTEGER NOT NULL,
    coach_spells_rows INTEGER NOT NULL,
    transfers_rows INTEGER NOT NULL,
    sidelined_rows INTEGER NOT NULL,
    raw_files INTEGER NOT NULL,
    incomplete_runs INTEGER NOT NULL,
    retrospective_warning TEXT NOT NULL,
    build_seconds REAL
);
"""

_INDEX_DDL = """
CREATE INDEX idx_injuries_fixture ON injuries (fixture_id);
CREATE INDEX idx_injuries_team_date ON injuries (team_id, fixture_date_utc);
CREATE INDEX idx_injuries_player ON injuries (player_id);
CREATE INDEX idx_coach_spells_team ON coach_spells (team_id, start_date);
CREATE INDEX idx_coach_spells_coach ON coach_spells (coach_id);
CREATE INDEX idx_transfers_player ON transfers (player_id);
CREATE INDEX idx_transfers_in ON transfers (team_in_id, transfer_date);
CREATE INDEX idx_transfers_out ON transfers (team_out_id, transfer_date);
CREATE INDEX idx_sidelined_player ON sidelined (player_id, start_date);
"""

_INSERT_SQL: dict[str, str] = {
    table: f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + column for column in columns)})"
    for table, columns in TABLE_COLUMNS.items()
}


def _entries(payload: Any) -> Iterator[Mapping[str, Any]]:
    for entry in _response_rows(payload):
        if isinstance(entry, Mapping):
            yield entry


def build_extras_tables(
    archive_root: str | Path,
    out_path: str | Path,
    *,
    now: Callable[[], str] = _utc_now_iso,
) -> ExtrasBuildReport:
    """Rebuild ``extras.sqlite`` from the raw archive (no API calls, state database not needed).

    Always a full rebuild into a temporary file next to ``out_path`` that is
    moved over the destination with :func:`os.replace` only after every table
    is written, so a crash or a corrupt raw file leaves the previous database
    untouched. Rules for turning raw files into rows:

    - injuries: only the newest COMPLETE fetch of each league-season is used;
      an interrupted, still incomplete fetch never shadows an older complete
      one. Exact duplicate rows inside that fetch are dropped.
    - coaches, sidelined: an entity (coach, player) can show up in several
      responses, each carrying its complete record; its rows come from the
      newest response that contains it (later file wins), so a refreshed
      career replaces the older one instead of being appended to it.
    - transfers: ``/transfers?team=T`` lists only the moves that involve club
      ``T``, so one player's history is spread over the responses of every
      club he moved between and no single response is complete. The table is
      the UNION of all responses; a move listed in several of them (both
      clubs of the same transfer) is stored once, from the newest response.

    Rows keep provider gaps as ``NULL``. Entries without an id cannot be
    attributed to a coach or player and are counted, not stored.
    """

    started = time.perf_counter()
    root = Path(archive_root)
    out = Path(out_path)

    injury_runs, incomplete = _complete_runs(_keyed_files(root, INJURIES))
    coach_runs, coach_incomplete = _complete_runs(_keyed_files(root, COACHES))
    transfer_runs, transfer_incomplete = _complete_runs(_keyed_files(root, TRANSFERS))
    incomplete += coach_incomplete + transfer_incomplete
    sidelined_files = _sidelined_files(root)
    raw_counts = {
        INJURIES: sum(len(v) for v in injury_runs.values()),
        COACHES: sum(len(v) for v in coach_runs.values()),
        TRANSFERS: sum(len(v) for v in transfer_runs.values()),
        SIDELINED: len(sidelined_files),
    }
    if not any(raw_counts.values()):
        # Fail closed: never replace a good database with an empty one because of a mistyped path.
        raise FileNotFoundError(f"no complete raw extras files found under {root / 'raw'}")

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".building", dir=out.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    replaced = False
    duplicates = {table: 0 for table in DATA_TABLES}
    without_id = 0
    try:
        with closing(sqlite3.connect(tmp_path)) as conn:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=OFF")
            conn.executescript(_TABLES_DDL)

            # injuries: newest complete fetch per league-season.
            latest_injury: dict[str, tuple[str, list[RawFile]]] = {}
            for (key, stamp), pages in injury_runs.items():
                if key not in latest_injury or stamp > latest_injury[key][0]:
                    latest_injury[key] = (stamp, pages)
            for key in sorted(latest_injury):
                stamp, pages = latest_injury[key]
                league_text, _, season_text = key.partition(":")
                fetched_at = _stamp_to_iso(stamp)
                seen: set[tuple[Any, ...]] = set()
                rows = []
                for raw in pages:
                    for entry in _entries(read_gz_json(raw.path)):
                        row = parse_injury_row(
                            entry,
                            fetched_at=fetched_at,
                            default_league_id=parse_int(league_text),
                            default_season=parse_int(season_text),
                        )
                        identity = tuple(row[c] for c in TABLE_COLUMNS["injuries"] if c != "fetched_at")
                        if identity in seen:
                            duplicates["injuries"] += 1
                            continue
                        seen.add(identity)
                        rows.append(row)
                conn.executemany(_INSERT_SQL["injuries"], rows)

            # coaches: newest response per coach; transfers: union of every response.
            without_id += _load_latest_per_entity(
                conn,
                "coach_spells",
                _ordered(coach_runs),
                entity_id=lambda entry: parse_int(entry.get("id")),
                parse=parse_coach_spells,
                duplicates=duplicates,
            )
            without_id += _load_transfers(conn, _ordered(transfer_runs), duplicates)
            _load_sidelined(conn, sidelined_files)

            conn.executescript(_INDEX_DDL)
            row_counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in DATA_TABLES
            }
            conn.executemany("INSERT INTO table_notes (table_name, note) VALUES (?, ?)", sorted(TABLE_NOTES.items()))
            built_at = now()
            seconds = round(time.perf_counter() - started, 3)
            conn.execute(
                """INSERT INTO build_info (
                       schema_version, built_at, source_root, injuries_rows, coach_spells_rows,
                       transfers_rows, sidelined_rows, raw_files, incomplete_runs,
                       retrospective_warning, build_seconds
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    SCHEMA_VERSION,
                    built_at,
                    str(root.resolve()),
                    row_counts["injuries"],
                    row_counts["coach_spells"],
                    row_counts["transfers"],
                    row_counts["sidelined"],
                    sum(raw_counts.values()),
                    incomplete,
                    RETROSPECTIVE_WARNING,
                    seconds,
                ),
            )
            conn.commit()
            conn.execute("ANALYZE")
            conn.commit()
        os.replace(tmp_path, out)
        replaced = True
    finally:
        if not replaced:
            tmp_path.unlink(missing_ok=True)

    return ExtrasBuildReport(
        out_path=out,
        source_root=root,
        built_at=built_at,
        row_counts=row_counts,
        duplicates_skipped=duplicates,
        raw_files=raw_counts,
        incomplete_runs=incomplete,
        entries_without_id=without_id,
        seconds=seconds,
    )


def _load_latest_per_entity(
    conn: sqlite3.Connection,
    table: str,
    files: Sequence[RawFile],
    *,
    entity_id: Callable[[Mapping[str, Any]], int | None],
    parse: Callable[..., list[dict[str, Any]]],
    duplicates: dict[str, int],
) -> int:
    """Insert each entity's rows from the newest file that mentions it; returns entries without id.

    Two passes so memory stays flat: the first finds the newest file per
    entity, the second emits only that file's rows for the entity.
    """

    owner: dict[int, int] = {}
    without_id = 0
    for index, raw in enumerate(files):
        for entry in _entries(read_gz_json(raw.path)):
            entity = entity_id(entry)
            if entity is None:
                without_id += 1
            else:
                owner[entity] = index  # files are ordered oldest -> newest
    for index, raw in enumerate(files):
        fetched_at = _stamp_to_iso(raw.stamp)
        emitted: set[int] = set()
        rows = []
        for entry in _entries(read_gz_json(raw.path)):
            entity = entity_id(entry)
            if entity is None or owner.get(entity) != index or entity in emitted:
                continue
            emitted.add(entity)
            seen: set[tuple[Any, ...]] = set()
            for row in parse(entry, fetched_at=fetched_at):
                identity = tuple(row[c] for c in TABLE_COLUMNS[table] if c != "fetched_at")
                if identity in seen:
                    duplicates[table] += 1
                    continue
                seen.add(identity)
                rows.append(row)
        conn.executemany(_INSERT_SQL[table], rows)
    return without_id


_TRANSFER_IDENTITY = "player_id, transfer_date, type, team_in_id, team_out_id"


def _load_transfers(conn: sqlite3.Connection, files: Sequence[RawFile], duplicates: dict[str, int]) -> int:
    """Insert the union of all ``/transfers`` responses; returns entries without a player id.

    ``/transfers?team=T`` only lists the moves involving ``T``, so a player's
    history is complete only across the responses of all his clubs. A move is
    identified by player, date, type and the two clubs (``NULL`` equals
    ``NULL``); a move that several responses list is kept once, from the
    newest of them (files are ordered oldest to newest, so the highest rowid).
    """

    without_id = 0
    for raw in files:
        fetched_at = _stamp_to_iso(raw.stamp)
        rows = []
        for entry in _entries(read_gz_json(raw.path)):
            if parse_int(_obj(entry.get("player")).get("id")) is None:
                without_id += 1
                continue
            rows.extend(parse_transfers(entry, fetched_at=fetched_at))
        conn.executemany(_INSERT_SQL["transfers"], rows)
    removed = conn.execute(
        f"""DELETE FROM transfers WHERE rowid NOT IN (
                SELECT MAX(rowid) FROM transfers GROUP BY {_TRANSFER_IDENTITY})"""
    ).rowcount
    duplicates["transfers"] += max(int(removed), 0)
    return without_id


def _load_sidelined(conn: sqlite3.Connection, files: Sequence[RawFile]) -> None:
    ordered = sorted(files, key=lambda raw: (raw.stamp, raw.path.name))
    owner: dict[int, int] = {}
    for index, raw in enumerate(ordered):
        payload = read_gz_json(raw.path)
        for player_id, records in parse_sidelined_payload(payload, _requested_ids(payload)).items():
            if records:
                owner[player_id] = index
    for index, raw in enumerate(ordered):
        fetched_at = _stamp_to_iso(raw.stamp)
        payload = read_gz_json(raw.path)
        rows = [
            {
                "player_id": player_id,
                "type": record["type"],
                "start_date": record["start"],
                "end_date": record["end"],
                "fetched_at": fetched_at,
            }
            for player_id, records in parse_sidelined_payload(payload, _requested_ids(payload)).items()
            if owner.get(player_id) == index
            for record in records
        ]
        conn.executemany(_INSERT_SQL["sidelined"], rows)


__all__ = [
    "SCHEMA_VERSION",
    "KINDS",
    "INJURIES",
    "COACHES",
    "TRANSFERS",
    "SIDELINED",
    "ENDPOINTS",
    "INJURY_FIRST_SEASON",
    "SIDELINED_BATCH_SIZE",
    "SIDELINED_DEFAULT_SINCE",
    "MAX_ATTEMPTS",
    "MAX_CONSECUTIVE_ERRORS",
    "RETROSPECTIVE_WARNING",
    "TABLE_NOTES",
    "TABLE_COLUMNS",
    "PlayerPriority",
    "injury_scope",
    "team_ids_from_history",
    "current_season_league_ids_from_history",
    "player_scope_from_history",
    "parse_injury_row",
    "parse_coach_spells",
    "parse_transfers",
    "parse_sidelined_payload",
    "ExtrasStore",
    "FetchRun",
    "KindReport",
    "ExtrasDownloader",
    "ExtrasBuildReport",
    "build_extras_tables",
    "read_gz_json",
]
