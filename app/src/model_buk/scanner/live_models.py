"""Live model forecasts for upcoming fixtures, in the scanner's canonical format.

The models of the comparison protocol (``reports/Modele 1X2 i gole - pytanie 1.md`` and
``reports/Modele rozne kartki strzaly - pytanie 1.md``) forecast *finished* seasons under a walk-forward
test.  This module runs the same model classes on fixtures that have not been played yet:

- **Goal markets.**  1X2, total goals over/under 1.5 / 2.5 / 3.5 and both teams to score are forecast by the
  log-linear pool ``pool_models`` (:func:`~model_buk.evaluation.model_selection.apply_log_linear_pool`) of
  the protocol's models -- Dixon-Coles, NB2, GAS-Poisson, Elo-Davidson (1X2 only), pi-ratings + gradient
  boosting, pi-ratings logit and the league-frequency baseline -- with the weights the comparison chose for
  the season (``model_comparison.sqlite``, table ``pool_weights``: fitted on the season before, out of
  sample).  From the pooled 1X2 come the exact derived markets: double chance (``dc``), draw no bet
  (``dnb``) and the Asian handicaps at 0 and +-0.5 (``ah``; the line 0 is the same bet as ``dnb``).
- **Count markets.**  Corners (total, home, away) and yellow cards (total, home, away; the market names
  are ``cards_*``) from the NB2 count regression of :mod:`model_buk.models.count_models`.  The fit gives an
  expected count per team and a dispersion per league, so *any* line is priced from the full negative-
  binomial distribution (a quarter-line grid, see :data:`COUNT_LINE_GRID`).  Shots are not offered.

What ``p_model`` means
----------------------
``p_model`` is the *settlement-adjusted* win probability ``q = W / (W + L)`` with ``W = P(win) + P(half
win) / 2`` and ``L = P(loss) + P(half loss) / 2``: the probability that makes ``1 / q`` the fair decimal
price when a push refunds the stake.  On half-lines (no push) it is the plain probability, and it is
exactly what devigging a two-way price of the same line estimates, so it can be compared with ``p_fair``
of the sharp book leg by leg.  ``P(push)`` is not part of the frame.

Fit once a day, predict every hour
----------------------------------
:func:`fit_live_models` fits on everything known before ``as_of``: finished matches (status ``FT``) that
kicked off before ``as_of - 3 h`` (:func:`train_cutoff`; the protocol's result latency).  Such a fit is
valid for every fixture with ``kickoff >= as_of`` (its training matches kicked off before ``kickoff - 3
h``), which :meth:`LiveBundle.forecast` checks per fixture.  The fitted state is cached per (model, league,
date) under ``Model-Buk-Data/scanner/model_cache/fits/<YYYYMMDD>/``; :func:`forecast_fixtures` then only
predicts (milliseconds per league) and never touches the history database.  The boosters of the pooled
pi-ratings model are shared between days (``boosters/``, one file per market, parameters and 28-day
bucket, exactly as :mod:`model_buk.models.gbm_model` names them).

Hyper-parameters are the ones the protocol's inner fold picks for the season (the season before it, pooled
over the 47 protocol leagues): :func:`select_hyperparameters` runs that selection with the framework's own
function and stores the choice (``hyperparameters_<season>_protocol_leagues.json``).  Nothing here changes a
model or a protocol constant; a league without ``min_train_matches`` finished matches, a league that is not
in the history and a team the model has never seen (the model's documented newcomer prior) behave exactly as
in the protocol: no rows, never a made-up forecast.

Competitions
------------
The 47 protocol leagues are where the pool was validated.  The 26 non-European leagues that joined the archive
later (:data:`EXTRA_LEAGUE_IDS`) get the same models with the same hyper-parameters and weights but no
out-of-sample validation (``validated`` is False in the report, so the scanner can treat them with more
caution).  The pooled pi-ratings boosters are trained on the protocol leagues only (a fingerprint-identical
reuse of the protocol's boosters); the store that gives the forecast features holds every modelled league.
Cup competitions (:data:`CUP_LEAGUE_IDS`) are not modelled.  A referee is used by the count regressions only
when the fixtures frame has a ``referee`` column; upcoming fixtures usually have none, and the regression
then uses the league level for that feature.

Interfaces (column names are part of the contract)
--------------------------------------------------
- :func:`forecast_fixtures` ``(fixtures, as_of, *, config, include_components, include_counts) ->
  DataFrame`` with the model frame ``fixture_id, market, period, line, selection, p_model, model_name``
  (``model_name`` is ``pool_models`` for the goal markets, ``count_nb2`` for corners and cards; the
  per-fixture report is ``frame.attrs["report"]``; :func:`forecast_with_report` returns both).
- :func:`fit_live_models` ``(as_of, *, config, ...) -> dict`` (the nightly job), :func:`select_hyperparameters`
  ``(season, matches, team_stats, ...) -> dict``, :func:`load_pool_weights`, :func:`load_live_matches`,
  :func:`load_prematch_fixtures`, :func:`latest_fit`.
"""

from __future__ import annotations

import json
import math
import os
import pickle
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from model_buk.evaluation.count_markets import (  # noqa: E402
    _CARD_EVENTS_SQL,
    _STATS_SQL,
    FAMILIES,
    MIN_CARD_FEED_SHARE,
    apply_coverage_rule,
    card_feed_reliability,
    count_config,
    count_market,
)
from model_buk.evaluation.framework import (  # noqa: E402
    MARKET_1X2,
    Market,
    PreparedMatches,
    _select_combinations,
    prepare_matches,
)
from model_buk.evaluation.model_selection import (  # noqa: E402
    BASELINE_NAME,
    FIXED_MODELS,
    apply_log_linear_pool,
    load_panel,
    run_selection,
)
from model_buk.evaluation.protocol import (  # noqa: E402
    COMPARISON_CONFIG,
    DATA_ROOT,
    GOAL_MARKETS,
    HISTORY_DB,
    PREDICTIONS_DIR,
    PROTOCOL_ID,
    PROTOCOL_LEAGUE_IDS,
    load_history_matches,
)
from model_buk.ingestion.api_football_history import CUP_COMPETITIONS, WAVE3_LEAGUES  # noqa: E402
from model_buk.models.count_models import (  # noqa: E402
    DEFAULT_GRID as COUNT_GRID,
    CountEngine,
    count_spec,
    over_probability,
)
from model_buk.models.dynamic_adapters import (  # noqa: E402
    EloDavidsonForecaster,
    GasPoissonForecaster,
    elo_davidson_spec,
    goal_market_probabilities,
    poisson_score_matrix,
)
from model_buk.models.gbm_model import (  # noqa: E402
    DEFAULT_GRID as GBM_GRID,
    GbmSettings,
    LogitBank,
    PiGbmEngine,
    _fit_task as _gbm_fit_task,
    grid_combinations,
    params_key,
    pi_gbm_spec,
)
from model_buk.models.goal_adapters import (  # noqa: E402
    DixonColesGoalModel,
    FitCache as GoalFitCache,
    NegativeBinomialGoalModel,
    dixon_coles_spec,
    fit_goal_model,
    matrix_market_probabilities,
    nb2_spec,
)

EXTRA_LEAGUE_IDS: frozenset[int] = frozenset(ls.league_id for ls in WAVE3_LEAGUES)
"""The non-European leagues added to the archive after the protocol was fixed (modelled, not validated)."""

CUP_LEAGUE_IDS: frozenset[int] = frozenset(ls.league_id for ls in CUP_COMPETITIONS)
"""Cup competitions: never modelled (a cup's team strengths are not identifiable within the cup, and knockout
ties with extra time and neutral venues are not what the league models describe)."""

MODELLED_LEAGUE_IDS: frozenset[int] = PROTOCOL_LEAGUE_IDS | EXTRA_LEAGUE_IDS
"""The competitions that get forecasts: the 47 protocol leagues (validated) plus the extra leagues."""

LIVE_VERSION = "live-models-v1"
"""Bump when the cached state or its meaning changes: fits of another version are not used."""

MODEL_NAME_POOL = "pool_models"
MODEL_NAME_COUNT = "count_nb2"

DEFAULT_CACHE_DIR = DATA_ROOT / "scanner" / "model_cache"
LEGACY_BOOSTER_DIR = Path(__file__).resolve().parents[3] / "runtime" / "pi_gbm_cache"

LATENCY_HOURS = 3.0
"""The protocol's result latency: a match is known only this long after its kickoff."""

HISTORY_MAX_AGE_HOURS = 36.0
"""A fit from a history table built longer ago than this (the nightly rebuild has been failing) is logged loudly."""

GOAL_MARKET_NAMES: tuple[str, ...] = tuple(market.name for market in GOAL_MARKETS)
MARKET_BY_NAME: dict[str, Market] = {market.name: market for market in GOAL_MARKETS}
GOAL_COMPONENTS: tuple[str, ...] = (BASELINE_NAME, *FIXED_MODELS)
"""Pool components in the order of the comparison tables (baseline first)."""

COUNT_STATS: dict[str, str] = {"corners": "corners", "cards": "yellow"}
"""Canonical statistic -> family of :data:`~model_buk.evaluation.count_markets.FAMILIES` (cards = yellow cards)."""

COUNT_SCOPES: tuple[str, ...] = ("total", "home", "away")
COUNT_MARKET_NAMES: dict[tuple[str, str], str] = {
    ("corners", "total"): "corners_ou",
    ("corners", "home"): "corners_home_ou",
    ("corners", "away"): "corners_away_ou",
    ("cards", "total"): "cards_ou",
    ("cards", "home"): "cards_home_ou",
    ("cards", "away"): "cards_away_ou",
}

COUNT_LINE_STEP = 0.25
COUNT_LINE_GRID: dict[tuple[str, str], tuple[float, float]] = {
    ("corners", "total"): (6.0, 14.0),
    ("corners", "home"): (2.0, 8.0),
    ("corners", "away"): (2.0, 8.0),
    ("cards", "total"): (1.0, 8.0),
    ("cards", "home"): (0.5, 4.0),
    ("cards", "away"): (0.5, 4.0),
}
"""Lines priced for every fixture, as ``(lowest, highest)`` in steps of 0.25 (whole, half and quarter lines)."""

MODEL_FRAME_COLUMNS: tuple[str, ...] = ("fixture_id", "market", "period", "line", "selection", "p_model", "model_name")
UPCOMING_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})

