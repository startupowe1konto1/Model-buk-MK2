# Plan prac: model i skaner value betów

Wersja z 28.09.2026. Plan opisuje, co robimy w najbliższych dniach i w jakiej
kolejności, jakie testy przechodzi każdy krok i po czym poznać, że krok jest
skończony. Tło i liczby: raport
[Weryfikacja modeli i value bettingu](../reports/Weryfikacja%20modeli%20i%20value%20bettingu.md)
i wyniki projektu w [MODEL-BUK-1-STAGE-RESULTS.md](MODEL-BUK-1-STAGE-RESULTS.md).

## Cel

System co godzinę przegląda mecze i wskazuje kilka kuponów (single albo AKO),
które u konkretnego legalnego polskiego bukmachera mają dodatnią wartość
oczekiwaną po podatku. Wybiera kupony z najwyższą dolną granicą EV, a nie z
najwyższą szansą trafienia. Może nie wskazać niczego.

Pula kandydatów (ustalone 29.09.2026): value betów jest mało, więc skaner
musi mieć **jak najwięcej meczów i kursów** do przejrzenia:

- wszystkie mecze, które jeszcze się nie zaczęły i mają wystawione kursy: dziś
  i kolejne dni, dopóki bukmacherzy publikują ceny (kolektor już teraz co 6
  godzin zbiera kursy do 6 dni naprzód, a co godzinę na dziś i 2 kolejne dni);
- wszystkie rozgrywki, dla których mamy model (od 30.09: 89), i dokładanie
  kolejnych, gdy dane pozwolą;
- wszystkie rynki, dla których mamy model (1X2, gole, BTTS, handicap, rożne,
  kartki, strzały), i wszyscy bukmacherzy z feedu;
- skan wielokrotnie w ciągu dnia (częstotliwość ustalimy w testach), bo kursy
  i składy się zmieniają; kursy na dalsze dni są mniej dopracowane przez
  bukmacherów, co może dawać więcej okazji, ale też więcej ruchu ceny do
  meczu.

Bez sztucznych limitów liczby meczów. Wynik to lista posortowana po dolnej
granicy EV, pogrupowana po dniu meczu, z dzisiejszymi na górze. Dziś
`paper_scan` patrzy sztywno na dziś i jutro (`day_offsets=(0, 1)`, maks. 20
meczów, 16 lig), więc ta reguła wchodzi przy budowie skanera produkcyjnego.

Progi, które wyznaczają cały projekt (τ = 0,88, czyli podatek 12% od stawki):
singiel wychodzi na zero przy kursie o 13,6% wyższym od uczciwego, AKO z 3 nóg
przy 4,4% przewagi na nogę, z 5 nóg przy 2,6%. Literatura dokumentuje przewagi
3–10% przed podatkiem, więc realną drogą są AKO z nóg value i promocje bez
podatku, a nie single.

## Co mamy

| Dane | Zakres | Stan |
|---|---|---|
| Mecze z API-Football: wyniki, statystyki drużyn, zdarzenia, składy, statystyki zawodników | 47 lig, 2015–2026, ok. 134 tys. meczów | gotowe, tabele w `Documents\Model-Buk-Data\api-football\tables\history.sqlite`; bieżący sezon dociągany co godzinę (`\ModelBuk\RefreshRecentMatches`) i przy logowaniu (`RefreshCurrentSeason`) |
| Kontuzje i absencje (wstecz: kto nie zagrał), trenerzy, transfery, przerwy zawodników | kontuzje 2021–2026 (135 tys.), transfery 374 tys. ruchów, przerwy 45,6 tys. zawodników od 2023 | gotowe, `api-football-extras\tables\extras.sqlite`; kontuzje historyczne **przeciekają** jako cecha, bo nie wiadomo, kiedy stały się znane |
| Kontuzje, składy i predykcje API ze znacznikiem czasu przed meczem | od 29.09.2026 | kolektor co 20 minut, `\ModelBuk\PrematchCollector`; to jest uczciwe źródło cech „na dzień meczu” |
| Kursy historyczne z football-data (Pinnacle, Bet365, średnia i maksimum rynku): zdjęcie kilka dni przed meczem i zamknięcie | 20 lig z top 20 i 8 z drugiej fali, 2012–2026 | połączone 1:1 z meczami API-Football: 99,92% meczów top 20 z lat 2015–2025, `joined\fd_af_matches.sqlite` |
| Kursy bieżące z czasem pobrania, wszystkie ligi i rynki, ok. 9 bukmacherów (w tym Pinnacle i Betfair) | od 28.09.2026 | kolektor co godzinę, zadanie `\ModelBuk\OddsCollector` |
| Kursy polskich bukmacherów | brak | historia: do kupienia tylko od grudnia 2025 (tanio) albo od 2023 (drogo), patrz niżej; bieżące: feed do decyzji (odds-api.io) |

