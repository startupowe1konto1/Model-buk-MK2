# Handoff — stan projektu na 05.09.2026

> Dokument startowy dla nowej sesji. Zawiera wszystko, co trzeba wiedzieć, żeby
> kontynuować bez czytania całej historii rozmowy.

---

## A. Kontekst projektu

### Czym jest Model Buk

Globalny silnik **wyszukiwania value w zakładach piłkarskich**. Nie typowanie meczów —
system porównuje własne prawdopodobieństwo z ceną bukmachera i podejmuje decyzję
`BET / NO BET / NO PREDICTION`.

Zespół: **Wojciech Rudnicki**, **Mikołaj** (autor vaultu metodologicznego),
**Oskar Pelzer**, **Mateusz Wojciechowski**.

### Stan wyjściowy (przed 05.09.2026)

Repo `mkonieczny1212/Model-buk` zawierało vault Obsidian: **25 notatek metodologicznych**
(`Football-Prediction-Brain/`), `experiments/test-bets.csv` z czterema zamrożonymi
testowymi betami, `AGENTS.md`. **Zero kodu, zero danych.**

Vault jest metodologicznie mocny i pozostaje źródłem prawdy. Kluczowe zasady, których
nie wolno łamać:

- **point-in-time** — model może użyć wyłącznie tego, co było wiadome w cutoffie ([[11]])
- **anti-hindsight** — hipoteza bez zamrożonego kursu i `P_model` nie jest trafionym betem ([[22]] §7)
- **`NO BET` i `NO PREDICTION` to pełnoprawne wyniki** ([[14]])
- **cost-aware** — koszt danych jest częścią wyniku ekonomicznego ([[18]])
- model produkcyjny jest **suchy**: żadnych ręcznych korekt prawdopodobieństwa ([[22]] §1)

### Rynki docelowe

Pierwsze: 1X2, O/U 2.5, BTTS. Równolegle badane rynki zdarzeń: **rożne drużynowe**,
SOT, kartki, team totals. Exact score odrzucony.

---

## B. Co zostało zrobione 05.09.2026

### B1. Krytyczna ocena — i pięć ustaleń, które zmieniają plan

**1. Podatek 12% od obrotu wywraca ekonomikę.**

```text
EV_net       = p × o × 0.88 − 1
p_breakeven  = 1.1364 / o
```

Minimalny sensowny `EV_gross` to **+13,6%**, nie +5% jak zakładał [[21]].
**Wszystkie cztery zamrożone bety w `experiments/test-bets.csv` są ujemne po podatku:**
MB-0001 −6,6%, MB-0002 −0,8%, MB-0003 −2,9%, MB-0004 −1,3%.

**2. Teza o „powtarzalnych zdarzeniach" działa odwrotnie.**
Najlepsza drużyna w bazie (Celtic, 448 meczów) trafia `rożne ≥ 4` w **89,1%** —
fair 1.12, break-even w PL **1.28**. Im częstsze zdarzenie, tym krótszy kurs, tym mocniej
podatek je zabija. Value leży w środku rozkładu (`p ≈ 0,45–0,60`, kursy 1.8–2.4),
nie na jego skraju. To wymaga rewizji [[17]].

**3. Rynek rożnych NIE jest „leniwie wyceniany".** Zmierzone marże:

| rynek / moment | marża |
|---|---|
| 1X2 closing | 3,0% |
| rożne meczowe closing | 5,0% |
| kartki closing | 5,2% |
| rożne drużynowe pre-match, **Pinnacle** | **6,1%** |
| rożne drużynowe pre-match, Bet365 | 7,2–8,5% |
| rożne drużynowe przedziały, **STS** | **13,7%** |

Przewaga nie polega na tym, że rynek nie policzył — polega na **rozjeździe między
Pinnacle a STS**.

**4. Kursy na rożne drużynowe SĄ dostępne.** API-Football ma `Home/Away Corners
Over/Under` (id 57/58), `Home/Away Shots On Target` (243/244), `Home/Away Team Total
Cards` (82/83) od 6 bukmacherów. Blokada, którą zakładaliśmy, nie istnieje.
Brakuje wyłącznie **historii tych kursów**.

**5. Bardzo dużo da się zbacktestować już dziś:**
227 525 meczów z 1X2, 156 474 z Asian Handicap, 148 397 z O/U 2.5,
i **86 025 z kursem otwarcia I zamknięcia Pinnacle** — czyli z policzalnym CLV.

### B2. Audyt STS (ręczny, przeglądarką)

**Kursy są widoczne bez logowania** — to koryguje założenie z transkryptu (1:58:01).
Mecz Ekstraklasy ma ~41 rynków w kategoriach: Gole, Handicap, **Rzuty rożne**, Zawodnicy, Połowy.

