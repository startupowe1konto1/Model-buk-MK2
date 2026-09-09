# Handoff — stan projektu na 09.09.2026

> Dokument startowy dla nowej sesji. Poprzedni handoff (05.09) leży w
> `docs/handoff-archiwum/HANDOFF-2026-09-05.md` — jest nadal dobrym opisem
> danych i rynku, ale w czterech miejscach został unieważniony. Tutaj jest to,
> co obowiązuje.

---

## A. Co się zmieniło od 05.09 — czytaj to pierwsze

### 1. Próg decyzyjny był błędny i przepuszczał zakłady przegrywające

Reguła pilotażu brzmiała `EV >= ok. 5%` i liczona była na kursie z tabeli
bukmachera. Polski podatek 12% pobierany jest **od stawki**, więc wyświetlany
kurs jest brutto: 100 zł po kursie 2.00 zwraca 176 zł, nie 200.

Wiążącym warunkiem jest teraz:

```text
EV_net = p_model × (τ × o) − 1 ≥ 0.05
```

| τ | reżim | break-even `p × o` | próg dla `EV_net ≥ 5%` |
|---|---|---|---|
| **0,88** | standard PL, 12% od stawki | 1,13636 (+13,64% brutto) | 1,19318 → **+19,32%** |
| 0,94 | Betclic po degradacji promocji | 1,06383 (+6,38%) | 1,11702 → +11,70% |
| **1,00** | Betclic bez podatku na SOLO | 1,00000 (0%) | 1,05 → +5,00% |

Dowód szkodliwości starej reguły: **MB-0001 był jedynym kandydatem, który
przeszedł przez bramkę w całym pilocie 27.08.** Miał orientacyjne `EV +6,2%`
i po podatku ma **−6,59%**.

Znaleziona przy tym luka strukturalna: nota 22 §5 używała w bramce terminu
`EV_net`, którego vault **nigdzie nie definiował** — §4 definiowała tylko
`EV_gross`. Kto implementował bramkę, musiał sobie dopowiedzieć, i dopowiedział
`EV_gross`. Luka domknięta w 22 §4–5 i 14. Pełna korekta z tabelami:
[[21 - Pilot live 27-08-2026]], sekcja „Korekta 09.09.2026".

### 2. Betclic zmienia rachunek ekonomiczny całego projektu

To **jedyny** legalny polski bukmacher z grą bez podatku na zakłady pojedyncze,
24/7, pre-match i live. Pozostali (Fortuna, Superbet, forBET, LVBET, Betfan,
Betters) mają to wyłącznie na AKO albo BetBuilder, czyli bezużytecznie dla tego
procesu.

Wszystkie cztery zamrożone bety są **ujemne** u bukmachera z podatkiem i
**dodatnie** bez niego. Podatek jest jedyną przyczyną, dla której są
przegrywające — nie model.

Konsekwencja dla [[25 - Kolejność budowy]] fazy −1: instrukcja „marża liczona z
podatkiem 12% w środku" dla Betclica jest nieprawdziwa. Bukmacher bez podatku
może mieć kursy o 13,6% gorsze i nadal wygrywać, co zmienia, kogo warto
snapshotować.

**Do zweryfikowania w regulaminie, nie na podstawie mediów:** pierwsze ~1000 zł
sumy stawek bez warunków, powyżej — SOLO bez podatku tylko przy ≥50% obrotu na
AKO, inaczej 6% na singlach. Okno 12 miesięcy kroczące albo 5000 ostatnich
zakładów.

### 3. Snapshotter nie zbierał danych 7–9.09 i raportował sukces

Krok `status` w workflow `plan + settle` wywalał się codziennie, bo
`Api.__init__` wczytywał klucz API bezwarunkowo, a `cmd_status` tworzy `Api`
tylko po to, żeby odczytać zużycie budżetu z lokalnej bazy SQLite. Klucz był
wymagany i nieużywany, a workflow nie przekazywał sekretu do tego kroku.

GitHub Actions pomija kolejne kroki po nieudanym, a po `status` stoi `Commit`.
Skutek był większy niż komunikat o błędzie: `plan` i `settle` kończyły się
sukcesem, po czym cała praca dnia ginęła razem z runnerem. Zacommitowana baza
stała na 6 września z zerem rekordów `pending`, więc godzinny `capture`
poprawnie nie miał czego zbierać i **kończył się sukcesem, nie robiąc nic**.

Naprawione 09.09 w `automated_bets_downloader`, commit `81c5b55`:
leniwe wczytywanie klucza + `if: always()` na kroku `Commit`.

Potwierdzenie po naprawie: wszystkie cztery kroki zielone, pierwszy w historii
repo commit rozliczeniowy (`986eff1`), tabela `results` 0 → 15, licznik
requestów trwały (58/90 zamiast wiecznego 90/90).

**Kursy na mecze z 7–9.09 są bezpowrotnie stracone.** Plan Free API-Football
widzi terminarz ±1 dzień i nie udostępnia historii kursów.

### 4. Punkt C2.4 starego handoffu był pułapką

