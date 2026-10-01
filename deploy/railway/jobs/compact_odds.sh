#!/usr/bin/env bash
# Daily odds retention: archive the change history of fixtures older than 7 days, keep 30 days of snapshots.
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" compact_odds 1800 --exclusive odds -- python scripts/compact_odds_archive.py --data-root "$D" --snapshot-keep-days 30
