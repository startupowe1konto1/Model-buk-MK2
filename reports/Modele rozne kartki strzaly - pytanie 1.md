# Rzuty rożne, kartki i strzały: czy model bije częstość ligową? (pytanie 1)

Model NB2 z formą drużyn, sędzią i odpoczynkiem bije częstość ligową (baseline) w prognozowaniu liczników meczowych. Sprawdziliśmy linie over/under w 4 statystykach (razem 34 linii), sezony testowe 2022–2025, ten sam protokół co dla goli. Przedział ufności różnicy log-loss leży nad zerem w 34 z 34 linii względem baseline'u z całej historii ligi i w 34 z 34 linii względem baseline'u z ostatnich 365 dni. Wielkość przewagi zależy od statystyki i od tego, czy prognozujemy jedną drużynę, czy sumę obu.

Średnia względna poprawa log-loss (strata baseline'u minus strata NB2, podzielona przez stratę baseline'u), średnia po liniach:

| Statystyka | Drużyna vs cała historia | Drużyna vs 365 dni | Suma vs cała historia | Suma vs 365 dni |
|---|---:|---:|---:|---:|
| Rzuty rożne | 4,5% | 4,6% | 0,7% | 0,7% |
| Żółte kartki | 2,3% | 1,8% | 2,6% | 1,6% |
| Strzały | 11,6% | 9,8% | 7,4% | 3,3% |
| Strzały celne | 6,3% | 6,3% | 2,3% | 2,1% |

Największa poprawa: strzały, jedna drużyna (11,6%), najmniejsza: rzuty rożne, suma obu drużyn (0,7%). Baseline z całej historii jest słaby tam, gdzie poziom statystyki dryfuje (strzały i kartki zmieniają się z sezonu na sezon), dlatego dodatkowo porównujemy z częstością z ostatnich 365 dni ligi; ta liczba jest uczciwszą miarą umiejętności.

Jak to czytać: różnica log-loss to strata baseline'u minus strata modelu, więc dodatnia liczba oznacza przewagę modelu. Model jest gotowy do zestawienia z kursami z kolektora, ale ten raport kursów nie używa.

## Jak mierzyliśmy

