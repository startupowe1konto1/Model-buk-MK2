# Model kontra rynek po podatku (pytanie 2 i przybliżenie pytania 3)

Plan, dni 4–5. Sprawdzamy dwie rzeczy. Czy model wie więcej niż rynek (kursy Pinnacle)? Czy da się na tym zarobić po podatku? Dane: kursy football-data połączone z meczami API-Football (1X2 i over/under 2,5) oraz prognozy z protokołu porównania modeli. Sezony testowe: 2023–2025. Kursy „otwarcia” to zdjęcie z piątku lub wtorku przed meczem i służą do decyzji. Kursy zamknięcia służą tylko do CLV.

## Tabela zbiorcza

**Najpierw mediana CLV i odsetek pobitych zamknięć.** Średnie CLV jest zwodnicze: ma dolną granicę −100% i nie ma górnej, więc kilka dużych ruchów podciąga średnią, choć typowy zakład traci (HANDOFF, sekcje B i C). Dlatego pierwsza tabela pokazuje medianę CLV po podatku i odsetek zakładów, w których cena wzięta była lepsza od uczciwej ceny zamknięcia Pinnacle (przed podatkiem). W nawiasie „los”: ten sam odsetek dla losowego obstawiania z tą samą liczbą zakładów, w tych samych oknach zdjęcia kursów, na tym samym rynku i w tych samych pasmach kursów. Single, bez marginesu ostrożności (z = 0), potęgowe de-vig. Strategia musi pobić „los”, a nie 50%.

| Strategia | Reżim | Bet365: zakładów | Bet365: mediana CLV po podatku | Bet365: pobite zamknięcia (los) | Max: zakładów | Max: mediana CLV po podatku | Max: pobite zamknięcia (los) |
|:---|:---|---:|---:|---:|---:|---:|---:|
| czysta referencja | τ = 0,88 | 10 (za mało) | +1,74% | 80,0% (los 19,1%) | 58 | +1,60% | 79,3% (los 31,3%) |
| czysta referencja | τ = 0,94 | 59 | −3,46% | 55,9% (los 16,6%) | 348 | −0,38% | 69,0% (los 35,0%) |
| czysta referencja | τ = 1,00 | 1 477 | +0,52% | 54,8% (los 21,3%) | 9 329 | +1,36% | 60,7% (los 41,0%) |
| referencja z wetem modelu | τ = 0,88 | 4 (za mało) | +1,74% | 100,0% (los 17,2%) | 26 | +3,97% | 88,5% (los 30,7%) |
| referencja z wetem modelu | τ = 0,94 | 20 | +1,66% | 75,0% (los 15,8%) | 141 | +1,46% | 75,9% (los 35,0%) |
| referencja z wetem modelu | τ = 1,00 | 516 | +0,48% | 54,1% (los 20,7%) | 4 248 | +1,55% | 61,4% (los 41,1%) |
| pula z rynkiem | τ = 0,88 | 11 (za mało) | +1,95% | 81,8% (los 19,7%) | 63 | +0,88% | 77,8% (los 33,3%) |
| pula z rynkiem | τ = 0,94 | 69 | −6,05% | 49,3% (los 16,3%) | 380 | −0,72% | 67,1% (los 34,9%) |
| pula z rynkiem | τ = 1,00 | 1 579 | +0,59% | 54,5% (los 21,0%) | 9 524 | +1,41% | 60,6% (los 40,9%) |
| model residualny | τ = 0,88 | 60 | −14,06% | 41,7% (los 16,0%) | 258 | −10,75% | 56,6% (los 36,3%) |
| model residualny | τ = 0,94 | 675 | −7,72% | 38,7% (los 18,1%) | 2 359 | −5,54% | 53,9% (los 38,1%) |
| model residualny | τ = 1,00 | 5 822 | −2,57% | 31,9% (los 21,0%) | 13 285 | −0,10% | 49,3% (los 41,4%) |

Pytanie z kryterium planu: czy dolna granica 95% przedziału ufności średniego CLV po podatku jest dodatnia? Odpowiedź „TAK” wymaga dodatniej dolnej granicy przy każdej z trzech metod de-vig i istotności po korekcie Holma. Każdy wiersz zbiera 30 wariantów: 3 ceny (Bet365 oraz dwie syntetyczne polskie ceny z marżą 6%), 2 progi ostrożności (z = 0 i z = 1,645) i 5 struktur (single, AKO 2–5). Ostatnia kolumna to ta sama odpowiedź dla ceny Max, czyli najwyższego kursu z kilkudziesięciu bukmacherów. Tej ceny nie da się postawić u jednego bukmachera.

| Strategia | Reżim | Bet365 i ceny polskie: dolna granica > 0? | Warianty z przedziałem ufności | Największa dolna granica (najgorsza z 3 metod de-vig) | Cena Max: dolna granica > 0? (dodatnich z ocenialnych) |
|:---|:---|:---|---:|:---|:---|
| czysta referencja | τ = 0,88 | NIE | 8 z 30 | −15,9% (PL z Bet365, z = 0, AKO 5, n = 73) | NIE (0 z 5) |
| czysta referencja | τ = 0,94 | NIE | 10 z 30 | −8,7% (PL z Bet365, z = 0, AKO 3, n = 145) | **TAK** (2 z 5) |
| czysta referencja | τ = 1,00 | NIE | 10 z 30 | −0,6% (PL z Bet365, z = 0, single, n = 1 389) | **TAK** (6 z 8) |
| referencja z wetem modelu | τ = 0,88 | za mało zakładów | 0 z 30 | brak ocenialnego wariantu | tak, ale nie po korekcie (1 z 5) |
| referencja z wetem modelu | τ = 0,94 | NIE | 10 z 30 | −5,8% (PL z Bet365, z = 0, AKO 2, n = 35) | **TAK** (3 z 5) |
| referencja z wetem modelu | τ = 1,00 | NIE | 10 z 30 | −0,6% (PL z Bet365, z = 0, single, n = 507) | **TAK** (5 z 6) |
| pula z rynkiem | τ = 0,88 | NIE | 8 z 30 | −15,9% (PL z Bet365, z = 0, AKO 5, n = 80) | NIE (0 z 5) |
| pula z rynkiem | τ = 0,94 | NIE | 10 z 30 | −8,2% (PL z Bet365, z = 0, AKO 2, n = 149) | **TAK** (2 z 5) |
| pula z rynkiem | τ = 1,00 | NIE | 10 z 30 | −0,7% (Bet365, z = 0, single, n = 1 579) | **TAK** (6 z 9) |
| model residualny | τ = 0,88 | NIE | 14 z 30 | −15,8% (PL z Bet365, z = 0, single, n = 65) | NIE (0 z 10) |
| model residualny | τ = 0,94 | NIE | 15 z 30 | −8,9% (PL z Bet365, z = 0, single, n = 749) | NIE (0 z 15) |
| model residualny | τ = 1,00 | NIE | 20 z 30 | −2,3% (PL z Bet365, z = 1,645, single, n = 86) | tak, ale nie po korekcie (5 z 17) |

„Za mało zakładów”: przy tym reżimie strategia nie znajduje 20 zakładów w trzech sezonach, więc przedziału nie da się policzyć. Taki wariant wchodzi do korekty z p = 1.

## Wynik w skrócie

