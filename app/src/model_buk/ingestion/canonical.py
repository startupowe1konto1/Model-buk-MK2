"""Versioned, fail-closed contract for historical match and odds imports.

Provider adapters should convert their raw rows to these records before any
feature generation.  A validation result always accounts for every input row:
rows with one or more issues are returned in ``quarantine_rows`` rather than
being dropped silently.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Iterable, Mapping


CANONICAL_SCHEMA_VERSION = "1.0"
"""The only schema version accepted by this implementation."""

_MATCH_REQUIRED_FIELDS = (
    "schema_version",
    "source",
    "provider",
    "source_record_id",
    "match_id",
    "competition_id",
    "kickoff",
    "known_at",
    "fetched_at",
    "home_team_id",
    "away_team_id",
    "status",
)
_ODDS_REQUIRED_FIELDS = (
    "schema_version",
    "source",
    "provider",
    "source_record_id",
    "match_id",
    "bookmaker",
    "market",
    "selection",
    "odds",
    "captured_at",
    "available_at",
    "fetched_at",
)
_FINISHED_STATUSES = frozenset({"finished", "ft", "final"})
_NO_RESULT_STATUSES = frozenset({"scheduled", "ns", "postponed", "cancelled"})
_KNOWN_STATUSES = _FINISHED_STATUSES | _NO_RESULT_STATUSES | frozenset(
    {"in_play", "live", "ht", "abandoned"}
)
_STAT_SUFFIXES = frozenset(
    {
        "score",
        "goals",
        "shots",
        "shots_on_target",
        "sot",
        "corners",
        "yellow_cards",
        "red_cards",
        "fouls",
        "offsides",
    }
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One concrete contract violation, tied to the original input row."""

    record_type: str
    row_index: int
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Immutable result of an import validation pass.

    ``valid_rows`` and ``quarantine_rows`` are immutable mapping views so a
    caller cannot accidentally alter what was validated after the fact.
    """

    valid_rows: tuple[Mapping[str, Any], ...]
    quarantine_rows: tuple[Mapping[str, Any], ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def valid_match_ids(self) -> frozenset[str]:
        """The validated match identifiers, useful as odds foreign keys."""

        return frozenset(
            str(row["match_id"])
            for row in self.valid_rows
            if row.get("match_id") is not None
        )


def _required_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_utc(value: Any) -> datetime:
    """Parse a timezone-aware timestamp and normalize it to UTC.

    Naive dates/times are deliberately rejected: assuming a provider timezone
    is a common source of historical leakage and duplicate fixtures.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        parsed = datetime.fromisoformat(candidate)
    else:
        # Supports pandas.Timestamp without making pandas a runtime dependency.
        converter = getattr(value, "to_pydatetime", None)
        if converter is None:
            raise TypeError("timestamp must be an ISO-8601 string or datetime")
        parsed = converter()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _utc_identity_timestamp(value: Any) -> str:
    return _to_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_match_id(
    competition_id: Any,
    kickoff: Any,
    home_team_id: Any,
    away_team_id: Any,
) -> str:
    """Build a stable ID from provider-independent fixture identity fields.

    The hash input contains only the competition ID, UTC kickoff and canonical
    team entity IDs.  It intentionally excludes filenames, row positions and
    any local path so importing the same data elsewhere yields the same ID.
    """

    values = tuple(
        _required_text(value)
        for value in (competition_id, home_team_id, away_team_id)
    )
    if any(value is None for value in values):
        raise ValueError("competition_id and team IDs must be non-blank")
    competition, home, away = values
    payload = {
        "competition_id": competition,
        "home_team_id": home,
        "away_team_id": away,
        "kickoff_utc": _utc_identity_timestamp(kickoff),
        "version": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"match_v1_{sha256(canonical.encode('utf-8')).hexdigest()}"


def _records(rows: Iterable[Mapping[str, Any]] | Any) -> tuple[Mapping[str, Any], ...]:
    """Accept iterable mappings and pandas DataFrames without retaining mutability."""

    to_dict = getattr(rows, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
        except TypeError:
            pass
    result: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("each canonical row must be a mapping")
        result.append(MappingProxyType(dict(row)))
    return tuple(result)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = value != value
    except Exception:  # pragma: no cover - defensive for unusual provider values
        return False
    return isinstance(missing, bool) and missing


def _valid_non_negative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    return math.isfinite(float(value)) and value >= 0


def _add_issue(
    issues_by_row: dict[int, list[ValidationIssue]],
    *,
    record_type: str,
    row_index: int,
    code: str,
    message: str,
    field: str | None = None,
) -> None:
    issues_by_row[row_index].append(
        ValidationIssue(record_type, row_index, code, message, field)
    )


def _check_required(
    row: Mapping[str, Any],
    fields: tuple[str, ...],
    row_index: int,
    record_type: str,
    issues_by_row: dict[int, list[ValidationIssue]],
) -> None:
    for field in fields:
        value = row.get(field)
        if _is_missing(value) or (isinstance(value, str) and not value.strip()):
            _add_issue(
                issues_by_row,
                record_type=record_type,
                row_index=row_index,
                code="missing_required_field",
                message=f"{field} is required",
                field=field,
            )


def _check_schema(
    row: Mapping[str, Any], row_index: int, record_type: str, issues_by_row: dict[int, list[ValidationIssue]]
) -> None:
    if _required_text(row.get("schema_version")) not in {None, CANONICAL_SCHEMA_VERSION}:
        _add_issue(
            issues_by_row,
            record_type=record_type,
            row_index=row_index,
            code="unsupported_schema_version",
            message=f"schema_version must be {CANONICAL_SCHEMA_VERSION}",
            field="schema_version",
        )


def _check_timestamp(
    row: Mapping[str, Any],
    field: str,
    row_index: int,
    record_type: str,
    issues_by_row: dict[int, list[ValidationIssue]],
) -> datetime | None:
    if _is_missing(row.get(field)):
        return None
    try:
        return _to_utc(row[field])
    except (TypeError, ValueError, OverflowError):
        _add_issue(
            issues_by_row,
            record_type=record_type,
            row_index=row_index,
            code="invalid_timestamp",
            message=f"{field} must be timezone-aware and UTC-convertible",
            field=field,
        )
        return None


def _check_time_order(
    earlier: datetime | None,
    later: datetime | None,
    *,
    earlier_field: str,
    later_field: str,
    row_index: int,
    record_type: str,
    issues_by_row: dict[int, list[ValidationIssue]],
) -> None:
    if earlier is not None and later is not None and earlier > later:
        _add_issue(
            issues_by_row,
            record_type=record_type,
            row_index=row_index,
            code="invalid_timestamp_order",
            message=f"{earlier_field} must not be later than {later_field}",
            field=earlier_field,
        )


def _check_unique(
    rows: tuple[Mapping[str, Any], ...],
    field: str,
    record_type: str,
    issues_by_row: dict[int, list[ValidationIssue]],
) -> None:
    locations: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        value = _required_text(row.get(field))
        if value is not None:
            locations[value].append(index)
    for value, indexes in locations.items():
        if len(indexes) > 1:
            for index in indexes:
                _add_issue(
                    issues_by_row,
                    record_type=record_type,
                    row_index=index,
                    code="duplicate_value",
                    message=f"{field} must be unique; duplicate value {value!r}",
                    field=field,
                )


def _finish_report(
    rows: tuple[Mapping[str, Any], ...], issues_by_row: dict[int, list[ValidationIssue]]
) -> ValidationReport:
    valid = tuple(row for index, row in enumerate(rows) if index not in issues_by_row)
    quarantine = tuple(row for index, row in enumerate(rows) if index in issues_by_row)
    issues = tuple(
        issue
        for index in range(len(rows))
        for issue in issues_by_row.get(index, ())
    )
    return ValidationReport(valid, quarantine, issues)


def validate_canonical_matches(matches: Iterable[Mapping[str, Any]] | Any) -> ValidationReport:
    """Validate canonical fixtures and return valid plus quarantined rows.

    Match IDs must equal :func:`canonical_match_id` for the supplied fixture
    identity.  Finished matches need a complete non-negative result; scheduled,
    postponed and cancelled matches may not already carry a result.
    """

    rows = _records(matches)
    issues_by_row: dict[int, list[ValidationIssue]] = defaultdict(list)
    for index, row in enumerate(rows):
        _check_required(row, _MATCH_REQUIRED_FIELDS, index, "match", issues_by_row)
        _check_schema(row, index, "match", issues_by_row)
        timestamps = {
            field: _check_timestamp(row, field, index, "match", issues_by_row)
            for field in ("kickoff", "known_at", "fetched_at")
        }
        _check_time_order(
            timestamps["known_at"],
            timestamps["fetched_at"],
            earlier_field="known_at",
            later_field="fetched_at",
            row_index=index,
            record_type="match",
            issues_by_row=issues_by_row,
        )

        home = _required_text(row.get("home_team_id"))
        away = _required_text(row.get("away_team_id"))
        if home is not None and away is not None and home == away:
            _add_issue(
                issues_by_row,
                record_type="match",
                row_index=index,
                code="same_team_identity",
                message="home_team_id and away_team_id must differ",
                field="away_team_id",
            )

        identity_fields = ("competition_id", "kickoff", "home_team_id", "away_team_id")
        if all(_required_text(row.get(field)) is not None for field in identity_fields):
            try:
                expected_id = canonical_match_id(
                    row["competition_id"], row["kickoff"], row["home_team_id"], row["away_team_id"]
                )
            except (TypeError, ValueError, OverflowError):
                expected_id = None
            if expected_id is not None and row.get("match_id") != expected_id:
                _add_issue(
                    issues_by_row,
                    record_type="match",
                    row_index=index,
                    code="noncanonical_match_id",
                    message="match_id does not match canonical fixture identity",
                    field="match_id",
                )

        score_fields = (("home_score", "away_score"), ("home_goals", "away_goals"))
        scores_present = False
        scores_complete = False
        for home_score, away_score in score_fields:
            values = (row.get(home_score), row.get(away_score))
            present = tuple(not _is_missing(value) for value in values)
            if any(present):
                scores_present = True
                if not all(present):
                    _add_issue(
                        issues_by_row,
                        record_type="match",
                        row_index=index,
                        code="incomplete_result",
                        message="home and away result values must appear together",
                        field=home_score if not present[0] else away_score,
                    )
                else:
                    scores_complete = True
                    for field, value in zip((home_score, away_score), values):
                        if not _valid_non_negative(value):
                            _add_issue(
                                issues_by_row,
                                record_type="match",
                                row_index=index,
                                code="invalid_statistic",
                                message=f"{field} must be a finite non-negative number",
                                field=field,
                            )

        for field, value in row.items():
            stat_name = (
                field.removeprefix("home_")
                if field.startswith("home_")
                else field.removeprefix("away_")
                if field.startswith("away_")
                else None
            )
            if stat_name in _STAT_SUFFIXES:
                if not _is_missing(value) and not _valid_non_negative(value):
                    _add_issue(
                        issues_by_row,
                        record_type="match",
                        row_index=index,
                        code="invalid_statistic",
                        message=f"{field} must be a finite non-negative number",
                        field=field,
                    )
        for container in ("stats", "statistics"):
            nested = row.get(container)
            if nested is not None and not isinstance(nested, Mapping):
                _add_issue(
                    issues_by_row,
                    record_type="match",
                    row_index=index,
                    code="invalid_statistics_container",
                    message=f"{container} must be a mapping when supplied",
                    field=container,
                )
            elif isinstance(nested, Mapping):
                for key, value in nested.items():
                    if not _is_missing(value) and not _valid_non_negative(value):
                        _add_issue(
                            issues_by_row,
                            record_type="match",
                            row_index=index,
                            code="invalid_statistic",
                            message=f"{container}.{key} must be a finite non-negative number",
                            field=f"{container}.{key}",
                        )

        status = _required_text(row.get("status"))
        status_normalized = status.lower() if status else None
        if status_normalized is not None and status_normalized not in _KNOWN_STATUSES:
            _add_issue(
                issues_by_row,
                record_type="match",
                row_index=index,
                code="unknown_status",
                message="status is not one of the supported canonical statuses",
                field="status",
            )
        elif status_normalized in _FINISHED_STATUSES and not scores_complete:
            _add_issue(
                issues_by_row,
                record_type="match",
                row_index=index,
                code="missing_finished_result",
                message="finished matches require both home and away result values",
                field="status",
            )
        elif status_normalized in _NO_RESULT_STATUSES and scores_present:
            _add_issue(
                issues_by_row,
                record_type="match",
                row_index=index,
                code="result_for_nonfinal_match",
                message="scheduled, postponed and cancelled matches cannot contain a result",
                field="status",
            )

    _check_unique(rows, "source_record_id", "match", issues_by_row)
    _check_unique(rows, "match_id", "match", issues_by_row)
    return _finish_report(rows, issues_by_row)


def validate_odds_snapshots(
    odds_snapshots: Iterable[Mapping[str, Any]] | Any,
    *,
    known_match_ids: Iterable[str],
    cutoff: Any | None = None,
) -> ValidationReport:
    """Validate odds rows against validated matches and an optional as-of cutoff.

    ``available_at`` later than ``cutoff`` is quarantined as leakage.  Callers
    should pass ``validate_canonical_matches(...).valid_match_ids`` so an odds
    quote cannot refer to a rejected fixture.
    """

    rows = _records(odds_snapshots)
    match_ids = frozenset(str(value) for value in known_match_ids)
    cutoff_utc = _to_utc(cutoff) if cutoff is not None else None
    issues_by_row: dict[int, list[ValidationIssue]] = defaultdict(list)
    for index, row in enumerate(rows):
        _check_required(row, _ODDS_REQUIRED_FIELDS, index, "odds", issues_by_row)
        _check_schema(row, index, "odds", issues_by_row)
        timestamps = {
            field: _check_timestamp(row, field, index, "odds", issues_by_row)
            for field in ("captured_at", "available_at", "fetched_at")
        }
        _check_time_order(
            timestamps["captured_at"],
            timestamps["available_at"],
            earlier_field="captured_at",
            later_field="available_at",
            row_index=index,
            record_type="odds",
            issues_by_row=issues_by_row,
        )
        _check_time_order(
            timestamps["available_at"],
            timestamps["fetched_at"],
            earlier_field="available_at",
            later_field="fetched_at",
            row_index=index,
            record_type="odds",
            issues_by_row=issues_by_row,
        )
        match_id = _required_text(row.get("match_id"))
        if match_id is not None and match_id not in match_ids:
            _add_issue(
                issues_by_row,
                record_type="odds",
                row_index=index,
                code="unknown_match_id",
                message="match_id is not present in the validated match set",
                field="match_id",
            )
        odds = row.get("odds")
        if not isinstance(odds, Real) or isinstance(odds, bool) or not math.isfinite(float(odds)) or odds <= 1:
            _add_issue(
                issues_by_row,
                record_type="odds",
                row_index=index,
                code="invalid_odds",
                message="odds must be a finite decimal value greater than 1",
                field="odds",
            )
        available_at = timestamps["available_at"]
        if cutoff_utc is not None and available_at is not None and available_at > cutoff_utc:
            _add_issue(
                issues_by_row,
                record_type="odds",
                row_index=index,
                code="information_leakage",
                message="available_at is later than the supplied as-of cutoff",
                field="available_at",
            )

    _check_unique(rows, "source_record_id", "odds", issues_by_row)
    return _finish_report(rows, issues_by_row)


__all__ = [
    "CANONICAL_SCHEMA_VERSION",
    "ValidationIssue",
    "ValidationReport",
    "canonical_match_id",
    "validate_canonical_matches",
    "validate_odds_snapshots",
]
