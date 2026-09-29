from __future__ import annotations

from datetime import date, datetime, timezone

from model_buk.ingestion.api_football_history import (
    CurrentSeasonRefresher,
    HistoryStore,
    LeagueSeason,
    RequestBudget,
)

LS = LeagueSeason(999, "TESTLG", "Test League", "Testland", 2020)


def test_run_dates_uses_one_list_call_per_day_and_keeps_only_scope(tmp_path):
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    old = now.timestamp() - 48 * 3600
    calls: list[dict] = []

    class DateClient:
        def get(self, endpoint, params):
            calls.append(dict(params))
            headers = {"x-ratelimit-requests-remaining": "5000"}
            if "date" in params:
                assert params["status"] == "FT-AET-PEN"
                rows = [
                    {"fixture": {"id": 1, "timestamp": old}, "league": {"id": 999, "season": 2020}},
                    {"fixture": {"id": 2, "timestamp": old}, "league": {"id": 555, "season": 2020}},  # not tracked
                    {"fixture": {"id": 3, "timestamp": old}, "league": {"id": 999, "season": 2019}},  # other season
                ]
                return {"response": rows}, headers
            return {"response": [{"fixture": {"id": int(x)}} for x in params["ids"].split("-")]}, headers

    store = HistoryStore(tmp_path / "archive")
    refresher = CurrentSeasonRefresher(DateClient(), store, RequestBudget(max_requests=100), now=lambda: now)
    report = refresher.run_dates([date(2026, 9, 28), date(2026, 9, 29)], [LS])

    assert len([c for c in calls if "date" in c]) == 2
    assert len([c for c in calls if "ids" in c]) == 1  # match 1 only, fetched once across both days
    assert store.progress()[0]["done"] == 1
    assert report.stopped_reason == "completed"
    assert not (tmp_path / "archive/raw/999/2020/ids_finished.json.gz").exists()


def test_run_dates_stops_when_budget_is_exhausted(tmp_path):
    class Client:
        def get(self, endpoint, params):
            return {"response": []}, {"x-ratelimit-requests-remaining": "5000"}

    store = HistoryStore(tmp_path / "archive")
    refresher = CurrentSeasonRefresher(Client(), store, RequestBudget(max_requests=1))
    report = refresher.run_dates([date(2026, 9, 28), date(2026, 9, 29)], [LS])
    assert report.stopped_reason == "budget_exhausted"
    assert report.requests_spent == 1
