# Model Buk MK2 — koncept scalony i plan pracy

Wersja z 1.10.2026. Dokument łączy trzy źródła: notatki i pomiary z repo
Oskara (`Model-Buk-Merged`, stan do 1.10), Twoje repo `mkonieczny1212/Model-buk`
(v0.7–v0.9, profil strategii 1,30–2,00) i zapis rozmów z sierpnia–września
(`Codex/2026-09-16`, propozycja v0.8 z 20.09). Pracujemy w `Model-buk-MK2`
(kopia robocza: `Documents\Model-Buk-Merged-work`). Repo Oskara tylko czytamy;
jego nowe zmiany dociągamy `git fetch upstream`, nigdy nie pushujemy do `main`
tamtego repo.

## 1. Co budujemy (jedno zdanie)

Skaner, który kilka razy dziennie przegląda mecze top lig i pucharów z danymi
live z API‑Football, wylicza uczciwą cenę każdego zdarzenia, porównuje ją z
kursami i proponuje **single z kursem 1,30–2,00 albo kupony z 2 nóg, których
kurs łączny mieści się w tym samym paśmie**, posortowane po dolnej granicy EV po
podatku 12%. Każda propozycja trafia do niezmiennego rejestru papierowego, jest
rozliczana po meczu i porównywana z kursem zamknięcia (CLV). Jeśli nic nie
przechodzi progu, skaner mówi to wprost.

## 2. Strategia kursowa — Twoje założenia (z v0.8 i rozmów)

| Parametr | Wartość | Skąd |
|---|---|---|
| kurs końcowy rekomendacji | 1,30–2,00 | `strategy.py` (commit 21.09, rozszerzone z 1,50–1,90) |
| preferowany koszyk | 1,65–1,80 (tylko do porównania, nie premiowany) | propozycja v0.8 §5A |
| kupon | 2 nogi, różne mecze, ten sam bukmacher, kurs łączny w paśmie (np. 1,30 × 1,30 = 1,69) | V08_SKANER_VALUE |
| konserwatywny edge | ≥ 3 p.p. (p_dolne − p_rynku po de‑vig) | v0.8 |
| konserwatywne EV netto | ≥ 5% liczone na dolnej granicy p | v0.8 |
| podatek | 12% od stawki, raz od całego kuponu (τ = 0,88) | ustawa, nota 22 |
| wiek kursu | ≤ 6 h | v0.8 |
| jeden single na mecz, zdarzeń z jednego meczu nie łączymy | | v0.7 §4 |
| stawki | tylko PAPER; po walidacji ¼ Kelly, max 1–2% kapitału | v0.8 §6 |
| promocja PAPER → BET | ≥ 200 rozliczonych, dolna granica 95% ROI > 0 i CLV > 0, ECE ≤ 5 p.p. | V09 |
| ligi | top 10 + LM, LE, LKE | rozmowa 16.09 |
| podstawa prognozy | aktualna forma (ostatnie mecze bieżącego sezonu), nie średnie z lat | rozmowa 16.09, V07 |

## 3. Co zmierzył Oskar i co to znaczy dla tej strategii

Trzy wyniki, które trzeba przyjąć, bo są policzone na dziesiątkach tysięcy meczów:

1. **Model przegrywa z Pinnacle w każdej lidze.** 1X2: pula modeli log‑loss 1,005
   wobec 0,993 (otwarcie) i 0,989 (zamknięcie) na 15 875 meczach, 0 z 18 lig
   lepszych. Pula model+rynek i model residualny nic nie dodają (waga modelu
   2–8%). Ten sam wniosek wyszedł Tobie dla rożnych (closing lepiej skalibrowany
   niż PURE v0.1 na 4 z 4 linii).
   **Konsekwencja:** uczciwa cena bierze się z odmarżowanego Pinnacle (albo
   konsensusu ≥ 3 bukmacherów, albo macierzy wyników dopasowanej do Pinnacle).
   Nasz model jest wetem i źródłem tam, gdzie ostrej ceny nie ma. Nie szukamy
   „lepszego modelu niż rynek”; szukamy **bukmachera, który odstaje od rynku**.
