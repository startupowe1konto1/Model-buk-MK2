"""Half-split goal model: the joint distribution of first- and second-half goals of a fixture.

Markets such as half time / full time, win both halves, to score in both halves, highest scoring half and
first / last team to score depend on *when* the goals fall, which the full-time score matrix of
:mod:`model_buk.scanner.fair` does not say.  This module splits the full-time matrix into halves.

Model
-----
Every goal of the home team falls in the first half with probability ``s_home``, independently of the other
goals (binomial thinning), and every away goal with ``s_away``::

    P(1H = (a, b), 2H = (c, d)) = M[a + c, b + d] * Bin(a; a + c, s_home) * Bin(b; b + d, s_away)

``M`` is the full-time matrix fitted to Pinnacle (:func:`model_buk.scanner.fair.fit_score_matrix`).  For a
Poisson full-time matrix this is exactly the model of independent halves with Poisson rates ``s * mu`` in the
first half and ``(1 - s) * mu`` in the second, so the first- and second-half rates add up to the full-time rates
(:meth:`HalfSplit.rates`).  For the Dixon-Coles matrix the construction keeps the full-time distribution exactly:
every full-time market of the joint equals the fitted matrix, and the half-time / full-time marginals are the
model's first-half and full-time results.

The shares, in order of preference (``HalfSplit.source``):

1. ``pinnacle_1h``: calibrated to Pinnacle's first-half prices of the fixture (1H 1X2, totals, Asian handicaps,
   team totals).  Least squares of the thinned first-half matrix against the de-vigged prices, with a weak pull
   (:data:`PRIOR_WEIGHT` per share) towards the league shares, so that a lone first-half total (which fixes the
   sum of the two rates, not their split) cannot produce an odd split.
2. ``league``: the share of goals scored before half time in the fixture's league, over the finished matches of
   the last :data:`DEFAULT_WINDOW_YEARS` years before the cutoff (``history.sqlite``, ``ht_home_goals`` /
   ``ht_away_goals``), shrunk towards the global share with an empirical-Bayes weight (:func:`shares_from_matches`).
   Only matches that kicked off strictly before the cutoff are read.
3. ``global``: the global share of the same window (a league with no history), or :data:`DEFAULT_SHARES` when no
   history is given at all.

First and last goal
-------------------
Given the goal counts of a half, the order of its goals is random (a Poisson process with constant rates within
the half, or any exchangeable split): the first goal of a half with ``a`` home and ``b`` away goals is the home
team's with probability ``a / (a + b)``.  The first goal of the match is the first goal of the first half, or of
the second half when the first ended goalless; "no goal" is ``M[0, 0]``.  The last goal mirrors it.

Measured (``app/scripts/measure_half_model.py``, see ``docs/SKANER.md``): the first-half share of goals is 0.435 to
0.45 by year (2015-2026, 211 630 matches of 89 leagues; it has risen by about one point since 2021, hence the
window), and 0.39 to 0.48 by league.  The calibration of the model on history is in ``docs/SKANER.md``.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.special import comb

DEFAULT_SHARES: Final[tuple[float, float]] = (0.447, 0.442)
"""Global first-half share of home and away goals (all leagues of ``history.sqlite``, 2022-09 to 2026-09); the
fallback when no history is given."""
DEFAULT_WINDOW_YEARS: Final = 4.0
"""Only the last four years before the cutoff estimate the shares (the share has drifted up since 2021)."""
SHARE_BOUNDS: Final[tuple[float, float]] = (0.2, 0.7)
PRIOR_WEIGHT: Final = 0.3
"""Weight of ``s - s_league`` against the first-half price residuals (in probability) in the calibration."""
MIN_PRIOR_STRENGTH: Final = 200.0
MAX_PRIOR_STRENGTH: Final = 20000.0
"""Bounds of the empirical-Bayes pseudo-count (goals) that pulls a league's share to the global one."""

_GRID: Final = 16

HALF_SOURCES: Final = ("pinnacle_1h", "league", "global")


