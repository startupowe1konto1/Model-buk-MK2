"""Typometr's check of the day's main bet builders shortly before kick-off: the line-ups, the latest odds and the
injury list, and the same builder (its events and thresholds as proposed) priced again with them.

A scheduled task runs this every 30 minutes during the day.  A main builder is checked by the first run from
CHECK_BEFORE_MIN minutes before kick-off on (so 35-65 minutes before it): /fixtures?id= (status and, when the
provider has them, the line-ups) and /odds?fixture= (unless fetched in the last ODDS_FRESH_MIN minutes).  Line-ups
usually come out 20-60 minutes before kick-off; when they were not out yet, the match is looked at once more by a
run from LINEUP_BEFORE_MIN minutes before kick-off on, at least RECHECK_GAP_MIN after the first (line-ups only).
The check:

- the builder's chance by the model and by the bookmakers' lines again (the same copula, means and random draws
  as when it was proposed; the goals follow the latest match odds; a player on the bench gets a sub's share);
- the line-ups: a player leg whose player does not start, and a team starting with few of its usual eleven
  (the players with the most starts and minutes in its last 5 matches, from kadry.json of the daily run);
- a verdict: 'ok', 'uwaga' (something to look at) or 'nie_graj', with the reasons in Polish.

The result is stored in the builder's logged payload ('kontrola') and in the app's data/st_typy.json; a match
checked this way keeps its builders in later daily runs (stats_daily.log_extras).  Requests: two per match at
most (the free plan's budget is shared with the daily run, which keeps some for these checks; on it, odds of the
last three hours are not fetched again).

Usage (from app/):
    .venv\\Scripts\\python scripts\\mk2\\stats_prematch.py           # the checks due now, then the next time
    .venv\\Scripts\\python scripts\\mk2\\stats_prematch.py --plan    # only the time of the next check, no requests
The last line is 'NEXT=<local time with offset>' or 'NEXT=none': when the next check is due.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stats_daily as sd  # noqa: E402
import stats_market as mk  # noqa: E402
import stats_sure as ss  # noqa: E402

CHECK_BEFORE_MIN = 65       # the first check (odds, status, line-ups when already out) from this long before kick-off
LINEUP_BEFORE_MIN = 40      # the second look, when the line-ups were not out at the first, from this long before
RECHECK_GAP_MIN = 15        # ... and at least this long after the first
EARLY_MIN = 3               # a run a few minutes early (the scheduler's delay varies) still does the checks
ODDS_FRESH_MIN = 30         # odds fetched this recently are not fetched again
ODDS_FRESH_FREE_MIN = 180   # on the free plan (100 requests a day) odds of the last 3 hours are kept
HORIZON_H = 36              # main builders looked at for the next check
USUAL = 11
ROTATION_MAX = 7            # a team starting with at most this many of its usual eleven is rotating
BENCH_SHARE = 0.30          # a player on the bench: about a quarter of a match, so this part of his usual share
DROP_WARN = 0.05            # the chance fell at least this much since the proposal
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE", "SUSP"}
DATA = HERE.parents[1] / "dashboard_mk2" / "app" / "data"


def when_due(start: dt.datetime, kontrola: dict | None) -> dt.datetime | None:
    """From when a main builder's next check is due: the first CHECK_BEFORE_MIN minutes before kick-off; when the
    line-ups were not out at the first, LINEUP_BEFORE_MIN minutes before it (at least RECHECK_GAP_MIN after the
    first); else none."""
    k = kontrola or {}
    if not k:
        return start - dt.timedelta(minutes=CHECK_BEFORE_MIN)
    if not k.get("sklad_znany") and not k.get("druga"):
        first = dt.datetime.fromisoformat(k["czas"].replace("Z", "+00:00")) if k.get("czas") else start
        return max(start - dt.timedelta(minutes=LINEUP_BEFORE_MIN), first + dt.timedelta(minutes=RECHECK_GAP_MIN))
    return None


def mains(pr: sqlite3.Connection, now: dt.datetime, hours: float = HORIZON_H) -> list[dict]:
    """Logged main builders of matches starting within ``hours``, with their payloads and the next check time."""
    out = []
    for r in pr.execute("select day, key, fixture_id, start, legs, payload from extras where kind = 'bb' "
                        "and start > ? and start < ? order by start", (sd.iso(now), sd.iso(now + dt.timedelta(hours=hours)))):
        p = json.loads(r["payload"] or "{}")
        if not p.get("glowny"):
            continue
        start = dt.datetime.fromisoformat(r["start"].replace("Z", "+00:00"))
        out.append({"day": r["day"], "key": r["key"], "fixture_id": r["fixture_id"], "start": start,
                    "legs": json.loads(r["legs"]), "p": p, "due": when_due(start, p.get("kontrola"))})
    return out


def next_check(rows: list[dict], now: dt.datetime) -> dt.datetime | None:
    """The earliest check still to do (at least two minutes from now: a scheduled run cannot start in the past)."""
    times = [r["due"] for r in rows if r["due"] is not None]
    if not times:
        return None
    return max(min(times), now + dt.timedelta(minutes=2))


def usual_eleven(squad: list[dict]) -> list[dict]:
    return sorted(squad, key=lambda p: (-p["s5"], -p["m5"]))[:USUAL]


def lineup_of(fixture: dict) -> dict[int, dict]:
    """team id -> {'xi': {player id: name}, 'lawka': {player id: name}} from a fixture object's line-ups."""
    out = {}
    for lu in fixture.get("lineups") or []:
        tid = (lu.get("team") or {}).get("id")
        xi = {x["player"]["id"]: x["player"].get("name") for x in lu.get("startXI") or [] if (x.get("player") or {}).get("id")}
        bench = {x["player"]["id"]: x["player"].get("name") for x in lu.get("substitutes") or [] if (x.get("player") or {}).get("id")}
        if tid and len(lu.get("startXI") or []) >= 11:          # a starter may come without an id: the eleven is still known
            out[tid] = {"xi": xi, "lawka": bench}
    return out


