from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_buk.cli import build_parser
from model_buk.evaluation import archive_goal_runner
from model_buk.evaluation.archive_goal_runner import _load_plan, run_nested_archive_forecasts
from model_buk.evaluation.nested_goals import NestedGoalsResult
from model_buk.ingestion.football_data_uk import FootballDataUKBatch


def test_nested_archive_example_plan_is_valid() -> None:
    plan = Path(__file__).parents[1] / "config" / "nested_goals_plan.example.json"
    candidates, folds, training = _load_plan(plan)

    assert {candidate.family for candidate in candidates} == {"dixon_coles", "negative_binomial"}
    assert len(folds) == 4
    assert len(folds[-1].inner_folds) == 3
    assert training["min_train_matches"] == 100


def test_nested_archive_cli_accepts_repeatable_league_filter() -> None:
    args = build_parser().parse_args(
        [
            "nested-goals-archives",
            "--archives", "one.zip", "two.zip",
            "--plan", "plan.json",
            "--outdir", "out",
            "--league", "E0",
            "--league", "D1",
        ]
    )
    assert args.command == "nested-goals-archives"
    assert args.league == ["E0", "D1"]


def test_nested_archive_runner_writes_per_league_oos_predictions(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"test archive identity")
    matches = pd.DataFrame(
        [
            {"league": league, "source_row_id": f"{league}-1"}
            for league in ("E0", "D1")
        ]
    )
    batch = FootballDataUKBatch(matches=matches, odds=pd.DataFrame())
    monkeypatch.setattr(archive_goal_runner, "load_football_data_uk_archives", lambda _paths: batch)

    def fake_nested(league_matches, candidates, folds, **_kwargs):
        league = str(league_matches.iloc[0]["league"])
        return NestedGoalsResult(
            inner_scores=pd.DataFrame([{"outer_fold": "outer", "model": candidates[0].name}]),
            selections=pd.DataFrame([{"outer_fold": "outer", "selected_candidate": candidates[0].name}]),
            outer_predictions=pd.DataFrame(
                [
                    {
                        "source_row_id": f"{league}-1",
                        "league": league,
                        "outer_fold": "outer",
                        "actual": "home",
                        "p_home": 0.6,
                        "p_draw": 0.2,
                        "p_away": 0.2,
                    }
                ]
            ),
        )

    monkeypatch.setattr(archive_goal_runner, "nested_walk_forward_goals", fake_nested)
    plan = Path(__file__).parents[1] / "config" / "nested_goals_plan.example.json"
    outdir = tmp_path / "results"

    manifest = run_nested_archive_forecasts([archive], plan, outdir)

    assert manifest["successful_leagues"] == ["D1", "E0"]
    assert manifest["prediction_rows"] == 2
    predictions = pd.read_csv(outdir / "nested_oos_predictions.csv")
    assert set(predictions["league_code"]) == {"D1", "E0"}
    assert len(manifest["source_archives"][0]["sha256"]) == 64


def test_nested_archive_runner_rejects_unknown_league(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"archive")
    batch = FootballDataUKBatch(
        matches=pd.DataFrame([{"league": "E0", "source_row_id": "E0-1"}]),
        odds=pd.DataFrame(),
    )
    monkeypatch.setattr(archive_goal_runner, "load_football_data_uk_archives", lambda _paths: batch)
    plan = Path(__file__).parents[1] / "config" / "nested_goals_plan.example.json"

    with pytest.raises(ValueError, match="absent from the archive"):
        run_nested_archive_forecasts([archive], plan, tmp_path / "results", leagues=["NOPE"])
