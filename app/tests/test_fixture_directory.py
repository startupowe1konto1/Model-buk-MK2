from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from model_buk.ingestion.api_football_history import RequestBudget
from model_buk.ingestion.fixture_directory import (
    FIXTURES_FRAME_COLUMNS,
    FixtureDirectory,
    FixtureDirectoryCollector,
    has_stat_data,
    load_fixture_stats,
    load_fixtures,
    parse_directory_fixture,
    parse_fixture_stats,
    parse_half_stats,
    plan_dates,
    read_pending_ids,
    regular_time_score,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def listing_obj(fixture_id, *, kickoff=None, status="NS", league_id=39, goals=(None, None), halftime=(None, None),
                fulltime=(None, None), extratime=(None, None), penalty=(None, None), elapsed=None,
                home="Home FC", away="Away FC", referee=None):
    kickoff = kickoff or NOW + timedelta(hours=3)
    return {
        "fixture": {
            "id": fixture_id, "referee": referee, "timezone": "UTC", "date": kickoff.isoformat(),
            "timestamp": int(kickoff.timestamp()),
            "venue": {"id": 10, "name": "Stadium", "city": "Town"},
            "status": {"long": "Not Started" if status == "NS" else "Match Finished", "short": status,
                       "elapsed": elapsed, "extra": None},
        },
        "league": {"id": league_id, "name": "Premier League", "country": "England", "season": 2026, "round": "Regular Season - 8"},
        "teams": {"home": {"id": fixture_id * 10 + 1, "name": home}, "away": {"id": fixture_id * 10 + 2, "name": away}},
        "goals": {"home": goals[0], "away": goals[1]},
        "score": {
            "halftime": {"home": halftime[0], "away": halftime[1]},
            "fulltime": {"home": fulltime[0], "away": fulltime[1]},
            "extratime": {"home": extratime[0], "away": extratime[1]},
            "penalty": {"home": penalty[0], "away": penalty[1]},
        },
    }


def stat_block(team_id, **values):
    """Statistics block; a value of ``...`` leaves the type out, ``None`` sends a provider null."""

    names = {"corners": "Corner Kicks", "yellow": "Yellow Cards", "red": "Red Cards", "shots": "Total Shots",
             "sot": "Shots on Goal"}
    items = [{"type": names[k], "value": v} for k, v in values.items() if v is not ...]
    return {"team": {"id": team_id, "name": f"team{team_id}"}, "statistics": items}


def detail_obj(fixture_id, *, statistics=None, events=None, status="FT", kickoff=None, goals=(2, 1)):
    obj = listing_obj(fixture_id, kickoff=kickoff or NOW - timedelta(days=1), status=status, goals=goals,
                      halftime=(1, 0), fulltime=goals)
    home_id, away_id = fixture_id * 10 + 1, fixture_id * 10 + 2
    if statistics is None:
        statistics = [stat_block(home_id, corners=6, yellow=2, red=None, shots=14, sot=5),
                      stat_block(away_id, corners=3, yellow=None, red=1, shots=8, sot=2)]
    obj["statistics"] = statistics
    obj["events"] = events if events is not None else []
    return obj


def card_event(team_id, detail="Yellow Card", minute=30):
    return {"time": {"elapsed": minute, "extra": None}, "team": {"id": team_id}, "type": "Card", "detail": detail}


def sub_event(team_id, minute=60):
    """A substitution: every complete provider event log has some."""

    return {"time": {"elapsed": minute, "extra": None}, "team": {"id": team_id}, "type": "subst", "detail": "Substitution 1"}


class FakeClient:
    """Serves listings by date (list of pages), details by ids, half stats by fixture; counts calls."""

    def __init__(self, listings=None, details=None, halves=None, quota=5000, fail_dates=()):
        self.listings = listings or {}
        self.details = details or {}
        self.halves = halves or {}
        self.quota = quota
        self.fail_dates = set(fail_dates)
        self.calls: list[tuple[str, dict]] = []

    def get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if endpoint == "/fixtures" and "date" in params:
            if params["date"] in self.fail_dates:
                raise RuntimeError("boom")
            pages = self.listings.get(params["date"], [[]])
            page = params.get("page", 1)
            return {"paging": {"current": page, "total": len(pages)}, "response": pages[page - 1]}, headers
        if endpoint == "/fixtures" and "ids" in params:
            ids = [int(i) for i in params["ids"].split("-")]
            assert len(ids) <= 20
            return {"paging": {"current": 1, "total": 1}, "response": [self.details[i] for i in ids if i in self.details]}, headers
        if endpoint == "/fixtures/statistics":
            return {"paging": {"current": 1, "total": 1}, "response": self.halves.get(params["fixture"], [])}, headers
        raise AssertionError(endpoint)


def make_collector(tmp_path, client, *, max_requests=100, now=NOW, budget=None):
    directory = FixtureDirectory(tmp_path)
    clock = {"now": now}
    collector = FixtureDirectoryCollector(
        client, directory, budget or RequestBudget(max_requests=max_requests), clock=lambda: clock["now"]
    )
    return collector, directory, clock


def refresh_today_only(collector, now=NOW, **kwargs):
    return collector.refresh(now, days_back=0, days_ahead=0, **kwargs)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_regular_time_score_excludes_extra_time():
    aet = listing_obj(1, status="AET", goals=(2, 1), fulltime=(1, 1), extratime=(1, 0))
    assert regular_time_score("AET", aet["goals"], aet["score"]) == (1, 1)
    row = parse_directory_fixture(aet)
    assert (row["home_goals"], row["away_goals"]) == (1, 1)  # 90 minutes
    assert (row["total_home_goals"], row["total_away_goals"]) == (2, 1)  # provider goals, incl. extra time


def test_regular_time_score_falls_back_and_never_invents_a_score():
    goals, score = {"home": 3, "away": 2}, {"fulltime": {}, "extratime": {"home": 1, "away": 1}}
    assert regular_time_score("AET", goals, score) == (2, 1)  # goals minus extra time
    assert regular_time_score("PEN", goals, {"fulltime": {}, "extratime": {}}) == (None, None)
    assert regular_time_score("1H", {"home": 1, "away": 0}, {}) == (1, 0)  # live score
    assert regular_time_score("NS", {"home": None, "away": None}, {}) == (None, None)


def test_parse_directory_fixture_keeps_missing_as_none():
    row = parse_directory_fixture(listing_obj(7))
    assert row["home_goals"] is None and row["ht_home_goals"] is None and row["referee"] is None
    assert row["league_name"] == "Premier League" and row["country"] == "England"
    assert row["kickoff_utc"].endswith("+00:00")
    obj = listing_obj(7)
    del obj["league"]["id"]
    assert parse_directory_fixture(obj) is None  # cannot be keyed


# --------------------------------------------------------------------------
# Calendar refresh
# --------------------------------------------------------------------------


def test_refresh_lists_days_and_upserts_fixtures(tmp_path):
    day = TODAY.isoformat()
    client = FakeClient({day: [[listing_obj(1), listing_obj(2, league_id=140)]]})
    collector, directory, _ = make_collector(tmp_path, client)
    report = refresh_today_only(collector)

    assert client.calls == [("/fixtures", {"date": day})]
    assert (report.requests, report.fixtures_listed, report.fixtures_new) == (1, 2, 2)
    assert directory.summary()["fixtures"] == 2
    frame = load_fixtures(directory.db_path)
    assert list(frame.columns) == list(FIXTURES_FRAME_COLUMNS)
    assert sorted(frame["league_id"]) == [39, 140]
    assert frame["home_goals"].isna().all()  # NS: no score, NaN not 0


def test_refresh_pages_through_a_multi_page_listing(tmp_path):
    day = TODAY.isoformat()
    client = FakeClient({day: [[listing_obj(1)], [listing_obj(2)], [listing_obj(3)]]})
    collector, directory, _ = make_collector(tmp_path, client)
    report = refresh_today_only(collector)

    assert [c[1].get("page") for c in client.calls] == [None, 2, 3]
    assert report.requests == 3
    assert directory.summary()["fixtures"] == 3


def test_upsert_keeps_latest_state_and_archives_only_changes(tmp_path):
    day = TODAY.isoformat()
    kickoff = NOW - timedelta(minutes=30)
    live = listing_obj(1, kickoff=kickoff, status="1H", goals=(0, 0), elapsed=30)
    other = listing_obj(2)
    client = FakeClient({day: [[live, other]]})
    collector, directory, clock = make_collector(tmp_path, client)
    refresh_today_only(collector)

    finished = listing_obj(1, kickoff=kickoff, status="FT", goals=(2, 1), halftime=(1, 0), fulltime=(2, 1), elapsed=90)
    client.listings[day] = [[finished, other]]
    clock["now"] = NOW + timedelta(hours=2)
    report = refresh_today_only(collector, now=NOW + timedelta(hours=2))

    assert (report.fixtures_new, report.fixtures_changed) == (0, 1)
    with sqlite3.connect(directory.db_path) as conn:
        row = conn.execute("SELECT status_short, home_goals, away_goals, ht_home_goals, first_seen_at, "
                           "state_changed_at, fetched_at FROM fixtures WHERE fixture_id=1").fetchone()
        untouched = conn.execute("SELECT state_changed_at, fetched_at FROM fixtures WHERE fixture_id=2").fetchone()
    assert row[:4] == ("FT", 2, 1, 1)
    assert row[4] == NOW.isoformat(timespec="seconds")  # first seen unchanged
    assert row[5] == row[6] == (NOW + timedelta(hours=2)).isoformat(timespec="seconds")
    # fixture 2 did not change: its state time stays, only the confirmation time moves
    assert untouched == (NOW.isoformat(timespec="seconds"), (NOW + timedelta(hours=2)).isoformat(timespec="seconds"))

    files = sorted((tmp_path / "raw" / "fixtures").rglob("*.jsonl.gz"))
    assert len(files) == 2  # first run: both new; second run: only the changed one
    second = [json.loads(line) for line in gzip.decompress(files[1].read_bytes()).decode().splitlines()]
    assert [r["fixture"]["fixture"]["id"] for r in second] == [1]


def test_an_older_state_never_overwrites_a_newer_one(tmp_path):
    directory = FixtureDirectory(tmp_path)
    newer = NOW + timedelta(hours=1)
    directory.upsert_fixtures("r2", "fixtures", "x", newer, [listing_obj(1, status="FT", goals=(1, 0), fulltime=(1, 0))])
    directory.upsert_fixtures("r1", "fixtures", "y", NOW, [listing_obj(1, status="1H", goals=(0, 0))])

    frame = load_fixtures(directory.db_path)
    assert frame["status_short"].tolist() == ["FT"]


def test_unchanged_relist_writes_no_raw_file(tmp_path):
    day = TODAY.isoformat()
    client = FakeClient({day: [[listing_obj(1)]]})
    collector, _, clock = make_collector(tmp_path, client)
    refresh_today_only(collector)
    clock["now"] = NOW + timedelta(minutes=30)
    report = refresh_today_only(collector, now=NOW + timedelta(minutes=30))

    assert (report.fixtures_new, report.fixtures_changed) == (0, 0)
    assert len(list((tmp_path / "raw" / "fixtures").rglob("*.jsonl.gz"))) == 1


def test_a_failing_day_does_not_stop_the_others_but_three_in_a_row_do(tmp_path):
    days = [TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=2)]
    client = FakeClient({d.isoformat(): [[listing_obj(10 + i)]] for i, d in enumerate(days)}, fail_dates={days[0].isoformat()})
    collector, directory, _ = make_collector(tmp_path, client)
    report = collector.refresh(NOW, days_back=0, days_ahead=2)

    assert report.dates_fetched == [days[1].isoformat(), days[2].isoformat()]
    assert len(report.errors) == 1 and report.stopped_reason == "completed"

    all_fail = FakeClient({}, fail_dates={d.isoformat() for d in days + [TODAY + timedelta(days=3)]})
    collector2, _, _ = make_collector(tmp_path / "b", all_fail)
    report2 = collector2.refresh(NOW, days_back=0, days_ahead=3)
    assert report2.stopped_reason == "api_error"
    assert len(all_fail.calls) == 3  # gave up after three consecutive failures


