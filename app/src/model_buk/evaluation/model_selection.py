"""Compare stored forecasts of many models on identical matches and choose among them without peeking.

Plan, days 3-4 ("question 1": does a model predict well?).  Every model of the fixed goal protocol
(:mod:`model_buk.evaluation.protocol`) has stored out-of-sample forecasts in
``predictions/<model>.sqlite``.  This module reads them into one :class:`ForecastPanel` and answers:

1. *Which model is better, and by how much?*  All models are scored on the **same** matches, the
   intersection of the non-missing forecasts of every compared model (:meth:`ForecastPanel.common_mask`);
   :func:`coverage_report` says how many matches each model lost and why.  Per league and pooled:
   log-loss (primary), Brier, RPS with matchday-cluster bootstrap intervals
   (:func:`score_models`), paired Diebold-Mariano tests against the baseline and against the best other
   model with Holm / Benjamini-Hochberg across leagues and Bonferroni intervals that hold for all leagues
   at once (:func:`paired_comparison`), and CORP reliability (:func:`corp_table`).
2. *Which model would we have picked in advance?*  :func:`run_selection` walks forward over the scored
   seasons ``S``: the choice for ``S`` uses only the forecasts of season ``selection_season(S) = S - 1``
   (themselves out-of-sample), the choice is then scored on ``S``.  Two rules are provided: the model with
   the best pooled log-loss (:func:`select_model`) and a log-linear pool of all candidates with weights
   fitted on ``S - 1`` (:func:`fit_log_linear_pool`; a linear pool and a per-league choice shrunk to the
   pooled one are kept as sensitivity checks).  The first season of the archive can only be a selection
   season: nothing is chosen for it and its rows stay NaN (missing stays missing).
3. *Does the selection really not peek?*  :func:`check_selection_no_lookahead` rewrites the outcomes of
   the scored season and all later seasons; no forecast of the scored season may change.  It is the
   analogue of :func:`model_buk.evaluation.framework.check_no_lookahead` and is mutation-checked in the tests
   by replacing :func:`selection_season`.

A forecast row that is NaN means "no forecast" everywhere in this module; it is never scored as a zero
and never filled.  A fixture missing from a model's table entirely counts as missing in the same way.

Derived forecasts (:class:`SelectionRun`) become :class:`~model_buk.evaluation.framework.WalkForwardResult`
objects (:func:`derived_result`) so :func:`model_buk.evaluation.protocol.save_result` stores them in the
same format as the model forecasts.

Known limitation shared with the framework's pooled hyper-parameter choice: "season ``S - 1``" is the
provider's season label.  Leagues with calendar-year seasons start before the European season with the
previous label has ended, so a pooled choice for ``S`` may include a few results (March to May of ``S``)
that were played after such a league's first ``S`` kickoff.  No outcome of season ``S`` itself is used.
:func:`calendar_year_check` measures the effect by re-running the selection on matches that kicked off before
the first ``S`` match of any league; the script stores that table next to the other comparison tables.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import optimize, stats

from model_buk.evaluation.framework import Market, WalkForwardResult
from model_buk.evaluation.protocol import COMPARISON_CONFIG, PREDICTIONS_DIR, load_predictions
from model_buk.evaluation.reliability import corp_by_class
from model_buk.evaluation.scoring import (
    METRICS,
    check_probabilities,
    log_loss_per_match,
    per_match_scores,
    score_table,
)
from model_buk.evaluation.significance import (
    benjamini_hochberg_adjust,
    cluster_bootstrap_mean_ci,
    diebold_mariano,
    holm_adjust,
)

BASELINE_NAME = "baseline_league_freq"
SELECTED_NAME = "selected_best"
SELECTED_LEAGUE_NAME = "selected_best_league"
POOL_NAME = "pool_models"
POOL_LINEAR_NAME = "pool_models_linear"
FIXED_MODELS: tuple[str, ...] = ("dixon_coles", "nb2", "gas_poisson", "elo_davidson", "pi_gbm", "pi_ratings")
POOLED_LABEL = "ALL"
_IDENTICAL_LOSS = 1e-12  # per-match loss differences below this are floating point dust, not a difference
DEFAULT_RIDGE = 1e-4

_META_COLUMNS = ("fixture_id", "league_code", "season", "matchday", "y")


class SelectionLeakError(AssertionError):
    """Raised when a selection or pool weight depends on outcomes of the season it is scored on."""


# ---------------------------------------------------------------------------
# Panel of stored forecasts
# ---------------------------------------------------------------------------


def _kickoff_ns(values: pd.Series | Sequence) -> np.ndarray:
    stamp = pd.to_datetime(pd.Series(values), utc=True, format="mixed", errors="raise").dt.as_unit("ns")
    return stamp.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64")


@dataclass
class ForecastPanel:
    """Forecasts of several models for one market, aligned on ``fixture_id`` (one row per fixture).

    ``meta`` holds ``fixture_id, league_code, season, matchday, kickoff_ns, kickoff, y`` sorted by
    ``(kickoff_ns, fixture_id)``; ``probs[model]`` is an ``(n, K)`` matrix in the market's class order
    where a NaN row means "no forecast".  ``present[model]`` says whether the fixture appears in the
    model's stored table at all (a fixture that is absent is missing in the same way as a NaN row; the
    distinction is only reported).
    """

    market: Market
    meta: pd.DataFrame
    probs: dict[str, np.ndarray]
    present: dict[str, np.ndarray]

    def __len__(self) -> int:
        return len(self.meta)

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(self.probs)

    @property
    def y(self) -> np.ndarray:
        return self.meta["y"].to_numpy(dtype=np.int64)

    @property
    def season(self) -> np.ndarray:
        return self.meta["season"].to_numpy(dtype=np.int64)

    @property
    def league(self) -> np.ndarray:
        return self.meta["league_code"].to_numpy(dtype=object)

    @property
    def matchday(self) -> np.ndarray:
        return self.meta["matchday"].to_numpy(dtype=object)

    @property
    def kickoff_ns(self) -> np.ndarray:
        return self.meta["kickoff_ns"].to_numpy(dtype=np.int64)

    def valid(self, model: str) -> np.ndarray:
        """Rows in which ``model`` has a forecast."""
        return ~np.isnan(self.probs[model]).any(axis=1)

    def common_mask(self, models: Iterable[str] | None = None, seasons: Iterable[int] | None = None) -> np.ndarray:
        """Rows every listed model forecast (optionally only in ``seasons``): the fair comparison sample."""
        names = tuple(models) if models is not None else self.models
        if not names:
            raise ValueError("no models to intersect")
        mask = np.ones(len(self), dtype=bool)
        for name in names:
            mask &= self.valid(name)
        if seasons is not None:
            mask &= np.isin(self.season, [int(s) for s in seasons])
        return mask

    def losses(self, model: str, metric: str = "log_loss") -> np.ndarray:
        """Per-match loss of one model (NaN where it has no forecast)."""
        return per_match_scores(self.probs[model], self.y, [metric])[metric].to_numpy(dtype=float)

    def with_model(self, name: str, probs: np.ndarray, present: np.ndarray | None = None) -> "ForecastPanel":
        """A copy of the panel with one more (derived) model."""
        if name in self.probs:
            raise ValueError(f"model {name!r} is already in the panel")
        matrix = check_probabilities(probs, name=f"{name} forecasts")
        if matrix.shape != (len(self), len(self.market.classes)):
            raise ValueError("forecast matrix does not match the panel")
        mark = ~np.isnan(matrix).any(axis=1) if present is None else np.asarray(present, dtype=bool)
        return ForecastPanel(self.market, self.meta, {**self.probs, name: matrix}, {**self.present, name: mark})

    def with_outcomes(self, y: np.ndarray) -> "ForecastPanel":
        """A copy with other outcomes (used by the look-ahead check to disturb the future)."""
        y = np.asarray(y, dtype=np.int64)
        if y.shape != (len(self),) or (len(y) and (y.min() < 0 or y.max() >= len(self.market.classes))):
            raise ValueError("outcomes do not match the panel")
        meta = self.meta.copy()
        meta["y"] = y
        return ForecastPanel(self.market, meta, self.probs, self.present)


def build_panel(frames: Mapping[str, pd.DataFrame], market: Market) -> ForecastPanel:
    """Align stored forecast tables of several models on ``fixture_id``.

    Every table needs ``fixture_id, league_code, season, matchday, kickoff, y`` and the probability
    columns of the market.  The tables must agree on league, season, matchday, kickoff and outcome of every
    fixture they share (otherwise they were produced on different data and cannot be compared).
    """
    if not frames:
        raise ValueError("no forecast tables given")
    probability_columns = [f"p_{label}" for label in market.classes]
    prepared: dict[str, tuple[pd.DataFrame, np.ndarray]] = {}
    for name, frame in frames.items():
        absent = [c for c in (*_META_COLUMNS, "kickoff", *probability_columns) if c not in frame.columns]
        if absent:
            raise ValueError(f"{name}: forecast table lacks column(s) {', '.join(absent)}")
        if frame["fixture_id"].duplicated().any():
            raise ValueError(f"{name}: fixture_id must be unique")
        if frame["y"].isna().any():
            raise ValueError(f"{name}: the outcome column has missing values")
        part = frame[list(_META_COLUMNS)].copy()
        part["y"] = part["y"].astype(np.int64)
        part["kickoff_ns"] = _kickoff_ns(frame["kickoff"])
        prepared[name] = (part.reset_index(drop=True), frame[probability_columns].to_numpy(dtype=float))
    meta = pd.concat([part for part, _ in prepared.values()], ignore_index=True).drop_duplicates()
    duplicated = meta["fixture_id"].duplicated(keep=False)
    if duplicated.any():
        first = meta.loc[duplicated, "fixture_id"].iloc[0]
        raise ValueError(
            f"the forecast tables disagree about fixture {first} (outcome, league, season, matchday or kickoff)"
        )
    meta = meta.sort_values(["kickoff_ns", "fixture_id"], kind="stable").reset_index(drop=True)
    meta["kickoff"] = pd.DatetimeIndex(pd.to_datetime(meta["kickoff_ns"].to_numpy(), unit="ns", utc=True))
    index = pd.Index(meta["fixture_id"])
    probs: dict[str, np.ndarray] = {}
    present: dict[str, np.ndarray] = {}
    for name, (part, matrix) in prepared.items():
        where = index.get_indexer(part["fixture_id"])
        full = np.full((len(meta), len(market.classes)), np.nan)
        full[where] = check_probabilities(matrix, name=f"{name} forecasts")
        mark = np.zeros(len(meta), dtype=bool)
        mark[where] = True
        probs[name] = full
        present[name] = mark
    return ForecastPanel(market, meta, probs, present)


def _has_market_table(path: Path, market: Market) -> bool:
    table = "forecasts_" + market.name.replace(".", "_")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def load_panel(
    market: Market,
    models: Iterable[str],
    root: str | Path = PREDICTIONS_DIR,
) -> tuple[ForecastPanel, dict[str, str]]:
    """Read the stored forecasts of ``models`` for one market.

    Returns the panel and ``{model: reason}`` for models without a file or without a table for this
    market (for example Elo-Davidson has 1X2 only).  Tables of another protocol version raise.
    """
    frames: dict[str, pd.DataFrame] = {}
    skipped: dict[str, str] = {}
    for model in models:
        path = Path(root) / f"{model}.sqlite"
        if not path.exists():
            skipped[model] = "no file"
        elif not _has_market_table(path, market):
            skipped[model] = "no forecasts for this market"
        else:
            frames[model] = load_predictions(model, market, root)
    return build_panel(frames, market), skipped


def _league_counts(league: np.ndarray, mask: np.ndarray, limit: int = 6) -> str:
    if not mask.any():
        return ""
    counts = pd.Series(league[mask]).value_counts()
    text = ", ".join(f"{name} {int(n)}" for name, n in counts.iloc[:limit].items())
    return text + (f", +{len(counts) - limit} more" if len(counts) > limit else "")


def coverage_report(
    panel: ForecastPanel,
    models: Sequence[str] | None = None,
    *,
    reference: str = BASELINE_NAME,
    seasons: Iterable[int] | None = None,
) -> pd.DataFrame:
    """How many matches every model forecast, and how many of them the intersection dropped.

    ``n_missing`` are fixtures without a usable forecast of the model (NaN rows plus fixtures absent from
    its table).  ``n_missing_shared`` of those are missing for ``reference`` too (the framework had too
    little league history: every model loses them); ``n_missing_specific`` are missing for this model
    only.  ``n_lost_to_others`` are matches the model forecast but another compared model did not.
    """
    names = tuple(models) if models is not None else panel.models
    in_scope = np.ones(len(panel), dtype=bool) if seasons is None else np.isin(panel.season, [int(s) for s in seasons])
    common = panel.common_mask(names, seasons)
    ref_valid = panel.valid(reference) if reference in panel.probs else np.ones(len(panel), dtype=bool)
    rows = []
    for name in names:
        valid = panel.valid(name)
        missing = in_scope & ~valid
        shared = missing & ~ref_valid
        specific = missing & ref_valid
        lost = in_scope & valid & ~common
        rows.append(
            {
                "model": name,
                "n_fixtures": int(in_scope.sum()),
                "n_absent_from_table": int((in_scope & ~panel.present[name]).sum()),
                "n_missing": int(missing.sum()),
                "n_missing_shared": int(shared.sum()),
                "n_missing_specific": int(specific.sum()),
                "n_forecast": int((in_scope & valid).sum()),
                "n_common": int(common.sum()),
                "n_lost_to_others": int(lost.sum()),
                "leagues_shared": _league_counts(panel.league, shared),
                "leagues_specific": _league_counts(panel.league, specific),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Scores, paired tests, reliability
# ---------------------------------------------------------------------------


def score_models(
    panel: ForecastPanel,
    models: Sequence[str],
    mask: np.ndarray,
    *,
    n_resamples: int = 1000,
    seed: int = 0,
) -> pd.DataFrame:
    """Mean log-loss / Brier / RPS with matchday-bootstrap intervals per model, per league and pooled."""
    tables = []
    for model in models:
        scores = per_match_scores(panel.probs[model][mask], panel.y[mask])
        scores["league_code"] = panel.league[mask]
        scores["cluster"] = panel.matchday[mask]
        table = score_table(scores, n_resamples=n_resamples, seed=seed, pooled_label=POOLED_LABEL)
        table.insert(0, "model", model)
        tables.append(table)
    return pd.concat(tables, ignore_index=True)


def scores_by_season(panel: ForecastPanel, models: Sequence[str], mask: np.ndarray) -> pd.DataFrame:
    """Mean losses per model and season on the common sample (no intervals)."""
    rows = []
    for model in models:
        scores = per_match_scores(panel.probs[model][mask], panel.y[mask])
        scores["season"] = panel.season[mask]
        grouped = scores.groupby("season")
        table = grouped[list(METRICS)].mean()
        table["n"] = grouped.size()
        table = table.reset_index()
        table.insert(0, "model", model)
        rows.append(table)
    return pd.concat(rows, ignore_index=True)


def bonferroni_interval(
    mean_diff: np.ndarray,
    std_error: np.ndarray,
    n_groups: np.ndarray,
    family: int,
    confidence_level: float = 0.95,
) -> tuple[np.ndarray, np.ndarray]:
    """Interval ``mean +- t * se`` that holds jointly for ``family`` comparisons at ``confidence_level``.

    Uses the reference of the Diebold-Mariano test in :func:`~model_buk.evaluation.significance.diebold_mariano`
    (Student-t with ``G - 1`` degrees of freedom and the Harvey-Leybourne-Newbold factor ``sqrt((G - 1) / G)``
    for ``G`` matchday clusters), so the interval excludes zero exactly when the raw p-value is below
    ``(1 - confidence_level) / family``.  NaN where the standard error is undefined or ``G < 3``.
    """
    mean = np.asarray(mean_diff, dtype=float)
    se = np.asarray(std_error, dtype=float)
    groups = np.asarray(n_groups, dtype=float)
    valid = np.isfinite(se) & (groups >= 3)
    critical = np.full(mean.shape, np.nan)
    critical[valid] = stats.t.ppf(1.0 - (1.0 - confidence_level) / (2.0 * max(int(family), 1)), groups[valid] - 1.0)
    half = critical * se / np.sqrt(np.maximum(groups - 1.0, 1.0) / np.maximum(groups, 1.0))
    return mean - half, mean + half


def _paired_row(
    label: str,
    diff: np.ndarray,
    clusters: np.ndarray,
    order: np.ndarray,
    *,
    n_resamples: int,
    seed: int,
    confidence_level: float,
) -> dict[str, object]:
    row: dict[str, object] = {"league_code": label, "n": int(len(diff)), "n_clusters": int(pd.Series(clusters).nunique())}
    nan = float("nan")
    if len(diff) == 0:
        row.update(mean_diff=nan, ci_lo=nan, ci_hi=nan, std_error=nan, dm_clusters=0, dm_stat=nan, p_value=nan)
        return row
    interval = cluster_bootstrap_mean_ci(
        diff, clusters, confidence_level=confidence_level, n_resamples=n_resamples, seed=seed
    )
    test = diebold_mariano(diff, clusters, order=order)
    row.update(
        mean_diff=interval.estimate,
        ci_lo=interval.lower,
        ci_hi=interval.upper,
        std_error=test.std_error,
        dm_clusters=test.n_clusters,
        dm_stat=test.statistic,
        p_value=test.p_value,
    )
    return row


def paired_comparison(
    reference_loss: np.ndarray,
    model_loss: np.ndarray,
    *,
    league: np.ndarray,
    cluster: np.ndarray,
    order: np.ndarray,
    n_resamples: int = 1000,
    seed: int = 0,
    confidence_level: float = 0.95,
    pooled_label: str = POOLED_LABEL,
    pooled_family: int = 1,
) -> pd.DataFrame:
    """Paired comparison of a model with a reference per league and pooled.

    ``mean_diff = reference_loss - model_loss`` (positive: the model is better).  Columns: percentile
    bootstrap interval over matchdays (``ci_lo``, ``ci_hi``), Diebold-Mariano statistic and two-sided
    p-value with HAC variance over matchdays, Holm and Benjamini-Hochberg adjusted p-values across the
    leagues, and ``ci_adj_lo`` / ``ci_adj_hi``: Bonferroni intervals ``mean +- t * se`` at level
    ``1 - (1 - confidence_level) / m`` for the ``m`` leagues tested, with the same Student-t reference and
    Harvey-Leybourne-Newbold factor as the test, so all league intervals hold together with the stated
    confidence and an interval excludes zero exactly when ``p_value < alpha / m``.  The pooled row (last)
    is descriptive; its adjusted interval uses ``pooled_family`` comparisons (1 = unadjusted).
    Only matches with a finite loss for both are compared.  Loss differences of 1e-12 or less count as
    zero, so two identical forecasts give ``mean_diff = 0`` and no test statistic (never a fabricated p-value).
    """
    ref = np.asarray(reference_loss, dtype=float)
    mod = np.asarray(model_loss, dtype=float)
    finite = np.isfinite(ref) & np.isfinite(mod)
    diff = (ref - mod)[finite]
    diff[np.abs(diff) <= _IDENTICAL_LOSS] = 0.0  # identical forecasts differ by rounding dust only
    leagues = np.asarray(league, dtype=object)[finite]
    clusters = np.asarray(cluster, dtype=object)[finite]
    stamps = np.asarray(order)[finite]
    sorter = np.argsort(stamps, kind="stable")
    diff, leagues, clusters, stamps = diff[sorter], leagues[sorter], clusters[sorter], stamps[sorter]
    options = dict(n_resamples=n_resamples, seed=seed, confidence_level=confidence_level)
    rows = [
        _paired_row(str(name), diff[leagues == name], clusters[leagues == name], stamps[leagues == name], **options)
        for name in sorted(pd.unique(leagues), key=str)
    ]
    table = pd.DataFrame(rows)
    pooled = pd.DataFrame([_paired_row(pooled_label, diff, clusters, stamps, **options)])
    if len(table):
        table["p_holm"] = holm_adjust(table["p_value"].to_numpy())
        table["p_bh"] = benjamini_hochberg_adjust(table["p_value"].to_numpy())
        tested = int(np.isfinite(table["std_error"].to_numpy(dtype=float)).sum())
    else:
        table = pd.DataFrame(columns=list(pooled.columns))
        table["p_holm"] = table["p_bh"] = np.nan
        tested = 0
    pooled["p_holm"] = pooled["p_bh"] = np.nan
    for frame, family in ((table, max(tested, 1)), (pooled, max(int(pooled_family), 1))):
        if frame.empty:
            continue
        frame["ci_adj_lo"], frame["ci_adj_hi"] = bonferroni_interval(
            frame["mean_diff"].to_numpy(dtype=float),
            frame["std_error"].to_numpy(dtype=float),
            frame["dm_clusters"].to_numpy(dtype=float),
            family,
            confidence_level,
        )
    return pd.concat([table, pooled], ignore_index=True)


def significance_counts(versus: pd.DataFrame, alpha: float = 0.05) -> dict[str, int]:
    """Leagues in which the model is better / worse than the reference (raw, Holm, Benjamini-Hochberg)."""
    leagues = versus[versus["league_code"] != POOLED_LABEL]
    diff = leagues["mean_diff"]
    counts = {"n_leagues": int(len(leagues)), "n_tested": int(leagues["p_value"].notna().sum()), "n_better": int((diff > 0).sum())}
    for label, column in (("raw", "p_value"), ("holm", "p_holm"), ("bh", "p_bh")):
        significant = leagues[column] < alpha
        counts[f"better_{label}"] = int((significant & (diff > 0)).sum())
        counts[f"worse_{label}"] = int((significant & (diff < 0)).sum())
    return counts


def corp_table(
    panel: ForecastPanel,
    models: Sequence[str],
    mask: np.ndarray,
    *,
    score: str = "brier",
    n_sim: int = 0,
    seed: int = 0,
) -> pd.DataFrame:
    """Pooled CORP decomposition ``MCB - DSC + UNC`` per model and class (last class only for 2-class markets).

    ``mean_p`` is the mean forecast of the class next to its ``base_rate`` (observed frequency).
    """
    classes = panel.market.classes
    binary = len(classes) == 2
    chosen = [len(classes) - 1] if binary else list(range(len(classes)))
    tables = []
    for model in models:
        matrix = panel.probs[model][mask]
        table = corp_by_class(matrix, panel.y[mask], classes, score=score, n_sim=n_sim, seed=seed, binary_single=binary)
        table["mean_p"] = [float(matrix[:, k].mean()) for k in chosen]
        table.insert(0, "model", model)
        tables.append(table)
    return pd.concat(tables, ignore_index=True)


def rank_models(panel: ForecastPanel, models: Sequence[str], mask: np.ndarray) -> pd.DataFrame:
    """Pooled mean log-loss per model on the sample, best first."""
    rows = [{"model": m, "log_loss": float(log_loss_per_match(panel.probs[m][mask], panel.y[mask]).mean())} for m in models]
    return pd.DataFrame(rows).sort_values("log_loss", kind="stable").reset_index(drop=True)


def best_other_model(ranking: pd.DataFrame, model: str, eligible: Iterable[str]) -> str | None:
    """The best model (lowest pooled log-loss) among ``eligible`` other than ``model``."""
    allowed = set(eligible) - {model}
    for name in ranking["model"]:
        if name in allowed:
            return str(name)
    return None


def compare_models(
    panel: ForecastPanel,
    models: Sequence[str],
    *,
    reference: str = BASELINE_NAME,
    best_pool: Sequence[str] | None = None,
    seasons: Iterable[int] | None = None,
    n_resamples: int = 1000,
    seed: int = 0,
    corp_sims: int = 0,
) -> dict[str, pd.DataFrame]:
    """The full comparison of ``models`` (and ``reference``) on their common matches.

    ``best_pool`` are the models eligible as "the best other model" (default: every model except the
    reference); the model's own rank is decided on the common sample, which makes the "versus best other"
    table descriptive, not a test of an ex-ante choice.  Returns the tables ``coverage``, ``ranking``,
    ``scores``, ``versus_reference``, ``versus_best``, ``by_season`` and ``corp`` (each model with its
    pooled and league rows).
    """
    everyone = tuple(dict.fromkeys([reference, *models]))
    mask = panel.common_mask(everyone, seasons)
    if not mask.any():
        raise ValueError("no match has a forecast from every compared model")
    eligible = tuple(best_pool) if best_pool is not None else tuple(m for m in everyone if m != reference)
    ranking = rank_models(panel, everyone, mask)
    losses = {m: panel.losses(m) for m in everyone}
    common = dict(league=panel.league[mask], cluster=panel.matchday[mask], order=panel.kickoff_ns[mask], n_resamples=n_resamples, seed=seed)

    def paired(reference_name: str, model: str) -> pd.DataFrame:
        table = paired_comparison(losses[reference_name][mask], losses[model][mask], **common)
        table.insert(0, "model", model)
        table.insert(1, "reference", reference_name)
        return table

    versus_reference = [paired(reference, m) for m in models if m != reference]
    versus_best = []
    for model in models:
        if model == reference:
            continue
        other = best_other_model(ranking, model, eligible)
        if other is not None:
            versus_best.append(paired(other, model))
    return {
        "coverage": coverage_report(panel, everyone, reference=reference, seasons=seasons),
        "ranking": ranking,
        "scores": score_models(panel, everyone, mask, n_resamples=n_resamples, seed=seed),
        "versus_reference": pd.concat(versus_reference, ignore_index=True) if versus_reference else pd.DataFrame(),
        "versus_best": pd.concat(versus_best, ignore_index=True) if versus_best else pd.DataFrame(),
        "by_season": scores_by_season(panel, everyone, mask),
        "corp": corp_table(panel, everyone, mask, n_sim=corp_sims, seed=seed),
    }


# ---------------------------------------------------------------------------
# Selection without peeking
# ---------------------------------------------------------------------------


def selection_season(scored_season: int) -> int:
    """The season whose forecasts decide the choice for ``scored_season``: always the one before it.

    Kept as a module-level function so the look-ahead check can be mutation-tested against a deliberately
    broken rule (``lambda s: s``), like ``training_cutoff_ns`` in the framework.
    """
    return int(scored_season) - 1


def select_model(
    losses: Mapping[str, np.ndarray],
    fit_rows: np.ndarray,
    candidates: Sequence[str] | None = None,
    *,
    cluster: np.ndarray | None = None,
    order: np.ndarray | None = None,
) -> tuple[str, pd.DataFrame]:
    """The candidate with the lowest mean loss on ``fit_rows`` (ties: the first in ``candidates``).

    ``losses[model]`` is the per-match loss over all rows, ``fit_rows`` the boolean mask of the selection
    sample (already restricted to the selection season).  Returns the choice and a table ``model, n,
    log_loss, gap`` sorted by loss where ``gap`` is the loss above the best.  When ``cluster`` is given the
    table also holds ``gap_se``, the standard error of the paired difference to the best over matchdays.
    """
    names = tuple(candidates) if candidates is not None else tuple(losses)
    rows = np.asarray(fit_rows, dtype=bool)
    if not rows.any():
        raise ValueError("no forecasts in the selection sample")
    means = []
    for name in names:
        values = np.asarray(losses[name], dtype=float)[rows]
        if not np.isfinite(values).all():
            raise ValueError(f"{name}: missing forecasts in the selection sample; restrict it to the common matches")
        means.append(float(values.mean()))
    best = int(np.argmin(means))
    table = pd.DataFrame({"model": names, "n": int(rows.sum()), "log_loss": means})
    table["gap"] = table["log_loss"] - means[best]
    table["gap_se"] = np.nan
    if cluster is not None:
        best_values = np.asarray(losses[names[best]], dtype=float)[rows]
        for i, name in enumerate(names):
            if i == best:
                continue
            diff = np.asarray(losses[name], dtype=float)[rows] - best_values
            test = diebold_mariano(diff, np.asarray(cluster)[rows], order=None if order is None else np.asarray(order)[rows])
            table.loc[i, "gap_se"] = test.std_error
    return names[best], table.sort_values("log_loss", kind="stable").reset_index(drop=True)


def select_models_per_league(
    losses: Mapping[str, np.ndarray],
    league: np.ndarray,
    fit_rows: np.ndarray,
    pooled_choice: str,
    candidates: Sequence[str],
) -> pd.DataFrame:
    """Per-league choice shrunk to the pooled choice (empirical Bayes on the selection sample).

    For every candidate ``m`` other than the pooled choice ``b`` the league's advantage over ``b`` is
    ``d_L = mean(loss_b - loss_m)`` with sampling variance ``v_L = s2 / n_L`` (``s2`` the pooled within-league
    variance of the per-match difference).  The between-league variance ``tau2`` of
    the true advantages is estimated with the DerSimonian-Laird moment estimator (precision-weighted, so a
    league of a few matches cannot inflate it), and the league value is shrunk to the pooled advantage
    ``D``: ``mu_L = D + tau2 / (tau2 + v_L) * (d_L - D)``.  A league takes
    the candidate with the largest ``mu_L`` if that is above zero and stays with ``b`` otherwise, so with
    no evidence of real differences between leagues every league keeps the pooled choice.  Leagues without
    rows in the selection sample also keep it.  Matches are treated as independent inside a league (the
    matchday correlation is small next to the between-league spread).
    """
    rows = np.asarray(fit_rows, dtype=bool)
    leagues_all = np.asarray(league, dtype=object)
    names = sorted(pd.unique(leagues_all), key=str)
    best_mu = pd.Series(0.0, index=names)
    chosen = pd.Series(pooled_choice, index=names, dtype=object)
    weight = pd.Series(np.nan, index=names)
    for model in candidates:
        if model == pooled_choice:
            continue
        diff = np.asarray(losses[pooled_choice], dtype=float)[rows] - np.asarray(losses[model], dtype=float)[rows]
        frame = pd.DataFrame({"league": leagues_all[rows], "d": diff})
        grouped = frame.groupby("league")["d"].agg(["mean", "size"])
        if len(grouped) < 2:
            continue
        overall = float(frame["d"].mean())
        deviations = frame["d"] - frame.groupby("league")["d"].transform("mean")
        within = float((deviations**2).sum() / max(len(frame) - len(grouped), 1))
        sampling = np.maximum(within / grouped["size"].to_numpy(dtype=float), 1e-12)
        means = grouped["mean"].to_numpy(dtype=float)
        precision = 1.0 / sampling
        centre = float((precision * means).sum() / precision.sum())
        q = float((precision * (means - centre) ** 2).sum())
        scale = float(precision.sum() - (precision**2).sum() / precision.sum())
        tau2 = max(0.0, (q - (len(means) - 1)) / scale) if scale > 0 else 0.0
        shrink = pd.Series(tau2 / (tau2 + sampling) if tau2 > 0 else np.zeros(len(grouped)), index=grouped.index)
        mu = overall + shrink * (grouped["mean"] - overall)
        better = mu.index[mu.to_numpy() > best_mu.reindex(mu.index).to_numpy()]
        for name in better:
            best_mu[name] = float(mu[name])
            chosen[name] = model
            weight[name] = float(shrink[name])
    return pd.DataFrame({"league_code": names, "chosen": chosen.to_numpy(dtype=object), "advantage": best_mu.to_numpy(), "shrinkage_weight": weight.to_numpy()})


# ---------------------------------------------------------------------------
# Pooling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolFit:
    """Weights of a pool and how the fit went (``objective`` is the penalised mean log-loss)."""

    weights: np.ndarray
    objective: float
    converged: bool
    kind: str


def _stack_log(probabilities: Sequence[np.ndarray], floor: float = 1e-15) -> np.ndarray:
    return np.log(np.clip(np.stack([np.asarray(p, dtype=float) for p in probabilities]), floor, 1.0))


def _simplex_fit(objective, m: int, ridge: float, kind: str) -> PoolFit:
    prior = np.full(m, 1.0 / m)

    def fun(w: np.ndarray) -> tuple[float, np.ndarray]:
        value, grad = objective(w)
        return value + ridge * float(np.sum((w - prior) ** 2)), grad + 2.0 * ridge * (w - prior)

    if m == 1:
        return PoolFit(np.ones(1), float(fun(np.ones(1))[0]), True, kind)
    start = fun(prior)[0]
    result = optimize.minimize(
        fun,
        prior,
        jac=True,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * m,
        constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0), "jac": lambda w: np.ones(m)}],
        options={"maxiter": 500, "ftol": 1e-13},
    )
    weights = np.clip(np.asarray(result.x, dtype=float), 0.0, None)
    weights[weights < 1e-9] = 0.0  # solver dust on the boundary
    weights = weights / weights.sum() if weights.sum() > 0 else prior
    final = fun(weights)[0]
    if not np.isfinite(final) or final > start + 1e-12:
        weights, final = prior, start
    return PoolFit(weights, float(final), bool(result.success), kind)


def fit_log_linear_pool(
    probabilities: Sequence[np.ndarray],
    y: np.ndarray,
    *,
    ridge: float = DEFAULT_RIDGE,
) -> PoolFit:
    """Weights ``w >= 0, sum w = 1`` of the log-linear pool ``p_c ~ prod_m p_mc ** w_m`` minimising log-loss.

    ``probabilities[m]`` is an ``(n, K)`` matrix without NaN, ``y`` the class indices.  The objective is
    convex in ``w``.  A tiny ridge towards equal weights (default 1e-4) makes the solution unique when
    models are near copies of each other (their weights then share instead of jumping to one of them); its
    effect on the log-loss is far below 1e-5.
    """
    logp = _stack_log(probabilities)
    m, n, _ = logp.shape
    y = np.asarray(y, dtype=np.int64)
    if n == 0 or len(y) != n:
        raise ValueError("outcomes do not match the forecasts")
    picked = logp[:, np.arange(n), y].mean(axis=1)

    def objective(w: np.ndarray) -> tuple[float, np.ndarray]:
        score = np.tensordot(w, logp, axes=(0, 0))
        top = score.max(axis=1, keepdims=True)
        log_norm = top[:, 0] + np.log(np.exp(score - top).sum(axis=1))
        value = float(np.mean(log_norm - score[np.arange(n), y]))
        soft = np.exp(score - log_norm[:, None])
        return value, np.einsum("nk,mnk->m", soft, logp) / n - picked

    return _simplex_fit(objective, m, ridge, "log_linear")


def apply_log_linear_pool(probabilities: Sequence[np.ndarray], weights: np.ndarray) -> np.ndarray:
    """Forecast of the log-linear pool with the given weights (rows sum to one)."""
    logp = _stack_log(probabilities)
    score = np.tensordot(np.asarray(weights, dtype=float), logp, axes=(0, 0))
    score -= score.max(axis=1, keepdims=True)
    pooled = np.exp(score)
    return pooled / pooled.sum(axis=1, keepdims=True)


def fit_linear_pool(
    probabilities: Sequence[np.ndarray],
    y: np.ndarray,
    *,
    ridge: float = DEFAULT_RIDGE,
) -> PoolFit:
    """Weights of the linear pool ``p = sum_m w_m p_m`` (``w >= 0, sum w = 1``) minimising log-loss."""
    stacked = np.stack([np.asarray(p, dtype=float) for p in probabilities])
    m, n, _ = stacked.shape
    y = np.asarray(y, dtype=np.int64)
    if n == 0 or len(y) != n:
        raise ValueError("outcomes do not match the forecasts")
    of_outcome = np.clip(stacked[:, np.arange(n), y], 1e-15, 1.0)

    def objective(w: np.ndarray) -> tuple[float, np.ndarray]:
        mixed = np.clip(w @ of_outcome, 1e-15, None)
        return float(-np.mean(np.log(mixed))), -(of_outcome / mixed).mean(axis=1)

    return _simplex_fit(objective, m, ridge, "linear")


def apply_linear_pool(probabilities: Sequence[np.ndarray], weights: np.ndarray) -> np.ndarray:
    """Forecast of the linear pool with the given weights."""
    stacked = np.stack([np.asarray(p, dtype=float) for p in probabilities])
    return np.tensordot(np.asarray(weights, dtype=float), stacked, axes=(0, 0))


# ---------------------------------------------------------------------------
# Nested selection run
# ---------------------------------------------------------------------------


@dataclass
class SelectionRun:
    """Choices, pool weights and forecasts of the ex-ante selection over the scored seasons.

    ``selected``, ``pool``, ``pool_linear`` and ``selected_league`` are ``(n, K)`` forecast matrices over
    the panel's rows (NaN outside the scored seasons, where the chosen model or a pool component has no
    forecast, and for every season that has no earlier season to select on).
    """

    market: Market
    candidates: tuple[str, ...]
    scored_seasons: tuple[int, ...]
    choices: pd.DataFrame
    weights: pd.DataFrame
    league_choices: pd.DataFrame
    selected: np.ndarray
    pool: np.ndarray
    pool_linear: np.ndarray
    selected_league: np.ndarray

    def forecasts(self) -> dict[str, np.ndarray]:
        return {
            SELECTED_NAME: self.selected,
            POOL_NAME: self.pool,
            POOL_LINEAR_NAME: self.pool_linear,
            SELECTED_LEAGUE_NAME: self.selected_league,
        }


def run_selection(
    panel: ForecastPanel,
    candidates: Sequence[str],
    scored_seasons: Iterable[int],
    *,
    ridge: float = DEFAULT_RIDGE,
    before_first_kickoff: bool = False,
) -> SelectionRun:
    """Choose and pool ``candidates`` for every scored season using only the season before it.

    For a scored season ``S`` the selection sample is the set of matches of season
    ``selection_season(S)`` that every candidate forecast.  From it: the model with the lowest log-loss
    (:func:`select_model`), the log-linear and linear pool weights, and the shrunk per-league choice.
    The forecasts of season ``S`` are then taken from the chosen model (per league for the league
    variant) or pooled with those weights; a match is left NaN when the chosen model or a pool component
    has no forecast for it.  A season for which the selection sample is empty raises (there is nothing to
    choose from); a scored season without matches in the panel (the coming season) only records the choice.

    ``before_first_kickoff=True`` is the strict variant used by :func:`calendar_year_check`: the selection
    sample keeps only the matches that kicked off before the first match of ``S`` in any league, so a
    calendar-year league that opens ``S`` in February never sees the March-May results of the European
    season ``S - 1``.  It leaves the coming season (no matches yet) unrestricted.
    """
    names = tuple(candidates)
    if len(names) < 2:
        raise ValueError("selection needs at least two candidates")
    unknown = [m for m in names if m not in panel.probs]
    if unknown:
        raise ValueError(f"candidates not in the panel: {', '.join(unknown)}")
    n, k = len(panel), len(panel.market.classes)
    seasons = tuple(sorted({int(s) for s in scored_seasons}))
    common = panel.common_mask(names)
    y, season, league = panel.y, panel.season, panel.league
    losses = {m: panel.losses(m) for m in names}
    selected, pool, pool_linear, by_league = (np.full((n, k), np.nan) for _ in range(4))
    choice_rows, weight_rows, league_rows = [], [], []
    for scored in seasons:
        fit_season = selection_season(scored)
        fit_rows = common & (season == fit_season)
        rows = season == scored
        if before_first_kickoff and rows.any():
            fit_rows &= panel.kickoff_ns < panel.kickoff_ns[rows].min()
        if not fit_rows.any():
            raise ValueError(f"no common forecasts in season {fit_season} to select for season {scored}")
        choice, table = select_model(losses, fit_rows, names, cluster=panel.matchday, order=panel.kickoff_ns)
        runner_up = table.iloc[1]
        entry = {
            "scored_season": scored,
            "fit_season": fit_season,
            "n_fit": int(fit_rows.sum()),
            "n_scored": int(rows.sum()),
            "chosen": choice,
            "runner_up": str(runner_up["model"]),
            "margin": float(runner_up["gap"]),
            "margin_se": float(runner_up["gap_se"]),
        }
        entry.update({f"ll_{m}": float(table.loc[table["model"] == m, "log_loss"].iloc[0]) for m in names})
        choice_rows.append(entry)
        usable = rows & panel.valid(choice)
        selected[usable] = panel.probs[choice][usable]

        components = [panel.probs[m][fit_rows] for m in names]
        pool_rows = rows & common
        for fit, apply, target in (
            (fit_log_linear_pool(components, y[fit_rows], ridge=ridge), apply_log_linear_pool, pool),
            (fit_linear_pool(components, y[fit_rows], ridge=ridge), apply_linear_pool, pool_linear),
        ):
            for m, w in zip(names, fit.weights):
                weight_rows.append(
                    {"scored_season": scored, "fit_season": fit_season, "kind": fit.kind, "model": m, "weight": float(w), "objective": fit.objective, "converged": fit.converged}
                )
            if pool_rows.any():
                target[pool_rows] = apply([panel.probs[m][pool_rows] for m in names], fit.weights)

        chosen_by_league = select_models_per_league(losses, league, fit_rows, choice, names)
        chosen_by_league.insert(0, "scored_season", scored)
        chosen_by_league.insert(1, "pooled_choice", choice)
        league_rows.append(chosen_by_league)
        lookup = dict(zip(chosen_by_league["league_code"], chosen_by_league["chosen"]))
        for name, model in lookup.items():
            here = rows & (league == name) & panel.valid(model)
            by_league[here] = panel.probs[model][here]
    return SelectionRun(
        market=panel.market,
        candidates=names,
        scored_seasons=seasons,
        choices=pd.DataFrame(choice_rows),
        weights=pd.DataFrame(weight_rows),
        league_choices=pd.concat(league_rows, ignore_index=True),
        selected=selected,
        pool=pool,
        pool_linear=pool_linear,
        selected_league=by_league,
    )


def calendar_year_check(
    panel: ForecastPanel,
    candidates: Sequence[str],
    scored_seasons: Iterable[int],
    *,
    ridge: float = DEFAULT_RIDGE,
) -> pd.DataFrame:
    """How much the season-label rule of :func:`run_selection` matters for calendar-year leagues.

    The main rule selects on all matches with the label ``S - 1``.  Leagues whose season is a calendar
    year open ``S`` in February or March, before the European season ``S - 1`` ends, so a few results
    (March to May) enter the pooled choice after such a league's first ``S`` kickoff.  This table compares
    the main rule with the strict one (:func:`run_selection` with ``before_first_kickoff=True``), season by
    season: the sizes of the selection samples, the two choices, and the log-loss of the pooled and the
    selected forecasts on the matches of ``S`` that both variants forecast.  Scored seasons without
    matches (the coming season) are left out.
    """
    names = tuple(candidates)
    seasons = tuple(sorted({int(s) for s in scored_seasons}))
    loose = run_selection(panel, names, seasons, ridge=ridge)
    strict = run_selection(panel, names, seasons, ridge=ridge, before_first_kickoff=True)
    rows = []
    for scored in seasons:
        in_season = panel.season == scored
        if not in_season.any():
            continue
        a = loose.choices.set_index("scored_season").loc[scored]
        b = strict.choices.set_index("scored_season").loc[scored]
        weights = {
            name: run.weights[(run.weights["scored_season"] == scored) & (run.weights["kind"] == "log_linear")]
            .set_index("model")["weight"]
            .reindex(names)
            .to_numpy(dtype=float)
            for name, run in (("loose", loose), ("strict", strict))
        }
        entry = {
            "scored_season": scored,
            "n_fit_main": int(a["n_fit"]),
            "n_fit_strict": int(b["n_fit"]),
            "chosen_main": str(a["chosen"]),
            "chosen_strict": str(b["chosen"]),
            "max_weight_difference": float(np.abs(weights["loose"] - weights["strict"]).max()),
        }
        pairs = (("pool", loose.pool, strict.pool), ("selected", loose.selected, strict.selected))
        both = in_season.copy()
        for _, first, second in pairs:
            both &= ~np.isnan(first).any(axis=1) & ~np.isnan(second).any(axis=1)
        entry["n_scored"] = int(both.sum())
        for label, first, second in pairs:
            entry[f"{label}_log_loss_main"] = float(log_loss_per_match(first[both], panel.y[both]).mean())
            entry[f"{label}_log_loss_strict"] = float(log_loss_per_match(second[both], panel.y[both]).mean())
        rows.append(entry)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Look-ahead check of the selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionLeakSeason:
    """Result of the check for one disturbed season: forecasts up to it must not move."""

    disturbed_from: int
    n_compared: int
    n_changed: int
    n_later_changed: int
    max_abs_difference: float

    @property
    def passed(self) -> bool:
        return self.n_changed == 0


@dataclass(frozen=True)
class SelectionLeakReport:
    seasons: tuple[SelectionLeakSeason, ...]

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.seasons)

    @property
    def n_changed(self) -> int:
        return sum(s.n_changed for s in self.seasons)

    @property
    def informative(self) -> bool:
        """True when disturbing the outcomes moved at least one later forecast (so the test can detect a leak)."""
        return any(s.n_later_changed > 0 for s in self.seasons)


def check_selection_no_lookahead(
    panel: ForecastPanel,
    candidates: Sequence[str],
    scored_seasons: Iterable[int],
    *,
    ridge: float = DEFAULT_RIDGE,
    tolerance: float = 1e-12,
) -> SelectionLeakReport:
    """Leakage test of the selection and the pools.

    For every scored season ``T`` the outcomes of ``T`` and all later seasons are replaced by other
    outcomes (each class index moved to the next class).  The choices, pool weights and forecasts for
    seasons up to and including ``T`` must not change at all: they may depend on the outcomes before ``T``
    only.  Forecasts of later seasons are expected to change (they depend on the disturbed outcomes of
    ``T`` and later); that count is reported so a vacuous test is visible (:attr:`SelectionLeakReport.informative`).
    """
    seasons = tuple(sorted({int(s) for s in scored_seasons}))
    k = len(panel.market.classes)
    original = run_selection(panel, candidates, seasons, ridge=ridge)
    reports = []
    for start in seasons:
        y = panel.y.copy()
        y[panel.season >= start] = (y[panel.season >= start] + 1) % k
        disturbed = run_selection(panel.with_outcomes(y), candidates, seasons, ridge=ridge)
        earlier = panel.season <= start
        later = panel.season > start
        compared = changed = later_changed = 0
        worst = 0.0
        for name, before in original.forecasts().items():
            after = disturbed.forecasts()[name]
            difference = np.abs(np.nan_to_num(before, nan=0.0) - np.nan_to_num(after, nan=0.0))
            difference[np.isnan(before) != np.isnan(after)] = np.inf
            per_row = difference.max(axis=1)
            has_forecast = ~np.isnan(before).any(axis=1) | ~np.isnan(after).any(axis=1)
            compared += int((earlier & has_forecast).sum())
            changed += int((earlier & (per_row > tolerance)).sum())
            later_changed += int((later & (per_row > tolerance)).sum())
            worst = max(worst, float(per_row[earlier].max(initial=0.0)))
        reports.append(SelectionLeakSeason(start, compared, changed, later_changed, worst))
    return SelectionLeakReport(tuple(reports))


def assert_selection_no_lookahead(*args, **kwargs) -> SelectionLeakReport:
    """Like :func:`check_selection_no_lookahead` but raises :class:`SelectionLeakError` on failure or when vacuous."""
    report = check_selection_no_lookahead(*args, **kwargs)
    if not report.passed:
        detail = ", ".join(f"from {s.disturbed_from}: {s.n_changed} changed (max {s.max_abs_difference:.3g})" for s in report.seasons if not s.passed)
        raise SelectionLeakError(f"selection or pool forecasts depend on outcomes of the scored season ({detail})")
    if not report.informative:
        raise SelectionLeakError("the outcome disturbance did not move any later forecast; the check is vacuous")
    return report


# ---------------------------------------------------------------------------
# Derived forecasts in the protocol's storage format
# ---------------------------------------------------------------------------


def derived_result(
    panel: ForecastPanel,
    probs: np.ndarray,
    *,
    model: str,
    params: Sequence[str],
    selections: pd.DataFrame,
    inner_scores: pd.DataFrame | None = None,
    elapsed_seconds: float = 0.0,
) -> WalkForwardResult:
    """Wrap forecasts over the whole panel as a :class:`WalkForwardResult` for :func:`save_result`.

    ``params`` is one string per panel row (what the model chose for that match).  Rows without a
    forecast stay NaN.
    """
    matrix = check_probabilities(probs, name=f"{model} forecasts")
    if matrix.shape != (len(panel), len(panel.market.classes)) or len(params) != len(panel):
        raise ValueError("forecasts and params must cover every row of the panel")
    predictions = pd.DataFrame(
        {
            "fixture_id": panel.meta["fixture_id"].to_numpy(),
            "league_code": panel.league,
            "season": panel.season,
            "matchday": panel.matchday,
            "kickoff": pd.DatetimeIndex(panel.meta["kickoff"]),
            "kickoff_ns": panel.kickoff_ns,
            "y": panel.y,
            "params": list(params),
        }
    )
    for j, label in enumerate(panel.market.classes):
        predictions[f"p_{label}"] = matrix[:, j]
    return WalkForwardResult(
        model=model,
        market=panel.market,
        config=COMPARISON_CONFIG,
        predictions=predictions,
        selections=selections,
        inner_scores=inner_scores if inner_scores is not None else pd.DataFrame(),
        dropped={"input": len(panel), "no_forecast": int(np.isnan(matrix).any(axis=1).sum())},
        elapsed_seconds=float(elapsed_seconds),
    )


def selected_params(panel: ForecastPanel, run: SelectionRun) -> list[str]:
    """Per-row description of the selected model (``{"selected": ..., "fit_season": ...}``) for the storage table."""
    by_season = {int(row.scored_season): row for row in run.choices.itertuples()}
    out = []
    for value in panel.season:
        row = by_season.get(int(value))
        out.append(
            json.dumps({"selected": row.chosen, "fit_season": int(row.fit_season)})
            if row is not None
            else json.dumps({"selected": None, "reason": "selection season"})
        )
    return out


def pool_params(panel: ForecastPanel, run: SelectionRun, kind: str = "log_linear") -> list[str]:
    """Per-row description of the pool weights (``{"weights": {...}, "fit_season": ...}``) for the storage table."""
    table = run.weights[run.weights["kind"] == kind]
    by_season = {
        int(season): (
            {row.model: round(float(row.weight), 6) for row in group.itertuples()},
            int(group["fit_season"].iloc[0]),
        )
        for season, group in table.groupby("scored_season")
    }
    out = []
    for value in panel.season:
        entry = by_season.get(int(value))
        out.append(
            json.dumps({"weights": entry[0], "fit_season": entry[1]})
            if entry is not None
            else json.dumps({"weights": None, "reason": "selection season"})
        )
    return out


def selection_tables(run: SelectionRun, kind: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(selections, inner_scores)`` frames of a derived result: what was chosen and how it did on the selection season."""
    if kind == "selected":
        selections = pd.DataFrame(
            {
                "outer_season": run.choices["scored_season"],
                "league": POOLED_LABEL,
                "params": [json.dumps({"selected": c}) for c in run.choices["chosen"]],
                "inner_log_loss": [float(row[f"ll_{row['chosen']}"]) for _, row in run.choices.iterrows()],
                "fit_season": run.choices["fit_season"],
            }
        )
        long = run.choices.melt(
            id_vars=["scored_season", "fit_season", "n_fit"],
            value_vars=[f"ll_{m}" for m in run.candidates],
            var_name="model",
            value_name="inner_log_loss",
        )
        long["model"] = long["model"].str.removeprefix("ll_")
        return selections, long.rename(columns={"scored_season": "outer_season"})
    pooled = run.weights[run.weights["kind"] == kind]
    rows = []
    for season, group in pooled.groupby("scored_season"):
        rows.append(
            {
                "outer_season": int(season),
                "league": POOLED_LABEL,
                "params": json.dumps({row.model: round(float(row.weight), 6) for row in group.itertuples()}),
                "inner_log_loss": float(group["objective"].iloc[0]),
                "fit_season": int(group["fit_season"].iloc[0]),
            }
        )
    return pd.DataFrame(rows), pooled.rename(columns={"scored_season": "outer_season"})


__all__ = [
    "BASELINE_NAME",
    "DEFAULT_RIDGE",
    "FIXED_MODELS",
    "POOLED_LABEL",
    "POOL_LINEAR_NAME",
    "POOL_NAME",
    "SELECTED_LEAGUE_NAME",
    "SELECTED_NAME",
    "ForecastPanel",
    "PoolFit",
    "SelectionLeakError",
    "SelectionLeakReport",
    "SelectionLeakSeason",
    "SelectionRun",
    "apply_linear_pool",
    "apply_log_linear_pool",
    "assert_selection_no_lookahead",
    "best_other_model",
    "bonferroni_interval",
    "build_panel",
    "calendar_year_check",
    "check_selection_no_lookahead",
    "compare_models",
    "corp_table",
    "coverage_report",
    "derived_result",
    "fit_linear_pool",
    "fit_log_linear_pool",
    "load_panel",
    "paired_comparison",
    "pool_params",
    "rank_models",
    "run_selection",
    "score_models",
    "scores_by_season",
    "select_model",
    "select_models_per_league",
    "selected_params",
    "selection_season",
    "selection_tables",
    "significance_counts",
]
