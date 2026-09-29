"""Decision-math tests: taxed Kelly, PIT threshold, per-bookmaker tau, CLV, EV gate, AKO.

Reference numbers come from the project notes:

- ``Football-Prediction-Brain/22 - Zasady modelu i rejestr testowych betow.md``
  and ``experiments/test-bets.csv`` (frozen test bets MB-0001..MB-0004),
- ``Audyt-danych-i-rynku/33 - Audyt Betclica i realne tau.md`` (Betclic tau),
- ``research_notes/Weryfikacja modeli i value bettingu/weryfikacja_matematyki_rynku.md``
  (taxed Kelly, PIT cliff at 2 280 zl, price-taken CLV).
"""

from __future__ import annotations

import copy
import csv
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from model_buk.decision import (
    EvGateConfig,
    assess_bet,
    evaluate_ev_gate,
    price_two_way_market,
)
from model_buk.markets.pricing import (
    DEFAULT_TAX_PROFILES,
    AccumulatorLeg,
    TaxProfiles,
    UnknownBookmakerError,
    accumulator_ev_net,
    accumulator_price,
    break_even_leg_edge,
    closing_line_value,
    closing_line_value_from_book,
    devig_probabilities,
    expected_value,
    expected_value_net,
    expected_value_net_lower_bound,
    probability_lower_bound,
    tax_multiplier_for,
)
from model_buk.markets.staking import (
    PIT_RATE,
    PIT_THRESHOLD_PLN,
    PitRule,
    effective_tax_multiplier,
    expected_value_net_at_stake,
    fractional_kelly_fraction,
    full_kelly_fraction,
    gross_payout,
    max_stake_below_pit,
    net_payout,
    pit_applies,
    plan_stake,
)
from model_buk.opportunity_selector import OpportunityCandidate, select_opportunities


TAU_STANDARD = 0.88

# (p_model, decimal odds, EV_gross, EV_net at tau = 0.88), as recorded in the notes.
MB_BETS = {
    "MB-0001": (0.55, 1.93, 0.0615, -0.0659),
    "MB-0002": (0.70, 1.61, 0.1270, -0.0082),
    "MB-0003": (0.61, 1.81, 0.1041, -0.0284),
    "MB-0004": (0.66, 1.70, 0.1220, -0.0126),
}

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# MB-0001..MB-0004 reproduce the EVs recorded in the notes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bet_id", sorted(MB_BETS))
def test_mb_bets_reproduce_recorded_gross_and_net_ev(bet_id: str) -> None:
    probability, odds, ev_gross_note, ev_net_note = MB_BETS[bet_id]

    assert round(expected_value(probability, odds), 4) == ev_gross_note
    assert round(expected_value_net(probability, odds, TAU_STANDARD), 4) == ev_net_note


def test_mb_bets_in_csv_register_match_the_constants_used_here() -> None:
    register = Path(__file__).resolve().parents[2] / "experiments" / "test-bets.csv"
    if not register.exists():
        pytest.skip("experiments/test-bets.csv not present")
    with register.open(encoding="utf-8", newline="") as handle:
        rows = {row["bet_id"]: row for row in csv.DictReader(handle)}
    for bet_id, (probability, odds, ev_gross_note, _ev_net_note) in MB_BETS.items():
        row = rows[bet_id]
        assert float(row["p_model"]) == probability
        assert float(row["odds"]) == odds
        assert float(row["ev_gross"]) == pytest.approx(ev_gross_note, abs=1e-4)


@pytest.mark.parametrize("bet_id", sorted(MB_BETS))
def test_no_mb_bet_has_positive_taxed_kelly(bet_id: str) -> None:
    probability, odds, _gross, _net = MB_BETS[bet_id]

    assert full_kelly_fraction(probability, odds, TAU_STANDARD) == 0.0
    assert fractional_kelly_fraction(probability, odds, TAU_STANDARD, 0.25) == 0.0


# --------------------------------------------------------------------------- #
# Kelly with the stake tax
# --------------------------------------------------------------------------- #


def test_kelly_mb0002_is_zero_taxed_but_20_8_percent_untaxed() -> None:
    # The report's headline example: the textbook formula would stake 20.8% of
    # the bankroll on a bet whose taxed EV is -0.82%.
    assert full_kelly_fraction(0.70, 1.61, 1.0) == pytest.approx(0.2082, abs=1e-4)
    assert full_kelly_fraction(0.70, 1.61, TAU_STANDARD) == 0.0


def test_taxed_kelly_uses_b_equal_tau_times_odds_minus_one() -> None:
    p, odds, tau = 0.60, 2.0, TAU_STANDARD
    b = tau * odds - 1.0
    expected = (p * b - (1.0 - p)) / b

    assert full_kelly_fraction(p, odds, tau) == pytest.approx(expected)
    assert full_kelly_fraction(p, odds, tau) == pytest.approx(0.056 / 0.76)
    # f* = EV_net / (tau * odds - 1)
    assert full_kelly_fraction(p, odds, tau) == pytest.approx(
        expected_value_net(p, odds, tau) / b
    )


def test_untaxed_kelly_textbook_value() -> None:
    assert full_kelly_fraction(0.60, 2.0, 1.0) == pytest.approx(0.20)


def test_fractional_kelly_scales_the_full_fraction() -> None:
    full = full_kelly_fraction(0.60, 2.0, TAU_STANDARD)

    assert fractional_kelly_fraction(0.60, 2.0, TAU_STANDARD, 0.25) == pytest.approx(
        0.25 * full
    )
    assert fractional_kelly_fraction(0.60, 2.0, TAU_STANDARD, 1.0) == pytest.approx(full)


