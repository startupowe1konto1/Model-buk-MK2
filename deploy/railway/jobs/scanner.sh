#!/usr/bin/env bash
# Value scanner in paper mode at :15 and :45; writes scanner/latest.html (was: ValueScanner, run_scanner.ps1).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" scanner 600 --shared odds --shared register -- python scripts/run_scanner.py --data-root "$D"
