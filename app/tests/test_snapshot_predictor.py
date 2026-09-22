from __future__ import annotations

from datetime import datetime, timezone

import pytest

from model_buk.current_pipeline import CurrentDataPipeline, CurrentPipelinePolicy, RunContext
from model_buk.snapshot_predictor import MultiMarketSnapshotPredictor, SnapshotPredictorPolicy
from model_buk.snapshots import SnapshotRecord, snapshots_frame


CUTOFF = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)


class FakeEngine:
    model_id = "pure-test-v1"

    def __init__(self, quality=90.0):
        self.quality = quality
        self.calls = []

    def analyze(self, league_code, date, home_team, away_team):
        self.calls.append((league_code, date, home_team, away_team))
        return {
            "fixture": {"home_team": home_team, "away_team": away_team},
            "data_quality": {"score": self.quality},
            "expected": {},
            "markets": [
                {"market_key": "result.home", "probability": 0.68},
                {"market_key": "result.draw", "probability": 0.20},
                {"market_key": "result.away", "probability": 0.12},
                {"market_key": "btts.yes", "probability": 0.55},
                {"market_key": "btts.no", "probability": 0.45},
                {"market_key": "goals.total.over.2.5", "probability": 0.58},
                {"market_key": "goals.total.under.2.5", "probability": 0.42},
            ],
        }


def record(entity_id, group, payload, *, version="v1"):
    return SnapshotRecord(
        entity_id=entity_id,
        feature_group=group,
        event_time="2026-09-19T18:00:00Z",
        available_at="2026-09-19T09:58:00Z",
        ingested_at="2026-09-19T10:00:01Z",
        provider="test",
        provider_version=version,
        payload=payload,
    )


def fixture_record():
    return record(
        "fixture:11",
        "fixture",
        {
            "fixture_id": 11,
            "kickoff": "2026-09-19T18:00:00Z",
            "league_code": "LL",
            "home": {"id": 1, "name": "Barcelona"},
            "away": {"id": 2, "name": "Sevilla"},
        },
    )


def quote_record(value="Home", odd=2.0, bet_id=1, bookmaker="Book", bookmaker_id=7):
    return record(
        f"fixture:11:quote:{bookmaker}:{value}",
        "odds",
        {
            "bookmaker_id": bookmaker_id,
            "bookmaker": bookmaker,
            "bet_id": bet_id,
            "bet": "Match Winner",
            "value": value,
            "odd": odd,
            "update": "2026-09-19T09:58:00Z",
        },
    )


def test_predictor_keeps_odds_outside_pure_call_and_builds_conservative_candidate():
    engine = FakeEngine()
    predictor = MultiMarketSnapshotPredictor(
        engine,
        policy=SnapshotPredictorPolicy(probability_margin=0.08, min_data_quality=60),
    )

    batch = predictor.predict(
        snapshots_frame([fixture_record(), quote_record()]),
        RunContext("run", CUTOFF),
    )

    assert engine.calls == [("LL", "2026-09-19T18:00:00Z", "Barcelona", "Sevilla")]
    assert len(batch.candidates) == 1
    candidate = batch.candidates[0]
    assert candidate.market_key == "result.home"
    assert candidate.probability == 0.68
    assert candidate.probability_lower_bound == pytest.approx(0.60)
    assert candidate.tax_multiplier == 0.88
    assert candidate.correlation_group == "result"
    assert "pure-test-v1" not in candidate.snapshot_id
    assert batch.model_id == predictor.model_id


def test_predictor_ignores_unmapped_quotes_and_low_quality_fixtures():
    snapshots = snapshots_frame([fixture_record(), quote_record(bet_id=999)])
    assert not MultiMarketSnapshotPredictor(FakeEngine()).predict(
        snapshots, RunContext("run", CUTOFF)
    ).candidates

    low_quality = snapshots_frame([fixture_record(), quote_record()])
    assert not MultiMarketSnapshotPredictor(FakeEngine(quality=20)).predict(
        low_quality, RunContext("run", CUTOFF)
    ).candidates