def players_for(fid: int, legs: list[dict], kadry: dict) -> dict[str, list[dict]]:
    """The likely starters as the builder's random draws had them: the app export's two per side, in its order (the
    order sets each player's random numbers), with the builder's own share (leg 'udzial') where it has one; a
    builder's player missing there (left out by a later run) goes first, with the squad file's share."""
    out: dict[str, list[dict]] = {"home": [], "away": []}
    try:
        for rec in json.loads((DATA / "st_mecze.json").read_text(encoding="utf-8")):
            if rec.get("id") == fid:
                for side, lst in (rec.get("zawodnicy") or {}).items():
                    out[side] = [{"id": p["id"], "s_sot": float(p["s"]), "name": p.get("n")} for p in lst or []]
    except (OSError, ValueError):
        pass
    squad = {p["id"]: p for side in ("home", "away") for p in (kadry.get(side) or [])}
    for l in legs:
        if l.get("stat") != "player_sot":
            continue
        pid, side = int(l["player_id"]), l["scope"]
        mine = next((p for p in out[side] if p["id"] == pid), None)
        if mine is None:
            mine = {"id": pid, "s_sot": float((squad.get(pid) or {}).get("s_sot") or 0.1), "name": l.get("player")}
            out[side].insert(0, mine)
        if l.get("udzial"):
            mine["s_sot"] = float(l["udzial"])
    return out