def test_kelly_is_zero_at_break_even_and_below() -> None:
    assert full_kelly_fraction(0.50, 2.0, 1.0) == 0.0  # EV exactly zero
    assert full_kelly_fraction(0.40, 2.0, 1.0) == 0.0  # negative edge
    assert full_kelly_fraction(0.0, 2.0, 1.0) == 0.0


def test_kelly_is_computed_on_the_lower_bound_not_the_point_estimate() -> None:
    point, lower = 0.70, 0.55
    odds = 2.0

    assert full_kelly_fraction(lower, odds, 1.0) < full_kelly_fraction(point, odds, 1.0)
    # A lower bound below break-even gives no stake even when the point estimate has edge.
    assert full_kelly_fraction(0.49, odds, 1.0) == 0.0
    assert full_kelly_fraction(0.60, odds, 1.0) > 0.0


@pytest.mark.parametrize("multiplier", [0.0, -0.1, 1.01, float("nan")])
def test_kelly_multiplier_must_be_in_unit_interval(multiplier: float) -> None:
    with pytest.raises(ValueError, match="kelly_multiplier"):
        fractional_kelly_fraction(0.60, 2.0, TAU_STANDARD, multiplier)


@pytest.mark.parametrize("odds", [1.0, 0.5, 0.0, -2.0, float("nan"), float("inf")])
def test_kelly_rejects_odds_at_or_below_one_and_non_finite(odds: float) -> None:
    with pytest.raises(ValueError, match="decimal_odds"):
        full_kelly_fraction(0.60, odds, 1.0)


@pytest.mark.parametrize("probability", [-0.01, 1.01, 2.0, float("nan"), float("inf")])
def test_kelly_rejects_probability_outside_unit_interval(probability: float) -> None:
    with pytest.raises(ValueError, match="p_lower"):
        full_kelly_fraction(probability, 2.0, 1.0)


@pytest.mark.parametrize("tau", [0.0, -0.5, 1.01, float("nan")])
def test_kelly_rejects_invalid_tax_multiplier(tau: float) -> None:
    with pytest.raises(ValueError, match="tax_multiplier"):
        full_kelly_fraction(0.60, 2.0, tau)


# --------------------------------------------------------------------------- #
# Polish PIT threshold: a win above 2 280 zl switches tau
# --------------------------------------------------------------------------- #


def test_pit_constants_match_the_notes() -> None:
    assert PIT_THRESHOLD_PLN == 2280.0
    assert PIT_RATE == 0.10


def test_win_above_threshold_switches_tau_to_0_792() -> None:
    odds = 2.0
    below = 1295.45  # payout 2 279.99
    above = 1295.46  # payout 2 280.0096

    assert gross_payout(below, odds, TAU_STANDARD) <= PIT_THRESHOLD_PLN
    assert gross_payout(above, odds, TAU_STANDARD) > PIT_THRESHOLD_PLN
    assert effective_tax_multiplier(below, odds, TAU_STANDARD) == pytest.approx(0.88)
    assert effective_tax_multiplier(above, odds, TAU_STANDARD) == pytest.approx(0.792)
    assert not pit_applies(below, odds, TAU_STANDARD)
    assert pit_applies(above, odds, TAU_STANDARD)


def test_payout_exactly_at_the_threshold_is_not_taxed() -> None:
    # 912 * 1.0 * 2.5 == 2 280.0 exactly: the threshold is "above", not "at or above".
    assert gross_payout(912.0, 2.5, 1.0) == 2280.0
    assert not pit_applies(912.0, 2.5, 1.0)
    assert pit_applies(912.01, 2.5, 1.0)
    # Settlement agrees with the flag: nothing is withheld at exactly the threshold.
    assert net_payout(912.0, 2.5, 1.0) == 2280.0
    assert effective_tax_multiplier(912.0, 2.5, 1.0) == 1.0


def test_pit_cliff_net_payout_drops_when_crossing_the_threshold() -> None:
    odds = 2.0
    just_below = net_payout(1295.45, odds, TAU_STANDARD)
    just_above = net_payout(1295.46, odds, TAU_STANDARD)

    assert just_below == pytest.approx(1295.45 * 0.88 * 2.0)
    assert just_above == pytest.approx(1295.46 * 0.88 * 2.0 * 0.9)
    assert just_above < just_below  # the cliff: a bigger stake pays less


def test_pit_tax_applies_to_the_whole_payout_not_the_excess() -> None:
    # 3 000 zl payout -> 300 zl tax, i.e. 2 700 zl paid (notes: art. 30 PIT).
    stake = 3000.0 / (1.0 * 2.0)
    assert net_payout(stake, 2.0, 1.0) == pytest.approx(2700.0)


def test_break_even_product_jumps_at_the_threshold() -> None:
    # p * o at break-even: 1 / 0.88 = 1.136 below the threshold, 1 / 0.792 = 1.263 above.
    assert 1.0 / effective_tax_multiplier(100.0, 2.0, TAU_STANDARD) == pytest.approx(1.13636, abs=1e-5)
    assert 1.0 / effective_tax_multiplier(1400.0, 2.0, TAU_STANDARD) == pytest.approx(1.26263, abs=1e-5)


def test_expected_value_net_at_stake_switches_with_the_threshold() -> None:
    p, odds = 0.60, 2.0
    small = expected_value_net_at_stake(p, odds, TAU_STANDARD, stake=100.0)
    large = expected_value_net_at_stake(p, odds, TAU_STANDARD, stake=1400.0)

    assert small == pytest.approx(p * 0.88 * odds - 1.0)
    assert large == pytest.approx(p * 0.792 * odds - 1.0)
    assert large < 0.0 < small


