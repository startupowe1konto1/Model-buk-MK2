#!/usr/bin/env python3
"""
Generator katalogu danych.

Przechodzi po data/raw/, otwiera kazdy plik i wyciaga: liczbe wierszy, kolumny,
typy, pokrycie (% niepustych), zakresy dat, zakresy liczbowe i przykladowe
wartosci dla kolumn kategorycznych. Wynik zapisuje jako markdown.

Katalog jest GENEROWANY, nie pisany recznie — po kazdej zmianie danych
wystarczy odpalic ponownie i commitowac diff.

Uzycie:
    PYTHONPATH=.pylibs python3 scripts/07_generate_data_catalogue.py \
        --raw data/raw --out KATALOG-DANYCH.md
"""
import argparse
import glob
import json
import os
import subprocess
import warnings

warnings.filterwarnings("ignore")
import pandas as pd

BIG_FILE_MB = 120          # powyzej tego czytamy probke, nie calosc
SAMPLE_ROWS = 300_000
MAX_CATEGORIES = 8

# Opisy zestawow: skad, jak odtworzyc, na co uwazac
DATASETS = {
    "club-football-2000-2025": dict(
        zrodlo="github.com/xgabora/Club-Football-Match-Data-2000-2025 (lustro Kaggle)",
        licencja="MIT",
        odtworzenie="curl -sL https://github.com/xgabora/Club-Football-Match-Data-2000-2025/raw/main/data/Matches.csv -o Matches.csv",
        uwagi="Zrodlem pierwotnym jest Football-Data.co.uk, ale scalone przez 27 krajow i wzbogacone o Elo z ClubElo oraz policzone Form3/Form5."),
    "football-data-uk": dict(
        zrodlo="football-data.co.uk",
        licencja="darmowe, bez rejestracji",
        odtworzenie="python3 scripts/01_download_football_data.py --out data/raw/football-data-uk --seasons 12",
        uwagi="Katalog main/ ma pelne statystyki meczowe; extra/ tylko wyniki i kursy (16 krajow, w tym Polska). Kolumny z prefiksem C = kursy zamkniecia. Zestaw kolumn kursowych zmienia sie miedzy sezonami (bukmacherzy pojawiaja sie i znikaja) — ponizej opisany najbogatszy plik main i jeden extra.",
        force_files=["main/E0_2526.csv", "extra/POL.csv"]),
    "transfermarkt": dict(
        zrodlo="Kaggle davidcariboo/player-scores (scraping transfermarkt.co.uk)",
        licencja="CC0 public domain",
        odtworzenie='curl -sL -H "Authorization: Bearer $KAGGLE_TOKEN" https://www.kaggle.com/api/v1/datasets/download/davidcariboo/player-scores/<plik>.csv',
        uwagi="Aktualizacje wstrzymane od lipca 2026 — dane kompletne do 2026-07-06. Jedyne zrodlo skladow i sedziego w 99%."),
    "understat": dict(
        zrodlo="understat.com przez biblioteke soccerdata",
        licencja="scraping wobec ToS — tylko research",
        odtworzenie="PYTHONPATH=.pylibs python3 scripts/06_download_understat.py",
        uwagi="xG dla top 5 lig. Pliki *_schedule to mecze, *_team_match_stats to statystyki druzynowe."),
    "footiqo": dict(
        zrodlo="footiqo.com — darmowe zbiory za podaniem maila",
        licencja="darmowe",
        odtworzenie="pobrane recznie ze strony (wymaga maila, nie da sie zautomatyzowac)",
        uwagi="JEDYNE zrodlo historycznych kursow na rozne i kartki. Rozne sa MECZOWE (suma), nie druzynowe."),
    "schochastics": dict(
        zrodlo="github.com/schochastics/football-data",
        licencja="otwarte",
        odtworzenie="curl -sL https://github.com/schochastics/football-data/raw/master/data/results/games.parquet -o games.parquet",
        uwagi="Najglebsza historia (od 1888), ale tylko wyniki. Do priorow i dlugich szeregow."),
    "apifootball": dict(
        zrodlo="API-Football v3, plan Free",
        licencja="wg ToS providera",
        odtworzenie="python3 scripts/02_apifootball_probe.py --key-file ~/.apifootball_key",
        uwagi="Wyniki sond, nie zbior produkcyjny. bets.json = katalog 338 typow zakladow, bookmakers.json = 33 bukmacherow."),
    "osm": dict(
        zrodlo="OpenStreetMap Overpass API",
        licencja="ODbL",
        odtworzenie="zapytanie Overpass o leisure=stadium w bbox Europy, filtr capacity>8000",
        uwagi="Tylko 242 stadiony. Dopasowanie do nazw z transfermarktu wynosi 3,7% — patrz nota 31."),
    "statsbomb": dict(
        zrodlo="github.com/statsbomb/open-data",
        licencja="StatsBomb, niekomercyjne",
        odtworzenie="curl -sL https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json",
        uwagi="Sam katalog rozgrywek. Pelne dane to osobny zestaw statsbomb-full."),
    "statsbomb-full": dict(
        zrodlo="github.com/statsbomb/open-data (shallow clone)",
        licencja="StatsBomb, niekomercyjne",
        odtworzenie="git clone --depth 1 https://github.com/statsbomb/open-data.git",
        uwagi="17 GB. NIE jest ciagly zbior ligowy: La Liga to sezony Messiego, Champions League to same finaly. Laboratorium metodologiczne, nie zrodlo do modelu."),
}


