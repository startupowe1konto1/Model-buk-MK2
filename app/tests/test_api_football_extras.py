from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from model_buk.ingestion import api_football_extras
from model_buk.ingestion.api_football_extras import (
    ExtrasDownloader,
    ExtrasStore,
    PlayerPriority,
    build_extras_tables,
    injury_scope,
    parse_coach_spells,
    parse_injury_row,
    parse_sidelined_payload,
    parse_transfers,
    player_scope_from_history,
    team_ids_from_history,
    current_season_league_ids_from_history,
)
from model_buk.ingestion.api_football_history import LeagueSeason, RequestBudget

LS_2023 = LeagueSeason(999, "TESTA", "Test A", "Testland", 2023)
LS_2024 = LeagueSeason(999, "TESTA", "Test A", "Testland", 2024)
LS_2026 = LeagueSeason(999, "TESTA", "Test A", "Testland", 2026)

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = T0):
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


def injury_row(fixture_id, team_id, player_id, *, league=999, season=2023, reason="Knee Injury", kind="Missing Fixture", date="2023-08-11T19:00:00+00:00"):
    return {
        "player": {"id": player_id, "name": f"Player {player_id}", "photo": "x", "type": kind, "reason": reason},
        "team": {"id": team_id, "name": f"Team {team_id}", "logo": "x"},
        "fixture": {"id": fixture_id, "timezone": "UTC", "date": date, "timestamp": 1691780400},
        "league": {"id": league, "season": season, "name": "Test A", "country": "Testland"},
    }


def coach_entry(coach_id, team_id, career):
    return {
        "id": coach_id,
        "name": f"Coach {coach_id}",
        "team": {"id": team_id, "name": f"Team {team_id}"},
        "career": [
            {"team": {"id": tid, "name": f"Team {tid}"}, "start": start, "end": end} for tid, start, end in career
        ],
    }


def transfer_entry(player_id, transfers, *, update="2023-04-05T04:02:27+00:00"):
    return {
        "player": {"id": player_id, "name": f"Player {player_id}"},
        "update": update,
        "transfers": [
            {
                "date": date,
                "type": kind,
                "teams": {
                    "in": None if t_in is None else {"id": t_in, "name": f"Team {t_in}"},
                    "out": None if t_out is None else {"id": t_out, "name": f"Team {t_out}"},
                },
            }
            for date, kind, t_in, t_out in transfers
        ],
    }


class FakeClient:
    """Canned offline API-Football responses; records every call."""

    def __init__(self, *, quota_start=10_000, injuries=None, coaches=None, transfers=None, sidelined=None, fail=(), single_shape=False):
        self.quota = quota_start
        self.injuries = injuries or {}
        self.coaches = coaches or {}
        self.transfers = transfers or {}
        self.sidelined = sidelined or {}
        self.fail = set(fail)
        self.single_shape = single_shape
        self.calls: list[tuple[str, dict]] = []

    def _envelope(self, endpoint, params, response, page=1, total=1):
        return {
            "get": endpoint.strip("/"),
            "parameters": {k: str(v) for k, v in params.items()},
            "errors": [],
            "results": len(response),
            "paging": {"current": page, "total": total},
            "response": response,
        }

    def get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == "/status":  # free: does not use up quota
            body = {"response": {"requests": {"current": 7500 - self.quota, "limit_day": 7500}}}
            return body, {"x-ratelimit-requests-remaining": str(self.quota)}
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if endpoint == "/injuries":
            key = (int(params["league"]), int(params["season"]))
            pages = self.injuries.get(
                key, [[injury_row(key[0] * 10 + 1, 1, 11, league=key[0], season=key[1]), injury_row(key[0] * 10 + 2, 2, 22, league=key[0], season=key[1])]]
            )
            page = int(params.get("page", 1))
            return self._envelope(endpoint, params, pages[page - 1], page, len(pages)), headers
        if endpoint == "/coachs":
            team = int(params["team"])
            if ("/coachs", team) in self.fail:
                raise RuntimeError(f"boom coachs {team}")
            entries = self.coaches.get(team, [coach_entry(team * 10, team, [(team, "2020-07-01", None)])])
            return self._envelope(endpoint, params, entries), headers
        if endpoint == "/transfers":
            team = int(params["team"])
            if ("/transfers", team) in self.fail:
                raise RuntimeError(f"boom transfers {team}")
            entries = self.transfers.get(team, [transfer_entry(team * 100, [("2021-07-01", "Free", team, 5)])])
            return self._envelope(endpoint, params, entries), headers
        if endpoint == "/sidelined":
            if "players" in params:
                ids = [int(x) for x in str(params["players"]).split("-")]
                assert len(ids) <= 20, "the API rejects more than 20 players per call"
                if self.single_shape and len(ids) == 1:
                    response = list(self.sidelined.get(ids[0], []))
                else:
                    response = [{"id": pid, "sidelined": self.sidelined[pid]} for pid in ids if pid in self.sidelined]
                return self._envelope(endpoint, params, response), headers
            raise AssertionError(f"unexpected params: {params}")
        raise AssertionError(f"unexpected endpoint: {endpoint}")


def read_gz(path):
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def make(tmp_path, client=None, *, max_requests=10_000, reserve=0, clock=None):
    store = ExtrasStore(tmp_path / "extras")
    client = client or FakeClient()
    budget = RequestBudget(max_requests=max_requests, reserve=reserve)
    downloader = ExtrasDownloader(client, store, budget, now=clock or Clock())
    return store, client, budget, downloader


# --------------------------------------------------------------------------
# Module contract
# --------------------------------------------------------------------------


def test_module_docstring_warns_that_injuries_are_retrospective():
    doc = api_football_extras.__doc__
    assert "RETROSPECTIVE" in doc
    assert "leak" in doc.lower()
    assert "RETROSPECTIVE" in api_football_extras.RETROSPECTIVE_WARNING


# --------------------------------------------------------------------------
# Injuries: one request per league-season, paging, resumability, refresh
# --------------------------------------------------------------------------


