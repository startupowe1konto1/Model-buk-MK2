from __future__ import annotations

import pandas as pd

from model_buk.ingestion import enrich_with_understat


def test_enriches_exact_date_league_and_canonical_teams():
    matches = pd.DataFrame(
        {
            "source_row_id": ["fd:1", "fd:2"],
            "league": ["E0", "E0"],
            "kickoff": ["2024-09-01 15:00", "2024-09-02 15:00"],
            "home_team": ["Man United", "Other"],
            "away_team": ["Nott'm Forest", "Third"],
        }
    )
    stats = pd.DataFrame(
        {
            "game_id": [101],
            "league": ["ENG-Premier League"],
            "kickoff": ["2024-09-01T14:00:00Z"],
            "home_team_name": ["Manchester United"],
            "away_team_name": ["Nottingham Forest"],
            "home_xg": [1.8],
            "away_xg": [0.7],
        }
    )

    enriched = enrich_with_understat(matches, stats)

    assert enriched.loc[0, "understat_game_id"] == 101
    assert enriched.loc[0, "home_xg"] == 1.8
    assert pd.isna(enriched.loc[1, "understat_game_id"])


def test_does_not_match_reversed_home_and_away():
    matches = pd.DataFrame(
        {
            "source_row_id": ["fd:1"],
            "league": ["E0"],
            "kickoff": ["2024-09-01"],
            "home_team": ["A"],
            "away_team": ["B"],
        }
    )
    stats = pd.DataFrame(
        {
            "game_id": [101],
            "league": ["ENG-Premier League"],
            "kickoff": ["2024-09-01T14:00:00Z"],
            "home_team_name": ["B"],
            "away_team_name": ["A"],
            "home_xg": [1.0],
            "away_xg": [1.0],
        }
    )

    enriched = enrich_with_understat(matches, stats)

    assert pd.isna(enriched.loc[0, "understat_game_id"])
