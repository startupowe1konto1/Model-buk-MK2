---
tags: [m0, bookmaker-dispersion, empirical, phase-1, tax]
status: draft
date: 2026-09-25
---

# 32 — M0 i granica podatkowa

> Nie research, tylko pomiary. Wszystko sprawdzone 25.09.2026 na odtworzonym
> przebiegu z 23.09. Skrypt: `scripts/10_m0_bookmaker_dispersion.py`.

## 0. Jedno zdanie

Cała mierzalna rozbieżność między bukmacherami jest warta około **8 punktów
procentowych** yieldu. Polski podatek kosztuje około **11,5**. Przewaga, która
w tym rynku istnieje, jest **mniejsza od podatku** — i to nie jest wada modelu
ani reguły wyboru, tylko arytmetyka.

## 1. Przebieg z 23.09 odtworzony

Cały łańcuch (`nested-goals-archives` → `prepare-football-data-replay` →
`replay-history` → `archive-promotion-gate`) przepuszczony lokalnie na ZIP-ie
zbudowanym z 264 plików `football-data-uk/main/`.

| etap | odtworzone | raport 23.09 |
|---|---:|---:|
| prognozy nested OOS | 30 956 | 30 956 |
| dopasowane do rynku | 30 927 | 30 927 |
| wiersze możliwości | 688 325 | 688 325 |
| spotkania w replayu | 30 912 | 30 912 |
| zakłady (outer 2025/26) | 2 307 | 2 334 |
| wynik | −498,34 | −505,41 |
| yield | −21,60% | −21,65% |

Różnica 27 zakładów (1,2%) pochodzi prawdopodobnie z rozstrzygania remisów przy
wyborze strategii. Wnioski identyczne.

**Czas:** nested runner 41 min (22 ligi, ~6 rdzeni), replay 16 min
(jednowątkowo), bramka 20 s.

## 2. Bramka wobec Pinnacle — werdykt nie zależy od benchmarku

Podejrzenie było takie, że „wszystkie 22 ligi gorsze od rynku" to artefakt
porównywania się ze **średnią** bukmacherów, która jest łatwiejszym
przeciwnikiem niż Pinnacle.

| | wobec `market_average` | wobec `pinnacle` |
|---|---:|---:|
| ligi, w których model lepszy | **0 / 22** | **0 / 22** |
| ligi z dodatnią górną granicą CI | — | **1 / 22** |
| typowa strata log-lossu | −0,02 … −0,06 | −0,02 … −0,07 |

> **Uwaga wbrew intuicji:** po zdjęciu marży średnia bukmacherów jest
> praktycznie tak samo ostra jak Pinnacle — różnice rzędu 0,01 log-lossu,
> a w siedmiu ligach średnia jest **lepsza**. Konsensus bywa mądrzejszy od
> najostrzejszej pojedynczej księgi.

## 3. Dlaczego yield wynosi −21,6% — reguła wyboru zbiera własny szum

Na 2 307 postawionych nogach sezonu 2025/26, wszystkie ligi razem:

| | |
|---|---:|
| model obiecał | **0,3944** |
| rynek obiecał (te same nogi) | 0,2388 |
| naprawdę się zdarzyło | **0,2293** |
| zawyżenie modelu | **+0,1651** |
| zawyżenie rynku | **+0,0095** |
| **nadwyżka modelu** | **+0,1556** |

**We wszystkich 20 ligach z próbą ≥50 nadwyżka jest dodatnia**, w paśmie
od +0,119 (D1) do +0,211 (SC2). Zawyżenie rynku waha się wokół zera w obie
strony; zawyżenie modelu nigdy nie spada poniżej +0,083.

Mechanizm: model różni się od rynku, ta różnica jest **szumem**, a bramka
wybiera systematycznie **największe** różnice. Im ostrzejszy próg, tym gorszy
wybór. Rynek na tych samych meczach ma rację, więc „trudne mecze" tego nie
tłumaczą.

Mierzone przez `SelectionReliability` w `app/evaluation/gates.py`.

## 4. M0 — mechanizm istnieje

500 598 nóg, 30 808 meczów, 10 bukmacherów. Konsensus **leave-one-out**, marża
zdejmowana osobno dla każdej księgi.

