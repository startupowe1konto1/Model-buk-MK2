# Audyt danych i rynku — 05.09.2026

Rozpoznanie terenu przed budową modelu. Nie modelowanie, nie produkt: sprawdzenie,
**jakie dane realnie da się zdobyć** i **jak naprawdę wygląda rynek**, na którym mamy grać.

Wszystkie liczby w notatkach są zmierzone na plikach albo na żywym API, nie szacowane.

## Co ustaliliśmy — pięć rzeczy, które zmieniają plan

**1. Podatek 12% wywraca ekonomikę.** Wszystkie cztery zamrożone bety
w `experiments/test-bets.csv` są **ujemne po podatku**. Minimalny sensowny `EV_gross`
to ~+13,6%, a nie +5% jak zakładał [[21 - Pilot live 27-08-2026]]. → [[23 - Krytyczna ocena wykonalnosci]]

**2. Teza o „powtarzalnych zdarzeniach" działa odwrotnie.** Najlepsza drużyna w bazie
(Celtic, 448 meczów) trafia `rożne ≥ 4` w 89,1% — fair 1.12, break-even w PL 1.28.
Im częstsze zdarzenie, tym krótszy kurs, tym mocniej podatek je zabija.
Value leży w środku rozkładu, nie na jego skraju. → [[27 - Faza -1 wyniki empiryczne]]

**3. Rynek rożnych nie jest „leniwie wyceniany".** Pinnacle robi team corners
przy marży **6,1%**, closing na rożne meczowe to **5,0%**. STS ma 13,7%.
Przewaga nie polega na tym, że rynek nie policzył — polega na rozjeździe między
Pinnacle a STS. → [[28 - API-Football wyniki sondy]]

**4. Kursy na rożne drużynowe SĄ dostępne** — API-Football daje `Home/Away Corners
Over/Under`, team cards i SOT od 6 bukmacherów. Blokada, którą zakładaliśmy, nie istnieje.
Brakuje wyłącznie **historii tych kursów**. → [[28 - API-Football wyniki sondy]]

**5. Bardzo dużo da się zbacktestować już dziś:** 227 525 meczów z 1X2,
156 474 z Asian Handicap, 148 397 z O/U 2.5, i **86 025 z kursem otwarcia i zamknięcia
Pinnacle** — czyli z policzalnym CLV. → [[30 - Inwentarz danych]]

## Notatki

| nr | o czym |
|---|---|
| [[23 - Krytyczna ocena wykonalnosci]] | podatek, limitowanie kont, paradoks typsterski, co właściwie sprzedajemy |
| [[24 - Architektura silnika]] | 11 warstw od ingestii do produktu, trzy zegary, stack |
| [[25 - Kolejnosc budowy]] | fazy 0–4; fazy 0–2 nie zawierają modelu sportowego i już są produktem |
| [[26 - Audyt zrodel danych i API]] | tanie API, historyczne zbiory, skąd providerzy biorą dane |
| [[27 - Faza -1 wyniki empiryczne]] | STS zmierzony ręcznie, base rates z 92 tys. meczów, FlashscoreScraping nie działa |
| [[28 - API-Football wyniki sondy]] | 33 bukmacherów, 338 rynków, zmierzone marże |
| [[29 - Darmowe zrodla stan pobrania]] | co pobrane, co dostępne, drabinka marż |
| [[30 - Inwentarz danych]] | pełny spis: rodzaje, okresy, segmenty, dziury |
| [[31 - TODO pogoda i entity resolution]] | odłożone świadomie, żeby nie uciekło |

## Skrypty

| plik | co robi |
|---|---|
| `01_download_football_data.py` | Football-Data.co.uk: 22 ligi × 12 sezonów + 16 krajów |
| `02_apifootball_probe.py` | budżetowa sonda API-Football (~10 ze 100 req/dzień) |
| `03_flashscore4_probe.py` | sonda RapidAPI flashscore4 (12 z 500 req/mies) |
| `04_coverage_and_baserates.py` | pokrycie pól + base rates progów + break-even po podatku |
| `05_odds_reality_check.py` | jakie rynki realnie wracają dla konkretnego meczu |
| `06_download_understat.py` | xG z Understat, 5 lig × 12 sezonów |

Wszystkie mają `--dry-run` tam, gdzie zużywają limit API. Klucze czytane z plików
w katalogu domowym, nigdy z kodu.

## Diagramy

`docs/model-buk-engine.html` — architektura silnika, 11 warstw, 4 widoki prowadzone.
`docs/snapshotter-workflow.html` — workflow zbieracza kursów.

Interaktywne: pan/zoom, wyszukiwanie, tryb jasny/ciemny, eksport PNG/SVG.
Otworzyć w przeglądarce.

## Co z tego wynikło operacyjnie

Powstało osobne repo **`automated_bets_downloader`** — zbieracz kursów działający
na GitHub Actions. Zbiera wyłącznie do przodu, bo historii kursów na rożne drużynowe
nie da się kupić. Każdy dzień zwłoki to dzień danych, których nie będzie.

## Czego wciąż brakuje

1. **Historycznych kursów na rożne drużynowe** — zero. Snapshotter to naprawia, ale dopiero z czasem.
2. **Ekstraklasa: rożne i SOT** — zero w ośmiu źródłach. Rynek domowy jest najgorzej pokryty.
3. **Kontuzje i zawieszenia z `known_at`** — wiemy kto zagrał, nie wiemy kto nie mógł.
4. **Ruch linii między otwarciem a zamknięciem** — mamy dwa punkty, nic pomiędzy.
