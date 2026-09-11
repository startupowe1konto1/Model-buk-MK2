from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from model_buk.config import CornerV02Config
from model_buk.distributions import (
    convolved_total_over_probability,
    estimate_nb_alpha,
    nb_over_probability,
    nb_over_probability_array,
)
from model_buk.evaluation.metrics import (
    calibration_table,
    count_metrics,
    expected_calibration_error,
    probability_metrics,
)
from model_buk.markets.pricing import devig_two_way, expected_value, fair_odds
from model_buk.models.corner_dual_v02 import fit_dual_model
from model_buk.strengths import add_corner_strength_features

TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_LINES = (2.5, 3.5, 4.5, 5.5, 6.5, 7.5)


def _ensure_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], format="mixed")
    return out


def prepare_v02_frame(frame: pd.DataFrame, cfg: CornerV02Config) -> pd.DataFrame:
    frame = _ensure_dates(frame)
    return add_corner_strength_features(
        frame,
        shrinkage_games=cfg.shrinkage_games,
        min_rate=cfg.min_rate,
        max_rate=cfg.max_rate,
    )


def run_dual_oof(frame: pd.DataFrame, cfg: CornerV02Config) -> pd.DataFrame:
    data = frame[(frame.source == "historical") & (frame.date >= cfg.train_start)].copy()
    rows: list[pd.DataFrame] = []
    for start, end, label in cfg.folds:
        train = data[data.date < start].copy()
        valid = data[(data.date >= start) & (data.date < end)].copy()
        if train.empty or valid.empty:
            continue
        model = fit_dual_model(train, cfg.catboost_params)
        home_mu, away_mu = model.predict(valid)
        out = valid[["date", "home_team", "away_team", "home_corners", "away_corners", "total_corners"]].copy()
        out["home_mu"] = home_mu
        out["away_mu"] = away_mu
        out["total_mu"] = home_mu + away_mu
        out["fold"] = label
        rows.append(out)
    if not rows:
        raise RuntimeError("No OOF folds produced predictions")
    return pd.concat(rows, ignore_index=True)


def estimate_dispersions(oof: pd.DataFrame) -> dict[str, float]:
    return {
        "home_alpha": estimate_nb_alpha(oof.home_corners.to_numpy(), oof.home_mu.to_numpy()),
        "away_alpha": estimate_nb_alpha(oof.away_corners.to_numpy(), oof.away_mu.to_numpy()),
        "total_alpha": estimate_nb_alpha(oof.total_corners.to_numpy(), oof.total_mu.to_numpy()),
    }


def distribution_comparison(oof: pd.DataFrame, alphas: dict[str, float], cfg: CornerV02Config) -> pd.DataFrame:
    rows: list[dict] = []
    for line in TOTAL_LINES:
        y = (oof.total_corners > line).astype(int).to_numpy()
        p_total_nb = np.array([
            nb_over_probability(mu, line, alphas["total_alpha"])
            for mu in oof.total_mu.to_numpy()
        ])
        p_conv = np.array([
            convolved_total_over_probability(h, a, line, alphas["home_alpha"], alphas["away_alpha"], cfg.max_count)
            for h, a in zip(oof.home_mu.to_numpy(), oof.away_mu.to_numpy())
        ])
        for method, p in (("total_nb", p_total_nb), ("independent_side_nb", p_conv)):
            rows.append({
                "line": line,
                "method": method,
                "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))),
                "brier": float(np.mean((p - y) ** 2)),
            })
    return pd.DataFrame(rows)


def choose_distribution(comparison: pd.DataFrame) -> str:
    scores = comparison.groupby("method").log_loss.mean().sort_values()
    return str(scores.index[0])


def _p_total_over(
    home_mu: float,
    away_mu: float,
    line: float,
    alphas: dict[str, float],
    method: str,
    max_count: int,
) -> float:
    if method == "independent_side_nb":
        return convolved_total_over_probability(
            home_mu, away_mu, line, alphas["home_alpha"], alphas["away_alpha"], max_count
        )
    return nb_over_probability(home_mu + away_mu, line, alphas["total_alpha"])


