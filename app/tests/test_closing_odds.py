from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from model_buk.ingestion.api_football_history import RequestBudget
from model_buk.ingestion.api_football_odds_archive import OddsArchive
from model_buk.ingestion.closing_odds import (
    DENSE_CADENCE,
    Candidate,
    ClosingOddsCollector,
    closing_quotes,
    min_gap_minutes,
    plan_requests,
    select_due,
)
from model_buk.ingestion.fixture_directory import FixtureDirectory

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


def odds_object(fixture_id, kickoff, *, league_id=39, season=2026, home_odd=2.10, update="2026-10-03T14:00:00+00:00",
                bookmakers=((4, "Pinnacle"),)):
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": league_id, "season": season},
        "update": update,
        "bookmakers": [
            {"id": bid, "name": name, "bets": [{"id": 1, "name": "Match Winner", "values": [
                {"value": "Home", "odd": str(home_odd)}, {"value": "Draw", "odd": "3.40"}, {"value": "Away", "odd": "3.60"}]}]}
            for bid, name in bookmakers
        ],
    }


def listing_obj(fixture_id, kickoff, *, status="NS", league_id=39, season=2026):
    return {
        "fixture": {"id": fixture_id, "timezone": "UTC", "date": kickoff.isoformat(), "timestamp": int(kickoff.timestamp()),
                    "status": {"long": "x", "short": status, "elapsed": None, "extra": None}},
        "league": {"id": league_id, "name": "League", "country": "England", "season": season, "round": "R1"},
        "teams": {"home": {"id": fixture_id * 10 + 1, "name": "H"}, "away": {"id": fixture_id * 10 + 2, "name": "A"}},
        "goals": {"home": None, "away": None},
        "score": {},
    }


class FakeOddsClient:
    """Serves /odds by fixture id or by (league, season, date) in pages of 10; counts calls."""

    def __init__(self, objects=None, quota=5000, fail=False):
        self.objects: dict[int, dict] = dict(objects or {})
        self.quota = quota
        self.fail = fail
        self.calls: list[dict] = []

    def get(self, endpoint, params):
        assert endpoint == "/odds"
        self.calls.append(dict(params))
        if self.fail:
            raise RuntimeError("boom")
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if "fixture" in params:
            found = [self.objects[params["fixture"]]] if params["fixture"] in self.objects else []
            return {"paging": {"current": 1, "total": 1}, "response": found}, headers
        day = params["date"]
        matching = [o for o in self.objects.values()
                    if o["league"]["id"] == params["league"] and o["league"]["season"] == params["season"]
                    and o["fixture"]["date"][:10] == day]
        pages = [matching[i:i + 10] for i in range(0, len(matching), 10)] or [[]]
        page = params.get("page", 1)
        return {"paging": {"current": page, "total": len(pages)}, "response": pages[page - 1]}, headers


class Env:
    """Odds archive + fixture directory + collector wired together on tmp_path."""

    def __init__(self, tmp_path, client=None, **kwargs):
        self.archive = OddsArchive(tmp_path / "odds")
        self.directory = FixtureDirectory(tmp_path / "dir")
        self.client = client or FakeOddsClient()
        self.now = NOW
        budget = kwargs.pop("budget", None) or RequestBudget(max_requests=100)
        self.collector = ClosingOddsCollector(
            self.client, self.archive, self.directory, budget,
            clock=lambda: self.now + timedelta(seconds=5), **kwargs,
        )

    def hourly_scan(self, objects, at=None, scan_id="h1"):
        """Store objects the way the hourly collector would have (kind NULL)."""

        at = at or NOW - timedelta(hours=2)
        self.archive.start_scan(scan_id, at, [at.date().isoformat()])
        self.archive.record(scan_id, at, objects)

    def run(self, now=None):
        self.now = now or NOW
        return self.collector.run(self.now)

    def scans(self):
        with closing(sqlite3.connect(self.archive.db_path)) as conn:
            return conn.execute("SELECT scan_id, kind, requests, fixtures_seen, quotes_changed, stopped_reason "
                                "FROM scans ORDER BY started_at").fetchall()

    def table_count(self, table):
        with closing(sqlite3.connect(self.archive.db_path)) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def minutes(n):
    return NOW + timedelta(minutes=n)


# --------------------------------------------------------------------------
# Pure planning
# --------------------------------------------------------------------------


