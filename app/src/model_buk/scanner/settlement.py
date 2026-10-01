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
* **Cards of the first half** come from the card events of the match: a card whose minute (``elapsed``) is at most
  45 is a first-half card, first-half stoppage time included (the provider records it as 45 plus ``extra``).  Every
  card event counts one card (a second yellow arrives as a yellow and a red event, 98.7% of team counts agree with
  the statistics block, history 2023-2026); the yellow-card markets count the ``Yellow Card`` and ``Second Yellow
  Card`` events.  Events count only when the log of the match is complete (it holds a substitution in regular
  time); otherwise the count is missing, never zero.  Where the events are missing, the first-half statistics of
  the provider (``fixture_stats_half``) are used for the cards (not for yellow cards alone).
* **Goal order** (first / last team to score) comes from the goal events of the regular 90 minutes (missed
  penalties excluded; an own goal counts for the team the provider lists, which is the team credited: 99.9% of
  history matches add up that way) in the order of ``(elapsed, extra, seq)``.  It is known only when the events add
  up to the 90-minute score of both teams; a goalless match (or half) is ``none`` without events.
* **Half-split markets** (half time / full time, win both halves, to score in both halves, highest scoring half)
  need the half-time score; the second half is the final score minus the half-time score.
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
from model_buk.paths import data_root

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
    "home_yellow",
    "away_yellow",
    "home_yellow_1h",
    "away_yellow_1h",
    "first_goal_side",
    "last_goal_side",
)
"""Match statistics :func:`settle_leg` reads from a result (a mapping, a Series or a :class:`MatchResult`).

``first_goal_side`` / ``last_goal_side`` are 1 (home) or 2 (away): the side of the first and the last goal of the
90 minutes (missing for a goalless match, where the legs settle as ``none`` without them)."""

GOAL_SIDE_HOME: int = 1
GOAL_SIDE_AWAY: int = 2
FIRST_HALF_LAST_MINUTE: int = 45
"""A card or goal event with ``elapsed`` at most 45 belongs to the first half (stoppage time is 45 + ``extra``)."""

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
        "yellow_ou",
        "yellow_home_ou",
        "yellow_away_ou",
        "eh",
    }
)
_GOAL_MARKETS = frozenset(
    {
        "1x2", "dc", "dnb", "ah", "ou", "team_ou_home", "team_ou_away", "btts", "cs",
        "odd_even", "odd_even_home", "odd_even_away", "eh", "win_to_nil_home", "win_to_nil_away",
    }
)  # fmt: skip
"""Markets settled on the goals of their own period."""
_HALF_SPLIT_MARKETS = frozenset(
    {
        "ht_ft", "win_both_halves_home", "win_both_halves_away", "win_either_half_home", "win_either_half_away",
        "score_both_halves", "score_both_halves_home", "score_both_halves_away", "btts_both_halves",
        "highest_half", "highest_half_home", "highest_half_away",
    }
)  # fmt: skip
"""Markets settled on the goals of both halves (period ``ft``)."""
_GOAL_ORDER_MARKETS = frozenset({"first_goal", "last_goal"})


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
    home_yellow: float | None = None
    away_yellow: float | None = None
    home_yellow_1h: float | None = None
    away_yellow_1h: float | None = None
    first_goal_side: float | None = None
    last_goal_side: float | None = None


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


def _result_word(home: float, away: float) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def _yes_no(selection: str, event: bool) -> str | None:
    if selection == "yes":
        return _yes(event)
    if selection == "no":
        return _yes(not event)
    return None


def _settle_half_split(market: str, selection: str, first: tuple[float, float], second: tuple[float, float]) -> str | None:
    """Markets on both halves: ``first`` and ``second`` are the (home, away) goals of each half."""
    h1, a1 = first
    h2, a2 = second
    if market == "ht_ft":
        return _yes(selection == f"{_result_word(h1, a1)}/{_result_word(h1 + h2, a1 + a2)}")
    if market == "win_both_halves_home":
        return _yes_no(selection, h1 > a1 and h2 > a2)
    if market == "win_both_halves_away":
        return _yes_no(selection, a1 > h1 and a2 > h2)
    if market == "win_either_half_home":
        return _yes_no(selection, h1 > a1 or h2 > a2)
    if market == "win_either_half_away":
        return _yes_no(selection, a1 > h1 or a2 > h2)
    if market == "score_both_halves":
        return _yes_no(selection, h1 + a1 > 0 and h2 + a2 > 0)
    if market == "score_both_halves_home":
        return _yes_no(selection, h1 > 0 and h2 > 0)
    if market == "score_both_halves_away":
        return _yes_no(selection, a1 > 0 and a2 > 0)
    if market == "btts_both_halves":
        return _yes_no(selection, h1 > 0 and a1 > 0 and h2 > 0 and a2 > 0)
    if market in ("highest_half", "highest_half_home", "highest_half_away"):
        x, y = {"highest_half": (h1 + a1, h2 + a2), "highest_half_home": (h1, h2), "highest_half_away": (a1, a2)}[market]
        if selection not in ("first_half", "second_half", "equal"):
            return None
        return _yes(selection == ("first_half" if x > y else "second_half" if y > x else "equal"))
    return None


