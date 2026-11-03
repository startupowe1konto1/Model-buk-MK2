"""Player shots and shots on target for bet-builder legs ("Lautaro: co najmniej 1 celny strzał").

A player's count is his share of the team's count: given the team's simulated shots on target N in a match,
his shots on target are Binomial(N, s), where s is his share of the team's shots on target while he is on
the pitch, scaled by the minutes he usually plays.  This keeps a player leg consistent with the team legs of
the same copula draw (a team that shoots more gives its striker more chances).  Shares come from the team's
last LAST matches, shrunk toward the position's share (PRIOR_WEIGHT team shots of evidence), and only
players who started at least MIN_STARTS of the team's last 5 matches are offered (bookmakers void a player
bet when he does not play; a starter almost always plays).

Data: the snapshot's player_match_stats (to 2026-09-30) and the gap database's player_match_stats, filled
from every fixture fetched by id (stats_daily step_results; backfill_from_raw for the archived responses).
A player listed with minutes but no shots has NULL shots in the API: that is 0.

Usage (from app/), the walk-forward check:  .venv\\Scripts\\python scripts\\mk2\\stats_players.py --since 2026-09-01
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import stats_model as sm  # noqa: E402
from model_buk.ingestion.api_football_tables import PLAYER_MATCH_STATS_COLUMNS, parse_player_match_stats  # noqa: E402

LAST = 10                 # team matches the shares are measured on
MIN_STARTS = 4            # of the team's last 5 matches
PRIOR_WEIGHT = {"sot": 6.0, "shots": 15.0}
POS_PRIOR = {"F": {"sot": 0.22, "shots": 0.20}, "M": {"sot": 0.09, "shots": 0.10}, "D": {"sot": 0.04, "shots": 0.06},
             "G": {"sot": 0.0, "shots": 0.0}}
PER_TEAM = 2              # players offered per team
# the share varies from match to match (role, opponent, minutes): the player's count given the team's is
# beta-binomial; kappa fitted on 7.9k appearances (2026-08-15..10-29) was 8.9 for '>= 1 shot on target'
# (calibrated 0.19/0.38/0.52/0.67/0.82 -> 0.20/0.37/0.53/0.63/0.79); 7.5 keeps the top a little lower
KAPPA_SOT = 7.5
STATS = {"sot": "shots_on", "shots": "shots_total"}
TEAM_COL = {"sot": "shots_on_goal", "shots": "total_shots"}


def gap_path() -> Path:
    return sm.OUT / "stats" / "gap.sqlite"


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(f"create table if not exists player_match_stats ({', '.join(PLAYER_MATCH_STATS_COLUMNS)}, "
                "primary key (fixture_id, player_id))")


def store_players(con: sqlite3.Connection, fixture: dict) -> int:
    """The players of one finished fixture (an API fixture object with the 'players' block) into the gap DB."""
    rows = [r for r in parse_player_match_stats(fixture) if r.get("player_id") is not None]
    if not rows:
        return 0
    ensure_table(con)
    con.executemany(f"insert or replace into player_match_stats values ({','.join('?' * len(PLAYER_MATCH_STATS_COLUMNS))})",
                    [tuple(r.get(c) for c in PLAYER_MATCH_STATS_COLUMNS) for r in rows])
    return len(rows)


def backfill_from_raw(raw_dir: Path | None = None) -> int:
    """The archived by-id responses (stats/raw/*.json.gz) of finished fixtures into the gap DB's player table."""
    raw_dir = raw_dir or sm.OUT / "stats" / "raw"
    con = sqlite3.connect(gap_path())
    ensure_table(con)
    n = 0
    for f in sorted(raw_dir.glob("*.json.gz")):
        try:
            data = json.load(gzip.open(f, "rt", encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fx in data.get("response", []):
            if isinstance(fx, dict) and fx.get("players") and (fx.get("fixture") or {}).get("status", {}).get("short") in ("FT", "AET", "PEN"):
                n += store_players(con, fx)
    con.commit()
    con.close()
    return n


def load_players(league_ids: list[int], since: str = "2025-07-01") -> pd.DataFrame:
    """Player rows (fixture, team, player, minutes, started, position, shots, shots on target) of the model's
    competitions since ``since``, snapshot first, gap DB for what the snapshot does not have."""
    frames = []
    snap = sm.DATA / "api-football" / "tables" / "history.sqlite"
    q = ("select p.fixture_id, p.team_id, p.player_id, p.player_name, p.minutes, p.substitute, p.position, "
         "p.shots_total, p.shots_on from player_match_stats p join matches m using(fixture_id) "
         f"where m.kickoff_utc >= ? and m.league_id in ({','.join('?' * len(league_ids))})")
    if snap.exists():
        con = sqlite3.connect("file:" + snap.as_posix() + "?mode=ro&immutable=1", uri=True)
        frames.append(pd.read_sql(q, con, params=[since, *league_ids]))
        con.close()
    gp = gap_path()
    if gp.exists():
        con = sqlite3.connect("file:" + gp.as_posix() + "?mode=ro", uri=True)
        ok = con.execute("select name from sqlite_master where name = 'player_match_stats'").fetchone()
        if ok:
            frames.append(pd.read_sql("select fixture_id, team_id, player_id, player_name, minutes, substitute, position, "
                                      "shots_total, shots_on from player_match_stats", con))
        con.close()
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True).drop_duplicates(["fixture_id", "player_id"], keep="first")
    d = d[d["minutes"].fillna(0) > 0].copy()
    for c in ("shots_total", "shots_on"):
        d[c] = d[c].fillna(0.0).astype(float)
    d["started"] = d["substitute"].fillna(0).astype(int) == 0
    d["position"] = d["position"].fillna("M")
    return d


def squad(players: pd.DataFrame, teams: pd.DataFrame, team_id: int, before: pd.Timestamp) -> list[dict]:
    """Everyone who played in the team's last 5 matches: starts (s5), average minutes over the 5 (m5, 0 when
    he did not play) and his share of the team's shots and shots on target over the last LAST matches (while on
    the pitch, scaled by his usual minutes; no prior).  The usual eleven are the first 11 by (s5, m5): the line-up
    check before kick-off counts how many of them start, and an injured player's weight in the team shows."""
    tm = teams[(teams["team_id"] == team_id) & (teams["kickoff"] < before) & teams["total_shots"].notna()]
    tm = tm.sort_values("kickoff").tail(LAST)
    if len(tm) < 3:
        return []
    fids = list(tm["fixture_id"])
    last5 = fids[-5:]
    pl = players[(players["team_id"] == team_id) & players["fixture_id"].isin(fids)]
    team_counts = tm.set_index("fixture_id")
    out = []
    for pid, g in pl.groupby("player_id"):
        g5 = g[g["fixture_id"].isin(last5)]
        if g5.empty:
            continue
        frac = (g["minutes"].clip(upper=90).astype(float) / 90.0).to_numpy()
        rec = {"id": int(pid), "n": str(g["player_name"].iloc[-1]), "pos": str(g["position"].mode().iloc[0])[:1].upper() or "M",
               "s5": int(g5["started"].sum()), "m5": round(float(g5["minutes"].clip(upper=90).sum()) / len(last5), 1)}
        usual = min(float(g["minutes"].clip(upper=90).mean()), 90.0) / 90.0
        for s, col in STATS.items():
            team_tot = (team_counts.loc[g["fixture_id"], TEAM_COL[s]].to_numpy(float) * frac).sum()
            rec[f"s_{s}"] = round(float(g[col].sum()) / team_tot * usual, 3) if team_tot > 0 else 0.0
        out.append(rec)
    out.sort(key=lambda r: (-r["s5"], -r["m5"]))
    return out


def profiles(players: pd.DataFrame, teams: pd.DataFrame, team_id: int, before: pd.Timestamp,
             exclude: set[int] | None = None) -> list[dict]:
    """The team's likely starters with their shares of the team's shots and shots on target, without the
    players in ``exclude`` (injured, suspended or doubtful for the match).

    ``teams``: the team-statistics rows (sm.load_rows) with fixture_id, team_id, kickoff and the count columns."""
    tm = teams[(teams["team_id"] == team_id) & (teams["kickoff"] < before) & teams["total_shots"].notna()]
    tm = tm.sort_values("kickoff").tail(LAST)
    if len(tm) < 3:
        return []
    fids = list(tm["fixture_id"])
    last5 = set(fids[-5:])
    pl = players[(players["team_id"] == team_id) & players["fixture_id"].isin(fids)]
    if pl.empty:
        return []
    team_counts = tm.set_index("fixture_id")
    out = []
    for pid, g in pl.groupby("player_id"):
        starts5 = int(g[g["fixture_id"].isin(last5)]["started"].sum())
        if starts5 < MIN_STARTS or int(pid) in (exclude or set()):
            continue
        pos = str(g["position"].mode().iloc[0])[:1].upper() or "M"
        if pos == "G":
            continue
        mins = g["minutes"].clip(upper=90).astype(float)
        frac = mins / 90.0
        rec = {"id": int(pid), "name": str(g["player_name"].iloc[-1]), "pos": pos, "starts5": starts5,
               "min_avg": round(float(g[g["started"]]["minutes"].clip(upper=90).mean() if g["started"].any() else mins.mean()), 1)}
        for s, col in STATS.items():
            team_tot = (team_counts.loc[g["fixture_id"], TEAM_COL[s]].to_numpy(float) * frac.to_numpy()).sum()
            own = float(g[col].sum())
            prior = POS_PRIOR.get(pos, POS_PRIOR["M"])[s]
            share = (own + PRIOR_WEIGHT[s] * prior) / (team_tot + PRIOR_WEIGHT[s])
            rec[f"s_{s}"] = round(float(share * min(rec["min_avg"], 90) / 90.0), 4)   # share of the team's full-match count
            rec[f"n_{s}"] = int(own)
        out.append(rec)
    out.sort(key=lambda r: -r["s_sot"])
    return out[:PER_TEAM]


def at_least_one(share: float, kappa: float = KAPPA_SOT, nmax: int = 40) -> np.ndarray:
    """P(player >= 1 | the team has N) for N = 0..nmax, the share beta-distributed (mean share, kappa)."""
    from scipy import special
    a, b = max(share, 1e-4) * kappa, max(1 - share, 1e-4) * kappa
    n = np.arange(nmax + 1)
    return 1.0 - np.exp(special.betaln(a, b + n) - special.betaln(a, b))


def player_lookup(fixture_ids: set[int]) -> dict[tuple[int, int], tuple[float, float, float]]:
    """(fixture, player) -> (minutes, shots, shots on target) of finished fixtures, for the settlement."""
    out: dict[tuple[int, int], tuple[float, float, float]] = {}
    if not fixture_ids:
        return out
    ids = sorted(fixture_ids)
    for path, mode in ((sm.DATA / "api-football" / "tables" / "history.sqlite", "?mode=ro&immutable=1"), (gap_path(), "?mode=ro")):
        if not path.exists():
            continue
        con = sqlite3.connect("file:" + path.as_posix() + mode, uri=True)
        if not con.execute("select name from sqlite_master where name = 'player_match_stats'").fetchone():
            con.close()
            continue
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for fid, pid, mins, sh, so in con.execute(
                    f"select fixture_id, player_id, minutes, shots_total, shots_on from player_match_stats "
                    f"where fixture_id in ({','.join('?' * len(chunk))})", chunk):
                if pid is not None and (fid, pid) not in out:
                    out[(int(fid), int(pid))] = (float(mins or 0), float(sh or 0), float(so or 0))
        con.close()
    return out


def chance_at_least(k: int, share: float, mu: float, alpha: float, kmax: int = 40) -> float:
    """P(player >= k) when the team's count is NB2(mu, alpha) and the player takes each with chance ``share``."""
    r = 1.0 / max(alpha, 1e-6)
    n = np.arange(kmax + 1)
    pn = stats.nbinom.pmf(n, r, r / (r + mu))
    return float((pn * stats.binom.sf(k - 1, n, share)).sum())


def main() -> None:
    from stats_config import MODEL_LEAGUES
    p = argparse.ArgumentParser(description="walk-forward check of the player model")
    p.add_argument("--since", default="2026-09-01")
    args = p.parse_args()
    n = backfill_from_raw()
    print(f"backfill: {n} player rows from archived responses")
    leagues = list(MODEL_LEAGUES)
    teams = sm.fill_known_zeros(sm.load_rows(leagues))
    players = load_players(leagues)
    print(f"players: {len(players)} rows, {players['fixture_id'].nunique()} matches")
    test = teams[(teams["kickoff"] >= pd.Timestamp(args.since, tz="UTC")) & teams["shots_on_goal"].notna()]
    rows = []
    for (fid, team), g in test.groupby(["fixture_id", "team_id"]):
        ko = g["kickoff"].iloc[0]
        prof = profiles(players, teams, int(team), ko)
        if not prof:
            continue
        actual = players[(players["fixture_id"] == fid) & (players["team_id"] == team)].set_index("player_id")
        team_sot, team_sh = float(g["shots_on_goal"].iloc[0]), float(g["total_shots"].iloc[0])
        hist = teams[(teams["team_id"] == team) & (teams["kickoff"] < ko)].sort_values("kickoff").tail(LAST)
        mu_sot, mu_sh = float(hist["shots_on_goal"].mean()), float(hist["total_shots"].mean())
        for r in prof:
            played = r["id"] in actual.index
            started = played and bool(actual.loc[r["id"], "started"]) if played else False
            row = {"fixture_id": fid, "player": r["name"], "pos": r["pos"], "played": played, "started": started,
                   "s_sot": r["s_sot"], "s_sh": r["s_shots"]}
            if played:
                a = actual.loc[r["id"]]
                row.update({"sot": float(a["shots_on"]), "shots": float(a["shots_total"]), "min": float(a["minutes"])})
                # conditional on the team's actual count (the share model alone) and with the team's recent mean
                row["p1_given"] = float(1 - (1 - r["s_sot"]) ** team_sot)
                row["p1"] = chance_at_least(1, r["s_sot"], mu_sot, 0.03)
                row["p2s"] = chance_at_least(2, r["s_shots"], mu_sh, 0.04)
            rows.append(row)
    d = pd.DataFrame(rows)
    print(f"offered players: {len(d)}, played {d['played'].mean():.3f}, started {d['started'].mean():.3f}")
    pl = d[d["played"]]
    for col, hit, name in (("p1_given", pl["sot"] >= 1, "≥1 SOT | team SOT"), ("p1", pl["sot"] >= 1, "≥1 SOT"), ("p2s", pl["shots"] >= 2, "≥2 shots")):
        bins = pd.cut(pl[col], [0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
        g = pd.DataFrame({"p": pl[col], "y": hit.astype(float), "b": bins}).groupby("b", observed=True).agg(n=("y", "size"), p=("p", "mean"), y=("y", "mean"))
        print(f"\n{name}: overall model {pl[col].mean():.3f} actual {hit.mean():.3f} (n {len(pl)})")
        print(g.round(3).to_string())


if __name__ == "__main__":
    main()
