# Model BUK 1 — wynik etapów 1–5

Stan po pełnym przebiegu z 23 września 2026 r.

## 1. Historyczny replay z CLI

Dodano planowany replay CSV/JSONL z walidacją point-in-time, kwarantanną,
hashami źródeł, nested walk-forward i niezależnym doborem strategii per liga.
Przykładowy plan porównuje 36 wariantów: trzy metody de-vig, trzy progi EV i
cztery limity top-N.

## 2. Dane rzeczywiste

Adapter wczytał bez rozpakowywania pięć lokalnych archiwów football-data.co.uk:

- 92 327 meczów z 22 lig;
- 5 132 553 rekordy kursowe;
- zakres dat od 2014-07-25 do 2026-05-31.

Źródło nie zawiera timestampów poszczególnych kwotowań. Opening odds zostały
więc użyte wyłącznie po jawnym przyjęciu założenia badawczego, a cały replay ma
`promotion_eligible=false`.

## 3. Nested benchmark per liga

Porównano Dixon–Coles i ujemny dwumianowy NB2, wybierając konfigurację na inner
OOF przed predykcją outer fold. Wszystkie 22 ligi zakończyły się powodzeniem:

- 30 956 prognoz nested OOS;
- 30 912 spotkań z pełnym kursem opening wykorzystanych w replayu;
- 688 325 wierszy możliwości, 0 rekordów w kwarantannie.

W outer 2025/26 wybrane strategie zawarły łącznie 2 334 zakłady, uzyskując
−505,4128 jednostki, czyli yield −21,65%. Cztery ligi miały dodatni punktowy
yield, ale żadna nie miała dodatniej dolnej granicy 95% CI.

## 4. Paper/shadow

Pipeline point-in-time, tryb offline/live, kontrola świeżości, atomowe manifesty
i skrypt do uruchamiania cyklicznego są gotowe i objęte testami. W lokalnym
zbiorze nie ma serii prawdziwych, timestampowanych snapshotów kursów, dlatego
nie można jeszcze przedstawić prospektywnej próbki paper/shadow. Kolejne
uruchomienia muszą zbierać takie snapshoty przed kickoffem.

## 5. Bramka promocji

Dodano per-ligową ocenę względem odmarżowionego benchmarku rynku: log-loss,
Brier, ECE i klastrowany bootstrap po dacie meczu. Na 30 927 dopasowanych
prognozach każda z 22 lig miała gorszy log-loss od rynku. Żadna liga nie
przeszła bramki jakości, a brak timestampów stanowi niezależną blokadę
promocji.

Wniosek: obecny model 1X2 nie powinien zostać promowany. Następny cykl powinien
skupić się na ulepszeniu modelu i zbieraniu prospektywnych snapshotów, a nie na
strojeniu strategii bukmacherskiej na tym samym archiwum.

## Weryfikacja

Pełny zestaw testów: 253 zaliczone, 1 ostrzeżenie zależności Starlette.

Artefakty wykonania są zapisane lokalnie w `app/runtime/replay/`:

- `model-oof-all/` — prognozy i selekcje modeli;
- `opportunities-all.csv` — wejście pełnego replayu;
- `replay-all/` — ledger i raporty strategii;
- `promotion-gate-all/` — metryki i decyzje bramki.

## Challenger GAS-Poisson

25 września 2026 r. wykonano dodatkowy nested screening trzech dynamicznych
konfiguracji GAS-Poisson na identycznych foldach. Wolny wariant wygrał 70 z 88
selekcji liga × outer fold, średni 18, a szybki nie wygrał żadnej.

W porównaniu z wcześniejszym wyborem Dixon–Coles/NB2, GAS poprawił log-loss w
20 z 22 lig. Średnia poprawa log-loss wyniosła 0,0142, a Brier 0,0102. Żadna
liga nadal nie pokonała benchmarku rynku closing, dlatego GAS pozostaje
challengerem i nie przechodzi bramki promocji.

Pełny replay GAS dał 1 537 zakładów, −416,5576 jednostki i yield −27,10%.
Żadna liga nie miała dodatniego punktowego yield ani dodatniej dolnej granicy
95% CI. Lepsza jakość probabilistyczna nie przełożyła się więc na lepszą
strategię wykonawczą. Szybki wariant GAS usunięto z głównego planu kandydatów;
pozostaje tylko w osobnym planie screeningu jako udokumentowany negatywny wynik.

## Challenger Elo–Davidson i blend

25 września 2026 r. dodano sekwencyjny model Elo–Davidson 1X2 oraz wykonano
nested screening trzech szybkości aktualizacji na tych samych 22 ligach i
czterech outer foldach. W 88 selekcjach liga × fold wariant średni wygrał 46
razy, szybki 27, a wolny 15. Wszystkie trzy warianty pozostają więc w głównym
planie kandydatów.

