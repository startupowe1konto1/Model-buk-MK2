"""Independent-marginal NB2 goal model with team attack and defence effects.

The NB2 parameterisation used here has ``Var(Y) = mu + alpha * mu**2``.
The shared dispersion ``alpha`` is kept strictly positive by a lower bound
(``1e-6``, the Poisson limit to six digits) and fitted by a deterministic,
conventional MLE.  It is optimised on the ``alpha`` scale, not on ``log alpha``:
near the Poisson limit the log-scale gradient vanishes and a fit that starts
there cannot leave it.

The fit uses an analytic gradient (:func:`fit_negative_binomial_arrays`) and
optional per-match time-decay weights, so a league refit takes milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


_MIN_ALPHA = 1e-8
_MAX_ALPHA = 1e4
# Lower bound of the dispersion in the array fit.  Below ~1e-6 an NB2 margin is a
# Poisson margin to six digits; the bound keeps d loglik / d alpha (computed as
# a difference of O(1) terms divided by alpha) accurate.
_MIN_ALPHA_FIT = 1e-6


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


@dataclass(frozen=True)
class NegativeBinomialArrayFit:
    """Result of :func:`fit_negative_binomial_arrays` on integer team indices ``0 .. n_teams - 1``.

    ``attack`` and ``defence`` are zero-sum vectors on the log goal-mean scale.
    ``converged`` is False when no optimiser reached a (near) zero gradient.
    """

    intercept: float
    home_advantage: float
    alpha: float
    attack: np.ndarray
    defence: np.ndarray
    converged: bool
    n_iterations: int
    n_evaluations: int
    message: str

    def parameters(self) -> tuple[float, float, float, np.ndarray, np.ndarray]:
        """The fitted values in the shape :func:`fit_negative_binomial_arrays` accepts as ``start``."""
        return self.intercept, self.home_advantage, self.alpha, self.attack, self.defence


# Scale-free tolerances: the objective is the weighted mean negative log-likelihood.
_ARRAY_FTOL = 1e-13
_ARRAY_GTOL = 1e-8
_ACCEPTED_GRADIENT = 1e-6


def _pack_start(
    start: tuple[float, float, float, np.ndarray, np.ndarray] | None,
    n_teams: int,
    initial: tuple[float, float, float],
) -> np.ndarray:
    """Parameter vector ``[intercept, home advantage, alpha, free attacks, free defences]``."""
    theta = np.zeros(3 + 2 * (n_teams - 1), dtype=float)
    if start is None:
        theta[:3] = initial
        return theta
    intercept, home_advantage, alpha, attack, defence = start
    attack = np.asarray(attack, dtype=float)
    defence = np.asarray(defence, dtype=float)
    if attack.shape != (n_teams,) or defence.shape != (n_teams,):
        raise ValueError("start attack/defence must have one value per team")
    theta[0] = intercept + attack.mean() + defence.mean()
    theta[1] = home_advantage
    theta[2] = float(np.clip(alpha, _MIN_ALPHA_FIT, _MAX_ALPHA))
    theta[3 : 2 + n_teams] = (attack - attack.mean())[:-1]
    theta[2 + n_teams :] = (defence - defence.mean())[:-1]
    return theta


def fit_negative_binomial_arrays(
    home_index: np.ndarray,
    away_index: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
    *,
    l2_strength: float = 0.0,
    start: tuple[float, float, float, np.ndarray, np.ndarray] | None = None,
    max_iterations: int = 1_000,
    ftol: float = _ARRAY_FTOL,
    gtol: float = _ARRAY_GTOL,
) -> NegativeBinomialArrayFit:
    """Weighted NB2 maximum likelihood on integer team indices, with analytic gradients.

    Same model and optimum as :func:`fit_negative_binomial` (shared dispersion,
    zero-sum attack and defence) with per-match ``weights`` (time decay).
    Goals must be non-negative integers.  ``start`` is an optional
    ``(intercept, home_advantage, alpha, attack, defence)`` tuple, for example
    the previous matchday's solution; it only chooses where the optimiser
    begins.  ``gamma(y + r) / gamma(r)`` is evaluated as a product of ``r + k``
    (exact for integer ``y``), which stays accurate when ``alpha`` is tiny and
    ``r = 1 / alpha`` is ~1e6.  The dispersion is optimised on the ``alpha``
    scale (bounds 1e-6 to 1e4): on a log scale the gradient vanishes as
    ``alpha -> 0`` and a fit that starts near the Poisson limit cannot leave it.
    """
    from scipy.optimize import minimize

    n = int(n_teams)
    if n < 2:
        raise ValueError("at least two teams are required")
    hi = np.asarray(home_index, dtype=np.intp)
    ai = np.asarray(away_index, dtype=np.intp)
    x = np.asarray(home_goals, dtype=float)
    y = np.asarray(away_goals, dtype=float)
    w = np.asarray(weights, dtype=float)
    total = float(w.sum())
    if not (len(hi) == len(ai) == len(x) == len(y) == len(w)) or len(hi) == 0:
        raise ValueError("index, goal and weight arrays must be non-empty and of equal length")
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError("weights must have a positive finite sum")
    if np.any(x != np.floor(x)) or np.any(y != np.floor(y)) or np.any(x < 0.0) or np.any(y < 0.0):
        raise ValueError("goals must be non-negative integers")
    wn = w / total
    l2n = float(l2_strength) / total
    xi = x.astype(np.intp)
    yi = y.astype(np.intp)
    n_steps = int(max(xi.max(), yi.max()))
    steps = np.arange(n_steps, dtype=float)
    n_free = n - 1

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        intercept, home_advantage, alpha = theta[0], theta[1], theta[2]
        attack = np.empty(n)
        attack[:n_free] = theta[3 : 3 + n_free]
        attack[n_free] = -attack[:n_free].sum()
        defence = np.empty(n)
        defence[:n_free] = theta[3 + n_free :]
        defence[n_free] = -defence[:n_free].sum()
        log_alpha = math.log(alpha)
        size = 1.0 / alpha
        log_home = np.clip(intercept + home_advantage + attack[hi] + defence[ai], -12.0, 12.0)
        log_away = np.clip(intercept + attack[ai] + defence[hi], -12.0, 12.0)
        mean_home = np.exp(log_home)
        mean_away = np.exp(log_away)
        log_growth_home = np.log1p(alpha * mean_home)
        log_growth_away = np.log1p(alpha * mean_away)
        # sum_{k<y} log(r + k) and sum_{k<y} r / (r + k): lookups by goal count.
        table_log = np.concatenate(([0.0], np.cumsum(np.log(size + steps))))
        table_ratio = np.concatenate(([0.0], np.cumsum(1.0 / (1.0 + alpha * steps))))
        # Terms free of the parameters (log y!) are dropped: the optimum is unchanged.
        ll_home = table_log[xi] - size * log_growth_home + x * (log_home + log_alpha - log_growth_home)
        ll_away = table_log[yi] - size * log_growth_away + y * (log_away + log_alpha - log_growth_away)
        log_likelihood = float(wn @ (ll_home + ll_away))
        if not math.isfinite(log_likelihood):
            return 1e100, np.zeros_like(theta)
        # d loglik / d log(mean) per match, and d loglik / d alpha = (d loglik / d log alpha) / alpha
        # where d loglik / d log alpha = -sum_{k<y} r/(r+k) + r log(1 + alpha mu) + (y - mu)/(1 + alpha mu)
        # (a difference of O(1) terms that is O(alpha) near the Poisson limit, hence the bound on alpha).
        d_home = (x - mean_home) / (1.0 + alpha * mean_home)
        d_away = (y - mean_away) / (1.0 + alpha * mean_away)
        d_alpha = (
            -table_ratio[xi]
            + size * log_growth_home
            + d_home
            - table_ratio[yi]
            + size * log_growth_away
            + d_away
        ) / alpha
        wh = wn * d_home
        wa = wn * d_away
        grad_attack = np.bincount(hi, wh, n) + np.bincount(ai, wa, n)
        grad_defence = np.bincount(ai, wh, n) + np.bincount(hi, wa, n)
        value = -log_likelihood
        gradient = np.empty_like(theta)
        gradient[0] = -(wh.sum() + wa.sum())
        gradient[1] = -wh.sum()
        gradient[2] = -float(wn @ d_alpha)
        if l2n > 0.0:
            value += l2n * (float(attack @ attack) + float(defence @ defence))
            grad_attack = grad_attack - 2.0 * l2n * attack
            grad_defence = grad_defence - 2.0 * l2n * defence
        gradient[3 : 3 + n_free] = -(grad_attack[:n_free] - grad_attack[n_free])
        gradient[3 + n_free :] = -(grad_defence[:n_free] - grad_defence[n_free])
        return value, gradient

    all_goals = np.concatenate([x, y])
    all_weights = np.concatenate([w, w])
    sample_mean = float(np.average(all_goals, weights=all_weights))
    sample_variance = float(np.average((all_goals - sample_mean) ** 2, weights=all_weights))
    home_mean = float(np.average(x, weights=w))
    away_mean = float(np.average(y, weights=w))
    initial = (
        math.log(max(sample_mean, 0.1)),
        math.log(max(home_mean, 0.1) / max(away_mean, 0.1)),
        float(np.clip((sample_variance - sample_mean) / max(sample_mean**2, 1e-8), 1e-3, 1.0)),
    )
    bounds = [(None, None), (None, None), (_MIN_ALPHA_FIT, _MAX_ALPHA)]
    bounds.extend([(None, None)] * (2 * n_free))
    options = {
        "maxiter": max_iterations,
        "maxfun": max(15_000, max_iterations * 20),
        "ftol": ftol,
        "gtol": gtol,
    }

    def projected_gradient(theta: np.ndarray, gradient: np.ndarray) -> float:
        free = np.ones_like(theta, dtype=bool)
        # A parameter on its bound with the gradient pushing outwards is stationary.
        free[2] = not (
            (theta[2] <= _MIN_ALPHA_FIT * (1.0 + 1e-9) and gradient[2] > 0.0)
            or (theta[2] >= _MAX_ALPHA * (1.0 - 1e-9) and gradient[2] < 0.0)
        )
        return float(np.max(np.abs(gradient[free]))) if free.any() else 0.0

    result = minimize(
        objective, _pack_start(start, n, initial), jac=True, method="L-BFGS-B", bounds=bounds, options=options
    )
    stationary = projected_gradient(result.x, result.jac) <= _ACCEPTED_GRADIENT
    if not (result.success or stationary) and start is not None:
        cold = minimize(
            objective, _pack_start(None, n, initial), jac=True, method="L-BFGS-B", bounds=bounds, options=options
        )
        if cold.success or cold.fun < result.fun:
            result = cold
            stationary = projected_gradient(result.x, result.jac) <= _ACCEPTED_GRADIENT
    if not (result.success or stationary):
        # L-BFGS-B may report an abnormal line-search termination for nearly
        # Poisson samples with alpha on its lower bound.  Powell is slower but
        # deterministic and robust for this small parameter vector.
        fallback_start = result.x if np.isfinite(result.x).all() else _pack_start(None, n, initial)
        powell = minimize(
            lambda theta: objective(theta)[0],
            fallback_start,
            method="Powell",
            bounds=bounds,
            options={"maxiter": max_iterations, "xtol": 1e-8, "ftol": 1e-8},
        )
        if powell.success or powell.fun < result.fun:
            result = powell
            stationary = projected_gradient(result.x, objective(result.x)[1]) <= _ACCEPTED_GRADIENT
    theta = result.x
    attack = np.empty(n)
    attack[:n_free] = theta[3 : 3 + n_free]
    attack[n_free] = -attack[:n_free].sum()
    defence = np.empty(n)
    defence[:n_free] = theta[3 + n_free :]
    defence[n_free] = -defence[:n_free].sum()
    return NegativeBinomialArrayFit(
        intercept=float(theta[0]),
        home_advantage=float(theta[1]),
        alpha=float(theta[2]),
        attack=attack,
        defence=defence,
        converged=bool(result.success or stationary) and bool(np.isfinite(theta).all()),
        n_iterations=int(getattr(result, "nit", 0)),
        n_evaluations=int(getattr(result, "nfev", 0)),
        message=str(result.message),
    )


def fit_negative_binomial(
    matches: pd.DataFrame,
    *,
    l2_strength: float = 0.0,
    max_iterations: int = 1_000,
    half_life_days: float | None = None,
    reference_date: object | None = None,
) -> NegativeBinomialModel:
    """Fit shared-dispersion NB2 marginals to chronologically labelled matches.

    ``date`` is validated to enforce the same canonical match input contract as
    the other goal models.  By default no recency weighting is applied; with
    ``half_life_days`` every match is weighted ``exp(-ln 2 * age / half_life)``
    (age in days before ``reference_date``, default the latest match), exactly
    as in :func:`model_buk.models.dixon_coles.time_decay_weights`.  Both
    team-effect vectors are zero-sum constrained.
    """
    from model_buk.models.dixon_coles import time_decay_weights

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

    if half_life_days is None:
        weights = np.ones(len(frame), dtype=float)
    else:
        weights = time_decay_weights(dates, reference_date=reference_date, half_life_days=half_life_days)
    fitted = fit_negative_binomial_arrays(
        home_index,
        away_index,
        home_goals,
        away_goals,
        weights,
        len(teams),
        l2_strength=l2_strength,
        max_iterations=max_iterations,
        ftol=1e-10,
        gtol=1e-6,
    )
    if not fitted.converged:
        raise RuntimeError(f"Negative-binomial fit failed: {fitted.message}")
    return NegativeBinomialModel(
        teams=teams,
        attack=dict(zip(teams, fitted.attack, strict=True)),
        defence=dict(zip(teams, fitted.defence, strict=True)),
        intercept=fitted.intercept,
        home_advantage=fitted.home_advantage,
        alpha=fitted.alpha,
        l2_strength=float(l2_strength),
    )
