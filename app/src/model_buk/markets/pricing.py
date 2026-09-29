"""Odds pricing: de-vig, EV, per-bookmaker stake tax, CLV and accumulator EV.

Everything here is pure arithmetic on decimal odds. The Polish regime pays
``stake * tau * odds`` on a win (``tau`` = 0.88 for the 12% stake tax), so every
"net" quantity takes ``tau`` explicitly. A missing input stays missing (``None``)
and is never turned into a zero.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


_BISECT_ITERATIONS: Final = 100


def _validated_odds(odds: Sequence[float]) -> tuple[float, ...]:
    try:
        values = tuple(float(odd) for odd in odds)
    except (TypeError, ValueError) as exc:
        raise ValueError("odds must be a sequence of finite decimal values") from exc
    if len(values) < 2:
        raise ValueError("odds must contain at least two prices")
    if any(not math.isfinite(odd) or odd <= 1.0 for odd in values):
        raise ValueError("decimal odds must be finite and greater than 1.0")
    return values


def devig_proportional(odds: Sequence[float]) -> tuple[float, ...]:
    """Remove the overround by normalising inverse decimal odds."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    total = sum(implied)
    return tuple(probability / total for probability in implied)


def devig_power(odds: Sequence[float]) -> tuple[float, ...]:
    """Fit the power exponent for which transformed probabilities sum to one."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    if abs(sum(implied) - 1.0) < 1e-12:
        return implied

    def total(exponent: float) -> float:
        return sum(probability**exponent for probability in implied)

    lower, upper = 0.0, 1.0
    while total(upper) > 1.0:
        upper *= 2.0
        if upper > 1e6:
            raise ValueError("could not solve the power de-vig exponent")
    for _ in range(_BISECT_ITERATIONS):
        midpoint = (lower + upper) / 2.0
        if total(midpoint) > 1.0:
            lower = midpoint
        else:
            upper = midpoint
    exponent = (lower + upper) / 2.0
    probabilities = tuple(probability**exponent for probability in implied)
    normalizer = sum(probabilities)
    return tuple(probability / normalizer for probability in probabilities)


def devig_shin(odds: Sequence[float]) -> tuple[float, ...]:
    """Remove the overround using Shin's informed-trader model."""
    values = _validated_odds(odds)
    implied = tuple(1.0 / odd for odd in values)
    overround_total = sum(implied)
    if abs(overround_total - 1.0) < 1e-12:
        return implied

    def probabilities(informed_share: float) -> tuple[float, ...]:
        if informed_share <= 0.0:
            return tuple(probability / math.sqrt(overround_total) for probability in implied)
        return tuple(
            (
                math.sqrt(
                    informed_share**2
                    + 4.0
                    * (1.0 - informed_share)
                    * probability**2
                    / overround_total
                )
                - informed_share
            )
            / (2.0 * (1.0 - informed_share))
            for probability in implied
        )

    lower, upper = 0.0, 0.5
    while sum(probabilities(upper)) > 1.0:
        upper = (upper + 1.0) / 2.0
        if upper > 1.0 - 1e-12:
            break
    for _ in range(_BISECT_ITERATIONS):
        midpoint = (lower + upper) / 2.0
        if sum(probabilities(midpoint)) > 1.0:
            lower = midpoint
        else:
            upper = midpoint
    result = probabilities((lower + upper) / 2.0)
    normalizer = sum(result)
    return tuple(probability / normalizer for probability in result)


DEVIG_METHODS: Final[dict[str, Callable[[Sequence[float]], tuple[float, ...]]]] = {
    "proportional": devig_proportional,
    "power": devig_power,
    "shin": devig_shin,
}


def devig_probabilities(
    odds: Sequence[float],
    method: str = "proportional",
) -> tuple[float, ...]:
    """Return positive fair probabilities for a complete bookmaker book."""
    if method not in DEVIG_METHODS:
        choices = ", ".join(sorted(DEVIG_METHODS))
        raise ValueError(f"unknown de-vig method {method!r}; expected one of: {choices}")
    return DEVIG_METHODS[method](odds)


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    over, under = devig_proportional((over_odds, under_odds))
    return over, under


def fair_odds(probability: float) -> float:
    if not (0.0 < probability <= 1.0):
        return math.inf if probability <= 0.0 else 1.0
    return 1.0 / probability


def expected_value(probability: float, decimal_odds: float) -> float:
    """Gross EV before stake tax, commission, and execution costs."""
    return probability * decimal_odds - 1.0


def expected_value_net(
    probability: float,
    decimal_odds: float,
    tax_multiplier: float,
) -> float:
    """EV after a stake-tax multiplier (0.88 means a 12% stake tax)."""
    if not 0.0 < tax_multiplier <= 1.0:
        raise ValueError("tax_multiplier must be in (0, 1]")
    return probability * (tax_multiplier * decimal_odds) - 1.0


