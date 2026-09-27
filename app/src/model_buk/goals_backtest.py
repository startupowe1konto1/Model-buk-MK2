from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from model_buk.models.dixon_coles import (
    fit_dixon_coles,
    market_probabilities,
    score_matrix,
)
from model_buk.models.gas_poisson import GasPoissonConfig, fit_gas_poisson
from model_buk.models.elo_davidson import EloDavidsonConfig, fit_elo_davidson
from model_buk.models.negative_binomial import fit_negative_binomial


@dataclass(frozen=True)
class GoalsFold:
    label: str
    start: str | pd.Timestamp
    end: str | pd.Timestamp

    def bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        start = pd.Timestamp(self.start)
        end = pd.Timestamp(self.end)
        if end <= start:
            raise ValueError("fold end must be later than fold start")
        return start, end


def _prepare_matches(matches: pd.DataFrame) -> pd.DataFrame:
    required = {
        "league",
        "kickoff",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
    }
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"matches is missing required columns: {', '.join(missing)}")
    columns = sorted(required)
    for optional in ("home_xg", "away_xg"):
        if optional in matches.columns:
            columns.append(optional)
    if "source_row_id" in matches.columns:
        columns.append("source_row_id")
    data = matches.loc[:, columns].copy()
    data["kickoff"] = pd.to_datetime(data["kickoff"], errors="coerce", format="mixed")
    data["home_goals"] = pd.to_numeric(data["home_goals"], errors="coerce")
    data["away_goals"] = pd.to_numeric(data["away_goals"], errors="coerce")
    data = data.dropna(
        subset=["league", "kickoff", "home_team", "away_team", "home_goals", "away_goals"]
    )
    return data.sort_values(["kickoff", "league", "home_team", "away_team"]).reset_index(drop=True)


def walk_forward_dixon_coles(
    matches: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    half_life_days: float | None = 365.0,
    l2_strength: float = 0.0,
    xg_weight: float = 0.0,
    min_train_matches: int = 100,
    max_goals: int = 10,
) -> pd.DataFrame:
    """Generate strictly chronological, league-specific Dixon-Coles forecasts."""
    if min_train_matches < 1:
        raise ValueError("min_train_matches must be positive")
    data = _prepare_matches(matches)
    predictions: list[dict] = []
    for fold in folds:
        start, end = fold.bounds()
        train_all = data[data["kickoff"] < start]
        valid_all = data[(data["kickoff"] >= start) & (data["kickoff"] < end)]
        for league, valid in valid_all.groupby("league", sort=True):
            train = train_all[train_all["league"] == league]
            if len(train) < min_train_matches:
                continue
            fit_frame = train.rename(columns={"kickoff": "date"})
            model = fit_dixon_coles(
                fit_frame,
                half_life_days=half_life_days,
                l2_strength=l2_strength,
                xg_weight=xg_weight,
                reference_date=start,
            )
            for row in valid.itertuples(index=False):
                home_rate, away_rate = model.expected_goals(
                    str(row.home_team),
                    str(row.away_team),
                    unknown_strategy="neutral",
                )
                probabilities = market_probabilities(
                    score_matrix(home_rate, away_rate, rho=model.rho, max_goals=max_goals)
                )
                actual = (
                    "home"
                    if row.home_goals > row.away_goals
                    else "draw"
                    if row.home_goals == row.away_goals
                    else "away"
                )
                predictions.append(
                    {
                        "fold": fold.label,
                        "source_row_id": getattr(row, "source_row_id", None),
                        "league": league,
                        "kickoff": row.kickoff,
                        "home_team": row.home_team,
                        "away_team": row.away_team,
                        "home_goals": int(row.home_goals),
                        "away_goals": int(row.away_goals),
                        "home_rate": home_rate,
                        "away_rate": away_rate,
                        "p_home": probabilities["result.home"],
                        "p_draw": probabilities["result.draw"],
                        "p_away": probabilities["result.away"],
                        "actual": actual,
                        "cold_start": bool(
                            row.home_team not in model.teams or row.away_team not in model.teams
                        ),
                    }
                )
    return pd.DataFrame(predictions)


