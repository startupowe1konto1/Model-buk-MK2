# Katalog danych — Model Buk

> **Ten plik jest generowany.** Nie edytuj recznie — odpal
> `python3 scripts/07_generate_data_catalogue.py` po kazdej zmianie danych.

Wygenerowano: **2026-09-05 17:18**

## Gdzie fizycznie leza dane

Dane surowe **nie sa wersjonowane w repo** — leza lokalnie na dysku (*MacBook Wojtka*):

```
/Users/wojciechrudnicki/Code_projects/hazard_saas/data/raw
```

Powod: 18 GB, w tym pojedyncze pliki powyzej twardego limitu GitHuba (100 MB).

**Dla reszty zespolu:** kazdy zestaw ma nizej komende odtworzenia. Siedem z dziesieciu odtwarza sie jedna komenda bez zadnych kluczy. Ten katalog opisuje *co* jest w danych; skrypty w `scripts/` opisuja *jak* je zdobyc.

## Podsumowanie

| zestaw | rozmiar | plikow | zrodlo | licencja |
|---|---|---|---|---|
| `apifootball` | 339 KB | 7 | API-Football v3, plan Free | wg ToS providera |
| `club-football-2000-2025` | 51 MB | 3 | github.com/xgabora/Club-Football-Match-Data-2000-2025 (lustro Kaggle) | MIT |
| `football-data-uk` | 41 MB | 280 | football-data.co.uk | darmowe, bez rejestracji |
| `footiqo` | 1 MB | 2 | footiqo.com — darmowe zbiory za podaniem maila | darmowe |
| `osm` | 145 KB | 1 | OpenStreetMap Overpass API | ODbL |
| `schochastics` | 18 MB | 2 | github.com/schochastics/football-data | otwarte |
| `statsbomb` | 34 KB | 1 | github.com/statsbomb/open-data | StatsBomb, niekomercyjne |
| `statsbomb-full` | 15 GB | 8986 | github.com/statsbomb/open-data (shallow clone) | StatsBomb, niekomercyjne |
| `transfermarkt` | 872 MB | 10 | Kaggle davidcariboo/player-scores (scraping transfermarkt.co.uk) | CC0 public domain |
| `understat` | 8 MB | 10 | understat.com przez biblioteke soccerdata | scraping wobec ToS — tylko research |

**Razem: 16 GB**


---

## `apifootball`

- **Zrodlo:** API-Football v3, plan Free
- **Licencja:** wg ToS providera
- **Odtworzenie:** `python3 scripts/02_apifootball_probe.py --key-file ~/.apifootball_key`
- **Uwagi:** Wyniki sond, nie zbior produkcyjny. bets.json = katalog 338 typow zakladow, bookmakers.json = 33 bukmacherow.

7 plikow nietabelarycznych (.json).


---

## `club-football-2000-2025`

- **Zrodlo:** github.com/xgabora/Club-Football-Match-Data-2000-2025 (lustro Kaggle)
- **Licencja:** MIT
- **Odtworzenie:** `curl -sL https://github.com/xgabora/Club-Football-Match-Data-2000-2025/raw/main/data/Matches.csv -o Matches.csv`
- **Uwagi:** Zrodlem pierwotnym jest Football-Data.co.uk, ale scalone przez 27 krajow i wzbogacone o Elo z ClubElo oraz policzone Form3/Form5.

### `club-football-2000-2025/EloRatings.csv`

9 MB · **245,033 wierszy** · 4 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `date` | object | 100.0% | zakres 2000-07-01 .. 2025-06-01 |
| `club` | object | 100.0% | 895 unikalnych, np. Aachen; Aalborg |
| `country` | object | 100.0% | 19 unikalnych, np. GER; DEN |
| `elo` | float64 | 100.0% | od 1069.68 do 2107.48, sr. 1505.07 |

### `club-football-2000-2025/Matches.csv`

42 MB · **230,557 wierszy** · 48 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `Division` | object | 100.0% | 38 unikalnych, np. F1; F1 |
| `MatchDate` | object | 100.0% | zakres 2000-07-28 .. 2025-06-01 |
| `MatchTime` | object | 43.0% | 195 unikalnych, np. 03:00:00; 03:00:00 |
| `HomeTeam` | object | 100.0% | 1,214 unikalnych, np. Marseille; Paris SG |
| `AwayTeam` | object | 100.0% | 1,206 unikalnych, np. Troyes; Strasbourg |
| `HomeElo` | float64 | 61.4% | od 1103.13 do 2107.48, sr. 1533.12 |
| `AwayElo` | float64 | 61.4% | od 1103.13 do 2107.48, sr. 1533.17 |
| `Form3Home` | float64 | 99.3% | od 0 do 9, sr. 3.98 |
| `Form5Home` | float64 | 99.3% | od 0 do 15, sr. 6.72 |
| `Form3Away` | float64 | 99.3% | od 0 do 9, sr. 4.20 |
| `Form5Away` | float64 | 99.3% | od 0 do 15, sr. 6.93 |
| `FTHome` | float64 | 100.0% | od 0 do 10, sr. 1.49 |
| `FTAway` | float64 | 100.0% | od 0 do 13, sr. 1.15 |
| `FTResult` | object | 100.0% | 3 wartosci: A, D, H |
| `HTHome` | float64 | 76.3% | od 0 do 7, sr. 0.66 |
| `HTAway` | float64 | 76.3% | od 0 do 7, sr. 0.50 |
| `HTResult` | object | 76.3% | 3 wartosci: A, D, H |
| `HomeShots` | float64 | 49.8% | od 0 do 46, sr. 12.77 |
| `AwayShots` | float64 | 49.8% | od 0 do 45, sr. 10.42 |
| `HomeTarget` | float64 | 49.4% | od 0 do 31, sr. 5.11 |
| `AwayTarget` | float64 | 49.4% | od 0 do 23, sr. 4.14 |
| `HomeFouls` | float64 | 49.4% | od 0 do 145, sr. 12.60 |
| `AwayFouls` | float64 | 49.4% | od 0 do 77, sr. 13.06 |
| `HomeCorners` | float64 | 49.6% | od 0 do 26, sr. 5.66 |
| `AwayCorners` | float64 | 49.6% | od 0 do 23, sr. 4.61 |
| `HomeYellow` | float64 | 51.7% | od 0 do 11, sr. 1.68 |
| `AwayYellow` | float64 | 51.7% | od 0 do 10, sr. 1.99 |
| `HomeRed` | float64 | 51.7% | od 0 do 3, sr. 0.09 |
| `AwayRed` | float64 | 51.7% | od 0 do 9, sr. 0.12 |
| `OddHome` | float64 | 98.7% | od 0 do 53.18, sr. 2.46 |
| `OddDraw` | float64 | 98.7% | od 1.29 do 22.55, sr. 3.61 |
| `OddAway` | float64 | 98.7% | od 1.01 do 71.71, sr. 4.02 |
| `MaxHome` | float64 | 88.0% | od 1.02 do 126, sr. 2.67 |
| `MaxDraw` | float64 | 88.0% | od 1.2 do 47, sr. 3.88 |
| `MaxAway` | float64 | 88.0% | od 0 do 301, sr. 4.51 |
| `Over25` | float64 | 64.4% | od 1.09 do 3.95, sr. 1.94 |
| `Under25` | float64 | 64.4% | od 1.2 do 6.75, sr. 1.89 |
| `MaxOver25` | float64 | 64.4% | od 1.11 do 172, sr. 2.05 |
| `MaxUnder25` | float64 | 64.4% | od 1.26 do 238, sr. 1.99 |
| `HandiSize` | float64 | 68.0% | od -99.9 do 12.5, sr. -0.30 |
| `HandiHome` | float64 | 67.9% | od 0 do 177, sr. 1.92 |
| `HandiAway` | float64 | 67.9% | od -0.5 do 15.14, sr. 1.95 |
| `C_LTH` | float64 | 48.8% | od 0.0097 do 0.9437, sr. 0.22 |
| `C_LTA` | float64 | 48.8% | od 0.0097 do 0.9449, sr. 0.20 |
| `C_VHD` | float64 | 48.8% | od 0.0098 do 0.9512, sr. 0.15 |
| `C_VAD` | float64 | 48.8% | od 0.0097 do 0.9511, sr. 0.13 |
| `C_HTB` | float64 | 48.8% | od 0.0098 do 0.9465, sr. 0.17 |
| `C_PHB` | float64 | 48.8% | od 0.0097 do 0.9415, sr. 0.14 |


---

## `football-data-uk`

- **Zrodlo:** football-data.co.uk
- **Licencja:** darmowe, bez rejestracji
- **Odtworzenie:** `python3 scripts/01_download_football_data.py --out data/raw/football-data-uk --seasons 12`
- **Uwagi:** Katalog main/ ma pelne statystyki meczowe; extra/ tylko wyniki i kursy (16 krajow, w tym Polska). Kolumny z prefiksem C = kursy zamkniecia. Zestaw kolumn kursowych zmienia sie miedzy sezonami (bukmacherzy pojawiaja sie i znikaja) — ponizej opisany najbogatszy plik main i jeden extra.

*280 plikow, 33 roznych zestawow kolumn (bukmacherzy pojawiaja sie i znikaja miedzy sezonami). Ponizej dwa reprezentanty: Premier League z `main/` i Polska z `extra/`.*

