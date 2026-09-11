from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

CF_NAME_MAP = {
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Nottm Forest": "Nottingham Forest",
    "QPR": "Queens Park Rangers",
    "West Brom": "West Bromwich Albion",
    "Wolves": "Wolverhampton Wanderers",
}

FOOTIQO_NAME_MAP = {
    "Manchester Utd": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nottingham": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
}


def load_match_frame(
    matches_csv: str | Path,
    understat_csv: str | Path,
    footiqo_corners_xlsx: str | Path,
    big_five_xlsx: str | Path,
) -> pd.DataFrame:
    """Build a canonical EPL match table from historical + 2025/26 sources.

    No feature is computed here. This stage only reconciles providers and raw
    match-level fields. The 2025/26 Footiqo closing prices are retained strictly
    for evaluation and never used as model inputs.
    """
    matches = pd.read_csv(matches_csv, low_memory=False)
    matches["MatchDate"] = pd.to_datetime(matches["MatchDate"])
    e0 = matches[(matches["Division"] == "E0") & (matches["MatchDate"] >= "2014-08-01")].copy()
    e0["home_team"] = e0["HomeTeam"].replace(CF_NAME_MAP)
    e0["away_team"] = e0["AwayTeam"].replace(CF_NAME_MAP)

    under = pd.read_csv(understat_csv)
    under["date_dt"] = pd.to_datetime(under["date"])
    under["home_canon"] = under["home_team"]
    under["away_canon"] = under["away_team"]

    cf = e0[
        [
            "MatchDate", "home_team", "away_team", "HomeCorners", "AwayCorners",
            "HomeShots", "AwayShots", "HomeTarget", "AwayTarget", "FTHome", "FTAway",
        ]
    ].copy()
    cf = cf.rename(columns={"home_team": "home_canon", "away_team": "away_canon"}).sort_values("MatchDate")

    under_small = under[
        [
            "date_dt", "home_canon", "away_canon", "home_xg", "away_xg",
            "home_ppda", "away_ppda", "home_deep_completions", "away_deep_completions",
        ]
    ].sort_values("date_dt")

    hist = pd.merge_asof(
        cf,
        under_small,
        left_on="MatchDate",
        right_on="date_dt",
        by=["home_canon", "away_canon"],
        direction="nearest",
        tolerance=pd.Timedelta("1D"),
    )

    historical = pd.DataFrame(
        {
            "date": hist["MatchDate"],
            "home_team": hist["home_canon"],
            "away_team": hist["away_canon"],
            "home_corners": hist["HomeCorners"],
            "away_corners": hist["AwayCorners"],
            "home_shots": hist["HomeShots"],
            "away_shots": hist["AwayShots"],
            "home_sot": hist["HomeTarget"],
            "away_sot": hist["AwayTarget"],
            "home_goals": hist["FTHome"],
            "away_goals": hist["FTAway"],
            "home_xg": hist["home_xg"],
            "away_xg": hist["away_xg"],
            "home_ppda": hist["home_ppda"],
            "away_ppda": hist["away_ppda"],
            "home_deep": hist["home_deep_completions"],
            "away_deep": hist["away_deep_completions"],
            "source": "historical",
        }
    )

    results = pd.read_excel(big_five_xlsx, sheet_name="Matches_Results")
    stats = pd.read_excel(big_five_xlsx, sheet_name="Statistics_FT")
    results = results[results["league"] == "Premier League"].copy()
    stats = stats[stats["league"] == "Premier League"].copy()
    current = results.merge(
        stats.drop(
            columns=[
                "start_datetime", "country", "competition_type", "league", "season",
                "home_team", "away_team", "referee",
            ]
        ),
        on="match_id",
        how="left",
    )

    corner_odds = pd.read_excel(footiqo_corners_xlsx, sheet_name="Corners_Closing_Odds")
    odds_cols = [c for c in corner_odds.columns if "corners_ft_closing_odds" in c]
    current = current.merge(corner_odds[["match_id"] + odds_cols], on="match_id", how="left")
    current["home_canon"] = current["home_team"].replace(FOOTIQO_NAME_MAP)
    current["away_canon"] = current["away_team"].replace(FOOTIQO_NAME_MAP)
    current["date_dt"] = pd.to_datetime(current["start_datetime"])

    under_2526 = under[under["season"] == 2526][
        [
            "date_dt", "home_canon", "away_canon", "home_xg", "away_xg",
            "home_ppda", "away_ppda", "home_deep_completions", "away_deep_completions",
        ]
    ].sort_values("date_dt")

    current = pd.merge_asof(
        current.sort_values("date_dt"),
        under_2526,
        on="date_dt",
        by=["home_canon", "away_canon"],
        direction="nearest",
        tolerance=pd.Timedelta("1D"),
    )

    holdout = pd.DataFrame(
        {
            "date": current["date_dt"],
            "match_id": current["match_id"],
            "home_team": current["home_canon"],
            "away_team": current["away_canon"],
            "home_corners": current["home_corners_ft"],
            "away_corners": current["away_corners_ft"],
            "home_shots": current["home_total_shots_ft"],
            "away_shots": current["away_total_shots_ft"],
            "home_sot": current["home_shots_on_target_ft"],
            "away_sot": current["away_shots_on_target_ft"],
            "home_goals": current["home_goals_ft"],
            "away_goals": current["away_goals_ft"],
            "home_xg": current["home_xg"],
            "away_xg": current["away_xg"],
            "home_ppda": current["home_ppda"],
            "away_ppda": current["away_ppda"],
            "home_deep": current["home_deep_completions"],
            "away_deep": current["away_deep_completions"],
            "source": "holdout",
        }
    )
    for col in odds_cols:
        holdout[col] = current[col].to_numpy()

    combined = pd.concat([historical, holdout], ignore_index=True, sort=False)
    combined = combined.sort_values("date").reset_index(drop=True)
    combined["internal_match_id"] = np.arange(len(combined))
    return combined
