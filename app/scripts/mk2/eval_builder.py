"""Joint model for same-match bet builders: correlations between a match's counts, and a backtest.

A bet builder combines events of one match (e.g. home team at least 12 shots, at least 4 on target,
more shots than the opponent, scores, wins).  They are far from independent, so their joint chance
comes from a Gaussian copula over the eight counts of a match
    [home shots, home shots on target, home corners, home goals, away shots, away sot, away corners, away goals]
with the model's negative-binomial margins (stats_model.fit_stat; goals are the 90-minute score).

The correlations are estimated on the 2025/26 walk-forward forecasts (monthly refits, as in
eval_stats_model.py): every observed count is turned into a randomised PIT normal score under its
own forecast, and because rounding to whole numbers weakens correlations (most for goals), each
pair's latent correlation is the one whose simulated counts reproduce the observed score
correlation.  The backtest then prices fixed bet-builder templates for every 2025/26 match with
the copula and with independent legs and compares both with what happened.

Usage (from app/):  .venv\\Scripts\\python scripts\\mk2\\eval_builder.py
Output: app/config/mk2/bb_korelacje.json (used by stats_sure.py) and <mk2>/stats/eval_builder/
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_stats_model as ev  # noqa: E402
import stats_model as sm  # noqa: E402
from stats_sure import GOALS_FIT  # noqa: E402

COUNTS = ("shots", "sot", "corners", "goals")
DIMS = [f"{side}_{s}" for side in ("home", "away") for s in COUNTS]
K_MAX = {"shots": 70, "sot": 35, "corners": 35, "goals": 15}
CONFIG = HERE.parents[1] / "config" / "mk2" / "bb_korelacje.json"
RNG = np.random.default_rng(20261015)


def cdf_table(mu: np.ndarray, alpha: np.ndarray, kmax: int) -> np.ndarray:
    """CDF F(k) for k = 0..kmax of NB2(mu, alpha), one row per forecast (last column forced to 1)."""
    r = 1.0 / np.maximum(alpha, 1e-6)[:, None]
    k = np.arange(kmax + 1)[None, :]
    c = stats.nbinom.cdf(k, r, r / (r + mu[:, None]))
    c[:, -1] = 1.0
    return c


def pit_scores(y: np.ndarray, cdf: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Randomised PIT normal scores of observed counts y under their CDF rows."""
    yi = np.clip(y.astype(int), 0, cdf.shape[1] - 1)
    hi = cdf[np.arange(len(y)), yi]
    lo = np.where(yi > 0, cdf[np.arange(len(y)), np.maximum(yi - 1, 0)], 0.0)
    u = lo + rng.random(len(y)) * (hi - lo)
    return stats.norm.ppf(np.clip(u, 1e-9, 1 - 1e-9))


