#!/usr/bin/env bash
# Daily upload of the archive to the Storage Bucket; local copies deleted 14 days after a verified upload.
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" archive_upload 1800 -- python scripts/archive_to_bucket.py --data-root "$D" --keep-local-days 14
