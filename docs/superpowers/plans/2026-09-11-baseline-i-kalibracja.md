# Baseline, kalibracja modelu i wrażliwość de-vigu — plan przyrostu

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Sprawić, żeby raporty w `reports/` dało się przeczytać bez tej rozmowy i wyciągnąć z nich właściwy wniosek — przez dodanie punktu odniesienia, wrażliwości na de-vig i kalibracji samego modelu.

**Architecture:** Jeden nowy moduł (`baseline.py`, bez modelu i bez bramki), jedno rozszerzenie walk-forward o predykcje dla wszystkich meczów testowych, jedna integracja w raporcie. Nic nie zmienia zachowania istniejącego pipeline'u — liczby CLV mają wyjść identyczne.

**Tech Stack:** Python 3.9.6, wyłącznie biblioteka standardowa.

## Skąd ten przyrost

Finalny review gałęzi `backtest-clv` zostawił trzy ustalenia Important i jedną lukę
pomiarową. Ten plan zamyka wszystkie cztery. Kontekst i pomiary są w `HANDOFF.md`
sekcja D — **przeczytaj ją przed startem, bo zawiera liczby, które te zadania mają odtworzyć.**

## Global Constraints

- Interpreter `/usr/bin/python3`, **3.9.6**. Bez `match`, bez PEP 604, bez generyków wbudowanych w adnotacjach runtime.
- **Wyłącznie stdlib.** numpy/pandas/sklearn/pytest nie są zainstalowane i nie wolno ich dodawać.
- Testy: `unittest`, z korzenia repo. Obecnie **101 zielonych** — żaden nie może zmienić wyniku ani tolerancji.
- Dane poza repo: `/Users/wojciechrudnicki/Code_projects/hazard_saas/data/raw/football-data-uk/main`. Raporty w `reports/` są commitowane, dane nigdy.
- Determinizm: bez nieziarnowanego losowania.
- **Istniejące liczby CLV muszą zostać bajt w bajt.** Ten przyrost dokłada informację, nie zmienia pomiaru. Każde zadanie, które regeneruje `reports/backtest-clv-*.md`, musi udowodnić `diff`em, że część wspólna się nie zmieniła.

## Zmiana procesu wobec poprzedniego planu — obowiązkowa

Poprzedni plan zawierał kompletny kod każdego zadania. Skutek: implementerzy
**przepisywali zamiast wyprowadzać**, przez co pięć pustych testów przeszło z planu
do repo nietkniętych, a wykrył je dopiero mutation testing w finalnym review.

Dlatego tutaj **nie ma gotowego kodu**. Każde zadanie podaje wymaganie, własność
do sprawdzenia i kryterium akceptacji. Kod i testy wyprowadza implementer.

**Każde zadanie ma obowiązek dowieść, że jego testy gryzą:** zepsuć tymczasowo
kod tak, żeby naruszyć sprawdzaną własność, pokazać czerwony test, przywrócić
kod i potwierdzić, że jest bajt w bajt. Test bez takiego dowodu jest niedokończony.

---

### Task 1: Model-free baseline — punkt odniesienia

**Files:** utwórz `backtest/baseline.py`, `tests/test_baseline.py`

**Po co:** bez zera odniesienia „+0,09% średniego CLV" czyta się jak sukces. Zmierzone
zero wynosi +0,1059%, czyli nasz model jest *poniżej* niego.

**Wymagania:**

1. Liczy CLV dla **każdej nogi każdego meczu**, bez modelu i bez bramki.
2. Ogranicza się do **sezonów testowych** — tych, w których walk-forward w ogóle
   obstawia. Przy `min_train_seasons=2` to sezony od `2016/17` w górę: **71 264 mecze,
   213 792 nogi**. Liczenie baseline'u na wszystkich 86 024 meczach jest **błędem** —
   dokłada dwa najwcześniejsze sezony, których model nigdy nie widzi, a które mają
   systematycznie lepszy CLV przez dryf overroundu. Zakres sezonów ma wynikać z tej
   samej funkcji, której używa walk-forward, a nie być wpisany na sztywno.
3. Raportuje dla **wszystkich trzech metod de-vigu**, nie tylko zwycięskiej.
4. Daje rozbicie po sezonach i po ligach, w tym samym formacie co raport CLV.
5. Zapisuje `reports/baseline-null.md` i `.json`, CLI analogiczne do istniejących.

**Liczby do odtworzenia** (de-vig `power`, sezony testowe) — jeśli Twój wynik się różni,
zatrzymaj się i znajdź przyczynę, nie dopasowuj testu:

| metoda | średni CLV | mediana | pobito zamknięcie |
|---|---:|---:|---:|
| `proportional` | +0,0119% | +0,0000% | 49,84% |
| `power` | +0,1059% | +0,0084% | **50,08%** |
| `shin` | +0,0769% | +0,0018% | 50,01% |

**Własności do przypięcia testem:**

- Baseline pobija zamknięcie w **około 50%** przypadków. To nie jest przypadek ani
  ciekawostka — przy braku informacji musi tak wyjść, więc odchylenie od 50% o więcej
  niż ułamek punktu procentowego oznacza błąd w łańcuchu, nie odkrycie. Przypnij to
  z tolerancją, którą sam uzasadnisz w komentarzu.
- Liczba nóg to dokładnie trzykrotność liczby meczów testowych.
- Zakres sezonów zgadza się z tym, od którego walk-forward zaczyna obstawiać —
  test ma to wiązać z `walkforward`, a nie powtarzać literał.
