# Next implementation steps

## P0 — make the corner engine stronger before adding more markets

1. Replace venue-window strength ratios with a proper dynamic team attack/defence state model.
2. Add calendar-time decay rather than only match-count windows.
3. Add uncertainty/shrinkage directly to predicted lambdas for promoted/low-sample teams.
4. Calibrate O/U probabilities on chronological OOF only.
5. Compare CatBoost with a hierarchical Negative Binomial / GLM baseline.
6. Add team-corners market backtests when historical team-line prices are available.

## P1 — production data layer

1. Canonical team/competition/provider IDs.
2. Daily append-only match and odds archive.
3. `known_at`, `fetched_at`, provider timestamp and prediction horizon on live inputs.
4. Market snapshots from Polish executable bookmakers.
5. Data-quality and provider-disagreement flags.

## P2 — additional engines

After the corners engine passes its quality gates:
- goals / BTTS;
- team SOT;
- team shots;
- cards;
- only later player props.

## Quality gates before calling a model deployable

- probability calibration competitive with market in relevant buckets;
- stable OOS behavior across multiple periods;
- no one-team/one-line concentration explaining most profit;
- edge buckets improve monotonically enough to be credible;
- prospective frozen sample with executable prices;
- CLV tracked;
- no leakage or post-kickoff data in any feature.