Budżet API-Football (7 500 zapytań dziennie): kursy ok. 600, wyniki ok. 150,
kolektor przedmeczowy ok. 300–450. Zostaje ok. 6 000 dziennie na dalsze
uzupełnienia; każdy skrypt zatrzymuje się przy rezerwie 1 500.

## Postęp (29.09.2026)

| Krok | Stan | Gdzie |
|---|---|---|
| Dzień 1.1: łączenie football-data z API-Football | gotowe: 99,92% top 20, niepołączone z przyczyną | `ingestion/fd_af_join.py`, raport `joined\unmatched_report.md` |
| Dzień 1.2: matematyka decyzji | gotowe: Kelly z podatkiem, próg PIT, τ per bukmacher, CLV na wziętym kursie, jeden typ na mecz, bramka EV z niepewnością, AKO | `markets/pricing.py`, `markets/staking.py`, `decision.py`; stare wywołania (`current_pipeline`, `historical_replay`, `backtest/gate.py`) jeszcze na dawnych ustawieniach |
| Dzień 2: rama ewaluacji | gotowe; baseline 1X2: log-loss 1,0742 na 68,8 tys. meczów (2021–2025) | `evaluation/framework.py`, raport `reports/Rama ewaluacji - baseline.md` |
| Stały protokół porównania modeli | gotowe: sezony testowe 2022–2025, rynki 1X2, gole 1,5/2,5/3,5, BTTS | `evaluation/protocol.py` |
| Dni 3–5 i 5–7 | w toku | modele, porównanie, model kontra rynek, rożne/kartki/strzały |
| Rozszerzenie zakresu: 26 męskich lig spoza Europy i 16 pucharów klubowych (LM, LE, LKE, Libertadores, Sudamericana, krajowe puchary) | pobieranie od nocy 30.09 (`\ModelBuk\NightlyHistoryBuild`, 02:15, do 5 000 zapytań na noc), potem co noc przebudowa tabel i połączenia z football-data | `api_football_history.py` (`WAVE3_LEAGUES`, `CUP_COMPETITIONS`); kursy Pinnacle dla USA, Brazylii, Argentyny, Meksyku, Japonii i Chin są już w plikach football-data. Protokół porównania modeli zostaje na 47 ligach europejskich |

Kursy historyczne istnieją tylko dla trzech rynków:

| Rynek | Kursy historyczne | Ligi z top 20 |
|---|---|---:|
| 1X2 | tak | 20 |
| Gole powyżej/poniżej 2,5 | tak | 16 |
| Handicap azjatycki | tak | 16 |
| Rożne, kartki, strzały, BTTS, linie goli 1,5 i 3,5, gole drużyn, połowy | nie | 0 |

## Trzy pytania testowe i czym na nie odpowiadamy

| Pytanie | 1X2, gole 2,5, handicap | Rożne, kartki, strzały, BTTS, inne linie |
|---|---|---|
| 1. Czy model dobrze przewiduje? | historia meczów, od razu | historia meczów, od razu |
| 2. Czy model wie więcej niż rynek? | kursy football-data, od razu | kursy z kolektora, po kilku tygodniach |
| 3. Czy zarabia u polskiego bukmachera po podatku? | przybliżenie (Pinnacle + zmierzona marża polska), pewna odpowiedź dopiero na żywo | tylko na żywo |

Pytanie 1 to filtr, nie dowód. Dotychczasowe modele były przyzwoicie
skalibrowane, a mimo to przegrywały z rynkiem. O sensie całego projektu
rozstrzyga pytanie 2, a o pieniądzach pytanie 3.

## Najbliższe dni