def walk_forward_gas_poisson(
    matches: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    config: GasPoissonConfig | None = None,
    min_train_matches: int = 100,
    max_goals: int = 10,
) -> pd.DataFrame:
    """Generate one-step-ahead GAS forecasts and update only after outcomes."""
    if min_train_matches < 1:
        raise ValueError("min_train_matches must be positive")
    data = _prepare_matches(matches)
    predictions: list[dict] = []
    for fold in folds:
        start, end = fold.bounds()
        train_all = data[data["kickoff"] < start]
        valid_all = data[(data["kickoff"] >= start) & (data["kickoff"] < end)]
        for league, valid in valid_all.groupby("league", sort=True):
            train = train_all[train_all["league"] == league]
            if len(train) < min_train_matches:
                continue
            model = fit_gas_poisson(train.rename(columns={"kickoff": "date"}), config=config)
            for kickoff, simultaneous in valid.groupby("kickoff", sort=True):
                pending: list[tuple] = []
                for row in simultaneous.itertuples(index=False):
                    home_rate, away_rate = model.expected_goals(
                        str(row.home_team), str(row.away_team)
                    )
                    probabilities = market_probabilities(
                        score_matrix(home_rate, away_rate, max_goals=max_goals)
                    )
                    actual = (
                        "home" if row.home_goals > row.away_goals else
                        "draw" if row.home_goals == row.away_goals else "away"
                    )
                    predictions.append(
                        {
                            "fold": fold.label,
                            "source_row_id": getattr(row, "source_row_id", None),
                            "league": league,
                            "kickoff": kickoff,
                            "home_team": row.home_team,
                            "away_team": row.away_team,
                            "home_goals": int(row.home_goals),
                            "away_goals": int(row.away_goals),
                            "home_rate": home_rate,
                            "away_rate": away_rate,
                            "p_home": probabilities["result.home"],
                            "p_draw": probabilities["result.draw"],
                            "p_away": probabilities["result.away"],
                            "actual": actual,
                            "cold_start": bool(
                                row.home_team not in model.teams or row.away_team not in model.teams
                            ),
                        }
                    )
                    pending.append(row)
                # Simultaneous fixtures cannot update one another.
                for row in pending:
                    model.observe_match(
                        str(row.home_team),
                        str(row.away_team),
                        int(row.home_goals),
                        int(row.away_goals),
                    )
    return pd.DataFrame(predictions)


def walk_forward_elo_davidson(
    matches: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    config: EloDavidsonConfig | None = None,
    min_train_matches: int = 100,
    max_goals: int = 10,
) -> pd.DataFrame:
    """Generate one-step-ahead Elo-Davidson forecasts without goal-distribution fitting."""
    del max_goals  # Kept for the common nested-runner signature.
    if min_train_matches < 1:
        raise ValueError("min_train_matches must be positive")
    data = _prepare_matches(matches)
    predictions: list[dict] = []
    for fold in folds:
        start, end = fold.bounds()
        train_all = data[data["kickoff"] < start]
        valid_all = data[(data["kickoff"] >= start) & (data["kickoff"] < end)]
        for league, valid in valid_all.groupby("league", sort=True):
            train = train_all[train_all["league"] == league]
            if len(train) < min_train_matches:
                continue
            model = fit_elo_davidson(train.rename(columns={"kickoff": "date"}), config=config)
            for kickoff, simultaneous in valid.groupby("kickoff", sort=True):
                pending: list[tuple] = []
                for row in simultaneous.itertuples(index=False):
                    cold_start = row.home_team not in model.teams or row.away_team not in model.teams
                    p_home, p_draw, p_away = model.probabilities(
                        str(row.home_team), str(row.away_team)
                    )
                    actual = (
                        "home"
                        if row.home_goals > row.away_goals
                        else "draw"
                        if row.home_goals == row.away_goals
                        else "away"
                    )
                    predictions.append(
                        {
                            "fold": fold.label,
                            "source_row_id": getattr(row, "source_row_id", None),
                            "league": league,
                            "kickoff": kickoff,
                            "home_team": row.home_team,
                            "away_team": row.away_team,
                            "home_goals": int(row.home_goals),
                            "away_goals": int(row.away_goals),
                            "p_home": p_home,
                            "p_draw": p_draw,
                            "p_away": p_away,
                            "actual": actual,
                            "cold_start": cold_start,
                        }
                    )
                    pending.append(row)
                for row in pending:
                    model.observe_match(
                        str(row.home_team),
                        str(row.away_team),
                        int(row.home_goals),
                        int(row.away_goals),
                    )
    return pd.DataFrame(predictions)


