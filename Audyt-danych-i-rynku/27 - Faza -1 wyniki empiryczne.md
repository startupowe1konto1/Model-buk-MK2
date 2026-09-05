---
tags: [phase-minus-1, bookmaker-audit, empirical, sts, football-data]
status: draft
date: 2026-09-05
---

# 27 — Faza −1: wyniki empiryczne

> Nie research, tylko pomiary. Wszystko sprawdzone 05.09.2026.

## 1. Audyt STS — mecz Korona Kielce – Wisła Kraków (Ekstraklasa, 17:30)

**Korekta założenia z transkryptu (1:58:01): kursy SĄ widoczne bez logowania i bez PESEL-u.** Rejestracja jest potrzebna do postawienia zakładu, nie do odczytania oferty. To otwiera drogę do scrapera kursów.

Kategorie rynków na meczu: **Gole · Handicap · Rzuty rożne · Zawodnicy · Połowy**. Łącznie ~41 rynków.

| Rynek | Jest? |
|---|---|
| Rzuty rożne — suma meczowa (O/U) | **TAK** |
| Rzuty rożne — która drużyna więcej | **TAK** |
| Rzuty rożne — **drużynowe, przedziały** | **TAK** |
| Rzuty rożne — 1. połowa (warianty) | TAK |
| **Kartki** | **NIE** |
| **Strzały / strzały celne (SOT)** | **NIE** |

### Kluczowa różnica formatu

STS nie oferuje `team corners over/under 3.5`. Oferuje **przedziały**: `0-2 / 3-4 / 5-6 / 7+`.

To znaczy, że **MB-0002/3/4 w `experiments/test-bets.csv` opisują rynek, którego u polskiego bukmachera nie ma w tej formie.** Rejestr trzeba przepisać na przedziały albo uznać za nierealizowalny.

### Zmierzona marża

Korona Kielce — liczba rzutów rożnych, przedziały:

| przedział | kurs | implied |
|---|---|---|
| 0-2 | 5.40 | 18.52% |
| 3-4 | 2.95 | 33.90% |
| 5-6 | 3.10 | 32.26% |
| 7+  | 3.20 | 31.25% |
| **suma** | | **115.93%** |

```text
overround        = 15.93%
marza            = 13.74%
```

Dla porównania 1X2 na tym samym meczu: 2.30 / 3.60 / 3.10 → suma 115.5%... czyli podobnie. Ale to Ekstraklasa; na Premier League 1X2 marża jest znacznie niższa, a rożne pozostają szerokie.

### Ile edge'u potrzeba (przedział 3-4 @ 2.95)

```text
p_market_devig = 0.3390 / 1.1593        = 29.24%
p_breakeven    = 1 / (2.95 * 0.88)      = 38.52%
POTRZEBNY EDGE                          = +9.28 pp
```

**Model musi twierdzić 38.5%, gdy rynek po zdjęciu marży mówi 29.2% — i to tylko po to, żeby wyjść na zero.** Do zysku trzeba więcej.

## 2. Football-Data.co.uk — pobrane i zmierzone

Skrypt `01_download_football_data.py`: 12 sezonów × 22 ligi = **264 pliki, 92 327 meczów**, plus 16 krajów EXTRA (w tym Ekstraklasa, 4131 meczów — ale bez statystyk).

Pokrycie (`04_coverage_and_baserates.py`):

| pole | pokrycie |
|---|---|
| wynik | 100.0% |
| strzały / SOT / rożne | 82.6% |
| kartki | 88.3% |
| **sędzia** | **40.9%** |
| 1X2 Bet365 open | 99.8% |
| **1X2 Pinnacle CLOSE** | **93.9%** |
| O/U 2.5 close | 57.7% |
| Asian Handicap close | 57.7% |
| **kursy na rożne / SOT / kartki** | **0.0%** |

Dwa wnioski:

- **Pinnacle closing odds na 93.9% meczów, za darmo.** To jest ostry benchmark do CLV. Fazy 1–2 z [[25 - Kolejność budowy]] da się zrobić offline, dziś, bez wydawania złotówki.
- **Sędzia tylko 40.9%.** Interakcje `Team×Referee` i `RefereeStrictness×TeamAggressiveness` z [[17 - Interakcje i wzorce powtarzalne]] mają połowę potrzebnych danych. Trzeba je albo dociągnąć skądinąd, albo zdegradować w priorytecie.

