from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from model_buk.ingestion.api_football_history import RequestBudget
from model_buk.ingestion.api_football_prematch import (
    TRACKED_LEAGUE_IDS,
    PrematchArchive,
    PrematchCollector,
    fixture_dates,
    parse_injury,
    parse_lineup_blocks,
    parse_prediction,
)

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
TRACKED = 39
UNTRACKED = 999


def listing(fixture_id, *, kickoff, league_id=TRACKED, status="NS", home=(10, "Home FC"), away=(20, "Away FC")):
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat(), "status": {"short": status}},
        "league": {"id": league_id, "season": 2026, "round": "Regular Season - 8"},
        "teams": {"home": {"id": home[0], "name": home[1]}, "away": {"id": away[0], "name": away[1]}},
    }


def with_lineups(obj, *, home_players=11, away_players=11, teams=(10, 20)):
    """A /fixtures?ids= object carrying lineups for the given teams."""

    blocks = []
    for team_id, count in zip(teams, (home_players, away_players)):
        if not count:
            continue
        blocks.append(
            {
                "team": {"id": team_id, "name": f"team {team_id}"},
                "coach": {"id": team_id * 100, "name": f"coach {team_id}"},
                "formation": "4-3-3",
                "startXI": [
                    {"player": {"id": team_id * 1000 + n, "name": f"p{n}", "number": n, "pos": "M", "grid": f"2:{n}"}}
                    for n in range(1, count + 1)
                ],
                "substitutes": [
                    {"player": {"id": team_id * 1000 + 50 + n, "name": f"s{n}", "number": 12 + n, "pos": "M", "grid": None}}
                    for n in range(2)
                ],
            }
        )
    return {**obj, "lineups": blocks}


def injury(fixture_id, player_id, *, kickoff, league_id=TRACKED, kind="Missing Fixture", reason="Knee Injury"):
    return {
        "player": {"id": player_id, "name": f"player {player_id}", "type": kind, "reason": reason},
        "team": {"id": 10, "name": "Home FC"},
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": league_id, "season": 2026},
    }


def prediction(percent=("45%", "30%", "25%"), *, advice="Double chance : Home or draw"):
    return {
        "predictions": {
            "winner": {"id": 10, "name": "Home FC", "comment": "Win or draw"},
            "win_or_draw": True,
            "under_over": "-3.5",
            "goals": {"home": "-1.5", "away": None},
            "advice": advice,
            "percent": {"home": percent[0], "draw": percent[1], "away": percent[2]},
        },
        "league": {"id": TRACKED},
    }


class FakeClient:
    """Serves scripted responses per endpoint; counts calls; decrements a quota header."""

    def __init__(self, *, quota_start=5000):
        self.fixtures_by_date: dict[str, list] = {}
        self.by_id: dict[int, dict] = {}
        self.injury_pages_by_date: dict[str, list[list]] = {}
        self.predictions: dict[int, list] = {}
        self.fail: dict[str, Exception] = {}
        self.quota = quota_start
        self.calls: list[tuple[str, dict]] = []

    def calls_to(self, endpoint, key=None):
        return [p for e, p in self.calls if e == endpoint and (key is None or key in p)]

    def get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint in self.fail:
            raise self.fail[endpoint]
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if endpoint == "/fixtures" and "date" in params:
            response = self.fixtures_by_date.get(params["date"], [])
            paging = {"current": 1, "total": 1}
        elif endpoint == "/fixtures" and "ids" in params:
            ids = [int(part) for part in params["ids"].split("-")]
            assert len(ids) <= 20
            response = [self.by_id[i] for i in ids if i in self.by_id]
            paging = {"current": 1, "total": 1}
        elif endpoint == "/injuries":
            pages = self.injury_pages_by_date.get(params["date"], [[]])
            page = params.get("page", 1)
            response = pages[page - 1]
            paging = {"current": page, "total": len(pages)}
        elif endpoint == "/predictions":
            response = self.predictions.get(int(params["fixture"]), [])
            paging = {"current": 1, "total": 1}
        else:  # pragma: no cover - a test asked for an endpoint the collector must not use
            raise AssertionError(f"unexpected endpoint {endpoint}")
        return {"paging": paging, "response": response}, headers


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


