# Modele 1X2 i gole: który jest najlepszy i czy bije częstość ligową? (pytanie 1)

**Odpowiedź na pytanie 1. Tak: 1X2 (zysk 6,0% log-lossu baseline'u). Skromnie: Gole 1,5, Gole 2,5, Gole 3,5 (zysk 0,7%–1,0% log-lossu baseline'u). Minimalnie: BTTS (zysk 0,3% log-lossu baseline'u).**

Porównaliśmy 6 modeli (w rynkach goli bez `elo_davidson`, który ma tylko 1X2) z baseline'em (częstości wyników w lidze) tą samą procedurą na 55 244 meczach z 47 lig, sezony 2022–2025. W 1X2 najlepszy pojedynczy model (`pi_gbm`) ma log-loss 1,0095 wobec 1,0736 u baseline'u, czyli 0,0641 lepiej. Po korekcie Holma jest istotnie lepszy w 47 z 47 lig. W rynkach goli przewaga jest o rząd wielkości mniejsza, a modele oparte tylko na bramkach mają w BTTS zysk bliski zera. To jest filtr, nie dowód: o sensie modelu rozstrzyga pytanie 2 (model kontra kursy zamknięcia Pinnacle), a o pieniądzach pytanie 3.

## Odpowiedź w skrócie

Zysk to log-loss baseline'u minus log-loss modelu, więc dodatnia liczba oznacza przewagę modelu. Przedział „po korekcie” to przedział Bonferroniego dla całej rodziny testów (wszystkie pary model–rynek względem baseline'u, 26 testów), więc obowiązuje łącznie, nie dla pojedynczego porównania.

| Rynek | Werdykt | Najlepszy pojedynczy model (2022–2025) | Log-loss modelu / baseline'u | Zysk [CI po korekcie] | Ligi istotnie lepsze po Holmie | Pula `pool_models` (2023–2025): zysk vs baseline / vs najlepszy pojedynczy |
|---|---|---|---|---|---|---|
| 1X2 (wynik meczu) | wyraźnie lepszy | `pi_gbm` | 1,0095 / 1,0736 | +0,0641 [+0,0594; +0,0687] (6,0%) | 47 z 47 | +0,0651 (6,1%) / +0,0016 |
| Gole powyżej 1,5 | lekko lepszy | `pi_gbm` | 0,5552 / 0,5590 | +0,0038 [+0,0026; +0,0051] (0,7%) | 3 z 47 | +0,0057 (1,0%) / +0,0014 |
| Gole powyżej 2,5 | lekko lepszy | `pi_gbm` | 0,6825 / 0,6889 | +0,0064 [+0,0048; +0,0079] (0,9%) | 8 z 47 | +0,0085 (1,2%) / +0,0020 |
| Gole powyżej 3,5 | lekko lepszy | `pi_gbm` | 0,5921 / 0,5980 | +0,0059 [+0,0044; +0,0074] (1,0%) | 4 z 47 | +0,0085 (1,4%) / +0,0021 |
| Obie drużyny strzelą (BTTS) | minimalnie lepszy | `pi_gbm` | 0,6875 / 0,6896 | +0,0021 [+0,0011; +0,0031] (0,3%) | 1 z 47 | +0,0031 (0,5%) / +0,0007 |

Jak daleko od baseline'u? W 1X2 średnie (geometryczne) prawdopodobieństwo, które model przypisał temu, co się naprawdę stało, to 36,4% wobec 34,2% u baseline'u. To realna, ale niewielka przewaga: częstości ligowe już same dają log-loss 1,0736, a idealna wiedza o wyniku dałaby 0. W rynkach goli i BTTS zysk to 0,3%–1,0% log-lossu baseline'u.

- **1X2 (wynik meczu): wyraźnie lepszy.** Ligi istotnie lepsze od baseline'u po Holmie (z 47): `dixon_coles` 47, `gas_poisson` 47, `pi_gbm` 47, `nb2` 45, `elo_davidson` 37, `pi_ratings` 37. Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: 0.
- **Gole powyżej 1,5: lekko lepszy.** Ligi istotnie lepsze od baseline'u po Holmie (z 47): `pi_gbm` 3, `dixon_coles` 1, `nb2` 1, `gas_poisson` 1, `pi_ratings` 1. Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: 0. Modele oparte tylko na bramkach: `dixon_coles` +0,0019, `nb2` +0,0019, `gas_poisson` +0,0025, istotne po korekcie, ale mniejsze niż u `pi_gbm`.
- **Gole powyżej 2,5: lekko lepszy.** Ligi istotnie lepsze od baseline'u po Holmie (z 47): `pi_gbm` 8, `pi_ratings` 4, `gas_poisson` 1, `dixon_coles` 0, `nb2` 0. Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: 0. Modele oparte tylko na bramkach: `dixon_coles` +0,0035, `nb2` +0,0035, `gas_poisson` +0,0044, istotne po korekcie, ale mniejsze niż u `pi_gbm`.
- **Gole powyżej 3,5: lekko lepszy.** Ligi istotnie lepsze od baseline'u po Holmie (z 47): `pi_gbm` 4, `pi_ratings` 1, `dixon_coles` 0, `nb2` 0, `gas_poisson` 0. Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: 0. Modele oparte tylko na bramkach: `dixon_coles` +0,0042, `nb2` +0,0042, `gas_poisson` +0,0047, istotne po korekcie, ale mniejsze niż u `pi_gbm`.
- **Obie drużyny strzelą (BTTS): minimalnie lepszy.** Ligi istotnie lepsze od baseline'u po Holmie (z 47): `dixon_coles` 1, `nb2` 1, `gas_poisson` 1, `pi_gbm` 1, `pi_ratings` 0. Ligi, w których baseline jest istotnie lepszy od najlepszego modelu: 0. Modele oparte tylko na bramkach: `dixon_coles` -0,0002, `nb2` -0,0004, `gas_poisson` +0,0001; bez istotnej przewagi po korekcie: `dixon_coles`, `nb2`, `gas_poisson`.

**Najlepszy model wybrany tylko na foldach wewnętrznych** (na sezonie przed ocenianym; wybór na kolejny sezon, 2026, opiera się na sezonie 2025):

| Rynek | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| 1X2 | `pi_gbm` | `pi_gbm` | `pi_gbm` | `pi_gbm` |
| Gole 1,5 | `pi_ratings` | `pi_gbm` | `pi_gbm` | `pi_gbm` |
| Gole 2,5 | `pi_gbm` | `pi_gbm` | `gas_poisson` | `pi_gbm` |
| Gole 3,5 | `pi_gbm` | `pi_gbm` | `gas_poisson` | `pi_gbm` |
| BTTS | `pi_gbm` | `pi_gbm` | `pi_gbm` | `pi_gbm` |

Pula `pool_models` (średnia geometryczna prognoz z wagami dobranymi na poprzednim sezonie) jest w każdym rynku lepsza od najlepszego pojedynczego modelu po fakcie (2023–2025): 1X2 +0,0016, Gole 1,5 +0,0014, Gole 2,5 +0,0020, Gole 3,5 +0,0021, BTTS +0,0007 (różnica log-lossu; test Diebolda–Mariano p < 0,05 w 5 z 5 rynków). Szczegóły i przedziały niżej. Do pytania 2 proponuję użyć `pool_models`; `selected_best` zostaje jako prostsza alternatywa.

## Jak mierzyliśmy

