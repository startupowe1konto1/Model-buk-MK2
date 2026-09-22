"""Repeatable orchestration for a current, point-in-time analysis run.

The module intentionally contains no network client or model loading policy.
Callers inject adapters which return :class:`~model_buk.snapshots.SnapshotRecord`
objects and a predictor which turns the validated as-of frame into opportunity
candidates.  This keeps the orchestration usable in production and completely
offline in tests.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from model_buk.opportunity_selector import (
    OpportunityCandidate,
    OpportunitySelection,
    select_opportunities,
)
from model_buk.snapshots import SnapshotRecord, latest_as_of, snapshots_frame, validate_snapshots


SCHEMA_VERSION = "current-pipeline-v1"


class PipelineDataError(ValueError):
    """Fetched data cannot safely be used for this decision time."""


class RunConflictError(RuntimeError):
    """A run id was already committed for a different request."""


@dataclass(frozen=True)
class RunContext:
    """Stable context supplied to every adapter and the predictor."""

    run_id: str
    cutoff: datetime


class SnapshotAdapter(Protocol):
    def __call__(self, context: RunContext) -> Iterable[SnapshotRecord | Mapping[str, Any]]:
        """Fetch records which the provider says were available by the cutoff."""


class CurrentPredictor(Protocol):
    def __call__(
        self,
        snapshots: pd.DataFrame,
        context: RunContext,
    ) -> Iterable[OpportunityCandidate] | "PredictionBatch":
        """Create priced opportunity candidates from validated snapshots."""


@dataclass(frozen=True)
class PredictionBatch:
    """Optional richer predictor response carrying an explicit model id."""

    candidates: tuple[OpportunityCandidate, ...]
    model_id: str

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be blank")


@dataclass(frozen=True)
class CurrentPipelinePolicy:
    """Completeness, freshness and portfolio gates for one run."""

    required_feature_groups: Mapping[str, int] = field(default_factory=dict)
    max_age_by_feature_group: Mapping[str, timedelta] = field(default_factory=dict)
    minimum_total_records: int = 1
    max_recommendations: int = 10
    min_conservative_ev_net: float = 0.0
    min_conservative_market_edge: float | None = None
    max_per_fixture: int = 2
    max_per_correlation_group: int = 1
    max_quote_age: timedelta | None = None

    def __post_init__(self) -> None:
        if self.minimum_total_records < 0:
            raise ValueError("minimum_total_records must be non-negative")
        if self.max_recommendations < 0:
            raise ValueError("max_recommendations must be non-negative")
        if self.min_conservative_ev_net < 0.0:
            raise ValueError("min_conservative_ev_net must be non-negative")
        if self.min_conservative_market_edge is not None and self.min_conservative_market_edge < 0.0:
            raise ValueError("min_conservative_market_edge must be non-negative or None")
        if self.max_per_fixture < 1:
            raise ValueError("max_per_fixture must be at least 1")
        if self.max_per_correlation_group < 1:
            raise ValueError("max_per_correlation_group must be at least 1")
        for group, count in self.required_feature_groups.items():
            if not str(group).strip() or int(count) < 1:
                raise ValueError("required feature groups need a non-blank name and count >= 1")
        for group, age in self.max_age_by_feature_group.items():
            if not str(group).strip() or age <= timedelta(0):
                raise ValueError("feature-group maximum ages must be positive")
        if self.max_quote_age is not None and self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_feature_groups": {
                str(key): int(value)
                for key, value in sorted(self.required_feature_groups.items())
            },
            "max_age_seconds_by_feature_group": {
                str(key): value.total_seconds()
                for key, value in sorted(self.max_age_by_feature_group.items())
            },
            "minimum_total_records": self.minimum_total_records,
            "max_recommendations": self.max_recommendations,
            "min_conservative_ev_net": self.min_conservative_ev_net,
            "min_conservative_market_edge": self.min_conservative_market_edge,
            "max_per_fixture": self.max_per_fixture,
            "max_per_correlation_group": self.max_per_correlation_group,
            "max_quote_age_seconds": (
                self.max_quote_age.total_seconds() if self.max_quote_age is not None else None
            ),
        }


@dataclass(frozen=True)
class PipelineRun:
    """Public, JSON-compatible output from a current analysis run."""

    manifest: Mapping[str, Any]
    result: Mapping[str, Any]
    reused: bool = False
    persisted: bool = True


def _utc(value: datetime | str | pd.Timestamp, *, field_name: str) -> datetime:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid timestamp") from exc
    if pd.isna(parsed) or parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.tz_convert("UTC").to_pydatetime()


def _iso(value: datetime | pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    """Convert common data values to a stable, strict-JSON representation."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return _iso(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        converted = [_json_value(item) for item in value]
        return sorted(converted, key=_canonical_json) if isinstance(value, (set, frozenset)) else converted
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _component_id(component: Any) -> str:
    explicit = getattr(component, "component_id", None) or getattr(component, "model_id", None)
    if explicit:
        return str(explicit)
    target = component if isinstance(component, type) else type(component)
    if callable(component) and hasattr(component, "__qualname__"):
        return f"{component.__module__}.{component.__qualname__}"
    return f"{target.__module__}.{target.__qualname__}"


