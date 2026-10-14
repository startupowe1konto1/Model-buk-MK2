"""Walk-forward backtest of the team-statistics model (scripts/mk2/stats_model.py).

The 2025/26 season (API season 2025, matches from 2025-08-01 to 2026-05-31; Norway and Sweden play
the calendar year, so for them it is 2025-08-01..2025-12-31) is predicted month by month: for each
month the model is fitted on everything before the first day of the month (history from season
2022) and then prices every league and UEFA-cup match kicking off in that month.  Each team's
domestic league (``home_league``) is taken from the matches played up to the end of the month,
i.e. only who plays in which league (known from the fixture list), never results, so a promoted
team is not treated as a cup-only team in its first month.

Every statistic is scored as binary over/under forecasts at fixed lines (team lines on each team's
own count with the team dispersion ``alpha``, total lines on the match total with ``alpha_total``):
log-loss and Brier score of the model against

* a league-frequency baseline: the share of matches over the line in the same league (UEFA cups
  pooled) during the 365 days before the fit date, separately for home teams, away teams and match
  totals; all leagues together when the league has fewer than 30 such matches; clipped to
  [0.02, 0.98];
* Oskar's NB2 forecasts (``<data>/predictions/count_nb2.sqlite``, domestic leagues only) on the
  fixtures both have; his ``yellow`` family is compared with a yellow-cards-only fit of this model.

Variants of the model (column ``variant``):

* ``as_is``: ``stats_model.fit_stat`` exactly as written, default hyper-parameters;
* ``season_fix``: the same, but trained without matches of seasons later than the predicted one.
  ``fit_stat`` forecasts with the level of the *latest* season in the training data; from mid-March
  2026 that is Norway's/Sweden's calendar season 2026 (a few rounds), which then sets the level of
  every league and cup.  Only the April and May 2026 fits differ from ``as_is``;
* ``tuned`` (when the grid runs): ``season_fix`` with the grid's best half-life and team ridge
  (best mean log-loss over shots, corners and cards).  It is chosen on this same test season, so
  its scores are optimistic.

Also written: mean bias (mean forecast mean vs actual mean) by competition and by month,
reliability (10 bins of P(over)) with a calibration summary, and a small hyper-parameter grid
(half-life 180/365/730 x team ridge 10/30/100, with ``--wide-grid`` also 300/1000) on shots,
corners and cards.  Differences in log-loss come with a standard error clustered by match.
Subsets: all, domestic, big5 (EPL, LaLiga, Serie A, Bundesliga, Ligue 1), cups (UCL+UEL+UECL),
ucl, ucl_main (UCL from September: league phase and knockouts), uel, uecl.  With ``--weekly`` the
NB2 comparison is repeated with weekly instead of monthly refits (Oskar's forecasts are refitted
every matchday).

League ids: ``LEAGUES`` of stats_gap.py (default) or, with ``--leagues config``,
``MODEL_LEAGUES`` of stats_config.py (the production set, which also has Cyprus).
Everything is read locally; the snapshot is opened read-only and nothing is fetched from the web.

Usage (from app/):
    .venv\\Scripts\\python scripts\\mk2\\eval_stats_model.py --wide-grid --weekly   # full, ~10 min
    .venv\\Scripts\\python scripts\\mk2\\eval_stats_model.py --no-grid              # quick, ~1.5 min
Output (<mk2>/stats/eval/): metrics.csv, verdict.csv, bias.csv, bias_month.csv, reliability.csv,
calibration.csv, grid.csv, weekly_nb2.csv, fits.csv, preds.csv.gz, lines.csv.gz, summary.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import special, stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stats_model as sm  # noqa: E402

FIRST_SEASON = 2022
TEST_SEASON = 2025
TEST_FROM = pd.Timestamp("2025-08-01", tz="UTC")
TEST_TO = pd.Timestamp("2026-06-01", tz="UTC")      # exclusive
DEFAULT = {"half_life_days": 180.0, "ridge_team": 300.0, "ridge_league": 3.0}
GRID_HL = (180.0, 365.0, 730.0)
GRID_RT = (10.0, 30.0, 100.0)
GRID_RT_WIDE = (300.0, 1000.0)
GRID_STATS = ("shots", "corners", "cards")
BASE_DAYS = 365
BASE_MIN_MATCHES = 30
BASE_CLIP = (0.02, 0.98)
EPS = 1e-6

LINES: dict[str, dict[str, tuple[float, ...]]] = {
    "shots": {"team": (10.5, 12.5, 14.5), "total": (23.5, 25.5, 27.5)},
    "sot": {"team": (3.5, 4.5), "total": (7.5, 8.5, 9.5)},
    "corners": {"team": (3.5, 4.5, 5.5), "total": (8.5, 9.5, 10.5, 11.5)},
    "cards": {"team": (1.5, 2.5), "total": (3.5, 4.5, 5.5)},
    "fouls": {"team": (10.5, 12.5), "total": (21.5, 23.5, 25.5)},
    "offsides": {"team": (1.5, 2.5), "total": (3.5, 4.5)},
    # yellow cards only: the counterpart of Oskar's "yellow" family (his lines)
    "yellow": {"team": (1.5, 2.5), "total": (2.5, 3.5, 4.5, 5.5)},
}
NB2_FAMILY = {"shots": "shots", "sot": "sot", "corners": "corners", "yellow": "yellow"}
BIG5 = {39: "EPL", 140: "LALIGA", 135: "SERIEA", 78: "BUNDESLIGA", 61: "LIGUE1"}
CUP_IDS = set(sm.CUPS)
VARIANTS = ("as_is", "season_fix")
SUBSETS = ("all", "domestic", "big5", "cups", "ucl", "ucl_main", "uel", "uecl")
SCOPES = {"team": ("home", "away"), "home": ("home",), "away": ("away",), "total": ("total",)}


# ---------------------------------------------------------------------------------------------
# inputs

def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + path.as_posix() + "?mode=ro&immutable=1", uri=True)


def model_leagues(source: str = "gap") -> dict[int, str]:
    """League ids of the model: LEAGUES of stats_gap.py ("gap", read from its source because importing
    it would load its HTTP client) or MODEL_LEAGUES of stats_config.py ("config")."""
    if source == "config":
        import stats_config  # plain dictionaries, no network code
        return {int(k): str(v) for k, v in stats_config.MODEL_LEAGUES.items()}
    tree = ast.parse((HERE / "stats_gap.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "LEAGUES":
            try:
                return {int(k): str(v) for k, v in ast.literal_eval(node.value).items()}
            except ValueError:
                raise SystemExit("LEAGUES in stats_gap.py is no longer a literal dict; use --leagues config")
    raise SystemExit("LEAGUES not found in stats_gap.py; use --leagues config")


def load_nb2(fixture_ids: set[int]) -> pd.DataFrame:
    """Oskar's NB2 over-probabilities for the test fixtures: family, scope, line, fixture_id, p_over, y."""
    path = sm.DATA / "predictions" / "count_nb2.sqlite"
    if not path.exists():
        print(f"no {path}: comparison with Oskar's NB2 skipped")
        return pd.DataFrame(columns=["family", "scope", "line", "fixture_id", "p_over", "y"])
    pat = re.compile(r"^forecasts_([a-z]+)_(home|away|total)_(\d+)_(\d+)$")
    frames = []
    with _ro(path) as con:
        names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        for name in sorted(names):
            m = pat.match(name)
            if not m:
                continue
            fam, scope, whole, frac = m.groups()
            d = pd.read_sql_query(f"select fixture_id, p_over, y from {name}", con)
            d = d[d["fixture_id"].isin(fixture_ids) & d["p_over"].notna()]
            frames.append(d.assign(family=fam, scope=scope, line=float(f"{whole}.{frac}")))
    nb2 = pd.concat(frames, ignore_index=True)
    return nb2.drop_duplicates(["family", "scope", "line", "fixture_id"], keep="last")


