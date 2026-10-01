"""Analysis-ready SQLite tables built from the raw API-Football history archive.

:mod:`model_buk.ingestion.api_football_history` only downloads and keeps raw
provider payloads (one gzip JSON file per ``/fixtures?ids=...`` batch). This
module is the separate, repeatable table-building step on top of that
archive. It is split in two layers on purpose:

- Pure parsing functions (``parse_match``, ``parse_team_match_stats``, ...)
  take ONE fixture dict exactly as it appears in a batch response and return
  plain row dicts. They never touch the filesystem or network, so every
  provider quirk can be pinned down with tiny synthetic fixtures in tests.
- :func:`build_history_tables` walks every batch file, feeds each fixture
  through the parsers and bulk-loads the rows into a fresh SQLite database
  that atomically replaces the previous one, so a failed or interrupted build
  never leaves a half-written database behind.

Missing data policy (see ``docs/DATA_CONTRACT.md``): a provider ``null`` or
an absent field is stored as SQL ``NULL`` and is never coerced to ``0``. This
matters in practice: API-Football reports e.g. ``"Red Cards": null`` for a
large share of team rows and ``null`` player shot/tackle counts, and whether
that means "zero" or "not collected" is a modelling decision that must be
made explicitly downstream, not silently here.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from model_buk.ingestion.api_football_history import all_planned_league_seasons

SCHEMA_VERSION = "api-football-history-tables-v1"
DEFAULT_FILES_PER_TRANSACTION = 200

# Provider stat ``type`` label -> typed column in ``team_match_stats``.
# Anything not listed here is preserved verbatim in ``extra_team_stats``.
TEAM_STAT_TYPES: dict[str, str] = {
    "Shots on Goal": "shots_on_goal",
    "Shots off Goal": "shots_off_goal",
    "Total Shots": "total_shots",
    "Blocked Shots": "blocked_shots",
    "Shots insidebox": "shots_insidebox",
    "Shots outsidebox": "shots_outsidebox",
    "Fouls": "fouls",
    "Corner Kicks": "corners",
    "Offsides": "offsides",
    "Ball Possession": "possession_pct",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Goalkeeper Saves": "goalkeeper_saves",
    "Total passes": "passes_total",
    "Passes accurate": "passes_accurate",
    "Passes %": "passes_pct",
    "expected_goals": "expected_goals",
    "goals_prevented": "goals_prevented",
}

MATCH_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "league_id",
    "league_code",
    "season",
    "round",
    "kickoff_utc",
    "status_short",
    "referee",
    "venue_id",
    "venue_name",
    "venue_city",
    "home_team_id",
    "home_team",
    "away_team_id",
    "away_team",
    "home_goals",
    "away_goals",
    "ht_home_goals",
    "ht_away_goals",
    "et_home_goals",
    "et_away_goals",
    "pen_home",
    "pen_away",
    "fetched_at",
    "batch_file",
)

TEAM_MATCH_STATS_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "team_id",
    "side",
    *TEAM_STAT_TYPES.values(),
)

EXTRA_TEAM_STATS_COLUMNS: tuple[str, ...] = ("fixture_id", "team_id", "stat_type", "value_text")

EVENT_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "seq",
    "elapsed",
    "extra",
    "team_id",
    "player_id",
    "player_name",
    "assist_id",
    "assist_name",
    "type",
    "detail",
    "comments",
)

LINEUP_COLUMNS: tuple[str, ...] = ("fixture_id", "team_id", "formation", "coach_id", "coach_name")

LINEUP_PLAYER_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "team_id",
    "player_id",
    "player_name",
    "number",
    "position",
    "grid",
    "is_starter",
)

PLAYER_MATCH_STATS_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "team_id",
    "player_id",
    "player_name",
    "minutes",
    "number",
    "position",
    "rating",
    "captain",
    "substitute",
    "offsides",
    "shots_total",
    "shots_on",
    "goals",
    "goals_conceded",
    "assists",
    "saves",
    "passes_total",
    "passes_key",
    "passes_accuracy",
    "tackles",
    "blocks",
    "interceptions",
    "duels_total",
    "duels_won",
    "dribbles_attempts",
    "dribbles_success",
    "dribbles_past",
    "fouls_drawn",
    "fouls_committed",
    "yellow_cards",
    "red_cards",
    "pen_won",
    "pen_committed",
    "pen_scored",
    "pen_missed",
    "pen_saved",
)

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "matches": MATCH_COLUMNS,
    "team_match_stats": TEAM_MATCH_STATS_COLUMNS,
    "extra_team_stats": EXTRA_TEAM_STATS_COLUMNS,
    "events": EVENT_COLUMNS,
    "lineups": LINEUP_COLUMNS,
    "lineup_players": LINEUP_PLAYER_COLUMNS,
    "player_match_stats": PLAYER_MATCH_STATS_COLUMNS,
}

DATA_TABLES: tuple[str, ...] = tuple(TABLE_COLUMNS)

SLIM_SKIPPED_TABLES: tuple[str, ...] = ("lineup_players", "player_match_stats")
"""Player-level tables a *slim* build leaves empty (``skip_tables``): about two thirds of the database.

