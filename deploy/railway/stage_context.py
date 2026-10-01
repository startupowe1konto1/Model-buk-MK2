"""Build the small upload folder for ``railway up``: exactly what the image needs, LF line endings, config at its root.

The repository holds gigabytes of research material that must not be uploaded on every deploy.  This copies

    app/src, app/scripts, app/config (no __pycache__), app/requirements-server.txt, deploy/railway

into a fresh folder, keeps the repository layout (so ``deploy/railway/Dockerfile`` builds the same from either
place), converts the shell scripts and the crontab to LF, and puts ``railway.json`` and ``.dockerignore`` at the
folder's root, where Railway and Docker look for them.  Then deploy with

    railway up <folder> --path-as-root --service model-buk --detach

Usage::

    python deploy/railway/stage_context.py                      # -> <data root>/deploy/context
    python deploy/railway/stage_context.py --out C:\\tmp\\ctx

Prints the folder and its size.  Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COPY_DIRS = ("app/src", "app/scripts", "app/config", "deploy/railway")
COPY_FILES = ("app/requirements-server.txt",)
LF_SUFFIXES = (".sh",)
LF_NAMES = ("crontab", "Dockerfile")
SKIP_PARTS = ("__pycache__", ".pytest_cache", ".venv")
SKIP_SUFFIXES = (".pyc", ".pyo", ".patch")


def _default_out() -> Path:
    sys.path.insert(0, str(REPO / "app" / "src"))
    from model_buk.paths import data_root

    return data_root() / "deploy" / "context"


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix in LF_SUFFIXES or source.name in LF_NAMES:
        target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
        if source.suffix == ".sh":
            os.chmod(target, 0o755)
    else:
        shutil.copy2(source, target)


def stage(repo: Path, out: Path) -> Path:
    """Write the context into ``out`` (emptied first); returns ``out``."""
    repo = Path(repo)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for folder in COPY_DIRS:
        for source in sorted((repo / folder).rglob("*")):
            relative = source.relative_to(repo)
            if not source.is_file() or any(part in SKIP_PARTS for part in relative.parts) or source.suffix in SKIP_SUFFIXES:
                continue
            _copy(source, out / relative)
    for name in COPY_FILES:
        _copy(repo / name, out / name)
    shutil.copy2(repo / "deploy" / "railway" / "railway.json", out / "railway.json")
    shutil.copy2(repo / "deploy" / "railway" / ".dockerignore", out / ".dockerignore")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="default: <data root>/deploy/context")
    args = parser.parse_args(argv)
    out = stage(REPO, args.out or _default_out())
    files = [p for p in out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"staged {len(files)} files ({size / 2**20:.1f} MiB) in {out}")
    print(f'deploy: railway up "{out}" --path-as-root --service model-buk --detach')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