Pracuję w sesjach, które otwierasz. Między sesjami działają tylko kolektory w
Harmonogramie zadań (folder `\ModelBuk\`: kursy, wyniki, dane przedmeczowe).
Komputer nie jest wybudzany, więc w czasie uśpienia dane przepadają. „Dzień” oznacza tu jeden blok pracy. Pracę
implementacyjną zlecam subagentom na tańszym modelu (Sonnet), a sam przeglądam
wyniki i diff przed każdym commitem.

### Dzień 1: łączenie danych i matematyka decyzji

1. Połączenie meczów football-data z meczami API-Football: po dacie (±1 dzień)
   i znormalizowanych nazwach drużyn, z tabelą mapowania nazw per liga.
   - Testy: znane pary meczów łączą się poprawnie, mecz bez pary trafia do
     raportu niepołączonych, żaden mecz nie łączy się dwa razy, wynik z obu
     źródeł musi się zgadzać (rozbieżność zatrzymuje łączenie).
   - Gotowe, gdy: co najmniej 99% meczów top 20 z football-data ma parę, a
     niepołączone są wypisane z przyczyną.
2. Poprawki matematyki decyzji wskazane w raporcie:
   - Kelly z podatkiem (`b = τ · kurs − 1`) i ułamkowy Kelly na `p_lower`;
   - próg PIT 2 280 zł (limit stawki albo τ = 0,792 powyżej progu);
   - τ osobno dla każdego bukmachera (Betclic 1,00 / 0,94);
   - CLV liczony na kursie faktycznie wziętym, surowy i po podatku;
   - jeden typ na mecz;
   - bramka EV z niepewnością zamiast stałego odcięcia 5 punktów procentowych;
   - EV kuponu AKO z iloczynu nóg.
   - Testy: MB-0001–MB-0004 dają te same EV co w notatkach, Kelly dla MB-0002
     wynosi 0, wygrana powyżej progu przełącza τ, EV AKO z trzech nóg po +5%
     przy τ = 0,88 wynosi +1,9%.

### Dzień 2: wspólna rama ewaluacji

Jedna rama do oceny wszystkich modeli i rynków, żeby porównania były uczciwe:

- zagnieżdżony walk-forward po sezonach, z tą samą kadencją dopasowania dla
  wszystkich modeli (przed każdą kolejką);
- metryki: log-loss (główna), Brier, RPS pomocniczo, diagramy niezawodności
  CORP z rozkładem na miskalibrację i dyskryminację;
- test Diebolda–Mariano względem baseline, korekta Holma lub
  Benjaminiego–Hochberga na liczbę lig, bootstrap klastrowany po kolejce;
- baseline: częstości ligowe.

Testy: rama odtwarza znany wynik na danych syntetycznych, a test przecieku
(przetasowanie przyszłych wyników nie zmienia wcześniejszych prognoz) przechodzi
próbę mutacji, czyli celowo zepsuty kod musi go wywalić.

### Dni 3–4: modele 1X2 i goli, pytanie 1

Na 130 tys. meczów z 47 lig: Dixon–Coles przeliczany przed każdą kolejką z
siatką okresu półtrwania 180–1 200 dni, NB2 z ważeniem czasu i priorem dla
beniaminków, GAS-Poisson z estymacją największej wiarygodności, Elo–Davidson,
pi-ratingi z gradient boostingiem. Każdy model daje prawdopodobieństwa 1X2,
goli 1,5 / 2,5 / 3,5 i BTTS.

Gotowe, gdy: jest tabela modeli per rynek i liga z log-lossem i przedziałami
ufności po korekcie, a najlepszy model dla każdego rynku jest wybrany wyłącznie
na wewnętrznych foldach.

### Dni 4–5: model kontra rynek, pytanie 2 dla 1X2, goli 2,5 i handicapu

Na połączonych danych z dnia 1:

- log-loss modelu i zamknięcia Pinnacle na tych samych meczach, per liga;
- pooling modelu z rynkiem (waga uczona w wewnętrznych foldach) i model
  residualny;
- trzy strategie: czysta referencja (cena bukmachera wobec odmarżowanego
  Pinnacle), referencja z wetem modelu, pooling;
- single i symulowane AKO z 2–5 nóg: nogi z różnych meczów, dostępne w tym
  samym czasie, u jednego bukmachera;
- trzy reżimy podatku (0,88 / 0,94 / 1,00), płaska stawka i ¼ Kelly'ego;
- wyniki: yield po podatku z przedziałem ufności, CLV, obsunięcie, liczba
  kuponów, a każdy wniosek sprawdzony przy wszystkich metodach de-vig i z
  korektą na liczbę testowanych wariantów.

Kursy „przed meczem” z football-data to zdjęcie z piątku lub wtorku. Służą do
decyzji, a zamknięcie wyłącznie do CLV. Polskie kursy przybliżamy jako cenę
Pinnacle pomniejszoną o marżę polskiego bukmachera zmierzoną na bieżąco. Wynik
dla polskiego bukmachera jest więc szacunkiem, nie pomiarem.

Gotowe, gdy: raport mówi dla każdej strategii i reżimu podatku, czy dolna
granica przedziału ufności CLV po podatku jest dodatnia.

### Dni 5–7: rożne, kartki i strzały, pytanie 1

Modele liczbowe (Poisson, NB2) dla rożnych, kartek, strzałów i strzałów
celnych, łącznie i per drużyna. Cechy: forma z 5 i 10 meczów, strzały, xG (od
2022), sędzia, odpoczynek, podróż. Tylko ligi i sezony, w których raport jakości
pokazuje pełne dane (np. bez Szkocji 2015–2018).

Gotowe, gdy: jest kalibracja per liga i linia (np. rożne 8,5 / 9,5 / 10,5) i
model gotowy do porównania z kursami, które zbiera kolektor.

### Dni 7–8: składy i zawodnicy

Ratingi zawodników z danych o grze w meczach i sprawdzenie, o ile znajomość
składu (publikowanego ok. 60–75 minut przed meczem) poprawia log-loss 1X2 i
goli. To jedyna rodzina modeli, która w badaniach wnosiła informację spoza
rynku. Jeśli poprawa będzie istotna, kolektor dostanie pobieranie składów
tuż przed meczem.

### Przez cały czas

- Sprawdzanie logu kolektora raz na sesję. Po powrocie lig z przerwy
  reprezentacyjnej pierwszy pomiar: jak często Bet365, 1xBet, Betano i inni
  odstają od Pinnacle, o ile i jak długo. To przybliżenie tego, czego szukamy u
  polskich bukmacherów.
- Aktualizacja tego planu po każdym zakończonym kroku.

## Decyzje po Twojej stronie

| Decyzja | Termin | Dlaczego |
|---|---|---|
| Przedłużenie API-Football Pro | przed 28.10.2026 | inaczej kolektor staje |
| Feed polskich kursów (np. odds-api.io, £49–99/mies.) | po dniach 4–5 | dopiero wynik testu kontra rynek pokaże, czy warto płacić |
| Zakup historycznych polskich kursów | po dniach 4–5 | przydatny tylko do pytania 3; szczegóły niżej |

### Wycena historycznych kursów polskich bukmacherów

Pełne notatki ze źródłami: [koszt_kursow_historycznych.md](../research_notes/Koszt%20historycznych%20kursow%20PL/koszt_kursow_historycznych.md).

- Żaden dostawca nie ma licencji od samych bukmacherów; wszyscy zbierają kursy
  wyświetlane na ich stronach. Ryzyko prawne leży głównie po stronie dostawcy,
  ale to nie jest opinia prawna.
- **Wariant minimalny, ok. 600–2 100 zł jednorazowo:** OddsPapi (Fortuna PL i
  Superbet PL, pełna historia zmian od stycznia 2026, ok. 50 USD za miesiąc) i
  odds-api.io (kursy zamknięcia od grudnia 2025, plan £99–179 za miesiąc;
  obecność historii STS, Betclic PL, Fortuny PL, LVbet i Betfanu trzeba
  sprawdzić w 7-dniowym okresie zwrotu). Pokrywa ok. 20–30% zakresu trzech
  sezonów.
- **Pełny zakres od marca 2023:** tylko OddsMarket Odds Archive (STS, Fortuna,
  Superbet, iForBET; nie wiadomo, czy wersje polskie). Wycena indywidualna,
  orientacyjnie 19–58 tys. zł. Demo i wycenę zamawiasz Ty.
- **Alternatywa bez zakupu:** starsze sezony przybliżamy kursami Pinnacle,
  Bet365 i średnią rynku z football-data, a relację polskich kursów do tej
  referencji kalibrujemy na okresie od grudnia 2025.
- Livesport (właściciel BetExplorera i OddsPortal) nie ma publicznej oferty
  danych; jedyna droga to zapytanie o licencję na info@livesport.eu.

Rekomendacja: nic nie kupować przed wynikiem dni 4–5. Jeśli test kontra rynek
da sygnał, najpierw wariant minimalny razem z bieżącym feedem odds-api.io
(ten sam plan daje oba), a OddsMarket tylko wtedy, gdy potrzebny będzie
backtest na dłuższej historii polskich cen.

## Kryterium po pierwszych dniach

- Jeśli w dniach 4–5 jakakolwiek strategia (single albo AKO, w jakimkolwiek
  reżimie podatku) ma dodatnią dolną granicę CLV po podatku poza próbą,
  kupujemy feed polskich kursów i przechodzimy do fazy bez stawiania.
- Jeśli żadna, ale modele rożnych i kartek są dobrze skalibrowane, czekamy na
  kilka tygodni kursów z kolektora i powtarzamy test dla tych rynków.
- Jeśli nic nie działa, skaner zostaje narzędziem pomiarowym, a zakłady
  ograniczają się do reżimów bez podatku, jeśli dadzą dodatni CLV.

## Później: od testów do pieniędzy

1. **Faza bez stawiania (4–8 tygodni).** Skaner działa na żywo i zapisuje
   kandydatów, ale nic nie rekomenduje. Mierzy częstość luk względem progów
   (13,6% / 6,4% dla singli, 6,6% / 4,4% / 2,6% na nogę AKO), ich trwałość i
   opóźnienie polskich bukmacherów za Pinnacle. Przejście dalej przy ok. 20
   singlach albo 10 kuponach AKO tygodniowo, które żyją dłużej niż 5 minut.
2. **Faza papierowa (6–8 tygodni albo 150 kuponów).** Zamrożona konfiguracja,
   płaska stawka, niezmienny rejestr z CLV. Stop po 100 kuponach, jeśli mediana
   CLV ≤ 0 albo pobitych zamknięć ≤ 50%; stop po 250, jeśli górna granica CLV
   po podatku jest poniżej zera.
3. **Pilot z pieniędzmi.** Tylko przy dodatniej dolnej granicy CLV po podatku
   na co najmniej 150 kuponach. Małe stawki, limit straty, rejestr limitów kont.
4. **Produkcja.** Przegląd dnia o 8:00, skan co godzinę, a w ostatnich 2
   godzinach przed meczem co 10–15 minut. Rekomendacja tylko przy kursach
   młodszych niż 10 minut. Powiadomienia tylko dla bukmacherów, u których masz
   konto.

## Skaner v1 i v1.1 (od 30.09.2026)

Wynik testu kontra rynek (`reports/Model kontra rynek - pytanie 2.md`):
model wie mniej niż Pinnacle, więc skaner opiera uczciwą cenę na ostrym
rynku, a modele służą jako weto i źródło tylko tam, gdzie ostrej ceny nie
ma. Zarabia się na odchyleniu ceny bukmachera od uczciwej, nie na „dużej
szansie”.

- **v1 (w budowie):** każdy przebieg przegląda wszystkie nadchodzące mecze z
  kursami; uczciwa cena z Pinnacle (albo Betfair lub konsensusu),
  rynki pochodne z macierzy wyników dopasowanej do Pinnacle; nogi z
  wartością dla każdego bukmachera i reżimu podatku; kupony 2–3 nóg (różne
  mecze, jeden bukmacher, dodatnie EV po podatku, szansa trafienia ≥ 30%);
  rejestr papierowy, rozliczanie, CLV na prawdziwym zamknięciu (kursy co
  15 minut przed meczem), strona HTML. Opis: `docs/SKANER.md`.
- **v1.1 (uzgodnione 30.09):** dołożyć rynki wyliczalne z macierzy wyników:
  parzyste/nieparzyste, handicap europejski, wygrana do zera, czyste konto;
  oraz z modelu podziału goli na połowy: do przerwy/koniec meczu, wygra obie
  połowy, gol w obu połowach, pierwsza/ostatnia bramka; kartki w 1. połowie
  (rozliczane z minut zdarzeń). Poza zakresem: rożne w 1. połowie (API nie
  podaje ich za połowę), wyścigi do X rożnych, zakłady na zawodników.
  Wszystkie kursy i tak są archiwizowane, więc te rynki da się wycenić
  później także wstecz.

## Pomysł Oskara: najlepsze kursy na zdarzeniach o wysokim prawdopodobieństwie (1.10.2026)

Alternatywa dla szukania przewagi nad rynkiem: zamiast „bić rynek” wybierać
zdarzenia o **wysokim prawdopodobieństwie** (np. szansa ≥ 60–75%), brać na nie
**najlepszy dostępny kurs** u polskich bukmacherów i łączyć 2–3 takie
zdarzenia w kupon, który ma dużą szansę wejść (zgodnie z limitem: noga do
2,00, kupon do 3,00).

Uczciwy rachunek, żeby wiedzieć, co sprawdzamy: jeśli najlepszy kurs nie
przebija uczciwej ceny, oczekiwany wynik kuponu to mniej więcej marża
bukmachera razy liczba nóg plus 12% podatku, więc kupony będą często wchodzić,
ale w długim okresie tracić. Przykład: 3 nogi po uczciwej cenie 1,40 (szansa
71%) dają kupon 2,74 z szansą ok. 36%; przy najlepszym kursie 1,43 na każdej
nodze wynik po podatku to ok. −8%, bez podatku ok. +4,5%. Pomysł staje się
opłacalny, gdy najlepszy kurs jest wyraźnie powyżej uczciwego (to już jest
„wartość”) albo w promocjach bez podatku.

Jak to sprawdzimy bez ryzyka: skaner doda osobną półkę „najlepszy kurs, wysoka
szansa” (zdarzenia z szansą powyżej progu, najlepszy kurs spośród
bukmacherów, kupony 2–3 nóg) i zapisze ją w rejestrze papierowym obok kuponów
z wartością. Po kilku tygodniach raport pokaże dla obu półek: odsetek
trafionych kuponów, wynik po podatku i bez podatku oraz CLV. Do tego potrzebne
są prawdziwe kursy polskich bukmacherów (feed).

## Na później: sygnały przepływu pieniędzy (pomysł z 29.09.2026)

Idea: wyłapać moment, w którym duże („ostre”) pieniądze przesuwają kurs,
zanim polski bukmacher go poprawi. Przepływ pieniędzy jest już zawarty w
cenie Pinnacle i Betfair, więc zarobić da się tylko na **opóźnieniu**
miękkiego bukmachera. To rozwinięcie strategii „czysta referencja” z dni 4–5
o dynamikę ruchu kursu. Wracamy do tego po wyniku testu kontra rynek i po
kilku tygodniach danych z kolektora.

| Źródło | Co daje | Stan i przeszkody |
|---|---|---|
| Własny detektor spadków kursów | Ruch kursu Pinnacle i Betfair między zdjęciami z naszego kolektora (9 bukmacherów, od 28.09) przy stojącym kursie miękkiego bukmachera | Darmowe, dane już się zbierają. Kolektor co godzinę gubi szybsze ruchy; w ostatnich godzinach przed meczem trzeba częściej (i tak jest w planie produkcji). Test: czy taki ruch przewiduje dalszy ruch do zamknięcia i wynik lepiej niż sama cena |
| Giełda Betfair: obrót na wynik | Prawdziwy wolumen stawek (ile pieniędzy na każdy wynik) | Wymaga konta Betfair i klucza API. Betfair nie ma polskiej licencji, więc najpierw sprawdzenie prawne (art. 107 KKS); zasada planu: Betfair tylko jako źródło cen. Dane historyczne Betfair są płatne |
| OddsPortal „Dropping Odds” | Lista kursów, które mocno spadły u wielu bukmacherów | Brak API; właściciel (Livesport) nie ma publicznej oferty danych, a scraping łamie regulamin. Najwyżej ręczne porównanie z naszym detektorem |
| Skanery komercyjne: RebelBetting, BetBurger, Breaking-Bet, OddsJam, TxOdds | Gotowe porównanie cen miękkich z ostrymi, „sharp money”, arbitraże | Płatne; zwykle bez polskich bukmacherów i bez podatku 12%. Przydatne jako punkt odniesienia; opis konkurencji w `research_notes/Weryfikacja modeli i value bettingu/analiza_serwisow_konkurencji.md` |

Kolejność: najpierw własny detektor (koszt zero), potem decyzja o Betfair
po opinii prawnej, a skanery komercyjne tylko jako porównanie.

## Stałe zasady

- Decyzja używa tylko danych znanych przed chwilą decyzji; zamknięcie służy
  wyłącznie do CLV.
- Archiwa są niezmienne; każda zmiana konfiguracji to nowy `config_id` i wyniki
  liczone od zera.
- Brak wartości zostaje brakiem, nigdy zerem.
- Pinnacle i Betfair są tylko źródłem cen; system nigdy nie rekomenduje tam
  zakładu (art. 107 KKS).
- Przed każdym commitem czytany jest diff, a pełny zestaw testów musi przejść
  (dziś 657 w `app/` i 130 w katalogu głównym).