2. **Próg podatkowy.** Przy τ = 0,88 singiel wychodzi na zero dopiero, gdy kurs
   jest o **13,6%** wyższy od uczciwego. Udokumentowane przewagi z porównania
   cen to 3–10%, a przewagę > 10% skaner Oskara uznaje za podejrzaną (30.09:
   wszystkie „przewagi” 15–25% były błędem danych). W pierwszych trzech
   przebiegach jego skanera: **0 singli i 0 kuponów przy pełnym podatku**,
   46–92 nóg z wartością bez podatku.
3. **Kupony z nóg value są jedyną drogą po podatku**, bo podatek płaci się raz,
   a przewagi nóg się mnożą: 2 nogi po 6,6% albo 3 nogi po 4,4% dają zero po
   podatku; dwie nogi po ~10% dają kupon z EV +7,4%.

To jest dokładnie Twoja intuicja „dwa mniejsze kursy dające łącznie kurs w
widełkach”. Rachunek ją potwierdza, z jednym zastrzeżeniem: noga musi
**przebijać uczciwą cenę**, nie tylko mieć wysokie prawdopodobieństwo. Jeśli
najlepszy kurs równa się uczciwemu, kupon z 2 nóg traci po podatku ~12%, a
„często wchodzi” nie zmienia wyniku w długim okresie (nota Oskara z 1.10 o
pomyśle „najlepszy kurs, wysoka szansa”).

### Wymagane przebicie uczciwej ceny na nogę (τ = 0,88)

| Struktura | próg zera | próg EV +5% |
|---|---:|---:|
| single | +13,6% | +19,3% |
| kupon 2 nogi | +6,6% na nogę | +9,2% |
| kupon 3 nogi | +4,4% na nogę | +6,1% |

Pasmo 1,30–2,00 oznacza uczciwe prawdopodobieństwa ok. 50–77%. Nogi kuponu
2 × ~1,30–1,45 to zdarzenia o szansie 70–80%: faworyci, under/over przy
jasnych liniach, podwójna szansa, handicapy. Tam marża bukmacherów jest
najniższa (efekt faworyt–outsider), więc to dobre pole do szukania odchyleń.

## 4. Jak łączymy oba podejścia

Dwie półki w jednym skanerze, obie zapisywane do rejestru i rozliczane
osobno, żeby po 4–8 tygodniach wiedzieć, która ma CLV:

**Półka A — wartość względem ostrej referencji (podejście Oskara).**
Uczciwa cena z Pinnacle/konsensusu, p_se jako niepewność, noga = kurs z
dodatnią dolną granicą EV bez podatku, kupon 2 nóg z dodatnią dolną granicą EV
przy τ = 0,88, kurs łączny 1,30–2,00 (Twój filtr), szansa trafienia ≥ 30%.

**Półka B — Twoja strategia „1,30–2,00” z modelem jako źródłem p.**
Dla zdarzeń, dla których nie ma ceny Pinnacle (rożne, kartki, strzały, gole
drużyn), p z naszego modelu (NB2 Oskara dla liczników: bije częstość ligową w
34 z 34 liniach; Gamma–Poisson live v0.7 jako drugi głos), konserwatywny edge
≥ 3 p.p. i EV netto ≥ 5% na dolnej granicy. Status zawsze PAPER, bo model nie
ma jeszcze porównania z kursami na tych rynkach.

Wspólne dla obu: rejestr append‑only (`config_id`, zamrożone p, kurs, czas,
źródło ceny), rozliczenie po meczu, CLV na kursie wziętym (surowy i po
podatku), mediana CLV i odsetek pobitych zamknięć (nie średnia), stop po 100
kuponach, jeśli mediana CLV ≤ 0.

## 5. Dane: co mamy, czego brakuje

