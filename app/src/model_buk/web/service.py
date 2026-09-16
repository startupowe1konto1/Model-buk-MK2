from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from model_buk.config import CornerV02Config, load_corner_v02_config
from model_buk.inference import attach_total_market_price
from model_buk.live_adjustment import apply_current_observations
from model_buk.live_provider import ApiFootballClient
from model_buk.multimarket import MultiMarketEngine, rebuild_markets_from_expected
from model_buk.odds_parser import normalize_odds, rank_opportunities
from model_buk.suite import LiveCornerSuite, load_live_corner_suite, predict_with_live_suite
from model_buk.weather import weather_context


@dataclass(frozen=True)
class ServicePaths:
    history: Path = Path("data/canonical/epl_matches_v01.csv.gz")
    model_root: Path = Path("models")
    config: Path = Path("config/corners_v02.toml")
    multileague_history: Path = Path("data/multileague/europe16_matches_v05.csv.gz")
    stadiums: Path = Path("data/stadiums_europe.json")


class PredictionService:
    """Backward-compatible service around the frozen EPL corner suite."""

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

    def predict(self, *, date: str, home_team: str, away_team: str, line: float | None = None, over_odds: float | None = None, under_odds: float | None = None) -> dict[str, Any]:
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
        prediction = predict_with_live_suite(self.history, self.suite, self.cfg, date=date, home_team=home_team, away_team=away_team)
        prediction["generated_at"] = datetime.now(timezone.utc).isoformat()
        prediction["data_status"] = self.status()
        if all(supplied):
            stub = {"total_markets": prediction["total_corners"]["markets"]}
            priced = attach_total_market_price(stub, self.cfg, line=float(line), over_odds=float(over_odds), under_odds=float(under_odds))
            prediction["market_check"] = priced["market_check"]
        return prediction