### `football-data-uk/main/E0_2526.csv`

199 KB · **380 wierszy** · 132 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `ï»¿Div` | object | 100.0% | 1 wartosci: E0 |
| `Date` | object | 100.0% | zakres 01/01/2026 .. 31/08/2025 |
| `Time` | object | 100.0% | 15 unikalnych, np. 20:00; 12:30 |
| `HomeTeam` | object | 100.0% | 20 unikalnych, np. Liverpool; Aston Villa |
| `AwayTeam` | object | 100.0% | 20 unikalnych, np. Bournemouth; Newcastle |
| `FTHG` | int64 | 100.0% | od 0 do 5, sr. 1.53 |
| `FTAG` | int64 | 100.0% | od 0 do 5, sr. 1.22 |
| `FTR` | object | 100.0% | 3 wartosci: A, D, H |
| `HTHG` | int64 | 100.0% | od 0 do 3, sr. 0.69 |
| `HTAG` | int64 | 100.0% | od 0 do 4, sr. 0.50 |
| `HTR` | object | 100.0% | 3 wartosci: A, D, H |
| `Referee` | object | 100.0% | 23 unikalnych, np. A Taylor; C Pawson |
| `HS` | int64 | 100.0% | od 1 do 35, sr. 13.84 |
| `AS` | int64 | 100.0% | od 3 do 30, sr. 11.16 |
| `HST` | int64 | 100.0% | od 0 do 11, sr. 4.50 |
| `AST` | int64 | 100.0% | od 0 do 11, sr. 3.87 |
| `HF` | int64 | 100.0% | od 3 do 20, sr. 10.65 |
| `AF` | int64 | 100.0% | od 3 do 21, sr. 10.99 |
| `HC` | int64 | 100.0% | od 0 do 14, sr. 5.39 |
| `AC` | int64 | 100.0% | od 0 do 15, sr. 4.60 |
| `HY` | int64 | 100.0% | od 0 do 5, sr. 1.67 |
| `AY` | int64 | 100.0% | od 0 do 6, sr. 2.08 |
| `HR` | int64 | 100.0% | od 0 do 2, sr. 0.05 |
| `AR` | int64 | 100.0% | od 0 do 1, sr. 0.05 |
| `B365H` | float64 | 100.0% | od 1.09 do 19, sr. 2.65 |
| `B365D` | float64 | 100.0% | od 3 do 11, sr. 4.02 |
| `B365A` | float64 | 100.0% | od 1.13 do 23, sr. 3.97 |
| `BFDH` | float64 | 99.7% | od 1.1 do 17, sr. 2.71 |
| `BFDD` | float64 | 99.7% | od 3 do 10, sr. 4.01 |
| `BFDA` | float64 | 99.7% | od 1.14 do 26, sr. 4.17 |
| `BMGMH` | float64 | 99.5% | od 1.07 do 18, sr. 2.71 |
| `BMGMD` | float64 | 99.5% | od 3.1 do 10, sr. 4.04 |
| `BMGMA` | float64 | 99.5% | od 1.13 do 23, sr. 4.14 |
| `BVH` | float64 | 99.5% | od 1.06 do 20, sr. 2.67 |
| `BVD` | float64 | 99.5% | od 3.1 do 10.5, sr. 3.99 |
| `BVA` | float64 | 99.5% | od 1.12 do 26, sr. 4.08 |
| `BWH` | float64 | 100.0% | od 1.11 do 14, sr. 2.61 |
| `BWD` | float64 | 100.0% | od 3.1 do 11, sr. 4.01 |
| `BWA` | float64 | 100.0% | od 1.17 do 20, sr. 3.88 |
| `CLH` | float64 | 74.2% | od 1.11 do 13, sr. 2.63 |
| `CLD` | float64 | 74.2% | od 3.1 do 11, sr. 4.02 |
| `CLA` | float64 | 74.2% | od 1.17 do 19, sr. 3.92 |
| `LBH` | float64 | 75.3% | od 1.1 do 13, sr. 2.62 |
| `LBD` | float64 | 75.3% | od 3.1 do 11, sr. 3.98 |
| `LBA` | float64 | 75.3% | od 1.17 do 19, sr. 3.87 |
| `PSH` | float64 | 55.3% | od 1.14 do 9.99, sr. 2.68 |
| `PSD` | float64 | 55.3% | od 3.07 do 8.77, sr. 4.08 |
| `PSA` | float64 | 55.3% | od 1.28 do 21.15, sr. 4.10 |
| `MaxH` | float64 | 100.0% | od 1.11 do 20, sr. 2.77 |
| `MaxD` | float64 | 100.0% | od 3.15 do 11, sr. 4.15 |
| `MaxA` | float64 | 100.0% | od 1.22 do 26, sr. 4.30 |
| `AvgH` | float64 | 100.0% | od 1.08 do 16.53, sr. 2.65 |
| `AvgD` | float64 | 100.0% | od 3.07 do 10.12, sr. 3.98 |
| `AvgA` | float64 | 100.0% | od 1.14 do 22.31, sr. 4.01 |
| `BFEH` | float64 | 94.7% | od 1.11 do 13, sr. 2.79 |
| `BFED` | float64 | 94.7% | od 3.15 do 13, sr. 4.22 |
| `BFEA` | float64 | 94.7% | od 1.3 do 32, sr. 4.45 |
| `B365>2.5` | float64 | 100.0% | od 1.3 do 2.5, sr. 1.78 |
| `B365<2.5` | float64 | 100.0% | od 1.53 do 3.5, sr. 2.12 |
| `P>2.5` | float64 | 55.3% | od 1.34 do 2.56, sr. 1.85 |
| `P<2.5` | float64 | 55.3% | od 1.55 do 3.26, sr. 2.10 |
| `Max>2.5` | float64 | 100.0% | od 1.31 do 2.55, sr. 1.81 |
| `Max<2.5` | float64 | 100.0% | od 1.55 do 3.55, sr. 2.16 |
| `Avg>2.5` | float64 | 100.0% | od 1.29 do 2.43, sr. 1.75 |
| `Avg<2.5` | float64 | 100.0% | od 1.53 do 3.38, sr. 2.09 |
| `BFE>2.5` | float64 | 94.7% | od 1.35 do 2.62, sr. 1.85 |
| `BFE<2.5` | float64 | 94.7% | od 1.6 do 3.7, sr. 2.22 |
| `AHh` | float64 | 99.7% | od -2.5 do 2.5, sr. -0.27 |
| `B365AHH` | float64 | 100.0% | od 1.7 do 2.1, sr. 1.92 |
| `B365AHA` | float64 | 100.0% | od 1.7 do 2.1, sr. 1.93 |
| `PAHH` | float64 | 55.3% | od 1.77 do 2.15, sr. 1.95 |
| `PAHA` | float64 | 55.3% | od 1.78 do 2.15, sr. 1.96 |
| `MaxAHH` | float64 | 100.0% | od 1.75 do 3.5, sr. 1.94 |
| `MaxAHA` | float64 | 100.0% | od 1.75 do 22, sr. 2.47 |
| `AvgAHH` | float64 | 100.0% | od 1.71 do 2.09, sr. 1.88 |
| `AvgAHA` | float64 | 100.0% | od 1.68 do 5.19, sr. 1.95 |
| `BFEAHH` | float64 | 94.7% | od 1.81 do 2.21, sr. 1.98 |
| `BFEAHA` | float64 | 94.7% | od 1.8 do 2.2, sr. 1.99 |
| `B365CH` | float64 | 100.0% | od 1.09 do 15, sr. 2.69 |
| `B365CD` | float64 | 100.0% | od 3 do 10, sr. 3.98 |
| `B365CA` | float64 | 100.0% | od 1.14 do 26, sr. 3.92 |
| `BFDCH` | float64 | 97.9% | od 1.11 do 17, sr. 2.75 |
| `BFDCD` | float64 | 97.9% | od 2.88 do 9.5, sr. 3.98 |
| `BFDCA` | float64 | 97.9% | od 1.17 do 26, sr. 4.06 |
| `BMGMCH` | float64 | 100.0% | od 1.07 do 20, sr. 2.76 |
| `BMGMCD` | float64 | 100.0% | od 3.05 do 10.5, sr. 3.98 |
| `BMGMCA` | float64 | 100.0% | od 1.12 do 25, sr. 4.08 |
| `BVCH` | float64 | 97.9% | od 1.08 do 17, sr. 2.73 |
| `BVCD` | float64 | 97.9% | od 3 do 11, sr. 3.98 |
| `BVCA` | float64 | 97.9% | od 1.14 do 26, sr. 4.00 |
| `BWCH` | float64 | 100.0% | od 1.12 do 13, sr. 2.65 |
| `BWCD` | float64 | 100.0% | od 3 do 10.5, sr. 3.95 |
| `BWCA` | float64 | 100.0% | od 1.17 do 21, sr. 3.80 |
| `CLCH` | float64 | 68.4% | od 1.11 do 13, sr. 2.67 |
| `CLCD` | float64 | 68.4% | od 3 do 10, sr. 3.94 |
| `CLCA` | float64 | 68.4% | od 1.17 do 21, sr. 3.84 |
| `LBCH` | float64 | 73.9% | od 1.11 do 13, sr. 2.65 |
| `LBCD` | float64 | 73.9% | od 3 do 10, sr. 3.92 |
| `LBCA` | float64 | 73.9% | od 1.17 do 19, sr. 3.76 |
| `PSCH` | float64 | 55.3% | od 1.13 do 12.24, sr. 2.77 |
| `PSCD` | float64 | 55.3% | od 3.03 do 9.55, sr. 4.01 |
| `PSCA` | float64 | 55.3% | od 1.27 do 27, sr. 4.18 |
| `MaxCH` | float64 | 100.0% | od 1.12 do 20, sr. 2.84 |
| `MaxCD` | float64 | 100.0% | od 3.1 do 12, sr. 4.11 |
| `MaxCA` | float64 | 100.0% | od 1.22 do 28, sr. 4.23 |
| `AvgCH` | float64 | 100.0% | od 1.09 do 15.78, sr. 2.70 |
| `AvgCD` | float64 | 100.0% | od 3.03 do 10.11, sr. 3.94 |
| `AvgCA` | float64 | 100.0% | od 1.15 do 22.69, sr. 3.94 |
| `BFECH` | float64 | 94.2% | od 1.11 do 15.5, sr. 2.87 |
| `BFECD` | float64 | 94.2% | od 3.15 do 13, sr. 4.17 |
| `BFECA` | float64 | 94.2% | od 1.25 do 32, sr. 4.39 |
| `B365C>2.5` | float64 | 100.0% | od 1.33 do 2.63, sr. 1.79 |
| `B365C<2.5` | float64 | 100.0% | od 1.5 do 3.4, sr. 2.13 |
| `PC>2.5` | float64 | 55.3% | od 1.34 do 2.53, sr. 1.87 |
| `PC<2.5` | float64 | 55.3% | od 1.57 do 3.31, sr. 2.10 |
| `MaxC>2.5` | float64 | 100.0% | od 1.35 do 2.63, sr. 1.82 |
| `MaxC<2.5` | float64 | 100.0% | od 1.57 do 3.6, sr. 2.20 |
| `AvgC>2.5` | float64 | 100.0% | od 1.34 do 2.43, sr. 1.76 |
| `AvgC<2.5` | float64 | 100.0% | od 1.53 do 3.19, sr. 2.10 |
| `BFEC>2.5` | float64 | 94.2% | od 1.38 do 2.62, sr. 1.86 |
| `BFEC<2.5` | float64 | 94.2% | od 1.6 do 3.55, sr. 2.24 |
| `AHCh` | float64 | 100.0% | od -2.5 do 2.25, sr. -0.25 |
| `B365CAHH` | float64 | 100.0% | od 1.68 do 2.2, sr. 1.92 |
| `B365CAHA` | float64 | 100.0% | od 1.65 do 2.15, sr. 1.93 |
| `PCAHH` | float64 | 55.3% | od 1.68 do 2.32, sr. 1.96 |
| `PCAHA` | float64 | 55.3% | od 1.68 do 2.33, sr. 1.97 |
| `MaxCAHH` | float64 | 100.0% | od 1.75 do 2.35, sr. 1.94 |
| `MaxCAHA` | float64 | 100.0% | od 1.75 do 2.28, sr. 1.96 |
| `AvgCAHH` | float64 | 100.0% | od 1.71 do 2.1, sr. 1.87 |
| `AvgCAHA` | float64 | 100.0% | od 1.7 do 2.08, sr. 1.89 |
| `BFECAHH` | float64 | 94.2% | od 1.81 do 2.32, sr. 1.99 |
| `BFECAHA` | float64 | 94.2% | od 1.74 do 2.22, sr. 2.00 |

