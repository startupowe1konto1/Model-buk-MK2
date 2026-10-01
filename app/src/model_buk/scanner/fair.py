"""Fair prices of canonical legs: Pinnacle de-vigged, a market-implied score matrix and a soft-book consensus.

:func:`fair_prices` turns a quotes frame (see :mod:`model_buk.scanner.markets`) into a *fair frame*: for every
leg ``(fixture_id, market, period, line, selection)`` a probability ``p_fair`` with an uncertainty ``p_se`` and
the source that produced it.  The model never overrides a market price: the statistical models of the project
are about 0.013 log-loss worse than de-vigged Pinnacle in 1X2 (``reports/Model kontra rynek - pytanie 2.md``),
so they are a separate frame and this module uses none of them.

Sources, in order of preference
-------------------------------
1. ``pinnacle``: the complementary selections of one line quoted by Pinnacle, de-vigged (default: power method;
   proportional and Shin are options).  A group is used only if it is complete (both sides of a line, all three
   of a 1X2) and its prices are proper (all > 1, overround in a sane range); otherwise the next source decides.
2. ``pinnacle_derived`` for the *exact* algebra of Pinnacle prices: double chance and draw no bet from the
   Pinnacle 1X2 of the same period (``p(1x) = p(home) + p(draw)``: no model involved).
3. ``consensus``: the median of the de-vigged probabilities of all other bookmakers (at least
   ``min_consensus_books``, default 3), renormalised within the set.  Sets with a leg in the tails (below
   ``TAIL_LIMIT`` or above ``1 - TAIL_LIMIT``) rank after the matrix: the soft books' favourite-longshot bias
   survives the de-vig there (below).
4. ``pinnacle_derived`` from the market-implied score matrix (below), for the markets Pinnacle does not quote
   (BTTS, correct score, team totals and totals lines it lacks, second-half markets, odd/even, European handicap,
   win to nil, ...) and for the tails; and from the half split of that matrix (:mod:`model_buk.scanner.halves`,
   ``kind`` ``derived_half``) for the markets on the goals of each half (half time / full time, win both or either
   half, to score in both halves, highest scoring half, first / last team to score) and the first- and
   second-half versions of odd/even and the European handicap.
5. ``consensus`` in the tails, then ``betfair``: Betfair's own de-vigged board as a single-book fallback where
   fewer than ``min_consensus_books`` bookmakers quote the set.  Correct score is never taken from a bookmaker
   board (boards are 30-60% overround and incomplete); it comes from the matrix only.  Yellow cards and the cards
   of a half (:func:`consensus_only`) are priced only by Pinnacle or a consensus: never by Betfair alone, never by
   a model; without such a source they stay unpriced (the pipeline counts them).

``fair_detail`` names the source of every price: ``pinnacle``, ``exact`` (double chance / draw no bet from the
Pinnacle 1X2), ``matrix``, ``half:pinnacle_1h`` / ``half:league`` / ``half:global`` (the half split and where its
shares came from), ``consensus`` or ``betfair``.

The task brief ranks Betfair second, as a near-fair exchange.  The archive says otherwise: "Betfair" in the
API-Football feed is the fixed-odds sportsbook.  Its 1X2 overround is 1.03 to 1.14 (median 1.109; Pinnacle 1.065,
Bet365 1.061), so there is no exchange spread and no commission to subtract, and its distance from the Pinnacle
price (RMS 0.036, common legs) is that of the better soft books (William Hill 0.032, BetVictor 0.033, Bet365 0.042).
It therefore sits in the consensus pool like any other book.  ``betfair_commission`` (default 0) and
:func:`betfair_net_odds` stay for a feed that carries the exchange: with a commission the de-vig runs on net prices.
The order of the sources is otherwise data-driven: the distance from Pinnacle of each source on legs both price is
Betfair 0.040, consensus 0.041 (3 books) to 0.023 (6+ books), score matrix 0.042 (see ``Uncertainty``).
``keep_all=True`` returns every source's row instead of the best one.

What ``p_fair`` means
---------------------
``p_fair`` is the *break-even* probability of the leg: the fair price is ``1 / p_fair``.  For a leg without a
possible refund (a half line, 1X2, BTTS) it is the win probability.  For a line that can push or settle half
(integer and quarter lines) it is ``(P(win) + P(half win)/2) / (1 - P(push) - (P(half win) + P(half loss))/2)``,
which is what de-vigging the two sides of the line gives too; ``p * odd - 1`` then has the right sign as an EV
but is larger than the true EV by the factor ``1 / (that denominator)`` (the settlement engine knows the real
outcomes).  Comparisons are always between identical lines, never across lines.

Market-implied score matrix
---------------------------
For each fixture and period the joint distribution of (home goals, away goals) is fitted, by least squares, to the
Pinnacle prices of that fixture and period: 1X2, every over/under line, every Asian-handicap line and the team
totals.  The default model is a Poisson pair with a Dixon-Coles low-score correction (mean goals of both teams and
``rho``); ``score_model="poisson"`` drops ``rho`` and ``"bivariate"`` uses a common-shock bivariate Poisson.  The
second half is the full-time matrix minus the first-half matrix: independent Poisson with the difference of the
means (halves are independent; this ignores the low-score correction of the full match).  Derived legs are
computed for the legs present in the quotes frame only.  The fit residual (RMSE over the fitted prices) is
returned by :func:`fit_score_matrix` and enters ``p_se``.

Uncertainty ``p_se``
--------------------
``p_se = sqrt((c(h)^2 + extra^2) * p * (1 - p) + tail^2 + abs_err^2)``.

* ``c(h)`` is the measured drift of the sharp line, ``RMS((p_close - p_now) / sqrt(p_now * (1 - p_now)))``, as in
  ``evaluation/market_test.py``: 0.06 for the football-data snapshot (Friday or Tuesday, one to three days before
  kickoff).  A random walk drifts with the square root of the time left, so ``c(h) = c_ref * sqrt(h / h_ref)``,
  clipped to [0.02, 0.09] (``h`` = hours from the decision time to kickoff).
* ``extra`` is how far a source lies from the Pinnacle price where both exist, in the same units, measured on the
  live archive (2026-09-28 to 2026-09-30, about 160 fixtures with Pinnacle boards): Betfair 0.040, consensus
  ``0.07 / sqrt(n_books)`` (0.041 / 0.031 / 0.030 / 0.023 for 3 / 4 / 5 / 6+ books), the score matrix 0.045
  (held-out team totals: 0.042).  Double chance and draw no bet derived from the Pinnacle 1X2 are exact algebra:
  0.  Second-half legs add 0.02 (halves assumed independent).
* The v1.1 markets are priced beyond what the matrix was fitted to, so their derived legs are wider (the choice
  of ``SeModel``): the half split has ``extra`` 0.05 (the matrix's 0.045 and about 0.02 for the split), plus 0.03
  when its shares were not calibrated to Pinnacle's first-half prices (league or global shares), and every derived
  leg of these markets adds ``market_extra`` in quadrature: the largest calibration bias of the market on history,
  ``max |predicted - observed| / sqrt(p (1 - p))`` over its selections, rounded up (``app/scripts/
  measure_half_model.py``: 33 096 matches of 18 leagues 2021-2025, the matrix fitted to Pinnacle's closing 1X2, over
  2.5 and Asian handicap from football-data).  Largest: European handicap 0.045 (the draw of ``home -1``, i.e. a
  one-goal home win: 0.201 predicted, 0.217 observed), half time / full time 0.045 (the comebacks home/away and
  away/home: 0.018 and 0.022 predicted, 0.024 and 0.027 observed), odd/even 0.04 (odd 0.478 / 0.497), first and
  last goal 0.035 (no goal 0.084 / 0.074), to score in both halves 0.035; win to nil, the team versions of both
  halves and the highest scoring half 0.01 to 0.015.  The live matrix is fitted to the whole Pinnacle ladder (more
  lines than the football-data closing prices), so these terms are on the safe side.  On the live archive
  (2026-09-30) the half split agrees with the consensus of the soft books as closely as the consensus agrees with
  itself (RMS of the standardised difference 0.026 for half time / full time, 0.038 highest scoring half, 0.041
  European handicap, 0.012-0.037 odd/even).  With these terms (six hours before kickoff, fit residual 0.01) a leg
  of these markets needs an edge of 10-12% at a probability of 0.5 and 8-9% at 0.7 to pass the lower-bound test
  (a plain matrix leg 9% at 0.5, a Pinnacle leg 3.5%), against the 10% cap of the engine: few of them become value
  legs, by design.
* ``tail`` = ``0.18 * m * (1 - 2m)`` with ``m = min(p, 1 - p)`` for every source but Pinnacle: soft-book
  de-vigging leaves the favourite-longshot bias in the tails (consensus and Betfair legs below 20% are 3 to 4
  points lower than the matrix, above 80% higher), and the ``p * (1 - p)`` scaling shrinks to nothing there.
* ``abs_err`` is an absolute error in probability: the matrix fit RMSE for derived legs, and for direct Pinnacle
  legs the *incoherence* with the rest of the Pinnacle ladder, ``max(|q - q_matrix| - 2.5 * sigma, 0)`` with
  ``sigma`` the robust (MAD) scatter of the fit residuals of that fixture and period (>= 8 fitted prices).  The
  provider's feed carries stale ladder lines (a handicap line unchanged for a day, identical prices on
  neighbouring lines: residuals of 0.05 to 0.08 against a typical 0.01); those keep their price but get a wide
  ``p_se`` so that they cannot pass a lower-bound value test.

Board time of a fair price
--------------------------
``fair_board_at`` is when the board the price was taken from was produced: the provider's own update time of the
board (``api_update``) where the quotes frame carries it, else the time the board was last seen (``fetched_at``).
A Pinnacle price and every price derived from Pinnacle (double chance, draw no bet, the score matrix) carry the
time of Pinnacle's board of the fixture, a Betfair price that of Betfair's board and a consensus price the
*oldest* board among the bookmakers it was taken from.  The engine compares a soft quote only with a fair price
from the same provider snapshot (``sync_window_minutes``, see :mod:`model_buk.scanner.engine`).  NaT where the
quotes frame has no times.

Calibration check (archive 2026-09-30, legs both a source and Pinnacle price): ``(p_source - p_pinnacle) / p_se``
has an RMS of 0.65 for the consensus (0.80 with 3 books, 0.55 with 4 to 6) and 0.74 for Betfair, so the standard
error is conservative by design (it also carries the drift of the reference itself); in the tails (Pinnacle below
15%) it was 1.33 before the tail term was widened to 0.18.  The archive is three days old: ``c_ref``, ``h_ref``
and every ``extra`` should be re-measured when weeks of quotes exist (the ``SeModel`` fields are parameters).

Caveat on the drift constants: they are measured between archive snapshots, and every snapshot is a provider
snapshot that is itself hours old (``provider_age_minutes`` of the quote frames: median about 3.8 hours, see
:mod:`model_buk.scanner.markets`).  Two archive snapshots one hour before kickoff and at the close are often the
same provider snapshot (Pinnacle 1X2 at one hour before kickoff against the last price before kickoff: unchanged
in 66% of 102 finished fixtures, at three hours before kickoff in 49% of 83; the last price before kickoff was
itself seen a median of 2 hours ahead), so the measured ``c`` at short leads understates the drift over the same
clock time.  The floor ``c_min`` (0.02) is the only guard at short leads; it was not validated against a
true real-time close and should be revisited once the near-kickoff collector has weeks of data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.stats import poisson

from model_buk.markets.pricing import DEVIG_METHODS, devig_probabilities, normalise_bookmaker_name
from model_buk.scanner.halves import DEFAULT_HALF_SHARES, HalfShares, HalfSplit, half_leg_probability, split_fixture
from model_buk.scanner.markets import HALF_MARKETS_V11, MATRIX_MARKETS_V11, SELECTIONS, YELLOW_MARKETS

FAIR_COLUMNS: Final[tuple[str, ...]] = (
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
    "fair_board_at",
    "fair_detail",
)

DEFAULT_METHOD: Final = "power"
PINNACLE: Final = "pinnacle"
BETFAIR: Final = "betfair"
DEFAULT_BETFAIR_COMMISSION: Final = 0.0
"""The feed's "Betfair" is the sportsbook (overround 1.03-1.14), so no commission; 0.02-0.05 for an exchange feed."""
TAIL_LIMIT: Final = 0.15
"""A consensus set with a leg whose ``min(p, 1 - p)`` is below this ranks after the matrix (the whole set)."""
DEFAULT_MIN_CONSENSUS_BOOKS: Final = 3
DEFAULT_MAX_AGE_MINUTES: Final = 180.0
DEFAULT_FAR_LEAD_HOURS: Final = 48.0
"""Matches further ahead than this may use ``max_age_far_minutes`` instead of ``max_age_minutes``."""

