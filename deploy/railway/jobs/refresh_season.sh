#!/usr/bin/env bash
# Full current-season sweep, daily (was: RefreshCurrentSeason at logon; leagues mode, ~50-80 requests).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" refresh_season 1800 --shared history -- python scripts/refresh_current_season.py --data-dir "$D/api-football"
