"""Daily retention of the odds archive: archive the change history of fixtures that kicked off long ago.

Exports the ``quotes`` / ``observations`` rows of fixtures that kicked off more than ``--keep-days`` (default 7,
the minimum: the scanner's CLV and bet-type check read 7 days back) ago to gzip JSONL files under
``<data>/archive/odds/exports/``, verifies the row counts, deletes the rows from ``odds.sqlite3`` and keeps compact
opening / pre-kickoff / closing snapshots in ``odds_snapshots.sqlite3``.  Raw scan files of old days move to
``<data>/archive/odds/raw/``.  Details: ``model_buk.maintenance.odds_retention``.

Usage (from app/)::

    .venv\\Scripts\\python scripts\\compact_odds_archive.py --dry-run
    python scripts/compact_odds_archive.py --snapshot-keep-days 30      # the server's daily job

Makes no API request.  Prints one summary line; exit code 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.maintenance.odds_retention import (  # noqa: E402
    DEFAULT_BATCH_FIXTURES,
    DEFAULT_KEEP_DAYS,
    run_retention,
)
from model_buk.paths import data_root  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None, help="default: $MODEL_BUK_DATA or the PC's Model-Buk-Data")
    parser.add_argument("--odds-dir", type=Path, default=None, help="default: <data-root>/api-football-odds")
    parser.add_argument("--archive-dir", type=Path, default=None, help="default: <data-root>/archive")
    parser.add_argument("--keep-days", type=float, default=DEFAULT_KEEP_DAYS, help="full history kept this long after kickoff (>= 7)")
    parser.add_argument("--snapshot-keep-days", type=float, default=None, help="prune compact snapshots older than this (default: keep)")
    parser.add_argument("--batch-fixtures", type=int, default=DEFAULT_BATCH_FIXTURES, help="fixtures per export / transaction")
    parser.add_argument("--vacuum", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--no-raw", action="store_true", help="leave the raw scan files where they are")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be archived")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = args.data_root or data_root()
    odds_dir = args.odds_dir or root / "api-football-odds"
    archive_dir = args.archive_dir or root / "archive"
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose else None
    try:
        report = run_retention(
            odds_dir,
            archive_dir,
            keep_days=args.keep_days,
            batch_fixtures=args.batch_fixtures,
            move_raw=not args.no_raw,
            vacuum=args.vacuum,
            snapshot_keep_days=args.snapshot_keep_days,
            dry_run=args.dry_run,
            log=log,
        )
    except Exception as exc:  # noqa: BLE001 - one line for the job log
        print(f"error: odds retention failed ({type(exc).__name__}: {str(exc)[:300]})")
        return 1
    usage = shutil.disk_usage(odds_dir)
    print(f"{report.summary_line()} disk_free={usage.free / 2**30:.2f}GiB of {usage.total / 2**30:.2f}GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
