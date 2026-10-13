# Model Buk MK2 — koncept scalony i plan pracy

Wersja z 1.10.2026, uzupełniona 4.10.2026 (§0, §5–§7, §10). Dokument łączy
trzy źródła: notatki i pomiary z repo Oskara (`Model-Buk-Merged`, stan do
1.10), Twoje repo `mkonieczny1212/Model-buk` (v0.7–v0.9, profil strategii
1,30–2,00) i zapis rozmów z sierpnia–września (`Codex/2026-09-16`, propozycja
v0.8 z 20.09). Pracujemy w `Model-buk-MK2` (kopia robocza:
`Documents\Model-Buk-Merged-work`, gałąź `mk/praca`). Repo Oskara tylko
czytamy: jego zmiany dociągamy `git fetch upstream`, a wysyłanie na pilota
`upstream` jest zablokowane (`push = DISABLED`).

## 0. Stan na 4.10.2026 (co zrobione, co ustalone)

> **13.10.2026:** dostaliśmy pełną paczkę danych Oskara (stan na 1.10 17:42 UTC) i
> mamy panel z kuponami, wynikami i planem. Szczegóły danych, okna powtórek, wyniki
> porównania strategii i odświeżanie panelu: [MK2-DANE.md](MK2-DANE.md). Aktualna lista
> zadań jest w `app/config/mk2/status.json` (zakładka Plan w panelu); punkty niżej
> opisują stan z 4.10, chyba że oznaczono inaczej.

- Gałąź `mk/praca` zawiera **całą pracę Oskara z 28.09–1.10**: była na
  GitHubie na gałęzi `feature/api-football-history` (41 commitów ponad
  `main`), nie na `main`. Dołączona fast-forwardem, bez konfliktów. Jest w
  niej: pobieranie historii z API-Football, kolektor kursów co godzinę,
  kolektor przedmeczowy (kontuzje, składy), kursy zamknięcia, katalog meczów,
  skaner v1.1 (`app/src/model_buk/scanner/`: uczciwa cena z Pinnacle,
  klasy rynków, wykluczanie rodzajów zakładów, kupony, rejestr, rozliczenia,
  strona HTML), wdrożenie na Railway, `docs/SKANER.md`,
  `docs/PLAN-SKANERA-VALUE-BETOW.md` i raporty.
