---
tags: [data-sources, api, providers, audit, historical-data]
status: draft
date: 2026-09-05
---

# 26 — Audyt źródeł danych i API

> Uzupełnia [[16 - Rejestr źródeł danych]] i [[20 - Global Data Coverage Matrix v1]]. Ceny zweryfikowane 05.09.2026, wymagają ponownego sprawdzenia przed zakupem.

## 1. Tanie API rynkowe (nie-enterprise)

| Provider | Cena | Co daje | Uwaga |
|---|---|---|---|
| **API-Football** | Free 100/d; $19 / $29 / $39 | fixtures, stats, lineups, injuries, odds pre-match + in-play, predictions | najlepszy stosunek ceny do zakresu; batchowalny per `league/date` |
| **football-data.org** | Free 12 rozgrywek; €49 / €99 / €199 + Stats €15 + Odds €15 | fixtures, lineups, cards; add-on: corners, fouls, possession, shots on/off; add-on odds = tylko 1X2 | dobry drugi provider do reconciliation |
| **Sportmonks** | od €29/mies | 2200+ lig, xG w wyższych planach | kandydat premium-lite |
| **TheStatsAPI** | ~$50/mies | 150 rozgrywek, 10 lat historii, odds + xG w jednym planie | wymaga shootoutu, marketing ≠ realna kompletność |
| **The Odds API** | $30/mies / 20k credits | dedykowany feed rynkowy; current `/odds` = 1 × markets × regions, historical 10× | najlepszy do timestamped odds |
| **SportsGameOdds** | free tier; $99–$499 | 80+ booków w tym Pinnacle | **US-focused** — mały pożytek dla PL |
| **Entity Sport** | od $150/mies | FIFA 2026, fantasy points | za drogo na start |
| **OddsJam / OpticOdds** | ~$5000/mies lub quote-only | 200+ sportsbooków, player props | enterprise, poza zasięgiem |

**Wniosek:** żaden tani feed nie daje kursów na `team corners / SOT / cards` u polskich bukmacherów. To nie jest kwestia budżetu — tego nie ma poniżej poziomu enterprise.

## 2. Duże historyczne zbiory

### Wyniki + statystyki + kursy razem
- **Football-Data.co.uk** — darmowy kręgosłup. 22+ sezonów, ~22 ligi europejskie. Wyniki, strzały, **SOT, rożne, faule, kartki** ORAZ kursy z wielu bukmacherów włącznie z closing. **To jedyne darmowe źródło, które ma statystyki i kursy w jednym rekordzie** — czyli jedyne, na którym da się od razu policzyć edge i CLV.

### Wyniki (bez statystyk) — dobre do priors/Elo
- `schochastics/football-data` (GitHub) — 1 237 935 meczów, 207 lig, 1888–2023, parquet
- **openfootball / football.db** — public domain, JSON/TXT
- Kaggle *Club Football Match Data (2000–2025)* — 27 krajów, 42 ligi

### Event data / xG
- **StatsBomb Open Data** — event-level + część meczów 360; coverage selektywne
- **Understat** — xG dla top 5 lig
- **FBref** (przez `soccerdata` w Pythonie) — bogate statystyki, ale scraping wobec ToS

### Historyczne kursy
- **Football-Data.co.uk** (jw.) — najprostsza droga
- **Betfair Historical Data** — oficjalne, timestamped, pakiety płatne
- **Apify `football-odds-historical`** — pre-match + closing z Bet365, Pinnacle, Betfair, William Hill, od sezonu 2000/01
- **OddAlerts** — opening / closing / peak, do 6 miesięcy wstecz
- **oddsbase.net** — archiwum od 2005
- **Footiqo** — darmowe sample, w tym *Premier League 2025/26 Corners & Cards Odds* ← **dokładnie nasz rynek, warto pobrać na już**

**Luka:** żadne z powyższych nie daje historycznych kursów na team corners/SOT **z polskich booków**. Footiqo (PL = Premier League) to najbliższy substytut do badań.

## 3. Skąd providerzy biorą dane — trzy warstwy

1. **Oficjalne prawa.** Genius Sports ma wyłączność od Football DataCo na zbieranie, licencjonowanie i dystrybucję live data z Premier League, EFL i SPFL. Sportradar ma NBA, NHL, MLB, UEFA, NASCAR — umowy do lat 30. To kontrakty za dziesiątki milionów.
2. **Skauci na stadionie.** Zespół ludzi na meczu, każdy klika inny wycinek zdarzeń. **Mikołaj opisał to dokładnie poprawnie w transkrypcie (1:49:02)** — „to klikają osoby, które siedzą na stadionie".
3. **Sublicencje i feedy opóźnione.** Po ugodzie z 2022 Sportradar zaprzestał nieoficjalnego skautingu na PL/EFL/SPFL i kupił od Genius sublicencję na opóźniony feed sprzedawany jako *Official FDC Secondary Feed*. Każdy trzeci podmiot chcący wysłać skautów musi mieć sublicencję.

### Co to znaczy dla nas