- **Ten sam protokół co dla goli** (`evaluation/protocol.py`): sezony testowe 2022–2025, jeden model na ligę, dopasowywany od nowa przed każdą kolejką na meczach zakończonych co najmniej 3 godziny przed pierwszym gwizdkiem kolejki. Hiperparametry wybieramy na jednym sezonie przed sezonem testowym (łącznie dla wszystkich lig, osobno dla każdej linii).
- **Co model widzi.** Przy prognozie meczu wszystkie liczniki tego meczu (rożne, kartki, strzały, faule, xG) są ukryte, nie tylko ten, który prognozujemy. Do formy używamy wyłącznie wcześniejszych meczów.
- **Baseline.** Dla każdej linii osobno: częstość przekroczenia tej linii w danej lidze, policzona na danych treningowych (wygładzanie Laplace'a). To odpowiednik częstości ligowych z bramek.
- **Metryki.** Główna to log-loss. Przedziały ufności: bootstrap po kolejkach (2 000 losowań). Test Diebolda–Mariano z wariancją HAC po kolejkach oraz korekty Holma i Benjaminiego–Hochberga liczone między ligami, osobno dla każdej linii. Kalibracja: rozkład CORP (MCB − DSC + UNC), p-wartość zbiorcza z 200 symulacji.
- **Uwaga o wyborze konstrukcji modelu.** Siatka hiperparametrów (kara, półokres) jest wybierana zgodnie z protokołem, na sezonie przed sezonem testowym. Sama konstrukcja modelu (okna 5/15/45/90, cechy pomocnicze, forma obu stron) została natomiast wybrana w przebiegach roboczych na ligach i sezonach testowych 2022–2025, czyli na tych samych wynikach, które oceniamy. Tabela ablacji niżej pokazuje, że alternatywne konstrukcje różnią się od końcowej o około 0,001–0,005 w log-loss (bez xG i bez cech pomocniczych też biją baseline). Przewaga nad baseline'em jest zwykle o rząd wielkości większa, ale dla sum rożnych (około +0,005) jest tego samego rzędu, więc tę liczbę traktujemy jako lekko zawyżoną.

## Statystyki, linie i model

| Statystyka | Definicja | Linie: mecz | Linie: drużyna |
|---|---|---|---|
| Rzuty rożne | rzuty rożne z `team_match_stats` | 8,5, 9,5, 10,5, 11,5 | 3,5, 4,5, 5,5 (gospodarz i gość osobno) |
| Żółte kartki | żółte kartki z `team_match_stats`; przy pustym polu liczba kartek z feedu zdarzeń, ale tylko w liga-sezonach, w których feed zapisuje kartki (niżej) | 2,5, 3,5, 4,5, 5,5 | 1,5, 2,5 (gospodarz i gość osobno) |
| Strzały | wszystkie strzały (`total_shots`) | 23,5, 25,5, 27,5 | 10,5, 12,5, 14,5 (gospodarz i gość osobno) |
| Strzały celne | strzały celne (`shots_on_goal`) | 7,5, 8,5, 9,5 | 3,5, 4,5 (gospodarz i gość osobno) |

**Model.** Każdy mecz to dwa wiersze (drużyna, rywal, gospodarz albo gość). Logarytm oczekiwanej liczby zależy liniowo od: formy własnej i rywala (średnia z ostatnich 5, 15, 45 i 90 meczów, osobno zdobyte i stracone, wygładzona do średniej ligi), formy pomocniczej (strzały dla rożnych i strzałów celnych, faule dla kartek, gole dla wszystkich statystyk), tendencji sędziego (średnia sumy z jego wcześniejszych meczów w lidze, wygładzona) oraz dni odpoczynku obu drużyn. Współczynniki są karane grzbietowo i opcjonalnie ważone czasem (półokres 1 000 dni albo brak zaniku). Wariant **Poisson** zakłada rozkład Poissona, wariant **NB2** rozkład ujemny dwumianowy z jedną dyspersją na ligę (największa wiarygodność). Suma meczu to NB2 ze średnią równą sumie średnich i własną dyspersją sumy.

**Żółte kartki.** Dostawca zostawia pole `yellow_cards` puste u większości drużyn bez kartki. Odrzucenie takich meczów usunęłoby właśnie mecze o małej liczbie kartek, więc przy pustym polu bierzemy liczbę zdarzeń „Yellow Card” z feedu zdarzeń meczu (gdy feed istnieje; bez feedu wartość zostaje pusta). Tam, gdzie puste pole oznacza prawdziwą lukę, feed podaje kartki. Tam, gdzie istnieją oba źródła, zgadzają się w 98% przypadków. **Poprawka po recenzji:** w części liga-sezonów (głównie 2015–2018, ale też 2020–2022 w SUPERLIGA) feed istnieje, lecz nie zapisuje kartek, więc „feed bez kartki” oznaczałoby tam zero kartek w każdym meczu (np. EERSTEDIVISIE 2017: średnio 0,0 kartki na mecz). Feed uznajemy za zapisujący kartki tylko wtedy, gdy co najmniej 90% zakończonych meczów liga-sezonu z feedem ma choć jedno zdarzenie kartkowe (w prawdziwych liga-sezonach jest to 93–99%). W pozostałych liga-sezonach zdarzenia są ignorowane, a wartość to pole dostawcy albo brak. Zero fauli obu drużyn naraz to znacznik braku danych (prawdziwy mecz ma faule), więc jest traktowane jako brak.

## Które ligi i sezony weszły do oceny

Reguła włączenia: liga-sezon wchodzi do statystyki tylko wtedy, gdy co najmniej 90% zakończonych meczów ma wartość dla obu drużyn (ten sam próg 90% co lista „Gaps” w `quality_report.md`, tu liczony dla sezonu, nie dla przedziału czteroletniego). W pozostałych liga-sezonach statystyka zostaje pusta i nigdy nie jest liczona jako zero. Mecz z brakiem u jednej drużyny odpada z całej rodziny linii, więc wszystkie linie jednej statystyki oceniamy na tych samych meczach.

| Statystyka | Ligi w ocenie | Mecze testowe | Wykluczone liga-sezony w latach testowych | Ligi bez żadnego sezonu testowego |
|---|---:|---:|---|---|
| Rzuty rożne | 36 | 40 923 | 55 z 184 | 1DIVISION, 1SNL, 2LIGA, 3LIGA, FIRSTLEAGUE, FIRSTLEAGUE2, LIGATHAAL, PREMIERLEAGUE, PREMIERLEAGUE3, SEGUNDALIGA, VEIKKAUSLIIGA |
| Żółte kartki | 47 | 55 010 | 1 z 184 | — |
| Strzały | 35 | 40 496 | 57 z 184 | 1DIVISION, 1SNL, 2LIGA, 3LIGA, FIRSTLEAGUE, FIRSTLEAGUE2, LIGATHAAL, NBI, PREMIERLEAGUE, PREMIERLEAGUE3, SEGUNDALIGA, VEIKKAUSLIIGA |
| Strzały celne | 35 | 40 486 | 57 z 184 | 1DIVISION, 1SNL, 2LIGA, 3LIGA, FIRSTLEAGUE, FIRSTLEAGUE2, LIGATHAAL, NBI, PREMIERLEAGUE, PREMIERLEAGUE3, SEGUNDALIGA, VEIKKAUSLIIGA |

## Wynik zbiorczy

Log-loss baseline'u z całej historii (Hist.), baseline'u z ostatnich 365 dni (365 d), Poissona i NB2 na tych samych meczach. Różnica to strata baseline'u minus strata NB2 (dodatnia = model lepszy) z przedziałem 95% (bootstrap po kolejkach). Ostatnia kolumna: liczba lig, w których NB2 ma niższą stratę niż baseline z historii, w nawiasie ile z nich istotnie po korekcie Holma (5%).

### Rzuty rożne

| Linia | Mecze | Hist. | 365 d | Poisson | NB2 | Różnica vs Hist. [95% CI] | Różnica vs 365 d [95% CI] | Ligi lepsze (Holm) |
|---|---:|---:|---:|---:|---:|---|---|---|
| mecz 8,5 | 40 923 | 0,6567 | 0,6566 | 0,6525 | 0,6517 | +0,0050 [+0,0039; +0,0060] | +0,0049 [+0,0039; +0,0060] | 32 z 36 (3) |
| mecz 9,5 | 40 923 | 0,6879 | 0,6879 | 0,6839 | 0,6834 | +0,0045 [+0,0034; +0,0056] | +0,0045 [+0,0034; +0,0055] | 32 z 36 (2) |
| mecz 10,5 | 40 923 | 0,6645 | 0,6645 | 0,6599 | 0,6597 | +0,0048 [+0,0038; +0,0059] | +0,0048 [+0,0038; +0,0058] | 30 z 36 (3) |
| mecz 11,5 | 40 923 | 0,5971 | 0,5970 | 0,5923 | 0,5922 | +0,0049 [+0,0039; +0,0059] | +0,0049 [+0,0039; +0,0059] | 29 z 36 (4) |
| gospodarz 3,5 | 40 923 | 0,5965 | 0,5973 | 0,5752 | 0,5696 | +0,0270 [+0,0249; +0,0289] | +0,0277 [+0,0255; +0,0297] | 35 z 36 (21) |
| gospodarz 4,5 | 40 923 | 0,6817 | 0,6823 | 0,6544 | 0,6516 | +0,0301 [+0,0279; +0,0324] | +0,0307 [+0,0284; +0,0329] | 36 z 36 (28) |
| gospodarz 5,5 | 40 923 | 0,6819 | 0,6823 | 0,6522 | 0,6511 | +0,0308 [+0,0285; +0,0331] | +0,0312 [+0,0289; +0,0335] | 36 z 36 (28) |
| gość 3,5 | 40 923 | 0,6739 | 0,6747 | 0,6475 | 0,6434 | +0,0305 [+0,0283; +0,0327] | +0,0313 [+0,0290; +0,0336] | 36 z 36 (30) |
| gość 4,5 | 40 923 | 0,6829 | 0,6835 | 0,6534 | 0,6526 | +0,0303 [+0,0282; +0,0325] | +0,0309 [+0,0287; +0,0332] | 36 z 36 (30) |
| gość 5,5 | 40 923 | 0,6100 | 0,6107 | 0,5808 | 0,5807 | +0,0293 [+0,0271; +0,0316] | +0,0300 [+0,0278; +0,0322] | 36 z 36 (26) |

### Żółte kartki

| Linia | Mecze | Hist. | 365 d | Poisson | NB2 | Różnica vs Hist. [95% CI] | Różnica vs 365 d [95% CI] | Ligi lepsze (Holm) |
|---|---:|---:|---:|---:|---:|---|---|---|
| mecz 2,5 | 55 010 | 0,5256 | 0,5213 | 0,5132 | 0,5130 | +0,0126 [+0,0111; +0,0141] | +0,0083 [+0,0072; +0,0095] | 44 z 47 (10) |
| mecz 3,5 | 55 010 | 0,6607 | 0,6541 | 0,6440 | 0,6439 | +0,0167 [+0,0150; +0,0185] | +0,0101 [+0,0088; +0,0115] | 44 z 47 (11) |
| mecz 4,5 | 55 010 | 0,6655 | 0,6589 | 0,6479 | 0,6478 | +0,0177 [+0,0159; +0,0195] | +0,0111 [+0,0097; +0,0124] | 45 z 47 (14) |
| mecz 5,5 | 55 010 | 0,5599 | 0,5541 | 0,5449 | 0,5448 | +0,0151 [+0,0134; +0,0166] | +0,0093 [+0,0081; +0,0105] | 45 z 47 (12) |
| gospodarz 1,5 | 55 010 | 0,6664 | 0,6640 | 0,6518 | 0,6519 | +0,0146 [+0,0131; +0,0161] | +0,0121 [+0,0109; +0,0133] | 47 z 47 (15) |
| gospodarz 2,5 | 55 010 | 0,6183 | 0,6153 | 0,6022 | 0,6022 | +0,0161 [+0,0146; +0,0176] | +0,0131 [+0,0117; +0,0144] | 47 z 47 (21) |
| gość 1,5 | 55 010 | 0,6347 | 0,6313 | 0,6219 | 0,6220 | +0,0127 [+0,0113; +0,0142] | +0,0093 [+0,0080; +0,0107] | 45 z 47 (12) |
| gość 2,5 | 55 010 | 0,6606 | 0,6565 | 0,6451 | 0,6451 | +0,0155 [+0,0139; +0,0170] | +0,0114 [+0,0100; +0,0127] | 46 z 47 (16) |

### Strzały

| Linia | Mecze | Hist. | 365 d | Poisson | NB2 | Różnica vs Hist. [95% CI] | Różnica vs 365 d [95% CI] | Ligi lepsze (Holm) |
|---|---:|---:|---:|---:|---:|---|---|---|
| mecz 23,5 | 40 496 | 0,6990 | 0,6655 | 0,6438 | 0,6427 | +0,0563 [+0,0523; +0,0602] | +0,0228 [+0,0208; +0,0248] | 34 z 35 (31) |
| mecz 25,5 | 40 496 | 0,7049 | 0,6756 | 0,6539 | 0,6531 | +0,0518 [+0,0478; +0,0556] | +0,0225 [+0,0203; +0,0246] | 34 z 35 (31) |
| mecz 27,5 | 40 496 | 0,6419 | 0,6191 | 0,6003 | 0,5991 | +0,0427 [+0,0394; +0,0462] | +0,0200 [+0,0179; +0,0221] | 34 z 35 (28) |
| gospodarz 10,5 | 40 496 | 0,6164 | 0,6019 | 0,5514 | 0,5432 | +0,0731 [+0,0696; +0,0767] | +0,0587 [+0,0556; +0,0618] | 34 z 35 (34) |
| gospodarz 12,5 | 40 496 | 0,7012 | 0,6847 | 0,6207 | 0,6157 | +0,0855 [+0,0814; +0,0894] | +0,0690 [+0,0656; +0,0723] | 35 z 35 (34) |
| gospodarz 14,5 | 40 496 | 0,6857 | 0,6706 | 0,6059 | 0,6009 | +0,0848 [+0,0809; +0,0888] | +0,0697 [+0,0663; +0,0730] | 35 z 35 (34) |
| gość 10,5 | 40 496 | 0,6995 | 0,6865 | 0,6280 | 0,6236 | +0,0759 [+0,0720; +0,0794] | +0,0629 [+0,0593; +0,0660] | 35 z 35 (35) |
| gość 12,5 | 40 496 | 0,6677 | 0,6554 | 0,5965 | 0,5930 | +0,0746 [+0,0707; +0,0782] | +0,0624 [+0,0590; +0,0656] | 35 z 35 (35) |
| gość 14,5 | 40 496 | 0,5523 | 0,5431 | 0,4959 | 0,4892 | +0,0630 [+0,0592; +0,0668] | +0,0539 [+0,0506; +0,0573] | 35 z 35 (33) |

### Strzały celne

| Linia | Mecze | Hist. | 365 d | Poisson | NB2 | Różnica vs Hist. [95% CI] | Różnica vs 365 d [95% CI] | Ligi lepsze (Holm) |
|---|---:|---:|---:|---:|---:|---|---|---|
| mecz 7,5 | 40 486 | 0,6621 | 0,6609 | 0,6480 | 0,6478 | +0,0143 [+0,0128; +0,0158] | +0,0131 [+0,0118; +0,0145] | 35 z 35 (18) |
| mecz 8,5 | 40 486 | 0,6859 | 0,6844 | 0,6706 | 0,6706 | +0,0153 [+0,0137; +0,0168] | +0,0138 [+0,0124; +0,0152] | 34 z 35 (21) |
| mecz 9,5 | 40 486 | 0,6462 | 0,6445 | 0,6304 | 0,6305 | +0,0157 [+0,0141; +0,0173] | +0,0140 [+0,0125; +0,0154] | 34 z 35 (18) |
| gospodarz 3,5 | 40 486 | 0,6451 | 0,6449 | 0,6043 | 0,6041 | +0,0410 [+0,0383; +0,0437] | +0,0408 [+0,0381; +0,0434] | 35 z 35 (32) |
| gospodarz 4,5 | 40 486 | 0,6905 | 0,6902 | 0,6426 | 0,6427 | +0,0477 [+0,0450; +0,0506] | +0,0475 [+0,0447; +0,0503] | 35 z 35 (33) |
| gość 3,5 | 40 486 | 0,6897 | 0,6897 | 0,6511 | 0,6504 | +0,0393 [+0,0368; +0,0420] | +0,0393 [+0,0367; +0,0420] | 35 z 35 (31) |
| gość 4,5 | 40 486 | 0,6428 | 0,6427 | 0,6032 | 0,6032 | +0,0396 [+0,0371; +0,0422] | +0,0394 [+0,0369; +0,0420] | 35 z 35 (30) |

Względem baseline'u z 365 dni NB2 jest istotnie lepszy po korekcie Holma w 728 z 1296 par liga-linia, a istotnie gorszy w 0. Baseline z 365 dni ma mniej danych (jeden sezon), więc jest szumniejszy, ale nie ma dryfu.

**Poisson czy NB2.** NB2 ma niższy log-loss niż Poisson w 28 z 34 linii. Średnia przewaga NB2: gość +0,0020, gospodarz +0,0028, mecz +0,0004. Liczniki są bardziej rozrzucone, niż zakłada Poisson (wariancja większa od średniej, tabela niżej), więc Poisson zawyża prawdopodobieństwo „over” na liniach poniżej średniej.

### Dlaczego suma jest trudniejsza niż drużyny

Liczniki obu drużyn w jednym meczu nie są niezależne. Tabela pokazuje średnie ważone po ligach w sezonach testowych: średnią sumy, stosunek wariancji do średniej (dla Poissona równy 1) i korelację liczby gospodarza z liczbą gościa.

| Statystyka | Średnia sumy | Wariancja / średnia (suma) | Wariancja / średnia (gospodarz) | Korelacja gospodarz–gość |
|---|---:|---:|---:|---:|
| Rzuty rożne | 9,79 | 1,16 | 1,58 | -0,26 |
| Żółte kartki | 4,22 | 1,07 | 0,94 | 0,15 |
| Strzały | 25,09 | 1,39 | 2,03 | -0,31 |
| Strzały celne | 8,58 | 1,15 | 1,32 | -0,13 |

Ujemna korelacja (rzuty rożne, strzały, strzały celne) oznacza, że gdy jedna drużyna dominuje, druga ma mniej. Model wie, kto dominuje, więc dobrze przewiduje każdą drużynę osobno, ale suma zmienia się dużo mniej niż jej składniki. Dodatnia korelacja (żółte kartki) oznacza, że ostry albo szybki mecz podnosi oba liczniki naraz; model łapie to przez tendencję sędziego i formę obu drużyn.

## Wynik w ligach

Różnica log-loss baseline minus NB2 w każdej lidze i linii (dodatnia = model lepszy). Gwiazdka oznacza istotną przewagę modelu po korekcie Holma (5%, korekta liczona między ligami dla danej linii), krzyżyk istotną przewagę baseline'u. Kolumny: M to suma meczu, G gospodarz, Go gość. Pełne tabele z przedziałami ufności, korektą Benjaminiego–Hochberga i statystyką DM są w `predictions/count_evaluation.sqlite` (tabela `by_league`).

### Rzuty rożne

| Liga | Mecze | M 8,5 | M 9,5 | M 10,5 | M 11,5 | G 3,5 | G 4,5 | G 5,5 | Go 3,5 | Go 4,5 | Go 5,5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1LIG | 1 361 | +0,002 | +0,001 | +0,002 | +0,003 | +0,048* | +0,043* | +0,039* | +0,044* | +0,042* | +0,038* |
| ALLSVENSKAN | 968 | +0,005 | +0,006 | +0,004 | +0,003 | +0,029* | +0,027* | +0,030* | +0,029* | +0,032* | +0,032* |
| AUSTRIA | 779 | +0,006 | +0,005 | +0,001 | -0,000 | +0,023 | +0,034* | +0,036* | +0,031* | +0,034* | +0,040* |
| BELGIUM | 1 272 | +0,003 | +0,005 | +0,004 | +0,004 | +0,010 | +0,021* | +0,027* | +0,024* | +0,027* | +0,024* |
| BUNDESLIGA | 1 230 | +0,002 | +0,004 | +0,006 | +0,004 | +0,040* | +0,046* | +0,044* | +0,020* | +0,025* | +0,028* |
| BUNDESLIGA2 | 1 230 | +0,003 | +0,003 | +0,001 | -0,001 | +0,011 | +0,009 | +0,008 | +0,011* | +0,011* | +0,006 |
| CHALLENGERPROL | 168 | -0,002 | +0,003 | +0,002 | +0,004 | +0,007 | +0,015 | +0,010 | +0,021 | +0,015 | +0,010 |
| CHAMPIONSHIP | 2 222 | -0,001 | -0,001 | +0,002 | +0,002 | +0,020* | +0,021* | +0,022* | +0,026* | +0,026* | +0,024* |
| CZECHLIGA | 1 077 | +0,000 | -0,005 | -0,003 | -0,000 | +0,035* | +0,039* | +0,039* | +0,056* | +0,048* | +0,050* |
| DENMARK | 771 | +0,007 | +0,009 | +0,005 | +0,007 | +0,014 | +0,018 | +0,022* | +0,024* | +0,025* | +0,013 |
| EERSTEDIVISIE | 1 519 | +0,002 | +0,002 | +0,004 | +0,004 | +0,033* | +0,040* | +0,034* | +0,034* | +0,033* | +0,031* |
| EKSTRAKLASA | 1 215 | +0,001 | +0,002 | +0,004 | +0,002 | +0,020* | +0,022* | +0,020* | +0,021* | +0,022* | +0,024* |
| ELITESERIEN | 965 | +0,017* | +0,019* | +0,023* | +0,018* | +0,041* | +0,042* | +0,053* | +0,033* | +0,031* | +0,041* |
| EPL | 1 520 | +0,004 | +0,004 | +0,005 | +0,008 | +0,038* | +0,049* | +0,057* | +0,043* | +0,046* | +0,037* |
| EREDIVISIE | 1 265 | +0,002 | +0,001 | +0,002 | +0,001 | +0,048* | +0,052* | +0,063* | +0,062* | +0,070* | +0,062* |
| GREECE | 948 | -0,004 | -0,002 | -0,004 | -0,007 | +0,079* | +0,085* | +0,074* | +0,074* | +0,068* | +0,069* |
| HNL | 707 | +0,014* | +0,004 | -0,000 | -0,003 | +0,023 | +0,035* | +0,026* | +0,033* | +0,031* | +0,036* |
| LALIGA | 1 520 | +0,008 | +0,007 | +0,008 | +0,009* | +0,016* | +0,021* | +0,021* | +0,024* | +0,027* | +0,027* |
| LALIGA2 | 1 870 | +0,000 | +0,001 | +0,002 | +0,003 | +0,006 | +0,007 | +0,007 | +0,010* | +0,013* | +0,013* |
| LEAGUEONE | 2 225 | +0,003 | +0,002 | +0,005 | +0,003 | +0,021* | +0,018* | +0,020* | +0,021* | +0,020* | +0,020* |
| LEAGUETWO | 2 222 | +0,006 | +0,007 | +0,006 | +0,008* | +0,014* | +0,015* | +0,016* | +0,024* | +0,016* | +0,020* |
| LIGAI | 941 | +0,005 | +0,005 | +0,003 | +0,003 | +0,011 | +0,022* | +0,027* | +0,017 | +0,017 | +0,017 |
| LIGUE1 | 1 301 | +0,004 | +0,002 | -0,001 | +0,001 | +0,033* | +0,024* | +0,025* | +0,029* | +0,027* | +0,028* |
| LIGUE2 | 1 373 | +0,004 | +0,003 | +0,006 | +0,008 | +0,014* | +0,016* | +0,011 | +0,012* | +0,011* | +0,011 |
| NBI | 129 | +0,003 | +0,004 | +0,006 | +0,006 | +0,022 | +0,042 | +0,052* | +0,034 | +0,027 | +0,022 |
| PREMIERDIVISIO | 292 | +0,011 | +0,013 | +0,013 | +0,012 | +0,011 | +0,026* | +0,030* | +0,039 | +0,043* | +0,034* |
| PREMIERLEAGUE2 | 971 | +0,007 | +0,005 | +0,004 | -0,000 | +0,013 | +0,021* | +0,013 | +0,019* | +0,021* | +0,021* |
| PRIMEIRA | 1 231 | +0,012 | +0,010 | +0,012* | +0,010 | +0,073* | +0,075* | +0,078* | +0,067* | +0,059* | +0,053* |
| SCOTLAND | 914 | +0,008 | +0,008 | +0,010 | +0,009 | +0,065* | +0,069* | +0,064* | +0,062* | +0,059* | +0,054* |
| SERIEA | 1 520 | +0,030* | +0,026* | +0,023* | +0,027* | +0,037* | +0,044* | +0,043* | +0,038* | +0,042* | +0,045* |
| SERIEB | 1 557 | +0,005 | +0,004 | +0,005 | +0,004 | +0,008 | +0,008 | +0,009 | +0,012* | +0,007 | +0,009 |
| SUPERETTAN | 235 | -0,003 | -0,001 | -0,003 | +0,006 | -0,000 | +0,008 | +0,002 | +0,019 | +0,021 | +0,027 |
| SUPERLIG | 1 340 | +0,003 | +0,002 | +0,001 | +0,005 | +0,023* | +0,022* | +0,028* | +0,032* | +0,037* | +0,035* |
| SUPERLIGA | 822 | +0,005 | +0,005 | +0,006 | +0,008 | +0,039* | +0,045* | +0,046* | +0,043* | +0,036* | +0,027 |
| SUPERLIGA2 | 379 | +0,002 | +0,002 | -0,001 | +0,001 | +0,010 | +0,017 | +0,027 | +0,012 | +0,016 | +0,018 |
| SWITZERLAND | 864 | +0,001 | +0,003 | +0,001 | -0,000 | +0,012 | +0,018* | +0,020* | +0,017* | +0,024* | +0,026* |

### Żółte kartki

| Liga | Mecze | M 2,5 | M 3,5 | M 4,5 | M 5,5 | G 1,5 | G 2,5 | Go 1,5 | Go 2,5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1DIVISION | 1 013 | +0,003 | -0,002 | -0,000 | -0,009 | +0,012 | +0,018 | +0,010 | +0,006 |
| 1LIG | 1 393 | +0,008 | +0,008 | +0,011 | +0,009 | +0,008 | +0,008 | +0,007 | +0,007 |
| 1SNL | 361 | -0,005 | -0,002 | +0,004 | +0,006 | +0,001 | +0,001 | -0,002 | -0,002 |
| 2LIGA | 942 | +0,006 | +0,010 | +0,018 | +0,021 | +0,009 | +0,008 | +0,007 | +0,012 |
| 3LIGA | 1 520 | +0,006 | +0,004 | +0,015* | +0,011 | +0,007 | +0,009 | +0,007 | +0,012 |
| ALLSVENSKAN | 968 | +0,014 | +0,021* | +0,020 | +0,014 | +0,017* | +0,023* | +0,013 | +0,012 |
| AUSTRIA | 779 | +0,007 | +0,018 | +0,016 | +0,014 | +0,016* | +0,007 | +0,006 | +0,004 |
| BELGIUM | 1 275 | +0,013 | +0,013 | +0,012 | +0,010 | +0,019* | +0,025* | +0,016* | +0,012 |
| BUNDESLIGA | 1 230 | +0,008 | +0,010 | +0,005 | +0,007 | +0,008 | +0,019* | +0,008 | +0,008 |
| BUNDESLIGA2 | 1 231 | +0,002 | +0,009 | +0,013 | +0,009 | +0,004 | +0,011 | +0,004 | +0,015 |
| CHALLENGERPROL | 692 | +0,010 | +0,018 | +0,022 | +0,023* | +0,011 | +0,016 | +0,012 | +0,018 |
| CHAMPIONSHIP | 2 225 | +0,014* | +0,013 | +0,015 | +0,010 | +0,015* | +0,009 | +0,018* | +0,011 |
| CZECHLIGA | 1 117 | +0,003 | +0,001 | +0,008 | +0,009 | +0,011 | +0,018* | +0,008 | +0,007 |
| DENMARK | 771 | +0,022* | +0,018 | +0,015 | +0,013 | +0,022* | +0,014 | +0,014 | +0,017 |
| EERSTEDIVISIE | 1 530 | +0,003 | +0,006 | +0,006 | +0,007 | +0,005 | +0,006 | +0,005 | +0,003 |
| EKSTRAKLASA | 1 224 | +0,012 | +0,014* | +0,007 | +0,009 | +0,012 | +0,009 | +0,009 | +0,007 |
| ELITESERIEN | 966 | +0,014 | +0,017 | +0,018* | +0,018* | +0,025* | +0,018 | +0,014 | +0,017* |
| EPL | 1 520 | +0,023* | +0,034* | +0,031 | +0,021 | +0,019 | +0,017 | +0,038* | +0,033* |
| EREDIVISIE | 1 265 | -0,003 | -0,000 | -0,002 | -0,000 | +0,012 | +0,004 | +0,000 | +0,003 |
| FIRSTLEAGUE | 1 153 | +0,004 | +0,008 | +0,005 | +0,011 | +0,016 | +0,021* | +0,006 | +0,012 |
| FIRSTLEAGUE2 | 1 223 | +0,038* | +0,065* | +0,069* | +0,063* | +0,034* | +0,041* | +0,035* | +0,043* |
| GREECE | 952 | -0,006 | +0,004 | +0,004 | +0,002 | +0,020* | +0,022* | +0,009 | +0,014 |
| HNL | 720 | +0,006 | +0,004 | +0,002 | +0,011 | +0,018 | +0,025* | +0,011 | +0,006 |
| LALIGA | 1 520 | +0,018 | +0,024 | +0,028* | +0,023 | +0,014 | +0,019* | +0,026* | +0,018 |
| LALIGA2 | 1 871 | +0,004 | +0,008 | +0,007 | +0,011 | +0,003 | +0,007 | +0,009 | +0,014* |
| LEAGUEONE | 2 226 | +0,015* | +0,021* | +0,021* | +0,016* | +0,016* | +0,018* | +0,019* | +0,024* |
| LEAGUETWO | 2 223 | +0,031* | +0,032* | +0,035* | +0,024* | +0,019* | +0,016* | +0,027* | +0,022* |
| LIGAI | 1 275 | +0,008 | +0,018 | +0,013 | +0,008 | +0,013 | +0,015* | +0,005 | +0,006 |
| LIGATHAAL | 958 | +0,009 | +0,021* | +0,026* | +0,017* | +0,011 | +0,022* | +0,007 | +0,013 |
| LIGUE1 | 1 301 | +0,014 | +0,010 | +0,013 | +0,010 | +0,007 | +0,004 | +0,004 | +0,006 |
| LIGUE2 | 1 378 | +0,008 | +0,010 | +0,008 | +0,009 | +0,005 | +0,013* | +0,001 | +0,009 |
| NBI | 792 | +0,013 | +0,018 | +0,019 | +0,007 | +0,011 | +0,006 | +0,010 | +0,021* |
| PREMIERDIVISIO | 492 | +0,009 | +0,020 | +0,009 | +0,003 | +0,014 | +0,025* | +0,016 | +0,016 |
| PREMIERLEAGUE | 938 | +0,013 | +0,031* | +0,035* | +0,028* | +0,018 | +0,013 | +0,016 | +0,024* |
| PREMIERLEAGUE2 | 976 | +0,012 | +0,012 | +0,008 | +0,008 | +0,008 | +0,005 | +0,019* | +0,021* |
| PREMIERLEAGUE3 | 974 | +0,037* | +0,052* | +0,057* | +0,054* | +0,023 | +0,034* | +0,039* | +0,049* |
| PRIMEIRA | 1 231 | +0,004 | +0,008 | +0,008 | +0,008 | +0,019* | +0,022* | +0,006 | +0,015 |
| SCOTLAND | 934 | +0,014 | +0,015 | +0,012 | +0,002 | +0,029* | +0,020 | +0,021* | +0,016 |
| SEGUNDALIGA | 1 231 | +0,006 | +0,009 | +0,011 | +0,011 | +0,002 | +0,006 | -0,003 | +0,004 |
| SERIEA | 1 521 | +0,032* | +0,053* | +0,055* | +0,044* | +0,046* | +0,040* | +0,024* | +0,037* |
| SERIEB | 1 558 | +0,009 | +0,012 | +0,017* | +0,019* | +0,010 | +0,015* | +0,003 | +0,013* |
| SUPERETTAN | 974 | +0,026* | +0,032* | +0,029* | +0,029* | +0,027* | +0,042* | +0,013 | +0,025* |
| SUPERLIG | 1 341 | +0,010 | +0,009 | +0,007 | +0,013 | +0,006 | +0,007 | +0,011 | +0,018* |
| SUPERLIGA | 896 | +0,016* | +0,011 | +0,014 | +0,012 | +0,021* | +0,023* | +0,010 | +0,010 |
| SUPERLIGA2 | 779 | +0,020 | +0,025 | +0,025* | +0,016 | +0,015 | +0,018 | +0,017* | +0,025* |
| SWITZERLAND | 871 | +0,019 | +0,014 | +0,018* | +0,020* | +0,010 | +0,012 | +0,006 | +0,017* |
| VEIKKAUSLIIGA | 680 | +0,011 | +0,006 | +0,012 | +0,007 | +0,018 | +0,017 | +0,005 | +0,009 |

### Strzały

| Liga | Mecze | M 23,5 | M 25,5 | M 27,5 | G 10,5 | G 12,5 | G 14,5 | Go 10,5 | Go 12,5 | Go 14,5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1LIG | 1 363 | +0,016* | +0,020* | +0,011 | +0,071* | +0,080* | +0,072* | +0,073* | +0,066* | +0,061* |
| ALLSVENSKAN | 968 | +0,026* | +0,029* | +0,030* | +0,083* | +0,098* | +0,090* | +0,044* | +0,056* | +0,040* |
| AUSTRIA | 779 | +0,026* | +0,026* | +0,028* | +0,055* | +0,068* | +0,080* | +0,036* | +0,046* | +0,057* |
| BELGIUM | 1 272 | +0,027* | +0,032* | +0,029* | +0,079* | +0,086* | +0,087* | +0,067* | +0,058* | +0,051* |
| BUNDESLIGA | 1 230 | +0,028* | +0,026* | +0,025* | +0,049* | +0,071* | +0,072* | +0,057* | +0,056* | +0,039* |
| BUNDESLIGA2 | 1 230 | +0,033* | +0,025* | +0,014 | +0,036* | +0,030* | +0,033* | +0,037* | +0,020* | +0,016 |
| CHALLENGERPROL | 168 | -0,031 | -0,033† | -0,010 | -0,003 | +0,016 | +0,020 | +0,064* | +0,049* | +0,045* |
| CHAMPIONSHIP | 2 223 | +0,014* | +0,018* | +0,018* | +0,038* | +0,050* | +0,048* | +0,048* | +0,038* | +0,035* |
| CZECHLIGA | 821 | +0,315* | +0,216* | +0,167* | +0,193* | +0,209* | +0,200* | +0,199* | +0,208* | +0,156* |
| DENMARK | 771 | +0,047* | +0,038* | +0,037* | +0,047* | +0,075* | +0,087* | +0,078* | +0,053* | +0,046* |
| EERSTEDIVISIE | 1 519 | +0,023* | +0,028* | +0,033* | +0,066* | +0,089* | +0,094* | +0,064* | +0,081* | +0,069* |
| EKSTRAKLASA | 1 215 | +0,256* | +0,240* | +0,170* | +0,127* | +0,169* | +0,151* | +0,121* | +0,121* | +0,126* |
| ELITESERIEN | 966 | +0,054* | +0,063* | +0,054* | +0,077* | +0,085* | +0,093* | +0,067* | +0,082* | +0,076* |
| EPL | 1 520 | +0,016 | +0,020* | +0,018 | +0,079* | +0,106* | +0,090* | +0,092* | +0,096* | +0,082* |
| EREDIVISIE | 1 265 | +0,016* | +0,022* | +0,024* | +0,080* | +0,108* | +0,132* | +0,121* | +0,118* | +0,120* |
| GREECE | 948 | +0,239* | +0,177* | +0,117* | +0,266* | +0,276* | +0,251* | +0,225* | +0,215* | +0,162* |
| HNL | 708 | +0,015 | +0,009 | +0,012 | +0,091* | +0,100* | +0,097* | +0,097* | +0,082* | +0,065* |
| LALIGA | 1 520 | +0,046* | +0,046* | +0,036* | +0,056* | +0,073* | +0,086* | +0,050* | +0,046* | +0,042* |
| LALIGA2 | 1 870 | +0,034* | +0,043* | +0,038* | +0,034* | +0,045* | +0,047* | +0,034* | +0,034* | +0,029* |
| LEAGUEONE | 2 225 | +0,022* | +0,018* | +0,019* | +0,044* | +0,048* | +0,045* | +0,058* | +0,050* | +0,035* |
| LEAGUETWO | 2 223 | +0,033* | +0,031* | +0,027* | +0,042* | +0,047* | +0,043* | +0,047* | +0,049* | +0,036* |
| LIGAI | 941 | +0,361* | +0,323* | +0,259* | +0,212* | +0,217* | +0,188* | +0,190* | +0,185* | +0,141* |
| LIGUE1 | 1 301 | +0,032* | +0,029* | +0,022* | +0,060* | +0,055* | +0,069* | +0,058* | +0,071* | +0,071* |
| LIGUE2 | 1 375 | +0,025* | +0,023* | +0,021* | +0,034* | +0,047* | +0,046* | +0,034* | +0,040* | +0,021* |
| PREMIERDIVISIO | 292 | +0,035* | +0,038* | +0,038* | +0,059* | +0,069* | +0,083* | +0,046* | +0,059* | +0,027 |
| PREMIERLEAGUE2 | 973 | +0,028* | +0,024* | +0,036* | +0,061* | +0,071* | +0,080* | +0,058* | +0,055* | +0,054* |
| PRIMEIRA | 1 231 | +0,037* | +0,033* | +0,017 | +0,101* | +0,115* | +0,116* | +0,114* | +0,120* | +0,099* |
| SCOTLAND | 912 | +0,029* | +0,014 | +0,013 | +0,109* | +0,115* | +0,134* | +0,132* | +0,131* | +0,117* |
| SERIEA | 1 520 | +0,024* | +0,032* | +0,032* | +0,065* | +0,084* | +0,078* | +0,071* | +0,074* | +0,062* |
| SERIEB | 1 558 | +0,074* | +0,067* | +0,044* | +0,055* | +0,056* | +0,057* | +0,050* | +0,043* | +0,036* |
| SUPERETTAN | 178 | +0,017 | +0,020 | +0,014* | +0,055* | +0,062* | +0,059* | +0,072* | +0,057* | +0,037* |
| SUPERLIG | 1 341 | +0,027* | +0,041* | +0,035* | +0,046* | +0,056* | +0,059* | +0,072* | +0,072* | +0,060* |
| SUPERLIGA | 825 | +0,032* | +0,037* | +0,038* | +0,094* | +0,107* | +0,111* | +0,092* | +0,101* | +0,102* |
| SUPERLIGA2 | 381 | +0,123* | +0,109* | +0,081* | +0,105* | +0,119* | +0,068* | +0,064* | +0,056* | +0,040* |
| SWITZERLAND | 864 | +0,060* | +0,052* | +0,058* | +0,062* | +0,065* | +0,065* | +0,086* | +0,072* | +0,049* |

### Strzały celne

| Liga | Mecze | M 7,5 | M 8,5 | M 9,5 | G 3,5 | G 4,5 | Go 3,5 | Go 4,5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1LIG | 1 363 | +0,012 | +0,014* | +0,019* | +0,042* | +0,048* | +0,050* | +0,048* |
| ALLSVENSKAN | 968 | +0,016* | +0,018* | +0,014 | +0,059* | +0,070* | +0,020 | +0,029* |
| AUSTRIA | 779 | +0,012 | +0,021* | +0,021* | +0,047* | +0,051* | +0,044* | +0,042* |
| BELGIUM | 1 272 | +0,009 | +0,010 | +0,009 | +0,044* | +0,060* | +0,039* | +0,035* |
| BUNDESLIGA | 1 230 | +0,014* | +0,022* | +0,027* | +0,048* | +0,058* | +0,048* | +0,046* |
| BUNDESLIGA2 | 1 230 | +0,002 | +0,004 | +0,005 | +0,015* | +0,018* | +0,014* | +0,012 |
| CHALLENGERPROL | 168 | +0,004 | -0,006 | -0,001 | +0,020 | +0,038 | +0,021 | +0,031 |
| CHAMPIONSHIP | 2 223 | +0,010* | +0,009 | +0,012* | +0,030* | +0,030* | +0,026* | +0,029* |
| CZECHLIGA | 815 | +0,020* | +0,018* | +0,020 | +0,060* | +0,081* | +0,043* | +0,035* |
| DENMARK | 771 | +0,007 | +0,008 | +0,011 | +0,036* | +0,033* | +0,030* | +0,042* |
| EERSTEDIVISIE | 1 519 | +0,017* | +0,016* | +0,019* | +0,044* | +0,050* | +0,037* | +0,040* |
| EKSTRAKLASA | 1 215 | +0,016* | +0,011* | +0,011 | +0,022* | +0,028* | +0,035* | +0,022* |
| ELITESERIEN | 966 | +0,022* | +0,018* | +0,020* | +0,042* | +0,054* | +0,043* | +0,042* |
| EPL | 1 520 | +0,010 | +0,012 | +0,010 | +0,058* | +0,051* | +0,053* | +0,055* |
| EREDIVISIE | 1 265 | +0,011 | +0,007 | +0,009 | +0,064* | +0,089* | +0,078* | +0,089* |
| GREECE | 948 | +0,004 | +0,007 | +0,004 | +0,078* | +0,090* | +0,074* | +0,083* |
| HNL | 708 | +0,018 | +0,017 | +0,019* | +0,044* | +0,070* | +0,069* | +0,049* |
| LALIGA | 1 520 | +0,020* | +0,021* | +0,019* | +0,058* | +0,056* | +0,041* | +0,034* |
| LALIGA2 | 1 870 | +0,025* | +0,025* | +0,027* | +0,021* | +0,025* | +0,021* | +0,023* |
| LEAGUEONE | 2 225 | +0,009* | +0,009* | +0,008 | +0,024* | +0,025* | +0,032* | +0,027* |
| LEAGUETWO | 2 223 | +0,015* | +0,016* | +0,017* | +0,027* | +0,031* | +0,026* | +0,027* |
| LIGAI | 942 | +0,016 | +0,020* | +0,015 | +0,034* | +0,036* | +0,034* | +0,023* |
| LIGUE1 | 1 301 | +0,025* | +0,028* | +0,031* | +0,045* | +0,043* | +0,061* | +0,058* |
| LIGUE2 | 1 375 | +0,012 | +0,013* | +0,009 | +0,015* | +0,019* | +0,020* | +0,022* |
| PREMIERDIVISIO | 292 | +0,006 | +0,005 | +0,007 | +0,016 | +0,037* | +0,016 | +0,017 |
| PREMIERLEAGUE2 | 973 | +0,027* | +0,029* | +0,031* | +0,045* | +0,078* | +0,043* | +0,041* |
| PRIMEIRA | 1 231 | +0,018* | +0,021* | +0,018* | +0,088* | +0,084* | +0,057* | +0,058* |
| SCOTLAND | 914 | +0,024* | +0,024* | +0,023* | +0,084* | +0,088* | +0,070* | +0,079* |
| SERIEA | 1 520 | +0,018* | +0,019* | +0,017* | +0,049* | +0,072* | +0,057* | +0,058* |
| SERIEB | 1 558 | +0,005 | +0,006 | +0,007* | +0,016* | +0,016* | +0,014* | +0,014* |
| SUPERETTAN | 172 | +0,002 | +0,008 | +0,009 | +0,042 | +0,036 | +0,040* | +0,033 |
| SUPERLIG | 1 341 | +0,016* | +0,018* | +0,019* | +0,037* | +0,052* | +0,029* | +0,052* |
| SUPERLIGA | 826 | +0,028* | +0,031* | +0,028* | +0,048* | +0,065* | +0,068* | +0,065* |
| SUPERLIGA2 | 379 | +0,003 | +0,007 | +0,004 | +0,037* | +0,032* | +0,013 | +0,013 |
| SWITZERLAND | 864 | +0,004 | +0,006 | +0,007 | +0,026* | +0,021* | +0,022* | +0,025* |

Łącznie po korekcie Holma NB2 jest istotnie lepszy od baseline'u w 764 z 1296 par liga-linia, a istotnie gorszy w 1. Po korekcie Benjaminiego–Hochberga: lepszy w 943, gorszy w 2.

## Kalibracja (CORP)

Rozkład CORP dzieli wynik Briera na MCB (miskalibracja, cena za prognozy niezgodne z częstością), DSC (zdolność odróżniania meczów) i UNC (niepewność samego wyniku): Brier = MCB − DSC + UNC. Wartości pomnożone przez 1000. Dobry model ma DSC dużo większe niż MCB. „Średnia p” to średnie prawdopodobieństwo „over”, „częstość” to odsetek meczów z „over”, obie pooled po wszystkich ligach.

### Rzuty rożne

| Linia | Częstość | Średnia p (NB2) | MCB baseline | DSC baseline | MCB NB2 | DSC NB2 | p(MCB) NB2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| mecz 8,5 | 0,625 | 0,628 | 0,19 | 2,43 | 0,33 | 4,88 | 0,567 |
| mecz 9,5 | 0,508 | 0,510 | 0,18 | 2,73 | 0,37 | 5,15 | 0,363 |
| mecz 10,5 | 0,393 | 0,395 | 0,21 | 2,74 | 0,31 | 5,13 | 0,886 |
| mecz 11,5 | 0,290 | 0,291 | 0,21 | 2,23 | 0,29 | 4,32 | 0,731 |
| gospodarz 3,5 | 0,714 | 0,713 | 0,20 | 0,97 | 0,42 | 12,16 | 0,085 |
| gospodarz 4,5 | 0,570 | 0,568 | 0,30 | 1,08 | 0,54 | 15,51 | 0,015 |
| gospodarz 5,5 | 0,431 | 0,426 | 0,24 | 1,11 | 0,55 | 15,88 | 0,010 |
| gość 3,5 | 0,594 | 0,604 | 0,25 | 1,02 | 0,54 | 15,47 | 0,015 |
| gość 4,5 | 0,436 | 0,443 | 0,26 | 1,29 | 0,53 | 15,92 | 0,025 |
| gość 5,5 | 0,301 | 0,305 | 0,21 | 0,96 | 0,38 | 13,11 | 0,582 |

### Żółte kartki

| Linia | Częstość | Średnia p (NB2) | MCB baseline | DSC baseline | MCB NB2 | DSC NB2 | p(MCB) NB2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| mecz 2,5 | 0,774 | 0,768 | 1,34 | 4,10 | 0,37 | 7,73 | 0,005 |
| mecz 3,5 | 0,601 | 0,589 | 2,02 | 7,77 | 0,55 | 14,12 | 0,005 |
| mecz 4,5 | 0,418 | 0,408 | 1,81 | 8,68 | 0,46 | 15,51 | 0,005 |
| mecz 5,5 | 0,260 | 0,258 | 1,18 | 6,17 | 0,28 | 10,89 | 0,592 |
| gospodarz 1,5 | 0,595 | 0,581 | 1,06 | 5,23 | 0,53 | 11,59 | 0,005 |
| gospodarz 2,5 | 0,321 | 0,321 | 0,79 | 4,87 | 0,32 | 11,20 | 0,373 |
| gość 1,5 | 0,661 | 0,639 | 1,18 | 3,57 | 0,79 | 8,93 | 0,005 |
| gość 2,5 | 0,388 | 0,380 | 1,09 | 4,51 | 0,33 | 10,78 | 0,433 |

### Strzały

| Linia | Częstość | Średnia p (NB2) | MCB baseline | DSC baseline | MCB NB2 | DSC NB2 | p(MCB) NB2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| mecz 23,5 | 0,587 | 0,584 | 11,69 | 3,40 | 0,55 | 17,12 | 0,020 |
| mecz 25,5 | 0,455 | 0,451 | 7,99 | 3,62 | 0,53 | 18,00 | 0,060 |
| mecz 27,5 | 0,328 | 0,326 | 4,53 | 3,56 | 0,52 | 15,36 | 0,020 |
| gospodarz 10,5 | 0,707 | 0,700 | 6,87 | 1,01 | 0,52 | 24,80 | 0,030 |
| gospodarz 12,5 | 0,551 | 0,544 | 7,18 | 1,21 | 0,49 | 33,64 | 0,522 |
| gospodarz 14,5 | 0,402 | 0,393 | 5,32 | 1,16 | 0,55 | 33,73 | 0,065 |
| gość 10,5 | 0,535 | 0,538 | 5,14 | 1,30 | 0,47 | 31,76 | 0,706 |
| gość 12,5 | 0,371 | 0,370 | 3,63 | 1,15 | 0,48 | 29,87 | 0,468 |
| gość 14,5 | 0,236 | 0,233 | 1,81 | 0,82 | 0,39 | 21,32 | 0,602 |

### Strzały celne

| Linia | Częstość | Średnia p (NB2) | MCB baseline | DSC baseline | MCB NB2 | DSC NB2 | p(MCB) NB2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| mecz 7,5 | 0,609 | 0,611 | 0,58 | 3,84 | 0,40 | 10,25 | 0,393 |
| mecz 8,5 | 0,482 | 0,484 | 0,62 | 3,91 | 0,42 | 11,14 | 0,453 |
| mecz 9,5 | 0,361 | 0,363 | 0,48 | 4,21 | 0,41 | 11,22 | 0,413 |
| gospodarz 3,5 | 0,650 | 0,648 | 0,48 | 1,41 | 0,41 | 18,87 | 0,751 |
| gospodarz 4,5 | 0,486 | 0,482 | 0,47 | 1,62 | 0,48 | 24,13 | 0,478 |
| gość 3,5 | 0,516 | 0,525 | 0,33 | 1,80 | 0,60 | 20,63 | 0,005 |
| gość 4,5 | 0,348 | 0,352 | 0,26 | 1,80 | 0,43 | 19,66 | 0,607 |

W 1206 z 1296 par liga-linia DSC modelu jest większe niż jego MCB. Mediana MCB NB2 w ligach wynosi 4,05 (×1000), mediana MCB baseline'u 1,17. Model jest więc trochę gorzej skalibrowany niż stała częstość, ale za to odróżnia mecze. Miskalibracja jest jednak wykrywalna statystycznie (p(MCB) poniżej 0,05 zbiorczo) w 13 z 34 linii. Największa różnica między średnią prognozą a częstością to 2,2 pkt proc. (żółte kartki, gość 1,5: 0,639 wobec 0,661).

Krzywa niezawodności CORP dla środkowej linii meczowej każdej statystyki (pooled NB2): prognoza i oszacowana częstość.

**Rzuty rożne, mecz 10,5**

| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |
|---:|---:|---:|
| 0,301 | 0,304 | 0,10 |
| 0,333 | 0,321 | 0,20 |
| 0,358 | 0,357 | 0,30 |
| 0,378 | 0,373 | 0,40 |
| 0,396 | 0,397 | 0,50 |
| 0,414 | 0,423 | 0,60 |
| 0,432 | 0,431 | 0,70 |
| 0,453 | 0,462 | 0,80 |
| 0,482 | 0,473 | 0,90 |
| 0,798 | 1,000 | 1,00 |

**Żółte kartki, mecz 4,5**

| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |
|---:|---:|---:|
| 0,240 | 0,245 | 0,10 |
| 0,294 | 0,303 | 0,20 |
| 0,335 | 0,354 | 0,30 |
| 0,371 | 0,381 | 0,40 |
| 0,405 | 0,436 | 0,50 |
| 0,440 | 0,451 | 0,60 |
| 0,478 | 0,494 | 0,70 |
| 0,521 | 0,536 | 0,80 |
| 0,579 | 0,574 | 0,90 |
| 0,985 | 1,000 | 1,00 |

**Strzały, mecz 25,5**

| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |
|---:|---:|---:|
| 0,271 | 0,284 | 0,10 |
| 0,328 | 0,335 | 0,20 |
| 0,372 | 0,386 | 0,30 |
| 0,409 | 0,417 | 0,40 |
| 0,446 | 0,463 | 0,50 |
| 0,485 | 0,476 | 0,60 |
| 0,526 | 0,541 | 0,70 |
| 0,573 | 0,581 | 0,80 |
| 0,637 | 0,637 | 0,90 |
| 0,942 | 1,000 | 1,00 |

**Strzały celne, mecz 8,5**

| Prognoza | Oszacowana częstość | Udział prognoz do tego punktu |
|---:|---:|---:|
| 0,355 | 0,355 | 0,10 |
| 0,393 | 0,373 | 0,20 |
| 0,423 | 0,399 | 0,30 |
| 0,450 | 0,457 | 0,40 |
| 0,476 | 0,482 | 0,50 |
| 0,505 | 0,486 | 0,60 |
| 0,538 | 0,529 | 0,70 |
| 0,573 | 0,555 | 0,80 |
| 0,620 | 0,617 | 0,90 |
| 0,914 | 1,000 | 1,00 |

Krzywe dla pozostałych linii są w tabeli `reliability`, a dla dowolnej ligi da się je policzyć z prognoz w `count_nb2.sqlite`.

### Kalibracja w ligach

Dla środkowej linii meczowej i środkowej linii drużynowej gospodarza każdej statystyki: średnie prawdopodobieństwo „over” i częstość oraz MCB i DSC modelu ×1000 w każdej lidze. Pozostałe linie są w tabeli `by_league`.

**Rzuty rożne**

| Liga | mecz 10,5: średnia p / częstość | mecz 10,5: MCB / DSC | gospodarz 4,5: średnia p / częstość | gospodarz 4,5: MCB / DSC |
|---|---:|---:|---:|---:|
| 1LIG | 0,293 / 0,267 | 2,5 / 1,8 | 0,492 / 0,478 | 4,9 / 24,0 |
| ALLSVENSKAN | 0,475 / 0,501 | 3,3 / 3,3 | 0,620 / 0,642 | 4,7 / 16,0 |
| AUSTRIA | 0,400 / 0,377 | 3,1 / 2,5 | 0,568 / 0,553 | 6,8 / 22,1 |
| BELGIUM | 0,406 / 0,421 | 2,9 / 4,8 | 0,585 / 0,587 | 3,6 / 13,8 |
| BUNDESLIGA | 0,386 / 0,391 | 2,6 / 5,0 | 0,562 / 0,577 | 4,6 / 25,5 |
| BUNDESLIGA2 | 0,439 / 0,438 | 1,6 / 1,1 | 0,609 / 0,598 | 2,7 / 7,0 |
| CHALLENGERPROL | 0,382 / 0,381 | 7,8 / 7,1 | 0,557 / 0,536 | 16,1 / 20,4 |
| CHAMPIONSHIP | 0,439 / 0,440 | 1,7 / 2,4 | 0,607 / 0,624 | 2,3 / 11,9 |
| CZECHLIGA | 0,436 / 0,447 | 3,5 / 1,4 | 0,601 / 0,585 | 4,9 / 22,0 |
| DENMARK | 0,414 / 0,418 | 3,8 / 5,9 | 0,565 / 0,582 | 4,9 / 12,5 |
| EERSTEDIVISIE | 0,422 / 0,434 | 2,7 / 4,4 | 0,556 / 0,584 | 5,0 / 22,8 |
| EKSTRAKLASA | 0,413 / 0,411 | 2,0 / 1,6 | 0,570 / 0,574 | 4,2 / 14,3 |
| ELITESERIEN | 0,482 / 0,485 | 5,2 / 15,7 | 0,638 / 0,619 | 4,5 / 23,1 |
| EPL | 0,463 / 0,467 | 2,4 / 4,6 | 0,606 / 0,597 | 3,9 / 26,3 |
| EREDIVISIE | 0,447 / 0,465 | 3,0 / 3,8 | 0,598 / 0,602 | 4,8 / 27,6 |
| GREECE | 0,294 / 0,269 | 3,8 / 2,1 | 0,517 / 0,495 | 5,7 / 44,6 |
| HNL | 0,308 / 0,308 | 3,3 / 2,2 | 0,500 / 0,487 | 5,4 / 21,2 |
| LALIGA | 0,358 / 0,364 | 3,3 / 6,7 | 0,569 / 0,572 | 2,8 / 12,7 |
| LALIGA2 | 0,343 / 0,328 | 2,8 / 3,6 | 0,567 / 0,561 | 3,1 / 6,5 |
| LEAGUEONE | 0,423 / 0,423 | 1,6 / 3,6 | 0,579 / 0,588 | 2,3 / 11,0 |
| LEAGUETWO | 0,400 / 0,384 | 3,2 / 5,9 | 0,558 / 0,579 | 2,1 / 8,2 |
| LIGAI | 0,352 / 0,357 | 3,7 / 4,1 | 0,546 / 0,571 | 4,4 / 12,7 |
| LIGUE1 | 0,363 / 0,332 | 2,9 / 1,8 | 0,559 / 0,533 | 5,3 / 15,7 |
| LIGUE2 | 0,315 / 0,326 | 2,5 / 4,9 | 0,518 / 0,518 | 3,0 / 10,7 |
| NBI | 0,431 / 0,411 | 7,2 / 5,8 | 0,553 / 0,589 | 24,3 / 41,7 |
| PREMIERDIVISIO | 0,387 / 0,377 | 6,0 / 10,5 | 0,587 / 0,606 | 11,5 / 21,5 |
| PREMIERLEAGUE2 | 0,361 / 0,381 | 2,9 / 3,3 | 0,554 / 0,579 | 4,1 / 13,1 |
| PRIMEIRA | 0,420 / 0,403 | 3,5 / 8,2 | 0,564 / 0,539 | 5,9 / 38,9 |
| SCOTLAND | 0,471 / 0,485 | 3,3 / 7,5 | 0,583 / 0,601 | 5,0 / 34,9 |
| SERIEA | 0,351 / 0,339 | 2,7 / 5,1 | 0,536 / 0,537 | 4,2 / 23,4 |
| SERIEB | 0,383 / 0,361 | 2,7 / 3,3 | 0,562 / 0,573 | 2,8 / 6,6 |
| SUPERETTAN | 0,470 / 0,519 | 9,0 / 5,0 | 0,600 / 0,651 | 10,3 / 9,7 |
| SUPERLIG | 0,381 / 0,372 | 1,6 / 2,0 | 0,563 / 0,546 | 4,1 / 14,6 |
| SUPERLIGA | 0,303 / 0,299 | 3,2 / 5,1 | 0,484 / 0,482 | 7,3 / 28,1 |
| SUPERLIGA2 | 0,397 / 0,372 | 6,3 / 4,8 | 0,587 / 0,599 | 9,7 / 17,2 |
| SWITZERLAND | 0,441 / 0,451 | 2,1 / 2,5 | 0,593 / 0,602 | 6,5 / 15,3 |

**Żółte kartki**

| Liga | mecz 4,5: średnia p / częstość | mecz 4,5: MCB / DSC | gospodarz 2,5: średnia p / częstość | gospodarz 2,5: MCB / DSC |
|---|---:|---:|---:|---:|
| 1DIVISION | 0,507 / 0,501 | 4,5 / 4,3 | 0,380 / 0,368 | 4,2 / 12,9 |
| 1LIG | 0,446 / 0,457 | 2,7 / 6,4 | 0,359 / 0,361 | 3,0 / 5,8 |
| 1SNL | 0,381 / 0,393 | 4,5 / 6,0 | 0,317 / 0,338 | 4,9 / 5,0 |
| 2LIGA | 0,504 / 0,542 | 6,2 / 8,7 | 0,403 / 0,426 | 4,0 / 4,5 |
| 3LIGA | 0,490 / 0,509 | 3,3 / 5,5 | 0,381 / 0,392 | 3,3 / 4,5 |
| ALLSVENSKAN | 0,293 / 0,305 | 4,3 / 11,7 | 0,248 / 0,260 | 3,6 / 11,9 |
| AUSTRIA | 0,421 / 0,439 | 6,6 / 12,6 | 0,328 / 0,320 | 4,7 / 7,8 |
| BELGIUM | 0,356 / 0,362 | 3,1 / 8,2 | 0,262 / 0,266 | 4,3 / 14,0 |
| BUNDESLIGA | 0,355 / 0,387 | 4,1 / 4,3 | 0,277 / 0,299 | 3,4 / 9,4 |
| BUNDESLIGA2 | 0,455 / 0,492 | 5,2 / 8,4 | 0,349 / 0,355 | 3,4 / 6,6 |
| CHALLENGERPROL | 0,511 / 0,496 | 6,4 / 14,9 | 0,409 / 0,358 | 7,8 / 13,6 |
| CHAMPIONSHIP | 0,354 / 0,356 | 1,8 / 5,3 | 0,268 / 0,257 | 2,5 / 4,5 |
| CZECHLIGA | 0,375 / 0,364 | 4,3 / 7,6 | 0,287 / 0,283 | 4,1 / 10,9 |
| DENMARK | 0,307 / 0,293 | 3,3 / 7,6 | 0,249 / 0,211 | 5,6 / 7,9 |
| EERSTEDIVISIE | 0,242 / 0,248 | 2,5 / 3,3 | 0,203 / 0,204 | 1,3 / 2,7 |
| EKSTRAKLASA | 0,408 / 0,431 | 4,1 / 6,9 | 0,328 / 0,327 | 2,5 / 6,3 |
| ELITESERIEN | 0,213 / 0,222 | 4,1 / 8,8 | 0,175 / 0,184 | 3,6 / 7,7 |
| EPL | 0,383 / 0,383 | 3,2 / 8,0 | 0,304 / 0,285 | 3,4 / 6,7 |
| EREDIVISIE | 0,196 / 0,214 | 1,9 / 0,9 | 0,154 / 0,154 | 1,3 / 1,9 |
| FIRSTLEAGUE | 0,528 / 0,519 | 4,9 / 6,3 | 0,394 / 0,383 | 4,3 / 12,8 |
| FIRSTLEAGUE2 | 0,324 / 0,329 | 5,0 / 15,7 | 0,277 / 0,285 | 3,1 / 8,9 |
| GREECE | 0,581 / 0,587 | 4,8 / 6,4 | 0,419 / 0,425 | 5,0 / 14,7 |
| HNL | 0,435 / 0,426 | 5,9 / 6,2 | 0,344 / 0,335 | 5,4 / 15,5 |
| LALIGA | 0,493 / 0,497 | 3,7 / 13,8 | 0,391 / 0,394 | 3,0 / 10,0 |
| LALIGA2 | 0,522 / 0,565 | 4,3 / 7,8 | 0,398 / 0,419 | 3,1 / 6,6 |
| LEAGUEONE | 0,343 / 0,357 | 2,0 / 5,5 | 0,266 / 0,279 | 1,7 / 5,7 |
| LEAGUETWO | 0,358 / 0,363 | 2,3 / 7,2 | 0,275 / 0,265 | 2,5 / 5,2 |
| LIGAI | 0,423 / 0,435 | 2,8 / 5,9 | 0,335 / 0,323 | 3,5 / 6,4 |
| LIGATHAAL | 0,479 / 0,493 | 4,6 / 15,9 | 0,385 / 0,407 | 5,4 / 15,4 |
| LIGUE1 | 0,314 / 0,312 | 2,5 / 7,4 | 0,251 / 0,234 | 2,4 / 3,7 |
| LIGUE2 | 0,308 / 0,327 | 3,0 / 6,4 | 0,253 / 0,250 | 2,8 / 7,7 |
| NBI | 0,465 / 0,477 | 6,6 / 15,5 | 0,354 / 0,360 | 4,3 / 5,5 |
| PREMIERDIVISIO | 0,515 / 0,579 | 10,1 / 12,4 | 0,401 / 0,429 | 6,2 / 16,8 |
| PREMIERLEAGUE | 0,266 / 0,246 | 3,2 / 8,9 | 0,220 / 0,217 | 2,5 / 3,7 |
| PREMIERLEAGUE2 | 0,446 / 0,431 | 3,4 / 5,1 | 0,352 / 0,337 | 4,4 / 5,9 |
| PREMIERLEAGUE3 | 0,396 / 0,391 | 4,1 / 10,8 | 0,315 / 0,317 | 3,4 / 9,9 |
| PRIMEIRA | 0,587 / 0,574 | 3,1 / 6,9 | 0,455 / 0,478 | 4,1 / 13,7 |
| SCOTLAND | 0,351 / 0,351 | 3,3 / 6,4 | 0,271 / 0,255 | 3,5 / 10,6 |
| SEGUNDALIGA | 0,598 / 0,629 | 4,1 / 9,0 | 0,473 / 0,489 | 4,0 / 6,8 |
| SERIEA | 0,400 / 0,398 | 5,2 / 23,3 | 0,303 / 0,287 | 4,6 / 15,9 |
| SERIEB | 0,550 / 0,573 | 3,7 / 11,6 | 0,422 / 0,422 | 3,2 / 10,3 |
| SUPERETTAN | 0,343 / 0,336 | 3,7 / 15,5 | 0,300 / 0,295 | 5,6 / 21,9 |
| SUPERLIG | 0,454 / 0,471 | 3,8 / 6,8 | 0,373 / 0,389 | 3,3 / 6,4 |
| SUPERLIGA | 0,407 / 0,422 | 4,0 / 7,9 | 0,316 / 0,318 | 4,1 / 12,6 |
| SUPERLIGA2 | 0,351 / 0,385 | 6,4 / 16,9 | 0,281 / 0,295 | 4,2 / 11,0 |
| SWITZERLAND | 0,497 / 0,510 | 4,9 / 13,6 | 0,377 / 0,366 | 3,5 / 8,8 |
| VEIKKAUSLIIGA | 0,385 / 0,368 | 4,5 / 9,4 | 0,323 / 0,328 | 4,2 / 10,4 |

**Strzały**

| Liga | mecz 25,5: średnia p / częstość | mecz 25,5: MCB / DSC | gospodarz 12,5: średnia p / częstość | gospodarz 12,5: MCB / DSC |
|---|---:|---:|---:|---:|
| 1LIG | 0,332 / 0,313 | 3,0 / 11,1 | 0,469 / 0,462 | 5,5 / 40,9 |
| ALLSVENSKAN | 0,563 / 0,572 | 6,1 / 17,5 | 0,599 / 0,616 | 5,5 / 47,6 |
| AUSTRIA | 0,409 / 0,403 | 5,5 / 14,6 | 0,503 / 0,519 | 6,1 / 37,3 |
| BELGIUM | 0,486 / 0,495 | 3,5 / 18,5 | 0,575 / 0,586 | 6,5 / 46,1 |
| BUNDESLIGA | 0,535 / 0,544 | 3,8 / 14,6 | 0,601 / 0,619 | 3,6 / 33,6 |
| BUNDESLIGA2 | 0,598 / 0,605 | 4,5 / 4,1 | 0,648 / 0,632 | 3,8 / 12,4 |
| CHALLENGERPROL | 0,477 / 0,429 | 24,5 / 5,7 | 0,545 / 0,488 | 13,3 / 18,5 |
| CHAMPIONSHIP | 0,388 / 0,399 | 2,0 / 10,3 | 0,517 / 0,545 | 4,0 / 27,8 |
| CZECHLIGA | 0,440 / 0,454 | 11,8 / 8,6 | 0,545 / 0,549 | 6,0 / 37,9 |
| DENMARK | 0,498 / 0,511 | 6,0 / 8,9 | 0,555 / 0,562 | 6,4 / 34,0 |
| EERSTEDIVISIE | 0,531 / 0,540 | 4,5 / 17,5 | 0,574 / 0,595 | 4,2 / 43,1 |
| EKSTRAKLASA | 0,571 / 0,599 | 4,7 / 21,1 | 0,619 / 0,626 | 4,2 / 29,7 |
| ELITESERIEN | 0,527 / 0,524 | 5,5 / 34,7 | 0,616 / 0,595 | 4,9 / 41,6 |
| EPL | 0,515 / 0,512 | 3,0 / 11,4 | 0,574 / 0,589 | 4,7 / 51,3 |
| EREDIVISIE | 0,598 / 0,606 | 4,0 / 13,2 | 0,618 / 0,623 | 3,6 / 50,2 |
| GREECE | 0,342 / 0,314 | 7,1 / 12,1 | 0,486 / 0,501 | 6,2 / 70,7 |
| HNL | 0,377 / 0,357 | 6,7 / 5,0 | 0,494 / 0,475 | 6,6 / 50,9 |
| LALIGA | 0,415 / 0,418 | 3,6 / 21,5 | 0,553 / 0,551 | 4,6 / 37,2 |
| LALIGA2 | 0,373 / 0,375 | 2,7 / 16,4 | 0,528 / 0,531 | 3,7 / 22,4 |
| LEAGUEONE | 0,355 / 0,369 | 2,8 / 10,6 | 0,476 / 0,511 | 4,3 / 27,1 |
| LEAGUETWO | 0,378 / 0,360 | 3,3 / 16,5 | 0,487 / 0,494 | 2,7 / 23,1 |
| LIGAI | 0,449 / 0,474 | 8,1 / 12,9 | 0,552 / 0,554 | 6,1 / 22,5 |
| LIGUE1 | 0,444 / 0,440 | 3,5 / 15,7 | 0,547 / 0,538 | 4,3 / 30,0 |
| LIGUE2 | 0,299 / 0,312 | 2,5 / 11,0 | 0,446 / 0,449 | 3,6 / 26,3 |
| PREMIERDIVISIO | 0,414 / 0,500 | 17,3 / 23,7 | 0,529 / 0,603 | 17,4 / 43,5 |
| PREMIERLEAGUE2 | 0,484 / 0,501 | 4,4 / 16,1 | 0,565 / 0,570 | 4,8 / 36,4 |
| PRIMEIRA | 0,439 / 0,446 | 4,4 / 15,7 | 0,527 / 0,515 | 5,7 / 55,1 |
| SCOTLAND | 0,465 / 0,471 | 6,4 / 10,2 | 0,510 / 0,555 | 8,8 / 54,5 |
| SERIEA | 0,452 / 0,441 | 4,0 / 12,5 | 0,545 / 0,547 | 4,3 / 41,9 |
| SERIEB | 0,475 / 0,492 | 3,2 / 10,2 | 0,559 / 0,558 | 3,2 / 14,5 |
| SUPERETTAN | 0,256 / 0,292 | 13,3 / 16,2 | 0,364 / 0,416 | 21,0 / 46,1 |
| SUPERLIG | 0,504 / 0,492 | 4,3 / 21,2 | 0,584 / 0,579 | 5,6 / 30,3 |
| SUPERLIGA | 0,419 / 0,423 | 4,8 / 21,5 | 0,514 / 0,493 | 7,0 / 54,3 |
| SUPERLIGA2 | 0,408 / 0,480 | 11,5 / 18,2 | 0,499 / 0,559 | 13,0 / 41,2 |
| SWITZERLAND | 0,617 / 0,600 | 5,2 / 13,9 | 0,641 / 0,633 | 5,4 / 26,3 |

**Strzały celne**

| Liga | mecz 8,5: średnia p / częstość | mecz 8,5: MCB / DSC | gospodarz 4,5: średnia p / częstość | gospodarz 4,5: MCB / DSC |
|---|---:|---:|---:|---:|
| 1LIG | 0,447 / 0,438 | 2,7 / 8,8 | 0,444 / 0,451 | 4,1 / 25,6 |
| ALLSVENSKAN | 0,527 / 0,529 | 4,3 / 11,4 | 0,507 / 0,485 | 6,2 / 36,7 |
| AUSTRIA | 0,487 / 0,447 | 5,5 / 9,6 | 0,464 / 0,431 | 6,1 / 27,1 |
| BELGIUM | 0,557 / 0,539 | 3,4 / 7,8 | 0,530 / 0,537 | 5,3 / 33,5 |
| BUNDESLIGA | 0,583 / 0,586 | 4,7 / 14,4 | 0,548 / 0,560 | 4,6 / 30,2 |
| BUNDESLIGA2 | 0,574 / 0,553 | 3,2 / 4,3 | 0,553 / 0,538 | 4,0 / 12,8 |
| CHALLENGERPROL | 0,562 / 0,518 | 8,7 / 3,7 | 0,547 / 0,524 | 17,0 / 34,0 |
| CHAMPIONSHIP | 0,397 / 0,391 | 2,1 / 6,3 | 0,428 / 0,436 | 3,7 / 18,5 |
| CZECHLIGA | 0,495 / 0,483 | 5,4 / 12,3 | 0,501 / 0,496 | 5,9 / 42,1 |
| DENMARK | 0,556 / 0,585 | 4,1 / 5,2 | 0,516 / 0,524 | 6,1 / 21,7 |
| EERSTEDIVISIE | 0,619 / 0,610 | 2,3 / 9,2 | 0,566 / 0,574 | 4,1 / 27,2 |
| EKSTRAKLASA | 0,477 / 0,474 | 2,4 / 7,2 | 0,479 / 0,489 | 4,2 / 17,5 |
| ELITESERIEN | 0,533 / 0,524 | 4,1 / 12,8 | 0,545 / 0,529 | 5,9 / 31,2 |
| EPL | 0,552 / 0,538 | 3,0 / 7,0 | 0,518 / 0,525 | 5,2 / 28,5 |
| EREDIVISIE | 0,631 / 0,634 | 3,6 / 6,5 | 0,577 / 0,583 | 4,4 / 44,2 |
| GREECE | 0,420 / 0,428 | 4,2 / 6,6 | 0,441 / 0,435 | 5,0 / 46,6 |
| HNL | 0,432 / 0,404 | 5,1 / 10,5 | 0,422 / 0,418 | 5,0 / 36,8 |
| LALIGA | 0,460 / 0,472 | 4,2 / 13,4 | 0,488 / 0,491 | 3,5 / 29,5 |
| LALIGA2 | 0,392 / 0,397 | 3,0 / 13,3 | 0,453 / 0,456 | 2,4 / 13,6 |
| LEAGUEONE | 0,402 / 0,408 | 1,7 / 6,0 | 0,417 / 0,442 | 3,1 / 14,6 |
| LEAGUETWO | 0,409 / 0,405 | 2,7 / 9,7 | 0,419 / 0,441 | 3,1 / 16,3 |
| LIGAI | 0,476 / 0,466 | 5,0 / 10,2 | 0,485 / 0,482 | 5,1 / 19,1 |
| LIGUE1 | 0,543 / 0,541 | 2,9 / 11,6 | 0,521 / 0,527 | 4,3 / 22,3 |
| LIGUE2 | 0,393 / 0,433 | 4,1 / 7,4 | 0,417 / 0,436 | 3,4 / 11,8 |
| PREMIERDIVISIO | 0,393 / 0,455 | 9,7 / 8,3 | 0,438 / 0,452 | 8,8 / 22,0 |
| PREMIERLEAGUE2 | 0,512 / 0,502 | 5,7 / 19,1 | 0,505 / 0,504 | 5,9 / 42,2 |
| PRIMEIRA | 0,458 / 0,465 | 4,6 / 13,3 | 0,459 / 0,453 | 5,1 / 43,0 |
| SCOTLAND | 0,449 / 0,457 | 5,3 / 16,1 | 0,446 / 0,496 | 8,7 / 44,4 |
| SERIEA | 0,452 / 0,439 | 3,5 / 7,9 | 0,458 / 0,438 | 3,9 / 34,6 |
| SERIEB | 0,430 / 0,429 | 2,8 / 5,7 | 0,449 / 0,448 | 3,3 / 10,6 |
| SUPERETTAN | 0,486 / 0,529 | 12,1 / 11,0 | 0,505 / 0,552 | 14,2 / 25,6 |
| SUPERLIG | 0,534 / 0,516 | 4,5 / 13,2 | 0,527 / 0,521 | 3,9 / 28,4 |
| SUPERLIGA | 0,494 / 0,487 | 5,6 / 19,4 | 0,481 / 0,460 | 5,1 / 34,5 |
| SUPERLIGA2 | 0,475 / 0,478 | 6,6 / 9,3 | 0,480 / 0,464 | 7,3 / 22,5 |
| SWITZERLAND | 0,587 / 0,593 | 4,2 / 6,1 | 0,547 / 0,565 | 5,3 / 14,7 |

## Wybrane hiperparametry

Siatka: kara grzbietowa 1 000 lub 5 000, półokres zaniku 1 000 dni lub brak zaniku. Wybór na sezonie przed sezonem testowym, osobno dla każdej linii i modelu (tak działa rama). Liczba wyborów (linie × modele × sezony testowe):

| Parametry | Wyborów |
|---|---:|
| kara 5 000, półokres 1 000 dni | 94 |
| kara 5 000, brak zaniku | 88 |
| kara 1 000, półokres 1 000 dni | 51 |
| kara 1 000, brak zaniku | 39 |

## Co wnoszą poszczególne cechy

Ablacja na 8 dużych ligach (EPL, LALIGA, EREDIVISIE, CHAMPIONSHIP, BUNDESLIGA, SERIEA, LIGUE1, SUPERLIG), ten sam protokół, NB2, jedno ustawienie hiperparametrów (kara 1 000, bez zaniku), środkowe linie. Wartość to przewaga nad baseline'em w log-loss (ta sama miara co wyżej). To badanie pomocnicze, nie część głównego przebiegu.

**Rzuty rożne**

| Wariant | Mecz | Gospodarz | Gość |
|---|---:|---:|---:|
| model końcowy | +0,0054 | +0,0334 | +0,0370 |
| + forma xG (tam, gdzie jest) | +0,0012 | +0,0309 | +0,0343 |
| + forma xG, mocniejsze wygładzenie cech pomocniczych | +0,0004 | +0,0304 | +0,0341 |
| bez cech pomocniczych | +0,0053 | +0,0313 | +0,0335 |
| bez sędziego | +0,0055 | +0,0334 | +0,0370 |
| bez odpoczynku | +0,0055 | +0,0333 | +0,0371 |
| krótsze okna (5, 10, 30) | +0,0053 | +0,0326 | +0,0357 |
| tylko własne zdobyte i stracone rywala | +0,0041 | +0,0332 | +0,0354 |

**Żółte kartki**

| Wariant | Mecz | Gospodarz | Gość |
|---|---:|---:|---:|
| model końcowy | +0,0200 | +0,0155 | +0,0171 |
| + forma xG (tam, gdzie jest) | +0,0165 | +0,0134 | +0,0136 |
| + forma xG, mocniejsze wygładzenie cech pomocniczych | +0,0154 | +0,0128 | +0,0125 |
| bez cech pomocniczych | +0,0199 | +0,0145 | +0,0159 |
| bez sędziego | +0,0158 | +0,0135 | +0,0143 |
| bez odpoczynku | +0,0197 | +0,0153 | +0,0171 |
| krótsze okna (5, 10, 30) | +0,0197 | +0,0151 | +0,0165 |
| tylko własne zdobyte i stracone rywala | +0,0202 | +0,0152 | +0,0173 |

**Strzały**

| Wariant | Mecz | Gospodarz | Gość |
|---|---:|---:|---:|
| model końcowy | +0,0287 | +0,0741 | +0,0686 |
| + forma xG (tam, gdzie jest) | +0,0252 | +0,0714 | +0,0658 |
| + forma xG, mocniejsze wygładzenie cech pomocniczych | +0,0245 | +0,0710 | +0,0648 |
| bez cech pomocniczych | +0,0288 | +0,0733 | +0,0672 |
| bez sędziego | +0,0288 | +0,0741 | +0,0687 |
| bez odpoczynku | +0,0288 | +0,0741 | +0,0685 |
| krótsze okna (5, 10, 30) | +0,0283 | +0,0728 | +0,0676 |
| tylko własne zdobyte i stracone rywala | +0,0266 | +0,0728 | +0,0668 |

**Strzały celne**

| Wariant | Mecz | Gospodarz | Gość |
|---|---:|---:|---:|
| model końcowy | +0,0163 | +0,0548 | +0,0503 |
| + forma xG (tam, gdzie jest) | +0,0131 | +0,0518 | +0,0493 |
| + forma xG, mocniejsze wygładzenie cech pomocniczych | +0,0126 | +0,0512 | +0,0490 |
| bez cech pomocniczych | +0,0145 | +0,0533 | +0,0494 |
| bez sędziego | +0,0164 | +0,0548 | +0,0504 |
| bez odpoczynku | +0,0162 | +0,0546 | +0,0502 |
| krótsze okna (5, 10, 30) | +0,0158 | +0,0533 | +0,0492 |
| tylko własne zdobyte i stracone rywala | +0,0153 | +0,0540 | +0,0491 |

**xG.** Wariant z formą xG jest gorszy od modelu końcowego w 24 z 24 porównań (rodzina × zakres × wariant). Powód: xG jest dostępne od 2023 roku i tylko w części lig, więc przez większość historii cecha jest pusta, a uczenie współczynnika opiera się na małej próbie. Dlatego xG nie wchodzi do modelu końcowego. To nie dowód, że xG jest bezużyteczne: po kilku sezonach danych warto to sprawdzić ponownie.

**Sędzia.** Dla żółtych kartek przewaga na sumie spada z +0,0200 do +0,0158 po usunięciu sędziego; dla rożnych i strzałów sędzia nic nie zmienia.

## Test przecieku

Wyniki i wszystkie liczniki (rożne, kartki, strzały, faule, xG, gole) meczów od daty granicznej zostały przetasowane między meczami. Prognozy z kolejek rozpoczętych do tej daty włącznie nie mogą się zmienić. Test przeszedł całą procedurę z wyborem hiperparametrów.

| Statystyka | Ligi | Data graniczna | Porównane prognozy | Zmienione prognozy | Wynik |
|---|---|---|---:|---:|---|
| Rzuty rożne | EPL, LALIGA, EREDIVISIE, ELITESERIEN | 2024-03-01 | 2 287 | 0 | przeszedł |
| Strzały | EPL, LALIGA, EREDIVISIE, ELITESERIEN | 2024-03-01 | 2 287 | 0 | przeszedł |
| Strzały celne | EPL, LALIGA, EREDIVISIE, ELITESERIEN | 2024-03-01 | 2 287 | 0 | przeszedł |
| Żółte kartki | EPL, LALIGA, EREDIVISIE, ELITESERIEN | 2024-03-01 | 2 287 | 0 | przeszedł |

## Czas

Cały przebieg protokołu (34 linie, dwa modele, test przecieku) trwał 28 minut na 3 procesach roboczych. Żółte kartki przeliczono ponownie po poprawce reguły feedu kartek (13 minut, 3 procesy robocze); pozostałe statystyki pochodzą z pierwszego przebiegu. Jedno dopasowanie obsługuje wszystkie linie danej statystyki (pamięć podręczna po dokładnym zbiorze treningowym), więc pierwsza linia kosztuje najwięcej, a kolejne niewiele. Czas ram ewaluacyjnych na statystykę i model (suma po liniach), w minutach: Rzuty rożne NB2 8,6; Rzuty rożne Poisson 7,2; Strzały NB2 7,0; Strzały Poisson 5,9; Strzały celne NB2 7,4; Strzały celne Poisson 5,8; Żółte kartki NB2 11,3; Żółte kartki Poisson 11,1. Dopasowań w jednym zadaniu: 24 000 do 35 000 (kilkanaście milisekund każde, rozwiązanie zaczyna od poprzedniego rozwiązania tej samej ligi).

## Ograniczenia i luki

- **Kursy.** Nie użyliśmy żadnych kursów ani do dopasowania, ani do oceny. Następny krok to zestawienie z kursami z kolektora (`api-football-odds`).
- **Reguły rozliczenia.** Liczymy mecze w regulaminowym czasie (status FT). Bukmacherzy mogą inaczej liczyć kartki (druga żółta jako osobna kartka) i rożne po 90. minucie.
- **Punkty kartkowe** (10 za żółtą, 25 za czerwoną) są policzone w danych (`home_booking`), ale nie modelowane: to wartości kratowe, których modele liczbowe nie dopasowują.
- **Podróż** pominięta: tabela meczów nie ma współrzędnych stadionów.
- **xG** jest dostępne głównie od 2023 roku i tylko w części lig, więc dla większości meczów treningowych jest puste (puste zostaje puste, nie zero).
- **Sędzia.** Nazwy sędziów w API-Football nie są ujednolicone; inny zapis tego samego sędziego rozbija jego historię.
- **Żółte kartki: jakość danych historycznych.** Po poprawce reguły feedu kartek 35 z 472 liga-sezonów nie wchodzi do żółtych kartek (głównie 2015–2018; w latach testowych tylko SUPERLIGA 2022). W ligach, w których feed zapisuje kartki tylko w 90–95% meczów, część meczów bez kartek może być luką zapisaną jako zero; próg 90% jest kompromisem między utratą meczów z zerem kartek a wpuszczeniem fałszywych zer.
- **Rożne i strzały: pojedyncze zera.** W danych jest 13 meczów z sumą 0 rożnych (8 w CZECHLIGA 2022, gdzie strzały i faule są puste, więc to najpewniej luka zapisana jako zero) i 12 meczów z sumą 0 strzałów. To poniżej 0,04% meczów; nie usuwaliśmy ich i nie sprawdzaliśmy wpływu.
- **Siatka hiperparametrów.** Kara grzbietowa 5 000 (górny brzeg siatki) jest wybierana najczęściej, więc szersza siatka mogłaby dać nieco lepsze wyniki; protokół pozwala to zmienić w kolejnym przebiegu.
- **Dryf poziomu.** W kilku ligach średnia liczba rożnych zmienia się z sezonu na sezon (EPL: 6,1 na gospodarza w 2023, 5,4 w 2024). Model nie ma osobnego członu na poziom ligi (próba z takim członem nie poprawiła wyniku), więc część dryfu zostaje w kalibracji.

## Jak użyć prognoz

Prognozy leżą w `Model-Buk-Data/predictions/count_nb2.sqlite` i `count_poisson.sqlite` (tabela `forecasts_<statystyka>_<mecz|home|away>_<linia>`, kolumny `p_under`, `p_over`, klucz `fixture_id` z API-Football), baseline w `count_league_frequencies.sqlite`, ocena w `count_evaluation.sqlite`. Nowy mecz prognozuje się przez `CountEngine(statystyka, 'nb2').forecast(historia, mecze, ridge=..., half_life_days=...)` z `models/count_models.py`: `historia` to zakończone mecze ligi z licznikami, `mecze` to mecze do prognozy bez liczników; wynik zawiera oczekiwane liczby i dyspersje, z których `over(zakres, linia)` daje prawdopodobieństwo dla dowolnej linii.
