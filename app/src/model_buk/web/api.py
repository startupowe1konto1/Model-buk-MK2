from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from model_buk.web.service import MatchAnalysisService, PredictionService, ServicePaths
from model_buk.web.storage import PredictionStore


class CornerPredictionRequest(BaseModel):
    date: str = Field(description="Fixture date/time")
    home_team: str
    away_team: str
    line: float | None = None
    over_odds: float | None = Field(default=None, gt=1.0)
    under_odds: float | None = Field(default=None, gt=1.0)
    persist: bool = True


class ManualAnalysisRequest(BaseModel):
    league_code: str
    date: str
    home_team: str
    away_team: str
    persist: bool = True


def _paths() -> ServicePaths:
    return ServicePaths(
        history=Path(os.getenv("MODEL_BUK_HISTORY", "data/canonical/epl_matches_v01.csv.gz")),
        model_root=Path(os.getenv("MODEL_BUK_MODEL_ROOT", "models")),
        config=Path(os.getenv("MODEL_BUK_CONFIG", "config/corners_v02.toml")),
        multileague_history=Path(os.getenv("MODEL_BUK_MULTILEAGUE_HISTORY", "data/multileague/europe16_matches_v05.csv.gz")),
        stadiums=Path(os.getenv("MODEL_BUK_STADIUMS", "data/stadiums_europe.json")),
    )


def _default_service() -> PredictionService:
    return PredictionService(_paths())


def _default_analysis_service() -> MatchAnalysisService:
    return MatchAnalysisService(
        _paths(),
        tax_multiplier=float(os.getenv("MODEL_BUK_TAX_MULTIPLIER", "0.88")),
        allow_research_bets=os.getenv("MODEL_BUK_ALLOW_RESEARCH_BETS", "0") == "1",
    )


def create_app(
    service: PredictionService | Any | None = None,
    analysis_service: MatchAnalysisService | Any | None = None,
    store: PredictionStore | Any | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    legacy_service = service or _default_service()
    # Existing tests can inject only the legacy service. New production startup
    # creates the v0.5 analysis orchestrator automatically.
    if analysis_service is None and service is None:
        analysis_service = _default_analysis_service()
    store = store or PredictionStore(os.getenv("MODEL_BUK_DB", "runtime/model_buk.sqlite3"))
    static_path = Path(static_dir or Path(__file__).with_name("static"))

    app = FastAPI(
        title="Model Buk API",
        version="0.5.0",
        description="Multi-league, multi-market football probability + current-context research engine.",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        try:
            if analysis_service is not None:
                return analysis_service.status()
            return legacy_service.status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/catalog/leagues")
    def leagues() -> dict[str, Any]:
        if analysis_service is None:
            return {"leagues": []}
        try:
            return {"leagues": analysis_service.leagues()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/teams")
    def teams(league: str | None = None) -> dict[str, list[str]]:
        try:
            if league and analysis_service is not None:
                return {"teams": analysis_service.teams(league)}
            return {"teams": legacy_service.teams()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/fixtures")
    def fixtures(date: str, league: list[str] = Query(default=[])) -> dict[str, Any]:
        if analysis_service is None:
            return {"mode": "manual", "fixtures": [], "message": "Live fixture service unavailable in this test mode."}
        try:
            return analysis_service.fixtures(date, league or None)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/analyze/manual")
    def analyze_manual(request: ManualAnalysisRequest) -> dict[str, Any]:
        if analysis_service is None:
            raise HTTPException(status_code=503, detail="v0.5 analysis service unavailable")
        try:
            payload = analysis_service.analyze_manual(
                request.league_code,
                request.date,
                request.home_team,
                request.away_team,
            )
            if request.persist:
                payload["prediction_id"] = store.save(payload)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/analyze/fixture/{fixture_id}")
    def analyze_fixture(fixture_id: int, deep: bool = True, persist: bool = True) -> dict[str, Any]:
        if analysis_service is None:
            raise HTTPException(status_code=503, detail="v0.5 analysis service unavailable")
        try:
            payload = analysis_service.analyze_fixture(fixture_id, deep=deep)
            normalized_odds = payload.get("normalized_odds") or []
            if normalized_odds:
                payload["odds_snapshots_saved"] = store.save_odds(fixture_id, normalized_odds)
            if persist:
                payload["prediction_id"] = store.save(payload)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Legacy v0.4 corner endpoint remains available for reproducibility.
    @app.post("/api/predict/corners")
    def predict_corners(request: CornerPredictionRequest) -> dict[str, Any]:
        try:
            payload = legacy_service.predict(**request.model_dump(exclude={"persist"}))
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


class _UnavailableService:
    def __init__(self, reason: str):
        self.reason = reason

    def __getattr__(self, _name: str) -> Any:
        raise FileNotFoundError(self.reason)


def _create_default_app_safely() -> FastAPI:
    try:
        return create_app()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        unavailable = _UnavailableService(
            "Backend is not ready: required versioned data/model artifacts are missing or invalid. "
            f"Details: {exc}"
        )
        return create_app(service=unavailable, analysis_service=unavailable)


app = _create_default_app_safely()


def run() -> None:
    uvicorn.run(
        "model_buk.web.api:app",
        host=os.getenv("MODEL_BUK_HOST", "127.0.0.1"),
        port=int(os.getenv("MODEL_BUK_PORT", "8000")),
        reload=os.getenv("MODEL_BUK_RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    run()
