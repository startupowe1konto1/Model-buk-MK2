"""Bring a new snapshot of Oskar's ``Model-Buk-Data`` into the local baseline folder.

The baseline (``Documents\\Model-Buk-Data``) is a copy of his data root: odds archive, match history, fixture
directory, pre-match archive, model cache, research outputs.  We read it, we do not write it (our scanner runs
go to ``Documents\\Model-Buk-Data-MK2``, see ``scan_mk2.py``), so a newer snapshot simply replaces it.  This
script makes that replacement safe and visible:

1. **Plan (default, nothing is written).**  Compares the snapshot (a ``.zip`` whose top folder is
   ``Model-Buk-Data``, or a folder) with the baseline file by file: new, changed (size or time differs), same,
   and files that exist only locally.  For every changed SQLite database it shows the approximate rows per table
   before and after (``max(rowid)``), so you see e.g. how many matches or odds scans the new snapshot adds.
2. **Apply (``--apply``).**  Extracts the new and changed files into a staging folder next to the baseline,
   checks their sizes (and CRCs for a zip), then swaps them in one by one; every replaced file is first moved to
   ``Model-Buk-Data._prev_<time>\\`` (``--no-backup`` skips that; ``--keep-prev`` backups are kept).  Files that
   exist only locally are never deleted (``--prune`` moves them into the same backup).  The record of the
   import goes to ``Model-Buk-Data\\_import\\import_<time>.json``.

Usage (from app/):

    .venv\\Scripts\\python scripts\\mk2\\import_data.py C:\\Users\\mkoni\\Downloads\\Model-Buk-Data.zip
    .venv\\Scripts\\python scripts\\mk2\\import_data.py C:\\Users\\mkoni\\Downloads\\Model-Buk-Data.zip --apply

Stop the scheduled MK2 jobs before ``--apply`` (``register_tasks.ps1 -Unregister`` or disable them): a
database that is open while it is replaced is refused (its ``-wal`` / ``-journal`` file is present).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGET = Path(os.environ.get("MODEL_BUK_DATA") or Path.home() / "Documents" / "Model-Buk-Data")
TOP = "Model-Buk-Data"
OWN_DIRS = ("_import",)  # written by this script; never compared or pruned
TRANSIENT_SUFFIXES = ("-shm",)  # SQLite shared-memory index: rebuilt on open, any reader touches it
MTIME_TOLERANCE_S = 2.0  # zip timestamps have a 2-second resolution
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")


@dataclass(frozen=True)
class Entry:
    size: int
    mtime: float


def zip_entries(zf: zipfile.ZipFile) -> dict[str, tuple[Entry, zipfile.ZipInfo]]:
    """Files of the zip below its ``Model-Buk-Data/`` folder (or its root when there is no such folder)."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    prefix = TOP + "/" if infos and all(i.filename.startswith(TOP + "/") for i in infos) else ""
    out: dict[str, tuple[Entry, zipfile.ZipInfo]] = {}
    for info in infos:
        rel = info.filename[len(prefix):]
        if not rel or rel.endswith(TRANSIENT_SUFFIXES):
            continue
        out[rel] = (Entry(info.file_size, time.mktime(info.date_time + (0, 0, -1))), info)
    return out


def folder_entries(root: Path) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel.split("/", 1)[0] in OWN_DIRS or rel.endswith(TRANSIENT_SUFFIXES):
                continue
            st = path.stat()
            out[rel] = Entry(st.st_size, st.st_mtime)
    return out


def same(a: Entry, b: Entry) -> bool:
    return a.size == b.size and abs(a.mtime - b.mtime) <= MTIME_TOLERANCE_S


