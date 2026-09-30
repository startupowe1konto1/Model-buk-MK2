"""Bet types that deviate systematically from the sharp price: measured on the archive, excluded from the scan.

A soft bookmaker sometimes quotes one of its bet types in a way that does not follow the market: a ladder that is
flat or shifted against Pinnacle, a stale alternative line, a yes/no formulation priced with a different margin
split.  Such a bet type produces "edges" on every scan that are an artefact of the bet type, not an opportunity.
This module measures, per ``(bookmaker, bet_id)``, how the bookmaker's own de-vigged probability of a leg differs
from Pinnacle's de-vigged probability of the same leg, and flags the pairs whose deviation is abnormal.

Measurement (:func:`measure_bet_types`)
---------------------------------------
* **Snapshots.**  The board as it stood at the decision time and every ``step_hours`` (12) before it, back to
  ``lookback_days`` (7): at each snapshot the latest price of every bookmaker, bet and selection seen at or before
  it, for the fixtures that had not kicked off, from boards seen at most ``max_board_age_hours`` (8) before it.  A
  fixture contributes once per snapshot it was upcoming in, so the sample is weighted like the scanner's own view
  (far and near kickoff).  Only prices seen at or before the decision time are read (a replay measures what it
  could have known).
* **Pairs of legs.**  For every complete set of one line of one bet type of one bookmaker (both sides of a two-way
  line, all three of a 1X2; double chance and exact score are skipped) the proportionally de-vigged probability
  of the set's first selection (home, over, yes) is compared with the de-vigged Pinnacle probability of the same
  canonical leg (power method, complete Pinnacle sets only, the scanner's direct ``pinnacle`` source).  The rows
  are **not** merged across bet types first, so two bet types of one bookmaker that price the same leg (Bet365
  "Goals Over/Under" 5 and "Goal Line" 50) are measured separately.
* **Statistics per pair**: ``market`` and ``period`` (the canonical market it prices), ``n_legs`` and
  ``n_fixtures``, ``median_dev`` (median of ``book - pinnacle``),
  ``mad_dev`` (1.4826 times the median absolute deviation from that median: a robust standard deviation),
  ``p90_abs_dev`` and ``tail_share`` (share of legs with ``|dev| > 0.05``, the size of a gap the engine's book
  shift guard begins to question).

Exclusion rule (:func:`flag_bet_types`)
---------------------------------------
A pair with at least ``min_legs`` (100) measured legs is excluded when

* ``|median_dev|`` is above ``max_abs_median`` (0.02): the bet type sits two points away from the sharp price
  on average;
* ``mad_dev`` is more than ``max_dispersion_ratio`` (1.4) times the typical dispersion of its market: the median
  ``mad_dev`` of the judged pairs of the same canonical market and period (of all judged pairs when fewer than
  three price that market).  Dispersion is judged against the peers because some markets scatter more for every
  bookmaker (team totals: 0.029 typical, 1X2: 0.018), so an absolute bound would exclude whole markets instead
  of the bookmaker that prices one badly;
* or ``tail_share`` is above ``max_tail_share`` (0.15): one leg in seven or more is more than five points off.

Measured on the archive (nine snapshots of 2026-09-29 and 30, 81 pairs with at least 100 legs): ``|median_dev|``
has a median of 0.006, a 95th percentile of 0.023 and a maximum of 0.030; the dispersion ratio a median of 1.00,
a 95th percentile of 1.33 and a maximum of 2.11; ``tail_share`` 0.034, 0.116 and 0.184.  The thresholds sit just
above the 95th percentiles, so a pair is excluded only when it is an outlier.  With them the archive excludes nine
pairs: Bet365 "Clean Sheet" (27, 28) and William Hill "Score a Goal" (43, 116), yes/no formulations of a team
total that sit 2.3 to 3 points off; Marathonbet first-half corners (77, +2.5 points); SBO 1X2 (1: 2.1 times the
dispersion of its market, 18% of its legs more than five points off) and first-half 1X2 (13: 1.8 times); Bet365
corners over/under (45: 1.6 times); BetVictor first-half goal totals (6: 1.5 times).  Bet365 "Goal Line" (50),
named as suspicious in the review, is *not* an outlier on these two days (median +0.001, dispersion 0.025 against
0.025 for its market, tail 5%, 7 820 legs): the value legs it produced sat at a book shift of 0.05 to 0.06, which
the engine's other guards (book shift, suspect cap, market tier) judge.  Pinnacle itself is never excluded (it is
the reference).  A pair with fewer legs than ``min_legs`` is not judged (missing stays missing: it is scanned).

The exclusion is applied when the quotes are loaded (:func:`model_buk.scanner.markets.load_current_quotes`,
``exclude_bet_types``): the rows of an excluded bet type are dropped *before* the legs of one bookmaker are
merged, so another bet type of the same bookmaker that prices the same leg still counts, and the excluded prices
never enter a consensus fair price either.  The table is kept in the scanner register (``bet_type_checks``,
append-only) and refreshed at most once a day (:attr:`ScanConfig.bet_type_refresh_hours`).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from model_buk.markets.pricing import normalise_bookmaker_name
from model_buk.scanner import markets

REFERENCE: Final = "pinnacle"
CHECK_COLUMNS: Final[tuple[str, ...]] = (
    "bookmaker",
    "bet_id",
    "bet",
    "market",
    "period",
    "n_legs",
    "n_fixtures",
    "median_dev",
    "mad_dev",
    "p90_abs_dev",
    "tail_share",
)
TAIL_GAP: Final = 0.05
"""A leg whose book probability is more than this far from Pinnacle's counts in ``tail_share``."""

