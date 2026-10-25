"""Fetch the finished matches the read-only snapshot does not have yet (with their team statistics).

The snapshot's history (``<data>/api-football/tables/history.sqlite``) stops around 2026-09-20.
For the leagues and cups of the statistics model this lists every finished fixture between
``--since`` and today and downloads the ones whose team statistics are missing from the
snapshot, 20 fixtures per request (``/fixtures?ids=``, which returns the statistics block).
Raw responses are kept gzip-compressed and the parsed rows go to ``<mk2>/stats/gap.sqlite``
(tables ``matches`` and ``team_match_stats``, same columns as the snapshot). Rerunning only
fetches what is still missing.

The API quota is shared with the collectors on Oskar's server: the run stops when the
account's remaining daily requests fall to ``--reserve`` or this run has used
``--max-requests``. The key comes from the Windows user environment and is never printed.

Usage (from app/):
    .venv\\Scripts\\python scripts\\mk2\\stats_gap.py --since 2026-09-14
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "src"))

from model_buk.ingestion.api_football_tables import (  # noqa: E402
    MATCH_COLUMNS,
    TEAM_MATCH_STATS_COLUMNS,
    parse_match,
    parse_team_match_stats,
)

BASE_URL = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}
DATA = Path(os.environ.get("MODEL_BUK_DATA") or Path.home() / "Documents" / "Model-Buk-Data")
OUT = Path(os.environ.get("MODEL_BUK_MK2_OUT") or Path.home() / "Documents" / "Model-Buk-Data-MK2")

# API-Football league ids of the statistics model: European top flights and the three UEFA cups.
LEAGUES: dict[int, str] = {
    39: "EPL", 140: "LALIGA", 135: "SERIEA", 78: "BUNDESLIGA", 61: "LIGUE1", 94: "PRIMEIRA",
    88: "EREDIVISIE", 144: "BELGIUM", 203: "SUPERLIG", 197: "GREECE", 345: "CZECHLIGA",
    106: "EKSTRAKLASA", 179: "SCOTLAND", 218: "AUSTRIA", 207: "SWITZERLAND", 119: "DENMARK",
    103: "ELITESERIEN", 113: "ALLSVENSKAN", 210: "HNL", 286: "SUPERLIGA", 333: "PREMIERLEAGUE3",
    332: "SUPERLIGA2", 2: "UCL", 3: "UEL", 848: "UECL",
}


def api_key() -> str:
    """The user-level key (registry) wins over a stale copy in this process' environment; elsewhere than on
    Windows (the cloud run) the key comes from the environment alone."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value = winreg.QueryValueEx(k, "API_FOOTBALL_KEY")[0].strip()
            if value:
                return value
    except (ImportError, OSError):
        pass
    value = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not value:
        raise SystemExit("API_FOOTBALL_KEY is not set")
    return value


class Client:
    def __init__(self, key: str, max_requests: int, reserve: int):
        self.session = requests.Session()
        self.session.headers["x-apisports-key"] = key
        self.max_requests = max_requests
        self.reserve = reserve
        self.used = 0
        self.remaining: int | None = None

    def get(self, endpoint: str, params: dict) -> dict:
        if self.used >= self.max_requests:
            raise RuntimeError(f"stopped: this run already used {self.used} requests")
        if self.remaining is not None and self.remaining <= self.reserve:
            raise RuntimeError(f"stopped: account has {self.remaining} requests left today (reserve {self.reserve})")
        for attempt in range(3):
            try:
                r = self.session.get(BASE_URL + endpoint, params=params, timeout=60)
                self.used += 1
                left = r.headers.get("x-ratelimit-requests-remaining")
                if left is not None and left.isdigit():
                    self.remaining = int(left)
                r.raise_for_status()
                data = r.json()
                if data.get("errors"):
                    raise RuntimeError(f"API errors: {data['errors']}")
                time.sleep(0.25)
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")


def snapshot_with_stats(fixture_ids: list[int]) -> set[int]:
    path = DATA / "api-football" / "tables" / "history.sqlite"
    con = sqlite3.connect("file:" + path.as_posix() + "?mode=ro&immutable=1", uri=True)
    have: set[int] = set()
    for i in range(0, len(fixture_ids), 500):
        chunk = fixture_ids[i:i + 500]
        q = f"select distinct fixture_id from team_match_stats where fixture_id in ({','.join('?' * len(chunk))})"
        have.update(r[0] for r in con.execute(q, chunk))
    con.close()
    return have


def open_gap_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(f"create table if not exists matches ({', '.join(MATCH_COLUMNS)}, primary key (fixture_id))")
    con.execute(f"create table if not exists team_match_stats ({', '.join(TEAM_MATCH_STATS_COLUMNS)}, "
                "primary key (fixture_id, team_id))")
    return con


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", default="2026-09-14")
    p.add_argument("--until", default=dt.date.today().isoformat())
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--max-requests", type=int, default=300)
    p.add_argument("--reserve", type=int, default=3000)
    args = p.parse_args()

    client = Client(api_key(), args.max_requests, args.reserve)
    gap_db = OUT / "stats" / "gap.sqlite"
    raw_dir = OUT / "stats" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    con = open_gap_db(gap_db)
    already = {r[0] for r in con.execute("select distinct fixture_id from team_match_stats")}

    finished: dict[int, str] = {}
    for league_id, code in LEAGUES.items():
        data = client.get("/fixtures", {"league": league_id, "season": args.season,
                                         "from": args.since, "to": args.until})
        n = 0
        for fx in data.get("response", []):
            if fx["fixture"]["status"]["short"] in FINISHED:
                finished[fx["fixture"]["id"]] = code
                n += 1
        print(f"{code}: {n} finished since {args.since}")

    ids = sorted(finished)
    have = snapshot_with_stats(ids) | already
    todo = [i for i in ids if i not in have]
    print(f"finished {len(ids)}, with stats already {len(have & set(ids))}, to fetch {len(todo)}")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for n, i in enumerate(range(0, len(todo), 20)):
        chunk = todo[i:i + 20]
        data = client.get("/fixtures", {"ids": "-".join(map(str, chunk))})
        name = f"fixtures_ids_{stamp}_{n:03d}.json.gz"
        with gzip.open(raw_dir / name, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        rows_m, rows_s = [], []
        for fx in data.get("response", []):
            fid = fx["fixture"]["id"]
            rows_m.append(parse_match(fx, league_code=finished.get(fid), fetched_at=fetched_at, batch_file=name))
            rows_s.extend(parse_team_match_stats(fx))
        con.executemany(f"insert or replace into matches values ({','.join('?' * len(MATCH_COLUMNS))})",
                        [tuple(r[c] for c in MATCH_COLUMNS) for r in rows_m])
        con.executemany(f"insert or replace into team_match_stats values ({','.join('?' * len(TEAM_MATCH_STATS_COLUMNS))})",
                        [tuple(r[c] for c in TEAM_MATCH_STATS_COLUMNS) for r in rows_s])
        con.commit()
        print(f"batch {n}: {len(rows_m)} matches, {len(rows_s)} team rows")
    print(f"requests used {client.used}, account remaining {client.remaining}")


if __name__ == "__main__":
    main()