### `football-data-uk/extra/POL.csv`

510 KB · **4,131 wierszy** · 25 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `ï»¿Country` | object | 100.0% | 1 wartosci: Poland |
| `League` | object | 100.0% | 1 wartosci: Ekstraklasa |
| `Season` | object | 100.0% | 15 unikalnych, np. 2012/2013; 2012/2013 |
| `Date` | object | 100.0% | zakres 01/02/2021 .. 31/10/2025 |
| `Time` | object | 100.0% | 27 unikalnych, np. 17:00; 19:30 |
| `Home` | object | 100.0% | 36 unikalnych, np. Pogon Szczecin; Piast Gliwice |
| `Away` | object | 100.0% | 36 unikalnych, np. Zaglebie; Gornik Z. |
| `HG` | int64 | 100.0% | od 0 do 8, sr. 1.49 |
| `AG` | int64 | 100.0% | od 0 do 6, sr. 1.16 |
| `Res` | object | 100.0% | 3 wartosci: A, D, H |
| `PSCH` | float64 | 94.1% | od 1.16 do 12.81, sr. 2.50 |
| `PSCD` | float64 | 94.1% | od 2.7 do 7.87, sr. 3.62 |
| `PSCA` | float64 | 94.1% | od 1.26 do 23.43, sr. 3.88 |
| `MaxCH` | float64 | 100.0% | od 1.16 do 13.44, sr. 2.56 |
| `MaxCD` | float64 | 100.0% | od 2.92 do 8, sr. 3.70 |
| `MaxCA` | float64 | 100.0% | od 1.3 do 23.43, sr. 4.01 |
| `AvgCH` | float64 | 100.0% | od 1.14 do 11.26, sr. 2.39 |
| `AvgCD` | float64 | 100.0% | od 2.76 do 7.3, sr. 3.46 |
| `AvgCA` | float64 | 100.0% | od 1.25 do 18.64, sr. 3.61 |
| `BFECH` | float64 | 15.4% | od 1.27 do 9, sr. 2.49 |
| `BFECD` | float64 | 15.4% | od 2.92 do 6.6, sr. 3.78 |
| `BFECA` | float64 | 15.4% | od 1.47 do 13.5, sr. 3.89 |
| `B365CH` | float64 | 7.8% | od 1.22 do 4.5, sr. 2.25 |
| `B365CD` | float64 | 7.8% | od 2.63 do 5.25, sr. 3.49 |
| `B365CA` | float64 | 7.8% | od 1.65 do 11, sr. 3.40 |


---

## `footiqo`

- **Zrodlo:** footiqo.com — darmowe zbiory za podaniem maila
- **Licencja:** darmowe
- **Odtworzenie:** `pobrane recznie ze strony (wymaga maila, nie da sie zautomatyzowac)`
- **Uwagi:** JEDYNE zrodlo historycznych kursow na rozne i kartki. Rozne sa MECZOWE (suma), nie druzynowe.

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Matches_Results``

1 MB · **1,750 wierszy** · 24 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120872, sr. 87644.16 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 19:00:00 .. 2026-05-24 21:00:00 |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `country` | object | 100.0% | 5 wartosci: England, France, Germany, Italy, Spain |
| `league` | object | 100.0% | 5 wartosci: Bundesliga, LaLiga, Ligue 1, Premier League, Serie A |
| `league_id` | int64 | 100.0% | od 1 do 5, sr. 2.87 |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team_id` | int64 | 100.0% | od 1 do 436, sr. 103.61 |
| `home_team` | object | 100.0% | 96 unikalnych, np. Villarreal; AC Milan |
| `away_team_id` | int64 | 100.0% | od 1 do 436, sr. 103.61 |
| `away_team` | object | 100.0% | 96 unikalnych, np. Atl. Madrid; Cagliari |
| `referee_id` | int64 | 100.0% | od 7 do 1066, sr. 160.59 |
| `referee` | object | 100.0% | 129 unikalnych, np. Miguel Sesma Espinosa; Marco Guida |
| `referee_country` | object | 100.0% | 6 wartosci: England, France, Germany, Italy, Spain, USA |
| `neutral_venue` | int64 | 100.0% | od 0 do 0, sr. 0.00 |
| `home_goals_1h` | int64 | 100.0% | od 0 do 5, sr. 0.67 |
| `away_goals_1h` | int64 | 100.0% | od 0 do 5, sr. 0.53 |
| `result_1h` | object | 100.0% | 3 wartosci: A, D, H |
| `home_goals_2h` | int64 | 100.0% | od 0 do 6, sr. 0.87 |
| `away_goals_2h` | int64 | 100.0% | od 0 do 7, sr. 0.70 |
| `result_2h` | object | 100.0% | 3 wartosci: A, D, H |
| `home_goals_ft` | int64 | 100.0% | od 0 do 8, sr. 1.54 |
| `away_goals_ft` | int64 | 100.0% | od 0 do 7, sr. 1.23 |
| `result_ft` | object | 100.0% | 3 wartosci: A, D, H |

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Statistics_FT``

1 MB · **1,750 wierszy** · 29 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120872, sr. 87644.16 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 19:00:00 .. 2026-05-24 21:00:00 |
| `country` | object | 100.0% | 5 wartosci: England, France, Germany, Italy, Spain |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `league` | object | 100.0% | 5 wartosci: Bundesliga, LaLiga, Ligue 1, Premier League, Serie A |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team` | object | 100.0% | 96 unikalnych, np. Villarreal; AC Milan |
| `away_team` | object | 100.0% | 96 unikalnych, np. Atl. Madrid; Cagliari |
| `referee` | object | 100.0% | 129 unikalnych, np. Miguel Sesma Espinosa; Marco Guida |
| `home_corners_ft` | int64 | 100.0% | od 0 do 21, sr. 5.32 |
| `away_corners_ft` | int64 | 100.0% | od 0 do 18, sr. 4.23 |
| `home_yellow_cards_ft` | int64 | 100.0% | od 0 do 8, sr. 1.81 |
| `away_yellow_cards_ft` | int64 | 100.0% | od 0 do 8, sr. 2.10 |
| `home_red_cards_ft` | int64 | 100.0% | od 0 do 3, sr. 0.10 |
| `away_red_cards_ft` | int64 | 100.0% | od 0 do 2, sr. 0.10 |
| `home_xg_ft` | float64 | 100.0% | od 0.06 do 6.02, sr. 1.55 |
| `away_xg_ft` | float64 | 100.0% | od 0 do 6.44, sr. 1.23 |
| `home_ball_possession_ft` | int64 | 100.0% | od 17 do 85, sr. 51.14 |
| `away_ball_possession_ft` | int64 | 100.0% | od 15 do 83, sr. 48.86 |
| `home_total_shots_ft` | int64 | 100.0% | od 1 do 41, sr. 13.94 |
| `away_total_shots_ft` | int64 | 100.0% | od 0 do 30, sr. 11.23 |
| `home_shots_on_target_ft` | int64 | 100.0% | od 0 do 14, sr. 4.77 |
| `away_shots_on_target_ft` | int64 | 100.0% | od 0 do 12, sr. 3.86 |
| `home_shots_off_target_ft` | int64 | 100.0% | od 0 do 18, sr. 5.39 |
| `away_shots_off_target_ft` | int64 | 100.0% | od 0 do 16, sr. 4.31 |
| `home_fouls_ft` | int64 | 100.0% | od 2 do 27, sr. 11.60 |
| `away_fouls_ft` | int64 | 100.0% | od 2 do 29, sr. 11.92 |
| `home_goalkeeper_saves_ft` | int64 | 100.0% | od 0 do 11, sr. 2.61 |
| `away_goalkeeper_saves_ft` | int64 | 100.0% | od 0 do 12, sr. 3.21 |

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Statistics_1H``

