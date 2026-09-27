"""Validated experiment contract for the future context ablation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTEXT_ABLATION_PLAN_VERSION = "context-ablation-plan-v1"
EXPECTED_VARIANTS = {
    "market": ("market",),
    "market_recent_form": ("market", "recent_form"),
    "market_context": ("market", "context"),
    "market_recent_form_context": ("market", "recent_form", "context"),
}


def load_context_ablation_plan(path: str | Path) -> dict[str, Any]:
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read context ablation plan") from exc
    if not isinstance(plan, dict):
        raise ValueError("context ablation plan must be an object")
    if plan.get("schema_version") != CONTEXT_ABLATION_PLAN_VERSION:
        raise ValueError("unsupported context ablation plan schema")
    if plan.get("target") != "result_1x2_regular_time":
        raise ValueError("context ablation target must be result_1x2_regular_time")
    validation = plan.get("validation")
    if not isinstance(validation, dict) or validation.get("method") != "nested_walk_forward":
        raise ValueError("context ablation must use nested_walk_forward validation")
    if validation.get("selection_metric") != "log_loss":
        raise ValueError("context ablation selection metric must be log_loss")
    variants = plan.get("variants")
    if not isinstance(variants, list):
        raise ValueError("context ablation variants must be a list")
    normalized: dict[str, tuple[str, ...]] = {}
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError("each context ablation variant must be an object")
        name = str(variant.get("name") or "").strip()
        families = variant.get("feature_families")
        if not name or name in normalized:
            raise ValueError("context ablation variant names must be unique and non-blank")
        if not isinstance(families, list) or any(not str(item).strip() for item in families):
            raise ValueError("context ablation feature_families must be non-blank strings")
        normalized[name] = tuple(str(item).strip() for item in families)
    if normalized != EXPECTED_VARIANTS:
        raise ValueError("context ablation must contain the four frozen feature variants")
    benchmark = plan.get("benchmark")
    if not isinstance(benchmark, dict):
        raise ValueError("context ablation benchmark must be an object")
    if benchmark.get("closing_market_role") != "diagnostic_only":
        raise ValueError("closing market must remain diagnostic_only")
    if plan.get("promotion_eligible") is not False:
        raise ValueError("unexecuted context ablation plans must not be promotion eligible")
    return plan


__all__ = [
    "CONTEXT_ABLATION_PLAN_VERSION",
    "EXPECTED_VARIANTS",
    "load_context_ablation_plan",
]
