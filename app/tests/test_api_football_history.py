from __future__ import annotations

from model_buk.ingestion.api_football_history import (
    DownloadReport,
    HistoryDownloader,
    HistoryStore,
    LeagueSeason,
    RequestBudget,
    top20_league_seasons,
)


LS = LeagueSeason(999, "TESTLG", "Test League", "Testland", 2020)


class FakeClient:
    """Canned, offline responses keyed by endpoint+params; counts every call."""

    def __init__(self, n_fixtures: int, *, quota_start: int = 1000, missing_ids: set[int] | None = None):
        self.n_fixtures = n_fixtures
        self.quota = quota_start
        self.missing_ids = missing_ids or set()
        self.calls: list[tuple[str, dict]] = []

    def get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        self.quota -= 1
        headers = {"x-ratelimit-requests-remaining": str(self.quota)}
        if "league" in params and "season" in params:
            response = [
                {"fixture": {"id": 1000 + i}, "league": {"id": params["league"]}}
                for i in range(self.n_fixtures)
            ]
            return {"response": response}, headers
        if "ids" in params:
            requested = [int(x) for x in params["ids"].split("-")]
            response = [
                {"fixture": {"id": fid}, "teams": {"home": {"name": "A"}, "away": {"name": "B"}}}
                for fid in requested
                if fid not in self.missing_ids
            ]
            return {"response": response}, headers
        raise AssertionError(f"unexpected params: {params}")


def test_fetches_ids_once_then_reuses_store(tmp_path):
    client = FakeClient(n_fixtures=5)
    store = HistoryStore(tmp_path / "archive")
    budget = RequestBudget(max_requests=100)
    downloader = HistoryDownloader(client, store, budget)

    report = downloader.run([LS])
    assert report.stopped_reason == "completed"
    assert report.league_seasons_completed == ["TESTLG:2020"]
    # 1 call for ids + 1 batch call for 5 fixtures (<=20 per batch)
    assert len(client.calls) == 2

    # Second run against the same store must not re-fetch ids or fixtures.
    client2 = FakeClient(n_fixtures=5)
    downloader2 = HistoryDownloader(client2, store, RequestBudget(max_requests=100))
    report2 = downloader2.run([LS])
    assert report2.league_seasons_completed == ["TESTLG:2020"]
    assert client2.calls == []


def test_batches_of_20_fixtures(tmp_path):
    client = FakeClient(n_fixtures=45)
    store = HistoryStore(tmp_path / "archive")
    downloader = HistoryDownloader(client, store, RequestBudget(max_requests=100))

    report = downloader.run([LS])
    assert report.stopped_reason == "completed"
    # 1 id call + ceil(45/20) = 3 batch calls
    batch_calls = [c for c in client.calls if "ids" in c[1]]
    assert len(batch_calls) == 3
    sizes = [len(c[1]["ids"].split("-")) for c in batch_calls]
    assert sizes == [20, 20, 5]

    progress = store.progress()
    assert progress == [
        {"league_id": 999, "code": "TESTLG", "season": 2020, "fixtures_total": 45, "done": 45, "missing": 0, "pending": 0}
    ]


def test_budget_stops_before_reserve_and_resumes(tmp_path):
    client = FakeClient(n_fixtures=45, quota_start=3)
    store = HistoryStore(tmp_path / "archive")
    # reserve=0 but quota_start=3: id call (quota->2), then first batch call (quota->1),
    # second batch call would bring quota to 0 which is still > reserve... use reserve=1
    # so the downloader stops once remaining_quota would drop to <= 1.
    budget = RequestBudget(max_requests=100, reserve=1)
    downloader = HistoryDownloader(client, store, budget)

    report = downloader.run([LS])
    assert report.stopped_reason == "budget_exhausted"
    assert report.league_seasons_completed == []
    progress = store.progress()[0]
    assert progress["pending"] > 0
    assert progress["done"] < 45

    # Resuming with a fresh, generous budget finishes the rest without
    # re-fetching already-done fixtures.
    client2 = FakeClient(n_fixtures=45, quota_start=1000)
    report2 = HistoryDownloader(client2, store, RequestBudget(max_requests=100)).run([LS])
    assert report2.stopped_reason == "completed"
    assert store.progress()[0]["done"] == 45
    assert store.progress()[0]["pending"] == 0