Na 30 927 prognozach dopasowanych do rynku Elo poprawił bazę Dixon–Coles/NB2
w 19 z 22 lig. Ważony log-loss wyniósł 1,01933, Brier 0,61094, a ECE 0,02009.
Elo miał zatem najlepszą kalibrację ECE, ale był minimalnie słabszy od GAS w
log-loss (1,01863) i Brier (0,61029). Żadna liga nie pokonała rynku closing.

Dodano audytowalny runner stałego blendu prawdopodobieństw, który sprawdza
pełną zgodność wierszy wejściowych, zapisuje hashe źródeł i nie optymalizuje
wag. Eksploracyjny blend Elo/GAS 50/50 osiągnął log-loss 1,01689, Brier
0,60920 i ECE 0,02260. Poprawił GAS w 15 ligach oraz Elo w 20 ligach, lecz
nadal nie pokonał rynku w żadnej lidze; benchmark rynku miał log-loss 0,99549.
Wyniku blendu nie należy traktować jako niezależnego potwierdzenia, ponieważ
wariant 50/50 oceniono po obejrzeniu rezultatów komponentów na tym samym outer
OOF. Wymaga on potwierdzenia na nowym, przyszłym holdoucie.

Dodatkowo uruchomiono właściwy blend walk-forward: pierwszy outer fold służył
wyłącznie jako historia meta-modelu, a wagi dla każdego następnego sezonu
wybierano na wszystkich zakończonych wcześniej outer foldach. Udział Elo rósł
z 30% dla 2023/24 przez 40% dla 2024/25 do 45% dla 2025/26. Na 23 126
prognozach trzech późniejszych holdoutów model osiągnął log-loss 1,01928,
Brier 0,61085 i ECE 0,02422, wobec log-loss rynku 0,99616. Był stabilnie
lepszy od obu komponentów liczonych na tych samych foldach, ale nadal nie
pokonał rynku w żadnej z 22 lig.

Replay zakładów Elo i blendu celowo pominięto na tym etapie: bramka jakości
prognoz odrzuciła wszystkie ligi, a wcześniejszy replay GAS wykazał, że sama
poprawa log-loss bez przewagi nad rynkiem nie daje uzasadnienia dla kolejnego
kosztownego strojenia wykonania na tym samym archiwum.

Nowe artefakty lokalne:

- `model-oof-elo-all/` i `promotion-gate-elo-all/`;
- `model-oof-blend-elo-gas-50-50-all/`;
- `promotion-gate-blend-elo-gas-50-50-all/`;
- `model-oof-blend-elo-gas-walk-forward-all/`;
- `promotion-gate-blend-elo-gas-walk-forward-all/`.

## Kursy przedmeczowe i limit kursu 2,00

26 września 2026 r. kurs opening został włączony jako jawny prior rynku w
logarytmicznym poolingu model–rynek. Waga była wybierana wyłącznie na
zakończonych wcześniejszych outer foldach. We wszystkich trzech kolejnych
sezonach procedura wybrała 0% GAS i 100% rynku opening. Na 23 112 prognozach
opening uzyskał log-loss 0,99964 oraz Brier 0,59764, wobec odpowiednio 1,02123
i 0,61208 dla GAS na tych samych meczach. Poprawa wystąpiła we wszystkich 22
ligach. Rynek closing nadal był lepszy: log-loss 0,99621 i Brier 0,59527.

Wniosek jest negatywny, ale ważny: kursy przedmeczowe niosą więcej informacji
niż obecny model bramkowy, a GAS nie wykazuje jeszcze mierzalnej wartości
dodanej ponad opening. Pooling pozostaje research-only, ponieważ archiwum nie
zawiera timestampów kwotowań opening; runner wymaga jawnego zaakceptowania tego
założenia i zapisuje `promotion_eligible=false`.

Do wspólnego selektora paper/live i replay dodano twardy, konfigurowalny limit
`max_decimal_odds=2.0`. Jest on stosowany przed wyborem najlepszej ceny, dzięki
czemu oferta powyżej limitu nie może wyprzeć bezpiecznej oferty tego samego
rynku. Limit nie jest elementem siatki optymalizacyjnej i nie może zostać
wyłączony przez dobór strategii.

