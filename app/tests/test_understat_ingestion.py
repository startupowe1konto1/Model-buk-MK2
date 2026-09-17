from zipfile import ZipFile
from io import StringIO

import pandas as pd

from model_buk.ingestion.understat import (
    SCHEDULE_COLUMNS,
    STATS_COLUMNS,
    load_understat_archives,
    normalize_understat_schedule,
    normalize_understat_stats,
    read_understat_csv,
)


def test_schedule_and_stats_are_separate_and_canonical():
    schedule = normalize_understat_schedule(pd.DataFrame({
        "match_id": ["1001"], "competition": ["EPL"], "season": ["2024"],
        "datetime": ["2024-09-01 15:00:00"], "home_id": [10], "home_team": ["A"],
        "away_id": [20], "away_team": ["B"], "home_score": [2], "away_score": [1],
    }), source_name="schedule.csv")
    stats = normalize_understat_stats(pd.DataFrame({
        "game_id": ["1001"], "league": ["EPL"], "season": ["2024"],
        "date": ["2024-09-01 15:00:00Z"], "home_team_id": [10],
        "home_team": ["A"], "away_team_id": [20], "away_team": ["B"],
        "home_goals": [2], "away_goals": [1], "home_xg": [1.7],
        "away_xg": [0.8], "home_np_xg": [1.4], "away_np_xg": [0.8],
        "home_ppda": [8.2], "away_ppda": [12.0],
        "home_deep_completions": [5], "away_deep_completions": [2],
    }), source_name="stats.csv")
    assert tuple(schedule.columns) == SCHEDULE_COLUMNS
    assert tuple(stats.columns) == STATS_COLUMNS
    assert schedule.loc[0, "game_id"] == "1001"
    assert stats.loc[0, "home_xg"] == 1.7
    assert stats.loc[0, "away_ppda"] == 12.0
    assert "home_xg" not in schedule.columns
    assert "home_ppda" not in schedule.columns


def test_missing_optional_stats_remain_nullable():
    out = normalize_understat_stats(pd.DataFrame({"id": [7], "home_team_id": [3]}))
    assert pd.isna(out.loc[0, "home_xg"])
    assert pd.isna(out.loc[0, "away_ppda"])
    assert out.loc[0, "game_id"] == "7"


def test_zip_loader_detects_members_without_extracting(tmp_path):
    archive_path = tmp_path / "part.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("understat/schedule.csv", "game_id,league,home_team,away_team,kickoff\n1,EPL,A,B,2024-01-01T12:00:00Z\n")
        archive.writestr(
            "understat/team_match_stats.csv",
            "game_id,home_team_id,away_team_id,home_xg,away_xg,home_ppda,away_ppda\n"
            "1,10,20,1.2,0.7,8.0,12.0\n",
        )
        archive.writestr("understat/ignore.csv", "foo,bar\n1,2\n")
    batch = load_understat_archives([archive_path], member_prefix="understat/")
    assert len(batch.schedule) == 1
    assert len(batch.stats) == 1
    assert batch.schedule.loc[0, "game_id"] == batch.stats.loc[0, "game_id"]
    assert not list(tmp_path.glob("*.csv"))


def test_standalone_reader_classifies_export():
    result = read_understat_csv(
        StringIO("game_id,home_team_id,away_team_id,home_xg,away_xg\n42,9,10,0.5,0.3\n"),
        kind="stats",
    )
    assert result.schedule.empty
    assert result.stats.loc[0, "game_id"] == "42"


def test_zip_loader_collapses_identical_schedule_copies(tmp_path):
    paths = [tmp_path / "part-1.zip", tmp_path / "part-2.zip"]
    schedule = "game_id,league,home_team,away_team,kickoff\n1,EPL,A,B,2024-01-01T12:00:00Z\n"
    for path in paths:
        with ZipFile(path, "w") as archive:
            archive.writestr("understat/schedule.csv", schedule)

    batch = load_understat_archives(paths, member_prefix="understat/")

    assert len(batch.schedule) == 1