- Tanie API (API-Football, flashscore4, TheStatsAPI) **nie mają oficjalnych praw**. Są downstream: agregacja, wtórne feedy albo scraping.
- Dowód wprost: ToS API-Football zastrzega natychmiastowe zawieszenie konta bez zwrotu w razie skargi ligi/federacji. To przyznanie, że łańcuch praw jest niepewny.
- **Latencja nas nie dotyczy.** Oficjalny feed liczy się w milisekundach; wtórny w sekundach/minutach. Przy predykcji przedmeczowej T−24h/T−6h ([[11 - Czas predykcji i leakage]]) to bez znaczenia. Nie musimy płacić za szybkość — to dobra wiadomość dla budżetu.
- **Jakość i definicje już tak.** Wtórny dostawca ma gorsze i niespójne definicje SOT/corners — dokładnie to, co [[21 - Pilot live 27-08-2026]] wykrył na xG.

## 4. Trzy konkretne rozwiązania

### 4.1 `gustavofariaa/FlashscoreScraping`

Node ≥18 + Playwright, output JSON/CSV, licencja Unlicense.

Zwraca: `matchId`, `stage`, `date`, `status`, `home`/`away` (nazwa + logo), `result` (wynik, regulation, karne), `information` (**sędzia, stadion, pojemność**), `statistics` (lista zmienna, „depends on match").

**Nie zwraca: kursów, składów, kontuzji, xG.**

- **Plus:** sędzia i stadion to czynniki wprost z [[04 - Rejestr cech]] i [[17 - Interakcje i wzorce powtarzalne]] (`Team×Referee`, `RefereeStrictness×TeamAggressiveness`). Darmowe pokrycie lig spoza Football-Data.co.uk.
- **Minus:** `statistics` bez gwarancji kompletności — to dyskwalifikuje użycie produkcyjne bez własnej walidacji.
- **Minus:** Playwright = headless browser per mecz. Sezon (380 meczów) to godziny, nie minuty.
- **Uwaga licencyjna:** Unlicense dotyczy **kodu**, nie danych. Dane pozostają Flashscore'a i podlegają ich ToS.
- **Werdykt:** dobry do jednorazowego backfillu badawczego. Nie jako warstwa produkcyjna.
- **Test do wykonania (30 min):** uruchomić na jednej lidze i policzyć, w ilu % meczów `statistics` faktycznie zawiera SOT i corners.

### 4.2 API-Football — konto demo, 100 req/dzień

Najlepsze, co mamy teraz. 100 req/dzień wystarczy na znacznie więcej, niż się wydaje, **pod warunkiem batchowania** (`fixtures?league=X&season=Y` to jeden request na całą kolejkę, nie na mecz).

**Do zrobienia na demo, za zero złotych:**
1. pokrycie statystyk dla 3 lig (Ekstraklasa, Premier League, Ligue 1) — ile % meczów ma corners i SOT;
2. `/odds` — jakie `bookmakers` i jakie `bets` (markets) faktycznie wracają; czy jest cokolwiek poza 1X2 / O-U / BTTS;
3. czy na liście `bookmakers` są STS, Superbet, Fortuna, Betclic.

Punkty 2–3 to **Faza −1 wykonana zdalnie**. Jeśli nie ma rynków corners/SOT ani polskich booków, potwierdza to konieczność własnego scrapingu booków albo zmiany rynku docelowego.

**Do sprawdzenia:** retention planu Free — często tylko bieżący sezon, co wyklucza backtest.

### 4.3 RapidAPI `flashscore4` — 500 req/mies free

Stan na 05.09.2026: v2, grupy endpointów **Search / Match / Player / Tournament / General / Team**. Opis: „live scores, fixtures, results, and stats". **Odds nie są reklamowane jako grupa endpointów.**

Ceny: BASIC $0 · PRO $3.50 · ULTRA $9.50 · MEGA $19.50/mies. 2666 subskrybentów, 9.9 popularity, 100% service level, 268 ms latency. Jest `Get Match Momentum` (live, nie przedmeczowe).

- 500 req/mies to ~16/dzień — za mało na cokolwiek poza ręcznym sprawdzeniem.
- To **to samo źródło co 4.1** (Flashscore), tylko opakowane i płatne. Zaleta: ktoś inny utrzymuje scraper i daje JSON. Wada: brak kursów, mały limit, zależność od anonimowego operatora, ten sam problem ToS o poziom wyżej.
- **Werdykt:** ULTRA za $9.50/mies może być tańsze niż utrzymywanie własnego scrapera — **ale tylko jeśli w playground potwierdzicie, że zwraca SOT i corners.** Do sprawdzenia tym samym testem co 4.1.

## 5. Wspólny wniosek

Wszystkie trzy rozwiązania i wszystkie tanie API mają tę samą dziurę: **brak kursów na team corners / SOT / cards u polskich bukmacherów.**

Zostają trzy drogi:
- **a)** własny scraping polskich booków — kruchy, wobec ToS, wymaga zalogowanego konta;
- **b)** zmiana rynku docelowego na obecny w feedach (1X2, O/U, BTTS, AH) — ale tam rynek jest znacznie efektywniejszy;
- **c)** granie gdzie indziej (exchange, offshore) — z konsekwencjami z [[23 - Krytyczna ocena wykonalności]].

To ta sama ściana co w [[23 - Krytyczna ocena wykonalności]] §9, tylko potwierdzona od strony podaży danych, a nie popytu.