def walk_forward_negative_binomial(
    matches: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    l2_strength: float = 0.0,
    min_train_matches: int = 100,
    max_goals: int = 10,
) -> pd.DataFrame:
    """Generate frozen-fold forecasts from the independent-marginal NB2 model."""
    if min_train_matches < 1:
        raise ValueError("min_train_matches must be positive")
    data = _prepare_matches(matches)
    predictions: list[dict] = []
    for fold in folds:
        start, end = fold.bounds()
        train_all = data[data["kickoff"] < start]
        valid_all = data[(data["kickoff"] >= start) & (data["kickoff"] < end)]
        for league, valid in valid_all.groupby("league", sort=True):
            train = train_all[train_all["league"] == league]
            if len(train) < min_train_matches:
                continue
            model = fit_negative_binomial(
                train.rename(columns={"kickoff": "date"}),
                l2_strength=l2_strength,
            )
            for row in valid.itertuples(index=False):
                home_rate, away_rate = model.expected_goals(
                    str(row.home_team), str(row.away_team), unknown_strategy="neutral"
                )
                probabilities = market_probabilities(
                    model.score_matrix(
                        str(row.home_team),
                        str(row.away_team),
                        max_goals=max_goals,
                        unknown_strategy="neutral",
                    )
                )
                actual = (
                    "home"
                    if row.home_goals > row.away_goals
                    else "draw"
                    if row.home_goals == row.away_goals
                    else "away"
                )
                predictions.append(
                    {
                        "fold": fold.label,
                        "source_row_id": getattr(row, "source_row_id", None),
                        "league": league,
                        "kickoff": row.kickoff,
                        "home_team": row.home_team,
                        "away_team": row.away_team,
                        "home_goals": int(row.home_goals),
                        "away_goals": int(row.away_goals),
                        "home_rate": home_rate,
                        "away_rate": away_rate,
                        "alpha": model.alpha,
                        "p_home": probabilities["result.home"],
                        "p_draw": probabilities["result.draw"],
                        "p_away": probabilities["result.away"],
                        "actual": actual,
                        "cold_start": bool(
                            row.home_team not in model.teams or row.away_team not in model.teams
                        ),
                    }
                )
    return pd.DataFrame(predictions)


def calibrate_goal_predictions(
    predictions: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    min_history: int = 100,
) -> pd.DataFrame:
    """Apply temperature scaling without fitting on the fold being scored.

    Raw probabilities remain available under ``*_raw`` columns.  Rows outside
    the supplied folds, or folds without enough earlier forecasts, retain the
    raw probabilities and carry an explicit calibration status.
    """
    from model_buk.evaluation.calibration import (
        CalibrationFold,
        calibrate_chronologically,
    )

    required = {"kickoff", "actual", "p_home", "p_draw", "p_away"}
    missing = sorted(required - set(predictions.columns))
    if missing:
        raise ValueError(f"predictions is missing required columns: {', '.join(missing)}")
    if predictions.empty:
        raise ValueError("predictions is empty")
    frozen_folds = tuple(folds)
    calibration_folds = tuple(
        CalibrationFold(fold.label, fold.start, fold.end) for fold in frozen_folds
    )
    probabilities = predictions[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)
    result = calibrate_chronologically(
        probabilities,
        predictions["actual"].to_numpy(),
        predictions["kickoff"],
        calibration_folds,
        classes=("home", "draw", "away"),
        min_history=min_history,
    )
    output = predictions.copy()
    for index, name in enumerate(("p_home", "p_draw", "p_away")):
        output[f"{name}_raw"] = probabilities[:, index]
        output[name] = result.probabilities[:, index]
    output["calibrated"] = result.calibrated
    output["calibration_status"] = "outside_fold"
    kickoff = pd.DatetimeIndex(
        pd.to_datetime(output["kickoff"], errors="coerce", utc=True, format="mixed")
    )
    for report in result.folds:
        start, end = report.fold.bounds()
        mask = (kickoff >= start) & (kickoff < end)
        output.loc[mask, "calibration_status"] = report.status
    return output


