#!/usr/bin/env bash
# Fixtures, line-ups, injuries, API predictions every 20 minutes (was: PrematchCollector, run_prematch_collector.ps1).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" prematch_collector 600 -- python scripts/collect_prematch.py --data-dir "$D/api-football-prematch"
