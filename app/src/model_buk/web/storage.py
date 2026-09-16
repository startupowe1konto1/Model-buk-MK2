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
    """Append-only local research registry.

    Predictions and odds snapshots are kept separately. This preserves the PURE
    model output and makes later CLV/line-movement analysis possible.
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    fixture_id INTEGER,
                    bookmaker TEXT,
                    market_key TEXT,
                    odds REAL NOT NULL,
                    provider_update TEXT,
                    raw_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_fixture_market ON odds_snapshots(fixture_id, market_key, created_at)")

    def save(self, payload: dict[str, Any]) -> int:
        fixture = payload.get("fixture", {})
        opportunities = payload.get("opportunities") or []
        best = opportunities[0] if opportunities else {}
        model_version = payload.get("engine_version") or payload.get("suite_version") or payload.get("model_version") or "unknown"
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
                    str(fixture.get("date") or fixture.get("kickoff") or ""),
                    str(fixture.get("home_team") or (fixture.get("home") or {}).get("name") or ""),
                    str(fixture.get("away_team") or (fixture.get("away") or {}).get("name") or ""),
                    str(model_version),
                    best.get("decision"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def save_odds(self, fixture_id: int | None, odds_rows: list[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with self._connect() as conn:
            for row in odds_rows:
                market_key = row.get("market_key")
                if not market_key:
                    continue
                conn.execute(
                    """
                    INSERT INTO odds_snapshots (
                        created_at, fixture_id, bookmaker, market_key, odds,
                        provider_update, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        fixture_id,
                        str(row.get("bookmaker") or ""),
                        str(market_key),
                        float(row["odd"]),
                        row.get("update"),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def recent_odds(self, fixture_id: int, market_key: str, bookmaker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM odds_snapshots WHERE fixture_id=? AND market_key=?"
        params: list[Any] = [fixture_id, market_key]
        if bookmaker:
            sql += " AND bookmaker=?"
            params.append(bookmaker)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 20) -> list[StoredPrediction]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM predictions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
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