def test_gap_shrinks_as_kickoff_approaches():
    assert min_gap_minutes(10) < min_gap_minutes(45) < min_gap_minutes(120)
    assert min_gap_minutes(500) == min_gap_minutes(150)


def test_select_due_uses_the_gap_for_the_time_to_kickoff():
    def cand(fid, to_kickoff, observed_ago):
        return Candidate(fid, 39, 2026, minutes(to_kickoff), True, None if observed_ago is None else minutes(-observed_ago))

    candidates = [
        cand(1, 10, 12),   # last hour of the match window: gap 10 min -> due
        cand(2, 10, 5),    # observed 5 minutes ago -> not due
        cand(3, 45, 30),   # 45 min out: gap 25 -> due
        cand(4, 45, 20),   # not due
        cand(5, 120, 50),  # 2 h out: gap 55 -> not due
        cand(6, 120, 60),  # due
        cand(7, 120, None),  # never observed -> due
        Candidate(8, 39, 2026, minutes(30), False, None),  # unknown to the odds archive: handled as a probe
    ]
    assert [c.fixture_id for c in select_due(candidates, NOW)] == [1, 3, 6, 7]
    assert len(select_due(candidates, NOW, DENSE_CADENCE)) == 7  # dense mode: everything known, every run


def test_plan_uses_league_day_requests_only_when_cheaper():
    day = minutes(60).date().isoformat()
    four_in_one_league = [Candidate(i, 39, 2026, minutes(30 + i), True) for i in range(4)]
    single = [Candidate(9, 140, 2026, minutes(20), True)]
    big_league = [Candidate(20 + i, 78, 2026, minutes(50), True) for i in range(3)]
    plan = plan_requests(four_in_one_league + single + big_league, {(39, day): 4, (78, day): 25})

    kinds = [(dict(r.params).get("league"), dict(r.params).get("fixture"), r.estimated_requests) for r in plan]
    # league 39: 4 due fixtures, 1 page -> one league-day request; league 140: single; league 78: 25 fixtures = 3 pages
    # is not cheaper than 3 single requests -> singles
    assert (39, None, 1) in kinds and (None, 9, 1) in kinds
    assert sum(1 for k in kinds if k[1] in (20, 21, 22)) == 3
    assert sum(r.estimated_requests for r in plan) == 1 + 1 + 3
    assert [r.earliest_kickoff for r in plan] == sorted(r.earliest_kickoff for r in plan)  # nearest kickoff first
    league_day = next(r for r in plan if r.is_league_day)
    assert dict(league_day.params) == {"league": 39, "season": 2026, "date": day}
    assert league_day.fixture_ids == (0, 1, 2, 3)


def test_plan_never_groups_without_a_season():
    due = [Candidate(i, 39, None, minutes(30), True) for i in range(5)]
    plan = plan_requests(due, {(39, minutes(30).date().isoformat()): 5})
    assert len(plan) == 5 and not any(r.is_league_day for r in plan)


# --------------------------------------------------------------------------
# Window selection
# --------------------------------------------------------------------------


def test_window_selection_uses_only_fixtures_kicking_off_within_150_minutes(tmp_path):
    env = Env(tmp_path)
    env.hourly_scan([
        odds_object(1, minutes(10)),
        odds_object(2, minutes(149)),
        odds_object(3, minutes(151)),   # outside the window
        odds_object(4, minutes(-5)),    # already started
        odds_object(5, minutes(60)),
    ])
    candidates = env.collector.candidates(NOW)
    assert [c.fixture_id for c in candidates] == [1, 5, 2]
    assert all(c.in_odds_archive for c in candidates)


def test_directory_completes_and_corrects_the_window(tmp_path):
    env = Env(tmp_path)
    env.hourly_scan([odds_object(1, minutes(30)), odds_object(2, minutes(40)), odds_object(3, minutes(50))])
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(minutes=10), [
        listing_obj(1, minutes(30)),
        listing_obj(2, minutes(40), status="PST"),            # postponed after the odds were seen
        listing_obj(3, minutes(300)),                          # rescheduled far outside the window
        listing_obj(4, minutes(45)),                           # not in the odds archive yet
        listing_obj(5, minutes(45), status="TBD"),             # no reliable kickoff
        listing_obj(6, minutes(45), status="1H"),              # already running
    ])
    by_id = {c.fixture_id: c for c in env.collector.candidates(NOW)}

    assert sorted(by_id) == [1, 4]
    assert by_id[1].in_odds_archive and not by_id[4].in_odds_archive