1. **Model nie wie więcej niż rynek.** Log-loss w 1X2: model (pula modeli) 1,0052, rynek 0,9925 na otwarciu i 0,9893 na zamknięciu (15 875 meczów, 18 lig). Różnica względem otwarcia to −0,0127 [−0,0149; −0,0105]; ujemna znaczy: model gorszy. W żadnej lidze żaden model nie jest lepszy od rynku (Holm: 0 z 18). Over/under 2,5: 0,6817 wobec 0,6759 i 0,6734.
2. **Pula z rynkiem i model residualny nic nie dodają.** Waga modelu w puli to 7,8%, 2,1% i 1,7% (sezony 2023, 2024, 2025), w over/under 2,5 prawie zero. Log-loss puli różni się od otwarcia o −0,0001 (p = 0,209), model residualny o −0,0001 [−0,0008; +0,0007]. Względem zamknięcia oba są gorsze o 0,0033.
3. **Na cenach, które przybliżają polskiego bukmachera, żadna strategia nie ma dodatniej dolnej granicy CLV po podatku.** Z 360 wariantów przedział da się policzyć w 125, dodatni jest w 0. Najlepszy przypadek to Bet365 bez podatku (τ = 1,00, single, 1 477 zakładów): mediana CLV +0,52%, pobitych zamknięć 54,8% (losowo 21,3%), ale średnie CLV po podatku −0,12% [−0,58%; +0,35%], czyli zero: przewaga widoczna na otwarciu znika do zamknięcia. Przy τ = 0,88 skaner prawie nie znajduje zakładów: single z Bet365 bez marginesu ostrożności to 10 zakładów w trzech sezonach, z marginesem 0.
4. **Dodatnia dolna granica pojawia się dopiero przy cenie Max.** Przy τ = 1,00, single: mediana CLV +1,36%, pobitych zamknięć 60,7% (losowo 41,0%), średnie CLV +1,18% [+1,01%; +1,35%] (n = 9 329). AKO z 5 nóg: mediana +5,3%, średnie +6,5% (dolna granica +5,6%). Przy τ = 0,94 tylko AKO z 4–5 nóg (5 nóg: mediana +2,8%, średnie +3,7%, dolna granica +2,3%). Przy τ = 0,88 żaden wariant nie przechodzi korekty (single: 58 zakładów, mediana CLV +1,6%, średnie +4,4%, dolna granica ujemna). Po korekcie Holma na 1260 sprawdzonych wariantów istotnych jest 30; wszystkie przy τ = 0,94 i τ = 1,00.
5. **Wynik zależy od marży polskiego bukmachera, której nie znamy.** Przy założonych 6% polskie ceny nie dają nic. Przy 4% (to marża samego Pinnacle) i szumie cen jak u Bet365, bez podatku (τ = 1,00), single mają medianę CLV +1,11%, pobitych zamknięć 58,5% i średnie CLV +0,7% (dolna granica +0,5%, n = 4 130; najgorsza z trzech metod de-vig). Przy τ = 0,94 i 0,88 taki scenariusz też nie ma dodatniej dolnej granicy dla singli.
6. **Model nie poprawia strategii cenowej.** Czysta referencja, referencja z wetem i pula dają podobne CLV. Nogi zgodne z modelem mają wyższe CLV o 0,9–1,6 pp przy cenie Max, a przy Bet365 różnica jest nieistotna w dwóch z trzech metod de-vig (sekcja 3.6). Sam model przeciw cenom Bet365 traci: CLV po podatku −17,9% przy τ = 0,88 (n = 6 253) i −6,0% przy τ = 1,00.

## Gałąź kryterium planu

Plan: jeśli jakakolwiek strategia (single albo AKO, w jakimkolwiek reżimie podatku) ma dodatnią dolną granicę CLV po podatku poza próbą, kupujemy feed polskich kursów. Jeśli żadna, ale modele rożnych i kartek są dobrze skalibrowane, czekamy na kursy z kolektora i powtarzamy test dla tych rynków. Jeśli nic nie działa, skaner zostaje narzędziem pomiarowym.

- **Bet365 i ceny z marżą 6%:** 0 z 360 wariantów ma dodatnią dolną granicę. Kryterium nie jest spełnione.
- **Cena Max oraz marża 4% przy τ = 1,00:** dodatnia dolna granica, istotna po korekcie na 1260 wariantów. Wymaga ceny, której nie ma u jednego bukmachera, albo założenia o marży.
- **τ = 0,88 (standardowy podatek):** żaden wariant nie przechodzi korekty na wielokrotne testowanie. Bez korekty dodatnią dolną granicę ma 1 z 420 wariantów (ceny: Max; np. referencja z wetem modelu, single, 26 zakładów: średnie CLV +4,6%, dolna granica +1,5%, p po korekcie = 1,00).

**Werdykt.** (1) Na cenach, które da się postawić u jednego bukmachera (Bet365, ceny polskie przy marży 6% i 8%), kryterium pierwszej gałęzi nie jest spełnione w żadnym reżimie podatku. Dla standardowego podatku (τ = 0,88) obowiązuje więc gałąź trzecia: skaner zostaje narzędziem pomiarowym, a zakłady ograniczają się do reżimów bez podatku, jeśli na prawdziwych polskich cenach dadzą dodatni CLV. (2) Pierwsza gałąź (zakup feedu polskich kursów) jest spełniona tylko warunkowo: na cenie Max, której nie da się postawić u jednego bukmachera, albo przy założeniu, że polski bukmacher ma marżę 4% i szum cen jak Bet365. Dotyczy to wyłącznie τ = 1,00 (single i AKO) oraz τ = 0,94 (AKO z 4–5 nóg, cena Max). To powód, żeby zmierzyć prawdziwe polskie ceny, a nie dowód, że przewaga istnieje. Cały sygnał to porównanie ceny z odmarżowanym Pinnacle; model do tego nie jest potrzebny. Decyzja o zakupie należy do Ciebie. (3) Druga gałąź (rożne i kartki): modele liczbowe biją częstość ligową w 34 z 34 linii i odróżniają mecze (DSC większe niż MCB w 1206 z 1296 par liga-linia), ale są trochę gorzej skalibrowane niż stała częstość, a miskalibracja jest wykrywalna w 13 z 34 linii (raport pytania 1, sekcja Kalibracja). Kursów historycznych dla tych rynków nie ma, więc gałąź druga czeka na dane z kolektora niezależnie od wyniku tego testu.

## Dane i założenia

- **CLV po podatku** = τ · kurs wzięty / uczciwy kurs zamknięcia Pinnacle − 1. Przy nieobciążonym zamknięciu to oczekiwany zwrot na złotówkę stawki po podatku. Liczymy go na cenie faktycznie wziętej (nie na ruchu rynku) i podajemy średnią, medianę oraz odsetek pobitych zamknięć.
- **Mecze.** Mecze z kursami Pinnacle na otwarciu i zamknięciu: 1X2 15 875 w sezonach 2023–2025 (18 lig), over/under 2,5 15 826. Ligi bez kursów Pinnacle w football-data (Ekstraklasa, ligi skandynawskie) nie wchodzą. W sezonie 2025/26 Pinnacle jest w football-data tylko do stycznia 2026 (ok. 40% meczów).
- **Kursy „otwarcia”.** Zdjęcie robione przy budowie tygodniowego pliku: piątek dla meczów od piątku do poniedziałku, wtorek dla wtorku–czwartku (średni ruch Pinnacle do zamknięcia jest najmniejszy dla meczów w piątek i we wtorek). Nogi jednego AKO pochodzą z jednego takiego okna, z różnych meczów, u jednego bukmachera. Marża w kolumnach Pinnacle zmieniła się (3,2% w 2022, 4,8% w 2024), więc CLV z różnych sezonów nie jest porównywalny; w obrębie sezonu tak.
- **De-vig.** Trzy metody: proporcjonalna, potęgowa, Shin. Wariant jest „dodatni”, tylko gdy dolna granica jest dodatnia przy każdej z trzech.
- **Decyzja.** Cena bukmachera (Bet365; Max i Avg jako czułość) musi przebić dolną granicę uczciwej ceny: `p − z·SE(p)`, gdzie `SE = c·√(p(1−p))`. `c` to zmierzony na trzech poprzednich sezonach ruch Pinnacle od zdjęcia do zamknięcia (0,059–0,065); z = 1,645 albo z = 0 (bez marginesu). Potem bramka EV z niepewnością (`assess_bet`), jeden typ na mecz (`select_opportunities`) i, dla AKO, dodatnie EV kuponu z dolnych granic (`accumulator_ev_net`, podatek raz na kupon).
- **Zakłady.** Płaska stawka 100 zł (obcięta poniżej progu PIT 2 280 zł wypłaty) albo ¼ Kelly'ego z podatkiem na dolnej granicy prawdopodobieństwa, z bankrollu 10 000 zł, najwyżej 2% na zakład.
- **Statystyka.** Przedziały: bootstrap po kolejkach (single) albo po oknach zdjęcia kursów (AKO), 4 000 losowań. P-wartość wariantu to największa z trzech metod de-vig (test przecięcia). Korekta Holma i Benjaminiego–Hochberga na rodzinie wariantów. Wariant z mniej niż 20 zakładami albo 10 klastrami wchodzi do rodziny z p = 1.
- **Liczba wariantów.** Sprawdzono 1260 wariantów (strategia × cena × próg × rynki × sezony × reżim × struktura); każdy pod trzema metodami de-vig. Rodzina „plan” (Bet365 i polskie ceny) ma 360 wariantów, „rynek” (Max i Avg) 240, „nagłówkowa” (Bet365, z = 1,645) 60. Tabele podają pełną listę; wnioski o „TAK” wymagają istotności po korekcie na rodzinie.
- **Nie zrobiono:** handicapu azjatyckiego. Linia handicapu zmienia się między otwarciem a zamknięciem, a ćwierćlinie zwracają połowę stawki; wymaga to osobnego rozliczania i dopasowania linii do CLV, więc nie jest to „proste”. Zostaje na później.

## 1. Czy model wie więcej niż rynek? (log-loss)