_NS_PER_HOUR = 3_600_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR
_STATS_COLUMNS = ("fixture_id", "team_id", "total_shots", "shots_on_goal", "corners", "expected_goals")
_STORE_MATCH_COLUMNS = (
    "fixture_id",
    "league_code",
    "season",
    "kickoff_utc",
    "home_team_id",
    "away_team_id",
    "home_goals",
    "away_goals",
    "status_short",
)


# ---------------------------------------------------------------------------
# Configuration and time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveConfig:
    """Paths and the few settings of the live models (everything else is the protocol's)."""

    cache_dir: Path = DEFAULT_CACHE_DIR
    history_db: Path = HISTORY_DB
    comparison_db: Path = PREDICTIONS_DIR / "model_comparison.sqlite"
    predictions_dir: Path = PREDICTIONS_DIR
    legacy_booster_dir: Path | None = LEGACY_BOOSTER_DIR
    latency_hours: float = LATENCY_HOURS
    min_train_matches: int = COMPARISON_CONFIG.min_train_matches
    league_ids: frozenset[int] | None = MODELLED_LEAGUE_IDS
    max_fit_age_hours: float = 96.0
    keep_fits: int = 3
    gbm_settings: GbmSettings = field(default_factory=GbmSettings)

    def __post_init__(self) -> None:
        for name in ("cache_dir", "history_db", "comparison_db", "predictions_dir"):
            object.__setattr__(self, name, Path(getattr(self, name)))
        if self.legacy_booster_dir is not None:
            object.__setattr__(self, "legacy_booster_dir", Path(self.legacy_booster_dir))
        if self.league_ids is not None:
            object.__setattr__(self, "league_ids", frozenset(int(i) for i in self.league_ids))
        if self.latency_hours < 0 or self.min_train_matches < 1 or self.max_fit_age_hours <= 0 or self.keep_fits < 1:
            raise ValueError("invalid LiveConfig")

    @property
    def fits_dir(self) -> Path:
        return self.cache_dir / "fits"

    @property
    def boosters_dir(self) -> Path:
        return self.cache_dir / "boosters"

    def walk_forward(self):
        """The protocol's configuration (test seasons are irrelevant here) with this config's minimum history."""
        from dataclasses import replace

        return replace(
            COMPARISON_CONFIG,
            min_train_matches=self.min_train_matches,
            result_latency_hours=self.latency_hours,
        )


def utc(value: Any) -> pd.Timestamp:
    """A timestamp in UTC (naive values are read as UTC)."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _ns(value: Any) -> int:
    return int(utc(value).as_unit("ns").value)


def train_cutoff(fit_as_of: Any, latency_hours: float = LATENCY_HOURS) -> pd.Timestamp:
    """Exclusive kickoff bound of the training matches of a fit made at ``fit_as_of``.

    A match is usable only if it kicked off before ``fit_as_of - latency`` (its result was known); the fit
    is then valid for every fixture kicking off at or after ``fit_as_of``.  Module-level so the leakage
    test can be mutation-checked against a deliberately broken cut-off.
    """
    return utc(fit_as_of) - pd.Timedelta(hours=latency_hours)


def season_label(moment: Any) -> int:
    """The provider's season label of a date in the European convention (August starts season ``year``)."""
    stamp = utc(moment)
    return stamp.year if stamp.month >= 7 else stamp.year - 1


# ---------------------------------------------------------------------------
# Data: history, team statistics, fixtures
# ---------------------------------------------------------------------------


def _with_retries(action: Callable[[], Any], *, attempts: int = 6, wait_seconds: float = 20.0) -> Any:
    """Run a short read of a database that a nightly job may be rebuilding; retry when it is busy."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return action()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError, OSError) as error:
            last = error
            if attempt + 1 < attempts:
                time.sleep(wait_seconds)
    raise RuntimeError(f"could not read the database after {attempts} attempts: {last}")


def _connect_ro(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def load_live_matches(
    db_path: str | Path = HISTORY_DB,
    league_ids: frozenset[int] | None = None,
    *,
    min_card_feed_share: float = MIN_CARD_FEED_SHARE,
    before: Any = None,
) -> pd.DataFrame:
    """The ``matches`` table plus ``home_<stat>`` / ``away_<stat>`` columns, for every league of the archive.

    Same definitions as :func:`model_buk.evaluation.count_markets.load_count_matches` (shots, shots on target,
    corners, fouls, xG, yellow cards from the provider or from the event feed where it records cards, red
    cards, booking points; NULL stays NaN), except that ``league_ids=None`` keeps *every* league in the
    archive instead of the 47 of the protocol.  With ``before`` only matches that kicked off before that instant
    are kept *before* the card-feed reliability of a league-season is judged, so that judgement too uses nothing
    from after the cut-off.  The database is opened read-only and closed at once (a nightly job rebuilds the
    file).
    """

    def read() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        matches = load_history_matches(db_path, league_ids=league_ids)
        with closing(_connect_ro(db_path)) as connection:
            return (
                matches,
                pd.read_sql_query(_STATS_SQL, connection),
                pd.read_sql_query(_CARD_EVENTS_SQL, connection),
                pd.read_sql_query("SELECT DISTINCT fixture_id FROM events", connection),
            )

    matches, stats, cards, feeds = _with_retries(read)
    if before is not None:
        stamp = pd.to_datetime(matches["kickoff_utc"], utc=True, format="mixed", errors="coerce")
        matches = matches[(stamp < utc(before)).to_numpy()].reset_index(drop=True)
    has_feed = set(feeds["fixture_id"].tolist())
    frame = matches.copy()
    frame["_has_feed"] = frame["fixture_id"].isin(has_feed)
    frame["_card_feed"] = card_feed_reliability(frame, set(cards["fixture_id"].tolist()), min_share=min_card_feed_share)
    source = {
        "shots": "total_shots",
        "sot": "shots_on_goal",
        "corners": "corners",
        "fouls": "fouls",
        "xg": "expected_goals",
        "yellow_stat": "yellow_cards",
    }
    for side in ("home", "away"):
        part = stats[stats["side"] == side].drop_duplicates("fixture_id").set_index("fixture_id")
        for name, column in source.items():
            frame[f"{side}_{name}"] = frame["fixture_id"].map(part[column]).astype(float)
        keyed = cards.set_index(["fixture_id", "team_id"])
        index = pd.MultiIndex.from_arrays([frame["fixture_id"], frame[f"{side}_team_id"]])
        for name, column in (("ev_yellow", "ev_yellow"), ("ev_red", "ev_red"), ("ev_second", "ev_second_yellow")):
            counts = keyed[column].reindex(index).to_numpy(dtype=float)
            trusted = frame["_card_feed"].to_numpy()
            counts = np.where(np.isnan(counts) & frame["_has_feed"].to_numpy() & trusted, 0.0, counts)
            counts = np.where(trusted, counts, np.nan)
            frame[f"{side}_{name}"] = counts
        frame[f"{side}_yellow"] = frame[f"{side}_yellow_stat"].fillna(frame[f"{side}_ev_yellow"])
        frame[f"{side}_red"] = frame[f"{side}_ev_red"]
        frame[f"{side}_booking"] = (
            10.0 * (frame[f"{side}_ev_yellow"] - frame[f"{side}_ev_second"]) + 25.0 * frame[f"{side}_ev_red"]
        )
    no_fouls = (frame["home_fouls"] == 0) & (frame["away_fouls"] == 0)
    frame.loc[no_fouls, ["home_fouls", "away_fouls"]] = np.nan
    drop = [c for c in frame.columns if c.endswith(("_yellow_stat", "_ev_yellow", "_ev_red", "_ev_second"))]
    return frame.drop(columns=[*drop, "_has_feed", "_card_feed"])


def load_team_stats(db_path: str | Path = HISTORY_DB) -> pd.DataFrame:
    """Per team and match statistics used by the pi-ratings store (read-only; NULL stays NaN)."""

    def read() -> pd.DataFrame:
        with closing(_connect_ro(db_path)) as connection:
            return pd.read_sql_query(f"SELECT {', '.join(_STATS_COLUMNS)} FROM team_match_stats", connection)

    return _with_retries(read)


def history_built_at(db_path: str | Path = HISTORY_DB) -> str | None:
    """When the history tables were built (``build_info``), for the fit manifest; None if unknown."""
    try:
        with closing(_connect_ro(db_path)) as connection:
            row = connection.execute("SELECT built_at FROM build_info").fetchone()
        return None if row is None else str(row[0])
    except (sqlite3.Error, OSError):
        return None


def load_prematch_fixtures(
    db_path: str | Path,
    start: Any,
    end: Any,
    *,
    league_names: Mapping[int, tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Fixtures kicking off in ``[start, end)`` from the pre-match archive, in the fixtures-frame layout.

    Columns: ``fixture_id, league_id, league_name, country, season, round, kickoff_utc, status_short,
    home_team_id, home_team, away_team_id, away_team, home_goals, away_goals``.  The pre-match archive knows
    neither league names nor scores, so those are NaN unless ``league_names`` (``{league_id: (name,
    country)}``) is given.
    """
    lo, hi = utc(start), utc(end)

    def read() -> pd.DataFrame:
        with closing(_connect_ro(db_path)) as connection:
            return pd.read_sql_query(
                "SELECT fixture_id, league_id, season, round, kickoff_utc, status_short, home_team_id, home_team, "
                "away_team_id, away_team FROM fixtures",
                connection,
            )

    frame = _with_retries(read)
    stamp = pd.to_datetime(frame["kickoff_utc"], utc=True, format="mixed", errors="coerce")
    frame = frame[(stamp >= lo) & (stamp < hi)].reset_index(drop=True)
    names = league_names or {}
    frame["league_name"] = frame["league_id"].map(lambda i: names.get(int(i), (np.nan, np.nan))[0])
    frame["country"] = frame["league_id"].map(lambda i: names.get(int(i), (np.nan, np.nan))[1])
    frame["home_goals"] = np.nan
    frame["away_goals"] = np.nan
    order = [
        "fixture_id", "league_id", "league_name", "country", "season", "round", "kickoff_utc", "status_short",
        "home_team_id", "home_team", "away_team_id", "away_team", "home_goals", "away_goals",
    ]  # fmt: skip
    return frame[order]


# ---------------------------------------------------------------------------
# Pool weights and hyper-parameters
# ---------------------------------------------------------------------------


