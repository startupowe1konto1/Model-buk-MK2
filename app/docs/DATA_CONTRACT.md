# Data contract — canonical EPL match history v0.1

The canonical history is the source used to build point-in-time features for both training and future inference.

Required fields:
- `date`, `home_team`, `away_team`, `source`, `internal_match_id`;
- home/away corners;
- home/away shots and shots on target;
- home/away goals;
- home/away xG, PPDA and deep completions.

Rules:
1. `internal_match_id` is unique.
2. `home_team != away_team`.
3. dates must parse successfully.
4. missing outcomes are allowed only for a future fixture added at inference time.
5. market odds are evaluation/decision data, never PURE feature inputs.
6. any live provider integration must include point-in-time availability,
   fetch time and provider IDs before production use.

## Point-in-time snapshot timestamps

Current live snapshots use these canonical internal fields:

- `event_time` — when the observed fact applies;
- `available_at` — the earliest time Model Buk may legally treat the fact as
  known for a prediction cutoff;
- `ingested_at` — when this installation fetched that exact version;
- `provider` and `provider_version` — source identity and immutable version.

External feeds or research files may submit `known_at` as an alias of
`available_at` and `fetched_at` as an alias of `ingested_at`. If both the alias
and canonical column are present, they must agree exactly after UTC parsing.
Rows with post-cutoff `available_at` remain unavailable rather than being
treated as neutral context.

## Match-context availability

Every requested current-context read records a `context_availability` snapshot
for the fixture. Each feature group has exactly one of these states:

- `available` — the provider returned at least one observation;
- `observed_empty` — the provider request succeeded and explicitly returned no
  observations;
- `missing` — the expected field was absent or null;
- `provider_error` — the provider reported that endpoint as failed;
- `not_requested` — the run policy deliberately skipped that feature group.

Only `available` supplies feature values. In particular, `observed_empty`,
`missing` and `provider_error` must never be converted into a neutral numeric
value. Pipeline manifests contain only aggregated status counts, not raw
provider payloads or error messages.

## Versioned match-context features

`model_buk.context_features.build_match_context_features` creates one
point-in-time row per fixture under schema `match-context-features-v1`.
Fresh-form observations are intentionally excluded so later ablations can
compare form and match context independently.

The first schema exposes counts for absences and explicit suspension markers,
lineup completeness, referee presence, rest days and away travel distance.
Every context family also has one-hot availability masks for all states above.
Unknown numeric values remain `NaN`; zero is emitted only when the provider
returned usable data from which a real zero can be derived. Player-strength,
formation and referee effects are not applied to probabilities until they have
passed chronological out-of-sample evaluation.

## Prospective snapshot archive

Live paper scans store normalized snapshot records in immutable JSONL segments
under schema `snapshot-archive-v1`. A segment manifest binds the `run_id`, UTC
cutoff, source adapter version, record count and payload checksum. Existing
segments are never overwritten. A retry reuses a verified segment before
calling the provider again; a conflicting cutoff, source version or checksum
fails closed. Dry runs do not write archive segments.

`export-context-features` verifies every segment and produces an unlabeled CSV
under schema `context-feature-dataset-v1`. Rows retain their archive run,
prediction cutoff, source version and source checksum. The companion manifest
sets `contains_outcomes=false` and `promotion_eligible=false`; outcomes may be
joined only after the fixture has finished and must never alter the archived
pre-match feature row.

## Post-match outcome labels

Outcome schema `fixture-outcomes-v1` requires `fixture_id`, `kickoff`,
`completed_at`, `available_at`, `ingested_at`, regular-time home/away goals,
`result_scope=regular_time`, provider and provider version. `known_at` and
`fetched_at` remain accepted timestamp aliases. The contract requires
`kickoff <= completed_at <= available_at <= ingested_at`.

The labeling join additionally enforces `prediction_cutoff < kickoff` and
`prediction_cutoff < outcome.available_at`. Invalid rows are quarantined;
provider disagreement on kickoff or score fails the entire join. Unmatched
fixtures stay reported as unmatched and are not converted into artificial
labels. Labeled exports use schema `labeled-context-dataset-v1` and remain
`promotion_eligible=false` until nested chronological evaluation is completed.