Nothing the server runs reads them -- the live models and the count models read ``matches``,
``team_match_stats``, ``events`` (cards and whether a match has an event feed) and ``build_info``; settlement
reads ``matches`` and ``team_match_stats``; the scanner reads none of the player tables.  Only research code
(``download_extras.py``'s player scope, the quality report's line-up / player-stats shares) uses them, on the PC
with a full build.  The tables are still created (empty), so every query keeps working."""

_REAL_TEAM_STAT_COLUMNS = frozenset({"possession_pct", "passes_pct", "expected_goals", "goals_prevented"})

_TEAM_STAT_DDL = ",\n    ".join(
    f"{column} {'REAL' if column in _REAL_TEAM_STAT_COLUMNS else 'INTEGER'}"
    for column in TEAM_STAT_TYPES.values()
)

_TABLES_DDL = f"""
CREATE TABLE matches (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER,
    league_code TEXT,
    season INTEGER,
    round TEXT,
    kickoff_utc TEXT,
    status_short TEXT,
    referee TEXT,
    venue_id INTEGER,
    venue_name TEXT,
    venue_city TEXT,
    home_team_id INTEGER,
    home_team TEXT,
    away_team_id INTEGER,
    away_team TEXT,
    home_goals INTEGER,
    away_goals INTEGER,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    et_home_goals INTEGER,
    et_away_goals INTEGER,
    pen_home INTEGER,
    pen_away INTEGER,
    fetched_at TEXT,
    batch_file TEXT
);
CREATE TABLE team_match_stats (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    side TEXT,
    {_TEAM_STAT_DDL},
    PRIMARY KEY (fixture_id, team_id)
);
CREATE TABLE extra_team_stats (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    stat_type TEXT,
    value_text TEXT
);
CREATE TABLE events (
    fixture_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    elapsed INTEGER,
    extra INTEGER,
    team_id INTEGER,
    player_id INTEGER,
    player_name TEXT,
    assist_id INTEGER,
    assist_name TEXT,
    type TEXT,
    detail TEXT,
    comments TEXT
);
CREATE TABLE lineups (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    formation TEXT,
    coach_id INTEGER,
    coach_name TEXT,
    PRIMARY KEY (fixture_id, team_id)
);
CREATE TABLE lineup_players (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER,
    player_name TEXT,
    number INTEGER,
    position TEXT,
    grid TEXT,
    is_starter INTEGER NOT NULL
);
CREATE TABLE player_match_stats (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER,
    player_name TEXT,
    minutes INTEGER,
    number INTEGER,
    position TEXT,
    rating REAL,
    captain INTEGER,
    substitute INTEGER,
    offsides INTEGER,
    shots_total INTEGER,
    shots_on INTEGER,
    goals INTEGER,
    goals_conceded INTEGER,
    assists INTEGER,
    saves INTEGER,
    passes_total INTEGER,
    passes_key INTEGER,
    passes_accuracy REAL,
    tackles INTEGER,
    blocks INTEGER,
    interceptions INTEGER,
    duels_total INTEGER,
    duels_won INTEGER,
    dribbles_attempts INTEGER,
    dribbles_success INTEGER,
    dribbles_past INTEGER,
    fouls_drawn INTEGER,
    fouls_committed INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    pen_won INTEGER,
    pen_committed INTEGER,
    pen_scored INTEGER,
    pen_missed INTEGER,
    pen_saved INTEGER
);
CREATE TABLE build_info (
    schema_version TEXT NOT NULL,
    built_at TEXT NOT NULL,
    source_root TEXT NOT NULL,
    state_db_used INTEGER NOT NULL,
    batch_files INTEGER NOT NULL,
    fixtures_count INTEGER NOT NULL,
    fixtures_without_id INTEGER NOT NULL,
    duplicate_fixtures_skipped INTEGER NOT NULL,
    matches_rows INTEGER NOT NULL,
    team_match_stats_rows INTEGER NOT NULL,
    extra_team_stats_rows INTEGER NOT NULL,
    events_rows INTEGER NOT NULL,
    lineups_rows INTEGER NOT NULL,
    lineup_players_rows INTEGER NOT NULL,
    player_match_stats_rows INTEGER NOT NULL,
    build_seconds REAL,
    skipped_tables TEXT
);
"""

# Created after the bulk load: building indexes once over sorted data is much
# faster than maintaining them row by row during millions of inserts.
_INDEX_DDL = """
CREATE INDEX idx_matches_league_season ON matches (league_id, season);
CREATE INDEX idx_matches_kickoff ON matches (kickoff_utc);
CREATE INDEX idx_matches_home_team ON matches (home_team_id, kickoff_utc);
CREATE INDEX idx_matches_away_team ON matches (away_team_id, kickoff_utc);
CREATE INDEX idx_extra_team_stats_fixture ON extra_team_stats (fixture_id);
CREATE UNIQUE INDEX idx_events_fixture ON events (fixture_id, seq);
CREATE INDEX idx_lineup_players_fixture ON lineup_players (fixture_id, team_id);
CREATE INDEX idx_lineup_players_player ON lineup_players (player_id);
CREATE INDEX idx_player_match_stats_fixture ON player_match_stats (fixture_id, team_id);
CREATE INDEX idx_player_match_stats_player ON player_match_stats (player_id);
"""

_INSERT_SQL: dict[str, str] = {
    table: (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + column for column in columns)})"
    )
    for table, columns in TABLE_COLUMNS.items()
}

# Fallback when the archive has no state.sqlite3 (e.g. a copied subset):
# the downloader's own league catalog still maps league ids to codes.
_CATALOG_LEAGUE_CODES: dict[int, str] = {ls.league_id: ls.code for ls in all_planned_league_seasons()}
# Several wave-2 codes are ambiguous on their own (PREMIERLEAGUE is Belarus,
# PREMIERLEAGUE2 Russia...), so the quality report also shows the country.
_CATALOG_COUNTRIES: dict[int, str] = {ls.league_id: ls.country for ls in all_planned_league_seasons()}
# Report order: top-20 leagues first, then the later waves and cups, as planned.
_CATALOG_ORDER: dict[int, int] = {
    league_id: position
    for position, league_id in enumerate(dict.fromkeys(ls.league_id for ls in all_planned_league_seasons()))
}

_EMPTY: Mapping[str, Any] = {}


# --------------------------------------------------------------------------
# Scalar parsing
# --------------------------------------------------------------------------


def parse_number(value: Any) -> int | float | None:
    """Parse a provider numeric value, keeping ``None`` for anything missing.

    Integers pass through unchanged, numeric strings (``"1.23"``) and
    percentages (``"55%"``) become floats. Blank, non-numeric and non-finite
    values return ``None`` instead of a guessed number. Booleans are not
    numbers here: a ``true`` in a count field is a provider error, not ``1``.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].rstrip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def parse_float(value: Any) -> float | None:
    """Like :func:`parse_number` but always a float (ratings, xG, percentages)."""

    number = parse_number(value)
    return None if number is None else float(number)


