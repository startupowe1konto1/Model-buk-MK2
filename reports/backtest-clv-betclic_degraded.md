# Backtest CLV

Rezim `betclic_degraded` (tau=0.94), de-vig `power`, prog EV_net >= 0.05

> Cena wejscia to kolumny `PSH/PSD/PSA`, czyli zdjecie z budowy pliku
> tygodniowego (zwykle piatek), a nie kurs otwarcia rynku. Mierzony CLV
> to ruch piatek -> zamkniecie: konserwatywny podzbior prawdziwego CLV.


## Wynik zbiorczy

| miara | wartosc |
|---|---:|
| zakladow | 46999 |
| sredni CLV | +0.1354% |
| mediana CLV | -0.4886% |
| pobito zamkniecie | 47.52% |
| trafien | 23.02% |

## Po sezonach

| klucz | n | sredni CLV | pobito zamkniecie |
|---|---:|---:|---:|
| 2016/17 | 6040 | +0.8321% | 50.63% |
| 2017/18 | 5647 | +0.7371% | 49.16% |
| 2018/19 | 5210 | +0.6171% | 48.16% |
| 2019/20 | 4453 | +0.2766% | 48.06% |
| 2020/21 | 5494 | +0.1116% | 46.36% |
| 2021/22 | 4811 | -0.5422% | 45.46% |
| 2022/23 | 4722 | -0.6104% | 45.60% |
| 2023/24 | 4830 | -0.5710% | 46.00% |
| 2024/25 | 4223 | -0.0688% | 47.76% |
| 2025/26 | 1569 | +0.4161% | 46.27% |

## Po ligach

| klucz | n | sredni CLV | pobito zamkniecie |
|---|---:|---:|---:|
| B1 | 1539 | -0.2095% | 46.59% |
| D1 | 2354 | -0.1356% | 47.20% |
| D2 | 1571 | +0.4334% | 49.14% |
| E0 | 3249 | +0.4344% | 49.09% |
| E1 | 3543 | -0.3187% | 46.23% |
| E2 | 2964 | +0.1121% | 48.52% |
| E3 | 2537 | +0.5184% | 50.73% |
| EC | 2726 | -0.4116% | 46.81% |
| F1 | 2326 | +0.0709% | 48.62% |
| F2 | 2151 | -0.0916% | 47.61% |
| G1 | 1455 | +1.4409% | 46.87% |
| I1 | 2531 | +0.2004% | 47.06% |
| I2 | 2322 | +1.0023% | 49.61% |
| N1 | 2106 | -0.7622% | 45.87% |
| P1 | 1730 | -0.1649% | 45.20% |
| SC0 | 1486 | -0.4842% | 43.47% |
| SC1 | 1109 | -1.8079% | 42.02% |
| SC2 | 1042 | +0.5732% | 50.29% |
| SC3 | 842 | +0.2091% | 49.52% |
| SP1 | 2659 | -0.9112% | 43.14% |
| SP2 | 2979 | +1.8016% | 51.86% |
| T1 | 1778 | +0.8515% | 46.57% |

## Kalibracja: model kontra rynek (zamkniecie)

ECE (blad kalibracji) mowi, o ile srednio przewidywane prawdopodobienstwo roznilo sie od tego, jak czesto dany wynik naprawde sie zdarzal. Liczony tu na **71264** meczach - kazdym meczu sezonow testowych, nie tylko na tych 46999, ktore przeszly bramke EV powyzej. Liczenie samej bramki dawaloby wynik obciazony w strone przypadkow, w ktorych model i tak juz roznil sie od rynku.

| kto | n | log-loss | Brier | RPS | ECE |
|---|---:|---:|---:|---:|---:|
| model | 71264 | 1.02482 | 0.61483 | 0.21128 | 0.00667 |
| rynek (`power`, zamkniecie) | 71264 | 0.99727 | 0.59605 | 0.20270 | 0.00387 |
