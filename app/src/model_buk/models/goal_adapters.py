"""Dixon-Coles and NB2 goal models as models of the common evaluation framework.

The framework (:mod:`model_buk.evaluation.framework`) wants ``fit(train)`` and
``predict(frame) -> (n, K)`` probabilities per market.  The two adapters here
turn the goal models of :mod:`.dixon_coles` and :mod:`.negative_binomial` into
exactly that, for every market of :data:`model_buk.evaluation.protocol.GOAL_MARKETS`
(1X2, total goals over/under 1.5 / 2.5 / 3.5 and both teams to score)::

    from model_buk.evaluation.framework import evaluate_model
    from model_buk.evaluation.protocol import COMPARISON_CONFIG, GOAL_MARKETS
    from model_buk.models.goal_adapters import dixon_coles_spec

    for market in GOAL_MARKETS:
        result = evaluate_model(dixon_coles_spec(market), matches, market, COMPARISON_CONFIG)

What a fit is
-------------
``fit(train)`` fits one league (the framework's per-league scope; a training
frame that mixes leagues is refused) by weighted maximum likelihood:

- every match is weighted ``exp(-ln 2 * age / half_life)``, age in days before
  the *last training match* (so the weights use nothing but training data);
- a ridge penalty (``L2_STRENGTH``, in units of one full-weight match) keeps
  the strength of teams with few recent matches near the league average and
  makes the fit well posed when old seasons carry almost no weight;
- Dixon-Coles adds the low-score correction ``rho``; NB2 has independent
  negative-binomial margins with one shared dispersion ``alpha`` (both models
  have the same team attack/defence effects, home advantage and intercept).
  With team effects in the mean, football goals show almost no overdispersion
  left: on the real data ``alpha`` sits on its lower bound (the Poisson limit)
  in about 90% of the league refits, so NB2 is close to independent Poisson
  goals and differs from Dixon-Coles mainly by the missing ``rho``.

``predict(frame)`` builds the truncated, renormalised score matrix of every
match (12 goals per side) and sums it into the market's classes, so all
markets of one match are coherent (1X2 sums to one, totals are cumulative,
BTTS is the complement of "a side scores nothing").

Teams the fit has never seen (promoted or new teams)
----------------------------------------------------
A team with no match in the training data gets the *newcomer prior*: the
average first-season strength of the league's earlier newcomers.  For every
team that first appears in the training data after the league's first season
(and whose first season is complete) the fit computes how many goals it scored
and conceded in that first season relative to what the fitted opponents would
have allowed a league-average team (``log(goals / expected goals)``).  The
prior is the mean of those team-seasons shrunk towards a global below-average
default with ``NEWCOMER_PRIOR_WEIGHT`` pseudo team-seasons, so a league with no
history of promotions falls back on the default.  The default constants were
estimated on the seasons up to 2020 only (see the comment at
``NEWCOMER_PRIOR_ATTACK``), which precede every inner and outer test season.
A team that returns after some seasons away is *not* replaced by the prior: it
keeps its fitted strength, which the ridge penalty has shrunk to the league
average because its matches are old.  The ridge shrinks a newcomer that is
already in the data towards the league average, not towards the newcomer prior.

Speed
-----
A league refit is a few milliseconds thanks to analytic gradients
(:func:`~model_buk.models.dixon_coles.fit_dixon_coles_arrays`) and to two
safe caches held in :class:`FitCache`:

- a *warm start*: the previous solution of the same league, model and
  hyper-parameters is only the optimiser's starting point (same optimum, same
  data window, no look-ahead);
- a *fit cache* keyed by (model, league, hyper-parameters, exact training set:
  a digest of the fixtures, teams, goals, kickoffs and seasons), so a fit made
  for one market or one inner fold serves every other request with the same
  training set, and can never be served for another one (change one goal and
  the key changes).

:func:`warm_fit_cache` fills the cache in parallel over leagues with joblib
before the (single-process, sequential) evaluation runs.
"""

from __future__ import annotations

import hashlib
import math
import os
import pickle
import time
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.special import gammaln

from model_buk.evaluation.framework import (
    MARKET_1X2,
    Market,
    ModelSpec,
    WalkForwardConfig,
    evaluate_model,
)
from model_buk.models.dixon_coles import DixonColesModel, fit_dixon_coles_arrays
from model_buk.models.negative_binomial import NegativeBinomialModel, fit_negative_binomial_arrays

FIT_VERSION = "goal-adapters-v1"
"""Bump when a fit would change (likelihood, priors, constants): stale cache entries are then ignored."""

HALF_LIVES_DAYS: tuple[int, ...] = (180, 365, 730, 1200)
"""Plan: half-life grid 180-1200 days; the framework picks one per test season on the inner fold."""

