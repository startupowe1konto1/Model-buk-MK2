from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from model_buk.cli import build_parser
from model_buk.evaluation.replay_runner import _parse_plan, run_historical_replay


def _timestamp(day: int, *, hour: int = 12, minute: int = 0) -> str:
    value = datetime(2026, 9, day, hour, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return value.isoformat().replace("+00:00", "Z")


def _row(day: int, selection: str, settlement: str) -> dict[str, str]:
    cutoff = datetime(2026, 9, day, 12, tzinfo=timezone.utc)
    observed = (cutoff - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    return {
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "kickoff": (cutoff + timedelta(hours=5)).isoformat().replace("+00:00", "Z"),
        "fixture_id": f"fixture-{day}",
        "market_key": "goals-total-2.5",
        "selection": selection,
        "decimal_odds": "2.0",
        "probability": "0.62" if selection == "over" else "0.38",
        "probability_lower_bound": "0.58" if selection == "over" else "0.30",
        "observed_at": observed,
        "snapshot_id": f"snapshot-{day}-{selection}",
        "bookmaker": "book-a",
        "tax_multiplier": "1.0",
        "execution_cost": "0.0",
        "correlation_group": "goals-total",
        "settlement": settlement,
        "market_group_id": f"fixture-{day}:goals-total-2.5",
        "market_size": "2",
    }


def _write_inputs(tmp_path, *, bad_row: bool = False):
    input_path = tmp_path / "history.csv"
    rows = []
    for day, result in ((1, "win"), (2, "loss"), (3, "win")):
        rows.extend((_row(day, "over", result), _row(day, "under", "loss" if result == "win" else "win")))
    if bad_row:
        rows.append(_row(3, "over", "invalid"))
    with input_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    plan = {
        "strategies": [
            {
                "name": "fixed-top1",
                "devig_method": "proportional",
                "min_conservative_ev_net": 0,
                "max_recommendations": 1,
                "min_conservative_market_edge": 0,
            }
        ],
        "folds": [
            {
                "label": "outer-2026-09-03",
                "outer_fold": {
                    "label": "outer",
                    "start": _timestamp(3, minute=-1),
                    "end": _timestamp(3, minute=1),
                },
                "inner_folds": [
                    {"label": f"inner-{day}", "start": _timestamp(day, minute=-1), "end": _timestamp(day, minute=1)}
                    for day in (1, 2)
                ],
            }
        ],
        "evaluation": {"min_inner_bets": 1, "bootstrap_resamples": 25, "seed": 7},
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return input_path, plan_path


def test_replay_history_cli_requires_input_plan_and_output_directory() -> None:
    args = build_parser().parse_args(
        ["replay-history", "--input", "input.csv", "--plan", "plan.json", "--outdir", "report"]
    )
    assert args.command == "replay-history"
    assert args.allow_quarantine is False
    assert args.by_stratum is False


def test_example_plan_is_valid() -> None:
    plan_path = Path(__file__).parents[1] / "config" / "replay_plan.example.json"

    strategies, folds, evaluation = _parse_plan(plan_path)

    assert len(strategies) == 36
    assert {strategy.devig_method for strategy in strategies} == {"proportional", "power", "shin"}
    assert len(folds) == 1
    assert len(folds[0].inner_folds) == 3
    assert evaluation["bootstrap_resamples"] == 2000


def test_runner_writes_nested_replay_reports_and_manifest(tmp_path) -> None:
    input_path, plan_path = _write_inputs(tmp_path)
    output_path = tmp_path / "report"

    manifest = run_historical_replay(input_path, plan_path, output_path)

    assert manifest["status"] == "completed"
    assert manifest["data"]["rows_accepted"] == 6
    assert manifest["selected_strategies"][0]["selected_strategy"] == "fixed-top1"
    assert (output_path / "outer_ledger.csv").is_file()
    assert (output_path / "outer_summary.csv").is_file()
    assert json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_runner_stops_on_quarantine_unless_explicitly_allowed(tmp_path) -> None:
    input_path, plan_path = _write_inputs(tmp_path, bad_row=True)
    output_path = tmp_path / "report"

    with pytest.raises(ValueError, match="quarantined rows"):
        run_historical_replay(input_path, plan_path, output_path)

    assert not output_path.exists()


def test_runner_selects_strategies_independently_by_stratum(tmp_path) -> None:
    input_path, plan_path = _write_inputs(tmp_path)
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["stratum_id"] = "E0"
    second_group = []
    for row in rows:
        copy = dict(row)
        copy["stratum_id"] = "D1"
        copy["fixture_id"] += "-D1"
        copy["market_group_id"] += "-D1"
        copy["snapshot_id"] += "-D1"
        second_group.append(copy)
    with input_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows + second_group)

    result = run_historical_replay(
        input_path,
        plan_path,
        tmp_path / "grouped-report",
        by_stratum=True,
    )

    assert result["by_stratum"] is True
    assert {row["stratum_id"] for row in result["selected_strategies"]} == {"E0", "D1"}