1 MB · **1,750 wierszy** · 29 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120872, sr. 87644.16 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 19:00:00 .. 2026-05-24 21:00:00 |
| `country` | object | 100.0% | 5 wartosci: England, France, Germany, Italy, Spain |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `league` | object | 100.0% | 5 wartosci: Bundesliga, LaLiga, Ligue 1, Premier League, Serie A |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team` | object | 100.0% | 96 unikalnych, np. Villarreal; AC Milan |
| `away_team` | object | 100.0% | 96 unikalnych, np. Atl. Madrid; Cagliari |
| `referee` | object | 100.0% | 129 unikalnych, np. Miguel Sesma Espinosa; Marco Guida |
| `home_corners_1h` | int64 | 100.0% | od 0 do 13, sr. 2.49 |
| `away_corners_1h` | int64 | 100.0% | od 0 do 10, sr. 1.98 |
| `home_yellow_cards_1h` | int64 | 100.0% | od 0 do 4, sr. 0.57 |
| `away_yellow_cards_1h` | int64 | 100.0% | od 0 do 4, sr. 0.68 |
| `home_red_cards_1h` | int64 | 100.0% | od 0 do 2, sr. 0.02 |
| `away_red_cards_1h` | int64 | 100.0% | od 0 do 2, sr. 0.03 |
| `home_xg_1h` | float64 | 100.0% | od 0 do 3.68, sr. 0.69 |
| `away_xg_1h` | float64 | 100.0% | od 0 do 4.56, sr. 0.55 |
| `home_ball_possession_1h` | int64 | 100.0% | od 16 do 87, sr. 51.25 |
| `away_ball_possession_1h` | int64 | 100.0% | od 13 do 84, sr. 48.75 |
| `home_total_shots_1h` | int64 | 100.0% | od 0 do 19, sr. 6.30 |
| `away_total_shots_1h` | int64 | 100.0% | od 0 do 18, sr. 5.06 |
| `home_shots_on_target_1h` | int64 | 100.0% | od 0 do 9, sr. 2.14 |
| `away_shots_on_target_1h` | int64 | 100.0% | od 0 do 8, sr. 1.74 |
| `home_shots_off_target_1h` | int64 | 100.0% | od 0 do 8, sr. 2.44 |
| `away_shots_off_target_1h` | int64 | 100.0% | od 0 do 11, sr. 1.93 |
| `home_fouls_1h` | int64 | 100.0% | od 0 do 16, sr. 5.46 |
| `away_fouls_1h` | int64 | 100.0% | od 0 do 14, sr. 5.67 |
| `home_goalkeeper_saves_1h` | int64 | 100.0% | od 0 do 6, sr. 1.20 |
| `away_goalkeeper_saves_1h` | int64 | 100.0% | od 0 do 7, sr. 1.46 |

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Statistics_2H``

1 MB · **1,750 wierszy** · 29 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120872, sr. 87644.16 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 19:00:00 .. 2026-05-24 21:00:00 |
| `country` | object | 100.0% | 5 wartosci: England, France, Germany, Italy, Spain |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `league` | object | 100.0% | 5 wartosci: Bundesliga, LaLiga, Ligue 1, Premier League, Serie A |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team` | object | 100.0% | 96 unikalnych, np. Villarreal; Verona |
| `away_team` | object | 100.0% | 96 unikalnych, np. Atl. Madrid; AS Roma |
| `referee` | object | 100.0% | 129 unikalnych, np. Miguel Sesma Espinosa; Simone Sozza |
| `home_corners_2h` | int64 | 100.0% | od 0 do 13, sr. 2.83 |
| `away_corners_2h` | int64 | 100.0% | od 0 do 13, sr. 2.25 |
| `home_yellow_cards_2h` | int64 | 100.0% | od 0 do 7, sr. 1.24 |
| `away_yellow_cards_2h` | int64 | 100.0% | od 0 do 8, sr. 1.42 |
| `home_red_cards_2h` | int64 | 100.0% | od 0 do 3, sr. 0.07 |
| `away_red_cards_2h` | int64 | 100.0% | od 0 do 2, sr. 0.07 |
| `home_xg_2h` | float64 | 100.0% | od 0 do 4.08, sr. 0.86 |
| `away_xg_2h` | float64 | 100.0% | od 0 do 3.99, sr. 0.68 |
| `home_ball_possession_2h` | int64 | 100.0% | od 14 do 86, sr. 51.05 |
| `away_ball_possession_2h` | int64 | 100.0% | od 14 do 86, sr. 48.95 |
| `home_total_shots_2h` | int64 | 100.0% | od 0 do 27, sr. 7.64 |
| `away_total_shots_2h` | int64 | 100.0% | od 0 do 19, sr. 6.17 |
| `home_shots_on_target_2h` | int64 | 100.0% | od 0 do 10, sr. 2.63 |
| `away_shots_on_target_2h` | int64 | 100.0% | od 0 do 10, sr. 2.12 |
| `home_shots_off_target_2h` | int64 | 100.0% | od 0 do 11, sr. 2.95 |
| `away_shots_off_target_2h` | int64 | 100.0% | od 0 do 10, sr. 2.38 |
| `home_fouls_2h` | int64 | 100.0% | od 0 do 16, sr. 6.13 |
| `away_fouls_2h` | int64 | 100.0% | od 0 do 19, sr. 6.25 |
| `home_goalkeeper_saves_2h` | int64 | 100.0% | od 0 do 7, sr. 1.41 |
| `away_goalkeeper_saves_2h` | int64 | 100.0% | od 0 do 8, sr. 1.75 |

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Closing_Odds``