- rożne: suma meczowa, „która drużyna więcej", **drużynowe w formie PRZEDZIAŁÓW** (0-2 / 3-4 / 5-6 / 7+)
- **kartek: BRAK**
- **strzałów/SOT: BRAK**

Zmierzona marża na przedziałach Korony Kielce: **13,74%**. Potrzebny edge na przedziale
3-4 @ 2.95: **+9,3 pp** względem rynku po zdjęciu marży.

**Konsekwencja:** MB-0002/3/4 opisują rynek `team corners over/under`, którego u polskiego
bukmachera w tej formie nie ma. Rejestr wymaga przepisania na przedziały albo uznania
za nierealizowalny.

### B3. Zebrane dane — 10 zestawów, 18 GB, 605 zmiennych, 9,2 mln rekordów

| zestaw | rozmiar | zawartość |
|---|---|---|
| statsbomb-full | 17 GB | dane zdarzeniowe, 4 235 meczów × ~3000 zdarzeń |
| transfermarkt | 887 MB | 88 958 meczów (sędzia 99,2%), 3,18 mln składów, 1,27 mln zdarzeń |
| club-football-2000-2025 | 52 MB | 230 557 meczów, 114 tys. ze statystykami, +245 tys. Elo |
| football-data-uk | 41 MB | 92 411 meczów, Pinnacle closing na 86 tys. |
| schochastics | 18 MB | 1 309 501 meczów 1888–2025 |
| understat | 9,7 MB | 21 690 meczów z xG, 2014–2025 |
| footiqo | 1,3 MB | **380 meczów z closing odds na rożne i kartki** |
| apifootball, osm, statsbomb | <1 MB | sondy, 242 stadiony, katalog rozgrywek |

Pełna rozpiska na poziomie kolumn: **`KATALOG-DANYCH.md`** (generowany skryptem).

### B4. Infrastruktura

**Dwa repozytoria na koncie `startupowe1konto1`, oba prywatne:**

1. **`automated_bets_downloader`** — snapshotter kursów.
   Działa na GitHub Actions: `capture` co godzinę (:07), `plan + settle` codziennie 06:20 UTC.
   Sekret `API_FOOTBALL_KEY` ustawiony. **Przetestowany end-to-end na runnerze.**
2. **`Model-buk-2.0`** — projekt. Kopia oryginału z pełną historią (21 commitów)
   + folder `Audyt-danych-i-rynku/` + `KATALOG-DANYCH.md`.

**Klucze API** (wszystkie `chmod 600`, poza repo):

```text
~/.apifootball_key      API-Football, plan Free 100 req/dzien
~/.rapidapi_key         RapidAPI flashscore4 (nieuzywany, patrz nizej)
~/.footballdata_key     football-data.org, 13 rozgrywek
~/.kaggle/access_token  Kaggle (format KGAT_)
```

`gh` zalogowany jako `startupowe1konto1` (zakresy `repo`, `workflow`).

**Dane surowe:** `/Users/wojciechrudnicki/Code_projects/hazard_saas/data/raw` (18 GB,
MacBook Wojtka, **poza repo**).

### B5. Ograniczenia API-Football Free — zmierzone, nie ma tego w dokumentacji

- terminarz widoczny **tylko ±1 dzień** → T-48h nieosiągalne
- limit **~10 requestów/minutę** → skrypt czeka 6,5 s, ma backoff na 429
- parametr `next` zablokowany, `season` ograniczony do **2022–2024**
- 100 req/dzień (w configu ostrożny cap 90)

### B6. Co odrzucone i dlaczego

- **FlashscoreScraping** — przetestowany, **nie działa**. Nawet przykład z README zwraca
  „Found 0 matches"; selektory DOM nieaktualne od listopada 2025.
- **RapidAPI flashscore4** — ścieżka `/api/flashscore/v2/matches/odds` poprawna,
  ale zwraca **Cloudflare 1010**. Subskrypcja działa (498/500), upstream blokuje.

---

## C. Co zostało do zrobienia

### C1. Decyzje czekające na Wojtka

1. **Konwersja transfermarktu na parquet?** 887 MB → ~250 MB, żaden plik >100 MB
   (zmierzone: `game_lineups` 400→73 MB, `appearances` 189→21 MB, `game_events` 171→32 MB).
   Bez tego push do repo się odbije.
2. **Czy dane wchodzą do `Model-buk-2.0`?** Grupa A (122 MB) wchodzi bez problemu.
   StatsBomb (17 GB) nie wejdzie nigdy.
3. **Co z katalogiem `hazard_saas/`** — zmienić nazwę na czytelną czy przenieść dane.

### C2. Następne kroki merytoryczne — rekomendowana kolejność