# --------------------------------------------------------------------------- #
# League shares from history
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HalfShares:
    """First-half shares of home and away goals: global and per league (already shrunk).

    ``cutoff`` is the time before which the matches were read (None for the built-in default);
    ``prior_strength`` the empirical-Bayes pseudo-count in goals; ``n_matches`` per league.
    """

    global_home: float = DEFAULT_SHARES[0]
    global_away: float = DEFAULT_SHARES[1]
    leagues: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    n_matches: Mapping[int, int] = field(default_factory=dict)
    cutoff: pd.Timestamp | None = None
    prior_strength: float = float("nan")
    window_years: float | None = None

    def for_league(self, league_id: object) -> tuple[float, float, str]:
        """``(s_home, s_away, source)`` of a league: its shrunk shares, else the global ones."""
        try:
            key = int(league_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            key = None
        if key is not None and key in self.leagues:
            home, away = self.leagues[key]
            return float(home), float(away), "league"
        return float(self.global_home), float(self.global_away), "global"


DEFAULT_HALF_SHARES: Final = HalfShares()


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def shares_from_matches(
    matches: pd.DataFrame,
    cutoff: object,
    *,
    window_years: float | None = DEFAULT_WINDOW_YEARS,
    prior_strength: float | None = None,
) -> HalfShares:
    """League and global first-half shares from finished matches that kicked off strictly before ``cutoff``.

    ``matches`` needs ``league_id, kickoff_utc, home_goals, away_goals, ht_home_goals, ht_away_goals`` (rows with
    a missing score or a half-time score above the final one are ignored).  With ``window_years`` only matches
    of that many years before the cutoff count.  A league's raw share ``G1 / G`` (first-half goals over goals) is
    shrunk to the global share ``g``: ``(G1 + k * g) / (G + k)``; the pseudo-count ``k`` is
    ``g * (1 - g) / tau2`` with ``tau2`` the between-league variance of the raw shares beyond their binomial noise
    (method of moments, pooled over home and away goals), clipped to [200, 20000] goals, unless given.
    """
    stamp = _utc(cutoff)
    needed = ["league_id", "kickoff_utc", "home_goals", "away_goals", "ht_home_goals", "ht_away_goals"]
    missing = [c for c in needed if c not in matches.columns]
    if missing:
        raise ValueError(f"matches need the columns: {', '.join(missing)}")
    frame = matches[needed].copy()
    kickoff = pd.to_datetime(frame["kickoff_utc"], utc=True, errors="coerce", format="ISO8601")
    keep = kickoff.notna() & (kickoff < stamp)
    if window_years is not None:
        keep &= kickoff >= stamp - pd.Timedelta(days=365.25 * float(window_years))
    goals = frame[needed[2:]].apply(pd.to_numeric, errors="coerce")
    keep &= goals.notna().all(axis=1)
    keep &= (goals["ht_home_goals"] <= goals["home_goals"]) & (goals["ht_away_goals"] <= goals["away_goals"])
    keep &= (goals >= 0).all(axis=1)
    frame = frame[keep.to_numpy()]
    goals = goals[keep.to_numpy()]
    if frame.empty or goals["home_goals"].sum() <= 0 or goals["away_goals"].sum() <= 0:
        return HalfShares(cutoff=stamp, window_years=window_years)
    goals = goals.assign(league_id=pd.to_numeric(frame["league_id"], errors="coerce"))
    goals = goals[goals["league_id"].notna()]
    table = goals.groupby("league_id").agg(
        g_home=("home_goals", "sum"), g_away=("away_goals", "sum"), h_home=("ht_home_goals", "sum"),
        h_away=("ht_away_goals", "sum"), n=("home_goals", "size"),
    )  # fmt: skip
    g_home = float(table["h_home"].sum() / table["g_home"].sum())
    g_away = float(table["h_away"].sum() / table["g_away"].sum())
    if prior_strength is None:
        excess = []
        for share, first, total in ((g_home, "h_home", "g_home"), (g_away, "h_away", "g_away")):
            rows = table[table[total] > 0]
            raw = rows[first] / rows[total]
            weight = rows[total] / rows[total].sum()
            observed = float((weight * (raw - share) ** 2).sum())
            noise = float((weight * share * (1.0 - share) / rows[total]).sum())
            excess.append((observed - noise, share))
        tau2 = float(np.mean([e for e, _ in excess]))
        base = float(np.mean([s * (1.0 - s) for _, s in excess]))
        k = base / tau2 if tau2 > 0 else MAX_PRIOR_STRENGTH
        prior_strength = float(np.clip(k, MIN_PRIOR_STRENGTH, MAX_PRIOR_STRENGTH))
    k = float(prior_strength)
    leagues: dict[int, tuple[float, float]] = {}
    counts: dict[int, int] = {}
    for league_id, row in table.iterrows():
        home = (row["h_home"] + k * g_home) / (row["g_home"] + k)
        away = (row["h_away"] + k * g_away) / (row["g_away"] + k)
        leagues[int(league_id)] = (float(home), float(away))
        counts[int(league_id)] = int(row["n"])
    return HalfShares(g_home, g_away, leagues, counts, stamp, k, window_years)


def load_history_matches(history_db: str | Path, cutoff: object, *, window_years: float | None = DEFAULT_WINDOW_YEARS) -> pd.DataFrame:
    """Finished matches of ``history.sqlite`` that kicked off strictly before ``cutoff`` (and within the window)."""
    stamp = _utc(cutoff)
    start = None if window_years is None else stamp - pd.Timedelta(days=365.25 * float(window_years))
    sql = (
        "SELECT league_id, kickoff_utc, home_goals, away_goals, ht_home_goals, ht_away_goals FROM matches "
        "WHERE status_short = 'FT' AND kickoff_utc < :cutoff AND (:start IS NULL OR kickoff_utc >= :start)"
    )
    params = {"cutoff": stamp.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "start": None if start is None else start.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
    path = Path(history_db)
    uri = "file:" + str(path.resolve()).replace("\\", "/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as connection:
        return pd.read_sql_query(sql, connection, params=params)


def estimate_half_shares(history_db: str | Path, cutoff: object, *, window_years: float | None = DEFAULT_WINDOW_YEARS) -> HalfShares:
    """:func:`shares_from_matches` of the matches of ``history_db`` before ``cutoff``; the default shares when the
    database is missing or unreadable (the result's ``cutoff`` is then None)."""
    if not Path(history_db).exists():
        return DEFAULT_HALF_SHARES
    try:
        matches = load_history_matches(history_db, cutoff, window_years=window_years)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return DEFAULT_HALF_SHARES
    return shares_from_matches(matches, cutoff, window_years=window_years)


# --------------------------------------------------------------------------- #
# Thinning a full-time matrix
# --------------------------------------------------------------------------- #


def _binomial_table(share: float, size: int = _GRID) -> np.ndarray:
    """``B[x, a] = Bin(a; x, share)``: probability that ``a`` of ``x`` goals fall in the first half."""
    x = np.arange(size)[:, None]
    a = np.arange(size)[None, :]
    with np.errstate(invalid="ignore"):
        table = comb(x, a) * float(share) ** a * (1.0 - float(share)) ** np.clip(x - a, 0, None)
    return np.where(a <= x, table, 0.0)


def first_half_matrix(full_time: np.ndarray, s_home: float, s_away: float) -> np.ndarray:
    """First-half score matrix of the thinned full-time matrix: ``B_home^T M B_away``."""
    size = full_time.shape[0]
    return _binomial_table(s_home, size).T @ full_time @ _binomial_table(s_away, size)


def second_half_matrix(full_time: np.ndarray, s_home: float, s_away: float) -> np.ndarray:
    """Second-half score matrix: the same thinning with the second-half shares ``1 - s``."""
    return first_half_matrix(full_time, 1.0 - float(s_home), 1.0 - float(s_away))


def joint_matrix(full_time: np.ndarray, s_home: float, s_away: float) -> np.ndarray:
    """``J[a, b, c, d]`` = P(first half a:b, second half c:d) (see the module docstring); sums to one."""
    size = full_time.shape[0]
    idx = np.arange(size)
    total = idx[:, None] + idx[None, :]  # [first-half goals, second-half goals] -> full-time goals
    valid = total < size
    clipped = np.where(valid, total, 0)

    def weights(share: float) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            w = comb(total, idx[:, None]) * float(share) ** idx[:, None] * (1.0 - float(share)) ** idx[None, :]
        return np.where(valid, w, 0.0)

    w_home = weights(s_home)
    w_away = weights(s_away)
    cells = full_time[clipped[:, None, :, None], clipped[None, :, None, :]]
    joint = cells * w_home[:, None, :, None] * w_away[None, :, None, :] * (valid[:, None, :, None] & valid[None, :, None, :])
    total_mass = joint.sum()
    return joint / total_mass if total_mass > 0 else joint


# --------------------------------------------------------------------------- #
# Calibration to Pinnacle's first-half prices
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ShareFit:
    s_home: float
    s_away: float
    rmse: float
    n_targets: int


def calibrate_shares(
    full_time: np.ndarray,
    targets: Sequence[object],
    prior: tuple[float, float],
    *,
    prior_weight: float = PRIOR_WEIGHT,
) -> ShareFit | None:
    """Shares whose thinned first-half matrix reproduces the de-vigged first-half prices ``targets``.

    ``targets`` are :class:`model_buk.scanner.fair.FitTarget` of the first half (1X2, totals, handicaps, team
    totals); ``prior`` the league shares the fit is weakly pulled towards.  None when no target can be used.
    ``rmse`` is over the price residuals only.
    """
    from model_buk.scanner.fair import leg_masks

    size = full_time.shape[0]
    rows = []
    for target in targets:
        q = float(getattr(target, "q"))
        if not (math.isfinite(q) and 0.0 < q < 1.0):
            continue
        line = float(getattr(target, "line"))
        masks = leg_masks(str(getattr(target, "market")), str(getattr(target, "selection")), line if math.isfinite(line) else float("nan"), size)
        if masks is None:
            continue
        rows.append((masks[0].ravel(), masks[1].ravel(), q, float(getattr(target, "weight", 1.0))))
    if not rows:
        return None
    numerator = np.vstack([r[0] for r in rows])
    denominator = np.vstack([r[1] for r in rows])
    q = np.array([r[2] for r in rows])
    weight = np.array([r[3] for r in rows])
    lo, hi = SHARE_BOUNDS
    prior_home = float(np.clip(prior[0], lo, hi))
    prior_away = float(np.clip(prior[1], lo, hi))

    def model(x: np.ndarray) -> np.ndarray:
        cells = first_half_matrix(full_time, float(x[0]), float(x[1])).ravel()
        return (numerator @ cells) / np.clip(denominator @ cells, 1e-12, None)

    def residual(x: np.ndarray) -> np.ndarray:
        prices = weight * (model(x) - q)
        pull = prior_weight * np.array([x[0] - prior_home, x[1] - prior_away])
        return np.concatenate([prices, pull])

    try:
        solution = optimize.least_squares(
            residual, np.array([prior_home, prior_away]), bounds=([lo, lo], [hi, hi]), method="trf", xtol=1e-10, ftol=1e-12
        )
    except (ValueError, FloatingPointError):
        return None
    s_home, s_away = float(solution.x[0]), float(solution.x[1])
    plain = model(solution.x) - q
    return ShareFit(s_home, s_away, float(np.sqrt(np.mean(plain**2))), len(rows))


# --------------------------------------------------------------------------- #
# The split of one fixture
# --------------------------------------------------------------------------- #


@dataclass
class HalfSplit:
    """A fixture's full-time matrix with the first-half shares of home and away goals.

    ``source`` is one of :data:`HALF_SOURCES`; ``rmse_1h`` the residual of the calibration to Pinnacle's
    first-half prices (NaN unless ``source == "pinnacle_1h"``).  The joint and the half matrices are built on
    first use.
    """

    full_time: np.ndarray
    s_home: float
    s_away: float
    source: str = "global"
    rmse_1h: float = float("nan")
    n_targets_1h: int = 0
    _joint: np.ndarray | None = field(default=None, repr=False)
    _first: np.ndarray | None = field(default=None, repr=False)
    _second: np.ndarray | None = field(default=None, repr=False)

    def joint(self) -> np.ndarray:
        if self._joint is None:
            self._joint = joint_matrix(self.full_time, self.s_home, self.s_away)
        return self._joint

    def first_half(self) -> np.ndarray:
        if self._first is None:
            self._first = first_half_matrix(self.full_time, self.s_home, self.s_away)
        return self._first

    def second_half(self) -> np.ndarray:
        if self._second is None:
            self._second = second_half_matrix(self.full_time, self.s_home, self.s_away)
        return self._second

    def period_matrix(self, period: str) -> np.ndarray | None:
        """Score matrix of ``ft``, ``1h`` or ``2h`` (None for another period)."""
        if period == "ft":
            return self.full_time
        if period == "1h":
            return self.first_half()
        if period == "2h":
            return self.second_half()
        return None

    def rates(self) -> dict[str, tuple[float, float]]:
        """Mean goals ``(home, away)`` of the full time and of each half (the halves add up to the full time)."""
        goals = np.arange(self.full_time.shape[0], dtype=float)
        home = float((self.full_time.sum(axis=1) * goals).sum())
        away = float((self.full_time.sum(axis=0) * goals).sum())
        return {
            "ft": (home, away),
            "1h": (self.s_home * home, self.s_away * away),
            "2h": ((1.0 - self.s_home) * home, (1.0 - self.s_away) * away),
        }


def split_fixture(
    full_time: np.ndarray,
    league_shares: tuple[float, float, str],
    first_half_targets: Sequence[object] = (),
) -> HalfSplit:
    """The :class:`HalfSplit` of a fixture: calibrated to its first-half targets when there are any, else the
    league (or global) shares."""
    s_home, s_away, source = league_shares
    if first_half_targets:
        fit = calibrate_shares(full_time, first_half_targets, (s_home, s_away))
        if fit is not None:
            return HalfSplit(full_time, fit.s_home, fit.s_away, "pinnacle_1h", fit.rmse, fit.n_targets)
    return HalfSplit(full_time, float(s_home), float(s_away), source)


# --------------------------------------------------------------------------- #
# Legs priced from the joint
# --------------------------------------------------------------------------- #

JOINT_MARKETS: Final[frozenset[str]] = frozenset(
    {
        "ht_ft",
        "win_both_halves_home",
        "win_both_halves_away",
        "win_either_half_home",
        "win_either_half_away",
        "score_both_halves",
        "score_both_halves_home",
        "score_both_halves_away",
        "btts_both_halves",
        "highest_half",
        "highest_half_home",
        "highest_half_away",
        "first_goal",
        "last_goal",
    }
)
"""Markets that need the joint of the two halves (the others of a half use the half's own matrix)."""

_RESULT = {"home": 1, "draw": 0, "away": -1}


@lru_cache(maxsize=256)
def joint_weights(market: str, period: str, selection: str, size: int = _GRID) -> np.ndarray | None:
    """Win weight of a leg over ``J[a, b, c, d]`` (0/1, or a fraction for first / last goal); None if unknown."""
    g = np.arange(size, dtype=float)
    a = g[:, None, None, None]
    b = g[None, :, None, None]
    c = g[None, None, :, None]
    d = g[None, None, None, :]
    shape = (size, size, size, size)

    def full(mask: np.ndarray) -> np.ndarray:
        out = np.broadcast_to(np.asarray(mask, dtype=float), shape).copy()
        out.setflags(write=False)
        return out

    yes_no = selection in ("yes", "no")
    flip = selection == "no"
    if market == "ht_ft" and period == "ft":
        parts = selection.split("/")
        if len(parts) != 2 or any(p not in _RESULT for p in parts):
            return None
        ht, ft = _RESULT[parts[0]], _RESULT[parts[1]]
        return full((np.sign(a - b) == ht) & (np.sign(a + c - b - d) == ft))
    if period != "ft" and market != "first_goal":
        return None
    if market in ("win_both_halves_home", "win_both_halves_away", "win_either_half_home", "win_either_half_away") and yes_no:
        first = (a > b) if market.endswith("home") else (b > a)
        second = (c > d) if market.endswith("home") else (d > c)
        event = (first & second) if "both" in market else (first | second)
        return full(event ^ flip)
    if market == "score_both_halves" and yes_no:
        return full((((a + b) > 0) & ((c + d) > 0)) ^ flip)
    if market in ("score_both_halves_home", "score_both_halves_away") and yes_no:
        event = ((a > 0) & (c > 0)) if market.endswith("home") else ((b > 0) & (d > 0))
        return full(event ^ flip)
    if market == "btts_both_halves" and yes_no:
        return full(((a > 0) & (b > 0) & (c > 0) & (d > 0)) ^ flip)
    if market in ("highest_half", "highest_half_home", "highest_half_away"):
        first = {"highest_half": a + b, "highest_half_home": a + 0.0 * b, "highest_half_away": b + 0.0 * a}[market]
        second = {"highest_half": c + d, "highest_half_home": c + 0.0 * d, "highest_half_away": d + 0.0 * c}[market]
        event = {"first_half": first > second, "second_half": second > first, "equal": first == second}.get(selection)
        return None if event is None else full(event)
    if market in ("first_goal", "last_goal"):
        if selection not in ("home", "away", "none"):
            return None
        with np.errstate(invalid="ignore", divide="ignore"):
            home_first_1h = np.where(a + b > 0, a / np.where(a + b > 0, a + b, 1.0), 0.0)
            home_first_2h = np.where(c + d > 0, c / np.where(c + d > 0, c + d, 1.0), 0.0)
        goals_1h = (a + b) > 0
        goals_2h = (c + d) > 0
        if market == "first_goal" and period == "1h":
            home = np.broadcast_to(home_first_1h, shape)
            scored = np.broadcast_to(goals_1h, shape)
        elif market == "first_goal" and period == "ft":
            home = np.where(goals_1h, home_first_1h, home_first_2h)
            scored = goals_1h | goals_2h
        elif market == "last_goal" and period == "ft":
            home = np.where(goals_2h, home_first_2h, home_first_1h)  # the last goal of a half is home's w.p. c/(c+d) too
            scored = goals_1h | goals_2h
        else:
            return None
        scored = np.broadcast_to(scored, shape)
        home = np.broadcast_to(home, shape)
        if selection == "none":
            return full(~scored)
        return full(np.where(scored, home if selection == "home" else 1.0 - home, 0.0))
    return None


def half_leg_probability(split: HalfSplit, market: str, period: str, selection: str, line: float = float("nan")) -> float:
    """Probability of a leg under the half split: joint markets from ``J``, the others from the period's matrix.

    Returns NaN for a leg the model cannot price.
    """
    from model_buk.scanner.fair import matrix_leg_probability

    if market in JOINT_MARKETS:
        weights = joint_weights(market, period, selection, split.full_time.shape[0])
        if weights is None:
            return float("nan")
        return float((split.joint() * weights).sum())
    matrix = split.period_matrix(period)
    if matrix is None:
        return float("nan")
    return matrix_leg_probability(matrix, market, selection, line)


__all__ = [
    "DEFAULT_HALF_SHARES",
    "DEFAULT_SHARES",
    "DEFAULT_WINDOW_YEARS",
    "HALF_SOURCES",
    "HalfShares",
    "HalfSplit",
    "JOINT_MARKETS",
    "ShareFit",
    "calibrate_shares",
    "estimate_half_shares",
    "first_half_matrix",
    "half_leg_probability",
    "joint_matrix",
    "joint_weights",
    "load_history_matches",
    "second_half_matrix",
    "shares_from_matches",
    "split_fixture",
]