L2_STRENGTH = 4.0
"""Ridge penalty ``L2_STRENGTH * (sum attack^2 + sum defence^2)`` in full-weight-match units.

Chosen once on the season 2021 (the inner fold of the first test season; data up
to 2021 only) with Dixon-Coles on 1X2, half-lives 365 and 1200 days, pooled over
all 47 leagues: log-loss 1.041 without a penalty, 1.0170 / 1.0157 / 1.0152 /
1.0167 / 1.0215 for penalties 1 / 2 / 4 / 8 / 16 at half-life 365 (the same
ordering at 1200).  Never tuned on a test season; ``run_models_dc_nb2.py --stage
pilot`` reproduces it."""

MAX_GOALS = 12
"""Goals per side kept in the score matrix (renormalised); P(more than 12) is ~1e-6."""

NEWCOMER_PRIOR_ATTACK = -0.05
NEWCOMER_PRIOR_DEFENCE = 0.05
"""Global default first-season strength of a newcomer (log goal-rate scale).

Attack below and defence above the league average (a positive defence value
lets the opponent score more).  Estimated as the mean over the 276 newcomer
team-seasons of the 34 leagues with an earlier promotion in the seasons up to
and including 2020: attack -0.047, defence +0.047, rounded.  The leagues differ
a lot (top leagues -0.1 to -0.3 attack, lower divisions about 0), which is why
this is only the shrinkage target of the per-league average, and alone for
leagues that have no earlier newcomers."""

NEWCOMER_PRIOR_WEIGHT = 6.0
"""Pseudo team-seasons of the global default in the per-league newcomer average."""

MIN_NEWCOMER_MATCHES = 8
"""A newcomer's first season counts towards the prior only with at least this many matches."""

_NS_PER_DAY = 86_400_000_000_000
_LOG_RATE_CLIP = 12.0
_MIN_TAU = 1e-12
_MIN_TRAIN_ROWS = 2