def reference_predictions(
    frame: pd.DataFrame,
    cfg: CornerV02Config,
    alphas: dict[str, float],
    distribution_method: str,
) -> tuple[pd.DataFrame, pd.DataFrame, object]:
    train = frame[(frame.source == "historical") & (frame.date >= cfg.train_start)].copy()
    ref = frame[frame.source == "holdout"].copy().reset_index(drop=True)
    model = fit_dual_model(train, cfg.catboost_params)
    home_mu, away_mu = model.predict(ref)
    ref["home_mu"] = home_mu
    ref["away_mu"] = away_mu
    ref["total_mu"] = home_mu + away_mu

    base_cols = ["date", "home_team", "away_team", "home_corners", "away_corners", "total_corners", "home_mu", "away_mu", "total_mu"]
    total_parts: list[pd.DataFrame] = []
    for line in TOTAL_LINES:
        token = str(line).replace(".", "_")
        over_col = f"over_{token}_corners_ft_closing_odds"
        under_col = f"under_{token}_corners_ft_closing_odds"
        z = ref[base_cols + [over_col, under_col]].copy()
        z = z.rename(columns={
            "home_corners": "actual_home_corners",
            "away_corners": "actual_away_corners",
            "total_corners": "actual_total",
            over_col: "over_odds",
            under_col: "under_odds",
        })
        z["line"] = line
        z["actual_over"] = (z.actual_total > line).astype(int)
        if distribution_method == "total_nb":
            z["p_model_over"] = nb_over_probability_array(z.total_mu.to_numpy(), line, alphas["total_alpha"])
        else:
            z["p_model_over"] = [
                convolved_total_over_probability(h, a, line, alphas["home_alpha"], alphas["away_alpha"], cfg.max_count)
                for h, a in zip(z.home_mu.to_numpy(), z.away_mu.to_numpy())
            ]
        z["p_model_under"] = 1.0 - z.p_model_over
        q_over = 1.0 / z.over_odds
        q_under = 1.0 / z.under_odds
        denom = q_over + q_under
        z["p_market_over"] = q_over / denom
        z["p_market_under"] = q_under / denom
        z["fair_over_odds"] = 1.0 / z.p_model_over.clip(lower=1e-12)
        z["fair_under_odds"] = 1.0 / z.p_model_under.clip(lower=1e-12)
        z["edge_over"] = z.p_model_over - z.p_market_over
        z["edge_under"] = z.p_model_under - z.p_market_under
        z["ev_over"] = z.p_model_over * z.over_odds - 1.0
        z["ev_under"] = z.p_model_under * z.under_odds - 1.0
        z["distribution_method"] = distribution_method
        total_parts.append(z)
    total = pd.concat(total_parts, ignore_index=True)

    team_parts: list[pd.DataFrame] = []
    for side in ("home", "away"):
        mu = ref[f"{side}_mu"].to_numpy()
        alpha = alphas[f"{side}_alpha"]
        actual_col = f"{side}_corners"
        team_name_col = f"{side}_team"
        for line in TEAM_LINES:
            z = ref[base_cols].copy()
            z = z.rename(columns={
                "home_corners": "actual_home_corners",
                "away_corners": "actual_away_corners",
                "total_corners": "actual_total",
            })
            z["side"] = side
            z["team"] = ref[team_name_col].to_numpy()
            z["line"] = line
            z["actual_team_corners"] = ref[actual_col].astype(int).to_numpy()
            z["actual_over"] = (z.actual_team_corners > line).astype(int)
            z["p_model_over"] = nb_over_probability_array(mu, line, alpha)
            z["p_model_under"] = 1.0 - z.p_model_over
            z["fair_over_odds"] = 1.0 / z.p_model_over.clip(lower=1e-12)
            z["fair_under_odds"] = 1.0 / z.p_model_under.clip(lower=1e-12)
            team_parts.append(z)
    team = pd.concat(team_parts, ignore_index=True)
    return total, team, model


