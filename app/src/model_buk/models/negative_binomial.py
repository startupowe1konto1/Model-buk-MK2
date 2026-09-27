"""Independent-marginal NB2 goal model with team attack and defence effects.

The NB2 parameterisation used here has ``Var(Y) = mu + alpha * mu**2``.
Keeping ``alpha`` on the log scale during fitting makes the shared dispersion
strictly positive while retaining a deterministic, conventional MLE fit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


_MIN_ALPHA = 1e-8
_MAX_ALPHA = 1e4


def _validate_rate_and_alpha(mean: float, alpha: float) -> None:
    if not math.isfinite(mean) or mean <= 0.0:
        raise ValueError("mean must be positive and finite")
    if not math.isfinite(alpha) or alpha <= 0.0:
        raise ValueError("alpha must be positive and finite")


def nb2_pmf(goals: int, mean: float, alpha: float) -> float:
    """Return NB2 probability mass for a non-negative integer goal count."""
    _validate_rate_and_alpha(mean, alpha)
    if not isinstance(goals, (int, np.integer)) or goals < 0:
        return 0.0
    size = 1.0 / alpha
    probability = size / (size + mean)
    log_pmf = (
        math.lgamma(goals + size)
        - math.lgamma(size)
        - math.lgamma(goals + 1.0)
        + size * math.log(probability)
        + goals * math.log1p(-probability)
    )
    return float(math.exp(log_pmf))


def score_matrix(
    home_mean: float,
    away_mean: float,
    alpha: float,
    *,
    max_goals: int = 10,
) -> list[list[float]]:
    """Return a normalized truncated score matrix from independent NB2 margins."""
    _validate_rate_and_alpha(home_mean, alpha)
    _validate_rate_and_alpha(away_mean, alpha)
    if not isinstance(max_goals, int) or max_goals < 1:
        raise ValueError("max_goals must be an integer of at least 1")
    home = np.array([nb2_pmf(goals, home_mean, alpha) for goals in range(max_goals + 1)])
    away = np.array([nb2_pmf(goals, away_mean, alpha) for goals in range(max_goals + 1)])
    matrix = np.outer(home, away)
    total = float(matrix.sum())
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError("score matrix has no probability mass")
    return (matrix / total).tolist()


@dataclass(frozen=True)
class NegativeBinomialModel:
    """NB2 goal model fit with centred attack and defence effects."""

    teams: tuple[str, ...]
    attack: dict[str, float]
    defence: dict[str, float]
    intercept: float
    home_advantage: float
    alpha: float
    l2_strength: float

    def __post_init__(self) -> None:
        if len(self.teams) < 2:
            raise ValueError("at least two teams are required")
        if set(self.attack) != set(self.teams) or set(self.defence) != set(self.teams):
            raise ValueError("attack and defence must contain exactly the fitted teams")
        if not math.isfinite(self.intercept) or not math.isfinite(self.home_advantage):
            raise ValueError("intercept and home_advantage must be finite")
        _validate_rate_and_alpha(1.0, self.alpha)
        if not math.isfinite(self.l2_strength) or self.l2_strength < 0.0:
            raise ValueError("l2_strength must be non-negative and finite")

    def expected_goals(
        self,
        home_team: str,
        away_team: str,
        *,
        unknown_strategy: str = "error",
    ) -> tuple[float, float]:
        """Return fitted home and away means on the goal scale."""
        if unknown_strategy not in {"error", "neutral"}:
            raise ValueError("unknown_strategy must be 'error' or 'neutral'")
        unknown = {home_team, away_team} - set(self.teams)
        if unknown and unknown_strategy == "error":
            raise ValueError(f"Unknown team(s): {', '.join(sorted(unknown))}")
        home_log_mean = (
            self.intercept
            + self.home_advantage
            + self.attack.get(home_team, 0.0)
            + self.defence.get(away_team, 0.0)
        )
        away_log_mean = (
            self.intercept
            + self.attack.get(away_team, 0.0)
            + self.defence.get(home_team, 0.0)
        )
        # Bounds avoid overflow for manually constructed models while preserving
        # all plausible football scoring rates.
        return float(math.exp(np.clip(home_log_mean, -12.0, 12.0))), float(
            math.exp(np.clip(away_log_mean, -12.0, 12.0))
        )

    def score_matrix(
        self,
        home_team: str,
        away_team: str,
        *,
        max_goals: int = 10,
        unknown_strategy: str = "error",
    ) -> list[list[float]]:
        """Return the independent-marginal NB2 score distribution."""
        home_mean, away_mean = self.expected_goals(
            home_team, away_team, unknown_strategy=unknown_strategy
        )
        return score_matrix(home_mean, away_mean, self.alpha, max_goals=max_goals)


def fit_negative_binomial(
    matches: pd.DataFrame,
    *,
    l2_strength: float = 0.0,
    max_iterations: int = 1_000,
) -> NegativeBinomialModel:
    """Fit shared-dispersion NB2 marginals to chronologically labelled matches.

    ``date`` is validated to enforce the same canonical match input contract as
    the other goal models, though this static model intentionally applies no
    recency weighting. Both team-effect vectors are zero-sum constrained.
    """
    from scipy.optimize import minimize
    from scipy.special import gammaln

    if not math.isfinite(l2_strength) or l2_strength < 0.0:
        raise ValueError("l2_strength must be non-negative and finite")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    frame = matches.loc[:, sorted(required)].copy()
    if frame[["home_team", "away_team"]].isna().any().any():
        raise ValueError("team names cannot be missing")
    if (frame["home_team"].astype(str).str.len() == 0).any() or (
        frame["away_team"].astype(str).str.len() == 0
    ).any():
        raise ValueError("team names cannot be empty")
    dates = pd.to_datetime(frame["date"], errors="coerce", format="mixed")
    home_goals = pd.to_numeric(frame["home_goals"], errors="coerce").to_numpy(dtype=float)
    away_goals = pd.to_numeric(frame["away_goals"], errors="coerce").to_numpy(dtype=float)
    if dates.isna().any() or not np.isfinite(home_goals).all() or not np.isfinite(away_goals).all():
        raise ValueError("matches contains invalid dates or goals")
    if np.any(home_goals < 0.0) or np.any(away_goals < 0.0):
        raise ValueError("goals must be non-negative")
    if not np.equal(home_goals, np.floor(home_goals)).all() or not np.equal(
        away_goals, np.floor(away_goals)
    ).all():
        raise ValueError("goals must be integers")

    home_teams = frame["home_team"].astype(str).to_numpy()
    away_teams = frame["away_team"].astype(str).to_numpy()
    if np.any(home_teams == away_teams):
        raise ValueError("home_team and away_team must differ")
    teams = tuple(sorted(set(home_teams) | set(away_teams)))
    if len(teams) < 2:
        raise ValueError("at least two teams are required")
    team_index = {team: index for index, team in enumerate(teams)}
    home_index = np.array([team_index[team] for team in home_teams], dtype=int)
    away_index = np.array([team_index[team] for team in away_teams], dtype=int)

    team_count = len(teams)
    all_goals = np.concatenate([home_goals, away_goals])
    initial_mean = max(float(all_goals.mean()), 0.1)
    # intercept, home advantage, log(alpha), free attacks, free defences
    initial = np.zeros(3 + 2 * (team_count - 1), dtype=float)
    initial[0] = math.log(initial_mean)
    initial[1] = math.log(max(float(home_goals.mean()), 0.1) / max(float(away_goals.mean()), 0.1))
    sample_mean = float(all_goals.mean())
    sample_variance = float(np.var(all_goals))
    initial[2] = math.log(np.clip((sample_variance - sample_mean) / max(sample_mean**2, 1e-8), _MIN_ALPHA, 1.0))

    def objective(parameters: np.ndarray) -> float:
        intercept, home_advantage, log_alpha = parameters[:3]
        attack = np.append(parameters[3 : 3 + team_count - 1], -parameters[3 : 3 + team_count - 1].sum())
        defence_free = parameters[3 + team_count - 1 :]
        defence = np.append(defence_free, -defence_free.sum())
        alpha = math.exp(log_alpha)
        home_mean = np.exp(np.clip(intercept + home_advantage + attack[home_index] + defence[away_index], -12.0, 12.0))
        away_mean = np.exp(np.clip(intercept + attack[away_index] + defence[home_index], -12.0, 12.0))
        size = 1.0 / alpha

        def log_likelihood(goals: np.ndarray, means: np.ndarray) -> np.ndarray:
            # Stable NB2 parameterisation near the Poisson boundary.  Computing
            # log(1 - size/(size + mean)) directly suffers catastrophic
            # cancellation when alpha is small.
            log_normalizer = np.log1p(alpha * means)
            log_probability = -log_normalizer
            log_one_minus_probability = (
                math.log(alpha) + np.log(means) - log_normalizer
            )
            return (
                gammaln(goals + size)
                - gammaln(size)
                - gammaln(goals + 1.0)
                + size * log_probability
                + goals * log_one_minus_probability
            )

        likelihood = log_likelihood(home_goals, home_mean) + log_likelihood(away_goals, away_mean)
        penalty = l2_strength * float(np.dot(attack, attack) + np.dot(defence, defence))
        value = -float(likelihood.sum()) + penalty
        return value if math.isfinite(value) else 1e100

    bounds = [(None, None), (None, None), (math.log(_MIN_ALPHA), math.log(_MAX_ALPHA))]
    bounds.extend([(None, None)] * (len(initial) - 3))
    result = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        bounds=bounds,
        options={
            "maxiter": max_iterations,
            "maxfun": max(15_000, max_iterations * max(20, len(initial) * 2)),
        },
    )
    if not result.success:
        # L-BFGS-B may report an abnormal line-search termination for nearly
        # Poisson samples with alpha on its lower bound.  Powell is slower but
        # deterministic and robust for this small parameter vector.
        fallback_start = result.x if np.isfinite(result.x).all() else initial
        result = minimize(
            objective,
            fallback_start,
            method="Powell",
            bounds=bounds,
            options={
                "maxiter": max_iterations,
                "xtol": 1e-8,
                "ftol": 1e-8,
            },
        )
    if not result.success:
        raise RuntimeError(f"Negative-binomial fit failed: {result.message}")

    fitted = result.x
    attack_values = np.append(fitted[3 : 3 + team_count - 1], -fitted[3 : 3 + team_count - 1].sum())
    defence_free = fitted[3 + team_count - 1 :]
    defence_values = np.append(defence_free, -defence_free.sum())
    return NegativeBinomialModel(
        teams=teams,
        attack=dict(zip(teams, attack_values, strict=True)),
        defence=dict(zip(teams, defence_values, strict=True)),
        intercept=float(fitted[0]),
        home_advantage=float(fitted[1]),
        alpha=float(math.exp(fitted[2])),
        l2_strength=float(l2_strength),
    )
