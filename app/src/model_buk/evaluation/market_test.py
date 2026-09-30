"""Model versus market after tax (plan, days 4-5): question 2 and, as an approximation, question 3.

Question 2 ("does the model know more than the market?") is answered on the football-data odds that
:mod:`model_buk.ingestion.fd_af_join` paired one to one with API-Football fixtures
(``joined/fd_af_matches.sqlite``) and on the stored out-of-sample forecasts of the goal protocol
(:mod:`model_buk.evaluation.protocol`).  Question 3 ("does it make money at a Polish bookmaker after tax?") can
only be approximated, see "Polish bookmaker" below.

What is measured
----------------
1. **Forecast quality against the market** (1X2 and over/under 2.5): log-loss of every model versus the
   de-vigged Pinnacle *opening* snapshot (``PSH/PSD/PSA``, ``P>2.5/P<2.5``: the only price that exists at decision
   time) and versus the Pinnacle *closing* line, on identical matches, per league and pooled, under all three de-vig
   methods, with matchday-cluster Diebold-Mariano tests and Holm / Benjamini-Hochberg across leagues
   (:func:`model_vs_market`).
2. **Pooling with the market and the market-residual model** (:func:`pooled_with_market`,
   :func:`residual_forecasts_1x2`): a log-linear pool of the de-vigged opening line and the model whose weight is fitted on
   *earlier* seasons only, and the multinomial residual model of :mod:`model_buk.models.market_residual` (1X2 only;
   the module is three-class).
3. **Betting strategies** on opening prices only (:func:`run_cell`): (a) *reference*, a soft-book price that beats the
   de-vigged Pinnacle opening line by more than the uncertainty haircut, (b) *reference with model veto* and
   (c) *pooling* / *residual* (the probability of the pool or of the residual model replaces the reference).  A model-only
   strategy is kept as a control.  Singles and simulated accumulators of 2-5 legs, three tax regimes
   (tau = 0.88 / 0.94 / 1.00), flat stake and taxed quarter Kelly.
4. **Statistics** (:func:`summarise_bets`): post-tax yield and closing line value on the price taken (mean, median,
   share of closing lines beaten) with matchday-cluster bootstrap intervals, one-sided bootstrap p-values, maximum
   drawdown, all under every de-vig method, and a random-betting baseline with the *same* selection rate and odds
   band (:func:`random_baseline`).  The lower bound of the post-tax CLV interval is what the plan's criterion reads.

Decision math is not re-implemented here: de-vig, EV, tax multipliers, CLV and accumulator EV come from
:mod:`model_buk.markets.pricing`, staking, the PIT threshold and taxed Kelly from :mod:`model_buk.markets.staking`, the
lower-bound EV gate from :mod:`model_buk.decision` and one-pick-per-match from :mod:`model_buk.opportunity_selector`.

No look-ahead
-------------
A decision may read only the *open* columns of a :class:`LegTable`: opening odds of the bookmaker, the de-vigged
opening reference and the stored forecasts (which are out-of-sample by protocol).  The closing line and the outcome
live in the separate ``settle`` frame and are read only after the picks are made, for CLV and settlement.
:func:`assert_open_only` refuses frames that carry closing or outcome columns, and the tests scramble the closing
columns and check that no pick changes (and that a deliberately broken reference source is caught).  Pool weights, the
residual model and the drift scale of the uncertainty haircut are always fitted on seasons strictly before the season
they are applied to.

Timing of the football-data prices
----------------------------------
The "opening" columns are a snapshot taken once when the weekly file is built (Friday afternoon for Friday to Monday
matches, Tuesday afternoon for Tuesday to Thursday matches; the mean move of Pinnacle to the close is smallest for
Friday and Tuesday kickoffs and larger for the other weekdays, which supports this reading).  All legs of one
:func:`snapshot_window` are therefore priced at the same instant, and only such legs are combined into one accumulator.
The margin baked into the Pinnacle opening columns changed between seasons (about 3.2% in 2022, 4.8% in 2024): CLV
comparisons across seasons are not safe, comparisons within a season are.

Polish bookmaker (question 3, approximation)
--------------------------------------------
No Polish price is archived for the test seasons, and the live archive (``api-football-odds``) holds no Polish-licensed
book either.  Two synthetic books are used, both labelled assumptions: ``pl_ps`` is the Pinnacle opening price with the
Pinnacle overround replaced by an assumed Polish overround (no idiosyncratic mispricing: only real model information
can create value), ``pl_b365`` is the Bet365 price re-based to the same overround (keeps a soft book's deviations from
Pinnacle).  The assumed overround (default 6%) is the level measured on the ten football-data soft books (Audyt 33:
6.8% mean, 6.2% median) and on Bet365 here (5.7% to 7.3% for 1X2).

Missing stays missing: a missing price or forecast removes the leg from a decision, it is never filled with zero, and a
match without a closing line is kept in yield but reported as missing in CLV.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from model_buk.decision import EvGateConfig, assess_bet
from model_buk.evaluation.framework import MARKET_1X2, Market, over_under
from model_buk.evaluation.model_selection import (
    POOLED_LABEL,
    apply_log_linear_pool,
    fit_log_linear_pool,
    paired_comparison,
)
from model_buk.evaluation.protocol import DATA_ROOT, load_predictions
from model_buk.evaluation.scoring import log_loss_per_match
from model_buk.evaluation.significance import (
    benjamini_hochberg_adjust,
    cluster_bootstrap_means,
    holm_adjust,
)
from model_buk.markets.pricing import (
    DEVIG_METHODS,
    TaxProfiles,
    AccumulatorLeg,
    accumulator_ev_net,
    closing_line_value,
    devig_probabilities,
    probability_lower_bound,
)
from model_buk.markets.staking import (
    DEFAULT_PIT_RULE,
    fractional_kelly_fraction,
    max_stake_below_pit,
    net_payout,
    plan_stake,
)
from model_buk.opportunity_selector import OpportunityCandidate, select_opportunities

JOINED_DB = DATA_ROOT / "joined" / "fd_af_matches.sqlite"
LIVE_ODDS_DB = DATA_ROOT / "api-football-odds" / "odds.sqlite3"
HISTORY_DB = DATA_ROOT / "api-football" / "tables" / "history.sqlite"
OUTPUT_DIR = DATA_ROOT / "market_test"
RESULTS_DB = OUTPUT_DIR / "market_test.sqlite"

DEVIG_ORDER: tuple[str, ...] = ("proportional", "power", "shin")
if set(DEVIG_ORDER) != set(DEVIG_METHODS):  # a new method in pricing must be added here on purpose
    raise RuntimeError("DEVIG_ORDER is out of date with model_buk.markets.pricing.DEVIG_METHODS")

TAX_REGIMES: Mapping[str, float] = {"tau_0.88": 0.88, "tau_0.94": 0.94, "tau_1.00": 1.00}
"""Stake-tax multipliers of the three regimes: Polish standard, Betclic degraded, tax free."""

FIRST_FORECAST_SEASON = 2022
"""First outer test season of the protocol: stored forecasts exist from here on."""
SELECTION_SEASONS: tuple[int, ...] = (2023, 2024, 2025)
"""Seasons for which an ex-ante model choice exists (season 2022 is the selection season)."""
DRIFT_LOOKBACK_SEASONS = 3
DEFAULT_Z = 1.645
"""One-sided 95% haircut on the reference probability (the plan's EV gate with uncertainty)."""
KELLY_MULTIPLIER = 0.25
KELLY_BANKROLL_PLN = 10_000.0
KELLY_MAX_FRACTION = 0.02
FLAT_STAKE_PLN = 100.0
DEFAULT_POLISH_OVERROUND = 1.06
MIN_BETS_FOR_INTERVAL = 20
MIN_CLUSTERS_FOR_INTERVAL = 10
ODDS_BANDS: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0, 5.0, np.inf)

BOOKS: tuple[str, ...] = ("bet365", "max", "avg", "pl_ps", "pl_b365")
STRATEGIES: tuple[str, ...] = ("reference", "reference_veto", "pool", "residual", "model")

OPEN_FAIR_SOURCE = "ps_open"
"""Which fair line feeds the decision: it must stay the *opening* one (the leak test patches it to prove that)."""


# --------------------------------------------------------------------------- #
# Markets and odds columns
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MarketSpec:
    """A market with its football-data columns (class order equals the protocol's :class:`Market`)."""

    name: str
    market: Market
    ps_open: tuple[str, ...]
    ps_close: tuple[str, ...]
    books: Mapping[str, tuple[str, ...]]
    labels: tuple[str, ...]

    @property
    def n_classes(self) -> int:
        return len(self.ps_open)

    def odds_columns(self) -> tuple[str, ...]:
        columns = [*self.ps_open, *self.ps_close]
        for cols in self.books.values():
            columns.extend(cols)
        return tuple(dict.fromkeys(columns))


MARKET_SPECS: Mapping[str, MarketSpec] = {
    "1x2": MarketSpec(
        "1x2",
        MARKET_1X2,
        ps_open=("PSH", "PSD", "PSA"),
        ps_close=("PSCH", "PSCD", "PSCA"),
        books={
            "bet365": ("B365H", "B365D", "B365A"),
            "max": ("MaxH", "MaxD", "MaxA"),
            "avg": ("AvgH", "AvgD", "AvgA"),
        },
        labels=("home", "draw", "away"),
    ),
    "over_under_2.5": MarketSpec(
        "over_under_2.5",
        over_under(2.5),
        ps_open=("P<2.5", "P>2.5"),
        ps_close=("PC<2.5", "PC>2.5"),
        books={
            "bet365": ("B365<2.5", "B365>2.5"),
            "max": ("Max<2.5", "Max>2.5"),
            "avg": ("Avg<2.5", "Avg>2.5"),
        },
        labels=("under", "over"),
    ),
}
MARKET_ORDER: tuple[str, ...] = ("1x2", "over_under_2.5")