def test_max_stake_below_pit_keeps_the_payout_at_or_below_the_threshold() -> None:
    cap = max_stake_below_pit(2.0, TAU_STANDARD)

    assert cap == 1295.45
    assert gross_payout(cap, 2.0, TAU_STANDARD) <= PIT_THRESHOLD_PLN
    assert gross_payout(round(cap + 0.01, 2), 2.0, TAU_STANDARD) > PIT_THRESHOLD_PLN
    # The notes quote ~1 295 zl at odds 2.00 / tau 0.88 and ~1 140 zl at tau 1.00.
    assert max_stake_below_pit(2.0, 1.0) == 1140.0


def test_pit_rule_is_configurable_and_validated() -> None:
    rule = PitRule(threshold_pln=1000.0, rate=0.15)

    assert effective_tax_multiplier(600.0, 2.0, 1.0, rule) == pytest.approx(0.85)
    assert effective_tax_multiplier(400.0, 2.0, 1.0, rule) == 1.0
    with pytest.raises(ValueError, match="threshold_pln"):
        PitRule(threshold_pln=0.0)
    with pytest.raises(ValueError, match="rate"):
        PitRule(rate=1.0)
    with pytest.raises(ValueError, match="rate"):
        PitRule(rate=-0.1)


# --------------------------------------------------------------------------- #
# Stake planning: Kelly + PIT stake cap
# --------------------------------------------------------------------------- #


def test_plan_stake_is_zero_without_taxed_edge() -> None:
    plan = plan_stake(
        bankroll=10_000.0,
        p_lower=0.70,
        decimal_odds=1.61,
        tax_multiplier=TAU_STANDARD,
        kelly_multiplier=0.25,
    )

    assert plan.stake == 0.0
    assert plan.full_kelly == 0.0
    assert plan.reason == "no_edge"


def test_plan_stake_quarter_kelly_without_pit() -> None:
    # tau = 1, p = 0.6, o = 2.0: full Kelly 20%, quarter Kelly 5% of 10 000 = 500.
    plan = plan_stake(
        bankroll=10_000.0,
        p_lower=0.60,
        decimal_odds=2.0,
        tax_multiplier=1.0,
        kelly_multiplier=0.25,
    )

    assert plan.stake == 500.0
    assert plan.full_kelly == pytest.approx(0.20)
    assert plan.applied_fraction == pytest.approx(0.05)
    assert not plan.pit_capped
    assert plan.effective_tax_multiplier == 1.0
    assert plan.reason == "ok"


def test_plan_stake_is_capped_so_the_payout_stays_below_the_pit_threshold() -> None:
    plan = plan_stake(
        bankroll=100_000.0,
        p_lower=0.60,
        decimal_odds=2.0,
        tax_multiplier=TAU_STANDARD,
        kelly_multiplier=0.25,
    )

    uncapped = 100_000.0 * 0.25 * full_kelly_fraction(0.60, 2.0, TAU_STANDARD)
    assert uncapped > 1295.45
    assert plan.stake == 1295.45
    assert plan.pit_capped
    assert plan.effective_tax_multiplier == pytest.approx(0.88)
    assert gross_payout(plan.stake, 2.0, TAU_STANDARD) <= PIT_THRESHOLD_PLN


def test_plan_stake_respects_max_bankroll_fraction_and_rounds_down_to_the_step() -> None:
    plan = plan_stake(
        bankroll=1234.56,
        p_lower=0.60,
        decimal_odds=2.0,
        tax_multiplier=1.0,
        kelly_multiplier=1.0,
        max_bankroll_fraction=0.05,
    )

    assert plan.stake == 61.72  # 0.05 * 1234.56 = 61.728, rounded down
    assert plan.applied_fraction == pytest.approx(61.72 / 1234.56)


@pytest.mark.parametrize("bankroll", [0.0, -1.0, float("nan"), float("inf")])
def test_plan_stake_rejects_invalid_bankroll(bankroll: float) -> None:
    with pytest.raises(ValueError, match="bankroll"):
        plan_stake(
            bankroll=bankroll,
            p_lower=0.6,
            decimal_odds=2.0,
            tax_multiplier=1.0,
            kelly_multiplier=0.25,
        )


# --------------------------------------------------------------------------- #
# tau per bookmaker: explicit, configurable, fail closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["Fortuna", "STS", "Superbet", "LV BET", "forBET", "Totalbet"])
def test_standard_polish_bookmakers_use_0_88(name: str) -> None:
    assert tax_multiplier_for(name) == 0.88


def test_betclic_operational_tau_is_0_94_and_first_thousand_is_1_00() -> None:
    assert tax_multiplier_for("Betclic") == 0.94
    assert tax_multiplier_for("betclic_first_1000") == 1.00
    assert tax_multiplier_for("Betclic First 1000") == 1.00


def test_bookmaker_lookup_ignores_case_spacing_and_punctuation() -> None:
    assert tax_multiplier_for("  LV-bet ") == tax_multiplier_for("lvbet") == 0.88


@pytest.mark.parametrize("name", ["pinnacle", "betfair", "consensus", "unknown-book", ""])
def test_unknown_bookmaker_fails_closed(name: str) -> None:
    with pytest.raises(UnknownBookmakerError):
        tax_multiplier_for(name)
    # It is also a ValueError, so generic validation handlers catch it.
    with pytest.raises(ValueError):
        tax_multiplier_for(name)


