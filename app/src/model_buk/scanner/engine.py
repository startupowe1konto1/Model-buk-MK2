"""Value legs: quotes joined to fair prices, EV per tax regime, and one pick per market family.

The scanner looks at every quote of every bookmaker for every upcoming match and asks one question: does this
price beat the fair price by more than the uncertainty of that fair price, after tax?  The fair price comes from
the sharp market (Pinnacle de-vigged, then Betfair or a consensus where Pinnacle is silent), which is supplied
already de-vigged in the *fair frame*.  Our statistical models are a secondary signal: a tie-break or an optional
veto, never a replacement of a market price (they are measurably worse than Pinnacle).

Canonical leg format
--------------------
A leg is identified by ``(fixture_id, market, period, line, selection)``; ``period`` is ``ft`` | ``1h`` | ``2h``.
Markets and selections: ``1x2`` home|draw|away, ``dc`` 1x|x2|12, ``dnb`` home|away, ``ah`` (line = handicap of the
HOME team, selection home|away), ``ou`` and ``team_ou_home`` / ``team_ou_away`` over|under, ``btts`` yes|no, ``cs``
``h:a``, ``corners_ou`` / ``corners_home_ou`` / ``corners_away_ou`` over|under, ``corners_ah``, ``corners_1x2``,
``cards_ou`` / ``cards_home_ou`` / ``cards_away_ou`` and ``cards_ah``.  Anything else is market ``other`` and is
never priced.

Frames (column names are the contract)
--------------------------------------
* quotes: ``fixture_id, league_id, kickoff_utc, bookmaker, bet_id, market, period, line, selection, odd,
  fetched_at`` (``fetched_at`` = when the price was last seen; the loader must not hand over the time of the last
  *change* because the archive stores changes only).
* fair: ``fixture_id, market, period, line, selection, p_fair, p_se, fair_source, devig_method, n_books,
  book_margin`` with ``fair_source`` in ``pinnacle | pinnacle_derived | betfair | consensus``.
* model: ``fixture_id, market, period, line, selection, p_model, model_name`` (optional, one row per leg).

Rules encoded here
------------------
* Pinnacle and Betfair are price sources only: a quote at a reference bookmaker never becomes a leg (art. 107 KKS,
  plan rule).  The set is configurable (:attr:`ScannerConfig.reference_bookmakers`) but never empty by default.
* No look-ahead: a quote seen after ``as_of`` is ignored, and quotes older than ``max_quote_age_hours`` (for
  matches more than ``far_lead_hours`` away: ``max_quote_age_far_hours``, because the odds collector looks at those
  days only every six hours) or for matches that start within ``min_minutes_to_kickoff`` are skipped.
* Missing stays missing: a leg without a fair price keeps ``p_fair`` NaN; an unknown standard error gives a NaN
  lower bound and therefore never a value flag.
* Plausibility guards: a leg whose raw edge exceeds ``max_edge`` (20% by default), whose fair probability has a
  standard error above ``max_p_se`` or whose bookmaker quotes a whole line far from the sharp price (``book_shift``
  above ``max_book_shift``, below) is classed ``suspect``: it is written to the register for research (later
  closing line value tells whether such gaps are real) but is never a value leg and never enters a coupon.
* Book shift: when a bookmaker quotes a complete set of one line (both sides of a two-way line, all three of a
  1X2) its own proportionally de-vigged probability of the leg is ``book_p`` and ``book_shift = book_p - p_fair``.
  A single price that is above the fair price by ``max_edge`` (20%) while the other side of the line is priced
  normally moves the de-vigged probability by at most 0.06 (largest at ``p = 0.5``), so a larger shift means the
  whole pair sits somewhere else: a stale board, or a ladder whose lines are mislabelled (measured on the live
  archive: Bet365 and SBO quote one price pair on several handicap lines).  The median ``|shift|`` of all two-way
  legs is 0.02 and its 95th percentile 0.057, but 91% of the legs that looked like value without this guard had
  a shift above 0.05.  The guard is skipped where the set is incomplete (missing stays missing).
* Refund share: on integer and quarter lines (Asian handicaps, totals) a stake can be returned (push) or half
  returned, and ``p_fair`` is then the *break-even* probability, so ``p_fair * odd - 1`` is larger than the real
  edge by ``1 / (1 - refund_share)``.  :func:`refund_share` reads the refund mass off the half lines of the same
  ladder in the fair frame (the push probability at line ``k`` is the difference of the two neighbouring half-line
  probabilities; a quarter line refunds half of it; draw no bet refunds the draw), and the per-regime EV is scaled
  by ``1 - refund_share``.  It is NaN where the ladder does not give it: the EV is then the unscaled one, and
  such a leg is not used in coupons.
* A leg priced only by the model is allowed in a separate, clearly flagged ``model_only`` class
  (``leg_class == "model_only"``, ``fair_source == "model"``).  It never enters the value list or a coupon.

Value definition
----------------
For every tax multiplier ``tau`` of :attr:`ScannerConfig.taus` (default 0.88, 0.94 and 1.00)::

    ev_net_t{tau}       = tau * p_fair * odd - 1
    ev_net_lower_t{tau} = tau * (p_fair - z * p_se) * odd - 1        (z = 1.645 by default)
    value_t{tau}        = ev_net_lower_t{tau} > min_ev

(on a line with refunds both EV columns are multiplied by ``1 - refund_share``, see above) so ``value_t088`` says "this single bet is profitable after the 12% stake tax with 95% confidence".  Membership of
the *value list* (``is_value``) is decided at :attr:`ScannerConfig.leg_tau` (default 1.00: the price beats the
fair price with confidence).  That is the right leg-level test for a coupon, because the stake tax is charged once
on the whole coupon, so a 5% edge per leg is enough for three legs (``1.05**3 * 0.88 - 1 = +1.87%``) although it
is far below the 13.6% a single bet needs.  The per-regime columns let the reader see which legs also survive as
singles.  When a paper stake is configured (:attr:`ScannerConfig.stake_pln`) the winnings-PIT switch of
:mod:`model_buk.markets.staking` is applied to ``tau`` per row.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from model_buk.markets.pricing import (
    normalise_bookmaker_name,
    require_non_negative,
    require_probability,
    require_tax_multiplier,
)
from model_buk.markets.staking import DEFAULT_PIT_RULE, PitRule
from model_buk.scanner.settlement import leg_key, tau_label

if TYPE_CHECKING:  # pragma: no cover
    from model_buk.scanner.coupons import CouponSet
    from model_buk.scanner.register import ScannerRegister

DATA_ROOT = Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
SCANNER_DIR = DATA_ROOT / "scanner"

REFERENCE_BOOKMAKERS: tuple[str, ...] = ("Pinnacle", "Betfair")
"""Price sources only: the scanner never recommends a bet at these bookmakers."""
DEFAULT_TAUS: tuple[float, ...] = (0.88, 0.94, 1.00)
"""Stake-tax multipliers: Polish standard, Betclic degraded, tax free."""

QUOTE_COLUMNS: tuple[str, ...] = (
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
FAIR_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "market",
    "period",
    "line",
    "selection",
    "p_fair",
    "p_se",
    "fair_source",
    "devig_method",
    "n_books",
    "book_margin",
)
MODEL_COLUMNS: tuple[str, ...] = ("fixture_id", "market", "period", "line", "selection", "p_model", "model_name")
FIXTURE_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "league_id",
    "league_name",
    "country",
    "season",
    "round",
    "kickoff_utc",
    "status_short",
    "home_team_id",
    "home_team",
    "away_team_id",
    "away_team",
    "home_goals",
    "away_goals",
)

_KEY = ["fixture_id", "market", "period", "line_key", "selection"]
KEY_COLUMNS: tuple[str, ...] = tuple(_KEY)
"""Join key of a leg (``line_key`` is ``line`` with NaN replaced by a sentinel)."""
_NO_LINE = -9999.0
"""Join sentinel for markets without a line (NaN keys are avoided on purpose)."""

_FAMILY_OF_MARKET: Mapping[str, str] = {
    "1x2": "result",
    "dc": "result",
    "dnb": "result",
    "ah": "result",
    "ou": "goals",
    "team_ou_home": "goals",
    "team_ou_away": "goals",
    "btts": "goals",
    "cs": "goals",
    "corners_ou": "corners",
    "corners_home_ou": "corners",
    "corners_away_ou": "corners",
    "corners_ah": "corners",
    "corners_1x2": "corners",
    "cards_ou": "cards",
    "cards_home_ou": "cards",
    "cards_away_ou": "cards",
    "cards_ah": "cards",
}

_AH_MARKETS = frozenset({"ah", "corners_ah", "cards_ah"})
"""Handicap markets: the line is the handicap of the home side, the covering side is ``home``."""
_TOTAL_MARKETS = frozenset(
    {
        "ou",
        "team_ou_home",
        "team_ou_away",
        "corners_ou",
        "corners_home_ou",
        "corners_away_ou",
        "cards_ou",
        "cards_home_ou",
        "cards_away_ou",
    }
)
"""Over/under markets: the covering side is ``over``."""
_SET_SIZE: Mapping[str, int] = {
    "1x2": 3,
    "corners_1x2": 3,
    "dnb": 2,
    "btts": 2,
    **{market: 2 for market in _AH_MARKETS | _TOTAL_MARKETS},
}
"""Complementary selections of one line of one bookmaker (double chance and exact score have no such set)."""


def market_family(market: str, period: str) -> str:
    """Correlated markets share a family: e.g. 1x2, double chance and every handicap are ``result_ft``.

    A coupon leg and a value-list pick are chosen per ``(fixture, bookmaker, family)`` so that the several lines
    of one ladder (an over/under ladder, an Asian-handicap ladder) never crowd the list.
    """
    family = _FAMILY_OF_MARKET.get(str(market), "other")
    return f"{family}_{period}"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScannerConfig:
    """Every parameter of the scanner; any change is a new :attr:`config_id` (plan rule).

    Evaluation
    ----------
    ``taus`` stake-tax multipliers evaluated for every leg; ``z`` the number of standard errors subtracted from
    ``p_fair``; ``min_ev`` the strict threshold the lower-bound EV must exceed; ``leg_tau`` the regime that decides
    membership of the value list; ``stake_pln`` an optional paper stake that switches on the winnings-PIT rule.
    ``model_delta`` the tolerance of ``model_agrees`` (``p_model >= p_fair - delta``); ``model_veto`` turns the
    model into a veto over value legs it contradicts (off by default: the model is worse than Pinnacle);
    ``model_only_se`` the standard error assumed for a leg priced only by the model.
    ``reference_bookmakers`` never produce legs; ``max_quote_age_hours``, ``min_minutes_to_kickoff`` and
    ``max_days_ahead`` bound the quotes considered.  ``max_quote_age_hours`` (3) is the staleness bound for matches
    within ``far_lead_hours`` (48) of the decision time; matches further away are refreshed by the odds collector
    only every six hours (a slow market: the lead-time drift term of the fair price is the larger one there), so
    they get ``max_quote_age_far_hours`` (8): without it more than half of the upcoming matches would be out of the
    pool for half of every day.  ``max_edge`` and ``max_p_se`` are plausibility guards: a leg
    whose raw edge is above ``max_edge`` (a soft book 20% above the sharp price is nearly always a data error, a
    line mismatch or a price that cannot be taken) or whose fair probability is more uncertain than ``max_p_se``
    (a poorly fitted derived price) is classed ``suspect``; it is logged for research but never value.
    ``max_book_shift`` is the third guard: the bookmaker's own de-vigged probability of the leg (from its complete
    set of the line) may differ from the fair probability by at most this much (0.06 is the largest shift a
    one-sided price gap of ``max_edge`` can cause), otherwise the bookmaker's whole pair is on another line or
    stale and the leg is ``suspect``.

    Coupons (see :mod:`model_buk.scanner.coupons`)
    ---------------------------------------------
    ``coupon_sizes`` legs per coupon; ``min_hit`` minimum win probability; ``min_coupon_ev`` strict threshold of
    the lower-bound EV at ``coupon_keep_tau`` (default 0.88: only coupons that stay profitable under the full
    stake tax are kept); ``top_k_legs`` and ``top_k_hit_legs`` prune the legs per bookmaker before combining (best
    by lower-bound EV plus best by win probability); ``top_n_per_bookmaker`` caps the proposed coupons per
    bookmaker (``top_max_leg_reuse`` limits how often one leg may appear among them, so the proposals are not one
    bet in several disguises); ``log_cap_per_bookmaker`` caps the coupons written to the register per bookmaker
    (``None`` logs every coupon that passes; the default 500 keeps an hourly register small, and the scan row
    keeps the full count of passing coupons).
    """

    taus: tuple[float, ...] = DEFAULT_TAUS
    z: float = 1.645
    min_ev: float = 0.0
    leg_tau: float = 1.00
    stake_pln: float | None = None
    model_delta: float = 0.02
    model_veto: bool = False
    model_only_se: float = 0.03
    include_model_only: bool = True
    max_edge: float = 0.20
    max_p_se: float = 0.05
    max_book_shift: float = 0.06
    reference_bookmakers: tuple[str, ...] = REFERENCE_BOOKMAKERS
    max_quote_age_hours: float = 3.0
    far_lead_hours: float = 48.0
    max_quote_age_far_hours: float = 8.0
    min_minutes_to_kickoff: float = 10.0
    max_days_ahead: float | None = None
    coupon_sizes: tuple[int, ...] = (2, 3)
    min_hit: float = 0.30
    min_coupon_ev: float = 0.0
    coupon_keep_tau: float = 0.88
    top_k_legs: int = 60
    top_k_hit_legs: int = 30
    top_n_per_bookmaker: int = 20
    top_max_leg_reuse: int = 3
    log_cap_per_bookmaker: int | None = 500
    top_value_rows: int = 200
    log_change_tolerance: float = 0.005
    log_heartbeat_hours: float = 6.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "taus", tuple(float(t) for t in self.taus))
        object.__setattr__(self, "coupon_sizes", tuple(int(n) for n in self.coupon_sizes))
        object.__setattr__(self, "reference_bookmakers", tuple(str(b) for b in self.reference_bookmakers))
        if not self.taus:
            raise ValueError("taus must not be empty")
        for tau in self.taus:
            require_tax_multiplier(tau, "tau")
        if len({tau_label(t) for t in self.taus}) != len(self.taus):
            raise ValueError("taus must be distinct")
        for name in ("leg_tau", "coupon_keep_tau"):
            value = getattr(self, name)
            if not any(math.isclose(value, tau, abs_tol=1e-9) for tau in self.taus):
                raise ValueError(f"{name} must be one of taus")
        for name in (
            "z",
            "min_ev",
            "model_delta",
            "model_only_se",
            "max_edge",
            "max_p_se",
            "max_book_shift",
            "max_quote_age_hours",
            "far_lead_hours",
            "max_quote_age_far_hours",
            "min_minutes_to_kickoff",
            "min_coupon_ev",
            "log_change_tolerance",
            "log_heartbeat_hours",
        ):
            require_non_negative(getattr(self, name), name)
        require_probability(self.min_hit, "min_hit")
        if self.max_quote_age_far_hours < self.max_quote_age_hours:
            raise ValueError("max_quote_age_far_hours must not be below max_quote_age_hours")
        if self.stake_pln is not None and not (math.isfinite(self.stake_pln) and self.stake_pln > 0):
            raise ValueError("stake_pln must be a finite positive amount or None")
        if self.max_days_ahead is not None and not self.max_days_ahead > 0:
            raise ValueError("max_days_ahead must be positive or None")
        if not self.coupon_sizes or any(n < 2 for n in self.coupon_sizes):
            raise ValueError("coupon_sizes must be integers of at least 2")
        for name in ("top_k_legs", "top_k_hit_legs", "top_n_per_bookmaker", "top_value_rows"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.top_max_leg_reuse < 1:
            raise ValueError("top_max_leg_reuse must be at least 1")
        if self.log_cap_per_bookmaker is not None and self.log_cap_per_bookmaker < 0:
            raise ValueError("log_cap_per_bookmaker must be non-negative or None")
        if not self.reference_bookmakers:
            raise ValueError("reference_bookmakers must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @property
    def config_id(self) -> str:
        """Short stable hash of every parameter: a changed config is a new id."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()[:12]

    @property
    def leg_label(self) -> str:
        return tau_label(self.leg_tau)

    @property
    def keep_label(self) -> str:
        return tau_label(self.coupon_keep_tau)

    def labels(self) -> tuple[str, ...]:
        return tuple(tau_label(t) for t in self.taus)


