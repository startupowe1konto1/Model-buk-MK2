from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, mean_squared_error

from .constants import (
    BET_EDGE_THRESHOLD,
    BET_EV_THRESHOLD,
    HOLDOUT_START,
    LINES,
    OOF_FOLDS,
    TRAIN_START,
)
from .model import estimate_nb_alpha, fit_total_model, over_probability, predict_mean


def run_oof(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame[(frame.source == "historical") & (frame.date >= TRAIN_START)].copy()
    rows: list[pd.DataFrame] = []
    for start, end, label in OOF_FOLDS:
        train = data[data.date < start]
        valid = data[(data.date >= start) & (data.date < end)].copy()
        model = fit_total_model(train)
        valid["mu_total"] = predict_mean(model, valid)
        valid["fold"] = label
        rows.append(valid[["date", "home_team", "away_team", "total_corners", "mu_total", "fold"]])
    return pd.concat(rows, ignore_index=True)


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    qo, qu = 1.0 / over_odds, 1.0 / under_odds
    denom = qo + qu
    return qo / denom, qu / denom


def holdout_predictions(frame: pd.DataFrame, alpha: float) -> pd.DataFrame:
    train = frame[(frame.source == "historical") & (frame.date >= TRAIN_START)].copy()
    hold = frame[frame.source == "holdout"].copy()
    model = fit_total_model(train)
    hold["mu_total"] = predict_mean(model, hold)

    records: list[dict] = []
    for _, row in hold.iterrows():
        for line in LINES:
            token = str(line).replace(".", "_")
            over_col = f"over_{token}_corners_ft_closing_odds"
            under_col = f"under_{token}_corners_ft_closing_odds"
            over_odds = float(row[over_col])
            under_odds = float(row[under_col])
            p_model_over = float(over_probability(row.mu_total, line, alpha))
            p_model_under = 1.0 - p_model_over
            p_market_over, p_market_under = devig_two_way(over_odds, under_odds)
            actual_over = int(row.total_corners > line)
            records.append(
                {
                    "date": row.date,
                    "home_team": row.home_team,
                    "away_team": row.away_team,
                    "line": line,
                    "actual_total": row.total_corners,
                    "mu_total": row.mu_total,
                    "p_model_over": p_model_over,
                    "p_market_over": p_market_over,
                    "over_odds": over_odds,
                    "under_odds": under_odds,
                    "actual_over": actual_over,
                    "edge_over": p_model_over - p_market_over,
                    "edge_under": p_model_under - p_market_under,
                    "ev_over": p_model_over * over_odds - 1.0,
                    "ev_under": p_model_under * under_odds - 1.0,
                }
            )
    return pd.DataFrame(records)


def select_bets(predictions: pd.DataFrame) -> pd.DataFrame:
    candidates: list[dict] = []
    for _, row in predictions.iterrows():
        options = []
        if row.edge_over >= BET_EDGE_THRESHOLD and row.ev_over >= BET_EV_THRESHOLD:
            options.append(("OVER", row.edge_over, row.ev_over, row.over_odds, row.actual_over))
        if row.edge_under >= BET_EDGE_THRESHOLD and row.ev_under >= BET_EV_THRESHOLD:
            options.append(("UNDER", row.edge_under, row.ev_under, row.under_odds, 1 - row.actual_over))
        if not options:
            continue
        side, edge, ev, odds, win = max(options, key=lambda x: x[2])
        item = row.to_dict()
        item.update({"side": side, "edge": edge, "ev": ev, "odds": odds, "win": int(win)})
        candidates.append(item)
    bets = pd.DataFrame(candidates)
    if bets.empty:
        return bets
    bets["match_key"] = (
        bets.date.astype(str) + "|" + bets.home_team.astype(str) + "|" + bets.away_team.astype(str)
    )
    bets = bets.sort_values("ev", ascending=False).groupby("match_key", as_index=False).head(1).copy()
    bets["profit_units"] = np.where(bets.win == 1, bets.odds - 1.0, -1.0)
    return bets.sort_values("date").reset_index(drop=True)


def probability_metrics(predictions: pd.DataFrame) -> dict:
    out: dict[str, dict] = {}
    for line in LINES:
        z = predictions[predictions.line == line]
        y = z.actual_over.astype(int)
        out[str(line)] = {
            "n": int(len(z)),
            "base_rate_over": float(y.mean()),
            "model_mean_over": float(z.p_model_over.mean()),
            "market_mean_over": float(z.p_market_over.mean()),
            "model_log_loss": float(log_loss(y, z.p_model_over)),
            "market_log_loss": float(log_loss(y, z.p_market_over)),
            "model_brier": float(brier_score_loss(y, z.p_model_over)),
            "market_brier": float(brier_score_loss(y, z.p_market_over)),
        }
    return out


def bootstrap_roi_ci(profit: np.ndarray, seed: int = 42, draws: int = 10000) -> list[float]:
    if len(profit) == 0:
        return [float("nan")] * 3
    rng = np.random.default_rng(seed)
    arr = np.asarray(profit, dtype=float)
    sims = np.empty(draws)
    for i in range(draws):
        sims[i] = rng.choice(arr, size=len(arr), replace=True).mean()
    return [float(x) for x in np.quantile(sims, [0.025, 0.5, 0.975])]


def build_summary(oof: pd.DataFrame, predictions: pd.DataFrame, bets: pd.DataFrame, alpha: float) -> dict:
    y = oof.total_corners.to_numpy()
    mu = oof.mu_total.to_numpy()
    summary = {
        "model_version": "corner-total-v0.1",
        "frozen_rules": {
            "train_start": TRAIN_START,
            "holdout_start": HOLDOUT_START,
            "edge_threshold": BET_EDGE_THRESHOLD,
            "ev_threshold": BET_EV_THRESHOLD,
            "one_bet_per_match": True,
            "market_features_in_model": False,
        },
        "oof": {
            "n": int(len(oof)),
            "mae_total": float(mean_absolute_error(y, mu)),
            "rmse_total": float(mean_squared_error(y, mu) ** 0.5),
            "nb_alpha": float(alpha),
        },
        "holdout_probability_metrics": probability_metrics(predictions),
        "holdout_betting": {
            "n_bets": int(len(bets)),
            "wins": int(bets.win.sum()) if len(bets) else 0,
            "hit_rate": float(bets.win.mean()) if len(bets) else float("nan"),
            "profit_units": float(bets.profit_units.sum()) if len(bets) else 0.0,
            "roi": float(bets.profit_units.mean()) if len(bets) else float("nan"),
            "avg_odds": float(bets.odds.mean()) if len(bets) else float("nan"),
            "avg_edge": float(bets.edge.mean()) if len(bets) else float("nan"),
            "avg_model_ev": float(bets.ev.mean()) if len(bets) else float("nan"),
            "bootstrap_roi_95_ci": bootstrap_roi_ci(bets.profit_units.to_numpy()) if len(bets) else [float("nan")]*3,
        },
    }
    return summary
