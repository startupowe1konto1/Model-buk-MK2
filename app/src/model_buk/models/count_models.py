"""Poisson and negative-binomial (NB2) regression models for match statistics.

The models forecast corners, yellow cards, shots and shots on target -- for each
team and for the match total -- so that any over/under line can be priced from one
fit (plan, days 5-7).  They plug into the common evaluation framework
(:mod:`model_buk.evaluation.framework`) as ordinary ``fit(train)`` /
``predict(frame)`` models for the markets of
:mod:`model_buk.evaluation.count_markets`.

Model
-----
Every match gives two team-rows (team, opponent, home or away).  For a team-row
the log of the expected count is linear in pre-match features::

    log mu = b0 + b_home * is_home
             + form of the statistic: for each window k of 5, 15, 45 and 90 matches
               the team's own count (for) and conceded count (against) and the
               opponent's for and against, each as a log-ratio to the league mean
             + auxiliary form over the last 10 matches (own and opponent, for and
               against): shots and goals for corners, fouls and goals for cards,
               goals for shots, shots and goals for shots on target
             + referee tendency + rest days of both teams

The form values are shrunk towards the league mean with a few pseudo-matches (a
newly promoted team, or the first matches of a history, get the league mean; a
value is never invented as zero, and a match whose statistic is unknown is skipped,
not counted).  The referee tendency is the referee's average match total over his
earlier matches of the league, shrunk the same way.  The two team-rows of a match
are fitted together (one coefficient vector per league, ridge-penalised, optionally
time-weighted).  Expected goals (``xg``) can be added as an auxiliary statistic
(``FeatureConfig(aux=(..., "xg"))``); on the 2022-2025 protocol it did not help,
because it exists mostly from 2023 and only in part of the leagues.

``kind="poisson"`` uses the Poisson likelihood.  ``kind="nb2"`` uses the NB2
negative binomial (``Var = mu + alpha * mu**2``) with one dispersion ``alpha`` per
league fitted by maximum likelihood.  Team lines use ``NB2(mu_team, alpha)``.  The
match total uses ``NB2(mu_home + mu_away, alpha_total)`` with its own dispersion
fitted on the training totals (two dependent counts do not add up to an NB2 with
the team dispersion); for the Poisson kind the total is exactly
``Poisson(mu_home + mu_away)``.

No look-ahead
-------------
Features of a row use only matches that kicked off before that row and whose
statistic is known; forecast rows use the training history only (the framework
hands over the matches finished before the matchday).  ``fit`` only records the
training frame; the work is done in ``predict`` on the training frame together
with the frame to forecast, and its result (the expected counts and dispersions)
is cached in a :class:`CountEngine` keyed by the exact training set, the forecast
rows and the hyper-parameters, so the fit that serves one line serves all lines of
a statistic.  The optimiser may start from the previous solution of the same
league (starting values only; the objective is strictly convex, so the optimum
does not depend on them).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import optimize, special

from model_buk.evaluation.count_markets import FAMILIES, CountFamily, Scope
from model_buk.evaluation.framework import LeagueFrequencyBaseline, Market, ModelSpec

Kind = Literal["poisson", "nb2"]

_NS_PER_DAY = 86_400_000_000_000
_MIN_ALPHA = 1e-3
_MAX_ALPHA = 20.0
_PROBABILITY_FLOOR = 1e-6

# Hyper-parameter grid chosen per outer season on the season before it (framework inner selection).
# ``ridge`` is in units of the mean training weight; ``half_life_days=None`` means no time decay.
DEFAULT_GRID: dict[str, tuple[float | None, ...]] = {
    "ridge": (1_000.0, 5_000.0),
    "half_life_days": (None, 1_000.0),
}


@dataclass(frozen=True)
class FeatureConfig:
    """Which pre-match features feed the regression (all defined in the module docstring)."""

    windows: tuple[int | None, ...] = (5, 15, 45, 90)
    aux: tuple[str, ...] | None = None  # None: the auxiliary statistics of the family
    aux_window: int = 10
    shrink: float = 3.0
    aux_shrink: float | None = None  # pseudo-matches for the auxiliary statistics; None: same as ``shrink``
    referee_shrink: float = 10.0
    both_sides: bool = True
    use_referee: bool = True
    use_rest: bool = True
    rest_cap_days: float = 21.0


# ---------------------------------------------------------------------------
# Feature table
# ---------------------------------------------------------------------------


def _float_column(frame: pd.DataFrame, name: str) -> np.ndarray:
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
    return np.full(len(frame), np.nan)


def _kickoff_ns(frame: pd.DataFrame) -> np.ndarray:
    if "kickoff" in frame.columns:
        return pd.DatetimeIndex(frame["kickoff"]).asi8.astype(np.int64)
    stamp = pd.to_datetime(frame["kickoff_utc"], utc=True, format="mixed")
    return pd.DatetimeIndex(stamp).as_unit("ns").asi8.astype(np.int64)


@dataclass
class _Blocks:
    """Team-rows sorted by (team, time): the shared index arithmetic of all rolling windows."""

    order: np.ndarray
    time: np.ndarray
    first: np.ndarray
    block: np.ndarray


def _team_blocks(team_code: np.ndarray, time_ns: np.ndarray) -> _Blocks:
    order = np.lexsort((np.arange(len(team_code)), time_ns, team_code))
    code = team_code[order]
    new = np.empty(len(code), dtype=bool)
    new[0] = True
    new[1:] = code[1:] != code[:-1]
    return _Blocks(order, time_ns[order], np.flatnonzero(new), np.cumsum(new) - 1)


def _prior_windows(
    blocks: _Blocks,
    known: np.ndarray,
    values: Sequence[np.ndarray],
    windows: Sequence[int | None],
) -> list[list[tuple[np.ndarray, np.ndarray]]]:
    """Sum and count of the last ``k`` known values strictly before each row, within its block.

    ``values`` are aligned with the sorted rows; ``known`` marks rows whose value
    is usable (rows with an unknown value are skipped, not counted as zero).
    ``windows`` holds window sizes; ``None`` means the whole block history.
    Returns, per window, one ``(sum, count)`` pair per value array.
    """
    kn = known.astype(np.int64)
    excl = np.cumsum(kn) - kn
    start = excl[blocks.first][blocks.block]
    cumulative = [np.concatenate(([0.0], np.cumsum(np.where(known, v, 0.0)[known]))) for v in values]
    out: list[list[tuple[np.ndarray, np.ndarray]]] = []
    for k in windows:
        low = start if k is None else np.maximum(excl - k, start)
        count = (excl - low).astype(float)
        out.append([(ks[excl] - ks[low], count) for ks in cumulative])
    return out


def _log_ratio(total: np.ndarray, count: np.ndarray, mean: float, shrink: float) -> np.ndarray:
    """``log`` of the shrunk average over ``mean`` (0 = league level; 0 also when nothing is known)."""
    if not np.isfinite(mean) or mean <= 0.0:
        return np.zeros(len(total))
    return np.log((total + shrink * mean) / (count + shrink) / mean)


class _Design:
    """Design matrix of the team-rows of a set of matches (train first, then the rows to forecast)."""

    def __init__(self, names: list[str], matrix: np.ndarray, target: np.ndarray, time_ns: np.ndarray, n_train: int) -> None:
        self.names = names
        self.matrix = matrix
        self.target = target
        self.time_ns = time_ns
        self.n_train = n_train

    @property
    def n_matches(self) -> int:
        return len(self.time_ns)


def build_design(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    family: CountFamily,
    config: FeatureConfig = FeatureConfig(),
) -> _Design:
    """Pre-match features for every team-row of ``train`` and of the ``frame`` to forecast.

    Layout: rows ``0..M-1`` are the home team-rows of the ``M = n_train + n_forecast``
    matches, rows ``M..2M-1`` the away team-rows (same match order).  Column 0 is the
    intercept and column 1 the home indicator; the rest are the features listed in
    the module docstring.  ``target`` holds the counts of the training rows (NaN for
    the rows to forecast).
    """
    n_train, n_query = len(train), len(frame)
    m = n_train + n_query
    time_ns = np.concatenate([_kickoff_ns(train), _kickoff_ns(frame)])
    home_ids = np.concatenate([train["home_team_id"].to_numpy(), frame["home_team_id"].to_numpy()])
    away_ids = np.concatenate([train["away_team_id"].to_numpy(), frame["away_team_id"].to_numpy()])
    codes, _ = pd.factorize(np.concatenate([home_ids, away_ids]))
    codes = codes.astype(np.int64)
    if (codes < 0).any():  # a row without a team id gets a code of its own: it has no history
        bad = codes < 0
        codes[bad] = codes.max(initial=-1) + 1 + np.arange(int(bad.sum()))
    blocks = _team_blocks(codes, np.concatenate([time_ns, time_ns]))
    order = blocks.order

    def to_layout(sorted_values: np.ndarray) -> np.ndarray:
        out = np.empty(2 * m)
        out[order] = sorted_values
        return out

    def opponent(layout: np.ndarray) -> np.ndarray:
        return np.roll(layout, m)

    aux = family.aux if config.aux is None else config.aux
    sources = [family.name, *aux]
    columns: list[np.ndarray] = [np.ones(2 * m), np.concatenate([np.ones(m), np.zeros(m)])]
    names = ["intercept", "is_home"]
    target = np.full(2 * m, np.nan)
    unknown = np.full(n_query, np.nan)  # the rows to forecast never contribute a count, whatever the frame holds
    for source in sources:
        home = np.concatenate([_float_column(train, f"home_{source}"), unknown])
        away = np.concatenate([_float_column(train, f"away_{source}"), unknown])
        own = np.concatenate([home, away])
        conceded = np.concatenate([away, home])
        known_all = np.isfinite(own) & np.isfinite(conceded)
        if source == family.name:
            target = own.copy()
        mean = float(own[known_all & (np.arange(2 * m) % m < n_train)].mean()) if known_all.any() else float("nan")
        windows = list(config.windows) if source == family.name else [config.aux_window]
        shrink = config.shrink if source == family.name or config.aux_shrink is None else config.aux_shrink
        stats = _prior_windows(blocks, known_all[order], [own[order], conceded[order]], windows)
        for k, ((own_sum, count), (con_sum, _)) in zip(windows, stats):
            own_form = to_layout(_log_ratio(own_sum, count, mean, shrink))
            conceded_form = to_layout(_log_ratio(con_sum, count, mean, shrink))
            columns.append(own_form)
            names.append(f"{source}_for_{k}")
            columns.append(opponent(conceded_form))
            names.append(f"{source}_opp_against_{k}")
            if config.both_sides:
                columns.append(conceded_form)
                names.append(f"{source}_against_{k}")
                columns.append(opponent(own_form))
                names.append(f"{source}_opp_for_{k}")
    if config.use_referee:
        columns.append(_referee_feature(train, frame, family, time_ns, n_train, config))
        names.append("referee")
    if config.use_rest:
        gap = np.full(2 * m, np.nan)
        gap[1:] = (blocks.time[1:] - blocks.time[:-1]) / _NS_PER_DAY
        gap[blocks.first] = np.nan
        rest = to_layout(np.log(np.clip(gap, 1.0, config.rest_cap_days)))
        rest = np.where(np.isnan(rest), np.log(7.0), rest)  # first match of a team in the history
        columns.append(rest)
        names.append("rest_own")
        columns.append(opponent(rest))
        names.append("rest_opp")
    return _Design(names, np.column_stack(columns), target, time_ns, n_train)


def _referee_feature(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    family: CountFamily,
    time_ns: np.ndarray,
    n_train: int,
    config: FeatureConfig,
) -> np.ndarray:
    """Referee's shrunk average match total over his earlier matches, as a log ratio (0 = unknown)."""
    m = len(time_ns)
    if "referee" not in train.columns or "referee" not in frame.columns:
        return np.zeros(2 * m)
    referee = np.concatenate([train["referee"].to_numpy(dtype=object), frame["referee"].to_numpy(dtype=object)])
    codes, _ = pd.factorize(referee)
    total = np.concatenate([_float_column(train, family.home) + _float_column(train, family.away), np.full(len(frame), np.nan)])
    known = np.isfinite(total)
    blocks = _team_blocks(codes.astype(np.int64), time_ns)
    (sum_, count), = _prior_windows(blocks, known[blocks.order], [total[blocks.order]], [None])[0]
    mean = float(total[known & (np.arange(m) < n_train)].mean()) if known.any() else float("nan")
    feature_sorted = _log_ratio(sum_, count, mean, config.referee_shrink)
    feature = np.empty(m)
    feature[blocks.order] = feature_sorted
    feature[codes < 0] = 0.0
    return np.concatenate([feature, feature])


