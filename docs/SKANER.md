# Skaner wartości (tryb papierowy)

Wersja 1.1 z 01.10.2026: nowe rynki (parzyste/nieparzyste, handicap
europejski, wygrana do zera, do przerwy / koniec, obie połowy, połowa z większą
liczbą goli, pierwszy i ostatni gol, żółte kartki w 1. połowie) i model podziału
goli na połowy; opis w „Rynki od wersji 1.1”. Wersja 1.0.1 z 30.09.2026 dodała
klasy rynków, limit podejrzanych przewag 10%, zgodność czasu kursów, wykluczanie
odbiegających rodzajów zakładów, unieważnianie meczów odwołanych i nową stronę.
Skaner co przebieg przegląda **wszystkie** nadchodzące
mecze, dla których w archiwum są kursy (dziś i kolejne dni, wszystkie ligi,
wszystkie rynki, wszyscy bukmacherzy, bez limitu liczby meczów), szuka kursów
wyższych od uczciwej ceny i składa z nich kupony 2–3 nóg. Nic nie jest
obstawiane: każda propozycja trafia do rejestru papierowego, a po meczu jest
rozliczana i porównywana z kursem zamknięcia, żeby po kilku tygodniach ocenić,
czy przewaga jest prawdziwa.

Kod: `app/src/model_buk/scanner/` (przebieg: `pipeline.py`, progi:
`config.py`), uruchomienie: `app/scripts/run_scanner.py` albo
`app/scripts/run_scanner.ps1`.

## Co robi jeden przebieg

1. **Kalendarz.** Odświeża katalog meczów z API-Football (wszystkie ligi
   naraz, jedno zapytanie na dzień): dziś przy każdym przebiegu, pozostałe dni
   okna (3 dni wstecz, 7 w przód) tylko gdy ich lista jest nieaktualna. Zwykle
   1–3 zapytania. Z katalogu biorą się nazwy drużyn i lig, a po meczu wyniki.
2. **Rodzaje zakładów.** Raz na dobę mierzy na archiwum kursów, które rodzaje
   zakładów bukmacherów (np. „Bet365, Clean Sheet”) systematycznie odbiegają od
   Pinnacle, i wyklucza je (opis niżej). Pomiar trwa 20–90 sekund i jest
   zapisywany w rejestrze; kolejne przebiegi tego dnia go tylko czytają.
3. **Kursy.** Wczytuje ostatni kurs każdego bukmachera dla każdego zakładu
   każdego meczu, który się jeszcze nie zaczął (bez wykluczonych rodzajów
   zakładów). Tylko kursy widziane przed chwilą decyzji.
4. **Uczciwe ceny.** Dla każdego zakładu liczy uczciwą szansę (opis niżej).
5. **Klasy rynków.** Każdy mecz dostaje klasę: *płynny* albo *słaby* (opis
   niżej). Do głównych sekcji strony trafiają tylko rynki płynne.
6. **Modele.** Nasze modele statystyczne (gole: pula modeli; rożne i kartki:
   model liczebności) liczą swoje szanse dla meczów lig, które modelujemy. Modele
   są trenowane raz na dobę (`fit_live_models.py fit`); jeśli nie ma treningu
   albo jest starszy niż doba, skaner działa bez modeli i zapisuje ostrzeżenie.
7. **Wartość i kupony.** Porównuje każdy kurs z uczciwą ceną, wybiera nogi z
   wartością i składa z nich kupony, osobno dla rynków płynnych i słabych.
8. **Rejestr.** Zapisuje przebieg do rejestru papierowego (jeden `scan_id`).
9. **Rozliczenie i CLV.** Unieważnia nogi meczów odwołanych i przełożonych,
   rozlicza nogi z wcześniejszych przebiegów, których
   mecze się skończyły (wynik z katalogu meczów; do zakładów na rożne i kartki
   dociąga statystyki meczu z API, z limitem zapytań), i zapisuje CLV, czyli
   porównanie wziętego kursu z ceną zamknięcia (ostatni kurs przed meczem).
10. **Wyniki.** Zapisuje stronę HTML, pliki CSV i jedną linię dziennika.

Przebieg trwa około 45 sekund (raz na dobę około minuty dłużej: pomiar rodzajów
zakładów). Jeśli nie ma internetu, klucza API albo limit
zapytań jest wyczerpany, skaner i tak działa na tym, co już jest w archiwach,
i zapisuje ostrzeżenie.

## Uczciwa cena

Uczciwa szansa (`p_fair`) to szansa zdarzenia bez marży bukmachera. Źródła,
w kolejności (bierzemy pierwsze dostępne):

1. **Pinnacle** po odjęciu marży (metoda potęgowa). Pinnacle przyjmuje duże
   stawki i nie wyrzuca wygrywających graczy, więc jego cena jest najlepszym
   dostępnym oszacowaniem prawdy. Test z 30.09 (`reports/Model kontra rynek -
   pytanie 2.md`) pokazał, że nasze modele są od niego gorsze.
2. **Pochodne z Pinnacle**: podwójna szansa i „remis – zwrot” liczone wprost z
   1X2 Pinnacle.
3. **Konsensus**: mediana co najmniej 3 innych bukmacherów po odjęciu marży.
4. **Macierz wyników dopasowana do Pinnacle**: rynki, których Pinnacle nie
   wystawia (np. obie drużyny strzelą, dokładny wynik, druga połowa,
   parzyste/nieparzyste, handicap europejski), liczone z rozkładu wyników
   dopasowanego do wszystkich jego kursów tego meczu, a rynki zależne od goli w
   każdej połowie z **podziału tej macierzy na połowy** (opis niżej).
5. **Konsensus na skrajnych szansach** (poniżej 15% lub powyżej 85%).
6. **Betfair** jako ostateczność (w tym źródle danych to zwykły bukmacher, nie
   giełda).

Każda uczciwa szansa ma swój błąd standardowy (`p_se`): im dalej do meczu, im
słabsze źródło i im mniej spójna drabinka linii, tym większy.

Żółte kartki w 1. połowie mają tylko dwa możliwe źródła ceny: Pinnacle albo
konsensus co najmniej 3 bukmacherów. Betfair sam nie wystarczy, model też nie.
Bez takiego źródła kurs zostaje niewyceniony i jest liczony osobno w dzienniku
przebiegu (`markets_v11.unpriced`) i na stronie („Co pominięto”).

## Rynki od wersji 1.1

### Co doszło