- **Ten sam protokół dla wszystkich modeli** (`evaluation/protocol.py`): sezony testowe 2022–2025, jeden model na ligę, dopasowywany od nowa przed każdą kolejką na meczach zakończonych co najmniej 3 godziny przed pierwszym gwizdkiem kolejki; hiperparametry wybierane na jednym sezonie przed sezonem testowym. Etykieta sezonu to rok startu według dostawcy (2025 = sezon 2025/26; w ligach z rokiem kalendarzowym po prostu rok). Prognozy modeli pochodzą z zapisanych plików `predictions/<model>.sqlite`, niczego nie liczyliśmy od nowa poza baseline'em.
- **Te same mecze.** W każdym rynku porównujemy modele na przecięciu prognoz wszystkich modeli i baseline'u: 55 244 z 55 449 meczów (niżej, ile odpadło i dlaczego). Brak prognozy zostaje brakiem, nigdy nie jest liczony jako zero.
- **Metryki.** Główna to log-loss, pomocniczo Brier i RPS (uporządkowane klasy). Przedziały 95% to bootstrap po kolejkach (2 000 losowań, ziarno 0; całe kolejki ligi losowane ze zwracaniem, bo mecze jednej kolejki nie są niezależne).
- **Testy.** Test Diebolda–Mariano z wariancją HAC po kolejkach: każdy model kontra baseline i kontra najlepszy z pozostałych. Korekta Holma i Benjaminiego–Hochberga liczona między ligami (rodzina 47 lig osobno dla modelu i rynku). Przedziały po korekcie to przedziały Bonferroniego z tą samą statystyką co test (przedział wyklucza zero dokładnie wtedy, gdy p < 0,05 / liczba porównań). Wyniki zbiorcze mają dodatkową korektę Holma po wszystkich parach model–rynek.
- **Wybór bez podglądania.** Dla sezonu S wybieramy model o najlepszym log-lossie na prognozach sezonu S − 1 (same są poza próbą) i oceniamy ten wybór na S. Sezon 2022 służy tylko do wyboru dla 2023, więc `selected_best` i `pool_models` mają prognozy od 2023. Pula ma wagi z S − 1 (nieujemne, suma 1, delikatna kara 1e-4 ku równym wagom, żeby prawie identyczne modele dzieliły wagę). W puli jest także baseline, więc pula może się cofnąć do częstości ligowych, gdy modele nic nie wnoszą. Ten sam wybór z sezonu S − 1 obowiązuje wszystkie ligi.
- **Modele w tabelach:**

  - `baseline_league_freq`: częstości wyników w lidze, liczone na danych treningowych (punkt odniesienia).
  - `dixon_coles`: Dixon–Coles: siły ataku i obrony drużyn z ważeniem czasu, półokres z folda wewnętrznego.
  - `nb2`: jak Dixon–Coles, ale z rozkładem ujemnym dwumianowym.
  - `gas_poisson`: Poisson z siłami drużyn, które zmieniają się po każdym meczu (GAS).
  - `elo_davidson`: rating Elo z rozkładem Davidsona (tylko 1X2).
  - `pi_ratings`: pi-ratingi i poziom ligi w regresji logistycznej.
  - `pi_gbm`: pi-ratingi, forma (gole, strzały, rzuty rożne) i poziom ligi w gradient boostingu.
  - `selected_best`: model z najlepszym log-lossem na poprzednim sezonie.
  - `pool_models`: pula liniowo-logarytmiczna wszystkich modeli i baseline'u, wagi z poprzedniego sezonu.
  - `selected_best_league`: jak wyżej, ale osobno dla każdej ligi, ściągnięte do wyboru zbiorczego (kontrola).
  - `pool_models_linear`: pula liniowa (zwykła średnia ważona), wagi z poprzedniego sezonu (kontrola).

## Które mecze weszły do porównania

W archiwum jest 55 449 meczów sezonów testowych 2022–2025 w 47 ligach. Do wspólnej próby weszło 55 244 (99,63%). Odpadło 205:

- **105 meczów bez prognozy u wszystkich modeli i baseline'u** (CHALLENGERPROL 55, PREMIERDIVISIO 50). To pierwsze kolejki lig, których archiwum zaczyna się dopiero w sezonie testowym: rama wymaga co najmniej 50 meczów historii ligi przed kolejką.
- `pi_ratings` nie ma prognozy dla 100 meczów, które baseline ma (PREMIERDIVISIO 52, CHALLENGERPROL 48); dotyczy rynków: 1X2, Gole 2,5, Gole 1,5, Gole 3,5, BTTS. Według opisu modelu brakuje tam poziomu ligi z ostatnich 730 dni (początek archiwum tych lig). Ponieważ porównujemy na przecięciu, wszystkie modele tracą te mecze (0,18% próby).
- `elo_davidson` ma prognozy tylko dla 1X2, więc w rynkach goli go nie ma.

We wszystkich rynkach wspólna próba to 55 244 meczów. Liczby dla każdego modelu (1X2; w rynkach goli te same, bez `elo_davidson`):

| Model | Ma prognozę | Bez prognozy | w tym też bez prognozy u baseline'u | bez prognozy tylko u tego modelu | Utracone przez brak u innych | Wspólna próba |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_league_freq` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `dixon_coles` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `nb2` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `gas_poisson` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `elo_davidson` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `pi_gbm` | 55 344 | 105 | 105 | 0 | 100 | 55 244 |
| `pi_ratings` | 55 244 | 205 | 105 | 100 | 0 | 55 244 |

## Wynik zbiorczy, sezony 2022–2025

Modele od najlepszego. „p po Holmie” to p-wartość testu Diebolda–Mariano dla wyniku zbiorczego po korekcie Holma na wszystkie pary model–rynek. „Ligi lepsze” to liczba lig, w których model jest istotnie lepszy od baseline'u na poziomie 5% bez korekty, po korekcie Holma i po korekcie Benjaminiego–Hochberga (z liczby lig w ocenie); „Ligi gorsze (Holm)” to liczba lig, w których baseline jest istotnie lepszy. Pełne tabele per liga (log-loss z przedziałami, testy, przedziały po korekcie Bonferroniego) są w `predictions/model_comparison.sqlite` (tabele `scores`, `versus_reference`, `versus_best`).

### 1X2 (wynik meczu)

| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |
|---|---|---:|---:|---|---|---:|---|---:|
| `pi_gbm` | 1,0095 [1,0059; 1,0132] | 0,6042 | 0,2074 | +0,0641 [+0,0611; +0,0673] | [+0,0594; +0,0687] | < 0,0001 | 47 / 47 / 47 z 47 | 0 |
| `gas_poisson` | 1,0137 [1,0102; 1,0169] | 0,6069 | 0,2087 | +0,0599 [+0,0574; +0,0627] | [+0,0559; +0,0640] | < 0,0001 | 47 / 47 / 47 z 47 | 0 |
| `dixon_coles` | 1,0137 [1,0103; 1,0167] | 0,6070 | 0,2087 | +0,0599 [+0,0574; +0,0626] | [+0,0559; +0,0640] | < 0,0001 | 47 / 47 / 47 z 47 | 0 |
| `nb2` | 1,0140 [1,0106; 1,0171] | 0,6071 | 0,2087 | +0,0596 [+0,0571; +0,0623] | [+0,0556; +0,0637] | < 0,0001 | 46 / 45 / 46 z 47 | 0 |
| `pi_ratings` | 1,0157 [1,0120; 1,0193] | 0,6083 | 0,2095 | +0,0579 [+0,0550; +0,0611] | [+0,0532; +0,0626] | < 0,0001 | 44 / 37 / 44 z 47 | 0 |
| `elo_davidson` | 1,0164 [1,0126; 1,0201] | 0,6087 | 0,2096 | +0,0572 [+0,0542; +0,0603] | [+0,0524; +0,0619] | < 0,0001 | 43 / 37 / 43 z 47 | 0 |
| `baseline_league_freq` | 1,0736 [1,0717; 1,0755] | 0,6497 | 0,2295 | — | — | — | — | — |

`pi_gbm` kontra najlepszy z pozostałych (`gas_poisson`): różnica +0,0041 [+0,0029; +0,0053], p < 0,0001; lepszy w 6 z 47 lig na poziomie 5%, po Holmie w 0. Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru.

### Gole powyżej 1,5

| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |
|---|---|---:|---:|---|---|---:|---|---:|
| `pi_gbm` | 0,5552 [0,5510; 0,5594] | 0,3699 | 0,1849 | +0,0038 [+0,0030; +0,0046] | [+0,0026; +0,0051] | < 0,0001 | 13 / 3 / 6 z 47 | 0 |
| `pi_ratings` | 0,5564 [0,5521; 0,5604] | 0,3707 | 0,1854 | +0,0027 [+0,0020; +0,0033] | [+0,0016; +0,0037] | < 0,0001 | 13 / 1 / 1 z 47 | 0 |
| `gas_poisson` | 0,5566 [0,5524; 0,5606] | 0,3710 | 0,1855 | +0,0025 [+0,0013; +0,0037] | [+0,0006; +0,0044] | 0,0004 | 4 / 1 / 1 z 47 | 0 |
| `nb2` | 0,5572 [0,5529; 0,5612] | 0,3715 | 0,1858 | +0,0019 [+0,0007; +0,0030] | [+0,0001; +0,0037] | 0,0078 | 7 / 1 / 1 z 47 | 0 |
| `dixon_coles` | 0,5572 [0,5528; 0,5613] | 0,3715 | 0,1857 | +0,0019 [+0,0007; +0,0030] | [+0,0000; +0,0037] | 0,0078 | 7 / 1 / 1 z 47 | 0 |
| `baseline_league_freq` | 0,5590 [0,5549; 0,5630] | 0,3726 | 0,1863 | — | — | — | — | — |

`pi_gbm` kontra najlepszy z pozostałych (`pi_ratings`): różnica +0,0012 [+0,0005; +0,0017], p = 0,0002; lepszy w 6 z 47 lig na poziomie 5%, po Holmie w 0. Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru.

### Gole powyżej 2,5

| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |
|---|---|---:|---:|---|---|---:|---|---:|
| `pi_gbm` | 0,6825 [0,6813; 0,6838] | 0,4895 | 0,2447 | +0,0064 [+0,0054; +0,0073] | [+0,0048; +0,0079] | < 0,0001 | 19 / 8 / 17 z 47 | 0 |
| `pi_ratings` | 0,6844 [0,6833; 0,6856] | 0,4914 | 0,2457 | +0,0044 [+0,0036; +0,0053] | [+0,0031; +0,0058] | < 0,0001 | 18 / 4 / 12 z 47 | 0 |
| `gas_poisson` | 0,6845 [0,6829; 0,6862] | 0,4913 | 0,2457 | +0,0044 [+0,0028; +0,0058] | [+0,0021; +0,0067] | < 0,0001 | 11 / 1 / 1 z 47 | 0 |
| `nb2` | 0,6854 [0,6838; 0,6871] | 0,4922 | 0,2461 | +0,0035 [+0,0020; +0,0049] | [+0,0013; +0,0057] | < 0,0001 | 12 / 0 / 4 z 47 | 0 |
| `dixon_coles` | 0,6854 [0,6838; 0,6871] | 0,4922 | 0,2461 | +0,0035 [+0,0020; +0,0049] | [+0,0013; +0,0057] | < 0,0001 | 11 / 0 / 4 z 47 | 0 |
| `baseline_league_freq` | 0,6889 [0,6880; 0,6898] | 0,4958 | 0,2479 | — | — | — | — | — |

`pi_gbm` kontra najlepszy z pozostałych (`pi_ratings`): różnica +0,0019 [+0,0012; +0,0025], p < 0,0001; lepszy w 9 z 47 lig na poziomie 5%, po Holmie w 0. Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru.

### Gole powyżej 3,5

| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |
|---|---|---:|---:|---|---|---:|---|---:|
| `pi_gbm` | 0,5921 [0,5884; 0,5958] | 0,4035 | 0,2018 | +0,0059 [+0,0049; +0,0069] | [+0,0044; +0,0074] | < 0,0001 | 19 / 4 / 15 z 47 | 0 |
| `gas_poisson` | 0,5933 [0,5895; 0,5974] | 0,4043 | 0,2021 | +0,0047 [+0,0032; +0,0062] | [+0,0024; +0,0070] | < 0,0001 | 8 / 0 / 0 z 47 | 0 |
| `nb2` | 0,5939 [0,5899; 0,5979] | 0,4046 | 0,2023 | +0,0042 [+0,0027; +0,0056] | [+0,0019; +0,0065] | < 0,0001 | 9 / 0 / 0 z 47 | 0 |
| `dixon_coles` | 0,5939 [0,5899; 0,5979] | 0,4046 | 0,2023 | +0,0042 [+0,0027; +0,0056] | [+0,0019; +0,0065] | < 0,0001 | 8 / 0 / 0 z 47 | 0 |
| `pi_ratings` | 0,5942 [0,5905; 0,5977] | 0,4052 | 0,2026 | +0,0039 [+0,0030; +0,0047] | [+0,0025; +0,0053] | < 0,0001 | 18 / 1 / 1 z 47 | 0 |
| `baseline_league_freq` | 0,5980 [0,5942; 0,6017] | 0,4084 | 0,2042 | — | — | — | — | — |

`pi_gbm` kontra najlepszy z pozostałych (`gas_poisson`): różnica +0,0012 [+0,0001; +0,0024], p = 0,0408; lepszy w 2 z 47 lig na poziomie 5%, po Holmie w 0. Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru.

### Obie drużyny strzelą (BTTS)

| Model | Log-loss [95% CI] | Brier | RPS | Zysk vs baseline [95% CI] | Zysk, CI po korekcie | p po Holmie (wszystkie pary) | Ligi lepsze: surowo / Holm / BH | Ligi gorsze (Holm) |
|---|---|---:|---:|---|---|---:|---|---:|
| `pi_gbm` | 0,6875 [0,6866; 0,6884] | 0,4944 | 0,2472 | +0,0021 [+0,0015; +0,0027] | [+0,0011; +0,0031] | < 0,0001 | 10 / 1 / 1 z 47 | 0 |
| `pi_ratings` | 0,6888 [0,6880; 0,6896] | 0,4957 | 0,2478 | +0,0008 [+0,0003; +0,0013] | [-0,0001; +0,0017] | 0,0167 | 5 / 0 / 1 z 47 | 1 |
| `gas_poisson` | 0,6895 [0,6882; 0,6907] | 0,4963 | 0,2481 | +0,0001 [-0,0009; +0,0012] | [-0,0014; +0,0017] | 1,0000 | 3 / 1 / 1 z 47 | 1 |
| `dixon_coles` | 0,6898 [0,6886; 0,6911] | 0,4966 | 0,2483 | -0,0002 [-0,0012; +0,0009] | [-0,0018; +0,0014] | 1,0000 | 1 / 1 / 1 z 47 | 1 |
| `nb2` | 0,6900 [0,6888; 0,6912] | 0,4968 | 0,2484 | -0,0004 [-0,0014; +0,0007] | [-0,0020; +0,0012] | 1,0000 | 2 / 1 / 1 z 47 | 1 |
| `baseline_league_freq` | 0,6896 [0,6889; 0,6903] | 0,4965 | 0,2482 | — | — | — | — | — |

`pi_gbm` kontra najlepszy z pozostałych (`pi_ratings`): różnica +0,0013 [+0,0007; +0,0018], p < 0,0001; lepszy w 7 z 47 lig na poziomie 5%, po Holmie w 1. Ranking modeli ustalono na tych samych meczach, więc to porównanie opisowe, a nie test ex ante wyboru.

## Wybór modelu bez podglądania, sezony 2023–2025

Dla każdego sezonu ocenianego wybieramy model na poprzednim sezonie i oceniamy go na tym sezonie. Kolumna „najlepszy po fakcie” pokazuje, kogo wybralibyśmy, gdybyśmy znali wynik sezonu, a „strata wyboru” ile log-lossu kosztuje niewiedza. „Przewaga nad drugim” to różnica log-lossu zwycięzcy i drugiego modelu w próbie wyboru; gdy jest mniejsza niż jej błąd standardowy, wybór jest w praktyce losowaniem między bliskimi modelami.

### 1X2 (wynik meczu)

| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |
|---|---|---|---|---:|
| 2023 | `pi_gbm` | 0,0049 ± 0,0012 (`gas_poisson`) | `pi_gbm` | 0,0000 |
| 2024 | `pi_gbm` | 0,0033 ± 0,0013 (`gas_poisson`) | `pi_gbm` | 0,0000 |
| 2025 | `pi_gbm` | 0,0038 ± 0,0013 (`gas_poisson`) | `pi_gbm` | 0,0000 |
| 2026 (następny) | `pi_gbm` | 0,0032 ± 0,0014 (`dixon_coles`) | — | — |

| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `pi_gbm` (najlepszy po fakcie) [95% CI] | p (DM) |
|---|---|---|---|---|---:|
| `baseline_league_freq` | 1,0739 [1,0716; 1,0761] | — | — | — | — |
| `pi_gbm` | 1,0104 [1,0064; 1,0145] | +0,0635 [+0,0601; +0,0669] | 42 z 47 | — | — |
| `selected_best` | 1,0104 [1,0064; 1,0145] | +0,0635 [+0,0601; +0,0669] | 42 z 47 | identyczne prognozy | — |
| `pool_models` | 1,0088 [1,0050; 1,0129] | +0,0651 [+0,0619; +0,0682] | 45 z 47 | +0,0016 [+0,0011; +0,0021] | < 0,0001 |
| `selected_best_league` | 1,0105 [1,0065; 1,0146] | +0,0634 [+0,0600; +0,0668] | 42 z 47 | -0,0001 [-0,0003; +0,0001] | 0,2703 |
| `pool_models_linear` | 1,0089 [1,0051; 1,0130] | +0,0650 [+0,0618; +0,0681] | 45 z 47 | +0,0015 [+0,0010; +0,0020] | < 0,0001 |

Wagi puli `pool_models` (pominięte modele z wagą 0):

| Model | 2023 | 2024 | 2025 | 2026 (następny) |
|---|---:|---:|---:|---:|
| `dixon_coles` | 0,18 | 0,15 | 0,17 | 0,19 |
| `nb2` | 0,00 | 0,00 | 0,00 | 0,20 |
| `gas_poisson` | 0,11 | 0,22 | 0,13 | 0,00 |
| `elo_davidson` | 0,07 | 0,03 | 0,10 | 0,00 |
| `pi_gbm` | 0,65 | 0,60 | 0,59 | 0,61 |