# ---------------------------------------------------------------------------
# Likelihoods and the regression solver
# ---------------------------------------------------------------------------


def nb2_alpha_mle(
    y: np.ndarray,
    mu: np.ndarray,
    weights: np.ndarray,
    *,
    lower: float = _MIN_ALPHA,
    upper: float = _MAX_ALPHA,
) -> float:
    """Maximum-likelihood NB2 dispersion ``alpha`` for fixed means (weighted counts).

    Solves the score equation in ``r = 1 / alpha`` by bracketing.  Returns
    ``lower`` when the data are not over-dispersed at all (Poisson limit) and
    ``upper`` when the likelihood keeps improving up to the bound.
    """
    y = np.asarray(y, dtype=float)
    counts = np.rint(y).astype(np.int64)
    by_count = np.bincount(counts, weights=weights)
    grid = np.arange(len(by_count), dtype=float)
    total = float(weights.sum())

    def score(log_r: float) -> float:
        r = math.exp(log_r)
        rm = r + mu
        head = float(by_count @ special.digamma(grid + r)) - total * float(special.digamma(r))
        return head + float(weights @ (math.log(r) - np.log(rm) + (mu - y) / rm))

    lo, hi = math.log(1.0 / upper), math.log(1.0 / lower)
    if score(hi) >= 0.0:
        return lower
    if score(lo) <= 0.0:
        return upper
    root = optimize.brentq(lambda u: score(u) * math.exp(u), lo, hi, xtol=1e-5, rtol=1e-6)
    return 1.0 / math.exp(root)


