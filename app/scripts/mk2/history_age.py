"""How old is the match history the live models would be fitted on?  Exit 0 = fresh enough, 3 = stale.

The nightly fit (``fit_live_models.py fit``) stamps its manifest with the time of the fit, not with the date of
the newest match it learnt from.  On a snapshot whose history ends days ago, a fit made today would look fresh to
the scanner (``model_max_fit_age_hours`` = 24) while its team states lack every recent result.  ``run_job.ps1``
calls this check first and skips the fit when the newest finished match is older than ``--max-days``; the
scanner then says ``model=fit_stale`` / ``no_fit`` and prices from the market only, which is the honest state.

Usage (from app/):

    .venv\\Scripts\\python scripts\\mk2\\history_age.py --history-db <path> --max-days 3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

FINISHED = ("FT", "AET", "PEN")


def newest_finished_kickoff(db: Path) -> datetime | None:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        marks = ",".join("?" * len(FINISHED))
        row = con.execute(
            f"SELECT max(kickoff_utc) FROM matches WHERE status_short IN ({marks})", FINISHED
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None
    stamp = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history-db", required=True)
    parser.add_argument("--max-days", type=float, default=3.0)
    args = parser.parse_args(argv)
    db = Path(args.history_db)
    if not db.exists():
        print(f"stale: no history database at {db}")
        return 3
    try:
        newest = newest_finished_kickoff(db)
    except sqlite3.Error as exc:
        print(f"stale: cannot read {db}: {exc}")
        return 3
    if newest is None:
        print("stale: no finished match in the history")
        return 3
    age_days = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
    verdict = "fresh" if age_days <= args.max_days else "stale"
    print(f"{verdict}: newest finished match {newest.isoformat(timespec='minutes')} ({age_days:.1f} days old, limit {args.max_days:g})")
    return 0 if verdict == "fresh" else 3


if __name__ == "__main__":
    sys.exit(main())
