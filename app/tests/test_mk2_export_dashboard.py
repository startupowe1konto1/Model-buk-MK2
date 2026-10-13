"""scripts/mk2/export_dashboard.py: config labels, value helpers and source discovery."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

from model_buk.scanner.config import ScanConfig

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mk2" / "export_dashboard.py"
spec = importlib.util.spec_from_file_location("mk2_export_dashboard", SCRIPT)
exp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = exp
spec.loader.exec_module(exp)


def current_config_json(**changes) -> str:
    """Today's ScanConfig as the register stores it (config_json): every field, nested settings as objects."""
    cfg = {}
    for f in fields(ScanConfig):
        value = getattr(ScanConfig(), f.name)
        if hasattr(value, "__dataclass_fields__"):
            cfg[f.name] = asdict(value)
            continue
        cfg[f.name] = list(value) if isinstance(value, tuple) else value
    cfg.update(changes)
    return json.dumps(cfg)


def test_config_note_names_changed_and_missing_settings():
    assert exp.config_note(current_config_json()) == ("ustawienia jak dziś domyślne", 0)
    note, missing = exp.config_note(current_config_json(coupon_sizes=[2], max_coupon_odds=2.0))
    assert missing == 0 and "coupon_sizes=[2]" in note and "max_coupon_odds=2.0" in note
    old = json.loads(current_config_json(max_edge=0.2))
    del old["max_leg_odds"], old["bet_type_checks"]
    note, missing = exp.config_note(json.dumps(old))
    assert missing == 2 and "max_edge=0.2" in note and "starsza wersja skanera" in note


def test_value_helpers_never_emit_nan():
    assert exp.num(float("nan")) is None and exp.num("x") is None and exp.num(1.23456789, 3) == 1.235
    assert exp.integer(2.6) == 3 and exp.integer(None) is None
    assert exp.iso("2026-09-30T18:00:00Z") == "2026-09-30T18:00Z"
    assert exp.iso("2026-09-30 20:00:00+02:00") == "2026-09-30T18:00Z"
    assert exp.iso(None) is None and exp.iso(float("nan")) is None
    assert exp.text("nan") is None and exp.text("") is None and exp.text("Bet365") == "Bet365"


def test_discover_finds_snapshot_and_own_registers(tmp_path):
    data, out = tmp_path / "Model-Buk-Data", tmp_path / "Model-Buk-Data-MK2"
    for path in (data / "scanner" / "scanner.sqlite", out / "scanner" / "replay-x" / "scanner.sqlite",
                 out / "scanner" / "_old" / "replay-y" / "scanner.sqlite"):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    found = {s.id: s for s in exp.discover(data, out)}
    assert set(found) == {"oskar-live", "mk2-replay-x"}  # folders starting with "_" are skipped
    assert found["oskar-live"].owner == "Oskar" and found["mk2-replay-x"].owner == "MK2"
