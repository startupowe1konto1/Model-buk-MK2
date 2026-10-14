"""Team-statistics model: expected shots, shots on target, corners, fouls, cards and offsides per team.

For every statistic one ridge-penalised Poisson regression is fitted on all team-rows (two per
match) of the European top flights and the UEFA cups::

    log mu(team vs opp) = b0 + b_home * home + b_cup * cup + b_season[season]
                          + league_for[league of team] + league_against[league of opp]
                          + team_for[team] + team_against[opp]

``b_season`` follows the level of the statistic from season to season (the provider's shot counts
rose by about a tenth in 2026); forecasts use the latest season's level.
``team_for`` is how much a team produces of the statistic (shots it takes, fouls it commits),
``team_against`` how much it concedes to opponents.  League effects let a cup match compare teams
from different leagues; team effects are shrunk towards their league (ridge), more strongly when
the team has few matches.  Older matches count less (half-life in days).  The spread around the
mean is a negative binomial (NB2) with one dispersion per statistic for team counts and one for
match totals, both fitted by maximum likelihood on the same weighted rows.

Data: the read-only snapshot history plus ``<mk2>/stats/gap.sqlite`` (newer matches).  Missing
values: API-Football reports some zero counts as null, so in a league where a statistic is
mostly present a null in a row that has other statistics is read as 0; in a league where the
statistic is mostly absent the rows are skipped.
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

COLUMNS = ["total_shots", "shots_on_goal", "corners", "fouls", "yellow_cards", "red_cards", "offsides"]


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + path.as_posix() + "?mode=ro&immutable=1", uri=True)


def load_rows(league_ids: list[int], first_season: int = 2024) -> pd.DataFrame:
    """One row per team per finished match with statistics: snapshot history plus the gap database."""
    cols = ", ".join(f"s.{c}" for c in COLUMNS)
    q = f"""select m.fixture_id, m.league_id, m.season, m.kickoff_utc, m.referee,
                   m.home_team_id, m.home_team, m.away_team_id, m.away_team,
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
    # zero counts reported as null: fill where the league usually has the statistic
    has_any = df[["total_shots", "corners"]].notna().any(axis=1)
    for c in COLUMNS:
        share = df.groupby("league_id")[c].transform(lambda s: s.notna().mean())
        # cards: a null is the normal way the provider reports none (most matches have no red card)
        fill = df[c].isna() & has_any & ((share >= 0.7) | (c in ("yellow_cards", "red_cards")))
        df.loc[fill, c] = 0
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
    b_season: float
    league_for: dict[int, float]
    league_against: dict[int, float]
    team_for: dict[int, float]
    team_against: dict[int, float]
    alpha: float
    alpha_total: float
    n_rows: int
    leagues: dict[int, int] = field(default_factory=dict)

    def mean(self, team: int, opp: int, home: bool, cup: bool) -> float:
        lt, lo = self.leagues.get(team, 0), self.leagues.get(opp, 0)
        eta = (self.b0 + self.b_home * home + self.b_cup * cup + self.b_season
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


def fit_stat(df: pd.DataFrame, stat: str, asof: pd.Timestamp, half_life_days: float = 365.0,
             ridge_team: float = 30.0, ridge_league: float = 3.0, leagues: dict[int, int] | None = None) -> StatFit:
    col = STATS[stat]
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
    seasons = np.array(sorted(d["season"].unique()))
    s_idx = {int(s): i for i, s in enumerate(seasons)}
    si = d["season"].map(lambda s: s_idx[int(s)]).to_numpy()
    ns = len(seasons)
    y = d[col].to_numpy(dtype=float)
    age = (asof - d["kickoff"]).dt.total_seconds().to_numpy() / 86400.0
    w = 0.5 ** (age / half_life_days)
    w = w / w.mean()

    # parameter layout: b0, b_home, b_cup, lf[nl], la[nl], tf[nt], ta[nt], season[ns]
    o_lf, o_la, o_tf, o_ta = 3, 3 + nl, 3 + 2 * nl, 3 + 2 * nl + nt
    o_s = 3 + 2 * nl + 2 * nt
    npar = o_s + ns
    pen = np.zeros(npar)
    pen[o_lf:o_tf] = ridge_league
    pen[o_tf:o_s] = ridge_team
    pen[o_s:] = 1.0

    def eta_of(x):
        return (x[0] + x[1] * home + x[2] * cup + x[o_lf + tl] + x[o_la + ol] + x[o_tf + ti] + x[o_ta + oi]
                + x[o_s + si])

    def fun(x):
        eta = eta_of(x)
        mu = np.exp(eta)
        f = np.sum(w * (mu - y * eta)) + 0.5 * np.sum(pen * x * x)
        g_eta = w * (mu - y)
        g = pen * x
        g[0] += g_eta.sum()
        g[1] += (g_eta * home).sum()
        g[2] += (g_eta * cup).sum()
        g[o_lf:o_lf + nl] += np.bincount(tl, g_eta, nl)
        g[o_la:o_la + nl] += np.bincount(ol, g_eta, nl)
        g[o_tf:o_tf + nt] += np.bincount(ti, g_eta, nt)
        g[o_ta:o_ta + nt] += np.bincount(oi, g_eta, nt)
        g[o_s:o_s + ns] += np.bincount(si, g_eta, ns)
        return f, g

    x0 = np.zeros(npar)
    x0[0] = math.log(max(np.average(y, weights=w), 0.05))
    res = optimize.minimize(fun, x0, jac=True, method="L-BFGS-B", options={"maxiter": 2000})
    x = res.x
    mu = np.exp(eta_of(x))
    alpha = _fit_alpha(y, mu, w)
    # totals: one row per match (home row + away row)
    d = d.assign(mu=mu, w=w)
    tot = d.groupby("fixture_id").agg(y=(col, "sum"), mu=("mu", "sum"), w=("w", "first"), n=("mu", "size"))
    tot = tot[tot["n"] == 2]
    alpha_total = _fit_alpha(tot["y"].to_numpy(float), tot["mu"].to_numpy(float), tot["w"].to_numpy(float))
    return StatFit(
        stat=stat, b0=float(x[0]), b_home=float(x[1]), b_cup=float(x[2]), b_season=float(x[o_s + ns - 1]),
        league_for={int(l): float(x[o_lf + i]) for l, i in l_idx.items()},
        league_against={int(l): float(x[o_la + i]) for l, i in l_idx.items()},
        team_for={int(t): float(x[o_tf + i]) for t, i in t_idx.items()},
        team_against={int(t): float(x[o_ta + i]) for t, i in t_idx.items()},
        alpha=alpha, alpha_total=alpha_total, n_rows=len(d), leagues=leagues,
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
