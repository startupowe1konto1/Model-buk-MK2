#!/usr/bin/env python3
"""
Budzetowa sonda RapidAPI 'flashscore4' (plan Free = 500 req/MIESIAC, ~16/dzien).
Domyslny budzet: 12 requestow. Kazdy jest liczony i raportowany.

Cel: ustalic empirycznie, czy ten feed daje cokolwiek, czego nie ma
w darmowym Football-Data.co.uk - a konkretnie:
  * kursy (jakiekolwiek),
  * statystyki meczowe (corners / SOT),
  * sklady, kontuzje,
  * ligi, ktorych Football-Data nie pokrywa (np. Ekstraklasa ze statystykami).

Klucz:
    echo "TWOJ_KLUCZ" > ~/.rapidapi_key && chmod 600 ~/.rapidapi_key
    python3 03_flashscore4_probe.py --key-file ~/.rapidapi_key

Plan wywolan bez wysylania:
    python3 03_flashscore4_probe.py --dry-run
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

HOST = "flashscore4.p.rapidapi.com"

# Endpointy widoczne w sidebarze RapidAPI (grupy: Search / Match / Player /
# Tournament / General / Team). Sciezki sa hipotezami - skrypt sam sprawdza,
# ktore odpowiadaja 200, i zapisuje surowe JSON-y do analizy.
CANDIDATES = [
    ("General",    "/v2/general/sports",            {}),
    ("Match",      "/v2/matches/by-day",            {"day": "0", "sport_id": "1", "timezone": "Europe/Warsaw"}),
    ("Match",      "/v2/matches/live",              {"sport_id": "1", "timezone": "Europe/Warsaw"}),
    ("Search",     "/v2/search",                    {"query": "Lech Poznan", "sport_id": "1"}),
]

# Te wymagaja id z wczesniejszych odpowiedzi - uzupelniane dynamicznie.
DETAIL_PATHS = [
    "/v2/match/detail", "/v2/match/statistics", "/v2/match/odds",
    "/v2/match/lineups", "/v2/match/summary", "/v2/match/h2h",
]

INTERESTING = ["odds", "corner", "shot", "target", "card", "lineup",
               "injur", "bookmaker", "statistic"]


class Client:
    def __init__(self, key, budget, dry):
        self.key, self.budget, self.dry = key, budget, dry
        self.used = 0
        self.log = []

    def get(self, path, params=None):
        if self.used >= self.budget:
            print(f"  !! BUDZET WYCZERPANY ({self.budget}) - pomijam {path}")
            return None, "budget"
        qs = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}"
                            for k, v in (params or {}).items()) if params else ""
        url = f"https://{HOST}{path}{qs}"
        if self.dry:
            print(f"  [dry-run] GET {url}")
            self.used += 1
            return None, "dry"
        req = urllib.request.Request(url, headers={
            "x-rapidapi-key": self.key, "x-rapidapi-host": HOST})
        self.used += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
            self.log.append((path, 200))
            time.sleep(0.5)
            return body, 200
        except urllib.error.HTTPError as e:
            self.log.append((path, e.code))
            time.sleep(0.3)
            return None, e.code
        except Exception as ex:
            self.log.append((path, str(ex)[:40]))
            return None, "err"


def walk_keys(obj, prefix="", out=None, depth=0):
    if out is None:
        out = set()
    if depth > 5:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(f"{prefix}{k}")
            walk_keys(v, prefix + k + ".", out, depth + 1)
    elif isinstance(obj, list) and obj:
        walk_keys(obj[0], prefix, out, depth + 1)
    return out


def main():
    import urllib.parse  # noqa  (uzywane w Client.get)
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--out", default="../data/flashscore4")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = ""
    if not args.dry_run:
        key = (open(os.path.expanduser(args.key_file)).read().strip()
               if args.key_file else os.environ.get("RAPIDAPI_KEY", "").strip())
        if not key:
            sys.exit("Brak klucza. --key-file albo RAPIDAPI_KEY. Albo --dry-run.")

    os.makedirs(args.out, exist_ok=True)
    c = Client(key, args.budget, args.dry_run)
    all_keys = set()
    match_id = None

    print("=" * 68)
    print(f"SONDA flashscore4  |  budzet {args.budget} req  (plan Free = 500/mies)")
    print("=" * 68)

    for group, path, params in CANDIDATES:
        print(f"\n[{group}] {path}")
        body, code = c.get(path, params)
        if code != 200:
            print(f"  -> {code}")
            continue
        fn = path.strip("/").replace("/", "_") + ".json"
        with open(os.path.join(args.out, fn), "w") as f:
            json.dump(body, f, indent=1)[:0] if False else json.dump(body, f, indent=1)
        ks = walk_keys(body)
        all_keys |= ks
        print(f"  -> 200, {len(ks)} unikalnych sciezek kluczy, zapisano {fn}")
        hits = sorted(k for k in ks if any(i in k.lower() for i in INTERESTING))
        if hits:
            print(f"  CIEKAWE KLUCZE: {', '.join(hits[:12])}")
        if match_id is None:
            for k in ks:
                pass
            txt = json.dumps(body)
            import re
            m = re.search(r'"(?:id|match_?id)"\s*:\s*"([A-Za-z0-9]{6,10})"', txt)
            if m:
                match_id = m.group(1)
                print(f"  wykryte match_id do testow szczegolowych: {match_id}")

    if match_id or args.dry_run:
        mid = match_id or "XXXXXXXX"
        print(f"\n--- endpointy szczegolowe dla match_id={mid} ---")
        for p in DETAIL_PATHS:
            if c.used >= c.budget:
                print(f"  (budzet wyczerpany, pomijam {p})")
                continue
            body, code = c.get(p, {"match_id": mid})
            status = "200" if code == 200 else str(code)
            note = ""
            if code == 200 and body is not None:
                ks = walk_keys(body)
                all_keys |= ks
                hits = sorted(k for k in ks if any(i in k.lower() for i in INTERESTING))
                note = f"  klucze: {', '.join(hits[:8])}" if hits else "  (brak ciekawych kluczy)"
                fn = p.strip("/").replace("/", "_") + ".json"
                with open(os.path.join(args.out, fn), "w") as f:
                    json.dump(body, f, indent=1)
            print(f"  {status:4s} {p}{note}")

    print("\n" + "=" * 68)
    print("WERDYKT")
    print("=" * 68)
    for kw in ["odds", "bookmaker", "corner", "shot", "card", "lineup", "injur"]:
        found = sorted(k for k in all_keys if kw in k.lower())
        print(f"  {kw:10s} {'TAK' if found else 'NIE ':4s} {', '.join(found[:5])}")
    print(f"\nZUZYTO: {c.used}/{args.budget} requestow "
          f"(z 500/mies zostaje ~{500 - c.used})")
    print(f"Surowe JSON-y: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    import urllib.parse
    main()