def fit_count_glm(
    x: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    penalty: np.ndarray,
    kind: Kind,
    *,
    beta0: np.ndarray | None = None,
    alpha0: float | None = None,
    max_iter: int = 40,
    tol: float = 1e-7,
) -> tuple[np.ndarray, float, int]:
    """Penalised Poisson / NB2 regression with a log link by Fisher scoring.

    ``penalty`` is the ridge weight per coefficient (0 for unpenalised columns).
    For ``kind="nb2"`` the dispersion is re-estimated by maximum likelihood after
    every coefficient step.  Returns ``(beta, alpha, iterations)``; ``alpha`` is 0
    for the Poisson kind.  The objective is strictly convex in ``beta`` for fixed
    ``alpha`` (positive penalty on all but the free columns), so the result does
    not depend on ``beta0``.
    """
    n, p = x.shape
    total = float(weights.sum())
    beta = np.zeros(p) if beta0 is None else np.array(beta0, dtype=float)
    if beta0 is None:
        beta[0] = math.log(max(float(weights @ y) / total, 1e-3))
    alpha = 0.0 if kind == "poisson" else (0.15 if alpha0 is None else float(alpha0))
    xt = x.T
    ridge = np.diag(penalty)
    iterations = 0
    for iterations in range(1, max_iter + 1):
        eta = np.clip(x @ beta, -12.0, 8.0)
        mu = np.exp(eta)
        w = weights * mu / (1.0 + alpha * mu)
        z = eta + (y - mu) / mu
        xtw = xt * w
        new = np.linalg.solve(xtw @ x + ridge, xtw @ z)
        if not np.isfinite(new).all():
            raise FloatingPointError("count regression diverged")
        step = float(np.max(np.abs(new - beta)))
        beta = new
        if kind == "nb2":
            mu_new = np.exp(np.clip(x @ beta, -12.0, 8.0))
            alpha_new = nb2_alpha_mle(y, mu_new, weights)
            change = abs(math.log(alpha_new) - math.log(alpha))
            alpha = alpha_new
            if step < tol * 1e2 and change < 1e-4:
                break
        elif step < tol:
            break
    return beta, alpha, iterations


