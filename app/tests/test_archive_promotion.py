from __future__ import annotations

import json

import pandas as pd

from model_buk.cli import build_parser
from model_buk.evaluation import archive_promotion
from model_buk.evaluation.archive_promotion import run_archive_promotion_gate
from model_buk.ingestion.football_data_uk import FootballDataUKBatch


def test_archive_promotion_cli_is_fail_closed_by_design() -> None:
    args = build_parser().parse_args(
        [
            "archive-promotion-gate",
            "--archives", "source.zip",
            "--predictions", "predictions.csv",
            "--outdir", "report",
        ]
    )
    assert args.command == "archive-promotion-gate"
    assert args.market_odds_kind == "closing"


def test_archive_promotion_writes_per_league_quality_and_timestamp_blocker(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"archive identity")
    predictions_path = tmp_path / "predictions.csv"
    pd.DataFrame(
        [
            {
                "source_row_id": f"E0-{i}",
                "league_code": "E0",
                "league": "E0",
                "kickoff": f"2025-08-{i + 1:02d}T15:00:00Z",
                "outer_fold": "2025/26",
                "actual": ["home", "draw", "away"][i % 3],
                "p_home": 0.5,
                "p_draw": 0.25,
                "p_away": 0.25,
            }
            for i in range(6)
        ]
    ).to_csv(predictions_path, index=False)
    matches = pd.DataFrame(
        [
            {
                "source_row_id": f"E0-{i}",
                "league": "E0",
                "kickoff": f"2025-08-{i + 1:02d}T15:00:00Z",
                "home_goals": [2, 1, 0][i % 3],
                "away_goals": [0, 1, 2][i % 3],
            }
            for i in range(6)
        ]
    )
    odds = pd.DataFrame()
    monkeypatch.setattr(
        archive_promotion,
        "load_football_data_uk_archives",
        lambda _paths: FootballDataUKBatch(matches=matches, odds=odds),
    )
    monkeypatch.setattr(
        archive_promotion,
        "build_market_1x2_benchmark",
        lambda *_a, **_k: pd.DataFrame(
            {
                "source_row_id": [f"E0-{i}" for i in range(6)],
                "market_p_home": [0.45] * 6,
                "market_p_draw": [0.3] * 6,
                "market_p_away": [0.25] * 6,
            }
        ),
    )

    result = run_archive_promotion_gate(
        [archive], predictions_path, tmp_path / "report", bootstrap_resamples=100
    )

    assert result["promotion_eligible"] is False
    assert result["matched_predictions"] == 6
    decision = json.loads((tmp_path / "report" / "promotion_decisions.json").read_text())[0]
    assert decision["promotion_eligible"] is False
    assert "timestamps" in decision["blocking_reason"]
    metrics = pd.read_csv(tmp_path / "report" / "promotion_metrics.csv")
    assert metrics.loc[0, "n"] == 6