def compare_goal_model_predictions(
    predictions_by_model: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Compare models on the intersection of exactly the same fixtures.

    The function deliberately performs no tuning and no winner promotion.  It
    is a paired diagnostic for already-frozen model configurations.
    """
    if len(predictions_by_model) < 2:
        raise ValueError("at least two model prediction sets are required")
    required = {
        "league", "kickoff", "home_team", "away_team", "actual",
        "p_home", "p_draw", "p_away",
    }
    prepared: dict[str, pd.DataFrame] = {}
    common_keys: set[str] | None = None
    for raw_name, frame in predictions_by_model.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("model names must not be blank")
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{name} predictions is missing required columns: {', '.join(missing)}")
        if frame.empty:
            raise ValueError(f"{name} predictions is empty")
        current = frame.copy()
        kickoff = pd.to_datetime(current["kickoff"], errors="coerce", utc=True, format="mixed")
        if kickoff.isna().any():
            raise ValueError(f"{name} predictions contains invalid kickoff values")
        current["_fixture_key"] = (
            current["league"].astype(str) + "|" + kickoff.astype(str) + "|"
            + current["home_team"].astype(str) + "|" + current["away_team"].astype(str)
        )
        if current["_fixture_key"].duplicated().any():
            raise ValueError(f"{name} predictions contains duplicate fixtures")
        keys = set(current["_fixture_key"])
        common_keys = keys if common_keys is None else common_keys & keys
        prepared[name] = current
    assert common_keys is not None
    if not common_keys:
        raise ValueError("model prediction sets have no common fixtures")

    ordered_keys = sorted(common_keys)
    reference_actual: dict[str, str] | None = None
    rows: list[dict[str, float | int | str]] = []
    for name, frame in prepared.items():
        paired = frame.set_index("_fixture_key").loc[ordered_keys].reset_index()
        actual = dict(zip(paired["_fixture_key"], paired["actual"], strict=True))
        if reference_actual is None:
            reference_actual = actual
        elif actual != reference_actual:
            raise ValueError("models disagree about actual outcomes for common fixtures")
        scores = score_goals_predictions(paired)
        rows.append(
            {
                "model": name,
                "available_n": int(len(frame)),
                "common_n": int(len(paired)),
                "paired_coverage": float(len(paired) / len(frame)),
                **scores,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["log_loss", "brier", "model"], ignore_index=True
    )


def compare_dixon_coles_configurations(
    matches: pd.DataFrame,
    folds: Iterable[GoalsFold],
    *,
    half_life_grid: Iterable[float | None],
    l2_grid: Iterable[float],
    xg_weight_grid: Iterable[float] = (0.0,),
    min_train_matches: int = 100,
    max_goals: int = 10,
) -> pd.DataFrame:
    """Evaluate decay and shrinkage settings on chronological validation folds."""
    frozen_folds = tuple(folds)
    rows: list[dict] = []
    for half_life_days in half_life_grid:
        for l2_strength in l2_grid:
            for xg_weight in xg_weight_grid:
                predictions = walk_forward_dixon_coles(
                    matches,
                    frozen_folds,
                    half_life_days=half_life_days,
                    l2_strength=l2_strength,
                    xg_weight=xg_weight,
                    min_train_matches=min_train_matches,
                    max_goals=max_goals,
                )
                if predictions.empty:
                    continue
                scores = score_goals_predictions(predictions)
                rows.append(
                    {
                        "half_life_days": half_life_days,
                        "l2_strength": float(l2_strength),
                        "xg_weight": float(xg_weight),
                        **scores,
                    }
                )
    if not rows:
        return pd.DataFrame(
            columns=[
                "half_life_days", "l2_strength", "xg_weight", "n",
                "log_loss", "brier", "accuracy",
            ]
        )
    return pd.DataFrame(rows).sort_values(
        ["log_loss", "brier", "l2_strength"],
        ignore_index=True,
    )


def score_goals_predictions(predictions: pd.DataFrame) -> dict[str, float | int]:
    """Score 1X2 probabilities with proper losses and explicit coverage.

    RPS complements log loss and Brier by respecting the natural ordering
    ``home < draw < away``.  A home/away miss is therefore penalised more than
    an adjacent home/draw or draw/away miss.
    """
    required = {"p_home", "p_draw", "p_away", "actual"}
    missing = sorted(required - set(predictions.columns))
    if missing:
        raise ValueError(f"predictions is missing required columns: {', '.join(missing)}")
    if predictions.empty:
        raise ValueError("predictions is empty")
    classes = ("home", "draw", "away")
    probability_columns = ("p_home", "p_draw", "p_away")
    probability_matrix = predictions.loc[:, probability_columns].to_numpy(dtype=float)
    if not np.isfinite(probability_matrix).all() or np.any(probability_matrix <= 0.0):
        raise ValueError("predictions contain invalid probabilities")
    probability_matrix = probability_matrix / probability_matrix.sum(axis=1, keepdims=True)
    class_index = {label: index for index, label in enumerate(classes)}
    try:
        actual_index = np.array([class_index[value] for value in predictions["actual"]])
    except KeyError as exc:
        raise ValueError(f"unknown actual class: {exc.args[0]}") from exc
    chosen = probability_matrix[np.arange(len(predictions)), actual_index]
    targets = np.eye(len(classes))[actual_index]
    predicted_index = probability_matrix.argmax(axis=1)
    cumulative_error = np.cumsum(probability_matrix, axis=1)[:, :-1] - np.cumsum(
        targets, axis=1
    )[:, :-1]
    return {
        "n": int(len(predictions)),
        "log_loss": float(-np.mean(np.log(np.clip(chosen, 1e-15, 1.0)))),
        "brier": float(np.mean(np.sum((probability_matrix - targets) ** 2, axis=1))),
        "rps": float(np.mean(np.sum(cumulative_error**2, axis=1) / (len(classes) - 1))),
        "accuracy": float(np.mean(predicted_index == actual_index)),
        "cold_start_rate": float(predictions.get("cold_start", pd.Series(False, index=predictions.index)).mean()),
    }


def build_market_1x2_benchmark(
    odds: pd.DataFrame,
    *,
    odds_kind: str,
    provider: str = "market_average",
) -> pd.DataFrame:
    """Create proportional de-vigged 1X2 probabilities for evaluation.

    The caller must choose ``odds_kind`` explicitly.  This makes use of closing
    prices visible at the call site and prevents an implicit look-ahead feature.
    """
    required = {"source_row_id", "provider", "odds_kind", "market", "selection", "odds"}
    missing = sorted(required - set(odds.columns))
    if missing:
        raise ValueError(f"odds is missing required columns: {', '.join(missing)}")
    if odds_kind not in {"opening", "closing"}:
        raise ValueError("odds_kind must be 'opening' or 'closing'")
    selected = odds[
        odds["provider"].eq(provider)
        & odds["odds_kind"].eq(odds_kind)
        & odds["market"].eq("1x2")
    ]
    wide = selected.pivot_table(
        index="source_row_id",
        columns="selection",
        values="odds",
        aggfunc="last",
    )
    if not {"home", "draw", "away"}.issubset(wide.columns):
        return pd.DataFrame(columns=["source_row_id", "market_p_home", "market_p_draw", "market_p_away"])
    wide = wide.dropna(subset=["home", "draw", "away"])
    inverse = 1.0 / wide[["home", "draw", "away"]].astype(float)
    probabilities = inverse.div(inverse.sum(axis=1), axis=0)
    probabilities = probabilities.rename(
        columns={
            "home": "market_p_home",
            "draw": "market_p_draw",
            "away": "market_p_away",
        }
    )
    return probabilities.reset_index()


def logarithmic_pool_1x2(
    predictions: pd.DataFrame,
    *,
    model_weight: float,
) -> pd.DataFrame:
    """Merge independent model and market probabilities with a log pool."""
    if not 0.0 <= model_weight <= 1.0:
        raise ValueError("model_weight must be between 0 and 1")
    model_columns = ["model_p_home", "model_p_draw", "model_p_away"]
    market_columns = ["market_p_home", "market_p_draw", "market_p_away"]
    missing = sorted(set(model_columns + market_columns) - set(predictions.columns))
    if missing:
        raise ValueError(f"predictions is missing pool columns: {', '.join(missing)}")
    model = predictions[model_columns].to_numpy(dtype=float)
    market = predictions[market_columns].to_numpy(dtype=float)
    if (
        not np.isfinite(model).all()
        or not np.isfinite(market).all()
        or np.any(model <= 0.0)
        or np.any(market <= 0.0)
    ):
        raise ValueError("pool probabilities must be finite and positive")
    log_pool = model_weight * np.log(model) + (1.0 - model_weight) * np.log(market)
    log_pool -= log_pool.max(axis=1, keepdims=True)
    pooled = np.exp(log_pool)
    pooled /= pooled.sum(axis=1, keepdims=True)
    output = predictions.copy()
    output[["p_home", "p_draw", "p_away"]] = pooled
    return output


def tune_logarithmic_pool_1x2(
    predictions: pd.DataFrame,
    *,
    model_weights: Iterable[float],
) -> pd.DataFrame:
    """Choose a model/market weight using chronological OOF predictions only."""
    rows: list[dict] = []
    for weight in model_weights:
        pooled = logarithmic_pool_1x2(predictions, model_weight=float(weight))
        scores = score_goals_predictions(pooled)
        rows.append({"model_weight": float(weight), **scores})
    if not rows:
        raise ValueError("model_weights must not be empty")
    return pd.DataFrame(rows).sort_values(["log_loss", "brier"], ignore_index=True)