# ---------------------------------------------------------------------------------------------
# model forecasts

def p_over(mu: np.ndarray, alpha: np.ndarray, line: float) -> np.ndarray:
    """P(X > line) under NB2(mu, alpha) for arrays; the same formula as stats_model.nb_over."""
    mu = np.asarray(mu, dtype=float)
    r = 1.0 / np.broadcast_to(np.asarray(alpha, dtype=float), mu.shape)
    return stats.nbinom.sf(math.floor(line), r, r / (r + mu))


def check_p_over() -> None:
    for mu, alpha, line in ((12.3, 0.05, 12.5), (4.1, 0.0001, 3.5), (24.0, 0.013, 25.5), (1.7, 0.2, 1.5)):
        ref = sm.nb_over(mu, alpha, line)[0]
        got = float(p_over(np.array([mu]), np.array([alpha]), line)[0])
        assert abs(ref - got) < 1e-12, (mu, alpha, line, ref, got)


def predict(df: pd.DataFrame, test: pd.DataFrame, stat: str, hl: float, rt: float, bounds: list,
            leagues_m: dict, log: list, variant: str) -> pd.DataFrame:
    """Expected count of every test team-row from the fit at the start of its period."""
    col = sm.STATS[stat]
    parts = []
    for a, b in bounds:
        rows = test[(test["kickoff"] >= a) & (test["kickoff"] < b)]
        if rows.empty:
            continue
        t0 = time.time()
        fit = sm.fit_stat(df, stat, a, half_life_days=hl, ridge_team=rt,
                          ridge_league=DEFAULT["ridge_league"], leagues=leagues_m[a])
        mu = [fit.mean(int(t), int(o), bool(h), bool(c))
              for t, o, h, c in zip(rows["team_id"], rows["opp_id"], rows["home"], rows["cup"])]
        # cup matches use the model's cup dispersion when it has one (StatFit.dispersion)
        cups = rows["cup"].astype(bool).to_numpy()
        parts.append(pd.DataFrame({"mu": mu,
                                   "alpha": np.where(cups, fit.dispersion("home", True), fit.dispersion("home", False)),
                                   "alpha_total": np.where(cups, fit.dispersion("total", True), fit.dispersion("total", False))},
                                  index=rows.index))
        log.append({"variant": variant, "stat": stat, "half_life_days": hl, "ridge_team": rt,
                    "ridge_league": DEFAULT["ridge_league"], "asof": a.strftime("%Y-%m-%d"),
                    "n_train_rows": fit.n_rows, "n_test_rows": int(rows[col].notna().sum()),
                    "alpha": fit.alpha, "alpha_total": fit.alpha_total, "b_home": fit.b_home,
                    "b_cup": fit.b_cup, "b_season": fit.b_season, "seconds": round(time.time() - t0, 2)})
    return pd.concat(parts) if parts else pd.DataFrame(columns=["mu", "alpha", "alpha_total"])