Ta sama próba dla wszystkich: mecze z kursami Pinnacle na otwarciu i zamknięciu oraz prognozami wszystkich modeli. Różnica = strata rynku minus strata modelu; dodatnia znaczy: model lepszy. Test Diebolda–Mariano z wariancją HAC po kolejkach, Holm na ligi. Tabela dla potęgowej metody de-vig; proporcjonalna i Shin zmieniają różnicę o najwyżej 0,0005.

### 1X2

| Model | Log-loss | Względem otwarcia (różnica [95% CI]) | Ligi lepsze / gorsze (Holm) | Względem zamknięcia (różnica [95% CI]) | Ligi lepsze / gorsze (Holm) |
|:---|---:|---:|---:|---:|---:|
| Pinnacle, otwarcie (rynek) | 0,9925 | — | — | — | — |
| Pinnacle, zamknięcie (rynek) | 0,9893 | — | — | — | — |
| częstości ligowe (baseline) | 1,0752 | −0,0826 [−0,0886; −0,0765] | 0 / 18 | −0,0858 [−0,0918; −0,0799] | 0 / 18 |
| Dixon–Coles | 1,0117 | −0,0192 [−0,0219; −0,0168] | 0 / 15 | −0,0224 [−0,0251; −0,0197] | 0 / 18 |
| NB2 | 1,0125 | −0,0200 [−0,0226; −0,0176] | 0 / 15 | −0,0232 [−0,0261; −0,0204] | 0 / 15 |
| GAS-Poisson | 1,0119 | −0,0194 [−0,0221; −0,0169] | 0 / 18 | −0,0226 [−0,0255; −0,0198] | 0 / 18 |
| Elo–Davidson | 1,0135 | −0,0209 [−0,0238; −0,0183] | 0 / 18 | −0,0242 [−0,0272; −0,0211] | 0 / 18 |
| pi-ratingi | 1,0125 | −0,0200 [−0,0228; −0,0173] | 0 / 15 | −0,0232 [−0,0263; −0,0202] | 0 / 16 |
| pi-ratingi + GBM | 1,0062 | −0,0137 [−0,0163; −0,0113] | 0 / 6 | −0,0169 [−0,0196; −0,0144] | 0 / 13 |
| wybór ex ante | 1,0062 | −0,0137 [−0,0163; −0,0113] | 0 / 6 | −0,0169 [−0,0196; −0,0144] | 0 / 13 |
| pula modeli | 1,0052 | −0,0127 [−0,0149; −0,0105] | 0 / 8 | −0,0159 [−0,0184; −0,0135] | 0 / 12 |

### Over/under 2,5

| Model | Log-loss | Względem otwarcia (różnica [95% CI]) | Ligi lepsze / gorsze (Holm) | Względem zamknięcia (różnica [95% CI]) | Ligi lepsze / gorsze (Holm) |
|:---|---:|---:|---:|---:|---:|
| Pinnacle, otwarcie (rynek) | 0,6759 | — | — | — | — |
| Pinnacle, zamknięcie (rynek) | 0,6734 | — | — | — | — |
| częstości ligowe (baseline) | 0,6913 | −0,0153 [−0,0178; −0,0130] | 0 / 9 | −0,0178 [−0,0205; −0,0154] | 0 / 10 |
| Dixon–Coles | 0,6851 | −0,0092 [−0,0110; −0,0073] | 0 / 4 | −0,0117 [−0,0137; −0,0096] | 0 / 5 |
| NB2 | 0,6852 | −0,0093 [−0,0110; −0,0074] | 0 / 3 | −0,0117 [−0,0137; −0,0096] | 0 / 6 |
| GAS-Poisson | 0,6843 | −0,0084 [−0,0101; −0,0067] | 0 / 5 | −0,0109 [−0,0128; −0,0089] | 0 / 9 |
| pi-ratingi | 0,6862 | −0,0102 [−0,0123; −0,0083] | 0 / 6 | −0,0127 [−0,0151; −0,0107] | 0 / 9 |
| pi-ratingi + GBM | 0,6835 | −0,0076 [−0,0093; −0,0060] | 0 / 3 | −0,0101 [−0,0120; −0,0083] | 0 / 9 |
| wybór ex ante | 0,6843 | −0,0084 [−0,0101; −0,0067] | 0 / 5 | −0,0108 [−0,0128; −0,0090] | 0 / 7 |
| pula modeli | 0,6817 | −0,0058 [−0,0071; −0,0045] | 0 / 3 | −0,0083 [−0,0099; −0,0068] | 0 / 8 |

Wszystkie różnice łączne są ujemne, największe p to < 0,001. Wybór ex ante i pula modeli używają tylko wyników poprzedniego sezonu. Lepszy model zmniejsza dystans do rynku, ale go nie domyka. Zamknięcie jest lepsze od otwarcia o 0,0032 w 1X2 i 0,0025 w over/under, więc z zamknięciem model przegrywa mocniej. Wyniki dla modeli bazowych na sezonach 2022–2025 (próba B) są w `market_test.sqlite`, tabela `loss_by_league`.

## 2. Pula z rynkiem i model residualny

Pula: log-liniowa mieszanka odmarżowanego otwarcia Pinnacle i modelu wybranego ex ante. Waga dopasowana na wcześniejszych sezonach (od 2022, bez sezonu prognozowanego). Model residualny (`market_residual.py`, tylko 1X2): ograniczone korekty logitów wokół otwarcia, sterowane formą drużyn z okien 5 i 10 meczów (gole, strzały, strzały celne, rożne, punkty), odpoczynkiem i kształtem otwarcia. Siła kary wybrana na sezonie poprzedzającym, dopasowanie na wcześniejszych sezonach (od 2015).

| Prognoza | Log-loss | Względem otwarcia [95% CI] | p | Względem zamknięcia [95% CI] | p |
|:---|---:|---:|---:|---:|---:|
| pula z rynkiem, 1X2 | 0,9926 | −0,0001 [−0,0002; 0,0000] | 0,209 | −0,0033 [−0,0044; −0,0020] | < 0,001 |
| model residualny, 1X2 | 0,9926 | −0,0001 [−0,0008; +0,0007] | 0,861 | −0,0033 [−0,0046; −0,0019] | < 0,001 |
| pula z rynkiem, over/under 2,5 | 0,6759 | 0,0000 [0,0000; 0,0000] | 0,497 | −0,0025 [−0,0034; −0,0016] | < 0,001 |

Waga modelu w puli (potęgowe de-vig): 1X2 7,8%, 2,1%, 1,7% w sezonach 2023, 2024, 2025 (model: pi_gbm, pi_gbm, pi_gbm); over/under 2,5 0,80%, 0,00%, 0,00% (model: pi_gbm, pi_gbm, gas_poisson). Pula i model residualny są nierozróżnialne od otwarcia i gorsze od zamknięcia. To, co model wie, rynek na otwarciu już ma. To, czego rynek dowiaduje się do zamknięcia, jest poza modelem. Pula zbudowana z puli modeli (2024–2025) daje wagę modelu równą zero.

## 3. Strategie na cenach otwarcia

### 3.1 Jak często cena bukmachera przebija odmarżowane otwarcie Pinnacle

Odsetek meczów (sezony 2023–2025), w których choć jedna noga ma cenę o co najmniej tyle powyżej uczciwej (potęgowe de-vig otwarcia Pinnacle). 0% to próg przy τ = 1,00, 6,4% próg singla przy τ = 0,94, 13,6% przy τ = 0,88.

| Rynek | Cena | Mecze | ≥ 0% | ≥ 6,4% | ≥ 13,6% | Mecze ≥ 13,6% |
|:---|:---|---:|---:|---:|---:|---:|
| 1X2 | Bet365 | 15 875 | 8,8% | 0,3% | 0,04% | 6 |
| 1X2 | Avg (średnia rynku) | 15 879 | 0,6% | 0,0% | 0,01% | 1 |
| 1X2 | Max (najlepsza cena rynku) | 15 879 | 51,7% | 1,9% | 0,30% | 48 |
| over/under 2,5 | Bet365 | 15 824 | 0,7% | 0,1% | 0,03% | 4 |
| over/under 2,5 | Avg (średnia rynku) | 15 829 | 0,1% | 0,0% | 0,01% | 1 |
| over/under 2,5 | Max (najlepsza cena rynku) | 15 829 | 18,7% | 0,3% | 0,08% | 12 |

Przy progu 13,6% (τ = 0,88) Bet365 przebija Pinnacle w 6 meczach na 15 875 w 1X2, a najlepsza cena rynku w 48. To powód, dla którego przy standardowym podatku nie ma czego testować.

### 3.2 Bet365, bez marginesu ostrożności (z = 0): dolna granica CLV po podatku

Komórka: liczba zakładów lub kuponów; dolna granica 95% przedziału średniego CLV po podatku (najgorsza z trzech metod de-vig). Pogrubienie: dodatnia przy każdej z trzech metod. „za mało”: mniej niż 20 zakładów albo 10 klastrów, przedziału nie czytamy.

