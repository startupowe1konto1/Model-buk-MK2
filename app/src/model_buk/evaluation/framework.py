"""Common evaluation framework: one fair walk-forward test bench for every model.

Every model (league frequency baseline, Dixon-Coles, NB2, GAS-Poisson,
Elo-Davidson, pi-ratings + gradient boosting, ...) and every market (1X2,
over/under, ...) is judged by the *same* procedure, scores and tests.

Plugging a model in
-------------------
A model is any object with two methods::

    class MyModel:
        classes = ("home", "draw", "away")        # optional; if present it must equal Market.classes

        def fit(self, train: pd.DataFrame) -> "MyModel": ...
        def predict(self, frame: pd.DataFrame) -> np.ndarray:
            ...  # shape (len(frame), n_classes); rows sum to 1

``train`` holds finished matches with all their columns (goals, teams,
kickoff, ...); ``frame`` holds the matches to forecast with every outcome
column *removed* (goals, half-time and extra-time scores, penalties, status).
Both carry a parsed UTC ``kickoff`` column next to the raw ``kickoff_utc``.
Probability columns follow the ordered outcome classes of the
:class:`Market` (1X2 = home, draw, away; over/under = under, over).  A row of
NaN means "no forecast" (missing stays missing; such rows are never scored as
zero and are reported in ``n_missing``).

Wrap it in a :class:`ModelSpec` -- a factory that builds a *fresh unfitted*
model for every refit plus an optional hyper-parameter grid::

    spec = ModelSpec(
        "dixon_coles",
        lambda half_life_days: DixonColesModel(half_life_days),
        param_grid={"half_life_days": [180, 365, 730, 1200]},
    )
    result = evaluate_model(spec, matches, MARKET_1X2, WalkForwardConfig(test_seasons=range(2021, 2026)))
    table = result.score_table()                       # log-loss, Brier, RPS per league + pooled
    versus = compare_to_baseline(result, baseline_result)   # DM + Holm + BH + bootstrap CI

The procedure
-------------
Outer folds are test seasons.  For every test season and every matchday (a
round; leagues or rounds without a clean round structure fall back to
calendar-week buckets, see :func:`assign_matchdays`) the model is refit from
scratch on the matches that finished before that matchday's *first kickoff*
(kickoff at least ``result_latency_hours`` earlier, so the result is known) and
predicts the whole matchday.  This cadence is imposed by the framework and is
identical for every model.  Nothing after the matchday start and no outcome of
the matchday itself is visible to the model.

Hyper-parameters are chosen by inner folds: for an outer season ``S`` each grid
combination is walk-forwarded over the ``inner_seasons`` seasons directly
before ``S`` using only rows of seasons earlier than ``S``; the combination
with the lowest pooled inner log-loss (or per league with
``select_per="league"``) is then run on ``S``.  Outer outcomes never influence
the choice.  Known limitation of the *pooled* choice: "earlier season" means the
provider's season label, and leagues with calendar-year seasons start before
the European season with the previous label has ended, so a few results that
arrived after such a league's first outer-season kickoff can reach the pooled
choice of one hyper-parameter setting.  ``select_per="league"`` never has this
overlap (a league's own season labels are consistent in time).

``scope="per_league"`` (default) fits one model per league, ``scope="pooled"``
fits a single model on all leagues with calendar-week refits.

Columns that reveal the outcome under another name (say ``home_winner``) must be
added to ``WalkForwardConfig.hidden_columns``; the framework can only hide, and
the leakage test can only disturb, the outcome columns it knows about.

Scoring and tests
-----------------
Per-match log-loss (primary), Brier and RPS live in :mod:`.scoring`;
CORP reliability diagrams with ``MCB - DSC + UNC`` in :mod:`.reliability`;
Diebold-Mariano (HAC), Holm / Benjamini-Hochberg and the matchday cluster
bootstrap in :mod:`.significance`.  :func:`compare_to_baseline` ties them
together per league and pooled.  :func:`check_no_lookahead` is the leakage
test: shuffling the outcomes of future matches must not change any earlier
forecast.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable

import numpy as np
import pandas as pd

from model_buk.evaluation.reliability import CorpScore, corp_by_class
from model_buk.evaluation.scoring import (
    METRICS,
    check_probabilities,
    log_loss_per_match,
    per_match_scores,
    score_table,
)
from model_buk.evaluation.significance import (
    benjamini_hochberg_adjust,
    cluster_bootstrap_mean_ci,
    diebold_mariano,
    holm_adjust,
)

_NS_PER_HOUR = 3_600_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR
_NS_PER_WEEK = 7 * _NS_PER_DAY
# 1970-01-05 was a Monday: week buckets run Monday 00:00 UTC to Sunday 24:00.
_MONDAY_EPOCH_NS = 4 * _NS_PER_DAY

DEFAULT_HIDDEN_COLUMNS: tuple[str, ...] = (
    "home_goals",
    "away_goals",
    "ht_home_goals",
    "ht_away_goals",
    "et_home_goals",
    "et_away_goals",
    "pen_home",
    "pen_away",
    "status_short",
    "fetched_at",
    "batch_file",
)


class LookaheadLeakError(AssertionError):
    """Raised when forecasts depend on outcomes that were not yet known."""


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Market:
    """A market: ordered outcome classes and how to read the outcome from a frame.

    ``outcome_fn`` maps a match frame to a float array holding the class index
    (0 .. K-1) or NaN where the outcome is unknown.  ``outcome_columns`` are
    hidden from ``predict``.
    """

    name: str
    classes: tuple[str, ...]
    outcome_fn: Callable[[pd.DataFrame], np.ndarray] = field(compare=False, repr=False)
    outcome_columns: tuple[str, ...] = ("home_goals", "away_goals")

    def outcome(self, frame: pd.DataFrame) -> np.ndarray:
        """Class index per match (float, NaN = unknown)."""
        return np.asarray(self.outcome_fn(frame), dtype=float)


def _goals(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    home = pd.to_numeric(frame["home_goals"], errors="coerce").to_numpy(dtype=float)
    away = pd.to_numeric(frame["away_goals"], errors="coerce").to_numpy(dtype=float)
    return home, away


def _outcome_1x2(frame: pd.DataFrame) -> np.ndarray:
    home, away = _goals(frame)
    out = np.where(home > away, 0.0, np.where(home == away, 1.0, 2.0))
    out[np.isnan(home) | np.isnan(away)] = np.nan
    return out


MARKET_1X2 = Market("1x2", ("home", "draw", "away"), _outcome_1x2)


def over_under(line: float) -> Market:
    """Total goals over/under ``line`` (classes: under < over)."""

    def outcome(frame: pd.DataFrame) -> np.ndarray:
        home, away = _goals(frame)
        out = (home + away > line).astype(float)
        out[np.isnan(home) | np.isnan(away)] = np.nan
        return out

    return Market(f"over_under_{line}", ("under", "over"), outcome)


# ---------------------------------------------------------------------------
# Model interface
# ---------------------------------------------------------------------------


@runtime_checkable
class ProbabilityModel(Protocol):
    """Anything with ``fit(train)`` and ``predict(frame) -> (n, K)`` probabilities."""

    def fit(self, train: pd.DataFrame) -> Any: ...

    def predict(self, frame: pd.DataFrame) -> np.ndarray: ...


@dataclass(frozen=True)
class ModelSpec:
    """A model factory plus an optional hyper-parameter grid for inner selection.

    ``factory(**params)`` must return a fresh, unfitted model every call.
    """

    name: str
    factory: Callable[..., ProbabilityModel]
    param_grid: Mapping[str, Sequence[Any]] | None = None

    def combinations(self) -> list[dict[str, Any]]:
        if not self.param_grid:
            return [{}]
        keys = list(self.param_grid)
        for key in keys:
            if len(self.param_grid[key]) == 0:
                raise ValueError(f"param_grid[{key!r}] is empty")
        return [dict(zip(keys, values)) for values in itertools.product(*(self.param_grid[k] for k in keys))]


def _params_key(params: Mapping[str, Any]) -> str:
    return json.dumps(dict(params), sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Configuration and data preparation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardConfig:
    """Everything that defines the fair test bench (identical for all models)."""

    test_seasons: Sequence[int]
    scope: Literal["per_league", "pooled"] = "per_league"
    matchday_method: Literal["auto", "round", "week"] = "auto"
    max_round_span_days: float = 7.0
    result_latency_hours: float = 3.0
    min_train_matches: int = 50
    inner_seasons: int = 2
    select_per: Literal["pooled", "league"] = "pooled"
    finished_statuses: tuple[str, ...] = ("FT",)
    id_col: str = "fixture_id"
    league_col: str = "league_code"
    season_col: str = "season"
    round_col: str = "round"
    kickoff_col: str = "kickoff_utc"
    status_col: str = "status_short"
    hidden_columns: tuple[str, ...] = DEFAULT_HIDDEN_COLUMNS

    def __post_init__(self) -> None:
        seasons = tuple(sorted({int(s) for s in self.test_seasons}))
        if not seasons:
            raise ValueError("test_seasons must not be empty")
        object.__setattr__(self, "test_seasons", seasons)
        if self.scope not in ("per_league", "pooled"):
            raise ValueError("scope must be 'per_league' or 'pooled'")
        if self.matchday_method not in ("auto", "round", "week"):
            raise ValueError("matchday_method must be 'auto', 'round' or 'week'")
        if self.select_per not in ("pooled", "league"):
            raise ValueError("select_per must be 'pooled' or 'league'")
        if self.select_per == "league" and self.scope == "pooled":
            raise ValueError("select_per='league' needs scope='per_league'")
        if self.max_round_span_days <= 0:
            raise ValueError("max_round_span_days must be positive")
        if self.result_latency_hours < 0:
            raise ValueError("result_latency_hours must not be negative")
        if self.min_train_matches < 1:
            raise ValueError("min_train_matches must be positive")
        if self.inner_seasons < 1:
            raise ValueError("inner_seasons must be positive")
        object.__setattr__(self, "finished_statuses", tuple(self.finished_statuses))
        object.__setattr__(self, "hidden_columns", tuple(self.hidden_columns))


def assign_matchdays(
    league: np.ndarray,
    season: np.ndarray,
    round_label: np.ndarray | None,
    kickoff_ns: np.ndarray,
    *,
    method: Literal["auto", "round", "week"] = "auto",
    max_round_span_days: float = 7.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Matchday label per match (within league and season) and global week bucket.

    ``method="round"`` groups by the provider's round label.  ``"week"`` groups
    by Monday-based calendar week (UTC).  ``"auto"`` uses rounds but re-splits
    by calendar week every round whose kickoffs span more than
    ``max_round_span_days`` (a postponed match played months later would
    otherwise glue itself to an old round) and uses the week for matches
    without a round label.  Matchday groups only decide *when the model is
    refit*; they never leak information because the training cut-off is the
    group's own first kickoff.

    Returns ``(matchday, week)``: ``matchday`` is a string label
    ``"<league>|<season>|<key>"`` used as the bootstrap/DM cluster, ``week`` is
    the integer week bucket used for pooled refits.
    """
    n = len(kickoff_ns)
    week = (kickoff_ns - _MONDAY_EPOCH_NS) // _NS_PER_WEEK
    base = pd.Series(league.astype(str)) + "|" + pd.Series(season.astype(str))
    if method == "week" or round_label is None:
        key = "w" + pd.Series(week.astype(str))
        return (base + "|" + key).to_numpy(dtype=object), week
    labels = pd.Series(round_label).fillna("").astype(str)
    has_round = (labels != "").to_numpy()
    round_key = base + "|r:" + labels
    weekly_key = base + "|w" + pd.Series(week.astype(str))
    if method == "round":
        matchday = round_key.where(has_round, weekly_key)
        return matchday.to_numpy(dtype=object), week
    stamps = pd.Series(kickoff_ns)
    grouped = stamps.groupby(round_key.to_numpy())
    span = (grouped.transform("max") - grouped.transform("min")).to_numpy()
    wide = span > int(max_round_span_days * _NS_PER_DAY)
    split = wide | ~has_round
    matchday = round_key.where(~split, weekly_key)
    return matchday.to_numpy(dtype=object), week