MIN_OVERROUND: Final = 0.995
"""A complete set whose prices sum to less than this (before commission for Betfair) is crossed or stale."""
MAX_OVERROUND: Final = 1.60
"""A complete set above this is not informative (a longshot-heavy board)."""

_SETS = dict(SELECTIONS)
_SORTED_SETS = {market: tuple(sorted(selections)) for market, selections in _SETS.items()}
_NO_LINE_KEY: Final = -9999.0
_GRID: Final = 16
"""Goals 0..15 per team are kept in every score matrix (mass beyond is renormalised away)."""

PRIORITY: Final[dict[str, int]] = {
    "pinnacle": 0,
    "derived_exact": 1,
    "consensus": 2,
    "derived_matrix": 3,
    "derived_half": 3,
    "betfair": 5,
}
"""Preference between sources of one leg (lower wins); a tail consensus ranks 4 (after the matrix and the half
split, which never price the same leg)."""


def consensus_only(market: object, period: object) -> bool:
    """Legs priced only from Pinnacle or a consensus of at least ``min_consensus_books`` (never Betfair's board
    alone, never a model): yellow cards, and cards of a half."""
    market = str(market)
    return market in YELLOW_MARKETS or (market.startswith("cards_") and str(period) in ("1h", "2h"))


# --------------------------------------------------------------------------- #
# De-vigging one complete set
# --------------------------------------------------------------------------- #


def overround(market: str, odds: Sequence[float]) -> float:
    """Sum of implied probabilities of a complete set, scaled so that a fair book gives 1.0.

    Double chance sums to 2 in a fair book, so its raw sum is halved.
    """
    total = sum(1.0 / float(o) for o in odds)
    return total / 2.0 if market == "dc" else total


def devig_set(market: str, odds: Sequence[float], method: str = DEFAULT_METHOD) -> tuple[float, ...]:
    """Fair probabilities of one complete set of a market, in the order of ``odds``.

    The three double-chance prices overlap (``p1x + px2 + p12 = 2``); they are halved (so they sum to one), de-vigged
    with ``method`` and doubled again.  Every other market is de-vigged as it stands.  ``method`` is any key of
    :data:`model_buk.markets.pricing.DEVIG_METHODS`.
    """
    if method not in DEVIG_METHODS:
        raise ValueError(f"unknown de-vig method {method!r}; expected one of: {', '.join(sorted(DEVIG_METHODS))}")
    if market == "dc":
        return tuple(2.0 * p for p in devig_probabilities([2.0 * float(o) for o in odds], method))
    return devig_probabilities([float(o) for o in odds], method)


