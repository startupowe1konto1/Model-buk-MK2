#!/usr/bin/env bash
# Nightly history: download what is missing, then rebuild the SLIM tables (was: NightlyHistoryBuild without the research-only football-data join).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DROP_CACHE_ARGS=--include-raw  # this job reads the 12 000 raw history files
exec "$HERE/run_job.sh" history_build 3600 --exclusive history -- bash -c 'python scripts/download_history.py --data-dir "$0/api-football" --max-requests 5000 --reserve 1500 && python scripts/build_history_tables.py --slim --archive-root "$0/api-football"' "$D"