# --------------------------------------------------------------------------- #
# Strict input validation shared by the decision-math modules
# --------------------------------------------------------------------------- #


def _finite_number(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def require_probability(value: float, name: str = "probability") -> float:
    """Return ``value`` as a float if it is a finite probability in [0, 1]."""
    number = _finite_number(value, name)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return number


def require_decimal_odds(value: float, name: str = "decimal_odds") -> float:
    """Return ``value`` as a float if it is finite decimal odds above 1.0."""
    number = _finite_number(value, name)
    if number <= 1.0:
        raise ValueError(f"{name} must be finite and greater than 1.0")
    return number


def require_tax_multiplier(value: float, name: str = "tax_multiplier") -> float:
    """Return ``value`` as a float if it is a stake-tax multiplier in (0, 1]."""
    number = _finite_number(value, name)
    if not 0.0 < number <= 1.0:
        raise ValueError(f"{name} must be in (0, 1]")
    return number


def require_non_negative(value: float, name: str) -> float:
    number = _finite_number(value, name)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return number


# --------------------------------------------------------------------------- #
# Stake-tax multiplier (tau) per bookmaker: explicit, configurable, fail closed
# --------------------------------------------------------------------------- #

STANDARD_TAX_MULTIPLIER: Final = 0.88
"""Polish standard: 12% gaming tax on the stake, passed on as ``tau = 0.88``."""

BETCLIC_DEGRADED_TAX_MULTIPLIER: Final = 0.94
"""Betclic "Bez podatku 2.0" once the 1 000 zl threshold is passed without the
50% accumulator share: singles settle at 0.94 (audit note 33: the realistic
regime for a singles-based operation)."""

BETCLIC_FIRST_THOUSAND_TAX_MULTIPLIER: Final = 1.0
"""Betclic tax-free singles, unconditional only for the first 1 000 zl of stakes
(audit note 33). This is a start-up allowance, not an operating regime."""


class UnknownBookmakerError(ValueError):
    """Raised when no tax multiplier is registered for a bookmaker (fail closed)."""


_NON_ALPHANUMERIC: Final = re.compile(r"[^a-z0-9]+")


def normalise_bookmaker_name(name: str) -> str:
    """Lower-case a bookmaker name and drop everything but letters and digits."""
    return _NON_ALPHANUMERIC.sub("", str(name).lower())


class TaxProfiles:
    """Immutable bookmaker -> stake-tax multiplier registry.

    Names are compared case-, space- and punctuation-insensitively
    (``"LV BET"`` == ``"lvbet"``). There is no fallback: a bookmaker that is not
    registered raises :class:`UnknownBookmakerError`, so a price from an
    unlisted book (a reference-only sharp book, or a typo) can never be
    evaluated under an assumed tax.
    """

    def __init__(self, multipliers: Mapping[str, float]) -> None:
        normalised: dict[str, float] = {}
        for name, multiplier in multipliers.items():
            key = normalise_bookmaker_name(name)
            if not key:
                raise ValueError("bookmaker name must contain letters or digits")
            value = require_tax_multiplier(multiplier)
            if key in normalised and normalised[key] != value:
                raise ValueError(f"ambiguous tax multiplier for bookmaker {name!r}")
            normalised[key] = value
        self._multipliers: Mapping[str, float] = MappingProxyType(normalised)

    def tax_multiplier(self, bookmaker: str) -> float:
        key = normalise_bookmaker_name(bookmaker)
        try:
            return self._multipliers[key]
        except KeyError:
            raise UnknownBookmakerError(
                f"no tax multiplier registered for bookmaker {bookmaker!r}"
            ) from None

    def __contains__(self, bookmaker: object) -> bool:
        return isinstance(bookmaker, str) and normalise_bookmaker_name(bookmaker) in self._multipliers

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._multipliers))

    def with_overrides(self, multipliers: Mapping[str, float]) -> TaxProfiles:
        """Return a new registry with ``multipliers`` added or replaced."""
        # Validating the overrides on their own first keeps the constructor's
        # ambiguity check (two spellings of one book with different values).
        overrides = TaxProfiles(multipliers)
        merged: dict[str, float] = dict(self._multipliers)
        merged.update(overrides._multipliers)
        return TaxProfiles(merged)

    def __reduce__(self) -> tuple[type[TaxProfiles], tuple[dict[str, float]]]:
        # A mapping proxy cannot be pickled or deep-copied; rebuild from a plain dict.
        return (TaxProfiles, (dict(self._multipliers),))


