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
6. any live provider integration must eventually add `known_at`, `fetched_at` and provider IDs before production use.
