#!/usr/bin/env python3
"""Decydujacy test: czy dla REALNEGO meczu wracaja rynki corners/SOT/cards
i u ktorych bukmacherow. Budzet domyslny: 6 requestow."""
import json, os, sys, time, urllib.request, urllib.error
from collections import Counter, defaultdict

KEY = open(os.path.expanduser("~/.apifootball_key")).read().strip()
HOST = "v3.football.api-sports.io"
OUT = "data/apifootball"
used = 0
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 6

def get(path, **params):
    global used
    if used >= BUDGET:
        print(f"  [budzet {BUDGET} wyczerpany]"); return None
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://{HOST}/{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    used += 1
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code}"); return None
    if b.get("errors"): print("  ! errors:", b["errors"])
    time.sleep(0.4)
    return b

TARGETS = ["corner", "shot", "card", "foul", "offside"]

for lname, lid in [("Ekstraklasa", 106), ("Premier League", 39)]:
    print("=" * 66)
    print(f"{lname} (id {lid}) - najblizsze mecze")
    print("=" * 66)
    fx = get("fixtures", league=lid, next=3)
    if not fx or not fx.get("response"):
        print("  brak nadchodzacych meczow"); continue
    for f in fx["response"][:1]:
        fid = f["fixture"]["id"]
        home = f["teams"]["home"]["name"]; away = f["teams"]["away"]["name"]
        print(f"\n  mecz: {home} - {away}  ({f['fixture']['date'][:16]})  id={fid}")
        o = get("odds", fixture=fid)
        if not o: break
        json.dump(o, open(f"{OUT}/odds_live_{fid}.json", "w"), indent=1)
        resp = o.get("response", [])
        if not resp:
            print("  >>> BRAK KURSOW dla tego meczu <<<"); continue
        per_book = defaultdict(set)
        for row in resp:
            for bk in row.get("bookmakers", []):
                for bet in bk.get("bets", []):
                    per_book[bk.get("name") or "?"].add(bet.get("name") or "?")
        print(f"  bukmacherow z kursami: {len(per_book)}")
        allbets = Counter()
        for bk, bets in per_book.items():
            allbets.update(bets)
            hits = sorted(b for b in bets if any(t in b.lower() for t in TARGETS))
            mark = f"  <-- {len(hits)} rynkow z naszej listy" if hits else ""
            print(f"    {bk:16s} {len(bets):3d} rynkow{mark}")
            for h in hits[:8]:
                print(f"         * {h}")
        print(f"\n  UNIKALNE RYNKI LACZNIE: {len(allbets)}")
        tgt = sorted(b for b in allbets if any(t in b.lower() for t in TARGETS))
        print(f"  RYNKI CORNERS/SOT/CARDS/FOULS: {len(tgt)}")
        for t in tgt: print(f"    - {t}")

print(f"\nZUZYTO {used} requestow.")