def test_an_older_directory_record_does_not_override_the_odds_archive(tmp_path):
    env = Env(tmp_path)
    env.hourly_scan([odds_object(1, minutes(30))], at=NOW - timedelta(minutes=5))
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(hours=3), [listing_obj(1, minutes(300), status="PST")])
    assert [c.fixture_id for c in env.collector.candidates(NOW)] == [1]


# --------------------------------------------------------------------------
# Runs: quotes appended through the archive
# --------------------------------------------------------------------------


def test_run_appends_changed_quotes_and_observations_to_the_same_archive(tmp_path):
    kickoff = minutes(20)
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, kickoff, home_odd=2.30, update="2026-10-03T14:50:00+00:00")}))
    env.hourly_scan([odds_object(1, kickoff, home_odd=2.10)], at=NOW - timedelta(minutes=55))
    before = env.table_count("quotes")

    report = env.run()

    assert report.due_fixtures == 1 and report.requests == 1
    assert (report.fixtures_recorded, report.boards_changed, report.quotes_changed) == (1, 1, 1)
    assert env.table_count("quotes") == before + 1  # only the moved price
    quotes = env.archive.quotes_as_of(1, NOW + timedelta(minutes=1))
    assert next(q["odd"] for q in quotes if q["selection"] == "Home") == 2.30
    # the earlier price is still there for a point-in-time query before the run
    earlier = env.archive.quotes_as_of(1, NOW - timedelta(minutes=30))
    assert next(q["odd"] for q in earlier if q["selection"] == "Home") == 2.10
    scan_id, kind, requests, fixtures_seen, quotes_changed, stopped = env.scans()[-1]
    assert scan_id.startswith("nk-") and kind == "near_kickoff"
    assert (requests, fixtures_seen, quotes_changed, stopped) == (1, 1, 1, "completed")
    assert env.scans()[0][1] is None  # the hourly scan keeps kind NULL


def test_unchanged_prices_are_not_duplicated_but_the_observation_is_kept(tmp_path):
    kickoff = minutes(15)
    obj = odds_object(1, kickoff)
    env = Env(tmp_path, FakeOddsClient({1: obj}))
    env.hourly_scan([obj], at=NOW - timedelta(minutes=50))
    quotes_before, obs_before = env.table_count("quotes"), env.table_count("observations")

    report = env.run()

    assert report.quotes_changed == 0 and report.boards_changed == 0
    assert env.table_count("quotes") == quotes_before
    assert env.table_count("observations") == obs_before + 1  # "board seen unchanged at this time"
    day_dir = tmp_path / "odds" / "raw" / NOW.strftime("%Y-%m-%d")
    assert list(day_dir.glob("nk-*")) == []  # nothing refreshed -> no raw file


def test_raw_file_has_per_response_fetch_time_and_only_refreshed_boards(tmp_path):
    kickoff = minutes(20)
    env = Env(tmp_path, FakeOddsClient({
        1: odds_object(1, kickoff, home_odd=2.4, update="2026-10-03T14:55:00+00:00"),
        2: odds_object(2, kickoff, update="2026-10-03T14:00:00+00:00"),
    }))
    env.hourly_scan([odds_object(1, kickoff), odds_object(2, kickoff, update="2026-10-03T14:00:00+00:00")])
    env.run()

    files = sorted((tmp_path / "odds" / "raw" / NOW.strftime("%Y-%m-%d")).glob("nk-*.jsonl.gz"))
    assert len(files) == 1
    rows = [json.loads(line) for line in gzip.decompress(files[0].read_bytes()).decode().splitlines()]
    assert [r["odds"]["fixture"]["id"] for r in rows] == [1]  # fixture 2's provider update did not move
    assert rows[0]["fetched_at"] == (NOW + timedelta(seconds=5)).isoformat(timespec="seconds")


def test_fixtures_that_started_before_the_response_arrived_are_dropped(tmp_path):
    kickoff = NOW + timedelta(seconds=3)  # kicks off before the (fake) response arrives at NOW + 5 s
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, kickoff, home_odd=9.9)}))
    env.hourly_scan([odds_object(1, kickoff)], at=NOW - timedelta(minutes=50))
    quotes_before = env.table_count("quotes")

    report = env.run()

    assert report.fixtures_recorded == 0 and env.table_count("quotes") == quotes_before


