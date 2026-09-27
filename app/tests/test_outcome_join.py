from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pandas as pd
import pytest

from model_buk.context_features import CONTEXT_FEATURE_SCHEMA_VERSION
from model_buk.evaluation.outcome_join import (
    OutcomeIssue,
    OutcomeLoadResult,
    join_context_features_with_outcomes,
    load_fixture_outcomes,
    prepare_labeled_context_dataset,
)


CUTOFF = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
KICKOFF = CUTOFF + timedelta(hours=8)
COMPLETED = KICKOFF + timedelta(hours=2)
AVAILABLE = COMPLETED + timedelta(minutes=2)


def feature_rows():
    return pd.DataFrame([
        {
            "archive_run_id": "run-1",
            "archive_records_sha256": "a" * 64,
            "archive_source_id": "source-v1",
            "fixture_id": "11",
            "prediction_cutoff": CUTOFF.isoformat(),
            "feature_schema_version": CONTEXT_FEATURE_SCHEMA_VERSION,
            "injuries_status_missing": 1.0,
        },
        {
            "archive_run_id": "run-2",
            "archive_records_sha256": "b" * 64,
            "archive_source_id": "source-v1",
            "fixture_id": "12",
            "prediction_cutoff": CUTOFF.isoformat(),
            "feature_schema_version": CONTEXT_FEATURE_SCHEMA_VERSION,
            "injuries_status_missing": 1.0,
        },
    ])


def outcome_frame(*, available_at=AVAILABLE, provider="provider-a", home_goals=2):
    return pd.DataFrame([
        {
            "fixture_id": "11",
            "kickoff": pd.Timestamp(KICKOFF),
            "completed_at": pd.Timestamp(COMPLETED),
            "available_at": pd.Timestamp(available_at),
            "ingested_at": pd.Timestamp(available_at + timedelta(minutes=1)),
            "home_goals": home_goals,
            "away_goals": 1,
            "result_scope": "regular_time",
            "provider": provider,
            "provider_version": "v1",
            "source_record_id": "11",
            "actual": "home" if home_goals > 1 else "draw",
        }
    ])


def load_result(frame=None):
    return OutcomeLoadResult(
        frame=outcome_frame() if frame is None else frame,
        source_sha256="c" * 64,
        rows_read=1,
        quarantine=(),
    )


def test_loader_accepts_timestamp_aliases_and_quarantines_impossible_timing(tmp_path):
    path = tmp_path / "outcomes.csv"
    pd.DataFrame([
        {
            "fixture_id": "11",
            "kickoff": KICKOFF.isoformat(),
            "completed_at": COMPLETED.isoformat(),
            "known_at": AVAILABLE.isoformat(),
            "fetched_at": (AVAILABLE + timedelta(minutes=1)).isoformat(),
            "home_goals": 2,
            "away_goals": 1,
            "result_scope": "regular_time",
            "provider": "provider-a",
            "provider_version": "v1",
        },
        {
            "fixture_id": "12",
            "kickoff": KICKOFF.isoformat(),
            "completed_at": (KICKOFF - timedelta(minutes=1)).isoformat(),
            "known_at": AVAILABLE.isoformat(),
            "fetched_at": (AVAILABLE + timedelta(minutes=1)).isoformat(),
            "home_goals": 0,
            "away_goals": 0,
            "result_scope": "regular_time",
            "provider": "provider-a",
            "provider_version": "v1",
        },
    ]).to_csv(path, index=False)

    loaded = load_fixture_outcomes(path)

    assert loaded.rows_read == 2
    assert len(loaded.frame) == 1
    assert loaded.frame.iloc[0]["actual"] == "home"
    assert len(loaded.quarantine) == 1
    assert "completed_at" in loaded.quarantine[0].message


def test_join_adds_labels_without_filling_unmatched_feature_rows():
    labeled, manifest = join_context_features_with_outcomes(feature_rows(), load_result())

    assert labeled["fixture_id"].tolist() == ["11"]
    assert labeled.iloc[0]["actual"] == "home"
    assert labeled.iloc[0]["home_goals"] == 2
    assert labeled.iloc[0]["injuries_status_missing"] == 1.0
    assert manifest["feature_rows"] == 2
    assert manifest["matched_rows"] == 1
    assert manifest["unmatched_rows"] == 1
    assert manifest["promotion_eligible"] is False


def test_join_rejects_outcome_known_before_prediction_and_provider_conflicts():
    leaked = load_result(outcome_frame(available_at=CUTOFF - timedelta(minutes=1)))
    with pytest.raises(ValueError, match="available at or before"):
        join_context_features_with_outcomes(feature_rows(), leaked)

    conflicting = pd.concat([
        outcome_frame(provider="provider-a", home_goals=2),
        outcome_frame(provider="provider-b", home_goals=0),
    ], ignore_index=True)
    with pytest.raises(ValueError, match="conflicting outcome providers"):
        join_context_features_with_outcomes(feature_rows(), load_result(conflicting))


def test_join_rejects_quarantine_by_default():
    result = OutcomeLoadResult(
        frame=outcome_frame(),
        source_sha256="c" * 64,
        rows_read=2,
        quarantine=(OutcomeIssue(2, "bad"),),
    )

    with pytest.raises(ValueError, match="quarantined"):
        join_context_features_with_outcomes(feature_rows(), result)


def test_prepare_labeled_dataset_verifies_feature_manifest_and_writes_evidence(tmp_path):
    features_path = tmp_path / "context.csv"
    feature_rows().iloc[:1].to_csv(features_path, index=False)
    feature_hash = sha256(features_path.read_bytes()).hexdigest()
    features_path.with_suffix(".csv.manifest.json").write_text(
        json.dumps({
            "schema_version": "context-feature-dataset-v1",
            "source_archive_sha256": "d" * 64,
            "output_sha256": feature_hash,
        }),
        encoding="utf-8",
    )
    outcomes_path = tmp_path / "outcomes.csv"
    pd.DataFrame([{
        "fixture_id": "11",
        "kickoff": KICKOFF.isoformat(),
        "completed_at": COMPLETED.isoformat(),
        "available_at": AVAILABLE.isoformat(),
        "ingested_at": (AVAILABLE + timedelta(minutes=1)).isoformat(),
        "home_goals": 2,
        "away_goals": 1,
        "result_scope": "regular_time",
        "provider": "provider-a",
        "provider_version": "v1",
    }]).to_csv(outcomes_path, index=False)
    output = tmp_path / "training.csv"

    manifest = prepare_labeled_context_dataset(features_path, outcomes_path, output)

    assert manifest["matched_rows"] == 1
    assert manifest["contains_outcomes"] is True
    assert manifest["promotion_eligible"] is False
    assert pd.read_csv(output).iloc[0]["actual"] == "home"
    assert output.with_suffix(".csv.manifest.json").is_file()