def betfair_net_odds(odd: float | np.ndarray, commission: float) -> float | np.ndarray:
    """Net decimal price of a back bet on the exchange after ``commission`` on the winnings."""
    if not 0.0 <= commission < 1.0:
        raise ValueError("commission must be in [0, 1)")
    return 1.0 + (np.asarray(odd, dtype=float) - 1.0) * (1.0 - commission)


# --------------------------------------------------------------------------- #
# Uncertainty model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SeModel:
    """Standard error of a fair probability (see "Uncertainty" in the module docstring).

    ``c_ref`` is the measured drift of the Pinnacle line from a snapshot ``h_ref`` hours before kickoff to the
    close (0.059-0.065 on football-data, ``evaluation/market_test.py``); ``exponent`` 0.5 is a random walk.
    ``extra`` maps a source key (``pinnacle``, ``derived_exact``, ``betfair``, ``derived_matrix``, ``consensus``)
    to its additional scale in the units of ``c`` (consensus: divided by ``sqrt(n_books)``); ``tail_relative``
    is the relative tail term of the non-Pinnacle sources; ``extra_second_half`` is added in quadrature to
    derived second-half legs.  ``market_extra`` adds a per-market scale (quadrature) to the derived legs of the
    markets priced from a matrix beyond the ones it was fitted to (odd/even, European handicap, win to nil and
    the half-split markets), and ``extra_half_uncalibrated`` is added to half-split legs whose shares were not
    calibrated to Pinnacle's first-half prices (league or global shares).
    """

    c_ref: float = 0.06
    h_ref: float = 48.0
    exponent: float = 0.5
    c_min: float = 0.02
    c_max: float = 0.09
    extra: Mapping[str, float] = field(
        default_factory=lambda: {
            "pinnacle": 0.0,
            "derived_exact": 0.0,
            "betfair": 0.04,
            "derived_matrix": 0.045,
            "derived_half": 0.05,
            "consensus": 0.07,
        }
    )
    tail_relative: float = 0.18
    extra_second_half: float = 0.02
    market_extra: Mapping[str, float] = field(
        default_factory=lambda: {
            # the largest calibration bias of the market on history, in the units of ``c``, rounded up
            # (``app/scripts/measure_half_model.py``: 33 096 matches 2021-2025, see the module docstring)
            "odd_even": 0.04,
            "odd_even_home": 0.02,
            "odd_even_away": 0.02,
            "eh": 0.045,
            "win_to_nil_home": 0.01,
            "win_to_nil_away": 0.01,
            "ht_ft": 0.045,
            "win_both_halves_home": 0.025,
            "win_both_halves_away": 0.025,
            "win_either_half_home": 0.03,
            "win_either_half_away": 0.03,
            "score_both_halves": 0.035,
            "score_both_halves_home": 0.01,
            "score_both_halves_away": 0.01,
            "btts_both_halves": 0.015,
            "highest_half": 0.015,
            "highest_half_home": 0.015,
            "highest_half_away": 0.015,
            "first_goal": 0.035,
            "last_goal": 0.035,
        }
    )
    extra_half_uncalibrated: float = 0.03

    def c(self, lead_hours: np.ndarray | float) -> np.ndarray:
        """Drift scale at ``lead_hours`` before kickoff (``c_ref`` where the lead is unknown)."""
        lead = np.asarray(lead_hours, dtype=float)
        lead = np.where(np.isfinite(lead), np.clip(lead, 0.0, None), self.h_ref)
        scale = self.c_ref * (lead / self.h_ref) ** self.exponent
        return np.clip(scale, self.c_min, self.c_max)

    def p_se(
        self,
        p: np.ndarray,
        lead_hours: np.ndarray | float,
        source_key: Sequence[str],
        *,
        n_books: np.ndarray | float = 1,
        abs_err: np.ndarray | float = 0.0,
        second_half: np.ndarray | bool = False,
        markets: Sequence[str] | None = None,
        half_uncalibrated: np.ndarray | bool = False,
    ) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        n = np.clip(np.asarray(n_books, dtype=float), 1.0, None)
        keys = np.asarray(list(source_key), dtype=object)
        extra = np.array([self.extra.get(str(k), self.extra["consensus"]) for k in keys], dtype=float)
        extra = np.where(keys == "consensus", extra / np.sqrt(n), extra)
        extra = np.sqrt(extra**2 + np.where(np.asarray(second_half, dtype=bool), self.extra_second_half**2, 0.0))
        derived = np.isin(keys, ("derived_matrix", "derived_half"))
        if markets is not None and derived.any():
            per_market = np.array([self.market_extra.get(str(m), 0.0) for m in markets], dtype=float)
            extra = np.sqrt(extra**2 + np.where(derived, per_market, 0.0) ** 2)
        uncalibrated = np.asarray(half_uncalibrated, dtype=bool) & (keys == "derived_half")
        extra = np.sqrt(extra**2 + np.where(uncalibrated, self.extra_half_uncalibrated**2, 0.0))
        sharp = np.isin(keys, ("pinnacle", "derived_exact"))
        m = np.minimum(p, 1.0 - p)
        tail = np.where(sharp, 0.0, self.tail_relative * m * (1.0 - 2.0 * m))
        err = np.asarray(abs_err, dtype=float)
        variance = (self.c(lead_hours) ** 2 + extra**2) * p * (1.0 - p) + tail**2 + np.where(np.isfinite(err), err, 0.0) ** 2
        return np.sqrt(np.clip(variance, 0.0, None))


DEFAULT_SE_MODEL: Final = SeModel()


# --------------------------------------------------------------------------- #
# Score matrix
# --------------------------------------------------------------------------- #

SCORE_MODELS: Final = ("poisson", "dc", "bivariate")


def score_matrix(
    mu_home: float,
    mu_away: float,
    *,
    rho: float = 0.0,
    common: float = 0.0,
    size: int = _GRID,
) -> np.ndarray:
    """Joint pmf of (home goals, away goals), ``[home, away]``, truncated at ``size - 1`` goals and renormalised.

    ``mu_home`` and ``mu_away`` are the mean goals of the two teams.  ``common`` (>= 0) is the rate of a shared
    Poisson shock (bivariate Poisson; the team rates are ``mu - common``, the means stay ``mu``).  ``rho`` is the
    Dixon-Coles low-score correction: the cells 0-0, 0-1, 1-0 and 1-1 are multiplied by
    ``1 - l1 * l2 * rho``, ``1 + l1 * rho``, ``1 + l2 * rho`` and ``1 - rho`` with the team rates ``l1, l2``.
    """
    if mu_home <= 0.0 or mu_away <= 0.0:
        raise ValueError("mean goals must be positive")
    common = float(max(common, 0.0))
    l1 = max(mu_home - common, 1e-9)
    l2 = max(mu_away - common, 1e-9)
    goals = np.arange(size)
    p1 = poisson.pmf(goals, l1)
    p2 = poisson.pmf(goals, l2)
    if common <= 0.0:
        matrix = np.outer(p1, p2)
    else:
        p3 = poisson.pmf(goals, common)
        matrix = np.zeros((size, size))
        for k in range(size):
            matrix[k:, k:] += p3[k] * np.outer(p1[: size - k], p2[: size - k])
    if rho != 0.0:
        matrix = matrix.copy()
        matrix[0, 0] *= max(1.0 - l1 * l2 * rho, 0.0)
        matrix[0, 1] *= max(1.0 + l1 * rho, 0.0)
        matrix[1, 0] *= max(1.0 + l2 * rho, 0.0)
        matrix[1, 1] *= max(1.0 - rho, 0.0)
    total = matrix.sum()
    if not total > 0.0:
        raise ValueError("degenerate score matrix")
    return matrix / total


