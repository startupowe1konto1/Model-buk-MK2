"""Tests for the common evaluation framework (walk-forward, scores, CORP, DM, leakage)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import model_buk.evaluation.framework as framework
from model_buk.evaluation.framework import (
    MARKET_1X2,
    GlobalFrequencyBaseline,
    LeagueFrequencyBaseline,
    LookaheadLeakError,
    ModelSpec,
    WalkForwardConfig,
    assert_no_lookahead,
    assign_matchdays,
    check_no_lookahead,
    compare_to_baseline,
    evaluate_model,
    global_frequency_spec,
    league_frequency_spec,
    over_under,
    permute_future_outcomes,
    prepare_matches,
)
from model_buk.evaluation.gates import multiclass_brier_score, multiclass_log_loss
from model_buk.evaluation.reliability import corp_by_class, corp_diagram_table, corp_reliability
from model_buk.evaluation.scoring import (
    brier_per_match,
    check_probabilities,
    log_loss_per_match,
    per_match_scores,
    rps_per_match,
    score_table,
)
from model_buk.evaluation.significance import (
    benjamini_hochberg_adjust,
    cluster_bootstrap_mean_ci,
    cluster_bootstrap_means,
    diebold_mariano,
    holm_adjust,
    newey_west_lags,
)

NS_PER_HOUR = 3_600_000_000_000
CLASSES_1X2 = ("home", "draw", "away")


# ---------------------------------------------------------------------------
# Synthetic league data with known true probabilities
# ---------------------------------------------------------------------------


def make_synthetic(n_leagues=6, n_seasons=4, n_teams=12, seed=11, first_season=2019):
    """Round-robin-like leagues with team strengths; returns (matches, true probabilities)."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    truth: dict[int, np.ndarray] = {}
    fixture_id = 1
    for league in range(n_leagues):
        code = f"L{league:02d}"
        home_advantage = 0.1 + 0.12 * league
        draw_weight = 0.6 + 0.2 * league  # leagues differ in home advantage and draw rate
        strength = rng.normal(0.0, 0.8, n_teams)
        for season_index in range(n_seasons):
            season = first_season + season_index
            start = pd.Timestamp(f"{season}-08-01", tz="UTC")
            first_saturday = start + pd.Timedelta(days=(5 - start.weekday()) % 7)
            for round_index in range(2 * (n_teams - 1)):
                order = rng.permutation(n_teams)
                for slot in range(n_teams // 2):
                    home, away = int(order[2 * slot]), int(order[2 * slot + 1])
                    diff = home_advantage + strength[home] - strength[away]
                    weights = np.array([np.exp(diff / 2.0), draw_weight, np.exp(-diff / 2.0)])
                    probs = weights / weights.sum()
                    outcome = int(rng.choice(3, p=probs))
                    extra = int(rng.poisson(0.8))
                    goals = [(1 + extra, extra), (extra, extra), (extra, 1 + extra)][outcome]
                    day = 0 if slot < 4 else 1
                    kickoff = first_saturday + pd.Timedelta(days=7 * round_index + day, hours=13 + slot % 4)
                    rows.append(
                        {
                            "fixture_id": fixture_id,
                            "league_code": code,
                            "season": season,
                            "round": f"Regular Season - {round_index + 1}",
                            "kickoff_utc": kickoff.isoformat(),
                            "home_team": f"{code}-T{home}",
                            "away_team": f"{code}-T{away}",
                            "home_goals": goals[0],
                            "away_goals": goals[1],
                            "status_short": "FT",
                        }
                    )
                    truth[fixture_id] = probs
                    fixture_id += 1
    return pd.DataFrame(rows), truth


@pytest.fixture(scope="module")
def synthetic():
    return make_synthetic()


@pytest.fixture(scope="module")
def config():
    return WalkForwardConfig(test_seasons=[2020, 2021, 2022], min_train_matches=50)


class OracleModel:
    """Knows the true match probabilities (not the outcomes)."""

    classes = CLASSES_1X2

    def __init__(self, truth):
        self.truth = truth

    def fit(self, train):
        return self

    def predict(self, frame):
        return np.vstack([self.truth[int(fid)] for fid in frame["fixture_id"]])


def oracle_spec(truth):
    return ModelSpec("oracle", lambda: OracleModel(truth))


@pytest.fixture(scope="module")
def baseline_result(synthetic, config):
    matches, _ = synthetic
    return evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, config)


@pytest.fixture(scope="module")
def oracle_result(synthetic, config):
    matches, truth = synthetic
    return evaluate_model(oracle_spec(truth), matches, MARKET_1X2, config)


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------


def test_scoring_matches_hand_computed_values():
    probs = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    y = np.array([0, 0])
    assert log_loss_per_match(probs, y) == pytest.approx([-np.log(0.5), -np.log(0.2)])
    assert brier_per_match(probs, y) == pytest.approx([0.25 + 0.09 + 0.04, 0.64 + 0.09 + 0.25])
    # RPS: cumulative forecast (0.5, 0.8) vs outcome (1, 1) -> (0.25 + 0.04) / 2
    assert rps_per_match(probs, y)[0] == pytest.approx(0.145)
    assert rps_per_match(probs, y)[1] == pytest.approx(((0.2 - 1) ** 2 + (0.5 - 1) ** 2) / 2)


