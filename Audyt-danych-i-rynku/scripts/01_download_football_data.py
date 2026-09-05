#!/usr/bin/env python3
"""
Pobiera cala historie z Football-Data.co.uk.

Dwa zbiory:
  * MAIN  (mmz4281) - 22 ligi europejskie, PELNE statystyki (strzaly, SOT, rozne,
    faule, kartki, sedzia) + kursy opening i closing z ~8 bukmacherow.
  * EXTRA (new/)    - ~20 dodatkowych krajow (w tym POLSKA), tylko wyniki + kursy,
    BEZ statystyk meczowych.

Uzycie:
    python3 01_download_football_data.py --out ../data/fd --seasons 10
"""
import argparse
import csv
import io
import os
import sys
import time
import urllib.request
import urllib.error

MAIN_BASE = "https://www.football-data.co.uk/mmz4281"
EXTRA_BASE = "https://www.football-data.co.uk/new"

# div -> opis. Te maja pelne statystyki meczowe.
MAIN_DIVS = {
    "E0": "England Premier League", "E1": "England Championship",
    "E2": "England League One", "E3": "England League Two", "EC": "England Conference",
    "SC0": "Scotland Premiership", "SC1": "Scotland Championship",
    "SC2": "Scotland League One", "SC3": "Scotland League Two",
    "D1": "Germany Bundesliga", "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A", "I2": "Italy Serie B",
    "SP1": "Spain LaLiga", "SP2": "Spain Segunda",
    "F1": "France Ligue 1", "F2": "France Ligue 2",
    "N1": "Netherlands Eredivisie", "B1": "Belgium Pro League",
    "P1": "Portugal Primeira Liga", "T1": "Turkey Super Lig", "G1": "Greece Super League",
}

# Te NIE maja statystyk meczowych - tylko wynik + kursy.
EXTRA_COUNTRIES = {
    "POL": "Poland Ekstraklasa", "AUT": "Austria Bundesliga", "DNK": "Denmark Superliga",
    "NOR": "Norway Eliteserien", "SWE": "Sweden Allsvenskan", "SWZ": "Switzerland Super League",
    "ROU": "Romania Liga I", "RUS": "Russia Premier League", "FIN": "Finland Veikkausliiga",
    "IRL": "Ireland Premier Division", "BRA": "Brazil Serie A", "ARG": "Argentina Primera",
    "USA": "USA MLS", "MEX": "Mexico Liga MX", "JPN": "Japan J1", "CHN": "China Super League",
}


def season_codes(n):
    """Ostatnie n sezonow jako kody Football-Data, np. '2526'."""
    out = []
    end = 26  # sezon 2025/26
    for i in range(n):
        y2 = end - i
        y1 = y2 - 1
        out.append(f"{y1:02d}{y2:02d}")
    return out


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
            with urllib.request.urlopen(req, timeout=45) as r:
                if r.status != 200:
                    return None
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../data/fd")
    ap.add_argument("--seasons", type=int, default=10, help="ile ostatnich sezonow (MAIN)")
    ap.add_argument("--skip-extra", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, "main"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "extra"), exist_ok=True)

    seasons = season_codes(args.seasons)
    ok = miss = 0
    total_rows = 0

    print(f"MAIN: {len(MAIN_DIVS)} lig x {len(seasons)} sezonow "
          f"({seasons[-1]} -> {seasons[0]})\n")

    for div, name in MAIN_DIVS.items():
        rows_div = 0
        for s in seasons:
            path = os.path.join(args.out, "main", f"{div}_{s}.csv")
            if os.path.exists(path) and os.path.getsize(path) > 500:
                rows_div += sum(1 for _ in open(path, encoding="latin-1")) - 1
                ok += 1
                continue
            data = fetch(f"{MAIN_BASE}/{s}/{div}.csv")
            if not data or len(data) < 500:
                miss += 1
                continue
            with open(path, "wb") as f:
                f.write(data)
            rows_div += data.decode("latin-1").count("\n") - 1
            ok += 1
            time.sleep(0.25)
        total_rows += rows_div
        print(f"  {div:4s} {name:32s} ~{rows_div:5d} meczow")

    if not args.skip_extra:
        print(f"\nEXTRA: {len(EXTRA_COUNTRIES)} krajow (bez statystyk meczowych)\n")
        for code, name in EXTRA_COUNTRIES.items():
            path = os.path.join(args.out, "extra", f"{code}.csv")
            if os.path.exists(path) and os.path.getsize(path) > 500:
                print(f"  {code:4s} {name:32s} (cache)")
                continue
            data = fetch(f"{EXTRA_BASE}/{code}.csv")
            if not data or len(data) < 500:
                print(f"  {code:4s} {name:32s} BRAK")
                continue
            with open(path, "wb") as f:
                f.write(data)
            n = data.decode("latin-1").count("\n") - 1
            print(f"  {code:4s} {name:32s} ~{n:5d} meczow")
            time.sleep(0.25)

    print(f"\nPobrano plikow MAIN: {ok}, brakow: {miss}, ~{total_rows} meczow ze statystykami.")
    print(f"Katalog: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    sys.exit(main())
