"""Does a match's imbalance change how its counts move together?  (McHale & Scarf: little dependence in even
matches, negative in mismatches.)

The bet-builder copula (eval_builder.py -> config/mk2/bb_korelacje.json) uses one correlation matrix for every
match.  This test splits the 2025/26 walk-forward matches by imbalance - the gap between the two sides' expected
goals, |log(mu_goals_home / mu_goals_away)| - into thirds, estimates the copula on the first half of the season
(Aug-Jan) once for all matches and once per third, and prices bet-builder templates on the second half (Feb-May)
both ways.  Rule fixed before looking: per-third matrices are adopted only if they lower the out-of-sample
log-loss of the templates as a whole and of the two-team templates.

Usage (from app/):  .venv\\Scripts\\python scripts\\mk2\\eval_copula_gap.py
Output: <mk2>/stats/eval_copula_gap/ (matches.csv.gz cache, report.json); with --write, the per-third matrices
are added to config/mk2/bb_korelacje.json under "wg_przewagi".
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_builder as eb  # noqa: E402
import eval_stats_model as ev  # noqa: E402
import stats_model as sm  # noqa: E402
from stats_sure import GOALS_FIT  # noqa: E402

OUT = sm.OUT / "stats" / "eval_copula_gap"
SPLIT = pd.Timestamp("2026-02-01", tz="UTC")     # estimate before, test from here
N_SIM = 4000
RNG = np.random.default_rng(20261103)


def walk_forward() -> pd.DataFrame:
    """One row per 2025/26 match: the twelve counts and their walk-forward forecasts (as eval_builder.main)."""
    cache = OUT / "matches.csv.gz"
    if cache.exists():
        m = pd.read_csv(cache, index_col=0)
        m["kickoff_h"] = pd.to_datetime(m["kickoff_h"], utc=True)
        return m
    leagues = ev.model_leagues("config")
    df = sm.load_rows(list(leagues), first_season=ev.FIRST_SEASON)
    test = df[(df["season"] == ev.TEST_SEASON) & (df["kickoff"] >= ev.TEST_FROM) & (df["kickoff"] < ev.TEST_TO)].copy()
    starts = list(pd.date_range(ev.TEST_FROM, ev.TEST_TO, freq="MS", inclusive="left"))
    bounds = [(a, a + pd.offsets.MonthBegin(1)) for a in starts]
    leagues_m = {a: sm.home_league(df[df["kickoff"] < b]) for a, b in bounds}
    t0 = time.time()
    preds = {}
    for s in eb.COUNTS:
        hl, rt = ((GOALS_FIT["half_life_days"], GOALS_FIT["ridge_team"]) if s == "goals"
                  else (ev.DEFAULT["half_life_days"], ev.DEFAULT["ridge_team"]))
        preds[s] = ev.predict(df, test, s, hl, rt, bounds, leagues_m, [], "as_is")
        print(f"{s}: {preds[s]['mu'].notna().sum()} forecasts, {time.time() - t0:.0f}s", flush=True)
    cols = {s: (sm.STATS.get(s) or sm.EXTRA_STATS[s]) for s in eb.COUNTS}
    t = test[["fixture_id", "side", "cup", "kickoff", "league_id"] + list(cols.values())].copy()
    for s in eb.COUNTS:
        t[f"mu_{s}"], t[f"al_{s}"] = preds[s]["mu"], preds[s]["alpha"]
    h = t[t["side"] == "home"].set_index("fixture_id")
    a = t[t["side"] == "away"].set_index("fixture_id")
    m = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    need = [f"{cols[s]}_{x}" for s in eb.COUNTS for x in ("h", "a")] + [f"mu_{s}_{x}" for s in eb.COUNTS for x in ("h", "a")]
    m = m.dropna(subset=need)
    OUT.mkdir(parents=True, exist_ok=True)
    m.to_csv(cache)
    return m


def scores_of(m: pd.DataFrame) -> tuple[dict, dict, np.ndarray]:
    cols = {s: (sm.STATS.get(s) or sm.EXTRA_STATS[s]) for s in eb.COUNTS}
    cdfs, ys, sc = {}, {}, {}
    for side, x in (("home", "h"), ("away", "a")):
        for s in eb.COUNTS:
            key = f"{side}_{s}"
            cdfs[key] = eb.cdf_table(m[f"mu_{s}_{x}"].to_numpy(float), m[f"al_{s}_{x}"].to_numpy(float), eb.K_MAX[s])
            ys[key] = m[f"{cols[s]}_{x}"].to_numpy(float)
            sc[key] = eb.pit_scores(ys[key], cdfs[key], RNG)
    return cdfs, ys, np.column_stack([sc[k] for k in eb.DIMS])


def latent(cdfs: dict, Z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    obs = np.corrcoef(Z, rowvar=False)
    lat = np.eye(len(eb.DIMS))
    for i in range(len(eb.DIMS)):
        for j in range(i + 1, len(eb.DIMS)):
            lat[i, j] = lat[j, i] = eb.latent_rho(cdfs[eb.DIMS[i]], cdfs[eb.DIMS[j]], obs[i, j])
    return eb.nearest_pd(lat), obs


def sub(cdfs: dict, mask: np.ndarray) -> dict:
    return {k: v[mask] for k, v in cdfs.items()}


TEMPLATES = {
    # two-team templates: the two sides' counts together
    "hS+aS": lambda X, t: (X["home_shots"] >= t["home_shots"]) & (X["away_shots"] >= t["away_shots"]),
    "hC+aC": lambda X, t: (X["home_sot"] >= t["home_sot"]) & (X["away_sot"] >= t["away_sot"]),
    "hS+aC": lambda X, t: (X["home_shots"] >= t["home_shots"]) & (X["away_sot"] >= t["away_sot"]),
    "aS+hC": lambda X, t: (X["away_shots"] >= t["away_shots"]) & (X["home_sot"] >= t["home_sot"]),
    "hR+aR": lambda X, t: (X["home_corners"] >= t["home_corners"]) & (X["away_corners"] >= t["away_corners"]),
    "fS+dS": lambda X, t: (X[t["fav"] + "_shots"] >= t[t["fav"] + "_shots"]) & (X[t["dog"] + "_shots"] >= t[t["dog"] + "_shots"]),
    # one-team templates with the opponent inside ("more shots than", "wins")
    "fS+fC+fM": lambda X, t: (X[t["fav"] + "_shots"] >= t[t["fav"] + "_shots"]) & (X[t["fav"] + "_sot"] >= t[t["fav"] + "_sot"])
                            & (X[t["fav"] + "_shots"] > X[t["dog"] + "_shots"]),
    "fS+fW": lambda X, t: (X[t["fav"] + "_shots"] >= t[t["fav"] + "_shots"]) & (X[t["fav"] + "_goals"] > X[t["dog"] + "_goals"]),
    "dS+dG": lambda X, t: (X[t["dog"] + "_shots"] >= t[t["dog"] + "_shots"]) & (X[t["dog"] + "_goals"] >= 1),
}
TWO_TEAM = ("hS+aS", "hC+aC", "hS+aC", "aS+hC", "hR+aR", "fS+dS")


def price(cdfs: dict, ys: dict, fav: np.ndarray, corr: np.ndarray, idx: np.ndarray) -> pd.DataFrame:
    """Each template's copula chance and outcome for the matches ``idx`` (thresholds at the 75% level)."""
    L = np.linalg.cholesky(corr)
    rows = []
    for i in idx:
        u = stats.norm.cdf(RNG.standard_normal((N_SIM, len(eb.DIMS))) @ L.T)
        X = {k: np.searchsorted(cdfs[k][i], u[:, d]) for d, k in enumerate(eb.DIMS)}
        Y = {k: np.array([ys[k][i]]) for k in eb.DIMS}
        t = {k: eb.threshold(cdfs[k][i], 0.75) for k in eb.DIMS if not k.endswith("_goals")}
        t["fav"], t["dog"] = ("home", "away") if fav[i] else ("away", "home")
        for name, f in TEMPLATES.items():
            rows.append({"i": int(i), "tpl": name, "p": float(f(X, t).mean()), "hit": bool(f(Y, t)[0])})
    return pd.DataFrame(rows)


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="add the per-third matrices to bb_korelacje.json if they win")
    args = ap.parse_args()
    t0 = time.time()
    m = walk_forward()
    print(f"matches: {len(m)} ({time.time() - t0:.0f}s)", flush=True)
    gap = np.abs(np.log(m["mu_goals_h"].to_numpy(float) / m["mu_goals_a"].to_numpy(float)))
    fav = (m["mu_goals_h"] >= m["mu_goals_a"]).to_numpy()
    early = (m["kickoff_h"] < SPLIT).to_numpy()
    cuts = np.quantile(gap[early], [1 / 3, 2 / 3])
    third = np.digitize(gap, cuts)              # 0 even, 1 middle, 2 mismatch
    cdfs, ys, Z = scores_of(m)

    # observed score correlations of the key pairs per third (whole season, for the record)
    pairs = [("home_shots", "away_shots"), ("home_sot", "away_sot"), ("home_corners", "away_corners"),
             ("home_goals", "away_goals"), ("home_shots", "home_sot"), ("home_shots", "away_corners")]
    report = {"matches": int(len(m)), "cuts": [round(float(c), 3) for c in cuts], "pairs": {}}
    for a, b in pairs:
        ia, ib = eb.DIMS.index(a), eb.DIMS.index(b)
        vals = [round(float(np.corrcoef(Z[third == k][:, ia], Z[third == k][:, ib])[0, 1]), 3) for k in range(3)]
        allv = round(float(np.corrcoef(Z[:, ia], Z[:, ib])[0, 1]), 3)
        report["pairs"][f"{a}~{b}"] = {"all": allv, "even": vals[0], "middle": vals[1], "mismatch": vals[2]}
        print(f"{a:>12s} ~ {b:<12s} all {allv:+.3f} | even {vals[0]:+.3f}  middle {vals[1]:+.3f}  mismatch {vals[2]:+.3f}", flush=True)

    # estimate on the first half of the season: pooled and per third
    pooled, _ = latent(sub(cdfs, early), Z[early])
    per = [latent(sub(cdfs, early & (third == k)), Z[early & (third == k)])[0] for k in range(3)]
    print(f"estimated ({time.time() - t0:.0f}s); pricing the second half", flush=True)

    late = np.where(~early)[0]
    a = price(cdfs, ys, fav, pooled, late)
    b = pd.concat([price(cdfs, ys, fav, per[k], late[third[late] == k]) for k in range(3)], ignore_index=True)
    res = a.merge(b, on=["i", "tpl"], suffixes=("_pooled", "_third"))
    res["third"] = third[res["i"].to_numpy()]
    out = {}
    for name, g in [("all", res), ("two-team", res[res["tpl"].isin(TWO_TEAM)])] + \
                   [(f"third {k}", res[res["third"] == k]) for k in range(3)] + \
                   [(t, res[res["tpl"] == t]) for t in TEMPLATES]:
        y = g["hit_pooled"].to_numpy(float)
        out[name] = {"n": int(len(g)), "hit": round(float(y.mean()), 4),
                     "p_pooled": round(float(g["p_pooled"].mean()), 4), "p_third": round(float(g["p_third"].mean()), 4),
                     "ll_pooled": round(logloss(g["p_pooled"].to_numpy(), y), 5),
                     "ll_third": round(logloss(g["p_third"].to_numpy(), y), 5)}
        o = out[name]
        print(f"{name:10s} n {o['n']:6d}  hit {o['hit']:.3f}  pooled {o['p_pooled']:.3f} ll {o['ll_pooled']:.5f}  "
              f"per-third {o['p_third']:.3f} ll {o['ll_third']:.5f}", flush=True)
    report["test"] = out
    wins = out["all"]["ll_third"] < out["all"]["ll_pooled"] and out["two-team"]["ll_third"] < out["two-team"]["ll_pooled"]
    report["adopt"] = bool(wins)
    print("ADOPT per-third matrices" if wins else "KEEP one matrix", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if wins and args.write:
        # final matrices from the whole season, per third, with the cuts that define the thirds
        full = [latent(sub(cdfs, third == k), Z[third == k])[0] for k in range(3)]
        cfg = json.loads(eb.CONFIG.read_text(encoding="utf-8"))
        cfg["wg_przewagi"] = {"_comment": "Per-third copula correlations by |log(mu_goals_home/mu_goals_away)| "
                                          "(scripts/mk2/eval_copula_gap.py, 2025/26 walk-forward).",
                              "progi": [round(float(c), 4) for c in cuts],
                              "korelacje": [[[round(float(x), 4) for x in row] for row in c] for c in full]}
        eb.CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        print(f"written to {eb.CONFIG.name}", flush=True)


if __name__ == "__main__":
    main()
