# Model Buk architecture

## Design principle

A market price is never allowed to manufacture the model probability. The pipeline separates the world model from the market comparison:

```text
raw football data
    ↓
canonical entities + match rows
    ↓
point-in-time features
    ↓
PURE count model
    ↓
probability distribution
    ↓
fair odds

bookmaker odds ──→ de-vig market probability
                         ↓
                 compare only here
                         ↓
                   edge + EV
                         ↓
                 BET / NO BET
```

## Corner Engine v0.2

For each fixture the model estimates:

```text
lambda_home = E[home corners | information known before kickoff]
lambda_away = E[away corners | information known before kickoff]
```

The count layer is CatBoost Poisson regression. Dispersion is estimated from chronological OOF residuals, and probabilities are produced with Negative Binomial distributions. Total-corner probabilities are selected between a direct total-NB approximation and convolution of the two side distributions using **pre-reference OOF only**.

## Point-in-time rule

Every team rolling or exponentially weighted metric is calculated after `shift(1)`. A current fixture can contain its final outcome in the historical table used for training/evaluation, but the feature vector for that fixture cannot read it.

Future inference appends a row with unknown outcomes and passes it through the same feature builder, so training and inference use the same feature definitions.

## Validation hierarchy

1. Unit tests for deterministic math and leakage.
2. Expanding chronological OOF for model/distribution selection.
3. Reference seasons for diagnostics only when previously opened.
4. New untouched season or prospective frozen predictions for evidence of edge.
5. CLV and realized ROI only after executable prices are archived prospectively.

## Versioning rule

Any material change to features, model family, distribution, calibration or decision thresholds receives a new model version. Previously opened holdouts cannot become untouched again.
