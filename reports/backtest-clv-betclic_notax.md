# Backtest CLV

Rezim `betclic_notax` (tau=1.0), de-vig `power`, prog EV_net >= 0.05

> Cena wejscia to kolumny `PSH/PSD/PSA`, czyli zdjecie z budowy pliku
> tygodniowego (zwykle piatek), a nie kurs otwarcia rynku. Mierzony CLV
> to ruch piatek -> zamkniecie: konserwatywny podzbior prawdziwego CLV.


## Wynik zbiorczy

| miara | wartosc |
|---|---:|
| zakladow | 66774 |
| meczow (odrebnych) | 54716 |
| sredni CLV | +0.0895% |
| mediana CLV | -0.3443% |
| pobito zamkniecie | 48.12% |
| trafien | 25.20% |

> Liczba zakladow to nie liczba odrebnych meczow (patrz wiersz
> "meczow (odrebnych)" wyzej). Jeden mecz moze dac dwie selekcje:
> model ocenia trzy mozliwe wyniki (gospodarz / remis / gosc), a jego
> roznice zdania z rynkiem na tych trzech wynikach w przyblizeniu
> sumuja sie do zera, wiec czesto dwie z trzech ocen wychodza jako
> "warte zakladu" jednoczesnie. Te dwie selekcje z tego samego meczu
> nie sa niezaleznymi obserwacjami - ich CLV z definicji porusza sie
> w przeciwne strony (kurs na jeden wynik tanieje dokladnie wtedy, gdy
> na inny drozeje). Liczac sama liczbe zakladow, zawyzamy wiec to,
> jak duza naprawde jest probka.


> Dodatnia srednia CLV przy ujemnej medianie (widoczne w tabeli
> wyzej) to wlasciwosc samego sposobu liczenia, nie dowod przewagi.
> CLV to iloraz cen (cena po ruchu rynku / cena, po ktorej wzieto
> zaklad, minus jeden): ma dolna granice na -100% (cena nie moze
> spasc bardziej), ale nie ma gornej granicy (cena moze urosnac
> dowolnie duzo). Garstka duzych, korzystnych ruchow ceny wystarczy,
> zeby podciagnac srednia w gore, mimo ze typowy pojedynczy zaklad
> traci troche na cenie. Dlatego mediana - a nie srednia - pokazuje,
> co spotyka typowy zaklad.


## Model kontra baseline (bez modelu)

Baseline to CLV policzone na WSZYSTKICH trzech nogach (H/D/A) kazdego
meczu sezonow testowych, bez modelu i bez bramki EV - punkt zero,
wzgledem ktorego czyta sie wynik modelu. Pelne rozbicie po wszystkich
trzech metodach de-vigu, sezonach i ligach jest w
`reports/baseline-null.md`; tutaj ten sam de-vig (`power`), co uzyty dla
modelu powyzej, zestawiony wprost z wynikiem modelu w jednej tabeli,
zeby nie trzeba bylo otwierac drugiego pliku.

To zestawienie rozstrzygaja `mediana CLV` i `pobito zamkniecie`, nie
`sredni CLV` (patrz notka o artefakcie ilorazowym wyzej). Na tych
danych model wypada gorzej niz baseline na obu tych miarach - gorzej
niz losowe branie wszystkich trzech nog kazdego meczu.

| kto | n (zakladow / nog) | sredni CLV | mediana CLV | pobito zamkniecie |
|---|---:|---:|---:|---:|
| model | 66774 | +0.0895% | -0.3443% | 48.12% |
| baseline (bez modelu) | 213792 | +0.1059% | +0.0084% | 50.08% |

## Po sezonach

