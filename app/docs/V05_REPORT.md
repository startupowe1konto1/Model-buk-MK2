# v0.5 implementation report

## Delivered

- 16-league historical coverage (2014/15+)
- multi-market engine: 1X2, BTTS, goals, corners, shots, SOT, cards
- time-decay + venue split + shrinkage + attack/opponent-suppression structure
- optional API-Football live fixture feed
- on-demand current recent statistics with cache and per-team call cap
- injuries, lineups, referee, venue context
- Open-Meteo extreme-weather context via OSM stadium matching
- odds normalization for major API-Football bet IDs
- de-vig, edge, EV and research BET gate
- odds snapshot archive in SQLite
- redesigned Match Analysis Dashboard
- EPL Corner Champion preserved as separate benchmark

## Important methodological line

Not every newly fetched factor is immediately allowed to change P_model.

**Probability inputs now:**
- time-decayed historical production/concession;
- venue splits;
- league baseline;
- Elo (mild, predefined transformation);
- current recent event counts from API, when available, shrunk to the historical baseline.

**Quality/context only for now:**
- raw injury count;
- confirmed lineups without player-strength weights;
- referee identity;
- ordinary weather.

This avoids replacing a reproducible model with manual pre-match opinions. These factors move into probability only after we quantify them and validate incremental OOS value.

## Cost controls

- dashboard fixture discovery: one date request, then local filtering across supported leagues;
- deep recent statistics only when the user opens a specific match;
- maximum five detailed historical statistics calls per team per deep analysis;
- disk cache for all provider calls;
- odds/injuries/lineups use endpoint-appropriate TTLs.

## Validation

Current automated suite: 19 tests covering previous core behavior plus multi-market probability generation, current-data adjustments, API offline mode, odds parsing, two-way/three-way de-vig, v0.5 API and registry behavior.
