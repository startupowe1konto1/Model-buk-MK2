"""Testy kolektora MK2 na udawanym API (bez sieci, tylko biblioteka standardowa)."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mk2_odds_collector.py"
spec = importlib.util.spec_from_file_location("mk2_odds_collector", SCRIPT)
mk2 = importlib.util.module_from_spec(spec)
sys.modules["mk2_odds_collector"] = mk2
spec.loader.exec_module(mk2)

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
KEY = "test-key-should-never-be-written"


class FakeResponse:
    def __init__(self, payload, remaining=7000, minute=290):
        self._body = json.dumps(payload).encode()
        self.headers = {
            "x-ratelimit-requests-remaining": str(remaining),
            "x-ratelimit-requests-limit": "7500",
            "X-RateLimit-Remaining": str(minute),
        }

    def read(self):
        return self._body


class FakeApi:
    """Zwraca odpowiedzi według endpointu i parametrów, liczy zapytania."""

    def __init__(self, routes, remaining=7000):
        self.routes = routes
        self.calls = []
        self.remaining = remaining

    def __call__(self, request, timeout):
        url = urllib.parse.urlparse(request.full_url)
        params = dict(urllib.parse.parse_qsl(url.query))
        assert request.get_header("X-apisports-key") == KEY
        self.calls.append((url.path, params))
        self.remaining -= 1
        handler = self.routes[url.path]
        return FakeResponse(handler(params), remaining=self.remaining)


def odds_row(fid, update, home_odd, league=39):
    return {
        "league": {"id": league, "name": "Premier League", "season": 2026},
        "fixture": {"id": fid, "date": "2026-10-18T14:00:00+00:00"},
        "update": update,
        "bookmakers": [
            {
                "id": 4,
                "name": "Pinnacle",
                "bets": [
                    {
                        "id": 1,
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": str(home_odd)},
                            {"value": "Draw", "odd": "3.60"},
                            {"value": "Away", "odd": "4.10"},
                            {"value": "Broken", "odd": None},
                        ],
                    }
                ],
            }
        ],
    }


def fixture_row(fid, league, kickoff, status="NS"):
    return {
        "fixture": {"id": fid, "date": kickoff, "status": {"short": status}},
        "league": {"id": league, "name": "L", "season": 2026},
        "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
        "goals": {"home": None, "away": None},
        "score": {"halftime": {"home": None, "away": None}},
    }


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.store = mk2.Store(self.data_dir / "collector.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make(self, routes, leagues=None, remaining=7000, reserve=1500):
        fake = FakeApi(routes, remaining=remaining)
        api = mk2.ApiClient(key=KEY, reserve=reserve, opener=fake, sleep=lambda s: None)
        collector = mk2.Collector(
            api=api,
            store=self.store,
            data_dir=self.data_dir,
            leagues=leagues or {39: "Premier League"},
            run_id="run-1",
            mode="test",
            now=lambda: NOW,
        )
        return collector, fake

    def test_odds_pages_and_dedup_by_provider_update(self):
        pages = {
            "1": {"paging": {"current": 1, "total": 2}, "response": [odds_row(100, "2026-10-14T08:00:00+00:00", 1.95)]},
            "2": {"paging": {"current": 2, "total": 2}, "response": [odds_row(101, "2026-10-14T08:00:00+00:00", 2.10)]},
        }
        routes = {
            "/leagues": lambda p: {"response": [{"seasons": [{"year": 2025}, {"year": 2026, "current": True}]}]},
            "/odds": lambda p: pages[p["page"]],
        }
        collector, fake = self.make(routes)
        collector.collect_odds()
        # 1 sezon + 2 strony
        self.assertEqual([c[0] for c in fake.calls], ["/leagues", "/odds", "/odds"])
        self.assertEqual(fake.calls[1][1], {"league": "39", "season": "2026", "page": "1"})
        # 3 poprawne kursy na mecz, "Broken" bez kursu pominięty (nie zero)
        self.assertEqual(collector.stats.quotes_new, 6)
        self.assertEqual(collector.stats.boards_seen, 2)

        # ten sam przebieg drugi raz: sezon z pamięci, kursy bez duplikatów,
        # ale obserwacja tablicy zapisana (świeżość)
        collector.stats = mk2.RunStats()
        collector.collect_odds()
        self.assertEqual([c[0] for c in fake.calls[3:]], ["/odds", "/odds"])
        self.assertEqual(collector.stats.quotes_new, 0)
        seen = self.store.conn.execute("SELECT COUNT(*) FROM odds_seen").fetchone()[0]
        self.assertEqual(seen, 4)

        # nowa wersja tablicy dostawcy z innym kursem = nowe wiersze
        pages["1"] = {"paging": {"current": 1, "total": 1}, "response": [odds_row(100, "2026-10-14T11:00:00+00:00", 1.90)]}
        collector.stats = mk2.RunStats()
        collector.collect_odds()
        self.assertEqual(collector.stats.quotes_new, 3)
        odds = [r[0] for r in self.store.conn.execute(
            "SELECT odd FROM quotes WHERE fixture_id=100 AND selection='Home' ORDER BY api_update")]
        self.assertEqual(odds, [1.95, 1.90])

    def test_fixtures_filtered_to_selected_leagues(self):
        def fixtures(params):
            return {"response": [
                fixture_row(1, 39, f"{params['date']}T15:00:00+00:00"),
                fixture_row(2, 999, f"{params['date']}T15:00:00+00:00"),
            ]}

        collector, fake = self.make({"/fixtures": fixtures})
        collector.collect_fixtures(days_back=1, days_ahead=2)
        self.assertEqual(len(fake.calls), 4)  # wczoraj, dziś, +1, +2
        self.assertEqual(fake.calls[0][1], {"date": "2026-10-13", "timezone": "UTC"})
        leagues = {r[0] for r in self.store.conn.execute("SELECT league_id FROM fixtures")}
        self.assertEqual(leagues, {39})

    def test_closing_only_upcoming_in_window(self):
        def kick(minutes):
            return (NOW + timedelta(minutes=minutes)).isoformat()

        rows = [
            fixture_row(10, 39, kick(30)),
            fixture_row(11, 39, kick(200)),          # poza oknem
            fixture_row(12, 39, kick(-30), "1H"),     # już trwa
            fixture_row(13, 999, kick(20)),           # inna liga
        ]
        for row in rows:
            self.store.upsert_fixture(row, NOW)
        self.store.commit()
        collector, fake = self.make({"/odds": lambda p: {"response": [odds_row(int(p["fixture"]), NOW.isoformat(), 1.8)]}})
        collector.collect_closing(window_min=120, max_fixtures=40)
        self.assertEqual(fake.calls, [("/odds", {"fixture": "10"})])

    def test_stops_at_reserve(self):
        routes = {
            "/leagues": lambda p: {"response": [{"seasons": [{"year": 2026, "current": True}]}]},
            "/odds": lambda p: {"paging": {"total": 50}, "response": []},
        }
        collector, fake = self.make(routes, remaining=1503, reserve=1500)
        with self.assertRaises(mk2.BudgetExhausted):
            collector.collect_odds()
        self.assertLessEqual(fake.remaining, 1500)
        self.assertEqual(len(fake.calls), 3)

    def test_provider_errors_raise_without_key(self):
        collector, _ = self.make({"/odds": lambda p: {"errors": {"plan": "nie w planie"}, "response": []},
                                  "/leagues": lambda p: {"response": [{"seasons": [{"year": 2026, "current": True}]}]}})
        with self.assertRaises(mk2.ApiError) as ctx:
            collector.collect_odds()
        self.assertNotIn(KEY, str(ctx.exception))

    def test_raw_archive_never_contains_key(self):
        routes = {"/fixtures": lambda p: {"response": [fixture_row(1, 39, "2026-10-14T15:00:00+00:00")]}}
        collector, _ = self.make(routes)
        collector.collect_fixtures(days_back=0, days_ahead=0)
        files = list((self.data_dir / "raw").rglob("*.json.gz"))
        self.assertEqual(len(files), 1)
        text = gzip.open(files[0], "rt", encoding="utf-8").read()
        self.assertNotIn(KEY, text)
        self.assertIn('"fetched_at"', text)

    def test_cli_without_key_exits_cleanly(self):
        code = mk2.main(["odds", "--data-dir", str(self.data_dir), "--key-env", "MK2_TEST_MISSING_KEY"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
