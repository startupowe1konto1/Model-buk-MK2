"""Join football-data.co.uk matches (with historical odds) to API-Football matches.

football-data.co.uk carries the bookmaker prices (Pinnacle, Bet365, market
average/maximum, over/under 2.5, Asian handicap, closing lines); API-Football
carries the match facts, lineups and statistics with stable fixture ids.  This
module ties the two together so a price row can be attached to a fixture id.

Design, in the same two layers as :mod:`model_buk.ingestion.api_football_tables`:

- Pure functions on DataFrames: :func:`join_matches` pairs rows by league, date
  (+/- one day) and team identity; :func:`seed_team_aliases` learns team-name
  aliases from co-occurrence.  Nothing there touches the filesystem, so every
  rule is pinned down by tiny synthetic inputs in ``tests/test_fd_af_join.py``.
- IO wrappers: :func:`read_football_data_archives` streams the provider CSVs
  out of the ZIP parts, :func:`load_api_matches` reads the analysis table
  read-only, :func:`write_join_database` builds the output SQLite atomically and
  :func:`render_report` writes the Polish unmatched report.

Rules that must not be relaxed (see ``docs/PLAN-SKANERA-VALUE-BETOW.md``):

- A pair needs the same league, a date within one day and the same *identity*
  of both teams.  Team identity comes from a per-league alias table (a human
  can edit it) or from an exact normalised-name match; it is never guessed by
  fuzzy similarity.  Fuzzy scores are only shown as suggestions in the report.
- The full-time score from both sources must agree.  A mismatch rejects the
  pair and is reported; it is never "fixed" silently.
- The join is strictly one-to-one: no API fixture and no football-data row is
  used twice.  Anything ambiguous stays unmatched, with a reason.
- A missing price or score stays missing (``NULL`` / ``NaN``), never zero.  A price
  cell that cannot be a decimal quote (``<= 1.0``; the provider writes ``0`` for "no
  quote") is missing too and becomes ``NULL``; the count goes into ``join_meta``.
- Closing prices are kept under their own names.  They are for closing-line
  value only and must not feed a model as features; the ``odds_columns``
  catalogue in the output database tags every column as opening or closing.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
from zipfile import ZipFile

import pandas as pd

from model_buk.ingestion.api_football_history import TOP20_LEAGUES

SCHEMA_VERSION = "fd-af-join-v1"
SOURCE = "football-data.co.uk"

DATE_TOLERANCE_DAYS = 1
SEED_MIN_SUPPORT = 2
SEED_MIN_AGREEMENT = 0.9
SEED_MAX_ROUNDS = 12
# Decimal odds are always above 1.0; the provider writes 0 (or a sub-1.0 typo) into a price cell when
# the quote is missing, so such a cell is set to NULL, as football_data_uk.py does when it reads prices.
MIN_VALID_PRICE = 1.0
TARGET_MATCH_RATE = 0.99
TOP20_SEASONS = range(2015, 2026)

MAIN_MEMBER_PREFIX = "football_data/football-data-uk/main/"
EXTRA_MEMBER_PREFIX = "football_data/football-data-uk/extra/"
DEFAULT_MEMBER_PREFIXES = (MAIN_MEMBER_PREFIX, EXTRA_MEMBER_PREFIX)


@dataclass(frozen=True)
class Division:
    """One football-data division (or per-country file) and its API-Football league."""

    code: str
    league_id: int
    label: str
    # Some "extra" country files mix several leagues; only this one is mapped.
    league_name: str | None = None


# football-data division / country-file code -> API-Football league id.  Divisions
# that are absent here (Conference, Scottish lower tiers, the Swiss Challenge
# League) have no league in the history archive and are
# reported as "no league mapping" instead of being guessed.
DIVISIONS: tuple[Division, ...] = (
    Division("E0", 39, "Premier League (Anglia)"),
    Division("E1", 40, "Championship (Anglia)"),
    Division("E2", 41, "League One (Anglia)"),
    Division("E3", 42, "League Two (Anglia)"),
    Division("SP1", 140, "La Liga (Hiszpania)"),
    Division("SP2", 141, "La Liga 2 (Hiszpania)"),
    Division("D1", 78, "Bundesliga (Niemcy)"),
    Division("D2", 79, "2. Bundesliga (Niemcy)"),
    Division("I1", 135, "Serie A (Włochy)"),
    Division("I2", 136, "Serie B (Włochy)"),
    Division("F1", 61, "Ligue 1 (Francja)"),
    Division("F2", 62, "Ligue 2 (Francja)"),
    Division("P1", 94, "Primeira Liga (Portugalia)"),
    Division("N1", 88, "Eredivisie (Holandia)"),
    Division("B1", 144, "Pro League (Belgia)"),
    Division("T1", 203, "Süper Lig (Turcja)"),
    Division("SC0", 179, "Premiership (Szkocja)"),
    Division("G1", 197, "Super League 1 (Grecja)"),
    Division("POL", 106, "Ekstraklasa (Polska)"),
    Division("AUT", 218, "Bundesliga (Austria)"),
    Division("SWZ", 207, "Super League (Szwajcaria)", league_name="Super League"),
    Division("DNK", 119, "Superliga (Dania)"),
    Division("SWE", 113, "Allsvenskan (Szwecja)"),
    Division("NOR", 103, "Eliteserien (Norwegia)"),
    Division("FIN", 244, "Veikkausliiga (Finlandia)"),
    Division("IRL", 357, "Premier Division (Irlandia)"),
    Division("ROU", 283, "Liga I (Rumunia)"),
    Division("RUS", 235, "Premier Liga (Rosja)"),
    # Third wave (non-European leagues archived from 2026-09-30 on). The ARG
    # file also carries the separate Copa de la Liga, which is not mapped.
    Division("ARG", 128, "Liga Profesional (Argentyna)", league_name="Liga Profesional"),
    Division("BRA", 71, "Serie A (Brazylia)"),
    Division("MEX", 262, "Liga MX (Meksyk)"),
    Division("USA", 253, "MLS (USA)"),
    Division("JPN", 98, "J1 League (Japonia)"),
    Division("CHN", 169, "Super League (Chiny)"),
)

DIVISION_BY_CODE: dict[str, Division] = {division.code: division for division in DIVISIONS}
LEAGUE_LABELS: dict[int, str] = {division.league_id: division.label for division in DIVISIONS}
TOP20_LEAGUE_IDS: frozenset[int] = frozenset(league.league_id for league in TOP20_LEAGUES)

# --- reasons -------------------------------------------------------------------------------

REASON_MATCHED = "matched"
REASON_NO_LEAGUE = "no_league_mapping"
REASON_SEASON_NOT_COVERED = "season_not_covered"
REASON_INVALID_ROW = "invalid_fd_row"
REASON_NO_FD_SCORE = "no_fd_score"
REASON_DUPLICATE = "duplicate_fd_row"
REASON_ALIAS_MISSING = "team_alias_missing"
REASON_NO_CANDIDATE = "no_candidate_on_date"
REASON_SWAPPED = "home_away_swapped"
REASON_API_NO_RESULT = "api_no_result"
REASON_SCORE_MISMATCH = "score_mismatch"
REASON_AMBIGUOUS = "ambiguous"

# These rows cannot be joined by construction and stay out of the match-rate
# denominator; everything else that is unmatched counts against the rate.
EXCLUDED_REASONS: tuple[str, ...] = (
    REASON_NO_LEAGUE,
    REASON_SEASON_NOT_COVERED,
    REASON_INVALID_ROW,
    REASON_NO_FD_SCORE,
    REASON_DUPLICATE,
)
FAILURE_REASONS: tuple[str, ...] = (
    REASON_ALIAS_MISSING,
    REASON_NO_CANDIDATE,
    REASON_SWAPPED,
    REASON_API_NO_RESULT,
    REASON_SCORE_MISMATCH,
    REASON_AMBIGUOUS,
)
ALL_REASONS: tuple[str, ...] = EXCLUDED_REASONS + FAILURE_REASONS

REASON_LABELS_PL: dict[str, str] = {
    REASON_MATCHED: "połączone",
    REASON_NO_LEAGUE: "brak mapowania ligi",
    REASON_SEASON_NOT_COVERED: "sezon poza zakresem API",
    REASON_INVALID_ROW: "wiersz FD bez daty lub drużyn",
    REASON_NO_FD_SCORE: "brak wyniku w FD",
    REASON_DUPLICATE: "duplikat wiersza FD",
    REASON_ALIAS_MISSING: "brak aliasu drużyny",
    REASON_NO_CANDIDATE: "brak kandydata w oknie dat",
    REASON_SWAPPED: "odwrócona para gospodarz/gość",
    REASON_API_NO_RESULT: "mecz API bez wyniku",
    REASON_SCORE_MISMATCH: "rozbieżny wynik",
    REASON_AMBIGUOUS: "niejednoznaczne",
}

REASON_HELP_PL: dict[str, str] = {
    REASON_NO_LEAGUE: "Dywizja football-data nie ma odpowiednika w archiwum API-Football "
    "(np. Conference, niższe ligi szkockie, ligi spoza Europy).",
    REASON_SEASON_NOT_COVERED: "Archiwum API-Football nie ma tego sezonu tej ligi "
    "(dla części lig zaczyna się od 2016 lub 2018).",
    REASON_INVALID_ROW: "Wiersz football-data bez daty, nazwy drużyny albo czytelnego sezonu.",
    REASON_NO_FD_SCORE: "football-data nie podaje wyniku (mecz nierozegrany lub nieuzupełniony).",
    REASON_DUPLICATE: "Ten sam mecz jest w football-data dwa razy; pierwszy wiersz jest połączony.",
    REASON_ALIAS_MISSING: "Nazwa z football-data nie ma aliasu ani dokładnego odpowiednika "
    "wśród drużyn tej ligi w API. Zwykle drużyna spoza ligi (baraże).",
    REASON_NO_CANDIDATE: "W API nie ma meczu tej pary w oknie ±1 dzień: mecz przełożony "
    "(football-data trzyma datę pierwotną, API nową) albo mecz w ogóle nie występuje w archiwum "
    "(baraże, dodatkowe kolejki).",
    REASON_SWAPPED: "W API jest ta para, ale z zamienionymi rolami gospodarz/gość.",
    REASON_API_NO_RESULT: "API ma mecz, ale bez wyniku (odwołany albo przełożony).",
    REASON_SCORE_MISMATCH: "Oba źródła mają ten sam mecz, ale różny wynik po 90 minutach. Najczęściej "
    "wynik przyznany przy zielonym stoliku (football-data) kontra wynik boiskowy (API) albo błąd "
    "jednego ze źródeł. Para jest odrzucona.",
    REASON_AMBIGUOUS: "Więcej niż jeden kandydat w API albo kilka różnych wierszy football-data "
    "zgłasza ten sam mecz.",
}

# Causes of individual shortfalls that were checked by hand against the API or the archive
# (2026-09-29); shown in the report only when the division and reason actually occur.
KNOWN_CAUSES_PL: tuple[tuple[str, str, str], ...] = (
    (
        "DNK",
        REASON_NO_CANDIDATE,
        "Głównie sezony 2016/2017 do 2018/2019: API-Football (liga 119) ma w rundzie spadkowej tylko "
        "kolejki 1–6 (sprawdzone zapytaniem o rundy ligi), a football-data zawiera dalsze kolejki tej rundy "
        "oraz baraże. Tych meczów nie ma w API, więc nie da się ich połączyć.",
    ),
    (
        "AUT",
        REASON_ALIAS_MISSING,
        "Baraż 2017/2018 z Wiener Neustadt (2. Liga): archiwum API nie ma tych dwóch meczów w lidze 218, "
        "a sama drużyna występuje w nim tylko w lidze 219.",
    ),
    (
        "SWZ",
        REASON_NO_CANDIDATE,
        "Baraże o utrzymanie z drużyną z Challenge League (2018/2019): tych meczów nie ma w archiwum API "
        "dla ligi 207.",
    ),
)

# --- column layouts ----------------------------------------------------------------------------

API_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "league_id",
    "league_code",
    "season",
    "kickoff_utc",
    "status_short",
    "home_team_id",
    "home_team",
    "away_team_id",
    "away_team",
    "home_goals",
    "away_goals",
    "et_home_goals",
    "et_away_goals",
)

FD_IDENTITY_COLUMNS: tuple[str, ...] = (
    "source_row_id",
    "fd_div",
    "fd_league_name",
    "fd_season",
    "fd_season_start",
    "fd_date",
    "fd_time",
    "fd_home_team",
    "fd_away_team",
    "fd_home_goals",
    "fd_away_goals",
    "fd_ht_home_goals",
    "fd_ht_away_goals",
)

MATCHED_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "fd_source_row_id",
    "fd_div",
    "fd_league_name",
    "fd_season",
    "fd_season_start",
    "league_id",
    "af_league_code",
    "af_season",
    "fd_date",
    "fd_time",
    "af_kickoff_utc",
    "date_diff_days",
    "fd_home_team",
    "fd_away_team",
    "af_home_team",
    "af_away_team",
    "af_home_team_id",
    "af_away_team_id",
    "home_match_method",
    "away_match_method",
    "fd_home_goals",
    "fd_away_goals",
    "af_home_goals",
    "af_away_goals",
    "score_basis",
    "fd_ht_home_goals",
    "fd_ht_away_goals",
)

UNMATCHED_COLUMNS: tuple[str, ...] = (
    "fd_source_row_id",
    "fd_div",
    "fd_league_name",
    "fd_season",
    "fd_season_start",
    "league_id",
    "fd_date",
    "fd_time",
    "fd_home_team",
    "fd_away_team",
    "fd_home_goals",
    "fd_away_goals",
    "reason",
    "in_scope",
    "detail",
    "missing_names",
)

API_UNPAIRED_COLUMNS: tuple[str, ...] = (
    "fixture_id",
    "league_id",
    "af_league_code",
    "season",
    "kickoff_utc",
    "status_short",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
)

_INTEGER_MATCHED = frozenset(
    {
        "fixture_id",
        "fd_season_start",
        "league_id",
        "af_season",
        "date_diff_days",
        "af_home_team_id",
        "af_away_team_id",
        "fd_home_goals",
        "fd_away_goals",
        "af_home_goals",
        "af_away_goals",
        "fd_ht_home_goals",
        "fd_ht_away_goals",
    }
)
_INTEGER_UNMATCHED = frozenset(
    {"fd_season_start", "league_id", "fd_home_goals", "fd_away_goals", "in_scope"}
)
_INTEGER_API_UNPAIRED = frozenset({"fixture_id", "league_id", "season", "home_goals", "away_goals"})

# Football-data columns that are not prices: match facts, headers of the "extra"
# layout and the provider's own bookkeeping.  Every other column is kept as a price.
_FACT_COLUMNS = frozenset(
    {
        "Div",
        "Date",
        "Time",
        "HomeTeam",
        "AwayTeam",
        "FTHG",
        "FTAG",
        "FTR",
        "HTHG",
        "HTAG",
        "HTR",
        "Referee",
        "Attendance",
        "HS",
        "AS",
        "HST",
        "AST",
        "HF",
        "AF",
        "HC",
        "AC",
        "HY",
        "AY",
        "HR",
        "AR",
        "HFKC",
        "AFKC",
        "Country",
        "League",
        "Season",
        "Home",
        "Away",
        "HG",
        "AG",
        "Res",
    }
)
_NON_PRICE_FRAME_COLUMNS = frozenset(FD_IDENTITY_COLUMNS) | {"league_id"}


# --- names -----------------------------------------------------------------------------------------

_LETTER_FOLDING = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ß": "ss",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "þ": "th",
        "ı": "i",
    }
)
# Tokens that only label the club form and differ between the two providers.
_CLUB_TOKENS = frozenset(
    {"fc", "cf", "sc", "afc", "ac", "as", "sv", "vfb", "ssc", "fk", "sk", "bk", "if", "ik", "cd", "ud", "rc", "sd", "us", "ca", "1"}
)


def normalize_team_name(value: object) -> str:
    """Fold a club name to a comparable key: ASCII, lower case, no club-form tokens."""

    text = unicodedata.normalize("NFKD", str(value).translate(_LETTER_FOLDING))
    text = text.encode("ascii", "ignore").decode("ascii").lower().replace("&", " and ")
    tokens = [token for token in re.split(r"[^a-z0-9]+", text) if token and token not in _CLUB_TOKENS]
    return " ".join(tokens)


def _alias_key(name: object) -> str:
    """Whitespace-collapsed original spelling; the alias table keys on this."""

    return " ".join(str(name).split())


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _fold(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


# --- price column catalogue -----------------------------------------------------------------------------

_BOOKMAKER_CODES: tuple[str, ...] = tuple(
    sorted(
        {
            "B365", "BW", "BF", "BFE", "BFD", "PS", "P", "WH", "1XB", "LB", "IW", "VC", "SB", "GB",
            "SJ", "Max", "Avg", "BMGM", "BV", "CL", "BbMx", "BbAv",
        },
        key=lambda code: (-len(code), code),
    )
)
_SELECTION_MARKETS: dict[str, str] = {
    "H": "1x2",
    "D": "1x2",
    "A": "1x2",
    ">2.5": "total_goals_2.5",
    "<2.5": "total_goals_2.5",
    "AHH": "asian_handicap",
    "AHA": "asian_handicap",
}


def classify_odds_column(name: str) -> tuple[str, str, str]:
    """Return ``(bookmaker code, market, phase)`` for a football-data price column.

    ``phase`` is ``closing`` for the ``...C...`` family and ``opening`` for the
    plain family, matching :mod:`model_buk.ingestion.football_data_uk`.  Counts
    of bookmakers (``Bb1X2``...) are ``meta``; unknown names are ``other``.
    """

    if name in ("AHh", "BbAHh"):
        return ("", "asian_handicap_line", "opening")
    if name == "AHCh":
        return ("", "asian_handicap_line", "closing")
    if name in ("Bb1X2", "BbOU", "BbAH"):
        return ("", "bookmaker_count", "meta")
    for code in _BOOKMAKER_CODES:
        if not name.startswith(code):
            continue
        rest = name[len(code):]
        phase = "opening"
        if rest.startswith("C") and rest[1:] in _SELECTION_MARKETS:
            phase, rest = "closing", rest[1:]
        if rest in _SELECTION_MARKETS:
            return (code, _SELECTION_MARKETS[rest], phase)
    return ("", "unknown", "other")


def _is_decimal_price_column(name: str) -> bool:
    """True for columns that hold decimal odds (not handicap lines, counts or unknown names)."""

    _, market, phase = classify_odds_column(name)
    return phase in ("opening", "closing") and market != "asian_handicap_line"


def _numeric_column(series: pd.Series) -> pd.Series:
    """Parse a provider column to float; stray whitespace (e.g. a trailing NBSP) is ignored."""

    if not pd.api.types.is_numeric_dtype(series):
        series = pd.Series(
            [value.strip() if isinstance(value, str) else value for value in series.tolist()],
            index=series.index,
            dtype="object",
        )
    return pd.to_numeric(series, errors="coerce").astype("float64")


def odds_columns_of(frame: pd.DataFrame) -> list[str]:
    """Price columns of a football-data frame: everything that is not identity data."""

    return [column for column in frame.columns if column not in _NON_PRICE_FRAME_COLUMNS]


# --- reading football-data -------------------------------------------------------------------------------


def _clean_text_column(series: pd.Series) -> list[str | None]:
    return [_clean_text(value) for value in series.tolist()]


def _parse_dates(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    four_digit_year = pd.to_datetime(text, format="%d/%m/%Y", errors="coerce")
    two_digit_year = pd.to_datetime(text, format="%d/%m/%y", errors="coerce")
    return four_digit_year.fillna(two_digit_year)


def _parse_extra_season(value: object) -> tuple[str | None, int | None]:
    text = _clean_text(value)
    if text is None:
        return None, None
    span = re.match(r"^(\d{4})\s*/\s*(\d{4})", text)
    if span:
        return f"{span.group(1)}/{span.group(2)}", int(span.group(1))
    single = re.match(r"^(\d{4})", text)
    if single:
        return single.group(1), int(single.group(1))
    return text, None


def _season_start_from_code(code: str) -> int:
    """``1516`` -> 2015 (the API-Football season is the year the season starts)."""

    if not re.fullmatch(r"\d{4}", code):
        raise ValueError(f"unrecognised football-data season code {code!r}")
    year = int(code[:2])
    return 2000 + year if year < 90 else 1900 + year


def normalize_fd_frame(
    frame: pd.DataFrame,
    *,
    source_name: str,
    div: str,
    season_start: int | None = None,
) -> pd.DataFrame:
    """Convert one provider CSV (main or "extra" layout) to identity columns plus prices.

    Rows without a date and without both team names are provider padding and are
    dropped; every kept row gets a ``source_row_id`` built like the existing
    :func:`~model_buk.ingestion.football_data_uk.load_football_data_uk_archives`
    (position in the original file), so ids stay comparable.
    """

    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    extra_layout = "Home" in frame.columns and "HomeTeam" not in frame.columns
    home_column, away_column = ("Home", "Away") if extra_layout else ("HomeTeam", "AwayTeam")
    home_goals_column, away_goals_column = ("HG", "AG") if extra_layout else ("FTHG", "FTAG")
    for required in ("Date", home_column, away_column):
        if required not in frame.columns:
            raise ValueError(f"{source_name}: missing football-data column {required!r}")

    def column(name: str) -> pd.Series:
        if name in frame.columns:
            return frame[name]
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")

    present = frame["Date"].notna() | frame[home_column].notna() | frame[away_column].notna()
    out = pd.DataFrame(index=frame.index)
    out["source_row_id"] = [f"{SOURCE}:{source_name}:{position}" for position in range(len(frame))]
    out["fd_div"] = div
    if extra_layout:
        out["fd_league_name"] = _clean_text_column(column("League"))
        seasons = [_parse_extra_season(value) for value in column("Season").tolist()]
        out["fd_season"] = [label for label, _ in seasons]
        out["fd_season_start"] = pd.array([start for _, start in seasons], dtype="Int64")
    else:
        out["fd_league_name"] = None
        label = None if season_start is None else f"{season_start}/{season_start + 1}"
        out["fd_season"] = label
        out["fd_season_start"] = pd.array([season_start] * len(frame), dtype="Int64")
    out["fd_date"] = _parse_dates(frame["Date"])
    out["fd_time"] = _clean_text_column(column("Time"))
    out["fd_home_team"] = _clean_text_column(frame[home_column])
    out["fd_away_team"] = _clean_text_column(frame[away_column])
    out["fd_home_goals"] = pd.to_numeric(column(home_goals_column), errors="coerce").astype("Int64")
    out["fd_away_goals"] = pd.to_numeric(column(away_goals_column), errors="coerce").astype("Int64")
    out["fd_ht_home_goals"] = pd.to_numeric(column("HTHG"), errors="coerce").astype("Int64")
    out["fd_ht_away_goals"] = pd.to_numeric(column("HTAG"), errors="coerce").astype("Int64")

    prices: dict[str, pd.Series] = {}
    unusable_prices = 0
    for name in frame.columns:
        if name in _FACT_COLUMNS or name.startswith("Unnamed"):
            continue
        values = _numeric_column(frame[name])
        if _is_decimal_price_column(name):
            unusable = values.le(MIN_VALID_PRICE)  # NaN compares False and stays missing
            unusable_prices += int(unusable.sum())
            values = values.mask(unusable)
        prices[name] = values
    if prices:
        out = pd.concat([out, pd.DataFrame(prices, index=frame.index)], axis=1)
    result = out.loc[present].reset_index(drop=True)
    result.attrs["unusable_prices_nulled"] = unusable_prices
    return result


def _read_csv_bytes(raw: bytes) -> pd.DataFrame:
    from io import BytesIO

    try:
        return pd.read_csv(BytesIO(raw), low_memory=False)
    except UnicodeDecodeError:
        return pd.read_csv(BytesIO(raw), low_memory=False, encoding="latin-1")


def read_football_data_archives(
    archives: Sequence[str | Path],
    *,
    prefixes: Sequence[str] = DEFAULT_MEMBER_PREFIXES,
) -> pd.DataFrame:
    """Stream every football-data CSV out of the ZIP parts into one wide frame.

    The frame starts with :data:`FD_IDENTITY_COLUMNS`; every price column found in
    any file follows under its original name (``NaN`` where a file lacks it).
    Decimal-odds cells that cannot be odds (``<= 1.0``, e.g. the provider's ``0``
    for "no quote") become ``NaN``; how many is in ``frame.attrs["unusable_prices_nulled"]``.
    Nothing is extracted to disk.  A member present in several parts is read once.
    """

    frames: list[pd.DataFrame] = []
    seen: set[str] = set()
    for archive_value in archives:
        archive_path = Path(archive_value)
        with ZipFile(archive_path) as archive:
            members = sorted(
                name
                for name in archive.namelist()
                if name.lower().endswith(".csv") and any(name.startswith(prefix) for prefix in prefixes)
            )
            for member in members:
                if member in seen:
                    continue
                seen.add(member)
                stem = Path(member).stem
                if member.startswith(MAIN_MEMBER_PREFIX):
                    div, _, season_code = stem.partition("_")
                    season_start: int | None = _season_start_from_code(season_code)
                else:
                    div, season_start = stem, None
                frames.append(
                    normalize_fd_frame(
                        _read_csv_bytes(archive.read(member)),
                        source_name=f"{archive_path.name}::{member}",
                        div=div,
                        season_start=season_start,
                    )
                )
    if not frames:
        empty = pd.DataFrame(columns=list(FD_IDENTITY_COLUMNS))
        empty.attrs["unusable_prices_nulled"] = 0
        return empty
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if combined["source_row_id"].duplicated().any():
        raise ValueError("football-data ingestion produced duplicate source_row_id values")
    prices = sorted(odds_columns_of(combined))
    result = combined.loc[:, list(FD_IDENTITY_COLUMNS) + prices]
    result.attrs["unusable_prices_nulled"] = sum(int(frame.attrs.get("unusable_prices_nulled", 0)) for frame in frames)
    return result


def _division_league(div: object, league_name: object) -> int | None:
    spec = DIVISION_BY_CODE.get(str(div))
    if spec is None:
        return None
    if spec.league_name is not None and _fold(league_name) != _fold(spec.league_name):
        return None
    return spec.league_id


def assign_league_ids(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with the API-Football ``league_id`` (``<NA>`` when unmapped)."""

    result = frame.copy()
    names = result["fd_league_name"].tolist() if "fd_league_name" in result.columns else [None] * len(result)
    ids = [_division_league(div, name) for div, name in zip(result["fd_div"].tolist(), names)]
    result["league_id"] = pd.array(ids, dtype="Int64")
    return result


