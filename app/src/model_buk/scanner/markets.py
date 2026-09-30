"""Market dictionary and quote loading for the scanner: API-Football bet types -> canonical legs.

The odds archive (:mod:`model_buk.ingestion.api_football_odds_archive`) stores what the provider showed: a bet
type id (``bet_id``), its name and a free-text selection ("Home -0.25", "Over 2.0", "Draw/Home", "1:0").  The
scanner works on *legs* identified by ``(fixture_id, market, period, line, selection)``.  This module turns the
first into the second (:func:`canonicalize`) and loads point-in-time quote frames from the archive
(:func:`load_current_quotes`, :func:`load_closing_quotes`).

Canonical legs
--------------
``period`` is ``ft`` | ``1h`` | ``2h``.  Markets and selections::

    1x2 home|draw|away        dc 1x|x2|12            dnb home|away
    ah  home|away             ou over|under         team_ou_home / team_ou_away over|under
    btts yes|no               cs 'h:a'
    corners_ou / corners_home_ou / corners_away_ou over|under      corners_ah home|away   corners_1x2 home|draw|away
    cards_ou / cards_home_ou / cards_away_ou over|under            cards_ah home|away

Everything else is ``market == "other"`` (``period == "na"``, ``line`` NaN, the raw selection kept, ``bet_id`` and
``bet`` untouched) and is never priced.  ``line`` is a float and NaN when a market has none.

Asian handicap sign convention (measured, differs from the task text)
---------------------------------------------------------------------
The provider labels **both** sides of an Asian handicap line with the handicap of the HOME team: "Home -0.5" and
"Away -0.5" are the two sides of one line (the home team gives half a goal), "Away +0.5" belongs to the line
``+0.5``.  The canonical ``line`` is therefore the number in the selection string for both sides, and the
selection is ``home`` or ``away``.  This was verified on the live archive, on every handicap bet type (full time,
halves, corners, cards): pairing ``Home s`` with ``Away s`` gives an overround of 1.02 to 1.20 (never below 1.0),
pairing ``Home s`` with ``Away -s`` gives values down to 0.15; and the de-vigged ``Home -0.5`` equals the
de-vigged 1X2 home probability (mean absolute difference 0.010), ``Home +0.5`` equals home plus draw and
``Home +0`` equals the draw-no-bet price.  Settling a canonical ``ah`` leg is then ``home_goals + line`` against
``away_goals`` for the home selection and the reverse for the away selection.

A few fixtures (2 of about 450 on 2026-09-30, all bookmakers at once, all periods) arrive with the sign of the
whole ladder reversed: the price of "Home +0.5" behaves like the home team giving half a goal, and the prices of
one bookmaker *rise* with the label instead of falling.  :func:`orient_handicaps` detects a reversed fixture (two
ladders of four or more lines whose home prices are rank-correlated at least +0.8 with the label, or one of five
or more lines at +0.95, and no ladder of the same fixture and market that says the opposite) and negates the
``line`` of every handicap row of that fixture and market; ``handicap_flipped`` marks them.  Ladders too short to
tell are left as labelled unless the fixture is flipped as a whole.

Equivalences that are mapped on purpose
---------------------------------------
* Clean sheet home (bet 27) yes/no = ``team_ou_away`` under/over 0.5; clean sheet away (28) likewise for
  ``team_ou_home``.  "Home Team Score a Goal" (43, halves 114/115) yes/no = ``team_ou_home`` over/under 0.5, the
  away versions (44, 116/117) ``team_ou_away``.
* Three-way totals ("Over N / Exactly N / Under N" with an integer ``N``, bets 85 and 77 at some bookmakers):
  the ``Over N`` leg wins on ``total >= N + 1`` and so equals the two-way leg ``Over N + 0.5``; ``Under N``
  equals ``Under N - 0.5``.  These rows are marked ``three_way = True`` and their ``Exactly N`` rows stay
  ``other``.  A group counts as three-way when the bet is 85 or the same (fixture, bookmaker, bet) also
  quotes an ``Exactly`` selection.

Deliberately not mapped (kept ``other``)
----------------------------------------
Yellow-card markets (bets 150-162: yellow cards only, a different statistic from "cards"), shots, offsides, fouls,
odd/even, exact-goals numbers, European (three-way) handicaps, half-time/full-time doubles, combined markets
(result and total, result and BTTS), player and time-window markets.  "Cards" (bets 80-83) are mapped; the
settlement side counts yellow plus red cards, operators differ on red cards.

Broken ladders
--------------
Some bookmakers' handicap and total ladders in the feed are not ladders: every line carries the balanced price
pair of the moment it was the main line (Bet365 ``Home -1 2.02 / Away -1 1.78`` next to ``Home 0 1.78 / Away 0
2.02``).  Against Pinnacle they are flat, so the extreme lines show an edge of 15-25% on one side that is not
real.  :func:`flag_incoherent_ladders` regresses the bookmaker's de-vigged line probabilities on Pinnacle's over
the common lines.  A healthy ladder has slope 0.85 to 1.15 (95% of the 4 394 assessable ladders of the board of
2026-09-30 16:10 UTC, all bookmakers); the broken ones (40 ladders, 0.9%, mostly Bet365 and Betano, most of them far
below 0.5) lie below 0.7.  The cut is not a clean gap: 8 ladders (BetVictor totals, Betano) sit between 0.70 and
0.75 and are kept, 5 between 0.60 and 0.70 are dropped.  Ladders with at least three common lines that span at
least 0.10 of probability and a slope below 0.7 are ``ladder_suspect``; the loaders drop them unless
``include_suspect_ladders=True``.  Ladders of fixtures without a Pinnacle ladder, and ladders of one or two
lines, cannot be checked.  A ladder that is merely shifted against Pinnacle (a healthy slope at a level 0.03-0.05
away, e.g. Bet365 handicaps on 2026-09-30) is *not* detected here; that is a real disagreement between the books
and the engine's book-shift guard is the place that judges it.

Quote frames
------------
:func:`load_current_quotes` and :func:`load_closing_quotes` return the columns ``fixture_id, league_id,
kickoff_utc, bookmaker, bet_id, market, period, line, selection, odd, fetched_at`` plus ``bookmaker_id, bet,
raw_selection, three_way, handicap_flipped, ladder_slope, ladder_suspect, changed_at, age_minutes, api_update,
provider_age_minutes``.  The archive stores price *changes*; ``fetched_at`` is when the bookmaker's board for the
fixture was **last seen** (a scan of the archive), not when the price last moved, so ``age_minutes`` (reference
time minus ``fetched_at``) is the staleness of the quote *as we saw it*.  ``changed_at`` is the time of the last
change of that price.  Prices <= 1.0 (suspended selections) are dropped: they are not prices.  Several raw
selections can map to one canonical leg of one bookmaker (Bet365 quotes total goals in two bet types); the
highest price is kept, it is the same event.

Provider staleness (measured, not obvious)
------------------------------------------
``age_minutes`` says nothing about how old the price is at the provider.  Every board the feed returns carries the
provider's own ``update`` time (``api_update``, one value per fixture and scan, shared by all bookmakers) and on
the 13 503 board sightings of 2026-09-28..30 it lags our fetch by a median of 3.8 hours (quartiles 2.9 and 5.9,
95th percentile 15 hours, 99% of the sightings more than one hour), in the near-kickoff scans too (median 3.4).
``provider_age_minutes`` (reference time minus ``api_update``; NaN when unknown) exposes it.  It is the
provider's own statement of how old the board is (whether ``update`` is the last refresh or the last change of the
odds is not documented; the smallest lag of the last hourly scan was 3.1 hours for every fixture, which looks like
a refresh cycle).  ``fetched_at`` stays the (conservative) time the price became available to us, and it is what
every point-in-time cut and the default age filters use.  Consequences the caller must keep in mind: a "current"
board is a provider snapshot from a few hours ago, and the "closing" quote of a fixture is the provider's snapshot
of about ``provider_age_minutes`` before the kickoff, not a price from the last minutes.

Both loaders read the archive in one read transaction, so a scan that the collector commits while a load is
running is either fully in or fully out of the result.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from urllib.parse import quote

import numpy as np
import pandas as pd

from model_buk.markets.pricing import normalise_bookmaker_name

MARKET_OTHER: Final = "other"
PERIOD_NA: Final = "na"
PERIODS: Final = ("ft", "1h", "2h")

CANONICAL_MARKETS: Final[tuple[str, ...]] = (
    "1x2",
    "dc",
    "dnb",
    "ah",
    "ou",
    "team_ou_home",
    "team_ou_away",
    "btts",
    "cs",
    "corners_ou",
    "corners_home_ou",
    "corners_away_ou",
    "corners_ah",
    "corners_1x2",
    "cards_ou",
    "cards_home_ou",
    "cards_away_ou",
    "cards_ah",
)

SELECTIONS: Final[dict[str, tuple[str, ...]]] = {
    "1x2": ("home", "draw", "away"),
    "dc": ("1x", "x2", "12"),
    "dnb": ("home", "away"),
    "ah": ("home", "away"),
    "ou": ("over", "under"),
    "team_ou_home": ("over", "under"),
    "team_ou_away": ("over", "under"),
    "btts": ("yes", "no"),
    "corners_ou": ("over", "under"),
    "corners_home_ou": ("over", "under"),
    "corners_away_ou": ("over", "under"),
    "corners_ah": ("home", "away"),
    "corners_1x2": ("home", "draw", "away"),
    "cards_ou": ("over", "under"),
    "cards_home_ou": ("over", "under"),
    "cards_away_ou": ("over", "under"),
    "cards_ah": ("home", "away"),
}
"""Complementary selections of one line of a market (``cs`` has an open-ended list of ``h:a``)."""

NO_LINE_MARKETS: Final[frozenset[str]] = frozenset({"1x2", "dc", "dnb", "btts", "cs", "corners_1x2"})
"""Markets without a line: the canonical ``line`` is NaN."""

LEG_KEY: Final[tuple[str, ...]] = ("fixture_id", "market", "period", "line", "selection")

QUOTE_COLUMNS: Final[tuple[str, ...]] = (
    "fixture_id",
    "league_id",
    "kickoff_utc",
    "bookmaker",
    "bet_id",
    "market",
    "period",
    "line",
    "selection",
    "odd",
    "fetched_at",
)
"""The quotes-frame contract; loaders add :data:`EXTRA_QUOTE_COLUMNS`."""

EXTRA_QUOTE_COLUMNS: Final[tuple[str, ...]] = (
    "bookmaker_id",
    "bet",
    "raw_selection",
    "three_way",
    "handicap_flipped",
    "ladder_slope",
    "ladder_suspect",
    "changed_at",
    "age_minutes",
    "api_update",
    "provider_age_minutes",
)

_NAN: Final = float("nan")

# --------------------------------------------------------------------------- #
# Selection parsers: text -> (line, canonical selection), or None when it does not parse
# --------------------------------------------------------------------------- #

_Parsed = tuple[float, str]

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_HANDICAP_RE = re.compile(rf"^(Home|Away)\s+({_NUMBER})$", re.IGNORECASE)
_TOTAL_RE = re.compile(rf"^(Over|Under)\s+({_NUMBER})$", re.IGNORECASE)
_SCORE_RE = re.compile(r"^(\d+)\s*:\s*(\d+)$")
_TEAMS = {"home": "home", "draw": "draw", "away": "away"}
_DOUBLE_CHANCE = {
    frozenset(("home", "draw")): "1x",
    frozenset(("draw", "away")): "x2",
    frozenset(("home", "away")): "12",
}


def _quarter_line(text: str, *, non_negative: bool) -> float | None:
    """A line in quarter steps as a float (``-0.0`` normalised to ``0.0``), or None if it is not one."""
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or abs(value * 4.0 - round(value * 4.0)) > 1e-9:
        return None
    if non_negative and value < 0.0:
        return None
    return value + 0.0


def _parse_three_way(selection: str) -> _Parsed | None:
    key = selection.strip().lower()
    return (_NAN, _TEAMS[key]) if key in _TEAMS else None


def _parse_two_way_teams(selection: str) -> _Parsed | None:
    key = selection.strip().lower()
    return (_NAN, key) if key in ("home", "away") else None


def _parse_double_chance(selection: str) -> _Parsed | None:
    parts = [part.strip().lower() for part in selection.split("/")]
    if len(parts) != 2 or any(part not in _TEAMS for part in parts):
        return None
    canonical = _DOUBLE_CHANCE.get(frozenset(parts))
    return (_NAN, canonical) if canonical is not None and len(set(parts)) == 2 else None


def _parse_handicap(selection: str) -> _Parsed | None:
    match = _HANDICAP_RE.match(selection.strip())
    if match is None:
        return None
    line = _quarter_line(match.group(2), non_negative=False)
    return None if line is None else (line, match.group(1).lower())


def _parse_total(selection: str) -> _Parsed | None:
    match = _TOTAL_RE.match(selection.strip())
    if match is None:
        return None
    line = _quarter_line(match.group(2), non_negative=True)
    return None if line is None else (line, match.group(1).lower())


def _parse_yes_no(selection: str) -> _Parsed | None:
    key = selection.strip().lower()
    return (_NAN, key) if key in ("yes", "no") else None


def _parse_score(selection: str) -> _Parsed | None:
    match = _SCORE_RE.match(selection.strip())
    return None if match is None else (_NAN, f"{int(match.group(1))}:{int(match.group(2))}")


def _parse_scores_a_goal(selection: str) -> _Parsed | None:
    """"Team scores a goal": yes = over 0.5 team goals, no = under 0.5."""
    key = selection.strip().lower()
    return (0.5, "over" if key == "yes" else "under") if key in ("yes", "no") else None


def _parse_keeps_clean_sheet(selection: str) -> _Parsed | None:
    """"Team keeps a clean sheet": yes = the opponent scores under 0.5, no = over 0.5."""
    key = selection.strip().lower()
    return (0.5, "under" if key == "yes" else "over") if key in ("yes", "no") else None


_PARSERS: Final[dict[str, Callable[[str], _Parsed | None]]] = {
    "three_way": _parse_three_way,
    "two_way_teams": _parse_two_way_teams,
    "double_chance": _parse_double_chance,
    "handicap": _parse_handicap,
    "total": _parse_total,
    "yes_no": _parse_yes_no,
    "score": _parse_score,
    "scores_a_goal": _parse_scores_a_goal,
    "keeps_clean_sheet": _parse_keeps_clean_sheet,
}


@dataclass(frozen=True)
class BetSpec:
    """How one API-Football bet type maps to a canonical market."""

    market: str
    period: str
    parser: str


def _specs() -> dict[int, BetSpec]:
    table: dict[int, BetSpec] = {}

    def add(bet_ids: Iterable[int], market: str, period: str, parser: str) -> None:
        for bet_id in bet_ids:
            table[bet_id] = BetSpec(market, period, parser)

    add((1,), "1x2", "ft", "three_way")
    add((13,), "1x2", "1h", "three_way")
    add((3,), "1x2", "2h", "three_way")
    add((2,), "dnb", "ft", "two_way_teams")
    add((109,), "dnb", "1h", "two_way_teams")
    add((182,), "dnb", "2h", "two_way_teams")
    add((12,), "dc", "ft", "double_chance")
    add((20,), "dc", "1h", "double_chance")
    add((33,), "dc", "2h", "double_chance")
    add((4,), "ah", "ft", "handicap")
    add((19,), "ah", "1h", "handicap")
    add((104,), "ah", "2h", "handicap")
    add((5, 50), "ou", "ft", "total")  # 50 = "Goal Line", Bet365's asian totals
    add((6, 72), "ou", "1h", "total")
    add((26,), "ou", "2h", "total")
    add((16,), "team_ou_home", "ft", "total")
    add((105,), "team_ou_home", "1h", "total")
    add((107,), "team_ou_home", "2h", "total")
    add((17,), "team_ou_away", "ft", "total")
    add((106,), "team_ou_away", "1h", "total")
    add((108,), "team_ou_away", "2h", "total")
    add((8,), "btts", "ft", "yes_no")
    add((34,), "btts", "1h", "yes_no")
    add((35,), "btts", "2h", "yes_no")
    add((10,), "cs", "ft", "score")
    add((31,), "cs", "1h", "score")
    add((62,), "cs", "2h", "score")
    add((45, 85), "corners_ou", "ft", "total")
    add((77,), "corners_ou", "1h", "total")
    add((127,), "corners_ou", "2h", "total")
    add((57,), "corners_home_ou", "ft", "total")
    add((132,), "corners_home_ou", "1h", "total")
    add((133,), "corners_home_ou", "2h", "total")
    add((58,), "corners_away_ou", "ft", "total")
    add((134,), "corners_away_ou", "1h", "total")
    add((135,), "corners_away_ou", "2h", "total")
    add((56,), "corners_ah", "ft", "handicap")
    add((125,), "corners_ah", "1h", "handicap")
    add((126,), "corners_ah", "2h", "handicap")
    add((55,), "corners_1x2", "ft", "three_way")
    add((130,), "corners_1x2", "1h", "three_way")
    add((131,), "corners_1x2", "2h", "three_way")
    add((80,), "cards_ou", "ft", "total")
    add((81,), "cards_ah", "ft", "handicap")
    add((82,), "cards_home_ou", "ft", "total")
    add((83,), "cards_away_ou", "ft", "total")
    # Equivalent formulations of an over/under 0.5 team total.
    add((27,), "team_ou_away", "ft", "keeps_clean_sheet")  # clean sheet of the home team
    add((28,), "team_ou_home", "ft", "keeps_clean_sheet")
    add((43,), "team_ou_home", "ft", "scores_a_goal")
    add((44,), "team_ou_away", "ft", "scores_a_goal")
    add((114,), "team_ou_home", "1h", "scores_a_goal")
    add((115,), "team_ou_home", "2h", "scores_a_goal")
    add((116,), "team_ou_away", "1h", "scores_a_goal")
    add((117,), "team_ou_away", "2h", "scores_a_goal")
    return table


BET_SPECS: Final[dict[int, BetSpec]] = _specs()
"""Bet type id -> canonical market, period and selection parser (every other bet type is ``other``)."""

THREE_WAY_TOTAL_BETS: Final[frozenset[int]] = frozenset({85})
"""Bet types that are always "Over N / Exactly N / Under N" at integer N."""


def map_bet(bet_id: int, selection: str) -> tuple[str, str, float, str] | None:
    """Canonical ``(market, period, line, selection)`` of one raw quote, or None if it is not a canonical leg.

    Three-way totals are *not* resolved here (they need the other selections of the same board); see
    :func:`canonicalize`.
    """
    try:
        spec = BET_SPECS.get(int(bet_id))
    except (TypeError, ValueError):
        return None
    if spec is None or selection is None:
        return None
    parsed = _PARSERS[spec.parser](str(selection))
    if parsed is None:
        return None
    line, canonical = parsed
    if spec.market in NO_LINE_MARKETS:
        line = _NAN
    return spec.market, spec.period, line, canonical


def _map_unique(pairs: pd.DataFrame) -> pd.DataFrame:
    """Map the distinct (bet_id, raw_selection) pairs; unmapped ones become ``other``."""
    rows = []
    for bet_id, raw in zip(pairs["bet_id"].tolist(), pairs["raw_selection"].tolist(), strict=True):
        mapped = map_bet(bet_id, raw)
        if mapped is None:
            rows.append((bet_id, raw, MARKET_OTHER, PERIOD_NA, _NAN, raw))
        else:
            rows.append((bet_id, raw, *mapped))
    return pd.DataFrame(rows, columns=["bet_id", "raw_selection", "market", "period", "line", "selection"])


def canonicalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Add the canonical leg columns to raw archive quotes.

    ``raw`` needs ``bet_id`` and ``selection`` (the provider's text); ``fixture_id`` and ``bookmaker_id`` (or
    ``bookmaker``) let the three-way totals be recognised.  The result is ``raw`` with ``selection`` replaced by
    the canonical selection and the new columns ``market``, ``period``, ``line``, ``raw_selection`` and
    ``three_way``; row order and every other column are kept.  Rows that are not canonical legs are
    ``market == "other"``, ``period == "na"``, ``line`` NaN and keep the raw selection.
    """
    for column in ("bet_id", "selection"):
        if column not in raw.columns:
            raise ValueError(f"raw quotes need a {column!r} column")
    frame = raw.copy()
    frame["raw_selection"] = frame["selection"].astype("object").map(lambda v: "" if v is None else str(v))
    frame["bet_id"] = pd.to_numeric(frame["bet_id"], errors="coerce").astype("Int64")
    frame = frame.drop(columns=["selection"])
    pairs = frame[["bet_id", "raw_selection"]].drop_duplicates()
    out = frame.merge(_map_unique(pairs), on=["bet_id", "raw_selection"], how="left")
    out["three_way"] = _resolve_three_way(out)
    shifted = out["three_way"].to_numpy(dtype=bool)
    if shifted.any():
        line = out["line"].to_numpy(dtype=float).copy()
        over = (out["selection"] == "over").to_numpy(dtype=bool)
        line[shifted & over] += 0.5
        line[shifted & ~over] -= 0.5
        out["line"] = line
    return out


