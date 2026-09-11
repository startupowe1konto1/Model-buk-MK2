# Model Buk — Corner Engine v0.1 report

## Status

**Technicznie działa i przechodzi testy, ale nie jest jeszcze modelem gotowym na real-money deployment.** v0.1 jest zamrożonym benchmarkiem, który sprawdza cały pipeline: raw data → canonical teams → point-in-time features → probability → fair odds → de-vig → edge/EV → BET/NO BET.

## Dlaczego wybrano tę wersję

Przed otwarciem finalnego holdoutu 2025/26 porównano kilka wariantów na expanding OOF 2022/23–2024/25:

| Wariant | OOF MAE total corners | OOF RMSE |
|---|---:|---:|
| Poisson linear — core | 2.766 | 3.443 |
| HistGradientBoosting Poisson — core | 2.790 | 3.488 |
| **CatBoost Poisson — core** | **2.742** | **3.429** |
| Poisson linear — enriched xG/PPDA | 2.788 | 3.462 |
| CatBoost Poisson — enriched xG/PPDA | 2.746 | 3.438 |

Wniosek: dodanie xG/PPDA/deep-completions na tym etapie nie poprawiło OOS. Zgodnie z zasadą projektu **nie dokładamy cechy tylko dlatego, że brzmi mądrze**.

Direct LightGBM classifiers dla O/U 7.5–10.5 również nie pobiły stabilnie prostego benchmarku bazowej częstości, dlatego v0.1 pozostaje modelem count-distribution.

## Frozen model

- target: full-time total corners;
- CatBoost Poisson;
- 45 point-in-time core features + home/away team categorical;
- Negative Binomial conversion from expected count to O/U probabilities;
- NB dispersion estimated only from OOF 2022/23–2024/25;
- market prices are **not** features;
- closing odds used only after prediction for de-vig and value calculation;
- BET gate: edge >= 5 pp and model EV >= 5%;
- maximum one selected market per match.

## OOF before final holdout

- matches: **1,140**
- MAE total corners: **2.742**
- RMSE: **3.429**
- estimated NB alpha: **0.01442**

## Final untouched 2025/26 holdout

380 Premier League matches × four O/U lines = 1,520 market observations.

### Probability quality versus closing market

| Line | Model Log Loss | Market Log Loss | Model Brier | Market Brier |
|---|---:|---:|---:|---:|
| 7.5 | 0.5631 | **0.5585** | 0.1873 | **0.1856** |
| 8.5 | 0.6433 | **0.6365** | 0.2254 | **0.2223** |
| 9.5 | 0.6886 | **0.6823** | 0.2477 | **0.2446** |
| 10.5 | 0.6892 | **0.6819** | 0.2479 | **0.2444** |

**Najważniejszy wniosek:** closing market był lepiej skalibrowany niż PURE v0.1 na każdej z czterech linii. Tego nie wolno ukrywać. v0.1 nie dowodzi przewagi informacyjnej nad rynkiem.

### Pre-frozen betting gate

Z reguły `edge >= 5 pp`, `EV >= 5%`, maksymalnie jeden bet na mecz:

- 208 betów;
- 66 wygranych;
- hit rate 31.7%;
- średni kurs 4.10;
- profit +43.03u;
- surowy ROI **+20.69%**;
- bootstrap 95% CI ROI około **-4.8% do +47.7%**.

ROI wygląda bardzo dobrze, ale przedział zawiera zero. Dodatkowo wynik jest mocno skoncentrowany w długich kursach, szczególnie `Under 7.5`, więc **nie traktujemy +20.7% jako potwierdzonego edge**.

Podział głównych sygnałów:

| Linia / strona | N | ROI |
|---|---:|---:|
| Under 7.5 | 126 | +32.0% |
| Over 10.5 | 70 | +7.7% |
| Under 8.5 | 12 | -22.3% |

Dodatkowy sanity check: samo bezwarunkowe obstawianie Under 7.5 w tym jednym sezonie również dawało dodatni zwrot (~+10.6%), więc część efektu może być anomalią sezonu / charakterystyką feedu cenowego, a nie przewagą modelu.

## Co v0.1 już udowodnił

1. Pipeline jest reprodukowalny i bez leakage na poziomie rolling features.
2. Potrafimy generować probabilistyczne ceny fair i porównywać je z zamknięciem rynku.
3. Model selection odbywa się przed finalnym holdoutem.
4. „Więcej danych” nie zawsze oznacza lepszy OOS — enrichment xG nie przeszedł quality gate.
5. Mamy konkretny benchmark, który każda kolejna wersja musi pobić.

## Następny eksperyment: v0.2

Nie stroimy v0.1 pod 2025/26. Następna wersja powinna zmienić konstrukcję modelu, nie tylko hiperparametry:

- osobne `lambda_home` i `lambda_away`;
- jawne dynamiczne corner attack / corner suppression strengths;
- shrinkage do średniej ligi dla małej próby;
- opponent-strength adjustment;
- możliwość wyceny team-corners, nie tylko total;
- recency decay zależny od czasu;
- później lineup/referee tylko jeśli poprawią OOS.

Nowa wersja musi przejść walk-forward i dostać nowy prospective/untouched test. 2025/26 pozostaje historycznym benchmarkiem v0.1, a nie zbiorem do wielokrotnego strojenia.