def test_budget_stops_refresh_and_reports_it(tmp_path):
    client = FakeClient({(TODAY + timedelta(days=o)).isoformat(): [[listing_obj(20 + o)]] for o in range(4)})
    collector, directory, _ = make_collector(tmp_path, client, max_requests=2)
    report = collector.refresh(NOW, days_back=0, days_ahead=3)

    assert report.stopped_reason == "budget_exhausted"
    assert report.requests == 2 and len(client.calls) == 2
    assert directory.summary()["fixtures"] == 2  # what was fetched before the stop is kept


def test_reserve_floor_stops_refresh(tmp_path):
    client = FakeClient({}, quota=1501)
    budget = RequestBudget(max_requests=100, reserve=1500)
    collector, _, _ = make_collector(tmp_path, client, budget=budget)
    report = collector.refresh(NOW, days_back=0, days_ahead=3)

    assert report.stopped_reason == "budget_exhausted"
    assert len(client.calls) == 1  # the quota header reached the reserve after the first call


# --------------------------------------------------------------------------
# Which days are due
# --------------------------------------------------------------------------


def _stamp(moment):
    return moment.isoformat(timespec="seconds")


def test_plan_dates_first_run_lists_the_whole_window_today_first():
    due = plan_dates(NOW, {}, days_back=3, days_ahead=7)
    assert due[0] == TODAY
    assert set(due) == {TODAY + timedelta(days=o) for o in range(-3, 8)}