# Operators holding a Polish Ministry of Finance permit (research note,
# "weryfikacja_matematyki_rynku.md" Q3.6, register state 2026-09-28). All pass the
# 12% stake tax on in the payout, so singles settle at 0.88. Their tax-free
# promotions are limited to accumulators / bet builders and are deliberately not
# modelled here. Reference-only books (Pinnacle, Betfair, ...) are absent on
# purpose: the system never recommends a bet there.
_STANDARD_POLISH_BOOKMAKERS: Final = (
    "Fortuna",
    "STS",
    "Superbet",
    "LV BET",
    "Totalbet",
    "BetFan",
    "eToto",
    "Betcris",
    "forBET",
    "ComeOn",
    "PZBuk",
    "Fuksiarz",
    "Admiral Bet",
    "Le Bull",
    "Betters",
    "Traf",
    "Wett Arena",
)

DEFAULT_TAX_PROFILES: Final = TaxProfiles(
    {
        **{name: STANDARD_TAX_MULTIPLIER for name in _STANDARD_POLISH_BOOKMAKERS},
        "Betclic": BETCLIC_DEGRADED_TAX_MULTIPLIER,
        "Betclic first 1000": BETCLIC_FIRST_THOUSAND_TAX_MULTIPLIER,
    }
)


def tax_multiplier_for(bookmaker: str, profiles: TaxProfiles | None = None) -> float:
    """Stake-tax multiplier for ``bookmaker``; raises for unregistered books.

    ``profiles`` defaults to :data:`DEFAULT_TAX_PROFILES`. Pass a custom
    :class:`TaxProfiles` to configure other operators or other regimes.
    """
    registry = DEFAULT_TAX_PROFILES if profiles is None else profiles
    return registry.tax_multiplier(bookmaker)


# --------------------------------------------------------------------------- #
# EV with uncertainty
# --------------------------------------------------------------------------- #


def probability_lower_bound(probability: float, standard_error: float, z: float) -> float:
    """One-sided lower bound ``p - z * SE(p)``, clamped at zero.

    ``z`` is the conservatism parameter (1.645 for a one-sided 95% bound). It has
    no default on purpose: the size of the haircut is a modelling decision.
    """
    p = require_probability(probability, "probability")
    se = require_non_negative(standard_error, "standard_error")
    z_value = require_non_negative(z, "z")
    return max(0.0, p - z_value * se)


def ev_standard_error(decimal_odds: float, tax_multiplier: float, probability_se: float) -> float:
    """``SE(EV_net) = tau * odds * SE(p)``: uncertainty grows with the odds."""
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    se = require_non_negative(probability_se, "probability_se")
    return tau * odds * se


def expected_value_net_lower_bound(
    probability_lower: float,
    decimal_odds: float,
    tax_multiplier: float,
    execution_cost: float = 0.0,
) -> float:
    """Net EV evaluated at the lower probability bound, minus execution cost."""
    p_lower = require_probability(probability_lower, "probability_lower")
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    cost = require_non_negative(execution_cost, "execution_cost")
    return p_lower * tau * odds - 1.0 - cost


def break_even_probability(decimal_odds: float, tax_multiplier: float) -> float:
    """Win probability at which ``EV_net`` is zero: ``1 / (tau * odds)``.

    A value above 1.0 means no probability can make the bet break even.
    """
    odds = require_decimal_odds(decimal_odds)
    tau = require_tax_multiplier(tax_multiplier)
    return 1.0 / (tau * odds)


# --------------------------------------------------------------------------- #
# Closing line value on the odds actually taken
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClosingLineValue:
    """CLV of a bet at the price actually taken.

    ``raw = odds_taken / fair_close_odds - 1`` and
    ``net = tau * odds_taken / fair_close_odds - 1``. Under an efficient close
    ``net`` estimates the bet's expected return per unit stake after tax. All
    fields are ``None`` when the closing price is missing.
    """

    raw: float | None
    net: float | None
    fair_close_odds: float | None


def closing_line_value(
    odds_taken: float,
    closing_fair_probability: float | None,
    tax_multiplier: float,
) -> ClosingLineValue:
    """CLV of ``odds_taken`` against the de-vigged closing probability.

    The closing price is used here and nowhere else: it may not feed a decision.
    A ``None`` or NaN closing probability yields an all-``None`` result rather
    than a zero. ``odds_taken`` must be a real price above 1.0.
    """
    odds = require_decimal_odds(odds_taken, "odds_taken")
    tau = require_tax_multiplier(tax_multiplier)
    if closing_fair_probability is None:
        return ClosingLineValue(None, None, None)
    try:
        probability = float(closing_fair_probability)
    except (TypeError, ValueError) as exc:
        raise ValueError("closing_fair_probability must be a number") from exc
    if math.isnan(probability):
        return ClosingLineValue(None, None, None)
    if not math.isfinite(probability) or not 0.0 < probability <= 1.0:
        raise ValueError("closing_fair_probability must be in (0, 1]")
    return ClosingLineValue(
        raw=odds * probability - 1.0,
        net=tau * odds * probability - 1.0,
        fair_close_odds=1.0 / probability,
    )