1 MB · **1,750 wierszy** · 33 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120872, sr. 87644.16 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 19:00:00 .. 2026-05-24 21:00:00 |
| `country` | object | 100.0% | 5 wartosci: England, France, Germany, Italy, Spain |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `league` | object | 100.0% | 5 wartosci: Bundesliga, LaLiga, Ligue 1, Premier League, Serie A |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team` | object | 100.0% | 96 unikalnych, np. Villarreal; Verona |
| `away_team` | object | 100.0% | 96 unikalnych, np. Atl. Madrid; AS Roma |
| `home_win_closing_odds` | float64 | 100.0% | od 1.07 do 16.6, sr. 2.83 |
| `draw_closing_odds` | float64 | 100.0% | od 2.67 do 16.5, sr. 4.06 |
| `away_win_closing_odds` | float64 | 100.0% | od 1.16 do 29, sr. 4.23 |
| `over_0_5_goals_closing_odds` | float64 | 99.4% | od 1.01 do 1.21, sr. 1.05 |
| `under_0_5_goals_closing_odds` | float64 | 99.4% | od 4.5 do 34, sr. 13.11 |
| `over_1_5_goals_closing_odds` | float64 | 100.0% | od 1.02 do 1.89, sr. 1.28 |
| `under_1_5_goals_closing_odds` | float64 | 100.0% | od 2 do 16, sr. 4.22 |
| `over_2_5_goals_closing_odds` | float64 | 100.0% | od 1.11 do 3.71, sr. 1.91 |
| `under_2_5_goals_closing_odds` | float64 | 100.0% | od 1.29 do 6.91, sr. 2.13 |
| `over_3_5_goals_closing_odds` | float64 | 100.0% | od 1.3 do 8.08, sr. 3.20 |
| `under_3_5_goals_closing_odds` | float64 | 100.0% | od 1.09 do 3.62, sr. 1.45 |
| `over_4_5_goals_closing_odds` | float64 | 100.0% | od 1.67 do 15, sr. 6.13 |
| `under_4_5_goals_closing_odds` | float64 | 100.0% | od 1.02 do 2.23, sr. 1.17 |
| `home_ah_minus_1_5_closing_odds` | float64 | 98.3% | od 1.24 do 17, sr. 5.12 |
| `away_ah_minus_1_5_closing_odds` | float64 | 98.3% | od 1.01 do 4.07, sr. 1.34 |
| `home_ah_minus_1_closing_odds` | float64 | 99.1% | od 1.08 do 17, sr. 4.38 |
| `away_ah_minus_1_closing_odds` | float64 | 99.1% | od 1.01 do 8.06, sr. 1.56 |
| `home_ah_0_closing_odds` | float64 | 100.0% | od 1.01 do 12.9, sr. 2.06 |
| `away_ah_0_closing_odds` | float64 | 100.0% | od 1.03 do 26, sr. 3.11 |
| `home_ah_plus_1_closing_odds` | float64 | 92.8% | od 1.01 do 4.35, sr. 1.29 |
| `away_ah_plus_1_closing_odds` | float64 | 92.8% | od 1.22 do 17, sr. 6.39 |
| `home_ah_plus_1_5_closing_odds` | float64 | 91.3% | od 1.01 do 2.62, sr. 1.19 |
| `away_ah_plus_1_5_closing_odds` | float64 | 91.3% | od 1.49 do 17, sr. 7.28 |
| `btts_yes_closing_odds` | float64 | 100.0% | od 1.28 do 2.68, sr. 1.78 |
| `btts_no_closing_odds` | float64 | 100.0% | od 1.47 do 3.68, sr. 2.11 |

### `footiqo/big_five_2025_26.xlsx  →  arkusz `Field_Legend``

1 MB · **175 wierszy** · 6 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `Big Five 2025/26 Database — Field & Code Legend` | object | 97.7% | 27 unikalnych, np. Use this sheet to interpret abbreviations, coded values, mis |
| `Unnamed: 1` | object | 95.4% | 132 unikalnych, np. Value / abbreviation; H |
| `Unnamed: 2` | object | 95.4% | 131 unikalnych, np. Meaning; Home-team win |
| `Unnamed: 3` | object | 95.4% | 33 unikalnych, np. Example; result_ft = H |
| `Unnamed: 4` | object | 95.4% | 20 unikalnych, np. Applies to; result_1h, result_2h, result_ft |
| `Unnamed: 5` | object | 50.9% | 35 unikalnych, np. Important note; The result refers to the stated match period |

### `footiqo/corners_cards_closing_PL_2025_26.xlsx  →  arkusz `Corners_Closing_Odds``

105 KB · **380 wierszy** · 24 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120850, sr. 86297.53 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 21:00:00 .. 2026-05-24 17:00:00 |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `country` | object | 100.0% | 1 wartosci: England |
| `league` | object | 100.0% | 1 wartosci: Premier League |
| `league_id` | int64 | 100.0% | od 2 do 2, sr. 2.00 |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team_id` | int64 | 100.0% | od 55 do 89, sr. 65.60 |
| `home_team` | object | 100.0% | 20 unikalnych, np. Brighton; Burnley |
| `away_team_id` | int64 | 100.0% | od 55 do 89, sr. 65.60 |
| `away_team` | object | 100.0% | 20 unikalnych, np. Manchester Utd; Wolves |
| `referee_id` | int64 | 100.0% | od 58 do 550, sr. 84.41 |
| `referee` | object | 100.0% | 23 unikalnych, np. Samuel Barrott; Andrew Kitchen |
| `home_corners_ft` | int64 | 100.0% | od 0 do 16, sr. 5.38 |
| `away_corners_ft` | int64 | 100.0% | od 0 do 15, sr. 4.59 |
| `total_corners_ft` | int64 | 100.0% | od 3 do 19, sr. 9.98 |
| `over_7_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.12 do 1.41, sr. 1.21 |
| `under_7_5_corners_ft_closing_odds` | float64 | 100.0% | od 2.93 do 6.33, sr. 4.58 |
| `over_8_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.25 do 1.75, sr. 1.41 |
| `under_8_5_corners_ft_closing_odds` | float64 | 100.0% | od 2.08 do 3.94, sr. 3.00 |
| `over_9_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.46 do 2.3, sr. 1.73 |
| `under_9_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.62 do 2.7, sr. 2.16 |
| `over_10_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.8 do 3.23, sr. 2.24 |
| `under_10_5_corners_ft_closing_odds` | float64 | 100.0% | od 1.34 do 2.01, sr. 1.68 |

### `footiqo/corners_cards_closing_PL_2025_26.xlsx  →  arkusz `Cards_Closing_Odds``

105 KB · **380 wierszy** · 24 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `match_id` | int64 | 100.0% | od 64370 do 120850, sr. 86297.53 |
| `start_datetime` | datetime64[ns] | 100.0% | zakres 2025-08-15 21:00:00 .. 2026-05-24 17:00:00 |
| `competition_type` | object | 100.0% | 1 wartosci: Domestic Leagues |
| `country` | object | 100.0% | 1 wartosci: England |
| `league` | object | 100.0% | 1 wartosci: Premier League |
| `league_id` | int64 | 100.0% | od 2 do 2, sr. 2.00 |
| `season` | object | 100.0% | 1 wartosci: 2025/2026 |
| `home_team_id` | int64 | 100.0% | od 55 do 89, sr. 65.60 |
| `home_team` | object | 100.0% | 20 unikalnych, np. Brighton; Burnley |
| `away_team_id` | int64 | 100.0% | od 55 do 89, sr. 65.60 |
| `away_team` | object | 100.0% | 20 unikalnych, np. Manchester Utd; Wolves |
| `referee_id` | int64 | 100.0% | od 58 do 550, sr. 84.41 |
| `referee` | object | 100.0% | 23 unikalnych, np. Samuel Barrott; Andrew Kitchen |
| `home_yellow_cards_ft` | int64 | 100.0% | od 0 do 5, sr. 1.72 |
| `away_yellow_cards_ft` | int64 | 100.0% | od 0 do 6, sr. 2.12 |
| `total_yellow_cards_ft` | int64 | 100.0% | od 0 do 10, sr. 3.84 |
| `over_2_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.02 do 3.75, sr. 1.27 |
| `under_2_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.27 do 13.52, sr. 4.36 |
| `over_3_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.14 do 9.55, sr. 1.74 |
| `under_3_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.06 do 5.67, sr. 2.33 |
| `over_4_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.37 do 25, sr. 2.74 |
| `under_4_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.01 do 3.07, sr. 1.57 |
| `over_5_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.8 do 25, sr. 4.84 |
| `under_5_5_yellow_cards_ft_closing_odds` | float64 | 100.0% | od 1.01 do 2, sr. 1.24 |

### `footiqo/corners_cards_closing_PL_2025_26.xlsx  →  arkusz `Field_Legend``

105 KB · **33 wierszy** · 6 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `Footiqo Corners & Cards Closing Odds Database — Field & Code Legend` | object | 87.9% | 13 unikalnych, np. General codes and conventions; Item |
| `Unnamed: 1` | object | 78.8% | 24 unikalnych, np. Value / abbreviation; FT |
| `Unnamed: 2` | object | 78.8% | 24 unikalnych, np. Meaning; Full time / regulation time |
| `Unnamed: 3` | object | 60.6% | 16 unikalnych, np. Example; total_corners_ft |
| `Unnamed: 4` | object | 78.8% | 4 wartosci: Applies to, Both sheets, Cards_Closing_Odds, Corners_Closing_Odds |
| `Unnamed: 5` | object | 42.4% | 12 unikalnych, np. Important note; Extra time and penalty shoot-outs are not in |


---

## `osm`

- **Zrodlo:** OpenStreetMap Overpass API
- **Licencja:** ODbL
- **Odtworzenie:** `zapytanie Overpass o leisure=stadium w bbox Europy, filtr capacity>8000`
- **Uwagi:** Tylko 242 stadiony. Dopasowanie do nazw z transfermarktu wynosi 3,7% — patrz nota 31.

1 plikow nietabelarycznych (.json).


---

## `schochastics`

- **Zrodlo:** github.com/schochastics/football-data
- **Licencja:** otwarte
- **Odtworzenie:** `curl -sL https://github.com/schochastics/football-data/raw/master/data/results/games.parquet -o games.parquet`
- **Uwagi:** Najglebsza historia (od 1888), ale tylko wyniki. Do priorow i dlugich szeregow.

### `schochastics/games.parquet`

16 MB · **1,309,501 wierszy** · 17 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `home` | object | 99.8% | 8,346 unikalnych, np. Bolton Wanderers; Everton |
| `away` | object | 99.8% | 8,369 unikalnych, np. Derby County; Accrington FC |
| `date` | object | 100.0% | zakres 1888-09-08 .. 2025-09-30 |
| `gh` | float64 | 100.0% | od 0 do 37, sr. 1.61 |
| `ga` | float64 | 100.0% | od 0 do 26, sr. 1.16 |
| `full_time` | object | 100.0% | 3 wartosci: E, F, P |
| `competition` | object | 99.4% | 224 unikalnych, np. england; england |
| `home_ident` | object | 100.0% | 8,484 unikalnych, np. Bolton Wanderers (England); Everton (England) |
| `away_ident` | object | 100.0% | 8,507 unikalnych, np. Derby County (England); Accrington FC (England) |
| `home_country` | object | 100.0% | 222 unikalnych, np. england; england |
| `away_country` | object | 100.0% | 222 unikalnych, np. england; england |
| `home_code` | object | 100.0% | 223 unikalnych, np. ENG; ENG |
| `away_code` | object | 100.0% | 223 unikalnych, np. ENG; ENG |
| `home_continent` | object | 100.0% | 7 wartosci: , Africa, Asia, Europe, North America, Oceania, South America |
| `away_continent` | object | 100.0% | 7 wartosci: , Africa, Asia, Europe, North America, Oceania, South America |
| `continent` | object | 100.0% | 7 wartosci: Africa, Asia, Europe, North America, Oceania, South America, world |
| `level` | object | 100.0% | 2 wartosci: international, national |

