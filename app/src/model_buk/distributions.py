from __future__ import annotations

import numpy as np
from scipy.stats import nbinom, poisson


def estimate_nb_alpha(y: np.ndarray, mu: np.ndarray, minimum: float = 0.001) -> float:
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), 1e-6, None)
    raw = np.nanmean(((y - mu) ** 2 - mu) / (mu ** 2))
    return float(max(minimum, raw))


def nb_pmf(mu: float, alpha: float, max_count: int = 40) -> np.ndarray:
    mu = float(max(mu, 1e-6))
    alpha = float(max(alpha, 1e-9))
    k = np.arange(max_count + 1)
    if alpha < 1e-6:
        pmf = poisson.pmf(k, mu)
    else:
        r = 1.0 / alpha
        p = r / (r + mu)
        pmf = nbinom.pmf(k, r, p)
    mass = pmf.sum()
    return pmf / mass if mass > 0 else pmf


def nb_over_probability(mu: float, line: float, alpha: float) -> float:
    mu = float(max(mu, 1e-6))
    k = int(np.floor(line))
    if alpha < 1e-6:
        return float(1.0 - poisson.cdf(k, mu))
    r = 1.0 / alpha
    p = r / (r + mu)
    return float(1.0 - nbinom.cdf(k, r, p))


def convolved_total_pmf(
    home_mu: float,
    away_mu: float,
    home_alpha: float,
    away_alpha: float,
    max_count: int = 40,
) -> np.ndarray:
    home = nb_pmf(home_mu, home_alpha, max_count=max_count)
    away = nb_pmf(away_mu, away_alpha, max_count=max_count)
    total = np.convolve(home, away)
    mass = total.sum()
    return total / mass if mass > 0 else total


def convolved_total_over_probability(
    home_mu: float,
    away_mu: float,
    line: float,
    home_alpha: float,
    away_alpha: float,
    max_count: int = 40,
) -> float:
    pmf = convolved_total_pmf(home_mu, away_mu, home_alpha, away_alpha, max_count)
    k = int(np.floor(line))
    return float(pmf[k + 1 :].sum())


def nb_over_probability_array(mu: np.ndarray, line: float, alpha: float) -> np.ndarray:
    mu = np.clip(np.asarray(mu, dtype=float), 1e-6, None)
    k = int(np.floor(line))
    if alpha < 1e-6:
        return 1.0 - poisson.cdf(k, mu)
    r = 1.0 / alpha
    p = r / (r + mu)
    return 1.0 - nbinom.cdf(k, r, p)
