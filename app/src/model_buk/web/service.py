from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from model_buk.config import CornerV02Config, load_corner_v02_config
from model_buk.inference import attach_total_market_price
from model_buk.suite import LiveCornerSuite, load_live_corner_suite, predict_with_live_suite


@dataclass(frozen=True)
class ServicePaths:
    history: Path = Path("data/canonical/epl_matches_v01.csv.gz")
    model_root: Path = Path("models")
    config: Path = Path("config/corners_v02.toml")


class PredictionService:
    """Lazy-loaded application service around the frozen model suite."""

    def __init__(self, paths: ServicePaths | None = None):
        self.paths = paths or ServicePaths()
        self._history: pd.DataFrame | None = None
        self._suite: LiveCornerSuite | None = None
        self._cfg: CornerV02Config | None = None

    @property
    def cfg(self) -> CornerV02Config:
        if self._cfg is None:
            self._cfg = load_corner_v02_config(self.paths.config)
        return self._cfg

    @property
    def history(self) -> pd.DataFrame:
        if self._history is None:
            frame = pd.read_csv(self.paths.history)
            frame["date"] = pd.to_datetime(frame["date"], format="mixed")
            self._history = frame
        return self._history

    @property
    def suite(self) -> LiveCornerSuite:
        if self._suite is None:
            self._suite = load_live_corner_suite(self.paths.model_root)
        return self._suite

    def teams(self) -> list[str]:
        frame = self.history
        teams = set(frame["home_team"].dropna().astype(str)) | set(frame["away_team"].dropna().astype(str))
        return sorted(teams)

    def status(self) -> dict[str, Any]:
        history = self.history
        latest = pd.Timestamp(history["date"].max())
        now = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
        latest_naive = latest.tz_localize(None) if latest.tzinfo else latest
        stale_days = max(0, int((now - latest_naive).total_seconds() // 86400))
        return {
            "status": "ok",
            "deployment_status": self.suite.registry.get("deployment_status", "research/paper only"),
            "history_rows": int(len(history)),
            "history_from": str(history["date"].min()),
            "history_through": str(latest),
            "data_staleness_days": stale_days,
            "total_model": self.suite.total_metadata.get("model_version"),
            "team_model": self.suite.team_engine.metadata.get("version"),
            "registry_as_of": self.suite.registry.get("as_of"),
        }

    def predict(
        self,
        *,
        date: str,
        home_team: str,
        away_team: str,
        line: float | None = None,
        over_odds: float | None = None,
        under_odds: float | None = None,
    ) -> dict[str, Any]:
        known_teams = set(self.teams())
        if home_team not in known_teams:
            raise ValueError(f"Unknown home team: {home_team}")
        if away_team not in known_teams:
            raise ValueError(f"Unknown away team: {away_team}")
        if home_team == away_team:
            raise ValueError("home_team and away_team must differ")

        supplied = [x is not None for x in (line, over_odds, under_odds)]
        if any(supplied) and not all(supplied):
            raise ValueError("line, over_odds and under_odds must be supplied together")

        prediction = predict_with_live_suite(
            self.history,
            self.suite,
            self.cfg,
            date=date,
            home_team=home_team,
            away_team=away_team,
        )
        prediction["generated_at"] = datetime.now(timezone.utc).isoformat()
        prediction["data_status"] = self.status()

        if all(supplied):
            stub = {"total_markets": prediction["total_corners"]["markets"]}
            priced = attach_total_market_price(
                stub,
                self.cfg,
                line=float(line),
                over_odds=float(over_odds),
                under_odds=float(under_odds),
            )
            prediction["market_check"] = priced["market_check"]
        return prediction