def revalue(pr: sqlite3.Connection, od: sqlite3.Connection, fid: int, legs: list[dict], players: dict[str, list[dict]],
            cut: float) -> dict | None:
    """The builder's chance by the model and by the bookmakers' lines with the latest odds: the model's means of
    the last forecast (with its market factors, as stats_sure.run), the goals from the latest match odds, the
    same random draws as at the proposal (seed = fixture)."""
    dims, corr = ss.load_corr()
    stats_ = [d.split("_", 1)[1] for d in dims if d.startswith("home_") and not d.endswith("_goals")]
    means, alphas = {}, {}
    for s in stats_:
        q = pr.execute("select mu_home, mu_away, alpha, alpha_total, k_home, k_away from predictions where fixture_id = ? "
                       "and stat = ? order by made_at desc limit 1", (fid, s)).fetchone()
        if q is None:
            return None
        means[f"home_{s}"], means[f"away_{s}"] = q["mu_home"] * (q["k_home"] or 1.0), q["mu_away"] * (q["k_away"] or 1.0)
        alphas[f"home_{s}"] = alphas[f"away_{s}"] = q["alpha"]
        alphas[f"total_{s}"] = q["alpha_total"]
    rows = sd.latest_odds(od, [fid]).get(fid, [])
    start_goals = (1.45, 1.15)
    try:
        for rec in json.loads((DATA / "st_mecze.json").read_text(encoding="utf-8")):
            if rec.get("id") == fid and (rec.get("gole") or {}).get("srednie"):
                start_goals = tuple(rec["gole"]["srednie"])
    except (OSError, ValueError):
        pass
    res = sd.result_probs(rows)
    means["home_goals"], means["away_goals"] = ss.goals_from_market(start_goals[0], start_goals[1], res[0], res[2]) if res else start_goals
    alphas["home_goals"] = alphas["away_goals"] = 1e-4
    seed = int(fid) % (2 ** 31)
    X = ss.simulate(means, alphas, dims, corr, seed=seed)
    ss.add_players(X, players, seed)
    mkt, _ = mk.market_means(means, alphas, rows, dims, corr)
    X_mkt = ss.simulate(mkt, alphas, dims, corr, seed=seed)
    ss.add_players(X_mkt, players, seed)
    try:
        p_model = ss.corrected(mk.joint_chance(X, legs), ss.BB_MISS * ss.selection_miss("bb"))
        p_mkt = mk.joint_chance(X_mkt, legs)
    except KeyError:
        return None
    est = mk.estimated_price(p_mkt, cut)
    fetched = max((r.get("fetched_at") or "" for r in rows), default="") or None
    return {"p_model": round(p_model, 4), "p_rynek": round(p_mkt, 4), "kurs_szac": est,
            "kurs_min_z_podatkiem": round(1 / (p_model * sd.TAX), 2) if p_model > 0 else None, "kursy_z": fetched}


