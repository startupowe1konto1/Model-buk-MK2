"""Download historical extras (injuries, coaches, transfers, sidelined) from API-Football.

Resumable and budget-aware, like scripts/download_history.py: raw gzip JSON
payloads are kept forever under the data dir, a small SQLite state database
records what is done, and a rerun never spends a request on something that is
already archived. See app/src/model_buk/ingestion/api_football_extras.py for
the design and for the leakage warning: historical injuries are
RETROSPECTIVE (who actually missed the match), not what was known before
kickoff.

Scope (all read from the match archive tables, never modified):

- injuries:  every planned league, seasons 2021..running season (one request
  per league-season, plus extra pages if the API pages).
- coaches, transfers: one request per distinct team id in ``matches``.
- sidelined: players seen in a lineup since --sidelined-since, top-20
  leagues first, most recent seasons first, then by appearances; up to 20
  players per request. Resumable over many days.

Usage (from the app/ directory, with .venv active):

    .venv\\Scripts\\python scripts\\download_extras.py ^
        --kinds injuries,coaches,transfers --max-requests 2800 --reserve 1500

    .venv\\Scripts\\python scripts\\download_extras.py ^
        --kinds sidelined --max-requests 2500 --reserve 1500

A later refresh of the running season's injuries (new timestamped raw files,
the old ones are kept):

    .venv\\Scripts\\python scripts\\download_extras.py --kinds injuries --refresh-days 7

The daily quota is shared with the scheduled collectors, so the run stops as
soon as the API's own remaining-quota header reaches --reserve. The API key is
read from the API_FOOTBALL_KEY environment variable and is never written to
disk or printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_history import RealHistoryClient  # noqa: E402

from model_buk.ingestion.api_football_extras import (  # noqa: E402
    COACHES,
    INJURIES,
    INJURY_FIRST_SEASON,
    KINDS,
    SIDELINED,
    SIDELINED_DEFAULT_SINCE,
    TRANSFERS,
    ExtrasDownloader,
    ExtrasStore,
    current_season_league_ids_from_history,
    injury_scope,
    player_scope_from_history,
    team_ids_from_history,
)
from model_buk.ingestion.api_football_history import CURRENT_SEASON, RequestBudget  # noqa: E402

DEFAULT_DATA_DIR = Path(r"C:\Users\oskar\Documents\Model-Buk-Data\api-football-extras")
DEFAULT_HISTORY_DB = Path(r"C:\Users\oskar\Documents\Model-Buk-Data\api-football\tables\history.sqlite")


def parse_kinds(text: str) -> list[str]:
    kinds: list[str] = []
    for part in text.split(","):
        kind = part.strip().lower()
        if not kind:
            continue
        if kind not in KINDS:
            raise argparse.ArgumentTypeError(f"unknown kind {kind!r}; choose from {', '.join(KINDS)}")
        if kind not in kinds:
            kinds.append(kind)
    if not kinds:
        raise argparse.ArgumentTypeError("no kinds given")
    return kinds


def preflight_quota(client: RealHistoryClient) -> int | None:
    """Daily requests left according to the API. ``/status`` is free (does not use quota)."""

    try:
        payload, headers = client.get("/status", {})
    except Exception as exc:  # noqa: BLE001 - the preflight is best effort
        print(f"Quota preflight failed ({type(exc).__name__}); relying on response headers instead.")
        return None
    raw = headers.get("x-ratelimit-requests-remaining")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    requests_block = (payload.get("response") or {}).get("requests") or {}
    try:
        return int(requests_block["limit_day"]) - int(requests_block["current"])
    except (KeyError, TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Root folder for raw archive + state")
    parser.add_argument(
        "--history-db",
        type=Path,
        default=DEFAULT_HISTORY_DB,
        help="History tables database (read-only) that supplies team ids, players and active leagues",
    )
    parser.add_argument(
        "--kinds",
        type=parse_kinds,
        default=list(KINDS),
        help=f"Comma-separated subset of: {', '.join(KINDS)} (processed in this order, sharing one budget)",
    )
    parser.add_argument("--max-requests", type=int, default=2500, help="Hard cap on API calls for this run")
    parser.add_argument(
        "--reserve",
        type=int,
        default=1500,
        help="Stop once the API's own daily remaining-quota header drops to this floor",
    )
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds to sleep between requests")
    parser.add_argument("--first-injury-season", type=int, default=INJURY_FIRST_SEASON)
    parser.add_argument("--current-season", type=int, default=CURRENT_SEASON, help="The running season")
    parser.add_argument(
        "--refresh-days",
        type=float,
        default=None,
        help="Refetch the running season's injuries when the last complete fetch is at least this old",
    )
    parser.add_argument(
        "--sidelined-since",
        default=SIDELINED_DEFAULT_SINCE,
        help="Queue players seen in a lineup on/after this ISO date for /sidelined",
    )
    args = parser.parse_args()

    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY is not set. Run app/USTAW_API_FOOTBALL.bat first.")
    if not args.history_db.is_file():
        raise SystemExit(f"History database not found: {args.history_db}")

    store = ExtrasStore(args.data_dir)
    client = RealHistoryClient(api_key)
    budget = RequestBudget(max_requests=args.max_requests, reserve=args.reserve)
    downloader = ExtrasDownloader(client, store, budget, sleep_between_requests=args.sleep)

    print(f"Data dir: {args.data_dir}")
    print(f"History DB: {args.history_db}")
    print(f"Kinds: {', '.join(args.kinds)}  max requests: {args.max_requests}  reserve: {args.reserve}")
    quota = preflight_quota(client)
    if quota is not None:
        print(f"Quota remaining before the run: {quota}")
        budget.remaining_quota = quota
        if quota <= args.reserve:
            print(f"Quota is already at or below the reserve ({args.reserve}); nothing was requested.")
            return
    print()

    reports = []
    for kind in args.kinds:
        if kind == INJURIES:
            active = current_season_league_ids_from_history(args.history_db, args.current_season)
            scope = injury_scope(
                first_season=args.first_injury_season,
                current_season=args.current_season,
                current_season_league_ids=active,
            )
            report = downloader.run_injuries(
                scope, refresh_after_days=args.refresh_days, current_season=args.current_season
            )
        elif kind == COACHES:
            report = downloader.run_coaches(team_ids_from_history(args.history_db))
        elif kind == TRANSFERS:
            report = downloader.run_transfers(team_ids_from_history(args.history_db))
        elif kind == SIDELINED:
            report = downloader.run_sidelined(player_scope_from_history(args.history_db, since=args.sidelined_since))
        else:  # pragma: no cover - parse_kinds already validated
            raise SystemExit(f"unknown kind {kind}")
        reports.append(report)
        print(report.summary_line(), flush=True)
        if report.stopped_reason != "completed":
            # The budget or a systemic error ended this kind; later kinds would only hit the same wall.
            break

    print()
    print(f"Requests spent this run: {budget.spent}")
    if budget.remaining_quota is not None:
        print(f"Quota remaining now: {budget.remaining_quota}")
    if any(r.stopped_reason == "budget_exhausted" for r in reports):
        print("Budget reached for this run. Rerun the same command later to continue where it stopped.")
    if any(r.stopped_reason == "errors" for r in reports):
        print("Stopped after repeated request errors; see state.sqlite3 (fetch_errors) and rerun later.")


if __name__ == "__main__":
    main()