| Strategia | Reżim | single | AKO 2 | AKO 3 | AKO 4 | AKO 5 |
|:---|:---|---:|---:|---:|---:|---:|
| czysta referencja | τ = 0,88 | 10 (za mało) | 30; −18,3% | 44; −22,3% | 61; −20,4% | 61; −19,2% |
| czysta referencja | τ = 0,94 | 59; −9,5% | 121; −8,9% | 135; −10,0% | 137; −9,5% | 126; −8,8% |
| czysta referencja | τ = 1,00 | 1 447; −0,7% | 670; −1,3% | 424; −1,6% | 299; −2,0% | 225; −2,2% |
| referencja z wetem modelu | τ = 0,88 | 4 (za mało) | 9 (za mało) | 9 (za mało) | 9 (za mało) | 10 (za mało) |
| referencja z wetem modelu | τ = 0,94 | 20; −8,4% | 37; −9,8% | 45; −10,1% | 46; −9,4% | 41; −11,3% |
| referencja z wetem modelu | τ = 1,00 | 516; −0,9% | 218; −1,6% | 122; −3,4% | 84; −4,3% | 51; −5,7% |
| pula z rynkiem | τ = 0,88 | 11 (za mało) | 32; −16,7% | 54; −19,6% | 67; −17,8% | 74; −17,8% |
| pula z rynkiem | τ = 0,94 | 69; −9,4% | 144; −8,6% | 157; −9,4% | 150; −9,6% | 137; −10,5% |
| pula z rynkiem | τ = 1,00 | 1 579; −0,7% | 744; −1,4% | 459; −1,9% | 330; −2,8% | 249; −4,2% |
| model residualny | τ = 0,88 | 60; −16,6% | 319; −17,4% | 453; −20,6% | 488; −22,7% | 487; −24,8% |
| model residualny | τ = 0,94 | 675; −8,9% | 1 006; −11,9% | 968; −14,3% | 848; −16,7% | 744; −19,2% |
| model residualny | τ = 1,00 | 5 571; −3,0% | 2 737; −5,9% | 1 787; −8,7% | 1 323; −11,3% | 1 037; −14,0% |

### 3.3 Bet365, z marginesem ostrożności (z = 1,645, wariant nagłówkowy)

| Strategia | Reżim | single | AKO 2 | AKO 3 | AKO 4 | AKO 5 |
|:---|:---|---:|---:|---:|---:|---:|
| czysta referencja | τ = 0,88 | 0 | 0 | 0 | 0 | 0 |
| czysta referencja | τ = 0,94 | 1 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| czysta referencja | τ = 1,00 | 6 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 0,88 | 0 | 0 | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 0,94 | 0 | 0 | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 1,00 | 1 (za mało) | 0 | 0 | 0 | 0 |
| pula z rynkiem | τ = 0,88 | 0 | 0 | 0 | 0 | 0 |
| pula z rynkiem | τ = 0,94 | 1 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| pula z rynkiem | τ = 1,00 | 6 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| model residualny | τ = 0,88 | 1 (za mało) | 1 (za mało) | 1 (za mało) | 1 (za mało) | 0 |
| model residualny | τ = 0,94 | 9 (za mało) | 6 (za mało) | 2 (za mało) | 2 (za mało) | 0 |
| model residualny | τ = 1,00 | 79; −3,0% | 20; −5,1% | 7 (za mało) | 2 (za mało) | 0 |

Margines `z·SE` to przy tych danych 4–5 punktów procentowych prawdopodobieństwa: `c` wynosi 0,059–0,065, bo zdjęcie kursów jest 1–4 dni przed meczem. Wariantów z przedziałem ufności jest tylko 2 z 60. Plan zakładał 1–2 punkty dla skanu tuż przed meczem; takich danych nie mamy.

### 3.4 Losowa linia bazowa (ta sama liczba zakładów, rynek i pasmo kursów)

Każda noga zakładu jest zamieniana na losową nogę z tego samego okna zdjęcia kursów, z tego samego rynku (1X2 albo over/under 2,5) i z tego samego pasma kursów (do 1,5; 1,5–2; 2–3; 3–5; powyżej 5), którą strategia mogła wziąć. Liczba zakładów, ich czas, mieszanka rynków i mieszanka kursów są więc identyczne; kupony dostają różne mecze. 200 powtórzeń. W HANDOFF losowe obstawianie biło zamknięcie w 50,08% przypadków, ale tam CLV był ruchem uczciwej ceny Pinnacle. Tutaj CLV liczymy na cenie miękkiego bukmachera, która ma marżę, więc losowa noga bije uczciwe zamknięcie rzadziej: dla singli ok. 21% u Bet365 i ok. 41% przy cenie Max. Strategia musi pobić tę linię, nie 50%. Metoda de-vig: potęgowa. p to odsetek powtórzeń losowych, które są co najmniej tak dobre jak strategia (najmniejsza możliwa wartość to 1/201).

**Bet365, czysta referencja, z = 0**

| Reżim | Struktura | Zakładów | Pobite zamknięcia | Losowo, ta sama liczba i pasmo kursów [5%; 95%] | p (losowo ≥ strategia) | Mediana CLV | Losowo: mediana | Średnie CLV | Losowo: średnia |
|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| τ = 0,88 | single | 10 | 80,0% | 19,1% [0,0%; 40,0%] | 0,005 | +1,74% | −19,54% | −0,13% | −20,54% |
| τ = 0,88 | AKO 2 | 30 | 46,7% | 9,9% [3,3%; 16,7%] | 0,005 | −14,57% | −27,12% | −10,06% | −28,16% |
| τ = 0,88 | AKO 3 | 44 | 45,5% | 6,5% [2,2%; 13,6%] | 0,005 | −15,36% | −34,16% | −14,46% | −34,15% |
| τ = 0,88 | AKO 4 | 61 | 45,9% | 4,5% [1,6%; 8,2%] | 0,005 | −15,18% | −38,52% | −13,74% | −38,42% |
| τ = 0,88 | AKO 5 | 61 | 47,5% | 3,4% [0,0%; 6,6%] | 0,005 | −14,85% | −42,92% | −13,52% | −42,31% |
| τ = 0,94 | single | 59 | 55,9% | 16,6% [8,5%; 23,7%] | 0,005 | −3,46% | −13,73% | −5,57% | −15,40% |
| τ = 0,94 | AKO 2 | 121 | 50,4% | 10,3% [5,8%; 14,9%] | 0,005 | −5,52% | −20,40% | −5,08% | −21,58% |
| τ = 0,94 | AKO 3 | 135 | 51,1% | 7,0% [3,7%; 11,1%] | 0,005 | −5,14% | −26,35% | −6,59% | −27,20% |
| τ = 0,94 | AKO 4 | 137 | 52,6% | 5,5% [2,2%; 8,8%] | 0,005 | −4,25% | −31,62% | −6,02% | −31,79% |
| τ = 0,94 | AKO 5 | 126 | 54,8% | 3,9% [1,6%; 6,4%] | 0,005 | −4,16% | −36,56% | −4,28% | −36,27% |
| τ = 1,00 | single | 1 477 | 54,8% | 21,3% [19,4%; 23,0%] | 0,005 | +0,52% | −5,28% | −0,12% | −6,59% |
| τ = 1,00 | AKO 2 | 691 | 53,7% | 13,3% [11,6%; 15,2%] | 0,005 | +0,69% | −11,72% | −0,17% | −12,93% |
| τ = 1,00 | AKO 3 | 433 | 56,1% | 9,4% [7,4%; 11,8%] | 0,005 | +1,21% | −17,91% | +0,34% | −18,67% |
| τ = 1,00 | AKO 4 | 301 | 51,2% | 6,3% [4,0%; 8,6%] | 0,005 | +0,47% | −24,02% | +0,28% | −24,38% |
| τ = 1,00 | AKO 5 | 232 | 53,0% | 4,4% [2,6%; 6,9%] | 0,005 | +1,43% | −29,54% | +0,69% | −29,42% |

Strategia bije los pod względem odsetka pobitych zamknięć i mediany, bo wybiera nogi z ceną powyżej uczciwej ceny otwarcia. Ale średnie CLV nie jest dodatnie. Dla singli przy τ = 1,00 średnia przewaga na otwarciu (p · kurs − 1) to +1,8%, a średnie CLV (po zamknięciu) −0,1%: przewaga widoczna na otwarciu znika do zamknięcia. Mediana i odsetek pobitych zamknięć mówią, czy przewaga jest typowa. Średnie CLV mówi, ile zostaje po uwzględnieniu ogona rozkładu. Zysk wymaga obu: mediany i odsetka powyżej losowego oraz dodatniej dolnej granicy średniej (HANDOFF, sekcje B i C). Tu jest tylko pierwsze.

