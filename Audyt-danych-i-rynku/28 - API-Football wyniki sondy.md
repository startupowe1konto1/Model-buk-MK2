---
tags: [api-football, phase-minus-1, odds, empirical, markets]
status: draft
date: 2026-09-05
---

# 28 — API-Football: wyniki sondy

> Zmierzone kluczem demo 05.09.2026. Zużyto ~14 ze 100 requestów dziennych.

## 1. Plan i limity

```text
plan          : Free
wazny do      : 2027-09-05
limit         : 100 req/dzien
```

Ograniczenia planu Free odkryte empirycznie:

| co | status |
|---|---|
| `?next=N` | **zablokowane** ("Free plans do not have access to the Next parameter") |
| `?season=2025/2026` | **zablokowane** — dozwolone tylko **2022–2024** |
| `/fixtures?date=YYYY-MM-DD` | **działa** — zwróciło **1545 meczów wszystkich lig w JEDNYM requeście** |
| `/odds?fixture=ID` dla dzisiejszego meczu | **działa**, pełne kursy |

To jest ważne: **backtest na sezonach 2022–2024 jest dostępny za darmo**, a bieżące kursy da się ciągnąć per fixture. Wzorzec `1 request na dzień (fixtures) + 1 request na mecz (odds)` mieści się w 100/dzień dla ~90 meczów.

## 2. Bukmacherzy — 33 dostępnych

```
10Bet, 188Bet, 1xBet, 5Dimes, 888Sport, Bet365, BetFred, BetVictor, Betano,
Betcris, Betfair, Betsson, Betway, Bovada, Bwin, ComeOn, Dafabet, Expekt,
Fonbet, Intertops, Interwetten, Ladbrokes, Marathonbet, Netbet, NordicBet,
Pinnacle, SBO, Sportingbet, Superbet, Tipico, Unibet, William Hill
```

- **Superbet (id 34) JEST** — jedyny bukmacher z polską licencją na liście.
- **Brak STS, Fortuny, Betclic, LVBET, Forbet, Etoto, Totalbet.**
- Jest **Pinnacle** i **Betfair** — czyli ostry benchmark do de-vigu.

## 3. Katalog rynków — 338 typów, w tym dokładnie nasze

| id | nazwa | to jest |
|---|---|---|
| 57 | **Home Corners Over/Under** | rożne gospodarza O/U — rynek z MB-0002/3/4 |
| 58 | **Away Corners Over/Under** | rożne gościa O/U |
| 243 / 244 | **Home / Away Shots On Target** | SOT drużynowe |
| 88 / 89 | Home / Away Total ShotOnGoal | |
| 82 / 83 | **Home / Away Team Total Cards** | kartki drużynowe |
| 150 / 151 | Home / Away Team Yellow Cards | |
| 176 | ShotOnTarget 1x2 | |
| 242 | Player Shots On Target | player props |
| 170–175 | Fouls (Total / Home / Away / 1x2 / Handicap) | |
| 164–169 | Offsides | |

Łącznie: 35 rynków rożnych, 21 strzałowych, 21 kartkowych, 11 faulowych, 7 spalonych.

## 4. Test na realnym meczu — Newcastle – Bournemouth, PL, 05.09.2026

Jeden request `/odds?fixture=1557395` zwrócił:

- **6 bukmacherów**: 1xBet, Bet365, BetVictor, Marathonbet, Pinnacle, William Hill
- **176 unikalnych rynków**
- **60 rynków z rodziny corners / SOT / cards / fouls**

Rozkład: 1xBet 83 rynki (43 nasze), Bet365 105 (31), Marathonbet 56 (7), Pinnacle 19 (7), William Hill 48 (4), BetVictor 14 (0).

## 5. Zmierzone marże na team corners — to jest sedno

