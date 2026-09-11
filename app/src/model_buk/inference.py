from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from model_buk.config import CornerV02Config
from model_buk.decision import price_two_way_market
from model_buk.distributions import (
    convolved_total_over_probability,
    nb_over_probability,
)
from model_buk.features import build_features
from model_buk.models.corner_dual_v02 import DualCornerModel
from model_buk.schema import validate_raw_history
from model_buk.strengths import add_corner_strength_features
from model_buk.v02_backtest import TEAM_LINES, TOTAL_LINES


@dataclass(frozen=True)
class LoadedCornerEngine:
    model: DualCornerModel
    metadata: dict

    @property
    def alphas(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.metadata["alphas"].items()}

    @property
    def distribution_method(self) -> str:
        return str(self.metadata["chosen_total_distribution"])

    @property
    def max_count(self) -> int:
        return int(self.metadata.get("config", {}).get("max_count", 40))


def load_corner_engine(modeldir: str | Path) -> LoadedCornerEngine:
    modeldir = Path(modeldir)
    meta_path = modeldir / "corner_dual_v0_2_metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing model metadata: {meta_path}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    home = CatBoostRegressor()
    away = CatBoostRegressor()
    home.load_model(str(modeldir / "corner_home_v0_2.cbm"))
    away.load_model(str(modeldir / "corner_away_v0_2.cbm"))
    return LoadedCornerEngine(DualCornerModel(home_model=home, away_model=away), metadata)


def _future_row(history: pd.DataFrame, date: str | pd.Timestamp, home_team: str, away_team: str) -> pd.DataFrame:
    row = {column: np.nan for column in history.columns}
    row.update(
        {
            "date": pd.Timestamp(date),
            "home_team": home_team,
            "away_team": away_team,
            "source": "future",
            "internal_match_id": int(pd.to_numeric(history["internal_match_id"]).max()) + 1,
        }
    )
    return pd.DataFrame([row], columns=history.columns)


def _data_quality(feature_row: pd.Series) -> dict[str, float | int]:
    numeric_feature_names = [
        c for c in feature_row.index
        if c.startswith("home_") or c.startswith("away_") or c.startswith("league_")
    ]
    ignored = {"home_team", "away_team", "home_corners", "away_corners"}
    numeric_feature_names = [c for c in numeric_feature_names if c not in ignored]
    non_null = int(feature_row[numeric_feature_names].notna().sum())
    coverage = non_null / max(len(numeric_feature_names), 1)
    home_games = float(feature_row.get("home_games_prior", 0) or 0)
    away_games = float(feature_row.get("away_games_prior", 0) or 0)
    depth = min(1.0, min(home_games, away_games) / 20.0)
    score = 100.0 * (0.70 * coverage + 0.30 * depth)
    return {
        "score": round(score, 1),
        "feature_coverage": round(coverage, 4),
        "home_games_prior": int(home_games),
        "away_games_prior": int(away_games),
    }


def _total_over_probability(engine: LoadedCornerEngine, home_mu: float, away_mu: float, line: float) -> float:
    a = engine.alphas
    if engine.distribution_method == "independent_side_nb":
        return convolved_total_over_probability(
            home_mu,
            away_mu,
            line,
            a["home_alpha"],
            a["away_alpha"],
            engine.max_count,
        )
    return nb_over_probability(home_mu + away_mu, line, a["total_alpha"])


def predict_fixture(
    raw_history: pd.DataFrame,
    engine: LoadedCornerEngine,
    cfg: CornerV02Config,
    *,
    date: str | pd.Timestamp,
    home_team: str,
    away_team: str,
) -> dict:
    """Generate a point-in-time corner forecast for one future fixture.

    The future fixture is appended with unknown outcomes. `build_features` shifts
    every rolling statistic by one match, so the prediction only uses information
    available before the fixture.
    """
    validate_raw_history(raw_history)
    history = raw_history.copy()
    history["date"] = pd.to_datetime(history["date"], format="mixed")
    future_date = pd.Timestamp(date)
    if future_date <= history["date"].min():
        raise ValueError("prediction date must be after the beginning of the available history")
    if home_team == away_team:
        raise ValueError("home_team and away_team must differ")

    augmented = pd.concat(
        [history, _future_row(history, future_date, home_team, away_team)],
        ignore_index=True,
        sort=False,
    )
    features = build_features(augmented)
    features = add_corner_strength_features(
        features,
        shrinkage_games=cfg.shrinkage_games,
        min_rate=cfg.min_rate,
        max_rate=cfg.max_rate,
    )
    row = features[features["source"] == "future"].iloc[-1]
    frame = pd.DataFrame([row])
    home_mu, away_mu = engine.model.predict(frame)
    home_mu_f, away_mu_f = float(home_mu[0]), float(away_mu[0])

    total_markets = []
    for line in TOTAL_LINES:
        p_over = _total_over_probability(engine, home_mu_f, away_mu_f, line)
        total_markets.append(
            {
                "line": line,
                "p_over": p_over,
                "p_under": 1.0 - p_over,
                "fair_over": 1.0 / max(p_over, 1e-12),
                "fair_under": 1.0 / max(1.0 - p_over, 1e-12),
            }
        )

    team_markets = []
    for side, team, mu, alpha in (
        ("home", home_team, home_mu_f, engine.alphas["home_alpha"]),
        ("away", away_team, away_mu_f, engine.alphas["away_alpha"]),
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

    return {
        "model_version": engine.metadata.get("version", "corner-dual-v0.2"),
        "prediction_timestamp_policy": "caller must persist timestamp before kickoff",
        "fixture": {
            "date": str(future_date),
            "home_team": home_team,
            "away_team": away_team,
        },
        "expected_corners": {
            "home": home_mu_f,
            "away": away_mu_f,
            "total": home_mu_f + away_mu_f,
        },
        "data_quality": _data_quality(row),
        "total_markets": total_markets,
        "team_markets": team_markets,
        "distribution_method": engine.distribution_method,
    }


def attach_total_market_price(
    prediction: dict,
    cfg: CornerV02Config,
    *,
    line: float,
    over_odds: float,
    under_odds: float,
) -> dict:
    """Attach market math after the PURE model prediction is already produced."""
    matches = [m for m in prediction["total_markets"] if float(m["line"]) == float(line)]
    if not matches:
        raise ValueError(f"Unsupported total-corners line: {line}")
    p_over = float(matches[0]["p_over"])
    over, under = price_two_way_market(
        p_over=p_over,
        over_odds=over_odds,
        under_odds=under_odds,
        edge_threshold=cfg.edge_threshold,
        ev_threshold=cfg.ev_threshold,
    )
    priced = dict(prediction)
    priced["market_check"] = {
        "market": "total_corners",
        "line": float(line),
        "over_odds": float(over_odds),
        "under_odds": float(under_odds),
        "over": over.__dict__,
        "under": under.__dict__,
        "best_decision": max((over, under), key=lambda d: d.ev).__dict__,
    }
    return priced