def check(item: dict, fixture: dict | None, names: dict, kadry: dict, absent: dict[int, dict],
          value: dict | None, now: dt.datetime, fetched: bool = True) -> dict:
    """The check of one main builder: what changed and the verdict, with the reasons in Polish."""
    p, legs = item["p"], item["legs"]
    prev = p.get("kontrola") or {}
    bad, warn, info = [], [], []
    status = ((fixture or {}).get("fixture") or {}).get("status", {}).get("short") or prev.get("status") or "NS"
    lineups = lineup_of(fixture or {})
    teams = {"home": (names.get("home_id"), names.get("home")), "away": (names.get("away_id"), names.get("away"))}
    known = all(teams[s][0] in lineups for s in teams)
    if status in sd.DEAD:
        bad.append("Mecz przełożony albo odwołany.")
    elif status in sd.FINISHED or status in LIVE:
        warn.append("Mecz już się zaczął.")
    sklady = {}
    if known:
        for side, (tid, tname) in teams.items():
            xi = lineups[tid]["xi"]
            usual = usual_eleven(kadry.get(side) or [])
            if len(usual) < USUAL:
                continue
            present = [u for u in usual if u["id"] in xi]
            missing = [u["n"] for u in usual if u["id"] not in xi]
            sklady[side] = {"w_skladzie": len(present), "z": USUAL, "brak": missing[:6]}
            if len(present) <= ROTATION_MAX:
                warn.append(f"{tname}: w składzie tylko {len(present)} z {USUAL} zwykle grających (brak: {', '.join(missing[:4])}).")
            elif missing:
                info.append(f"{tname}: {len(present)} z {USUAL} zwykle grających (brak: {', '.join(missing[:3])}).")
            else:
                info.append(f"{tname}: najmocniejszy skład.")
    zaw = []
    for l in legs:
        if l.get("stat") != "player_sot":
            continue
        pid, name = int(l["player_id"]), l.get("player")
        side = l["scope"]
        tid = teams.get(side, (None, None))[0]
        if known and tid in lineups:
            if pid in lineups[tid]["xi"]:
                zaw.append({"n": name, "stan": "gra"})
                info.append(f"{name} w podstawowym składzie.")
            elif pid in lineups[tid]["lawka"]:
                zaw.append({"n": name, "stan": "lawka"})
                bad.append(f"{name} zaczyna na ławce: zdarzenie z nim wejdzie rzadko.")
            else:
                zaw.append({"n": name, "stan": "poza"})
                bad.append(f"{name} poza kadrą meczową: bukmacher zwykle unieważnia zakład na zawodnika, który nie zagrał.")
        else:
            out = absent.get(pid)
            if out:
                zaw.append({"n": name, "stan": "niepewny" if out["type"] == "Questionable" else "nie_zagra"})
                (warn if out["type"] == "Questionable" else bad).append(
                    f"{name}: {sd.injury_pl(out['reason'])}" + (" (występ niepewny)." if out["type"] == "Questionable" else "."))
            else:
                zaw.append({"n": name, "stan": "nieznany"})
    if value:
        drop = p["p_model"] - value["p_model"]
        if value["p_model"] < ss.MAIN_MIN_P - 0.05:
            bad.append(f"Szansa spadła do {round(value['p_model'] * 100)}% (była {round(p['p_model'] * 100)}%).")
        elif drop >= DROP_WARN:
            warn.append(f"Szansa spadła z {round(p['p_model'] * 100)}% do {round(value['p_model'] * 100)}%.")
    if not fetched:
        info.append("Nie udało się pobrać danych meczu (limit zapytań API).")
    elif not known and status not in sd.DEAD:
        info.append("Składów jeszcze nie ma." + (" Sprawdzę je jeszcze raz bliżej meczu." if not prev else ""))
    out = {"czas": sd.iso(now), "minut_przed": round((item["start"] - now).total_seconds() / 60), "status": status,
           "sklad_znany": known, "druga": bool(prev), "sklady": sklady, "zawodnicy": zaw,
           "werdykt": "nie_graj" if bad else "uwaga" if warn else "ok", "powody": bad + warn, "info": info,
           "p_model_rano": p.get("p_model"), "p_rynek_rano": p.get("p_rynek")}
    if value:
        out.update(value)
    elif prev:
        out.update({k: prev[k] for k in ("p_model", "p_rynek", "kurs_szac", "kurs_min_z_podatkiem", "kursy_z") if k in prev})
    return out


