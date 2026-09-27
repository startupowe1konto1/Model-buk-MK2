from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_buk.evaluation.context_ablation import (
    EXPECTED_VARIANTS,
    load_context_ablation_plan,
)


PLAN_PATH = Path(__file__).parents[1] / "config" / "context_ablation_plan.example.json"


def test_example_plan_freezes_four_nested_walk_forward_variants():
    plan = load_context_ablation_plan(PLAN_PATH)

    assert plan["validation"]["selection_metric"] == "log_loss"
    assert {
        variant["name"]: tuple(variant["feature_families"])
        for variant in plan["variants"]
    } == EXPECTED_VARIANTS
    assert plan["benchmark"]["closing_market_role"] == "diagnostic_only"
    assert plan["promotion_eligible"] is False


def test_plan_rejects_missing_variant_and_promotion_claim(tmp_path):
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    plan["variants"] = plan["variants"][:-1]
    plan["promotion_eligible"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="four frozen feature variants"):
        load_context_ablation_plan(path)
