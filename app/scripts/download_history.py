"""Bulk-download historical match detail from API-Football into a local archive.

Resumable and budget-aware: safe to stop (Ctrl+C) and rerun; it picks up
exactly where it left off and never re-spends a request on an
already-downloaded fixture. See docs/CURRENT-DATA-PIPELINE.md and
app/src/model_buk/ingestion/api_football_history.py for the design.

Usage (from the app/ directory, with .venv active):

    .venv\\Scripts\\python scripts\\download_history.py ^
        --data-dir "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football" ^
        --max-requests 6000 --reserve 500

The API key is read from the API_FOOTBALL_KEY environment variable, the
same one used by the live app; it is never written to disk by this script.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.ingestion.api_football_history import (  # noqa: E402
    BASE_URL,
    HistoryDownloader,
    HistoryStore,
    RequestBudget,
    top20_league_seasons,
)


class RealHistoryClient:
    def __init__(self, api_key: str, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers["x-apisports-key"] = api_key
        self.timeout = timeout

    def get(self, endpoint: str, params: dict) -> tuple[dict, dict]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(BASE_URL + endpoint, params=params, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise RuntimeError(f"API-Football error: {payload['errors']}")
                headers = {k.lower(): v for k, v in response.headers.items()}
                return payload, headers
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"giving up on {endpoint} {params}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Root folder for the raw archive + progress database")
    parser.add_argument("--max-requests", type=int, default=6000, help="Hard cap on API calls for this run")
    parser.add_argument(
        "--reserve",
        type=int,
        default=500,
        help="Stop once the API's own daily remaining-quota header drops to this floor",
    )
    parser.add_argument("--sleep", type=float, default=0.25, help="Seconds to sleep between requests")
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY is not set. Run app/USTAW_API_FOOTBALL.bat first.")

    store = HistoryStore(args.data_dir)
    client = RealHistoryClient(api_key)
    budget = RequestBudget(max_requests=args.max_requests, reserve=args.reserve)
    downloader = HistoryDownloader(client, store, budget, sleep_between_requests=args.sleep)

    print(f"Data dir: {args.data_dir}")
    print(f"Scope: {len(top20_league_seasons())} league-seasons")
    report = downloader.run(top20_league_seasons())

    print(f"Requests spent this run: {report.requests_spent}")
    print(f"Stopped: {report.stopped_reason}")
    print(f"League-seasons fully completed this run: {len(report.league_seasons_completed)}")

    print("\nProgress summary:")
    total_done = total_all = 0
    for row in store.progress():
        total_done += row["done"]
        total_all += row["fixtures_total"]
        status = "OK" if row["done"] + row["missing"] >= row["fixtures_total"] else "..."
        print(
            f"  [{status}] {row['code']:14s} {row['season']}  "
            f"done={row['done']:4d}  missing={row['missing']:3d}  pending={row['pending']:4d}  "
            f"total={row['fixtures_total']:4d}"
        )
    print(f"\nTotal: {total_done}/{total_all} fixtures downloaded across all planned league-seasons.")
    if report.stopped_reason == "budget_exhausted":
        print("\nBudget exhausted for this run. Rerun this same command tomorrow (or with a higher --max-requests) to continue.")


if __name__ == "__main__":
    main()
