# Pipeline danych bieżących

Ta warstwa uruchamia analizę o określonej chwili `cutoff` i zapisuje wyłącznie
audytowalny wynik oraz manifest. Surowe odpowiedzi dostawców i dane prywatne nie
są umieszczane w artefaktach przebiegu.

## Przepływ

1. Adaptery pobierają wersjonowane `SnapshotRecord`.
2. Pipeline odrzuca rekordy z `available_at > cutoff`.
3. Dla każdej encji, grupy cech i dostawcy pozostaje najnowsza znana wersja.
4. Kontrole kompletności i świeżości zatrzymują przebieg z niepełnymi danymi.
5. Wersjonowany predyktor tworzy `OpportunityCandidate`.
6. Selekcja używa dolnej granicy prawdopodobieństwa i EV po podatku oraz
   kosztach wykonania. Nie uzupełnia rankingu słabszymi typami.
7. `result.json` i `manifest.json` są zapisywane atomowo i mogą zostać ponownie
   odczytane bez powtórnego pobierania danych.

## Minimalny przykład

```python
from datetime import timedelta

from model_buk.current_pipeline import CurrentDataPipeline, CurrentPipelinePolicy

pipeline = CurrentDataPipeline(
    adapters={"fixtures": fixture_adapter, "odds": odds_adapter},
    predictor=versioned_predictor,
    predictor_id="ensemble-1x2-v1",
    output_dir="runtime/current-runs",
    policy=CurrentPipelinePolicy(
        required_feature_groups={"fixtures": 1, "odds": 1},
        max_age_by_feature_group={"odds": timedelta(minutes=30)},
        max_quote_age=timedelta(minutes=30),
        max_recommendations=10,
        min_conservative_ev_net=0.02,
    ),
)

run = pipeline.run("2026-09-18T12:00Z")
```

Adapter może być funkcją przyjmującą `RunContext` albo obiektem z metodą
`fetch(context)`. Powinien mieć jawny `component_id`. Predyktor analogicznie
może być funkcją lub obiektem z `predict(frame, context)` i powinien mieć
wersjonowany `model_id` albo zostać przekazany z `predictor_id`.

Gotowy `ApiFootballSnapshotAdapter` zamienia istniejącego
`ApiFootballClient` na snapshoty terminarza, kursów, absencji, składów i
bieżącej formy. Limity dni oraz liczby spotkań są jawne, aby kontrolować koszt
API. Kurs z czasem aktualizacji późniejszym niż `cutoff` jest pomijany — adapter
nie próbuje odtwarzać nieznanej wcześniejszej ceny. Zewnętrzna predykcja
API-Football jest celowo wykluczona z cech PURE.

`MultiMarketSnapshotPredictor` uruchamia istniejący silnik PURE wyłącznie na
terminarzu i danych zespołów, a kursy dołącza dopiero po obliczeniu
prawdopodobieństw. Tworzy kandydatów z jawnym podatkiem, kosztem wykonania oraz
prowizorycznym konserwatywnym marginesem prawdopodobieństwa. Margines ten nie
jest przedziałem ufności i nie zastępuje kalibracji — wynik pozostaje wyłącznie
paper/shadow, dopóki dana para liga × rynek nie przejdzie bramki promocji OOS.
Silnik musi mieć wersjonowany `model_id` albo otrzymać jawny `engine_id`, najlepiej
z hashem użytego artefaktu i zbioru treningowego.

## Warunek promocji modelu

Model nie staje się championem tylko dlatego, że ma dodatni ROI w jednej próbie.
`model_buk.evaluation.gates` wymaga odpowiedniej liczby obserwacji, przewagi
log-loss nad odmarżowionym benchmarkiem rynkowym potwierdzonej bootstrapem,
braku niedopuszczalnego pogorszenia Brier/ECE oraz — opcjonalnie — dodatniej
dolnej granicy rentowności netto.

Dla wyników zależnych w obrębie kolejki, dnia albo liga×sezon należy przekazać
`bootstrap_clusters`; wtedy całe bloki są losowane razem. Konfiguracje modeli są
wybierane przez `nested_walk_forward_goals` wyłącznie na inner OOF, przed
uruchomieniem zwycięzcy na outer foldzie.

Do czasu przejścia tych kryteriów wykonanie powinno działać wyłącznie w trybie
paper/shadow.

## Uruchomienie skanera

Tryb live odczytuje klucz wyłącznie ze zmiennej środowiskowej:

```powershell
$env:API_FOOTBALL_KEY = "..."
.venv\Scripts\python -m model_buk scan-current `
  --config config/paper_scan.toml `
  --mode live
```

Powtórzenie tego samego `run_id` i `cutoff` odczyta zatwierdzony wynik bez
ponownego zużywania API. `--dry-run` nie zapisuje wyniku pipeline'u, ale w trybie
live nadal może wykonać requesty i uzupełnić lokalny cache.

Tryb offline nie tworzy klienta API:

```powershell
.venv\Scripts\python -m model_buk scan-current `
  --config config/paper_scan.toml `
  --mode offline `
  --snapshots runtime/replay/snapshots.json `
  --cutoff 2026-09-20T12:00:00Z `
  --dry-run
```

Do cyklicznego uruchamiania służy `scripts/run_paper_scan.ps1`. W Harmonogramie
zadań Windows należy wywoływać ten skrypt co kilka godzin. Skrypt nie zawiera
klucza API; sekret musi być ustawiony w środowisku konta uruchamiającego zadanie.
Pipeline zakłada atomową blokadę dla katalogu przebiegu i odrzuci równoległe
uruchomienie tego samego `run_id`. Po twardym przerwaniu procesu należy najpierw
sprawdzić osierocony plik `.run-*.lock`, zamiast automatycznie go usuwać.