def table_rows(path: Path) -> dict[str, int] | str:
    """Approximate rows per table (``max(rowid)``; fast on big tables), or the error text."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            names = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            rows: dict[str, int] = {}
            for name in names:
                try:
                    rows[name] = int(con.execute(f'select coalesce(max(rowid), 0) from "{name}"').fetchone()[0])
                except sqlite3.DatabaseError:  # WITHOUT ROWID tables
                    rows[name] = int(con.execute(f'select count(*) from "{name}"').fetchone()[0])
            return rows
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def busy_databases(target: Path) -> list[str]:
    return sorted(
        p.relative_to(target).as_posix()
        for suffix in ("-wal", "-journal")
        for p in target.rglob(f"*{suffix}")
        if p.stat().st_size > 0
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="snapshot: a .zip with a Model-Buk-Data folder, or a folder")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="baseline folder (default: %(default)s)")
    parser.add_argument("--apply", action="store_true", help="copy the new and changed files in (default: plan only)")
    parser.add_argument("--no-backup", action="store_true", help="do not keep the replaced files")
    parser.add_argument("--keep-prev", type=int, default=1, help="backups of earlier imports to keep (default: 1)")
    parser.add_argument("--prune", action="store_true", help="also move files that are not in the snapshot into the backup")
    parser.add_argument("--list", type=int, default=25, help="how many changed files to list (default: 25)")
    args = parser.parse_args(argv)

    source = Path(args.source)
    target = Path(args.target)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zf = zipfile.ZipFile(source) if source.is_file() else None
    if zf is not None:
        src = {rel: entry for rel, (entry, _) in zip_entries(zf).items()}
        infos = {rel: info for rel, (_, info) in zip_entries(zf).items()}
    elif source.is_dir():
        root = source / TOP if (source / TOP).is_dir() else source
        src = folder_entries(root)
        infos = {}
    else:
        parser.error(f"no such snapshot: {source}")
    dst = folder_entries(target) if target.is_dir() else {}

    new = sorted(r for r in src if r not in dst)
    changed = sorted(r for r in src if r in dst and not same(src[r], dst[r]))
    unchanged = [r for r in src if r in dst and same(src[r], dst[r])]
    local_only = sorted(r for r in dst if r not in src)
    incoming = sum(src[r].size for r in new + changed)

    print(f"snapshot : {source}  ({len(src)} files, {human(sum(e.size for e in src.values()))})")
    print(f"baseline : {target}  ({len(dst)} files)")
    print(f"new {len(new)} | changed {len(changed)} | unchanged {len(unchanged)} | only local {len(local_only)} | to copy {human(incoming)}")
    for label, items in (("new", new), ("changed", changed), ("only local (kept)" if not args.prune else "only local (pruned)", local_only)):
        if items:
            print(f"-- {label}:")
            for rel in items[: args.list]:
                print(f"   {rel}  {human(src[rel].size) if rel in src else human(dst[rel].size)}")
            if len(items) > args.list:
                print(f"   ... and {len(items) - args.list} more")

    db_changes = [r for r in changed if r.endswith(DB_SUFFIXES)]
    if not args.apply:
        for rel in db_changes:
            print(f"-- {rel}: rows now {table_rows(target / rel)}")
        print("plan only; nothing was written. Run again with --apply to import.")
        return 0

    if not new and not changed and not (args.prune and local_only):
        print("baseline already matches the snapshot; nothing to do.")
        return 0
    busy = busy_databases(target)
    if busy:
        print("refused: these databases look open (stop the scheduled jobs first): " + ", ".join(busy))
        return 2
    free = shutil.disk_usage(target.parent).free
    if free < incoming * 1.2 + (0 if args.no_backup else 0):
        print(f"refused: {human(free)} free, {human(incoming)} needed")
        return 2

    before = {rel: table_rows(target / rel) for rel in db_changes}
    staging = target.parent / f"{target.name}._staging_{stamp}"
    backup = target.parent / f"{target.name}._prev_{stamp}"
    staging.mkdir(parents=True)
    t0 = time.time()
    for rel in new + changed:
        out = staging / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if zf is not None:
            with zf.open(infos[rel]) as s, open(out, "wb") as d:  # reading to EOF checks the CRC
                shutil.copyfileobj(s, d, 8 << 20)
        else:
            shutil.copyfile(root / rel, out)
        os.utime(out, (src[rel].mtime, src[rel].mtime))
        if out.stat().st_size != src[rel].size:
            print(f"refused: size mismatch after extraction of {rel}; staging left at {staging}")
            return 3
    print(f"staged {len(new) + len(changed)} files in {time.time() - t0:.0f}s")

    replaced: list[str] = []
    for rel in new + changed:
        dest = target / rel
        if dest.exists():
            if args.no_backup:
                dest.unlink()
            else:
                keep = backup / rel
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(keep))
            replaced.append(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging / rel), str(dest))
    if args.prune:
        for rel in local_only:
            keep = backup / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target / rel), str(keep))
    shutil.rmtree(staging, ignore_errors=True)

    after = {rel: table_rows(target / rel) for rel in db_changes}
    for rel in db_changes:
        print(f"-- {rel}")
        old, cur = before[rel], after[rel]
        if isinstance(old, dict) and isinstance(cur, dict):
            for name in sorted(set(old) | set(cur)):
                if old.get(name) != cur.get(name):
                    print(f"   {name}: {old.get(name, '-')} -> {cur.get(name, '-')}")
        else:
            print(f"   before {old} | after {cur}")

    prev = sorted(p for p in target.parent.glob(f"{target.name}._prev_*") if p.is_dir())
    for old_backup in prev[: max(len(prev) - max(args.keep_prev, 0), 0)]:
        shutil.rmtree(old_backup, ignore_errors=True)

    record = {
        "imported_at_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(source),
        "source_sha256": sha256_of(source) if zf is not None else None,
        "target": str(target),
        "new": new,
        "changed": changed,
        "only_local": local_only,
        "pruned": bool(args.prune),
        "backup": None if args.no_backup else str(backup),
        "db_rows_before": before,
        "db_rows_after": after,
    }
    (target / "_import").mkdir(exist_ok=True)
    out_record = target / "_import" / f"import_{stamp}.json"
    out_record.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"imported: {len(new)} new, {len(changed)} changed; backup: {record['backup']}; record: {out_record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
