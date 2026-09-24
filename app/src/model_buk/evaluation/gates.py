"""Auditable model-versus-market evaluation and conservative promotion gates.

The functions in this module deliberately operate on out-of-sample predictions.
They do not construct folds or join odds: callers must do that chronologically
before evaluating.  Market probabilities are expected to be de-vigged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable, Sequence

import numpy as np


_EPSILON = 1e-15


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float
    absolute_gap: float


@dataclass(frozen=True)
class CalibrationReport:
    ece: float
    bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class ProbabilityEvaluation:
    n: int
    log_loss: float
    brier: float
    ece: float
    calibration_bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    confidence_level: float
    n_resamples: int
    seed: int


@dataclass(frozen=True)
class BettingEvaluation:
    opportunities: int
    priceable: int
    bets: int
    coverage: float
    selectivity: float
    total_staked: float
    total_profit: float
    roi: float
    yield_rate: float
    win_rate: float
    average_odds: float
    net_returns: tuple[float, ...]


@dataclass(frozen=True)
class SelectionReliability:
    """Does the model deliver what it promised *on the legs it chose to bet*?

    Aggregate calibration answers a different question.  It is computed over
    every row, so it is dominated by the many fixtures the gate never touches,
    and a model can be honest there while being wrong exactly where it stakes
    money.

    The market column is the control that makes this readable.  A negative
    ``model_overstatement`` alone proves nothing: selections are chosen
    *because* the model disagreed with the market upwards, so even a model with
    no systematic bias will overstate on that subset - the noise that triggered
    the selection is the same noise that regresses.  What the winner's curse
    cannot explain is the model overstating much more than the market does on
    the *same* legs: if those fixtures were merely hard, the market would miss
    them too.

    ``excess_overstatement`` is therefore the number to read, and it follows the
    sign convention of ``ece_regression``: positive means the model is worse.
    """

    n: int
    mean_model_probability: float
    mean_market_probability: float
    realised_rate: float
    model_overstatement: float
    market_overstatement: float
    excess_overstatement: float


@dataclass(frozen=True)
class EvaluationReport:
    model: ProbabilityEvaluation
    market: ProbabilityEvaluation
    log_loss_improvement: float
    brier_improvement: float
    ece_regression: float
    log_loss_improvement_ci: BootstrapInterval
    betting: BettingEvaluation | None = None
    net_yield_ci: BootstrapInterval | None = None
    selection: SelectionReliability | None = None


@dataclass(frozen=True)
class PromotionPolicy:
    """Thresholds that must all pass before a candidate can be promoted."""

    min_samples: int = 500
    min_log_loss_improvement: float = 0.0
    max_brier_regression: float = 0.0
    max_ece_regression: float = 0.01
    max_model_ece: float | None = None
    min_bets: int = 0
    min_net_yield_lower_bound: float | None = None

    def __post_init__(self) -> None:
        if self.min_samples < 1:
            raise ValueError("min_samples must be positive")
        if self.max_brier_regression < 0.0:
            raise ValueError("max_brier_regression must be non-negative")
        if self.max_ece_regression < 0.0:
            raise ValueError("max_ece_regression must be non-negative")
        if self.max_model_ece is not None and not 0.0 <= self.max_model_ece <= 1.0:
            raise ValueError("max_model_ece must be between 0 and 1")
        if self.min_bets < 0:
            raise ValueError("min_bets must be non-negative")


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    checks: dict[str, bool]
    reasons: tuple[str, ...]


def _probability_inputs(
    actual: Sequence[Hashable] | np.ndarray,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    classes: Sequence[Hashable] | None,
) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.asarray(probabilities, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("probabilities must be a two-dimensional matrix with at least two classes")
    labels = np.asarray(actual)
    if labels.ndim != 1 or len(labels) != len(matrix):
        raise ValueError("actual must be one-dimensional and match probability rows")
    if len(labels) == 0:
        raise ValueError("evaluation inputs must not be empty")
    if not np.isfinite(matrix).all():
        raise ValueError("probabilities must be finite")
    if np.any(matrix < 0.0):
        raise ValueError("probabilities must be non-negative")
    row_sums = matrix.sum(axis=1)
    if np.any(row_sums <= 0.0):
        raise ValueError("each probability row must have positive mass")
    matrix = matrix / row_sums[:, None]

    if classes is None:
        if np.issubdtype(labels.dtype, np.integer):
            indices = labels.astype(int)
            if np.any(indices < 0) or np.any(indices >= matrix.shape[1]):
                raise ValueError("actual class index is outside the probability matrix")
            return indices, matrix
        canonical = ("home", "draw", "away")
        values = set(labels.tolist())
        if matrix.shape[1] == 3 and values.issubset(canonical):
            classes = canonical
        else:
            raise ValueError("classes must be supplied for non-integer labels")
    if len(classes) != matrix.shape[1] or len(set(classes)) != len(classes):
        raise ValueError("classes must be unique and match probability columns")
    class_index = {label: index for index, label in enumerate(classes)}
    try:
        indices = np.asarray([class_index[label] for label in labels], dtype=int)
    except KeyError as exc:
        raise ValueError(f"unknown actual class: {exc.args[0]}") from exc
    return indices, matrix


def _per_observation_log_loss(indices: np.ndarray, probabilities: np.ndarray) -> np.ndarray:
    chosen = probabilities[np.arange(len(indices)), indices]
    return -np.log(np.clip(chosen, _EPSILON, 1.0))


def multiclass_log_loss(
    actual: Sequence[Hashable] | np.ndarray,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
) -> float:
    indices, matrix = _probability_inputs(actual, probabilities, classes)
    return float(_per_observation_log_loss(indices, matrix).mean())


def multiclass_brier_score(
    actual: Sequence[Hashable] | np.ndarray,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
) -> float:
    """Return the multiclass Brier score (sum across classes, mean across rows)."""
    indices, matrix = _probability_inputs(actual, probabilities, classes)
    targets = np.eye(matrix.shape[1], dtype=float)[indices]
    return float(np.sum((matrix - targets) ** 2, axis=1).mean())


def calibration_report(
    actual: Sequence[Hashable] | np.ndarray,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
    bins: int = 10,
) -> CalibrationReport:
    """Measure top-label calibration with equal-width confidence bins."""
    if bins < 2:
        raise ValueError("bins must be at least 2")
    indices, matrix = _probability_inputs(actual, probabilities, classes)
    confidence = matrix.max(axis=1)
    correct = (matrix.argmax(axis=1) == indices).astype(float)
    bin_index = np.minimum((confidence * bins).astype(int), bins - 1)
    rows: list[CalibrationBin] = []
    weighted_gap = 0.0
    for index in range(bins):
        mask = bin_index == index
        count = int(mask.sum())
        if count == 0:
            continue
        mean_confidence = float(confidence[mask].mean())
        accuracy = float(correct[mask].mean())
        gap = abs(mean_confidence - accuracy)
        weighted_gap += count * gap
        rows.append(
            CalibrationBin(
                lower=index / bins,
                upper=(index + 1) / bins,
                count=count,
                mean_confidence=mean_confidence,
                accuracy=accuracy,
                absolute_gap=gap,
            )
        )
    return CalibrationReport(ece=float(weighted_gap / len(indices)), bins=tuple(rows))


def probability_evaluation(
    actual: Sequence[Hashable] | np.ndarray,
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
    calibration_bins: int = 10,
) -> ProbabilityEvaluation:
    indices, matrix = _probability_inputs(actual, probabilities, classes)
    calibration = calibration_report(indices, matrix, bins=calibration_bins)
    targets = np.eye(matrix.shape[1], dtype=float)[indices]
    return ProbabilityEvaluation(
        n=len(indices),
        log_loss=float(_per_observation_log_loss(indices, matrix).mean()),
        brier=float(np.sum((matrix - targets) ** 2, axis=1).mean()),
        ece=calibration.ece,
        calibration_bins=calibration.bins,
    )


def bootstrap_mean_interval(
    values: Iterable[float] | np.ndarray,
    *,
    confidence_level: float = 0.95,
    n_resamples: int = 2_000,
    seed: int = 0,
) -> BootstrapInterval:
    """Return a deterministic percentile bootstrap interval for a mean."""
    sample = np.asarray(tuple(values), dtype=float)
    if sample.ndim != 1 or len(sample) == 0 or not np.isfinite(sample).all():
        raise ValueError("values must be a non-empty finite one-dimensional sample")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples, dtype=float)
    # Chunking avoids allocating n_resamples * n observations for large holdouts.
    for start in range(0, n_resamples, 256):
        stop = min(start + 256, n_resamples)
        indices = rng.integers(0, len(sample), size=(stop - start, len(sample)))
        means[start:stop] = sample[indices].mean(axis=1)
    tail = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(means, [tail, 1.0 - tail])
    return BootstrapInterval(
        estimate=float(sample.mean()),
        lower=float(lower),
        upper=float(upper),
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        seed=seed,
    )


def bootstrap_cluster_mean_interval(
    values: Iterable[float] | np.ndarray,
    clusters: Iterable[Hashable] | np.ndarray,
    *,
    confidence_level: float = 0.95,
    n_resamples: int = 2_000,
    seed: int = 0,
) -> BootstrapInterval:
    """Return a percentile bootstrap interval after resampling whole clusters.

    A resample draws clusters with replacement and includes every observation
    belonging to each drawn cluster.  Consequently the statistic remains the
    observation-level mean while preserving within-cluster dependence.
    """
    try:
        sample = np.asarray(tuple(values), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("values must be a non-empty finite one-dimensional sample") from exc
    if sample.ndim != 1 or len(sample) == 0 or not np.isfinite(sample).all():
        raise ValueError("values must be a non-empty finite one-dimensional sample")
    try:
        cluster_values = tuple(clusters)
    except TypeError as exc:
        raise ValueError("clusters must be a one-dimensional sequence") from exc
    if len(cluster_values) != len(sample):
        raise ValueError("clusters must be one-dimensional and match values")
    if len(cluster_values) == 0:
        raise ValueError("clusters must be a non-empty one-dimensional sample")
    groups: dict[Hashable, list[int]] = {}
    for index, label in enumerate(cluster_values):
        try:
            hash(label)
        except TypeError as exc:
            raise ValueError("clusters must contain hashable labels") from exc
        try:
            missing = bool(label != label)
        except (TypeError, ValueError):
            missing = True
        if label is None or missing:
            raise ValueError("clusters must contain non-missing labels")
        groups.setdefault(label, []).append(index)
    try:
        level = float(confidence_level)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence_level must be between 0 and 1") from exc
    if not np.isfinite(level) or not 0.0 < level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    if isinstance(n_resamples, (bool, np.bool_)) or not isinstance(
        n_resamples, (int, np.integer)
    ) or n_resamples < 1:
        raise ValueError("n_resamples must be positive")

    group_indices = tuple(np.asarray(indices, dtype=int) for indices in groups.values())
    group_sums = np.asarray([sample[indices].sum() for indices in group_indices], dtype=float)
    group_sizes = np.asarray([len(indices) for indices in group_indices], dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples, dtype=float)
    # Sampling cluster sums/sizes avoids materialising a large ragged matrix.
    for start in range(0, n_resamples, 256):
        stop = min(start + 256, n_resamples)
        choices = rng.integers(0, len(group_indices), size=(stop - start, len(group_indices)))
        means[start:stop] = (
            group_sums[choices].sum(axis=1) / group_sizes[choices].sum(axis=1)
        )
    tail = (1.0 - level) / 2.0
    lower, upper = np.quantile(means, [tail, 1.0 - tail])
    return BootstrapInterval(
        estimate=float(sample.mean()),
        lower=float(lower),
        upper=float(upper),
        confidence_level=level,
        n_resamples=n_resamples,
        seed=seed,
    )


def _broadcast_cost(value: float | Sequence[float], size: int) -> np.ndarray:
    costs = np.asarray(value, dtype=float)
    if costs.ndim == 0:
        costs = np.full(size, float(costs))
    if costs.shape != (size,) or not np.isfinite(costs).all() or np.any(costs < 0.0):
        raise ValueError("execution_cost must be non-negative and scalar or one value per row")
    return costs


def evaluate_betting_returns(
    wins: Sequence[bool] | np.ndarray,
    decimal_odds: Sequence[float] | np.ndarray,
    *,
    selected: Sequence[bool] | np.ndarray | None = None,
    available: Sequence[bool] | np.ndarray | None = None,
    tax_multiplier: float = 1.0,
    execution_cost: float | Sequence[float] = 0.0,
) -> BettingEvaluation:
    """Evaluate one-unit bets after stake tax and per-bet execution cost.

    ``coverage`` is the share of opportunities with an executable quote;
    ``selectivity`` is the share of priceable opportunities actually selected.
    With flat one-unit stakes, ROI and yield are numerically identical.
    """
    outcomes = np.asarray(wins, dtype=bool)
    odds = np.asarray(decimal_odds, dtype=float)
    if outcomes.ndim != 1 or odds.shape != outcomes.shape or len(outcomes) == 0:
        raise ValueError("wins and decimal_odds must be matching non-empty vectors")
    if not 0.0 < tax_multiplier <= 1.0:
        raise ValueError("tax_multiplier must be in (0, 1]")
    inferred_available = np.isfinite(odds) & (odds > 1.0)
    if available is None:
        available_mask = inferred_available
    else:
        available_mask = np.asarray(available, dtype=bool)
        if available_mask.shape != outcomes.shape:
            raise ValueError("available must match wins")
        if np.any(available_mask & ~inferred_available):
            raise ValueError("available rows must have finite decimal odds greater than 1")
    if selected is None:
        selected_mask = available_mask.copy()
    else:
        selected_mask = np.asarray(selected, dtype=bool)
        if selected_mask.shape != outcomes.shape:
            raise ValueError("selected must match wins")
    if np.any(selected_mask & ~available_mask):
        raise ValueError("selected bets must be a subset of available quotes")

    costs = _broadcast_cost(execution_cost, len(outcomes))
    chosen_wins = outcomes[selected_mask]
    chosen_odds = odds[selected_mask]
    chosen_costs = costs[selected_mask]
    returns = np.where(
        chosen_wins,
        tax_multiplier * chosen_odds - 1.0,
        -1.0,
    ) - chosen_costs
    bets = len(returns)
    priceable = int(available_mask.sum())
    total_profit = float(returns.sum())
    roi = total_profit / bets if bets else float("nan")
    return BettingEvaluation(
        opportunities=len(outcomes),
        priceable=priceable,
        bets=bets,
        coverage=float(priceable / len(outcomes)),
        selectivity=float(bets / priceable) if priceable else 0.0,
        total_staked=float(bets),
        total_profit=total_profit,
        roi=float(roi),
        yield_rate=float(roi),
        win_rate=float(chosen_wins.mean()) if bets else float("nan"),
        average_odds=float(chosen_odds.mean()) if bets else float("nan"),
        net_returns=tuple(float(value) for value in returns),
    )


def _selection_indices(
    selections: Sequence[Hashable] | np.ndarray | None,
    model_matrix: np.ndarray,
    classes: Sequence[Hashable] | None,
) -> np.ndarray:
    if selections is None:
        return model_matrix.argmax(axis=1)
    values = np.asarray(selections)
    if values.shape != (len(model_matrix),):
        raise ValueError("bet_selections must contain one selection per row")
    if np.issubdtype(values.dtype, np.integer):
        indices = values.astype(int)
    else:
        if classes is None:
            raise ValueError("classes are required for labelled bet_selections")
        mapping = {label: index for index, label in enumerate(classes)}
        try:
            indices = np.asarray([mapping[value] for value in values], dtype=int)
        except KeyError as exc:
            raise ValueError(f"unknown bet selection: {exc.args[0]}") from exc
    if np.any(indices < 0) or np.any(indices >= model_matrix.shape[1]):
        raise ValueError("bet selection index is outside the odds matrix")
    return indices


def selection_reliability(
    hit: Sequence[bool] | np.ndarray,
    model_probability: Sequence[float] | np.ndarray,
    market_probability: Sequence[float] | np.ndarray,
    *,
    selected: Sequence[bool] | np.ndarray | None = None,
) -> SelectionReliability | None:
    """Compare promise against outcome on the selected legs only.

    All three inputs are per-row and describe the *same* selection: whether it
    won, what the model said about it, and what the de-vigged market said about
    it.  Keeping the outcome column shared is what makes the two overstatements
    comparable; scoring the model and the market on different subsets would
    measure nothing.

    Returns ``None`` when nothing was selected, because an overstatement
    computed on zero legs is not a weaker signal - it is the absence of one.
    """

    hits = np.asarray(hit, dtype=bool)
    model_values = np.asarray(model_probability, dtype=float)
    market_values = np.asarray(market_probability, dtype=float)
    if not (hits.shape == model_values.shape == market_values.shape):
        raise ValueError("hit, model_probability and market_probability must align")
    if hits.ndim != 1:
        raise ValueError("selection reliability expects one value per row")
    if selected is None:
        mask = np.ones_like(hits, dtype=bool)
    else:
        mask = np.asarray(selected, dtype=bool)
        if mask.shape != hits.shape:
            raise ValueError("selected must contain one flag per row")
    if not mask.any():
        return None

    chosen_hits = hits[mask]
    chosen_model = model_values[mask]
    chosen_market = market_values[mask]
    realised = float(chosen_hits.mean())
    mean_model = float(chosen_model.mean())
    mean_market = float(chosen_market.mean())
    model_overstatement = mean_model - realised
    market_overstatement = mean_market - realised
    return SelectionReliability(
        n=int(mask.sum()),
        mean_model_probability=mean_model,
        mean_market_probability=mean_market,
        realised_rate=realised,
        model_overstatement=float(model_overstatement),
        market_overstatement=float(market_overstatement),
        excess_overstatement=float(model_overstatement - market_overstatement),
    )


def evaluate_model_against_market(
    actual: Sequence[Hashable] | np.ndarray,
    model_probabilities: Sequence[Sequence[float]] | np.ndarray,
    market_probabilities: Sequence[Sequence[float]] | np.ndarray,
    *,
    classes: Sequence[Hashable] | None = None,
    calibration_bins: int = 10,
    confidence_level: float = 0.95,
    bootstrap_resamples: int = 2_000,
    seed: int = 0,
    bootstrap_clusters: Sequence[Hashable] | np.ndarray | None = None,
    offered_odds: Sequence[Sequence[float]] | np.ndarray | None = None,
    bet_selections: Sequence[Hashable] | np.ndarray | None = None,
    selected: Sequence[bool] | np.ndarray | None = None,
    tax_multiplier: float = 1.0,
    execution_cost: float | Sequence[float] = 0.0,
) -> EvaluationReport:
    """Build one report from paired model and de-vigged market forecasts."""
    actual_indices, model_matrix = _probability_inputs(
        actual, model_probabilities, classes
    )
    market_indices, market_matrix = _probability_inputs(
        actual, market_probabilities, classes
    )
    if not np.array_equal(actual_indices, market_indices) or model_matrix.shape != market_matrix.shape:
        raise ValueError("model and market probabilities must describe the same rows and classes")

    model = probability_evaluation(
        actual_indices, model_matrix, calibration_bins=calibration_bins
    )
    market = probability_evaluation(
        actual_indices, market_matrix, calibration_bins=calibration_bins
    )
    improvement_values = (
        _per_observation_log_loss(actual_indices, market_matrix)
        - _per_observation_log_loss(actual_indices, model_matrix)
    )
    bootstrap_cluster_values: tuple[Hashable, ...] | None = None
    if bootstrap_clusters is None:
        improvement_ci = bootstrap_mean_interval(
            improvement_values,
            confidence_level=confidence_level,
            n_resamples=bootstrap_resamples,
            seed=seed,
        )
    else:
        bootstrap_cluster_values = tuple(bootstrap_clusters)
        if len(bootstrap_cluster_values) != len(improvement_values):
            raise ValueError("bootstrap_clusters must match evaluation rows")
        improvement_ci = bootstrap_cluster_mean_interval(
            improvement_values,
            bootstrap_cluster_values,
            confidence_level=confidence_level,
            n_resamples=bootstrap_resamples,
            seed=seed,
        )

    betting: BettingEvaluation | None = None
    yield_ci: BootstrapInterval | None = None
    selection_report: SelectionReliability | None = None
    if offered_odds is not None:
        odds_matrix = np.asarray(offered_odds, dtype=float)
        if odds_matrix.shape != model_matrix.shape:
            raise ValueError("offered_odds must match the probability matrix")
        selection_indices = _selection_indices(bet_selections, model_matrix, classes)
        row_indices = np.arange(len(actual_indices))
        chosen_odds = odds_matrix[row_indices, selection_indices]
        selection_hits = actual_indices == selection_indices
        betting = evaluate_betting_returns(
            selection_hits,
            chosen_odds,
            selected=selected,
            tax_multiplier=tax_multiplier,
            execution_cost=execution_cost,
        )
        # Derived once and reused by the cluster bootstrap below: two independent
        # copies of this expression could drift apart while both keep passing.
        # No intersection with availability is needed - evaluate_betting_returns
        # has already rejected any mask that is not a subset of the priceable
        # rows, so by this line the two are the same set.
        available_mask = np.isfinite(chosen_odds) & (chosen_odds > 1.0)
        selected_mask = (
            available_mask.copy() if selected is None else np.asarray(selected, dtype=bool)
        )
        # Scored on the legs that were actually bet, with the market judged on
        # those same legs - see SelectionReliability for why the control matters.
        selection_report = selection_reliability(
            selection_hits,
            model_matrix[row_indices, selection_indices],
            market_matrix[row_indices, selection_indices],
            selected=selected_mask,
        )
        if betting.bets:
            if bootstrap_clusters is None:
                yield_ci = bootstrap_mean_interval(
                    betting.net_returns,
                    confidence_level=confidence_level,
                    n_resamples=bootstrap_resamples,
                    seed=seed + 1,
                )
            else:
                selected_clusters = tuple(
                    label
                    for label, is_bet in zip(bootstrap_cluster_values, selected_mask)
                    if is_bet
                )
                yield_ci = bootstrap_cluster_mean_interval(
                    betting.net_returns,
                    selected_clusters,
                    confidence_level=confidence_level,
                    n_resamples=bootstrap_resamples,
                    seed=seed + 1,
                )

    return EvaluationReport(
        model=model,
        market=market,
        log_loss_improvement=float(market.log_loss - model.log_loss),
        brier_improvement=float(market.brier - model.brier),
        ece_regression=float(model.ece - market.ece),
        log_loss_improvement_ci=improvement_ci,
        betting=betting,
        net_yield_ci=yield_ci,
        selection=selection_report,
    )


def decide_promotion(
    report: EvaluationReport,
    policy: PromotionPolicy | None = None,
) -> PromotionDecision:
    """Promote only when every evidence, calibration, and optional ROI gate passes."""
    policy = policy or PromotionPolicy()
    checks: dict[str, bool] = {
        "enough_evaluation_samples": report.model.n >= policy.min_samples,
        "log_loss_improvement_point": (
            report.market.log_loss - report.model.log_loss
            > policy.min_log_loss_improvement
        ),
        "log_loss_improvement_confident": (
            report.log_loss_improvement_ci.lower > policy.min_log_loss_improvement
        ),
        "brier_regression_acceptable": (
            report.model.brier - report.market.brier <= policy.max_brier_regression
        ),
        "calibration_regression_acceptable": (
            report.model.ece - report.market.ece <= policy.max_ece_regression
        ),
    }
    if policy.max_model_ece is not None:
        checks["absolute_calibration_acceptable"] = report.model.ece <= policy.max_model_ece
    if policy.min_bets > 0:
        checks["enough_bets"] = report.betting is not None and report.betting.bets >= policy.min_bets
    if policy.min_net_yield_lower_bound is not None:
        checks["net_yield_confident"] = (
            report.net_yield_ci is not None
            and report.net_yield_ci.lower >= policy.min_net_yield_lower_bound
        )

    labels = {
        "enough_evaluation_samples": "insufficient evaluation samples",
        "log_loss_improvement_point": "model log loss does not improve on the market",
        "log_loss_improvement_confident": "log-loss improvement is not supported by the confidence interval",
        "brier_regression_acceptable": "Brier score regression exceeds policy",
        "calibration_regression_acceptable": "calibration regression exceeds policy",
        "absolute_calibration_acceptable": "model ECE exceeds policy",
        "enough_bets": "insufficient selected bets",
        "net_yield_confident": "net yield confidence bound is below policy",
    }
    reasons = tuple(labels[name] for name, passed in checks.items() if not passed)
    return PromotionDecision(promote=all(checks.values()), checks=checks, reasons=reasons)


# Concise aliases for consumers that prefer domain-generic names.
evaluate_multiclass = evaluate_model_against_market
promotion_decision = decide_promotion
