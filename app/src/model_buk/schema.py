from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

RAW_REQUIRED_COLUMNS = {
    "date",
    "home_team",
    "away_team",
    "home_corners",
    "away_corners",
    "home_shots",
    "away_shots",
    "home_sot",
    "away_sot",
    "home_goals",
    "away_goals",
    "home_xg",
    "away_xg",
    "home_ppda",
    "away_ppda",
    "home_deep",
    "away_deep",
    "source",
    "internal_match_id",
}


def require_columns(frame: pd.DataFrame, required: Iterable[str], *, label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def validate_raw_history(frame: pd.DataFrame) -> None:
    require_columns(frame, RAW_REQUIRED_COLUMNS, label="raw history")
    if frame.empty:
        raise ValueError("raw history is empty")
    if frame["internal_match_id"].duplicated().any():
        raise ValueError("raw history contains duplicate internal_match_id values")
    dates = pd.to_datetime(frame["date"], errors="coerce", format="mixed")
    if dates.isna().any():
        raise ValueError("raw history contains invalid dates")
    if (frame["home_team"].isna() | frame["away_team"].isna()).any():
        raise ValueError("raw history contains missing team names")
    if (frame["home_team"] == frame["away_team"]).any():
        raise ValueError("raw history contains a match where home_team == away_team")