def _sublines(line: float) -> tuple[float, ...]:
    """A quarter line is two half stakes on the neighbouring lines; other lines are themselves."""
    quarters = round(line * 4.0)
    if quarters % 2 == 0:
        return (float(line),)
    return (line - 0.25, line + 0.25)


@lru_cache(maxsize=4096)
def leg_masks(market: str, selection: str, line: float, size: int = _GRID) -> tuple[np.ndarray, np.ndarray] | None:
    """Break-even masks ``(N, D)`` of a leg over the score grid: ``q = sum(M * N) / sum(M * D)``.

    With cell probabilities ``M`` a leg with sub-line results ``r`` wins where ``r > 0`` and loses where ``r < 0``
    (a push is ``r == 0``); a quarter line averages two sub-lines.  A unit stake at price ``o`` has expected profit
    ``o * sum(M * N) - sum(M * D)``, so the break-even probability of the price is ``N / D``.  Returns None for a
    market that cannot be derived from goals (corners, cards) or an unknown selection.
    """
    goals = np.arange(size)
    x = goals[:, None].astype(float)
    y = goals[None, :].astype(float)
    ones = np.ones((size, size))
    if market == "1x2":
        wins = {"home": x > y, "draw": x == y, "away": x < y}.get(selection)
        return None if wins is None else (wins.astype(float), ones)
    if market == "dc":
        wins = {"1x": x >= y, "x2": x <= y, "12": x != y}.get(selection)
        return None if wins is None else (wins.astype(float), ones)
    if market == "dnb":
        wins = {"home": x > y, "away": x < y}.get(selection)
        return None if wins is None else (wins.astype(float), (x != y).astype(float))
    if market == "btts":
        both = (x > 0) & (y > 0)
        wins = {"yes": both, "no": ~both}.get(selection)
        return None if wins is None else (wins.astype(float), ones)
    if market == "cs":
        try:
            home_goals, away_goals = (int(v) for v in selection.split(":"))
        except ValueError:
            return None
        return ((x == home_goals) & (y == away_goals)).astype(float), ones
    if market in ("odd_even", "odd_even_home", "odd_even_away"):
        stat = {"odd_even": x + y, "odd_even_home": x + 0.0 * y, "odd_even_away": 0.0 * x + y}[market]
        wins = {"odd": stat % 2 == 1, "even": stat % 2 == 0}.get(selection)
        return None if wins is None else (wins.astype(float), ones)
    if market in ("win_to_nil_home", "win_to_nil_away"):
        event = (x > y) & (y == 0) if market == "win_to_nil_home" else (y > x) & (x == 0)
        wins = {"yes": event, "no": ~event}.get(selection)
        return None if wins is None else (wins.astype(float), ones)
    if market == "eh":
        # European (three-way) handicap: ``line`` is the whole-goal handicap of the home team, no refund
        if not math.isfinite(line) or abs(line - round(line)) > 1e-9:
            return None
        margin = x - y + round(line)
        wins = {"home": margin > 0, "draw": margin == 0, "away": margin < 0}.get(selection)
        return None if wins is None else (wins.astype(float), ones)

    def stat_result(stat: np.ndarray, sub: float, side: str) -> np.ndarray:
        return stat - sub if side == "over" else sub - stat

    if market in ("ou", "team_ou_home", "team_ou_away"):
        if selection not in ("over", "under") or not math.isfinite(line):
            return None
        stat = {"ou": x + y, "team_ou_home": x + 0.0 * y, "team_ou_away": 0.0 * x + y}[market]
        results = [stat_result(stat, sub, selection) for sub in _sublines(line)]
    elif market == "ah":
        if selection not in ("home", "away") or not math.isfinite(line):
            return None
        sign = 1.0 if selection == "home" else -1.0
        results = [sign * (x - y + sub) for sub in _sublines(line)]
    else:
        return None
    wins = np.mean([(r > 0).astype(float) for r in results], axis=0)
    losses = np.mean([(r < 0).astype(float) for r in results], axis=0)
    return wins, wins + losses


def matrix_leg_probability(matrix: np.ndarray, market: str, selection: str, line: float = float("nan")) -> float:
    """Break-even probability of a leg under a score matrix (NaN if the leg cannot be derived from goals)."""
    if market == "cs":
        try:
            home_goals, away_goals = (int(v) for v in selection.split(":"))
        except ValueError:
            return float("nan")
        if home_goals >= matrix.shape[0] or away_goals >= matrix.shape[1]:
            return float("nan")
        return float(matrix[home_goals, away_goals])
    masks = leg_masks(market, selection, float(line) if math.isfinite(line) else float("nan"), matrix.shape[0])
    if masks is None:
        return float("nan")
    numerator, denominator = masks
    total = float((matrix * denominator).sum())
    return float((matrix * numerator).sum() / total) if total > 0.0 else float("nan")


@dataclass(frozen=True)
class ScoreMatrixFit:
    """A fitted score matrix and how well it reproduces the prices it was fitted to."""

    matrix: np.ndarray
    mu_home: float
    mu_away: float
    rho: float
    common: float
    rmse: float
    max_abs_residual: float
    n_targets: int
    model: str

    @property
    def params(self) -> dict[str, float]:
        return {"mu_home": self.mu_home, "mu_away": self.mu_away, "rho": self.rho, "common": self.common}


@dataclass(frozen=True)
class FitTarget:
    """One price the matrix should reproduce: a leg and its de-vigged break-even probability."""

    market: str
    selection: str
    line: float
    q: float
    weight: float = 1.0


_RHO_BOUND: Final = 0.30


def _unpack(x: np.ndarray, model: str) -> tuple[float, float, float, float]:
    mu_home, mu_away = math.exp(x[0]), math.exp(x[1])
    rho = float(x[2]) if model == "dc" else 0.0
    common = float(x[2]) if model == "bivariate" else 0.0
    return mu_home, mu_away, rho, common


def fit_score_matrix(
    targets: Sequence[FitTarget],
    *,
    model: str = "dc",
    size: int = _GRID,
    initial: tuple[float, float] = (1.4, 1.1),
) -> ScoreMatrixFit | None:
    """Least-squares fit of a score matrix to de-vigged prices.

    Every target contributes ``weight * (q_model - q_target)``; a two-way market needs one target per line (the
    other side is one minus it).  At least three targets and a 1X2-like or handicap price plus a total are
    needed for the means to be identified; otherwise None.  ``model`` is one of :data:`SCORE_MODELS`.
    """
    if model not in SCORE_MODELS:
        raise ValueError(f"unknown score model {model!r}; expected one of: {', '.join(SCORE_MODELS)}")
    rows: list[tuple[np.ndarray, np.ndarray, float, float]] = []
    for target in targets:
        if not (math.isfinite(target.q) and 0.0 < target.q < 1.0):
            continue
        masks = leg_masks(target.market, target.selection, float(target.line) if math.isfinite(target.line) else float("nan"), size)
        if masks is None:
            continue
        rows.append((masks[0].ravel(), masks[1].ravel(), float(target.q), float(target.weight)))
    if len(rows) < 3:
        return None
    markets = {t.market for t in targets}
    if not ({"1x2", "ah"} & markets) or not ({"ou"} & markets):
        return None
    numerator = np.vstack([r[0] for r in rows])
    denominator = np.vstack([r[1] for r in rows])
    q = np.array([r[2] for r in rows])
    weight = np.array([r[3] for r in rows])

    def build(x: np.ndarray) -> np.ndarray:
        mu_home, mu_away, rho, common = _unpack(x, model)
        common = min(common, 0.95 * min(mu_home, mu_away))
        return score_matrix(mu_home, mu_away, rho=rho, common=common, size=size).ravel()

    def residual(x: np.ndarray) -> np.ndarray:
        cells = build(x)
        return weight * ((numerator @ cells) / np.clip(denominator @ cells, 1e-12, None) - q)

    x0 = [math.log(initial[0]), math.log(initial[1])]
    lower = [math.log(0.05), math.log(0.05)]
    upper = [math.log(8.0), math.log(8.0)]
    if model == "dc":
        x0.append(0.0)
        lower.append(-_RHO_BOUND)
        upper.append(_RHO_BOUND)
    elif model == "bivariate":
        x0.append(0.05)
        lower.append(0.0)
        upper.append(1.5)
    try:
        solution = optimize.least_squares(residual, np.array(x0), bounds=(lower, upper), method="trf", x_scale=1.0, xtol=1e-10, ftol=1e-12)
    except (ValueError, FloatingPointError):
        return None
    mu_home, mu_away, rho, common = _unpack(solution.x, model)
    common = min(common, 0.95 * min(mu_home, mu_away))
    matrix = score_matrix(mu_home, mu_away, rho=rho, common=common, size=size)
    plain = (numerator @ matrix.ravel()) / np.clip(denominator @ matrix.ravel(), 1e-12, None) - q
    return ScoreMatrixFit(
        matrix=matrix,
        mu_home=mu_home,
        mu_away=mu_away,
        rho=rho,
        common=common,
        rmse=float(np.sqrt(np.mean(plain**2))),
        max_abs_residual=float(np.max(np.abs(plain))),
        n_targets=len(rows),
        model=model,
    )