- Średnia jest dodatnia, a mediana bliska zeru. To prawoskośność metryki ilorazowej,
  nie przewaga. Warto przypiąć, bo to najczystszy dowód, że dodatnia średnia CLV
  sama w sobie nic nie znaczy.

**Dowód, że testy gryzą:** pokaż co najmniej, że test zakresu sezonów pada, gdy
baseline policzy się na wszystkich meczach zamiast na testowych.

---

### Task 2: Kalibracja modelu

**Files:** zmodyfikuj `backtest/walkforward.py`, `backtest/run_backtest.py`; testy w `tests/test_walkforward.py`

**Po co:** znamy ECE rynku (**0,00311** na zamknięciu) i nie znamy ECE modelu. Cała
diagnoza „model przesadnie pewny na długich strzałach" opiera się na trafialności 21%
i na tym, że 15% selekcji przechodzi bramkę wymagającą +19,3%. To poszlaka, nie pomiar.

**Wymagania:**

1. Walk-forward zbiera `P_model` dla **każdego meczu sezonu testowego**, nie tylko dla
   tych, które przeszły bramkę. Kalibracja liczona wyłącznie na selekcjach jest
   warunkowana bramką, czyli z definicji obciążona.
2. Liczy dla modelu te same metryki, które `report_devig` liczy dla rynku: log-loss,
   Brier, RPS, ECE. Użyj istniejącego `backtest/metrics.py` — nie pisz drugiej implementacji.
3. Raport CLV pokazuje metryki modelu **obok rynkowych**, żeby porównanie było w jednym miejscu.
4. **Jedno przejście.** Nie wolno uruchamiać walk-forward drugi raz po to, żeby zebrać
   predykcje. Poprzedni review złapał dokładnie taki zbędny przebieg w `report_devig`.
5. `summarize` i `run_backtest` mają dalej działać; sygnatury publiczne mogą się zmienić,
   ale zmiana ma być świadoma i opisana w raporcie.

**Własność do przypięcia testem:** liczba zebranych predykcji równa się liczbie meczów
w sezonach testowych — nie liczbie selekcji. To odróżnia pomiar bezwarunkowy od
warunkowanego bramką i jest właśnie tym, co ten task ma załatwić.

**Kryterium odczytu, ustalone z góry:** jeśli ECE modelu jest o rząd wielkości gorsze
od rynkowego 0,00311, diagnoza się potwierdza i następnym krokiem jest kalibracja
`P_model` (izotoniczna albo Platta), a nie dokładanie cech. Jeśli ECE jest porównywalne,
diagnoza była błędna i trzeba szukać gdzie indziej. **Zapisz wynik, nie naginaj interpretacji.**

---

### Task 3: Integracja raportu

**Files:** zmodyfikuj `backtest/run_backtest.py`; testy w `tests/test_walkforward.py` lub nowym pliku

**Wymagania:**

1. Raport CLV zawiera **wiersz baseline** z Taska 1 obok wyniku modelu, w tej samej tabeli.
   Czytelnik ma widzieć oba bez otwierania drugiego pliku.
2. Raport podaje **liczbę odrębnych meczów** obok liczby zakładów. Jeden mecz może dać
   dwie selekcje (krawędzie sumują się do zera na trzech nogach, więc dwie mogą być
   dodatnie), a nogi tego samego meczu mają mechanicznie ujemnie skorelowany CLV.
   Raportowanie samego `n` zawyża efektywną próbę.
3. Raport **nazywa wprost** rozjazd średniej i mediany jako artefakt metryki ilorazowej,
   a nie zostawia go do interpretacji. Sformułowanie wymyśl sam, ale ma być jednoznaczne.
4. Usuń z raportu wszelkie sformułowania sugerujące, że wyższy próg oznacza lepsze
   porządkowanie selekcji. Średni CLV i „pobito zamknięcie" idą między reżimami
   w przeciwnych kierunkach, więc ta teza jest nieuprawniona.

**Własność do przypięcia testem:** liczba odrębnych meczów jest **mniejsza lub równa**
liczbie zakładów, i **ostro mniejsza**, gdy którykolwiek mecz dał dwie selekcje.

---

## Kryteria akceptacji

**Techniczne:**
- `python3 -m unittest discover -s tests` w całości zielone, wyjście czyste.
- Część wspólna `reports/backtest-clv-*.md` nietknięta wobec stanu sprzed przyrostu — udowodnione `diff`em.
- Każdy nowy test ma udokumentowany dowód, że pada pod odpowiednią mutacją.

**Empiryczne — to są wyniki, nie warunki zaliczenia:**
- ECE i log-loss modelu obok rynkowych.
- Baseline dla trzech metod de-vigu, po sezonach i ligach.
- Liczba odrębnych meczów w każdym reżimie.

## Czego ten przyrost NIE robi

Nie zmienia modelu, nie dokłada cech, nie rusza bramki ani τ. Nie naprawia też wyboru
metody de-vigu na całym zbiorze (`best_method` nadal widzi sezony testowe) — to zostaje
otwarte, bo poprawne rozwiązanie wymaga przeniesienia wyboru do wnętrza pętli
walk-forward, co jest osobnym przyrostem. Ten plan sprawia jedynie, że **wrażliwość
na ten wybór jest widoczna**, zamiast ukryta.
