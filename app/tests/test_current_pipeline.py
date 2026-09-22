from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json

import pytest

from model_buk.current_pipeline import (
    CurrentDataPipeline,
    CurrentPipelinePolicy,
    PipelineDataError,
    PredictionBatch,
    RunConflictError,
)
from model_buk.opportunity_selector import OpportunityCandidate
from model_buk.snapshots import SnapshotRecord


CUTOFF = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def snapshot(
    *,
    feature_group: str = "odds",
    available_at: datetime | None = None,
    version: str = "v1",
    payload: dict | None = None,
) -> SnapshotRecord:
    available = available_at or CUTOFF - timedelta(minutes=2)
    return SnapshotRecord(
        entity_id="fixture:123",
        feature_group=feature_group,
        event_time=CUTOFF + timedelta(hours=6),
        available_at=available,
        ingested_at=max(available, CUTOFF + timedelta(seconds=1)),
        provider="test-provider",
        provider_version=version,
        payload=payload or {"price": 2.0},
        source_record_id=f"private-source-id-{version}",
    )


def candidate(*, observed_at: datetime = CUTOFF - timedelta(minutes=2)) -> OpportunityCandidate:
    return OpportunityCandidate(
        fixture_id="fixture:123",
        market_key="goals.over.2.5",
        selection="over",
        decimal_odds=2.0,
        probability=0.64,
        probability_lower_bound=0.60,
        observed_at=observed_at,
        snapshot_id="quote:abc",
        bookmaker="test-book",
    )


def candidate_with_market_probability(
    *,
    market_probability: float | None,
    lower: float = 0.60,
) -> OpportunityCandidate:
    return OpportunityCandidate(
        fixture_id="fixture:123",
        market_key="goals.over.2.5",
        selection="over",
        decimal_odds=2.0,
        probability=0.64,
        probability_lower_bound=lower,
        observed_at=CUTOFF - timedelta(minutes=2),
        snapshot_id="quote:abc",
        bookmaker="test-book",
        market_probability=market_probability,
    )


def test_runs_as_of_pipeline_and_persists_only_sanitized_deterministic_artifacts(tmp_path):
    seen = {}

    class Adapter:
        def fetch(self, context):
            seen["adapter_cutoff"] = context.cutoff
            return [
                snapshot(version="old", available_at=CUTOFF - timedelta(minutes=5)),
                snapshot(version="current", payload={"api_secret": "do-not-persist", "price": 2.0}),
            ]

    def predictor(frame, context):
        seen["predictor_cutoff"] = context.cutoff
        seen["versions"] = frame["provider_version"].tolist()
        return PredictionBatch((candidate(),), model_id="offline-model-v1")

    pipeline = CurrentDataPipeline(
        adapters={"local-fixture": Adapter()},
        predictor=predictor,
        predictor_id="offline-predictor-v1",
        output_dir=tmp_path,
        policy=CurrentPipelinePolicy(
            required_feature_groups={"odds": 1},
            max_age_by_feature_group={"odds": timedelta(minutes=10)},
            max_quote_age=timedelta(minutes=10),
        ),
    )

    run = pipeline.run("scheduled/2026-09-18T12", cutoff=CUTOFF)

    assert seen == {
        "adapter_cutoff": CUTOFF,
        "predictor_cutoff": CUTOFF,
        "versions": ["current"],
    }
    assert run.result["selected"][0]["fixture_id"] == "fixture:123"
    assert run.manifest["record_count"] == 1
    assert run.manifest["contains_raw_payloads"] is False
    artifacts = sorted(tmp_path.rglob("*.json"))
    assert [path.name for path in artifacts] == ["manifest.json", "result.json"]
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in artifacts)
    assert "do-not-persist" not in serialized
    assert "private-source-id" not in serialized
    manifest_path = next(path for path in artifacts if path.name == "manifest.json")
    assert json.loads(manifest_path.read_text())["input_sha256"]


def test_committed_run_id_is_idempotent_without_refetching(tmp_path):
    calls = Counter()

    def adapter(context):
        calls["adapter"] += 1
        return [snapshot()]

    def predictor(frame, context):
        calls["predictor"] += 1
        return [candidate()]

    pipeline = CurrentDataPipeline(
        adapters={"fake": adapter},
        predictor=predictor,
        predictor_id="test-v1",
        output_dir=tmp_path,
    )
    first = pipeline.run("same-run", cutoff=CUTOFF)
    second = pipeline.run("same-run", cutoff=CUTOFF)

    assert calls == Counter(adapter=1, predictor=1)
    assert not first.reused
    assert second.reused
    assert first.manifest == second.manifest
    assert first.result == second.result


def test_reusing_run_id_with_different_request_is_rejected(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"fake": lambda context: [snapshot()]},
        predictor=lambda frame, context: [],
        predictor_id="test-v1",
        output_dir=tmp_path,
    )
    pipeline.run("same-run", cutoff=CUTOFF)

    with pytest.raises(RunConflictError, match="different cutoff or configuration"):
        pipeline.run("same-run", cutoff=CUTOFF + timedelta(minutes=1))