### Gole powyżej 1,5

| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |
|---|---|---|---|---:|
| 2023 | `pi_ratings` | 0,0000 ± 0,0006 (`pi_gbm`) | `pi_gbm` | 0,0007 |
| 2024 | `pi_gbm` | 0,0007 ± 0,0006 (`pi_ratings`) | `pi_gbm` | 0,0000 |
| 2025 | `pi_gbm` | 0,0007 ± 0,0010 (`gas_poisson`) | `pi_gbm` | 0,0000 |
| 2026 (następny) | `pi_gbm` | 0,0016 ± 0,0010 (`dixon_coles`) | — | — |

| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `pi_gbm` (najlepszy po fakcie) [95% CI] | p (DM) |
|---|---|---|---|---|---:|
| `baseline_league_freq` | 0,5561 [0,5513; 0,5607] | — | — | — | — |
| `pi_gbm` | 0,5518 [0,5467; 0,5568] | +0,0043 [+0,0034; +0,0053] | 3 z 47 | — | — |
| `selected_best` | 0,5520 [0,5469; 0,5569] | +0,0041 [+0,0032; +0,0050] | 2 z 47 | -0,0002 [-0,0006; +0,0002] | 0,2667 |
| `pool_models` | 0,5505 [0,5454; 0,5554] | +0,0057 [+0,0048; +0,0066] | 4 z 47 | +0,0014 [+0,0008; +0,0019] | < 0,0001 |
| `selected_best_league` | 0,5522 [0,5472; 0,5572] | +0,0039 [+0,0030; +0,0047] | 3 z 47 | -0,0004 [-0,0010; +0,0001] | 0,0981 |
| `pool_models_linear` | 0,5505 [0,5455; 0,5555] | +0,0056 [+0,0047; +0,0065] | 4 z 47 | +0,0013 [+0,0008; +0,0018] | < 0,0001 |

Wagi puli `pool_models` (pominięte modele z wagą 0):

| Model | 2023 | 2024 | 2025 | 2026 (następny) |
|---|---:|---:|---:|---:|
| `dixon_coles` | 0,00 | 0,03 | 0,00 | 0,29 |
| `nb2` | 0,21 | 0,00 | 0,17 | 0,00 |
| `gas_poisson` | 0,20 | 0,38 | 0,29 | 0,13 |
| `pi_gbm` | 0,27 | 0,37 | 0,42 | 0,58 |
| `pi_ratings` | 0,33 | 0,22 | 0,12 | 0,00 |

### Gole powyżej 2,5

| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |
|---|---|---|---|---:|
| 2023 | `pi_gbm` | 0,0017 ± 0,0006 (`pi_ratings`) | `pi_gbm` | 0,0000 |
| 2024 | `pi_gbm` | 0,0016 ± 0,0011 (`gas_poisson`) | `gas_poisson` | 0,0001 |
| 2025 | `gas_poisson` | 0,0001 ± 0,0012 (`pi_gbm`) | `pi_gbm` | 0,0017 |
| 2026 (następny) | `pi_gbm` | 0,0017 ± 0,0012 (`gas_poisson`) | — | — |

| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `pi_gbm` (najlepszy po fakcie) [95% CI] | p (DM) |
|---|---|---|---|---|---:|
| `baseline_league_freq` | 0,6896 [0,6885; 0,6906] | — | — | — | — |
| `pi_gbm` | 0,6830 [0,6816; 0,6845] | +0,0065 [+0,0053; +0,0077] | 4 z 47 | — | — |
| `selected_best` | 0,6836 [0,6820; 0,6852] | +0,0059 [+0,0046; +0,0073] | 2 z 47 | -0,0006 [-0,0014; +0,0002] | 0,1531 |
| `pool_models` | 0,6811 [0,6796; 0,6825] | +0,0085 [+0,0073; +0,0097] | 9 z 47 | +0,0020 [+0,0014; +0,0025] | < 0,0001 |
| `selected_best_league` | 0,6842 [0,6826; 0,6858] | +0,0054 [+0,0040; +0,0067] | 2 z 47 | -0,0012 [-0,0020; -0,0003] | 0,0072 |
| `pool_models_linear` | 0,6811 [0,6796; 0,6825] | +0,0085 [+0,0073; +0,0096] | 9 z 47 | +0,0019 [+0,0014; +0,0025] | < 0,0001 |

Wagi puli `pool_models` (pominięte modele z wagą 0):

| Model | 2023 | 2024 | 2025 | 2026 (następny) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | 0,00 | 0,00 | 0,03 | 0,00 |
| `dixon_coles` | 0,08 | 0,04 | 0,00 | 0,23 |
| `nb2` | 0,10 | 0,00 | 0,17 | 0,00 |
| `gas_poisson` | 0,11 | 0,36 | 0,34 | 0,21 |
| `pi_gbm` | 0,61 | 0,45 | 0,44 | 0,43 |
| `pi_ratings` | 0,10 | 0,14 | 0,02 | 0,12 |

### Gole powyżej 3,5

| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |
|---|---|---|---|---:|
| 2023 | `pi_gbm` | 0,0013 ± 0,0006 (`pi_ratings`) | `pi_gbm` | 0,0000 |
| 2024 | `pi_gbm` | 0,0013 ± 0,0011 (`gas_poisson`) | `gas_poisson` | 0,0024 |
| 2025 | `gas_poisson` | 0,0008 ± 0,0006 (`nb2`) | `pi_gbm` | 0,0020 |
| 2026 (następny) | `pi_gbm` | 0,0020 ± 0,0012 (`gas_poisson`) | — | — |

| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `pi_gbm` (najlepszy po fakcie) [95% CI] | p (DM) |
|---|---|---|---|---|---:|
| `baseline_league_freq` | 0,6028 [0,5982; 0,6074] | — | — | — | — |
| `pi_gbm` | 0,5964 [0,5919; 0,6007] | +0,0064 [+0,0053; +0,0076] | 4 z 47 | — | — |
| `selected_best` | 0,5970 [0,5926; 0,6016] | +0,0058 [+0,0044; +0,0071] | 2 z 47 | -0,0007 [-0,0014; +0,0001] | 0,0967 |
| `pool_models` | 0,5943 [0,5898; 0,5986] | +0,0085 [+0,0073; +0,0098] | 6 z 47 | +0,0021 [+0,0015; +0,0028] | < 0,0001 |
| `selected_best_league` | 0,5970 [0,5925; 0,6014] | +0,0058 [+0,0044; +0,0072] | 2 z 47 | -0,0006 [-0,0015; +0,0003] | 0,1864 |
| `pool_models_linear` | 0,5943 [0,5898; 0,5986] | +0,0085 [+0,0073; +0,0098] | 6 z 47 | +0,0021 [+0,0014; +0,0027] | < 0,0001 |

Wagi puli `pool_models` (pominięte modele z wagą 0):

| Model | 2023 | 2024 | 2025 | 2026 (następny) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | 0,02 | 0,00 | 0,00 | 0,03 |
| `dixon_coles` | 0,11 | 0,12 | 0,08 | 0,23 |
| `nb2` | 0,16 | 0,04 | 0,13 | 0,00 |
| `gas_poisson` | 0,06 | 0,27 | 0,42 | 0,18 |
| `pi_gbm` | 0,51 | 0,43 | 0,34 | 0,52 |
| `pi_ratings` | 0,13 | 0,14 | 0,02 | 0,05 |

### Obie drużyny strzelą (BTTS)

| Sezon oceniany | Wybór z poprzedniego sezonu | Przewaga nad drugim modelem w wyborze (± błąd std.) | Najlepszy po fakcie w tym sezonie | Strata wyboru vs najlepszy po fakcie |
|---|---|---|---|---:|
| 2023 | `pi_gbm` | 0,0004 ± 0,0005 (`pi_ratings`) | `pi_gbm` | 0,0000 |
| 2024 | `pi_gbm` | 0,0012 ± 0,0005 (`pi_ratings`) | `pi_gbm` | 0,0000 |
| 2025 | `pi_gbm` | 0,0006 ± 0,0009 (`gas_poisson`) | `pi_gbm` | 0,0000 |
| 2026 (następny) | `pi_gbm` | 0,0023 ± 0,0005 (`pi_ratings`) | — | — |