def patch_export(results: list[tuple[dict, dict]]) -> int:
    """The checks into the app's data/st_typy.json (the builders by key), written as the daily export writes it."""
    path = DATA / "st_typy.json"
    try:
        typy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for item, k in results:
        key = item["p"].get("klucz")
        for b in (typy.get(item["day"]) or {}).get("bb") or []:
            if b.get("klucz") == key:
                b["kontrola"] = k
                n += 1
    path.write_text(json.dumps(typy, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return n


def local(t: dt.datetime) -> str:
    return t.astimezone().replace(microsecond=0).isoformat()


def run(args: argparse.Namespace) -> None:
    now = sd.utcnow()
    fx, od, pr = sd.init_dbs()
    rows = mains(pr, now)
    due = [r for r in rows if r["due"] is not None and r["due"] <= now + dt.timedelta(minutes=EARLY_MIN)]
    if args.plan or not due:
        if not args.plan:
            print("checks: nothing due now")
        nxt = next_check(rows, now)
        print(f"NEXT={local(nxt) if nxt else 'none'}")
        return
    client = sd.open_client(2 * len({r['fixture_id'] for r in due}) + 1, 3000)
    try:
        kadry_all = json.loads((sd.STATS_DIR / "kadry.json").read_text(encoding="utf-8")).get("mecze", {})
    except (OSError, ValueError):
        kadry_all = {}
    absent_all = sd.injuries_of(fx, sorted({r["fixture_id"] for r in due}))
    ledger = sd.load_ledger(str(sd.DEFAULT_LEDGER) if sd.DEFAULT_LEDGER.exists() else None)
    cut, _ = sd.bookmaker_cut(pr, ledger)
    raw = sd.STATS_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    fixtures: dict[int, dict | None] = {}
    results = []
    for item in due:
        fid = item["fixture_id"]
        if fid not in fixtures:
            fixtures[fid] = None
            try:
                data = client.get("/fixtures", {"id": fid})
                stamp = sd.iso(sd.utcnow()).replace(":", "")
                with gzip.open(raw / f"prematch_{fid}_{stamp}.json.gz", "wt", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                resp = data.get("response") or []
                if resp:
                    fixtures[fid] = resp[0]
                    fx.execute("insert or replace into fixtures values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               sd.fixture_row(resp[0], sd.iso(sd.utcnow())))
                    fx.commit()
            except (RuntimeError, OSError) as exc:
                print(f"fixture {fid}: {exc}")
            first = not item["p"].get("kontrola")
            last = od.execute("select max(fetched_at) from odds_fetch where fixture_id = ?", (fid,)).fetchone()[0]
            age = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 60 if last else 1e9
            if first and age > (ODDS_FRESH_FREE_MIN if client.free else ODDS_FRESH_MIN):
                try:
                    sd.store_odds(od, fid, client.get("/odds", {"fixture": fid}))
                except (RuntimeError, OSError) as exc:
                    print(f"odds {fid}: {exc}")
        f = fx.execute("select home_id, home, away_id, away from fixtures where fixture_id = ?", (fid,)).fetchone()
        names = dict(f) if f else {}
        kadry = kadry_all.get(str(fid)) or {}
        players = players_for(fid, item["legs"], kadry)
        lineups = lineup_of(fixtures[fid] or {})
        for side, lst in players.items():         # a player on the bench gets a sub's part of his share, one out none
            tid = names.get(f"{side}_id")
            for pl in lst:
                if tid in lineups and pl["id"] in lineups[tid]["lawka"]:
                    pl["s_sot"] *= BENCH_SHARE
                elif tid in lineups and pl["id"] not in lineups[tid]["xi"]:
                    pl["s_sot"] = 0.0
        value = revalue(pr, od, fid, item["legs"], players, cut)
        k = check(item, fixtures[fid], names, kadry, absent_all.get(fid, {}), value, now, fixtures[fid] is not None)
        item["p"]["kontrola"] = k
        pr.execute("update extras set payload = ? where day = ? and kind = 'bb' and key = ?",
                   (json.dumps(item["p"], ensure_ascii=False, default=float), item["day"], item["key"]))
        pr.commit()
        results.append((item, k))
        price = f" · szac. kurs teraz {k['kurs_szac']} (przy propozycji {item['p'].get('kurs_szac')})" if k.get("kurs_szac") else ""
        print(f"{item['p'].get('mecz')} {local(item['start'])[11:16]} | {k['werdykt']} | szansa "
              f"{round((k.get('p_model') or item['p']['p_model']) * 100)}% (była {round(item['p']['p_model'] * 100)}%){price}"
              f" | składy {'znane' if k['sklad_znany'] else 'nieznane'} | " + " ".join(k["powody"] + k["info"]))
    n = patch_export(results)
    print(f"checks: {len(results)} main builders checked, {n} updated in the app data; "
          f"requests used {client.used}, account remaining {client.remaining}")
    nxt = next_check(mains(pr, sd.utcnow()), sd.utcnow())
    print(f"NEXT={local(nxt) if nxt else 'none'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plan", action="store_true", help="only print the time of the next check (no requests)")
    args = p.parse_args()
    if args.plan:
        run(args)
        return
    with sd.run_lock(wait_min=12.0):
        run(args)


if __name__ == "__main__":
    main()
