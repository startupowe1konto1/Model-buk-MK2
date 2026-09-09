# Handoff — stan projektu na 09.09.2026, po backteście CLV

> Dokument startowy dla nowej sesji. Poprzedni handoff z tego samego dnia leży w
> `docs/handoff-archiwum/HANDOFF-2026-09-09-przed-backtestem.md`. Jego opis danych,
> podatku i Betclica **obowiązuje nadal**. Unieważniona jest wyłącznie sekcja C.1
> („Backtest CLV — to jest ścieżka krytyczna") — bo została wykonana. Tutaj jest wynik.

---

## A. Jedno zdanie

Backtest CLV istnieje, przeszedł na 86 024 meczach i mówi, że **dwucechowy model Elo
nie ma przewagi nad Pinnacle** — a przy okazji ujawnił, że dwie rzeczy, w które
łatwo było uwierzyć (dodatni średni CLV i spadek efektywności rynku w czasie),
są artefaktami, nie wynikami.

---

## B. Wynik

### Trzy reżimy podatkowe

| reżim | τ | próg `p × o` | zakładów | średni CLV | mediana CLV | **pobito zamknięcie** | trafień |
|---|---:|---:|---:|---:|---:|---:|---:|
| `pl_standard` | 0,88 | 1,19318 | 32 152 | +0,33% | −0,48% | **47,72%** | 21,0% |
| `betclic_degraded` | 0,94 | 1,11702 | 46 999 | +0,14% | −0,49% | **47,52%** | 23,0% |
| `betclic_notax` | 1,00 | 1,05000 | 66 774 | +0,09% | −0,34% | **48,12%** | 25,2% |

### Zero odniesienia, bez którego powyższe nie znaczy nic

Wszystkie 258 072 nogi po de-vigu, **żadnego modelu i żadnej bramki**:

```text
średni CLV        +0,1087%
pobito zamknięcie   49,98%
```

Czyli: nasz najlepszy reżim (+0,0895% przy 48,12%) jest **gorszy od losowego brania nóg**
na obu miarach. 47,7% wobec 49,98% przy n = 32 152 to około **8 błędów standardowych
poniżej przypadku**. Model nie jest nieinformatywny — jego niezgody z Pinnacle są
systematycznie po złej stronie.

To nie jest sygnatura leakage'u. Leakage zawyża pozorną przewagę, a tu nie ma czego zawyżać.

### Dwie tezy, które wyglądały na wyniki i nimi nie są

**„Wyższy próg daje lepszy CLV, więc `EV_net` porządkuje selekcje sensownie" — nieprawda.**
Średni CLV i „pobito zamknięcie" idą między reżimami w **przeciwnych** kierunkach.
`p_close/p_open − 1` ma podłogę na −1 i brak sufitu, więc ostrzejszy próg przechyla
selekcje w długie strzały i podbija średnią przez nierówność Jensena. W decylu
najdłuższych kursów (bez modelu): średnia +0,762%, mediana −0,551%.

**„CLV spada z sezonu na sezon, więc rynek się wyostrzył" — nieprawda.**
Ten sam kształt odtwarza się z **całkowicie wyłączonym modelem i bramką** i przeżywa
wszystkie trzy metody de-vigu. Przyczyna jest w danych: średni overround kolumn `PS*`
rośnie z **2,46% (2015/16) do 5,08% (2024/25)**, podczas gdy zamknięcie stoi na ~3,9%.
Zmieniło się to, **czym jest piątkowe zdjęcie** u football-data.co.uk.

> **Reguła do zapamiętania:** porównania CLV między sezonami na kolumnach `PS*`
> nie są bezpieczne. Tabela sezonowa w `reports/` nie jest trendem efektywności rynku.

---

## C. Co powstało

Gałąź **`backtest-clv`**, 19 commitów przed `main`, **101 testów zielonych**, czysty stdlib.

```text
backtest/
├── loader.py         CSV, mieszane kodowania, typ Match, granice sezonów
├── devig.py          proportional / power / Shin
├── metrics.py        Brier, log-loss, RPS, ECE, koszyki kalibracji
├── report_devig.py   kalibracja rynku; wybiera metodę de-vigu
├── elo.py            Elo liczone wyłącznie do przodu
├── model.py          wieloklasowa regresja logistyczna, pełny gradient
├── gate.py           τ, EV_net, reguła decyzyjna
├── clv.py            closing line value
├── walkforward.py    pętla scalająca całość
└── run_backtest.py   CLI, raporty markdown + JSON
tests/                101 testów
reports/              devig-calibration.md + backtest-clv-<τ>.{md,json}
docs/superpowers/plans/2026-09-09-backtest-clv.md
```

Uruchomienie (jeden reżim ≈ 9 min):

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0
python3 -m unittest discover -s tests
python3 -m backtest.report_devig --out reports/devig-calibration.md
python3 -m backtest.run_backtest --tau pl_standard --out reports/backtest-clv-pl_standard
```

### Rzeczy, które warto znać, zanim się w to wejdzie

- **86 025 kontra 86 024.** Audyt podaje 86 025 wierszy z kompletem kursów i ma rację.
  Backtest używa 86 024, bo jeden mecz (Panathinaikos–Olympiakos, 17.03.2019, derby
  przerwane przy 0:3) nie ma wyniku końcowego. Mecz przerwany to VOID. **Nie domykać
  tej różnicy fallbackiem na `HTR`** — stan z przerwy nie jest wynikiem meczu.
- **Pliki `football-data-uk` mają niejednolite kodowanie.** Część UTF-8 z BOM, część
  latin-1. Naiwne czytanie gubi kolumnę `Div` w 11 658 wierszach i daje 23 ligi zamiast 22.
  `loader.read_rows` wykrywa BOM na bajtach przed dekodowaniem.
- **Rynek po de-vigu jest skalibrowany:** ECE **0,00311** na zamknięciu, overround
  3,44% → 3,10%. Zwycięska metoda: `power` (najniższy log-loss).
- **Cena wejścia to `PSH/PSD/PSA`, czyli zdjęcie piątkowe, nie otwarcie rynku.**
  Mierzymy ruch piątek → zamknięcie. Przewaga może siedzieć we wcześniejszym oknie,
  którego te dane nie pokazują.

---

## D. Czego NIE zrobiono — trzy ustalenia z finalnego review

Kod jest poprawny; recenzent prześledził wszystkie powierzchnie leakage'u i są czyste.
Poniższe nie psują poprawności, tylko **znaczenie liczb**. Wymagają ponownych przebiegów.

### D.1 Raporty nie mają zera odniesienia — priorytet najwyższy

`reports/*.md` podają „+0,09% średniego CLV" bez baseline'u z sekcji B. Ktokolwiek
przeczyta te pliki bez tej rozmowy, wyciągnie odwrotny wniosek. Do zrobienia:
wiersz baseline w każdym raporcie, i albo zamiana średniego CLV na średni log-CLV
(`ln(p_close/p_open)`) jako liczbę główną, albo jawne nazwanie rozjazdu średnia/mediana
artefaktem metryki.

### D.2 Metoda de-vigu wybierana na danych z przyszłości

`run_backtest.py` woła `best_method(matches)` na **całym** zbiorze, w tym na sezonach,
które backtest dopiero będzie „przewidywał". To ten sam grzech, który cały pipeline
ma wykluczać, tylko na poziomie hiperparametru.

I nie jest obojętny. Longshot CLV bez modelu:

| metoda | longshot CLV |
|---|---:|
| `proportional` | +0,056% |
| `shin` | +0,442% |
| **`power`** (wybierana) | **+0,599%** |

Rozrzut 10×, większy niż cały raportowany efekt — a pipeline wybiera najbardziej
pochlebną z trzech. Minimum: raportować wrażliwość na wszystkie trzy metody obok siebie.
Poprawnie: wybierać metodę z sezonów treningowych, wewnątrz pętli walk-forward.

### D.3 Jeden mecz może dać dwie selekcje

Krawędzie sumują się do zera na trzech nogach, więc dwie mogą być dodatnie jednocześnie.
`summarize` liczy nogi jako niezależne, choć nogi tego samego meczu mają mechanicznie
ujemnie skorelowany CLV (prawdopodobieństwa sumują się do jedynki). Brakuje liczby
odrębnych meczów i błędów standardowych — raporty zawyżają efektywną próbę.

### D.4 Nie zmierzono kalibracji modelu

Znamy ECE rynku (0,00311), nie znamy ECE modelu. Diagnoza „model przesadnie pewny
na długich strzałach" opiera się na trafialności 21% i na tym, że 15% wszystkich
selekcji przechodzi bramkę wymagającą +19,3% — to mocna poszlaka, nie pomiar.
Jeden przebieg, ~9 min, i wiadomo.

---

## E. Dług metodologiczny w samym vaulcie

**Nota 22 §5 przeczy `HANDOFF`owi.** Nota zapisuje warunek jako
`EV_net <= EV_min → NO BET` (wymaga ostrej nierówności), a handoff jako
`EV_net ≥ 0.05` (dopuszcza równość). Kod trzyma się handoffu i ma to udokumentowane
w `backtest/gate.py`. **Notę 22 trzeba uspójnić.**

Osobno, obserwacja o procesie, nie o kodzie: plan implementacyjny zawierał kompletny
kod każdego zadania, więc implementerzy przepisywali, zamiast wyprowadzać. Skutek —
**pięć pustych testów** przeszło z planu do repo nietkniętych (test leakage'u w Elo
porównujący floata sprzed mutacji, fixture z `o_open == o_close` pilnujący „nigdy nie
wyceniaj po zamknięciu", nieprzypięte granice bramki, test koszyka przechodzący na
pustym wejściu, brak wiązania indeksu kursu z wynikiem). Wszystkie wykryte mutation
testingiem i naprawione — każdy nowy test udowodniony przez tymczasowe zepsucie kodu.
**Wniosek na przyszłość: plan z gotowym kodem kupuje szybkość kosztem tego, że defekt
planu nikt po drodze nie zakwestionuje.**

---

## F. Co robić dalej, w tej kolejności

1. **Kalibracja modelu** (D.4) — jedna liczba, 9 minut, rozstrzyga diagnozę.
   Jeśli potwierdzi rozkalibrowanie, następny krok jest tani: kalibracja izotoniczna
   albo Platt na `P_model`, co często samo usuwa większość pozornych przewag.
2. **Baseline i wrażliwość de-vigu** (D.1, D.2) — żeby raporty w repo mówiły prawdę
   bez przypisu. ~40 min przebiegów.
3. **Dopiero potem cechy.** Model jest liniowym logitem na jednej cesze, więc
   `log(p_D/p_H)` jest wymuszenie monotoniczne w `elo_diff`, podczas gdy prawdziwe
   prawdopodobieństwo remisu jest garbate. Bramka strzela głównie w remisy i długie
   strzały w meczach niedopasowanych — dokładnie tam, gdzie ta specyfikacja jest najgorsza.
4. **Pogoda** (sekcja B poprzedniego handoffu) — niezależne, nadal otwarte.
5. **Dług w rejestrze i faza −1 / Betclic** — niezależne, nadal otwarte.

> **Uwaga na proporcje.** „Brak przewagi" jest uczciwym werdyktem o **tym modelu**.
> Jest znacznie słabszym dowodem na temat Pinnacle, niż sugerowałby ton raportów.
> Zbudowano przyrząd pomiarowy i zmierzono nim baseline — to jest dorobek tej sesji,
> a nie rozstrzygnięcie, czy w projekcie jest przewaga do znalezienia.

---

## G. Gdzie co jest

```text
Model-buk-2.0/
├── HANDOFF.md                        ten plik
├── docs/handoff-archiwum/            poprzednie, w tym 09.09 sprzed backtestu
├── docs/superpowers/plans/           plan backtestu, 10 tasków
├── backtest/                         pipeline (galaz backtest-clv)
├── tests/                            101 testow
├── reports/                          kalibracja rynku + 3 rezimy CLV
├── Football-Prediction-Brain/        25 not metodologicznych (zrodlo prawdy)
├── Audyt-danych-i-rynku/             notatki i skrypty 01-09
└── experiments/test-bets.csv         4 bety, dlug wg C.3 poprzedniego handoffu

hazard_saas/data/raw/                 dane surowe, 18 GB, POZA repo
```
