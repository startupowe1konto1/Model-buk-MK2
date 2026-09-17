from __future__ import annotations

import pandas as pd

from model_buk.team_names import ALIASES, normalize_name


UNDERSTAT_TO_FOOTBALL_DATA_LEAGUE = {
    "ENG-Premier League": "E0",
    "ESP-La Liga": "SP1",
    "FRA-Ligue 1": "F1",
    "GER-Bundesliga": "D1",
    "ITA-Serie A": "I1",
}

_NORMALIZED_TEAM_ALIASES = {
    normalize_name(alias): normalize_name(canonical)
    for alias, canonical in ALIASES.items()
}


def _team_key(value: object) -> str:
    normalized = normalize_name(str(value))
    return _NORMALIZED_TEAM_ALIASES.get(normalized, normalized)


def enrich_with_understat(
    matches: pd.DataFrame,
    understat_stats: pd.DataFrame,
) -> pd.DataFrame:
    """Attach Understat features using conservative exact canonical match keys.

    The cross-provider link requires the same league, calendar date, canonical
    home team and canonical away team.  Fuzzy matching is deliberately excluded
    from automatic ingestion; unresolved rows remain null rather than risking a
    silent match to the wrong fixture.
    """
    match_required = {"source_row_id", "league", "kickoff", "home_team", "away_team"}
    stats_required = {
        "game_id",
        "league",
        "kickoff",
        "home_team_name",
        "away_team_name",
        "home_xg",
        "away_xg",
    }
    match_missing = sorted(match_required - set(matches.columns))
    stats_missing = sorted(stats_required - set(understat_stats.columns))
    if match_missing:
        raise ValueError(f"matches is missing enrichment columns: {', '.join(match_missing)}")
    if stats_missing:
        raise ValueError(f"understat_stats is missing enrichment columns: {', '.join(stats_missing)}")

    left = matches.copy()
    right = understat_stats.copy()
    left["_match_date"] = pd.to_datetime(left["kickoff"], errors="coerce", format="mixed").dt.date
    right["_match_date"] = pd.to_datetime(
        right["kickoff"], errors="coerce", format="mixed", utc=True
    ).dt.date
    left["_home_key"] = left["home_team"].map(_team_key)
    left["_away_key"] = left["away_team"].map(_team_key)
    right["_home_key"] = right["home_team_name"].map(_team_key)
    right["_away_key"] = right["away_team_name"].map(_team_key)
    right["_league_key"] = right["league"].map(UNDERSTAT_TO_FOOTBALL_DATA_LEAGUE)

    key = ["_league_key", "_match_date", "_home_key", "_away_key"]
    right = right.dropna(subset=key)
    if right.duplicated(key, keep=False).any():
        raise ValueError("Understat enrichment contains duplicate canonical match keys")

    feature_columns = [
        "game_id",
        "home_xg",
        "away_xg",
        "home_npxg",
        "away_npxg",
        "home_ppda",
        "away_ppda",
        "home_deep_completions",
        "away_deep_completions",
    ]
    available_features = [column for column in feature_columns if column in right.columns]
    lookup = right[key + available_features].rename(columns={"game_id": "understat_game_id"})
    left["_league_key"] = left["league"].astype("string")
    enriched = left.merge(lookup, on=key, how="left", validate="many_to_one")
    return enriched.drop(columns=key)