def test_league_day_request_is_paged_and_records_every_fixture(tmp_path):
    objects = {i: odds_object(i, minutes(30 + i % 5)) for i in range(1, 13)}  # 12 fixtures of one league -> 2 pages
    env = Env(tmp_path, FakeOddsClient(objects))
    env.hourly_scan(list(objects.values()), at=NOW - timedelta(minutes=50))
    for obj in objects.values():
        obj["update"] = "2026-10-03T14:59:00+00:00"

    report = env.run()

    assert [c.get("page") for c in env.client.calls] == [None, 2]
    assert all("league" in c and c["season"] == 2026 for c in env.client.calls)
    assert report.requests == 2 and report.fixtures_recorded == 12 and report.boards_changed == 12


def test_single_fixture_uses_the_fixture_parameter(tmp_path):
    env = Env(tmp_path, FakeOddsClient({7: odds_object(7, minutes(12))}))
    env.hourly_scan([odds_object(7, minutes(12))], at=NOW - timedelta(minutes=45))
    env.run()
    assert env.client.calls == [{"fixture": 7}]


def test_dense_cadence_refetches_everything_in_the_window_each_run(tmp_path):
    objects = {i: odds_object(i, minutes(100 + i)) for i in (1, 2)}
    env = Env(tmp_path, FakeOddsClient(objects), cadence=DENSE_CADENCE)
    env.hourly_scan(list(objects.values()), at=NOW - timedelta(minutes=3))
    assert env.run().due_fixtures == 2


def test_recently_observed_fixtures_are_not_refetched(tmp_path):
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, minutes(100))}))
    env.hourly_scan([odds_object(1, minutes(100))], at=NOW - timedelta(minutes=20))
    report = env.run()
    assert report.due_fixtures == 0 and env.client.calls == []
    assert env.scans()[-1][1] == "near_kickoff"  # the run itself is still logged


# --------------------------------------------------------------------------
# Budget and errors
# --------------------------------------------------------------------------


def test_budget_stops_the_run_and_serves_the_nearest_kickoffs_first(tmp_path):
    objects = {i: odds_object(i, minutes(10 * i), league_id=100 + i) for i in range(1, 6)}
    env = Env(tmp_path, FakeOddsClient(objects), budget=RequestBudget(max_requests=2))
    env.hourly_scan(list(objects.values()), at=NOW - timedelta(minutes=50))

    report = env.run()

    assert report.stopped_reason == "budget_exhausted" and report.requests == 2
    assert [c["fixture"] for c in env.client.calls] == [1, 2]  # nearest kickoffs first
    assert env.scans()[-1][5] == "budget_exhausted"


def test_reserve_floor_from_remembered_quota_prevents_any_request(tmp_path):
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, minutes(10))}), budget=RequestBudget(max_requests=50, reserve=1500))
    env.hourly_scan([odds_object(1, minutes(10))], at=NOW - timedelta(minutes=50))
    env.directory.save_quota(1400, NOW - timedelta(minutes=30))  # earlier today
    report = env.run()
    assert report.stopped_reason == "budget_exhausted" and env.client.calls == []


def test_yesterdays_quota_reading_is_ignored(tmp_path):
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, minutes(10))}), budget=RequestBudget(max_requests=50, reserve=1500))
    env.hourly_scan([odds_object(1, minutes(10))], at=NOW - timedelta(minutes=50))
    env.directory.save_quota(1400, NOW - timedelta(days=1))
    assert env.run().requests == 1


def test_quota_header_is_remembered_after_a_request(tmp_path):
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, minutes(10))}, quota=4000))
    env.hourly_scan([odds_object(1, minutes(10))], at=NOW - timedelta(minutes=50))
    env.run()
    assert env.directory.load_quota(NOW) == 3999


def test_api_errors_are_reported_and_three_in_a_row_stop_the_run(tmp_path):
    objects = {i: odds_object(i, minutes(10 * i), league_id=100 + i) for i in range(1, 6)}
    env = Env(tmp_path, FakeOddsClient(objects, fail=True))
    env.hourly_scan(list(objects.values()), at=NOW - timedelta(minutes=50))
    report = env.run()

    assert report.stopped_reason == "api_error" and len(env.client.calls) == 3 and len(report.errors) == 3
    assert report.quotes_changed == 0


# --------------------------------------------------------------------------
# Unseen fixtures (probes)
# --------------------------------------------------------------------------