def test_tax_profiles_are_configurable_and_validated() -> None:
    custom = TaxProfiles({"my-book": 0.90})

    assert tax_multiplier_for("My Book", custom) == 0.90
    with pytest.raises(UnknownBookmakerError):
        tax_multiplier_for("fortuna", custom)  # nothing is inherited implicitly

    extended = DEFAULT_TAX_PROFILES.with_overrides({"Fortuna": 1.0, "new-book": 0.88})
    assert tax_multiplier_for("fortuna", extended) == 1.0
    assert tax_multiplier_for("new book", extended) == 0.88
    assert tax_multiplier_for("fortuna") == 0.88  # the default registry is not mutated

    for bad in (0.0, -0.2, 1.01, float("nan")):
        with pytest.raises(ValueError, match="tax_multiplier"):
            TaxProfiles({"book": bad})
    with pytest.raises(ValueError, match="ambiguous"):
        TaxProfiles({"Lv Bet": 0.88, "lvbet": 0.94})
    with pytest.raises(ValueError, match="ambiguous"):
        DEFAULT_TAX_PROFILES.with_overrides({"Lv Bet": 0.88, "lvbet": 0.94})
    with pytest.raises(ValueError, match="tax_multiplier"):
        DEFAULT_TAX_PROFILES.with_overrides({"Fortuna": 1.5})


def test_tax_profiles_survive_pickle_and_deepcopy() -> None:
    # Replay runs may hand the registry to worker processes.
    for clone in (pickle.loads(pickle.dumps(DEFAULT_TAX_PROFILES)), copy.deepcopy(DEFAULT_TAX_PROFILES)):
        assert clone.names() == DEFAULT_TAX_PROFILES.names()
        assert clone.tax_multiplier("Betclic") == 0.94
        with pytest.raises(UnknownBookmakerError):
            clone.tax_multiplier("pinnacle")


# --------------------------------------------------------------------------- #
# EV gate on the lower bound (uncertainty instead of a fixed 5 pp cut)
# --------------------------------------------------------------------------- #


def test_probability_lower_bound_subtracts_z_standard_errors_and_clamps() -> None:
    assert probability_lower_bound(0.60, 0.02, 1.645) == pytest.approx(0.60 - 0.0329)
    assert probability_lower_bound(0.60, 0.0, 1.645) == 0.60
    assert probability_lower_bound(0.05, 0.10, 2.0) == 0.0  # never below zero


@pytest.mark.parametrize(
    ("probability", "se", "z"),
    [(1.2, 0.01, 1.0), (0.5, -0.01, 1.0), (0.5, 0.01, -1.0), (float("nan"), 0.01, 1.0)],
)
def test_probability_lower_bound_validates_inputs(probability: float, se: float, z: float) -> None:
    with pytest.raises(ValueError):
        probability_lower_bound(probability, se, z)


def test_ev_lower_bound_uses_p_lower_tax_and_execution_cost() -> None:
    assert expected_value_net_lower_bound(0.58, 2.0, TAU_STANDARD) == pytest.approx(0.58 * 1.76 - 1.0)
    assert expected_value_net_lower_bound(0.58, 2.0, TAU_STANDARD, execution_cost=0.01) == pytest.approx(
        0.58 * 1.76 - 1.0 - 0.01
    )


def test_ev_gate_rejects_when_the_lower_bound_is_not_positive() -> None:
    # Point EV_net = 0.6 * 1.76 - 1 = +5.6%, but at 1.645 SE the lower bound is negative.
    p_lower = probability_lower_bound(0.60, 0.02, 1.645)
    result = evaluate_ev_gate(
        probability=0.60,
        probability_lower_bound=p_lower,
        decimal_odds=2.0,
        tax_multiplier=TAU_STANDARD,
        min_ev_lower=0.0,
    )

    assert result.ev_net == pytest.approx(0.056)
    assert result.ev_net_lower < 0.0
    assert not result.passed


def test_ev_gate_passes_with_a_tighter_estimate_and_is_strict_at_the_threshold() -> None:
    p_lower = probability_lower_bound(0.60, 0.02, 1.0)  # 0.58 -> EV_lower = +2.08%
    passing = evaluate_ev_gate(
        probability=0.60,
        probability_lower_bound=p_lower,
        decimal_odds=2.0,
        tax_multiplier=TAU_STANDARD,
        min_ev_lower=0.0,
    )
    assert passing.passed
    assert passing.ev_net_lower == pytest.approx(0.58 * 1.76 - 1.0)

    # Exactly break-even on the lower bound is not an opportunity (strict).
    at_zero = evaluate_ev_gate(
        probability=0.75,
        probability_lower_bound=0.5,
        decimal_odds=2.0,
        tax_multiplier=1.0,
        min_ev_lower=0.0,
    )
    assert at_zero.ev_net_lower == 0.0
    assert not at_zero.passed


def test_ev_gate_threshold_is_a_parameter_not_a_constant() -> None:
    kwargs = dict(
        probability=0.62,
        probability_lower_bound=0.60,
        decimal_odds=2.0,
        tax_multiplier=1.0,
    )  # EV_lower = +20%

    assert evaluate_ev_gate(min_ev_lower=0.10, **kwargs).passed
    assert not evaluate_ev_gate(min_ev_lower=0.25, **kwargs).passed


def test_ev_gate_rejects_inconsistent_probabilities() -> None:
    with pytest.raises(ValueError, match="probability_lower_bound"):
        evaluate_ev_gate(
            probability=0.5,
            probability_lower_bound=0.6,
            decimal_odds=2.0,
            tax_multiplier=1.0,
            min_ev_lower=0.0,
        )


def test_ev_gate_config_validation() -> None:
    assert EvGateConfig(z=1.645).min_ev_lower == 0.0
    with pytest.raises(ValueError, match="z"):
        EvGateConfig(z=-1.0)
    with pytest.raises(ValueError, match="min_ev_lower"):
        EvGateConfig(z=1.0, min_ev_lower=-0.01)


