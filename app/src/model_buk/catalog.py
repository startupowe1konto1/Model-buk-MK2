from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LeagueSpec:
    code: str
    name: str
    country: str
    historical_division: str
    api_football_id: int
    default_season_start_month: int = 7


# First production-oriented coverage wave: 16 European leagues with both a
# sizeable local history and a stable API-Football league identifier.
LEAGUES: dict[str, LeagueSpec] = {
    "EPL": LeagueSpec("EPL", "Premier League", "England", "E0", 39),
    "CHAMPIONSHIP": LeagueSpec("CHAMPIONSHIP", "Championship", "England", "E1", 40),
    "LALIGA": LeagueSpec("LALIGA", "LaLiga", "Spain", "SP1", 140),
    "LALIGA2": LeagueSpec("LALIGA2", "LaLiga 2", "Spain", "SP2", 141),
    "BUNDESLIGA": LeagueSpec("BUNDESLIGA", "Bundesliga", "Germany", "D1", 78),
    "BUNDESLIGA2": LeagueSpec("BUNDESLIGA2", "2. Bundesliga", "Germany", "D2", 79),
    "SERIEA": LeagueSpec("SERIEA", "Serie A", "Italy", "I1", 135),
    "SERIEB": LeagueSpec("SERIEB", "Serie B", "Italy", "I2", 136),
    "LIGUE1": LeagueSpec("LIGUE1", "Ligue 1", "France", "F1", 61),
    "LIGUE2": LeagueSpec("LIGUE2", "Ligue 2", "France", "F2", 62),
    "PRIMEIRA": LeagueSpec("PRIMEIRA", "Primeira Liga", "Portugal", "P1", 94),
    "EREDIVISIE": LeagueSpec("EREDIVISIE", "Eredivisie", "Netherlands", "N1", 88),
    "BELGIUM": LeagueSpec("BELGIUM", "Belgian Pro League", "Belgium", "B1", 144),
    "SUPERLIG": LeagueSpec("SUPERLIG", "Süper Lig", "Turkey", "T1", 203),
    "SCOTLAND": LeagueSpec("SCOTLAND", "Scottish Premiership", "Scotland", "SC0", 179),
    "EKSTRAKLASA": LeagueSpec("EKSTRAKLASA", "Ekstraklasa", "Poland", "POL", 106),
}

DIVISION_TO_LEAGUE = {v.historical_division: v for v in LEAGUES.values()}
API_ID_TO_LEAGUE = {v.api_football_id: v for v in LEAGUES.values()}


def league_catalog() -> list[dict]:
    return [
        {
            "code": spec.code,
            "name": spec.name,
            "country": spec.country,
            "historical_division": spec.historical_division,
            "api_football_id": spec.api_football_id,
        }
        for spec in LEAGUES.values()
    ]


def season_for_date(year: int, month: int, start_month: int = 7) -> int:
    return year if month >= start_month else year - 1
