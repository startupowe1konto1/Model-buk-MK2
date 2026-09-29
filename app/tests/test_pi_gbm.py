"""Offline tests: pi-ratings, the as-of feature store and the pooled boosting model."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from model_buk.evaluation.framework import (
    MARKET_1X2,
    WalkForwardConfig,
    check_no_lookahead,
    evaluate_model,
    league_frequency_spec,
    over_under,
)
from model_buk.evaluation.protocol import MARKET_BTTS
from model_buk.models.gbm_model import (
    LOGIT_FEATURES,
    GbmSettings,
    PiGbmEngine,
    PiGbmModel,
    PooledLogit,
    grid_combinations,
    pi_gbm_spec,
    pi_logit_spec,
)
from model_buk.models.match_features import AsOfStore, feature_names
from model_buk.models.pi_ratings import (
    PiRatingConfig,
    PiRatings,
    expected_goal_difference,
    pi_rating_pass,
    rating_to_goal_difference,
    update_ratings,
    weighted_error,
)

DAY = 86_400 * 10**9
HOUR = 3_600 * 10**9


# ---------------------------------------------------------------------------
# Pi-ratings
# ---------------------------------------------------------------------------


def test_pi_update_matches_the_hand_computed_example():
    cfg = PiRatingConfig()  # lambda 0.035, gamma 0.7, b 10, c 3
    # Match 1, two fresh teams, home wins 3-0.  Expected goal difference 0, error 3,
    # psi = 3 * log10(4) = 1.806180.  Home: R_H = 0.035 * 1.806180 = 0.0632163,
    # R_A = 0.7 * 0.0632163 = 0.0442514.  Away mirrors with the opposite sign.
    assert weighted_error(3.0, cfg) == pytest.approx(1.806180, abs=1e-6)
    hh, ha, ah, aa = update_ratings(0.0, 0.0, 0.0, 0.0, 3.0, cfg)
    assert hh == pytest.approx(0.0632163, abs=1e-7)
    assert ha == pytest.approx(0.0442514, abs=1e-7)
    assert aa == pytest.approx(-0.0632163, abs=1e-7)
    assert ah == pytest.approx(-0.0442514, abs=1e-7)

    # Match 2: a fresh team X hosts the team of match 1 (away rating 0.0442514), 1-1.
    # Expected goal difference of X = -(10 ** (0.0442514 / 3) - 1) = -0.0345476, error 0.0345476,
    # psi = 0.0442514; X did better than expected.  X: R_H = 0.0015488, R_A = 0.0010842;
    # the visitor: R_A = 0.0442514 * (1 - 0.035) = 0.0427026,
    # R_H = 0.0632163 - 0.7 * 0.0015488 = 0.0621321.
    assert expected_goal_difference(0.0, ha, cfg) == pytest.approx(-0.0345476, abs=1e-7)
    x_home, x_away, v_home, v_away = update_ratings(0.0, 0.0, hh, ha, 0.0, cfg)
    assert x_home == pytest.approx(0.0015488, abs=1e-7)
    assert x_away == pytest.approx(0.0010842, abs=1e-7)
    assert v_away == pytest.approx(0.0427026, abs=1e-7)
    assert v_home == pytest.approx(0.0621321, abs=1e-7)


def test_pi_goal_scale_is_odd_and_zero_at_zero():
    cfg = PiRatingConfig()
    assert rating_to_goal_difference(0.0, cfg) == 0.0
    assert rating_to_goal_difference(1.3, cfg) == pytest.approx(-rating_to_goal_difference(-1.3, cfg))
    assert rating_to_goal_difference(3.0, cfg) == pytest.approx(9.0)  # 10 ** (3 / 3) - 1


def test_pi_pass_equals_incremental_reference_and_only_uses_the_past():
    rng = np.random.default_rng(1)
    n_keys, n = 6, 120
    home = rng.integers(0, n_keys, n)
    away = (home + rng.integers(1, n_keys, n)) % n_keys
    diff = rng.integers(-3, 4, n).astype(float)
    passed = pi_rating_pass(home, away, diff, n_keys)
    reference = PiRatings()
    for i in range(n):
        reference.observe(int(home[i]), int(away[i]), max(diff[i], 0), max(-diff[i], 0))
        assert passed.home_home[i] == pytest.approx(reference.home[int(home[i])])
        assert passed.away_away[i] == pytest.approx(reference.away[int(away[i])])
        assert passed.home_matches[i] == reference.matches[int(home[i])]
    # The state after row i does not change when later rows change.
    changed = diff.copy()
    changed[60:] = -changed[60:]
    again = pi_rating_pass(home, away, changed, n_keys)
    assert np.array_equal(passed.home_home[:60], again.home_home[:60])
    assert not np.array_equal(passed.home_home[60:], again.home_home[60:])


def test_pi_pass_keeps_a_missing_score_missing():
    passed = pi_rating_pass(np.array([0, 0]), np.array([1, 1]), np.array([np.nan, 2.0]), 2)
    assert passed.home_home[0] == 0.0 and passed.home_matches[0] == 0  # a missing score is not a 0-0
    assert passed.home_matches[1] == 1


# ---------------------------------------------------------------------------
# Synthetic league data
# ---------------------------------------------------------------------------


def _round_robin(teams: list[int]) -> list[list[tuple[int, int]]]:
    n = len(teams)
    ring = list(teams)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(ring[i], ring[n - 1 - i]) for i in range(n // 2)])
        ring = [ring[0]] + [ring[-1]] + ring[1:-1]
    return rounds + [[(b, a) for a, b in pairs] for pairs in rounds]


def synthetic(seed: int = 0, seasons=(2020, 2021, 2022), spread: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two leagues of eight teams (team 999 moves from league AAA to BBB in 2022) plus team statistics.

    League AAA has shots, shots on target and corners for ~90% of its matches and xG only in 2022;
    league BBB has statistics only in 2022 and no xG.
    """
    rng = np.random.default_rng(seed)
    strength = {team: rng.normal(0, spread) for team in list(range(100, 108)) + list(range(200, 208)) + [999]}
    fixture = 1
    rows = []
    stats = []
    for season in seasons:
        for league, base in (("AAA", 100), ("BBB", 200)):
            teams = list(range(base, base + 8))
            if league == "AAA" and season < 2022:
                teams[7] = 999
            if league == "BBB" and season == 2022:
                teams[7] = 999
            start = pd.Timestamp(f"{season}-08-01T15:00:00", tz="UTC")
            for r, pairs in enumerate(_round_robin(teams)):
                for j, (h, a) in enumerate(pairs):
                    kickoff = start + pd.Timedelta(days=7 * r) + pd.Timedelta(hours=[0, -2, 2.5, 0][j])
                    lam_h = math.exp(0.25 + strength[h] - strength[a])
                    lam_a = math.exp(0.0 + strength[a] - strength[h])
                    hg, ag = int(rng.poisson(lam_h)), int(rng.poisson(lam_a))
                    rows.append(
                        {
                            "fixture_id": fixture,
                            "league_code": league,
                            "season": season,
                            "round": f"Regular Season - {r + 1}",
                            "kickoff_utc": kickoff.isoformat(),
                            "status_short": "FT",
                            "home_team_id": h,
                            "away_team_id": a,
                            "home_goals": hg,
                            "away_goals": ag,
                        }
                    )
                    has_stats = (league == "AAA" and rng.random() < 0.9) or (league == "BBB" and season == 2022)
                    if has_stats:
                        for team, goals in ((h, hg), (a, ag)):
                            shots = float(rng.poisson(9 + 2 * goals))
                            stats.append(
                                {
                                    "fixture_id": fixture,
                                    "team_id": team,
                                    "total_shots": shots,
                                    "shots_on_goal": float(rng.poisson(3 + goals)),
                                    "corners": float(rng.poisson(5)),
                                    "expected_goals": float(rng.gamma(2.0, 0.7)) if (league == "AAA" and season == 2022) else np.nan,
                                }
                            )
                    fixture += 1
    return pd.DataFrame(rows), pd.DataFrame(stats)


