#!/usr/bin/env python3
"""
Most miedzy nazwa stadionu jako tekstem a wspolrzednymi, a potem pogoda.

Nota 31 zakladala dopasowywanie ~430 klubow. Ta liczba byla bledna: clubs.csv
zna 796 klubow i pokrywa 85.6% gospodarzy, a w games.csv jest ich 2980.
Wlasciwa miara nie jest liczba encji, tylko POKRYCIE MECZOW: top 300 nazw
stadionow to 65% meczow, top 500 to 78%. Dlatego dopasowujemy nazwy stadionow
posortowane po liczbie meczow i zatrzymujemy sie tam, gdzie przestaje sie to
oplacac.

Etapy (kazdy zapisuje wynik, wiec mozna je odpalac osobno):

  osm      pelny zestaw stadionow z Overpass, BEZ filtra pojemnosci
           (poprzednie pobranie mialo filtr > 8000 i przywiozlo 242 z 11 147,
           gubiac m.in. Olimpico, Meazza i Bernabeu)
  match    fuzzy match nazw + flaga venue_confidence
  weather  Open-Meteo Archive, jedno zapytanie na stadion na caly zakres dat

UWAGA na leakage (nota 11): Open-Meteo Archive zwraca pogode ZREALIZOWANA, nie
prognoze znana przed kickoffem. Do backtestu historycznego to akceptowalne, w
produkcji trzeba uzyc endpointu prognoz i zapisywac je z known_at.

Uzycie:
    python3 09_stadiums_and_weather.py osm     --out ../../../hazard_saas/data/raw
    python3 09_stadiums_and_weather.py match   --out ../../../hazard_saas/data/raw
    python3 09_stadiums_and_weather.py weather --out ../../../hazard_saas/data/raw --top 500
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

OVERPASS = "https://overpass-api.de/api/interpreter"
WDQS = "https://query.wikidata.org/sparql"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"

# Europa z zapasem: Islandia i Azory na zachodzie, Ural i Kaukaz na wschodzie.
EUROPE_BBOX = (34.0, -26.0, 72.0, 45.0)

# slowa, ktore nic nie roznicuja miedzy "Stadio Olimpico" i "Olimpico di Roma"
STOPWORDS = {
    "stadion", "stadio", "stade", "stadium", "estadio", "estadi", "arena",
    "park", "parken", "sportpark", "de", "del", "di", "da", "do", "la", "el",
    "les", "los", "the", "of", "und", "am", "im", "op", "aan", "van", "at",
    "municipal", "comunale", "city", "miejski", "im", "na",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).lower()
    toks = [t for t in s.split() if t and t not in STOPWORDS]
    return " ".join(toks)


# --------------------------------------------------------------------------
# etap 1: OSM
# --------------------------------------------------------------------------
def _overpass(query: str, attempts: int = 3) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    for k in range(attempts):
        try:
            req = urllib.request.Request(
                OVERPASS, data=data, headers={"User-Agent": "model-buk/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except Exception as e:
            wait = 20 * (k + 1)
            print(f"    proba {k + 1}/{attempts} nieudana ({str(e)[:50]}), "
                  f"czekam {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("Overpass nie odpowiedzial")


def fetch_osm(out_dir: str, step: float = 8.0) -> str:
    """Pobiera kaflami. Cala Europa w jednym zapytaniu konczy sie 504."""
    s, w, n, e = EUROPE_BBOX
    tiles = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            tiles.append((lat, lon, min(lat + step, n), min(lon + step, e)))
            lon += step
        lat += step

    dest = os.path.join(out_dir, "osm", "stadiums_europe_full.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    seen: dict[tuple, dict] = {}
    print(f"Overpass: {len(tiles)} kafli po {step} stopni", flush=True)

    for i, (a, b, cc, d) in enumerate(tiles, 1):
        q = f"""
