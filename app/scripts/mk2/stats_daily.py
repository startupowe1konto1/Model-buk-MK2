"""Daily run of the MK2 statistics model: results, calendar, odds, forecasts, picks, dashboard data.

Steps (all on by default; each can be skipped):

1. calendar  -- /fixtures?date=D for the last ``--back`` days and the next ``--days`` days (one request
               per day for all competitions), kept in ``<mk2>/stats/fixtures.sqlite``.
2. results   -- finished fixtures of the model's competitions whose team statistics are not in the
               snapshot or ``stats/gap.sqlite`` yet: /fixtures?ids= (20 per request) into the gap DB.
3. odds      -- /odds?fixture=ID for fixtures of the shown competitions starting within ``--odds-hours``;
               a fixture is refetched after ``--odds-refresh`` hours, or every run in its last 3 hours.
               Statistics markets go to ``stats/odds.sqlite`` with the fetch time (an archive for
               checking the model against the market later).
4. forecast  -- one fit per statistic on all finished matches; expected counts for every upcoming
               fixture saved in ``stats/predictions.sqlite`` with the time they were made.
5. picks     -- the market-aligned candidates (stats_picks) of each day; the top ones are logged as
               paper picks in ``stats/predictions.sqlite`` (table picks) and settled later.
6. export    -- JSON for the dashboard in ``--dest`` (matches, teams, picks, track record, meta).

The API quota is shared with Oskar's collectors: a run stops spending at ``--max-requests`` or when
the account has ``--reserve`` requests left.  The key is read from the Windows user environment.

Usage (from app/):
    .venv\\Scripts\\python scripts\\mk2\\stats_daily.py
    .venv\\Scripts\\python scripts\\mk2\\stats_daily.py --skip odds --dest dashboard_mk2/app/data
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import stats_model as sm  # noqa: E402
import stats_picks as sp  # noqa: E402
from model_buk.ingestion.api_football_tables import (  # noqa: E402
    MATCH_COLUMNS,
    TEAM_MATCH_STATS_COLUMNS,
    parse_match,
    parse_team_match_stats,
)
from stats_config import CUPS, EXTRA, MODEL_LEAGUES, SHORT, SHOWN, TOP15  # noqa: E402
from stats_gap import Client, api_key, open_gap_db  # noqa: E402
from stats_today import parse_odds  # noqa: E402

FINISHED = {"FT", "AET", "PEN"}
NOT_STARTED = {"NS", "TBD"}
DEAD = {"PST", "CANC", "ABD", "AWD", "WO"}
STATS_DIR = sm.OUT / "stats"
PICK_LIMIT_PER_DAY = 5


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------- storage

def open_db(name: str) -> sqlite3.Connection:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATS_DIR / name)
    con.row_factory = sqlite3.Row
    return con


def init_dbs() -> tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection]:
    fx = open_db("fixtures.sqlite")
    fx.execute("""create table if not exists fixtures (
        fixture_id integer primary key, league_id integer, season integer, round text, kickoff_utc text,
        status text, referee text, home_id integer, home text, away_id integer, away text,
        home_goals integer, away_goals integer, venue text, fetched_at text)""")
    od = open_db("odds.sqlite")
    od.execute("""create table if not exists odds (
        fetched_at text, fixture_id integer, bookmaker text, stat text, scope text, line real, side text,
        odd real, primary key (fetched_at, fixture_id, bookmaker, stat, scope, line, side))""")
    od.execute("create table if not exists odds_fetch (fixture_id integer, fetched_at text, n_rows integer, "
               "primary key (fixture_id, fetched_at))")
    pr = open_db("predictions.sqlite")
    pr.execute("""create table if not exists predictions (
        made_at text, fixture_id integer, stat text, mu_home real, mu_away real, alpha real, alpha_total real,
        k_home real, k_away real, k_total real, primary key (made_at, fixture_id, stat))""")
    pr.execute("""create table if not exists picks (
        made_at text, day text, fixture_id integer, stat text, scope text, line real, side text,
        p_model real, p_market real, odd real, bookmaker text, ev real, ev_tax real, rank integer,
        primary key (day, fixture_id, stat, scope, line, side))""")
    for con in (fx, od, pr):
        con.commit()
    return fx, od, pr


# ---------------------------------------------------------------------------- 1. calendar

def step_calendar(client: Client, fx: sqlite3.Connection, back: int, days: int) -> int:
    today = dt.date.today()
    n = 0
    now = iso(utcnow())
    for offset in range(-back, days + 1):
        day = (today + dt.timedelta(days=offset)).isoformat()
        data = client.get("/fixtures", {"date": day})
        rows = []
        for f in data.get("response", []):
            lid = f["league"]["id"]
            if lid not in MODEL_LEAGUES:
                continue
            fxb, t, g = f["fixture"], f["teams"], f.get("goals") or {}
            rows.append((fxb["id"], lid, f["league"]["season"], f["league"].get("round"), fxb["date"],
                         fxb["status"]["short"], fxb.get("referee"), t["home"]["id"], t["home"]["name"],
                         t["away"]["id"], t["away"]["name"], g.get("home"), g.get("away"),
                         (fxb.get("venue") or {}).get("name"), now))
        fx.executemany("insert or replace into fixtures values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        fx.commit()
        n += len(rows)
        print(f"calendar {day}: {len(rows)} fixtures in the model's competitions")
    return n


# ---------------------------------------------------------------------------- 2. results

def have_stats(ids: list[int]) -> set[int]:
    out: set[int] = set()
    snapshot = sm.DATA / "api-football" / "tables" / "history.sqlite"
    for path in (snapshot, STATS_DIR / "gap.sqlite"):
        if not path.exists():
            continue
        # the snapshot is never written: immutable keeps SQLite from touching even its -wal/-shm files
        mode = "?mode=ro&immutable=1" if path == snapshot else "?mode=ro"
        con = sqlite3.connect("file:" + path.as_posix() + mode, uri=True)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"select distinct fixture_id from team_match_stats where fixture_id in ({','.join('?' * len(chunk))})"
            out.update(r[0] for r in con.execute(q, chunk))
        con.close()
    return out


def step_results(client: Client, fx: sqlite3.Connection) -> int:
    cutoff = iso(utcnow() - dt.timedelta(hours=2))
    rows = fx.execute("select fixture_id, league_id from fixtures where status in ('FT','AET','PEN') "
                      "and kickoff_utc < ?", (cutoff,)).fetchall()
    ids = [r["fixture_id"] for r in rows]
    todo = [i for i in ids if i not in have_stats(ids)]
    print(f"results: {len(ids)} finished, {len(todo)} without statistics yet")
    if not todo:
        return 0
    gap = open_gap_db(STATS_DIR / "gap.sqlite")
    raw = STATS_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    codes = {r["fixture_id"]: SHORT.get(r["league_id"], str(r["league_id"])) for r in rows}
    added = 0
    for n, i in enumerate(range(0, len(todo), 20)):
        chunk = todo[i:i + 20]
        data = client.get("/fixtures", {"ids": "-".join(map(str, chunk))})
        name = f"daily_ids_{stamp}_{n:03d}.json.gz"
        with gzip.open(raw / name, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        ms, ss = [], []
        for f in data.get("response", []):
            fid = f["fixture"]["id"]
            stats_rows = parse_team_match_stats(f)
            if len(stats_rows) < 2:      # provider has not filled the statistics yet: try again next run
                continue
            ms.append(parse_match(f, league_code=codes.get(fid), fetched_at=iso(utcnow()), batch_file=name))
            ss.extend(stats_rows)
        gap.executemany(f"insert or replace into matches values ({','.join('?' * len(MATCH_COLUMNS))})",
                        [tuple(r[c] for c in MATCH_COLUMNS) for r in ms])
        gap.executemany(f"insert or replace into team_match_stats values "
                        f"({','.join('?' * len(TEAM_MATCH_STATS_COLUMNS))})",
                        [tuple(r[c] for c in TEAM_MATCH_STATS_COLUMNS) for r in ss])
        gap.commit()
        added += len(ms)
    gap.close()
    print(f"results: {added} matches with statistics added")
    return added


# ---------------------------------------------------------------------------- 3. odds

def step_odds(client: Client, fx: sqlite3.Connection, od: sqlite3.Connection, hours: float,
              refresh_hours: float, limit: int) -> int:
    now = utcnow()
    upto = iso(now + dt.timedelta(hours=hours))
    rows = fx.execute("select fixture_id, kickoff_utc, league_id from fixtures where status in ('NS','TBD') "
                      "and kickoff_utc > ? and kickoff_utc < ? order by kickoff_utc", (iso(now), upto)).fetchall()
    rows = [r for r in rows if r["league_id"] in SHOWN]
    last = {r[0]: r[1] for r in od.execute("select fixture_id, max(fetched_at) from odds_fetch group by fixture_id")}
    fetched = 0
    for r in rows:
        if fetched >= limit:
            print(f"odds: limit {limit} reached")
            break
        kick = dt.datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
        prev = last.get(r["fixture_id"])
        if prev:
            age_h = (now - dt.datetime.fromisoformat(prev.replace("Z", "+00:00"))).total_seconds() / 3600
            soon = (kick - now).total_seconds() / 3600 < 3
            if age_h < (0.75 if soon else refresh_hours):
                continue
        try:
            payload = client.get("/odds", {"fixture": r["fixture_id"]})
        except RuntimeError as exc:
            print(f"odds: {exc}")
            break
        stamp = iso(utcnow())
        quotes = parse_odds(payload)
        od.executemany("insert or replace into odds values (?,?,?,?,?,?,?,?)",
                       [(stamp, r["fixture_id"], q["bookmaker"], q["stat"], q["scope"], q["line"], q["side"], q["odd"])
                        for q in quotes])
        od.execute("insert or replace into odds_fetch values (?,?,?)", (r["fixture_id"], stamp, len(quotes)))
        od.commit()
        fetched += 1
    print(f"odds: {fetched} fixtures fetched ({len(rows)} upcoming within {hours:g} h)")
    return fetched


def latest_odds(od: sqlite3.Connection, fixture_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for fid in fixture_ids:
        row = od.execute("select max(fetched_at) from odds where fixture_id = ?", (fid,)).fetchone()
        if not row or not row[0]:
            continue
        out[fid] = [dict(r) for r in od.execute(
            "select bookmaker, stat, scope, line, side, odd, fetched_at from odds where fixture_id = ? and fetched_at = ?",
            (fid, row[0]))]
    return out


# ---------------------------------------------------------------------------- 4. forecast

def team_match_counts(df: pd.DataFrame, now: dt.datetime, days: int = 365) -> dict[int, int]:
    """Matches with shot statistics per team in the last ``days`` days."""
    recent = df[(df["kickoff"] >= pd.Timestamp(now - dt.timedelta(days=days))) & df["total_shots"].notna()]
    return {int(k): int(v) for k, v in recent.groupby("team_id").size().items()}


def analysis_for(fixtures: list[sqlite3.Row], fits: dict, odds: dict[int, list[dict]],
                 team_n: dict[int, int] | None = None) -> dict:
    """The same structure stats_today.py writes, for all upcoming fixtures at once."""
    out = {"mecze": [], "model": {s: {"home": math.exp(f.b_home), "alpha": f.alpha, "alpha_total": f.alpha_total}
                                  for s, f in fits.items()},
           "model_puchary": {s: {"home": math.exp(f.b_home), "alpha": f.dispersion("home", True),
                                 "alpha_total": f.dispersion("total", True)} for s, f in fits.items()}}
    for f in fixtures:
        cup = f["league_id"] in CUPS
        means = {}
        for s, fit in fits.items():
            mh = fit.mean(f["home_id"], f["away_id"], True, cup)
            ma = fit.mean(f["away_id"], f["home_id"], False, cup)
            means[s] = {"home": mh, "away": ma, "total": mh + ma}
        lines = []
        frame = pd.DataFrame(odds.get(f["fixture_id"], []))
        if not frame.empty:
            for (stat, scope, line), g in frame.groupby(["stat", "scope", "line"]):
                fit = fits[stat]
                mu = means[stat][scope]
                p_over, p_push, p_under = sm.nb_over(mu, fit.dispersion(scope, cup), line)
                piv = g.pivot_table(index="bookmaker", columns="side", values="odd", aggfunc="max")
                fair = []
                for bm, r in piv.iterrows():
                    if pd.notna(r.get("over")) and pd.notna(r.get("under")):
                        io, iu = 1 / r["over"], 1 / r["under"]
                        fair.append(io / (io + iu))
                row = {"stat": stat, "scope": scope, "line": float(line), "p_over": p_over, "p_under": p_under,
                       "p_push": p_push, "rynek_over": float(np.mean(fair)) if fair else None}
                for side, p_win in (("over", p_over), ("under", p_under)):
                    if side in piv.columns and piv[side].notna().any():
                        prices = {bm: float(o) for bm, o in piv[side].dropna().items()}
                        best = max(prices, key=prices.get)
                        row[f"kurs_{side}"] = prices[best]
                        row[f"buk_{side}"] = best
                        row[f"kursy_{side}"] = prices
                        row[f"p_{side}"] = p_win
                lines.append(row)
        n_home = (team_n or {}).get(f["home_id"], 0)
        n_away = (team_n or {}).get(f["away_id"], 0)
        out["mecze"].append({"fixture_id": f["fixture_id"], "start": f["kickoff_utc"], "gospodarz": f["home"],
                             "gosc": f["away"], "liga_id": f["league_id"], "srednie": means, "linie": lines,
                             "n_gosp": n_home, "n_gosc": n_away,
                             "min_n": min(n_home, n_away) if team_n is not None else sp.MIN_TEAM_MATCHES})
    return out


def factors_with_fallback(analysis: dict) -> dict[tuple[str, str], float]:
    """Market-alignment factor per (stat, scope); team scopes without lines borrow the total's.

    Factors are clamped to [0.85, 1.2]: a larger gap means the model and the bookmakers count the
    statistic differently, and stretching the model that far would hide it rather than fix it.
    """
    raw = sp.level_factors(analysis)
    out = {}
    for stat in sm.STATS:
        tot = raw.get((stat, "total"), 1.0)
        for scope in ("home", "away", "total"):
            out[(stat, scope)] = float(min(1.2, max(0.85, raw.get((stat, scope), tot))))
    return out


GROUP_MIN_LINES = 20


def group_of(league_id: int) -> str:
    return "puchary" if league_id in CUPS else "ligi"


def group_factors(analysis: dict, pooled: dict) -> dict[str, dict[tuple[str, str], float]]:
    """Alignment factors for the cups and for the leagues separately.

    The market's level differs between them (on 2026-10-14 it expected about 9% more corners than
    the model in the Champions League but 2% more in the leagues).  A group uses its own factor
    where it has at least GROUP_MIN_LINES market lines for that statistic and scope, else the
    group's total-scope factor, else the pooled one.
    """
    out = {}
    for g in ("puchary", "ligi"):
        sub = {"mecze": [m for m in analysis["mecze"] if group_of(m["liga_id"]) == g],
               "model": analysis["model_puchary" if g == "puchary" else "model"]}
        raw = sp.level_factors(sub)
        count: dict[tuple[str, str], int] = {}
        for m in sub["mecze"]:
            for l in m["linie"]:
                if l.get("rynek_over") is not None:
                    count[(l["stat"], l["scope"])] = count.get((l["stat"], l["scope"]), 0) + 1
        fac = {}
        for stat in sm.STATS:
            tot = raw[(stat, "total")] if count.get((stat, "total"), 0) >= GROUP_MIN_LINES else pooled[(stat, "total")]
            for scope in ("home", "away", "total"):
                v = raw[(stat, scope)] if count.get((stat, scope), 0) >= GROUP_MIN_LINES else                     (tot if scope != "total" else pooled[(stat, "total")])
                if scope != "total" and count.get((stat, scope), 0) < GROUP_MIN_LINES and count.get((stat, "total"), 0) < GROUP_MIN_LINES:
                    v = pooled[(stat, scope)]
                fac[(stat, scope)] = float(min(1.2, max(0.85, v)))
        out[g] = fac
    return out


# ---------------------------------------------------------------------------- 6. export helpers

def team_profiles(df: pd.DataFrame, team_ids: set[int], fits: dict, leagues: dict[int, int]) -> dict:
    """Season averages for/against (domestic league, current season), ranks in the league, last 5."""
    stats = ("shots", "sot", "corners", "fouls", "cards", "offsides")
    cols = {s: sm.STATS[s] for s in stats}
    opp = df.set_index(["fixture_id", "team_id"])
    out = {}
    cur = df[df["cup"] == 0]
    latest_season = cur.groupby("league_id")["season"].transform("max")
    cur = cur[cur["season"] == latest_season]
    agg_rows = []
    for (lid, tid), g in cur.groupby(["league_id", "team_id"]):
        rec = {"league_id": lid, "team_id": tid, "n": len(g)}
        for s, c in cols.items():
            rec[f"{s}_for"] = g[c].mean()
            against = [v for v in (opp.loc[(f, o)][c] for f, o in zip(g["fixture_id"], g["opp_id"])) if pd.notna(v)]
            rec[f"{s}_against"] = float(np.mean(against)) if against else np.nan
        agg_rows.append(rec)
    agg = pd.DataFrame(agg_rows)
    for tid in team_ids:
        rows_t = df[df["team_id"] == tid].sort_values("kickoff")
        if rows_t.empty:
            continue
        lid = leagues.get(tid, 0)
        prof = {"nazwa": rows_t["team"].iloc[-1], "liga_id": lid, "sezon": {}, "miejsce": {}, "model": {}, "ost5": []}
        a = agg[(agg["league_id"] == lid)] if not agg.empty else agg
        me = a[a["team_id"] == tid] if not a.empty else a
        if not me.empty:
            me = me.iloc[0]
            prof["mecze_w_sezonie"] = int(me["n"])
            for s in stats:
                f_, ag = me[f"{s}_for"], me[f"{s}_against"]
                prof["sezon"][s] = [_r(f_), _r(ag)]
                # 1 = most produced / most conceded in the league
                rank_for = int((a[f"{s}_for"] > f_).sum() + 1) if pd.notna(f_) else None
                rank_against = int((a[f"{s}_against"] > ag).sum() + 1) if pd.notna(ag) else None
                prof["miejsce"][s] = [rank_for, rank_against, int(len(a))]
        for s in stats:
            fit = fits.get(s)
            if fit:
                prof["model"][s] = [_r(math.exp(fit.team_for.get(tid, 0.0) + fit.league_for.get(lid, 0.0)), 3),
                                    _r(math.exp(fit.team_against.get(tid, 0.0) + fit.league_against.get(lid, 0.0)), 3)]
        for _, r in rows_t.tail(5).iloc[::-1].iterrows():
            o = opp.loc[(r["fixture_id"], r["opp_id"])]
            prof["ost5"].append({
                "data": r["kickoff"].strftime("%Y-%m-%d"), "rywal": r["opp"], "dom": bool(r["home"]),
                "rozgr": SHORT.get(int(r["league_id"]), str(r["league_id"])),
                "s": {s: [_r(r[cols[s]], 0), _r(o[cols[s]], 0)] for s in stats},
            })
        out[str(tid)] = prof
    return out


def _r(v, nd: int = 2):
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return None
    return round(float(v), nd)


def highlights(m: dict, teams: dict) -> list[str]:
    """Plain-language reasons: a team that produces a lot against an opponent that concedes a lot."""
    words = {"shots": ("oddaje średnio", "strzałów na mecz", "oddają średnio", "strzałów"),
             "corners": ("wywalcza średnio", "rożnych na mecz", "wywalczają średnio", "rożnych"),
             "fouls": ("fauluje średnio", "razy na mecz", "faulują średnio", "razy"),
             "cards": ("dostaje średnio", "kartek na mecz", "dostają średnio", "kartek")}
    out = []
    for att, deff in (("home", "away"), ("away", "home")):
        ta, td = teams.get(str(m[f"{att}_id"])), teams.get(str(m[f"{deff}_id"]))
        if not ta or not td:
            continue
        for s, (verb, noun, verb2, noun2) in words.items():
            ra, rd = ta["miejsce"].get(s), td["miejsce"].get(s)
            sa, sd = ta["sezon"].get(s), td["sezon"].get(s)
            if not (ra and rd and sa and sd) or None in (ra[0], rd[1], sa[0], sd[1]):
                continue
            top_a = ra[0] <= max(3, round(ra[2] * 0.25))
            top_d = rd[1] <= max(3, round(rd[2] * 0.25))
            if top_a and top_d:
                out.append(f"{ta['nazwa']} {verb} {_pl(sa[0])} {noun} ({ra[0]}. wynik w swojej lidze), "
                           f"a rywale drużyny {td['nazwa']} {verb2} {_pl(sd[1])} {noun2} "
                           f"({rd[1]}. najwyższy wynik w swojej lidze).")
            elif top_a and ra[0] == 1:
                out.append(f"{ta['nazwa']} {verb} {_pl(sa[0])} {noun}, najwięcej w swojej lidze.")
    return out[:6]


def _pl(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


# ---------------------------------------------------------------------------- 7. results and track record

def finished_results(df: pd.DataFrame, days_back: int = 21) -> dict:
    """Actual statistics of recently finished matches of the shown competitions (for settling bets)."""
    cut = pd.Timestamp(utcnow() - dt.timedelta(days=days_back))
    d = df[(df["kickoff"] >= cut) & df["league_id"].isin(list(SHOWN))]
    out = {}
    for fid, g in d.groupby("fixture_id"):
        if len(g) != 2:
            continue
        h = g[g["side"] == "home"].iloc[0]
        a = g[g["side"] == "away"].iloc[0]
        out[str(int(fid))] = {
            "start": h["kickoff"].isoformat(), "liga_id": int(h["league_id"]), "gosp": h["team"], "gosc": a["team"],
            "s": {s: [_r(h[c], 0), _r(a[c], 0)] for s, c in sm.STATS.items()},
        }
    return out


def forecast_track(pr: sqlite3.Connection, df: pd.DataFrame, finished: dict) -> dict:
    """How the last forecast before kickoff compared with what happened, per statistic.

    For each team and statistic the 70% threshold ("at least k") of the aligned forecast is checked:
    if the model is calibrated about 70% of them come true.
    """
    rows = [dict(r) for r in pr.execute("select * from predictions")]
    if not rows:
        return {}
    kick = {int(k): v["start"] for k, v in finished.items()}
    best: dict[tuple[int, str], dict] = {}
    for r in rows:
        k = kick.get(r["fixture_id"])
        if k is None or r["made_at"] >= k.replace("+00:00", "Z"):
            continue
        key = (r["fixture_id"], r["stat"])
        if key not in best or r["made_at"] > best[key]["made_at"]:
            best[key] = r
    per: dict[str, dict] = {}
    for (fid, stat), r in best.items():
        act = finished[str(fid)]["s"].get(stat)
        if not act or None in act:
            continue
        acc = per.setdefault(stat, {"n": 0, "pred": 0.0, "fakt": 0.0, "abs": 0.0, "progi": 0, "trafione": 0})
        for side, mu, k_, y in (("home", r["mu_home"], r["k_home"], act[0]), ("away", r["mu_away"], r["k_away"], act[1])):
            m = mu * (k_ or 1.0)
            acc["n"] += 1
            acc["pred"] += m
            acc["fakt"] += y
            acc["abs"] += abs(m - y)
            k70 = 0
            for k in range(1, 60):
                if sm.nb_over(m, r["alpha"], k - 0.5)[0] >= 0.70:
                    k70 = k
                else:
                    break
            if k70 > 0:
                acc["progi"] += 1
                acc["trafione"] += int(y >= k70)
    out = {}
    for stat, a in per.items():
        n = a["n"]
        out[stat] = {"n": n, "srednia_prognoza": round(a["pred"] / n, 2), "srednia_fakt": round(a["fakt"] / n, 2),
                     "blad_sredni": round(a["abs"] / n, 2), "progi_70": a["progi"],
                     "progi_70_trafione": a["trafione"],
                     "progi_70_proc": round(a["trafione"] / a["progi"], 3) if a["progi"] else None}
    return out


# ---------------------------------------------------------------------------- 8. settle paper picks

def settle_picks(pr: sqlite3.Connection, df: pd.DataFrame) -> list[dict]:
    rows = [dict(r) for r in pr.execute("select * from picks order by day, rank")]
    by_fix = {fid: g for fid, g in df.groupby("fixture_id")}
    out = []
    for p in rows:
        g = by_fix.get(p["fixture_id"])
        res = None
        if g is not None and len(g) == 2:
            col = sm.STATS[p["stat"]]
            h = g[g["side"] == "home"][col].iloc[0]
            a = g[g["side"] == "away"][col].iloc[0]
            if pd.notna(h) and pd.notna(a):
                val = {"home": h, "away": a, "total": h + a}[p["scope"]]
                if val == p["line"]:
                    res = "zwrot"
                elif (val > p["line"]) == (p["side"] == "over"):
                    res = "wygrana"
                else:
                    res = "przegrana"
                p["wartosc"] = float(val)
        p["wynik"] = res
        if res == "wygrana":
            p["zysk"] = round(p["odd"] - 1, 4)
            p["zysk_podatek"] = round(p["odd"] * 0.88 - 1, 4)
        elif res == "przegrana":
            p["zysk"] = p["zysk_podatek"] = -1.0
        elif res == "zwrot":
            p["zysk"] = p["zysk_podatek"] = 0.0
        out.append(p)
    return out


# ---------------------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--days", type=int, default=7, help="calendar days ahead")
    p.add_argument("--back", type=int, default=3, help="calendar days back (results)")
    p.add_argument("--odds-hours", type=float, default=72)
    p.add_argument("--odds-refresh", type=float, default=8, help="hours between odds fetches of a fixture")
    p.add_argument("--odds-limit", type=int, default=200)
    p.add_argument("--max-requests", type=int, default=600)
    p.add_argument("--reserve", type=int, default=3000)
    p.add_argument("--skip", default="", help="comma list: calendar,results,odds,picks,export")
    p.add_argument("--dest", default=str(HERE.parents[1] / "dashboard_mk2" / "app" / "data"))
    p.add_argument("--half-life", type=float, default=180.0)
    args = p.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    fx, od, pr = init_dbs()
    client = Client(api_key(), args.max_requests, args.reserve)
    if "calendar" not in skip:
        step_calendar(client, fx, args.back, args.days)
    if "results" not in skip:
        step_results(client, fx)
    if "odds" not in skip:
        step_odds(client, fx, od, args.odds_hours, args.odds_refresh, args.odds_limit)

    # forecast
    now = utcnow()
    df = sm.load_rows(list(MODEL_LEAGUES))
    leagues = sm.home_league(df)
    asof = pd.Timestamp(now)
    fits = {s: sm.fit_stat(df, s, asof, half_life_days=args.half_life, leagues=leagues) for s in sm.STATS}
    horizon = iso(now + dt.timedelta(days=args.days + 1))
    since = iso(now - dt.timedelta(hours=3))
    upcoming = fx.execute("select * from fixtures where kickoff_utc > ? and kickoff_utc < ? and status not in "
                          "('FT','AET','PEN','PST','CANC','ABD','AWD','WO') order by kickoff_utc",
                          (since, horizon)).fetchall()
    upcoming = [f for f in upcoming if f["league_id"] in MODEL_LEAGUES]
    odds = latest_odds(od, [f["fixture_id"] for f in upcoming])
    analysis = analysis_for(upcoming, fits, odds, team_match_counts(df, now))
    pooled = factors_with_fallback(analysis)
    factors = group_factors(analysis, pooled)
    fac = lambda m: factors[group_of(m["liga_id"])]  # noqa: E731
    made = iso(now)
    pr.executemany("insert or replace into predictions values (?,?,?,?,?,?,?,?,?,?)", [
        (made, m["fixture_id"], s, m["srednie"][s]["home"], m["srednie"][s]["away"],
         fits[s].dispersion("home", m["liga_id"] in CUPS), fits[s].dispersion("total", m["liga_id"] in CUPS),
         fac(m)[(s, "home")], fac(m)[(s, "away")], fac(m)[(s, "total")])
        for m in analysis["mecze"] for s in sm.STATS])
    pr.commit()
    print(f"forecast: {len(upcoming)} upcoming fixtures, {sum(1 for m in analysis['mecze'] if m['linie'])} with odds")

    # picks
    cands = []
    for g, gf in factors.items():
        cands += sp.candidates({"mecze": [m for m in analysis["mecze"] if group_of(m["liga_id"]) == g],
                                "model": analysis["model_puchary" if g == "puchary" else "model"]}, gf)
    for c in cands:
        c["day"] = next(m["start"] for m in analysis["mecze"] if m["fixture_id"] == c["fixture_id"])[:10]
    good = [c for c in cands if c["do_typow"] and sp.MIN_EDGE <= c["przewaga"] <= sp.MAX_EDGE and c["ev"] >= sp.MIN_EV]
    good.sort(key=lambda c: -c["ev"])
    days: dict[str, list[dict]] = {}
    seen = set()
    for c in good:
        key = (c["fixture_id"], c["stat"], c["scope"])
        if key in seen:
            continue
        seen.add(key)
        days.setdefault(c["day"], []).append(c)
    if "picks" not in skip:
        kickoff = {m["fixture_id"]: m["start"] for m in analysis["mecze"]}
        for day, lst in days.items():
            # Picks of matches that have started are frozen (they are what gets settled); the rest of
            # the day's list is replaced by this run's, so the log keeps the last list made before kickoff.
            logged = [dict(r) for r in pr.execute("select fixture_id from picks where day = ?", (day,))]
            frozen = [r for r in logged if kickoff.get(r["fixture_id"], "") <= made or r["fixture_id"] not in kickoff]
            open_ids = [r["fixture_id"] for r in logged if r not in frozen]
            if open_ids:
                pr.execute(f"delete from picks where day = ? and fixture_id in ({','.join('?' * len(open_ids))})",
                           (day, *open_ids))
            fresh = [c for c in lst if kickoff.get(c["fixture_id"], "") > made]
            for rank, c in enumerate(fresh[:max(0, PICK_LIMIT_PER_DAY - len(frozen))], len(frozen) + 1):
                pr.execute("insert or replace into picks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    made, day, c["fixture_id"], c["stat"], c["scope"], c["line"], c["side"], c["p_model"],
                    c["p_rynek"], c["kurs"], c["bukmacher"], c["ev"], c["ev_podatek"], rank))
        pr.commit()

    if "export" not in skip:
        export(Path(args.dest), analysis, factors, fits, df, leagues, upcoming, days, pr, made)
    print(f"requests used {client.used}, account remaining {client.remaining}")


def export(dest: Path, analysis: dict, factors: dict, fits: dict, df: pd.DataFrame, leagues: dict,
           upcoming: list, days: dict, pr: sqlite3.Connection, made: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    by_id = {f["fixture_id"]: f for f in upcoming}
    team_ids = {f["home_id"] for f in upcoming} | {f["away_id"] for f in upcoming}
    teams = team_profiles(df, team_ids, fits, leagues)
    matches = []
    for m in analysis["mecze"]:
        f = by_id[m["fixture_id"]]
        gf = factors[group_of(m["liga_id"])]
        k = {s: [gf[(s, "home")], gf[(s, "away")]] for s in sm.STATS}
        rec = {
            "id": m["fixture_id"], "start": f["kickoff_utc"], "liga_id": f["league_id"],
            "runda": f["round"], "sedzia": f["referee"], "status": f["status"],
            "gosp": f["home"], "gosc": f["away"], "gosp_id": f["home_id"], "gosc_id": f["away_id"],
            "home_id": f["home_id"], "away_id": f["away_id"],
            "m": {s: [round(m["srednie"][s]["home"] * k[s][0], 2), round(m["srednie"][s]["away"] * k[s][1], 2)]
                  for s in sm.STATS},
            "surowe": {s: [round(m["srednie"][s]["home"], 2), round(m["srednie"][s]["away"], 2)] for s in sm.STATS},
            "kursy": [[l["stat"], l["scope"], l["line"], l.get("kurs_over"), l.get("buk_over"), l.get("kurs_under"),
                       l.get("buk_under"), None if l.get("rynek_over") is None else round(l["rynek_over"], 4),
                       len(set(l.get("kursy_over", {})) | set(l.get("kursy_under", {})))] for l in m["linie"]],
        }
        rec["wyr"] = highlights(rec, teams)
        rec["n"] = [m.get("n_gosp"), m.get("n_gosc")]
        for key in ("home_id", "away_id"):
            rec.pop(key)
        matches.append(rec)
    # The day's picks shown are the logged ones (what will be settled); the fresh candidates fill
    # days not logged yet and feed the coupons.
    finished = finished_results(df, days_back=21)
    track = forecast_track(pr, df, finished)
    settled = settle_picks(pr, df)
    # aligned expected count per (fixture, stat, scope), shown next to each pick's line
    expected = {}
    for m in analysis["mecze"]:
        gf = factors[group_of(m["liga_id"])]
        for s in sm.STATS:
            for scope in ("home", "away", "total"):
                expected[(m["fixture_id"], s, scope)] = m["srednie"][s][scope] * gf[(s, scope)]
    logged: dict[str, list[dict]] = {}
    for s in settled:
        logged.setdefault(s["day"], []).append(s)
    picks_out = {}
    for day in sorted(set(days) | set(logged)):
        lst = days.get(day, [])
        top = [_logged_pick(s, by_id, expected) for s in sorted(logged[day], key=lambda s: s["rank"])]             if day in logged else [_pick(c) for c in lst[:PICK_LIMIT_PER_DAY]]
        picks_out[day] = {"top": top, "kupony2": sp.coupons(lst, 2), "kupony3": sp.coupons(lst, 3),
                          "kandydaci": [_pick(c) for c in lst[:30]]}
    done = [p for p in settled if p.get("wynik")]
    summary = {
        "typy": len(settled), "rozliczone": len(done),
        "wygrane": sum(p["wynik"] == "wygrana" for p in done),
        "przegrane": sum(p["wynik"] == "przegrana" for p in done),
        "zysk": round(sum(p["zysk"] for p in done), 3),
        "zysk_podatek": round(sum(p["zysk_podatek"] for p in done), 3),
    }
    meta = {
        "utworzono": made,
        "dane_do": df["kickoff"].max().isoformat(),
        "ligi": {str(k): v for k, v in MODEL_LEAGUES.items()},
        "dodatkowe": list(EXTRA),
        "skroty": {str(k): v for k, v in SHORT.items()},
        "top15": list(TOP15), "puchary": list(CUPS),
        "model": {s: {"alpha": round(f.alpha, 5), "alpha_total": round(f.alpha_total, 5),
                      "alpha_puchary": round(f.dispersion("home", True), 5),
                      "alpha_total_puchary": round(f.dispersion("total", True), 5),
                      "dom": round(math.exp(f.b_home), 3), "puchar": round(math.exp(f.b_cup), 3),
                      "k": {g: [round(gf[(s, "home")], 3), round(gf[(s, "away")], 3), round(gf[(s, "total")], 3)]
                            for g, gf in factors.items()}}
                  for s, f in fits.items()},
        "podatek": 0.88,
        "mecze": len(matches),
        "z_kursami": sum(1 for m in matches if m["kursy"]),
    }
    write = lambda name, obj: (dest / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),  # noqa: E731
                                                      encoding="utf-8")
    write("st_mecze.json", matches)
    write("st_druzyny.json", teams)
    write("st_typy.json", picks_out)
    write("st_wyniki.json", {"podsumowanie": summary, "typy": settled, "prognozy": track})
    write("st_rozliczenia.json", finished)
    write("st_meta.json", meta)
    print(f"export: {len(matches)} matches, {len(teams)} teams, {sum(len(v['top']) for v in picks_out.values())} picks -> {dest}")


def _logged_pick(s: dict, by_id: dict, expected: dict) -> dict:
    f = by_id.get(s["fixture_id"])
    m = {"gospodarz": f["home"], "gosc": f["away"]} if f else {"gospodarz": "?", "gosc": "?"}
    p = s["p_model"]
    return {"fixture_id": s["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", "stat": s["stat"],
            "scope": s["scope"], "line": s["line"], "side": s["side"],
            "opis": sp.describe(m, s["stat"], s["scope"], s["side"], s["line"]),
            "p_model": p, "p_rynek": s["p_market"], "kurs": s["odd"], "bukmacher": s["bookmaker"],
            "kurs_uczciwy": round(1 / p, 2), "kurs_min_z_podatkiem": round(1 / (p * 0.88), 2),
            "ev": s["ev"], "ev_podatek": s["ev_tax"], "przewaga": round(p - s["p_market"], 4),
            "wynik": s.get("wynik"), "wartosc": s.get("wartosc"), "zapisano": s["made_at"],
            "oczekiwane": _r(expected.get((s["fixture_id"], s["stat"], s["scope"])), 2)}


def _pick(c: dict) -> dict:
    return {k: c[k] for k in ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis", "p_model", "p_rynek",
                              "kurs", "bukmacher", "kurs_uczciwy", "kurs_min_z_podatkiem", "ev", "ev_podatek",
                              "przewaga", "oczekiwane")}


if __name__ == "__main__":
    main()
