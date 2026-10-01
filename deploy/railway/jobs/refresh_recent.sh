#!/usr/bin/env bash
# Finished matches of the last days, hourly (was: RefreshRecentMatches, refresh_current_season.py --mode dates).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" refresh_recent 900 --shared history -- python scripts/refresh_current_season.py --mode dates --data-dir "$D/api-football"