| decyl | kurs | trafień | konsensus | CLV | yield przed podatkiem |
|---|---:|---:|---:|---:|---:|
| **0** (kurs najdłuższy) | 3,64 | 0,3501 | 0,3516 | **+0,0088** | **−3,95%** |
| 5 | 3,42 | 0,3312 | 0,3269 | −0,0003 | −6,33% |
| **9** (kurs najkrótszy) | 3,06 | 0,3612 | 0,3525 | **−0,0087** | **−12,28%** |

Korelacja odchylenia z ruchem własnej ceny: **−0,359**, monotonicznie przez
wszystkie dziesięć decyli. Ruch bez warunkowania wynosi dokładnie zero, więc to
nie dryf.

**Teza noty 25 jest prawdziwa:** miękka cena wraca do konsensusu, a obstawianie
jej zamiast ostrej jest warte **8,3 punktu** yieldu. Konsensus jest przy tym
świetnie skalibrowany (0,3516 obiecane, 0,3501 zrealizowane).

## 5. M0 — i nie wystarcza

| reżim | najlepszy decyl |
|---|---|
| **τ = 0,88 (Polska)** | **−15,47%** [−16,69%, −14,26%] |
| τ = 1,00 (bez podatku) | −3,95% [−5,33%, −2,57%] |

Przy polskim podatku **żaden bukmacher nie ma przedziału ufności sięgającego
zera**; najlepszy to interwetten −10,10% [−13,23%, −6,97%].

Bez podatku czterech wychodzi na plus punktowo (interwetten +2,16%, 1xbet
+1,86%, ladbrokes +0,58%, bwin +0,05%), ale **ani jeden nie ma przedziału
wykluczającego zero**. To break-even, nie przewaga. Dwaj najlepsi nie są zresztą
legalnie dostępni w Polsce.

**Zaostrzanie progu nie pomaga.** Schodząc z najniższych 10% odchylenia do
najniższych 0,5%, yield błądzi między −3,1% a −7,6% przy rozszerzającym się
przedziale. Prawdziwy sygnał wzmocniłby się przy ostrzejszym filtrze.

## 6. Zastrzeżenia

- **Brak timestampów kwotowań obciąża pomiar w stronę ZNAJDOWANIA sygnału.**
  „Otwarcie" u dwóch ksiąg to nie ten sam moment, więc część odchylenia jest
  różnicą czasu obserwacji, nie realną rozbieżnością. Prawdziwy efekt jest więc
  raczej **mniejszy** niż zmierzony — co wzmacnia wniosek negatywny.
- Tylko rynek 1X2 i tylko zdjęcia tygodniowe. Rozbieżność, która realnie żyje,
  dzieje się **w dniu meczowym**, a tego te dane nie widzą. To dokładnie powód,
  dla którego nota 25 stawia snapshotter jako fazę 0.
- Replay ma `promotion_eligible=false` na poziomie manifestu — opening odds użyte
  po jawnym założeniu badawczym.

## 7. Co z tego wynika

Trzy niezależne „nie", każde zmierzone na tych samych danych:

1. **model kontra rynek** — 0 z 22 lig, wobec obu benchmarków;
2. **reguła wyboru** — zbiera własny szum, nadwyżka +0,156 we wszystkich ligach;
3. **rozbieżność rynkowa (M0)** — realna i monotoniczna, ale mniejsza od podatku.

Zostają trzy kierunki, w kolejności od najbardziej do najmniej obiecującego:

1. **Reżim bez podatku.** Jedyna dźwignia ruszająca 11,5 punktu naraz. Betclic
   „gra bez podatku" na SOLO wisi w notatkach od początku i **nadal nie został
   zaudytowany** — patrz [[23 - Krytyczna ocena wykonalnosci]] §9.
2. **Prawdziwe snapshoty z timestampami.** Bez nich nie zobaczymy rozbieżności
   tam, gdzie ona żyje. Konto API-Football jest zawieszone od 13.09.
3. **Produkt z [[23 - Krytyczna ocena wykonalnosci]] §5 — „nie sprzedajecie
   alfy".** Mamy teraz twardy dowód, że alfy w tych danych nie ma, i to
   paradoksalnie wzmacnia tamtą tezę: sprzedawalne są kompresja researchu,
   dyscyplina i weryfikowalne receipts.

**Czego NIE robić:** dokładać czwartego podejścia do modelu sportowego (TimesFM,
kolejne cechy) przed rozstrzygnięciem punktu 1. Sufit jest teraz policzony —
nawet model idealnie skalibrowany musiałby pokonać 11,5 punktu podatku, a cała
zmierzona nieefektywność rynku jest warta 8.
