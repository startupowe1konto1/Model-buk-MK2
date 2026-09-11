from __future__ import annotations

LINES = (7.5, 8.5, 9.5, 10.5)

CAT_FEATURES = ["home_team", "away_team"]

CORE_FEATURES: list[str] = []
for side in ("home", "away"):
    for metric in ("corners_for", "corners_against"):
        for suffix in ("r5", "r10", "ewm8", "venue5"):
            CORE_FEATURES.append(f"{side}_{metric}_{suffix}")
    for metric in ("shots_for", "shots_against", "sot_for", "sot_against"):
        for suffix in ("r5", "ewm8"):
            CORE_FEATURES.append(f"{side}_{metric}_{suffix}")
    for metric in ("goals_for", "goals_against"):
        CORE_FEATURES.append(f"{side}_{metric}_r5")
    CORE_FEATURES += [
        f"{side}_rest_days",
        f"{side}_points_r5",
        f"{side}_games_prior",
    ]
CORE_FEATURES += [
    "league_total_corners_r100",
    "league_home_corners_r100",
    "league_away_corners_r100",
]
CORE_FEATURES = list(dict.fromkeys(CORE_FEATURES))

MODEL_PARAMS = {
    "loss_function": "Poisson",
    "iterations": 600,
    "depth": 5,
    "learning_rate": 0.03,
    "l2_leaf_reg": 8,
    "random_seed": 42,
    "verbose": False,
    "allow_writing_files": False,
}

# Frozen before evaluating 2025/26 holdout.
BET_EDGE_THRESHOLD = 0.05
BET_EV_THRESHOLD = 0.05
TRAIN_START = "2015-01-01"
HOLDOUT_START = "2025-08-01"

OOF_FOLDS = (
    ("2022-08-01", "2023-08-01", "2022/23"),
    ("2023-08-01", "2024-08-01", "2023/24"),
    ("2024-08-01", "2025-08-01", "2024/25"),
)
