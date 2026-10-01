"""Daily retention of the scanner register: archive old near-miss legs, keep everything the scanner reads.

Exports ``legs`` rows of fixtures that kicked off more than ``--keep-days`` (default 7) ago and that are neither
value legs (any regime) nor coupon legs, have no CLV row and are settled (or older than ``--unsettled-grace-days``)
to ``<data>/archive/scanner/exports/``, verifies the row counts and deletes them from ``scanner.sqlite``.
Settlements, CLV, coupons, coupon legs, value legs, scans, configs and bet-type checks are never touched.  With
``--forecasts-keep-days`` the model forecast log (``model_forecasts.sqlite``) is trimmed the same way.  Writes a
weekly gzip backup of the register to ``<data>/archive/scanner/backups/``.  Details:
``model_buk.maintenance.register_retention``.

Usage (from app/)::

    .venv\\Scripts\\python scripts\\compact_scanner_register.py --dry-run
    python scripts/compact_scanner_register.py --forecasts-keep-days 30     # the server's daily job

Makes no API request.  Prints one summary line; exit code 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.maintenance.register_retention import (  # noqa: E402
    DEFAULT_KEEP_DAYS,
    DEFAULT_UNSETTLED_GRACE_DAYS,
    run_register_retention,
)
from model_buk.paths import data_root  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None, help="default: $MODEL_BUK_DATA or the PC's Model-Buk-Data")
    parser.add_argument("--register", type=Path, default=None, help="default: <data-root>/scanner/scanner.sqlite")
    parser.add_argument("--forecast-log", type=Path, default=None, help="default: <data-root>/scanner/model_forecasts.sqlite")
    parser.add_argument("--archive-dir", type=Path, default=None, help="default: <data-root>/archive")
    parser.add_argument("--keep-days", type=float, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--unsettled-grace-days", type=float, default=DEFAULT_UNSETTLED_GRACE_DAYS)
    parser.add_argument("--forecasts-keep-days", type=float, default=None, help="also trim the forecast log (default: off)")
    parser.add_argument("--no-backup", action="store_true", help="skip the weekly register backup")
    parser.add_argument("--vacuum", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = args.data_root or data_root()
    register = args.register or root / "scanner" / "scanner.sqlite"
    forecast_log = args.forecast_log or root / "scanner" / "model_forecasts.sqlite"
    archive_dir = args.archive_dir or root / "archive"
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose else None
    try:
        report = run_register_retention(
            register,
            archive_dir,
            keep_days=args.keep_days,
            unsettled_grace_days=args.unsettled_grace_days,
            forecast_log=forecast_log,
            forecasts_keep_days=args.forecasts_keep_days,
            backup=not args.no_backup,
            vacuum=args.vacuum,
            dry_run=args.dry_run,
            log=log,
        )
    except Exception as exc:  # noqa: BLE001 - one line for the job log
        print(f"error: register retention failed ({type(exc).__name__}: {str(exc)[:300]})")
        return 1
    print(report.summary_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
