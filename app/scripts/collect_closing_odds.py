"""Near-kickoff odds: turn the last stored quote before kickoff into a real closing line.

Meant to run every 15 minutes.  Fetches the odds of fixtures that kick off within the
next ``--window-minutes`` (default 150) and appends them to the same odds archive
the hourly collector writes (change-only quotes, observations, raw files); the
scans are marked ``kind = 'near_kickoff'``.  See model_buk.ingestion.closing_odds
for what is fetched when and why.

Usage (from app/):

    .venv\\Scripts\\python scripts\\collect_closing_odds.py

The API key comes from the API_FOOTBALL_KEY environment variable and is never
printed or stored.  A run stops after ``--max-requests`` calls or when the daily
quota falls to ``--reserve``.
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
from model_buk.ingestion.api_football_odds_archive import OddsArchive  # noqa: E402
from model_buk.ingestion.closing_odds import (  # noqa: E402
    DEFAULT_CADENCE,
    DENSE_CADENCE,
    ClosingOddsCollector,
)
from model_buk.ingestion.fixture_directory import FixtureDirectory  # noqa: E402

DATA_ROOT = r"C:\Users\oskar\Documents\Model-Buk-Data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DATA_ROOT + r"\api-football-odds", help="The odds archive to append to")
    parser.add_argument("--directory-dir", default=DATA_ROOT + r"\fixture-directory",
                        help="Fixture directory (calendar, probe and quota state)")
    parser.add_argument("--window-minutes", type=float, default=150.0, help="Fetch fixtures kicking off within this many minutes")
    parser.add_argument("--max-requests", type=int, default=80, help="Hard cap on API calls for this run")
    parser.add_argument("--reserve", type=int, default=1500, help="Stop when the daily remaining quota drops to this")
    parser.add_argument("--max-unseen", type=int, default=10,
                        help="Probe at most this many fixtures the odds archive has never seen")
    parser.add_argument("--dense", action="store_true", help="Fetch every fixture in the window on every run")
    parser.add_argument("--sleep", type=float, default=0.15, help="Seconds to sleep between requests")
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        print("API_FOOTBALL_KEY is not set", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    archive = OddsArchive(args.data_dir)
    budget = RequestBudget(max_requests=args.max_requests, reserve=args.reserve)
    collector = ClosingOddsCollector(
        RealHistoryClient(api_key), archive, FixtureDirectory(args.directory_dir), budget,
        window_minutes=args.window_minutes,
        cadence=DENSE_CADENCE if args.dense else DEFAULT_CADENCE,
        max_unseen=args.max_unseen,
        sleep_between_requests=args.sleep,
    )
    report = collector.run(now)
    summary = archive.summary()
    print(
        f"{now.isoformat(timespec='seconds')} scan={report.scan_id} window={report.window_fixtures} "
        f"due={report.due_fixtures} unseen_probed={report.unseen_probed} unseen_found={report.unseen_found} "
        f"requests={report.requests} fixtures={report.fixtures_recorded} boards_changed={report.boards_changed} "
        f"changed={report.quotes_changed} stopped={report.stopped_reason} quota={budget.remaining_quota} | "
        f"total fixtures={summary['fixtures']} quotes={summary['quotes']} scans={summary['scans']}"
    )
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