# --------------------------------------------------------------------------- #
# Frame preparation
# --------------------------------------------------------------------------- #


def to_utc(series: pd.Series) -> pd.Series:
    """Timestamps as timezone-aware UTC; unparseable values become NaT."""
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dt, "tz", None) is None:
            return series.dt.tz_localize("UTC")
        return series.dt.tz_convert("UTC")
    return pd.to_datetime(series, utc=True, errors="coerce", format="ISO8601")


def _text(series: pd.Series, default: str | None = None) -> pd.Series:
    out = series.astype("object").where(series.notna(), default)
    return out.map(lambda v: str(v).strip().lower() if v is not None and v == v else v)


def _normalise_keys(frame: pd.DataFrame) -> pd.DataFrame:
    """Canonical key columns: int fixture id, lower-case text, ``line_key`` without NaN."""
    out = frame.copy()
    out["fixture_id"] = pd.to_numeric(out["fixture_id"], errors="coerce").astype("Int64")
    out = out[out["fixture_id"].notna()].copy()
    out["fixture_id"] = out["fixture_id"].astype("int64")
    out["market"] = _text(out["market"])
    out["period"] = _text(out["period"], "ft")
    out["selection"] = _text(out["selection"])
    out["line"] = pd.to_numeric(out["line"], errors="coerce").astype(float)
    out["line_key"] = out["line"].round(4).fillna(_NO_LINE)
    return out