Zalecał kontrolę snapshottera przez `snapshotter.py status` — a to była
dokładnie ta komenda, która się wywalała.

**Szersza lekcja, którą warto zaadresować:** system zdrowy i bezczynny wygląda
identycznie jak system martwy. Oba raportują sukces. Jeden mail na dobę
przychodził tylko dlatego, że `status` przypadkiem padał; bez tej usterki cisza
byłaby całkowita. Osłona do dopisania: jeśli w horyzoncie są mecze z universum,
a od N dni nie zapisano żadnego zdjęcia — zgłoś błąd.

---

## B. Stan pogody i entity resolution — robota w toku

Nota [[31 - TODO pogoda i entity resolution]] zakładała dopasowywanie „~430
klubów". **Ta liczba była błędna:** `clubs.csv` zna 796 klubów i pokrywa 85,6%
gospodarzy, a w `games.csv` jest ich 2980. Właściwą miarą nie jest liczba encji,
tylko **pokrycie meczów**.

### Co zrobione

Skrypt: `Audyt-danych-i-rynku/scripts/09_stadiums_and_weather.py`,
trzy niezależne etapy (`wikidata` / `match` / `weather`).

**Overpass odpadł jako kanał.** Główna instancja `overpass-api.de` odrzuca
połączenia, `kumi.systems` daje 504, `overpass.osm.ch` ma tylko dane
Szwajcarii. Pierwsza próba pobierania kaflami przez 41 minut nie ściągnęła nic
— 0,81 s czasu CPU, zero otwartych połączeń, sam sen w retry.

**Wikidata okazała się lepsza, nie tylko dostępna.** Trzyma **aliasy** nazw,
więc „Giuseppe Meazza" i „San Siro" to jeden obiekt — a właśnie na takich parach
łamało się dopasowanie po nazwie.

```text
wikidata: 6 764 stadiony, 15 895 aliasów -> 22 659 wariantów nazw   (21 s)
dopasowane nazwy:  1 493 / 2 968
pokrycie MECZÓW:  68 882 / 88 716  = 77,6%    (przed: 16,6% na 242 stadionach OSM)
  exact: 1 384 nazw
  fuzzy:   109 nazw
```

Wyniki w `hazard_saas/data/raw/osm/`: `stadiums_wikidata.json` (1,2 MB) i
`stadium_match.csv` z kolumną `venue_confidence`.

### Co zostało

**Pogoda nie jest pobrana.** Open-Meteo Archive zwraca `429 Too Many Requests`
— ich limit liczy nie wywołania, a **wagę**: zapytanie o 20 lat × 9 zmiennych
jest ogromne. Blokada weszła po ~10 zapytaniach i utrzymuje się jako
natychmiastowe 429 w 0,2 s, czyli cooldown na poziomie IP.

Skrypt jest już przygotowany pod mniejszą wagę: **5 zmiennych zamiast 9**
(ekstrema temperatury, opad, śnieg, porywy wiatru — zgodnie z
[[04 - Rejestr cech]], gdzie liczy się `EnvironmentalShock`, nie średni
komfort), zakres **od 2014-01-01** zamiast 2006 (bo `football-data-uk` startuje
2014-07 i wcześniejsza pogoda nie ma z czym się spotkać), pauza 5 s i backoff
na 429 z poszanowaniem `Retry-After`.

Do zrobienia w następnej sesji:

```bash
cd Audyt-danych-i-rynku/scripts
python3 -u 09_stadiums_and_weather.py weather \
  --out ../../../hazard_saas/data/raw --top 500
```

Odczekać cooldown (rzędu godziny), potem uruchomić i **nie próbować w kółko** —
każda próba w blokadzie ją wydłuża. Top 500 dopasowanych stadionów pokrywa
60 475 meczów (68,2%).

Ręczny przegląd, który się opłaca: **pierwsze 50 nietrafionych nazw to 8 629
meczów.** Wszystkie to nazwy sponsorskie albo transliteracje — „Bluenergy
Stadium" to Stadio Friuli, „MHPArena Stuttgart" to Mercedes-Benz Arena,
„Decathlon Arena-Stade Pierre-Mauroy" to Stade Pierre-Mauroy. Pół godziny pracy
podnosi pokrycie o kilka punktów procentowych.

### Pułapka na leakage, o której nie wolno zapomnieć

Open-Meteo Archive zwraca **pogodę zrealizowaną, nie prognozę**. Do backtestu
historycznego to akceptowalne. W produkcji to dokładnie ten leakage, który
wymienia [[11 - Czas predykcji i leakage]] — model przewidujący T−24h nie mógł
wiedzieć, ile faktycznie spadło deszczu. Produkcja musi używać endpointu
prognoz i zapisywać je z `known_at`.

---

## C. Co robić dalej, w tej kolejności

### 1. Backtest CLV — to jest ścieżka krytyczna

Jedyne miejsce, gdzie dziś można się czegokolwiek nauczyć o własnym pipelinie.
Podłoże jest **lepsze**, niż zapisano w starym handoffie:

