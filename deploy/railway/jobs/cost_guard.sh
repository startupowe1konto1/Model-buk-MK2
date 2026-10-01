#!/usr/bin/env bash
# Hourly cost guard: prices the workspace's billing-period usage and stops this deployment when the project or the
# workspace reaches its limit (COST_GUARD_PROJECT_LIMIT_USD / COST_GUARD_WORKSPACE_LIMIT_USD); see railway_cost_guard.py.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/run_job.sh" cost_guard 120 -- python scripts/railway_cost_guard.py
