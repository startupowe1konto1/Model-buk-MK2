from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DixonColesModel:
    """Fitted Dixon-Coles parameters on the log goal-rate scale."""

    teams: tuple[str, ...]
    attack: dict[str, float]
    defence: dict[str, float]
    intercept: float
    home_advantage: float
    rho: float
    reference_date: pd.Timestamp
    xi: float
    l2_strength: float
    xg_weight: float

    def expected_goals(
        self,
        home_team: str,
        away_team: str,
        *,
        unknown_strategy: str = "error",
    ) -> tuple[float, float]:
        """Return expected goals, optionally shrinking unseen teams to league average."""
        unknown = {home_team, away_team} - set(self.teams)
        if unknown and unknown_strategy == "error":
            raise ValueError(f"Unknown team(s): {', '.join(sorted(unknown))}")
        if unknown_strategy not in {"error", "neutral"}:
            raise ValueError("unknown_strategy must be 'error' or 'neutral'")
        home_attack = self.attack.get(home_team, 0.0)
        home_defence = self.defence.get(home_team, 0.0)
        away_attack = self.attack.get(away_team, 0.0)
        away_defence = self.defence.get(away_team, 0.0)
        home_rate = math.exp(
            self.intercept
            + self.home_advantage
            + home_attack
            + away_defence
        )
        away_rate = math.exp(
            self.intercept + away_attack + home_defence
        )
        return home_rate, away_rate


def _decay_rate(*, half_life_days: float | None, xi: float | None) -> float:
    if half_life_days is not None and xi is not None:
        raise ValueError("Specify either half_life_days or xi, not both")
    if half_life_days is not None:
        if not math.isfinite(half_life_days) or half_life_days <= 0.0:
            raise ValueError("half_life_days must be positive and finite")
        return math.log(2.0) / half_life_days
    if xi is not None:
        if not math.isfinite(xi) or xi < 0.0:
            raise ValueError("xi must be non-negative and finite")
        return xi
    return 0.0


def time_decay_weights(
    match_dates: Sequence[date | datetime | str] | pd.Series,
    *,
    reference_date: date | datetime | str | None = None,
    half_life_days: float | None = None,
    xi: float | None = None,
) -> np.ndarray:
    """Return Dixon-Coles exponential weights ``exp(-xi * age_days)``.

    With neither ``half_life_days`` nor ``xi`` set, every match receives weight
    one. This preserves the unweighted behaviour for existing callers.
    """
    rate = _decay_rate(half_life_days=half_life_days, xi=xi)
    dates = pd.DatetimeIndex(pd.to_datetime(match_dates, errors="coerce", format="mixed"))
    if len(dates) == 0:
        return np.array([], dtype=float)
    if dates.isna().any():
        raise ValueError("match_dates contains invalid dates")
    reference = pd.Timestamp(reference_date) if reference_date is not None else dates.max()
    if pd.isna(reference):
        raise ValueError("reference_date is invalid")
    if dates.tz is not None:
        dates = dates.tz_convert("UTC").tz_localize(None)
    if reference.tzinfo is not None:
        reference = reference.tz_convert("UTC").tz_localize(None)
    age_days = (reference - dates).total_seconds().to_numpy(dtype=float) / 86_400.0
    if np.any(age_days < -1e-12):
        raise ValueError("reference_date cannot be earlier than a match date")
    return np.exp(-rate * np.maximum(age_days, 0.0))