> Patrzac na tabele ponizej latwo pomyslec: CLV z roku na rok
> systematycznie spada, wiec rynek z czasem nauczyl sie wyceniac
> mecze lepiej. To jest wniosek bledny. Ten sam spadek wychodzi
> identycznie, gdy z backtestu wyjmie sie caly model i cala
> bramke zakladow - wiec to nie jest cecha naszych typow, tylko
> czegos w samych danych wejsciowych. Kurs, po ktorym tu liczymy
> wejscie w zaklad (kolumny `PSH/PSD/PSA`), to zdjecie robione raz
> w tygodniu przy budowie pliku przez football-data.co.uk, a to,
> jak dokladnie to zdjecie jest robione, zmienilo sie na
> przestrzeni lat: srednia marza wliczona w te kolumny urosla z
> 2.46% w sezonie 2015/16 do 5.08% w sezonie 2024/25, podczas gdy
> marza na zamknieciu przez caly ten czas trzyma sie okolo 3.9%.
> Im pozniejszy sezon, tym gorszy (bardziej zamarzowany) jest wiec
> kurs startowy pomiaru - i dlatego ruch do zamkniecia wyglada na
> mniejszy, chociaz rynek wcale nie zrobil sie bardziej efektywny.
> Wniosek: liczb z tej tabeli nie wolno czytac jako trendu
> efektywnosci rynku miedzy sezonami. W obrebie jednego, pojedynczego
> sezonu ponizsze liczby sa jak najbardziej wiarygodne.

| klucz | n | sredni CLV | pobito zamkniecie |
|---|---:|---:|---:|
| 2016/17 | 8044 | +0.5750% | 50.05% |
| 2017/18 | 7633 | +0.6237% | 49.27% |
| 2018/19 | 7240 | +0.5063% | 48.55% |
| 2019/20 | 6160 | +0.1382% | 48.33% |
| 2020/21 | 7762 | +0.1168% | 47.55% |
| 2021/22 | 6934 | -0.3698% | 46.91% |
| 2022/23 | 7048 | -0.4352% | 47.13% |
| 2023/24 | 7213 | -0.4858% | 46.61% |
| 2024/25 | 6349 | -0.1102% | 48.75% |
| 2025/26 | 2391 | +0.4199% | 47.22% |

## Po ligach

| klucz | n | sredni CLV | pobito zamkniecie |
|---|---:|---:|---:|
| B1 | 2427 | -0.0554% | 48.13% |
| D1 | 3321 | -0.0690% | 48.09% |
| D2 | 2537 | +0.3032% | 50.02% |
| E0 | 4328 | +0.3729% | 49.33% |
| E1 | 4845 | -0.3387% | 46.44% |
| E2 | 4192 | +0.2210% | 49.28% |
| E3 | 3802 | +0.4641% | 50.95% |
| EC | 4126 | -0.4930% | 46.56% |
| F1 | 3299 | +0.1302% | 49.92% |
| F2 | 2972 | -0.0856% | 48.12% |
| G1 | 1911 | +1.2585% | 47.04% |
| I1 | 3604 | +0.1275% | 47.84% |
| I2 | 3178 | +0.7268% | 49.72% |
| N1 | 3039 | -0.6539% | 46.73% |
| P1 | 2494 | -0.3972% | 45.63% |
| SC0 | 2030 | -0.5846% | 43.50% |
| SC1 | 1490 | -1.5086% | 43.89% |
| SC2 | 1492 | +0.8593% | 51.61% |
| SC3 | 1305 | +0.2308% | 50.50% |
| SP1 | 3668 | -0.7766% | 44.82% |
| SP2 | 3985 | +1.2744% | 50.61% |
| T1 | 2729 | +0.8032% | 48.48% |

## Kalibracja: model kontra rynek (zamkniecie)

ECE (blad kalibracji) mowi, o ile srednio przewidywane prawdopodobienstwo roznilo sie od tego, jak czesto dany wynik naprawde sie zdarzal. Liczony tu na **71264** meczach - kazdym meczu sezonow testowych, nie tylko na tych 66774, ktore przeszly bramke EV powyzej. Liczenie samej bramki dawaloby wynik obciazony w strone przypadkow, w ktorych model i tak juz roznil sie od rynku.

| kto | n | log-loss | Brier | RPS | ECE |
|---|---:|---:|---:|---:|---:|
| model | 71264 | 1.02482 | 0.61483 | 0.21128 | 0.00667 |
| rynek (`power`, zamkniecie) | 71264 | 0.99727 | 0.59605 | 0.20270 | 0.00387 |