### 3.5 Yield po podatku, ¼ Kelly i obsunięcie

Bet365, czysta referencja, z = 0, potęgowe de-vig. Przedziały yieldu: bootstrap po kolejkach albo oknach. Przy tej liczbie zakładów yield jest zbyt niepewny, żeby cokolwiek rozstrzygać; CLV ma znacznie mniejszy rozrzut i dlatego jest miarą decyzyjną. Przedziały yieldu długich AKO są dodatkowo nierzetelne: kilka trafień z kursem rzędu 10–200 decyduje o wyniku, a bootstrap zaniża ogon. Dolna granica yieldu powyżej zera (np. τ = 1,00, AKO 3) przy średnim CLV bliskim zera to szczęście, nie przewaga.

| Reżim | Struktura | Zakładów / kuponów | Mediana CLV po podatku | Pobite zamknięcia (przed podatkiem) | Średnie CLV po podatku [95% CI] | Yield płaski [95% CI] | Yield ¼ Kelly | Obsunięcie płaskie (jedn. 100 zł) | Obsunięcie ¼ Kelly |
|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| τ = 0,88 | single | 10 | +1,74% | 80,0% | −0,13% [−5,61%; +4,61%] | −48,5% [−100,0%; +17,6%] | +35,5% | 7,0 | 1,5% |
| τ = 0,88 | AKO 2 | 30 | −14,57% | 46,7% | −10,06% [−17,28%; −3,03%] | +21,6% [−100,0%; +243,7%] | +81,6% | 13,4 | 1,1% |
| τ = 0,88 | AKO 3 | 44 | −15,36% | 45,5% | −14,46% [−22,32%; −7,11%] | −54,3% [−100,0%; +44,3%] | −71,4% | 11,9 | 0,9% |
| τ = 0,88 | AKO 4 | 61 | −15,18% | 45,9% | −13,74% [−20,36%; −7,46%] | −100,0% [−100,0%; −100,0%] | −100,0% | 9,9 | 0,6% |
| τ = 0,88 | AKO 5 | 61 | −14,85% | 47,5% | −13,52% [−19,18%; −7,70%] | −100,0% [−100,0%; −100,0%] | −100,0% | 3,8 | 0,3% |
| τ = 0,94 | single | 59 | −3,46% | 55,9% | −5,57% [−9,49%; −1,96%] | −31,8% [−71,8%; +18,0%] | −18,6% | 21,3 | 6,8% |
| τ = 0,94 | AKO 2 | 121 | −5,52% | 50,4% | −5,08% [−8,26%; −1,99%] | +13,4% [−56,2%; +96,8%] | +24,8% | 22,6 | 2,6% |
| τ = 0,94 | AKO 3 | 135 | −5,14% | 51,1% | −6,59% [−10,03%; −3,36%] | +38,9% [−64,3%; +163,9%] | −14,9% | 22,6 | 1,4% |
| τ = 0,94 | AKO 4 | 137 | −4,25% | 52,6% | −6,02% [−9,50%; −2,64%] | −33,8% [−100,0%; +112,5%] | −85,9% | 18,3 | 1,6% |
| τ = 0,94 | AKO 5 | 126 | −4,16% | 54,8% | −4,28% [−8,03%; −0,56%] | +91,9% [−100,0%; +447,2%] | −63,1% | 5,9 | 0,5% |
| τ = 1,00 | single | 1 477 | +0,52% | 54,8% | −0,12% [−0,58%; +0,35%] | +7,0% [−1,5%; +15,5%] | +0,1% | 41,2 | 12,6% |
| τ = 1,00 | AKO 2 | 691 | +0,69% | 53,7% | −0,17% [−1,17%; +0,73%] | +18,8% [−6,1%; +46,2%] | +19,1% | 41,2 | 4,8% |
| τ = 1,00 | AKO 3 | 433 | +1,21% | 56,1% | +0,34% [−1,08%; +1,74%] | +67,6% [+10,2%; +129,3%] | +52,2% | 26,7 | 2,5% |
| τ = 1,00 | AKO 4 | 301 | +0,47% | 51,2% | +0,28% [−1,71%; +2,25%] | +37,3% [−50,3%; +138,9%] | −4,0% | 27,1 | 1,6% |
| τ = 1,00 | AKO 5 | 232 | +1,43% | 53,0% | +0,69% [−1,93%; +3,26%] | +63,9% [−100,0%; +265,9%] | +73,0% | 16,9 | 1,1% |

### 3.6 Czy model porządkuje nogi, które przechodzą sito cenowe? (efekt weta)

Bierzemy wszystkie nogi, które przechodzą sito cenowe (dolna granica uczciwej ceny poniżej ceny bukmachera), i dzielimy je na dwie grupy. Pierwsza: model uważa wynik za co najmniej tak prawdopodobny jak referencja (weto zostawia). Druga: model uważa go za mniej prawdopodobny. Porównujemy surowe CLV (przy τ = 1,00 równe CLV po podatku). Wariant „warstwowy” robi to samo osobno dla każdej kombinacji rynku, wyniku i pasma kursów.

| Cena | De-vig | Nogi zostawione | Nogi odrzucone | CLV zostawionych | CLV odrzuconych | Różnica [95% CI] | Różnica warstwowa [95% CI] |
|:---|:---|---:|---:|---:|---:|---:|---:|
| Avg (średnia rynku) | power | 28 | 89 | +1,83% | −2,76% | +4,60 [+0,65; +8,51] pp | +2,63 [−2,69; +7,68] pp |
| Avg (średnia rynku) | proportional | 141 | 179 | −1,80% | −7,84% | +6,04 [+2,32; +10,02] pp | +6,46 [+2,44; +10,74] pp |
| Avg (średnia rynku) | shin | 31 | 82 | −0,58% | −5,36% | +4,78 [−1,33; +10,96] pp | +0,93 [−5,56; +7,33] pp |
| Bet365 | power | 518 | 982 | +0,12% | −0,22% | +0,35 [−0,61; +1,37] pp | +0,67 [−0,61; +2,01] pp |
| Bet365 | proportional | 989 | 1 355 | +0,77% | −0,27% | +1,05 [+0,08; +2,04] pp | +1,32 [+0,19; +2,45] pp |
| Bet365 | shin | 563 | 903 | −0,12% | −0,23% | +0,11 [−0,95; +1,19] pp | +0,39 [−0,91; +1,74] pp |
| Max (najlepsza cena rynku) | power | 4 652 | 7 450 | +1,50% | +0,86% | +0,64 [+0,34; +0,95] pp | +0,86 [+0,50; +1,23] pp |
| Max (najlepsza cena rynku) | proportional | 5 817 | 7 222 | +2,58% | +1,06% | +1,52 [+1,12; +1,91] pp | +1,62 [+1,17; +2,05] pp |
| Max (najlepsza cena rynku) | shin | 4 587 | 6 678 | +1,64% | +0,83% | +0,81 [+0,45; +1,17] pp | +1,06 [+0,64; +1,47] pp |

Przy cenie Max nogi zgodne z modelem mają wyższe CLV o 0,6–1,5 pp, a po kontroli rynku, wyniku i pasma kursów o 0,9–1,6 pp; dolna granica jest dodatnia przy każdej metodzie. Przy Bet365 różnica jest istotna tylko dla de-vig proporcjonalnego. Przy Avg próby są małe. Wygląda to tak, jakby model niósł odrobinę informacji o tym, dokąd Pinnacle przesunie się od otwarcia do zamknięcia. Efekt to jednostki punktu procentowego, a podatek to 12 punktów: wniosku o zyskowności to nie zmienia.

### 3.7 Cena Max i Avg: górna granica tego, co daje porównywanie cen

Max to najwyższy kurs spośród kilkudziesięciu bukmacherów z football-data w chwili zdjęcia, Avg to średnia. Nie da się ich postawić u jednego bukmachera. Pokazują, ile daje samo porównywanie cen z odmarżowanym Pinnacle.

**Max, z = 0**

