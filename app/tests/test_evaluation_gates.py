from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from model_buk.evaluation.gates import (
    PromotionPolicy,
    selection_reliability,
    bootstrap_cluster_mean_interval,
    bootstrap_mean_interval,
    calibration_report,
    decide_promotion,
    evaluate_betting_returns,
    evaluate_model_against_market,
    multiclass_brier_score,
    multiclass_log_loss,
)


def test_multiclass_proper_scores_match_manual_values() -> None:
    actual = np.array([0, 1, 2])
    probabilities = np.array(
        [[0.8, 0.1, 0.1], [0.2, 0.7, 0.1], [0.1, 0.2, 0.7]]
    )

    expected_log_loss = -np.mean(np.log([0.8, 0.7, 0.7]))
    targets = np.eye(3)[actual]
    expected_brier = np.mean(np.sum((probabilities - targets) ** 2, axis=1))

    assert multiclass_log_loss(actual, probabilities) == pytest.approx(expected_log_loss)
    assert multiclass_brier_score(actual, probabilities) == pytest.approx(expected_brier)


def test_probability_rows_are_normalized_but_invalid_values_are_rejected() -> None:
    actual = ["home", "away"]
    unnormalised = [[6.0, 3.0, 1.0], [1.0, 2.0, 7.0]]

    assert multiclass_log_loss(actual, unnormalised, classes=("home", "draw", "away")) < 0.6
    with pytest.raises(ValueError, match="non-negative"):
        multiclass_brier_score([0], [[0.8, -0.1, 0.3]])


def test_ece_uses_top_label_confidence_and_is_weighted_by_bin_size() -> None:
    actual = [0, 0, 1, 1]
    probabilities = [
        [0.9, 0.1],
        [0.8, 0.2],
        [0.6, 0.4],  # confidently wrong
        [0.3, 0.7],
    ]

    report = calibration_report(actual, probabilities, bins=2)

    # All rows land in the upper bin: mean confidence=.75, accuracy=.75.
    assert report.ece == pytest.approx(0.0)
    assert sum(item.count for item in report.bins) == 4


def test_betting_metrics_apply_tax_and_execution_cost_and_separate_coverage() -> None:
    result = evaluate_betting_returns(
        wins=[True, False, True, False],
        decimal_odds=[2.0, 2.0, np.nan, 3.0],
        selected=[True, True, False, False],
        tax_multiplier=0.88,
        execution_cost=0.02,
    )

    # Profits: (0.88*2 - 1 - .02) + (-1 - .02) = -.28 units.
    assert result.total_profit == pytest.approx(-0.28)
    assert result.roi == pytest.approx(-0.14)
    assert result.yield_rate == pytest.approx(-0.14)
    assert result.coverage == pytest.approx(3 / 4)
    assert result.selectivity == pytest.approx(2 / 3)
    assert result.bets == 2


def test_bootstrap_interval_is_deterministic_for_a_seed() -> None:
    values = np.array([-1.0, 0.5, 0.7, 1.2, 1.4])

    first = bootstrap_mean_interval(values, n_resamples=500, seed=17)
    second = bootstrap_mean_interval(values, n_resamples=500, seed=17)

    assert first == second
    assert first.lower <= first.estimate <= first.upper


def test_cluster_bootstrap_is_deterministic_and_preserves_dependence() -> None:
    values = np.array([0.0, 0.0, 0.0, 10.0, 10.0, 10.0])
    clusters = ["low", "low", "low", "high", "high", "high"]

    first = bootstrap_cluster_mean_interval(values, clusters, n_resamples=500, seed=17)
    second = bootstrap_cluster_mean_interval(values, clusters, n_resamples=500, seed=17)
    iid = bootstrap_mean_interval(values, n_resamples=500, seed=17)

    assert first == second
    assert first.lower <= first.estimate <= first.upper
    # Correlated observations must produce a wider uncertainty interval than IID rows.
    assert first.upper - first.lower > iid.upper - iid.lower


def test_cluster_bootstrap_yield_uses_only_selected_bets() -> None:
    actual = np.zeros(6, dtype=int)
    model = np.tile([[0.8, 0.1, 0.1]], (6, 1))
    market = np.tile([[1 / 3, 1 / 3, 1 / 3]], (6, 1))
    odds = np.array([[2.0, 1.1, 1.1], [10.0, 1.1, 1.1], [2.0, 1.1, 1.1],
                     [3.0, 1.1, 1.1], [2.0, 1.1, 1.1], [4.0, 1.1, 1.1]])
    selected = np.array([False, True, False, True, False, True])
    clusters = ["unused-a", "bet-a", "unused-b", "bet-b", "unused-c", "bet-c"]

    report = evaluate_model_against_market(
        actual,
        model,
        market,
        offered_odds=odds,
        bet_selections=np.zeros(6, dtype=int),
        selected=selected,
        bootstrap_clusters=clusters,
        bootstrap_resamples=500,
        seed=23,
    )

    assert report.betting is not None and report.betting.bets == 3
    assert report.net_yield_ci is not None
    expected = bootstrap_cluster_mean_interval(
        report.betting.net_returns,
        ["bet-a", "bet-b", "bet-c"],
        n_resamples=500,
        seed=24,
    )
    assert report.net_yield_ci == expected