# ---------------------------------------------------------------------------
# Fitted state and the fit cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalFit:
    """A fitted goal model on team keys (API-Football team ids, or names as fallback).

    ``shape`` is ``rho`` for Dixon-Coles and the dispersion ``alpha`` for NB2.
    ``newcomer_attack`` / ``newcomer_defence`` are what a team unseen in the
    training data gets (see the module docstring).
    """

    kind: str
    team_keys: np.ndarray
    attack: np.ndarray
    defence: np.ndarray
    intercept: float
    home_advantage: float
    shape: float
    newcomer_attack: float
    newcomer_defence: float
    n_newcomer_teams: int
    n_matches: int
    converged: bool
    n_iterations: int

    def strengths(self, keys: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Attack, defence and a "team was in the training data" mask for ``keys``."""
        position = np.minimum(np.searchsorted(self.team_keys, keys), len(self.team_keys) - 1)
        known = self.team_keys[position] == keys
        attack = np.where(known, self.attack[position], self.newcomer_attack)
        defence = np.where(known, self.defence[position], self.newcomer_defence)
        return attack, defence, known

    def to_model(self) -> DixonColesModel | NegativeBinomialModel:
        """The fitted parameters as the plain model dataclass (team names are ``str(key)``)."""
        names = tuple(str(key) for key in self.team_keys)
        attack = dict(zip(names, (float(v) for v in self.attack), strict=True))
        defence = dict(zip(names, (float(v) for v in self.defence), strict=True))
        if self.kind == "dixon_coles":
            return DixonColesModel(
                teams=names,
                attack=attack,
                defence=defence,
                intercept=self.intercept,
                home_advantage=self.home_advantage,
                rho=self.shape,
                reference_date=pd.Timestamp(0),
                xi=0.0,
                l2_strength=L2_STRENGTH,
                xg_weight=0.0,
            )
        return NegativeBinomialModel(
            teams=names,
            attack=attack,
            defence=defence,
            intercept=self.intercept,
            home_advantage=self.home_advantage,
            alpha=self.shape,
            l2_strength=L2_STRENGTH,
        )


class FitCache:
    """Fits keyed by (model, league, parameters, exact training set) plus warm-start solutions.

    ``get`` / ``put`` count hits and misses.  The key contains a digest of the
    training set's content, so a fit is served only for the identical data.
    Warm starts are per (model, league, parameters) and are only starting
    values for the optimiser; they never enter the key or the result.
    """

    def __init__(self) -> None:
        self._fits: dict[tuple[Any, ...], GoalFit] = {}
        self._warm: dict[tuple[Any, ...], tuple[np.ndarray, tuple[float, float, float, np.ndarray, np.ndarray], float, float]] = {}
        self.hits = 0
        self.misses = 0
        self.failures = 0

    def __len__(self) -> int:
        return len(self._fits)

    def get(self, key: tuple[Any, ...]) -> GoalFit | None:
        found = self._fits.get(key)
        if found is None:
            self.misses += 1
        else:
            self.hits += 1
        return found

    def put(self, key: tuple[Any, ...], fit: GoalFit) -> None:
        self._fits[key] = fit
        if not fit.converged:
            self.failures += 1

    def warm_start(self, key: tuple[Any, ...]):
        return self._warm.get(key)

    def set_warm_start(self, key: tuple[Any, ...], fit: GoalFit) -> None:
        if fit.converged:
            self._warm[key] = (
                fit.team_keys,
                (fit.intercept, fit.home_advantage, fit.shape, fit.attack, fit.defence),
                fit.newcomer_attack,
                fit.newcomer_defence,
            )

    def clear(self) -> None:
        """Forget every fit and warm start and reset the counters (start of an independent run)."""
        self._fits.clear()
        self._warm.clear()
        self.hits = self.misses = self.failures = 0

    def stats(self) -> dict[str, int]:
        return {"fits": len(self._fits), "hits": self.hits, "misses": self.misses, "failures": self.failures}

    def update(self, other: "FitCache | dict[tuple[Any, ...], GoalFit]") -> int:
        """Merge fits from another cache (or a plain dict); returns the number added."""
        source = other._fits if isinstance(other, FitCache) else other
        before = len(self._fits)
        for key, fit in source.items():
            if key[0] == FIT_VERSION:
                self._fits.setdefault(key, fit)
        return len(self._fits) - before

    def save(self, path: str | Path) -> Path:
        """Write the fits (not the warm starts) atomically."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with open(temporary, "wb") as handle:
            pickle.dump({"version": FIT_VERSION, "fits": self._fits}, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, target)
        return target

    def load(self, path: str | Path) -> int:
        """Merge a file written by :meth:`save`; files of another version are ignored."""
        with open(path, "rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict) or payload.get("version") != FIT_VERSION:
            return 0
        return self.update(payload["fits"])


FIT_CACHE = FitCache()
"""The process-wide default cache used by the model specs."""


# ---------------------------------------------------------------------------
# Training data
# ---------------------------------------------------------------------------


def _numeric(series: pd.Series, dtype: type) -> np.ndarray:
    """A numeric column as a numpy array; the slow ``to_numeric`` only when the dtype is not numeric."""
    if series.dtype.kind in "iufb":
        return series.to_numpy(dtype=dtype, na_value=np.nan) if series.dtype.kind == "f" else series.to_numpy(dtype=dtype)
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=dtype, na_value=np.nan)


def _team_keys(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Home and away team keys: API-Football ids when complete, else team names."""
    if {"home_team_id", "away_team_id"} <= set(frame.columns):
        home = frame["home_team_id"]
        away = frame["away_team_id"]
        if home.dtype.kind in "iu" and away.dtype.kind in "iu":
            return home.to_numpy(dtype=np.int64), away.to_numpy(dtype=np.int64)
        home = pd.to_numeric(home, errors="coerce")
        away = pd.to_numeric(away, errors="coerce")
        if not (home.isna().any() or away.isna().any()):
            return home.to_numpy(dtype=np.int64), away.to_numpy(dtype=np.int64)
    return (
        frame["home_team"].astype(str).to_numpy(dtype=object),
        frame["away_team"].astype(str).to_numpy(dtype=object),
    )


def _kickoff_ns(frame: pd.DataFrame) -> np.ndarray:
    """Kickoffs as UTC nanoseconds since the epoch (parsed only when the column is text)."""
    column = "kickoff" if "kickoff" in frame.columns else "kickoff_utc"
    stamps = frame[column]
    if not pd.api.types.is_datetime64_any_dtype(stamps):
        stamps = pd.to_datetime(stamps, utc=True, format="mixed")
    if stamps.dt.tz is not None:
        stamps = stamps.dt.tz_convert("UTC").dt.tz_localize(None)
    return stamps.dt.as_unit("ns").to_numpy().view("int64")


def _digest(arrays: Iterable[np.ndarray]) -> bytes:
    """Content digest of the training columns (dtype, length and every value)."""
    hasher = hashlib.blake2b(digest_size=16)
    for array in arrays:
        array = np.asarray(array)
        hasher.update(f"{array.dtype.str}:{array.shape}|".encode())
        if array.dtype.kind in "iuf":
            hasher.update(np.ascontiguousarray(array).tobytes())
        else:
            hasher.update(pd.util.hash_array(array.astype(object)).tobytes())
    return hasher.digest()


@dataclass
class _Training:
    league: str
    home_keys: np.ndarray
    away_keys: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    kickoff_ns: np.ndarray
    season: np.ndarray
    digest: bytes

    @property
    def n(self) -> int:
        return len(self.home_goals)


def _training_arrays(train: pd.DataFrame) -> _Training:
    if "league_code" in train.columns:
        codes = train["league_code"]
        if codes.nunique(dropna=False) > 1:
            raise ValueError(
                "goal models are fitted one league at a time (WalkForwardConfig scope='per_league'); "
                f"the training frame holds {codes.nunique(dropna=False)} leagues"
            )
        league = str(codes.iloc[0]) if len(codes) else ""
    else:
        league = ""
    home_keys, away_keys = _team_keys(train)
    home_goals = _numeric(train["home_goals"], float)
    away_goals = _numeric(train["away_goals"], float)
    if not (np.isfinite(home_goals).all() and np.isfinite(away_goals).all()):
        raise ValueError("training matches must have both goal counts (missing stays missing, never zero)")
    kickoff = _kickoff_ns(train)
    season = _numeric(train["season"], np.int64) if "season" in train.columns else np.zeros(len(train), dtype=np.int64)
    fixtures = _numeric(train["fixture_id"], np.int64) if "fixture_id" in train.columns else np.zeros(len(train), dtype=np.int64)
    digest = _digest([fixtures, home_keys, away_keys, home_goals, away_goals, kickoff, season])
    return _Training(league, home_keys, away_keys, home_goals, away_goals, kickoff, season, digest)


def _decay_weights(kickoff_ns: np.ndarray, half_life_days: float) -> np.ndarray:
    """``exp(-ln 2 * age / half_life)`` with age in days before the last training kickoff."""
    age_days = (kickoff_ns.max() - kickoff_ns) / _NS_PER_DAY
    return np.exp(-math.log(2.0) / float(half_life_days) * age_days)


# ---------------------------------------------------------------------------
# Newcomer prior
# ---------------------------------------------------------------------------


def newcomer_prior(
    intercept: float,
    home_advantage: float,
    attack: np.ndarray,
    defence: np.ndarray,
    home_index: np.ndarray,
    away_index: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    season: np.ndarray,
    *,
    default_attack: float = NEWCOMER_PRIOR_ATTACK,
    default_defence: float = NEWCOMER_PRIOR_DEFENCE,
    prior_weight: float = NEWCOMER_PRIOR_WEIGHT,
    min_matches: int = MIN_NEWCOMER_MATCHES,
) -> tuple[float, float, int]:
    """Average first-season strength of the league's earlier newcomers, shrunk to a default.

    A newcomer is a team whose first match in the data is after the league's
    first season and before its last season (so its first season is over).
    For its first season, ``attack = log((goals scored + 0.5) / (expected + 0.5))``
    where *expected* is what a team with attack 0 would score against the
    fitted defences of its opponents (home advantage included), and
    ``defence = log((goals conceded + 0.5) / (expected + 0.5))`` likewise
    (a positive value is a leaky defence).  Returns ``(attack, defence, n)``.
    """
    n_teams = len(attack)
    first = np.full(n_teams, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, home_index, season)
    np.minimum.at(first, away_index, season)
    first_of_league = season.min()
    last_of_league = season.max()
    in_first_home = season == first[home_index]
    in_first_away = season == first[away_index]
    if first_of_league == last_of_league:
        return default_attack, default_defence, 0
    expected_scoring_home = np.exp(intercept + home_advantage + defence[away_index])
    expected_scoring_away = np.exp(intercept + defence[home_index])
    expected_conceding_home = np.exp(intercept + attack[away_index])
    expected_conceding_away = np.exp(intercept + home_advantage + attack[home_index])

    def by_team(index: np.ndarray, mask: np.ndarray, values: np.ndarray) -> np.ndarray:
        return np.bincount(index[mask], values[mask], n_teams)

    scored = by_team(home_index, in_first_home, home_goals) + by_team(away_index, in_first_away, away_goals)
    expected_scored = by_team(home_index, in_first_home, expected_scoring_home) + by_team(
        away_index, in_first_away, expected_scoring_away
    )
    conceded = by_team(home_index, in_first_home, away_goals) + by_team(away_index, in_first_away, home_goals)
    expected_conceded = by_team(home_index, in_first_home, expected_conceding_home) + by_team(
        away_index, in_first_away, expected_conceding_away
    )
    matches = np.bincount(home_index[in_first_home], minlength=n_teams) + np.bincount(
        away_index[in_first_away], minlength=n_teams
    )
    eligible = (first > first_of_league) & (first < last_of_league) & (matches >= min_matches)
    n_eligible = int(eligible.sum())
    if n_eligible == 0:
        return default_attack, default_defence, 0
    team_attack = np.log((scored[eligible] + 0.5) / (expected_scored[eligible] + 0.5))
    team_defence = np.log((conceded[eligible] + 0.5) / (expected_conceded[eligible] + 0.5))
    weight = float(prior_weight)
    return (
        float((team_attack.sum() + weight * default_attack) / (n_eligible + weight)),
        float((team_defence.sum() + weight * default_defence) / (n_eligible + weight)),
        n_eligible,
    )


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def fit_goal_model(
    kind: str,
    train: pd.DataFrame,
    *,
    half_life_days: float,
    l2_strength: float = L2_STRENGTH,
    cache: FitCache | None = FIT_CACHE,
    max_iterations: int = 2_000,
) -> GoalFit:
    """Fit ``kind`` ("dixon_coles" or "nb2") on the training matches of one league.

    Served from ``cache`` when the identical training set was fitted before with
    the same league and parameters; otherwise fitted (warm-started from the
    previous solution of the same league/parameters, if any) and cached.
    """
    if kind not in ("dixon_coles", "nb2"):
        raise ValueError("kind must be 'dixon_coles' or 'nb2'")
    if not math.isfinite(half_life_days) or half_life_days <= 0.0:
        raise ValueError("half_life_days must be positive and finite")
    data = _training_arrays(train)
    key = (FIT_VERSION, kind, data.league, float(half_life_days), float(l2_strength), data.digest)
    if cache is not None:
        found = cache.get(key)
        if found is not None:
            return found
    warm_key = (kind, data.league, float(half_life_days), float(l2_strength))

    team_keys, inverse = np.unique(np.concatenate([data.home_keys, data.away_keys]), return_inverse=True)
    home_index = inverse[: data.n]
    away_index = inverse[data.n :]
    n_teams = len(team_keys)
    if data.n < _MIN_TRAIN_ROWS or n_teams < 2:
        fit = GoalFit(
            kind, team_keys, np.zeros(n_teams), np.zeros(n_teams), 0.0, 0.0, 0.0,
            NEWCOMER_PRIOR_ATTACK, NEWCOMER_PRIOR_DEFENCE, 0, data.n, False, 0,
        )
        if cache is not None:
            cache.put(key, fit)
        return fit
    weights = _decay_weights(data.kickoff_ns, half_life_days)

    start = None
    previous = cache.warm_start(warm_key) if cache is not None else None
    if previous is not None:
        previous_keys, (intercept, home_adv, shape, previous_attack, previous_defence), new_attack, new_defence = previous
        position = np.minimum(np.searchsorted(previous_keys, team_keys), len(previous_keys) - 1)
        known = previous_keys[position] == team_keys
        start = (
            intercept,
            home_adv,
            shape,
            np.where(known, previous_attack[position], new_attack),
            np.where(known, previous_defence[position], new_defence),
        )

    if kind == "dixon_coles":
        result = fit_dixon_coles_arrays(
            home_index, away_index, data.home_goals, data.away_goals, weights, n_teams,
            l2_strength=l2_strength, start=start, max_iterations=max_iterations,
        )
        shape = result.rho
    else:
        result = fit_negative_binomial_arrays(
            home_index, away_index, data.home_goals, data.away_goals, weights, n_teams,
            l2_strength=l2_strength, start=start, max_iterations=max_iterations,
        )
        shape = result.alpha
    prior_attack, prior_defence, n_newcomers = newcomer_prior(
        result.intercept, result.home_advantage, result.attack, result.defence,
        home_index, away_index, data.home_goals, data.away_goals, data.season,
    )
    fit = GoalFit(
        kind=kind,
        team_keys=team_keys,
        attack=result.attack,
        defence=result.defence,
        intercept=result.intercept,
        home_advantage=result.home_advantage,
        shape=float(shape),
        newcomer_attack=prior_attack,
        newcomer_defence=prior_defence,
        n_newcomer_teams=n_newcomers,
        n_matches=data.n,
        converged=bool(result.converged),
        n_iterations=int(result.n_iterations),
    )
    if cache is not None:
        cache.put(key, fit)
        cache.set_warm_start(warm_key, fit)
    return fit


# ---------------------------------------------------------------------------
# Score matrices and market probabilities
# ---------------------------------------------------------------------------


def poisson_pmf_table(rates: np.ndarray, max_goals: int) -> np.ndarray:
    """``(n, max_goals + 1)`` Poisson probabilities for each rate."""
    goals = np.arange(max_goals + 1, dtype=float)
    log_rates = np.log(rates)[:, None]
    return np.exp(goals[None, :] * log_rates - rates[:, None] - gammaln(goals + 1.0)[None, :])


def nb2_pmf_table(means: np.ndarray, alpha: float, max_goals: int) -> np.ndarray:
    """``(n, max_goals + 1)`` NB2 probabilities (``Var = mu + alpha mu^2``) for each mean.

    ``gamma(y + r) / gamma(r)`` is the product ``r (r + 1) ... (r + y - 1)``
    (exact for integer ``y``), which keeps the table accurate for tiny ``alpha``.
    """
    goals = np.arange(max_goals + 1, dtype=float)
    size = 1.0 / alpha
    table_log = np.concatenate(([0.0], np.cumsum(np.log(size + np.arange(max_goals, dtype=float)))))
    growth = np.log1p(alpha * means)[:, None]
    log_means = np.log(means)[:, None]
    log_pmf = (
        table_log[None, :]
        - size * growth
        + goals[None, :] * (log_means + math.log(alpha) - growth)
        - gammaln(goals + 1.0)[None, :]
    )
    return np.exp(log_pmf)


def dixon_coles_score_matrices(
    home_rate: np.ndarray, away_rate: np.ndarray, rho: float, max_goals: int = MAX_GOALS
) -> np.ndarray:
    """Normalised ``(n, G + 1, G + 1)`` score matrices (home goals on axis 1) with the low-score correction."""
    joint = poisson_pmf_table(home_rate, max_goals)[:, :, None] * poisson_pmf_table(away_rate, max_goals)[:, None, :]
    joint[:, 0, 0] *= np.maximum(1.0 - home_rate * away_rate * rho, _MIN_TAU)
    joint[:, 0, 1] *= np.maximum(1.0 + home_rate * rho, _MIN_TAU)
    joint[:, 1, 0] *= np.maximum(1.0 + away_rate * rho, _MIN_TAU)
    joint[:, 1, 1] *= max(1.0 - rho, _MIN_TAU)
    return joint / joint.sum(axis=(1, 2), keepdims=True)


def nb2_score_matrices(
    home_mean: np.ndarray, away_mean: np.ndarray, alpha: float, max_goals: int = MAX_GOALS
) -> np.ndarray:
    """Normalised ``(n, G + 1, G + 1)`` score matrices from independent NB2 margins."""
    joint = nb2_pmf_table(home_mean, alpha, max_goals)[:, :, None] * nb2_pmf_table(away_mean, alpha, max_goals)[:, None, :]
    return joint / joint.sum(axis=(1, 2), keepdims=True)


_OVER_UNDER_PREFIX = "over_under_"


@lru_cache(maxsize=None)
def _class_indicator(name: str, classes: tuple[str, ...], max_goals: int) -> np.ndarray:
    """``(K, (G + 1)^2)`` 0/1 matrix: which score cells belong to each class of the market."""
    goals = np.arange(max_goals + 1)
    home = goals[:, None]
    away = goals[None, :]
    if name == "1x2":
        rows = [home > away, home == away, home < away]
        expected = ("home", "draw", "away")
    elif name == "btts":
        rows = [(home == 0) | (away == 0), (home > 0) & (away > 0)]
        expected = ("no", "yes")
    elif name.startswith(_OVER_UNDER_PREFIX):
        line = float(name[len(_OVER_UNDER_PREFIX) :])
        total = home + away
        rows = [total < line, total > line]
        expected = ("under", "over")
    else:
        raise ValueError(f"the goal models cannot forecast the market {name!r}")
    if classes != expected:
        raise ValueError(f"market {name!r} has classes {classes}, expected {expected}")
    return np.stack([row.reshape(-1) for row in rows]).astype(float)


def matrix_market_probabilities(matrices: np.ndarray, market: Market) -> np.ndarray:
    """Class probabilities ``(n, K)`` of ``market`` from score matrices ``(n, G + 1, G + 1)``."""
    max_goals = matrices.shape[1] - 1
    indicator = _class_indicator(market.name, tuple(market.classes), max_goals)
    return matrices.reshape(len(matrices), -1) @ indicator.T


# ---------------------------------------------------------------------------
# Framework models
# ---------------------------------------------------------------------------


class GoalMarketModel:
    """A goal model that forecasts one market; the base of the two framework models."""

    kind = ""

    def __init__(
        self,
        market: Market,
        half_life_days: float,
        *,
        l2_strength: float = L2_STRENGTH,
        max_goals: int = MAX_GOALS,
        cache: FitCache | None = FIT_CACHE,
    ) -> None:
        # Fail early on a market the score matrix cannot express.
        _class_indicator(market.name, tuple(market.classes), max_goals)
        self.market = market
        self.classes = tuple(market.classes)
        self.half_life_days = float(half_life_days)
        self.l2_strength = float(l2_strength)
        self.max_goals = int(max_goals)
        self.cache = cache
        self.fitted: GoalFit | None = None

    def fit(self, train: pd.DataFrame) -> "GoalMarketModel":
        self.fitted = fit_goal_model(
            self.kind,
            train,
            half_life_days=self.half_life_days,
            l2_strength=self.l2_strength,
            cache=self.cache,
        )
        return self

    def _rates(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        fit = self._require_fit()
        home_keys, away_keys = _team_keys(frame)
        home_attack, home_defence, home_known = fit.strengths(home_keys)
        away_attack, away_defence, away_known = fit.strengths(away_keys)
        log_home = fit.intercept + fit.home_advantage + home_attack + away_defence
        log_away = fit.intercept + away_attack + home_defence
        return (
            np.exp(np.clip(log_home, -_LOG_RATE_CLIP, _LOG_RATE_CLIP)),
            np.exp(np.clip(log_away, -_LOG_RATE_CLIP, _LOG_RATE_CLIP)),
            ~(home_known & away_known),
        )

    def _require_fit(self) -> GoalFit:
        if self.fitted is None:
            raise RuntimeError("fit must be called before predict")
        return self.fitted

    def expected_goals(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Expected home and away goals per match (teams unseen in training get the newcomer prior)."""
        home, away, _ = self._rates(frame)
        return home, away

    def uses_newcomer_prior(self, frame: pd.DataFrame) -> np.ndarray:
        """True for matches in which at least one team was not in the training data."""
        return self._rates(frame)[2]

    def score_matrices(self, frame: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        fit = self._require_fit()
        if not fit.converged:
            # No usable fit: no forecast (never a made-up one).
            return np.full((len(frame), len(self.classes)), np.nan)
        return matrix_market_probabilities(self.score_matrices(frame), self.market)


class DixonColesGoalModel(GoalMarketModel):
    """Time-weighted Dixon-Coles (independent Poisson + low-score correction) for one market."""

    kind = "dixon_coles"

    def score_matrices(self, frame: pd.DataFrame) -> np.ndarray:
        fit = self._require_fit()
        home, away, _ = self._rates(frame)
        return dixon_coles_score_matrices(home, away, fit.shape, self.max_goals)


class NegativeBinomialGoalModel(GoalMarketModel):
    """Time-weighted NB2 (independent negative-binomial margins, shared dispersion) for one market."""

    kind = "nb2"

    def score_matrices(self, frame: pd.DataFrame) -> np.ndarray:
        fit = self._require_fit()
        home, away, _ = self._rates(frame)
        return nb2_score_matrices(home, away, fit.shape, self.max_goals)


def _spec(
    name: str,
    model_class: type[GoalMarketModel],
    market: Market,
    half_lives: Sequence[float],
    l2_strength: float,
    max_goals: int,
    cache: FitCache | None,
) -> ModelSpec:
    def factory(half_life_days: float) -> GoalMarketModel:
        return model_class(market, half_life_days, l2_strength=l2_strength, max_goals=max_goals, cache=cache)

    return ModelSpec(name, factory, param_grid={"half_life_days": [float(h) for h in half_lives]} if len(half_lives) > 1 else None)


def dixon_coles_spec(
    market: Market,
    *,
    half_lives: Sequence[float] = HALF_LIVES_DAYS,
    l2_strength: float = L2_STRENGTH,
    max_goals: int = MAX_GOALS,
    cache: FitCache | None = FIT_CACHE,
) -> ModelSpec:
    """Framework spec ``"dixon_coles"`` with the half-life grid (a single half-life means no selection)."""
    if len(half_lives) == 1:
        return ModelSpec(
            "dixon_coles",
            lambda: DixonColesGoalModel(market, half_lives[0], l2_strength=l2_strength, max_goals=max_goals, cache=cache),
        )
    return _spec("dixon_coles", DixonColesGoalModel, market, half_lives, l2_strength, max_goals, cache)


def nb2_spec(
    market: Market,
    *,
    half_lives: Sequence[float] = HALF_LIVES_DAYS,
    l2_strength: float = L2_STRENGTH,
    max_goals: int = MAX_GOALS,
    cache: FitCache | None = FIT_CACHE,
) -> ModelSpec:
    """Framework spec ``"nb2"`` with the half-life grid (a single half-life means no selection)."""
    if len(half_lives) == 1:
        return ModelSpec(
            "nb2",
            lambda: NegativeBinomialGoalModel(market, half_lives[0], l2_strength=l2_strength, max_goals=max_goals, cache=cache),
        )
    return _spec("nb2", NegativeBinomialGoalModel, market, half_lives, l2_strength, max_goals, cache)


SPEC_BUILDERS: dict[str, Callable[..., ModelSpec]] = {"dixon_coles": dixon_coles_spec, "nb2": nb2_spec}


# ---------------------------------------------------------------------------
# Filling the cache in parallel
# ---------------------------------------------------------------------------


def _warm_league(
    kind: str,
    league: str,
    league_matches: pd.DataFrame,
    half_lives: Sequence[float],
    seasons: Sequence[int],
    config: WalkForwardConfig,
    l2_strength: float,
    path: str,
) -> dict[str, Any]:
    """Run the framework's own walk-forward for one league and every half-life; save the fits.

    Uses :func:`evaluate_model` with a single half-life (no selection), so the
    matchdays, cut-offs and training sets are exactly the framework's.
    """
    started = time.perf_counter()
    cache = FitCache()
    warm_config = replace(config, test_seasons=tuple(seasons))
    for half_life in half_lives:
        spec = SPEC_BUILDERS[kind](MARKET_1X2, half_lives=[half_life], l2_strength=l2_strength, cache=cache)
        evaluate_model(spec, league_matches, MARKET_1X2, warm_config)
    cache.save(path)
    return {"kind": kind, "league": league, "seconds": time.perf_counter() - started, **cache.stats()}


def warm_fit_cache(
    matches: pd.DataFrame,
    kinds: Sequence[str],
    cache_dir: str | Path,
    *,
    config: WalkForwardConfig,
    seasons: Sequence[int],
    half_lives: Sequence[float] = HALF_LIVES_DAYS,
    l2_strength: float = L2_STRENGTH,
    n_jobs: int = 3,
    league_col: str = "league_code",
    leagues: Sequence[str] | None = None,
    log: Callable[[str], None] | None = print,
) -> list[dict[str, Any]]:
    """Fit every (model, league, half-life, matchday) of ``seasons`` in worker processes.

    One job per (model, league); each writes ``<cache_dir>/<kind>__<league>__<tag>.pkl``
    and is skipped when that file exists (so an interrupted run resumes).  The
    files are merged into a :class:`FitCache` with :func:`load_fit_cache`.  The
    evaluation itself stays sequential and unchanged: with the fits present it
    only pays for predictions, and any fit missing from the files is simply
    computed on demand, so this step can only save time, never change a result.
    """
    from joblib import Parallel, delayed

    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tag = hashlib.blake2b(
        repr((FIT_VERSION, tuple(half_lives), tuple(seasons), float(l2_strength))).encode(), digest_size=4
    ).hexdigest()
    counts = matches.groupby(league_col).size().sort_values(ascending=False)
    wanted = [str(name) for name in counts.index if leagues is None or str(name) in set(leagues)]
    jobs = []
    finished: list[dict[str, Any]] = []
    for league in wanted:
        for kind in kinds:
            path = directory / f"{kind}__{league}__{tag}.pkl"
            if path.exists():
                finished.append({"kind": kind, "league": league, "seconds": 0.0, "skipped": True})
                continue
            league_matches = matches[matches[league_col] == league]
            jobs.append(delayed(_warm_league)(kind, league, league_matches, list(half_lives), list(seasons), config, l2_strength, str(path)))
    if log:
        log(f"warm_fit_cache: {len(jobs)} jobs to run, {len(finished)} already on disk, {n_jobs} workers")
    if jobs:
        started = time.perf_counter()
        runner = Parallel(n_jobs=n_jobs, backend="loky", return_as="generator_unordered")
        for done, outcome in enumerate(runner(jobs), start=1):
            finished.append(outcome)
            if log:
                log(
                    f"  [{done}/{len(jobs)} {time.perf_counter() - started:7.0f}s] {outcome['kind']} {outcome['league']}: "
                    f"{outcome['fits']} fits in {outcome['seconds']:.0f}s"
                )
    return finished


def load_fit_cache(cache_dir: str | Path, cache: FitCache = FIT_CACHE) -> int:
    """Merge every ``*.pkl`` written by :func:`warm_fit_cache` into ``cache``; returns the fits added."""
    added = 0
    for path in sorted(Path(cache_dir).glob("*.pkl")):
        added += cache.load(path)
    return added


__all__ = [
    "FIT_CACHE",
    "FIT_VERSION",
    "HALF_LIVES_DAYS",
    "L2_STRENGTH",
    "MAX_GOALS",
    "NEWCOMER_PRIOR_ATTACK",
    "NEWCOMER_PRIOR_DEFENCE",
    "NEWCOMER_PRIOR_WEIGHT",
    "DixonColesGoalModel",
    "FitCache",
    "GoalFit",
    "GoalMarketModel",
    "NegativeBinomialGoalModel",
    "dixon_coles_score_matrices",
    "dixon_coles_spec",
    "fit_goal_model",
    "load_fit_cache",
    "matrix_market_probabilities",
    "nb2_pmf_table",
    "nb2_score_matrices",
    "nb2_spec",
    "newcomer_prior",
    "poisson_pmf_table",
    "warm_fit_cache",
]
