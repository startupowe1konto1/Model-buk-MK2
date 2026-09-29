"""Count models and count markets: corners, cards, shots and shots on target."""

from __future__ import annotations

import pickle
import sqlite3
from contextlib import closing
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from model_buk.evaluation.count_markets import (
    DEFAULT_FAMILIES,
    FAMILIES,
    STAT_COLUMNS,
    CountOutcome,
    apply_coverage_rule,
    card_feed_reliability,
    count_config,
    count_market,
    count_markets,
    coverage_table,
    leakage_market,
    load_count_matches,
)
from model_buk.evaluation.framework import (
    WalkForwardConfig,
    assert_no_lookahead,
    evaluate_model,
    prepare_matches,
)
from model_buk.evaluation.protocol import COMPARISON_CONFIG, GOAL_MARKETS
from model_buk.models.count_models import (
    CountEngine,
    FeatureConfig,
    build_design,
    RecentFrequencyBaseline,
    count_baseline_spec,
    count_recent_baseline_spec,
    count_spec,
    fit_count_glm,
    nb2_alpha_mle,
    over_probability,
)

CORNERS = FAMILIES["corners"]


# ---------------------------------------------------------------------------
# Synthetic league with real team effects
# ---------------------------------------------------------------------------


def synthetic_matches(
    n_teams: int = 10,
    seasons: tuple[int, ...] = (2020, 2021, 2022, 2023),
    seed: int = 0,
    strength: float = 0.35,
) -> pd.DataFrame:
    """A double round-robin league whose corners, shots, fouls and cards depend on the teams."""
    rng = np.random.default_rng(seed)
    team_ids = np.arange(101, 101 + n_teams)
    attack = rng.normal(0.0, strength, n_teams)
    defence = rng.normal(0.0, strength, n_teams)
    referees = np.array(["Ref A", "Ref B", "Ref C", "Ref D"])
    referee_effect = np.array([-0.3, 0.0, 0.0, 0.3])
    rows = []
    fixture_id = 1
    for season in seasons:
        start = pd.Timestamp(f"{season}-08-01 15:00", tz="UTC")
        order = list(range(n_teams))
        rounds = []
        for r in range(n_teams - 1):  # circle method
            pairs = [(order[i], order[n_teams - 1 - i]) for i in range(n_teams // 2)]
            rounds.append(pairs)
            order = [order[0]] + [order[-1]] + order[1:-1]
        rounds = rounds + [[(b, a) for a, b in pairs] for pairs in rounds]
        for r, pairs in enumerate(rounds):
            for h, a in pairs:
                referee = int(rng.integers(0, 4))
                kick = start + pd.Timedelta(days=7 * r, hours=int(rng.integers(0, 6)))
                lam_h = np.exp(1.6 + 0.15 + attack[h] + defence[a])
                lam_a = np.exp(1.6 + attack[a] + defence[h])
                corners_h, corners_a = rng.poisson(lam_h), rng.poisson(lam_a)
                shots_h, shots_a = rng.poisson(2.6 * lam_h), rng.poisson(2.6 * lam_a)
                card_scale = np.exp(referee_effect[referee])
                yellow_h, yellow_a = rng.poisson(1.9 * card_scale), rng.poisson(2.1 * card_scale)
                rows.append(
                    {
                        "fixture_id": fixture_id,
                        "league_id": 1,
                        "league_code": "TST",
                        "season": season,
                        "round": f"Regular Season - {r + 1}",
                        "kickoff_utc": kick.isoformat(),
                        "status_short": "FT",
                        "venue_id": int(team_ids[h]),
                        "referee": referees[referee],
                        "home_team_id": int(team_ids[h]),
                        "home_team": f"Team {team_ids[h]}",
                        "away_team_id": int(team_ids[a]),
                        "away_team": f"Team {team_ids[a]}",
                        "home_goals": int(rng.poisson(1.4)),
                        "away_goals": int(rng.poisson(1.1)),
                        "ht_home_goals": 0,
                        "ht_away_goals": 0,
                        "et_home_goals": np.nan,
                        "et_away_goals": np.nan,
                        "pen_home": np.nan,
                        "pen_away": np.nan,
                        "home_corners": corners_h,
                        "away_corners": corners_a,
                        "home_shots": shots_h,
                        "away_shots": shots_a,
                        "home_sot": rng.binomial(shots_h, 0.35),
                        "away_sot": rng.binomial(shots_a, 0.35),
                        "home_yellow": yellow_h,
                        "away_yellow": yellow_a,
                        "home_booking": 10.0 * yellow_h,
                        "away_booking": 10.0 * yellow_a,
                        "home_fouls": rng.poisson(12),
                        "away_fouls": rng.poisson(12),
                        "home_xg": np.nan,
                        "away_xg": np.nan,
                        "home_red": 0.0,
                        "away_red": 0.0,
                    }
                )
                fixture_id += 1
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def league() -> pd.DataFrame:
    return synthetic_matches()


def straddling_cutoff(matches: pd.DataFrame) -> pd.Timestamp:
    """A cut-off inside round 14 of season 2023: some of its matches kick off before it, some at or after it."""
    stamps = pd.to_datetime(
        matches[(matches["season"] == 2023) & (matches["round"] == "Regular Season - 14")]["kickoff_utc"], utc=True
    )
    unique = np.sort(stamps.unique())
    assert len(unique) >= 2
    return pd.Timestamp(unique[1])


def small_config(seasons=(2023,)) -> WalkForwardConfig:
    return replace(count_config(), test_seasons=tuple(seasons), min_train_matches=50)


# ---------------------------------------------------------------------------
# Markets, configuration and the inclusion rule
# ---------------------------------------------------------------------------


def test_count_outcome_reads_totals_and_teams_and_keeps_missing_missing():
    frame = pd.DataFrame({"home_corners": [5.0, 3.0, np.nan, 6.0], "away_corners": [5.0, 4.0, 4.0, np.nan]})
    total = CountOutcome("home_corners", "away_corners", "total", 9.5)(frame)
    assert total[:2].tolist() == [1.0, 0.0] and np.isnan(total[2:]).all()
    home = CountOutcome("home_corners", "away_corners", "home", 4.5)(frame)
    assert home[:2].tolist() == [1.0, 0.0]
    # A team line needs both teams' counts, so every market of a family scores the same matches.
    assert np.isnan(home[2]) and np.isnan(home[3])
    away = CountOutcome("home_corners", "away_corners", "away", 4.5)(frame)
    assert away[:2].tolist() == [1.0, 0.0]


def test_markets_hide_both_count_columns_and_are_picklable():
    markets = count_markets("corners")
    assert [m.name for m in markets][:2] == ["corners_total_8.5", "corners_total_9.5"]
    assert {m.name for m in markets} >= {"corners_home_4.5", "corners_away_5.5"}
    for market in markets:
        assert market.classes == ("under", "over")
        assert market.outcome_columns == ("home_corners", "away_corners")
    restored = pickle.loads(pickle.dumps(markets[0]))
    frame = pd.DataFrame({"home_corners": [6.0], "away_corners": [5.0]})
    assert restored.outcome(frame).tolist() == [1.0]
    with pytest.raises(ValueError):
        count_market(CORNERS, "total", 9.0)  # a whole-number line could be a push


def test_default_families_have_lines_and_columns():
    for name in DEFAULT_FAMILIES:
        family = FAMILIES[name]
        assert family.total_lines and family.team_lines
        assert set(family.columns) <= set(STAT_COLUMNS)


def test_count_config_is_the_protocol_with_more_hidden_columns():
    config = count_config()
    assert replace(config, hidden_columns=COMPARISON_CONFIG.hidden_columns) == COMPARISON_CONFIG
    assert set(STAT_COLUMNS) <= set(config.hidden_columns)
    assert set(COMPARISON_CONFIG.hidden_columns) <= set(config.hidden_columns)
    assert list(config.test_seasons) == [2022, 2023, 2024, 2025]
    assert GOAL_MARKETS  # the goal protocol itself stays untouched


def test_predict_never_sees_a_count_column(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    for column in STAT_COLUMNS:
        assert column not in prep.visible.columns
        assert column in prep.frame.columns
    assert "referee" in prep.visible.columns and "home_team_id" in prep.visible.columns


def test_coverage_rule_blanks_league_seasons_below_ninety_percent():
    rows = []
    for season, share in ((2022, 1.0), (2023, 0.5)):
        for i in range(20):
            present = i < int(share * 20)
            rows.append(
                {
                    "league_code": "AAA",
                    "season": season,
                    "status_short": "FT",
                    "home_corners": 5.0 if present else np.nan,
                    "away_corners": 4.0 if present else np.nan,
                    "home_fouls": 11.0,
                    "away_fouls": 12.0,
                }
            )
    frame = pd.DataFrame(rows)
    table = coverage_table(frame, ["corners"])
    assert table.set_index("season")["share"].to_dict() == {2022: 1.0, 2023: 0.5}
    filtered, rule = apply_coverage_rule(frame, ["corners"], min_share=0.9)
    assert rule.set_index("season")["included"].to_dict() == {2022: True, 2023: False}
    assert filtered.loc[filtered["season"] == 2022, "home_corners"].notna().all()
    assert filtered.loc[filtered["season"] == 2023, ["home_corners", "away_corners"]].isna().all().all()
    # auxiliary statistics are never filtered, and the input frame is left alone
    assert filtered["home_fouls"].notna().all()
    assert frame["home_corners"].notna().sum() == 30


def test_load_count_matches_joins_stats_and_card_events(tmp_path):
    path = tmp_path / "history.sqlite"
    matches = pd.DataFrame(
        {
            "fixture_id": [1, 2, 3],
            "league_id": 39,  # a league of the protocol (load_history_matches keeps only those)
            "league_code": "TST",
            "season": 2024,
            "round": "R1",
            "kickoff_utc": ["2024-08-01T15:00:00+00:00"] * 3,
            "status_short": "FT",
            "venue_id": 1,
            "referee": ["A", "B", None],
            "home_team_id": [10, 10, 10],
            "home_team": "H",
            "away_team_id": [20, 20, 20],
            "away_team": "A",
            "home_goals": 1,
            "away_goals": 0,
            "ht_home_goals": 0,
            "ht_away_goals": 0,
            "et_home_goals": None,
            "et_away_goals": None,
            "pen_home": None,
            "pen_away": None,
        }
    )
    stats_rows = pd.DataFrame(
        {
            "fixture_id": [1, 1, 2, 2, 3, 3],
            "team_id": [10, 20, 10, 20, 10, 20],
            "side": ["home", "away"] * 3,
            "total_shots": [12, 8, 9, 7, 10, 6],
            "shots_on_goal": [5, 3, 4, 2, 3, 2],
            "corners": [6, 4, 5, 3, None, None],
            "fouls": [10, 11, 9, 8, 12, 14],
            # fixture 1: stat present; fixture 2: NULL for the home team (no card in the feed) and 3 for away;
            # fixture 3: no event feed at all, so a NULL stays missing
            "yellow_cards": [2, 1, None, 3, None, None],
            "expected_goals": [1.2, 0.8, None, None, None, None],
        }
    )
    events = pd.DataFrame(
        [
            # fixture 1: home yellow x2 (one player twice -> sent off), home red, away yellow
            dict(fixture_id=1, seq=1, team_id=10, player_id=7, type="Card", detail="Yellow Card"),
            dict(fixture_id=1, seq=2, team_id=10, player_id=7, type="Card", detail="Yellow Card"),
            dict(fixture_id=1, seq=3, team_id=10, player_id=7, type="Card", detail="Red Card"),
            dict(fixture_id=1, seq=4, team_id=20, player_id=9, type="Card", detail="Yellow Card"),
            # fixture 2: only away cards (3 yellows), home has none; the feed exists
            dict(fixture_id=2, seq=1, team_id=20, player_id=1, type="Card", detail="Yellow Card"),
            dict(fixture_id=2, seq=2, team_id=20, player_id=2, type="Card", detail="Yellow Card"),
            dict(fixture_id=2, seq=3, team_id=20, player_id=3, type="Card", detail="Yellow Card"),
            dict(fixture_id=2, seq=4, team_id=20, player_id=3, type="Goal", detail="Normal Goal"),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        matches.to_sql("matches", connection, index=False)
        stats_rows.to_sql("team_match_stats", connection, index=False)
        events.to_sql("events", connection, index=False)
    frame = load_count_matches(path).set_index("fixture_id")
    assert frame.loc[1, ["home_corners", "away_corners"]].tolist() == [6, 4]
    assert frame.loc[1, ["home_shots", "away_shots", "home_sot", "away_sot"]].tolist() == [12, 8, 5, 3]
    # provider value wins where present
    assert frame.loc[1, ["home_yellow", "away_yellow"]].tolist() == [2, 1]
    # NULL with an event feed: the events say zero cards for the home team; the provider's 3 stands for away
    assert frame.loc[2, ["home_yellow", "away_yellow"]].tolist() == [0, 3]
    # NULL without any event feed stays missing, never zero
    assert frame.loc[3, ["home_yellow", "away_yellow", "home_red", "home_booking"]].isna().all()
    assert np.isnan(frame.loc[3, "home_corners"])
    # booking points: 10 per yellow, 25 per red, a second yellow that ends in a red counted once
    assert frame.loc[1, "home_booking"] == 10 * 1 + 25 * 1
    assert frame.loc[1, "away_booking"] == 10
    assert frame.loc[1, "home_red"] == 1 and frame.loc[2, "home_red"] == 0
    assert frame.loc[1, "home_xg"] == pytest.approx(1.2) and np.isnan(frame.loc[2, "home_xg"])


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def test_form_features_use_only_earlier_known_matches_and_skip_missing_values():
    # Team 1 (home in every row) plays five matches; the third has an unknown corner count.
    kick = pd.date_range("2024-01-01", periods=6, freq="7D", tz="UTC")
    frame = pd.DataFrame(
        {
            "fixture_id": range(6),
            "league_code": "TST",
            "kickoff": kick,
            "home_team_id": 1,
            "away_team_id": [2, 3, 4, 5, 6, 7],
            "referee": "R",
            "home_corners": [8.0, 4.0, np.nan, 6.0, 5.0, np.nan],
            "away_corners": [2.0, 4.0, 5.0, 4.0, 3.0, np.nan],
        }
    )
    train, query = frame.iloc[:5], frame.iloc[5:]
    config = FeatureConfig(windows=(2,), aux=(), use_referee=False, use_rest=False, shrink=1.0, both_sides=False)
    design = build_design(train, query.drop(columns=["home_corners", "away_corners"]), CORNERS, config)
    assert design.names == ["intercept", "is_home", "corners_for_2", "corners_opp_against_2"]
    m = design.n_matches
    # league mean over the training team-rows with both counts known (the third match is unknown):
    # home 8,4,6,5 and away 2,4,4,3 -> 36 / 8
    mean = (8 + 4 + 6 + 5 + 2 + 4 + 4 + 3) / 8
    # last two known matches of team 1 before the forecast: 6 and 5 (the unknown one is skipped)
    expected = np.log(((6 + 5) + 1.0 * mean) / (2 + 1.0) / mean)
    assert design.matrix[5, 2] == pytest.approx(expected)
    # nothing is known about team 7 (a new team): its shrunk form equals the league level, i.e. exactly 0
    assert design.matrix[m + 5, 2] == pytest.approx(0.0)
    # the opponent's conceded corners feed the home team's row: team 7 has no history either
    assert design.matrix[5, 3] == pytest.approx(0.0)
    # the forecast row carries no target
    assert np.isnan(design.target[5]) and np.isnan(design.target[m + 5])
    assert design.target[0] == 8.0 and design.target[m] == 2.0
    # a training row never sees its own value: the fourth match (6 corners) is preceded by 8 and 4 (the unknown one is skipped)
    assert design.matrix[3, 2] == pytest.approx(np.log(((8 + 4) + 1.0 * mean) / (2 + 1.0) / mean))
    # the first match of a team has no history: the league level, 0
    assert design.matrix[0, 2] == pytest.approx(0.0)


def test_features_of_a_forecast_row_do_not_change_with_later_outcomes(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    cut = 200
    frame_rows = prep.frame.iloc[cut : cut + 5]
    query = prep.visible.iloc[cut : cut + 5]
    disturbed = prep.frame.copy()
    rng = np.random.default_rng(1)
    for column in STAT_COLUMNS:
        disturbed.loc[cut:, column] = rng.permutation(disturbed.loc[cut:, column].to_numpy())
    a = build_design(prep.frame.iloc[:cut], query, CORNERS)
    b = build_design(disturbed.iloc[:cut], query, CORNERS)
    assert np.array_equal(a.matrix, b.matrix)
    assert len(frame_rows) == 5


def test_counts_in_the_frame_to_forecast_are_never_used(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    cut = 200
    train, hidden = prep.frame.iloc[:cut], prep.visible.iloc[cut : cut + 8]
    with_counts = prep.frame.iloc[cut : cut + 8]  # the same rows, outcomes included
    a = build_design(train, hidden, CORNERS)
    b = build_design(train, with_counts, CORNERS)
    assert np.array_equal(a.matrix, b.matrix, equal_nan=True)
    assert np.isnan(b.target[cut : cut + 8]).all()


# ---------------------------------------------------------------------------
# Likelihoods
# ---------------------------------------------------------------------------


def test_nb2_alpha_mle_recovers_dispersion_and_hits_the_poisson_bound():
    rng = np.random.default_rng(3)
    mu = rng.uniform(2.0, 9.0, 20_000)
    alpha = 0.35
    y = rng.negative_binomial(1.0 / alpha, (1.0 / alpha) / (1.0 / alpha + mu))
    assert nb2_alpha_mle(y, mu, np.ones_like(mu)) == pytest.approx(alpha, rel=0.08)
    poisson = rng.poisson(mu)
    assert nb2_alpha_mle(poisson, mu, np.ones_like(mu)) < 0.02
    # weights count: doubling every weight leaves the estimate unchanged
    assert nb2_alpha_mle(y, mu, np.full_like(mu, 2.0)) == pytest.approx(nb2_alpha_mle(y, mu, np.ones_like(mu)), rel=1e-4)


@pytest.mark.parametrize("kind", ["poisson", "nb2"])
def test_glm_recovers_coefficients_and_ignores_the_starting_point(kind):
    rng = np.random.default_rng(5)
    n = 6_000
    x = np.column_stack([np.ones(n), rng.normal(size=(n, 3))])
    truth = np.array([1.4, 0.3, -0.2, 0.0])
    mu = np.exp(x @ truth)
    y = rng.poisson(mu) if kind == "poisson" else rng.negative_binomial(4.0, 4.0 / (4.0 + mu))
    penalty = np.array([0.0, 1.0, 1.0, 1.0])
    weights = np.ones(n)
    beta, alpha, _ = fit_count_glm(x, y.astype(float), weights, penalty, kind)
    assert np.allclose(beta, truth, atol=0.05)
    if kind == "nb2":
        assert alpha == pytest.approx(0.25, rel=0.25)
    else:
        assert alpha == 0.0
    start = truth + np.array([0.5, -0.4, 0.3, 0.2])
    beta2, alpha2, _ = fit_count_glm(x, y.astype(float), weights, penalty, kind, beta0=start, alpha0=0.9)
    assert np.allclose(beta, beta2, atol=1e-5)
    assert alpha2 == pytest.approx(alpha, rel=1e-3, abs=1e-4)


def test_over_probability_matches_scipy_distributions():
    mean = np.array([0.4, 3.0, 9.7, 24.0])
    for line in (0.5, 4.5, 9.5, 26.5):
        assert over_probability(mean, 0.0, line, "poisson") == pytest.approx(
            np.clip(stats.poisson.sf(int(line), mean), 1e-6, 1 - 1e-6), abs=1e-12
        )
        alpha = 0.3
        r = 1.0 / alpha
        expected = stats.nbinom.sf(int(line), r, r / (r + mean))
        assert over_probability(mean, alpha, line, "nb2") == pytest.approx(np.clip(expected, 1e-6, 1 - 1e-6), abs=1e-9)


# ---------------------------------------------------------------------------
# Engine and framework integration
# ---------------------------------------------------------------------------


def test_engine_caches_by_exact_training_set_and_parameters(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    engine = CountEngine("corners", "nb2")
    train, query = prep.frame.iloc[:200], prep.visible.iloc[200:205]
    first = engine.forecast(train, query, ridge=100.0, half_life_days=None)
    again = engine.forecast(train, query, ridge=100.0, half_life_days=None)
    assert engine.n_fits == 1 and engine.n_hits == 1
    assert np.array_equal(first.mu_home, again.mu_home)
    engine.forecast(train, query, ridge=300.0, half_life_days=None)
    assert engine.n_fits == 2  # other hyper-parameters, other fit
    changed = train.copy()
    changed.loc[changed.index[50], "home_corners"] += 3.0
    engine.forecast(changed, query, ridge=100.0, half_life_days=None)
    assert engine.n_fits == 3  # same ids, different values: never a stale hit


def test_warm_start_changes_nothing_but_the_speed(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    warm, cold = CountEngine("corners", "nb2", warm_start=True), CountEngine("corners", "nb2", warm_start=False)
    for end in (150, 155, 160, 165):
        train, query = prep.frame.iloc[:end], prep.visible.iloc[end : end + 5]
        a = warm.forecast(train, query, ridge=100.0, half_life_days=720.0)
        b = cold.forecast(train, query, ridge=100.0, half_life_days=720.0)
        assert np.allclose(a.mu_home, b.mu_home, rtol=1e-5) and np.allclose(a.mu_away, b.mu_away, rtol=1e-5)
        assert a.alpha == pytest.approx(b.alpha, rel=1e-3, abs=1e-4)


def test_team_and_total_lines_are_consistent_probabilities(league):
    market = count_market(CORNERS, "total", 9.5)
    prep = prepare_matches(league, market, small_config())
    engine = CountEngine("corners", "nb2")
    forecast = engine.forecast(prep.frame.iloc[:250], prep.visible.iloc[250:260], ridge=100.0, half_life_days=None)
    lines = [7.5, 8.5, 9.5, 10.5]
    over = np.column_stack([forecast.over("total", line) for line in lines])
    assert (np.diff(over, axis=1) <= 0).all()  # a higher line is never more likely
    for scope in ("home", "away"):
        p = forecast.over(scope, 4.5)
        assert ((p > 0) & (p < 1)).all()
    assert np.isfinite(forecast.mu_home).all() and (forecast.mu_home > 0).all()
    table = forecast.over_table(total_lines=[8.5, 9.5], team_lines=[4.5])
    assert list(table.columns) == ["total_8.5", "total_9.5", "home_4.5", "away_4.5"]
    assert np.allclose(table["total_9.5"], forecast.over("total", 9.5))


@pytest.mark.parametrize("kind", ["poisson", "nb2"])
def test_model_beats_the_league_frequency_baseline_on_team_effects(league, kind):
    config = small_config(seasons=(2022, 2023))
    engine = CountEngine("corners", kind)
    market = count_market(CORNERS, "home", 4.5)
    grid = {"ridge": (30.0, 300.0), "half_life_days": (None,)}
    model = evaluate_model(count_spec(engine, "home", 4.5, grid=grid), league, market, config)
    baseline = evaluate_model(count_baseline_spec(market), league, market, config)
    assert model.model == f"count_{kind}" and baseline.model == "count_league_frequencies"
    assert len(model.predictions) == len(baseline.predictions) > 100
    model_loss = model.scores(["log_loss"])["log_loss"].mean()
    baseline_loss = baseline.scores(["log_loss"])["log_loss"].mean()
    assert model_loss < baseline_loss - 0.01
    assert set(model.selections["params"]) <= {'{"half_life_days": null, "ridge": 30.0}', '{"half_life_days": null, "ridge": 300.0}'}


@pytest.mark.parametrize("scope, line", [("total", 9.5), ("away", 4.5)])
def test_all_lines_of_a_statistic_reuse_the_fits_of_the_first_line(league, scope, line):
    config = small_config(seasons=(2023,))
    engine = CountEngine("corners", "nb2")
    single = {"ridge": (300.0,), "half_life_days": (None,)}
    first = evaluate_model(count_spec(engine, "home", 4.5, grid=single), league, count_market(CORNERS, "home", 4.5), config)
    fits = engine.n_fits
    assert fits > 10
    other = evaluate_model(count_spec(engine, scope, line, grid=single), league, count_market(CORNERS, scope, line), config)
    assert engine.n_fits == fits  # not a single new fit
    assert engine.n_hits >= fits
    assert len(other.predictions) == len(first.predictions)
    assert other.predictions["p_over"].notna().all()


def test_yellow_cards_pick_up_the_referee_effect(league):
    yellow = FAMILIES["yellow"]
    config = small_config(seasons=(2022, 2023))
    market = count_market(yellow, "total", 3.5)
    grid = {"ridge": (100.0,), "half_life_days": (None,)}
    with_ref = evaluate_model(count_spec(CountEngine("yellow", "nb2"), "total", 3.5, grid=grid), league, market, config)
    no_ref = evaluate_model(
        count_spec(CountEngine("yellow", "nb2", features=FeatureConfig(use_referee=False)), "total", 3.5, grid=grid),
        league,
        market,
        config,
    )
    assert with_ref.scores(["log_loss"])["log_loss"].mean() < no_ref.scores(["log_loss"])["log_loss"].mean()


@pytest.mark.parametrize("family", ["corners", "yellow"])
def test_no_lookahead_when_every_statistic_of_the_future_is_shuffled(league, family):
    fam = FAMILIES[family]
    config = small_config(seasons=(2023,))
    market = count_market(fam, "total", fam.total_lines[1])
    grid = {"ridge": (100.0, 1000.0), "half_life_days": (None,)}

    def run(matches: pd.DataFrame):
        engine = CountEngine(family, "nb2")  # a fresh engine: the cache never carries data over
        return evaluate_model(count_spec(engine, "total", fam.total_lines[1], grid=grid), matches, market, config)

    report = assert_no_lookahead(run, league, leakage_market(fam), straddling_cutoff(league), modes=("shuffle",))
    assert report.passed and report.n_compared > 50


def test_the_leakage_test_catches_a_model_that_sees_the_matchday_it_forecasts(league, monkeypatch):
    """Mutation check: with a broken training cut-off the shuffled-statistics test must fail."""
    from model_buk.evaluation import framework

    monkeypatch.setattr(framework, "training_cutoff_ns", lambda start, latency: start + 3 * 24 * 3_600_000_000_000)
    fam = FAMILIES["corners"]
    config = small_config(seasons=(2023,))
    market = count_market(fam, "total", 9.5)
    grid = {"ridge": (100.0,), "half_life_days": (None,)}

    def run(matches):
        return evaluate_model(count_spec(CountEngine("corners", "poisson"), "total", 9.5, grid=grid), matches, market, config)

    with pytest.raises(framework.LookaheadLeakError):
        assert_no_lookahead(run, league, leakage_market(fam), straddling_cutoff(league), modes=("shuffle",))


def test_no_lookahead_when_home_and_away_statistics_swap(league):
    """Mirror test written for count columns: swap home and away statistics of the future."""
    config = small_config(seasons=(2023,))
    market = count_market(CORNERS, "home", 4.5)
    grid = {"ridge": (100.0,), "half_life_days": (None,)}
    cutoff = straddling_cutoff(league)

    def run(matches):
        return evaluate_model(count_spec(CountEngine("corners", "poisson"), "home", 4.5, grid=grid), matches, market, config)

    original = run(league).predictions.set_index("fixture_id")
    swapped = league.copy()
    future = pd.to_datetime(swapped["kickoff_utc"], utc=True) >= cutoff
    for stat in ("corners", "shots", "sot", "yellow", "fouls"):
        left, right = swapped.loc[future, f"home_{stat}"].copy(), swapped.loc[future, f"away_{stat}"].copy()
        swapped.loc[future, f"home_{stat}"], swapped.loc[future, f"away_{stat}"] = right, left
    changed = run(swapped).predictions.set_index("fixture_id")
    early = original.index[original["group_start_ns"] <= cutoff.value]
    assert len(early) > 50
    assert np.allclose(original.loc[early, "p_over"], changed.loc[early, "p_over"], atol=1e-12)
    later = original.index[original["group_start_ns"] > cutoff.value]
    assert not np.allclose(original.loc[later, "p_over"], changed.loc[later, "p_over"])  # the swap is visible to later fits


def test_recent_baseline_follows_the_latest_level_and_never_peeks(league):
    market = count_market(CORNERS, "home", 4.5)
    prep = prepare_matches(league, market, small_config())
    train = prep.frame.iloc[:300].copy()
    model = RecentFrequencyBaseline(market, window_days=60.0, min_matches=10).fit(train)
    p = model.predict(prep.visible.iloc[300:303])
    stamps = pd.DatetimeIndex(train["kickoff"]).asi8
    recent = train[stamps >= stamps.max() - 60 * 86_400 * 10**9]
    hits = float((recent["home_corners"] > 4.5).sum())
    assert p[0, 1] == pytest.approx((hits + 1.0) / (len(recent) + 2.0))
    assert np.allclose(p.sum(axis=1), 1.0)
    # a league that is not in the training data gets no forecast (missing stays missing)
    other = prep.visible.iloc[300:302].assign(league_code="XXX")
    assert np.isnan(model.predict(other)).all()
    # too few matches inside the window: the latest ``min_matches`` are used
    tiny = RecentFrequencyBaseline(market, window_days=0.5, min_matches=25).fit(train)
    last = train.iloc[-25:]
    assert tiny.predict(prep.visible.iloc[300:301])[0, 1] == pytest.approx(((last["home_corners"] > 4.5).sum() + 1.0) / 27.0)
    spec = count_recent_baseline_spec(market)
    assert spec.name == "count_recent_frequencies"
    result = evaluate_model(spec, league, market, small_config())
    assert result.predictions["p_over"].notna().any()
    with pytest.raises(ValueError):
        RecentFrequencyBaseline(market, window_days=0.0)


def test_engine_rejects_unknown_kind():
    with pytest.raises(ValueError):
        CountEngine("corners", "gaussian")


# ---------------------------------------------------------------------------
# The evaluation script: summaries, resumable run, report
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_count_models.py"
    spec = importlib.util.spec_from_file_location("run_count_models_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_market_reports_pooled_league_and_calibration_tables(league, script):
    config = small_config(seasons=(2022, 2023))
    market = count_market(CORNERS, "home", 4.5)
    grid = {"ridge": (100.0, 1000.0), "half_life_days": (None,)}
    model = evaluate_model(count_spec(CountEngine("corners", "nb2"), "home", 4.5, grid=grid), league, market, config)
    baseline = evaluate_model(count_baseline_spec(market), league, market, config)
    tables = script.summarize_market(model, baseline, n_resamples=100, n_sim=5)
    assert set(tables) == {"pooled", "by_league", "reliability", "by_season", "selections"}
    pooled = tables["pooled"].iloc[0]
    assert pooled["market"] == "corners_home_4.5" and pooled["model"] == "count_nb2"
    assert pooled["scope"] == "home" and pooled["line"] == 4.5 and pooled["n_leagues"] == 1
    assert pooled["diff"] == pytest.approx(pooled["ll_base"] - pooled["ll_model"])
    assert pooled["diff"] > 0.01 and pooled["diff_lo"] < pooled["diff"] < pooled["diff_hi"]
    assert 0.0 <= pooled["mean_p_over"] <= 1.0 and 0.0 <= pooled["freq_over"] <= 1.0
    # Brier = MCB - DSC + UNC holds for the model and the baseline
    assert pooled["brier_model"] == pytest.approx(pooled["mcb_model"] - pooled["dsc_model"] + pooled["unc"], abs=1e-9)
    assert pooled["brier_base"] == pytest.approx(pooled["mcb_base"] - pooled["dsc_base"] + pooled["unc"], abs=1e-9)
    assert pooled["dsc_model"] > pooled["dsc_base"]
    leagues = tables["by_league"]
    assert leagues["league_code"].tolist() == ["TST"] and leagues["n"].iloc[0] == pooled["n"]
    assert len(tables["reliability"]) >= 2 and set(tables["by_season"]["season"]) == {2022, 2023}
    assert set(tables["selections"]["outer_season"]) == {2022, 2023}


def test_run_family_task_is_resumable_and_finalize_writes_forecasts(league, script, tmp_path):
    partial = tmp_path / "count_partial"
    partial.mkdir()
    league.to_pickle(partial / "matches.pkl")
    task = {
        "family": "corners",
        "partial_dir": str(partial),
        "resamples": 50,
        "corp_sims": 3,
        "leagues": [],
        "test_seasons": [2023],
        "grid": {"ridge": (100.0, 1000.0), "half_life_days": (None,)},
    }
    for kind in ("poisson", "nb2"):
        first = script.run_family_task({**task, "kind": kind})
        finished = sorted(partial.glob(f"corners__{kind}__*.pkl"))
        assert first["markets"] == len(finished) == len(count_markets("corners"))
        stamps = {p: p.stat().st_mtime_ns for p in finished}
        script.run_family_task({**task, "kind": kind})  # a rerun skips every finished market
        assert {p: p.stat().st_mtime_ns for p in finished} == stamps
    # the 365-day benchmark leaves ``recent__*`` files next to the market payloads; finalize must not read them as payloads
    script.run_recent_task({"family": "corners", "partial_dir": str(partial), "resamples": 50})
    assert list(partial.glob("recent__corners__*.pkl"))
    root = tmp_path / "predictions"
    root.mkdir()
    saved = script.finalize(root, partial)
    assert saved["forecast_tables"] == 2 * len(count_markets("corners"))
    from model_buk.evaluation.protocol import load_predictions

    market = count_market(CORNERS, "total", 9.5)
    stored = load_predictions("count_poisson", market, root)
    baseline = load_predictions("count_league_frequencies", market, root)
    assert len(stored) == len(baseline) > 30
    assert np.allclose(stored[["p_under", "p_over"]].sum(axis=1), 1.0)
    tables = script.load_evaluation(root)
    assert {"pooled", "by_league", "reliability", "selections", "run_info"} <= set(tables)
    assert set(tables["pooled"]["model"]) == {"count_poisson", "count_nb2"}
    report = script.build_report(tables, small_config(seasons=(2023,)), n_resamples=50, n_sim=3)
    assert report.startswith("# Rzuty rożne, kartki i strzały")
    for heading in ("## Wynik zbiorczy", "## Wynik w ligach", "## Kalibracja (CORP)", "## Test przecieku", "## Ograniczenia i luki"):
        assert heading in report


def _write_history(path, matches, stats_rows, events):
    with closing(sqlite3.connect(path)) as connection:
        matches.to_sql("matches", connection, index=False)
        stats_rows.to_sql("team_match_stats", connection, index=False)
        events.to_sql("events", connection, index=False)


def test_an_event_feed_without_cards_is_missing_data_not_zero_cards(tmp_path):
    """Review fix: "feed exists, no card event" means zero cards only where the feed records cards at all."""
    rows = []
    for season, first_id in ((2016, 100), (2023, 200)):
        for i in range(10):
            rows.append(
                {
                    "fixture_id": first_id + i,
                    "league_id": 39,
                    "league_code": "TST",
                    "season": season,
                    "round": f"R{i}",
                    "kickoff_utc": f"{season}-08-{i + 1:02d}T15:00:00+00:00",
                    "status_short": "FT",
                    "venue_id": 1,
                    "referee": "A",
                    "home_team_id": 10,
                    "home_team": "H",
                    "away_team_id": 20,
                    "away_team": "A",
                    "home_goals": 1,
                    "away_goals": 0,
                    "ht_home_goals": 0,
                    "ht_away_goals": 0,
                    "et_home_goals": None,
                    "et_away_goals": None,
                    "pen_home": None,
                    "pen_away": None,
                }
            )
    matches = pd.DataFrame(rows)
    stats_rows = pd.DataFrame(
        [
            {
                "fixture_id": fid,
                "team_id": team,
                "side": side,
                "total_shots": 10,
                "shots_on_goal": 4,
                "corners": 5,
                # a foul count of 0 for both teams is a placeholder; one team at 0 is a real value
                "fouls": 0 if fid in (100, 101) or (fid == 102 and side == "home") else 12,
                "yellow_cards": None,
                "expected_goals": None,
            }
            for fid in matches["fixture_id"]
            for team, side in ((10, "home"), (20, "away"))
        ]
    )
    events = []
    for fid in matches["fixture_id"]:
        events.append(dict(fixture_id=fid, seq=1, team_id=10, player_id=1, type="Goal", detail="Normal Goal"))
        # 2023: the feed records a yellow card of the home team in 9 of 10 fixtures; 2016: in none of them
        if 200 <= fid < 209:
            events.append(dict(fixture_id=fid, seq=2, team_id=10, player_id=2, type="Card", detail="Yellow Card"))
    path = tmp_path / "history.sqlite"
    _write_history(path, matches, stats_rows, pd.DataFrame(events))
    frame = load_count_matches(path).set_index("fixture_id")
    old = frame.loc[100:109]
    assert old[["home_yellow", "away_yellow", "home_red", "home_booking"]].isna().all().all()  # missing, not zero
    new = frame.loc[200:209]
    assert new.loc[200:208, "home_yellow"].eq(1.0).all() and new.loc[200:208, "away_yellow"].eq(0.0).all()
    # the ninth fixture without a card is a real zero: 9 of 10 fixtures meets the 90% threshold
    assert new.loc[209, ["home_yellow", "away_yellow"]].tolist() == [0.0, 0.0]
    # a stricter threshold turns the whole league-season into missing values
    strict = load_count_matches(path, min_card_feed_share=0.95).set_index("fixture_id")
    assert strict.loc[200:209, ["home_yellow", "away_yellow"]].isna().all().all()
    # the coverage rule then removes what the feed rule left missing
    filtered, table = apply_coverage_rule(load_count_matches(path), ["yellow"])
    share = table.set_index("season")["share"].to_dict()
    assert share == {2016: 0.0, 2023: 1.0}
    assert filtered.loc[filtered["season"] == 2016, ["home_yellow", "away_yellow"]].isna().all().all()
    # fouls: 0 and 0 is a placeholder (missing); 0 and 12 is a real match
    assert frame.loc[100, ["home_fouls", "away_fouls"]].isna().all()
    assert frame.loc[102, ["home_fouls", "away_fouls"]].tolist() == [0.0, 12.0]
    assert frame.loc[103, ["home_fouls", "away_fouls"]].tolist() == [12.0, 12.0]


def test_card_feed_reliability_uses_finished_fixtures_with_a_feed_per_league_season():
    frame = pd.DataFrame(
        {
            "fixture_id": range(8),
            "league_code": ["A"] * 4 + ["B"] * 4,
            "season": [2020] * 8,
            "status_short": ["FT", "FT", "FT", "PST", "FT", "FT", "FT", "FT"],
            "_has_feed": [True, True, True, True, True, True, False, False],
        }
    )
    # League A: three finished fixtures with a feed, two with cards (the postponed one does not count).
    # League B: two finished fixtures with a feed and none with a card; two without any feed.
    reliable = card_feed_reliability(frame, {0, 1, 3}, min_share=0.6)
    assert reliable.tolist() == [True] * 4 + [False] * 4
    assert not card_feed_reliability(frame, {0, 1, 3}, min_share=0.7).any()


def test_total_line_uses_its_own_dispersion_when_the_two_team_counts_are_correlated():
    """Review addition: a shared match shock makes the total more dispersed than each team's count."""
    matches = synthetic_matches(n_teams=16)
    shock = np.random.default_rng(11).poisson(4.0, len(matches))
    matches["home_corners"] = matches["home_corners"] + shock
    matches["away_corners"] = matches["away_corners"] + shock
    prep = prepare_matches(matches, count_market(CORNERS, "total", 9.5), small_config())
    n = 700
    forecast = CountEngine("corners", "nb2").forecast(
        prep.frame.iloc[:n], prep.visible.iloc[n : n + 240], ridge=100.0, half_life_days=None
    )
    observed = (prep.frame["home_corners"] + prep.frame["away_corners"]).iloc[n : n + 240].to_numpy()
    mean = forecast.mu_home + forecast.mu_away
    empirical = float(((observed - mean) ** 2).mean())
    implied = float((mean + forecast.alpha_total * mean**2).mean())
    assert forecast.alpha_total > 0.01 and forecast.alpha_total > 3 * forecast.alpha
    assert implied == pytest.approx(empirical, rel=0.15)  # the team dispersion alone would miss by about a third


def test_time_decay_weights_recent_matches_more():
    """Review addition: a short half-life follows a recent level shift, no decay averages the whole history."""
    matches = synthetic_matches()
    prep = prepare_matches(matches, count_market(CORNERS, "total", 9.5), small_config())
    train, query = prep.frame.iloc[:250].copy(), prep.visible.iloc[250:256]
    train.loc[train.index[:125], ["home_corners", "away_corners"]] += 6.0  # an old, much higher level
    plain = FeatureConfig(windows=(), aux=(), use_referee=False, use_rest=False)
    flat = CountEngine("corners", "nb2", features=plain).forecast(train, query, ridge=10.0, half_life_days=None)
    decayed = CountEngine("corners", "nb2", features=plain).forecast(train, query, ridge=10.0, half_life_days=60.0)
    assert flat.mu_home.mean() > decayed.mu_home.mean() + 1.0
    assert flat.mu_away.mean() > decayed.mu_away.mean() + 1.0


def test_referee_feature_uses_only_the_referees_earlier_matches():
    kick = pd.date_range("2024-01-01", periods=7, freq="7D", tz="UTC")
    frame = pd.DataFrame(
        {
            "fixture_id": range(7),
            "league_code": "TST",
            "kickoff": kick,
            "home_team_id": [1, 2, 3, 4, 5, 6, 7],
            "away_team_id": [11, 12, 13, 14, 15, 16, 17],
            "referee": ["A", "B", "A", None, "A", "B", "A"],
            "home_corners": [6.0, 3.0, 5.0, 4.0, 9.0, 2.0, np.nan],
            "away_corners": [4.0, 2.0, 5.0, 4.0, 7.0, 3.0, np.nan],
        }
    )
    train, query = frame.iloc[:6], frame.iloc[6:].drop(columns=["home_corners", "away_corners"])
    config = FeatureConfig(windows=(), aux=(), use_rest=False, referee_shrink=2.0)
    design = build_design(train, query, CORNERS, config)
    column = design.names.index("referee")
    mean = np.mean([10.0, 5.0, 10.0, 8.0, 16.0, 5.0])  # league mean of the match totals in the training rows

    def expected(total: float, matches: int) -> float:
        return float(np.log((total + 2.0 * mean) / (matches + 2.0) / mean))

    m = design.n_matches
    feature = design.matrix[:, column]
    assert feature[0] == pytest.approx(0.0)  # a referee's first match: league level
    assert feature[2] == pytest.approx(expected(10.0, 1))  # A before his second match: only match 0
    assert feature[4] == pytest.approx(expected(10.0 + 10.0, 2))  # A before match 4: matches 0 and 2, never match 4 itself
    assert feature[5] == pytest.approx(expected(5.0, 1))  # B
    assert feature[3] == pytest.approx(0.0)  # no referee named: neutral, never a value of some other referee
    assert feature[6] == pytest.approx(expected(10.0 + 10.0 + 16.0, 3))  # forecast row of A: all his earlier matches
    assert np.array_equal(feature[:m], feature[m:])  # the same value for both team-rows of a match
