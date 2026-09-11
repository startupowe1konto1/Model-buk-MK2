# Skąd wziął się ten katalog

## Źródło

Kod w `app/` **nie powstał w tym repozytorium**. Został skopiowany 11.09.2026 z:

- repozytorium: `github.com/mkonieczny1212/Model-buk`
- commit: `7dc4bab` z 2026-09-11
- autor: mkonieczny1212

To równoległa linia pracy nad **tym samym projektem** — tamto repozytorium ma te
same notatki `Football-Prediction-Brain` i ten sam `experiments/test-bets.csv`.
Różnica polega na tym, czym każda strona się zajęła: tutaj powstał backtest CLV
na rynku 1X2, tam silnik przewidujący **rzuty rożne**.

## Co to robi

Silnik `corner-dual-v0.2`: przewiduje liczbę rożnych osobno dla gospodarzy i
gości, zamienia to na rozkłady prawdopodobieństwa i wycenia rynki „łącznie
rożnych" oraz „rożne drużyny". Do tego aplikacja webowa i 12 testów.

Metodycznie jest to zrobione porządnie: cechy liczone wyłącznie z przeszłości
(`shift(1)`), walidacja chronologiczna a nie losowa, kursy bukmacherskie **nie
są** wejściem modelu, marża zdejmowana dopiero po predykcji, model zamrożony
przed otwarciem danych testowych.

Ich własny, uczciwie postawiony wniosek z `MODEL_REPORT.md`:

> „Closing market był lepiej skalibrowany niż PURE v0.1 na każdej z czterech
> linii. Tego nie wolno ukrywać. v0.1 nie dowodzi przewagi informacyjnej nad
> rynkiem."

**To ta sama konkluzja, do której doszliśmy niezależnie na rynku 1X2** (patrz
`reports/` i `HANDOFF.md`). Dwie różne osoby, dwa różne rynki, dwie różne
metody — i ten sam wynik. To mocniejszy dowód niż każdy z osobna.

## Trzy rzeczy, o których trzeba wiedzieć

### 1. Ich reguła decyzyjna nie znała podatku

W chwili kopiowania `expected_value()` liczyło `p * kurs - 1`, czyli
opłacalność **przed** polskim podatkiem. To ta sama luka, którą ten projekt
zamknął 09.09.2026 — przez nią jedyny kandydat, który przeszedł przez bramkę
w pilotażu 27.08, okazał się zakładem przegrywającym.

Poprawione po imporcie; szczegóły w historii gita. Konsekwencja dla ich
raportów: podane tam ROI (np. **+20,7%** dla bramki v0.1) jest liczone przed
podatkiem. Przy 12% od stawki realny zwrot jest o kilkanaście punktów niższy,
a ich własny przedział ufności i tak obejmował zero.

### 2. Ich kopie wspólnych notatek są starsze od naszych

Notatki `00`, `14`, `21`, `22` i `README` różnią się — ich wersje pochodzą
sprzed poprawki podatkowej i ich nota 22 definiuje wyłącznie `EV_gross`.
**Nie zostały skopiowane.** Obowiązują wersje z tego repozytorium.

### 3. Inny zestaw narzędzi niż reszta repozytorium

`app/` wymaga pandas, numpy, scipy, scikit-learn, catboost, FastAPI i uvicorn
(patrz `app/requirements.txt`). Reszta tego repozytorium — w szczególności
`backtest/` — jest świadomie napisana **wyłącznie na bibliotece standardowej**
i ma działać bez instalowania czegokolwiek.

To nie jest konflikt: `app/` jest osobnym katalogiem z własnymi zależnościami.
Ale nie mieszaj tych dwóch światów — nie importuj pandas do `backtest/` ani nie
przepisuj `app/` na stdlib bez uzgodnienia z autorem.

## Uwaga o autorstwie

Kod został skopiowany, a nie scalony razem z historią gita, żeby nie wciągnąć
tu nieaktualnych wersji wspólnych notatek. Historia zmian tego kodu żyje
w repozytorium źródłowym podanym wyżej.