def _resolve_three_way(frame: pd.DataFrame) -> np.ndarray:
    """Rows that are an integer-line Over/Under of a three-way total (see the module docstring)."""
    is_total = (frame["market"].isin(("corners_ou", "cards_ou", "ou"))) & frame["selection"].isin(("over", "under"))
    line = frame["line"].to_numpy(dtype=float)
    integer_line = np.isfinite(line) & (np.abs(line - np.round(line)) < 1e-9)
    exactly = frame["raw_selection"].str.match(r"^\s*Exactly\b", case=False).to_numpy()
    bet = frame["bet_id"].to_numpy(dtype=float, na_value=np.nan)
    board = [column for column in ("fixture_id", "bookmaker_id", "bookmaker") if column in frame.columns][:2]
    board_has_exactly = np.zeros(len(frame), dtype=bool)
    if "fixture_id" in board and len(board) == 2 and exactly.any():
        key = frame[[*board, "bet_id"]].copy()
        key["_exactly"] = exactly
        board_has_exactly = key.groupby([*board, "bet_id"], dropna=False)["_exactly"].transform("any").to_numpy(dtype=bool)
    always = np.isin(bet, sorted(THREE_WAY_TOTAL_BETS))
    return is_total.to_numpy() & integer_line & (board_has_exactly | always)


