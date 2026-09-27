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