def _candidate_dict(candidate: OpportunityCandidate, conservative_ev_net: float) -> dict[str, Any]:
    return {
        "fixture_id": candidate.fixture_id,
        "market_key": candidate.market_key,
        "selection": candidate.selection,
        "bookmaker": candidate.bookmaker,
        "decimal_odds": candidate.decimal_odds,
        "probability": candidate.probability,
        "probability_lower_bound": candidate.probability_lower_bound,
        "observed_at": _iso(candidate.observed_at),
        "snapshot_id": candidate.snapshot_id,
        "tax_multiplier": candidate.tax_multiplier,
        "execution_cost": candidate.execution_cost,
        "correlation_group": candidate.correlation_group,
        "conservative_ev_net": conservative_ev_net,
        "market_probability": candidate.market_probability,
        "conservative_market_edge": candidate.conservative_market_edge,
    }


def _serialize_selection(selection: OpportunitySelection) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected = [
        _candidate_dict(item.candidate, item.conservative_ev_net)
        for item in selection.selected
    ]
    return selected, dict(sorted(selection.rejection_counts.items()))


class CurrentDataPipeline:
    """Dependency-injected current-data runner with committed-run idempotency.

    Adapters are invoked in sorted name order.  An adapter can be a callable or
    an object exposing ``fetch(context)``.  Likewise, a predictor can be a
    callable or expose ``predict(frame, context)``.  The frame contains only the
    latest provider-specific snapshot that passed all point-in-time gates.
    """

    def __init__(
        self,
        *,
        adapters: Mapping[str, SnapshotAdapter | Any],
        predictor: CurrentPredictor | Any,
        output_dir: str | Path,
        policy: CurrentPipelinePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        predictor_id: str | None = None,
    ) -> None:
        if not adapters:
            raise ValueError("at least one snapshot adapter is required")
        if any(not str(name).strip() for name in adapters):
            raise ValueError("adapter names must not be blank")
        self._adapters = dict(adapters)
        self._predictor = predictor
        self._output_dir = Path(output_dir)
        self._policy = policy or CurrentPipelinePolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._predictor_id = predictor_id or _component_id(predictor)

    def run(
        self,
        run_id: str,
        *,
        cutoff: datetime | str | pd.Timestamp | None = None,
        dry_run: bool = False,
    ) -> PipelineRun:
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise ValueError("run_id must not be blank")
        cutoff_utc = _utc(cutoff if cutoff is not None else self._clock(), field_name="cutoff")
        context = RunContext(normalized_run_id, cutoff_utc)
        request = self._request_descriptor(context)
        request_digest = _digest(request)
        run_dir = self._run_dir(normalized_run_id)

        if not dry_run:
            committed = self._load_committed(run_dir, request_digest)
            if committed is not None:
                return committed

            lock_path = self._acquire_run_lock(run_dir)
            try:
                # Another process may have committed between the first check and
                # our atomic lock acquisition.
                committed = self._load_committed(run_dir, request_digest)
                if committed is not None:
                    return committed
                return self._execute(
                    context=context,
                    request_digest=request_digest,
                    run_dir=run_dir,
                    dry_run=False,
                )
            finally:
                lock_path.unlink(missing_ok=True)

        return self._execute(
            context=context,
            request_digest=request_digest,
            run_dir=run_dir,
            dry_run=True,
        )

    def _execute(
        self,
        *,
        context: RunContext,
        request_digest: str,
        run_dir: Path,
        dry_run: bool,
    ) -> PipelineRun:
        normalized_run_id = context.run_id
        cutoff_utc = context.cutoff

        frame, adapter_counts = self._fetch(context)
        as_of = self._validate_as_of(frame, context)
        candidates, model_id = self._predict(as_of, context)
        selection = self._select(candidates, cutoff_utc)
        selected, rejection_counts = _serialize_selection(selection)

        input_digest = _digest(self._records_for_digest(as_of))
        result = {
            "schema_version": SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "cutoff": _iso(cutoff_utc),
            "model_id": model_id,
            "candidate_count": len(candidates),
            "selected": selected,
            "rejection_counts": rejection_counts,
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "cutoff": _iso(cutoff_utc),
            "request_sha256": request_digest,
            "input_sha256": input_digest,
            "result_sha256": _digest(result),
            "predictor_id": self._predictor_id,
            "model_id": model_id,
            "adapter_counts": dict(sorted(adapter_counts.items())),
            "record_count": len(as_of),
            "feature_group_counts": dict(sorted(Counter(as_of["feature_group"]).items())),
            "provider_counts": dict(sorted(Counter(as_of["provider"]).items())),
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "rejection_counts": rejection_counts,
            "policy": self._policy.as_dict(),
            "contains_raw_payloads": False,
        }
        run = PipelineRun(manifest=manifest, result=result, reused=False, persisted=not dry_run)
        if not dry_run:
            self._commit(run_dir, run)
        return run

    def _acquire_run_lock(self, run_dir: Path) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._output_dir / f".{run_dir.name}.lock"
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise RunConflictError("run_id is already being processed") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "process_id": os.getpid(),
                        "run_directory": run_dir.name,
                    },
                    handle,
                    sort_keys=True,
                )
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        return lock_path

    def _request_descriptor(self, context: RunContext) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": context.run_id,
            "cutoff": _iso(context.cutoff),
            "adapters": {
                name: _component_id(self._adapters[name])
                for name in sorted(self._adapters)
            },
            "predictor_id": self._predictor_id,
            "policy": self._policy.as_dict(),
        }

    def _run_dir(self, run_id: str) -> Path:
        # Hashing prevents path traversal and keeps arbitrary scheduler ids usable.
        return self._output_dir / f"run-{sha256(run_id.encode('utf-8')).hexdigest()[:24]}"

    def _fetch(self, context: RunContext) -> tuple[pd.DataFrame, dict[str, int]]:
        records: list[SnapshotRecord] = []
        sources: list[str] = []
        counts: dict[str, int] = {}
        for name in sorted(self._adapters):
            adapter = self._adapters[name]
            operation = getattr(adapter, "fetch", adapter)
            if not callable(operation):
                raise TypeError(f"adapter {name!r} is neither callable nor exposes fetch()")
            fetched = operation(context)
            if fetched is None:
                raise PipelineDataError(f"adapter {name!r} returned no iterable")
            count = 0
            for item in fetched:
                try:
                    record = item if isinstance(item, SnapshotRecord) else SnapshotRecord(**dict(item))
                except (TypeError, ValueError) as exc:
                    raise PipelineDataError(f"adapter {name!r} returned an invalid snapshot: {exc}") from exc
                records.append(record)
                sources.append(name)
                count += 1
            counts[name] = count
        frame = snapshots_frame(records)
        frame["source_adapter"] = sources
        return frame, counts

    def _validate_as_of(self, frame: pd.DataFrame, context: RunContext) -> pd.DataFrame:
        try:
            checked = validate_snapshots(frame)
        except ValueError as exc:
            raise PipelineDataError(str(exc)) from exc
        cutoff = pd.Timestamp(context.cutoff)
        future = checked[checked["available_at"] > cutoff]
        if not future.empty:
            groups = ", ".join(sorted(future["feature_group"].astype(str).unique()))
            raise PipelineDataError(
                f"{len(future)} snapshot(s) became available after cutoff in: {groups}"
            )
        if checked.empty:
            as_of = checked
        else:
            # Every fetched row is already known to this run. ``available_at`` is
            # the anti-lookahead boundary; ingestion may happen just after the
            # cutoff while adapters are being called.
            known_by = checked["ingested_at"].max()
            as_of = latest_as_of(checked, cutoff, known_by=known_by)
        if len(as_of) < self._policy.minimum_total_records:
            raise PipelineDataError(
                f"only {len(as_of)} usable snapshot(s); minimum is {self._policy.minimum_total_records}"
            )
        group_counts = Counter(as_of["feature_group"].astype(str))
        missing = {
            group: required - group_counts.get(group, 0)
            for group, required in self._policy.required_feature_groups.items()
            if group_counts.get(group, 0) < required
        }
        if missing:
            detail = ", ".join(f"{group} ({count} missing)" for group, count in sorted(missing.items()))
            raise PipelineDataError(f"incomplete snapshot set: {detail}")
        for group, max_age in self._policy.max_age_by_feature_group.items():
            rows = as_of[as_of["feature_group"].eq(group)]
            stale = rows[(cutoff - rows["available_at"]) > max_age]
            if not stale.empty:
                raise PipelineDataError(
                    f"{len(stale)} stale snapshot(s) in {group!r}; maximum age is {max_age}"
                )
        return as_of.sort_values(
            ["entity_id", "feature_group", "provider", "available_at", "ingested_at"],
            kind="stable",
        ).reset_index(drop=True)

    def _predict(
        self,
        frame: pd.DataFrame,
        context: RunContext,
    ) -> tuple[tuple[OpportunityCandidate, ...], str]:
        operation = getattr(self._predictor, "predict", self._predictor)
        if not callable(operation):
            raise TypeError("predictor is neither callable nor exposes predict()")
        predicted = operation(frame.copy(), context)
        if isinstance(predicted, PredictionBatch):
            candidates = tuple(predicted.candidates)
            model_id = predicted.model_id
        else:
            if predicted is None:
                raise PipelineDataError("predictor returned no candidate iterable")
            candidates = tuple(predicted)
            model_id = self._predictor_id
        if any(not isinstance(candidate, OpportunityCandidate) for candidate in candidates):
            raise TypeError("predictor must return OpportunityCandidate instances")
        future = [candidate for candidate in candidates if candidate.observed_at > context.cutoff]
        if future:
            raise PipelineDataError("predictor returned candidate observed after the cutoff")
        return candidates, model_id

    def _select(
        self,
        candidates: tuple[OpportunityCandidate, ...],
        cutoff: datetime,
    ) -> OpportunitySelection:
        kwargs: dict[str, Any] = {
            "max_recommendations": self._policy.max_recommendations,
            "min_conservative_ev_net": self._policy.min_conservative_ev_net,
            "min_conservative_market_edge": self._policy.min_conservative_market_edge,
            "max_per_fixture": self._policy.max_per_fixture,
            "max_per_correlation_group": self._policy.max_per_correlation_group,
        }
        if self._policy.max_quote_age is not None:
            kwargs.update(decision_time=cutoff, max_quote_age=self._policy.max_quote_age)
        return select_opportunities(candidates, **kwargs)

    @staticmethod
    def _records_for_digest(frame: pd.DataFrame) -> list[dict[str, Any]]:
        columns = [
            "entity_id",
            "feature_group",
            "event_time",
            "available_at",
            "ingested_at",
            "provider",
            "provider_version",
            "payload",
            "source_record_id",
            "source_adapter",
        ]
        return [
            {column: _json_value(row[column]) for column in columns if column in frame.columns}
            for _, row in frame.iterrows()
        ]

    def _load_committed(self, run_dir: Path, request_digest: str) -> PipelineRun | None:
        manifest_path = run_dir / "manifest.json"
        result_path = run_dir / "result.json"
        if not manifest_path.exists():
            return None
        if not result_path.exists():
            raise PipelineDataError(f"committed run is missing result: {run_dir}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineDataError(f"cannot read committed run: {run_dir}") from exc
        if manifest.get("request_sha256") != request_digest:
            raise RunConflictError("run_id already exists with a different cutoff or configuration")
        if manifest.get("result_sha256") != _digest(result):
            raise PipelineDataError("committed result checksum does not match its manifest")
        return PipelineRun(manifest=manifest, result=result, reused=True, persisted=True)

    @staticmethod
    def _commit(run_dir: Path, run: PipelineRun) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        result_path = run_dir / "result.json"
        manifest_path = run_dir / "manifest.json"
        # Manifest is the commit marker and is replaced last.  An interrupted
        # first write can therefore be retried safely.
        CurrentDataPipeline._atomic_json(result_path, run.result)
        CurrentDataPipeline._atomic_json(manifest_path, run.manifest)

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        payload = json.dumps(
            _json_value(value),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
