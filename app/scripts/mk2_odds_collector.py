#!/usr/bin/env python3
"""MK2: kolektor kursów i kalendarza meczów z API-Football.

Tylko biblioteka standardowa, żeby działał bez instalowania pakietów.
Klucz bierze wyłącznie ze zmiennej środowiskowej (domyślnie API_FOOTBALL_KEY)
i nigdy go nie zapisuje ani nie wypisuje.

Tryby:
  status    jedno zapytanie /status: plan, data końca, zużycie dzienne
  fixtures  kalendarz i wyniki: wczoraj .. dziś + N dni (1 zapytanie na dzień)
  odds      kursy przedmeczowe dla wybranych lig (zapytania per liga i strona)
  closing   kursy meczów, które zaczynają się w ciągu najbliższych X minut
            (1 zapytanie na mecz, z limitem), do uczciwego CLV
  all       fixtures + odds (zadanie godzinne)

Dane trafiają poza repozytorium, domyślnie do
  ~/Documents/Model-Buk-MK2-Data/
    collector.sqlite      tabele: runs, league_seasons, fixtures, quotes, odds_seen
    raw/<endpoint>/<data>/*.json.gz   surowe odpowiedzi (bez klucza)
    logs/collector.jsonl  jedna linia na przebieg

Zasady (z notatek projektu): archiwum jest tylko dopisywane; każdy kurs ma czas
pobrania i czas aktualizacji dostawcy; brak danych zostaje brakiem; skrypt
zatrzymuje się, gdy dzienny limit spadnie do rezerwy (domyślnie 1500).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

API_BASE = "https://v3.football.api-sports.io"
COLLECTOR_VERSION = "mk2-odds-collector-v1"

# Ligi z MK2-KONCEPT §2: czołowe ligi krajowe + trzy puchary UEFA.
# Identyfikatory API-Football (takie same jak w app/src/model_buk/catalog.py).
DEFAULT_LEAGUES: dict[int, str] = {
    39: "Premier League",
    40: "Championship",
    140: "LaLiga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    94: "Primeira Liga",
    88: "Eredivisie",
    144: "Belgian Pro League",
    203: "Super Lig",
    179: "Scottish Premiership",
    106: "Ekstraklasa",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Conference League",
}

UPCOMING_STATUSES = ("NS", "TBD")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def default_data_dir() -> Path:
    return Path.home() / "Documents" / "Model-Buk-MK2-Data"


# --------------------------------------------------------------------------- API


class ApiError(RuntimeError):
    """Dostawca zwrócił błąd albo nieprawidłową odpowiedź."""


class BudgetExhausted(RuntimeError):
    """Dzienny limit zapytań spadł do rezerwy."""


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (stały, znany host)


@dataclass
class ApiClient:
    key: str
    reserve: int = 1500
    base: str = API_BASE
    timeout: float = 30.0
    opener: Opener = _default_opener
    sleep: Callable[[float], None] = time.sleep
    requests_made: int = 0
    remaining_day: int | None = None
    limit_day: int | None = None
    remaining_minute: int | None = None

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.key:
            raise ApiError("brak klucza API")
        if self.remaining_day is not None and self.remaining_day <= self.reserve:
            raise BudgetExhausted(
                f"zostało {self.remaining_day} zapytań, rezerwa {self.reserve}"
            )
        if self.remaining_minute is not None and self.remaining_minute <= 2:
            self.sleep(61)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        url = self.base + endpoint
        if clean:
            url += "?" + urllib.parse.urlencode(sorted(clean.items()))
        request = urllib.request.Request(
            url, headers={"x-apisports-key": self.key, "Accept": "application/json"}
        )
        try:
            response = self.opener(request, self.timeout)
            body = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:  # nie wypisujemy URL z nagłówkami
            raise ApiError(f"HTTP {exc.code} dla {endpoint}") from None
        except urllib.error.URLError as exc:
            raise ApiError(f"brak połączenia z API ({exc.reason})") from None
        self.requests_made += 1
        self._read_limits(headers)
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            raise ApiError(f"odpowiedź {endpoint} nie jest JSON") from None
        errors = payload.get("errors")
        if errors:
            # errors bywa listą albo słownikiem; treść nie zawiera klucza
            raise ApiError(f"{endpoint}: {json.dumps(errors, ensure_ascii=False)[:300]}")
        return payload

    def _read_limits(self, headers: dict[str, str]) -> None:
        def as_int(name: str) -> int | None:
            try:
                return int(headers[name])
            except (KeyError, TypeError, ValueError):
                return None

        day = as_int("x-ratelimit-requests-remaining")
        if day is not None:
            self.remaining_day = day
        limit = as_int("x-ratelimit-requests-limit")
        if limit is not None:
            self.limit_day = limit
        minute = as_int("x-ratelimit-remaining")
        if minute is not None:
            self.remaining_minute = minute


# ----------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    collector_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    message TEXT,
    requests INTEGER,
    remaining_day INTEGER,
    fixtures_upserted INTEGER,
    boards_seen INTEGER,
    quotes_new INTEGER
);
CREATE TABLE IF NOT EXISTS league_seasons (
    league_id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id INTEGER PRIMARY KEY,
    league_id INTEGER,
    league_name TEXT,
    season INTEGER,
    kickoff_utc TEXT,
    status TEXT,
    home_id INTEGER,
    home TEXT,
    away_id INTEGER,
    away TEXT,
    goals_home INTEGER,
    goals_away INTEGER,
    ht_home INTEGER,
    ht_away INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fixtures_kickoff ON fixtures(kickoff_utc);
-- jeden wiersz na każdą tablicę kursów meczu, którą widzieliśmy (świeżość)
CREATE TABLE IF NOT EXISTS odds_seen (
    fixture_id INTEGER NOT NULL,
    api_update TEXT,
    fetched_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    bookmakers INTEGER
);
CREATE INDEX IF NOT EXISTS odds_seen_fixture ON odds_seen(fixture_id, fetched_at);
-- kurs zapisujemy raz na wersję tablicy dostawcy (api_update)
CREATE TABLE IF NOT EXISTS quotes (
    fixture_id INTEGER NOT NULL,
    bookmaker_id INTEGER NOT NULL,
    bookmaker TEXT,
    bet_id INTEGER NOT NULL,
    bet TEXT,
    selection TEXT NOT NULL,
    odd REAL NOT NULL,
    api_update TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    UNIQUE (fixture_id, bookmaker_id, bet_id, selection, api_update)
);
CREATE INDEX IF NOT EXISTS quotes_fixture ON quotes(fixture_id, bet_id);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # runs
    def start_run(self, mode: str, now: datetime) -> str:
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.conn.execute(
            "INSERT INTO runs(run_id, mode, collector_version, started_at) VALUES (?,?,?,?)",
            (run_id, mode, COLLECTOR_VERSION, iso(now)),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, stats: "RunStats", status: str, message: str | None) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at=?, status=?, message=?, requests=?, remaining_day=?,
               fixtures_upserted=?, boards_seen=?, quotes_new=? WHERE run_id=?""",
            (
                iso(utc_now()),
                status,
                message,
                stats.requests,
                stats.remaining_day,
                stats.fixtures_upserted,
                stats.boards_seen,
                stats.quotes_new,
                run_id,
            ),
        )
        self.conn.commit()

    # seasons
    def cached_season(self, league_id: int, now: datetime, max_age_h: float = 24) -> int | None:
        row = self.conn.execute(
            "SELECT season, fetched_at FROM league_seasons WHERE league_id=?", (league_id,)
        ).fetchone()
        if not row:
            return None
        fetched = parse_ts(row[1])
        if fetched is None or now - fetched > timedelta(hours=max_age_h):
            return None
        return int(row[0])

    def save_season(self, league_id: int, season: int, now: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO league_seasons(league_id, season, fetched_at) VALUES (?,?,?)",
            (league_id, season, iso(now)),
        )

    # fixtures
    def upsert_fixture(self, row: dict[str, Any], now: datetime) -> bool:
        fixture = row.get("fixture") or {}
        fid = fixture.get("id")
        if fid is None:
            return False
        league = row.get("league") or {}
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}
        ht = (row.get("score") or {}).get("halftime") or {}
        kickoff = parse_ts(fixture.get("date"))
        self.conn.execute(
            """INSERT INTO fixtures(fixture_id, league_id, league_name, season, kickoff_utc, status,
                   home_id, home, away_id, away, goals_home, goals_away, ht_home, ht_away, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(fixture_id) DO UPDATE SET
                   league_id=excluded.league_id, league_name=excluded.league_name,
                   season=excluded.season, kickoff_utc=excluded.kickoff_utc,
                   status=excluded.status, home_id=excluded.home_id, home=excluded.home,
                   away_id=excluded.away_id, away=excluded.away,
                   goals_home=excluded.goals_home, goals_away=excluded.goals_away,
                   ht_home=excluded.ht_home, ht_away=excluded.ht_away,
                   updated_at=excluded.updated_at""",
            (
                int(fid),
                league.get("id"),
                league.get("name"),
                league.get("season"),
                iso(kickoff) if kickoff else None,
                (fixture.get("status") or {}).get("short"),
                (teams.get("home") or {}).get("id"),
                (teams.get("home") or {}).get("name"),
                (teams.get("away") or {}).get("id"),
                (teams.get("away") or {}).get("name"),
                goals.get("home"),
                goals.get("away"),
                ht.get("home"),
                ht.get("away"),
                iso(now),
            ),
        )
        return True

    def ensure_fixture_stub(self, odds_row: dict[str, Any], now: datetime) -> None:
        """Mecz znany tylko z kursów: zapisz minimum, nie nadpisuj pełnego wiersza."""
        fixture = odds_row.get("fixture") or {}
        league = odds_row.get("league") or {}
        fid = fixture.get("id")
        if fid is None:
            return
        kickoff = parse_ts(fixture.get("date"))
        self.conn.execute(
            """INSERT OR IGNORE INTO fixtures(fixture_id, league_id, league_name, season,
                   kickoff_utc, updated_at) VALUES (?,?,?,?,?,?)""",
            (
                int(fid),
                league.get("id"),
                league.get("name"),
                league.get("season"),
                iso(kickoff) if kickoff else None,
                iso(now),
            ),
        )

    def upcoming_fixtures(self, now: datetime, window_min: int, leagues: Iterable[int]) -> list[int]:
        league_list = list(leagues)
        marks = ",".join("?" for _ in league_list)
        rows = self.conn.execute(
            f"""SELECT fixture_id FROM fixtures
                WHERE kickoff_utc > ? AND kickoff_utc <= ?
                  AND (status IS NULL OR status IN ('NS','TBD'))
                  AND league_id IN ({marks})
                ORDER BY kickoff_utc""",
            (iso(now), iso(now + timedelta(minutes=window_min)), *league_list),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # odds
    def save_odds_board(self, odds_row: dict[str, Any], run_id: str, mode: str, now: datetime) -> int:
        fixture = odds_row.get("fixture") or {}
        fid = fixture.get("id")
        if fid is None:
            return 0
        update = odds_row.get("update")
        update_ts = parse_ts(update)
        update_key = iso(update_ts) if update_ts else (str(update) if update else iso(now))
        bookmakers = odds_row.get("bookmakers") or []
        self.conn.execute(
            "INSERT INTO odds_seen(fixture_id, api_update, fetched_at, run_id, mode, bookmakers) "
            "VALUES (?,?,?,?,?,?)",
            (int(fid), update_key, iso(now), run_id, mode, len(bookmakers)),
        )
        new = 0
        for bookmaker in bookmakers:
            bid = bookmaker.get("id")
            if bid is None:
                continue
            for bet in bookmaker.get("bets") or []:
                bet_id = bet.get("id")
                if bet_id is None:
                    continue
                for value in bet.get("values") or []:
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue  # brak kursu zostaje brakiem
                    if not odd > 1.0:
                        continue
                    selection = str(value.get("value"))
                    cur = self.conn.execute(
                        """INSERT OR IGNORE INTO quotes(fixture_id, bookmaker_id, bookmaker, bet_id,
                               bet, selection, odd, api_update, first_seen_at, run_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            int(fid),
                            int(bid),
                            bookmaker.get("name"),
                            int(bet_id),
                            bet.get("name"),
                            selection,
                            odd,
                            update_key,
                            iso(now),
                            run_id,
                        ),
                    )
                    new += cur.rowcount
        return new

    def commit(self) -> None:
        self.conn.commit()


def archive_raw(data_dir: Path, endpoint: str, params: dict[str, Any], payload: Any, now: datetime) -> Path:
    safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
    tag = "_".join(f"{k}-{params[k]}" for k in sorted(params)) or "all"
    folder = data_dir / "raw" / safe_endpoint / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%H%M%S')}_{tag}.json.gz"
    record = {"fetched_at": iso(now), "endpoint": endpoint, "params": params, "payload": payload}
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    return path


# -------------------------------------------------------------------- collector


@dataclass
class RunStats:
    requests: int = 0
    remaining_day: int | None = None
    fixtures_upserted: int = 0
    boards_seen: int = 0
    quotes_new: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class Collector:
    api: ApiClient
    store: Store
    data_dir: Path
    leagues: dict[int, str]
    run_id: str = ""
    mode: str = ""
    keep_raw: bool = True
    now: Callable[[], datetime] = utc_now
    stats: RunStats = field(default_factory=RunStats)

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = self.api.get(endpoint, params)
        self.stats.requests = self.api.requests_made
        self.stats.remaining_day = self.api.remaining_day
        if self.keep_raw:
            archive_raw(self.data_dir, endpoint, params, payload, self.now())
        return payload

    def collect_fixtures(self, days_back: int = 1, days_ahead: int = 3) -> None:
        today = self.now().date()
        wanted = set(self.leagues)
        for offset in range(-days_back, days_ahead + 1):
            day = today + timedelta(days=offset)
            payload = self._get("/fixtures", {"date": day.isoformat(), "timezone": "UTC"})
            for row in payload.get("response") or []:
                league_id = (row.get("league") or {}).get("id")
                if league_id in wanted and self.store.upsert_fixture(row, self.now()):
                    self.stats.fixtures_upserted += 1
            self.store.commit()

    def season_for(self, league_id: int) -> int | None:
        now = self.now()
        cached = self.store.cached_season(league_id, now)
        if cached is not None:
            return cached
        payload = self._get("/leagues", {"id": league_id, "current": "true"})
        for row in payload.get("response") or []:
            for season in row.get("seasons") or []:
                if season.get("current") and season.get("year") is not None:
                    year = int(season["year"])
                    self.store.save_season(league_id, year, now)
                    self.store.commit()
                    return year
        self.stats.warnings.append(f"liga {league_id}: brak bieżącego sezonu")
        return None

    def collect_odds(self, max_pages_per_league: int = 20) -> None:
        for league_id in self.leagues:
            season = self.season_for(league_id)
            if season is None:
                continue
            page, total = 1, 1
            while page <= total and page <= max_pages_per_league:
                payload = self._get("/odds", {"league": league_id, "season": season, "page": page})
                self._store_odds_payload(payload)
                paging = payload.get("paging") or {}
                try:
                    total = int(paging.get("total") or 1)
                except (TypeError, ValueError):
                    total = 1
                page += 1
            if total > max_pages_per_league:
                self.stats.warnings.append(
                    f"liga {league_id}: {total} stron kursów, pobrano {max_pages_per_league}"
                )

    def collect_closing(self, window_min: int = 120, max_fixtures: int = 40) -> None:
        fixture_ids = self.store.upcoming_fixtures(self.now(), window_min, self.leagues)
        if len(fixture_ids) > max_fixtures:
            self.stats.warnings.append(
                f"{len(fixture_ids)} meczów w oknie zamknięcia, pobrano {max_fixtures} najbliższych"
            )
        for fid in fixture_ids[:max_fixtures]:
            payload = self._get("/odds", {"fixture": fid})
            self._store_odds_payload(payload)

    def _store_odds_payload(self, payload: dict[str, Any]) -> None:
        now = self.now()
        for row in payload.get("response") or []:
            self.store.ensure_fixture_stub(row, now)
            self.stats.quotes_new += self.store.save_odds_board(row, self.run_id, self.mode, now)
            self.stats.boards_seen += 1
        self.store.commit()


# --------------------------------------------------------------------------- CLI


def parse_leagues(text: str | None) -> dict[int, str]:
    if not text:
        return dict(DEFAULT_LEAGUES)
    result: dict[int, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        league_id = int(part)
        result[league_id] = DEFAULT_LEAGUES.get(league_id, f"liga {league_id}")
    return result


def write_log(data_dir: Path, record: dict[str, Any]) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "collector.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_status(api: ApiClient) -> int:
    payload = api.get("/status")
    response = payload.get("response") or {}
    if isinstance(response, list):  # przy błędzie dostawca zwraca pustą listę
        response = response[0] if response else {}
    sub = response.get("subscription") or {}
    req = response.get("requests") or {}
    print(f"plan: {sub.get('plan')}  aktywny: {sub.get('active')}  koniec: {sub.get('end')}")
    print(f"zapytania dziś: {req.get('current')} / {req.get('limit_day')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MK2: kolektor kursów API-Football")
    parser.add_argument("mode", choices=["status", "fixtures", "odds", "closing", "all"])
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--leagues", help="identyfikatory lig po przecinku (domyślnie 15 lig MK2)")
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--days-ahead", type=int, default=3)
    parser.add_argument("--reserve", type=int, default=1500, help="nie schodź poniżej tylu zapytań dziennie")
    parser.add_argument("--closing-window-min", type=int, default=120)
    parser.add_argument("--closing-max", type=int, default=40)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--no-raw", action="store_true", help="nie zapisuj surowych odpowiedzi")
    parser.add_argument("--key-env", default="API_FOOTBALL_KEY")
    args = parser.parse_args(argv)

    key = (os.environ.get(args.key_env) or "").strip()
    if not key:
        print(
            f"Brak klucza: ustaw zmienną {args.key_env} (Windows: setx {args.key_env} \"...\", "
            "potem nowe okno PowerShell).",
            file=sys.stderr,
        )
        return 2

    api = ApiClient(key=key, reserve=args.reserve)
    if args.mode == "status":
        try:
            return run_status(api)
        except ApiError as exc:
            print(f"Błąd API: {exc}", file=sys.stderr)
            return 1

    store = Store(args.data_dir / "collector.sqlite")
    started = utc_now()
    collector = Collector(
        api=api,
        store=store,
        data_dir=args.data_dir,
        leagues=parse_leagues(args.leagues),
        mode=args.mode,
        keep_raw=not args.no_raw,
    )
    collector.run_id = store.start_run(args.mode, started)
    status, message, code = "ok", None, 0
    try:
        if args.mode in ("fixtures", "all"):
            collector.collect_fixtures(args.days_back, args.days_ahead)
        if args.mode in ("odds", "all"):
            collector.collect_odds(args.max_pages)
        if args.mode == "closing":
            collector.collect_closing(args.closing_window_min, args.closing_max)
    except BudgetExhausted as exc:
        status, message, code = "budget", str(exc), 3
    except ApiError as exc:
        status, message, code = "error", str(exc), 1
    finally:
        stats = collector.stats
        if stats.warnings and message is None:
            message = "; ".join(stats.warnings)[:1000]
        store.finish_run(collector.run_id, stats, status, message)
        store.close()
        record = {
            "run_id": collector.run_id,
            "mode": args.mode,
            "started_at": iso(started),
            "finished_at": iso(utc_now()),
            "status": status,
            "message": message,
            "requests": stats.requests,
            "remaining_day": stats.remaining_day,
            "fixtures_upserted": stats.fixtures_upserted,
            "boards_seen": stats.boards_seen,
            "quotes_new": stats.quotes_new,
            "warnings": stats.warnings,
        }
        write_log(args.data_dir, record)
        print(json.dumps(record, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