def test_plan_dates_skips_fresh_future_days_and_settled_past_days():
    last = {}
    for offset in range(-3, 8):
        day = TODAY + timedelta(days=offset)
        if offset < 0:  # listed well after the day ended: settled
            last[day.isoformat()] = _stamp(datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=8))
        else:
            last[day.isoformat()] = _stamp(NOW - timedelta(minutes=20))
    assert plan_dates(NOW, last, days_back=3, days_ahead=7) == [TODAY]  # today is always listed

    later = NOW + timedelta(hours=2)  # tomorrow (60 min) and the far days (6 h) are not yet stale
    assert plan_dates(later, last, days_back=3, days_ahead=7) == [TODAY, TODAY + timedelta(days=1)]
    much_later = NOW + timedelta(hours=7)
    assert set(plan_dates(much_later, last, days_back=3, days_ahead=7)) == {TODAY + timedelta(days=o) for o in range(0, 8)}


def test_plan_dates_relists_a_past_day_listed_too_early():
    yesterday = TODAY - timedelta(days=1)
    listed_same_evening = _stamp(datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=22))
    due = plan_dates(NOW, {yesterday.isoformat(): listed_same_evening}, days_back=1, days_ahead=0)
    assert yesterday in due  # matches were still running at that listing
    due_forced = plan_dates(NOW, {yesterday.isoformat(): _stamp(NOW)}, days_back=1, days_ahead=0, force=True)
    assert yesterday in due_forced