| Strategia | Reżim | single | AKO 2 | AKO 3 | AKO 4 | AKO 5 |
|:---|:---|---:|---:|---:|---:|---:|
| czysta referencja | τ = 0,88 | 58; −3,6% | 168; −5,0% | 270; −4,3% | 344; −3,3% | 407; −1,8% |
| czysta referencja | τ = 0,94 | 348; −2,1% | 724; −1,6% | 922; −0,3% | **962; +1,0%** | **916; +2,3%** |
| czysta referencja | τ = 1,00 | **8 791; +1,0%** | **4 346; +2,1%** | **2 856; +3,2%** | **2 125; +4,4%** | **1 679; +5,6%** |
| referencja z wetem modelu | τ = 0,88 | **26; +1,5%** | 70; −0,6% | 109; −1,4% | 133; −0,7% | 154; −1,1% |
| referencja z wetem modelu | τ = 0,94 | 141; −0,3% | 273; −0,3% | **348; +0,3%** | **367; +2,0%** | **370; +3,7%** |
| referencja z wetem modelu | τ = 1,00 | **4 176; +1,3%** | **2 035; +2,6%** | **1 327; +4,0%** | **969; +5,4%** | **759; +6,9%** |
| pula z rynkiem | τ = 0,88 | 63; −2,3% | 185; −4,6% | 295; −3,6% | 381; −2,8% | 435; −1,6% |
| pula z rynkiem | τ = 0,94 | 380; −2,0% | 792; −1,3% | 989; −0,3% | **1 000; +0,9%** | **970; +2,2%** |
| pula z rynkiem | τ = 1,00 | **9 083; +1,0%** | **4 491; +2,0%** | **2 963; +3,1%** | **2 198; +4,3%** | **1 743; +5,4%** |
| model residualny | τ = 0,88 | 258; −11,0% | 1 105; −11,5% | 1 444; −11,9% | 1 495; −11,9% | 1 436; −12,0% |
| model residualny | τ = 0,94 | 2 359; −5,8% | 3 102; −5,9% | 2 744; −6,2% | 2 353; −6,7% | 2 028; −7,1% |
| model residualny | τ = 1,00 | 13 093; −0,4% | 6 493; −0,8% | 4 304; −1,2% | 3 199; −1,5% | 2 540; −1,8% |

**Max, z = 1,645**

| Strategia | Reżim | single | AKO 2 | AKO 3 | AKO 4 | AKO 5 |
|:---|:---|---:|---:|---:|---:|---:|
| czysta referencja | τ = 0,88 | 7 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| czysta referencja | τ = 0,94 | 11 (za mało) | 6 (za mało) | 0 | 0 | 0 |
| czysta referencja | τ = 1,00 | **37; +13,3%** | 8 (za mało) | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 0,88 | 3 (za mało) | 0 | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 0,94 | 5 (za mało) | 0 | 0 | 0 | 0 |
| referencja z wetem modelu | τ = 1,00 | 9 (za mało) | 0 | 0 | 0 | 0 |
| pula z rynkiem | τ = 0,88 | 7 (za mało) | 1 (za mało) | 0 | 0 | 0 |
| pula z rynkiem | τ = 0,94 | 10 (za mało) | 6 (za mało) | 0 | 0 | 0 |
| pula z rynkiem | τ = 1,00 | **36; +12,4%** | 7 (za mało) | 1 (za mało) | 0 | 0 |
| model residualny | τ = 0,88 | 3 (za mało) | 10 (za mało) | 14 (za mało) | 17 (za mało) | 18 (za mało) |
| model residualny | τ = 0,94 | 27; −3,7% | 43; −4,6% | 47; −5,2% | 40; −4,6% | 31; −4,0% |
| model residualny | τ = 1,00 | **351; +0,3%** | **150; +0,3%** | **84; +1,0%** | **55; +1,2%** | **36; +1,9%** |

**Max, czysta referencja, z = 0: CLV, yield i obsunięcie (potęgowe de-vig)**

| Reżim | Struktura | Zakładów / kuponów | Mediana CLV po podatku | Pobite zamknięcia (przed podatkiem) | Średnie CLV po podatku [95% CI] | Yield płaski [95% CI] | Yield ¼ Kelly | Obsunięcie płaskie (jedn. 100 zł) | Obsunięcie ¼ Kelly |
|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| τ = 0,88 | single | 58 | +1,60% | 79,3% | +4,44% [−1,40%; +11,31%] | −41,2% [−68,7%; −10,4%] | +5,1% | 28,9 | 9,6% |
| τ = 0,88 | AKO 2 | 168 | −1,29% | 69,0% | −1,38% [−4,97%; +2,30%] | −26,0% [−72,0%; +28,9%] | +9,0% | 61,2 | 8,2% |
| τ = 0,88 | AKO 3 | 270 | −2,77% | 72,2% | −0,60% [−3,47%; +2,24%] | +33,3% [−48,5%; +124,9%] | +73,3% | 46,6 | 4,4% |
| τ = 0,88 | AKO 4 | 344 | −3,21% | 71,2% | −0,64% [−3,25%; +1,95%] | +14,8% [−72,5%; +128,4%] | +12,8% | 40,6 | 2,6% |
| τ = 0,88 | AKO 5 | 407 | +0,04% | 71,5% | +0,64% [−1,78%; +3,26%] | −11,6% [−100,0%; +122,8%] | −20,3% | 31,3 | 1,7% |
| τ = 0,94 | single | 348 | −0,38% | 69,0% | −0,33% [−2,11%; +1,47%] | −18,0% [−37,0%; +2,3%] | −1,8% | 86,1 | 14,4% |
| τ = 0,94 | AKO 2 | 724 | −0,34% | 68,2% | −0,10% [−1,41%; +1,22%] | −11,5% [−32,1%; +10,9%] | −1,0% | 80,2 | 13,0% |
| τ = 0,94 | AKO 3 | 922 | −0,26% | 69,1% | +0,86% [−0,34%; +2,08%] | +13,3% [−16,8%; +44,3%] | +37,7% | 90,6 | 7,1% |
| τ = 0,94 | AKO 4 | 962 | +1,07% | 69,4% | +2,49% [+1,22%; +3,78%] | +15,0% [−25,8%; +59,4%] | −0,1% | 71,1 | 7,3% |
| τ = 0,94 | AKO 5 | 937 | +2,77% | 69,3% | +3,70% [+2,33%; +5,11%] | +49,0% [−16,5%; +118,5%] | +6,7% | 43,1 | 3,5% |
| τ = 1,00 | single | 9 329 | +1,36% | 60,7% | +1,18% [+1,01%; +1,35%] | +1,9% [−0,8%; +4,7%] | +2,7% | 136,4 | 22,2% |
| τ = 1,00 | AKO 2 | 4 616 | +2,30% | 60,6% | +2,40% [+2,06%; +2,74%] | +4,8% [−2,1%; +11,6%] | +4,0% | 178,1 | 19,4% |
| τ = 1,00 | AKO 3 | 3 045 | +3,22% | 61,0% | +3,69% [+3,17%; +4,20%] | +20,5% [+5,0%; +35,5%] | +22,2% | 143,2 | 8,6% |
| τ = 1,00 | AKO 4 | 2 258 | +4,40% | 61,4% | +5,06% [+4,36%; +5,73%] | +10,2% [−10,2%; +30,7%] | +8,0% | 159,1 | 9,7% |
| τ = 1,00 | AKO 5 | 1 794 | +5,34% | 62,3% | +6,50% [+5,62%; +7,41%] | +21,5% [−16,8%; +64,5%] | +23,2% | 91,3 | 5,6% |

**Losowa linia bazowa dla Max, czysta referencja, z = 0**

| Reżim | Struktura | Zakładów | Pobite zamknięcia | Losowo, ta sama liczba i pasmo kursów [5%; 95%] | p (losowo ≥ strategia) | Mediana CLV | Losowo: mediana | Średnie CLV | Losowo: średnia |
|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| τ = 0,88 | single | 58 | 79,3% | 31,3% [22,3%; 41,4%] | 0,005 | +1,60% | −16,01% | +4,44% | −16,03% |
| τ = 0,88 | AKO 2 | 168 | 69,0% | 29,8% [23,8%; 35,7%] | 0,005 | −1,29% | −18,55% | −1,38% | −18,53% |
| τ = 0,88 | AKO 3 | 270 | 72,2% | 26,8% [22,2%; 31,5%] | 0,005 | −2,77% | −20,61% | −0,60% | −20,37% |
| τ = 0,88 | AKO 4 | 344 | 71,2% | 25,3% [20,6%; 28,8%] | 0,005 | −3,21% | −22,09% | −0,64% | −21,67% |
| τ = 0,88 | AKO 5 | 407 | 71,5% | 24,1% [20,9%; 27,8%] | 0,005 | +0,04% | −23,61% | +0,64% | −22,93% |
| τ = 0,94 | single | 348 | 69,0% | 35,0% [31,3%; 39,4%] | 0,005 | −0,38% | −9,10% | −0,33% | −9,59% |
| τ = 0,94 | AKO 2 | 724 | 68,2% | 32,2% [29,3%; 35,0%] | 0,005 | −0,34% | −11,00% | −0,10% | −11,35% |
| τ = 0,94 | AKO 3 | 922 | 69,1% | 30,0% [27,5%; 32,2%] | 0,005 | −0,26% | −12,81% | +0,86% | −12,85% |
| τ = 0,94 | AKO 4 | 962 | 69,4% | 28,7% [26,5%; 31,1%] | 0,005 | +1,07% | −14,28% | +2,49% | −14,03% |
| τ = 0,94 | AKO 5 | 937 | 69,3% | 26,6% [24,2%; 28,6%] | 0,005 | +2,77% | −16,03% | +3,70% | −15,66% |
| τ = 1,00 | single | 9 329 | 60,7% | 41,0% [40,4%; 41,7%] | 0,005 | +1,36% | −1,37% | +1,18% | −1,91% |
| τ = 1,00 | AKO 2 | 4 616 | 60,6% | 36,1% [34,9%; 37,1%] | 0,005 | +2,30% | −3,41% | +2,40% | −3,78% |
| τ = 1,00 | AKO 3 | 3 045 | 61,0% | 32,7% [31,2%; 34,3%] | 0,005 | +3,22% | −5,53% | +3,69% | −5,65% |
| τ = 1,00 | AKO 4 | 2 258 | 61,4% | 30,1% [28,7%; 31,8%] | 0,005 | +4,40% | −7,53% | +5,06% | −7,38% |
| τ = 1,00 | AKO 5 | 1 794 | 62,3% | 27,7% [26,2%; 29,4%] | 0,005 | +5,34% | −9,61% | +6,50% | −9,15% |

