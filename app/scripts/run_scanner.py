"""Run one scan of the value scanner (paper mode) and print one summary line.

Every run looks at every upcoming match with odds in the archive, prices every leg against the fair price,
records the scan in the paper register, settles earlier paper legs, records closing line value and writes the
page and the CSV files (see ``model_buk.scanner.pipeline`` and ``docs/SKANER.md``).

Usage (from app/):

    .venv\\Scripts\\python scripts\\run_scanner.py                 # live run: now, API for calendar and statistics
    .venv\\Scripts\\python scripts\\run_scanner.py --no-api        # no API request; uses what the archives hold
    .venv\\Scripts\\python scripts\\run_scanner.py --offline       # research run: no API, separate outputs
    .venv\\Scripts\\python scripts\\run_scanner.py --as-of 2026-09-30T18:00:00Z   # replay a decision time
    .venv\\Scripts\\python scripts\\run_scanner.py --days-ahead 2  # only matches starting within two days

``--offline`` never touches the live paper register or page: its register, page, CSV and run log go to
``Model-Buk-Data\\scanner\\offline\\``.  ``--as-of`` makes no API request (a replay must not fetch today's calendar).
``--days-ahead`` is part of the value definition, so such a run is a separate paper experiment (another config id).

The API key comes from the API_FOOTBALL_KEY environment variable (or the user-level variable of the same name)
and is never printed or stored.  Requests stop when the daily remaining quota falls to ``--reserve`` (1500).
Exit code 0 on success, 1 when the run failed (the traceback goes to ``Model-Buk-Data\\logs\\scanner_errors.log``).
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from model_buk.scanner.config import RunSettings, ScanConfig  # noqa: E402
from model_buk.scanner.engine import DATA_ROOT  # noqa: E402

OFFLINE_SUBDIR = "offline"


def api_key() -> str | None:
    """The API key from the process environment, else the user-level environment variable (Windows)."""
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if key:
        return key
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, "API_FOOTBALL_KEY")
            return str(value).strip() or None
        except OSError:
            return None
    return None


def make_client(key: str):
    """The real API-Football client (``download_history.RealHistoryClient``)."""
    from download_history import RealHistoryClient

    return RealHistoryClient(key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", default=None, help="decision time (UTC ISO); default now. Implies --no-api")
    parser.add_argument("--offline", action="store_true", help="no API, outputs to scanner\\offline (live register untouched)")
    parser.add_argument("--no-api", action="store_true", help="make no API request (calendar and statistics not refreshed)")
    parser.add_argument("--days-ahead", type=float, default=None, help="only matches starting within this many days (default: all)")
    parser.add_argument("--data-root", default=str(DATA_ROOT), help="root of Model-Buk-Data")
    parser.add_argument("--reserve", type=int, default=1500, help="stop API requests at this daily remaining quota")
    parser.add_argument("--no-settle", action="store_true", help="skip settlement and closing line value")
    parser.add_argument("--verbose", action="store_true", help="progress messages on stderr")
    return parser


def settings_from(args: argparse.Namespace) -> RunSettings:
    root = Path(args.data_root)
    api = not (args.no_api or args.offline or args.as_of)
    out_dir = root / "scanner" / OFFLINE_SUBDIR if args.offline else None
    return RunSettings(
        data_root=root,
        out_dir=out_dir,
        api_enabled=api,
        api_reserve=int(args.reserve),
        settle=not args.no_settle,
    )


def main(argv: list[str] | None = None) -> int:
    from model_buk.scanner.pipeline import run_pipeline

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.days_ahead is not None and not args.days_ahead > 0:
        parser.error("--days-ahead must be a positive number of days (leave it out to scan every upcoming match)")
    config = ScanConfig(max_days_ahead=args.days_ahead) if args.days_ahead is not None else ScanConfig()
    settings = settings_from(args)
    log = (lambda text: print(text, file=sys.stderr, flush=True)) if args.verbose else None
    try:
        client = None
        if settings.api_enabled:
            key = api_key()
            client = make_client(key) if key else None
        result = run_pipeline(config, as_of=args.as_of, settings=settings, client=client, log=log)
    except Exception as exc:  # noqa: BLE001 - one line for the scheduler log, the traceback to a file
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            settings.log_dir.mkdir(parents=True, exist_ok=True)
            with open(settings.log_dir / "scanner_errors.log", "a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}]\n{traceback.format_exc()}\n")
        except OSError:
            pass
        print(f"error: scan failed ({type(exc).__name__}: {str(exc)[:200]})")
        return 1
    print(result.summary_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