- Katalog danych: każdy skrypt czyta zmienną `MODEL_BUK_DATA`; bez niej
  domyślnie wskazuje na dysk Oskara. U nas: `Documents\Model-Buk-Data`.
  Skrypty w `app/scripts/mk2/`: `env.ps1` (katalog danych, klucz z rejestru
  użytkownika), `run_job.ps1 -Job <zadanie>` (jeden runner dla wszystkich
  zadań, log w `<dane>\logs\<zadanie>.log`), `register_tasks.ps1`
  (Harmonogram zadań Windows, folder `\ModelBukMK2\`, profil `Shared` albo
  `Full`).
- Kolektor kursów sprawdzony na Twoim kluczu: 3 zapytania → 10 meczów,
  27 311 kursów, 9 bukmacherów (Bet365, Marathonbet, 1xBet, Betfair,
  William Hill, BetVictor, **Pinnacle**, Betano, SBO).
- **Klucz API w systemie to plan Free** (konto „Mikołaj”): 100 zapytań
  dziennie, 10 na minutę, kursy tylko z okna wczoraj–jutro, a zapytania po
  lidze dla sezonu 2026 odrzucane („try from 2022 to 2024”). Na tym planie
  skaner nie ma jak działać (szczegóły w §6). Potrzebny klucz z planu Pro.
- Avast skanuje ruch TLS i podmienia certyfikaty; Python w venv ich nie
  uznawał. Obejście: `truststore` (magazyn certyfikatów Windows) włączony
  plikiem `app/.venv/Lib/site-packages/zz_truststore.pth`. To ustawienie
  venv, nie repo: po odtworzeniu venv trzeba je powtórzyć
  (`pip install truststore` + ten sam plik).
- Dane z `Downloads\V7\data` skopiowane do `app/data/` (historia 16 lig
  v0.5, EPL, rożne, Understat); są poza gitem.
- Oskar wpisał 1.10 limity „nogi do 2,00, kupon do 3,00” do
  `ScanConfig` (`max_leg_odds`, `max_coupon_odds`) i opisał pomysł
  „najlepszy kurs przy wysokiej szansie” z testem w rejestrze. Nasze pasmo
  1,30–2,00 na kurs łączny jest węższe od jego 3,00.

## 1. Co budujemy (jedno zdanie)

Skaner, który kilka razy dziennie przegląda mecze z danymi live z
API‑Football, wylicza uczciwą cenę każdego zdarzenia, porównuje ją z
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
| promocja PAPER → BET | ≥ 200 rozliczonych, dolna granica 95% ROI > 0 i CLV > 0, ECE ≤ 5 p.p. (zob. §10 pkt 4) | V09 |
| ligi | top 10 + LM, LE, LKE (zob. §10 pkt 3) | rozmowa 16.09 |
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
   podatku; dwie nogi po 10% dają kupon z EV +6,5% (1,10² × 0,88 − 1).

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

Pasmo 1,30–2,00 na kurs łączny oznacza uczciwe prawdopodobieństwa kuponu ok.
50–77%. Przy 2 nogach każda ma wtedy kurs średnio **1,14–1,41**, czyli szansę
ok. 71–88%: faworyci, podwójna szansa, under/over przy jasnych liniach,
handicapy. Tam marża bukmacherów jest najniższa (efekt faworyt–outsider), więc
to dobre pole do szukania odchyleń, ale też odchylenia rzędu 7–10% zdarzają się
tam rzadko (zob. §10 pkt 2).

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

## 5. Dane: co mamy, czego brakuje (stan 4.10)

| Dane | Stan w `mk/praca` | Uwagi |
|---|---|---|
| Klient API‑Football (fixtures, kursy, kontuzje, składy, statystyki) | jest: `live_provider.py`, `ingestion/api_football.py`, `scripts/download_history.py` | klucz z `API_FOOTBALL_KEY`; licznik limitu z nagłówków |
| Kolektor kursów co godzinę + archiwum SQLite | jest: `scripts/collect_odds.py`, `ingestion/api_football_odds_archive.py`; u nas archiwum zaczęte 4.10 (test) | koszt: 1 zapytanie na 10 meczów danego dnia; sobota ≈ 40 stron |
| Kursy zamknięcia, kolektor przedmeczowy, katalog meczów | jest: `collect_closing_odds.py`, `run_prematch_collector.ps1`, `ingestion/fixture_directory.py` | wymaga planu Pro |
| Uczciwa cena z Pinnacle, klasy rynków, wykluczanie rodzajów zakładów, macierz wyników, podział na połowy | **jest**: `scanner/fair.py`, `tiers.py`, `bet_types.py`, `halves.py` | dołączone z gałęzi Oskara 4.10 |
| Generator kuponów, rejestr, rozliczenia, CLV, strona HTML | **jest**: `scanner/coupons.py`, `register.py`, `settlement.py`, `report_html.py`; `scripts/run_scanner.py` | progi w `scanner/config.py` (`ScanConfig`, `RunSettings`) |
| Historia meczów do fitowania modeli (`api-football/tables/history.sqlite`) | **jest od 13.10** (paczka Oskara): 221 644 mecze, 89 rozgrywek, ostatni mecz 30.09 | za stara na fit (blokada 3 dni); odświeży ją nowa paczka albo `refresh_current_season.py` na kluczu Pro. `seed.tar.gz` niepotrzebny: jest w całości w paczce |
| Historia 16 lig v0.5, EPL, rożne, Understat (CSV) | jest w `app/data/` (poza gitem) | z `Downloads\V7\data` |
| Profil strategii 1,30–2,00, generator kuponów v0.8, rozliczenia v0.9 | w Twoim repo `mkonieczny1212/Model-buk` (`strategy.py`, `settlement.py`, `web/`) | do przeniesienia jako filtr pasma na `ScanConfig`, nie jako osobny skaner |
| Kursy polskich bukmacherów | **brak u obu** | feed ma 9 zagranicznych; polskie wymagają odds‑api.io (£49–99/mies.) — decyzja po 4 tygodniach pomiaru |

## 6. API‑Football: co pokazały sondy z 4.10 i co z tego wynika

Zmierzone na kluczu z systemu (plan Free):

| Zapytanie | Wynik |
|---|---|
| `/status` | plan Free, 100 zapytań/dzień, 10/min, ważny do 16.09.2027 |
| `/odds?date=2026-10-05` | 7 stron po 10 meczów, 9 bukmacherów, ~60 rodzajów zakładów (w tym rożne) |
| `/odds?date=2026-10-04` (niedziela) | 41 stron |
| `/odds?date=2026-10-10` (sobota za tydzień) | odmowa: „Free plans do not have access to this date, try from 2026-10-03 to 2026-10-05” |
| `/odds?league=39&season=2026` | odmowa: „Free plans do not have access to this season, try from 2022 to 2024” |
| `/fixtures?date=2026-10-05` | 99 meczów, w naszych ligach 1 (poniedziałek) |

Wnioski:

1. Na planie Free nie da się prowadzić archiwum kursów (okno 3 dni, a
   sam weekend to ~80 stron dziennie przy limicie 100), nie da się odświeżać
   katalogu meczów ani pobierać statystyk do rozliczeń dla sezonu 2026, i
   nie da się ściągnąć historii do modeli. Skaner Oskara zakłada plan Pro
   (7 500/dzień) z rezerwą 1 500 i ~100–300 zapytań na dobę dla samego
   skanera plus ~600 dla kursów.
2. Decyzja 4.10: **używamy klucza ze wspólnego opłaconego konta**, czyli
   dzielimy z instancją Oskara te same 7 500 zapytań dziennie. Klucz zostaje
   w zmiennej użytkownika Windows (`setx API_FOOTBALL_KEY "..."`), nigdy w
   repo. Oskar musi o tym wiedzieć: w soboty jego kolektor sam potrafi
   zjeść 2–3 tys. zapytań.
3. Zasada współdzielenia: nasze zadania mają rezerwę **3 000**, wyższą od
   jego rezerw (kolektory 1 500, kursy zamknięcia 2 500), więc gdy limit się
   kurczy, zatrzymujemy się pierwsi. Profil `Shared` w `register_tasks.ps1`
   uruchamia u nas tylko: kolektor kursów co 2 h (`--days-ahead 1
   --max-requests 60`, ≤ 720/dzień), kursy zamknięcia co 15 min
   (`--max-requests 15`) i skaner co 30 min (1–3 zapytania na kalendarz,
   do 40 na rozliczenia), a o 3:00 nocny fit modeli (bez API, do
   `Model-Buk-Data-MK2\model_cache`, z hiperparametrami z paczki; pomijany, gdy
   najnowszy mecz w historii ma więcej niż 3 dni). Kolektora przedmeczowego,
   odświeżania sezonu i pobierania historii u nas nie uruchamiamy: historię
   bierzemy z paczek od Oskara (13.10: pełny `Model-Buk-Data`, przyjmowany przez
   `scripts/mk2/import_data.py`; `seed.tar.gz` jest w niej zawarty). Gdyby trzeba
   było odświeżyć ją samemu, to tylko do kopii w folderze MK2, nigdy do paczki.
   Profil `Full` (własny klucz Pro) włącza wszystkie zadania jak u Oskara.
4. Sieć: Avast podmienia certyfikaty TLS; venv ma włączony `truststore`
   (§0). Bez tego każdy skrypt kończy się `CERTIFICATE_VERIFY_FAILED`.

## 7. Plan pracy (gałąź `mk/praca`)

| Krok | Co | Gotowe, gdy | Stan |
|---|---|---|---|
| 0 | gałąź `mk/praca`, blokada pusha do repo Oskara, dane z V7, venv, testy, gałąź Oskara dołączona | zielone testy na czystym klonie | 4.10: zrobione, testy w toku |
| 1 | klucz ze wspólnego konta (`setx`), `register_tasks.ps1` (profil Shared: kolektor kursów co 2 h, kursy zamknięcia, skaner) | po 24 h archiwum ma kursy ≥ 8 bukmacherów dla meczów dziś+jutro, `scanner.log` ma wpisy co 30 min | skrypty gotowe i przetestowane; czeka na klucz i rejestrację zadań |
| 2 | historia meczów i fit modeli: paczka od Oskara w `Model-Buk-Data` (nie pobieramy historii na wspólnym kluczu) | skaner raportuje `model=ok` zamiast `fit_stale` | 13.10: paczka przyjęta, ale historia kończy się 30.09, więc fit stoi do nowszej paczki |
| 3 | pierwszy przebieg skanera Oskara u nas (`run_scanner.py`), odczyt `latest.html` i rejestru; zrozumienie, które progi odpowiadają za „0 kuponów przy τ 0,88” | przebieg zapisany z `config_id`, strona pokazuje wynik | 13.10: zrobione: powtórki 30.09–1.10 z modelami (`scan_mk2.py`), rozliczone; 0 kuponów przy 12% w obu konfiguracjach (MK2-DANE.md §4) |
| 4 | nasz profil: filtr pasma 1,30–2,00 na kurs łączny i rozmiar kuponu 2 jako pola `ScanConfig` (nowy `config_id`), półka B dla rynków bez Pinnacle; testy graniczne 1,30/2,00 i podatku | testy zielone, przebieg z nowym `config_id` w rejestrze | |
| 5 | raport 7/30 dni z rejestru: mediana CLV, pobite zamknięcia, zysk po podatku, osobno A/B i single/kupony | po tygodniu raport liczy się sam | |
| 6 | po 4 tygodniach: decyzja o feedzie polskich kursów; po 8 tygodniach: raport promocji | | |

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
- Kod Oskara (`scanner/`, `ingestion/`, `scripts/*.ps1`) zmieniamy jak
  najmniej; nasze ustawienia i skrypty trzymamy w `app/scripts/mk2/` i w
  polach `ScanConfig`, żeby `git fetch upstream` dalej wchodził bez konfliktów.

## 9. Decyzje po Twojej stronie

1. ~~Klucz API-Football z planu Pro~~ — rozstrzygnięte 4.10: wspólne konto
   (§6 pkt 2–3). Do zrobienia: `setx`, powiadomić Oskara, poprosić o nowszą paczkę
   danych (po 1.10; zbieranie przeszło na jego serwer).
2. Lista lig: top 10 (które dokładnie?) + LM/LE/LKE, czy — jak Oskar —
   wszystkie rozgrywki z kursami (zob. §10 pkt 3).
3. Bukmacher docelowy do pomiaru: na razie Bet365/Betano z feedu jako
   zastępstwo, polskie kursy po pierwszym miesiącu.
4. Rytm skanów: co godzinę + co 15 min w ostatnich 2 h przed meczem
   (propozycja; kolektor Oskara już tak działa).

## 10. Sprzeczności w koncepcie do rozstrzygnięcia

1. **Single 1,30–2,00 przy τ 0,88 nie przejdą.** Singiel potrzebuje
   przebicia +13,6%, a skaner odrzuca przewagi > 10% jako podejrzane. Single
   mają sens tylko przy τ 0,94/1,00 (Betclic do 1 000 zł). Propozycja: single
   liczyć i zapisywać tylko dla reżimu Betclic, kupony dla τ 0,88.
2. **Okno dla kuponów jest wąskie.** 2 nogi z EV +5% wymagają po ~9–10%
   przebicia na nogę przy kursach nóg ~1,2–1,4, gdzie odchylenia są
   najmniejsze. Realistyczny cel pierwszego miesiąca to zmierzyć, jak często
   takie nogi w ogóle istnieją, nie zysk.
3. **Liczba lig działa przeciw strategii.** Value betów jest mało, a w
   topowych ligach rynek jest najbardziej efektywny; Oskar celowo skanuje
   wszystkie rozgrywki z kursami (89 z modelami). Ograniczenie do top 10 +
   puchary zmniejsza liczbę okazji. Propozycja: skanować wszystko, a „top 10”
   trzymać jako filtr na stronie.
4. **Próg promocji 200 kuponów jest za słaby.** Przy kursie ~1,7 i EV +5%
   200 zakładów daje przedział ±10%; dolna granica > 0 wymaga ok. 800. Przy
   0 kuponach na przebieg przy τ 0,88 to miesiące. Szybszym wskaźnikiem jest
   CLV (mediana, odsetek pobitych zamknięć) po 100–200 nogach.
5. **Półka B stoi na słabym fundamencie.** Model, który przegrywa z
   Pinnacle na 1X2 i golach, na rynkach niszowych może generować fałszywe
   przewagi. Test papierowy jest uczciwy, ale oczekiwania niskie; półka B
   nigdy nie wchodzi do kuponów półki A (tak jak `include_model_only` u
   Oskara).
