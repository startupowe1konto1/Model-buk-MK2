from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from model_buk.config import load_corner_v02_config
from model_buk.inference import attach_total_market_price, load_corner_engine, predict_fixture
from model_buk.live_fit import fit_live_corner_models
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
    return parser


def _validate_market_triplet(line: float | None, over: float | None, under: float | None) -> bool:
    supplied = [x is not None for x in (line, over, under)]
    if any(supplied) and not all(supplied):
        raise SystemExit("--line, --over-odds and --under-odds must be supplied together")
    return all(supplied)


def _write_and_print(payload_obj: dict, out: str | None) -> None:
    payload = json.dumps(payload_obj, indent=2)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload)


def main() -> None:
    args = build_parser().parse_args()

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