def second_half_matrix(full: ScoreMatrixFit, first: ScoreMatrixFit, *, size: int = _GRID) -> np.ndarray | None:
    """Independent-Poisson matrix of the second half: means of the full match minus means of the first half.

    ``None`` when a second-half mean would be non-positive (the two fits are inconsistent).
    """
    full_home, full_away = matrix_means(full.matrix)
    first_home, first_away = matrix_means(first.matrix)
    mu_home, mu_away = full_home - first_home, full_away - first_away
    if mu_home <= 0.03 or mu_away <= 0.03:
        return None
    return score_matrix(mu_home, mu_away, size=size)


def matrix_means(matrix: np.ndarray) -> tuple[float, float]:
    """Mean home and away goals of a score matrix."""
    goals = np.arange(matrix.shape[0], dtype=float)
    return float((matrix.sum(axis=1) * goals).sum()), float((matrix.sum(axis=0) * goals).sum())


# --------------------------------------------------------------------------- #
# Direct sources: de-vig every complete set of every bookmaker
# --------------------------------------------------------------------------- #


def _prepare(
    quotes: pd.DataFrame,
    max_age_minutes: float | None,
    max_age_far_minutes: float | None = None,
    far_lead_hours: float = DEFAULT_FAR_LEAD_HOURS,
) -> pd.DataFrame:
    needed = ["fixture_id", "bookmaker", "market", "period", "line", "selection", "odd"]
    missing = [c for c in needed if c not in quotes.columns]
    if missing:
        raise ValueError(f"quotes frame is missing columns: {', '.join(missing)}")
    columns = needed + [c for c in ("kickoff_utc", "age_minutes", "three_way", "league_id") if c in quotes.columns]
    q = quotes[columns].copy()
    q["board_at"] = board_times(quotes)
    odd = pd.to_numeric(q["odd"], errors="coerce").to_numpy(dtype=float)
    keep = np.isfinite(odd) & (odd > 1.0) & q["market"].isin([*_SETS, "cs"]).to_numpy()
    if "three_way" in q.columns:
        keep &= ~q["three_way"].fillna(False).to_numpy(dtype=bool)
    if max_age_minutes is not None and "age_minutes" in q.columns:
        age = pd.to_numeric(q["age_minutes"], errors="coerce").to_numpy(dtype=float)
        limit = np.full(len(q), float(max_age_minutes))
        if max_age_far_minutes is not None and {"kickoff_utc", "fetched_at"} <= set(quotes.columns):
            # lead time at the reference time of ``age_minutes`` (= fetched_at + age): matches far ahead are refreshed
            # only every few hours by the odds collector and may have an older board
            kickoff = pd.to_datetime(quotes["kickoff_utc"], utc=True, errors="coerce")
            fetched = pd.to_datetime(quotes["fetched_at"], utc=True, errors="coerce")
            lead = ((kickoff - fetched).dt.total_seconds() / 3600.0).to_numpy(dtype=float) - age / 60.0
            limit = np.where(lead > float(far_lead_hours), float(max_age_far_minutes), limit)
        keep &= ~(age > limit)
    q = q[keep].copy()
    q["odd"] = odd[keep]
    q["fixture_id"] = q["fixture_id"].astype("int64")
    q["bk"] = q["bookmaker"].map(normalise_bookmaker_name)
    q["line_key"] = q["line"].astype(float).round(4).fillna(_NO_LINE_KEY)
    key = ["fixture_id", "bk", "market", "period", "line_key", "selection"]
    q = q.sort_values("odd", kind="mergesort")
    q = q[~q.duplicated(key, keep="last")]
    return q.reset_index(drop=True)


def board_times(quotes: pd.DataFrame) -> pd.Series:
    """Board time of every quote row: the provider's ``api_update``, else ``fetched_at``, else NaT (UTC)."""
    out = pd.Series(pd.NaT, index=quotes.index, dtype="datetime64[ns, UTC]")
    for column in ("fetched_at", "api_update"):  # the later one wins where it is known
        if column not in quotes.columns:
            continue
        values = quotes[column]
        if not pd.api.types.is_datetime64_any_dtype(values):
            values = pd.to_datetime(values, utc=True, errors="coerce", format="ISO8601")
        elif getattr(values.dt, "tz", None) is None:
            values = values.dt.tz_localize("UTC")
        values = values.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
        out = values.where(values.notna(), out)
    return out


def _board_table(prepared: pd.DataFrame) -> pd.Series:
    """Oldest board time per ``(fixture_id, bk)`` of a prepared frame (one board per bookmaker and fixture)."""
    if prepared.empty or "board_at" not in prepared.columns:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return prepared.groupby(["fixture_id", "bk"])["board_at"].min()


def _devig_all(prepared: pd.DataFrame, method: str, commission: float) -> pd.DataFrame:
    """De-vig every complete set of every bookmaker: one row per (bookmaker, leg) with ``p`` and the overround.

    Betfair sets are de-vigged on commission-adjusted prices; the reported ``margin`` is that of the quoted prices.
    """
    parts: list[pd.DataFrame] = []
    for market, selections in _SORTED_SETS.items():
        sub = prepared[prepared["market"] == market]
        if sub.empty:
            continue
        wide = sub.pivot(index=["fixture_id", "bk", "period", "line_key"], columns="selection", values="odd")
        if any(s not in wide.columns for s in selections):
            continue
        wide = wide[list(selections)].dropna()
        if wide.empty:
            continue
        odds = wide.to_numpy(dtype=float)
        index = wide.index.to_frame(index=False)
        is_betfair = (index["bk"] == BETFAIR).to_numpy()
        used = np.where(is_betfair[:, None], 1.0 + (odds - 1.0) * (1.0 - commission), odds)
        raw_sum = (1.0 / odds).sum(axis=1)
        used_sum = (1.0 / used).sum(axis=1)
        scale = 2.0 if market == "dc" else 1.0
        margin = raw_sum / scale - 1.0
        ok = (used_sum / scale >= MIN_OVERROUND) & (raw_sum / scale <= MAX_OVERROUND)
        probs = np.full(odds.shape, np.nan)
        for i in np.flatnonzero(ok):
            try:
                probs[i] = devig_set(market, used[i], method)
            except ValueError:  # a set the solver cannot fit is not a usable price
                continue
        good = ok & np.isfinite(probs).all(axis=1)
        if not good.any():
            continue
        frame = index[good].reset_index(drop=True)
        long = []
        for j, selection in enumerate(selections):
            piece = frame.copy()
            piece["market"] = market
            piece["selection"] = selection
            piece["p"] = probs[good, j]
            piece["margin"] = margin[good]
            long.append(piece)
        parts.append(pd.concat(long, ignore_index=True))
    if not parts:
        return pd.DataFrame(columns=["fixture_id", "bk", "period", "line_key", "market", "selection", "p", "margin"])
    return pd.concat(parts, ignore_index=True)