def test_future_available_snapshot_is_rejected_before_prediction(tmp_path):
    predictor_called = False

    def predictor(frame, context):
        nonlocal predictor_called
        predictor_called = True
        return []

    pipeline = CurrentDataPipeline(
        adapters={
            "fake": lambda context: [
                snapshot(available_at=CUTOFF + timedelta(seconds=1))
            ]
        },
        predictor=predictor,
        output_dir=tmp_path,
    )

    with pytest.raises(PipelineDataError, match="available after cutoff"):
        pipeline.run("future-data", cutoff=CUTOFF)
    assert not predictor_called


def test_completeness_and_freshness_are_enforced(tmp_path):
    policy = CurrentPipelinePolicy(
        required_feature_groups={"fixtures": 1, "odds": 1},
        max_age_by_feature_group={"odds": timedelta(minutes=10)},
    )
    incomplete = CurrentDataPipeline(
        adapters={"fake": lambda context: [snapshot(feature_group="fixtures")]},
        predictor=lambda frame, context: [],
        output_dir=tmp_path,
        policy=policy,
    )
    with pytest.raises(PipelineDataError, match="incomplete snapshot set: odds"):
        incomplete.run("incomplete", cutoff=CUTOFF)

    stale = CurrentDataPipeline(
        adapters={
            "fake": lambda context: [
                snapshot(feature_group="fixtures"),
                snapshot(feature_group="odds", available_at=CUTOFF - timedelta(minutes=11)),
            ]
        },
        predictor=lambda frame, context: [],
        output_dir=tmp_path,
        policy=policy,
    )
    with pytest.raises(PipelineDataError, match="stale snapshot"):
        stale.run("stale", cutoff=CUTOFF)


def test_future_candidate_is_rejected_even_without_quote_age_policy(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"fake": lambda context: [snapshot()]},
        predictor=lambda frame, context: [
            candidate(observed_at=CUTOFF + timedelta(seconds=1))
        ],
        output_dir=tmp_path,
    )

    with pytest.raises(PipelineDataError, match="candidate observed after"):
        pipeline.run("future-candidate", cutoff=CUTOFF)


def test_dry_run_uses_offline_dependencies_without_writing(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"offline": lambda context: [snapshot()]},
        predictor=lambda frame, context: [candidate()],
        output_dir=tmp_path,
    )

    run = pipeline.run("dry", cutoff=CUTOFF, dry_run=True)

    assert run.persisted is False
    assert run.result["selected"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_recommendations": -1}, "max_recommendations"),
        ({"min_conservative_ev_net": -0.01}, "min_conservative_ev_net"),
        ({"min_conservative_market_edge": -0.01}, "min_conservative_market_edge"),
        ({"max_per_fixture": 0}, "max_per_fixture"),
        ({"max_per_correlation_group": 0}, "max_per_correlation_group"),
    ],
)
def test_policy_rejects_invalid_portfolio_limits(kwargs, message):
    with pytest.raises(ValueError, match=message):
        CurrentPipelinePolicy(**kwargs)


def test_adapter_component_id_is_part_of_idempotency_request(tmp_path):
    class Adapter:
        def __init__(self, component_id):
            self.component_id = component_id

        def fetch(self, context):
            return [snapshot()]

    first = CurrentDataPipeline(
        adapters={"provider": Adapter("provider-v1")},
        predictor=lambda frame, context: [],
        predictor_id="test-v1",
        output_dir=tmp_path,
    )
    second = CurrentDataPipeline(
        adapters={"provider": Adapter("provider-v2")},
        predictor=lambda frame, context: [],
        predictor_id="test-v1",
        output_dir=tmp_path,
    )

    first.run("same-run", cutoff=CUTOFF)
    with pytest.raises(RunConflictError, match="different cutoff or configuration"):
        second.run("same-run", cutoff=CUTOFF)


def test_market_edge_policy_is_applied_and_recorded(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"fake": lambda context: [snapshot()]},
        predictor=lambda frame, context: [
            candidate_with_market_probability(market_probability=0.59, lower=0.60)
        ],
        output_dir=tmp_path,
        policy=CurrentPipelinePolicy(min_conservative_market_edge=0.02),
    )

    run = pipeline.run("market-edge", cutoff=CUTOFF)

    assert run.result["selected"] == []
    assert run.result["rejection_counts"] == {"conservative_market_edge_below_threshold": 1}
    assert run.manifest["policy"]["min_conservative_market_edge"] == 0.02


def test_concurrent_same_run_id_fails_closed_before_fetch(tmp_path):
    calls = Counter()

    def adapter(context):
        calls["adapter"] += 1
        return [snapshot()]

    pipeline = CurrentDataPipeline(
        adapters={"fake": adapter},
        predictor=lambda frame, context: [],
        output_dir=tmp_path,
    )
    run_dir = pipeline._run_dir("locked-run")
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path = tmp_path / f".{run_dir.name}.lock"
    lock_path.write_text("existing process", encoding="utf-8")

    with pytest.raises(RunConflictError, match="already being processed"):
        pipeline.run("locked-run", cutoff=CUTOFF)
    assert calls == Counter()


def test_successful_committed_run_releases_process_lock(tmp_path):
    pipeline = CurrentDataPipeline(
        adapters={"fake": lambda context: [snapshot()]},
        predictor=lambda frame, context: [],
        output_dir=tmp_path,
    )
    pipeline.run("completed-run", cutoff=CUTOFF)

    assert not list(tmp_path.glob("*.lock"))
    assert not list(tmp_path.glob(".*.lock"))
