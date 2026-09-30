"""Settlement of canonical legs and coupons, and closing line value.

A leg is identified by ``(fixture_id, market, period, line, selection)`` (see :mod:`model_buk.scanner.engine`).
:func:`settle_leg` turns that identity plus the match statistics into one of five outcomes:

``win`` | ``loss`` | ``push`` | ``half_win`` | ``half_loss``

and a match that was not played voids its legs: ``void`` (:func:`void_decision`).

Quarter lines (``x.25`` / ``x.75``) are two half-stakes on the neighbouring lines, so they can end as
``half_win`` or ``half_loss``.  Integer lines can end as ``push``.

Rules that matter
-----------------
* **Missing stays missing.**  When the statistic a leg needs is absent (no half-time score, no corner count, a
  match that is not finished) the answer is ``None``.  Nothing is guessed and nothing is filled with zero.
* **90 minutes only.**  Bookmaker markets settle on regular time.  :func:`load_match_results` therefore reads a
  score only for matches with status ``FT``; extra time and penalties are left missing, because the archive does
  not store the 90-minute score of such a match.
* **Second half = full time minus half time**, for goals, corners and cards alike.
* **Cards** are counted as yellow plus red cards.  Operators differ (some count a red card as two, some use booking
  points), so card legs are an approximation and the settlement ``source`` says so.
* **Money.**  Every "net" quantity uses the stake-tax multiplier ``tau`` once per bet: a leg with return factor ``f``
  (win ``odds``, half win ``(1 + odds) / 2``, push ``1``, half loss ``0.5``, loss ``0``) has a net profit of
  ``tau * f - 1`` per unit stake.  A bet that is entirely void (a single push, or a coupon of pushes) is treated as
  refunded in full, profit zero.  A coupon multiplies the return factors of its legs (a pushed leg drops out with
  factor one); the tax is charged once on the whole coupon.
* **Void.**  A leg of a match that was postponed, cancelled, abandoned, awarded, a walkover, suspended or
  interrupted (provider statuses ``PST``, ``CANC``, ``ABD``, ``AWD``, ``WO``, ``SUSP``, ``INT``) is ``void``: the
  stake comes back (return factor one, profit zero, like a push, and it is left out of hit rates).  ``CANC``,
  ``ABD``, ``AWD`` and ``WO`` are final and void at once; ``PST``, ``SUSP`` and ``INT`` only once the match is still
  in that state ``grace_hours`` (48) after the kickoff the leg was taken for, which is how bookmakers treat a
  match that is not completed within the next two days.  A postponed match the provider has already moved to a
  new date (status ``NS`` / ``TBD``) is void too once that date is more than ``grace_hours`` after the original
  kickoff and the grace time has passed.  A void leg drops out of a coupon like a push; a coupon whose every leg
  is void is ``void`` itself (stake returned).
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from model_buk.markets.pricing import ClosingLineValue, closing_line_value

OUTCOMES: tuple[str, ...] = ("win", "loss", "push", "half_win", "half_loss", "void")

VOID_STATUSES: frozenset[str] = frozenset({"PST", "CANC", "ABD", "AWD", "WO", "SUSP", "INT"})
"""Provider statuses of a match that was not (or not fully) played: its legs are void (see :func:`void_decision`)."""
FINAL_VOID_STATUSES: frozenset[str] = frozenset({"CANC", "ABD", "AWD", "WO"})
"""Void statuses that are final: the legs are void at once (the others wait for the grace time)."""
RESCHEDULED_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})
DEFAULT_VOID_GRACE_HOURS: float = 48.0

RESULT_FIELDS: tuple[str, ...] = (
    "home_goals",
    "away_goals",
    "home_goals_1h",
    "away_goals_1h",
    "home_corners",
    "away_corners",
    "home_corners_1h",
    "away_corners_1h",
    "home_cards",
    "away_cards",
    "home_cards_1h",
    "away_cards_1h",
)
"""Match statistics :func:`settle_leg` reads from a result (a mapping, a Series or a :class:`MatchResult`)."""

_PERIODS = ("ft", "1h", "2h")
_LINE_MARKETS = frozenset(
    {
        "ah",
        "ou",
        "team_ou_home",
        "team_ou_away",
        "corners_ou",
        "corners_home_ou",
        "corners_away_ou",
        "corners_ah",
        "cards_ou",
        "cards_home_ou",
        "cards_away_ou",
        "cards_ah",
    }
)


@dataclass(frozen=True)
class MatchResult:
    """Match statistics needed to settle legs; every field may be missing (``None``)."""

    home_goals: float | None = None
    away_goals: float | None = None
    home_goals_1h: float | None = None
    away_goals_1h: float | None = None
    home_corners: float | None = None
    away_corners: float | None = None
    home_corners_1h: float | None = None
    away_corners_1h: float | None = None
    home_cards: float | None = None
    away_cards: float | None = None
    home_cards_1h: float | None = None
    away_cards_1h: float | None = None


# --------------------------------------------------------------------------- #
# Leg identity
# --------------------------------------------------------------------------- #


def format_line(line: object) -> str:
    """Canonical text of a line: two decimals, ``"na"`` when the line is missing."""
    number = _number(line)
    return "na" if number is None else f"{number + 0.0:.2f}"  # + 0.0: a negative zero is the line 0.00


def tau_label(tau: float) -> str:
    """Column suffix of a tax regime: ``0.88`` -> ``t088``, ``1.0`` -> ``t100``."""
    return f"t{round(float(tau) * 100):03d}"


def leg_key(fixture_id: object, market: object, period: object, line: object, selection: object) -> str:
    """Stable text identity of a leg: ``fixture|market|period|line|selection``."""
    return f"{int(float(fixture_id))}|{market}|{period}|{format_line(line)}|{selection}"


# --------------------------------------------------------------------------- #
# Statistics access
# --------------------------------------------------------------------------- #


def _number(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` for ``None``, NaN and non-numbers."""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stat(result: Any, name: str) -> float | None:
    if isinstance(result, Mapping) or hasattr(result, "get"):
        raw = result.get(name)
    else:
        raw = getattr(result, name, None)
    number = _number(raw)
    if number is None or number < 0 or number != int(number):
        return None
    return number


