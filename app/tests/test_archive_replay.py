from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from model_buk.evaluation.archive_replay import build_football_data_replay_frame
from model_buk.evaluation.replay_data import load_replay_data
from model_buk.ingestion.football_data_uk import FootballDataUKBatch


def batch() -> FootballDataUKBatch:
    matches = pd.DataFrame(
        [
            {
                "source_row_id": "match-1",
                "kickoff": pd.Timestamp("2025-01-01 12:00:00"),
                "home_goals": 1,
                "away_goals": 0,
            },
            {
                "source_row_id": "unsettled",
                "kickoff": pd.Timestamp("2025-01-02 12:00:00"),
                "home_goals": pd.NA,
                "away_goals": pd.NA,
            },
        ]
    )
    odds = pd.DataFrame(
        [
            {"source_row_id": "match-1", "provider": "book-a", "odds_kind": kind, "market": "1x2", "selection": selection, "odds": price}
            for kind, prices in (("opening", {"home": 2.1, "draw": 3.4, "away": 3.8}),
                                 ("closing", {"home": 2.0, "draw": 3.5, "away": 4.0}))
            for selection, price in prices.items()
        ]
    )
    return FootballDataUKBatch(matches=matches, odds=odds)


def forecasts(actual: str = "home") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_row_id": "match-1",
                "league_code": "E0",
                "outer_fold": "outer-1",
                "actual": actual,
                "p_home": 0.55,
                "p_draw": 0.25,
                "p_away": 0.20,
            }
        ]
    )


def test_archive_conversion_requires_explicit_opening_time_assumption() -> None:
    with pytest.raises(ValueError, match="no quote timestamps"):
        build_football_data_replay_frame(batch(), forecasts())


def test_archive_conversion_builds_replay_rows_and_marks_assumptions(tmp_path) -> None:
    result = build_football_data_replay_frame(
        batch(),
        forecasts(),
        assume_opening_available_at_cutoff=True,
        cutoff_lead=timedelta(minutes=60),
        probability_haircut=0.05,
    )

    assert len(result) == 3
    assert set(result["execution_price_kind"]) == {"opening"}
    assert set(result["settlement"]) == {"win", "loss"}
    assert result.iloc[0]["closing_odds"] in {2.0, 3.5, 4.0}
    assert result["source_assumption"].str.contains("research_only").all()

    path = tmp_path / "replay.csv"
    result.to_csv(path, index=False)
    loaded = load_replay_data(path)
    assert loaded.rows_accepted == 3
    assert len(loaded.manifest()["source_assumptions"]) == 1
    assert {item.stratum_id for item in loaded.opportunities} == {"E0"}


def test_archive_conversion_rejects_settlement_mismatch_and_non_oos_duplicates() -> None:
    with pytest.raises(ValueError, match="settlements do not match"):
        build_football_data_replay_frame(
            batch(), forecasts("away"), assume_opening_available_at_cutoff=True
        )
    duplicate_forecasts = pd.concat([forecasts(), forecasts()], ignore_index=True)
    with pytest.raises(ValueError, match="at most one OOS row"):
        build_football_data_replay_frame(
            batch(), duplicate_forecasts, assume_opening_available_at_cutoff=True
        )
