"""Understat ingestion with separate schedule and team-stat tables.

Understat exports are not perfectly stable (the same field has appeared as
``xG``, ``xg`` and ``expected_goals``).  This adapter deliberately keeps the
schedule and statistics separate: the only supported relationship is the
provider's game identifier.  ZIP members are opened in-place; they are never
extracted to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

import pandas as pd

SOURCE = "understat"

SCHEDULE_COLUMNS = (
    "source_row_id", "source", "game_id", "league", "season", "kickoff",
    "home_team_id", "home_team_name", "away_team_id", "away_team_name",
    "home_goals", "away_goals",
)
STATS_COLUMNS = (
    "source_row_id", "source", "game_id", "league", "season", "kickoff",
    "home_team_id", "home_team_name", "away_team_id", "away_team_name",
    "home_goals", "away_goals", "home_xg", "away_xg", "home_npxg",
    "away_npxg", "home_ppda", "away_ppda", "home_deep_completions",
    "away_deep_completions",
)

_ALIASES = {
    "game_id": ("game_id", "match_id", "id", "fixture_id"),
    "league": ("league", "competition", "league_name"),
    "season": ("season", "season_id", "year"),
    "kickoff": ("kickoff", "datetime", "date", "match_date"),
    "home_team_id": ("home_team_id", "home_id", "h_id"),
    "away_team_id": ("away_team_id", "away_id", "a_id"),
    "home_team_name": ("home_team_name", "home_team", "home", "h_team"),
    "away_team_name": ("away_team_name", "away_team", "away", "a_team"),
    "home_goals": ("home_goals", "home_score", "h_goals", "goals_h"),
    "away_goals": ("away_goals", "away_score", "a_goals", "goals_a"),
    "home_xg": ("home_xg", "home_expected_goals"),
    "away_xg": ("away_xg", "away_expected_goals"),
    "home_npxg": ("home_npxg", "home_np_xg", "home_non_penalty_xg"),
    "away_npxg": ("away_npxg", "away_np_xg", "away_non_penalty_xg"),
    "home_ppda": ("home_ppda",),
    "away_ppda": ("away_ppda",),
    "home_deep_completions": ("home_deep_completions", "home_deep"),
    "away_deep_completions": ("away_deep_completions", "away_deep"),
}

@dataclass(frozen=True)
class UnderstatBatch:
    schedule: pd.DataFrame
    stats: pd.DataFrame

def _first(frame: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series(pd.NA, index=frame.index, dtype="object")

def _source_ids(frame: pd.DataFrame, source_name: str) -> pd.Series:
    return pd.Series([f"{SOURCE}:{source_name}:{i}" for i in range(len(frame))], index=frame.index, dtype="string")

def _base(frame: pd.DataFrame, source_name: str) -> tuple[pd.DataFrame, pd.Series]:
    if not str(source_name).strip():
        raise ValueError("source_name must not be blank")
    return frame.loc[_first(frame, _ALIASES["game_id"]).notna()].copy(), _source_ids(frame, source_name)

def normalize_understat_schedule(frame: pd.DataFrame, *, source_name: str = "frame") -> pd.DataFrame:
    """Normalize one schedule export.  Missing optional fields remain nullable."""
    work, ids = _base(frame, source_name)
    ids = ids.loc[work.index]
    out = pd.DataFrame({"source_row_id": ids, "source": SOURCE}, index=work.index)
    for target in SCHEDULE_COLUMNS[2:]:
        out[target] = _first(work, _ALIASES[target])
    out["game_id"] = out["game_id"].astype("string").str.strip()
    for col in ("home_team_id", "away_team_id"):
        out[col] = out[col].astype("string").str.strip()
    for col in ("home_team_name", "away_team_name", "league", "season"):
        out[col] = out[col].astype("string").str.strip()
    out["kickoff"] = pd.to_datetime(out["kickoff"], errors="coerce", format="mixed", utc=True)
    for col in ("home_goals", "away_goals"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    return out.loc[:, SCHEDULE_COLUMNS].reset_index(drop=True)

def normalize_understat_stats(frame: pd.DataFrame, *, source_name: str = "frame") -> pd.DataFrame:
    """Normalize Understat's wide home/away match-stat export."""
    work, ids = _base(frame, source_name)
    ids = ids.loc[work.index]
    out = pd.DataFrame({"source_row_id": ids, "source": SOURCE}, index=work.index)
    for target in STATS_COLUMNS[2:]:
        out[target] = _first(work, _ALIASES[target])
    out["game_id"] = out["game_id"].astype("string").str.strip()
    for col in ("home_team_id", "away_team_id", "home_team_name", "away_team_name", "league", "season"):
        out[col] = out[col].astype("string").str.strip()
    out["kickoff"] = pd.to_datetime(out["kickoff"], errors="coerce", format="mixed", utc=True)
    for col in ("home_goals", "away_goals"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    for col in (
        "home_xg", "away_xg", "home_npxg", "away_npxg", "home_ppda",
        "away_ppda", "home_deep_completions", "away_deep_completions",
    ):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Float64")
    return out.loc[:, STATS_COLUMNS].reset_index(drop=True)

def read_understat_csv(path_or_buffer: Any, *, kind: str | None = None, **kwargs: Any) -> UnderstatBatch:
    """Read a standalone CSV, classifying it from its header when necessary."""
    name = Path(path_or_buffer).name if isinstance(path_or_buffer, (str, Path)) else "buffer"
    frame = pd.read_csv(path_or_buffer, **kwargs)
    selected = kind or _kind(frame.columns, name)
    if selected == "schedule":
        return UnderstatBatch(normalize_understat_schedule(frame, source_name=name), pd.DataFrame(columns=STATS_COLUMNS))
    if selected == "stats":
        return UnderstatBatch(pd.DataFrame(columns=SCHEDULE_COLUMNS), normalize_understat_stats(frame, source_name=name))
    raise ValueError("cannot classify Understat CSV as schedule or stats")

def _kind(columns: Iterable[str], name: str) -> str | None:
    cols = {str(c).strip().lower() for c in columns}
    has_game = bool(cols & {x.lower() for x in _ALIASES["game_id"]})
    if not has_game:
        return None
    lowered = name.lower()
    if "team_match_stats" in lowered or {"home_ppda", "away_ppda"}.issubset(cols):
        return "stats"
    if cols & {x.lower() for x in _ALIASES["home_team_name"] + _ALIASES["away_team_name"]}:
        return "schedule"
    return "stats" if "stat" in lowered else "schedule" if any(x in lowered for x in ("match", "fixture", "schedule")) else None

def load_understat_archives(
    archives: Iterable[str | Path],
    *,
    member_prefix: str = "football_data/understat/",
) -> UnderstatBatch:
    """Find and read Understat CSV members directly from split ZIP archives."""
    schedules: list[pd.DataFrame] = []
    stats: list[pd.DataFrame] = []
    for archive_value in archives:
        path = Path(archive_value)
        with ZipFile(path) as archive:
            for member in sorted(archive.namelist()):
                if member_prefix and not member.startswith(member_prefix):
                    continue
                if not member.lower().endswith(".csv"):
                    continue
                with archive.open(member) as stream:
                    try:
                        header = pd.read_csv(stream, nrows=0)
                    except (UnicodeDecodeError, pd.errors.ParserError):
                        with archive.open(member) as retry:
                            header = pd.read_csv(retry, nrows=0, encoding="latin-1")
                kind = _kind(header.columns, member)
                if kind is None:
                    continue
                with archive.open(member) as stream:
                    try:
                        raw = pd.read_csv(stream, low_memory=False)
                    except UnicodeDecodeError:
                        with archive.open(member) as retry:
                            raw = pd.read_csv(retry, low_memory=False, encoding="latin-1")
                source_name = f"{path.name}::{member}"
                (schedules if kind == "schedule" else stats).append(
                    normalize_understat_schedule(raw, source_name=source_name) if kind == "schedule" else normalize_understat_stats(raw, source_name=source_name)
                )
    schedule = pd.concat(schedules, ignore_index=True) if schedules else pd.DataFrame(columns=SCHEDULE_COLUMNS)
    stat = pd.concat(stats, ignore_index=True) if stats else pd.DataFrame(columns=STATS_COLUMNS)
    schedule = _deduplicate_games(schedule, label="schedule")
    stat = _deduplicate_games(stat, label="stats")
    return UnderstatBatch(schedule=schedule, stats=stat)


def _deduplicate_games(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    """Collapse identical split-file copies and reject conflicting game rows."""
    if frame.empty:
        return frame
    canonical = [column for column in frame.columns if column not in {"source_row_id"}]
    deduplicated = frame.drop_duplicates(subset=canonical, keep="first").copy()
    conflicts = deduplicated["game_id"].duplicated(keep=False)
    if conflicts.any():
        ids = sorted(deduplicated.loc[conflicts, "game_id"].astype(str).unique())
        preview = ", ".join(ids[:5])
        raise ValueError(f"Understat {label} contains conflicting rows for game_id: {preview}")
    return deduplicated.reset_index(drop=True)