def _pair(result: Any, stat: str, period: str) -> tuple[float, float] | None:
    """(home, away) statistic of the period, ``None`` when any needed number is missing."""
    if period == "ft":
        home, away = _stat(result, f"home_{stat}"), _stat(result, f"away_{stat}")
    elif period == "1h":
        home, away = _stat(result, f"home_{stat}_1h"), _stat(result, f"away_{stat}_1h")
    elif period == "2h":
        home_ft, away_ft = _stat(result, f"home_{stat}"), _stat(result, f"away_{stat}")
        home_1h, away_1h = _stat(result, f"home_{stat}_1h"), _stat(result, f"away_{stat}_1h")
        if None in (home_ft, away_ft, home_1h, away_1h):
            return None
        home, away = home_ft - home_1h, away_ft - away_1h  # type: ignore[operator]
        if home < 0 or away < 0:  # inconsistent input: never guess
            return None
    else:
        return None
    if home is None or away is None:
        return None
    return home, away


# --------------------------------------------------------------------------- #
# Line settlement
# --------------------------------------------------------------------------- #


def _sub_lines(line: float) -> tuple[float, ...] | None:
    """The lines a stake is split over: one for whole and half lines, two for quarter lines."""
    quarter = round(line * 4)
    if abs(line * 4 - quarter) > 1e-9:
        return None
    if quarter % 2 == 0:
        return (line,)
    return ((quarter - 1) / 4.0, (quarter + 1) / 4.0)


def _sign_outcome(margin: float) -> int:
    if margin > 1e-12:
        return 1
    if margin < -1e-12:
        return -1
    return 0


def _settle_line(line: float | None, margin_at) -> str | None:
    """Outcome of a bet whose margin at line ``l`` is ``margin_at(l)`` (positive wins)."""
    if line is None:
        return None
    parts = _sub_lines(line)
    if parts is None:
        return None
    score = sum(_sign_outcome(margin_at(part)) for part in parts)
    if len(parts) == 1:
        return {1: "win", 0: "push", -1: "loss"}[score]
    return {2: "win", 1: "half_win", 0: "push", -1: "half_loss", -2: "loss"}[score]


def _yes(condition: bool) -> str:
    return "win" if condition else "loss"


def _settle_three_way(home: float, away: float, selection: str) -> str | None:
    if selection == "home":
        return _yes(home > away)
    if selection == "draw":
        return _yes(home == away)
    if selection == "away":
        return _yes(home < away)
    return None


def _settle_over_under(total: float, line: float | None, selection: str) -> str | None:
    if selection == "over":
        return _settle_line(line, lambda l: total - l)
    if selection == "under":
        return _settle_line(line, lambda l: l - total)
    return None


