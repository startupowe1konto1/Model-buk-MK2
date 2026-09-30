"""Market reliability tier of a fixture: ``liquid`` (the sharp price is credible) or ``thin`` (it is not).

The fair price of the scanner is Pinnacle's.  That is the best estimate of the truth only where Pinnacle takes
serious money: in the main men's leagues and the big international competitions.  In friendlies, women's and
youth football, reserve and amateur divisions and obscure competitions Pinnacle's own price is a guess with a wide
margin, and a soft book that disagrees with it is as likely to be right.  On 2026-09-30 (an international break)
such fixtures produced almost all "value" legs, with 15-25% "edges" that were artefacts.  Every fixture is
therefore put in one of two tiers, and only the ``liquid`` tier feeds the main sections of the page; the ``thin``
tier is scanned, logged and shown separately as an experiment.

A fixture is ``thin`` when any of these holds (``tier_reason`` names every rule that fired, joined with ``+``):

Name rules (``tier_name_rules``; from the fixture directory's league name, country and team names)
    ``women``     a women's competition or team ("Women", "Femenin", "Frauen", "WSL", "Damallsvenskan", ...; the
                  provider names women's teams "... W");
    ``youth``     a youth or under-N competition or team ("U17" ... "U23", "Youth", "Primavera", "Juniores",
                  "Revelação", "Premier League 2", "Premier League International Cup", "Elite League", ...);
    ``friendly``  friendlies of any kind ("Friendlies", "Friendlies Clubs", "Club Friendlies");
    ``reserve``   reserve and amateur divisions ("Reserve", "Amateur", "Regionalliga", "Oberliga", "Non League",
                  "Serie D", "Tercera", ...) or a reserve team ("... II", "... III", "... B", "... 2",
                  "... Res.").
Data rules (the board at the decision time, fresh boards only: the scanner's own staleness limits)
    ``no_pinnacle``      Pinnacle has no complete full-time 1X2 on a fresh board: there is no sharp reference at
                         all (the fair price would be a soft-book consensus);
    ``pinnacle_margin``  Pinnacle's full-time 1X2 overround is above ``tier_max_pinnacle_overround`` (1.08);
    ``few_bookmakers``   fewer than ``tier_min_bookmakers`` (6) bookmakers, Pinnacle and Betfair included, quote
                         the fixture.

How the thresholds were chosen (odds archive 2026-09-28..30, boards every six hours, 674 fixtures)
    Pinnacle's 1X2 overround in the 89 tracked competitions
    (:func:`model_buk.ingestion.api_football_history.all_planned_league_seasons`) has a median of 1.048 to 1.062
    by lead time and a 95th percentile of 1.065 to 1.070 two days and more before kickoff, up to 1.09 in the last
    two days (the Canadian Premier League and Peru's Primera División); in the other competitions the median is
    1.068 to 1.093 (friendlies 1.088, English non-league 1.10, Israel's Liga Alef 1.128, the women's European cup
    1.14).  Above 1.08 lie 5% of the tracked-competition sightings and 46% of the others.  With Pinnacle on the
    board every tracked fixture was quoted by at least 6 bookmakers (median 8 of the 11 in the feed); 4 or 5
    occurred only elsewhere (an under-19 friendly, lower divisions).  Without a Pinnacle 1X2 were 13% of the
    tracked sightings (English League One and Two four days before kickoff, before Pinnacle opens its market)
    and 37% of the others (Serie C, J3, Swedish and Spanish third and fourth tiers); such a fixture becomes
    ``liquid`` once Pinnacle opens.

A fixture that is not in the fixture directory is judged by the data rules alone.  The tier is a property of the
fixture at the decision time: it is recomputed on every scan and stored on every leg and coupon of the register.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

from model_buk.markets.pricing import normalise_bookmaker_name

if TYPE_CHECKING:  # pragma: no cover
    from model_buk.scanner.engine import ScannerConfig

LIQUID: Final = "liquid"
THIN: Final = "thin"
TIERS: Final[tuple[str, ...]] = (LIQUID, THIN)
TIER_COLUMNS: Final[tuple[str, ...]] = ("fixture_id", "tier", "tier_reason", "n_bookmakers", "pinnacle_overround")

_WOMEN_LEAGUE = re.compile(
    r"\bwomen|\bwoman\b|\bwomen's\b|\bfemen|\bf[eé]minin|\bfemminil|\bfrauen|\bdamallsvenskan\b|\bwsl\b|\bnwsl\b"
    r"|\bliga f\b|\bw-league\b|\bwk-league\b|\bkvinde|\bkvinn|\btoppserien\b|\bdames\b|\bdonne\b|\bladies\b"
    r"|\bkansallinen liiga\b|\bnorthern super league\b|\bsupreme division women\b",
    re.IGNORECASE,
)
_YOUTH = re.compile(
    r"\bu-?\s?(1[3-9]|2[0-3])\b|\bsub-?\s?(1[3-9]|2[0-3])\b|\bunder[- ]?(1[3-9]|2[0-3])\b|\byouth\b|\bprimavera\b"
    r"|\bjunior|\bjuniores\b|\bjuvenil|\brevela[cç][aã]o\b|\bpremier league 2\b|\bpremier league international cup\b"
    r"|\belite league\b|\bdevelopment league\b|\bdiski challenge\b|\bnext gen\b|\bacademy league\b",
    re.IGNORECASE,
)
_FRIENDLY = re.compile(r"\bfriendl", re.IGNORECASE)
_RESERVE_LEAGUE = re.compile(
    r"\breserve|\bamateur|\bregionalliga\b|\boberliga\b|\bnon league\b|\bserie d\b|\btercera\b|\blandesliga\b"
    r"|\bverbandsliga\b|\bbezirksliga\b",
    re.IGNORECASE,
)
_WOMEN_TEAM = re.compile(r"\sW$")
_RESERVE_TEAM = re.compile(r"\s(II|III|B|2)$|\bRes\.?$|\bReserves?\b")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def name_reasons(league_name: object, country: object = None, home_team: object = None, away_team: object = None) -> list[str]:
    """The name rules that make a fixture ``thin`` (see the module docstring); an empty list for none."""
    league = f"{_text(league_name)} {_text(country)}".strip()
    teams = [_text(home_team), _text(away_team)]
    reasons: list[str] = []
    if _WOMEN_LEAGUE.search(league) or any(_WOMEN_TEAM.search(t) for t in teams if t):
        reasons.append("women")
    if _YOUTH.search(league) or any(_YOUTH.search(t) for t in teams if t):
        reasons.append("youth")
    if _FRIENDLY.search(league):
        reasons.append("friendly")
    if _RESERVE_LEAGUE.search(league) or any(_RESERVE_TEAM.search(t) for t in teams if t):
        reasons.append("reserve")
    return reasons


def _fresh_quotes(quotes: pd.DataFrame, config: ScannerConfig, as_of: pd.Timestamp) -> pd.DataFrame:
    """Rows of boards the engine would consider fresh at ``as_of`` (same staleness limits as the legs)."""
    q = quotes[["fixture_id", "bookmaker", "market", "period", "selection", "odd", "kickoff_utc", "fetched_at"]].copy()
    q["fixture_id"] = pd.to_numeric(q["fixture_id"], errors="coerce")
    q = q[q["fixture_id"].notna()]
    q["fixture_id"] = q["fixture_id"].astype("int64")
    fetched = pd.to_datetime(q["fetched_at"], utc=True, errors="coerce", format="ISO8601") if not pd.api.types.is_datetime64_any_dtype(q["fetched_at"]) else q["fetched_at"]
    kickoff = pd.to_datetime(q["kickoff_utc"], utc=True, errors="coerce", format="ISO8601") if not pd.api.types.is_datetime64_any_dtype(q["kickoff_utc"]) else q["kickoff_utc"]
    fetched = fetched.dt.tz_localize("UTC") if getattr(fetched.dt, "tz", None) is None else fetched
    kickoff = kickoff.dt.tz_localize("UTC") if getattr(kickoff.dt, "tz", None) is None else kickoff
    lead = (kickoff - as_of).dt.total_seconds() / 3600.0
    limit = np.where(lead > config.far_lead_hours, config.max_quote_age_far_hours, config.max_quote_age_hours)
    age = (as_of - fetched).dt.total_seconds() / 3600.0
    keep = fetched.notna().to_numpy() & (fetched <= as_of).to_numpy() & ~(age.to_numpy(dtype=float) > limit)
    return q[keep]


def fixture_tiers(
    quotes: pd.DataFrame,
    fixtures: pd.DataFrame | None,
    config: ScannerConfig,
    as_of: object,
) -> pd.DataFrame:
    """Tier of every fixture of ``quotes`` at ``as_of``: :data:`TIER_COLUMNS` (see the module docstring).

    ``quotes`` is the quotes frame of the scan (every bookmaker, reference books included); ``fixtures`` the
    fixtures frame (``fixture_id, league_name, country, home_team, away_team``; optional).
    """
    stamp = pd.Timestamp(as_of)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    if quotes is None or len(quotes) == 0:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in TIER_COLUMNS})
    ids = pd.to_numeric(quotes["fixture_id"], errors="coerce").dropna().astype("int64").unique()
    out = pd.DataFrame({"fixture_id": np.sort(ids)})
    fresh = _fresh_quotes(quotes, config, stamp)
    books = fresh.assign(bk=fresh["bookmaker"].map(normalise_bookmaker_name)).groupby("fixture_id")["bk"].nunique()
    out["n_bookmakers"] = out["fixture_id"].map(books).fillna(0).astype(int)
    pin = fresh[
        (fresh["bookmaker"].map(normalise_bookmaker_name) == "pinnacle")
        & (fresh["market"].astype(str).str.lower() == "1x2")
        & (fresh["period"].astype(str).str.lower() == "ft")
    ]
    overround = pd.Series(dtype=float)
    if len(pin):
        wide = pin.pivot_table(index="fixture_id", columns="selection", values="odd", aggfunc="max")
        if {"home", "draw", "away"} <= set(wide.columns):
            wide = wide[["home", "draw", "away"]].dropna()
            wide = wide[(wide > 1.0).all(axis=1)]
            overround = (1.0 / wide).sum(axis=1)
    out["pinnacle_overround"] = out["fixture_id"].map(overround).astype(float)

    names: dict[int, list[str]] = {}
    if config.tier_name_rules and fixtures is not None and len(fixtures) and "fixture_id" in fixtures.columns:
        frame = fixtures.drop_duplicates("fixture_id", keep="last")
        for record in frame.to_dict(orient="records"):
            try:
                fixture_id = int(record["fixture_id"])
            except (TypeError, ValueError):
                continue
            names[fixture_id] = name_reasons(
                record.get("league_name"), record.get("country"), record.get("home_team"), record.get("away_team")
            )
    reasons = []
    for fixture_id, n_books, margin in zip(out["fixture_id"], out["n_bookmakers"], out["pinnacle_overround"], strict=True):
        found = list(names.get(int(fixture_id), []))
        if not np.isfinite(margin):
            found.append("no_pinnacle")
        elif margin > config.tier_max_pinnacle_overround:
            found.append("pinnacle_margin")
        if n_books < config.tier_min_bookmakers:
            found.append("few_bookmakers")
        reasons.append("+".join(found))
    out["tier_reason"] = reasons
    out["tier"] = np.where(out["tier_reason"] == "", LIQUID, THIN)
    return out[list(TIER_COLUMNS)]


__all__ = ["LIQUID", "THIN", "TIERS", "TIER_COLUMNS", "fixture_tiers", "name_reasons"]
