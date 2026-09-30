"""Append-only paper register of the scanner (SQLite).

The register records what the scanner saw and proposed so that it can be evaluated later without hindsight:

``configs``      one row per config (``config_id`` = hash of every parameter; a changed parameter is a new id)
``scans``        one row per scan: time, config, counts
``legs``         the near-miss universe: every leg whose price is above the fair price (raw edge above zero) at any
                 bookmaker, plus every value leg, with all computed columns, the fair source, the model columns,
                 the bookmaker, the odd and the time the price was seen
``coupons``, ``coupon_legs``  every coupon that passed (capped only when a cap is configured) and its legs
``settlements``  leg outcomes (``win | loss | push | half_win | half_loss | void``), source and time
``clv``          closing line value of the price taken, per regime
``bet_type_checks``  the daily measurement of how far every ``(bookmaker, bet_id)`` sits from Pinnacle and which
                 pairs are excluded (:mod:`model_buk.scanner.bet_types`)

Every leg and coupon carries the market tier of its fixture (``tier``: ``liquid`` | ``thin``, legs also
``tier_reason``), legs the ``unsynced`` flag and ``board_lag_min``, coupons the ``regime`` (the strictest tax
regime at which the coupon passes).  Columns added after a register was created are added to it on open
(``ALTER TABLE ... ADD COLUMN``: additive, nothing is rewritten), so older rows keep NULL there.

Rules
-----
* **Append only.**  Every table has triggers that abort ``UPDATE`` and ``DELETE``.  A correction is a new row (the
  latest settlement of a leg wins); a scan id that already exists is ignored (idempotent).
* **Change-only legs and coupons.**  The same offer is seen every hour.  A leg row is written when the leg is new,
  its odd or probability moved, its class changed or the last row is older than the heartbeat; the scan row keeps
  the full counts (``n_legs_seen`` versus ``n_legs_logged``), so nothing is hidden.  "New" and "last row" are
  taken within the scan's own ``config_id``, so a new config writes its whole universe once and its paper bets
  start from what it saw first.
* **Paper bets.**  The paper bet of a leg is its *first* value observation at a bookmaker (the price that could
  have been taken), of a coupon its first appearance among the proposed coupons.  Later observations do not count
  again.  Per tax regime (:meth:`ScannerRegister.paper_results`) the paper bet is the first observation that is
  value at that regime (a leg: ``is_value`` and ``value_t###``; a coupon: listed and ``pass_t###``), per tier the
  bets of fixtures of that tier.  Results and CLV are computed on those.  What counts as value depends on the config, so the paper
  results (:meth:`ScannerRegister.paper_summary`, and the page) are taken per ``config_id``: a changed config
  starts a new experiment, its bets are not mixed with those of an older definition of value.
* **Timestamps** are UTC ISO strings (``YYYY-MM-DDTHH:MM:SSZ``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from model_buk.scanner import settlement as st
from model_buk.scanner.engine import KEY_COLUMNS, SCANNER_DIR, ScannerConfig, normalise_keys, to_utc
from model_buk.scanner.settlement import tau_label

if TYPE_CHECKING:  # pragma: no cover
    from model_buk.scanner.coupons import CouponSet

DEFAULT_PATH = SCANNER_DIR / "scanner.sqlite"
ISO = "%Y-%m-%dT%H:%M:%SZ"

_LEG_COLUMNS: dict[str, str] = {
    "fixture_id": "INTEGER",
    "league_id": "INTEGER",
    "kickoff_utc": "TEXT",
    "bet_id": "INTEGER",
    "market": "TEXT",
    "period": "TEXT",
    "line": "REAL",
    "selection": "TEXT",
    "market_family": "TEXT",
    "odd": "REAL",
    "fetched_at": "TEXT",
    "quote_age_min": "REAL",
    "p_fair": "REAL",
    "p_se": "REAL",
    "fair_source": "TEXT",
    "devig_method": "TEXT",
    "n_books": "REAL",
    "book_margin": "REAL",
    "p_model": "REAL",
    "model_name": "TEXT",
    "model_agrees": "REAL",
    "p_used": "REAL",
    "p_se_used": "REAL",
    "p_lower": "REAL",
    "fair_odd": "REAL",
    "edge_raw": "REAL",
    "edge_raw_lower": "REAL",
    "refund_share": "REAL",
    "book_shift": "REAL",
    "board_lag_min": "REAL",
    "tier": "TEXT",
    "tier_reason": "TEXT",
    "suspect": "INTEGER",
    "unsynced": "INTEGER",
    "vetoed": "INTEGER",
    "is_value": "INTEGER",
    "is_model_only": "INTEGER",
    "is_model_only_value": "INTEGER",
    "leg_class": "TEXT",
}
_COUPON_COLUMNS: dict[str, str] = {
    "bookmaker": "TEXT",
    "n_legs": "INTEGER",
    "odds_total": "REAL",
    "p_hit": "REAL",
    "p_hit_lower": "REAL",
    "kickoff_first": "TEXT",
    "kickoff_last": "TEXT",
    "leg_keys": "TEXT",
    "rank": "INTEGER",
    "is_top": "INTEGER",
    "is_logged": "INTEGER",
    "tier": "TEXT",
    "regime": "TEXT",
}
_COUPON_LEG_COLUMNS: dict[str, str] = {
    "leg_no": "INTEGER",
    "leg_key": "TEXT",
    "bookmaker": "TEXT",
    "fixture_id": "INTEGER",
    "kickoff_utc": "TEXT",
    "market": "TEXT",
    "period": "TEXT",
    "line": "REAL",
    "selection": "TEXT",
    "odd": "REAL",
    "p_fair": "REAL",
    "p_lower": "REAL",
    "refund_share": "REAL",
    "fair_source": "TEXT",
    "tier": "TEXT",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
    config_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    first_used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    as_of TEXT NOT NULL,
    config_id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    n_quotes INTEGER,
    n_legs INTEGER,
    n_market_fair INTEGER,
    n_edge_positive INTEGER,
    n_value INTEGER,
    n_model_only INTEGER,
    n_picks INTEGER,
    n_fixtures INTEGER,
    n_legs_seen INTEGER,
    n_legs_logged INTEGER,
    n_coupons_evaluated INTEGER,
    n_coupons_passing INTEGER,
    n_coupons_top INTEGER,
    n_coupons_logged INTEGER,
    diagnostics_json TEXT
);
CREATE TABLE IF NOT EXISTS legs (
    scan_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    leg_key TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    config_id TEXT NOT NULL,
{leg_columns},
    PRIMARY KEY (scan_id, bookmaker, leg_key)
);
CREATE INDEX IF NOT EXISTS idx_legs_key ON legs (bookmaker, leg_key, as_of);
CREATE INDEX IF NOT EXISTS idx_legs_fixture ON legs (fixture_id);
CREATE TABLE IF NOT EXISTS coupons (
    scan_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    coupon_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
{coupon_columns},
    PRIMARY KEY (scan_id, coupon_id)
);
CREATE INDEX IF NOT EXISTS idx_coupons_id ON coupons (coupon_id, as_of);
CREATE TABLE IF NOT EXISTS coupon_legs (
    scan_id TEXT NOT NULL,
    coupon_id TEXT NOT NULL,
{coupon_leg_columns},
    PRIMARY KEY (scan_id, coupon_id, leg_no)
);
CREATE TABLE IF NOT EXISTS settlements (
    leg_key TEXT NOT NULL,
    fixture_id INTEGER,
    market TEXT,
    period TEXT,
    line REAL,
    selection TEXT,
    outcome TEXT NOT NULL,
    settled_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_settlements_unique ON settlements (leg_key, outcome, source);
CREATE INDEX IF NOT EXISTS idx_settlements_key ON settlements (leg_key);
CREATE TABLE IF NOT EXISTS clv (
    scan_id TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    leg_key TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    odd_taken REAL NOT NULL,
    closing_odd REAL,
    closing_p_fair REAL,
    closing_fair_source TEXT,
    clv_raw REAL,
    source TEXT,
    PRIMARY KEY (scan_id, bookmaker, leg_key, computed_at)
);
CREATE TABLE IF NOT EXISTS bet_type_checks (
    data_as_of TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    bet_id INTEGER NOT NULL,
    bet TEXT,
    market TEXT,
    period TEXT,
    n_legs INTEGER,
    n_fixtures INTEGER,
    median_dev REAL,
    mad_dev REAL,
    p90_abs_dev REAL,
    tail_share REAL,
    mad_ratio REAL,
    excluded INTEGER,
    reason TEXT,
    settings_json TEXT,
    PRIMARY KEY (data_as_of, bookmaker, bet_id)
);
"""