def parse_int(value: Any) -> int | None:
    """Parse an identifier or goal count; non-integral values become ``None``."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    number = parse_number(value)
    if isinstance(number, float) and number.is_integer():
        return int(number)
    if isinstance(number, int):
        return number
    return None


def parse_flag(value: Any) -> int | None:
    """Map provider booleans to 1/0 for SQLite, keeping unknown as ``None``."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return 1
        if lowered in {"false", "0", "no"}:
            return 0
    return None


def _text(value: Any) -> str | None:
    """Non-blank text or ``None``; an empty provider string carries no information."""

    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _value_text(value: Any) -> str | None:
    """Lossless text form of an arbitrary raw stat value for the long table."""

    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else _EMPTY


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(block: Mapping[str, Any], *keys: str) -> Any:
    """First non-null value among alias keys (API-Football spells ``commited``)."""

    for key in keys:
        value = block.get(key)
        if value is not None:
            return value
    return None


def parse_kickoff_utc(fixture_block: Mapping[str, Any]) -> str | None:
    """Kickoff as an ISO-8601 UTC string (``2023-08-11T19:00:00+00:00``).

    The ISO ``date`` is authoritative and converted to UTC whatever offset it
    carries, so string ordering equals time ordering across the whole table.
    The unix ``timestamp`` is only a fallback. A naive date is accepted only
    when the provider explicitly labels the fixture timezone as UTC.
    """

    raw = fixture_block.get("date")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip())
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None and str(fixture_block.get("timezone") or "").upper() == "UTC":
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    timestamp = parse_int(fixture_block.get("timestamp"))
    if timestamp is not None:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")
    return None


# --------------------------------------------------------------------------
# Per-fixture parsers (pure: one fixture dict in, row dicts out)
# --------------------------------------------------------------------------


def fixture_id_of(fixture: Mapping[str, Any]) -> int | None:
    return parse_int(_obj(_obj(fixture).get("fixture")).get("id"))


def _require_fixture_id(fixture: Mapping[str, Any]) -> int:
    fixture_id = fixture_id_of(fixture)
    if fixture_id is None:
        raise ValueError("fixture has no fixture.id; it cannot be keyed")
    return fixture_id


def _team_ids(fixture: Mapping[str, Any]) -> tuple[int | None, int | None]:
    teams = _obj(fixture.get("teams"))
    return parse_int(_obj(teams.get("home")).get("id")), parse_int(_obj(teams.get("away")).get("id"))


def team_side(team_id: int | None, home_id: int | None, away_id: int | None) -> str | None:
    """'home'/'away' by matching the block's team id; ``None`` if it matches neither.

    The per-team lists (statistics, lineups, players) are *usually* home
    first, but that ordering is not documented, so side is never inferred
    from list position.
    """

    if team_id is None:
        return None
    if team_id == home_id:
        return "home"
    if team_id == away_id:
        return "away"
    return None


def parse_match(
    fixture: Mapping[str, Any],
    *,
    league_code: str | None = None,
    fetched_at: str | None = None,
    batch_file: str | None = None,
) -> dict[str, Any]:
    """One ``matches`` row. Provenance fields are supplied by the caller."""

    fixture_id = _require_fixture_id(fixture)
    block = _obj(fixture.get("fixture"))
    venue = _obj(block.get("venue"))
    status = _obj(block.get("status"))
    league = _obj(fixture.get("league"))
    teams = _obj(fixture.get("teams"))
    home = _obj(teams.get("home"))
    away = _obj(teams.get("away"))
    goals = _obj(fixture.get("goals"))
    score = _obj(fixture.get("score"))
    halftime = _obj(score.get("halftime"))
    extratime = _obj(score.get("extratime"))
    penalty = _obj(score.get("penalty"))
    return {
        "fixture_id": fixture_id,
        "league_id": parse_int(league.get("id")),
        "league_code": league_code,
        "season": parse_int(league.get("season")),
        "round": _text(league.get("round")),
        "kickoff_utc": parse_kickoff_utc(block),
        "status_short": _text(status.get("short")),
        "referee": _text(block.get("referee")),
        "venue_id": parse_int(venue.get("id")),
        "venue_name": _text(venue.get("name")),
        "venue_city": _text(venue.get("city")),
        "home_team_id": parse_int(home.get("id")),
        "home_team": _text(home.get("name")),
        "away_team_id": parse_int(away.get("id")),
        "away_team": _text(away.get("name")),
        "home_goals": parse_int(goals.get("home")),
        "away_goals": parse_int(goals.get("away")),
        "ht_home_goals": parse_int(halftime.get("home")),
        "ht_away_goals": parse_int(halftime.get("away")),
        "et_home_goals": parse_int(extratime.get("home")),
        "et_away_goals": parse_int(extratime.get("away")),
        "pen_home": parse_int(penalty.get("home")),
        "pen_away": parse_int(penalty.get("away")),
        "fetched_at": fetched_at,
        "batch_file": batch_file,
    }


