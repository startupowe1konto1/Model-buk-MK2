"""One scanner run of the MK2 copy, with Oskar's data as a read-only baseline.

``scripts/run_scanner.py`` writes its register, page, CSV files, logs and replays under the data root, i.e. into
the ``Model-Buk-Data`` folder that we receive from Oskar and replace whenever he sends a new snapshot.  This
wrapper runs the same pipeline (:func:`model_buk.scanner.pipeline.run_pipeline`) but keeps everything it writes in
a separate folder, so a new snapshot never wipes our paper register and our runs never touch his:

    <data-root>   Documents\\Model-Buk-Data       odds archive, history, fixture directory, prematch, model cache
                                                   (read only)
    <out-root>    Documents\\Model-Buk-Data-MK2   scanner\\<name>\\ (register, page, CSV, run log), logs\\

A replay (``--as-of``) reads everything from the snapshot.  A live run (``--live``) reads the odds archive and the
fixture directory that *we* keep current (``<out-root>\\api-football-odds``, ``<out-root>\\fixture-directory``,
filled by ``run_job.ps1 -Job odds_collector`` and by the scanner's own calendar refresh), and the history and
pre-match archive from the snapshot.  The model cache is ``<out-root>\\model_cache`` once our nightly fit has written
a fit there that is newer than the
snapshot's (as of the decision time), else the snapshot's.

Value definition: ``ScanConfig`` defaults (Oskar's live experiment) unless ``--profile`` (a JSON object of
``ScanConfig`` fields) and/or ``--set field=value`` change it.  Every change gives another ``config_id``, so it is
a separate paper experiment; the register keeps experiments apart by that id.

Usage (from app/):

    .venv\\Scripts\\python scripts\\mk2\\scan_mk2.py --as-of 2026-09-30T18:00:00Z
    .venv\\Scripts\\python scripts\\mk2\\scan_mk2.py --as-of 2026-09-30T18:00:00Z --profile config\\mk2\\profile_band_2leg.json
    .venv\\Scripts\\python scripts\\mk2\\scan_mk2.py --as-of 2026-09-30T18:00:00Z --set max_coupon_odds=2.0 --set "coupon_sizes=(2,)"
    .venv\\Scripts\\python scripts\\mk2\\scan_mk2.py --live --name live    # now; the API is used only with --live
    .venv\\Scripts\\python scripts\\mk2\\compare_replays.py <out-root>\\scanner\\<name>\\scanner.sqlite

Replays work only where the snapshot holds fresh boards (``max_quote_age_hours``: 3 h near, 8 h far) and, for the
models, a fit at most 24 h older than ``--as-of`` (see docs/MK2-DANE.md for the windows of the current snapshot).

``--as-of`` (a replay) never makes an API request.  Without ``--live`` no run makes one either.  The API key, when
used, comes from API_FOOTBALL_KEY (process or user-level variable) and is never printed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import traceback
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent
sys.path.insert(0, str(APP / "src"))
sys.path.insert(0, str(APP / "scripts"))

from model_buk.scanner.config import RunSettings, ScanConfig  # noqa: E402

DEFAULT_DATA_ROOT = Path(os.environ.get("MODEL_BUK_DATA") or Path.home() / "Documents" / "Model-Buk-Data")
DEFAULT_OUT_ROOT = Path(os.environ.get("MODEL_BUK_MK2_OUT") or Path.home() / "Documents" / "Model-Buk-Data-MK2")


def config_overrides(profile: str | None, assignments: list[str]) -> dict:
    """``ScanConfig`` overrides from a JSON profile and ``field=value`` pairs (Python literals; later wins)."""
    known = {f.name for f in fields(ScanConfig)}
    overrides: dict = {}
    if profile:
        loaded = json.loads(Path(profile).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{profile}: the profile must be a JSON object of ScanConfig fields")
        overrides.update({k: v for k, v in loaded.items() if not k.startswith("_")})
    for item in assignments:
        name, sep, raw = item.partition("=")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"--set expects field=value, got {item!r}")
        try:
            overrides[name] = ast.literal_eval(raw.strip())
        except (ValueError, SyntaxError):
            overrides[name] = raw.strip()
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(f"unknown ScanConfig fields: {', '.join(unknown)}")
    for name, value in list(overrides.items()):
        if isinstance(value, list):  # JSON has no tuples; every sequence field of ScanConfig is a tuple
            overrides[name] = tuple(value)
    return overrides


def newest_fit(cache_dir: Path, until: pd.Timestamp) -> pd.Timestamp:
    """``fit_as_of`` of the newest complete fit in a model cache made at or before ``until`` (minimum if none)."""
    best = pd.Timestamp.min.tz_localize("UTC")
    for manifest in cache_dir.glob("fits/*/manifest.json"):
        try:
            stamp = pd.Timestamp(json.loads(manifest.read_text(encoding="utf-8"))["fit_as_of"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        if best < stamp <= until:
            best = stamp
    return best


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    when = parser.add_mutually_exclusive_group()
    when.add_argument("--as-of", default=None, help="decision time of a replay (UTC ISO); never uses the API")
    when.add_argument("--live", action="store_true", help="scan now and use the API for calendar and statistics")
    parser.add_argument(
        "--name",
        default=None,
        help="experiment folder under <out-root>\\scanner (default: replay-<config id> / live / now). A register keeps one "
        "scan per decision time, so two configs replayed at the same --as-of need two folders",
    )
    parser.add_argument("--profile", default=None, help="JSON object of ScanConfig fields")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="ScanConfig override (repeatable)")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Oskar's data snapshot (read only)")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="where this run writes")
    parser.add_argument("--model-cache", default=None, help="model cache (default: <out-root>\\model_cache when it has a fit, else the snapshot's)")
    parser.add_argument("--reserve", type=int, default=3000, help="with --live: stop API requests at this remaining quota")
    parser.add_argument("--no-settle", action="store_true", help="skip settlement and closing line value")
    parser.add_argument("--verbose", action="store_true", help="progress messages on stderr")
    return parser


def build_run(args: argparse.Namespace) -> tuple[ScanConfig, RunSettings, str]:
    """The value definition, the run settings and the experiment name of the arguments.

    Every path the pipeline writes (register, page, CSV, run log, error log) is under ``--out-root``; with
    ``--live`` so are the odds archive and the fixture directory, which a live run refreshes.
    """
    data_root = Path(args.data_root)
    out_root = Path(args.out_root)
    config = ScanConfig(**config_overrides(args.profile, args.set))
    name = args.name or (f"replay-{config.config_id}" if args.as_of else "live" if args.live else "now")
    own_cache = out_root / "model_cache"
    snapshot_cache = data_root / "scanner" / "model_cache"
    if args.model_cache:
        model_cache = Path(args.model_cache)
    else:
        # the cache with the newest fit usable at the decision time: ours once our nightly fit runs, the
        # snapshot's when a newer snapshot brings a fresher fit than ours
        until = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now(tz="UTC")
        until = until.tz_localize("UTC") if until.tzinfo is None else until.tz_convert("UTC")
        model_cache = own_cache if newest_fit(own_cache, until) > newest_fit(snapshot_cache, until) else None
    settings = RunSettings(
        data_root=data_root,
        odds_db=out_root / "api-football-odds" / "odds.sqlite3" if args.live else None,
        directory_root=out_root / "fixture-directory" if args.live else None,
        model_cache_dir=model_cache,
        out_dir=out_root / "scanner" / name,
        log_dir=out_root / "logs",
        api_enabled=bool(args.live),
        api_reserve=int(args.reserve),
        settle=not args.no_settle,
    )
    return config, settings, name


def main(argv: list[str] | None = None) -> int:
    from model_buk.scanner.pipeline import run_pipeline

    args = build_parser().parse_args(argv)
    config, settings, name = build_run(args)
    client = None
    if settings.api_enabled:
        from run_scanner import api_key, make_client

        key = api_key()
        client = make_client(key) if key else None
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose else None
    try:
        result = run_pipeline(config, as_of=args.as_of, settings=settings, client=client, log=log)
    except Exception as exc:  # noqa: BLE001 - one line for the scheduler log, the traceback to a file
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(settings.log_dir / "scan_mk2_errors.log", "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {name} cfg={config.config_id}\n{traceback.format_exc()}\n")
        print(f"error: scan failed ({type(exc).__name__}: {str(exc)[:200]})")
        return 1
    print(f"name={name} {result.summary_line()}")
    if not result.recorded:
        print(
            f"warning: not recorded - {settings.register_path} already holds a scan at this decision time "
            "(one scan per decision time per register); use another --name for another config"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