HANDICAP_MARKETS: Final[tuple[str, ...]] = ("ah", "corners_ah", "cards_ah")


def orient_handicaps(quotes: pd.DataFrame, *, min_lines: int = 4, min_rank_correlation: float = 0.8) -> pd.DataFrame:
    """Correct fixtures whose handicap ladders carry the opposite sign (see the module docstring).

    Evidence: for every ladder (fixture, bookmaker, market, period) with at least ``min_lines`` lines, the rank
    correlation of the home price with the line.  A correct ladder has home prices that fall as the line rises
    (a bigger head start is cheaper); a reversed one has them rise.  A (fixture, market) is flipped when no ladder
    is clearly correct and either two ladders are clearly reversed or a single one is beyond doubt (rank
    correlation at least 0.95 over at least five lines): ``line`` is then negated on all its handicap rows.
    Returns a copy with the boolean column ``handicap_flipped``.
    """
    out = quotes.copy()
    out["handicap_flipped"] = False
    home = out[out["market"].isin(HANDICAP_MARKETS) & (out["selection"] == "home")]
    if home.empty:
        return out
    ladder_key = ["fixture_id", "bookmaker_id" if "bookmaker_id" in out.columns else "bookmaker", "market", "period"]
    # per (fixture, market): [reversed ladders, correct ladders, ladders beyond doubt]
    evidence: dict[tuple[int, str], list[int]] = {}
    for (fixture_id, _book, market, _period), ladder in home.groupby(ladder_key, sort=False):
        ladder = ladder.drop_duplicates("line")
        if len(ladder) < min_lines:
            continue
        rho = ladder["line"].corr(ladder["odd"], method="spearman")
        if not np.isfinite(rho):
            continue
        votes = evidence.setdefault((int(fixture_id), market), [0, 0, 0])
        if rho >= min_rank_correlation:
            votes[0] += 1
            votes[2] += int(rho >= 0.95 and len(ladder) >= 5)
        elif rho <= -min_rank_correlation:
            votes[1] += 1
    flipped = {key for key, (reversed_n, correct_n, sure_n) in evidence.items() if correct_n == 0 and (reversed_n >= 2 or sure_n >= 1)}
    if flipped:
        keys = pd.MultiIndex.from_arrays([out["fixture_id"], out["market"]])
        mask = keys.isin(list(flipped))
        out.loc[mask, "line"] = -out.loc[mask, "line"].to_numpy(dtype=float) + 0.0
        out.loc[mask, "handicap_flipped"] = True
    return out


