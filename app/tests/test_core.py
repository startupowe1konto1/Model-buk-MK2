from __future__ import annotations

import numpy as np
import pandas as pd

from model_buk.backtest import devig_two_way
from model_buk.features import build_features
from model_buk.model import over_probability


def tiny_frame() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2025-01-01", periods=8, freq="7D")
    for i, date in enumerate(dates):
        rows.append(
            {
                "date": date,
                "internal_match_id": i,
                "home_team": "A" if i % 2 == 0 else "B",
                "away_team": "B" if i % 2 == 0 else "A",
                "home_corners": 4 + (i % 3),
                "away_corners": 3 + (i % 2),
                "home_shots": 10 + i,
                "away_shots": 8 + i,
                "home_sot": 4,
                "away_sot": 3,
                "home_goals": 1,
                "away_goals": 1,
                "home_xg": 1.2,
                "away_xg": 1.0,
                "home_ppda": 10.0,
                "away_ppda": 11.0,
                "home_deep": 6,
                "away_deep": 5,
            }
        )
    return pd.DataFrame(rows)


def test_devig_sums_to_one() -> None:
    po, pu = devig_two_way(1.80, 2.05)
    assert abs(po + pu - 1.0) < 1e-12


def test_over_probability_is_monotone_in_line() -> None:
    probs = [float(over_probability(10.0, line, 0.02)) for line in (7.5, 8.5, 9.5, 10.5)]
    assert all(0 <= p <= 1 for p in probs)
    assert probs == sorted(probs, reverse=True)


def test_current_match_target_does_not_enter_own_features() -> None:
    raw = tiny_frame()
    f1 = build_features(raw)
    target_idx = 6
    changed = raw.copy()
    changed.loc[target_idx, "home_corners"] = 99
    changed.loc[target_idx, "away_corners"] = 88
    f2 = build_features(changed)
    feature_cols = [c for c in f1.columns if c.startswith("home_") or c.startswith("away_")]
    feature_cols = [c for c in feature_cols if c not in {"home_corners", "away_corners"}]
    a = f1.loc[target_idx, feature_cols]
    b = f2.loc[target_idx, feature_cols]
    pd.testing.assert_series_equal(a, b, check_names=False)