def test_max_requests_cap_independent_of_quota_header(tmp_path):
    client = FakeClient(n_fixtures=45, quota_start=10_000)
    store = HistoryStore(tmp_path / "archive")
    budget = RequestBudget(max_requests=2)  # id call + 1 batch call, then stop
    downloader = HistoryDownloader(client, store, budget)

    report = downloader.run([LS])
    assert report.stopped_reason == "budget_exhausted"
    assert report.requests_spent == 2
    assert len(client.calls) == 2


def test_missing_fixture_ids_are_flagged_not_retried_forever(tmp_path):
    client = FakeClient(n_fixtures=5, missing_ids={1002})
    store = HistoryStore(tmp_path / "archive")
    report = HistoryDownloader(client, store, RequestBudget(max_requests=100)).run([LS])

    assert report.stopped_reason == "completed"
    progress = store.progress()[0]
    assert progress["done"] == 4
    assert progress["missing"] == 1
    assert progress["pending"] == 0

    # A follow-up run must not spend a request retrying the missing fixture.
    client2 = FakeClient(n_fixtures=5, missing_ids={1002})
    report2 = HistoryDownloader(client2, store, RequestBudget(max_requests=100)).run([LS])
    assert client2.calls == []
    assert report2.league_seasons_completed == ["TESTLG:2020"]


def test_raw_batch_file_round_trips(tmp_path):
    import gzip
    import json

    client = FakeClient(n_fixtures=3)
    store = HistoryStore(tmp_path / "archive")
    HistoryDownloader(client, store, RequestBudget(max_requests=100)).run([LS])

    batch_files = list((tmp_path / "archive" / "raw" / "999" / "2020" / "batches").glob("*.json.gz"))
    assert len(batch_files) == 1
    payload = json.loads(gzip.decompress(batch_files[0].read_bytes()))
    assert len(payload["response"]) == 3
    assert payload["response"][0]["fixture"]["id"] == 1000


def test_progress_reports_multiple_league_seasons_independently(tmp_path):
    store = HistoryStore(tmp_path / "archive")
    other = LeagueSeason(998, "OTHERLG", "Other League", "Otherland", 2021)
    downloader = HistoryDownloader(FakeClient(n_fixtures=5), store, RequestBudget(max_requests=100))
    downloader.run([LS])
    downloader2 = HistoryDownloader(FakeClient(n_fixtures=7), store, RequestBudget(max_requests=100))
    downloader2.run([other])

    progress = {(p["league_id"], p["season"]): p for p in store.progress()}
    assert progress[(999, 2020)]["fixtures_total"] == 5
    assert progress[(998, 2021)]["fixtures_total"] == 7


def test_request_budget_allow_and_record():
    budget = RequestBudget(max_requests=2, reserve=5)
    assert budget.allow()
    budget.record({"x-ratelimit-requests-remaining": "100"})
    assert budget.allow()
    budget.record({"x-ratelimit-requests-remaining": "5"})
    # spent == max_requests now
    assert not budget.allow()


def test_request_budget_respects_reserve_from_headers():
    budget = RequestBudget(max_requests=1000, reserve=10)
    budget.record({"x-ratelimit-requests-remaining": "11"})
    assert budget.allow()
    budget.record({"x-ratelimit-requests-remaining": "10"})
    assert not budget.allow()


def test_top20_league_seasons_scope_is_stable_and_reasonable():
    seasons = top20_league_seasons()
    codes = {ls.code for ls in seasons}
    assert "EPL" in codes and "EKSTRAKLASA" in codes and "AUSTRIA" in codes
    assert len(codes) == 20
    # Every season should be within a sane, explicit historical window.
    assert all(2015 <= ls.season <= 2025 for ls in seasons)
    # No duplicate (league, season) pairs.
    pairs = [(ls.league_id, ls.season) for ls in seasons]
    assert len(pairs) == len(set(pairs))