# --------------------------------------------------------------------------
# Settlement statistics
# --------------------------------------------------------------------------


def test_missing_statistics_stay_null_and_never_become_zero(tmp_path):
    fid = 5
    statistics = [stat_block(51, corners=6, yellow=None, red=..., shots=14, sot=...),
                  stat_block(52, corners=..., yellow=1, red=None, shots=8, sot=2)]
    client = FakeClient(details={fid: detail_obj(fid, statistics=statistics)})
    collector, directory, _ = make_collector(tmp_path, client)
    report = collector.fetch_fixture_details([fid], NOW)

    assert (report.requests, report.fixtures_with_stats) == (1, 1)
    frame = load_fixture_stats(directory.db_path).set_index("side")
    home, away = frame.loc["home"], frame.loc["away"]
    assert home["corners"] == 6 and home["shots"] == 14
    assert home[["yellow_cards", "red_cards", "shots_on_target"]].isna().all()  # null / absent -> NULL
    assert away["yellow_cards"] == 1 and away[["corners", "red_cards"]].isna().all()
    assert away["shots_on_target"] == 2


def test_fixture_without_statistics_gets_no_row_and_is_retried_later(tmp_path):
    fid = 6
    client = FakeClient(details={fid: detail_obj(fid, statistics=[{"team": {"id": 61}, "statistics": []},
                                                                  {"team": {"id": 62}, "statistics": []}])})
    collector, directory, _ = make_collector(tmp_path, client)
    first = collector.fetch_fixture_details([fid], NOW)
    assert (first.fixtures_returned, first.fixtures_without_stats) == (1, 1)
    assert directory.ids_with_stats([fid]) == set()

    # too soon: not retried; after the retry gap: retried
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=1)).requests == 0
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=4)).requests == 1
    # after MAX attempts of empty answers it is given up
    for hours in (8, 12):
        collector.fetch_fixture_details([fid], NOW + timedelta(hours=hours))
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=40)).requests == 0
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=40), force=True).requests == 1


def test_details_are_batched_by_twenty_and_skip_fixtures_that_have_stats(tmp_path):
    ids = list(range(100, 145))  # 45 fixtures
    client = FakeClient(details={i: detail_obj(i) for i in ids})
    collector, directory, _ = make_collector(tmp_path, client)
    report = collector.fetch_fixture_details(ids, NOW)

    sizes = [len(params["ids"].split("-")) for endpoint, params in client.calls]
    assert sizes == [20, 20, 5] and report.requests == 3
    assert directory.summary()["fixtures_with_stats"] == 45
    again = collector.fetch_fixture_details(ids, NOW + timedelta(hours=1))
    assert again.requests == 0 and again.skipped == 45