LADDER_MARKETS: Final[dict[str, tuple[str, str]]] = {
    market: (selections[0], selections[1])
    for market, selections in SELECTIONS.items()
    if len(selections) == 2 and market not in ("dnb", "btts")
}
"""Two-way markets that come as a ladder of lines, with the two selections of one line."""


def flag_incoherent_ladders(
    quotes: pd.DataFrame,
    *,
    min_lines: int = 3,
    min_range: float = 0.10,
    min_slope: float = 0.7,
) -> pd.DataFrame:
    """Add ``ladder_slope`` and ``ladder_suspect`` (see "Broken ladders" in the module docstring).

    For every non-Pinnacle ladder (fixture, bookmaker, market, period) the line probabilities of the two-way pairs
    (proportional de-vig of the pair's prices) are regressed on Pinnacle's over the lines both quote.  The slope is
    reported on every row of a ladder that could be assessed (at least ``min_lines`` common lines whose Pinnacle
    probabilities span ``min_range``); NaN elsewhere.  A slope below ``min_slope`` marks the ladder suspect.
    """
    out = quotes.copy()
    out["ladder_slope"] = np.nan
    out["ladder_suspect"] = False
    book = "bookmaker_id" if "bookmaker_id" in out.columns else "bookmaker"
    usable = out[out["market"].isin(list(LADDER_MARKETS)) & ~out["three_way"].astype(bool) & (out["odd"] > 1.0)]
    if usable.empty:
        return out
    pairs = []
    for market, (first, second) in LADDER_MARKETS.items():
        sub = usable[usable["market"] == market]
        if sub.empty:
            continue
        wide = sub.pivot_table(index=["fixture_id", book, "period", "line"], columns="selection", values="odd", aggfunc="max")
        if first not in wide.columns or second not in wide.columns:
            continue
        wide = wide[[first, second]].dropna()
        inverse = 1.0 / wide
        frame = wide.index.to_frame(index=False)
        frame["market"] = market
        frame["q"] = (inverse[first] / (inverse[first] + inverse[second])).to_numpy()
        pairs.append(frame)
    if not pairs:
        return out
    table = pd.concat(pairs, ignore_index=True)
    sharp_ids = set(out.loc[out["bookmaker"].map(normalise_bookmaker_name) == "pinnacle", book])
    is_sharp = table[book].isin(sharp_ids)
    sharp = table[is_sharp].rename(columns={"q": "q_sharp"}).drop(columns=book)
    soft = table[~is_sharp].merge(sharp, on=["fixture_id", "market", "period", "line"], how="inner")
    if soft.empty:
        return out
    group = ["fixture_id", book, "market", "period"]
    soft["xy"], soft["xx"] = soft["q"] * soft["q_sharp"], soft["q_sharp"] ** 2
    stats = soft.groupby(group).agg(n=("q", "size"), sx=("q_sharp", "sum"), sy=("q", "sum"), sxy=("xy", "sum"), sxx=("xx", "sum"),
                                    lo=("q_sharp", "min"), hi=("q_sharp", "max")).reset_index()
    stats = stats[(stats["n"] >= min_lines) & ((stats["hi"] - stats["lo"]) >= min_range)]
    if stats.empty:
        return out
    denominator = stats["n"] * stats["sxx"] - stats["sx"] ** 2
    stats["ladder_slope"] = (stats["n"] * stats["sxy"] - stats["sx"] * stats["sy"]) / denominator.where(denominator > 0)
    stats["ladder_suspect_new"] = stats["ladder_slope"] < min_slope
    merged = out[group].merge(stats[[*group, "ladder_slope", "ladder_suspect_new"]], on=group, how="left")
    out["ladder_slope"] = merged["ladder_slope"].to_numpy()
    out["ladder_suspect"] = merged["ladder_suspect_new"].fillna(False).to_numpy(dtype=bool)
    return out


