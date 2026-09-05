---
tags: [inventory, data, coverage, gaps]
status: active
date: 2026-09-05
---

# 30 — Inwentarz danych

> Stan na 05.09.2026. Wszystkie liczby zmierzone na plikach w `data/raw/`, nie z pamięci.

## A. Co mamy — według rodzaju danych

### A1. Wyniki meczów (kto, z kim, kiedy, ile)

| źródło | meczów | okres | zakres |
|---|---|---|---|
| schochastics | **1 309 501** | 1888–2025 | 224 rozgrywki, cały świat |
| club-football-2000-2025 | 230 557 | 2000–2025 | 38 lig |
| transfermarkt | 88 958 | 2006–2026 | 70 rozgrywek, 31 krajów |

To najszersza warstwa. Wyników nam nie brakuje.

### A2. Statystyki meczowe (rożne, strzały, SOT, faule, kartki)

| źródło | meczów | okres |
|---|---|---|
| club-football-2000-2025 | **114 363** | 2000–2025 |
| footiqo Big Five | 1 750 | 2025/26 (z podziałem na połowy) |

22 ligi europejskie: E0–E3, EC, SC0–SC3, D1, D2, I1, I2, SP1, SP2, F1, F2, N1, B1, P1, T1, G1.

**Bez statystyk (16 krajów):** Polska, Argentyna, Austria, Brazylia, Chiny, Dania, Finlandia, Irlandia, Japonia, Meksyk, Norwegia, Rumunia, Rosja, Szwecja, Szwajcaria, USA.

### A3. xG

| źródło | meczów | okres | ligi |
|---|---|---|---|
| Understat | 10 734 | 2020–2025 | top 5 |
| footiqo Big Five | 1 750 | 2025/26 | top 5, **z podziałem na połowy** |

### A4. Kursy

| rynek | meczów | źródło |
|---|---|---|
| 1X2 | **227 527** | club-football |
| Asian Handicap | 156 733 | club-football |
| O/U 2.5 | 148 398 | club-football |
| 1X2 Pinnacle **closing** | ~86 000 | football-data.co.uk |
| **rożne (meczowe) closing** | **380** | footiqo, tylko PL 2025/26 |
| **kartki closing** | **380** | footiqo, tylko PL 2025/26 |
| **rożne drużynowe** | **0** | — |

### A5. Zawodnicy, składy, zdarzenia

| plik | wierszy | okres |
|---|---|---|
| `game_lineups` | **3 179 016** (1 780 759 XI + 1 398 257 ławka) | 2013–2026 |
| `appearances` | 1 894 350 | 2012–2026 |
| `game_events` (gole, kartki, zmiany z minutą) | 1 274 469 | 2006–2026 |
| `player_valuations` | 656 301 | 2000–2026 |
| `transfers` | 175 165 | 1993–2030 |

### A6. Kontekst meczu

| cecha | pokrycie | źródło |
|---|---|---|
| **sędzia** | **99,2%** (88 958 meczów) | transfermarkt |
| sędzia | 40,9% | football-data.co.uk |
| stadion | 99,7% | transfermarkt |
| formacje | 90,8% | transfermarkt |
| frekwencja | 87,9% | transfermarkt |
| trener (nazwisko) | — | transfermarkt |
| pozycja w tabeli przed meczem | — | transfermarkt |
| **Elo** | 245 033 snapshotów, 895 klubów, 19 krajów, 2000–2025 | ClubElo przez club-football |
| Elo doklejone do meczów | 141 597 | club-football |
| stadiony ze współrzędnymi | 242 | OpenStreetMap |
| pogoda | on-demand, bez limitu | Open-Meteo |

### A7. Dane zdarzeniowe

StatsBomb Open Data: 80 par rozgrywki × sezon (Champions League 18 sezonów, La Liga 18, MŚ 8). Pobrany na razie tylko katalog.

## B. Segmenty — jak to się dzieli

### Według ligi

| segment | wyniki | statystyki | xG | kursy 1X2 | rożne/kartki kursy | składy/sędzia |
|---|---|---|---|---|---|---|
| **Top 5 Europy** | ✔ | ✔ | ✔ | ✔ | tylko PL 25/26 | ✔ |
| **Reszta Europy (17 lig)** | ✔ | ✔ | ✘ | ✔ | ✘ | ✔ |
| **Ekstraklasa** | ✔ | **✘** | ✘ | ✔ | ✘ | ✔ od 2024 |
| **Reszta świata** | ✔ | ✘ | ✘ | ✔ | ✘ | częściowo |

### Ekstraklasa osobno