normalise_keys = _normalise_keys


def effective_tau(tau: float, odd: np.ndarray, stake_pln: float | None, rule: PitRule = DEFAULT_PIT_RULE) -> np.ndarray:
    """``tau`` per row, switched to ``tau * (1 - rate)`` where a paper stake pushes the payout over the PIT threshold.

    Mirrors :func:`model_buk.markets.staking.effective_tax_multiplier` (strictly above the threshold) for arrays.
    """
    odd = np.asarray(odd, dtype=float)
    if stake_pln is None:
        return np.full(odd.shape, float(tau))
    payout = stake_pln * tau * odd
    return np.where(payout > rule.threshold_pln, tau * (1.0 - rule.rate), float(tau))


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"{name} frame is missing columns: {', '.join(missing)}")


@dataclass
class EvaluationDiagnostics:
    """What happened to the quotes on the way to the legs frame (all counts are quote rows)."""

    n_quotes: int = 0
    n_bad_odds: int = 0
    n_unmapped: int = 0
    n_reference_dropped: int = 0
    n_untimed_or_future: int = 0
    n_stale: int = 0
    n_started: int = 0
    n_beyond_horizon: int = 0
    n_duplicate: int = 0
    n_unpriced: int = 0
    n_legs: int = 0
    n_market_fair: int = 0
    n_model_only: int = 0
    n_edge_positive: int = 0
    n_value: int = 0
    n_vetoed: int = 0
    n_suspect: int = 0
    n_fixtures: int = 0
    n_bookmakers: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Book probability and refund share