SMALL_STORE = {"min_league_matches": 20, "min_previous_season_matches": 20}


@pytest.fixture(scope="module")
def data() -> tuple[pd.DataFrame, pd.DataFrame]:
    return synthetic()


@pytest.fixture(scope="module")
def store(data) -> AsOfStore:
    matches, stats = data
    return AsOfStore(matches, stats, **SMALL_STORE)


# ---------------------------------------------------------------------------
# Brute-force reference of the documented feature definitions
# ---------------------------------------------------------------------------


def _t_ns(matches: pd.DataFrame) -> np.ndarray:
    stamp = pd.to_datetime(matches["kickoff_utc"], utc=True).dt.as_unit("ns")
    return stamp.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64")


def reference_features(matches, stats, *, league, season, home, away, kickoff_ns, cutoff_ns, windows=(5, 10)):
    """Feature vector by explicit loops over the matches that kicked off before the cut-off."""
    frame = matches.assign(t=_t_ns(matches))
    known = frame[frame["t"] < cutoff_ns].sort_values(["t", "fixture_id"])
    by_key = {(int(r.fixture_id), int(r.team_id)): r for r in stats.itertuples()}
    stat_column = {"shots": "total_shots", "sot": "shots_on_goal", "corners": "corners", "xg": "expected_goals"}

    def team_rows(team):
        out = []
        for r in known.itertuples():
            if r.home_team_id == team:
                out.append((r, r.home_goals, r.away_goals, r.away_team_id))
            elif r.away_team_id == team:
                out.append((r, r.away_goals, r.home_goals, r.home_team_id))
        return out

    def stat(fixture, team, stem):
        row = by_key.get((int(fixture), int(team)))
        return np.nan if row is None else float(getattr(row, stat_column[stem]))

    def mean_or_nan(values, need):
        valid = [v for v in values if not np.isnan(v)]
        return float(np.mean(valid)) if len(valid) >= need else np.nan

    feats: dict[str, float] = {}
    league_known = known[known["league_code"] == league]
    league_window = league_known[league_known["t"] >= cutoff_ns - 730 * DAY]
    if len(league_window) >= SMALL_STORE["min_league_matches"]:
        hg, ag = league_window["home_goals"].to_numpy(float), league_window["away_goals"].to_numpy(float)
        values = [hg.mean(), ag.mean(), (hg > ag).mean(), (hg == ag).mean(), (hg + ag > 2.5).mean(), ((hg > 0) & (ag > 0)).mean()]
    else:
        values = [np.nan] * 6
    for stem, value in zip(("home_goals", "away_goals", "home_win", "draw", "over25", "btts"), values):
        feats[f"lg_{stem}"] = value

    pi = PiRatings()
    for r in league_known.itertuples():
        pi.observe(r.home_team_id, r.away_team_id, r.home_goals, r.away_goals)
    hh, ha = pi.rating(home)
    ah, aa = pi.rating(away)
    feats.update(pi_h_home=hh, pi_h_away=ha, pi_a_home=ah, pi_a_away=aa, pi_exp_gd=pi.expected_goal_difference(home, away))
    feats["pi_h_matches"] = float(pi.matches.get(home, 0))
    feats["pi_a_matches"] = float(pi.matches.get(away, 0))

    for side, team in (("h", home), ("a", away)):
        rows = team_rows(team)
        for k in windows:
            last = rows[-k:]
            need = -(-k // 2)
            gf = [float(x[1]) for x in last]
            ga = [float(x[2]) for x in last]
            feats[f"{side}_goals_for_{k}"] = mean_or_nan(gf, need)
            feats[f"{side}_goals_against_{k}"] = mean_or_nan(ga, need)
            feats[f"{side}_points_{k}"] = mean_or_nan([3.0 if f > g else 1.0 if f == g else 0.0 for f, g in zip(gf, ga)], need)
            for stem in ("shots", "sot", "corners", "xg"):
                feats[f"{side}_{stem}_for_{k}"] = mean_or_nan([stat(x[0].fixture_id, team, stem) for x in last], need)
                feats[f"{side}_{stem}_against_{k}"] = mean_or_nan([stat(x[0].fixture_id, x[3], stem) for x in last], need)
        feats[f"{side}_rest_days"] = min((kickoff_ns - rows[-1][0].t) / DAY, 60.0) if rows else np.nan
        feats[f"{side}_matches_14d"] = float(sum(1 for x in rows if x[0].t >= kickoff_ns - 14 * DAY))

    previous = league_known[league_known["season"] == season - 1]
    comparable = len(previous) >= SMALL_STORE["min_previous_season_matches"]
    for side, team in (("h", home), ("a", away)):
        played = ((previous["home_team_id"] == team) | (previous["away_team_id"] == team)).any()
        feats[f"{side}_promoted"] = (0.0 if played else 1.0) if comparable else np.nan
    for k in windows:
        feats[f"d_goal_diff_{k}"] = (feats[f"h_goals_for_{k}"] - feats[f"h_goals_against_{k}"]) - (
            feats[f"a_goals_for_{k}"] - feats[f"a_goals_against_{k}"]
        )
    k = max(windows)
    feats[f"d_xg_diff_{k}"] = (feats[f"h_xg_for_{k}"] - feats[f"h_xg_against_{k}"]) - (
        feats[f"a_xg_for_{k}"] - feats[f"a_xg_against_{k}"]
    )
    return np.array([feats[name] for name in feature_names(windows)], dtype=float)


def assert_store_matches_reference(store, matches, stats, cases):
    for league, season, home, away, kickoff_ns, cutoff_ns in cases:
        got = store.features(
            league_code=np.array([league], dtype=object),
            season=np.array([season]),
            home_team_id=np.array([home]),
            away_team_id=np.array([away]),
            kickoff_ns=np.array([kickoff_ns]),
            cutoff_ns=cutoff_ns,
        )[0]
        expected = reference_features(
            matches, stats, league=league, season=season, home=home, away=away, kickoff_ns=kickoff_ns, cutoff_ns=cutoff_ns
        )
        np.testing.assert_allclose(got, expected, rtol=1e-9, atol=1e-9, equal_nan=True, err_msg=f"{league} {home}-{away} at {cutoff_ns}")


def _cases(matches: pd.DataFrame):
    """Target matches from several times, leagues and seasons with their matchday cut-off."""
    t = _t_ns(matches)
    rng = np.random.default_rng(3)
    picks = list(rng.choice(len(matches), size=14, replace=False)) + [0, len(matches) - 1]
    cases = []
    for i in picks:
        r = matches.iloc[i]
        cases.append((r.league_code, int(r.season), int(r.home_team_id), int(r.away_team_id), int(t[i]), int(t[i]) - 3 * HOUR))
    return cases


def test_store_features_equal_the_brute_force_definition(data, store):
    matches, stats = data
    assert_store_matches_reference(store, matches, stats, _cases(matches))


def test_store_uses_the_whole_pooled_history_of_a_moving_team(data, store):
    matches, stats = data
    t = _t_ns(matches)
    late = matches[(matches["season"] == 2022) & (matches["league_code"] == "BBB") & ((matches["home_team_id"] == 999) | (matches["away_team_id"] == 999))]
    i = late.index[3]
    row = matches.loc[i]
    case = ("BBB", 2022, int(row.home_team_id), int(row.away_team_id), int(t[i]), int(t[i]) - 3 * HOUR)
    assert_store_matches_reference(store, matches, stats, [case])
    # Team 999 played in league AAA before: its form counts, its pi-rating in BBB starts fresh.
    x = store.features(
        league_code=np.array(["BBB"], dtype=object), season=np.array([2022]),
        home_team_id=np.array([999]), away_team_id=np.array([201]), kickoff_ns=np.array([int(t[i])]),
        cutoff_ns=int(pd.Timestamp("2022-08-01", tz="UTC").tz_localize(None).value),
    )[0]
    names = feature_names()
    assert x[names.index("h_goals_for_10")] > 0  # form from league AAA
    assert x[names.index("pi_h_matches")] == 0  # no BBB match yet
    assert x[names.index("h_promoted")] == 1.0  # not in BBB last season
    assert x[names.index("a_promoted")] == 0.0


# ---------------------------------------------------------------------------
# As-of behaviour: nothing at or after the cut-off, mutation-checked
# ---------------------------------------------------------------------------


def test_history_never_contains_a_match_at_or_after_the_cutoff(data, store):
    matches, _ = data
    t = pd.Series(_t_ns(matches), index=matches["fixture_id"])
    for team in (100, 103, 999, 207):
        for cutoff in (int(t.quantile(q)) for q in (0.1, 0.4, 0.8, 1.0)):
            fixtures = store.team_history_fixtures(team, cutoff)
            assert len(fixtures) > 0
            assert (t.loc[fixtures] < cutoff).all()
    # The boundary is strict: a match kicking off exactly at the cut-off is excluded, one nanosecond later it is in.
    fixture = int(matches.iloc[40]["fixture_id"])
    kickoff = int(t.loc[fixture])
    team = int(matches.iloc[40]["home_team_id"])
    assert fixture not in set(store.team_history_fixtures(team, kickoff))
    assert fixture in set(store.team_history_fixtures(team, kickoff + 1))


def test_mutated_cutoff_breaks_the_brute_force_comparison(data, monkeypatch):
    matches, stats = data
    cases = _cases(matches)
    healthy = AsOfStore(matches, stats, **SMALL_STORE)
    assert_store_matches_reference(healthy, matches, stats, cases)  # passes unmutated

    mutated = AsOfStore(matches, stats, **SMALL_STORE)
    original = AsOfStore._cutoff_rank
    monkeypatch.setattr(AsOfStore, "_cutoff_rank", lambda self, cutoff: original(self, np.asarray(cutoff, dtype=np.int64) + DAY))
    with pytest.raises(AssertionError):
        assert_store_matches_reference(mutated, matches, stats, cases)  # cutoff + 1 day must be detected


def test_features_do_not_change_when_only_the_future_changes(data):
    matches, stats = data
    t = _t_ns(matches)
    cutoff = int(np.quantile(t, 0.6))
    cases = [c for c in _cases(matches) if c[5] <= cutoff]
    assert cases
    future = t >= cutoff
    matches2 = matches.copy()
    matches2.loc[future, ["home_goals", "away_goals"]] = matches.loc[future, ["away_goals", "home_goals"]].to_numpy() + 2
    fixture_future = set(matches.loc[future, "fixture_id"])
    stats2 = stats.copy()
    changed = stats2["fixture_id"].isin(fixture_future)
    for column in ("total_shots", "shots_on_goal", "corners", "expected_goals"):
        stats2.loc[changed, column] = 99.0
    a = AsOfStore(matches, stats, **SMALL_STORE)
    b = AsOfStore(matches2, stats2, **SMALL_STORE)
    for league, season, home, away, kickoff, cut in cases:
        args = dict(
            league_code=np.array([league], dtype=object), season=np.array([season]), home_team_id=np.array([home]),
            away_team_id=np.array([away]), kickoff_ns=np.array([kickoff]), cutoff_ns=cut,
        )
        np.testing.assert_array_equal(a.features(**args), b.features(**args))


def test_missing_stays_missing(data, store):
    matches, stats = data
    names = feature_names()
    t = _t_ns(matches)
    # League BBB has no statistics before 2022: shots, corners and xG of a 2021 BBB match are NaN, never 0.
    i = matches[(matches["league_code"] == "BBB") & (matches["season"] == 2021)].index[-1]
    row = matches.loc[i]
    x = store.features(
        league_code=np.array(["BBB"], dtype=object), season=np.array([2021]), home_team_id=np.array([int(row.home_team_id)]),
        away_team_id=np.array([int(row.away_team_id)]), kickoff_ns=np.array([int(t[i])]), cutoff_ns=int(t[i]) - 3 * HOUR,
    )[0]
    for stem in ("shots", "sot", "corners", "xg"):
        assert np.isnan(x[names.index(f"h_{stem}_for_10")]) and np.isnan(x[names.index(f"h_{stem}_against_5")])
    assert not np.isnan(x[names.index("h_goals_for_10")])
    # Before any match of the data: no form, no rest days, no league level; pi-ratings are average, matches 0.
    first = store.features(
        league_code=np.array(["AAA"], dtype=object), season=np.array([2020]), home_team_id=np.array([100]),
        away_team_id=np.array([101]), kickoff_ns=np.array([int(t.min())]), cutoff_ns=int(t.min()) - 3 * HOUR,
    )[0]
    assert np.isnan(first[names.index("h_goals_for_5")]) and np.isnan(first[names.index("h_rest_days")])
    assert np.isnan(first[names.index("lg_home_goals")]) and np.isnan(first[names.index("h_promoted")])
    assert first[names.index("pi_h_home")] == 0.0 and first[names.index("pi_h_matches")] == 0.0
    assert first[names.index("h_matches_14d")] == 0.0
    # A team or league the store has never seen is missing, not zero.
    unknown = store.features(
        league_code=np.array(["ZZZ"], dtype=object), season=np.array([2022]), home_team_id=np.array([5]),
        away_team_id=np.array([6]), kickoff_ns=np.array([int(t.max())]), cutoff_ns=int(t.max()),
    )[0]
    assert np.isnan(unknown[names.index("lg_home_goals")]) and np.isnan(unknown[names.index("h_goals_for_10")])


# ---------------------------------------------------------------------------
# The boosting model under the protocol
# ---------------------------------------------------------------------------

TINY = GbmSettings(iterations=25, early_stopping_rounds=8, border_count=16, min_train_rows=40, bucket_days=28)
GRID = {"learning_rate": [0.1], "depth": [2, 3]}
CONFIG = WalkForwardConfig(test_seasons=[2022], scope="per_league", inner_seasons=1, select_per="pooled")
# First kickoff of a matchday: the outcomes of that whole matchday are disturbed while its forecasts are compared.
LEAK_CUTOFF = "2022-10-10T13:00:00+00:00"


def _run(matches, stats, market, *, cache_dir=None):
    engine = PiGbmEngine(matches, stats, settings=TINY, cache_dir=cache_dir)
    return evaluate_model(pi_gbm_spec(engine, market, GRID), matches, market, CONFIG)


@pytest.mark.parametrize("market", [MARKET_1X2, over_under(2.5), MARKET_BTTS], ids=lambda m: m.name)
def test_pi_gbm_produces_valid_probabilities_for_every_market(data, market):
    matches, stats = data
    result = _run(matches, stats, market)
    assert len(result.predictions) == int((matches["season"] == 2022).sum())
    probabilities = result.probabilities()
    assert np.isfinite(probabilities).all()
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert (probabilities > 0).all()
    assert len(result.selections) == 1 and set(result.inner_scores["params"]) == {
        '{"depth": 2, "learning_rate": 0.1}',
        '{"depth": 3, "learning_rate": 0.1}',
    }


@pytest.fixture(scope="module")
def strong_signal() -> tuple[pd.DataFrame, pd.DataFrame]:
    return synthetic(seed=4, seasons=tuple(range(2014, 2023)), spread=0.6)


def test_pi_gbm_learns_the_right_direction_and_beats_the_league_frequencies(strong_signal):
    """Column order, labels and features must line up: with real team strengths the model has to win clearly."""
    matches, stats = strong_signal
    baseline = evaluate_model(league_frequency_spec(MARKET_1X2), matches, MARKET_1X2, CONFIG)
    result = _run(matches, stats, MARKET_1X2)
    model_loss = result.scores()["log_loss"].mean()
    base_loss = baseline.scores()["log_loss"].mean()
    assert model_loss < base_loss - 0.05, (model_loss, base_loss)
    p = result.probabilities()
    y = result.outcome()
    assert p[y == 0, 0].mean() > p[y == 2, 0].mean() + 0.1  # home wins get more home probability than away wins
    assert p[y == 2, 2].mean() > p[y == 0, 2].mean() + 0.1
    totals = _run(matches, stats, over_under(2.5))
    # Class order [under, over]: the mean "over" probability is nearer to the observed over rate than its complement is.
    mean_over, observed = totals.probabilities()[:, 1].mean(), totals.outcome().mean()
    assert abs(mean_over - observed) < abs((1.0 - mean_over) - observed)


def test_no_lookahead_for_the_whole_pipeline(data):
    matches, stats = data
    original = matches

    def run(frame):
        # A fresh engine per run: the store and the boosters see the disturbed outcomes, and the
        # statistics of the future are scrambled too when the outcomes were.
        disturbed = frame is not original
        engine_stats = stats.copy()
        if disturbed:
            future = set(frame.loc[frame["kickoff_utc"] >= LEAK_CUTOFF, "fixture_id"])
            mask = engine_stats["fixture_id"].isin(future)
            engine_stats.loc[mask, "total_shots"] = engine_stats.loc[mask, "total_shots"].to_numpy()[::-1] + 5
        engine = PiGbmEngine(frame, engine_stats, settings=TINY)
        return evaluate_model(pi_gbm_spec(engine, MARKET_1X2, GRID), frame, MARKET_1X2, CONFIG)

    report = check_no_lookahead(run, matches, MARKET_1X2, LEAK_CUTOFF, seed=1)
    assert report.passed and report.n_compared > 20


def test_lookahead_check_catches_a_leaking_cutoff(data, monkeypatch):
    matches, stats = data
    original = matches
    real_rank = AsOfStore._cutoff_rank
    monkeypatch.setattr(AsOfStore, "_cutoff_rank", lambda self, cutoff: real_rank(self, np.asarray(cutoff, dtype=np.int64) + DAY))

    def run(frame):
        engine = PiGbmEngine(frame, stats, settings=TINY)
        return evaluate_model(pi_gbm_spec(engine, MARKET_1X2, GRID), frame, MARKET_1X2, CONFIG)

    report = check_no_lookahead(run, original, MARKET_1X2, LEAK_CUTOFF, seed=1)
    assert not report.passed  # shifting the cut-off by a day lets the matchday's own results in


def test_booster_bucket_is_never_later_than_the_cutoff_and_uses_earlier_matches_only(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY)
    bank = engine.bank
    all_t = _t_ns(matches)
    for cutoff in np.linspace(all_t.min(), all_t.max(), 25).astype(np.int64):
        cut = bank.cut_for(int(cutoff))
        assert cut <= cutoff
        assert cutoff - cut < 28 * DAY
        rows = bank.n_rows_before(cut)
        assert (engine.matrix.t_ns[:rows] < cut).all()
    # Training rows carry features computed 3 hours before their own kickoff and their own result.
    assert engine.matrix.X.shape == (len(matches), len(feature_names()))


def test_booster_rows_are_exactly_the_matches_before_the_bucket_start(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY)
    bank = engine.bank
    t = engine.matrix.t_ns
    # A cut that coincides with a kickoff instant: that match kicked off *at* the bound and is not a training row.
    for k in (10, 60, 200):
        cut = int(t[k])
        rows = bank.n_rows_before(cut)
        assert (t[:rows] < cut).all() and (t[rows:] >= cut).all()


def test_engine_cutoff_is_first_kickoff_minus_the_result_latency(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY)
    frame = matches[(matches["league_code"] == "AAA") & (matches["season"] == 2022) & (matches["round"] == "Regular Season - 6")].copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    assert len(frame) == 4
    assert engine.matchday_cutoff(frame) == int(_t_ns(frame).min()) - 3 * HOUR


def _round_frame(matches: pd.DataFrame, number: int) -> pd.DataFrame:
    frame = matches[(matches["league_code"] == "AAA") & (matches["season"] == 2022) & (matches["round"] == f"Regular Season - {number}")].copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    return frame


def _with_moved_and_changed_match(matches: pd.DataFrame, moved_index: int, kickoff: pd.Timestamp, *, change_result: bool) -> pd.DataFrame:
    out = matches.copy()
    out.loc[moved_index, "kickoff_utc"] = kickoff.isoformat()
    if change_result:
        out.loc[moved_index, ["home_goals", "away_goals"]] = [0, 4]
    return out


def test_result_less_than_the_latency_before_the_matchday_is_invisible_but_an_older_one_is_used(data):
    matches, stats = data
    target = _round_frame(matches, 6)
    first = pd.Timestamp(int(_t_ns(target).min()), tz="UTC")
    # One match of round 5 is moved next to round 6; its teams then play in round 6, so any leak would show.
    moved = matches[(matches["league_code"] == "AAA") & (matches["season"] == 2022) & (matches["round"] == "Regular Season - 5")].index[0]

    def features(kickoff: pd.Timestamp, change_result: bool) -> np.ndarray:
        engine = PiGbmEngine(_with_moved_and_changed_match(matches, moved, kickoff, change_result=change_result), stats, settings=TINY)
        return engine.frame_features(target, engine.matchday_cutoff(target))

    late = first - pd.Timedelta(hours=1)  # finished after the cut-off (first kickoff minus 3 h): not known
    np.testing.assert_array_equal(features(late, False), features(late, True))
    early = first - pd.Timedelta(hours=4)  # known at the cut-off: the changed result must show (positive control)
    assert not np.array_equal(features(early, False), features(early, True), equal_nan=True)


def test_training_rows_carry_features_as_of_their_own_kickoff_minus_the_latency(data, monkeypatch):
    matches, stats = data
    # The engine's store uses the default league-level thresholds; make the reference use the same.
    monkeypatch.setitem(SMALL_STORE, "min_league_matches", 100)
    monkeypatch.setitem(SMALL_STORE, "min_previous_season_matches", 100)
    engine = PiGbmEngine(matches, stats, settings=TINY)
    ordered = matches.assign(t=_t_ns(matches)).sort_values(["t", "fixture_id"]).reset_index(drop=True)
    for row in (3, 60, 150, len(ordered) - 1):
        r = ordered.iloc[row]
        assert engine.matrix.fixture_id[row] == r.fixture_id and engine.matrix.t_ns[row] == r.t
        expected = reference_features(
            matches, stats, league=r.league_code, season=int(r.season), home=int(r.home_team_id), away=int(r.away_team_id),
            kickoff_ns=int(r.t), cutoff_ns=int(r.t) - 3 * HOUR,
        )
        np.testing.assert_allclose(engine.matrix.X[row], expected, rtol=1e-5, atol=1e-5, equal_nan=True, err_msg=f"training row {row}")


def test_training_frame_reaching_past_the_cutoff_is_refused(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY)
    frame = matches[matches["season"] == 2022].head(4).copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    later = matches[matches["season"] == 2022].tail(3).copy()
    later["kickoff"] = pd.to_datetime(later["kickoff_utc"], utc=True).dt.as_unit("ns")
    model = PiGbmModel(engine, MARKET_1X2, {"learning_rate": 0.1, "depth": 2})
    model.fit(later)
    with pytest.raises(ValueError, match="cut-off"):
        model.predict(frame)


def test_boosters_are_cached_on_disk_and_reused(data, tmp_path):
    matches, stats = data
    market = over_under(2.5)
    engine = PiGbmEngine(matches, stats, settings=TINY, cache_dir=tmp_path)
    combos = grid_combinations(GRID)
    cuts = engine.required_cuts(_t_ns(matches[matches["season"] == 2022])[:60])
    assert cuts
    engine.bank.prefetch([market], combos, cuts, n_jobs=1, log=lambda *_: None)
    files = sorted(tmp_path.glob("*.cbm"))
    assert len(files) == len(cuts) * len(combos)
    assert engine.bank.missing_tasks([market], combos, cuts) == []
    # A new engine loads the same boosters and forecasts identically to fitting in-process.
    fresh = PiGbmEngine(matches, stats, settings=TINY, cache_dir=tmp_path)
    frame = matches[matches["season"] == 2022].head(4).copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    frame = frame.drop(columns=["home_goals", "away_goals"])
    first = fresh.predict(market, combos[0], frame)
    inprocess = PiGbmEngine(matches, stats, settings=TINY)
    second = inprocess.predict(market, combos[0], frame)
    np.testing.assert_allclose(first, second, atol=1e-6)


def test_cached_boosters_are_keyed_by_the_features_they_were_fitted_on(data, tmp_path):
    matches, stats = data
    market = over_under(2.5)
    params = {"learning_rate": 0.1, "depth": 2}
    default = PiGbmEngine(matches, stats, settings=TINY, cache_dir=tmp_path)
    other = PiGbmEngine(matches, stats, settings=TINY, cache_dir=tmp_path, pi_config=PiRatingConfig(learning_rate=0.08))
    cut = default.required_cuts(_t_ns(matches[matches["season"] == 2022])[:60])[0]
    assert default.bank.path(market, params, cut) != other.bank.path(market, params, cut)


def test_a_stale_training_matrix_on_disk_is_replaced_before_workers_read_it(data, tmp_path):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY, cache_dir=tmp_path)
    market = over_under(2.5)
    root = engine.bank.write_matrix_files([market])
    np.testing.assert_array_equal(np.load(root / "X.npy"), engine.matrix.X)
    np.save(root / "X.npy", np.zeros_like(engine.matrix.X))  # what an earlier, different feature definition would leave
    np.save(root / "y_over-under-2-5.npy", np.zeros(len(engine.matrix), dtype=np.int64))
    engine.bank.write_matrix_files([market])
    np.testing.assert_array_equal(np.load(root / "X.npy"), engine.matrix.X)
    np.testing.assert_array_equal(np.load(root / "y_over-under-2-5.npy"), engine.bank.labels(market))