def test_injuries_one_request_per_league_season_and_rerun_is_free(tmp_path):
    store, client, budget, downloader = make(tmp_path)

    report = downloader.run_injuries([LS_2023, LS_2024], current_season=2026)

    assert report.stopped_reason == "completed"
    assert report.requests_spent == 2
    assert (report.targets_total, report.already_done, report.completed, report.remaining) == (2, 0, 2, 0)
    assert client.calls == [
        ("/injuries", {"league": 999, "season": 2023}),
        ("/injuries", {"league": 999, "season": 2024}),
    ]
    files = sorted((tmp_path / "extras" / "raw" / "injuries").rglob("*.json.gz"))
    assert len(files) == 2
    assert all(re.search(r"_p001of001\.json\.gz$", f.name) for f in files)
    # The raw payload is archived verbatim.
    payload = read_gz(files[0])
    assert payload["paging"] == {"current": 1, "total": 1}
    assert payload["response"][0]["player"]["reason"] == "Knee Injury"

    client2 = FakeClient()
    report2 = ExtrasDownloader(client2, store, RequestBudget(max_requests=100), now=Clock()).run_injuries(
        [LS_2023, LS_2024], current_season=2026
    )
    assert client2.calls == []
    assert (report2.requests_spent, report2.already_done, report2.completed) == (0, 2, 0)


def test_injuries_empty_response_is_archived_and_not_refetched(tmp_path):
    store, client, _, downloader = make(tmp_path, FakeClient(injuries={(999, 2023): [[]]}))
    downloader.run_injuries([LS_2023], current_season=2026)
    assert len(client.calls) == 1
    client2 = FakeClient()
    ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()).run_injuries(
        [LS_2023], current_season=2026
    )
    assert client2.calls == []


def test_injuries_paging_loops_every_page(tmp_path):
    pages = [
        [injury_row(1, 1, 11)],
        [injury_row(2, 1, 12)],
        [injury_row(3, 1, 13)],
    ]
    store, client, _, downloader = make(tmp_path, FakeClient(injuries={(999, 2023): pages}))

    report = downloader.run_injuries([LS_2023], current_season=2026)

    assert report.completed == 1 and report.requests_spent == 3
    assert [c[1] for c in client.calls] == [
        {"league": 999, "season": 2023},
        {"league": 999, "season": 2023, "page": 2},
        {"league": 999, "season": 2023, "page": 3},
    ]
    names = sorted(p.name for p in (tmp_path / "extras" / "raw" / "injuries").rglob("*.json.gz"))
    assert [n.split("_", 1)[1] for n in names] == ["p001of003.json.gz", "p002of003.json.gz", "p003of003.json.gz"]


def test_paging_interrupted_by_budget_resumes_same_run_without_repeating_pages(tmp_path):
    pages = [[injury_row(i, 1, 10 + i)] for i in range(1, 4)]
    clock = Clock()
    store = ExtrasStore(tmp_path / "extras")
    client = FakeClient(injuries={(999, 2023): pages})
    first = ExtrasDownloader(client, store, RequestBudget(max_requests=2), now=clock).run_injuries(
        [LS_2023], current_season=2026
    )
    assert first.stopped_reason == "budget_exhausted"
    assert (first.completed, first.remaining, first.requests_spent) == (0, 1, 2)

    clock.advance(hours=5)
    client2 = FakeClient(injuries={(999, 2023): pages})
    second = ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=clock).run_injuries(
        [LS_2023], current_season=2026
    )
    assert second.stopped_reason == "completed" and second.completed == 1
    # Only the missing page was requested.
    assert [c[1] for c in client2.calls] == [{"league": 999, "season": 2023, "page": 3}]
    files = sorted((tmp_path / "extras" / "raw" / "injuries").rglob("*.json.gz"))
    assert len(files) == 3
    # All three pages belong to the one fetch started in the first run.
    assert len({f.name.split("_")[0] for f in files}) == 1


def test_page_file_left_by_a_crash_is_reused_instead_of_requested_again(tmp_path):
    pages = [[injury_row(1, 1, 11)], [injury_row(2, 1, 12)]]
    store, client, _, downloader = make(tmp_path, FakeClient(injuries={(999, 2023): pages}))
    downloader.run_injuries([LS_2023], current_season=2026)
    assert len(client.calls) == 2
    # Simulate a crash after the raw write but before the state commit.
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.execute("UPDATE fetch_runs SET pages_done = 1, complete = 0, completed_at = NULL")
        conn.commit()
    client2 = FakeClient(injuries={(999, 2023): pages})
    report = ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()).run_injuries(
        [LS_2023], current_season=2026
    )
    assert client2.calls == []
    assert report.completed == 1


def test_max_requests_stops_and_resume_finishes_the_rest(tmp_path):
    scope = [LeagueSeason(900 + i, f"L{i}", "L", "X", 2023) for i in range(5)]
    store, client, _, downloader = make(tmp_path, max_requests=3)
    report = downloader.run_injuries(scope, current_season=2026)
    assert report.stopped_reason == "budget_exhausted"
    assert (report.completed, report.remaining, report.requests_spent) == (3, 2, 3)

    client2 = FakeClient()
    report2 = ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()).run_injuries(
        scope, current_season=2026
    )
    assert (report2.already_done, report2.completed, report2.requests_spent) == (3, 2, 2)
    assert {c[1]["league"] for c in client2.calls} == {903, 904}


def test_quota_reserve_from_response_header_stops_the_run(tmp_path):
    scope = [LeagueSeason(900 + i, f"L{i}", "L", "X", 2023) for i in range(5)]
    _, client, budget, downloader = make(tmp_path, FakeClient(quota_start=1002), reserve=1000)
    report = downloader.run_injuries(scope, current_season=2026)
    assert report.stopped_reason == "budget_exhausted"
    assert report.requests_spent == 2  # header showed 1001, then 1000 which is the reserve floor
    assert budget.remaining_quota == 1000