| Model | Log-loss [95% CI] | Zysk vs baseline [95% CI] | Ligi lepsze niż baseline (Holm) | Zysk vs `pi_gbm` (najlepszy po fakcie) [95% CI] | p (DM) |
|---|---|---|---|---|---:|
| `baseline_league_freq` | 0,6896 [0,6887; 0,6904] | — | — | — | — |
| `pi_gbm` | 0,6871 [0,6861; 0,6881] | +0,0025 [+0,0017; +0,0032] | 0 z 47 | — | — |
| `selected_best` | 0,6871 [0,6861; 0,6881] | +0,0025 [+0,0017; +0,0032] | 0 z 47 | identyczne prognozy | — |
| `pool_models` | 0,6864 [0,6854; 0,6874] | +0,0031 [+0,0024; +0,0038] | 2 z 47 | +0,0007 [+0,0002; +0,0011] | 0,0020 |
| `selected_best_league` | 0,6873 [0,6862; 0,6884] | +0,0023 [+0,0015; +0,0031] | 0 z 47 | -0,0002 [-0,0006; +0,0002] | 0,3806 |
| `pool_models_linear` | 0,6864 [0,6854; 0,6874] | +0,0031 [+0,0024; +0,0038] | 2 z 47 | +0,0007 [+0,0002; +0,0011] | 0,0021 |

Wagi puli `pool_models` (pominięte modele z wagą 0):

| Model | 2023 | 2024 | 2025 | 2026 (następny) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | 0,14 | 0,00 | 0,07 | 0,00 |
| `dixon_coles` | 0,00 | 0,32 | 0,00 | 0,13 |
| `nb2` | 0,18 | 0,03 | 0,00 | 0,00 |
| `gas_poisson` | 0,08 | 0,03 | 0,44 | 0,17 |
| `pi_gbm` | 0,40 | 0,58 | 0,41 | 0,70 |
| `pi_ratings` | 0,19 | 0,04 | 0,08 | 0,00 |

**Wybór osobno dla każdej ligi** (`selected_best_league`, ze ściągnięciem do wyboru zbiorczego metodą empirycznego Bayesa) nie pomaga: różnica log-lossu względem wyboru zbiorczego to 1X2 -0,0001, Gole 1,5 -0,0002, Gole 2,5 -0,0006, Gole 3,5 +0,0001, BTTS -0,0002. Różnice między modelami w jednej lidze są za małe względem szumu, żeby dało się je wybierać ligą po lidze. **Pula liniowa** (`pool_models_linear`) daje praktycznie to samo co logarytmiczna.

## Kalibracja (CORP), sezony 2023–2025

Rozkład CORP dzieli wynik Briera na MCB (miskalibracja), DSC (zdolność odróżniania meczów) i UNC (nieusuwalna niepewność): Brier = MCB − DSC + UNC. Wartości ×1000; dla 1X2 suma po trzech klasach, dla rynków dwuklasowych zdarzenie „powyżej” albo „tak”. Dobry model ma DSC dużo większe od MCB. „Średnia prognoza vs częstość” to różnica w punktach procentowych (dla 1X2 klasa z największą różnicą). p(MCB) to najmniejsza p-wartość testu Monte Carlo (200 symulacji) hipotezy, że prognozy są wiarygodne.

### 1X2 (wynik meczu)

| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | -0,3 | 0,42 | 0,66 | 0,204 |
| `dixon_coles` | +0,5 | 1,35 | 44,07 | 0,005 |
| `nb2` | -1,0 | 1,53 | 44,17 | 0,005 |
| `gas_poisson` | -1,2 | 1,43 | 44,18 | 0,005 |
| `elo_davidson` | -0,3 | 1,51 | 42,23 | 0,005 |
| `pi_ratings` | -0,2 | 1,31 | 42,55 | 0,005 |
| `pi_gbm` (najlepszy pojedynczy) | -0,4 | 1,35 | 46,65 | 0,010 |
| `selected_best` | -0,4 | 1,35 | 46,65 | 0,010 |
| `pool_models` | +0,3 | 1,15 | 47,49 | 0,020 |

### Gole powyżej 1,5

| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | -1,0 (74,3% wobec 75,3%) | 0,27 | 1,38 | 0,005 |
| `dixon_coles` | -0,9 (74,4% wobec 75,3%) | 0,81 | 2,64 | 0,005 |
| `nb2` | -1,4 (73,9% wobec 75,3%) | 0,88 | 2,68 | 0,005 |
| `gas_poisson` | -1,0 (74,3% wobec 75,3%) | 0,79 | 2,91 | 0,005 |
| `pi_ratings` | +0,2 (75,4% wobec 75,3%) | 0,22 | 2,32 | 0,408 |
| `pi_gbm` (najlepszy pojedynczy) | +0,1 (75,4% wobec 75,3%) | 0,23 | 2,91 | 0,378 |
| `selected_best` | +0,1 (75,4% wobec 75,3%) | 0,25 | 2,85 | 0,169 |
| `pool_models` | -0,3 (75,0% wobec 75,3%) | 0,24 | 3,40 | 0,378 |

### Gole powyżej 2,5

| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | -1,6 (50,1% wobec 51,7%) | 0,57 | 2,09 | 0,005 |
| `dixon_coles` | -1,9 (49,8% wobec 51,7%) | 1,37 | 5,09 | 0,005 |
| `nb2` | -1,9 (49,7% wobec 51,7%) | 1,35 | 5,07 | 0,005 |
| `gas_poisson` | -1,4 (50,2% wobec 51,7%) | 1,26 | 5,46 | 0,005 |
| `pi_ratings` | -0,2 (51,4% wobec 51,7%) | 0,37 | 4,12 | 0,109 |
| `pi_gbm` (najlepszy pojedynczy) | -0,3 (51,4% wobec 51,7%) | 0,35 | 5,07 | 0,378 |
| `selected_best` | -0,6 (51,0% wobec 51,7%) | 0,64 | 5,08 | 0,005 |
| `pool_models` | -0,8 (50,8% wobec 51,7%) | 0,41 | 6,08 | 0,065 |

### Gole powyżej 3,5

| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | -1,3 (28,2% wobec 29,5%) | 0,46 | 1,97 | 0,005 |
| `dixon_coles` | -0,9 (28,6% wobec 29,5%) | 0,92 | 4,83 | 0,005 |
| `nb2` | -0,9 (28,6% wobec 29,5%) | 0,91 | 4,82 | 0,005 |
| `gas_poisson` | -0,6 (28,9% wobec 29,5%) | 0,91 | 5,07 | 0,005 |
| `pi_ratings` | -0,1 (29,3% wobec 29,5%) | 0,34 | 3,60 | 0,045 |
| `pi_gbm` (najlepszy pojedynczy) | -0,2 (29,2% wobec 29,5%) | 0,31 | 4,49 | 0,393 |
| `selected_best` | -0,3 (29,1% wobec 29,5%) | 0,53 | 4,50 | 0,005 |
| `pool_models` | -0,6 (28,9% wobec 29,5%) | 0,40 | 5,49 | 0,010 |

### Obie drużyny strzelą (BTTS)

| Model | Średnia prognoza vs częstość (pkt proc.) | MCB ×1000 | DSC ×1000 | p(MCB) |
|---|---:|---:|---:|---:|
| `baseline_league_freq` | -1,2 (51,7% wobec 52,9%) | 0,37 | 1,30 | 0,005 |
| `dixon_coles` | -1,2 (51,7% wobec 52,9%) | 1,13 | 2,28 | 0,005 |
| `nb2` | -1,6 (51,3% wobec 52,9%) | 1,24 | 2,28 | 0,005 |
| `gas_poisson` | -1,5 (51,5% wobec 52,9%) | 1,06 | 2,41 | 0,005 |
| `pi_ratings` | -0,3 (52,6% wobec 52,9%) | 0,27 | 1,65 | 0,358 |
| `pi_gbm` (najlepszy pojedynczy) | -0,2 (52,7% wobec 52,9%) | 0,25 | 2,39 | 0,806 |
| `selected_best` | -0,2 (52,7% wobec 52,9%) | 0,25 | 2,39 | 0,806 |
| `pool_models` | -0,7 (52,2% wobec 52,9%) | 0,34 | 2,83 | 0,060 |

**Rynki goli i BTTS.** Modele oparte tylko na bramkach (Dixon–Coles, NB2, GAS) zaniżają prawdopodobieństwo goli („powyżej” albo „tak”): średnia różnica względem częstości (w punktach procentowych) to Gole 1,5 -1,1, Gole 2,5 -1,7, Gole 3,5 -0,8, BTTS -1,4. Baseline też zaniża (Gole 1,5 -1,0, Gole 2,5 -1,6, Gole 3,5 -1,3, BTTS -1,2), prawdopodobnie dlatego, że częstości z przeszłości są niższe niż w kolejnych sezonach (poziom goli rośnie). MCB modeli z samych bramek jest 2,9–4,6 razy większe niż u najlepszego pojedynczego modelu, który uczy się poziomu ligi i formy wprost.

**1X2.** Miskalibracja jest mała: MCB 1,15–1,53 przy DSC 42,2–47,5 (×1000). Test Monte Carlo wykrywa ją u 10 z 10 modeli i wariantów (najmniejsza z trzech p-wartości klas poniżej 0,05, bez korekty na trzy klasy); przy tylu meczach wykrywa się także drobne odchylenia.

