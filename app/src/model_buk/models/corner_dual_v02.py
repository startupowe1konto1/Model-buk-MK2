from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from model_buk.constants import CAT_FEATURES, CORE_FEATURES
from model_buk.strengths import STRENGTH_FEATURES


V02_NUMERIC_FEATURES = CORE_FEATURES + STRENGTH_FEATURES
V02_FEATURES = CAT_FEATURES + V02_NUMERIC_FEATURES


@dataclass
class DualCornerModel:
    home_model: CatBoostRegressor
    away_model: CatBoostRegressor

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        home = np.clip(self.home_model.predict(frame[V02_FEATURES]), 0.05, 20.0)
        away = np.clip(self.away_model.predict(frame[V02_FEATURES]), 0.05, 20.0)
        return home, away

    def save(self, directory: str | Path, metadata: dict | None = None) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.home_model.save_model(str(directory / "corner_home_v0_2.cbm"))
        self.away_model.save_model(str(directory / "corner_away_v0_2.cbm"))
        payload = {"features": V02_FEATURES, **(metadata or {})}
        (directory / "corner_dual_v0_2_metadata.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


def fit_dual_model(train: pd.DataFrame, params: dict) -> DualCornerModel:
    home = CatBoostRegressor(**params)
    away = CatBoostRegressor(**params)
    home.fit(train[V02_FEATURES], train["home_corners"], cat_features=CAT_FEATURES)
    away.fit(train[V02_FEATURES], train["away_corners"], cat_features=CAT_FEATURES)
    return DualCornerModel(home_model=home, away_model=away)