def test_two_way_market_legacy_behaviour_is_unchanged_without_a_gate() -> None:
    over, under = price_two_way_market(
        p_over=0.55,
        over_odds=1.93,
        under_odds=1.75,
        edge_threshold=0.05,
        ev_threshold=0.05,
        tax_multiplier=1.0,
    )

    assert over.decision == "BET"
    assert over.ev_net_lower is None
    assert under.ev_net_lower is None


def test_two_way_market_uncertain_probability_is_no_bet_under_the_ev_gate() -> None:
    kwargs = dict(
        p_over=0.55,
        over_odds=1.93,
        under_odds=1.75,
        edge_threshold=0.05,
        ev_threshold=0.0,
        tax_multiplier=1.0,
    )
    noisy, _ = price_two_way_market(
        **kwargs, ev_gate=EvGateConfig(z=1.645), p_over_se=0.03
    )
    tight, _ = price_two_way_market(
        **kwargs, ev_gate=EvGateConfig(z=1.645), p_over_se=0.01
    )

    assert noisy.ev_net_lower is not None and noisy.ev_net_lower < 0.0
    assert noisy.decision == "NO BET"
    assert tight.ev_net_lower is not None and tight.ev_net_lower > 0.0
    assert tight.decision == "BET"


def test_two_way_market_gate_uses_the_under_sides_own_odds() -> None:
    # UNDER (p = 0.45 at odds 2.5): the point EV is +12.5% and 1.645 * 0.03 = 4.9 pp
    # off p still leaves 0.4007 * 2.5 - 1 = +0.2%, so the gate passes on the UNDER
    # side's own odds. OVER (p = 0.55 at odds 1.6) is negative at its lower bound.
    over, under = price_two_way_market(
        p_over=0.55,
        over_odds=1.60,
        under_odds=2.50,
        edge_threshold=-1.0,
        ev_threshold=-1.0,
        tax_multiplier=1.0,
        ev_gate=EvGateConfig(z=1.645),
        p_over_se=0.03,
    )

    assert under.ev_net_lower == pytest.approx((0.45 - 1.645 * 0.03) * 2.50 - 1.0)
    assert under.ev_net_lower > 0.0
    assert under.decision == "BET"
    assert over.ev_net_lower == pytest.approx((0.55 - 1.645 * 0.03) * 1.60 - 1.0)
    assert over.ev_net_lower < 0.0
    assert over.decision == "NO BET"


def test_two_way_market_standard_error_without_a_gate_is_rejected() -> None:
    with pytest.raises(ValueError, match="ev_gate"):
        price_two_way_market(
            p_over=0.55,
            over_odds=1.93,
            under_odds=1.75,
            edge_threshold=0.05,
            ev_threshold=0.0,
            tax_multiplier=1.0,
            p_over_se=0.02,
        )


def test_two_way_market_gate_requires_a_standard_error() -> None:
    with pytest.raises(ValueError, match="p_over_se"):
        price_two_way_market(
            p_over=0.55,
            over_odds=1.93,
            under_odds=1.75,
            edge_threshold=0.05,
            ev_threshold=0.0,
            tax_multiplier=1.0,
            ev_gate=EvGateConfig(z=1.0),
        )


def test_assess_bet_mb0002_has_no_kelly_and_fails_the_gate() -> None:
    assessment = assess_bet(
        bookmaker="Fortuna",
        probability=0.70,
        probability_lower_bound=0.70,
        decimal_odds=1.61,
        min_ev_lower=0.0,
        kelly_multiplier=0.25,
    )

    assert assessment.tax_multiplier == 0.88
    assert assessment.ev_net == pytest.approx(-0.00824)
    assert not assessment.gate_passed
    assert assessment.kelly_full == 0.0
    assert assessment.kelly_applied == 0.0


def test_assess_bet_stakes_only_when_the_lower_bound_clears_the_gate() -> None:
    assessment = assess_bet(
        bookmaker="Betclic",
        probability=0.66,
        probability_lower_bound=0.60,
        decimal_odds=1.90,
        min_ev_lower=0.0,
        kelly_multiplier=0.25,
    )

    # Betclic operational tau 0.94: EV_lower = 0.60 * 0.94 * 1.90 - 1 = +7.16%.
    assert assessment.tax_multiplier == 0.94
    assert assessment.ev_net_lower == pytest.approx(0.60 * 0.94 * 1.90 - 1.0)
    assert assessment.gate_passed
    assert assessment.kelly_full == pytest.approx(
        assessment.ev_net_lower / (0.94 * 1.90 - 1.0)
    )
    assert assessment.kelly_applied == pytest.approx(0.25 * assessment.kelly_full)


def test_assess_bet_gate_failure_zeroes_a_kelly_that_is_positive_on_its_own() -> None:
    # p_lower 0.60 at 0.88 * 2.0 has a positive taxed Kelly (EV_lower = +5.6%), but a
    # required lower-bound EV of 10% is not met: the rejected bet carries no stake.
    assessment = assess_bet(
        bookmaker="Fortuna",
        probability=0.65,
        probability_lower_bound=0.60,
        decimal_odds=2.0,
        min_ev_lower=0.10,
        kelly_multiplier=0.25,
    )

    assert full_kelly_fraction(0.60, 2.0, TAU_STANDARD) > 0.0
    assert assessment.ev_net_lower == pytest.approx(0.056)
    assert not assessment.gate_passed
    assert assessment.kelly_full == 0.0
    assert assessment.kelly_applied == 0.0