def _quote(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def load_joined_odds(
    path: str | Path = JOINED_DB,
    *,
    seasons: Iterable[int] | None = None,
    markets: Iterable[str] = MARKET_ORDER,
) -> pd.DataFrame:
    """Read the football-data odds joined to API-Football fixtures (read-only).

    One row per fixture: ``fixture_id`` (str), ``league`` (API-Football league code), ``season`` (API-Football
    season label, the one the protocol uses), ``kickoff`` (UTC), the API-Football goals and every odds column of
    the requested markets.  A price stored as NULL (missing, or 0 / below 1.0 in the source) stays NaN.
    """
    columns: list[str] = []
    for name in markets:
        columns.extend(MARKET_SPECS[name].odds_columns())
    columns = list(dict.fromkeys(columns))
    select = ", ".join(_quote(c) for c in columns)
    query = (
        "SELECT fixture_id, af_league_code AS league, af_season AS season, af_kickoff_utc AS kickoff, "
        f"af_home_goals AS home_goals, af_away_goals AS away_goals, {select} FROM fd_af_matches"
    )
    params: list[int] = []
    if seasons is not None:
        wanted = sorted({int(s) for s in seasons})
        query += " WHERE af_season IN (" + ",".join("?" for _ in wanted) + ")"
        params = wanted
    with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    frame["fixture_id"] = frame["fixture_id"].astype(str)
    frame["kickoff"] = pd.to_datetime(frame["kickoff"], utc=True, format="mixed")
    frame["season"] = frame["season"].astype(int)
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values(["kickoff", "fixture_id"], kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# De-vig, snapshot windows
# --------------------------------------------------------------------------- #


def devig_matrix(odds: np.ndarray, method: str) -> np.ndarray:
    """Row-wise fair probabilities of complete books with :func:`~model_buk.markets.pricing.devig_probabilities`.

    A row with a missing price or a price at or below 1.0 stays an all-NaN row: an incomplete book cannot be
    de-vigged and is never completed with a guess.
    """
    matrix = np.asarray(odds, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("odds must be a two-dimensional (rows, outcomes) array")
    if method not in DEVIG_METHODS:
        raise ValueError(f"unknown de-vig method {method!r}")
    out = np.full(matrix.shape, np.nan)
    complete = np.isfinite(matrix).all(axis=1) & (matrix > 1.0).all(axis=1)
    if not complete.any():
        return out
    rows = matrix[complete]
    unique, inverse = np.unique(rows, axis=0, return_inverse=True)
    solved = np.array([devig_probabilities(tuple(row), method) for row in unique])
    out[complete] = solved[np.asarray(inverse).reshape(-1)]
    return out


def snapshot_window(kickoff: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Label of the football-data snapshot that priced each match.

    Friday to Monday kickoffs belong to the Friday snapshot (label ``"F<date>"``), Tuesday to Thursday kickoffs to the
    Tuesday snapshot (``"T<date>"``).  Legs of one window share one snapshot instant.
    """
    stamps = pd.to_datetime(pd.Series(kickoff), utc=True)
    day = stamps.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    weekday = day.dt.dayofweek.to_numpy()  # Monday = 0
    back = np.select([weekday == 4, weekday == 5, weekday == 6, weekday == 0], [0, 1, 2, 3], default=-1)
    tuesday_back = np.select([weekday == 1, weekday == 2, weekday == 3], [0, 1, 2], default=-1)
    label = np.where(back >= 0, "F", "T")
    offset = np.where(back >= 0, back, tuesday_back)
    start = day - pd.to_timedelta(offset, unit="D")
    return np.asarray(label + start.dt.strftime("%Y-%m-%d").to_numpy(), dtype=object)


def window_datetime(window: str) -> datetime:
    """The (approximate) snapshot instant of a window label: 12:00 UTC on its Friday or Tuesday."""
    return datetime.strptime(window[1:], "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Market panel: one row per fixture
# --------------------------------------------------------------------------- #


@dataclass
class MarketPanel:
    """Odds and de-vigged lines of one market, one row per fixture, sorted by kickoff.

    ``odds`` holds ``ps_open``, ``ps_close`` and the books as ``(n, K)`` arrays (NaN = missing); ``fair`` maps
    ``(source, method)`` for ``source`` in ``ps_open`` / ``ps_close`` to the de-vigged line.  ``meta`` carries
    ``fixture_id, league, season, kickoff, matchday, group, y`` (``y`` is NaN where the outcome is unknown).
    """

    spec: MarketSpec
    meta: pd.DataFrame
    odds: dict[str, np.ndarray]
    fair: dict[tuple[str, str], np.ndarray]

    def __len__(self) -> int:
        return len(self.meta)

    @property
    def y(self) -> np.ndarray:
        return self.meta["y"].to_numpy(dtype=float)

    @property
    def season(self) -> np.ndarray:
        return self.meta["season"].to_numpy(dtype=np.int64)

    @property
    def fixture_ids(self) -> np.ndarray:
        return self.meta["fixture_id"].to_numpy(dtype=object)


def build_market_panel(
    odds_frame: pd.DataFrame,
    spec: MarketSpec,
    *,
    matchday: Mapping[str, str] | pd.Series | None = None,
    methods: Sequence[str] = DEVIG_ORDER,
) -> MarketPanel:
    """Turn the joined odds table into a :class:`MarketPanel`.

    ``matchday`` maps ``fixture_id`` to the framework's matchday label (the DM / bootstrap cluster of the
    protocol).  Fixtures without one (seasons before the protocol) get a calendar-week label of their league.
    """
    frame = odds_frame.sort_values(["kickoff", "fixture_id"], kind="stable").reset_index(drop=True)
    meta = frame[["fixture_id", "league", "season", "kickoff"]].copy()
    stamps = pd.to_datetime(meta["kickoff"], utc=True)
    week = stamps.dt.tz_convert("UTC").dt.tz_localize(None).dt.strftime("%G-W%V")
    fallback = meta["league"].astype(str) + "|" + meta["season"].astype(str) + "|" + week
    if matchday is not None:
        mapped = meta["fixture_id"].map(dict(matchday) if not isinstance(matchday, dict) else matchday)
        meta["matchday"] = mapped.where(mapped.notna(), fallback)
    else:
        meta["matchday"] = fallback
    meta["group"] = snapshot_window(stamps)
    meta["y"] = spec.market.outcome(frame)
    odds: dict[str, np.ndarray] = {
        "ps_open": frame[list(spec.ps_open)].to_numpy(dtype=float),
        "ps_close": frame[list(spec.ps_close)].to_numpy(dtype=float),
    }
    for book, columns in spec.books.items():
        odds[book] = frame[list(columns)].to_numpy(dtype=float)
    fair = {
        (source, method): devig_matrix(odds[source], method)
        for source in ("ps_open", "ps_close")
        for method in methods
    }
    return MarketPanel(spec, meta, odds, fair)


def save_panel_cache(panel: MarketPanel, path: str | Path) -> None:
    """Pickle the arrays of a panel (a local cache; the :class:`Market` object itself is not picklable)."""
    import pickle

    state = {"name": panel.spec.name, "meta": panel.meta, "odds": panel.odds, "fair": panel.fair}
    with open(path, "wb") as handle:
        pickle.dump(state, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_panel_cache(path: str | Path) -> MarketPanel:
    """Load a panel written by :func:`save_panel_cache` (only ever call this on a file this code wrote)."""
    import pickle

    with open(path, "rb") as handle:
        state = pickle.load(handle)
    return MarketPanel(MARKET_SPECS[state["name"]], state["meta"], state["odds"], state["fair"])


def polish_odds(
    panel: MarketPanel,
    kind: str,
    overround: float = DEFAULT_POLISH_OVERROUND,
) -> np.ndarray:
    """Synthetic Polish prices (an assumption, see the module docstring).

    ``kind = "pl_ps"``: Pinnacle opening odds times ``O_ps / overround`` (proportional re-margining: the fair
    distribution is Pinnacle's, only the margin is Polish).  ``kind = "pl_b365"``: the same with Bet365's opening
    odds and Bet365's own overround, so a soft book's deviations from Pinnacle are kept at the Polish margin.
    """
    if kind not in ("pl_ps", "pl_b365"):
        raise ValueError("kind must be 'pl_ps' or 'pl_b365'")
    if not math.isfinite(overround) or overround <= 1.0:
        raise ValueError("overround must be a finite number above 1.0 (1.06 = 6% margin)")
    source = panel.odds["ps_open" if kind == "pl_ps" else "bet365"]
    with np.errstate(invalid="ignore", divide="ignore"):
        book_sum = (1.0 / source).sum(axis=1, keepdims=True)
        scaled = source * book_sum / overround
    scaled[~(np.isfinite(scaled).all(axis=1) & (scaled > 1.0).all(axis=1))] = np.nan
    return scaled


def overround_table(panel: MarketPanel) -> pd.DataFrame:
    """Mean opening/closing overround per source and season (only fully priced books), for the margin discussion."""
    rows = []
    for source, matrix in panel.odds.items():
        with np.errstate(invalid="ignore", divide="ignore"):
            over = (1.0 / matrix).sum(axis=1) - 1.0
        over[~(np.isfinite(matrix).all(axis=1) & (matrix > 1.0).all(axis=1))] = np.nan
        frame = pd.DataFrame({"season": panel.season, "overround": over}).dropna()
        for season, part in frame.groupby("season"):
            rows.append(
                {
                    "market": panel.spec.name,
                    "source": source,
                    "season": int(season),
                    "n": int(len(part)),
                    "mean_overround": float(part["overround"].mean()),
                    "median_overround": float(part["overround"].median()),
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Uncertainty of the reference: drift of the sharp line to the close
# --------------------------------------------------------------------------- #


def drift_scale(
    fair_open: np.ndarray,
    fair_close: np.ndarray,
    season: np.ndarray,
    target_season: int,
    *,
    lookback: int = DRIFT_LOOKBACK_SEASONS,
) -> float:
    """Scale ``c`` of the standard error ``SE(p) = c * sqrt(p * (1 - p))`` of a reference probability.

    ``c`` is the root mean square of ``(p_close - p_open) / sqrt(p_open * (1 - p_open))`` over the ``lookback``
    seasons *before* ``target_season`` (rows with both lines): how far the sharp line moves between the snapshot
    and the close, the measured uncertainty of the decision-time reference (plan: "sigma of the reference at scan
    time against the reference at the close").  Later seasons are never read.  ``nan`` if no earlier row exists.
    """
    seasons = np.asarray(season, dtype=np.int64)
    use = (seasons < int(target_season)) & (seasons >= int(target_season) - int(lookback))
    o = np.asarray(fair_open, dtype=float)[use]
    c = np.asarray(fair_close, dtype=float)[use]
    ok = np.isfinite(o).all(axis=1) & np.isfinite(c).all(axis=1)
    if not ok.any():
        return float("nan")
    o, c = o[ok], c[ok]
    z = (c - o) / np.sqrt(np.clip(o * (1.0 - o), 1e-12, None))
    return float(np.sqrt(np.mean(z * z)))


# --------------------------------------------------------------------------- #
# Stored forecasts, ex-ante model choice, pooling with the market
# --------------------------------------------------------------------------- #

PREDICTIONS_ROOT = DATA_ROOT / "predictions"
BASE_MODELS: tuple[str, ...] = ("dixon_coles", "nb2", "gas_poisson", "elo_davidson", "pi_gbm", "pi_ratings")
DERIVED_MODELS: tuple[str, ...] = ("selected_best", "pool_models")
BASELINE_MODEL = "baseline_league_freq"


def _forecast_table(market: Market) -> str:
    return "forecasts_" + market.name.replace(".", "_")


def load_forecast_matrix(
    model: str,
    spec: MarketSpec,
    fixture_ids: Sequence[str],
    root: str | Path = PREDICTIONS_ROOT,
) -> np.ndarray | None:
    """Stored forecasts of one model aligned to ``fixture_ids`` ((n, K), NaN = no forecast).

    ``None`` if the model has no file or no table for the market (Elo-Davidson has 1X2 only).  Tables written under
    another protocol raise (see :func:`~model_buk.evaluation.protocol.load_predictions`).
    """
    path = Path(root) / f"{model}.sqlite"
    if not path.exists():
        return None
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_forecast_table(spec.market),)
        ).fetchone()
    if row is None:
        return None
    frame = load_predictions(model, spec.market, root)
    frame["fixture_id"] = frame["fixture_id"].astype(str)
    columns = [f"p_{label}" for label in spec.market.classes]
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    indexed = frame.drop_duplicates("fixture_id").set_index("fixture_id")[columns]
    return indexed.reindex(list(fixture_ids)).to_numpy(dtype=float)


def load_protocol_frame(spec: MarketSpec, root: str | Path = PREDICTIONS_ROOT) -> pd.DataFrame:
    """Fixture, protocol league label, season, matchday cluster and outcome of the protocol test seasons.

    Read from the league-frequency baseline table, which holds every scored match of the protocol (NaN forecast rows
    included), so the cluster labels are exactly those of the model comparison.
    """
    frame = load_predictions(BASELINE_MODEL, spec.market, root)
    frame["fixture_id"] = frame["fixture_id"].astype(str)
    return frame[["fixture_id", "league_code", "season", "matchday", "y"]].drop_duplicates("fixture_id")


def selected_model_by_season(spec: MarketSpec, root: str | Path = PREDICTIONS_ROOT) -> dict[int, str]:
    """Model that ``selected_best`` chose ex ante for every scored season (from the previous season's log-loss)."""
    path = Path(root) / "selected_best.sqlite"
    table = _forecast_table(spec.market) + "_selections"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        frame = pd.read_sql_query(f"SELECT outer_season, params FROM {table} WHERE league = 'ALL'", connection)
    chosen: dict[int, str] = {}
    for season, params in zip(frame["outer_season"], frame["params"]):
        name = json.loads(params).get("selected")
        if name:
            chosen[int(season)] = str(name)
    return chosen


def load_model_arrays(
    panel: MarketPanel,
    models: Iterable[str],
    root: str | Path = PREDICTIONS_ROOT,
) -> dict[str, np.ndarray]:
    """Forecast matrices of every model that exists for the panel's market (missing models are left out)."""
    arrays: dict[str, np.ndarray] = {}
    for model in models:
        matrix = load_forecast_matrix(model, panel.spec, panel.fixture_ids, root)
        if matrix is not None:
            arrays[model] = matrix
    return arrays


def _finite_rows(*matrices: np.ndarray) -> np.ndarray:
    mask = np.ones(len(matrices[0]), dtype=bool)
    for matrix in matrices:
        mask &= np.isfinite(matrix).all(axis=1) if matrix.ndim == 2 else np.isfinite(matrix)
    return mask


MIN_POOL_FIT_MATCHES = 200


def pooled_with_market(
    panel: MarketPanel,
    method: str,
    base_forecasts: Mapping[str, np.ndarray],
    chosen: Mapping[int, str],
    *,
    first_fit_season: int = FIRST_FORECAST_SEASON,
) -> tuple[np.ndarray, pd.DataFrame]:
    """Log-linear pool of the de-vigged Pinnacle opening line and a model, weight fitted on earlier seasons.

    For every season ``t`` in ``chosen`` the pool weight ``(w_market, w_model)`` is fitted (log-loss, simplex,
    :func:`~model_buk.evaluation.model_selection.fit_log_linear_pool`) on the rows of seasons
    ``first_fit_season .. t - 1`` that have both the opening line and the forecast of ``chosen[t]``, and applied to season
    ``t`` only.  Seasons without enough earlier rows stay NaN.  Returns the ``(n, K)`` forecasts and the table of
    fitted weights.
    """
    q = panel.fair[(OPEN_FAIR_SOURCE, method)]
    y = panel.y
    season = panel.season
    out = np.full(q.shape, np.nan)
    rows = []
    for target in sorted(chosen):
        name = chosen[target]
        forecast = base_forecasts.get(name)
        if forecast is None:
            continue
        fit_rows = (season >= first_fit_season) & (season < target) & _finite_rows(q, forecast, y)
        row = {"season": int(target), "method": method, "model": name, "n_fit": int(fit_rows.sum())}
        if fit_rows.sum() >= MIN_POOL_FIT_MATCHES:
            fit = fit_log_linear_pool([q[fit_rows], forecast[fit_rows]], y[fit_rows].astype(np.int64))
            apply_rows = (season == target) & _finite_rows(q, forecast)
            if apply_rows.any():
                out[apply_rows] = apply_log_linear_pool([q[apply_rows], forecast[apply_rows]], fit.weights)
            row.update(w_market=float(fit.weights[0]), w_model=float(fit.weights[1]), converged=bool(fit.converged))
        else:
            row.update(w_market=np.nan, w_model=np.nan, converged=False)
        rows.append(row)
    return out, pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Market-residual model (1X2)
# --------------------------------------------------------------------------- #

RESIDUAL_L2_GRID: tuple[float, ...] = (0.03, 0.1, 0.3, 1.0)


def load_form_features(history_db: str | Path = HISTORY_DB) -> pd.DataFrame:
    """Point-in-time form features (:func:`~model_buk.models.market_residual.build_market_residual_features`).

    Built from finished matches of the protocol's leagues with the per-team shots, shots on target and corners of
    ``team_match_stats`` (missing statistics stay NaN; the model imputes the training median).  Returns one row per
    fixture, ``source_row_id`` = fixture id.
    """
    from model_buk.evaluation.protocol import PROTOCOL_LEAGUE_IDS
    from model_buk.models.market_residual import build_market_residual_features

    query = (
        "SELECT m.fixture_id AS source_row_id, m.league_code AS league, m.kickoff_utc AS kickoff, "
        "m.home_team_id, m.away_team_id, m.home_goals, m.away_goals, m.league_id, "
        "h.total_shots AS home_shots, a.total_shots AS away_shots, "
        "h.shots_on_goal AS home_sot, a.shots_on_goal AS away_sot, "
        "h.corners AS home_corners, a.corners AS away_corners "
        "FROM matches m "
        "LEFT JOIN team_match_stats h ON h.fixture_id = m.fixture_id AND h.team_id = m.home_team_id "
        "LEFT JOIN team_match_stats a ON a.fixture_id = m.fixture_id AND a.team_id = m.away_team_id "
        "WHERE m.status_short = 'FT' AND m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL"
    )
    with closing(sqlite3.connect(f"file:{Path(history_db).as_posix()}?mode=ro", uri=True)) as connection:
        matches = pd.read_sql_query(query, connection)
    matches = matches[matches["league_id"].isin(PROTOCOL_LEAGUE_IDS)].drop(columns="league_id")
    matches["source_row_id"] = matches["source_row_id"].astype(str)
    matches["home_team"] = matches["home_team_id"].astype(str)
    matches["away_team"] = matches["away_team_id"].astype(str)
    matches = matches.drop(columns=["home_team_id", "away_team_id"])
    matches = matches.drop_duplicates("source_row_id")
    kickoff = pd.to_datetime(matches["kickoff"], utc=True, format="mixed", errors="coerce")
    matches = matches.loc[kickoff.notna()].copy()
    matches["kickoff"] = kickoff[kickoff.notna()]
    # A team cannot play two matches at one kickoff; a provider duplicate (a rescheduled fixture kept twice) is
    # dropped rather than allowed to break the rolling windows.
    both = pd.concat(
        [
            matches[["source_row_id", "league", "home_team", "kickoff"]].rename(columns={"home_team": "team"}),
            matches[["source_row_id", "league", "away_team", "kickoff"]].rename(columns={"away_team": "team"}),
        ]
    )
    clash = both.duplicated(["league", "team", "kickoff"], keep="first")
    matches = matches[~matches["source_row_id"].isin(both.loc[clash, "source_row_id"])]
    return build_market_residual_features(matches.reset_index(drop=True))


def _market_shape(q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    with np.errstate(invalid="ignore", divide="ignore"):
        entropy = -np.sum(np.where(q > 0, q * np.log(q), 0.0), axis=1)
    entropy[~np.isfinite(q).all(axis=1)] = np.nan
    favourite = np.max(q, axis=1)
    return entropy, favourite


def residual_forecasts_1x2(
    panel: MarketPanel,
    method: str,
    features: pd.DataFrame,
    seasons: Sequence[int],
    *,
    l2_grid: Sequence[float] = RESIDUAL_L2_GRID,
    config_seed: int = 42,
) -> tuple[np.ndarray, pd.DataFrame]:
    """Market-residual forecasts (1X2) for each season in ``seasons``, fitted only on earlier seasons.

    The de-vigged Pinnacle opening line is the offset, corrections are bounded log-odds shifts driven by form
    differences (5 and 10 match windows of goals, shots, shots on target, corners, points, rest days) and the shape
    of the opening line.  For season ``t`` the ``l2_strength`` is chosen on season ``t - 1`` (fit on seasons before
    it), then the model is refitted on all seasons before ``t`` and predicts ``t``.  Returns the ``(n, 3)``
    forecasts (NaN where the opening line is missing) and the selection table.
    """
    from model_buk.models.market_residual import (
        RESIDUAL_FEATURE_COLUMNS,
        MarketResidualConfig,
        fit_market_residual,
    )

    if panel.spec.name != "1x2":
        raise ValueError("the market-residual model is three-class and works for 1X2 only")
    q = panel.fair[(OPEN_FAIR_SOURCE, method)]
    y = panel.y
    season = panel.season
    frame = features.drop_duplicates("source_row_id").set_index("source_row_id").reindex(panel.fixture_ids)
    frame = frame.reset_index(drop=True)
    entropy, favourite = _market_shape(q)
    frame["opening_entropy"] = entropy
    frame["opening_favourite_probability"] = favourite
    out = np.full(q.shape, np.nan)
    rows = []
    usable = _finite_rows(q, y)
    for target in seasons:
        train = usable & (season < target)
        validation = usable & (season == target - 1)
        if train.sum() < MIN_POOL_FIT_MATCHES:
            continue
        inner_train = train & (season < target - 1)
        best_l2, best_loss = float(l2_grid[0]), np.inf
        if validation.sum() >= MIN_POOL_FIT_MATCHES and inner_train.sum() >= MIN_POOL_FIT_MATCHES:
            for l2 in l2_grid:
                cfg = MarketResidualConfig(l2_strength=float(l2), seed=config_seed)
                fitted = fit_market_residual(
                    frame.loc[inner_train], q[inner_train], y[inner_train].astype(np.int64), config=cfg
                )
                pred = fitted.predict_proba(frame.loc[validation], q[validation])
                loss = float(log_loss_per_match(pred, y[validation].astype(np.int64)).mean())
                if loss < best_loss:
                    best_l2, best_loss = float(l2), loss
        cfg = MarketResidualConfig(l2_strength=best_l2, seed=config_seed)
        model = fit_market_residual(frame.loc[train], q[train], y[train].astype(np.int64), config=cfg)
        apply_rows = (season == target) & _finite_rows(q)
        if apply_rows.any():
            out[apply_rows] = model.predict_proba(frame.loc[apply_rows], q[apply_rows])
        rows.append(
            {
                "season": int(target),
                "method": method,
                "l2_strength": best_l2,
                "inner_log_loss": best_loss if np.isfinite(best_loss) else np.nan,
                "n_fit": int(train.sum()),
                "features": len(RESIDUAL_FEATURE_COLUMNS),
            }
        )
    return out, pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Forecast quality against the market
# --------------------------------------------------------------------------- #


def _group_mean(values: np.ndarray, groups: np.ndarray) -> dict[str, float]:
    frame = pd.DataFrame({"v": values, "g": groups})
    out = {str(k): float(v) for k, v in frame.groupby("g")["v"].mean().items()}
    out[POOLED_LABEL] = float(np.mean(values)) if len(values) else float("nan")
    return out


def model_vs_market(
    panel: MarketPanel,
    forecasts: Mapping[str, np.ndarray],
    *,
    mask: np.ndarray,
    sample: str,
    methods: Sequence[str] = DEVIG_ORDER,
    references: Sequence[str] = ("open", "close"),
    n_resamples: int = 1000,
    pooled_family: int = 1,
    seed: int = 0,
) -> pd.DataFrame:
    """Log-loss of every forecast versus the de-vigged Pinnacle line, per league and pooled, on identical matches.

    ``mask`` is the common sample (rows where the opening line, the closing line and every forecast exist; the
    caller builds it so all models are compared on the same matches).  ``mean_diff = market_loss - model_loss``: positive
    means the model is *better* than the market.  Per league: matchday-cluster percentile bootstrap interval,
    Diebold-Mariano p-value, Holm and Benjamini-Hochberg p-values across the leagues and Bonferroni intervals
    (:func:`~model_buk.evaluation.model_selection.paired_comparison`).  ``pooled_family`` is the number of pooled
    comparisons of the family (for the Bonferroni interval of the pooled row).
    """
    keep = np.asarray(mask, dtype=bool)
    y = panel.y[keep].astype(np.int64)
    league = panel.meta["league"].to_numpy(dtype=object)[keep]
    cluster = panel.meta["matchday"].to_numpy(dtype=object)[keep]
    order = panel.meta["kickoff"].to_numpy()[keep]
    tables = []
    for reference in references:
        source = "ps_open" if reference == "open" else "ps_close"
        for method in methods:
            market_loss = log_loss_per_match(panel.fair[(source, method)][keep], y)
            market_by = _group_mean(market_loss, league)
            for name, matrix in forecasts.items():
                model_loss = log_loss_per_match(matrix[keep], y)
                model_by = _group_mean(model_loss, league)
                table = paired_comparison(
                    market_loss,
                    model_loss,
                    league=league,
                    cluster=cluster,
                    order=order,
                    n_resamples=n_resamples,
                    seed=seed,
                    pooled_family=pooled_family,
                )
                table.insert(0, "model", name)
                table.insert(0, "method", method)
                table.insert(0, "reference", reference)
                table.insert(0, "sample", sample)
                table.insert(0, "market", panel.spec.name)
                table["market_loss"] = table["league_code"].map(market_by)
                table["model_loss"] = table["league_code"].map(model_by)
                tables.append(table)
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


def loss_by_season(
    panel: MarketPanel,
    forecasts: Mapping[str, np.ndarray],
    *,
    mask: np.ndarray,
    methods: Sequence[str] = DEVIG_ORDER,
) -> pd.DataFrame:
    """Mean log-loss per season of the market lines and of every forecast on the common sample (descriptive)."""
    keep = np.asarray(mask, dtype=bool)
    y = panel.y[keep].astype(np.int64)
    season = panel.season[keep]
    rows = []

    def add(name: str, kind: str, method: str, loss: np.ndarray) -> None:
        for s in sorted(set(season.tolist())) + [None]:
            sel = np.ones(len(loss), dtype=bool) if s is None else season == s
            rows.append(
                {
                    "market": panel.spec.name,
                    "name": name,
                    "kind": kind,
                    "method": method,
                    "season": "ALL" if s is None else str(s),
                    "n": int(sel.sum()),
                    "log_loss": float(loss[sel].mean()) if sel.any() else np.nan,
                }
            )

    for source, kind in (("ps_open", "market_open"), ("ps_close", "market_close")):
        for method in methods:
            add(f"pinnacle_{kind[7:]}", kind, method, log_loss_per_match(panel.fair[(source, method)][keep], y))
    for name, matrix in forecasts.items():
        add(name, "model", "", log_loss_per_match(matrix[keep], y))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Leg table: one row per (fixture, outcome), open columns apart from settlement columns
# --------------------------------------------------------------------------- #

_FORBIDDEN_OPEN_PATTERN = re.compile(r"close|closing|^win$|^y$|outcome|result|goals", re.IGNORECASE)


class ClosingLeakError(AssertionError):
    """Raised when a decision frame carries closing-line or outcome columns."""


def assert_open_only(frame: pd.DataFrame) -> None:
    """Refuse a decision frame with a closing-line, outcome or result column (structural no-look-ahead guard)."""
    bad = [column for column in frame.columns if _FORBIDDEN_OPEN_PATTERN.search(str(column))]
    if bad:
        raise ClosingLeakError(f"decision frame carries closing or outcome columns: {', '.join(bad)}")


@dataclass
class LegTable:
    """All legs of one or more markets, split into what a decision may read and what only settlement may read.

    ``open`` (index = ``leg_id``): fixture, market, outcome class ``k`` and label, league, season, snapshot ``group``,
    matchday ``cluster``, kickoff, the bookmakers' opening odds (``odds_<book>``), the de-vigged opening reference
    (``ref_open_<method>``), the drift scale (``scale_ref_<method>``), and the forecasts (``p_model``,
    ``scale_model_<method>``, ``pool_<method>``, ``resid_<method>``).  ``settle`` (same index): ``win`` and the
    de-vigged closing line ``close_fair_<method>``.
    """

    open: pd.DataFrame
    settle: pd.DataFrame

    def __post_init__(self) -> None:
        assert_open_only(self.open)
        if not self.open.index.equals(self.settle.index):
            raise ValueError("open and settle frames must share one index")


def concat_leg_tables(tables: Sequence[LegTable]) -> LegTable:
    """Stack the leg tables of several markets (the leg ids are renumbered)."""
    opened = pd.concat([t.open for t in tables], ignore_index=True)
    settled = pd.concat([t.settle for t in tables], ignore_index=True)
    opened.index.name = settled.index.name = "leg_id"
    return LegTable(opened, settled)


def build_leg_table(
    panel: MarketPanel,
    *,
    seasons: Sequence[int],
    methods: Sequence[str] = DEVIG_ORDER,
    model_base: Mapping[str, np.ndarray] | None = None,
    chosen: Mapping[int, str] | None = None,
    pool: Mapping[str, np.ndarray] | None = None,
    resid: Mapping[str, np.ndarray] | None = None,
    polish_overround: float = DEFAULT_POLISH_OVERROUND,
) -> LegTable:
    """Legs of one market for the given seasons.

    ``model_base`` holds stored forecasts per model, ``chosen`` the ex-ante model per season (the forecast of that
    model becomes ``p_model``), ``pool`` / ``resid`` map a de-vig method to the pooled / residual forecasts.  Missing
    inputs stay NaN.  The reference of a decision is ``panel.fair[(OPEN_FAIR_SOURCE, method)]``.
    """
    spec = panel.spec
    k_count = spec.n_classes
    rows = np.flatnonzero(np.isin(panel.season, [int(s) for s in seasons]))
    n_rows = len(rows)
    rep = np.repeat(rows, k_count)
    klass = np.tile(np.arange(k_count), n_rows)
    season = panel.season[rep]

    def leg(matrix: np.ndarray) -> np.ndarray:
        return np.asarray(matrix, dtype=float)[rows].reshape(-1)

    meta = panel.meta
    frame: dict[str, np.ndarray] = {
        "fixture_id": meta["fixture_id"].to_numpy(dtype=object)[rep],
        "market": np.full(len(rep), spec.name, dtype=object),
        "k": klass,
        "selection": np.asarray(spec.labels, dtype=object)[klass],
        "league": meta["league"].to_numpy(dtype=object)[rep],
        "season": season,
        "group": meta["group"].to_numpy(dtype=object)[rep],
        "cluster": meta["matchday"].to_numpy(dtype=object)[rep],
        "kickoff_ns": pd.to_datetime(meta["kickoff"], utc=True).dt.as_unit("ns").astype("int64").to_numpy()[rep],
    }
    for book in spec.books:
        frame[f"odds_{book}"] = leg(panel.odds[book])
    frame["odds_pl_ps"] = leg(polish_odds(panel, "pl_ps", polish_overround))
    frame["odds_pl_b365"] = leg(polish_odds(panel, "pl_b365", polish_overround))

    model_p = np.full((len(panel), k_count), np.nan)
    if model_base and chosen:
        for target, name in chosen.items():
            source = model_base.get(name)
            if source is not None:
                mine = panel.season == target
                model_p[mine] = source[mine]
    frame["p_model"] = leg(model_p)

    settle: dict[str, np.ndarray] = {}
    outcome = panel.y[rep]
    settle["win"] = np.where(np.isnan(outcome), np.nan, (outcome == klass).astype(float))
    for method in methods:
        opened = panel.fair[(OPEN_FAIR_SOURCE, method)]
        closed = panel.fair[("ps_close", method)]
        frame[f"ref_open_{method}"] = leg(opened)
        settle[f"close_fair_{method}"] = leg(closed)
        scale_ref = {
            s: drift_scale(panel.fair[("ps_open", method)], closed, panel.season, s) for s in {int(v) for v in seasons}
        }
        frame[f"scale_ref_{method}"] = np.array([scale_ref[int(s)] for s in season], dtype=float)
        scale_model = {}
        for s in {int(v) for v in seasons}:
            name = chosen.get(s) if chosen else None
            source = model_base.get(name) if (model_base and name) else None
            scale_model[s] = drift_scale(source, closed, panel.season, s) if source is not None else np.nan
        frame[f"scale_model_{method}"] = np.array([scale_model[int(s)] for s in season], dtype=float)
        pooled = None if pool is None else pool.get(method)
        residual = None if resid is None else resid.get(method)
        frame[f"pool_{method}"] = leg(pooled) if pooled is not None else np.full(len(rep), np.nan)
        frame[f"resid_{method}"] = leg(residual) if residual is not None else np.full(len(rep), np.nan)
    opened_frame = pd.DataFrame(frame)
    settle_frame = pd.DataFrame(settle)
    opened_frame.index.name = settle_frame.index.name = "leg_id"
    return LegTable(opened_frame, settle_frame)


# --------------------------------------------------------------------------- #
# Decisions: strategy probability, uncertainty haircut, one pick per match
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CellSpec:
    """One decision setup (everything but the tax regime and the number of legs).

    ``market_set``: ``"all"`` (1X2 and over/under 2.5 compete, one pick per match) or a single market.  ``strategy``:
    ``reference`` (soft price against the de-vigged Pinnacle opening line), ``reference_veto`` (the same, blocked when the
    model thinks the outcome less likely than the reference), ``pool`` (log-linear pool of reference and model),
    ``residual`` (market-residual model, 1X2 only) or ``model`` (the model's own probability; a control).  ``book``: the
    price taken.  ``method``: the de-vig method of the reference *and* of the closing line used for CLV.  ``z``: standard
    errors of haircut on the probability (0 = no haircut).
    """

    market_set: str = "all"
    strategy: str = "reference"
    book: str = "bet365"
    method: str = "power"
    z: float = DEFAULT_Z
    seasons: tuple[int, ...] = SELECTION_SEASONS

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy {self.strategy!r}")
        if self.book not in BOOKS:
            raise ValueError(f"unknown book {self.book!r}")
        if self.method not in DEVIG_ORDER:
            raise ValueError(f"unknown de-vig method {self.method!r}")
        if self.market_set not in ("all", *MARKET_ORDER):
            raise ValueError(f"unknown market set {self.market_set!r}")
        if not math.isfinite(self.z) or self.z < 0:
            raise ValueError("z must be a finite non-negative number")
        object.__setattr__(self, "seasons", tuple(sorted({int(s) for s in self.seasons})))

    @property
    def gate(self) -> EvGateConfig:
        """The plan's EV gate with uncertainty: net EV of the lower probability bound must be strictly above zero."""
        return EvGateConfig(z=self.z, min_ev_lower=0.0)

    def key(self) -> dict[str, object]:
        return {
            "market_set": self.market_set,
            "strategy": self.strategy,
            "book": self.book,
            "method": self.method,
            "z": self.z,
            "seasons": "-".join(str(s) for s in self.seasons),
        }


def strategy_probability(open_legs: pd.DataFrame, cell: CellSpec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Probability, standard-error scale and permission of every leg under the strategy of ``cell``.

    Returns ``(p, scale, allowed)``; ``SE(p) = scale * sqrt(p * (1 - p))``.  A leg whose probability or scale is
    missing is not allowed (it cannot be decided), never filled.
    """
    method = cell.method
    reference = open_legs[f"ref_open_{method}"].to_numpy(dtype=float)
    scale_ref = open_legs[f"scale_ref_{method}"].to_numpy(dtype=float)
    p_model = open_legs["p_model"].to_numpy(dtype=float)
    if cell.strategy == "reference":
        p, scale, allowed = reference, scale_ref, np.ones(len(reference), dtype=bool)
    elif cell.strategy == "reference_veto":
        with np.errstate(invalid="ignore"):
            allowed = np.isfinite(p_model) & (p_model >= reference)
        p, scale = reference, scale_ref
    elif cell.strategy == "pool":
        p, scale, allowed = open_legs[f"pool_{method}"].to_numpy(dtype=float), scale_ref, np.ones(len(reference), dtype=bool)
    elif cell.strategy == "residual":
        p, scale, allowed = open_legs[f"resid_{method}"].to_numpy(dtype=float), scale_ref, np.ones(len(reference), dtype=bool)
    else:  # "model"
        p, scale = p_model, open_legs[f"scale_model_{method}"].to_numpy(dtype=float)
        allowed = np.ones(len(reference), dtype=bool)
    allowed = allowed & np.isfinite(p) & np.isfinite(scale) & (p > 0) & (p < 1)
    return p, scale, allowed


def _cell_frame(open_legs: pd.DataFrame, cell: CellSpec) -> pd.DataFrame:
    assert_open_only(open_legs)
    mask = open_legs["season"].isin(cell.seasons).to_numpy()
    if cell.market_set != "all":
        mask = mask & (open_legs["market"] == cell.market_set).to_numpy()
    return open_legs.loc[mask]


def select_gross_legs(open_legs: pd.DataFrame, cell: CellSpec) -> pd.DataFrame:
    """Best leg per match whose price beats the *lower bound* fair price (gross, before tax), ranked.

    The lower bound is ``p - z * SE(p)`` (:func:`~model_buk.markets.pricing.probability_lower_bound`); the
    candidate must have ``p_lower * odds > 1`` (net EV of the lower bound at ``tau = 1``, the weakest
    condition any tax regime or accumulator leg has to meet) and is then ranked by
    :func:`~model_buk.opportunity_selector.select_opportunities`, which keeps one pick per match.  Returns the
    picks in rank order (best first) with ``p``, ``p_lower``, ``odds`` and the leg's ids.
    """
    frame = _cell_frame(open_legs, cell)
    empty = pd.DataFrame(
        columns=["leg_id", "fixture_id", "market", "selection", "group", "cluster", "season", "kickoff_ns", "odds", "p", "p_lower"]
    )
    if frame.empty:
        return empty
    p, scale, allowed = strategy_probability(frame, cell)
    odds = frame[f"odds_{cell.book}"].to_numpy(dtype=float)
    allowed = allowed & np.isfinite(odds) & (odds > 1.0)
    with np.errstate(invalid="ignore"):
        se = scale * np.sqrt(np.clip(p * (1.0 - p), 0.0, None))
        lower_bound = np.maximum(p - cell.gate.z * se, 0.0)
        screened = allowed & (lower_bound * odds > 1.0 - 1e-9)
    positions = np.flatnonzero(screened)
    if positions.size == 0:
        return empty
    candidates = []
    where: dict[tuple[str, str, str], int] = {}
    fixture_ids = frame["fixture_id"].to_numpy(dtype=object)
    markets = frame["market"].to_numpy(dtype=object)
    labels = frame["selection"].to_numpy(dtype=object)
    groups = frame["group"].to_numpy(dtype=object)
    for pos in positions:
        p_lower = probability_lower_bound(float(p[pos]), float(se[pos]), cell.gate.z)
        candidates.append(
            OpportunityCandidate(
                fixture_id=str(fixture_ids[pos]),
                market_key=str(markets[pos]),
                selection=str(labels[pos]),
                decimal_odds=float(odds[pos]),
                probability=float(p[pos]),
                probability_lower_bound=p_lower,
                observed_at=window_datetime(str(groups[pos])),
                snapshot_id=str(groups[pos]),
                bookmaker=cell.book,
                tax_multiplier=1.0,
            )
        )
        where[(str(fixture_ids[pos]), str(markets[pos]), str(labels[pos]))] = int(pos)
    selection = select_opportunities(
        candidates,
        max_recommendations=len(candidates),
        max_decimal_odds=1e9,
        max_per_fixture=1,
    )
    picks = []
    leg_ids = frame.index.to_numpy()
    for rank, item in enumerate(selection.selected):
        candidate = item.candidate
        pos = where[candidate.logical_key]
        picks.append(
            {
                "leg_id": int(leg_ids[pos]),
                "fixture_id": candidate.fixture_id,
                "market": candidate.market_key,
                "selection": candidate.selection,
                "group": candidate.snapshot_id,
                "cluster": frame["cluster"].iloc[pos],
                "season": int(frame["season"].iloc[pos]),
                "kickoff_ns": int(frame["kickoff_ns"].iloc[pos]),
                "odds": candidate.decimal_odds,
                "p": candidate.probability,
                "p_lower": candidate.probability_lower_bound,
                "rank": rank,
            }
        )
    return pd.DataFrame(picks) if picks else empty


def make_bets(picks: pd.DataFrame, cell: CellSpec, tau: float, n_legs: int) -> pd.DataFrame:
    """Singles (``n_legs = 1``) or accumulators from the gross-selected legs, under tax multiplier ``tau``.

    A single must pass :func:`~model_buk.decision.assess_bet`: net EV of the lower bound above zero under the book's
    tax multiplier.  An accumulator takes, inside every snapshot group, consecutive blocks of ``n_legs`` legs in rank
    order (legs of different matches only, priced at the same snapshot, one book) and is kept when
    :func:`~model_buk.markets.pricing.accumulator_ev_net` of the *lower* probability bounds is strictly positive (the
    tax is paid once for the whole coupon).  Returns one row per bet with the leg ids.
    """
    if not isinstance(n_legs, int) or isinstance(n_legs, bool) or n_legs < 1:
        raise ValueError("n_legs must be a positive integer")
    columns = ["group", "cluster", "season", "kickoff_last_ns", "leg_ids", "fixtures", "odds", "p", "p_lower", "n_legs", "tau"]
    if picks.empty:
        return pd.DataFrame(columns=columns)
    profiles = TaxProfiles({cell.book: tau})
    ordered = picks.sort_values(["group", "rank"], kind="stable")
    rows = []
    if n_legs == 1:
        for record in ordered.itertuples(index=False):
            assessment = assess_bet(
                bookmaker=cell.book,
                probability=record.p,
                probability_lower_bound=record.p_lower,
                decimal_odds=record.odds,
                min_ev_lower=cell.gate.min_ev_lower,
                kelly_multiplier=KELLY_MULTIPLIER,
                tax_profiles=profiles,
            )
            if not assessment.gate_passed:
                continue
            rows.append(
                {
                    "group": record.group,
                    "cluster": record.cluster,
                    "season": record.season,
                    "kickoff_last_ns": record.kickoff_ns,
                    "leg_ids": (int(record.leg_id),),
                    "fixtures": str(record.fixture_id),
                    "odds": float(record.odds),
                    "p": float(record.p),
                    "p_lower": float(record.p_lower),
                    "n_legs": 1,
                    "tau": float(tau),
                }
            )
    else:
        for group, part in ordered.groupby("group", sort=False):
            records = list(part.itertuples(index=False))
            for start in range(0, len(records) - n_legs + 1, n_legs):
                block = records[start : start + n_legs]
                legs = [AccumulatorLeg(str(r.fixture_id), float(r.p_lower), float(r.odds)) for r in block]
                if accumulator_ev_net(legs, tau) <= 0.0:
                    continue
                rows.append(
                    {
                        "group": group,
                        "cluster": group,  # a coupon spans matchdays of several leagues: cluster by snapshot window
                        "season": int(block[0].season),
                        "kickoff_last_ns": int(max(r.kickoff_ns for r in block)),
                        "leg_ids": tuple(int(r.leg_id) for r in block),
                        "fixtures": "|".join(str(r.fixture_id) for r in block),
                        "odds": float(math.prod(r.odds for r in block)),
                        "p": float(math.prod(r.p for r in block)),
                        "p_lower": float(math.prod(r.p_lower for r in block)),
                        "n_legs": n_legs,
                        "tau": float(tau),
                    }
                )
    return pd.DataFrame(rows, columns=columns)


# --------------------------------------------------------------------------- #
# Settlement, closing line value, stakes
# --------------------------------------------------------------------------- #


def settle_bets(bets: pd.DataFrame, settle: pd.DataFrame, cell: CellSpec, tau: float) -> pd.DataFrame:
    """Add outcome, closing line value on the price taken, and flat / Kelly stakes and profits.

    Reads the closing line and the outcome for the first time (decisions are already made).  The coupon's fair closing
    probability is the product of its legs' de-vigged Pinnacle closing probabilities (NaN if any leg has no closing
    line); ``clv_raw = odds * fair_close - 1`` and ``clv_net = tau * odds * fair_close - 1``
    (:func:`~model_buk.markets.pricing.closing_line_value`).  Flat stake: 100 PLN cut back below the winnings-PIT
    threshold.  Kelly stake: taxed quarter Kelly on the lower bound of a fixed 10 000 PLN bankroll, at most 2% per bet
    (:func:`~model_buk.markets.staking.plan_stake`); a bet whose Kelly stake is zero is not staked.
    """
    out = bets.copy()
    if out.empty:
        for column in ("win", "fair_close", "clv_raw", "clv_net", "stake_flat", "profit_flat", "stake_kelly", "profit_kelly"):
            out[column] = pd.Series(dtype=float)
        return out
    win_col = settle["win"].to_numpy(dtype=float)
    close_col = settle[f"close_fair_{cell.method}"].to_numpy(dtype=float)
    index = settle.index
    wins, fair_close, clv_raw, clv_net = [], [], [], []
    flat_stake, flat_profit, kelly_stake, kelly_profit = [], [], [], []
    for record in out.itertuples(index=False):
        positions = index.get_indexer(list(record.leg_ids))
        win_values = win_col[positions]
        won = np.nan if np.isnan(win_values).any() else float(win_values.min())
        closes = close_col[positions]
        fair = float("nan") if np.isnan(closes).any() else float(np.prod(closes))
        clv = closing_line_value(record.odds, None if math.isnan(fair) else fair, tau)
        wins.append(won)
        fair_close.append(fair)
        clv_raw.append(np.nan if clv.raw is None else clv.raw)
        clv_net.append(np.nan if clv.net is None else clv.net)
        cap = max_stake_below_pit(record.odds, tau)
        stake = min(FLAT_STAKE_PLN, cap)
        flat_stake.append(stake)
        flat_profit.append(_profit(stake, record.odds, tau, won))
        plan = plan_stake(
            bankroll=KELLY_BANKROLL_PLN,
            p_lower=record.p_lower,
            decimal_odds=record.odds,
            tax_multiplier=tau,
            kelly_multiplier=KELLY_MULTIPLIER,
            max_bankroll_fraction=KELLY_MAX_FRACTION,
        )
        kelly_stake.append(plan.stake)
        kelly_profit.append(_profit(plan.stake, record.odds, tau, won))
    out["win"] = wins
    out["fair_close"] = fair_close
    out["clv_raw"] = clv_raw
    out["clv_net"] = clv_net
    out["stake_flat"] = flat_stake
    out["profit_flat"] = flat_profit
    out["stake_kelly"] = kelly_stake
    out["profit_kelly"] = kelly_profit
    return out


def _profit(stake: float, odds: float, tau: float, won: float) -> float:
    """Profit of a settled stake: ``net_payout - stake`` on a win (stake tax and PIT included), ``-stake`` on a loss."""
    if stake <= 0.0 or not math.isfinite(won):
        return 0.0 if stake <= 0.0 else float("nan")
    return net_payout(stake, odds, tau) - stake if won >= 0.5 else -stake


def kelly_path(bets: pd.DataFrame, *, bankroll: float = KELLY_BANKROLL_PLN) -> tuple[float, float]:
    """Compounded taxed quarter-Kelly path over the snapshot windows: ``(max_drawdown, final_bankroll / start)``.

    At the start of every snapshot window the stake of each bet is sized on the *current* bankroll
    (:func:`~model_buk.markets.staking.plan_stake`); the window settles at its last kickoff.  Windows do not overlap.
    Drawdown is measured between window ends (intra-window swings are not seen).  Bets with an unknown outcome are skipped.
    """
    if bets.empty:
        return 0.0, 1.0
    equity = float(bankroll)
    peak = equity
    worst = 0.0
    ordered = bets.dropna(subset=["win"]).assign(_t=lambda f: f["kickoff_last_ns"]).sort_values(["_t"], kind="stable")
    windows = ordered.groupby("group", sort=False)["_t"].min().sort_values().index
    for group in windows:
        part = ordered[ordered["group"] == group]
        change = 0.0
        for record in part.itertuples(index=False):
            plan = plan_stake(
                bankroll=equity,
                p_lower=record.p_lower,
                decimal_odds=record.odds,
                tax_multiplier=record.tau,
                kelly_multiplier=KELLY_MULTIPLIER,
                max_bankroll_fraction=KELLY_MAX_FRACTION,
            )
            change += _profit(plan.stake, record.odds, record.tau, float(record.win))
        equity += change
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return float(worst), float(equity / bankroll)


# --------------------------------------------------------------------------- #
# Statistics: matchday-cluster bootstrap, drawdown, summaries
# --------------------------------------------------------------------------- #


def _ci(draws: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    tail = (1.0 - level) / 2.0
    low, high = np.quantile(draws, [tail, 1.0 - tail])
    return float(low), float(high)


def _p_positive(draws: np.ndarray) -> float:
    """One-sided bootstrap p-value of ``H0: mean <= 0`` against ``H1: mean > 0`` (never exactly zero)."""
    return float((1.0 + np.sum(draws <= 0.0)) / (len(draws) + 1.0))


def bootstrap_mean(
    values: np.ndarray,
    clusters: np.ndarray,
    *,
    n_resamples: int = 4000,
    seed: int = 0,
    level: float = 0.95,
) -> dict[str, float]:
    """Mean of ``values`` with a percentile interval and a one-sided p-value, resampling whole clusters.

    ``clusters`` are the matchday labels (single bets) or snapshot windows (coupons): whole clusters are drawn with
    replacement (:func:`~model_buk.evaluation.significance.cluster_bootstrap_means`), so bets of one matchday
    are never treated as independent draws.  ``se`` is the standard deviation of the bootstrap distribution;
    ``p_positive`` the one-sided bootstrap p-value of ``mean <= 0`` (never below ``1 / (B + 1)``), ``p_z`` the normal
    approximation with the bootstrap standard error and ``p_test`` the one the family-wise correction uses (``p_z`` where the
    bootstrap cannot resolve the tail and there are at least 30 clusters, otherwise the larger of the two).
    """
    sample = np.asarray(values, dtype=float)
    cluster_labels = np.asarray(clusters)
    draws = cluster_bootstrap_means(sample, cluster_labels, n_resamples=n_resamples, seed=seed)
    low, high = _ci(draws, level)
    se = float(np.std(draws, ddof=1)) if len(draws) > 1 else float("nan")
    mean = float(sample.mean())
    p_boot = _p_positive(draws)
    p_z = float(stats.norm.sf(mean / se)) if se > 0 and math.isfinite(se) else float("nan")
    # A bootstrap p-value cannot fall below 1 / (B + 1); when no resample went to zero or below and there are enough
    # matchdays for the mean to be close to normal, the normal approximation with the bootstrap standard error stands in.
    n_clusters = int(pd.Series(cluster_labels).nunique())
    if not math.isfinite(p_z):
        p_test = p_boot
    elif n_clusters >= 30 and not np.any(draws <= 0.0):
        p_test = p_z
    else:
        p_test = max(p_boot, p_z)
    return {"mean": mean, "lo": low, "hi": high, "se": se, "p_positive": p_boot, "p_z": p_z, "p_test": p_test}


def bootstrap_ratio(
    numerator: np.ndarray,
    denominator: np.ndarray,
    clusters: np.ndarray,
    *,
    n_resamples: int = 4000,
    seed: int = 0,
    level: float = 0.95,
) -> dict[str, float]:
    """Ratio of sums (yield = profit / stake) with a cluster-bootstrap interval."""
    top = np.asarray(numerator, dtype=float)
    bottom = np.asarray(denominator, dtype=float)
    draws = cluster_bootstrap_means(
        np.column_stack([top, bottom]), np.asarray(clusters), n_resamples=n_resamples, seed=seed
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = draws[:, 0] / draws[:, 1]
    ratio = ratio[np.isfinite(ratio)]
    point = float(top.sum() / bottom.sum()) if bottom.sum() > 0 else float("nan")
    if len(ratio) == 0:
        return {"value": point, "lo": float("nan"), "hi": float("nan")}
    low, high = _ci(ratio, level)
    return {"value": point, "lo": low, "hi": high}


def max_drawdown_units(profit: np.ndarray, order: np.ndarray, unit: float) -> float:
    """Largest peak-to-trough fall of the cumulative profit (in ``unit``s) when bets settle in ``order``."""
    if len(profit) == 0:
        return 0.0
    sequence = np.asarray(profit, dtype=float)[np.argsort(np.asarray(order), kind="stable")] / unit
    path = np.concatenate([[0.0], np.cumsum(sequence)])
    return float(np.max(np.maximum.accumulate(path) - path))


SUMMARY_NAN_KEYS = (
    "clv_net_mean", "clv_net_lo", "clv_net_hi", "clv_net_se", "clv_net_p", "clv_net_pz", "clv_net_ptest", "clv_net_median", "clv_raw_mean",
    "clv_raw_median", "share_beat_raw", "share_beat_raw_lo", "share_beat_raw_hi", "share_beat_net",
    "yield_flat", "yield_flat_lo", "yield_flat_hi", "yield_kelly", "yield_kelly_lo", "yield_kelly_hi",
    "max_drawdown_flat_units", "max_drawdown_kelly", "kelly_growth", "hit_rate", "mean_odds",
)


def summarise_bets(
    bets: pd.DataFrame,
    *,
    n_resamples: int = 4000,
    seed: int = 0,
    level: float = 0.95,
) -> dict[str, object]:
    """Number of bets, CLV on the price taken, post-tax yield, drawdown; intervals cluster the bets by matchday.

    ``bets`` is the output of :func:`settle_bets`.  CLV rows exist only where the closing line does (``n_clv``); the
    yield uses every settled bet.  ``clv_net_*`` is the post-tax CLV (mean, percentile interval, one-sided bootstrap
    p-value of ``mean <= 0``, median), ``share_beat_raw`` the share of bets whose price beat the fair closing price
    (``clv_raw > 0``).  ``interval_ok`` says the sample is large enough to read an interval (at least
    :data:`MIN_BETS_FOR_INTERVAL` bets in :data:`MIN_CLUSTERS_FOR_INTERVAL` clusters).  Yields are per unit staked: flat
    (100 PLN per bet) and taxed quarter Kelly (weights of a fixed 10 000 PLN bankroll); drawdown is in units of the flat
    stake for flat staking and relative to the peak for the compounded Kelly path.
    """
    summary: dict[str, object] = {key: float("nan") for key in SUMMARY_NAN_KEYS}
    summary.update(n_bets=int(len(bets)), n_clv=0, n_clusters=0, interval_ok=False)
    if bets.empty:
        return summary
    clv = bets[np.isfinite(bets["clv_net"].to_numpy(dtype=float))]
    summary["n_clv"] = int(len(clv))
    summary["n_clusters"] = int(clv["cluster"].nunique()) if len(clv) else 0
    summary["hit_rate"] = float(bets["win"].mean()) if bets["win"].notna().any() else float("nan")
    summary["mean_odds"] = float(bets["odds"].mean())
    if len(clv):
        clusters = clv["cluster"].to_numpy(dtype=object)
        mean_ci = bootstrap_mean(clv["clv_net"].to_numpy(), clusters, n_resamples=n_resamples, seed=seed, level=level)
        beat = bootstrap_mean(
            (clv["clv_raw"].to_numpy() > 0).astype(float), clusters, n_resamples=n_resamples, seed=seed, level=level
        )
        summary.update(
            clv_net_mean=mean_ci["mean"], clv_net_lo=mean_ci["lo"], clv_net_hi=mean_ci["hi"], clv_net_se=mean_ci["se"],
            clv_net_p=mean_ci["p_positive"], clv_net_pz=mean_ci["p_z"], clv_net_ptest=mean_ci["p_test"],
            clv_net_median=float(clv["clv_net"].median()),
            clv_raw_mean=float(clv["clv_raw"].mean()), clv_raw_median=float(clv["clv_raw"].median()),
            share_beat_raw=beat["mean"], share_beat_raw_lo=beat["lo"], share_beat_raw_hi=beat["hi"],
            share_beat_net=float((clv["clv_net"].to_numpy() > 0).mean()),
        )
        summary["interval_ok"] = bool(
            len(clv) >= MIN_BETS_FOR_INTERVAL and summary["n_clusters"] >= MIN_CLUSTERS_FOR_INTERVAL
        )
    flat = bets[np.isfinite(bets["profit_flat"].to_numpy(dtype=float))]
    if len(flat):
        ratio = bootstrap_ratio(
            flat["profit_flat"].to_numpy(), flat["stake_flat"].to_numpy(), flat["cluster"].to_numpy(dtype=object),
            n_resamples=n_resamples, seed=seed, level=level,
        )
        summary.update(yield_flat=ratio["value"], yield_flat_lo=ratio["lo"], yield_flat_hi=ratio["hi"])
        summary["max_drawdown_flat_units"] = max_drawdown_units(
            flat["profit_flat"].to_numpy(), flat["kickoff_last_ns"].to_numpy(), FLAT_STAKE_PLN
        )
    kelly = bets[(bets["stake_kelly"] > 0) & np.isfinite(bets["profit_kelly"].to_numpy(dtype=float))]
    if len(kelly):
        ratio = bootstrap_ratio(
            kelly["profit_kelly"].to_numpy(), kelly["stake_kelly"].to_numpy(), kelly["cluster"].to_numpy(dtype=object),
            n_resamples=n_resamples, seed=seed, level=level,
        )
        summary.update(yield_kelly=ratio["value"], yield_kelly_lo=ratio["lo"], yield_kelly_hi=ratio["hi"])
        drawdown, growth = kelly_path(bets)
        summary.update(max_drawdown_kelly=drawdown, kelly_growth=growth)
    return summary


# --------------------------------------------------------------------------- #
# Random betting with the same selection rate and odds band
# --------------------------------------------------------------------------- #


def odds_band(odds: np.ndarray) -> np.ndarray:
    """Odds band index (0 to 4) with edges 1.5, 2, 3 and 5."""
    return np.digitize(np.asarray(odds, dtype=float), np.asarray(ODDS_BANDS[1:-1], dtype=float))


RANDOM_DIAGNOSTICS: tuple[str, ...] = (
    "random_same_window_share",
    "random_same_market_share",
    "random_same_band_share",
    "random_mean_leg_odds",
)


def random_baseline(
    bets: pd.DataFrame,
    legs: LegTable,
    cell: CellSpec,
    tau: float,
    *,
    n_replicates: int = 200,
    seed: int = 0,
) -> dict[str, float]:
    """What random betting with the same selection rate gets on the same CLV statistics.

    Every leg of every real bet (with a known closing line) is swapped for a random leg *of the same snapshot window,
    the same market and the same odds band* that the strategy could have taken (the book's price, the strategy's
    probability and the closing line present), so the number of bets, their timing, their market mix and their odds mix
    are identical; coupons get distinct matches.  ``n_replicates`` such draws give the distribution of the mean and the
    median post-tax CLV and of the share of closing lines beaten (``clv_raw > 0``) under pure luck.  The p-values are
    one-sided: the share of replicates at or above the real value.  ``random_same_window_share`` /
    ``random_same_market_share`` / ``random_same_band_share`` (1.0 by construction) and ``random_mean_leg_odds`` are
    diagnostics that let a test prove the matching is really done.
    """
    keys = ("mean_net", "median_net", "share_raw", "share_net")
    nan_row = {f"random_{k}_{s}": float("nan") for k in keys for s in ("mean", "p05", "p95")}
    nan_row.update({f"p_random_{k}": float("nan") for k in keys})
    nan_row.update({k: float("nan") for k in RANDOM_DIAGNOSTICS})
    nan_row["random_replicates"] = 0
    clv = bets[np.isfinite(bets["clv_net"].to_numpy(dtype=float))] if len(bets) else bets
    if clv.empty:
        return nan_row
    frame = _cell_frame(legs.open, cell)
    settle = legs.settle.loc[frame.index]
    odds = frame[f"odds_{cell.book}"].to_numpy(dtype=float)
    _, _, allowed = strategy_probability(frame, cell)
    close = settle[f"close_fair_{cell.method}"].to_numpy(dtype=float)
    eligible = allowed & np.isfinite(odds) & (odds > 1.0) & np.isfinite(close)
    pool_ids = frame.index.to_numpy()[eligible]
    pool_odds, pool_close = odds[eligible], close[eligible]
    group_codes, group_uniques = pd.factorize(frame["group"].to_numpy(dtype=object)[eligible])
    market_codes, market_uniques = pd.factorize(frame["market"].to_numpy(dtype=object)[eligible])
    fixture_codes, _ = pd.factorize(frame["fixture_id"].to_numpy(dtype=object)[eligible])
    n_bands = len(ODDS_BANDS) - 1
    n_markets = int(len(market_uniques))
    band_codes = odds_band(pool_odds).astype(np.int64)
    pool_key = (group_codes.astype(np.int64) * n_markets + market_codes.astype(np.int64)) * n_bands + band_codes
    order = np.argsort(pool_key, kind="stable")
    pool_ids, pool_odds, pool_close, pool_key, fixture_codes, group_codes, market_codes, band_codes = (
        pool_ids[order], pool_odds[order], pool_close[order], pool_key[order], fixture_codes[order],
        group_codes[order], market_codes[order], band_codes[order],
    )
    n_keys = int(len(group_uniques)) * n_markets * n_bands
    starts = np.searchsorted(pool_key, np.arange(n_keys), side="left")
    counts = np.searchsorted(pool_key, np.arange(n_keys), side="right") - starts
    position = pd.Series(np.arange(len(pool_ids)), index=pool_ids)

    leg_matrix = np.array([list(t) for t in clv["leg_ids"]], dtype=np.int64)
    if not np.isin(leg_matrix.reshape(-1), pool_ids).all():
        return nan_row
    own = position.loc[leg_matrix.reshape(-1)].to_numpy().reshape(leg_matrix.shape)
    real_key = pool_key[own]
    n_bets, n_legs = leg_matrix.shape
    rng = np.random.default_rng(seed)

    def draw(shape_keys: np.ndarray) -> np.ndarray:
        u = rng.random(shape_keys.shape)
        return starts[shape_keys] + np.minimum((u * counts[shape_keys]).astype(np.int64), counts[shape_keys] - 1)

    keys_full = np.broadcast_to(real_key, (n_replicates, n_bets, n_legs))
    picked = draw(keys_full)
    if n_legs > 1:
        for _ in range(20):
            codes = fixture_codes[picked]
            clash = np.zeros(codes.shape[:2], dtype=bool)
            for a in range(n_legs):
                for b in range(a + 1, n_legs):
                    clash |= codes[:, :, a] == codes[:, :, b]
            if not clash.any():
                break
            rows_r, rows_b = np.nonzero(clash)
            picked[rows_r, rows_b] = draw(real_key[rows_b])
        codes = fixture_codes[picked]
        clash = np.zeros(codes.shape[:2], dtype=bool)
        for a in range(n_legs):
            for b in range(a + 1, n_legs):
                clash |= codes[:, :, a] == codes[:, :, b]
    else:
        clash = np.zeros((n_replicates, n_bets), dtype=bool)
    odds_prod = pool_odds[picked].prod(axis=2)
    close_prod = pool_close[picked].prod(axis=2)
    diagnostics = {
        "random_same_window_share": float(np.mean(group_codes[picked] == group_codes[own])),
        "random_same_market_share": float(np.mean(market_codes[picked] == market_codes[own])),
        "random_same_band_share": float(np.mean(band_codes[picked] == band_codes[own])),
        "random_mean_leg_odds": float(np.mean(pool_odds[picked])),
    }
    with np.errstate(invalid="ignore"):
        net = tau * odds_prod * close_prod - 1.0
        raw = odds_prod * close_prod - 1.0
    net[clash] = np.nan
    raw[clash] = np.nan
    random_stats = {
        "mean_net": np.nanmean(net, axis=1),
        "median_net": np.nanmedian(net, axis=1),
        "share_raw": np.nanmean(np.where(np.isnan(raw), np.nan, (raw > 0).astype(float)), axis=1),
        "share_net": np.nanmean(np.where(np.isnan(net), np.nan, (net > 0).astype(float)), axis=1),
    }
    real_net = clv["clv_net"].to_numpy(dtype=float)
    real_raw = clv["clv_raw"].to_numpy(dtype=float)
    real_stats = {
        "mean_net": float(real_net.mean()),
        "median_net": float(np.median(real_net)),
        "share_raw": float((real_raw > 0).mean()),
        "share_net": float((real_net > 0).mean()),
    }
    out: dict[str, float] = {"random_replicates": int(n_replicates), **diagnostics}
    for key in keys:
        sample = random_stats[key]
        out[f"random_{key}_mean"] = float(np.nanmean(sample))
        out[f"random_{key}_p05"] = float(np.nanquantile(sample, 0.05))
        out[f"random_{key}_p95"] = float(np.nanquantile(sample, 0.95))
        out[f"p_random_{key}"] = float((1.0 + np.sum(sample >= real_stats[key])) / (len(sample) + 1.0))
    return out


# --------------------------------------------------------------------------- #
# One cell end to end, family-wise correction, verdicts
# --------------------------------------------------------------------------- #

MAX_LEGS = 5


def evaluate_cell(
    legs: LegTable,
    cell: CellSpec,
    *,
    taus: Mapping[str, float] = TAX_REGIMES,
    n_legs_list: Sequence[int] = tuple(range(1, MAX_LEGS + 1)),
    n_resamples: int = 4000,
    n_replicates: int = 200,
    seed: int = 0,
    keep_bets: bool = True,
    tags: Mapping[str, object] | None = None,
) -> tuple[list[dict[str, object]], list[pd.DataFrame]]:
    """Decide, settle and summarise one cell for every tax regime and number of legs.

    The gross-selected picks do not depend on the tax regime; the regime enters the single-bet gate, the accumulator
    gate, CLV and the settlement.  Returns the summary rows (cell keys, regime, legs, statistics, random baseline)
    and, if ``keep_bets``, the settled bets.
    """
    picks = select_gross_legs(legs.open, cell)
    rows: list[dict[str, object]] = []
    stored: list[pd.DataFrame] = []
    for regime, tau in taus.items():
        for n_legs in n_legs_list:
            bets = make_bets(picks, cell, tau, n_legs)
            settled = settle_bets(bets, legs.settle, cell, tau)
            row = {**cell.key(), **(tags or {}), "regime": regime, "tau": float(tau), "n_legs": int(n_legs)}
            row["n_picks_gross"] = int(len(picks))
            row.update(summarise_bets(settled, n_resamples=n_resamples, seed=seed))
            row.update(random_baseline(settled, legs, cell, tau, n_replicates=n_replicates, seed=seed))
            rows.append(row)
            if keep_bets and len(settled):
                stored.append(settled.assign(**cell.key(), **(tags or {}), regime=regime))
    return rows, stored


def holm_bh_by_family(table: pd.DataFrame, p_column: str, family_columns: Sequence[str] = ()) -> pd.DataFrame:
    """Holm and Benjamini-Hochberg adjusted copies of ``p_column`` inside every family (NaN stays NaN)."""
    out = table.copy()
    out[p_column + "_holm"] = np.nan
    out[p_column + "_bh"] = np.nan
    groups = [out.index] if not family_columns else [g.index for _, g in out.groupby(list(family_columns))]
    for index in groups:
        values = out.loc[index, p_column].to_numpy(dtype=float)
        out.loc[index, p_column + "_holm"] = holm_adjust(values)
        out.loc[index, p_column + "_bh"] = benjamini_hochberg_adjust(values)
    return out


def combine_devig_verdicts(summary: pd.DataFrame, *, alpha: float = 0.05) -> pd.DataFrame:
    """One row per (setup without the de-vig method), the conclusion under *every* de-vig method.

    A setup counts as positive only if the lower bound of the post-tax CLV interval is above zero under all three
    methods (intersection-union: the p-value of the setup is the largest of the three one-sided p-values), and its
    interval is readable (``interval_ok`` under all methods).  ``lo_min`` / ``mean_min`` are the worst values over the
    methods.  Holm and Benjamini-Hochberg over the rows given (choose the family by passing the right subset).
    """
    keys = ["market_set", "strategy", "book", "z", "seasons", "regime", "tau", "n_legs"]
    frame = summary
    if "polish_overround" in frame.columns:  # the assumed Polish overround is part of a setup's identity (0 = not a Polish book)
        frame = frame.assign(polish_overround=frame["polish_overround"].fillna(0.0))
        keys = [*keys, "polish_overround"]
    rows = []
    for values, part in frame.groupby(keys, sort=False):
        methods = part.set_index("method")
        complete = all(m in methods.index for m in DEVIG_ORDER)
        lows = methods["clv_net_lo"].reindex(DEVIG_ORDER)
        means = methods["clv_net_mean"].reindex(DEVIG_ORDER)
        p_values = methods["clv_net_ptest"].reindex(DEVIG_ORDER)
        readable = bool(methods["interval_ok"].reindex(DEVIG_ORDER).fillna(False).all())
        row = dict(zip(keys, values))
        if "family" in part.columns:
            row["family"] = "|".join(sorted({str(f) for f in part["family"].dropna()}))
        row.update(
            n_bets_min=int(methods["n_bets"].min()),
            n_bets_max=int(methods["n_bets"].max()),
            n_clv_min=int(methods["n_clv"].min()),
            interval_ok=bool(complete and readable),
            mean_min=float(means.min()) if complete else np.nan,
            lo_min=float(lows.min()) if complete else np.nan,
            p_iut=float(p_values.max()) if complete and readable else np.nan,
            positive_lb=bool(complete and readable and (lows > 0).all()),
            median_min=float(methods["clv_net_median"].reindex(DEVIG_ORDER).min()),
            share_beat_raw_min=float(methods["share_beat_raw"].reindex(DEVIG_ORDER).min()),
            p_random_share_max=float(methods["p_random_share_raw"].reindex(DEVIG_ORDER).max()),
            yield_flat_mean=float(methods["yield_flat"].mean()),
            yield_kelly_mean=float(methods["yield_kelly"].mean()),
        )
        rows.append(row)
    return pd.DataFrame(rows)


HEADLINE_STRATEGIES: tuple[str, ...] = ("reference", "reference_veto", "pool", "residual")


def cell_plan(seasons: Sequence[int] = SELECTION_SEASONS) -> list[tuple[str, CellSpec]]:
    """Every decision setup that is run, labelled by family.

    ``headline`` (the family the plan's criterion and the family-wise correction refer to): Bet365 prices, 1X2 and
    over/under 2.5 competing for one pick per match, haircut ``z = 1.645``, seasons 2023-2025, strategies
    reference / reference with veto / pool / residual, each under the three de-vig methods.  Everything else is a
    sensitivity family: ``control`` (model alone), ``z0`` (no haircut), ``books`` (Max and Avg prices), ``polish``
    (the two synthetic Polish books), ``split`` (one market at a time) and ``seasons_all`` (2022-2025 for the strategies that
    do not need an ex-ante model choice).  Residual cells for over/under are left out (the model is three-class).
    """
    plan: list[tuple[str, CellSpec]] = []

    def add(family: str, **kwargs) -> None:
        kwargs.setdefault("seasons", tuple(seasons))
        for method in DEVIG_ORDER:
            plan.append((family, CellSpec(method=method, **kwargs)))

    for strategy in HEADLINE_STRATEGIES:
        add("headline", strategy=strategy)
        add("z0", strategy=strategy, z=0.0)
    add("control", strategy="model")
    add("control", strategy="model", z=0.0)
    for book in ("max", "avg"):
        for strategy in HEADLINE_STRATEGIES:
            for z in (DEFAULT_Z, 0.0):
                add("books", strategy=strategy, book=book, z=z)
    for book in ("pl_ps", "pl_b365"):
        for strategy in HEADLINE_STRATEGIES:
            for z in (DEFAULT_Z, 0.0):
                add("polish", strategy=strategy, book=book, z=z)
    for market in MARKET_ORDER:
        for strategy in HEADLINE_STRATEGIES:
            if strategy == "residual" and market != "1x2":
                continue
            for z in (DEFAULT_Z, 0.0):
                add("split", strategy=strategy, market_set=market, z=z)
    for strategy in ("reference", "residual"):
        for z in (DEFAULT_Z, 0.0):
            add("seasons_all", strategy=strategy, z=z, seasons=(2022, *seasons))
    return plan


def apply_family_correction(
    verdicts: pd.DataFrame,
    families: Mapping[str, pd.Series],
    *,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """Holm and Benjamini-Hochberg adjusted p-values inside every named family and across every setup tried.

    ``p_iut`` (largest one-sided p-value over the de-vig methods) is the setup's p-value; a setup whose interval cannot
    be read (too few bets or matchdays) is a hypothesis that was tested with no power, so it enters with ``p = 1``: the
    family keeps its full size and the correction is not made lenient by leaving empty cells out.  For a family called
    ``name`` the columns ``p_holm_<name>``, ``p_bh_<name>`` and ``signal_<name>`` are added (``signal`` = positive lower
    bound under every de-vig method *and* Holm-significant inside the family); ``p_holm_all`` / ``signal_all`` use every
    setup tried as the family.
    """
    out = verdicts.copy()
    p = out["p_iut"].to_numpy(dtype=float)
    p = np.where(np.isfinite(p), p, 1.0)
    out["p_family"] = p
    for name, family_mask in families.items():
        mask = np.asarray(family_mask, dtype=bool)
        out[f"p_holm_{name}"] = np.nan
        out[f"p_bh_{name}"] = np.nan
        if mask.any():
            out.loc[mask, f"p_holm_{name}"] = holm_adjust(p[mask])
            out.loc[mask, f"p_bh_{name}"] = benjamini_hochberg_adjust(p[mask])
        out[f"signal_{name}"] = out["positive_lb"] & (out[f"p_holm_{name}"] < alpha)
        out[f"n_family_{name}"] = int(mask.sum())
    out["p_holm_all"] = holm_adjust(p)
    out["p_bh_all"] = benjamini_hochberg_adjust(p)
    out["signal_all"] = out["positive_lb"] & (out["p_holm_all"] < alpha)
    out["n_family_all"] = int(len(out))
    return out


def plan_family_masks(verdicts: pd.DataFrame) -> dict[str, pd.Series]:
    """The families of the plan's criterion.

    ``plan``: the prices a Polish player could plausibly face (Bet365 and the two synthetic Polish books, at both
    haircuts), 1X2 and over/under together, seasons 2023-2025, the four strategies, the default Polish overround
    (60 setups per book and haircut: 4 strategies x 3 regimes x 5 structures).  ``shopping``: the best (``max``) and
    average (``avg``) price of the football-data bookmakers, same grid: not a bookmaker one can bet at, an upper bound of
    what price comparison delivers.  ``headline``: the pre-set subset of ``plan`` with Bet365 and the full ``z = 1.645``
    haircut.
    """
    standard = (
        (verdicts["market_set"] == "all")
        & (verdicts["seasons"] == "-".join(str(s) for s in SELECTION_SEASONS))
        & verdicts["strategy"].isin(HEADLINE_STRATEGIES)
    )
    if "polish_overround" in verdicts.columns:
        standard = standard & (verdicts["polish_overround"].isin([0.0, DEFAULT_POLISH_OVERROUND]))
    return {
        "plan": standard & verdicts["book"].isin(["bet365", "pl_ps", "pl_b365"]),
        "shopping": standard & verdicts["book"].isin(["max", "avg"]),
        "headline": standard & (verdicts["book"] == "bet365") & (verdicts["z"] == DEFAULT_Z),
    }


def veto_effect(
    legs: LegTable,
    cell: CellSpec,
    *,
    n_resamples: int = 4000,
    seed: int = 0,
) -> dict[str, float]:
    """Does the model's opinion sort the legs that pass the price screen by their closing line value?

    Takes every leg of ``cell`` (its ``strategy`` is ignored: reference probability, book, de-vig method, haircut and
    seasons count) that passes the screen ``p_lower * odds > 1`` and has an ex-ante model forecast and a closing line,
    splits them by whether the model thinks the outcome at least as likely as the reference (``p_model >= reference``:
    the leg the veto keeps) or less likely, and compares the raw closing line value ``odds * fair_close - 1`` (equal to
    the post-tax value at ``tau = 1``) of the two groups.  ``diff`` is kept minus vetoed.  The two groups differ in their
    odds mix (a model shrunk to the league mean disagrees with the market mostly on long shots, whose prices deviate
    more), so ``diff_strat`` repeats the comparison inside strata of market, outcome (home / draw / away, under / over) and odds band and averages the stratum
    differences with the number of kept legs as weights.  Matchday-cluster bootstrap intervals (both groups are
    resampled together); a positive ``diff_strat`` interval would mean the model knows something the reference does not.
    """
    reference_cell = replace(cell, strategy="reference")
    frame = _cell_frame(legs.open, reference_cell)
    settle = legs.settle.loc[frame.index]
    p, scale, allowed = strategy_probability(frame, reference_cell)
    odds = frame[f"odds_{cell.book}"].to_numpy(dtype=float)
    p_model = frame["p_model"].to_numpy(dtype=float)
    close = settle[f"close_fair_{cell.method}"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        se = scale * np.sqrt(np.clip(p * (1.0 - p), 0.0, None))
        lower = np.maximum(p - cell.gate.z * se, 0.0)
        pool = allowed & np.isfinite(odds) & (odds > 1.0) & np.isfinite(p_model) & np.isfinite(close) & (lower * odds > 1.0)
        agree = pool & (p_model >= p)
        disagree = pool & (p_model < p)
    clv = odds * close - 1.0
    clusters = frame["cluster"].to_numpy(dtype=object)
    out: dict[str, float] = {"n_keep": int(agree.sum()), "n_veto": int(disagree.sum())}
    nan = float("nan")
    out.update(clv_keep=nan, clv_veto=nan, diff=nan, lo=nan, hi=nan, p_one_sided=nan)
    out.update(diff_strat=nan, lo_strat=nan, hi_strat=nan, p_one_sided_strat=nan)
    if agree.sum() < 5 or disagree.sum() < 5:
        return out
    use = agree | disagree
    keep = agree[use].astype(float)
    veto = 1.0 - keep
    values = clv[use]
    stratum = pd.factorize(
        pd.Series(frame["market"].to_numpy(dtype=object)[use])
        + "|"
        + pd.Series(frame["selection"].to_numpy(dtype=object)[use])
        + "|"
        + pd.Series(odds_band(odds[use]).astype(str))
    )[0]
    n_strata = int(stratum.max()) + 1
    columns = [values * keep, keep, values * veto, veto]
    for k in range(n_strata):
        member = (stratum == k).astype(float)
        columns.extend([values * keep * member, keep * member, values * veto * member, veto * member])
    draws = cluster_bootstrap_means(np.column_stack(columns), clusters[use], n_resamples=n_resamples, seed=seed)
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = draws[:, 0] / draws[:, 1] - draws[:, 2] / draws[:, 3]
        weights = np.zeros((len(draws), n_strata))
        gaps = np.zeros((len(draws), n_strata))
        for k in range(n_strata):
            base = 4 + 4 * k
            kept_mean = draws[:, base] / draws[:, base + 1]
            vetoed_mean = draws[:, base + 2] / draws[:, base + 3]
            valid = (draws[:, base + 1] > 0) & (draws[:, base + 3] > 0)
            weights[:, k] = np.where(valid, draws[:, base + 1], 0.0)
            gaps[:, k] = np.where(valid, kept_mean - vetoed_mean, 0.0)
        total = weights.sum(axis=1)
        strat = (weights * gaps).sum(axis=1) / total
    diff = diff[np.isfinite(diff)]
    strat = strat[np.isfinite(strat)]
    out.update(clv_keep=float(clv[agree].mean()), clv_veto=float(clv[disagree].mean()))
    out["diff"] = out["clv_keep"] - out["clv_veto"]
    # point estimate of the stratified difference on the sample itself
    point_weights, point_gaps = [], []
    for k in range(n_strata):
        member = stratum == k
        kept, vetoed = values[member & (keep > 0)], values[member & (veto > 0)]
        if len(kept) and len(vetoed):
            point_weights.append(len(kept))
            point_gaps.append(kept.mean() - vetoed.mean())
    if point_weights:
        out["diff_strat"] = float(np.average(point_gaps, weights=point_weights))
    if len(diff):
        out["lo"], out["hi"] = _ci(diff)
        out["p_one_sided"] = _p_positive(diff)
    if len(strat):
        out["lo_strat"], out["hi_strat"] = _ci(strat)
        out["p_one_sided_strat"] = _p_positive(strat)
    return out


# --------------------------------------------------------------------------- #
# Live archive: margins of the soft books next to Pinnacle
# --------------------------------------------------------------------------- #


def measure_live_margins(db_path: str | Path = LIVE_ODDS_DB) -> pd.DataFrame:
    """Current overround of every bookmaker in the live odds archive (1X2 and over/under 2.5), and the gap to Pinnacle.

    Uses the latest price per fixture, bookmaker and selection (``quote_state``).  Only complete books count.  ``gap_pp``
    is the mean of ``overround - overround_of_Pinnacle`` on the fixtures both quote.  The archive holds no
    Polish-licensed bookmaker (no Superbet, STS, Fortuna, Betclic); this table therefore measures soft books in
    general and the Polish overround stays an assumption.
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        names = pd.read_sql_query("SELECT DISTINCT bookmaker_id, bookmaker FROM quotes", connection)
        state = pd.read_sql_query(
            "SELECT fixture_id, bookmaker_id, bet_id, selection, odd FROM quote_state "
            "WHERE (bet_id = 1 AND selection IN ('Home', 'Draw', 'Away')) "
            "OR (bet_id = 5 AND selection IN ('Over 2.5', 'Under 2.5'))",
            connection,
        )
    state["odd"] = pd.to_numeric(state["odd"], errors="coerce")
    state = state[state["odd"] > 1.0]
    rows = []
    for market, bet_id, size in (("1x2", 1, 3), ("over_under_2.5", 5, 2)):
        part = state[state["bet_id"] == bet_id].copy()
        part["inverse"] = 1.0 / part["odd"]
        grouped = part.groupby(["fixture_id", "bookmaker_id"]).agg(inverse=("inverse", "sum"), count=("inverse", "size"))
        complete = grouped[grouped["count"] == size].reset_index()
        complete["overround"] = complete["inverse"] - 1.0
        pinnacle = complete[complete["bookmaker_id"] == 4].set_index("fixture_id")["overround"]
        for book_id, book in complete.groupby("bookmaker_id"):
            joined = book.set_index("fixture_id")["overround"].to_frame("overround").join(pinnacle.rename("pinnacle"), how="inner")
            rows.append(
                {
                    "market": market,
                    "bookmaker_id": int(book_id),
                    "n": int(len(book)),
                    "mean_overround": float(book["overround"].mean()),
                    "median_overround": float(book["overround"].median()),
                    "n_with_pinnacle": int(len(joined)),
                    "gap_to_pinnacle_pp": float((joined["overround"] - joined["pinnacle"]).mean() * 100.0) if len(joined) else np.nan,
                }
            )
    table = pd.DataFrame(rows).merge(names, on="bookmaker_id", how="left")
    return table.sort_values(["market", "mean_overround"]).reset_index(drop=True)