def human(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f} {u}" if u != "B" else f"{n} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def count_lines(path):
    try:
        return int(subprocess.run(["wc", "-l", path], capture_output=True,
                                  text=True).stdout.split()[0]) - 1
    except Exception:
        return None


def load(path, sheet=None):
    """Zwraca (df, n_rows_total, sampled). Duze pliki: probka z poczatku I konca,
    zeby zakresy dat obejmowaly caly zbior."""
    size_mb = os.path.getsize(path) / 1048576
    ext = os.path.splitext(path)[1].lower()
    if ext == ".parquet":
        df = pd.read_parquet(path)
        return df, len(df), False
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, sheet_name=sheet if sheet is not None else 0)
        return df, len(df), False
    if ext in (".csv", ".txt"):
        enc = "latin-1" if "football-data-uk" in path else "utf-8"
        if size_mb > BIG_FILE_MB:
            total = count_lines(path)
            half = SAMPLE_ROWS // 2
            head = pd.read_csv(path, nrows=half, low_memory=False, encoding=enc)
            if total and total > SAMPLE_ROWS:
                tail = pd.read_csv(path, low_memory=False, encoding=enc,
                                   skiprows=range(1, total - half + 1),
                                   names=head.columns, header=0)
                df = pd.concat([head, tail], ignore_index=True)
            else:
                df = head
            return df, total, True
        df = pd.read_csv(path, low_memory=False, encoding=enc)
        return df, len(df), False
    return None, None, False