def _settle_handicap(diff: float, line: float | None, selection: str) -> str | None:
    """``diff`` = home minus away statistic; ``line`` is the handicap applied to the home side."""
    if selection == "home":
        return _settle_line(line, lambda l: diff + l)
    if selection == "away":
        return _settle_line(line, lambda l: -(diff + l))
    return None


def settle_leg(market: str, period: str, line: float | None, selection: str, result: Any) -> str | None:
    """Outcome of one canonical leg, or ``None`` when it cannot be settled from ``result``.

    ``result`` supplies the statistics listed in :data:`RESULT_FIELDS` (a mapping, a pandas Series or a
    :class:`MatchResult`; a missing or NaN value is treated as missing).  ``line`` is a float or ``None``/NaN
    where the market has no line.  For handicaps the line is the handicap applied to the home side, so an away
    selection wins when ``away - home - line`` is positive.
    """
    market = str(market).strip().lower()
    period = str(period).strip().lower()
    selection = str(selection).strip().lower()
    line_value = _number(line)
    if period not in _PERIODS:
        return None
    if market in _LINE_MARKETS and line_value is None:
        return None

    if market in ("1x2", "dc", "dnb", "ah", "ou", "team_ou_home", "team_ou_away", "btts", "cs"):
        pair = _pair(result, "goals", period)
    elif market.startswith("corners_"):
        pair = _pair(result, "corners", period)
    elif market.startswith("cards_"):
        pair = _pair(result, "cards", period)
    else:
        return None
    if pair is None:
        return None
    home, away = pair

    if market in ("1x2", "corners_1x2"):
        return _settle_three_way(home, away, selection)
    if market == "dc":
        if selection == "1x":
            return _yes(home >= away)
        if selection == "x2":
            return _yes(home <= away)
        if selection == "12":
            return _yes(home != away)
        return None
    if market == "dnb":
        if home == away:
            return "push" if selection in ("home", "away") else None
        if selection == "home":
            return _yes(home > away)
        if selection == "away":
            return _yes(home < away)
        return None
    if market in ("ah", "corners_ah", "cards_ah"):
        return _settle_handicap(home - away, line_value, selection)
    if market in ("ou", "corners_ou", "cards_ou"):
        return _settle_over_under(home + away, line_value, selection)
    if market in ("team_ou_home", "corners_home_ou", "cards_home_ou"):
        return _settle_over_under(home, line_value, selection)
    if market in ("team_ou_away", "corners_away_ou", "cards_away_ou"):
        return _settle_over_under(away, line_value, selection)
    if market == "btts":
        if selection == "yes":
            return _yes(home > 0 and away > 0)
        if selection == "no":
            return _yes(not (home > 0 and away > 0))
        return None
    if market == "cs":
        parts = selection.split(":")
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            return None
        return _yes(home == int(parts[0]) and away == int(parts[1]))
    return None


def settle_frame(legs: pd.DataFrame, results: pd.DataFrame) -> pd.Series:
    """Outcome of every row of ``legs`` (columns ``fixture_id, market, period, line, selection``).

    ``results`` has one row per fixture: a ``fixture_id`` column (or index) and any of :data:`RESULT_FIELDS`.
    A fixture without a result row, or a leg that needs a missing statistic, gets ``None``.
    """
    if legs.empty:
        return pd.Series([], dtype=object, index=legs.index)
    table = results
    if "fixture_id" in table.columns:
        table = table.set_index("fixture_id")
    table = table[~table.index.duplicated(keep="last")]
    by_fixture = {int(fid): row for fid, row in table.to_dict(orient="index").items()}
    outcomes: list[str | None] = []
    for fixture_id, market, period, line, selection in zip(
        legs["fixture_id"], legs["market"], legs["period"], legs["line"], legs["selection"], strict=True
    ):
        result = by_fixture.get(int(fixture_id))
        outcomes.append(None if result is None else settle_leg(market, period, line, selection, result))
    return pd.Series(outcomes, index=legs.index, dtype=object)


# --------------------------------------------------------------------------- #
# Returns of legs and coupons
# --------------------------------------------------------------------------- #