W poprzednim replayu GAS aż 1 512 z 1 537 zakładów miało kurs powyżej 2,00 i
odpowiadało za −413,0384 jednostki wyniku. Po wprowadzeniu limitu żadna liga
osobno nie osiągnęła wymaganych 50 inner bets, więc per-ligowy replay został
poprawnie zatrzymany przez bramkę minimalnej próbki. Wspólna strategia dla
wszystkich lig miała 118 inner bets i inner yield −14,13%. Na outer 2025/26
zawarła 38 zakładów przy kursach 1,62–2,00, uzyskując −4,4368 jednostki i yield
−11,68%; 95% CI wyniósł około [−38,0%, +15,5%]. Żaden wybrany zakład nie
przekroczył kursu 2,00, ale wynik nadal nie potwierdza przewagi.

Nowe artefakty lokalne:

- `model-oof-gas-opening-market-pool-v2-all/`;
- `promotion-gate-gas-opening-market-pool-v2-all/`;
- `replay-gas-max-odds-2-global/`.

## Model residualny względem opening

26 września 2026 r. zbudowano globalny model residualny, w którym
odmarżowione prawdopodobieństwa opening są stałym offsetem log-szans, a model
uczy się wyłącznie ograniczonych korekt. Cechy obejmują przesunięte średnie z
5 i 10 wcześniejszych meczów dla bramek, strzałów, strzałów celnych, rożnych i
punktów, różnicę odpoczynku oraz charakterystykę rozkładu rynku. Statystyki
bieżącego meczu nie mogą wejść do jego własnych cech, a imputacja i
standaryzacja są dopasowywane wyłącznie na zbiorze treningowym.

Nested screening trzech poziomów regularyzacji wykorzystał poprzedni sezon do
wyboru konfiguracji i sezony 2023/24–2025/26 jako outer holdouty. Na 23 112
prognozach residual uzyskał log-loss 0,999611 wobec 0,999644 dla samego opening,
ale Brier 0,597670 wobec 0,597642 dla opening. Poprawa log-loss wyniosła tylko
0,000033; klastrowany 95% CI [−0,000623, +0,000717] obejmuje zero. Residual był
lepszy od opening w 9 z 22 lig, bez dodatniej dolnej granicy CI w którejkolwiek
lidze. Względem closing nadal przegrał globalnie (0,996215), choć punktowo
pokonał closing w dwóch ligach.

Z 503 882 przygotowanych historycznych możliwości 86 299 miało kurs ≤2,00,
ale ani jedna nie miała dodatniego konserwatywnego EV po 5% haircutcie,
podatku 12% i kosztach wykonania. Pełny replay został więc pominięty: selektor
nie miałby żadnego dopuszczalnego zakładu. Model pozostaje challengerem i nie
przechodzi bramki promocji.

Nowe artefakty lokalne:

- `model-oof-opening-market-residual-all/`;
- `promotion-gate-opening-market-residual-all/`;
- `opportunities-opening-market-residual-all.csv` i manifest źródłowy.

## Częściowo ligowy residual

26 września 2026 r. przetestowano rozszerzenie residualu o regulowane efekty
ligowe. Współczynniki formy pozostały globalne, natomiast każda liga otrzymała
osobne korekty log-szans z karą L2 od 5 do 100 razy silniejszą niż pozostałe
cechy. Siłę shrinkage wybierano wyłącznie na sezonie walidacyjnym przed każdym
outer holdoutem.

Walidacja zwiększała preferowaną siłę tłumienia efektów ligowych: wybrane
mnożniki wyniosły kolejno 10, 20 i 100. Outer log-loss pogorszył się do
0,999774 wobec 0,999611 dla residualu globalnego oraz 0,999644 dla samego
opening. Brier wyniósł 0,597772, również gorzej od obu punktów odniesienia.
Częściowo ligowy wariant poprawił model globalny tylko w 10 z 22 lig i opening
w 7 ligach. Różnica względem residualu globalnego wyniosła −0,000163, z 95% CI
[−0,000369, +0,000038]. Bramka promocji ponownie odrzuciła wszystkie ligi.

Wariant ligowy zostaje zapisany jako negatywny wynik i nie jest kierowany do
replayu: jest słabszy od modelu globalnego, który sam nie wygenerował żadnej
dopuszczalnej okazji przy kursie ≤2,00.

Nowe artefakty lokalne:

- `model-oof-opening-market-residual-league-all/`;
- `promotion-gate-opening-market-residual-league-all/`.

## Ablacja świeżej formy 5/10

27 września 2026 r. dodano do runnera residualnego możliwość testowania
zestawów cech bez zmiany kontraktu walk-forward. Nowy plan porównuje trzy
warianty: tylko ostatnie 5 meczów, tylko ostatnie 10 meczów oraz połączenie
5+10. W każdym outer foldzie walidacja wybiera jednocześnie zestaw cech i siłę
regularyzacji, a następnie model jest refitowany wyłącznie na wcześniejszych
danych.

