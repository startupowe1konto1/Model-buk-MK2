"""Join football-data.co.uk matches (with odds) to API-Football matches.

Offline only: reads the football-data ZIP parts and the API-Football
``history.sqlite`` (opened read-only) and makes no API calls.  Every run
refreshes the ``source=auto`` rows of the team-alias table (human rows
``manual``/``block`` are never touched), joins, and atomically rewrites
``fd_af_matches.sqlite`` plus the Polish ``unmatched_report.md``.  See
app/src/model_buk/ingestion/fd_af_join.py for the rules.

Usage (from the app/ directory):

    .venv\\Scripts\\python scripts\\build_fd_af_join.py

    .venv\\Scripts\\python scripts\\build_fd_af_join.py ^
        --history-db "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\api-football\\tables\\history.sqlite" ^
        --out-dir "C:\\Users\\oskar\\Documents\\Model-Buk-Data\\joined" --no-seed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.ingestion.fd_af_join import (  # noqa: E402
    REASON_LABELS_PL,
    TARGET_MATCH_RATE,
    TOP20_LEAGUE_IDS,
    TOP20_SEASONS,
    run_join,
    summarize_by_league_season,
)

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVES = tuple(
    Path(r"C:\Users\oskar\Desktop\Model Buk") / f"football_data-20260916T173459Z-1-00{part}.zip"
    for part in range(1, 6)
)
DEFAULT_HISTORY_DB = Path(r"C:\Users\oskar\Documents\Model-Buk-Data\api-football\tables\history.sqlite")
DEFAULT_OUT_DIR = Path(r"C:\Users\oskar\Documents\Model-Buk-Data\joined")
DEFAULT_ALIASES = APP_ROOT / "config" / "team_aliases_fd_af.csv"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, action="append", help="football-data ZIP part (repeatable)")
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY_DB, help="API-Football history.sqlite (read-only)")
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES, help="Team alias CSV (human-editable)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Folder for fd_af_matches.sqlite and unmatched_report.md")
    parser.add_argument("--no-seed", action="store_true", help="Do not refresh the auto alias rows; use the file as it is")
    args = parser.parse_args()

    archives = tuple(args.archive) if args.archive else DEFAULT_ARCHIVES
    print(f"football-data archives: {len(archives)}")
    print(f"API history:            {args.history_db}")
    print(f"Alias table:            {args.aliases}")
    print(f"Output folder:          {args.out_dir}")
    summary = run_join(
        archives=archives,
        history_db=args.history_db,
        aliases_path=args.aliases,
        out_dir=args.out_dir,
        seed=not args.no_seed,
        log=lambda message: print("  " + message, flush=True),
    )

    result = summary.result
    table = summarize_by_league_season(result)
    print(f"\nDone in {summary.seconds:.1f}s")
    print(f"Matched rows: {len(result.matched)}   unmatched rows: {len(result.unmatched)}")
    for reason, count in result.unmatched["reason"].value_counts().items():
        print(f"  {reason:24s} {count:7d}  ({REASON_LABELS_PL[reason]})")
    top20 = table[table["league_id"].isin(TOP20_LEAGUE_IDS) & table["fd_season_start"].isin(list(TOP20_SEASONS))]
    print("\nTop-20 leagues, seasons 2015-2025 (matched / eligible):")
    for (league_id, div), group in top20.groupby(["league_id", "fd_div"]):
        eligible, matched = int(group["eligible"].sum()), int(group["matched"].sum())
        rate = matched / eligible if eligible else float("nan")
        print(f"  {div:4s} league {int(league_id):4d}  {matched:6d} / {eligible:6d}  {rate:7.2%}")
    eligible, matched = int(top20["eligible"].sum()), int(top20["matched"].sum())
    overall = matched / eligible if eligible else float("nan")
    verdict = "OK" if overall >= TARGET_MATCH_RATE else "BELOW TARGET"
    print(f"  {'ALL':4s}              {matched:6d} / {eligible:6d}  {overall:7.2%}  (target {TARGET_MATCH_RATE:.0%}: {verdict})")
    if summary.unknown_alias_targets:
        print("\nAlias rows whose team id is not in that league's API matches:")
        for league_id, name, team_id in summary.unknown_alias_targets:
            print(f"  league {league_id}: {name!r} -> {team_id}")
    print(f"\nReport: {args.out_dir / 'unmatched_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