class MatchAnalysisService:
    """v0.5 orchestration: many leagues, many markets, current context and odds."""

    def __init__(
        self,
        paths: ServicePaths | None = None,
        provider: ApiFootballClient | None = None,
        *,
        tax_multiplier: float = 0.88,
        allow_research_bets: bool = False,
    ):
        self.paths = paths or ServicePaths()
        self.multimarket = MultiMarketEngine(self.paths.multileague_history)
        self.provider = provider or ApiFootballClient()
        self.tax_multiplier = tax_multiplier
        self.allow_research_bets = allow_research_bets
        self.corner_service: PredictionService | None = None
        try:
            if self.paths.history.exists() and self.paths.model_root.exists():
                self.corner_service = PredictionService(self.paths)
        except Exception:
            self.corner_service = None

    def status(self) -> dict[str, Any]:
        leagues = self.multimarket.leagues()
        latest = max((x["history_through"] for x in leagues if x["history_through"]), default=None)
        return {
            "status": "ok",
            "app_version": "0.5.0",
            "deployment_status": "research / paper betting",
            "live_provider": self.provider.status(),
            "leagues": len(leagues),
            "historical_matches": int(sum(x["matches"] for x in leagues)),
            "history_through": latest,
            "market_engines": ["1X2", "BTTS", "Goals", "Corners", "Shots", "SOT", "Cards"],
            "epl_corner_champion_available": self.corner_service is not None,
            "pure_model_rule": "Bookmaker odds are never model inputs; prices are attached only after PURE probabilities exist.",
            "betting_gate": "enabled" if self.allow_research_bets else "disabled_until_oos_validation",
            "tax_multiplier": self.tax_multiplier,
        }

    def leagues(self) -> list[dict[str, Any]]:
        return self.multimarket.leagues()

    def teams(self, league_code: str) -> list[str]:
        return self.multimarket.teams(league_code)

    def fixtures(self, date: str, league_codes: list[str] | None = None) -> dict[str, Any]:
        if not self.provider.connected:
            return {
                "mode": "manual",
                "fixtures": [],
                "message": "Podłącz API-Football, aby aplikacja sama pobierała bieżące mecze. Analiza ręczna wielu lig działa bez API.",
                "provider": self.provider.status(),
            }
        return {
            "mode": "live",
            "fixtures": self.provider.fixtures(date, league_codes),
            "provider": self.provider.status(),
        }

    @staticmethod
    def _corner_reference_to_markets(reference: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for m in reference.get("total_corners", {}).get("markets", []):
            line = float(m["line"])
            rows.append({"market_key": f"corners.total.over.{line}", "label": f"Powyżej {line}", "group": "corners", "threshold": line, "side": "over", "probability": float(m["p_over"]), "fair_odds": float(m["fair_over"])})
            rows.append({"market_key": f"corners.total.under.{line}", "label": f"Poniżej {line}", "group": "corners", "threshold": line, "side": "under", "probability": float(m["p_under"]), "fair_odds": float(m["fair_under"])})
        for m in reference.get("team_corners", {}).get("markets", []):
            line = float(m["line"]); side = m["side"]
            rows.append({"market_key": f"corners.{side}.over.{line}", "label": f"{m['team']}: powyżej {line}", "group": "corners", "threshold": line, "side": "over", "probability": float(m["p_over"]), "fair_odds": float(m["fair_over"])})
            rows.append({"market_key": f"corners.{side}.under.{line}", "label": f"{m['team']}: poniżej {line}", "group": "corners", "threshold": line, "side": "under", "probability": float(m["p_under"]), "fair_odds": float(m["fair_under"])})
        return rows

    def _attach_epl_corner_reference(self, analysis: dict[str, Any]) -> None:
        if analysis["league"]["code"] != "EPL" or self.corner_service is None:
            return
        h = analysis["fixture"]["home_team"]
        a = analysis["fixture"]["away_team"]
        try:
            # Resolve names against the canonical EPL model history.
            candidates = self.corner_service.teams()
            from model_buk.team_names import resolve_team_name
            rh, _ = resolve_team_name(h, candidates); ra, _ = resolve_team_name(a, candidates)
            if not rh or not ra:
                return
            ref = self.corner_service.predict(date=analysis["fixture"]["date"], home_team=rh, away_team=ra)
            analysis["benchmarks"] = analysis.get("benchmarks", {})
            analysis["benchmarks"]["epl_corner_champion"] = {
                "status": "validated_reference_not_current_context",
                "model": ref.get("total_corners", {}).get("model"),
                "expected_total": ref.get("total_corners", {}).get("expected_total"),
                "expected_home": ref.get("team_corners", {}).get("expected_home"),
                "expected_away": ref.get("team_corners", {}).get("expected_away"),
                "markets": self._corner_reference_to_markets(ref),
                "note": "Best historical EPL corners benchmark. v0.5 current-context engine is evaluated separately.",
            }
        except Exception as exc:
            analysis.setdefault("warnings", []).append(f"EPL corner benchmark unavailable: {exc}")

    @staticmethod
    def _context_cards(live: dict[str, Any], weather: dict[str, Any] | None) -> list[dict[str, Any]]:
        fixture = live.get("fixture") or {}
        injuries = live.get("injuries") or []
        lineups = live.get("lineups") or []
        home_id = (fixture.get("home") or {}).get("id")
        away_id = (fixture.get("away") or {}).get("id")
        home_inj = [x for x in injuries if x.get("team_id") == home_id]
        away_inj = [x for x in injuries if x.get("team_id") == away_id]
        cards = [
            {
                "key": "injuries",
                "label": "Kontuzje / zawieszenia",
                "home": len(home_inj),
                "away": len(away_inj),
                "details": {"home": home_inj, "away": away_inj},
                "status": "quality_gate",
                "model_usage": "not_probability_input_yet",
                "explanation": "Aktualne absencje są pobierane, ale bez player-strength nie dostają arbitralnej wagi w probability.",
            },
            {
                "key": "lineups",
                "label": "Składy",
                "home": next((x for x in lineups if x.get("team_id") == home_id), None),
                "away": next((x for x in lineups if x.get("team_id") == away_id), None),
                "status": "confirmed" if len(lineups) >= 2 else "not_confirmed",
                "model_usage": "quality_gate",
                "explanation": "Potwierdzone XI obniża uncertainty; lineup-strength będzie osobnym, walidowanym modelem.",
            },
            {
                "key": "referee",
                "label": "Sędzia",
                "value": fixture.get("referee"),
                "status": "context",
                "model_usage": "monitor_only",
                "explanation": "Nie wpływa na probability, dopóki referee×team interaction nie przejdzie testu OOS.",
            },
            {
                "key": "venue",
                "label": "Stadion",
                "value": fixture.get("venue"),
                "status": "context",
                "model_usage": "venue_home_away_already_in_model",
            },
        ]
        if weather:
            cards.append({
                "key": "weather",
                "label": "Pogoda",
                "value": weather,
                "status": "context",
                "model_usage": weather.get("model_usage", "monitor_only"),
                "explanation": weather.get("note"),
            })
        return cards

    def analyze_manual(self, league_code: str, date: str, home_team: str, away_team: str, persist_context: bool = False) -> dict[str, Any]:
        analysis = self.multimarket.analyze(league_code, date, home_team, away_team)
        analysis["mode"] = "manual_historical_baseline"
        analysis["current_context"] = {
            "connected": False,
            "message": "To jest szeroki baseline. Aby użyć bieżących meczów, kontuzji, lineupów i kursów, podłącz API-Football lub analizuj fixture z dashboardu.",
        }
        self._attach_epl_corner_reference(analysis)
        analysis["opportunities"] = []
        analysis["market_comparison"] = []
        analysis["generated_at"] = datetime.now(timezone.utc).isoformat()
        return analysis

    def analyze_fixture(self, fixture_id: int, deep: bool = True) -> dict[str, Any]:
        if not self.provider.connected:
            raise RuntimeError("API-Football nie jest podłączone.")
        live = self.provider.match_context(fixture_id, deep=deep)
        fixture = live["fixture"]
        league_code = fixture.get("league_code")
        if not league_code:
            raise ValueError(f"Liga fixture_id={fixture_id} nie jest jeszcze wspierana przez v0.5")

        analysis = self.multimarket.analyze(
            league_code,
            fixture["kickoff"],
            fixture["home"]["name"],
            fixture["away"]["name"],
        )
        analysis["mode"] = "live_current_context"
        analysis["fixture"]["fixture_id"] = fixture_id
        analysis["fixture"]["provider_home"] = fixture["home"]
        analysis["fixture"]["provider_away"] = fixture["away"]
        analysis["fixture"]["round"] = fixture.get("round")
        analysis["fixture"]["venue"] = fixture.get("venue")
        analysis["fixture"]["referee"] = fixture.get("referee")

        observations = live.get("recent_observations") or {}
        analysis = apply_current_observations(analysis, observations.get("home") or [], observations.get("away") or [])
        analysis["markets"] = rebuild_markets_from_expected(analysis)

        weather = None
        if self.paths.stadiums.exists():
            try:
                weather = weather_context(self.paths.stadiums, fixture)
            except Exception as exc:
                weather = {"available": False, "reason": str(exc)}
        analysis["factors"] = analysis.get("factors", []) + self._context_cards(live, weather)
        analysis["current_context"] = {
            "connected": True,
            "provider": live.get("provider"),
            "injuries": live.get("injuries"),
            "lineups": live.get("lineups"),
            "weather": weather,
            "external_prediction_benchmark": live.get("external_prediction_benchmark"),
            "errors": live.get("errors"),
        }

        raw_odds = live.get("odds") or []
        opportunities, compared = rank_opportunities(
            analysis["markets"],
            raw_odds,
            tax_multiplier=self.tax_multiplier,
            allow_bets=self.allow_research_bets,
        )
        analysis["opportunities"] = opportunities[:12]
        analysis["market_comparison"] = compared
        analysis["raw_odds_count"] = len(raw_odds)
        analysis["normalized_odds"] = normalize_odds(raw_odds)

        self._attach_epl_corner_reference(analysis)

        samples = analysis.get("current_data", {}).get("observation_samples", {})
        sample_values = [v.get("home", 0) for v in samples.values()] + [v.get("away", 0) for v in samples.values()]
        current_n = min(sample_values) if sample_values else 0
        base_quality = float(analysis["data_quality"]["score"])
        live_bonus = 12 if analysis.get("current_data", {}).get("used_in_model") else 0
        lineup_bonus = 4 if len(live.get("lineups") or []) >= 2 else 0
        injury_bonus = 2 if not any(str(e).startswith("injuries:") for e in live.get("errors") or []) else 0
        odds_bonus = 2 if raw_odds else 0
        confidence = min(96.0, base_quality + live_bonus + lineup_bonus + injury_bonus + odds_bonus)
        analysis["data_readiness"] = {
            "score": round(confidence, 1),
            "current_observations_min_sample": int(current_n),
            "lineups_confirmed": len(live.get("lineups") or []) >= 2,
            "odds_available": bool(raw_odds),
            "label": "wysoka" if confidence >= 80 else "średnia" if confidence >= 60 else "niska",
            "note": "Operational data-readiness score; not calibrated predictive confidence.",
        }
        analysis["confidence"] = analysis["data_readiness"]
        analysis["generated_at"] = datetime.now(timezone.utc).isoformat()
        return analysis