def load_pool_weights(
    season: int,
    db_path: str | Path = PREDICTIONS_DIR / "model_comparison.sqlite",
    *,
    kind: str = "log_linear",
    sample: str = "selection",
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Pool weights ``{market: {model: weight}}`` the comparison chose for ``season``.

    The weights of scored season ``S`` were fitted on the out-of-sample forecasts of ``S - 1`` (nothing of
    ``S`` is in them).  When ``season`` itself is not stored the latest stored season not after it is used
    (``meta["season_used"]`` says which).  Raises if the table has no season up to ``season``.
    """

    def read() -> pd.DataFrame:
        with closing(_connect_ro(db_path)) as connection:
            return pd.read_sql_query(
                "SELECT market, scored_season, fit_season, model, weight FROM pool_weights WHERE kind = ? AND sample = ?",
                connection,
                params=(kind, sample),
            )

    table = _with_retries(read)
    stored = sorted({int(s) for s in table["scored_season"]})
    usable = [s for s in stored if s <= int(season)]
    if not usable:
        raise ValueError(f"no stored pool weights for season {season} or earlier (stored: {stored})")
    used = max(usable)
    chosen = table[table["scored_season"] == used]
    weights: dict[str, dict[str, float]] = {}
    for market, group in chosen.groupby("market"):
        values = {str(row.model): float(row.weight) for row in group.itertuples()}
        total = sum(values.values())
        if any(v < 0 for v in values.values()) or abs(total - 1.0) > 1e-6:
            raise ValueError(f"pool weights of {market} do not form a distribution (sum {total})")
        weights[str(market)] = values
    meta = {
        "requested_season": int(season),
        "season_used": int(used),
        "fit_season": int(chosen["fit_season"].iloc[0]),
        "source": f"{Path(db_path).name}:pool_weights ({kind}, {sample})",
    }
    return weights, meta


def compute_pool_weights(
    season: int,
    predictions_dir: str | Path = PREDICTIONS_DIR,
    *,
    models: Sequence[str] = FIXED_MODELS,
) -> dict[str, dict[str, float]]:
    """Recompute the pool weights of ``season`` from the stored forecasts of ``season - 1``.

    Exactly what the comparison does (:func:`model_buk.evaluation.model_selection.run_selection`): the
    log-linear pool with the baseline and every model that has forecasts, fitted on the common matches of the
    season before.  For a season whose weights are already stored this reproduces the table.
    """
    weights: dict[str, dict[str, float]] = {}
    for market in GOAL_MARKETS:
        panel, _ = load_panel(market, [BASELINE_NAME, *models], predictions_dir)
        candidates = [BASELINE_NAME, *[m for m in models if m in panel.models]]
        run = run_selection(panel, candidates, [season])
        table = run.weights[run.weights["kind"] == "log_linear"]
        weights[market.name] = {str(r.model): float(r.weight) for r in table.itertuples()}
    return weights


def models_needed(weights: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    """Components with a positive weight in at least one market, in the order of :data:`GOAL_COMPONENTS`."""
    return tuple(m for m in GOAL_COMPONENTS if any(w.get(m, 0.0) > 0.0 for w in weights.values()))


def hyperparameters_path(cache_dir: str | Path, season: int) -> Path:
    """Where the protocol-league selection of a season is stored (the file name says which league set it used)."""
    return Path(cache_dir) / f"hyperparameters_{int(season)}_protocol_leagues.json"


def load_hyperparameters(cache_dir: str | Path, season: int) -> dict[str, Any] | None:
    path = hyperparameters_path(cache_dir, season)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def stored_hyperparameters(predictions_dir: str | Path = PREDICTIONS_DIR) -> dict[str, Any]:
    """The hyper-parameters the protocol chose for the *latest test season* it ran (fallback only).

    Read from the ``*_selections`` tables next to the stored forecasts.  It is the choice made for the last
    tested season (fitted on the season before it), not the one for the coming season, so use it only when
    :func:`select_hyperparameters` has not been run.
    """
    root = Path(predictions_dir)
    out: dict[str, Any] = {"source": "stored selection of the latest protocol test season", "season": None}
    for model in ("dixon_coles", "nb2", "elo_davidson", "pi_gbm"):
        path = root / f"{model}.sqlite"
        if not path.exists():
            continue
        entry: dict[str, Any] = {}
        with closing(_connect_ro(path)) as connection:
            for market in GOAL_MARKETS:
                table = "forecasts_" + market.name.replace(".", "_") + "_selections"
                try:
                    frame = pd.read_sql_query(f"SELECT outer_season, params FROM {table}", connection)
                except (pd.errors.DatabaseError, sqlite3.Error):
                    continue
                if frame.empty:
                    continue
                last = frame.sort_values("outer_season").iloc[-1]
                out["season"] = int(last["outer_season"])
                entry[market.name] = json.loads(last["params"])
        if entry:
            out[model] = entry
    count_db = root / "count_evaluation.sqlite"
    if count_db.exists():
        with closing(_connect_ro(count_db)) as connection:
            frame = pd.read_sql_query("SELECT market, model, outer_season, params FROM selections WHERE model = 'count_nb2'", connection)
        if not frame.empty:
            frame = frame[frame["outer_season"] == frame["outer_season"].max()]
            parts = frame["market"].str.rsplit("_", n=2, expand=True)
            frame = frame.assign(family=parts[0], scope=parts[1])
            counts: dict[str, dict[str, Any]] = {}
            for (family, scope), group in frame.groupby(["family", "scope"]):
                counts.setdefault(str(family), {})[str(scope)] = json.loads(group["params"].mode().iloc[0])
            out["count"] = counts
    return out


# ---------------------------------------------------------------------------
# Probability helpers: pooling, derived markets, count distributions
# ---------------------------------------------------------------------------


def pool_probabilities(components: Mapping[str, np.ndarray], weights: Mapping[str, float]) -> np.ndarray:
    """Log-linear pool of the components with positive weight (the comparison's ``pool_models``).

    ``components[name]`` is an ``(n, K)`` matrix, a NaN row meaning no forecast.  Rows where any positive-
    weight component has no forecast (or the component is absent) come out NaN, as in the comparison (a
    match is left NaN when a pool component has no forecast); the other rows are
    :func:`~model_buk.evaluation.model_selection.apply_log_linear_pool` of the same weights.
    """
    names = [m for m, w in weights.items() if w > 0.0]
    if not names:
        raise ValueError("a pool needs at least one positive weight")
    reference = next(iter(components.values()), None)
    if reference is None:
        raise ValueError("no component forecasts")
    n, k = np.asarray(reference).shape
    out = np.full((n, k), np.nan)
    if any(m not in components for m in names):
        return out
    stacked = [np.asarray(components[m], dtype=float) for m in names]
    valid = np.ones(n, dtype=bool)
    for matrix in stacked:
        valid &= ~np.isnan(matrix).any(axis=1)
    if valid.any():
        out[valid] = apply_log_linear_pool([matrix[valid] for matrix in stacked], np.array([weights[m] for m in names], dtype=float))
    return out


def derived_1x2_markets(p: np.ndarray) -> dict[tuple[str, float, str], np.ndarray]:
    """Exact markets implied by a 1X2 forecast ``(n, 3)`` (home, draw, away).

    ``(market, line, selection) -> probability``: double chance ``1x`` / ``x2`` / ``12``, draw no bet (the
    stake comes back on a draw, so ``home / (home + away)``: the settlement-adjusted probability) and the Asian
    handicaps at 0 and +-0.5 (line is the handicap of the home side; ``ah`` 0 equals ``dnb``).
    """
    home, draw, away = p[:, 0], p[:, 1], p[:, 2]
    nan = float("nan")
    return {
        ("dc", nan, "1x"): home + draw,
        ("dc", nan, "x2"): draw + away,
        ("dc", nan, "12"): home + away,
        ("dnb", nan, "home"): home / (home + away),
        ("dnb", nan, "away"): away / (home + away),
        ("ah", 0.0, "home"): home / (home + away),
        ("ah", 0.0, "away"): away / (home + away),
        ("ah", -0.5, "home"): home,
        ("ah", -0.5, "away"): draw + away,
        ("ah", 0.5, "home"): home + draw,
        ("ah", 0.5, "away"): away,
    }


def count_over_probability(mean: np.ndarray, alpha: float, line: float, kind: str = "nb2") -> np.ndarray:
    """Settlement-adjusted probability ``q`` of *over* ``line`` for a Poisson / NB2 count with mean ``mean``.

    A whole line ``c`` is a win when ``X > c``, a push at ``X = c`` and a loss when ``X < c``; a half line has
    no push; a quarter line ``c + 0.25`` (``c + 0.75``) stakes half on ``c`` and half on ``c + 0.5``
    (``c + 0.5`` and ``c + 1``).  ``q = W / (W + L)`` with the win and loss mass of the two halves (see the
    module docstring); ``P(under) = 1 - q`` exactly.
    """
    mean = np.asarray(mean, dtype=float)
    doubled = round(line * 4)
    if abs(line * 4 - doubled) > 1e-9:
        raise ValueError("lines must be multiples of 0.25")
    if doubled % 2 == 0:
        parts = [(doubled / 4.0, 1.0)]
    else:
        parts = [((doubled - 1) / 4.0, 0.5), ((doubled + 1) / 4.0, 0.5)]
    win = np.zeros_like(mean)
    loss = np.zeros_like(mean)
    for component, weight in parts:
        win += weight * over_probability(mean, alpha, component, kind)
        loss += weight * (1.0 - over_probability(mean, alpha, math.ceil(component) - 1.0, kind))
    return win / (win + loss)


def count_lines(stat: str, scope: str) -> np.ndarray:
    """The quarter-line grid priced for a statistic and scope (:data:`COUNT_LINE_GRID`)."""
    low, high = COUNT_LINE_GRID[(stat, scope)]
    return np.round(np.arange(low, high + COUNT_LINE_STEP / 2, COUNT_LINE_STEP), 2)


# ---------------------------------------------------------------------------
# Rows of the model frame
# ---------------------------------------------------------------------------


def _rows(
    fixture_ids: np.ndarray,
    market: str,
    line: float,
    selection: str,
    p: np.ndarray,
    model_name: str,
) -> pd.DataFrame:
    keep = ~np.isnan(p)
    n = int(keep.sum())
    return pd.DataFrame(
        {
            "fixture_id": fixture_ids[keep].astype(np.int64),
            "market": np.full(n, market, dtype=object),
            "period": np.full(n, "ft", dtype=object),
            "line": np.full(n, line, dtype=float),
            "selection": np.full(n, selection, dtype=object),
            "p_model": np.asarray(p, dtype=float)[keep],
            "model_name": np.full(n, model_name, dtype=object),
        }
    )


def goal_market_rows(fixture_ids: np.ndarray, market_name: str, p: np.ndarray, model_name: str, *, derived: bool = True) -> list[pd.DataFrame]:
    """Model-frame rows of one goal market's forecast ``(n, K)`` (plus the derived markets for 1X2)."""
    rows: list[pd.DataFrame] = []
    nan = float("nan")
    if market_name == "1x2":
        for j, selection in enumerate(("home", "draw", "away")):
            rows.append(_rows(fixture_ids, "1x2", nan, selection, p[:, j], model_name))
        if derived:
            for (market, line, selection), values in derived_1x2_markets(p).items():
                rows.append(_rows(fixture_ids, market, line, selection, values, model_name))
    elif market_name.startswith("over_under_"):
        line = float(market_name.removeprefix("over_under_"))
        rows.append(_rows(fixture_ids, "ou", line, "under", p[:, 0], model_name))
        rows.append(_rows(fixture_ids, "ou", line, "over", p[:, 1], model_name))
    elif market_name == "btts":
        rows.append(_rows(fixture_ids, "btts", nan, "no", p[:, 0], model_name))
        rows.append(_rows(fixture_ids, "btts", nan, "yes", p[:, 1], model_name))
    else:
        raise ValueError(f"not a goal market: {market_name}")
    return rows


def count_market_rows(
    fixture_ids: np.ndarray,
    stat: str,
    scope: str,
    mean: np.ndarray,
    alpha: float,
    kind: str,
    model_name: str = MODEL_NAME_COUNT,
) -> list[pd.DataFrame]:
    """Model-frame rows of one count market over its whole line grid (over and under)."""
    market = COUNT_MARKET_NAMES[(stat, scope)]
    lines = count_lines(stat, scope)
    over = np.column_stack([count_over_probability(mean, alpha, float(line), kind) for line in lines])  # (n, lines)
    ids = np.repeat(np.asarray(fixture_ids, dtype=np.int64), len(lines))
    grid = np.tile(lines, len(fixture_ids))
    frames = []
    for selection, p in (("over", over.ravel()), ("under", 1.0 - over.ravel())):
        keep = ~np.isnan(p)
        n = int(keep.sum())
        frames.append(
            pd.DataFrame(
                {
                    "fixture_id": ids[keep],
                    "market": np.full(n, market, dtype=object),
                    "period": np.full(n, "ft", dtype=object),
                    "line": grid[keep].astype(float),
                    "selection": np.full(n, selection, dtype=object),
                    "p_model": p[keep],
                    "model_name": np.full(n, model_name, dtype=object),
                }
            )
        )
    return frames


# ---------------------------------------------------------------------------
# Fitting one league
# ---------------------------------------------------------------------------


def _baseline_probabilities(train: pd.DataFrame, alpha: float = 1.0) -> dict[str, np.ndarray]:
    """League outcome frequencies per goal market with Laplace smoothing (the protocol's ``LeagueFrequencyBaseline``)."""
    out: dict[str, np.ndarray] = {}
    for market in GOAL_MARKETS:
        y = market.outcome(train)
        y = y[~np.isnan(y)].astype(np.int64)
        counts = np.bincount(y, minlength=len(market.classes)).astype(float) + alpha
        out[market.name] = counts / counts.sum()
    return out


def _fit_goal_league(task: dict[str, Any]) -> dict[str, Any]:
    """Fit the per-league goal models of the plan on one league's training frame (worker entry point)."""
    started = time.perf_counter()
    train: pd.DataFrame = task["train"]
    plan: Mapping[str, Any] = task["plan"]
    fits: dict[str, Any] = {}
    status: dict[str, str] = {}
    for kind in ("dixon_coles", "nb2"):
        if kind in plan["models"]:
            fitted = {}
            for half_life in plan["half_lives"][kind]:
                fitted[float(half_life)] = fit_goal_model(kind, train, half_life_days=float(half_life), cache=None)
            fits[kind] = fitted
            status[kind] = "ok" if all(f.converged for f in fitted.values()) else "not_converged"
    if "gas_poisson" in plan["models"]:
        fits["gas_poisson"] = GasPoissonForecaster(MARKET_1X2).fit(train)
        status["gas_poisson"] = "ok" if fits["gas_poisson"]._fitted.fit.converged else "not_converged"
    if "elo_davidson" in plan["models"]:
        fits["elo_davidson"] = EloDavidsonForecaster(float(plan["elo_k"])).fit(train)
        status["elo_davidson"] = "ok"
    if BASELINE_NAME in plan["models"]:
        fits[BASELINE_NAME] = _baseline_probabilities(train)
        status[BASELINE_NAME] = "ok"
    return {
        "league_code": task["league_code"],
        "fits": fits,
        "status": status,
        "n_train": len(train),
        "max_train_kickoff": train["kickoff"].max().isoformat(),
        "seconds": time.perf_counter() - started,
    }


_COUNT_KEEP = ("fixture_id", "league_code", "season", "kickoff", "home_team_id", "away_team_id", "referee")


def _count_train_columns(family: str) -> list[str]:
    fam = FAMILIES[family]
    names = [fam.name, *fam.aux]
    columns = list(_COUNT_KEEP)
    for name in names:
        for side in ("home", "away"):
            column = f"{side}_{name}"
            if column not in columns:
                columns.append(column)
    return columns


# ---------------------------------------------------------------------------
# Hyper-parameter selection (the protocol's inner fold for the coming season)
# ---------------------------------------------------------------------------


def _matchday_starts(prep: PreparedMatches, seasons: Iterable[int]) -> np.ndarray:
    frame = pd.DataFrame({"matchday": prep.matchday, "t": prep.t, "season": prep.season})
    frame = frame[frame["season"].isin([int(s) for s in seasons])]
    return frame.groupby("matchday")["t"].min().to_numpy(dtype=np.int64)


def _inner_choice(spec, prep: PreparedMatches, market: Market, config, season: int) -> tuple[dict[str, Any], float, int]:
    """The framework's own pooled inner-fold choice for outer ``season``: ``(params, inner log-loss, n)``."""
    combos = spec.combinations()
    available = np.unique(prep.season)
    _, pooled_choice, scores = _select_combinations(spec, combos, prep, market, config, int(season), available, {})
    row = scores[(scores["league"] == "ALL") & (scores["combo"] == pooled_choice)].iloc[0]
    return dict(combos[pooled_choice]), float(row["inner_log_loss"]), int(row["n"])


def _select_dc_nb2(kind: str, matches: pd.DataFrame, season: int, config) -> dict[str, Any]:
    cache = GoalFitCache()
    out: dict[str, Any] = {}
    for market in GOAL_MARKETS:
        prep = prepare_matches(matches, market, config)
        spec = (dixon_coles_spec if kind == "dixon_coles" else nb2_spec)(market, cache=cache)
        params, loss, n = _inner_choice(spec, prep, market, config, season)
        out[market.name] = {"params": params, "inner_log_loss": loss, "n": n}
    return out


def _select_elo(matches: pd.DataFrame, season: int, config) -> dict[str, Any]:
    prep = prepare_matches(matches, MARKET_1X2, config)
    params, loss, n = _inner_choice(elo_davidson_spec(), prep, MARKET_1X2, config, season)
    return {MARKET_1X2.name: {"params": params, "inner_log_loss": loss, "n": n}}


def _select_pi_gbm(matches: pd.DataFrame, stats: pd.DataFrame, season: int, config, live: LiveConfig, n_jobs: int, log) -> dict[str, Any]:
    engine = PiGbmEngine(matches, stats, latency_hours=live.latency_hours, settings=live.gbm_settings, cache_dir=live.boosters_dir)
    combos = grid_combinations(GBM_GRID)
    out: dict[str, Any] = {}
    for market in GOAL_MARKETS:
        prep = prepare_matches(matches, market, config)
        sub = prep.subset(prep.season < season)
        inner_season = int(np.unique(sub.season)[-1])
        cuts = engine.required_cuts(_matchday_starts(sub, [inner_season]))
        _prefetch_boosters(engine, live, {(market.name, params_key(p)): (market, dict(p)) for p in combos}, cuts, n_jobs, log)
        spec = pi_gbm_spec(engine, market, GBM_GRID)
        params, loss, n = _inner_choice(spec, prep, market, config, season)
        out[market.name] = {"params": params, "inner_log_loss": loss, "n": n}
        log(f"  pi_gbm {market.name}: {params} (inner log-loss {loss:.4f}, n={n})")
    return out


def _select_count_family(family: str, matches: pd.DataFrame, season: int, live: LiveConfig, log) -> dict[str, Any]:
    fam = FAMILIES[family]
    engine = CountEngine(fam, "nb2")
    cfg = count_config(live.walk_forward())
    combos_spec = None
    by_scope: dict[str, list[dict[int, float]]] = {scope: [] for scope in COUNT_SCOPES}
    matches, _ = apply_coverage_rule(matches, families=(family,))

    for scope in COUNT_SCOPES:
        lines = fam.total_lines if scope == "total" else fam.team_lines
        for line in lines:
            market = count_market(fam, scope, line)
            prep = prepare_matches(matches, market, cfg)
            spec = count_spec(engine, scope, line)
            combos = spec.combinations()
            available = np.unique(prep.season)
            _, _, scores = _select_combinations(spec, combos, prep, market, cfg, int(season), available, {})
            rows = scores[scores["league"] == "ALL"].sort_values("combo")
            by_scope[scope].append({int(r.combo): float(r.inner_log_loss) for r in rows.itertuples()})
            combos_spec = combos
    out: dict[str, Any] = {}
    for scope, tables in by_scope.items():
        means = {i: float(np.mean([t[i] for t in tables])) for i in tables[0]}
        best = min(means, key=means.get)
        out[scope] = {"params": dict(combos_spec[best]), "inner_log_loss": means[best], "n_lines": len(tables)}
        log(f"  count {family} {scope}: {out[scope]['params']} (mean inner log-loss {means[best]:.4f} over {len(tables)} lines)")
    return out


def _select_task(name: str, matches: pd.DataFrame, stats: pd.DataFrame | None, season: int, live: LiveConfig, n_jobs: int) -> tuple[str, Any, float]:
    """One selection job for a worker process; returns ``(name, result, seconds)``."""
    started = time.perf_counter()
    config = live.walk_forward()
    log = lambda text: print(f"[select {name}] {text}", flush=True)  # noqa: E731
    if name in ("dixon_coles", "nb2"):
        result = _select_dc_nb2(name, matches, season, config)
    elif name == "elo_davidson":
        result = _select_elo(matches, season, config)
    elif name == "pi_gbm":
        result = _select_pi_gbm(matches, stats, season, config, live, n_jobs, log)
    elif name.startswith("count_"):
        result = _select_count_family(name.removeprefix("count_"), matches, season, live, log)
    else:
        raise ValueError(f"unknown selection task {name}")
    return name, result, time.perf_counter() - started


def select_hyperparameters(
    season: int,
    matches: pd.DataFrame,
    team_stats: pd.DataFrame,
    *,
    config: LiveConfig | None = None,
    models: Sequence[str] | None = None,
    families: Sequence[str] = ("corners", "yellow"),
    n_jobs: int = 1,
    write: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the protocol's inner-fold selection for ``season`` and store it (``hyperparameters_<season>.json``).

    For every model with a grid the framework's own :func:`~model_buk.evaluation.framework._select_combinations`
    walks forward over the season before ``season`` (matches of earlier seasons only, one refit before every
    matchday, pooled over leagues) and takes the setting with the lowest pooled log-loss -- what
    ``evaluate_model`` does for the outer season ``season``.  Dixon-Coles / NB2 (half-life) and pi-ratings
    boosting (depth) are chosen per market, Elo-Davidson (K) for 1X2, and the count regressions (ridge, half-life)
    per family and scope as the setting with the lowest mean inner log-loss over the lines of that scope.  Models
    without a grid (GAS-Poisson, pi-ratings logit) need nothing.  ``models`` defaults to the ones with positive
    pool weight for ``season``.
    """
    live = config or LiveConfig()
    if models is None:
        weights, _ = load_pool_weights(season, live.comparison_db)
        models = models_needed(weights)
    # The protocol chooses on the pooled forecasts of its own 47 leagues; leagues added later never enter the choice.
    if "league_id" in matches.columns:
        matches = matches[matches["league_id"].isin(PROTOCOL_LEAGUE_IDS)].reset_index(drop=True)
        team_stats = team_stats[team_stats["fixture_id"].isin(set(matches["fixture_id"]))].reset_index(drop=True)
    tasks = [m for m in ("dixon_coles", "nb2", "elo_davidson", "pi_gbm") if m in models]
    tasks += [f"count_{family}" for family in families]
    log(f"selecting hyper-parameters for season {season}: {tasks}")
    started = time.perf_counter()
    if n_jobs > 1 and len(tasks) > 1:
        from joblib import Parallel, delayed

        results = Parallel(n_jobs=min(n_jobs, len(tasks)), backend="loky")(
            delayed(_select_task)(name, matches, team_stats, season, live, 1) for name in tasks
        )
    else:
        results = [_select_task(name, matches, team_stats, season, live, n_jobs) for name in tasks]
    out: dict[str, Any] = {
        "version": LIVE_VERSION,
        "protocol_id": PROTOCOL_ID,
        "league_scope": "the 47 protocol leagues",
        "season": int(season),
        "source": f"framework inner fold on season {int(season) - 1}, pooled over leagues (evaluation.framework._select_combinations)",
        "computed_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "seconds": {},
        "inner_log_loss": {},
        "count": {},
    }
    for name, result, seconds in results:
        out["seconds"][name] = round(seconds, 1)
        if name.startswith("count_"):
            out["count"][name.removeprefix("count_")] = {scope: entry["params"] for scope, entry in result.items()}
            out["inner_log_loss"][name] = {scope: entry["inner_log_loss"] for scope, entry in result.items()}
        else:
            out[name] = {market: entry["params"] for market, entry in result.items()}
            out["inner_log_loss"][name] = {market: entry["inner_log_loss"] for market, entry in result.items()}
    out["total_seconds"] = round(time.perf_counter() - started, 1)
    if write:
        path = hyperparameters_path(live.cache_dir, season)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, out)
        log(f"stored {path} ({out['total_seconds']}s)")
    return out


def default_hyperparameters(season: int, config: LiveConfig, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The stored selection for ``season``, else the protocol's latest stored choice (with a loud warning)."""
    stored = load_hyperparameters(config.cache_dir, season)
    if stored is not None:
        return stored
    fallback = stored_hyperparameters(config.predictions_dir)
    log(
        f"WARNING: no {hyperparameters_path(config.cache_dir, season).name} in {config.cache_dir}; using the protocol's stored choice for "
        f"season {fallback.get('season')} (run `fit_live_models.py select` to compute the choice for {season})"
    )
    fallback["season"] = int(season)
    return fallback


# ---------------------------------------------------------------------------
# Booster fitting (parallel, resumable, with the protocol's cached boosters as a seed)
# ---------------------------------------------------------------------------


def _booster_task(*args: Any) -> dict[str, Any]:
    """Worker entry point: fit one booster."""
    return _gbm_fit_task(*args)


def _seed_booster(path: Path | None, legacy_dir: Path | None) -> bool:
    """Copy a booster the protocol run already fitted (same name = same rows, settings and parameters)."""
    if path is None or path.exists() or legacy_dir is None:
        return False
    source = Path(legacy_dir) / path.name
    if not source.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path)
    return True


def _prefetch_boosters(
    engine: PiGbmEngine,
    live: LiveConfig,
    wanted: Mapping[Any, tuple[Market, dict[str, Any]]],
    cuts: Sequence[int],
    n_jobs: int,
    log: Callable[[str], None],
) -> None:
    """Make sure every (market, params, cut) booster is on disk: seed from the protocol cache, fit the rest."""
    from dataclasses import asdict

    bank = engine.bank
    missing: list[tuple[Market, dict[str, Any], int, Path]] = []
    seeded = 0
    for market, params in wanted.values():
        for cut in sorted({int(c) for c in cuts}):
            if not bank.usable(cut):
                continue
            path = bank.path(market, params, cut)
            if path is None or path.exists():
                continue
            if _seed_booster(path, live.legacy_booster_dir):
                seeded += 1
            else:
                missing.append((market, params, cut, path))
    if seeded:
        log(f"boosters: {seeded} seeded from the protocol cache")
    if not missing:
        return
    root = bank.write_matrix_files(sorted({m for m, *_ in missing}, key=lambda m: m.name))
    settings = asdict(bank.settings)
    log(f"boosters: fitting {len(missing)} with {min(n_jobs, len(missing))} worker(s)")
    jobs = [(str(root), market.name, params, settings, cut, str(path)) for market, params, cut, path in missing]
    started = time.perf_counter()
    if n_jobs > 1 and len(jobs) > 1:
        from joblib import Parallel, delayed

        outcomes = Parallel(n_jobs=min(n_jobs, len(jobs)), backend="loky")(delayed(_booster_task)(*job) for job in jobs)
    else:
        outcomes = [_booster_task(*job) for job in jobs]
    for info in outcomes:
        log(f"  fitted {info['market']} {info['params']} rows={info['rows']} iters={info['best_iteration']} {info['seconds']:.1f}s")
    log(f"boosters: done in {time.perf_counter() - started:.0f}s")


# ---------------------------------------------------------------------------
# Files: atomic writes, fit directories
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _write_pickle(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, path)


def _read_pickle(path: Path) -> Any:
    with open(path, "rb") as handle:
        return pickle.load(handle)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(name))


def fit_directory(config: LiveConfig, fit_as_of: Any) -> Path:
    return config.fits_dir / utc(fit_as_of).strftime("%Y%m%d")


def _prune_fits(config: LiveConfig, keep_dir: Path, log: Callable[[str], None]) -> None:
    """Delete the fit directories older than the newest ``keep_fits`` (the cache is derived data)."""
    directories = sorted(p for p in config.fits_dir.glob("*") if p.is_dir() and (p / "manifest.json").exists())
    for old in directories[: -config.keep_fits]:
        if old != keep_dir:
            shutil.rmtree(old, ignore_errors=True)
            log(f"pruned old fit {old.name}")


# ---------------------------------------------------------------------------
# The nightly fit
# ---------------------------------------------------------------------------


def _goal_plan(models: Sequence[str], hyperparameters: Mapping[str, Any], weights: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """Which goal models to fit per league and with which parameters (only what the pool weights use)."""
    half_lives: dict[str, list[float]] = {}
    for kind in ("dixon_coles", "nb2"):
        if kind in models:
            used = {
                float(hyperparameters[kind][market]["half_life_days"])
                for market in GOAL_MARKET_NAMES
                if weights.get(market, {}).get(kind, 0.0) > 0.0
            }
            half_lives[kind] = sorted(used)
    elo_k = None
    if "elo_davidson" in models:
        elo_k = float(hyperparameters["elo_davidson"][MARKET_1X2.name]["k_factor"])
    return {"models": tuple(models), "half_lives": half_lives, "elo_k": elo_k}


def _pi_params(hyperparameters: Mapping[str, Any], weights: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, Any]]:
    return {
        market: dict(hyperparameters["pi_gbm"][market])
        for market in GOAL_MARKET_NAMES
        if weights.get(market, {}).get("pi_gbm", 0.0) > 0.0
    }


def fit_live_models(
    as_of: Any = None,
    *,
    config: LiveConfig | None = None,
    matches: pd.DataFrame | None = None,
    team_stats: pd.DataFrame | None = None,
    leagues: Iterable[str] | None = None,
    weights: Mapping[str, Mapping[str, float]] | None = None,
    hyperparameters: Mapping[str, Any] | None = None,
    families: Sequence[str] = ("corners", "yellow"),
    n_jobs: int = 1,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fit every live model on everything known before ``as_of`` and cache it (the nightly job).

    Trains on finished (``FT``) matches that kicked off before :func:`train_cutoff` (``as_of - 3 h``), one
    model per league and per pool component with positive weight, the count regressions per league and
    family, and the pooled pi-ratings boosters / logit.  ``matches`` / ``team_stats`` default to the history
    database; leagues with fewer than ``min_train_matches`` training matches are skipped.  Everything is
    written under ``<cache_dir>/fits/<YYYYMMDD>/`` and the ``manifest.json`` last, so an interrupted fit is
    never picked up.  Returns the manifest.
    """
    live = config or LiveConfig()
    started = time.perf_counter()
    fit_as_of = utc(as_of if as_of is not None else pd.Timestamp.now(tz="UTC"))
    cutoff = train_cutoff(fit_as_of, live.latency_hours)
    season = season_label(fit_as_of)
    seconds: dict[str, float] = {}

    if weights is None:
        weights, weights_meta = load_pool_weights(season, live.comparison_db)
    else:
        weights_meta = {"source": "given", "season_used": None}
    needed = models_needed(weights)
    hp = dict(hyperparameters) if hyperparameters is not None else default_hyperparameters(season, live, log)
    if "count" not in hp:
        default_params = {"ridge": COUNT_GRID["ridge"][0], "half_life_days": COUNT_GRID["half_life_days"][0]}
        hp["count"] = {family: {scope: dict(default_params) for scope in COUNT_SCOPES} for family in families}
        log("WARNING: no count-model hyper-parameters; using the first setting of the protocol's grid")
    log(f"fit as of {fit_as_of.isoformat()} (training cutoff {cutoff.isoformat()}), season {season}, models {list(needed)}")

    t0 = time.perf_counter()
    from_database = matches is None
    built_at = history_built_at(live.history_db) if from_database else None  # read before the tables: a rebuild may follow
    if built_at is not None:
        try:
            history_age = float((fit_as_of - utc(built_at)) / pd.Timedelta(hours=1))
        except (ValueError, TypeError):
            history_age = None
        if history_age is not None and history_age > HISTORY_MAX_AGE_HOURS:
            log(f"WARNING: the history tables were built {history_age:.0f} h before this fit ({built_at}); the nightly rebuild may be failing")
    if matches is None:
        matches = load_live_matches(live.history_db, league_ids=live.league_ids, before=cutoff)
    else:
        stamp = pd.to_datetime(matches["kickoff_utc"], utc=True, format="mixed", errors="coerce")
        matches = matches[(stamp < cutoff).to_numpy()].reset_index(drop=True)  # nothing at or after the cut-off is seen
        if live.league_ids is not None and "league_id" in matches.columns:
            matches = matches[matches["league_id"].isin(live.league_ids)].reset_index(drop=True)
    if team_stats is None:
        team_stats = load_team_stats(live.history_db)
    team_stats = team_stats[team_stats["fixture_id"].isin(set(matches["fixture_id"]))].reset_index(drop=True)
    seconds["load"] = time.perf_counter() - t0
    log(f"loaded {len(matches)} matches, {len(team_stats)} team-statistic rows in {seconds['load']:.1f}s")

    config_wf = live.walk_forward()
    prep = prepare_matches(matches, MARKET_1X2, config_wf)
    train_all = prep.frame[prep.frame["kickoff"] < cutoff].reset_index(drop=True)
    ids_ok = train_all[["home_team_id", "away_team_id"]].notna().all(axis=1)
    if not ids_ok.all():
        log(f"dropping {int((~ids_ok).sum())} training matches without team ids")
        train_all = train_all[ids_ok].reset_index(drop=True)
    for column in ("home_team_id", "away_team_id"):
        train_all[column] = train_all[column].astype(np.int64)

    counts = train_all.groupby("league_code").size()
    eligible = [str(code) for code, n in counts.items() if n >= live.min_train_matches]
    if leagues is not None:
        wanted = set(leagues)
        eligible = [code for code in eligible if code in wanted]
    league_ids: dict[str, int] = {}
    if "league_id" in train_all.columns:
        league_ids = {str(k): int(v) for k, v in train_all.groupby("league_code")["league_id"].first().items()}
    log(f"{len(eligible)} leagues with at least {live.min_train_matches} training matches")

    directory = fit_directory(live, fit_as_of)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()  # a refit of the same day: the fit is incomplete until the new manifest is written

    # -- goal models, one job per league
    t0 = time.perf_counter()
    plan = _goal_plan([m for m in needed if m not in ("pi_gbm", "pi_ratings")], hp, weights)
    goal_models = list(plan["models"])
    league_status: dict[str, dict[str, Any]] = {}
    if goal_models:
        jobs = [{"league_code": code, "train": train_all[train_all["league_code"] == code], "plan": plan} for code in eligible]
        if n_jobs > 1 and len(jobs) > 1:
            from joblib import Parallel, delayed

            results = Parallel(n_jobs=min(n_jobs, len(jobs)), backend="loky")(delayed(_fit_goal_league)(job) for job in jobs)
        else:
            results = [_fit_goal_league(job) for job in jobs]
        for result in results:
            code = result["league_code"]
            league_status[code] = {"n_train": result["n_train"], "max_train_kickoff": result["max_train_kickoff"], "models": result["status"]}
            for model, fitted in result["fits"].items():
                _write_pickle(directory / f"{model}__{_safe(code)}.pkl", {"model": model, "league_code": code, "fit": fitted})
    else:
        for code in eligible:
            sub = train_all[train_all["league_code"] == code]
            league_status[code] = {"n_train": len(sub), "max_train_kickoff": sub["kickoff"].max().isoformat(), "models": {}}
    seconds["goal_models"] = time.perf_counter() - t0
    log(f"goal models ({', '.join(goal_models) or 'none'}) fitted for {len(eligible)} leagues in {seconds['goal_models']:.1f}s")

    # -- count models: one training frame per (family, league)
    t0 = time.perf_counter()
    count_leagues: dict[str, list[str]] = {}
    if families:
        covered, _ = apply_coverage_rule(matches, families=tuple(dict.fromkeys(families)))
        count_cfg = count_config(config_wf)
        for family in families:
            fam = FAMILIES[family]
            stat = next(key for key, value in COUNT_STATS.items() if value == family)
            market = count_market(fam, "total", fam.total_lines[0])
            count_prep = prepare_matches(covered, market, count_cfg)
            frame = count_prep.frame[count_prep.frame["kickoff"] < cutoff]
            columns = [c for c in _count_train_columns(family) if c in frame.columns]
            done: list[str] = []
            for code, group in frame.groupby("league_code"):
                code = str(code)
                if code not in league_status or len(group) < live.min_train_matches:
                    continue
                train = group[columns].reset_index(drop=True)
                _write_pickle(
                    directory / f"count_{stat}__{_safe(code)}.pkl",
                    {"model": f"count_{stat}", "league_code": code, "family": family, "train": train, "n_train": len(train)},
                )
                league_status[code].setdefault("counts", {})[stat] = len(train)
                done.append(code)
            count_leagues[stat] = done
            log(f"count {stat}: {len(done)} leagues")
    seconds["count_models"] = time.perf_counter() - t0

    # -- pooled pi-ratings models
    t0 = time.perf_counter()
    pi_info: dict[str, Any] = {}
    if "pi_gbm" in needed or "pi_ratings" in needed:
        pi_info = _fit_pi_models(train_all, team_stats, cutoff, needed, hp, weights, live, directory, n_jobs, log)
    seconds["pi_models"] = time.perf_counter() - t0

    manifest = {
        "version": LIVE_VERSION,
        "protocol_id": PROTOCOL_ID,
        "fit_as_of": fit_as_of.isoformat(),
        "train_cutoff": cutoff.isoformat(),
        "latency_hours": live.latency_hours,
        "season": season,
        "history_built_at": built_at,
        "n_train_matches": int(len(train_all)),
        "max_train_kickoff": train_all["kickoff"].max().isoformat() if len(train_all) else None,
        "weights": {market: dict(values) for market, values in weights.items()},
        "weights_meta": weights_meta,
        "hyperparameters": hp,
        "goal_plan": {**plan, "models": list(plan["models"])},
        "models_needed": list(needed),
        "count_stats": list(count_leagues),
        "league_ids": league_ids,
        "modelled_league_ids": None if live.league_ids is None else sorted(live.league_ids),
        "leagues": league_status,
        "count_leagues": count_leagues,
        "pi": pi_info,
        "seconds": {**{k: round(v, 1) for k, v in seconds.items()}, "total": round(time.perf_counter() - started, 1)},
    }
    _write_json(manifest_path, manifest)
    _prune_fits(live, directory, log)
    log(f"fit written to {directory} in {manifest['seconds']['total']}s")
    return manifest


def _fit_pi_models(
    train_all: pd.DataFrame,
    team_stats: pd.DataFrame,
    cutoff: pd.Timestamp,
    needed: Sequence[str],
    hp: Mapping[str, Any],
    weights: Mapping[str, Mapping[str, float]],
    live: LiveConfig,
    directory: Path,
    n_jobs: int,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Pooled pi-ratings booster and logit per market, trained on the matches before the 28-day bucket."""
    latency_ns = int(live.latency_hours * _NS_PER_HOUR)
    inputs = train_all[list(_STORE_MATCH_COLUMNS[:3]) + ["kickoff", "home_team_id", "away_team_id", "home_goals", "away_goals", "status_short"]].copy()
    inputs["kickoff_utc"] = inputs["kickoff"].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    inputs = inputs.drop(columns="kickoff")
    stats = team_stats[team_stats["fixture_id"].isin(set(inputs["fixture_id"]))].reset_index(drop=True)
    _write_pickle(directory / "pi_inputs.pkl", {"matches": inputs, "team_stats": stats})

    # The boosters are trained on the 47 protocol leagues only (as validated, and the protocol's cached boosters
    # apply); the store that gives the forecast features holds every modelled league.  A team's features come
    # from its own league and its own matches, so for the protocol leagues both stores agree.
    if "league_id" in train_all.columns:
        protocol_rows = train_all["league_id"].isin(PROTOCOL_LEAGUE_IDS).to_numpy()
    else:
        protocol_rows = np.ones(len(train_all), dtype=bool)
    engine_inputs = inputs[protocol_rows].reset_index(drop=True)
    engine_stats = stats[stats["fixture_id"].isin(set(engine_inputs["fixture_id"]))].reset_index(drop=True)
    engine = PiGbmEngine(
        engine_inputs, engine_stats, latency_hours=live.latency_hours, settings=live.gbm_settings, cache_dir=live.boosters_dir, kind="gbm"
    )
    cutoff_ns = _ns(cutoff)
    cut = engine.bank.cut_for(cutoff_ns)
    info: dict[str, Any] = {
        "cut_ns": int(cut),
        "cut_day": int(cut // _NS_PER_DAY),
        "usable": bool(engine.bank.usable(cut)),
        "n_booster_rows": int(engine.bank.n_rows_before(cut)),
        "boosters": {},
        "logit": [],
    }
    if not engine.bank.usable(cut):
        log(f"pi models: fewer than {live.gbm_settings.min_train_rows} training rows before the booster cut; skipped")
        return info
    if "pi_gbm" in needed:
        params = _pi_params(hp, weights)
        wanted = {(market, params_key(p)): (MARKET_BY_NAME[market], p) for market, p in params.items()}
        _prefetch_boosters(engine, live, wanted, [cut], n_jobs, log)
        for market, p in params.items():
            path = engine.bank.path(MARKET_BY_NAME[market], p, cut)
            info["boosters"][market] = {"params": p, "path": None if path is None else path.name, "exists": bool(path is not None and path.exists())}
    if "pi_ratings" in needed:
        bank = LogitBank(engine.matrix, live.gbm_settings, latency_ns=latency_ns, cache_dir=None)
        models = {}
        for market_name in GOAL_MARKET_NAMES:
            if weights.get(market_name, {}).get("pi_ratings", 0.0) > 0.0:
                models[market_name] = bank.model(MARKET_BY_NAME[market_name], {}, cut)
        _write_pickle(directory / "pi_ratings__pooled.pkl", {"model": "pi_ratings", "cut_ns": int(cut), "logit": models})
        info["logit"] = sorted(models)
    return info


# ---------------------------------------------------------------------------
# The cached fit and the forecast
# ---------------------------------------------------------------------------


def latest_fit(config: LiveConfig, as_of: Any) -> tuple[Path, dict[str, Any]] | None:
    """The newest complete fit made at or before ``as_of``: ``(directory, manifest)``; None if there is none."""
    best: tuple[pd.Timestamp, Path, dict[str, Any]] | None = None
    for manifest_path in config.fits_dir.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("version") != LIVE_VERSION:
            continue
        stamp = utc(manifest["fit_as_of"])
        if stamp <= utc(as_of) and (best is None or stamp > best[0]):
            best = (stamp, manifest_path.parent, manifest)
    return None if best is None else (best[1], best[2])


REPORT_COLUMNS = ("fixture_id", "league_id", "league_code", "validated", "status", "models", "n_rows")


def _normalise_fixtures(fixtures: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """The columns the forecast needs, typed; one row per fixture (the last listing wins)."""
    needed = ("fixture_id", "league_id", "kickoff_utc", "home_team_id", "away_team_id")
    absent = [c for c in needed if c not in fixtures.columns]
    if absent:
        raise ValueError(f"fixtures lack column(s): {', '.join(absent)}")
    frame = fixtures.drop_duplicates("fixture_id", keep="last").reset_index(drop=True)
    out = pd.DataFrame(
        {
            "fixture_id": pd.to_numeric(frame["fixture_id"], errors="coerce"),
            "league_id": pd.to_numeric(frame["league_id"], errors="coerce"),
            "home_team_id": pd.to_numeric(frame["home_team_id"], errors="coerce"),
            "away_team_id": pd.to_numeric(frame["away_team_id"], errors="coerce"),
            "kickoff": pd.to_datetime(frame["kickoff_utc"], utc=True, format="mixed", errors="coerce").dt.as_unit("ns"),
            "status_short": frame["status_short"].astype(object) if "status_short" in frame.columns else np.full(len(frame), "NS", dtype=object),
            "season": pd.to_numeric(frame["season"], errors="coerce") if "season" in frame.columns else np.nan,
            "referee": frame["referee"].astype(object) if "referee" in frame.columns else np.full(len(frame), None, dtype=object),
        }
    )
    out["season"] = out["season"].fillna(season_label(as_of))
    return out


class LiveBundle:
    """One cached fit (directory + manifest): loads per-league state on demand and forecasts fixtures."""

    def __init__(self, directory: Path, manifest: Mapping[str, Any], config: LiveConfig) -> None:
        self.directory = Path(directory)
        self.manifest = dict(manifest)
        self.config = config
        self.fit_as_of = utc(manifest["fit_as_of"])
        self.cutoff = utc(manifest["train_cutoff"])
        self._league_cache: dict[tuple[str, str], Any] = {}
        self._pi: dict[str, Any] | None = None
        self._engines: dict[str, CountEngine] = {}
        fitted = set(manifest.get("leagues", {}))
        self.code_by_league_id = {int(v): k for k, v in manifest.get("league_ids", {}).items() if k in fitted}

    # -- lookups

    def _load(self, model: str, league_code: str) -> Any | None:
        key = (model, league_code)
        if key not in self._league_cache:
            path = self.directory / f"{model}__{_safe(league_code)}.pkl"
            self._league_cache[key] = _read_pickle(path) if path.exists() else None
        return self._league_cache[key]

    def _hp(self, model: str, market: str) -> Mapping[str, Any]:
        return self.manifest["hyperparameters"][model][market]

    # -- goal components of one league

    def goal_components(self, code: str, frame: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
        """``{model: {market: (n, K)}}`` for the fixtures of one league (NaN rows where a model has no forecast)."""
        weights = self.manifest["weights"]
        n = len(frame)
        out: dict[str, dict[str, np.ndarray]] = {}

        def nan(market: str) -> np.ndarray:
            return np.full((n, len(MARKET_BY_NAME[market].classes)), np.nan)

        for kind, model_class in (("dixon_coles", DixonColesGoalModel), ("nb2", NegativeBinomialGoalModel)):
            entry = self._load(kind, code)
            if entry is None or kind not in self.manifest["goal_plan"]["models"]:
                continue
            per_half_life: dict[float, np.ndarray | None] = {}
            probabilities: dict[str, np.ndarray] = {}
            for market in GOAL_MARKET_NAMES:
                if weights.get(market, {}).get(kind, 0.0) <= 0.0:
                    continue
                half_life = float(self._hp(kind, market)["half_life_days"])
                fitted = entry["fit"][half_life]
                if not fitted.converged:
                    probabilities[market] = nan(market)
                    continue
                if half_life not in per_half_life:
                    model = model_class(MARKET_1X2, half_life, cache=None)
                    model.fitted = fitted
                    per_half_life[half_life] = model.score_matrices(frame)
                probabilities[market] = matrix_market_probabilities(per_half_life[half_life], MARKET_BY_NAME[market])
            out[kind] = probabilities
        entry = self._load("gas_poisson", code)
        if entry is not None:
            forecaster = entry["fit"]
            home, away = forecaster.expected_goals(frame)
            matrix = poisson_score_matrix(home, away, max_goals=forecaster.max_goals)
            out["gas_poisson"] = {
                market: goal_market_probabilities(matrix, MARKET_BY_NAME[market])
                for market in GOAL_MARKET_NAMES
                if weights.get(market, {}).get("gas_poisson", 0.0) > 0.0
            }
        entry = self._load("elo_davidson", code)
        if entry is not None:
            out["elo_davidson"] = {MARKET_1X2.name: entry["fit"].predict(frame)}
        entry = self._load(BASELINE_NAME, code)
        if entry is not None:
            out[BASELINE_NAME] = {market: np.tile(p, (n, 1)) for market, p in entry["fit"].items()}
        return out

    # -- count forecasts of one league

    def count_forecast(self, stat: str, code: str, frame: pd.DataFrame):
        entry = self._load(f"count_{stat}", code)
        if entry is None:
            return None
        family = COUNT_STATS[stat]
        engine = self._engines.setdefault(family, CountEngine(family, "nb2"))
        params = self.manifest["hyperparameters"]["count"][family]
        return engine, entry, params

    # -- pooled pi-ratings models

    def _pi_resources(self) -> dict[str, Any] | None:
        if self._pi is not None:
            return self._pi
        info = self.manifest.get("pi") or {}
        path = self.directory / "pi_inputs.pkl"
        if not info.get("usable") or not path.exists():
            self._pi = {}
            return None
        from catboost import CatBoostClassifier

        from model_buk.models.match_features import AsOfStore

        inputs = _read_pickle(path)
        store = AsOfStore(inputs["matches"], inputs["team_stats"])
        boosters = {}
        for market, entry in info.get("boosters", {}).items():
            booster_path = self.config.boosters_dir / entry["path"] if entry.get("path") else None
            if booster_path is not None and booster_path.exists():
                model = CatBoostClassifier()
                model.load_model(str(booster_path))
                boosters[market] = model
        logit = {}
        logit_path = self.directory / "pi_ratings__pooled.pkl"
        if logit_path.exists():
            logit = _read_pickle(logit_path)["logit"]
        self._pi = {"store": store, "boosters": boosters, "logit": logit}
        return self._pi

    def pi_components(self, frame: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
        """Pooled pi-ratings booster and logit forecasts for fixtures of fitted leagues, aligned with ``frame``."""
        resources = self._pi_resources()
        if not resources:
            return {}
        store = resources["store"]
        x = store.features(
            league_code=frame["league_code"].to_numpy(dtype=object),
            season=frame["season"].to_numpy(dtype=np.int64),
            home_team_id=frame["home_team_id"].to_numpy(dtype=np.int64),
            away_team_id=frame["away_team_id"].to_numpy(dtype=np.int64),
            kickoff_ns=frame["kickoff"].dt.as_unit("ns").dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64"),
            cutoff_ns=_ns(self.cutoff),
        ).astype(np.float32)
        out: dict[str, dict[str, np.ndarray]] = {}
        gbm: dict[str, np.ndarray] = {}
        for market, booster in resources["boosters"].items():
            proba = np.clip(np.asarray(booster.predict_proba(x), dtype=float), 1e-6, None)
            gbm[market] = proba / proba.sum(axis=1, keepdims=True)
        if gbm:
            out["pi_gbm"] = gbm
        if resources["logit"]:
            out["pi_ratings"] = {market: model.predict_proba(x) for market, model in resources["logit"].items() if model is not None}
        return out

    # -- the forecast

    def forecast(
        self,
        fixtures: pd.DataFrame,
        as_of: Any,
        *,
        include_components: bool = False,
        include_counts: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Model frame and per-fixture report for ``fixtures`` (see :func:`forecast_fixtures`)."""
        as_of = utc(as_of)
        fx = _normalise_fixtures(fixtures, as_of)
        latency = pd.Timedelta(hours=self.manifest["latency_hours"])
        report = pd.DataFrame(
            {
                "fixture_id": fx["fixture_id"].astype("Int64"),
                "league_id": fx["league_id"].astype("Int64"),
                "league_code": [self.code_by_league_id.get(int(i)) if pd.notna(i) else None for i in fx["league_id"]],
                "validated": [bool(pd.notna(i) and int(i) in PROTOCOL_LEAGUE_IDS) for i in fx["league_id"]],
                "status": "ok",
                "models": "",
                "n_rows": 0,
            }
        )
        status = np.full(len(fx), "ok", dtype=object)
        status[fx["kickoff"].isna().to_numpy()] = "no_kickoff"
        status[fx[["fixture_id", "home_team_id", "away_team_id"]].isna().any(axis=1).to_numpy()] = "missing_ids"
        status[~fx["status_short"].fillna("NS").isin(UPCOMING_STATUSES).to_numpy() & (status == "ok")] = "not_upcoming"
        status[(fx["kickoff"] <= as_of).fillna(True).to_numpy() & (status == "ok")] = "not_upcoming"
        no_league = report["league_code"].isna().to_numpy() & (status == "ok")
        status[no_league] = "no_league_history"
        # A fit is valid for a fixture only if it trained on matches that kicked off before kickoff - latency.
        too_new = ((fx["kickoff"] - latency) < self.cutoff).fillna(False).to_numpy() & (status == "ok")
        status[too_new] = "fit_too_new"
        report["status"] = status

        live = report["status"] == "ok"
        frame = fx[live.to_numpy()].copy()
        frame["league_code"] = report.loc[live, "league_code"].to_numpy()
        frame["fixture_id"] = frame["fixture_id"].astype(np.int64)
        for column in ("league_id", "home_team_id", "away_team_id", "season"):
            frame[column] = frame[column].astype(np.int64)
        frame = frame.reset_index(drop=True)

        pieces: list[pd.DataFrame] = []
        per_fixture_models: dict[int, list[str]] = {}
        pi_all = self.pi_components(frame) if len(frame) else {}
        for code, group in frame.groupby("league_code", sort=False):
            code = str(code)
            index = group.index.to_numpy()
            league_frame = group.reset_index(drop=True)
            ids = league_frame["fixture_id"].to_numpy(dtype=np.int64)
            components = self.goal_components(code, league_frame)
            for model, per_market in pi_all.items():
                components[model] = {market: probs[index] for market, probs in per_market.items()}
            for market in GOAL_MARKET_NAMES:
                weights = self.manifest["weights"].get(market)
                if not weights:
                    continue
                by_model = {model: comp[market] for model, comp in components.items() if market in comp}
                if not by_model:
                    continue
                pooled = pool_probabilities(by_model, weights)
                pieces.extend(goal_market_rows(ids, market, pooled, MODEL_NAME_POOL))
                ok = ~np.isnan(pooled).any(axis=1)
                for fid in ids[ok]:
                    per_fixture_models.setdefault(int(fid), [])
                    if MODEL_NAME_POOL not in per_fixture_models[int(fid)]:
                        per_fixture_models[int(fid)].append(MODEL_NAME_POOL)
                if include_components:
                    for model, probs in by_model.items():
                        pieces.extend(goal_market_rows(ids, market, probs, model))
            if include_counts:
                pieces.extend(self._count_rows(code, league_frame, per_fixture_models))

        if pieces:
            model_frame = pd.concat(pieces, ignore_index=True)[list(MODEL_FRAME_COLUMNS)]
            model_frame = model_frame[(model_frame["p_model"] >= 0.0) & (model_frame["p_model"] <= 1.0)].reset_index(drop=True)
        else:
            model_frame = pd.DataFrame({c: pd.Series(dtype=object) for c in MODEL_FRAME_COLUMNS}).astype(
                {"fixture_id": "int64", "line": "float64", "p_model": "float64"}
            )
        counts = model_frame.groupby("fixture_id").size() if len(model_frame) else pd.Series(dtype=int)
        report["n_rows"] = report["fixture_id"].map(counts).fillna(0).astype(int)
        report["models"] = report["fixture_id"].map(lambda f: ",".join(per_fixture_models.get(int(f), [])) if pd.notna(f) else "")
        has_rows = report["n_rows"] > 0
        report.loc[(report["status"] == "ok") & ~has_rows, "status"] = "no_forecast"
        model_frame.attrs["report"] = report[list(REPORT_COLUMNS)]
        # Provenance for the paper log: which cached fit produced these forecasts (the fit directories are pruned).
        model_frame.attrs["fit_as_of"] = self.fit_as_of.isoformat()
        model_frame.attrs["train_cutoff"] = self.cutoff.isoformat()
        model_frame.attrs["history_built_at"] = self.manifest.get("history_built_at")
        return model_frame, report[list(REPORT_COLUMNS)]

    def _count_rows(self, code: str, league_frame: pd.DataFrame, per_fixture_models: dict[int, list[str]]) -> list[pd.DataFrame]:
        rows: list[pd.DataFrame] = []
        ids = league_frame["fixture_id"].to_numpy(dtype=np.int64)
        for stat in self.manifest.get("count_stats", []):
            resolved = self.count_forecast(stat, code, league_frame)
            if resolved is None:
                continue
            engine, entry, params = resolved
            family = COUNT_STATS[stat]
            query = league_frame[["fixture_id", "league_code", "home_team_id", "away_team_id", "kickoff", "referee"]].copy()
            forecast = engine.forecast(entry["train"], query, ridge=float(params["total"]["ridge"]), half_life_days=params["total"]["half_life_days"])
            per_scope = {"total": forecast}
            for scope in ("home", "away"):
                p = params[scope]
                if p == params["total"]:
                    per_scope[scope] = forecast
                else:
                    per_scope[scope] = engine.forecast(entry["train"], query, ridge=float(p["ridge"]), half_life_days=p["half_life_days"])
            for scope in COUNT_SCOPES:
                fc = per_scope[scope]
                if scope == "total":
                    mean, alpha = fc.mu_home + fc.mu_away, fc.alpha_total
                else:
                    mean, alpha = (fc.mu_home if scope == "home" else fc.mu_away), fc.alpha
                rows.extend(count_market_rows(ids, stat, scope, mean, alpha, fc.kind))
            ok = ~np.isnan(forecast.mu_home + forecast.mu_away)
            for fid in ids[ok]:
                names = per_fixture_models.setdefault(int(fid), [])
                if MODEL_NAME_COUNT not in names:
                    names.append(MODEL_NAME_COUNT)
        return rows


_BUNDLES: dict[tuple, "LiveBundle"] = {}


def forecast_with_report(
    fixtures: pd.DataFrame,
    as_of: Any = None,
    *,
    config: LiveConfig | None = None,
    include_components: bool = False,
    include_counts: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model frame and per-fixture report for upcoming ``fixtures`` as known at ``as_of`` (default: now).

    ``fixtures`` needs ``fixture_id, league_id, kickoff_utc, home_team_id, away_team_id`` (``season``,
    ``status_short`` and ``referee`` are used when present).  Only fixtures with status ``NS`` / ``TBD`` and a
    kickoff after ``as_of`` are forecast.  Uses the newest complete fit made at or before ``as_of`` and never
    reads the history database; with no fit, or one older than ``max_fit_age_hours``, every fixture gets the
    status ``no_fit`` / ``fit_stale`` and there are no rows.  The report has one row per fixture: ``status``
    (``ok``, ``no_forecast``, ``not_upcoming``, ``no_league_history``, ``missing_ids``, ``no_kickoff``,
    ``fit_too_new``, ``no_fit``, ``fit_stale``), ``validated`` (the fixture's league is one of the 47 protocol
    leagues), the models that produced rows and the number of rows.
    """
    live = config or LiveConfig()
    now = utc(as_of if as_of is not None else pd.Timestamp.now(tz="UTC"))
    empty = pd.DataFrame({c: pd.Series(dtype=object) for c in MODEL_FRAME_COLUMNS}).astype(
        {"fixture_id": "int64", "line": "float64", "p_model": "float64"}
    )

    def refuse(reason: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        fx = fixtures.drop_duplicates("fixture_id", keep="last") if "fixture_id" in fixtures.columns else fixtures
        report = pd.DataFrame(
            {
                "fixture_id": fx["fixture_id"].to_numpy() if "fixture_id" in fx.columns else [],
                "league_id": fx["league_id"].to_numpy() if "league_id" in fx.columns else [],
                "league_code": None,
                "validated": False,
                "status": reason,
                "models": "",
                "n_rows": 0,
            }
        )[list(REPORT_COLUMNS)]
        empty.attrs["report"] = report
        return empty, report

    found = latest_fit(live, now)
    if found is None:
        return refuse("no_fit")
    directory, manifest = found
    age_hours = (now - utc(manifest["fit_as_of"])) / pd.Timedelta(hours=1)
    if age_hours > live.max_fit_age_hours:
        return refuse("fit_stale")
    stamp = (directory / "manifest.json").stat().st_mtime_ns
    key = (str(directory), manifest["fit_as_of"], stamp, str(live.boosters_dir))
    bundle = _BUNDLES.get(key)
    if bundle is None:
        _BUNDLES.clear()  # one fit at a time: an hourly run in a long-lived process loads the store and boosters once
        bundle = _BUNDLES[key] = LiveBundle(directory, manifest, live)
    return bundle.forecast(fixtures, now, include_components=include_components, include_counts=include_counts)


def forecast_fixtures(
    fixtures: pd.DataFrame,
    as_of: Any = None,
    *,
    config: LiveConfig | None = None,
    include_components: bool = False,
    include_counts: bool = True,
) -> pd.DataFrame:
    """The model frame of upcoming fixtures: ``fixture_id, market, period, line, selection, p_model, model_name``.

    See :func:`forecast_with_report`; the per-fixture report is ``frame.attrs["report"]`` and the cached fit that
    produced the rows is named by ``frame.attrs["fit_as_of"]`` / ``["train_cutoff"]`` / ``["history_built_at"]``
    (absent when no fit was used).  ``include_components``
    adds the rows of every pool component (``dixon_coles``, ``pi_gbm``, ...) next to ``pool_models`` for the
    later evaluation of the models.
    """
    frame, _ = forecast_with_report(
        fixtures, as_of, config=config, include_components=include_components, include_counts=include_counts
    )
    return frame


__all__ = [
    "COUNT_LINE_GRID",
    "COUNT_MARKET_NAMES",
    "DEFAULT_CACHE_DIR",
    "GOAL_COMPONENTS",
    "LIVE_VERSION",
    "LiveBundle",
    "LiveConfig",
    "MODEL_FRAME_COLUMNS",
    "MODEL_NAME_COUNT",
    "MODEL_NAME_POOL",
    "compute_pool_weights",
    "count_lines",
    "count_over_probability",
    "derived_1x2_markets",
    "fit_live_models",
    "forecast_fixtures",
    "forecast_with_report",
    "goal_market_rows",
    "latest_fit",
    "load_hyperparameters",
    "load_live_matches",
    "load_pool_weights",
    "load_prematch_fixtures",
    "load_team_stats",
    "models_needed",
    "pool_probabilities",
    "season_label",
    "select_hyperparameters",
    "stored_hyperparameters",
    "train_cutoff",
    "utc",
]
