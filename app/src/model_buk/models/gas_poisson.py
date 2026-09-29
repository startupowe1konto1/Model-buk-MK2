from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GasPoissonConfig:
    attack_step: float = 0.04
    defence_step: float = 0.04
    persistence: float = 0.98
    min_rate: float = 0.05
    max_rate: float = 8.0
    # The historical recursion re-centres the states on every match.  The
    # maximum-likelihood recursion (``fit_gas_poisson_mle``) does not: the
    # intercept carries the league level and untouched teams keep their state.
    center_states: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.attack_step <= 1.0:
            raise ValueError("attack_step must be between 0 and 1")
        if not 0.0 <= self.defence_step <= 1.0:
            raise ValueError("defence_step must be between 0 and 1")
        if not 0.0 <= self.persistence <= 1.0:
            raise ValueError("persistence must be between 0 and 1")
        if self.min_rate <= 0.0 or self.max_rate <= self.min_rate:
            raise ValueError("rates must satisfy 0 < min_rate < max_rate")


@dataclass
class GasPoissonModel:
    """Observation-driven attack/defence state updated by Poisson scores."""

    intercept: float
    home_advantage: float
    attack: dict[str, float]
    defence: dict[str, float]
    config: GasPoissonConfig
    # State of a team the model has never seen (a promoted team): the prior
    # estimated by ``fit_gas_poisson_mle``; zero (league average) by default.
    new_team_attack: float = 0.0
    new_team_defence: float = 0.0

    @property
    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.attack) | set(self.defence)))

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_log_rate = (
            self.intercept
            + self.home_advantage
            + self.attack.get(home_team, self.new_team_attack)
            + self.defence.get(away_team, self.new_team_defence)
        )
        away_log_rate = (
            self.intercept
            + self.attack.get(away_team, self.new_team_attack)
            + self.defence.get(home_team, self.new_team_defence)
        )
        home_rate = float(np.clip(math.exp(home_log_rate), self.config.min_rate, self.config.max_rate))
        away_rate = float(np.clip(math.exp(away_log_rate), self.config.min_rate, self.config.max_rate))
        return home_rate, away_rate

    def observe_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> tuple[float, float]:
        """Update latent states after returning the pre-match expected rates."""
        if home_goals < 0 or away_goals < 0:
            raise ValueError("goals must be non-negative")
        home_rate, away_rate = self.expected_goals(home_team, away_team)
        self.attack.setdefault(home_team, self.new_team_attack)
        self.attack.setdefault(away_team, self.new_team_attack)
        self.defence.setdefault(home_team, self.new_team_defence)
        self.defence.setdefault(away_team, self.new_team_defence)
        home_score = float(home_goals) - home_rate
        away_score = float(away_goals) - away_rate
        persistence = self.config.persistence
        self.attack[home_team] = (
            persistence * self.attack[home_team] + self.config.attack_step * home_score
        )
        self.defence[away_team] = (
            persistence * self.defence[away_team] + self.config.defence_step * home_score
        )
        self.attack[away_team] = (
            persistence * self.attack[away_team] + self.config.attack_step * away_score
        )
        self.defence[home_team] = (
            persistence * self.defence[home_team] + self.config.defence_step * away_score
        )
        if self.config.center_states:
            self._center_states()
        return home_rate, away_rate

    def _center_states(self) -> None:
        if self.attack:
            mean_attack = float(np.mean(list(self.attack.values())))
            for team in self.attack:
                self.attack[team] -= mean_attack
        if self.defence:
            mean_defence = float(np.mean(list(self.defence.values())))
            for team in self.defence:
                self.defence[team] -= mean_defence


