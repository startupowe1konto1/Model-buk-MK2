"""Team-statistics model: expected shots, shots on target, corners, fouls, cards and offsides per team.

For every statistic one ridge-penalised Poisson regression is fitted on all team-rows (two per
match) of the European top flights and the UEFA cups::

    log mu(team vs opp) = b0 + b_home * home + b_cup * cup + b_home_cup * home * cup + level[half-year]
                          + league_for[league of team] + league_against[league of opp]
                          + team_for[team] + team_against[opp]

``level`` follows the level of the statistic from half-year to half-year (the provider's shot counts
rose by about a tenth in 2026), with a penalty on each change; forecasts use the latest half-year.
``team_for`` is how much a team produces of the statistic (shots it takes, fouls it commits),
``team_against`` how much it concedes to opponents.  League effects let a cup match compare teams
from different leagues; team effects are shrunk towards their league (ridge), more strongly when
the team has few matches.  Older matches count less (half-life in days).  The spread around the
mean is a negative binomial (NB2) with one dispersion per statistic for team counts and one for
match totals, both fitted by maximum likelihood on the same weighted rows.

Data: the read-only snapshot history plus ``<mk2>/stats/gap.sqlite`` (newer matches).  Missing
values: API-Football leaves yellow/red cards and offsides empty when there were none, so those
nulls are read as 0 in a row that has its statistics block; a missing shot, shot-on-target,
corner or foul count is skipped (never read as 0).
"""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, special, stats

DATA = Path(os.environ.get("MODEL_BUK_DATA") or Path.home() / "Documents" / "Model-Buk-Data")
OUT = Path(os.environ.get("MODEL_BUK_MK2_OUT") or Path.home() / "Documents" / "Model-Buk-Data-MK2")

CUPS = {2: "UCL", 3: "UEL", 848: "UECL"}
FINISHED = ("FT", "AET", "PEN")

# statistic -> how it is read from team_match_stats
STATS: dict[str, str] = {
    "shots": "total_shots",
    "sot": "shots_on_goal",
    "corners": "corners",
    "fouls": "fouls",
    "cards": "cards",          # yellow + red (a red counts once)
    "yellow": "yellow_cards",
    "offsides": "offsides",
}
STAT_PL = {"shots": "strzały", "sot": "strzały celne", "corners": "rożne", "fouls": "faule",
           "cards": "kartki", "yellow": "żółte kartki", "offsides": "spalone"}
# Counts modelled only for bet builders (scoring and winning); never picked or shown on their own.
# Goals are the 90-minute score: matches that went to extra time have them missing.
EXTRA_STATS: dict[str, str] = {"goals": "goals"}

MIN_CUP_ROWS = 300

COLUMNS = ["total_shots", "shots_on_goal", "corners", "fouls", "yellow_cards", "red_cards", "offsides"]


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + path.as_posix() + "?mode=ro&immutable=1", uri=True)


def load_rows(league_ids: list[int], first_season: int = 2024) -> pd.DataFrame:
    """One row per team per finished match with statistics: snapshot history plus the gap database."""
    cols = ", ".join(f"s.{c}" for c in COLUMNS)
    q = f"""select m.fixture_id, m.league_id, m.season, m.kickoff_utc, m.referee, m.status_short,
                   m.home_team_id, m.home_team, m.away_team_id, m.away_team, m.home_goals, m.away_goals,
                   s.team_id, s.side, {cols}
            from matches m join team_match_stats s on s.fixture_id = m.fixture_id
            where m.season >= ? and m.status_short in ('FT','AET','PEN')
              and m.league_id in ({','.join('?' * len(league_ids))})"""
    frames = []
    snap = DATA / "api-football" / "tables" / "history.sqlite"
    with _ro(snap) as con:
        frames.append(pd.read_sql_query(q, con, params=[first_season, *league_ids]))
    gap = OUT / "stats" / "gap.sqlite"
    if gap.exists():
        with _ro(gap) as con:
            frames.append(pd.read_sql_query(q, con, params=[first_season, *league_ids]))
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["fixture_id", "team_id"], keep="last")
    df = df[df["side"].isin(["home", "away"])].copy()
    # keep matches with both team rows
    both = df.groupby("fixture_id")["team_id"].transform("count") == 2
    df = df[both].copy()
    df["kickoff"] = pd.to_datetime(df["kickoff_utc"], utc=True)
    df["opp_id"] = np.where(df["side"] == "home", df["away_team_id"], df["home_team_id"])
    df["team"] = np.where(df["side"] == "home", df["home_team"], df["away_team"])
    df["opp"] = np.where(df["side"] == "home", df["away_team"], df["home_team"])
    df["home"] = (df["side"] == "home").astype(float)
    df["cup"] = df["league_id"].isin(list(CUPS)).astype(float)
    goals = np.where(df["side"] == "home", df["home_goals"], df["away_goals"]).astype(float)
    df["goals"] = np.where(df["status_short"] == "FT", goals, np.nan)
    return fill_known_zeros(df)


