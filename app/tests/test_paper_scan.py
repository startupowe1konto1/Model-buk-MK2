from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from model_buk.paper_config import PaperScanConfig
from model_buk.paper_scan import (
    OfflineSnapshotAdapter,
    PaperScanError,
    ProviderUnavailableError,
    build_paper_scan,
    default_run_id,
    normalize_cutoff,
    run_paper_scan,
)


CUTOFF = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


class FakeEngine:
    def analyze(self, league_code, date, home_team, away_team):
        return {
            "fixture": {"home_team": home_team, "away_team": away_team},
            "data_quality": {"score": 90.0},
            "expected": {},
            "markets": [{"market_key": "result.home", "probability": 0.68}],
        }


def snapshot_values():
    common = {
        "event_time": "2026-09-20T18:00:00Z",
        "available_at": "2026-09-20T09:58:00Z",
        "ingested_at": "2026-09-20T10:00:01Z",
        "provider": "offline-test",
        "provider_version": "v1",
    }
    return [
        {
            **common,
            "entity_id": "fixture:11",
            "feature_group": "fixture",
            "payload": {
                "fixture_id": 11,
                "kickoff": "2026-09-20T18:00:00Z",
                "league_code": "LL",
                "home": {"id": 1, "name": "Barcelona"},
                "away": {"id": 2, "name": "Sevilla"},
            },
        },
        {
            **common,
            "entity_id": "fixture:11:quote:home",
            "feature_group": "odds",
            "payload": {
                "bookmaker_id": 7,
                "bookmaker": "Book",
                "bet_id": 1,
                "bet": "Match Winner",
                "value": "Home",
                "odd": 2.0,
                "update": "2026-09-20T09:58:00Z",
            },
        },
    ]


def make_config(tmp_path, history_path):
    return PaperScanConfig(
        history_path=history_path,
        output_dir=tmp_path / "runs",
        required_feature_groups={"fixture": 1, "odds": 1},
    )


def test_offline_scan_is_deterministic_and_does_not_construct_network_client(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text("synthetic-history", encoding="utf-8")
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(json.dumps({"snapshots": snapshot_values()}), encoding="utf-8")
    config = make_config(tmp_path, history)

    built = build_paper_scan(
        config,
        source_mode="offline",
        snapshots_path=snapshots,
        engine=FakeEngine(),
    )
    output = run_paper_scan(built, cutoff=CUTOFF, dry_run=True)

    assert output["mode"] == "paper/shadow"
    assert output["source_mode"] == "offline"
    assert output["run_id"] == "scan-20260920T100000Z"
    assert output["candidate_count"] == 1
    assert output["selected_count"] == 1
    assert output["persisted"] is False
    assert list(config.output_dir.glob("**/*")) == []
    assert str(tmp_path) not in json.dumps(output)


def test_history_bytes_change_engine_identity(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text("first", encoding="utf-8")
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(json.dumps(snapshot_values()), encoding="utf-8")
    config = make_config(tmp_path, history)
    first = build_paper_scan(
        config, source_mode="offline", snapshots_path=snapshots, engine=FakeEngine()
    )
    history.write_text("second", encoding="utf-8")
    second = build_paper_scan(
        config, source_mode="offline", snapshots_path=snapshots, engine=FakeEngine()
    )
    assert first.engine_manifest["engine_id"] != second.engine_manifest["engine_id"]


def test_live_mode_requires_provider_and_unknown_league_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    history = tmp_path / "history.csv"
    history.write_text("history", encoding="utf-8")
    config = make_config(tmp_path, history)
    with pytest.raises(ProviderUnavailableError, match="API_FOOTBALL_KEY"):
        build_paper_scan(config, source_mode="live", api_key="", engine=FakeEngine())

    invalid = PaperScanConfig(
        history_path=history,
        output_dir=tmp_path / "runs",
        league_codes=("UNKNOWN",),
    )
    with pytest.raises(PaperScanError, match="unsupported league"):
        build_paper_scan(invalid, source_mode="live", api_key="x", engine=FakeEngine())


def test_offline_input_and_cutoff_validation(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text("history", encoding="utf-8")
    config = make_config(tmp_path, history)
    with pytest.raises(PaperScanError, match="snapshots_path"):
        build_paper_scan(config, source_mode="offline", engine=FakeEngine())
    with pytest.raises(PaperScanError, match="timezone"):
        normalize_cutoff("2026-09-20T10:00:00")
    assert default_run_id(CUTOFF) == "scan-20260920T100000Z"


def test_offline_jsonl_and_corruption_are_handled(tmp_path):
    path = tmp_path / "snapshots.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in snapshot_values()), encoding="utf-8")
    adapter = OfflineSnapshotAdapter(path)
    assert len(adapter.fetch(None)) == 2

    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    with pytest.raises(PaperScanError, match="cannot read"):
        OfflineSnapshotAdapter(broken).fetch(None)
