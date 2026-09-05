---
tags: [architecture, engineering, pipeline, stack]
status: draft
date: 2026-09-05
---

# 24 — Architektura silnika

> Diagram interaktywny: `model-buk-engine.html` (archify, dark/light, eksport PNG/SVG). Źródło: `model-buk-engine.architecture.json`.

## 0. Zasada nadrzędna: trzy różne zegary

Najczęstszy błąd architektoniczny w tego typu systemach to sklejenie trzech niezależnych pętli w jedną. To są trzy osobne zegary:

| Zegar | Co robi | Wymaganie |
|---|---|---|
| **Batch/historyczny** | backfill, trening, backtest | poprawność, nie latencja |
| **Snapshot** | odds / lineups / injuries | ciągły, append-only, **nigdy nie blokowany przez model** |
| **Decyzyjny** | T−72h / T−24h / T−6h / T−60min | czyta point-in-time, produkuje zamrożone predykcje |

[[11 - Czas predykcji i leakage]] opisuje EARLY / PRE-MATCH / LINEUP jako warianty modelu. To są **wyzwalacze pipeline'u**, a pętla snapshotu musi być od nich odpięta. Jeśli snapshotter czeka na model, tracicie dane w momentach największego ruchu kursów.

## L0 — Raw Landing Zone

Niezmienna, append-only strefa lądowania. Surowy payload dokładnie jak przyszedł + koperta:

```text
provider, endpoint, request_params,
fetched_at, provider_timestamp,
payload_hash, api_credits_used
```

Zero transformacji przy zapisie. To jest gwarancja „zawsze możemy odtworzyć". Bez niej reprodukowalny backtest nie istnieje — a [[12 - Ewaluacja i backtesting]] całe stoi na tym założeniu.

**Odds Snapshotter jest osobnym procesem na własnym harmonogramie**: godzinowo dla universe, zagęszczanie do 15/5 min w ostatnich 6 godzinach przed kickoffem.

## L1 — Canonical Entity Layer

Rozstrzyganie tożsamości między providerami: drużyna, zawodnik, sędzia, stadion, rozgrywki, sezon. „Górnik Zabrze" vs „Gornik Zabrze" vs „GKS Górnik".

- tabele crosswalk `provider_id → canonical_id` z confidence i ręcznym override;
- `definition_version` dla każdej statystyki — „strzał celny" znaczy co innego u różnych providerów, co [[21 - Pilot live 27-08-2026]] już wykrył na xG.

**To jest warstwa, na której te projekty umierają.** Niewdzięczna, nieograniczona i ok. 30% realnej pracy. Warto to wiedzieć przed startem, a nie w trzecim miesiącu.

## L2 — Point-in-Time Fact Store

Bitemporalny. Każdy fakt niesie `event_time`, `published_at`, `ingested_at`, `known_at`.

**Jedyne API odczytu to `as_of(t)`.** Nie da się zapytać o „stan bieżący" — nie dlatego, że nie wypada, tylko dlatego, że taka funkcja nie istnieje.

To najważniejsze ograniczenie inżynieryjne w całym systemie. Nota [[11 - Czas predykcji i leakage]] formułuje zasadę; architektura musi uczynić jej złamanie **niemożliwym**, bo leakage nie rzuca wyjątku — po cichu zawyża wyniki backtestu.

## L3 — Feature Engine

Liczy wyłącznie z widoków `as_of(t)`. Deterministyczny, wersjonowany, testowalny.

- **C0 — tanie/lokalne**: rolling team state, base rates, Elo, dni odpoczynku, strength of schedule. Liczone dla **całego universe**, nocą, z cache. Koszt marginalny ≈ 0.
- **C2/C3 — drogie/kontekstowe**: lineup delta, absencje, sędzia, environment shock. Liczone **tylko dla shortlisty**.

Feature Registry z [[04 - Rejestr cech]] jako artefakty kodu, nie tylko dokumentacja: każda cecha to nazwana funkcja z semantyką `known_at`, minimalną próbą i polityką shrinkage.

## L4 — Model Layer

Trzy rodziny z [[03 - Architektura modelu]]:

- **MARKET (M0)** — silnik de-vig. Kilka metod (proporcjonalna, Shin, log-odds). Produkuje `p_market`.
- **PURE** — tylko dane sportowe. Poisson/Dixon–Coles dla goli; osobne modele licznikowe (NegBin / Beta-Binomial) dla SOT, corners, cards.
- **HYBRID** — łączenie, tylko jeśli bije oba na CLV.

Każda wersja to niezmienny artefakt: hash kodu + wersja danych + hiperparametry + okno treningowe + krzywe kalibracji.

**Kalibracja jest osobnym, obowiązkowym etapem** (isotonic / Platt). Surowy output modelu nigdy nie trafia do decyzji.

## L5 — Market Layer