def test_assess_bet_fails_closed_for_unknown_bookmaker() -> None:
    with pytest.raises(UnknownBookmakerError):
        assess_bet(
            bookmaker="pinnacle",
            probability=0.60,
            probability_lower_bound=0.58,
            decimal_odds=2.0,
            min_ev_lower=0.0,
            kelly_multiplier=0.25,
        )


# --------------------------------------------------------------------------- #
# CLV on the odds actually taken, raw and after tax
# --------------------------------------------------------------------------- #


def test_clv_uses_odds_taken_against_the_fair_closing_price() -> None:
    # Fair close 2.00 (p = 0.50); we took 2.10.
    clv = closing_line_value(2.10, 0.50, TAU_STANDARD)

    assert clv.fair_close_odds == pytest.approx(2.0)
    assert clv.raw == pytest.approx(2.10 / 2.00 - 1.0)
    assert clv.net == pytest.approx(0.88 * 2.10 / 2.00 - 1.0)
    assert clv.raw > 0.0 > clv.net  # beats the close before tax, loses after tax


def test_clv_net_without_tax_equals_raw() -> None:
    clv = closing_line_value(1.85, 0.55, 1.0)

    assert clv.net == pytest.approx(clv.raw)


def test_clv_is_the_price_taken_not_the_fair_to_fair_drift() -> None:
    # Same closing line, a worse price taken gives a worse CLV.
    better = closing_line_value(2.10, 0.50, 1.0)
    worse = closing_line_value(1.90, 0.50, 1.0)

    assert better.raw > 0.0 > worse.raw


def test_clv_from_a_full_closing_book_devigs_the_selection() -> None:
    closing_book = (2.00, 3.50, 3.70)
    fair = devig_probabilities(closing_book, "power")

    clv = closing_line_value_from_book(2.05, closing_book, 0, method="power", tax_multiplier=0.94)

    assert clv.raw == pytest.approx(2.05 * fair[0] - 1.0)
    assert clv.net == pytest.approx(0.94 * 2.05 * fair[0] - 1.0)


def test_clv_missing_closing_price_stays_missing_not_zero() -> None:
    for missing in (None, float("nan")):
        clv = closing_line_value(2.0, missing, TAU_STANDARD)
        assert clv.raw is None and clv.net is None and clv.fair_close_odds is None
    unavailable = closing_line_value_from_book(
        2.0, None, 0, method="power", tax_multiplier=TAU_STANDARD
    )
    assert unavailable.raw is None and unavailable.net is None


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_clv_from_a_book_with_a_missing_price_stays_missing_not_zero(missing: object) -> None:
    clv = closing_line_value_from_book(
        2.0, (2.0, missing, 3.5), 0, method="power", tax_multiplier=TAU_STANDARD  # type: ignore[arg-type]
    )

    assert clv.raw is None and clv.net is None and clv.fair_close_odds is None


def test_clv_from_a_book_still_rejects_invalid_prices_and_indices() -> None:
    with pytest.raises(ValueError, match="decimal odds"):
        closing_line_value_from_book(2.0, (2.0, 1.0, 3.5), 0, method="power", tax_multiplier=1.0)
    with pytest.raises(ValueError, match="selection_index"):
        closing_line_value_from_book(2.0, (2.0, 3.5), 2, method="power", tax_multiplier=1.0)
    with pytest.raises(ValueError, match="selection_index"):
        closing_line_value_from_book(2.0, (2.0, 3.5), -1, method="power", tax_multiplier=1.0)
    # A typo in the method is an error even when there is no closing book to de-vig.
    with pytest.raises(ValueError, match="de-vig method"):
        closing_line_value_from_book(2.0, None, 0, method="powr", tax_multiplier=1.0)


@pytest.mark.parametrize("odds", [1.0, 0.9, -1.0, float("nan"), float("inf")])
def test_clv_rejects_invalid_odds_taken(odds: float) -> None:
    with pytest.raises(ValueError, match="odds_taken"):
        closing_line_value(odds, 0.5, 1.0)


@pytest.mark.parametrize("probability", [0.0, -0.1, 1.1, float("inf")])
def test_clv_rejects_invalid_closing_probability(probability: float) -> None:
    with pytest.raises(ValueError, match="closing_fair_probability"):
        closing_line_value(2.0, probability, 1.0)


def test_clv_rejects_invalid_tax_multiplier() -> None:
    with pytest.raises(ValueError, match="tax_multiplier"):
        closing_line_value(2.0, 0.5, 1.2)


# --------------------------------------------------------------------------- #
# Accumulators (AKO): EV = tau * prod(p_i * o_i) - 1, legs from different matches
# --------------------------------------------------------------------------- #


def _legs(count: int, edge: float = 0.05, odds: float = 2.1) -> list[AccumulatorLeg]:
    return [
        AccumulatorLeg(fixture_id=f"m{index}", probability=(1.0 + edge) / odds, decimal_odds=odds)
        for index in range(count)
    ]


def test_three_leg_accumulator_with_five_percent_per_leg_is_plus_1_9_percent() -> None:
    ev = accumulator_ev_net(_legs(3), TAU_STANDARD)

    assert ev == pytest.approx(1.05**3 * 0.88 - 1.0)
    assert ev == pytest.approx(0.01871, abs=1e-5)
    assert round(ev, 3) == 0.019


def test_accumulator_is_below_break_even_for_the_same_edge_on_single_bets() -> None:
    single = expected_value_net(1.05 / 2.1, 2.1, TAU_STANDARD)  # 5% edge, taxed once
    ako3 = accumulator_ev_net(_legs(3), TAU_STANDARD)

    assert single == pytest.approx(0.05 * 0.88 - 0.12)
    assert single < 0.0 < ako3  # the tax is paid once, the edge compounds


