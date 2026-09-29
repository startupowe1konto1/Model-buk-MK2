"""Sequential Elo-Davidson probabilities for home/draw/away outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EloDavidsonConfig:
    k_factor: float = 20.0
    rating_scale: float = 400.0
    initial_rating: float = 1500.0
    home_advantage_points: float | None = None
    draw_parameter: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.k_factor) or self.k_factor <= 0.0:
            raise ValueError("k_factor must be positive and finite")
        if not math.isfinite(self.rating_scale) or self.rating_scale <= 0.0:
            raise ValueError("rating_scale must be positive and finite")
        if not math.isfinite(self.initial_rating):
            raise ValueError("initial_rating must be finite")
        if self.home_advantage_points is not None and not math.isfinite(
            self.home_advantage_points
        ):
            raise ValueError("home_advantage_points must be finite or None")
        if self.draw_parameter is not None and (
            not math.isfinite(self.draw_parameter) or self.draw_parameter <= 0.0
        ):
            raise ValueError("draw_parameter must be positive and finite or None")


@dataclass
class EloDavidsonModel:
    ratings: dict[str, float]
    config: EloDavidsonConfig
    home_advantage_points: float
    draw_parameter: float
    # Rating of a team the model has never seen (a promoted team); ``None`` means ``config.initial_rating``.
    new_team_rating: float | None = None

    @property
    def _unseen_rating(self) -> float:
        return self.config.initial_rating if self.new_team_rating is None else self.new_team_rating

    @property
    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(self.ratings))

    def probabilities(self, home_team: str, away_team: str) -> tuple[float, float, float]:
        home_rating = self.ratings.get(home_team, self._unseen_rating)
        away_rating = self.ratings.get(away_team, self._unseen_rating)
        home_strength = 10.0 ** (
            (home_rating + self.home_advantage_points) / self.config.rating_scale
        )
        away_strength = 10.0 ** (away_rating / self.config.rating_scale)
        draw_strength = self.draw_parameter * math.sqrt(home_strength * away_strength)
        total = home_strength + draw_strength + away_strength
        return (
            home_strength / total,
            draw_strength / total,
            away_strength / total,
        )

    def observe_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> tuple[float, float, float]:
        if home_goals < 0 or away_goals < 0:
            raise ValueError("goals must be non-negative")
        probabilities = self.probabilities(home_team, away_team)
        self.ratings.setdefault(home_team, self._unseen_rating)
        self.ratings.setdefault(away_team, self._unseen_rating)
        actual_score = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
        expected_score = probabilities[0] + 0.5 * probabilities[1]
        change = self.config.k_factor * (actual_score - expected_score)
        self.ratings[home_team] += change
        self.ratings[away_team] -= change
        return probabilities


def _estimated_home_advantage(frame: pd.DataFrame, rating_scale: float) -> float:
    home_wins = float((frame["home_goals"] > frame["away_goals"]).sum()) + 0.5
    away_wins = float((frame["home_goals"] < frame["away_goals"]).sum()) + 0.5
    estimate = rating_scale * math.log10(home_wins / away_wins)
    return float(max(-200.0, min(200.0, estimate)))


def _estimated_draw_parameter(frame: pd.DataFrame) -> float:
    draw_rate = (float((frame["home_goals"] == frame["away_goals"]).sum()) + 1.0) / (
        len(frame) + 2.0
    )
    estimate = 2.0 * draw_rate / max(1.0 - draw_rate, 1e-9)
    return float(max(0.1, min(2.0, estimate)))


def fit_elo_davidson(
    matches: pd.DataFrame,
    *,
    config: EloDavidsonConfig | None = None,
) -> EloDavidsonModel:
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    frame = matches.loc[:, sorted(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", format="mixed")
    frame["home_goals"] = pd.to_numeric(frame["home_goals"], errors="coerce")
    frame["away_goals"] = pd.to_numeric(frame["away_goals"], errors="coerce")
    if frame[list(required)].isna().any().any():
        raise ValueError("matches contains missing or invalid required values")
    if (frame[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("goals must be non-negative")

    resolved = config or EloDavidsonConfig()
    teams = sorted(set(frame["home_team"].astype(str)) | set(frame["away_team"].astype(str)))
    model = EloDavidsonModel(
        ratings={team: resolved.initial_rating for team in teams},
        config=resolved,
        home_advantage_points=(
            resolved.home_advantage_points
            if resolved.home_advantage_points is not None
            else _estimated_home_advantage(frame, resolved.rating_scale)
        ),
        draw_parameter=(
            resolved.draw_parameter
            if resolved.draw_parameter is not None
            else _estimated_draw_parameter(frame)
        ),
    )
    for _, simultaneous in frame.sort_values(
        ["date", "home_team", "away_team"]
    ).groupby("date", sort=True):
        pending = list(simultaneous.itertuples(index=False))
        for row in pending:
            model.observe_match(
                str(row.home_team),
                str(row.away_team),
                int(row.home_goals),
                int(row.away_goals),
            )
    return model


# ---------------------------------------------------------------------------
# Fast sequential fit with maximum-likelihood home advantage and draw parameter
# ---------------------------------------------------------------------------
#
# ``fit_elo_davidson`` above is the reference implementation (plain Python
# objects, closed-form crude estimates of the home advantage and draw
# parameter).  The functions below run the same rating recursion on plain
# lists, estimate the two nuisance parameters by maximum likelihood on the
# training matches and give teams that are new to the league a documented
# prior rating.  Nothing here looks past the last training match.
#
# Davidson probabilities depend only on the rating difference
# D = r_home + H - r_away:  with t = 10 ** (D / (2 * scale)),
#
#   P(home) = t / Z,   P(draw) = nu / Z,   P(away) = (1 / t) / Z,   Z = t + nu + 1 / t.
#
# H and nu are not fitted in one go with the ratings (the ratings depend on
# them through the recursion).  Instead the recursion is run, its *pre-match*
# rating differences are recorded and (H, log nu) maximise the Davidson
# likelihood of those differences (a concave two-parameter problem, solved by
# Newton's method); this is repeated a fixed number of times, so the result is a
# deterministic function of the training matches.  The likelihood counted is the
# filtered one: each match is scored with the ratings from before its result.
#
# Prior for a team that enters the league after its first observed season (a
# promoted team): rating = mean rating of the teams that played in the league in
# the entrant's season or the one before, plus a shift ``entrant_shift`` (rating
# points, negative: promoted teams are weaker than the league average).  The
# shift is chosen on a small grid by the same filtered likelihood (plus the
# optional Gaussian prior ``entrant_prior``) and refined by a parabola through
# the best three grid points.  Teams that appear for the
# first time in the forecast frame get the same prior.

_LOG10 = math.log(10.0)
ENTRANT_SHIFT_GRID = (-160.0, -120.0, -80.0, -40.0, 0.0, 40.0)
# Gaussian prior (mean, sd) on the entrant shift in rating points.  The mean is
# read off fits on matches before 2022-08-01 (before the first test season): the
# median maximum-likelihood shift of a promoted team was about -65 points.  The
# sd is about the spread of that estimate across leagues; on a few entrants it
# keeps the shift from running to a grid edge.
ENTRANT_SHIFT_PRIOR = (-60.0, 40.0)
MIN_REFERENCE_TEAMS = 6


@dataclass(frozen=True)
class EloFit:
    """Fitted sequential Elo-Davidson state at the end of the training matches."""

    ratings: np.ndarray  # rating per team index (NaN: never seen)
    last_season: np.ndarray  # season of the last match per team (-1: never seen)
    home_advantage_points: float
    draw_parameter: float
    entrant_shift: float
    k_factor: float
    rating_scale: float
    initial_rating: float
    log_likelihood: float  # filtered Davidson log-likelihood of the training matches
    n_matches: int
    team_keys: tuple = ()

    def reference_rating(self, season: int) -> float:
        """Prior rating of a team new to the league in ``season``."""
        return _reference_level(self.ratings, self.last_season, season, self.initial_rating) + self.entrant_shift

    def probabilities(self, home: np.ndarray, away: np.ndarray, season: np.ndarray) -> np.ndarray:
        """(n, 3) home/draw/away probabilities; team index ``-1`` means a team the fit has not seen."""
        home = np.asarray(home)
        away = np.asarray(away)
        season = np.asarray(season)
        ratings = []
        for indices in (home, away):
            values = np.empty(len(indices))
            known = indices >= 0
            values[known] = self.ratings[indices[known]]
            for position in np.flatnonzero(~known):
                values[position] = self.reference_rating(int(season[position]))
            ratings.append(values)
        return davidson_probabilities(
            ratings[0] + self.home_advantage_points - ratings[1], self.draw_parameter, self.rating_scale
        )

    def to_model(self, config: EloDavidsonConfig | None = None) -> "EloDavidsonModel":
        keys = self.team_keys or tuple(range(len(self.ratings)))
        resolved = config or EloDavidsonConfig(
            k_factor=self.k_factor,
            rating_scale=self.rating_scale,
            initial_rating=self.initial_rating,
        )
        seen = ~np.isnan(self.ratings)
        return EloDavidsonModel(
            ratings={str(key): float(rating) for key, rating, ok in zip(keys, self.ratings, seen) if ok},
            config=resolved,
            home_advantage_points=self.home_advantage_points,
            draw_parameter=self.draw_parameter,
            new_team_rating=(
                self.reference_rating(int(self.last_season.max()) + 1) if seen.any() else None
            ),
        )


def davidson_probabilities(difference: np.ndarray, draw_parameter: float, rating_scale: float = 400.0) -> np.ndarray:
    """Home/draw/away probabilities for rating differences ``r_home + H - r_away`` (vectorised)."""
    t = np.exp(np.asarray(difference, dtype=float) * (_LOG10 / (2.0 * rating_scale)))
    inverse = 1.0 / t
    total = t + draw_parameter + inverse
    return np.column_stack([t / total, np.full(t.shape, draw_parameter) / total, inverse / total])


def _reference_level(
    ratings: np.ndarray, last_season: np.ndarray, season: int, default: float
) -> float:
    """Mean rating of the teams that played in ``season`` or the season before."""
    active = last_season >= season - 1
    if int(active.sum()) < MIN_REFERENCE_TEAMS:
        return default
    return float(ratings[active].mean())


def davidson_mle(
    difference: np.ndarray,
    outcome: np.ndarray,
    rating_scale: float,
    *,
    start: tuple[float, float] = (60.0, 0.6),
    iterations: int = 12,
) -> tuple[float, float]:
    """Maximum-likelihood home advantage (rating points) and draw parameter nu given rating differences.

    ``difference`` is ``r_home - r_away`` before the match, ``outcome`` 0/1/2 for home win/draw/away win.
    The log-likelihood is concave in ``(H, log nu)``; damped Newton steps converge in a handful of iterations.
    A tiny ridge keeps the Hessian invertible on very short histories.
    """
    q = _LOG10 / rating_scale
    difference = np.asarray(difference, dtype=float)
    s_y = 0.5 - np.asarray(outcome, dtype=float) * 0.5  # +1/2 home win, 0 draw, -1/2 away win
    is_draw = (np.asarray(outcome) == 1).astype(float)
    n = len(difference)
    home_adv, log_nu = float(start[0]), math.log(float(start[1]))

    def log_likelihood(h: float, w: float) -> float:
        x = q * (difference + h)
        m = np.maximum(np.maximum(0.5 * x, -0.5 * x), w)
        log_z = m + np.log(np.exp(0.5 * x - m) + np.exp(w - m) + np.exp(-0.5 * x - m))
        return float(np.sum(s_y * x + w * is_draw - log_z))

    current = log_likelihood(home_adv, log_nu)
    for _ in range(iterations):
        x = q * (difference + home_adv)
        a = np.exp(0.5 * x)
        c = 1.0 / a
        b = math.exp(log_nu)
        z = a + b + c
        p_h, p_d, p_a = a / z, b / z, c / z
        mean_s = 0.5 * (p_h - p_a)
        var_s = 0.25 * (p_h + p_a) - mean_s**2
        g_x = s_y - mean_s
        g_w = is_draw - p_d
        grad = np.array([q * g_x.sum(), g_w.sum()])
        h_xx = -var_s.sum() * q * q
        h_xw = q * (mean_s * p_d).sum()
        h_ww = -(p_d * (1.0 - p_d)).sum()
        hessian = np.array([[h_xx, h_xw], [h_xw, h_ww]]) - 1e-6 * n * np.eye(2)
        try:
            step = -np.linalg.solve(hessian, grad)
        except np.linalg.LinAlgError:
            break
        scale = 1.0
        improved = False
        for _ in range(12):
            candidate_h = home_adv + scale * step[0]
            candidate_w = log_nu + scale * step[1]
            candidate = log_likelihood(candidate_h, candidate_w)
            if math.isfinite(candidate) and candidate >= current - 1e-12:
                improved = True
                break
            scale *= 0.5
        if not improved:
            break
        moved = abs(candidate_h - home_adv) + abs(candidate_w - log_nu)
        home_adv, log_nu, current = candidate_h, candidate_w, candidate
        if moved < 1e-7:
            break
    home_adv = float(min(max(home_adv, -150.0), 300.0))
    nu = float(min(max(math.exp(log_nu), 0.2), 3.0))
    return home_adv, nu


def _elo_pass(
    home: list[int],
    away: list[int],
    outcome_score: list[float],
    season: list[int],
    is_entrant: list[bool],
    *,
    n_teams: int,
    k_factor: float,
    rating_scale: float,
    initial_rating: float,
    home_advantage_points: float,
    draw_parameter: float,
    entrant_shift: float,
):
    """One chronological pass; returns (pre-match rating differences r_h - r_a, ratings, last seasons)."""
    nan = float("nan")
    ratings = [initial_rating] * n_teams
    last_season = [-1] * n_teams
    seen = [not flag for flag in is_entrant]  # founders start at the initial rating, entrants on first sight
    for team, flag in enumerate(is_entrant):
        if flag:
            ratings[team] = nan
    half_c = _LOG10 / (2.0 * rating_scale)
    nu = draw_parameter
    half_nu = 0.5 * nu
    exp = math.exp
    differences: list[float] = []
    push = differences.append
    for h, a, score, s in zip(home, away, outcome_score, season):
        if not seen[h] or not seen[a]:
            for team in (h, a):
                if not seen[team]:
                    seen[team] = True
                    ratings[team] = _entry_rating(ratings, last_season, seen, s, initial_rating) + entrant_shift
        rating_h = ratings[h]
        rating_a = ratings[a]
        difference = rating_h - rating_a
        push(difference)
        t = exp((difference + home_advantage_points) * half_c)
        expected = (t + half_nu) / (t + nu + 1.0 / t)
        change = k_factor * (score - expected)
        ratings[h] = rating_h + change
        ratings[a] = rating_a - change
        last_season[h] = s
        last_season[a] = s
    unseen = [not flag for flag in seen]
    final = np.array(ratings, dtype=float)
    final[unseen] = np.nan
    return np.array(differences), final, np.array(last_season, dtype=np.int64)


def _entry_rating(ratings: list[float], last_season: list[int], seen: list[bool], season: int, default: float) -> float:
    """Mean rating of the seen teams that played in ``season`` or the season before (else ``default``)."""
    total = 0.0
    count = 0
    limit = season - 1
    for team, last in enumerate(last_season):
        if last >= limit and seen[team]:
            total += ratings[team]
            count += 1
    if count < MIN_REFERENCE_TEAMS:
        return default
    return total / count


def _filtered_log_likelihood(
    differences: np.ndarray, outcome: np.ndarray, home_adv: float, nu: float, rating_scale: float
) -> float:
    probabilities = davidson_probabilities(differences + home_adv, nu, rating_scale)
    return float(np.sum(np.log(probabilities[np.arange(len(outcome)), outcome])))


def fit_elo_arrays(
    home: Sequence[int],
    away: Sequence[int],
    outcome: Sequence[int],
    season: Sequence[int],
    *,
    n_teams: int,
    k_factor: float,
    rating_scale: float = 400.0,
    initial_rating: float = 1500.0,
    entrants: Sequence[int] = (),
    team_keys: tuple = (),
    alternations: int = 2,
    entrant_grid: Sequence[float] = ENTRANT_SHIFT_GRID,
    entrant_prior: tuple[float, float] | None = None,
) -> EloFit:
    """Run Elo-Davidson through the training matches (chronological order) and estimate its nuisance parameters.

    ``outcome`` is 0/1/2 (home win/draw/away win), ``season`` the season label per match, ``entrants``
    the teams that first play after the first season of the data.  ``entrant_prior`` is an optional
    Gaussian prior ``(mean, sd)`` on the entrant shift.  The whole fit is a deterministic function of
    the training arrays.
    """
    if k_factor <= 0:
        raise ValueError("k_factor must be positive")
    n = len(home)
    if n == 0:
        raise ValueError("no matches to fit")
    outcome_array = np.asarray(outcome, dtype=np.int64)
    score = (1.0 - outcome_array * 0.5).tolist()  # 1, 0.5, 0
    home_list = [int(v) for v in home]
    away_list = [int(v) for v in away]
    season_list = [int(v) for v in season]
    is_entrant = [False] * n_teams
    for team in entrants:
        is_entrant[int(team)] = True

    def run(h_adv: float, nu: float, shift: float):
        return _elo_pass(
            home_list,
            away_list,
            score,
            season_list,
            is_entrant,
            n_teams=n_teams,
            k_factor=float(k_factor),
            rating_scale=float(rating_scale),
            initial_rating=float(initial_rating),
            home_advantage_points=h_adv,
            draw_parameter=nu,
            entrant_shift=shift,
        )

    home_wins = float(np.sum(outcome_array == 0)) + 0.5
    away_wins = float(np.sum(outcome_array == 2)) + 0.5
    h_adv = rating_scale * math.log10(home_wins / away_wins)
    draws = (float(np.sum(outcome_array == 1)) + 1.0) / (n + 2.0)
    nu = float(min(max(2.0 * draws / max(1.0 - draws, 1e-9), 0.1), 2.0))
    shift = 0.0
    grid = tuple(float(v) for v in entrant_grid)

    def refit_nuisance(differences: np.ndarray) -> None:
        nonlocal h_adv, nu
        h_adv, nu = davidson_mle(differences, outcome_array, rating_scale, start=(h_adv, nu))

    for _ in range(alternations):
        differences, _, _ = run(h_adv, nu, shift)
        refit_nuisance(differences)
    if entrants and len(grid) == 1:
        shift = grid[0]
    elif entrants:
        scores = []
        for candidate in grid:
            differences, _, _ = run(h_adv, nu, candidate)
            score_value = _filtered_log_likelihood(differences, outcome_array, h_adv, nu, rating_scale)
            if entrant_prior is not None:  # Gaussian prior on the shift: a penalty in log-likelihood units
                score_value -= 0.5 * ((candidate - entrant_prior[0]) / entrant_prior[1]) ** 2
            scores.append(score_value)
        best = int(np.argmax(scores))
        shift = grid[best]
        if 0 < best < len(scores) - 1:
            left, mid, right = scores[best - 1], scores[best], scores[best + 1]
            curvature = left - 2.0 * mid + right
            if curvature < 0:
                spacing = grid[best + 1] - grid[best]
                shift += float(np.clip(0.5 * (left - right) / curvature, -1.0, 1.0)) * spacing
        differences, _, _ = run(h_adv, nu, shift)
        refit_nuisance(differences)
    differences, ratings, last_season = run(h_adv, nu, shift)
    return EloFit(
        ratings=ratings,
        last_season=last_season,
        home_advantage_points=float(h_adv),
        draw_parameter=float(nu),
        entrant_shift=float(shift),
        k_factor=float(k_factor),
        rating_scale=float(rating_scale),
        initial_rating=float(initial_rating),
        log_likelihood=_filtered_log_likelihood(differences, outcome_array, h_adv, nu, rating_scale),
        n_matches=n,
        team_keys=tuple(team_keys),
    )


__all__ = [
    "ENTRANT_SHIFT_GRID",
    "ENTRANT_SHIFT_PRIOR",
    "EloDavidsonConfig",
    "EloDavidsonModel",
    "EloFit",
    "davidson_mle",
    "davidson_probabilities",
    "fit_elo_arrays",
    "fit_elo_davidson",
]