def test_details_refresh_the_final_score_in_the_directory(tmp_path):
    fid = 8
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(days=1),
                              [listing_obj(fid, kickoff=NOW - timedelta(days=1), status="2H", goals=(1, 0), elapsed=80)])
    client = FakeClient(details={fid: detail_obj(fid, goals=(2, 1))})
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=10), clock=lambda: NOW)
    collector.fetch_fixture_details([fid], NOW)

    frame = load_fixtures(directory.db_path)
    assert frame.loc[0, "status_short"] == "FT" and (frame.loc[0, "home_goals"], frame.loc[0, "away_goals"]) == (2, 1)
    # the complete response, events included, is archived raw
    files = list((tmp_path / "raw" / "details").rglob("*.jsonl.gz"))
    assert len(files) == 1 and "events" in json.loads(gzip.decompress(files[0].read_bytes()).decode().splitlines()[0])["fixture"]


def test_matches_that_are_too_recent_are_not_requested(tmp_path):
    fid = 9
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW, [listing_obj(fid, kickoff=NOW - timedelta(minutes=45), status="2H")])
    client = FakeClient(details={fid: detail_obj(fid)})
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=10), clock=lambda: NOW)

    assert collector.fetch_fixture_details([fid], NOW).requests == 0
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=3)).requests == 1


def test_cards_from_events_settle_where_the_statistics_block_is_silent():
    fid = 4
    home_id, away_id = 41, 42
    stats = [stat_block(home_id, corners=5, yellow=None, red=None), stat_block(away_id, corners=2, yellow=2, red=None)]
    events = [card_event(home_id, "Yellow Card"), card_event(home_id, "Red Card", 70), card_event(away_id, "Yellow Card"),
              {"time": {"elapsed": 12}, "team": {"id": home_id}, "type": "Goal", "detail": "Normal Goal"}, sub_event(away_id)]
    rows = {r["side"]: r for r in parse_fixture_stats(detail_obj(fid, statistics=stats, events=events), "t")}

    assert rows["home"]["yellow_cards"] is None and rows["home"]["yellow_cards_events"] == 1
    assert rows["home"]["red_cards_events"] == 1 and rows["away"]["red_cards_events"] == 0
    assert rows["away"]["yellow_cards"] == 2 and rows["away"]["yellow_cards_events"] == 1


def test_without_any_events_card_event_counts_are_unknown_not_zero():
    obj = detail_obj(4, events=[])
    rows = parse_fixture_stats(obj, "t")
    assert all(r["yellow_cards_events"] is None and r["red_cards_events"] is None for r in rows)


def test_half_statistics_are_parsed_per_team_and_half(tmp_path):
    fid = 12
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(days=1),
                              [listing_obj(fid, kickoff=NOW - timedelta(days=1), status="FT", goals=(1, 0), fulltime=(1, 0))])
    home_id, away_id = fid * 10 + 1, fid * 10 + 2
    response = [
        {"team": {"id": home_id}, "statistics": [], "statistics_1h": [{"type": "Corner Kicks", "value": 2},
                                                                     {"type": "Yellow Cards", "value": 0},
                                                                     {"type": "Total Shots", "value": None}],
         "statistics_2h": [{"type": "Corner Kicks", "value": 4}, {"type": "Shots on Goal", "value": 3}]},
        {"team": {"id": away_id}, "statistics": [], "statistics_1h": [{"type": "Corner Kicks", "value": 1}]},
    ]
    client = FakeClient(halves={fid: response})
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=10), clock=lambda: NOW)
    report = collector.fetch_half_stats([fid], NOW)

    assert client.calls == [("/fixtures/statistics", {"fixture": fid, "half": "true"})]
    assert report.fixtures_with_stats == 1
    frame = load_fixture_stats(directory.db_path, halves=True).set_index(["side", "half"])
    assert frame.loc[("home", "1h"), "corners"] == 2 and frame.loc[("home", "2h"), "corners"] == 4
    assert frame.loc[("home", "1h"), "yellow_cards"] == 0  # provider sent an explicit 0
    assert frame.loc[("home", "1h"), ["shots", "shots_on_target"]].isna().all()
    assert frame.loc[("home", "2h"), "shots_on_target"] == 3
    assert ("away", "2h") not in frame.index  # no 2H block for the away team -> no row
    assert collector.fetch_half_stats([fid], NOW + timedelta(hours=1)).requests == 0  # already have it


def test_parse_half_stats_ignores_teams_without_id():
    rows = parse_half_stats([{"team": {}, "statistics_1h": [{"type": "Corner Kicks", "value": 1}]}], 1, "t", 10, 20)
    assert rows == []


