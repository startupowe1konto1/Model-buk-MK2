"""Stop the Railway service before the bill goes past what the user agreed to.

The workspace already has a $10 hard limit, but reaching it takes every
workload offline (the user's other project too) and the user wants the total
bill to stay at the Hobby plan's $5. This guard runs hourly inside the
container, reads the billing-period usage of the workspace from Railway's
public GraphQL API, prices it with Railway's per-minute rates and, when either

* this project's usage reaches ``project_limit`` (default $3.50), or
* the whole workspace's usage reaches ``workspace_limit`` (default $4.80),

writes ``<data>/.cost_guard_stop`` (every job wrapper then skips its run) and
stops the service's current deployment, so compute billing ends. Restarting is
a deliberate human act: delete the flag and redeploy.

Prices (https://docs.railway.com/pricing): RAM $10/GB-month, CPU $20/vCPU-month,
volume $0.15/GB-month, all metered per minute (a month = 30 days = 43,200
minutes), egress $0.05/GB. The API reports CPU in vCPU-minutes, memory and
volume in GB-minutes and egress in GB; on 2026-10-01 these rates reproduced the
dashboard's project cost to the cent ($0.6805 computed vs $0.6804 shown).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

API_URL = "https://backboard.railway.com/graphql/v2"
MINUTES_PER_MONTH = 30 * 24 * 60
PRICE_PER_UNIT: dict[str, float] = {
    "CPU_USAGE": 20.0 / MINUTES_PER_MONTH,  # per vCPU-minute
    "MEMORY_USAGE_GB": 10.0 / MINUTES_PER_MONTH,  # per GB-minute
    "DISK_USAGE_GB": 0.15 / MINUTES_PER_MONTH,  # per GB-minute (volume)
    "NETWORK_TX_GB": 0.05,  # per GB
}
STOP_FLAG = ".cost_guard_stop"

USAGE_QUERY = (
    "query($workspaceId: String!) { usage(workspaceId: $workspaceId, measurements: "
    "[CPU_USAGE, MEMORY_USAGE_GB, NETWORK_TX_GB, DISK_USAGE_GB]) { measurement value tags { projectId } } }"
)
STOP_MUTATION = "mutation($id: String!) { deploymentStop(id: $id) }"

Poster = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class GuardConfig:
    token: str
    workspace_id: str
    project_id: str
    deployment_id: str | None
    project_limit: float = 3.50
    workspace_limit: float = 4.80


@dataclass
class GuardResult:
    project_cost: float
    workspace_cost: float
    breached: list[str] = field(default_factory=list)
    stopped: bool = False

    def summary(self) -> str:
        state = "STOP " + "; ".join(self.breached) if self.breached else "ok"
        return f"project=${self.project_cost:.4f} workspace=${self.workspace_cost:.4f} -> {state}"


def cost_by_project(rows: list[Mapping[str, Any]]) -> dict[str, float]:
    """Price the API's usage rows; unknown measurements are ignored, missing values count as zero usage."""

    costs: dict[str, float] = {}
    for row in rows:
        price = PRICE_PER_UNIT.get(str(row.get("measurement")))
        if price is None:
            continue
        project = str(((row.get("tags") or {}).get("projectId")) or "")
        costs[project] = costs.get(project, 0.0) + float(row.get("value") or 0.0) * price
    return costs


def requests_poster(token: str, timeout: float = 30.0) -> Poster:
    import requests  # imported lazily so the pricing logic stays testable without network libraries

    def post(query: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
        response = requests.post(
            API_URL,
            json={"query": query, "variables": dict(variables)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Railway API error: {payload['errors']}")
        return payload["data"]

    return post


def run_guard(
    config: GuardConfig, data_root: Path, post: Poster, *, now: datetime | None = None, act: bool = True
) -> GuardResult:
    """Price the usage and, when a limit is reached and ``act`` is true, write the stop flag and stop the deployment."""

    data = post(USAGE_QUERY, {"workspaceId": config.workspace_id})
    costs = cost_by_project(list(data.get("usage") or []))
    result = GuardResult(project_cost=costs.get(config.project_id, 0.0), workspace_cost=sum(costs.values()))
    if result.project_cost >= config.project_limit:
        result.breached.append(f"project usage ${result.project_cost:.2f} >= ${config.project_limit:.2f}")
    if result.workspace_cost >= config.workspace_limit:
        result.breached.append(f"workspace usage ${result.workspace_cost:.2f} >= ${config.workspace_limit:.2f}")
    if not result.breached or not act:
        return result

    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    flag = Path(data_root) / STOP_FLAG
    flag.write_text(
        json.dumps({"stopped_at": stamp, "reasons": result.breached, "project_cost": result.project_cost,
                    "workspace_cost": result.workspace_cost}, indent=2),
        encoding="utf-8",
    )
    if config.deployment_id:
        post(STOP_MUTATION, {"id": config.deployment_id})
        result.stopped = True
    return result


__all__ = [
    "API_URL",
    "PRICE_PER_UNIT",
    "STOP_FLAG",
    "GuardConfig",
    "GuardResult",
    "cost_by_project",
    "requests_poster",
    "run_guard",
]
