from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone

from model_buk.ingestion.api_football_history import RequestBudget
from model_buk.ingestion.api_football_odds_archive import (
    OddsArchive,
    OddsCollector,
    scan_dates,
)

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
TRACKED = 39
UNTRACKED = 999


def odds_object(fixture_id, *, league_id=TRACKED, kickoff=NOW + timedelta(hours=6), home_odd=2.10, bookmaker_id=4):
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": league_id, "season": 2026},
        "update": "2026-10-03T08:00:00+00:00",
        "bookmakers": [
            {
                "id": bookmaker_id,
                "name": "Pinnacle",
                "bets": [
                    {
                        "id": 1,
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": str(home_odd)},
                            {"value": "Draw", "odd": "3.40"},
                            {"value": "Away", "odd": "3.60"},
                        ],
                    }
                ],
            }
        ],
    }


class FakeClient:
    """Serves pre-built pages per date; counts calls; decrements a quota header."""

    def __init__(self, pages_by_date, quota_start=1000):
        self.pages_by_date = pages_by_date
        self.quota = quota_start
        self.calls = []

    def get(self, endpoint, params):
        assert endpoint == "/odds"
        self.calls.append(dict(params))
        self.quota -= 1
        pages = self.pages_by_date.get(params["date"], [[]])
        response = pages[params["page"] - 1]
        payload = {"paging": {"current": params["page"], "total": len(pages)}, "response": response}
        return payload, {"x-ratelimit-requests-remaining": str(self.quota)}


def run_scan(archive, pages_by_date, now=NOW, budget=None, dates=None):
    client = FakeClient(pages_by_date)
    collector = OddsCollector(client, archive, budget or RequestBudget(max_requests=100), league_ids={TRACKED})
    report = collector.run(now, dates or [now.date()])
    return report, client


def test_pages_through_all_pages_and_filters_leagues(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    pages = {day: [[odds_object(1), odds_object(2, league_id=UNTRACKED)], [odds_object(3)]]}
    report, client = run_scan(archive, pages)

    assert [c["page"] for c in client.calls] == [1, 2]
    assert report.fixtures_seen == 2  # fixture 2 is in an untracked league
    assert report.quotes_changed == 6  # 2 fixtures x 3 selections
    assert archive.summary()["fixtures"] == 2


def test_unchanged_prices_are_not_duplicated_but_observations_are(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    run_scan(archive, {day: [[odds_object(1)]]})
    report2, _ = run_scan(archive, {day: [[odds_object(1)]]}, now=NOW + timedelta(hours=1))

    assert report2.quotes_changed == 0
    summary = archive.summary()
    assert summary["quotes"] == 3
    assert summary["observations"] == 2  # the board was seen twice
    assert summary["scans"] == 2


def test_price_move_adds_a_row_and_as_of_query_is_point_in_time(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    later = NOW + timedelta(hours=1)
    run_scan(archive, {day: [[odds_object(1, home_odd=2.10)]]})
    report2, _ = run_scan(archive, {day: [[odds_object(1, home_odd=2.25)]]}, now=later)
    assert report2.quotes_changed == 1

    def home(quotes):
        return next(q["odd"] for q in quotes if q["selection"] == "Home")

    # Between the two scans only the first price was known.
    assert home(archive.quotes_as_of(1, NOW + timedelta(minutes=30))) == 2.10
    assert home(archive.quotes_as_of(1, later)) == 2.25
    # Before the first scan nothing was known at all.
    assert archive.quotes_as_of(1, NOW - timedelta(minutes=1)) == []


def test_fixtures_already_kicked_off_are_never_archived(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    started = odds_object(1, kickoff=NOW - timedelta(minutes=5))
    at_kickoff = odds_object(2, kickoff=NOW)
    upcoming = odds_object(3)
    report, _ = run_scan(archive, {day: [[started, at_kickoff, upcoming]]})

    assert report.fixtures_seen == 1
    assert archive.quotes_as_of(1, NOW + timedelta(days=1)) == []
    assert archive.quotes_as_of(2, NOW + timedelta(days=1)) == []


def test_budget_stops_scan_and_records_reason(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    pages = {day: [[odds_object(1)], [odds_object(2)], [odds_object(3)]]}
    report, client = run_scan(archive, pages, budget=RequestBudget(max_requests=2))

    assert report.stopped_reason == "budget_exhausted"
    assert len(client.calls) == 2
    # What was fetched before the stop is still archived.
    assert report.fixtures_seen == 2


def test_raw_snapshot_is_written_once_with_fetch_time(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    run_scan(archive, {day: [[odds_object(1)]]})

    files = list((tmp_path / "raw" / day).glob("*.jsonl.gz"))
    assert len(files) == 1
    lines = gzip.decompress(files[0].read_bytes()).decode("utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["fetched_at"] == NOW.isoformat(timespec="seconds")
    assert first["odds"]["fixture"]["id"] == 1


def test_scan_with_nothing_tracked_writes_no_raw_file_but_logs_the_scan(tmp_path):
    archive = OddsArchive(tmp_path)
    day = NOW.date().isoformat()
    report, _ = run_scan(archive, {day: [[odds_object(1, league_id=UNTRACKED)]]})

    assert report.fixtures_seen == 0
    assert not (tmp_path / "raw").exists()
    assert archive.summary()["scans"] == 1


def test_scan_dates_covers_today_through_horizon():
    assert scan_dates(NOW, days_ahead=2) == [date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)]
    assert len(scan_dates(NOW, days_ahead=2, wide_days=4)) == 7