| Rynek (w kodzie) | Rodzaje zakładów API | Uczciwa cena |
|---|---|---|
| parzysta / nieparzysta liczba goli (`odd_even`, drużyn: `odd_even_home`, `odd_even_away`) | 21, 1. połowa 22, 2. połowa 63, drużyny 23 i 60 | macierz (mecz), podział na połowy (połowy) |
| handicap europejski, 3 wyniki (`eh`) | 9, 1. połowa 18, 2. połowa 181 | jw. |
| wygrana do zera (`win_to_nil_home`, `win_to_nil_away`) | 29, 30, 36 | macierz |
| do przerwy / koniec meczu (`ht_ft`, 9 wyników) | 7 | podział na połowy |
| wygra obie połowy / co najmniej jedną (`win_both_halves_*`, `win_either_half_*`) | 37, 53, 32, 39 | jw. |
| gol w obu połowach (`score_both_halves`, drużyny `_home`, `_away`) | 184, 111, 112, 48 | jw. |
| obie strzelą w obu połowach (`btts_both_halves`) | 113 | jw. |
| połowa z większą liczbą goli (`highest_half`, drużyn `_home`, `_away`) | 11, 192, 193 | jw. |
| pierwszy gol (`first_goal`, także w 1. połowie) i ostatni gol (`last_goal`) | 14, 185, 15 | jw. |
| żółte kartki w 1. połowie (`yellow_ou`, drużyn `yellow_home_ou`, `yellow_away_ou`) | 155, 309, 310 | tylko Pinnacle albo konsensus 3 bukmacherów |

Kolejność źródeł jak w pozostałych rynkach: Pinnacle nie wystawia żadnego z nich,
więc pierwszeństwo ma konsensus co najmniej 3 bukmacherów, a model (macierz albo
podział na połowy) wycenia resztę i zestawy z nogą poniżej 15% (np. prawie cały
rynek „do przerwy / koniec” i „nikt nie strzeli”). Parzyste/nieparzyste całego
meczu wycenia więc głównie konsensus.

Rozpoznawanych rodzajów zakładów jest teraz **85 ze 187** w archiwum (było 57) i
dwa z katalogu dostawcy, których w archiwum jeszcze nie było (309, 310). Udział
kursów, które skaner rozumie (tablica z 30.09, 21:30 UTC, 471 meczów):

| Bukmacher | przed | po |
|---|---|---|
| Bet365 | 37,9% | 49,4% |
| Betano | 64,9% | 74,3% |
| Betfair | 71,9% | 80,4% |
| Marathonbet | 72,9% | 93,8% |
| William Hill | 76,6% | 87,5% |
| SBO | 84,5% | 100% |
| 1xBet | 92,9% | 98,3% |
| BetVictor | 98,5% | 100% |
| Pinnacle | 100% | 100% |
| razem | 68,7% | 79,0% |

Na całym archiwum (1,34 mln zmian kursów) z 72,8% na 82,6%.

Czyste konto (27, 28) było już wcześniej mapowane jako „rywal poniżej 0,5
gola” i zostaje tak; nie jest osobnym rynkiem. Dalej nierozpoznane: żółte
kartki całego meczu i 2. połowy (inna statystyka niż „kartki”), dokładna liczba
goli, rynki łączone (wynik i gole, wynik i obie strzelą), remis z
bramkami, margines zwycięstwa, strzelcy, rzuty rożne w przedziałach, minuty.
Katalog dostawcy (`/odds/bets`, 338 rodzajów) **nie ma** rynku na wszystkie
kartki w 1. połowie; ma tylko żółte kartki, więc tylko one są mapowane.

Kilka rzeczy sprawdzonych na archiwum (30.09):

- **Handicap europejski** ma tę samą konwencję co azjatycki: „Home -1”,
  „Draw -1”, „Away -1” to trzy wyniki jednej linii, na której gospodarz oddaje
  gola (RMS zgodności z macierzą 0,025 przy tym odczycie, 0,48 przy odwrotnym).
  W tych samych dwóch meczach co handicap azjatycki znak jest odwrócony, więc
  oba handicapy bramkowe „głosują” razem przy korekcie znaku.
- **Zakłady z nazwą drużyny w wyborze** („Win To Nil” 36, „Win Both Halves” 32,
  „To Win Either Half” 39, „To Score In Both Halves By Teams” 48: „Home” albo
  „Away”) to noga „tak” rynku tej drużyny. Ich kursy zgadzają się z modelem tak
  samo jak kursy zakładów tak/nie innych bukmacherów (korelacja 0,99). Nie mają
  ceny „nie”, więc same nie dają konsensusu.

### Podział goli na połowy

Rynki zależne od tego, w której połowie padły gole, liczymy z modelu podziału.
Każdy gol gospodarza pada w 1. połowie z prawdopodobieństwem `s_dom`, gościa z
`s_gość`, niezależnie od innych goli. Rozkład wyników całego meczu zostaje
dokładnie ten sam (dopasowany do Pinnacle), a średnie liczby goli w połowach
sumują się do średnich meczu. Udziały `s` bierzemy:

1. z cen Pinnacle na 1. połowę tego meczu (1X2, sumy goli, handicapy, gole
   drużyn), jeśli są: dopasowanie najmniejszych kwadratów ze słabym
   przyciąganiem do udziałów ligi, bo sama suma goli w 1. połowie ustala sumę,
   a nie podział między drużyny (`fair_detail` = `half:pinnacle_1h`; tak było
   dla wszystkich meczów z macierzą 30.09),
2. inaczej z historii ligi: odsetek goli przed przerwą w meczach z ostatnich 4
   lat **przed chwilą decyzji** (`history.sqlite`), ściągnięty do udziału
   ogólnego (empiryczny Bayes, siła ok. 9 800 goli, bo ligi różnią się mało:
   0,39–0,48), `half:league`,
3. w lidze bez historii: udział ogólny (0,449 gospodarze, 0,441 goście),
   `half:global`.

Pierwszy i ostatni gol: przy danej liczbie goli w połowie ich kolejność jest
losowa, więc pierwszy gol połowy z `a` golami gospodarza i `b` gościa należy do
gospodarza z prawdopodobieństwem `a / (a + b)`. „Nikt nie strzeli” to wynik 0:0.

**Kalibracja na historii** (`app/scripts/measure_half_model.py`): 33 096 meczów
18 lig z lat 2021–2025, macierz dopasowana do kursów zamknięcia Pinnacle z
football-data (1X2, powyżej/poniżej 2,5, handicap azjatycki), udziały liczone
tylko z meczów sprzed miesiąca danego meczu. Log-loss (mniej = lepiej) wobec
naiwnego podziału po połowie goli na połowę:

| Rynek | naiwny | udziały ligi | różnica (± błąd) |
|---|---|---|---|
| wynik do przerwy (1X2 1. połowy) | 1,0353 | 1,0344 | −0,0009 ± 0,0003 |
| do przerwy / koniec | 1,8808 | 1,8753 | −0,0055 ± 0,0004 |
| połowa z większą liczbą goli | 1,0876 | 1,0738 | −0,0137 ± 0,0009 |
| gole 1. połowy powyżej 1,5 | 0,6321 | 0,6262 | −0,0059 ± 0,0006 |
| gole 2. połowy powyżej 1,5 | 0,6809 | 0,6752 | −0,0056 ± 0,0006 |
| pierwszy gol w 1. połowie | 1,0588 | 1,0571 | −0,0018 ± 0,0005 |
| wynik do przerwy przy znanym wyniku końcowym | 1,3490 | 1,3342 | −0,0148 ± 0,0010 |

