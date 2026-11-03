"""Does a recent-level correction make the forecasts better?  (2026/27 started with clearly more shots: the model's
means, already aligned with the bookmakers', trailed the actual counts by a few per cent.)

Walk-forward on the forecasts logged before kick-off (predictions.sqlite, from 14.10): for each settled match the
correction uses only matches settled before that forecast was made - the actual counts against the logged means
(mu x k) over the last WINDOW_DAYS, as a log ratio shrunk towards 0 with N0 team-matches; the league variant shrinks
each league's ratio towards the overall one with N0_LEAGUE.  Scored by the negative-binomial log-likelihood of every
team count and match total, with a 90% interval from resampling match days.  Rule fixed before looking: a statistic
gets the correction only when the overall variant's gain has its interval above 0; the league variant replaces it
only when it beats the overall one the same way.

Usage (from app/):  .venv\\Scripts\\python scripts\\mk2\\eval_level.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stats_daily as sd  # noqa: E402
import stats_model as sm  # noqa: E402

WINDOW_DAYS = 28
N0 = 200            # team-matches: the overall correction's shrinkage
N0_LEAGUE = 80      # team-matches: a league's shrinkage towards the overall correction
SETTLE_H = 3        # a match counts once this long after kick-off
STATS = ("shots", "sot", "corners", "fouls", "cards")
RNG = np.random.default_rng(20261103)


def logged(pr: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """Per settled match and statistic: the last forecast made before kick-off and what happened."""
    kick = df.groupby("fixture_id")["kickoff"].first()
    out = []
    for s in STATS:
        col = sm.STATS[s]
        y = df.pivot_table(index="fixture_id", columns="side", values=col, aggfunc="first")
        lvl = ", poziom" if "poziom" in [r[1] for r in pr.execute("pragma table_info(predictions)")] else ", 1.0 as poziom"
        q = pd.read_sql_query("select made_at, fixture_id, mu_home, mu_away, alpha, alpha_total, k_home, k_away, k_total"
                              + lvl + " from predictions where stat = ?", pr, params=[s])
        q["poziom"] = q["poziom"].fillna(1.0)
        q["made"] = pd.to_datetime(q["made_at"], utc=True)
        q = q[q["fixture_id"].isin(kick.index)]
        q = q[q["made"] < q["fixture_id"].map(kick)]
        q = q.sort_values("made").drop_duplicates("fixture_id", keep="last")
        q = q.join(y, on="fixture_id").dropna(subset=["home", "away"])
        q["stat"] = s
        out.append(q)
    t = pd.concat(out, ignore_index=True)
    t["kickoff"] = t["fixture_id"].map(kick)
    t["league_id"] = t["fixture_id"].map(df.groupby("fixture_id")["league_id"].first())
    # the means as forecast before any level correction (the one stored in 'poziom' taken out)
    t["mh"], t["ma"] = t["mu_home"] * t["k_home"] / t["poziom"], t["mu_away"] * t["k_away"] / t["poziom"]
    t["mt"] = (t["mu_home"] + t["mu_away"]) * t["k_total"] / t["poziom"]
    return t


def level_at(hist: pd.DataFrame, league: int) -> tuple[float, float]:
    """(overall, league) multipliers from earlier matches: shrunk log ratio of actual to forecast counts."""
    if hist.empty:
        return 1.0, 1.0
    y, m = hist["home"].sum() + hist["away"].sum(), hist["mh"].sum() + hist["ma"].sum()
    n = 2 * len(hist)
    b = n / (n + N0) * np.log(y / m)
    h = hist[hist["league_id"] == league]
    if h.empty:
        return float(np.exp(b)), float(np.exp(b))
    yl, ml, nl = h["home"].sum() + h["away"].sum(), h["mh"].sum() + h["ma"].sum(), 2 * len(h)
    bl = b + nl / (nl + N0_LEAGUE) * (np.log(yl / ml) - b)
    return float(np.exp(b)), float(np.exp(bl))


def nb_ll(y: np.ndarray, mu: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    r = 1.0 / np.maximum(alpha, 1e-6)
    return stats.nbinom.logpmf(y.astype(int), r, r / (r + mu))


def main() -> None:
    pr = sqlite3.connect("file:" + (sm.OUT / "stats" / "predictions.sqlite").as_posix() + "?mode=ro", uri=True)
    df = sm.load_rows(list(sd.MODEL_LEAGUES), first_season=2025)
    df = df[df["kickoff"] >= pd.Timestamp("2026-10-01", tz="UTC")]
    t = logged(pr, df)
    print(f"settled forecasts: {t['fixture_id'].nunique()} matches, {t['made'].min():%d.%m}-{t['kickoff'].max():%d.%m}")
    for s in STATS:
        g = t[t["stat"] == s].sort_values("made").reset_index(drop=True)
        lv = np.ones((len(g), 2))
        for i, r in g.iterrows():
            hist = g[(g["kickoff"] < r["made"] - pd.Timedelta(hours=SETTLE_H)) & (g["kickoff"] >= r["made"] - pd.Timedelta(days=WINDOW_DAYS))]
            lv[i] = level_at(hist, r["league_id"])
        day = g["kickoff"].dt.date.to_numpy()
        res = {}
        for name, L in (("none", np.ones(len(g))), ("overall", lv[:, 0]), ("league", lv[:, 1])):
            ll = (nb_ll(g["home"].to_numpy(), g["mh"].to_numpy() * L, g["alpha"].to_numpy())
                  + nb_ll(g["away"].to_numpy(), g["ma"].to_numpy() * L, g["alpha"].to_numpy()))
            llt = nb_ll((g["home"] + g["away"]).to_numpy(), g["mt"].to_numpy() * L, g["alpha_total"].to_numpy())
            res[name] = (ll, llt)
        y = g["home"].sum() + g["away"].sum()
        print(f"\n{s}: {len(g)} matches, actual/forecast {y / (g['mh'].sum() + g['ma'].sum()):.3f}, "
              f"overall multiplier now {lv[-1, 0]:.3f} (range {lv[:, 0].min():.3f}-{lv[:, 0].max():.3f})")
        days = np.unique(day)
        for a, b in (("overall", "none"), ("league", "overall")):
            for k, what in ((0, "teams"), (1, "totals")):
                d = res[a][k] - res[b][k]
                per_day = pd.Series(d).groupby(day).sum()
                boot = [per_day.loc[RNG.choice(days, len(days))].sum() / len(d) for _ in range(2000)]
                lo, hi = np.percentile(boot, [5, 95])
                print(f"  {a:7s} vs {b:7s} {what:6s}: gain per match {d.mean():+.4f}  90% [{lo:+.4f}, {hi:+.4f}]"
                      f"{'  <- better' if lo > 0 else ''}")


if __name__ == "__main__":
    main()