def known_league(env, league_id):
    """Make the odds archive know a league (an archived fixture far outside the window)."""

    env.hourly_scan([odds_object(900 + league_id, minutes(3000), league_id=league_id)], scan_id=f"known-{league_id}")


def test_unseen_fixture_with_odds_is_recorded_and_the_probe_logged(tmp_path):
    env = Env(tmp_path, FakeOddsClient({4: odds_object(4, minutes(45), league_id=200)}))
    known_league(env, 200)
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(4, minutes(45), league_id=200)])
    report = env.run()

    assert (report.unseen_probed, report.unseen_found, report.fixtures_recorded) == (1, 1, 1)
    assert env.archive.quotes_as_of(4, NOW + timedelta(minutes=1))
    assert env.directory.probe_history([4])[4][0] == 0  # found: no empty probes


def test_unseen_fixture_without_odds_is_probed_at_most_twice(tmp_path):
    env = Env(tmp_path, FakeOddsClient({}))
    known_league(env, 200)
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(4, minutes(140), league_id=200)])

    assert env.run().unseen_probed == 1
    assert env.run(NOW + timedelta(minutes=15)).unseen_probed == 0  # re-probe gap not over
    assert env.run(NOW + timedelta(minutes=50)).unseen_probed == 1
    assert env.run(NOW + timedelta(minutes=100)).unseen_probed == 0  # two empty probes: given up
    assert len(env.client.calls) == 2


def test_max_unseen_caps_probes_per_run(tmp_path):
    env = Env(tmp_path, FakeOddsClient({}), max_unseen=3)
    for i in range(10):
        known_league(env, 300 + i)
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(i, minutes(30 + i), league_id=300 + i) for i in range(10)])
    assert env.run().unseen_probed == 3


def test_leagues_the_odds_archive_never_saw_are_not_probed(tmp_path):
    env = Env(tmp_path, FakeOddsClient({}))
    known_league(env, 200)
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(1, minutes(60), league_id=200),
                                                                listing_obj(2, minutes(60), league_id=201)])
    candidates = env.collector.candidates(NOW)
    assert [c.fixture_id for c in env.collector.select_unseen(candidates, NOW)] == [1]


def test_leagues_whose_probes_keep_coming_back_empty_are_skipped(tmp_path):
    env = Env(tmp_path, FakeOddsClient({}))
    known_league(env, 555)
    known_league(env, 556)
    env.directory.log_probes(NOW - timedelta(hours=5), [(100 + i, 555, False) for i in range(4)])
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(9, minutes(60), league_id=555),
                                                                listing_obj(10, minutes(60), league_id=556)])
    candidates = env.collector.candidates(NOW)
    assert [c.fixture_id for c in env.collector.select_unseen(candidates, NOW)] == [10]


# --------------------------------------------------------------------------
# Reading the closing line
# --------------------------------------------------------------------------


def test_closing_quotes_return_the_last_price_before_kickoff_with_its_observation_time(tmp_path):
    kickoff = minutes(20)
    env = Env(tmp_path, FakeOddsClient({1: odds_object(1, kickoff, home_odd=2.30)}))
    env.hourly_scan([odds_object(1, kickoff, home_odd=2.10)], at=NOW - timedelta(minutes=55))
    env.run()  # near-kickoff observation at NOW + 5 s, kickoff in 20 min

    frame = closing_quotes(env.archive.db_path)
    home = frame[frame["selection"] == "Home"].iloc[0]
    assert home["odd"] == 2.30 and home["bookmaker"] == "Pinnacle"
    assert abs(home["minutes_observed_before_kickoff"] - (20 - 5 / 60)) < 1e-6
    assert len(frame) == 3  # Home / Draw / Away

    only_fresh = closing_quotes(env.archive.db_path, max_minutes_before_kickoff=5)
    assert only_fresh.empty
    assert closing_quotes(env.archive.db_path, fixture_ids=[999]).empty


def test_closing_quotes_ignore_anything_seen_after_kickoff(tmp_path):
    kickoff = minutes(10)
    env = Env(tmp_path)
    env.hourly_scan([odds_object(1, kickoff, home_odd=2.10)], at=NOW - timedelta(minutes=50))
    # a price that reached the archive after kickoff (e.g. from a late manual scan) must not become the "closing" price
    env.archive.start_scan("late", NOW + timedelta(minutes=30), ["x"])
    env.archive.record("late", NOW + timedelta(minutes=30), [odds_object(1, kickoff + timedelta(hours=1), home_odd=1.50)])
    with closing(sqlite3.connect(env.archive.db_path)) as conn:  # keep the fixture's true kickoff
        conn.execute("UPDATE fixtures SET kickoff_utc=? WHERE fixture_id=1", (kickoff.isoformat(timespec="seconds"),))
        conn.commit()

    frame = closing_quotes(env.archive.db_path)
    assert frame[frame["selection"] == "Home"].iloc[0]["odd"] == 2.10