def dedupe_legs(quotes: pd.DataFrame) -> pd.DataFrame:
    """One row per (fixture, bookmaker, leg): the highest price wins (the same event quoted in two bet types)."""
    bookmaker = "bookmaker_id" if "bookmaker_id" in quotes.columns else "bookmaker"
    key = ["fixture_id", bookmaker, "market", "period", "selection", "_line_key"]
    frame = quotes.copy()
    frame["_line_key"] = frame["line"].round(4).fillna(-9999.0)
    is_leg = (frame["market"] != MARKET_OTHER).to_numpy(dtype=bool)
    legs = frame[is_leg].sort_values("odd", kind="mergesort")
    legs = legs[~legs.duplicated(key, keep="last")]
    return pd.concat([legs, frame[~is_leg]]).drop(columns="_line_key").sort_index()


# --------------------------------------------------------------------------- #
# Loading from the odds archive
# --------------------------------------------------------------------------- #


def _iso(moment: datetime | pd.Timestamp | str) -> str:
    stamp = pd.Timestamp(moment)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _connect(odds_db: str | Path) -> sqlite3.Connection:
    """Read-only connection with one open read transaction, so every query of a load sees the same archive state.

    The collector commits a whole scan at once; without the transaction a scan committed between the quote query
    and the observation query would give a fresh sighting to a price that was read from before the scan.
    """
    path = Path(odds_db)
    if not path.exists():
        raise FileNotFoundError(f"odds archive not found: {path}")
    # percent-encode the path: a "#", "?" or "%" in a folder name would otherwise cut the URI and open another file
    connection = sqlite3.connect(f"file:{quote(path.as_posix(), safe='/:')}?mode=ro", uri=True, timeout=60)
    connection.execute("BEGIN")
    return connection


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce", format="ISO8601")


