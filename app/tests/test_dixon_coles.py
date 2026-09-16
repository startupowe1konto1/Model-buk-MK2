from model_buk.models.dixon_coles import (
    low_score_correction,
    market_probabilities,
    score_matrix,
)


def test_score_matrix_is_normalized():
    matrix = score_matrix(1.6, 1.1, rho=-0.08, max_goals=10)
    assert abs(sum(sum(row) for row in matrix) - 1.0) < 1e-12
    assert all(value >= 0.0 for row in matrix for value in row)


def test_market_probabilities_are_coherent():
    markets = market_probabilities(score_matrix(1.6, 1.1, rho=-0.08))
    assert abs(
        markets["result.home"] + markets["result.draw"] + markets["result.away"] - 1.0
    ) < 1e-12
    assert abs(markets["btts.yes"] + markets["btts.no"] - 1.0) < 1e-12
    assert abs(markets["goals.total.over.2.5"] + markets["goals.total.under.2.5"] - 1.0) < 1e-12


def test_rho_changes_only_low_score_cells():
    assert low_score_correction(2, 1, 1.5, 1.0, -0.1) == 1.0
    assert low_score_correction(1, 1, 1.5, 1.0, -0.1) > 1.0
