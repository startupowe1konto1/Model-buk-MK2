"""Join frozen pre-match context features to post-match 1X2 labels."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from model_buk.context_dataset import CONTEXT_DATASET_SCHEMA_VERSION
from model_buk.context_features import CONTEXT_FEATURE_SCHEMA_VERSION


OUTCOME_CONTRACT_VERSION = "fixture-outcomes-v1"
LABELED_CONTEXT_DATASET_VERSION = "labeled-context-dataset-v1"
OUTCOME_COLUMNS = (
    "fixture_id",
    "kickoff",
    "completed_at",
    "available_at",
    "ingested_at",
    "home_goals",
    "away_goals",
    "result_scope",
    "provider",
    "provider_version",
    "source_record_id",
    "actual",
)
OUTCOME_REQUIRED_COLUMNS = frozenset(set(OUTCOME_COLUMNS) - {"source_record_id", "actual"})
TIME_ALIASES = {"known_at": "available_at", "fetched_at": "ingested_at"}


@dataclass(frozen=True, slots=True)
class OutcomeIssue:
    row_number: int
    message: str


@dataclass(frozen=True, slots=True)
class OutcomeLoadResult:
    frame: pd.DataFrame
    source_sha256: str
    rows_read: int
    quarantine: tuple[OutcomeIssue, ...]


def _read_records(path: Path, content: bytes) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        text = content.decode("utf-8-sig")
        return pd.read_csv(io.StringIO(text), dtype="object").to_dict(orient="records")
    if suffix in {".jsonl", ".ndjson"}:
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                records.append({"__error__": f"invalid JSON: {exc.msg}", "__line__": line_number})
                continue
            if not isinstance(value, dict):
                value = {"__error__": "JSONL row must be an object", "__line__": line_number}
            records.append(value)
        return records
    raise ValueError("outcomes input must be a .csv, .jsonl or .ndjson file")


def _timestamp(value: Any, *, field: str) -> pd.Timestamp:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        raise ValueError(f"{field} must be a valid timezone-aware timestamp")
    raw = pd.Timestamp(value)
    if raw.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return pd.Timestamp(timestamp)


def _non_blank(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        raise ValueError(f"{field} must not be blank")
    return text


def _goals(value: Any, *, field: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a non-negative integer") from None
    if not number.is_integer() or number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return int(number)


def _normalize_outcome(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for alias, canonical in TIME_ALIASES.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
        elif canonical in normalized and alias in normalized:
            canonical_time = _timestamp(normalized[canonical], field=canonical)
            alias_time = _timestamp(normalized[alias], field=alias)
            if canonical_time != alias_time:
                raise ValueError(f"{canonical} conflicts with {alias}")
    missing = sorted(OUTCOME_REQUIRED_COLUMNS - normalized.keys())
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    fixture_id = _non_blank(normalized["fixture_id"], field="fixture_id")
    kickoff = _timestamp(normalized["kickoff"], field="kickoff")
    completed_at = _timestamp(normalized["completed_at"], field="completed_at")
    available_at = _timestamp(normalized["available_at"], field="available_at")
    ingested_at = _timestamp(normalized["ingested_at"], field="ingested_at")
    if completed_at < kickoff:
        raise ValueError("completed_at cannot be earlier than kickoff")
    if available_at < completed_at:
        raise ValueError("available_at cannot be earlier than completed_at")
    if ingested_at < available_at:
        raise ValueError("ingested_at cannot be earlier than available_at")
    scope = _non_blank(normalized["result_scope"], field="result_scope")
    if scope != "regular_time":
        raise ValueError("result_scope must be regular_time")
    home_goals = _goals(normalized["home_goals"], field="home_goals")
    away_goals = _goals(normalized["away_goals"], field="away_goals")
    actual = "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away"
    source_record_id = normalized.get("source_record_id")
    return {
        "fixture_id": fixture_id,
        "kickoff": kickoff,
        "completed_at": completed_at,
        "available_at": available_at,
        "ingested_at": ingested_at,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "result_scope": scope,
        "provider": _non_blank(normalized["provider"], field="provider"),
        "provider_version": _non_blank(
            normalized["provider_version"], field="provider_version"
        ),
        "source_record_id": (
            None if source_record_id is None or str(source_record_id).strip() == ""
            else str(source_record_id).strip()
        ),
        "actual": actual,
    }


def load_fixture_outcomes(path: str | Path) -> OutcomeLoadResult:
    source = Path(path)
    content = source.read_bytes()
    records = _read_records(source, content)
    accepted: list[dict[str, Any]] = []
    quarantine: list[OutcomeIssue] = []
    offset = 2 if source.suffix.lower() == ".csv" else 1
    for index, row in enumerate(records, start=offset):
        row_number = int(row.get("__line__", index))
        try:
            if row.get("__error__"):
                raise ValueError(str(row["__error__"]))
            accepted.append(_normalize_outcome(row))
        except (TypeError, ValueError, OverflowError) as exc:
            quarantine.append(OutcomeIssue(row_number, str(exc)))
    return OutcomeLoadResult(
        frame=pd.DataFrame(accepted, columns=OUTCOME_COLUMNS),
        source_sha256=sha256(content).hexdigest(),
        rows_read=len(records),
        quarantine=tuple(quarantine),
    )


def _resolved_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    latest = (
        frame.sort_values(
            ["fixture_id", "provider", "available_at", "ingested_at", "provider_version"],
            kind="stable",
        )
        .groupby(["fixture_id", "provider"], as_index=False, sort=False)
        .tail(1)
    )
    resolved: list[pd.Series] = []
    comparison = ["kickoff", "home_goals", "away_goals", "result_scope"]
    for fixture_id, rows in latest.groupby("fixture_id", sort=True):
        if any(rows[column].nunique(dropna=False) != 1 for column in comparison):
            raise ValueError(f"conflicting outcome providers for fixture {fixture_id}")
        resolved.append(
            rows.sort_values(
                ["available_at", "ingested_at", "provider"], kind="stable"
            ).iloc[-1]
        )
    return pd.DataFrame(resolved, columns=OUTCOME_COLUMNS).reset_index(drop=True)


def join_context_features_with_outcomes(
    features: pd.DataFrame,
    outcomes: OutcomeLoadResult,
    *,
    allow_quarantine: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if outcomes.quarantine and not allow_quarantine:
        raise ValueError(f"outcomes contain {len(outcomes.quarantine)} quarantined row(s)")
    required_features = {
        "archive_run_id",
        "archive_records_sha256",
        "archive_source_id",
        "fixture_id",
        "prediction_cutoff",
        "feature_schema_version",
    }
    missing = sorted(required_features - set(features.columns))
    if missing:
        raise ValueError(f"context features are missing columns: {', '.join(missing)}")
    prepared = features.copy()
    prepared["fixture_id"] = prepared["fixture_id"].astype(str)
    if prepared.duplicated(["archive_run_id", "fixture_id"]).any():
        raise ValueError("context features contain duplicate run_id and fixture_id rows")
    if not prepared["feature_schema_version"].eq(CONTEXT_FEATURE_SCHEMA_VERSION).all():
        raise ValueError("context features contain an unsupported feature schema")
    prepared["prediction_cutoff"] = pd.to_datetime(
        prepared["prediction_cutoff"], errors="coerce", utc=True
    )
    if prepared["prediction_cutoff"].isna().any():
        raise ValueError("context features contain invalid prediction_cutoff values")

    resolved = _resolved_outcomes(outcomes.frame)
    outcome_columns = [
        "fixture_id",
        "kickoff",
        "completed_at",
        "available_at",
        "home_goals",
        "away_goals",
        "result_scope",
        "provider",
        "provider_version",
        "actual",
    ]
    merged = prepared.merge(
        resolved[outcome_columns],
        on="fixture_id",
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    matched = merged[merged["_merge"].eq("both")].copy()
    if not matched.empty:
        if (matched["prediction_cutoff"] >= matched["kickoff"]).any():
            raise ValueError("context feature cutoff must be earlier than outcome kickoff")
        if (matched["available_at"] <= matched["prediction_cutoff"]).any():
            raise ValueError("outcome was available at or before the prediction cutoff")
    matched = matched.drop(columns="_merge").rename(columns={
        "available_at": "outcome_available_at",
        "provider": "outcome_provider",
        "provider_version": "outcome_provider_version",
    })
    manifest = {
        "schema_version": LABELED_CONTEXT_DATASET_VERSION,
        "outcome_contract_version": OUTCOME_CONTRACT_VERSION,
        "feature_schema_version": CONTEXT_FEATURE_SCHEMA_VERSION,
        "feature_rows": len(prepared),
        "matched_rows": len(matched),
        "unmatched_rows": int((merged["_merge"] == "left_only").sum()),
        "outcome_rows_read": outcomes.rows_read,
        "outcome_rows_accepted": len(outcomes.frame),
        "outcome_rows_quarantined": len(outcomes.quarantine),
        "target": "result_1x2_regular_time",
        "contains_outcomes": True,
        "promotion_eligible": False,
    }
    return matched.reset_index(drop=True), manifest


def _load_context_features(path: Path) -> tuple[pd.DataFrame, dict[str, Any], str]:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    content = path.read_bytes()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("context feature manifest is missing or invalid") from exc
    if manifest.get("schema_version") != CONTEXT_DATASET_SCHEMA_VERSION:
        raise ValueError("context feature dataset schema is unsupported")
    digest = sha256(content).hexdigest()
    if manifest.get("output_sha256") != digest:
        raise ValueError("context feature dataset checksum mismatch")
    return (
        pd.read_csv(io.BytesIO(content), dtype={"fixture_id": "string"}),
        manifest,
        digest,
    )


def prepare_labeled_context_dataset(
    feature_path: str | Path,
    outcome_path: str | Path,
    output_path: str | Path,
    *,
    allow_outcome_quarantine: bool = False,
) -> dict[str, Any]:
    features_path = Path(feature_path)
    outcomes_path = Path(outcome_path)
    output = Path(output_path)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    if output.exists() or manifest_path.exists():
        raise FileExistsError(f"output already exists: {output}")
    features, feature_manifest, feature_hash = _load_context_features(features_path)
    outcomes = load_fixture_outcomes(outcomes_path)
    labeled, join_manifest = join_context_features_with_outcomes(
        features,
        outcomes,
        allow_quarantine=allow_outcome_quarantine,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        labeled.to_csv(temporary, index=False)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    completed = {
        **join_manifest,
        "feature_dataset_sha256": feature_hash,
        "feature_source_archive_sha256": feature_manifest["source_archive_sha256"],
        "outcome_source_sha256": outcomes.source_sha256,
        "output_file": output.name,
        "output_sha256": sha256(output.read_bytes()).hexdigest(),
    }
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
    try:
        temporary_manifest.write_text(
            json.dumps(completed, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, manifest_path)
    finally:
        temporary_manifest.unlink(missing_ok=True)
    return completed


__all__ = [
    "LABELED_CONTEXT_DATASET_VERSION",
    "OUTCOME_CONTRACT_VERSION",
    "OutcomeIssue",
    "OutcomeLoadResult",
    "join_context_features_with_outcomes",
    "load_fixture_outcomes",
    "prepare_labeled_context_dataset",
]