def test_pending_stats_ids_lists_finished_fixtures_without_statistics(tmp_path):
    directory = FixtureDirectory(tmp_path)
    old = NOW - timedelta(days=1)
    directory.upsert_fixtures("r", "fixtures", "x", NOW, [
        listing_obj(1, kickoff=old, status="FT", goals=(1, 0), fulltime=(1, 0)),
        listing_obj(2, kickoff=old + timedelta(hours=1), status="FT", goals=(0, 0), fulltime=(0, 0)),
        listing_obj(3, kickoff=old, status="PST"),
        listing_obj(4, kickoff=NOW + timedelta(hours=2), status="NS"),
        listing_obj(5, kickoff=NOW - timedelta(minutes=30), status="FT", goals=(0, 0), fulltime=(0, 0)),
    ])
    directory.upsert_stats([{**parse_fixture_stats(detail_obj(2), "t")[0]}])

    assert directory.pending_stats_ids(NOW) == [1]  # 2 has stats, 3 postponed, 4 upcoming, 5 too recent
    assert directory.pending_stats_ids(NOW, limit=0) == []


def test_load_fixtures_filters_by_kickoff_and_status(tmp_path):
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW, [
        listing_obj(1, kickoff=NOW + timedelta(hours=1)),
        listing_obj(2, kickoff=NOW + timedelta(hours=30)),
        listing_obj(3, kickoff=NOW - timedelta(hours=5), status="FT", goals=(1, 1), fulltime=(1, 1)),
    ])
    upcoming = load_fixtures(directory.db_path, kickoff_from=NOW, kickoff_to=NOW + timedelta(hours=24), statuses=["NS"])
    assert upcoming["fixture_id"].tolist() == [1]
    assert load_fixtures(directory.db_path, fixture_ids=[3])["home_goals"].tolist() == [1]
    assert load_fixtures(directory.db_path, fixture_ids=[]).empty
    full = load_fixtures(directory.db_path, full=True)
    assert {"status_long", "ht_home_goals", "fetched_at", "venue_name"} <= set(full.columns)


@pytest.mark.parametrize("bad", ["", None])
def test_blank_text_fields_are_null(bad):
    obj = listing_obj(1)
    obj["fixture"]["referee"] = bad
    assert parse_directory_fixture(obj)["referee"] is None


def test_events_without_a_statistics_block_still_give_card_counts_but_not_statistics(tmp_path):
    fid = 14
    home_id, away_id = fid * 10 + 1, fid * 10 + 2
    events = [card_event(home_id, "Yellow Card"), card_event(home_id, "Second Yellow card", 80), card_event(away_id, "Red Card", 60),
              {"time": {"elapsed": 12}, "team": {"id": away_id}, "type": "Goal", "detail": "Normal Goal"}, sub_event(home_id)]
    obj = detail_obj(fid, statistics=[{"team": {"id": home_id}, "statistics": []}, {"team": {"id": away_id}, "statistics": []}],
                     events=events)
    client = FakeClient(details={fid: obj})
    collector, directory, _ = make_collector(tmp_path, client)
    report = collector.fetch_fixture_details([fid], NOW)

    assert (report.fixtures_returned, report.fixtures_with_stats, report.fixtures_without_stats) == (1, 0, 1)
    frame = load_fixture_stats(directory.db_path).set_index("side")
    assert frame["corners"].isna().all() and frame["shots"].isna().all()  # no statistics: NULL, not 0
    assert (frame.loc["home", "yellow_cards_events"], frame.loc["home", "second_yellow_events"]) == (1, 1)
    assert (frame.loc["away", "red_cards_events"], frame.loc["away", "yellow_cards_events"]) == (1, 0)
    assert frame["events_count"].tolist() == [5, 5]
    # such a row is not "statistics present": the match is still retried later
    assert directory.ids_with_stats([fid]) == set()
    assert directory.summary()["fixtures_with_stats"] == 0
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=4)).requests == 1


def test_existing_directory_without_events_count_is_migrated(tmp_path):
    with sqlite3.connect(tmp_path / "directory.sqlite3") as conn:
        conn.execute("CREATE TABLE fixture_stats (fixture_id INTEGER NOT NULL, team_id INTEGER NOT NULL, side TEXT, corners INTEGER, "
                     "yellow_cards INTEGER, red_cards INTEGER, shots INTEGER, shots_on_target INTEGER, yellow_cards_events INTEGER, "
                     "red_cards_events INTEGER, second_yellow_events INTEGER, fetched_at TEXT NOT NULL, PRIMARY KEY (fixture_id, team_id))")
    directory = FixtureDirectory(tmp_path)
    directory.upsert_stats(parse_fixture_stats(detail_obj(1), "t"))
    assert len(load_fixture_stats(directory.db_path)) == 2


