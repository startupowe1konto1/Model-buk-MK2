from __future__ import annotations

import pandas as pd
from zipfile import ZipFile

from model_buk.ingestion.football_data_uk import (
    MATCH_COLUMNS,
    ODDS_COLUMNS,
    load_football_data_uk_archives,
    normalize_football_data_uk,
    read_football_data_uk_csv,
)


def test_normalizes_match_facts_and_keeps_closing_prices_out_of_matches():
    raw = pd.DataFrame(
        {
            "Div": ["E0"],
            "Date": ["17/08/2025"],
            "Time": ["16:30"],
            "HomeTeam": ["North FC"],
            "AwayTeam": ["South FC"],
            "FTHG": [2],
            "FTAG": [1],
            "HS": [14],
            "AS": [8],
            "HST": [6],
            "AST": [3],
            "HC": [7],
            "AC": [2],
            "HY": [1],
            "AY": [3],
            "HR": [0],
            "AR": [1],
            "B365H": [1.80],
            "B365D": [3.60],
            "B365A": [4.50],
            "B365CH": [1.72],
            "B365CD": [3.80],
            "B365CA": [4.90],
        }
    )

    batch = normalize_football_data_uk(raw)

    assert tuple(batch.matches.columns) == MATCH_COLUMNS
    assert batch.matches.loc[0, "kickoff"] == pd.Timestamp("2025-08-17 16:30")
    assert batch.matches.loc[0, "home_sot"] == 6
    assert not any("odds" in column.lower() for column in batch.matches.columns)
    assert set(batch.odds["odds_kind"]) == {"opening", "closing"}
    assert len(batch.opening_odds) == 3
    assert len(batch.closing_odds) == 3


def test_missing_optional_columns_are_nullable_and_missing_odds_are_allowed():
    raw = pd.DataFrame(
        {
            "Div": ["SP1"],
            "Date": ["2025-09-01"],
            "HomeTeam": ["Home"],
            "AwayTeam": ["Away"],
            "FTHG": [1],
            "FTAG": [0],
        }
    )

    batch = normalize_football_data_uk(raw)

    assert pd.isna(batch.matches.loc[0, "home_shots"])
    assert pd.isna(batch.matches.loc[0, "away_yellow_cards"])
    assert tuple(batch.odds.columns) == ODDS_COLUMNS
    assert batch.odds.empty


def test_drops_fully_empty_trailing_rows_but_preserves_source_position():
    raw = pd.DataFrame(
        {
            "Div": [pd.NA, "E0"],
            "Date": [pd.NA, "17/08/2025"],
            "HomeTeam": [pd.NA, "Home"],
            "AwayTeam": [pd.NA, "Away"],
        }
    )

    batch = normalize_football_data_uk(raw, source_name="season.csv")

    assert len(batch.matches) == 1
    assert batch.matches.loc[0, "source_row_id"].endswith("season.csv:1")


def test_normalizes_totals_and_handicap_with_explicit_price_kind():
    raw = pd.DataFrame(
        {
            "Div": ["I1"],
            "Date": ["01/09/2025"],
            "HomeTeam": ["A"],
            "AwayTeam": ["B"],
            "P>2.5": [2.02],
            "P<2.5": [1.88],
            "PC>2.5": [1.95],
            "PC<2.5": [1.95],
            "AHh": [-0.5],
            "PSAHH": [1.91],
            "PSAHA": [2.01],
            "AHCh": [-0.75],
            "PSCAHH": [2.08],
            "PSCAHA": [1.84],
        }
    )

    batch = normalize_football_data_uk(raw)

    totals = batch.odds.loc[batch.odds["market"].eq("total_goals")]
    handicap = batch.odds.loc[batch.odds["market"].eq("asian_handicap")]
    assert set(totals["provider"]) == {"pinnacle"}
    assert set(totals["odds_kind"]) == {"opening", "closing"}
    assert set(totals["line"]) == {2.5}
    assert len(totals) == 4
    assert set(handicap.loc[handicap["odds_kind"].eq("opening"), "line"]) == {-0.5}
    assert set(handicap.loc[handicap["odds_kind"].eq("closing"), "line"]) == {-0.75}


def test_reads_only_synthetic_csv(tmp_path):
    path = tmp_path / "season.csv"
    pd.DataFrame(
        {
            "Div": ["D1"],
            "Date": ["02/09/2025"],
            "Time": ["20:45"],
            "HomeTeam": ["Alpha"],
            "AwayTeam": ["Beta"],
            "B365H": [2.10],
        }
    ).to_csv(path, index=False)

    batch = read_football_data_uk_csv(path)

    assert batch.matches.loc[0, "league"] == "D1"
    assert batch.matches.loc[0, "kickoff"] == pd.Timestamp("2025-09-02 20:45")
    assert batch.opening_odds.loc[0, "provider"] == "bet365"
    assert batch.matches.loc[0, "source_row_id"].endswith("season.csv:0")


def test_source_name_prevents_cross_file_row_id_collisions():
    raw = pd.DataFrame(
        {
            "Div": ["E0"],
            "Date": ["17/08/2025"],
            "HomeTeam": ["A"],
            "AwayTeam": ["B"],
        }
    )
    first = normalize_football_data_uk(raw, source_name="E0-2526.csv")
    second = normalize_football_data_uk(raw, source_name="E0-2425.csv")

    assert first.matches.loc[0, "source_row_id"] != second.matches.loc[0, "source_row_id"]


def test_loads_split_archives_without_extracting_files(tmp_path):
    first_zip = tmp_path / "part-001.zip"
    second_zip = tmp_path / "part-002.zip"
    prefix = "football_data/football-data-uk/main/"
    with ZipFile(first_zip, "w") as archive:
        archive.writestr(
            prefix + "E0-2526.csv",
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H\n"
            "E0,17/08/2025,Alpha,Beta,2,1,1.90\n",
        )
        archive.writestr("football_data/unrelated/large.json", "{}")
    with ZipFile(second_zip, "w") as archive:
        archive.writestr(
            prefix + "SP1-2526.csv",
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H\n"
            "SP1,18/08/2025,Gamma,Delta,0,0,2.10\n",
        )

    batch = load_football_data_uk_archives([first_zip, second_zip])

    assert len(batch.matches) == 2
    assert len(batch.odds) == 2
    assert batch.matches["source_row_id"].is_unique
    assert set(batch.matches["league"]) == {"E0", "SP1"}
    assert not list(tmp_path.glob("*.csv"))
