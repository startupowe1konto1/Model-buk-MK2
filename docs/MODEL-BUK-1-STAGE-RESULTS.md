# Model BUK 1 — wynik etapów 1–5

Stan po pełnym przebiegu z 23 września 2026 r.

## 1. Historyczny replay z CLI

Dodano planowany replay CSV/JSONL z walidacją point-in-time, kwarantanną,
hashami źródeł, nested walk-forward i niezależnym doborem strategii per liga.
Przykładowy plan porównuje 36 wariantów: trzy metody de-vig, trzy progi EV i
cztery limity top-N.

## 2. Dane rzeczywiste

Adapter wczytał bez rozpakowywania pięć lokalnych archiwów football-data.co.uk:

- 92 327 meczów z 22 lig;
- 5 132 553 rekordy kursowe;
- zakres dat od 2014-07-25 do 2026-05-31.

Źródło nie zawiera timestampów poszczególnych kwotowań. Opening odds zostały
więc użyte wyłącznie po jawnym przyjęciu założenia badawczego, a cały replay ma
`promotion_eligible=false`.

## 3. Nested benchmark per liga

Porównano Dixon–Coles i ujemny dwumianowy NB2, wybierając konfigurację na inner
OOF przed predykcją outer fold. Wszystkie 22 ligi zakończyły się powodzeniem:

- 30 956 prognoz nested OOS;
- 30 912 spotkań z pełnym kursem opening wykorzystanych w replayu;
- 688 325 wierszy możliwości, 0 rekordów w kwarantannie.

W outer 2025/26 wybrane strategie zawarły łącznie 2 334 zakłady, uzyskując
−505,4128 jednostki, czyli yield −21,65%. Cztery ligi miały dodatni punktowy
yield, ale żadna nie miała dodatniej dolnej granicy 95% CI.

## 4. Paper/shadow

Pipeline point-in-time, tryb offline/live, kontrola świeżości, atomowe manifesty
i skrypt do uruchamiania cyklicznego są gotowe i objęte testami. W lokalnym
zbiorze nie ma serii prawdziwych, timestampowanych snapshotów kursów, dlatego
nie można jeszcze przedstawić prospektywnej próbki paper/shadow. Kolejne
uruchomienia muszą zbierać takie snapshoty przed kickoffem.

## 5. Bramka promocji

Dodano per-ligową ocenę względem odmarżowionego benchmarku rynku: log-loss,
Brier, ECE i klastrowany bootstrap po dacie meczu. Na 30 927 dopasowanych
prognozach każda z 22 lig miała gorszy log-loss od rynku. Żadna liga nie
przeszła bramki jakości, a brak timestampów stanowi niezależną blokadę
promocji.

Wniosek: obecny model 1X2 nie powinien zostać promowany. Następny cykl powinien
skupić się na ulepszeniu modelu i zbieraniu prospektywnych snapshotów, a nie na
strojeniu strategii bukmacherskiej na tym samym archiwum.

## Weryfikacja

Pełny zestaw testów: 229 zaliczonych, 1 ostrzeżenie zależności Starlette.

Artefakty wykonania są zapisane lokalnie w `app/runtime/replay/`:

- `model-oof-all/` — prognozy i selekcje modeli;
- `opportunities-all.csv` — wejście pełnego replayu;
- `replay-all/` — ledger i raporty strategii;
- `promotion-gate-all/` — metryki i decyzje bramki.