def _promotion_frame(repeats: int = 40) -> tuple[list[int], np.ndarray, np.ndarray]:
    # Candidate confidence is empirically calibrated: four wins and one miss per block.
    actual = np.tile([0, 0, 0, 0, 2], repeats)
    model = np.tile([[0.8, 0.1, 0.1]], (len(actual), 1))
    market = np.tile([[1 / 3, 1 / 3, 1 / 3]], (len(actual), 1))
    return actual.tolist(), model, market


def test_conservative_promotion_requires_significant_market_improvement() -> None:
    actual, model, market = _promotion_frame()
    report = evaluate_model_against_market(
        actual,
        model,
        market,
        calibration_bins=10,
        bootstrap_resamples=1000,
        seed=9,
    )
    decision = decide_promotion(
        report,
        PromotionPolicy(min_samples=100, max_ece_regression=0.01),
    )

    assert report.model.log_loss < report.market.log_loss
    assert report.log_loss_improvement_ci.lower > 0
    assert decision.promote
    assert all(decision.checks.values())


def test_promotion_rejects_too_few_samples_and_calibration_regression() -> None:
    actual, model, market = _promotion_frame(repeats=4)
    report = evaluate_model_against_market(
        actual, model, market, bootstrap_resamples=200, seed=3
    )
    deliberately_uncalibrated = replace(
        report,
        model=replace(report.model, ece=report.market.ece + 0.2),
    )
    decision = decide_promotion(
        deliberately_uncalibrated,
        PromotionPolicy(min_samples=100, max_ece_regression=0.01),
    )

    assert not decision.promote
    assert not decision.checks["enough_evaluation_samples"]
    assert not decision.checks["calibration_regression_acceptable"]
    assert "insufficient evaluation samples" in decision.reasons[0]


def test_optional_net_yield_gate_uses_the_lower_confidence_bound() -> None:
    actual, model, market = _promotion_frame()
    report = evaluate_model_against_market(
        actual,
        model,
        market,
        offered_odds=np.full_like(model, 1.10),
        bet_selections=np.zeros(len(actual), dtype=int),
        tax_multiplier=1.0,
        bootstrap_resamples=500,
        seed=7,
    )
    decision = decide_promotion(
        report,
        PromotionPolicy(
            min_samples=100,
            min_bets=100,
            min_net_yield_lower_bound=0.0,
        ),
    )

    assert report.betting is not None and report.betting.bets == len(actual)
    assert report.net_yield_ci is not None and report.net_yield_ci.upper < 0.0
    assert not decision.promote
    assert not decision.checks["net_yield_confident"]


def _legs(model_p, market_p, n_hits, n_total):
    """n_total legs sharing one model and one market price; n_hits of them won."""
    hits = np.array([True] * n_hits + [False] * (n_total - n_hits))
    return hits, np.full(n_total, model_p), np.full(n_total, market_p)