def _goal_side(result: Any, name: str) -> str | None:
    side = _stat(result, name)
    return {GOAL_SIDE_HOME: "home", GOAL_SIDE_AWAY: "away"}.get(int(side)) if side is not None else None


def _settle_goal_order(market: str, period: str, selection: str, result: Any) -> str | None:
    """First / last team to score (``home`` | ``away`` | ``none``) of the match, or the first goal of the first half."""
    if selection not in ("home", "away", "none") or (market, period) not in (("first_goal", "ft"), ("first_goal", "1h"), ("last_goal", "ft")):
        return None
    goals = _pair(result, "goals", period)
    if goals is None:
        return None
    home, away = goals
    if home + away == 0:
        return _yes(selection == "none")
    side = _goal_side(result, "first_goal_side" if market == "first_goal" else "last_goal_side")
    if side is None:
        return None
    full = _pair(result, "goals", "ft")
    if full is not None and ((side == "home" and full[0] == 0) or (side == "away" and full[1] == 0)):
        return None  # the goal order contradicts the score: never guess
    return _yes(selection == side)


def settle_leg(market: str, period: str, line: float | None, selection: str, result: Any) -> str | None:
    """Outcome of one canonical leg, or ``None`` when it cannot be settled from ``result``.

    ``result`` supplies the statistics listed in :data:`RESULT_FIELDS` (a mapping, a pandas Series or a
    :class:`MatchResult`; a missing or NaN value is treated as missing).  ``line`` is a float or ``None``/NaN
    where the market has no line.  For handicaps the line is the handicap applied to the home side, so an away
    selection wins when ``away - home - line`` is positive (European handicaps: three results of ``home + line``
    against ``away``, never a refund).
    """
    market = str(market).strip().lower()
    period = str(period).strip().lower()
    selection = str(selection).strip().lower()
    line_value = _number(line)
    if period not in _PERIODS:
        return None
    if market in _LINE_MARKETS and line_value is None:
        return None
    if market in _HALF_SPLIT_MARKETS:
        if period != "ft":
            return None
        first, second = _pair(result, "goals", "1h"), _pair(result, "goals", "2h")
        return None if first is None or second is None else _settle_half_split(market, selection, first, second)
    if market in _GOAL_ORDER_MARKETS:
        return _settle_goal_order(market, period, selection, result)

    if market in _GOAL_MARKETS:
        pair = _pair(result, "goals", period)
    elif market.startswith("corners_"):
        pair = _pair(result, "corners", period)
    elif market.startswith("cards_"):
        pair = _pair(result, "cards", period)
    elif market.startswith("yellow_"):
        pair = _pair(result, "yellow", period)
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
    if market in ("ou", "corners_ou", "cards_ou", "yellow_ou"):
        return _settle_over_under(home + away, line_value, selection)
    if market in ("team_ou_home", "corners_home_ou", "cards_home_ou", "yellow_home_ou"):
        return _settle_over_under(home, line_value, selection)
    if market in ("team_ou_away", "corners_away_ou", "cards_away_ou", "yellow_away_ou"):
        return _settle_over_under(away, line_value, selection)
    if market == "eh":
        if line_value is None or abs(line_value - round(line_value)) > 1e-9:
            return None
        return _settle_three_way(home + round(line_value), away, selection)
    if market in ("odd_even", "odd_even_home", "odd_even_away"):
        total = {"odd_even": home + away, "odd_even_home": home, "odd_even_away": away}[market]
        if selection not in ("odd", "even"):
            return None
        return _yes((selection == "odd") == (int(total) % 2 == 1))
    if market == "win_to_nil_home":
        return _yes_no(selection, home > away and away == 0)
    if market == "win_to_nil_away":
        return _yes_no(selection, away > home and home == 0)
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
# Statistics from the event log
# --------------------------------------------------------------------------- #

EVENT_FIELDS: tuple[str, ...] = (
    "home_cards_1h",
    "away_cards_1h",
    "home_yellow",
    "away_yellow",
    "home_yellow_1h",
    "away_yellow_1h",
    "first_goal_side",
    "last_goal_side",
)
"""Result fields :func:`event_results` derives from the event log."""
_YELLOW_DETAILS = frozenset({"yellow card", "second yellow card"})


