"""Upload new files of ``<data>/archive`` to the Railway Storage Bucket (S3 API) and prune old local copies.

Every final archive file (odds and register exports, moved raw odds scans, weekly register backups) is uploaded
once to ``<prefix><relative path>`` with its SHA-256 in the object metadata and verified with a HEAD request;
already uploaded files are skipped (local state in ``<data>/archive/.upload_state.sqlite``, plus a HEAD check for
files the state does not know).  Local files older than ``--keep-local-days`` are deleted only after a verified
upload and a fresh HEAD confirming the bucket copy.  Details: ``model_buk.maintenance.bucket_sync``.

Credentials (environment; never printed): AWS_S3_BUCKET_NAME, AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION, AWS_S3_URL_STYLE -- the names Railway's bucket "AWS SDK" preset and
``railway bucket credentials`` use (BUCKET / ENDPOINT / ACCESS_KEY_ID / SECRET_ACCESS_KEY / REGION also work).

Usage::

    python scripts/archive_to_bucket.py                    # the server's daily job
    python scripts/archive_to_bucket.py --dry-run          # list what would be uploaded

Prints one summary line; exit code 0 when every file is safe in the bucket, 1 when any upload failed, 2 when the
credentials are missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.maintenance.bucket_sync import DEFAULT_PREFIX, BucketConfig, make_client, upload_archive  # noqa: E402
from model_buk.paths import data_root  # noqa: E402


def main(argv: list[str] | None = None, *, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None, help="default: $MODEL_BUK_DATA or the PC's Model-Buk-Data")
    parser.add_argument("--archive-dir", type=Path, default=None, help="default: <data-root>/archive")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="key prefix in the bucket (default: archive/)")
    parser.add_argument("--keep-local-days", type=float, default=14.0, help="delete verified local files older than this; <0 keeps all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    archive_dir = args.archive_dir or (args.data_root or data_root()) / "archive"
    try:
        config = BucketConfig.from_env(prefix=args.prefix)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose else None
    try:
        s3 = client if client is not None else make_client(config)
        report = upload_archive(
            archive_dir,
            s3,
            config,
            keep_local_days=None if args.keep_local_days < 0 else args.keep_local_days,
            dry_run=args.dry_run,
            log=log,
        )
    except Exception as exc:  # noqa: BLE001 - one line for the job log
        print(f"error: upload failed ({type(exc).__name__}: {str(exc)[:300]})")
        return 1
    print(f"{report.summary_line()} {config.describe()}{' DRY-RUN' if args.dry_run else ''}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