[out:json][timeout:290];
(
  node["leisure"="stadium"]({a},{b},{cc},{d});
  way["leisure"="stadium"]({a},{b},{cc},{d});
  relation["leisure"="stadium"]({a},{b},{cc},{d});
);
out center tags;
"""
        try:
            els = _overpass(q).get("elements", [])
        except RuntimeError:
            print(f"  [{i}/{len(tiles)}] kafel {a},{b} POMINIETY", flush=True)
            continue
        for x in els:
            seen[(x.get("type"), x.get("id"))] = x
        named = sum(1 for x in els if x.get("tags", {}).get("name"))
        print(f"  [{i}/{len(tiles)}] {a:.0f},{b:.0f} -> {len(els):5d} obiektow "
              f"({named:4d} z nazwa) | razem {len(seen):6,}", flush=True)
        time.sleep(2)

    body = json.dumps({"elements": list(seen.values())}).encode()
    with open(dest, "wb") as f:
        f.write(body)
    named = sum(1 for x in seen.values() if x.get("tags", {}).get("name"))
    print(f"\n{len(seen):,} obiektow ({named:,} z nazwa) -> {dest} "
          f"({len(body) / 1e6:.1f} MB)")
    return dest



# --------------------------------------------------------------------------
# etap 1b: Wikidata (kanal podstawowy)
# --------------------------------------------------------------------------
WD_QUERY = """
SELECT ?venue ?venueLabel ?coord ?capacity
       (GROUP_CONCAT(DISTINCT ?alias; separator="||") AS ?aliases)
