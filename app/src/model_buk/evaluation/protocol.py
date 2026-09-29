"""The fixed protocol every goal/1X2 model is compared under (plan, days 3-4).

One configuration, one set of markets and one storage format, so results of
models built in separate steps are directly comparable:

- :data:`COMPARISON_CONFIG` -- outer test seasons 2022-2025, one model per
  league refit before every matchday, hyper-parameters chosen on the single
  season before each test season (pooled across leagues).
- :data:`GOAL_MARKETS` -- 1X2, total goals 1.5 / 2.5 / 3.5 and BTTS.
- :func:`save_result` / :func:`load_predictions` -- out-of-sample forecasts in
  ``Model-Buk-Data/predictions/<model>.sqlite``, one table per market, keyed
  by ``fixture_id`` so they join to football-data odds via
  ``joined/fd_af_matches.sqlite``.

Changing anything here invalidates every stored comparison: bump
:data:`PROTOCOL_ID` and rerun all models.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

from model_buk.evaluation.framework import (
    MARKET_1X2,
    Market,
    WalkForwardConfig,
    WalkForwardResult,
    over_under,
)

PROTOCOL_ID = "goals-protocol-v1"
DATA_ROOT = Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
HISTORY_DB = DATA_ROOT / "api-football" / "tables" / "history.sqlite"
PREDICTIONS_DIR = DATA_ROOT / "predictions"

COMPARISON_CONFIG = WalkForwardConfig(
    test_seasons=range(2022, 2026),
    scope="per_league",
    inner_seasons=1,
    select_per="pooled",
)

MATCH_COLUMNS = (
    "fixture_id",
    "league_id",
    "league_code",
    "season",
    "round",
    "kickoff_utc",
    "status_short",
    "venue_id",
    "referee",
    "home_team_id",
    "home_team",
    "away_team_id",
    "away_team",
    "home_goals",
    "away_goals",
    "ht_home_goals",
    "ht_away_goals",
    "et_home_goals",
    "et_away_goals",
    "pen_home",
    "pen_away",
)


def _outcome_btts(frame: pd.DataFrame) -> np.ndarray:
    home = pd.to_numeric(frame["home_goals"], errors="coerce").to_numpy(dtype=float)
    away = pd.to_numeric(frame["away_goals"], errors="coerce").to_numpy(dtype=float)
    out = ((home > 0) & (away > 0)).astype(float)
    out[np.isnan(home) | np.isnan(away)] = np.nan
    return out


MARKET_BTTS = Market("btts", ("no", "yes"), _outcome_btts)

GOAL_MARKETS: tuple[Market, ...] = (
    MARKET_1X2,
    over_under(1.5),
    over_under(2.5),
    over_under(3.5),
    MARKET_BTTS,
)


def load_history_matches(db_path: str | Path = HISTORY_DB) -> pd.DataFrame:
    """Read the API-Football ``matches`` table read-only (all seasons, all statuses)."""

    path = Path(db_path)
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        return pd.read_sql_query(f"SELECT {', '.join(MATCH_COLUMNS)} FROM matches", connection)


def _table_name(market: Market) -> str:
    return "forecasts_" + market.name.replace(".", "_")


def save_result(result: WalkForwardResult, root: str | Path = PREDICTIONS_DIR) -> Path:
    """Write one model's out-of-sample forecasts for one market (replaces that market's table).

    The table keeps the forecast rows exactly as the framework produced them
    (NaN rows stay NaN) plus the protocol id, so a stale table is detectable.
    """

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.model}.sqlite"
    keep = [
        column
        for column in ("fixture_id", "league_code", "season", "matchday", "kickoff", "y", "params")
        if column in result.predictions.columns
    ]
    frame = result.predictions[keep + result.probability_columns].copy()
    if "kickoff" in frame.columns:
        frame["kickoff"] = pd.to_datetime(frame["kickoff"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if "params" in frame.columns:
        frame["params"] = frame["params"].astype(str)
    frame["protocol_id"] = PROTOCOL_ID
    with closing(sqlite3.connect(path)) as connection:
        frame.to_sql(_table_name(result.market), connection, if_exists="replace", index=False)
        result.selections.astype(str).to_sql(
            _table_name(result.market) + "_selections", connection, if_exists="replace", index=False
        )
        connection.commit()
    return path


def load_predictions(model: str, market: Market, root: str | Path = PREDICTIONS_DIR) -> pd.DataFrame:
    """Read stored forecasts; raises if they were produced under another protocol."""

    path = Path(root) / f"{model}.sqlite"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        frame = pd.read_sql_query(f"SELECT * FROM {_table_name(market)}", connection)
    stale = set(frame["protocol_id"].unique()) - {PROTOCOL_ID}
    if stale:
        raise ValueError(f"{model}/{market.name} was produced under protocol {sorted(stale)}, not {PROTOCOL_ID}")
    return frame


__all__ = [
    "PROTOCOL_ID",
    "DATA_ROOT",
    "HISTORY_DB",
    "PREDICTIONS_DIR",
    "COMPARISON_CONFIG",
    "MATCH_COLUMNS",
    "MARKET_BTTS",
    "GOAL_MARKETS",
    "load_history_matches",
    "save_result",
    "load_predictions",
]
