from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from model_buk.config import CornerV02Config
from model_buk.distributions import nb_over_probability
from model_buk.features import build_features
from model_buk.inference import LoadedCornerEngine, load_corner_engine
from model_buk.model import over_probability
from model_buk.models.corner_dual_v02 import V02_FEATURES
from model_buk.schema import validate_raw_history
from model_buk.strengths import add_corner_strength_features
from model_buk.v02_backtest import TEAM_LINES, TOTAL_LINES


@dataclass(frozen=True)
class LiveCornerSuite:
    total_model: CatBoostRegressor
    total_metadata: dict
    team_engine: LoadedCornerEngine
    registry: dict


def load_live_corner_suite(model_root: str | Path) -> LiveCornerSuite:
    root = Path(model_root)
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))

    total_dir = root / registry["total_corners"]["path"]
    total_meta = json.loads((total_dir / "metadata.json").read_text(encoding="utf-8"))
    total_model = CatBoostRegressor()
    total_model.load_model(str(total_dir / "corner_total_v0_1.cbm"))

    team_dir = root / registry["team_corners"]["path"]
    team_engine = load_corner_engine(team_dir)
    return LiveCornerSuite(total_model, total_meta, team_engine, registry)


def _future_row(history: pd.DataFrame, date: pd.Timestamp, home_team: str, away_team: str) -> pd.DataFrame:
    row = {column: np.nan for column in history.columns}
    row.update(
        {
            "date": date,
            "home_team": home_team,
            "away_team": away_team,
            "source": "future",
            "internal_match_id": int(pd.to_numeric(history["internal_match_id"]).max()) + 1,
        }
    )
    return pd.DataFrame([row], columns=history.columns)


def predict_with_live_suite(
    raw_history: pd.DataFrame,
    suite: LiveCornerSuite,
    cfg: CornerV02Config,
    *,
    date: str | pd.Timestamp,
    home_team: str,
    away_team: str,
) -> dict:
    validate_raw_history(raw_history)
    history = raw_history.copy()
    history["date"] = pd.to_datetime(history["date"], format="mixed")
    future_date = pd.Timestamp(date)
    if home_team == away_team:
        raise ValueError("home_team and away_team must differ")

    combined = pd.concat(
        [history, _future_row(history, future_date, home_team, away_team)],
        ignore_index=True,
        sort=False,
    )
    features = build_features(combined)
    base = features[features["source"] == "future"].iloc[-1]

    total_features = suite.total_metadata["cat_features"] + suite.total_metadata["core_features"]
    total_mu = float(np.clip(suite.total_model.predict(pd.DataFrame([base])[total_features])[0], 0.1, 30.0))
    total_alpha = float(suite.total_metadata["nb_alpha"])
    total_markets = []
    for line in TOTAL_LINES:
        p_over = float(over_probability(total_mu, line, total_alpha))
        total_markets.append(
            {
                "line": line,
                "p_over": p_over,
                "p_under": 1.0 - p_over,
                "fair_over": 1.0 / max(p_over, 1e-12),
                "fair_under": 1.0 / max(1.0 - p_over, 1e-12),
            }
        )

    with_strengths = add_corner_strength_features(
        features,
        shrinkage_games=cfg.shrinkage_games,
        min_rate=cfg.min_rate,
        max_rate=cfg.max_rate,
    )
    dual_row = with_strengths[with_strengths["source"] == "future"].iloc[-1]
    dual_frame = pd.DataFrame([dual_row])
    home_mu_arr, away_mu_arr = suite.team_engine.model.predict(dual_frame)
    home_mu, away_mu = float(home_mu_arr[0]), float(away_mu_arr[0])

    team_markets = []
    for side, team, mu, alpha in (
        ("home", home_team, home_mu, suite.team_engine.alphas["home_alpha"]),
        ("away", away_team, away_mu, suite.team_engine.alphas["away_alpha"]),
    ):
        for line in TEAM_LINES:
            p_over = nb_over_probability(mu, line, alpha)
            team_markets.append(
                {
                    "side": side,
                    "team": team,
                    "line": line,
                    "p_over": p_over,
                    "p_under": 1.0 - p_over,
                    "fair_over": 1.0 / max(p_over, 1e-12),
                    "fair_under": 1.0 / max(1.0 - p_over, 1e-12),
                }
            )

    team_history = pd.concat(
        [
            history.loc[history.home_team.eq(home_team), ["date"]].assign(side="home"),
            history.loc[history.away_team.eq(home_team), ["date"]].assign(side="away"),
        ],
        ignore_index=True,
    )
    opp_history = pd.concat(
        [
            history.loc[history.home_team.eq(away_team), ["date"]],
            history.loc[history.away_team.eq(away_team), ["date"]],
        ],
        ignore_index=True,
    )
    min_games = min(len(team_history), len(opp_history))
    completeness = float(pd.DataFrame([base])[total_features].notna().mean(axis=1).iloc[0])
    dq = round(100 * (0.75 * completeness + 0.25 * min(1.0, min_games / 20.0)), 1)

    return {
        "suite_version": "corner-suite-v0.3-router",
        "deployment_status": suite.registry.get("deployment_status", "research/paper only"),
        "fixture": {"date": str(future_date), "home_team": home_team, "away_team": away_team},
        "data_quality": {"score": dq, "feature_coverage": round(completeness, 4), "min_team_history_matches": int(min_games)},
        "total_corners": {
            "model": suite.total_metadata["model_version"],
            "expected_total": total_mu,
            "markets": total_markets,
        },
        "team_corners": {
            "model": suite.team_engine.metadata.get("version", "corner-dual-v0.2-livefit"),
            "expected_home": home_mu,
            "expected_away": away_mu,
            "markets": team_markets,
            "status": "experimental until executable historical team-corners odds are available",
        },
    }