def test_scoring_agrees_with_existing_gate_metrics():
    rng = np.random.default_rng(3)
    raw = rng.dirichlet([2, 1.5, 1.2], size=200)
    y = rng.integers(0, 3, 200)
    assert log_loss_per_match(raw, y).mean() == pytest.approx(multiclass_log_loss(y, raw))
    assert brier_per_match(raw, y).mean() == pytest.approx(multiclass_brier_score(y, raw))


def test_rps_is_half_brier_for_two_classes():
    rng = np.random.default_rng(4)
    p_over = rng.uniform(0.05, 0.95, 50)
    probs = np.column_stack([1 - p_over, p_over])
    y = rng.integers(0, 2, 50)
    assert rps_per_match(probs, y) == pytest.approx(brier_per_match(probs, y) / 2)


def test_rps_rewards_near_misses_more_than_far_misses():
    near = rps_per_match(np.array([[0.0, 1.0, 0.0]]), np.array([0]))[0]
    far = rps_per_match(np.array([[0.0, 0.0, 1.0]]), np.array([0]))[0]
    assert near < far


def test_missing_forecast_rows_stay_missing_not_zero():
    probs = np.array([[0.5, 0.3, 0.2], [np.nan, np.nan, np.nan]])
    scores = per_match_scores(probs, np.array([0, 1]))
    assert scores.iloc[0].notna().all()
    assert scores.iloc[1].isna().all()
    table = score_table(
        scores.assign(league_code="A", cluster=["x", "y"]), n_resamples=50
    )
    pooled = table.iloc[-1]
    assert pooled["n"] == 1 and pooled["n_missing"] == 1


def test_check_probabilities_rejects_bad_rows():
    with pytest.raises(ValueError, match="sum to one"):
        check_probabilities([[0.5, 0.4, 0.2]])
    with pytest.raises(ValueError, match="non-negative"):
        check_probabilities([[1.2, -0.2, 0.0]])
    with pytest.raises(ValueError, match="partly NaN"):
        check_probabilities([[0.5, np.nan, 0.5]])
    out = check_probabilities([[0.5, 0.3, 0.2 + 1e-9], [np.nan] * 3])
    assert out[0].sum() == pytest.approx(1.0, abs=1e-15)
    assert np.isnan(out[1]).all()


# ---------------------------------------------------------------------------
# CORP reliability and decomposition
# ---------------------------------------------------------------------------


