#!/usr/bin/env python3
"""
Analiza pobranych danych Football-Data.co.uk.

1. Pokrycie pol: corners, SOT, kartki, sedzia, kursy (opening/closing).
2. Base rates progow zdarzen (team corners >= x, team SOT >= x) per liga.
3. Kurs break-even po polskim podatku 12% - czyli jaki kurs MUSI dac bukmacher,
   zeby taki zaklad mial w ogole sens.

Uzycie:
    python3 04_coverage_and_baserates.py --dir ../data/fdfull/main
"""
import argparse
import csv
import glob
import os
from collections import defaultdict, Counter

TAX = 0.12  # polski podatek od obrotu


def breakeven_odds(p):
    """Kurs, przy ktorym EV_net = 0 przy podatku od stawki."""
    return (1.0 / (1.0 - TAX)) / p if p > 0 else float("inf")


def load(dirpath):
    rows = []
    for path in sorted(glob.glob(os.path.join(dirpath, "*.csv"))):
        div = os.path.basename(path).split("_")[0]
        with open(path, encoding="latin-1", newline="") as f:
            for r in csv.DictReader(f):
                if not r.get("HomeTeam"):
                    continue
                r["_div"] = div
                r["_file"] = os.path.basename(path)
                rows.append(r)
    return rows


