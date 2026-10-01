"""Build analysis-ready SQLite tables from the raw API-Football history archive.

Offline only: reads the gzip batch files and state.sqlite3 written by
scripts/download_history.py (never modifies them) and makes no API calls.
Every run is a full rebuild into a temp file that atomically replaces the
output, so it is always safe to rerun or interrupt. See
app/src/model_buk/ingestion/api_football_tables.py for the table design.

Usage (from the app/ directory):

    .venv\\Scripts\\python scripts\\build_history_tables.py

    .venv\\Scripts\\python scripts\\build_history_tables.py ^
        --archive-root "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football" ^
        --out "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football\\tables\\history.sqlite"

A per-league data-quality summary is written as markdown next to the
database (tables/quality_report.md by default).

``--slim`` leaves the player-level tables (lineup_players, player_match_stats)
empty: about a third of the size, and everything the live models, the count
models, settlement and the scanner read is still there (see
model_buk.ingestion.api_football_tables.SLIM_SKIPPED_TABLES). The server
(Railway) builds slim; the PC keeps the full build for research. A slim build
writes no quality report unless --quality-report is given (its line-up and
player-stats shares would read 0%).

The archive root defaults to <data root>/api-football; the data root is
$MODEL_BUK_DATA, else C:\\Users\\oskar\\Documents\\Model-Buk-Data (model_buk.paths).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.ingestion.api_football_tables import (  # noqa: E402
    DATA_TABLES,
    DEFAULT_FILES_PER_TRANSACTION,
    SLIM_SKIPPED_TABLES,
    build_history_tables,
    write_quality_report,
)
from model_buk.paths import data_root  # noqa: E402

DEFAULT_ARCHIVE_ROOT = data_root() / "api-football"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=DEFAULT_ARCHIVE_ROOT,
        help="Archive folder containing raw/ and state.sqlite3 (read-only)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output SQLite path (default: <archive-root>/tables/history.sqlite)",
    )
    parser.add_argument(
        "--quality-report",
        type=Path,
        default=None,
        help="Markdown quality summary path (default: quality_report.md next to --out; none for a slim build)",
    )
    parser.add_argument(
        "--files-per-transaction",
        type=int,
        default=DEFAULT_FILES_PER_TRANSACTION,
        help="Batch files loaded per SQLite transaction",
    )
    parser.add_argument(
        "--slim",
        action="store_true",
        help="Leave the player-level tables empty (" + ", ".join(SLIM_SKIPPED_TABLES) + "); used on the server",
    )
    parser.add_argument(
        "--skip-table",
        action="append",
        default=[],
        choices=[table for table in DATA_TABLES if table != "matches"],
        help="Leave this table empty (repeatable; --slim already covers the player-level tables)",
    )
    args = parser.parse_args()

    skip = tuple(dict.fromkeys([*(SLIM_SKIPPED_TABLES if args.slim else ()), *args.skip_table]))
    out = args.out or args.archive_root / "tables" / "history.sqlite"
    quality_path = args.quality_report or (None if skip else out.parent / "quality_report.md")
    started = time.perf_counter()

    def progress(files_done: int, files_total: int, fixtures_done: int) -> None:
        elapsed = time.perf_counter() - started
        print(f"  {files_done:5d}/{files_total} batch files  {fixtures_done:7d} fixtures  {elapsed:6.1f}s", flush=True)

    print(f"Archive: {args.archive_root}")
    print(f"Output:  {out}")
    if skip:
        print(f"Left empty: {', '.join(skip)}")
    report = build_history_tables(
        args.archive_root,
        out,
        files_per_transaction=args.files_per_transaction,
        progress=progress,
        skip_tables=skip,
    )

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"\nBuilt in {report.seconds:.1f}s -> {out} ({size_mb:.1f} MiB)")
    print(f"state.sqlite3 provenance used: {report.state_db_used}")
    print(
        f"Batch files: {report.batch_files}  fixtures: {report.fixtures}  "
        f"duplicates skipped: {report.duplicate_fixtures_skipped}  without id: {report.fixtures_without_id}"
    )
    print("Rows per table:")
    for table, count in report.row_counts.items():
        print(f"  {table:20s} {count:10d}")

    if quality_path is None:
        print("\nQuality report: not written (slim build; pass --quality-report to write it anyway)")
        return
    write_quality_report(out, quality_path)
    print(f"\nQuality report: {quality_path}")


if __name__ == "__main__":
    main()
