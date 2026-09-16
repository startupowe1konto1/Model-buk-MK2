# Model Buk v0.5 — backend badawczy

> Status: research/paper betting only. Szerokie rynki v0.5 nie emitują
> rekomendacji `BET` bez jawnego włączenia eksperymentalnej bramki po walidacji.
> Ekonomika używa domyślnego polskiego profilu podatkowego 0.88.

Model Buk is a reproducible football probability + market-value research engine. v0.5 expands the previous EPL/corners prototype into a **multi-league, multi-market application** with an optional live context layer.

## What changed in v0.5

### 16 European leagues

- Premier League, Championship
- LaLiga, LaLiga 2
- Bundesliga, 2. Bundesliga
- Serie A, Serie B
- Ligue 1, Ligue 2
- Primeira Liga
- Eredivisie
- Belgian Pro League
- Süper Lig
- Scottish Premiership
- Ekstraklasa

The bundled historical table contains ~61k matches from 2014/15 onward for these competitions.

### Markets

The broad v0.5 engine prices:

- 1X2
- BTTS
- total goals + team goals
- total corners + team corners
- total shots + team shots
- total shots on target + team SOT
- total cards + team cards

`fair_odds = 1 / P_model`.

Bookmaker prices are **never** model inputs. They are attached after PURE probabilities are calculated.

### Current-match context (optional API-Football integration)

If `API_FOOTBALL_KEY` is configured, opening one fixture triggers a quota-aware deep analysis:

- current fixture metadata;
- recent completed fixtures;
- recent match statistics (shots, SOT, corners, cards) with a strict call cap + disk cache;
- injuries / suspensions;
- lineups when available;
- referee + venue;
- pre-match odds;
- API-Football prediction only as an **external benchmark**, never a model feature;
- weather from Open-Meteo when the venue can be matched to the bundled OSM stadium database.

Current recent event counts are shrunk toward the historical baseline and can update expected counts. Injuries, lineup, referee and ordinary weather are currently **quality/context gates**, not arbitrary probability multipliers. This is intentional: they only become probability inputs after a reproducible player/referee/weather model passes OOS validation.

### Value layer

Supported API-Football markets are normalized to Model Buk market keys. The app displays:

- Model probability
- Model fair odds
- bookmaker and offered odds
- de-vig market probability where the opposite side / 1X2 trio is available
- edge
- EV
- BET / NO BET

Default decision gate remains research-only and requires both positive probability edge and EV.

## One-click Windows start

1. Unzip the full project.
2. Double-click `URUCHOM_MODEL_BUK.bat`.
3. First launch may take a few minutes while Python packages are installed.
4. The one-click launcher opens automatically at `http://127.0.0.1:8010/?v=05` to avoid collisions with older local builds that may still occupy port 8000.

### Enable live data

Double-click:

```text
USTAW_API_FOOTBALL.bat
```

Paste your API-Football / API-Sports key, close Model Buk and start it again.

Equivalent manual command:

```bat
setx API_FOOTBALL_KEY "YOUR_KEY"
```

The key is a local Windows environment variable and is never committed to GitHub.

## Command-line setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
pytest -q
model-buk-web
```

Manual `model-buk-web` uses port 8000 by default. The bundled Windows one-click launcher sets `MODEL_BUK_PORT=8010` deliberately.

## Web API

- `GET /api/status`
- `GET /api/catalog/leagues`
- `GET /api/teams?league=EPL`
- `GET /api/fixtures?date=YYYY-MM-DD&league=EPL`
- `POST /api/analyze/manual`
- `POST /api/analyze/fixture/{fixture_id}`
- `GET /api/predictions`
- legacy: `POST /api/predict/corners`

## Model architecture in v0.5

### Broad multi-market baseline

For each event count:

1. time-decayed team production;
2. time-decayed opponent concession;
3. home/away split;
4. shrinkage to the league baseline;
5. mild Elo adjustment where appropriate;
6. Negative Binomial probability distribution;
7. current API observations, if available, are shrunk into the baseline with capped adjustment.

This is a transparent challenger layer. It is not presented as a proven profitable production model.

### EPL corners

The trained v0.1/v0.2 EPL Corner Engine is kept as a **separate historical benchmark** in EPL match analysis. We do not silently replace or rewrite its old holdout evidence.

## Safety against false edge

- point-in-time rules remain mandatory;
- no random train/test split for final evaluation;
- no odds as PURE features;
- current context is separated by whether it is `model_input`, `quality_gate`, or `monitor_only`;
- API-Football's own prediction is benchmark-only;
- `NO BET` is a first-class result;
- local SQLite stores prospective predictions and odds snapshots separately for later CLV and line-movement analysis.

## Current status

**Research / paper betting only.** v0.5 is a substantially more complete application and data pipeline, not proof of durable profitability. The next research priority is validating the broad engines league-by-league and building quantified player/lineup strength so current injuries and confirmed XI can enter probabilities without subjective weights.
