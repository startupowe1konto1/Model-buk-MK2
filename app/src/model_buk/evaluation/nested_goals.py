"""Leakage-safe nested walk-forward selection for football goal models.

The inner folds are used exclusively to choose a model configuration.  The
outer fold is deliberately evaluated only after that decision has been made,
so an outer outcome can never influence its own selection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from numbers import Real
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

import pandas as pd

from model_buk.goals_backtest import (
    GoalsFold,
    calibrate_goal_predictions,
    compare_goal_model_predictions,
    walk_forward_dixon_coles,
    walk_forward_gas_poisson,
    walk_forward_negative_binomial,
)
from model_buk.models.gas_poisson import GasPoissonConfig


GoalModelFamily = Literal["dixon_coles", "gas_poisson", "negative_binomial"]
_PROBABILITY_COLUMNS = ("p_home", "p_draw", "p_away")


def _utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("fold bounds must be valid timestamps")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _is_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


@dataclass(frozen=True)
class GoalModelCandidate:
    """One admissible model family and its frozen hyperparameters."""

    name: str
    family: GoalModelFamily
    parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("candidate name must not be blank")
        if self.family not in {"dixon_coles", "gas_poisson", "negative_binomial"}:
            raise ValueError("candidate family is not supported")
        parameters = dict(self.parameters)
        allowed = {
            "dixon_coles": {"half_life_days", "l2_strength", "xg_weight"},
            "gas_poisson": {"config"},
            "negative_binomial": {"l2_strength"},
        }[self.family]
        unknown = sorted(set(parameters) - allowed)
        if unknown:
            raise ValueError(
                f"{self.family} candidate has unsupported parameters: {', '.join(unknown)}"
            )
        if self.family == "dixon_coles":
            half_life = parameters.get("half_life_days")
            if half_life is not None and (not _is_number(half_life) or half_life <= 0):
                raise ValueError("half_life_days must be positive or None")
            for parameter in ("l2_strength", "xg_weight"):
                value = parameters.get(parameter)
                if value is not None and (not _is_number(value) or value < 0):
                    raise ValueError(f"{parameter} must be non-negative")
            xg_weight = parameters.get("xg_weight")
            if xg_weight is not None and xg_weight > 1:
                raise ValueError("xg_weight must be at most 1")
        elif self.family == "negative_binomial":
            l2_strength = parameters.get("l2_strength")
            if l2_strength is not None and (not _is_number(l2_strength) or l2_strength < 0):
                raise ValueError("l2_strength must be non-negative")
        else:
            config = parameters.get("config")
            if config is not None and not isinstance(config, GasPoissonConfig):
                raise TypeError("gas_poisson config must be a GasPoissonConfig")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "parameters", MappingProxyType(parameters))

    def audit_parameters(self) -> dict[str, object]:
        """Return serialisable configuration metadata for audit dataframes."""
        result: dict[str, object] = {}
        for key, value in self.parameters.items():
            result[key] = asdict(value) if isinstance(value, GasPoissonConfig) else value
        return result


@dataclass(frozen=True)
class NestedGoalsFold:
    """An outer holdout and the strictly earlier inner validation intervals."""

    label: str
    outer_fold: GoalsFold
    inner_folds: tuple[GoalsFold, ...]

    def __post_init__(self) -> None:
        label = self.label.strip()
        if not label:
            raise ValueError("nested fold label must not be blank")
        if not isinstance(self.outer_fold, GoalsFold):
            raise TypeError("outer_fold must be a GoalsFold")
        inner = tuple(self.inner_folds)
        if not inner:
            raise ValueError("inner_folds must not be empty")
        if not all(isinstance(fold, GoalsFold) for fold in inner):
            raise TypeError("inner_folds must contain GoalsFold values")
        outer_start, _ = self.outer_fold.bounds()
        outer_start = _utc_timestamp(outer_start)
        prior_end: pd.Timestamp | None = None
        labels: set[str] = set()
        for fold in inner:
            start, end = map(_utc_timestamp, fold.bounds())
            if fold.label in labels:
                raise ValueError("inner fold labels must be unique")
            if prior_end is not None and start < prior_end:
                raise ValueError("inner folds must be chronological and non-overlapping")
            if end > outer_start:
                raise ValueError("every inner fold must end on or before the outer fold start")
            labels.add(fold.label)
            prior_end = end
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "inner_folds", inner)


@dataclass(frozen=True)
class NestedGoalsResult:
    """Auditable results from nested model selection and held-out scoring."""

    inner_scores: pd.DataFrame
    selections: pd.DataFrame
    outer_predictions: pd.DataFrame


def _validate_inputs(
    candidates: Iterable[GoalModelCandidate],
    folds: Iterable[NestedGoalsFold],
    *,
    min_train_matches: int,
    max_goals: int,
    calibration_min_history: int,
) -> tuple[tuple[GoalModelCandidate, ...], tuple[NestedGoalsFold, ...]]:
    frozen_candidates = tuple(candidates)
    if len(frozen_candidates) < 2:
        raise ValueError("at least two candidates are required for nested selection")
    if not all(isinstance(candidate, GoalModelCandidate) for candidate in frozen_candidates):
        raise TypeError("candidates must contain GoalModelCandidate values")
    names = [candidate.name for candidate in frozen_candidates]
    if len(names) != len(set(names)):
        raise ValueError("candidate names must be unique")
    frozen_folds = tuple(folds)
    if not frozen_folds:
        raise ValueError("folds must not be empty")
    if not all(isinstance(fold, NestedGoalsFold) for fold in frozen_folds):
        raise TypeError("folds must contain NestedGoalsFold values")
    labels = [fold.label for fold in frozen_folds]
    if len(labels) != len(set(labels)):
        raise ValueError("nested fold labels must be unique")
    previous_end: pd.Timestamp | None = None
    for fold in frozen_folds:
        start, end = map(_utc_timestamp, fold.outer_fold.bounds())
        if previous_end is not None and start < previous_end:
            raise ValueError("outer folds must be chronological and non-overlapping")
        previous_end = end
    if min_train_matches < 1:
        raise ValueError("min_train_matches must be positive")
    if max_goals < 1:
        raise ValueError("max_goals must be positive")
    if calibration_min_history < 1:
        raise ValueError("calibration_min_history must be positive")
    return frozen_candidates, frozen_folds


def _walk_forward(
    candidate: GoalModelCandidate,
    matches: pd.DataFrame,
    folds: tuple[GoalsFold, ...],
    *,
    min_train_matches: int,
    max_goals: int,
) -> pd.DataFrame:
    params = dict(candidate.parameters)
    if candidate.family == "dixon_coles":
        return walk_forward_dixon_coles(
            matches, folds, min_train_matches=min_train_matches, max_goals=max_goals, **params
        )
    if candidate.family == "gas_poisson":
        return walk_forward_gas_poisson(
            matches, folds, min_train_matches=min_train_matches, max_goals=max_goals, **params
        )
    return walk_forward_negative_binomial(
        matches, folds, min_train_matches=min_train_matches, max_goals=max_goals, **params
    )


def _with_audit_metadata(predictions: pd.DataFrame, candidate: GoalModelCandidate, outer_label: str) -> pd.DataFrame:
    if predictions.empty:
        return predictions.copy()
    output = predictions.copy()
    output["outer_fold"] = outer_label
    output["selected_candidate"] = candidate.name
    output["selected_family"] = candidate.family
    output["selected_parameters"] = [candidate.audit_parameters()] * len(output)
    return output


def _calibrate_outer_from_inner(
    inner_predictions: pd.DataFrame,
    outer_predictions: pd.DataFrame,
    outer_fold: GoalsFold,
    *,
    min_history: int,
) -> pd.DataFrame:
    """Calibrate one selected outer interval using only its selected inner OOF rows."""
    calibration_input = pd.concat([inner_predictions, outer_predictions], ignore_index=True)
    calibrated = calibrate_goal_predictions(
        calibration_input, (outer_fold,), min_history=min_history
    )
    start, end = map(_utc_timestamp, outer_fold.bounds())
    kickoff = pd.DatetimeIndex(
        pd.to_datetime(calibrated["kickoff"], errors="coerce", utc=True, format="mixed")
    )
    return calibrated.loc[(kickoff >= start) & (kickoff < end)].reset_index(drop=True)


def nested_walk_forward_goals(
    matches: pd.DataFrame,
    candidates: Iterable[GoalModelCandidate],
    folds: Iterable[NestedGoalsFold],
    *,
    min_train_matches: int = 100,
    max_goals: int = 10,
    calibration_min_history: int = 100,
) -> NestedGoalsResult:
    """Select configurations on inner OOF forecasts then score selected outer forecasts.

    Selection uses a paired comparison over the intersection of each nested fold's
    inner OOF predictions.  No candidate is run against an outer interval
    until after the deterministic ``log_loss, brier, name`` tie-break.
    """
    frozen_candidates, frozen_folds = _validate_inputs(
        candidates,
        folds,
        min_train_matches=min_train_matches,
        max_goals=max_goals,
        calibration_min_history=calibration_min_history,
    )
    inner_audit: list[pd.DataFrame] = []
    selection_rows: list[dict[str, object]] = []
    outer_frames: list[pd.DataFrame] = []

    for nested_fold in frozen_folds:
        inner_predictions: dict[str, pd.DataFrame] = {}
        for candidate in frozen_candidates:
            predictions = _walk_forward(
                candidate,
                matches,
                nested_fold.inner_folds,
                min_train_matches=min_train_matches,
                max_goals=max_goals,
            )
            if predictions.empty:
                raise ValueError(
                    f"candidate {candidate.name!r} has no inner OOF predictions "
                    f"for nested fold {nested_fold.label!r}"
                )
            inner_predictions[candidate.name] = predictions

        comparison = compare_goal_model_predictions(inner_predictions).copy()
        candidate_by_name = {candidate.name: candidate for candidate in frozen_candidates}
        comparison.insert(0, "outer_fold", nested_fold.label)
        comparison["family"] = comparison["model"].map(
            lambda name: candidate_by_name[name].family
        )
        comparison["parameters"] = comparison["model"].map(
            lambda name: candidate_by_name[name].audit_parameters()
        )
        comparison["inner_fold_labels"] = [
            tuple(fold.label for fold in nested_fold.inner_folds)
        ] * len(comparison)
        inner_audit.append(comparison)

        # compare_goal_model_predictions already sorts log loss, Brier and name.
        winner_name = str(comparison.iloc[0]["model"])
        winner = candidate_by_name[winner_name]
        selection_rows.append(
            {
                "outer_fold": nested_fold.label,
                "selected_candidate": winner.name,
                "selected_family": winner.family,
                "selected_parameters": winner.audit_parameters(),
                "selection_log_loss": float(comparison.iloc[0]["log_loss"]),
                "selection_brier": float(comparison.iloc[0]["brier"]),
                "selection_common_n": int(comparison.iloc[0]["common_n"]),
                "inner_fold_labels": tuple(fold.label for fold in nested_fold.inner_folds),
            }
        )
        outer_predictions = _walk_forward(
            winner,
            matches,
            (nested_fold.outer_fold,),
            min_train_matches=min_train_matches,
            max_goals=max_goals,
        )
        if outer_predictions.empty:
            raise ValueError(
                f"selected candidate {winner.name!r} has no outer predictions "
                f"for nested fold {nested_fold.label!r}"
            )
        outer_frames.append(
            _calibrate_outer_from_inner(
                inner_predictions[winner.name],
                _with_audit_metadata(outer_predictions, winner, nested_fold.label),
                nested_fold.outer_fold,
                min_history=calibration_min_history,
            )
        )

    combined_outer = pd.concat(outer_frames, ignore_index=True)
    # Guard against changes in a calibrator implementation: this API always
    # returns valid 1X2 probability mass to downstream betting evaluation.
    if not (
        combined_outer.loc[:, _PROBABILITY_COLUMNS].sum(axis=1).round(12) == 1.0
    ).all():
        raise RuntimeError("calibrated outer probabilities are not normalised")
    selections = pd.DataFrame(selection_rows)
    status_by_outer = combined_outer.groupby("outer_fold", sort=False)["calibration_status"].agg(
        lambda values: tuple(sorted(set(values)))
    )
    selections["calibration_status"] = selections["outer_fold"].map(status_by_outer)
    return NestedGoalsResult(
        inner_scores=pd.concat(inner_audit, ignore_index=True),
        selections=selections,
        outer_predictions=combined_outer,
    )


__all__ = [
    "GoalModelCandidate",
    "GoalModelFamily",
    "NestedGoalsFold",
    "NestedGoalsResult",
    "nested_walk_forward_goals",
]
