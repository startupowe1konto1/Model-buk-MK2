"""The configurable data root (model_buk.paths) and the slim history build (no player-level tables)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from model_buk import paths
from model_buk.evaluation import protocol
from model_buk.ingestion.api_football_tables import DATA_TABLES, SLIM_SKIPPED_TABLES, build_history_tables
from model_buk.scanner import live_models, settlement

from test_api_football_tables import make_archive_with_state, table_dump

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


# --------------------------------------------------------------------------- #
# Data root
# --------------------------------------------------------------------------- #


def test_data_root_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    assert paths.data_root() == tmp_path
    assert paths.odds_db() == tmp_path / "api-football-odds" / "odds.sqlite3"
    assert paths.history_db() == tmp_path / "api-football" / "tables" / "history.sqlite"
    assert paths.scanner_register() == tmp_path / "scanner" / "scanner.sqlite"
    assert paths.archive_dir() == tmp_path / "archive"
    assert paths.data_path("logs", "x.log") == tmp_path / "logs" / "x.log"


def test_default_data_root_is_unchanged_on_windows_and_data_on_linux(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.data_root() == Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths.data_root() == paths.POSIX_DEFAULT
    monkeypatch.setenv(paths.ENV_VAR, "   ")
    assert paths.data_root() == paths.POSIX_DEFAULT  # blank counts as unset


@pytest.mark.skipif(sys.platform != "win32", reason="the PC default")
def test_protocol_data_root_keeps_the_pc_default_when_unset():
    import os

    if os.environ.get(paths.ENV_VAR):
        pytest.skip("MODEL_BUK_DATA is set in this environment")
    assert protocol.DATA_ROOT == Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
    assert protocol.HISTORY_DB == protocol.DATA_ROOT / "api-football" / "tables" / "history.sqlite"
    assert live_models.DEFAULT_CACHE_DIR == protocol.DATA_ROOT / "scanner" / "model_cache"


# --------------------------------------------------------------------------- #
# Slim history
# --------------------------------------------------------------------------- #


@pytest.fixture
def full_and_slim(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    full = tmp_path / "full" / "history.sqlite"
    slim = tmp_path / "slim" / "history.sqlite"
    build_history_tables(archive, full)
    report = build_history_tables(archive, slim, skip_tables=SLIM_SKIPPED_TABLES)
    return full, slim, report


def test_slim_build_leaves_only_the_player_tables_empty(full_and_slim):
    full, slim, report = full_and_slim
    full_rows, slim_rows = table_dump(full), table_dump(slim)
    for table in DATA_TABLES:
        if table in SLIM_SKIPPED_TABLES:
            assert full_rows[table] and slim_rows[table] == []
        else:
            assert slim_rows[table] == full_rows[table], table
    assert report.skipped_tables == SLIM_SKIPPED_TABLES
    assert all(report.row_counts[t] == 0 for t in SLIM_SKIPPED_TABLES)
    with closing(sqlite3.connect(slim)) as conn:
        info = conn.execute("SELECT skipped_tables, lineup_players_rows, matches_rows FROM build_info").fetchone()
        assert info == ("lineup_players,player_match_stats", 0, 4)
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_player_match_stats_player" in indexes  # empty tables keep their schema
    with closing(sqlite3.connect(full)) as conn:
        assert conn.execute("SELECT skipped_tables FROM build_info").fetchone() == (None,)


def test_everything_the_server_reads_is_identical_on_a_slim_build(full_and_slim):
    full, slim, _ = full_and_slim
    pd.testing.assert_frame_equal(live_models.load_live_matches(slim), live_models.load_live_matches(full))
    pd.testing.assert_frame_equal(live_models.load_team_stats(slim), live_models.load_team_stats(full))
    pd.testing.assert_frame_equal(
        protocol.load_history_matches(slim, league_ids=None), protocol.load_history_matches(full, league_ids=None)
    )
    ids = [1001, 1002, 1003, 2001]
    pd.testing.assert_frame_equal(settlement.load_match_results(ids, slim), settlement.load_match_results(ids, full))
    assert live_models.history_built_at(slim) is not None


def test_matches_cannot_be_skipped_and_unknown_tables_are_refused(tmp_path):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    with pytest.raises(ValueError):
        build_history_tables(archive, tmp_path / "a.sqlite", skip_tables=("matches",))
    with pytest.raises(ValueError):
        build_history_tables(archive, tmp_path / "b.sqlite", skip_tables=("no_such_table",))


def test_the_script_builds_slim_without_a_quality_report(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "archive"
    make_archive_with_state(archive)
    spec = importlib.util.spec_from_file_location("build_history_tables_script", SCRIPTS / "build_history_tables.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "tables" / "history.sqlite"
    monkeypatch.setattr(sys, "argv", ["build_history_tables.py", "--slim", "--archive-root", str(archive), "--out", str(out)])
    module.main()
    printed = capsys.readouterr().out
    assert "Left empty: lineup_players, player_match_stats" in printed
    assert not (out.parent / "quality_report.md").exists()
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM player_match_stats").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0