| bukmacher | rynek | kursy | overround | marża |
|---|---|---|---|---|
| **Pinnacle** | Home Corners O/U 5.5 | 1.70 / 2.10 | 106.4% | **6.1%** |
| **Pinnacle** | Away Corners O/U 4.5 | 1.81 / 1.95 | 106.5% | **6.1%** |
| Bet365 | Away Corners O/U 4.5 | 1.73 / 2.00 | 107.8% | 7.2% |
| Bet365 | Home Corners O/U 5.5 | 1.83 / 1.83 | 109.3% | 8.5% |
| Marathonbet | Home Corners O/U 6.0 | 1.96 / 1.75 | 108.2% | 7.5% |
| 1xBet | Home Corners O/U 6.0 | 1.89 / 1.81 | 108.2% | 7.5% |
| 1xBet | Home Corners O/U 4.5 | 1.31 / 2.87 | 111.2% | 10.1% |
| **STS** (przedziały, [[27 - Faza -1 wyniki empiryczne]]) | Korona rożne | 5.40/2.95/3.10/3.20 | 115.9% | **13.7%** |

### To obala tezę o „leniwej wycenie" — dla top lig

**Pinnacle wycenia team corners przy marży 6,1%.** Pinnacle to najostrzejszy bukmacher na świecie, który utrzymuje wąskie marże właśnie dlatego, że modeluje rynek poważnie i przyjmuje ostre pieniądze. Jeśli oni schodzą do 6,1% na rożnych drużynowych w Premier League, ten rynek **nie jest miękki**.

Teza z [[26 - Audyt źródeł danych i API]] i z transkryptu — „rożne są wyceniane regułą kciuka i nikt ich nie poprawia" — dla lig top jest **nieprawdziwa**.

### Ale otwiera to inną drogę

```text
Pinnacle team corners  6,1% marzy   <- benchmark fair
STS    team corners   13,7% marzy   <- gdzie realnie gramy (PL, licencja)
```

Różnica 7,6 pp to **marża, nie edge** — nie da się zagrać ceny Pinnacle w STS. Ale Pinnacle staje się **darmowym, ostrym estymatorem `p_fair` dla team corners**, czego wcześniej nie mieliśmy w ogóle.

Realna strategia v1 przestaje być „nasz model vs bukmacher" i staje się:

> zdjąć vig z Pinnacle → uzyskać rozkład rożnych → sprawdzić, czy przedziały STS
> są niespójne z tym rozkładem → grać tylko te niespójności

To jest dokładnie warstwa **M0 / Faza 1** z [[25 - Kolejność budowy]], tylko teraz z potwierdzonym źródłem danych.

## 6. Ile edge'u nadal potrzeba

Pinnacle, Away Corners Over 4.5 @ 1.81:

```text
p_market_devig  = (1/1.81) / 1.065        = 51,9%
p_breakeven PL  = 1 / (1.81 * 0.88)       = 62,8%
POTRZEBNY EDGE                            = +10,9 pp  wzgledem Pinnacle
```

Podatek 12% nie znika. Ale przy kursach 1.8–2.1 wymagany edge to ~11 pp, a nie ~14 pp jak przy zdarzeniach wysokiej częstości z [[27 - Faza -1 wyniki empiryczne]] §3. Potwierdza to, że **środek rozkładu jest właściwym celem**.

## 7. Co się zmienia w planie

1. **Blokada „brak kursów na team corners" jest zdjęta.** Są, tanio, przez API-Football.
2. **Ale nie u polskich bukmacherów** poza Superbetem. STS/Fortuna/Betclic wymagają własnego scrapera (kursy widoczne bez logowania — [[27 - Faza -1 wyniki empiryczne]] §1).
3. **Pinnacle jako benchmark dla team corners** — to jest nowy, mocny element, którego nie było w [[16 - Rejestr źródeł danych]].
4. **Sezony 2022–2024 dostępne na Free** — backtest da się zacząć bez płacenia.
5. Plan Pro ($19) zdejmuje limit sezonów i podnosi do 7500 req/dzień. **Rekomendacja: kupić dopiero po zbudowaniu Fazy 1 na danych 2022–2024.**

## 8. Pliki

Surowe odpowiedzi w `data/apifootball/`:
`status.json`, `bookmakers.json`, `bets.json`, `odds_live_1557395.json`.
Skrypty: `scripts/02_apifootball_probe.py`, `scripts/05_odds_reality_check.py`.
