r"""Pull the server's archive from the Railway Storage Bucket to the PC and merge it into the PC's full history.

Downloads every object under the prefix (default ``archive/``) that ``<data>\archive`` does not hold yet
(verified size and SHA-256, atomic rename), then merges the exports:

- odds exports -> ``<data>\api-football-odds\odds.sqlite3`` (quotes, observations, fixtures, scans; duplicates are
  skipped) and the PC's ``odds_snapshots.sqlite3`` (recomputed for those fixtures; kept forever on the PC);
- scanner leg exports and forecast-log exports -> ``<data>\archive\scanner\register_archive.sqlite``;
- raw odds scans and weekly register backups stay as files under ``<data>\archive``.

Every merged export is recorded (``archive_imports``), so running this again merges nothing twice.  Nothing in the
bucket is changed or deleted.

Setup on the PC (once): ``app\.venv\Scripts\python -m pip install boto3`` and the bucket credentials as user
environment variables (AWS_S3_BUCKET_NAME, AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_DEFAULT_REGION, AWS_S3_URL_STYLE; see deploy\railway\README.md).  They are read from the process environment
or, on Windows, from the user's environment in the registry.

Usage (from app\)::

    .venv\Scripts\python scripts\pull_archive.py
    .venv\Scripts\python scripts\pull_archive.py --dry-run
    .venv\Scripts\python scripts\pull_archive.py --no-download     # only merge what is already downloaded
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.maintenance.bucket_sync import (  # noqa: E402
    DEFAULT_PREFIX,
    BucketConfig,
    PullReport,
    make_client,
    merge_exports,
    pull_archive,
)
from model_buk.paths import data_root  # noqa: E402


def main(argv: list[str] | None = None, *, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None, help="default: $MODEL_BUK_DATA or the PC's Model-Buk-Data")
    parser.add_argument("--archive-dir", type=Path, default=None, help="default: <data-root>/archive")
    parser.add_argument("--odds-dir", type=Path, default=None, help="odds archive to merge into (default: <data-root>/api-football-odds)")
    parser.add_argument("--register-archive", type=Path, default=None, help="default: <archive-dir>/scanner/register_archive.sqlite")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--no-download", action="store_true", help="skip the bucket; merge what is already downloaded")
    parser.add_argument("--no-merge", action="store_true", help="download only")
    parser.add_argument("--no-snapshots", action="store_true", help="do not update the PC's compact snapshot table")
    parser.add_argument("--dry-run", action="store_true", help="list what would be downloaded; merge nothing")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = args.data_root or data_root()
    archive_dir = args.archive_dir or root / "archive"
    odds_dir = args.odds_dir or root / "api-football-odds"
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose or args.dry_run else None
    report = PullReport()
    if not args.no_download:
        try:
            config = BucketConfig.from_env(prefix=args.prefix)
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 2
        try:
            s3 = client if client is not None else make_client(config)
            report = pull_archive(archive_dir, s3, config, dry_run=args.dry_run, log=log)
        except Exception as exc:  # noqa: BLE001
            print(f"error: download failed ({type(exc).__name__}: {str(exc)[:300]})")
            return 1
    if not args.no_merge and not args.dry_run:
        try:
            report.merged = merge_exports(
                archive_dir,
                odds_root=odds_dir,
                register_archive=args.register_archive,
                snapshots=not args.no_snapshots,
                log=log,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"error: merge failed ({type(exc).__name__}: {str(exc)[:300]}); downloaded files are kept, rerun to retry")
            return 1
    print(report.summary_line() + (" DRY-RUN" if args.dry_run else ""))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
