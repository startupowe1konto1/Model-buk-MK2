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
5. picks     -- the market-aligned candidates (stats_picks) of each day, each with a plain-language
               reason; the top ones and the best two-leg coupon are logged as paper bets in
               ``stats/predictions.sqlite`` (tables picks, coupons), frozen at kick-off and settled
               from the match statistics.
6. export    -- JSON for the dashboard in ``--dest`` (matches, teams, picks, track record, meta).

The API quota is shared with other users of the same API account: a run stops spending at
``--max-requests`` or when the account has ``--reserve`` requests left.  The key is read from the
Windows user environment.

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
import stats_sure as ss  # noqa: E402
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
# The day's highlighted picks ("typy dnia"); every other match with prices gets its best bet too
# (user, 2026-10-15: bet on every match, study as many events as possible).
TOP_PER_DAY = 5
ALL_PER_DAY = 80
STAKE = 100.0          # zł on every paper bet and coupon (user, 2026-10-15)


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
    cols = [r[1] for r in pr.execute("pragma table_info(picks)")]
    if "why" not in cols:
        pr.execute("alter table picks add column why text")
    if "expected" not in cols:
        pr.execute("alter table picks add column expected real")
    if "rules" not in cols:
        pr.execute("alter table picks add column rules text")
    if "top" not in cols:   # 1 for the day's highlighted picks; empty on rows from before 15.10 (all were)
        pr.execute("alter table picks add column top integer")
    pr.execute("""create table if not exists coupons (
        made_at text, day text, kind text, legs text, odd real, p_model real, p_market real, ev real,
        ev_tax real, why text, primary key (day, kind))""")
    if "rules" not in [r[1] for r in pr.execute("pragma table_info(coupons)")]:
        pr.execute("alter table coupons add column rules text")
    # near-certain events (kind 'pewniak') and bet builders (kind 'bb'), logged before kick-off
    pr.execute("""create table if not exists extras (
        made_at text, day text, kind text, key text, fixture_id integer, start text, legs text,
        p_model real, odd real, payload text, rules text, primary key (day, kind, key))""")
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
        quotes = parse_odds(payload) + parse_result_odds(payload)
        od.executemany("insert or replace into odds values (?,?,?,?,?,?,?,?)",
                       [(stamp, r["fixture_id"], q["bookmaker"], q["stat"], q["scope"], q["line"], q["side"], q["odd"])
                        for q in quotes])
        od.execute("insert or replace into odds_fetch values (?,?,?)", (r["fixture_id"], stamp, len(quotes)))
        od.commit()
        fetched += 1
    print(f"odds: {fetched} fixtures fetched ({len(rows)} upcoming within {hours:g} h)")
    return fetched


def parse_result_odds(payload: dict) -> list[dict]:
    """The match-winner market (API-Football bet 1) as rows of stat 'result': side home, draw or away."""
    rows = []
    for block in payload.get("response", []):
        for bm in block.get("bookmakers", []):
            for bet in bm.get("bets", []):
                if bet.get("id") != 1:
                    continue
                for v in bet.get("values", []):
                    side = {"Home": "home", "Draw": "draw", "Away": "away"}.get(str(v.get("value")))
                    try:
                        odd = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if side and odd > 1.0:
                        rows.append({"stat": "result", "scope": "match", "line": 0.0, "side": side,
                                     "bookmaker": bm["name"], "odd": odd})
    return rows


def result_probs(rows: list[dict]) -> tuple[float, float, float] | None:
    """Margin-free chances of a home win, a draw and an away win: the median over bookmakers."""
    by_bm: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["stat"] == "result":
            by_bm.setdefault(r["bookmaker"], {})[r["side"]] = r["odd"]
    probs = []
    for q in by_bm.values():
        if len(q) == 3:
            inv = {s: 1 / q[s] for s in ("home", "draw", "away")}
            tot = sum(inv.values())
            probs.append([inv["home"] / tot, inv["draw"] / tot, inv["away"] / tot])
    if not probs:
        return None
    med = np.median(np.array(probs), axis=0)
    med = med / med.sum()
    return float(med[0]), float(med[1]), float(med[2])


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
        result = result_probs(odds.get(f["fixture_id"], []))
        if not frame.empty:
            frame = frame[frame["stat"].isin(list(fits))]
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
                             "n_gosp": n_home, "n_gosc": n_away, "home_id": f["home_id"], "away_id": f["away_id"],
                             "wynik_rynek": result,
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
    """Season averages for/against in the domestic league, ranks in the league, last 5 matches.

    The season shown is the latest one in which the team has at least MIN_SEASON_MATCHES league
    matches with statistics (early in a season, or with a provider gap, the previous one), and the
    ranks compare it with the teams of that league and season that also have enough matches.
    """
    stats = ("shots", "sot", "corners", "fouls", "cards", "offsides")
    cols = {s: sm.STATS[s] for s in stats}
    opp = df.set_index(["fixture_id", "team_id"])
    out = {}
    dom = df[df["cup"] == 0]
    newest = dom.groupby("league_id")["season"].transform("max")
    dom = dom[dom["season"] >= newest - 1]
    agg_rows = []
    for (lid, season, tid), g in dom.groupby(["league_id", "season", "team_id"]):
        rec = {"league_id": lid, "season": season, "team_id": tid, "n": len(g)}
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
        mine = agg[(agg["league_id"] == lid) & (agg["team_id"] == tid) & (agg["n"] >= MIN_SEASON_MATCHES)] \
            if not agg.empty else agg
        if not mine.empty:
            me = mine.sort_values("season").iloc[-1]
            a = agg[(agg["league_id"] == lid) & (agg["season"] == me["season"]) & (agg["n"] >= MIN_SEASON_MATCHES)]
            prof["mecze_w_sezonie"] = int(me["n"])
            prof["sezon_nazwa"] = _season_label(int(me["season"]), int(lid))
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


