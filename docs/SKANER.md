# Skaner wartości (tryb papierowy)

Wersja z 30.09.2026. Skaner co przebieg przegląda **wszystkie** nadchodzące
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
2. **Kursy.** Wczytuje ostatni kurs każdego bukmachera dla każdego zakładu
   każdego meczu, który się jeszcze nie zaczął. Tylko kursy widziane przed
   chwilą decyzji.
3. **Uczciwe ceny.** Dla każdego zakładu liczy uczciwą szansę (opis niżej).
4. **Modele.** Nasze modele statystyczne (gole: pula modeli; rożne i kartki:
   model liczebności) liczą swoje szanse dla meczów lig, które modelujemy. Modele
   są trenowane raz na dobę (`fit_live_models.py fit`); jeśli nie ma treningu
   albo jest starszy niż doba, skaner działa bez modeli i zapisuje ostrzeżenie.
5. **Wartość i kupony.** Porównuje każdy kurs z uczciwą ceną, wybiera nogi z
   wartością i składa z nich kupony.
6. **Rejestr.** Zapisuje przebieg do rejestru papierowego (jeden `scan_id`).
7. **Rozliczenie i CLV.** Rozlicza nogi z wcześniejszych przebiegów, których
   mecze się skończyły (wynik z katalogu meczów; do zakładów na rożne i kartki
   dociąga statystyki meczu z API, z limitem zapytań), i zapisuje CLV, czyli
   porównanie wziętego kursu z ceną zamknięcia (ostatni kurs przed meczem).
8. **Wyniki.** Zapisuje stronę HTML, pliki CSV i jedną linię dziennika.

Przebieg trwa około 20 sekund. Jeśli nie ma internetu, klucza API albo limit
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
   wystawia (np. obie drużyny strzelą, dokładny wynik, druga połowa), liczone z
   rozkładu wyników dopasowanego do wszystkich jego kursów tego meczu.
5. **Konsensus na skrajnych szansach** (poniżej 15% lub powyżej 85%).
6. **Betfair** jako ostateczność (w tym źródle danych to zwykły bukmacher, nie
   giełda).

Każda uczciwa szansa ma swój błąd standardowy (`p_se`): im dalej do meczu, im
słabsze źródło i im mniej spójna drabinka linii, tym większy.

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

- przewaga nad uczciwą ceną przekracza 20% (prawie zawsze błąd danych albo
  kurs, którego nie da się zagrać),
- uczciwa cena jest bardzo niepewna (p_se powyżej 0,05),
- cała para kursów bukmachera leży daleko od ceny rynkowej (przesunięcie o
  ponad 6 punktów procentowych: stara tablica albo źle opisana linia).

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

## Kupony

Kupon to 2 albo 3 nogi z wartością z **różnych meczów u jednego
bukmachera**. Nogi traktujemy jako niezależne:

- szansa trafienia = iloczyn szans nóg,
- EV kuponu = τ · (iloczyn p · kurs) − 1, a dolna granica z p_dolne.

Kupon jest proponowany, gdy jego dolna granica EV przy τ = 0,88 (pełny
podatek) jest dodatnia i szansa trafienia wynosi co najmniej 30%. Kupony są
ułożone po dolnej granicy EV, potem po szansie trafienia; na bukmachera
najwyżej 20 propozycji, a jedna noga może wystąpić najwyżej w 3 z nich.

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
działa w telefonie:

- **Nagłówek**: czas skanu (czas polski) i liczby: mecze ocenione (te, dla
  których był świeży kurs i uczciwa cena), bukmacherzy (bez Pinnacle i
  Betfair), ocenione kursy, nogi z wartością, proponowane kupony, podejrzane
  kursy. W dzienniku (`logs\scanner.log`) `fixtures=` to wszystkie mecze z
  kursami w archiwum, a `evaluated=` to mecze ocenione, czyli liczba z
  nagłówka strony.
- **Najlepsze kupony** (do 15): bukmacher, kurs łączny, nogi (mecz, godzina,
  zakład, kurs, szansa), szansa trafienia, EV dla trzech założeń podatkowych z
  dolną granicą.
- **Nogi z wartością** (do 200), pogrupowane po dniu meczu, dzisiejsze na
  górze: godzina, mecz i liga, zakład, bukmacher, kurs, uczciwy kurs, szansa,
  przewaga, szansa zwrotu stawki, EV dla trzech założeń, źródło uczciwej ceny,
  zdanie modelu. Kliknięcie nagłówka sortuje tabelę.