Udział ogólny daje to samo co udziały ligi (różnice poniżej 0,0002), okno 2
lata, 4 lata i cała historia też. Rynki, w których podział prawie nie gra roli
(gol w obu połowach, wygrane połowy, pierwszy i ostatni gol meczu), mają ten sam
log-loss co podział naiwny.

Kalibracja „w dużym” (średnia przewidywana wobec częstości, udziały ligi):
największe odchylenia to powroty w „do przerwy / koniec” (gospodarz/gość
przewidziane 1,8%, było 2,4%; gość/gospodarz 2,2% wobec 2,7%), handicap
europejski gospodarza −1, wynik „remis” czyli wygrana gospodarza jednym golem
(20,1% wobec 21,7%), nieparzysta liczba goli (47,8% wobec 49,7%), mecz bez gola
(8,4% wobec 7,4%) i gol w obu połowach (53,8% wobec 55,4%). Te różnice wchodzą
do błędu ceny.

**Błąd ceny nowych rynków.** Ceny z modelu dostają `fair_source` =
`pinnacle_derived`, ale szerszy `p_se` niż zwykłe nogi z macierzy: podział na
połowy ma dodatkowy składnik 0,05 (macierz 0,045 plus ok. 0,02 na sam podział),
o 0,03 więcej, gdy udziały nie pochodzą z cen Pinnacle na 1. połowę, a każdy
nowy rynek dokłada swój składnik równy największemu odchyleniu kalibracji
(w jednostkach `sqrt(p(1-p))`, zaokrąglonemu w górę): handicap europejski i
do przerwy / koniec 0,045, parzyste/nieparzyste 0,04, pierwszy i ostatni gol
oraz gol w obu połowach 0,035, pozostałe 0,01–0,03. Sześć godzin przed meczem
taka noga potrzebuje przewagi 10–12% przy szansie 0,5 i 8–9% przy 0,7 (noga
Pinnacle 3,5%, zwykła noga z macierzy 9%), a limit podejrzanych przewag to 10%.
Z modelu rzadko więc wyjdzie noga z wartością; to celowe. Na żywej tablicy
(30.09) model zgadza się z konsensusem bukmacherów tak dobrze, jak konsensus
sam ze sobą (np. do przerwy / koniec: różnica 0,026 w tych jednostkach).

Wszystkie pozostałe zasady bez zmian: limit podejrzanych przewag 10%, klasy
rynków (noga wyceniona tylko konsensusem jest „słaba”), wykluczanie rodzajów
zakładów, zgodność czasu tablic.

### Rozliczenie nowych rynków

- Parzyste/nieparzyste, handicap europejski (bez zwrotu: trzy wyniki), wygrana
  do zera: z wyniku okresu.
- Do przerwy / koniec, obie połowy, połowa z większą liczbą goli (remis 0:0
  to „po równo”): z wyniku do przerwy i końcowego; 2. połowa = koniec − przerwa.
- Pierwszy i ostatni gol: ze zdarzeń meczu (gole z pierwszych 90 minut bez
  niestrzelonych karnych, w kolejności minut; gol samobójczy liczy się drużynie,
  którą wskazuje dostawca, czyli tej, która go zyskała: tak sumuje się 99,9%
  meczów w historii). Tylko gdy gole ze zdarzeń sumują się do wyniku; mecz albo
  połowa bez goli to „nikt” bez zdarzeń.
- Żółte kartki w 1. połowie: zdarzenia z minutą (`elapsed`) do 45 włącznie, czyli
  z doliczonym czasem 1. połowy (45+N). Liczą się tylko z pełnego zapisu zdarzeń
  (ze zmianą zawodnika w 90 minutach); przy niepełnym zapisie brak danych, nigdy
  zero. Kartki (żółte i czerwone) w 1. połowie liczymy tak samo ze zdarzeń, a
  gdy ich brak, z połówkowych statystyk dostawcy jak dotąd. W historii 98,7%
  liczb kartek drużyn ze zdarzeń zgadza się z blokiem statystyk.

Zdarzenia (gole, kartki, zmiany) z minutami trafiają do tabeli
`fixture_events` katalogu meczów z tych samych zapytań `/fixtures?ids`, które
już pobierały statystyki (bez dodatkowych zapytań). Przy pierwszym otwarciu
katalogu tabela jest wypełniana z zapisanych surowych odpowiedzi
(`raw/details`). Brakujący wynik do przerwy, kolejność goli albo kartki z
minutami dociągają statystyki do rozliczeń (20 meczów na zapytanie).

## Wartość: kiedy noga jest propozycją

Dla każdego kursu u każdego bukmachera i trzech założeń podatkowych
(τ = 0,88: podatek 12% od stawki, jak w Polsce; τ = 0,94; τ = 1,00: bez
podatku) skaner liczy:

- **EV** (oczekiwany zysk na złotówkę stawki) = τ · p_fair · kurs − 1,
- **dolną granicę EV** = τ · p_dolne · kurs − 1, gdzie
  p_dolne = p_fair − 1,645 · p_se (ostrożne oszacowanie szansy).

**Noga z wartością** to kurs, którego dolna granica EV przy τ = 1,00 jest
dodatnia: kurs bije uczciwą cenę z zapasem na niepewność. Podatek liczymy
dopiero na kuponie, bo płaci się go raz od całej stawki kuponu. Kolumny dla
τ = 0,88 i 0,94 pokazują, czy noga broni się także jako pojedynczy zakład.

Kurs jest **podejrzany** (zapisywany tylko do badań, nigdy proponowany), gdy:

- przewaga nad uczciwą ceną przekracza 10% (`max_edge`; 30.09 wszystkie
  „przewagi” 15–25% okazały się błędem danych, cienkim rynkiem albo starą
  tablicą),
- uczciwa cena jest bardzo niepewna (p_se powyżej 0,05),
- cała para kursów bukmachera leży daleko od ceny rynkowej (przesunięcie o
  ponad 6 punktów procentowych: stara tablica albo źle opisana linia).

**Ważna konsekwencja limitu 10%.** Pojedynczy zakład opłaca się po podatku
12% dopiero przy przewadze co najmniej 13,6% (1 / 0,88). Przewagi powyżej 10%
uznajemy za podejrzane, więc **singiel praktycznie nigdy nie przejdzie przy
pełnym podatku**. Po podatku mogą się opłacać tylko kupony (podatek płaci się
raz, a przewagi nóg się mnożą).