def select_reference_bets(predictions: pd.DataFrame, cfg: CornerV02Config) -> pd.DataFrame:
    candidates: list[dict] = []
    for _, row in predictions.iterrows():
        options = []
        if row.edge_over >= cfg.edge_threshold and row.ev_over >= cfg.ev_threshold:
            options.append(("OVER", row.edge_over, row.ev_over, row.over_odds, row.actual_over, row.p_model_over))
        if row.edge_under >= cfg.edge_threshold and row.ev_under >= cfg.ev_threshold:
            options.append(("UNDER", row.edge_under, row.ev_under, row.under_odds, 1 - row.actual_over, row.p_model_under))
        if not options:
            continue
        side, edge, ev, odds, win, p_model = max(options, key=lambda x: x[2])
        item = row.to_dict()
        item.update({"side": side, "edge": edge, "ev": ev, "odds": odds, "win": int(win), "p_model": p_model})
        candidates.append(item)
    bets = pd.DataFrame(candidates)
    if bets.empty:
        return bets
    bets["match_key"] = bets.date.astype(str) + "|" + bets.home_team + "|" + bets.away_team
    bets = bets.sort_values("ev", ascending=False).groupby("match_key", as_index=False).head(cfg.max_bets_per_match)
    bets["profit_units"] = np.where(bets.win == 1, bets.odds - 1.0, -1.0)
    return bets.sort_values("date").reset_index(drop=True)


def bootstrap_roi_ci(profit: np.ndarray, seed: int = 42, draws: int = 10000) -> list[float]:
    arr = np.asarray(profit, dtype=float)
    if len(arr) == 0:
        return [float("nan")] * 3
    rng = np.random.default_rng(seed)
    sims = np.empty(draws)
    for i in range(draws):
        sims[i] = rng.choice(arr, size=len(arr), replace=True).mean()
    return [float(v) for v in np.quantile(sims, [0.025, 0.5, 0.975])]


