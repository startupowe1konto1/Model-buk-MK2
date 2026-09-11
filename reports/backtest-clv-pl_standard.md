# Backtest CLV

Rezim `pl_standard` (tau=0.88), de-vig `power`, prog EV_net >= 0.05

> Cena wejscia to kolumny `PSH/PSD/PSA`, czyli zdjecie z budowy pliku
> tygodniowego (zwykle piatek), a nie kurs otwarcia rynku. Mierzony CLV
> to ruch piatek -> zamkniecie: konserwatywny podzbior prawdziwego CLV.


## Wynik zbiorczy

| miara | wartosc |
|---|---:|
| zakladow | 32152 |
| meczow (odrebnych) | 27900 |
| sredni CLV | +0.3288% |
| mediana CLV | -0.4841% |
| pobito zamkniecie | 47.72% |
| trafien | 21.03% |

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
| model | 32152 | +0.3288% | -0.4841% | 47.72% |
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
| 2016/17 | 4457 | +1.2003% | 52.03% |
| 2017/18 | 4065 | +1.0676% | 49.84% |
| 2018/19 | 3683 | +0.7481% | 48.19% |
| 2019/20 | 3136 | +0.4794% | 48.02% |
| 2020/21 | 3725 | +0.3537% | 46.26% |
| 2021/22 | 3189 | -0.4294% | 45.50% |
| 2022/23 | 3041 | -0.7700% | 44.89% |
| 2023/24 | 3129 | -0.4301% | 45.38% |
| 2024/25 | 2723 | -0.1811% | 46.93% |
| 2025/26 | 1004 | +0.8515% | 47.91% |

## Po ligach

| klucz | n | sredni CLV | pobito zamkniecie |
|---|---:|---:|---:|
| B1 | 949 | -0.5046% | 43.73% |
| D1 | 1666 | +0.0252% | 46.46% |
| D2 | 965 | +0.7434% | 50.05% |
| E0 | 2451 | +0.5782% | 49.57% |
| E1 | 2528 | -0.1845% | 46.64% |
| E2 | 2049 | +0.1625% | 47.97% |
| E3 | 1570 | +0.6967% | 50.89% |
| EC | 1776 | -0.1478% | 47.35% |
| F1 | 1566 | +0.1758% | 48.91% |
| F2 | 1486 | +0.1440% | 48.65% |
| G1 | 1105 | +1.8666% | 46.61% |
| I1 | 1710 | +0.5177% | 48.30% |
| I2 | 1599 | +1.4755% | 51.03% |
| N1 | 1448 | -0.8466% | 45.23% |
| P1 | 1122 | -0.3794% | 43.85% |
| SC0 | 1030 | -0.1213% | 43.88% |
| SC1 | 799 | -2.5074% | 40.18% |
| SC2 | 753 | +0.7144% | 49.67% |
| SC3 | 555 | +0.3431% | 50.09% |
| SP1 | 1872 | -0.5679% | 44.34% |
| SP2 | 2068 | +2.4197% | 53.24% |
| T1 | 1085 | +1.3734% | 46.64% |

## Kalibracja: model kontra rynek (zamkniecie)

ECE (blad kalibracji) mowi, o ile srednio przewidywane prawdopodobienstwo roznilo sie od tego, jak czesto dany wynik naprawde sie zdarzal. Liczony tu na **71264** meczach - kazdym meczu sezonow testowych, nie tylko na tych 32152, ktore przeszly bramke EV powyzej. Liczenie samej bramki dawaloby wynik obciazony w strone przypadkow, w ktorych model i tak juz roznil sie od rynku.

| kto | n | log-loss | Brier | RPS | ECE |
|---|---:|---:|---:|---:|---:|
| model | 71264 | 1.02482 | 0.61483 | 0.21128 | 0.00667 |
| rynek (`power`, zamkniecie) | 71264 | 0.99727 | 0.59605 | 0.20270 | 0.00387 |
