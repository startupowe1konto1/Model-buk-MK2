from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GasPoissonConfig:
    attack_step: float = 0.04
    defence_step: float = 0.04
    persistence: float = 0.98
    min_rate: float = 0.05
    max_rate: float = 8.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.attack_step <= 1.0:
            raise ValueError("attack_step must be between 0 and 1")
        if not 0.0 <= self.defence_step <= 1.0:
            raise ValueError("defence_step must be between 0 and 1")
        if not 0.0 <= self.persistence <= 1.0:
            raise ValueError("persistence must be between 0 and 1")
        if self.min_rate <= 0.0 or self.max_rate <= self.min_rate:
            raise ValueError("rates must satisfy 0 < min_rate < max_rate")


@dataclass
class GasPoissonModel:
    """Observation-driven attack/defence state updated by Poisson scores."""

    intercept: float
    home_advantage: float
    attack: dict[str, float]
    defence: dict[str, float]
    config: GasPoissonConfig

    @property
    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.attack) | set(self.defence)))

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_log_rate = (
            self.intercept
            + self.home_advantage
            + self.attack.get(home_team, 0.0)
            + self.defence.get(away_team, 0.0)
        )
        away_log_rate = (
            self.intercept
            + self.attack.get(away_team, 0.0)
            + self.defence.get(home_team, 0.0)
        )
        home_rate = float(np.clip(math.exp(home_log_rate), self.config.min_rate, self.config.max_rate))
        away_rate = float(np.clip(math.exp(away_log_rate), self.config.min_rate, self.config.max_rate))
        return home_rate, away_rate

    def observe_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> tuple[float, float]:
        """Update latent states after returning the pre-match expected rates."""
        if home_goals < 0 or away_goals < 0:
            raise ValueError("goals must be non-negative")
        home_rate, away_rate = self.expected_goals(home_team, away_team)
        self.attack.setdefault(home_team, 0.0)
        self.attack.setdefault(away_team, 0.0)
        self.defence.setdefault(home_team, 0.0)
        self.defence.setdefault(away_team, 0.0)
        home_score = float(home_goals) - home_rate
        away_score = float(away_goals) - away_rate
        persistence = self.config.persistence
        self.attack[home_team] = (
            persistence * self.attack[home_team] + self.config.attack_step * home_score
        )
        self.defence[away_team] = (
            persistence * self.defence[away_team] + self.config.defence_step * home_score
        )
        self.attack[away_team] = (
            persistence * self.attack[away_team] + self.config.attack_step * away_score
        )
        self.defence[home_team] = (
            persistence * self.defence[home_team] + self.config.defence_step * away_score
        )
        self._center_states()
        return home_rate, away_rate

    def _center_states(self) -> None:
        if self.attack:
            mean_attack = float(np.mean(list(self.attack.values())))
            for team in self.attack:
                self.attack[team] -= mean_attack
        if self.defence:
            mean_defence = float(np.mean(list(self.defence.values())))
            for team in self.defence:
                self.defence[team] -= mean_defence


def fit_gas_poisson(
    matches: pd.DataFrame,
    *,
    config: GasPoissonConfig | None = None,
) -> GasPoissonModel:
    """Initialize league rates and replay matches chronologically into GAS state."""
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    data = matches.loc[:, sorted(required)].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce", format="mixed")
    data["home_goals"] = pd.to_numeric(data["home_goals"], errors="coerce")
    data["away_goals"] = pd.to_numeric(data["away_goals"], errors="coerce")
    if data[["date", "home_team", "away_team", "home_goals", "away_goals"]].isna().any().any():
        raise ValueError("matches contains missing or invalid required values")
    if (data[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("goals must be non-negative")
    away_mean = max(float(data["away_goals"].mean()), 0.05)
    home_mean = max(float(data["home_goals"].mean()), 0.05)
    teams = sorted(set(data["home_team"]) | set(data["away_team"]))
    model = GasPoissonModel(
        intercept=math.log(away_mean),
        home_advantage=math.log(home_mean / away_mean),
        attack={str(team): 0.0 for team in teams},
        defence={str(team): 0.0 for team in teams},
        config=config or GasPoissonConfig(),
    )
    for row in data.sort_values(["date", "home_team", "away_team"]).itertuples(index=False):
        model.observe_match(
            str(row.home_team),
            str(row.away_team),
            int(row.home_goals),
            int(row.away_goals),
        )
    return model