def fill_known_zeros(df: pd.DataFrame) -> pd.DataFrame:
    """Read the provider's empty card and offside counts as zeros; leave every other gap missing.

    The provider leaves cards and offsides empty when there were none (checked against card events
    and players' offsides), so those nulls are zeros in a row whose statistics block is there.  A
    null shot, corner or foul count means the statistic is missing (cup qualifiers often have
    corners but no shots): such rows are skipped for that statistic, never read as zero.
    """
    has_block = df[["total_shots", "corners"]].notna().any(axis=1)
    for c in ("yellow_cards", "red_cards"):
        df.loc[df[c].isna() & has_block, c] = 0
    df.loc[df["offsides"].isna() & df["total_shots"].notna(), "offsides"] = 0
    df["cards"] = df["yellow_cards"] + df["red_cards"]
    return df


def home_league(df: pd.DataFrame) -> dict[int, int]:
    """Each team's domestic league: the non-cup league it played most matches in (else 0)."""
    dom = df[df["cup"] == 0].groupby(["team_id", "league_id"]).size().reset_index(name="n")
    dom = dom.sort_values("n").drop_duplicates("team_id", keep="last")
    out = dict(zip(dom["team_id"], dom["league_id"]))
    for t in df["team_id"].unique():
        out.setdefault(int(t), 0)
    return {int(k): int(v) for k, v in out.items()}


@dataclass
class StatFit:
    stat: str
    b0: float
    b_home: float
    b_cup: float
    b_season: float  # level of the latest half-year with data: the one forecasts use
    league_for: dict[int, float]
    league_against: dict[int, float]
    team_for: dict[int, float]
    team_against: dict[int, float]
    alpha: float
    alpha_total: float
    n_rows: int
    leagues: dict[int, int] = field(default_factory=dict)
    b_home_cup: float = 0.0
    period_effects: dict[str, float] = field(default_factory=dict)
    alpha_cup: float | None = None        # dispersion of team counts in UEFA cup matches
    alpha_total_cup: float | None = None  # and of their match totals

    def dispersion(self, scope: str, cup: bool) -> float:
        """NB2 alpha for a team count (scope home/away) or a match total, in a cup match or not."""
        if scope == "total":
            return self.alpha_total_cup if cup and self.alpha_total_cup is not None else self.alpha_total
        return self.alpha_cup if cup and self.alpha_cup is not None else self.alpha

    def mean(self, team: int, opp: int, home: bool, cup: bool) -> float:
        lt, lo = self.leagues.get(team, 0), self.leagues.get(opp, 0)
        eta = (self.b0 + self.b_home * home + self.b_cup * cup + self.b_home_cup * (home and cup) + self.b_season
               + self.league_for.get(lt, 0.0) + self.league_against.get(lo, 0.0)
               + self.team_for.get(team, 0.0) + self.team_against.get(opp, 0.0))
        return float(math.exp(eta))


