from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from model_buk.ingestion.api_football_history import HistoryStore
from model_buk.ingestion.api_football_tables import (
    DATA_TABLES,
    SCHEMA_VERSION,
    TABLE_COLUMNS,
    build_history_tables,
    parse_events,
    parse_extra_team_stats,
    parse_fixture,
    parse_flag,
    parse_int,
    parse_lineup_players,
    parse_lineups,
    parse_match,
    parse_number,
    parse_player_match_stats,
    parse_team_match_stats,
    quality_summary,
    render_quality_report,
    team_side,
    write_quality_report,
)

HOME_ID = 10
AWAY_ID = 20


def stat_block(team_id: int, **overrides):
    """A realistic per-team statistics block; ``overrides`` map type -> value."""

    values = {
        "Shots on Goal": 4,
        "Shots off Goal": 3,
        "Total Shots": 9,
        "Blocked Shots": 2,
        "Shots insidebox": 6,
        "Shots outsidebox": 3,
        "Fouls": 11,
        "Corner Kicks": 5,
        "Offsides": 1,
        "Ball Possession": "55%",
        "Yellow Cards": 2,
        "Red Cards": None,
        "Goalkeeper Saves": 3,
        "Total passes": 400,
        "Passes accurate": 320,
        "Passes %": "80%",
        "expected_goals": "1.23",
    }
    values.update(overrides)
    return {
        "team": {"id": team_id, "name": f"Team {team_id}", "logo": "x.png"},
        "statistics": [{"type": key, "value": value} for key, value in values.items()],
    }


def player_entry(player_id: int, *, rating="7.5", captain=False, minutes=90, statistics=None):
    if statistics is None:
        statistics = [
            {
                "games": {
                    "minutes": minutes,
                    "number": 9,
                    "position": "F",
                    "rating": rating,
                    "captain": captain,
                    "substitute": False,
                },
                "offsides": None,
                "shots": {"total": 3, "on": None},
                "goals": {"total": 1, "conceded": 0, "assists": None, "saves": None},
                "passes": {"total": 20, "key": 2, "accuracy": "15"},
                "tackles": {"total": None, "blocks": None, "interceptions": 1},
                "duels": {"total": 10, "won": 6},
                "dribbles": {"attempts": 2, "success": 1, "past": None},
                "fouls": {"drawn": 1, "committed": 0},
                "cards": {"yellow": 0, "red": 0},
                "penalty": {"won": None, "commited": 1, "scored": 0, "missed": 0, "saved": None},
            }
        ]
    return {"player": {"id": player_id, "name": f"Player {player_id}", "photo": "p.png"}, "statistics": statistics}