def test_a_league_without_enough_pooled_history_gets_no_forecast_not_zeros(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=GbmSettings(iterations=5, min_train_rows=10_000))
    frame = matches.head(4).copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.as_unit("ns")
    out = engine.predict(MARKET_1X2, {"learning_rate": 0.1, "depth": 2}, frame)
    assert np.isnan(out).all()


# ---------------------------------------------------------------------------
# Ratings-only logit (ablation)
# ---------------------------------------------------------------------------


def test_pooled_logit_learns_the_rating_effect_and_keeps_missing_features_missing():
    names = feature_names()
    rng = np.random.default_rng(5)
    n = 4000
    X = np.zeros((n, len(names)), dtype=np.float32)
    gd = rng.normal(0, 1, n)
    X[:, names.index("pi_exp_gd")] = gd
    for name in LOGIT_FEATURES[1:]:
        X[:, names.index(name)] = rng.normal(0, 1, n)
    home_win = (rng.random(n) < 1 / (1 + np.exp(-1.5 * gd))).astype(int)
    model = PooledLogit.fit(X, home_win, 2, names)
    low, high = X[:2].copy(), X[:2].copy()
    low[:, names.index("pi_exp_gd")] = -2.0
    high[:, names.index("pi_exp_gd")] = 2.0
    assert model.predict_proba(high)[0, 1] > 0.8 > 0.2 > model.predict_proba(low)[0, 1]
    broken = X[:3].copy()
    broken[1, names.index("lg_home_goals")] = np.nan
    proba = model.predict_proba(broken)
    assert np.isnan(proba[1]).all() and np.isfinite(proba[[0, 2]]).all()
    assert np.allclose(proba[[0, 2]].sum(axis=1), 1.0)


def test_pi_logit_runs_under_the_protocol(data):
    matches, stats = data
    engine = PiGbmEngine(matches, stats, settings=TINY, kind="logit")
    result = evaluate_model(pi_logit_spec(engine, MARKET_1X2), matches, MARKET_1X2, CONFIG)
    p = result.probabilities()
    finite = np.isfinite(p).all(axis=1)
    assert finite.sum() > 100
    assert np.allclose(p[finite].sum(axis=1), 1.0)
    with pytest.raises(ValueError):
        pi_logit_spec(PiGbmEngine(matches, stats, settings=TINY), MARKET_1X2)
