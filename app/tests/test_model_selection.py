"""Tests for model comparison on identical matches and the ex-ante model selection (offline, synthetic)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import model_buk.evaluation.model_selection as ms
from model_buk.evaluation.framework import MARKET_1X2, compare_to_baseline, over_under
from model_buk.evaluation.protocol import PROTOCOL_ID, load_predictions, save_result
from model_buk.evaluation.significance import benjamini_hochberg_adjust, diebold_mariano, holm_adjust

OU = over_under(2.5)
SEASONS = (2022, 2023, 2024, 2025)


# ---------------------------------------------------------------------------
# Synthetic forecast tables
# ---------------------------------------------------------------------------


def make_fixtures(n_leagues=3, seasons=SEASONS, rounds=6, per_round=4, seed=3, n_classes=2):
    """One row per fixture: league, season, matchday, kickoff, outcome (random, so forecasts must earn their score)."""
    rng = np.random.default_rng(seed)
    rows = []
    fixture_id = 1
    for league in range(n_leagues):
        for season in seasons:
            for round_no in range(rounds):
                kickoff = pd.Timestamp(f"{season}-01-05", tz="UTC") + pd.Timedelta(days=14 * round_no, hours=league)
                for j in range(per_round):
                    rows.append(
                        {
                            "fixture_id": fixture_id,
                            "league_code": f"L{league}",
                            "season": season,
                            "matchday": f"L{league}|{season}|r:{round_no}",
                            "kickoff": (kickoff + pd.Timedelta(minutes=j)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "y": int(rng.integers(0, n_classes)),
                        }
                    )
                    fixture_id += 1
    return pd.DataFrame(rows)


def binary_table(fixtures: pd.DataFrame, p_over: np.ndarray) -> pd.DataFrame:
    table = fixtures.copy()
    table["params"] = "{}"
    table["p_under"] = 1.0 - p_over
    table["p_over"] = p_over
    return table


def oracle(fixtures: pd.DataFrame, seasons: tuple[int, ...], strength: float = 0.8) -> np.ndarray:
    """P(over) that reflects the realised outcome in ``seasons`` (a deliberate cheat) and is 0.5 elsewhere."""
    p = np.full(len(fixtures), 0.5)
    hit = fixtures["season"].isin(seasons).to_numpy()
    y = fixtures["y"].to_numpy()
    p[hit] = np.where(y[hit] == 1, strength, 1.0 - strength)
    return p


def flip_panel(fixtures: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Model A is an oracle in 2022 and 2024, model B in 2023 and 2025, the baseline is flat."""
    return {
        "baseline_league_freq": binary_table(fixtures, np.full(len(fixtures), 0.5)),
        "model_a": binary_table(fixtures, oracle(fixtures, (2022, 2024))),
        "model_b": binary_table(fixtures, oracle(fixtures, (2023, 2025))),
    }


@pytest.fixture()
def fixtures() -> pd.DataFrame:
    return make_fixtures()


@pytest.fixture()
def flip(fixtures) -> ms.ForecastPanel:
    return ms.build_panel(flip_panel(fixtures), OU)


# ---------------------------------------------------------------------------
# Panel and intersection
# ---------------------------------------------------------------------------


def test_common_mask_is_the_intersection_and_missing_is_never_filled(fixtures):
    n = len(fixtures)
    p = np.full(n, 0.4)
    a = binary_table(fixtures, p.copy())
    b = binary_table(fixtures, p.copy())
    c = binary_table(fixtures, p.copy())
    a.loc[0:9, ["p_under", "p_over"]] = np.nan  # a: first 10 fixtures
    b.loc[5:14, ["p_under", "p_over"]] = np.nan  # b: overlapping 10
    c = c.drop(index=[20, 21])  # c: two fixtures absent from its table
    panel = ms.build_panel({"a": a, "b": b, "c": c}, OU)
    mask = panel.common_mask()
    fixture_ids = panel.meta["fixture_id"].to_numpy()
    expected = set(fixtures["fixture_id"]) - set(fixtures["fixture_id"].iloc[list(range(0, 15)) + [20, 21]])
    assert set(fixture_ids[mask]) == expected
    assert np.isnan(panel.probs["c"][~panel.present["c"]]).all()
    assert (~panel.present["c"]).sum() == 2
    # a NaN row is not a zero: no model's mean forecast moved because of the gap
    assert np.nanmean(panel.probs["a"][:, 1]) == pytest.approx(0.4)


def test_common_mask_restricted_to_seasons(flip):
    mask = flip.common_mask(seasons=(2023, 2024))
    assert set(flip.season[mask]) == {2023, 2024}
    assert mask.sum() == np.isin(flip.season, (2023, 2024)).sum()


