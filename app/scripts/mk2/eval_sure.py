"""Backtest of near-certain events ("pewniaczki"): how often do events the model rates at 85-95% come in?

Walk-forward on the 2025/26 season exactly as eval_stats_model.py (monthly refits of
stats_model.fit_stat, history from 2022, the cup dispersion in cup matches).  For every team-match
row and statistic (shots, shots on target, corners), for the team's own count and for the match
total:

* model thresholds: the highest k with P(X >= k) >= level and whether X >= k came in;
* the record before the match: in how many of the team's last 10 matches with that statistic (all
  competitions) it reached k (for match totals: both teams' last 10 matches);
* naive streaks without the model: k = the lowest value of the last 10 matches (10 of 10) or the
  second lowest (9 of 10), and whether the next match reached it;
* reliability in the tail: hit rate by bins of the model's probability;
* daily accumulators: the five likeliest events of a day from five different matches.

Usage (from app/):  .venv\\Scripts\\python scripts\\mk2\\eval_sure.py
Output: <mk2>/stats/eval_sure/{summary.csv, reliability.csv, accumulators.csv, rows.csv.gz}
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_stats_model as ev  # noqa: E402
import stats_model as sm  # noqa: E402

PICK_STATS = ("shots", "sot", "corners")
LEVELS = (0.85, 0.90, 0.95)
K_MAX = 70
LAGS = 10


def sf_matrix(mu: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """P(X >= k) for k = 0..K_MAX (columns) under NB2(mu, alpha) (rows)."""
    r = 1.0 / alpha[:, None]
    k = np.arange(K_MAX + 1)[None, :]
    return stats.nbinom.sf(k - 1, r, r / (r + mu[:, None]))


def highest_k(sf: np.ndarray, level: float) -> tuple[np.ndarray, np.ndarray]:
    """The highest k >= 1 with P(X >= k) >= level (0 when none) and its probability."""
    ok = sf >= level
    ok[:, 0] = False
    k = np.where(ok.any(axis=1), K_MAX - np.argmax(ok[:, ::-1], axis=1), 0)
    p = np.where(k > 0, sf[np.arange(len(k)), k], np.nan)
    return k, p


def lag_frame(values: pd.DataFrame, key: str) -> pd.DataFrame:
    """For each (fixture_id, team_id): the team's previous LAGS values of ``key`` (oldest last)."""
    v = values.sort_values("kickoff")
    g = v.groupby("team_id")[key]
    lags = pd.concat({f"lag{i}": g.shift(i) for i in range(1, LAGS + 1)}, axis=1)
    return pd.concat([v[["fixture_id", "team_id"]], lags], axis=1).set_index(["fixture_id", "team_id"])