def test_read_pending_ids_from_a_sqlite_file(tmp_path):
    path = tmp_path / "pending.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE pending_fixture_ids (fixture_id INTEGER, need_halves INTEGER)")
        conn.executemany("INSERT INTO pending_fixture_ids VALUES (?, ?)", [(3, 0), (1, 1), (3, 0), (2, None), (None, 1)])
    assert read_pending_ids(path) == ([1, 2, 3], [1])

    other = tmp_path / "other.sqlite3"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.execute("CREATE TABLE settle_queue (fixture_id INTEGER)")
        conn.executemany("INSERT INTO settle_queue VALUES (?)", [(9,), (8,)])
    assert read_pending_ids(other) == ([8, 9], [])  # first table with a fixture_id column, no halves column
    with pytest.raises(ValueError):
        read_pending_ids(other, "unrelated")


def test_remembered_quota_below_the_reserve_blocks_the_calendar_refresh(tmp_path):
    client = FakeClient({})
    directory = FixtureDirectory(tmp_path)
    directory.save_quota(1400, NOW - timedelta(hours=1))
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=50, reserve=1500), clock=lambda: NOW)
    report = collector.refresh(NOW, days_back=0, days_ahead=2)

    assert client.calls == [] and report.stopped_reason == "budget_exhausted"


# --------------------------------------------------------------------------
# Review fixes: what must never be claimed about the 90 minutes
# --------------------------------------------------------------------------


def test_a_goals_only_event_log_gives_no_card_counts():
    """Thin provider coverage (goals but no substitutions) says nothing about cards: NULL, not 0."""

    home_id, away_id = 41, 42
    goals_only = [{"time": {"elapsed": m}, "team": {"id": home_id}, "type": "Goal", "detail": "Normal Goal"} for m in (10, 50, 80)]
    rows = parse_fixture_stats(detail_obj(4, statistics=[], events=goals_only), "t")
    assert rows == []  # no statistics block and no trustworthy event log: nothing to store

    with_stats = parse_fixture_stats(
        detail_obj(4, statistics=[stat_block(home_id, corners=5), stat_block(away_id, corners=2)], events=goals_only), "t")
    assert len(with_stats) == 2
    for row in with_stats:
        assert row["yellow_cards_events"] is None and row["red_cards_events"] is None and row["second_yellow_events"] is None
        assert row["events_count"] == 3  # the size of the log is still reported


def test_extra_time_cards_do_not_count_towards_the_90_minutes():
    home_id, away_id = 41, 42
    events = [card_event(home_id, "Yellow Card", 40), card_event(home_id, "Yellow Card", 91), card_event(away_id, "Red Card", 119),
              sub_event(home_id, 70), card_event(away_id, "Yellow Card", 90)]
    rows = {r["side"]: r for r in parse_fixture_stats(detail_obj(4, statistics=[], events=events), "t")}
    assert (rows["home"]["yellow_cards_events"], rows["away"]["yellow_cards_events"]) == (1, 1)  # minute 90 counts, 91 does not
    assert (rows["home"]["red_cards_events"], rows["away"]["red_cards_events"]) == (0, 0)  # the red card came in extra time
    # a log whose only substitutions happened in extra time is not shown to be complete
    late_sub_only = [card_event(home_id, "Yellow Card", 40), sub_event(home_id, 105)]
    assert parse_fixture_stats(detail_obj(4, statistics=[], events=late_sub_only), "t") == []


@pytest.mark.parametrize("status", ["AET", "PEN"])
def test_statistics_of_matches_with_extra_time_are_not_stored_as_90_minute_figures(status):
    home_id, away_id = 41, 42
    obj = detail_obj(4, status=status, events=[card_event(home_id, "Yellow Card", 30), sub_event(away_id)])
    rows = {r["side"]: r for r in parse_fixture_stats(obj, "t")}
    assert all(r["corners"] is None and r["yellow_cards"] is None and r["red_cards"] is None
               and r["shots"] is None and r["shots_on_target"] is None for r in rows.values())
    assert rows["home"]["yellow_cards_events"] == 1  # regular-time cards from a complete log are still known
    assert not any(has_stat_data(r) for r in rows.values())
    # the same block on a normal finish is kept
    assert {r["corners"] for r in parse_fixture_stats(detail_obj(4, status="FT"), "t")} == {6, 3}