def make_fixture(
    fixture_id: int = 1001,
    *,
    league_id: int = 39,
    season: int = 2023,
    status: str = "FT",
    date: str = "2023-08-11T19:00:00+00:00",
    statistics=None,
    events=None,
    lineups=None,
    players=None,
    omit: tuple[str, ...] = (),
):
    fixture = {
        "fixture": {
            "id": fixture_id,
            "referee": "C. Pawson",
            "timezone": "UTC",
            "date": date,
            "timestamp": 1691780400,
            "periods": {"first": 1691780400, "second": 1691784000},
            "venue": {"id": 512, "name": "Turf Moor", "city": "Burnley"},
            "status": {"long": "Match Finished", "short": status, "elapsed": 90, "extra": None},
        },
        "league": {"id": league_id, "name": "Premier League", "country": "England", "season": season, "round": "Regular Season - 1"},
        "teams": {
            "home": {"id": HOME_ID, "name": "Home FC", "winner": False},
            "away": {"id": AWAY_ID, "name": "Away FC", "winner": True},
        },
        "goals": {"home": 0, "away": 3},
        "score": {
            "halftime": {"home": 0, "away": 2},
            "fulltime": {"home": 0, "away": 3},
            "extratime": {"home": None, "away": None},
            "penalty": {"home": None, "away": None},
        },
        "events": events
        if events is not None
        else [
            {
                "time": {"elapsed": 4, "extra": None},
                "team": {"id": AWAY_ID, "name": "Away FC"},
                "player": {"id": 1100, "name": "E. Haaland"},
                "assist": {"id": 44, "name": "Rodri"},
                "type": "Goal",
                "detail": "Normal Goal",
                "comments": None,
            }
        ],
        "lineups": lineups
        if lineups is not None
        else [
            {
                "team": {"id": HOME_ID, "name": "Home FC"},
                "coach": {"id": 7, "name": "Coach Home"},
                "formation": "4-4-2",
                "startXI": [{"player": {"id": 1, "name": "Keeper", "number": 1, "pos": "G", "grid": "1:1"}}],
                "substitutes": [{"player": {"id": 2, "name": "Sub", "number": 12, "pos": "D", "grid": None}}],
            },
            {
                "team": {"id": AWAY_ID, "name": "Away FC"},
                "coach": {"id": 8, "name": "Coach Away"},
                "formation": "4-3-3",
                "startXI": [{"player": {"id": 3, "name": "Striker", "number": 9, "pos": "F", "grid": "4:2"}}],
                "substitutes": [],
            },
        ],
        "statistics": statistics if statistics is not None else [stat_block(HOME_ID), stat_block(AWAY_ID)],
        "players": players
        if players is not None
        else [
            {"team": {"id": HOME_ID, "name": "Home FC", "update": "2024-05-21T04:12:03+00:00"}, "players": [player_entry(1)]},
            {"team": {"id": AWAY_ID, "name": "Away FC", "update": "2024-05-21T04:12:03+00:00"}, "players": [player_entry(3)]},
        ],
    }
    for key in omit:
        fixture.pop(key, None)
    return fixture


# ---------------------------------------------------------------- scalars


def test_parse_number_handles_percent_numeric_strings_and_garbage():
    assert parse_number("55%") == 55.0 and isinstance(parse_number("55%"), float)
    assert parse_number(" 80 % ") == 80.0
    assert parse_number("1.23") == 1.23
    assert parse_number("-0.27") == -0.27
    assert parse_number(7) == 7 and isinstance(parse_number(7), int)
    assert parse_number(0) == 0
    for missing in (None, "", "  ", "%", "n/a", "nan", "inf", True, False, [], {}):
        assert parse_number(missing) is None, missing

    assert parse_int("12") == 12
    assert parse_int(3.0) == 3
    assert parse_int("1.5") is None
    assert parse_int(None) is None
    assert parse_flag(True) == 1 and parse_flag(False) == 0
    assert parse_flag("true") == 1
    assert parse_flag(None) is None and parse_flag("maybe") is None


def test_team_side_matches_ids_not_list_position():
    assert team_side(HOME_ID, HOME_ID, AWAY_ID) == "home"
    assert team_side(AWAY_ID, HOME_ID, AWAY_ID) == "away"
    assert team_side(99, HOME_ID, AWAY_ID) is None
    assert team_side(None, HOME_ID, AWAY_ID) is None


# ----------------------------------------------------------- team stats


def test_team_stats_parse_percent_and_numeric_strings():
    rows = parse_team_match_stats(make_fixture())
    assert len(rows) == 2
    home = rows[0]
    assert set(home) == set(TABLE_COLUMNS["team_match_stats"])
    assert home["possession_pct"] == 55.0
    assert home["passes_pct"] == 80.0
    assert home["expected_goals"] == 1.23
    assert home["corners"] == 5
    assert home["total_shots"] == 9


def test_team_stats_nulls_stay_null_and_real_zeros_stay_zero():
    fixture = make_fixture(
        statistics=[stat_block(HOME_ID, **{"Corner Kicks": None, "Yellow Cards": 0, "expected_goals": None})]
    )
    (row,) = parse_team_match_stats(fixture)
    assert row["corners"] is None
    assert row["red_cards"] is None
    assert row["expected_goals"] is None
    # goals_prevented is absent from the provider list entirely -> NULL, not 0.
    assert row["goals_prevented"] is None
    assert row["yellow_cards"] == 0


