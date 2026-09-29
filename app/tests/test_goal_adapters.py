"""Dixon-Coles and NB2 as framework models: probabilities, prior, cache, warm start, leakage."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import approx_fprime
from scipy.special import gammaln

import model_buk.evaluation.framework as framework
from model_buk.evaluation.framework import (
    MARKET_1X2,
    LookaheadLeakError,
    WalkForwardConfig,
    assert_no_lookahead,
    check_no_lookahead,
    evaluate_model,
    league_frequency_spec,
    over_under,
)
from model_buk.evaluation.protocol import GOAL_MARKETS, MARKET_BTTS
from model_buk.models import goal_adapters as ga
from model_buk.models.dixon_coles import (
    fit_dixon_coles,
    fit_dixon_coles_arrays,
    market_probabilities,
    score_matrix as dc_score_matrix,
)
from model_buk.models.negative_binomial import (
    fit_negative_binomial,
    fit_negative_binomial_arrays,
    score_matrix as nb2_score_matrix,
)

GRID = [180.0, 730.0]


# ---------------------------------------------------------------------------
# Synthetic league with known strengths and promotions
# ---------------------------------------------------------------------------


def make_league(
    *,
    n_seasons: int = 5,
    n_teams: int = 10,
    promoted: int = 2,
    newcomer_shift: float = 0.5,
    seed: int = 5,
    league: str = "TST",
    first_season: int = 2018,
    alpha: float | None = None,
) -> pd.DataFrame:
    """Double round robins; every season ``promoted`` teams are replaced by weaker new ids.

    Goals are Poisson (or NB2 with dispersion ``alpha``) with log-rate
    ``0.25 + home_advantage + attack + defence``.  A newcomer's attack is
    ``-newcomer_shift`` and its defence ``+newcomer_shift`` below/above normal
    for its first season only.
    """
    rng = np.random.default_rng(seed)
    next_id = 100
    teams = {}
    for _ in range(n_teams):
        teams[next_id] = (rng.normal(0, 0.25), rng.normal(0, 0.25), False)
        next_id += 1
    rows = []
    fixture = 1
    for season_index in range(n_seasons):
        season = first_season + season_index
        if season_index > 0:
            for old in rng.choice(list(teams), size=promoted, replace=False):
                del teams[int(old)]
            for _ in range(promoted):
                teams[next_id] = (rng.normal(0, 0.25) - newcomer_shift, rng.normal(0, 0.25) + newcomer_shift, True)
                next_id += 1
        ids = sorted(teams)
        start = pd.Timestamp(f"{season}-08-01", tz="UTC")
        saturday = start + pd.Timedelta(days=(5 - start.weekday()) % 7)
        round_number = 0
        for leg in range(2):
            for r in range(n_teams - 1):
                round_number += 1
                order = list(rng.permutation(ids))
                for slot in range(n_teams // 2):
                    home, away = int(order[2 * slot]), int(order[2 * slot + 1])
                    if leg == 1:
                        home, away = away, home
                    rate_home = math.exp(0.25 + 0.3 + teams[home][0] + teams[away][1])
                    rate_away = math.exp(0.25 + teams[away][0] + teams[home][1])
                    if alpha is None:
                        goals = (int(rng.poisson(rate_home)), int(rng.poisson(rate_away)))
                    else:
                        size = 1.0 / alpha
                        goals = tuple(int(rng.negative_binomial(size, size / (size + m))) for m in (rate_home, rate_away))
                    kickoff = saturday + pd.Timedelta(days=7 * (round_number - 1) + (0 if slot < n_teams // 4 else 1), hours=14)
                    rows.append(
                        {
                            "fixture_id": fixture,
                            "league_code": league,
                            "season": season,
                            "round": f"Regular Season - {round_number}",
                            "kickoff_utc": kickoff.isoformat(),
                            "status_short": "FT",
                            "home_team_id": home,
                            "away_team_id": away,
                            "home_team": f"T{home}",
                            "away_team": f"T{away}",
                            "home_goals": goals[0],
                            "away_goals": goals[1],
                        }
                    )
                    fixture += 1
    return pd.DataFrame(rows)


def with_kickoff(frame: pd.DataFrame) -> pd.DataFrame:
    """The parsed ``kickoff`` column the framework adds next to ``kickoff_utc``."""
    out = frame.copy()
    out["kickoff"] = pd.to_datetime(out["kickoff_utc"], utc=True).dt.as_unit("ns")
    return out


def to_arrays(frame: pd.DataFrame, half_life: float = 365.0):
    ids, inverse = np.unique(np.concatenate([frame["home_team_id"], frame["away_team_id"]]), return_inverse=True)
    n = len(frame)
    kickoff = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns").dt.tz_localize(None).to_numpy().view("int64")
    weights = np.exp(-math.log(2) / half_life * (kickoff.max() - kickoff) / 86_400e9)
    return (
        inverse[:n],
        inverse[n:],
        frame["home_goals"].to_numpy(float),
        frame["away_goals"].to_numpy(float),
        weights,
        len(ids),
        ids,
    )


@pytest.fixture(scope="module")
def league() -> pd.DataFrame:
    return with_kickoff(make_league())


@pytest.fixture(scope="module")
def overdispersed() -> pd.DataFrame:
    return with_kickoff(make_league(n_seasons=6, n_teams=12, alpha=0.35, seed=8))


# ---------------------------------------------------------------------------
# Probabilities: coherent, and identical to the score matrix
# ---------------------------------------------------------------------------


def train_test_split(frame: pd.DataFrame, season: int = 2021):
    train = frame[frame["season"] < season]
    test = frame[frame["season"] == season].head(20)
    hidden = test.drop(columns=["home_goals", "away_goals", "status_short"])
    return train, hidden


@pytest.mark.parametrize("market", GOAL_MARKETS, ids=lambda market: market.name)
@pytest.mark.parametrize("model_class", [ga.DixonColesGoalModel, ga.NegativeBinomialGoalModel])
def test_market_probabilities_sum_to_one_and_follow_the_market_classes(league, market, model_class):
    train, hidden = train_test_split(league)
    model = model_class(market, 365.0, cache=None).fit(train)
    probabilities = model.predict(hidden)
    assert probabilities.shape == (len(hidden), len(market.classes))
    assert model.classes == market.classes
    assert probabilities.min() >= 0.0
    assert probabilities.sum(axis=1) == pytest.approx(1.0, abs=1e-12)


def test_dixon_coles_market_probabilities_equal_the_scalar_score_matrix(league):
    train, hidden = train_test_split(league)
    max_goals = 12
    models = {m.name: ga.DixonColesGoalModel(m, 365.0, max_goals=max_goals, cache=None).fit(train) for m in GOAL_MARKETS}
    fit = models["1x2"].fitted
    home_rate, away_rate = models["1x2"].expected_goals(hidden)
    for i in range(len(hidden)):
        matrix = dc_score_matrix(float(home_rate[i]), float(away_rate[i]), rho=fit.shape, max_goals=max_goals)
        reference = market_probabilities(matrix, total_line=2.5)
        assert models["1x2"].predict(hidden)[i] == pytest.approx(
            [reference["result.home"], reference["result.draw"], reference["result.away"]], abs=1e-12
        )
        assert models["over_under_2.5"].predict(hidden)[i] == pytest.approx(
            [reference["goals.total.under.2.5"], reference["goals.total.over.2.5"]], abs=1e-12
        )
        assert models["btts"].predict(hidden)[i] == pytest.approx([reference["btts.no"], reference["btts.yes"]], abs=1e-12)
        for line in (1.5, 3.5):
            line_reference = market_probabilities(matrix, total_line=line)
            assert models[f"over_under_{line}"].predict(hidden)[i] == pytest.approx(
                [line_reference[f"goals.total.under.{line}"], line_reference[f"goals.total.over.{line}"]], abs=1e-12
            )


def test_nb2_market_probabilities_equal_the_scalar_score_matrix(overdispersed):
    train, hidden = train_test_split(overdispersed, season=2022)
    max_goals = 12
    model = ga.NegativeBinomialGoalModel(MARKET_1X2, 365.0, max_goals=max_goals, cache=None).fit(train)
    assert model.fitted.shape > 0.05  # overdispersed data: a genuine NB2, not the Poisson limit
    home_rate, away_rate = model.expected_goals(hidden)
    predicted = model.predict(hidden)
    totals = ga.NegativeBinomialGoalModel(over_under(2.5), 365.0, max_goals=max_goals, cache=None).fit(train).predict(hidden)
    btts = ga.NegativeBinomialGoalModel(MARKET_BTTS, 365.0, max_goals=max_goals, cache=None).fit(train).predict(hidden)
    for i in range(len(hidden)):
        matrix = nb2_score_matrix(float(home_rate[i]), float(away_rate[i]), model.fitted.shape, max_goals=max_goals)
        reference = market_probabilities(matrix, total_line=2.5)
        assert predicted[i] == pytest.approx([reference["result.home"], reference["result.draw"], reference["result.away"]], abs=1e-12)
        assert totals[i] == pytest.approx([reference["goals.total.under.2.5"], reference["goals.total.over.2.5"]], abs=1e-12)
        assert btts[i] == pytest.approx([reference["btts.no"], reference["btts.yes"]], abs=1e-12)


def test_vectorised_score_matrices_equal_the_scalar_ones_and_sum_to_one():
    home = np.array([0.4, 1.3, 2.9])
    away = np.array([1.1, 0.8, 2.2])
    for rho in (-0.12, 0.0, 0.05):
        stacked = ga.dixon_coles_score_matrices(home, away, rho, 10)
        assert stacked.sum(axis=(1, 2)) == pytest.approx(1.0)
        for i in range(3):
            assert stacked[i] == pytest.approx(np.array(dc_score_matrix(home[i], away[i], rho=rho, max_goals=10)), abs=1e-13)
    for alpha in (1e-4, 0.2, 1.5):
        stacked = ga.nb2_score_matrices(home, away, alpha, 10)
        for i in range(3):
            assert stacked[i] == pytest.approx(np.array(nb2_score_matrix(home[i], away[i], alpha, max_goals=10)), abs=1e-9)
    # In the Poisson limit (alpha on its lower bound) NB2 is the independent Poisson matrix; the tables stay
    # accurate there although r = 1 / alpha is ~1e8 (a plain lgamma difference would lose ~7 digits).
    limit = ga.nb2_score_matrices(home, away, 1e-8, 10)
    assert limit == pytest.approx(ga.dixon_coles_score_matrices(home, away, 0.0, 10), abs=1e-7)


def test_unknown_market_and_wrong_class_order_are_refused():
    from model_buk.evaluation.framework import Market

    with pytest.raises(ValueError, match="cannot forecast"):
        ga.DixonColesGoalModel(Market("corners", ("under", "over"), lambda frame: np.zeros(len(frame))), 365.0)
    with pytest.raises(ValueError, match="classes"):
        ga.DixonColesGoalModel(Market("btts", ("yes", "no"), lambda frame: np.zeros(len(frame))), 365.0)


# ---------------------------------------------------------------------------
# The fast fit is the same estimator
# ---------------------------------------------------------------------------


def reference_dixon_coles_nll(theta, hi, ai, x, y, w, n, l2):
    """The original (mask based) Dixon-Coles objective, weights not normalised, log x! included."""
    m, h, rho = theta[:3]
    a = np.append(theta[3 : 2 + n], -theta[3 : 2 + n].sum())
    d = np.append(theta[2 + n :], -theta[2 + n :].sum())
    rate_h = np.exp(np.clip(m + h + a[hi] + d[ai], -8, 8))
    rate_a = np.exp(np.clip(m + a[ai] + d[hi], -8, 8))
    tau = np.ones(len(x))
    k = (x == 0) & (y == 0)
    tau[k] = 1 - rate_h[k] * rate_a[k] * rho
    k = (x == 0) & (y == 1)
    tau[k] = 1 + rate_h[k] * rho
    k = (x == 1) & (y == 0)
    tau[k] = 1 + rate_a[k] * rho
    k = (x == 1) & (y == 1)
    tau[k] = 1 - rho
    ll = x * np.log(rate_h) - rate_h - gammaln(x + 1) + y * np.log(rate_a) - rate_a - gammaln(y + 1) + np.log(tau)
    return -float(w @ ll) + l2 * float(a @ a + d @ d)


def reference_nb2_nll(theta, hi, ai, x, y, w, n, l2):
    m, h, log_alpha = theta[:3]
    a = np.append(theta[3 : 2 + n], -theta[3 : 2 + n].sum())
    d = np.append(theta[2 + n :], -theta[2 + n :].sum())
    alpha = math.exp(log_alpha)
    size = 1 / alpha
    mean_h = np.exp(np.clip(m + h + a[hi] + d[ai], -12, 12))
    mean_a = np.exp(np.clip(m + a[ai] + d[hi], -12, 12))

    def ll(goals, mean):
        growth = np.log1p(alpha * mean)
        return gammaln(goals + size) - gammaln(size) - gammaln(goals + 1) - size * growth + goals * (math.log(alpha) + np.log(mean) - growth)

    return -float(w @ (ll(x, mean_h) + ll(y, mean_a))) + l2 * float(a @ a + d @ d)


@pytest.mark.parametrize("l2", [0.0, 2.0])
def test_dixon_coles_fit_sits_at_the_optimum_of_the_reference_likelihood(league, l2):
    hi, ai, x, y, w, n, _ = to_arrays(league, 300.0)
    fit = fit_dixon_coles_arrays(hi, ai, x, y, w, n, l2_strength=l2)
    assert fit.converged
    theta = np.concatenate([[fit.intercept, fit.home_advantage, fit.rho], fit.attack[:-1], fit.defence[:-1]])
    assert abs(fit.attack.sum()) < 1e-10 and abs(fit.defence.sum()) < 1e-10
    args = (hi, ai, x, y, w, n, l2)
    gradient = approx_fprime(theta, reference_dixon_coles_nll, 1e-7, *args)
    assert np.abs(gradient).max() < 5e-3 * max(1.0, w.sum() / 100)
    # ...and it is a minimum: random directions raise the reference objective.
    rng = np.random.default_rng(0)
    base = reference_dixon_coles_nll(theta, *args)
    for _ in range(20):
        step = rng.normal(0, 0.02, size=theta.shape)
        step[2] = 0.0
        assert reference_dixon_coles_nll(theta + step, *args) > base - 1e-7


@pytest.mark.parametrize("l2", [0.0, 2.0])
def test_nb2_fit_sits_at_the_optimum_of_the_reference_likelihood(overdispersed, l2):
    hi, ai, x, y, w, n, _ = to_arrays(overdispersed, 500.0)
    fit = fit_negative_binomial_arrays(hi, ai, x, y, w, n, l2_strength=l2)
    assert fit.converged
    assert 0.1 < fit.alpha < 1.0
    theta = np.concatenate([[fit.intercept, fit.home_advantage, math.log(fit.alpha)], fit.attack[:-1], fit.defence[:-1]])
    args = (hi, ai, x, y, w, n, l2)
    gradient = approx_fprime(theta, reference_nb2_nll, 1e-7, *args)
    assert np.abs(gradient).max() < 5e-3 * max(1.0, w.sum() / 100)


def test_fit_negative_binomial_supports_time_decay_without_changing_the_default(league):
    frame = league.assign(date=pd.to_datetime(league["kickoff_utc"], utc=True))
    static = fit_negative_binomial(frame, l2_strength=1.0)
    same = fit_negative_binomial(frame, l2_strength=1.0, half_life_days=None)
    assert same.attack == pytest.approx(static.attack)
    decayed = fit_negative_binomial(frame, l2_strength=1.0, half_life_days=120.0)
    assert decayed.alpha > 0.0
    assert sum(decayed.attack.values()) == pytest.approx(0.0, abs=1e-9)
    assert any(abs(decayed.attack[t] - static.attack[t]) > 1e-3 for t in static.teams)
    # the public fit is the array fit with the documented weights
    hi, ai, x, y, w, n, ids = to_arrays(league, 120.0)
    arrays = fit_negative_binomial_arrays(hi, ai, x, y, w, n, l2_strength=1.0)
    assert decayed.alpha == pytest.approx(arrays.alpha, rel=0.05, abs=1e-4)
    assert decayed.attack["T100"] == pytest.approx(arrays.attack[list(ids).index(100)], abs=2e-3)


def test_public_dixon_coles_fit_agrees_with_the_array_fit(league):
    frame = league.assign(date=pd.to_datetime(league["kickoff_utc"], utc=True))
    public = fit_dixon_coles(frame, half_life_days=300.0, l2_strength=2.0)
    hi, ai, x, y, w, n, ids = to_arrays(league, 300.0)
    arrays = fit_dixon_coles_arrays(hi, ai, x, y, w, n, l2_strength=2.0)
    by_name = {f"T{i}": k for k, i in enumerate(ids)}
    assert public.rho == pytest.approx(arrays.rho, abs=2e-4)
    for team, position in by_name.items():
        assert public.attack[team] == pytest.approx(arrays.attack[position], abs=2e-3)
        assert public.defence[team] == pytest.approx(arrays.defence[position], abs=2e-3)


@pytest.mark.parametrize("kind", ["dixon_coles", "nb2"])
def test_warm_start_reaches_the_same_optimum_as_a_cold_start(overdispersed, kind):
    hi, ai, x, y, w, n, _ = to_arrays(overdispersed, 400.0)
    fitter = fit_dixon_coles_arrays if kind == "dixon_coles" else fit_negative_binomial_arrays
    cold = fitter(hi, ai, x, y, w, n, l2_strength=2.0)
    rng = np.random.default_rng(1)
    shape = cold.rho if kind == "dixon_coles" else cold.alpha
    perturbed = (
        cold.intercept + 0.1,
        cold.home_advantage - 0.1,
        shape * (1.0 if kind == "dixon_coles" else 2.0) + (0.05 if kind == "dixon_coles" else 0.0),
        cold.attack + rng.normal(0, 0.2, n),
        cold.defence + rng.normal(0, 0.2, n),
    )
    warm = fitter(hi, ai, x, y, w, n, l2_strength=2.0, start=perturbed)
    exact_start = fitter(hi, ai, x, y, w, n, l2_strength=2.0, start=cold.parameters())
    assert warm.converged and exact_start.converged
    for other in (warm, exact_start):
        assert other.intercept == pytest.approx(cold.intercept, abs=1e-4)
        assert other.home_advantage == pytest.approx(cold.home_advantage, abs=1e-4)
        assert other.attack == pytest.approx(cold.attack, abs=1e-4)
        assert other.defence == pytest.approx(cold.defence, abs=1e-4)
    assert exact_start.n_iterations <= 2  # starting at the optimum costs (almost) nothing


@pytest.mark.parametrize("kind", ["dixon_coles", "nb2"])
def test_a_chain_of_warm_started_cached_fits_matches_independent_cold_fits(league, kind):
    cache = ga.FitCache()
    frame = with_kickoff(league)
    for cut in (120, 150, 180, 210, 240):
        train = frame.iloc[:cut]
        chained = ga.fit_goal_model(kind, train, half_life_days=365.0, cache=cache)
        cold = ga.fit_goal_model(kind, train, half_life_days=365.0, cache=None)
        assert chained.attack == pytest.approx(cold.attack, abs=1e-4)
        assert chained.defence == pytest.approx(cold.defence, abs=1e-4)
        assert chained.intercept == pytest.approx(cold.intercept, abs=1e-4)
        assert chained.newcomer_attack == pytest.approx(cold.newcomer_attack, abs=1e-3)


# ---------------------------------------------------------------------------
# Teams the fit has not seen
# ---------------------------------------------------------------------------


def test_promoted_team_prior_is_learned_from_earlier_newcomers_and_used(league):
    train, hidden = train_test_split(league, season=2022)
    model = ga.DixonColesGoalModel(MARKET_1X2, 730.0, cache=None).fit(train)
    fit = model.fitted
    assert fit.n_newcomer_teams >= 4
    # The synthetic newcomers really are weaker (attack down, defence up), and so is the prior.
    assert fit.newcomer_attack < ga.NEWCOMER_PRIOR_ATTACK < 0.0
    assert fit.newcomer_defence > ga.NEWCOMER_PRIOR_DEFENCE > 0.0
    known_home = int(train["home_team_id"].iloc[-1])
    known_away = int(train["away_team_id"].iloc[-1])
    frame = hidden.head(2).copy()
    frame["home_team_id"] = [9001, known_home]
    frame["away_team_id"] = [known_away, 9002]
    home_rate, away_rate = model.expected_goals(frame)
    a_away, d_away, _ = fit.strengths(np.array([known_away]))
    a_home, d_home, _ = fit.strengths(np.array([known_home]))
    assert home_rate[0] == pytest.approx(math.exp(fit.intercept + fit.home_advantage + fit.newcomer_attack + d_away[0]))
    assert away_rate[0] == pytest.approx(math.exp(fit.intercept + a_away[0] + fit.newcomer_defence))
    assert home_rate[1] == pytest.approx(math.exp(fit.intercept + fit.home_advantage + a_home[0] + fit.newcomer_defence))
    assert model.uses_newcomer_prior(frame).tolist() == [True, True]
    assert not model.uses_newcomer_prior(hidden.head(1).assign(home_team_id=known_home, away_team_id=known_away)).any()
    probabilities = model.predict(frame)
    assert np.isfinite(probabilities).all() and probabilities.sum(axis=1) == pytest.approx(1.0)
    # A newcomer is expected to score less than a league-average team would in the same fixture.
    assert home_rate[0] < math.exp(fit.intercept + fit.home_advantage + 0.0 + d_away[0])


def test_league_without_earlier_newcomers_falls_back_on_the_default_prior():
    one_season = with_kickoff(make_league(n_seasons=1))
    model = ga.DixonColesGoalModel(MARKET_1X2, 365.0, cache=None).fit(one_season)
    assert model.fitted.n_newcomer_teams == 0
    assert model.fitted.newcomer_attack == ga.NEWCOMER_PRIOR_ATTACK
    assert model.fitted.newcomer_defence == ga.NEWCOMER_PRIOR_DEFENCE


def test_newcomer_prior_needs_complete_first_seasons_and_enough_matches():
    intercept, home_adv = 0.2, 0.3
    attack = np.zeros(4)
    defence = np.zeros(4)
    # team 3 appears only in the last season: its first season is incomplete, so it does not count
    home = np.array([0, 1, 2, 3, 0, 1])
    away = np.array([1, 2, 0, 0, 3, 2])
    season = np.array([1, 1, 1, 2, 2, 2])
    result = ga.newcomer_prior(intercept, home_adv, attack, defence, home, away, np.ones(6), np.ones(6), season)
    assert result[2] == 0
    assert (result[0], result[1]) == (ga.NEWCOMER_PRIOR_ATTACK, ga.NEWCOMER_PRIOR_DEFENCE)


def reference_newcomer_prior(intercept, home_adv, attack, defence, home, away, home_goals, away_goals, season, *,
                             default_attack, default_defence, weight, min_matches):
    """The documented newcomer prior written as plain per-match loops (independent of the vectorised code)."""
    first_of_league, last_of_league = season.min(), season.max()
    attacks, defences = [], []
    for team in range(len(attack)):
        rows = [i for i in range(len(season)) if team in (home[i], away[i])]
        first = min(season[i] for i in rows)
        if not (first_of_league < first < last_of_league):
            continue
        played = scored = conceded = expected_scored = expected_conceded = 0.0
        for i in (i for i in rows if season[i] == first):
            played += 1
            if home[i] == team:
                scored += home_goals[i]
                conceded += away_goals[i]
                expected_scored += math.exp(intercept + home_adv + defence[away[i]])
                expected_conceded += math.exp(intercept + attack[away[i]])
            else:
                scored += away_goals[i]
                conceded += home_goals[i]
                expected_scored += math.exp(intercept + defence[home[i]])
                expected_conceded += math.exp(intercept + home_adv + attack[home[i]])
        if played < min_matches:
            continue
        attacks.append(math.log((scored + 0.5) / (expected_scored + 0.5)))
        defences.append(math.log((conceded + 0.5) / (expected_conceded + 0.5)))
    if not attacks:
        return default_attack, default_defence, 0
    used = len(attacks)
    return (
        (sum(attacks) + weight * default_attack) / (used + weight),
        (sum(defences) + weight * default_defence) / (used + weight),
        used,
    )


def test_newcomer_prior_equals_the_documented_per_match_definition():
    rng = np.random.default_rng(11)
    squads = {1: range(0, 6), 2: range(2, 8), 3: range(4, 10), 4: range(6, 12)}  # 2 leave, 2 newcomers each season
    home, away, season = [], [], []
    for year, teams in squads.items():
        for a in teams:
            for b in teams:
                if a != b:
                    home.append(a)
                    away.append(b)
                    season.append(year)
    home, away, season = np.array(home), np.array(away), np.array(season)
    keep = np.ones(len(season), dtype=bool)
    thin = np.flatnonzero((season == 2) & ((home == 7) | (away == 7)))
    keep[thin[3:]] = False  # newcomer 7 plays only 3 matches in its first season: below the minimum
    home, away, season = home[keep], away[keep], season[keep]
    home_goals = rng.poisson(1.4, len(season)).astype(float)
    away_goals = rng.poisson(1.1, len(season)).astype(float)
    attack, defence = rng.normal(0, 0.2, 12), rng.normal(0, 0.2, 12)
    options = dict(default_attack=-0.2, default_defence=0.15, weight=3.0)
    used = {}
    for min_matches in (8, 1):
        expected = reference_newcomer_prior(0.1, 0.25, attack, defence, home, away, home_goals, away_goals, season,
                                            min_matches=min_matches, **options)
        got = ga.newcomer_prior(0.1, 0.25, attack, defence, home, away, home_goals, away_goals, season,
                                default_attack=-0.2, default_defence=0.15, prior_weight=3.0, min_matches=min_matches)
        assert got[2] == expected[2]
        assert got[:2] == pytest.approx(expected[:2], abs=1e-12)
        used[min_matches] = got[2]
    # Newcomers 6, 8, 9 always count, 7 only without the minimum; the first season's teams (0-5) and the last
    # season's newcomers (10, 11: their first season is not over) never count.
    assert used == {8: 3, 1: 4}


def test_too_little_training_data_gives_no_forecast_not_a_made_up_one(league):
    tiny = with_kickoff(league).iloc[:1]
    model = ga.DixonColesGoalModel(MARKET_1X2, 365.0, cache=None).fit(tiny)
    assert not model.fitted.converged
    forecast = model.predict(with_kickoff(league).iloc[5:8].drop(columns=["home_goals", "away_goals"]))
    assert forecast.shape == (3, 3) and np.isnan(forecast).all()


def test_mixed_leagues_and_missing_goals_are_refused(league):
    other = league.assign(league_code="OTH").iloc[:20]
    with pytest.raises(ValueError, match="one league at a time"):
        ga.fit_goal_model("dixon_coles", pd.concat([league.iloc[:20], other]), half_life_days=365.0, cache=None)
    missing = league.iloc[:40].copy()
    missing["home_goals"] = missing["home_goals"].astype(float)
    missing.iloc[3, missing.columns.get_loc("home_goals")] = np.nan
    with pytest.raises(ValueError, match="never zero"):
        ga.fit_goal_model("nb2", missing, half_life_days=365.0, cache=None)


# ---------------------------------------------------------------------------
# The fit cache never serves a fit for another training set
# ---------------------------------------------------------------------------


def test_cache_serves_only_the_identical_training_set(league):
    cache = ga.FitCache()
    frame = with_kickoff(league)
    train = frame.iloc[:200]
    first = ga.fit_goal_model("dixon_coles", train, half_life_days=365.0, cache=cache)
    assert cache.stats() == {"fits": 1, "hits": 0, "misses": 1, "failures": 0}
    again = ga.fit_goal_model("dixon_coles", train.copy(), half_life_days=365.0, cache=cache)
    assert again is first and cache.hits == 1

    def misses_for(mutated: pd.DataFrame, **overrides) -> bool:
        options = {"half_life_days": 365.0, "cache": cache, **overrides}
        before = cache.misses
        result = ga.fit_goal_model("dixon_coles", mutated, **options)
        return cache.misses == before + 1 and result is not first

    # one goal changed (same fixtures, same teams, same kickoffs)
    changed_goal = train.copy()
    changed_goal.iloc[-1, changed_goal.columns.get_loc("home_goals")] += 1
    assert misses_for(changed_goal)
    # one team swapped, one fixture id changed, one kickoff moved, one season relabelled, one match fewer
    swapped = train.copy()
    swapped.iloc[7, swapped.columns.get_loc("home_team_id")] = int(train["home_team_id"].iloc[8])
    assert misses_for(swapped)
    other_fixture = train.copy()
    other_fixture.iloc[3, other_fixture.columns.get_loc("fixture_id")] = 10**6
    assert misses_for(other_fixture)
    moved = train.copy()
    moved.iloc[-1, moved.columns.get_loc("kickoff")] += pd.Timedelta(hours=1)
    assert misses_for(moved)
    relabelled = train.copy()
    relabelled.iloc[-1, relabelled.columns.get_loc("season")] += 1
    assert misses_for(relabelled)
    assert misses_for(train.iloc[:-1])
    # another half-life, another ridge, another league label, another model: all different fits
    assert misses_for(train, half_life_days=730.0)
    assert misses_for(train, l2_strength=5.0)
    assert misses_for(train.assign(league_code="OTH"))
    before = cache.misses
    ga.fit_goal_model("nb2", train, half_life_days=365.0, cache=cache)
    assert cache.misses == before + 1
    # ... and the untouched original is still served from the cache
    assert ga.fit_goal_model("dixon_coles", train, half_life_days=365.0, cache=cache) is first


def test_cached_evaluation_equals_uncached_evaluation_and_serves_every_market(league):
    matches = league.drop(columns=["kickoff"], errors="ignore")
    config = WalkForwardConfig(test_seasons=[2021, 2022], inner_seasons=1, min_train_matches=50)
    cache = ga.FitCache()
    uncached = evaluate_model(ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=None), matches, MARKET_1X2, config)
    cached = evaluate_model(ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=cache), matches, MARKET_1X2, config)
    assert cached.probabilities() == pytest.approx(uncached.probabilities(), abs=1e-6)
    fits_after_first_market = len(cache)
    evaluate_model(ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=cache), matches, MARKET_1X2, config)
    assert len(cache) == fits_after_first_market  # the same market again needs no fit at all
    hits_before = cache.hits
    evaluate_model(ga.dixon_coles_spec(over_under(2.5), half_lives=GRID, cache=cache), matches, over_under(2.5), config)
    # Another market shares the whole inner grid; it may only add fits for an outer half-life it chose differently.
    assert len(cache) - fits_after_first_market < 0.5 * fits_after_first_market
    assert cache.hits - hits_before > 0.5 * fits_after_first_market
    assert cache.failures == 0


def test_cache_round_trip_and_version_guard(tmp_path, league):
    cache = ga.FitCache()
    train = with_kickoff(league).iloc[:150]
    fit = ga.fit_goal_model("nb2", train, half_life_days=365.0, cache=cache)
    path = cache.save(tmp_path / "fits.pkl")
    restored = ga.FitCache()
    assert restored.load(path) == 1
    served = ga.fit_goal_model("nb2", train, half_life_days=365.0, cache=restored)
    assert restored.hits == 1 and served.attack == pytest.approx(fit.attack)
    assert ga.load_fit_cache(tmp_path, ga.FitCache()) == 1
    # entries written by another version of the fitting code are ignored
    import pickle

    stale = tmp_path / "stale.pkl"
    stale.write_bytes(pickle.dumps({"version": "old", "fits": {("old", "x"): fit}}))
    assert ga.FitCache().load(stale) == 0
    cache.clear()
    assert len(cache) == 0 and cache.stats()["hits"] == 0


# ---------------------------------------------------------------------------
# Framework integration, time decay and leakage
# ---------------------------------------------------------------------------


def test_models_run_in_the_framework_and_beat_league_frequencies(league):
    matches = league.drop(columns=["kickoff"], errors="ignore")
    config = WalkForwardConfig(test_seasons=[2021, 2022], inner_seasons=1, min_train_matches=50)
    baseline = evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, config)
    baseline_loss = baseline.score_table(n_resamples=50).iloc[-1]["log_loss"]
    for spec in (ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=ga.FitCache()), ga.nb2_spec(MARKET_1X2, half_lives=GRID, cache=ga.FitCache())):
        result = evaluate_model(spec, matches, MARKET_1X2, config)
        assert result.model == spec.name
        assert result.probabilities().sum(axis=1) == pytest.approx(1.0)
        assert set(result.selections["params"]) <= {'{"half_life_days": 180.0}', '{"half_life_days": 730.0}'}
        assert result.score_table(n_resamples=50).iloc[-1]["log_loss"] < baseline_loss


def test_model_names_are_the_protocol_names():
    assert ga.dixon_coles_spec(MARKET_1X2).name == "dixon_coles"
    assert ga.nb2_spec(MARKET_1X2).name == "nb2"
    assert list(ga.dixon_coles_spec(MARKET_1X2).param_grid["half_life_days"]) == [180.0, 365.0, 730.0, 1200.0]
    assert ga.dixon_coles_spec(MARKET_1X2, half_lives=[365]).param_grid is None


def test_time_decay_makes_the_fit_follow_a_team_that_changed():
    """A team whose strength jumps halfway through: a short half-life sees the jump, a long one averages."""
    rng = np.random.default_rng(2)
    rows = []
    fixture = 1
    for day in range(0, 720, 3):
        for home, away in ((1, 2), (2, 3), (3, 1), (2, 1), (3, 2), (1, 3)):
            boost = 0.9 if (home == 1 and day >= 360) else 0.0
            rate_home = math.exp(0.2 + 0.3 + boost)
            rate_away = math.exp(0.2 + (0.9 if (away == 1 and day >= 360) else 0.0))
            rows.append(
                {
                    "fixture_id": fixture,
                    "league_code": "TST",
                    "season": 2020 + day // 360,
                    "kickoff": pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=day, hours=fixture % 5),
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_team": f"T{home}",
                    "away_team": f"T{away}",
                    "home_goals": int(rng.poisson(rate_home)),
                    "away_goals": int(rng.poisson(rate_away)),
                }
            )
            fixture += 1
    train = pd.DataFrame(rows)
    short = ga.fit_goal_model("dixon_coles", train, half_life_days=60.0, cache=None)
    long = ga.fit_goal_model("dixon_coles", train, half_life_days=5000.0, cache=None)
    assert short.attack[0] > long.attack[0] + 0.15  # team 1 (index 0) is much stronger after the jump


def test_honest_goal_models_pass_the_leakage_test_with_the_cache_cleared_per_run():
    matches = make_league(n_seasons=4, n_teams=8, seed=9).drop(columns=["kickoff"], errors="ignore")
    config = WalkForwardConfig(test_seasons=[2020, 2021], inner_seasons=1, min_train_matches=40)
    block = matches[(matches["season"] == 2020) & (matches["round"] == "Regular Season - 8")]
    cutoff = pd.to_datetime(block["kickoff_utc"], utc=True).min().normalize() + pd.Timedelta(days=1)
    for market in (MARKET_1X2, over_under(2.5)):
        for build in (ga.dixon_coles_spec, ga.nb2_spec):
            spec = build(market, half_lives=GRID)

            def run(data, spec=spec, market=market):
                ga.FIT_CACHE.clear()
                return evaluate_model(spec, data, market, config)

            report = assert_no_lookahead(run, matches, market, cutoff)
            assert report.passed and report.n_compared > 30
            assert all(m.max_abs_difference == 0.0 for m in report.modes)
    ga.FIT_CACHE.clear()


def test_leakage_test_catches_a_broken_cutoff_with_the_goal_models(monkeypatch):
    """Mutation check: the same test on the same models must fail once the training cut-off admits the future."""
    matches = make_league(n_seasons=4, n_teams=8, seed=9).drop(columns=["kickoff"], errors="ignore")
    config = WalkForwardConfig(test_seasons=[2020, 2021], inner_seasons=1, min_train_matches=40)
    block = matches[(matches["season"] == 2020) & (matches["round"] == "Regular Season - 8")]
    cutoff = pd.to_datetime(block["kickoff_utc"], utc=True).min().normalize() + pd.Timedelta(days=1)
    spec = ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID)

    def run(data):
        ga.FIT_CACHE.clear()
        return evaluate_model(spec, data, MARKET_1X2, config)

    assert check_no_lookahead(run, matches, MARKET_1X2, cutoff).passed
    monkeypatch.setattr(framework, "training_cutoff_ns", lambda start, latency: start + 20 * 24 * 3600 * 10**9)
    broken = check_no_lookahead(run, matches, MARKET_1X2, cutoff)
    assert not broken.passed and broken.n_forecasts_changed > 0
    with pytest.raises(LookaheadLeakError):
        assert_no_lookahead(run, matches, MARKET_1X2, cutoff)
    monkeypatch.undo()
    ga.FIT_CACHE.clear()


def test_a_stale_cache_would_hide_a_leak_which_is_why_runs_clear_it(league):
    """Documents the reason for clearing: identical training sets are served from the cache by design."""
    cache = ga.FitCache()
    frame = with_kickoff(league)
    train = frame.iloc[:180]
    ga.fit_goal_model("dixon_coles", train, half_life_days=365.0, cache=cache)
    # even a fit that never saw the shuffled future is a hit when the training set is unchanged
    shuffled_future = frame.copy()
    shuffled_future.loc[shuffled_future.index >= 180, "home_goals"] = shuffled_future.loc[shuffled_future.index >= 180, "home_goals"].to_numpy()[::-1]
    ga.fit_goal_model("dixon_coles", shuffled_future.iloc[:180], half_life_days=365.0, cache=cache)
    assert cache.hits == 1 and cache.misses == 1


@pytest.mark.parametrize(
    "kind, model_class", [("dixon_coles", ga.DixonColesGoalModel), ("nb2", ga.NegativeBinomialGoalModel)]
)
def test_fitted_parameters_convert_to_the_plain_model_dataclasses(league, kind, model_class):
    train, hidden = train_test_split(league, season=2020)
    seen = set(train["home_team_id"]) | set(train["away_team_id"])
    hidden = hidden[hidden["home_team_id"].isin(seen) & hidden["away_team_id"].isin(seen)]
    assert len(hidden) >= 3
    adapter = model_class(MARKET_1X2, 365.0, cache=None).fit(train)
    plain = adapter.fitted.to_model()
    home, away = adapter.expected_goals(hidden)
    for i in range(3):
        expected = plain.expected_goals(str(hidden["home_team_id"].iloc[i]), str(hidden["away_team_id"].iloc[i]), unknown_strategy="neutral")
        assert expected == pytest.approx((home[i], away[i]), rel=1e-9)


def test_warm_fit_cache_writes_resumable_files_that_serve_the_evaluation(tmp_path):
    first = make_league(seed=1, league="AAA")
    second = make_league(seed=2, league="BBB").assign(fixture_id=lambda f: f["fixture_id"] + 10_000)
    matches = pd.concat([first, second], ignore_index=True)
    config = WalkForwardConfig(test_seasons=[2021, 2022], inner_seasons=1, min_train_matches=50)
    outcome = ga.warm_fit_cache(
        matches, ["dixon_coles"], tmp_path, config=config, seasons=[2020, 2021], half_lives=GRID, n_jobs=2, log=None
    )
    assert len(list(tmp_path.glob("*.pkl"))) == 2 and all(row["fits"] > 50 for row in outcome)
    again = ga.warm_fit_cache(
        matches, ["dixon_coles"], tmp_path, config=config, seasons=[2020, 2021], half_lives=GRID, n_jobs=2, log=None
    )
    assert all(row.get("skipped") for row in again)  # resumable: finished jobs are not repeated

    cache = ga.FitCache()
    assert ga.load_fit_cache(tmp_path, cache) == sum(row["fits"] for row in outcome)
    spec = ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=cache)
    served = evaluate_model(spec, matches, MARKET_1X2, config)
    # Both inner folds (2020, 2021) and the outer 2021 were warmed; only the outer 2022 fits are new.
    outer_2022_matchdays = matches[matches["season"] == 2022].groupby("league_code")["round"].nunique().sum()
    assert cache.misses == outer_2022_matchdays
    assert cache.hits > 3 * cache.misses
    fresh = evaluate_model(ga.dixon_coles_spec(MARKET_1X2, half_lives=GRID, cache=None), matches, MARKET_1X2, config)
    assert served.probabilities() == pytest.approx(fresh.probabilities(), abs=1e-6)