# ---------------------------------------------------------------------------
# Line probabilities
# ---------------------------------------------------------------------------


def over_probability(mean: np.ndarray, alpha: float, line: float, kind: Kind) -> np.ndarray:
    """``P(count > line)`` for a Poisson (``alpha`` ignored) or NB2 count with the given mean."""
    k = math.floor(line)
    mean = np.asarray(mean, dtype=float)
    if kind == "poisson" or alpha <= 0.0:
        p = special.pdtrc(k, mean)
    else:
        # NB2 survival function through the regularised incomplete beta function
        # (scipy.special.nbdtrc truncates a non-integer size, so it cannot be used here).
        r = 1.0 / alpha
        p = special.betainc(k + 1, r, mean / (r + mean))
    return np.clip(p, _PROBABILITY_FLOOR, 1.0 - _PROBABILITY_FLOOR)


@dataclass(frozen=True)
class CountForecast:
    """Expected counts of the forecast rows and the dispersions of the fitted NB2."""

    mu_home: np.ndarray
    mu_away: np.ndarray
    alpha: float
    alpha_total: float
    kind: Kind

    def over(self, scope: Scope, line: float) -> np.ndarray:
        """Probability of more than ``line`` for the match total or one team."""
        if scope == "total":
            return over_probability(self.mu_home + self.mu_away, self.alpha_total, line, self.kind)
        mean = self.mu_home if scope == "home" else self.mu_away
        return over_probability(mean, self.alpha, line, self.kind)

    def over_table(self, total_lines: Sequence[float] = (), team_lines: Sequence[float] = ()) -> pd.DataFrame:
        """``P(over)`` per row for many lines at once, columns ``total_9.5``, ``home_4.5``, ``away_4.5``.

        Handy for pricing every line a bookmaker offers for the same fixtures from one fit.
        """
        columns = {f"total_{line}": self.over("total", line) for line in total_lines}
        for scope in ("home", "away"):
            columns.update({f"{scope}_{line}": self.over(scope, line) for line in team_lines})
        return pd.DataFrame(columns)


