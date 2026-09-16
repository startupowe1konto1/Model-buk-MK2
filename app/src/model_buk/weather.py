from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from model_buk.team_names import normalize_name


@lru_cache(maxsize=2)
def load_stadiums(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    output = []
    for element in raw.get("elements", []):
        tags = element.get("tags") or {}
        sport = str(tags.get("sport") or "").lower()
        if sport and "soccer" not in sport and "football" not in sport:
            continue
        point = element.get("center") or element
        if point.get("lat") is None or point.get("lon") is None:
            continue
        output.append({
            "name": tags.get("name") or tags.get("name:en"),
            "city": tags.get("addr:city"),
            "lat": float(point["lat"]),
            "lon": float(point["lon"]),
            "capacity": tags.get("capacity"),
        })
    return output


def find_stadium(path: str | Path, venue_name: str | None, city: str | None = None) -> dict[str, Any] | None:
    if not venue_name:
        return None
    target = normalize_name(venue_name)
    best = None
    best_score = 0.0
    for stadium in load_stadiums(path):
        if not stadium.get("name"):
            continue
        score = SequenceMatcher(None, target, normalize_name(stadium["name"])).ratio()
        if city and stadium.get("city") and normalize_name(city) == normalize_name(stadium["city"]):
            score += 0.08
        if score > best_score:
            best, best_score = stadium, score
    if best and best_score >= 0.62:
        return {**best, "match_score": round(best_score, 3)}
    return None


def forecast_for_kickoff(lat: float, lon: float, kickoff: str, timeout: int = 10) -> dict[str, Any] | None:
    dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,snowfall",
        "timezone": "UTC",
        "forecast_days": 16,
    }
    response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    target = dt.astimezone(timezone.utc).replace(tzinfo=None)
    parsed = [datetime.fromisoformat(t) for t in times]
    idx = min(range(len(parsed)), key=lambda i: abs((parsed[i] - target).total_seconds()))
    return {
        "time": times[idx],
        "temperature_c": (hourly.get("temperature_2m") or [None] * len(times))[idx],
        "precipitation_mm": (hourly.get("precipitation") or [None] * len(times))[idx],
        "wind_kmh": (hourly.get("wind_speed_10m") or [None] * len(times))[idx],
        "gusts_kmh": (hourly.get("wind_gusts_10m") or [None] * len(times))[idx],
        "snowfall_cm": (hourly.get("snowfall") or [None] * len(times))[idx],
    }


def weather_context(stadium_path: str | Path, fixture: dict[str, Any]) -> dict[str, Any]:
    venue = fixture.get("venue") or {}
    try:
        stadium = find_stadium(stadium_path, venue.get("name"), venue.get("city"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "reason": f"Lokalna baza stadionów jest niedostępna: {exc}",
            "model_usage": "monitor_only",
        }
    if not stadium:
        return {"available": False, "reason": "Nie dopasowano stadionu do lokalnej bazy OSM."}
    try:
        forecast = forecast_for_kickoff(stadium["lat"], stadium["lon"], fixture["kickoff"])
    except Exception as exc:
        return {"available": False, "stadium": stadium, "reason": str(exc)}
    if not forecast:
        return {"available": False, "stadium": stadium, "reason": "Brak prognozy w zakresie Open-Meteo."}
    wind = float(forecast.get("wind_kmh") or 0)
    gusts = float(forecast.get("gusts_kmh") or 0)
    rain = float(forecast.get("precipitation_mm") or 0)
    snow = float(forecast.get("snowfall_cm") or 0)
    temp = forecast.get("temperature_c")
    extreme = bool(wind >= 35 or gusts >= 55 or rain >= 5 or snow >= 1 or (temp is not None and (float(temp) <= -5 or float(temp) >= 32)))
    return {
        "available": True,
        "stadium": stadium,
        "forecast": forecast,
        "extreme": extreme,
        "model_usage": "quality_gate_only" if extreme else "monitor_only",
        "note": "Normal weather is not used as a model feature; only extremes are flagged for review.",
    }