# --------------------------------------------------------------------------- #


def book_probability(quotes: pd.DataFrame) -> np.ndarray:
    """A bookmaker's own proportionally de-vigged probability of every quote, NaN where its set is incomplete.

    ``quotes`` needs ``bookmaker, fixture_id, market, period, line_key`` and ``odd`` (one row per selection).  The
    set of a line is complete when the bookmaker quotes as many selections as the market has (two for a two-way
    line, three for a 1X2); markets without such a set (double chance, exact score) stay NaN.
    """
    if len(quotes) == 0:
        return np.zeros(0)
    keys = [quotes["bookmaker"], quotes["fixture_id"], quotes["market"], quotes["period"], quotes["line_key"]]
    inverse = 1.0 / quotes["odd"].astype(float)
    total = inverse.groupby(keys).transform("sum").to_numpy(dtype=float)
    size = inverse.groupby(keys).transform("size").to_numpy(dtype=float)
    expected = quotes["market"].map(_SET_SIZE).to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(size == expected, inverse.to_numpy(dtype=float) / total, np.nan)


def _cover_probabilities(fair_frame: pd.DataFrame) -> pd.Series:
    """P(the covering side wins) at every priced line: ``home`` for handicaps, ``over`` for totals.

    The other side contributes its complement, which is exact on half lines (no refund, the two sides sum to
    one); the main side is preferred where both exist.  Indexed by ``(fixture_id, market, period, line_key)``.
    """
    names = ["fixture_id", "market", "period", "line_key"]
    lined = fair_frame[fair_frame["market"].isin(_AH_MARKETS | _TOTAL_MARKETS) & (fair_frame["line_key"] != _NO_LINE)]
    if lined.empty:
        return pd.Series(dtype=float, index=pd.MultiIndex.from_arrays([[], [], [], []], names=names))
    covering = np.where(lined["market"].isin(_AH_MARKETS), "home", "over")
    is_main = lined["selection"].to_numpy() == covering
    p = lined["p_fair"].to_numpy(dtype=float)
    table = pd.DataFrame(
        {
            "fixture_id": lined["fixture_id"].to_numpy(),
            "market": lined["market"].to_numpy(),
            "period": lined["period"].to_numpy(),
            "line_key": lined["line_key"].to_numpy(dtype=float),
            "cover": np.where(is_main, p, 1.0 - p),
            "main": is_main,
        }
    )
    table = table.sort_values("main", ascending=False, kind="mergesort").drop_duplicates(names)
    return table.set_index(names)["cover"]