def test_current_season_refresh_writes_a_new_timestamped_file_and_never_overwrites(tmp_path):
    clock = Clock()
    store = ExtrasStore(tmp_path / "extras")
    scope = [LS_2023, LS_2026]
    ExtrasDownloader(FakeClient(), store, RequestBudget(max_requests=10), now=clock).run_injuries(
        scope, current_season=2026
    )
    raw = tmp_path / "extras" / "raw" / "injuries"
    original = {p: p.read_bytes() for p in raw.rglob("*.json.gz")}
    assert len(original) == 2

    # Too early for a refresh: nothing happens.
    clock.advance(days=3)
    early = FakeClient()
    ExtrasDownloader(early, store, RequestBudget(max_requests=10), now=clock).run_injuries(
        scope, current_season=2026, refresh_after_days=7
    )
    assert early.calls == []

    # Later: only the running season is refreshed; finished seasons never are.
    clock.advance(days=5)
    refreshed = FakeClient(injuries={(999, 2026): [[injury_row(77, 1, 99, season=2026)]]})
    report = ExtrasDownloader(refreshed, store, RequestBudget(max_requests=10), now=clock).run_injuries(
        scope, current_season=2026, refresh_after_days=7
    )
    assert refreshed.calls == [("/injuries", {"league": 999, "season": 2026})]
    assert report.completed == 1 and report.already_done == 1
    after = {p: p.read_bytes() for p in raw.rglob("*.json.gz")}
    assert len(after) == 3
    for path, content in original.items():
        assert after[path] == content  # old raw files are byte-identical

    # Without --refresh-days the running season is treated like any other.
    plain = FakeClient()
    ExtrasDownloader(plain, store, RequestBudget(max_requests=10), now=clock).run_injuries(
        scope, current_season=2026
    )
    assert plain.calls == []


def test_raw_files_are_append_only(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    target = store.page_path("injuries", "999:2023", "20260929T120000Z", 1, 1)
    first = store.write_raw(target, {"response": [1]})
    original = first.read_bytes()
    second = store.write_raw(target, {"response": [2]})
    assert second != first and second.exists()
    assert first.read_bytes() == original  # the earlier file was not touched
    assert read_gz(second) == {"response": [2]}
    assert list(target.parent.glob("*.tmp")) == []


def test_a_new_fetch_never_reuses_or_precedes_an_existing_stamp(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    now = T0
    first = store.open_run("injuries", "999:2026", now)
    store.record_page(first, 1, 1, 0, True, now)
    second = store.open_run("injuries", "999:2026", now)  # same second: must still be a distinct, later fetch
    assert second.stamp > first.stamp and not second.complete
    # An unfinished fetch is resumed, not restarted.
    assert store.open_run("injuries", "999:2026", now + timedelta(hours=1)).stamp == second.stamp


# --------------------------------------------------------------------------
# Coaches / transfers: one request per team
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, endpoint, method",
    [("coaches", "/coachs", "run_coaches"), ("transfers", "/transfers", "run_transfers")],
)
def test_team_kinds_one_request_per_team_and_rerun_is_free(tmp_path, kind, endpoint, method):
    store, client, _, downloader = make(tmp_path)
    report = getattr(downloader, method)([33, 34, 35])
    assert report.stopped_reason == "completed"
    assert report.requests_spent == 3
    assert client.calls == [(endpoint, {"team": 33}), (endpoint, {"team": 34}), (endpoint, {"team": 35})]
    assert len(list((tmp_path / "extras" / "raw" / kind).rglob("*.json.gz"))) == 3

    client2 = FakeClient()
    report2 = getattr(ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()), method)([33, 34, 35])
    assert client2.calls == []
    assert (report2.already_done, report2.completed) == (3, 0)


