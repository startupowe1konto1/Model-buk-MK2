"""Tests of the sequential-model adapters: GAS-Poisson by maximum likelihood and Elo-Davidson.

Everything is offline and synthetic.  The properties that matter for the
comparison protocol are pinned down here: the likelihood gradient is exact, the
maximum-likelihood fit recovers known parameters, the ratings and states use
only past matches, probabilities are valid for every goal market, one fit
serves all markets through the cache, and the framework's leakage test passes.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from model_buk.evaluation.framework import (
    MARKET_1X2,
    Market,
    WalkForwardConfig,
    check_no_lookahead,
    evaluate_model,
    over_under,
)
from model_buk.evaluation.protocol import GOAL_MARKETS, MARKET_BTTS
from model_buk.models import gas_poisson as gas
from model_buk.models.dynamic_adapters import (
    EloDavidsonForecaster,
    FitCache,
    GasPoissonForecaster,
    _training_arrays,
    elo_davidson_spec,
    gas_poisson_spec,
    goal_market_probabilities,
    poisson_score_matrix,
)
from model_buk.models.elo_davidson import (
    EloDavidsonConfig,
    EloDavidsonModel,
    _elo_pass,
    davidson_mle,
    davidson_probabilities,
    fit_elo_arrays,
)

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------


def _simulate_gas(n_teams: int, n_rounds: int, theta: dict[str, float], seed: int):
    """Matches from the observation-driven recursion itself (so the filtered likelihood is exact)."""
    rng = np.random.default_rng(seed)
    attack = np.zeros(n_teams)
    defence = np.zeros(n_teams)
    home_index, away_index, goals_h, goals_a = [], [], [], []
    for _ in range(n_rounds):
        order = rng.permutation(n_teams)
        for pair in range(n_teams // 2):
            h, a = int(order[2 * pair]), int(order[2 * pair + 1])
            rate_h = math.exp(theta["c"] + theta["h"] + attack[h] + defence[a])
            rate_a = math.exp(theta["c"] + attack[a] + defence[h])
            yh, ya = int(rng.poisson(rate_h)), int(rng.poisson(rate_a))
            home_index.append(h)
            away_index.append(a)
            goals_h.append(yh)
            goals_a.append(ya)
            sh, sa = yh - rate_h, ya - rate_a
            b = theta["B"]
            attack[h] = b * attack[h] + theta["A_att"] * sh
            defence[a] = b * defence[a] + theta["A_def"] * sh
            attack[a] = b * attack[a] + theta["A_att"] * sa
            defence[h] = b * defence[h] + theta["A_def"] * sa
    return np.array(home_index), np.array(away_index), np.array(goals_h, float), np.array(goals_a, float)


def _round_robin(n: int) -> list[list[tuple[int, int]]]:
    """Circle-method round robin for an even number of teams: n - 1 rounds of n / 2 (home, away) pairs."""
    teams = list(range(n))
    rounds = []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


def _league_table(
    *,
    n_teams: int = 10,
    seasons: tuple[int, ...] = (2019, 2020, 2021, 2022, 2023),
    league: str = "TEST",
    seed: int = 3,
    first_id: int = 1,
    spread: float = 0.3,
    replace_after: int | None = None,
) -> pd.DataFrame:
    """A double round-robin league in the protocol's column layout, weekly rounds, fixed team strengths.

    With ``replace_after`` set, team 0 is replaced by a brand-new (weak) team id in later seasons,
    a promoted team.
    """
    rng = np.random.default_rng(seed)
    strength = rng.normal(0.0, spread, size=n_teams + 1)
    strength[n_teams] = -1.5 * spread  # the promoted team
    first_half = _round_robin(n_teams)
    rounds = first_half + [[(b, a) for a, b in block] for block in first_half]
    rows = []
    fixture = first_id
    for season in seasons:
        ids = list(range(n_teams))
        if replace_after is not None and season > replace_after:
            ids[0] = n_teams
        start = pd.Timestamp(f"{season}-08-10T15:00:00Z")
        for number, block in enumerate(rounds, start=1):
            kickoff = start + pd.Timedelta(days=7 * (number - 1))
            for home, away in block:
                rate_h = math.exp(0.15 + 0.25 + strength[ids[home]] - strength[ids[away]])
                rate_a = math.exp(0.15 + strength[ids[away]] - strength[ids[home]])
                rows.append(
                    {
                        "fixture_id": fixture,
                        "league_code": league,
                        "season": season,
                        "round": f"Regular Season - {number}",
                        "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "status_short": "FT",
                        "home_team_id": ids[home] + 1000,
                        "home_team": f"T{ids[home]}",
                        "away_team_id": ids[away] + 1000,
                        "away_team": f"T{ids[away]}",
                        "home_goals": float(rng.poisson(rate_h)),
                        "away_goals": float(rng.poisson(rate_a)),
                    }
                )
                fixture += 1
    return pd.DataFrame(rows)


def _prepared_train(matches: pd.DataFrame, n: int) -> pd.DataFrame:
    frame = matches.iloc[:n].copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    return frame


CONFIG = WalkForwardConfig(test_seasons=(2022, 2023), inner_seasons=1, min_train_matches=50)


# ---------------------------------------------------------------------------
# GAS-Poisson: likelihood, gradient, estimation
# ---------------------------------------------------------------------------


def test_gas_gradient_matches_finite_differences() -> None:
    home, away, goals_h, goals_a = _simulate_gas(
        8, 60, {"c": 0.1, "h": 0.25, "A_att": 0.05, "A_def": 0.04, "B": 0.98}, seed=5
    )
    data = gas._GasData(home.tolist(), away.tolist(), goals_h.tolist(), goals_a.tolist(), 8, [2, 5])
    theta = np.array([0.12, 0.2, 0.04, 0.03, 0.02, -0.15, 0.1])
    value, gradient = gas._log_likelihood_and_gradient(theta, data, 4.0)
    assert value == pytest.approx(gas._run_filter(theta, data, 4.0)[0])
    numeric = np.zeros(7)
    for index in range(7):
        step = np.zeros(7)
        step[index] = 1e-6
        numeric[index] = (
            gas._run_filter(theta + step, data, 4.0)[0] - gas._run_filter(theta - step, data, 4.0)[0]
        ) / 2e-6
    np.testing.assert_allclose(gradient, numeric, rtol=1e-5, atol=1e-6)


def test_gas_mle_recovers_known_parameters() -> None:
    truth = {"c": 0.10, "h": 0.25, "A_att": 0.05, "A_def": 0.04, "B": 0.985}
    home, away, goals_h, goals_a = _simulate_gas(18, 600, truth, seed=1)
    weak = gas.GasMleSettings(step_prior_sd=1e3, forget_prior_sd=1e3, entrant_prior_sd=1e3)
    fit = gas.fit_gas_arrays(home, away, goals_h, goals_a, n_teams=18, settings=weak)
    assert fit.converged
    assert fit.home_advantage == pytest.approx(truth["h"], abs=0.05)
    assert fit.attack_step == pytest.approx(truth["A_att"], abs=0.015)
    assert fit.defence_step == pytest.approx(truth["A_def"], abs=0.015)
    assert fit.persistence == pytest.approx(truth["B"], abs=0.015)
    # Without re-centring the intercept is only identified together with the mean level of the
    # states (which random-walks a little), so compare the level that matters: the average rate.
    data = gas._GasData(home.tolist(), away.tolist(), goals_h.tolist(), goals_a.tolist(), 18, [])

    def mean_total_rate(theta) -> float:
        tape: list = []
        gas._run_filter(theta, data, 4.0, tape)
        return float(np.mean([row[4] + row[5] for row in tape]))

    truth_theta = [truth["c"], truth["h"], truth["A_att"], truth["A_def"], 1.0 - truth["B"], 0.0, 0.0]
    assert mean_total_rate(fit.theta()) == pytest.approx(mean_total_rate(truth_theta), rel=0.02)
    # The estimate is a maximum: the likelihood at the truth cannot beat it.
    assert fit.log_likelihood >= gas._run_filter(truth_theta, data, 4.0)[0] - 1e-6


def test_gas_mle_beats_the_fixed_default_step_sizes() -> None:
    truth = {"c": 0.10, "h": 0.25, "A_att": 0.06, "A_def": 0.06, "B": 0.97}
    home, away, goals_h, goals_a = _simulate_gas(16, 400, truth, seed=2)
    weak = gas.GasMleSettings(step_prior_sd=1e3, forget_prior_sd=1e3, entrant_prior_sd=1e3)
    fit = gas.fit_gas_arrays(home, away, goals_h, goals_a, n_teams=16, settings=weak)
    data = gas._GasData(home.tolist(), away.tolist(), goals_h.tolist(), goals_a.tolist(), 16, [])
    default_config = gas.GasPoissonConfig()  # the historical fixed settings: 0.04 / 0.04 / 0.98
    fixed = gas._run_filter(
        [fit.intercept, fit.home_advantage, default_config.attack_step, default_config.defence_step,
         1.0 - default_config.persistence, 0.0, 0.0],
        data,
        4.0,
    )[0]
    assert fit.log_likelihood >= fixed - 1e-9


def test_gas_fit_states_equal_the_reference_recursion() -> None:
    """The fast pass and ``GasPoissonModel.observe_match`` (without re-centring) give the same states."""
    home, away, goals_h, goals_a = _simulate_gas(
        6, 40, {"c": 0.1, "h": 0.25, "A_att": 0.05, "A_def": 0.04, "B": 0.98}, seed=8
    )
    theta = [0.1, 0.25, 0.05, 0.04, 0.02, 0.0, 0.0]
    data = gas._GasData(home.tolist(), away.tolist(), goals_h.tolist(), goals_a.tolist(), 6, [])
    _, attack, defence = gas._run_filter(theta, data, 4.0)
    model = gas.GasPoissonModel(
        intercept=0.1,
        home_advantage=0.25,
        attack={str(i): 0.0 for i in range(6)},
        defence={str(i): 0.0 for i in range(6)},
        config=gas.GasPoissonConfig(attack_step=0.05, defence_step=0.04, persistence=0.98, center_states=False),
    )
    for h, a, yh, ya in zip(home, away, goals_h, goals_a):
        model.observe_match(str(h), str(a), int(yh), int(ya))
    np.testing.assert_allclose([model.attack[str(i)] for i in range(6)], attack, atol=1e-12)
    np.testing.assert_allclose([model.defence[str(i)] for i in range(6)], defence, atol=1e-12)


def test_gas_historical_recentring_is_unchanged_by_default() -> None:
    config = gas.GasPoissonConfig()
    assert config.center_states is True
    model = gas.fit_gas_poisson(
        pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=30, freq="3D"),
                "home_team": ["A", "B", "C"] * 10,
                "away_team": ["B", "C", "A"] * 10,
                "home_goals": [i % 3 for i in range(30)],
                "away_goals": [i % 2 for i in range(30)],
            }
        )
    )
    assert abs(sum(model.attack.values())) < 1e-10


def test_gas_fit_is_a_function_of_the_training_matches_only() -> None:
    """States after n matches do not depend on any match after n (the filter looks only backwards)."""
    home, away, goals_h, goals_a = _simulate_gas(
        6, 50, {"c": 0.1, "h": 0.25, "A_att": 0.05, "A_def": 0.04, "B": 0.98}, seed=9
    )
    n = 90
    settings = gas.GasMleSettings()
    early = gas.fit_gas_arrays(home[:n], away[:n], goals_h[:n], goals_a[:n], n_teams=6, settings=settings)
    goals_h_changed = goals_h.copy()
    goals_a_changed = goals_a.copy()
    goals_h_changed[n:] = goals_a[n:]
    goals_a_changed[n:] = goals_h[n:]
    again = gas.fit_gas_arrays(
        home[:n], away[:n], goals_h_changed[:n], goals_a_changed[:n], n_teams=6, settings=settings
    )
    np.testing.assert_array_equal(early.attack, again.attack)
    np.testing.assert_array_equal(early.theta(), again.theta())


def test_fit_gas_poisson_mle_dataframe_api_returns_a_usable_model() -> None:
    home, away, goals_h, goals_a = _simulate_gas(
        8, 100, {"c": 0.1, "h": 0.25, "A_att": 0.05, "A_def": 0.04, "B": 0.98}, seed=4
    )
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2020-08-01", periods=len(home), freq="D"),
            "season": 2020,
            "home_team": [f"T{i}" for i in home],
            "away_team": [f"T{i}" for i in away],
            "home_goals": goals_h,
            "away_goals": goals_a,
        }
    )
    model, fit = gas.fit_gas_poisson_mle(frame)
    assert fit.converged
    rate_h, rate_a = model.expected_goals("T0", "T1")
    assert rate_h > 0 and rate_a > 0
    unseen = model.expected_goals("T0", "NEW")  # never-seen team: the estimated entrant prior
    assert all(np.isfinite(unseen))
    model.observe_match("T0", "T1", 2, 1)  # still an ordinary GasPoissonModel
    assert not model.config.center_states


# ---------------------------------------------------------------------------
# Score matrix and markets
# ---------------------------------------------------------------------------


def test_score_matrix_reproduces_closed_form_goal_markets() -> None:
    rate_h = np.array([1.6, 0.8, 2.9])
    rate_a = np.array([1.1, 1.4, 0.5])
    matrix = poisson_score_matrix(rate_h, rate_a)
    np.testing.assert_allclose(matrix.sum(axis=(1, 2)), 1.0)
    for line in (1.5, 2.5, 3.5):
        probabilities = goal_market_probabilities(matrix, over_under(line))
        expected_over = poisson.sf(np.floor(line), rate_h + rate_a)
        np.testing.assert_allclose(probabilities[:, 1], expected_over, atol=1e-8)
        np.testing.assert_allclose(probabilities.sum(axis=1), 1.0)
    btts = goal_market_probabilities(matrix, MARKET_BTTS)
    np.testing.assert_allclose(btts[:, 1], (1 - np.exp(-rate_h)) * (1 - np.exp(-rate_a)), atol=1e-8)
    result = goal_market_probabilities(matrix, MARKET_1X2)
    np.testing.assert_allclose(result.sum(axis=1), 1.0)
    assert (result[:, 0] > result[:, 2]).tolist() == (rate_h > rate_a).tolist()


def test_unknown_market_is_refused() -> None:
    with pytest.raises(ValueError):
        goal_market_probabilities(poisson_score_matrix(np.array([1.0]), np.array([1.0])), Market("corners", ("a", "b"), lambda f: np.zeros(len(f))))


# ---------------------------------------------------------------------------
# Elo-Davidson
# ---------------------------------------------------------------------------


def test_davidson_mle_recovers_home_advantage_and_draw_parameter() -> None:
    rng = np.random.default_rng(1)
    n = 20_000
    difference = rng.normal(0.0, 120.0, size=n)
    true_h, true_nu = 80.0, 0.9
    probabilities = davidson_probabilities(difference + true_h, true_nu)
    outcome = np.array([rng.choice(3, p=p) for p in probabilities])
    h_hat, nu_hat = davidson_mle(difference, outcome, 400.0)
    assert h_hat == pytest.approx(true_h, abs=6.0)
    assert nu_hat == pytest.approx(true_nu, abs=0.05)


def test_davidson_probabilities_are_valid_and_ordered() -> None:
    difference = np.array([-300.0, 0.0, 300.0])
    probabilities = davidson_probabilities(difference, 0.8)
    np.testing.assert_allclose(probabilities.sum(axis=1), 1.0)
    assert (probabilities > 0).all()
    assert probabilities[0, 0] < probabilities[1, 0] < probabilities[2, 0]
    assert probabilities[0, 2] > probabilities[1, 2] > probabilities[2, 2]
    # The draw is likeliest between equal teams.
    assert probabilities[1, 1] > probabilities[0, 1] and probabilities[1, 1] > probabilities[2, 1]


def test_fast_elo_pass_matches_the_reference_recursion() -> None:
    rng = np.random.default_rng(7)
    n_teams, n = 6, 120
    home = rng.integers(0, n_teams, n)
    away = (home + rng.integers(1, n_teams, n)) % n_teams
    outcome = rng.integers(0, 3, n)
    score = (1.0 - outcome * 0.5).tolist()
    differences, ratings, _ = _elo_pass(
        home.tolist(), away.tolist(), score, [2020] * n, [False] * n_teams,
        n_teams=n_teams, k_factor=20.0, rating_scale=400.0, initial_rating=1500.0,
        home_advantage_points=65.0, draw_parameter=0.75, entrant_shift=0.0,
    )
    reference = EloDavidsonModel(
        ratings={str(t): 1500.0 for t in range(n_teams)},
        config=EloDavidsonConfig(k_factor=20.0),
        home_advantage_points=65.0,
        draw_parameter=0.75,
    )
    reference_differences = []
    for h, a, y in zip(home, away, outcome):
        reference_differences.append(reference.ratings[str(h)] - reference.ratings[str(a)])
        goals = {0: (1, 0), 1: (1, 1), 2: (0, 1)}[int(y)]
        reference.observe_match(str(h), str(a), *goals)
    np.testing.assert_allclose(differences, reference_differences, atol=1e-9)
    np.testing.assert_allclose(ratings, [reference.ratings[str(t)] for t in range(n_teams)], atol=1e-9)


def test_elo_ratings_before_a_match_use_only_earlier_matches() -> None:
    rng = np.random.default_rng(5)
    n_teams, n = 8, 200
    home = rng.integers(0, n_teams, n)
    away = (home + rng.integers(1, n_teams, n)) % n_teams
    outcome = rng.integers(0, 3, n)
    kwargs = dict(
        n_teams=n_teams, k_factor=25.0, rating_scale=400.0, initial_rating=1500.0,
        home_advantage_points=60.0, draw_parameter=0.7, entrant_shift=0.0,
    )
    full, _, _ = _elo_pass(home.tolist(), away.tolist(), (1.0 - outcome * 0.5).tolist(), [2020] * n, [False] * n_teams, **kwargs)
    cut = 120
    changed = outcome.copy()
    changed[cut:] = 2 - changed[cut:]  # rewrite the future
    partial, _, _ = _elo_pass(home.tolist(), away.tolist(), (1.0 - changed * 0.5).tolist(), [2020] * n, [False] * n_teams, **kwargs)
    np.testing.assert_array_equal(full[: cut + 1], partial[: cut + 1])  # match `cut` itself is scored before its result is used
    assert not np.array_equal(full[cut + 1 :], partial[cut + 1 :])


def test_elo_entrant_enters_at_the_mean_of_the_active_teams_plus_the_shift() -> None:
    rng = np.random.default_rng(3)
    founders = 8
    home, away, outcome, season = [], [], [], []
    for _ in range(4):  # season 2020: founders only
        order = rng.permutation(founders)
        for pair in range(founders // 2):
            home.append(int(order[2 * pair]))
            away.append(int(order[2 * pair + 1]))
            outcome.append(int(rng.integers(0, 3)))
            season.append(2020)
    prefix = len(home)
    home += [8, 1]  # team 8 (a promoted team) first plays in season 2021
    away += [0, 2]
    outcome += [0, 2]
    season += [2021, 2021]
    kwargs = dict(
        n_teams=founders + 1, k_factor=20.0, rating_scale=400.0, initial_rating=1500.0,
        home_advantage_points=60.0, draw_parameter=0.7, entrant_shift=-70.0,
    )
    is_entrant = [False] * founders + [True]
    score = lambda values: (1.0 - np.asarray(values) * 0.5).tolist()
    _, ratings_before, _ = _elo_pass(home[:prefix], away[:prefix], score(outcome[:prefix]), season[:prefix], is_entrant, **kwargs)
    differences, _, _ = _elo_pass(home, away, score(outcome), season, is_entrant, **kwargs)
    active_mean = float(np.mean(ratings_before[:founders]))
    assert differences[prefix] == pytest.approx(active_mean - 70.0 - ratings_before[0])


def test_entrant_prior_pulls_the_shift_toward_its_mean() -> None:
    table = _league_table(replace_after=2020, seed=6)
    train = _prepared_train(table, len(table))
    tight = EloDavidsonForecaster(20.0, entrant_prior=(-60.0, 1.0)).fit(train)
    assert tight.parameters["entrant_shift"] == pytest.approx(-60.0, abs=15.0)


def test_elo_fit_converts_to_the_reference_model_with_the_same_probabilities() -> None:
    table = _league_table(seed=4)
    train = _prepared_train(table, 260)
    forecaster = EloDavidsonForecaster(20.0).fit(train)
    fit = forecaster._fitted.fit
    model = fit.to_model()
    assert model.home_advantage_points == fit.home_advantage_points
    assert model.draw_parameter == fit.draw_parameter
    index = forecaster._fitted.index
    home, away = int(index[0]), int(index[1])
    expected = fit.probabilities(np.array([0]), np.array([1]), np.array([2021]))[0]
    np.testing.assert_allclose(model.probabilities(str(home), str(away)), expected)
    # a team the model has never seen enters at the entrant prior (league mean plus the shift)
    assert model.new_team_rating == pytest.approx(fit.reference_rating(int(fit.last_season.max()) + 1))


def test_elo_unseen_team_gets_the_entrant_prior_and_valid_probabilities() -> None:
    table = _league_table(seed=4)
    train = _prepared_train(table, 260)
    model = EloDavidsonForecaster(20.0).fit(train)
    frame = pd.DataFrame(
        {"home_team_id": [1000, 9999, 1001], "away_team_id": [1001, 1000, 9998], "season": [2021, 2021, 2021]}
    )
    probabilities = model.predict(frame)
    assert probabilities.shape == (3, 3)
    np.testing.assert_allclose(probabilities.sum(axis=1), 1.0)
    assert (probabilities > 0).all()
    assert model.parameters["draw_parameter"] > 0


# ---------------------------------------------------------------------------
# Adapters inside the framework
# ---------------------------------------------------------------------------


def test_adapter_refuses_a_training_frame_with_two_leagues() -> None:
    table = pd.concat(
        [_league_table(league="A", seed=1), _league_table(league="B", seed=2, first_id=10_000)], ignore_index=True
    )
    train = _prepared_train(table, len(table))
    with pytest.raises(ValueError, match="several leagues"):
        GasPoissonForecaster(MARKET_1X2).fit(train)
    with pytest.raises(ValueError, match="several leagues"):
        EloDavidsonForecaster().fit(train)


@pytest.mark.parametrize("market", GOAL_MARKETS, ids=lambda m: m.name)
def test_gas_forecasts_are_valid_probabilities_for_every_goal_market(market: Market) -> None:
    table = _league_table()
    result = evaluate_model(gas_poisson_spec(market, cache=FitCache()), table, market, CONFIG)
    probabilities = result.probabilities()
    assert len(probabilities) == (table["season"] >= 2022).sum()
    assert np.isfinite(probabilities).all()
    np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, atol=1e-12)
    assert (probabilities > 0).all()


def test_elo_forecasts_are_valid_probabilities() -> None:
    table = _league_table()
    result = evaluate_model(elo_davidson_spec(cache=FitCache()), table, MARKET_1X2, CONFIG)
    probabilities = result.probabilities()
    assert np.isfinite(probabilities).all()
    np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, atol=1e-12)
    assert set(result.selections["params"]) <= {f'{{"k_factor": {k}}}' for k in (10.0, 20.0, 30.0, 40.0)}


def test_models_beat_league_frequencies_on_data_with_real_team_strength() -> None:
    from model_buk.evaluation.framework import league_frequency_spec
    from model_buk.evaluation.scoring import log_loss_per_match

    table = _league_table(n_teams=12, seasons=(2017, 2018, 2019, 2020, 2021, 2022, 2023), seed=21)
    baseline = evaluate_model(league_frequency_spec(MARKET_1X2), table, MARKET_1X2, CONFIG)
    gas_result = evaluate_model(gas_poisson_spec(MARKET_1X2, cache=FitCache()), table, MARKET_1X2, CONFIG)
    elo_result = evaluate_model(elo_davidson_spec(cache=FitCache()), table, MARKET_1X2, CONFIG)

    def loss(result) -> float:
        return float(np.mean(log_loss_per_match(result.probabilities(), result.outcome())))

    assert loss(gas_result) < loss(baseline)
    assert loss(elo_result) < loss(baseline)


def test_one_fit_serves_all_five_goal_markets() -> None:
    table = _league_table()
    cache = FitCache()
    evaluate_model(gas_poisson_spec(MARKET_1X2, cache=cache), table, MARKET_1X2, CONFIG)
    fits_after_first = len(cache)
    misses_after_first = cache.misses
    for market in GOAL_MARKETS[1:]:
        evaluate_model(gas_poisson_spec(market, cache=cache), table, market, CONFIG)
    assert len(cache) == fits_after_first  # no new fit for the four other markets
    assert cache.misses == misses_after_first
    assert cache.hits >= 4 * fits_after_first


def test_cache_key_contains_the_outcomes_of_the_training_set() -> None:
    table = _league_table()
    train = _prepared_train(table, 150)
    cache = FitCache()
    first = GasPoissonForecaster(MARKET_1X2, cache=cache).fit(train)
    swapped = train.copy()
    swapped[["home_goals", "away_goals"]] = swapped[["away_goals", "home_goals"]].to_numpy()
    second = GasPoissonForecaster(MARKET_1X2, cache=cache).fit(swapped)
    assert len(cache) == 2  # the same fixtures with other outcomes are another training set
    assert first.parameters["home_advantage"] != second.parameters["home_advantage"]
    third = GasPoissonForecaster(MARKET_1X2, cache=cache).fit(train)
    assert len(cache) == 2 and cache.hits == 1
    assert third.parameters == first.parameters


def test_gas_forecast_for_an_unseen_team_uses_the_entrant_prior() -> None:
    table = _league_table(replace_after=2020, seed=8)
    train = _prepared_train(table, len(table))
    model = GasPoissonForecaster(MARKET_1X2).fit(train)
    fit = model._fitted.fit
    frame = pd.DataFrame({"home_team_id": [999_999], "away_team_id": [1001]})
    rate_h, rate_a = model.expected_goals(frame)
    away_state = model._fitted.index.get_loc(1001)
    expected_h = math.exp(fit.intercept + fit.home_advantage + fit.entrant_attack + fit.defence[away_state])
    assert rate_h[0] == pytest.approx(min(max(expected_h, 0.05), 8.0))
    assert np.isfinite(model.predict(frame)).all()


def test_gas_leakage_test_passes_with_the_cache_disabled() -> None:
    table = _league_table(seed=13)
    spec = gas_poisson_spec(MARKET_1X2, cache=None)
    report = check_no_lookahead(
        lambda frame: evaluate_model(spec, frame, MARKET_1X2, CONFIG), table, MARKET_1X2, "2023-01-15"
    )
    assert report.passed, report
    assert report.n_compared > 0


def test_elo_leakage_test_passes_with_the_cache_disabled() -> None:
    table = _league_table(seed=14)
    spec = elo_davidson_spec(cache=None)
    report = check_no_lookahead(
        lambda frame: evaluate_model(spec, frame, MARKET_1X2, CONFIG), table, MARKET_1X2, "2023-01-15"
    )
    assert report.passed, report
    assert report.n_compared > 0


def test_a_model_that_peeks_at_future_outcomes_is_caught_by_the_same_test() -> None:
    """Mutation check: forcing the fit to see the whole table must fail the leakage test."""
    table = _league_table(seed=15)

    class Peeking(GasPoissonForecaster):
        def fit(self, train: pd.DataFrame):
            everything = _prepared_train(self.everything, len(self.everything))
            everything = everything[everything["league_code"] == train["league_code"].iloc[0]]
            return super().fit(everything)

    def run(frame: pd.DataFrame):
        Peeking.everything = frame
        from model_buk.evaluation.framework import ModelSpec

        spec = ModelSpec("peek", lambda: Peeking(MARKET_1X2))
        return evaluate_model(spec, frame, MARKET_1X2, CONFIG)

    report = check_no_lookahead(run, table, MARKET_1X2, "2023-01-15")
    assert not report.passed


# ---------------------------------------------------------------------------
# Review additions: pin down what a wrong-but-plausible implementation would break
# ---------------------------------------------------------------------------


def test_first_appearance_entrants_are_teams_that_first_play_after_the_first_season() -> None:
    home = np.array([0, 1, 2, 3, 0, 4, 2, 3, 5])
    away = np.array([1, 0, 3, 2, 4, 1, 0, 0, 1])
    season = np.array([1, 1, 1, 1, 2, 2, 2, 3, 3])
    # Teams 0-3 play in the first season; team 3 skips season 2 and returns (not new); 4 and 5 enter later.
    assert sorted(gas.first_appearance_entrants(home, away, season, 6)) == [4, 5]
    empty = np.array([], dtype=int)
    assert gas.first_appearance_entrants(empty, empty, empty, 0) == []


def test_gas_forecaster_state_and_rates_equal_a_replay_of_the_reference_model() -> None:
    """The fitted end state and the forecast rates are what the reference recursion gives at the cutoff.

    Founders start at zero, a promoted team at the estimated entrant prior, and every training match
    (also the last one) has updated the states before a forecast is made.
    """
    table = _league_table(n_teams=8, seasons=(2019, 2020, 2021), replace_after=2019, seed=31)
    train = _prepared_train(table, len(table))
    forecaster = GasPoissonForecaster(MARKET_1X2).fit(train)
    fit = forecaster._fitted.fit
    first_season = train["season"].min()
    opening = train[train["season"] == first_season]
    founders = set(opening["home_team_id"]) | set(opening["away_team_id"])
    promoted = (set(train["home_team_id"]) | set(train["away_team_id"])) - founders
    assert promoted, "the fixture must contain a promoted team"
    reference = gas.GasPoissonModel(
        intercept=fit.intercept,
        home_advantage=fit.home_advantage,
        attack={str(team): 0.0 for team in founders},
        defence={str(team): 0.0 for team in founders},
        config=fit.config(),
        new_team_attack=fit.entrant_attack,
        new_team_defence=fit.entrant_defence,
    )
    for row in train.itertuples(index=False):
        reference.observe_match(str(row.home_team_id), str(row.away_team_id), int(row.home_goals), int(row.away_goals))
    for position, team in enumerate(forecaster._fitted.index):
        assert fit.attack[position] == pytest.approx(reference.attack[str(team)], abs=1e-12)
        assert fit.defence[position] == pytest.approx(reference.defence[str(team)], abs=1e-12)
    teams = [int(team) for team in forecaster._fitted.index]
    pairs = [
        (teams[0], teams[1]),
        (teams[1], teams[0]),
        (teams[2], teams[5]),
        (teams[5], teams[2]),
        (777_777, teams[3]),
        (teams[3], 777_777),
    ]
    frame = pd.DataFrame({"home_team_id": [p[0] for p in pairs], "away_team_id": [p[1] for p in pairs]})
    home_rate, away_rate = forecaster.expected_goals(frame)
    for position, (home, away) in enumerate(pairs):
        expected = reference.expected_goals(str(home), str(away))
        assert home_rate[position] == pytest.approx(expected[0], rel=1e-12)
        assert away_rate[position] == pytest.approx(expected[1], rel=1e-12)


def test_gas_penalty_is_the_gaussian_prior_of_the_settings() -> None:
    settings = gas.GasMleSettings()
    theta = np.array([0.1, 0.2, 0.035, 0.01, 0.02, -0.15, 0.3])
    value, gradient = gas._penalty(theta, settings)
    explicit = (
        0.5 * ((0.035 - settings.step_prior_mean) / settings.step_prior_sd) ** 2
        + 0.5 * ((0.01 - settings.step_prior_mean) / settings.step_prior_sd) ** 2
        + 0.5 * ((0.02 - settings.forget_prior_mean) / settings.forget_prior_sd) ** 2
        + 0.5 * (-0.15 / settings.entrant_prior_sd) ** 2
        + 0.5 * (0.3 / settings.entrant_prior_sd) ** 2
    )
    assert value == pytest.approx(explicit)
    numeric = np.zeros(7)
    for index in range(7):
        step = np.zeros(7)
        step[index] = 1e-7
        numeric[index] = (gas._penalty(theta + step, settings)[0] - gas._penalty(theta - step, settings)[0]) / 2e-7
    np.testing.assert_allclose(gradient, numeric, rtol=1e-6, atol=1e-8)
    at_centres = np.array(
        [0.0, 0.0, settings.step_prior_mean, settings.step_prior_mean, settings.forget_prior_mean, 0.0, 0.0]
    )
    assert gas._penalty(at_centres, settings)[0] == pytest.approx(0.0)


def test_gas_fit_follows_the_prior_when_the_prior_is_tight() -> None:
    """The penalty is part of the optimised objective: a very tight prior pins the estimates to its centres."""
    home, away, goals_h, goals_a = _simulate_gas(
        10, 60, {"c": 0.1, "h": 0.25, "A_att": 0.02, "A_def": 0.02, "B": 0.99}, seed=3
    )
    weak = gas.fit_gas_arrays(
        home, away, goals_h, goals_a, n_teams=10, entrants=[3, 7],
        settings=gas.GasMleSettings(step_prior_sd=1e3, forget_prior_sd=1e3, entrant_prior_sd=1e3),
    )
    tight = gas.fit_gas_arrays(
        home, away, goals_h, goals_a, n_teams=10, entrants=[3, 7],
        settings=gas.GasMleSettings(
            step_prior_mean=0.10, step_prior_sd=1e-3, forget_prior_mean=0.05, forget_prior_sd=1e-3, entrant_prior_sd=1e-3
        ),
    )
    assert weak.entrant_attack > 0.1  # the data alone pull the entrant prior away from zero
    assert tight.attack_step == pytest.approx(0.10, abs=0.005)
    assert tight.defence_step == pytest.approx(0.10, abs=0.005)
    assert 1.0 - tight.persistence == pytest.approx(0.05, abs=0.005)
    assert tight.entrant_attack == pytest.approx(0.0, abs=0.005)
    assert tight.entrant_defence == pytest.approx(0.0, abs=0.005)


def test_gas_forecaster_fit_uses_every_training_match_including_the_last() -> None:
    table = _league_table(seed=17)
    train = _prepared_train(table, 200)
    full = GasPoissonForecaster(MARKET_1X2).fit(train)
    fewer = GasPoissonForecaster(MARKET_1X2).fit(train.iloc[:-1])
    last = train.iloc[-1]
    position = full._fitted.index.get_loc(int(last["home_team_id"]))
    assert full._fitted.fit.attack[position] != fewer._fitted.fit.attack[position]


def test_elo_entry_rating_is_the_mean_of_the_recently_active_teams_only() -> None:
    """A team that did not play in the entrant's season or the season before is not part of the mean."""
    rng = np.random.default_rng(11)
    founders = 12
    home, away, outcome, season = [], [], [], []

    def play(players: list[int], year: int, rounds: int) -> None:
        for _ in range(rounds):
            order = rng.permutation(players)
            for pair in range(len(players) // 2):
                home.append(int(order[2 * pair]))
                away.append(int(order[2 * pair + 1]))
                outcome.append(int(rng.integers(0, 3)))
                season.append(year)

    play(list(range(founders)), 2019, 4)
    play(list(range(8)), 2020, 4)  # teams 8-11 are absent in 2020
    prefix = len(home)
    home += [founders, 1]  # team 12 (promoted) first plays in 2021
    away += [0, 2]
    outcome += [0, 2]
    season += [2021, 2021]
    kwargs = dict(
        n_teams=founders + 1, k_factor=20.0, rating_scale=400.0, initial_rating=1500.0,
        home_advantage_points=60.0, draw_parameter=0.7, entrant_shift=-70.0,
    )
    is_entrant = [False] * founders + [True]
    score = lambda values: (1.0 - np.asarray(values) * 0.5).tolist()
    _, before, _ = _elo_pass(home[:prefix], away[:prefix], score(outcome[:prefix]), season[:prefix], is_entrant, **kwargs)
    differences, _, _ = _elo_pass(home, away, score(outcome), season, is_entrant, **kwargs)
    active_mean = float(np.mean(before[:8]))
    assert abs(active_mean - float(np.mean(before[:founders]))) > 1e-3  # the inactive teams would change the mean
    assert differences[prefix] == pytest.approx(active_mean - 70.0 - before[0], abs=1e-9)


def test_elo_forecast_rating_of_an_unseen_team_depends_on_the_forecast_season() -> None:
    table = _league_table(seed=4, replace_after=2020)  # team 0 leaves after 2020 and never returns
    train = _prepared_train(table, len(table))
    forecaster = EloDavidsonForecaster(20.0).fit(train)
    fit = forecaster._fitted.fit
    index = forecaster._fitted.index
    opponent = int(index[3])
    frame = pd.DataFrame(
        {"home_team_id": [999_999, 999_999], "away_team_id": [opponent, opponent], "season": [2024, 2021]}
    )
    probabilities = forecaster.predict(frame)
    for row, year in enumerate((2024, 2021)):
        active = fit.last_season >= year - 1
        assert active.sum() >= 6
        rating = float(fit.ratings[active].mean()) + fit.entrant_shift
        expected = davidson_probabilities(
            np.array([rating + fit.home_advantage_points - fit.ratings[3]]), fit.draw_parameter, fit.rating_scale
        )[0]
        np.testing.assert_allclose(probabilities[row], expected, atol=1e-12)
    # 2024: the departed team is excluded from the mean, 2021: it still counts, so the two forecasts differ.
    assert not np.allclose(probabilities[0], probabilities[1])


def test_adapters_flag_the_promoted_team_and_the_gas_fit_estimates_its_prior() -> None:
    table = _league_table(replace_after=2020, seed=6)  # team 0 is replaced by a weak new team after 2020
    train = _prepared_train(table, len(table))
    data = _training_arrays(train)
    promoted_id = 1000 + 10  # ids are team number + 1000; the promoted team is number n_teams
    assert [int(data.team_ids[index]) for index in data.entrants] == [promoted_id]
    fit = GasPoissonForecaster(MARKET_1X2).fit(train)._fitted.fit
    assert abs(fit.entrant_attack) > 1e-3 and abs(fit.entrant_defence) > 1e-3  # estimated from the data, not left at zero
    no_entrants = _training_arrays(train[train["season"] <= 2020])
    assert no_entrants.entrants == []


def test_elo_fit_home_advantage_and_draw_parameter_are_maximum_likelihood_on_its_own_ratings() -> None:
    """The nuisance parameters are a fixed point of the Davidson MLE on the fit's pre-match rating differences."""
    for seed, kwargs in ((4, {}), (6, {"replace_after": 2020}), (21, {"n_teams": 12})):
        table = _league_table(seed=seed, **kwargs)
        train = _prepared_train(table, len(table))
        data = _training_arrays(train)
        outcome = np.where(data.home_goals > data.away_goals, 0, np.where(data.home_goals == data.away_goals, 1, 2))
        fit = fit_elo_arrays(
            data.home_index, data.away_index, outcome, data.season, n_teams=len(data.team_ids),
            k_factor=20.0, entrants=data.entrants, entrant_prior=(-60.0, 40.0),
        )
        is_entrant = [False] * len(data.team_ids)
        for team in data.entrants:
            is_entrant[team] = True
        differences, _, _ = _elo_pass(
            data.home_index.tolist(), data.away_index.tolist(), (1.0 - outcome * 0.5).tolist(), data.season.tolist(),
            is_entrant, n_teams=len(data.team_ids), k_factor=20.0, rating_scale=400.0, initial_rating=1500.0,
            home_advantage_points=fit.home_advantage_points, draw_parameter=fit.draw_parameter,
            entrant_shift=fit.entrant_shift,
        )
        home_advantage, draw_parameter = davidson_mle(
            differences, outcome, 400.0, start=(fit.home_advantage_points, fit.draw_parameter)
        )
        assert fit.home_advantage_points == pytest.approx(home_advantage, abs=0.5)
        assert fit.draw_parameter == pytest.approx(draw_parameter, abs=0.01)
        # ... and it is not the closed-form starting value the alternation begins from
        crude = 400.0 * math.log10(((outcome == 0).sum() + 0.5) / ((outcome == 2).sum() + 0.5))
        assert abs(fit.home_advantage_points - crude) > 2.0


def test_elo_cache_key_contains_the_hyper_parameter_k() -> None:
    table = _league_table(seed=4)
    train = _prepared_train(table, 260)
    cache = FitCache()
    low = EloDavidsonForecaster(10.0, cache=cache).fit(train)
    high = EloDavidsonForecaster(40.0, cache=cache).fit(train)
    assert len(cache) == 2
    assert low.parameters["k_factor"] == 10.0 and high.parameters["k_factor"] == 40.0
    again = EloDavidsonForecaster(10.0, cache=cache).fit(train)
    assert len(cache) == 2 and cache.hits == 1
    assert again.parameters == low.parameters
