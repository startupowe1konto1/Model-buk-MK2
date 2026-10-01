"""One run of the pre-match collector: fixtures, line-ups, injuries, API predictions.

Meant to be run by a scheduler (hourly). Every stored row carries the time it
was fetched, so features built from it can never see information from after
their decision time. Each run:

1. lists today's and tomorrow's fixtures (UTC) of the tracked leagues,
2. fetches line-ups of fixtures kicking off within the next ``--lineup-minutes``
   that have none yet (an empty answer is retried on the next run),
3. snapshots injuries for the same dates,
4. fetches the provider's prediction once for fixtures kicking off within the
   next ``--prediction-hours`` (a benchmark for our model, not a feature).

With an hourly cadence a line-up is captured roughly 5-100 minutes before
kickoff (published about an hour ahead, so typically 5-60 minutes before).
Safe to run at any time: raw files are append-only and a run stops early
rather than eat into the quota reserve. Design and tables:
app/src/model_buk/ingestion/api_football_prematch.py

Usage (from app/):

    .venv\\Scripts\\python scripts\\collect_prematch.py ^
        --data-dir "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football-prematch"

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
from model_buk.ingestion.api_football_prematch import (  # noqa: E402
    LINEUP_WINDOW_MINUTES,
    PREDICTION_WINDOW_HOURS,
    TRACKED_LEAGUE_IDS,
    PrematchArchive,
    PrematchCollector,
)
from model_buk.paths import data_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=str(data_root() / "api-football-prematch"))
    parser.add_argument("--max-requests", type=int, default=150, help="Hard cap on API calls for this run")
    parser.add_argument("--reserve", type=int, default=1500, help="Stop when the daily remaining quota drops to this")
    parser.add_argument("--days-ahead", type=int, default=1, help="Fixture/injury dates: today plus this many days")
    parser.add_argument("--lineup-minutes", type=int, default=LINEUP_WINDOW_MINUTES, help="Line-up window before kickoff")
    parser.add_argument("--prediction-hours", type=int, default=PREDICTION_WINDOW_HOURS, help="Prediction window before kickoff")
    parser.add_argument(
        "--leagues",
        choices=("tracked", "all"),
        default="tracked",
        help="tracked = the 47 archived leagues; all = every league the feed returns (for trial runs)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        print("API_FOOTBALL_KEY is not set", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    archive = PrematchArchive(args.data_dir)
    collector = PrematchCollector(
        RealHistoryClient(api_key),
        archive,
        RequestBudget(max_requests=args.max_requests, reserve=args.reserve),
        league_ids=TRACKED_LEAGUE_IDS if args.leagues == "tracked" else None,
        days_ahead=args.days_ahead,
        lineup_window_minutes=args.lineup_minutes,
        prediction_window_hours=args.prediction_hours,
    )
    report = collector.run(now)
    total = archive.summary()
    stopped = report.stopped_reason
    if report.errors:
        stopped += f" ({'; '.join(report.errors)[:300]})"
    print(
        f"{now.isoformat(timespec='seconds')} run={report.run_id} dates={','.join(report.dates)} "
        f"requests={report.requests} fixtures={report.fixtures_seen} "
        f"injuries_new={report.injuries_new} injuries_seen={report.injuries_seen} "
        f"lineups={report.lineups_captured}/{report.lineups_tried} predictions={report.predictions_stored} "
        f"stopped={stopped} | total fixtures={total['fixtures']} injuries={total['injuries']} "
        f"lineup_fixtures={total['lineup_fixtures']} predictions={total['predictions']} runs={total['runs']}"
    )
    return 1 if report.stopped_reason == "api_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