def test_a_failing_team_is_skipped_recorded_and_retried_only_a_few_times(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    for attempt in range(1, 4):
        client = FakeClient(fail={("/coachs", 34)})
        report = ExtrasDownloader(client, store, RequestBudget(max_requests=50), now=Clock()).run_coaches([33, 34, 35])
        assert report.stopped_reason == "completed"
        assert report.completed == (2 if attempt == 1 else 0)
        assert report.remaining == (1 if attempt < 3 else 0)
        assert report.failed == (1 if attempt == 3 else 0)
    # After MAX_ATTEMPTS failures the team is left alone even if the API recovers.
    healthy = FakeClient()
    report = ExtrasDownloader(healthy, store, RequestBudget(max_requests=50), now=Clock()).run_coaches([33, 34, 35])
    assert healthy.calls == []
    assert report.failed == 1


def test_a_success_clears_earlier_failures_so_blips_do_not_add_up_over_time(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    for _ in range(api_football_extras.MAX_ATTEMPTS - 1):
        ExtrasDownloader(
            FakeClient(fail={("/coachs", 34)}), store, RequestBudget(max_requests=50), now=Clock()
        ).run_coaches([33, 34])
    assert store.error_attempts("coaches") == {"34": api_football_extras.MAX_ATTEMPTS - 1}
    report = ExtrasDownloader(FakeClient(), store, RequestBudget(max_requests=50), now=Clock()).run_coaches([33, 34])
    assert report.completed == 1 and report.failed == 0
    assert store.error_attempts("coaches") == {}


def test_many_consecutive_errors_abort_the_kind(tmp_path):
    teams = list(range(1, 12))
    client = FakeClient(fail={("/transfers", t) for t in teams})
    _, _, _, downloader = make(tmp_path, client)
    report = downloader.run_transfers(teams)
    assert report.stopped_reason == "errors"
    assert len(client.calls) == api_football_extras.MAX_CONSECUTIVE_ERRORS


# --------------------------------------------------------------------------
# Sidelined: batches of players, priority, empty players, resumability
# --------------------------------------------------------------------------


def _players():
    players = []
    for pid in range(1, 46):
        if pid <= 5:
            players.append(PlayerPriority(pid, False, 2025, 100 + pid))  # not top-20: last
        elif pid <= 25:
            players.append(PlayerPriority(pid, True, 2025, pid))
        else:
            players.append(PlayerPriority(pid, True, 2024, 1000 + pid))  # older season: after 2025
    return players


def test_sidelined_batches_of_20_in_priority_order_and_empty_players_are_not_retried(tmp_path):
    data = {
        7: [{"type": "Knee Injury", "start": "2025-01-01", "end": "2025-02-01"}],
        30: [{"type": "Virus", "start": "2024-03-01", "end": None}],
        3: [{"type": "Ankle Injury", "start": "2024-04-01", "end": "2024-04-20"}],
    }
    store, client, _, downloader = make(tmp_path, FakeClient(sidelined=data))
    report = downloader.run_sidelined(_players())

    assert report.stopped_reason == "completed"
    assert report.requests_spent == 3  # 45 players -> 20 + 20 + 5
    batches = [[int(x) for x in c[1]["players"].split("-")] for c in client.calls]
    assert [len(b) for b in batches] == [20, 20, 5]
    # Top-20 leagues first, most recent season first, then most appearances.
    assert set(batches[0]) == set(range(6, 26))
    assert batches[0][0] == 25 and batches[0][-1] == 6
    assert set(batches[1]) == set(range(26, 46))
    assert set(batches[2]) == {1, 2, 3, 4, 5}
    counts = store.sidelined_counts()
    assert counts == {"done": 3, "empty": 42, "pending": 0, "failed": 0}
    assert (report.targets_total, report.completed, report.remaining) == (45, 45, 0)

    client2 = FakeClient(sidelined=data)
    report2 = ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()).run_sidelined(_players())
    assert client2.calls == []
    assert (report2.already_done, report2.completed) == (45, 0)


def test_sidelined_budget_stop_resumes_where_it_left_off(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    first = ExtrasDownloader(FakeClient(), store, RequestBudget(max_requests=1), now=Clock()).run_sidelined(_players())
    assert first.stopped_reason == "budget_exhausted"
    assert first.completed == 20 and first.remaining == 25
    client2 = FakeClient()
    second = ExtrasDownloader(client2, store, RequestBudget(max_requests=10), now=Clock()).run_sidelined(_players())
    assert second.stopped_reason == "completed"
    assert len(client2.calls) == 2
    sent = {int(x) for c in client2.calls for x in c[1]["players"].split("-")}
    assert sent == set(range(1, 46)) - set(range(6, 26))  # the first 20 were not asked for again


def test_sidelined_new_players_join_the_queue_later(tmp_path):
    store = ExtrasStore(tmp_path / "extras")
    first = [PlayerPriority(i, True, 2025, 10) for i in range(1, 6)]
    ExtrasDownloader(FakeClient(), store, RequestBudget(max_requests=10), now=Clock()).run_sidelined(first)
    client = FakeClient()
    more = first + [PlayerPriority(99, True, 2026, 3)]
    report = ExtrasDownloader(client, store, RequestBudget(max_requests=10), now=Clock()).run_sidelined(more)
    assert [c[1] for c in client.calls] == [{"players": "99"}]
    assert (report.already_done, report.completed) == (5, 1)


def test_sidelined_failed_batch_is_counted_and_players_skipped_after_max_attempts(tmp_path):
    class Exploding(FakeClient):
        def get(self, endpoint, params):
            self.calls.append((endpoint, dict(params)))
            raise RuntimeError("network down")

    store = ExtrasStore(tmp_path / "extras")
    players = [PlayerPriority(i, True, 2025, 10) for i in range(1, 4)]
    for _ in range(api_football_extras.MAX_ATTEMPTS):
        report = ExtrasDownloader(Exploding(), store, RequestBudget(max_requests=50), now=Clock()).run_sidelined(players)
        assert report.completed == 0
    healthy = FakeClient()
    report = ExtrasDownloader(healthy, store, RequestBudget(max_requests=50), now=Clock()).run_sidelined(players)
    assert healthy.calls == []
    assert report.failed == 3


def test_pending_players_copes_with_a_huge_queue_and_a_huge_exclusion_set(tmp_path):
    # Regression: an exclusion list passed as ``NOT IN (?, ?, ...)`` overflowed SQLite's variable
    # limit once tens of thousands of players had been processed in one run.
    store = ExtrasStore(tmp_path / "extras")
    store.register_players(PlayerPriority(pid, True, 2025, 1000 - pid % 1000) for pid in range(1, 60_001))
    excluded = set(range(1, 40_001))
    batch = store.pending_players(20, exclude=excluded)
    assert len(batch) == 20 and not (set(batch) & excluded)


def test_a_failing_sidelined_batch_does_not_stop_the_rest_of_the_run(tmp_path):
    class FlakyOnce(FakeClient):
        def get(self, endpoint, params):
            if endpoint == "/sidelined" and not any(c[0] == "/sidelined" for c in self.calls):
                self.calls.append((endpoint, dict(params)))
                raise RuntimeError("timeout")
            return super().get(endpoint, params)

    players = [PlayerPriority(i, True, 2025, 100 - i) for i in range(1, 46)]
    client = FlakyOnce()
    store = ExtrasStore(tmp_path / "extras")
    report = ExtrasDownloader(client, store, RequestBudget(max_requests=50), now=Clock()).run_sidelined(players)
    # The first batch (players 1..20) failed once and is left for a later run; 21..45 were fetched.
    assert report.stopped_reason == "completed"
    assert (report.completed, report.remaining, report.failed) == (25, 20, 0)
    assert len(client.calls) == 3  # 1 failed + 2 fetched, no immediate retry of the failed batch
    later = FakeClient()
    report2 = ExtrasDownloader(later, store, RequestBudget(max_requests=50), now=Clock()).run_sidelined(players)
    assert report2.completed == 20 and report2.remaining == 0
    assert {int(x) for x in later.calls[0][1]["players"].split("-")} == set(range(1, 21))


def test_parse_sidelined_payload_handles_batch_and_single_player_shapes():
    batch = {"response": [{"id": 5, "sidelined": [{"type": "A", "start": "2024-01-01", "end": None}]}, {"id": 6, "sidelined": []}]}
    assert parse_sidelined_payload(batch, requested_ids=[5, 6, 7]) == {
        5: [{"type": "A", "start": "2024-01-01", "end": None}],
        6: [],
    }
    single = {"response": [{"type": "B", "start": "2024-02-01", "end": "2024-02-09"}]}
    assert parse_sidelined_payload(single, requested_ids=[9]) == {
        9: [{"type": "B", "start": "2024-02-01", "end": "2024-02-09"}]
    }
    assert parse_sidelined_payload({"response": []}, requested_ids=[9]) == {}
    assert parse_sidelined_payload({}, requested_ids=[1, 2]) == {}


# --------------------------------------------------------------------------
# Scope helpers
# --------------------------------------------------------------------------


def test_injury_scope_covers_2021_to_running_season_leagues_first_seasons_descending():
    scope = injury_scope(current_season=2026, current_season_league_ids={39, 40})
    assert all(ls.season >= 2021 for ls in scope)
    assert scope[0].league_id == 39 and [ls.season for ls in scope[:6]] == [2026, 2025, 2024, 2023, 2022, 2021]
    assert (39, 2026) in {(ls.league_id, ls.season) for ls in scope}
    assert (140, 2026) not in {(ls.league_id, ls.season) for ls in scope}  # no 2026 matches archived
    assert (140, 2025) in {(ls.league_id, ls.season) for ls in scope}
    keys = [(ls.league_id, ls.season) for ls in scope]
    assert len(keys) == len(set(keys))

    everything = injury_scope(current_season=2026)
    assert (140, 2026) in {(ls.league_id, ls.season) for ls in everything}


@pytest.fixture
def history_db(tmp_path):
    path = tmp_path / "history.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE matches (fixture_id INTEGER PRIMARY KEY, league_id INTEGER, season INTEGER,
                kickoff_utc TEXT, home_team_id INTEGER, away_team_id INTEGER);
            CREATE TABLE lineup_players (fixture_id INTEGER NOT NULL, team_id INTEGER NOT NULL,
                player_id INTEGER, player_name TEXT, is_starter INTEGER);
            """
        )
        matches = [
            (1, 39, 2022, "2022-09-01T18:00:00+00:00", 10, 11),   # top-20, before the 2023 cut-off
            (2, 39, 2024, "2024-09-01T18:00:00+00:00", 10, 12),
            (3, 999, 2025, "2025-09-01T18:00:00+00:00", 20, 21),  # not top-20
            (4, 999, 2026, "2026-09-01T18:00:00+00:00", 20, 22),
            (5, 39, 2026, "2026-09-05T18:00:00+00:00", 12, 10),
        ]
        conn.executemany("INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?)", matches)
        lineups = [
            (1, 10, 100, "a", 1), (1, 11, 101, "b", 1),
            (2, 10, 100, "a", 1), (2, 12, 102, "c", 0),
            (3, 20, 200, "d", 1), (3, 21, 201, "e", 1),
            (4, 20, 200, "d", 1), (4, 22, None, "unknown", 1),
            (5, 12, 102, "c", 1), (5, 10, 100, "a", 1), (5, 10, 103, "f", 0),
        ]
        conn.executemany("INSERT INTO lineup_players VALUES (?, ?, ?, ?, ?)", lineups)
        conn.commit()
    return path


def test_team_ids_from_history_orders_top20_first_then_most_recent(history_db):
    # 10, 12 and 11 played in league 39 (top-20): 10 and 12 share the latest kickoff (team id breaks
    # the tie), 11 last played in 2022. Then the other teams by recency: 20 and 22 (2026), 21 (2025).
    assert team_ids_from_history(history_db) == [10, 12, 11, 20, 22, 21]


def test_current_season_league_ids_from_history(history_db):
    assert current_season_league_ids_from_history(history_db, 2026) == {39, 999}
    assert current_season_league_ids_from_history(history_db, 2024) == {39}


def test_player_scope_from_history_filters_by_date_and_ranks_by_priority(history_db):
    players = player_scope_from_history(history_db, since="2023-01-01")
    assert players == [
        PlayerPriority(100, True, 2026, 2),   # two appearances since the cut-off, latest 2026, top-20
        PlayerPriority(102, True, 2026, 2),
        PlayerPriority(103, True, 2026, 1),
        PlayerPriority(200, False, 2026, 2),
        PlayerPriority(201, False, 2025, 1),
    ]
    assert 101 not in {p.player_id for p in players}
    assert 101 in {p.player_id for p in player_scope_from_history(history_db, since="2020-01-01")}


# --------------------------------------------------------------------------
# Parsers keep NULL as NULL
# --------------------------------------------------------------------------


def test_parse_injury_row_normalises_time_and_keeps_missing_values_null():
    row = injury_row(5, 44, 18957, date="2023-08-11T21:00:00+02:00", reason=None)
    parsed = parse_injury_row(row, fetched_at="2026-09-29T12:00:00+00:00", default_league_id=999, default_season=2023)
    assert parsed == {
        "fixture_id": 5,
        "league_id": 999,
        "season": 2023,
        "team_id": 44,
        "player_id": 18957,
        "player_name": "Player 18957",
        "type": "Missing Fixture",
        "reason": None,
        "fixture_date_utc": "2023-08-11T19:00:00+00:00",
        "fetched_at": "2026-09-29T12:00:00+00:00",
    }
    bare = parse_injury_row({"player": {"id": 1}}, fetched_at="t", default_league_id=None, default_season=None)
    assert bare["fixture_id"] is None and bare["team_id"] is None and bare["fixture_date_utc"] is None
    assert bare["player_name"] is None and bare["type"] is None and bare["league_id"] is None


def test_parse_coach_spells_and_transfers_keep_gaps_null():
    entry = coach_entry(7, 33, [(33, "2022-06-01", None), (None, "2018-01-01", "2022-05-01")])
    spells = parse_coach_spells(entry, fetched_at="t")
    assert spells == [
        {"coach_id": 7, "coach_name": "Coach 7", "team_id": 33, "start_date": "2022-06-01", "end_date": None, "fetched_at": "t"},
        {"coach_id": 7, "coach_name": "Coach 7", "team_id": None, "start_date": "2018-01-01", "end_date": "2022-05-01", "fetched_at": "t"},
    ]
    assert parse_coach_spells({"id": 8, "name": "X", "career": None}, fetched_at="t") == []
    assert parse_coach_spells({"name": "no id"}, fetched_at="t") == []

    player = transfer_entry(9, [("2021-07-01", "€ 5M", 33, 44), (None, None, None, 44)])
    rows = parse_transfers(player, fetched_at="t")
    assert rows == [
        {"player_id": 9, "player_name": "Player 9", "transfer_date": "2021-07-01", "type": "€ 5M", "team_in_id": 33, "team_out_id": 44, "fetched_at": "t"},
        {"player_id": 9, "player_name": "Player 9", "transfer_date": None, "type": None, "team_in_id": None, "team_out_id": 44, "fetched_at": "t"},
    ]
    assert parse_transfers({"transfers": []}, fetched_at="t") == []


def test_junk_in_date_fields_becomes_null_but_real_dates_survive():
    # The live archive has a coach spell that "starts" on 'Winner' and one that "ends" on '1x'.
    junk = coach_entry(7, 33, [(33, "Winner", "1x"), (34, "2020-02-30", "2021-01-05")])
    spells = parse_coach_spells(junk, fetched_at="t")
    assert [(s["start_date"], s["end_date"]) for s in spells] == [(None, None), (None, "2021-01-05")]
    move = parse_transfers(transfer_entry(9, [("garbage", "Free", 1, 2)]), fetched_at="t")
    assert move[0]["transfer_date"] is None and move[0]["type"] == "Free"
    records = parse_sidelined_payload(
        {"response": [{"id": 5, "sidelined": [{"type": "Knee", "start": "n/a", "end": "2024-05-01"}]}]}, [5]
    )
    assert records == {5: [{"type": "Knee", "start": None, "end": "2024-05-01"}]}


# --------------------------------------------------------------------------
# Table build
# --------------------------------------------------------------------------


def _rows(path, sql, params=()):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _fill_archive(tmp_path):
    """Run every kind against the fake client and return (root, client)."""

    clock = Clock()
    client = FakeClient(
        injuries={
            (999, 2023): [
                [
                    injury_row(1, 1, 11, date="2023-08-11T21:00:00+02:00"),
                    injury_row(2, 1, 12, reason=None, kind="Questionable"),
                    {"player": {"id": 13, "name": "No Team"}, "fixture": {"id": 3}, "league": {"id": 999, "season": 2023}},
                ]
            ],
        },
        coaches={
            33: [coach_entry(7, 33, [(33, "2022-06-01", None), (194, "2018-01-01", "2022-05-01")])],
            34: [
                coach_entry(8, 34, [(34, "2021-01-01", "2023-01-01")]),
                # The same coach 7 comes back through his former club, with the same career.
                coach_entry(7, 34, [(33, "2022-06-01", None), (194, "2018-01-01", "2022-05-01")]),
            ],
        },
        transfers={
            33: [transfer_entry(9, [("2021-07-01", "€ 5M", 33, 44), ("2019-01-01", "Loan", 44, 50)])],
            34: [
                transfer_entry(9, [("2021-07-01", "€ 5M", 33, 44), ("2019-01-01", "Loan", 44, 50)]),
                transfer_entry(10, [(None, "N/A", None, 34)]),
            ],
        },
        sidelined={
            11: [{"type": "Knee Injury", "start": "2023-08-01", "end": "2023-09-01"}, {"type": "Virus", "start": "2024-01-01", "end": None}],
            12: [{"type": "Ankle Injury", "start": "2023-05-01", "end": "2023-05-20"}],
        },
    )
    store = ExtrasStore(tmp_path / "extras")
    downloader = ExtrasDownloader(client, store, RequestBudget(max_requests=1000), now=clock)
    downloader.run_injuries([LS_2023], current_season=2026)
    downloader.run_coaches([33, 34])
    downloader.run_transfers([33, 34])
    downloader.run_sidelined([PlayerPriority(11, True, 2025, 5), PlayerPriority(12, True, 2025, 4), PlayerPriority(13, True, 2025, 3)])
    return tmp_path / "extras", clock


def test_build_tables_from_the_archive(tmp_path):
    root, _ = _fill_archive(tmp_path)
    out = root / "tables" / "extras.sqlite"
    report = build_extras_tables(root, out, now=lambda: "2026-09-30T00:00:00+00:00")

    injuries = _rows(out, "SELECT * FROM injuries ORDER BY fixture_id")
    assert [r["fixture_id"] for r in injuries] == [1, 2, 3]
    assert injuries[0] == {
        "fixture_id": 1, "league_id": 999, "season": 2023, "team_id": 1, "player_id": 11,
        "player_name": "Player 11", "type": "Missing Fixture", "reason": "Knee Injury",
        "fixture_date_utc": "2023-08-11T19:00:00+00:00", "fetched_at": "2026-09-29T12:00:00+00:00",
    }
    assert injuries[1]["reason"] is None and injuries[1]["type"] == "Questionable"
    assert injuries[2]["team_id"] is None and injuries[2]["reason"] is None and injuries[2]["fixture_date_utc"] is None

    spells = _rows(out, "SELECT coach_id, team_id, start_date, end_date FROM coach_spells ORDER BY coach_id, start_date")
    # Coach 7 arrived through two team responses but is stored once.
    assert spells == [
        {"coach_id": 7, "team_id": 194, "start_date": "2018-01-01", "end_date": "2022-05-01"},
        {"coach_id": 7, "team_id": 33, "start_date": "2022-06-01", "end_date": None},
        {"coach_id": 8, "team_id": 34, "start_date": "2021-01-01", "end_date": "2023-01-01"},
    ]

    transfers = _rows(out, "SELECT player_id, transfer_date, type, team_in_id, team_out_id FROM transfers ORDER BY player_id, transfer_date")
    assert transfers == [
        {"player_id": 9, "transfer_date": "2019-01-01", "type": "Loan", "team_in_id": 44, "team_out_id": 50},
        {"player_id": 9, "transfer_date": "2021-07-01", "type": "€ 5M", "team_in_id": 33, "team_out_id": 44},
        {"player_id": 10, "transfer_date": None, "type": "N/A", "team_in_id": None, "team_out_id": 34},
    ]

    sidelined = _rows(out, "SELECT player_id, type, start_date, end_date FROM sidelined ORDER BY player_id, start_date")
    assert sidelined == [
        {"player_id": 11, "type": "Knee Injury", "start_date": "2023-08-01", "end_date": "2023-09-01"},
        {"player_id": 11, "type": "Virus", "start_date": "2024-01-01", "end_date": None},
        {"player_id": 12, "type": "Ankle Injury", "start_date": "2023-05-01", "end_date": "2023-05-20"},
    ]
    assert all(r["fetched_at"] for r in _rows(out, "SELECT fetched_at FROM sidelined"))

    assert report.row_counts == {"injuries": 3, "coach_spells": 3, "transfers": 3, "sidelined": 3}
    assert report.duplicates_skipped["coach_spells"] == 0  # replaced per coach, not appended
    (info,) = _rows(out, "SELECT * FROM build_info")
    assert info["schema_version"] == api_football_extras.SCHEMA_VERSION
    assert info["built_at"] == "2026-09-30T00:00:00+00:00"
    assert "RETROSPECTIVE" in info["retrospective_warning"]
    assert info["injuries_rows"] == 3 and info["sidelined_rows"] == 3


def test_build_tables_carry_the_retrospective_warning(tmp_path):
    root, _ = _fill_archive(tmp_path)
    out = root / "tables" / "extras.sqlite"
    build_extras_tables(root, out)
    notes = {r["table_name"]: r["note"] for r in _rows(out, "SELECT * FROM table_notes")}
    assert set(notes) == {"injuries", "coach_spells", "transfers", "sidelined"}
    assert "RETROSPECTIVE" in notes["injuries"] and "leak" in notes["injuries"].lower()
    assert "end_date" in notes["coach_spells"] and "end_date" in notes["sidelined"]
    assert "NULL" in notes["sidelined"]  # absence of a row is not "never injured"


def test_transfers_table_is_the_union_of_the_team_responses_not_the_newest_one(tmp_path):
    # Regression: /transfers?team=T lists only the moves that involve T (verified on the live archive:
    # 54k of 60k players seen in two or more team responses had a different move list in each), so
    # keeping only the newest response per player silently dropped about half of all moves.
    clock = Clock()
    store = ExtrasStore(tmp_path / "extras")
    shared = ("2021-07-01", "€ 5M", 33, 44)
    loan = ("2019-01-01", "Loan", 44, 50)
    old_free = ("2010-01-01", "Free", 60, 33)
    client = FakeClient(
        transfers={
            33: [transfer_entry(9, [shared, old_free])],
            44: [transfer_entry(9, [shared, loan])],
            50: [transfer_entry(9, [loan]), transfer_entry(10, [(None, "N/A", None, 50)])],
            51: [transfer_entry(10, [(None, "N/A", None, 50)])],
        }
    )
    downloader = ExtrasDownloader(client, store, RequestBudget(max_requests=50), now=clock)
    downloader.run_transfers([33])
    clock.advance(hours=1)
    downloader.run_transfers([44, 50, 51])

    out = tmp_path / "extras" / "tables" / "extras.sqlite"
    report = build_extras_tables(tmp_path / "extras", out)

    rows = _rows(out, "SELECT player_id, transfer_date, type, team_in_id, team_out_id, fetched_at FROM transfers ORDER BY player_id, transfer_date")
    assert [(r["player_id"], r["transfer_date"], r["type"], r["team_in_id"], r["team_out_id"]) for r in rows] == [
        (9, "2010-01-01", "Free", 60, 33),
        (9, "2019-01-01", "Loan", 44, 50),
        (9, "2021-07-01", "€ 5M", 33, 44),
        (10, None, "N/A", None, 50),  # listed twice with NULL date and NULL club: still one row
    ]
    fetched = {(r["player_id"], r["transfer_date"]): r["fetched_at"] for r in rows}
    assert fetched[(9, "2010-01-01")] == "2026-09-29T12:00:00+00:00"  # only the older response has it
    assert fetched[(9, "2021-07-01")] == "2026-09-29T13:00:00+00:00"  # listed twice: newest response wins
    assert report.row_counts["transfers"] == 4
    assert report.duplicates_skipped["transfers"] == 3  # shared + loan + player 10's second listing


def test_build_drops_exact_duplicate_injury_rows_and_counts_them(tmp_path):
    twin = injury_row(1, 1, 11)
    client = FakeClient(injuries={(999, 2023): [[twin, dict(twin), injury_row(2, 1, 12)]]})
    store, _, _, downloader = make(tmp_path, client)
    downloader.run_injuries([LS_2023], current_season=2026)
    out = tmp_path / "extras" / "tables" / "extras.sqlite"
    report = build_extras_tables(tmp_path / "extras", out)
    assert [r["fixture_id"] for r in _rows(out, "SELECT fixture_id FROM injuries ORDER BY fixture_id")] == [1, 2]
    assert report.duplicates_skipped["injuries"] == 1


def test_build_keeps_the_same_player_in_two_fixtures_and_a_reason_change_as_separate_injury_rows(tmp_path):
    client = FakeClient(
        injuries={(999, 2023): [[injury_row(1, 1, 11), injury_row(2, 1, 11), injury_row(1, 1, 11, reason="Illness")]]}
    )
    _, _, _, downloader = make(tmp_path, client)
    downloader.run_injuries([LS_2023], current_season=2026)
    out = tmp_path / "extras" / "tables" / "extras.sqlite"
    report = build_extras_tables(tmp_path / "extras", out)
    assert report.row_counts["injuries"] == 3 and report.duplicates_skipped["injuries"] == 0


def test_build_uses_only_the_latest_complete_fetch_and_ignores_incomplete_ones(tmp_path):
    clock = Clock()
    store = ExtrasStore(tmp_path / "extras")
    first = FakeClient(injuries={(999, 2026): [[injury_row(1, 1, 11, season=2026)]]})
    ExtrasDownloader(first, store, RequestBudget(max_requests=10), now=clock).run_injuries([LS_2026], current_season=2026)
    clock.advance(days=8)
    second = FakeClient(injuries={(999, 2026): [[injury_row(1, 1, 11, season=2026), injury_row(2, 1, 12, season=2026)]]})
    ExtrasDownloader(second, store, RequestBudget(max_requests=10), now=clock).run_injuries(
        [LS_2026], current_season=2026, refresh_after_days=7
    )
    # A third refresh is cut short after page 1 of 2: it must not shadow the complete one.
    clock.advance(days=8)
    pages = [[injury_row(9, 1, 19, season=2026)], [injury_row(10, 1, 20, season=2026)]]
    ExtrasDownloader(FakeClient(injuries={(999, 2026): pages}), store, RequestBudget(max_requests=1), now=clock).run_injuries(
        [LS_2026], current_season=2026, refresh_after_days=7
    )

    out = tmp_path / "extras" / "tables" / "extras.sqlite"
    report = build_extras_tables(tmp_path / "extras", out)
    rows = _rows(out, "SELECT fixture_id, fetched_at FROM injuries ORDER BY fixture_id")
    assert [r["fixture_id"] for r in rows] == [1, 2]
    assert {r["fetched_at"] for r in rows} == {"2026-10-07T12:00:00+00:00"}
    assert report.incomplete_runs == 1


def test_build_is_idempotent_and_fails_closed_on_an_empty_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_extras_tables(tmp_path / "nothing", tmp_path / "nothing" / "tables" / "extras.sqlite")
    assert not (tmp_path / "nothing" / "tables" / "extras.sqlite").exists()

    root, _ = _fill_archive(tmp_path)
    out = root / "tables" / "extras.sqlite"
    build_extras_tables(root, out)
    first = {t: _rows(out, f"SELECT * FROM {t} ORDER BY 1, 2, 3, 4") for t in ("injuries", "coach_spells", "transfers", "sidelined")}
    build_extras_tables(root, out)
    second = {t: _rows(out, f"SELECT * FROM {t} ORDER BY 1, 2, 3, 4") for t in ("injuries", "coach_spells", "transfers", "sidelined")}
    assert first == second


def test_failed_build_leaves_the_previous_database_untouched(tmp_path):
    root, _ = _fill_archive(tmp_path)
    out = root / "tables" / "extras.sqlite"
    build_extras_tables(root, out)
    before = out.read_bytes()
    # A corrupt raw file must abort the rebuild instead of producing a partial table.
    victim = next((root / "raw" / "coaches").rglob("*.json.gz"))
    victim_copy = victim.read_bytes()
    victim.write_bytes(b"not gzip at all")
    try:
        with pytest.raises(Exception):
            build_extras_tables(root, out)
    finally:
        victim.write_bytes(victim_copy)
    assert out.read_bytes() == before
    assert list(out.parent.glob("*.building")) == []


# --------------------------------------------------------------------------
# Command-line scripts (offline: the HTTP client is replaced by the fake)
# --------------------------------------------------------------------------


def _load_script(name):
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_download_cli(monkeypatch, script, fake, *args):
    monkeypatch.setattr(script, "RealHistoryClient", lambda key: fake)
    monkeypatch.setenv("API_FOOTBALL_KEY", "not-a-real-key")
    monkeypatch.setattr(sys, "argv", ["download_extras.py", *args])
    script.main()


def test_download_cli_runs_every_kind_then_the_build_cli_writes_the_tables(tmp_path, monkeypatch, capsys, history_db):
    script = _load_script("download_extras")
    data_dir = tmp_path / "extras"
    fake = FakeClient(quota_start=6000)
    _run_download_cli(
        monkeypatch, script, fake,
        "--data-dir", str(data_dir), "--history-db", str(history_db),
        "--kinds", "injuries,coaches,transfers,sidelined",
        "--max-requests", "5000", "--reserve", "100", "--sleep", "0",
    )
    out = capsys.readouterr().out
    for kind in ("injuries", "coaches", "transfers", "sidelined"):
        assert re.search(rf"^{kind}\s+requests=", out, re.MULTILINE), out
    assert "Requests spent this run:" in out and "Quota remaining now:" in out
    assert fake.calls[0][0] == "/status"  # the free quota preflight comes first
    assert sum(1 for c in fake.calls if c[0] == "/coachs") == 6  # six distinct teams in the fixture
    assert sum(1 for c in fake.calls if c[0] == "/sidelined") == 1  # five players fit one batch
    spent = len(fake.calls) - 1
    assert f"Requests spent this run: {spent}" in out

    # A rerun of the same command spends nothing.
    before = len(fake.calls)
    _run_download_cli(
        monkeypatch, script, fake,
        "--data-dir", str(data_dir), "--history-db", str(history_db),
        "--max-requests", "5000", "--reserve", "100", "--sleep", "0",
    )
    assert [c[0] for c in fake.calls[before:]] == ["/status"]

    build = _load_script("build_extras_tables")
    monkeypatch.setattr(sys, "argv", ["build_extras_tables.py", "--archive-root", str(data_dir)])
    build.main()
    printed = capsys.readouterr().out
    assert "Rows per table:" in printed and "RETROSPECTIVE" in printed
    tables = data_dir / "tables" / "extras.sqlite"
    assert tables.is_file()
    assert _rows(tables, "SELECT COUNT(*) AS n FROM injuries")[0]["n"] > 0
    assert _rows(tables, "SELECT COUNT(*) AS n FROM coach_spells")[0]["n"] == 6


def test_download_cli_does_nothing_when_quota_is_already_at_the_reserve(tmp_path, monkeypatch, capsys, history_db):
    script = _load_script("download_extras")
    fake = FakeClient(quota_start=1400)
    _run_download_cli(
        monkeypatch, script, fake,
        "--data-dir", str(tmp_path / "extras"), "--history-db", str(history_db),
        "--kinds", "coaches", "--reserve", "1500", "--sleep", "0",
    )
    assert [c[0] for c in fake.calls] == ["/status"]
    assert "nothing was requested" in capsys.readouterr().out


def test_download_cli_rejects_unknown_kinds(tmp_path, monkeypatch, history_db):
    script = _load_script("download_extras")
    with pytest.raises(SystemExit):
        _run_download_cli(
            monkeypatch, script, FakeClient(),
            "--data-dir", str(tmp_path / "extras"), "--history-db", str(history_db), "--kinds", "injuries,bogus",
        )