def event_results(events: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    """Settlement statistics from the event log of finished matches (see the module docstring for the rules).

    ``events``: ``fixture_id, seq, elapsed, extra, team_id, type, detail`` (``type`` as the provider spells it:
    ``Goal``, ``Card``, ``subst``; case does not matter).  ``fixtures``: ``fixture_id, home_team_id, away_team_id,
    home_goals, away_goals`` (the 90-minute score).  Returns ``fixture_id`` and :data:`EVENT_FIELDS` for every
    fixture of ``fixtures``, NaN where the log does not tell: cards and yellow cards only from a complete log (a
    substitution in regular time) whose card events all name a team of the match (yellow cards: and a known
    detail), the goal order only when the goal events add up to the score.
    """
    columns = ["fixture_id", *EVENT_FIELDS]
    if fixtures is None or len(fixtures) == 0:
        return pd.DataFrame(columns=columns)
    base = fixtures[["fixture_id", "home_team_id", "away_team_id", "home_goals", "away_goals"]].copy()
    base["fixture_id"] = pd.to_numeric(base["fixture_id"], errors="coerce").astype("Int64")
    base = base.dropna(subset=["fixture_id"]).drop_duplicates("fixture_id").set_index("fixture_id")
    out = pd.DataFrame(index=base.index, columns=list(EVENT_FIELDS), dtype=float)
    if events is None or len(events) == 0:
        return out.reset_index()[columns]
    ev = events.copy()
    ev["fixture_id"] = pd.to_numeric(ev["fixture_id"], errors="coerce").astype("Int64")
    ev = ev[ev["fixture_id"].isin(base.index)]
    ev["elapsed"] = pd.to_numeric(ev["elapsed"], errors="coerce")
    ev["extra"] = pd.to_numeric(ev["extra"], errors="coerce").fillna(0.0)
    ev["seq"] = pd.to_numeric(ev["seq"], errors="coerce")
    ev["kind"] = ev["type"].astype("object").map(lambda v: str(v).strip().lower() if v is not None else "")
    ev["detail_l"] = ev["detail"].astype("object").map(lambda v: str(v).strip().lower() if isinstance(v, str) else "")
    ev = ev[ev["elapsed"].notna() & (ev["elapsed"] <= 90)]  # regular time only
    home_id = ev["fixture_id"].map(base["home_team_id"])
    away_id = ev["fixture_id"].map(base["away_team_id"])
    team = pd.to_numeric(ev["team_id"], errors="coerce")
    ev["side"] = np.where(team.notna() & (team == pd.to_numeric(home_id, errors="coerce")), "home",
                          np.where(team.notna() & (team == pd.to_numeric(away_id, errors="coerce")), "away", ""))  # fmt: skip
    first_half = ev["elapsed"] <= FIRST_HALF_LAST_MINUTE

    complete = set(ev.loc[ev["kind"] == "subst", "fixture_id"].dropna().astype(int))
    cards = ev[ev["kind"] == "card"]
    no_side = set(cards.loc[cards["side"] == "", "fixture_id"].dropna().astype(int))
    unknown_detail = set(cards.loc[cards["detail_l"] == "", "fixture_id"].dropna().astype(int))
    card_ok = [int(f) in complete and int(f) not in no_side for f in out.index]
    yellow_ok = [ok and int(f) not in unknown_detail for ok, f in zip(card_ok, out.index, strict=True)]

    def count(mask: pd.Series, side: str) -> pd.Series:
        return cards[mask & (cards["side"] == side)].groupby("fixture_id").size().reindex(out.index, fill_value=0).astype(float)

    card_1h = first_half.reindex(cards.index)
    is_yellow = cards["detail_l"].isin(_YELLOW_DETAILS)
    for side in ("home", "away"):
        out[f"{side}_cards_1h"] = count(card_1h, side).where(card_ok)
        out[f"{side}_yellow"] = count(is_yellow, side).where(yellow_ok)
        out[f"{side}_yellow_1h"] = count(is_yellow & card_1h, side).where(yellow_ok)

    goals = ev[(ev["kind"] == "goal") & (ev["detail_l"] != "missed penalty")].sort_values(["fixture_id", "elapsed", "extra", "seq"])
    if len(goals):
        grouped = goals.groupby("fixture_id")
        n_home = grouped["side"].agg(lambda v: int((v == "home").sum())).reindex(out.index)
        n_away = grouped["side"].agg(lambda v: int((v == "away").sum())).reindex(out.index)
        n_all = grouped.size().reindex(out.index)
        score_home = pd.to_numeric(base["home_goals"], errors="coerce")
        score_away = pd.to_numeric(base["away_goals"], errors="coerce")
        consistent = (n_home == score_home) & (n_away == score_away) & (n_all == n_home + n_away) & (n_all > 0)
        code = {"home": float(GOAL_SIDE_HOME), "away": float(GOAL_SIDE_AWAY)}
        first = grouped["side"].first().map(code).reindex(out.index)
        last = grouped["side"].last().map(code).reindex(out.index)
        out["first_goal_side"] = first.where(consistent.fillna(False).astype(bool))
        out["last_goal_side"] = last.where(consistent.fillna(False).astype(bool))
    return out.reset_index().rename(columns={"index": "fixture_id"})[columns]


def _has_columns(con: sqlite3.Connection, table: str, columns: Sequence[str]) -> bool:
    """True when ``table`` exists and has every one of ``columns`` (older or test databases may lack them)."""
    present = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    return bool(present) and all(column in present for column in columns)


def _history_events(con: sqlite3.Connection, chunk: Sequence[int]) -> pd.DataFrame:
    marks = ",".join("?" * len(chunk))
    return pd.read_sql_query(
        "SELECT fixture_id, seq, elapsed, extra, team_id, type, detail FROM events "
        f"WHERE type IN ('Goal', 'Card', 'subst') AND fixture_id IN ({marks})",
        con,
        params=list(chunk),
    )


# --------------------------------------------------------------------------- #
# Results from the history archive
# --------------------------------------------------------------------------- #

DATA_ROOT = data_root()  # $MODEL_BUK_DATA, else C:\Users\oskar\Documents\Model-Buk-Data on Windows
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
    from ``ht_*``, corners and cards (yellow plus red) from ``team_match_stats`` and the cards of the first half,
    yellow cards and the goal order from ``events`` (:func:`event_results`); a statistic that the archive does not
    hold stays NaN.  Returns an empty frame (no exception) when the database is unavailable.
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
                teams = ", home_team_id, away_team_id" if _has_columns(con, "matches", ("home_team_id", "away_team_id")) else ""
                matches = pd.read_sql_query(
                    "SELECT fixture_id, home_goals, away_goals, ht_home_goals AS home_goals_1h, "
                    f"ht_away_goals AS away_goals_1h{teams} FROM matches "
                    f"WHERE status_short = 'FT' AND fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                derived = pd.DataFrame(columns=["fixture_id", *EVENT_FIELDS])
                if teams and _has_columns(con, "events", ("fixture_id", "seq", "elapsed", "extra", "team_id", "type", "detail")):
                    derived = event_results(_history_events(con, chunk), matches)
                matches = matches.drop(columns=["home_team_id", "away_team_id"], errors="ignore")
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
                if len(derived):
                    derived["fixture_id"] = derived["fixture_id"].astype("int64")
                    matches = matches.merge(derived, on="fixture_id", how="left")
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
    cards (yellow plus red) per side from ``fixture_stats``; the cards of the first half from the card events
    (``fixture_events``, :func:`event_results`), else from ``fixture_stats_half``; the first-half corners from
    ``fixture_stats_half``; yellow cards and the goal order from the events only.  A statistic the provider did
    not give stays NaN.  Only finished matches (``FT``, ``AET``, ``PEN``) are returned; an unavailable database
    gives an empty frame.
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
                teams = ", home_team_id, away_team_id" if _has_columns(con, "fixtures", ("home_team_id", "away_team_id")) else ""
                base = pd.read_sql_query(
                    "SELECT fixture_id, home_goals, away_goals, ht_home_goals AS home_goals_1h, "
                    f"ht_away_goals AS away_goals_1h{teams} "
                    f"FROM fixtures WHERE status_short IN ('FT', 'AET', 'PEN') AND fixture_id IN ({marks})",
                    con,
                    params=chunk,
                )
                if base.empty:
                    continue
                derived = pd.DataFrame(columns=["fixture_id", *EVENT_FIELDS])
                if teams and _has_columns(con, "fixture_events", ("fixture_id", "seq", "elapsed", "extra", "team_id", "type", "detail")):
                    events = pd.read_sql_query(
                        "SELECT fixture_id, seq, elapsed, extra, team_id, type, detail FROM fixture_events "
                        f"WHERE fixture_id IN ({marks})",
                        con,
                        params=chunk,
                    )
                    derived = event_results(events, base)
                base = base.drop(columns=["home_team_id", "away_team_id"], errors="ignore")
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
                if len(derived):
                    derived = derived.set_index(derived["fixture_id"].astype("int64")).drop(columns="fixture_id")
                    base = base.set_index(base["fixture_id"].astype("int64"), drop=False)
                    for column in EVENT_FIELDS:
                        from_events = pd.to_numeric(derived[column], errors="coerce").reindex(base.index)
                        if column in base.columns:  # the events win, the half statistics fill what they leave missing
                            base[column] = from_events.where(from_events.notna(), pd.to_numeric(base[column], errors="coerce"))
                        else:
                            base[column] = from_events
                    base = base.reset_index(drop=True)
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