def test_statistics_of_a_match_that_is_still_running_are_not_stored(tmp_path):
    fid = 21
    live = detail_obj(fid, status="2H", kickoff=NOW - timedelta(hours=3))  # delayed match, statistics partial
    client = FakeClient(details={fid: live})
    collector, directory, _ = make_collector(tmp_path, client)
    report = collector.fetch_fixture_details([fid], NOW)

    assert (report.fixtures_returned, report.fixtures_with_stats, report.fixtures_without_stats) == (1, 0, 1)
    assert directory.ids_with_stats([fid]) == set() and load_fixture_stats(directory.db_path).empty
    assert load_fixtures(directory.db_path).loc[0, "status_short"] == "2H"  # the fixture row still follows the match

    client.details[fid] = detail_obj(fid, status="FT", kickoff=NOW - timedelta(hours=3))
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=4)).fixtures_with_stats == 1
    assert directory.ids_with_stats([fid]) == {fid}


def test_half_statistics_are_only_stored_for_matches_known_to_be_finished(tmp_path):
    home_id, away_id = 81, 82
    response = [{"team": {"id": t}, "statistics": [], "statistics_1h": [{"type": "Corner Kicks", "value": 2}],
                 "statistics_2h": [{"type": "Corner Kicks", "value": 3}]} for t in (home_id, away_id)]
    client = FakeClient(halves={8: response, 9: response, 10: response})
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(days=1), [
        listing_obj(8, kickoff=NOW - timedelta(days=1), status="2H", goals=(1, 0)),      # not finished
        listing_obj(9, kickoff=NOW - timedelta(days=1), status="AET", goals=(1, 1), fulltime=(1, 1), extratime=(0, 0)),
    ])
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=10), clock=lambda: NOW)
    report = collector.fetch_half_stats([8, 9, 10], NOW)  # 10 is not in the directory at all

    assert [params["fixture"] for _, params in client.calls] == [9]  # no quota spent on the other two
    assert (report.requests, report.skipped, report.fixtures_with_stats) == (1, 2, 1)
    frame = load_fixture_stats(directory.db_path, halves=True)
    assert set(frame["fixture_id"]) == {9} and set(frame["half"]) == {"1h"}  # extra time: first half only


@pytest.mark.parametrize("status", ["AWD", "WO", "ABD"])
def test_matches_without_a_played_result_have_no_score(status):
    obj = listing_obj(3, status=status, goals=(3, 0), fulltime=(3, 0))
    row = parse_directory_fixture(obj)
    assert (row["home_goals"], row["away_goals"]) == (None, None)
    assert (row["total_home_goals"], row["total_away_goals"]) == (3, 0)  # the provider's figure is kept as is


def test_load_fixtures_kickoff_bounds_are_inclusive_from_and_exclusive_to(tmp_path):
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW, [
        listing_obj(1, kickoff=NOW), listing_obj(2, kickoff=NOW + timedelta(hours=1)), listing_obj(3, kickoff=NOW + timedelta(hours=2)),
    ])
    frame = load_fixtures(directory.db_path, kickoff_from=NOW + timedelta(hours=1), kickoff_to=NOW + timedelta(hours=2))
    assert frame["fixture_id"].tolist() == [2]


def test_ids_the_provider_does_not_return_count_as_empty_attempts(tmp_path):
    """A fixture the API answers nothing for is not retried for ever."""

    fid = 31
    directory = FixtureDirectory(tmp_path)
    directory.upsert_fixtures("r", "fixtures", "x", NOW - timedelta(days=1),
                              [listing_obj(fid, kickoff=NOW - timedelta(days=1), status="FT", goals=(1, 0), fulltime=(1, 0))])
    client = FakeClient(details={})  # the provider returns an empty response for this id
    collector = FixtureDirectoryCollector(client, directory, RequestBudget(max_requests=50), clock=lambda: NOW)
    for hours in (0, 4, 8, 12):
        assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=hours)).requests == 1
    assert collector.fetch_fixture_details([fid], NOW + timedelta(hours=16)).requests == 0  # given up after four tries


def test_raw_files_are_never_overwritten(tmp_path):
    from model_buk.ingestion.fixture_directory import _write_raw

    path = tmp_path / "raw" / "fixtures" / "2026-10-03" / "run_x.jsonl.gz"
    first = _write_raw(path, tmp_path, [{"n": 1}])
    second = _write_raw(path, tmp_path, [{"n": 2}])
    assert first != second and len(list(path.parent.glob("*.jsonl.gz"))) == 2
    assert json.loads(gzip.decompress((tmp_path / first).read_bytes()).decode()) == {"n": 1}