MIN_SEASON_MATCHES = 4
CALENDAR_YEAR_LEAGUES = {103, 113}  # Norway, Sweden play spring to autumn


def _season_label(season: int, league_id: int) -> str:
    return str(season) if league_id in CALENDAR_YEAR_LEAGUES else f"{season}/{str(season + 1)[-2:]}"


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


# ---------------------------------------------------------------------------- 8. explanations

NOUN_GEN = {"shots": "strzałów", "sot": "celnych strzałów", "corners": "rożnych", "fouls": "fauli",
            "cards": "kartek", "yellow": "żółtych kartek", "offsides": "spalonych"}


def _pc(p: float) -> str:
    return f"{round(p * 100)}%"


def _pl2(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _int(v) -> str:
    return "?" if v is None else str(int(round(v)))


def rank_phrase(r: int, n: int) -> str:
    """'3. najwięcej w lidze' for the top half, '2. najmniej w lidze' for the bottom half."""
    return f"{r}. najwięcej w lidze" if r <= (n + 1) / 2 else f"{n - r + 1}. najmniej w lidze"


def why_pick(c: dict, m: dict, teams: dict, rank: int, n: int) -> str:
    """Plain-language reasons for a pick: the model's number, the teams' record, the market, the ranking."""
    stat, scope, side, line = c["stat"], c["scope"], c["side"], c["line"]
    noun = NOUN_GEN[stat]
    who = {"home": m["gospodarz"], "away": m["gosc"], "total": "w całym meczu"}[scope]
    k = int(line + 0.5)
    parts = [f"Model oczekuje średnio {_pl(c['oczekiwane'])} {noun} ({who}). Szansa, że padnie co najmniej {k}, "
             f"to {_pc(c['p_model'])} (u bukmacherów to zakład „powyżej {_pl(line)}”)."]
    if side != "over":
        parts = [f"Model oczekuje średnio {_pl(c['oczekiwane'])} {noun} ({who}); zakład „poniżej {_pl(line)}” "
                 f"wychodzi z szansą {_pc(c['p_model'])}."]
    th, ta = teams.get(str(m["home_id"])), teams.get(str(m["away_id"]))
    if scope in ("home", "away"):
        t, o = (th, ta) if scope == "home" else (ta, th)
        tn, on = (m["gospodarz"], m["gosc"]) if scope == "home" else (m["gosc"], m["gospodarz"])
        if t and t["sezon"].get(stat) and t["sezon"][stat][0] is not None:
            r = t["miejsce"].get(stat) or [None, None, None]
            parts.append(f"{tn} ma w swojej lidze średnio {_pl(t['sezon'][stat][0])} {noun} na mecz"
                         + (f" ({rank_phrase(r[0], r[2])})" if r[0] else "") + ".")
        if o and o["sezon"].get(stat) and o["sezon"][stat][1] is not None:
            r = o["miejsce"].get(stat) or [None, None, None]
            parts.append(f"Rywale drużyny {on} mają przeciw niej średnio {_pl(o['sezon'][stat][1])}"
                         + (f" ({rank_phrase(r[1], r[2])})" if r[1] else "") + ".")
        last_t = [g["s"][stat][0] for g in (t or {}).get("ost5", []) if g["s"].get(stat) and g["s"][stat][0] is not None]
        last_o = [g["s"][stat][1] for g in (o or {}).get("ost5", []) if g["s"].get(stat) and g["s"][stat][1] is not None]
        if last_t:
            parts.append(f"Ostatnie mecze drużyny {tn}: {', '.join(_int(x) for x in last_t)}.")
        if last_o:
            parts.append(f"Rywale drużyny {on} w jej ostatnich meczach: {', '.join(_int(x) for x in last_o)}.")
    else:
        for t, tn in ((th, m["gospodarz"]), (ta, m["gosc"])):
            if t and t["sezon"].get(stat) and None not in t["sezon"][stat]:
                parts.append(f"{tn}: w lidze średnio {_pl(t['sezon'][stat][0])} {noun} własnych "
                             f"i {_pl(t['sezon'][stat][1])} rywali na mecz.")
    parts.append(f"Bukmacherzy dają {_pc(c['p_rynek'])} (najlepszy kurs {_pl2(c['kurs'])}, {c['bukmacher']}), "
                 f"model {_pc(c['p_model'])}: różnica {round((c['p_model'] - c['p_rynek']) * 100):+d} pkt.")
    if n:
        parts.append(("Najwyższy" if rank == 1 else f"{rank}. najwyższy") + f" oczekiwany zysk spośród {n} zakładów tego dnia, "
                     "które spełniają zasady: tylko „co najmniej” (powyżej X,5), kurs do ok. 1,8, zysk także po podatku "
                     "12%, strzały, celne albo rożne, różnica z rynkiem 4–15 pkt, drużyny z co najmniej 10 meczami w danych.")
    return " ".join(parts)


def _leg_name(l: dict) -> str:
    """A leg's description with the match instead of 'W meczu' for whole-match bets."""
    return l["opis"].replace("W meczu:", f"{l['mecz']}:", 1) if l.get("mecz") else l["opis"]


def why_coupon(k: dict, alts: list[dict], rank: int = 1) -> str:
    """rank: the coupon's place among the day's coupons with the same number of legs."""
    q = math.prod(l["p_rynek"] for l in k["legs"])
    n, n2 = k.get("n_kombinacji"), k.get("n_dwojek")
    s = (f"{len(k['legs'])} zdarzenia z różnych meczów, łączny kurs {_pl2(k['kurs'])}. Szansa wg modelu {_pc(k['p_model'])}, "
         f"wg bukmacherów {_pc(q)}; oczekiwany zysk z podatkiem {round(k['ev_podatek'] * 100):+d}% stawki. ")
    place = "Największy oczekiwany zysk" if rank == 1 else f"{rank}. miejsce pod względem oczekiwanego zysku"
    if n is None:
        s += "Kupon o łącznym kursie 1,8–2,5 złożony z typów tego dnia."
    elif len(k["legs"]) == 2:
        s += f"{place} spośród {n2} kuponów z 2 zdarzeń o łącznym kursie 1,8–2,5, które opłacają się po podatku."
    else:
        s += (("Tego dnia żaden kupon z 2 zdarzeń nie spełnił zasad. " if not n2 else "")
              + f"{place} spośród {n - n2} kuponów z 3 zdarzeń o łącznym kursie 1,8–2,5, które opłacają się po podatku.")
    s += " Każde zdarzenie to „co najmniej …” z kursem do ok. 1,8"
    if alts:
        s += ". Kolejne: " + "; ".join(
            f"{' + '.join(_leg_name(l) for l in a['legs'])} (kurs {_pl2(a['kurs'])}, zysk {round(a['ev_podatek'] * 100):+d}%)"
            for a in alts[:2])
    return s + "."


# ---------------------------------------------------------------------------- 9. settle paper picks and coupons

def settle_leg(leg: dict, by_fix: dict) -> tuple[str | None, float | None]:
    g = by_fix.get(leg["fixture_id"])
    if g is None or len(g) != 2:
        return None, None
    col = sm.STATS.get(leg["stat"]) or sm.EXTRA_STATS[leg["stat"]]
    h = g[g["side"] == "home"][col].iloc[0]
    a = g[g["side"] == "away"][col].iloc[0]
    if pd.isna(h) or pd.isna(a):
        return None, None
    if leg["side"] == "more":   # the team's count above the opponent's (a tie loses)
        mine, theirs = (h, a) if leg["scope"] == "home" else (a, h)
        return ("wygrana" if mine > theirs else "przegrana"), float(mine)
    val = float({"home": h, "away": a, "total": h + a}[leg["scope"]])
    if val == leg["line"]:
        return "zwrot", val
    return ("wygrana" if (val > leg["line"]) == (leg["side"] == "over") else "przegrana"), val


def settle_picks(pr: sqlite3.Connection, df: pd.DataFrame) -> list[dict]:
    rows = [dict(r) for r in pr.execute("select * from picks order by day, rank")]
    by_fix = {fid: g for fid, g in df.groupby("fixture_id")}
    out = []
    for p in rows:
        res, val = settle_leg(p, by_fix)
        if val is not None:
            p["wartosc"] = val
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


def settle_extras(pr: sqlite3.Connection, df: pd.DataFrame) -> list[dict]:
    """Logged near-certain events and bet builders: in when every leg came in."""
    rows = [dict(r) for r in pr.execute("select * from extras order by day, kind, p_model desc")]
    by_fix = {fid: g for fid, g in df.groupby("fixture_id")}
    for r in rows:
        legs = json.loads(r["legs"])
        res = [settle_leg(l, by_fix) for l in legs]
        for l, (w, v) in zip(legs, res):
            l["wynik"], l["wartosc"] = w, v
        r["legs"] = legs
        r["payload"] = json.loads(r["payload"]) if r.get("payload") else {}
        r["wynik"] = ("przegrana" if any(w == "przegrana" for w, _ in res)
                      else "wygrana" if all(w == "wygrana" for w, _ in res) else None)
    return rows


def log_extras(pr: sqlite3.Connection, extra: dict, kickoff: dict, made: str) -> None:
    """Near-certain events, bet builders and the coupon of near-certain events of matches not started
    yet: the day's rows are replaced by this run's; rows of started matches stay (they get settled)."""
    days = (set(extra["pewne"]) | set(extra["bb"]) | set(extra["kupon_pewne"])
            | {r[0] for r in pr.execute("select distinct day from extras where day >= ?", (made[:10],))}
            | {r[0] for r in pr.execute("select day from coupons where kind = 'pewne' and day >= ?", (made[:10],))})
    for day in sorted(days):
        rows = [dict(r) for r in pr.execute("select key, fixture_id from extras where day = ?", (day,))]
        for r in rows:
            if kickoff.get(r["fixture_id"], "") > made:
                pr.execute("delete from extras where day = ? and key = ?", (day, r["key"]))
        for kind, lst in (("pewniak", extra["pewne"].get(day, [])), ("bb", extra["bb"].get(day, []))):
            for e in lst:
                if kickoff.get(e["fixture_id"], "") <= made:
                    continue
                key = (f"{e['fixture_id']}:{e['stat']}:{e['scope']}" if kind == "pewniak"
                       else f"{e['fixture_id']}:{e['rodzaj']}")
                legs = ([{k: e[k] for k in ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis")}]
                        if kind == "pewniak" else e["legs"])
                pr.execute("insert or ignore into extras values (?,?,?,?,?,?,?,?,?,?,?)", (
                    made, day, kind, key, e["fixture_id"], e["start"], json.dumps(legs, ensure_ascii=False),
                    e["p_model"], e.get("kurs"), json.dumps(e, ensure_ascii=False, default=float), sp.RULES))
        k = extra["kupon_pewne"].get(day)
        row = pr.execute("select legs from coupons where day = ? and kind = 'pewne'", (day,)).fetchone()
        started = row is not None and min(kickoff.get(l["fixture_id"], "") for l in json.loads(row[0])) <= made
        if started:
            continue
        if k and min(kickoff.get(l["fixture_id"], "") for l in k["legs"]) > made:
            pr.execute("insert or replace into coupons (made_at, day, kind, legs, odd, p_model, p_market, ev, ev_tax, "
                       "why, rules) values (?,?,?,?,?,?,?,?,?,?,?)", (
                           made, day, "pewne", json.dumps(k["legs"], ensure_ascii=False), k["kurs"], k["p_model"],
                           k["p_rynek"], k["ev"], k["ev_podatek"], k["why"], sp.RULES))
        elif row is not None:
            pr.execute("delete from coupons where day = ? and kind = 'pewne'", (day,))


def settle_coupons(pr: sqlite3.Connection, df: pd.DataFrame) -> list[dict]:
    rows = [dict(r) for r in pr.execute("select * from coupons order by day")]
    by_fix = {fid: g for fid, g in df.groupby("fixture_id")}
    for c in rows:
        legs = json.loads(c["legs"])
        res = [settle_leg(l, by_fix) for l in legs]
        for l, (r, v) in zip(legs, res):
            l["wynik"], l["wartosc"] = r, v
        c["legs"] = legs
        c["wynik"] = None
        if any(r == "przegrana" for r, _ in res):
            c["wynik"], c["zysk"], c["zysk_podatek"] = "przegrana", -1.0, -1.0
        elif all(r in ("wygrana", "zwrot") for r, _ in res):
            odd = math.prod(l["kurs"] if r == "wygrana" else 1.0 for l, (r, _) in zip(legs, res))
            if odd > 1.0:
                c["wynik"], c["zysk"], c["zysk_podatek"] = "wygrana", round(odd - 1, 4), round(odd * 0.88 - 1, 4)
            else:
                c["wynik"], c["zysk"], c["zysk_podatek"] = "zwrot", 0.0, 0.0
    return rows


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
    goals_fit = sm.fit_stat(df, "goals", asof, leagues=leagues, **ss.GOALS_FIT)
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

    team_ids = {f["home_id"] for f in upcoming} | {f["away_id"] for f in upcoming}
    teams = team_profiles(df, team_ids, fits, leagues)

    # candidates: one per (match, statistic, scope), best expected value first, grouped by day
    cands = []
    for g, gf in factors.items():
        cands += sp.candidates({"mecze": [m for m in analysis["mecze"] if group_of(m["liga_id"]) == g],
                                "model": analysis["model_puchary" if g == "puchary" else "model"]}, gf)
    by_match = {m["fixture_id"]: m for m in analysis["mecze"]}
    for c in cands:
        c["day"] = by_match[c["fixture_id"]]["start"][:10]
    # Every match gets its best bet within the user's rules (only "at least" on X.5 lines, odds up to
    # about 1.8, shots / on target / corners) and the safety limits (market band, price check, edge at
    # most 15 points): a line that qualifies as a pick of the day first, then the most expected profit
    # after tax, even when it is negative, so the paper test covers as many matches as possible. The
    # five best qualifying ones are the day's highlighted picks.
    allowed = [c for c in cands if c["do_typow"] and c["przewaga"] <= sp.MAX_EDGE]
    best_of_match: dict[int, dict] = {}
    for c in sorted(allowed, key=lambda c: (not strict_pick(c), -c["ev_podatek"], -c["p_model"])):
        best_of_match.setdefault(c["fixture_id"], c)
    days: dict[str, list[dict]] = {}
    for c in sorted(best_of_match.values(), key=lambda c: (not strict_pick(c), -c["ev_podatek"], -c["p_model"])):
        days.setdefault(c["day"], []).append(c)
    for lst in days.values():
        n_top = 0
        for c in lst:
            c["top"] = strict_pick(c) and n_top < TOP_PER_DAY
            n_top += c["top"]
    # Coupon legs come from a wider pool than the picks: likelier events (model at least 55%) whose
    # price still beats the model's fair price, with a smaller edge over the market (2-15 points).
    leg_pool: dict[str, list[dict]] = {}
    seen_leg = set()
    for c in sorted(cands, key=lambda c: -c["ev"]):
        key = (c["fixture_id"], c["stat"], c["scope"])
        if (c["do_typow"] and 0.02 <= c["przewaga"] <= sp.MAX_EDGE and c["ev"] > 0 and c["p_model"] >= 0.55
                and key not in seen_leg):
            seen_leg.add(key)
            leg_pool.setdefault(c["day"], []).append(c)
    for day, lst in leg_pool.items():
        for rank, c in enumerate(lst, 1):
            c.setdefault("why", why_pick(c, by_match[c["fixture_id"]], teams, rank, None))
    coupons_of_day: dict[str, list[dict]] = {}
    for day in sorted(set(days) | set(leg_pool)):
        lst = days.get(day, [])
        n_strict = sum(strict_pick(c) for c in lst)
        for rank, c in enumerate(lst, 1):
            c["why"] = why_pick(c, by_match[c["fixture_id"]], teams, rank, n_strict if c["top"] else None)
            if not c["top"]:
                c["why"] += " " + why_rest(c)
        pool = leg_pool.get(day, [])
        # The user wants two events, at most three: a three-leg coupon only when no two-leg one qualifies.
        ks = [k for k in sp.coupons(pool, 2, top=10**6) + sp.coupons(pool, 3, top=10**6) if k["ev_podatek"] > 0]
        ks.sort(key=lambda k: (len(k["legs"]), -k["ev"]))
        n2 = sum(len(k["legs"]) == 2 for k in ks)
        for k in ks:
            k["n_kombinacji"], k["n_dwojek"] = len(ks), n2
        ks = ks[:5]
        for i, k in enumerate(ks):
            k["why"] = why_coupon(k, ks[i + 1:i + 3], i + 1 if len(k["legs"]) == 2 else i + 1 - n2)
        coupons_of_day[day] = ks

    # near-certain events, a coupon made of them and same-match bet builders (stats_sure.py)
    extra = ss.run(analysis, fac, fits, goals_fit, df, set(SHOWN), set(CUPS), made)
    print(f"pewniaczki: {sum(len(v) for v in extra['pewne'].values())}, bet buildery: "
          f"{sum(len(v) for v in extra['bb'].values())}, kupony z pewniaczków: {len(extra['kupon_pewne'])}")

    if "picks" not in skip:
        kickoff = {m["fixture_id"]: m["start"] for m in analysis["mecze"]}
        logged_days = {r[0] for r in pr.execute("select day from picks union select day from coupons")}
        for day in sorted(set(days) | set(coupons_of_day) | {d for d in logged_days if d >= made[:10]}):
            lst = days.get(day, [])
            # Picks of matches that have started are frozen (they are what gets settled); the rest of
            # the day's list is replaced by this run's, so the log keeps the last list made before kickoff.
            logged = [dict(r) for r in pr.execute("select fixture_id, top from picks where day = ?", (day,))]
            frozen = [r for r in logged if kickoff.get(r["fixture_id"], "") <= made or r["fixture_id"] not in kickoff]
            open_ids = [r["fixture_id"] for r in logged if r not in frozen]
            if open_ids:
                pr.execute(f"delete from picks where day = ? and fixture_id in ({','.join('?' * len(open_ids))})",
                           (day, *open_ids))
            fresh = [c for c in lst if kickoff.get(c["fixture_id"], "") > made]
            # highlighted picks already frozen keep their places among the five
            free_top = max(0, TOP_PER_DAY - sum(1 for r in frozen if r["top"] is None or r["top"]))
            n_top = 0
            for rank, c in enumerate(fresh[:ALL_PER_DAY], len(frozen) + 1):
                is_top = strict_pick(c) and n_top < free_top
                n_top += is_top
                pr.execute("insert or replace into picks (made_at, day, fixture_id, stat, scope, line, side, p_model, "
                           "p_market, odd, bookmaker, ev, ev_tax, rank, why, expected, rules, top) "
                           "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                               made, day, c["fixture_id"], c["stat"], c["scope"], c["line"], c["side"], c["p_model"],
                               c["p_rynek"], c["kurs"], c["bukmacher"], c["ev"], c["ev_podatek"], rank, c["why"],
                               c["oczekiwane"], sp.RULES, int(is_top)))
            # the coupon of the day: the first coupon of the list, frozen once its first match has started
            ks = [k for k in coupons_of_day.get(day) or []
                  if min(kickoff.get(l["fixture_id"], "") for l in k["legs"]) > made]
            row = pr.execute("select legs from coupons where day = ? and kind = '2'", (day,)).fetchone()
            started = row is not None and min(kickoff.get(l["fixture_id"], "") for l in json.loads(row[0])) <= made
            if row is not None and not started and not ks:
                pr.execute("delete from coupons where day = ? and kind = '2'", (day,))
            if ks and not started:
                k = ks[0]
                pr.execute("insert or replace into coupons (made_at, day, kind, legs, odd, p_model, p_market, ev, "
                           "ev_tax, why, rules) values (?,?,?,?,?,?,?,?,?,?,?)", (
                               made, day, "2", json.dumps(k["legs"], ensure_ascii=False), k["kurs"], k["p_model"],
                               math.prod(l["p_rynek"] for l in k["legs"]), k["ev"], k["ev_podatek"], k["why"], sp.RULES))
        log_extras(pr, extra, kickoff, made)
        pr.commit()

    if "export" not in skip:
        export(Path(args.dest), analysis, factors, fits, df, teams, upcoming, days, coupons_of_day, pr, fx, made, extra)
    print(f"requests used {client.used}, account remaining {client.remaining}")


def fixture_names(fx: sqlite3.Connection, ids: set[int]) -> dict[int, dict]:
    out = {}
    for fid in ids:
        r = fx.execute("select fixture_id, league_id, kickoff_utc, home, away from fixtures where fixture_id = ?",
                       (fid,)).fetchone()
        if r:
            out[fid] = dict(r)
    return out


def plan_items() -> list[dict]:
    path = HERE.parents[1] / "config" / "mk2" / "status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return []


def export(dest: Path, analysis: dict, factors: dict, fits: dict, df: pd.DataFrame, teams: dict,
           upcoming: list, days: dict, coupons_of_day: dict, pr: sqlite3.Connection, fx: sqlite3.Connection,
           made: str, extra: dict | None = None) -> None:
    extra = extra or {"pewne": {}, "bb": {}, "kupon_pewne": {}}
    dest.mkdir(parents=True, exist_ok=True)
    by_id = {f["fixture_id"]: f for f in upcoming}
    rank_of = {lid: i for i, lid in enumerate([*CUPS, *TOP15, *EXTRA])}
    matches = []
    for m in analysis["mecze"]:
        f = by_id[m["fixture_id"]]
        gf = factors[group_of(m["liga_id"])]
        k = {s: [gf[(s, "home")], gf[(s, "away")], gf[(s, "total")]] for s in sm.STATS}
        rec = {
            "id": m["fixture_id"], "start": f["kickoff_utc"], "liga_id": f["league_id"],
            "runda": f["round"], "sedzia": f["referee"], "status": f["status"],
            "gosp": f["home"], "gosc": f["away"], "gosp_id": f["home_id"], "gosc_id": f["away_id"],
            "home_id": f["home_id"], "away_id": f["away_id"],
            # home, away and whole match, each aligned with its own market factor (as the picks are)
            "m": {s: [round(m["srednie"][s][sc] * k[s][i], 2) for i, sc in enumerate(("home", "away", "total"))]
                  for s in sm.STATS},
            "kursy": [[l["stat"], l["scope"], l["line"], l.get("kurs_over"), l.get("buk_over"), l.get("kurs_under"),
                       l.get("buk_under"), None if l.get("rynek_over") is None else round(l["rynek_over"], 4),
                       len(set(l.get("kursy_over", {})) | set(l.get("kursy_under", {})))] for l in m["linie"]],
        }
        rec["wyr"] = highlights(rec, teams)
        rec["n"] = [m.get("n_gosp"), m.get("n_gosc")]
        for key in ("home_id", "away_id"):
            rec.pop(key)
        matches.append(rec)
    # fixed order everywhere: competition, kick-off, home team
    matches.sort(key=lambda r: (rank_of.get(r["liga_id"], 999), r["start"], r["gosp"]))

    finished = finished_results(df, days_back=21)
    track = forecast_track(pr, df, finished)
    settled = settle_picks(pr, df)
    coupons = settle_coupons(pr, df)
    extras = settle_extras(pr, df)
    names = fixture_names(fx, {p["fixture_id"] for p in settled}
                          | {l["fixture_id"] for c in coupons for l in c["legs"]})

    def named(fid: int) -> dict:
        f = names.get(fid) or {}
        return {"gospodarz": f.get("home", "?"), "gosc": f.get("away", "?")}

    def logged_pick(s: dict) -> dict:
        m = named(s["fixture_id"])
        p = s["p_model"]
        return {"fixture_id": s["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}",
                "start": (names.get(s["fixture_id"]) or {}).get("kickoff_utc"), "stat": s["stat"],
                "scope": s["scope"], "line": s["line"], "side": s["side"],
                "opis": sp.describe(m, s["stat"], s["scope"], s["side"], s["line"]),
                "zaklad": f"{'powyżej' if s['side'] == 'over' else 'poniżej'} {_pl(s['line'])}",
                "zasady": s.get("rules") or "v1",
                "p_model": p, "p_rynek": s["p_market"], "kurs": s["odd"], "bukmacher": s["bookmaker"],
                "kurs_min_z_podatkiem": round(1 / (p * 0.88), 2), "ev": s["ev"], "ev_podatek": s["ev_tax"],
                "wynik": s.get("wynik"), "wartosc": s.get("wartosc"), "zysk": s.get("zysk"),
                "zysk_podatek": s.get("zysk_podatek"), "zapisano": s["made_at"], "why": s.get("why"),
                "oczekiwane": _r(s.get("expected"), 2), "top": s.get("top") is None or bool(s.get("top")),
                **money(s.get("zysk_podatek"), s.get("wynik"), s["ev_tax"])}

    def logged_coupon(c: dict) -> dict:
        for l in c["legs"]:
            l.setdefault("zaklad", f"{'powyżej' if l['side'] == 'over' else 'poniżej'} {_pl(l['line'])}")
        return {"legs": c["legs"], "kurs": c["odd"], "p_model": c["p_model"], "p_rynek": c["p_market"],
                "zasady": c.get("rules") or "v1",
                "ev": c["ev"], "ev_podatek": c["ev_tax"], "why": c["why"], "wynik": c.get("wynik"),
                "zysk": c.get("zysk"), "zysk_podatek": c.get("zysk_podatek"), "zapisano": c["made_at"],
                **money(c.get("zysk_podatek"), c.get("wynik"), c["ev_tax"])}

    logged: dict[str, list[dict]] = {}
    for s in settled:
        logged.setdefault(s["day"], []).append(s)
    logged_c = {c["day"]: c for c in coupons if c["kind"] == "2"}
    logged_cp = {c["day"]: c for c in coupons if c["kind"] == "pewne"}
    logged_x: dict[tuple[str, str], list[dict]] = {}
    for r in extras:
        logged_x.setdefault((r["day"], r["kind"]), []).append(r)

    def logged_extra(r: dict) -> dict:
        """A logged near-certain event or builder as the app shows it, with its result."""
        out = dict(r["payload"])
        out.update({"wynik": r["wynik"], "zapisano": r["made_at"]})
        if r["kind"] == "pewniak":
            out["wartosc"] = r["legs"][0].get("wartosc")     # what came in, as with the picks
        else:
            out["legs"] = r["legs"]
        return out

    picks_out = {}
    for day in sorted(set(days) | set(logged) | set(logged_c) | set(extra["pewne"]) | set(extra["bb"])
                      | {d for d, _ in logged_x} | set(logged_cp)):
        lst = days.get(day, [])
        everything = ([logged_pick(s) for s in sorted(logged[day], key=lambda s: s["rank"])] if day in logged
                      else [_pick(c) for c in lst[:ALL_PER_DAY]])
        top = [p for p in everything if p["top"]]
        ks = coupons_of_day.get(day, [])
        kupon = logged_coupon(logged_c[day]) if day in logged_c else (ks[0] if ks else None)
        picks_out[day] = {"top": top, "wszystkie": everything, "kupon": kupon, "kupony": ks[:5],
                          "kandydaci": [_pick(c) for c in lst[:30]]}
        # near-certain events and builders: the logged ones (with results) where there are any
        picks_out[day]["pewne"] = ([logged_extra(r) for r in logged_x[(day, "pewniak")]] if (day, "pewniak") in logged_x
                                   else extra["pewne"].get(day, []))
        picks_out[day]["bb"] = ([logged_extra(r) for r in logged_x[(day, "bb")]] if (day, "bb") in logged_x
                                else extra["bb"].get(day, []))
        picks_out[day]["kupon_pewne"] = (logged_coupon(logged_cp[day]) if day in logged_cp
                                         else extra["kupon_pewne"].get(day))

    # results: what came in and what did not, per day and in total
    def tally(items: list[dict]) -> dict:
        done = [x for x in items if x.get("wynik")]
        return {"n": len(items), "rozliczone": len(done),
                "trafione": sum(x["wynik"] == "wygrana" for x in done),
                "chybione": sum(x["wynik"] == "przegrana" for x in done),
                "zysk": round(sum(x["zysk"] for x in done), 3),
                "zysk_podatek": round(sum(x["zysk_podatek"] for x in done), 3)}
    results = []
    for day in sorted(set(logged) | set(logged_c) | set(logged_cp), reverse=True):
        ps = [logged_pick(s) for s in sorted(logged.get(day, []), key=lambda s: s["rank"])]
        ps.sort(key=lambda p: not p["top"])
        kc = logged_coupon(logged_c[day]) if day in logged_c else None
        kp = logged_coupon(logged_cp[day]) if day in logged_cp else None
        old = any(not s.get("rules") for s in logged.get(day, [])) or (day in logged_c and not logged_c[day].get("rules"))
        tops = [p for p in ps if p["top"]]
        results.append({"dzien": day, "typy": ps, "kupon": kc, "kupon_pewne": kp, "typy_wynik": tally(tops),
                        "wszystkie_wynik": tally(ps), "kupon_wynik": tally([kc]) if kc else None, "stare_zasady": old,
                        "finanse": {"top5": finance(tops), "pozostale": finance([p for p in ps if not p["top"]]),
                                    "kupon": finance([kc] if kc else []), "kupon_pewne": finance([kp] if kp else []),
                                    "dzien": finance(ps + [x for x in (kc, kp) if x])}})
    current = [s for s in settled if s.get("rules")]
    old_rows = [s for s in settled if not s.get("rules")]
    day_coupons = [c for c in coupons if c["kind"] == "2"]

    def hits(rows: list[dict]) -> dict:
        """Logged near-certain events or builders: how many came in, against the model's expectation."""
        done = [r for r in rows if r["wynik"]]
        return {"n": len(rows), "rozliczone": len(done), "trafione": sum(r["wynik"] == "wygrana" for r in done),
                "oczekiwane": round(sum(r["p_model"] for r in done), 2)}
    # money: every paper bet ever logged (the old rules' day included; it is also shown on its own)
    cur_p = [logged_pick(s) for s in settled]
    cur_c = [logged_coupon(c) for c in day_coupons]
    cur_k = [logged_coupon(c) for c in coupons if c["kind"] == "pewne"]
    run, cum = [], 0.0
    for r in sorted(results, key=lambda r: r["dzien"]):
        f = r["finanse"]["dzien"]
        if f["rozliczone"]:
            cum += f["zysk"]
            run.append({"dzien": r["dzien"], "zysk": f["zysk"], "suma": round(cum, 2)})
    wyniki = {"dni": results, "stawka": STAKE,
              "finanse": {"top5": finance([p for p in cur_p if p["top"]]),
                          "pozostale": finance([p for p in cur_p if not p["top"]]),
                          "kupon": finance(cur_c), "kupon_pewne": finance(cur_k), "lacznie": finance(cur_p + cur_c + cur_k)},
              "finanse_stare": finance([logged_pick(s) for s in old_rows]
                                       + [logged_coupon(c) for c in day_coupons if not c.get("rules")]),
              "narastajaco": run,
              "razem": {"typy": tally([logged_pick(s) for s in current]),
                        "kupony": tally([logged_coupon(c) for c in day_coupons if c.get("rules")])},
              "stare_zasady": {"typy": tally([logged_pick(s) for s in old_rows]),
                               "kupony": tally([logged_coupon(c) for c in day_coupons if not c.get("rules")])},
              "pewniaki": hits([r for r in extras if r["kind"] == "pewniak"]),
              "bet_buildery": hits([r for r in extras if r["kind"] == "bb"]),
              "kupony_pewne": tally([logged_coupon(c) for c in coupons if c["kind"] == "pewne"]),
              "prognozy": track}
    meta = {
        "utworzono": made,
        "dane_do": df["kickoff"].max().isoformat(),
        "ligi": {str(k): v for k, v in MODEL_LEAGUES.items()},
        "skroty": {str(k): v for k, v in SHORT.items()},
        "top15": list(TOP15), "puchary": list(CUPS), "dodatkowe": list(EXTRA),
        "model": {s: {"alpha": round(f.alpha, 5), "alpha_total": round(f.alpha_total, 5),
                      "alpha_puchary": round(f.dispersion("home", True), 5),
                      "alpha_total_puchary": round(f.dispersion("total", True), 5)}
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
    write("st_wyniki.json", wyniki)
    write("st_rozliczenia.json", finished)
    write("st_plan.json", plan_items())
    write("st_meta.json", meta)
    print(f"export: {len(matches)} matches, {len(teams)} teams, {sum(len(v['top']) for v in picks_out.values())} picks, "
          f"{len(results)} days of results -> {dest}")


def _pick(c: dict) -> dict:
    out = {k: c.get(k) for k in ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis", "zaklad", "p_model",
                                 "p_rynek", "kurs", "bukmacher", "kurs_uczciwy", "kurs_min_z_podatkiem", "ev",
                                 "ev_podatek", "przewaga", "oczekiwane", "why", "zasady", "top")}
    out.update(money(None, None, c.get("ev_podatek")))
    return out


def strict_pick(c: dict) -> bool:
    """A line good enough for the day's highlighted picks: pays after tax and the model is clearly
    (4-15 points) above the bookmakers."""
    return sp.MIN_EDGE <= c["przewaga"] <= sp.MAX_EDGE and c["ev_podatek"] >= 0


def why_rest(c: dict) -> str:
    if strict_pick(c):
        return "Spełnia zasady typów dnia, ale pięć innych zakładów tego dnia ma wyższy oczekiwany zysk."
    if c["ev_podatek"] >= 0:
        return ("Opłaca się po podatku, ale model różni się od bukmacherów o mniej niż 4 punkty, więc to nie jest typ "
                "dnia; to najlepszy zakład w tym meczu.")
    return (f"To najlepszy zakład w tym meczu według zasad, ale po podatku model spodziewa się straty "
            f"({round(c['ev_podatek'] * 100):+d}% stawki): jest zapisany, żeby sprawdzać model na każdym meczu.")


def money(unit_after_tax: float | None, result: str | None, ev_tax: float | None = None) -> dict:
    """A 100 zł paper bet: the stake, the payout after the 12% tax and the profit (once settled), and the
    profit the model expects."""
    out = {"stawka": STAKE, "zysk_oczekiwany_zl": None if ev_tax is None else round(STAKE * ev_tax, 2)}
    if result is None or unit_after_tax is None:
        return {**out, "wyplata": None, "zysk_zl": None}
    return {**out, "wyplata": round(STAKE * (1 + unit_after_tax), 2), "zysk_zl": round(STAKE * unit_after_tax, 2)}


def finance(items: list[dict]) -> dict:
    """Settled 100 zł bets: how many, how many came in, staked, paid out after tax, profit, return."""
    done = [x for x in items if x.get("wynik")]
    staked = STAKE * len(done)
    paid = round(sum(x["wyplata"] for x in done), 2)
    return {"n": len(items), "rozliczone": len(done), "trafione": sum(x["wynik"] == "wygrana" for x in done),
            "postawione": staked, "wyplaty": paid, "zysk": round(paid - staked, 2),
            "roi": round((paid - staked) / staked, 4) if staked else None}


if __name__ == "__main__":
    main()
