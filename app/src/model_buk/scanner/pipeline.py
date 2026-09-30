"""One scanner run end to end, in paper mode: calendar, quotes, fair prices, models, value, register, settlement.

:func:`run_pipeline` is what the scheduled job runs (``app/scripts/run_scanner.py``).  Every run looks at every
upcoming match that has odds in the archive (no fixture cap, all markets, all bookmakers) and:

1. **Calendar.**  Refreshes the fixture directory (:class:`~model_buk.ingestion.fixture_directory.
   FixtureDirectoryCollector`): today on every run, the other days of the window only when their listing is
   stale, one request per day for every league at once.  Skipped without a client, with the API switched off or
   when the quota is at the reserve; the run goes on with what the directory already holds.
2. **Quotes.**  The latest price of every bookmaker for every leg of every fixture that has not started
   (:func:`model_buk.scanner.markets.load_current_quotes`, only prices seen at or before ``as_of``).
3. **Fair prices.**  Pinnacle de-vigged, derived legs and consensus (:func:`model_buk.scanner.fair.fair_prices`).
4. **Models.**  The pooled goal models and the corner / card count models for the fixtures of the modelled
   competitions, predicted from the cached nightly fit (:mod:`model_buk.scanner.live_models`).  A missing fit or
   one older than :attr:`ScanConfig.model_max_fit_age_hours` (one day) is skipped with a warning: the models are a
   secondary signal and the scan is complete without them.  The forecasts of every fit are appended to
   ``model_forecasts.sqlite`` (goal markets for every forecast fixture, count lines only where a bookmaker
   quotes them) so the live models can be evaluated later even after their cache is pruned.
5. **Value.**  Legs, value list and coupons (:mod:`model_buk.scanner.engine`, :mod:`model_buk.scanner.coupons`).
6. **Register.**  The scan is appended to the paper register under one ``scan_id``
   (:class:`~model_buk.scanner.register.ScannerRegister`); the scan row's ``diagnostics_json`` also carries the
   pipeline's own counts (fair prices by source, model coverage, calendar refresh).
7. **Settlement and CLV.**  Paper legs of earlier scans whose match is over are settled: scores come from the
   fixture directory (the history archive fills gaps); for corner and card legs the match statistics are fetched
   (``/fixtures?ids``, 20 matches per request; first-half splits one request per match, value and coupon legs
   first), each step capped and stopped at the quota reserve.  A leg whose statistic is still missing stays
   unsettled and is retried on the next run.  Closing line value is recorded for paper legs whose match has
   kicked off, from the last quotes strictly before kickoff (closing prices are used for CLV only).
8. **Outputs.**  ``latest.html`` and ``html/YYYY-MM-DD.html`` (the page), ``latest_value_legs.csv`` /
   ``latest_coupons.csv`` and their dated copies under ``csv/`` (Polish date, overwritten by later runs of the same
   day, like the dated page), and one JSON line per run in ``runs.jsonl`` (timings, API requests, counts,
   warnings).

Steps 2, 3, 5 and 6 must succeed (an exception there aborts the run: nothing half-done is recorded).  The others
degrade: a failure becomes a warning in the result and in the run log, and the run goes on.

Point in time.  Quotes and fair prices only use prices seen at or before ``as_of``; the models use a fit whose
training data ended before ``as_of - 3 h``.  The calendar refresh, settlement and closing prices are about
matches that are already decided or only name the fixtures, they never enter a decision.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

from model_buk.scanner.config import RunSettings, ScanConfig
from model_buk.scanner.engine import (
    FIXTURE_COLUMNS,
    ScanResult,
    default_scan_id,
    evaluate_legs_with_diagnostics,
    select_model_only,
    select_value_picks,
    to_utc,
)
from model_buk.scanner.settlement import RESULT_FIELDS, format_line, tau_label

PIPELINE_VERSION = "scanner-pipeline-v1"
"""Written to the run log and the scan diagnostics; bump when the meaning of a pipeline output changes."""

FINISHED_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN"})
_COUNT_PREFIXES: tuple[str, ...] = ("corners_", "cards_")
_GOAL_MODEL_MARKETS: frozenset[str] = frozenset({"1x2", "dc", "dnb", "ah", "ou", "btts"})
"""Model markets logged for every forecast fixture (a few rows each); count lines are logged only where quoted."""


# --------------------------------------------------------------------------- #
# Result of a run
# --------------------------------------------------------------------------- #


@dataclass
class PipelineResult:
    """Everything one run did (in memory); :meth:`record` is the JSON line of the run log."""

    scan_id: str
    as_of: pd.Timestamp
    config: ScanConfig
    settings: RunSettings
    started_at: pd.Timestamp
    finished_at: pd.Timestamp | None = None
    scan: ScanResult | None = None
    quotes: pd.DataFrame | None = None
    fair: pd.DataFrame | None = None
    model: pd.DataFrame | None = None
    fixtures: pd.DataFrame | None = None
    recorded: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    directory: dict[str, Any] = field(default_factory=dict)
    model_info: dict[str, Any] = field(default_factory=dict)
    settlement: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)

    @property
    def api_requests(self) -> int:
        return int(sum(int(v) for k, v in self.api.items() if k.startswith("requests_")))

    @property
    def seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else pd.Timestamp.now(tz="UTC")
        return float((end - self.started_at).total_seconds())

    def record(self) -> dict[str, Any]:
        """The run as plain JSON-serialisable data (one line of ``runs.jsonl``)."""
        return _jsonable(
            {
                "pipeline_version": PIPELINE_VERSION,
                "scan_id": self.scan_id,
                "as_of": _iso(self.as_of),
                "started_at": _iso(self.started_at),
                "finished_at": _iso(self.finished_at),
                "seconds": round(self.seconds, 2),
                "config_id": self.config.config_id,
                "recorded": self.recorded,
                "counts": self.counts,
                "timings": {k: round(v, 3) for k, v in self.timings.items()},
                "api": self.api,
                "directory": self.directory,
                "model": self.model_info,
                "settlement": self.settlement,
                "warnings": self.warnings,
                "outputs": self.outputs,
                "settings": self.settings.to_dict(),
            }
        )

    def summary_line(self) -> str:
        """One line for the scheduler log (``Model-Buk-Data\\logs\\scanner.log``)."""
        c = self.counts
        fair = c.get("fair_by_source", {})
        fair_text = ",".join(f"{_SOURCE_SHORT.get(k, k)}={v}" for k, v in sorted(fair.items()))
        value_text = " ".join(f"value_{label}={c.get('value_by_tau', {}).get(label, 0)}" for label in self.config.labels())
        model = self.model_info.get("status", "off")
        if model == "ok":
            model = f"ok({self.model_info.get('fixtures_forecast', 0)}fx)"
        parts = [
            f"scan={self.scan_id}",
            f"cfg={self.config.config_id}",
            f"recorded={int(self.recorded)}",
            f"fixtures={c.get('fixtures', 0)}",
            f"evaluated={c.get('fixtures_evaluated', 0)}",
            f"quotes={c.get('quotes', 0)}",
            f"fair={c.get('fair_legs', 0)}({fair_text})",
            f"legs={c.get('legs', 0)}",
            f"above_fair={c.get('above_fair', 0)}",
            f"suspect={c.get('suspect', 0)}",
            value_text,
            f"picks={c.get('picks', 0)}",
            f"coupons={c.get('coupons_top', 0)}/{c.get('coupons_passing', 0)}",
            f"model={model}",
            f"settled={self.settlement.get('settled', 0)}",
            f"clv={self.settlement.get('clv_rows', 0)}",
            f"api={self.api_requests}",
            f"quota={self.api.get('quota_remaining', '?')}",
            f"time={self.seconds:.1f}s",
            f"warnings={len(self.warnings)}",
        ]
        line = " ".join(p for p in parts if p)
        if self.warnings:
            line += " | " + " | ".join(w.replace("\n", " ") for w in self.warnings)
        return line


_SOURCE_SHORT = {"pinnacle": "pin", "pinnacle_derived": "der", "consensus": "cons", "betfair": "bf"}


def _iso(value: object) -> str | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonable(value: Any) -> Any:
    """Numbers, text, lists and dicts only (numpy scalars, timestamps and paths converted; NaN becomes None)."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return _iso(value)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _utc_stamp(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


