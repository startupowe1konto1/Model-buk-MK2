"""scripts/mk2/import_data.py: plan, apply with backup, and refusal while a database is open."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mk2" / "import_data.py"
spec = importlib.util.spec_from_file_location("mk2_import_data", SCRIPT)
imp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = imp  # dataclasses look the module up while the class is created
spec.loader.exec_module(imp)

OLD = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
NEW = time.mktime((2026, 10, 12, 12, 0, 0, 0, 0, -1))


def make_db(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("create table scans (id integer primary key, at text)")
    con.executemany("insert into scans (at) values (?)", [(f"t{i}",) for i in range(rows)])
    con.commit()
    con.close()


def write(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture()
def baseline(tmp_path: Path) -> Path:
    root = tmp_path / "Model-Buk-Data"
    make_db(root / "api-football-odds" / "odds.sqlite3", 3)
    os.utime(root / "api-football-odds" / "odds.sqlite3", (OLD, OLD))
    write(root / "logs" / "odds_collector.log", "old\n", OLD)
    write(root / "fixture-directory" / "README.md", "same\n", OLD)
    write(root / "scanner" / "replay" / "ours.txt", "local only\n", OLD)
    write(root / "scanner" / "scanner.sqlite-shm", "x", OLD)
    return root


def snapshot_zip(tmp_path: Path, baseline: Path) -> Path:
    src = tmp_path / "src" / "Model-Buk-Data"
    make_db(src / "api-football-odds" / "odds.sqlite3", 5)
    os.utime(src / "api-football-odds" / "odds.sqlite3", (NEW, NEW))
    write(src / "logs" / "odds_collector.log", "old\nnew\n", NEW)
    write(src / "fixture-directory" / "README.md", "same\n", OLD)
    write(src / "predictions" / "new.sqlite", "", NEW)
    write(src / "scanner" / "scanner.sqlite-shm", "yy", NEW)
    out = tmp_path / "Model-Buk-Data.zip"
    with zipfile.ZipFile(out, "w") as zf:
        for path in sorted(src.parent.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src.parent).as_posix())
    return out


def test_plan_writes_nothing(tmp_path, baseline, capsys):
    source = snapshot_zip(tmp_path, baseline)
    before = sorted(p.as_posix() for p in baseline.rglob("*"))
    assert imp.main([str(source), "--target", str(baseline)]) == 0
    out = capsys.readouterr().out
    assert "new 1 | changed 2 | unchanged 1 | only local 1" in out  # -shm is ignored on both sides
    assert sorted(p.as_posix() for p in baseline.rglob("*")) == before


def test_apply_replaces_backs_up_and_keeps_local_files(tmp_path, baseline):
    source = snapshot_zip(tmp_path, baseline)
    assert imp.main([str(source), "--target", str(baseline), "--apply"]) == 0

    assert (baseline / "logs" / "odds_collector.log").read_text(encoding="utf-8") == "old\nnew\n"
    assert (baseline / "predictions" / "new.sqlite").exists()
    assert (baseline / "scanner" / "replay" / "ours.txt").exists()  # never deleted without --prune
    con = sqlite3.connect(baseline / "api-football-odds" / "odds.sqlite3")
    assert con.execute("select count(*) from scans").fetchone()[0] == 5
    con.close()

    backups = list(tmp_path.glob("Model-Buk-Data._prev_*"))
    assert len(backups) == 1
    assert (backups[0] / "logs" / "odds_collector.log").read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob("Model-Buk-Data._staging_*"))

    records = list((baseline / "_import").glob("import_*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["new"] == ["predictions/new.sqlite"]
    assert record["db_rows_before"]["api-football-odds/odds.sqlite3"] == {"scans": 3}
    assert record["db_rows_after"]["api-football-odds/odds.sqlite3"] == {"scans": 5}

    # a second import of the same snapshot is a no-op
    assert imp.main([str(source), "--target", str(baseline), "--apply"]) == 0
    assert len(list(tmp_path.glob("Model-Buk-Data._prev_*"))) == 1


def test_apply_refuses_while_a_database_is_open(tmp_path, baseline, capsys):
    source = snapshot_zip(tmp_path, baseline)
    write(baseline / "api-football-odds" / "odds.sqlite3-wal", "pending", NEW)
    assert imp.main([str(source), "--target", str(baseline), "--apply"]) == 2
    assert "refused" in capsys.readouterr().out
    assert (baseline / "logs" / "odds_collector.log").read_text(encoding="utf-8") == "old\n"