- normalizacja kursów między bukmacherami;
- **kanonizacja marketów** — „Team A over 3.5 corners" jest wyrażane na 6 sposobów; to jest realnie trudne i niedoceniane;
- de-vig per book **i** konsensus;
- **śledzenie najlepszej osiągalnej ceny**, nie średniej — i „osiągalnej" znaczy: u bukmachera, u którego użytkownik ma konto (patrz [[23 - Krytyczna ocena wykonalności]] §6);
- ruch linii / steam detection; przechwycenie closing odds do CLV.

## L6 — Cost-Aware Scanner

Lejek z [[18 - Ekonomika pipeline i cost-aware scanning]], zaimplementowany jako bramki budżetowe:

| Tier | Zakres | Koszt |
|---|---|---|
| 0 | lokalny skan całego universe z cache — tysiące trójek `match × market × selection` | ≈ 0 |
| 1 | tani batch bieżących kursów dla ocalałych | C1 |
| 2 | shortlist: lineups, injuries | C2 |
| 3 | głębokie/propsowe fetche dla finalnej garstki | C3 |

Twarde stopy: wyczerpanie budżetu zwraca `NO PREDICTION / BUDGET GATE`. Każdy fetch loguje `api_credits_used` **przypisane do kandydata** — dopiero to daje realny koszt na actionable bet.

## L7 — Decision Gate

Deterministyczna, wersjonowana reguła. Wejścia: `p_calibrated`, pasmo niepewności, `p_market_devig`, najlepsza osiągalna cena, `data_quality`, **model podatkowy**, ograniczenia egzekucji (limity stawek).

```text
EV_net = p * o * (1 - tax_rate) - 1
```

`tax_rate` jest **specyficzny dla jurysdykcji i użytkownika**: 12% od obrotu w PL, prowizja od wygranej na exchange, 0 offshore. Patrz [[23 - Krytyczna ocena wykonalności]] §1 — bez tego pola wszystkie cztery dotychczasowe testowe bety wyglądają na dodatnie, a są ujemne.

Wyjście: `BET / NO BET / NO PREDICTION` + pełny ślad uzasadnienia.

## L8 — Frozen Prediction Store

Append-only. Każda decyzja — **także NO BET** — zapisana niezmiennie przed kickoffem, z kompletem pól z [[22 - Zasady modelu i rejestr testowych betów]] §3.

Po kickoffie osobna append-only tabela rozliczeń dołącza wynik, closing odds i CLV. Pola predykcyjne są niezmienne **na poziomie bazy**, nie regulaminu.

To jednocześnie egzekucja zasady anti-hindsight i **publiczna warstwa wiarygodności produktu**.

## L9 — Evaluation & Backtest

Walk-forward używający **tego samego `as_of()` i tego samego kodu cech co produkcja**. Jeśli backtest i produkcja mają osobne ścieżki kodu, backtest jest fikcją.

Metryki z [[12 - Ewaluacja i backtesting]] + ROI **po podatku** + drawdown, w rozbiciu na `league × market`. Monitory driftu: kalibracja, zmiana schematu providera, spadki coverage, koszt na bet.

## L10 — Product Surface

- **analiza on-demand** — użytkownik podaje mecz/market, lejek odpala się tylko dla niego (pomysł Wojtka, transkrypt 1:15:00);
- **feed per użytkownik** — filtrowany po jego bukmacherach, ligach i wielkości stawki; to jednocześnie ogranicza ruch linii, bo rozprasza typy;
- **publiczny rejestr** — receipts.

## Stack — konkretnie

| Warstwa | Wybór | Uzasadnienie |
|---|---|---|
| Język | Python | ekosystem statystyczny |
| Baza | PostgreSQL (partycjonowane tabele odds) | wolumen to setki tysięcy meczów i miliony snapshotów, nie big data |
| Landing | object storage (S3/R2) + manifest w PG | tanie, niezmienne, replayable |
| Orkiestracja | cron + lekki task runner → Prefect/Dagster gdy urośnie | Airflow to przerost |
| Modele | statsmodels / scipy + scikit-learn (kalibracja) | interpretowalność jest częścią produktu |
| API | FastAPI | |

**Świadomie NIE**: Kafka, feature-store SaaS, lakehouse, Kubernetes. Przy tym wolumenie to teatr architektoniczny.

Wszystko jest DAG-iem idempotentnych, odtwarzalnych tasków kluczowanych `(entity, as_of)`. Ponowne uruchomienie musi dać identyczny wynik.

## Trzy najbardziej ryzykowne komponenty

1. **L1 Canonical / L5 kanonizacja marketów** — nudne, nieograniczone, zabójcze.
2. **L2 point-in-time** — jeden wyciek unieważnia po cichu wszystko poniżej.
3. **L5 pozyskanie kursów propsowych** — brak czystego API dla polskich booków; scraping za loginem, kruchy i sprzeczny z ToS. Patrz [[23 - Krytyczna ocena wykonalności]] §7.
