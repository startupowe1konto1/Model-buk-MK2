from pathlib import Path

from fastapi.testclient import TestClient

from model_buk.web.api import create_app
from model_buk.web.storage import PredictionStore


class FakeLegacy:
    def status(self): return {"status": "ok"}
    def teams(self): return ["Arsenal", "Chelsea"]
    def predict(self, **kwargs): return {"fixture": {"date": kwargs["date"], "home_team": kwargs["home_team"], "away_team": kwargs["away_team"]}, "suite_version": "legacy", "total_corners": {"markets": []}, "team_corners": {"markets": []}}


class FakeAnalysis:
    def status(self):
        return {"status": "ok", "app_version": "0.5.0", "live_provider": {"connected": False}, "leagues": 6, "historical_matches": 1, "market_engines": ["Goals"]}
    def leagues(self): return [{"code": "EPL", "name": "Premier League", "country": "England"}]
    def teams(self, league): return ["Arsenal", "Chelsea"]
    def fixtures(self, date, league_codes=None): return {"mode": "manual", "fixtures": [], "provider": {"connected": False}}
    def analyze_manual(self, league_code, date, home_team, away_team):
        return {
            "engine_version": "v05-test",
            "league": {"code": league_code, "name": "Premier League", "country": "England"},
            "fixture": {"date": date, "home_team": home_team, "away_team": away_team},
            "expected": {"goals": {"home": 1.5, "away": 1.0, "total": 2.5}},
            "markets": [], "factors": [], "opportunities": [], "market_comparison": [],
        }


def test_v05_catalog_manual_analysis_and_registry(tmp_path: Path):
    store = PredictionStore(tmp_path / "v05.sqlite3")
    app = create_app(service=FakeLegacy(), analysis_service=FakeAnalysis(), store=store)
    client = TestClient(app)
    assert client.get("/api/catalog/leagues").json()["leagues"][0]["code"] == "EPL"
    assert client.get("/api/teams?league=EPL").json()["teams"] == ["Arsenal", "Chelsea"]
    r = client.post("/api/analyze/manual", json={"league_code": "EPL", "date": "2026-09-15T20:00", "home_team": "Arsenal", "away_team": "Chelsea", "persist": True})
    assert r.status_code == 200
    assert r.json()["prediction_id"] == 1
    assert client.get("/api/predictions").json()["predictions"][0]["model_version"] == "v05-test"