```text
mecze z KOMPLETNYM Pinnacle open + close : 86 024
zakres                                   : 2014-07-25 → 2026-01-14
ligi                                     : 22
overround                                : 3,44% na otwarciu → 3,10% na zamknięciu
```

I jest co gonić, bo linie realnie się ruszają:

| selekcja | mediana \|ruchu fair prob\| | udział ruchów > 2 pp |
|---|---|---|
| gospodarz | 1,85 pp | **46,9%** |
| remis | 0,78 pp | 12,9% |
| gość | 1,59 pp | 40,7% |

Pięć kroków:

1. **de-vig** na otwarciu i zamknięciu; ten krok jest testowalny sam w sobie —
   czy prawdopodobieństwa po zdjęciu marży są skalibrowane na zamknięciu
2. **`P_model` wyłącznie z cech point-in-time**; `club-football` ma Elo obu
   stron w momencie meczu, więc tego nie trzeba budować
3. **bramka decyzyjna** z progiem na `EV_net`, nie `EV_gross`
4. **CLV, nie zysk:** `CLV = fair_open / fair_close − 1` na cenach po zdjęciu
   marży, dla każdej selekcji, którą model wziąłby po kursie otwarcia
5. **walk-forward**, nigdy jeden podział

Kryterium rozstrzygające: jeśli łańcuch nie produkuje dodatniego CLV na 1X2 przy
86 tysiącach meczów, na rożnych też nie wyprodukuje.

### 2. Dokończyć pogodę

Patrz sekcja B. Nota [[04]] stawia pogodę na niskim priorytecie bazowym, więc to
praca na przyrostową wartość — ale jest już rozpoczęta i tanio ją domknąć.

### 3. Dług w rejestrze — resztki

Próg jest już naprawiony (to była pilna część). Zostało:

- rozliczyć **MB-0003** i **MB-0004** (mecze z 03.09, wymaga liczby rożnych)
- **unieważnić MB-0002** — jego własne pole `notes` przyznaje, że kurs nie był
  aktualny w momencie zamrożenia, więc wg [[22]] §7 to nie jest ważny frozen bet
- dodać do `experiments/test-bets.csv` kolumny `tax_regime`, `odds_effective`,
  `ev_net`

Uwaga na proporcje: **rozliczenie czterech betów nie powie nic o tym, czy
podejście działa.** Przy `n = 4` nie da się odróżnić umiejętności od rzutu
monetą. Wartość tej pracy jest w tym, że rejestr przestanie dezinformować
zespół i że τ wejdzie do schematu, a nie w wiedzy o modelu.

### 4. Osłona monitoringu snapshottera

Patrz A.4. Jeden warunek, który odróżni „bezczynny bo przerwa na kadry" od
„martwy".

### 5. Faza −1: dokończyć audyt rynku

Zrobiony jest tylko STS (05.09, ręcznie). Zostają Superbet, Fortuna i **Betclic**
— ten ostatni jest teraz najważniejszy z powodów z A.2.

---

## D. Gdzie co jest

```text
Model-buk-2.0/
├── HANDOFF.md                      ten plik
├── docs/handoff-archiwum/          poprzednie handoffy
├── Football-Prediction-Brain/      25 not metodologicznych (zrodlo prawdy)
│   ├── 00 Mapa · 14 Decyzje · 21 Pilot · 22 Zasady   <- zmienione 09.09
├── Audyt-danych-i-rynku/
│   ├── 23-31 *.md                  notatki z audytu 05.09
│   └── scripts/09_stadiums_and_weather.py            <- nowe, etapy wikidata/match/weather
├── KATALOG-DANYCH.md               pelna rozpiska kolumn
└── experiments/test-bets.csv        4 bety, dlug wg C.3

automated_bets_downloader/          snapshotter, naprawiony 09.09 (81c5b55)

hazard_saas/                        dane surowe, POZA repo
├── data/raw/                       18 GB, 9 333 pliki, MANIFEST.txt
│   ├── osm/stadiums_wikidata.json  6 764 stadiony z aliasami
│   ├── osm/stadium_match.csv       dopasowanie, 77,6% meczow
│   └── weather/                    PUSTE — patrz B
└── report/                         atlas PDF, projekt niezalezny od Model Buk
```

Uwaga: `hazard_saas/report/` to **osobny projekt** — 31-stronicowy atlas
wizualizacji na tych samych danych, 27 plansz, 136 liczb weryfikowanych
skryptem. Nie jest częścią Model Buk i nie wpływa na metodologię silnika.

---

## E. Jedno zdanie na koniec

Podatek był przez cały czas policzony w złą stronę, a snapshotter przez trzy dni
raportował sukces, nie zbierając nic — oba błędy były niewidoczne, bo nic ich nie
sprawdzało. Następny etap nie polega na dołożeniu kolejnego źródła danych, tylko
na przepuszczeniu tego, co już jest, przez pełny łańcuch aż do CLV, żeby dowiedzieć
się, czy w ogóle jest tu przewaga do znalezienia.