## 3. Base rates — teza o „powtarzalnych zdarzeniach" zmierzona

Poziom ligi, perspektywa jednej drużyny:

| liga | C>=4 | C>=5 | SOT>=3 | SOT>=4 |
|---|---|---|---|---|
| E0 Premier League | 68.5% | 54.5% | 75.9% | 59.0% |
| I1 Serie A | 66.3% | 51.3% | 77.5% | 61.1% |
| D1 Bundesliga | 64.8% | 49.4% | 80.0% | 64.6% |
| SP1 LaLiga | 64.5% | 49.0% | 73.8% | 56.3% |

Najbardziej powtarzalne drużyny (min. 40 meczów):

| drużyna | n | P(C>=4) | fair | break-even po 12% |
|---|---|---|---|---|
| Celtic (SC0) | 448 | 89.1% | 1.12 | **1.28** |
| Rangers (SC0) | 371 | 87.3% | 1.15 | **1.30** |
| Bayern (D1) | 408 | 86.0% | 1.16 | **1.32** |
| Man City (E0) | 456 | 85.7% | 1.17 | **1.33** |
| Liverpool (E0) | 456 | 83.1% | 1.20 | **1.37** |

### To obala wersję tezy z transkryptu

Mikołaj (1:06:00): „Barcelona, cztery strzały i cztery rożne, 90% spotkań."

Liczba jest **prawie trafna** — najlepsza drużyna w bazie ma 89.1% na 448 meczach. Ale wniosek jest odwrotny do zamierzonego:

```text
P = 89.1%  ->  fair 1.12  ->  break-even w PL 1.28
```

Bukmacher musiałby dać kurs **14% powyżej fair value**, żebyśmy wyszli na zero. Realnie wycenia takie zdarzenie na 1.05–1.10, czyli **poniżej** fair.

**Im bardziej powtarzalne zdarzenie, tym krótszy kurs, tym bardziej podatek 12% je zabija.** Zdarzenia o wysokiej częstości są najgorszym, nie najlepszym celem.

Value — jeśli istnieje — leży w środku rozkładu: `p ≈ 0.45–0.60`, kursy 1.8–2.4, gdzie 12% podatku to „tylko" ~6–7 pp wymaganego edge'u, a nie 14%.

**To powinno przekierować [[17 - Interakcje i wzorce powtarzalne]] z „pattern miner szukający wysokich base rates" na „pattern miner szukający rozbieżności w środku rozkładu".**

## 4. FlashscoreScraping — przetestowany, NIE DZIAŁA

Sklonowany, `npm install` + `playwright install chromium` OK. Uruchomiony dwa razy:

```text
country=poland  league=ekstraklasa-2024-2025      -> Found 0 matches
country=england league=premier-league-2023-2024   -> Found 0 matches   (przyklad z README!)
```

Oba URL-e Flashscore zwracają HTTP 200, więc slugi są poprawne — **zepsute są selektory DOM**. Flashscore zmienił markup. Ostatni release 1.4.0 to listopad 2025; otwarte issues „Problema con cargar datos" (11.2025) i „League season" (12.2025) opisują to samo.

Kod czyta wyłącznie `/summary/stats/0/` — **zakładka kursów nie jest w ogóle dotykana**, więc potwierdzone: żadnych kursów, nigdy.

Licencja: plik `LICENSE` to Unlicense (public domain). Pole `"license": "UNLICENSED"` w `package.json` to niedbałość autora, nie zastrzeżenie praw. Dane pozostają Flashscore'a.

**Werdykt: nie używać.** Naprawa selektorów to praca, którą trzeba by powtarzać po każdej zmianie Flashscore, dla danych, które w 82.6% mamy za darmo z Football-Data.

## 5. Co z tego wynika dla kolejnego kroku

1. **Rożne drużynowe u STS istnieją, ale jako przedziały i z marżą ~13.7%.** Rejestr testowych betów trzeba przepisać na ten format.
2. **Kartek i strzałów u STS nie ma.** Dwie z czterech rodzin rynków z [[10 - Następne kroki]] odpadają na starcie — przynajmniej u tego bukmachera.
3. **Kursy da się czytać bez logowania** — scraper kursów STS jest technicznie wykonalny (ToS to osobna sprawa).
4. **Pinnacle closing za darmo na 93.9% meczów** — Faza 1 i 2 do zrobienia od zaraz, offline.
5. **Teza o powtarzalnych zdarzeniach wymaga odwrócenia** — patrz §3.
