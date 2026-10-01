#!/usr/bin/env bash
# Hourly pre-match odds of every league (was: ModelBuk task OddsCollector, run_odds_collector.ps1). About 30-65 API requests.
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" odds_collector 900 --shared odds -- python scripts/collect_odds.py --data-dir "$D/api-football-odds"