def test_side_detection_uses_team_ids_even_when_order_is_swapped():
    fixture = make_fixture(statistics=[stat_block(AWAY_ID), stat_block(HOME_ID), stat_block(77)])
    sides = {row["team_id"]: row["side"] for row in parse_team_match_stats(fixture)}
    assert sides == {AWAY_ID: "away", HOME_ID: "home", 77: None}


def test_unknown_stat_type_and_unparseable_value_go_to_extra_team_stats():
    fixture = make_fixture(
        statistics=[stat_block(HOME_ID, **{"Throwins": 12, "Medical Treatment": None, "Corner Kicks": "lots"})]
    )
    (row,) = parse_team_match_stats(fixture)
    assert row["corners"] is None  # unparseable, never guessed
    extra = parse_extra_team_stats(fixture)
    assert {(r["stat_type"], r["value_text"]) for r in extra} == {
        ("Throwins", "12"),
        ("Medical Treatment", None),
        ("Corner Kicks", "lots"),
    }
    assert all(r["team_id"] == HOME_ID and r["fixture_id"] == 1001 for r in extra)
    assert all(set(r) == set(TABLE_COLUMNS["extra_team_stats"]) for r in extra)


def test_empty_team_statistics_block_emits_no_all_null_row():
    fixture = make_fixture(statistics=[{"team": {"id": HOME_ID}, "statistics": []}, stat_block(AWAY_ID)])
    rows = parse_team_match_stats(fixture)
    assert [row["team_id"] for row in rows] == [AWAY_ID]


# -------------------------------------------------- missing lists / keys


@pytest.mark.parametrize("variant", ["absent", "empty", "null"])
def test_missing_statistics_lineups_players_events_do_not_crash(variant):
    keys = ("statistics", "lineups", "players", "events")
    if variant == "absent":
        fixture = make_fixture(omit=keys)
    else:
        fixture = make_fixture()
        for key in keys:
            fixture[key] = [] if variant == "empty" else None
    rows = parse_fixture(fixture)
    assert rows.match["fixture_id"] == 1001
    assert rows.team_match_stats == []
    assert rows.extra_team_stats == []
    assert rows.events == []
    assert rows.lineups == []
    assert rows.lineup_players == []
    assert rows.player_match_stats == []


# --------------------------------------------------------------- matches


def test_parse_match_converts_kickoff_to_utc_and_keeps_null_scores():
    fixture = make_fixture(date="2023-08-11T21:00:00+02:00")
    fixture["fixture"]["referee"] = "  "
    fixture["goals"] = {"home": None, "away": None}
    row = parse_match(fixture, league_code="EPL", fetched_at="2026-09-28T16:58:11+00:00", batch_file="raw/39/2023/batches/00001.json.gz")
    assert set(row) == set(TABLE_COLUMNS["matches"])
    assert row["kickoff_utc"] == "2023-08-11T19:00:00+00:00"
    assert row["referee"] is None
    assert row["home_goals"] is None and row["away_goals"] is None
    assert (row["ht_home_goals"], row["ht_away_goals"]) == (0, 2)
    assert row["et_home_goals"] is None and row["pen_home"] is None
    assert row["league_code"] == "EPL" and row["league_id"] == 39 and row["season"] == 2023
    assert (row["home_team_id"], row["away_team_id"]) == (HOME_ID, AWAY_ID)
    assert row["venue_city"] == "Burnley"


def test_parse_match_falls_back_to_timestamp_and_requires_fixture_id():
    fixture = make_fixture()
    fixture["fixture"]["date"] = None
    assert parse_match(fixture)["kickoff_utc"] == "2023-08-11T19:00:00+00:00"
    fixture["fixture"]["id"] = None
    with pytest.raises(ValueError):
        parse_match(fixture)


# ---------------------------------------------------------------- events