## Stabilność po sezonach

Zysk względem baseline'u (log-loss baseline'u minus modelu) na wspólnej próbie, po sezonach. `selected_best` i `pool_models` nie mają prognoz na 2022.

| Rynek | Model | 2022 | 2023 | 2024 | 2025 |
|---|---|---:|---:|---:|---:|
| 1X2 | `dixon_coles` | +0,0608 | +0,0643 | +0,0575 | +0,0571 |
| 1X2 | `nb2` | +0,0605 | +0,0641 | +0,0571 | +0,0569 |
| 1X2 | `gas_poisson` | +0,0609 | +0,0653 | +0,0577 | +0,0559 |
| 1X2 | `elo_davidson` | +0,0590 | +0,0616 | +0,0554 | +0,0526 |
| 1X2 | `pi_ratings` | +0,0591 | +0,0633 | +0,0560 | +0,0533 |
| 1X2 | `pi_gbm` | +0,0659 | +0,0686 | +0,0615 | +0,0603 |
| 1X2 | `selected_best` | — | +0,0686 | +0,0615 | +0,0603 |
| 1X2 | `pool_models` | — | +0,0701 | +0,0630 | +0,0621 |
| Gole 1,5 | `dixon_coles` | +0,0006 | +0,0015 | +0,0026 | +0,0027 |
| Gole 1,5 | `nb2` | +0,0006 | +0,0017 | +0,0028 | +0,0024 |
| Gole 1,5 | `gas_poisson` | +0,0008 | +0,0033 | +0,0036 | +0,0024 |
| Gole 1,5 | `pi_ratings` | +0,0024 | +0,0036 | +0,0028 | +0,0018 |
| Gole 1,5 | `pi_gbm` | +0,0023 | +0,0043 | +0,0043 | +0,0044 |
| Gole 1,5 | `selected_best` | — | +0,0036 | +0,0043 | +0,0044 |
| Gole 1,5 | `pool_models` | — | +0,0057 | +0,0059 | +0,0053 |
| Gole 2,5 | `dixon_coles` | +0,0008 | +0,0031 | +0,0048 | +0,0052 |
| Gole 2,5 | `nb2` | +0,0009 | +0,0031 | +0,0048 | +0,0051 |
| Gole 2,5 | `gas_poisson` | +0,0012 | +0,0053 | +0,0058 | +0,0052 |
| Gole 2,5 | `pi_ratings` | +0,0042 | +0,0053 | +0,0033 | +0,0050 |
| Gole 2,5 | `pi_gbm` | +0,0059 | +0,0069 | +0,0057 | +0,0069 |
| Gole 2,5 | `selected_best` | — | +0,0069 | +0,0057 | +0,0052 |
| Gole 2,5 | `pool_models` | — | +0,0085 | +0,0082 | +0,0087 |
| Gole 3,5 | `dixon_coles` | +0,0008 | +0,0042 | +0,0078 | +0,0039 |
| Gole 3,5 | `nb2` | +0,0008 | +0,0042 | +0,0078 | +0,0039 |
| Gole 3,5 | `gas_poisson` | +0,0004 | +0,0054 | +0,0086 | +0,0043 |
| Gole 3,5 | `pi_ratings` | +0,0030 | +0,0051 | +0,0039 | +0,0035 |
| Gole 3,5 | `pi_gbm` | +0,0043 | +0,0067 | +0,0062 | +0,0063 |
| Gole 3,5 | `selected_best` | — | +0,0067 | +0,0062 | +0,0043 |
| Gole 3,5 | `pool_models` | — | +0,0085 | +0,0097 | +0,0074 |
| BTTS | `dixon_coles` | -0,0019 | +0,0007 | -0,0000 | +0,0004 |
| BTTS | `nb2` | -0,0020 | +0,0005 | -0,0002 | +0,0002 |
| BTTS | `gas_poisson` | -0,0018 | +0,0006 | +0,0010 | +0,0008 |
| BTTS | `pi_ratings` | +0,0006 | +0,0012 | +0,0004 | +0,0011 |
| BTTS | `pi_gbm` | +0,0010 | +0,0024 | +0,0016 | +0,0034 |
| BTTS | `selected_best` | — | +0,0024 | +0,0016 | +0,0034 |
| BTTS | `pool_models` | — | +0,0032 | +0,0027 | +0,0035 |

Najlepszy pojedynczy model każdego rynku ma dodatni zysk w 20 z 20 par rynek–sezon.

Modele oparte tylko na bramkach mają ujemny zysk w 5 z 60 par rynek–model–sezon: BTTS 2022 (`dixon_coles`), BTTS 2024 (`dixon_coles`), BTTS 2022 (`nb2`), BTTS 2024 (`nb2`), BTTS 2022 (`gas_poisson`).

W 1X2 zysk każdego modelu jest w 2025 niższy niż w 2022 (`pi_gbm`: +0,0659 w 2022, +0,0603 w 2025). Przyczyny nie badaliśmy (może to dryf poziomu ligi albo zmiana samych sezonów).

## Wynik w ligach, sezony 2023–2025

Zysk względem baseline'u w każdej lidze (dodatni = model lepszy). Gwiazdka oznacza istotną przewagę modelu po korekcie Holma (5%, korekta między ligami dla danego modelu i rynku), krzyżyk istotną przewagę baseline'u. Próba to sezony 2023–2025 (te same mecze dla wszystkich modeli).

### 1X2