def _consensus(devigged: pd.DataFrame, min_books: int) -> pd.DataFrame:
    """Median probability over bookmakers per leg, renormalised within its set; at least ``min_books`` books."""
    if devigged.empty:
        return devigged.assign(n_books=pd.Series(dtype="int64"))
    key = ["fixture_id", "market", "period", "line_key"]
    grouped = devigged.groupby([*key, "selection"], sort=False)
    aggregations = {"p": ("p", "median"), "margin": ("margin", "median"), "n_books": ("bk", "nunique")}
    if "board_at" in devigged.columns:
        aggregations["board_at"] = ("board_at", "min")
    med = grouped.agg(**aggregations).reset_index()
    set_n = med.groupby(key)["n_books"].transform("min")
    med = med[set_n >= min_books].copy()
    if med.empty:
        return med
    target = np.where(med["market"] == "dc", 2.0, 1.0)
    total = med.groupby(key)["p"].transform("sum")
    med["p"] = med["p"] / total * target
    med["n_books"] = set_n[med.index].astype("int64")
    return med


# --------------------------------------------------------------------------- #
# Derived source: the score matrix from Pinnacle prices
# --------------------------------------------------------------------------- #

_FIT_MARKETS: Final = ("1x2", "ou", "ah", "team_ou_home", "team_ou_away")
_MATRIX_MARKETS: Final = (
    "1x2", "dc", "dnb", "ah", "ou", "team_ou_home", "team_ou_away", "btts", "cs",
    *sorted(MATRIX_MARKETS_V11), *sorted(HALF_MARKETS_V11),
)  # fmt: skip


def _targets_from_pinnacle(rows: pd.DataFrame) -> list[FitTarget]:
    """Fit targets of one fixture and period from Pinnacle rows (one target per set line, the first selection)."""
    targets: list[FitTarget] = []
    for record in rows[rows["market"].isin(_FIT_MARKETS)].itertuples(index=False):
        first = {"1x2": ("home", "draw", "away")}.get(record.market, ("home", "over"))
        if record.selection not in first:
            continue
        line = float("nan") if record.line_key == _NO_LINE_KEY else float(record.line_key)
        targets.append(FitTarget(record.market, record.selection, line, float(record.p)))
    return targets


@dataclass
class _FixtureMatrices:
    ft: ScoreMatrixFit | None = None
    first: ScoreMatrixFit | None = None
    second: np.ndarray | None = None
    second_rmse: float = float("nan")
    first_targets: list[FitTarget] = field(default_factory=list)
    _half: HalfSplit | None = None

    def half_split(self, league_shares: tuple[float, float, str]) -> HalfSplit | None:
        """The half split of the full-time matrix (:mod:`model_buk.scanner.halves`): calibrated to Pinnacle's
        first-half prices when there are any, else ``league_shares``; built on first use."""
        if self.ft is None:
            return None
        if self._half is None:
            self._half = split_fixture(self.ft.matrix, league_shares, self.first_targets)
        return self._half


def fit_fixture_matrices(pinnacle: pd.DataFrame, *, model: str = "dc") -> dict[int, _FixtureMatrices]:
    """Full-time, first-half and second-half score matrices per fixture from Pinnacle's de-vigged rows."""
    result: dict[int, _FixtureMatrices] = {}
    if pinnacle.empty:
        return result
    for (fixture_id, period), rows in pinnacle[pinnacle["period"].isin(("ft", "1h"))].groupby(["fixture_id", "period"], sort=False):
        targets = _targets_from_pinnacle(rows)
        fit = fit_score_matrix(targets, model=model)
        entry = result.setdefault(int(fixture_id), _FixtureMatrices())
        if period == "ft":
            entry.ft = fit
        else:
            entry.first = fit
            entry.first_targets = targets
    for entry in result.values():
        if entry.ft is not None and entry.first is not None:
            entry.second = second_half_matrix(entry.ft, entry.first)
            if entry.second is not None:
                entry.second_rmse = float(math.hypot(entry.ft.rmse, entry.first.rmse))
    return result


MIN_TARGETS_FOR_COHERENCE: Final = 8
COHERENCE_SIGMAS: Final = 2.5
COHERENCE_SIGMA_FLOOR: Final = 0.01


def _coherence_excess(pinnacle: pd.DataFrame, matrices: dict[int, _FixtureMatrices]) -> pd.Series:
    """Per Pinnacle row: how far its price lies outside the scatter of the rest of its ladder (absolute probability).

    ``|q - q_matrix|`` minus ``COHERENCE_SIGMAS`` robust standard deviations of the fit residuals of the same
    fixture and period, floored at zero; zero where there are fewer than ``MIN_TARGETS_FOR_COHERENCE`` fitted prices.
    """
    excess = pd.Series(0.0, index=pinnacle.index)
    if not matrices:
        return excess
    fit_rows = pinnacle[pinnacle["market"].isin(_FIT_MARKETS) & pinnacle["period"].isin(("ft", "1h"))]
    for (fixture_id, period), rows in fit_rows.groupby(["fixture_id", "period"], sort=False):
        entry = matrices.get(int(fixture_id))
        fit = None if entry is None else (entry.ft if period == "ft" else entry.first)
        if fit is None or fit.n_targets < MIN_TARGETS_FOR_COHERENCE:
            continue
        residual = np.array(
            [
                abs(
                    matrix_leg_probability(fit.matrix, r.market, r.selection, float("nan") if r.line_key == _NO_LINE_KEY else float(r.line_key))
                    - r.p
                )
                for r in rows.itertuples(index=False)
            ]
        )
        finite = residual[np.isfinite(residual)]
        if len(finite) < MIN_TARGETS_FOR_COHERENCE:
            continue
        sigma = max(COHERENCE_SIGMA_FLOOR, 1.4826 * float(np.median(np.abs(finite - np.median(finite)))))
        excess.loc[rows.index] = np.where(np.isfinite(residual), np.clip(residual - COHERENCE_SIGMAS * sigma, 0.0, None), 0.0)
    return excess


