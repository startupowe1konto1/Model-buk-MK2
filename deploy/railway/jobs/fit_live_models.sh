#!/usr/bin/env bash
# Nightly refit of the live models after the history rebuild (was: FitLiveModels, fit --jobs 3; one worker here).
D="${MODEL_BUK_DATA:-/data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" fit_live_models 1800 --shared history -- python scripts/fit_live_models.py --cache-dir "$D/scanner/model_cache" --history-db "$D/api-football/tables/history.sqlite" fit --jobs 1
