from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from model_buk import cli
from model_buk.paper_config import PaperScanConfig


def test_scan_parser_accepts_repeatable_scope_and_safe_flags():
    args = cli.build_parser().parse_args(
        [
            "scan-current",
            "--mode", "offline",
            "--snapshots", "snapshots.json",
            "--league", "EPL",
            "--league", "LL",
            "--day-offset", "0",
            "--day-offset", "2",
            "--no-include-context",
            "--include-recent-observations",
            "--no-archive-live-snapshots",
            "--snapshot-archive-dir", "archive",
            "--dry-run",
        ]
    )

    assert args.mode == "offline"
    assert args.league == ["EPL", "LL"]
    assert args.day_offset == [0, 2]
    assert args.include_context is False
    assert args.include_recent_observations is True
    assert args.archive_live_snapshots is False
    assert args.snapshot_archive_dir == "archive"
    assert args.dry_run is True


def test_context_export_parser_has_safe_explicit_output():
    args = cli.build_parser().parse_args(
        [
            "export-context-features",
            "--archive-dir", "archive",
            "--out", "context.csv",
        ]
    )

    assert args.archive_dir == "archive"
    assert args.out == "context.csv"


def test_context_training_parser_requires_features_outcomes_and_output():
    args = cli.build_parser().parse_args(
        [
            "prepare-context-training",
            "--features", "context.csv",
            "--outcomes", "outcomes.csv",
            "--out", "training.csv",
        ]
    )

    assert args.features == "context.csv"
    assert args.outcomes == "outcomes.csv"
    assert args.out == "training.csv"
    assert args.allow_outcome_quarantine is False


def test_cli_overrides_versioned_config_without_reading_a_secret(monkeypatch, tmp_path):
    base = PaperScanConfig(
        history_path=tmp_path / "history.csv",
        output_dir=tmp_path / "runs",
    )
    monkeypatch.setattr(cli, "load_paper_scan_config", lambda path: base)
    args = cli.build_parser().parse_args(
        [
            "scan-current",
            "--mode", "offline",
            "--snapshots", "snapshots.json",
            "--history", "override.csv",
            "--outdir", "other-runs",
            "--snapshot-archive-dir", "other-archive",
            "--max-fixtures", "4",
            "--max-recommendations", "3",
            "--min-ev-net", "0.04",
            "--max-quote-age-minutes", "12",
        ]
    )

    config = cli._paper_config_from_args(args)
    assert config.history_path == Path("override.csv")
    assert config.output_dir == Path("other-runs")
    assert config.snapshot_archive_dir == Path("other-archive")
    assert config.max_fixtures == 4
    assert config.max_recommendations == 3
    assert config.min_conservative_ev_net == 0.04
    assert config.quote_freshness == timedelta(minutes=12)


def test_scan_command_passes_only_environment_key_to_live_factory(monkeypatch, tmp_path):
    base = PaperScanConfig(
        history_path=tmp_path / "history.csv",
        output_dir=tmp_path / "runs",
    )
    monkeypatch.setattr(cli, "load_paper_scan_config", lambda path: base)
    monkeypatch.setenv("TEST_API_KEY", "private-value")
    seen = {}

    def fake_build(config, **kwargs):
        seen.update(kwargs)
        return "built"

    monkeypatch.setattr(cli, "build_paper_scan", fake_build)
    monkeypatch.setattr(
        cli,
        "run_paper_scan",
        lambda built, **kwargs: {"status": "ok", "built": built, **kwargs},
    )
    args = cli.build_parser().parse_args(
        ["scan-current", "--api-key-env", "TEST_API_KEY", "--dry-run"]
    )

    output = cli._run_scan_current(args)

    assert seen["api_key"] == "private-value"
    assert output["status"] == "ok"
    assert output["dry_run"] is True


def test_real_offline_cli_composition_runs_without_network(tmp_path, multileague_history_path):
    snapshots = tmp_path / "snapshots.json"
    common = {
        "event_time": "2024-08-10T18:00:00Z",
        "available_at": "2024-08-01T09:58:00Z",
        "ingested_at": "2024-08-01T10:00:01Z",
        "provider": "offline-test",
        "provider_version": "v1",
    }
    snapshots.write_text(
        json.dumps(
            [
                {
                    **common,
                    "entity_id": "fixture:11",
                    "feature_group": "fixture",
                    "payload": {
                        "fixture_id": 11,
                        "kickoff": "2024-08-10T18:00:00Z",
                        "league_code": "EPL",
                        "home": {"id": 1, "name": "Arsenal"},
                        "away": {"id": 2, "name": "Chelsea"},
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
                        "update": "2024-08-01T09:58:00Z",
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "paper.toml"
    config_path.write_text(
        "\n".join(
            [
                "[paper_scan]",
                f'history_path = "{multileague_history_path.as_posix()}"',
                f'output_dir = "{(tmp_path / "runs").as_posix()}"',
                'model_id = "offline-e2e-v1"',
                "min_data_quality = 0",
                "probability_haircut = 0",
                "quote_freshness_minutes = 30",
                "",
                "[paper_scan.required_feature_groups]",
                "fixture = 1",
                "odds = 1",
            ]
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(
        [
            "scan-current",
            "--config", str(config_path),
            "--mode", "offline",
            "--snapshots", str(snapshots),
            "--cutoff", "2024-08-01T10:00:00Z",
            "--dry-run",
        ]
    )

    output = cli._run_scan_current(args)

    assert output["status"] == "ok"
    assert output["source_mode"] == "offline"
    assert output["candidate_count"] == 1
    assert output["persisted"] is False
