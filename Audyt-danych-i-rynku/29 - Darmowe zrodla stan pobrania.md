---
tags: [data-sources, free, inventory, footiqo]
status: draft
date: 2026-09-05
---

# 29 — Darmowe źródła: stan pobrania

> Inwentarz tego, co realnie mamy na dysku, i co jeszcze da się wziąć bez płacenia.
> Wszystko sprawdzone 05.09.2026.

## A. POBRANE I ZWERYFIKOWANE

### A1. Football-Data.co.uk — 92 327 meczów

12 sezonów × 22 ligi. Statystyki 82,6%, kartki 88,3%, **sędzia tylko 40,9%**.
Kursy: Bet365 open 99,8%, **Pinnacle closing 93,9%**, O/U 2.5 i AH closing 57,7%.
Brak kursów na rożne/SOT/kartki. Szczegóły: [[27 - Faza -1 wyniki empiryczne]].

### A2. Footiqo — Corners & Cards Closing Odds (PL 2025/26)

380 meczów, 3 arkusze. **Pierwszy zbiór z historycznymi kursami na rożne i kartki.**

| arkusz | zawartość |
|---|---|
| `Corners_Closing_Odds` | rożne home/away/total FT + **closing O/U na 7.5, 8.5, 9.5, 10.5** + sędzia |
| `Cards_Closing_Odds` | żółte home/away/total FT + **closing O/U na 2.5, 3.5, 4.5, 5.5** + sędzia |

Pokrycie **100%** na wszystkich liniach. Sędzia **100%** (vs 40,9% w Football-Data).

Zmierzone marże na kursach zamknięcia:

```text
rożne  7.5 / 8.5 / 9.5 / 10.5   ->  5,01 / 4,98 / 5,00 / 5,02 %
kartki 2.5 / 3.5 / 4.5 / 5.5    ->  5,18 / 5,23 / 5,23 / 5,16 %
```

**Ograniczenie: to są rożne MECZOWE, nie drużynowe.** Wyniki drużynowe (`home_corners_ft`, `away_corners_ft`) są, ale ceny tylko na sumę.

### A3. Footiqo — Big Five 2025/26 Database

1750 meczów: LaLiga 380, Serie A 380, Premier League 380, Bundesliga 306, Ligue 1 304.

| arkusz | zawartość |
|---|---|
| `Matches_Results` | gole 1H/2H/FT, sędzia + kraj sędziego, **neutral_venue** |
| `Statistics_FT` / `_1H` / `_2H` | rożne, żółte, czerwone, **xG**, posiadanie, strzały, **SOT**, strzały niecelne, faule, obrony bramkarza — **w rozbiciu na połowy** |
| `Closing_Odds` | 1X2, O/U 0.5–4.5, AH −1.5/−1/0/+1/+1.5, **BTTS** |

**Pokrycie 100% na każdym polu.** Marża 1X2 closing: **3,02%**.

To jest jakościowo lepsze niż Football-Data: xG, podział na połowy, obrony bramkarza, neutral venue, pełny sędzia.

### A4. schochastics/football-data

`games.parquet` — 1 309 501 meczów, 1888–2026, 207 lig. Tylko wyniki. Dobre do priorów i długich szeregów.
`goals_time/pol-ekstraklasa.csv` — minuty goli Ekstraklasy od 1994.

### A5. Open-Meteo — archiwum pogody

Działa, bez klucza, bez limitu w praktyce. Przykład zwrócił temperaturę, opad i wiatr dla współrzędnych i zakresu dat. To pokrywa `EnvironmentalShock` z [[04 - Rejestr cech]] w całości i za darmo.

```
https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..
   &start_date=..&end_date=..&daily=temperature_2m_max,precipitation_sum,wind_speed_10m_max
```

### A6. StatsBomb Open Data