def fit_dixon_coles(
    matches: pd.DataFrame,
    *,
    half_life_days: float | None = None,
    xi: float | None = None,
    reference_date: date | datetime | str | None = None,
    l2_strength: float = 0.0,
    xg_weight: float = 0.0,
    max_iterations: int = 1_000,
) -> DixonColesModel:
    """Fit team attack/defence strengths with optional time decay.

    Expected columns are ``date``, ``home_team``, ``away_team``,
    ``home_goals`` and ``away_goals``. Attack strengths are constrained to sum
    to zero, which makes the parametrisation identifiable.
    """
    from scipy.optimize import minimize
    from scipy.special import gammaln

    if not math.isfinite(l2_strength) or l2_strength < 0.0:
        raise ValueError("l2_strength must be non-negative and finite")
    if not math.isfinite(xg_weight) or xg_weight < 0.0:
        raise ValueError("xg_weight must be non-negative and finite")
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    if xg_weight > 0.0:
        required.update({"home_xg", "away_xg"})
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    frame = matches.loc[:, sorted(required)].copy()
    if frame[["home_team", "away_team"]].isna().any().any():
        raise ValueError("team names cannot be missing")
    dates = pd.to_datetime(frame["date"], errors="coerce", format="mixed")
    home_goals = pd.to_numeric(frame["home_goals"], errors="coerce").to_numpy(dtype=float)
    away_goals = pd.to_numeric(frame["away_goals"], errors="coerce").to_numpy(dtype=float)
    if dates.isna().any() or not np.isfinite(home_goals).all() or not np.isfinite(away_goals).all():
        raise ValueError("matches contains invalid dates or goals")
    if np.any(home_goals < 0) or np.any(away_goals < 0):
        raise ValueError("goals must be non-negative")
    if not np.equal(home_goals, np.floor(home_goals)).all() or not np.equal(
        away_goals, np.floor(away_goals)
    ).all():
        raise ValueError("goals must be integers")
    if xg_weight > 0.0:
        home_xg = pd.to_numeric(frame["home_xg"], errors="coerce").to_numpy(dtype=float)
        away_xg = pd.to_numeric(frame["away_xg"], errors="coerce").to_numpy(dtype=float)
        if np.any(home_xg[np.isfinite(home_xg)] < 0.0) or np.any(
            away_xg[np.isfinite(away_xg)] < 0.0
        ):
            raise ValueError("xG must be non-negative where available")
    else:
        home_xg = np.full(len(frame), np.nan)
        away_xg = np.full(len(frame), np.nan)

    teams = tuple(sorted(set(frame["home_team"]) | set(frame["away_team"])))
    if len(teams) < 2:
        raise ValueError("at least two teams are required")
    team_index = {team: index for index, team in enumerate(teams)}
    try:
        home_index = np.array([team_index[team] for team in frame["home_team"]])
        away_index = np.array([team_index[team] for team in frame["away_team"]])
    except KeyError as exc:
        raise ValueError("team names cannot be missing") from exc
    if np.any(home_index == away_index):
        raise ValueError("home_team and away_team must differ")

    weights = time_decay_weights(
        dates,
        reference_date=reference_date,
        half_life_days=half_life_days,
        xi=xi,
    )
    decay_rate = _decay_rate(half_life_days=half_life_days, xi=xi)
    reference = pd.Timestamp(reference_date) if reference_date is not None else dates.max()
    team_count = len(teams)
    initial_mean = max(float(np.mean(np.concatenate([home_goals, away_goals]))), 0.1)
    # intercept, home advantage, rho, n-1 attacks, n-1 defences
    initial = np.zeros(3 + 2 * (team_count - 1), dtype=float)
    initial[0] = math.log(initial_mean)

    def objective(parameters: np.ndarray) -> float:
        intercept, home_advantage, rho = parameters[:3]
        attack_free = parameters[3 : 3 + team_count - 1]
        attack = np.append(attack_free, -attack_free.sum())
        defence_free = parameters[3 + team_count - 1 :]
        defence = np.append(defence_free, -defence_free.sum())
        home_rate = np.exp(
            np.clip(intercept + home_advantage + attack[home_index] + defence[away_index], -8, 8)
        )
        away_rate = np.exp(
            np.clip(intercept + attack[away_index] + defence[home_index], -8, 8)
        )
        tau = np.ones(len(frame), dtype=float)
        tau[(home_goals == 0) & (away_goals == 0)] = (
            1.0 - home_rate[(home_goals == 0) & (away_goals == 0)]
            * away_rate[(home_goals == 0) & (away_goals == 0)]
            * rho
        )
        tau[(home_goals == 0) & (away_goals == 1)] = (
            1.0 + home_rate[(home_goals == 0) & (away_goals == 1)] * rho
        )
        tau[(home_goals == 1) & (away_goals == 0)] = (
            1.0 + away_rate[(home_goals == 1) & (away_goals == 0)] * rho
        )
        tau[(home_goals == 1) & (away_goals == 1)] = 1.0 - rho
        if np.any(tau <= 0.0) or not np.isfinite(tau).all():
            return 1e100
        log_likelihood = (
            home_goals * np.log(home_rate)
            - home_rate
            - gammaln(home_goals + 1.0)
            + away_goals * np.log(away_rate)
            - away_rate
            - gammaln(away_goals + 1.0)
            + np.log(tau)
        )
        if xg_weight > 0.0:
            home_mask = np.isfinite(home_xg)
            away_mask = np.isfinite(away_xg)
            xg_log_likelihood = np.zeros(len(frame), dtype=float)
            xg_log_likelihood[home_mask] += (
                home_xg[home_mask] * np.log(home_rate[home_mask])
                - home_rate[home_mask]
                - gammaln(home_xg[home_mask] + 1.0)
            )
            xg_log_likelihood[away_mask] += (
                away_xg[away_mask] * np.log(away_rate[away_mask])
                - away_rate[away_mask]
                - gammaln(away_xg[away_mask] + 1.0)
            )
            log_likelihood = log_likelihood + xg_weight * xg_log_likelihood
        penalty = l2_strength * float(np.dot(attack, attack) + np.dot(defence, defence))
        return -float(np.dot(weights, log_likelihood)) + penalty

    result = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        bounds=[(None, None), (None, None), (-0.3, 0.3)]
        + [(None, None)] * (len(initial) - 3),
        # SciPy's default ``maxfun`` (15k) can stop a perfectly healthy fit
        # before ``maxiter`` when numerical gradients are evaluated for dozens
        # of team parameters. Scale it explicitly with the parameter count.
        options={
            "maxiter": max_iterations,
            "maxfun": max(15_000, max_iterations * max(20, len(initial) * 2)),
        },
    )
    if not result.success:
        raise RuntimeError(f"Dixon-Coles fit failed: {result.message}")

    fitted = result.x
    attack_free = fitted[3 : 3 + team_count - 1]
    attack_values = np.append(attack_free, -attack_free.sum())
    defence_free = fitted[3 + team_count - 1 :]
    defence_values = np.append(defence_free, -defence_free.sum())
    return DixonColesModel(
        teams=teams,
        attack=dict(zip(teams, attack_values, strict=True)),
        defence=dict(zip(teams, defence_values, strict=True)),
        intercept=float(fitted[0]),
        home_advantage=float(fitted[1]),
        rho=float(fitted[2]),
        reference_date=pd.Timestamp(reference),
        xi=decay_rate,
        l2_strength=float(l2_strength),
        xg_weight=float(xg_weight),
    )


