from __future__ import annotations

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from scipy.stats import nbinom

from .constants import CAT_FEATURES, CORE_FEATURES, MODEL_PARAMS


def fit_total_model(train: pd.DataFrame) -> CatBoostRegressor:
    model = CatBoostRegressor(**MODEL_PARAMS)
    features = CAT_FEATURES + CORE_FEATURES
    model.fit(train[features], train["total_corners"], cat_features=CAT_FEATURES)
    return model


def predict_mean(model: CatBoostRegressor, frame: pd.DataFrame) -> np.ndarray:
    features = CAT_FEATURES + CORE_FEATURES
    return np.clip(model.predict(frame[features]), 0.1, 30.0)


def estimate_nb_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), 1e-6, None)
    alpha = np.nanmean(((y - mu) ** 2 - mu) / (mu**2))
    return float(max(0.001, alpha))


def over_probability(mu: np.ndarray | float, line: float, alpha: float) -> np.ndarray:
    mu = np.asarray(mu, dtype=float)
    r = 1.0 / alpha
    p = r / (r + mu)
    k = int(np.floor(line))
    return 1.0 - nbinom.cdf(k, r, p)
