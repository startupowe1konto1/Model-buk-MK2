from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from model_buk.models.negative_binomial import (
    NegativeBinomialModel,
    fit_negative_binomial,
    nb2_pmf,
)


def _matches() -> pd.DataFrame:
    teams = ("A", "B", "C", "D")
    rows = []
    for index in range(64):
        home = teams[index % 4]
        away = teams[(index + 1 + index // 8) % 4]
        if home == away:
            away = teams[(teams.index(home) + 1) % 4]
        rows.append(
            {
                "date": pd.Timestamp("2023-08-01") + pd.Timedelta(days=index * 3),
                "home_team": home,
                "away_team": away,
                "home_goals": (index * 3 + index // 7) % 5,
                "away_goals": (index + index // 5) % 4,
            }
        )
    return pd.DataFrame(rows)


def test_fit_has_positive_means_and_shared_positive_dispersion():
    model = fit_negative_binomial(_matches(), l2_strength=0.2)

    assert model.alpha > 0.0
    assert math.isfinite(model.alpha)
    assert all(mean > 0.0 for mean in model.expected_goals("A", "B"))
    assert abs(sum(model.attack.values())) < 1e-10
    assert abs(sum(model.defence.values())) < 1e-10


def test_score_matrix_sums_to_one():
    model = fit_negative_binomial(_matches())
    matrix = np.asarray(model.score_matrix("A", "B", max_goals=12))

    assert matrix.shape == (13, 13)
    assert matrix.sum() == pytest.approx(1.0)
    assert np.all(matrix >= 0.0)


def test_overdispersion_increases_far_tail_probability():
    near_poisson_tail = nb2_pmf(9, mean=1.5, alpha=1e-6)
    overdispersed_tail = nb2_pmf(9, mean=1.5, alpha=0.8)

    assert overdispersed_tail > near_poisson_tail


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (_matches().drop(columns="date"), "missing required columns"),
        (_matches().assign(home_goals=-1), "non-negative"),
        (_matches().assign(away_goals=1.5), "integers"),
        (_matches().assign(home_team="A", away_team="A"), "must differ"),
    ],
)
def test_fit_rejects_invalid_match_inputs(frame: pd.DataFrame, message: str):
    with pytest.raises(ValueError, match=message):
        fit_negative_binomial(frame)


def test_model_and_distribution_validate_invalid_parameters():
    with pytest.raises(ValueError, match="positive and finite"):
        nb2_pmf(1, mean=0.0, alpha=0.2)
    with pytest.raises(ValueError, match="positive and finite"):
        nb2_pmf(1, mean=1.0, alpha=0.0)
    with pytest.raises(ValueError, match="Unknown team"):
        fit_negative_binomial(_matches()).expected_goals("Z", "A")
    with pytest.raises(ValueError, match="max_goals"):
        fit_negative_binomial(_matches()).score_matrix("A", "B", max_goals=0)
    with pytest.raises(ValueError, match="l2_strength"):
        fit_negative_binomial(_matches(), l2_strength=-0.1)


def test_manually_constructed_model_requires_positive_alpha():
    with pytest.raises(ValueError, match="alpha"):
        NegativeBinomialModel(
            teams=("A", "B"),
            attack={"A": 0.0, "B": 0.0},
            defence={"A": 0.0, "B": 0.0},
            intercept=0.0,
            home_advantage=0.0,
            alpha=0.0,
            l2_strength=0.0,
        )
