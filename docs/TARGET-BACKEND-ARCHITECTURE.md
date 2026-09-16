# Docelowa architektura backendu

```text
append-only raw snapshots
        ↓
canonical event/knowledge-time contract + entity resolution
        ↓
as-of feature store
        ↓
market-specific model adapters
  ├─ Elo / naive / EWMA
  ├─ Poisson / Dixon–Coles / bivariate Poisson
  ├─ dynamic state-space / Skellam
  ├─ existing corners champion/challenger
  └─ TimesFM 2.5 feature challenger
        ↓
nested rolling-origin evaluation + calibration
        ↓
odds snapshots + de-vig + tax + settlement + risk
        ↓
immutable prediction registry
        ↓
FastAPI (readiness separated from liveness)
```

## Minimalny kontrakt danych

Każdy rekord powinien zawierać co najmniej:

- `fixture_id`, `team_id`, `opponent_id`, `competition_id`, `season_id`;
- `kickoff_at`, `event_at`, `known_at`, `fetched_at`;
- `provider`, `provider_record_id`, `source_version`, `feature_version`;
- perspektywę home/away i statystyki z perspektywy zespołu;
- hash surowej odpowiedzi oraz status jakości/entity resolution.

Każda cecha użyta w prognozie musi spełniać `known_at <= prediction_cutoff`.

## Interfejs modelu

Każdy adapter powinien implementować logiczny kontrakt:

```python
ForecastProvider.predict(snapshot) -> ForecastResult
```

`ForecastResult` przechowuje cutoff, wersję modelu i danych, oczekiwane liczby,
kwantyle, rozkład wyników, prawdopodobieństwa rynków oraz ostrzeżenia jakości.
Kursy bukmachera nie mogą być cechami modelu PURE.

## Zasady decyzji

- de-vig wybierany tylko na danych treningowych wewnątrz foldu;
- `EV_net = p * odds * tax_multiplier - 1`;
- decyzja dopiero po kalibracji i z dolną granicą niepewności edge;
- domyślnie `NO PREDICTION`, jeśli brakuje walidacji league × market;
- rejestracja snapshotu kursu, limitu, czasu i reguł settlement;
- paper/shadow mode przed jakimkolwiek realnym stakingiem.
