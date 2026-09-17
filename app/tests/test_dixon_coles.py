import math

import pandas as pd
import pytest

from model_buk.models.dixon_coles import (
    fit_dixon_coles,
    low_score_correction,
    market_probabilities,
    score_matrix,
    time_decay_weights,
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


def test_time_decay_defaults_to_equal_weights():
    weights = time_decay_weights(["2024-01-01", "2024-01-11"])
    assert weights.tolist() == [1.0, 1.0]


def test_time_decay_half_life_and_xi_are_equivalent():
    dates = ["2024-01-01", "2024-01-11", "2024-01-21"]
    by_half_life = time_decay_weights(dates, half_life_days=10)
    by_xi = time_decay_weights(dates, xi=math.log(2.0) / 10.0)
    assert by_half_life == pytest.approx([0.25, 0.5, 1.0])
    assert by_xi == pytest.approx(by_half_life)


def test_time_decay_rejects_ambiguous_or_future_configuration():
    with pytest.raises(ValueError, match="either"):
        time_decay_weights(["2024-01-01"], half_life_days=30, xi=0.01)
    with pytest.raises(ValueError, match="earlier"):
        time_decay_weights(["2024-01-02"], reference_date="2024-01-01", xi=0.01)


def test_fit_dixon_coles_supports_decay_without_changing_default_api():
    teams = ["A", "B", "C", "D"]
    rows = []
    for index in range(48):
        home = teams[index % 4]
        away = teams[(index + 1 + index // 4) % 4]
        if home == away:
            away = teams[(teams.index(home) + 1) % 4]
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 3),
                "home_team": home,
                "away_team": away,
                "home_goals": (index + teams.index(home)) % 3,
                "away_goals": (index + teams.index(away)) % 2,
            }
        )
    matches = pd.DataFrame(rows)

    unweighted = fit_dixon_coles(matches)
    decayed = fit_dixon_coles(matches, half_life_days=60)

    assert unweighted.xi == 0.0
    assert decayed.xi == pytest.approx(math.log(2.0) / 60.0)
    assert sum(decayed.attack.values()) == pytest.approx(0.0, abs=1e-10)
    assert sum(decayed.defence.values()) == pytest.approx(0.0, abs=1e-10)
    assert all(rate > 0.0 for rate in decayed.expected_goals("A", "B"))


def test_l2_shrinkage_reduces_team_strength_magnitude():
    teams = ["A", "B", "C", "D"]
    rows = []
    for index in range(64):
        home = teams[index % 4]
        away = teams[(index + 1) % 4]
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 2),
                "home_team": home,
                "away_team": away,
                "home_goals": 4 if home == "A" else index % 2,
                "away_goals": 0 if away == "D" else (index + 1) % 2,
            }
        )
    matches = pd.DataFrame(rows)

    unregularized = fit_dixon_coles(matches, l2_strength=0.0)
    regularized = fit_dixon_coles(matches, l2_strength=10.0)
    raw_norm = sum(value * value for value in unregularized.attack.values())
    shrunk_norm = sum(value * value for value in regularized.attack.values())

    assert regularized.l2_strength == 10.0
    assert shrunk_norm < raw_norm


def test_fit_rejects_negative_l2_strength():
    with pytest.raises(ValueError, match="l2_strength"):
        fit_dixon_coles(pd.DataFrame({"date": ["2024-01-01"]}), l2_strength=-1.0)


def test_xg_quasi_likelihood_accepts_partial_historical_coverage():
    teams = ["A", "B", "C", "D"]
    rows = []
    for index in range(48):
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 3),
                "home_team": teams[index % 4],
                "away_team": teams[(index + 1) % 4],
                "home_goals": index % 3,
                "away_goals": index % 2,
                "home_xg": 1.4 if index % 5 else float("nan"),
                "away_xg": 0.9 if index % 7 else float("nan"),
            }
        )

    model = fit_dixon_coles(pd.DataFrame(rows), xg_weight=0.5)

    assert model.xg_weight == 0.5
    assert all(rate > 0.0 for rate in model.expected_goals("A", "B"))