_CURRENT_SQL = """
WITH live AS (
    SELECT fixture_id, league_id, kickoff_utc FROM fixtures
    WHERE kickoff_utc > :as_of AND (:horizon IS NULL OR kickoff_utc <= :horizon)
),
ranked AS (
    SELECT q.fixture_id, q.bookmaker_id, q.bookmaker, q.bet_id, q.bet, q.selection, q.odd, q.fetched_at,
           ROW_NUMBER() OVER (PARTITION BY q.fixture_id, q.bookmaker_id, q.bet_id, q.selection
                              ORDER BY q.fetched_at DESC, q.rowid DESC) AS rn
    FROM quotes q JOIN live USING (fixture_id)
    WHERE q.fetched_at <= :as_of
)
SELECT r.fixture_id, live.league_id, live.kickoff_utc, r.bookmaker_id, r.bookmaker, r.bet_id, r.bet,
       r.selection, r.odd, r.fetched_at AS changed_at
FROM ranked r JOIN live USING (fixture_id)
WHERE r.rn = 1
"""

_OBSERVED_SQL = """
SELECT o.fixture_id, o.bookmaker_id, MAX(o.fetched_at) AS observed_at, o.api_update AS api_update
FROM observations o JOIN fixtures f USING (fixture_id)
WHERE o.fetched_at <= :as_of AND f.kickoff_utc > :as_of AND (:horizon IS NULL OR f.kickoff_utc <= :horizon)
GROUP BY o.fixture_id, o.bookmaker_id
"""