# --- reading API-Football --------------------------------------------------------------------------------


def load_api_matches(db_path: str | Path, league_ids: Iterable[int] | None = None) -> pd.DataFrame:
    """Read the API-Football ``matches`` table read-only (the collector may rebuild it)."""

    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    query = f"SELECT {', '.join(API_COLUMNS)} FROM matches"
    params: list[int] = []
    if league_ids is not None:
        ids = sorted({int(league_id) for league_id in league_ids})
        query += f" WHERE league_id IN ({', '.join('?' for _ in ids)})"
        params = ids
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return pd.read_sql_query(query, conn, params=params)


def _api_day(value: object) -> date | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


_EXTRA_TIME_STATUSES = frozenset({"AET", "PEN"})


class _Fixture(NamedTuple):
    fixture_id: int
    day: date
    home_goals: int | None
    away_goals: int | None
    status: str | None
    kickoff: str | None
    season: int | None
    league_code: str | None
    home_id: int
    away_id: int
    home_name: str | None
    away_name: str | None
    regulation_home: int | None = None
    regulation_away: int | None = None


def _api_fixtures(api: pd.DataFrame) -> list[tuple[int, _Fixture]]:
    """``(league_id, fixture)`` for every API row that can take part in a join."""

    columns = {
        name: api[name].tolist() if name in api.columns else [None] * len(api) for name in API_COLUMNS
    }
    fixtures: list[tuple[int, _Fixture]] = []
    for position in range(len(api)):
        fixture_id = _int_or_none(columns["fixture_id"][position])
        league_id = _int_or_none(columns["league_id"][position])
        home_id = _int_or_none(columns["home_team_id"][position])
        away_id = _int_or_none(columns["away_team_id"][position])
        day = _api_day(columns["kickoff_utc"][position])
        if fixture_id is None or league_id is None or home_id is None or away_id is None or day is None:
            continue
        status = _clean_text(columns["status_short"][position])
        home_goals = _int_or_none(columns["home_goals"][position])
        away_goals = _int_or_none(columns["away_goals"][position])
        regulation_home = regulation_away = None
        if status in _EXTRA_TIME_STATUSES and home_goals is not None and away_goals is not None:
            # After extra time the provider's goals include the extra-time goals, while
            # football-data reports the 90-minute score; derive it when both parts are known.
            extra_home = _int_or_none(columns["et_home_goals"][position])
            extra_away = _int_or_none(columns["et_away_goals"][position])
            if extra_home is not None and extra_away is not None:
                if home_goals >= extra_home and away_goals >= extra_away:
                    regulation_home, regulation_away = home_goals - extra_home, away_goals - extra_away
        fixtures.append(
            (
                league_id,
                _Fixture(
                    fixture_id=fixture_id,
                    day=day,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    status=status,
                    kickoff=_clean_text(columns["kickoff_utc"][position]),
                    season=_int_or_none(columns["season"][position]),
                    league_code=_clean_text(columns["league_code"][position]),
                    home_id=home_id,
                    away_id=away_id,
                    home_name=_clean_text(columns["home_team"][position]),
                    away_name=_clean_text(columns["away_team"][position]),
                    regulation_home=regulation_home,
                    regulation_away=regulation_away,
                ),
            )
        )
    return fixtures


