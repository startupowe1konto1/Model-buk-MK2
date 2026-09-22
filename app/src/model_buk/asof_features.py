from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

import numpy as np
import pandas as pd

from .team_names import ALIASES, normalize_name


TimestampLike: TypeAlias = str | datetime | pd.Timestamp
FeatureValue: TypeAlias = str | int | float | pd.Timestamp

_IDENTITY_COLUMNS = {"date", "available_at", "home_team", "away_team", "internal_match_id"}
_PAIRED_METRICS = ("corners", "shots", "sot", "goals", "xg", "deep")
_SINGLE_METRICS = ("ppda",)
_SCOPES = ("overall", "home", "away")


def _aware_utc_timestamp(value: TimestampLike, *, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid timestamp") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{field} must be a valid timestamp")
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.tz_convert("UTC")


def _team_key(value: object) -> str:
    raw = str(value).strip()
    alias = ALIASES.get(normalize_name(raw), raw)
    return normalize_name(alias)


@dataclass(frozen=True, slots=True)
class TargetFixture:
    """A fixture for which features are calculated at a decision cutoff."""

    fixture_id: str
    kickoff: TimestampLike
    home_team: str
    away_team: str

    def __post_init__(self) -> None:
        if not str(self.fixture_id).strip():
            raise ValueError("fixture_id must not be blank")
        if not str(self.home_team).strip() or not str(self.away_team).strip():
            raise ValueError("target team names must not be blank")
        if _team_key(self.home_team) == _team_key(self.away_team):
            raise ValueError("target home_team and away_team must be different")
        object.__setattr__(self, "kickoff", _aware_utc_timestamp(self.kickoff, field="kickoff"))


def _validate_windows(windows: tuple[int, ...]) -> tuple[int, ...]:
    if not windows or any(not isinstance(window, int) or window <= 0 for window in windows):
        raise ValueError("windows must contain positive integers")
    return tuple(dict.fromkeys(windows))


