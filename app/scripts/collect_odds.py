"""One scan of upcoming pre-match odds into the prospective odds archive.

Meant to be run by a scheduler (hourly). Each run pages through today and
the next ``--days-ahead`` days; every ``--wide-every-hours`` hours it also
looks ``--wide-days`` further ahead. Safe to run at any time: scans are
append-only and a run stops early rather than eat into the quota reserve.

Usage (from app/):

    .venv\\Scripts\\python scripts\\collect_odds.py ^
        --data-dir "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football-odds"

The API key comes from the API_FOOTBALL_KEY environment variable.
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
from model_buk.ingestion.api_football_odds_archive import (  # noqa: E402
    TRACKED_LEAGUE_IDS,
    OddsArchive,
    OddsCollector,
    scan_dates,
)
from model_buk.paths import data_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(data_root() / "api-football-odds"))
    parser.add_argument("--days-ahead", type=int, default=2)
    parser.add_argument("--wide-days", type=int, default=4)
    parser.add_argument("--wide-every-hours", type=int, default=6)
    parser.add_argument("--max-requests", type=int, default=150, help="Hard cap on API calls for this run")
    parser.add_argument("--reserve", type=int, default=1500, help="Stop when the daily remaining quota drops to this")
    parser.add_argument(
        "--leagues",
        choices=("all", "tracked"),
        default="all",
        help="all = every league the feed returns (same request cost); tracked = the 47 archived leagues",
    )
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        print("API_FOOTBALL_KEY is not set", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    wide = args.wide_days if now.hour % max(args.wide_every_hours, 1) == 0 else 0
    dates = scan_dates(now, args.days_ahead, wide)

    archive = OddsArchive(args.data_dir)
    collector = OddsCollector(
        RealHistoryClient(api_key),
        archive,
        RequestBudget(max_requests=args.max_requests, reserve=args.reserve),
        league_ids=None if args.leagues == "all" else TRACKED_LEAGUE_IDS,
    )
    report = collector.run(now, dates)
    summary = archive.summary()
    print(
        f"{now.isoformat(timespec='seconds')} scan={report.scan_id} dates={','.join(report.dates)} "
        f"requests={report.requests} fixtures={report.fixtures_seen} changed={report.quotes_changed} "
        f"stopped={report.stopped_reason} | total fixtures={summary['fixtures']} quotes={summary['quotes']} "
        f"bookmakers={summary['bookmakers']} scans={summary['scans']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
