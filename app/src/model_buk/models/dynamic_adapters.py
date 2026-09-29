"""Adapters that plug the sequential models into the common evaluation framework.

The GAS-Poisson model (static parameters by maximum likelihood, states from
the filtered recursion) and the Elo-Davidson model are *sequential*: fitting
means running the recursion through the training matches, and the state at the
cutoff is what forecasts the next matchday.  The framework wants
``fit(train)`` / ``predict(frame)`` objects (see
:mod:`model_buk.evaluation.framework`), refit before every matchday.  These
adapters do that and add what the plain models lack:

- **Team identity** by the provider's team id, one model per league (a training
  frame with several leagues is refused).
- **Promoted-team prior.**  A team is *new to the league* when its first match
  falls in a later season than the first season of the training data.  GAS
  starts such a team at an estimated attack/defence prior, Elo at the mean
  rating of the league's active teams plus an estimated (negative) shift; both
  are estimated on the training matches only.  A team that the training data
  has never seen gets the same prior at forecast time.
- **Markets.**  GAS turns the two Poisson rates into an (independent-Poisson)
  score matrix and reads 1X2, over/under lines and BTTS from it, so one fit
  serves every goal market.  Elo forecasts 1X2 only.
- **A fit cache** keyed by ``(model, league, parameters, exact training set)``.
  The training set is identified by a digest of fixture ids, teams, seasons and
  goals, so a hit can only return the fit of the identical data: the optimum is
  unchanged, the cache merely avoids fitting twice (all five goal markets share
  the training set of a matchday; the inner hyper-parameter folds of one outer
  season reuse fits of the outer run).  ``cache=None`` disables it.  The
  leakage test runs with the cache off: with it on, the early matchdays of the
  disturbed run would be served from the fits of the original run and the test
  would no longer exercise the fitting code.

Nothing here reads a column that the framework hides from ``predict``: the
forecast frame supplies team ids and the season label only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd
from scipy.special import gammaln

from model_buk.evaluation.framework import MARKET_1X2, Market, ModelSpec
from model_buk.models.elo_davidson import ENTRANT_SHIFT_GRID, ENTRANT_SHIFT_PRIOR, EloFit, fit_elo_arrays
from model_buk.models.gas_poisson import (
    GasFit,
    GasMleSettings,
    GasPoissonConfig,
    fit_gas_arrays,
    first_appearance_entrants,
)

DEFAULT_MAX_GOALS = 15
ELO_K_GRID = (10.0, 20.0, 30.0, 40.0)

# ---------------------------------------------------------------------------
# Score matrix and goal markets
# ---------------------------------------------------------------------------


def poisson_score_matrix(
    home_rate: np.ndarray, away_rate: np.ndarray, *, max_goals: int = DEFAULT_MAX_GOALS
) -> np.ndarray:
    """Joint score distribution ``(n, max_goals + 1, max_goals + 1)`` of two independent Poissons.

    Truncated at ``max_goals`` per side and renormalised (with 15 goals the
    truncated mass is below 1e-9 for the rates in use).
    """
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    home_rate = np.asarray(home_rate, dtype=float)
    away_rate = np.asarray(away_rate, dtype=float)
    if np.any(home_rate <= 0) or np.any(away_rate <= 0):
        raise ValueError("Poisson rates must be positive")
    goals = np.arange(max_goals + 1, dtype=float)
    log_factorial = gammaln(goals + 1.0)

    def pmf(rate: np.ndarray) -> np.ndarray:
        return np.exp(goals[None, :] * np.log(rate)[:, None] - rate[:, None] - log_factorial[None, :])

    matrix = pmf(home_rate)[:, :, None] * pmf(away_rate)[:, None, :]
    return matrix / matrix.sum(axis=(1, 2), keepdims=True)


def goal_market_probabilities(matrix: np.ndarray, market: Market) -> np.ndarray:
    """Probabilities in the class order of ``market`` from a score matrix ``(n, G+1, G+1)``.

    Supports the goal markets of the comparison protocol: ``1x2`` (home, draw,
    away), ``over_under_<line>`` (under, over) and ``btts`` (no, yes).
    """
    size = matrix.shape[1]
    home_goals = np.arange(size)[:, None]
    away_goals = np.arange(size)[None, :]
    name = market.name
    if name == "1x2":
        parts = [
            (home_goals > away_goals),
            (home_goals == away_goals),
            (home_goals < away_goals),
        ]
    elif name == "btts":
        both = (home_goals > 0) & (away_goals > 0)
        parts = [~both, both]
    elif name.startswith("over_under_"):
        over = (home_goals + away_goals) > float(name.removeprefix("over_under_"))
        parts = [~over, over]
    else:
        raise ValueError(f"market {name!r} is not a goal market this adapter can price")
    columns = [(matrix * mask[None, :, :]).sum(axis=(1, 2)) for mask in parts]
    return np.column_stack(columns)


# ---------------------------------------------------------------------------
# Fit cache and training-set extraction
# ---------------------------------------------------------------------------


class FitCache:
    """Fits keyed by ``(model, league, parameters, digest of the exact training set)``."""

    def __init__(self) -> None:
        self.items: dict[tuple, Any] = {}
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self.items)

    def get(self, key: tuple) -> Any | None:
        value = self.items.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: tuple, value: Any) -> None:
        self.items[key] = value

    def update(self, other: "FitCache | Mapping[tuple, Any]") -> None:
        self.items.update(other.items if isinstance(other, FitCache) else other)

    def stats(self) -> dict[str, int]:
        return {"items": len(self.items), "hits": self.hits, "misses": self.misses}


@dataclass
class _Training:
    league: str
    fixture_id: np.ndarray
    home_team: np.ndarray
    away_team: np.ndarray
    season: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    team_ids: np.ndarray  # sorted unique team ids
    home_index: np.ndarray
    away_index: np.ndarray
    entrants: list[int]
    digest: str = field(default="")


def _training_arrays(train: pd.DataFrame, *, league_col: str = "league_code") -> _Training:
    """Chronological arrays of a training frame; raises for several leagues or missing values."""
    if train.empty:
        raise ValueError("training frame is empty")
    required = ["fixture_id", "home_team_id", "away_team_id", "season", "home_goals", "away_goals"]
    absent = [column for column in required if column not in train.columns]
    if absent:
        raise ValueError(f"training frame is missing columns: {', '.join(absent)}")
    if league_col in train.columns:
        leagues = train[league_col].unique()
        if len(leagues) != 1:
            raise ValueError("the sequential models are fitted per league; got a training frame with several leagues")
        league = str(leagues[0])
    else:
        league = ""
    goals = train[["home_goals", "away_goals"]].to_numpy(dtype=float)
    if not np.isfinite(goals).all() or (goals < 0).any():
        raise ValueError("training goals must be finite and non-negative (missing stays missing: drop such matches first)")
    fixture_id = train["fixture_id"].to_numpy(dtype=np.int64)
    home_team = train["home_team_id"].to_numpy(dtype=np.int64)
    away_team = train["away_team_id"].to_numpy(dtype=np.int64)
    season = train["season"].to_numpy(dtype=np.int64)
    if "kickoff" in train.columns:
        kickoff = pd.DatetimeIndex(train["kickoff"]).asi8
        if len(kickoff) > 1 and (np.diff(kickoff) < 0).any():
            order = np.lexsort((fixture_id, kickoff))
            fixture_id, home_team, away_team, season, goals = (
                fixture_id[order],
                home_team[order],
                away_team[order],
                season[order],
                goals[order],
            )
    n = len(fixture_id)
    team_ids, inverse = np.unique(np.concatenate([home_team, away_team]), return_inverse=True)
    home_index, away_index = inverse[:n], inverse[n:]
    digest = hashlib.blake2b(digest_size=16)
    for array in (fixture_id, home_team, away_team, season, goals[:, 0].astype(np.int64), goals[:, 1].astype(np.int64)):
        digest.update(np.ascontiguousarray(array, dtype=np.int64).tobytes())
    return _Training(
        league=league,
        fixture_id=fixture_id,
        home_team=home_team,
        away_team=away_team,
        season=season,
        home_goals=goals[:, 0],
        away_goals=goals[:, 1],
        team_ids=team_ids,
        home_index=home_index,
        away_index=away_index,
        entrants=first_appearance_entrants(home_index, away_index, season, len(team_ids)),
        digest=digest.hexdigest(),
    )


@dataclass
class _GasFitted:
    fit: GasFit
    index: pd.Index  # team ids in fit order


@dataclass
class _EloFitted:
    fit: EloFit
    index: pd.Index


# ---------------------------------------------------------------------------
# GAS-Poisson
# ---------------------------------------------------------------------------


class GasPoissonForecaster:
    """GAS-Poisson with maximum-likelihood parameters, one league per instance.

    ``fit(train)`` estimates (intercept, home advantage, attack/defence step,
    persistence, promoted-team prior) on the training matches and filters the
    states up to the last one; ``predict(frame)`` turns the state of the two
    teams into Poisson rates, builds the score matrix and reads the market.
    """

    def __init__(
        self,
        market: Market,
        *,
        settings: GasMleSettings | None = None,
        cache: FitCache | None = None,
        max_goals: int = DEFAULT_MAX_GOALS,
        rate_bounds: tuple[float, float] | None = None,
    ) -> None:
        self.market = market
        self.classes = market.classes
        self.settings = settings or GasMleSettings()
        self.cache = cache
        self.max_goals = max_goals
        defaults = GasPoissonConfig()
        self.rate_bounds = rate_bounds or (defaults.min_rate, defaults.max_rate)
        self._fitted: _GasFitted | None = None
        self._settings_key = repr(self.settings)

    def fit(self, train: pd.DataFrame) -> "GasPoissonForecaster":
        data = _training_arrays(train)
        key = ("gas_poisson", data.league, self._settings_key, data.digest)
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is None:
            fit = fit_gas_arrays(
                data.home_index,
                data.away_index,
                data.home_goals,
                data.away_goals,
                n_teams=len(data.team_ids),
                entrants=data.entrants,
                settings=self.settings,
                team_keys=tuple(int(t) for t in data.team_ids),
            )
            cached = _GasFitted(fit, pd.Index(data.team_ids))
            if self.cache is not None:
                self.cache.put(key, cached)
        self._fitted = cached
        return self

    @property
    def parameters(self) -> dict[str, float]:
        if self._fitted is None:
            raise RuntimeError("fit must be called first")
        return self._fitted.fit.parameters()

    def expected_goals(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Poisson rates (home, away) for every row of ``frame`` from the state at the cutoff."""
        if self._fitted is None:
            raise RuntimeError("fit must be called before predict")
        fit = self._fitted.fit
        home = self._fitted.index.get_indexer(frame["home_team_id"].to_numpy())
        away = self._fitted.index.get_indexer(frame["away_team_id"].to_numpy())
        # Index -1 (a team the training data has never seen) reads the last element: the entrant prior.
        attack = np.append(fit.attack, fit.entrant_attack)
        defence = np.append(fit.defence, fit.entrant_defence)
        log_home = fit.intercept + fit.home_advantage + attack[home] + defence[away]
        log_away = fit.intercept + attack[away] + defence[home]
        low, high = self.rate_bounds
        return np.clip(np.exp(log_home), low, high), np.clip(np.exp(log_away), low, high)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        home_rate, away_rate = self.expected_goals(frame)
        matrix = poisson_score_matrix(home_rate, away_rate, max_goals=self.max_goals)
        return goal_market_probabilities(matrix, self.market)


