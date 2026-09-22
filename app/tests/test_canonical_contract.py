from __future__ import annotations

from datetime import datetime, timezone

import pytest

from model_buk.ingestion import (
    CANONICAL_SCHEMA_VERSION,
    ValidationIssue,
    canonical_match_id,
    validate_canonical_matches,
    validate_odds_snapshots,
)


def _match(**overrides):
    kickoff = "2024-08-10T18:00:00+02:00"
    row = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "source": "historical-import",
        "provider": "provider-x",
        "source_record_id": "provider-x:fixture:123",
        "competition_id": "eng.1",
        "kickoff": kickoff,
        "known_at": "2024-08-01T12:00:00Z",
        "fetched_at": "2024-08-01T12:05:00Z",
        "home_team_id": "team:home",
        "away_team_id": "team:away",
        "status": "scheduled",
    }
    row["match_id"] = canonical_match_id(
        row["competition_id"], row["kickoff"], row["home_team_id"], row["away_team_id"]
    )
    row.update(overrides)
    return row


def _odds(match_id: str, **overrides):
    row = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "source": "historical-import",
        "provider": "provider-x",
        "source_record_id": "provider-x:odds:1",
        "match_id": match_id,
        "bookmaker": "book-a",
        "market": "1x2",
        "selection": "home",
        "odds": 2.25,
        "captured_at": "2024-08-01T12:01:00Z",
        "available_at": "2024-08-01T12:02:00Z",
        "fetched_at": "2024-08-01T12:05:00Z",
    }
    row.update(overrides)
    return row


def test_match_id_is_deterministic_across_equivalent_timezones_and_paths():
    first = canonical_match_id("eng.1", "2024-08-10T18:00:00+02:00", "team:home", "team:away")
    second = canonical_match_id("eng.1", "2024-08-10T16:00:00Z", "team:home", "team:away")

    assert first == second
    assert first.startswith("match_v1_")
    assert len(first) == len("match_v1_") + 64


def test_naive_kickoff_is_quarantined_not_discarded():
    report = validate_canonical_matches([_match(kickoff="2024-08-10T18:00:00")])

    assert not report.valid_rows
    assert len(report.quarantine_rows) == 1
    assert {issue.code for issue in report.issues} >= {"invalid_timestamp"}


def test_duplicates_and_invalid_statistics_are_quarantined_for_every_row():
    first = _match(status="finished", home_goals=2, away_goals=1)
    second = _match(
        source_record_id=first["source_record_id"],
        status="finished",
        home_goals=-1,
        away_goals=0,
        home_yellow_cards=-1,
    )
    report = validate_canonical_matches([first, second])

    assert len(report.quarantine_rows) == 2
    assert {issue.field for issue in report.issues} >= {
        "source_record_id",
        "match_id",
        "home_goals",
        "home_yellow_cards",
    }
    assert all(isinstance(issue, ValidationIssue) for issue in report.issues)
    with pytest.raises((AttributeError, TypeError)):
        report.issues[0].code = "changed"


def test_valid_match_and_odds_are_retained_and_report_is_immutable():
    match_report = validate_canonical_matches([_match()])
    odds_report = validate_odds_snapshots(
        [_odds(next(iter(match_report.valid_match_ids)))],
        known_match_ids=match_report.valid_match_ids,
    )

    assert match_report.is_valid
    assert odds_report.is_valid
    with pytest.raises(TypeError):
        match_report.valid_rows[0]["source"] = "changed"


def test_odds_foreign_key_and_as_of_leakage_are_quarantined():
    match = _match()
    match_report = validate_canonical_matches([match])
    cutoff = datetime(2024, 8, 1, 12, 3, tzinfo=timezone.utc)
    report = validate_odds_snapshots(
        [
            _odds("missing-match"),
            _odds(match["match_id"], source_record_id="provider-x:odds:2", available_at="2024-08-01T12:04:00Z"),
        ],
        known_match_ids=match_report.valid_match_ids,
        cutoff=cutoff,
    )

    assert not report.valid_rows
    assert len(report.quarantine_rows) == 2
    assert {issue.code for issue in report.issues} >= {"unknown_match_id", "information_leakage"}


def test_finished_result_and_odds_price_rules_fail_closed():
    match = _match(status="finished")
    match_report = validate_canonical_matches([match])
    odds_report = validate_odds_snapshots(
        [_odds(match["match_id"], odds=1.0)], known_match_ids={match["match_id"]}
    )

    assert "missing_finished_result" in {issue.code for issue in match_report.issues}
    assert "invalid_odds" in {issue.code for issue in odds_report.issues}


def test_timestamp_order_is_validated_for_lineage_and_quotes():
    match = _match(known_at="2024-08-01T12:06:00Z", fetched_at="2024-08-01T12:05:00Z")
    match_report = validate_canonical_matches([match])
    odds_report = validate_odds_snapshots(
        [
            _odds(
                match["match_id"],
                captured_at="2024-08-01T12:04:00Z",
                available_at="2024-08-01T12:03:00Z",
            )
        ],
        known_match_ids={match["match_id"]},
    )

    assert "invalid_timestamp_order" in {issue.code for issue in match_report.issues}
    assert "invalid_timestamp_order" in {issue.code for issue in odds_report.issues}