### `schochastics/pol-ekstraklasa.csv`

2 MB · **20,434 wierszy** · 8 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `scoring_player` | object | 100.0% | 2,406 unikalnych, np. Dariusz Podolski; Wojciech Małocha |
| `scoring_team` | object | 100.0% | 55 unikalnych, np. Widzew Łódź; Widzew Łódź |
| `time` | object | 100.0% | 116 unikalnych, np. 40; 52 |
| `GH` | int64 | 100.0% | od 0 do 9, sr. 1.34 |
| `GA` | int64 | 100.0% | od 0 do 7, sr. 0.95 |
| `game` | object | 100.0% | 5,407 unikalnych, np. Warta Poznań vs. Widzew Łódź 0:4; Warta Poznań vs. Widzew Łó |
| `date` | object | 100.0% | zakres 1994-07-30 .. 2024-11-08 |
| `season` | object | 98.0% | 31 unikalnych, np. 1994-1995; 1994-1995 |


---

## `statsbomb`

- **Zrodlo:** github.com/statsbomb/open-data
- **Licencja:** StatsBomb, niekomercyjne
- **Odtworzenie:** `curl -sL https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json`
- **Uwagi:** Sam katalog rozgrywek. Pelne dane to osobny zestaw statsbomb-full.

1 plikow nietabelarycznych (.json).


---

## `statsbomb-full`

- **Zrodlo:** github.com/statsbomb/open-data (shallow clone)
- **Licencja:** StatsBomb, niekomercyjne
- **Odtworzenie:** `git clone --depth 1 https://github.com/statsbomb/open-data.git`
- **Uwagi:** 17 GB. NIE jest ciagly zbior ligowy: La Liga to sezony Messiego, Champions League to same finaly. Laboratorium metodologiczne, nie zrodlo do modelu.

Zbior plikow JSON, nie tabelaryczny. Struktura:

- `data/events/` — 4,235 plikow, 12 GB
- `data/lineups/` — 4,235 plikow, 79 MB
- `data/matches/` — 80 plikow, 7 MB
- `data/three-sixty/` — 426 plikow, 3 GB


---

## `transfermarkt`

- **Zrodlo:** Kaggle davidcariboo/player-scores (scraping transfermarkt.co.uk)
- **Licencja:** CC0 public domain
- **Odtworzenie:** `curl -sL -H "Authorization: Bearer $KAGGLE_TOKEN" https://www.kaggle.com/api/v1/datasets/download/davidcariboo/player-scores/<plik>.csv`
- **Uwagi:** Aktualizacje wstrzymane od lipca 2026 — dane kompletne do 2026-07-06. Jedyne zrodlo skladow i sedziego w 99%.

### `transfermarkt/players.csv`

19 MB · **50,149 wierszy** · 26 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `player_id` | int64 | 100.0% | od 10 do 1.55576e+06, sr. 512784.46 |
| `first_name` | object | 93.7% | 10,800 unikalnych, np. Miroslav; Roman |
| `last_name` | object | 100.0% | 33,876 unikalnych, np. Klose; Weidenfeller |
| `name` | object | 100.0% | 48,825 unikalnych, np. Miroslav Klose; Roman Weidenfeller |
| `last_season` | int64 | 100.0% | od 2012 do 2025, sr. 2021.67 |
| `current_club_id` | int64 | 100.0% | od 2 do 142301, sr. 8547.64 |
| `player_code` | object | 100.0% | 48,738 unikalnych, np. miroslav-klose; roman-weidenfeller |
| `country_of_birth` | object | 88.3% | 211 unikalnych, np. Poland; Germany |
| `city_of_birth` | object | 88.8% | 12,335 unikalnych, np. Opole  ; Diez   |
| `country_of_citizenship` | object | 99.5% | 202 unikalnych, np. Germany; Germany |
| `date_of_birth` | object | 99.9% | zakres 1968-07-31 00:00:00 .. 2011-02-23 00:00:00 |
| `sub_position` | object | 98.8% | 13 unikalnych, np. Centre-Forward; Goalkeeper |
| `position` | object | 100.0% | 5 wartosci: Attack, Defender, Goalkeeper, Midfield, Missing |
| `foot` | object | 88.2% | 3 wartosci: both, left, right |
| `height_in_cm` | float64 | 91.3% | od 17 do 210, sr. 182.00 |
| `contract_expiration_date` | object | 63.0% | zakres 2000-05-31 00:00:00 .. 2035-06-30 00:00:00 |
| `agent_name` | object | 53.5% | 3,954 unikalnych, np. ASBW Sport Marketing; Neubauer 13 GmbH |
| `image_url` | object | 100.0% | 41,061 unikalnych, np. https://img.a.transfermarkt.technology/portrait/header/10-14 |
| `international_caps` | float64 | 38.6% | od 0 do 233, sr. 11.37 |
| `international_goals` | float64 | 38.6% | od 0 do 146, sr. 1.18 |
| `current_national_team_id` | float64 | 6.5% | od 3262 do 53982, sr. 9230.51 |
| `url` | object | 100.0% | 50,149 unikalnych, np. https://www.transfermarkt.co.uk/miroslav-klose/profil/spiele |
| `current_club_domestic_competition_id` | object | 94.0% | 32 unikalnych, np. IT1; L1 |
| `current_club_name` | object | 94.0% | 793 unikalnych, np. Società Sportiva Lazio S.p.A.; Borussia Dortmund |
| `market_value_in_eur` | float64 | 82.8% | od 10000 do 2e+08, sr. 1511049.29 |
| `highest_market_value_in_eur` | float64 | 82.8% | od 10000 do 2e+08, sr. 3450773.45 |

### `transfermarkt/games.csv`

28 MB · **88,958 wierszy** · 23 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `game_id` | int64 | 100.0% | od 25767 do 4.8689e+06, sr. 3393562.69 |
| `competition_id` | object | 100.0% | 70 unikalnych, np. FIWC; FIWC |
| `season` | int64 | 100.0% | od 2005 do 2025, sr. 2019.04 |
| `round` | object | 100.0% | 128 unikalnych, np. Round of 16; Round of 16 |
| `date` | object | 100.0% | zakres 2006-06-09 .. 2026-07-06 |
| `home_club_id` | int64 | 100.0% | od 1 do 134734, sr. 5963.32 |
| `away_club_id` | int64 | 100.0% | od 2 do 134734, sr. 5436.94 |
| `home_club_goals` | int64 | 100.0% | od 0 do 15, sr. 1.60 |
| `away_club_goals` | int64 | 100.0% | od 0 do 19, sr. 1.33 |
| `home_club_position` | float64 | 71.5% | od 1 do 21, sr. 9.11 |
| `away_club_position` | float64 | 71.5% | od 1 do 21, sr. 9.29 |
| `home_club_manager_name` | object | 99.1% | 6,137 unikalnych, np. Óscar Tabárez; Diego Maradona |
| `away_club_manager_name` | object | 99.1% | 5,756 unikalnych, np. Jung-moo Huh; Javier Aguirre |
| `stadium` | object | 99.7% | 2,968 unikalnych, np. Nelson Mandela Bay Stadium; FNB-Stadium |
| `attendance` | float64 | 87.9% | od 1 do 99354, sr. 18282.46 |
| `referee` | object | 99.2% | 3,135 unikalnych, np. Wolfgang Stark; Roberto Rosetti |
| `url` | object | 100.0% | 88,958 unikalnych, np. https://www.transfermarkt.co.uk/spielbericht/index/spielberi |
| `home_club_formation` | object | 90.8% | 57 unikalnych, np. 4-3-3 Defending; 4-4-2 Diamond |
| `away_club_formation` | object | 90.9% | 68 unikalnych, np. 4-3-3 Defending; 4-1-4-1 |
| `home_club_name` | object | 99.9% | 2,901 unikalnych, np. Uruguay; Argentina |
| `away_club_name` | object | 100.0% | 2,600 unikalnych, np. South Korea; Mexico |
| `aggregate` | object | 100.0% | 119 unikalnych, np. 2:1; 3:1 |
| `competition_type` | object | 98.6% | 5 wartosci: domestic_cup, domestic_league, international_cup, national_team_competition, other |

### `transfermarkt/clubs.csv`

