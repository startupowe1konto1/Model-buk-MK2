"""Refresh the fixture directory and fetch settlement statistics.

One run (a few requests; meant for every 30-60 minutes):

1. lists the calendar from ``--days-back`` days ago to ``--days-ahead`` days ahead
   (``/fixtures?date=``; every league at once), skipping days that are still fresh;
2. optionally fetches score and team statistics for finished matches
   (``/fixtures?ids=``, 20 ids per request) that the scanner has to settle:
   ``--settle-stats-for <sqlite>`` reads their ids (table ``pending_fixture_ids``, or
   any table with a ``fixture_id`` column; an optional ``need_halves`` column also
   requests first/second-half statistics, one request per match),
   ``--settle-ids 1,2,3`` gives them directly and ``--auto-settle N`` takes up to N
   finished matches of the directory that still lack statistics.

Usage (from app/):

    .venv\\Scripts\\python scripts\\collect_fixture_directory.py --days-back 3 --days-ahead 7

The API key comes from the API_FOOTBALL_KEY environment variable and is never
printed or stored.  A run stops when the daily quota falls to ``--reserve``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from download_history import RealHistoryClient  # noqa: E402  (same scripts/ folder)
from model_buk.ingestion.api_football_history import RequestBudget  # noqa: E402
from model_buk.ingestion.fixture_directory import (  # noqa: E402
    FixtureDirectory,
    FixtureDirectoryCollector,
    read_pending_ids,
)

DEFAULT_DATA_DIR = r"C:\Users\oskar\Documents\Model-Buk-Data\fixture-directory"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--days-ahead", type=int, default=7)
    parser.add_argument("--near-refresh-minutes", type=float, default=60.0, help="Re-list tomorrow when older than this")
    parser.add_argument("--far-refresh-hours", type=float, default=6.0, help="Re-list days after tomorrow when older than this")
    parser.add_argument("--force", action="store_true", help="List every day of the window regardless of freshness")
    parser.add_argument("--skip-refresh", action="store_true", help="Only fetch settlement statistics")
    parser.add_argument("--settle-stats-for", help="SQLite file with the fixture ids to settle")
    parser.add_argument("--pending-table", help="Table in that file (default: pending_fixture_ids or the first with fixture_id)")
    parser.add_argument("--settle-ids", help="Comma-separated fixture ids to settle")
    parser.add_argument("--halves", action="store_true", help="Also fetch first/second-half statistics for all settle ids")
    parser.add_argument("--settle-force", action="store_true",
                        help="Fetch the settle ids again even if they have statistics or were tried recently")
    parser.add_argument("--auto-settle", type=int, default=0, metavar="N",
                        help="Also settle up to N finished matches of the directory that lack statistics")
    parser.add_argument("--max-requests", type=int, default=60, help="Hard cap on API calls for this run")
    parser.add_argument("--reserve", type=int, default=1500, help="Stop when the daily remaining quota drops to this")
    parser.add_argument("--sleep", type=float, default=0.15, help="Seconds to sleep between requests")
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        print("API_FOOTBALL_KEY is not set", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    directory = FixtureDirectory(args.data_dir)
    budget = RequestBudget(max_requests=args.max_requests, reserve=args.reserve)
    collector = FixtureDirectoryCollector(
        RealHistoryClient(api_key), directory, budget, sleep_between_requests=args.sleep
    )

    parts = [f"{now.isoformat(timespec='seconds')}"]
    stopped = "completed"
    if not args.skip_refresh:
        report = collector.refresh(
            now, days_back=args.days_back, days_ahead=args.days_ahead,
            near_refresh_minutes=args.near_refresh_minutes, far_refresh_hours=args.far_refresh_hours,
            force=args.force,
        )
        parts.append(
            f"run={report.run_id} dates={','.join(report.dates_fetched) or '-'} requests={report.requests} "
            f"listed={report.fixtures_listed} new={report.fixtures_new} changed={report.fixtures_changed}"
        )
        stopped = report.stopped_reason
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)

    settle: list[int] = []
    need_halves: list[int] = []
    if args.settle_stats_for:
        ids, halves = read_pending_ids(args.settle_stats_for, args.pending_table)
        settle += ids
        need_halves += halves
    if args.settle_ids:
        settle += [int(x) for x in args.settle_ids.split(",") if x.strip()]
    if args.auto_settle > 0:
        settle += directory.pending_stats_ids(now, limit=args.auto_settle)
    if args.halves:
        need_halves += settle
    settle = sorted(set(settle))
    if settle and stopped == "completed":
        details = collector.fetch_fixture_details(settle, now, force=args.settle_force)
        parts.append(
            f"settle_requested={details.requested} skipped={details.skipped} requests={details.requests} "
            f"with_stats={details.fixtures_with_stats} without_stats={details.fixtures_without_stats}"
        )
        stopped = details.stopped_reason
        for error in details.errors:
            print(f"error: {error}", file=sys.stderr)
        if need_halves and stopped == "completed":
            halves_report = collector.fetch_half_stats(sorted(set(need_halves)), now, force=args.settle_force)
            parts.append(
                f"half_requested={halves_report.requested} requests={halves_report.requests} "
                f"with_stats={halves_report.fixtures_with_stats}"
            )
            stopped = halves_report.stopped_reason
            for error in halves_report.errors:
                print(f"error: {error}", file=sys.stderr)

    summary = directory.summary()
    parts.append(
        f"stopped={stopped} quota={budget.remaining_quota} | total fixtures={summary['fixtures']} "
        f"leagues={summary['leagues']} with_stats={summary['fixtures_with_stats']}"
    )
    print(" ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
