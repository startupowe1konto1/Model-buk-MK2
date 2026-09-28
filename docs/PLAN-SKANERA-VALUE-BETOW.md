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

Progi, które wyznaczają cały projekt (τ = 0,88, czyli podatek 12% od stawki):
singiel wychodzi na zero przy kursie o 13,6% wyższym od uczciwego, AKO z 3 nóg
przy 4,4% przewagi na nogę, z 5 nóg przy 2,6%. Literatura dokumentuje przewagi
3–10% przed podatkiem, więc realną drogą są AKO z nóg value i promocje bez
podatku, a nie single.

## Co mamy

| Dane | Zakres | Stan |
|---|---|---|
| Mecze z API-Football: wyniki, statystyki drużyn, zdarzenia, składy, statystyki zawodników | 47 lig, 2015–2025, 130 474 mecze | gotowe, tabele w `Documents\Model-Buk-Data\api-football\tables\history.sqlite` |
| Kursy historyczne z football-data (Pinnacle, Bet365, średnia i maksimum rynku): zdjęcie kilka dni przed meczem i zamknięcie | 20 lig z top 20 i 8 z drugiej fali, 2012–2026 | na dysku: `Desktop\Model Buk\football_data-*.zip` |
| Kursy bieżące z czasem pobrania, wszystkie ligi i rynki, ok. 9 bukmacherów (w tym Pinnacle i Betfair) | od 28.09.2026 | kolektor co godzinę, zadanie `\ModelBuk\OddsCollector` |
| Kursy polskich bukmacherów | brak | historia: wycena w toku; bieżące: feed do decyzji (odds-api.io) |

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

Pracuję w sesjach, które otwierasz. Między sesjami działa tylko kolektor kursów
w Harmonogramie zadań. „Dzień” oznacza tu jeden blok pracy. Pracę
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
- Uzupełnianie planu o wynik wyceny polskich kursów historycznych.

## Decyzje po Twojej stronie

| Decyzja | Termin | Dlaczego |
|---|---|---|
| Przedłużenie API-Football Pro | przed 28.10.2026 | inaczej kolektor staje |
| Feed polskich kursów (np. odds-api.io, £49–99/mies.) | po dniach 4–5 | dopiero wynik testu kontra rynek pokaże, czy warto płacić |
| Zakup historycznych polskich kursów | po wycenie | przydatny tylko do pytania 3 |

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

## Stałe zasady

- Decyzja używa tylko danych znanych przed chwilą decyzji; zamknięcie służy
  wyłącznie do CLV.
- Archiwa są niezmienne; każda zmiana konfiguracji to nowy `config_id` i wyniki
  liczone od zera.
- Brak wartości zostaje brakiem, nigdy zerem.
- Pinnacle i Betfair są tylko źródłem cen; system nigdy nie rekomenduje tam
  zakładu (art. 107 KKS).
- Przed każdym commitem czytany jest diff, a pełny zestaw testów musi przejść
  (dziś 346).