_SET_MARKETS: Final[dict[str, tuple[str, ...]]] = {m: s for m, s in markets.SELECTIONS.items() if m != "dc"}


def _mad(values: np.ndarray) -> float:
    if len(values) == 0:
        return float("nan")
    return 1.4826 * float(np.median(np.abs(values - np.median(values))))


def board_deviations(quotes: pd.DataFrame, *, method: str = "power") -> pd.DataFrame:
    """Per measured leg of one board: ``book - pinnacle`` de-vigged probability, per bookmaker and bet type.

    ``quotes`` are canonical quotes that were *not* merged across bet types (several rows per leg and bookmaker
    are allowed, one per bet type); they need ``fixture_id, bookmaker, bet_id, market, period, line, selection,
    odd`` and optionally ``bet``.  Returns ``fixture_id, bookmaker, bet_id, bet, market, period, line_key,
    selection, book_p, p_pin, dev``.
    """
    from model_buk.scanner.fair import _devig_all, _prepare

    columns = ["fixture_id", "bookmaker", "bet_id", "bet", "market", "period", "line_key", "selection", "book_p", "p_pin", "dev"]
    if quotes is None or len(quotes) == 0:
        return pd.DataFrame(columns=columns)
    q = quotes[quotes["market"].isin(list(_SET_MARKETS))].copy()
    if "three_way" in q.columns:
        q = q[~q["three_way"].fillna(False).astype(bool)]
    q = q[pd.to_numeric(q["odd"], errors="coerce") > 1.0]
    if q.empty:
        return pd.DataFrame(columns=columns)
    q["odd"] = q["odd"].astype(float)
    q["bk"] = q["bookmaker"].map(normalise_bookmaker_name)
    q["line_key"] = pd.to_numeric(q["line"], errors="coerce").round(4).fillna(-9999.0)
    if "bet" not in q.columns:
        q["bet"] = None

    sharp = q[q["bk"] == REFERENCE]
    if sharp.empty:
        return pd.DataFrame(columns=columns)
    prepared = _prepare(sharp.drop(columns=["line_key", "bk"]), None)
    pin = _devig_all(prepared, method, 0.0)
    pin = pin[pin["bk"] == REFERENCE][["fixture_id", "market", "period", "line_key", "selection", "p"]].rename(columns={"p": "p_pin"})

    soft = q[q["bk"] != REFERENCE]
    key = ["fixture_id", "bookmaker", "bet_id", "market", "period", "line_key"]
    soft = soft.sort_values("odd", kind="mergesort").drop_duplicates([*key, "selection"], keep="last")
    inverse = 1.0 / soft["odd"]
    groups = [soft[c] for c in key]
    size = inverse.groupby(groups).transform("size")
    total = inverse.groupby(groups).transform("sum")
    need = soft["market"].map(lambda m: len(_SET_MARKETS[m]))
    first = soft["market"].map(lambda m: _SET_MARKETS[m][0])
    keep = (size == need) & (soft["selection"] == first) & (total >= 0.995) & (total <= 1.60)
    soft = soft[keep].assign(book_p=(inverse / total)[keep])
    out = soft.merge(pin, on=["fixture_id", "market", "period", "line_key", "selection"], how="inner")
    out["dev"] = out["book_p"] - out["p_pin"]
    return out[columns].reset_index(drop=True)


