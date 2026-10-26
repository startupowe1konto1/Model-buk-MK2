"""Do extra factors improve the statistics model out of sample?  (user, 2026-10-26)

The model (stats_model.fit_stat) knows each team's long-run level for and against, its league, home
advantage, cups and the half-year level.  The user asked for recent form, a team's form at its own
venue, line-ups and absences, the referee, congestion.  This walk-forward test says, factor by factor,
whether any of them adds to what the model already says:

* test window: every match of the model's competitions from 2025-08 to the latest data, month by
  month; each month the model is fitted on everything before the month (history from 2022);
* for every team row the factors are computed point-in-time, from matches before that kick-off,
  as residuals against the same fit (r = log((y+0.5)/(mu+0.5))):
    form5    mean residual of the team's last 5 matches (any venue)
    venue5   mean residual of the team's last 5 matches at the same venue (home or away)
    oppc5    mean residual of what the opponent conceded in its last 5 matches
    rest     days since the team's previous match (capped at 14), congest = matches in the last 7 days
    lineup   share of the team's regular starters (started >= 50% of its last 8 line-ups) missing
             from today's starting eleven (the snapshot's line-ups; known about an hour before kick-off)
    ref      the referee's shrunk mean match residual over his previous matches (sum / (n + 20))
* the adjustment log(mu') = log(mu) + b . factors is fitted as a Poisson regression on the test rows of
  the months before each month (month-forward, so the coefficients never see the month they price),
  and scored against the plain model on the same over/under lines as eval_stats_model.py (team lines):
  log-loss and Brier, with a standard error clustered by match.  Also: each factor alone, and the
  coefficients with t-statistics from a fit on all test rows (in-sample, for interpretation).

Usage: python eval_factors.py [--out DIR] [--from 2025-08-01] [--stats shots,sot,corners,fouls,cards]
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_stats_model as ev  # noqa: E402
import stats_model as sm  # noqa: E402
from stats_config import MODEL_LEAGUES  # noqa: E402

FACTORS = ("form5", "venue5", "oppc5", "rest", "congest", "lineup", "ref")
FIRST_SEASON = 2022
REF_SHRINK = 20.0
LINEUP_WINDOW = 8
LINEUP_MIN = 5


def log(msg: str) -> None:
    print(msg, flush=True)


def mu_all(fit: sm.StatFit, d: pd.DataFrame) -> np.ndarray:
    """The fit's expected count for every row of d (its own period level for past rows)."""
    per = sm.period_of(d["kickoff"]).to_numpy()
    keys = [f"{int(p) // 2}H{int(p) % 2 + 1}" for p in per]
    pe = np.array([fit.period_effects.get(k, fit.b_season) for k in keys])
    lt = d["team_id"].map(lambda t: fit.league_for.get(fit.leagues.get(int(t), 0), 0.0)).to_numpy(float)
    lo = d["opp_id"].map(lambda t: fit.league_against.get(fit.leagues.get(int(t), 0), 0.0)).to_numpy(float)
    tf = d["team_id"].map(lambda t: fit.team_for.get(int(t), 0.0)).to_numpy(float)
    ta = d["opp_id"].map(lambda t: fit.team_against.get(int(t), 0.0)).to_numpy(float)
    home = d["home"].to_numpy(float)
    cup = d["cup"].to_numpy(float)
    eta = fit.b0 + fit.b_home * home + fit.b_cup * cup + fit.b_home_cup * home * cup + pe + lt + lo + tf + ta
    return np.exp(eta)


def load_lineups(fixture_ids: set[int]) -> dict[tuple[int, int], frozenset]:
    """Starting elevens of the snapshot's matches: (fixture, team) -> player ids."""
    snap = sm.DATA / "api-football" / "tables" / "history.sqlite"
    out: dict[tuple[int, int], set] = defaultdict(set)
    with sm._ro(snap) as con:
        q = ("select lp.fixture_id, lp.team_id, lp.player_id from lineup_players lp join matches m using(fixture_id) "
             "where m.season >= ? and lp.is_starter = 1")
        for fid, tid, pid in con.execute(q, (max(FIRST_SEASON, 2024),)):
            if fid in fixture_ids and pid is not None:
                out[(int(fid), int(tid))].add(int(pid))
    return {k: frozenset(v) for k, v in out.items()}


