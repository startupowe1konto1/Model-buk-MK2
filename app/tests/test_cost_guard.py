from __future__ import annotations

import json

import pytest

from model_buk.maintenance.cost_guard import STOP_FLAG, GuardConfig, cost_by_project, run_guard

OURS = "proj-ours"
OTHER = "proj-other"


def _rows(project: str, cpu: float, mem: float, net: float = 0.0, disk: float = 0.0) -> list[dict]:
    return [
        {"measurement": "CPU_USAGE", "value": cpu, "tags": {"projectId": project}},
        {"measurement": "MEMORY_USAGE_GB", "value": mem, "tags": {"projectId": project}},
        {"measurement": "NETWORK_TX_GB", "value": net, "tags": {"projectId": project}},
        {"measurement": "DISK_USAGE_GB", "value": disk, "tags": {"projectId": project}},
    ]


def test_pricing_reproduces_the_railway_dashboard():
    # Values read from the API on 2026-10-01 for a project the dashboard showed at $0.6804.
    cost = cost_by_project(_rows(OTHER, cpu=45.10182846666667, mem=2743.874667861334, net=0.488642971))
    assert cost[OTHER] == pytest.approx(0.6804, abs=0.0002)


def test_month_of_one_vcpu_and_one_gb_costs_thirty_dollars():
    minutes = 30 * 24 * 60
    assert cost_by_project(_rows(OURS, cpu=minutes, mem=minutes))[OURS] == pytest.approx(30.0)
    assert cost_by_project(_rows(OURS, cpu=0, mem=0, disk=5 * minutes))[OURS] == pytest.approx(0.75)


class FakeApi:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append((query, dict(variables)))
        if "deploymentStop" in query:
            return {"deploymentStop": True}
        return {"usage": self.rows}


def _config(**kw):
    base = dict(token="t", workspace_id="ws", project_id=OURS, deployment_id="dep-1", project_limit=3.5, workspace_limit=4.8)
    base.update(kw)
    return GuardConfig(**base)


def test_under_the_limits_nothing_happens(tmp_path):
    api = FakeApi(_rows(OURS, cpu=1000, mem=5000) + _rows(OTHER, cpu=45, mem=2700))
    result = run_guard(_config(), tmp_path, api)
    assert not result.breached and not result.stopped
    assert not (tmp_path / STOP_FLAG).exists()
    assert all("deploymentStop" not in q for q, _ in api.calls)


def test_project_limit_writes_the_flag_and_stops_the_deployment(tmp_path):
    minutes = 30 * 24 * 60
    api = FakeApi(_rows(OURS, cpu=0.1 * minutes, mem=0.2 * minutes))  # $2 + $2 = $4 >= $3.50
    result = run_guard(_config(), tmp_path, api)
    assert result.stopped
    assert result.breached and "project" in result.breached[0]
    flag = json.loads((tmp_path / STOP_FLAG).read_text(encoding="utf-8"))
    assert flag["project_cost"] == pytest.approx(4.0)
    assert api.calls[-1] == ("mutation($id: String!) { deploymentStop(id: $id) }", {"id": "dep-1"})


def test_workspace_limit_counts_the_other_project_too(tmp_path):
    minutes = 30 * 24 * 60
    api = FakeApi(_rows(OURS, cpu=0, mem=0.1 * minutes) + _rows(OTHER, cpu=0, mem=0.4 * minutes))  # $1 + $4
    result = run_guard(_config(), tmp_path, api)
    assert result.project_cost == pytest.approx(1.0)
    assert result.workspace_cost == pytest.approx(5.0)
    assert result.stopped and "workspace" in result.breached[0]


def test_without_a_deployment_id_it_only_writes_the_flag(tmp_path):
    minutes = 30 * 24 * 60
    api = FakeApi(_rows(OURS, cpu=0, mem=0.5 * minutes))
    result = run_guard(_config(deployment_id=None), tmp_path, api)
    assert result.breached and not result.stopped
    assert (tmp_path / STOP_FLAG).exists()


def test_dry_run_never_acts(tmp_path):
    minutes = 30 * 24 * 60
    api = FakeApi(_rows(OURS, cpu=0, mem=0.5 * minutes))
    result = run_guard(_config(), tmp_path, api, act=False)
    assert result.breached and not result.stopped
    assert not (tmp_path / STOP_FLAG).exists()
