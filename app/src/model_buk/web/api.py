from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from model_buk.web.service import PredictionService, ServicePaths
from model_buk.web.storage import PredictionStore


class CornerPredictionRequest(BaseModel):
    date: str = Field(description="Fixture date/time")
    home_team: str
    away_team: str
    line: float | None = None
    over_odds: float | None = Field(default=None, gt=1.0)
    under_odds: float | None = Field(default=None, gt=1.0)
    persist: bool = True


def _default_service() -> PredictionService:
    return PredictionService(
        ServicePaths(
            history=Path(os.getenv("MODEL_BUK_HISTORY", "data/canonical/epl_matches_v01.csv.gz")),
            model_root=Path(os.getenv("MODEL_BUK_MODEL_ROOT", "models")),
            config=Path(os.getenv("MODEL_BUK_CONFIG", "config/corners_v02.toml")),
        )
    )


def create_app(
    service: PredictionService | Any | None = None,
    store: PredictionStore | Any | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    service = service or _default_service()
    store = store or PredictionStore(os.getenv("MODEL_BUK_DB", "runtime/model_buk.sqlite3"))
    static_path = Path(static_dir or Path(__file__).with_name("static"))

    app = FastAPI(
        title="Model Buk API",
        version="0.4.0",
        description="Research/paper football probability and market-value engine.",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        try:
            return service.status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/teams")
    def teams() -> dict[str, list[str]]:
        try:
            return {"teams": service.teams()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/predict/corners")
    def predict_corners(request: CornerPredictionRequest) -> dict[str, Any]:
        try:
            payload = service.predict(**request.model_dump(exclude={"persist"}))
            if request.persist:
                payload["prediction_id"] = store.save(payload)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/predictions")
    def predictions(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
        items = store.recent(limit)
        return {
            "predictions": [
                {
                    "id": item.id,
                    "created_at": item.created_at,
                    "fixture_date": item.fixture_date,
                    "home_team": item.home_team,
                    "away_team": item.away_team,
                    "model_version": item.model_version,
                    "decision": item.decision,
                    "payload": item.payload,
                }
                for item in items
            ]
        }

    app.mount("/static", StaticFiles(directory=static_path), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_path / "index.html")

    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "model_buk.web.api:app",
        host=os.getenv("MODEL_BUK_HOST", "127.0.0.1"),
        port=int(os.getenv("MODEL_BUK_PORT", "8000")),
        reload=os.getenv("MODEL_BUK_RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    run()
