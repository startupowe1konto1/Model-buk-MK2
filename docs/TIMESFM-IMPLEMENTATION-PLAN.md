# TimesFM — decyzja i plan wdrożenia

## Decyzja

Warto przetestować **TimesFM 2.5**, ale wyłącznie jako challenger prognozujący
sekwencyjne statystyki drużyn. Nie powinien bezpośrednio generować 1X2 ani
rekomendacji zakładów.

TimesFM 3.0 nie może być użyty w planowanym produkcie komercyjnym: jego wagi
mają licencję non-commercial. Kod repozytorium i checkpoint TimesFM 2.5 są
dostępne na Apache-2.0.

Źródła:

- <https://github.com/google-research/timesfm>
- <https://huggingface.co/google/timesfm-2.5-200m-pytorch>
- <https://www.research.google/blog/timesfm-3-a-zero-shot-foundation-model-for-multivariate-forecasting/>
- <https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE>
- <https://arxiv.org/abs/2310.10688>

## Rola w modelu

Jeden krok szeregu oznacza jeden mecz zespołu. Kandydaci na cele:

- npxG/xGA lub xG/xGA;
- strzały i strzały celne;
- wejścia w pole karne/deep completions;
- rożne i kartki jako osobne rynki.

Znane przed meczem kowariaty: home/away, Elo przeciwnika, liga, faza sezonu,
odpoczynek, podróż oraz wersjonowane informacje o składzie. Każda zmienna musi
mieć `available_at`.

Prognozy i kwantyle TimesFM powinny wejść jako cechy lub offset do modelu
meczowego, np. dynamicznego Dixon–Coles:

```text
log(lambda_home) = intercept + home_advantage + attack_home - defence_away
                   + beta * timesfm_features
log(lambda_away) = intercept + attack_away - defence_home
                   + gamma * timesfm_features
```

## Kolejność eksperymentu

1. Zbudować kontrakt danych i rolling-origin splitter bez leakage.
2. Uruchomić baseline: league mean, EWMA, Elo, Poisson i Dixon–Coles.
3. Dodać dynamiczny Poisson/Skellam oraz xG.
4. Uruchomić TimesFM 2.5 zero-shot dla kontekstów 32/64/128/256.
5. Przetestować TimesFM 2.5 + XReg.
6. Włączyć prognozy TimesFM jako cechy Dixon–Coles/state-space.
7. Dopiero potem rozważyć pooled LoRA na wielu ligach; nie trenować osobnego
   adaptera dla każdej drużyny.

## Walidacja

- foldy rolling-origin po kolejce/dacie;
- dwa wiersze tego samego meczu zawsze w jednym foldzie;
- osobne okno kalibracyjne i nietknięty test;
- block bootstrap po kolejce/dacie zamiast IID po zakładach;
- segmenty: liga, sezon, początek sezonu, beniaminkowie, home/away, długie kursy.

Metryki: MAE, pinball loss i CRPS dla statystyk; log loss, Brier i RPS dla
rynków; ECE/slope/intercept dla kalibracji; CLV, yield netto, drawdown i obrót
dla shadow bettingu.

## Kryteria go/no-go

TimesFM przechodzi do shadow production tylko, jeśli:

- poprawia log loss/NLL o co najmniej 1% względem najlepszego baseline'u;
- 95% block-bootstrap CI różnicy nie obejmuje zera;
- poprawa występuje w co najmniej 70% foldów league-season;
- ECE nie przekracza 2 p.p., a pokrycie przedziału 90% wynosi 87–93%;
- nie korzysta z informacji niedostępnych w chwili predykcji;
- inference mieści się w SLA, a awaria przełącza model na Dixon–Coles/xG;
- używany checkpoint i revision są przypięte oraz licencyjnie dopuszczalne.

No-go: poprawa występuje tylko w MAE statystyk, znika po kalibracji lub na
najnowszym sezonie, albo jest mniejsza niż 0,5% log loss przy istotnie wyższym
koszcie operacyjnym.
