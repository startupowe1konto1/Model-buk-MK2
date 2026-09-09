---
tags: [model-rules, experiments, test-bets, reproducibility]
status: active
---

# Zasady modelu i rejestr testowych betów

## 1. Model produkcyjny nie jest opinią

Model Buk ma być **suchym systemem matematyczno-statystycznym**. Jego rolą jest estymacja prawdopodobieństw zdarzeń, porównanie ich z rynkiem i zastosowanie wcześniej ustalonej reguły decyzyjnej.

Niedozwolony schemat produkcyjny:

`„wydaje się, że zespół będzie dominował” → 70% → BET`

Wymagany schemat:

`dane point-in-time → features/interactions → wersjonowany model → P_model → uncertainty/calibration → fair odds → market de-vig → edge/EV → decision gate`

Narracja może służyć do **postawienia hipotezy badawczej**, ale nie do ręcznej zmiany prawdopodobieństwa w produkcji.

## 2. Status ręcznych pilotów

Dotychczasowe `v0-manual-*` zachowujemy, ponieważ pomagają odkrywać wymagania dotyczące danych i rynku. Są jednak oznaczone jako **manual/research**.

Ich zadanie:

- sprawdzić, czy dane da się pozyskać;
- znaleźć potencjalne interakcje;
- sprawdzić dostępność konkretnych marketów;
- nauczyć nas formatu końcowego outputu;
- ujawnić konflikty providerów.

Nie mogą być używane jako dowód, że model ma edge.

## 3. Frozen prediction protocol

Każdy testowy bet musi przed kickoffem otrzymać trwały rekord zawierający co najmniej:

```yaml
bet_id:
pilot_id:
prediction_timestamp:
match_date:
competition:
home_team:
away_team:
market_family:
selection:
line:
side:
bookmaker:
odds:
p_model:
model_version:
data_version:
data_quality:
model_confidence:
decision:
stake_test:
source_timestamp:
```

Po kickoffie pól predykcyjnych **nie wolno edytować**. Dopisujemy jedynie rezultat, settlement, closing odds/CLV i metryki oceny.

## 4. Podstawowe obliczenia

Dla kursu dziesiętnego `o` i modelowego prawdopodobieństwa `p`:

```text
fair_odds = 1 / p
raw_implied_probability = 1 / o
edge = p - market_probability_devig
EV_gross = p * o - 1
EV_net   = p * (tau * o) - 1        # tau = mnoznik rezimu podatkowego
```

`tau` deklarujemy per bukmacher: `0.88` w polskim standardzie (podatek 12% od
stawki), `1.00` u bukmachera bez podatku na zakłady pojedyncze. Wyświetlany kurs
jest brutto — stawka 100 zł po kursie 2.00 zwraca 176 zł, nie 200.

Przykład: jeśli `P_model=0.60`, fair odds wynosi `1.67`. Kurs `1.90` daje
`EV_gross = +14%`, co wygląda dobrze, ale przy `tau=0.88` to
`EV_net = +0.32%` — czyli praktycznie break-even. **Wnioskowanie z `EV_gross`
jest w polskim reżimie podatkowym bezwartościowe.** Do tego model musi jeszcze
przejść quality, calibration, uncertainty i minimalny safety margin.

## 5. Decision gate

Docelowo reguła ma być mechaniczna. Przykładowa struktura, której progi muszą zostać wyznaczone badawczo:

```text
IF data_quality < Q_min              → NO PREDICTION
ELSE IF calibrated_edge < edge_min   → NO BET
ELSE IF EV_net <= EV_min             → NO BET
ELSE IF uncertainty too high         → NO BET
ELSE                                 → BET CANDIDATE
```

Nie ustalamy jeszcze finalnych wartości `Q_min`, `edge_min` i `EV_min` bez backtestu.

**`EV_min` obowiązuje na `EV_net`, nigdy na `EV_gross`.** Break-even przy
`tau=0.88` leży na `p * o = 1.13636`, więc próg wyrażony w kursach brutto musi
przekraczać `+13.64%`, zanim w ogóle zacznie oznaczać zysk. Historia tej pomyłki
i tabela progów wg reżimu: [[21 - Pilot live 27-08-2026]], korekta 09.09.2026.

## 6. Rejestr testów

Kanoniczny, przyjazny Gitowi zapis znajduje się w:

`experiments/test-bets.csv`

Excel `Model_Buk_Test_Bets_Tracker.xlsx` jest widokiem roboczym do ręcznego rozliczania i zawiera:

- Dashboard z liczbą testów, hit rate, P/L i ROI;
- tabelę `Test_Bets` z odds, `P_model`, fair odds, implied probability, de-vig market probability, edge i EV;
- `Decisions_Log` również dla `NO BET` i `NO PREDICTION`;
- `Model_Principles` z regułami metodologicznymi.

Po każdym zakończonym meczu użytkownik może ustawić `Status = WIN/LOSS/VOID/PUSH` oraz wpisać faktyczny rezultat/statystykę. Excel automatycznie aktualizuje P/L, cumulative P/L, hit rate i ROI.

## 7. Zasada anti-hindsight

Jeżeli przed meczem powiedzieliśmy jedynie „warto zbadać Salzburg 4+ SOT”, ale nie zamroziliśmy konkretnego kursu i `P_model`, wynik 7 SOT **nie jest trafionym betem modelu**. Jest jedynie potwierdzeniem, że hipoteza zasługuje na formalny test.

To zabezpiecza projekt przed nieświadomym cherry-pickingiem.

## 8. Co dalej

Najbliższy krok metodologiczny to wybrać jeden rynek count-data i zastąpić ręczne procenty formalną estymacją. Kandydaci: `team corners` lub `team SOT`, ponieważ odpowiadają filozofii Match Opportunity Engine i mają względnie zrozumiałą strukturę licznikową.

Pierwszy benchmark powinien być prosty i kontrolowalny (Poisson/Negative Binomial + team/opponent strengths + venue + decay), a dopiero później rozszerzany o interakcje.