def test_coverage_report_counts_shared_specific_and_lost(fixtures):
    p = np.full(len(fixtures), 0.4)
    base = binary_table(fixtures, p.copy())
    a = binary_table(fixtures, p.copy())
    b = binary_table(fixtures, p.copy())
    early = fixtures["league_code"].eq("L0").to_numpy() & fixtures["season"].eq(2022).to_numpy() & (np.arange(len(fixtures)) < 10)
    for table in (base, a, b):  # every model, including the baseline, lacks the first matches of L0
        table.loc[early, ["p_under", "p_over"]] = np.nan
    only_b = np.zeros(len(fixtures), dtype=bool)
    only_b[[100, 101, 102]] = True
    b.loc[only_b, ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel({"baseline_league_freq": base, "a": a, "b": b}, OU)
    table = ms.coverage_report(panel).set_index("model")
    n_early = int(early.sum())
    assert table.loc["a", "n_missing"] == n_early
    assert table.loc["a", "n_missing_shared"] == n_early
    assert table.loc["a", "n_missing_specific"] == 0
    assert table.loc["a", "n_lost_to_others"] == 3  # a forecast them, b did not
    assert table.loc["b", "n_missing_specific"] == 3
    assert table.loc["b", "n_lost_to_others"] == 0
    assert table.loc["b", "n_common"] == len(fixtures) - n_early - 3
    assert table.loc["b", "leagues_shared"].startswith("L0 ")
    assert (table["n_common"] == table.loc["b", "n_common"]).all()


def test_coverage_report_for_one_season(fixtures):
    p = np.full(len(fixtures), 0.4)
    a = binary_table(fixtures, p.copy())
    a.loc[fixtures["season"].eq(2023).to_numpy(), ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel({"baseline_league_freq": binary_table(fixtures, p), "a": a}, OU)
    table = ms.coverage_report(panel, seasons=(2022,)).set_index("model")
    assert table.loc["a", "n_missing"] == 0
    table = ms.coverage_report(panel, seasons=(2023,)).set_index("model")
    assert table.loc["a", "n_missing"] == table.loc["a", "n_fixtures"]


def test_tables_that_disagree_about_an_outcome_are_refused(fixtures):
    a = binary_table(fixtures, np.full(len(fixtures), 0.4))
    b = a.copy()
    b.loc[7, "y"] = 1 - b.loc[7, "y"]
    with pytest.raises(ValueError, match="disagree"):
        ms.build_panel({"a": a, "b": b}, OU)


def test_partly_missing_rows_and_bad_probabilities_are_refused(fixtures):
    a = binary_table(fixtures, np.full(len(fixtures), 0.4))
    a.loc[3, "p_under"] = np.nan
    with pytest.raises(ValueError, match="partly NaN"):
        ms.build_panel({"a": a}, OU)
    b = binary_table(fixtures, np.full(len(fixtures), 0.4))
    b.loc[3, "p_under"] = 0.9
    with pytest.raises(ValueError, match="sum to one"):
        ms.build_panel({"b": b}, OU)


def test_panel_is_sorted_by_kickoff_and_accepts_timestamps(fixtures):
    shuffled = fixtures.sample(frac=1.0, random_state=1).reset_index(drop=True)
    shuffled["kickoff"] = pd.to_datetime(shuffled["kickoff"], utc=True)
    panel = ms.build_panel({"a": binary_table(shuffled, np.full(len(shuffled), 0.4))}, OU)
    assert (np.diff(panel.kickoff_ns) >= 0).all()
    assert len(panel) == len(fixtures)


def test_load_panel_skips_models_without_a_market_table(tmp_path, fixtures):
    panel = ms.build_panel({"a": binary_table(fixtures, np.full(len(fixtures), 0.4))}, OU)
    result = ms.derived_result(panel, panel.probs["a"], model="a", params=["{}"] * len(panel), selections=pd.DataFrame({"outer_season": [2022]}))
    save_result(result, tmp_path)
    loaded, skipped = ms.load_panel(OU, ["a", "b_missing_file"], tmp_path)
    assert loaded.models == ("a",)
    assert skipped == {"b_missing_file": "no file"}
    # a model with a file but no table for the market is skipped with a reason; nothing left to compare is an error
    with pytest.raises(ValueError, match="no forecast tables"):
        ms.load_panel(MARKET_1X2, ["a"], tmp_path)


# ---------------------------------------------------------------------------
# Selection never uses the scored season
# ---------------------------------------------------------------------------


def test_selection_season_is_the_previous_one():
    assert ms.selection_season(2024) == 2023
    assert ms.selection_season(2023) == 2022


def test_choice_for_a_season_uses_only_the_season_before(flip):
    run = ms.run_selection(flip, ["baseline_league_freq", "model_a", "model_b"], (2023, 2024, 2025))
    chosen = dict(zip(run.choices["scored_season"], run.choices["chosen"]))
    # A is the oracle of 2022 and 2024, B of 2023 and 2025: the choice for S follows S - 1 ...
    assert chosen == {2023: "model_a", 2024: "model_b", 2025: "model_a"}
    # ... and is therefore the worse model on the scored season (an implementation that looked at S would pick the other)
    for scored, model in chosen.items():
        rows = flip.season == scored
        other = "model_b" if model == "model_a" else "model_a"
        assert flip.losses(model)[rows].mean() > flip.losses(other)[rows].mean()
        picked = run.selected[rows]
        assert np.allclose(picked, flip.probs[model][rows])
    assert (run.choices["fit_season"] == run.choices["scored_season"] - 1).all()


def test_first_season_has_no_selection_and_stays_nan(flip):
    run = ms.run_selection(flip, ["baseline_league_freq", "model_a", "model_b"], (2023, 2024, 2025))
    first = flip.season == 2022
    for name, matrix in run.forecasts().items():
        assert np.isnan(matrix[first]).all(), name
        assert not np.isnan(matrix[~first]).any(), name
    with pytest.raises(ValueError, match="no common forecasts in season 2021"):
        ms.run_selection(flip, ["baseline_league_freq", "model_a"], (2022,))


def test_choice_ignores_forecasts_and_outcomes_of_the_scored_and_later_seasons(flip):
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    reference = ms.run_selection(flip, candidates, (2024,))
    rng = np.random.default_rng(0)
    probs = {name: matrix.copy() for name, matrix in flip.probs.items()}
    later = flip.season >= 2024
    for name in probs:
        noise = rng.uniform(0.05, 0.95, size=later.sum())
        probs[name][later] = np.column_stack([1.0 - noise, noise])
    y = flip.y.copy()
    y[later] = 1 - y[later]
    changed = ms.ForecastPanel(flip.market, flip.meta, probs, flip.present).with_outcomes(y)
    again = ms.run_selection(changed, candidates, (2024,))
    assert again.choices["chosen"].tolist() == reference.choices["chosen"].tolist()
    pd.testing.assert_frame_equal(
        again.weights.drop(columns=["objective"]), reference.weights.drop(columns=["objective"])
    )
    assert again.weights["objective"].tolist() == reference.weights["objective"].tolist()


def test_selection_leak_check_passes_and_is_informative(flip):
    report = ms.assert_selection_no_lookahead(flip, ["baseline_league_freq", "model_a", "model_b"], (2023, 2024, 2025))
    assert report.passed and report.informative
    assert report.n_changed == 0
    assert all(season.n_compared > 0 for season in report.seasons)


def test_mutation_selecting_on_the_scored_season_is_caught(flip, monkeypatch):
    """A rule that selects on season S instead of S - 1 must fail the look-ahead check."""
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    monkeypatch.setattr(ms, "selection_season", lambda season: season)
    report = ms.check_selection_no_lookahead(flip, candidates, (2023, 2024, 2025))
    assert not report.passed
    assert report.n_changed > 0
    with pytest.raises(ms.SelectionLeakError, match="scored season"):
        ms.assert_selection_no_lookahead(flip, candidates, (2023, 2024, 2025))


def test_mutation_selecting_on_a_later_season_is_caught(flip, monkeypatch):
    monkeypatch.setattr(ms, "selection_season", lambda season: min(season + 1, 2025))
    with pytest.raises(ms.SelectionLeakError):
        ms.assert_selection_no_lookahead(flip, ["baseline_league_freq", "model_a", "model_b"], (2023, 2024))


def test_leak_check_is_not_vacuous_when_nothing_depends_on_outcomes(fixtures):
    """Identical forecasts everywhere and 50/50 outcomes cannot move: the check says it learned nothing."""
    p = np.full(len(fixtures), 0.5)
    panel = ms.build_panel({"a": binary_table(fixtures, p.copy()), "b": binary_table(fixtures, p.copy())}, OU)
    # b's forecasts equal a's, so ties resolve to the first candidate whatever the outcomes are; the pool is
    # unique thanks to the ridge, so neither the choice nor the weights react to the disturbance
    report = ms.check_selection_no_lookahead(panel, ["a", "b"], (2023, 2024))
    assert report.passed and not report.informative
    with pytest.raises(ms.SelectionLeakError, match="vacuous"):
        ms.assert_selection_no_lookahead(panel, ["a", "b"], (2023, 2024))


def test_selection_uses_only_matches_every_candidate_forecast(fixtures):
    p_a = oracle(fixtures, (2022,))
    p_b = np.full(len(fixtures), 0.5)
    a = binary_table(fixtures, p_a)
    b = binary_table(fixtures, p_b)
    hole = fixtures["season"].eq(2022).to_numpy() & (np.arange(len(fixtures)) % 2 == 0)
    b.loc[hole, ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel({"a": a, "b": b}, OU)
    run = ms.run_selection(panel, ["a", "b"], (2023,))
    in_2022 = fixtures["season"].eq(2022).to_numpy()
    assert run.choices["n_fit"].iloc[0] == int((in_2022 & ~hole).sum())


# ---------------------------------------------------------------------------
# Pool weights
# ---------------------------------------------------------------------------


def test_pool_weights_are_fitted_on_the_season_before_only(flip):
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    run = ms.run_selection(flip, candidates, (2023, 2024))
    for scored in (2023, 2024):
        rows = (flip.season == scored - 1) & flip.common_mask(candidates)
        expected = ms.fit_log_linear_pool([flip.probs[m][rows] for m in candidates], flip.y[rows])
        got = run.weights[(run.weights["scored_season"] == scored) & (run.weights["kind"] == "log_linear")]
        assert got["weight"].to_numpy() == pytest.approx(expected.weights)
        assert got["fit_season"].eq(scored - 1).all()
        # the pool forecast of season S is the pool of the S-1 weights applied to the season S forecasts
        target = flip.season == scored
        again = ms.apply_log_linear_pool([flip.probs[m][target] for m in candidates], expected.weights)
        assert np.allclose(run.pool[target], again)


def test_pool_follows_the_previous_season_not_the_scored_one(flip):
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    run = ms.run_selection(flip, candidates, (2023,))
    weights = run.weights[(run.weights["kind"] == "log_linear")].set_index("model")["weight"]
    assert weights["model_a"] > 0.9  # A was the oracle of 2022 (B is the oracle of the scored season 2023)


def test_log_linear_pool_recovers_known_weights():
    rng = np.random.default_rng(5)
    n = 60_000
    p_a = rng.uniform(0.1, 0.9, n)
    p_b = rng.uniform(0.1, 0.9, n)
    logit = 0.7 * np.log(p_a / (1 - p_a)) + 0.3 * np.log(p_b / (1 - p_b))
    truth = 1.0 / (1.0 + np.exp(-logit))
    y = (rng.uniform(size=n) < truth).astype(int)
    fit = ms.fit_log_linear_pool([np.column_stack([1 - p_a, p_a]), np.column_stack([1 - p_b, p_b])], y)
    assert fit.weights.sum() == pytest.approx(1.0)
    assert fit.weights == pytest.approx([0.7, 0.3], abs=0.05)


def test_linear_pool_recovers_known_mixture():
    rng = np.random.default_rng(6)
    n = 80_000
    p_a = rng.uniform(0.05, 0.95, n)
    p_b = rng.uniform(0.05, 0.95, n)
    truth = 0.35 * p_a + 0.65 * p_b
    y = (rng.uniform(size=n) < truth).astype(int)
    fit = ms.fit_linear_pool([np.column_stack([1 - p_a, p_a]), np.column_stack([1 - p_b, p_b])], y)
    assert fit.weights == pytest.approx([0.35, 0.65], abs=0.05)


def test_pool_weights_stay_on_the_simplex_and_copies_share_equally():
    rng = np.random.default_rng(7)
    n = 20_000
    p = rng.uniform(0.1, 0.9, n)
    y = (rng.uniform(size=n) < p).astype(int)
    forecast = np.column_stack([1 - p, p])
    flat = np.full((n, 2), 0.5)
    for fitter in (ms.fit_log_linear_pool, ms.fit_linear_pool):
        fit = fitter([forecast, forecast.copy(), flat], y)
        assert (fit.weights >= 0).all() and fit.weights.sum() == pytest.approx(1.0)
        assert fit.weights[0] == pytest.approx(fit.weights[1], abs=1e-3)
        assert fit.weights[2] < 0.05


def test_pool_of_three_way_market_sums_to_one():
    rng = np.random.default_rng(8)
    raw = rng.dirichlet([4, 3, 3], size=(3, 500))
    y = rng.integers(0, 3, 500)
    fit = ms.fit_log_linear_pool(list(raw), y)
    pooled = ms.apply_log_linear_pool(list(raw), fit.weights)
    assert pooled.shape == (500, 3)
    assert np.allclose(pooled.sum(axis=1), 1.0)
    single = ms.apply_log_linear_pool([raw[0]], np.ones(1))
    assert np.allclose(single, raw[0])


def test_pool_fit_is_convex_and_beats_every_single_model_in_sample():
    rng = np.random.default_rng(9)
    n = 30_000
    strength = rng.normal(size=n)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-strength))).astype(int)
    a = 1 / (1 + np.exp(-(strength + rng.normal(scale=0.7, size=n))))
    b = 1 / (1 + np.exp(-(strength + rng.normal(scale=0.7, size=n))))
    forecasts = [np.column_stack([1 - a, a]), np.column_stack([1 - b, b])]
    fit = ms.fit_log_linear_pool(forecasts, y, ridge=0.0)

    def loss(p):
        return float(-np.mean(np.log(p[np.arange(n), y])))

    pooled = loss(ms.apply_log_linear_pool(forecasts, fit.weights))
    assert pooled < min(loss(f) for f in forecasts)
    for start in ([1.0, 0.0], [0.0, 1.0], [0.5, 0.5]):
        assert loss(ms.apply_log_linear_pool(forecasts, np.array(start))) >= pooled - 1e-9


