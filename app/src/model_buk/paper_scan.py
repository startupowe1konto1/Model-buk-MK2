"""Composition root for repeatable live and offline paper/shadow scans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from model_buk.artifact_identity import build_artifact_manifest, sha256_file
from model_buk.catalog import LEAGUES
from model_buk.current_pipeline import (
    CurrentDataPipeline,
    CurrentPipelinePolicy,
    PipelineRun,
)
from model_buk.ingestion.api_football import (
    ApiFootballSnapshotAdapter,
    ApiFootballSnapshotPolicy,
)
from model_buk.live_provider import ApiFootballClient
from model_buk.multimarket import MultiMarketEngine
from model_buk.paper_config import PaperScanConfig
from model_buk.snapshot_predictor import MultiMarketSnapshotPredictor, SnapshotPredictorPolicy
from model_buk.snapshots import SnapshotRecord


SourceMode = Literal["live", "offline"]


class PaperScanError(RuntimeError):
    """The scan cannot be composed safely with the requested inputs."""


class ProviderUnavailableError(PaperScanError):
    """Live mode was requested without an available provider."""


class OfflineSnapshotAdapter:
    """Read explicit JSON/JSONL snapshots without constructing a network client."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise PaperScanError("offline snapshot input is missing or is not a file")
        self.component_id = f"offline-snapshots-v1:{sha256_file(self.path)}"

    def fetch(self, context: Any) -> list[SnapshotRecord]:
        try:
            if self.path.suffix.lower() == ".jsonl":
                values = [
                    json.loads(line)
                    for line in self.path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            else:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                values = payload.get("snapshots") if isinstance(payload, dict) else payload
        except (OSError, json.JSONDecodeError) as exc:
            raise PaperScanError("cannot read offline snapshot input") from exc
        if not isinstance(values, list):
            raise PaperScanError("offline snapshot input must be a JSON array or snapshots object")
        records: list[SnapshotRecord] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise PaperScanError(f"offline snapshot at index {index} must be an object")
            try:
                records.append(SnapshotRecord(**value))
            except (TypeError, ValueError) as exc:
                raise PaperScanError(f"invalid offline snapshot at index {index}: {exc}") from exc
        return records


@dataclass(frozen=True, slots=True)
class BuiltPaperScan:
    pipeline: CurrentDataPipeline
    source_mode: SourceMode
    config_id: str
    engine_manifest: dict[str, Any]


def _validate_leagues(league_codes: tuple[str, ...]) -> None:
    unknown = sorted(set(league_codes) - set(LEAGUES))
    if unknown:
        raise PaperScanError(f"unsupported league code(s): {', '.join(unknown)}")


def _engine_manifest(config: PaperScanConfig) -> dict[str, Any]:
    if not config.history_path.is_file():
        raise PaperScanError("historical model input is missing or is not a file")
    return build_artifact_manifest(
        config.model_id or "multimarket",
        config.engine_id or config.version,
        {"history.csv": config.history_path},
    )


def build_paper_scan(
    config: PaperScanConfig,
    *,
    source_mode: SourceMode,
    api_key: str | None = None,
    snapshots_path: str | Path | None = None,
    cache_dir: str | Path = "runtime/api_cache/paper-scan",
    clock: Callable[[], datetime] | None = None,
    client: Any | None = None,
    engine: Any | None = None,
) -> BuiltPaperScan:
    """Build the production composition without making provider requests."""

    if source_mode not in {"live", "offline"}:
        raise PaperScanError("source_mode must be 'live' or 'offline'")
    _validate_leagues(config.league_codes)
    manifest = _engine_manifest(config)
    pure_engine = engine or MultiMarketEngine(config.history_path)
    predictor = MultiMarketSnapshotPredictor(
        pure_engine,
        engine_id=str(manifest["engine_id"]),
        policy=SnapshotPredictorPolicy(
            probability_margin=config.probability_haircut,
            devig_method=config.devig_method,
            tax_multiplier=config.tax_multiplier,
            execution_cost=config.execution_cost,
            min_data_quality=config.min_data_quality,
        ),
    )

    if source_mode == "live":
        live_client = client or ApiFootballClient(api_key=api_key, cache_dir=cache_dir)
        if not getattr(live_client, "connected", False):
            raise ProviderUnavailableError(
                "live mode requires API_FOOTBALL_KEY or an explicit API key"
            )
        adapter: Any = ApiFootballSnapshotAdapter(
            live_client,
            policy=ApiFootballSnapshotPolicy(
                day_offsets=config.api_day_offsets,
                league_codes=config.league_codes,
                include_context=config.include_context,
                include_recent_observations=config.include_recent_observations,
                max_fixtures=config.max_fixtures,
            ),
            clock=clock,
        )
        adapter_name = "api-football"
    else:
        if snapshots_path is None:
            raise PaperScanError("offline mode requires snapshots_path")
        adapter = OfflineSnapshotAdapter(snapshots_path)
        adapter_name = "offline-snapshots"

    pipeline = CurrentDataPipeline(
        adapters={adapter_name: adapter},
        predictor=predictor,
        predictor_id=predictor.model_id,
        output_dir=config.output_dir,
        policy=CurrentPipelinePolicy(
            required_feature_groups=config.required_feature_groups,
            max_age_by_feature_group={"odds": config.quote_freshness},
            max_recommendations=config.max_recommendations,
            min_conservative_ev_net=config.min_conservative_ev_net,
            min_conservative_market_edge=config.min_conservative_market_edge,
            max_decimal_odds=config.max_decimal_odds,
            max_quote_age=config.quote_freshness,
        ),
        clock=clock,
    )
    return BuiltPaperScan(
        pipeline=pipeline,
        source_mode=source_mode,
        config_id=config.config_id,
        engine_manifest=manifest,
    )


def normalize_cutoff(value: str | datetime | pd.Timestamp | None) -> datetime:
    timestamp = pd.Timestamp(value if value is not None else datetime.now(timezone.utc))
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise PaperScanError("cutoff must include a timezone")
    return timestamp.tz_convert("UTC").to_pydatetime()


def default_run_id(cutoff: datetime) -> str:
    return cutoff.astimezone(timezone.utc).strftime("scan-%Y%m%dT%H%M%SZ")


def run_paper_scan(
    built: BuiltPaperScan,
    *,
    cutoff: str | datetime | pd.Timestamp | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cutoff_utc = normalize_cutoff(cutoff)
    effective_run_id = str(run_id).strip() if run_id is not None else default_run_id(cutoff_utc)
    if not effective_run_id:
        raise PaperScanError("run_id must not be blank")
    run: PipelineRun = built.pipeline.run(
        effective_run_id,
        cutoff=cutoff_utc,
        dry_run=dry_run,
    )
    return {
        "status": "ok",
        "mode": "paper/shadow",
        "source_mode": built.source_mode,
        "run_id": effective_run_id,
        "cutoff": cutoff_utc.isoformat().replace("+00:00", "Z"),
        "persisted": run.persisted,
        "reused": run.reused,
        "config_id": built.config_id,
        "engine_id": built.engine_manifest["engine_id"],
        "candidate_count": run.result["candidate_count"],
        "selected_count": len(run.result["selected"]),
        "result": run.result,
        "manifest": run.manifest,
    }


__all__ = [
    "BuiltPaperScan",
    "OfflineSnapshotAdapter",
    "PaperScanError",
    "ProviderUnavailableError",
    "build_paper_scan",
    "default_run_id",
    "normalize_cutoff",
    "run_paper_scan",
]