203 KB · **796 wierszy** · 17 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `club_id` | int64 | 100.0% | od 3 do 118373, sr. 9010.48 |
| `club_code` | object | 100.0% | 796 unikalnych, np. arminia-bielefeld; paris-fc |
| `name` | object | 100.0% | 796 unikalnych, np. Arminia Bielefeld; Paris FC |
| `domestic_competition_id` | object | 100.0% | 32 unikalnych, np. L1; FR1 |
| `total_market_value` | float64 | 0.0% | — |
| `squad_size` | int64 | 100.0% | od 0 do 50, sr. 26.78 |
| `average_age` | float64 | 95.2% | od 0 do 30.1, sr. 25.46 |
| `foreigners_number` | int64 | 100.0% | od 0 do 36, sr. 10.55 |
| `foreigners_percentage` | float64 | 92.5% | od 2.4 do 100, sr. 40.04 |
| `national_team_players` | int64 | 100.0% | od 0 do 24, sr. 3.92 |
| `stadium_name` | object | 100.0% | 760 unikalnych, np. SchücoArena; Stade Jean Bouin |
| `stadium_seats` | int64 | 100.0% | od 0 do 87523, sr. 24269.77 |
| `net_transfer_record` | object | 100.0% | 482 unikalnych, np. +€5.90m; €-25.00m |
| `coach_name` | object | 50.6% | 395 unikalnych, np. Stéphane Gilli; Steve Cooper |
| `last_season` | int64 | 100.0% | od 2012 do 2025, sr. 2023.39 |
| `filename` | object | 100.0% | 14 unikalnych, np. ../data/raw/transfermarkt-scraper/2021/clubs.json.gz; ../dat |
| `url` | object | 100.0% | 796 unikalnych, np. https://www.transfermarkt.co.uk/arminia-bielefeld/startseite |

### `transfermarkt/appearances.csv`

189 MB · **1,894,350 wierszy** · 13 kolumn *(statystyki z probki 300,000 wierszy: poczatek + koniec pliku)*

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `appearance_id` | object | 100.0% | 300,000 unikalnych, np. 2231978_38004; 2233748_79232 |
| `game_id` | int64 | 100.0% | od 2.21161e+06 do 4.8689e+06, sr. 3459436.16 |
| `player_id` | int64 | 100.0% | od 10 do 1.52844e+06, sr. 295670.35 |
| `player_club_id` | int64 | 100.0% | od 1 do 132877, sr. 3697.79 |
| `player_current_club_id` | int64 | 100.0% | od 2 do 127001, sr. 4756.58 |
| `date` | object | 100.0% | zakres 2012-07-03 .. 2026-06-28 |
| `player_name` | object | 100.0% | 15,754 unikalnych, np. Aurélien Joachim; Ruslan Abyshov |
| `competition_id` | object | 100.0% | 48 unikalnych, np. CLQ; ELQ |
| `yellow_cards` | int64 | 100.0% | od 0 do 2, sr. 0.14 |
| `red_cards` | int64 | 100.0% | od 0 do 1, sr. 0.00 |
| `goals` | int64 | 100.0% | od 0 do 5, sr. 0.10 |
| `assists` | int64 | 100.0% | od 0 do 5, sr. 0.08 |
| `minutes_played` | int64 | 100.0% | od 0 do 120, sr. 67.91 |

### `transfermarkt/game_events.csv`

171 MB · **1,274,469 wierszy** · 11 kolumn *(statystyki z probki 300,000 wierszy: poczatek + koniec pliku)*

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `game_event_id` | object | 100.0% | 300,000 unikalnych, np. 60d28dc455d299f5abdb65d7204fef40; 71280d139049475e02eb762789 |
| `date` | object | 100.0% | zakres 2006-06-11 .. 2026-07-06 |
| `game_id` | int64 | 100.0% | od 46379 do 4.8689e+06, sr. 3470255.04 |
| `minute` | int64 | 100.0% | od -1 do 120, sr. 62.04 |
| `type` | object | 100.0% | 4 wartosci: Cards, Goals, Shootout, Substitutions |
| `club_id` | int64 | 100.0% | od 1 do 134732, sr. 6178.83 |
| `club_name` | object | 100.0% | 2,066 unikalnych, np. South Korea; South Korea |
| `player_id` | int64 | 100.0% | od 10 do 1.52278e+06, sr. 317437.02 |
| `description` | object | 98.3% | 5,079 unikalnych, np. 1. Yellow card  , Foul; 1. Yellow card  , Foul |
| `player_in_id` | float64 | 49.5% | od 10 do 1.56412e+06, sr. 392625.02 |
| `player_assist_id` | float64 | 15.2% | od 10 do 1.5169e+06, sr. 269489.67 |

### `transfermarkt/club_games.csv`

14 MB · **177,916 wierszy** · 11 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `game_id` | int64 | 100.0% | od 25767 do 4.8689e+06, sr. 3393562.69 |
| `club_id` | int64 | 100.0% | od 1 do 134734, sr. 5700.13 |
| `own_goals` | int64 | 100.0% | od 0 do 19, sr. 1.46 |
| `own_position` | float64 | 71.5% | od 1 do 21, sr. 9.20 |
| `own_manager_name` | object | 99.1% | 7,031 unikalnych, np. Óscar Tabárez; Jung-moo Huh |
| `opponent_id` | int64 | 100.0% | od 1 do 134734, sr. 5700.13 |
| `opponent_goals` | int64 | 100.0% | od 0 do 19, sr. 1.46 |
| `opponent_position` | float64 | 71.5% | od 1 do 21, sr. 9.20 |
| `opponent_manager_name` | object | 99.1% | 7,031 unikalnych, np. Jung-moo Huh; Óscar Tabárez |
| `hosting` | object | 100.0% | 2 wartosci: Away, Home |
| `is_win` | int64 | 100.0% | od 0 do 1, sr. 0.39 |

### `transfermarkt/competitions.csv`

12 KB · **65 wierszy** · 11 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `competition_id` | object | 100.0% | 65 unikalnych, np. A1; AFAC |
| `competition_code` | object | 100.0% | 62 unikalnych, np. bundesliga; afc-asian-cup |
| `name` | object | 100.0% | 62 unikalnych, np. bundesliga; afc-asian-cup |
| `sub_type` | object | 100.0% | 16 unikalnych, np. first_tier; afc_asian_cup |
| `type` | object | 100.0% | 5 wartosci: domestic_cup, domestic_league, international_cup, national_team_competition, other |
| `country_id` | int64 | 100.0% | od -1 do 215, sr. 86.49 |
| `country_name` | object | 81.5% | 31 unikalnych, np. Austria; Argentina |
| `domestic_league_code` | object | 81.5% | 31 unikalnych, np. A1; ARG1 |
| `confederation` | object | 100.0% | 5 wartosci: afrika, amerika, asien, europa, fifa |
| `total_clubs` | float64 | 76.9% | od 10 do 30, sr. 17.16 |
| `url` | object | 100.0% | 65 unikalnych, np. https://www.transfermarkt.co.uk/bundesliga/startseite/wettbe |

### `transfermarkt/game_lineups.csv`

397 MB · **3,179,016 wierszy** · 10 kolumn *(statystyki z probki 300,000 wierszy: poczatek + koniec pliku)*

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `game_lineups_id` | object | 100.0% | 300,000 unikalnych, np. b2dbe01c3656b06c8e23e9de714e26bb; b50a3ec6d52fd1490aab42042a |
| `date` | object | 100.0% | zakres 2013-07-02 .. 2026-07-06 |
| `game_id` | int64 | 100.0% | od 2.31726e+06 do 4.8689e+06, sr. 3550233.20 |
| `player_id` | int64 | 100.0% | od 10 do 1.56412e+06, sr. 361155.73 |
| `club_id` | int64 | 100.0% | od 1 do 134734, sr. 7602.80 |
| `player_name` | object | 100.0% | 39,294 unikalnych, np. Christian Poulsen; Niklas Moisander |
| `type` | object | 100.0% | 2 wartosci: starting_lineup, substitutes |
| `position` | object | 100.0% | 18 unikalnych, np. Defensive Midfield; Centre-Back |
| `number` | object | 100.0% | 150 unikalnych, np. 5; 4 |
| `team_captain` | int64 | 100.0% | od 0 do 1, sr. 0.05 |

### `transfermarkt/transfers.csv`

17 MB · **175,165 wierszy** · 10 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `player_id` | int64 | 100.0% | od 2989 do 1.54544e+06, sr. 505230.24 |
| `transfer_date` | object | 100.0% | zakres 1993-07-01 .. 2030-06-30 |
| `transfer_season` | object | 100.0% | 35 unikalnych, np. 25/26; 27/28 |
| `from_club_id` | int64 | 100.0% | od 1 do 142714, sr. 21072.17 |
| `to_club_id` | int64 | 100.0% | od 1 do 142487, sr. 16527.06 |
| `from_club_name` | object | 100.0% | 18,285 unikalnych, np. Reggiana; Gimcheon Sangmu |
| `to_club_name` | object | 100.0% | 14,086 unikalnych, np. FC Empoli; Jeju SK |
| `transfer_fee` | float64 | 64.9% | od 0 do 2.22e+08, sr. 739835.97 |
| `market_value_in_eur` | float64 | 61.1% | od 10000 do 1.8e+08, sr. 1753553.92 |
| `player_name` | object | 100.0% | 23,036 unikalnych, np. Luca Belardinelli; Chan-gi An |

### `transfermarkt/player_valuations.csv`