def describe_column(s):
    """Zwraca (typ, pokrycie%, opis zakresu/wartosci)."""
    cov = 100.0 * s.notna().mean() if len(s) else 0.0
    nn = s.dropna()
    if nn.empty:
        return str(s.dtype), cov, "—"
    if pd.api.types.is_numeric_dtype(s):
        return str(s.dtype), cov, f"od {nn.min():g} do {nn.max():g}, sr. {nn.mean():.2f}"
    nunq = nn.nunique()
    sample = nn.astype(str)
    if nunq <= MAX_CATEGORIES:
        vals = ", ".join(sorted(sample.unique())[:MAX_CATEGORIES])
        return str(s.dtype), cov, f"{nunq} wartosci: {vals}"
    # data?
    if sample.str.match(r"^\d{4}-\d{2}-\d{2}").mean() > 0.8 or \
       sample.str.match(r"^\d{2}/\d{2}/\d{4}").mean() > 0.8:
        return str(s.dtype), cov, f"zakres {sample.min()} .. {sample.max()}"
    ex = "; ".join(sample.head(2).tolist())[:60]
    return str(s.dtype), cov, f"{nunq:,} unikalnych, np. {ex}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="KATALOG-DANYCH.md")
    ap.add_argument("--host", default="MacBook Wojtka")
    args = ap.parse_args()

    raw_abs = os.path.abspath(args.raw)
    out = []
    A = out.append

    A("# Katalog danych — Model Buk\n")
    A("> **Ten plik jest generowany.** Nie edytuj recznie — odpal\n"
      "> `python3 scripts/07_generate_data_catalogue.py` po kazdej zmianie danych.\n")
    A(f"Wygenerowano: **{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}**\n")

    A("## Gdzie fizycznie leza dane\n")
    A(f"Dane surowe **nie sa wersjonowane w repo** — leza lokalnie na dysku "
      f"(*{args.host}*):\n")
    A(f"```\n{raw_abs}\n```\n")
    A("Powod: 18 GB, w tym pojedyncze pliki powyzej twardego limitu GitHuba (100 MB).\n")
    A("**Dla reszty zespolu:** kazdy zestaw ma nizej komende odtworzenia. "
      "Siedem z dziesieciu odtwarza sie jedna komenda bez zadnych kluczy. "
      "Ten katalog opisuje *co* jest w danych; skrypty w `scripts/` opisuja *jak* je zdobyc.\n")

    # --- podsumowanie
    dirs = sorted(d for d in os.listdir(args.raw)
                  if os.path.isdir(os.path.join(args.raw, d)))
    A("## Podsumowanie\n")
    A("| zestaw | rozmiar | plikow | zrodlo | licencja |")
    A("|---|---|---|---|---|")
    total = 0
    for d in dirs:
        p = os.path.join(args.raw, d)
        files = [f for f in glob.glob(os.path.join(p, "**", "*"), recursive=True)
                 if os.path.isfile(f) and "/.git/" not in f]
        size = sum(os.path.getsize(f) for f in files)
        total += size
        meta = DATASETS.get(d, {})
        A(f"| `{d}` | {human(size)} | {len(files)} | {meta.get('zrodlo','—')} | {meta.get('licencja','—')} |")
    A(f"\n**Razem: {human(total)}**\n")

    # --- szczegoly
    for d in dirs:
        p = os.path.join(args.raw, d)
        meta = DATASETS.get(d, {})
        A(f"\n---\n\n## `{d}`\n")
        if meta:
            A(f"- **Zrodlo:** {meta['zrodlo']}")
            A(f"- **Licencja:** {meta['licencja']}")
            A(f"- **Odtworzenie:** `{meta['odtworzenie']}`")
            A(f"- **Uwagi:** {meta['uwagi']}\n")

        files = sorted(f for f in glob.glob(os.path.join(p, "**", "*"), recursive=True)
                       if os.path.isfile(f) and "/.git/" not in f)
        tabular = [f for f in files if os.path.splitext(f)[1].lower()
                   in (".csv", ".parquet", ".xlsx")]

        if d == "statsbomb-full":
            A("Zbior plikow JSON, nie tabelaryczny. Struktura:\n")
            for sub in ("events", "lineups", "matches", "three-sixty"):
                sp = os.path.join(p, "data", sub)
                if os.path.isdir(sp):
                    fl = glob.glob(os.path.join(sp, "**", "*.json"), recursive=True)
                    sz = sum(os.path.getsize(x) for x in fl)
                    A(f"- `data/{sub}/` — {len(fl):,} plikow, {human(sz)}")
            A("")
            continue

        if not tabular:
            A(f"{len(files)} plikow nietabelarycznych "
              f"({', '.join(sorted({os.path.splitext(f)[1] for f in files}))}).\n")
            continue

        # dla katalogow z wieloma jednorodnymi plikami pokazujemy reprezentanta
        # grupujemy po sygnaturze kolumn: pliki o identycznej strukturze
        # opisujemy raz, przez najwiekszego reprezentanta
        show, groups = [], {}
        if len(tabular) > 6:
            for f in tabular:
                try:
                    enc = "latin-1" if "football-data-uk" in f else "utf-8"
                    if f.endswith(".parquet"):
                        cols = tuple(pd.read_parquet(f).columns)
                    elif f.endswith(".xlsx"):
                        cols = ("xlsx", os.path.basename(f))
                    else:
                        cols = tuple(pd.read_csv(f, nrows=0, encoding=enc).columns)
                except Exception:
                    cols = ("?", os.path.basename(f))
                groups.setdefault(cols, []).append(f)
            forced = meta.get("force_files")
            if forced:
                A(f"*{len(tabular)} plikow, {len(groups)} roznych zestawow kolumn "
                  f"(bukmacherzy pojawiaja sie i znikaja miedzy sezonami). "
                  f"Ponizej dwa reprezentanty: Premier League z `main/` "
                  f"i Polska z `extra/`.*\n")
                for pat in forced:
                    m = [f for f in tabular if f.endswith(pat)]
                    if m:
                        show.append(m[0])
                groups = {}
            cap = meta.get("max_reps")
            # najpierw najbogatszy schemat, potem rozmiar
            ordered = [] if forced else sorted(groups.items(),
                             key=lambda kv: (-len(kv[0]),
                                             -max(os.path.getsize(x) for x in kv[1])))
            ordered = list(ordered)
            if cap:
                A(f"*{len(tabular)} plikow, {len(groups)} roznych zestawow kolumn "
                  f"— ponizej {cap} reprezentantow.*\n")
                ordered = ordered[:cap]
            for cols, fl in ordered:
                rep = max(fl, key=os.path.getsize)
                show.append(rep)
                if len(fl) > 1 and not cap:
                    A(f"*{len(fl)} plikow o identycznej strukturze "
                      f"(`{os.path.basename(fl[0])}` … `{os.path.basename(fl[-1])}`) "
                      f"— opis reprezentanta `{os.path.basename(rep)}`.*\n")
        else:
            show = tabular

        # xlsx rozwijamy na poszczegolne arkusze
        expanded = []
        for f in show:
            if f.lower().endswith((".xlsx", ".xls")):
                try:
                    for sh in pd.ExcelFile(f).sheet_names:
                        expanded.append((f, sh))
                except Exception:
                    expanded.append((f, None))
            else:
                expanded.append((f, None))

        for f, sheet in expanded:
            rel = os.path.relpath(f, args.raw) + (f"  →  arkusz `{sheet}`" if sheet else "")
            try:
                df, nrows, sampled = load(f, sheet)
            except Exception as e:
                A(f"### `{rel}`\n\nNie udalo sie otworzyc: {str(e)[:100]}\n")
                continue
            if df is None:
                continue
            note = (f" *(statystyki z probki {len(df):,} wierszy: poczatek + koniec pliku)*"
                    if sampled else "")
            A(f"### `{rel}`\n")
            A(f"{human(os.path.getsize(f))} · **{nrows:,} wierszy** · "
              f"{len(df.columns)} kolumn{note}\n")
            A("| kolumna | typ | pokrycie | zakres / wartosci |")
            A("|---|---|---:|---|")
            for c in df.columns:
                t, cov, desc = describe_column(df[c])
                A(f"| `{c}` | {t} | {cov:.1f}% | {desc} |")
            A("")

    text = "\n".join(out)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Zapisano {args.out} ({len(text)/1024:.0f} KB, {len(out)} linii)")


if __name__ == "__main__":
    main()