Dla singli przy τ = 1,00 średnia przewaga na otwarciu to +1,9%, a średnie CLV +1,2%: mniej niż połowa przewagi przetrwała do zamknięcia, ale reszta jest dodatnia i pokonuje los. Cena Avg (średnia rynku) nie ma dodatniej dolnej granicy w żadnym z 120 wariantów (0 dodatnich).

**Stabilność w sezonach (τ = 1,00, single, potęgowe de-vig)**

| Wariant (τ = 1,00, single) | Sezon | Zakładów | Średnie CLV | Mediana CLV | Pobite zamknięcia |
|:---|---:|---:|---:|---:|---:|
| Max, czysta referencja, z = 0 | 2023 | 4 373 | +1,43% | +1,56% | 62,1% |
| Max, czysta referencja, z = 0 | 2024 | 3 864 | +0,99% | +1,32% | 59,9% |
| Max, czysta referencja, z = 0 | 2025 | 1 092 | +0,86% | +0,85% | 58,5% |
| polska cena z Bet365 (marża 4%), czysta referencja, z = 0 | 2023 | 2 017 | +0,90% | +1,34% | 60,3% |
| polska cena z Bet365 (marża 4%), czysta referencja, z = 0 | 2024 | 2 044 | +0,64% | +1,22% | 58,0% |
| polska cena z Bet365 (marża 4%), czysta referencja, z = 0 | 2025 | 696 | +0,58% | +0,91% | 60,1% |
| Bet365, czysta referencja, z = 0 | 2023 | 792 | +0,16% | +0,66% | 54,7% |
| Bet365, czysta referencja, z = 0 | 2024 | 523 | −0,57% | +0,52% | 54,9% |
| Bet365, czysta referencja, z = 0 | 2025 | 162 | 0,00% | +0,22% | 54,9% |

Tabela sprawdza tylko, czy znak wyniku powtarza się w każdym sezonie. Nie czytać jej jako trendu skuteczności rynku: marża w kolumnach Pinnacle zmieniła się między sezonami (3,2% w 2022, 4,8% w 2024), więc CLV z różnych sezonów nie jest porównywalny (HANDOFF, sekcja C, punkt 3).

### 3.8 Kontrola: sam model

| Reżim | z | Single: zakładów | CLV po podatku [95% CI] | Pobite zamknięcia | Yield płaski [95% CI] |
|:---|---:|---:|---:|---:|---:|
| τ = 0,88 | 1,645 | 304 | −15,9% [−17,4%; −14,4%] | 32,3% | −31,1% [−47,1%; −15,3%] |
| τ = 0,88 | 0 | 6 253 | −17,9% [−18,2%; −17,6%] | 23,3% | −22,9% [−26,4%; −19,5%] |
| τ = 0,94 | 1,645 | 684 | −9,8% [−10,8%; −8,9%] | 32,4% | −13,1% [−23,6%; −1,7%] |
| τ = 0,94 | 0 | 12 002 | −11,9% [−12,1%; −11,7%] | 22,8% | −15,5% [−17,9%; −13,0%] |
| τ = 1,00 | 1,645 | 1 479 | −4,6% [−5,2%; −4,0%] | 28,2% | −7,9% [−14,9%; −0,7%] |
| τ = 1,00 | 0 | 18 052 | −6,0% [−6,1%; −5,8%] | 22,0% | −8,6% [−10,5%; −6,6%] |

Sam model, bez ceny referencyjnej, wybiera zakłady, na których rynek się z nim nie zgadza, i na tych zakładach przegrywa z zamknięciem. To ta sama diagnoza co w HANDOFF: model mało wie.

### 3.9 Czułość: rynki osobno i sezon 2022

| Rynek | Strategia (Bet365, z = 0, τ = 1,00, single) | Sezony | Zakładów | CLV po podatku [95% CI] |
|:---|:---|:---|---:|---:|
| 1X2 | czysta referencja | sezony 2023–2025 | 1 400 | −0,20% [−0,70%; +0,30%] |
| 1X2 | referencja z wetem modelu | sezony 2023–2025 | 472 | −0,06% [−0,87%; +0,75%] |
| 1X2 | pula z rynkiem | sezony 2023–2025 | 1 502 | −0,12% [−0,59%; +0,36%] |
| 1X2 | model residualny | sezony 2023–2025 | 5 822 | −2,70% [−2,91%; −2,51%] |
| over/under 2,5 | czysta referencja | sezony 2023–2025 | 99 | +1,02% [−0,59%; +2,60%] |
| over/under 2,5 | referencja z wetem modelu | sezony 2023–2025 | 46 | +1,97% [−0,80%; +4,85%] |
| over/under 2,5 | pula z rynkiem | sezony 2023–2025 | 100 | +1,13% [−0,50%; +2,73%] |
| 1X2 i over/under 2,5 | czysta referencja | sezony 2022–2025 | 2 268 | +0,20% [−0,17%; +0,57%] |
| 1X2 i over/under 2,5 | model residualny | sezony 2022–2025 | 8 842 | −3,24% [−3,41%; −3,06%] |

Osobne rynki i dodatkowy sezon 2022 (strategie bez ex ante modelu) nie zmieniają obrazu. Model residualny przegrywa z zamknięciem o ok. 3 punkty CLV: jego wybór nie przebija ceny zamknięcia. Wszystkie warianty są w tabeli `strategy_summary`.

## 4. Przybliżenie polskiego bukmachera

**Marża.** W archiwum kursów na żywo (`api-football-odds`) nie ma żadnego polskiego bukmachera (ani Superbetu, ani STS, Fortuny, Betclica). Poniżej marże innych bukmacherów z archiwum na meczach, które kwotuje też Pinnacle. Marże w archiwum są wyższe niż w football-data, bo obejmują wszystkie ligi i mecze do 6 dni naprzód; porównywać trzeba różnicę do Pinnacle.

| Bukmacher (1X2, wrzesień 2026) | Mecze | Marża średnia | Marża mediana | Mecze z Pinnacle | Różnica do Pinnacle |
|:---|---:|---:|---:|---:|---:|
| 1xBet | 439 | 6,7% | 5,1% | 310 | −0,8 pp |
| Pinnacle | 316 | 7,4% | 7,0% | 316 | 0,0 pp |
| Betano | 375 | 8,0% | 8,3% | 290 | +0,4 pp |
| Betfair | 412 | 9,8% | 10,3% | 306 | +2,6 pp |
| Marathonbet | 408 | 10,2% | 11,8% | 310 | +2,7 pp |
| William Hill | 342 | 11,0% | 10,9% | 303 | +3,8 pp |
| Bet365 | 383 | 11,3% | 10,8% | 281 | +2,4 pp |
| BetVictor | 246 | 11,5% | 9,5% | 215 | +3,8 pp |
| SBO | 127 | 15,7% | 15,2% | 127 | +9,5 pp |

Marża (nadwyżka sumy odwrotności kursów nad 1) w football-data, średnia po sezonach. Bet365 leży 1,6–2,7 punktu powyżej Pinnacle w 1X2:

| Rynek | Źródło | 2023 | 2024 | 2025 |
|:---|:---|---:|---:|---:|
| 1X2 | Pinnacle, otwarcie | 3,5% | 4,8% | 4,6% |
| 1X2 | Bet365, otwarcie | 5,7% | 6,4% | 7,3% |
| 1X2 | średnia rynku, otwarcie | 5,8% | 6,1% | 7,5% |
| 1X2 | najlepsza cena, otwarcie | 1,4% | 2,0% | 3,2% |
| over/under 2,5 | Pinnacle, otwarcie | 3,7% | 4,3% | 4,2% |
| over/under 2,5 | Bet365, otwarcie | 5,4% | 5,3% | 5,5% |
| over/under 2,5 | średnia rynku, otwarcie | 5,8% | 5,9% | 7,4% |
| over/under 2,5 | najlepsza cena, otwarcie | 2,0% | 2,4% | 4,1% |