# ---------------------------------------------------------------------------
# Engine (one per statistic and kind) and the model that plugs into the framework
# ---------------------------------------------------------------------------


def _params_tuple(ridge: float, half_life_days: float | None) -> tuple[float, float | None]:
    return float(ridge), (None if half_life_days is None else float(half_life_days))


class CountEngine:
    """Fits the regression for one statistic and serves every line from the cached result.

    One engine is shared by all markets (lines and scopes) of a statistic, so a
    matchday is fitted once per hyper-parameter setting no matter how many lines
    are evaluated.  The cache key contains the exact training rows (fixture ids and
    the statistic's values), the forecast rows and the parameters; a different table
    can never hit a stale entry.
    """

    def __init__(
        self,
        family: CountFamily | str,
        kind: Kind,
        *,
        features: FeatureConfig = FeatureConfig(),
        warm_start: bool = True,
    ) -> None:
        if kind not in ("poisson", "nb2"):
            raise ValueError("kind must be 'poisson' or 'nb2'")
        self.family = FAMILIES[family] if isinstance(family, str) else family
        self.kind: Kind = kind
        self.features = features
        self.aux = self.family.aux if features.aux is None else features.aux
        self.warm_start = warm_start
        self._cache: dict[tuple[Any, ...], CountForecast] = {}
        self._warm: dict[tuple[Any, ...], tuple[np.ndarray, float]] = {}
        self.n_fits = 0
        self.n_hits = 0

    def _train_key(self, train: pd.DataFrame) -> tuple[Any, ...]:
        ids = train["fixture_id"].to_numpy()
        home = _float_column(train, self.family.home)
        away = _float_column(train, self.family.away)
        aux = tuple(
            float(np.nansum(_float_column(train, f"{side}_{name}"))) for name in self.aux for side in ("home", "away")
        )
        return (self._league(train), len(ids), hash(ids.tobytes()), hash(home.tobytes()), hash(away.tobytes()), aux)

    @staticmethod
    def _league(train: pd.DataFrame) -> str:
        return str(train["league_code"].iloc[0]) if len(train) and "league_code" in train.columns else ""

    def forecast(
        self,
        train: pd.DataFrame,
        frame: pd.DataFrame,
        *,
        ridge: float,
        half_life_days: float | None,
    ) -> CountForecast:
        """Expected counts for the rows of ``frame`` from the matches in ``train``."""
        if len(frame) == 0:
            return CountForecast(np.empty(0), np.empty(0), 0.0, 0.0, self.kind)
        params = _params_tuple(ridge, half_life_days)
        key = (self._train_key(train), hash(frame["fixture_id"].to_numpy().tobytes()), len(frame), params)
        cached = self._cache.get(key)
        if cached is not None:
            self.n_hits += 1
            return cached
        result = self._fit_and_forecast(train, frame, params)
        self._cache[key] = result
        self.n_fits += 1
        return result

    def _fit_and_forecast(
        self, train: pd.DataFrame, frame: pd.DataFrame, params: tuple[float, float | None]
    ) -> CountForecast:
        ridge, half_life = params
        n_query = len(frame)
        design = build_design(train, frame, self.family, self.features)
        m, n_train = design.n_matches, design.n_train
        both_known = np.isfinite(design.target[:n_train]) & np.isfinite(design.target[m : m + n_train])
        matches = np.flatnonzero(both_known)
        if len(matches) < 10:  # nothing to learn from: no forecast (missing stays missing)
            nan = np.full(n_query, np.nan)
            return CountForecast(nan, nan.copy(), 0.0, 0.0, self.kind)
        rows_train = np.concatenate([matches, m + matches])
        rows_query = np.concatenate([np.arange(n_train, m), m + np.arange(n_train, m)])
        y = design.target[rows_train]
        # Standardise on the training rows; the intercept and the home column stay unpenalised.
        x_train = design.matrix[rows_train]
        centre = x_train.mean(axis=0)
        scale = x_train.std(axis=0)
        scale[scale < 1e-9] = 1.0
        centre[0], scale[0] = 0.0, 1.0
        x_std = (design.matrix - centre) / scale
        if half_life is None:
            match_weight = np.ones(m)
        else:
            age = np.maximum(int(design.time_ns[:n_train].max()) - design.time_ns, 0)
            match_weight = np.power(0.5, age / (half_life * _NS_PER_DAY))
        weights = np.concatenate([match_weight[matches], match_weight[matches]])
        penalty = np.full(x_std.shape[1], ridge * float(weights.mean()))
        penalty[:2] = 0.0
        warm_key = (self._league(train), params)
        beta0 = alpha0 = None
        if self.warm_start and warm_key in self._warm and len(self._warm[warm_key][0]) == x_std.shape[1]:
            raw_previous, alpha0 = self._warm[warm_key]
            beta0 = raw_previous * scale
            beta0[0] = raw_previous[0] + float(raw_previous[1:] @ centre[1:])
        x_fit = x_std[rows_train]
        beta, alpha, _ = fit_count_glm(x_fit, y, weights, penalty, self.kind, beta0=beta0, alpha0=alpha0)
        raw = beta / scale
        raw[0] = beta[0] - float(raw[1:] @ centre[1:])
        self._warm[warm_key] = (raw, alpha)
        alpha_total = 0.0
        if self.kind == "nb2":
            mu_fit = np.exp(np.clip(x_fit @ beta, -12.0, 8.0))
            k = len(matches)
            alpha_total = nb2_alpha_mle(y[:k] + y[k:], mu_fit[:k] + mu_fit[k:], weights[:k])
        mu_query = np.exp(np.clip(x_std[rows_query] @ beta, -12.0, 8.0))
        return CountForecast(mu_query[:n_query], mu_query[n_query:], alpha, alpha_total, self.kind)