@contextmanager
def _timed(result: PipelineResult, step: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        result.timings[step] = result.timings.get(step, 0.0) + (time.perf_counter() - start)


# --------------------------------------------------------------------------- #
# Step 1: calendar refresh
# --------------------------------------------------------------------------- #


def _collector(client: Any, settings: RunSettings, max_requests: int, clock: Callable[[], datetime] | None):
    from model_buk.ingestion.api_football_history import RequestBudget
    from model_buk.ingestion.fixture_directory import FixtureDirectory, FixtureDirectoryCollector

    directory = FixtureDirectory(settings.directory_root)
    budget = RequestBudget(max_requests=int(max_requests), reserve=int(settings.api_reserve))
    collector = FixtureDirectoryCollector(
        client, directory, budget, sleep_between_requests=float(settings.api_sleep), clock=clock
    )
    return collector, directory, budget


def refresh_directory(
    client: Any,
    settings: RunSettings,
    now: pd.Timestamp,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """List the due days of the calendar window (see :meth:`FixtureDirectoryCollector.refresh`).

    Returns ``requests, dates_fetched, fixtures_listed, fixtures_new, fixtures_changed, stopped_reason, errors,
    quota_remaining``.  Never raises for API trouble: errors are reported in the dictionary.
    """
    collector, directory, budget = _collector(client, settings, settings.max_requests_directory, clock)
    moment = now.to_pydatetime()
    report = collector.refresh(
        moment, days_back=int(settings.directory_days_back), days_ahead=int(settings.directory_days_ahead)
    )
    return {
        "requests": int(report.requests),
        "dates_fetched": list(report.dates_fetched),
        "fixtures_listed": int(report.fixtures_listed),
        "fixtures_new": int(report.fixtures_new),
        "fixtures_changed": int(report.fixtures_changed),
        "stopped_reason": report.stopped_reason,
        "errors": [str(e)[:200] for e in report.errors],
        "quota_remaining": directory.load_quota(moment) if budget.remaining_quota is None else budget.remaining_quota,
    }


# --------------------------------------------------------------------------- #
# Fixture names and the fixtures frame of the models
# --------------------------------------------------------------------------- #


def load_fixture_frame(fixture_ids: Iterable[int], settings: RunSettings) -> pd.DataFrame:
    """Fixtures frame (contract columns, plus ``referee`` where known) of ``fixture_ids``.

    The fixture directory first (every league), the prematch archive (tracked competitions) for fixtures the
    directory does not hold; a fixture found in neither is absent.  Never raises for an unreadable database.
    """
    ids = sorted({int(i) for i in fixture_ids})
    columns = [*FIXTURE_COLUMNS, "referee"]
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    if not ids:
        return empty
    parts: list[pd.DataFrame] = []
    found: set[int] = set()
    if settings.directory_db.exists():
        from model_buk.ingestion.fixture_directory import load_fixtures

        try:
            frame = load_fixtures(settings.directory_db, fixture_ids=ids, full=True)
            for column in columns:
                if column not in frame.columns:
                    frame[column] = np.nan
            parts.append(frame[columns])
            found = set(pd.to_numeric(frame["fixture_id"]).astype(int))
        except (sqlite3.Error, pd.errors.DatabaseError):
            pass
    missing = [i for i in ids if i not in found]
    if missing and Path(settings.prematch_db).exists():
        try:
            uri = f"{Path(settings.prematch_db).resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=30)) as con:
                rows = []
                for start in range(0, len(missing), 500):
                    chunk = missing[start : start + 500]
                    marks = ",".join("?" * len(chunk))
                    rows.append(
                        pd.read_sql_query(
                            "SELECT fixture_id, league_id, season, round, kickoff_utc, status_short, home_team_id, "
                            f"home_team, away_team_id, away_team FROM fixtures WHERE fixture_id IN ({marks})",
                            con,
                            params=chunk,
                        )
                    )
            frame = pd.concat(rows, ignore_index=True).drop_duplicates("fixture_id", keep="last")
            for column in columns:
                if column not in frame.columns:
                    frame[column] = np.nan
            parts.append(frame[columns])
        except (sqlite3.Error, pd.errors.DatabaseError):
            pass
    parts = [p for p in parts if len(p)]
    if not parts:
        return empty
    return pd.concat(parts, ignore_index=True).drop_duplicates("fixture_id", keep="first").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Step 4: models
# --------------------------------------------------------------------------- #


def forecast_models(
    fixtures: pd.DataFrame, as_of: pd.Timestamp, config: ScanConfig, settings: RunSettings
) -> tuple[pd.DataFrame | None, dict[str, Any], list[str]]:
    """Model frame of the upcoming ``fixtures`` from the cached fit: ``(frame or None, info, warnings)``.

    The fit must exist and be at most :attr:`ScanConfig.model_max_fit_age_hours` old; otherwise the models are
    skipped (``info["status"]`` is ``disabled``, ``import_error``, ``no_fit``, ``fit_stale``, ``no_fixtures`` or
    ``error``) and the scan runs on market prices alone.
    """
    if not config.use_models:
        return None, {"status": "disabled"}, []
    try:
        from model_buk.scanner import live_models as lm
    except Exception as exc:  # noqa: BLE001 - e.g. a blocked binary of a dependency; the scan goes on
        return None, {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"[:300]}, [
            f"models skipped: the live models cannot be imported ({type(exc).__name__})"
        ]
    live = lm.LiveConfig(cache_dir=Path(settings.model_cache_dir), max_fit_age_hours=float(config.model_max_fit_age_hours))
    found = lm.latest_fit(live, as_of)
    if found is None:
        return None, {"status": "no_fit"}, [
            f"models skipped: no model fit in {settings.model_cache_dir} (run app/scripts/fit_live_models.py fit)"
        ]
    _, manifest = found
    fit_as_of = lm.utc(manifest["fit_as_of"])
    age_hours = float((as_of - fit_as_of) / pd.Timedelta(hours=1))
    info: dict[str, Any] = {
        "fit_as_of": _iso(fit_as_of),
        "fit_age_hours": round(age_hours, 2),
        "train_cutoff": manifest.get("train_cutoff"),
        "history_built_at": manifest.get("history_built_at"),
    }
    if age_hours > config.model_max_fit_age_hours:
        info["status"] = "fit_stale"
        return None, info, [
            f"models skipped: the newest fit is {age_hours:.0f} h old (limit {config.model_max_fit_age_hours:g} h); "
            "the nightly fit_live_models.py fit refreshes it"
        ]
    if fixtures is None or len(fixtures) == 0:
        info["status"] = "no_fixtures"
        return None, info, []
    kickoff = to_utc(fixtures["kickoff_utc"])
    upcoming = fixtures[(kickoff > as_of).to_numpy()]
    upcoming = upcoming[upcoming["home_team_id"].notna() & upcoming["away_team_id"].notna()]
    if upcoming.empty:
        info["status"] = "no_fixtures"
        return None, info, []
    try:
        frame, report = lm.forecast_with_report(upcoming, as_of, config=live, include_counts=config.model_include_counts)
    except Exception as exc:  # noqa: BLE001 - the models are a secondary signal
        info.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
        return None, info, [f"models skipped: forecast failed ({type(exc).__name__}: {str(exc)[:120]})"]
    ok = report["status"] == "ok"
    info.update(
        status="ok",
        fixtures_asked=int(len(upcoming)),
        fixtures_forecast=int(ok.sum()),
        fixtures_validated=int((ok & report["validated"].astype(bool)).sum()),
        statuses={str(k): int(v) for k, v in report["status"].value_counts().items()},
        rows=int(len(frame)),
    )
    return frame, info, []


class ForecastLog:
    """Append-only SQLite log of the live model forecasts (``model_forecasts.sqlite``).

    One row per ``(fit_as_of, fixture, market, period, line, selection, model_name)``: a forecast depends only on
    the fit and the fixture, so the first scan that sees it writes it and later scans add nothing.  ``UPDATE`` and
    ``DELETE`` are blocked by triggers.  ``line_key`` is the canonical line text (``"na"`` without a line).
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS model_forecasts (
        fit_as_of TEXT NOT NULL,
        fixture_id INTEGER NOT NULL,
        market TEXT NOT NULL,
        period TEXT NOT NULL,
        line_key TEXT NOT NULL,
        line REAL,
        selection TEXT NOT NULL,
        model_name TEXT NOT NULL,
        p_model REAL NOT NULL,
        kickoff_utc TEXT,
        first_scan_id TEXT NOT NULL,
        first_as_of TEXT NOT NULL,
        PRIMARY KEY (fit_as_of, fixture_id, market, period, line_key, selection, model_name)
    );
    CREATE INDEX IF NOT EXISTS idx_model_forecasts_fixture ON model_forecasts (fixture_id);
    CREATE TRIGGER IF NOT EXISTS model_forecasts_no_update BEFORE UPDATE ON model_forecasts
        BEGIN SELECT RAISE(ABORT, 'the forecast log is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS model_forecasts_no_delete BEFORE DELETE ON model_forecasts
        BEGIN SELECT RAISE(ABORT, 'the forecast log is append-only'); END;
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=60)) as con:
            con.executescript(self._SCHEMA)
            con.commit()

    def append(
        self,
        model: pd.DataFrame,
        *,
        fit_as_of: object,
        scan_id: str,
        as_of: object,
        quoted: pd.DataFrame | None = None,
        kickoff: Mapping[int, object] | None = None,
    ) -> int:
        """Append the rows of ``model`` not logged yet; returns the number of new rows.

        Goal markets are logged for every fixture; count markets (corners, cards) only for the legs in ``quoted``
        (``fixture_id, market, period, line, selection``), because their line grid is long and only a quoted line
        can ever be compared with a price.
        """
        if model is None or len(model) == 0 or fit_as_of is None:
            return 0
        frame = model.copy()
        frame["line_key"] = [format_line(v) for v in frame["line"]]
        keep = frame["market"].isin(_GOAL_MODEL_MARKETS).to_numpy()
        if quoted is not None and len(quoted):
            q = quoted[["fixture_id", "market", "period", "line", "selection"]].copy()
            q["line_key"] = [format_line(v) for v in q["line"]]
            q["fixture_id"] = pd.to_numeric(q["fixture_id"]).astype("int64")
            keys = set(zip(q["fixture_id"], q["market"], q["period"], q["line_key"], q["selection"], strict=True))
            quoted_mask = np.fromiter(
                (
                    k in keys
                    for k in zip(
                        frame["fixture_id"].astype("int64"), frame["market"], frame["period"], frame["line_key"], frame["selection"], strict=True
                    )
                ),
                dtype=bool,
                count=len(frame),
            )
            keep = keep | quoted_mask
        frame = frame[keep]
        if frame.empty:
            return 0
        kickoff = kickoff or {}
        rows = [
            (
                _iso(fit_as_of),
                int(f),
                str(m),
                str(p),
                str(lk),
                None if not (isinstance(ln, (int, float)) and math.isfinite(float(ln))) else float(ln),
                str(s),
                str(name),
                float(pm),
                _iso(kickoff.get(int(f))),
                scan_id,
                _iso(as_of),
            )
            for f, m, p, lk, ln, s, name, pm in zip(
                frame["fixture_id"],
                frame["market"],
                frame["period"],
                frame["line_key"],
                frame["line"],
                frame["selection"],
                frame["model_name"],
                frame["p_model"],
                strict=True,
            )
            if pm == pm
        ]
        with closing(sqlite3.connect(self.path, timeout=60)) as con:
            before = con.execute("SELECT COUNT(*) FROM model_forecasts").fetchone()[0]
            con.executemany("INSERT OR IGNORE INTO model_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            after = con.execute("SELECT COUNT(*) FROM model_forecasts").fetchone()[0]
            con.commit()
        return int(after - before)

    def read(self) -> pd.DataFrame:
        with closing(sqlite3.connect(self.path, timeout=60)) as con:
            return pd.read_sql_query("SELECT * FROM model_forecasts", con)


# --------------------------------------------------------------------------- #
# Step 7: settlement data
# --------------------------------------------------------------------------- #


def statistics_needed(
    pending: pd.DataFrame, results: pd.DataFrame, as_of: pd.Timestamp, min_age_hours: float = 2.0
) -> tuple[list[int], list[int]]:
    """Fixtures whose unsettled legs need the match statistics fetched: ``(details_ids, halves_ids)``.

    ``pending`` holds unsettled legs (``fixture_id, market, period, kickoff_utc``), ``results`` the settlement
    statistics already known (:func:`model_buk.scanner.settlement.load_results`).  Only matches that kicked off at
    least ``min_age_hours`` before ``as_of`` are considered.  A fixture needs *details* (final score and full-time
    statistics, 20 per request) when it has no score yet, when a first- or second-half goal leg has no half-time
    score, or when a full-time or second-half corner / card leg has no count; it needs *halves* (one request per
    match) when a first- or second-half corner / card leg has no first-half count.
    """
    if pending is None or len(pending) == 0:
        return [], []
    legs = pending.copy()
    legs["kickoff"] = to_utc(legs["kickoff_utc"])
    legs = legs[legs["kickoff"] <= as_of - pd.Timedelta(hours=min_age_hours)]
    if legs.empty:
        return [], []
    legs["fixture_id"] = pd.to_numeric(legs["fixture_id"]).astype("int64")
    known = results.copy() if results is not None and len(results) else pd.DataFrame(columns=["fixture_id", *RESULT_FIELDS])
    known["fixture_id"] = pd.to_numeric(known["fixture_id"]).astype("int64")
    known = known.drop_duplicates("fixture_id").set_index("fixture_id")

    def missing(fixture_ids: pd.Series, column: str) -> np.ndarray:
        values = known[column].reindex(fixture_ids.to_numpy()) if column in known.columns else pd.Series(np.nan, index=fixture_ids)
        return pd.to_numeric(values, errors="coerce").isna().to_numpy()

    market = legs["market"].astype(str)
    period = legs["period"].astype(str)
    corners = market.str.startswith("corners_").to_numpy()
    cards = market.str.startswith("cards_").to_numpy()
    goals = ~(corners | cards)
    full = period.isin(["ft", "2h"]).to_numpy()
    half = period.isin(["1h", "2h"]).to_numpy()
    fid = legs["fixture_id"]
    details = (
        missing(fid, "home_goals")
        | (goals & half & missing(fid, "home_goals_1h"))
        | (corners & full & missing(fid, "home_corners"))
        | (cards & full & missing(fid, "home_cards"))
    )
    halves = (corners & half & missing(fid, "home_corners_1h")) | (cards & half & missing(fid, "home_cards_1h"))
    order = legs.sort_values("kickoff", kind="mergesort")
    rank = {f: i for i, f in enumerate(order["fixture_id"].drop_duplicates())}
    details_ids = sorted(set(fid[details]), key=rank.__getitem__)
    halves_ids = sorted(set(fid[halves]), key=rank.__getitem__)
    return [int(i) for i in details_ids], [int(i) for i in halves_ids]


def _prioritise(ids: Sequence[int], first: set[int]) -> list[int]:
    """``ids`` with the members of ``first`` moved to the front (order otherwise kept)."""
    return [i for i in ids if i in first] + [i for i in ids if i not in first]


def fetch_settlement_statistics(
    client: Any,
    settings: RunSettings,
    now: pd.Timestamp,
    details_ids: Sequence[int],
    halves_ids: Sequence[int],
    *,
    priority: set[int] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Fetch the match statistics the settlement lacks, within the per-step caps and above the quota reserve.

    Fixtures in ``priority`` (those with value or coupon legs) go first.  Only fixtures the directory considers
    worth a request are asked (:meth:`FixtureDirectory.stats_candidates`: not asked recently, not given up on,
    finished long enough ago), so a slot of the cap is never spent on a request the collector would skip.
    """
    from model_buk.ingestion.api_football_history import DEFAULT_BATCH_SIZE
    from model_buk.ingestion.fixture_directory import load_fixtures

    priority = priority or set()
    moment = now.to_pydatetime()
    out: dict[str, Any] = {"requests_details": 0, "requests_halves": 0, "stopped_reason": "completed", "errors": []}
    if details_ids and settings.max_requests_details > 0:
        collector, directory, _ = _collector(client, settings, settings.max_requests_details, clock)
        eligible = directory.stats_candidates(list(details_ids), moment, min_age_hours=settings.stats_min_age_hours)
        eligible = _prioritise([i for i in details_ids if i in set(eligible)], priority)
        eligible = eligible[: int(settings.max_requests_details) * int(DEFAULT_BATCH_SIZE)]
        if eligible:
            report = collector.fetch_fixture_details(eligible, moment, min_age_hours=settings.stats_min_age_hours)
            out.update(
                requests_details=int(report.requests),
                details_asked=len(eligible),
                details_with_stats=int(report.fixtures_with_stats),
                details_without_stats=int(report.fixtures_without_stats),
                stopped_reason=report.stopped_reason,
            )
            out["errors"] += [str(e)[:200] for e in report.errors]
    if halves_ids and settings.max_requests_halves > 0 and out["stopped_reason"] == "completed":
        collector, directory, _ = _collector(client, settings, settings.max_requests_halves, clock)
        eligible = set(directory.stats_candidates(list(halves_ids), moment, halves=True, min_age_hours=settings.stats_min_age_hours))
        if eligible:
            status = load_fixtures(directory.db_path, fixture_ids=sorted(eligible))
            finished = set(pd.to_numeric(status.loc[status["status_short"].isin(FINISHED_STATUSES), "fixture_id"]).astype(int))
            eligible &= finished
        ordered = _prioritise([i for i in halves_ids if i in eligible], priority)[: int(settings.max_requests_halves)]
        if ordered:
            report = collector.fetch_half_stats(ordered, moment, min_age_hours=settings.stats_min_age_hours)
            out.update(
                requests_halves=int(report.requests),
                halves_asked=len(ordered),
                halves_with_stats=int(report.fixtures_with_stats),
                stopped_reason=report.stopped_reason,
            )
            out["errors"] += [str(e)[:200] for e in report.errors]
    return out


# --------------------------------------------------------------------------- #
# Step 8: CSV outputs
# --------------------------------------------------------------------------- #


def _names(fixtures: pd.DataFrame | None) -> dict[int, tuple[str, str, str]]:
    table: dict[int, tuple[str, str, str]] = {}
    if fixtures is None or len(fixtures) == 0:
        return table
    for row in fixtures.itertuples(index=False):
        try:
            fid = int(row.fixture_id)
        except (TypeError, ValueError):
            continue
        home = row.home_team if isinstance(row.home_team, str) and row.home_team else f"Gospodarz #{fid}"
        away = row.away_team if isinstance(row.away_team, str) and row.away_team else f"Gość #{fid}"
        league = getattr(row, "league_name", None)
        country = getattr(row, "country", None)
        league_text = " · ".join(str(v) for v in (country, league) if isinstance(v, str) and v)
        table[fid] = (home, away, league_text)
    return table


def _local_text(value: object) -> str:
    from model_buk.scanner.report_html import local_time

    stamp = local_time(value)
    return "" if stamp is None else stamp.strftime("%Y-%m-%d %H:%M")


def value_legs_table(picks: pd.DataFrame, fixtures: pd.DataFrame | None, config: ScanConfig, scan_id: str) -> pd.DataFrame:
    """The value list of a scan as a flat table for a spreadsheet (Polish labels, Polish kickoff time)."""
    from model_buk.scanner.report_html import leg_label_pl

    labels = config.labels()
    ev_columns = [c for label in labels for c in (f"ev_net_{label}", f"ev_net_lower_{label}", f"value_{label}")]
    columns = [
        "rank", "kickoff_pl", "league", "match", "bet_pl", "bookmaker", "odd", "fair_odd", "p_fair", "p_se", "p_lower",
        "fair_source", "n_books", "edge_raw", "refund_share", "book_shift", *ev_columns, "p_model", "model_name",
        "model_agrees", "quote_age_min", "fetched_at", "kickoff_utc", "fixture_id", "market", "period", "line",
        "selection", "bet_id", "leg_key", "scan_id", "config_id",
    ]  # fmt: skip
    if picks is None or len(picks) == 0:
        return pd.DataFrame(columns=columns)
    names = _names(fixtures)
    out = picks.copy()
    default = ("", "", "")
    out["match"] = [
        f"{names.get(int(f), default)[0] or f'Gospodarz #{int(f)}'} - {names.get(int(f), default)[1] or f'Gość #{int(f)}'}"
        for f in out["fixture_id"]
    ]
    out["league"] = [names.get(int(f), default)[2] for f in out["fixture_id"]]
    out["bet_pl"] = [
        leg_label_pl(m, p, ln, s, *(names.get(int(f), (f"Gospodarz #{int(f)}", f"Gość #{int(f)}", ""))[:2]))
        for f, m, p, ln, s in zip(out["fixture_id"], out["market"], out["period"], out["line"], out["selection"], strict=True)
    ]
    out["kickoff_pl"] = [_local_text(v) for v in out["kickoff_utc"]]
    out["scan_id"] = scan_id
    out["config_id"] = config.config_id
    for column in columns:
        if column not in out.columns:
            out[column] = np.nan
    return out[columns]


def coupons_table(coupon_set: Any, fixtures: pd.DataFrame | None, config: ScanConfig, scan_id: str) -> pd.DataFrame:
    """The proposed coupons of a scan, one row per leg, best coupon first (the order of the page)."""
    from model_buk.scanner.report_html import leg_label_pl

    labels = config.labels()
    ev_columns = [c for label in labels for c in (f"ev_net_{label}", f"ev_net_lower_{label}")]
    columns = [
        "coupon_rank", "coupon_id", "bookmaker", "n_legs", "odds_total", "p_hit", "p_hit_lower", *ev_columns,
        "leg_no", "kickoff_pl", "league", "match", "bet_pl", "odd", "p_fair", "p_lower", "refund_share",
        "fair_source", "kickoff_utc", "fixture_id", "market", "period", "line", "selection", "leg_key", "scan_id",
        "config_id",
    ]  # fmt: skip
    if coupon_set is None or len(coupon_set.coupons) == 0:
        return pd.DataFrame(columns=columns)
    keep = tau_label(config.coupon_keep_tau)
    top = coupon_set.top.sort_values([f"ev_net_lower_{keep}", "p_hit"], ascending=[False, False], kind="mergesort").reset_index(drop=True)
    if top.empty:
        return pd.DataFrame(columns=columns)
    top["coupon_rank"] = np.arange(1, len(top) + 1)
    head = top[["coupon_rank", "coupon_id", "bookmaker", "n_legs", "odds_total", "p_hit", "p_hit_lower", *ev_columns]]
    legs = coupon_set.legs.drop(columns=["bookmaker"], errors="ignore")
    out = head.merge(legs, on="coupon_id", how="inner").sort_values(["coupon_rank", "leg_no"], kind="mergesort")
    names = _names(fixtures)
    default = ("", "", "")
    out["match"] = [
        f"{names.get(int(f), default)[0] or f'Gospodarz #{int(f)}'} - {names.get(int(f), default)[1] or f'Gość #{int(f)}'}"
        for f in out["fixture_id"]
    ]
    out["league"] = [names.get(int(f), default)[2] for f in out["fixture_id"]]
    out["bet_pl"] = [
        leg_label_pl(m, p, ln, s, *(names.get(int(f), (f"Gospodarz #{int(f)}", f"Gość #{int(f)}", ""))[:2]))
        for f, m, p, ln, s in zip(out["fixture_id"], out["market"], out["period"], out["line"], out["selection"], strict=True)
    ]
    out["kickoff_pl"] = [_local_text(v) for v in out["kickoff_utc"]]
    out["scan_id"] = scan_id
    out["config_id"] = config.config_id
    for column in columns:
        if column not in out.columns:
            out[column] = np.nan
    return out[columns].reset_index(drop=True)


def _write_csv_atomic(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(handle)
    try:
        frame.to_csv(tmp_name, index=False, encoding="utf-8-sig", float_format="%.6g")
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_csvs(
    scan: ScanResult, fixtures: pd.DataFrame | None, config: ScanConfig, out_dir: Path
) -> tuple[list[Path], list[str]]:
    """``latest_value_legs.csv``, ``latest_coupons.csv`` and the dated copies ``csv/YYYY-MM-DD_*.csv`` (Polish date).

    Returns ``(written paths, warnings)``: a file that cannot be replaced (open in a spreadsheet) is a warning.
    """
    from model_buk.scanner.report_html import local_time

    stamp = local_time(scan.as_of) or scan.as_of
    day = stamp.strftime("%Y-%m-%d")
    tables = {
        "value_legs": value_legs_table(scan.picks, fixtures, config, scan.scan_id),
        "coupons": coupons_table(scan.coupons, fixtures, config, scan.scan_id),
    }
    written: list[Path] = []
    problems: list[str] = []
    for name, table in tables.items():
        for target in (Path(out_dir) / f"latest_{name}.csv", Path(out_dir) / "csv" / f"{day}_{name}.csv"):
            try:
                _write_csv_atomic(table, target)
                written.append(target)
            except OSError as exc:
                problems.append(f"cannot write {target.name}: {exc.__class__.__name__}")
    return written, problems


def append_run_log(path: Path, record: Mapping[str, Any]) -> None:
    """Append one JSON line to the run log (append-only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# Counts for the report
# --------------------------------------------------------------------------- #


def scan_counts(quotes: pd.DataFrame, fair: pd.DataFrame, scan: ScanResult, config: ScanConfig) -> dict[str, Any]:
    """Headline numbers of a scan (fixtures, quotes, fair prices by source, legs above fair, value per regime)."""
    legs = scan.legs
    market = ~legs["is_model_only"].astype(bool) if len(legs) else pd.Series(dtype=bool)
    suspect = legs["suspect"].astype(bool) if len(legs) else pd.Series(dtype=bool)
    vetoed = legs["vetoed"].astype(bool) if len(legs) else pd.Series(dtype=bool)
    value_by_tau: dict[str, int] = {}
    for label in config.labels():
        if len(legs):
            value_by_tau[label] = int((legs[f"value_{label}"].astype(bool) & market & ~suspect & ~vetoed).sum())
        else:
            value_by_tau[label] = 0
    fair_by_source = (
        {str(k): int(v) for k, v in fair["fair_source"].value_counts().items()} if fair is not None and len(fair) else {}
    )
    coupons = scan.coupons
    mapped = quotes["market"] != "other" if len(quotes) and "market" in quotes.columns else pd.Series(dtype=bool)
    return {
        "fixtures": int(quotes["fixture_id"].nunique()) if len(quotes) else 0,
        "fixtures_evaluated": int(scan.diagnostics.n_fixtures),
        "bookmakers": int(quotes["bookmaker"].nunique()) if len(quotes) else 0,
        "quotes": int(len(quotes)),
        "quotes_mapped": int(mapped.sum()) if len(mapped) else 0,
        "fair_legs": int(len(fair)) if fair is not None else 0,
        "fair_fixtures": int(fair["fixture_id"].nunique()) if fair is not None and len(fair) else 0,
        "fair_by_source": fair_by_source,
        "legs": int(len(legs)),
        "legs_market_fair": int(market.sum()) if len(legs) else 0,
        "legs_model_only": int(legs["is_model_only"].astype(bool).sum()) if len(legs) else 0,
        "legs_with_model": int(legs["p_model"].notna().sum()) if len(legs) else 0,
        "above_fair": int(((legs["edge_raw"] > 0) & market).sum()) if len(legs) else 0,
        "suspect": int(suspect.sum()) if len(legs) else 0,
        "value": int(legs["is_value"].astype(bool).sum()) if len(legs) else 0,
        "value_by_tau": value_by_tau,
        "picks": int(len(scan.picks)),
        "model_only_value": int(len(scan.model_only)),
        "coupons_evaluated": int(coupons.n_evaluated),
        "coupons_passing": int(coupons.n_passing),
        "coupons_top": int(len(coupons.top)) if len(coupons.coupons) else 0,
        "diagnostics": scan.diagnostics.to_dict(),
    }


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def run_pipeline(
    config: ScanConfig | None = None,
    *,
    as_of: datetime | pd.Timestamp | str | None = None,
    settings: RunSettings | None = None,
    client: Any = None,
    clock: Callable[[], datetime] | None = None,
    log: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run one scan end to end (see the module docstring) and return what it did.

    ``as_of`` is the decision time (default: now, UTC).  ``client`` is an API-Football client with ``get(endpoint,
    params) -> (payload, headers)`` (``RealHistoryClient`` of ``app/scripts/download_history.py``); without it, or
    with ``settings.api_enabled`` off, no request is made.  ``clock`` stamps the fetch times of the calendar and
    statistics (default: the real clock).  ``log`` receives progress messages.
    """
    from model_buk.scanner import fair as fair_module
    from model_buk.scanner import markets, report_html
    from model_buk.scanner.coupons import build_coupons
    from model_buk.scanner.register import ScannerRegister
    from model_buk.scanner.settlement import load_results

    config = config or ScanConfig()
    settings = settings or RunSettings()
    say = log or (lambda text: None)
    decision_time = _utc_stamp(as_of)
    scan_id = default_scan_id(decision_time)
    result = PipelineResult(scan_id, decision_time, config, settings, started_at=pd.Timestamp.now(tz="UTC"))
    api_on = bool(settings.api_enabled) and client is not None
    result.api = {"enabled": api_on, "requests_directory": 0, "requests_details": 0, "requests_halves": 0}
    if settings.api_enabled and client is None:
        result.warnings.append("API off: no API client (no key or offline); the calendar and statistics were not refreshed")

    # 1. calendar ------------------------------------------------------------------------------------------------
    api_reachable = api_on
    if api_on:
        with _timed(result, "directory"):
            try:
                result.directory = refresh_directory(client, settings, decision_time, clock=clock)
                result.api["requests_directory"] = result.directory["requests"]
                if result.directory.get("quota_remaining") is not None:
                    result.api["quota_remaining"] = result.directory["quota_remaining"]
                stopped = result.directory["stopped_reason"]
                if stopped != "completed":
                    result.warnings.append(f"calendar refresh stopped: {stopped}")
                    api_reachable = stopped != "api_error"
                elif result.directory["errors"]:
                    result.warnings.append(f"calendar refresh: {len(result.directory['errors'])} failed day(s)")
            except Exception as exc:  # noqa: BLE001 - the scan runs on the directory as it is
                api_reachable = False
                result.directory = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                result.warnings.append(f"calendar refresh failed ({type(exc).__name__})")
        say(f"directory: {result.directory}")

    # 2. quotes --------------------------------------------------------------------------------------------------
    with _timed(result, "quotes"):
        quotes = markets.load_current_quotes(
            settings.odds_db,
            decision_time,
            horizon_days=config.max_days_ahead,
            include_suspect_ladders=config.include_suspect_ladders,
        )
    result.quotes = quotes
    say(f"quotes: {len(quotes)} rows, {quotes['fixture_id'].nunique() if len(quotes) else 0} fixtures")

    # 3. fair prices ---------------------------------------------------------------------------------------------
    with _timed(result, "fair"):
        fair = fair_module.fair_prices(quotes, as_of=decision_time, **config.fair_kwargs())
    result.fair = fair
    say(f"fair: {len(fair)} legs")

    with _timed(result, "fixtures"):
        fixture_ids = quotes["fixture_id"].dropna().astype(int).unique().tolist() if len(quotes) else []
        fixtures = load_fixture_frame(fixture_ids, settings)
    result.fixtures = fixtures
    known = set(pd.to_numeric(fixtures["fixture_id"]).astype(int)) if len(fixtures) else set()
    unknown = [i for i in fixture_ids if i not in known]
    result.counts["fixtures_without_calendar"] = len(unknown)
    if unknown:
        result.warnings.append(
            f"{len(unknown)} of {len(fixture_ids)} fixtures with odds are in neither the fixture directory nor the "
            "prematch archive: no names and no model for them"
        )

    # 4. models --------------------------------------------------------------------------------------------------
    with _timed(result, "models"):
        model, result.model_info, model_warnings = forecast_models(fixtures, decision_time, config, settings)
        result.warnings += model_warnings
        result.model = model
        if model is not None and len(model) and settings.write_forecast_log:
            try:
                mapped = quotes[quotes["market"] != "other"] if len(quotes) else quotes
                kickoff = dict(zip(pd.to_numeric(fixtures["fixture_id"]).astype(int), fixtures["kickoff_utc"], strict=True))
                result.model_info["rows_logged"] = ForecastLog(settings.forecast_log_path).append(
                    model,
                    fit_as_of=result.model_info.get("fit_as_of"),
                    scan_id=scan_id,
                    as_of=decision_time,
                    quoted=mapped,
                    kickoff=kickoff,
                )
            except (sqlite3.Error, OSError, ValueError) as exc:
                result.warnings.append(f"model forecast log not written ({type(exc).__name__})")
    say(f"models: {result.model_info}")

    # 5. value legs and coupons ----------------------------------------------------------------------------------
    with _timed(result, "evaluate"):
        legs, diag = evaluate_legs_with_diagnostics(quotes, fair, model, config, as_of=decision_time)
        picks = select_value_picks(legs, config)
        coupon_set = build_coupons(picks, config)
        model_only = select_model_only(legs, config)
    scan = ScanResult(scan_id, decision_time, config, legs, picks, coupon_set, model_only, diag)
    result.scan = scan
    result.counts = {**scan_counts(quotes, fair, scan, config), **result.counts}

    # 6. register ------------------------------------------------------------------------------------------------
    register = ScannerRegister(settings.register_path)
    with _timed(result, "record"):
        diagnostics = {
            **diag.to_dict(),
            "n_picks": int(len(picks)),
            "pipeline": _jsonable(
                {
                    "version": PIPELINE_VERSION,
                    "fair_by_source": result.counts["fair_by_source"],
                    "fair_legs": result.counts["fair_legs"],
                    "value_by_tau": result.counts["value_by_tau"],
                    "model": result.model_info,
                    "directory": {k: v for k, v in result.directory.items() if k != "errors"},
                    "api_requests_directory": result.api["requests_directory"],
                }
            ),
        }
        result.recorded = register.record_scan(
            scan_id=scan_id,
            started_at=result.started_at,
            as_of=decision_time,
            config=config,
            legs=legs,
            coupons=coupon_set,
            diagnostics=diagnostics,
        )
        scan.recorded = result.recorded
    if not result.recorded:
        result.warnings.append(f"scan {scan_id} was already in the register: nothing new was recorded")
    say(f"recorded: {result.recorded}")

    # 7. settlement and closing line value -----------------------------------------------------------------------
    if settings.settle:
        with _timed(result, "settle"):
            try:
                _settle(result, register, settings, decision_time, client if api_reachable else None, clock, load_results)
                scan.settled = int(result.settlement.get("settled", 0))
            except Exception as exc:  # noqa: BLE001 - the scan is recorded; settlement is retried next run
                result.warnings.append(f"settlement failed ({type(exc).__name__}: {str(exc)[:120]})")
        with _timed(result, "clv"):
            try:
                need = register.paper_legs_without_clv(decision_time)
                result.settlement["clv_pending"] = int(len(need))
                clv_rows = 0
                if not need.empty:
                    closing_quotes = markets.load_closing_quotes(
                        settings.odds_db,
                        need["fixture_id"].unique().tolist(),
                        include_suspect_ladders=config.include_suspect_ladders,
                    )
                    if len(closing_quotes):
                        closing_fair = fair_module.fair_prices(closing_quotes, **config.closing_fair_kwargs())
                        clv_rows = register.record_clv(
                            closing_fair, taus=config.taus, closing_quotes=closing_quotes, now=decision_time
                        )
                result.settlement["clv_rows"] = int(clv_rows)
                scan.clv_rows = int(clv_rows)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"closing line value failed ({type(exc).__name__}: {str(exc)[:120]})")
    result.api["requests_details"] = int(result.settlement.get("requests_details", 0))
    result.api["requests_halves"] = int(result.settlement.get("requests_halves", 0))
    if api_on:
        try:
            from model_buk.ingestion.fixture_directory import FixtureDirectory

            quota = FixtureDirectory(settings.directory_root).load_quota(pd.Timestamp.now(tz="UTC").to_pydatetime())
            if quota is not None:
                result.api["quota_remaining"] = int(quota)
        except (sqlite3.Error, OSError):
            pass

    # 8. page, CSV, run log --------------------------------------------------------------------------------------
    with _timed(result, "outputs"):
        try:
            summary = register.paper_summary(now=decision_time, taus=config.taus, config_id=config.config_id)
            scan.html = report_html.render_page(scan, fixtures, summary)
            if settings.write_page:
                scan.html_paths = report_html.write_page(scan.html, settings.out_dir, decision_time)
                result.outputs["page"] = str(scan.html_paths[0])
                result.outputs["page_dated"] = str(scan.html_paths[1])
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"page not written ({type(exc).__name__}: {str(exc)[:120]})")
        if settings.write_csv:
            try:
                written, problems = write_csvs(scan, fixtures, config, Path(settings.out_dir))
                result.warnings += problems
                for path in written:
                    result.outputs[path.name if path.parent == Path(settings.out_dir) else f"csv/{path.name}"] = str(path)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"CSV not written ({type(exc).__name__}: {str(exc)[:120]})")
    result.finished_at = pd.Timestamp.now(tz="UTC")
    if settings.write_run_log:
        try:
            append_run_log(settings.run_log_path, result.record())
            result.outputs["run_log"] = str(settings.run_log_path)
        except OSError as exc:
            result.warnings.append(f"run log not written ({type(exc).__name__})")
    return result