**Założenie:** marża polskiego bukmachera 6% (`DEFAULT_POLISH_OVERROUND = 1,06`). To poziom Bet365 (5,7–7,3% w 1X2) i mediany dziesięciu bukmacherów z football-data (6,2%, Audyt 33), czyli ok. 2 punkty powyżej Pinnacle, jak zmierzone różnice miękkich bukmacherów. Jedyny zmierzony mecz STS (1X2 na Ekstraklasie) miał 3,5% (Audyt 27, po korekcie sumy). Dlatego sprawdzamy też 4% (tyle ma sam Pinnacle: scenariusz optymistyczny) i 8%. **To założenie, nie pomiar.**

**Dwie syntetyczne polskie ceny.** „Polska cena z Pinnacle”: cena Pinnacle z otwarcia z marżą podmienioną na polską. Uczciwy rozkład jest Pinnacle, więc wartość może dać tylko prawdziwa informacja modelu. „Polska cena z Bet365”: cena Bet365 z marżą sprowadzoną do polskiej; zachowuje odchylenia miękkiego bukmachera od Pinnacle. Pierwsza prawie nigdy nie przebija uczciwej ceny. Druga zależy od marży:

| Cena | Marża | Reżim | Referencja, single | Referencja, AKO 5 | Pula z rynkiem, single | Pula z rynkiem, AKO 5 |
|:---|---:|:---|---:|---:|---:|---:|
| polska cena z Bet365 | 4% | τ = 0,88 | 22; −6,9% [dolna −13,6%] | 198; −2,8% [dolna −6,2%] | 22; −5,3% [dolna −10,5%] | 207; −4,1% [dolna −7,4%] |
| polska cena z Bet365 | 4% | τ = 0,94 | 158; −3,8% [dolna −5,6%] | 401; +1,1% [dolna −1,3%] | 175; −3,3% [dolna −5,1%] | 432; +0,9% [dolna −1,2%] |
| polska cena z Bet365 | 4% | τ = 1,00 | **4 130; +0,7% [dolna +0,5%]** | **755; +4,2% [dolna +2,8%]** | **4 289; +0,8% [dolna +0,6%]** | **781; +4,7% [dolna +3,0%]** |
| polska cena z Bet365 | 6% | τ = 0,88 | 11 (za mało) | 73; −10,1% [dolna −15,9%] | 12 (za mało) | 80; −10,1% [dolna −15,9%] |
| polska cena z Bet365 | 6% | τ = 0,94 | 72; −5,4% [dolna −8,8%] | 129; −5,2% [dolna −9,2%] | 73; −4,8% [dolna −8,5%] | 137; −5,8% [dolna −9,9%] |
| polska cena z Bet365 | 6% | τ = 1,00 | 1 389; −0,1% [dolna −0,6%] | 205; −0,2% [dolna −3,3%] | 1 500; −0,2% [dolna −0,7%] | 228; −0,1% [dolna −3,4%] |
| polska cena z Bet365 | 8% | τ = 0,88 | 6 (za mało) | 22; −16,3% [dolna −27,1%] | 6 (za mało) | 28; −15,5% [dolna −24,1%] |
| polska cena z Bet365 | 8% | τ = 0,94 | 40; −6,5% [dolna −11,7%] | 40; −10,8% [dolna −18,8%] | 38; −6,1% [dolna −11,9%] | 47; −11,2% [dolna −19,7%] |
| polska cena z Bet365 | 8% | τ = 1,00 | 482; −1,5% [dolna −2,5%] | 48; −5,2% [dolna −12,8%] | 530; −1,6% [dolna −2,6%] | 57; −6,0% [dolna −13,7%] |
| polska cena z Pinnacle | 4% | τ = 0,88 | 0 | 0 | 0 | 0 |
| polska cena z Pinnacle | 4% | τ = 0,94 | 0 | 0 | 0 | 3 (za mało) |
| polska cena z Pinnacle | 4% | τ = 1,00 | 0 | 0 | 80; −1,7% [dolna −4,1%] | 3 (za mało) |
| polska cena z Pinnacle | 6% | τ = 0,88 | 0 | 0 | 0 | 0 |
| polska cena z Pinnacle | 6% | τ = 0,94 | 0 | 0 | 0 | 0 |
| polska cena z Pinnacle | 6% | τ = 1,00 | 0 | 0 | 8 (za mało) | 0 |
| polska cena z Pinnacle | 8% | τ = 0,88 | 0 | 0 | 0 | 0 |
| polska cena z Pinnacle | 8% | τ = 0,94 | 0 | 0 | 0 | 0 |
| polska cena z Pinnacle | 8% | τ = 1,00 | 0 | 0 | 2 (za mało) | 0 |

Komórka: liczba zakładów; średnie CLV po podatku (najgorsza z trzech metod de-vig); w nawiasie dolna granica. Pogrubienie: dolna granica dodatnia przy wszystkich trzech metodach. Przy „polskiej cenie z Pinnacle” referencja nie znajduje zakładów, a pula prawie ich nie znajduje: model nie ma dość informacji, żeby przebić marżę. Dodatni wynik ma tylko cena z szumem Bet365, marża 4% i τ = 1,00. Przy marży 6% lub 8% wyniku dodatniego nie ma nawet bez podatku, a przy τ = 0,94 i 0,88 nie ma go dla singli w żadnym scenariuszu.

## 5. Ograniczenia

- Kursy „otwarcia” to jedno zdjęcie tygodniowe, nie cena w chwili, gdy skaner by działał. CLV liczymy do zamknięcia Pinnacle. Nie wiemy, czy cena była jeszcze dostępna po kilku minutach.
- Sezon 2025/26 ma kursy Pinnacle tylko do stycznia 2026 (ok. 40% meczów), więc rok 2025 wnosi mniej niż 2023 i 2024.
- Polskie ceny to przybliżenia z założoną marżą. Nie znamy ich rozrzutu względem Pinnacle, opóźnienia ani limitów stawek.
- Max i Avg to ceny, których nie da się postawić u jednego bukmachera. Dodatnie CLV przy Max mierzy rozrzut cen, nie zysk do zrealizowania.
- Model wybrany ex ante (`selected_best`) to `pi_gbm` w 1X2 i w over/under 2,5 (w 2025 `gas_poisson`). Wybór zależy od poprzedniego sezonu, ale sam zestaw modeli był dobierany na tych samych sezonach testowych (raport pytania 1).
- Podatek to mnożnik wypłaty τ. PIT od wygranej powyżej 2 280 zł obcina stawkę płaską i Kelly'ego, więc długie AKO mają stawkę mniejszą niż 100 zł.
- Handicap azjatycki nie był testowany. Rynki rożnych, kartek i strzałów nie mają kursów historycznych.
- Wielokrotne testowanie: trzy metody de-vig i wiele wariantów. Korekta Holma działa na rodzinie i na wszystkich wariantach; wariant z małą próbą wchodzi z p = 1, więc korekta nie jest łagodna. P-wartości wariantów z dużą próbą pochodzą z przybliżenia normalnego z błędem standardowym z bootstrapu (bootstrap sam nie zejdzie poniżej 1/4 001).

## 6. Pliki

- Kod: `app/src/model_buk/evaluation/market_test.py`, `app/scripts/run_market_test.py`, testy `app/tests/test_market_test.py`.
- Wyniki: `Model-Buk-Data/market_test/market_test.sqlite` (tabele: `loss_by_league`, `loss_pooled`, `loss_by_season`, `pool_weights`, `residual_selection`, `strategy_summary`, `strategy_verdicts`, `strategy_bets`, `veto_effect`, `live_margins`, `overround`).
- Odtworzenie: `.venv\Scripts\python scripts\run_market_test.py --stage all --workers 3` (kilkanaście minut, do 3 procesów). Etap `panel` czyta bazę `fd_af_matches.sqlite` w całości; po dołączeniu do niej nowych lig i pucharów wyniki policzone od nowa obejmą także te mecze.
- Kontrola niezależna (recenzja): Bet365, z = 0, single przy τ = 1,00 oraz AKO 2, 3 i 5 przy τ = 1,00 i 0,88, a także Max, z = 0, single przy τ = 1,00 zostały przeliczone od zera z surowej bazy `fd_af_matches.sqlite` innym kodem. Te same zakłady, te same CLV. Log-loss w 1X2 (otwarcie, zamknięcie, pula modeli, 15 875 meczów) też się zgadza. Test przecieku na prawdziwych danych: po losowym przemieszaniu kursów zamknięcia sezonu podejmującego decyzję żadna z 30 kombinacji strategii, ceny i progu nie zmienia typów.