612 meczów (2024–2026) w transfermarkcie, ale za to jakość idealna: **sędzia 100%, stadion 100%, formacje 99,8%, frekwencja 100%**, 25 339 wpisów składów, 9 698 zdarzeń (5 485 zmian, 2 533 kartki, 1 680 goli).

**Ale rożnych i SOT dla Ekstraklasy nie ma nigdzie.** To jest twarda dziura.

### Według czasu

| okres | co mamy |
|---|---|
| 1888–2000 | tylko wyniki |
| 2000–2006 | + statystyki meczowe, + kursy 1X2, + Elo |
| 2006–2012 | + zdarzenia (gole/kartki/zmiany z minutą), + sędzia, + stadion |
| 2012–2013 | + występy zawodników |
| 2013–2020 | + składy wyjściowe i ławka |
| 2020–2025 | + xG |
| 2025/26 | + closing odds na rożne i kartki (tylko Premier League), + xG z podziałem na połowy |
| dziś | API-Football na żywo: 6 bukmacherów, 60 rynków z rodziny corners/SOT/cards |

## C. Czego brakuje

### C1. Krytyczne

1. **Historyczne kursy na rożne drużynowe — zero.** To blokuje backtest głównej tezy projektu. Mamy wyniki drużynowe i mamy bieżące ceny, ale nie mamy pary „cena + wynik" w historii.
2. **Ekstraklasa: rożne i SOT — zero.** W żadnym z ośmiu źródeł. Rynek domowy jest najsłabiej pokryty.

### C2. Poważne

3. **Kontuzje i zawieszenia historyczne z `known_at`.** Wiemy kto zagrał (składy), nie wiemy kto był niedostępny i od kiedy było to wiadomo. Bez tego cechy typu `MissingPlayer × OpponentThreat` z noty 17 są nie do policzenia bez leakage.
4. **Ruch linii między otwarciem a zamknięciem.** Mamy opening i closing, nie mamy nic pomiędzy. Steam detection niemożliwy.
5. **Predicted lineups przed meczem.** Mamy tylko potwierdzone po fakcie — użycie ich wprost w modelu T−24h to podręcznikowy leakage z noty 11.

### C3. Mniejsze

6. xG poza top 5 lig.
7. Historyczne player props.
8. Sędzia w danych sprzed 2006 (tylko 40,9% z football-data).

## D. Co jeszcze możemy zdobyć

### D1. Bez pieniędzy, mogę sam

| co | ile to daje | koszt |
|---|---|---|
| **Snapshotter kursów API-Football od dziś** | buduje brakujący zbiór z C1 — po sezonie mamy historyczne ceny na rożne drużynowe | 1 skrypt, mieści się w 100 req/dzień |
| **API-Football sezony 2022–2024** | statystyki + kursy dla wielu lig; darmowy plan je obejmuje | ~500–1000 requestów, czyli 5–10 dni po 100/dzień |
| **Pogoda dla wszystkich meczów** | domyka `EnvironmentalShock`; mamy już stadiony | Open-Meteo, bez limitu |
| **StatsBomb pełne dane zdarzeniowe** | xT/VAEP/pressing do badań | kilka GB z GitHuba |
| **Understat 2014–2019** | rozszerza xG z 10,7k do ~20k meczów | scraping, kilkanaście minut |
| **football-data.org** | ID kanoniczne do warstwy L1 | klucz już mamy |

### D2. Za pieniądze

| co | cena | co odblokowuje |
|---|---|---|
| API-Football **Pro** | $19/mies | zdejmuje limit sezonów 2022–2024, 7500 req/dzień |
| The Odds API | $30/mies | historyczne kursy z timestampami — rozwiązuje C2 §4 |
| Betfair Historical Data | pakiety | exchange, timestamped, do CLV |
| football-data.org Stats+Odds | +€30/mies | rożne/faule/posiadanie dla 12 rozgrywek |

### D3. Tylko własną pracą

**Scraper kursów STS/Fortuna/Betclic.** To jedyna droga do polskich kursów na rożne. Wiemy z [[27 - Faza -1 wyniki empiryczne]], że kursy są widoczne bez logowania, więc technicznie wykonalne. Rozwiązuje C1 §1 dla rynku, na którym realnie gramy.

## E. Wniosek w jednym zdaniu

Mamy bardzo dużo danych **o tym, co się w meczach działo** (1,3 mln wyników, 114 tys. ze statystykami, 3,2 mln wpisów składów, sędzia w 99%), a bardzo mało danych **o tym, ile rynek za to żądał** w interesujących nas rynkach (380 meczów z kursami na rożne, zero na rożne drużynowe).

Model da się zbudować już dziś. Zbacktestować zakład — jeszcze nie. Dlatego snapshotter z D1 jest pilniejszy niż jakakolwiek analiza.
