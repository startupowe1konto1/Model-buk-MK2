"""Compare scanner experiments (config ids) recorded in one paper register, scan by scan.

Reads a register written by ``scan_mk2.py`` (read only) and prints Markdown tables:

* per scan and config: value legs under each tax regime, proposed (top) coupons, how many pass at tau 0.88 / 0.94
  / 1.00 on the lower-bound EV, the best lower-bound EV at 0.88 (how far the closest coupon is from paying the
  12% stake tax), total-odds range and the share of coupons inside the 1.30-2.00 band;
* per config: the edge of the value legs (``edge_raw``: price over fair price minus one) against the per-leg
  thresholds of a 2-leg coupon at tau 0.88 (6.6% to break even, 9.2% for +5% EV) and the 10% plausibility cap.

Usage (from app/):

    .venv\\Scripts\\python scripts\\mk2\\compare_replays.py C:\\Users\\mkoni\\Documents\\Model-Buk-Data-MK2\\scanner\\replay-compare\\scanner.sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

BAND = (1.30, 2.00)
LEG_BREAK_EVEN_2 = (1 / 0.88) ** 0.5 - 1  # 6.6%
LEG_EV5_2 = (1.05 / 0.88) ** 0.5 - 1  # 9.2%


def markdown(frame: pd.DataFrame) -> str:
    """A Markdown table without the optional ``tabulate`` dependency of ``DataFrame.to_markdown``."""
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join(lines)


def read(db: Path, sql: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, con)
    finally:
        con.close()


def label_configs(scans: pd.DataFrame) -> dict[str, str]:
    """A short readable label per config id: the ScanConfig fields that differ from the first config seen."""
    configs = scans.drop_duplicates("config_id")[["config_id", "config_json"]]
    parsed = {row.config_id: json.loads(row.config_json) for row in configs.itertuples()}
    if not parsed:
        return {}
    base_id = next(iter(parsed))
    base = parsed[base_id]
    labels = {}
    for cid, cfg in parsed.items():
        diff = {k: v for k, v in cfg.items() if base.get(k) != v}
        labels[cid] = cid + (" (" + ", ".join(f"{k}={v}" for k, v in diff.items()) + ")" if diff else " (base)")
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("register", help="scanner.sqlite of one experiment folder")
    args = parser.parse_args(argv)
    db = Path(args.register)

    scans = read(db, "SELECT scan_id, as_of, config_id, config_json, n_fixtures, n_value, diagnostics_json FROM scans ORDER BY as_of, started_at")
    coupons = read(db, "SELECT * FROM coupons WHERE is_top = 1")
    legs = read(
        db,
        "SELECT scan_id, config_id, leg_key, odd, edge_raw, edge_raw_lower, tier, value_t100,"
        " suspect, unsynced, vetoed FROM legs",
    )
    # Only plausible legs (not suspect: edge <= 10% etc., not unsynced, not vetoed) can be proposed.  The leg log
    # is change-only (a leg is written again only when its price moves or at a heartbeat), so per-scan counts come
    # from the scan's own diagnostics and the leg table is used only for the edge distribution, one row per
    # (config, leg, price).
    legs = legs[(legs.suspect == 0) & (legs.unsynced == 0) & (legs.vetoed == 0)]
    legs = legs.drop_duplicates(["config_id", "leg_key", "odd"])
    labels = label_configs(scans)
    print("Configs:")
    for cid, text in labels.items():
        print(f"- {text}")
    print()

    rows = []
    for scan in scans.itertuples():
        diag = json.loads(scan.diagnostics_json or "{}")
        by_tau = (diag.get("pipeline") or {}).get("value_by_tau") or {}
        sc = coupons[(coupons.scan_id == scan.scan_id) & (coupons.config_id == scan.config_id)]
        in_band = sc.odds_total.between(*BAND).mean() if len(sc) else float("nan")
        rows.append({
            "as_of": scan.as_of,
            "config": scan.config_id,
            "fixtures": diag.get("n_fixtures", scan.n_fixtures),
            "value legs t088/t094/t100": "/".join(str(by_tau.get(t, "?")) for t in ("t088", "t094", "t100")),
            "top coupons": len(sc),
            "pass t088/t094/t100": f"{int(sc.pass_t088.sum())}/{int(sc.pass_t094.sum())}/{int(sc.pass_t100.sum())}" if len(sc) else "-",
            "best EV-low t088": f"{sc.ev_net_lower_t088.max():+.1%}" if len(sc) else "-",
            "odds total min-max": f"{sc.odds_total.min():.2f}-{sc.odds_total.max():.2f}" if len(sc) else "-",
            "in 1.30-2.00": f"{in_band:.0%}" if len(sc) else "-",
            "p_hit median": f"{sc.p_hit.median():.0%}" if len(sc) else "-",
        })
    table = pd.DataFrame(rows)
    print(markdown(table))
    print()

    print("Edge of value legs (tau 1.00 value list) against the per-leg thresholds of a 2-leg coupon at tau 0.88:")
    out = []
    for cid, part in legs[legs.value_t100 == 1].groupby("config_id"):
        e = part.edge_raw
        out.append({
            "config": cid,
            "value legs": len(part),
            "median edge": f"{e.median():.1%}",
            ">= 6.6% (2-leg break-even)": f"{(e >= LEG_BREAK_EVEN_2).mean():.0%}",
            ">= 9.2% (2-leg EV +5%)": f"{(e >= LEG_EV5_2).mean():.0%}",
            "lower-bound edge >= 6.6%": f"{(part.edge_raw_lower >= LEG_BREAK_EVEN_2).mean():.0%}",
            "median odd": f"{part.odd.median():.2f}",
            "liquid share": f"{(part.tier == 'liquid').mean():.0%}",
        })
    print(markdown(pd.DataFrame(out)) if out else "(no value legs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