def _snapshot_quotes(connection: sqlite3.Connection, snapshot: pd.Timestamp, max_board_age_hours: float) -> pd.DataFrame:
    params = {"as_of": markets._iso(snapshot), "horizon": None}
    raw = pd.read_sql_query(markets._CURRENT_SQL, connection, params=params)
    if raw.empty:
        return raw
    observed = pd.read_sql_query(markets._OBSERVED_SQL, connection, params=params)
    raw = raw.merge(observed, on=["fixture_id", "bookmaker_id"], how="left")
    seen = pd.to_datetime(raw["observed_at"], utc=True, errors="coerce", format="ISO8601")
    seen = seen.fillna(pd.to_datetime(raw["changed_at"], utc=True, errors="coerce", format="ISO8601"))
    fresh = (snapshot - seen) <= pd.Timedelta(hours=float(max_board_age_hours))
    raw = raw[fresh.to_numpy() & (raw["odd"].to_numpy(dtype=float) > 1.0)].reset_index(drop=True)
    if raw.empty:
        return raw
    return markets.orient_handicaps(markets.canonicalize(raw))


def measure_bet_types(
    odds_db: str | Path,
    as_of: object,
    *,
    lookback_days: float = 7.0,
    step_hours: float = 12.0,
    max_board_age_hours: float = 8.0,
    method: str = "power",
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Deviation statistics per ``(bookmaker, bet_id)`` from snapshots of the archive (see the module docstring).

    Returns ``(stats, info)``: ``stats`` has :data:`CHECK_COLUMNS` (one row per pair with at least one measured
    leg, the reference bookmaker never appears), ``info`` holds ``snapshots``, ``legs`` and ``fixtures``.
    """
    stamp = pd.Timestamp(as_of)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    count = max(int(math.floor(float(lookback_days) * 24.0 / float(step_hours))), 0) + 1
    snapshots = [stamp - pd.Timedelta(hours=float(step_hours) * k) for k in range(count)]
    parts: list[pd.DataFrame] = []
    used = 0
    with closing(markets._connect(odds_db)) as connection:
        for snapshot in snapshots:
            quotes = _snapshot_quotes(connection, snapshot, max_board_age_hours)
            if quotes is None or len(quotes) == 0:
                continue
            deviations = board_deviations(quotes[quotes["market"] != markets.MARKET_OTHER], method=method)
            if len(deviations):
                used += 1
                parts.append(deviations)
    info: dict[str, object] = {"snapshots": used, "snapshots_tried": len(snapshots), "legs": 0, "fixtures": 0}
    if not parts:
        return pd.DataFrame(columns=list(CHECK_COLUMNS)), info
    legs = pd.concat(parts, ignore_index=True)
    info["legs"] = int(len(legs))
    info["fixtures"] = int(legs["fixture_id"].nunique())
    return deviation_stats(legs), info


def deviation_stats(legs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-leg deviations (:func:`board_deviations`) to one row per ``(bookmaker, bet_id)``."""
    if legs is None or len(legs) == 0:
        return pd.DataFrame(columns=list(CHECK_COLUMNS))
    rows = []
    for (bookmaker, bet_id), group in legs.groupby(["bookmaker", "bet_id"], sort=True):
        dev = group["dev"].to_numpy(dtype=float)
        dev = dev[np.isfinite(dev)]
        if len(dev) == 0:
            continue
        bets = group["bet"].dropna()
        rows.append(
            {
                "bookmaker": str(bookmaker),
                "bet_id": int(bet_id),
                "bet": str(bets.iloc[0]) if len(bets) else None,
                "market": str(group["market"].mode().iloc[0]),
                "period": str(group["period"].mode().iloc[0]),
                "n_legs": int(len(dev)),
                "n_fixtures": int(group["fixture_id"].nunique()),
                "median_dev": float(np.median(dev)),
                "mad_dev": _mad(dev),
                "p90_abs_dev": float(np.quantile(np.abs(dev), 0.9)),
                "tail_share": float(np.mean(np.abs(dev) > TAIL_GAP)),
            }
        )
    return pd.DataFrame(rows, columns=list(CHECK_COLUMNS))


MIN_PEERS: Final = 3
"""Judged pairs of one market and period needed for a market-specific typical dispersion."""


def flag_bet_types(
    stats: pd.DataFrame,
    *,
    max_abs_median: float,
    max_dispersion_ratio: float,
    max_tail_share: float,
    min_legs: int,
) -> pd.DataFrame:
    """``stats`` with ``excluded`` (bool) and ``reason``: ``median``, ``dispersion`` and / or ``tail`` joined with
    ``+`` for an excluded pair, empty for a kept one, ``too_few_legs`` for a pair that was not judged; and
    ``mad_ratio``, the pair's dispersion over the typical one of its market (see the module docstring)."""
    out = stats.copy()
    if out.empty:
        out["mad_ratio"] = pd.Series(dtype=float)
        out["excluded"] = pd.Series(dtype=bool)
        out["reason"] = pd.Series(dtype=object)
        return out
    if "period" not in out.columns:
        out["period"] = "ft"
    enough = pd.to_numeric(out["n_legs"], errors="coerce").fillna(0) >= int(min_legs)
    reference = out["bookmaker"].map(normalise_bookmaker_name) == REFERENCE
    mad = pd.to_numeric(out["mad_dev"], errors="coerce")
    judged = enough & ~reference & mad.notna()
    overall = float(mad[judged].median()) if judged.any() else float("nan")
    typical = pd.Series(overall, index=out.index, dtype=float)
    for (market, period), group in out[judged].groupby(["market", "period"]):
        if len(group) >= MIN_PEERS:
            same = (out["market"] == market) & (out["period"] == period)
            typical[same] = float(group["mad_dev"].median())
    out["mad_ratio"] = mad / typical.where(typical > 0)
    median = pd.to_numeric(out["median_dev"], errors="coerce").abs() > float(max_abs_median)
    spread = out["mad_ratio"] > float(max_dispersion_ratio)
    tail = pd.to_numeric(out["tail_share"], errors="coerce") > float(max_tail_share)
    reasons = []
    for is_enough, m, s, t, ref in zip(enough, median, spread, tail, reference, strict=True):
        if ref:
            reasons.append("")
        elif not is_enough:
            reasons.append("too_few_legs")
        else:
            reasons.append("+".join(name for name, hit in (("median", m), ("dispersion", s), ("tail", t)) if hit))
    out["reason"] = reasons
    out["excluded"] = enough & ~reference & (median | spread | tail)
    return out


def excluded_pairs(flagged: pd.DataFrame) -> frozenset[tuple[str, int]]:
    """The excluded ``(normalised bookmaker, bet_id)`` pairs of :func:`flag_bet_types`."""
    if flagged is None or len(flagged) == 0 or "excluded" not in flagged.columns:
        return frozenset()
    rows = flagged[flagged["excluded"].astype(bool)]
    return frozenset((normalise_bookmaker_name(b), int(i)) for b, i in zip(rows["bookmaker"], rows["bet_id"], strict=True))


def pair_labels(pairs: Iterable[tuple[str, int]], names: pd.DataFrame | None = None) -> list[str]:
    """Readable ``Bookmaker bet_id (bet)`` labels of excluded pairs, for diagnostics and the page."""
    lookup: dict[tuple[str, int], tuple[str, str | None]] = {}
    if names is not None and len(names):
        for record in names.itertuples(index=False):
            lookup[(normalise_bookmaker_name(record.bookmaker), int(record.bet_id))] = (str(record.bookmaker), getattr(record, "bet", None))
    labels = []
    for bookmaker, bet_id in sorted(pairs):
        shown, bet = lookup.get((bookmaker, int(bet_id)), (bookmaker, None))
        labels.append(f"{shown} {int(bet_id)}" + (f" ({bet})" if isinstance(bet, str) and bet else ""))
    return labels


__all__ = [
    "CHECK_COLUMNS",
    "board_deviations",
    "deviation_stats",
    "excluded_pairs",
    "flag_bet_types",
    "measure_bet_types",
    "pair_labels",
]
