"""Run fail-closed per-league promotion checks on nested OOS predictions."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from model_buk.artifact_identity import sha256_file
from model_buk.evaluation.gates import (
    PromotionPolicy,
    decide_promotion,
    evaluate_model_against_market,
)
from model_buk.goals_backtest import build_market_1x2_benchmark
from model_buk.ingestion.football_data_uk import load_football_data_uk_archives


_PREDICTION_COLUMNS = {
    "source_row_id",
    "league_code",
    "outer_fold",
    "actual",
    "p_home",
    "p_draw",
    "p_away",
}


def _policy_from_json(path: str | Path | None) -> PromotionPolicy:
    if path is None:
        return PromotionPolicy()
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read promotion policy: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("promotion policy must be a JSON object")
    allowed = set(PromotionPolicy.__dataclass_fields__)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"promotion policy contains unknown fields: {', '.join(unknown)}")
    return PromotionPolicy(**value)


def run_archive_promotion_gate(
    archives: list[str | Path] | tuple[str | Path, ...],
    predictions_path: str | Path,
    outdir: str | Path,
    *,
    market_odds_kind: str = "closing",
    market_provider: str = "market_average",
    policy_path: str | Path | None = None,
    bootstrap_resamples: int = 2_000,
    seed: int = 42,
) -> dict[str, Any]:
    """Evaluate forecast quality per league; never auto-promote a model artifact."""
    if market_odds_kind not in {"opening", "closing"}:
        raise ValueError("market_odds_kind must be opening or closing")
    if bootstrap_resamples < 100:
        raise ValueError("bootstrap_resamples must be at least 100")
    output = Path(outdir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"output directory is not empty: {output}")
    predictions = pd.read_csv(predictions_path)
    missing = sorted(_PREDICTION_COLUMNS - set(predictions.columns))
    if missing:
        raise ValueError(f"nested OOS predictions are missing columns: {', '.join(missing)}")
    if predictions["source_row_id"].duplicated().any():
        raise ValueError("nested predictions must contain one outer OOS row per match")

    batch = load_football_data_uk_archives(archives)
    matches = batch.matches.dropna(subset=["home_goals", "away_goals"]).copy()
    matches["actual_match"] = [
        "home" if home > away else "draw" if home == away else "away"
        for home, away in zip(matches["home_goals"], matches["away_goals"])
    ]
    market = build_market_1x2_benchmark(
        batch.odds,
        odds_kind=market_odds_kind,
        provider=market_provider,
    )
    canonical = matches[
        ["source_row_id", "league", "kickoff", "actual_match"]
    ].rename(
        columns={"league": "archive_league", "kickoff": "archive_kickoff"}
    )
    source = predictions.merge(
        canonical,
        on="source_row_id",
        how="inner",
        validate="one_to_one",
    )
    if source.empty:
        raise ValueError("no OOS predictions matched settled archive rows")
    if source["actual"].ne(source["actual_match"]).any():
        raise ValueError("prediction actuals do not match canonical archive settlements")
    if source["league_code"].astype(str).ne(source["archive_league"].astype(str)).any():
        raise ValueError("prediction league codes do not match canonical archive leagues")
    source = source.merge(market, on="source_row_id", how="inner", validate="one_to_one")
    if source.empty:
        raise ValueError(f"no matched OOS rows have a complete {market_provider} {market_odds_kind} 1X2 book")

    policy = _policy_from_json(policy_path)
    decisions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for league, group in source.groupby("league_code", sort=True):
        probabilities = group[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)
        market_probabilities = group[
            ["market_p_home", "market_p_draw", "market_p_away"]
        ].to_numpy(dtype=float)
        kickoff = pd.to_datetime(
            group["archive_kickoff"], errors="coerce", utc=True, format="mixed"
        )
        report = evaluate_model_against_market(
            group["actual"].to_numpy(),
            probabilities,
            market_probabilities,
            classes=("home", "draw", "away"),
            bootstrap_resamples=bootstrap_resamples,
            seed=seed,
            bootstrap_clusters=kickoff.dt.date.astype(str).to_numpy(),
        )
        decision = decide_promotion(report, policy)
        decisions.append(
            {
                "league": str(league),
                "decision": "hold_for_paper" if decision.promote else "reject",
                "quality_gate_passed": decision.promote,
                "promotion_eligible": False,
                "blocking_reason": "historical archive has no point-in-time quote timestamps",
                "reasons": list(decision.reasons),
                "checks": decision.checks,
                "outer_folds": sorted(group["outer_fold"].astype(str).unique().tolist()),
            }
        )
        metrics.append(
            {
                "league": str(league),
                "n": report.model.n,
                "model_log_loss": report.model.log_loss,
                "market_log_loss": report.market.log_loss,
                "log_loss_improvement": report.log_loss_improvement,
                "log_loss_improvement_ci_lower": report.log_loss_improvement_ci.lower,
                "log_loss_improvement_ci_upper": report.log_loss_improvement_ci.upper,
                "model_brier": report.model.brier,
                "market_brier": report.market.brier,
                "ece_regression": report.ece_regression,
                "model_ece": report.model.ece,
                "market_odds_kind": market_odds_kind,
                "market_provider": market_provider,
                "quality_gate_passed": decision.promote,
                "promotion_eligible": False,
            }
        )

    output.mkdir(parents=True, exist_ok=True)
    metrics_frame = pd.DataFrame(metrics)
    metrics_frame.to_csv(output / "promotion_metrics.csv", index=False)
    decisions_path = output / "promotion_decisions.json"
    decisions_path.write_text(json.dumps(decisions, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "status": "completed",
        "promotion_eligible": False,
        "promotion_blocker": "source quote timestamps are absent; this is forecast-quality evidence only",
        "market_odds_kind": market_odds_kind,
        "market_provider": market_provider,
        "policy": asdict(policy),
        "bootstrap_resamples": bootstrap_resamples,
        "seed": seed,
        "matched_predictions": len(source),
        "leagues": [item["league"] for item in decisions],
        "predictions": {
            "path": str(Path(predictions_path).resolve()),
            "sha256": sha256_file(predictions_path),
        },
        "archives": [
            {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for path in archives
        ],
        "reports": ["promotion_metrics.csv", "promotion_decisions.json"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return {**manifest, "decisions": decisions}


__all__ = ["run_archive_promotion_gate"]