def num(r, k):
    v = (r.get(k) or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="../data/fdfull/main")
    ap.add_argument("--min-matches", type=int, default=40,
                    help="minimum meczow druzyny, zeby pokazac ja w rankingu")
    args = ap.parse_args()

    rows = load(args.dir)
    print(f"Wczytano {len(rows)} meczow z {args.dir}\n")

    # ---------- 1. POKRYCIE -------------------------------------------
    fields = {
        "wynik (FTHG/FTAG)": ("FTHG", "FTAG"),
        "strzaly (HS/AS)": ("HS", "AS"),
        "SOT (HST/AST)": ("HST", "AST"),
        "rozne (HC/AC)": ("HC", "AC"),
        "faule (HF/AF)": ("HF", "AF"),
        "kartki zolte (HY/AY)": ("HY", "AY"),
        "kartki czerw. (HR/AR)": ("HR", "AR"),
    }
    print("=" * 72)
    print("POKRYCIE PROSTYCH STATYSTYK")
    print("=" * 72)
    for label, (a, b) in fields.items():
        n = sum(1 for r in rows if num(r, a) is not None and num(r, b) is not None)
        print(f"  {label:24s} {n:6d}/{len(rows)}  ({100*n/len(rows):5.1f}%)")
    n_ref = sum(1 for r in rows if (r.get("Referee") or "").strip())
    print(f"  {'sedzia (Referee)':24s} {n_ref:6d}/{len(rows)}  ({100*n_ref/len(rows):5.1f}%)")

    print("\n" + "=" * 72)
    print("POKRYCIE KURSOW  (C = closing / kurs zamkniecia)")
    print("=" * 72)
    odds_fields = {
        "1X2 Bet365 open (B365H)": "B365H",
        "1X2 Bet365 CLOSE (B365CH)": "B365CH",
        "1X2 Pinnacle open (PSH)": "PSH",
        "1X2 Pinnacle CLOSE (PSCH)": "PSCH",
        "1X2 Betfair Exch CLOSE (BFECH)": "BFECH",
        "1X2 najlepszy kurs (MaxH)": "MaxH",
        "O/U 2.5 open (B365>2.5)": "B365>2.5",
        "O/U 2.5 CLOSE (B365C>2.5)": "B365C>2.5",
        "Asian Handicap CLOSE (B365CAHH)": "B365CAHH",
        "ROZNE - jakikolwiek kurs": None,
        "SOT - jakikolwiek kurs": None,
        "KARTKI - jakikolwiek kurs": None,
    }
    header = rows[0].keys() if rows else []
    for label, key in odds_fields.items():
        if key is None:
            print(f"  {label:34s}      0/{len(rows)}  (  0.0%)   <-- BRAK W ZBIORZE")
            continue
        n = sum(1 for r in rows if num(r, key) is not None)
        print(f"  {label:34s} {n:6d}/{len(rows)}  ({100*n/len(rows):5.1f}%)")

    # ---------- 2. BASE RATES -----------------------------------------
    print("\n" + "=" * 72)
    print("BASE RATES PROGOW - poziom ligi (perspektywa jednej druzyny)")
    print("=" * 72)
    print(f"{'liga':5s} {'n(team-mecze)':>13s} {'C>=4':>7s} {'C>=5':>7s} "
          f"{'SOT>=3':>7s} {'SOT>=4':>7s} {'kartka>=1':>10s}")
    per_div = defaultdict(lambda: Counter())
    for r in rows:
        d = r["_div"]
        for side in ("H", "A"):
            c = num(r, f"{side}C")
            s = num(r, f"{side}ST")
            y = num(r, f"{side}Y")
            if c is None and s is None:
                continue
            per_div[d]["n"] += 1
            if c is not None:
                if c >= 4: per_div[d]["c4"] += 1
                if c >= 5: per_div[d]["c5"] += 1
            if s is not None:
                if s >= 3: per_div[d]["s3"] += 1
                if s >= 4: per_div[d]["s4"] += 1
            if y is not None and y >= 1:
                per_div[d]["y1"] += 1

    for d in sorted(per_div, key=lambda x: -per_div[x]["n"]):
        k = per_div[d]
        n = k["n"] or 1
        print(f"{d:5s} {k['n']:13d} {100*k['c4']/n:6.1f}% {100*k['c5']/n:6.1f}% "
              f"{100*k['s3']/n:6.1f}% {100*k['s4']/n:6.1f}% {100*k['y1']/n:9.1f}%")

    # ---------- 3. NAJBARDZIEJ POWTARZALNE DRUZYNY ---------------------
    print("\n" + "=" * 72)
    print("TOP 15 DRUZYN: 'corners >= 4' (teza z noty 17 - powtarzalne zdarzenia)")
    print("=" * 72)
    team = defaultdict(lambda: Counter())
    for r in rows:
        for side, name_key in (("H", "HomeTeam"), ("A", "AwayTeam")):
            t = (r.get(name_key) or "").strip()
            c = num(r, f"{side}C")
            s = num(r, f"{side}ST")
            if not t:
                continue
            key = f"{r['_div']}|{t}"
            team[key]["n"] += 1
            if c is not None and c >= 4: team[key]["c4"] += 1
            if s is not None and s >= 3: team[key]["s3"] += 1

    ranked = [(k, v) for k, v in team.items() if v["n"] >= args.min_matches]
    ranked.sort(key=lambda kv: -kv[1]["c4"] / kv[1]["n"])
    print(f"{'druzyna':32s} {'n':>5s} {'P(C>=4)':>9s} {'fair':>7s} "
          f"{'break-even po 12%':>18s}")
    for k, v in ranked[:15]:
        p = v["c4"] / v["n"]
        print(f"{k:32s} {v['n']:5d} {100*p:8.1f}% {1/p:7.2f} {breakeven_odds(p):18.2f}")

    print("\n" + "=" * 72)
    print("CO Z TEGO WYNIKA")
    print("=" * 72)
    if ranked:
        k, v = ranked[0]
        p = v["c4"] / v["n"]
        print(f"  Najlepsza druzyna: {k}, P(corners>=4) = {100*p:.1f}%")
        print(f"  Fair odds        : {1/p:.2f}")
        print(f"  Break-even w PL  : {breakeven_odds(p):.2f}  (musi byc WYZSZY niz fair)")
        print(f"  Czyli bukmacher musi dac kurs o {100*(breakeven_odds(p)/(1/p)-1):.1f}% wyzszy")
        print(f"  od fair, ZANIM w ogole zaczniemy zarabiac.")
    print("\n  Ten zbior NIE zawiera kursow na corners/SOT/kartki.")
    print("  Mozna tu zbudowac i skalibrowac MODEL, ale nie da sie zbacktestowac")
    print("  ZAKLADU - bo nie ma historycznej ceny. To jest dokladnie wask ie gardlo")
    print("  z noty 23 par. 9, tylko widziane od strony danych.")


if __name__ == "__main__":
    main()
