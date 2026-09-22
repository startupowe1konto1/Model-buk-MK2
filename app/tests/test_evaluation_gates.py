from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from model_buk.evaluation.gates import (
    PromotionPolicy,
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
