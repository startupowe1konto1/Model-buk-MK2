from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from model_buk.current_pipeline import CurrentDataPipeline, RunContext
from model_buk.ingestion.api_football import (
    ApiFootballSnapshotAdapter,
    ApiFootballSnapshotError,
    ApiFootballSnapshotPolicy,
)


CUTOFF = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)


class FakeClient:
    connected = True

    def __init__(self):
        self.fixture_queries = []
        self.deep_values = []
        self.benchmark_values = []

    def fixtures(self, date, league_codes=None):
        self.fixture_queries.append((date, league_codes))
        return [
            {
                "fixture_id": 11,
                "kickoff": "2026-09-19T18:00:00Z",
                "status": "NS",
                "league_code": "LL",
                "home": {"id": 1, "name": "Barcelona"},
                "away": {"id": 2, "name": "Sevilla"},
            },
            {
                "fixture_id": 12,
                "kickoff": "2026-09-19T09:00:00Z",
                "status": "FT",
                "league_code": "LL",
                "home": {"id": 3, "name": "A"},
                "away": {"id": 4, "name": "B"},
            },
        ]

    def match_context(
        self,
        fixture_id,
        deep=True,
        *,
        include_prediction_benchmark=True,
        fixture_override=None,
    ):
        self.deep_values.append(deep)
        self.benchmark_values.append(include_prediction_benchmark)
        return {
            "fixture": {
                "fixture_id": fixture_id,
                "home": {"id": 1, "name": "Barcelona"},
                "away": {"id": 2, "name": "Sevilla"},
            },
            "odds": [
                {
                    "bookmaker_id": 7,
                    "bookmaker": "Book",
                    "bet_id": 1,
                    "bet": "Match Winner",
                    "value": "Home",
                    "odd": 1.9,
                    "update": "2026-09-19T09:58:00Z",
                },
                {
                    "bookmaker_id": 7,
                    "bookmaker": "Book",
                    "bet_id": 1,
                    "bet": "Match Winner",
                    "value": "Away",
                    "odd": 4.2,
                    "update": "2026-09-19T10:00:01Z",
                },
            ],
            "injuries": [{"team_id": 2, "player": "Example"}],
            "lineups": [],
            "recent_observations": {"home": [{"goals_for": 2}], "away": []},
            "external_prediction_benchmark": {"winner": "Barcelona", "secret": "never-copy"},
        }


def _adapter(client=None, **policy_kwargs):
    return ApiFootballSnapshotAdapter(
        client or FakeClient(),
        policy=ApiFootballSnapshotPolicy(day_offsets=(0,), league_codes=("LL",), **policy_kwargs),
        clock=lambda: CUTOFF + timedelta(seconds=2),
    )


def test_adapter_builds_future_fixture_context_and_skips_post_cutoff_quote():
    client = FakeClient()
    records = _adapter(client).fetch(RunContext("run-1", CUTOFF))

    assert client.fixture_queries == [("2026-09-19", ["LL"])]
    assert client.deep_values == [True]
    assert client.benchmark_values == [False]
    assert {record.feature_group for record in records} == {
        "fixture", "odds", "injuries", "lineups", "team_form", "context_availability"
    }
    assert sum(record.feature_group == "odds" for record in records) == 1
    assert all(record.entity_id != "fixture:12" for record in records)
    serialized_payloads = repr([record.payload for record in records])
    assert "never-copy" not in serialized_payloads
    assert all(record.ingested_at >= record.available_at for record in records)


def test_adapter_integrates_with_current_pipeline_without_network_or_raw_artifacts(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"api-football": _adapter()},
        predictor=lambda frame, context: [],
        predictor_id="no-op-v1",
        output_dir=tmp_path,
    )

    run = pipeline.run("scheduled-1", cutoff=CUTOFF, dry_run=True)

    assert run.manifest["record_count"] == 7
    assert run.manifest["feature_group_counts"]["odds"] == 1
    assert run.manifest["context_coverage"] == {
        "fixture_count": 1,
        "incomplete_fixture_count": 0,
        "provider_error_fixture_count": 0,
        "status_counts": {
            "injuries": {"available": 1},
            "lineups": {"observed_empty": 1},
            "odds": {"available": 1},
            "team_form": {"available": 1},
        },
    }
    assert run.manifest["context_features"] == {
        "schema_version": "match-context-features-v1",
        "row_count": 1,
        "used_in_model": False,
    }
    assert run.result["selected"] == []
    assert list(tmp_path.iterdir()) == []


def test_context_can_be_disabled_to_control_quota():
    client = FakeClient()
    records = _adapter(client, include_context=False).fetch(RunContext("run-1", CUTOFF))

    assert [record.feature_group for record in records] == ["fixture"]
    assert client.deep_values == []


def test_component_id_changes_when_policy_changes():
    first = _adapter(max_fixtures=5)
    second = _adapter(max_fixtures=6)
    assert first.component_id != second.component_id


def test_disconnected_client_and_invalid_policy_fail_closed():
    client = FakeClient()
    client.connected = False
    with pytest.raises(ApiFootballSnapshotError, match="not connected"):
        _adapter(client).fetch(RunContext("run-1", CUTOFF))

    with pytest.raises(ValueError, match="max_fixtures"):
        ApiFootballSnapshotPolicy(max_fixtures=0)

    with pytest.raises(ValueError, match="day_offsets"):
        ApiFootballSnapshotPolicy(day_offsets=(0.5,))


def test_failed_context_endpoint_is_not_misrepresented_as_empty_data():
    client = FakeClient()
    original = client.match_context

    def failed_context(
        fixture_id,
        deep=True,
        *,
        include_prediction_benchmark=True,
        fixture_override=None,
    ):
        payload = original(
            fixture_id,
            deep=deep,
            include_prediction_benchmark=include_prediction_benchmark,
            fixture_override=fixture_override,
        )
        payload["injuries"] = []
        payload["recent_observations"] = {"home": [], "away": []}
        payload["errors"] = ["injuries: quota exceeded", "recent_home: timeout"]
        return payload

    client.match_context = failed_context
    records = _adapter(client).fetch(RunContext("run-1", CUTOFF))
    groups = [record.feature_group for record in records]

    assert "injuries" not in groups
    assert "team_form" not in groups
    assert "lineups" in groups
    availability = next(
        record for record in records if record.feature_group == "context_availability"
    )
    assert availability.payload["features"] == {
        "injuries": "provider_error",
        "lineups": "observed_empty",
        "odds": "available",
        "team_form": "provider_error",
    }


def test_missing_context_key_is_not_misrepresented_as_observed_empty():
    client = FakeClient()
    original = client.match_context

    def incomplete_context(*args, **kwargs):
        payload = original(*args, **kwargs)
        del payload["lineups"]
        return payload

    client.match_context = incomplete_context
    records = _adapter(client).fetch(RunContext("run-1", CUTOFF))

    assert not any(record.feature_group == "lineups" for record in records)
    availability = next(
        record for record in records if record.feature_group == "context_availability"
    )
    assert availability.payload["features"]["lineups"] == "missing"


def test_empty_recent_observations_are_reported_as_observed_empty():
    client = FakeClient()
    original = client.match_context

    def empty_recent_context(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload["recent_observations"] = {"home": [], "away": []}
        return payload

    client.match_context = empty_recent_context
    records = _adapter(client).fetch(RunContext("run-1", CUTOFF))

    availability = next(
        record for record in records if record.feature_group == "context_availability"
    )
    assert availability.payload["features"]["team_form"] == "observed_empty"
