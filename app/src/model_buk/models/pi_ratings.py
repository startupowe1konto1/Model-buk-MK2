"""Pi-ratings (Constantinou & Fenton 2013): dynamic team ratings from goal differences.

Every team carries two ratings, one for home matches and one for away matches.
Both live on a goal-difference scale: a rating ``r`` stands for the goal
difference the team is expected to achieve against an average opponent,

    g(r) = sign(r) * (b ** (|r| / c) - 1),      b = 10, c = 3

and the expected goal difference of a match is ``g(home team's home rating) -
g(away team's away rating)``.  After the match the error ``e = |observed -
expected|`` is damped by ``psi(e) = c * log10(1 + e)`` (a 5-goal surprise is not
five times a 1-goal surprise) and signed towards the side that did better than
expected:

    home team:  R_H += lambda * psi_H,          R_A += gamma * (change of R_H)
    away team:  R_A += lambda * psi_A,          R_H += gamma * (change of R_A)
    psi_A = -psi_H

``lambda`` (learning rate) and ``gamma`` (how strongly a home result also moves
the team's away rating and vice versa) are the two parameters of the method;
the defaults are the values of the paper.  Ratings start at 0 (an average
team) and only ever depend on earlier matches, so a chronological pass
(:func:`pi_rating_pass`) yields the rating state after every match and the
state "as of" any cut-off is the state after the last match before it.

Nothing here reads outcomes of the match being forecast: ``expected_goal_difference``
takes ratings only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PiRatingConfig:
    """Parameters of the pi-rating update (defaults: Constantinou & Fenton 2013)."""

    learning_rate: float = 0.035  # lambda
    home_away_coupling: float = 0.7  # gamma
    base: float = 10.0  # b
    scale: float = 3.0  # c

    def __post_init__(self) -> None:
        if not (math.isfinite(self.learning_rate) and self.learning_rate > 0.0):
            raise ValueError("learning_rate must be positive and finite")
        if not (math.isfinite(self.home_away_coupling) and 0.0 <= self.home_away_coupling <= 1.0):
            raise ValueError("home_away_coupling must lie in [0, 1]")
        if not (math.isfinite(self.base) and self.base > 1.0):
            raise ValueError("base must be greater than 1")
        if not (math.isfinite(self.scale) and self.scale > 0.0):
            raise ValueError("scale must be positive and finite")


def rating_to_goal_difference(rating: float, config: PiRatingConfig) -> float:
    """Expected goal difference against an average opponent for one rating."""
    magnitude = config.base ** (abs(rating) / config.scale) - 1.0
    return magnitude if rating >= 0.0 else -magnitude


def expected_goal_difference(home_home_rating: float, away_away_rating: float, config: PiRatingConfig) -> float:
    """Expected goal difference of the home team: ``g(R_H(home)) - g(R_A(away))``."""
    return rating_to_goal_difference(home_home_rating, config) - rating_to_goal_difference(away_away_rating, config)


def weighted_error(error: float, config: PiRatingConfig) -> float:
    """Damped error ``psi(e) = c * log10(1 + e)`` (``e >= 0``)."""
    return config.scale * math.log10(1.0 + error)


def update_ratings(
    home_home: float,
    home_away: float,
    away_home: float,
    away_away: float,
    goal_difference: float,
    config: PiRatingConfig,
) -> tuple[float, float, float, float]:
    """Ratings after one match: ``(home team home, home team away, away team home, away team away)``.

    ``goal_difference`` is home goals minus away goals.  The expectation uses the
    ratings *before* the match; the four arguments are the pre-match ratings.
    """
    expected = expected_goal_difference(home_home, away_away, config)
    psi = weighted_error(abs(goal_difference - expected), config)
    psi_home = psi if goal_difference > expected else -psi
    psi_away = -psi_home
    new_home_home = home_home + psi_home * config.learning_rate
    new_home_away = home_away + (new_home_home - home_home) * config.home_away_coupling
    new_away_away = away_away + psi_away * config.learning_rate
    new_away_home = away_home + (new_away_away - away_away) * config.home_away_coupling
    return new_home_home, new_home_away, new_away_home, new_away_away


class PiRatings:
    """Incrementally updated pi-ratings keyed by any hashable team key (reference implementation)."""

    def __init__(self, config: PiRatingConfig | None = None) -> None:
        self.config = config or PiRatingConfig()
        self.home: dict = {}
        self.away: dict = {}
        self.matches: dict = {}

    def rating(self, team) -> tuple[float, float]:
        """``(home rating, away rating)``; an unseen team is average (0, 0)."""
        return self.home.get(team, 0.0), self.away.get(team, 0.0)

    def expected_goal_difference(self, home_team, away_team) -> float:
        return expected_goal_difference(self.home.get(home_team, 0.0), self.away.get(away_team, 0.0), self.config)

    def observe(self, home_team, away_team, home_goals: float, away_goals: float) -> None:
        """Update both teams from one finished match."""
        hh, ha = self.rating(home_team)
        ah, aa = self.rating(away_team)
        hh, ha, ah, aa = update_ratings(hh, ha, ah, aa, float(home_goals) - float(away_goals), self.config)
        self.home[home_team], self.away[home_team] = hh, ha
        self.home[away_team], self.away[away_team] = ah, aa
        self.matches[home_team] = self.matches.get(home_team, 0) + 1
        self.matches[away_team] = self.matches.get(away_team, 0) + 1


@dataclass(frozen=True)
class PiPass:
    """Rating state of both teams *after* every match of a chronological pass.

    Arrays are aligned with the matches passed to :func:`pi_rating_pass`.
    ``*_matches`` count the team's matches so far including this one.
    """

    home_home: np.ndarray
    home_away: np.ndarray
    away_home: np.ndarray
    away_away: np.ndarray
    home_matches: np.ndarray
    away_matches: np.ndarray


def pi_rating_pass(
    home_key: np.ndarray,
    away_key: np.ndarray,
    goal_difference: np.ndarray,
    n_keys: int,
    config: PiRatingConfig | None = None,
) -> PiPass:
    """One chronological pass over matches (row order = play order).

    ``home_key`` / ``away_key`` are integer team indices in ``[0, n_keys)``.  The
    state after row ``i`` depends only on rows ``0..i``.  A goal difference of
    NaN leaves the ratings unchanged (missing stays missing, never zero).
    """
    cfg = config or PiRatingConfig()
    n = len(home_key)
    home_home = np.zeros(n)
    home_away = np.zeros(n)
    away_home = np.zeros(n)
    away_away = np.zeros(n)
    home_matches = np.zeros(n, dtype=np.int64)
    away_matches = np.zeros(n, dtype=np.int64)
    rating_home = [0.0] * n_keys
    rating_away = [0.0] * n_keys
    played = [0] * n_keys
    hk = np.asarray(home_key, dtype=np.int64).tolist()
    ak = np.asarray(away_key, dtype=np.int64).tolist()
    gd = np.asarray(goal_difference, dtype=float).tolist()
    for i in range(n):
        h = hk[i]
        a = ak[i]
        diff = gd[i]
        if diff == diff:  # not NaN
            rating_home[h], rating_away[h], rating_home[a], rating_away[a] = update_ratings(
                rating_home[h], rating_away[h], rating_home[a], rating_away[a], diff, cfg
            )
            played[h] += 1
            played[a] += 1
        home_home[i] = rating_home[h]
        home_away[i] = rating_away[h]
        away_home[i] = rating_home[a]
        away_away[i] = rating_away[a]
        home_matches[i] = played[h]
        away_matches[i] = played[a]
    return PiPass(home_home, home_away, away_home, away_away, home_matches, away_matches)


__all__ = [
    "PiPass",
    "PiRatingConfig",
    "PiRatings",
    "expected_goal_difference",
    "pi_rating_pass",
    "rating_to_goal_difference",
    "update_ratings",
    "weighted_error",
]
