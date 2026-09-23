from __future__ import annotations

import csv
import json

import pytest

from model_buk.evaluation.replay_data import load_replay_data


def replay_row(**overrides):
    row = {
        "cutoff": "2026-09-20T12:00:00Z",
        "kickoff": "2026-09-20T17:00:00Z",
        "fixture_id": "fixture-1",
        "market_key": "goals-total-2.5",
        "selection": "over",
        "decimal_odds": "2.05",
        "probability": "0.58",
        "probability_lower_bound": "0.54",
        "observed_at": "2026-09-20T11:55:00Z",
        "snapshot_id": "snapshot-1",
        "bookmaker": "book-a",
        "tax_multiplier": "0.88",
        "execution_cost": "0.01",
        "correlation_group": "totals",
        "settlement": "win",
        "market_group_id": "fixture-1:goals-total-2.5",
        "market_size": "2",
    }
    row.update(overrides)
    return row


def test_load_csv_parses_opportunity_and_source_manifest(tmp_path) -> None:
    path = tmp_path / "replay.csv"
    row = replay_row()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=row.keys())
        writer.writeheader()
        writer.writerow(row)

    result = load_replay_data(path)

    assert result.rows_read == result.rows_accepted == 1
    assert result.quarantine == ()
    assert result.opportunities[0].candidate.decimal_odds == pytest.approx(2.05)
    assert result.opportunities[0].candidate.probability_lower_bound == pytest.approx(0.54)
    assert result.manifest()["source_sha256"] == result.source_sha256
    assert result.manifest()["rows_quarantined"] == 0


def test_load_jsonl_quarantines_bad_rows_and_preserves_valid_rows(tmp_path) -> None:
    path = tmp_path / "replay.jsonl"
    path.write_text(
        json.dumps(replay_row()) + "\n" + json.dumps(replay_row(probability="nan")) + "\n",
        encoding="utf-8",
    )

    result = load_replay_data(path)

    assert result.rows_read == 2
    assert result.rows_accepted == 1
    assert result.quarantine[0].row_number == 2
    assert "probability must be finite" in result.quarantine[0].message
    assert result.manifest()["quarantine_codes"] == {"invalid_row": 1}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cutoff", "2026-09-20T12:00:00", "must include a timezone"),
        ("execution_price_kind", "closing", "closing is evaluation-only"),
        ("kickoff", "2026-09-20T11:00:00Z", "cutoff must be earlier than kickoff"),
    ],
)
def test_load_quarantines_temporal_and_execution_contract_violations(tmp_path, field, value, message) -> None:
    path = tmp_path / "replay.jsonl"
    path.write_text(json.dumps(replay_row(**{field: value})) + "\n", encoding="utf-8")

    result = load_replay_data(path)

    assert result.rows_accepted == 0
    assert message in result.quarantine[0].message


def test_csv_missing_required_column_rejects_source(tmp_path) -> None:
    path = tmp_path / "incomplete.csv"
    path.write_text("cutoff,kickoff\n2026-09-20T12:00:00Z,2026-09-20T17:00:00Z\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        load_replay_data(path)