_CLOSING_SQL = """
WITH target AS (
    SELECT fixture_id, league_id, kickoff_utc FROM fixtures WHERE fixture_id IN ({marks})
),
ranked AS (
    SELECT q.fixture_id, q.bookmaker_id, q.bookmaker, q.bet_id, q.bet, q.selection, q.odd, q.fetched_at,
           ROW_NUMBER() OVER (PARTITION BY q.fixture_id, q.bookmaker_id, q.bet_id, q.selection
                              ORDER BY q.fetched_at DESC, q.rowid DESC) AS rn
    FROM quotes q JOIN target t USING (fixture_id)
    WHERE q.fetched_at < t.kickoff_utc
)
SELECT r.fixture_id, target.league_id, target.kickoff_utc, r.bookmaker_id, r.bookmaker, r.bet_id, r.bet,
       r.selection, r.odd, r.fetched_at AS changed_at
FROM ranked r JOIN target USING (fixture_id)
WHERE r.rn = 1
"""

_CLOSING_OBSERVED_SQL = """
SELECT o.fixture_id, o.bookmaker_id, MAX(o.fetched_at) AS observed_at, o.api_update AS api_update
FROM observations o JOIN fixtures f USING (fixture_id)
WHERE f.fixture_id IN ({marks}) AND o.fetched_at < f.kickoff_utc
GROUP BY o.fixture_id, o.bookmaker_id
"""


def _finish_quotes(
    raw: pd.DataFrame,
    observed: pd.DataFrame,
    reference_time: pd.Series | pd.Timestamp,
    include_other: bool,
    include_suspect_ladders: bool = False,
) -> pd.DataFrame:
    """Canonicalize, attach the last sighting of every board and order the columns."""
    if raw.empty:
        columns = [*QUOTE_COLUMNS, *EXTRA_QUOTE_COLUMNS]
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    raw = raw.merge(observed, on=["fixture_id", "bookmaker_id"], how="left")
    raw["kickoff_utc"] = _utc(raw["kickoff_utc"])
    raw["changed_at"] = _utc(raw["changed_at"])
    observed_at = _utc(raw.pop("observed_at"))
    # A board that was never observed cannot be older than its last price change.
    raw["fetched_at"] = observed_at.fillna(raw["changed_at"])
    raw["api_update"] = _utc(raw["api_update"])  # the provider's own update time of the board (NaT when unknown)
    valid = np.isfinite(raw["odd"].to_numpy(dtype=float)) & (raw["odd"].to_numpy(dtype=float) > 1.0)
    quotes = orient_handicaps(canonicalize(raw.loc[valid].reset_index(drop=True)))
    if not include_other:
        quotes = quotes[quotes["market"] != MARKET_OTHER].reset_index(drop=True)
    quotes = dedupe_legs(quotes).reset_index(drop=True)
    quotes = flag_incoherent_ladders(quotes)
    if not include_suspect_ladders:
        quotes = quotes[~quotes["ladder_suspect"]].reset_index(drop=True)
    reference = reference_time if isinstance(reference_time, pd.Timestamp) else quotes["fixture_id"].map(reference_time)
    quotes["age_minutes"] = (reference - quotes["fetched_at"]).dt.total_seconds() / 60.0
    quotes["provider_age_minutes"] = (reference - quotes["api_update"]).dt.total_seconds() / 60.0
    quotes["odd"] = quotes["odd"].astype(float)
    ordered = [*QUOTE_COLUMNS, *EXTRA_QUOTE_COLUMNS]
    return quotes[ordered].sort_values(["fixture_id", "bookmaker", "market", "period", "line", "selection"]).reset_index(drop=True)


