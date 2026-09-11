from pathlib import Path

from fastapi.testclient import TestClient

from model_buk.web.api import create_app
from model_buk.web.storage import PredictionStore


class FakeService:
    def status(self):
        return {"status":"ok","deployment_status":"research/paper only","history_rows":100,"history_from":"2025-01-01","history_through":"2026-05-24","data_staleness_days":10,"total_model":"total-v1","team_model":"team-v2","registry_as_of":"2026-05-24"}
    def teams(self):
        return ["Arsenal","Chelsea"]
    def predict(self, **kwargs):
        return {
            "suite_version":"test-suite",
            "fixture":{"date":kwargs["date"],"home_team":kwargs["home_team"],"away_team":kwargs["away_team"]},
            "data_quality":{"score":90},
            "total_corners":{"model":"total-v1","expected_total":10.1,"markets":[]},
            "team_corners":{"model":"team-v2","expected_home":5.6,"expected_away":4.5,"markets":[]},
        }


def test_web_health_and_prediction(tmp_path: Path):
    store=PredictionStore(tmp_path/"test.sqlite3")
    app=create_app(service=FakeService(),store=store)
    client=TestClient(app)
    assert client.get('/api/health').json()=={"status":"ok"}
    assert client.get('/api/teams').json()["teams"]==["Arsenal","Chelsea"]
    r=client.post('/api/predict/corners',json={"date":"2026-09-12T17:30","home_team":"Arsenal","away_team":"Chelsea","persist":True})
    assert r.status_code==200
    assert r.json()["prediction_id"]==1
    recent=client.get('/api/predictions').json()["predictions"]
    assert len(recent)==1
    assert recent[0]["home_team"]=="Arsenal"
