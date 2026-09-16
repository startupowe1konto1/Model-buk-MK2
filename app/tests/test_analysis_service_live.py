from pathlib import Path

from model_buk.web.service import MatchAnalysisService, ServicePaths


class FakeProvider:
    connected = True
    def status(self): return {"name": "fake", "connected": True}
    def fixtures(self, date, league_codes=None): return []
    def match_context(self, fixture_id, deep=True):
        odds = [
            {"bookmaker_id": 1, "bookmaker": "TestBook", "bet_id": 5, "bet": "Goals Over/Under", "value": "Over 2.5", "odd": 2.10, "update": "2026-09-15T10:00:00Z"},
            {"bookmaker_id": 1, "bookmaker": "TestBook", "bet_id": 5, "bet": "Goals Over/Under", "value": "Under 2.5", "odd": 1.75, "update": "2026-09-15T10:00:00Z"},
        ]
        home_obs = [{"goals_for": 3, "goals_against": 1, "shots_for": 17, "shots_against": 9, "sot_for": 7, "sot_against": 3, "corners_for": 7, "corners_against": 4, "cards_for": 2, "cards_against": 2}] * 5
        away_obs = [{"goals_for": 1, "goals_against": 2, "shots_for": 10, "shots_against": 15, "sot_for": 3, "sot_against": 5, "corners_for": 4, "corners_against": 6, "cards_for": 2, "cards_against": 3}] * 5
        return {
            "fixture": {
                "fixture_id": fixture_id, "kickoff": "2026-09-15T20:00:00+00:00", "league_code": "EPL", "league_name": "Premier League",
                "home": {"id": 1, "name": "Arsenal", "logo": None}, "away": {"id": 2, "name": "Chelsea", "logo": None},
                "round": "Regular Season - 5", "referee": "Test Ref", "venue": {"name": None, "city": None},
            },
            "injuries": [{"team_id": 2, "player": "Player A"}],
            "lineups": [], "odds": odds, "external_prediction_benchmark": None,
            "recent_observations": {"home": home_obs, "away": away_obs},
            "errors": [], "provider": self.status(), "generated_at": "x",
        }


def test_live_analysis_uses_current_observations_and_odds(tmp_path: Path, multileague_history_path):
    paths = ServicePaths(
        history=Path("does-not-exist.csv"),
        model_root=tmp_path / "models-none",
        config=Path("config/corners_v02.toml"),
        multileague_history=multileague_history_path,
        stadiums=Path("does-not-exist.json"),
    )
    service = MatchAnalysisService(paths=paths, provider=FakeProvider())
    result = service.analyze_fixture(123, deep=True)
    assert result["mode"] == "live_current_context"
    assert result["current_data"]["used_in_model"] is True
    assert result["raw_odds_count"] == 2
    assert result["market_comparison"]
    assert any(f["key"] == "injuries" for f in result["factors"])
    assert result["confidence"]["score"] > result["data_quality"]["score"]
