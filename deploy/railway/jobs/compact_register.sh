#!/usr/bin/env bash
# Daily register retention (old near-miss legs, forecast log after 30 days) and the weekly register backup.
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" compact_register 1200 --exclusive register -- python scripts/compact_scanner_register.py --data-root "$D" --forecasts-keep-days 30
