"""Adapters that turn provider-specific data into internal canonical tables."""

from .football_data_uk import (
    FootballDataUKBatch,
    load_football_data_uk_archives,
    normalize_football_data_uk,
    read_football_data_uk_csv,
)
from .enrichment import enrich_with_understat
from .canonical import (
    CANONICAL_SCHEMA_VERSION,
    ValidationIssue,
    ValidationReport,
    canonical_match_id,
    validate_canonical_matches,
    validate_odds_snapshots,
)
from .api_football import (
    ApiFootballSnapshotAdapter,
    ApiFootballSnapshotError,
    ApiFootballSnapshotPolicy,
)
from .understat import (
    SCHEDULE_COLUMNS,
    STATS_COLUMNS,
    UnderstatBatch,
    load_understat_archives,
    normalize_understat_schedule,
    normalize_understat_stats,
    read_understat_csv,
)

__all__ = [
    "FootballDataUKBatch",
    "load_football_data_uk_archives",
    "normalize_football_data_uk",
    "read_football_data_uk_csv",
    "enrich_with_understat",
    "CANONICAL_SCHEMA_VERSION",
    "ValidationIssue",
    "ValidationReport",
    "canonical_match_id",
    "validate_canonical_matches",
    "validate_odds_snapshots",
    "ApiFootballSnapshotAdapter",
    "ApiFootballSnapshotError",
    "ApiFootballSnapshotPolicy",
    "SCHEDULE_COLUMNS",
    "STATS_COLUMNS",
    "UnderstatBatch",
    "load_understat_archives",
    "normalize_understat_schedule",
    "normalize_understat_stats",
    "read_understat_csv",
]