def test_accumulator_price_multiplies_odds_and_probabilities() -> None:
    price = accumulator_price(_legs(3, edge=0.0, odds=2.0))

    assert price.n_legs == 3
    assert price.combined_odds == pytest.approx(8.0)
    assert price.combined_probability == pytest.approx(0.125)
    assert price.fair_odds == pytest.approx(8.0)


def test_accumulator_matches_manual_product_for_uneven_legs() -> None:
    legs = [
        AccumulatorLeg("a", 0.60, 1.80),
        AccumulatorLeg("b", 0.45, 2.40),
        AccumulatorLeg("c", 0.70, 1.55),
    ]
    expected = 0.88 * (0.60 * 1.80) * (0.45 * 2.40) * (0.70 * 1.55) - 1.0

    assert accumulator_ev_net(legs, TAU_STANDARD) == pytest.approx(expected)


def test_accumulator_rejects_two_legs_from_the_same_match() -> None:
    legs = [
        AccumulatorLeg("m1", 0.60, 1.80),
        AccumulatorLeg("m1", 0.55, 1.90),
        AccumulatorLeg("m2", 0.60, 1.80),
    ]

    with pytest.raises(ValueError, match="different matches"):
        accumulator_ev_net(legs, TAU_STANDARD)
    with pytest.raises(ValueError, match="different matches"):
        accumulator_price(legs)


def test_accumulator_duplicate_detection_also_covers_non_string_fixture_ids() -> None:
    legs = [AccumulatorLeg(101, 0.6, 1.8), AccumulatorLeg(102, 0.6, 1.8)]  # type: ignore[arg-type]
    assert accumulator_ev_net(legs, TAU_STANDARD) == pytest.approx(0.88 * (0.6 * 1.8) ** 2 - 1.0)

    same = [AccumulatorLeg(101, 0.6, 1.8), AccumulatorLeg("101", 0.6, 1.8)]  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="different matches"):
        accumulator_ev_net(same, TAU_STANDARD)


def test_accumulator_needs_at_least_two_legs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        accumulator_ev_net([], TAU_STANDARD)
    with pytest.raises(ValueError, match="at least two"):
        accumulator_ev_net(_legs(1), TAU_STANDARD)


@pytest.mark.parametrize("odds", [1.0, 0.8, float("nan"), float("inf")])
def test_accumulator_leg_rejects_invalid_odds(odds: float) -> None:
    with pytest.raises(ValueError, match="decimal_odds"):
        AccumulatorLeg("m1", 0.5, odds)


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan")])
def test_accumulator_leg_rejects_probability_outside_unit_interval(probability: float) -> None:
    with pytest.raises(ValueError, match="probability"):
        AccumulatorLeg("m1", probability, 2.0)


def test_accumulator_leg_rejects_empty_fixture_id() -> None:
    with pytest.raises(ValueError, match="fixture_id"):
        AccumulatorLeg("  ", 0.5, 2.0)


def test_accumulator_rejects_invalid_tax_multiplier() -> None:
    with pytest.raises(ValueError, match="tax_multiplier"):
        accumulator_ev_net(_legs(3), 0.0)


def test_break_even_leg_edge_matches_the_plan_thresholds() -> None:
    # Plan: single 13.6%, 3-leg AKO 4.4% per leg, 5-leg AKO 2.6% per leg (tau = 0.88).
    assert break_even_leg_edge(1, 0.88) == pytest.approx(0.13636, abs=1e-5)
    assert break_even_leg_edge(3, 0.88) == pytest.approx(0.0435, abs=5e-4)
    assert break_even_leg_edge(5, 0.88) == pytest.approx(0.0259, abs=5e-4)
    assert break_even_leg_edge(3, 1.0) == 0.0
    # At the break-even edge the accumulator EV is zero.
    edge = break_even_leg_edge(3, 0.88)
    assert accumulator_ev_net(_legs(3, edge=edge), 0.88) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(ValueError, match="n_legs"):
        break_even_leg_edge(0, 0.88)


# --------------------------------------------------------------------------- #
# Selector: one pick per match, documented tie-break, per-bookmaker tau
# --------------------------------------------------------------------------- #


def _candidate(
    fixture_id: str,
    market_key: str,
    *,
    lower: float = 0.60,
    probability: float | None = None,
    odds: float = 2.0,
    observed_at: datetime = NOW,
    snapshot_id: str = "snapshot-1",
    bookmaker: str = "book-a",
    tax_multiplier: float = 1.0,
    selection: str = "over",
) -> OpportunityCandidate:
    return OpportunityCandidate(
        fixture_id=fixture_id,
        market_key=market_key,
        selection=selection,
        decimal_odds=odds,
        probability=max(lower, 0.63) if probability is None else probability,
        probability_lower_bound=lower,
        observed_at=observed_at,
        snapshot_id=snapshot_id,
        bookmaker=bookmaker,
        tax_multiplier=tax_multiplier,
    )


def test_selector_keeps_one_pick_per_match_by_default() -> None:
    result = select_opportunities(
        [
            _candidate("m1", "goals.over.2.5", lower=0.62),
            _candidate("m1", "btts.yes", lower=0.66),
            _candidate("m1", "cards.over.4.5", lower=0.58),
            _candidate("m2", "goals.over.2.5", lower=0.61),
        ]
    )

    picks = {item.candidate.fixture_id: item.candidate.market_key for item in result.selected}
    assert picks == {"m1": "btts.yes", "m2": "goals.over.2.5"}
    assert result.rejection_counts == {"fixture_limit": 2}