def _derived_rows(
    wanted: pd.DataFrame,
    pinnacle: pd.DataFrame,
    matrices: dict[int, _FixtureMatrices],
    half_shares: HalfShares = DEFAULT_HALF_SHARES,
    leagues: Mapping[int, object] | None = None,
) -> pd.DataFrame:
    """Derived legs for the wanted keys: exact double chance / draw no bet, matrix-based legs and half-split legs.

    The v1.1 markets of a whole match that need only the final score (odd/even, European handicap, win to nil) come
    from the full-time matrix; their first- and second-half versions and every market that needs the goals of each
    half (:data:`model_buk.scanner.markets.HALF_MARKETS_V11`) come from the half split (``kind`` ``derived_half``,
    ``detail`` ``half:<source>``).
    """
    out: list[dict[str, object]] = []
    leagues = leagues or {}
    have = set(zip(pinnacle["fixture_id"], pinnacle["market"], pinnacle["period"], pinnacle["line_key"], pinnacle["selection"], strict=True))
    one_x_two: dict[tuple[int, str], dict[str, float]] = {}
    for record in pinnacle[pinnacle["market"] == "1x2"].itertuples(index=False):
        one_x_two.setdefault((int(record.fixture_id), record.period), {})[record.selection] = float(record.p)

    for record in wanted.itertuples(index=False):
        fixture_id = int(record.fixture_id)
        key = (fixture_id, record.market, record.period, record.line_key, record.selection)
        if key in have:
            continue
        line = float("nan") if record.line_key == _NO_LINE_KEY else float(record.line_key)
        base = {"fixture_id": fixture_id, "market": record.market, "period": record.period, "line_key": record.line_key, "selection": record.selection}
        result = one_x_two.get((fixture_id, record.period))
        if record.market in ("dc", "dnb") and result is not None and len(result) == 3:
            home, draw, away = result["home"], result["draw"], result["away"]
            exact = {
                ("dc", "1x"): home + draw,
                ("dc", "x2"): draw + away,
                ("dc", "12"): home + away,
                ("dnb", "home"): home / (home + away),
                ("dnb", "away"): away / (home + away),
            }.get((record.market, record.selection))
            if exact is not None:
                out.append({**base, "p": exact, "kind": "derived_exact", "abs_err": 0.0, "second_half": False, "detail": "exact"})
                continue
        entry = matrices.get(fixture_id)
        if entry is None or record.market not in _MATRIX_MARKETS:
            continue
        if record.market in HALF_MARKETS_V11 or (record.market in MATRIX_MARKETS_V11 and record.period in ("1h", "2h")):
            split = entry.half_split(half_shares.for_league(leagues.get(fixture_id)))
            if split is None:
                continue
            p = half_leg_probability(split, record.market, record.period, record.selection, line)
            if math.isfinite(p) and 0.0 < p < 1.0:
                rmse = math.hypot(entry.ft.rmse if entry.ft else 0.0, split.rmse_1h if math.isfinite(split.rmse_1h) else 0.0)
                out.append(
                    {**base, "p": p, "kind": "derived_half", "abs_err": rmse, "second_half": False, "detail": f"half:{split.source}"}
                )
            continue
        if record.period == "ft":
            fit, matrix, second = entry.ft, entry.ft.matrix if entry.ft else None, False
            rmse = fit.rmse if fit else float("nan")
        elif record.period == "1h":
            fit, matrix, second = entry.first, entry.first.matrix if entry.first else None, False
            rmse = fit.rmse if fit else float("nan")
        elif record.period == "2h":
            matrix, second, rmse = entry.second, True, entry.second_rmse
        else:
            continue
        if matrix is None:
            continue
        p = matrix_leg_probability(matrix, record.market, record.selection, line)
        if math.isfinite(p) and 0.0 < p < 1.0:
            out.append({**base, "p": p, "kind": "derived_matrix", "abs_err": rmse, "second_half": second, "detail": "matrix"})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def _lead_hours(quotes: pd.DataFrame, as_of: pd.Timestamp | str | None) -> pd.Series:
    """Hours from the decision time to kickoff, per fixture (NaN when kickoff is unknown).

    Without ``as_of`` the decision time of a fixture is the latest ``fetched_at`` among its quotes: the hour of
    the last scan for a current frame, the last sighting before kickoff for a closing frame.
    """
    if "kickoff_utc" not in quotes.columns:
        return pd.Series(dtype=float)
    frame = quotes.drop_duplicates("fixture_id")[["fixture_id", "kickoff_utc"]].copy()
    kickoff = pd.to_datetime(frame["kickoff_utc"], utc=True, errors="coerce")
    if as_of is not None:
        now = pd.Timestamp(as_of)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        hours = (kickoff - now).dt.total_seconds() / 3600.0
    elif "fetched_at" in quotes.columns:
        seen = pd.to_datetime(quotes["fetched_at"], utc=True, errors="coerce").groupby(quotes["fixture_id"]).max()
        hours = (kickoff - frame["fixture_id"].map(seen)).dt.total_seconds() / 3600.0
    else:
        hours = pd.Series(np.nan, index=frame.index)
    return pd.Series(hours.to_numpy(dtype=float), index=frame["fixture_id"].astype("int64").to_numpy())


def fair_candidates(
    quotes: pd.DataFrame,
    *,
    method: str = DEFAULT_METHOD,
    betfair_commission: float = DEFAULT_BETFAIR_COMMISSION,
    min_consensus_books: int = DEFAULT_MIN_CONSENSUS_BOOKS,
    max_age_minutes: float | None = DEFAULT_MAX_AGE_MINUTES,
    derive: bool = True,
    score_model: str = "dc",
    coherence: bool = True,
    max_age_far_minutes: float | None = None,
    far_lead_hours: float = DEFAULT_FAR_LEAD_HOURS,
    half_shares: HalfShares | None = None,
) -> pd.DataFrame:
    """Every source's probability for every leg it can price, before the choice of the preferred one.

    Columns: ``fixture_id, market, period, line_key, selection, p, kind, n_books, book_margin, abs_err,
    second_half, board_at, detail``; ``kind`` is a key of :data:`PRIORITY` (``derived_exact``, ``derived_matrix``
    and ``derived_half`` all have ``fair_source == "pinnacle_derived"`` in the public frame; ``detail`` tells them
    apart and names the source of the half split).  ``line_key`` is the line with NaN replaced by -9999.
    :func:`fair_prices` documents the parameters.
    """
    if method not in DEVIG_METHODS:
        raise ValueError(f"unknown de-vig method {method!r}; expected one of: {', '.join(sorted(DEVIG_METHODS))}")
    prepared = _prepare(quotes, max_age_minutes, max_age_far_minutes, far_lead_hours)
    columns = [
        "fixture_id", "market", "period", "line_key", "selection", "p", "kind", "n_books", "book_margin", "abs_err",
        "second_half", "board_at", "detail",
    ]  # fmt: skip
    if prepared.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    devigged = _devig_all(prepared, method, betfair_commission)
    boards = _board_table(prepared)

    def board_of(fixture_ids: object, bk: object) -> np.ndarray:
        ids = np.asarray(fixture_ids)
        books = np.asarray(bk, dtype=object) if not isinstance(bk, str) else np.full(len(ids), bk, dtype=object)
        if not len(boards) or not len(ids):
            return np.full(len(ids), np.datetime64("NaT"), dtype="datetime64[ns]")
        index = pd.MultiIndex.from_arrays([ids, books])
        return pd.to_datetime(boards.reindex(index), utc=True).dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")

    if len(devigged):
        devigged["board_at"] = pd.to_datetime(board_of(devigged["fixture_id"], devigged["bk"])).tz_localize("UTC")
    key = ["fixture_id", "market", "period", "line_key", "selection"]
    candidates: list[pd.DataFrame] = []

    def add(
        frame: pd.DataFrame,
        kind: str,
        n_books: object,
        margin: object,
        abs_err: object = 0.0,
        second_half: object = False,
        board_at: object = None,
        detail: object = None,
    ) -> None:
        if frame.empty:
            return
        piece = frame[key + ["p"]].copy()
        piece["kind"] = kind
        piece["n_books"] = n_books
        piece["book_margin"] = margin
        piece["abs_err"] = abs_err
        piece["second_half"] = second_half
        piece["board_at"] = pd.NaT if board_at is None else pd.to_datetime(np.asarray(board_at), utc=True)
        piece["detail"] = kind if detail is None else detail
        candidates.append(piece)

    pinnacle = devigged[devigged["bk"] == PINNACLE]
    matrices = fit_fixture_matrices(pinnacle, model=score_model) if (derive or coherence) and not pinnacle.empty else {}
    add(
        pinnacle,
        "pinnacle",
        1,
        pinnacle["margin"].to_numpy(),
        _coherence_excess(pinnacle, matrices).to_numpy() if coherence else 0.0,
        board_at=pinnacle["board_at"] if "board_at" in pinnacle.columns else None,
    )
    betfair = devigged[devigged["bk"] == BETFAIR]
    if len(betfair):
        # yellow cards and the cards of a half: Pinnacle or a consensus only, never one soft board
        betfair = betfair[~np.array([consensus_only(m, p) for m, p in zip(betfair["market"], betfair["period"], strict=True)], dtype=bool)]
    add(betfair, "betfair", 1, betfair["margin"].to_numpy(), board_at=betfair["board_at"] if "board_at" in betfair.columns else None)
    consensus = _consensus(devigged[devigged["bk"] != PINNACLE], min_consensus_books)
    add(
        consensus,
        "consensus",
        consensus["n_books"].to_numpy(),
        consensus["margin"].to_numpy(),
        board_at=consensus["board_at"] if "board_at" in consensus.columns else None,
    )
    if derive and not pinnacle.empty:
        wanted = prepared[prepared["market"].isin(_MATRIX_MARKETS)][key].drop_duplicates()
        leagues = (
            dict(zip(prepared["fixture_id"], prepared["league_id"], strict=True)) if "league_id" in prepared.columns else {}
        )
        derived = _derived_rows(wanted, pinnacle, matrices, half_shares or DEFAULT_HALF_SHARES, leagues)
        for kind, part in (derived.groupby("kind") if not derived.empty else []):
            add(
                part, kind, 1, np.nan, part["abs_err"].to_numpy(), part["second_half"].to_numpy(),
                board_at=pd.to_datetime(board_of(part["fixture_id"], PINNACLE)).tz_localize("UTC"),
                detail=part["detail"].to_numpy(),
            )
    if not candidates:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    stacked = pd.concat(candidates, ignore_index=True)
    stacked["n_books"] = stacked["n_books"].astype("int64")
    stacked["board_at"] = pd.to_datetime(stacked["board_at"], utc=True, errors="coerce")
    return stacked[columns]


