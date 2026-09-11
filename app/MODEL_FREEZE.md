# Model Buk — Corner Engine v0.1 freeze

Frozen before evaluating the 2025/26 Premier League closing-corners holdout.

## Objective
Predict the full-time **total number of corners** without using bookmaker prices as model inputs.

## Data
- Historical EPL: Club Football Match Data, 2014/15–2024/25.
- Understat is merged only as a research/enrichment source; v0.1 core model does **not** use xG/PPDA/deep-completion features.
- Final holdout: Footiqo EPL 2025/26, 380 matches, closing O/U corners prices at 7.5/8.5/9.5/10.5.

## Features
45 point-in-time features: prior rolling/EWM corners, shots, SOT, goals, venue splits, rest, points form, games played and rolling league baselines. Every rolling team statistic uses `shift(1)`.

## Model
CatBoost Poisson regression of total corners.
- iterations: 600
- depth: 5
- learning_rate: 0.03
- l2_leaf_reg: 8
- seed: 42
- categorical: home_team, away_team

The predictive mean is converted to O/U probabilities with a Negative Binomial distribution. Dispersion is estimated only from pre-holdout out-of-fold predictions (2022/23–2024/25).

## Decision rule
For each offered line and both sides:
- de-vig closing market probability;
- model edge >= 5 percentage points;
- model EV >= 5%;
- maximum one bet per match, selecting the candidate with highest model EV.

The 2025/26 holdout must not be used to tune this v0.1 specification after results are observed.
