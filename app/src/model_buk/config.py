from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class CornerV02Config:
    version: str
    train_start: str
    reference_start: str
    max_count: int
    random_seed: int
    catboost_params: dict
    shrinkage_games: float
    min_rate: float
    max_rate: float
    edge_threshold: float
    ev_threshold: float
    max_bets_per_match: int
    folds: tuple[tuple[str, str, str], ...]


def load_corner_v02_config(path: str | Path) -> CornerV02Config:
    path = Path(path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return CornerV02Config(
        version=str(raw["model"]["version"]),
        train_start=str(raw["model"]["train_start"]),
        reference_start=str(raw["validation"]["reference_start"]),
        max_count=int(raw["model"]["max_count"]),
        random_seed=int(raw["model"]["random_seed"]),
        catboost_params=dict(raw["catboost"]),
        shrinkage_games=float(raw["strengths"]["shrinkage_games"]),
        min_rate=float(raw["strengths"]["min_rate"]),
        max_rate=float(raw["strengths"]["max_rate"]),
        edge_threshold=float(raw["betting"]["edge_threshold"]),
        ev_threshold=float(raw["betting"]["ev_threshold"]),
        max_bets_per_match=int(raw["betting"]["max_bets_per_match"]),
        folds=tuple(tuple(map(str, fold)) for fold in raw["validation"]["folds"]),
    )