| Dane | Stan w MK2 (kopia z 27.09) | Uwagi |
|---|---|---|
| Klient API‑Football (fixtures, kursy, kontuzje, składy, statystyki, predykcje jako benchmark) | jest: `app/src/model_buk/live_provider.py`, `ingestion/api_football.py` | klucz z `API_FOOTBALL_KEY`; cache na dysku; licznik limitu z nagłówków |
| Skan papierowy `scan-current` | jest: `paper_scan.py`, `config/paper_scan.toml` | dziś: 2 dni, max 10 meczów, próg EV 2%, limit kursu 2,00, haircut 5 p.p. — do podmiany na profil z §2 |
| Modele goli (DC, NB2, GAS, Elo, residual) i rożnych | są w `models/` | |
| Uczciwa cena z Pinnacle, klasy rynków, wykluczanie rodzajów zakładów, macierz wyników | **brak** (skaner Oskara `scanner/` powstał 30.09, niewypchnięty) | albo dociągamy po jego pushu, albo piszemy lekką wersję (ok. 300 linii: de‑vig potęgowy 1X2/O‑U/AH + konsensus) |
| Archiwum kursów (co godzinę) | **brak** | API‑Football nie trzyma historii kursów, więc bez własnego archiwum nie ma CLV ani „wieku kursu”. To pierwsza rzecz do uruchomienia |
| Historia meczów do fitowania modeli | **brak w Git** (`data/multileague/europe16_matches_v05.csv.gz` jest w `Downloads\V7\data`) | skopiować z V7 do `app/data/`; pełne `history.sqlite` (134 tys. meczów) ma tylko Oskar — poprosić o `seed.tar.gz` |
| Profil strategii 1,30–2,00, generator kuponów, rozliczenia v0.9 | są w Twoim repo (`strategy.py`, `settlement.py`, `web/`) | do przeniesienia do MK2 jako `app/src/model_buk/strategy.py` + testy |
| Kursy polskich bukmacherów | **brak u obu** | feed API‑Football ma ~9 zagranicznych (Bet365, 1xBet, Betano, Pinnacle, Betfair…); polskie wymagają odds‑api.io (£49–99/mies.) — decyzja po 4 tygodniach pomiaru |

### Dodatkowe źródła sprawdzone 14.10.2026

**betwatch.fr (moneyway).** Pokazuje, ile pieniędzy dopasowano na giełdzie
Betfair na każdy wynik (wolumen i udział w rynku: 1X2, powyżej/poniżej,
połowy i inne), odświeżane co 15 s na żywo i co 60 s przed meczem. Strona
blokuje automaty (weryfikacja „czy jesteś człowiekiem”), więc nie scrapujemy;
jest API: darmowy okres 14 dni i 1 000 zapytań, potem płatny plan (ceny tylko
po zalogowaniu), limit 1 zapytanie / 40 s dla danych przedmeczowych. Rola w
projekcie: sygnał „przepływu pieniędzy” z planu Oskara (29.09) — czy udział
wolumenu na wyniku odbiega od szansy z odmarżowanego Pinnacle i czy taki
rozjazd przewiduje ruch kursu do zamknięcia. Moment startu: dopiero gdy nasz
kolektor kursów działa stabilnie (żeby było z czym porównać); 14 dni × zrzut co
30 min = ok. 670 zapytań, mieści się w darmowym okresie. Betfair tylko jako
źródło danych, nigdy do zakładów (art. 107 KKS).

**FBref.** W styczniu 2026 Opta wypowiedziała Sports Reference dane
zaawansowane; FBref usunął je i zostawił tylko podstawowe statystyki
historyczne. xG, strzały, rożne, kartki i składy mamy z API‑Football (xG od
2022) i Understat, więc FBref nie wnosi nowej informacji. Nie budujemy na nim
niczego; ewentualnie do ręcznej kontroli pojedynczych liczb.

## 6. Podłączenie API‑Football (plan Pro, 7 500 zapytań/dzień)

Stan 14.10: kolektor gotowy (`app/scripts/mk2_odds_collector.py`, testy
`app/tests/test_mk2_odds_collector.py`, harmonogram
`app/scripts/mk2_register_tasks.ps1`). Instrukcja krok po kroku:
`docs/MK2-START.md`. Środowisko chmurowe asystenta nie ma dostępu do
API‑Football (blokada sieci), więc kolektor działa na komputerze MK.


1. Klucz jako zmienna użytkownika Windows, nie w plikach repo:
   `setx API_FOOTBALL_KEY "twój_klucz"` (potem nowe okno PowerShell).