def poisson_pmf(goals: int, rate: float) -> float:
    if goals < 0:
        return 0.0
    if rate <= 0.0:
        raise ValueError("Poisson rate must be positive")
    return math.exp(-rate) * (rate**goals) / math.factorial(goals)


def low_score_correction(
    home_goals: int,
    away_goals: int,
    home_rate: float,
    away_rate: float,
    rho: float,
) -> float:
    """Dixon-Coles correction for the four low-scoring outcomes."""
    if (home_goals, away_goals) == (0, 0):
        tau = 1.0 - home_rate * away_rate * rho
    elif (home_goals, away_goals) == (0, 1):
        tau = 1.0 + home_rate * rho
    elif (home_goals, away_goals) == (1, 0):
        tau = 1.0 + away_rate * rho
    elif (home_goals, away_goals) == (1, 1):
        tau = 1.0 - rho
    else:
        tau = 1.0
    if tau <= 0.0:
        raise ValueError("rho produces a non-positive Dixon-Coles correction")
    return tau


def score_matrix(
    home_rate: float,
    away_rate: float,
    *,
    rho: float = 0.0,
    max_goals: int = 10,
) -> list[list[float]]:
    """Return a normalized truncated joint score distribution."""
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    matrix = []
    for home_goals in range(max_goals + 1):
        row = []
        for away_goals in range(max_goals + 1):
            probability = (
                poisson_pmf(home_goals, home_rate)
                * poisson_pmf(away_goals, away_rate)
                * low_score_correction(
                    home_goals,
                    away_goals,
                    home_rate,
                    away_rate,
                    rho,
                )
            )
            row.append(probability)
        matrix.append(row)
    total = sum(sum(row) for row in matrix)
    if total <= 0.0:
        raise ValueError("score matrix has no probability mass")
    return [[value / total for value in row] for row in matrix]


def market_probabilities(matrix: list[list[float]], *, total_line: float = 2.5) -> dict[str, float]:
    home = draw = away = btts_yes = over = 0.0
    for home_goals, row in enumerate(matrix):
        for away_goals, probability in enumerate(row):
            if home_goals > away_goals:
                home += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away += probability
            if home_goals > 0 and away_goals > 0:
                btts_yes += probability
            if home_goals + away_goals > total_line:
                over += probability
    return {
        "result.home": home,
        "result.draw": draw,
        "result.away": away,
        "btts.yes": btts_yes,
        "btts.no": 1.0 - btts_yes,
        f"goals.total.over.{total_line}": over,
        f"goals.total.under.{total_line}": 1.0 - over,
    }
