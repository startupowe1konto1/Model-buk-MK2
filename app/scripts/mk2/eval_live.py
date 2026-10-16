"""Live check of the statistics model against the bookmakers on finished matches.

For every finished fixture with statistics (data/st_rozliczenia.json) that had odds in the archive,
take the last odds fetch and the last forecast made before kick-off, and compare, line by line
("at least" on X.5 lines only, as the app bets): the model's chance, the bookmakers' margin-free
chance and what happened.  Prints how well each side forecast (Brier score, lower is better), how
the model's edge over the market translated into hits, and the paper result after the 12% tax of
simple betting rules, by statistic, scope and odds band.

Usage: python eval_live.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stats_model as sm  # noqa: E402
import stats_picks as sp  # noqa: E402

DATA = Path(r"C:\Users\mkoni\Documents\Model-Buk-Data-MK2\stats")
SETTLE = HERE.parents[1] / "dashboard_mk2" / "app" / "data" / "st_rozliczenia.json"
TAX = 0.88


def rows() -> list[dict]:
    res = json.loads(SETTLE.read_text(encoding="utf-8"))
    od = sqlite3.connect(DATA / "odds.sqlite")
    pr = sqlite3.connect(DATA / "predictions.sqlite")
    out = []
    for fid, r in res.items():
        fid = int(fid)
        start = r["start"].replace("+00:00", "Z")
        last = od.execute("select max(fetched_at) from odds where fixture_id = ? and fetched_at < ?", (fid, start)).fetchone()[0]
        made = pr.execute("select max(made_at) from predictions where fixture_id = ? and made_at < ?", (fid, start)).fetchone()[0]
        if not last or not made:
            continue
        pred = {s: (mh, ma, a, at, kh, ka, kt) for s, mh, ma, a, at, kh, ka, kt in pr.execute(
            "select stat, mu_home, mu_away, alpha, alpha_total, k_home, k_away, k_total from predictions "
            "where fixture_id = ? and made_at = ?", (fid, made))}
        quotes = defaultdict(dict)
        for bm, stat, scope, line, side, odd in od.execute(
                "select bookmaker, stat, scope, line, side, odd from odds where fixture_id = ? and fetched_at = ?", (fid, last)):
            quotes[(stat, scope, line)].setdefault(bm, {})[side] = odd
        for (stat, scope, line), books in quotes.items():
            if stat not in sp.PICK_STATS or stat not in pred or not sp.is_half_line(line):
                continue
            v = r["s"].get(stat)
            if not v or v[0] is None:
                continue
            actual = v[0] if scope == "home" else v[1] if scope == "away" else v[0] + v[1]
            mh, ma, a, at, kh, ka, kt = pred[stat]
            mu = {"home": mh * kh, "away": ma * ka, "total": (mh + ma) * kt}[scope]
            p = sm.nb_over(mu, at if scope == "total" else a, line)[0]
            fair = [1 / b["over"] / (1 / b["over"] + 1 / b["under"]) for b in books.values() if "over" in b and "under" in b]
            overs = {bm: b["over"] for bm, b in books.items() if "over" in b}
            if not fair or not overs:
                continue
            med = float(np.median(list(overs.values())))
            odd = max(o for o in overs.values() if o <= med * sp.MAX_PRICE_OVER_MEDIAN)
            pin = books.get("Pinnacle", {})
            q_pin = 1 / pin["over"] / (1 / pin["over"] + 1 / pin["under"]) if "over" in pin and "under" in pin else None
            out.append({"fixture_id": fid, "liga_id": r["liga_id"], "mecz": f"{r['gosp']} – {r['gosc']}", "stat": stat,
                        "scope": scope, "line": line, "p": p, "q": float(np.mean(fair)), "q_pin": q_pin, "odd": odd,
                        "band": sp.band_of(odd), "hit": int(actual > line), "actual": actual, "mu": mu})
    return out


def brier(x: list[dict], key: str) -> float:
    return float(np.mean([(r[key] - r["hit"]) ** 2 for r in x]))


def bet(x: list[dict]) -> str:
    if not x:
        return "—"
    prof = sum((r["odd"] * TAX - 1) if r["hit"] else -1 for r in x)
    return (f"n {len(x):4d}  trafione {sum(r['hit'] for r in x):4d} ({np.mean([r['hit'] for r in x]):.0%})  "
            f"model {np.mean([r['p'] for r in x]):.0%}  rynek {np.mean([r['q'] for r in x]):.0%}  "
            f"wynik {prof * 100:+8.0f} zł  ({prof / len(x):+.1%})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json")
    args = ap.parse_args()
    x = rows()
    print(f"lines: {len(x)} on {len({r['fixture_id'] for r in x})} finished matches")
    print("\nBrier (lower is better): model vs bookmakers")
    for key in sorted({(r["stat"], r["scope"]) for r in x}):
        g = [r for r in x if (r["stat"], r["scope"]) == key]
        print(f"  {key[0]:8s} {key[1]:6s} n {len(g):5d}  model {brier(g, 'p'):.4f}  rynek {brier(g, 'q'):.4f}")
    print(f"  all              n {len(x):5d}  model {brier(x, 'p'):.4f}  rynek {brier(x, 'q'):.4f}")
    pin = [r for r in x if r["q_pin"] is not None]
    if pin:
        print(f"  with Pinnacle    n {len(pin):5d}  model {brier(pin, 'p'):.4f}  rynek {brier(pin, 'q'):.4f}  "
              f"Pinnacle {brier(pin, 'q_pin'):.4f}")
    print("\nEdge (model minus market) and what happened, lines priced 1.25-2.00")
    inb = [r for r in x if r["band"] is not None]
    for lo, hi in ((-1, -0.04), (-0.04, 0.0), (0.0, 0.04), (0.04, 0.08), (0.08, 0.15), (0.15, 1)):
        print(f"  edge {lo:+.2f}..{hi:+.2f}: " + bet([r for r in inb if lo <= r["p"] - r["q"] < hi]))
    print("\nRules after tax, by odds band")
    rules = {
        "z wartością + przewaga 4-15 pkt": lambda r: 0.04 <= r["p"] - r["q"] <= 0.15 and r["p"] * r["odd"] * TAX >= 1,
        "z wartością (każda przewaga ≤15)": lambda r: r["p"] - r["q"] <= 0.15 and r["p"] * r["odd"] * TAX >= 1,
        "wszystkie linie": lambda r: True,
    }
    for name, f in rules.items():
        print(f"  {name}")
        for b in (0, 1, 2):
            print(f"    pasmo {b}: " + bet([r for r in inb if r["band"] == b and f(r)]))
    print("\nBy statistic and scope (z wartością + przewaga 4-15 pkt, 1.25-2.00)")
    for key in sorted({(r["stat"], r["scope"]) for r in inb}):
        print(f"  {key[0]:8s} {key[1]:6s} " + bet([r for r in inb if (r["stat"], r["scope"]) == key and rules['z wartością + przewaga 4-15 pkt'](r)]))
    if args.json:
        Path(args.json).write_text(json.dumps(x, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