- **Tylko model**: kursy bez ceny rynkowej (tylko do badań).
- **Wyniki papierowe** za 7 i 30 dni, osobno pojedyncze nogi i kupony:
  liczba zakładów, rozliczone, trafienia, zysk na złotówkę stawki dla trzech
  założeń podatkowych, CLV (mediana i odsetek pobitych zamknięć). Liczone od
  pierwszej chwili, w której noga albo kupon stały się propozycją, po kursie z
  tej chwili, tylko dla bieżącej konfiguracji.
- **Jak czytać**: krótkie objaśnienia.

## Gdzie są pliki

Wszystko w `C:\Users\oskar\Documents\Model-Buk-Data\`:

| Plik | Co zawiera |
|---|---|
| `scanner\latest.html`, `scanner\html\RRRR-MM-DD.html` | strona (ostatni skan; kopia dnia nadpisywana kolejnymi skanami tego dnia) |
| `scanner\latest_value_legs.csv`, `scanner\csv\RRRR-MM-DD_value_legs.csv` | nogi z wartością ostatniego skanu (arkusz: polskie opisy zakładów, czas polski, wszystkie liczby) |
| `scanner\latest_coupons.csv`, `scanner\csv\RRRR-MM-DD_coupons.csv` | proponowane kupony, jeden wiersz na nogę, najlepszy kupon pierwszy |
| `scanner\scanner.sqlite` | rejestr papierowy (tylko dopisywanie): konfiguracje, skany, nogi, kupony, rozliczenia, CLV |
| `scanner\model_forecasts.sqlite` | prognozy modeli z każdego treningu (tylko dopisywanie) |
| `scanner\runs.jsonl` | jedna linia JSON na przebieg: czasy kroków, zapytania API, liczby, ostrzeżenia |
| `scanner\model_cache\` | nocny trening modeli (czyta go skaner) |
| `scanner\offline\` | wszystko to samo dla przebiegów `--offline` (rejestr na żywo pozostaje nietknięty) |
| `logs\scanner.log` | jedna linia na przebieg z `run_scanner.ps1` |
| `logs\scanner_errors.log` | pełny opis błędu, gdy przebieg się nie udał |
| `fixture-directory\directory.sqlite3` | kalendarz, wyniki i statystyki meczów (odświeżane przez skaner) |
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
  kursy widziane przed nią); bez zapytań do API.
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
  błędem niż okazją. Stąd limity „podejrzanych” kursów.
- **CLV to ta sama miara ceny.** Porównanie z zamknięciem używa tej samej
  definicji uczciwej ceny, więc samo w sobie nie dowodzi przewagi. Wnioski
  dopiero po setkach rozliczonych zakładów.
- **Nogi kuponu traktujemy jako niezależne.** Korelacja meczów tej samej ligi
  i godziny nie jest modelowana.
- **Kartki** liczymy jako żółte plus czerwone; bukmacherzy liczą je różnie.
- **Mecze odwołane i przełożone** nie są na razie rozliczane jako zwrot:
  zostają nierozliczone.
- **Nowa konfiguracja = nowy eksperyment.** Każda zmiana progu zmienia numer
  konfiguracji (`config_id`); wyniki papierowe są liczone osobno dla każdej
  konfiguracji.

## Co skaner zbiera codziennie

Przy każdym przebiegu skaner zapisuje:

- **nogi**: każdy kurs wyższy od uczciwej ceny u każdego bukmachera (także
  te bez zapasu na niepewność i podejrzane), z uczciwą szansą, jej źródłem i
  błędem, szansą modelu, EV dla trzech założeń podatkowych, klasą (wartość,
  prawie, podejrzana, tylko model) i czasem, w którym kurs był widziany; wiersz
  powstaje, gdy noga jest nowa albo coś się w niej zmieniło (co najmniej raz na
  6 godzin), więc nic nie ginie, a rejestr nie puchnie,
- **kupony**: wszystkie spełniające warunki (do 500 na bukmachera i skan) z
  ich nogami, oraz liczbę wszystkich sprawdzonych i spełniających warunki,
- **przebieg**: liczby, konfigurację, źródła uczciwych cen, stan modeli,
  odświeżenie kalendarza, zapytania API, czasy kroków i ostrzeżenia
  (`scans` w rejestrze i `runs.jsonl`),
- **rozliczenia** nóg zakończonych meczów (wygrana, przegrana, zwrot,
  połowa wygranej, połowa przegranej) i **CLV** nóg, których mecz już się
  zaczął,
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