# ---------------------------------------------------------------------------
# Missing stays missing
# ---------------------------------------------------------------------------


def test_selected_and_pool_are_nan_where_a_needed_forecast_is_missing(fixtures):
    tables = flip_panel(fixtures)
    hole = fixtures["season"].eq(2024).to_numpy() & (np.arange(len(fixtures)) % 3 == 0)
    tables["model_a"].loc[hole, ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel(tables, OU)
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    run = ms.run_selection(panel, candidates, (2023, 2024, 2025))
    hole_ids = set(fixtures.loc[hole, "fixture_id"])
    is_hole = panel.meta["fixture_id"].isin(hole_ids).to_numpy()
    assert np.isnan(run.pool[is_hole]).all()  # a component is missing
    assert np.isnan(run.pool_linear[is_hole]).all()
    chosen_2024 = run.choices.set_index("scored_season").loc[2024, "chosen"]
    assert chosen_2024 == "model_b"  # so the selected forecast there exists
    assert not np.isnan(run.selected[is_hole]).any()
    # 2025 is decided by 2024, where the hole rows are dropped for everyone: n_fit shows the drop
    assert run.choices.set_index("scored_season").loc[2025, "n_fit"] == int((panel.season == 2024).sum() - is_hole.sum())


# ---------------------------------------------------------------------------
# Paired comparison
# ---------------------------------------------------------------------------


def make_effect_panel(seed=13):
    """Model = baseline plus a league-specific edge (from none to strong) on random outcomes."""
    fixtures = make_fixtures(n_leagues=5, rounds=12, per_round=10, seed=seed)
    rng = np.random.default_rng(seed)
    y = fixtures["y"].to_numpy()
    edge = fixtures["league_code"].map({"L0": 0.0, "L1": 0.02, "L2": 0.06, "L3": 0.12, "L4": 0.2}).to_numpy()
    p_model = np.clip(0.5 + np.where(y == 1, edge, -edge) + rng.normal(scale=0.03, size=len(y)), 0.05, 0.95)
    return ms.build_panel(
        {"baseline_league_freq": binary_table(fixtures, np.full(len(y), 0.5)), "model": binary_table(fixtures, p_model)}, OU
    )


def test_paired_comparison_matches_compare_to_baseline():
    panel = make_effect_panel()
    mask = panel.common_mask()
    table = ms.paired_comparison(
        panel.losses("baseline_league_freq")[mask],
        panel.losses("model")[mask],
        league=panel.league[mask],
        cluster=panel.matchday[mask],
        order=panel.kickoff_ns[mask],
        n_resamples=200,
        seed=0,
    )
    base = ms.derived_result(panel, panel.probs["baseline_league_freq"], model="b", params=["{}"] * len(panel), selections=pd.DataFrame())
    model = ms.derived_result(panel, panel.probs["model"], model="m", params=["{}"] * len(panel), selections=pd.DataFrame())
    reference = compare_to_baseline(model, base, n_resamples=200, seed=0)
    assert table["league_code"].tolist() == reference["league_code"].tolist()
    for column in ("n", "n_clusters", "mean_diff", "ci_lo", "ci_hi", "dm_stat", "p_value", "p_holm", "p_bh"):
        assert table[column].to_numpy(dtype=float) == pytest.approx(reference[column].to_numpy(dtype=float), nan_ok=True), column


def test_bonferroni_interval_excludes_zero_exactly_when_the_raw_p_value_is_below_alpha_over_m():
    panel = make_effect_panel()
    mask = panel.common_mask()
    table = ms.paired_comparison(
        panel.losses("baseline_league_freq")[mask],
        panel.losses("model")[mask],
        league=panel.league[mask],
        cluster=panel.matchday[mask],
        order=panel.kickoff_ns[mask],
        n_resamples=100,
    )
    leagues = table[table["league_code"] != ms.POOLED_LABEL]
    m = len(leagues)
    excludes_zero = (leagues["ci_adj_lo"] > 0) | (leagues["ci_adj_hi"] < 0)
    assert excludes_zero.tolist() == (leagues["p_value"] < 0.05 / m).tolist()
    assert (leagues["ci_adj_hi"] - leagues["ci_adj_lo"] > 0).all()
    pooled = table[table["league_code"] == ms.POOLED_LABEL].iloc[0]
    assert pooled["ci_adj_hi"] > pooled["ci_adj_lo"]
    assert leagues["mean_diff"].is_monotonic_increasing  # the edge grows with the league number by construction
    assert leagues["p_holm"].between(0, 1).all()
    assert leagues["p_holm"].to_numpy() == pytest.approx(holm_adjust(leagues["p_value"].to_numpy()))


def test_bonferroni_interval_widens_with_the_family_and_is_nan_without_a_standard_error():
    mean = np.array([0.01, 0.01, 0.01, 0.01])
    se = np.array([0.002, 0.002, np.nan, 0.002])
    groups = np.array([100.0, 100.0, 100.0, 2.0])
    lo1, hi1 = ms.bonferroni_interval(mean, se, groups, 1)
    lo47, hi47 = ms.bonferroni_interval(mean, se, groups, 47)
    assert (hi47[:2] - lo47[:2] > hi1[:2] - lo1[:2]).all()
    assert np.isnan([lo1[2], hi1[2], lo1[3], hi1[3]]).all()  # no standard error, or fewer than three matchdays
    # a single comparison at 95%: t(99) * se / sqrt(99 / 100)
    assert (hi1[0] - lo1[0]) / 2 == pytest.approx(1.984217 * 0.002 / np.sqrt(0.99), rel=1e-5)


def test_identical_forecasts_give_a_zero_difference_and_no_p_value():
    panel = make_effect_panel()
    mask = panel.common_mask()
    loss = panel.losses("model")[mask]
    dust = loss + 1e-16 * np.arange(len(loss))  # what renormalisation leaves behind
    table = ms.paired_comparison(
        loss, dust, league=panel.league[mask], cluster=panel.matchday[mask], order=panel.kickoff_ns[mask], n_resamples=50
    )
    assert (table["mean_diff"] == 0.0).all()
    assert table["p_value"].isna().all() and table["dm_stat"].isna().all()


def test_paired_comparison_ignores_matches_without_a_forecast():
    panel = make_effect_panel()
    loss_ref = panel.losses("baseline_league_freq")
    loss_model = panel.losses("model").copy()
    loss_model[:50] = np.nan
    table = ms.paired_comparison(
        loss_ref, loss_model, league=panel.league, cluster=panel.matchday, order=panel.kickoff_ns, n_resamples=50
    )
    assert table["n"].iloc[-1] == len(panel) - 50


def test_significance_counts_separate_better_and_worse():
    versus = pd.DataFrame(
        {
            "league_code": ["A", "B", "C", "D", ms.POOLED_LABEL],
            "mean_diff": [0.01, 0.02, -0.02, 0.001, 0.005],
            "p_value": [0.001, 0.2, 0.01, 0.5, 0.0001],
            "p_holm": [0.004, 0.6, 0.06, 0.9, np.nan],  # league C is significant raw but not after Holm
            "p_bh": [0.004, 0.3, 0.02, 0.6, np.nan],
        }
    )
    counts = ms.significance_counts(versus)
    assert counts["n_leagues"] == 4 and counts["n_better"] == 3
    assert counts["better_raw"] == 1 and counts["worse_raw"] == 1
    assert counts["better_holm"] == 1 and counts["worse_holm"] == 0
    assert counts["better_bh"] == 1 and counts["worse_bh"] == 1


def test_paired_comparison_adjusts_moderate_p_values_across_leagues():
    """Holm and Benjamini-Hochberg must move the p-values that are neither tiny nor huge (the strong-edge panel hides that)."""
    rng = np.random.default_rng(31)
    means = {"L0": 0.0, "L1": 0.004, "L2": 0.008, "L3": 0.012, "L4": 0.016}
    league = np.repeat(list(means), 300).astype(object)
    diff = np.concatenate([rng.normal(mu, 0.1, 300) for mu in means.values()])
    cluster = np.array([f"{lg}|{i // 5}" for lg, i in zip(league, np.tile(np.arange(300), 5))], dtype=object)
    table = ms.paired_comparison(
        diff, np.zeros_like(diff), league=league, cluster=cluster, order=np.arange(len(diff)), n_resamples=50
    )
    leagues = table[table["league_code"] != ms.POOLED_LABEL]
    raw = leagues["p_value"].to_numpy()
    assert leagues["p_holm"].to_numpy() == pytest.approx(holm_adjust(raw))
    assert leagues["p_bh"].to_numpy() == pytest.approx(benjamini_hochberg_adjust(raw))
    moved = leagues[(raw > 1e-3) & (raw < 0.5)]
    assert len(moved) >= 2
    assert (moved["p_holm"] > moved["p_value"] * 1.5).all()  # Holm multiplies by up to m = 5
    assert (leagues["p_bh"] <= leagues["p_holm"] + 1e-12).all()


def test_choice_table_reports_the_runner_up_and_its_margin(flip):
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    run = ms.run_selection(flip, candidates, (2023, 2024))
    for row in run.choices.itertuples():
        rows = (flip.season == row.fit_season) & flip.common_mask(candidates)
        means = {m: float(flip.losses(m)[rows].mean()) for m in candidates}
        ordered = sorted(means, key=means.get)
        assert row.chosen == ordered[0] and row.runner_up == ordered[1]
        assert row.margin == pytest.approx(means[ordered[1]] - means[ordered[0]])
        assert np.isnan(row.margin_se) or row.margin_se > 0  # an exactly constant gap has no standard error
        for m in candidates:
            assert getattr(row, f"ll_{m}") == pytest.approx(means[m])


def test_select_model_reports_the_paired_standard_error_of_the_gap():
    panel = make_effect_panel()
    losses = {m: panel.losses(m) for m in panel.models}
    rows = panel.common_mask()
    choice, table = ms.select_model(
        losses, rows, ["baseline_league_freq", "model"], cluster=panel.matchday, order=panel.kickoff_ns
    )
    assert choice == "model"
    diff = losses["baseline_league_freq"][rows] - losses["model"][rows]
    expected = diebold_mariano(diff, panel.matchday[rows], order=panel.kickoff_ns[rows]).std_error
    entry = table.set_index("model").loc["baseline_league_freq"]
    assert entry["gap"] == pytest.approx(float(diff.mean()))
    assert entry["gap_se"] == pytest.approx(expected) and expected > 0


def test_scores_by_season_matches_a_manual_average(flip):
    mask = flip.common_mask()
    table = ms.scores_by_season(flip, ["model_a"], mask)
    assert sorted(table["season"]) == list(SEASONS)
    for row in table.itertuples():
        rows = mask & (flip.season == row.season)
        assert row.n == int(rows.sum())
        assert row.log_loss == pytest.approx(float(flip.losses("model_a")[rows].mean()))


# ---------------------------------------------------------------------------
# Calendar-year leagues: selection sample cut at the first kickoff of the scored season
# ---------------------------------------------------------------------------


def overlapping_panel():
    """Late rounds of season 2022 are played in February 2023, after the first match of season 2023.

    Model A is a good oracle on the early 2022 rounds and confidently wrong on the late ones; B is a mild
    oracle throughout.  Using all of 2022 picks B, using only what finished before 2023 began picks A.
    """
    fx = make_fixtures(n_leagues=3, rounds=6, per_round=6, seed=11)
    late = (fx["season"].eq(2022) & fx["matchday"].str.endswith(("r:4", "r:5"))).to_numpy()
    kick = pd.to_datetime(fx["kickoff"], utc=True)
    shifted = pd.Timestamp("2023-02-01", tz="UTC") + pd.to_timedelta(np.arange(len(fx)), unit="s")
    fx["kickoff"] = kick.where(~late, shifted).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    y = fx["y"].to_numpy()
    p_a = np.full(len(fx), 0.5)
    in_2022 = fx["season"].eq(2022).to_numpy()
    p_a[in_2022 & ~late] = np.where(y[in_2022 & ~late] == 1, 0.8, 0.2)
    p_a[late] = np.where(y[late] == 1, 0.02, 0.98)
    p_b = np.full(len(fx), 0.5)
    p_b[in_2022] = np.where(y[in_2022] == 1, 0.6, 0.4)
    tables = {
        "baseline_league_freq": binary_table(fx, np.full(len(fx), 0.5)),
        "model_a": binary_table(fx, p_a),
        "model_b": binary_table(fx, p_b),
    }
    return ms.build_panel(tables, OU), late, fx


def test_strict_selection_sample_stops_at_the_first_kickoff_of_the_scored_season():
    panel, late, fx = overlapping_panel()
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    loose = ms.run_selection(panel, candidates, (2023,))
    strict = ms.run_selection(panel, candidates, (2023,), before_first_kickoff=True)
    early_2022 = int((fx["season"].eq(2022) & ~pd.Series(late)).sum())
    assert loose.choices["chosen"].iloc[0] == "model_b" and strict.choices["chosen"].iloc[0] == "model_a"
    assert loose.choices["n_fit"].iloc[0] == int(fx["season"].eq(2022).sum())
    assert strict.choices["n_fit"].iloc[0] == early_2022
    # the late 2022 outcomes cannot reach the strict choice, the pool weights or the per-league choice ...
    y = panel.y.copy()
    late_rows = (panel.season == 2022) & (panel.kickoff_ns >= pd.Timestamp("2023-02-01", tz="UTC").value)
    assert late_rows.sum() == late.sum()
    y[late_rows] = 1 - y[late_rows]
    disturbed_strict = ms.run_selection(panel.with_outcomes(y), candidates, (2023,), before_first_kickoff=True)
    assert disturbed_strict.weights["weight"].to_numpy() == pytest.approx(strict.weights["weight"].to_numpy())
    assert disturbed_strict.choices["chosen"].tolist() == strict.choices["chosen"].tolist()
    assert np.allclose(disturbed_strict.pool, strict.pool, equal_nan=True)
    # ... while the main rule does react to them (this is the effect the check measures)
    disturbed_loose = ms.run_selection(panel.with_outcomes(y), candidates, (2023,))
    assert not np.allclose(disturbed_loose.weights["weight"].to_numpy(), loose.weights["weight"].to_numpy(), atol=1e-3)


def test_calendar_year_check_compares_the_two_rules_season_by_season(flip):
    panel, _, _ = overlapping_panel()
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    table = ms.calendar_year_check(panel, candidates, (2023, 2024, 2025))
    first = table.set_index("scored_season").loc[2023]
    assert first["chosen_main"] == "model_b" and first["chosen_strict"] == "model_a"
    assert first["n_fit_strict"] < first["n_fit_main"]
    assert first["max_weight_difference"] > 0.05
    assert np.isfinite(table.drop(columns=["chosen_main", "chosen_strict"]).to_numpy(dtype=float)).all()
    # with no overlap between the seasons the strict rule is the main rule
    plain = ms.calendar_year_check(flip, candidates, (2023, 2024, 2025))
    assert (plain["chosen_main"] == plain["chosen_strict"]).all()
    assert (plain["n_fit_main"] == plain["n_fit_strict"]).all()
    assert (plain["max_weight_difference"] < 1e-12).all()
    assert plain["pool_log_loss_main"].to_numpy() == pytest.approx(plain["pool_log_loss_strict"].to_numpy())
    # the coming season (no matches) is left out of the table
    assert ms.calendar_year_check(flip, candidates, (2023, 2026))["scored_season"].tolist() == [2023]


# ---------------------------------------------------------------------------
# Whole comparison
# ---------------------------------------------------------------------------


def test_compare_models_scores_every_model_on_the_same_matches(fixtures):
    n = len(fixtures)
    rng = np.random.default_rng(2)
    tables = {
        "baseline_league_freq": binary_table(fixtures, np.full(n, 0.5)),
        "good": binary_table(fixtures, oracle(fixtures, SEASONS, 0.7)),
        "flat": binary_table(fixtures, np.clip(0.5 + rng.normal(scale=0.02, size=n), 0.1, 0.9)),
    }
    tables["good"].loc[:9, ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel(tables, OU)
    result = ms.compare_models(panel, ["good", "flat"], n_resamples=100, corp_sims=0)
    scores = result["scores"]
    pooled = scores[scores["league_code"] == ms.POOLED_LABEL].set_index("model")
    assert (pooled["n"] == n - 10).all()  # everyone on the intersection
    assert result["ranking"]["model"].iloc[0] == "good"
    versus = result["versus_reference"]
    assert set(versus["model"]) == {"good", "flat"} and set(versus["reference"]) == {"baseline_league_freq"}
    best = result["versus_best"].query("league_code == 'ALL'").set_index("model")
    assert best.loc["flat", "reference"] == "good" and best.loc["good", "reference"] == "flat"
    corp = result["corp"]
    assert set(corp["class"]) == {"over"} and set(corp["model"]) == {"baseline_league_freq", "good", "flat"}
    assert corp.loc[corp["model"] == "good", "mean_p"].iloc[0] == pytest.approx(
        np.nanmean(panel.probs["good"][:, 1][panel.common_mask(["baseline_league_freq", "good", "flat"])])
    )


def test_compare_models_refuses_an_empty_intersection(fixtures):
    p = np.full(len(fixtures), 0.5)
    a = binary_table(fixtures, p.copy())
    b = binary_table(fixtures, p.copy())
    a.loc[:, ["p_under", "p_over"]] = np.nan
    panel = ms.build_panel({"baseline_league_freq": b, "a": a}, OU)
    with pytest.raises(ValueError, match="no match has a forecast"):
        ms.compare_models(panel, ["a"], n_resamples=10)


def test_three_way_market_panel_and_scores():
    fixtures = make_fixtures(n_classes=3, rounds=5)
    rng = np.random.default_rng(4)
    raw = rng.dirichlet([5, 3, 4], size=len(fixtures))
    frames = {}
    for name in ("baseline_league_freq", "m"):
        table = fixtures.copy()
        table["params"] = "{}"
        table[["p_home", "p_draw", "p_away"]] = raw
        frames[name] = table
    panel = ms.build_panel(frames, MARKET_1X2)
    result = ms.compare_models(panel, ["m"], n_resamples=50, corp_sims=0)
    corp = result["corp"]
    assert set(corp["class"]) == {"home", "draw", "away"}
    assert (result["versus_reference"].query("league_code == 'ALL'")["mean_diff"].abs() < 1e-12).all()


# ---------------------------------------------------------------------------
# Per-league choice shrunk to the pooled one
# ---------------------------------------------------------------------------


def league_losses(sizes, advantage, noise=0.3, seed=0):
    """Losses of ``b`` and ``m`` where ``m`` is better than ``b`` by ``advantage[league]`` on average."""
    rng = np.random.default_rng(seed)
    leagues, base, other = [], [], []
    for name, n in sizes.items():
        b = rng.normal(1.0, noise, n)
        m = b - advantage[name] + rng.normal(0.0, noise, n)
        leagues += [name] * n
        base.append(b)
        other.append(m)
    return np.array(leagues, dtype=object), {"b": np.concatenate(base), "m": np.concatenate(other)}


def test_per_league_choice_keeps_the_pooled_choice_without_real_differences():
    sizes = {f"L{i}": 400 for i in range(12)}
    league, losses = league_losses(sizes, {name: -0.02 for name in sizes})
    table = ms.select_models_per_league(losses, league, np.ones(len(league), dtype=bool), "b", ["b", "m"])
    assert (table["chosen"] == "b").all()


def test_per_league_choice_switches_only_with_strong_evidence():
    sizes = {f"L{i}": 400 for i in range(30)}
    sizes["BIG"] = 2500
    sizes["TINY"] = 6
    advantage = {name: -0.03 for name in sizes}
    advantage["BIG"] = 0.08
    league, losses = league_losses(sizes, advantage, seed=2)
    tiny = league == "TINY"
    losses["m"][tiny] = losses["b"][tiny] - 0.05  # a lucky handful of matches: the raw per-league choice would switch
    assert (losses["b"][tiny] - losses["m"][tiny]).mean() > 0
    table = ms.select_models_per_league(losses, league, np.ones(len(league), dtype=bool), "b", ["b", "m"]).set_index("league_code")
    assert table.loc["BIG", "chosen"] == "m"
    assert table.loc["TINY", "chosen"] == "b"
    switched = table[table["chosen"] == "m"]
    assert (switched["advantage"] > 0).all()
    assert len(switched) <= 2  # the 30 ordinary leagues keep the pooled choice up to one lucky league
    assert table.loc["BIG", "shrinkage_weight"] > 0.9


def test_per_league_choice_gives_leagues_without_selection_data_the_pooled_choice():
    sizes = {"A": 300, "B": 300, "C": 300}
    league, losses = league_losses(sizes, {"A": 0.1, "B": -0.1, "C": 0.0})
    rows = league != "C"
    table = ms.select_models_per_league(losses, league, rows, "b", ["b", "m"]).set_index("league_code")
    assert table.loc["C", "chosen"] == "b"


# ---------------------------------------------------------------------------
# Selection helpers and storage
# ---------------------------------------------------------------------------


def test_select_model_breaks_ties_by_candidate_order_and_refuses_gaps():
    losses = {"x": np.array([0.5, 0.5, 0.9]), "y": np.array([0.5, 0.5, 0.1])}
    choice, table = ms.select_model(losses, np.array([True, True, False]), ["x", "y"])
    assert choice == "x" and table["gap"].abs().max() == 0
    choice, _ = ms.select_model(losses, np.array([True, True, True]), ["x", "y"])
    assert choice == "y"
    with pytest.raises(ValueError, match="missing forecasts"):
        ms.select_model({"x": np.array([0.1, np.nan]), "y": np.array([0.2, 0.2])}, np.array([True, True]), ["x", "y"])
    with pytest.raises(ValueError, match="no forecasts"):
        ms.select_model(losses, np.zeros(3, dtype=bool))


def test_derived_forecasts_round_trip_through_the_protocol_storage(tmp_path, flip):
    candidates = ["baseline_league_freq", "model_a", "model_b"]
    run = ms.run_selection(flip, candidates, (2023, 2024, 2025))
    selections, inner = ms.selection_tables(run, "selected")
    result = ms.derived_result(
        flip, run.selected, model=ms.SELECTED_NAME, params=ms.selected_params(flip, run), selections=selections, inner_scores=inner
    )
    save_result(result, tmp_path)
    pool_selections, pool_inner = ms.selection_tables(run, "log_linear")
    save_result(
        ms.derived_result(flip, run.pool, model=ms.POOL_NAME, params=ms.pool_params(flip, run), selections=pool_selections, inner_scores=pool_inner),
        tmp_path,
    )
    stored = load_predictions(ms.SELECTED_NAME, OU, tmp_path)
    assert set(stored["protocol_id"]) == {PROTOCOL_ID}
    assert len(stored) == len(flip)
    first = stored["season"] == 2022
    assert stored.loc[first, ["p_under", "p_over"]].isna().all().all()  # the selection season is not forecast
    assert stored.loc[~first, ["p_under", "p_over"]].notna().all().all()
    assert np.allclose(stored[["p_under", "p_over"]].sum(axis=1)[~first], 1.0)
    params = stored.loc[stored["season"] == 2024, "params"].map(json.loads)
    assert (params.map(lambda p: p["selected"]) == "model_b").all() and (params.map(lambda p: p["fit_season"]) == 2023).all()
    pool_params = load_predictions(ms.POOL_NAME, OU, tmp_path).query("season == 2025")["params"].map(json.loads)
    assert (pool_params.map(lambda p: p["fit_season"]) == 2024).all()
    assert pool_params.iloc[0]["weights"].keys() == {"baseline_league_freq", "model_a", "model_b"}
    # the stored forecasts read back into a panel with the same numbers
    panel, skipped = ms.load_panel(OU, [ms.SELECTED_NAME, ms.POOL_NAME], tmp_path)
    assert skipped == {}
    order = np.argsort(flip.meta["fixture_id"].to_numpy())
    back = panel.probs[ms.SELECTED_NAME][np.argsort(panel.meta["fixture_id"].to_numpy())]
    assert np.allclose(back, run.selected[order], equal_nan=True)


# ---------------------------------------------------------------------------
# The whole script on synthetic stored forecasts
# ---------------------------------------------------------------------------


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "compare_goal_models.py"
    spec = importlib.util.spec_from_file_location("compare_goal_models_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_market_tables(market, strengths, seed):
    """Forecast tables per model whose skill grows with ``strength`` (they peek at the outcome on purpose)."""
    k = len(market.classes)
    fixtures = make_fixtures(n_leagues=3, rounds=8, per_round=6, seed=seed, n_classes=k)
    rng = np.random.default_rng(seed)
    onehot = np.eye(k)[fixtures["y"].to_numpy()]
    tables = {}
    for name, strength in strengths.items():
        logits = strength * onehot + rng.normal(scale=0.4, size=(len(fixtures), k))
        probs = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs /= probs.sum(axis=1, keepdims=True)
        table = fixtures.copy()
        table["params"] = "{}"
        for j, label in enumerate(market.classes):
            table[f"p_{label}"] = probs[:, j]
        tables[name] = table
    return tables


def test_script_runs_end_to_end_on_synthetic_predictions(tmp_path):
    module = _load_script()
    strengths = {"baseline_league_freq": 0.0, "dixon_coles": 0.4, "gas_poisson": 0.6, "pi_gbm": 1.0}
    for seed, market in enumerate(module.markets_in_order()):
        tables = _synthetic_market_tables(market, strengths, seed)
        for name, table in tables.items():
            panel = ms.build_panel({name: table}, market)
            result = ms.derived_result(
                panel, panel.probs[name], model=name, params=["{}"] * len(panel), selections=pd.DataFrame({"outer_season": [2022]})
            )
            save_result(result, tmp_path)
    args = SimpleNamespace(
        predictions_dir=tmp_path, resamples=40, corp_sims=0, seed=0, summary=tmp_path / "summary.json", out=tmp_path / "report.md", force=False, stage="all"
    )
    summary = module.stage_compare(args)
    assert all(entry["leakage_passed"] for entry in summary["markets"].values())
    report = module.stage_report(args).read_text(encoding="utf-8")
    assert "## Odpowiedź w skrócie" in report and "`pool_models`" in report and "`pi_gbm`" in report
    tables = module.read_tables(tmp_path / module.COMPARISON_DB)
    assert set(tables["selection_choices"]["market"]) == {market.name for market in module.markets_in_order()}
    assert (tables["selection_leakage"]["n_changed"] == 0).all()
    assert (tables["selection_leakage_mutation"]["n_changed"] > 0).all()  # the broken rule is caught on the same data
    for market in module.markets_in_order():
        for model in (ms.SELECTED_NAME, ms.POOL_NAME):
            stored = load_predictions(model, market, tmp_path)
            assert set(stored["protocol_id"]) == {PROTOCOL_ID}
            assert stored.loc[stored["season"] == 2022, market_columns(market)].isna().all().all()
            assert stored.loc[stored["season"] > 2022, market_columns(market)].notna().all().all()
    choices = tables["selection_choices"]
    assert (choices["fit_season"] == choices["scored_season"] - 1).all()
    assert (choices.loc[choices["n_scored"] > 0, "chosen"] == "pi_gbm").all()  # the strongest synthetic model wins every time
    # the calendar-year check is part of the comparison, and its own stage refreshes only that table
    calendar = tables["calendar_year_check"]
    assert set(calendar["market"]) == {market.name for market in module.markets_in_order()}
    assert (calendar["chosen_main"] == calendar["chosen_strict"]).all()  # the synthetic seasons do not overlap
    assert "`calendar_year_check`" in report
    module.stage_calendar(args)
    again = module.read_tables(tmp_path / module.COMPARISON_DB)
    assert set(again) == set(tables)
    for name, frame in tables.items():
        pd.testing.assert_frame_equal(again[name], frame, check_exact=False)


def market_columns(market):
    return [f"p_{label}" for label in market.classes]