`competitions.json` pobrany: **80 par rozgrywki×sezon**. Champions League 18 sezonów, La Liga 18, World Cup 8, Ligue 1 3, Bundesliga 2, Premier League 2, Serie A 2, Euro 2.
Dane na poziomie zdarzeń — do badań xG/xT/pressingu, nie do produkcji.

## B. DOSTĘPNE, NIEPOBRANE

| źródło | co daje | jak wziąć |
|---|---|---|
| **dcaribou/transfermarkt-datasets** | wyceny zawodników, występy, składy, transfery — CSV/parquet | GitHub, wolna licencja |
| **Understat** | xG dla top 5 lig, per mecz i per strzał | scraping / `soccerdata` (Python) |
| **FBref** | najbogatsze statystyki publiczne | `soccerdata`, ale wobec ToS |
| **football-data.org** | 12 rozgrywek na Free | wymaga darmowego klucza |
| **openfootball / football.db** | wyniki, public domain | GitHub |
| **Kaggle — Club Football Match Data 2000–2025** | 27 krajów, 42 ligi | wymaga konta |
| **OpenStreetMap** | współrzędne i wysokość stadionów — potrzebne do Open-Meteo | Overpass API |
| **Footiqo — kolejne darmowe zbiory** | mają więcej niż te dwa | trzeba sprawdzić na stronie |

## C. NIEDOSTĘPNE / PROBLEMY

- **ClubElo** (`api.clubelo.com`) — darmowe API z ratingami Elo, ale **dziś zwraca HTTP 502**. Serwer padnięty, nie zmiana polityki. Warto ponowić — to jest gotowy prior siły drużyn dla v0.4 z [[08 - Wersje modelu]].
- **FlashscoreScraping** — nie działa, patrz [[27 - Faza -1 wyniki empiryczne]] §4.
- **api-football.com/documentation-v3** — za ochroną antybotową, dokumentacji nie da się odczytać automatycznie.

## D. Czego nadal NIE mamy za darmo

**Historycznych kursów na rożne DRUŻYNOWE.** Mamy:

- wyniki drużynowe (Football-Data, Footiqo, API-Football) ✔
- historyczne ceny na rożne **meczowe** (Footiqo, 1 sezon PL) ✔
- bieżące ceny na rożne **drużynowe** (API-Football, [[28 - API-Football wyniki sondy]]) ✔
- **historyczne ceny na rożne drużynowe** ✘

To znaczy, że model team corners można zbudować i skalibrować, ale zbacktestować z ceną — jeszcze nie. **Rozwiązanie: zacząć snapshotować `/odds?fixture=` z API-Football od dziś** (Faza 0 z [[25 - Kolejność budowy]]). Po jednym sezonie mamy własny zbiór, którego nikt nie sprzedaje tanio.

## E. Wnioski o marżach — pełna drabinka

Zebrane pomiary z różnych miejsc:

| rynek / moment | marża |
|---|---|
| 1X2 closing (Footiqo Big Five) | **3,0%** |
| Rożne meczowe closing (Footiqo PL) | **5,0%** |
| Kartki closing (Footiqo PL) | **5,2%** |
| Rożne drużynowe pre-match, Pinnacle | **6,1%** |
| Rożne drużynowe pre-match, Bet365 | 7,2–8,5% |
| Rożne drużynowe pre-match, 1xBet | 7,5–10,1% |
| Rożne drużynowe przedziały, **STS** | **13,7%** |

Wzorzec jest jednoznaczny: **marża rośnie wraz z egzotycznością rynku i spada w miarę zbliżania się do kickoffu.** Rynek dochodzi do efektywnej ceny na zamknięciu.

Konsekwencja dla strategii: przewaga nie polega na tym, że rynek „nie policzył" rożnych — policzył. Polega na tym, że **STS ma 13,7% marży tam, gdzie Pinnacle ma 6,1%**, a to znaczy, że kształt oferty STS musi się gdzieś rozjeżdżać z rozkładem Pinnacle. Tam trzeba szukać. Patrz [[28 - API-Football wyniki sondy]] §5.