class CountLineModel:
    """Framework model: probability of under/over one line of a count market."""

    classes = ("under", "over")

    def __init__(
        self,
        engine: CountEngine,
        scope: Scope,
        line: float,
        *,
        ridge: float = DEFAULT_GRID["ridge"][0],
        half_life_days: float | None = DEFAULT_GRID["half_life_days"][0],
    ) -> None:
        self.engine = engine
        self.scope: Scope = scope
        self.line = float(line)
        self.ridge = float(ridge)
        self.half_life_days = half_life_days
        self._train: pd.DataFrame | None = None

    def fit(self, train: pd.DataFrame) -> "CountLineModel":
        # The regression is solved in predict() together with the rows it forecasts (see the module docstring).
        self._train = train
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self._train is None:
            raise RuntimeError("fit must be called before predict")
        forecast = self.engine.forecast(self._train, frame, ridge=self.ridge, half_life_days=self.half_life_days)
        over = forecast.over(self.scope, self.line)
        return np.column_stack([1.0 - over, over])


def count_spec(
    engine: CountEngine,
    scope: Scope,
    line: float,
    *,
    grid: Mapping[str, Sequence[Any]] | None = None,
) -> ModelSpec:
    """Framework spec ``count_<kind>`` for one line, sharing the fits of ``engine``."""
    return ModelSpec(
        f"count_{engine.kind}",
        lambda **params: CountLineModel(engine, scope, line, **params),
        param_grid=dict(DEFAULT_GRID if grid is None else grid),
    )