def _nb_loglik(y: np.ndarray, mu: np.ndarray, alpha: float, w: np.ndarray) -> float:
    r = 1.0 / alpha
    ll = (special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1)
          + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu)))
    return float(np.sum(w * ll))


def _fit_alpha(y: np.ndarray, mu: np.ndarray, w: np.ndarray) -> float:
    res = optimize.minimize_scalar(lambda la: -_nb_loglik(y, mu, math.exp(la), w),
                                   bounds=(math.log(1e-4), math.log(5.0)), method="bounded")
    return float(math.exp(res.x))


def period_of(kickoff: pd.Series) -> pd.Series:
    """Half-year index (year * 2 + 0 for January-June, 1 for July-December)."""
    return kickoff.dt.year * 2 + (kickoff.dt.month >= 7).astype(int)


def fit_stat(df: pd.DataFrame, stat: str, asof: pd.Timestamp, half_life_days: float = 180.0,
             ridge_team: float = 300.0, ridge_league: float = 3.0, leagues: dict[int, int] | None = None,
             fuse_periods: float = 50.0) -> StatFit:
    """Fit one statistic on the matches before ``asof``.

    Defaults from the 2025/26 walk-forward backtest (scripts/mk2/eval_stats_model.py): half-life
    180 days and team ridge 300 had the best log-loss and removed the over-confidence of the
    earlier 365/30.  The level is one effect per half-year with a penalty on the change from one
    half-year to the next (``fuse_periods``), so a half-year that has barely started stays at the
    previous level instead of jumping on a few rounds of one league (the old per-season effect
    took the level from Norway's calendar-year season every spring).  ``b_home_cup`` lets the
    home advantage differ in the UEFA cups.
    """
    col = STATS.get(stat) or EXTRA_STATS[stat]
    d = df[df[col].notna() & (df["kickoff"] < asof)].copy()
    leagues = leagues or home_league(df)
    teams = np.array(sorted(set(d["team_id"]) | set(d["opp_id"])))
    t_idx = {int(t): i for i, t in enumerate(teams)}
    lg = np.array(sorted(set(leagues.get(int(t), 0) for t in teams)))
    l_idx = {int(x): i for i, x in enumerate(lg)}
    nt, nl = len(teams), len(lg)

    ti = d["team_id"].map(lambda t: t_idx[int(t)]).to_numpy()
    oi = d["opp_id"].map(lambda t: t_idx[int(t)]).to_numpy()
    tl = np.array([l_idx[leagues.get(int(t), 0)] for t in d["team_id"]])
    ol = np.array([l_idx[leagues.get(int(t), 0)] for t in d["opp_id"]])
    home = d["home"].to_numpy()
    cup = d["cup"].to_numpy()
    home_cup = home * cup
    per = period_of(d["kickoff"]).to_numpy()
    periods = np.array(sorted(set(per)))
    p_idx = {int(p): i for i, p in enumerate(periods)}
    si = np.array([p_idx[int(p)] for p in per])
    ns = len(periods)
    y = d[col].to_numpy(dtype=float)
    age = (asof - d["kickoff"]).dt.total_seconds().to_numpy() / 86400.0
    w = 0.5 ** (age / half_life_days)
    w = w / w.mean()

    # parameter layout: b0, b_home, b_cup, b_home_cup, lf[nl], la[nl], tf[nt], ta[nt], period[ns]
    o_lf, o_la, o_tf, o_ta = 4, 4 + nl, 4 + 2 * nl, 4 + 2 * nl + nt
    o_s = 4 + 2 * nl + 2 * nt
    npar = o_s + ns
    pen = np.zeros(npar)
    pen[3] = 10.0
    pen[o_lf:o_tf] = ridge_league
    pen[o_tf:o_s] = ridge_team
    pen[o_s:] = 1.0

    def eta_of(x):
        return (x[0] + x[1] * home + x[2] * cup + x[3] * home_cup + x[o_lf + tl] + x[o_la + ol]
                + x[o_tf + ti] + x[o_ta + oi] + x[o_s + si])

    def fun(x):
        eta = eta_of(x)
        mu = np.exp(eta)
        e = x[o_s:o_s + ns]
        step = np.diff(e)
        f = np.sum(w * (mu - y * eta)) + 0.5 * np.sum(pen * x * x) + 0.5 * fuse_periods * np.sum(step * step)
        g_eta = w * (mu - y)
        g = pen * x
        g[0] += g_eta.sum()
        g[1] += (g_eta * home).sum()
        g[2] += (g_eta * cup).sum()
        g[3] += (g_eta * home_cup).sum()
        g[o_lf:o_lf + nl] += np.bincount(tl, g_eta, nl)
        g[o_la:o_la + nl] += np.bincount(ol, g_eta, nl)
        g[o_tf:o_tf + nt] += np.bincount(ti, g_eta, nt)
        g[o_ta:o_ta + nt] += np.bincount(oi, g_eta, nt)
        g[o_s:o_s + ns] += np.bincount(si, g_eta, ns)
        if ns > 1:
            g[o_s + 1:o_s + ns] += fuse_periods * step
            g[o_s:o_s + ns - 1] -= fuse_periods * step
        return f, g

    x0 = np.zeros(npar)
    x0[0] = math.log(max(np.average(y, weights=w), 0.05))
    res = optimize.minimize(fun, x0, jac=True, method="L-BFGS-B", options={"maxiter": 2000})
    x = res.x
    mu = np.exp(eta_of(x))
    alpha = _fit_alpha(y, mu, w)
    # Cup matches spread more around their mean (rotation, mismatches): their own dispersion when
    # there are enough of them, the league one otherwise.
    cup_rows = cup == 1
    alpha_cup = _fit_alpha(y[cup_rows], mu[cup_rows], w[cup_rows]) if cup_rows.sum() >= MIN_CUP_ROWS else None
    # totals: one row per match (home row + away row)
    d = d.assign(mu=mu, w=w)
    tot = d.groupby("fixture_id").agg(y=(col, "sum"), mu=("mu", "sum"), w=("w", "first"), n=("mu", "size"),
                                      cup=("cup", "first"))
    tot = tot[tot["n"] == 2]
    alpha_total = _fit_alpha(tot["y"].to_numpy(float), tot["mu"].to_numpy(float), tot["w"].to_numpy(float))
    tc = tot[tot["cup"] == 1]
    alpha_total_cup = (_fit_alpha(tc["y"].to_numpy(float), tc["mu"].to_numpy(float), tc["w"].to_numpy(float))
                       if len(tc) >= MIN_CUP_ROWS // 2 else None)
    return StatFit(
        stat=stat, b0=float(x[0]), b_home=float(x[1]), b_cup=float(x[2]), b_season=float(x[o_s + ns - 1]),
        b_home_cup=float(x[3]),
        period_effects={f"{int(p) // 2}H{int(p) % 2 + 1}": float(x[o_s + i]) for p, i in p_idx.items()},
        league_for={int(l): float(x[o_lf + i]) for l, i in l_idx.items()},
        league_against={int(l): float(x[o_la + i]) for l, i in l_idx.items()},
        team_for={int(t): float(x[o_tf + i]) for t, i in t_idx.items()},
        team_against={int(t): float(x[o_ta + i]) for t, i in t_idx.items()},
        alpha=alpha, alpha_total=alpha_total, n_rows=len(d), leagues=leagues,
        alpha_cup=alpha_cup, alpha_total_cup=alpha_total_cup,
    )


def nb_over(mu: float, alpha: float, line: float) -> tuple[float, float, float]:
    """(P(X > line), P(X == line) for whole lines, P(X < line)) under NB2(mu, alpha)."""
    r = 1.0 / alpha
    dist = stats.nbinom(r, r / (r + mu))
    if float(line).is_integer():
        k = int(line)
        push = float(dist.pmf(k))
        over = float(dist.sf(k))
        return over, push, max(0.0, 1.0 - over - push)
    over = float(dist.sf(math.floor(line)))
    return over, 0.0, 1.0 - over