def predict_variants(data: dict, test: pd.DataFrame, stat: str, hl: float, rt: float, bounds: list,
                     affected: list, leagues_m: dict, log: list) -> dict[str, pd.DataFrame]:
    """as_is for all months; season_fix refits only the months whose training data differ."""
    as_is = predict(data["as_is"], test, stat, hl, rt, bounds, leagues_m, log, "as_is")
    fixed = predict(data["season_fix"], test, stat, hl, rt, affected, leagues_m, log, "season_fix")
    return {"as_is": as_is, "season_fix": pd.concat([as_is.drop(index=fixed.index), fixed]).loc[as_is.index]}


def line_frame(rows: pd.DataFrame, stat: str) -> pd.DataFrame:
    """One row per forecast unit and line: team rows (scope home/away) and match totals (scope total)."""
    col = sm.STATS[stat]
    r = rows[rows[col].notna()]
    keep = ["fixture_id", "league_id", "month"]
    parts = []
    for line in LINES[stat]["team"]:
        p = r[keep].copy()
        p["scope"] = r["side"].to_numpy()
        p["line"] = line
        p["y"] = r[col].to_numpy()
        p["mu"] = r["mu"].to_numpy()
        p["p"] = p_over(r["mu"].to_numpy(), r["alpha"].to_numpy(), line)
        parts.append(p)
    tot = (r.groupby("fixture_id")
           .agg(league_id=("league_id", "first"), month=("month", "first"), y=(col, "sum"),
                mu=("mu", "sum"), alpha_total=("alpha_total", "first"), n=(col, "size"))
           .reset_index())
    tot = tot[tot["n"] == 2]
    for line in LINES[stat]["total"]:
        p = tot[keep].copy()
        p["scope"] = "total"
        p["line"] = line
        p["y"] = tot["y"].to_numpy()
        p["mu"] = tot["mu"].to_numpy()
        p["p"] = p_over(tot["mu"].to_numpy(), tot["alpha_total"].to_numpy(), line)
        parts.append(p)
    out = pd.concat(parts, ignore_index=True)
    out["o"] = (out["y"] > out["line"]).astype(float)
    out["stat"] = stat
    return out


# ---------------------------------------------------------------------------------------------
# comparisons