2. Środowisko: `cd Model-Buk-Merged-work\app`, `python -m venv .venv`,
   `.venv\Scripts\python -m pip install -r requirements-lock.txt`.
3. Test połączenia: `.venv\Scripts\python -m model_buk scan-current --config config/paper_scan.toml --mode live`
   — w wyniku powinien być status dostawcy z `requests_remaining`.
4. Budżet dzienny (wzór Oskara): kursy ~600, wyniki ~150, przedmeczowe
   300–450, rezerwa 1 500; każdy skrypt zatrzymuje się przy rezerwie.
5. Kolektor kursów: co godzinę dla dziś + 2 dni, co 6 h do 6 dni naprzód,
   zapis surowych odpowiedzi (JSON) i tabeli `quotes(fixture, bookmaker,
   bet_type, selection, odds, seen_at, api_update)`; kursy zamknięcia co
   15 minut w ostatnich 2 godzinach przed meczem.

## 7. Plan pracy (gałąź `mk/praca`)

| Krok | Co | Gotowe, gdy |
|---|---|---|
| 0 | gałąź `mk/praca`, dane z V7 do `app/data`, klucz API, testy przechodzą (`pytest -q`) | zielone testy na czystym klonie |
| 1 | kolektor kursów + katalog meczów (top 10 lig + 3 puchary), zadanie w Harmonogramie co godzinę | po 24 h archiwum ma kursy z co najmniej 8 bukmacherów dla wszystkich meczów okna |
| 2 | profil strategii z §2 jako `StrategyProfile` w MK2; `paper_scan.toml` na nowe progi; test graniczny 1,30/2,00 i podatku | testy z Twojego repo przeniesione i zielone |
| 3 | uczciwa cena: de‑vig Pinnacle (potęgowy), pochodne, konsensus ≥ 3; p_se wg czasu do meczu; limit przewagi 10% | na tablicy z jednego dnia liczba nóg i kuponów zgadza się z ręcznym sprawdzeniem 5 meczów |
| 4 | półki A i B, generator kuponów 2 nóg w paśmie, rejestr SQLite append‑only, strona HTML / widok w `web/` | pierwszy przebieg zapisany z `config_id`, strona pokazuje „brak kuponów” gdy pusto |
| 5 | rozliczenia (v0.9) + CLV na prawdziwym zamknięciu, raport 7/30 dni: mediana CLV, pobite zamknięcia, zysk po podatku, osobno A/B i single/kupony | po tygodniu raport liczy się sam |
| 6 | po 4 tygodniach: decyzja o feedzie polskich kursów; po 8 tygodniach: raport promocji wg bramki V09 | |

Dopiero po kroku 5 wracamy do modeli (składy T−60, sędzia, forma 5/10),
i tylko jeśli półka B pokaże, że model coś wnosi względem konsensusu.

## 8. Zasady, których się trzymamy

- Decyzja tylko na danych znanych przed chwilą decyzji; zamknięcie tylko do CLV.
- Brak danych zostaje brakiem, nigdy zerem; brak kursu = NO BET z powodem.
- Kursy nigdy nie są wejściem modelu PURE.
- Każda zmiana progu = nowy `config_id`, wyniki papierowe od zera.
- Pinnacle i Betfair tylko jako źródło ceny (art. 107 KKS).
- Średnie CLV nie jest wynikiem; patrzymy na medianę i odsetek pobitych zamknięć.
- Nie commitujemy kluczy, danych ani `.venv`; diff czytany przed każdym commitem.

## 9. Decyzje po Twojej stronie

1. Lista lig: top 10 (które dokładnie?) + LM/LE/LKE — potwierdź.
2. Czy prosimy Oskara o push skanera (30.09–1.10) i `seed.tar.gz`, czy budujemy
   lekką wersję uczciwej ceny sami (ok. 1 dzień pracy).
3. Bukmacher docelowy do pomiaru: na razie Bet365/Betano z feedu jako zastępstwo,
   polskie kursy po pierwszym miesiącu.
4. Rytm skanów: co godzinę + co 15 min w ostatnich 2 h przed meczem (propozycja).