# --- alias table --------------------------------------------------------------------------------------------

ALIAS_COLUMNS: tuple[str, ...] = (
    "league_id",
    "fd_div",
    "fd_team",
    "af_team_id",
    "af_team",
    "source",
    "support",
    "note",
)
ALIAS_SOURCES = ("manual", "auto", "block")
_ALIAS_HEADER = (
    "# Team-name aliases: football-data.co.uk name -> API-Football team id, per league.",
    "# source=manual: decided by a human, never touched by the tooling and wins over everything.",
    "# source=block: never map this football-data name automatically (af_team_id stays empty).",
    "# source=auto: seeded from co-occurrence (same league, date and score, one side already known);",
    "#   regenerated on every run. To fix one, change its source to manual and correct af_team_id.",
    "# Names that already match exactly after normalisation are not listed here.",
)


@dataclass(frozen=True)
class AliasRow:
    league_id: int
    fd_div: str
    fd_team: str
    af_team_id: int | None
    af_team: str
    source: str
    support: int | None = None
    note: str = ""

    @property
    def key(self) -> tuple[int, str]:
        return (self.league_id, _alias_key(self.fd_team))


def load_aliases(path: str | Path) -> list[AliasRow]:
    """Read the alias CSV (``#`` comment lines allowed); a missing file means no aliases."""

    alias_path = Path(path)
    if not alias_path.exists():
        return []
    with alias_path.open(encoding="utf-8-sig", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    missing = [name for name in ALIAS_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{alias_path.name}: missing columns {missing}")
    rows: list[AliasRow] = []
    seen: dict[tuple[tuple[int, str], str], int] = {}
    for number, raw in enumerate(reader, start=2):
        where = f"{alias_path.name} row {number}"
        source = (raw["source"] or "").strip().lower()
        if source not in ALIAS_SOURCES:
            raise ValueError(f"{where}: source must be one of {ALIAS_SOURCES}, got {raw['source']!r}")
        try:
            league_id = int(str(raw["league_id"]).strip())
        except ValueError:
            raise ValueError(f"{where}: league_id must be an integer, got {raw['league_id']!r}") from None
        team_text = str(raw["af_team_id"] or "").strip()
        if source == "block":
            af_team_id = None
        else:
            try:
                af_team_id = int(team_text)
            except ValueError:
                raise ValueError(f"{where}: af_team_id must be an integer, got {raw['af_team_id']!r}") from None
        support_text = str(raw["support"] or "").strip()
        support = int(float(support_text)) if support_text else None
        fd_team = _alias_key(raw["fd_team"] or "")
        if not fd_team:
            raise ValueError(f"{where}: fd_team is empty")
        row = AliasRow(
            league_id=league_id,
            fd_div=(raw["fd_div"] or "").strip(),
            fd_team=fd_team,
            af_team_id=af_team_id,
            af_team=(raw["af_team"] or "").strip(),
            source=source,
            support=support,
            note=(raw["note"] or "").strip(),
        )
        if (row.key, source) in seen:
            raise ValueError(f"{where}: duplicate {source} alias for {row.fd_team!r} in league {league_id}")
        seen[(row.key, source)] = number
        rows.append(row)
    for (key, source), number in seen.items():
        if source == "manual" and (key, "block") in seen:
            raise ValueError(
                f"{alias_path.name}: conflicting manual and block rows for {key[1]!r} in league {key[0]}"
            )
    return sorted(rows, key=_alias_sort_key)


def _alias_sort_key(row: AliasRow) -> tuple[int, str, str]:
    return (row.league_id, row.fd_team.casefold(), row.source)


def save_aliases(path: str | Path, rows: Iterable[AliasRow]) -> None:
    """Write the alias CSV atomically, sorted, with an explanatory ``#`` header."""

    alias_path = Path(path)
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    temp = alias_path.with_name(alias_path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        for line in _ALIAS_HEADER:
            handle.write(line + "\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(ALIAS_COLUMNS)
        for row in sorted(rows, key=_alias_sort_key):
            writer.writerow(
                [
                    row.league_id,
                    row.fd_div,
                    row.fd_team,
                    "" if row.af_team_id is None else row.af_team_id,
                    row.af_team,
                    row.source,
                    "" if row.support is None else row.support,
                    row.note,
                ]
            )
    os.replace(temp, alias_path)


class TeamResolver:
    """Resolve a football-data team name inside one league to an API-Football team id.

    Precedence: manual alias, then block, then auto alias, then an *exact*
    normalised-name match (only when it points at a single API team).  Nothing
    fuzzy ever resolves a name.
    """

    _RANK = {"auto": 1, "block": 2, "manual": 3}

    def __init__(self, api: pd.DataFrame, aliases: Iterable[AliasRow]) -> None:
        self._latest_name: dict[int, dict[int, str]] = defaultdict(dict)
        self._exact: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        sides = []
        for prefix in ("home", "away"):
            side = api[["league_id", "kickoff_utc", f"{prefix}_team_id", f"{prefix}_team"]].copy()
            side.columns = pd.Index(["league_id", "kickoff_utc", "team_id", "team"])
            sides.append(side)
        teams = pd.concat(sides, ignore_index=True).dropna(subset=["league_id", "team_id", "team"])
        teams = teams.sort_values("kickoff_utc", kind="stable").drop_duplicates(
            ["league_id", "team_id", "team"], keep="last"
        )
        for league_id, team_id, team in zip(
            teams["league_id"].tolist(), teams["team_id"].tolist(), teams["team"].tolist()
        ):
            league, identifier = int(league_id), int(team_id)
            self._latest_name[league][identifier] = str(team)
            self._exact[league][normalize_team_name(team)].add(identifier)
        self._alias: dict[tuple[int, str], tuple[int | None, str]] = {}
        ranks: dict[tuple[int, str], int] = {}
        for alias in aliases:
            rank = self._RANK[alias.source]
            if rank >= ranks.get(alias.key, 0):
                ranks[alias.key] = rank
                self._alias[alias.key] = (alias.af_team_id, alias.source)

    def resolve(self, league_id: int, fd_team: str) -> tuple[int | None, str | None]:
        """Return ``(team id, method)``; ``(None, None)`` when the name cannot be resolved."""

        entry = self._alias.get((league_id, _alias_key(fd_team)))
        if entry is not None:
            team_id, method = entry
            return (None, None) if team_id is None else (team_id, method)
        key = normalize_team_name(fd_team)
        if not key:  # a name made only of club-form tokens says nothing about the club
            return (None, None)
        ids = self._exact.get(league_id, {}).get(key, set())
        if len(ids) == 1:
            return (next(iter(ids)), "exact")
        return (None, None)

    def is_blocked(self, league_id: int, fd_team: str) -> bool:
        entry = self._alias.get((league_id, _alias_key(fd_team)))
        return entry is not None and entry[0] is None

    def team_name(self, league_id: int, team_id: int) -> str:
        return self._latest_name.get(league_id, {}).get(team_id, "")

    def team_ids(self, league_id: int) -> set[int]:
        return set(self._latest_name.get(league_id, {}))

    def teams(self, league_id: int) -> dict[int, str]:
        return dict(self._latest_name.get(league_id, {}))

    def unknown_targets(self) -> list[tuple[int, str, int]]:
        """Alias rows whose team id never appears in that league's API matches."""

        return sorted(
            (league_id, name, team_id)
            for (league_id, name), (team_id, _) in self._alias.items()
            if team_id is not None and team_id not in self._latest_name.get(league_id, {})
        )


# --- shared preparation -----------------------------------------------------------------------------------------


def _prepare_fd(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy().reset_index(drop=True)
    for column in FD_IDENTITY_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = None
    prepared["fd_date"] = pd.to_datetime(prepared["fd_date"], errors="coerce")
    if "league_id" not in prepared.columns:
        prepared = assign_league_ids(prepared)
    return prepared


def _fd_days(frame: pd.DataFrame) -> list[date | None]:
    return [None if pd.isna(stamp) else stamp.date() for stamp in frame["fd_date"].tolist()]


# --- seeding aliases ---------------------------------------------------------------------------------------------


def seed_team_aliases(
    fd: pd.DataFrame,
    api: pd.DataFrame,
    fixed: Iterable[AliasRow] = (),
    *,
    min_support: int = SEED_MIN_SUPPORT,
    min_agreement: float = SEED_MIN_AGREEMENT,
    tolerance_days: int = DATE_TOLERANCE_DAYS,
) -> list[AliasRow]:
    """Learn ``auto`` aliases from co-occurrence; ambiguity never becomes an alias.

    Evidence: a football-data row (same league) where exactly one side already
    resolves to an API team, and the API has exactly one fixture of that team
    inside the date window with the same full-time score.  The API's other side
    is then a vote for the unresolved football-data name.  A name is accepted
    only with at least ``min_support`` votes of which ``min_agreement`` agree.
    Accepted names unlock further rows, so the process repeats until it settles.

    ``fixed`` are the human rows (``manual``/``block``); they are honoured and
    never returned.  Names that already match exactly are not aliased at all.
    """

    prepared = _prepare_fd(fd)
    resolver = TeamResolver(api, fixed)
    by_home: dict[tuple[int, int], list[tuple[date, int, int, int]]] = defaultdict(list)
    by_away: dict[tuple[int, int], list[tuple[date, int, int, int]]] = defaultdict(list)
    for league_id, fixture in _api_fixtures(api):
        if fixture.home_goals is None or fixture.away_goals is None:
            continue
        by_home[(league_id, fixture.home_id)].append((fixture.day, fixture.home_goals, fixture.away_goals, fixture.away_id))
        by_away[(league_id, fixture.away_id)].append((fixture.day, fixture.home_goals, fixture.away_goals, fixture.home_id))

    days = _fd_days(prepared)
    columns = {
        name: prepared[name].tolist()
        for name in ("league_id", "fd_div", "fd_home_team", "fd_away_team", "fd_home_goals", "fd_away_goals")
    }
    rows_by_league: dict[int, list[tuple[str, str, date, int, int]]] = defaultdict(list)
    div_by_league_name: dict[tuple[int, str], str] = {}
    for position in range(len(prepared)):
        league_id = _int_or_none(columns["league_id"][position])
        home = _clean_text(columns["fd_home_team"][position])
        away = _clean_text(columns["fd_away_team"][position])
        home_goals = _int_or_none(columns["fd_home_goals"][position])
        away_goals = _int_or_none(columns["fd_away_goals"][position])
        day = days[position]
        if None in (league_id, home, away, home_goals, away_goals, day):
            continue
        rows_by_league[league_id].append((_alias_key(home), _alias_key(away), day, home_goals, away_goals))  # type: ignore[arg-type]
        div = str(columns["fd_div"][position])
        div_by_league_name.setdefault((league_id, _alias_key(home)), div)  # type: ignore[arg-type]
        div_by_league_name.setdefault((league_id, _alias_key(away)), div)  # type: ignore[arg-type]

    seeded: list[AliasRow] = []
    for league_id in sorted(rows_by_league):
        rows = rows_by_league[league_id]
        names = {name for row in rows for name in row[:2]}
        known: dict[str, int] = {}
        blocked: set[str] = set()
        for name in names:
            if resolver.is_blocked(league_id, name):
                blocked.add(name)
                continue
            team_id, _ = resolver.resolve(league_id, name)
            if team_id is not None:
                known[name] = team_id
        accepted: dict[str, tuple[int, int, int]] = {}
        for _ in range(SEED_MAX_ROUNDS):
            votes: dict[str, Counter[int]] = defaultdict(Counter)
            for home, away, day, home_goals, away_goals in rows:
                home_id, away_id = known.get(home), known.get(away)
                if (home_id is None) == (away_id is None):
                    continue
                if home_id is not None:
                    pool, unknown = by_home.get((league_id, home_id), ()), away
                else:
                    pool, unknown = by_away.get((league_id, away_id), ()), home  # type: ignore[arg-type]
                if unknown in blocked:
                    continue
                candidates = [
                    other
                    for other_day, pool_home, pool_away, other in pool
                    if abs((other_day - day).days) <= tolerance_days
                    and pool_home == home_goals
                    and pool_away == away_goals
                ]
                if len(candidates) == 1:
                    votes[unknown][candidates[0]] += 1
            fresh: dict[str, tuple[int, int, int]] = {}
            for name, counter in votes.items():
                if name in known:
                    continue
                team_id, top = counter.most_common(1)[0]
                total = sum(counter.values())
                if top >= min_support and top / total >= min_agreement:
                    fresh[name] = (team_id, top, total)
            if not fresh:
                break
            for name, entry in fresh.items():
                known[name] = entry[0]
                accepted[name] = entry
        for name in sorted(accepted, key=str.casefold):
            team_id, top, total = accepted[name]
            seeded.append(
                AliasRow(
                    league_id=league_id,
                    fd_div=div_by_league_name.get((league_id, name), ""),
                    fd_team=name,
                    af_team_id=team_id,
                    af_team=resolver.team_name(league_id, team_id),
                    source="auto",
                    support=top,
                    note=f"{top} of {total} co-occurrences agree",
                )
            )
    return seeded


# --- joining ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class JoinResult:
    """Outcome of :func:`join_matches`.

    Every football-data row appears in exactly one of ``matched`` and
    ``unmatched``; ``api_unpaired`` lists API fixtures in football-data-covered
    league-seasons that no football-data row was paired with.
    """

    matched: pd.DataFrame
    unmatched: pd.DataFrame
    api_unpaired: pd.DataFrame
    odds_columns: tuple[str, ...]


def _score_text(home: int | None, away: int | None) -> str:
    return f"{'?' if home is None else home}:{'?' if away is None else away}"


SCORE_BASIS_GOALS = "api_goals"
SCORE_BASIS_REGULATION = "api_regulation_time"


def _score_basis(fixture: _Fixture, home_goals: int, away_goals: int) -> str | None:
    """Which API score equals the football-data full-time score, or ``None``.

    The provider's goals are the score at the end of the match.  After extra time
    (``AET``/``PEN``) football-data may instead carry the 90-minute score, which
    is the provider's goals minus the extra-time goals when both are known.
    """

    if fixture.home_goals == home_goals and fixture.away_goals == away_goals:
        return SCORE_BASIS_GOALS
    if fixture.regulation_home == home_goals and fixture.regulation_away == away_goals:
        return SCORE_BASIS_REGULATION
    return None


def join_matches(
    fd: pd.DataFrame,
    api: pd.DataFrame,
    resolver: TeamResolver,
    *,
    tolerance_days: int = DATE_TOLERANCE_DAYS,
) -> JoinResult:
    """Pair football-data rows with API-Football fixtures, strictly one-to-one."""

    prepared = _prepare_fd(fd)
    prices = odds_columns_of(prepared)
    fixtures = _api_fixtures(api)
    covered: set[tuple[int, int]] = set()
    index: dict[tuple[int, int, int], list[_Fixture]] = defaultdict(list)
    for league_id, fixture in fixtures:
        if fixture.season is not None:
            covered.add((league_id, fixture.season))
        index[(league_id, fixture.home_id, fixture.away_id)].append(fixture)

    columns = {name: prepared[name].tolist() for name in FD_IDENTITY_COLUMNS}
    league_ids = prepared["league_id"].tolist()
    days = _fd_days(prepared)

    claims: dict[int, list[tuple[int, _Fixture, int, str, str, str]]] = defaultdict(list)
    rejected: dict[int, tuple[str, str, str | None]] = {}

    def reject(position: int, reason: str, detail: str = "", missing: str | None = None) -> None:
        rejected[position] = (reason, detail, missing)

    for position in range(len(prepared)):
        day = days[position]
        home = _clean_text(columns["fd_home_team"][position])
        away = _clean_text(columns["fd_away_team"][position])
        if day is None or home is None or away is None:
            reject(position, REASON_INVALID_ROW, "brak daty albo nazwy drużyny")
            continue
        league_id = _int_or_none(league_ids[position])
        if league_id is None:
            reject(position, REASON_NO_LEAGUE, f"dywizja {columns['fd_div'][position]}")
            continue
        season_start = _int_or_none(columns["fd_season_start"][position])
        if season_start is None:
            reject(position, REASON_INVALID_ROW, "nieczytelny sezon")
            continue
        if (league_id, season_start) not in covered:
            reject(position, REASON_SEASON_NOT_COVERED, f"API nie ma sezonu {season_start} tej ligi")
            continue
        home_goals = _int_or_none(columns["fd_home_goals"][position])
        away_goals = _int_or_none(columns["fd_away_goals"][position])
        if home_goals is None or away_goals is None:
            reject(position, REASON_NO_FD_SCORE, "football-data nie podaje wyniku")
            continue
        home_id, home_method = resolver.resolve(league_id, home)
        away_id, away_method = resolver.resolve(league_id, away)
        if home_id is None or away_id is None:
            missing = [name for name, team_id in ((home, home_id), (away, away_id)) if team_id is None]
            parts = []
            if home_id is None:
                parts.append(f"gospodarze '{home}'")
            if away_id is None:
                parts.append(f"goście '{away}'")
            reject(position, REASON_ALIAS_MISSING, "brak aliasu: " + "; ".join(parts), " | ".join(missing))
            continue
        pair = index.get((league_id, home_id, away_id), [])
        window = [fixture for fixture in pair if abs((day - fixture.day).days) <= tolerance_days]
        if not window:
            reverse = [
                fixture
                for fixture in index.get((league_id, away_id, home_id), [])
                if abs((day - fixture.day).days) <= tolerance_days
            ]
            if reverse:
                other = min(reverse, key=lambda fixture: abs((day - fixture.day).days))
                reject(
                    position,
                    REASON_SWAPPED,
                    f"w API jest odwrócona para (fixture {other.fixture_id}, "
                    f"{other.day.isoformat()}, wynik {_score_text(other.home_goals, other.away_goals)})",
                )
            elif pair:
                nearest = min(pair, key=lambda fixture: abs((day - fixture.day).days))
                reject(
                    position,
                    REASON_NO_CANDIDATE,
                    f"najbliższy mecz tej pary w API: {nearest.day.isoformat()} "
                    f"(różnica {abs((day - nearest.day).days)} dni, fixture {nearest.fixture_id})",
                )
            else:
                reject(position, REASON_NO_CANDIDATE, "ta para nie ma żadnego meczu w tej lidze w API")
            continue
        played = [f for f in window if f.home_goals is not None and f.away_goals is not None]
        if not played:
            first = window[0]
            reject(
                position,
                REASON_API_NO_RESULT,
                f"API: fixture {first.fixture_id} ma status {first.status or '?'} i nie ma wyniku",
            )
            continue
        agreeing = [
            (f, basis)
            for f in played
            if (basis := _score_basis(f, home_goals, away_goals)) is not None
        ]
        if not agreeing:
            shown = "; ".join(
                f"fixture {f.fixture_id} ({f.status or '?'}) {_score_text(f.home_goals, f.away_goals)}"
                + (
                    f", po 90 min {_score_text(f.regulation_home, f.regulation_away)}"
                    if f.regulation_home is not None
                    else ""
                )
                for f in sorted(played, key=lambda f: abs((day - f.day).days))
            )
            reject(
                position,
                REASON_SCORE_MISMATCH,
                f"wynik FD {_score_text(home_goals, away_goals)}, API: {shown}",
            )
            continue
        distances = sorted(abs((day - f.day).days) for f, _ in agreeing)
        if len(agreeing) > 1 and distances[0] == distances[1]:
            shown = ", ".join(f"{f.fixture_id} ({f.day.isoformat()})" for f, _ in agreeing)
            reject(position, REASON_AMBIGUOUS, f"kandydaci w API: {shown}")
            continue
        chosen, basis = min(agreeing, key=lambda entry: abs((day - entry[0].day).days))
        claims[chosen.fixture_id].append(
            (position, chosen, (day - chosen.day).days, home_method or "", away_method or "", basis)
        )

    accepted: list[tuple[int, _Fixture, int, str, str, str]] = []
    for fixture_id in sorted(claims):
        entries = claims[fixture_id]
        if len(entries) == 1:
            accepted.append(entries[0])
            continue
        signatures = {
            (
                days[position],
                _clean_text(columns["fd_home_team"][position]),
                _clean_text(columns["fd_away_team"][position]),
                _int_or_none(columns["fd_home_goals"][position]),
                _int_or_none(columns["fd_away_goals"][position]),
            )
            for position, *_ in entries
        }
        entries = sorted(entries, key=lambda entry: entry[0])
        if len(signatures) == 1:
            accepted.append(entries[0])
            for position, *_ in entries[1:]:
                reject(
                    position,
                    REASON_DUPLICATE,
                    f"powtórzony wiersz FD; mecz jest już połączony z wierszem {columns['source_row_id'][entries[0][0]]}",
                )
        else:
            ids = ", ".join(str(columns["source_row_id"][position]) for position, *_ in entries)
            for position, *_ in entries:
                reject(
                    position,
                    REASON_AMBIGUOUS,
                    f"fixture {fixture_id} zgłasza kilka różnych wierszy FD: {ids}",
                )

    matched = _assemble_matched(prepared, columns, days, accepted, prices)
    unmatched = _assemble_unmatched(columns, days, league_ids, rejected)
    paired_ids = {fixture.fixture_id for _, fixture, *_ in accepted}
    covered_by_fd = {
        (league_id, season)
        for league_id, season in zip(
            (_int_or_none(value) for value in league_ids),
            (_int_or_none(value) for value in columns["fd_season_start"]),
        )
        if league_id is not None and season is not None
    }
    unpaired_rows = [
        {
            "fixture_id": fixture.fixture_id,
            "league_id": league_id,
            "af_league_code": fixture.league_code,
            "season": fixture.season,
            "kickoff_utc": fixture.kickoff,
            "status_short": fixture.status,
            "home_team": fixture.home_name,
            "away_team": fixture.away_name,
            "home_goals": fixture.home_goals,
            "away_goals": fixture.away_goals,
        }
        for league_id, fixture in fixtures
        if fixture.fixture_id not in paired_ids and (league_id, fixture.season) in covered_by_fd
    ]
    unpaired = _typed_frame(unpaired_rows, API_UNPAIRED_COLUMNS, _INTEGER_API_UNPAIRED)
    return JoinResult(matched=matched, unmatched=unmatched, api_unpaired=unpaired, odds_columns=tuple(prices))


def _typed_frame(rows: list[dict[str, Any]], columns: Sequence[str], integers: frozenset[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=list(columns))
    for column in columns:
        if column in integers:
            frame[column] = pd.array(frame[column].tolist(), dtype="Int64")
    return frame


def _assemble_matched(
    prepared: pd.DataFrame,
    columns: Mapping[str, list],
    days: list[date | None],
    accepted: list[tuple[int, _Fixture, int, str, str, str]],
    prices: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for position, fixture, diff, home_method, away_method, basis in accepted:
        rows.append(
            {
                "fixture_id": fixture.fixture_id,
                "fd_source_row_id": columns["source_row_id"][position],
                "fd_div": columns["fd_div"][position],
                "fd_league_name": _clean_text(columns["fd_league_name"][position]),
                "fd_season": _clean_text(columns["fd_season"][position]),
                "fd_season_start": columns["fd_season_start"][position],
                "league_id": prepared["league_id"].iloc[position],
                "af_league_code": fixture.league_code,
                "af_season": fixture.season,
                "fd_date": days[position].isoformat() if days[position] else None,
                "fd_time": _clean_text(columns["fd_time"][position]),
                "af_kickoff_utc": fixture.kickoff,
                "date_diff_days": diff,
                "fd_home_team": _clean_text(columns["fd_home_team"][position]),
                "fd_away_team": _clean_text(columns["fd_away_team"][position]),
                "af_home_team": fixture.home_name,
                "af_away_team": fixture.away_name,
                "af_home_team_id": fixture.home_id,
                "af_away_team_id": fixture.away_id,
                "home_match_method": home_method,
                "away_match_method": away_method,
                "fd_home_goals": columns["fd_home_goals"][position],
                "fd_away_goals": columns["fd_away_goals"][position],
                "af_home_goals": fixture.home_goals,
                "af_away_goals": fixture.away_goals,
                "score_basis": basis,
                "fd_ht_home_goals": columns["fd_ht_home_goals"][position],
                "fd_ht_away_goals": columns["fd_ht_away_goals"][position],
            }
        )
    frame = _typed_frame(rows, MATCHED_COLUMNS, _INTEGER_MATCHED)
    positions = [entry[0] for entry in accepted]
    price_frame = prepared.iloc[positions][prices].reset_index(drop=True) if positions else pd.DataFrame(columns=prices)
    combined = pd.concat([frame, price_frame], axis=1)
    return combined.sort_values(["league_id", "fd_date", "fixture_id"], kind="stable", ignore_index=True)


def _assemble_unmatched(
    columns: Mapping[str, list],
    days: list[date | None],
    league_ids: list,
    rejected: Mapping[int, tuple[str, str, str | None]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for position in sorted(rejected):
        reason, detail, missing = rejected[position]
        rows.append(
            {
                "fd_source_row_id": columns["source_row_id"][position],
                "fd_div": columns["fd_div"][position],
                "fd_league_name": _clean_text(columns["fd_league_name"][position]),
                "fd_season": _clean_text(columns["fd_season"][position]),
                "fd_season_start": columns["fd_season_start"][position],
                "league_id": league_ids[position],
                "fd_date": days[position].isoformat() if days[position] else None,
                "fd_time": _clean_text(columns["fd_time"][position]),
                "fd_home_team": _clean_text(columns["fd_home_team"][position]),
                "fd_away_team": _clean_text(columns["fd_away_team"][position]),
                "fd_home_goals": columns["fd_home_goals"][position],
                "fd_away_goals": columns["fd_away_goals"][position],
                "reason": reason,
                "in_scope": int(reason not in EXCLUDED_REASONS),
                "detail": detail,
                "missing_names": missing,
            }
        )
    frame = _typed_frame(rows, UNMATCHED_COLUMNS, _INTEGER_UNMATCHED)
    frame["in_scope"] = frame["in_scope"].astype("Int64").astype(bool)
    return frame


# --- summaries -------------------------------------------------------------------------------------------------------


def summarize_by_league_season(result: JoinResult) -> pd.DataFrame:
    """Counts of matched rows and of every unmatched reason per division and season."""

    keys = ["fd_div", "league_id", "fd_season_start"]
    parts = []
    if not result.matched.empty:
        parts.append(result.matched[keys].assign(outcome=REASON_MATCHED))
    if not result.unmatched.empty:
        parts.append(result.unmatched[keys].assign(outcome=result.unmatched["reason"]))
    outcomes = [REASON_MATCHED, *ALL_REASONS]
    if not parts:
        return pd.DataFrame(columns=[*keys, "fd_rows", *outcomes, "excluded", "eligible", "match_rate"])
    combined = pd.concat(parts, ignore_index=True)
    combined["league_id"] = combined["league_id"].astype("Int64").fillna(0).astype("int64")
    combined["fd_season_start"] = combined["fd_season_start"].astype("Int64").fillna(0).astype("int64")
    table = combined.groupby([*keys, "outcome"]).size().unstack("outcome", fill_value=0)
    for outcome in outcomes:
        if outcome not in table.columns:
            table[outcome] = 0
    table = table.reset_index()
    table.columns.name = None
    table["fd_rows"] = table[outcomes].sum(axis=1)
    table["excluded"] = table[list(EXCLUDED_REASONS)].sum(axis=1)
    table["eligible"] = table["fd_rows"] - table["excluded"]
    table["match_rate"] = table[REASON_MATCHED] / table["eligible"].where(table["eligible"] > 0)
    ordered = [*keys, "fd_rows", *outcomes, "excluded", "eligible", "match_rate"]
    return table.loc[:, ordered].sort_values(["league_id", "fd_div", "fd_season_start"], ignore_index=True)


def suggest_team_names(
    result: JoinResult,
    resolver: TeamResolver,
    *,
    top: int = 3,
) -> dict[tuple[int, str], list[tuple[str, float]]]:
    """Fuzzy API name suggestions for unresolved football-data names (never applied)."""

    missing = result.unmatched.loc[result.unmatched["reason"].eq(REASON_ALIAS_MISSING)]
    names: set[tuple[int, str]] = set()
    for league_id, joined in zip(missing["league_id"].tolist(), missing["missing_names"].tolist()):
        if _int_or_none(league_id) is None or not joined:
            continue
        for name in str(joined).split(" | "):
            names.add((int(league_id), name))
    suggestions: dict[tuple[int, str], list[tuple[str, float]]] = {}
    for league_id, name in sorted(names):
        target = normalize_team_name(name)
        scored = sorted(
            (
                (SequenceMatcher(None, target, normalize_team_name(api_name)).ratio(), api_name)
                for api_name in resolver.teams(league_id).values()
            ),
            reverse=True,
        )
        suggestions[(league_id, name)] = [(api_name, score) for score, api_name in scored[:top]]
    return suggestions


# --- output database ---------------------------------------------------------------------------------------------------


_MATCHED_DDL_TYPES: dict[str, str] = {
    **{column: "INTEGER" for column in _INTEGER_MATCHED},
    **{column: "TEXT" for column in MATCHED_COLUMNS if column not in _INTEGER_MATCHED},
}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_column(frame: pd.DataFrame, name: str) -> list[Any]:
    series = frame[name]
    missing = series.isna().tolist()
    return [None if gone else value for value, gone in zip(series.tolist(), missing)]


def _insert_frame(conn: sqlite3.Connection, table: str, frame: pd.DataFrame, columns: Sequence[str]) -> None:
    if frame.empty:
        return
    values = zip(*(_sql_column(frame, name) for name in columns))
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(_quote(name) for name in columns)}) VALUES ({placeholders})",
        values,
    )


def write_join_database(
    path: str | Path,
    result: JoinResult,
    *,
    meta: Mapping[str, object] | None = None,
) -> None:
    """Build the output SQLite in a temp file and atomically replace ``path``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    for leftover in (temp, temp.with_name(temp.name + "-journal")):
        if leftover.exists():
            leftover.unlink()
    prices = list(result.odds_columns)
    matched_columns = [*MATCHED_COLUMNS, *prices]
    definitions = []
    for column in MATCHED_COLUMNS:
        sql_type = _MATCHED_DDL_TYPES[column]
        if column == "fixture_id":
            definitions.append("fixture_id INTEGER PRIMARY KEY")
        elif column == "fd_source_row_id":
            definitions.append("fd_source_row_id TEXT NOT NULL UNIQUE")
        elif column in ("fd_div", "league_id", "fd_date", "date_diff_days"):
            definitions.append(f"{column} {sql_type} NOT NULL")
        else:
            definitions.append(f"{column} {sql_type}")
    definitions.extend(f"{_quote(price)} REAL" for price in prices)
    unmatched_definitions = ", ".join(
        f"{column} {'INTEGER' if column in _INTEGER_UNMATCHED else 'TEXT'}" for column in UNMATCHED_COLUMNS
    )
    unpaired_definitions = ", ".join(
        f"{column} {'INTEGER' if column in _INTEGER_API_UNPAIRED else 'TEXT'}"
        for column in API_UNPAIRED_COLUMNS
    )
    try:
        with closing(sqlite3.connect(temp)) as conn:
            conn.executescript(
                f"""
                CREATE TABLE fd_af_matches ({', '.join(definitions)});
                CREATE INDEX idx_fd_af_league_season ON fd_af_matches (league_id, fd_season_start);
                CREATE INDEX idx_fd_af_date ON fd_af_matches (fd_date);
                CREATE TABLE fd_unmatched ({unmatched_definitions});
                CREATE INDEX idx_fd_unmatched_reason ON fd_unmatched (reason);
                CREATE TABLE api_without_fd ({unpaired_definitions});
                CREATE TABLE odds_columns (
                    column_name TEXT PRIMARY KEY, bookmaker TEXT, market TEXT, phase TEXT
                );
                CREATE TABLE join_meta (key TEXT PRIMARY KEY, value TEXT);
                """
            )
            _insert_frame(conn, "fd_af_matches", result.matched, matched_columns)
            unmatched = result.unmatched.copy()
            unmatched["in_scope"] = unmatched["in_scope"].astype(int)
            _insert_frame(conn, "fd_unmatched", unmatched, UNMATCHED_COLUMNS)
            _insert_frame(conn, "api_without_fd", result.api_unpaired, API_UNPAIRED_COLUMNS)
            conn.executemany(
                "INSERT INTO odds_columns (column_name, bookmaker, market, phase) VALUES (?, ?, ?, ?)",
                [(price, *classify_odds_column(price)) for price in prices],
            )
            info = {
                "schema_version": SCHEMA_VERSION,
                "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "matched_rows": str(len(result.matched)),
                "unmatched_rows": str(len(result.unmatched)),
                **{key: str(value) for key, value in (meta or {}).items()},
            }
            conn.executemany("INSERT INTO join_meta (key, value) VALUES (?, ?)", sorted(info.items()))
            conn.commit()
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


# --- report (Polish) -----------------------------------------------------------------------------------------------------


def _percent(numerator: float, denominator: float) -> str:
    return "–" if not denominator else f"{100 * numerator / denominator:.2f}%"


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def _league_name(league_id: int, div: str) -> str:
    return LEAGUE_LABELS.get(league_id) or f"{div} (bez mapowania)"


def render_report(
    result: JoinResult,
    *,
    generated_at: str | None = None,
    suggestions: Mapping[tuple[int, str], Sequence[tuple[str, float]]] | None = None,
    meta: Mapping[str, object] | None = None,
    sample_limit: int = 60,
) -> str:
    """Render the Polish unmatched report: rates per league, reasons, missing aliases."""

    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    table = summarize_by_league_season(result)
    total_rows = int(table["fd_rows"].sum()) if not table.empty else 0
    matched_total = len(result.matched)
    excluded_total = int(table["excluded"].sum()) if not table.empty else 0
    eligible_total = total_rows - excluded_total
    lines: list[str] = [
        "# Połączenie football-data.co.uk z API-Football: raport niepołączonych meczów",
        "",
        f"Wygenerowano: {stamp}.",
        "",
    ]
    if meta:
        lines += [f"- {key}: {value}" for key, value in meta.items()] + [""]

    lines += [
        "## Wynik w skrócie",
        "",
        f"- Wierszy football-data razem: **{total_rows}**.",
        f"- Połączone z meczem API-Football: **{matched_total}**.",
        f"- Wyłączone z rachunku skuteczności (nie da się ich połączyć z założenia): **{excluded_total}**.",
        f"- Do połączenia (kwalifikujące się): **{eligible_total}**, z czego niepołączone: "
        f"**{eligible_total - matched_total}**.",
        f"- Skuteczność wśród kwalifikujących się: **{_percent(matched_total, eligible_total)}**.",
        "",
        "### Przyczyny (wszystkie wiersze football-data)",
        "",
    ]
    reason_counts = Counter(result.unmatched["reason"].tolist())
    reason_rows = [(REASON_LABELS_PL[REASON_MATCHED], matched_total, "połączone")]
    for reason in ALL_REASONS:
        if reason_counts.get(reason):
            scope = "wyłączone z rachunku" if reason in EXCLUDED_REASONS else "niepołączone (liczy się do skuteczności)"
            reason_rows.append((REASON_LABELS_PL[reason], reason_counts[reason], scope))
    lines += [_md_table(["Przyczyna", "Wiersze", "Status"], reason_rows), ""]

    top20 = table[table["league_id"].isin(TOP20_LEAGUE_IDS) & table["fd_season_start"].isin(list(TOP20_SEASONS))]
    lines += ["## Skuteczność w 20 ligach top, sezony 2015–2025", ""]
    if top20.empty:
        lines += ["Brak danych.", ""]
    else:
        rows = []
        grouped = top20.groupby(["league_id", "fd_div"], sort=True)
        for (league_id, div), group in grouped:
            fd_rows, excluded = int(group["fd_rows"].sum()), int(group["excluded"].sum())
            eligible, matched = int(group["eligible"].sum()), int(group[REASON_MATCHED].sum())
            not_covered = int(group[REASON_SEASON_NOT_COVERED].sum())
            rows.append(
                (
                    _league_name(int(league_id), str(div)),
                    div,
                    fd_rows,
                    not_covered,
                    excluded - not_covered,
                    eligible,
                    matched,
                    eligible - matched,
                    _percent(matched, eligible),
                    _percent(matched, fd_rows - int(group[REASON_DUPLICATE].sum()) - int(group[REASON_NO_FD_SCORE].sum())),
                )
            )
        totals = top20[["fd_rows", "excluded", "eligible", REASON_MATCHED, REASON_SEASON_NOT_COVERED]].sum()
        rows.append(
            (
                "**Razem top 20**",
                "",
                int(totals["fd_rows"]),
                int(totals[REASON_SEASON_NOT_COVERED]),
                int(totals["excluded"] - totals[REASON_SEASON_NOT_COVERED]),
                int(totals["eligible"]),
                int(totals[REASON_MATCHED]),
                int(totals["eligible"] - totals[REASON_MATCHED]),
                f"**{_percent(totals[REASON_MATCHED], totals['eligible'])}**",
                "",
            )
        )
        lines += [
            _md_table(
                [
                    "Liga",
                    "Kod FD",
                    "Mecze FD",
                    "Sezon poza zakresem API",
                    "Inne wyłączone",
                    "Do połączenia",
                    "Połączone",
                    "Niepołączone",
                    "Skuteczność (z kwalifikujących się)",
                    "Skuteczność (ze wszystkich z wynikiem)",
                ],
                rows,
            ),
            "",
            f"Cel z planu: co najmniej {TARGET_MATCH_RATE:.0%} połączonych wśród kwalifikujących się. "
            f"Wynik: **{_percent(totals[REASON_MATCHED], totals['eligible'])}**. "
            "Kolumna „Sezon poza zakresem API” to mecze z sezonów, których archiwum API-Football "
            "nie ma dla danej ligi (np. Ekstraklasa zaczyna się w API od sezonu 2018); "
            "ostatnia kolumna liczy je jako niepołączone.",
            "",
        ]

    others = table[~table["league_id"].isin(TOP20_LEAGUE_IDS) & table["league_id"].gt(0)]
    if not others.empty:
        lines += ["## Pozostałe zmapowane ligi (wszystkie sezony)", ""]
        rows = []
        for (league_id, div), group in others.groupby(["league_id", "fd_div"], sort=True):
            eligible, matched = int(group["eligible"].sum()), int(group[REASON_MATCHED].sum())
            rows.append(
                (
                    _league_name(int(league_id), str(div)),
                    div,
                    int(group["fd_rows"].sum()),
                    int(group[REASON_SEASON_NOT_COVERED].sum()),
                    eligible,
                    matched,
                    eligible - matched,
                    _percent(matched, eligible),
                )
            )
        lines += [
            _md_table(
                ["Liga", "Kod FD", "Mecze FD", "Sezon poza zakresem API", "Do połączenia", "Połączone", "Niepołączone", "Skuteczność"],
                rows,
            ),
            "",
        ]

    unmapped = result.unmatched[result.unmatched["reason"].eq(REASON_NO_LEAGUE)]
    if not unmapped.empty:
        lines += ["## Dywizje bez mapowania ligi (nie są łączone)", ""]
        rows = []
        for (div, name), group in unmapped.groupby(
            [unmapped["fd_div"], unmapped["fd_league_name"].fillna("")], sort=True
        ):
            seasons = group["fd_season_start"].dropna()
            span = f"{int(seasons.min())}–{int(seasons.max())}" if len(seasons) else "–"
            rows.append((div, name or "–", len(group), span))
        lines += [_md_table(["Kod FD", "Nazwa ligi", "Wiersze", "Sezony"], rows), ""]

    lines += _report_causes_section(result)
    lines += _report_league_season_section(table)
    lines += _report_alias_section(result, suggestions or {})
    lines += _report_sample_sections(result, sample_limit)
    lines += _report_api_gap_section(result)
    lines += _report_date_section(result)
    lines += [
        "## Metoda",
        "",
        f"- Para wymaga tej samej ligi, daty w oknie ±{DATE_TOLERANCE_DAYS} dzień (data football-data kontra data UTC "
        "startu meczu w API-Football) i tej samej tożsamości obu drużyn.",
        "- Tożsamość drużyny: alias z tabeli `app/config/team_aliases_fd_af.csv` (wpis ręczny ma pierwszeństwo), "
        "a bez aliasu dokładna zgodność znormalizowanej nazwy. Podobieństwo tekstu nigdy nie łączy meczów; "
        "służy tylko do podpowiedzi w tym raporcie.",
        "- Aliasy `auto` powstają automatycznie: ta sama liga, ten sam dzień i wynik, jedna drużyna już rozpoznana, "
        f"co najmniej {SEED_MIN_SUPPORT} zgodne wystąpienia i {SEED_MIN_AGREEMENT:.0%} zgodności. "
        "Niejednoznaczne przypadki nie dostają aliasu.",
        "- Wynik po 90 minutach z obu źródeł musi się zgadzać, inaczej para jest odrzucona i trafia tutaj.",
        "- Łączenie jest ścisłe 1:1: żaden mecz API ani wiersz football-data nie jest użyty dwa razy.",
        "- Kursy zamknięcia (kolumny z literą C, np. PSCH, MaxCH) służą wyłącznie do liczenia CLV; "
        "tabela `odds_columns` w bazie oznacza każdą kolumnę jako otwarcia lub zamknięcia.",
        "",
    ]
    return "\n".join(lines)


def _report_causes_section(result: JoinResult) -> list[str]:
    counts = Counter(result.unmatched["reason"].tolist())
    if not counts:
        return []
    lines = ["## Co oznaczają przyczyny", ""]
    lines += [
        f"- **{REASON_LABELS_PL[reason]}** ({counts[reason]}): {REASON_HELP_PL[reason]}"
        for reason in ALL_REASONS
        if counts.get(reason)
    ]
    lines.append("")
    pairs = Counter(zip(result.unmatched["fd_div"].tolist(), result.unmatched["reason"].tolist()))
    notes = [(div, reason, text) for div, reason, text in KNOWN_CAUSES_PL if pairs.get((div, reason))]
    if notes:
        lines += ["### Sprawdzone przyczyny pojedynczych braków", ""]
        lines += [
            f"- **{_league_name(DIVISION_BY_CODE[div].league_id, div)}**, {REASON_LABELS_PL[reason]} "
            f"(wiersze: {pairs[(div, reason)]}): {text}"
            for div, reason, text in notes
        ]
        lines.append("")
    return lines


def _report_league_season_section(table: pd.DataFrame) -> list[str]:
    lines = ["## Niepołączone wg ligi i sezonu", ""]
    problems = table[(table["excluded"] > 0) | (table["eligible"] > table[REASON_MATCHED])]
    problems = problems[problems["league_id"].gt(0)]
    if problems.empty:
        return lines + ["Wszystkie mecze kwalifikujące się zostały połączone.", ""]
    rows = []
    for record in problems.itertuples(index=False):
        row = record._asdict()
        rows.append(
            (
                _league_name(int(row["league_id"]), str(row["fd_div"])),
                f"{int(row['fd_season_start'])}/{int(row['fd_season_start']) + 1}",
                row["fd_rows"],
                row[REASON_MATCHED],
                _percent(row[REASON_MATCHED], row["eligible"]),
                row[REASON_ALIAS_MISSING],
                row[REASON_NO_CANDIDATE],
                row[REASON_SCORE_MISMATCH],
                row[REASON_AMBIGUOUS],
                row[REASON_SWAPPED],
                row[REASON_API_NO_RESULT],
                row["excluded"],
            )
        )
    lines += [
        "Wiersze tylko dla ligi i sezonu, w których coś zostało niepołączone lub wyłączone. "
        "„Wyłączone” to sezon poza zakresem API, mecz bez wyniku w football-data albo duplikat.",
        "",
        _md_table(
            [
                "Liga",
                "Sezon",
                "Mecze FD",
                "Połączone",
                "Skuteczność",
                "Brak aliasu",
                "Brak kandydata",
                "Rozbieżny wynik",
                "Niejednoznaczne",
                "Odwrócona para",
                "API bez wyniku",
                "Wyłączone",
            ],
            rows,
        ),
        "",
    ]
    return lines


def _report_alias_section(
    result: JoinResult, suggestions: Mapping[tuple[int, str], Sequence[tuple[str, float]]]
) -> list[str]:
    missing = result.unmatched[result.unmatched["reason"].eq(REASON_ALIAS_MISSING)]
    lines = ["## Brakujące aliasy drużyn", ""]
    if missing.empty:
        return lines + ["Każda drużyna z niepołączonych meczów ma alias.", ""]
    counts: Counter[tuple[int, str, str]] = Counter()
    for league_id, div, joined in zip(
        missing["league_id"].tolist(), missing["fd_div"].tolist(), missing["missing_names"].tolist()
    ):
        for name in str(joined).split(" | "):
            counts[(int(league_id) if _int_or_none(league_id) is not None else 0, str(div), name)] += 1
    rows = []
    for (league_id, div, name), count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        hints = suggestions.get((league_id, name), ())
        shown = ", ".join(f"{api_name} ({score:.2f})" for api_name, score in hints) or "–"
        rows.append((_league_name(league_id, div), name, count, shown))
    lines += [
        "Nazwa z football-data, dla której nie ma aliasu ani dokładnego odpowiednika. Podpowiedzi to najbliższe "
        "nazwy z API (podobieństwo tekstu, tylko do oceny przez człowieka). Aby dodać alias, wpisz wiersz "
        "`source=manual` w `app/config/team_aliases_fd_af.csv`.",
        "",
        _md_table(["Liga", "Nazwa w football-data", "Niepołączone mecze", "Podpowiedzi z API"], rows),
        "",
    ]
    return lines


def _report_sample_sections(result: JoinResult, limit: int) -> list[str]:
    lines: list[str] = []
    for reason, title in (
        (REASON_SCORE_MISMATCH, "Rozbieżności wyniku (odrzucone pary)"),
        (REASON_AMBIGUOUS, "Niejednoznaczne dopasowania"),
        (REASON_SWAPPED, "Odwrócona para gospodarz/gość"),
        (REASON_API_NO_RESULT, "Mecz API bez wyniku"),
        (REASON_NO_CANDIDATE, "Brak kandydata w oknie dat"),
    ):
        subset = result.unmatched[result.unmatched["reason"].eq(reason)]
        if subset.empty:
            continue
        lines += [f"## {title}", ""]
        # The 20 target leagues come first so a cap never hides the rows that matter most.
        priority = ~subset["league_id"].astype("Int64").isin(list(TOP20_LEAGUE_IDS))
        shown = subset.assign(_priority=priority.astype(int)).sort_values(
            ["_priority", "fd_div", "fd_date"], kind="stable"
        ).head(limit)
        rows = [
            (
                _league_name(int(row.league_id), str(row.fd_div)) if _int_or_none(row.league_id) is not None else row.fd_div,
                row.fd_date,
                f"{row.fd_home_team} – {row.fd_away_team}",
                _score_text(_int_or_none(row.fd_home_goals), _int_or_none(row.fd_away_goals)),
                row.detail,
            )
            for row in shown.itertuples(index=False)
        ]
        lines += [_md_table(["Liga", "Data", "Mecz (football-data)", "Wynik FD", "Szczegóły"], rows), ""]
        if len(subset) > limit:
            lines += [
                f"Pokazano {limit} z {len(subset)} wierszy. Pełna lista jest w tabeli `fd_unmatched` "
                "(kolumny `reason` i `detail`).",
                "",
            ]
    return lines


def _report_api_gap_section(result: JoinResult) -> list[str]:
    gaps = result.api_unpaired
    lines = ["## Mecze API-Football bez pary w football-data", ""]
    if gaps.empty:
        return lines + ["Każdy mecz API z pokrytych sezonów ma parę.", ""]
    finished = gaps[gaps["home_goals"].notna()]
    lines += [
        f"W sezonach pokrytych przez football-data jest **{len(gaps)}** meczów API bez pary "
        f"(w tym **{len(finished)}** z wynikiem). Zwykle to mecze, których football-data nie ma "
        "(baraże i play-offy, dodatkowe kolejki, sezony przerwane w 2020 r.), mecze odwołane albo "
        "pary odrzucone z powodu rozbieżnego wyniku. Lista jest w tabeli `api_without_fd`.",
        "",
    ]
    grouped = (
        finished[finished["league_id"].isin(TOP20_LEAGUE_IDS)]
        .groupby("league_id")
        .size()
        .sort_values(ascending=False)
    )
    if not grouped.empty:
        rows = [(_league_name(int(league_id), ""), int(count)) for league_id, count in grouped.items()]
        lines += [_md_table(["Liga (top 20)", "Mecze API z wynikiem bez pary"], rows), ""]
    return lines


def _report_date_section(result: JoinResult) -> list[str]:
    if result.matched.empty:
        return []
    counts = result.matched["date_diff_days"].value_counts().sort_index()
    rows = [(f"{int(diff):+d}", int(count)) for diff, count in counts.items()]
    return [
        "## Różnica dat w połączonych parach",
        "",
        "Data football-data minus data UTC startu meczu w API (0 = ten sam dzień).",
        "",
        _md_table(["Różnica (dni)", "Pary"], rows),
        "",
    ]


# --- orchestration -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    result: JoinResult
    alias_rows: int
    auto_alias_rows: int
    seconds: float
    unknown_alias_targets: tuple[tuple[int, str, int], ...]


def run_join(
    *,
    archives: Sequence[str | Path],
    history_db: str | Path,
    aliases_path: str | Path,
    out_dir: str | Path,
    seed: bool = True,
    log: Any = None,
) -> RunSummary:
    """Read both sources, refresh the alias table, join, and write database plus report."""

    started = time.perf_counter()

    def say(message: str) -> None:
        if log is not None:
            log(message)

    raw_fd = read_football_data_archives(archives)
    unusable_prices = int(raw_fd.attrs.get("unusable_prices_nulled", 0))
    fd = assign_league_ids(raw_fd)
    say(f"football-data rows: {len(fd)} ({unusable_prices} price cells <= {MIN_VALID_PRICE} set to NULL)")
    mapped_leagues = sorted({int(value) for value in fd["league_id"].dropna().tolist()})
    api = load_api_matches(history_db, mapped_leagues)
    say(f"API-Football matches in mapped leagues: {len(api)}")
    existing = load_aliases(aliases_path)
    fixed = [row for row in existing if row.source in ("manual", "block")]
    if seed:
        auto = seed_team_aliases(fd, api, fixed)
        rows = [*fixed, *auto]
        save_aliases(aliases_path, rows)
        say(f"aliases: {len(fixed)} human rows kept, {len(auto)} auto rows seeded")
    else:
        rows = existing
    resolver = TeamResolver(api, rows)
    result = join_matches(fd, api, resolver)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "football_data_archives": "; ".join(Path(p).name for p in archives),
        "api_history_db": str(history_db),
        "alias_file": str(aliases_path),
        "date_tolerance_days": DATE_TOLERANCE_DAYS,
        "price_cells_not_above_1_set_to_null": unusable_prices,
    }
    write_join_database(out / "fd_af_matches.sqlite", result, meta=meta)
    suggestions = suggest_team_names(result, resolver)
    report = render_report(
        result,
        suggestions=suggestions,
        meta={"Komórki z kursem nie większym niż 1,00 (brak notowania) zamienione na NULL": unusable_prices},
    )
    (out / "unmatched_report.md").write_text(report, encoding="utf-8")
    return RunSummary(
        result=result,
        alias_rows=len(rows),
        auto_alias_rows=sum(1 for row in rows if row.source == "auto"),
        seconds=time.perf_counter() - started,
        unknown_alias_targets=tuple(resolver.unknown_targets()),
    )
