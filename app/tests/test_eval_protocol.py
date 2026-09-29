from __future__ import annotations

import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest

from model_buk.evaluation.framework import WalkForwardConfig, evaluate_model, league_frequency_spec
from model_buk.evaluation.protocol import GOAL_MARKETS, MARKET_BTTS, load_predictions, save_result


def _matches(n_per_season: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    fixture_id = 1
    for season in (2020, 2021, 2022):
        start = pd.Timestamp(f"{season}-08-01", tz="UTC")
        for i in range(n_per_season):
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "league_code": "TST",
                    "season": season,
                    "round": f"Regular Season - {i // 6 + 1}",
                    "kickoff_utc": (start + pd.Timedelta(days=7 * (i // 6))).isoformat(),
                    "status_short": "FT",
                    "home_goals": int(rng.poisson(1.5)),
                    "away_goals": int(rng.poisson(1.1)),
                }
            )
            fixture_id += 1
    return pd.DataFrame(rows)


def test_btts_outcome_keeps_missing_scores_missing():
    frame = pd.DataFrame({"home_goals": [1, 0, 2, None], "away_goals": [1, 3, 0, 1]})
    out = MARKET_BTTS.outcome(frame)
    assert out[:3].tolist() == [1.0, 0.0, 0.0]
    assert np.isnan(out[3])


def test_goal_markets_are_the_protocol_set():
    assert [m.name for m in GOAL_MARKETS] == ["1x2", "over_under_1.5", "over_under_2.5", "over_under_3.5", "btts"]


def test_save_and_load_round_trip_and_stale_protocol_is_rejected(tmp_path):
    config = WalkForwardConfig(test_seasons=[2022], inner_seasons=1)
    result = evaluate_model(league_frequency_spec(MARKET_BTTS), _matches(), MARKET_BTTS, config)
    path = save_result(result, tmp_path)

    loaded = load_predictions(result.model, MARKET_BTTS, tmp_path)
    assert len(loaded) == len(result.predictions)
    assert np.allclose(loaded[["p_no", "p_yes"]].to_numpy(), result.probabilities(), equal_nan=True)

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE forecasts_btts SET protocol_id = 'old'")
        connection.commit()
    with pytest.raises(ValueError, match="protocol"):
        load_predictions(result.model, MARKET_BTTS, tmp_path)