def baseline_tables(df: pd.DataFrame, stat: str, starts: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Over-frequencies in the 365 days before each fit date: per league (cups pooled as -1), and all leagues."""
    col = sm.STATS[stat]
    d = df[df[col].notna()]
    rec, rec_all = [], []
    for a in starts:
        w = d[(d["kickoff"] >= a - pd.Timedelta(days=BASE_DAYS)) & (d["kickoff"] < a)]
        grp = np.where(w["cup"] == 1, -1, w["league_id"])
        units = []
        for side in ("home", "away"):
            m = (w["side"] == side).to_numpy()
            units.append((side, grp[m], w[col].to_numpy()[m], LINES[stat]["team"]))
        t = (w.assign(group=grp).groupby("fixture_id")
             .agg(y=(col, "sum"), n=(col, "size"), group=("group", "first")))
        t = t[t["n"] == 2]
        units.append(("total", t["group"].to_numpy(), t["y"].to_numpy(), LINES[stat]["total"]))
        month = a.strftime("%Y-%m")
        for scope, g, y, lines in units:
            for line in lines:
                o = pd.Series((y > line).astype(float))
                s = o.groupby(g).agg(["mean", "size"]).reset_index()
                for gid, fr, n in zip(s.iloc[:, 0], s["mean"], s["size"]):
                    rec.append({"month": month, "group": int(gid), "scope": scope, "line": line,
                                "freq": float(fr), "n": int(n)})
                rec_all.append({"month": month, "scope": scope, "line": line, "freq_all": float(o.mean()),
                                "n_all": len(o)})
    return pd.DataFrame(rec), pd.DataFrame(rec_all)


def add_baseline(long: pd.DataFrame, tables: tuple[pd.DataFrame, pd.DataFrame]) -> pd.DataFrame:
    by_league, every = tables
    long = long.assign(group=np.where(long["league_id"].isin(CUP_IDS), -1, long["league_id"]))
    by_league = by_league[by_league["n"] >= BASE_MIN_MATCHES][["month", "group", "scope", "line", "freq"]]
    long = long.merge(by_league, on=["month", "group", "scope", "line"], how="left")
    long = long.merge(every[["month", "scope", "line", "freq_all"]], on=["month", "scope", "line"], how="left")
    long["base_fallback"] = long["freq"].isna()
    long["p_base"] = np.clip(long["freq"].fillna(long["freq_all"]), *BASE_CLIP)
    return long.drop(columns=["freq", "freq_all"])


def add_nb2(long: pd.DataFrame, nb2: pd.DataFrame, stat: str) -> pd.DataFrame:
    fam = NB2_FAMILY.get(stat)
    if fam is None or nb2.empty:
        return long.assign(p_nb2=np.nan, o_nb2=np.nan)
    n = (nb2[nb2["family"] == fam][["fixture_id", "scope", "line", "p_over", "y"]]
         .rename(columns={"p_over": "p_nb2", "y": "o_nb2"}))
    return long.merge(n, on=["fixture_id", "scope", "line"], how="left")


def add_scores(long: pd.DataFrame) -> pd.DataFrame:
    o = long["o"].to_numpy()
    for key, col in (("m", "p"), ("b", "p_base"), ("n", "p_nb2")):
        p = np.clip(long[col].to_numpy(dtype=float), EPS, 1 - EPS)
        long[f"ll_{key}"] = -(o * np.log(p) + (1 - o) * np.log1p(-p))
        long[f"br_{key}"] = (long[col].to_numpy(dtype=float) - o) ** 2
    return long


def subset_mask(frame: pd.DataFrame, subset: str) -> np.ndarray:
    lid = frame["league_id"]
    masks = {
        "all": lambda: np.ones(len(frame), dtype=bool),
        "domestic": lambda: (~lid.isin(CUP_IDS)).to_numpy(),
        "big5": lambda: lid.isin(set(BIG5)).to_numpy(),
        "cups": lambda: lid.isin(CUP_IDS).to_numpy(),
        "ucl": lambda: (lid == 2).to_numpy(),
        "ucl_main": lambda: ((lid == 2) & (frame["month"] >= "2025-09")).to_numpy(),
        "uel": lambda: (lid == 3).to_numpy(),
        "uecl": lambda: (lid == 848).to_numpy(),
    }
    return masks[subset]()


def mean_se(values: np.ndarray, clusters: np.ndarray) -> tuple[float, float]:
    """Mean and its standard error with clusters (matches) as the independent units."""
    s = pd.Series(values).groupby(np.asarray(clusters)).agg(["sum", "size"])
    n, g = float(s["size"].sum()), len(s)
    if n == 0:
        return float("nan"), float("nan")
    m = float(s["sum"].sum()) / n
    if g < 2:
        return m, float("nan")
    resid = s["sum"].to_numpy() - s["size"].to_numpy() * m
    return m, float(math.sqrt((resid ** 2).sum() * g / (g - 1)) / n)


def score_group(g: pd.DataFrame) -> dict:
    r = {"n": len(g), "n_matches": g["fixture_id"].nunique(), "freq_over": g["o"].mean(),
         "mean_p_model": g["p"].mean(), "mean_p_base": g["p_base"].mean(),
         "ll_model": g["ll_m"].mean(), "ll_base": g["ll_b"].mean(),
         "brier_model": g["br_m"].mean(), "brier_base": g["br_b"].mean()}
    r["dll_base"], r["dll_base_se"] = mean_se((g["ll_b"] - g["ll_m"]).to_numpy(), g["fixture_id"].to_numpy())
    h = g[g["p_nb2"].notna()]
    r["n_nb2"] = len(h)
    if len(h):
        r.update({"ll_model_nb2": h["ll_m"].mean(), "ll_base_nb2": h["ll_b"].mean(), "ll_nb2": h["ll_n"].mean(),
                  "brier_model_nb2": h["br_m"].mean(), "brier_nb2": h["br_n"].mean(),
                  "mean_p_nb2": h["p_nb2"].mean(), "y_agree_nb2": float((h["o_nb2"] == h["o"]).mean())})
        r["dll_nb2"], r["dll_nb2_se"] = mean_se((h["ll_n"] - h["ll_m"]).to_numpy(), h["fixture_id"].to_numpy())
    return r


def metrics_table(L: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for subset in SUBSETS:
        Ls = L[subset_mask(L, subset)]
        for stat in LINES:
            Lst = Ls[Ls["stat"] == stat]
            for scope, members in SCOPES.items():
                Lsc = Lst[Lst["scope"].isin(members)]
                for line in LINES[stat]["total" if scope == "total" else "team"]:
                    g = Lsc[Lsc["line"] == line]
                    if len(g):
                        rows.append({"subset": subset, "stat": stat, "scope": scope, "line": line, **score_group(g)})
    return pd.DataFrame(rows)


def _verdict(d, se) -> str:
    if d is None or not np.isfinite(d):
        return "n/a"
    if np.isfinite(se) and d > 2 * se:
        return "model better"
    if np.isfinite(se) and d < -2 * se:
        return "model worse"
    return "no clear difference"


def verdict_table(L: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Per subset and statistic, all lines together (home, away and total forecasts)."""
    rows = []
    for subset in SUBSETS:
        Ls = L[subset_mask(L, subset)]
        for stat in LINES:
            g = Ls[Ls["stat"] == stat]
            if g.empty:
                continue
            m = metrics[(metrics["subset"] == subset) & (metrics["stat"] == stat)
                        & metrics["scope"].isin(["team", "total"])]
            r = {"subset": subset, "stat": stat, "n_forecasts": len(g), "n_matches": g["fixture_id"].nunique(),
                 "ll_model": g["ll_m"].mean(), "ll_base": g["ll_b"].mean(),
                 "brier_model": g["br_m"].mean(), "brier_base": g["br_b"].mean()}
            r["dll_base"], r["dll_base_se"] = mean_se((g["ll_b"] - g["ll_m"]).to_numpy(), g["fixture_id"].to_numpy())
            r["lines_better_than_base"] = f"{int((m['ll_model'] < m['ll_base']).sum())}/{len(m)}"
            h = g[g["p_nb2"].notna()]
            r["n_nb2_forecasts"] = len(h)
            r["n_nb2_matches"] = h["fixture_id"].nunique()
            if len(h):
                r["ll_model_nb2"] = h["ll_m"].mean()
                r["ll_nb2"] = h["ll_n"].mean()
                r["brier_model_nb2"] = h["br_m"].mean()
                r["brier_nb2"] = h["br_n"].mean()
                r["dll_nb2"], r["dll_nb2_se"] = mean_se((h["ll_n"] - h["ll_m"]).to_numpy(),
                                                        h["fixture_id"].to_numpy())
                mm = m[m["n_nb2"] > 0]
                r["lines_better_than_nb2"] = f"{int((mm['ll_model_nb2'] < mm['ll_nb2']).sum())}/{len(mm)}"
            r["verdict_base"] = _verdict(r["dll_base"], r["dll_base_se"])
            r["verdict_nb2"] = _verdict(r.get("dll_nb2"), r.get("dll_nb2_se")) if len(h) else "no NB2 forecasts"
            rows.append(r)
    return pd.DataFrame(rows)


def bias_tables(L: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    units = L.drop_duplicates(["stat", "fixture_id", "scope"])[
        ["stat", "fixture_id", "league_id", "month", "scope", "mu", "y"]]
    rows = []
    for subset in SUBSETS:
        u = units[subset_mask(units, subset)]
        for stat in LINES:
            for scope, members in SCOPES.items():
                g = u[(u["stat"] == stat) & u["scope"].isin(members)]
                if len(g):
                    rows.append({"subset": subset, "stat": stat, "scope": scope, "n": len(g),
                                 "mean_forecast": g["mu"].mean(), "mean_actual": g["y"].mean(),
                                 "diff": g["mu"].mean() - g["y"].mean(),
                                 "ratio": g["mu"].mean() / g["y"].mean() if g["y"].mean() else np.nan})
    by_month = []
    for comp in ("domestic", "cups"):
        u = units[subset_mask(units, comp)]
        u = u.assign(sc=np.where(u["scope"] == "total", "total", "team"))
        for (stat, month, scope), g in u.groupby(["stat", "month", "sc"]):
            by_month.append({"subset": comp, "stat": stat, "month": month, "scope": scope, "n": len(g),
                             "mean_forecast": g["mu"].mean(), "mean_actual": g["y"].mean(),
                             "ratio": g["mu"].mean() / g["y"].mean() if g["y"].mean() else np.nan})
    return pd.DataFrame(rows), pd.DataFrame(by_month)


def recalibration(p: np.ndarray, o: np.ndarray) -> tuple[float, float]:
    """Intercept and slope of the logistic regression of the outcome on logit(P); (0, 1) = calibrated."""
    x = special.logit(np.clip(p, EPS, 1 - EPS))
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(50):
        q = special.expit(X @ beta)
        h = X.T @ (X * (q * (1 - q))[:, None])
        step = np.linalg.solve(h, X.T @ (o - q))
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def calibration_tables(L: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reliability bins (model) and a calibration summary (model and baseline), all lines pooled per stat."""
    rel, cal = [], []
    for subset in ("all", "domestic", "cups", "ucl"):
        Ls = L[subset_mask(L, subset)]
        for stat in LINES:
            g = Ls[Ls["stat"] == stat]
            if g.empty:
                continue
            for key, col in (("model", "p"), ("base", "p_base")):
                p, o = g[col].to_numpy(dtype=float), g["o"].to_numpy(dtype=float)
                b = np.minimum((p * 10).astype(int), 9)
                ece = 0.0
                for k in range(10):
                    m = b == k
                    if m.any():
                        ece += m.sum() / len(p) * abs(p[m].mean() - o[m].mean())
                        if key == "model":
                            rel.append({"subset": subset, "stat": stat, "bin": f"{k / 10:.1f}-{(k + 1) / 10:.1f}",
                                        "n": int(m.sum()), "mean_p": p[m].mean(), "freq_over": o[m].mean()})
                a0, a1 = recalibration(p, o)
                cal.append({"subset": subset, "stat": stat, "forecast": key, "n": len(p), "mean_p": p.mean(),
                            "freq_over": o.mean(), "ece": ece, "recal_intercept": a0, "recal_slope": a1})
    return pd.DataFrame(rel), pd.DataFrame(cal)


def grid_rows(long: pd.DataFrame, hl: float, rt: float, stat: str, variant: str) -> list[dict]:
    o = long["o"].to_numpy()
    p = np.clip(long["p"].to_numpy(), EPS, 1 - EPS)
    long = long.assign(ll=-(o * np.log(p) + (1 - o) * np.log1p(-p)),
                       sc=np.where(long["scope"] == "total", "total", "team"))
    out = []
    for subset in ("all", "domestic", "cups", "ucl", "big5"):
        per_line = long[subset_mask(long, subset)].groupby(["sc", "line"])["ll"].mean()
        out.append({"variant": variant, "half_life_days": hl, "ridge_team": rt, "stat": stat, "subset": subset,
                    "ll_mean_over_lines": float(per_line.mean()), "n_lines": len(per_line)})
    return out


def grid_best(grid: pd.DataFrame, variant: str, subset: str = "all") -> tuple[pd.DataFrame, dict]:
    g = grid[(grid["variant"] == variant) & (grid["subset"] == subset)].pivot_table(
        index=["half_life_days", "ridge_team"], columns="stat", values="ll_mean_over_lines")
    g["mean"] = g[list(GRID_STATS)].mean(axis=1)
    default = (DEFAULT["half_life_days"], DEFAULT["ridge_team"])
    best = {}
    for s in [*GRID_STATS, "mean"]:
        k = g[s].idxmin()
        best[s] = {"half_life_days": k[0], "ridge_team": k[1], "ll": float(g.loc[k, s]),
                   "ll_default": float(g.loc[default, s]), "gain_vs_default": float(g.loc[default, s] - g.loc[k, s])}
    return g.reset_index(), best


# ---------------------------------------------------------------------------------------------

def evaluate(L: pd.DataFrame) -> dict[str, pd.DataFrame]:
    metrics = metrics_table(L)
    bias, bias_month = bias_tables(L)
    rel, cal = calibration_tables(L)
    return {"metrics": metrics, "verdict": verdict_table(L, metrics), "bias": bias, "bias_month": bias_month,
            "reliability": rel, "calibration": cal}


def weekly_check(data: dict, test: pd.DataFrame, nb2: pd.DataFrame, monthly: dict, log: list) -> pd.DataFrame:
    """NB2 comparison with weekly refits (season_fix data) next to the monthly ones, same forecasts."""
    starts = list(pd.date_range(TEST_FROM, TEST_TO, freq="7D", inclusive="left"))
    bounds = [(a, min(a + pd.Timedelta(days=7), TEST_TO)) for a in starts]
    leagues_w = {a: sm.home_league(data["as_is"][data["as_is"]["kickoff"] < b]) for a, b in bounds}
    rows = []
    for stat in NB2_FAMILY:
        pr = predict(data["season_fix"], test, stat, DEFAULT["half_life_days"], DEFAULT["ridge_team"], bounds,
                     leagues_w, log, "season_fix_weekly")
        w = add_scores(add_nb2(line_frame(test.join(pr), stat).assign(p_base=0.5), nb2, stat))
        w = w[w["p_nb2"].notna()]
        key = ["fixture_id", "scope", "line"]
        for variant in VARIANTS:
            m = monthly[variant]
            m = m[(m["stat"] == stat) & m["p_nb2"].notna()][key + ["ll_m"]].rename(columns={"ll_m": f"ll_{variant}"})
            w = w.merge(m, on=key, how="left")
        d, se = mean_se((w["ll_n"] - w["ll_m"]).to_numpy(), w["fixture_id"].to_numpy())
        rows.append({"stat": stat, "n_forecasts": len(w), "n_matches": w["fixture_id"].nunique(),
                     "ll_monthly_as_is": w["ll_as_is"].mean(), "ll_monthly_season_fix": w["ll_season_fix"].mean(),
                     "ll_weekly_season_fix": w["ll_m"].mean(), "ll_nb2": w["ll_n"].mean(),
                     "dll_nb2_weekly": d, "dll_nb2_weekly_se": se, "verdict_nb2_weekly": _verdict(d, se)})
        print(f"weekly {stat} done", flush=True)
    return pd.DataFrame(rows)


def fmt(frame: pd.DataFrame, digits: int = 4) -> str:
    return frame.to_string(index=False, float_format=lambda v: f"{v:.{digits}f}")


def pm(d, se) -> str:
    return "" if d is None or not np.isfinite(d) else f"{d:+.4f}+-{se:.4f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(sm.OUT / "stats" / "eval"))
    p.add_argument("--no-grid", action="store_true", help="skip the hyper-parameter grid (and the tuned variant)")
    p.add_argument("--wide-grid", action="store_true", help="grid also with ridge_team 300 and 1000")
    p.add_argument("--weekly", action="store_true", help="also compare weekly refits with Oskar's NB2")
    p.add_argument("--leagues", choices=("gap", "config"), default="gap",
                   help="league ids: stats_gap.LEAGUES (default) or stats_config.MODEL_LEAGUES")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    check_p_over()

    leagues = model_leagues(args.leagues)
    model_file = Path(sm.__file__)
    model_version = {"file": str(model_file), "md5": hashlib.md5(model_file.read_bytes()).hexdigest(),
                     "modified": pd.Timestamp(model_file.stat().st_mtime, unit="s", tz="UTC").isoformat()}
    print(f"stats_model.py md5 {model_version['md5']}, modified {model_version['modified']}", flush=True)
    df = sm.load_rows(list(leagues), first_season=FIRST_SEASON)
    data = {"as_is": df, "season_fix": df[df["season"] <= TEST_SEASON]}
    test = df[(df["season"] == TEST_SEASON) & (df["kickoff"] >= TEST_FROM) & (df["kickoff"] < TEST_TO)].copy()
    test["month"] = test["kickoff"].dt.strftime("%Y-%m")
    starts = list(pd.date_range(TEST_FROM, TEST_TO, freq="MS", inclusive="left"))
    bounds = [(a, a + pd.offsets.MonthBegin(1)) for a in starts]
    later = df[df["season"] > TEST_SEASON]
    affected = [(a, b) for a, b in bounds if (later["kickoff"] < a).any()]
    leagues_m = {a: sm.home_league(df[df["kickoff"] < b]) for a, b in bounds}
    n_fx = {s: int(test.loc[subset_mask(test, s), "fixture_id"].nunique()) for s in SUBSETS}
    print(f"rows {len(df)} (history from {FIRST_SEASON}), test fixtures {n_fx}, months {len(starts)}, "
          f"season_fix refits {[a.strftime('%Y-%m') for a, _ in affected]}", flush=True)

    nb2 = load_nb2(set(int(x) for x in test["fixture_id"].unique()))
    print(f"NB2 forecasts on test fixtures: {len(nb2)} rows, {nb2['fixture_id'].nunique()} fixtures", flush=True)

    fits_log: list[dict] = []
    preds: dict[str, dict[str, pd.DataFrame]] = {}
    frames: dict[str, list] = {v: [] for v in VARIANTS}
    base_tables = {}
    for stat in LINES:
        t0 = time.time()
        preds[stat] = predict_variants(data, test, stat, DEFAULT["half_life_days"], DEFAULT["ridge_team"], bounds,
                                       affected, leagues_m, fits_log)
        base_tables[stat] = baseline_tables(df, stat, starts)
        for variant in VARIANTS:
            long = line_frame(test.join(preds[stat][variant]), stat)
            frames[variant].append(add_nb2(add_baseline(long, base_tables[stat]), nb2, stat))
        print(f"{stat}: {len(long)} line forecasts per variant, {time.time() - t0:.0f}s", flush=True)
    Ls = {v: add_scores(pd.concat(frames[v], ignore_index=True)) for v in VARIANTS}
    res = {v: evaluate(Ls[v]) for v in VARIANTS}

    grid = pd.DataFrame()
    tuned = None
    if not args.no_grid:
        rows = []
        for hl in GRID_HL:
            for rt in GRID_RT + (GRID_RT_WIDE if args.wide_grid else ()):
                for stat in GRID_STATS:
                    if hl == DEFAULT["half_life_days"] and rt == DEFAULT["ridge_team"]:
                        pv = preds[stat]
                    else:
                        pv = predict_variants(data, test, stat, hl, rt, bounds, affected, leagues_m, fits_log)
                    for variant in VARIANTS:
                        rows += grid_rows(line_frame(test.join(pv[variant]), stat), hl, rt, stat, variant)
                print(f"grid half-life {hl:.0f}, ridge_team {rt:.0f} done ({time.time() - started:.0f}s)", flush=True)
        grid = pd.DataFrame(rows)
        # tuned variant: season_fix data with the best setting of the grid, every statistic
        b = grid_best(grid, "season_fix", "all")[1]["mean"]
        tuned = {"half_life_days": b["half_life_days"], "ridge_team": b["ridge_team"],
                 "ridge_league": DEFAULT["ridge_league"]}
        tf = []
        for stat in LINES:
            pt = predict(data["season_fix"], test, stat, tuned["half_life_days"], tuned["ridge_team"], bounds,
                         leagues_m, fits_log, "tuned")
            preds[stat]["tuned"] = pt
            tf.append(add_nb2(add_baseline(line_frame(test.join(pt), stat), base_tables[stat]), nb2, stat))
        Ls["tuned"] = add_scores(pd.concat(tf, ignore_index=True))
        res["tuned"] = evaluate(Ls["tuned"])
        print(f"tuned variant {tuned} done ({time.time() - started:.0f}s)", flush=True)

    weekly = weekly_check(data, test, nb2, Ls, fits_log) if args.weekly else pd.DataFrame()

    # ------------------------------------------------------------------ files
    variants = list(res)

    def stack(name: str) -> pd.DataFrame:
        return pd.concat([res[v][name].assign(variant=v) for v in variants], ignore_index=True)

    tables_out = {name: stack(name) for name in ("metrics", "verdict", "bias", "bias_month", "reliability",
                                                 "calibration")}
    for name, frame in tables_out.items():
        cols = ["variant"] + [c for c in frame.columns if c != "variant"]
        frame[cols].to_csv(out / f"{name}.csv", index=False, float_format="%.6f")
    pd.DataFrame(fits_log).to_csv(out / "fits.csv", index=False, float_format="%.6f")
    if not grid.empty:
        grid.to_csv(out / "grid.csv", index=False, float_format="%.6f")
    if not weekly.empty:
        weekly.to_csv(out / "weekly_nb2.csv", index=False, float_format="%.6f")
    pr = test[["fixture_id", "league_id", "season", "kickoff_utc", "month", "side", "team_id", "opp_id", "cup"]].copy()
    for stat in LINES:
        pr[f"y_{stat}"] = test[sm.STATS[stat]]
        for variant in variants:
            pr[f"mu_{stat}_{variant}"] = preds[stat][variant]["mu"].round(4)
    pr.to_csv(out / "preds.csv.gz", index=False)
    cols = ["stat", "fixture_id", "league_id", "month", "scope", "line", "y", "mu", "o", "p", "p_base",
            "base_fallback", "p_nb2", "o_nb2"]
    pd.concat([Ls[v][cols].assign(variant=v) for v in variants], ignore_index=True).to_csv(
        out / "lines.csv.gz", index=False, float_format="%.5f")

    # ------------------------------------------------------------------ summary
    best = {}
    if not grid.empty:
        for variant in VARIANTS:
            best[variant] = {sub: grid_best(grid, variant, sub)[1] for sub in ("all", "cups")}
    summary = {
        "created": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "stats_model": model_version,
        "design": {"history_from_season": FIRST_SEASON, "test_season": TEST_SEASON,
                   "test_from": str(TEST_FROM.date()), "test_to_exclusive": str(TEST_TO.date()),
                   "refit": "monthly, asof = first day of month (UTC)", "params": DEFAULT,
                   "tuned_params": tuned, "league_source": args.leagues, "league_ids": sorted(leagues),
                   "season_fix_months": [a.strftime("%Y-%m") for a, _ in affected],
                   "baseline": f"league over-frequency, {BASE_DAYS} days before asof, home/away/total separately, "
                               f"UEFA cups pooled, all leagues if < {BASE_MIN_MATCHES} matches, clip {BASE_CLIP}",
                   "lines": {s: {k: list(v) for k, v in d.items()} for s, d in LINES.items()}},
        "test_fixtures": n_fx,
        "nb2_rows_on_test_fixtures": int(len(nb2)),
        "verdict": tables_out["verdict"].replace({np.nan: None}).to_dict(orient="records"),
        "calibration": tables_out["calibration"].to_dict(orient="records"),
        "grid_best": best,
        "weekly_nb2": weekly.to_dict(orient="records"),
        "seconds": round(time.time() - started, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")

    # ------------------------------------------------------------------ screen
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 1000)
    key = ["subset", "stat", "scope", "line"]
    short = {"as_is": "", "season_fix": "_fix", "tuned": "_tuned"}
    t = res["as_is"]["metrics"]
    for variant in variants[1:]:
        t = t.merge(res[variant]["metrics"][key + ["ll_model", "ll_model_nb2"]].rename(
            columns={"ll_model": f"ll{short[variant]}", "ll_model_nb2": f"ll{short[variant]}_nb2"}), on=key)
    show = t[(t["subset"] == "all") & t["scope"].isin(["team", "total"])]
    print(f"\n== all test matches: log-loss per line (model {' / '.join(variants)}, baseline, "
          f"Oskar NB2 on the overlap) ==")
    print(fmt(show[["stat", "scope", "line", "n", "freq_over", "ll_model"] + [f"ll{short[v]}" for v in variants[1:]]
                   + ["ll_base", "n_nb2", "ll_model_nb2"] + [f"ll{short[v]}_nb2" for v in variants[1:]]
                   + ["ll_nb2"]]))
    va = res["as_is"]["verdict"]
    v = va[["subset", "stat", "n_matches", "n_nb2_matches"]].copy()
    for variant in variants:
        vv = res[variant]["verdict"]
        v[f"base{short[variant]}"] = [pm(d, s) for d, s in zip(vv["dll_base"], vv["dll_base_se"])]
    for variant in variants:
        vv = res[variant]["verdict"]
        v[f"nb2{short[variant]}"] = [pm(d, s) for d, s in zip(vv["dll_nb2"], vv["dll_nb2_se"])]
    vf = res["season_fix"]["verdict"]
    v["lines_vs_base_fix"] = vf["lines_better_than_base"]
    v["lines_vs_nb2_fix"] = vf["lines_better_than_nb2"]
    print("\n== log-loss gain of the model (other - model, > 0 = model better; +- SE clustered by match) ==")
    print(v.to_string(index=False))
    for variant in variants:
        b = res[variant]["bias"]
        b = b[b["subset"].isin(["domestic", "cups", "ucl"]) & (b["scope"] == "team")]
        print(f"\n== mean bias (forecast / actual, team counts; totals have the same ratio), {variant} ==")
        print(fmt(b.pivot_table(index="stat", columns="subset", values="ratio").reset_index(), 3))
    cal = tables_out["calibration"]
    cal = cal[cal["subset"].isin(["all", "cups", "ucl"])]
    cal = cal.assign(name=np.where(cal["forecast"] == "base", "base", cal["variant"]))
    cal = cal[(cal["forecast"] == "model") | (cal["variant"] == "as_is")]
    print("\n== calibration: logistic recalibration slope (1 = calibrated, < 1 = overconfident) and ECE ==")
    print(fmt(cal.pivot_table(index=["subset", "stat"], columns="name", values=["recal_slope", "ece"])
              .reset_index(), 3))
    rel = res["as_is"]["reliability"]
    rel = rel[rel["subset"] == "all"]
    print("\n== reliability, as_is, all matches: observed over-frequency per bin of P(over) ==")
    print(fmt(rel.pivot_table(index="stat", columns="bin", values="freq_over").reset_index(), 3))
    print(fmt(rel.pivot_table(index="stat", columns="bin", values="mean_p").reset_index(), 3))
    if not grid.empty:
        for variant in VARIANTS:
            for sub in ("all", "cups"):
                g, bst = grid_best(grid, variant, sub)
                print(f"\n== grid, {variant}, {sub}: mean log-loss over lines ==")
                print(fmt(g, 5))
                print("best:", json.dumps(bst, default=float))
    if not weekly.empty:
        print("\n== weekly refits vs Oskar NB2 (same forecasts) ==")
        print(fmt(weekly))
    print(f"\nwritten to {out} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
