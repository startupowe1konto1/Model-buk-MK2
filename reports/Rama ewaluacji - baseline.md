# Rama ewaluacji: wynik baseline na 1X2

Wspólna rama ewaluacji jest gotowa i zmierzyła pierwszy punkt odniesienia: częstości wyników w lidze. Na 68 813 meczach z 47 lig (sezony 2021–2025) baseline ma log-loss **1,0742** (przedział 95%: 1,0725–1,0759), Brier 0,6501 i RPS 0,2296. Każdy kolejny model (Dixon–Coles, NB2, GAS-Poisson, Elo–Davidson, pi-ratingi z boostingiem) będzie oceniany tą samą procedurą i porównywany z tymi liczbami.

## Jak mierzymy

- **Walk-forward po sezonach.** Sezony testowe to 2021, 2022, 2023, 2024, 2025. Model jest dopasowywany od nowa przed każdą kolejką (rundą; jeśli mecze rundy rozrzucone są na więcej niż 7 dni, np. po przełożeniu meczu, dzielimy je na tygodnie kalendarzowe).
- **Tylko przeszłość.** Do prognozy kolejki trafiają wyłącznie mecze, które skończyły się przed pierwszym gwizdkiem tej kolejki (kickoff co najmniej 3 godziny wcześniej, żeby wynik był znany). Wyniki samej kolejki są przed modelem ukryte.
- **Baseline** to częstości 1X2 w danej lidze policzone na danych treningowych (z wygładzeniem Laplace'a, jeden pseudo-mecz na klasę). Dopasowanie wymaga co najmniej 50 meczów historii ligi.
- **Metryki.** Log-loss jest główną (im niżej, tym lepiej), Brier i RPS pomocniczo (RPS uwzględnia, że wyniki są uporządkowane: gospodarz, remis, gość). Przedziały ufności to bootstrap po kolejkach (2 000 losowań, całe kolejki losowane ze zwracaniem), bo mecze jednej kolejki nie są niezależne.
- **Dane.** Tabela `matches` z API-Football (`history.sqlite`): 133 975 wierszy, z czego do oceny weszły tylko zakończone w regulaminowym czasie (status FT). Odpadło 1 397 meczów (odwołane, przełożone, walkowery, dogrywki i karne) oraz 0 bez wyniku.

## Wynik zbiorczy

| | Mecze | Log-loss [95% CI] | Brier [95% CI] | RPS [95% CI] |
|---|---:|---|---|---|
| Wszystkie ligi | 68 813 | 1,0742 [1,0725; 1,0759] | 0,6501 [0,6489; 0,6512] | 0,2296 [0,2290; 0,2301] |

Bez prognozy zostało 211 meczów sezonów testowych: pierwsze kolejki lig, których dane w archiwum zaczynają się dopiero w sezonie testowym (CHALLENGERPROL, FIRSTLEAGUE2, LIGATHAAL, PREMIERDIVISIO), gdy historia liczy mniej niż 50 meczów. Nie są liczone jako zero, po prostu ich nie oceniamy.

Po sezonach (te same mecze, bez przedziałów):

| Sezon | Mecze | Log-loss | Brier | RPS |
|---|---:|---:|---:|---:|
| 2021 | 13 469 | 1,0767 | 0,6517 | 0,2299 |
| 2022 | 13 688 | 1,0728 | 0,6491 | 0,2294 |
| 2023 | 13 971 | 1,0722 | 0,6487 | 0,2297 |
| 2024 | 13 850 | 1,0739 | 0,6498 | 0,2288 |
| 2025 | 13 835 | 1,0755 | 0,6510 | 0,2299 |

## Wynik w 47 ligach

| Liga | Mecze | Log-loss [95% CI] | Brier [95% CI] | RPS [95% CI] |
|---|---:|---|---|---|
| 1DIVISION | 1 207 | 1,0692 [1,0576; 1,0822] | 0,6473 [0,6395; 0,6559] | 0,2340 [0,2302; 0,2379] |
| 1LIG | 1 741 | 1,0652 [1,0530; 1,0777] | 0,6439 [0,6354; 0,6524] | 0,2303 [0,2265; 0,2341] |
| 1SNL | 544 | 1,0905 [1,0759; 1,1042] | 0,6613 [0,6515; 0,6707] | 0,2315 [0,2259; 0,2369] |
| 2LIGA | 1 182 | 1,0672 [1,0560; 1,0793] | 0,6464 [0,6392; 0,6540] | 0,2354 [0,2323; 0,2385] |
| 3LIGA | 1 893 | 1,0709 [1,0636; 1,0779] | 0,6478 [0,6428; 0,6526] | 0,2302 [0,2268; 0,2333] |
| ALLSVENSKAN | 1 210 | 1,0657 [1,0548; 1,0780] | 0,6449 [0,6374; 0,6532] | 0,2345 [0,2308; 0,2384] |
| AUSTRIA | 974 | 1,0918 [1,0773; 1,1066] | 0,6617 [0,6524; 0,6712] | 0,2299 [0,2261; 0,2339] |
| BELGIUM | 1 608 | 1,0760 [1,0639; 1,0883] | 0,6517 [0,6432; 0,6599] | 0,2323 [0,2283; 0,2362] |
| BUNDESLIGA | 1 538 | 1,0716 [1,0600; 1,0840] | 0,6483 [0,6402; 0,6568] | 0,2304 [0,2267; 0,2340] |
| BUNDESLIGA2 | 1 539 | 1,0753 [1,0657; 1,0848] | 0,6507 [0,6439; 0,6573] | 0,2302 [0,2267; 0,2338] |
| CHALLENGERPROL | 692 | 1,0778 [1,0616; 1,0935] | 0,6538 [0,6440; 0,6632] | 0,2373 [0,2342; 0,2409] |
| CHAMPIONSHIP | 2 781 | 1,0751 [1,0674; 1,0823] | 0,6505 [0,6452; 0,6556] | 0,2288 [0,2259; 0,2318] |
| CZECHLIGA | 1 397 | 1,0667 [1,0529; 1,0795] | 0,6448 [0,6350; 0,6538] | 0,2302 [0,2262; 0,2342] |
| DENMARK | 963 | 1,0849 [1,0711; 1,0983] | 0,6574 [0,6478; 0,6668] | 0,2300 [0,2251; 0,2355] |
| EERSTEDIVISIE | 1 910 | 1,0621 [1,0494; 1,0750] | 0,6427 [0,6342; 0,6514] | 0,2344 [0,2308; 0,2378] |
| EKSTRAKLASA | 1 530 | 1,0760 [1,0646; 1,0887] | 0,6508 [0,6429; 0,6597] | 0,2256 [0,2213; 0,2301] |
| ELITESERIEN | 1 206 | 1,0581 [1,0439; 1,0729] | 0,6389 [0,6288; 0,6494] | 0,2322 [0,2276; 0,2370] |
| EPL | 1 900 | 1,0679 [1,0564; 1,0794] | 0,6461 [0,6383; 0,6540] | 0,2321 [0,2286; 0,2356] |
| EREDIVISIE | 1 587 | 1,0678 [1,0546; 1,0809] | 0,6459 [0,6368; 0,6548] | 0,2317 [0,2283; 0,2351] |
| FIRSTLEAGUE | 1 376 | 1,0652 [1,0514; 1,0790] | 0,6437 [0,6340; 0,6532] | 0,2287 [0,2245; 0,2330] |
| FIRSTLEAGUE2 | 1 553 | 1,0828 [1,0723; 1,0938] | 0,6557 [0,6482; 0,6633] | 0,2246 [0,2209; 0,2283] |
| GREECE | 1 194 | 1,0828 [1,0704; 1,0956] | 0,6559 [0,6471; 0,6651] | 0,2309 [0,2262; 0,2358] |
| HNL | 900 | 1,0762 [1,0609; 1,0922] | 0,6511 [0,6406; 0,6620] | 0,2280 [0,2225; 0,2335] |
| LALIGA | 1 900 | 1,0656 [1,0545; 1,0766] | 0,6436 [0,6358; 0,6515] | 0,2252 [0,2213; 0,2292] |
| LALIGA2 | 2 339 | 1,0694 [1,0595; 1,0797] | 0,6464 [0,6396; 0,6534] | 0,2196 [0,2154; 0,2236] |
| LEAGUEONE | 2 783 | 1,0713 [1,0632; 1,0793] | 0,6480 [0,6424; 0,6535] | 0,2301 [0,2275; 0,2327] |
| LEAGUETWO | 2 779 | 1,0791 [1,0721; 1,0860] | 0,6533 [0,6484; 0,6581] | 0,2290 [0,2260; 0,2319] |
| LIGAI | 1 591 | 1,0771 [1,0678; 1,0866] | 0,6516 [0,6453; 0,6582] | 0,2253 [0,2220; 0,2288] |
| LIGATHAAL | 1 143 | 1,0855 [1,0770; 1,0936] | 0,6581 [0,6525; 0,6635] | 0,2331 [0,2297; 0,2365] |
| LIGUE1 | 1 682 | 1,0729 [1,0618; 1,0836] | 0,6493 [0,6416; 0,6568] | 0,2318 [0,2285; 0,2351] |
| LIGUE2 | 1 761 | 1,0813 [1,0728; 1,0894] | 0,6547 [0,6487; 0,6603] | 0,2274 [0,2236; 0,2311] |
| NBI | 990 | 1,0791 [1,0659; 1,0930] | 0,6532 [0,6442; 0,6626] | 0,2300 [0,2259; 0,2341] |
| PREMIERDIVISIO | 492 | 1,0807 [1,0632; 1,0979] | 0,6541 [0,6419; 0,6662] | 0,2270 [0,2201; 0,2332] |
| PREMIERLEAGUE | 1 165 | 1,0826 [1,0719; 1,0933] | 0,6560 [0,6490; 0,6630] | 0,2325 [0,2293; 0,2358] |
| PREMIERLEAGUE2 | 1 220 | 1,0695 [1,0561; 1,0833] | 0,6465 [0,6370; 0,6561] | 0,2278 [0,2237; 0,2323] |
| PREMIERLEAGUE3 | 1 117 | 1,0852 [1,0741; 1,0961] | 0,6579 [0,6508; 0,6650] | 0,2323 [0,2286; 0,2362] |
| PRIMEIRA | 1 539 | 1,0729 [1,0606; 1,0846] | 0,6494 [0,6410; 0,6570] | 0,2315 [0,2278; 0,2352] |
| SCOTLAND | 1 167 | 1,0645 [1,0514; 1,0787] | 0,6434 [0,6350; 0,6528] | 0,2305 [0,2272; 0,2337] |
| SEGUNDALIGA | 1 539 | 1,0868 [1,0787; 1,0950] | 0,6587 [0,6531; 0,6644] | 0,2300 [0,2264; 0,2334] |
| SERIEA | 1 901 | 1,0878 [1,0796; 1,0967] | 0,6594 [0,6539; 0,6653] | 0,2304 [0,2269; 0,2339] |
| SERIEB | 1 946 | 1,0861 [1,0771; 1,0951] | 0,6581 [0,6521; 0,6642] | 0,2206 [0,2162; 0,2250] |
| SUPERETTAN | 1 218 | 1,0741 [1,0604; 1,0867] | 0,6495 [0,6398; 0,6585] | 0,2261 [0,2218; 0,2304] |
| SUPERLIG | 1 749 | 1,0637 [1,0516; 1,0757] | 0,6425 [0,6339; 0,6509] | 0,2281 [0,2248; 0,2314] |
| SUPERLIGA | 1 496 | 1,0744 [1,0571; 1,0923] | 0,6503 [0,6388; 0,6619] | 0,2324 [0,2286; 0,2364] |
| SUPERLIGA2 | 974 | 1,0719 [1,0570; 1,0867] | 0,6486 [0,6384; 0,6590] | 0,2316 [0,2268; 0,2364] |
| SWITZERLAND | 1 053 | 1,0769 [1,0640; 1,0898] | 0,6514 [0,6427; 0,6602] | 0,2270 [0,2227; 0,2314] |
| VEIKKAUSLIIGA | 844 | 1,0695 [1,0572; 1,0826] | 0,6475 [0,6390; 0,6566] | 0,2365 [0,2321; 0,2410] |

Najniższy log-loss ma ELITESERIEN (1,0581), najwyższy AUSTRIA (1,0918). W 40 z 47 lig przedział ufności zawiera wartość zbiorczą, więc większość różnic między ligami mieści się w szumie. Do oceny modelu liczy się różnica wobec baseline'u w tej samej lidze, nie poziom bezwzględny.

## Niezawodność (CORP)

Diagram niezawodności CORP (Dimitriadis, Gneiting, Jordan 2021) dopasowuje do prognoz regresję izotoniczną i rozkłada wynik na trzy części: S = MCB − DSC + UNC. MCB to miskalibracja (cena za prognozy, które nie mówią prawdy o częstości), DSC to zdolność rozróżniania meczów, UNC to nieunikniona niepewność samego wyniku. Liczone jednoklasowo (klasa kontra reszta) na wszystkich prognozach razem; p(MCB) to test Monte Carlo (200 symulacji) hipotezy, że prognozy są wiarygodne.

Brier:

| Klasa | Częstość | Wynik | MCB | DSC | UNC | p(MCB) |
|---|---:|---:|---:|---:|---:|---:|
| gospodarz | 0,434 | 0,24568 | 0,00015 | 0,00010 | 0,24562 | 0,030 |
| remis | 0,257 | 0,19094 | 0,00015 | 0,00023 | 0,19103 | 0,020 |
| gość | 0,309 | 0,21344 | 0,00014 | 0,00023 | 0,21352 | 0,080 |

Log-loss:

| Klasa | Wynik | MCB | DSC | UNC | p(MCB) |
|---|---:|---:|---:|---:|---:|
| gospodarz | 0,68448 | 0,00031 | 0,00020 | 0,68436 | 0,035 |
| remis | 0,56985 | 0,00038 | 0,00060 | 0,57007 | 0,010 |
| gość | 0,61810 | 0,00034 | 0,00055 | 0,61831 | 0,085 |

Krzywa CORP dla klasy „gospodarz” (prognoza i oszacowane prawdopodobieństwo warunkowe w punktach kwantylowych):

| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |
|---:|---:|---:|
| 0,413 | 0,416 | 0,10 |
| 0,422 | 0,431 | 0,20 |
| 0,427 | 0,431 | 0,30 |
| 0,431 | 0,433 | 0,40 |
| 0,434 | 0,433 | 0,50 |
| 0,441 | 0,435 | 0,60 |
| 0,446 | 0,439 | 0,70 |
| 0,451 | 0,446 | 0,80 |
| 0,457 | 0,446 | 0,90 |
| 0,509 | 0,472 | 1,00 |

Baseline zna tylko średnie ligi i nie odróżnia meczów między sobą, więc prawie cały wynik to UNC. Największe MCB w tabeli Briera to 0,00015, największe DSC 0,00023. Model, który ma coś do powiedzenia o konkretnych meczach, musi mieć DSC wyraźnie wyższe, a MCB nadal bliskie zera.

Dla 2 z 3 klas w tabeli Briera p(MCB) jest poniżej 0,05, czyli nawet ten prosty baseline ma statystycznie wykrywalną miskalibrację. Jest ona bardzo mała: MCB 0,00015 przy UNC od 0,191. MCB kolejnych modeli będzie mierzone względem tej wartości.

## Test istotności na prawdziwych danych

Żeby sprawdzić działanie testów na prawdziwych danych, porównaliśmy baseline z jeszcze prostszym punktem odniesienia: jedne częstości 1X2 dla wszystkich lig naraz (pooled, odświeżane co tydzień kalendarzowy). Różnica strat to strata globalnych częstości minus strata częstości ligowych, więc dodatnia liczba oznacza, że ligowe są lepsze.

- Wszystkie ligi razem: średnia różnica log-loss 0,00030 [-0,00018; 0,00081], statystyka DM 1,14, p = 0,2539: różnica nie jest istotna statystycznie na poziomie 5%.
- Liga po lidze (test Diebolda–Mariano z wariancją HAC po kolejkach, 47 lig): p < 0,05 bez korekty w 5, po korekcie Holma w 0, po korekcie Benjaminiego–Hochberga w 0.

## Test przecieku

Wyniki meczów od 2023-11-01 zostały przetasowane (i osobno odbite gospodarz–gość). Prognozy 32 449 meczów z kolejek rozpoczętych do tej daty włącznie porównano z oryginałem: test przeszedł (shuffle: zmieniono wyniki 26 114 przyszłych meczów, zmienionych prognoz 0, maksymalna różnica 0,000; mirror: zmieniono wyniki 29 778 przyszłych meczów, zmienionych prognoz 0, maksymalna różnica 0,000).

## Czas

Baseline na 69 024 meczach testowych (9 891 kolejek, 9 861 dopasowań): 5,7 s. Punkt odniesienia z globalnymi częstościami: 0,4 s. Cały przebieg skryptu razem z bootstrapem, CORP i testem przecieku: 25,9 s.

## Jak podłączyć kolejny model

Model to obiekt z metodami `fit(train)` i `predict(frame)`, który zwraca macierz prawdopodobieństw (wiersze sumują się do 1, kolumny w kolejności klas rynku: gospodarz, remis, gość albo poniżej, powyżej). Opakowuje się go w `ModelSpec` z fabryką i opcjonalną siatką hiperparametrów (np. półokres zaniku 180–1 200 dni), a wybór z siatki robi rama na foldach wewnętrznych sprzed sezonu testowego. Opis interfejsu jest w docstringu modułu `app/src/model_buk/evaluation/framework.py`; skrypt `app/scripts/run_eval_baseline.py` jest gotowym wzorem użycia. Test `check_no_lookahead` sprawdza każdy nowy model pod kątem podglądania przyszłości.
