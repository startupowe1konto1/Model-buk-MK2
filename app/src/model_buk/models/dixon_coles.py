from __future__ import annotations

import math


def poisson_pmf(goals: int, rate: float) -> float:
    if goals < 0:
        return 0.0
    if rate <= 0.0:
        raise ValueError("Poisson rate must be positive")
    return math.exp(-rate) * (rate**goals) / math.factorial(goals)


def low_score_correction(
    home_goals: int,
    away_goals: int,
    home_rate: float,
    away_rate: float,
    rho: float,
) -> float:
    """Dixon-Coles correction for the four low-scoring outcomes."""
    if (home_goals, away_goals) == (0, 0):
        tau = 1.0 - home_rate * away_rate * rho
    elif (home_goals, away_goals) == (0, 1):
        tau = 1.0 + home_rate * rho
    elif (home_goals, away_goals) == (1, 0):
        tau = 1.0 + away_rate * rho
    elif (home_goals, away_goals) == (1, 1):
        tau = 1.0 - rho
    else:
        tau = 1.0
    if tau <= 0.0:
        raise ValueError("rho produces a non-positive Dixon-Coles correction")
    return tau


def score_matrix(
    home_rate: float,
    away_rate: float,
    *,
    rho: float = 0.0,
    max_goals: int = 10,
) -> list[list[float]]:
    """Return a normalized truncated joint score distribution."""
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    matrix = []
    for home_goals in range(max_goals + 1):
        row = []
        for away_goals in range(max_goals + 1):
            probability = (
                poisson_pmf(home_goals, home_rate)
                * poisson_pmf(away_goals, away_rate)
                * low_score_correction(
                    home_goals,
                    away_goals,
                    home_rate,
                    away_rate,
                    rho,
                )
            )
            row.append(probability)
        matrix.append(row)
    total = sum(sum(row) for row in matrix)
    if total <= 0.0:
        raise ValueError("score matrix has no probability mass")
    return [[value / total for value in row] for row in matrix]


def market_probabilities(matrix: list[list[float]], *, total_line: float = 2.5) -> dict[str, float]:
    home = draw = away = btts_yes = over = 0.0
    for home_goals, row in enumerate(matrix):
        for away_goals, probability in enumerate(row):
            if home_goals > away_goals:
                home += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away += probability
            if home_goals > 0 and away_goals > 0:
                btts_yes += probability
            if home_goals + away_goals > total_line:
                over += probability
    return {
        "result.home": home,
        "result.draw": draw,
        "result.away": away,
        "btts.yes": btts_yes,
        "btts.no": 1.0 - btts_yes,
        f"goals.total.over.{total_line}": over,
        f"goals.total.under.{total_line}": 1.0 - over,
    }