def lineup_feature(df: pd.DataFrame, xi: dict[tuple[int, int], frozenset]) -> pd.Series:
    """Share of the regular starters (>= half of the team's last LINEUP_WINDOW line-ups) not starting."""
    out = pd.Series(np.nan, index=df.index)
    d = df.sort_values("kickoff")
    for team, g in d.groupby("team_id", sort=False):
        prev: deque = deque(maxlen=LINEUP_WINDOW)
        for idx, fid in zip(g.index, g["fixture_id"]):
            today = xi.get((int(fid), int(team)))
            if today is None:
                continue
            if len(prev) >= LINEUP_MIN:
                counts: dict[int, int] = defaultdict(int)
                for s in prev:
                    for p in s:
                        counts[p] += 1
                regular = {p for p, c in counts.items() if c * 2 >= len(prev)}
                if len(regular) >= 6:
                    out.at[idx] = 1.0 - len(today & regular) / len(regular)
            prev.append(today)
    return out


def rolling_features(d: pd.DataFrame, r: np.ndarray) -> pd.DataFrame:
    """Point-in-time factors for every row of d from residuals r (rows before each kick-off only)."""
    x = d[["fixture_id", "team_id", "opp_id", "home", "kickoff", "referee"]].copy()
    x["r"] = r
    x = x.sort_values(["kickoff", "fixture_id"])
    g = x.groupby("team_id", sort=False)["r"]
    x["form5"] = g.transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    gv = x.groupby(["team_id", "home"], sort=False)["r"]
    x["venue5"] = gv.transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    # what the opponent conceded recently: the residuals of the rows played against it
    go = x.groupby("opp_id", sort=False)["r"]
    x["oppc5"] = go.transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    k = x.groupby("team_id", sort=False)["kickoff"]
    gap = (x["kickoff"] - k.shift(1)).dt.total_seconds() / 86400.0
    x["rest"] = gap.clip(upper=14.0)
    prev2 = k.shift(2)
    x["congest"] = ((x["kickoff"] - prev2).dt.total_seconds() / 86400.0 <= 7.0).astype(float)
    x.loc[prev2.isna(), "congest"] = 0.0
    # referee: one residual per match (mean of both rows), shrunk running mean over earlier matches
    m = x.groupby("fixture_id", sort=False).agg(kickoff=("kickoff", "first"), referee=("referee", "first"), r=("r", "mean"))
    m = m[m["referee"].notna() & (m["referee"] != "")].sort_values("kickoff")
    gr = m.groupby("referee", sort=False)["r"]
    csum = gr.transform(lambda s: s.shift(1).fillna(0).cumsum())
    cnt = gr.cumcount()
    m["ref"] = csum / (cnt + REF_SHRINK)
    x = x.join(m["ref"], on="fixture_id")
    return x.loc[d.index, ["form5", "venue5", "oppc5", "rest", "congest", "ref"]]


