"""Build analysis-ready SQLite tables from the raw API-Football extras archive.

Offline only: reads the gzip files written by scripts/download_extras.py
(never modifies them) and makes no API calls. Every run is a full rebuild
into a temp file that atomically replaces the output, so it is always safe to
rerun or interrupt. See app/src/model_buk/ingestion/api_football_extras.py for
the table design and the rules for refreshed / interrupted fetches.

WARNING: the ``injuries`` table is RETROSPECTIVE (who actually missed a
match), not what was known before kickoff. Features built from it leak the
future unless restricted to information that existed before the decision
time. The database repeats this warning in ``build_info`` and ``table_notes``.

Usage (from the app/ directory):

    .venv\\Scripts\\python scripts\\build_extras_tables.py

    .venv\\Scripts\\python scripts\\build_extras_tables.py ^
        --archive-root "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football-extras" ^
        --out "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football-extras\\tables\\extras.sqlite"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.ingestion.api_football_extras import (  # noqa: E402
    RETROSPECTIVE_WARNING,
    build_extras_tables,
)
from model_buk.paths import data_root  # noqa: E402

DEFAULT_ARCHIVE_ROOT = data_root() / "api-football-extras"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=DEFAULT_ARCHIVE_ROOT,
        help="Archive folder containing raw/ (read-only)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output SQLite path (default: <archive-root>/tables/extras.sqlite)",
    )
    args = parser.parse_args()

    out = args.out or args.archive_root / "tables" / "extras.sqlite"
    print(f"Archive: {args.archive_root}")
    print(f"Output:  {out}")
    report = build_extras_tables(args.archive_root, out)

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"\nBuilt in {report.seconds:.1f}s -> {out} ({size_mb:.1f} MiB)")
    print("Complete raw files used: " + ", ".join(f"{kind}={count}" for kind, count in report.raw_files.items()))
    print(f"Incomplete (interrupted) fetches ignored: {report.incomplete_runs}")
    print(f"Entries without an id (not stored): {report.entries_without_id}")
    print("Exact duplicate rows dropped: " + ", ".join(f"{t}={n}" for t, n in report.duplicates_skipped.items()))
    print("Rows per table:")
    for table, count in report.row_counts.items():
        print(f"  {table:14s} {count:10d}")
    print(f"\n{RETROSPECTIVE_WARNING}")


if __name__ == "__main__":
    main()
