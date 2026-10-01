"""Build the initial data set for the Railway volume (``seed.tar.gz``) from the PC's Model-Buk-Data.

What goes in (paths relative to the data root, i.e. to /data on the volume):

- ``api-football/raw/`` + ``state.sqlite3``  -- the raw history archive: the nightly job rebuilds the tables from
  it and the refresh jobs append to it (without it a rebuild would replace the history with the few new matches);
- ``api-football/tables/history.sqlite``     -- a fresh SLIM build (no lineup_players / player_match_stats);
- ``api-football-odds/odds.sqlite3``         -- a consistent copy, then the server's retention applied (full
  change history of the last 7 days after kickoff and of every upcoming fixture) and VACUUMed, plus
  ``odds_snapshots.sqlite3`` and the raw scans of the last days;
- ``api-football-prematch/``, ``fixture-directory/`` -- complete (small);
- ``scanner/scanner.sqlite`` (compacted copy), ``model_forecasts.sqlite``, ``latest*.html|csv``, ``html/``, ``csv/``;
- ``scanner/model_cache/`` -- the newest complete fit, the boosters it uses and those of the newest booster
  bucket, the hyper-parameter files (not the 170 MB of older protocol boosters);
- ``predictions/model_comparison.sqlite``    -- the pool weights of the live models;
- ``api-football-extras/tables/extras.sqlite`` (unless ``--no-extras``; no server job reads it today).

Not included: research data (other predictions/*.sqlite, market_test, joined, scanner/offline|replay), logs.

SQLite files are copied with the online backup API, so the PC's jobs may keep running; for a clean cut-over
disable them first (see README.md).  Writes ``seed.tar.gz``, ``seed.tar.gz.sha256`` and ``seed_manifest.json``
to ``--out`` (default ``<data root>/deploy``), prints the sizes and checks that a 5 GB volume keeps at least
1.5 GB free (exit code 1 when it would not).

    app\\.venv\\Scripts\\python deploy\\railway\\build_slim_dataset.py
    app\\.venv\\Scripts\\python deploy\\railway\\build_slim_dataset.py --history-db <existing slim history.sqlite>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app" / "src"))

from model_buk.ingestion.api_football_tables import SLIM_SKIPPED_TABLES, build_history_tables  # noqa: E402
from model_buk.maintenance.odds_retention import run_retention  # noqa: E402
from model_buk.maintenance.register_retention import run_register_retention  # noqa: E402
from model_buk.paths import data_root  # noqa: E402

VOLUME_BYTES = 5 * 10**9
MIN_FREE_BYTES = int(1.5 * 10**9)
GIB = 2**30


def sqlite_copy(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database (online backup; the source may be written meanwhile)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=120)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")


def copy_tree(source: Path, target: Path, *, skip_dirs: tuple[str, ...] = (), sqlite_suffixes: tuple[str, ...] = (".sqlite", ".sqlite3")) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in skip_dirs for part in relative.parts) or not path.is_file():
            continue
        if path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        destination = target / relative
        if path.suffix in sqlite_suffixes:
            sqlite_copy(path, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def model_cache_selection(cache: Path) -> list[Path]:
    """Files of the model cache the server needs: newest complete fit, its boosters and the newest bucket's."""
    fits = sorted(p.parent for p in (cache / "fits").glob("*/manifest.json"))
    if not fits:
        raise SystemExit(f"no complete fit in {cache / 'fits'} -- run fit_live_models.py fit on the PC first")
    newest = fits[-1]
    manifest = json.loads((newest / "manifest.json").read_text(encoding="utf-8"))
    wanted = {str(v.get("path")) for v in (manifest.get("pi") or {}).get("boosters", {}).values() if v.get("path")}
    boosters = list((cache / "boosters").glob("*.cbm"))
    days = sorted({p.name.split("__")[2] for p in boosters if p.name.count("__") >= 3})
    if days:
        wanted |= {p.name for p in boosters if p.name.split("__")[2] == days[-1]}
    selected = [p for p in newest.rglob("*") if p.is_file()]
    selected += [cache / "boosters" / name for name in sorted(wanted) if (cache / "boosters" / name).exists()]
    selected += sorted(cache.glob("hyperparameters_*.json"))
    return selected


