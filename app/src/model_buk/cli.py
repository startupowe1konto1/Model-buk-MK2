from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path

import pandas as pd
from requests import RequestException

from model_buk.config import load_corner_v02_config
from model_buk.inference import attach_total_market_price, load_corner_engine, predict_fixture
from model_buk.live_fit import fit_live_corner_models
from model_buk.paper_config import PaperScanConfig, PaperScanConfigError, load_paper_scan_config
from model_buk.paper_scan import (
    PaperScanError,
    ProviderUnavailableError,
    build_paper_scan,
    run_paper_scan,
)
from model_buk.current_pipeline import PipelineDataError, RunConflictError
from model_buk.artifact_identity import sha256_file
from model_buk.evaluation.archive_replay import build_football_data_replay_frame
from model_buk.evaluation.archive_goal_runner import run_nested_archive_forecasts
from model_buk.evaluation.archive_promotion import run_archive_promotion_gate
from model_buk.evaluation.replay_runner import run_historical_replay
from model_buk.ingestion.football_data_uk import load_football_data_uk_archives
from model_buk.suite import load_live_corner_suite, predict_with_live_suite
from model_buk.v02_backtest import run_v02_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-buk", description="Model Buk research engine")
    sub = parser.add_subparsers(dest="command", required=True)

    backtest = sub.add_parser("backtest-corners-v02", help="Run dual home/away corner model v0.2")
    backtest.add_argument("--data", default="data/processed/epl_total_corners_v01.csv.gz")
    backtest.add_argument("--config", default="config/corners_v02.toml")
    backtest.add_argument("--outdir", default="outputs")
    backtest.add_argument("--modeldir", default="models")

    fit_live = sub.add_parser("fit-live-corners", help="Refit frozen corner specs on all completed local matches")
    fit_live.add_argument("--data", default="data/processed/epl_total_corners_v01.csv.gz")
    fit_live.add_argument("--config", default="config/corners_v02.toml")
    fit_live.add_argument("--eval-v01-meta", default="models/metadata.json")
    fit_live.add_argument("--eval-v02-meta", default="models/corner_dual_v0_2_metadata.json")
    fit_live.add_argument("--model-root", default="models")

    predict_suite = sub.add_parser("predict-corners", help="Use the current champion/challenger corner suite")
    predict_suite.add_argument("--history", default="data/canonical/epl_matches_v01.csv.gz")
    predict_suite.add_argument("--model-root", default="models")
    predict_suite.add_argument("--config", default="config/corners_v02.toml")
    predict_suite.add_argument("--date", required=True)
    predict_suite.add_argument("--home", required=True)
    predict_suite.add_argument("--away", required=True)
    predict_suite.add_argument("--line", type=float, default=None, help="Optional total-corners line for market check")
    predict_suite.add_argument("--over-odds", type=float, default=None)
    predict_suite.add_argument("--under-odds", type=float, default=None)
    predict_suite.add_argument("--out", default=None)

    predict = sub.add_parser("predict-corners-v02", help="Research-only dual v0.2 pricing for one future fixture")
    predict.add_argument("--history", default="data/canonical/epl_matches_v01.csv.gz")
    predict.add_argument("--modeldir", default="models")
    predict.add_argument("--config", default="config/corners_v02.toml")
    predict.add_argument("--date", required=True, help="Fixture timestamp/date, e.g. 2026-09-12 17:30")
    predict.add_argument("--home", required=True)
    predict.add_argument("--away", required=True)
    predict.add_argument("--line", type=float, default=None, help="Optional total-corners line to compare with market")
    predict.add_argument("--over-odds", type=float, default=None)
    predict.add_argument("--under-odds", type=float, default=None)
    predict.add_argument("--out", default=None, help="Optional JSON output path")

    scan = sub.add_parser(
        "scan-current",
        help="Run the current-data pipeline in paper/shadow mode",
    )
    scan.add_argument("--config", default="config/paper_scan.toml")
    scan.add_argument("--mode", choices=("live", "offline"), default="live")
    scan.add_argument("--snapshots", default=None, help="JSON/JSONL snapshot input for offline mode")
    scan.add_argument("--api-key-env", default="API_FOOTBALL_KEY")
    scan.add_argument("--cache-dir", default="runtime/api_cache/paper-scan")
    scan.add_argument("--history", default=None)
    scan.add_argument("--outdir", default=None)
    scan.add_argument("--league", action="append", default=None)
    scan.add_argument("--day-offset", action="append", type=int, default=None)
    scan.add_argument("--max-fixtures", type=int, default=None)
    scan.add_argument("--max-recommendations", type=int, default=None)
    scan.add_argument("--min-ev-net", type=float, default=None)
    scan.add_argument("--tax-multiplier", type=float, default=None)
    scan.add_argument("--execution-cost", type=float, default=None)
    scan.add_argument("--probability-haircut", type=float, default=None)
    scan.add_argument("--min-data-quality", type=float, default=None)
    scan.add_argument("--max-quote-age-minutes", type=float, default=None)
    scan.add_argument(
        "--include-context",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    scan.add_argument(
        "--include-recent-observations",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    scan.add_argument("--cutoff", default=None, help="Timezone-aware ISO-8601 timestamp")
    scan.add_argument("--run-id", default=None)
    scan.add_argument("--dry-run", action="store_true")
    scan.add_argument("--out", default=None, help="Optional JSON summary output path")

    replay = sub.add_parser(
        "replay-history",
        help="Run a plan-driven nested historical opportunity replay",
    )
    replay.add_argument("--input", required=True, help="CSV or JSONL replay observations")
    replay.add_argument("--plan", required=True, help="JSON strategy and nested-fold plan")
    replay.add_argument("--outdir", required=True, help="New or empty directory for replay evidence")
    replay.add_argument(
        "--allow-quarantine",
        action="store_true",
        help="Continue using valid rows while recording quarantined rows in the report",
    )
    replay.add_argument(
        "--by-stratum",
        action="store_true",
        help="Select and score strategies independently for each input stratum_id (for example, league)",
    )

    prepare_replay = sub.add_parser(
        "prepare-football-data-replay",
        help="Join nested OOS 1X2 forecasts to local football-data.co.uk ZIP archives",
    )
    prepare_replay.add_argument("--archives", nargs="+", required=True, help="Source ZIP archive paths")
    prepare_replay.add_argument("--predictions", required=True, help="CSV from nested OOS model forecasts")
    prepare_replay.add_argument("--out", required=True, help="New output CSV path")
    prepare_replay.add_argument(
        "--assume-opening-available",
        action="store_true",
        help="Explicitly accept the research-only assumption that opening odds were available at cutoff",
    )
    prepare_replay.add_argument("--cutoff-lead-minutes", type=float, default=60.0)
    prepare_replay.add_argument("--probability-haircut", type=float, default=0.05)
    prepare_replay.add_argument("--tax-multiplier", type=float, default=0.88)
    prepare_replay.add_argument("--execution-cost", type=float, default=0.0)

    nested_goals = sub.add_parser(
        "nested-goals-archives",
        help="Fit per-league nested OOS 1X2 forecasts from football-data.co.uk ZIP archives",
    )
    nested_goals.add_argument("--archives", nargs="+", required=True, help="Source ZIP archive paths")
    nested_goals.add_argument("--plan", required=True, help="JSON nested model and fold plan")
    nested_goals.add_argument("--outdir", required=True, help="New or empty output directory")
    nested_goals.add_argument("--league", action="append", default=None, help="Restrict to league code; repeatable")

    promotion = sub.add_parser(
        "archive-promotion-gate",
        help="Compare nested OOS forecasts to the market, per league (timestamps still block promotion)",
    )
    promotion.add_argument("--archives", nargs="+", required=True, help="Source ZIP archive paths")
    promotion.add_argument("--predictions", required=True, help="CSV from nested OOS forecasts")
    promotion.add_argument("--outdir", required=True, help="New or empty output directory")
    promotion.add_argument("--market-odds-kind", choices=("opening", "closing"), default="closing")
    promotion.add_argument("--market-provider", default="market_average")
    promotion.add_argument("--policy", default=None, help="Optional JSON promotion policy")
    promotion.add_argument("--bootstrap-resamples", type=int, default=2000)
    promotion.add_argument("--seed", type=int, default=42)
    return parser


def _validate_market_triplet(line: float | None, over: float | None, under: float | None) -> bool:
    supplied = [x is not None for x in (line, over, under)]
    if any(supplied) and not all(supplied):
        raise SystemExit("--line, --over-odds and --under-odds must be supplied together")
    return all(supplied)


def _write_and_print(payload_obj: dict, out: str | None) -> None:
    payload = json.dumps(payload_obj, indent=2, ensure_ascii=False)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload)


def _paper_config_from_args(args: argparse.Namespace) -> PaperScanConfig:
    config = load_paper_scan_config(args.config)
    overrides: dict = {}
    mapping = {
        "history": "history_path",
        "outdir": "output_dir",
        "max_fixtures": "max_fixtures",
        "max_recommendations": "max_recommendations",
        "min_ev_net": "min_conservative_ev_net",
        "tax_multiplier": "tax_multiplier",
        "execution_cost": "execution_cost",
        "probability_haircut": "probability_haircut",
        "min_data_quality": "min_data_quality",
        "include_context": "include_context",
        "include_recent_observations": "include_recent_observations",
    }
    for argument, field_name in mapping.items():
        value = getattr(args, argument)
        if value is not None:
            overrides[field_name] = value
    if args.league is not None:
        overrides["league_codes"] = tuple(args.league)
    if args.day_offset is not None:
        overrides["api_day_offsets"] = tuple(args.day_offset)
    if args.max_quote_age_minutes is not None:
        from datetime import timedelta

        overrides["quote_freshness"] = timedelta(minutes=args.max_quote_age_minutes)
    return replace(config, **overrides)


def _run_scan_current(args: argparse.Namespace) -> dict:
    config = _paper_config_from_args(args)
    api_key = os.getenv(args.api_key_env) if args.mode == "live" else None
    built = build_paper_scan(
        config,
        source_mode=args.mode,
        api_key=api_key,
        snapshots_path=args.snapshots,
        cache_dir=args.cache_dir,
    )
    return run_paper_scan(
        built,
        cutoff=args.cutoff,
        run_id=args.run_id,
        dry_run=args.dry_run,
    )


def _scan_error(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, PaperScanConfigError):
        return "config_error", 2
    if isinstance(exc, ProviderUnavailableError):
        return "provider_unavailable", 3
    if isinstance(exc, RunConflictError):
        return "run_conflict", 4
    if isinstance(exc, PipelineDataError):
        return "data_quality_error", 5
    if isinstance(exc, PaperScanError):
        return "not_ready", 6
    if isinstance(exc, ValueError):
        return "data_quality_error", 5
    return "provider_error", 7


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "archive-promotion-gate":
        try:
            payload = run_archive_promotion_gate(
                args.archives,
                args.predictions,
                args.outdir,
                market_odds_kind=args.market_odds_kind,
                market_provider=args.market_provider,
                policy_path=args.policy,
                bootstrap_resamples=args.bootstrap_resamples,
                seed=args.seed,
            )
        except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
            _write_and_print({"status": "promotion_gate_error", "message": str(exc)}, None)
            raise SystemExit(5) from None
        _write_and_print(
            {
                "status": payload["status"],
                "promotion_eligible": False,
                "matched_predictions": payload["matched_predictions"],
                "leagues": payload["leagues"],
                "output_dir": str(Path(args.outdir).resolve()),
            },
            None,
        )
        return

    if args.command == "nested-goals-archives":
        try:
            payload = run_nested_archive_forecasts(
                args.archives,
                args.plan,
                args.outdir,
                leagues=args.league,
            )
        except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
            _write_and_print({"status": "nested_forecast_error", "message": str(exc)}, None)
            raise SystemExit(5) from None
        _write_and_print(
            {
                "status": payload["status"],
                "successful_leagues": payload["successful_leagues"],
                "skipped_leagues": payload["skipped_leagues"],
                "prediction_rows": payload["prediction_rows"],
                "output_dir": str(Path(args.outdir).resolve()),
            },
            None,
        )
        return

    if args.command == "prepare-football-data-replay":
        output_path = Path(args.out)
        manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
        try:
            if output_path.exists() or manifest_path.exists():
                raise FileExistsError(f"output already exists: {output_path}")
            batch = load_football_data_uk_archives(args.archives)
            predictions = pd.read_csv(args.predictions)
            replay_frame = build_football_data_replay_frame(
                batch,
                predictions,
                assume_opening_available_at_cutoff=args.assume_opening_available,
                cutoff_lead=timedelta(minutes=args.cutoff_lead_minutes),
                probability_haircut=args.probability_haircut,
                tax_multiplier=args.tax_multiplier,
                execution_cost=args.execution_cost,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            replay_frame.to_csv(output_path, index=False)
            source_manifest = {
                "status": "research_dataset_prepared",
                "source_assumption": sorted(replay_frame["source_assumption"].unique().tolist()),
                "rows_written": len(replay_frame),
                "matched_fixtures": int(replay_frame["fixture_id"].nunique()),
                "predictions_path": str(Path(args.predictions).resolve()),
                "predictions_sha256": sha256_file(args.predictions),
                "archives": [
                    {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
                    for path in args.archives
                ],
                "output_csv": str(output_path.resolve()),
                "promotion_eligible": False,
            }
            manifest_path.write_text(
                json.dumps(source_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _write_and_print({"status": "replay_data_error", "message": str(exc)}, None)
            raise SystemExit(5) from None
        _write_and_print(
            {
                "status": source_manifest["status"],
                "rows_written": source_manifest["rows_written"],
                "matched_fixtures": source_manifest["matched_fixtures"],
                "promotion_eligible": False,
                "output_csv": source_manifest["output_csv"],
                "manifest": str(manifest_path.resolve()),
            },
            None,
        )
        return

    if args.command == "replay-history":
        try:
            payload = run_historical_replay(
                args.input,
                args.plan,
                args.outdir,
                allow_quarantine=args.allow_quarantine,
                by_stratum=args.by_stratum,
            )
        except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
            _write_and_print(
                {"status": "replay_error", "message": str(exc)},
                None,
            )
            raise SystemExit(5) from None
        _write_and_print(
            {
                "status": payload["status"],
                "source_sha256": payload["data"]["source_sha256"],
                "rows_accepted": payload["data"]["rows_accepted"],
                "rows_quarantined": payload["data"]["rows_quarantined"],
                "selected_strategies": payload["selected_strategies"],
                "output_dir": str(Path(args.outdir).resolve()),
            },
            None,
        )
        return

    if args.command == "scan-current":
        try:
            payload = _run_scan_current(args)
        except (
            PaperScanConfigError,
            PaperScanError,
            PipelineDataError,
            RunConflictError,
            RequestException,
            RuntimeError,
            ValueError,
        ) as exc:
            status, exit_code = _scan_error(exc)
            _write_and_print(
                {
                    "status": status,
                    "mode": "paper/shadow",
                    "source_mode": args.mode,
                    "message": str(exc),
                },
                args.out,
            )
            raise SystemExit(exit_code) from None
        _write_and_print(payload, args.out)
        return

    if args.command == "backtest-corners-v02":
        cfg = load_corner_v02_config(args.config)
        summary = run_v02_pipeline(args.data, cfg, args.outdir, args.modeldir)
        print(json.dumps(summary, indent=2))
        return

    if args.command == "fit-live-corners":
        cfg = load_corner_v02_config(args.config)
        registry = fit_live_corner_models(
            args.data,
            cfg,
            eval_v01_metadata_path=args.eval_v01_meta,
            eval_v02_metadata_path=args.eval_v02_meta,
            model_root=args.model_root,
        )
        print(json.dumps(registry, indent=2))
        return

    if args.command == "predict-corners":
        cfg = load_corner_v02_config(args.config)
        history = pd.read_csv(args.history)
        suite = load_live_corner_suite(args.model_root)
        prediction = predict_with_live_suite(
            history,
            suite,
            cfg,
            date=args.date,
            home_team=args.home,
            away_team=args.away,
        )
        if _validate_market_triplet(args.line, args.over_odds, args.under_odds):
            total_stub = {"total_markets": prediction["total_corners"]["markets"]}
            priced = attach_total_market_price(
                total_stub,
                cfg,
                line=args.line,
                over_odds=args.over_odds,
                under_odds=args.under_odds,
            )
            prediction["market_check"] = priced["market_check"]
        _write_and_print(prediction, args.out)
        return

    if args.command == "predict-corners-v02":
        cfg = load_corner_v02_config(args.config)
        history = pd.read_csv(args.history)
        engine = load_corner_engine(args.modeldir)
        prediction = predict_fixture(
            history,
            engine,
            cfg,
            date=args.date,
            home_team=args.home,
            away_team=args.away,
        )
        if _validate_market_triplet(args.line, args.over_odds, args.under_odds):
            prediction = attach_total_market_price(
                prediction,
                cfg,
                line=args.line,
                over_odds=args.over_odds,
                under_odds=args.under_odds,
            )
        _write_and_print(prediction, args.out)
        return

    raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