@dataclass
class PreparedMatches:
    """Finished matches sorted by kickoff plus the aligned helper arrays."""

    frame: pd.DataFrame
    visible: pd.DataFrame
    y: np.ndarray
    t: np.ndarray
    season: np.ndarray
    league: np.ndarray
    matchday: np.ndarray
    week: np.ndarray
    dropped: dict[str, int]
    key: tuple[Any, ...] = ()

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, mask: np.ndarray) -> "PreparedMatches":
        """Rows selected by a boolean mask (order preserved, index reset)."""
        keep = np.flatnonzero(np.asarray(mask, dtype=bool))
        return PreparedMatches(
            frame=self.frame.iloc[keep].reset_index(drop=True),
            visible=self.visible.iloc[keep].reset_index(drop=True),
            y=self.y[keep],
            t=self.t[keep],
            season=self.season[keep],
            league=self.league[keep],
            matchday=self.matchday[keep],
            week=self.week[keep],
            dropped=self.dropped,
            key=self.key,
        )


def _preparation_key(market: Market, config: WalkForwardConfig) -> tuple[Any, ...]:
    """The settings that shape prepared data (a prepared table is reusable only under the same key)."""
    return (
        market.name,
        market.outcome_columns,
        config.matchday_method,
        config.max_round_span_days,
        config.finished_statuses,
        config.id_col,
        config.league_col,
        config.season_col,
        config.round_col,
        config.kickoff_col,
        config.status_col,
        tuple(sorted(config.hidden_columns)),
    )