def count_baseline_spec(market: Market, *, alpha: float = 1.0) -> ModelSpec:
    """League frequency of the line's outcome from the training matches (the baseline of every count line)."""
    return ModelSpec("count_league_frequencies", lambda: LeagueFrequencyBaseline(market, alpha=alpha))


class RecentFrequencyBaseline:
    """Outcome frequency of the league over its last ``window_days`` of training matches.

    A stricter benchmark than the all-history league frequency for statistics that drift
    (shots have risen for years): it follows the league's recent level.  With fewer than
    ``min_matches`` matches inside the window the latest ``min_matches`` are used.  Laplace
    smoothing as in :class:`~model_buk.evaluation.framework.LeagueFrequencyBaseline`;
    a league that is not in the training data gets a NaN forecast.
    """

    def __init__(
        self,
        market: Market,
        *,
        window_days: float = 365.0,
        min_matches: int = 60,
        alpha: float = 1.0,
        league_col: str = "league_code",
    ) -> None:
        if window_days <= 0 or min_matches < 1 or alpha < 0:
            raise ValueError("window_days and min_matches must be positive and alpha not negative")
        self.market = market
        self.classes = market.classes
        self.window_ns = int(window_days * _NS_PER_DAY)
        self.min_matches = int(min_matches)
        self.alpha = float(alpha)
        self.league_col = league_col
        self._probabilities: dict[Any, np.ndarray] = {}

    def fit(self, train: pd.DataFrame) -> "RecentFrequencyBaseline":
        k = len(self.classes)
        y = self.market.outcome(train)
        time_ns = _kickoff_ns(train)
        leagues = train[self.league_col].to_numpy()
        self._probabilities = {}
        for league in pd.unique(leagues):
            rows = np.flatnonzero((leagues == league) & ~np.isnan(y))
            if len(rows) == 0:
                continue
            rows = rows[np.argsort(time_ns[rows], kind="stable")]
            recent = rows[time_ns[rows] >= time_ns[rows[-1]] - self.window_ns]
            if len(recent) < self.min_matches:
                recent = rows[-self.min_matches :]
            counts = np.bincount(y[recent].astype(np.int64), minlength=k).astype(float) + self.alpha
            self._probabilities[league] = counts / counts.sum()
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        out = np.full((len(frame), len(self.classes)), np.nan)
        for league, probabilities in self._probabilities.items():
            out[(frame[self.league_col] == league).to_numpy()] = probabilities
        return out


def count_recent_baseline_spec(market: Market, *, window_days: float = 365.0) -> ModelSpec:
    """League frequency over the last ``window_days`` (the drift-aware benchmark of a count line)."""
    return ModelSpec("count_recent_frequencies", lambda: RecentFrequencyBaseline(market, window_days=window_days))


__all__ = [
    "RecentFrequencyBaseline",
    "count_recent_baseline_spec",
    "CountEngine",
    "CountForecast",
    "CountLineModel",
    "DEFAULT_GRID",
    "FeatureConfig",
    "build_design",
    "count_baseline_spec",
    "count_spec",
    "fit_count_glm",
    "nb2_alpha_mle",
    "over_probability",
]
