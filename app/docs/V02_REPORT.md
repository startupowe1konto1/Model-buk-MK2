# Corner Engine v0.2 — implementation report

## What changed

v0.2 predicts `home_corners` and `away_corners` separately, adds shrunk dynamic attack/concession strength features, and exposes team-corners probability tables. It also compares a direct total Negative Binomial distribution with the convolution of the two team distributions using pre-2025 OOF only.

## Pre-2025 OOF

1,140 matches, expanding chronological folds 2022/23–2024/25.

| Model | MAE total corners | RMSE total corners |
|---|---:|---:|
| v0.1 total-count champion | **2.7424** | **3.4287** |
| v0.2 dual home+away | 2.7698 | 3.4717 |

Therefore v0.2 **does not replace v0.1 for total-corners forecasting**.

Side-count metrics for v0.2:
- home MAE: 2.3840;
- away MAE: 2.1267.

Estimated dispersions from OOF:
- home alpha: 0.1091;
- away alpha: 0.1568;
- total alpha: 0.0203.

The best OOF total probability method is `total_nb`, narrowly beating independent side convolution on average log loss.

## 2025/26 reference only

This season was already opened during v0.1, so it cannot be treated as untouched evidence for v0.2.

The closing market remains better than v0.2 on probability log loss at 7.5, 8.5, 9.5 and 10.5. The v0.2 frozen value gate produces positive raw ROI in the reference sample, but the bootstrap interval is wide and includes zero. No deployment claim is made.

## Champion/challenger decision

For current paper/live inference:
- **Total corners champion:** v0.1 total-count specification, refit on all completed local data.
- **Team corners challenger:** v0.2 dual model, refit on all completed local data.
- **Deployment status:** research/paper only.

This routing is stored in `models/registry.json` and intentionally separates model selection evidence from current-use refitting.