| Liga | Mecze | `dixon_coles` | `nb2` | `gas_poisson` | `elo_davidson` | `pi_ratings` | `pi_gbm` | `pool_models` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1DIVISION | 748 | +0,117* | +0,118* | +0,124* | +0,133* | +0,129* | +0,125* | +0,127* |
| 1LIG | 1 076 | +0,079* | +0,080* | +0,076* | +0,049* | +0,049* | +0,079* | +0,081* |
| 1SNL | 180 | +0,079* | +0,079* | +0,081* | +0,067 | +0,073* | +0,081* | +0,083* |
| 2LIGA | 702 | +0,035* | +0,035* | +0,042* | +0,030 | +0,025 | +0,028 | +0,035* |
| 3LIGA | 1 140 | +0,017* | +0,018* | +0,016* | +0,010 | +0,012 | +0,015 | +0,018* |
| ALLSVENSKAN | 726 | +0,049* | +0,051* | +0,054* | +0,044* | +0,048* | +0,055* | +0,056* |
| AUSTRIA | 585 | +0,049* | +0,048* | +0,050* | +0,044 | +0,047 | +0,055* | +0,056* |
| BELGIUM | 948 | +0,068* | +0,067* | +0,067* | +0,066* | +0,065* | +0,068* | +0,071* |
| BUNDESLIGA | 922 | +0,090* | +0,087* | +0,092* | +0,091* | +0,094* | +0,100* | +0,100* |
| BUNDESLIGA2 | 923 | +0,019 | +0,019 | +0,019* | +0,015 | +0,019 | +0,032* | +0,030* |
| CHALLENGERPROL | 644 | +0,060* | +0,061* | +0,050* | +0,056* | +0,055* | +0,060* | +0,065* |
| CHAMPIONSHIP | 1 669 | +0,029* | +0,029* | +0,025* | +0,024* | +0,023* | +0,028* | +0,031* |
| CZECHLIGA | 837 | +0,110* | +0,110* | +0,112* | +0,112* | +0,117* | +0,116* | +0,117* |
| DENMARK | 578 | +0,054* | +0,054* | +0,057* | +0,056* | +0,055* | +0,059* | +0,061* |
| EERSTEDIVISIE | 1 150 | +0,038* | +0,038* | +0,037* | +0,035* | +0,031 | +0,042* | +0,044* |
| EKSTRAKLASA | 918 | +0,018 | +0,017 | +0,020* | +0,012 | +0,017 | +0,013 | +0,017 |
| ELITESERIEN | 724 | +0,061* | +0,062* | +0,060* | +0,057* | +0,055* | +0,063* | +0,065* |
| EPL | 1 140 | +0,090* | +0,089* | +0,091* | +0,091* | +0,087* | +0,085* | +0,091* |
| EREDIVISIE | 945 | +0,114* | +0,111* | +0,113* | +0,112* | +0,114* | +0,116* | +0,119* |
| FIRSTLEAGUE | 869 | +0,088* | +0,088* | +0,091* | +0,088* | +0,091* | +0,097* | +0,098* |
| FIRSTLEAGUE2 | 917 | +0,029* | +0,028* | +0,028* | +0,026* | +0,027* | +0,032* | +0,032* |
| GREECE | 712 | +0,129* | +0,128* | +0,125* | +0,119* | +0,128* | +0,134* | +0,134* |
| HNL | 540 | +0,090* | +0,090* | +0,091* | +0,085* | +0,086* | +0,092* | +0,095* |
| LALIGA | 1 140 | +0,092* | +0,092* | +0,095* | +0,094* | +0,096* | +0,096* | +0,098* |
| LALIGA2 | 1 403 | +0,016* | +0,016* | +0,019* | +0,013 | +0,017 | +0,021* | +0,022* |
| LEAGUEONE | 1 670 | +0,038* | +0,039* | +0,034* | +0,036* | +0,032* | +0,040* | +0,042* |
| LEAGUETWO | 1 669 | +0,014 | +0,014 | +0,015* | +0,009 | +0,009 | +0,020* | +0,021* |
| LIGAI | 956 | +0,035* | +0,035* | +0,032* | +0,036 | +0,036* | +0,038* | +0,039* |
| LIGATHAAL | 719 | +0,075* | +0,075* | +0,074* | +0,072* | +0,073* | +0,077* | +0,079* |
| LIGUE1 | 921 | +0,068* | +0,069* | +0,070* | +0,068* | +0,068* | +0,070* | +0,073* |
| LIGUE2 | 999 | +0,011 | +0,013 | +0,011 | +0,008 | +0,007 | +0,022* | +0,021* |
| NBI | 594 | +0,045* | +0,045* | +0,042* | +0,044* | +0,045* | +0,052* | +0,051* |
| PREMIERDIVISIO | 440 | +0,062* | +0,064* | +0,061* | +0,049* | +0,054* | +0,059* | +0,062* |
| PREMIERLEAGUE | 696 | +0,075* | +0,075* | +0,071* | +0,066* | +0,065* | +0,070* | +0,074* |
| PREMIERLEAGUE2 | 732 | +0,085* | +0,083* | +0,087* | +0,089* | +0,089* | +0,091* | +0,092* |
| PREMIERLEAGUE3 | 730 | +0,089* | +0,089* | +0,088* | +0,081* | +0,077* | +0,098* | +0,097* |
| PRIMEIRA | 924 | +0,140* | +0,143* | +0,150* | +0,144* | +0,150* | +0,155* | +0,154* |
| SCOTLAND | 701 | +0,095* | +0,095* | +0,095* | +0,088* | +0,099* | +0,107* | +0,105* |
| SEGUNDALIGA | 923 | +0,015 | +0,015 | +0,015 | +0,012 | +0,012 | +0,016 | +0,018 |
| SERIEA | 1 140 | +0,104* | +0,102* | +0,103* | +0,101* | +0,104* | +0,107* | +0,109* |
| SERIEB | 1 168 | +0,024* | +0,018 | +0,017* | +0,020 | +0,023* | +0,029* | +0,028* |
| SUPERETTAN | 730 | +0,020 | +0,020 | +0,019* | +0,014 | +0,020 | +0,025 | +0,026* |
| SUPERLIG | 1 028 | +0,085* | +0,083* | +0,086* | +0,095* | +0,087* | +0,102* | +0,099* |
| SUPERLIGA | 896 | +0,099* | +0,098* | +0,100* | +0,091* | +0,098* | +0,101* | +0,104* |
| SUPERLIGA2 | 582 | +0,070* | +0,070* | +0,075* | +0,076* | +0,076* | +0,078* | +0,079* |
| SWITZERLAND | 689 | +0,028* | +0,028* | +0,026* | +0,019 | +0,021 | +0,028* | +0,030* |
| VEIKKAUSLIIGA | 513 | +0,062* | +0,063* | +0,064* | +0,065* | +0,070* | +0,065* | +0,068* |

### Gole i BTTS

Zysk puli `pool_models` względem baseline'u; w nawiasie `pi_gbm` (najlepszy pojedynczy model).

| Liga | Mecze | Gole 1,5 | Gole 2,5 | Gole 3,5 | BTTS |
|---|---:|---:|---:|---:|---:|
| 1DIVISION | 748 | +0,006 (+0,002) | +0,010 (+0,006) | +0,012 (+0,005) | +0,005 (+0,004) |
| 1LIG | 1 076 | +0,008 (+0,005) | +0,019* (+0,012*) | +0,026* (+0,014*) | +0,003 (+0,003) |
| 1SNL | 180 | -0,002 (+0,001) | +0,011 (+0,007) | +0,009 (-0,000) | -0,003 (-0,002) |
| 2LIGA | 702 | +0,005 (+0,001) | +0,008 (+0,003) | +0,011 (+0,003) | +0,008 (+0,002) |
| 3LIGA | 1 140 | +0,003 (+0,003) | +0,005 (+0,003) | +0,008 (+0,005) | +0,004 (+0,003) |
| ALLSVENSKAN | 726 | +0,007 (+0,002) | +0,009 (+0,004) | +0,008 (+0,004) | +0,003 (+0,002) |
| AUSTRIA | 585 | -0,000 (-0,001) | -0,002 (-0,003) | +0,003 (+0,004) | +0,000 (+0,000) |
| BELGIUM | 948 | +0,009 (+0,011*) | +0,013* (+0,012) | +0,012* (+0,009) | +0,002 (+0,000) |
| BUNDESLIGA | 922 | +0,005 (+0,002) | +0,015* (+0,012) | +0,018 (+0,007) | +0,005 (+0,001) |
| BUNDESLIGA2 | 923 | +0,001 (+0,003) | +0,004 (+0,005) | +0,005 (+0,007) | +0,002 (+0,003) |
| CHALLENGERPROL | 644 | -0,003 (-0,004) | -0,005 (-0,005) | -0,004 (-0,004) | +0,000 (+0,001) |
| CHAMPIONSHIP | 1 669 | +0,003 (+0,002) | +0,003 (+0,001) | +0,003 (+0,002) | +0,005 (+0,003) |
| CZECHLIGA | 837 | +0,007 (+0,003) | +0,007 (+0,003) | +0,005 (+0,002) | +0,002 (-0,002) |
| DENMARK | 578 | +0,004 (+0,003) | +0,002 (+0,001) | +0,006 (+0,008) | +0,001 (+0,001) |
| EERSTEDIVISIE | 1 150 | +0,005 (+0,005) | +0,004 (+0,003) | +0,003 (-0,002) | +0,004 (+0,005) |
| EKSTRAKLASA | 918 | +0,007 (+0,004) | +0,012* (+0,012) | +0,007 (+0,007) | +0,004 (+0,004) |
| ELITESERIEN | 724 | +0,005 (-0,001) | +0,008 (+0,006) | +0,012 (+0,012) | +0,005 (-0,001) |
| EPL | 1 140 | +0,008 (+0,008) | +0,007 (+0,004) | +0,006 (+0,005) | +0,008 (+0,009) |
| EREDIVISIE | 945 | +0,006 (+0,004) | +0,008 (+0,003) | +0,011 (+0,006) | +0,003 (+0,002) |
| FIRSTLEAGUE | 869 | +0,005 (+0,003) | +0,007 (+0,006) | +0,004 (+0,003) | -0,001 (-0,003) |
| FIRSTLEAGUE2 | 917 | -0,000 (-0,002) | -0,001 (-0,003) | +0,000 (-0,000) | -0,001 (-0,000) |
| GREECE | 712 | +0,019 (+0,016) | +0,023 (+0,019) | +0,007 (+0,006) | +0,013 (+0,015) |
| HNL | 540 | +0,008 (+0,006) | +0,017 (+0,017) | +0,005 (+0,005) | -0,000 (+0,000) |
| LALIGA | 1 140 | +0,013* (+0,007) | +0,019* (+0,012*) | +0,017* (+0,009) | +0,006 (+0,004) |
| LALIGA2 | 1 403 | +0,010* (+0,011*) | +0,014* (+0,015*) | +0,019* (+0,016*) | +0,008 (+0,007) |
| LEAGUEONE | 1 670 | +0,007* (+0,004) | +0,006 (+0,004) | +0,007* (+0,006*) | +0,005* (+0,003) |
| LEAGUETWO | 1 669 | +0,002 (+0,003) | +0,004 (+0,003) | +0,004 (+0,002) | +0,001 (+0,002) |
| LIGAI | 956 | +0,002 (+0,002) | +0,003 (+0,002) | +0,005 (+0,007) | +0,001 (+0,001) |
| LIGATHAAL | 719 | +0,007 (+0,007) | +0,011 (+0,007) | +0,016 (+0,014) | +0,001 (+0,001) |
| LIGUE1 | 921 | +0,004 (+0,003) | +0,010 (+0,005) | +0,013* (+0,009) | +0,001 (+0,001) |
| LIGUE2 | 999 | -0,002 (-0,001) | +0,004 (+0,003) | +0,004 (+0,003) | +0,001 (+0,000) |
| NBI | 594 | -0,000 (-0,004) | -0,004 (-0,007) | -0,004 (-0,003) | +0,002 (+0,001) |
| PREMIERDIVISIO | 440 | +0,003 (+0,002) | +0,008 (+0,007) | +0,007 (+0,003) | -0,008 (-0,006) |
| PREMIERLEAGUE | 696 | +0,006 (+0,004) | +0,003 (+0,004) | +0,012 (+0,011) | -0,002 (-0,001) |
| PREMIERLEAGUE2 | 732 | +0,018 (+0,013) | +0,014 (+0,007) | +0,011 (+0,006) | +0,014* (+0,012) |
| PREMIERLEAGUE3 | 730 | +0,013 (+0,007) | +0,018 (+0,012) | +0,016 (+0,010) | +0,003 (-0,001) |
| PRIMEIRA | 924 | +0,016* (+0,015*) | +0,027* (+0,028*) | +0,011 (+0,011) | +0,003 (+0,003) |
| SCOTLAND | 701 | +0,010 (+0,011) | +0,016 (+0,013) | +0,010 (+0,011) | +0,006 (+0,004) |
| SEGUNDALIGA | 923 | -0,000 (+0,002) | -0,002 (-0,000) | +0,001 (+0,002) | -0,001 (+0,001) |
| SERIEA | 1 140 | +0,008 (+0,005) | +0,011 (+0,011) | +0,012 (+0,011) | +0,005 (+0,003) |
| SERIEB | 1 168 | +0,002 (+0,002) | +0,002 (+0,003) | +0,001 (+0,001) | -0,004 (-0,002) |
| SUPERETTAN | 730 | -0,000 (-0,000) | -0,003 (-0,002) | -0,002 (-0,001) | +0,000 (+0,000) |
| SUPERLIG | 1 028 | +0,004 (+0,002) | +0,011 (+0,009) | +0,015 (+0,013) | -0,001 (-0,001) |
| SUPERLIGA | 896 | +0,009 (+0,009) | +0,017* (+0,014) | +0,015 (+0,016) | +0,005 (+0,007) |
| SUPERLIGA2 | 582 | +0,006 (+0,004) | +0,005 (+0,005) | +0,010 (+0,011*) | +0,003 (+0,003) |
| SWITZERLAND | 689 | -0,001 (-0,005) | +0,003 (+0,001) | -0,001 (-0,002) | -0,001 (-0,003) |
| VEIKKAUSLIIGA | 513 | +0,013 (+0,013) | +0,025* (+0,026) | +0,022 (+0,024) | +0,012 (+0,011) |