def _settle(
    result: PipelineResult,
    register: Any,
    settings: RunSettings,
    now: pd.Timestamp,
    client: Any,
    clock: Callable[[], datetime] | None,
    load_results: Callable[..., pd.DataFrame],
) -> None:
    """Step 7a: fetch missing match statistics (budget-aware) and settle every unsettled leg whose match is over."""
    info: dict[str, Any] = {"pending_legs": 0, "started_fixtures": 0, "settled": 0}
    result.settlement = info
    pending = register.unsettled_legs()
    info["pending_legs"] = int(len(pending))
    if pending.empty:
        return
    kickoff = to_utc(pending["kickoff_utc"])
    started = pending[(kickoff <= now).to_numpy()]
    info["started_fixtures"] = int(started["fixture_id"].nunique())
    if started.empty:
        return
    ids = pd.to_numeric(started["fixture_id"]).astype(int).unique().tolist()
    results = load_results(ids, settings.directory_db, settings.history_db)
    if client is not None:
        details_ids, halves_ids = statistics_needed(started, results, now, settings.stats_min_age_hours)
        info["need_details"] = len(details_ids)
        info["need_halves"] = len(halves_ids)
        if details_ids or halves_ids:
            candidates = sorted(set(details_ids) | set(halves_ids))
            priority: set[int] = set()
            for start in range(0, len(candidates), 500):
                chunk = candidates[start : start + 500]
                marks = ",".join("?" * len(chunk))
                rows = register.read(
                    f"SELECT DISTINCT fixture_id FROM legs WHERE is_value = 1 AND fixture_id IN ({marks}) "
                    f"UNION SELECT DISTINCT fixture_id FROM coupon_legs WHERE fixture_id IN ({marks})",
                    [*chunk, *chunk],
                )
                priority |= set(pd.to_numeric(rows["fixture_id"]).astype(int)) if len(rows) else set()
            info["priority_fixtures"] = len(priority)
            fetched = fetch_settlement_statistics(
                client, settings, now, details_ids, halves_ids, priority=priority, clock=clock
            )
            info.update(fetched)
            if fetched.get("stopped_reason") not in (None, "completed"):
                result.warnings.append(f"statistics fetch stopped: {fetched['stopped_reason']}")
            if fetched["requests_details"] or fetched["requests_halves"]:
                results = load_results(ids, settings.directory_db, settings.history_db)
    info["fixtures_with_result"] = int(len(results))
    info["settled"] = int(register.settle_pending(results, now=now)) if len(results) else 0


__all__ = [
    "PIPELINE_VERSION",
    "ForecastLog",
    "PipelineResult",
    "append_run_log",
    "coupons_table",
    "fetch_settlement_statistics",
    "forecast_models",
    "load_fixture_frame",
    "refresh_directory",
    "run_pipeline",
    "scan_counts",
    "statistics_needed",
    "value_legs_table",
    "write_csvs",
]
