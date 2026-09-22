from __future__ import annotations

import math

import pandas as pd
import pytest

from model_buk.asof_features import TargetFixture, build_asof_features


def _row(
    match_id: str,
    date: str,
    available_at: str,
    home: str,
    away: str,
    home_goals: float,
    away_goals: float,
    *,
    home_corners: float | None = None,
    away_corners: float | None = None,
) -> dict[str, object]:
    return {
        "internal_match_id": match_id,
        "date": date,
        "available_at": available_at,
        "home_team": home,
        "away_team": away,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_corners": home_corners,
        "away_corners": away_corners,
    }


def _target() -> TargetFixture:
    return TargetFixture(
        fixture_id="target-1",
        kickoff="2026-09-20T18:00:00+00:00",
        home_team="A",
        away_team="B",
    )


def test_records_published_after_cutoff_cannot_change_features() -> None:
    before = _row(
        "m1", "2026-09-01T18:00:00Z", "2026-09-01T20:00:00Z", "A", "C", 2, 0,
        home_corners=5, away_corners=2,
    )
    after = _row(
        "m2", "2026-09-10T18:00:00Z", "2026-09-19T13:00:01Z", "A", "D", 99, 0,
        home_corners=99, away_corners=0,
    )
    cutoff = "2026-09-19T13:00:00+00:00"

    baseline = build_asof_features(pd.DataFrame([before]), _target(), cutoff, windows=(5,))
    with_late_record = build_asof_features(
        pd.DataFrame([before, after]), _target(), cutoff, windows=(5,)
    )

    pd.testing.assert_series_equal(
        pd.Series(baseline), pd.Series(with_late_record), check_names=False
    )
    assert with_late_record["home_goals_for_overall_r5"] == 2.0


def test_post_cutoff_revision_cannot_replace_known_version() -> None:
    original = _row(
        "m1", "2026-09-01T18:00:00Z", "2026-09-01T20:00:00Z", "A", "C", 1, 0
    )
    corrected_late = {**original, "available_at": "2026-09-19T13:00:01Z", "home_goals": 50}

    features = build_asof_features(
        pd.DataFrame([corrected_late, original]),
        _target(),
        "2026-09-19T13:00:00Z",
        windows=(5,),
    )

    assert features["eligible_matches"] == 1
    assert features["home_goals_for_overall_r5"] == 1.0


def test_future_event_is_excluded_even_if_feed_claims_it_was_available() -> None:
    impossible_future_result = _row(
        "m2", "2026-09-19T14:00:00Z", "2026-09-19T12:00:00Z", "A", "D", 50, 0
    )

    features = build_asof_features(
        pd.DataFrame([impossible_future_result]),
        _target(),
        "2026-09-19T13:00:00Z",
        windows=(5,),
    )

    assert features["eligible_matches"] == 0
    assert math.isnan(features["home_goals_for_overall_r5"])


def test_rolling_scopes_rest_and_order_are_deterministic() -> None:
    rows = [
        _row("m2", "2026-09-08T18:00:00Z", "2026-09-08T20:00:00Z", "C", "A", 0, 3),
        _row("m1", "2026-09-01T18:00:00Z", "2026-09-01T20:00:00Z", "A", "D", 1, 0),
        _row("m3", "2026-09-15T18:00:00Z", "2026-09-15T20:00:00Z", "A", "E", 5, 1),
    ]
    cutoff = "2026-09-19T13:00:00Z"

    first = build_asof_features(pd.DataFrame(rows), _target(), cutoff, windows=(2,))
    shuffled = build_asof_features(
        pd.DataFrame(rows).sample(frac=1, random_state=17), _target(), cutoff, windows=(2,)
    )

    pd.testing.assert_series_equal(pd.Series(first), pd.Series(shuffled), check_names=False)
    assert first["home_goals_for_overall_r2"] == 4.0
    assert first["home_goals_for_home_r2"] == 3.0
    assert first["home_goals_for_away_r2"] == 3.0
    assert first["home_rest_days"] == pytest.approx(5.0)


def test_opponent_adjustment_uses_opponent_history_before_each_match() -> None:
    rows = [
        _row("m1", "2026-08-01T18:00:00Z", "2026-08-01T20:00:00Z", "C", "B", 2, 0),
        _row("m2", "2026-08-08T18:00:00Z", "2026-08-08T20:00:00Z", "A", "B", 4, 0),
    ]

    features = build_asof_features(
        pd.DataFrame(rows), _target(), "2026-09-19T13:00:00Z", windows=(5,)
    )

    assert features["home_goals_for_opponent_adjusted_overall_r5"] == 2.0


def test_missing_optional_metrics_produce_nan_instead_of_zero() -> None:
    frame = pd.DataFrame([
        _row("m1", "2026-09-01T18:00:00Z", "2026-09-01T20:00:00Z", "A", "C", 1, 0)
    ])

    features = build_asof_features(frame, _target(), "2026-09-19T13:00:00Z", windows=(5,))

    assert math.isnan(features["home_xg_for_overall_r5"])
    assert math.isnan(features["away_rest_days"])


def test_cutoff_after_kickoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="cutoff cannot be later"):
        build_asof_features(pd.DataFrame(columns=list(_row(
            "m1", "2026-09-01T18:00:00Z", "2026-09-01T20:00:00Z", "A", "B", 1, 0
        ))), _target(), "2026-09-20T18:00:01Z")