def _is_missing(value: object) -> bool:
    """True for ``None`` and NaN (a missing price), False for any real number."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def closing_line_value_from_book(
    odds_taken: float,
    closing_book_odds: Sequence[float] | None,
    selection_index: int,
    *,
    method: str,
    tax_multiplier: float,
) -> ClosingLineValue:
    """CLV against a complete closing book, de-vigged with ``method``.

    ``method`` is required: CLV conclusions must be checked against every
    de-vig method because the choice moves CLV by about as much as the effect
    being measured. A missing closing book (``None``) gives an all-``None`` result.
    """
    if method not in DEVIG_METHODS:
        # Checked even when the closing book is missing, so a typo cannot hide
        # behind missing data.
        raise ValueError(
            f"unknown de-vig method {method!r}; expected one of: {', '.join(sorted(DEVIG_METHODS))}"
        )
    if closing_book_odds is None or any(_is_missing(price) for price in closing_book_odds):
        # A closing book with a missing price cannot be de-vigged: the CLV is
        # unavailable, not zero.
        require_decimal_odds(odds_taken, "odds_taken")
        require_tax_multiplier(tax_multiplier)
        return ClosingLineValue(None, None, None)
    fair = devig_probabilities(closing_book_odds, method)
    if not 0 <= selection_index < len(fair):
        raise ValueError("selection_index is outside the closing book")
    return closing_line_value(odds_taken, fair[selection_index], tax_multiplier)


# --------------------------------------------------------------------------- #
# Accumulators (AKO)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AccumulatorLeg:
    """One leg of an accumulator: a selection in one match with its price.

    To evaluate an accumulator conservatively pass each leg's lower probability
    bound as ``probability``; the product of lower bounds is then a lower bound
    of the coupon's win probability (for independent matches).
    """

    fixture_id: str
    probability: float
    decimal_odds: float

    def __post_init__(self) -> None:
        if not str(self.fixture_id).strip():
            raise ValueError("fixture_id must not be empty")
        require_probability(self.probability, "probability")
        require_decimal_odds(self.decimal_odds)


@dataclass(frozen=True)
class AccumulatorPrice:
    n_legs: int
    combined_odds: float
    combined_probability: float

    @property
    def fair_odds(self) -> float:
        return fair_odds(self.combined_probability)


def _validated_legs(legs: Sequence[AccumulatorLeg]) -> tuple[AccumulatorLeg, ...]:
    values = tuple(legs)
    if len(values) < 2:
        raise ValueError("an accumulator needs at least two legs")
    if any(not isinstance(leg, AccumulatorLeg) for leg in values):
        raise TypeError("legs must be AccumulatorLeg instances")
    fixtures = [str(leg.fixture_id).strip() for leg in values]
    if len(set(fixtures)) != len(fixtures):
        raise ValueError("accumulator legs must come from different matches")
    return values


def accumulator_price(legs: Sequence[AccumulatorLeg]) -> AccumulatorPrice:
    """Combined odds and win probability, assuming independent matches."""
    values = _validated_legs(legs)
    return AccumulatorPrice(
        n_legs=len(values),
        combined_odds=math.prod(leg.decimal_odds for leg in values),
        combined_probability=math.prod(leg.probability for leg in values),
    )


def accumulator_ev_net(legs: Sequence[AccumulatorLeg], tax_multiplier: float) -> float:
    """``EV = tau * prod(p_i * o_i) - 1`` for legs from different matches.

    The stake tax is paid once on the whole coupon, so ``tau`` appears once
    while the per-leg edges compound. All legs must be at one bookmaker; the
    caller resolves that bookmaker's ``tax_multiplier``.
    """
    values = _validated_legs(legs)
    tau = require_tax_multiplier(tax_multiplier)
    return tau * math.prod(leg.probability * leg.decimal_odds for leg in values) - 1.0


def break_even_leg_edge(n_legs: int, tax_multiplier: float) -> float:
    """Per-leg edge (``p * o - 1``) at which an ``n_legs`` accumulator has zero EV.

    Solves ``tau * (1 + e) ** n = 1``: about 13.6% for a single, 4.4% per leg for
    three legs and 2.6% per leg for five legs at ``tau = 0.88``.
    """
    if isinstance(n_legs, bool) or not isinstance(n_legs, int) or n_legs < 1:
        raise ValueError("n_legs must be a positive integer")
    tau = require_tax_multiplier(tax_multiplier)
    return (1.0 / tau) ** (1.0 / n_legs) - 1.0