def _prepare_as_of_history(
    records: pd.DataFrame,
    *,
    target: TargetFixture,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    missing = sorted(_IDENTITY_COLUMNS - set(records.columns))
    if missing:
        raise ValueError(f"history is missing required columns: {', '.join(missing)}")

    data = records.copy()
    data["_event_time"] = pd.to_datetime(data["date"], errors="coerce", utc=True, format="mixed")
    data["_available_at"] = pd.to_datetime(
        data["available_at"], errors="coerce", utc=True, format="mixed"
    )
    if data["_event_time"].isna().any():
        raise ValueError("history contains invalid date values")
    if data["_available_at"].isna().any():
        raise ValueError("history contains invalid available_at values")
    if data["internal_match_id"].isna().any():
        raise ValueError("history contains missing internal_match_id values")
    if data["home_team"].isna().any() or data["away_team"].isna().any():
        raise ValueError("history contains missing team names")

    # An outcome must both have happened and have been published by the decision
    # time. Excluding the target ID is an additional guard against malformed
    # feeds that expose a target row with an incorrect event timestamp.
    usable = data[
        (data["_available_at"] <= cutoff)
        & (data["_event_time"] < cutoff)
        & data["internal_match_id"].astype(str).ne(str(target.fixture_id))
    ].copy()
    if usable.empty:
        return usable

    # A feed can contain corrections. Select the newest version known at cutoff,
    # then impose a stable chronological order independent of input row order.
    usable["_id_sort"] = usable["internal_match_id"].astype(str)
    tie_breakers: list[str] = []
    for column in ("ingested_at", "provider", "provider_version", "source_record_id"):
        if column in usable.columns:
            normalized = f"_sort_{column}"
            if column == "ingested_at":
                usable[normalized] = pd.to_datetime(
                    usable[column], errors="coerce", utc=True, format="mixed"
                )
            else:
                usable[normalized] = usable[column].fillna("").astype(str)
            tie_breakers.append(normalized)
    usable = usable.sort_values(
        ["_id_sort", "_available_at", *tie_breakers], kind="mergesort"
    ).drop_duplicates("_id_sort", keep="last")
    usable = usable.sort_values(["_event_time", "_id_sort"], kind="mergesort").reset_index(drop=True)
    return usable


def _long_history(history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for match_order, match in history.iterrows():
        for side, opponent_side in (("home", "away"), ("away", "home")):
            row: dict[str, object] = {
                "_match_order": match_order,
                "date": match["_event_time"],
                "team": str(match[f"{side}_team"]).strip(),
                "team_key": _team_key(match[f"{side}_team"]),
                "opponent": str(match[f"{opponent_side}_team"]).strip(),
                "opponent_key": _team_key(match[f"{opponent_side}_team"]),
                "venue": side,
            }
            for metric in _PAIRED_METRICS:
                own_column = f"{side}_{metric}"
                opposing_column = f"{opponent_side}_{metric}"
                row[f"{metric}_for"] = pd.to_numeric(
                    pd.Series([match.get(own_column, np.nan)]), errors="coerce"
                ).iloc[0]
                row[f"{metric}_against"] = pd.to_numeric(
                    pd.Series([match.get(opposing_column, np.nan)]), errors="coerce"
                ).iloc[0]
            for metric in _SINGLE_METRICS:
                row[metric] = pd.to_numeric(
                    pd.Series([match.get(f"{side}_{metric}", np.nan)]), errors="coerce"
                ).iloc[0]
            goals_for = row["goals_for"]
            goals_against = row["goals_against"]
            if pd.isna(goals_for) or pd.isna(goals_against):
                row["points"] = np.nan
            elif float(goals_for) > float(goals_against):
                row["points"] = 3.0
            elif float(goals_for) == float(goals_against):
                row["points"] = 1.0
            else:
                row["points"] = 0.0
            rows.append(row)

    columns = ["_match_order", "date", "team", "team_key", "opponent", "opponent_key", "venue"]
    if not rows:
        return pd.DataFrame(columns=columns)
    long = pd.DataFrame(rows).sort_values(["_match_order", "venue"], kind="mergesort")

    # Normalize each performance by what that opponent had conceded before that
    # historical match. The shift makes this secondary feature chronological as
    # well as cutoff-safe.
    for metric in _PAIRED_METRICS:
        prior_column = f"_{metric}_against_prior"
        long[prior_column] = long.groupby("team_key", sort=False)[f"{metric}_against"].transform(
            lambda values: values.shift(1).expanding(min_periods=1).mean()
        )
        opponent_prior = {
            (row["_match_order"], row["team_key"]): row[prior_column]
            for _, row in long[["_match_order", "team_key", prior_column]].iterrows()
        }
        denominator = pd.Series(
            [
                opponent_prior.get((row["_match_order"], row["opponent_key"]), np.nan)
                for _, row in long[["_match_order", "opponent_key"]].iterrows()
            ],
            index=long.index,
            dtype=float,
        )
        denominator = denominator.where(denominator > 0)
        long[f"{metric}_for_opponent_adjusted"] = long[f"{metric}_for"] / denominator
    return long


def _mean(values: pd.Series) -> float:
    result = values.mean(skipna=True)
    return float(result) if pd.notna(result) else float("nan")


def build_asof_features(
    records: pd.DataFrame,
    target: TargetFixture,
    cutoff: TimestampLike,
    *,
    windows: tuple[int, ...] = (5, 10),
) -> dict[str, FeatureValue]:
    """Build one leakage-safe feature vector for ``target``.

    Only versions with ``available_at <= cutoff`` and matches with ``date <
    cutoff`` are eligible. Rolling values use the most recent N matches within
    each overall/home/away scope. Missing observations are ignored; when no
    observation exists, the feature is ``NaN`` rather than a fabricated zero.
    """

    cutoff_ts = _aware_utc_timestamp(cutoff, field="cutoff")
    if cutoff_ts > target.kickoff:
        raise ValueError("cutoff cannot be later than target kickoff")
    selected_windows = _validate_windows(windows)
    history = _prepare_as_of_history(records, target=target, cutoff=cutoff_ts)
    long = _long_history(history)

    features: dict[str, FeatureValue] = {
        "fixture_id": str(target.fixture_id),
        "kickoff": target.kickoff,
        "cutoff": cutoff_ts,
        "home_team": str(target.home_team).strip(),
        "away_team": str(target.away_team).strip(),
        "eligible_matches": int(len(history)),
    }
    rolling_columns = [
        *(f"{metric}_{direction}" for metric in _PAIRED_METRICS for direction in ("for", "against")),
        *(f"{metric}_for_opponent_adjusted" for metric in _PAIRED_METRICS),
        *_SINGLE_METRICS,
        "points",
    ]

    for target_side, target_team in (("home", target.home_team), ("away", target.away_team)):
        team_rows = long[long.get("team_key", pd.Series(dtype=str)).eq(_team_key(target_team))].copy()
        features[f"{target_side}_matches_prior"] = int(len(team_rows))
        features[f"{target_side}_home_matches_prior"] = int(team_rows["venue"].eq("home").sum()) if not team_rows.empty else 0
        features[f"{target_side}_away_matches_prior"] = int(team_rows["venue"].eq("away").sum()) if not team_rows.empty else 0
        if team_rows.empty:
            features[f"{target_side}_rest_days"] = float("nan")
        else:
            rest = (target.kickoff - team_rows["date"].max()).total_seconds() / 86_400.0
            features[f"{target_side}_rest_days"] = float(rest)

        for scope in _SCOPES:
            scoped = team_rows if scope == "overall" else team_rows[team_rows["venue"].eq(scope)]
            for window in selected_windows:
                recent = scoped.tail(window)
                for column in rolling_columns:
                    values = recent[column] if column in recent else pd.Series(dtype=float)
                    features[f"{target_side}_{column}_{scope}_r{window}"] = _mean(values)

    return features


__all__ = ["TargetFixture", "build_asof_features"]