Kurs jest **niezsynchronizowany** (zapisywany, liczony w diagnostyce, nigdy
proponowany), gdy tablica bukmachera i tablica, z której wzięto uczciwą cenę,
pochodzą z różnych chwil dostawcy: ich czasy aktualizacji (`api_update`, a gdy
go brak — czas, w którym tablicę widzieliśmy) różnią się o więcej niż 60 minut
(`sync_window_minutes`). Dostawca nadaje jeden czas aktualizacji całemu meczowi
naraz, więc na bieżącej tablicy różnica wynosi zero; pojawia się tylko wtedy,
gdy Pinnacle zdjął mecz, a jego ostatnia cena jest ze starszej chwili. Kolejne
aktualizacje jednego meczu są oddalone o co najmniej 3,75 godziny (5. percentyl
w archiwum 28–30.09), więc 60 minut oznacza „ta sama chwila dostawcy”. Gdy
któregoś czasu nie ma, kurs nie jest oceniany tą regułą.

Na liniach, gdzie stawka może wrócić (handicapy i sumy całkowite oraz
ćwiartkowe, „remis – zwrot”), EV jest skorygowane o szansę zwrotu.

Z nóg z wartością lista na stronie bierze najlepszą na mecz, bukmachera i
rodzaj rynku (np. jedna noga z drabinki handicapów), posortowane po dolnej
granicy EV, a przy remisie po wyższej szansie.

**Rola modelu.** Model niczego nie zmienia w cenie ani w liście propozycji.
Przy każdej nodze strona pokazuje, czy model się zgadza (jego szansa nie jest
niższa od uczciwej o więcej niż 2 punkty procentowe). Kursy, dla których nie
ma żadnej ceny rynkowej, a jest tylko szansa modelu, tworzą osobną klasę
„tylko model”: są zapisywane do badań i nigdy nie trafiają do kuponów.

## Co znaczą klasy rynków

Cena Pinnacle jest dobrym oszacowaniem prawdy tylko tam, gdzie Pinnacle
przyjmuje duże pieniądze: w głównych ligach mężczyzn i dużych turniejach. W
meczach towarzyskich, kobiecych, młodzieżowych, rezerwach, ligach amatorskich i
egzotycznych rozgrywkach jego cena to zgadywanie z dużą marżą, a bukmacher,
który się z nim nie zgadza, równie dobrze może mieć rację. Dlatego każdy mecz
dostaje klasę:

- **płynny** — cena Pinnacle jest wiarygodna; tylko te mecze trafiają do
  głównych sekcji strony,
- **słaby** — wszystko inne; nogi i kupony są zapisywane i pokazywane osobno,
  jako eksperyment, z osobnymi wynikami papierowymi.

Mecz jest **słaby**, gdy spełnia którąkolwiek z reguł (na stronie widać,
która):

| Reguła | Kiedy |
|---|---|
| kobiety | nazwa ligi (Women, Femenino, Frauen, WSL, Damallsvenskan…) albo drużyny „… W” |
| młodzież | U17–U23, Youth, Primavera, Juniores, Revelação, Premier League 2, Premier League International Cup, Elite League… |
| towarzyski | Friendlies, Friendlies Clubs, Club Friendlies |
| rezerwy / amatorzy | Reserve, Amateur, Regionalliga, Oberliga, Non League, Serie D, Tercera… albo drużyna „… II”, „… B”, „… 2”, „… Res.” |
| brak Pinnacle | Pinnacle nie ma pełnego 1X2 na świeżej tablicy (nie ma żadnej ostrej ceny) |
| wysoka marża Pinnacle | marża 1X2 Pinnacle (suma odwrotności kursów) powyżej 1,08 |
| mało bukmacherów | mniej niż 6 bukmacherów (z Pinnacle i Betfair) wystawia mecz |
| cena bez Pinnacle | pojedyncza noga meczu płynnego, której uczciwa cena nie pochodzi od Pinnacle (konsensus bukmacherów albo Betfair) |

Nazwy biorą się z katalogu meczów (liga, kraj, drużyny). Mecz spoza katalogu
jest oceniany tylko regułami danych.

**Jak wybrano progi** (archiwum kursów 28–30.09, tablice co 6 godzin, 674
mecze): w 89 śledzonych rozgrywkach marża 1X2 Pinnacle ma medianę 1,048–1,062
(zależnie od czasu do meczu) i 95. percentyl 1,065–1,070 dwa dni i więcej przed
meczem, do 1,09 w ostatnich dwóch dniach (Kanada, Peru). W pozostałych
rozgrywkach mediana to 1,068–1,093 (towarzyskie 1,088, angielskie ligi
amatorskie 1,10, izraelska Liga Alef 1,128, kobiecy puchar Europy 1,14). Powyżej
1,08 leży 5% obserwacji śledzonych rozgrywek i 46% pozostałych. Gdy Pinnacle
był na tablicy, każdy mecz śledzonych rozgrywek miał co najmniej 6
bukmacherów (mediana 8 z 11); 4–5 zdarzały się tylko gdzie indziej. Bez 1X2
Pinnacle było 13% obserwacji śledzonych rozgrywek (angielska League One i Two
cztery dni przed meczem, zanim Pinnacle otworzy rynek) i 37% pozostałych (Serie
C, J3, trzecie i czwarte ligi Szwecji i Hiszpanii); taki mecz staje się płynny,
gdy Pinnacle otworzy rynek.

Klasa jest liczona od nowa w każdym przebiegu i zapisana przy każdej nodze
(`tier`, `tier_reason`) i każdym kuponie (`tier`) w rejestrze. Kupony nigdy nie
mieszają klas. Progi są w konfiguracji (`tier_max_pinnacle_overround`,
`tier_min_bookmakers`, `tier_name_rules`, `tier_sharp_fair_only`).

## Wykluczone rodzaje zakładów

Niektóre rodzaje zakładów niektórych bukmacherów systematycznie odbiegają od
Pinnacle (np. inna marża po stronie „tak” w zakładach „czyste konto”, rozchwiana
drabinka linii). Dają „przewagę” w każdym skanie, a nie jest to okazja. Raz na
dobę skaner mierzy to na archiwum:

- **próbka**: tablice z chwili decyzji i co 12 godzin wstecz przez 7 dni (mecze,
  które się jeszcze nie zaczęły, tablice nie starsze niż 8 godzin), więc waży
  tak jak widzi je skaner: daleko i blisko meczu;
- **pomiar**: dla każdej pełnej linii jednego rodzaju zakładu jednego
  bukmachera (obie strony linii, trzy wyniki 1X2) jego szansa po odjęciu marży
  minus szansa Pinnacle tej samej nogi; rodzaje zakładów nie są przed tym łączone
  (Bet365 „Goals Over/Under” 5 i „Goal Line” 50 są mierzone osobno);