def test_events_keep_provider_order_and_null_team():
    events = [
        {"time": {"elapsed": 90, "extra": 3}, "team": {"id": HOME_ID}, "player": {"id": 5, "name": "A"}, "assist": {"id": None, "name": None}, "type": "Card", "detail": "Yellow Card", "comments": "Foul"},
        {"time": {"elapsed": 12, "extra": None}, "team": {"id": None}, "player": {"id": None, "name": None}, "assist": {}, "type": "Var", "detail": "Goal cancelled", "comments": None},
        {"time": {"elapsed": 12, "extra": None}, "team": {"id": AWAY_ID}, "player": {"id": 6, "name": "B"}, "assist": {"id": 7, "name": "C"}, "type": "subst", "detail": "Substitution 1", "comments": None},
    ]
    rows = parse_events(make_fixture(events=events))
    assert [row["seq"] for row in rows] == [0, 1, 2]
    assert [row["elapsed"] for row in rows] == [90, 12, 12]
    assert rows[0]["extra"] == 3 and rows[1]["extra"] is None
    assert rows[1]["team_id"] is None and rows[1]["player_id"] is None
    assert rows[2]["assist_id"] == 7 and rows[2]["type"] == "subst"
    assert all(set(row) == set(TABLE_COLUMNS["events"]) for row in rows)


# --------------------------------------------------------------- lineups


def test_lineups_and_lineup_players_flag_starters():
    fixture = make_fixture()
    fixture["lineups"][1]["startXI"].append({"player": {"id": None, "name": "Unknown", "number": None, "pos": None, "grid": None}})
    lineups = parse_lineups(fixture)
    assert [(row["team_id"], row["formation"], row["coach_name"]) for row in lineups] == [
        (HOME_ID, "4-4-2", "Coach Home"),
        (AWAY_ID, "4-3-3", "Coach Away"),
    ]
    players = parse_lineup_players(fixture)
    assert [(row["team_id"], row["player_id"], row["is_starter"]) for row in players] == [
        (HOME_ID, 1, 1),
        (HOME_ID, 2, 0),
        (AWAY_ID, 3, 1),
        (AWAY_ID, None, 1),
    ]
    assert players[0]["grid"] == "1:1" and players[1]["grid"] is None
    assert all(set(row) == set(TABLE_COLUMNS["lineup_players"]) for row in players)


# --------------------------------------------------------- player stats


def test_player_match_stats_parse_rating_flags_and_keep_nulls():
    fixture = make_fixture(
        players=[
            {
                "team": {"id": HOME_ID},
                "players": [
                    player_entry(1, rating="7.5", captain=True),
                    player_entry(2, rating=None, minutes=None),
                    player_entry(4, statistics=[]),
                ],
            }
        ]
    )
    rows = parse_player_match_stats(fixture)
    assert all(set(row) == set(TABLE_COLUMNS["player_match_stats"]) for row in rows)
    first, unused, bare = rows
    assert first["rating"] == 7.5 and isinstance(first["rating"], float)
    assert first["captain"] == 1 and first["substitute"] == 0
    assert first["pen_committed"] == 1  # provider spells it "commited"
    assert first["passes_accuracy"] == 15.0
    assert first["goals"] == 1 and first["red_cards"] == 0  # real zeros stay zero
    assert first["shots_on"] is None and first["tackles"] is None and first["offsides"] is None
    assert unused["rating"] is None and unused["minutes"] is None
    assert bare["player_id"] == 4
    assert all(bare[column] is None for column in ("minutes", "rating", "captain", "goals", "pen_scored"))


# ------------------------------------------------------ builder helpers


def write_batch_file(path: Path, fixtures: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps({"get": "fixtures", "errors": [], "response": fixtures}).encode("utf-8")))


