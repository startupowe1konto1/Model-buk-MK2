# Model Buk

Reproducible football probability + market-value engine. The project is now in the **implementation phase**: research notes remain important, but executable code, frozen configurations, tests and walk-forward validation are the source of truth for model behavior.

## Current engine: Corner Engine v0.2

`corner-dual-v0.2` predicts home and away corner counts separately, converts them to full probability distributions and prices both total-corners and team-corners markets.

Core rules:
- PURE probabilities are calculated **without bookmaker odds as model inputs**;
- every rolling feature is point-in-time (`shift(1)`);
- validation is chronological, never random train/test;
- bookmaker margin is removed only after the model prediction;
- `BET` requires both minimum edge and minimum EV;
- `NO BET` is a normal output;
- 2025/26 is a **reference benchmark for v0.2**, not an untouched proof, because it was already opened during v0.1 research.

## Project structure

```text
src/model_buk/
  data.py                  provider reconciliation / canonical match table
  features.py              point-in-time rolling features
  strengths.py             shrunk attack/concession corner strengths
  distributions.py         Poisson/NB probability layer
  models/corner_dual_v02.py home/away count models
  v02_backtest.py          walk-forward + reference benchmark
  inference.py             future-fixture pricing
  decision.py              de-vig, edge, EV, BET/NO BET
  schema.py                data contracts
  evaluation/              count/probability/calibration metrics
  markets/                 market-pricing utilities
config/
  corners_v02.toml         frozen v0.2 configuration
models/                    trained model artifacts + metadata
outputs/                   OOF/reference results
artifacts/                 generated examples
 tests/                    leakage, distribution, pricing and schema tests
```

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
pytest -q
```

## Rebuild the canonical and processed datasets

Until data preparation is folded into the main CLI, run:

```bash
python prepare_dataset.py \
  --matches /path/to/Matches.csv \
  --understat /path/to/eng_team_match_stats.csv \
  --footiqo-corners /path/to/corners_cards_closing_PL_2025_26.xlsx \
  --big-five /path/to/big_five_2025_26.xlsx
```

This writes:
- `data/canonical/epl_matches_v01.csv.gz` — raw canonical point-in-time history used for future fixture feature generation;
- `data/processed/epl_total_corners_v01.csv.gz` — model-ready historical/reference feature table.

## Run v0.2 walk-forward backtest

```bash
model-buk backtest-corners-v02 \
  --data data/processed/epl_total_corners_v01.csv.gz \
  --config config/corners_v02.toml \
  --outdir outputs \
  --modeldir models
```

The pipeline writes OOF predictions, distribution comparison, reference probabilities, calibration tables, selected reference bets and model metadata.

## Fit current-use models on all completed local data

After a new completed-match batch is appended, refit the frozen specifications for live/paper inference:

```bash
model-buk fit-live-corners
```

This creates `models/registry.json` plus current-use artifacts under `models/live/`. Backtest artifacts stay separate from current-use fitting.

## Price a future fixture

The default router uses the **v0.1 total-corners champion** for total markets and the **v0.2 dual challenger** for team-corners:

```bash
model-buk predict-corners \
  --date "2026-09-12 17:30" \
  --home "Liverpool" \
  --away "Manchester City"
```

The output contains:
- expected home/away/total corners;
- total O/U probabilities and fair odds for 7.5–10.5;
- team-corner probabilities and fair odds for 2.5–7.5;
- deterministic data-quality diagnostics.

Only after the PURE prediction exists, compare with a two-way market:

```bash
model-buk predict-corners \
  --date "2026-09-12 17:30" \
  --home "Liverpool" \
  --away "Manchester City" \
  --line 9.5 \
  --over-odds 1.95 \
  --under-odds 1.85
```

The market block returns de-vig market probability, fair odds, edge, EV and `BET/NO BET` using the frozen thresholds from `config/corners_v02.toml`. `predict-corners-v02` remains available as a research-only dual-model command.


## Run the web application

Model Buk now includes a FastAPI backend and a lightweight browser UI. The web layer uses the same frozen inference code as the CLI and stores prospective prediction payloads in an append-only SQLite registry.

```bash
pip install -e .
model-buk-web
```

Open `http://127.0.0.1:8000`.

Useful endpoints:
- `GET /api/status` — model/data freshness and champion/challenger state;
- `GET /api/teams` — canonical teams available in local history;
- `POST /api/predict/corners` — PURE prediction plus optional market comparison;
- `GET /api/predictions` — frozen local prediction log.

Runtime paths can be overridden with `MODEL_BUK_HISTORY`, `MODEL_BUK_MODEL_ROOT`, `MODEL_BUK_CONFIG` and `MODEL_BUK_DB`.

## Verification

Current implementation checks:
- rolling-feature leakage;
- valid/monotonic probability distributions;
- two-way de-vig math;
- team and total corner probability monotonicity;
- strength shrinkage;
- data-schema failures;
- market decision gate separation from PURE inference.

Run:

```bash
pytest -q
```

## Current evidence

v0.2 is an engineering/research milestone, **not a claim of profitable deployment**. On pre-2025 OOF, the dual model currently does not beat the simpler v0.1 total-count model on total-corner MAE. On the already-seen 2025/26 reference season, the closing market also remains better on probability log loss. That is useful evidence: the codebase works, but the next job is improving information quality/model structure rather than curve-fitting thresholds.

See `docs/ARCHITECTURE.md` and `docs/NEXT_STEPS.md`.