_FAIR_SOURCE: Final = {
    "pinnacle": "pinnacle",
    "derived_exact": "pinnacle_derived",
    "derived_matrix": "pinnacle_derived",
    "derived_half": "pinnacle_derived",
    "betfair": "betfair",
    "consensus": "consensus",
}


def fair_prices(
    quotes: pd.DataFrame,
    *,
    method: str = DEFAULT_METHOD,
    betfair_commission: float = DEFAULT_BETFAIR_COMMISSION,
    min_consensus_books: int = DEFAULT_MIN_CONSENSUS_BOOKS,
    max_age_minutes: float | None = DEFAULT_MAX_AGE_MINUTES,
    derive: bool = True,
    score_model: str = "dc",
    coherence: bool = True,
    se_model: SeModel = DEFAULT_SE_MODEL,
    as_of: pd.Timestamp | str | None = None,
    keep_all: bool = False,
    max_age_far_minutes: float | None = None,
    far_lead_hours: float = DEFAULT_FAR_LEAD_HOURS,
    half_shares: HalfShares | None = None,
) -> pd.DataFrame:
    """Fair frame of a quotes frame: ``fixture_id, market, period, line, selection, p_fair, p_se, fair_source,
    devig_method, n_books, book_margin, fair_board_at, fair_detail`` (see the module docstring for the sources and
    their order; ``fair_detail`` is ``pinnacle``, ``exact``, ``matrix``, ``half:<source>``, ``consensus`` or
    ``betfair``).

    Parameters
    ----------
    quotes:
        Canonical quotes (``model_buk.scanner.markets``); rows of ``other`` markets, prices <= 1.0, three-way
        totals and, if ``age_minutes`` is present, boards older than ``max_age_minutes`` are ignored.  For matches
        more than ``far_lead_hours`` ahead of the reference time (``fetched_at + age_minutes``) the bound is
        ``max_age_far_minutes`` instead (``None``: the same bound for every match).
    method:
        De-vig method, ``"power"`` (default), ``"proportional"`` or ``"shin"``.
    betfair_commission, min_consensus_books:
        See the module docstring.
    derive:
        Derive the legs Pinnacle does not quote from the score matrix (and the exact double chance / draw no bet).
    score_model:
        ``"dc"`` (default), ``"poisson"`` or ``"bivariate"``.
    coherence:
        Widen ``p_se`` of Pinnacle prices that do not fit the rest of the Pinnacle ladder (stale lines).
    se_model, as_of:
        Uncertainty model and decision time for the lead time to kickoff (default: per fixture, the latest
        ``fetched_at`` of its quotes; ``p_se`` of a closing frame is then that of a price seen just before kickoff).
    keep_all:
        Return one row per (leg, source) instead of the preferred source only.
    half_shares:
        First-half goal shares per league for the half split (:func:`model_buk.scanner.halves.estimate_half_shares`,
        estimated from matches before the decision time); None uses the built-in global shares.

    A group that is not a complete complementary set of one bookmaker gets no fair price from that bookmaker;
    a leg no source can price is absent (missing stays missing).  Only information in ``quotes`` is used, so a
    quotes frame cut at a decision time gives point-in-time fair prices.  The frame has one row per leg unless
    ``keep_all``; ``book_margin`` is NaN for derived legs.
    """
    stacked = fair_candidates(
        quotes,
        method=method,
        betfair_commission=betfair_commission,
        min_consensus_books=min_consensus_books,
        max_age_minutes=max_age_minutes,
        derive=derive,
        score_model=score_model,
        coherence=coherence,
        max_age_far_minutes=max_age_far_minutes,
        far_lead_hours=far_lead_hours,
        half_shares=half_shares,
    )
    if stacked.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in FAIR_COLUMNS})
    key = ["fixture_id", "market", "period", "line_key", "selection"]
    stacked = stacked.copy()
    stacked["priority"] = stacked["kind"].map(PRIORITY)
    # A consensus set with a tail leg (one below TAIL_LIMIT) ranks after the matrix as a whole, so a set never
    # mixes a consensus leg with a matrix leg.
    is_consensus = stacked["kind"] == "consensus"
    tail_depth = np.minimum(stacked["p"], 1.0 - stacked["p"]).where(is_consensus)
    set_key = ["fixture_id", "market", "period", "line_key", "kind"]
    tail = is_consensus & (tail_depth.groupby([stacked[c] for c in set_key]).transform("min") < TAIL_LIMIT)
    stacked.loc[tail, "priority"] = PRIORITY["derived_matrix"] + 1
    stacked = stacked.sort_values([*key, "priority"], kind="mergesort")
    if not keep_all:
        stacked = stacked[~stacked.duplicated(key, keep="first")]
    stacked = stacked.reset_index(drop=True)

    lead = _lead_hours(quotes, as_of)
    lead_hours = stacked["fixture_id"].map(lead).to_numpy(dtype=float) if len(lead) else np.full(len(stacked), np.nan)
    stacked["p_se"] = se_model.p_se(
        stacked["p"].to_numpy(dtype=float),
        lead_hours,
        stacked["kind"].tolist(),
        n_books=stacked["n_books"].to_numpy(dtype=float),
        abs_err=stacked["abs_err"].to_numpy(dtype=float),
        second_half=stacked["second_half"].to_numpy(dtype=bool),
        markets=stacked["market"].astype(str).tolist(),
        half_uncalibrated=stacked["detail"].isin(["half:league", "half:global"]).to_numpy(dtype=bool),
    )
    stacked["fair_source"] = stacked["kind"].map(_FAIR_SOURCE)
    stacked["line"] = np.where(stacked["line_key"] == _NO_LINE_KEY, np.nan, stacked["line_key"])
    stacked["devig_method"] = method
    stacked = stacked.rename(columns={"p": "p_fair", "board_at": "fair_board_at", "detail": "fair_detail"})
    return stacked[list(FAIR_COLUMNS)].sort_values(["fixture_id", "market", "period", "line", "selection"]).reset_index(drop=True)
