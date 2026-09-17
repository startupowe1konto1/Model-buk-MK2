"""Normalize football-data.co.uk CSV variants without leaking closing prices.

Match facts and prices are deliberately returned as separate tables.  This
keeps closing prices available for evaluation while making it difficult to
accidentally include them in a model feature matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import IO, Any
from zipfile import ZipFile

import pandas as pd

SOURCE = "football-data.co.uk"

MATCH_COLUMNS = (
    "source_row_id",
    "source",
    "league",
    "kickoff",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
    "home_shots",
    "away_shots",
    "home_sot",
    "away_sot",
    "home_corners",
    "away_corners",
    "home_yellow_cards",
    "away_yellow_cards",
    "home_red_cards",
    "away_red_cards",
)

ODDS_COLUMNS = (
    "source_row_id",
    "provider",
    "odds_kind",
    "market",
    "selection",
    "line",
    "odds",
)

_MATCH_ALIASES = {
    "league": ("Div",),
    "home_team": ("HomeTeam",),
    "away_team": ("AwayTeam",),
    "home_goals": ("FTHG",),
    "away_goals": ("FTAG",),
    "home_shots": ("HS",),
    "away_shots": ("AS",),
    "home_sot": ("HST",),
    "away_sot": ("AST",),
    "home_corners": ("HC",),
    "away_corners": ("AC",),
    "home_yellow_cards": ("HY",),
    "away_yellow_cards": ("AY",),
    "home_red_cards": ("HR",),
    "away_red_cards": ("AR",),
}

_INTEGER_COLUMNS = tuple(
    column
    for column in MATCH_COLUMNS
    if column.startswith(("home_", "away_"))
    and column not in {"home_team", "away_team"}
)

_PROVIDERS = {
    "B365": "bet365",
    "BW": "bwin",
    "BF": "betfair_sportsbook",
    "BFE": "betfair_exchange",
    "PS": "pinnacle",
    "P": "pinnacle",
    "WH": "william_hill",
    "1XB": "1xbet",
    "LB": "ladbrokes",
    "IW": "interwetten",
    "VC": "betvictor",
    "SB": "sportingbet",
    "GB": "gamebookers",
    "SJ": "stan_james",
    "Max": "market_max",
    "Avg": "market_average",
}


@dataclass(frozen=True)
class FootballDataUKBatch:
    """Canonical match facts plus prices kept outside the feature table."""

    matches: pd.DataFrame
    odds: pd.DataFrame

    @property
    def opening_odds(self) -> pd.DataFrame:
        """Prices eligible for pre-match features, if their availability allows it."""

        return self.odds.loc[self.odds["odds_kind"].eq("opening")].copy()

    @property
    def closing_odds(self) -> pd.DataFrame:
        """Evaluation-only prices; these must not be used as model features."""

        return self.odds.loc[self.odds["odds_kind"].eq("closing")].copy()


def _first_present(frame: pd.DataFrame, aliases: tuple[str, ...]) -> pd.Series:
    for alias in aliases:
        if alias in frame.columns:
            return frame[alias]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


def _parse_kickoff(frame: pd.DataFrame) -> pd.Series:
    date_value = _first_present(frame, ("Date",))
    time_value = _first_present(frame, ("Time",))
    has_time = time_value.notna() & time_value.astype("string").str.strip().ne("")
    combined = date_value.astype("string").str.strip()
    combined = combined.where(~has_time, combined + " " + time_value.astype("string").str.strip())
    return pd.to_datetime(combined, errors="coerce", dayfirst=True, format="mixed")


def _empty_odds() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_row_id": pd.Series(dtype="string"),
            "provider": pd.Series(dtype="string"),
            "odds_kind": pd.Series(dtype="string"),
            "market": pd.Series(dtype="string"),
            "selection": pd.Series(dtype="string"),
            "line": pd.Series(dtype="Float64"),
            "odds": pd.Series(dtype="Float64"),
        }
    )


def _append_price(
    rows: list[pd.DataFrame],
    frame: pd.DataFrame,
    source_ids: pd.Series,
    *,
    column: str,
    provider: str,
    odds_kind: str,
    market: str,
    selection: str,
    line: float | pd.Series | None = None,
) -> None:
    if column not in frame.columns:
        return
    prices = pd.to_numeric(frame[column], errors="coerce")
    valid = prices.gt(1.0)
    if not valid.any():
        return
    if isinstance(line, pd.Series):
        lines = pd.to_numeric(line, errors="coerce").astype("Float64")
    else:
        lines = pd.Series(line, index=frame.index, dtype="Float64")
    rows.append(
        pd.DataFrame(
            {
                "source_row_id": source_ids.loc[valid].astype("string"),
                "provider": provider,
                "odds_kind": odds_kind,
                "market": market,
                "selection": selection,
                "line": lines.loc[valid],
                "odds": prices.loc[valid].astype("Float64"),
            }
        )
    )


def _normalize_odds(frame: pd.DataFrame, source_ids: pd.Series) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for code, provider in _PROVIDERS.items():
        for odds_kind, close_marker in (("opening", ""), ("closing", "C")):
            for suffix, selection in (("H", "home"), ("D", "draw"), ("A", "away")):
                _append_price(
                    rows,
                    frame,
                    source_ids,
                    column=f"{code}{close_marker}{suffix}",
                    provider=provider,
                    odds_kind=odds_kind,
                    market="1x2",
                    selection=selection,
                )

            total_code = "P" if code == "PS" else code
            for suffix, selection in ((">2.5", "over"), ("<2.5", "under")):
                _append_price(
                    rows,
                    frame,
                    source_ids,
                    column=f"{total_code}{close_marker}{suffix}",
                    provider=provider,
                    odds_kind=odds_kind,
                    market="total_goals",
                    selection=selection,
                    line=2.5,
                )

            handicap_line_name = "AHCh" if odds_kind == "closing" else "AHh"
            handicap_line = (
                frame[handicap_line_name]
                if handicap_line_name in frame.columns
                else pd.Series(pd.NA, index=frame.index)
            )
            for suffix, selection in (("AHH", "home"), ("AHA", "away")):
                _append_price(
                    rows,
                    frame,
                    source_ids,
                    column=f"{code}{close_marker}{suffix}",
                    provider=provider,
                    odds_kind=odds_kind,
                    market="asian_handicap",
                    selection=selection,
                    line=handicap_line,
                )

    if not rows:
        return _empty_odds()
    result = pd.concat(rows, ignore_index=True).loc[:, ODDS_COLUMNS]
    # Some seasons use ``P`` while others use ``PS`` for Pinnacle.  Both aliases
    # are scanned deliberately, so remove an exact duplicate if a header variant
    # makes the same quote reachable through both patterns.
    result = result.drop_duplicates(ignore_index=True)
    return result.sort_values(
        ["source_row_id", "market", "odds_kind", "provider", "selection"],
        ignore_index=True,
    )


def normalize_football_data_uk(
    frame: pd.DataFrame,
    *,
    source_name: str = "frame",
) -> FootballDataUKBatch:
    """Convert any supported football-data.co.uk header variant to a canonical batch.

    Missing optional facts and absent bookmaker columns become nullable values;
    they do not make ingestion fail.  Closing prices live only in ``batch.odds``
    and ``batch.closing_odds``, never in ``batch.matches``.
    """

    normalized_source_name = str(source_name).strip()
    if not normalized_source_name:
        raise ValueError("source_name must not be blank")
    source_positions = pd.Series(range(len(frame)), index=frame.index, dtype="int64")
    identity_present = (
        _first_present(frame, ("Date",)).notna()
        | _first_present(frame, _MATCH_ALIASES["home_team"]).notna()
        | _first_present(frame, _MATCH_ALIASES["away_team"]).notna()
    )
    # Historical provider files occasionally end with completely empty rows.
    # They are not matches and would otherwise create duplicate null identities.
    frame = frame.loc[identity_present].copy()
    source_positions = source_positions.loc[identity_present]
    source_ids = pd.Series(
        [
            f"{SOURCE}:{normalized_source_name}:{position}"
            for position in source_positions.to_numpy()
        ],
        index=frame.index,
        dtype="string",
    )
    matches = pd.DataFrame(index=frame.index)
    matches["source_row_id"] = source_ids
    matches["source"] = SOURCE
    matches["league"] = _first_present(frame, _MATCH_ALIASES["league"]).astype("string")
    matches["kickoff"] = _parse_kickoff(frame)
    for target, aliases in _MATCH_ALIASES.items():
        if target == "league":
            continue
        matches[target] = _first_present(frame, aliases)

    for column in ("home_team", "away_team"):
        matches[column] = matches[column].astype("string").str.strip()
    for column in _INTEGER_COLUMNS:
        matches[column] = pd.to_numeric(matches[column], errors="coerce").astype("Int64")

    matches = matches.loc[:, MATCH_COLUMNS].reset_index(drop=True)
    odds = _normalize_odds(frame, source_ids)
    return FootballDataUKBatch(matches=matches, odds=odds)


def read_football_data_uk_csv(
    path_or_buffer: str | Path | IO[str] | IO[bytes],
    **read_csv_kwargs: Any,
) -> FootballDataUKBatch:
    """Read one provider CSV and normalize it without retaining the raw frame."""

    source_name = Path(path_or_buffer).name if isinstance(path_or_buffer, (str, Path)) else "buffer"
    return normalize_football_data_uk(
        pd.read_csv(path_or_buffer, **read_csv_kwargs),
        source_name=source_name,
    )


def load_football_data_uk_archives(
    archives: list[str | Path] | tuple[str | Path, ...],
    *,
    member_prefix: str = "football_data/football-data-uk/main/",
) -> FootballDataUKBatch:
    """Stream provider CSV files directly from one or more ZIP parts.

    Nothing is extracted to the repository.  Each archive/member pair becomes
    part of ``source_row_id``, so season files can be concatenated safely even
    though the upstream data has no stable match identifier.
    """
    match_frames: list[pd.DataFrame] = []
    odds_frames: list[pd.DataFrame] = []
    for archive_path_value in archives:
        archive_path = Path(archive_path_value)
        with ZipFile(archive_path) as archive:
            members = sorted(
                name
                for name in archive.namelist()
                if name.startswith(member_prefix) and name.lower().endswith(".csv")
            )
            for member in members:
                raw = archive.read(member)
                try:
                    provider_frame = pd.read_csv(BytesIO(raw), low_memory=False)
                except UnicodeDecodeError:
                    provider_frame = pd.read_csv(
                        BytesIO(raw),
                        low_memory=False,
                        encoding="latin-1",
                    )
                batch = normalize_football_data_uk(
                    provider_frame,
                    source_name=f"{archive_path.name}::{member}",
                )
                match_frames.append(batch.matches)
                if not batch.odds.empty:
                    odds_frames.append(batch.odds)

    matches = (
        pd.concat(match_frames, ignore_index=True)
        if match_frames
        else pd.DataFrame(columns=MATCH_COLUMNS)
    )
    odds = (
        pd.concat(odds_frames, ignore_index=True)
        if odds_frames
        else _empty_odds()
    )
    if not matches.empty and matches["source_row_id"].duplicated().any():
        raise ValueError("archive ingestion produced duplicate source_row_id values")
    return FootballDataUKBatch(matches=matches, odds=odds)
