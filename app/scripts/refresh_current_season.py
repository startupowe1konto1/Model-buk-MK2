r"""Archive finished matches of the season that is still running.

Complements download_history.py (which covers completed seasons only). Safe
to run daily: it lists finished matches per league, then downloads full
detail only for matches not archived yet and finished at least
``--min-age-hours`` ago. Writes into the same archive, so
build_history_tables.py picks the new matches up on its next run.

Usage (from app/):

    .venv\Scripts\python scripts\refresh_current_season.py ^
        --data-dir "C:\Users\oskar\Documents\Model-Buk-Data\api-football"

The API key comes from the API_FOOTBALL_KEY environment variable.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from download_history import RealHistoryClient  # noqa: E402  (same scripts/ folder)
from model_buk.ingestion.api_football_history import (  # noqa: E402
    CURRENT_SEASON,
    DEFAULT_MIN_AGE_HOURS,
    CurrentSeasonRefresher,
    HistoryStore,
    RequestBudget,
    current_season_scope,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=r"C:\Users\oskar\Documents\Model-Buk-Data\api-football")
    parser.add_argument(
        "--mode",
        choices=("leagues", "dates"),
        default="leagues",
        help="leagues = one list call per league (full sweep, ~50 requests); "
        "dates = one call per recent day (a few requests, meant for hourly runs)",
    )
    parser.add_argument("--days-back", type=int, default=3, help="dates mode: how many past days to scan")
    parser.add_argument("--season", type=int, default=CURRENT_SEASON)
    parser.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    parser.add_argument("--max-requests", type=int, default=1500, help="Hard cap on API calls for this run")
    parser.add_argument(
        "--reserve",
        type=int,
        default=1500,
        help="Stop when the daily remaining quota drops to this (keeps room for the odds collector)",
    )
    parser.add_argument("--sleep", type=float, default=0.25)
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        print("API_FOOTBALL_KEY is not set", file=sys.stderr)
        return 2

    store = HistoryStore(args.data_dir)
    budget = RequestBudget(max_requests=args.max_requests, reserve=args.reserve)
    refresher = CurrentSeasonRefresher(
        RealHistoryClient(api_key),
        store,
        budget,
        sleep_between_requests=args.sleep,
        min_age_hours=args.min_age_hours,
    )
    scope = current_season_scope(args.season)
    if args.mode == "dates":
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=n) for n in range(args.days_back, -1, -1)]
        report = refresher.run_dates(days, scope)
    else:
        report = refresher.run(scope)

    rows = [r for r in store.progress() if r["season"] == args.season]
    done = sum(r["done"] for r in rows)
    print(
        f"mode={args.mode} season={args.season} leagues={len(scope)} requests={report.requests_spent} "
        f"stopped={report.stopped_reason} archived_matches={done} "
        f"pending={sum(r['pending'] for r in rows)} missing={sum(r['missing'] for r in rows)} "
        f"quota_remaining={budget.remaining_quota}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
