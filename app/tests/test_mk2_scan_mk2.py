"""scripts/mk2/scan_mk2.py: overrides of the value definition and where a run may write."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from model_buk.scanner.config import ScanConfig

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mk2" / "scan_mk2.py"
spec = importlib.util.spec_from_file_location("mk2_scan_mk2", SCRIPT)
scan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scan
spec.loader.exec_module(scan)

PROFILE = Path(__file__).resolve().parents[1] / "config" / "mk2" / "profile_band_2leg.json"


def run_for(tmp_path: Path, *extra: str):
    data, out = tmp_path / "Model-Buk-Data", tmp_path / "Model-Buk-Data-MK2"
    args = scan.build_parser().parse_args(["--data-root", str(data), "--out-root", str(out), *extra])
    return (*scan.build_run(args), data, out)


def writes(settings) -> list[Path]:
    return [Path(settings.out_dir), Path(settings.register_path), Path(settings.log_dir)]


def test_profile_and_set_change_the_config_id():
    base = ScanConfig()
    band = ScanConfig(**scan.config_overrides(str(PROFILE), []))
    assert band.coupon_sizes == (2,) and band.max_coupon_odds == 2.0 and band.max_leg_odds == 2.0
    assert band.config_id != base.config_id
    assert scan.config_overrides(None, ["coupon_sizes=(2,)", "min_hit=0.4"]) == {"coupon_sizes": (2,), "min_hit": 0.4}
    with pytest.raises(ValueError, match="unknown ScanConfig fields"):
        scan.config_overrides(None, ["max_coupon_odd=2"])


def test_replay_reads_the_snapshot_and_writes_only_to_out_root(tmp_path):
    config, settings, name, data, out = run_for(tmp_path, "--as-of", "2026-09-30T18:00:00Z")
    assert name == f"replay-{config.config_id}"
    assert not settings.api_enabled
    assert Path(settings.odds_db).is_relative_to(data)
    assert Path(settings.directory_root).is_relative_to(data)
    assert Path(settings.model_cache_dir) == data / "scanner" / "model_cache"
    for path in writes(settings):
        assert path.is_relative_to(out) and not path.is_relative_to(data)


def test_live_keeps_its_own_odds_and_calendar(tmp_path):
    config, settings, name, data, out = run_for(tmp_path, "--live", "--name", "live")
    assert settings.api_enabled and name == "live"
    assert Path(settings.odds_db) == out / "api-football-odds" / "odds.sqlite3"
    assert Path(settings.directory_root) == out / "fixture-directory"
    assert Path(settings.history_db).is_relative_to(data)  # history still comes from the snapshot
    for path in writes(settings):
        assert path.is_relative_to(out)


def write_fit(cache: Path, day: str, fit_as_of: str) -> None:
    manifest = cache / "fits" / day / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"fit_as_of": fit_as_of}), encoding="utf-8")


def test_the_newest_usable_fit_wins(tmp_path):
    data, out = tmp_path / "Model-Buk-Data", tmp_path / "Model-Buk-Data-MK2"
    write_fit(data / "scanner" / "model_cache", "20260930", "2026-09-30T16:01:42Z")
    write_fit(out / "model_cache", "20261013", "2026-10-13T03:00:00Z")
    # live now: our fit is newer than the snapshot's
    _, settings, _, _, _ = run_for(tmp_path, "--live")
    assert Path(settings.model_cache_dir) == out / "model_cache"
    # a replay before our fit existed uses the snapshot's fit
    _, settings, _, _, _ = run_for(tmp_path, "--as-of", "2026-09-30T18:00:00Z")
    assert Path(settings.model_cache_dir) == data / "scanner" / "model_cache"
    # a newer snapshot brings a fresher fit than ours: it wins
    write_fit(data / "scanner" / "model_cache", "20261020", "2026-10-20T03:00:00Z")
    _, settings, _, _, _ = run_for(tmp_path, "--as-of", "2026-10-20T12:00:00Z")
    assert Path(settings.model_cache_dir) == data / "scanner" / "model_cache"
