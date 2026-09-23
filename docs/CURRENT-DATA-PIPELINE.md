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

## Import danych do historycznego replayu

`model_buk.evaluation.replay_data.load_replay_data(path)` przyjmuje CSV albo
JSONL/NDJSON z jednym wierszem na wybór rynku w danym snapshotcie. Wymagane są
kolumny: `cutoff`, `kickoff`, `fixture_id`, `market_key`, `selection`,
`decimal_odds`, `probability`, `probability_lower_bound`, `observed_at`,
`snapshot_id`, `bookmaker`, `tax_multiplier`, `execution_cost`,
`correlation_group`, `settlement`, `market_group_id` i `market_size`.

Opcjonalne kolumny to `execution_price_kind` (domyślnie `snapshot`),
`closing_odds` (wyłącznie diagnostyka CLV) oraz `cluster_id` (domyślnie cutoff).
Wszystkie timestampy muszą zawierać strefę czasową. Wiersz z cutoffem po
kickoffie, obserwacją po cutoffie, niepoprawnym prawdopodobieństwem albo ceną
`closing` jako wykonawczą trafia do kwarantanny. Brak wymaganych kolumn odrzuca
cały plik. Wynik importu zawiera SHA-256 pliku, liczbę zaakceptowanych i
odrzuconych rekordów oraz błędy przypisane do numeru wiersza.

Importer nie wylicza predykcji modelu ani nie odtwarza kursu, którego nie ma w
zapisanym snapshotcie. Dane wejściowe muszą więc zawierać punktową predykcję i
jej dolną granicę dostępną przed cutoffem; dopiero wtedy można złożyć je z
historycznymi kwotowaniami i settlementem.

## Uruchomienie nested replayu

Komenda `replay-history` wykonuje nested walk-forward na powyższym wejściu i
planie JSON. Plan definiuje strategię albo siatkę metod de-vig, progów EV i
top-N oraz wewnętrzne i zewnętrzne foldy. `--by-stratum` stroi strategie
oddzielnie dla każdego `stratum_id`, np. ligi.

```powershell
python -m model_buk replay-history `
  --input runtime/replay/opportunities.csv `
  --plan config/replay_plan.example.json `
  --outdir runtime/replay/run-2026-09-23 `
  --by-stratum
```

Domyślnie komenda zatrzyma się, jeśli importer skieruje choć jeden rekord do
kwarantanny. `--allow-quarantine` pozwala kontynuować na poprawnych rekordach,
ale zachowuje pełny wykaz odrzuceń w raporcie. Katalog raportu musi być nowy albo
pusty. Wynik obejmuje wyniki inner foldów, wybrane strategie, ledger zewnętrzny,
podsumowania outer foldów, kwarantannę i manifest z hashem danych oraz planem.

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

## Stan lokalnych danych historycznych

Lokalne archiwa `football_data` można wczytać bez rozpakowywania przez
`load_football_data_uk_archives`. W sprawdzonej kopii adapter odczytał 92 327
meczów z 22 lig (2014-07-25–2026-05-31) oraz 5 132 553 rekordy kursowe dla 1X2,
Asian Handicap i totals, w wariantach opening i closing.

Pliki football-data.co.uk nie zawierają timestampu pobrania poszczególnych
kursów. Opening jest oznaczeniem rodzaju ceny w źródle, ale nie dowodzi, że
konkretna cena była dostępna w wybranym cutoffie. Te dane nadają się do
historycznej oceny prognoz względem rynku i diagnostyki closing-price; nie
stanowią same w sobie audytowalnego dowodu wykonalnego ROI point-in-time. W
archiwum jest również jeden timestampowany snapshot API-Football, ale to za mało
do walidacji historycznej. Do pełnego replayu wykonania potrzebna jest seria
snapshotów z `observed_at` przed kickoffem.

Do celów eksploracyjnych można przygotować wiersze replayu z cen opening i
zewnętrznych predykcji OOS. Komenda wymaga jawnego potwierdzenia założenia o
dostępności kursu; oznacza każdy rekord jako `research_only`, a replay zapisuje
blokadę promocji w manifeście.

```powershell
$archives = Get-ChildItem "$env:USERPROFILE\Desktop\Model Buk\*.zip" | ForEach-Object FullName
python -m model_buk prepare-football-data-replay `
  --archives $archives `
  --predictions outputs/nested_oos_predictions.csv `
  --out runtime/replay/football_data_opportunities.csv `
  --assume-opening-available
```

`--predictions` musi zawierać `source_row_id`, `outer_fold`, `actual`, `p_home`,
`p_draw` i `p_away` z nested OOS. Wiersze bez rozliczonego wyniku lub bez pełnego
kursu opening nie trafią do replayu. Closing odds są dołączane tylko jako
diagnostyka. Dla danych z prawdziwymi timestampami użyj importerów CSV/JSONL bez
flagi `--assume-opening-available`.

Predykcje per liga można wygenerować nested runnerem. Plan przykładowy obejmuje
dwa modele count-data, wybór parametrów na inner OOF i kalibrację przed każdym
outer foldem:

```powershell
python -m model_buk nested-goals-archives `
  --archives $archives `
  --plan config/nested_goals_plan.example.json `
  --outdir runtime/replay/model-oof
```

Runner zapisuje predykcje w `nested_oos_predictions.csv`, wyniki selekcji per
liga i manifest z hashami archiwów. Następnie ten plik podajesz do
`prepare-football-data-replay`, a przygotowany CSV do `replay-history --by-stratum`.
Przykładowa siatka strategii porównuje trzy metody de-vig, trzy progi EV i cztery
limity top-N (36 wariantów) wewnątrz foldów.

Do porównania prognoz z benchmarkiem rynku per liga służy `archive-promotion-gate`:

```powershell
python -m model_buk archive-promotion-gate `
  --archives $archives `
  --predictions runtime/replay/model-oof/nested_oos_predictions.csv `
  --outdir runtime/replay/promotion-gate
```

Raport zawiera log-loss, Brier, ECE i klastrowany bootstrap po dacie meczu.
Jest to wyłącznie bramka jakości prognoz: dla archiwów bez timestampów kursu
`promotion_eligible` pozostaje `false`, niezależnie od wyniku metryk.