def reference_pav(values, weights):
    """Small pool-adjacent-violators reference implementation."""
    blocks = []  # [mean, weight, count]
    for value, weight in zip(values, weights):
        blocks.append([value, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            mean2, w2, c2 = blocks.pop()
            mean1, w1, c1 = blocks.pop()
            blocks.append([(mean1 * w1 + mean2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out = []
    for mean, _, count in blocks:
        out.extend([mean] * count)
    return np.array(out)


@pytest.mark.parametrize("score", ["brier", "log_loss"])
def test_corp_curve_is_isotonic_regression_and_decomposition_is_exact(score):
    rng = np.random.default_rng(5)
    p = np.round(rng.uniform(0.05, 0.95, 600), 2)  # many ties on purpose
    event = (rng.uniform(size=600) < np.clip(p * 0.7 + 0.15, 0, 1)).astype(float)
    result = corp_reliability(p, event, score=score)
    unique = np.unique(p)
    ybar = np.array([event[p == value].mean() for value in unique])
    size = np.array([(p == value).sum() for value in unique], dtype=float)
    assert result.cep == pytest.approx(reference_pav(ybar, size), abs=1e-12)
    assert np.all(np.diff(result.cep) >= -1e-12)
    assert result.mcb >= 0.0 and result.dsc >= 0.0
    assert result.mean_score == pytest.approx(result.mcb - result.dsc + result.unc, abs=1e-12)
    if score == "brier":
        assert result.mean_score == pytest.approx(np.mean((p - event) ** 2), abs=1e-12)
        assert result.unc == pytest.approx(event.mean() * (1 - event.mean()), abs=1e-12)


def test_corp_mcb_is_zero_for_a_reliable_forecaster_and_positive_for_a_miscalibrated_one():
    rng = np.random.default_rng(6)
    p = rng.uniform(0.1, 0.9, 4000)
    event = (rng.uniform(size=4000) < p).astype(float)
    good = corp_reliability(p, event, n_sim=200, seed=1)
    overconfident = np.clip(0.5 + 1.8 * (p - 0.5), 0.02, 0.98)
    bad = corp_reliability(overconfident, event, n_sim=200, seed=1)
    assert good.mcb < 0.003
    assert good.mcb_pvalue > 0.05
    assert bad.mcb > 5 * good.mcb
    assert bad.mcb_pvalue < 0.01


def test_corp_perfectly_calibrated_constant_forecast_has_zero_mcb_and_dsc():
    p = np.full(100, 0.4)
    event = np.r_[np.ones(40), np.zeros(60)]
    result = corp_reliability(p, event)
    assert result.mcb == pytest.approx(0.0, abs=1e-15)
    assert result.dsc == pytest.approx(0.0, abs=1e-15)
    assert result.mean_score == pytest.approx(result.unc)


def test_corp_helpers_for_multiclass_and_diagram_table():
    rng = np.random.default_rng(8)
    probs = rng.dirichlet([3, 2, 3], size=1000)
    y = np.array([rng.choice(3, p=row) for row in probs])
    table = corp_by_class(probs, y, CLASSES_1X2, n_sim=20)
    assert list(table["class"]) == list(CLASSES_1X2)
    assert (table["mcb"] < 0.01).all()
    two = corp_by_class(probs[:, :2] / probs[:, :2].sum(axis=1, keepdims=True), np.minimum(y, 1), ("under", "over"), binary_single=True)
    assert list(two["class"]) == ["over"]
    diagram = corp_diagram_table(corp_reliability(probs[:, 0], (y == 0).astype(float)), n_points=8)
    assert len(diagram) <= 8 and diagram["forecast"].is_monotonic_increasing


def test_corp_drops_missing_forecasts_and_validates_input():
    result = corp_reliability([0.2, np.nan, 0.8], [0, 1, 1])
    assert result.n == 2
    with pytest.raises(ValueError):
        corp_reliability([0.2, 0.3], [0, 2])
    with pytest.raises(ValueError):
        corp_reliability([1.2], [1])


# ---------------------------------------------------------------------------
# Significance: Diebold-Mariano, corrections, bootstrap
# ---------------------------------------------------------------------------


def manual_dm_variance(d, lags):
    n = len(d)
    centred = d - d.mean()
    gamma0 = centred @ centred
    total = gamma0
    for lag in range(1, lags + 1):
        total += 2 * (1 - lag / (lags + 1)) * (centred[lag:] @ centred[:-lag])
    return total / n**2


def test_dm_matches_textbook_newey_west_for_single_observation_clusters():
    rng = np.random.default_rng(9)
    d = rng.normal(0.05, 1.0, 300)
    result = diebold_mariano(d, lags=4)
    expected_se = np.sqrt(manual_dm_variance(d, 4))
    assert result.std_error == pytest.approx(expected_se)
    hln = np.sqrt((300 - 1) / 300)
    assert result.statistic == pytest.approx(d.mean() / expected_se * hln)
    assert result.n_clusters == 300 and result.lags == 4
    assert result.p_greater == pytest.approx(result.p_value / 2) if result.statistic > 0 else True


def test_dm_hac_variance_exceeds_naive_for_positively_autocorrelated_losses():
    rng = np.random.default_rng(10)
    noise = rng.normal(size=1500)
    d = np.empty(1500)
    d[0] = noise[0]
    for i in range(1, 1500):
        d[i] = 0.7 * d[i - 1] + noise[i]
    naive = diebold_mariano(d, lags=0)
    hac = diebold_mariano(d)
    assert hac.std_error > 1.5 * naive.std_error


def test_dm_has_nominal_size_and_power():
    rng = np.random.default_rng(12)
    rejections_null = sum(diebold_mariano(rng.normal(0, 1, 250)).p_value < 0.05 for _ in range(400))
    rejections_alt = sum(diebold_mariano(rng.normal(0.3, 1, 250)).p_value < 0.05 for _ in range(200))
    assert 0.02 <= rejections_null / 400 <= 0.09
    assert rejections_alt / 200 > 0.9


def test_dm_cluster_sums_control_within_matchday_dependence():
    rng = np.random.default_rng(13)
    n_clusters, size = 120, 8
    shock = np.repeat(rng.normal(0, 1, n_clusters), size)
    d = shock + rng.normal(0, 0.3, n_clusters * size)
    clusters = np.repeat(np.arange(n_clusters), size)
    naive = diebold_mariano(d, lags=0)
    clustered = diebold_mariano(d, clusters, lags=0)
    assert clustered.std_error > 2 * naive.std_error
    assert clustered.n_clusters == n_clusters


def test_dm_orders_by_time_and_handles_degenerate_samples():
    d = np.array([0.1, -0.2, 0.3, 0.05, 0.4, -0.1, 0.2, 0.15])
    ordered = diebold_mariano(d)
    reversed_input = diebold_mariano(d[::-1], order=np.arange(len(d))[::-1])
    assert reversed_input.statistic == pytest.approx(ordered.statistic)
    constant = diebold_mariano(np.full(20, 0.1))
    assert np.isnan(constant.p_value)
    assert np.isnan(diebold_mariano([0.1, 0.2]).p_value)
    with pytest.raises(ValueError):
        diebold_mariano([0.1, np.nan, 0.2])
    assert newey_west_lags(100) == 4


def test_holm_and_benjamini_hochberg_known_values_and_nan_passthrough():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    assert holm_adjust(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert benjamini_hochberg_adjust(p) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    with_nan = np.array([0.01, np.nan, 0.04])
    holm = holm_adjust(with_nan)
    assert np.isnan(holm[1]) and holm[0] == pytest.approx(0.02) and holm[2] == pytest.approx(0.04)
    assert np.isnan(benjamini_hochberg_adjust(with_nan)[1])
    assert (holm_adjust(p) >= p).all() and (benjamini_hochberg_adjust(p) >= p).all()
    assert (benjamini_hochberg_adjust(p) <= holm_adjust(p) + 1e-12).all()
    with pytest.raises(ValueError):
        holm_adjust([1.5])


def test_cluster_bootstrap_ci_is_wider_than_iid_when_matchdays_share_shocks():
    rng = np.random.default_rng(14)
    n_clusters, size = 100, 10
    clusters = np.repeat(np.arange(n_clusters), size)
    values = np.repeat(rng.normal(0, 1, n_clusters), size) + rng.normal(0, 0.2, n_clusters * size)
    clustered = cluster_bootstrap_mean_ci(values, clusters, n_resamples=1000, seed=1)
    iid = cluster_bootstrap_mean_ci(values, np.arange(len(values)), n_resamples=1000, seed=1)
    assert clustered.estimate == pytest.approx(values.mean())
    assert (clustered.upper - clustered.lower) > 2 * (iid.upper - iid.lower)
    assert clustered.lower < values.mean() < clustered.upper


def test_cluster_bootstrap_is_reproducible_and_paired_across_columns():
    rng = np.random.default_rng(15)
    matrix = rng.normal(size=(400, 3))
    clusters = np.repeat(np.arange(80), 5)
    first = cluster_bootstrap_means(matrix, clusters, n_resamples=300, seed=7)
    second = cluster_bootstrap_means(matrix, clusters, n_resamples=300, seed=7)
    assert first.shape == (300, 3) and np.array_equal(first, second)
    single = cluster_bootstrap_means(matrix[:, 1], clusters, n_resamples=300, seed=7)
    assert single == pytest.approx(first[:, 1])
    constant = cluster_bootstrap_mean_ci(np.full(50, 0.7), np.repeat(np.arange(10), 5), n_resamples=100)
    assert constant.lower == pytest.approx(0.7) and constant.upper == pytest.approx(0.7)


def test_cluster_bootstrap_matches_slow_loop_distribution():
    rng = np.random.default_rng(16)
    values = rng.normal(size=60)
    clusters = np.repeat(np.arange(12), 5)
    draws = cluster_bootstrap_means(values, clusters, n_resamples=4000, seed=2)
    sums = np.array([values[clusters == c].sum() for c in range(12)])
    assert draws.mean() == pytest.approx(values.mean(), abs=0.03)
    # standard error of the resampled mean ~ cluster-level bootstrap sd
    assert draws.std() == pytest.approx(sums.std() / 60 * np.sqrt(12) * 1.0, rel=0.15)


# ---------------------------------------------------------------------------
# Matchday assignment and data preparation
# ---------------------------------------------------------------------------


def test_assign_matchdays_splits_postponed_match_out_of_its_round():
    kick = pd.to_datetime(
        [
            "2021-08-07T14:00:00+00:00",
            "2021-08-08T14:00:00+00:00",
            "2021-08-14T14:00:00+00:00",
            "2021-08-15T14:00:00+00:00",
            "2021-11-03T19:00:00+00:00",  # postponed round-2 match played months later
        ],
        utc=True,
    )
    ns = kick.as_unit("ns").tz_localize(None).to_numpy().view("int64")
    league = np.array(["A"] * 5, dtype=object)
    season = np.full(5, 2021)
    rounds = np.array(["R1", "R1", "R2", "R2", "R2"], dtype=object)
    auto, week = assign_matchdays(league, season, rounds, ns, method="auto")
    assert len(set(auto[:2])) == 1
    assert auto[2] != auto[4] and auto[3] != auto[4]
    assert auto[0] != auto[2]
    by_round, _ = assign_matchdays(league, season, rounds, ns, method="round")
    assert len(set(by_round)) == 2
    by_week, _ = assign_matchdays(league, season, rounds, ns, method="week")
    assert len(set(by_week)) == 3
    assert week[0] == week[1] and week[0] != week[2]
    no_rounds, _ = assign_matchdays(league, season, None, ns, method="auto")
    assert len(set(no_rounds)) == 3
    blank = np.array(["", "", "R2", "R2", None], dtype=object)
    mixed, _ = assign_matchdays(league, season, blank, ns, method="auto")
    assert mixed[0] == mixed[1] and mixed[0] != mixed[2]


def test_prepare_matches_drops_unfinished_and_missing_without_imputing(synthetic, config):
    matches, _ = synthetic
    dirty = matches.head(40).copy()
    dirty.loc[0, "status_short"] = "PST"
    dirty.loc[1, ["home_goals", "away_goals"]] = [None, None]
    dirty.loc[2, "status_short"] = "AET"
    dirty.loc[3, "kickoff_utc"] = None
    prep = prepare_matches(dirty, MARKET_1X2, config)
    assert len(prep) == 36
    assert prep.dropped["not_finished"] == 2
    assert prep.dropped["missing_outcome"] == 1
    assert prep.dropped["missing_kickoff_or_season"] == 1
    assert (np.diff(prep.t) >= 0).all()
    assert "home_goals" not in prep.visible.columns and "home_goals" in prep.frame.columns
    with pytest.raises(ValueError, match="unique"):
        prepare_matches(pd.concat([matches.head(5), matches.head(5)]), MARKET_1X2, config)
    with pytest.raises(ValueError, match="missing required"):
        prepare_matches(matches.drop(columns=["season"]), MARKET_1X2, config)


# ---------------------------------------------------------------------------
# Walk-forward invariants
# ---------------------------------------------------------------------------


class RecordingModel:
    """Baseline-like model that records what the framework shows it."""

    classes = CLASSES_1X2
    calls: list[dict] = []

    def fit(self, train):
        self.train = train
        return self

    def predict(self, frame):
        RecordingModel.calls.append(
            {
                "train_last": self.train["kickoff"].max(),
                "train_n": len(self.train),
                "train_has_goals": "home_goals" in self.train.columns,
                "test_first": frame["kickoff"].min(),
                "test_columns": set(frame.columns),
                "leagues": set(frame["league_code"]),
                "rounds": set(frame["round"]),
                "seasons": set(frame["season"]),
            }
        )
        return np.tile([0.4, 0.3, 0.3], (len(frame), 1))


def test_framework_shows_models_only_the_past_and_hides_outcomes(synthetic, config):
    matches, _ = synthetic
    RecordingModel.calls = []
    result = evaluate_model(ModelSpec("recorder", RecordingModel), matches, MARKET_1X2, config)
    calls = RecordingModel.calls
    assert calls, "the model was never asked for a forecast"
    latency = pd.Timedelta(hours=config.result_latency_hours)
    for call in calls:
        assert call["train_last"] < call["test_first"] - latency + pd.Timedelta(microseconds=1)
        assert call["train_has_goals"]
        assert not call["test_columns"] & {"home_goals", "away_goals", "status_short"}
        assert len(call["leagues"]) == 1 and len(call["rounds"]) == 1 and len(call["seasons"]) == 1
    # every test-season match got exactly one forecast, one refit per matchday
    n_test = int(matches["season"].isin(config.test_seasons).sum())
    assert len(result.predictions) == n_test
    assert result.predictions["fixture_id"].is_unique
    n_matchdays = result.predictions["matchday"].nunique()
    assert len(calls) == n_matchdays
    assert (result.predictions["n_train"] >= config.min_train_matches).all()
    # the earliest test matchdays cannot use later seasons
    assert result.predictions["season"].isin(config.test_seasons).all()


def test_training_size_grows_with_the_matchday_and_never_includes_the_matchday_itself(synthetic, config):
    matches, _ = synthetic
    result = evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, config)
    frame = result.predictions
    assert (frame["group_start_ns"].to_numpy() <= frame["kickoff_ns"].to_numpy()).all()
    for name, block in frame.groupby("league_code"):
        history = matches[matches["league_code"] == name]
        history_ns = pd.to_datetime(history["kickoff_utc"], utc=True).dt.as_unit("ns").astype("int64").to_numpy()
        expected = np.array([(history_ns < s - 3 * NS_PER_HOUR).sum() for s in block["group_start_ns"]])
        assert np.array_equal(block["n_train"].to_numpy(), expected)


def test_pooled_scope_refits_weekly_across_leagues(synthetic, config):
    matches, _ = synthetic
    pooled_config = WalkForwardConfig(test_seasons=config.test_seasons, scope="pooled", min_train_matches=50)
    RecordingModel.calls = []
    result = evaluate_model(ModelSpec("recorder", RecordingModel), matches, MARKET_1X2, pooled_config)
    assert any(len(call["leagues"]) > 1 for call in RecordingModel.calls)
    assert len(result.predictions) == int(matches["season"].isin(config.test_seasons).sum())
    assert result.predictions["matchday"].str.contains("|", regex=False).all()


def test_missing_history_leaves_forecasts_missing_not_zero(synthetic):
    matches, _ = synthetic
    early = WalkForwardConfig(test_seasons=[2019], min_train_matches=50)
    result = evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, early)
    frame = result.predictions
    assert frame[["p_home", "p_draw", "p_away"]].isna().any(axis=1).any()
    assert not (frame[["p_home", "p_draw", "p_away"]] == 0).any().any()
    table = result.score_table(n_resamples=50)
    assert table.iloc[-1]["n_missing"] > 0


def test_prepared_matches_are_only_reusable_under_the_same_settings(synthetic, config):
    matches, _ = synthetic
    prep = prepare_matches(matches, MARKET_1X2, config)
    spec = league_frequency_spec(MARKET_1X2)
    same = evaluate_model(spec, prep, MARKET_1X2, config)
    direct = evaluate_model(spec, matches, MARKET_1X2, config)
    pd.testing.assert_frame_equal(same.predictions, direct.predictions)
    with pytest.raises(ValueError, match="different market"):
        evaluate_model(league_frequency_spec(over_under(2.5)), prep, over_under(2.5), config)
    with pytest.raises(ValueError, match="different market"):
        evaluate_model(spec, prep, MARKET_1X2, WalkForwardConfig(test_seasons=[2021], matchday_method="week"))


def test_model_output_shape_is_validated(synthetic, config):
    matches, _ = synthetic

    class Wrong:
        def fit(self, train):
            return self

        def predict(self, frame):
            return np.full((len(frame), 2), 0.5)

    with pytest.raises(ValueError, match="predicted shape"):
        evaluate_model(ModelSpec("wrong", Wrong), matches, MARKET_1X2, config)


def test_declared_class_order_must_match_the_market(synthetic):
    """A model that orders its columns differently would otherwise be scored against the wrong outcomes."""
    matches, _ = synthetic
    one_season = WalkForwardConfig(test_seasons=[2021], min_train_matches=50)

    class Reversed:
        classes = ("away", "draw", "home")

        def fit(self, train):
            return self

        def predict(self, frame):
            return np.tile([0.5, 0.3, 0.2], (len(frame), 1))

    class Undeclared(Reversed):
        classes = None

    with pytest.raises(ValueError, match="classes"):
        evaluate_model(ModelSpec("reversed", Reversed), matches, MARKET_1X2, one_season)
    assert len(evaluate_model(ModelSpec("undeclared", Undeclared), matches, MARKET_1X2, one_season).predictions) > 0
    accepted = evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, one_season)
    assert len(accepted.predictions) > 0


# ---------------------------------------------------------------------------
# Known result on synthetic data
# ---------------------------------------------------------------------------


def test_true_probabilities_beat_the_baseline_and_dm_is_significant(baseline_result, oracle_result):
    base = baseline_result.score_table(n_resamples=300, seed=1).set_index("league_code")
    oracle = oracle_result.score_table(n_resamples=300, seed=1).set_index("league_code")
    assert oracle.loc["ALL", "log_loss"] < base.loc["ALL", "log_loss"] - 0.02
    assert oracle.loc["ALL", "brier"] < base.loc["ALL", "brier"]
    assert oracle.loc["ALL", "rps"] < base.loc["ALL", "rps"]
    # intervals from matchday clusters bracket the estimates
    assert (oracle["log_loss_lo"] <= oracle["log_loss"]).all() and (oracle["log_loss"] <= oracle["log_loss_hi"]).all()
    comparison = compare_to_baseline(oracle_result, baseline_result, n_resamples=500, seed=2)
    pooled = comparison.iloc[-1]
    assert pooled["league_code"] == "ALL"
    assert pooled["mean_diff"] > 0.02 and pooled["ci_lo"] > 0
    assert pooled["dm_stat"] > 4 and pooled["p_value"] < 1e-4
    leagues = comparison.iloc[:-1]
    assert len(leagues) == 6
    assert (leagues["p_holm"] >= leagues["p_value"] - 1e-15).all()
    assert (leagues["p_bh"] <= leagues["p_holm"] + 1e-12).all()
    assert (leagues["p_holm"] < 0.05).sum() >= 4  # a league with weak strength dispersion may stay insignificant
    # a model is not significantly different from itself
    self_test = compare_to_baseline(baseline_result, baseline_result, n_resamples=100)
    assert self_test.iloc[-1]["mean_diff"] == 0.0 and np.isnan(self_test.iloc[-1]["p_value"])


def test_oracle_is_calibrated_and_baseline_lacks_discrimination(baseline_result, oracle_result):
    oracle_corp = oracle_result.corp(n_sim=100, seed=3).set_index("class")
    baseline_corp = baseline_result.corp(n_sim=100, seed=3).set_index("class")
    assert (oracle_corp["mcb"] < 0.005).all()
    assert (oracle_corp["mcb_pvalue"] > 0.01).all()
    # the baseline only knows league frequencies, so it discriminates little; the oracle knows the teams
    assert (oracle_corp.loc[["home", "away"], "dsc"] > 5 * baseline_corp.loc[["home", "away"], "dsc"]).all()
    assert oracle_corp["dsc"].sum() > 2 * baseline_corp["dsc"].sum()
    assert (oracle_corp["mcb"] + oracle_corp["unc"] - oracle_corp["dsc"]).round(12).equals(oracle_corp["score"].round(12))
    # miscalibrated version: sharpen the true probabilities
    sharpened = oracle_result.predictions.copy()
    cols = oracle_result.probability_columns
    powered = sharpened[cols].to_numpy() ** 2.5
    sharpened[cols] = powered / powered.sum(axis=1, keepdims=True)
    bad = corp_by_class(sharpened[cols].to_numpy(), sharpened["y"].to_numpy(), CLASSES_1X2, n_sim=100, seed=3).set_index("class")
    assert (bad["mcb"] > 5 * oracle_corp["mcb"]).all()
    assert (bad["mcb_pvalue"] < 0.05).all()


def test_pooled_league_frequencies_beat_global_frequencies_when_leagues_differ(synthetic, config, baseline_result):
    matches, _ = synthetic
    pooled_config = WalkForwardConfig(test_seasons=config.test_seasons, scope="pooled", min_train_matches=50)
    global_result = evaluate_model(global_frequency_spec(MARKET_1X2), matches, MARKET_1X2, pooled_config)
    comparison = compare_to_baseline(baseline_result, global_result, n_resamples=300)
    assert comparison.iloc[-1]["mean_diff"] > 0
    assert comparison.iloc[-1]["p_value"] < 0.05


def test_over_under_market_runs_end_to_end(synthetic, config):
    matches, _ = synthetic
    market = over_under(2.5)
    result = evaluate_model(league_frequency_spec(market), matches, market, config)
    assert result.probability_columns == ["p_under", "p_over"]
    assert result.probabilities().sum(axis=1) == pytest.approx(1.0)
    table = result.score_table(n_resamples=50)
    assert table.iloc[-1]["rps"] == pytest.approx(table.iloc[-1]["brier"] / 2)
    assert list(result.corp()["class"]) == ["over"]
    expected = ((matches["home_goals"] + matches["away_goals"]) > 2.5).astype(float)
    assert market.outcome(matches) == pytest.approx(expected.to_numpy())


# ---------------------------------------------------------------------------
# Leakage test and its mutation check
# ---------------------------------------------------------------------------


def _mid_matchday_cutoff(matches, season=2021, round_number=11):
    """Sunday 00:00 of a round: Saturday matches played, Sunday matches still to come."""
    block = matches[(matches["season"] == season) & (matches["round"] == f"Regular Season - {round_number}")]
    first = pd.to_datetime(block["kickoff_utc"], utc=True).min()
    return first.normalize() + pd.Timedelta(days=1)


def test_honest_models_pass_the_leakage_test(synthetic, config):
    matches, truth = synthetic
    cutoff = _mid_matchday_cutoff(matches)
    for spec, scope in (
        (league_frequency_spec(MARKET_1X2), "per_league"),
        (oracle_spec(truth), "per_league"),
        (global_frequency_spec(MARKET_1X2), "pooled"),
    ):
        cfg = WalkForwardConfig(test_seasons=config.test_seasons, scope=scope, min_train_matches=50)
        report = assert_no_lookahead(lambda m, s=spec, c=cfg: evaluate_model(s, m, MARKET_1X2, c), matches, MARKET_1X2, cutoff)
        assert report.passed and report.n_compared > 100
        assert {m.mode for m in report.modes} == {"shuffle", "mirror"}
        assert all(m.n_future_rows_changed > 100 and m.max_abs_difference == 0.0 for m in report.modes)


class FutureFrequencyLeaker:
    """Deliberately leaky: outcome frequencies from the WHOLE table, future included."""

    classes = CLASSES_1X2

    def __init__(self, source, per_league):
        self.source = source
        self.per_league = per_league

    def fit(self, train):
        return self

    def predict(self, frame):
        everything = self.source["matches"]
        y = MARKET_1X2.outcome(everything)
        out = np.empty((len(frame), 3))
        for i, league in enumerate(frame["league_code"]):
            mask = (everything["league_code"] == league).to_numpy() if self.per_league else np.ones(len(y), bool)
            out[i] = (np.bincount(y[mask].astype(int), minlength=3) + 1.0) / (mask.sum() + 3.0)
        return out


class OwnOutcomePeeker:
    """Deliberately leaky: nudges the forecast towards the match's own true result."""

    classes = CLASSES_1X2

    def __init__(self, source):
        self.source = source

    def fit(self, train):
        return self

    def predict(self, frame):
        everything = self.source["matches"]
        outcome = pd.Series(MARKET_1X2.outcome(everything), index=everything["fixture_id"].to_numpy())
        out = np.full((len(frame), 3), 0.2)
        for i, fixture in enumerate(frame["fixture_id"]):
            out[i, int(outcome.loc[fixture])] = 0.6
        return out


def _leaky_runner(model_cls, config, **kwargs):
    source: dict = {}

    def run(matches):
        source["matches"] = matches  # side channel: the "peeking" model sees the table under test
        spec = ModelSpec("leaky", lambda: model_cls(source, **kwargs))
        return evaluate_model(spec, matches, MARKET_1X2, config)

    return run


@pytest.mark.parametrize(
    "model_cls, kwargs",
    [
        (FutureFrequencyLeaker, {"per_league": True}),
        (FutureFrequencyLeaker, {"per_league": False}),
        (OwnOutcomePeeker, {}),
    ],
)
def test_leakage_test_catches_deliberately_leaky_models(synthetic, config, model_cls, kwargs):
    """Mutation check: the leakage test must FAIL for models that look ahead."""
    matches, _ = synthetic
    cutoff = _mid_matchday_cutoff(matches)
    run = _leaky_runner(model_cls, config, **kwargs)
    report = check_no_lookahead(run, matches, MARKET_1X2, cutoff)
    assert not report.passed
    assert report.n_forecasts_changed > 0
    with pytest.raises(LookaheadLeakError):
        assert_no_lookahead(run, matches, MARKET_1X2, cutoff)


def test_global_frequency_leak_is_only_visible_to_the_mirror_mode(synthetic, config):
    """A plain shuffle keeps global class counts, so a global-frequency leak needs the mirror mode."""
    matches, _ = synthetic
    cutoff = _mid_matchday_cutoff(matches)
    run = _leaky_runner(FutureFrequencyLeaker, config, per_league=False)
    shuffle_only = check_no_lookahead(run, matches, MARKET_1X2, cutoff, modes=("shuffle",))
    both = check_no_lookahead(run, matches, MARKET_1X2, cutoff)
    assert shuffle_only.passed  # documents the blind spot of a bare shuffle
    assert not both.passed
    assert {m.mode for m in both.modes if not m.passed} == {"mirror"}


def test_leakage_test_catches_a_broken_training_cutoff(synthetic, config, monkeypatch):
    """Mutation check on the framework itself: a cut-off that admits future matches must be caught."""
    matches, _ = synthetic
    cutoff = _mid_matchday_cutoff(matches)
    spec = league_frequency_spec(MARKET_1X2)

    def run(m):
        return evaluate_model(spec, m, MARKET_1X2, config)

    assert check_no_lookahead(run, matches, MARKET_1X2, cutoff).passed
    monkeypatch.setattr(framework, "training_cutoff_ns", lambda start, latency: start + 20 * 24 * 3600 * 10**9)
    broken = check_no_lookahead(run, matches, MARKET_1X2, cutoff)
    assert not broken.passed and broken.n_forecasts_changed > 0
    # The same broken cut-off is caught when the test cutoff sits exactly on a matchday's first kickoff.
    block = matches[(matches["season"] == 2021) & (matches["round"] == "Regular Season - 11")]
    round_start = pd.to_datetime(block["kickoff_utc"], utc=True).min()
    assert check_no_lookahead(run, matches, MARKET_1X2, round_start).passed is False
    monkeypatch.undo()
    assert check_no_lookahead(run, matches, MARKET_1X2, round_start).passed


def test_leakage_test_catches_an_off_by_one_nanosecond_cutoff(synthetic, monkeypatch):
    """With no result latency a cut-off one nanosecond too late lets the matchday's own first match into the fit."""
    matches, _ = synthetic
    zero_latency = WalkForwardConfig(test_seasons=[2021], result_latency_hours=0.0, min_train_matches=50)
    spec = league_frequency_spec(MARKET_1X2)
    block = matches[(matches["season"] == 2021) & (matches["round"] == "Regular Season - 11")]
    round_start = pd.to_datetime(block["kickoff_utc"], utc=True).min()

    def run(m):
        return evaluate_model(spec, m, MARKET_1X2, zero_latency)

    assert check_no_lookahead(run, matches, MARKET_1X2, round_start).passed
    monkeypatch.setattr(framework, "training_cutoff_ns", lambda start, latency: start - latency + 1)
    broken = check_no_lookahead(run, matches, MARKET_1X2, round_start)
    assert not broken.passed and broken.n_forecasts_changed > 0
    monkeypatch.undo()
    assert check_no_lookahead(run, matches, MARKET_1X2, round_start).passed


def test_permute_future_outcomes_touches_only_future_matches(synthetic):
    matches, _ = synthetic
    cutoff = pd.Timestamp("2021-01-01", tz="UTC")
    kickoff = pd.to_datetime(matches["kickoff_utc"], utc=True)
    for mode in ("shuffle", "mirror"):
        permuted, changed = permute_future_outcomes(matches, MARKET_1X2, cutoff, mode=mode, seed=3)
        assert changed > 0
        past = (kickoff < cutoff).to_numpy()
        pd.testing.assert_frame_equal(matches[past], permuted[past])
        assert not matches.loc[~past, ["home_goals", "away_goals"]].equals(permuted.loc[~past, ["home_goals", "away_goals"]])
    shuffled, _ = permute_future_outcomes(matches, MARKET_1X2, cutoff, mode="shuffle", seed=3)
    future = ~(kickoff < cutoff).to_numpy()
    assert sorted(shuffled.loc[future, "home_goals"]) == sorted(matches.loc[future, "home_goals"])
    with pytest.raises(ValueError):
        permute_future_outcomes(matches, MARKET_1X2, "2100-01-01")


# ---------------------------------------------------------------------------
# Nested hyper-parameter selection
# ---------------------------------------------------------------------------


class ShrunkBaseline(LeagueFrequencyBaseline):
    """League frequencies with a tunable amount of shrinkage towards uniform."""

    def __init__(self, shrink):
        super().__init__(MARKET_1X2, alpha=shrink)


SHRINK_SPEC = ModelSpec("shrunk", ShrunkBaseline, param_grid={"shrink": [1.0, 40.0, 5000.0]})


def test_nested_selection_uses_only_data_before_the_outer_season(synthetic, config):
    matches, _ = synthetic
    result = evaluate_model(SHRINK_SPEC, matches, MARKET_1X2, config)
    assert set(result.selections["outer_season"]) == set(config.test_seasons)
    assert len(result.predictions) == int(matches["season"].isin(config.test_seasons).sum())
    # heavy shrinkage (5000 pseudo-matches) is clearly worse and never selected
    assert '"shrink": 5000.0' not in " ".join(result.selections["params"])
    inner = result.inner_scores
    assert set(inner["params"]) == {'{"shrink": 1.0}', '{"shrink": 40.0}', '{"shrink": 5000.0}'}
    # Disturbing every outcome from the start of the outer season on cannot change its inner scores
    for outer in config.test_seasons:
        start = pd.to_datetime(matches.loc[matches["season"] == outer, "kickoff_utc"], utc=True).min()
        disturbed, changed = permute_future_outcomes(matches, MARKET_1X2, start, mode="shuffle", seed=5)
        assert changed > 0
        again = evaluate_model(SHRINK_SPEC, disturbed, MARKET_1X2, config)
        before = inner[inner["outer_season"] == outer].reset_index(drop=True)
        after = again.inner_scores[again.inner_scores["outer_season"] == outer].reset_index(drop=True)
        pd.testing.assert_frame_equal(before, after)
        chosen = result.selections.query("outer_season == @outer")["params"].tolist()
        assert chosen == again.selections.query("outer_season == @outer")["params"].tolist()


def test_nested_selection_passes_the_leakage_test(synthetic, config):
    matches, _ = synthetic
    start = pd.to_datetime(matches.loc[matches["season"] == 2021, "kickoff_utc"], utc=True).min()
    cutoff = start + pd.Timedelta(days=60)
    report = assert_no_lookahead(lambda m: evaluate_model(SHRINK_SPEC, m, MARKET_1X2, config), matches, MARKET_1X2, cutoff)
    assert report.passed


def test_per_league_selection_and_pooled_scope_selection_run(synthetic, config):
    matches, _ = synthetic
    per_league = WalkForwardConfig(test_seasons=[2021, 2022], select_per="league", min_train_matches=50)
    result = evaluate_model(SHRINK_SPEC, matches, MARKET_1X2, per_league)
    assert len(result.predictions) == int(matches["season"].isin([2021, 2022]).sum())
    assert set(result.selections["league"]) == set(matches["league_code"])
    assert result.predictions["params"].nunique() >= 1
    pooled = WalkForwardConfig(test_seasons=[2021], scope="pooled", min_train_matches=50)

    def spec_factory(shrink):
        return GlobalFrequencyBaseline(MARKET_1X2, alpha=shrink)

    pooled_result = evaluate_model(
        ModelSpec("global_shrunk", spec_factory, param_grid={"shrink": [1.0, 500.0]}), matches, MARKET_1X2, pooled
    )
    assert len(pooled_result.predictions) == int(matches["season"].eq(2021).sum())
    with pytest.raises(ValueError, match="select_per"):
        WalkForwardConfig(test_seasons=[2021], scope="pooled", select_per="league")
    with pytest.raises(ValueError, match="before"):
        evaluate_model(SHRINK_SPEC, matches, MARKET_1X2, WalkForwardConfig(test_seasons=[2019]))


def test_config_validation():
    with pytest.raises(ValueError):
        WalkForwardConfig(test_seasons=[])
    with pytest.raises(ValueError):
        WalkForwardConfig(test_seasons=[2021], scope="global")
    with pytest.raises(ValueError):
        WalkForwardConfig(test_seasons=[2021], min_train_matches=0)
    assert WalkForwardConfig(test_seasons=range(2021, 2024)).test_seasons == (2021, 2022, 2023)