Na tych samych 23 112 prognozach outer OOS ablation uzyskał log-loss
0,999235 i Brier 0,597457, wobec 0,999644 i 0,597642 dla samego opening.
Jest to lepsze niż poprzedni residual globalny, ale nadal za małe, by zmienić
wniosek jakościowy. Rynek closing pozostaje wyraźnie lepszy z log-loss
0,996215.

Selekcja foldów była mieszana: dla sezonu 2023/24 wygrał wariant 5 meczów,
a dla 2024/25 i 2025/26 wariant 5+10. Średnio na walidacji minimalnie najlepsze
było okno 10 meczów, lecz różnice między 5, 10 i 5+10 były bardzo małe. Punktowo
model poprawił opening w 15 z 22 lig, ale tylko liga P1 miała dodatnią dolną
granicę CI względem opening. Względem closing model był punktowo lepszy w 4
ligach i w żadnej nie miał dodatniej dolnej granicy CI.

Pełna tabela okazji zawierała 503 882 rekordy, z czego 86 299 miało kurs ≤2,00.
Żaden rekord nie miał dodatniego konserwatywnego EV po haircutcie 5%, podatku
12% i kosztach wykonania. Punktowe dodatnie EV bez zabezpieczenia dolnym
oszacowaniem wystąpiło tylko w 5 wierszach. Nie ma więc podstaw do replayu
zakładów ani rekomendowania stawek.

Nowe artefakty lokalne:

- `model-oof-opening-market-residual-form-ablation-all/`;
- `promotion-gate-opening-market-residual-form-ablation-all/`;
- `promotion-gate-opening-market-residual-form-ablation-vs-opening-all/`;
- `opportunities-opening-market-residual-form-ablation-all.csv` i manifest
  źródłowy.

## Założenia dalszego rozwoju: świeża forma i kontekst meczu

27 września 2026 r. doprecyzowano priorytet modelowania: wyników drużyny z
ostatnich 5–10 spotkań nie należy rozmywać długą historią, na przykład całym
poprzednim rokiem. To krótkoterminowa forma ma być podstawowym opisem aktualnej
siły, z dłuższą historią używaną co najwyżej jako słabo ważone tło i
zabezpieczenie przy małej próbce. Model powinien porównywać okna 5 i 10 meczów
oraz sprawdzać ich wartość w walidacji kroczącej bez przecieku przyszłości.

Model residualny już wylicza przesunięte średnie z 5 i 10 wcześniejszych
spotkań dla bramek, strzałów, strzałów celnych, rożnych i punktów, a także
odpoczynek. Są to jednak obecnie głównie cechy zagregowane; dotychczasowy wynik
pokazuje, że same w sobie nie poprawiły wiarygodnie prognozy ponad kurs
opening. Nie należy więc traktować krótkiego okna jako potwierdzonej przewagi,
tylko jako preferowany kierunek dalszych testów.

Drugim priorytetem są informacje właściwe dla konkretnego spotkania: dostępność
i przewidywany skład zawodników, kontuzje i zawieszenia, tożsamość sędziego i
jego historyczny profil, odpoczynek/podróż oraz inne potwierdzone zdarzenia
przedmeczowe. Każda taka cecha musi mieć źródło i czas publikacji nie późniejszy
niż chwila prognozy. Informacja nieznana w tym momencie ma pozostać brakująca,
a nie być odtwarzana z późniejszego raportu. Model powinien uwzględniać
niepewność i brak tych danych zamiast zakładać, że czynnik jest neutralny.

Obecna aplikacja może wyświetlać kontekst kontuzji i sędziego z integracji
bieżącej, lecz te dane nie są jeszcze częścią zwalidowanych cech historycznego
modelu residualnego. Archiwum użyte w dotychczasowych replayach nie zapewnia
kompletnych, punktowych snapshotów składów, kontuzji ani informacji
sędziowskich. Nie wolno zatem przypisywać modelowi przewagi z tych czynników
ani oceniać jej przez retrospektywne dane, które mogły nie być znane przed
meczem.

Następny eksperyment powinien porównać: krótką formę 5 meczów, formę 10
meczów, ograniczone połączenie obu okien oraz te same warianty z kontekstem
spotkania, gdy dostępne są timestampowane dane. Należy raportować przez ablację
wkład każdej grupy cech, pokrycie danych, kalibrację oraz wyniki osobno dla
lig i sezonów. Rynek przedmeczowy pozostaje obowiązkowym punktem odniesienia,
a limit kursu 2,00 pozostaje twardą granicą selekcji. Do paper/shadow i
promocji można przejść dopiero po zbieraniu prospektywnych snapshotów i
przejściu bramki jakości.
