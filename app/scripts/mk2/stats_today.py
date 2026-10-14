"""Price the statistics markets of chosen fixtures with the team-statistics model.

For each fixture: expected shots, shots on target, corners, fouls, cards and offsides of both
teams, the probability of every over/under line the bookmakers offer, the market's own
probability (each bookmaker's two prices with the margin removed, averaged), the best price and
the expected value per unit stake without tax and with the Polish 12% stake tax.  Also writes
the teams' recent form (last 5 matches, all competitions) so the numbers can be explained.

Usage (from app/):
    .venv\\Scripts\\python scripts\\mk2\\stats_today.py --league 2 --date 2026-10-14
Output: <mk2>/stats/analysis/<date>_<league>.json (and a short summary on screen).
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats_model as sm  # noqa: E402
from stats_gap import LEAGUES, Client, api_key  # noqa: E402

TAX = 0.88
BETS: dict[int, tuple[str, str]] = {
    211: ("shots", "total"), 221: ("shots", "home"), 220: ("shots", "away"),
    87: ("sot", "total"), 88: ("sot", "home"), 89: ("sot", "away"), 243: ("sot", "home"), 244: ("sot", "away"),
    45: ("corners", "total"), 57: ("corners", "home"), 58: ("corners", "away"),
    80: ("cards", "total"), 82: ("cards", "home"), 83: ("cards", "away"),
    150: ("yellow", "home"), 151: ("yellow", "away"),
    173: ("fouls", "total"), 171: ("fouls", "home"), 170: ("fouls", "away"),
    164: ("offsides", "total"), 167: ("offsides", "home"), 168: ("offsides", "away"),
}
VALUE = re.compile(r"^(Over|Under)\s+(\d+(?:\.\d+)?)$")


def parse_odds(payload: dict) -> list[dict]:
    """Flat rows (stat, scope, line, side, bookmaker, odd) for the statistics markets."""
    rows = []
    for block in payload.get("response", []):
        for bm in block.get("bookmakers", []):
            for bet in bm.get("bets", []):
                key = BETS.get(bet.get("id"))
                if not key:
                    continue
                for v in bet.get("values", []):
                    m = VALUE.match(str(v.get("value", "")).strip())
                    try:
                        odd = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if m and odd > 1.0:
                        rows.append({"stat": key[0], "scope": key[1], "line": float(m.group(2)),
                                     "side": m.group(1).lower(), "bookmaker": bm["name"], "odd": odd})
    return rows


def form(df: pd.DataFrame, team: int, n: int = 5) -> dict:
    d = df[df["team_id"] == team].sort_values("kickoff").tail(n)
    opp = df.set_index(["fixture_id", "team_id"])
    out = {"mecze": [], "srednie": {}}
    for _, r in d.iterrows():
        o = opp.loc[(r["fixture_id"], r["opp_id"])]
        out["mecze"].append({"data": r["kickoff"].strftime("%d.%m"), "rywal": r["opp"], "dom": bool(r["home"]),
                             "rozgrywki": LEAGUES.get(int(r["league_id"]), str(r["league_id"])),
                             **{f"{s}_za": _num(r[sm.STATS[s]]) for s in ("shots", "sot", "corners", "fouls", "cards")},
                             **{f"{s}_przeciw": _num(o[sm.STATS[s]]) for s in ("shots", "sot", "corners", "fouls", "cards")}})
    for s in ("shots", "sot", "corners", "fouls", "cards", "offsides"):
        col = sm.STATS[s]
        mine = d[col].dropna()
        theirs = [opp.loc[(f, o)][col] for f, o in zip(d["fixture_id"], d["opp_id"])]
        theirs = [x for x in theirs if pd.notna(x)]
        out["srednie"][s] = {"za": round(float(mine.mean()), 2) if len(mine) else None,
                             "przeciw": round(float(np.mean(theirs)), 2) if theirs else None}
    return out


def _num(v):
    return None if pd.isna(v) else float(v)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--league", type=int, required=True)
    p.add_argument("--date", default=dt.date.today().isoformat())
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--half-life", type=float, default=365.0)
    args = p.parse_args()

    client = Client(api_key(), max_requests=40, reserve=3000)
    fx = client.get("/fixtures", {"league": args.league, "season": args.season, "date": args.date,
                                  "timezone": "Europe/Warsaw"})["response"]
    fx = [f for f in fx if f["fixture"]["status"]["short"] in ("NS", "TBD")]
    print(f"{len(fx)} fixtures not started")

    df = sm.load_rows(list(LEAGUES))
    leagues = sm.home_league(df)
    asof = pd.Timestamp.now(tz="UTC")
    fits = {s: sm.fit_stat(df, s, asof, half_life_days=args.half_life, leagues=leagues) for s in sm.STATS}

    out = {"utworzono": asof.isoformat(timespec="seconds"), "liga": args.league, "data": args.date, "mecze": []}
    for f in fx:
        fid = f["fixture"]["id"]
        h, a = f["teams"]["home"], f["teams"]["away"]
        cup = args.league in sm.CUPS
        means = {}
        for s, fit in fits.items():
            mh = fit.mean(h["id"], a["id"], True, cup)
            ma = fit.mean(a["id"], h["id"], False, cup)
            means[s] = {"home": round(mh, 2), "away": round(ma, 2), "total": round(mh + ma, 2)}
        odds = parse_odds(client.get("/odds", {"fixture": fid}))
        lines = []
        frame = pd.DataFrame(odds)
        if not frame.empty:
            for (stat, scope, line), g in frame.groupby(["stat", "scope", "line"]):
                fit = fits[stat]
                mu = means[stat][scope]
                alpha = fit.alpha_total if scope == "total" else fit.alpha
                p_over, p_push, p_under = sm.nb_over(mu, alpha, line)
                piv = g.pivot_table(index="bookmaker", columns="side", values="odd", aggfunc="max")
                fair = []
                for bm, r in piv.iterrows():
                    if pd.notna(r.get("over")) and pd.notna(r.get("under")):
                        io, iu = 1 / r["over"], 1 / r["under"]
                        fair.append((bm, io / (io + iu), io + iu - 1))
                q_over = float(np.mean([x[1] for x in fair])) if fair else None
                pin = next((x[1] for x in fair if x[0] == "Pinnacle"), None)
                row = {"stat": stat, "scope": scope, "line": line, "p_over": round(p_over, 4),
                       "p_under": round(p_under, 4), "p_push": round(p_push, 4),
                       "rynek_over": round(q_over, 4) if q_over is not None else None,
                       "pinnacle_over": round(pin, 4) if pin is not None else None,
                       "bukmacherzy": sorted(piv.index.tolist())}
                for side, p_win in (("over", p_over), ("under", p_under)):
                    if side in piv.columns and piv[side].notna().any():
                        best_bm = piv[side].idxmax()
                        best = float(piv[side].max())
                        row[f"kurs_{side}"] = best
                        row[f"buk_{side}"] = best_bm
                        row[f"kursy_{side}"] = {bm: float(o) for bm, o in piv[side].dropna().items()}
                        row[f"ev_{side}"] = round(p_win * best + p_push - 1, 4)
                        row[f"ev_{side}_podatek"] = round(p_win * best * TAX + p_push * TAX - 1, 4)
                lines.append(row)
        out["mecze"].append({
            "fixture_id": fid, "start": f["fixture"]["date"], "gospodarz": h["name"], "gosc": a["name"],
            "gospodarz_id": h["id"], "gosc_id": a["id"], "sedzia": f["fixture"].get("referee"),
            "srednie": means, "linie": lines,
            "forma": {"gospodarz": form(df, h["id"]), "gosc": form(df, a["id"])},
        })

    dest = sm.OUT / "stats" / "analysis"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{args.date}_{args.league}.json"
    out["model"] = {s: {"home": round(float(np.exp(f.b_home)), 3), "puchar": round(float(np.exp(f.b_cup)), 3),
                        "alpha": round(f.alpha, 4), "alpha_total": round(f.alpha_total, 4), "wiersze": f.n_rows}
                    for s, f in fits.items()}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written {path}; requests {client.used}, account remaining {client.remaining}")


if __name__ == "__main__":
    main()