def total_probability_report(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    cal_rows = []
    for line in TOTAL_LINES:
        z = predictions[predictions.line == line]
        y = z.actual_over.to_numpy()
        model = probability_metrics(y, z.p_model_over.to_numpy())
        market = probability_metrics(y, z.p_market_over.to_numpy())
        table = calibration_table(y, z.p_model_over.to_numpy(), bins=10)
        ece = expected_calibration_error(table)
        summary_rows.append({
            "line": line,
            "n": len(z),
            "model_log_loss": model["log_loss"],
            "market_log_loss": market["log_loss"],
            "model_brier": model["brier"],
            "market_brier": market["brier"],
            "model_ece_10": ece,
            "base_rate_over": model["base_rate"],
            "model_mean_over": model["mean_prediction"],
            "market_mean_over": market["mean_prediction"],
        })
        table.insert(0, "line", line)
        cal_rows.append(table)
    return pd.DataFrame(summary_rows), pd.concat(cal_rows, ignore_index=True)


def build_v02_summary(
    oof: pd.DataFrame,
    v01_baseline: dict | None,
    alphas: dict[str, float],
    dist_comparison: pd.DataFrame,
    chosen_distribution: str,
    ref_predictions: pd.DataFrame,
    bets: pd.DataFrame,
    cfg: CornerV02Config,
) -> dict:
    oof_home = count_metrics(oof.home_corners, oof.home_mu)
    oof_away = count_metrics(oof.away_corners, oof.away_mu)
    oof_total = count_metrics(oof.total_corners, oof.total_mu)
    prob_summary, _ = total_probability_report(ref_predictions)
    betting = {
        "n_bets": int(len(bets)),
        "wins": int(bets.win.sum()) if len(bets) else 0,
        "hit_rate": float(bets.win.mean()) if len(bets) else float("nan"),
        "profit_units": float(bets.profit_units.sum()) if len(bets) else 0.0,
        "roi": float(bets.profit_units.mean()) if len(bets) else float("nan"),
        "avg_odds": float(bets.odds.mean()) if len(bets) else float("nan"),
        "avg_edge": float(bets.edge.mean()) if len(bets) else float("nan"),
        "avg_model_ev": float(bets.ev.mean()) if len(bets) else float("nan"),
        "bootstrap_roi_95_ci": bootstrap_roi_ci(bets.profit_units.to_numpy(), cfg.random_seed) if len(bets) else [float("nan")]*3,
    }
    return {
        "model_version": cfg.version,
        "status": "research_reference_not_untouched_holdout",
        "methodology_note": (
            "2025/26 was already inspected during v0.1. It is a reference benchmark for v0.2, "
            "not an untouched proof of edge. Model/distribution choice for v0.2 is based on pre-2025 OOF only."
        ),
        "config": {
            "train_start": cfg.train_start,
            "reference_start": cfg.reference_start,
            "edge_threshold": cfg.edge_threshold,
            "ev_threshold": cfg.ev_threshold,
            "max_bets_per_match": cfg.max_bets_per_match,
            "shrinkage_games": cfg.shrinkage_games,
        },
        "oof": {
            "n": int(len(oof)),
            "dual_home": oof_home,
            "dual_away": oof_away,
            "dual_total": oof_total,
            "v01_total_baseline": v01_baseline,
            "alphas": alphas,
            "distribution_comparison": dist_comparison.to_dict(orient="records"),
            "chosen_total_distribution": chosen_distribution,
        },
        "reference_2025_26_probability_metrics": prob_summary.to_dict(orient="records"),
        "reference_2025_26_betting": betting,
    }


def run_v02_pipeline(
    data_path: str | Path,
    cfg: CornerV02Config,
    outdir: str | Path,
    modeldir: str | Path,
) -> dict:
    frame = pd.read_csv(data_path)
    frame = prepare_v02_frame(frame, cfg)

    oof = run_dual_oof(frame, cfg)
    alphas = estimate_dispersions(oof)
    dist_cmp = distribution_comparison(oof, alphas, cfg)
    chosen = choose_distribution(dist_cmp)

    # Do not retrain v0.1 inside the same CatBoost process: on some Python/OpenMP
    # combinations sequential model pools can deadlock. Read the frozen v0.1
    # benchmark if available; v0.2 itself remains fully reproducible.
    v01_baseline = None
    frozen_v01_summary = Path(outdir) / "backtest_summary.json"
    if frozen_v01_summary.exists():
        try:
            prior = json.loads(frozen_v01_summary.read_text(encoding="utf-8"))
            v01_baseline = prior.get("oof")
        except Exception:
            v01_baseline = None

    ref_total, ref_team, final_model = reference_predictions(frame, cfg, alphas, chosen)
    bets = select_reference_bets(ref_total, cfg)
    prob_summary, calibration = total_probability_report(ref_total)
    summary = build_v02_summary(oof, v01_baseline, alphas, dist_cmp, chosen, ref_total, bets, cfg)

    outdir = Path(outdir)
    modeldir = Path(modeldir)
    outdir.mkdir(parents=True, exist_ok=True)
    modeldir.mkdir(parents=True, exist_ok=True)

    oof.to_csv(outdir / "v02_oof_predictions.csv", index=False)
    dist_cmp.to_csv(outdir / "v02_distribution_comparison.csv", index=False)
    ref_total.to_csv(outdir / "v02_reference_total_predictions.csv", index=False)
    ref_team.to_csv(outdir / "v02_reference_team_probabilities.csv", index=False)
    bets.to_csv(outdir / "v02_reference_bets.csv", index=False)
    prob_summary.to_csv(outdir / "v02_reference_probability_metrics.csv", index=False)
    calibration.to_csv(outdir / "v02_reference_calibration.csv", index=False)
    (outdir / "v02_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    final_model.save(modeldir, metadata={
        "version": cfg.version,
        "alphas": alphas,
        "chosen_total_distribution": chosen,
        "config": asdict(cfg),
    })
    return summary