WHERE {
  ?club wdt:P31/wdt:P279* wd:Q476028 .
  ?club wdt:P115 ?venue .
  ?venue wdt:P625 ?coord .
  OPTIONAL { ?venue wdt:P1083 ?capacity }
  OPTIONAL {
    ?venue skos:altLabel ?alias
    FILTER(lang(?alias) IN ("en","pl","it","es","de","fr","pt","nl","tr","ru","uk"))
  }
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "en,pl,it,es,de,fr,pt,nl,tr,ru,uk" }
}
GROUP BY ?venue ?venueLabel ?coord ?capacity
"""


def fetch_wikidata(out_dir: str) -> str:
    """Stadiony pilkarskie z Wikidata: wspolrzedne, pojemnosc i ALIASY nazw.

    Overpass okazal sie niedostepny (glowna instancja odrzuca polaczenia,
    kumi.systems daje 504, osm.ch ma tylko dane Szwajcarii). Wikidata jest tu
    lepsza nie tylko dlatego, ze odpowiada: trzyma aliasy, wiec "Giuseppe
    Meazza" i "San Siro" to jeden obiekt, a to wlasnie na takich parach
    lamalo sie dopasowanie po nazwie.
    """
    url = WDQS + "?" + urllib.parse.urlencode({"query": WD_QUERY, "format": "json"})
    req = urllib.request.Request(url, headers={
        "User-Agent": "model-buk/1.0 (research)",
        "Accept": "application/sparql-results+json"})
    print("Wikidata: pytam o stadiony klubow pilkarskich ...", flush=True)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        js = json.load(r)
    rows = js["results"]["bindings"]

    out = []
    for b in rows:
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", b["coord"]["value"])
        if not m:
            continue
        lon, lat = float(m.group(1)), float(m.group(2))
        aliases = [a for a in b.get("aliases", {}).get("value", "").split("||") if a]
        out.append({"qid": b["venue"]["value"].rsplit("/", 1)[-1],
                    "name": b["venueLabel"]["value"],
                    "aliases": aliases, "lat": lat, "lon": lon,
                    "capacity": b.get("capacity", {}).get("value")})
    dest = os.path.join(out_dir, "osm", "stadiums_wikidata.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    n_alias = sum(len(x["aliases"]) for x in out)
    print(f"  {len(out):,} stadionow, {n_alias:,} aliasow, "
          f"{time.time() - t0:.1f}s -> {dest}")
    return dest


def load_osm(out_dir: str) -> list[dict]:
    """Wikidata jako kanal podstawowy, OSM jako zapasowy."""
    wd = os.path.join(out_dir, "osm", "stadiums_wikidata.json")
    if os.path.exists(wd):
        raw = json.load(open(wd, encoding="utf-8"))
        rows = []
        for x in raw:
            for nm in [x["name"], *x["aliases"]]:
                rows.append({"osm_name": nm, "lat": x["lat"], "lon": x["lon"],
                             "capacity": x["capacity"], "country": None,
                             "norm": norm(nm)})
        print(f"Wikidata: {len(raw):,} stadionow -> {len(rows):,} wariantow nazw")
        return rows
    for name in ("stadiums_europe_full.json", "stadiums_europe.json"):
        p = os.path.join(out_dir, "osm", name)
        if os.path.exists(p):
            els = json.load(open(p))["elements"]
            print(f"OSM: {name} -> {len(els):,} obiektow")
            rows = []
            for e in els:
                t = e.get("tags", {})
                nm = t.get("name")
                lat = e.get("lat") or (e.get("center") or {}).get("lat")
                lon = e.get("lon") or (e.get("center") or {}).get("lon")
                if not nm or lat is None or lon is None:
                    continue
                rows.append({"osm_name": nm, "lat": float(lat), "lon": float(lon),
                             "capacity": t.get("capacity"),
                             "country": t.get("addr:country"),
                             "norm": norm(nm)})
            return rows
    sys.exit("Brak danych OSM. Odpal najpierw: 09_stadiums_and_weather.py osm")


# --------------------------------------------------------------------------
# etap 2: dopasowanie
# --------------------------------------------------------------------------
def cmd_match(out_dir: str, min_ratio: float = 0.86) -> str:
    import pandas as pd

    tm = os.path.join(out_dir, "transfermarkt", "games.csv")
    g = pd.read_csv(tm, usecols=["date", "home_club_id", "stadium"]).dropna(
        subset=["stadium"])
    counts = g.stadium.value_counts()

    osm = load_osm(out_dir)
    exact: dict[str, dict] = {}
    for r in osm:
        # przy duplikatach nazwy wybieramy obiekt o wiekszej pojemnosci
        cur = exact.get(r["norm"])
        if cur is None or (_cap(r) > _cap(cur)):
            exact[r["norm"]] = r
    osm_keys = list(exact)

    rows = []
    for stadium, n in counts.items():
        key = norm(stadium)
        hit, conf, how = exact.get(key), None, None
        if hit:
            conf, how = 1.0, "exact"
        else:
            best, best_r = None, 0.0
            for k in osm_keys:
                r = SequenceMatcher(None, key, k).ratio()
                if r > best_r:
                    best, best_r = k, r
            if best is not None and best_r >= min_ratio:
                hit, conf, how = exact[best], round(best_r, 3), "fuzzy"
        rows.append({
            "stadium": stadium, "matches": int(n), "norm": key,
            "osm_name": hit["osm_name"] if hit else None,
            "lat": hit["lat"] if hit else None,
            "lon": hit["lon"] if hit else None,
            "capacity": hit["capacity"] if hit else None,
            "venue_confidence": conf, "match_kind": how,
        })
    df = pd.DataFrame(rows)
    dest = os.path.join(out_dir, "osm", "stadium_match.csv")
    df.to_csv(dest, index=False)

    tot = df.matches.sum()
    ok = df[df.lat.notna()]
    print(f"\ndopasowane nazwy: {len(ok):,} / {len(df):,}")
    print(f"pokrycie MECZOW: {ok.matches.sum():,} / {tot:,} "
          f"({ok.matches.sum() / tot * 100:.1f}%)")
    for kind in ("exact", "fuzzy"):
        s = df[df.match_kind == kind]
        print(f"  {kind:6s} {len(s):5,} nazw, {s.matches.sum():7,} meczow")
    print(f"\nnajczestsze NIEdopasowane (do recznej weryfikacji):")
    for _, r in df[df.lat.isna()].head(15).iterrows():
        print(f"  {r.matches:5d}  {r.stadium}")
    print(f"\n-> {dest}")
    return dest


def _cap(r) -> int:
    try:
        return int(str(r.get("capacity") or 0).split(";")[0])
    except Exception:
        return 0


# --------------------------------------------------------------------------
# etap 3: pogoda
# --------------------------------------------------------------------------
# Nota 04 stawia na EnvironmentalShock, nie na sredni komfort: ekstrema
# temperatury, opad i wiatr. Kazda dodatkowa zmienna mnozy wage zapytania.
DAILY_VARS = ("temperature_2m_max", "temperature_2m_min",
              "precipitation_sum", "snowfall_sum", "wind_gusts_10m_max")

# Pogoda ma sens tam, gdzie mamy tez kursy i wyniki. Zbior football-data-uk
# zaczyna sie 2014-07, wiec wczesniejsze lata nie maja z czym sie spotkac.
WEATHER_FROM = "2014-01-01"


def cmd_weather(out_dir: str, top: int, pause: float = 5.0) -> str:
    import pandas as pd

    match_csv = os.path.join(out_dir, "osm", "stadium_match.csv")
    if not os.path.exists(match_csv):
        sys.exit("Brak stadium_match.csv. Odpal najpierw etap: match")
    m = pd.read_csv(match_csv)
    m = m[m.lat.notna()].sort_values("matches", ascending=False).head(top)

    g = pd.read_csv(os.path.join(out_dir, "transfermarkt", "games.csv"),
                    usecols=["date", "stadium"]).dropna(subset=["stadium"])
    g["date"] = pd.to_datetime(g.date, errors="coerce")
    span = g.groupby("stadium").date.agg(["min", "max"])

    dest_dir = os.path.join(out_dir, "weather")
    os.makedirs(dest_dir, exist_ok=True)
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    frames, failed = [], []
    print(f"Open-Meteo Archive: {len(m)} stadionow, jedno zapytanie na stadion\n")
    for i, r in enumerate(m.itertuples(), 1):
        if r.stadium not in span.index:
            continue
        d0, d1 = span.loc[r.stadium, "min"], span.loc[r.stadium, "max"]
        floor = pd.Timestamp(WEATHER_FROM)
        if d1 < floor:
            continue
        d0 = max(d0, floor)
        params = {
            "latitude": f"{r.lat:.4f}", "longitude": f"{r.lon:.4f}",
            "start_date": d0.strftime("%Y-%m-%d"),
            "end_date": d1.strftime("%Y-%m-%d"),
            "daily": ",".join(DAILY_VARS), "timezone": "UTC",
        }
        url = f"{OPEN_METEO}?{urllib.parse.urlencode(params)}"
        js = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=180) as resp:
                    js = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After") or 0) or 60 * (attempt + 1)
                    print(f"  [{i}/{len(m)}] 429, czekam {wait}s "
                          f"(proba {attempt + 1}/5)", flush=True)
                    time.sleep(wait)
                    continue
                failed.append((r.stadium, f"HTTP {e.code}"))
                break
            except Exception as e:
                failed.append((r.stadium, str(e)[:60]))
                break
        if js is None:
            if not failed or failed[-1][0] != r.stadium:
                failed.append((r.stadium, "limit po 5 probach"))
            time.sleep(pause)
            continue
        daily = js.get("daily") or {}
        if not daily.get("time"):
            failed.append((r.stadium, "brak daily"))
            time.sleep(pause)
            continue
        df = pd.DataFrame(daily)
        df.insert(0, "stadium", r.stadium)
        df.insert(1, "lat", r.lat)
        df.insert(2, "lon", r.lon)
        df["elevation"] = js.get("elevation")
        df["venue_confidence"] = r.venue_confidence
        df["provider"] = "open-meteo-archive"
        df["fetched_at"] = fetched_at
        frames.append(df)
        if i % 25 == 0 or i == len(m):
            print(f"  [{i}/{len(m)}] {r.stadium[:34]:34s} "
                  f"{len(df):5d} dni  (razem {sum(len(f) for f in frames):,})",
                  flush=True)
        time.sleep(pause)

    if not frames:
        sys.exit("Nic nie pobrano.")
    out = pd.concat(frames, ignore_index=True)
    dest = os.path.join(dest_dir, "weather_daily.parquet")
    out.to_parquet(dest, index=False)
    print(f"\n{len(out):,} wierszy, {out.stadium.nunique()} stadionow -> {dest}")
    print(f"rozmiar: {os.path.getsize(dest) / 1e6:.1f} MB")
    if failed:
        print(f"\nnieudane ({len(failed)}):")
        for s, e in failed[:10]:
            print(f"  {s}: {e}")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["wikidata", "osm", "match", "weather"])
    ap.add_argument("--out", default="../../../hazard_saas/data/raw")
    ap.add_argument("--top", type=int, default=500,
                    help="ile stadionow wg liczby meczow (weather)")
    ap.add_argument("--min-ratio", type=float, default=0.86,
                    help="prog fuzzy matchingu (match)")
    a = ap.parse_args()
    out = os.path.abspath(a.out)

    if a.stage == "wikidata":
        fetch_wikidata(out)
    elif a.stage == "osm":
        fetch_osm(out)
    elif a.stage == "match":
        cmd_match(out, a.min_ratio)
    else:
        cmd_weather(out, a.top)


if __name__ == "__main__":
    main()