def load_current_quotes(
    odds_db: str | Path,
    as_of: datetime | pd.Timestamp | str,
    *,
    horizon_days: float | None = None,
    include_other: bool = True,
    include_suspect_ladders: bool = False,
) -> pd.DataFrame:
    """The prices on the boards at ``as_of``: latest quote per (fixture, bookmaker, bet, selection), canonicalized.

    Only quotes with ``fetched_at <= as_of`` are read (a price first seen later never leaks into an earlier
    decision) and only fixtures that kick off after ``as_of`` (and, when given, within ``horizon_days``).
    ``fetched_at`` of the result is the last time the bookmaker's board of the fixture was observed at or before
    ``as_of``; ``age_minutes`` is ``as_of - fetched_at`` (bookmaker-level staleness) and ``changed_at`` the time of
    the last change of that price.  ``include_other=False`` drops the rows that are not canonical legs; the rows of
    ladders that :func:`flag_incoherent_ladders` marks suspect are dropped unless ``include_suspect_ladders``.
    """
    as_of_ts = pd.Timestamp(as_of)
    as_of_ts = as_of_ts.tz_localize("UTC") if as_of_ts.tzinfo is None else as_of_ts.tz_convert("UTC")
    params = {
        "as_of": _iso(as_of_ts),
        "horizon": None if horizon_days is None else _iso(as_of_ts + pd.Timedelta(days=float(horizon_days))),
    }
    with closing(_connect(odds_db)) as connection:
        raw = pd.read_sql_query(_CURRENT_SQL, connection, params=params)
        observed = pd.read_sql_query(_OBSERVED_SQL, connection, params=params)
    return _finish_quotes(raw, observed, as_of_ts, include_other, include_suspect_ladders)


def _chunks(values: Sequence[int], size: int = 800) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def load_closing_quotes(
    odds_db: str | Path,
    fixture_ids: Iterable[int],
    *,
    include_other: bool = True,
    include_suspect_ladders: bool = False,
) -> pd.DataFrame:
    """The last quote strictly before each fixture's kickoff: the closing proxy for closing line value.

    Same columns as :func:`load_current_quotes`.  ``fetched_at`` is the last sighting of the bookmaker's board
    before kickoff and ``age_minutes`` is ``kickoff_utc - fetched_at``: how far before the start the "close" was
    seen (the archive is scanned about hourly, so the true close can be up to that much later).  The provider's
    own snapshot is older still: ``provider_age_minutes`` is ``kickoff_utc - api_update`` (see "Provider
    staleness" in the module docstring; typically 3 to 4 hours even for a board seen minutes before the start).  A
    fixture without any quote before its kickoff is absent.  Closing prices are for CLV only, never for a
    decision; pass ``max_age_minutes=None`` (or a bound above the scan spacing) to
    :func:`model_buk.scanner.fair.fair_prices` for them, its default rejects boards seen more than three hours
    before the reference time.  The loader does not know whether a fixture has started: the caller must ask only
    for fixtures whose kickoff has passed, otherwise "closing" is just the latest quote so far.
    """
    ids = sorted({int(f) for f in fixture_ids})
    raw_parts: list[pd.DataFrame] = []
    observed_parts: list[pd.DataFrame] = []
    with closing(_connect(odds_db)) as connection:
        for chunk in _chunks(ids):
            marks = ",".join("?" * len(chunk))
            raw_parts.append(pd.read_sql_query(_CLOSING_SQL.format(marks=marks), connection, params=chunk))
            observed_parts.append(pd.read_sql_query(_CLOSING_OBSERVED_SQL.format(marks=marks), connection, params=chunk))
    raw = pd.concat(raw_parts, ignore_index=True) if raw_parts else pd.DataFrame()
    observed = pd.concat(observed_parts, ignore_index=True) if observed_parts else pd.DataFrame()
    if raw.empty:
        return _finish_quotes(raw, observed, pd.Timestamp("1970-01-01", tz="UTC"), include_other, include_suspect_ladders)
    kickoff = raw.drop_duplicates("fixture_id").set_index("fixture_id")["kickoff_utc"]
    return _finish_quotes(raw, observed, _utc(kickoff), include_other, include_suspect_ladders)


def mapped_share(quotes: pd.DataFrame, by: str = "bookmaker") -> pd.DataFrame:
    """Share of quotes that map to a canonical market, per ``by`` (default the bookmaker).

    Columns: the group, ``n_quotes``, ``n_mapped``, ``share_mapped``.
    """
    mapped = quotes["market"] != MARKET_OTHER
    table = pd.DataFrame({by: quotes[by], "mapped": mapped}).groupby(by, dropna=False)["mapped"].agg(["size", "sum"])
    table.columns = ["n_quotes", "n_mapped"]
    table["share_mapped"] = table["n_mapped"] / table["n_quotes"]
    return table.reset_index()


def utc_now() -> pd.Timestamp:
    """Current time as a UTC timestamp (the scanner's default ``as_of``)."""
    return pd.Timestamp(datetime.now(timezone.utc))