def _team_stat_rows(fixture: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fixture_id = _require_fixture_id(fixture)
    home_id, away_id = _team_ids(fixture)
    stat_rows: list[dict[str, Any]] = []
    extra_rows: list[dict[str, Any]] = []
    seen_teams: set[int] = set()
    for entry in _list(fixture.get("statistics")):
        entry = _obj(entry)
        team_id = parse_int(_obj(entry.get("team")).get("id"))
        items = _list(entry.get("statistics"))
        # An empty per-team list means the provider has no statistics for
        # that match; emitting an all-NULL row would falsely suggest
        # coverage. A block without a team id cannot be attributed.
        if team_id is None or not items or team_id in seen_teams:
            continue
        seen_teams.add(team_id)
        row: dict[str, Any] = dict.fromkeys(TEAM_MATCH_STATS_COLUMNS)
        row["fixture_id"] = fixture_id
        row["team_id"] = team_id
        row["side"] = team_side(team_id, home_id, away_id)
        filled: set[str] = set()
        for item in items:
            item = _obj(item)
            stat_type = item.get("type")
            value = item.get("value")
            column = TEAM_STAT_TYPES.get(stat_type) if isinstance(stat_type, str) else None
            if column is not None and column not in filled:
                parsed = parse_number(value)
                if parsed is not None or value is None:
                    filled.add(column)
                    row[column] = parsed
                    continue
            # Unknown stat types, repeated types and unparseable values are
            # kept verbatim rather than dropped or guessed.
            extra_rows.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "stat_type": _text(stat_type),
                    "value_text": _value_text(value),
                }
            )
        stat_rows.append(row)
    return stat_rows, extra_rows