def void_decision(
    status: object,
    scheduled_kickoff: object,
    current_kickoff: object,
    now: object,
    grace_hours: float = DEFAULT_VOID_GRACE_HOURS,
) -> str | None:
    """Why a leg is void now (``"void:<STATUS>"``, ``"void:rescheduled"``), or ``None`` when it is not (yet).

    ``scheduled_kickoff`` is the kickoff the leg was taken for, ``current_kickoff`` the one the provider lists now
    (it moves when a postponed match gets a new date).  See the module docstring for the rule.
    """
    code = str(status).strip().upper() if isinstance(status, str) else ""
    scheduled = pd.Timestamp(scheduled_kickoff) if scheduled_kickoff is not None else pd.NaT
    moment = pd.Timestamp(now)
    if not pd.isna(scheduled) and scheduled.tzinfo is None:
        scheduled = scheduled.tz_localize("UTC")
    if moment.tzinfo is None:
        moment = moment.tz_localize("UTC")
    if code in FINAL_VOID_STATUSES:
        return f"void:{code}"
    if pd.isna(scheduled):
        return None
    grace = pd.Timedelta(hours=float(grace_hours))
    if moment < scheduled + grace:
        return None
    if code in VOID_STATUSES:
        return f"void:{code}"
    if code in RESCHEDULED_STATUSES and current_kickoff is not None:
        current = pd.Timestamp(current_kickoff)
        if not pd.isna(current):
            current = current.tz_localize("UTC") if current.tzinfo is None else current
            if current > scheduled + grace:
                return "void:rescheduled"
    return None


def return_factor(outcome: str | None, odd: float) -> float | None:
    """Multiple of the (post-tax) stake returned by one leg; ``None`` for an unknown outcome."""
    if outcome == "win":
        return float(odd)
    if outcome == "half_win":
        return (1.0 + float(odd)) / 2.0
    if outcome in ("push", "void"):
        return 1.0
    if outcome == "half_loss":
        return 0.5
    if outcome == "loss":
        return 0.0
    return None


def single_profit(outcome: str | None, odd: float, tau: float) -> float | None:
    """Net profit per unit stake of a single bet: ``tau * factor - 1``, zero when fully void.

    ``None`` when the outcome is unknown.  See the module docstring for the treatment of pushes.
    """
    factor = return_factor(outcome, odd)
    if factor is None:
        return None
    if outcome in ("push", "void"):
        return 0.0
    return tau * factor - 1.0


@dataclass(frozen=True)
class CouponSettlement:
    """Result of :func:`settle_coupon`.

    ``outcome`` is ``loss`` (a lost leg, factor 0), ``push`` (every leg pushed or void, factor 1), ``void`` (every
    leg void), ``win`` (factor above 1) or ``half_loss`` (a partial loss, factor between 0 and 1).  ``factor``
    multiplies the post-tax stake.
    """

    outcome: str
    factor: float
    n_active: int
    all_void: bool

    def profit(self, tau: float) -> float:
        """Net profit per unit stake at stake-tax multiplier ``tau`` (zero when every leg was void)."""
        if self.all_void:
            return 0.0
        return tau * self.factor - 1.0


def settle_coupon(legs: Sequence[tuple[str | None, float]]) -> CouponSettlement | None:
    """Settle a coupon from ``(outcome, odd)`` per leg.

    A pushed or void leg drops out (factor one), a half win pays ``(1 + odd) / 2`` and a half loss returns half the
    stake; the factors multiply.  One lost leg loses the coupon even if other legs are still open.  Otherwise the
    coupon is ``None`` (unsettled) while any leg has no outcome.
    """
    if not legs:
        raise ValueError("a coupon needs at least one leg")
    refunded = ("push", "void")
    if any(outcome == "loss" for outcome, _ in legs):
        return CouponSettlement("loss", 0.0, sum(1 for o, _ in legs if o not in refunded), False)
    factors: list[float] = []
    for outcome, odd in legs:
        factor = return_factor(outcome, odd)
        if factor is None:
            return None
        factors.append(factor)
    factor = math.prod(factors)
    all_void = all(outcome in refunded for outcome, _ in legs)
    n_active = sum(1 for outcome, _ in legs if outcome not in refunded)
    if all(outcome == "void" for outcome, _ in legs):
        outcome_name = "void"
    elif all_void:
        outcome_name = "push"
    elif factor > 1.0 + 1e-12:
        outcome_name = "win"
    elif factor < 1.0 - 1e-12:
        outcome_name = "half_loss"
    else:
        outcome_name = "push"
    return CouponSettlement(outcome_name, factor, n_active, all_void)


