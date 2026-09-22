from __future__ import annotations

import argparse
from dataclasses import replace
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