def parse_team_match_stats(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ``team_match_stats`` row per team with a non-empty statistics list."""

    return _team_stat_rows(fixture)[0]


def parse_extra_team_stats(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Long-format rows for stat types with no typed column (never dropped)."""

    return _team_stat_rows(fixture)[1]


def parse_events(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Events in provider order; ``seq`` is the 0-based position in that list.

    Provider order is kept because events sharing the same minute (e.g. a
    goal and a VAR cancellation) are only disambiguated by it.
    """

    fixture_id = _require_fixture_id(fixture)
    rows: list[dict[str, Any]] = []
    for seq, event in enumerate(_list(fixture.get("events"))):
        event = _obj(event)
        clock = _obj(event.get("time"))
        team = _obj(event.get("team"))
        player = _obj(event.get("player"))
        assist = _obj(event.get("assist"))
        rows.append(
            {
                "fixture_id": fixture_id,
                "seq": seq,
                "elapsed": parse_int(clock.get("elapsed")),
                "extra": parse_int(clock.get("extra")),
                "team_id": parse_int(team.get("id")),
                "player_id": parse_int(player.get("id")),
                "player_name": _text(player.get("name")),
                "assist_id": parse_int(assist.get("id")),
                "assist_name": _text(assist.get("name")),
                "type": _text(event.get("type")),
                "detail": _text(event.get("detail")),
                "comments": _text(event.get("comments")),
            }
        )
    return rows


def _lineup_rows(fixture: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fixture_id = _require_fixture_id(fixture)
    lineups: list[dict[str, Any]] = []
    players: list[dict[str, Any]] = []
    seen_teams: set[int] = set()
    for entry in _list(fixture.get("lineups")):
        entry = _obj(entry)
        team_id = parse_int(_obj(entry.get("team")).get("id"))
        if team_id is None or team_id in seen_teams:
            continue
        seen_teams.add(team_id)
        coach = _obj(entry.get("coach"))
        lineups.append(
            {
                "fixture_id": fixture_id,
                "team_id": team_id,
                "formation": _text(entry.get("formation")),
                "coach_id": parse_int(coach.get("id")),
                "coach_name": _text(coach.get("name")),
            }
        )
        for is_starter, key in ((1, "startXI"), (0, "substitutes")):
            for slot in _list(entry.get(key)):
                player = _obj(_obj(slot).get("player"))
                players.append(
                    {
                        "fixture_id": fixture_id,
                        "team_id": team_id,
                        "player_id": parse_int(player.get("id")),
                        "player_name": _text(player.get("name")),
                        "number": parse_int(player.get("number")),
                        "position": _text(player.get("pos")),
                        "grid": _text(player.get("grid")),
                        "is_starter": is_starter,
                    }
                )
    return lineups, players


def parse_lineups(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ``lineups`` row per team block (formation and coach)."""

    return _lineup_rows(fixture)[0]


def parse_lineup_players(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Starting XI (``is_starter=1``) then substitutes (``0``), per team."""

    return _lineup_rows(fixture)[1]


def parse_player_match_stats(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per listed player; only the first statistics entry is used.

    For a single fixture API-Football returns exactly one statistics entry
    per player (verified across the whole 2015-2025 archive). A player listed
    with no statistics entry still gets a row with all stats ``NULL``, since
    being listed is itself information.
    """

    fixture_id = _require_fixture_id(fixture)
    rows: list[dict[str, Any]] = []
    for entry in _list(fixture.get("players")):
        entry = _obj(entry)
        team_id = parse_int(_obj(entry.get("team")).get("id"))
        if team_id is None:
            continue
        for listed in _list(entry.get("players")):
            listed = _obj(listed)
            identity = _obj(listed.get("player"))
            stats_list = _list(listed.get("statistics"))
            stats = _obj(stats_list[0]) if stats_list else _EMPTY
            games = _obj(stats.get("games"))
            shots = _obj(stats.get("shots"))
            goals = _obj(stats.get("goals"))
            passes = _obj(stats.get("passes"))
            tackles = _obj(stats.get("tackles"))
            duels = _obj(stats.get("duels"))
            dribbles = _obj(stats.get("dribbles"))
            fouls = _obj(stats.get("fouls"))
            cards = _obj(stats.get("cards"))
            penalty = _obj(stats.get("penalty"))
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "player_id": parse_int(identity.get("id")),
                    "player_name": _text(identity.get("name")),
                    "minutes": parse_number(games.get("minutes")),
                    "number": parse_int(games.get("number")),
                    "position": _text(games.get("position")),
                    "rating": parse_float(games.get("rating")),
                    "captain": parse_flag(games.get("captain")),
                    "substitute": parse_flag(games.get("substitute")),
                    "offsides": parse_number(stats.get("offsides")),
                    "shots_total": parse_number(shots.get("total")),
                    "shots_on": parse_number(shots.get("on")),
                    "goals": parse_number(goals.get("total")),
                    "goals_conceded": parse_number(goals.get("conceded")),
                    "assists": parse_number(goals.get("assists")),
                    "saves": parse_number(goals.get("saves")),
                    "passes_total": parse_number(passes.get("total")),
                    "passes_key": parse_number(passes.get("key")),
                    # Provider string; for players it is usually the count of
                    # accurate passes, not a percentage, despite the name.
                    "passes_accuracy": parse_float(passes.get("accuracy")),
                    "tackles": parse_number(tackles.get("total")),
                    "blocks": parse_number(tackles.get("blocks")),
                    "interceptions": parse_number(tackles.get("interceptions")),
                    "duels_total": parse_number(duels.get("total")),
                    "duels_won": parse_number(duels.get("won")),
                    "dribbles_attempts": parse_number(dribbles.get("attempts")),
                    "dribbles_success": parse_number(dribbles.get("success")),
                    "dribbles_past": parse_number(dribbles.get("past")),
                    "fouls_drawn": parse_number(fouls.get("drawn")),
                    "fouls_committed": parse_number(fouls.get("committed")),
                    "yellow_cards": parse_number(cards.get("yellow")),
                    "red_cards": parse_number(cards.get("red")),
                    "pen_won": parse_number(penalty.get("won")),
                    "pen_committed": parse_number(_first_present(penalty, "commited", "committed")),
                    "pen_scored": parse_number(penalty.get("scored")),
                    "pen_missed": parse_number(penalty.get("missed")),
                    "pen_saved": parse_number(penalty.get("saved")),
                }
            )
    return rows


@dataclass(frozen=True)
class FixtureRows:
    """Every table's rows for one fixture."""

    match: dict[str, Any]
    team_match_stats: list[dict[str, Any]]
    extra_team_stats: list[dict[str, Any]]
    events: list[dict[str, Any]]
    lineups: list[dict[str, Any]]
    lineup_players: list[dict[str, Any]]
    player_match_stats: list[dict[str, Any]]


def parse_fixture(
    fixture: Mapping[str, Any],
    *,
    league_code: str | None = None,
    fetched_at: str | None = None,
    batch_file: str | None = None,
) -> FixtureRows:
    """Parse one fixture into rows for all tables (each parser run once)."""

    team_stats, extra_stats = _team_stat_rows(fixture)
    lineups, lineup_players = _lineup_rows(fixture)
    return FixtureRows(
        match=parse_match(fixture, league_code=league_code, fetched_at=fetched_at, batch_file=batch_file),
        team_match_stats=team_stats,
        extra_team_stats=extra_stats,
        events=parse_events(fixture),
        lineups=lineups,
        lineup_players=lineup_players,
        player_match_stats=parse_player_match_stats(fixture),
    )


# --------------------------------------------------------------------------
# Archive -> SQLite builder
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveProvenance:
    """League codes and fetch times from the downloader's ``state.sqlite3``."""

    league_codes: Mapping[tuple[int, int], str]
    fetched_at: Mapping[int, str]
    state_db_used: bool

    def league_code(self, league_id: int | None, season: int | None) -> str | None:
        if league_id is None:
            return None
        code = self.league_codes.get((league_id, season)) if season is not None else None
        return code if code is not None else _CATALOG_LEAGUE_CODES.get(league_id)


def load_archive_provenance(archive_root: str | Path) -> ArchiveProvenance:
    """Read provenance from ``state.sqlite3`` strictly read-only.

    The URI ``mode=ro`` guarantees the builder can never modify the
    downloader's progress database, even by accident. An archive without a
    state file (e.g. a copied subset) still builds, with catalog league
    codes and ``NULL`` fetch times.
    """

    state_path = Path(archive_root) / "state.sqlite3"
    if not state_path.is_file():
        return ArchiveProvenance(league_codes={}, fetched_at={}, state_db_used=False)
    uri = f"{state_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        league_codes = {
            (int(league_id), int(season)): str(code)
            for league_id, season, code in conn.execute(
                "SELECT league_id, season, code FROM league_season_status"
            )
            if code is not None
        }
        fetched_at = {
            int(fixture_id): str(value)
            for fixture_id, value in conn.execute(
                "SELECT fixture_id, fetched_at FROM fixture_status WHERE fetched_at IS NOT NULL"
            )
        }
    return ArchiveProvenance(league_codes=league_codes, fetched_at=fetched_at, state_db_used=True)


