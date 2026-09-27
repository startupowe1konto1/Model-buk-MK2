"""Versioned, secret-free configuration for paper/shadow scans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any
import tomllib


CONFIG_VERSION = "paper-scan-v1"
MAX_RECOMMENDATIONS = 10


class PaperScanConfigError(ValueError):
    """The paper/shadow configuration is invalid or incomplete."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_blank(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperScanConfigError(f"{field_name} must be a non-blank string")
    return value.strip()


def _path(value: Any, *, field_name: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise PaperScanConfigError(f"{field_name} must be a path string or Path")
    if isinstance(value, str) and not value.strip():
        raise PaperScanConfigError(f"{field_name} must not be empty")
    result = Path(value)
    if not str(result):
        raise PaperScanConfigError(f"{field_name} must not be empty")
    return result


def _float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperScanConfigError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise PaperScanConfigError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class PaperScanConfig:
    """Validated settings for one repeatable paper/shadow scan.

    ``model_id`` and ``engine_id`` are alternatives: at least one must be
    versioned and non-blank. Keeping both fields identifies model and engine
    artifacts independently when both are available.
    """

    version: str = CONFIG_VERSION
    output_dir: Path = Path("runtime/paper-runs")
    snapshot_archive_dir: Path = Path("runtime/snapshot-archive")
    archive_live_snapshots: bool = True
    history_path: Path = Path("data/multileague/europe16_matches_v05.csv.gz")
    model_id: str | None = "pure-multimarket-v1"
    engine_id: str | None = None
    api_day_offsets: tuple[int, ...] = (0, 1)
    league_codes: tuple[str, ...] = ()
    max_fixtures: int = 10
    include_context: bool = True
    include_recent_observations: bool = False
    max_recommendations: int = MAX_RECOMMENDATIONS
    min_conservative_ev_net: float = 0.02
    min_conservative_market_edge: float | None = None
    max_decimal_odds: float = 2.0
    devig_method: str = "proportional"
    tax_multiplier: float = 0.88
    execution_cost: float = 0.0
    probability_haircut: float = 0.05
    min_data_quality: float = 60.0
    quote_freshness: timedelta = timedelta(minutes=30)
    required_feature_groups: Mapping[str, int] = field(
        default_factory=lambda: {"fixture": 1, "odds": 1}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _non_blank(self.version, field_name="version"))
        object.__setattr__(self, "output_dir", _path(self.output_dir, field_name="output_dir"))
        object.__setattr__(
            self,
            "snapshot_archive_dir",
            _path(self.snapshot_archive_dir, field_name="snapshot_archive_dir"),
        )
        object.__setattr__(self, "history_path", _path(self.history_path, field_name="history_path"))
        if not isinstance(self.archive_live_snapshots, bool):
            raise PaperScanConfigError("archive_live_snapshots must be a boolean")

        for field_name in ("model_id", "engine_id"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _non_blank(value, field_name=field_name))
        if not self.model_id and not self.engine_id:
            raise PaperScanConfigError("at least one of model_id or engine_id is required")

        if not isinstance(self.api_day_offsets, Sequence) or isinstance(
            self.api_day_offsets, (str, bytes)
        ):
            raise PaperScanConfigError("api_day_offsets must be a sequence of integers")
        offsets = tuple(self.api_day_offsets)
        if not offsets or any(not _is_int(value) or value < 0 for value in offsets):
            raise PaperScanConfigError("api_day_offsets must contain non-negative integers")
        if len(set(offsets)) != len(offsets):
            raise PaperScanConfigError("api_day_offsets must not contain duplicates")
        object.__setattr__(self, "api_day_offsets", offsets)

        if not isinstance(self.league_codes, Sequence) or isinstance(self.league_codes, (str, bytes)):
            raise PaperScanConfigError("league_codes must be a sequence of strings")
        leagues = tuple(_non_blank(value, field_name="league_codes item") for value in self.league_codes)
        if len(set(leagues)) != len(leagues):
            raise PaperScanConfigError("league_codes must not contain duplicates")
        object.__setattr__(self, "league_codes", leagues)

        if not _is_int(self.max_fixtures) or self.max_fixtures < 1:
            raise PaperScanConfigError("max_fixtures must be a positive integer")
        if not _is_int(self.max_recommendations) or not 0 <= self.max_recommendations <= MAX_RECOMMENDATIONS:
            raise PaperScanConfigError(f"max_recommendations must be an integer in [0, {MAX_RECOMMENDATIONS}]")
        if not isinstance(self.include_context, bool):
            raise PaperScanConfigError("include_context must be a boolean")
        if not isinstance(self.include_recent_observations, bool):
            raise PaperScanConfigError("include_recent_observations must be a boolean")

        ev = _float(self.min_conservative_ev_net, field_name="min_conservative_ev_net")
        edge = (
            None
            if self.min_conservative_market_edge is None
            else _float(self.min_conservative_market_edge, field_name="min_conservative_market_edge")
        )
        cost = _float(self.execution_cost, field_name="execution_cost")
        tax = _float(self.tax_multiplier, field_name="tax_multiplier")
        haircut = _float(self.probability_haircut, field_name="probability_haircut")
        quality = _float(self.min_data_quality, field_name="min_data_quality")
        max_odds = _float(self.max_decimal_odds, field_name="max_decimal_odds")
        if ev < 0.0:
            raise PaperScanConfigError("min_conservative_ev_net must be non-negative")
        if edge is not None and edge < 0.0:
            raise PaperScanConfigError("min_conservative_market_edge must be non-negative or null")
        if max_odds <= 1.0:
            raise PaperScanConfigError("max_decimal_odds must be greater than 1.0")
        method = _non_blank(self.devig_method, field_name="devig_method")
        if method not in {"proportional", "power", "shin"}:
            raise PaperScanConfigError("devig_method must be one of: proportional, power, shin")
        if cost < 0.0:
            raise PaperScanConfigError("execution_cost must be non-negative")
        if not 0.0 < tax <= 1.0:
            raise PaperScanConfigError("tax_multiplier must be in (0, 1]")
        if not 0.0 <= haircut < 1.0:
            raise PaperScanConfigError("probability_haircut must be in [0, 1)")
        if not 0.0 <= quality <= 100.0:
            raise PaperScanConfigError("min_data_quality must be between 0 and 100")
        object.__setattr__(self, "min_conservative_ev_net", ev)
        object.__setattr__(self, "min_conservative_market_edge", edge)
        object.__setattr__(self, "max_decimal_odds", max_odds)
        object.__setattr__(self, "devig_method", method)
        object.__setattr__(self, "execution_cost", cost)
        object.__setattr__(self, "tax_multiplier", tax)
        object.__setattr__(self, "probability_haircut", haircut)
        object.__setattr__(self, "min_data_quality", quality)

        if not isinstance(self.quote_freshness, timedelta) or self.quote_freshness <= timedelta(0):
            raise PaperScanConfigError("quote_freshness must be a positive timedelta")

        if not isinstance(self.required_feature_groups, Mapping):
            raise PaperScanConfigError("required_feature_groups must be a mapping")
        groups: dict[str, int] = {}
        for name, count in self.required_feature_groups.items():
            group = _non_blank(name, field_name="required feature group")
            if not _is_int(count) or count < 1:
                raise PaperScanConfigError("required feature group counts must be integers >= 1")
            groups[group] = count
        if not groups:
            raise PaperScanConfigError("required_feature_groups must not be empty")
        object.__setattr__(self, "required_feature_groups", groups)

    @property
    def day_offsets(self) -> tuple[int, ...]:
        """Alias matching :class:`ApiFootballSnapshotPolicy`."""

        return self.api_day_offsets

    @property
    def config_id(self) -> str:
        """Stable short digest suitable for a run manifest."""

        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return f"{self.version}:{sha256(encoded.encode('utf-8')).hexdigest()[:16]}"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible, secret-free representation."""

        return {
            "version": self.version,
            "output_dir": str(self.output_dir),
            "snapshot_archive_dir": str(self.snapshot_archive_dir),
            "archive_live_snapshots": self.archive_live_snapshots,
            "history_path": str(self.history_path),
            "model_id": self.model_id,
            "engine_id": self.engine_id,
            "api_day_offsets": list(self.api_day_offsets),
            "league_codes": list(self.league_codes),
            "max_fixtures": self.max_fixtures,
            "include_context": self.include_context,
            "include_recent_observations": self.include_recent_observations,
            "max_recommendations": self.max_recommendations,
            "min_conservative_ev_net": self.min_conservative_ev_net,
            "min_conservative_market_edge": self.min_conservative_market_edge,
            "max_decimal_odds": self.max_decimal_odds,
            "devig_method": self.devig_method,
            "tax_multiplier": self.tax_multiplier,
            "execution_cost": self.execution_cost,
            "probability_haircut": self.probability_haircut,
            "min_data_quality": self.min_data_quality,
            "quote_freshness_seconds": self.quote_freshness.total_seconds(),
            "required_feature_groups": dict(sorted(self.required_feature_groups.items())),
        }

    @classmethod
    def from_toml(cls, path: str | Path) -> "PaperScanConfig":
        """Load and validate a TOML file without reading environment secrets."""

        config_path = Path(path)
        try:
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except OSError as exc:
            raise PaperScanConfigError(f"cannot read configuration: {config_path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise PaperScanConfigError(f"invalid TOML configuration: {config_path}") from exc
        section = raw.get("paper_scan", raw)
        if not isinstance(section, dict):
            raise PaperScanConfigError("paper_scan must be a TOML table")
        values = dict(section)
        if "quote_freshness" in values:
            freshness = values.pop("quote_freshness")
            if isinstance(freshness, bool) or not isinstance(freshness, (int, float)):
                raise PaperScanConfigError("quote_freshness must be seconds as a number")
            values["quote_freshness"] = timedelta(seconds=float(freshness))
        elif "quote_freshness_seconds" in values:
            seconds = values.pop("quote_freshness_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                raise PaperScanConfigError("quote_freshness_seconds must be a number")
            values["quote_freshness"] = timedelta(seconds=float(seconds))
        elif "quote_freshness_minutes" in values:
            minutes = values.pop("quote_freshness_minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
                raise PaperScanConfigError("quote_freshness_minutes must be a number")
            values["quote_freshness"] = timedelta(minutes=float(minutes))
        if "api_day_offsets" not in values and "day_offsets" in values:
            values["api_day_offsets"] = values.pop("day_offsets")
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise PaperScanConfigError(f"unknown paper_scan keys: {', '.join(sorted(unknown))}")
        try:
            return cls(**values)
        except TypeError as exc:
            raise PaperScanConfigError(f"invalid paper_scan keys: {exc}") from exc


def load_paper_scan_config(path: str | Path) -> PaperScanConfig:
    """Functional loader for callers that prefer a module-level API."""

    return PaperScanConfig.from_toml(path)