- **wykluczenie** (co najmniej 100 zmierzonych nóg): mediana odchylenia powyżej
  2 p.p., albo rozrzut (odporne odchylenie standardowe) ponad 1,4 raza większy
  niż typowy rozrzut tego samego rynku u innych bukmacherów, albo ponad 15% nóg
  odchylonych o więcej niż 5 p.p. Pinnacle nigdy nie jest wykluczany. Rozrzut
  porównujemy z tym samym rynkiem, bo niektóre rynki (sumy goli drużyn) są
  rozchwiane u wszystkich bukmacherów.

Progi leżą tuż nad 95. percentylem (archiwum 29–30.09, 9 tablic, 81 par
bukmacher–rodzaj zakładu): mediana |odchylenia| 0,006, 95. percentyl 0,023;
stosunek rozrzutu 1,00 i 1,33; udział dużych odchyleń 3,4% i 11,6%. Na tej
próbce wykluczonych jest 9 par: Bet365 „Clean Sheet” (27, 28) i William Hill
„Score a Goal” (43, 116) — odchylone o 2,3–3 p.p.; Marathonbet rożne w 1.
połowie (77); SBO 1X2 (1) i 1X2 1. połowy (13); Bet365 rożne powyżej/poniżej
(45); BetVictor gole w 1. połowie (6). Bet365 „Goal Line” (50) na tych dwóch
dniach **nie** odstaje (mediana +0,001, rozrzut taki jak rynek, 5% dużych
odchyleń): jego „wartościowe” nogi miały przesunięcie pary 5–6 p.p. i łapią je
inne reguły (przesunięcie pary, limit 10%, klasa rynku).

Kursy wykluczonych rodzajów zakładów są odrzucane przy wczytywaniu, zanim
kursy jednego bukmachera zostaną połączone, więc inny rodzaj zakładu tego
samego bukmachera na tę samą nogę dalej się liczy, a wykluczone kursy nie
wchodzą też do konsensusu. Pomiar (także przy powtórce `--as-of`, tylko z
danych sprzed chwili decyzji) trafia do tabeli `bet_type_checks` w rejestrze;
lista wykluczeń jest w polu statusu strony i w dzienniku (`bet_types`).

## Kupony

Kupon to 2 albo 3 nogi z wartością z **różnych meczów u jednego
bukmachera**, zawsze z jednej klasy rynku. Nogi traktujemy jako niezależne:

- szansa trafienia = iloczyn szans nóg,
- EV kuponu = τ · (iloczyn p · kurs) − 1, a dolna granica z p_dolne.

Kupon jest proponowany, gdy jego dolna granica EV przy τ = 0,88 (pełny
podatek) jest dodatnia i szansa trafienia wynosi co najmniej 30%. Osobno skaner
zbiera kupony, które spełniają warunki **tylko przy niższym podatku** (τ = 0,94
albo 1,00, a nie 0,88); są pokazywane w osobnej sekcji i nie są propozycją przy
zwykłym podatku. Każdy kupon ma w rejestrze `regime`: najostrzejszy podatek,
przy którym się opłaca (`t088`, `t094`, `t100`). Kupony są ułożone po dolnej
granicy EV, potem po szansie trafienia; w każdej klasie i grupie na
bukmachera najwyżej 20 propozycji, a jedna noga może wystąpić najwyżej w 3 z
nich.

### Przykład

Mecz 1: Pinnacle wystawia 1,55 / 4,30 / 6,00 (gospodarz / remis / gość). Po
odjęciu marży szansa gospodarza p_fair = 0,6313 (uczciwy kurs 1,58). Sześć
godzin przed meczem błąd standardowy p_se = 0,010, więc
p_dolne = 0,6313 − 1,645 · 0,010 = 0,6148. Bet365 płaci za gospodarza **1,75**.

- Bez podatku: EV = 0,6313 · 1,75 − 1 = **+10,5%**, dolna granica
  0,6148 · 1,75 − 1 = **+7,6%**. Dodatnia, więc to noga z wartością.
- Jako pojedynczy zakład z podatkiem 12%: 0,88 · 0,6313 · 1,75 − 1 = −2,8%.
  Pojedynczo ten zakład traci.

Mecz 2 wygląda tak samo. Kupon z dwóch nóg u Bet365:

- kurs łączny 1,75 · 1,75 = 3,06, szansa trafienia 0,6313 · 0,6313 = **39,8%**
  (więcej niż 30%),
- EV z podatkiem: 0,88 · (0,6313 · 1,75)² − 1 = **+7,4%**,
- dolna granica: 0,88 · (0,6148 · 1,75)² − 1 = **+1,9%**, dodatnia, więc
  kupon jest proponowany.

Dwie nogi po około 10% przewagi wystarczają, bo podatek płaci się raz, a
przewagi się mnożą. Pojedynczy zakład potrzebowałby przewagi około 13,6%.

## Co pokazuje strona

`latest.html` (i kopia dnia w `html/RRRR-MM-DD.html`) to jeden plik, który
działa w telefonie. Sekcje po kolei:

1. **Status**: czas skanu (czas polski), mecze z kursami, mecze ocenione
   (płynne / słabe), kursy, ocenione kursy, nogi z wartością dla każdego
   podatku (płynne / słabe), kupony przy podatku 12%, stan modeli, zapytania do
   API i rozwijana lista „Co pominięto i dlaczego” (kursy stare, rozpoczęte,
   bez uczciwej ceny, podejrzane, niezsynchronizowane, wykluczone rodzaje
   zakładów, mecze bez nazw, ostrzeżenia).
2. **Single opłacalne same w sobie przy podatku 12%** — rynki płynne. Zwykle
   puste (patrz „ważna konsekwencja limitu 10%”); strona mówi to wprost.
3. **Kupony 2–3 nogi przy podatku 12%** — rynki płynne. Gdy pusto, strona pisze
   wprost: „Dziś nic nie opłaca się po podatku 12%”.
4. **Tylko przy niższym podatku (np. promocje, Betclic)** — nogi i kupony rynków
   płynnych, które opłacają się dopiero przy podatku 6% albo bez podatku;
   wyraźnie oznaczone „nie opłaca się przy podatku 12%”.
5. **Eksperymentalne: rynki słabe** — nogi i kupony rynków słabych, z powodem
   klasy przy każdej nodze i ostrzeżeniem, że to niewiarygodne.
6. **Wyniki papierowe** — osobno dla rynków płynnych i słabych, dla każdego
   podatku (12%, 6%, bez podatku), pojedyncze i kupony, za 7 i 30 dni: liczba
   zakładów, rozliczone, trafienia, zysk na złotówkę stawki przy tym podatku,
   CLV (liczba, mediana, odsetek pobitych zamknięć). Pod spodem **tabela
   ostatnich 7 dni**: każdy zaproponowany kupon (z nogami i wynikiem każdej nogi)
   i każda noga z wartością, z wynikiem: *wygrany*, *przegrany*, *zwrot*,
   *pół-wygrany*, *pół-przegrany*, *unieważniony* albo *oczekuje*, oraz zyskiem
   przy podatku 12%.