def poisson_adjust(y: np.ndarray, mu0: np.ndarray, X: np.ndarray, ridge: float = 1e-3) -> np.ndarray:
    """Coefficients b of log(mu) = log(mu0) + X b by Poisson likelihood (small ridge)."""
    off = np.log(mu0)

    def fun(b):
        eta = off + X @ b
        mu = np.exp(eta)
        f = np.sum(mu - y * eta) + 0.5 * ridge * b @ b
        g = X.T @ (mu - y) + ridge * b
        return f, g
    res = optimize.minimize(fun, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B")
    return res.x


def standardize(F: pd.DataFrame, ref: pd.DataFrame) -> np.ndarray:
    """Centre and scale by the reference rows; unknown values become 'average' (0)."""
    mean, std = ref.mean(), ref.std().replace(0, 1.0)
    Z = (F - mean) / std
    return Z.fillna(0.0).to_numpy(float)


def line_scores(rows: pd.DataFrame, mu_col: str, stat: str) -> pd.DataFrame:
    """Log-loss and Brier of P(over) on the team lines, one row per (team row, line)."""
    parts = []
    for line in ev.LINES[stat]["team"]:
        p = np.clip(ev.p_over(rows[mu_col].to_numpy(float), rows["alpha"].to_numpy(float), line), 1e-4, 1 - 1e-4)
        o = (rows["y"].to_numpy(float) > line).astype(float)
        parts.append(pd.DataFrame({"fixture_id": rows["fixture_id"].to_numpy(), "line": line,
                                   "ll": -(o * np.log(p) + (1 - o) * np.log(1 - p)), "brier": (p - o) ** 2}))
    return pd.concat(parts, ignore_index=True)


def t_stats(y: np.ndarray, mu0: np.ndarray, X: np.ndarray, names: tuple[str, ...]) -> dict[str, dict]:
    """In-sample Poisson coefficients with approximate standard errors (Fisher information)."""
    b = poisson_adjust(y, mu0, X)
    mu = mu0 * np.exp(X @ b)
    info = X.T @ (X * mu[:, None]) + 1e-3 * np.eye(X.shape[1])
    se = np.sqrt(np.diag(np.linalg.inv(info)))
    return {n: {"b": round(float(bi), 4), "se": round(float(s), 4), "t": round(float(bi / s), 2)} for n, bi, s in zip(names, b, se)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(sm.OUT / "stats" / "eval_factors"))
    ap.add_argument("--from", dest="test_from", default="2025-08-01")
    ap.add_argument("--stats", default="shots,sot,corners,fouls,cards")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stats_ = [s.strip() for s in args.stats.split(",") if s.strip()]
    t_all = time.time()

    df = sm.load_rows(list(MODEL_LEAGUES), first_season=FIRST_SEASON)
    df["kickoff"] = pd.to_datetime(df["kickoff_utc"], utc=True)
    test_from = pd.Timestamp(args.test_from, tz="UTC")
    test_to = (df["kickoff"].max().normalize() + pd.Timedelta(days=1))
    starts = list(pd.date_range(test_from, test_to, freq="MS", inclusive="left"))
    bounds = [(a, min(a + pd.offsets.MonthBegin(1), test_to)) for a in starts]
    log(f"rows {len(df)}, test {test_from.date()}..{test_to.date()}, {len(bounds)} months")
    xi = load_lineups(set(int(f) for f in df["fixture_id"].unique()))
    lineup = lineup_feature(df, xi)
    log(f"line-ups: {len(xi)} team line-ups, lineup factor known for {int(lineup.notna().sum())} rows")

    report: dict = {"test_from": str(test_from.date()), "test_to": str(test_to.date()), "stats": {}}
    for stat in stats_:
        t0 = time.time()
        col = sm.STATS[stat]
        rows_out = []
        for a, b in bounds:
            train_df = df[df["kickoff"] < a]
            leagues = sm.home_league(df[df["kickoff"] < b])
            fit = sm.fit_stat(train_df, stat, a, leagues=leagues)
            known = df[df[col].notna()]
            mu = mu_all(fit, known)
            r = np.log((known[col].to_numpy(float) + 0.5) / (mu + 0.5))
            feats = rolling_features(known, r)
            sel = known[(known["kickoff"] >= a) & (known["kickoff"] < b)]
            if sel.empty:
                continue
            part = feats.loc[sel.index].copy()
            part["lineup"] = lineup.loc[sel.index]
            part["y"] = sel[col].to_numpy(float)
            part["mu0"] = pd.Series(mu, index=known.index).loc[sel.index]
            cups = sel["cup"].astype(bool).to_numpy()
            part["alpha"] = np.where(cups, fit.dispersion("home", True), fit.dispersion("home", False))
            part["fixture_id"] = sel["fixture_id"].to_numpy()
            part["month"] = a.strftime("%Y-%m")
            part["league_id"] = sel["league_id"].to_numpy()
            rows_out.append(part)
        T = pd.concat(rows_out)
        months = sorted(T["month"].unique())
        # month-forward: coefficients from the earlier test months price each month
        T["mu_all"] = T["mu0"]
        single = {f: T["mu0"].copy() for f in FACTORS}
        coefs_by_month = {}
        for i, m in enumerate(months):
            if i < 2:
                continue
            tr = T[T["month"] < m]
            te = T[T["month"] == m]
            Xtr = standardize(tr[list(FACTORS)], tr[list(FACTORS)])
            Xte = standardize(te[list(FACTORS)], tr[list(FACTORS)])
            bm = poisson_adjust(tr["y"].to_numpy(), tr["mu0"].to_numpy(), Xtr)
            coefs_by_month[m] = dict(zip(FACTORS, np.round(bm, 4)))
            T.loc[te.index, "mu_all"] = te["mu0"].to_numpy() * np.exp(Xte @ bm)
            for j, f in enumerate(FACTORS):
                bj = poisson_adjust(tr["y"].to_numpy(), tr["mu0"].to_numpy(), Xtr[:, [j]])
                single[f].loc[te.index] = te["mu0"].to_numpy() * np.exp(Xte[:, [j]] @ bj)
        scored = T[T["month"] >= months[2]] if len(months) > 2 else T.iloc[0:0]
        base = line_scores(scored, "mu0", stat)
        res = {"n_rows": int(len(scored)), "n_matches": int(scored["fixture_id"].nunique()),
               "months_scored": months[2:], "logloss_model": round(float(base["ll"].mean()), 5),
               "brier_model": round(float(base["brier"].mean()), 5)}
        adj = line_scores(scored, "mu_all", stat)
        d, se = ev.mean_se((base["ll"] - adj["ll"]).to_numpy(), base["fixture_id"].to_numpy())
        res["all_factors"] = {"logloss": round(float(adj["ll"].mean()), 5), "gain": round(float(d), 5), "se": round(float(se), 5),
                              "brier": round(float(adj["brier"].mean()), 5)}
        res["single"] = {}
        for f in FACTORS:
            scored_f = scored.assign(mu_f=single[f].loc[scored.index])
            adj_f = line_scores(scored_f, "mu_f", stat)
            d, se = ev.mean_se((base["ll"] - adj_f["ll"]).to_numpy(), base["fixture_id"].to_numpy())
            res["single"][f] = {"gain": round(float(d), 5), "se": round(float(se), 5), "known": int(T[f].notna().sum())}
        X = standardize(T[list(FACTORS)], T[list(FACTORS)])
        res["coefficients_in_sample"] = t_stats(T["y"].to_numpy(), T["mu0"].to_numpy(), X, FACTORS)
        res["coefficients_by_month"] = coefs_by_month
        res["factor_sd"] = {f: round(float(T[f].std()), 4) for f in FACTORS}
        report["stats"][stat] = res
        T.to_csv(out / f"rows_{stat}.csv.gz", index=False, compression="gzip")
        log(f"{stat}: {len(T)} rows, {time.time() - t0:.0f}s; model ll {res['logloss_model']} -> all factors "
            f"{res['all_factors']['logloss']} (gain {res['all_factors']['gain']:+.5f} ± {res['all_factors']['se']:.5f})")
        for f in FACTORS:
            s = res["single"][f]
            c = res["coefficients_in_sample"][f]
            log(f"    {f:8s} gain {s['gain']:+.5f} ± {s['se']:.5f}   coef {c['b']:+.4f} (t {c['t']:+.1f})   known {s['known']}")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"done in {time.time() - t_all:.0f}s -> {out}")


if __name__ == "__main__":
    main()