def gas_poisson_spec(
    market: Market,
    *,
    cache: FitCache | None = None,
    settings: GasMleSettings | None = None,
    name: str = "gas_poisson",
) -> ModelSpec:
    """Framework spec of the GAS-Poisson model for one goal market (no hyper-parameter grid)."""
    return ModelSpec(name, lambda: GasPoissonForecaster(market, settings=settings, cache=cache))


# ---------------------------------------------------------------------------
# Elo-Davidson
# ---------------------------------------------------------------------------


class EloDavidsonForecaster:
    """Sequential Elo-Davidson (1X2 only), one league per instance."""

    classes = MARKET_1X2.classes

    def __init__(
        self,
        k_factor: float = 20.0,
        *,
        cache: FitCache | None = None,
        alternations: int = 2,
        entrant_grid: tuple[float, ...] = ENTRANT_SHIFT_GRID,
        entrant_prior: tuple[float, float] | None = ENTRANT_SHIFT_PRIOR,
        rating_scale: float = 400.0,
        initial_rating: float = 1500.0,
    ) -> None:
        self.k_factor = float(k_factor)
        self.cache = cache
        self.alternations = int(alternations)
        self.entrant_grid = tuple(float(v) for v in entrant_grid)
        self.entrant_prior = None if entrant_prior is None else (float(entrant_prior[0]), float(entrant_prior[1]))
        self.rating_scale = float(rating_scale)
        self.initial_rating = float(initial_rating)
        self._fitted: _EloFitted | None = None

    def fit(self, train: pd.DataFrame) -> "EloDavidsonForecaster":
        data = _training_arrays(train)
        key = (
            "elo_davidson",
            data.league,
            (self.k_factor, self.alternations, self.entrant_grid, self.entrant_prior, self.rating_scale, self.initial_rating),
            data.digest,
        )
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is None:
            outcome = np.where(data.home_goals > data.away_goals, 0, np.where(data.home_goals == data.away_goals, 1, 2))
            fit = fit_elo_arrays(
                data.home_index,
                data.away_index,
                outcome,
                data.season,
                n_teams=len(data.team_ids),
                k_factor=self.k_factor,
                rating_scale=self.rating_scale,
                initial_rating=self.initial_rating,
                entrants=data.entrants,
                team_keys=tuple(int(t) for t in data.team_ids),
                alternations=self.alternations,
                entrant_grid=self.entrant_grid,
                entrant_prior=self.entrant_prior,
            )
            cached = _EloFitted(fit, pd.Index(data.team_ids))
            if self.cache is not None:
                self.cache.put(key, cached)
        self._fitted = cached
        return self

    @property
    def parameters(self) -> dict[str, float]:
        if self._fitted is None:
            raise RuntimeError("fit must be called first")
        fit = self._fitted.fit
        return {
            "k_factor": fit.k_factor,
            "home_advantage_points": fit.home_advantage_points,
            "draw_parameter": fit.draw_parameter,
            "entrant_shift": fit.entrant_shift,
        }

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self._fitted is None:
            raise RuntimeError("fit must be called before predict")
        home = self._fitted.index.get_indexer(frame["home_team_id"].to_numpy())
        away = self._fitted.index.get_indexer(frame["away_team_id"].to_numpy())
        return self._fitted.fit.probabilities(home, away, frame["season"].to_numpy(dtype=np.int64))


def elo_davidson_spec(
    *,
    k_grid: tuple[float, ...] = ELO_K_GRID,
    cache: FitCache | None = None,
    name: str = "elo_davidson",
) -> ModelSpec:
    """Framework spec of the Elo-Davidson model; ``k_factor`` is chosen on the inner season."""
    return ModelSpec(
        name,
        lambda k_factor: EloDavidsonForecaster(k_factor, cache=cache),
        param_grid={"k_factor": list(k_grid)},
    )


__all__ = [
    "DEFAULT_MAX_GOALS",
    "ELO_K_GRID",
    "EloDavidsonForecaster",
    "FitCache",
    "GasPoissonForecaster",
    "elo_davidson_spec",
    "gas_poisson_spec",
    "goal_market_probabilities",
    "poisson_score_matrix",
]
