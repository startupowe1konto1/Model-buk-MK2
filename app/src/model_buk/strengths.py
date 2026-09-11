from __future__ import annotations

import numpy as np
import pandas as pd

STRENGTH_FEATURES = [
    "home_corner_attack_strength",
    "away_corner_attack_strength",
    "home_corner_concede_strength",
    "away_corner_concede_strength",
    "home_corner_attack_strength_ewm",
    "away_corner_attack_strength_ewm",
    "home_corner_concede_strength_ewm",
    "away_corner_concede_strength_ewm",
    "home_mu_strength_baseline",
    "away_mu_strength_baseline",
]


def _shrunk_ratio(
    value: pd.Series,
    baseline: pd.Series,
    games_prior: pd.Series,
    shrinkage_games: float,
    min_rate: float,
    max_rate: float,
) -> pd.Series:
    ratio = value / baseline.replace(0, np.nan)
    weight = games_prior.astype(float) / (games_prior.astype(float) + shrinkage_games)
    out = 1.0 + weight * (ratio - 1.0)
    return out.clip(min_rate, max_rate)


def add_corner_strength_features(
    frame: pd.DataFrame,
    shrinkage_games: float = 8.0,
    min_rate: float = 0.35,
    max_rate: float = 2.50,
) -> pd.DataFrame:
    """Add explicit point-in-time attack and suppression features.

    Inputs are themselves shifted rolling/ewm statistics, so these derived
    features remain point-in-time safe. Venue features compare home attacking
    output with the league home baseline, and away output with the league away
    baseline. EWM variants provide a less sparse recency signal.
    """
    out = frame.copy()
    home_base = out["league_home_corners_r100"]
    away_base = out["league_away_corners_r100"]

    out["home_corner_attack_strength"] = _shrunk_ratio(
        out["home_corners_for_venue5"], home_base, out["home_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["away_corner_attack_strength"] = _shrunk_ratio(
        out["away_corners_for_venue5"], away_base, out["away_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["home_corner_concede_strength"] = _shrunk_ratio(
        out["home_corners_against_venue5"], away_base, out["home_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["away_corner_concede_strength"] = _shrunk_ratio(
        out["away_corners_against_venue5"], home_base, out["away_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )

    # EWM signals use overall recent team behavior, normalized to the relevant
    # league venue rate for the target side.
    out["home_corner_attack_strength_ewm"] = _shrunk_ratio(
        out["home_corners_for_ewm8"], home_base, out["home_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["away_corner_attack_strength_ewm"] = _shrunk_ratio(
        out["away_corners_for_ewm8"], away_base, out["away_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["home_corner_concede_strength_ewm"] = _shrunk_ratio(
        out["home_corners_against_ewm8"], away_base, out["home_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )
    out["away_corner_concede_strength_ewm"] = _shrunk_ratio(
        out["away_corners_against_ewm8"], home_base, out["away_games_prior"],
        shrinkage_games, min_rate, max_rate,
    )

    # Geometric combination keeps the multiplicative interpretation while being
    # less explosive than a full product of attack and opponent concession.
    out["home_mu_strength_baseline"] = home_base * np.sqrt(
        out["home_corner_attack_strength"] * out["away_corner_concede_strength"]
    )
    out["away_mu_strength_baseline"] = away_base * np.sqrt(
        out["away_corner_attack_strength"] * out["home_corner_concede_strength"]
    )
    return out
