#!/usr/bin/env bash
# Near-kickoff odds every 15 minutes (was: ClosingOdds, run_closing_odds.ps1 --max-requests 40 --reserve 2500).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" closing_odds 600 --shared odds -- python scripts/collect_closing_odds.py --data-dir "$D/api-football-odds" --directory-dir "$D/fixture-directory" --max-requests 40 --reserve 2500
