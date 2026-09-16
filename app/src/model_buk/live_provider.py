from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from model_buk.catalog import API_ID_TO_LEAGUE, LEAGUES, season_for_date


API_BASE = "https://v3.football.api-sports.io"


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    connected: bool
    reason: str | None = None
    requests_today: int | None = None
    requests_limit: int | None = None


class JsonCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str, ttl_seconds: int) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class ApiFootballClient:
    """Small, quota-aware API-Football adapter.

    The adapter is optional. Without API_FOOTBALL_KEY the application remains
    usable in historical/manual mode and explicitly reports missing live context.
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = "runtime/api_cache",
        timeout: int = 12,
    ):
        self.api_key = (api_key or os.getenv("API_FOOTBALL_KEY") or "").strip()
        self.timeout = timeout
        self.cache = JsonCache(cache_dir)
        self._last_headers: dict[str, str] = {}
        self._session = requests.Session()

    @property
    def connected(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "name": "API-Football",
            "connected": self.connected,
            "reason": None if self.connected else "Brak API_FOOTBALL_KEY — działa tryb historyczny/manualny.",
            "requests_remaining": self._last_headers.get("x-ratelimit-requests-remaining"),
            "requests_limit": self._last_headers.get("x-ratelimit-requests-limit"),
        }

    def _get(self, endpoint: str, params: dict[str, Any] | None = None, ttl: int = 300) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("API-Football nie jest podłączone. Ustaw API_FOOTBALL_KEY.")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached
        response = self._session.get(
            API_BASE + endpoint,
            params=params,
            headers={"x-apisports-key": self.api_key},
            timeout=self.timeout,
        )
        self._last_headers = {k.lower(): v for k, v in response.headers.items()}
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            raise RuntimeError(f"API-Football: {errors}")
        self.cache.put(key, payload)
        return payload

    def fixtures(self, date: str, league_codes: list[str] | None = None) -> list[dict[str, Any]]:
        # Cost-aware: one precise date call, then filter the supported leagues
        # locally. This avoids spending one quota call per league on every refresh.
        target = datetime.fromisoformat(date).date()
        codes = set(league_codes or LEAGUES.keys())
        allowed_ids = {LEAGUES[code].api_football_id: code for code in codes if code in LEAGUES}
        payload = self._get("/fixtures", {"date": str(target)}, ttl=180)
        items: list[dict[str, Any]] = []
        for row in payload.get("response", []):
            league_id = int((row.get("league") or {}).get("id") or 0)
            code = allowed_ids.get(league_id)
            if code:
                items.append(self._normalize_fixture(row, code))
        return sorted(items, key=lambda x: x.get("kickoff") or "")

    def fixture(self, fixture_id: int) -> dict[str, Any]:
        payload = self._get("/fixtures", {"id": fixture_id}, ttl=60)
        rows = payload.get("response", [])
        if not rows:
            raise ValueError(f"Nie znaleziono fixture_id={fixture_id}")
        league_id = int(rows[0].get("league", {}).get("id") or 0)
        league = API_ID_TO_LEAGUE.get(league_id)
        return self._normalize_fixture(rows[0], league.code if league else None)

    @staticmethod
    def _normalize_fixture(row: dict[str, Any], league_code: str | None) -> dict[str, Any]:
        fixture = row.get("fixture", {})
        league = row.get("league", {})
        teams = row.get("teams", {})
        goals = row.get("goals", {})
        venue = fixture.get("venue") or {}
        return {
            "fixture_id": fixture.get("id"),
            "kickoff": fixture.get("date"),
            "status": (fixture.get("status") or {}).get("short"),
            "status_long": (fixture.get("status") or {}).get("long"),
            "league_code": league_code,
            "league_id": league.get("id"),
            "league_name": league.get("name"),
            "country": league.get("country"),
            "season": league.get("season"),
            "round": league.get("round"),
            "home": {
                "id": (teams.get("home") or {}).get("id"),
                "name": (teams.get("home") or {}).get("name"),
                "logo": (teams.get("home") or {}).get("logo"),
                "winner": (teams.get("home") or {}).get("winner"),
                "goals": goals.get("home"),
            },
            "away": {
                "id": (teams.get("away") or {}).get("id"),
                "name": (teams.get("away") or {}).get("name"),
                "logo": (teams.get("away") or {}).get("logo"),
                "winner": (teams.get("away") or {}).get("winner"),
                "goals": goals.get("away"),
            },
            "referee": fixture.get("referee"),
            "venue": {"id": venue.get("id"), "name": venue.get("name"), "city": venue.get("city")},
        }

    def injuries(self, fixture_id: int) -> list[dict[str, Any]]:
        payload = self._get("/injuries", {"fixture": fixture_id}, ttl=60 * 60)
        output = []
        for row in payload.get("response", []):
            output.append({
                "team_id": (row.get("team") or {}).get("id"),
                "team": (row.get("team") or {}).get("name"),
                "player_id": (row.get("player") or {}).get("id"),
                "player": (row.get("player") or {}).get("name"),
                "type": (row.get("player") or {}).get("type"),
                "reason": (row.get("player") or {}).get("reason"),
            })
        return output

    def lineups(self, fixture_id: int) -> list[dict[str, Any]]:
        payload = self._get("/fixtures/lineups", {"fixture": fixture_id}, ttl=15 * 60)
        result = []
        for team in payload.get("response", []):
            start = []
            for entry in team.get("startXI") or []:
                p = entry.get("player") or {}
                start.append({"id": p.get("id"), "name": p.get("name"), "number": p.get("number"), "pos": p.get("pos")})
            result.append({
                "team_id": (team.get("team") or {}).get("id"),
                "team": (team.get("team") or {}).get("name"),
                "formation": team.get("formation"),
                "coach": (team.get("coach") or {}).get("name"),
                "startXI": start,
            })
        return result

    def odds(self, fixture_id: int) -> list[dict[str, Any]]:
        payload = self._get("/odds", {"fixture": fixture_id}, ttl=60 * 30)
        output: list[dict[str, Any]] = []
        for fixture_row in payload.get("response", []):
            update = fixture_row.get("update")
            for bookmaker in fixture_row.get("bookmakers") or []:
                bname = bookmaker.get("name")
                bid = bookmaker.get("id")
                for bet in bookmaker.get("bets") or []:
                    for value in bet.get("values") or []:
                        try:
                            odd = float(value.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        output.append({
                            "bookmaker_id": bid,
                            "bookmaker": bname,
                            "bet_id": bet.get("id"),
                            "bet": bet.get("name"),
                            "value": value.get("value"),
                            "odd": odd,
                            "update": update,
                        })
        return output

    def prediction_benchmark(self, fixture_id: int) -> dict[str, Any] | None:
        payload = self._get("/predictions", {"fixture": fixture_id}, ttl=60 * 60)
        rows = payload.get("response", [])
        if not rows:
            return None
        p = rows[0].get("predictions") or {}
        return {
            "winner": (p.get("winner") or {}).get("name"),
            "win_or_draw": p.get("win_or_draw"),
            "under_over": p.get("under_over"),
            "advice": p.get("advice"),
            "percent": p.get("percent"),
            "note": "External benchmark only — never used as a PURE Model Buk feature.",
        }

    def recent_fixtures(self, team_id: int, last: int = 6) -> list[dict[str, Any]]:
        payload = self._get("/fixtures", {"team": team_id, "last": last}, ttl=60 * 30)
        output = []
        for row in payload.get("response", []):
            league_id = int((row.get("league") or {}).get("id") or 0)
            spec = API_ID_TO_LEAGUE.get(league_id)
            output.append(self._normalize_fixture(row, spec.code if spec else None))
        return output

    def fixture_statistics(self, fixture_id: int) -> dict[int, dict[str, float]]:
        payload = self._get("/fixtures/statistics", {"fixture": fixture_id}, ttl=60 * 60 * 24 * 7)
        output: dict[int, dict[str, float]] = {}
        mapping = {
            "Shots on Goal": "sot",
            "Total Shots": "shots",
            "Corner Kicks": "corners",
            "Yellow Cards": "yellow",
            "Red Cards": "red",
            "Fouls": "fouls",
            "Ball Possession": "possession",
        }
        for row in payload.get("response", []):
            team_id = int((row.get("team") or {}).get("id") or 0)
            stats: dict[str, float] = {}
            for item in row.get("statistics") or []:
                key = mapping.get(item.get("type"))
                if not key:
                    continue
                value = item.get("value")
                if isinstance(value, str) and value.endswith("%"):
                    value = value[:-1]
                try:
                    stats[key] = float(value or 0)
                except (TypeError, ValueError):
                    continue
            if team_id:
                output[team_id] = stats
        return output

    def current_observations(
        self,
        team_id: int,
        last: int = 5,
        max_stat_calls: int = 5,
        *,
        prediction_cutoff: str | None = None,
        exclude_fixture_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent completed matches and their event counts on demand.

        Cost control: fixture list is one call; detailed statistics are capped and
        cached, so dashboard scanning never performs this work automatically.
        """
        fixtures = self.recent_fixtures(team_id, last=last)
        output: list[dict[str, Any]] = []
        detailed_calls = 0
        cutoff = (
            datetime.fromisoformat(prediction_cutoff.replace("Z", "+00:00"))
            if prediction_cutoff
            else None
        )
        fetched_at = datetime.now(timezone.utc).isoformat()
        for fixture in fixtures:
            if fixture.get("status") not in {"FT", "AET", "PEN"}:
                continue
            fixture_id = int(fixture.get("fixture_id") or 0)
            if exclude_fixture_id is not None and fixture_id == int(exclude_fixture_id):
                continue
            kickoff_raw = fixture.get("kickoff")
            if cutoff and kickoff_raw:
                kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                cutoff_cmp = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
                if kickoff >= cutoff_cmp:
                    continue
            home = fixture["home"]
            away = fixture["away"]
            is_home = int(home.get("id") or 0) == int(team_id)
            row = {
                "date": fixture.get("kickoff"),
                "fixture_id": fixture_id,
                "known_at": fetched_at,
                "fetched_at": fetched_at,
                "venue": "home" if is_home else "away",
                "goals_for": home.get("goals") if is_home else away.get("goals"),
                "goals_against": away.get("goals") if is_home else home.get("goals"),
                "opponent": away.get("name") if is_home else home.get("name"),
            }
            if detailed_calls < max_stat_calls:
                try:
                    stats = self.fixture_statistics(int(fixture["fixture_id"]))
                    own = stats.get(int(team_id), {})
                    opp_id = int(away.get("id") if is_home else home.get("id"))
                    opp = stats.get(opp_id, {})
                    row.update({
                        "shots_for": own.get("shots"), "shots_against": opp.get("shots"),
                        "sot_for": own.get("sot"), "sot_against": opp.get("sot"),
                        "corners_for": own.get("corners"), "corners_against": opp.get("corners"),
                        "cards_for": (own.get("yellow", 0) or 0) + 2 * (own.get("red", 0) or 0),
                        "cards_against": (opp.get("yellow", 0) or 0) + 2 * (opp.get("red", 0) or 0),
                    })
                    detailed_calls += 1
                except Exception:
                    pass
            output.append(row)
        return output

    def match_context(self, fixture_id: int, deep: bool = True) -> dict[str, Any]:
        fixture = self.fixture(fixture_id)
        injuries: list[dict[str, Any]] = []
        lineups: list[dict[str, Any]] = []
        odds: list[dict[str, Any]] = []
        benchmark = None
        current: dict[str, list[dict[str, Any]]] = {"home": [], "away": []}
        errors: list[str] = []

        for label, fn in (
            ("injuries", lambda: self.injuries(fixture_id)),
            ("lineups", lambda: self.lineups(fixture_id)),
            ("odds", lambda: self.odds(fixture_id)),
            ("prediction_benchmark", lambda: self.prediction_benchmark(fixture_id)),
        ):
            try:
                value = fn()
                if label == "injuries": injuries = value
                elif label == "lineups": lineups = value
                elif label == "odds": odds = value
                else: benchmark = value
            except Exception as exc:
                errors.append(f"{label}: {exc}")

        if deep:
            for side in ("home", "away"):
                try:
                    current[side] = self.current_observations(
                        int(fixture[side]["id"]),
                        last=6,
                        max_stat_calls=5,
                        prediction_cutoff=fixture.get("kickoff"),
                        exclude_fixture_id=fixture_id,
                    )
                except Exception as exc:
                    errors.append(f"recent_{side}: {exc}")

        return {
            "fixture": fixture,
            "injuries": injuries,
            "lineups": lineups,
            "odds": odds,
            "external_prediction_benchmark": benchmark,
            "recent_observations": current,
            "errors": errors,
            "provider": self.status(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