38 MB · **656,301 wierszy** · 6 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `player_id` | int64 | 100.0% | od 10 do 1.52329e+06, sr. 299884.35 |
| `date` | object | 100.0% | zakres 2000-01-20 .. 2026-06-12 |
| `market_value_in_eur` | int64 | 100.0% | od 0 do 2e+08, sr. 2290092.74 |
| `current_club_name` | object | 100.0% | 8,055 unikalnych, np. Unknown; Unknown |
| `current_club_id` | int64 | 100.0% | od 1 do 136215, sr. 6707.55 |
| `player_club_domestic_competition_id` | object | 85.8% | 32 unikalnych, np. BE1; SC1 |


---

## `understat`

- **Zrodlo:** understat.com przez biblioteke soccerdata
- **Licencja:** scraping wobec ToS — tylko research
- **Odtworzenie:** `PYTHONPATH=.pylibs python3 scripts/06_download_understat.py`
- **Uwagi:** xG dla top 5 lig. Pliki *_schedule to mecze, *_team_match_stats to statystyki druzynowe.

*2 plikow o identycznej strukturze (`eng_team_match_stats.csv` … `ita_team_match_stats.csv`) — opis reprezentanta `eng_team_match_stats.csv`.*

*3 plikow o identycznej strukturze (`esp_team_match_stats.csv` … `ger_team_match_stats.csv`) — opis reprezentanta `esp_team_match_stats.csv`.*

*5 plikow o identycznej strukturze (`eng_schedule.csv` … `ita_schedule.csv`) — opis reprezentanta `eng_schedule.csv`.*

### `understat/eng_team_match_stats.csv`

1 MB · **4,180 wierszy** · 29 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `league` | object | 100.0% | 1 wartosci: ENG-Premier League |
| `season` | int64 | 100.0% | od 1415 do 2526, sr. 1956.73 |
| `game` | object | 100.0% | zakres 2014-08-16 Arsenal-Crystal Palace .. 2026-05-24 West Ham-Leeds |
| `league_id` | int64 | 100.0% | od 1 do 1, sr. 1.00 |
| `season_id` | int64 | 100.0% | od 2014 do 2025, sr. 2019.36 |
| `game_id` | int64 | 100.0% | od 81 do 29157, sr. 13351.82 |
| `date` | object | 100.0% | zakres 2014-08-16 12:45:00 .. 2026-05-24 15:00:00 |
| `home_team_id` | int64 | 100.0% | od 71 do 285, sr. 109.59 |
| `away_team_id` | int64 | 100.0% | od 71 do 285, sr. 109.59 |
| `home_team` | object | 100.0% | 35 unikalnych, np. Arsenal; Leicester |
| `away_team` | object | 100.0% | 35 unikalnych, np. Crystal Palace; Everton |
| `away_team_code` | object | 100.0% | 35 unikalnych, np. CRY; EVE |
| `home_team_code` | object | 100.0% | 35 unikalnych, np. ARS; LEI |
| `away_points` | int64 | 100.0% | od 0 do 3, sr. 1.19 |
| `away_expected_points` | float64 | 100.0% | od 0.0004 do 2.9994, sr. 1.20 |
| `away_goals` | int64 | 100.0% | od 0 do 9, sr. 1.25 |
| `away_xg` | float64 | 100.0% | od 0 do 5.8331, sr. 1.27 |
| `away_np_xg` | float64 | 100.0% | od 0 do 5.8331, sr. 1.19 |
| `away_np_xg_difference` | float64 | 100.0% | od -5.71514 do 5.36808, sr. -0.28 |
| `away_ppda` | float64 | 100.0% | od 2.44828 do 116.75, sr. 13.04 |
| `away_deep_completions` | int64 | 100.0% | od 0 do 30, sr. 6.18 |
| `home_points` | int64 | 100.0% | od 0 do 3, sr. 1.57 |
| `home_expected_points` | float64 | 100.0% | od 0.0003 do 2.9992, sr. 1.58 |
| `home_goals` | int64 | 100.0% | od 0 do 9, sr. 1.55 |
| `home_xg` | float64 | 100.0% | od 0 do 6.67151, sr. 1.58 |
| `home_np_xg` | float64 | 100.0% | od 0 do 6.05305, sr. 1.47 |
| `home_np_xg_difference` | float64 | 100.0% | od -5.36808 do 5.71514, sr. 0.28 |
| `home_ppda` | float64 | 100.0% | od 2.30303 do 193, sr. 11.76 |
| `home_deep_completions` | int64 | 100.0% | od 0 do 37, sr. 7.56 |

### `understat/esp_team_match_stats.csv`

1001 KB · **4,180 wierszy** · 29 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `league` | object | 100.0% | 1 wartosci: ESP-La Liga |
| `season` | int64 | 100.0% | od 1415 do 2526, sr. 1956.73 |
| `game` | object | 100.0% | zakres 2014-08-23 Almeria-Espanyol .. 2026-05-24 Villarreal-Atletico Madrid |
| `league_id` | int64 | 100.0% | od 4 do 4, sr. 4.00 |
| `season_id` | int64 | 100.0% | od 2014 do 2025, sr. 2019.36 |
| `game_id` | int64 | 100.0% | od 1399 do 29537, sr. 14038.65 |
| `date` | object | 100.0% | zakres 2014-08-23 18:00:00 .. 2026-05-24 19:00:00 |
| `home_team_id` | int64 | 100.0% | od 137 do 289, sr. 158.97 |
| `away_team_id` | int64 | 100.0% | od 137 do 289, sr. 158.97 |
| `home_team` | object | 100.0% | 32 unikalnych, np. Almeria; Granada |
| `away_team` | object | 100.0% | 32 unikalnych, np. Espanyol; Deportivo La Coruna |
| `away_team_code` | object | 100.0% | 30 unikalnych, np. ESP; DEP |
| `home_team_code` | object | 100.0% | 30 unikalnych, np. ALM; GRA |
| `home_points` | int64 | 100.0% | od 0 do 3, sr. 1.63 |
| `home_expected_points` | float64 | 100.0% | od 0.0018 do 2.9988, sr. 1.64 |
| `home_goals` | int64 | 100.0% | od 0 do 10, sr. 1.51 |
| `home_xg` | float64 | 100.0% | od 0.0503369 do 6.61091, sr. 1.57 |
| `home_np_xg` | float64 | 100.0% | od 0.0503369 do 6.61091, sr. 1.42 |
| `home_np_xg_difference` | float64 | 100.0% | od -6.27149 do 6.00797, sr. 0.36 |
| `home_ppda` | float64 | 100.0% | od 2.26923 do 50.2857, sr. 9.94 |
| `home_deep_completions` | int64 | 100.0% | od 0 do 42, sr. 6.45 |
| `away_points` | int64 | 100.0% | od 0 do 3, sr. 1.11 |
| `away_expected_points` | float64 | 100.0% | od 0.0006 do 2.997, sr. 1.12 |
| `away_goals` | int64 | 100.0% | od 0 do 8, sr. 1.14 |
| `away_xg` | float64 | 100.0% | od 0 do 7.11049, sr. 1.16 |
| `away_np_xg` | float64 | 100.0% | od 0 do 7.11049, sr. 1.06 |
| `away_np_xg_difference` | float64 | 100.0% | od -6.00797 do 6.27149, sr. -0.36 |
| `away_ppda` | float64 | 100.0% | od 2.36842 do 91.5, sr. 11.38 |
| `away_deep_completions` | int64 | 100.0% | od 0 do 28, sr. 4.94 |

### `understat/eng_schedule.csv`

859 KB · **4,560 wierszy** · 20 kolumn

| kolumna | typ | pokrycie | zakres / wartosci |
|---|---|---:|---|
| `league` | object | 100.0% | 1 wartosci: ENG-Premier League |
| `season` | int64 | 100.0% | od 1415 do 2526, sr. 1962.08 |
| `game` | object | 100.0% | zakres 2014-08-16 Arsenal-Crystal Palace .. 2026-05-24 West Ham-Leeds |
| `league_id` | int64 | 100.0% | od 1 do 1, sr. 1.00 |
| `season_id` | int64 | 100.0% | od 2014 do 2025, sr. 2019.42 |
| `game_id` | int64 | 100.0% | od 81 do 29157, sr. 13455.58 |
| `date` | object | 100.0% | zakres 2014-08-16 12:45:00 .. 2026-05-24 15:00:00 |
| `home_team_id` | int64 | 100.0% | od 71 do 285, sr. 110.35 |
| `away_team_id` | int64 | 100.0% | od 71 do 285, sr. 110.35 |
| `home_team` | object | 100.0% | 35 unikalnych, np. Arsenal; Leicester |
| `away_team` | object | 100.0% | 35 unikalnych, np. Crystal Palace; Everton |
| `away_team_code` | object | 100.0% | 35 unikalnych, np. CRY; EVE |
| `home_team_code` | object | 100.0% | 35 unikalnych, np. ARS; LEI |
| `home_goals` | int64 | 100.0% | od 0 do 9, sr. 1.53 |
| `away_goals` | int64 | 100.0% | od 0 do 9, sr. 1.26 |
| `home_xg` | float64 | 100.0% | od 0 do 6.67151, sr. 1.57 |
| `away_xg` | float64 | 100.0% | od 0 do 5.8331, sr. 1.27 |
| `is_result` | bool | 100.0% | od 1 do 1, sr. 1.00 |
| `has_data` | bool | 100.0% | od 1 do 1, sr. 1.00 |
| `url` | object | 100.0% | 4,180 unikalnych, np. https://understat.com/match/4755; https://understat.com/matc |