def record(lags: pd.DataFrame, k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How many of the previous LAGS values reached k, and how many values there were."""
    arr = lags.to_numpy(dtype=float)
    n = np.isfinite(arr).sum(axis=1)
    hits = (arr >= k[:, None]).sum(axis=1)
    return hits, n


def main() -> None:
    out = sm.OUT / "stats" / "eval_sure"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    leagues = ev.model_leagues("config")
    df = sm.load_rows(list(leagues), first_season=ev.FIRST_SEASON)
    test = df[(df["season"] == ev.TEST_SEASON) & (df["kickoff"] >= ev.TEST_FROM) & (df["kickoff"] < ev.TEST_TO)].copy()
    starts = list(pd.date_range(ev.TEST_FROM, ev.TEST_TO, freq="MS", inclusive="left"))
    bounds = [(a, a + pd.offsets.MonthBegin(1)) for a in starts]
    leagues_m = {a: sm.home_league(df[df["kickoff"] < b]) for a, b in bounds}
    print(f"rows {len(df)}, test rows {len(test)}, months {len(bounds)}", flush=True)

    rows = []
    for stat in PICK_STATS:
        col = sm.STATS[stat]
        pred = ev.predict(df, test, stat, ev.DEFAULT["half_life_days"], ev.DEFAULT["ridge_team"], bounds,
                          leagues_m, [], "as_is")
        t = test.join(pred)
        t = t[t[col].notna() & t["mu"].notna()].copy()
        # match totals: both team rows of a fixture present
        pair = t.pivot_table(index="fixture_id", columns="side", values=[col, "mu", "alpha_total"], aggfunc="first")
        pair = pair.dropna()
        # team history (all competitions with the statistic) and match-total history per team
        have = df[df[col].notna()].copy()
        tot = have.groupby("fixture_id")[col].transform("sum")
        have["total_val"] = np.where(have.groupby("fixture_id")[col].transform("count") == 2, tot, np.nan)
        lag_team = lag_frame(have, col)
        lag_total = lag_frame(have[have["total_val"].notna()], "total_val")

        # team scope
        sf = sf_matrix(t["mu"].to_numpy(float), t["alpha"].to_numpy(float))
        lags_t = lag_team.reindex(pd.MultiIndex.from_arrays([t["fixture_id"], t["team_id"]])).to_numpy(float)
        y = t[col].to_numpy(float)
        base = {"stat": stat, "scope": "team", "fixture_id": t["fixture_id"].to_numpy(),
                "kickoff": t["kickoff"].to_numpy(), "cup": t["cup"].to_numpy(), "team_id": t["team_id"].to_numpy()}
        for level in LEVELS:
            k, p = highest_k(sf, level)
            arr = lags_t
            hits = (arr >= k[:, None]).sum(axis=1)
            n = np.isfinite(arr).sum(axis=1)
            rows.append(pd.DataFrame({**base, "method": f"model_{level:.2f}", "k": k, "p": p, "y": y,
                                      "hit": y >= k, "rec_hits": hits, "rec_n": n}))
        # naive streaks (team scope)
        srt = np.sort(np.where(np.isfinite(lags_t), lags_t, np.inf), axis=1)
        full = np.isfinite(lags_t).sum(axis=1) == LAGS
        for name, kk in (("streak_10of10", srt[:, 0]), ("streak_9of10", srt[:, 1])):
            kk = np.where(full, kk, np.nan)
            rows.append(pd.DataFrame({**base, "method": name, "k": kk, "p": np.nan, "y": y, "hit": y >= kk,
                                      "rec_hits": np.nan, "rec_n": np.where(full, LAGS, 0)}))

        # match totals
        mu_t = (pair[("mu", "home")] + pair[("mu", "away")]).to_numpy(float)
        al_t = pair[("alpha_total", "home")].to_numpy(float)
        y_t = (pair[(col, "home")] + pair[(col, "away")]).to_numpy(float)
        sf_t = sf_matrix(mu_t, al_t)
        fx = test.drop_duplicates("fixture_id").set_index("fixture_id").loc[pair.index]
        lh = lag_total.reindex(pd.MultiIndex.from_arrays([pair.index, fx["home_team_id"]])).to_numpy(float)
        la = lag_total.reindex(pd.MultiIndex.from_arrays([pair.index, fx["away_team_id"]])).to_numpy(float)
        base_t = {"stat": stat, "scope": "total", "fixture_id": pair.index.to_numpy(), "kickoff": fx["kickoff"].to_numpy(),
                  "cup": fx["cup"].to_numpy(), "team_id": fx["home_team_id"].to_numpy()}
        for level in LEVELS:
            k, p = highest_k(sf_t, level)
            hits = np.minimum((lh >= k[:, None]).sum(axis=1), (la >= k[:, None]).sum(axis=1))
            n = np.minimum(np.isfinite(lh).sum(axis=1), np.isfinite(la).sum(axis=1))
            rows.append(pd.DataFrame({**base_t, "method": f"model_{level:.2f}", "k": k, "p": p, "y": y_t,
                                      "hit": y_t >= k, "rec_hits": hits, "rec_n": n}))
        print(f"{stat}: {len(t)} team rows, {len(pair)} matches, {time.time() - t0:.0f}s", flush=True)

    R = pd.concat(rows, ignore_index=True)
    R = R[(R["k"] > 0) & R["k"].notna()].copy()
    R["hit"] = R["hit"].astype(bool)
    R.to_csv(out / "rows.csv.gz", index=False)

    # summary: model alone, model + record, naive streaks
    summ = []
    for (stat, scope, method), g in R.groupby(["stat", "scope", "method"]):
        variants = {"wszystkie": g}
        if method.startswith("model"):
            full = g[g["rec_n"] == LAGS]
            variants["rekord_10_z_10"] = full[full["rec_hits"] == LAGS]
            variants["rekord_9_z_10"] = full[full["rec_hits"] >= 9]
            variants["rekord_8_z_10"] = full[full["rec_hits"] >= 8]
            variants["rekord_ponizej_8"] = full[full["rec_hits"] < 8]
        for vname, h in variants.items():
            if len(h) == 0:
                continue
            for comp, hh in (("all", h), ("ligi", h[h["cup"] == 0]), ("puchary", h[h["cup"] == 1])):
                if len(hh) < 30:
                    continue
                summ.append({"stat": stat, "scope": scope, "method": method, "filtr": vname, "rozgrywki": comp,
                             "n": len(hh), "p_model": round(float(hh["p"].mean()), 4) if hh["p"].notna().any() else None,
                             "trafione": round(float(hh["hit"].mean()), 4), "k_srednio": round(float(hh["k"].mean()), 1)})
    S = pd.DataFrame(summ)
    S.to_csv(out / "summary.csv", index=False)

    # reliability in the tail: every k with P >= 0.80 for team rows (not only the highest one)
    rel = R[R["method"].str.startswith("model")].copy()
    bins = [0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 1.0]
    rel["bin"] = pd.cut(rel["p"], bins, right=False)
    REL = (rel.groupby(["stat", "scope", "bin"], observed=True)
              .agg(n=("hit", "size"), p_model=("p", "mean"), trafione=("hit", "mean")).reset_index())
    REL.to_csv(out / "reliability.csv", index=False)

    # daily accumulators: five likeliest events of a day from five different matches
    acc = []
    for level in (0.90,):
        cand = R[(R["method"] == f"model_{level:.2f}") & (R["rec_n"] == LAGS) & (R["rec_hits"] >= 9)].copy()
        cand["day"] = pd.to_datetime(cand["kickoff"], utc=True).dt.strftime("%Y-%m-%d")
        for day, g in cand.groupby("day"):
            g = g.sort_values("p", ascending=False).drop_duplicates("fixture_id")
            if len(g) < 5:
                continue
            legs = g.head(5)
            acc.append({"day": day, "level": level, "p_model": float(np.prod(legs["p"])), "hit": bool(legs["hit"].all()),
                        "legs_hit": int(legs["hit"].sum())})
    A = pd.DataFrame(acc)
    A.to_csv(out / "accumulators.csv", index=False)

    pd.set_option("display.width", 200)
    show = S[(S["rozgrywki"] == "all")].sort_values(["stat", "scope", "method", "filtr"])
    print(show.to_string(index=False))
    print(REL.to_string(index=False))
    if len(A):
        print(f"accumulators (5 legs, p>=0.90 and record >= 9/10): days {len(A)}, mean p {A['p_model'].mean():.3f}, "
              f"hit {A['hit'].mean():.3f}, mean legs hit {A['legs_hit'].mean():.2f}")
    print(f"done in {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