def test_complete_book_gets_devigged_market_probabilities():
    snapshots = snapshots_frame(
        [
            fixture_record(),
            quote_record("Home", 2.0),
            quote_record("Draw", 4.0),
            quote_record("Away", 4.0),
        ]
    )

    batch = MultiMarketSnapshotPredictor(FakeEngine()).predict(
        snapshots, RunContext("run", CUTOFF)
    )

    probabilities = [candidate.market_probability for candidate in batch.candidates]
    assert probabilities == pytest.approx([0.5, 0.25, 0.25])
    assert sum(probabilities) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("quotes", "expected_keys"),
    [
        (
            [quote_record("Yes", 1.8, bet_id=8), quote_record("No", 2.2, bet_id=8)],
            {"btts.yes", "btts.no"},
        ),
        (
            [
                quote_record("Over 2.5", 2.0, bet_id=5),
                quote_record("Under 2.5", 1.8, bet_id=5),
            ],
            {"goals.total.over.2.5", "goals.total.under.2.5"},
        ),
    ],
)
def test_complete_two_way_book_is_devigged_only_at_the_same_line(quotes, expected_keys):
    batch = MultiMarketSnapshotPredictor(FakeEngine()).predict(
        snapshots_frame([fixture_record(), *quotes]), RunContext("run", CUTOFF)
    )

    assert {candidate.market_key for candidate in batch.candidates} == expected_keys
    assert sum(candidate.market_probability for candidate in batch.candidates) == pytest.approx(1.0)


def test_incomplete_book_has_no_market_probability():
    batch = MultiMarketSnapshotPredictor(FakeEngine()).predict(
        snapshots_frame([fixture_record(), quote_record("Home", 2.0)]),
        RunContext("run", CUTOFF),
    )

    assert len(batch.candidates) == 1
    assert batch.candidates[0].market_probability is None


def test_devig_never_combines_quotes_from_different_bookmakers():
    snapshots = snapshots_frame(
        [
            fixture_record(),
            quote_record("Home", 2.0, bookmaker="Book A", bookmaker_id=7),
            quote_record("Draw", 4.0, bookmaker="Book A", bookmaker_id=7),
            quote_record("Away", 4.0, bookmaker="Book B", bookmaker_id=8),
        ]
    )

    batch = MultiMarketSnapshotPredictor(FakeEngine()).predict(
        snapshots, RunContext("run", CUTOFF)
    )

    assert len(batch.candidates) == 3
    assert all(candidate.market_probability is None for candidate in batch.candidates)


def test_policy_and_model_identity_are_explicit():
    first = MultiMarketSnapshotPredictor(FakeEngine())
    second = MultiMarketSnapshotPredictor(
        FakeEngine(), policy=SnapshotPredictorPolicy(probability_margin=0.10)
    )
    assert first.model_id != second.model_id
    with pytest.raises(ValueError, match="devig_method"):
        SnapshotPredictorPolicy(devig_method="unknown")

    class UnversionedEngine:
        pass

    with pytest.raises(ValueError, match="engine_id is required"):
        MultiMarketSnapshotPredictor(UnversionedEngine())


def test_end_to_end_snapshot_prediction_and_selection_is_paper_ready(tmp_path):
    source_records = [fixture_record(), quote_record(odd=2.0)]
    predictor = MultiMarketSnapshotPredictor(
        FakeEngine(),
        policy=SnapshotPredictorPolicy(probability_margin=0.05, tax_multiplier=0.88),
    )
    pipeline = CurrentDataPipeline(
        adapters={"offline-snapshot-source": lambda context: source_records},
        predictor=predictor,
        output_dir=tmp_path,
        policy=CurrentPipelinePolicy(
            required_feature_groups={"fixture": 1, "odds": 1},
            min_conservative_ev_net=0.02,
            max_recommendations=10,
        ),
    )

    run = pipeline.run("paper-run", cutoff=CUTOFF, dry_run=True)

    assert run.result["model_id"] == predictor.model_id
    assert run.result["candidate_count"] == 1
    assert len(run.result["selected"]) == 1
    assert run.result["selected"][0]["market_key"] == "result.home"
    assert run.result["selected"][0]["conservative_ev_net"] > 0.02
    assert run.persisted is False