def _numeric_name_key(name: str) -> tuple[int, int, str]:
    return (0, int(name), "") if name.isdigit() else (1, 0, name)


def list_batch_files(archive_root: str | Path) -> list[Path]:
    """All ``raw/{league}/{season}/batches/*.json.gz`` files in stable order.

    Ordered numerically by league id, season, then file name, so a rebuild
    always processes files identically regardless of filesystem ordering.
    """

    raw_dir = Path(archive_root) / "raw"
    files = [path for path in raw_dir.glob("*/*/batches/*.json.gz") if path.is_file()]
    return sorted(
        files,
        key=lambda path: (
            _numeric_name_key(path.parent.parent.parent.name),
            _numeric_name_key(path.parent.parent.name),
            path.name,
        ),
    )


def read_batch_file(path: Path) -> list[Any]:
    """The ``response`` list of one raw batch file."""

    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, ValueError) as exc:
        raise ValueError(f"cannot read batch file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"batch file {path} is not a JSON object")
    return _list(payload.get("response"))


@dataclass(frozen=True)
class BuildReport:
    out_path: Path
    source_root: Path
    built_at: str
    state_db_used: bool
    batch_files: int
    fixtures: int
    fixtures_without_id: int
    duplicate_fixtures_skipped: int
    row_counts: dict[str, int]
    seconds: float
    skipped_tables: tuple[str, ...] = ()


ProgressCallback = Callable[[int, int, int], None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _flush(conn: sqlite3.Connection, buffers: dict[str, list[dict[str, Any]]]) -> None:
    for table, rows in buffers.items():
        if rows:
            conn.executemany(_INSERT_SQL[table], rows)
            rows.clear()
    conn.commit()


def build_history_tables(
    archive_root: str | Path,
    out_path: str | Path,
    *,
    files_per_transaction: int = DEFAULT_FILES_PER_TRANSACTION,
    progress: ProgressCallback | None = None,
    now: Callable[[], str] = _utc_now_iso,
    skip_tables: Sequence[str] = (),
) -> BuildReport:
    """Rebuild the whole analysis database from the raw archive.

    Always a full rebuild into a temporary file next to ``out_path`` that is
    moved over the destination with :func:`os.replace` only after every
    table and index is written. A crash, a corrupt batch file or Ctrl+C
    therefore leaves the previous database untouched (and the temp file is
    removed). Because nothing is appended incrementally, running the build
    twice produces identical content.

    ``progress(files_done, files_total, fixtures_done)`` is called after each
    committed chunk of ``files_per_transaction`` batch files.

    ``skip_tables`` (e.g. :data:`SLIM_SKIPPED_TABLES`) leaves those data tables
    empty: they are created with their indexes but no row is parsed into them,
    and ``build_info.skipped_tables`` lists them (comma separated; NULL for a
    full build). ``matches`` cannot be skipped.
    """

    if files_per_transaction < 1:
        raise ValueError("files_per_transaction must be positive")
    skipped = tuple(dict.fromkeys(str(table) for table in skip_tables))
    unknown = [table for table in skipped if table not in DATA_TABLES]
    if unknown:
        raise ValueError(f"unknown table(s) to skip: {', '.join(unknown)}")
    if "matches" in skipped:
        raise ValueError("the matches table cannot be skipped")
    kept_tables = tuple(table for table in DATA_TABLES if table not in skipped)
    started = time.perf_counter()
    root = Path(archive_root)
    out = Path(out_path)
    files = list_batch_files(root)
    if not files:
        # Fail closed: never replace a good database with an empty one just
        # because the archive path was mistyped.
        raise FileNotFoundError(f"no batch files found under {root / 'raw'}")
    provenance = load_archive_provenance(root)
    out.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".building", dir=out.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        with closing(sqlite3.connect(tmp_path)) as conn:
            # Durability pragmas are safe to relax: a crash mid-build only
            # ever damages the throwaway temp file, never ``out_path``.
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=OFF")
            conn.executescript(_TABLES_DDL)

            buffers: dict[str, list[dict[str, Any]]] = {table: [] for table in kept_tables}
            seen: set[int] = set()
            fixtures = 0
            without_id = 0
            duplicates = 0
            for index, path in enumerate(files, start=1):
                batch_file = path.relative_to(root).as_posix()
                for fixture in read_batch_file(path):
                    fixture = _obj(fixture)
                    fixture_id = fixture_id_of(fixture)
                    if fixture_id is None:
                        without_id += 1
                        continue
                    if fixture_id in seen:
                        # Fixture ids are globally unique; a repeat can only
                        # be a re-fetched copy. Files are processed in a
                        # fixed order, so the kept copy is deterministic.
                        duplicates += 1
                        continue
                    seen.add(fixture_id)
                    league = _obj(fixture.get("league"))
                    rows = parse_fixture(
                        fixture,
                        league_code=provenance.league_code(
                            parse_int(league.get("id")), parse_int(league.get("season"))
                        ),
                        fetched_at=provenance.fetched_at.get(fixture_id),
                        batch_file=batch_file,
                    )
                    buffers["matches"].append(rows.match)
                    for table in kept_tables[1:]:
                        buffers[table].extend(getattr(rows, table))
                    fixtures += 1
                if index % files_per_transaction == 0 or index == len(files):
                    _flush(conn, buffers)
                    if progress is not None:
                        progress(index, len(files), fixtures)

            conn.executescript(_INDEX_DDL)
            row_counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in DATA_TABLES
            }
            built_at = now()
            seconds = round(time.perf_counter() - started, 3)
            conn.execute(
                """INSERT INTO build_info (
                       schema_version, built_at, source_root, state_db_used, batch_files,
                       fixtures_count, fixtures_without_id, duplicate_fixtures_skipped,
                       matches_rows, team_match_stats_rows, extra_team_stats_rows, events_rows,
                       lineups_rows, lineup_players_rows, player_match_stats_rows, build_seconds,
                       skipped_tables
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    SCHEMA_VERSION,
                    built_at,
                    str(root.resolve()),
                    int(provenance.state_db_used),
                    len(files),
                    fixtures,
                    without_id,
                    duplicates,
                    *(row_counts[table] for table in DATA_TABLES),
                    seconds,
                    ",".join(skipped) or None,
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

    return BuildReport(
        out_path=out,
        source_root=root,
        built_at=built_at,
        state_db_used=provenance.state_db_used,
        batch_files=len(files),
        fixtures=fixtures,
        fixtures_without_id=without_id,
        duplicate_fixtures_skipped=duplicates,
        row_counts=row_counts,
        seconds=round(time.perf_counter() - started, 3),
        skipped_tables=skipped,
    )


# --------------------------------------------------------------------------
# Data-quality summary
# --------------------------------------------------------------------------

QUALITY_SEASON_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("2015-2018", 2015, 2018),
    ("2019-2022", 2019, 2022),
    ("2023-2025", 2023, 2025),
)
PLAYED_STATUSES: tuple[str, ...] = ("FT", "AET", "PEN")
QUALITY_METRICS: tuple[str, ...] = (
    "corners",
    "shots",
    "shots_on_goal",
    "expected_goals",
    "lineups",
    "player_stats",
    "events",
)
_METRIC_LABELS: dict[str, str] = {
    "corners": "Corners",
    "shots": "Shots",
    "shots_on_goal": "SoT",
    "expected_goals": "xG",
    "lineups": "Lineups",
    "player_stats": "Player stats",
    "events": "Events",
}


def _quality_sql() -> str:
    bucket_case = " ".join(
        f"WHEN m.season BETWEEN {low} AND {high} THEN '{label}'" for label, low, high in QUALITY_SEASON_BUCKETS
    )
    played = ", ".join(f"'{status}'" for status in PLAYED_STATUSES)
    shares = ",\n               ".join(
        f"CAST(SUM(played AND {metric}) AS REAL) / NULLIF(SUM(played), 0) AS {metric}"
        for metric in QUALITY_METRICS
    )
    return f"""
        WITH ts AS (
            SELECT fixture_id,
                   SUM(corners IS NOT NULL) AS corners,
                   SUM(total_shots IS NOT NULL) AS shots,
                   SUM(shots_on_goal IS NOT NULL) AS shots_on_goal,
                   SUM(expected_goals IS NOT NULL) AS expected_goals
            FROM team_match_stats
            WHERE side IS NOT NULL
            GROUP BY fixture_id
        ),
        lu AS (
            SELECT fixture_id, COUNT(DISTINCT team_id) AS teams
            FROM lineup_players WHERE is_starter = 1
            GROUP BY fixture_id
        ),
        ps AS (
            SELECT fixture_id, COUNT(DISTINCT team_id) AS teams
            FROM player_match_stats WHERE minutes IS NOT NULL
            GROUP BY fixture_id
        ),
        ev AS (SELECT DISTINCT fixture_id FROM events),
        base AS (
            SELECT m.league_id,
                   m.league_code,
                   m.season,
                   CASE {bucket_case} ELSE 'other' END AS season_bucket,
                   COALESCE(m.status_short IN ({played}), 0) AS played,
                   COALESCE(ts.corners, 0) >= 2 AS corners,
                   COALESCE(ts.shots, 0) >= 2 AS shots,
                   COALESCE(ts.shots_on_goal, 0) >= 2 AS shots_on_goal,
                   COALESCE(ts.expected_goals, 0) >= 2 AS expected_goals,
                   COALESCE(lu.teams, 0) >= 2 AS lineups,
                   COALESCE(ps.teams, 0) >= 2 AS player_stats,
                   ev.fixture_id IS NOT NULL AS events
            FROM matches AS m
            LEFT JOIN ts ON ts.fixture_id = m.fixture_id
            LEFT JOIN lu ON lu.fixture_id = m.fixture_id
            LEFT JOIN ps ON ps.fixture_id = m.fixture_id
            LEFT JOIN ev ON ev.fixture_id = m.fixture_id
        )
        SELECT league_id,
               league_code,
               season_bucket,
               MIN(season) AS first_season,
               MAX(season) AS last_season,
               SUM(played) AS played,
               COUNT(*) - SUM(played) AS not_played,
               {shares}
        FROM base
        GROUP BY league_id, league_code, season_bucket
    """


def quality_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per league code and season bucket: share of played matches with data.

    A metric counts for a match only when present (non-NULL) for BOTH
    teams, because a model feature needs both sides: corners, total shots,
    shots on goal and expected goals from ``team_match_stats``; lineups =
    both teams list at least one starter; player stats = both teams have a
    player row with non-NULL minutes; events = at least one event row.
    Shares are over played matches only (FT/AET/PEN); postponed, cancelled
    and awarded fixtures are counted separately as ``not_played``.
    """

    cursor = conn.execute(_quality_sql())
    columns = [description[0] for description in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    bucket_order = {label: index for index, (label, _, _) in enumerate(QUALITY_SEASON_BUCKETS)}
    rows.sort(
        key=lambda row: (
            _CATALOG_ORDER.get(row["league_id"], len(_CATALOG_ORDER)),
            row["league_code"] or "",
            row["league_id"] or 0,
            bucket_order.get(row["season_bucket"], len(bucket_order)),
        )
    )
    return rows


def read_build_info(conn: sqlite3.Connection) -> dict[str, Any]:
    cursor = conn.execute("SELECT * FROM build_info")
    columns = [description[0] for description in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row is not None else {}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _league_label(row: Mapping[str, Any]) -> str:
    return str(row["league_code"] or f"league {row['league_id']}")


def render_quality_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    build_info: Mapping[str, Any] | None = None,
    gap_threshold: float = 0.9,
) -> str:
    """Markdown report: gap highlights first, then the full coverage table."""

    lines: list[str] = ["# API-Football history tables: data quality", ""]
    if build_info:
        lines += [
            f"- Built at: {build_info.get('built_at')} (schema `{build_info.get('schema_version')}`)",
            f"- Source archive: `{build_info.get('source_root')}`",
            f"- Batch files: {build_info.get('batch_files')}; fixtures: {build_info.get('fixtures_count')}"
            f"; duplicates skipped: {build_info.get('duplicate_fixtures_skipped')}"
            f"; without id: {build_info.get('fixtures_without_id')}",
            "- Rows: "
            + ", ".join(f"{table}={build_info.get(f'{table}_rows')}" for table in DATA_TABLES),
            "",
        ]
    lines += [
        "Each share is the fraction of **played** matches (status "
        + "/".join(PLAYED_STATUSES)
        + ") where the field is present (non-NULL) for **both** teams. "
        "Lineups = both teams list at least one starter; player stats = both teams have a player row "
        "with non-NULL minutes; events = at least one event row. Fixtures with any other status "
        "(cancelled, postponed, awarded, walkover) are reported as *not played* and excluded from shares. "
        "NULL means the provider did not supply the value; it is never treated as zero.",
        "",
        f"## Gaps (share below {gap_threshold * 100:.0f}%)",
        "",
    ]
    any_gap = False
    for metric in QUALITY_METRICS:
        gaps = [
            f"{_league_label(row)} {row['season_bucket']} ({_pct(row[metric])})"
            for row in rows
            if row["played"] and row[metric] is not None and row[metric] < gap_threshold
        ]
        if gaps:
            any_gap = True
            lines.append(f"- **{_METRIC_LABELS[metric]}** ({len(gaps)}): " + ", ".join(gaps))
    if not any_gap:
        lines.append("- None.")
    lines += ["", "## Coverage per league and season bucket", ""]
    header = [
        "League",
        "Country",
        "Bucket",
        "Seasons",
        "Played",
        "Not played",
        *(_METRIC_LABELS[m] for m in QUALITY_METRICS),
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---", "---", "---", "---", "---:", "---:", *(["---:"] * len(QUALITY_METRICS))]) + "|")
    for row in rows:
        seasons = (
            str(row["first_season"])
            if row["first_season"] == row["last_season"]
            else f"{row['first_season']}-{row['last_season']}"
        )
        cells = [
            _league_label(row),
            _CATALOG_COUNTRIES.get(row["league_id"], ""),
            str(row["season_bucket"]),
            seasons,
            str(row["played"]),
            str(row["not_played"]),
            *(_pct(row[metric]) for metric in QUALITY_METRICS),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_quality_report(db_path: str | Path, report_path: str | Path, *, gap_threshold: float = 0.9) -> str:
    """Render the quality report for a built database and write it as markdown."""

    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        text = render_quality_report(
            quality_summary(conn), build_info=read_build_info(conn), gap_threshold=gap_threshold
        )
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return text


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_FILES_PER_TRANSACTION",
    "TEAM_STAT_TYPES",
    "TABLE_COLUMNS",
    "DATA_TABLES",
    "SLIM_SKIPPED_TABLES",
    "parse_number",
    "parse_float",
    "parse_int",
    "parse_flag",
    "parse_kickoff_utc",
    "fixture_id_of",
    "team_side",
    "parse_match",
    "parse_team_match_stats",
    "parse_extra_team_stats",
    "parse_events",
    "parse_lineups",
    "parse_lineup_players",
    "parse_player_match_stats",
    "FixtureRows",
    "parse_fixture",
    "ArchiveProvenance",
    "load_archive_provenance",
    "list_batch_files",
    "read_batch_file",
    "BuildReport",
    "build_history_tables",
    "QUALITY_SEASON_BUCKETS",
    "PLAYED_STATUSES",
    "QUALITY_METRICS",
    "quality_summary",
    "read_build_info",
    "render_quality_report",
    "write_quality_report",
]
