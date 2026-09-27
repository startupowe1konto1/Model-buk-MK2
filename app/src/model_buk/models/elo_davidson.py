"""Sequential Elo-Davidson probabilities for home/draw/away outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class EloDavidsonConfig:
    k_factor: float = 20.0
    rating_scale: float = 400.0
    initial_rating: float = 1500.0
    home_advantage_points: float | None = None
    draw_parameter: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.k_factor) or self.k_factor <= 0.0:
            raise ValueError("k_factor must be positive and finite")
        if not math.isfinite(self.rating_scale) or self.rating_scale <= 0.0:
            raise ValueError("rating_scale must be positive and finite")
        if not math.isfinite(self.initial_rating):
            raise ValueError("initial_rating must be finite")
        if self.home_advantage_points is not None and not math.isfinite(
            self.home_advantage_points
        ):
            raise ValueError("home_advantage_points must be finite or None")
        if self.draw_parameter is not None and (
            not math.isfinite(self.draw_parameter) or self.draw_parameter <= 0.0
        ):
            raise ValueError("draw_parameter must be positive and finite or None")


@dataclass
class EloDavidsonModel:
    ratings: dict[str, float]
    config: EloDavidsonConfig
    home_advantage_points: float
    draw_parameter: float

    @property
    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(self.ratings))

    def probabilities(self, home_team: str, away_team: str) -> tuple[float, float, float]:
        home_rating = self.ratings.get(home_team, self.config.initial_rating)
        away_rating = self.ratings.get(away_team, self.config.initial_rating)
        home_strength = 10.0 ** (
            (home_rating + self.home_advantage_points) / self.config.rating_scale
        )
        away_strength = 10.0 ** (away_rating / self.config.rating_scale)
        draw_strength = self.draw_parameter * math.sqrt(home_strength * away_strength)
        total = home_strength + draw_strength + away_strength
        return (
            home_strength / total,
            draw_strength / total,
            away_strength / total,
        )

    def observe_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> tuple[float, float, float]:
        if home_goals < 0 or away_goals < 0:
            raise ValueError("goals must be non-negative")
        probabilities = self.probabilities(home_team, away_team)
        self.ratings.setdefault(home_team, self.config.initial_rating)
        self.ratings.setdefault(away_team, self.config.initial_rating)
        actual_score = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
        expected_score = probabilities[0] + 0.5 * probabilities[1]
        change = self.config.k_factor * (actual_score - expected_score)
        self.ratings[home_team] += change
        self.ratings[away_team] -= change
        return probabilities


def _estimated_home_advantage(frame: pd.DataFrame, rating_scale: float) -> float:
    home_wins = float((frame["home_goals"] > frame["away_goals"]).sum()) + 0.5
    away_wins = float((frame["home_goals"] < frame["away_goals"]).sum()) + 0.5
    estimate = rating_scale * math.log10(home_wins / away_wins)
    return float(max(-200.0, min(200.0, estimate)))


def _estimated_draw_parameter(frame: pd.DataFrame) -> float:
    draw_rate = (float((frame["home_goals"] == frame["away_goals"]).sum()) + 1.0) / (
        len(frame) + 2.0
    )
    estimate = 2.0 * draw_rate / max(1.0 - draw_rate, 1e-9)
    return float(max(0.1, min(2.0, estimate)))


def fit_elo_davidson(
    matches: pd.DataFrame,
    *,
    config: EloDavidsonConfig | None = None,
) -> EloDavidsonModel:
    required = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    if matches.empty:
        raise ValueError("matches is empty")
    frame = matches.loc[:, sorted(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", format="mixed")
    frame["home_goals"] = pd.to_numeric(frame["home_goals"], errors="coerce")
    frame["away_goals"] = pd.to_numeric(frame["away_goals"], errors="coerce")
    if frame[list(required)].isna().any().any():
        raise ValueError("matches contains missing or invalid required values")
    if (frame[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("goals must be non-negative")

    resolved = config or EloDavidsonConfig()
    teams = sorted(set(frame["home_team"].astype(str)) | set(frame["away_team"].astype(str)))
    model = EloDavidsonModel(
        ratings={team: resolved.initial_rating for team in teams},
        config=resolved,
        home_advantage_points=(
            resolved.home_advantage_points
            if resolved.home_advantage_points is not None
            else _estimated_home_advantage(frame, resolved.rating_scale)
        ),
        draw_parameter=(
            resolved.draw_parameter
            if resolved.draw_parameter is not None
            else _estimated_draw_parameter(frame)
        ),
    )
    for _, simultaneous in frame.sort_values(
        ["date", "home_team", "away_team"]
    ).groupby("date", sort=True):
        pending = list(simultaneous.itertuples(index=False))
        for row in pending:
            model.observe_match(
                str(row.home_team),
                str(row.away_team),
                int(row.home_goals),
                int(row.away_goals),
            )
    return model


__all__ = ["EloDavidsonConfig", "EloDavidsonModel", "fit_elo_davidson"]