def test_closing_quotes_keep_bookmakers_and_selections_apart(tmp_path):
    kickoff = minutes(90)
    env = Env(tmp_path)
    both = ((4, "Pinnacle"), (8, "Bet365"))
    env.hourly_scan([odds_object(1, kickoff, home_odd=2.10, bookmakers=both)], at=NOW - timedelta(minutes=50), scan_id="a")
    # only Pinnacle's price moves later; Bet365's closing price stays the earlier one
    env.archive.start_scan("b", NOW - timedelta(minutes=20), ["x"])
    pinnacle_only = odds_object(1, kickoff, home_odd=1.95, bookmakers=((4, "Pinnacle"),))
    env.archive.record("b", NOW - timedelta(minutes=20), [pinnacle_only])

    frame = closing_quotes(env.archive.db_path)
    home = frame[frame["selection"] == "Home"].set_index("bookmaker")
    assert home.loc["Pinnacle", "odd"] == 1.95 and home.loc["Bet365", "odd"] == 2.10
    assert home.loc["Bet365", "minutes_observed_before_kickoff"] == 90 + 50  # last board look at Bet365: the first scan
    assert home.loc["Pinnacle", "minutes_observed_before_kickoff"] == 90 + 20
    draw = frame[frame["selection"] == "Draw"].set_index("bookmaker")
    assert draw["odd"].tolist() == [3.40, 3.40] and len(frame) == 6  # 2 bookmakers x 3 selections


def test_a_probe_whose_request_failed_is_not_logged(tmp_path):
    env = Env(tmp_path, FakeOddsClient({}, fail=True))
    known_league(env, 200)
    env.directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(4, minutes(45), league_id=200)])
    report = env.run()
    assert report.unseen_probed == 1 and report.errors
    assert env.directory.probe_history([4]) == {}  # never answered: no probe outcome recorded, so it is tried again


def test_raw_snapshot_of_a_scan_is_never_overwritten(tmp_path):
    archive = OddsArchive(tmp_path)
    entries = [(NOW, odds_object(1, minutes(30)))]
    first = archive.write_raw_entries("nk-x", NOW, entries)
    assert (tmp_path / first).exists()
    with pytest.raises(FileExistsError):
        archive.write_raw_entries("nk-x", NOW, entries)


def test_closing_quotes_window_filters(tmp_path):
    env = Env(tmp_path)
    env.hourly_scan([odds_object(1, minutes(30)), odds_object(2, minutes(300))], at=NOW - timedelta(minutes=10))
    assert set(closing_quotes(env.archive.db_path, kickoff_to=minutes(60))["fixture_id"]) == {1}
    assert set(closing_quotes(env.archive.db_path, kickoff_from=minutes(60))["fixture_id"]) == {2}


# --------------------------------------------------------------------------
# Archive compatibility
# --------------------------------------------------------------------------


def test_existing_archive_without_scan_kind_is_migrated_and_stays_readable(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    with closing(sqlite3.connect(root / "odds.sqlite3")) as conn:
        conn.execute("CREATE TABLE scans (scan_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, dates TEXT NOT NULL, "
                     "requests INTEGER NOT NULL DEFAULT 0, fixtures_seen INTEGER NOT NULL DEFAULT 0, "
                     "quotes_changed INTEGER NOT NULL DEFAULT 0, stopped_reason TEXT, raw_file TEXT)")
        conn.execute("INSERT INTO scans (scan_id, started_at, dates) VALUES ('old', '2026-10-01T10:00:00+00:00', '2026-10-01')")
        conn.commit()
    archive = OddsArchive(root)
    with closing(sqlite3.connect(archive.db_path)) as conn:
        assert conn.execute("SELECT scan_id, kind FROM scans").fetchall() == [("old", None)]
    archive.start_scan("new", NOW, ["2026-10-03"])  # the old call signature still works
    archive.start_scan("nk-new", NOW, ["2026-10-03"], kind="near_kickoff")
    OddsArchive(root)  # re-opening (second migration pass) is harmless