def draw(cdf: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Counts with CDF rows ``cdf`` (n x K) at uniforms ``u`` (n x m): the smallest k with F(k) >= u."""
    out = np.empty(u.shape, dtype=np.int16)
    for i in range(cdf.shape[0]):
        out[i] = np.searchsorted(cdf[i], u[i])
    return out


def latent_rho(cdf_a: np.ndarray, cdf_b: np.ndarray, target: float, reps: int = 8) -> float:
    """The copula correlation whose simulated counts give the observed PIT-score correlation."""
    n = cdf_a.shape[0]
    z1 = RNG.standard_normal((n, reps))
    z2 = RNG.standard_normal((n, reps))
    v1, v2 = RNG.random((n, reps)), RNG.random((n, reps))

    def score_corr(rho: float) -> float:
        za, zb = z1, rho * z1 + np.sqrt(1 - rho * rho) * z2
        xa, xb = draw(cdf_a, stats.norm.cdf(za)), draw(cdf_b, stats.norm.cdf(zb))
        sa = _scores_sim(xa, cdf_a, v1)
        sb = _scores_sim(xb, cdf_b, v2)
        return float(np.corrcoef(sa.ravel(), sb.ravel())[0, 1])

    lo, hi = -0.95, 0.95
    f_lo, f_hi = score_corr(lo) - target, score_corr(hi) - target
    if f_lo > 0:
        return lo
    if f_hi < 0:
        return hi
    return float(optimize.brentq(lambda r: score_corr(r) - target, lo, hi, xtol=1e-3))


def _scores_sim(x: np.ndarray, cdf: np.ndarray, v: np.ndarray) -> np.ndarray:
    rows = np.arange(x.shape[0])[:, None]
    hi = cdf[rows, x]
    lo = np.where(x > 0, cdf[rows, np.maximum(x - 1, 0)], 0.0)
    return stats.norm.ppf(np.clip(lo + v * (hi - lo), 1e-9, 1 - 1e-9))


def nearest_pd(c: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    w, v = np.linalg.eigh((c + c.T) / 2)
    c2 = v @ np.diag(np.maximum(w, eps)) @ v.T
    d = np.sqrt(np.diag(c2))
    return c2 / np.outer(d, d)


def threshold(cdf_row: np.ndarray, level: float) -> int:
    """The highest k >= 1 with P(X >= k) >= level (0 if none)."""
    sf = 1.0 - np.concatenate([[0.0], cdf_row[:-1]])   # P(X >= k) for k = 0..K
    ok = np.where(sf >= level)[0]
    ok = ok[ok >= 1]
    return int(ok.max()) if len(ok) else 0


def main() -> None:
    out = sm.OUT / "stats" / "eval_builder"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    leagues = ev.model_leagues("config")
    df = sm.load_rows(list(leagues), first_season=ev.FIRST_SEASON)
    test = df[(df["season"] == ev.TEST_SEASON) & (df["kickoff"] >= ev.TEST_FROM) & (df["kickoff"] < ev.TEST_TO)].copy()
    starts = list(pd.date_range(ev.TEST_FROM, ev.TEST_TO, freq="MS", inclusive="left"))
    bounds = [(a, a + pd.offsets.MonthBegin(1)) for a in starts]
    leagues_m = {a: sm.home_league(df[df["kickoff"] < b]) for a, b in bounds}

    preds = {}
    for s in COUNTS:
        hl, rt = ((GOALS_FIT["half_life_days"], GOALS_FIT["ridge_team"]) if s == "goals"
                  else (ev.DEFAULT["half_life_days"], ev.DEFAULT["ridge_team"]))
        p = ev.predict(df, test, s, hl, rt, bounds, leagues_m, [], "as_is")
        preds[s] = p
        print(f"{s}: forecasts {p['mu'].notna().sum()}, {time.time() - t0:.0f}s", flush=True)

    # one row per match with all eight counts and forecasts
    cols = {s: (sm.STATS.get(s) or sm.EXTRA_STATS[s]) for s in COUNTS}
    t = test[["fixture_id", "side", "cup", "kickoff", "league_id"] + list(cols.values())].copy()
    for s in COUNTS:
        t[f"mu_{s}"], t[f"al_{s}"] = preds[s]["mu"], preds[s]["alpha"]
    h = t[t["side"] == "home"].set_index("fixture_id")
    a = t[t["side"] == "away"].set_index("fixture_id")
    m = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    need = [f"{cols[s]}_{x}" for s in COUNTS for x in ("h", "a")] + [f"mu_{s}_{x}" for s in COUNTS for x in ("h", "a")]
    m = m.dropna(subset=need)
    print(f"matches with all eight counts: {len(m)}", flush=True)

    cdfs, ys, scores = {}, {}, {}
    for side, x in (("home", "h"), ("away", "a")):
        for s in COUNTS:
            key = f"{side}_{s}"
            cdfs[key] = cdf_table(m[f"mu_{s}_{x}"].to_numpy(float), m[f"al_{s}_{x}"].to_numpy(float), K_MAX[s])
            ys[key] = m[f"{cols[s]}_{x}"].to_numpy(float)
            scores[key] = pit_scores(ys[key], cdfs[key], RNG)
    Z = np.column_stack([scores[k] for k in DIMS])
    obs = np.corrcoef(Z, rowvar=False)
    lat = np.eye(len(DIMS))
    for i in range(len(DIMS)):
        for j in range(i + 1, len(DIMS)):
            lat[i, j] = lat[j, i] = latent_rho(cdfs[DIMS[i]], cdfs[DIMS[j]], obs[i, j])
    lat = nearest_pd(lat)
    print("observed score correlations:\n" + pd.DataFrame(obs, DIMS, DIMS).round(3).to_string())
    print("latent copula correlations:\n" + pd.DataFrame(lat, DIMS, DIMS).round(3).to_string(), flush=True)
    CONFIG.write_text(json.dumps({
        "_comment": "Gaussian-copula correlations of a match's counts for bet builders (scripts/mk2/eval_builder.py, "
                    "2025/26 walk-forward, PIT scores corrected for rounding to whole numbers).",
        "wymiary": DIMS, "korelacje": [[round(float(x), 4) for x in row] for row in lat],
        "korelacje_obserwowane": [[round(float(x), 4) for x in row] for row in obs],
        "mecze": int(len(m)), "utworzono": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1), encoding="utf-8")

    # backtest of bet-builder templates: copula vs independent legs vs reality
    L = np.linalg.cholesky(lat)
    n_sim = 4000
    idx = np.arange(len(m))
    rows = []
    for i in idx:
        u = stats.norm.cdf(RNG.standard_normal((n_sim, len(DIMS))) @ L.T)
        X = {k: np.searchsorted(cdfs[k][i], u[:, d]) for d, k in enumerate(DIMS)}
        Y = {k: ys[k][i] for k in DIMS}
        for team, opp in (("home", "away"), ("away", "home")):
            a80 = threshold(cdfs[f"{team}_shots"][i], 0.80)
            b80 = threshold(cdfs[f"{team}_sot"][i], 0.80)
            legs_sim = {
                "S": X[f"{team}_shots"] >= a80, "C": X[f"{team}_sot"] >= b80,
                "M": X[f"{team}_shots"] > X[f"{opp}_shots"], "G": X[f"{team}_goals"] >= 1,
                "W": X[f"{team}_goals"] > X[f"{opp}_goals"],
            }
            legs_obs = {
                "S": Y[f"{team}_shots"] >= a80, "C": Y[f"{team}_sot"] >= b80,
                "M": Y[f"{team}_shots"] > Y[f"{opp}_shots"], "G": Y[f"{team}_goals"] >= 1,
                "W": Y[f"{team}_goals"] > Y[f"{opp}_goals"],
            }
            for tpl in ("SCM", "SCG", "SMW", "SCMG", "MW", "SG", "SC", "M", "G", "W"):
                joint = np.logical_and.reduce([legs_sim[c] for c in tpl])
                indep = float(np.prod([legs_sim[c].mean() for c in tpl]))
                rows.append({"fixture_id": m.index[i], "cup": m["cup_h"].iloc[i], "team": team, "tpl": tpl,
                             "p_copula": float(joint.mean()), "p_indep": indep,
                             "hit": bool(all(legs_obs[c] for c in tpl))})
    B = pd.DataFrame(rows)
    B.to_csv(out / "templates.csv.gz", index=False)
    summ = B.groupby(["tpl"]).agg(n=("hit", "size"), p_copula=("p_copula", "mean"), p_indep=("p_indep", "mean"),
                                  trafione=("hit", "mean")).reset_index()
    summ["ll_copula"] = B.groupby("tpl").apply(lambda g: -np.mean(np.where(g["hit"], np.log(np.clip(g["p_copula"], 1e-4, 1)),
                                                                          np.log(np.clip(1 - g["p_copula"], 1e-4, 1)))),
                                               include_groups=False).to_numpy()
    summ["ll_indep"] = B.groupby("tpl").apply(lambda g: -np.mean(np.where(g["hit"], np.log(np.clip(g["p_indep"], 1e-4, 1)),
                                                                         np.log(np.clip(1 - g["p_indep"], 1e-4, 1)))),
                                              include_groups=False).to_numpy()
    summ.to_csv(out / "summary.csv", index=False)
    B["bin"] = pd.cut(B["p_copula"], [0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    rel = B.groupby(["bin"], observed=True).agg(n=("hit", "size"), p_copula=("p_copula", "mean"),
                                                trafione=("hit", "mean")).reset_index()
    rel.to_csv(out / "reliability.csv", index=False)
    pd.set_option("display.width", 200)
    print(summ.round(4).to_string(index=False))
    print(rel.round(4).to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s -> {out}, {CONFIG}")


if __name__ == "__main__":
    main()