def refund_share(legs: pd.DataFrame, fair_frame: pd.DataFrame) -> np.ndarray:
    """Share of the stake that comes back without a win (see the module docstring); NaN where it is unknown.

    ``legs`` needs ``fixture_id, market, period, line``; ``fair_frame`` is the prepared fair frame (with
    ``line_key``).  Zero for lines that cannot refund (half lines, 1X2, BTTS, double chance, exact score).  For an
    integer line ``k`` it is the probability of landing exactly on the line, read off the two neighbouring half
    lines of the same ladder: ``P(cover at k + 0.5) - P(cover at k - 0.5)`` for a handicap (the home side covers
    more the higher the line) and ``P(over k - 0.5) - P(over k + 0.5)`` for a total.  A quarter line stakes half
    on an integer line and half on a half line, so it refunds half of that.  Draw no bet refunds the draw.  A
    ladder that does not give a probability between 0 and 1 (a missing neighbour, stale incoherent prices) gives
    NaN.
    """
    n = len(legs)
    share = np.zeros(n, dtype=float)
    if n == 0:
        return share
    market = legs["market"].to_numpy(dtype=object)
    period = legs["period"].to_numpy(dtype=object)
    fixture = legs["fixture_id"].to_numpy()
    line = legs["line"].to_numpy(dtype=float)
    handicap = np.isin(market, list(_AH_MARKETS))
    lined = handicap | np.isin(market, list(_TOTAL_MARKETS))

    quarters = np.zeros(n, dtype=int)
    quarters[lined & np.isfinite(line)] = np.round(line[lined & np.isfinite(line)] * 4).astype(int)
    remainder = quarters % 4
    whole = lined & np.isfinite(line) & (remainder == 0)
    quarter = lined & np.isfinite(line) & (remainder % 2 == 1)
    share[lined & ~np.isfinite(line)] = np.nan  # a lined market without a line cannot be judged
    need = whole | quarter
    if need.any():
        # the integer line the refund happens on: the line itself, or the integer neighbour of a quarter line
        anchor = np.where(whole, line, np.where(remainder == 1, line - 0.25, line + 0.25))
        cover = _cover_probabilities(fair_frame)

        def look(offset: float) -> np.ndarray:
            index = pd.MultiIndex.from_arrays(
                [fixture[need], market[need], period[need], np.round(anchor[need] + offset, 4)],
                names=list(cover.index.names),
            )
            return cover.reindex(index).to_numpy(dtype=float)

        upper, lower = look(0.5), look(-0.5)
        push = np.where(handicap[need], upper - lower, lower - upper)
        push = np.where((push >= 0.0) & (push < 1.0), push, np.nan)
        share[need] = np.where(whole[need], 1.0, 0.5) * push

    draw_no_bet = market == "dnb"
    if draw_no_bet.any():
        draws = fair_frame[(fair_frame["market"] == "1x2") & (fair_frame["selection"] == "draw")]
        draws = draws.drop_duplicates(["fixture_id", "period"]).set_index(["fixture_id", "period"])["p_fair"]
        index = pd.MultiIndex.from_arrays([fixture[draw_no_bet], period[draw_no_bet]], names=["fixture_id", "period"])
        share[draw_no_bet] = draws.reindex(index).to_numpy(dtype=float)
    return share


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def evaluate_legs_with_diagnostics(
    quotes: pd.DataFrame,
    fair: pd.DataFrame,
    model: pd.DataFrame | None,
    config: ScannerConfig | None = None,
    *,
    as_of: datetime | pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, EvaluationDiagnostics]:
    """Join every usable quote to its fair price and model probability and evaluate it.

    Returns ``(legs, diagnostics)``; :func:`evaluate_legs` is the frame alone.  ``as_of`` is the decision time
    (default: now, UTC); it is not part of the config because it changes every run.
    """
    config = config or ScannerConfig()
    _require_columns(quotes, ("fixture_id", "kickoff_utc", "bookmaker", "market", "period", "line", "selection", "odd", "fetched_at"), "quotes")
    _require_columns(fair, ("fixture_id", "market", "period", "line", "selection", "p_fair"), "fair")
    decision_time = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    decision_time = decision_time.tz_localize("UTC") if decision_time.tzinfo is None else decision_time.tz_convert("UTC")
    diag = EvaluationDiagnostics(n_quotes=len(quotes))

    q = quotes.copy()
    for column in QUOTE_COLUMNS:
        if column not in q.columns:
            q[column] = np.nan
    q = _normalise_keys(q)
    q["odd"] = pd.to_numeric(q["odd"], errors="coerce").astype(float)
    q["kickoff_utc"] = to_utc(q["kickoff_utc"])
    q["fetched_at"] = to_utc(q["fetched_at"])
    q["bookmaker"] = q["bookmaker"].astype("object")

    def drop(mask: pd.Series, counter: str) -> None:
        nonlocal q
        setattr(diag, counter, getattr(diag, counter) + int(mask.sum()))
        q = q[~mask]

    # Rows lost while normalising keys (no fixture id) count as unmapped.
    diag.n_unmapped += len(quotes) - len(q)
    drop(~np.isfinite(q["odd"]) | (q["odd"] <= 1.0), "n_bad_odds")
    drop(~q["market"].isin(list(_FAMILY_OF_MARKET)), "n_unmapped")
    reference = {normalise_bookmaker_name(b) for b in config.reference_bookmakers}
    drop(q["bookmaker"].map(lambda b: normalise_bookmaker_name(b) in reference).astype(bool), "n_reference_dropped")
    drop(q["fetched_at"].isna() | (q["fetched_at"] > decision_time), "n_untimed_or_future")
    # Matches beyond far_lead_hours are refreshed only every few hours by the odds collector: a longer age is allowed.
    lead_hours = (q["kickoff_utc"] - decision_time).dt.total_seconds() / 3600.0
    age_limit = np.where(lead_hours > config.far_lead_hours, config.max_quote_age_far_hours, config.max_quote_age_hours)
    drop((decision_time - q["fetched_at"]).dt.total_seconds() / 3600.0 > age_limit, "n_stale")
    lead = pd.Timedelta(minutes=config.min_minutes_to_kickoff)
    drop(q["kickoff_utc"].isna() | (q["kickoff_utc"] <= decision_time + lead), "n_started")
    if config.max_days_ahead is not None:
        drop(q["kickoff_utc"] > decision_time + pd.Timedelta(days=config.max_days_ahead), "n_beyond_horizon")

    # One current price per bookmaker and leg: the latest observation, the better price on a tie.
    q = q.sort_values(["fetched_at", "odd"], kind="mergesort")
    duplicated = q.duplicated(["bookmaker", *_KEY], keep="last")
    drop(duplicated, "n_duplicate")

    q["book_p"] = book_probability(q)
    fair_frame = _prepare_fair(fair)
    model_frame = _prepare_model(model)
    merged = q.merge(fair_frame, on=_KEY, how="left", validate="many_to_one")
    merged = merged.merge(model_frame, on=_KEY, how="left", validate="many_to_one")

    p_fair = merged["p_fair"].to_numpy(dtype=float)
    p_se = merged["p_se"].to_numpy(dtype=float)
    p_model = merged["p_model"].to_numpy(dtype=float)
    has_market = np.isfinite(p_fair)
    model_only = ~has_market & np.isfinite(p_model) & bool(config.include_model_only)
    priced = has_market | model_only
    diag.n_unpriced = int((~priced).sum())
    merged = merged[priced].copy()
    p_fair, p_se, p_model = p_fair[priced], p_se[priced], p_model[priced]
    has_market, model_only = has_market[priced], model_only[priced]

    odd = merged["odd"].to_numpy(dtype=float)
    # Refunds only exist on lines the fair frame prices; a model-only leg has no ladder to read them from.
    refund = np.where(has_market, refund_share(merged, fair_frame), 0.0)
    kept = 1.0 - np.nan_to_num(refund, nan=0.0)
    p_used = np.where(has_market, p_fair, p_model)
    se_used = np.where(has_market, p_se, config.model_only_se)
    finite_bound = np.isfinite(p_used) & np.isfinite(se_used)
    p_lower = np.where(finite_bound, np.clip(p_used - config.z * np.nan_to_num(se_used), 0.0, 1.0), np.nan)

    out = merged.reset_index(drop=True)
    out["leg_key"] = [
        leg_key(f, m, p, ln, s)
        for f, m, p, ln, s in zip(out["fixture_id"], out["market"], out["period"], out["line"], out["selection"], strict=True)
    ]
    out["market_family"] = [market_family(m, p) for m, p in zip(out["market"], out["period"], strict=True)]
    out["quote_age_min"] = (decision_time - out["fetched_at"]).dt.total_seconds().to_numpy() / 60.0
    out["p_fair"] = np.where(has_market, p_fair, np.nan)
    out["p_se"] = np.where(has_market, p_se, np.nan)
    out["fair_source"] = np.where(has_market, out["fair_source"].astype("object"), "model")
    out["p_model"] = p_model
    out["p_used"] = p_used
    out["p_se_used"] = se_used
    out["p_lower"] = p_lower
    out["fair_odd"] = 1.0 / p_used
    out["edge_raw"] = p_used * odd - 1.0
    out["edge_raw_lower"] = p_lower * odd - 1.0
    out["refund_share"] = refund
    out["book_shift"] = np.where(has_market, merged["book_p"].to_numpy(dtype=float) - p_fair, np.nan)
    out["is_model_only"] = model_only

    with np.errstate(invalid="ignore"):
        agrees = np.where(has_market & np.isfinite(p_model), (p_model >= p_fair - config.model_delta).astype(float), np.nan)
    out["model_agrees"] = agrees

    value_flags: dict[str, np.ndarray] = {}
    for tau in config.taus:
        label = tau_label(tau)
        tau_row = effective_tau(tau, odd, config.stake_pln)
        out[f"ev_net_{label}"] = kept * (tau_row * p_used * odd - 1.0)
        lower = kept * (tau_row * p_lower * odd - 1.0)
        out[f"ev_net_lower_{label}"] = lower
        with np.errstate(invalid="ignore"):
            value_flags[label] = np.isfinite(lower) & (lower > config.min_ev)
        out[f"value_{label}"] = value_flags[label]

    leg_value = value_flags[config.leg_label]
    edge_raw = out["edge_raw"].to_numpy(dtype=float)
    edge_positive = edge_raw > 0.0
    book_shift = out["book_shift"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        suspect = edge_positive & (
            (edge_raw > config.max_edge)
            | (np.nan_to_num(se_used, nan=0.0) > config.max_p_se)
            | (np.abs(np.nan_to_num(book_shift, nan=0.0)) > config.max_book_shift)
        )
    vetoed = bool(config.model_veto) & leg_value & has_market & ~suspect & (agrees == 0.0)
    is_value = leg_value & has_market & ~vetoed & ~suspect
    out["suspect"] = suspect
    out["vetoed"] = vetoed
    out["is_value"] = is_value
    out["is_model_only_value"] = leg_value & model_only & ~suspect
    out["leg_class"] = np.select(
        [is_value, suspect, model_only & edge_positive, has_market & edge_positive],
        ["value", "suspect", "model_only", "near_miss"],
        default="none",
    )
    out["as_of"] = decision_time

    ordered = [
        "leg_key",
        "as_of",
        "fixture_id",
        "league_id",
        "kickoff_utc",
        "bookmaker",
        "bet_id",
        "market",
        "period",
        "line",
        "selection",
        "market_family",
        "odd",
        "fetched_at",
        "quote_age_min",
        "p_fair",
        "p_se",
        "fair_source",
        "devig_method",
        "n_books",
        "book_margin",
        "p_model",
        "model_name",
        "model_agrees",
        "p_used",
        "p_se_used",
        "p_lower",
        "fair_odd",
        "edge_raw",
        "edge_raw_lower",
        "refund_share",
        "book_shift",
    ]
    ev_columns = [c for tau in config.taus for c in (f"ev_net_{tau_label(tau)}", f"ev_net_lower_{tau_label(tau)}", f"value_{tau_label(tau)}")]
    tail = ["suspect", "vetoed", "is_value", "is_model_only", "is_model_only_value", "leg_class"]
    for column in ("devig_method", "n_books", "book_margin", "model_name", "bet_id", "league_id"):
        if column not in out.columns:
            out[column] = np.nan
    out = out[[*ordered, *ev_columns, *tail]]

    diag.n_legs = len(out)
    diag.n_market_fair = int(has_market.sum())
    diag.n_model_only = int(model_only.sum())
    diag.n_edge_positive = int((out["leg_class"] != "none").sum())
    diag.n_value = int(is_value.sum())
    diag.n_vetoed = int(vetoed.sum())
    diag.n_suspect = int(suspect.sum())
    diag.n_fixtures = int(out["fixture_id"].nunique())
    diag.n_bookmakers = int(out["bookmaker"].nunique())
    return out, diag


def evaluate_legs(
    quotes: pd.DataFrame,
    fair: pd.DataFrame,
    model: pd.DataFrame | None,
    config: ScannerConfig | None = None,
    *,
    as_of: datetime | pd.Timestamp | str | None = None,
) -> pd.DataFrame:
    """The legs frame: every usable quote with its fair price, model probability, EV per regime and value flags.

    Columns: the quote identity (``fixture_id, league_id, kickoff_utc, bookmaker, bet_id, market, period, line,
    selection, odd, fetched_at``), ``leg_key``, ``market_family``, the fair price (``p_fair, p_se, fair_source,
    devig_method, n_books, book_margin``), the model (``p_model, model_name, model_agrees``), the probability
    actually used (``p_used, p_se_used, p_lower, fair_odd``), ``edge_raw = p_fair * odd - 1``, ``refund_share`` and
    ``book_shift`` (see the module docstring), per regime
    ``ev_net_t###``, ``ev_net_lower_t###`` and ``value_t###`` (pure arithmetic), and the classification
    ``suspect, vetoed, is_value, is_model_only, is_model_only_value, leg_class`` with ``leg_class`` in ``value |
    suspect | near_miss | model_only | none``.  Quotes at reference bookmakers, stale or started quotes and quotes without any probability are
    absent (see :func:`evaluate_legs_with_diagnostics` for the counts).
    """
    return evaluate_legs_with_diagnostics(quotes, fair, model, config, as_of=as_of)[0]


def _prepare_fair(fair: pd.DataFrame) -> pd.DataFrame:
    f = fair.copy()
    for column in FAIR_COLUMNS:
        if column not in f.columns:
            f[column] = np.nan
    f = _normalise_keys(f)
    f["p_fair"] = pd.to_numeric(f["p_fair"], errors="coerce").astype(float)
    f["p_se"] = pd.to_numeric(f["p_se"], errors="coerce").astype(float)
    f = f[(f["p_fair"] > 0.0) & (f["p_fair"] < 1.0)]
    # A leg with several fair rows keeps the best-informed one (most books), then the first.
    f["n_books"] = pd.to_numeric(f["n_books"], errors="coerce")
    f = f.sort_values("n_books", ascending=False, kind="mergesort", na_position="last")
    f = f.drop_duplicates(_KEY, keep="first")
    return f[[*_KEY, "p_fair", "p_se", "fair_source", "devig_method", "n_books", "book_margin"]]


def _prepare_model(model: pd.DataFrame | None) -> pd.DataFrame:
    columns = [*_KEY, "p_model", "model_name"]
    if model is None or len(model) == 0:
        empty = pd.DataFrame(columns=columns)
        empty["fixture_id"] = empty["fixture_id"].astype("int64")
        empty["line_key"] = empty["line_key"].astype(float)
        for c in ("market", "period", "selection"):
            empty[c] = empty[c].astype("object")
        empty["p_model"] = empty["p_model"].astype(float)
        return empty
    _require_columns(model, ("fixture_id", "market", "period", "line", "selection", "p_model"), "model")
    m = model.copy()
    if "model_name" not in m.columns:
        m["model_name"] = np.nan
    m = _normalise_keys(m)
    m["p_model"] = pd.to_numeric(m["p_model"], errors="coerce").astype(float)
    m = m[(m["p_model"] >= 0.0) & (m["p_model"] <= 1.0)]
    m = m.sort_values(["model_name"], kind="mergesort", na_position="last")
    m = m.drop_duplicates(_KEY, keep="first")  # one row per leg is expected; the first name wins otherwise
    return m[columns]


# --------------------------------------------------------------------------- #
# Value list
# --------------------------------------------------------------------------- #


def select_value_picks(legs: pd.DataFrame, config: ScannerConfig | None = None) -> pd.DataFrame:
    """The value list: value legs, one pick per ``(fixture, bookmaker, market family)``, best first.

    Ranking: lower-bound EV at ``leg_tau`` (value comes first), then the win probability (a higher chance is
    preferred among value legs), then the price.  ``model_only`` legs and legs at reference bookmakers are never
    included.  Adds ``rank`` (1 = best overall).
    """
    config = config or ScannerConfig()
    ev_lower = f"ev_net_lower_{config.leg_label}"
    picks = legs[legs["is_value"] & ~legs["is_model_only"]]
    picks = picks.sort_values(
        [ev_lower, "p_fair", "odd", "leg_key", "bookmaker"],
        ascending=[False, False, False, True, True],
        kind="mergesort",
    )
    picks = picks.drop_duplicates(["fixture_id", "bookmaker", "market_family"], keep="first").reset_index(drop=True)
    picks.insert(0, "rank", np.arange(1, len(picks) + 1))
    return picks


def select_model_only(legs: pd.DataFrame, config: ScannerConfig | None = None, limit: int = 25) -> pd.DataFrame:
    """Model-only legs whose lower-bound EV clears the threshold: research only, never a coupon leg."""
    config = config or ScannerConfig()
    ev_lower = f"ev_net_lower_{config.leg_label}"
    subset = legs[legs["is_model_only_value"]].sort_values([ev_lower, "odd"], ascending=[False, False], kind="mergesort")
    subset = subset.drop_duplicates(["fixture_id", "bookmaker", "market_family"], keep="first")
    return subset.head(limit).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# One scan, end to end
# --------------------------------------------------------------------------- #


@dataclass
class ScanResult:
    """Everything one scan produced (in memory); the register and the page are written from this."""

    scan_id: str
    as_of: pd.Timestamp
    config: ScannerConfig
    legs: pd.DataFrame
    picks: pd.DataFrame
    coupons: CouponSet
    model_only: pd.DataFrame
    diagnostics: EvaluationDiagnostics
    recorded: bool = False
    html: str = ""
    html_paths: list[Path] = field(default_factory=list)
    settled: int = 0
    clv_rows: int = 0


def default_scan_id(as_of: pd.Timestamp) -> str:
    return as_of.strftime("%Y%m%dT%H%M%SZ")


def run_scan(
    quotes: pd.DataFrame,
    fair: pd.DataFrame,
    model: pd.DataFrame | None,
    fixtures: pd.DataFrame,
    *,
    config: ScannerConfig | None = None,
    as_of: datetime | pd.Timestamp | str | None = None,
    scan_id: str | None = None,
    register: ScannerRegister | None = None,
    out_dir: str | Path | None = None,
    results: pd.DataFrame | None = None,
    closing: pd.DataFrame | None = None,
    closing_quotes: pd.DataFrame | None = None,
) -> ScanResult:
    """Evaluate, build coupons, log to the register, settle what can be settled and write the HTML page.

    ``register`` (optional) receives the scan; ``out_dir`` (optional) receives ``latest.html`` and
    ``html/YYYY-MM-DD.html``.  ``results`` (one row per finished fixture, ``fixture_id`` plus the columns of
    :data:`model_buk.scanner.settlement.RESULT_FIELDS`) settles earlier paper legs; ``closing`` (a fair frame
    taken at the last snapshot before kickoff) and optionally ``closing_quotes`` (the closing price per bookmaker,
    quotes-frame columns) give their closing line value.  Both only touch legs recorded by *earlier* scans.
    """
    from model_buk.scanner import report_html
    from model_buk.scanner.coupons import build_coupons

    config = config or ScannerConfig()
    decision_time = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    decision_time = decision_time.tz_localize("UTC") if decision_time.tzinfo is None else decision_time.tz_convert("UTC")
    scan_id = scan_id or default_scan_id(decision_time)
    started_at = pd.Timestamp.now(tz="UTC")

    legs, diag = evaluate_legs_with_diagnostics(quotes, fair, model, config, as_of=decision_time)
    picks = select_value_picks(legs, config)
    coupon_set = build_coupons(picks, config)
    model_only = select_model_only(legs, config)
    result = ScanResult(scan_id, decision_time, config, legs, picks, coupon_set, model_only, diag)

    if register is not None:
        result.recorded = register.record_scan(
            scan_id=scan_id,
            started_at=started_at,
            as_of=decision_time,
            config=config,
            legs=legs,
            coupons=coupon_set,
            diagnostics={**diag.to_dict(), "n_picks": int(len(picks))},
        )
        if results is not None and len(results):
            result.settled = register.settle_pending(results, now=decision_time)
        if closing is not None and len(closing):
            result.clv_rows = register.record_clv(closing, taus=config.taus, closing_quotes=closing_quotes, now=decision_time)

    summary = (
        register.paper_summary(now=decision_time, taus=config.taus, config_id=config.config_id) if register is not None else None
    )
    result.html = report_html.render_page(result, fixtures, summary)
    if out_dir is not None:
        result.html_paths = report_html.write_page(result.html, out_dir, decision_time)
    return result


# --------------------------------------------------------------------------- #
# The daily scan on the real archives
# --------------------------------------------------------------------------- #

ODDS_DB = DATA_ROOT / "api-football-odds" / "odds.sqlite3"
DIRECTORY_DB = DATA_ROOT / "fixture-directory" / "directory.sqlite3"
PREMATCH_DB = DATA_ROOT / "api-football-prematch" / "prematch.sqlite3"


def _fixture_names(fixture_ids: list[int], directory_db: Path, prematch_db: Path) -> pd.DataFrame:
    """Names of the fixtures for the page: the fixture directory, else the prematch archive, else nothing."""
    import sqlite3
    from contextlib import closing

    ids = sorted({int(i) for i in fixture_ids})
    if not ids:
        return pd.DataFrame(columns=list(FIXTURE_COLUMNS))
    if directory_db.exists():
        from model_buk.ingestion.fixture_directory import load_fixtures

        try:
            return load_fixtures(directory_db, fixture_ids=ids)
        except sqlite3.Error:
            pass
    if prematch_db.exists():
        try:
            with closing(sqlite3.connect(f"{prematch_db.as_uri()}?mode=ro", uri=True, timeout=30)) as con:
                marks = ",".join("?" * len(ids))
                return pd.read_sql_query(
                    "SELECT fixture_id, league_id, season, round, kickoff_utc, status_short, home_team_id, home_team, "
                    f"away_team_id, away_team FROM fixtures WHERE fixture_id IN ({marks})",
                    con,
                    params=ids,
                )
        except (sqlite3.Error, pd.errors.DatabaseError):
            pass
    return pd.DataFrame(columns=list(FIXTURE_COLUMNS))


def run_daily_scan(
    *,
    config: ScannerConfig | None = None,
    as_of: datetime | pd.Timestamp | str | None = None,
    odds_db: str | Path = ODDS_DB,
    directory_db: str | Path = DIRECTORY_DB,
    history_db: str | Path | None = None,
    prematch_db: str | Path = PREMATCH_DB,
    register_path: str | Path | None = None,
    out_dir: str | Path | None = SCANNER_DIR,
    model: pd.DataFrame | Any = None,
    settle: bool = True,
) -> ScanResult:
    """One full scan on the real archives: quotes, fair prices, value legs, coupons, register, settlement, CLV, page.

    Reads the current board from the odds archive (:func:`model_buk.scanner.markets.load_current_quotes`, only
    prices seen at or before ``as_of``), prices it (:func:`model_buk.scanner.fair.fair_prices`), records the scan
    in the register and writes the page to ``out_dir`` (``None`` writes no page).  With ``settle`` the paper legs of
    earlier scans whose match is over are settled from the fixture directory and the history archive, and the
    closing line value of legs whose match has started is computed from the last quotes before kickoff (used for
    CLV only).  ``model`` is an optional model frame or a callable ``(quotes, as_of) -> frame``; the models are a
    secondary signal and are absent by default.
    """
    from model_buk.scanner import fair as fair_module
    from model_buk.scanner import markets
    from model_buk.scanner.register import ScannerRegister
    from model_buk.scanner.settlement import HISTORY_DB, load_results

    config = config or ScannerConfig()
    decision_time = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    decision_time = decision_time.tz_localize("UTC") if decision_time.tzinfo is None else decision_time.tz_convert("UTC")
    history = Path(history_db) if history_db is not None else HISTORY_DB

    quotes = markets.load_current_quotes(odds_db, decision_time)
    fair = fair_module.fair_prices(quotes, as_of=decision_time)
    model_frame = model(quotes, decision_time) if callable(model) else model
    fixtures = _fixture_names(quotes["fixture_id"].unique().tolist(), Path(directory_db), Path(prematch_db))
    register = ScannerRegister(register_path)

    results = closing_fair = closing_quotes = None
    if settle:
        pending = register.unsettled_legs()
        if not pending.empty:
            started = pending[to_utc(pending["kickoff_utc"]) <= decision_time]
            if not started.empty:
                results = load_results(started["fixture_id"].unique().tolist(), Path(directory_db), history)
        need = register.paper_legs_without_clv(decision_time)
        if not need.empty:
            closing_quotes = markets.load_closing_quotes(odds_db, need["fixture_id"].unique().tolist())
            if len(closing_quotes):
                closing_fair = fair_module.fair_prices(closing_quotes, max_age_minutes=None)
    return run_scan(
        quotes,
        fair,
        model_frame,
        fixtures,
        config=config,
        as_of=decision_time,
        register=register,
        out_dir=out_dir,
        results=results,
        closing=closing_fair,
        closing_quotes=closing_quotes,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m model_buk.scanner.engine``: run one scan with the default config and print the counts."""
    import argparse

    parser = argparse.ArgumentParser(description="Run one value scan on the real archives (paper mode).")
    parser.add_argument("--as-of", default=None, help="decision time (UTC ISO); default: now")
    parser.add_argument("--out-dir", default=str(SCANNER_DIR), help="folder for latest.html and html/YYYY-MM-DD.html")
    parser.add_argument("--register", default=None, help="path of scanner.sqlite (default: the scanner folder)")
    parser.add_argument("--no-settle", action="store_true", help="skip settlement and closing line value")
    args = parser.parse_args(argv)
    result = run_daily_scan(as_of=args.as_of, out_dir=args.out_dir, register_path=args.register, settle=not args.no_settle)
    diag = result.diagnostics
    print(
        f"scan {result.scan_id} config {result.config.config_id}: recorded={result.recorded} quotes={diag.n_quotes} "
        f"legs={diag.n_legs} value={diag.n_value} suspect={diag.n_suspect} picks={len(result.picks)} "
        f"coupons_passing={result.coupons.n_passing} top={len(result.coupons.top)} settled={result.settled} clv={result.clv_rows}"
    )
    for path in result.html_paths:
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