_TABLES = ("configs", "scans", "legs", "coupons", "coupon_legs", "settlements", "clv", "bet_type_checks")
OUTCOME_PL: dict[str, str] = {
    "win": "wygrany",
    "loss": "przegrany",
    "push": "zwrot",
    "half_win": "pół-wygrany",
    "half_loss": "pół-przegrany",
    "void": "unieważniony",
}
"""Polish names of the outcomes (a bet without an outcome is ``oczekuje``)."""


def _ddl(columns: dict[str, str]) -> str:
    return ",\n".join(f"    {name} {kind}" for name, kind in columns.items())


def _iso(value: object) -> str | None:
    if value is None or value is pd.NaT:
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.strftime(ISO)


def _sql_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _rows(frame: pd.DataFrame, columns: Sequence[str]) -> list[tuple]:
    """Python values of ``columns`` row by row; NaN / NaT / NA become NULL, times become ISO text."""
    prepared: list[list[Any]] = []
    for column in columns:
        series = frame[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            values = [_iso(v) for v in series.tolist()]
        else:
            values = [None if _is_missing(v) else (int(v) if isinstance(v, (bool, np.bool_)) else v) for v in series.tolist()]
        prepared.append(values)
    return list(zip(*prepared, strict=True)) if prepared else []


class ScannerRegister:
    """The append-only SQLite register (see the module docstring).

    Every method opens and closes its own connection, so the object can be shared and the file is never held
    open between calls.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(
                _SCHEMA.format(
                    leg_columns=_ddl(_LEG_COLUMNS),
                    coupon_columns=_ddl(_COUPON_COLUMNS),
                    coupon_leg_columns=_ddl(_COUPON_LEG_COLUMNS),
                )
            )
            migrated = {
                "legs": _LEG_COLUMNS,
                "coupons": _COUPON_COLUMNS,
                "coupon_legs": _COUPON_LEG_COLUMNS,
                "bet_type_checks": {"period": "TEXT", "mad_ratio": "REAL"},
            }
            for table, columns in migrated.items():
                existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns.items():
                    if name not in existing:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            for table in _TABLES:
                for action in ("UPDATE", "DELETE"):
                    con.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} "
                        "BEGIN SELECT RAISE(ABORT, 'the scanner register is append-only'); END"
                    )
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def read(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        """Run a read-only query and return a frame (timestamps stay ISO text)."""
        with closing(self._connect()) as con:
            return pd.read_sql_query(sql, con, params=list(params))

    def table(self, name: str) -> pd.DataFrame:
        if name not in _TABLES:
            raise ValueError(f"unknown table {name!r}")
        return self.read(f"SELECT * FROM {name}")

    def has_scan(self, scan_id: str) -> bool:
        return not self.read("SELECT 1 FROM scans WHERE scan_id = ?", [scan_id]).empty

    # ------------------------------------------------------------------ #
    # Writing a scan
    # ------------------------------------------------------------------ #

    def record_scan(
        self,
        *,
        scan_id: str,
        started_at: object,
        as_of: object,
        config: ScannerConfig,
        legs: pd.DataFrame,
        coupons: CouponSet | None = None,
        diagnostics: dict[str, int] | None = None,
        finished_at: object = None,
    ) -> bool:
        """Append one scan; returns ``False`` (and writes nothing) when ``scan_id`` was recorded before.

        ``legs`` is the frame of :func:`model_buk.scanner.engine.evaluate_legs`; only its near-miss universe and
        value legs are written, and only where something changed since the last row of the same leg.  The whole
        scan is one transaction: it is complete or absent.
        """
        as_of_text = _iso(as_of)
        if as_of_text is None:
            raise ValueError("as_of is required")
        diagnostics = diagnostics or {}
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                if con.execute("SELECT 1 FROM scans WHERE scan_id = ?", (scan_id,)).fetchone():
                    con.execute("ROLLBACK")
                    return False
                con.execute(
                    "INSERT OR IGNORE INTO configs (config_id, config_json, first_used_at) VALUES (?, ?, ?)",
                    (config.config_id, config.to_json(), as_of_text),
                )
                seen = self._loggable_legs(legs)
                changed = self._changed_legs(con, seen, as_of, config)
                self._insert_legs(con, changed, scan_id, as_of_text, config.config_id)

                n_coupons_logged = 0
                n_coupons_top = n_coupons_passing = n_coupons_evaluated = 0
                if coupons is not None:
                    n_coupons_evaluated = int(coupons.n_evaluated)
                    n_coupons_passing = int(coupons.n_passing)
                    n_coupons_top = int(coupons.top.shape[0]) if len(coupons.coupons) else 0
                    n_coupons_logged = self._insert_coupons(con, coupons, scan_id, as_of, as_of_text, config)

                n_value = int(legs["is_value"].sum()) if "is_value" in legs.columns and len(legs) else 0
                n_picks = int(diagnostics.get("n_picks", n_value))
                con.execute(
                    "INSERT INTO scans (scan_id, started_at, finished_at, as_of, config_id, config_json, n_quotes, n_legs, "
                    "n_market_fair, n_edge_positive, n_value, n_model_only, n_picks, n_fixtures, n_legs_seen, "
                    "n_legs_logged, n_coupons_evaluated, n_coupons_passing, n_coupons_top, n_coupons_logged, "
                    "diagnostics_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        scan_id,
                        _iso(started_at),
                        _iso(finished_at if finished_at is not None else pd.Timestamp.now(tz="UTC")),
                        as_of_text,
                        config.config_id,
                        config.to_json(),
                        diagnostics.get("n_quotes"),
                        diagnostics.get("n_legs", len(legs)),
                        diagnostics.get("n_market_fair"),
                        diagnostics.get("n_edge_positive"),
                        diagnostics.get("n_value", n_value),
                        diagnostics.get("n_model_only"),
                        n_picks,
                        diagnostics.get("n_fixtures"),
                        len(seen),
                        len(changed),
                        n_coupons_evaluated,
                        n_coupons_passing,
                        n_coupons_top,
                        n_coupons_logged,
                        json.dumps(diagnostics, sort_keys=True),
                    ),
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return True

    @staticmethod
    def _loggable_legs(legs: pd.DataFrame) -> pd.DataFrame:
        """Near-miss universe (price above fair) plus value legs plus value legs priced only by the model."""
        if legs is None or len(legs) == 0:
            return legs.iloc[0:0] if legs is not None else pd.DataFrame()
        mask = legs["leg_class"].isin(["value", "near_miss", "suspect", "unsynced"]) | legs["is_model_only_value"].astype(bool)
        return legs[mask]

    def _ensure_columns(self, con: sqlite3.Connection, table: str, frame: pd.DataFrame) -> None:
        """Add the columns of ``frame`` that the table lacks (regime columns depend on the config)."""
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        for column in frame.columns:
            if column in existing:
                continue
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {_sql_type(frame[column])}")

    def _changed_legs(
        self, con: sqlite3.Connection, seen: pd.DataFrame, as_of: object, config: ScannerConfig
    ) -> pd.DataFrame:
        if seen is None or len(seen) == 0:
            return seen
        con.execute("DROP TABLE IF EXISTS temp._keys")
        con.execute("CREATE TEMP TABLE _keys (bookmaker TEXT, leg_key TEXT, PRIMARY KEY (bookmaker, leg_key)) WITHOUT ROWID")
        con.executemany(
            "INSERT OR IGNORE INTO temp._keys VALUES (?, ?)",
            list(zip(seen["bookmaker"].tolist(), seen["leg_key"].tolist(), strict=True)),
        )
        previous = pd.read_sql_query(
            "SELECT l.bookmaker, l.leg_key, l.odd AS prev_odd, l.p_used AS prev_p, l.is_value AS prev_value, "
            "l.leg_class AS prev_class, l.as_of AS prev_as_of FROM temp._keys k "
            "JOIN legs l ON l.bookmaker = k.bookmaker AND l.leg_key = k.leg_key AND l.config_id = ? "
            "WHERE l.as_of = (SELECT MAX(l2.as_of) FROM legs l2 WHERE l2.bookmaker = k.bookmaker AND l2.leg_key = k.leg_key "
            "AND l2.config_id = ?)",
            con,
            params=[config.config_id, config.config_id],
        )
        con.execute("DROP TABLE IF EXISTS temp._keys")
        merged = seen[["bookmaker", "leg_key", "odd", "p_used", "is_value", "leg_class"]].merge(
            previous.drop_duplicates(["bookmaker", "leg_key"]), on=["bookmaker", "leg_key"], how="left"
        )
        now = pd.Timestamp(as_of)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        age = (now - to_utc(merged["prev_as_of"])).dt.total_seconds() / 3600.0
        is_new = merged["prev_odd"].isna()
        moved = (
            ((merged["odd"] - merged["prev_odd"]).abs() > 1e-9)
            | ((merged["p_used"] - merged["prev_p"]).abs() >= config.log_change_tolerance)
            | (merged["is_value"].astype(int) != merged["prev_value"].fillna(-1).astype(int))
            | (merged["leg_class"] != merged["prev_class"])
            | (age >= config.log_heartbeat_hours)
        )
        keep = (is_new | moved).to_numpy()
        return seen.reset_index(drop=True).loc[keep]

    def _insert_legs(self, con: sqlite3.Connection, legs: pd.DataFrame, scan_id: str, as_of_text: str, config_id: str) -> None:
        if legs is None or len(legs) == 0:
            return
        frame = legs.drop(columns=[c for c in ("as_of",) if c in legs.columns]).copy()
        for column in ("bet_id", "league_id"):
            if column not in frame.columns:
                frame[column] = np.nan
        regime = [c for c in frame.columns if c.startswith(("ev_net_", "value_"))]
        # regime columns depend on the config; columns added to the schema later are added to older registers
        self._ensure_columns(con, "legs", frame[[*regime, *[c for c in frame.columns if c in _LEG_COLUMNS]]])
        columns = [c for c in frame.columns if c in _LEG_COLUMNS or c in {"leg_key", "bookmaker"} or c in regime]
        rows = _rows(frame, columns)
        names = ["scan_id", "as_of", "config_id", *columns]
        marks = ",".join("?" * len(names))
        con.executemany(
            f"INSERT OR IGNORE INTO legs ({','.join(names)}) VALUES ({marks})",
            [(scan_id, as_of_text, config_id, *row) for row in rows],
        )

    def _insert_coupons(
        self,
        con: sqlite3.Connection,
        coupons: CouponSet,
        scan_id: str,
        as_of: object,
        as_of_text: str,
        config: ScannerConfig,
    ) -> int:
        frame = coupons.coupons
        if frame is None or len(frame) == 0:
            return 0
        wanted = frame[frame["is_top"].astype(bool) | frame["is_logged"].astype(bool)]
        if wanted.empty:
            return 0
        con.execute("DROP TABLE IF EXISTS temp._cids")
        con.execute("CREATE TEMP TABLE _cids (coupon_id TEXT PRIMARY KEY) WITHOUT ROWID")
        con.executemany("INSERT OR IGNORE INTO temp._cids VALUES (?)", [(c,) for c in wanted["coupon_id"].tolist()])
        previous = pd.read_sql_query(
            "SELECT c.coupon_id, c.odds_total AS prev_odds, c.is_top AS prev_top, c.as_of AS prev_as_of "
            "FROM temp._cids k JOIN coupons c ON c.coupon_id = k.coupon_id AND c.config_id = ? "
            "WHERE c.as_of = (SELECT MAX(c2.as_of) FROM coupons c2 WHERE c2.coupon_id = k.coupon_id AND c2.config_id = ?)",
            con,
            params=[config.config_id, config.config_id],
        ).drop_duplicates("coupon_id")
        con.execute("DROP TABLE IF EXISTS temp._cids")
        merged = wanted[["coupon_id", "odds_total", "is_top"]].merge(previous, on="coupon_id", how="left")
        now = pd.Timestamp(as_of)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        age = (now - to_utc(merged["prev_as_of"])).dt.total_seconds() / 3600.0
        changed = (
            merged["prev_odds"].isna()
            | ((merged["odds_total"] - merged["prev_odds"]).abs() > 1e-9)
            | (merged["is_top"].astype(int) != merged["prev_top"].fillna(-1).astype(int))
            | (age >= config.log_heartbeat_hours)
        )
        keep_ids = set(merged.loc[changed.to_numpy(), "coupon_id"])
        rows = wanted[wanted["coupon_id"].isin(keep_ids)].copy()
        if rows.empty:
            return 0
        regime = [c for c in rows.columns if c.startswith(("ev_net_", "pass_"))]
        self._ensure_columns(con, "coupons", rows[[*regime, *[c for c in rows.columns if c in _COUPON_COLUMNS]]])
        columns = [c for c in rows.columns if c in _COUPON_COLUMNS or c in regime]
        names = ["scan_id", "as_of", "coupon_id", "config_id", *columns]
        marks = ",".join("?" * len(names))
        values = _rows(rows, ["coupon_id", *columns])
        con.executemany(
            f"INSERT OR IGNORE INTO coupons ({','.join(names)}) VALUES ({marks})",
            [(scan_id, as_of_text, row[0], config.config_id, *row[1:]) for row in values],
        )
        legs = coupons.legs[coupons.legs["coupon_id"].isin(keep_ids)]
        if len(legs):
            leg_columns = [c for c in legs.columns if c in _COUPON_LEG_COLUMNS or c == "coupon_id"]
            self._ensure_columns(con, "coupon_legs", legs[[c for c in leg_columns if c in _COUPON_LEG_COLUMNS]])
            leg_values = _rows(legs, leg_columns)
            names = ["scan_id", *leg_columns]
            con.executemany(
                f"INSERT OR IGNORE INTO coupon_legs ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                [(scan_id, *row) for row in leg_values],
            )
        return len(rows)

    # ------------------------------------------------------------------ #
    # Paper bets
    # ------------------------------------------------------------------ #

    def _columns(self, table: str) -> set[str]:
        with closing(self._connect()) as con:
            return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

    def _keep_label(self, config_id: str | None) -> str:
        """Label of the full-tax regime of a config (``coupon_keep_tau``; the default config's when unknown)."""
        default = tau_label(ScannerConfig().coupon_keep_tau)
        if config_id is None:
            return default
        rows = self.read("SELECT config_json FROM configs WHERE config_id = ?", [config_id])
        if rows.empty:
            return default
        try:
            return tau_label(float(json.loads(rows["config_json"].iloc[0]).get("coupon_keep_tau")))
        except (TypeError, ValueError):
            return default

    def paper_legs(self, config_id: str | None = None, *, regime: str | None = None, tier: str | None = None) -> pd.DataFrame:
        """First value observation of every ``(bookmaker, leg)``: the price a paper bet would have taken.

        With ``config_id`` only the observations made under that config count (the first one *under that config*).
        ``regime`` (a label such as ``t088``) counts only observations that are value at that tax regime (the
        first such observation is the bet); ``tier`` only legs of fixtures of that market tier.  A regime the
        register has no column for gives an empty frame.
        """
        conditions, params = ["is_value = 1"], []
        if regime is not None:
            if f"value_{regime}" not in self._columns("legs"):
                return pd.DataFrame()
            conditions.append(f"value_{regime} = 1")
        if config_id is not None:
            conditions.append("config_id = ?")
            params.append(config_id)
        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier)
        frame = self.read(
            "SELECT * FROM (SELECT l.*, ROW_NUMBER() OVER (PARTITION BY bookmaker, leg_key ORDER BY as_of, scan_id) AS rn "
            f"FROM legs l WHERE {' AND '.join(conditions)}) WHERE rn = 1",
            params,
        )
        return self._times(frame.drop(columns=["rn"], errors="ignore"), ("as_of", "kickoff_utc", "fetched_at"))

    def paper_coupons(
        self, config_id: str | None = None, *, regime: str | None = None, tier: str | None = None, listed: bool = False
    ) -> pd.DataFrame:
        """First appearance of every coupon among the proposed (top) coupons (optionally under one config).

        By default the proposals are the listed coupons that pass at the full tax (``coupon_keep_tau`` of the
        config; rows written before coupons carried a ``regime`` all did).  ``regime`` instead takes every listed
        coupon that passes at that regime (``pass_t###``), ``listed=True`` every listed coupon of any group;
        ``tier`` only coupons of that market tier.
        """
        conditions, params = ["is_top = 1"], []
        if regime is not None:
            if f"pass_{regime}" not in self._columns("coupons"):
                return pd.DataFrame()
            conditions.append(f"pass_{regime} = 1")
        elif not listed:
            conditions.append("(regime IS NULL OR regime = ?)")
            params.append(self._keep_label(config_id))
        if config_id is not None:
            conditions.append("config_id = ?")
            params.append(config_id)
        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier)
        frame = self.read(
            "SELECT * FROM (SELECT c.*, ROW_NUMBER() OVER (PARTITION BY coupon_id ORDER BY as_of, scan_id) AS rn "
            f"FROM coupons c WHERE {' AND '.join(conditions)}) WHERE rn = 1",
            params,
        )
        return self._times(frame.drop(columns=["rn"], errors="ignore"), ("as_of", "kickoff_first", "kickoff_last"))

    def latest_settlements(self) -> pd.DataFrame:
        """The current outcome of every settled leg (a later row supersedes an earlier one)."""
        return self.read(
            "SELECT s.* FROM settlements s JOIN (SELECT leg_key, MAX(rowid) AS mr FROM settlements GROUP BY leg_key) m "
            "ON s.rowid = m.mr"
        )

    def latest_clv(self) -> pd.DataFrame:
        return self.read(
            "SELECT c.* FROM clv c JOIN (SELECT scan_id, bookmaker, leg_key, MAX(rowid) AS mr FROM clv "
            "GROUP BY scan_id, bookmaker, leg_key) m ON c.rowid = m.mr"
        )

    @staticmethod
    def _times(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
        for column in columns:
            if column in frame.columns:
                frame[column] = to_utc(frame[column])
        return frame

    # ------------------------------------------------------------------ #
    # Settlement and CLV
    # ------------------------------------------------------------------ #

    def unsettled_legs(self) -> pd.DataFrame:
        """Distinct legs (from ``legs`` and ``coupon_legs``) that have no settlement row yet."""
        return self.read(
            "SELECT leg_key, fixture_id, market, period, line, selection, MIN(kickoff_utc) AS kickoff_utc FROM ("
            "SELECT leg_key, fixture_id, market, period, line, selection, kickoff_utc FROM legs "
            "UNION ALL SELECT leg_key, fixture_id, market, period, line, selection, kickoff_utc FROM coupon_legs) u "
            "WHERE leg_key NOT IN (SELECT leg_key FROM settlements) "
            "GROUP BY leg_key, fixture_id, market, period, line, selection"
        )

    def append_settlements(self, frame: pd.DataFrame, *, source: str, settled_at: object = None) -> int:
        """Append settlement rows (``leg_key, fixture_id, market, period, line, selection, outcome``)."""
        if frame is None or len(frame) == 0:
            return 0
        stamp = _iso(settled_at if settled_at is not None else pd.Timestamp.now(tz="UTC"))
        rows = frame.dropna(subset=["outcome"]).copy()
        for optional in ("fixture_id", "market", "period", "line", "selection"):
            if optional not in rows.columns:
                rows[optional] = np.nan
        rows["settled_at"] = stamp
        rows["source"] = source
        columns = ["leg_key", "fixture_id", "market", "period", "line", "selection", "outcome", "settled_at", "source"]
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            before = con.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
            con.executemany(
                f"INSERT OR IGNORE INTO settlements ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                _rows(rows, columns),
            )
            after = con.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
            con.execute("COMMIT")
        return int(after - before)

    def settle_pending(self, results: pd.DataFrame, *, now: object = None, source: str = "results:90min:cards=yellow+red") -> int:
        """Settle every unsettled leg whose fixture has a result; returns the number of new settlement rows.

        Corners and cards need the corresponding columns in ``results``; a leg whose statistic is missing stays
        unsettled (never guessed) and is retried on the next call.
        """
        legs = self.unsettled_legs()
        if legs.empty or results is None or len(results) == 0:
            return 0
        finished = set(pd.to_numeric(results["fixture_id"] if "fixture_id" in results.columns else results.index).astype(int))
        legs = legs[legs["fixture_id"].astype(int).isin(finished)]
        if legs.empty:
            return 0
        outcomes = st.settle_frame(legs, results)
        settled = legs.assign(outcome=outcomes)
        settled = settled[settled["outcome"].notna()]
        return self.append_settlements(settled, source=source, settled_at=now)

    def settle_voids(self, statuses: pd.DataFrame, *, now: object = None, grace_hours: float = st.DEFAULT_VOID_GRACE_HOURS) -> int:
        """Settle as ``void`` every unsettled leg whose match was not played (:func:`settlement.void_decision`).

        ``statuses`` has ``fixture_id, status_short, kickoff_utc`` (the provider's current kickoff) per fixture;
        the leg's own ``kickoff_utc`` is the kickoff it was taken for.  Returns the number of new settlement rows;
        ``source`` is ``void:<STATUS>`` or ``void:rescheduled``.
        """
        if statuses is None or len(statuses) == 0:
            return 0
        legs = self.unsettled_legs()
        if legs.empty:
            return 0
        stamp = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        table = statuses.drop_duplicates("fixture_id", keep="last").copy()
        table["fixture_id"] = pd.to_numeric(table["fixture_id"], errors="coerce")
        table = table[table["fixture_id"].notna()].set_index(table["fixture_id"].astype("int64"))
        legs = legs[pd.to_numeric(legs["fixture_id"], errors="coerce").isin(table.index)].copy()
        if legs.empty:
            return 0
        reasons = []
        for fixture_id, kickoff in zip(legs["fixture_id"].astype("int64"), legs["kickoff_utc"], strict=True):
            row = table.loc[int(fixture_id)]
            reasons.append(st.void_decision(row.get("status_short"), kickoff, row.get("kickoff_utc"), stamp, grace_hours))
        legs["reason"] = reasons
        legs = legs[legs["reason"].notna()]
        written = 0
        for reason, group in legs.groupby("reason"):
            written += self.append_settlements(group.assign(outcome="void"), source=str(reason), settled_at=stamp)
        return written

    def record_bet_type_checks(
        self, checks: pd.DataFrame, *, data_as_of: object, computed_at: object = None, settings: dict | None = None
    ) -> int:
        """Append one measurement of the bet types (:func:`model_buk.scanner.bet_types.flag_bet_types` rows)."""
        if checks is None or len(checks) == 0:
            return 0
        frame = checks.copy()
        frame["data_as_of"] = _iso(data_as_of)
        frame["computed_at"] = _iso(computed_at if computed_at is not None else pd.Timestamp.now(tz="UTC"))
        frame["settings_json"] = json.dumps(settings or {}, sort_keys=True)
        for column in ("bet", "market", "period", "mad_ratio", "excluded", "reason"):
            if column not in frame.columns:
                frame[column] = np.nan
        columns = [
            "data_as_of", "computed_at", "bookmaker", "bet_id", "bet", "market", "period", "n_legs", "n_fixtures",
            "median_dev", "mad_dev", "p90_abs_dev", "tail_share", "mad_ratio", "excluded", "reason", "settings_json",
        ]  # fmt: skip
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            before = con.execute("SELECT COUNT(*) FROM bet_type_checks").fetchone()[0]
            con.executemany(
                f"INSERT OR IGNORE INTO bet_type_checks ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                _rows(frame, columns),
            )
            after = con.execute("SELECT COUNT(*) FROM bet_type_checks").fetchone()[0]
            con.execute("COMMIT")
        return int(after - before)

    def latest_bet_type_checks(self, as_of: object) -> pd.DataFrame:
        """The newest measurement whose data ended at or before ``as_of`` (empty when there is none)."""
        stamp = _iso(as_of)
        latest = self.read("SELECT MAX(data_as_of) AS d FROM bet_type_checks WHERE data_as_of <= ?", [stamp])
        if latest.empty or latest["d"].iloc[0] is None:
            return pd.DataFrame()
        return self.read("SELECT * FROM bet_type_checks WHERE data_as_of = ? ORDER BY bookmaker, bet_id", [latest["d"].iloc[0]])

    def paper_legs_without_clv(self, now: object, max_age_days: float | None = 7.0) -> pd.DataFrame:
        """Paper legs whose match has kicked off and that have no CLV row yet.

        A leg whose match started more than ``max_age_days`` ago is not retried (``None`` retries every leg): a
        closing price that has not turned up in a week will not, and the retry loads and prices the closing board
        of every fixture in the list on every scan.
        """
        legs = self.paper_legs()
        if legs.empty:
            return legs
        stamp = pd.Timestamp(now)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        done = self.latest_clv()
        legs = legs[legs["kickoff_utc"] <= stamp]
        if max_age_days is not None:
            legs = legs[legs["kickoff_utc"] >= stamp - pd.Timedelta(days=max_age_days)]
        if done.empty:
            return legs
        key = done[["scan_id", "bookmaker", "leg_key"]].assign(_done=1)
        merged = legs.merge(key, on=["scan_id", "bookmaker", "leg_key"], how="left")
        return merged[merged["_done"].isna()].drop(columns=["_done"])

    def record_clv(
        self,
        closing_fair: pd.DataFrame,
        *,
        taus: Sequence[float],
        closing_quotes: pd.DataFrame | None = None,
        now: object = None,
        source: str = "closing_fair",
    ) -> int:
        """Append the CLV of paper legs whose match has started, from ``closing_fair``.

        ``closing_fair`` holds the de-vigged closing probability per leg (``fixture_id, market, period, line,
        selection, p_fair`` and optionally ``fair_source``); it must be taken from the last snapshot before
        kickoff.  ``closing_quotes`` (optional, quotes-frame columns) supplies the closing price at the bookmaker
        that was used.  The closing price is used here and nowhere else.  ``clv_raw = odd_taken * p_close - 1``;
        the net value per regime is ``tau * odd_taken * p_close - 1`` and is stored as ``clv_net_t###``.  Legs
        without a closing probability get no row (missing stays missing) and are retried on the next call.

        When ``closing_quotes`` carries ``fetched_at``, a closing price is only a closing price if a board was
        observed *after* the board the decision was taken from: the archive is scanned about hourly, so the last
        board before kickoff is often the very board the leg was priced on, and comparing a price with itself
        says nothing (every such leg would look like a beaten close).  Those legs get a row with a missing
        closing probability and ``source`` ending in ``:no_later_board``: they never count in the CLV statistics
        and are not retried (the set of boards before kickoff cannot change any more).
        """
        stamp = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        pending = self.paper_legs_without_clv(stamp)
        if pending.empty or closing_fair is None or len(closing_fair) == 0:
            return 0
        key = list(KEY_COLUMNS)
        close = normalise_keys(closing_fair)
        close["closing_p_fair"] = pd.to_numeric(close["p_fair"], errors="coerce")
        close = close[(close["closing_p_fair"] > 0.0) & (close["closing_p_fair"] <= 1.0)]
        if "fair_source" not in close.columns:
            close["fair_source"] = np.nan
        close = close.rename(columns={"fair_source": "closing_fair_source"})
        close = close[[*key, "closing_p_fair", "closing_fair_source"]].drop_duplicates(key, keep="last")

        p = normalise_keys(pending)
        p["odd_taken"] = pd.to_numeric(p["odd"], errors="coerce")
        merged = p.merge(close, on=key, how="left")
        merged["closing_odd"] = np.nan
        if closing_quotes is not None and len(closing_quotes):
            quotes = normalise_keys(closing_quotes)
            quotes["closing_odd"] = pd.to_numeric(quotes["odd"], errors="coerce")
            quotes = quotes[[*key, "bookmaker", "closing_odd"]].drop_duplicates([*key, "bookmaker"], keep="last")
            merged = merged.drop(columns=["closing_odd"]).merge(quotes, on=[*key, "bookmaker"], how="left")
        merged["source"] = source
        no_later = pd.Series(False, index=merged.index)
        if closing_quotes is not None and len(closing_quotes) and "fetched_at" in closing_quotes.columns:
            boards = to_utc(closing_quotes["fetched_at"])
            last_board = pd.Series(boards.to_numpy(), index=pd.to_numeric(closing_quotes["fixture_id"]).astype("int64").to_numpy())
            last_board = last_board.groupby(level=0).max()
            seen = to_utc(merged["fetched_at"]) if "fetched_at" in merged.columns else pd.Series(pd.NaT, index=merged.index)
            later = merged["fixture_id"].map(last_board) > seen
            no_later = ~later.fillna(False).astype(bool)
            merged.loc[no_later, ["closing_p_fair", "closing_odd"]] = np.nan
            merged.loc[no_later, "closing_fair_source"] = np.nan
            merged.loc[no_later, "source"] = source + ":no_later_board"
        merged = merged[merged["closing_p_fair"].notna() | no_later].copy()
        if merged.empty:
            return 0
        clv = st.clv_columns(merged, taus)
        merged = pd.concat([merged.reset_index(drop=True), clv.reset_index(drop=True)], axis=1)
        merged["computed_at"] = _iso(stamp)
        net_columns = [f"clv_net_{tau_label(t)}" for t in taus]
        columns = [
            "scan_id",
            "bookmaker",
            "leg_key",
            "computed_at",
            "odd_taken",
            "closing_odd",
            "closing_p_fair",
            "closing_fair_source",
            "clv_raw",
            "source",
        ]
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            existing = {row[1] for row in con.execute("PRAGMA table_info(clv)")}
            for column in net_columns:
                if column not in existing:
                    con.execute(f"ALTER TABLE clv ADD COLUMN {column} REAL")
            all_columns = [*columns, *net_columns]
            before = con.execute("SELECT COUNT(*) FROM clv").fetchone()[0]
            con.executemany(
                f"INSERT OR IGNORE INTO clv ({','.join(all_columns)}) VALUES ({','.join('?' * len(all_columns))})",
                _rows(merged, all_columns),
            )
            after = con.execute("SELECT COUNT(*) FROM clv").fetchone()[0]
            con.execute("COMMIT")
        return int(after - before)

    # ------------------------------------------------------------------ #
    # Paper results
    # ------------------------------------------------------------------ #

    def paper_summary(
        self,
        *,
        now: object = None,
        windows: Sequence[int] = (7, 30),
        taus: Sequence[float] = (0.88, 0.94, 1.00),
        config_id: str | None = None,
    ) -> pd.DataFrame:
        """Paper results per window (last ``windows`` days by kickoff) for singles and for coupons.

        ``config_id`` restricts the bets to those first proposed under that config (default: all configs).

        One row per ``(kind, window_days)`` with ``kind`` in ``single | coupon``: ``n_bets`` (paper bets whose
        match has started), ``n_settled``, ``hit_rate`` (win 1, half win 0.75, half loss 0.25, pushes excluded),
        ``yield_t###`` (mean net profit per unit stake, settled bets only), ``n_clv``, ``median_clv_raw``,
        ``median_clv_net_t###`` and ``share_beaten`` (share of closing lines beaten, ``clv_raw > 0``).
        Missing values are NaN, never zero.
        """
        stamp = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        singles = self._single_table(stamp, taus, config_id)
        coupons = self._coupon_table(stamp, taus, config_id)
        rows = []
        for kind, table in (("single", singles), ("coupon", coupons)):
            for days in windows:
                rows.append(_summarise(kind, days, table, stamp, taus))
        return pd.DataFrame(rows)

    def paper_results(
        self,
        *,
        now: object = None,
        windows: Sequence[int] = (7, 30),
        taus: Sequence[float] = (0.88, 0.94, 1.00),
        config_id: str | None = None,
        tiers: Sequence[str] = ("liquid", "thin"),
    ) -> pd.DataFrame:
        """Paper results per ``(tier, regime, kind, window)``: the bets of one market tier that were value at one tax
        regime, with their profit at that regime.

        Columns: ``tier, regime`` (``t088`` ...), ``tau, kind`` (``single | coupon``), ``window_days, n_bets,
        n_settled, hit_rate, yield, n_clv, median_clv_raw, median_clv_net, share_beaten`` (see
        :meth:`paper_summary`; ``yield`` and ``median_clv_net`` at the row's own ``tau``).
        """
        stamp = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        rows = []
        for tier in tiers:
            for tau in taus:
                label = tau_label(tau)
                singles = self._single_table(stamp, [tau], config_id, regime=label, tier=tier)
                coupons = self._coupon_table(stamp, [tau], config_id, regime=label, tier=tier)
                for kind, table in (("single", singles), ("coupon", coupons)):
                    for days in windows:
                        row = _summarise(kind, days, table, stamp, [tau])
                        rows.append(
                            {
                                "tier": tier,
                                "regime": label,
                                "tau": float(tau),
                                "kind": kind,
                                "window_days": int(days),
                                "n_bets": row["n_bets"],
                                "n_settled": row["n_settled"],
                                "hit_rate": row["hit_rate"],
                                "yield": row[f"yield_{label}"],
                                "n_clv": row["n_clv"],
                                "median_clv_raw": row["median_clv_raw"],
                                "median_clv_net": row[f"median_clv_net_{label}"],
                                "share_beaten": row["share_beaten"],
                            }
                        )
        return pd.DataFrame(rows)

    def recent_bets(
        self, *, now: object = None, days: float = 7.0, config_id: str | None = None, taus: Sequence[float] = (0.88, 0.94, 1.00)
    ) -> dict[str, pd.DataFrame]:
        """The paper bets proposed in the last ``days`` days (by the time they were first proposed) and their fate.

        Returns ``{"singles": ..., "coupons": ..., "coupon_legs": ...}``.  Singles are the first value observation
        of every leg (with ``tier``, the ``value_t###`` flags, ``outcome`` and ``profit_t###``); coupons every coupon
        first listed (of either tax group, with ``tier``, ``regime``, ``outcome`` and ``profit_t###``); coupon legs
        the legs of those coupons with their own ``outcome``.  ``outcome`` is ``None`` while the bet is open.
        """
        stamp = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        since = stamp - pd.Timedelta(days=float(days))
        settled = self.latest_settlements()[["leg_key", "outcome"]].drop_duplicates("leg_key")
        singles = self.paper_legs(config_id)
        if not singles.empty:
            singles = singles[(singles["as_of"] >= since) & (singles["as_of"] <= stamp)]
            singles = singles.merge(settled, on="leg_key", how="left")
            for tau in taus:
                singles[f"profit_{tau_label(tau)}"] = [
                    st.single_profit(o, odd, tau) if isinstance(o, str) else np.nan
                    for o, odd in zip(singles["outcome"], singles["odd"], strict=True)
                ]
            singles = singles.sort_values(["kickoff_utc", "bookmaker", "leg_key"], ascending=[False, True, True], kind="mergesort")
        coupons = self.paper_coupons(config_id, listed=True)
        coupon_legs = pd.DataFrame()
        if not coupons.empty:
            coupons = coupons[(coupons["as_of"] >= since) & (coupons["as_of"] <= stamp)].copy()
        if not coupons.empty:
            ids = coupons["coupon_id"].tolist()
            marks = ",".join("?" * len(ids))
            coupon_legs = self.read(f"SELECT * FROM coupon_legs WHERE coupon_id IN ({marks})", ids)
            first = coupons[["scan_id", "coupon_id"]]
            coupon_legs = coupon_legs.merge(first, on=["scan_id", "coupon_id"], how="inner").merge(settled, on="leg_key", how="left")
            coupon_legs = self._times(coupon_legs, ("kickoff_utc",))
            outcomes, profits = [], {tau_label(t): [] for t in taus}
            for record in coupons.itertuples(index=False):
                group = coupon_legs[coupon_legs["coupon_id"] == record.coupon_id]
                result = (
                    st.settle_coupon([(o if isinstance(o, str) else None, float(odd)) for o, odd in zip(group["outcome"], group["odd"], strict=True)])
                    if len(group)
                    else None
                )
                outcomes.append(None if result is None else result.outcome)
                for tau in taus:
                    profits[tau_label(tau)].append(np.nan if result is None else result.profit(tau))
            coupons["outcome"] = outcomes
            for label, values in profits.items():
                coupons[f"profit_{label}"] = values
            coupons = coupons.sort_values(["kickoff_last", "coupon_id"], ascending=[False, True], kind="mergesort")
        return {"singles": singles, "coupons": coupons, "coupon_legs": coupon_legs}

    def _single_table(
        self,
        now: pd.Timestamp,
        taus: Sequence[float],
        config_id: str | None = None,
        *,
        regime: str | None = None,
        tier: str | None = None,
    ) -> pd.DataFrame:
        legs = self.paper_legs(config_id, regime=regime, tier=tier)
        if legs.empty:
            return legs
        legs = legs[legs["kickoff_utc"] <= now]
        settled = self.latest_settlements()[["leg_key", "outcome"]].drop_duplicates("leg_key")
        clv = self.latest_clv()
        keep = ["scan_id", "bookmaker", "leg_key", "clv_raw", *[c for c in clv.columns if c.startswith("clv_net_")]]
        table = legs.merge(settled, on="leg_key", how="left")
        if not clv.empty:
            table = table.merge(clv[keep], on=["scan_id", "bookmaker", "leg_key"], how="left")
        table["when"] = table["kickoff_utc"]
        for tau in taus:
            table[f"profit_{tau_label(tau)}"] = [
                st.single_profit(o, odd, tau) if isinstance(o, str) else np.nan
                for o, odd in zip(table["outcome"], table["odd"], strict=True)
            ]
        table["hit"] = [st.hit_value(o) if isinstance(o, str) else np.nan for o in table["outcome"]]
        return table

    def _coupon_table(
        self,
        now: pd.Timestamp,
        taus: Sequence[float],
        config_id: str | None = None,
        *,
        regime: str | None = None,
        tier: str | None = None,
    ) -> pd.DataFrame:
        coupons = self.paper_coupons(config_id, regime=regime, tier=tier)
        if coupons.empty:
            return coupons
        coupons = coupons[coupons["kickoff_first"] <= now]
        if coupons.empty:
            return coupons
        legs = self.read("SELECT * FROM coupon_legs")
        settled = self.latest_settlements()[["leg_key", "outcome"]].drop_duplicates("leg_key")
        clv = self.latest_clv().drop_duplicates(["bookmaker", "leg_key"], keep="first")
        legs = legs.merge(settled, on="leg_key", how="left")
        if not clv.empty:
            legs = legs.merge(clv[["bookmaker", "leg_key", "closing_p_fair"]], on=["bookmaker", "leg_key"], how="left")
        else:
            legs["closing_p_fair"] = np.nan
        records = []
        for coupon in coupons.itertuples(index=False):
            group = legs[(legs["scan_id"] == coupon.scan_id) & (legs["coupon_id"] == coupon.coupon_id)]
            outcome = (
                st.settle_coupon([(o if isinstance(o, str) else None, float(odd)) for o, odd in zip(group["outcome"], group["odd"], strict=True)])
                if len(group)
                else None
            )
            row: dict[str, Any] = {"coupon_id": coupon.coupon_id, "when": coupon.kickoff_last, "n_legs": coupon.n_legs}
            if outcome is None:
                row.update({"outcome": None, "hit": np.nan})
                for tau in taus:
                    row[f"profit_{tau_label(tau)}"] = np.nan
            else:
                row.update({"outcome": outcome.outcome, "hit": 1.0 if outcome.factor > 1.0 else (0.0 if outcome.factor < 1.0 else np.nan)})
                for tau in taus:
                    row[f"profit_{tau_label(tau)}"] = outcome.profit(tau)
            closings = group["closing_p_fair"].to_numpy(dtype=float)
            if len(group) and np.isfinite(closings).all():
                product = float(np.prod(group["odd"].to_numpy(dtype=float) * closings))
                row["clv_raw"] = product - 1.0
                for tau in taus:
                    row[f"clv_net_{tau_label(tau)}"] = tau * product - 1.0
            else:
                row["clv_raw"] = np.nan
                for tau in taus:
                    row[f"clv_net_{tau_label(tau)}"] = np.nan
            records.append(row)
        return pd.DataFrame(records)


def _summarise(kind: str, days: int, table: pd.DataFrame, now: pd.Timestamp, taus: Sequence[float]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": kind,
        "window_days": int(days),
        "n_bets": 0,
        "n_settled": 0,
        "hit_rate": np.nan,
        "n_clv": 0,
        "median_clv_raw": np.nan,
        "share_beaten": np.nan,
    }
    for tau in taus:
        row[f"yield_{tau_label(tau)}"] = np.nan
        row[f"median_clv_net_{tau_label(tau)}"] = np.nan
    if table is None or len(table) == 0:
        return row
    window = table[table["when"] >= now - pd.Timedelta(days=days)]
    row["n_bets"] = int(len(window))
    if window.empty:
        return row
    settled = window[window["outcome"].notna()]
    row["n_settled"] = int(len(settled))
    if len(settled):
        hits = settled["hit"].dropna()
        row["hit_rate"] = float(hits.mean()) if len(hits) else np.nan
        for tau in taus:
            values = settled[f"profit_{tau_label(tau)}"].dropna()
            row[f"yield_{tau_label(tau)}"] = float(values.mean()) if len(values) else np.nan
    if "clv_raw" in window.columns:
        clv = window["clv_raw"].dropna()
        row["n_clv"] = int(len(clv))
        if len(clv):
            row["median_clv_raw"] = float(clv.median())
            row["share_beaten"] = float((clv > 0).mean())
            for tau in taus:
                column = f"clv_net_{tau_label(tau)}"
                if column in window.columns:
                    values = window[column].dropna()
                    row[f"median_clv_net_{tau_label(tau)}"] = float(values.median()) if len(values) else np.nan
    return row