def make(tmp_path, client, *, now=NOW, budget=None, clock=None, **kwargs):
    archive = PrematchArchive(tmp_path)
    collector = PrematchCollector(
        client,
        archive,
        budget or RequestBudget(max_requests=200),
        league_ids={TRACKED},
        clock=clock or Clock(now),
        **kwargs,
    )
    return archive, collector


def today_key(now=NOW):
    return now.date().isoformat()


def db(tmp_path):
    return sqlite3.connect(tmp_path / "prematch.sqlite3")


# --------------------------------------------------------------------------
# Fixtures listing
# --------------------------------------------------------------------------


def test_fixture_dates_are_today_and_tomorrow_utc():
    assert fixture_dates(NOW) == [NOW.date(), NOW.date() + timedelta(days=1)]
    assert len(fixture_dates(NOW, days_ahead=3)) == 4


def test_tracked_leagues_come_from_the_planned_scope():
    assert {39, 106, 140} <= TRACKED_LEAGUE_IDS
    assert len(TRACKED_LEAGUE_IDS) == 47


def test_listing_keeps_tracked_upcoming_fixtures_and_skips_the_rest(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [
        listing(1, kickoff=NOW + timedelta(hours=5)),
        listing(2, kickoff=NOW + timedelta(hours=6), league_id=UNTRACKED),
        listing(3, kickoff=NOW - timedelta(hours=3), status="FT"),
        listing(4, kickoff=NOW + timedelta(hours=7), status="PST"),
        listing(5, kickoff=NOW + timedelta(hours=8), status="TBD"),
    ]
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    with db(tmp_path) as conn:
        rows = dict(conn.execute("SELECT fixture_id, status_short FROM fixtures"))
    assert rows == {1: "NS", 5: "TBD"}
    assert report.fixtures_seen == 2
    assert [p["date"] for p in client.calls_to("/fixtures", "date")] == [today_key(), (NOW + timedelta(days=1)).date().isoformat()]


def test_known_fixture_keeps_first_seen_and_follows_status_changes(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [listing(1, kickoff=NOW + timedelta(hours=5))]
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    later = NOW + timedelta(hours=1)
    client.fixtures_by_date[today_key()] = [listing(1, kickoff=NOW + timedelta(hours=5), status="PST")]
    _, collector2 = make(tmp_path, client, now=later)
    collector2.run(later)

    with db(tmp_path) as conn:
        row = conn.execute("SELECT status_short, first_seen_at, last_seen_at FROM fixtures WHERE fixture_id=1").fetchone()
    assert row == ("PST", NOW.isoformat(timespec="seconds"), later.isoformat(timespec="seconds"))


# --------------------------------------------------------------------------
# Lineups
# --------------------------------------------------------------------------


def lineup_setup(client, offsets_minutes, *, now=NOW):
    """Register fixtures kicking off ``offsets_minutes`` from now; ids are 100 + index."""

    listed = []
    for index, offset in enumerate(offsets_minutes):
        fixture_id = 100 + index
        obj = listing(fixture_id, kickoff=now + timedelta(minutes=offset))
        listed.append(obj)
        client.by_id[fixture_id] = with_lineups(obj)
    client.fixtures_by_date[now.date().isoformat()] = listed
    return [100 + index for index in range(len(offsets_minutes))]


def test_lineup_window_is_zero_to_one_hundred_minutes_ahead(tmp_path):
    client = FakeClient()
    ids = lineup_setup(client, [-5, 0, 30, 100, 101, 400])
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    requested = client.calls_to("/fixtures", "ids")
    assert len(requested) == 1
    # Kickoff exactly now is already too late; 30 and 100 minutes ahead are in.
    assert requested[0]["ids"] == f"{ids[2]}-{ids[3]}"
    assert report.lineups_captured == 2


def test_lineup_rows_carry_fetch_time_and_minutes_before_kickoff(tmp_path):
    client = FakeClient()
    lineup_setup(client, [55])
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    with db(tmp_path) as conn:
        players = conn.execute(
            "SELECT team_id, is_starter, formation, fetched_at, minutes_before_kickoff FROM lineup_players WHERE fixture_id=100"
        ).fetchall()
        teams = conn.execute("SELECT team_id, formation, coach_id FROM lineups WHERE fixture_id=100 ORDER BY team_id").fetchall()
    assert len(players) == 2 * (11 + 2)
    assert sum(1 for p in players if p[1] == 1) == 22
    assert {p[2] for p in players} == {"4-3-3"}
    assert {p[3] for p in players} == {NOW.isoformat(timespec="seconds")}
    assert {p[4] for p in players} == {55.0}
    assert teams == [(10, "4-3-3", 1000), (20, "4-3-3", 2000)]


def test_captured_lineup_is_not_requested_again(tmp_path):
    client = FakeClient()
    lineup_setup(client, [90])
    archive, collector = make(tmp_path, client)
    collector.run(NOW)
    later = NOW + timedelta(minutes=20)
    client.fixtures_by_date[later.date().isoformat()] = client.fixtures_by_date[NOW.date().isoformat()]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)

    assert len(client.calls_to("/fixtures", "ids")) == 1
    assert report2.lineups_captured == 0
    with db(tmp_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lineup_players").fetchone()[0] == 26


def test_empty_lineups_are_retried_on_the_next_run(tmp_path):
    client = FakeClient()
    [fixture_id] = lineup_setup(client, [95])
    published = client.by_id[fixture_id]
    client.by_id[fixture_id] = {**published, "lineups": []}
    archive, collector = make(tmp_path, client)
    report1 = collector.run(NOW)

    assert report1.lineups_captured == 0
    assert report1.lineups_tried == 1
    with db(tmp_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lineup_players").fetchone()[0] == 0
        attempts = conn.execute("SELECT fixture_id, teams_found, minutes_before_kickoff FROM lineup_attempts").fetchall()
    assert attempts == [(fixture_id, 0, 95.0)]

    later = NOW + timedelta(minutes=50)  # 45 minutes before kickoff: the provider has published by now
    client.by_id[fixture_id] = published
    client.fixtures_by_date[later.date().isoformat()] = [listing(fixture_id, kickoff=NOW + timedelta(minutes=95))]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)

    assert report2.lineups_captured == 1
    assert len(client.calls_to("/fixtures", "ids")) == 2
    with db(tmp_path) as conn:
        assert {r[0] for r in conn.execute("SELECT minutes_before_kickoff FROM lineup_players")} == {45.0}


def test_half_published_lineup_is_completed_without_duplicating_the_first_team(tmp_path):
    client = FakeClient()
    [fixture_id] = lineup_setup(client, [80])
    obj = listing(fixture_id, kickoff=NOW + timedelta(minutes=80))
    client.by_id[fixture_id] = with_lineups(obj, away_players=0)
    archive, collector = make(tmp_path, client)
    report1 = collector.run(NOW)
    assert report1.lineups_captured == 0  # only one team so far

    later = NOW + timedelta(minutes=30)
    client.by_id[fixture_id] = with_lineups(obj)
    client.fixtures_by_date[later.date().isoformat()] = [obj]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)

    assert report2.lineups_captured == 1
    with db(tmp_path) as conn:
        per_team = dict(conn.execute("SELECT team_id, COUNT(*) FROM lineup_players GROUP BY team_id"))
        first_team_fetch = conn.execute("SELECT DISTINCT fetched_at FROM lineup_players WHERE team_id=10").fetchall()
    assert per_team == {10: 13, 20: 13}
    assert first_team_fetch == [(NOW.isoformat(timespec="seconds"),)]


def test_lineup_requests_are_batched_by_twenty(tmp_path):
    client = FakeClient()
    lineup_setup(client, [60] * 25)
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    sizes = [len(p["ids"].split("-")) for p in client.calls_to("/fixtures", "ids")]
    assert sizes == [20, 5]
    assert report.lineups_captured == 25


def test_lineup_fetched_after_kickoff_is_never_recorded(tmp_path):
    client = FakeClient()
    lineup_setup(client, [10])
    # The response arrives after the (listed) kickoff: not a pre-match observation.
    # Clock calls: two fixture listings, then the lineup batch.
    late = NOW + timedelta(minutes=11)
    times = iter([NOW, NOW])
    archive, collector = make(tmp_path, client, clock=lambda: next(times, late))
    report = collector.run(NOW)

    assert report.lineups_captured == 0
    with db(tmp_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lineup_players").fetchone()[0] == 0


# --------------------------------------------------------------------------
# Predictions
# --------------------------------------------------------------------------


def test_prediction_is_fetched_once_for_fixtures_within_thirty_hours(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [
        listing(1, kickoff=NOW + timedelta(hours=5)),
        listing(2, kickoff=NOW + timedelta(hours=29, minutes=59)),
        listing(3, kickoff=NOW + timedelta(hours=31)),
    ]
    for fixture_id in (1, 2, 3):
        client.predictions[fixture_id] = [prediction()]
    archive, collector = make(tmp_path, client)
    report1 = collector.run(NOW)
    assert report1.predictions_stored == 2
    assert sorted(int(p["fixture"]) for p in client.calls_to("/predictions")) == [1, 2]

    later = NOW + timedelta(hours=1)  # fixture 3 is now inside the 30 h window
    client.fixtures_by_date[later.date().isoformat()] = client.fixtures_by_date[today_key()]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)
    assert report2.predictions_stored == 1
    assert sorted(int(p["fixture"]) for p in client.calls_to("/predictions")) == [1, 2, 3]

    _, collector3 = make(tmp_path, client, now=later + timedelta(hours=1))
    collector3.run(later + timedelta(hours=1))
    assert len(client.calls_to("/predictions")) == 3  # nothing fetched twice


def test_prediction_fields_are_extracted_with_fetch_time(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [listing(1, kickoff=NOW + timedelta(hours=5))]
    client.predictions[1] = [prediction()]
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    with db(tmp_path) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM predictions WHERE fixture_id=1").fetchone())
    assert (row["percent_home"], row["percent_draw"], row["percent_away"]) == (45.0, 30.0, 25.0)
    assert row["advice"] == "Double chance : Home or draw"
    assert row["under_over"] == "-3.5"
    assert row["goals_home"] == -1.5
    assert row["goals_away"] is None  # a missing value stays missing
    assert row["winner_team_id"] == 10 and row["win_or_draw"] == 1
    assert row["fetched_at"] == NOW.isoformat(timespec="seconds")
    assert row["minutes_before_kickoff"] == 300.0


def test_empty_prediction_is_retried_a_limited_number_of_times(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [listing(1, kickoff=NOW + timedelta(hours=5))]
    for hour in range(5):
        moment = NOW + timedelta(hours=hour)
        client.fixtures_by_date[moment.date().isoformat()] = client.fixtures_by_date[today_key()]
        _, collector = make(tmp_path, client, now=moment)
        collector.run(moment)

    assert len(client.calls_to("/predictions")) == 3
    with db(tmp_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


def test_prediction_fetched_after_kickoff_is_not_stored(tmp_path):
    client = FakeClient()
    client.fixtures_by_date[today_key()] = [listing(1, kickoff=NOW + timedelta(minutes=5))]
    client.predictions[1] = [prediction()]
    # Two fixture listings arrive on time; everything after them arrives past kickoff.
    late = NOW + timedelta(minutes=6)
    times = iter([NOW, NOW])
    archive, collector = make(tmp_path, client, clock=lambda: next(times, late))
    report = collector.run(NOW)

    assert report.predictions_stored == 0


def test_parse_prediction_tolerates_missing_blocks():
    assert parse_prediction({}) is None
    parsed = parse_prediction({"predictions": {"percent": {"home": "60%"}}})
    assert parsed["percent_home"] == 60.0
    assert parsed["percent_draw"] is None and parsed["advice"] is None and parsed["winner_team_id"] is None


# --------------------------------------------------------------------------
# Injuries
# --------------------------------------------------------------------------


def test_injury_first_seen_stays_and_last_seen_moves(tmp_path):
    client = FakeClient()
    kickoff = NOW + timedelta(hours=6)
    client.injury_pages_by_date[today_key()] = [[injury(1, 7, kickoff=kickoff)]]
    archive, collector = make(tmp_path, client)
    report1 = collector.run(NOW)
    assert (report1.injuries_new, report1.injuries_seen) == (1, 0)

    later = NOW + timedelta(hours=2)
    client.injury_pages_by_date[later.date().isoformat()] = [[injury(1, 7, kickoff=kickoff)]]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)
    assert (report2.injuries_new, report2.injuries_seen) == (0, 1)

    with db(tmp_path) as conn:
        rows = conn.execute(
            "SELECT fixture_id, player_id, type, reason, first_seen_at, last_seen_at, kickoff_utc FROM injuries"
        ).fetchall()
    assert rows == [
        (1, 7, "Missing Fixture", "Knee Injury", NOW.isoformat(timespec="seconds"),
         later.isoformat(timespec="seconds"), kickoff.isoformat(timespec="seconds"))
    ]


def test_changed_injury_status_adds_a_row_and_keeps_history(tmp_path):
    client = FakeClient()
    kickoff = NOW + timedelta(hours=6)
    client.injury_pages_by_date[today_key()] = [[injury(1, 7, kickoff=kickoff, kind="Questionable", reason="Knock")]]
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    later = NOW + timedelta(hours=2)
    client.injury_pages_by_date[later.date().isoformat()] = [[injury(1, 7, kickoff=kickoff)]]
    _, collector2 = make(tmp_path, client, now=later)
    report2 = collector2.run(later)
    assert report2.injuries_new == 1

    known_early = archive.injuries_as_of(1, NOW + timedelta(minutes=30))
    known_late = archive.injuries_as_of(1, later)
    assert [r["type"] for r in known_early] == ["Questionable"]
    assert sorted(r["type"] for r in known_late) == ["Missing Fixture", "Questionable"]
    assert archive.injuries_as_of(1, NOW - timedelta(minutes=1)) == []


def test_injuries_of_untracked_leagues_and_started_matches_are_dropped(tmp_path):
    client = FakeClient()
    client.injury_pages_by_date[today_key()] = [
        [
            injury(1, 7, kickoff=NOW + timedelta(hours=6)),
            injury(2, 8, kickoff=NOW + timedelta(hours=6), league_id=UNTRACKED),
            injury(3, 9, kickoff=NOW - timedelta(hours=1)),
            injury(4, 10, kickoff=NOW),
        ]
    ]
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert report.injuries_new == 1
    with db(tmp_path) as conn:
        assert [r[0] for r in conn.execute("SELECT fixture_id FROM injuries")] == [1]


def test_duplicate_entries_in_one_snapshot_count_once(tmp_path):
    # The real feed lists every injury twice with identical content.
    client = FakeClient()
    kickoff = NOW + timedelta(hours=6)
    entry = injury(1, 7, kickoff=kickoff)
    client.injury_pages_by_date[today_key()] = [[entry, dict(entry), injury(1, 8, kickoff=kickoff)]]
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert (report.injuries_new, report.injuries_seen) == (2, 0)
    raw = read_raw(next(p for p in raw_files(tmp_path) if "injuries" in p.parts))
    assert len(raw) == 2


def test_injury_paging_is_followed(tmp_path):
    client = FakeClient()
    kickoff = NOW + timedelta(hours=6)
    client.injury_pages_by_date[today_key()] = [[injury(1, 7, kickoff=kickoff)], [injury(1, 8, kickoff=kickoff)]]
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    calls = client.calls_to("/injuries")
    assert [c.get("page") for c in calls if c["date"] == today_key()] == [None, 2]
    assert report.injuries_new == 2


def test_parse_injury_needs_a_fixture_player_and_kickoff():
    kickoff = NOW + timedelta(hours=1)
    assert parse_injury(injury(1, 7, kickoff=kickoff))["player_id"] == 7
    assert parse_injury({"player": {"id": 7}, "fixture": {"id": 1}}) is None
    assert parse_injury({"player": {}, "fixture": {"id": 1, "date": kickoff.isoformat()}}) is None


# --------------------------------------------------------------------------
# Budget, errors, raw archive
# --------------------------------------------------------------------------


def busy_client():
    client = FakeClient()
    ids = lineup_setup(client, [50, 60])
    for fixture_id in ids:
        client.predictions[fixture_id] = [prediction()]
    client.injury_pages_by_date[today_key()] = [[injury(ids[0], 7, kickoff=NOW + timedelta(minutes=50))]]
    return client, ids


def test_budget_cap_stops_the_run_and_keeps_what_was_fetched(tmp_path):
    client, _ = busy_client()
    archive, collector = make(tmp_path, client, budget=RequestBudget(max_requests=3))
    report = collector.run(NOW)

    assert report.stopped_reason == "budget_exhausted"
    assert report.requests == 3 == len(client.calls)
    # 2 listings and the lineup batch were paid for; the lineup is archived.
    assert report.lineups_captured == 2
    assert report.predictions_stored == 0
    assert archive.summary()["lineup_teams"] == 4


def test_quota_reserve_stops_the_run(tmp_path):
    client, _ = busy_client()
    client.quota = 1501
    archive, collector = make(tmp_path, client, budget=RequestBudget(max_requests=100, reserve=1500))
    report = collector.run(NOW)

    assert report.stopped_reason == "budget_exhausted"
    assert report.requests == 1


def test_run_completes_and_reports_all_counts(tmp_path):
    client, ids = busy_client()
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert report.stopped_reason == "completed"
    assert (report.fixtures_seen, report.lineups_captured, report.predictions_stored) == (2, 2, 2)
    assert (report.injuries_new, report.injuries_seen) == (1, 0)
    with db(tmp_path) as conn:
        run = conn.execute("SELECT requests, stopped_reason, finished_at FROM runs").fetchone()
    assert run[0] == report.requests and run[1] == "completed" and run[2] is not None


def test_a_failing_endpoint_does_not_block_the_other_steps(tmp_path):
    client, _ = busy_client()
    client.fail["/injuries"] = RuntimeError("API-Football error: boom")
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert report.stopped_reason == "api_error"
    assert any("injuries" in message for message in report.errors)
    assert report.lineups_captured == 2 and report.predictions_stored == 2
    # A failed call still cost quota, so it counts against the per-run cap.
    assert report.requests == len(client.calls)


def test_repeated_prediction_errors_stop_that_step_only(tmp_path):
    client = FakeClient()
    ids = lineup_setup(client, [50, 60, 70, 80, 90])
    client.fail["/predictions"] = RuntimeError("API-Football error: boom")
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert report.stopped_reason == "api_error"
    assert len(client.calls_to("/predictions")) == 3
    assert report.lineups_captured == len(ids)


def raw_files(tmp_path):
    return sorted((tmp_path / "raw").rglob("*.jsonl.gz"))


def read_raw(path):
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()]


def test_raw_payloads_are_kept_with_fetch_time_per_kind(tmp_path):
    client, ids = busy_client()
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    kinds = {path.relative_to(tmp_path / "raw").parts[0] for path in raw_files(tmp_path)}
    assert kinds == {"fixtures", "lineups", "injuries", "predictions"}
    lineup_file = next(p for p in raw_files(tmp_path) if "lineups" in p.parts)
    rows = read_raw(lineup_file)
    assert {row["fetched_at"] for row in rows} == {NOW.isoformat(timespec="seconds")}
    assert {row["fixture"]["fixture"]["id"] for row in rows} == set(ids)
    prediction_file = next(p for p in raw_files(tmp_path) if "predictions" in p.parts)
    assert read_raw(prediction_file)[0]["prediction"]["predictions"]["advice"].startswith("Double chance")


def test_raw_files_are_append_only(tmp_path):
    client, _ = busy_client()
    archive, collector = make(tmp_path, client)
    collector.run(NOW)
    first = {path: path.read_bytes() for path in raw_files(tmp_path)}

    later = NOW + timedelta(hours=1)
    client.fixtures_by_date[later.date().isoformat()] = client.fixtures_by_date[today_key()]
    _, collector2 = make(tmp_path, client, now=later)
    collector2.run(later)

    after = {path: path.read_bytes() for path in raw_files(tmp_path)}
    assert all(after[path] == data for path, data in first.items())  # nothing rewritten
    assert len(after) > len(first)  # the second run added its own files

    with pytest.raises(FileExistsError):
        archive.write_raw("fixtures", "20261003T090000Z", NOW, [{"fixture": {"id": 1}}])


def test_run_with_nothing_to_store_writes_no_raw_files_but_logs_the_run(tmp_path):
    client = FakeClient()
    archive, collector = make(tmp_path, client)
    report = collector.run(NOW)

    assert report.stopped_reason == "completed"
    assert not (tmp_path / "raw").exists()
    assert archive.summary()["runs"] == 1


def test_archive_can_be_reopened(tmp_path):
    PrematchArchive(tmp_path)
    archive = PrematchArchive(tmp_path)
    assert archive.summary()["runs"] == 0


def test_a_fixture_not_listed_in_this_run_is_not_acted_on(tmp_path):
    # A fixture moved to another date drops out of the listing; its stored row is
    # stale and must cost neither a line-up nor a prediction request.
    tomorrow = (NOW + timedelta(days=1)).date().isoformat()
    client = FakeClient()
    soon = listing(100, kickoff=NOW + timedelta(hours=3))
    later = listing(200, kickoff=NOW + timedelta(hours=31))
    client.fixtures_by_date[today_key()] = [soon]
    client.fixtures_by_date[tomorrow] = [later]
    client.by_id[100] = with_lineups(soon)
    client.predictions[100] = [prediction()]
    client.predictions[200] = [prediction()]
    _, collector = make(tmp_path, client)
    collector.run(NOW)
    assert [int(p["fixture"]) for p in client.calls_to("/predictions")] == [100]  # 200 is 31 h away

    second = NOW + timedelta(hours=2, minutes=30)  # 100 kicks off in 30 min, 200 is 28.5 h away
    client.fixtures_by_date = {}
    _, collector2 = make(tmp_path, client, now=second)
    collector2.run(second)
    assert client.calls_to("/fixtures", "ids") == []
    assert [int(p["fixture"]) for p in client.calls_to("/predictions")] == [100]

    third = second + timedelta(minutes=5)  # listed again: now both are due
    client.fixtures_by_date[today_key()] = [soon]
    client.fixtures_by_date[tomorrow] = [later]
    _, collector3 = make(tmp_path, client, now=third)
    collector3.run(third)
    assert len(client.calls_to("/fixtures", "ids")) == 1
    assert [int(p["fixture"]) for p in client.calls_to("/predictions")] == [100, 200]


def test_tbd_fixtures_are_stored_but_never_acted_on(tmp_path):
    # A TBD kickoff is a placeholder, so it cannot anchor a line-up or prediction window.
    client = FakeClient()
    obj = listing(1, kickoff=NOW + timedelta(minutes=50), status="TBD")
    client.fixtures_by_date[today_key()] = [obj]
    client.by_id[1] = with_lineups(obj)
    client.predictions[1] = [prediction()]
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    assert client.calls_to("/fixtures", "ids") == []
    assert client.calls_to("/predictions") == []
    assert 1 in archive.known_fixture_ids()


def test_first_capture_wins_at_the_archive_level(tmp_path):
    archive = PrematchArchive(tmp_path)
    kickoff = NOW + timedelta(minutes=40)
    block = parse_lineup_blocks(with_lineups(listing(1, kickoff=kickoff)))[10]
    archive.record_lineup_team(1, block, NOW, 40.0, None)
    changed = {**block, "team": {**block["team"], "formation": "5-4-1"}}
    archive.record_lineup_team(1, changed, NOW + timedelta(minutes=10), 30.0, None)
    with db(tmp_path) as conn:
        assert conn.execute("SELECT formation, fetched_at FROM lineups WHERE fixture_id=1").fetchall() == [
            ("4-3-3", NOW.isoformat(timespec="seconds"))
        ]
        assert conn.execute("SELECT COUNT(*) FROM lineup_players WHERE team_id=10").fetchone()[0] == 13

    parsed = parse_prediction(prediction())
    assert archive.record_prediction(1, kickoff.isoformat(timespec="seconds"), parsed, NOW, 40.0, None) is True
    later = {**parsed, "advice": "changed"}
    assert archive.record_prediction(1, kickoff.isoformat(timespec="seconds"), later, NOW + timedelta(minutes=5), 35.0, None) is False
    assert archive.prediction_as_of(1, NOW + timedelta(hours=1))["advice"] == parsed["advice"]


def test_lineup_as_of_is_point_in_time(tmp_path):
    client = FakeClient()
    lineup_setup(client, [55])
    archive, collector = make(tmp_path, client)
    collector.run(NOW)

    assert archive.lineup_as_of(100, NOW - timedelta(seconds=1)) == []
    known = archive.lineup_as_of(100, NOW)
    assert len(known) == 26 and {row["is_starter"] for row in known} == {0, 1}