def build(source_root: Path, out_dir: Path, *, history_db: Path | None, extras: bool, keep_staging: bool, log=print) -> dict:
    started = time.perf_counter()
    now = datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "seed-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    archive_scratch = out_dir / "seed-archive-scratch"  # retention exports of the copies (the PC keeps the originals)
    if archive_scratch.exists():
        shutil.rmtree(archive_scratch)

    # history: raw (packed straight from the source), state copy, slim tables
    history_src = source_root / "api-football"
    sqlite_copy(history_src / "state.sqlite3", staging / "api-football" / "state.sqlite3")
    slim_target = staging / "api-football" / "tables" / "history.sqlite"
    if history_db is not None:
        log(f"history: using {history_db}")
        sqlite_copy(history_db, slim_target)
    else:
        log("history: building the slim tables from the raw archive (about 2 minutes)")
        build_history_tables(history_src, slim_target, skip_tables=SLIM_SKIPPED_TABLES)

    # odds: copy, server retention, vacuum
    odds_src = source_root / "api-football-odds"
    copy_tree(odds_src, staging / "api-football-odds")
    report = run_retention(staging / "api-football-odds", archive_scratch, now=now, vacuum="always", snapshot_keep_days=30)
    log(f"odds: {report.summary_line()}")

    # small collectors
    copy_tree(source_root / "api-football-prematch", staging / "api-football-prematch")
    copy_tree(source_root / "fixture-directory", staging / "fixture-directory")

    # scanner outputs and register
    scanner_src = source_root / "scanner"
    scanner_dst = staging / "scanner"
    sqlite_copy(scanner_src / "scanner.sqlite", scanner_dst / "scanner.sqlite")
    if (scanner_src / "model_forecasts.sqlite").exists():
        sqlite_copy(scanner_src / "model_forecasts.sqlite", scanner_dst / "model_forecasts.sqlite")
    reg = run_register_retention(scanner_dst / "scanner.sqlite", archive_scratch, now=now, backup=False, vacuum="always")
    log(f"register: {reg.summary_line()}")
    for name in ("latest.html", "latest_value_legs.csv", "latest_coupons.csv", "runs.jsonl"):
        if (scanner_src / name).exists():
            shutil.copy2(scanner_src / name, scanner_dst / name)
    for folder in ("html", "csv"):
        if (scanner_src / folder).exists():
            shutil.copytree(scanner_src / folder, scanner_dst / folder)
    cache_src = scanner_src / "model_cache"
    for path in model_cache_selection(cache_src):
        target = scanner_dst / "model_cache" / path.relative_to(cache_src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    sqlite_copy(source_root / "predictions" / "model_comparison.sqlite", staging / "predictions" / "model_comparison.sqlite")
    if extras and (source_root / "api-football-extras" / "tables" / "extras.sqlite").exists():
        sqlite_copy(source_root / "api-football-extras" / "tables" / "extras.sqlite", staging / "api-football-extras" / "tables" / "extras.sqlite")

    # pack: staging + raw history straight from the source
    tar_path = out_dir / "seed.tar.gz"
    partial = out_dir / "seed.tar.gz.partial"
    sizes: dict[str, int] = {}

    def account(name: str, size: int) -> None:
        top = "/".join(name.split("/")[:2]) if name.startswith(("api-football/", "scanner/", "api-football-odds/")) else name.split("/")[0]
        sizes[top] = sizes.get(top, 0) + size

    total = 0
    with tarfile.open(partial, "w:gz", compresslevel=6) as tar:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                name = path.relative_to(staging).as_posix()
                tar.add(path, arcname=name, recursive=False)
                account(name, path.stat().st_size)
                total += path.stat().st_size
        raw_root = history_src / "raw"
        for path in sorted(raw_root.rglob("*")):
            if path.is_file():
                name = "api-football/" + path.relative_to(history_src).as_posix()
                tar.add(path, arcname=name, recursive=False)
                account(name, path.stat().st_size)
                total += path.stat().st_size
    os.replace(partial, tar_path)
    digest = hashlib.sha256()
    with open(tar_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    (out_dir / "seed.tar.gz.sha256").write_text(f"{digest.hexdigest()}  seed.tar.gz\n", encoding="utf-8")
    compressed = tar_path.stat().st_size
    peak = compressed + total  # archive and extracted files exist together until the archive is deleted
    summary = {
        "built_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(source_root),
        "uncompressed_bytes": total,
        "compressed_bytes": compressed,
        "sha256": digest.hexdigest(),
        "free_after_extract_on_5GB": VOLUME_BYTES - total,
        "free_during_extract_on_5GB": VOLUME_BYTES - peak,
        "by_folder_bytes": dict(sorted(sizes.items(), key=lambda kv: -kv[1])),
        "odds_retention": report.summary_line(),
        "register_retention": reg.summary_line(),
        "seconds": round(time.perf_counter() - started, 1),
    }
    (out_dir / "seed_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not keep_staging:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(archive_scratch, ignore_errors=True)  # exports / old raw scans of the copies: the PC has the originals
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=None, help="data root to read (default: $MODEL_BUK_DATA or the PC's)")
    parser.add_argument("--out", type=Path, default=None, help="default: <source>/deploy")
    parser.add_argument("--history-db", type=Path, default=None, help="use this slim history.sqlite instead of building one")
    parser.add_argument("--no-extras", action="store_true", help="leave api-football-extras out (no server job reads it)")
    parser.add_argument("--keep-staging", action="store_true")
    args = parser.parse_args(argv)
    source = args.source or data_root()
    out = args.out or source / "deploy"
    summary = build(source, out, history_db=args.history_db, extras=not args.no_extras, keep_staging=args.keep_staging)
    print(f"seed: {out / 'seed.tar.gz'}")
    print(f"  uncompressed {summary['uncompressed_bytes'] / GIB:.2f} GiB, compressed {summary['compressed_bytes'] / GIB:.2f} GiB, sha256 {summary['sha256'][:16]}...")
    for folder, size in summary["by_folder_bytes"].items():
        print(f"  {folder:40s} {size / 2**20:9.1f} MiB")
    print(f"  free on a 5 GB volume after extraction: {summary['free_after_extract_on_5GB'] / 10**9:.2f} GB "
          f"(during extraction: {summary['free_during_extract_on_5GB'] / 10**9:.2f} GB)")
    ok = summary["free_after_extract_on_5GB"] >= MIN_FREE_BYTES and summary["free_during_extract_on_5GB"] > 0
    print("  OK: at least 1.5 GB stays free" if ok else "  TOO BIG: less than 1.5 GB would stay free")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
