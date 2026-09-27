from __future__ import annotations

import pandas as pd
import pytest

from model_buk.cli import build_parser
from model_buk.evaluation import archive_market_pool
from model_buk.evaluation.archive_market_pool import run_archive_market_pool
from model_buk.ingestion.football_data_uk import FootballDataUKBatch


def _inputs(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"archive")
    predictions = tmp_path / "predictions.csv"
    rows = []
    odds = []
    actuals = ("home", "away", "draw")
    prices = (
        {"home": 1.6, "draw": 4.0, "away": 6.0},
        {"home": 5.0, "draw": 4.0, "away": 1.7},
        {"home": 3.0, "draw": 2.8, "away": 3.0},
    )
    for index, (actual, book) in enumerate(zip(actuals, prices, strict=True), start=1):
        source_id = f"row-{index}"
        rows.append(
            {
                "source_row_id": source_id,
                "league_code": "E0",
                "outer_fold": f"fold-{index}",
                "kickoff": f"202{index + 1}-01-01T12:00:00Z",
                "actual": actual,
                "p_home": 0.45,
                "p_draw": 0.25,
                "p_away": 0.30,
            }
        )
        for selection, price in book.items():
            odds.append(
                {
                    "source_row_id": source_id,
                    "provider": "market_average",
                    "odds_kind": "opening",
                    "market": "1x2",
                    "selection": selection,
                    "odds": price,
                }
            )
    pd.DataFrame(rows).to_csv(predictions, index=False)
    batch = FootballDataUKBatch(matches=pd.DataFrame(), odds=pd.DataFrame(odds))
    monkeypatch.setattr(archive_market_pool, "load_football_data_uk_archives", lambda _: batch)
    return archive, predictions


def test_market_pool_requires_explicit_opening_availability_assumption(tmp_path) -> None:
    predictions = tmp_path / "predictions.csv"
    predictions.write_text("source_row_id\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no quote timestamps"):
        run_archive_market_pool([], predictions, tmp_path / "out")


def test_market_pool_selects_weights_only_from_prior_outer_folds(tmp_path, monkeypatch) -> None:
    archive, predictions = _inputs(tmp_path, monkeypatch)

    manifest = run_archive_market_pool(
        [archive],
        predictions,
        tmp_path / "out",
        model_weights=(0.0, 0.5, 1.0),
        assume_opening_available_at_cutoff=True,
    )

    selections = pd.read_csv(tmp_path / "out" / "market_pool_selections.csv")
    forecasts = pd.read_csv(tmp_path / "out" / "nested_oos_predictions.csv")
    assert selections["training_outer_folds"].tolist() == ["('fold-1',)", "('fold-1', 'fold-2')"]
    assert forecasts["outer_fold"].tolist() == ["fold-2", "fold-3"]
    assert forecasts[["p_home", "p_draw", "p_away"]].sum(axis=1).tolist() == pytest.approx([1, 1])
    assert {
        "opening_market_p_home",
        "opening_market_p_draw",
        "opening_market_p_away",
    } <= set(forecasts)
    assert not {"market_p_home", "market_p_draw", "market_p_away"} & set(forecasts)
    assert manifest["promotion_eligible"] is False
    assert manifest["warmup_outer_fold"] == "fold-1"


def test_market_pool_cli_requires_explicit_assumption_flag() -> None:
    args = build_parser().parse_args(
        [
            "pool-nested-goals-with-opening-market",
            "--archives", "source.zip",
            "--predictions", "predictions.csv",
            "--outdir", "out",
            "--assume-opening-available",
        ]
    )
    assert args.command == "pool-nested-goals-with-opening-market"
    assert args.assume_opening_available is True