def fit_gas_poisson(
    matches: pd.DataFrame,
    *,
    config: GasPoissonConfig | None = None,
) -> GasPoissonModel:
    """Initialize league rates and replay matches chronologically into GAS state."""
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    data = matches.loc[:, sorted(required)].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce", format="mixed")
    data["home_goals"] = pd.to_numeric(data["home_goals"], errors="coerce")
    data["away_goals"] = pd.to_numeric(data["away_goals"], errors="coerce")
    if data[["date", "home_team", "away_team", "home_goals", "away_goals"]].isna().any().any():
        raise ValueError("matches contains missing or invalid required values")
    if (data[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("goals must be non-negative")
    away_mean = max(float(data["away_goals"].mean()), 0.05)
    home_mean = max(float(data["home_goals"].mean()), 0.05)
    teams = sorted(set(data["home_team"]) | set(data["away_team"]))
    model = GasPoissonModel(
        intercept=math.log(away_mean),
        home_advantage=math.log(home_mean / away_mean),
        attack={str(team): 0.0 for team in teams},
        defence={str(team): 0.0 for team in teams},
        config=config or GasPoissonConfig(),
    )
    for row in data.sort_values(["date", "home_team", "away_team"]).itertuples(index=False):
        model.observe_match(
            str(row.home_team),
            str(row.away_team),
            int(row.home_goals),
            int(row.away_goals),
        )
    return model


# ---------------------------------------------------------------------------
# Maximum-likelihood estimation of the static parameters
# ---------------------------------------------------------------------------
#
# Recursion (identical to ``GasPoissonModel.observe_match`` without the
# re-centring), for home team h and away team a:
#
#   log lambda_H = intercept + home_advantage + attack[h] + defence[a]
#   log lambda_A = intercept + attack[a] + defence[h]
#   attack[h] <- B * attack[h] + A_att * (y_H - lambda_H)   defence[a] <- B * defence[a] + A_def * (y_H - lambda_H)
#   attack[a] <- B * attack[a] + A_att * (y_A - lambda_A)   defence[h] <- B * defence[h] + A_def * (y_A - lambda_A)
#
# Only the two teams that played are updated (persistence is "per own match").
# The static parameters (intercept, home advantage, A_att, A_def, B and the
# prior state of a team that enters the league after its first observed season,
# a promoted team) maximise the *filtered* Poisson log-likelihood: every match
# is scored with the rates produced before its own result was seen.  The
# gradient is exact (a reverse-mode sweep over the recursion), so an L-BFGS-B
# fit needs about twenty passes over a league's history from a cold start (about
# 0.1 s for 3,000 matches); ``start`` can warm-start it from an earlier fit on a
# subset of the same matches.
#
# A weak Gaussian penalty (a proper prior: its weight does not grow with the
# sample) keeps the two step sizes, the forgetting rate and the promoted-team
# prior finite on short histories; see ``GasMleSettings``.

PARAMETER_NAMES = (
    "intercept",
    "home_advantage",
    "attack_step",
    "defence_step",
    "persistence",
    "entrant_attack",
    "entrant_defence",
)
# The optimiser works on x = theta * _SCALE with theta = (c, h, A_att, A_def, 1 - B, pi_att, pi_def):
# the scales bring the step sizes and the forgetting rate to O(1).
_SCALE = np.array([1.0, 1.0, 10.0, 10.0, 50.0, 1.0, 1.0])


@dataclass(frozen=True)
class GasMleSettings:
    """Penalty, bounds and optimiser settings of the maximum-likelihood fit.

    The penalty is a Gaussian prior on the two step sizes, the forgetting rate
    ``1 - persistence`` and the promoted-team prior (attack and defence state of
    a team that enters the league after its first season).  Its centres and
    widths come from *unpenalised* fits of 44 leagues on matches before
    2022-08-01, i.e. before the first test season of the comparison protocol:
    median step 0.02 and forgetting 0.007 (persistence 0.993), spread across
    leagues about 0.005-0.01 and 0.007; the widths used here are two to four
    times wider than that spread, so on a league with a few hundred matches the
    prior only stops a step size from running to the bound, and with thousands
    it is negligible (unpenalised and penalised forecasts differ by less than
    0.0001 in log-loss).  Bounds keep the recursion stable: steps at most 0.15.
    """

    step_prior_mean: float = 0.02
    step_prior_sd: float = 0.02
    forget_prior_mean: float = 0.007  # prior on 1 - persistence
    forget_prior_sd: float = 0.02
    entrant_prior_sd: float = 0.25
    max_step: float = 0.15
    max_forget: float = 0.4
    max_log_rate: float = 4.0  # exp(4) = 54 goals: only a guard against overflow
    max_iterations: int = 200
    ftol: float = 1e-11
    gtol: float = 1e-7

    def bounds(self) -> list[tuple[float, float]]:
        theta_bounds = [
            (-2.0, 2.0),
            (-1.0, 1.0),
            (0.0, self.max_step),
            (0.0, self.max_step),
            (0.0, self.max_forget),
            (-1.0, 1.0),
            (-1.0, 1.0),
        ]
        return [(lo * scale, hi * scale) for (lo, hi), scale in zip(theta_bounds, _SCALE)]


@dataclass
class GasFit:
    """Result of one maximum-likelihood fit: parameters plus the filtered end state."""

    intercept: float
    home_advantage: float
    attack_step: float
    defence_step: float
    persistence: float
    entrant_attack: float
    entrant_defence: float
    attack: np.ndarray  # state per team index after the last training match
    defence: np.ndarray
    log_likelihood: float  # Poisson log-likelihood of the training matches (without the constant log y!)
    penalty: float
    n_matches: int
    n_evaluations: int
    converged: bool
    message: str = ""
    team_keys: tuple = field(default_factory=tuple)

    def theta(self) -> np.ndarray:
        """Parameter vector in the order of :data:`PARAMETER_NAMES` (natural units, 1 - B instead of B)."""
        return np.array(
            [
                self.intercept,
                self.home_advantage,
                self.attack_step,
                self.defence_step,
                1.0 - self.persistence,
                self.entrant_attack,
                self.entrant_defence,
            ]
        )

    def parameters(self) -> dict[str, float]:
        return {
            "intercept": self.intercept,
            "home_advantage": self.home_advantage,
            "attack_step": self.attack_step,
            "defence_step": self.defence_step,
            "persistence": self.persistence,
            "entrant_attack": self.entrant_attack,
            "entrant_defence": self.entrant_defence,
        }

    def config(self) -> GasPoissonConfig:
        return GasPoissonConfig(
            attack_step=min(max(self.attack_step, 0.0), 1.0),
            defence_step=min(max(self.defence_step, 0.0), 1.0),
            persistence=min(max(self.persistence, 0.0), 1.0),
            center_states=False,
        )

    def to_model(self) -> GasPoissonModel:
        """The fitted filter as an ordinary :class:`GasPoissonModel` (states at the cutoff)."""
        keys = self.team_keys or tuple(range(len(self.attack)))
        return GasPoissonModel(
            intercept=self.intercept,
            home_advantage=self.home_advantage,
            attack={str(key): float(value) for key, value in zip(keys, self.attack)},
            defence={str(key): float(value) for key, value in zip(keys, self.defence)},
            config=self.config(),
            new_team_attack=self.entrant_attack,
            new_team_defence=self.entrant_defence,
        )


@dataclass
class _GasData:
    """Training matches as plain Python lists: the sequential loop is fastest on lists."""

    home: list[int]
    away: list[int]
    home_goals: list[float]
    away_goals: list[float]
    n_teams: int
    entrants: list[int]

    @property
    def n_matches(self) -> int:
        return len(self.home)


def _run_filter(theta: Sequence[float], data: _GasData, max_log_rate: float, tape: list | None = None):
    """One forward pass; returns (log-likelihood without the log y! constant, attack, defence)."""
    c, home_adv, step_att, step_def, forget, pi_att, pi_def = theta
    persistence = 1.0 - forget
    attack = [0.0] * data.n_teams
    defence = [0.0] * data.n_teams
    for team in data.entrants:
        attack[team] = pi_att
        defence[team] = pi_def
    exp = math.exp
    log_likelihood = 0.0
    home_intercept = c + home_adv
    cap = max_log_rate
    if tape is None:
        for h, a, goals_h, goals_a in zip(data.home, data.away, data.home_goals, data.away_goals):
            att_h = attack[h]
            def_a = defence[a]
            att_a = attack[a]
            def_h = defence[h]
            log_h = home_intercept + att_h + def_a
            log_a = c + att_a + def_h
            if log_h > cap:
                log_h = cap
            if log_a > cap:
                log_a = cap
            rate_h = exp(log_h)
            rate_a = exp(log_a)
            log_likelihood += goals_h * log_h - rate_h + goals_a * log_a - rate_a
            score_h = goals_h - rate_h
            score_a = goals_a - rate_a
            attack[h] = persistence * att_h + step_att * score_h
            defence[a] = persistence * def_a + step_def * score_h
            attack[a] = persistence * att_a + step_att * score_a
            defence[h] = persistence * def_h + step_def * score_a
    else:
        push = tape.append
        for h, a, goals_h, goals_a in zip(data.home, data.away, data.home_goals, data.away_goals):
            att_h = attack[h]
            def_a = defence[a]
            att_a = attack[a]
            def_h = defence[h]
            log_h = home_intercept + att_h + def_a
            log_a = c + att_a + def_h
            if log_h > cap:
                log_h = cap
            if log_a > cap:
                log_a = cap
            rate_h = exp(log_h)
            rate_a = exp(log_a)
            log_likelihood += goals_h * log_h - rate_h + goals_a * log_a - rate_a
            score_h = goals_h - rate_h
            score_a = goals_a - rate_a
            attack[h] = persistence * att_h + step_att * score_h
            defence[a] = persistence * def_a + step_def * score_h
            attack[a] = persistence * att_a + step_att * score_a
            defence[h] = persistence * def_h + step_def * score_a
            push((h, a, score_h, score_a, rate_h, rate_a, att_h, def_a, att_a, def_h))
    return log_likelihood, attack, defence


def _log_likelihood_and_gradient(theta: Sequence[float], data: _GasData, max_log_rate: float):
    """Log-likelihood and its exact gradient wrt theta (natural units) by a reverse sweep."""
    c, home_adv, step_att, step_def, forget, pi_att, pi_def = theta
    persistence = 1.0 - forget
    tape: list = []
    log_likelihood, _, _ = _run_filter(theta, data, max_log_rate, tape)
    adj_att = [0.0] * data.n_teams  # d(later log-likelihood) / d(state after the current match)
    adj_def = [0.0] * data.n_teams
    grad_persistence = grad_att = grad_def = grad_c = grad_home = 0.0
    for h, a, score_h, score_a, rate_h, rate_a, att_h, def_a, att_a, def_h in reversed(tape):
        u_att_h = adj_att[h]
        u_def_a = adj_def[a]
        u_att_a = adj_att[a]
        u_def_h = adj_def[h]
        grad_persistence += u_att_h * att_h + u_def_a * def_a + u_att_a * att_a + u_def_h * def_h
        grad_att += u_att_h * score_h + u_att_a * score_a
        grad_def += u_def_a * score_h + u_def_h * score_a
        r_h = score_h - rate_h * (step_att * u_att_h + step_def * u_def_a)
        r_a = score_a - rate_a * (step_att * u_att_a + step_def * u_def_h)
        grad_c += r_h + r_a
        grad_home += r_h
        adj_att[h] = persistence * u_att_h + r_h
        adj_def[a] = persistence * u_def_a + r_h
        adj_att[a] = persistence * u_att_a + r_a
        adj_def[h] = persistence * u_def_h + r_a
    grad_pi_att = grad_pi_def = 0.0
    for team in data.entrants:
        grad_pi_att += adj_att[team]
        grad_pi_def += adj_def[team]
    gradient = np.array([grad_c, grad_home, grad_att, grad_def, -grad_persistence, grad_pi_att, grad_pi_def])
    return log_likelihood, gradient


def _penalty(theta: np.ndarray, settings: GasMleSettings) -> tuple[float, np.ndarray]:
    """Gaussian prior penalty (a positive number to subtract from the log-likelihood) and its gradient."""
    gradient = np.zeros(7)
    value = 0.0
    for index in (2, 3):
        z = (theta[index] - settings.step_prior_mean) / settings.step_prior_sd
        value += 0.5 * z * z
        gradient[index] = z / settings.step_prior_sd
    z = (theta[4] - settings.forget_prior_mean) / settings.forget_prior_sd
    value += 0.5 * z * z
    gradient[4] = z / settings.forget_prior_sd
    for index in (5, 6):
        z = theta[index] / settings.entrant_prior_sd
        value += 0.5 * z * z
        gradient[index] = z / settings.entrant_prior_sd
    return value, gradient


def default_start(home_goals: np.ndarray, away_goals: np.ndarray) -> np.ndarray:
    """Cold start (natural units): league goal means, step 0.02, forgetting 0.007, no entrant effect."""
    away_mean = max(float(np.mean(away_goals)), 0.05)
    home_mean = max(float(np.mean(home_goals)), 0.05)
    return np.array([math.log(away_mean), math.log(home_mean / away_mean), 0.02, 0.02, 0.007, 0.0, 0.0])


def fit_gas_arrays(
    home: Sequence[int],
    away: Sequence[int],
    home_goals: Sequence[float],
    away_goals: Sequence[float],
    *,
    n_teams: int,
    entrants: Sequence[int] = (),
    start: np.ndarray | None = None,
    settings: GasMleSettings | None = None,
    team_keys: tuple = (),
) -> GasFit:
    """Maximise the penalised filtered Poisson likelihood; matches must be in chronological order.

    ``home``/``away`` are team indices ``0..n_teams-1``; ``entrants`` lists the
    teams that entered the league after its first observed season (their state
    starts at the estimated entrant prior instead of zero).  ``start`` is a
    parameter vector in the order of :data:`PARAMETER_NAMES` and natural units
    (for example ``GasFit.theta()`` of an earlier fit on a subset of the same
    matches): it only chooses where the optimiser begins.
    """
    from scipy.optimize import minimize

    settings = settings or GasMleSettings()
    goals_h = [float(v) for v in home_goals]
    goals_a = [float(v) for v in away_goals]
    if not goals_h:
        raise ValueError("no matches to fit")
    data = _GasData(
        [int(v) for v in home],
        [int(v) for v in away],
        goals_h,
        goals_a,
        int(n_teams),
        [int(v) for v in entrants],
    )
    n = data.n_matches
    if start is None:
        theta0 = default_start(np.asarray(goals_h), np.asarray(goals_a))
    else:
        theta0 = np.asarray(start, dtype=float).copy()
    bounds = settings.bounds()
    x0 = np.clip(theta0 * _SCALE, [b[0] for b in bounds], [b[1] for b in bounds])
    evaluations = 0

    def objective(x: np.ndarray):
        nonlocal evaluations
        evaluations += 1
        theta = x / _SCALE
        log_likelihood, gradient = _log_likelihood_and_gradient(theta, data, settings.max_log_rate)
        pen, pen_gradient = _penalty(theta, settings)
        value = -(log_likelihood - pen) / n
        if not math.isfinite(value):
            return 1e10, np.zeros_like(x)
        return value, -(gradient - pen_gradient) / n / _SCALE

    result = minimize(
        objective,
        x0,
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": settings.max_iterations, "ftol": settings.ftol, "gtol": settings.gtol},
    )
    theta = result.x / _SCALE
    log_likelihood, attack, defence = _run_filter(theta, data, settings.max_log_rate)
    pen, _ = _penalty(theta, settings)
    return GasFit(
        intercept=float(theta[0]),
        home_advantage=float(theta[1]),
        attack_step=float(theta[2]),
        defence_step=float(theta[3]),
        persistence=float(1.0 - theta[4]),
        entrant_attack=float(theta[5]),
        entrant_defence=float(theta[6]),
        attack=np.asarray(attack),
        defence=np.asarray(defence),
        log_likelihood=float(log_likelihood),
        penalty=float(pen),
        n_matches=n,
        n_evaluations=evaluations,
        converged=bool(result.success),
        message=str(result.message),
        team_keys=tuple(team_keys),
    )


def first_appearance_entrants(
    home: np.ndarray, away: np.ndarray, season: np.ndarray, n_teams: int
) -> list[int]:
    """Teams whose first match falls in a later season than the first season of the data."""
    if len(home) == 0:
        return []
    interleaved = np.empty(2 * len(home), dtype=np.int64)
    interleaved[0::2] = home
    interleaved[1::2] = away
    _, first_index = np.unique(interleaved, return_index=True)
    teams = interleaved[first_index]
    first_season = np.asarray(season)[first_index // 2]
    return [int(t) for t in teams[first_season > np.min(season)]]


def fit_gas_poisson_mle(
    matches: pd.DataFrame,
    *,
    settings: GasMleSettings | None = None,
    start: np.ndarray | None = None,
) -> tuple[GasPoissonModel, GasFit]:
    """Estimate the GAS-Poisson parameters by maximum likelihood on ``matches``.

    Same input as :func:`fit_gas_poisson` (``date``, ``home_team``, ``away_team``,
    ``home_goals``, ``away_goals``); an optional ``season`` column marks the
    teams that entered the league after the first season, whose prior state is
    estimated.  Returns the filtered model at the end of ``matches`` (usable with
    ``expected_goals`` / ``observe_match``) and the raw fit.
    """
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    columns = sorted(required | ({"season"} if "season" in matches.columns else set()))
    data = matches.loc[:, columns].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce", format="mixed")
    data["home_goals"] = pd.to_numeric(data["home_goals"], errors="coerce")
    data["away_goals"] = pd.to_numeric(data["away_goals"], errors="coerce")
    if data[["date", "home_team", "away_team", "home_goals", "away_goals"]].isna().any().any():
        raise ValueError("matches contains missing or invalid required values")
    if (data[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("goals must be non-negative")
    data = data.sort_values(["date", "home_team", "away_team"], kind="stable")
    home_names = data["home_team"].astype(str).to_numpy()
    away_names = data["away_team"].astype(str).to_numpy()
    keys, inverse = np.unique(np.concatenate([home_names, away_names]), return_inverse=True)
    n = len(data)
    home_index, away_index = inverse[:n], inverse[n:]
    entrants = (
        first_appearance_entrants(home_index, away_index, data["season"].to_numpy(), len(keys))
        if "season" in data.columns
        else []
    )
    fit = fit_gas_arrays(
        home_index,
        away_index,
        data["home_goals"].to_numpy(),
        data["away_goals"].to_numpy(),
        n_teams=len(keys),
        entrants=entrants,
        start=start,
        settings=settings,
        team_keys=tuple(keys),
    )
    return fit.to_model(), fit