def make_archive_with_state(root: Path) -> None:
    """Two league-seasons, one batch file each, written via the real downloader store."""

    store = HistoryStore(root)
    epl = [
        make_fixture(1001),
        make_fixture(
            1002,
            statistics=[stat_block(HOME_ID, **{"Corner Kicks": None, "Throwins": 20}), stat_block(AWAY_ID)],
            lineups=[],
            players=[],
        ),
        make_fixture(1003, status="CANC", statistics=[], events=[], lineups=[], players=[]),
    ]
    other = [make_fixture(2001, league_id=999, season=2020, date="2020-10-01T12:00:00+00:00")]
    for league_id, season, code, fixtures in ((39, 2023, "EPL", epl), (999, 2020, "TESTLG", other)):
        ids = [f["fixture"]["id"] for f in fixtures]
        store.save_ids(league_id, season, code, ids, {"response": []})
        batch_file = store.write_batch(league_id, season, {"response": fixtures}, 1)
        store.mark_batch_done(league_id, season, ids, ids, batch_file)


def table_counts(db_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in DATA_TABLES}


def table_dump(db_path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {table: sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr) for table in DATA_TABLES}


# ------------------------------------------------------ end-to-end build


def test_end_to_end_build_from_temp_archive(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    out = tmp_path / "tables" / "history.sqlite"
    progress_calls = []

    report = build_history_tables(archive, out, files_per_transaction=1, progress=lambda *a: progress_calls.append(a))

    assert report.batch_files == 2
    assert report.fixtures == 4
    assert report.state_db_used is True
    assert progress_calls == [(1, 2, 3), (2, 2, 4)]
    assert table_counts(out) == {
        "matches": 4,
        "team_match_stats": 6,
        "extra_team_stats": 1,
        "events": 3,
        "lineups": 4,
        "lineup_players": 6,
        "player_match_stats": 4,
    }
    assert report.row_counts == table_counts(out)

    with closing(sqlite3.connect(out)) as conn:
        match = conn.execute(
            "SELECT league_code, season, kickoff_utc, fetched_at, batch_file FROM matches WHERE fixture_id=1001"
        ).fetchone()
        assert match[0:3] == ("EPL", 2023, "2023-08-11T19:00:00+00:00")
        assert match[3] is not None  # provenance from state.sqlite3
        assert match[4] == "raw/39/2023/batches/00001.json.gz"
        assert conn.execute("SELECT league_code FROM matches WHERE fixture_id=2001").fetchone() == ("TESTLG",)

        # NULL in the source is NULL in SQLite, not 0.
        corners = conn.execute(
            "SELECT corners, typeof(corners), red_cards FROM team_match_stats WHERE fixture_id=1002 AND side='home'"
        ).fetchone()
        assert corners == (None, "null", None)
        assert conn.execute(
            "SELECT typeof(possession_pct), typeof(expected_goals) FROM team_match_stats WHERE fixture_id=1001 AND side='home'"
        ).fetchone() == ("real", "real")
        assert conn.execute("SELECT stat_type, value_text FROM extra_team_stats").fetchall() == [("Throwins", "20")]

        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {
            "idx_matches_league_season",
            "idx_matches_kickoff",
            "idx_events_fixture",
            "idx_player_match_stats_player",
        } <= indexes

        info = conn.execute(
            "SELECT schema_version, fixtures_count, matches_rows, player_match_stats_rows, state_db_used FROM build_info"
        ).fetchall()
        assert info == [(SCHEMA_VERSION, 4, 4, 4, 1)]

    # Only the final database remains next to it: no temp/journal leftovers.
    assert [p.name for p in out.parent.iterdir()] == ["history.sqlite"]


def test_rebuild_is_idempotent(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    out = tmp_path / "history.sqlite"

    first = build_history_tables(archive, out)
    dump_first = table_dump(out)
    second = build_history_tables(archive, out)

    assert first.row_counts == second.row_counts
    assert table_dump(out) == dump_first
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM build_info").fetchone() == (1,)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive", "history.sqlite"]


def test_build_never_modifies_state_db_or_raw_files(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)

    def digest() -> dict[str, str]:
        return {
            str(p.relative_to(archive)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(archive.rglob("*"))
            if p.is_file()
        }

    before = digest()
    build_history_tables(archive, tmp_path / "out" / "history.sqlite")
    assert digest() == before


def test_failed_build_keeps_previous_database(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    out = tmp_path / "history.sqlite"
    build_history_tables(archive, out)
    counts_before = table_counts(out)

    corrupt = archive / "raw" / "999" / "2020" / "batches" / "00002.json.gz"
    corrupt.write_bytes(b"this is not gzip")
    with pytest.raises(ValueError, match="00002.json.gz"):
        build_history_tables(archive, out)

    assert table_counts(out) == counts_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive", "history.sqlite"]


def test_build_without_state_db_uses_catalog_codes_and_skips_duplicates(tmp_path):
    archive = tmp_path / "archive"
    write_batch_file(archive / "raw" / "39" / "2023" / "batches" / "00001.json.gz", [make_fixture(1001), make_fixture(1002)])
    write_batch_file(
        archive / "raw" / "999" / "2020" / "batches" / "00001.json.gz",
        [make_fixture(1002), make_fixture(3001, league_id=999, season=2020), {"fixture": {"id": None}}],
    )
    out = tmp_path / "history.sqlite"

    report = build_history_tables(archive, out)

    assert report.state_db_used is False
    assert report.fixtures == 3
    assert report.duplicate_fixtures_skipped == 1
    assert report.fixtures_without_id == 1
    with closing(sqlite3.connect(out)) as conn:
        rows = conn.execute("SELECT fixture_id, league_code, fetched_at, batch_file FROM matches ORDER BY fixture_id").fetchall()
    assert rows == [
        (1001, "EPL", None, "raw/39/2023/batches/00001.json.gz"),
        # First copy in the fixed (league, season, file) order is kept.
        (1002, "EPL", None, "raw/39/2023/batches/00001.json.gz"),
        (3001, None, None, "raw/999/2020/batches/00001.json.gz"),
    ]


def test_build_with_no_batch_files_fails_closed(tmp_path):
    (tmp_path / "archive" / "raw").mkdir(parents=True)
    out = tmp_path / "history.sqlite"
    with pytest.raises(FileNotFoundError):
        build_history_tables(tmp_path / "archive", out)
    assert not out.exists()


# --------------------------------------------------------- quality report


def test_quality_summary_shares_over_played_matches(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    out = tmp_path / "history.sqlite"
    build_history_tables(archive, out)

    with closing(sqlite3.connect(out)) as conn:
        rows = {(row["league_code"], row["season_bucket"]): row for row in quality_summary(conn)}

    epl = rows[("EPL", "2023-2025")]
    assert (epl["played"], epl["not_played"]) == (2, 1)
    assert epl["corners"] == 0.5  # 1002 home corners are NULL
    assert epl["shots"] == 1.0
    assert epl["expected_goals"] == 1.0
    assert epl["lineups"] == 0.5 and epl["player_stats"] == 0.5
    other = rows[("TESTLG", "2019-2022")]
    assert other["played"] == 1 and other["corners"] == 1.0

    report_path = tmp_path / "quality_report.md"
    text = write_quality_report(out, report_path)
    assert report_path.read_text(encoding="utf-8") == text
    assert "| EPL | England | 2023-2025 | 2023 | 2 | 1 |" in text
    assert "| TESTLG |  | 2019-2022 | 2020 | 1 | 0 |" in text  # league outside the catalog
    assert "**Corners** (1): EPL 2023-2025 (50.0%)" in text


def test_render_quality_report_without_gaps():
    row = {
        "league_id": 39,
        "league_code": "EPL",
        "season_bucket": "2015-2018",
        "first_season": 2015,
        "last_season": 2018,
        "played": 10,
        "not_played": 0,
        **{metric: 1.0 for metric in ("corners", "shots", "shots_on_goal", "expected_goals", "lineups", "player_stats", "events")},
    }
    text = render_quality_report([row])
    assert "- None." in text
    assert "| EPL | England | 2015-2018 | 2015-2018 | 10 | 0 | 100.0% |" in text