def test_selector_one_pick_per_match_is_the_best_by_conservative_ev() -> None:
    # A lower probability at longer odds can have the higher conservative EV.
    result = select_opportunities(
        [
            _candidate("m1", "a", lower=0.70, odds=1.60),  # EV = +12%
            _candidate("m1", "b", lower=0.55, odds=2.00),  # EV = +10%
            _candidate("m1", "c", lower=0.62, odds=2.00),  # EV = +24%
        ]
    )

    assert [item.candidate.market_key for item in result.selected] == ["c"]


def test_selector_tie_break_prefers_higher_probability_lower_bound() -> None:
    # Both have conservative EV of exactly +0.5 (0.75 * 2.0 - 1 and 0.5 * 3.0 - 1).
    result = select_opportunities(
        [
            _candidate("m1", "long-odds", lower=0.5, probability=0.6, odds=3.0),
            _candidate("m1", "short-odds", lower=0.75, probability=0.8, odds=2.0),
        ],
        max_decimal_odds=5.0,
    )

    assert [item.candidate.market_key for item in result.selected] == ["short-odds"]


def test_selector_tie_break_then_prefers_the_fresher_quote() -> None:
    older = _candidate("m1", "older", observed_at=NOW - timedelta(minutes=5), snapshot_id="s-old")
    fresher = _candidate("m1", "fresher", observed_at=NOW, snapshot_id="s-new")

    result = select_opportunities([older, fresher])

    assert [item.candidate.market_key for item in result.selected] == ["fresher"]


def test_selector_tie_break_finally_uses_the_stable_logical_key() -> None:
    first = _candidate("m1", "market-b")
    second = _candidate("m1", "market-a")

    forward = select_opportunities([first, second])
    backward = select_opportunities([second, first])

    assert [item.candidate.market_key for item in forward.selected] == ["market-a"]
    assert [item.candidate.market_key for item in backward.selected] == ["market-a"]


def test_selector_pick_limit_is_still_configurable() -> None:
    result = select_opportunities(
        [
            _candidate("m1", "a", lower=0.64),
            _candidate("m1", "b", lower=0.62),
            _candidate("m1", "c", lower=0.60),
        ],
        max_per_fixture=2,
    )

    assert [item.candidate.market_key for item in result.selected] == ["a", "b"]


def test_selector_without_profiles_returns_the_same_candidate_objects() -> None:
    candidate = _candidate("m1", "a", tax_multiplier=0.88, lower=0.70)

    result = select_opportunities([candidate])

    assert result.selected[0].candidate is candidate


def test_selector_uses_the_bookmaker_profile_tau_when_profiles_are_given() -> None:
    # lower = 0.55 at odds 2.0: +3.4% at Betclic (0.94), -3.2% at Fortuna (0.88).
    betclic = _candidate("m1", "a", lower=0.55, bookmaker="Betclic", tax_multiplier=1.0)
    fortuna = _candidate("m2", "a", lower=0.55, bookmaker="Fortuna", tax_multiplier=1.0)

    result = select_opportunities([betclic, fortuna], tax_profiles=DEFAULT_TAX_PROFILES)

    assert [item.candidate.fixture_id for item in result.selected] == ["m1"]
    assert result.selected[0].candidate.tax_multiplier == 0.94
    assert result.selected[0].conservative_ev_net == pytest.approx(0.55 * 0.94 * 2.0 - 1.0)
    assert result.rejection_counts == {"conservative_ev_below_threshold": 1}


def test_selector_profile_overrides_the_candidates_own_tax_multiplier() -> None:
    # The candidate default of 1.0 must not survive when a profile registry is supplied.
    candidate = _candidate("m1", "a", lower=0.55, bookmaker="Fortuna", tax_multiplier=1.0)

    without = select_opportunities([candidate])
    with_profiles = select_opportunities([candidate], tax_profiles=DEFAULT_TAX_PROFILES)

    assert len(without.selected) == 1
    assert with_profiles.selected == ()


def test_selector_fails_closed_for_unknown_bookmakers_when_profiles_are_given() -> None:
    result = select_opportunities(
        [
            _candidate("m1", "a", lower=0.70, bookmaker="pinnacle"),
            _candidate("m2", "a", lower=0.70, bookmaker="consensus"),
            _candidate("m3", "a", lower=0.70, bookmaker="Fortuna"),
        ],
        tax_profiles=DEFAULT_TAX_PROFILES,
    )

    assert [item.candidate.fixture_id for item in result.selected] == ["m3"]
    assert result.rejection_counts == {"unknown_bookmaker_tax": 2}


def test_selector_best_price_is_compared_after_tax_per_bookmaker() -> None:
    # Fortuna's higher headline price loses to Betclic once tau differs (0.88 vs 0.94).
    fortuna = _candidate("m1", "a", lower=0.60, odds=2.00, bookmaker="Fortuna")
    betclic = _candidate("m1", "a", lower=0.60, odds=1.95, bookmaker="Betclic")

    result = select_opportunities([fortuna, betclic], tax_profiles=DEFAULT_TAX_PROFILES)

    assert [item.candidate.bookmaker for item in result.selected] == ["Betclic"]
    assert result.rejection_counts == {"inferior_price": 1}


def test_selector_ev_gate_uses_the_lower_bound_not_the_point_estimate() -> None:
    noisy = _candidate("m1", "a", lower=0.50, probability=0.62, odds=2.0)  # lower EV = 0
    solid = _candidate("m2", "a", lower=0.56, probability=0.58, odds=2.0)  # lower EV = +12%

    result = select_opportunities([noisy, solid])

    assert [item.candidate.fixture_id for item in result.selected] == ["m2"]
    assert result.rejection_counts == {"conservative_ev_below_threshold": 1}