Dalej: **Tylko model** (kursy bez ceny rynkowej, tylko do badań) i **Jak
czytać**. Wyniki są liczone od pierwszej chwili, w której noga albo kupon stały
się propozycją przy danym podatku, po kursie z tej chwili, tylko dla bieżącej
konfiguracji.

## Gdzie są pliki

Wszystko w `C:\Users\oskar\Documents\Model-Buk-Data\`:

| Plik | Co zawiera |
|---|---|
| `scanner\latest.html`, `scanner\html\RRRR-MM-DD.html` | strona (ostatni skan; kopia dnia nadpisywana kolejnymi skanami tego dnia) |
| `scanner\latest_value_legs.csv`, `scanner\csv\RRRR-MM-DD_value_legs.csv` | nogi z wartością ostatniego skanu (arkusz: polskie opisy zakładów, czas polski, wszystkie liczby, klasa rynku `tier` i jej powód) |
| `scanner\latest_coupons.csv`, `scanner\csv\RRRR-MM-DD_coupons.csv` | kupony z listy, jeden wiersz na nogę: wszystkie klasy i grupy podatkowe (kolumny `tier` i `regime`), najpierw rynki płynne i kupony przy pełnym podatku, w grupie najlepszy pierwszy |
| `scanner\scanner.sqlite` | rejestr papierowy (tylko dopisywanie): konfiguracje, skany, nogi, kupony, rozliczenia, CLV, pomiary rodzajów zakładów (`bet_type_checks`) |
| `scanner\model_forecasts.sqlite` | prognozy modeli z każdego treningu (tylko dopisywanie) |
| `scanner\runs.jsonl` | jedna linia JSON na przebieg: czasy kroków, zapytania API, liczby, ostrzeżenia |
| `scanner\model_cache\` | nocny trening modeli (czyta go skaner) |
| `scanner\offline\` | wszystko to samo dla przebiegów `--offline` (rejestr na żywo pozostaje nietknięty) |
| `scanner\replay\` | wszystko to samo dla powtórek `--as-of` bez `--live-register` |
| `logs\scanner.log` | jedna linia na przebieg z `run_scanner.ps1` |
| `logs\scanner_errors.log` | pełny opis błędu, gdy przebieg się nie udał |
| `fixture-directory\directory.sqlite3` | kalendarz, wyniki i statystyki meczów (odświeżane przez skaner), od wersji 1.1 także zdarzenia meczu z minutami (`fixture_events`) |
| `api-football-odds\odds.sqlite3` | archiwum kursów (pisze kolektor kursów, skaner tylko czyta) |

## Jak uruchomić

Z folderu `app\`:

```
.venv\Scripts\python scripts\run_scanner.py
```

Opcje:

- `--no-api`: bez żadnego zapytania do API (kalendarz i statystyki nie są
  odświeżane, reszta działa normalnie).
- `--offline`: przebieg badawczy, bez API, a wszystkie wyniki idą do
  `scanner\offline\` (rejestr papierowy na żywo pozostaje nietknięty).
- `--as-of 2026-09-30T18:00:00Z`: powtórka decyzji z podanej chwili (tylko
  kursy widziane przed nią); bez zapytań do API. Powtórka **nigdy** nie pisze do
  rejestru na żywo: jej rejestr, strona, CSV i dziennik idą do
  `scanner\replay\` (z `--offline` do `scanner\offline\`).
- `--live-register` (tylko z `--as-of`): zapisz powtórkę jednak do rejestru na
  żywo. Świadoma decyzja, np. żeby uzupełnić przebieg, który nie zdążył się
  wykonać.
- `--days-ahead 2`: tylko mecze, które zaczynają się w ciągu 2 dni. To zmienia
  definicję puli, więc jest to osobny eksperyment papierowy (inny numer
  konfiguracji).
- `--no-settle`: bez rozliczania i CLV.
- `--reserve 1500`: poziom dziennego limitu API, przy którym skaner przestaje
  wysyłać zapytania (domyślnie 1500, wspólna rezerwa wszystkich kolektorów).
- `--verbose`: postęp na ekranie.

Do harmonogramu służy `app\scripts\run_scanner.ps1` (dopisuje jedną linię do
`logs\scanner.log`). Zadania w Harmonogramie nie są jeszcze założone.
Proponowany rytm:

| Zadanie | Kiedy | Po co |
|---|---|---|
| `run_scanner.ps1` | co 30 minut, np. o :15 i :45 | po godzinnym kolektorze kursów (:05); częściej = więcej obserwacji i świeższe propozycje |
| `run_closing_odds.ps1` | co 15 minut | kursy tuż przed meczem, potrzebne do uczciwego CLV |
| `fit_live_models.py fit --jobs 3` | codziennie ok. 03:00 | trening modeli po nocnej przebudowie historii (02:15); bez niego po dobie skaner działa bez modeli |

Osobne zadanie dla katalogu meczów (`collect_fixture_directory.py`) nie jest
potrzebne, jeśli skaner działa co 30–60 minut: skaner sam odświeża kalendarz
i dociąga statystyki do rozliczeń.

## Tryb papierowy: zastrzeżenia

- **Zagraniczni bukmacherzy zamiast polskich.** Źródło kursów ma około 9
  bukmacherów (Bet365, 1xBet, Betano, Marathonbet, William Hill, BetVictor,
  SBO, a także Pinnacle i Betfair). Polskich legalnych bukmacherów w nim nie ma,
  więc propozycje dotyczą zagranicznych domów i służą jako zastępstwo. Polscy
  bukmacherzy mają zwykle wyższą marżę i inne kursy; wynik papierowy nie
  przenosi się na nich jeden do jednego.
- **Pinnacle i Betfair nigdy nie są proponowane.** Służą tylko jako źródło
  ceny.
- **Podatek.** τ = 0,88 to polski podatek 12% od stawki; τ = 0,94 i 1,00 są
  pokazane dla porównania (np. promocje bez podatku). Propozycja kuponu
  wymaga dodatniej dolnej granicy EV przy pełnym podatku.
- **Model jest tylko dodatkiem.** Nasze modele są gorsze od Pinnacle (o około
  0,013 log-loss w 1X2), więc nie zmieniają ceny rynkowej ani listy
  propozycji. Ligi spoza 47 przetestowanych mają modele bez sprawdzenia na
  danych.
- **Kursy dostawcy są opóźnione.** Tablica kursów w API ma zwykle kilka godzin
  (mediana około 3,8 godziny według znacznika dostawcy). Kurs „teraz” może
  już nie istnieć u bukmachera.
- **Stare kursy są pomijane, dalsze mecze mają luźniejszy limit.** Kurs, który
  kolektor widział dawniej niż 3 godziny temu, nie wchodzi do oceny. Kolektor
  kursów sprawdza dziś i dwa kolejne dni co godzinę, a dalsze dni tylko co 6
  godzin (o 0, 6, 12 i 18 UTC), więc dla meczu odległego o więcej niż 48
  godzin limit wynosi 8 godzin (to samo dla tablic, z których liczymy uczciwą
  cenę). Dzięki temu dalsze mecze są w puli w każdym przebiegu, a nie co
  drugi. Gdy komputer jest wyłączony i szeroki skan nie działa dłużej niż 8
  godzin, dalsze mecze wypadają z puli do następnego szerokiego skanu. W
  pierwszym przebiegu (30.09), jeszcze z jednym limitem 3 godzin, z tego
  powodu odpadło 195 z 389 meczów z kursami (komputer był wyłączony w nocy).
  Limity są w konfiguracji (`max_quote_age_hours`, `far_lead_hours`,
  `max_quote_age_far_hours`, `fair_max_age_minutes`,
  `fair_max_age_far_minutes`).
- **Cienkie rynki.** W meczach towarzyskich, kobiecych, młodzieżowych i
  niższych ligach Pinnacle bywa mało wiarygodny, a różnice kursów są częściej
  błędem niż okazją. Dlatego takie mecze są w klasie „słaby” i nie trafiają do
  głównych sekcji (patrz „Co znaczą klasy rynków”).
- **CLV to ta sama miara ceny.** Porównanie z zamknięciem używa tej samej
  definicji uczciwej ceny, więc samo w sobie nie dowodzi przewagi. Wnioski
  dopiero po setkach rozliczonych zakładów.
- **Nogi kuponu traktujemy jako niezależne.** Korelacja meczów tej samej ligi
  i godziny nie jest modelowana.
- **Kartki** liczymy jako żółte plus czerwone; bukmacherzy liczą je różnie.
- **Model podziału na połowy** zakłada, że gole w połowach padają niezależnie.
  W historii zaniża przez to powroty („do przerwy / koniec” gospodarz/gość i
  gość/gospodarz) o ok. jedną czwartą; dodatkowy błąd ceny to pokrywa, ale ceny
  z modelu na tych nogach są niższe od prawdziwych.
- **Nowe rynki bez Pinnacle.** Pinnacle nie wystawia żadnego z nowych rynków,
  więc ich ceny to model (z macierzy Pinnacle) albo konsensus. Nie da się też
  zmierzyć, czy któryś bukmacher systematycznie odbiega na tych rynkach
  (pomiar rodzajów zakładów porównuje z Pinnacle).
- **Mecze odwołane i przełożone** są unieważniane (zwrot stawki, zysk zero,
  poza liczeniem trafień). Statusy dostawcy: odwołany (CANC), przerwany (ABD),
  przyznany walkowerem (AWD, WO) — od razu; przełożony (PST), zawieszony (SUSP),
  przerwany chwilowo (INT) — gdy mecz nadal ma taki status 48 godzin
  (`void_grace_hours`) po godzinie, na którą noga była wzięta. Mecz przełożony,
  któremu dostawca dał już nową datę (status NS/TBD), jest unieważniany, gdy nowa
  data wypada ponad 48 godzin po pierwotnej i ten czas minął. Unieważniona noga
  wypada z kuponu jak zwrot; kupon z samymi unieważnionymi nogami jest
  unieważniony. Status bierze się z katalogu meczów.
- **Nowa konfiguracja = nowy eksperyment.** Każda zmiana progu zmienia numer
  konfiguracji (`config_id`); wyniki papierowe są liczone osobno dla każdej
  konfiguracji.

## Co skaner zbiera codziennie

Przy każdym przebiegu skaner zapisuje:

- **nogi**: każdy kurs wyższy od uczciwej ceny u każdego bukmachera (także
  te bez zapasu na niepewność, podejrzane i niezsynchronizowane), z uczciwą
  szansą, jej źródłem i błędem, szansą modelu, EV dla trzech założeń
  podatkowych, klasą nogi (wartość, prawie, podejrzana, niezsynchronizowana,
  tylko model), klasą rynku (`tier`, `tier_reason`), różnicą czasu tablic
  (`board_lag_min`) i czasem, w którym kurs był widziany; wiersz
  powstaje, gdy noga jest nowa albo coś się w niej zmieniło (co najmniej raz na
  6 godzin), więc nic nie ginie, a rejestr nie puchnie,
- **kupony**: wszystkie spełniające warunki (do 500 na bukmachera, klasę i
  grupę podatkową w skanie) z ich nogami, klasą rynku i najostrzejszym
  podatkiem, przy którym się opłacają, oraz liczbę wszystkich sprawdzonych i
  spełniających warunki,
- **pomiar rodzajów zakładów** raz na dobę (`bet_type_checks`),
- **przebieg**: liczby, konfigurację, źródła uczciwych cen, stan modeli,
  odświeżenie kalendarza, zapytania API, czasy kroków i ostrzeżenia
  (`scans` w rejestrze i `runs.jsonl`),
- **rozliczenia** nóg zakończonych meczów (wygrana, przegrana, zwrot,
  połowa wygranej, połowa przegranej, unieważniony) i **CLV** nóg, których
  mecz już się zaczął,
- **prognozy modeli** z każdego treningu (gole dla każdego meczu, rożne i
  kartki dla linii, które ktoś wystawia),
- **kalendarz** wszystkich meczów wszystkich lig (3 dni wstecz, 7 w przód),
  wyniki i statystyki meczów potrzebne do rozliczeń (rożne, kartki, także za
  połowy); najpierw dla meczów z propozycjami.

Obok skanera działają kolektory, które zbierają dane wejściowe: kursy co
godzinę (`OddsCollector`), kursy tuż przed meczem (`run_closing_odds.ps1`,
co 15 minut, do założenia w Harmonogramie), dane przedmeczowe co 20 minut
(kontuzje, składy, predykcje API), nocna przebudowa historii i nocny trening
modeli. Surowe kursy są archiwizowane w całości, więc każdy przebieg skanera
da się później odtworzyć (`--as-of`), a nowe rynki wycenić wstecz.

Od wersji 1.1 przebieg zapisuje też liczby nowych rynków (`markets_v11` w
`runs.jsonl` i w diagnostyce skanu: kursy, ceny według źródła, kursy bez ceny,
nogi z wartością według rynku, klasy i podatku) oraz udziały goli w 1. połowie
użyte w modelu (`half_shares`).

Budżet API skanera na przebieg: kalendarz 1–3 zapytania (najwyżej 15),
statystyki do rozliczeń najwyżej 20 zapytań (20 meczów na zapytanie) plus 20
zapytań za połowy (jedno na mecz). Przy 48 przebiegach na dobę to zwykle
100–300 zapytań dziennie. Każdy krok zatrzymuje się, gdy dzienny limit spadnie
do 1500.

## Pierwszy przebieg (30.09.2026, 19:12 czasu polskiego, przed poprawką limitu wieku kursów)

- 389 meczów z kursami, 600 909 kursów u 11 bukmacherów (w tym Pinnacle i
  Betfair jako źródła ceny), uczciwe ceny dla 60 162 zakładów w 188 meczach
  (Pinnacle 10 161, pochodne z Pinnacle 41 994, konsensus 7 699, Betfair 308).
- 157 487 ocenionych kursów, 3 252 powyżej uczciwej ceny (1 120 z nich
  podejrzanych), 92 nogi z wartością bez podatku, 6 przy τ = 0,94, 0 przy
  pełnym podatku; na liście 75 nóg (jedna na mecz, bukmachera i rodzaj rynku),
  głównie Bet365 (43) i 1xBet (14), głównie handicapy i sumy goli.
- Kupony: sprawdzono 8 619 kombinacji, żadna nie ma dodatniej dolnej granicy
  EV przy pełnym podatku (najlepsza: −2,1%); przy τ = 0,94 spełniałoby warunki
  40 kuponów. Strona uczciwie pokazuje „brak kuponów”.
- Modele: trening z 30.09, 16:01 UTC; prognozy dla 78 meczów (34 w
  przetestowanych ligach); pozostałe mecze są z lig bez modelu.
- API: 2 zapytania (kalendarz na dziś i jutro), pozostało 2 342 z dziennego
  limitu. Czas: 17,4 s.
- Rozliczenie: 3 520 nóg czeka na rozliczenie, 15 meczów już się zaczęło, ale
  żaden jeszcze się nie skończył, więc nic nie rozliczono. CLV zapisano dla 88
  nóg z wcześniejszych, testowych skanów (sprzed poprawek progów).

## Przebieg kontrolny po przeglądzie (30.09.2026, 20:05 czasu polskiego)

Po przeglądzie zmieniono limit wieku kursów (osobny, luźniejszy dla meczów
dalszych niż 48 godzin), więc konfiguracja ma nowy numer (`13fb93a8d56c`) i
liczy się od nowa. Przebieg zaraz po szerokim skanie kursów (18:05 UTC):

- 493 mecze z kursami, 467 z nich ocenionych (poprzednio 185 z 386, bo
  dalsze mecze miały kursy sprzed 23 godzin); 735 977 kursów, uczciwe ceny dla
  127 544 zakładów (Pinnacle 18 543, pochodne z Pinnacle 81 358, konsensus
  26 821, Betfair 822).
- 350 271 ocenionych kursów, 7 264 powyżej uczciwej ceny (2 782
  podejrzanych), 88 nóg z wartością bez podatku, 6 przy τ = 0,94, 0 przy pełnym
  podatku; na liście 74 nogi. Żaden kupon nie spełnia warunków (sprawdzono
  7 890 kombinacji). Dalsze mecze mają szerszy błąd ceny, więc prawie nie dają
  nóg z wartością (jedna z 88).
- Czas 36 s, jedno zapytanie do API (kalendarz), nic do rozliczenia jeszcze nie
  ma (pierwsze mecze kończą się dopiero wieczorem).

## Przebieg po wersji 1.0.1 (30.09.2026, 21:17 czasu polskiego)

Nowa konfiguracja `637ff88f7672` (limit przewagi 10%, klasy rynków, zgodność
czasu tablic, wykluczone rodzaje zakładów, unieważnianie):

- 478 meczów z kursami, 452 ocenione: **166 płynnych, 286 słabych**; 705 695
  kursów, 329 891 ocenionych (płynne 173 869, słabe 156 022).
- Wykluczone rodzaje zakładów (pomiar z 21:15, 67 074 zmierzone nogi w 311
  meczach, 72 pary ocenione): 1xBet 106, Bet365 27 i 28, BetVictor 6, SBO 1 i 13,
  William Hill 43 i 116; odrzucono 6 198 kursów.
- 2 852 podejrzane kursy, 0 niezsynchronizowanych (na bieżącej tablicy wszyscy
  bukmacherzy meczu mają ten sam czas dostawcy).
- Nogi z wartością: płynne 0 / 0 / 7 (podatek 12% / 6% / bez podatku), słabe
  0 / 0 / 49. Kupony przy podatku 12%: **0** (ani płynne, ani słabe). Tylko przy
  niższym podatku: 1 kupon płynny (bez podatku, dolna granica EV +0,9%) i 638
  słabych (20 na liście).
- Wniosek: po podatku 12% dziś nic się nie opłaca; na płynnych rynkach
  przewagi są małe (2–9%) i wystarczają najwyżej przy zakładach bez podatku.
- API: 1 zapytanie (kalendarz na dziś), zostało 2 115. Czas 36 s (66 s w
  przebiegu z pomiarem rodzajów zakładów).

## Przebieg po wersji 1.1 (01.10.2026, 19:19 czasu polskiego)

Nowa konfiguracja `3d4a122f421d` (nowe rynki zmieniają definicję wartości, więc
wyniki papierowe liczą się od nowa). Przebieg na żywo kilkanaście minut po
godzinnym skanie kursów:

- 625 meczów z kursami, 475 ocenionych (164 płynne, 311 słabych), 863 164
  kursy; uczciwe ceny dla 155 104 nóg (Pinnacle 20 522, pochodne z Pinnacle
  107 102, konsensus 25 980, Betfair 1 500).
- Nowe rynki: 90 993 kursy; ceny głównie z podziału na połowy
  (`half:pinnacle_1h`, np. 2 700 nóg „do przerwy / koniec”, 5 032 handicapu
  europejskiego w połowach) i z macierzy; 148 kursów na żółte kartki w 1.
  połowie (tylko 1xBet) zostało niewycenionych, zgodnie z regułą.
- Nogi z wartością: płynne 0 / 0 / 46, słabe 0 / 0 / 46 (podatek 12% / 6% /
  bez podatku), **żadna z nowych rynków**. Nowe rynki dały 450 nóg powyżej
  uczciwej ceny: 272 „prawie” (najlepsza dolna granica EV −2,9%) i 178
  podejrzanych (głównie para kursów daleko od ceny modelu: 140).
- Kupony przy podatku 12%: 0. Tylko przy niższym podatku: 17 płynnych i 16
  słabych na liście.
- API: 2 zapytania (kalendarz). Katalog meczów dostał tabelę zdarzeń (44 mecze
  z zapisanych odpowiedzi).

Powtórka offline tablicy z 30.09, 20:30 UTC (449 meczów ocenionych, 167 płynnych)
daje to samo: 56 nóg z wartością bez podatku (7 płynnych), żadna z nowych
rynków; 392 nogi nowych rynków powyżej uczciwej ceny (231 „prawie”, 161
podejrzanych).