## Test przecieku wyboru

Test analogiczny do testu przecieku ramy: wyniki sezonu T i wszystkich późniejszych zostają zmienione (numer klasy przesunięty o jeden), a potem sprawdzamy, czy jakakolwiek prognoza `selected_best`, `pool_models`, puli liniowej i wyboru per liga z sezonów do T włącznie się zmieniła. Nie może się zmienić, bo wybór i wagi dla sezonu S widzą tylko wyniki z S − 1. Prognozy późniejszych sezonów muszą się zmienić, co pokazuje, że test coś wykrywa.

| Rynek | Zmieniony od sezonu T | Prognoz porównanych (4 rodzaje, sezony do T) | Zmienionych (musi być 0) | Zmienionych w sezonach po T (oczekiwane; po 2025 nic nie ma) |
|---|---|---:|---:|---:|
| 1X2 | 2023 | 55 684 | 0 | 110 740 |
| 1X2 | 2024 | 111 084 | 0 | 55 340 |
| 1X2 | 2025 | 166 424 | 0 | 0 |
| Gole 2,5 | 2023 | 55 684 | 0 | 109 936 |
| Gole 2,5 | 2024 | 111 084 | 0 | 55 111 |
| Gole 2,5 | 2025 | 166 424 | 0 | 0 |
| Gole 1,5 | 2023 | 55 484 | 0 | 109 165 |
| Gole 1,5 | 2024 | 110 884 | 0 | 54 738 |
| Gole 1,5 | 2025 | 166 224 | 0 | 0 |
| Gole 3,5 | 2023 | 55 684 | 0 | 107 860 |
| Gole 3,5 | 2024 | 111 084 | 0 | 55 340 |
| Gole 3,5 | 2025 | 166 424 | 0 | 0 |
| BTTS | 2023 | 55 684 | 0 | 109 297 |
| BTTS | 2024 | 111 084 | 0 | 54 743 |
| BTTS | 2025 | 166 424 | 0 | 0 |

**Próba mutacji.** Ten sam test z celowo zepsutą regułą (wybór i wagi z sezonu S zamiast S − 1) zmienia prognozy w każdym rynku i przy każdym T: od 52 853 do 55 684 zmienionych prognoz na prawdziwych danych. Skrypt przerywa pracę, gdyby test tego nie wykrył. Osobno `app/tests/test_model_selection.py` sprawdza to samo na danych syntetycznych.

Same modele mają własne testy przecieku opisane przy ich budowie (na wybranych ligach); tu sprawdzamy tylko warstwę wyboru i puli.

## Ograniczenia

- **To filtr, nie dowód.** Pytanie 1 mówi, że model przewiduje lepiej niż częstość ligowa. Rynek kursów wie więcej niż baseline, więc to, czy model wie więcej niż rynek, pokaże dopiero pytanie 2 (kursy zamknięcia Pinnacle na połączonych danych).
- **Ranking „najlepszy po fakcie” jest opisowy.** Liczymy go na sezonach ocenianych, więc przy porównaniu z nim wygrywający model ma przewagę. Uczciwym testem ex ante są `selected_best` i `pool_models`, wybrane na poprzednim sezonie.
- **Sezony.** `selected_best` i `pool_models` oceniamy na 2023–2025, bo 2022 jest sezonem wyboru. W ligach z rokiem kalendarzowym (etykieta sezonu = rok) sezon S − 1 kończy się przed startem S, ale w ligach europejskich ostatnie kolejki S − 1 (marzec–maj) rozgrywają się już po starcie lig kalendarzowych sezonu S. Wybór zbiorczy może więc widzieć kilka wyników innych lig, które zapadły po pierwszym meczu takiej ligi w S. Wyników sezonu S nie widzi nigdy. Sprawdziliśmy, ile to zmienia: wybór tylko na meczach S − 1 rozegranych przed pierwszym meczem S (zostaje 63%–66% próby wyboru) daje ten sam wybór modelu w 14 z 15 przypadków rynek–sezon (ze zmienionych wyborów 1 z 1 miało w próbie wyboru przewagę mniejszą niż błąd standardowy). Log-loss puli zmienia się najwyżej o 0,0003, log-loss wyboru o 0,0012. Tabela `calendar_year_check`.
- **Modele goli nie są niezależne od siebie.** Dixon–Coles, NB2 i GAS mają prawie identyczne prognozy (NB2 jest blisko Poissona), więc ich zgodność nie jest trzema niezależnymi potwierdzeniami. Modele bazujące na bramkach zaniżają liczbę bramek (patrz kalibracja); poprawka poziomu goli albo warstwa reszt względem rynku (pytanie 2) może to naprawić.
- **Małe zyski w rynkach goli i BTTS.** Zysk 0,3%–1,0% log-lossu baseline'u jest istotny statystycznie zbiorczo, ale po korekcie Holma wychodzi w 1–8 z 47 lig. Czy to wystarczy wobec kursów, pokaże pytanie 2.
- **Zależność meczów.** Bootstrap i test DM po kolejkach zakładają, że kolejki są mniej więcej niezależne. Te same drużyny grają w wielu kolejkach, więc przedziały mogą być odrobinę za wąskie.
- **Zakres.** 47 lig europejskich, sezony 2022–2025; wnioski nie obejmują innych lig ani rozgrywek pucharowych.

## Pliki i czas

- `predictions/model_comparison.sqlite`: wszystkie tabele (pokrycie, ranking, wyniki per liga, testy z korektami, CORP, wybory, wagi puli, test przecieku).
- `predictions/baseline_league_freq.sqlite`: baseline przeliczony pod `COMPARISON_CONFIG`. `predictions/selected_best.sqlite` i `predictions/pool_models.sqlite`: wybrany model i pula w formacie `save_result`; kolumna `params` mówi, co wybrano dla meczu; wiersze sezonu 2022 mają NaN (sezon wyboru).
- Kod: `app/src/model_buk/evaluation/model_selection.py`, `app/scripts/compare_goal_models.py`, testy `app/tests/test_model_selection.py`.
- Czas: porównanie i wybór dla wszystkich rynków 5,3 min (jeden proces).
