#!/usr/bin/env python3
"""
Budzetowa sonda API-Football. Odpowiada na pytania Fazy -1 przy minimalnej
liczbie requestow (plan Free = 100/dzien).

Odpowiada na:
  1. Jaki mam plan i ile requestow zostalo?                    -> /status          (1)
  2. Czy sa polscy bukmacherzy (STS/Superbet/Fortuna/Betclic)? -> /odds/bookmakers (1)
  3. Czy sa rynki corners / SOT / cards?                       -> /odds/bets       (1)
  4. Jakie jest realne pokrycie statystyk (corners, SOT)?      -> /fixtures/statistics (N)
  5. Jakie rynki faktycznie wracaja dla konkretnego meczu?     -> /odds            (N)
  6. Jak gleboka jest historia na planie Free?                 -> /fixtures        (kilka)

Klucz (NIE wpisuj go w kod ani nie wklejaj do czatu):
    echo "TWOJ_KLUCZ" > ~/.apifootball_key && chmod 600 ~/.apifootball_key
    python3 02_apifootball_probe.py --key-file ~/.apifootball_key

albo:  export API_FOOTBALL_KEY=...   &&  python3 02_apifootball_probe.py

Tryb sucho (nic nie wysyla, pokazuje plan wywolan):
    python3 02_apifootball_probe.py --dry-run
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import Counter

DIRECT_HOST = "v3.football.api-sports.io"
RAPID_HOST = "api-football-v1.p.rapidapi.com"

# Bukmacherzy dostepni w PL - szukamy ich w /odds/bookmakers
PL_BOOKS = ["sts", "superbet", "fortuna", "betclic", "totalbet", "etoto", "lvbet", "forbet"]
# Slowa kluczowe rynkow, ktore nas interesuja - szukamy w /odds/bets
TARGET_MARKETS = ["corner", "shot", "card", "booking", "offside", "foul", "team total"]

# id lig w API-Football (stabilne)
LEAGUES = {"Premier League": 39, "Ekstraklasa": 106, "Ligue 1": 61,
           "LaLiga": 140, "Serie A": 135, "Bundesliga": 78}


class Client:
    def __init__(self, key, mode, budget, dry_run=False):
        self.key = key
        self.mode = mode
        self.budget = budget
        self.used = 0
        self.dry_run = dry_run
        self.host = RAPID_HOST if mode == "rapid" else DIRECT_HOST

    def get(self, path, params=None):
        if self.used >= self.budget:
            raise RuntimeError(f"BUDZET WYCZERPANY ({self.budget} req) - zatrzymuje sie.")
        qs = ""
        if params:
            qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        url = f"https://{self.host}/{path.lstrip('/')}{qs}"
        if self.dry_run:
            print(f"  [dry-run] GET {url}")
            self.used += 1
            return {"response": [], "results": 0}
        if self.mode == "rapid":
            headers = {"x-rapidapi-key": self.key, "x-rapidapi-host": RAPID_HOST}
        else:
            headers = {"x-apisports-key": self.key}
        req = urllib.request.Request(url, headers=headers)
        self.used += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print(f"  ! HTTP {e.code} dla {path}")
            return {"response": [], "errors": str(e), "results": 0}
        if body.get("errors"):
            print(f"  ! API errors: {body['errors']}")
        time.sleep(0.4)
        return body


def save(outdir, name, obj):
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--mode", choices=["direct", "rapid"], default="direct")
    ap.add_argument("--budget", type=int, default=25, help="twardy limit requestow")
    ap.add_argument("--sample", type=int, default=5, help="ile meczow probkowac")
    ap.add_argument("--league", default="Ekstraklasa", choices=list(LEAGUES))
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--out", default="../data/apifootball")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = ""
    if not args.dry_run:
        if args.key_file:
            key = open(os.path.expanduser(args.key_file)).read().strip()
        else:
            key = os.environ.get("API_FOOTBALL_KEY", "").strip()
        if not key:
            sys.exit("Brak klucza. Uzyj --key-file albo API_FOOTBALL_KEY. "
                     "Albo odpal z --dry-run zeby zobaczyc plan wywolan.")

    c = Client(key, args.mode, args.budget, args.dry_run)
    out = args.out
    report = {}

    print("=" * 68)
    print(f"SONDA API-FOOTBALL  |  budzet {args.budget} req  |  tryb {args.mode}")
    print("=" * 68)

    # --- 1. status ------------------------------------------------------
    print("\n[1/6] /status")
    st = c.get("status")
    resp = st.get("response") or {}
    if resp:
        sub = resp.get("subscription", {})
        req = resp.get("requests", {})
        print(f"  plan={sub.get('plan')} aktywna_do={sub.get('end')}")
        print(f"  requesty dzis: {req.get('current')}/{req.get('limit_day')}")
        report["status"] = resp
    save(out, "status.json", st)

    # --- 2. bukmacherzy -------------------------------------------------
    print("\n[2/6] /odds/bookmakers  <- CZY SA POLSCY BUKMACHERZY")
    bm = c.get("odds/bookmakers")
    books = bm.get("response", [])
    save(out, "bookmakers.json", bm)
    print(f"  lacznie bukmacherow: {len(books)}")
    found = [b for b in books if any(p in (b.get("name") or "").lower() for p in PL_BOOKS)]
    if found:
        for b in found:
            print(f"  ZNALEZIONO: id={b['id']:4d}  {b['name']}")
    else:
        print("  >>> BRAK polskich bukmacherow na liscie. <<<")
    print("  pierwszych 15:", ", ".join((b.get("name") or "?") for b in books[:15]))
    report["bookmakers_total"] = len(books)
    report["bookmakers_pl"] = [b["name"] for b in found]

    # --- 3. rynki -------------------------------------------------------
    print("\n[3/6] /odds/bets  <- CZY SA RYNKI CORNERS / SOT / CARDS")
    bets = c.get("odds/bets")
    blist = bets.get("response", [])
    save(out, "bets.json", bets)
    print(f"  lacznie typow zakladow: {len(blist)}")
    hits = {}
    for kw in TARGET_MARKETS:
        m = [b for b in blist if kw in (b.get("name") or "").lower()]
        hits[kw] = [f"{b['id']}:{b.get('name') or '?'}" for b in m]
        flag = "OK " if m else "BRAK"
        print(f"  [{flag}] {kw:10s} -> {len(m):3d}  {'; '.join(x.split(':',1)[1] for x in m[:4])}")
    report["markets"] = hits

    # --- 4. fixtures ----------------------------------------------------
    lid = LEAGUES[args.league]
    print(f"\n[4/6] /fixtures  liga={args.league}(id {lid}) sezon={args.season}")
    fx = c.get("fixtures", {"league": lid, "season": args.season, "status": "FT"})
    fixtures = fx.get("response", [])
    save(out, f"fixtures_{lid}_{args.season}.json", fx)
    print(f"  meczow zakonczonych: {len(fixtures)}  (1 request na cala lige/sezon!)")
    if not fixtures:
        print("  >>> Pusto. Plan Free moze nie miec tego sezonu (retention). <<<")
    report["fixtures_count"] = len(fixtures)

    sample = [f["fixture"]["id"] for f in fixtures[: args.sample]]

    # --- 5. pokrycie statystyk -----------------------------------------
    print(f"\n[5/6] /fixtures/statistics  <- POKRYCIE CORNERS / SOT  (probka {len(sample)})")
    stat_names = Counter()
    have_corners = have_sot = 0
    for fid in sample:
        s = c.get("fixtures/statistics", {"fixture": fid})
        teams = s.get("response", [])
        names = set()
        for t in teams:
            for item in t.get("statistics", []):
                if item.get("value") is not None:
                    names.add(item["type"])
        stat_names.update(names)
        if any("corner" in n.lower() for n in names):
            have_corners += 1
        if any("shots on goal" in n.lower() or "on target" in n.lower() for n in names):
            have_sot += 1
    if sample:
        print(f"  corners obecne: {have_corners}/{len(sample)}   SOT obecne: {have_sot}/{len(sample)}")
        print("  wszystkie typy statystyk:", ", ".join(sorted(stat_names)))
    report["stat_coverage"] = {"n": len(sample), "corners": have_corners, "sot": have_sot,
                               "types": sorted(stat_names)}

    # --- 6. realne rynki na meczu --------------------------------------
    print(f"\n[6/6] /odds  <- JAKIE RYNKI REALNIE WRACAJA (probka {min(2, len(sample))})")
    seen_bets, seen_books = Counter(), Counter()
    for fid in sample[:2]:
        o = c.get("odds", {"fixture": fid})
        for row in o.get("response", []):
            for bk in row.get("bookmakers", []):
                seen_books[bk.get("name")] += 1
                for b in bk.get("bets", []):
                    seen_bets[b.get("name")] += 1
        save(out, f"odds_fixture_{fid}.json", o)
    if seen_bets:
        print(f"  bukmacherzy w odpowiedzi: {', '.join(list(seen_books)[:12])}")
        print(f"  rynki ({len(seen_bets)}):")
        for n, k in seen_bets.most_common(30):
            mark = "  <<<" if any(t in n.lower() for t in TARGET_MARKETS) else ""
            print(f"    - {n}{mark}")
    else:
        print("  brak kursow dla probki (mecz zakonczony -> kursy mogly zniknac)")
    report["odds_markets_seen"] = list(seen_bets)
    report["odds_books_seen"] = list(seen_books)

    save(out, "PODSUMOWANIE.json", report)
    print("\n" + "=" * 68)
    print(f"ZUZYTO REQUESTOW: {c.used}/{args.budget}")
    print(f"Surowe odpowiedzi + PODSUMOWANIE.json w: {os.path.abspath(out)}")
    print("=" * 68)


if __name__ == "__main__":
    main()