def hit_value(outcome: str | None) -> float | None:
    """Hit weight of an outcome for hit-rate statistics: win 1, half win 0.75, push and void excluded, half loss 0.25."""
    return {"win": 1.0, "half_win": 0.75, "half_loss": 0.25, "loss": 0.0}.get(outcome or "", None)


# --------------------------------------------------------------------------- #
# Closing line value
# --------------------------------------------------------------------------- #


def clv(odd_taken: float, closing_p_fair: float | None, tau: float) -> ClosingLineValue:
    """CLV of a price taken against the de-vigged closing probability.

    ``raw = odd_taken / (1 / p_close) - 1`` and ``net = tau * odd_taken / (1 / p_close) - 1``; all fields are
    ``None`` when the closing probability is missing.  The closing price is used here and nowhere else.
    """
    return closing_line_value(odd_taken, closing_p_fair, tau)


def clv_columns(frame: pd.DataFrame, taus: Sequence[float], *, odd: str = "odd_taken", p_close: str = "closing_p_fair") -> pd.DataFrame:
    """Vectorised CLV: ``clv_raw`` and ``clv_net_t{tau}`` columns, NaN where the closing probability is missing."""
    taken = pd.to_numeric(frame[odd], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(frame[p_close], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(taken) & np.isfinite(close) & (close > 0.0) & (close <= 1.0) & (taken > 1.0)
    out = pd.DataFrame(index=frame.index)
    raw = np.where(valid, taken * close - 1.0, np.nan)
    out["clv_raw"] = raw
    for tau in taus:
        out[f"clv_net_{tau_label(tau)}"] = np.where(valid, tau * taken * close - 1.0, np.nan)
    return out


# --------------------------------------------------------------------------- #
# Results from the history archive
# --------------------------------------------------------------------------- #

DATA_ROOT = Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
HISTORY_DB = DATA_ROOT / "api-football" / "tables" / "history.sqlite"
DIRECTORY_DB = DATA_ROOT / "fixture-directory" / "directory.sqlite3"


def _read_only(path: Path, attempts: int = 3) -> sqlite3.Connection:
    """Read-only connection; the nightly job rebuilds the file, so retry a few times."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
        except sqlite3.Error as exc:  # pragma: no cover - depends on the rebuild timing
            last = exc
            time.sleep(1.0 + attempt)
    raise sqlite3.OperationalError(f"cannot open {path}: {last}")


def load_match_results(fixture_ids: Sequence[int], history_db: str | Path = HISTORY_DB) -> pd.DataFrame:
    """Settlement statistics of finished matches from ``history.sqlite`` (columns of :data:`RESULT_FIELDS`).

    Only matches with status ``FT`` (regular time) are returned.  Goals come from ``matches``, half-time goals
    from ``ht_*`` and corners and cards (yellow plus red) from ``team_match_stats``; a statistic that the archive
    does not hold stays NaN.  Returns an empty frame (no exception) when the database is unavailable.
    """
    ids = sorted({int(x) for x in fixture_ids})
    columns = ["fixture_id", *RESULT_FIELDS]
    if not ids or not Path(history_db).exists():
        return pd.DataFrame(columns=columns)
    try:
        with closing(_read_only(Path(history_db))) as con:
            frames = []
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                matches = pd.read_sql_query(
                    "SELECT fixture_id, home_goals, away_goals, ht_home_goals AS home_goals_1h, "
                    "ht_away_goals AS away_goals_1h FROM matches "
                    f"WHERE status_short = 'FT' AND fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                stats = pd.read_sql_query(
                    "SELECT fixture_id, side, corners, "
                    "CASE WHEN yellow_cards IS NULL AND red_cards IS NULL THEN NULL "
                    "ELSE COALESCE(yellow_cards, 0) + COALESCE(red_cards, 0) END AS cards "
                    f"FROM team_match_stats WHERE fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                if not stats.empty:
                    wide = stats.pivot_table(index="fixture_id", columns="side", values=["corners", "cards"], aggfunc="first", dropna=False)
                    wide.columns = [f"{side}_{stat}" for stat, side in wide.columns]
                    matches = matches.merge(wide.reset_index(), on="fixture_id", how="left")
                frames.append(matches)
    except sqlite3.Error:
        return pd.DataFrame(columns=columns)
    if not frames:
        return pd.DataFrame(columns=columns)
    out = pd.concat(frames, ignore_index=True)
    for name in RESULT_FIELDS:
        if name not in out.columns:
            out[name] = np.nan
    return out[columns]


def load_directory_results(fixture_ids: Sequence[int], directory_db: str | Path = DIRECTORY_DB) -> pd.DataFrame:
    """Settlement statistics from the fixture directory (:mod:`model_buk.ingestion.fixture_directory`).

    The directory's ``home_goals`` / ``away_goals`` are the regular-time (90 minute) score, so matches that
    ended ``AET`` or ``PEN`` are settled on their 90 minutes too.  Half-time goals come from ``ht_*``; corners and
    cards (yellow plus red) per side from ``fixture_stats`` and their first-half values from
    ``fixture_stats_half``.  A statistic the provider did not give stays NaN.  Only finished matches (``FT``,
    ``AET``, ``PEN``) are returned; an unavailable database gives an empty frame.
    """
    ids = sorted({int(x) for x in fixture_ids})
    columns = ["fixture_id", *RESULT_FIELDS]
    if not ids or not Path(directory_db).exists():
        return pd.DataFrame(columns=columns)
    cards = (
        "CASE WHEN yellow_cards IS NULL AND red_cards IS NULL THEN NULL "
        "ELSE COALESCE(yellow_cards, 0) + COALESCE(red_cards, 0) END"
    )
    frames: list[pd.DataFrame] = []
    try:
        with closing(_read_only(Path(directory_db))) as con:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                base = pd.read_sql_query(
                    "SELECT fixture_id, home_goals, away_goals, ht_home_goals AS home_goals_1h, "
                    "ht_away_goals AS away_goals_1h "
                    f"FROM fixtures WHERE status_short IN ('FT', 'AET', 'PEN') AND fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                if base.empty:
                    continue
                full = pd.read_sql_query(
                    f"SELECT fixture_id, side, corners, {cards} AS cards FROM fixture_stats WHERE fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                half = pd.read_sql_query(
                    f"SELECT fixture_id, side, corners, {cards} AS cards FROM fixture_stats_half "
                    f"WHERE half = '1h' AND fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                for table, suffix in ((full, ""), (half, "_1h")):
                    if table.empty:
                        continue
                    wide = table.pivot_table(
                        index="fixture_id", columns="side", values=["corners", "cards"], aggfunc="first", dropna=False
                    )
                    wide.columns = [f"{side}_{stat}{suffix}" for stat, side in wide.columns]
                    base = base.merge(wide.reset_index(), on="fixture_id", how="left")
                frames.append(base)
    except sqlite3.Error:
        return pd.DataFrame(columns=columns)
    if not frames:
        return pd.DataFrame(columns=columns)
    out = pd.concat(frames, ignore_index=True)
    for name in RESULT_FIELDS:
        if name not in out.columns:
            out[name] = np.nan
    return out[columns]


def load_fixture_statuses(fixture_ids: Sequence[int], directory_db: str | Path = DIRECTORY_DB) -> pd.DataFrame:
    """``fixture_id, status_short, kickoff_utc`` of the fixture directory (for :func:`void_decision`).

    An unavailable database gives an empty frame.
    """
    ids = sorted({int(x) for x in fixture_ids})
    columns = ["fixture_id", "status_short", "kickoff_utc"]
    if not ids or not Path(directory_db).exists():
        return pd.DataFrame(columns=columns)
    frames = []
    try:
        with closing(_read_only(Path(directory_db))) as con:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                frames.append(
                    pd.read_sql_query(f"SELECT fixture_id, status_short, kickoff_utc FROM fixtures WHERE fixture_id IN ({marks})", con, params=chunk)
                )
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def load_results(
    fixture_ids: Sequence[int],
    directory_db: str | Path = DIRECTORY_DB,
    history_db: str | Path = HISTORY_DB,
) -> pd.DataFrame:
    """Results for :func:`settle_frame`: the fixture directory first, the history archive for what is missing.

    Cell by cell, a value from the directory wins and a NaN is filled from the history (both come from the same
    provider and both hold the regular-time score).  Fixtures found in neither are absent.
    """
    columns = ["fixture_id", *RESULT_FIELDS]
    directory = load_directory_results(fixture_ids, directory_db)
    history = load_match_results(fixture_ids, history_db)
    if directory.empty and history.empty:
        return pd.DataFrame(columns=columns)
    if history.empty:
        return directory
    if directory.empty:
        return history
    combined = directory.set_index("fixture_id").combine_first(history.set_index("fixture_id"))
    return combined.reset_index()[columns]
