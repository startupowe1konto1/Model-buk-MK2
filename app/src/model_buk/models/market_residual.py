"""Market-anchored multinomial residual model and point-in-time form features."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


FORM_METRICS = (
    "goals_for",
    "goals_against",
    "shots_for",
    "shots_against",
    "sot_for",
    "sot_against",
    "corners_for",
    "corners_against",
    "points",
)
FORM_WINDOWS = (5, 10)
RESIDUAL_FEATURE_COLUMNS = tuple(
    f"{metric}_r{window}_diff" for metric in FORM_METRICS for window in FORM_WINDOWS
) + ("rest_days_diff", "opening_entropy", "opening_favourite_probability")


def build_market_residual_features(matches: pd.DataFrame) -> pd.DataFrame:
    """Create shifted rolling team-form differences for each scheduled match."""
    required = {
        "source_row_id", "league", "kickoff", "home_team", "away_team",
        "home_goals", "away_goals", "home_shots", "away_shots",
        "home_sot", "away_sot", "home_corners", "away_corners",
    }
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches are missing columns: {', '.join(missing)}")
    data = matches.copy()
    data["kickoff"] = pd.to_datetime(data["kickoff"], errors="coerce", utc=True, format="mixed")
    if data["kickoff"].isna().any():
        raise ValueError("matches contain invalid kickoff values")
    data = data.sort_values(["kickoff", "source_row_id"], kind="stable").reset_index(drop=True)

    common = {
        "source_row_id": data["source_row_id"].to_numpy(),
        "league": data["league"].astype(str).to_numpy(),
        "kickoff": data["kickoff"].to_numpy(),
    }
    home = pd.DataFrame(
        {
            **common,
            "side": "home",
            "team": data["home_team"].astype(str).to_numpy(),
            "goals_for": data["home_goals"].to_numpy(),
            "goals_against": data["away_goals"].to_numpy(),
            "shots_for": data["home_shots"].to_numpy(),
            "shots_against": data["away_shots"].to_numpy(),
            "sot_for": data["home_sot"].to_numpy(),
            "sot_against": data["away_sot"].to_numpy(),
            "corners_for": data["home_corners"].to_numpy(),
            "corners_against": data["away_corners"].to_numpy(),
        }
    )
    away = pd.DataFrame(
        {
            **common,
            "side": "away",
            "team": data["away_team"].astype(str).to_numpy(),
            "goals_for": data["away_goals"].to_numpy(),
            "goals_against": data["home_goals"].to_numpy(),
            "shots_for": data["away_shots"].to_numpy(),
            "shots_against": data["home_shots"].to_numpy(),
            "sot_for": data["away_sot"].to_numpy(),
            "sot_against": data["home_sot"].to_numpy(),
            "corners_for": data["away_corners"].to_numpy(),
            "corners_against": data["home_corners"].to_numpy(),
        }
    )
    long = pd.concat([home, away], ignore_index=True).sort_values(
        ["league", "team", "kickoff", "source_row_id"], kind="stable"
    )
    if long.duplicated(["league", "team", "kickoff"], keep=False).any():
        raise ValueError("a team has multiple matches at the same kickoff timestamp")
    long["points"] = np.where(
        long["goals_for"] > long["goals_against"],
        3.0,
        np.where(long["goals_for"] == long["goals_against"], 1.0, 0.0),
    )
    groups = long.groupby(["league", "team"], sort=False)
    for metric in FORM_METRICS:
        numeric = pd.to_numeric(long[metric], errors="coerce")
        for window in FORM_WINDOWS:
            long[f"{metric}_r{window}"] = numeric.groupby(
                [long["league"], long["team"]], sort=False
            ).transform(lambda values, w=window: values.shift(1).rolling(w, min_periods=3).mean())
    long["previous_kickoff"] = groups["kickoff"].shift(1)
    long["rest_days"] = (
        long["kickoff"] - long["previous_kickoff"]
    ).dt.total_seconds().div(86400.0).clip(lower=0.0, upper=60.0)

    rolling = [f"{metric}_r{window}" for metric in FORM_METRICS for window in FORM_WINDOWS]
    home_features = long.loc[long["side"].eq("home"), ["source_row_id", *rolling, "rest_days"]]
    away_features = long.loc[long["side"].eq("away"), ["source_row_id", *rolling, "rest_days"]]
    home_features = home_features.rename(columns={name: f"home_{name}" for name in [*rolling, "rest_days"]})
    away_features = away_features.rename(columns={name: f"away_{name}" for name in [*rolling, "rest_days"]})
    result = data.merge(home_features, on="source_row_id", how="left", validate="one_to_one")
    result = result.merge(away_features, on="source_row_id", how="left", validate="one_to_one")
    for name in rolling:
        result[f"{name}_diff"] = result[f"home_{name}"] - result[f"away_{name}"]
    result["rest_days_diff"] = result["home_rest_days"] - result["away_rest_days"]
    return result


@dataclass(frozen=True)
class MarketResidualConfig:
    l2_strength: float = 0.1
    learning_rate: float = 0.02
    epochs: int = 40
    batch_size: int = 1024
    seed: int = 42
    max_logit_correction: float = 2.0
    include_league_effects: bool = False
    league_l2_multiplier: float = 10.0

    def __post_init__(self) -> None:
        if self.l2_strength < 0 or self.learning_rate <= 0:
            raise ValueError("l2_strength must be non-negative and learning_rate positive")
        if self.epochs < 1 or self.batch_size < 1:
            raise ValueError("epochs and batch_size must be positive")
        if self.max_logit_correction <= 0:
            raise ValueError("max_logit_correction must be positive")
        if not isinstance(self.include_league_effects, bool):
            raise ValueError("include_league_effects must be a boolean")
        if self.league_l2_multiplier <= 0:
            raise ValueError("league_l2_multiplier must be positive")


@dataclass(frozen=True)
class MarketResidualModel:
    config: MarketResidualConfig
    feature_columns: tuple[str, ...]
    medians: np.ndarray
    means: np.ndarray
    scales: np.ndarray
    coefficients: np.ndarray

    def predict_proba(self, features: pd.DataFrame, market_probabilities: np.ndarray) -> np.ndarray:
        matrix = _standardise(features, self.feature_columns, self.medians, self.means, self.scales)
        design = np.column_stack([np.ones(len(matrix)), matrix])
        correction = np.clip(
            design @ self.coefficients,
            -self.config.max_logit_correction,
            self.config.max_logit_correction,
        )
        market = _probability_matrix(market_probabilities)
        logits = np.column_stack(
            [
                np.log(market[:, 0] / market[:, 2]) + correction[:, 0],
                np.log(market[:, 1] / market[:, 2]) + correction[:, 1],
                np.zeros(len(market)),
            ]
        )
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        return probabilities / probabilities.sum(axis=1, keepdims=True)

    def audit_parameters(self) -> dict[str, object]:
        return asdict(self.config)


def _probability_matrix(value: np.ndarray) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != 3:
        raise ValueError("market_probabilities must have shape (n, 3)")
    if not np.isfinite(matrix).all() or (matrix <= 0).any():
        raise ValueError("market probabilities must be finite and positive")
    matrix = matrix / matrix.sum(axis=1, keepdims=True)
    return np.clip(matrix, 1e-12, 1.0)


def _standardise(
    features: pd.DataFrame,
    columns: tuple[str, ...],
    medians: np.ndarray,
    means: np.ndarray,
    scales: np.ndarray,
) -> np.ndarray:
    missing = sorted(set(columns) - set(features.columns))
    if missing:
        raise ValueError(f"features are missing columns: {', '.join(missing)}")
    matrix = features.loc[:, list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    matrix = np.where(np.isfinite(matrix), matrix, medians)
    return (matrix - means) / scales


def fit_market_residual(
    features: pd.DataFrame,
    market_probabilities: np.ndarray,
    actual: np.ndarray,
    *,
    config: MarketResidualConfig | None = None,
    feature_columns: tuple[str, ...] = RESIDUAL_FEATURE_COLUMNS,
) -> MarketResidualModel:
    """Fit bounded log-odds corrections while keeping market odds as offset."""
    cfg = config or MarketResidualConfig()
    labels = np.asarray(actual, dtype=int)
    if len(features) != len(labels) or len(features) == 0:
        raise ValueError("features and actual must have matching non-empty rows")
    if not np.isin(labels, [0, 1, 2]).all():
        raise ValueError("actual must contain class indices 0, 1 or 2")
    market = _probability_matrix(market_probabilities)
    raw = features.loc[:, list(feature_columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    finite = np.where(np.isfinite(raw), raw, np.nan)
    medians = np.nanmedian(finite, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    imputed = np.where(np.isfinite(raw), raw, medians)
    means = imputed.mean(axis=0)
    scales = imputed.std(axis=0)
    scales = np.where(scales > 1e-8, scales, 1.0)
    matrix = (imputed - means) / scales
    design = np.column_stack([np.ones(len(matrix)), matrix])
    coefficients = np.zeros((design.shape[1], 2), dtype=float)
    first_moment = np.zeros_like(coefficients)
    second_moment = np.zeros_like(coefficients)
    rng = np.random.default_rng(cfg.seed)
    step = 0
    base_logits = np.column_stack(
        [np.log(market[:, 0] / market[:, 2]), np.log(market[:, 1] / market[:, 2])]
    )
    targets = np.eye(3)[labels]
    penalty_multipliers = np.ones(len(feature_columns), dtype=float)
    for index, name in enumerate(feature_columns):
        if name.startswith("league__"):
            penalty_multipliers[index] = cfg.league_l2_multiplier
    for _epoch in range(cfg.epochs):
        order = rng.permutation(len(design))
        for start in range(0, len(design), cfg.batch_size):
            indices = order[start : start + cfg.batch_size]
            x_batch = design[indices]
            correction = np.clip(
                x_batch @ coefficients,
                -cfg.max_logit_correction,
                cfg.max_logit_correction,
            )
            logits = np.column_stack(
                [base_logits[indices] + correction, np.zeros(len(indices))]
            )
            logits -= logits.max(axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            gradient = x_batch.T @ (probabilities[:, :2] - targets[indices, :2]) / len(indices)
            gradient[1:] += (
                cfg.l2_strength * penalty_multipliers[:, None] * coefficients[1:]
            )
            step += 1
            first_moment = 0.9 * first_moment + 0.1 * gradient
            second_moment = 0.999 * second_moment + 0.001 * gradient**2
            corrected_first = first_moment / (1.0 - 0.9**step)
            corrected_second = second_moment / (1.0 - 0.999**step)
            coefficients -= cfg.learning_rate * corrected_first / (np.sqrt(corrected_second) + 1e-8)
    return MarketResidualModel(
        config=cfg,
        feature_columns=feature_columns,
        medians=medians,
        means=means,
        scales=scales,
        coefficients=coefficients,
    )


__all__ = [
    "MarketResidualConfig",
    "MarketResidualModel",
    "RESIDUAL_FEATURE_COLUMNS",
    "build_market_residual_features",
    "fit_market_residual",
]