def prepare_matches(matches: pd.DataFrame, market: Market, config: WalkForwardConfig) -> PreparedMatches:
    """Keep finished matches with a known outcome, parse kickoff, sort, label matchdays.

    Rows that are not finished (postponed, cancelled, awarded, extra time /
    penalties by default), lack a goal count, a kickoff or a season are dropped
    and counted in ``dropped``; nothing is imputed.
    """
    required = [config.id_col, config.league_col, config.season_col, config.kickoff_col, *market.outcome_columns]
    absent = [column for column in required if column not in matches.columns]
    if absent:
        raise ValueError(f"matches is missing required columns: {', '.join(absent)}")
    dropped: dict[str, int] = {"input": len(matches)}
    frame = matches.reset_index(drop=True)
    if config.status_col in frame.columns:
        finished = frame[config.status_col].isin(config.finished_statuses).to_numpy()
        dropped["not_finished"] = int((~finished).sum())
        frame = frame.loc[finished]
    else:
        dropped["not_finished"] = 0
    outcome = market.outcome(frame)
    known = ~np.isnan(outcome)
    dropped["missing_outcome"] = int((~known).sum())
    frame = frame.loc[known]
    outcome = outcome[known]
    kickoff = pd.to_datetime(frame[config.kickoff_col], utc=True, format="mixed", errors="coerce")
    valid_kickoff = kickoff.notna().to_numpy()
    season = pd.to_numeric(frame[config.season_col], errors="coerce")
    valid_season = season.notna().to_numpy()
    valid = valid_kickoff & valid_season
    dropped["missing_kickoff_or_season"] = int((~valid).sum())
    frame = frame.loc[valid].copy()
    outcome = outcome[valid]
    kickoff = kickoff[valid]
    season = season[valid]
    if frame[config.id_col].duplicated().any():
        raise ValueError(f"{config.id_col} must be unique")
    frame["kickoff"] = kickoff.dt.as_unit("ns")
    frame[config.season_col] = season.astype(np.int64)
    t = frame["kickoff"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64")
    order = np.lexsort((frame[config.id_col].to_numpy(), t))
    frame = frame.iloc[order].reset_index(drop=True)
    t = t[order]
    y = outcome[order].astype(np.int64)
    league = frame[config.league_col].astype(str).to_numpy(dtype=object)
    season_values = frame[config.season_col].to_numpy(dtype=np.int64)
    round_label = frame[config.round_col].to_numpy(dtype=object) if config.round_col in frame.columns else None
    matchday, week = assign_matchdays(
        league,
        season_values,
        round_label,
        t,
        method=config.matchday_method,
        max_round_span_days=config.max_round_span_days,
    )
    hidden = [column for column in {*config.hidden_columns, *market.outcome_columns} if column in frame.columns]
    visible = frame.drop(columns=hidden)
    return PreparedMatches(
        frame, visible, y, t, season_values, league, matchday, week, dropped, _preparation_key(market, config)
    )


# ---------------------------------------------------------------------------
# Walk-forward core
# ---------------------------------------------------------------------------


def training_cutoff_ns(group_start_ns: int, latency_ns: int) -> int:
    """Exclusive kickoff bound of the training data for a matchday.

    A match may be used for training only if it kicked off before the
    matchday's first kickoff minus the result latency, i.e. its result was
    known.  Kept as a module-level function so the leakage test can be
    mutation-checked against a deliberately broken cut-off.
    """
    return group_start_ns - latency_ns


def _walk_forward(
    factory: Callable[..., ProbabilityModel],
    params: Mapping[str, Any],
    prep: PreparedMatches,
    market: Market,
    config: WalkForwardConfig,
    seasons: Sequence[int],
    *,
    leagues: set[str] | None = None,
) -> pd.DataFrame:
    """Refit before every matchday of ``seasons`` and forecast it; returns predictions."""
    k = len(market.classes)
    n = len(prep)
    latency = int(config.result_latency_hours * _NS_PER_HOUR)
    probs = np.full((n, k), np.nan)
    n_train = np.full(n, -1, dtype=np.int64)
    group_start = np.full(n, np.iinfo(np.int64).min, dtype=np.int64)
    covered = np.zeros(n, dtype=bool)
    season_set = np.asarray(sorted({int(s) for s in seasons}))

    if config.scope == "per_league":
        unit_indices = []
        for name in pd.unique(prep.league):
            if leagues is not None and name not in leagues:
                continue
            unit_indices.append(np.flatnonzero(prep.league == name))
    else:
        unit_indices = [np.arange(n)]

    for idx in unit_indices:
        in_test = np.isin(prep.season[idx], season_set)
        test_local = np.flatnonzero(in_test)
        if test_local.size == 0:
            continue
        covered[idx[test_local]] = True
        t_unit = prep.t[idx]
        labels = prep.matchday[idx][test_local] if config.scope == "per_league" else prep.week[idx][test_local]
        codes, uniques = pd.factorize(labels)
        n_groups = len(uniques)
        starts = np.full(n_groups, np.iinfo(np.int64).max, dtype=np.int64)
        np.minimum.at(starts, codes, t_unit[test_local])
        by_group = np.argsort(codes, kind="stable")
        bounds = np.searchsorted(codes[by_group], np.arange(n_groups + 1))
        train_frame = prep.frame.iloc[idx]
        visible_frame = prep.visible.iloc[idx]
        for g in np.argsort(starts, kind="stable"):
            rows_local = test_local[by_group[bounds[g] : bounds[g + 1]]]
            rows_global = idx[rows_local]
            start = int(starts[g])
            train_end = int(np.searchsorted(t_unit, training_cutoff_ns(start, latency), side="left"))
            group_start[rows_global] = start
            n_train[rows_global] = train_end
            if train_end < config.min_train_matches:
                continue
            model = factory(**params)
            declared = getattr(model, "classes", None)
            if declared is not None and tuple(str(label) for label in declared) != market.classes:
                raise ValueError(
                    f"model declares classes {tuple(declared)} but the market expects the ordered "
                    f"classes {market.classes}; reorder the probability columns and the declaration"
                )
            model.fit(train_frame.iloc[:train_end])
            forecast = np.asarray(model.predict(visible_frame.iloc[rows_local]), dtype=float)
            if forecast.shape != (len(rows_local), k):
                raise ValueError(
                    f"model predicted shape {forecast.shape}, expected {(len(rows_local), k)} "
                    f"for classes {market.classes}"
                )
            probs[rows_global] = check_probabilities(forecast, name="model forecast")

    chosen = np.flatnonzero(covered)
    frame = prep.frame
    out = pd.DataFrame(
        {
            "fixture_id": frame[config.id_col].to_numpy()[chosen],
            "league_code": prep.league[chosen],
            "season": prep.season[chosen],
            "matchday": prep.matchday[chosen],
            "kickoff": pd.DatetimeIndex(pd.to_datetime(prep.t[chosen], unit="ns", utc=True)),
            "kickoff_ns": prep.t[chosen],
            "group_start_ns": group_start[chosen],
            "n_train": n_train[chosen],
            "y": prep.y[chosen],
        }
    )
    for j, label in enumerate(market.classes):
        out[f"p_{label}"] = probs[chosen, j]
    return out


@dataclass
class WalkForwardResult:
    """Out-of-sample forecasts of one model plus how they were produced."""

    model: str
    market: Market
    config: WalkForwardConfig
    predictions: pd.DataFrame
    selections: pd.DataFrame
    inner_scores: pd.DataFrame
    dropped: dict[str, int]
    elapsed_seconds: float

    @property
    def probability_columns(self) -> list[str]:
        return [f"p_{label}" for label in self.market.classes]

    def probabilities(self) -> np.ndarray:
        return self.predictions[self.probability_columns].to_numpy(dtype=float)

    def outcome(self) -> np.ndarray:
        return self.predictions["y"].to_numpy(dtype=np.int64)

    def scores(self, metrics: Iterable[str] = METRICS) -> pd.DataFrame:
        """Per-match losses next to fixture, league, matchday cluster and kickoff."""
        table = per_match_scores(self.probabilities(), self.outcome(), metrics)
        head = self.predictions[["fixture_id", "league_code", "season", "matchday", "kickoff", "kickoff_ns"]].reset_index(drop=True)
        head["cluster"] = head["matchday"]
        return pd.concat([head, table], axis=1)

    def score_table(self, **kwargs: Any) -> pd.DataFrame:
        """Mean log-loss / Brier / RPS with matchday-bootstrap intervals, per league and pooled."""
        return score_table(self.scores(), **kwargs)

    def corp(
        self,
        *,
        league: str | None = None,
        score: CorpScore = "brier",
        n_sim: int = 0,
        seed: int = 0,
    ) -> pd.DataFrame:
        """CORP ``MCB - DSC + UNC`` decomposition, one-vs-rest per class (last class only if binary)."""
        frame = self.predictions
        if league is not None:
            frame = frame[frame["league_code"] == league]
        valid = frame[self.probability_columns].notna().all(axis=1).to_numpy()
        frame = frame.loc[valid]
        return corp_by_class(
            frame[self.probability_columns].to_numpy(dtype=float),
            frame["y"].to_numpy(),
            self.market.classes,
            score=score,
            n_sim=n_sim,
            seed=seed,
            binary_single=len(self.market.classes) == 2,
        )


def _select_combinations(
    spec: ModelSpec,
    combos: list[dict[str, Any]],
    prep: PreparedMatches,
    market: Market,
    config: WalkForwardConfig,
    outer_season: int,
    available_seasons: np.ndarray,
    cache: dict[tuple[Any, ...], pd.DataFrame],
) -> tuple[dict[str, int], int, pd.DataFrame]:
    """Inner-fold selection for one outer season.

    Returns ``(choice_by_league, pooled_choice, inner_score_rows)``.  Every
    combination is walk-forwarded over the seasons just before
    ``outer_season`` on a copy of the data restricted to seasons before it.
    """
    earlier = available_seasons[available_seasons < outer_season]
    inner = [int(s) for s in earlier[-config.inner_seasons :]]
    if not inner:
        raise ValueError(f"no season before {outer_season} to select hyper-parameters on")
    sub = prep.subset(prep.season < outer_season)
    probability_columns = [f"p_{label}" for label in market.classes]
    frames: list[pd.DataFrame] = []
    for index, params in enumerate(combos):
        parts = []
        for season in inner:
            key = (index, season, outer_season if config.scope == "pooled" else None)
            if key not in cache:
                cache[key] = _walk_forward(spec.factory, params, sub, market, config, [season])
            parts.append(cache[key])
        predictions = pd.concat(parts, ignore_index=True)
        frames.append(
            pd.DataFrame(
                {
                    "fixture_id": predictions["fixture_id"].to_numpy(),
                    "league_code": predictions["league_code"].to_numpy(),
                    "loss": log_loss_per_match(
                        predictions[probability_columns].to_numpy(dtype=float),
                        predictions["y"].to_numpy(dtype=np.int64),
                    ),
                    "combo": index,
                }
            )
        )
    long = pd.concat(frames, ignore_index=True)
    # Compare combinations on the rows every combination forecast (fair pairing).
    forecast_count = long["loss"].notna().groupby(long["fixture_id"]).transform("sum")
    scored = long[(forecast_count == len(combos)) & long["loss"].notna()]
    rows = []
    pooled = scored.groupby("combo")["loss"].agg(["mean", "size"])
    for index, row in pooled.iterrows():
        rows.append({"outer_season": outer_season, "league": "ALL", "combo": int(index), "inner_log_loss": float(row["mean"]), "n": int(row["size"])})
    per_league = scored.groupby(["league_code", "combo"])["loss"].agg(["mean", "size"])
    for (league, index), row in per_league.iterrows():
        rows.append({"outer_season": outer_season, "league": league, "combo": int(index), "inner_log_loss": float(row["mean"]), "n": int(row["size"])})
    inner_scores = pd.DataFrame(rows)
    if pooled.empty:
        raise ValueError(f"no inner forecasts to select hyper-parameters for season {outer_season}")
    pooled_choice = int(pooled["mean"].idxmin())
    choice: dict[str, int] = {}
    if config.select_per == "league":
        by_league = per_league["mean"].unstack("combo")
        for league, row in by_league.iterrows():
            choice[str(league)] = int(row.idxmin()) if row.notna().any() else pooled_choice
    return choice, pooled_choice, inner_scores


def evaluate_model(
    spec: ModelSpec,
    matches: pd.DataFrame | PreparedMatches,
    market: Market,
    config: WalkForwardConfig,
) -> WalkForwardResult:
    """Nested walk-forward evaluation of one model on one market.

    ``matches`` is the raw match table or a :class:`PreparedMatches` (build it
    once with :func:`prepare_matches` to share the work between models).
    """
    started = time.perf_counter()
    if isinstance(matches, PreparedMatches):
        if matches.key != _preparation_key(market, config):
            raise ValueError("prepared matches were built for a different market or configuration")
        prep = matches
    else:
        prep = prepare_matches(matches, market, config)
    combos = spec.combinations()
    seasons = list(config.test_seasons)
    available = np.unique(prep.season)
    selection_rows: list[dict[str, Any]] = []
    inner_frames: list[pd.DataFrame] = []
    if len(combos) == 1:
        predictions = _walk_forward(spec.factory, combos[0], prep, market, config, seasons)
        predictions["params"] = _params_key(combos[0])
        for season in seasons:
            selection_rows.append({"outer_season": season, "league": "ALL", "params": _params_key(combos[0]), "inner_log_loss": np.nan})
    else:
        cache: dict[tuple[Any, ...], pd.DataFrame] = {}
        outer_parts: list[pd.DataFrame] = []
        for season in seasons:
            choice, pooled_choice, inner_scores = _select_combinations(
                spec, combos, prep, market, config, season, available, cache
            )
            inner_scores = inner_scores.assign(params=[_params_key(combos[i]) for i in inner_scores["combo"]]).drop(columns="combo")
            inner_frames.append(inner_scores)
            if config.select_per == "pooled":
                groups = {pooled_choice: None}
                selection_rows.append(
                    {
                        "outer_season": season,
                        "league": "ALL",
                        "params": _params_key(combos[pooled_choice]),
                        "inner_log_loss": float(
                            inner_scores[(inner_scores["league"] == "ALL") & (inner_scores["params"] == _params_key(combos[pooled_choice]))]["inner_log_loss"].iloc[0]
                        ),
                    }
                )
            else:
                groups = {}
                for league in pd.unique(prep.league[prep.season == season]):
                    picked = choice.get(str(league), pooled_choice)
                    groups.setdefault(picked, set()).add(str(league))
                    selection_rows.append(
                        {"outer_season": season, "league": str(league), "params": _params_key(combos[picked]), "inner_log_loss": np.nan}
                    )
            for picked, league_set in groups.items():
                part = _walk_forward(spec.factory, combos[picked], prep, market, config, [season], leagues=league_set)
                part["params"] = _params_key(combos[picked])
                outer_parts.append(part)
        predictions = pd.concat(outer_parts, ignore_index=True)
    predictions = predictions.sort_values(["kickoff_ns", "fixture_id"], kind="stable").reset_index(drop=True)
    elapsed = time.perf_counter() - started
    return WalkForwardResult(
        model=spec.name,
        market=market,
        config=config,
        predictions=predictions,
        selections=pd.DataFrame(selection_rows),
        inner_scores=pd.concat(inner_frames, ignore_index=True) if inner_frames else pd.DataFrame(),
        dropped=prep.dropped,
        elapsed_seconds=elapsed,
    )


def evaluate_models(
    specs: Iterable[ModelSpec],
    matches: pd.DataFrame,
    market: Market,
    config: WalkForwardConfig,
) -> dict[str, WalkForwardResult]:
    """Evaluate several models on identical prepared data; results keyed by model name."""
    prep = prepare_matches(matches, market, config)
    results: dict[str, WalkForwardResult] = {}
    for spec in specs:
        if spec.name in results:
            raise ValueError(f"duplicate model name: {spec.name}")
        results[spec.name] = evaluate_model(spec, prep, market, config)
    return results


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


class LeagueFrequencyBaseline:
    """Outcome frequencies per league, estimated on the training matches only.

    Laplace smoothing (``alpha`` pseudo-matches per class, default 1) keeps
    every probability positive and is negligible next to hundreds of matches.
    A league not present in the training data gets a NaN forecast (missing
    stays missing).
    """

    def __init__(self, market: Market, *, league_col: str = "league_code", alpha: float = 1.0) -> None:
        if alpha < 0:
            raise ValueError("alpha must not be negative")
        self.market = market
        self.classes = market.classes
        self.league_col = league_col
        self.alpha = float(alpha)
        self._leagues: pd.Index | None = None
        self._probabilities: np.ndarray | None = None

    def fit(self, train: pd.DataFrame) -> "LeagueFrequencyBaseline":
        k = len(self.classes)
        y = self.market.outcome(train)
        known = ~np.isnan(y)
        codes, leagues = pd.factorize(train[self.league_col].to_numpy())
        counts = np.bincount(codes[known] * k + y[known].astype(np.int64), minlength=len(leagues) * k)
        counts = counts.reshape(len(leagues), k).astype(float) + self.alpha
        self._leagues = pd.Index(leagues)
        self._probabilities = counts / counts.sum(axis=1, keepdims=True)
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self._leagues is None or self._probabilities is None:
            raise RuntimeError("fit must be called before predict")
        position = self._leagues.get_indexer(frame[self.league_col].to_numpy())
        out = np.full((len(frame), len(self.classes)), np.nan)
        known = position >= 0
        out[known] = self._probabilities[position[known]]
        return out


class GlobalFrequencyBaseline:
    """Outcome frequencies over all training matches (one forecast for every match)."""

    def __init__(self, market: Market, *, alpha: float = 1.0) -> None:
        if alpha < 0:
            raise ValueError("alpha must not be negative")
        self.market = market
        self.classes = market.classes
        self.alpha = float(alpha)
        self._probabilities: np.ndarray | None = None

    def fit(self, train: pd.DataFrame) -> "GlobalFrequencyBaseline":
        y = self.market.outcome(train)
        y = y[~np.isnan(y)].astype(np.int64)
        counts = np.bincount(y, minlength=len(self.classes)).astype(float) + self.alpha
        self._probabilities = counts / counts.sum()
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self._probabilities is None:
            raise RuntimeError("fit must be called before predict")
        return np.tile(self._probabilities, (len(frame), 1))


def league_frequency_spec(market: Market, *, alpha: float = 1.0) -> ModelSpec:
    """The standard baseline: league outcome frequencies from training data only."""
    return ModelSpec("league_frequencies", lambda: LeagueFrequencyBaseline(market, alpha=alpha))


def global_frequency_spec(market: Market, *, alpha: float = 1.0) -> ModelSpec:
    """Reference: one set of outcome frequencies for all leagues (use with ``scope='pooled'``)."""
    return ModelSpec("global_frequencies", lambda: GlobalFrequencyBaseline(market, alpha=alpha))


# ---------------------------------------------------------------------------
# Model versus baseline
# ---------------------------------------------------------------------------


def compare_to_baseline(
    model: WalkForwardResult,
    baseline: WalkForwardResult,
    *,
    metric: str = "log_loss",
    alternative: Literal["two-sided", "greater"] = "two-sided",
    n_resamples: int = 2_000,
    confidence_level: float = 0.95,
    lags: int | None = None,
    seed: int = 0,
    pooled_label: str = "ALL",
) -> pd.DataFrame:
    """Paired comparison of ``model`` with ``baseline`` per league and pooled.

    Loss difference ``d = baseline_loss - model_loss`` per match, so a positive
    ``mean_diff`` means the model is better.  Per league: matchday-cluster
    bootstrap interval, Diebold-Mariano test with HAC variance over matchdays,
    and Holm / Benjamini-Hochberg adjusted p-values across the leagues in the
    table.  The pooled row is descriptive and not part of the corrected family.
    Only matches forecast by both models are compared.  ``alternative="greater"``
    uses the one-sided p-value "the model is better".
    """
    if model.market.classes != baseline.market.classes:
        raise ValueError("model and baseline must evaluate the same market")
    a = model.scores([metric]).rename(columns={metric: "model_loss"})
    b = baseline.scores([metric])[["fixture_id", metric]].rename(columns={metric: "baseline_loss"})
    both = a.merge(b, on="fixture_id", how="inner").dropna(subset=["model_loss", "baseline_loss"])
    both["diff"] = both["baseline_loss"] - both["model_loss"]
    both = both.sort_values(["kickoff_ns", "fixture_id"], kind="stable")
    tail = (1.0 - confidence_level) / 2.0

    def one(label: str, frame: pd.DataFrame) -> dict[str, Any]:
        d = frame["diff"].to_numpy(dtype=float)
        clusters = frame["cluster"].to_numpy()
        row: dict[str, Any] = {"league_code": label, "n": len(d), "n_clusters": int(frame["cluster"].nunique())}
        if len(d) == 0:
            row.update(mean_diff=np.nan, ci_lo=np.nan, ci_hi=np.nan, dm_stat=np.nan, p_value=np.nan)
            return row
        interval = cluster_bootstrap_mean_ci(
            d, clusters, confidence_level=confidence_level, n_resamples=n_resamples, seed=seed
        )
        test = diebold_mariano(d, clusters, order=frame["kickoff_ns"].to_numpy(), lags=lags)
        row.update(
            mean_diff=interval.estimate,
            ci_lo=interval.lower,
            ci_hi=interval.upper,
            dm_stat=test.statistic,
            p_value=test.p_value if alternative == "two-sided" else test.p_greater,
        )
        return row

    rows = [one(str(label), frame) for label, frame in both.groupby("league_code", sort=True)]
    table = pd.DataFrame(rows)
    if len(table):
        table["p_holm"] = holm_adjust(table["p_value"].to_numpy())
        table["p_bh"] = benjamini_hochberg_adjust(table["p_value"].to_numpy())
    else:
        table["p_holm"] = table["p_bh"] = np.nan
    pooled = one(pooled_label, both)
    pooled["p_holm"] = pooled["p_bh"] = np.nan
    table = pd.concat([table, pd.DataFrame([pooled])], ignore_index=True)
    table.attrs.update(metric=metric, alternative=alternative, confidence_level=confidence_level)
    return table


# ---------------------------------------------------------------------------
# Leakage test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakageModeResult:
    """Outcome of the look-ahead comparison for one way of disturbing the future."""

    mode: str
    n_future_rows_changed: int
    n_forecasts_changed: int
    max_abs_difference: float

    @property
    def passed(self) -> bool:
        return self.n_forecasts_changed == 0


@dataclass(frozen=True)
class LeakageReport:
    """Result of the look-ahead test (see :func:`check_no_lookahead`)."""

    passed: bool
    cutoff: pd.Timestamp
    n_compared: int
    modes: tuple[LeakageModeResult, ...]

    @property
    def n_forecasts_changed(self) -> int:
        return max((mode.n_forecasts_changed for mode in self.modes), default=0)

    @property
    def max_abs_difference(self) -> float:
        return max((mode.max_abs_difference for mode in self.modes), default=0.0)


_MIRROR_PAIRS = (
    ("home_goals", "away_goals"),
    ("ht_home_goals", "ht_away_goals"),
    ("et_home_goals", "et_away_goals"),
    ("pen_home", "pen_away"),
)


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def permute_future_outcomes(
    matches: pd.DataFrame,
    market: Market,
    cutoff: str | pd.Timestamp,
    *,
    mode: Literal["shuffle", "mirror"] = "shuffle",
    kickoff_col: str = "kickoff_utc",
    seed: int = 0,
) -> tuple[pd.DataFrame, int]:
    """Disturb the outcomes of matches kicking off at or after ``cutoff``.

    ``mode="shuffle"`` permutes the outcome columns jointly among those matches
    (class frequencies of the whole future are kept, but every match gets
    someone else's result).  ``mode="mirror"`` swaps home and away scores of
    every such match, which also changes *global* frequencies (home advantage)
    that a shuffle cannot touch.  Rows keep their identity (teams, kickoff,
    round).  Only matches with a known outcome are touched.  Returns the
    disturbed copy and the number of future matches whose outcome class changed.
    """
    stamp = _utc(cutoff)
    kickoff = pd.to_datetime(matches[kickoff_col], utc=True, format="mixed", errors="coerce")
    known = ~np.isnan(market.outcome(matches))
    future = np.flatnonzero(((kickoff >= stamp).to_numpy()) & known)
    if len(future) < 2:
        raise ValueError("fewer than two matches at or after the cutoff; nothing to disturb")
    permuted = matches.copy()
    if mode == "shuffle":
        columns = [
            c
            for c in dict.fromkeys([*market.outcome_columns, *(c for pair in _MIRROR_PAIRS for c in pair)])
            if c in matches.columns
        ]
        order = np.random.default_rng(seed).permutation(len(future))
        block = matches.iloc[future][columns].reset_index(drop=True).iloc[order].reset_index(drop=True)
        for column in columns:
            permuted.iloc[future, permuted.columns.get_loc(column)] = block[column].to_numpy()
    elif mode == "mirror":
        for left, right in _MIRROR_PAIRS:
            if left in matches.columns and right in matches.columns:
                left_values = matches.iloc[future][left].to_numpy()
                right_values = matches.iloc[future][right].to_numpy()
                permuted.iloc[future, permuted.columns.get_loc(left)] = right_values
                permuted.iloc[future, permuted.columns.get_loc(right)] = left_values
    else:
        raise ValueError("mode must be 'shuffle' or 'mirror'")
    before = market.outcome(matches.iloc[future])
    after = market.outcome(permuted.iloc[future])
    changed = int(np.sum(~np.isnan(before) & ~np.isnan(after) & (before != after)))
    return permuted, changed


def check_no_lookahead(
    run: Callable[[pd.DataFrame], WalkForwardResult],
    matches: pd.DataFrame,
    market: Market,
    cutoff: str | pd.Timestamp,
    *,
    modes: Sequence[Literal["shuffle", "mirror"]] = ("shuffle", "mirror"),
    kickoff_col: str = "kickoff_utc",
    seed: int = 0,
    tolerance: float = 1e-12,
) -> LeakageReport:
    """Leakage test: disturbing future outcomes must not change earlier forecasts.

    ``run(matches)`` must execute the complete evaluation (nested selection
    included); it is called once on the original table and once per mode on a
    copy in which the outcomes of every match kicking off at or after
    ``cutoff`` are shuffled (and, in mirror mode, home/away swapped).  Every
    forecast whose matchday *started* at or before ``cutoff`` has to be identical in
    both runs, including the forecasts for matches of that matchday that kick
    off after the cutoff (so a model peeking at the outcome of the match it
    forecasts is caught too).  A difference means that something (a model, or
    the framework itself) looked at outcomes that were not yet known.  Modes
    that leave every future outcome unchanged (e.g. mirror on over/under) are
    skipped; at least one mode has to apply.  Pass
    ``lambda m: evaluate_model(spec, m, market, config)`` as ``run``.
    """
    stamp = _utc(cutoff)
    original = run(matches)
    columns = original.probability_columns
    left = original.predictions.set_index("fixture_id")
    early = left.index[left["group_start_ns"].to_numpy() <= stamp.value]
    if len(early) == 0:
        raise ValueError("no forecasts from matchdays starting at or before the cutoff to compare")
    results: list[LeakageModeResult] = []
    for mode in modes:
        disturbed, changed = permute_future_outcomes(
            matches, market, stamp, mode=mode, kickoff_col=kickoff_col, seed=seed
        )
        if changed == 0:
            continue
        right = run(disturbed).predictions.set_index("fixture_id")
        missing_after = early.difference(right.index)
        if len(missing_after):
            raise LookaheadLeakError(
                f"{len(missing_after)} early forecasts disappeared after disturbing future outcomes ({mode})"
            )
        a = left.loc[early, columns].to_numpy(dtype=float)
        b = right.loc[early, columns].to_numpy(dtype=float)
        difference = np.abs(np.nan_to_num(a, nan=0.0) - np.nan_to_num(b, nan=0.0))
        difference[np.isnan(a) != np.isnan(b)] = np.inf
        row_difference = difference.max(axis=1)
        results.append(
            LeakageModeResult(
                mode=mode,
                n_future_rows_changed=changed,
                n_forecasts_changed=int((row_difference > tolerance).sum()),
                max_abs_difference=float(row_difference.max()),
            )
        )
    if not results:
        raise ValueError("no leakage mode changed any future outcome; choose another cutoff or seed")
    return LeakageReport(
        passed=all(result.passed for result in results),
        cutoff=stamp,
        n_compared=len(early),
        modes=tuple(results),
    )


def assert_no_lookahead(*args: Any, **kwargs: Any) -> LeakageReport:
    """Like :func:`check_no_lookahead` but raises :class:`LookaheadLeakError` on failure."""
    report = check_no_lookahead(*args, **kwargs)
    if not report.passed:
        detail = ", ".join(
            f"{m.mode}: {m.n_forecasts_changed} changed (max diff {m.max_abs_difference:.3g})"
            for m in report.modes
            if not m.passed
        )
        raise LookaheadLeakError(
            f"forecasts of {report.n_compared} matches from matchdays before {report.cutoff} "
            f"depend on later outcomes ({detail})"
        )
    return report


__all__ = [
    "DEFAULT_HIDDEN_COLUMNS",
    "GlobalFrequencyBaseline",
    "LeagueFrequencyBaseline",
    "LeakageModeResult",
    "LeakageReport",
    "LookaheadLeakError",
    "MARKET_1X2",
    "Market",
    "ModelSpec",
    "PreparedMatches",
    "ProbabilityModel",
    "WalkForwardConfig",
    "WalkForwardResult",
    "assert_no_lookahead",
    "assign_matchdays",
    "check_no_lookahead",
    "compare_to_baseline",
    "evaluate_model",
    "evaluate_models",
    "global_frequency_spec",
    "league_frequency_spec",
    "over_under",
    "permute_future_outcomes",
    "prepare_matches",
    "training_cutoff_ns",
]
