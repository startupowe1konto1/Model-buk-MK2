from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredPrediction:
    id: int
    created_at: str
    fixture_date: str
    home_team: str
    away_team: str
    model_version: str
    decision: str | None
    payload: dict[str, Any]


class PredictionStore:
    """Small append-only SQLite registry for prospective model runs.

    Prediction payloads are stored exactly as produced by the engine so a later
    result/CLV layer can evaluate what was known at prediction time.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    fixture_date TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    decision TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at DESC)"
            )

    def save(self, payload: dict[str, Any]) -> int:
        fixture = payload.get("fixture", {})
        market = payload.get("market_check") or {}
        best = market.get("best_decision") or {}
        model_version = (
            payload.get("suite_version")
            or payload.get("model_version")
            or payload.get("total_corners", {}).get("model")
            or "unknown"
        )
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO predictions (
                    created_at, fixture_date, home_team, away_team,
                    model_version, decision, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    str(fixture.get("date", "")),
                    str(fixture.get("home_team", "")),
                    str(fixture.get("away_team", "")),
                    str(model_version),
                    best.get("decision"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> list[StoredPrediction]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM predictions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            StoredPrediction(
                id=int(row["id"]),
                created_at=str(row["created_at"]),
                fixture_date=str(row["fixture_date"]),
                home_team=str(row["home_team"]),
                away_team=str(row["away_team"]),
                model_version=str(row["model_version"]),
                decision=row["decision"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]