**1. Spłacić dług w rejestrze (30 minut).**
Rozliczyć MB-0003 i MB-0004 (mecze z 03.09), **unieważnić MB-0002** (kurs nieaktualny
w momencie zamrożenia → wg [[22]] to nie jest ważny frozen bet), dodać kolumnę
`ev_net_after_tax`, poprawić próg z [[21]] z +5% na wartość po podatku.

**2. Backtest na tym, co już mamy — 86 025 meczów z CLV.**
To jest jedyne miejsce, gdzie dziś można się czegokolwiek nauczyć o własnym pipelinie.
Jeśli cały łańcuch (features → model → de-vig → decision gate → CLV) nie produkuje
dodatniego CLV na 1X2 przy 227 tysiącach meczów, na rożnych też nie wyprodukuje —
a dowiecie się tego w tydzień zamiast w rok.

**3. Model rożnych na Footiqo PL 2025/26.**
380 meczów, closing odds na 4 liniach, sędzia w 100%, marża 5%. Pierwszy realny test,
czy jakikolwiek model bije rynek na tym rynku.

**4. Pilnować snapshottera.**
Pierwsze prawdziwe zdjęcia lecą automatycznie. Sprawdzić po tygodniu:
`python3 scripts/snapshotter.py status` w repo `automated_bets_downloader`.

**5. Po 2–4 tygodniach: rozszerzyć universum o Championship.**
Ma **najwyższe base rates rożnych** ze wszystkich lig (69,8% na C≥4 przy 13 605 meczach)
i jest mniej ostro wyceniany niż Premier League.

**6. Decyzja o API-Football Pro ($19/mies).**
Zdejmuje limit sezonów 2022–2024 i podnosi do 7500 req/dzień. **Nie kupować przed
zbudowaniem backtestu na darmowych danych.**

### C3. Odłożone świadomie

- **Pogoda + entity resolution** — [[31 - TODO pogoda i entity resolution]].
  2 968 nazw stadionów, dopasowanie do OSM na poziomie 3,7%. Droga przez kluby
  (~430 encji) zamiast stadionów skraca to 7×.
- **StatsBomb** — 17 GB, warstwa v5 z [[08]]. Jesteście przed v0.1.
- **API-Football sezony 2022–2024** — Wojtek wstrzymał. ~500–1000 requestów, 5–10 dni.
- **Scraper STS/Fortuna/Betclic** — jedyna droga do polskich kursów na rożne.
  1–2 dni pracy. **Zbiera wyłącznie do przodu** — archiwum kursów nie istnieje.

### C4. Cztery dziury w danych, w kolejności blokowania

1. **Historia cen rożnych drużynowych — 0 meczów.** Wyniki mamy dla 114 363.
   Snapshotter to naprawia, ale dopiero z czasem.
2. **Ekstraklasa: rożne i SOT — 0 źródeł.** Rynek domowy pokryty gorzej niż
   czwarta liga angielska. Ma za to 4 131 meczów z **Pinnacle closing na 94,1%**,
   więc CLV na 1X2 jest tam policzalne.
3. **Kontuzje z `known_at`** — wiemy kto zagrał, nie wiemy kto nie mógł.
4. **Ruch linii** — mamy otwarcie i zamknięcie, nic pomiędzy.

---

## D. Gdzie co jest

```text
Model-buk-2.0/
├── Football-Prediction-Brain/     25 not oryginalnych (zrodlo prawdy metodologicznej)
├── Audyt-danych-i-rynku/
│   ├── README.md                  streszczenie 5 ustalen
│   ├── 23–31 *.md                 dziewiec not z 05.09
│   ├── scripts/                   7 skryptow pobierania i sond
│   └── docs/                      2 interaktywne diagramy HTML
├── KATALOG-DANYCH.md              pelna rozpiska kolumn (generowana)
├── HANDOFF.md                     ten plik
└── experiments/test-bets.csv      4 bety, 3 wymagaja rozliczenia

automated_bets_downloader/
├── scripts/snapshotter.py         plan / capture / settle / status
├── config/universe.json           Ekstraklasa (106) + Premier League (39)
├── .github/workflows/             2 workflow, aktywne
└── data/odds_snapshots/           manifest.db + YYYY-MM-DD/*.json.gz

hazard_saas/data/raw/              18 GB danych surowych (poza repo)
```

**Wizualizacja stanu danych:** https://claude.ai/code/artifact/372fcb55-2658-440a-a691-ec7d3d18ae87

---

## E. Jedno zdanie na koniec

Mamy bardzo dużo danych **o tym, co się w meczach działo**, i prawie żadnych **o tym,
ile rynek za to żądał** w rynkach, które nas interesują. Model da się zbudować dziś;
zakład da się zbacktestować dopiero, gdy snapshotter nazbiera cen — dlatego działa
od wczoraj i każdy dzień zwłoki to dzień danych, których już nie będzie.
