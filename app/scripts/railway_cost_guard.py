"""Hourly cost guard for the Railway service (see model_buk.maintenance.cost_guard).

Environment (Railway injects the RAILWAY_* ids; the user sets the token):
  RAILWAY_GUARD_TOKEN              workspace or account API token (secret; set with `variable set --stdin`)
  COST_GUARD_WORKSPACE_ID          the workspace id (shown by `railway usage projects --json`)
  RAILWAY_PROJECT_ID, RAILWAY_DEPLOYMENT_ID   injected by Railway
  COST_GUARD_PROJECT_LIMIT_USD     default 3.50
  COST_GUARD_WORKSPACE_LIMIT_USD   default 4.80
Without a token the guard reports itself inactive and exits 0; the job wrappers' daily runtime budget and the
workspace's $10 hard limit still apply.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.maintenance.cost_guard import GuardConfig, requests_poster, run_guard  # noqa: E402
from model_buk.paths import data_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="only print the costs; never write the flag or stop")
    args = parser.parse_args()
    token = os.environ.get("RAILWAY_GUARD_TOKEN", "").strip()
    workspace = os.environ.get("COST_GUARD_WORKSPACE_ID", "").strip()
    project = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
    if not (token and workspace and project):
        print("guard inactive: RAILWAY_GUARD_TOKEN, COST_GUARD_WORKSPACE_ID or RAILWAY_PROJECT_ID not set")
        return 0
    config = GuardConfig(
        token=token,
        workspace_id=workspace,
        project_id=project,
        deployment_id=os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip() or None,
        project_limit=float(os.environ.get("COST_GUARD_PROJECT_LIMIT_USD", "3.50")),
        workspace_limit=float(os.environ.get("COST_GUARD_WORKSPACE_LIMIT_USD", "4.80")),
    )
    result = run_guard(config, data_root(), requests_poster(token), act=not args.dry_run)
    print(result.summary() + (" (deployment stopped)" if result.stopped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