class TestSelectionReliability:
    def test_nothing_selected_returns_none(self):
        hits, model_p, market_p = _legs(0.4, 0.2, 5, 10)
        assert selection_reliability(hits, model_p, market_p, selected=np.zeros(10, bool)) is None

    def test_overstatement_is_positive_when_promise_exceeds_outcome(self):
        hits, model_p, market_p = _legs(0.40, 0.40, 10, 100)
        report = selection_reliability(hits, model_p, market_p)
        assert report.realised_rate == pytest.approx(0.10)
        assert report.model_overstatement == pytest.approx(0.30)

    def test_understatement_is_negative(self):
        hits, model_p, market_p = _legs(0.10, 0.10, 40, 100)
        assert selection_reliability(hits, model_p, market_p).model_overstatement == pytest.approx(-0.30)

    def test_honest_selection_has_no_excess(self):
        hits, model_p, market_p = _legs(0.25, 0.25, 25, 100)
        report = selection_reliability(hits, model_p, market_p)
        assert report.model_overstatement == pytest.approx(0.0)
        assert report.excess_overstatement == pytest.approx(0.0)

    def test_excess_isolates_the_model_from_hard_fixtures(self):
        # Both sides overstate by 0.20: the legs were simply hard, and the
        # market missed them exactly as badly. That is not a model defect.
        hits, model_p, market_p = _legs(0.30, 0.30, 10, 100)
        report = selection_reliability(hits, model_p, market_p)
        assert report.model_overstatement == pytest.approx(0.20)
        assert report.market_overstatement == pytest.approx(0.20)
        assert report.excess_overstatement == pytest.approx(0.0)

    def test_excess_is_positive_when_only_the_model_overstates(self):
        # The shape measured on 1X2: the model promises far more than it
        # delivers while the market, on the same legs, is nearly exact.
        hits, model_p, market_p = _legs(0.3168, 0.2175, 2103, 10000)
        report = selection_reliability(hits, model_p, market_p)
        assert report.model_overstatement == pytest.approx(0.1065, abs=1e-4)
        assert report.market_overstatement == pytest.approx(0.0072, abs=1e-4)
        assert report.excess_overstatement == pytest.approx(0.0993, abs=1e-4)

    def test_unselected_legs_cannot_move_the_result(self):
        hits = np.array([True, False, False, False])
        model_p = np.array([0.50, 0.50, 0.99, 0.99])
        market_p = np.array([0.50, 0.50, 0.01, 0.01])
        mask = np.array([True, True, False, False])
        report = selection_reliability(hits, model_p, market_p, selected=mask)
        assert report.n == 2
        assert report.mean_model_probability == pytest.approx(0.50)
        assert report.mean_market_probability == pytest.approx(0.50)

    def test_both_sides_are_scored_on_the_same_outcomes(self):
        # Swapping which legs won must move both overstatements together;
        # if the market were scored on a different outcome column, the excess
        # would change here and it must not.
        hits_a = np.array([True, True, False, False])
        hits_b = np.array([False, False, True, True])
        model_p = np.full(4, 0.60)
        market_p = np.full(4, 0.40)
        a = selection_reliability(hits_a, model_p, market_p)
        b = selection_reliability(hits_b, model_p, market_p)
        assert a.excess_overstatement == pytest.approx(b.excess_overstatement)

    def test_misaligned_inputs_are_refused(self):
        with pytest.raises(ValueError):
            selection_reliability(np.array([True, False]), np.array([0.5]), np.array([0.5]))

    def test_misaligned_mask_is_refused(self):
        hits, model_p, market_p = _legs(0.4, 0.2, 1, 3)
        with pytest.raises(ValueError):
            selection_reliability(hits, model_p, market_p, selected=np.array([True]))


class TestSelectionReliabilityInReport:
    def _report(self, **kwargs):
        actual = np.array([0, 1, 0, 1, 0, 1, 0, 1])
        model = np.tile(np.array([[0.70, 0.30]]), (8, 1))
        market = np.tile(np.array([[0.50, 0.50]]), (8, 1))
        return evaluate_model_against_market(
            actual, model, market, classes=[0, 1], bootstrap_resamples=50, **kwargs
        )

    def test_absent_without_odds(self):
        assert self._report().selection is None

    def test_present_with_odds(self):
        report = self._report(offered_odds=np.full((8, 2), 2.0))
        assert report.selection is not None
        assert report.selection.n == 8

    def test_counts_exactly_the_legs_that_were_bet(self):
        # The diagnostic must describe the same legs the betting evaluation
        # priced - otherwise the two halves of the report disagree.
        odds = np.full((8, 2), 2.0)
        odds[0, 0] = float("nan")
        report = self._report(offered_odds=odds)
        assert report.selection.n == report.betting.bets

    def test_respects_the_selected_mask(self):
        mask = np.array([True, True, True, True, False, False, False, False])
        report = self._report(offered_odds=np.full((8, 2), 2.0), selected=mask)
        assert report.selection.n == 4

    def test_selecting_an_unpriceable_leg_is_refused(self):
        # This is what keeps the diagnostic and the betting evaluation on the
        # same legs: a mask covering a missing price is rejected outright, so
        # the two can never describe different sets. Pinned here because the
        # diagnostic relies on it rather than re-checking availability itself.
        odds = np.full((8, 2), 2.0)
        odds[0, 0] = float("nan")
        with pytest.raises(ValueError, match="subset of available"):
            self._report(offered_odds=odds, selected=np.ones(8, dtype=bool))

    def test_market_column_comes_from_the_market_matrix(self):
        report = self._report(offered_odds=np.full((8, 2), 2.0))
        # model says 0.70 on the selected side, market says 0.50
        assert report.selection.mean_model_probability == pytest.approx(0.70)
        assert report.selection.mean_market_probability == pytest.approx(0.50)
