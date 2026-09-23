"""Load and validate point-in-time rows for historical opportunity replay."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from model_buk.evaluation.historical_replay import HistoricalOpportunity
from model_buk.opportunity_selector import OpportunityCandidate


REQUIRED_COLUMNS = frozenset(
    {
        "cutoff",
        "kickoff",
        "fixture_id",
        "market_key",
        "selection",
        "decimal_odds",
        "probability",
        "probability_lower_bound",
        "observed_at",
        "snapshot_id",
        "bookmaker",
        "tax_multiplier",
        "execution_cost",
        "correlation_group",
        "settlement",
        "market_group_id",
        "market_size",
    }
)


@dataclass(frozen=True, slots=True)
class ReplayDataIssue:
    row_number: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReplayDataLoadResult:
    source_path: str
    source_sha256: str
    format: str
    rows_read: int
    opportunities: tuple[HistoricalOpportunity, ...]
    quarantine: tuple[ReplayDataIssue, ...]
    source_assumptions: tuple[str, ...] = ()

    @property
    def rows_accepted(self) -> int:
        return len(self.opportunities)

    def manifest(self) -> Mapping[str, Any]:
        """Return stable, serializable provenance and data-quality counts."""
        return MappingProxyType(
            {
                "source_path": self.source_path,
                "source_sha256": self.source_sha256,
                "format": self.format,
                "rows_read": self.rows_read,
                "rows_accepted": self.rows_accepted,
                "rows_quarantined": len(self.quarantine),
                "source_assumptions": list(self.source_assumptions),
                "quarantine_codes": dict(
                    sorted(
                        (code, sum(issue.code == code for issue in self.quarantine))
                        for code in {issue.code for issue in self.quarantine}
                    )
                ),
            }
        )


def _timestamp(row: Mapping[str, Any], name: str) -> datetime:
    value = str(row.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return timestamp


def _text(row: Mapping[str, Any], name: str, *, optional: bool = False) -> str | None:
    value = row.get(name)
    if optional and (value is None or str(value).strip() == ""):
        return None
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _number(row: Mapping[str, Any], name: str, *, optional: bool = False) -> float | None:
    value = row.get(name)
    if optional and (value is None or str(value).strip() == ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _opportunity(row: Mapping[str, Any]) -> HistoricalOpportunity:
    candidate = OpportunityCandidate(
        fixture_id=_text(row, "fixture_id"),  # type: ignore[arg-type]
        market_key=_text(row, "market_key"),  # type: ignore[arg-type]
        selection=_text(row, "selection"),  # type: ignore[arg-type]
        decimal_odds=_number(row, "decimal_odds"),  # type: ignore[arg-type]
        probability=_number(row, "probability"),  # type: ignore[arg-type]
        probability_lower_bound=_number(row, "probability_lower_bound"),  # type: ignore[arg-type]
        observed_at=_timestamp(row, "observed_at"),
        snapshot_id=_text(row, "snapshot_id"),  # type: ignore[arg-type]
        bookmaker=_text(row, "bookmaker"),  # type: ignore[arg-type]
        tax_multiplier=_number(row, "tax_multiplier"),  # type: ignore[arg-type]
        execution_cost=_number(row, "execution_cost"),  # type: ignore[arg-type]
        correlation_group=_text(row, "correlation_group", optional=True),
    )
    market_size_value = _number(row, "market_size")
    assert market_size_value is not None
    if not market_size_value.is_integer():
        raise ValueError("market_size must be an integer")
    return HistoricalOpportunity(
        cutoff=_timestamp(row, "cutoff"),
        kickoff=_timestamp(row, "kickoff"),
        candidate=candidate,
        settlement=_text(row, "settlement"),  # type: ignore[arg-type]
        market_group_id=_text(row, "market_group_id"),  # type: ignore[arg-type]
        market_size=int(market_size_value),
        execution_price_kind=_text(row, "execution_price_kind", optional=True) or "snapshot",  # type: ignore[arg-type]
        closing_odds=_number(row, "closing_odds", optional=True),
        cluster_id=_text(row, "cluster_id", optional=True),
        stratum_id=_text(row, "stratum_id", optional=True),
    )


def _records(path: Path, content: bytes) -> tuple[str, list[Mapping[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        headers = set(reader.fieldnames or ())
        missing = sorted(REQUIRED_COLUMNS - headers)
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        return "csv", list(reader)
    if suffix in {".jsonl", ".ndjson"}:
        records: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                records.append({"__row_error__": f"invalid JSON: {exc.msg}", "__line__": line_number})
                continue
            if not isinstance(value, dict):
                records.append({"__row_error__": "JSONL row must be an object", "__line__": line_number})
                continue
            missing = sorted(REQUIRED_COLUMNS - value.keys())
            if missing:
                value = {**value, "__row_error__": f"missing required columns: {', '.join(missing)}"}
            records.append(value)
        return "jsonl", records
    raise ValueError("replay input must be a .csv, .jsonl or .ndjson file")


def load_replay_data(path: str | Path) -> ReplayDataLoadResult:
    """Load one row per executable outcome and quarantine invalid rows.

    Structural errors (unsupported format or missing CSV columns) reject the
    source. Row-level errors are retained in ``quarantine`` with a source row
    number so a corrected dataset can be audited and reloaded.
    """
    source = Path(path)
    content = source.read_bytes()
    file_format, records = _records(source, content)
    opportunities: list[HistoricalOpportunity] = []
    quarantine: list[ReplayDataIssue] = []
    assumptions = sorted(
        {
            str(row["source_assumption"]).strip()
            for row in records
            if row.get("source_assumption") is not None and str(row["source_assumption"]).strip()
        }
    )
    for row_number, row in enumerate(records, start=2 if file_format == "csv" else 1):
        displayed_number = int(row.get("__line__", row_number))
        if row.get("__row_error__"):
            quarantine.append(ReplayDataIssue(displayed_number, "invalid_row", str(row["__row_error__"])))
            continue
        try:
            opportunities.append(_opportunity(row))
        except (TypeError, ValueError, OverflowError) as exc:
            quarantine.append(ReplayDataIssue(displayed_number, "invalid_row", str(exc)))
    return ReplayDataLoadResult(
        source_path=str(source.resolve()),
        source_sha256=hashlib.sha256(content).hexdigest(),
        format=file_format,
        rows_read=len(records),
        opportunities=tuple(opportunities),
        quarantine=tuple(quarantine),
        source_assumptions=tuple(assumptions),
    )


__all__ = ["ReplayDataIssue", "ReplayDataLoadResult", "load_replay_data"]
