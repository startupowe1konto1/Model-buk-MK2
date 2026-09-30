"""Model versus market after tax: offline tests on synthetic odds (no archive is read)."""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from model_buk.evaluation import market_test as mt
from model_buk.markets.pricing import devig_probabilities
from model_buk.markets.staking import DEFAULT_PIT_RULE, net_payout

SPEC_1X2 = mt.MARKET_SPECS["1x2"]
SPEC_OU = mt.MARKET_SPECS["over_under_2.5"]
CLOSE_COLUMNS = [*SPEC_1X2.ps_close, *SPEC_OU.ps_close]


# --------------------------------------------------------------------------- #
# Synthetic football-data odds
# --------------------------------------------------------------------------- #


def _book(fair: np.ndarray, margin: float) -> np.ndarray:
    return 1.0 / (fair * (1.0 + margin))


def make_odds_frame(seed: int = 0, seasons: tuple[int, ...] = (2019, 2020, 2021, 2022), weeks: int = 24) -> pd.DataFrame:
    """Matches on Friday, Saturday, Sunday, Tuesday and Wednesday of every week with lines around the true probabilities."""
    rng = np.random.default_rng(seed)
    friday = pd.Timestamp("2019-08-09", tz="UTC")  # a Friday
    rows = []
    fixture = 1
    for season in seasons:
        season_start = friday + pd.Timedelta(days=364 * (season - 2019))
        for week in range(weeks):
            for offset, count in ((0, 2), (1, 6), (2, 4), (4, 3), (5, 3)):
                for slot in range(count):
                    kickoff = season_start + pd.Timedelta(days=7 * week + offset, hours=slot % 3)
                    lam_h, lam_a = rng.uniform(0.8, 2.2), rng.uniform(0.6, 1.8)
                    grid = np.outer(stats.poisson.pmf(np.arange(9), lam_h), stats.poisson.pmf(np.arange(9), lam_a))
                    grid /= grid.sum()
                    home = np.tril(grid, -1).sum()
                    draw = np.trace(grid)
                    true_1x2 = np.array([home, draw, 1.0 - home - draw])
                    totals = np.add.outer(np.arange(9), np.arange(9))
                    true_ou = np.array([grid[totals <= 2].sum(), grid[totals > 2].sum()])
                    hg, ag = np.unravel_index(rng.choice(grid.size, p=grid.reshape(-1)), grid.shape)
                    row = {
                        "fixture_id": str(fixture),
                        "league": "L1" if slot % 2 == 0 else "L2",
                        "season": season,
                        "kickoff": kickoff,
                        "home_goals": float(hg),
                        "away_goals": float(ag),
                    }
                    for spec, true in ((SPEC_1X2, true_1x2), (SPEC_OU, true_ou)):
                        def line(sigma: float) -> np.ndarray:
                            noisy = true * np.exp(rng.normal(0.0, sigma, size=true.shape))
                            return noisy / noisy.sum()

                        ps_open = _book(line(0.05), 0.035)
                        ps_close = _book(line(0.02), 0.03)
                        for name, value in zip(spec.ps_open, ps_open):
                            row[name] = float(value)
                        for name, value in zip(spec.ps_close, ps_close):
                            row[name] = float(value)
                        for book, margin, sigma in (("bet365", 0.06, 0.05), ("max", 0.02, 0.06), ("avg", 0.055, 0.02)):
                            prices = _book(line(sigma), margin)
                            if rng.random() < 0.06:  # a stale price now and then
                                prices[rng.integers(len(prices))] *= 1.3
                            for name, value in zip(spec.books[book], prices):
                                row[name] = float(value)
                    rows.append(row)
                    fixture += 1
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def odds_frame() -> pd.DataFrame:
    return make_odds_frame()


def _legs(frame: pd.DataFrame, seasons=(2022,), **kwargs) -> mt.LegTable:
    tables = [
        mt.build_leg_table(mt.build_market_panel(frame, spec), seasons=seasons, **kwargs) for spec in (SPEC_1X2, SPEC_OU)
    ]
    return mt.concat_leg_tables(tables)


@pytest.fixture(scope="module")
def legs(odds_frame) -> mt.LegTable:
    return _legs(odds_frame)


def _cell(**kwargs) -> mt.CellSpec:
    defaults = dict(strategy="reference", book="bet365", method="power", z=0.0, seasons=(2022,))
    defaults.update(kwargs)
    return mt.CellSpec(**defaults)


# --------------------------------------------------------------------------- #
# Basics: de-vig, windows, Polish prices, drift scale
# --------------------------------------------------------------------------- #


def test_devig_matrix_uses_the_pricing_functions_and_keeps_missing_rows_missing() -> None:
    odds = np.array([[2.0, 3.4, 4.1], [np.nan, 3.0, 3.0], [1.0, 5.0, 9.0], [1.5, 4.0, 7.5]])
    for method in mt.DEVIG_ORDER:
        out = mt.devig_matrix(odds, method)
        assert np.allclose(out[0], devig_probabilities((2.0, 3.4, 4.1), method))
        assert np.allclose(out[3], devig_probabilities((1.5, 4.0, 7.5), method))
        assert np.isnan(out[1]).all() and np.isnan(out[2]).all()  # never completed or filled with zero


def test_snapshot_window_puts_friday_to_monday_and_tuesday_to_thursday_together() -> None:
    days = pd.to_datetime(
        ["2023-03-03", "2023-03-04", "2023-03-05", "2023-03-06", "2023-03-07", "2023-03-08", "2023-03-09", "2023-03-10"],
        utc=True,
    )
    labels = mt.snapshot_window(pd.Series(days))
    assert list(labels[:4]) == ["F2023-03-03"] * 4  # Fri, Sat, Sun, Mon
    assert list(labels[4:7]) == ["T2023-03-07"] * 3  # Tue, Wed, Thu
    assert labels[7] == "F2023-03-10"  # the next Friday
    assert mt.window_datetime("F2023-03-03").tzinfo is not None


def test_polish_prices_carry_exactly_the_assumed_overround(odds_frame) -> None:
    panel = mt.build_market_panel(odds_frame, SPEC_1X2)
    for kind in ("pl_ps", "pl_b365"):
        prices = mt.polish_odds(panel, kind, 1.07)
        assert np.allclose((1.0 / prices).sum(axis=1), 1.07)
    broken = odds_frame.copy()
    broken.loc[0, "PSD"] = np.nan
    panel = mt.build_market_panel(broken, SPEC_1X2)
    assert np.isnan(mt.polish_odds(panel, "pl_ps")[0]).all()
    with pytest.raises(ValueError):
        mt.polish_odds(panel, "pl_ps", 1.0)


def test_drift_scale_reads_only_seasons_before_the_target(odds_frame) -> None:
    panel = mt.build_market_panel(odds_frame, SPEC_1X2)
    fair_open, fair_close = panel.fair[("ps_open", "power")], panel.fair[("ps_close", "power")]
    base = mt.drift_scale(fair_open, fair_close, panel.season, 2021)
    changed = fair_close.copy()
    changed[panel.season >= 2021] = 1.0 / 3.0  # nothing from the target season on may matter
    assert mt.drift_scale(fair_open, changed, panel.season, 2021) == base
    assert np.isfinite(base) and base > 0
    assert math.isnan(mt.drift_scale(fair_open, fair_close, panel.season, 2019))


def test_load_joined_odds_keeps_missing_prices_missing(tmp_path) -> None:
    columns = [c for spec in (SPEC_1X2, SPEC_OU) for c in spec.odds_columns()]
    path = tmp_path / "joined.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        cols = ", ".join(f'"{c}" REAL' for c in dict.fromkeys(columns))
        connection.execute(
            "CREATE TABLE fd_af_matches (fixture_id INTEGER, af_league_code TEXT, af_season INTEGER, "
            f"af_kickoff_utc TEXT, af_home_goals INTEGER, af_away_goals INTEGER, {cols})"
        )
        connection.execute(
            "INSERT INTO fd_af_matches (fixture_id, af_league_code, af_season, af_kickoff_utc, af_home_goals, af_away_goals, PSH, PSD) "
            "VALUES (7, 'EPL', 2023, '2023-05-06T14:00:00+00:00', 2, 1, 2.1, 3.3)"
        )
        connection.commit()
    frame = mt.load_joined_odds(path, seasons=[2023])
    assert frame.loc[0, "fixture_id"] == "7" and frame.loc[0, "PSH"] == 2.1
    assert math.isnan(frame.loc[0, "PSA"])  # NULL stays NaN, not zero
    assert mt.load_joined_odds(path, seasons=[2020]).empty


# --------------------------------------------------------------------------- #
# No look-ahead: decisions never read the closing line
# --------------------------------------------------------------------------- #


def _scramble_close(frame: pd.DataFrame, season: int, seed: int = 5) -> pd.DataFrame:
    out = frame.copy()
    rows = np.flatnonzero((out["season"] == season).to_numpy())
    shuffled = np.random.default_rng(seed).permutation(rows)
    out.loc[out.index[rows], CLOSE_COLUMNS] = out.loc[out.index[shuffled], CLOSE_COLUMNS].to_numpy()
    return out


def _picks(frame: pd.DataFrame, cell: mt.CellSpec) -> pd.DataFrame:
    return mt.select_gross_legs(_legs(frame, seasons=cell.seasons).open, cell)


def test_decisions_do_not_change_when_the_closing_line_of_the_decision_season_changes(odds_frame) -> None:
    cell = _cell()
    before = _picks(odds_frame, cell)
    after = _picks(_scramble_close(odds_frame, 2022), cell)
    assert len(before) > 10  # the comparison is not vacuous
    pd.testing.assert_frame_equal(before.reset_index(drop=True), after.reset_index(drop=True))


def test_closing_line_only_changes_the_clv_after_the_picks_are_made(odds_frame) -> None:
    cell = _cell()
    legs_a = _legs(odds_frame)
    legs_b = _legs(_scramble_close(odds_frame, 2022))
    picks = mt.select_gross_legs(legs_a.open, cell)
    bets = mt.make_bets(picks, cell, 1.0, 1)
    clv_a = mt.settle_bets(bets, legs_a.settle, cell, 1.0)["clv_net"].to_numpy()
    clv_b = mt.settle_bets(bets, legs_b.settle, cell, 1.0)["clv_net"].to_numpy()
    assert not np.allclose(clv_a, clv_b)


def _flatten_close(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    """Closing lines of one season replaced by an extreme constant line (a huge move from every opening line)."""
    out = frame.copy()
    rows = (out["season"] == season).to_numpy()
    out.loc[rows, list(SPEC_1X2.ps_close)] = 3.0
    out.loc[rows, list(SPEC_OU.ps_close)] = 2.0
    return out


@pytest.mark.parametrize("book", ["bet365", "max"])
def test_haircut_scale_reads_only_earlier_seasons_never_the_decision_season(odds_frame, book) -> None:
    """The haircut ``z * SE`` uses the drift of earlier seasons; the closing line of the season decided on must not enter it."""
    cell = _cell(book=book, z=mt.DEFAULT_Z)
    before = _picks(odds_frame, cell)
    assert len(before) > 20
    assert (before["p_lower"] < before["p"]).all() and (before["p_lower"] * before["odds"] > 1.0).all()  # the haircut is applied
    after = _picks(_flatten_close(odds_frame, 2022), cell)
    pd.testing.assert_frame_equal(before.reset_index(drop=True), after.reset_index(drop=True))
    # positive control: the closing lines of an EARLIER season do set the scale, so the check above has teeth
    earlier = _picks(_flatten_close(odds_frame, 2021), cell)
    assert len(earlier) != len(before) or not before.reset_index(drop=True).equals(earlier.reset_index(drop=True))


def test_leak_test_has_teeth_a_decision_that_reads_the_close_is_caught(odds_frame, monkeypatch) -> None:
    """Mutation: point the decision reference at the closing line; the scramble check must then fail."""
    cell = _cell()
    monkeypatch.setattr(mt, "OPEN_FAIR_SOURCE", "ps_close")
    before = _picks(odds_frame, cell)
    after = _picks(_scramble_close(odds_frame, 2022), cell)
    same = len(before) == len(after) and before.reset_index(drop=True).equals(after.reset_index(drop=True))
    assert not same


def test_decision_frames_refuse_closing_and_outcome_columns(legs) -> None:
    mt.assert_open_only(legs.open)
    for name in ("close_fair_power", "win", "y", "home_goals", "PSCH_closing"):
        with pytest.raises(mt.ClosingLeakError):
            mt.assert_open_only(legs.open.assign(**{name: 1.0}))
    with pytest.raises(mt.ClosingLeakError):
        mt.LegTable(legs.open.assign(close_fair_power=0.3), legs.settle)
    with pytest.raises(mt.ClosingLeakError):
        mt.select_gross_legs(legs.open.assign(win=1.0), _cell())


def test_pool_weight_and_residual_never_see_the_season_they_forecast(odds_frame) -> None:
    panel = mt.build_market_panel(odds_frame, SPEC_1X2)
    rng = np.random.default_rng(3)
    model = rng.dirichlet([3, 2, 2], size=len(panel))
    forecasts = {"m": model}
    base, _ = mt.pooled_with_market(panel, "power", forecasts, {2021: "m", 2022: "m"}, first_fit_season=2019)
    disturbed = mt.MarketPanel(panel.spec, panel.meta.assign(y=np.where(panel.season >= 2021, 0.0, panel.meta["y"])), panel.odds, panel.fair)
    again, table = mt.pooled_with_market(disturbed, "power", forecasts, {2021: "m", 2022: "m"}, first_fit_season=2019)
    rows = panel.season == 2021
    assert np.isfinite(base[rows]).all()
    assert np.allclose(base[rows], again[rows])  # outcomes of season 2021 and later did not enter the 2021 weight
    assert set(table["season"]) == {2021, 2022}
    assert not np.allclose(base[panel.season == 2022], again[panel.season == 2022])  # the 2022 weight does use 2021


def test_residual_model_is_three_class_only_and_uses_only_earlier_seasons(odds_frame) -> None:
    from model_buk.models.market_residual import RESIDUAL_FEATURE_COLUMNS

    panel = mt.build_market_panel(odds_frame, SPEC_1X2)
    features = pd.DataFrame(
        np.random.default_rng(1).normal(size=(len(panel), len(RESIDUAL_FEATURE_COLUMNS) - 2)),
        columns=[c for c in RESIDUAL_FEATURE_COLUMNS if not c.startswith("opening_")],
    )
    features["source_row_id"] = panel.fixture_ids
    base, _ = mt.residual_forecasts_1x2(panel, "power", features, [2022], l2_grid=(0.1,))
    disturbed = mt.MarketPanel(panel.spec, panel.meta.assign(y=np.where(panel.season >= 2022, 2.0, panel.meta["y"])), panel.odds, panel.fair)
    again, _ = mt.residual_forecasts_1x2(disturbed, "power", features, [2022], l2_grid=(0.1,))
    rows = panel.season == 2022
    assert np.isfinite(base[rows]).all() and np.allclose(base[rows].sum(axis=1), 1.0)
    assert np.allclose(base[rows], again[rows])
    with pytest.raises(ValueError):
        mt.residual_forecasts_1x2(mt.build_market_panel(odds_frame, SPEC_OU), "power", features, [2022])


# --------------------------------------------------------------------------- #
# Singles, accumulators, one pick per match
# --------------------------------------------------------------------------- #


def test_one_pick_per_match_across_both_markets(legs) -> None:
    picks = mt.select_gross_legs(legs.open, _cell())
    assert picks["fixture_id"].is_unique
    assert (picks["p_lower"] * picks["odds"] > 1.0).all()
    assert set(picks["market"]) <= set(mt.MARKET_ORDER)


@pytest.mark.parametrize("n_legs", [2, 3, 4])
def test_accumulator_legs_come_from_different_matches_priced_in_one_snapshot(legs, n_legs) -> None:
    cell = _cell()
    picks = mt.select_gross_legs(legs.open, cell)
    bets = mt.make_bets(picks, cell, 1.0, n_legs)
    assert len(bets) > 0
    open_frame = legs.open
    for record in bets.itertuples(index=False):
        rows = open_frame.loc[list(record.leg_ids)]
        assert len(rows) == n_legs
        assert rows["fixture_id"].is_unique  # different matches
        assert rows["group"].nunique() == 1 and rows["group"].iloc[0] == record.group  # the same snapshot window
        assert record.cluster == record.group  # a coupon is clustered by its snapshot window
        assert record.odds == pytest.approx(float(np.prod([picks.set_index("leg_id").loc[i, "odds"] for i in record.leg_ids])))
    # every leg is used at most once
    all_legs = [i for ids in bets["leg_ids"] for i in ids]
    assert len(all_legs) == len(set(all_legs))


def test_single_bets_are_clustered_by_matchday_and_pass_the_taxed_gate(legs) -> None:
    cell = _cell()
    picks = mt.select_gross_legs(legs.open, cell)
    for tau in (0.88, 0.94, 1.0):
        bets = mt.make_bets(picks, cell, tau, 1)
        opened = legs.open.loc[[ids[0] for ids in bets["leg_ids"]]]
        assert (bets["cluster"].to_numpy(dtype=object) == opened["cluster"].to_numpy(dtype=object)).all()
        assert (tau * bets["p_lower"] * bets["odds"] > 1.0).all()


def test_tax_regimes_switch_the_gate_the_clv_and_the_payout(legs) -> None:
    assert dict(mt.TAX_REGIMES) == {"tau_0.88": 0.88, "tau_0.94": 0.94, "tau_1.00": 1.0}
    cell = _cell()
    picks = mt.select_gross_legs(legs.open, cell)
    counts = {tau: len(mt.make_bets(picks, cell, tau, 1)) for tau in (0.88, 0.94, 1.0)}
    assert counts[0.88] < counts[0.94] < counts[1.0]  # a heavier stake tax passes fewer bets
    settled = {tau: mt.settle_bets(mt.make_bets(picks, cell, tau, 1), legs.settle, cell, tau) for tau in (0.88, 1.0)}
    top = settled[1.0]
    row = top[top["win"] == 1.0].iloc[0]
    assert row["clv_net"] == pytest.approx(row["odds"] * row["fair_close"] - 1.0)  # tau 1: net equals raw
    assert row["profit_flat"] == pytest.approx(row["stake_flat"] * (row["odds"] - 1.0))
    low = settled[0.88]
    win = low[low["win"] == 1.0].iloc[0]
    assert win["clv_net"] == pytest.approx(0.88 * win["odds"] * win["fair_close"] - 1.0)
    assert win["clv_raw"] == pytest.approx(win["odds"] * win["fair_close"] - 1.0)
    assert win["profit_flat"] == pytest.approx(win["stake_flat"] * (0.88 * win["odds"] - 1.0))
    lost = low[low["win"] == 0.0].iloc[0]
    assert lost["profit_flat"] == pytest.approx(-lost["stake_flat"])


def test_winnings_pit_cuts_the_flat_stake_of_a_long_shot_coupon() -> None:
    settle = pd.DataFrame({"win": [1.0, 1.0], "close_fair_power": [0.05, 0.05]}, index=pd.RangeIndex(2, name="leg_id"))
    bets = pd.DataFrame(
        [
            {
                "group": "F2023-01-06", "cluster": "F2023-01-06", "season": 2023, "kickoff_last_ns": 1, "leg_ids": (0, 1),
                "fixtures": "a|b", "odds": 60.0, "p": 0.03, "p_lower": 0.02, "n_legs": 2, "tau": 0.88,
            }
        ]
    )
    settled = mt.settle_bets(bets, settle, _cell(), 0.88)
    stake = settled["stake_flat"].iloc[0]
    assert stake < mt.FLAT_STAKE_PLN  # 100 PLN would pay 4 224 PLN, above the 2 280 PLN threshold
    assert 0.88 * 60.0 * stake <= DEFAULT_PIT_RULE.threshold_pln + 1e-9
    assert settled["profit_flat"].iloc[0] == pytest.approx(net_payout(stake, 60.0, 0.88) - stake)


def test_coupon_settlement_needs_every_leg_and_multiplies_the_closing_probabilities() -> None:
    settle = pd.DataFrame(
        {"win": [1.0, 0.0, 1.0, np.nan], "close_fair_power": [0.5, 0.4, np.nan, 0.3]}, index=pd.RangeIndex(4, name="leg_id")
    )

    def coupon(ids: tuple[int, int]) -> dict:
        return {
            "group": "F2023-01-06", "cluster": "F2023-01-06", "season": 2023, "kickoff_last_ns": 1, "leg_ids": ids,
            "fixtures": "a|b", "odds": 6.0, "p": 0.2, "p_lower": 0.18, "n_legs": 2, "tau": 0.88,
        }

    settled = mt.settle_bets(pd.DataFrame([coupon((0, 1)), coupon((0, 2)), coupon((0, 3))]), settle, _cell(), 0.88)
    lost, unpriced, unknown = (settled.iloc[i] for i in range(3))
    assert lost["win"] == 0.0 and lost["profit_flat"] == pytest.approx(-lost["stake_flat"])  # one lost leg loses the coupon
    assert lost["clv_net"] == pytest.approx(0.88 * 6.0 * (0.5 * 0.4) - 1.0)  # the closing probabilities of all legs multiply
    assert lost["clv_raw"] == pytest.approx(6.0 * (0.5 * 0.4) - 1.0)
    assert unpriced["win"] == 1.0 and unpriced["profit_flat"] == pytest.approx(unpriced["stake_flat"] * (0.88 * 6.0 - 1.0))
    assert math.isnan(unpriced["clv_net"]) and math.isnan(unpriced["fair_close"])  # one missing closing line: no CLV, not a guess
    assert math.isnan(unknown["win"]) and math.isnan(unknown["profit_flat"])  # an unknown outcome is not a loss
    assert unknown["clv_net"] == pytest.approx(0.88 * 6.0 * (0.5 * 0.3) - 1.0)


def test_missing_closing_lines_are_dropped_from_clv_and_never_counted_as_zero() -> None:
    n = 60
    clv = np.where(np.arange(n) % 3 == 0, np.nan, 0.05)
    frame = pd.DataFrame(
        {
            "clv_net": clv, "clv_raw": clv + 0.1, "win": 1.0, "odds": 2.0, "profit_flat": 100.0, "stake_flat": 100.0,
            "profit_kelly": 0.0, "stake_kelly": 0.0, "kickoff_last_ns": np.arange(n), "group": "g", "p_lower": 0.5,
            "tau": 1.0, "cluster": np.arange(n) // 3,
        }
    )
    summary = mt.summarise_bets(frame, n_resamples=200)
    assert summary["n_bets"] == n and summary["n_clv"] == int(np.isfinite(clv).sum())
    assert summary["clv_net_mean"] == pytest.approx(0.05) and summary["clv_net_median"] == pytest.approx(0.05)
    assert summary["share_beat_raw"] == pytest.approx(1.0)  # the share is taken over priced bets only


def test_kelly_path_grows_on_wins_and_draws_down_on_losses() -> None:
    def frame(wins):
        return pd.DataFrame(
            {
                "group": [f"F2023-01-0{i}" for i in range(len(wins))],
                "kickoff_last_ns": list(range(len(wins))),
                "odds": 2.5,
                "p_lower": 0.5,
                "tau": 1.0,
                "win": wins,
            }
        )

    drawdown, growth = mt.kelly_path(frame([1.0, 1.0, 1.0]))
    assert drawdown == 0.0 and growth > 1.0
    drawdown, growth = mt.kelly_path(frame([1.0, 0.0, 0.0]))
    assert drawdown > 0.0
    assert mt.kelly_path(frame([]).iloc[0:0]) == (0.0, 1.0)


# --------------------------------------------------------------------------- #
# Statistics: clustered bootstrap, random baseline, family correction
# --------------------------------------------------------------------------- #


def test_bootstrap_resamples_whole_matchdays_not_single_bets() -> None:
    # Two matchdays whose bets all share one value each: a resample of whole matchdays can only give -1, 0 or +1.
    values = np.repeat(np.array([-1.0, 1.0]), 20)
    matchday = np.repeat(np.arange(2), 20)
    draws = mt.cluster_bootstrap_means(values, matchday, n_resamples=300, seed=2)
    assert set(np.round(draws, 9)) <= {-1.0, 0.0, 1.0} and len(set(np.round(draws, 9))) == 3
    single_bets = mt.cluster_bootstrap_means(values, np.arange(len(values)), n_resamples=300, seed=2)
    assert len(set(np.round(single_bets, 6))) > 3  # bets as their own clusters would give a continuum

    # Bets inside a matchday are perfectly dependent: pretending they are independent understates the noise.
    day_values = np.repeat(np.array([-1.0, 1.0, -1.0, 1.0, 0.0, 0.5, -0.5, 1.0, -1.0, 0.0]), 20)
    days = np.repeat(np.arange(10), 20)
    clustered = mt.bootstrap_mean(day_values, days, n_resamples=2000, seed=1)
    naive = mt.bootstrap_mean(day_values, np.arange(len(day_values)), n_resamples=2000, seed=1)
    assert clustered["se"] > 3.0 * naive["se"]
    assert clustered["lo"] < clustered["mean"] < clustered["hi"]


def test_summary_intervals_depend_on_the_matchday_labels() -> None:
    n = 400
    base = pd.DataFrame(
        {
            "clv_net": np.repeat(np.linspace(-0.05, 0.08, 20), 20),
            "clv_raw": np.repeat(np.linspace(-0.05, 0.08, 20), 20) + 0.12,
            "win": 0.0,
            "odds": 2.0,
            "profit_flat": np.repeat(np.linspace(-100.0, 90.0, 20), 20),
            "stake_flat": 100.0,
            "profit_kelly": 0.0,
            "stake_kelly": 0.0,
            "kickoff_last_ns": np.arange(n),
            "group": "g",
            "p_lower": 0.5,
            "tau": 1.0,
        }
    )
    clustered = mt.summarise_bets(base.assign(cluster=np.repeat(np.arange(20), 20)), n_resamples=1000)
    per_bet = mt.summarise_bets(base.assign(cluster=np.arange(n)), n_resamples=1000)
    width = lambda s: s["clv_net_hi"] - s["clv_net_lo"]  # noqa: E731
    assert clustered["clv_net_mean"] == pytest.approx(per_bet["clv_net_mean"])
    assert width(clustered) > 2.0 * width(per_bet)
    assert clustered["yield_flat_hi"] - clustered["yield_flat_lo"] > 2.0 * (per_bet["yield_flat_hi"] - per_bet["yield_flat_lo"])
    assert clustered["interval_ok"] and clustered["n_clusters"] == 20


def test_summary_of_no_bets_is_missing_not_zero() -> None:
    summary = mt.summarise_bets(pd.DataFrame(columns=["clv_net"]))
    assert summary["n_bets"] == 0 and not summary["interval_ok"]
    assert math.isnan(summary["clv_net_mean"]) and math.isnan(summary["yield_flat"])


def test_random_baseline_of_random_picks_is_not_significant(legs) -> None:
    cell = _cell(z=0.0)
    frame = mt._cell_frame(legs.open, cell)
    rng = np.random.default_rng(11)
    ok = frame[f"odds_{cell.book}"].notna() & frame["ref_open_power"].notna() & legs.settle.loc[frame.index, "close_fair_power"].notna()
    chosen = rng.choice(frame.index[ok.to_numpy()].to_numpy(), size=300, replace=False)
    chosen_frame = frame.loc[chosen]
    unscreened = pd.DataFrame(  # random legs straight into settlement: no price screen
        {
            "group": chosen_frame["group"].to_numpy(dtype=object),
            "cluster": chosen_frame["cluster"].to_numpy(dtype=object),
            "season": chosen_frame["season"].to_numpy(),
            "kickoff_last_ns": chosen_frame["kickoff_ns"].to_numpy(),
            "leg_ids": [(int(i),) for i in chosen],
            "fixtures": chosen_frame["fixture_id"].to_numpy(dtype=object),
            "odds": chosen_frame["odds_bet365"].to_numpy(),
            "p": chosen_frame["ref_open_power"].to_numpy(),
            "p_lower": chosen_frame["ref_open_power"].to_numpy(),
            "n_legs": 1,
            "tau": 1.0,
        }
    )
    bets = mt.settle_bets(unscreened, legs.settle, cell, 1.0)
    base = mt.random_baseline(bets, legs, cell, 1.0, n_replicates=200, seed=3)
    assert base["random_replicates"] == 200
    assert base["p_random_share_raw"] > 0.02 and base["p_random_mean_net"] > 0.02  # random picks do not beat random picks
    assert base["random_share_raw_p05"] <= base["random_share_raw_mean"] <= base["random_share_raw_p95"]


def test_random_baseline_reproduces_the_odds_mix_and_beats_a_price_screen(legs) -> None:
    cell = _cell(z=0.0)
    picks = mt.select_gross_legs(legs.open, cell)
    bets = mt.settle_bets(mt.make_bets(picks, cell, 1.0, 1), legs.settle, cell, 1.0)
    base = mt.random_baseline(bets, legs, cell, 1.0, n_replicates=200, seed=3)
    # picks were screened on price against the reference: they beat the close far more often than random legs of the same odds mix
    assert (bets["clv_raw"] > 0).mean() > base["random_share_raw_p95"]
    assert base["p_random_share_raw"] < 0.05
    # the random legs never take a cheaper band than the strategy: the mean odds are close
    two = mt.make_bets(picks, cell, 1.0, 2)
    two = mt.settle_bets(two, legs.settle, cell, 1.0)
    coupons = mt.random_baseline(two, legs, cell, 1.0, n_replicates=50, seed=4)
    assert coupons["random_replicates"] == 50 and np.isfinite(coupons["random_share_raw_mean"])


def test_random_baseline_matches_window_market_and_odds_band_of_every_real_leg(legs) -> None:
    cell = _cell(z=0.0)
    picks = mt.select_gross_legs(legs.open, cell)
    assert picks["market"].nunique() == 2  # both markets are in play, so a market mismatch would show
    for n_legs in (1, 2):
        bets = mt.settle_bets(mt.make_bets(picks, cell, 1.0, n_legs), legs.settle, cell, 1.0)
        base = mt.random_baseline(bets, legs, cell, 1.0, n_replicates=60, seed=1)
        assert base["random_same_window_share"] == 1.0
        assert base["random_same_market_share"] == 1.0
        assert base["random_same_band_share"] == 1.0
        real_mean_odds = legs.open.loc[[i for ids in bets["leg_ids"] for i in ids], "odds_bet365"].mean()
        assert base["random_mean_leg_odds"] == pytest.approx(real_mean_odds, rel=0.08)  # the odds mix is reproduced


def test_reference_veto_keeps_only_legs_the_model_rates_at_least_as_likely_as_the_reference(legs) -> None:
    frame = legs.open.copy()
    reference = frame["ref_open_power"].to_numpy()
    rng = np.random.default_rng(4)
    frame["p_model"] = np.clip(reference * np.exp(rng.normal(0.0, 0.15, size=len(frame))), 0.01, 0.99)
    cell = _cell(strategy="reference_veto")
    picks = mt.select_gross_legs(frame, cell)
    assert len(picks) > 10
    chosen = frame.loc[picks["leg_id"]]
    assert (chosen["p_model"].to_numpy() >= chosen["ref_open_power"].to_numpy()).all()
    assert len(mt.select_gross_legs(frame.assign(p_model=reference * 0.9), cell)) == 0  # the model doubts everything
    assert len(mt.select_gross_legs(frame.assign(p_model=np.nan), cell)) == 0  # no forecast: nothing invented


@pytest.mark.parametrize("n_legs", [2, 3])
def test_coupon_gate_pays_the_stake_tax_once_on_the_product_of_the_legs(legs, n_legs) -> None:
    cell = _cell()
    picks = mt.select_gross_legs(legs.open, cell)
    tau = 0.88
    bets = mt.make_bets(picks, cell, tau, n_legs)
    assert len(bets) > 0
    assert (tau * bets["p_lower"] * bets["odds"] > 1.0).all()
    expected = 0
    for _, part in picks.sort_values(["group", "rank"], kind="stable").groupby("group", sort=False):
        records = part.to_dict("records")
        for start in range(0, len(records) - n_legs + 1, n_legs):
            block = records[start : start + n_legs]
            expected += int(tau * np.prod([r["p_lower"] * r["odds"] for r in block]) > 1.0)
    assert len(bets) == expected  # a gate that taxed each leg (tau ** n) or ignored the tax would count differently


def test_kelly_stake_is_sized_on_the_lower_probability_bound() -> None:
    settle = pd.DataFrame({"win": [1.0], "close_fair_power": [0.4]}, index=pd.RangeIndex(1, name="leg_id"))

    def bet(p_lower: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "group": "F2023-01-06", "cluster": "c", "season": 2023, "kickoff_last_ns": 1, "leg_ids": (0,),
                    "fixtures": "a", "odds": 2.6, "p": 0.5, "p_lower": p_lower, "n_legs": 1, "tau": 1.0,
                }
            ]
        )

    # p * odds = 1.30 (a fat edge), but p_lower * odds = 0.988: no edge at the lower bound, so no Kelly stake
    assert mt.settle_bets(bet(0.38), settle, _cell(), 1.0)["stake_kelly"].iloc[0] == 0.0
    stake = mt.settle_bets(bet(0.45), settle, _cell(), 1.0)["stake_kelly"].iloc[0]
    from model_buk.markets.staking import plan_stake

    expected = plan_stake(
        bankroll=mt.KELLY_BANKROLL_PLN, p_lower=0.45, decimal_odds=2.6, tax_multiplier=1.0,
        kelly_multiplier=mt.KELLY_MULTIPLIER, max_bankroll_fraction=mt.KELLY_MAX_FRACTION,
    ).stake
    assert stake == pytest.approx(expected) and stake > 0.0


def test_family_correction_keeps_untestable_setups_in_the_family() -> None:
    verdicts = pd.DataFrame(
        {
            "p_iut": [0.001, 0.02, np.nan, np.nan, 0.5],
            "positive_lb": [True, True, False, False, False],
        }
    )
    mask = pd.Series([True, True, True, True, False])
    out = mt.apply_family_correction(verdicts, {"plan": mask})
    assert out["p_family"].tolist() == [0.001, 0.02, 1.0, 1.0, 0.5]
    assert out.loc[0, "p_holm_plan"] == pytest.approx(0.004)  # 4 hypotheses in the family, NaN ones included
    assert bool(out.loc[0, "signal_plan"]) and not bool(out.loc[1, "signal_plan"])
    assert out.loc[4, "p_holm_plan"] != out.loc[4, "p_holm_plan"]  # outside the family
    assert out["n_family_plan"].iloc[0] == 4 and out["n_family_all"].iloc[0] == 5
    assert out.loc[0, "p_holm_all"] == pytest.approx(0.005)  # five setups tried in all


def test_plan_family_masks_separate_polish_proxies_from_price_shopping() -> None:
    base = dict(market_set="all", seasons="2023-2025".replace("-", "-"), strategy="reference", z=mt.DEFAULT_Z, polish_overround=0.0)
    seasons = "-".join(str(s) for s in mt.SELECTION_SEASONS)
    rows = [
        {**base, "seasons": seasons, "book": "bet365"},
        {**base, "seasons": seasons, "book": "max"},
        {**base, "seasons": seasons, "book": "pl_ps", "polish_overround": 1.06},
        {**base, "seasons": seasons, "book": "pl_ps", "polish_overround": 1.04},  # margin sensitivity: not in the family
        {**base, "seasons": seasons, "book": "bet365", "market_set": "1x2"},
        {**base, "seasons": "2022-2023-2024-2025", "book": "bet365"},
        {**base, "seasons": seasons, "book": "bet365", "strategy": "model"},
    ]
    masks = mt.plan_family_masks(pd.DataFrame(rows))
    assert masks["plan"].tolist() == [True, False, True, False, False, False, False]
    assert masks["shopping"].tolist() == [False, True, False, False, False, False, False]
    assert masks["headline"].tolist() == [True, False, False, False, False, False, False]


def test_bootstrap_p_value_is_resolved_beyond_the_resample_count() -> None:
    clusters = np.repeat(np.arange(60), 5)
    rng = np.random.default_rng(9)
    strong = rng.normal(0.2, 0.05, size=len(clusters))
    result = mt.bootstrap_mean(strong, clusters, n_resamples=500, seed=0)
    assert result["p_positive"] == pytest.approx(1.0 / 501.0)  # the bootstrap cannot say more than 1 / (B + 1)
    assert result["p_test"] < 1e-10  # the normal approximation with the bootstrap SE can
    weak = mt.bootstrap_mean(rng.normal(0.0, 0.5, size=len(clusters)), clusters, n_resamples=500, seed=0)
    assert weak["p_test"] >= weak["p_positive"]


def test_veto_effect_compares_legs_the_model_agrees_with_to_those_it_does_not(legs) -> None:
    open_frame = legs.open.copy()
    rng = np.random.default_rng(2)
    reference = open_frame["ref_open_power"].to_numpy()
    open_frame["p_model"] = np.clip(reference * np.exp(rng.normal(0.0, 0.1, size=len(open_frame))), 0.01, 0.99)
    table = mt.LegTable(open_frame, legs.settle)
    result = mt.veto_effect(table, _cell(z=0.0), n_resamples=300)
    assert result["n_keep"] > 20 and result["n_veto"] > 20
    assert result["lo"] <= result["diff"] <= result["hi"] or np.isnan(result["diff"])
    assert result["diff"] == pytest.approx(result["clv_keep"] - result["clv_veto"])
    empty = mt.veto_effect(mt.LegTable(legs.open.assign(p_model=np.nan), legs.settle), _cell(z=0.0), n_resamples=50)
    assert empty["n_keep"] == 0 and np.isnan(empty["diff"])  # no forecast: nothing to sort by, and nothing invented


def test_combine_devig_verdicts_needs_every_method_to_be_positive() -> None:
    def rows(lows):
        return [
            {
                "market_set": "all", "strategy": "reference", "book": "bet365", "z": 1.645, "seasons": "2023", "regime": "tau_1.00",
                "tau": 1.0, "n_legs": 1, "method": m, "n_bets": 100, "n_clv": 100, "interval_ok": True, "clv_net_lo": lo,
                "clv_net_mean": lo + 0.02, "clv_net_ptest": 0.01 if lo > 0 else 0.4, "clv_net_median": 0.01, "share_beat_raw": 0.6,
                "p_random_share_raw": 0.01, "yield_flat": 0.1, "yield_kelly": 0.1,
            }
            for m, lo in zip(mt.DEVIG_ORDER, lows)
        ]

    good = mt.combine_devig_verdicts(pd.DataFrame(rows([0.01, 0.02, 0.03])))
    assert bool(good["positive_lb"].iloc[0]) and good["lo_min"].iloc[0] == pytest.approx(0.01)
    mixed = mt.combine_devig_verdicts(pd.DataFrame(rows([0.01, -0.02, 0.03])))
    assert not bool(mixed["positive_lb"].iloc[0])  # one de-vig method disagrees
    assert mixed["p_iut"].iloc[0] == pytest.approx(0.4)


def test_cell_plan_covers_every_family_and_never_asks_for_a_residual_over_under() -> None:
    plan = mt.cell_plan()
    families = {family for family, _ in plan}
    assert {"headline", "z0", "control", "books", "polish", "split", "seasons_all"} <= families
    headline = [c for f, c in plan if f == "headline"]
    assert len(headline) == 12 and {c.book for c in headline} == {"bet365"} and {c.z for c in headline} == {mt.DEFAULT_Z}
    assert not [c for _, c in plan if c.strategy == "residual" and c.market_set == "over_under_2.5"]
    with pytest.raises(ValueError):
        mt.CellSpec(strategy="oracle")
    with pytest.raises(ValueError):
        mt.CellSpec(z=-1.0)


# --------------------------------------------------------------------------- #
# Forecast quality against the market and the live margins
# --------------------------------------------------------------------------- #


def test_model_vs_market_is_positive_when_the_model_is_better(odds_frame) -> None:
    panel = mt.build_market_panel(odds_frame, SPEC_1X2)
    y = panel.y.astype(int)
    truth = np.full((len(panel), 3), 0.1)
    truth[np.arange(len(y)), y] = 0.8  # an oracle: strictly better than any market line
    mask = np.isfinite(panel.fair[("ps_open", "power")]).all(axis=1) & np.isfinite(panel.fair[("ps_close", "power")]).all(axis=1)
    table = mt.model_vs_market(
        panel, {"oracle": truth, "flat": np.full((len(panel), 3), 1 / 3)}, mask=mask, sample="t", methods=("power",), n_resamples=200
    )
    pooled = table[table["league_code"] == mt.POOLED_LABEL].set_index(["model", "reference"])
    assert pooled.loc[("oracle", "open"), "mean_diff"] > 0.2
    assert pooled.loc[("flat", "open"), "mean_diff"] < 0.0  # the market beats a flat forecast
    assert (pooled["market_loss"] < 1.1).all()
    assert set(table["league_code"]) >= {"L1", "L2", mt.POOLED_LABEL}


def test_live_margins_measure_the_overround_next_to_pinnacle(tmp_path) -> None:
    path = tmp_path / "odds.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE quotes (bookmaker_id INTEGER, bookmaker TEXT)")
        connection.execute(
            "CREATE TABLE quote_state (fixture_id INTEGER, bookmaker_id INTEGER, bet_id INTEGER, selection TEXT, odd REAL)"
        )
        connection.executemany("INSERT INTO quotes VALUES (?, ?)", [(4, "Pinnacle"), (8, "Bet365")])
        books = {4: (2.0, 4.0, 4.0), 8: (1.9, 3.8, 3.8)}
        for book, prices in books.items():
            for label, price in zip(("Home", "Draw", "Away"), prices):
                connection.execute("INSERT INTO quote_state VALUES (1, ?, 1, ?, ?)", (book, label, price))
        connection.execute("INSERT INTO quote_state VALUES (1, 8, 5, 'Over 2.5', 1.9)")  # incomplete book: ignored
        connection.commit()
    table = mt.measure_live_margins(path)
    row = table[(table["market"] == "1x2") & (table["bookmaker"] == "Bet365")].iloc[0]
    assert row["mean_overround"] == pytest.approx(1 / 1.9 + 2 / 3.8 - 1.0)
    assert row["gap_to_pinnacle_pp"] == pytest.approx(100.0 * (1 / 1.9 + 2 / 3.8 - 1.0))  # Pinnacle's overround is exactly 0 here
    assert not ((table["market"] == "over_under_2.5")).any()


def test_evaluate_cell_runs_end_to_end_for_every_regime_and_structure(legs) -> None:
    rows, stored = mt.evaluate_cell(legs, _cell(), n_resamples=200, n_replicates=20, tags={"family": "test"})
    frame = pd.DataFrame(rows)
    assert len(frame) == 3 * mt.MAX_LEGS and set(frame["regime"]) == set(mt.TAX_REGIMES)
    assert (frame["family"] == "test").all()
    tax_free = frame[(frame["regime"] == "tau_1.00") & (frame["n_legs"] == 1)].iloc[0]
    assert tax_free["n_bets"] > 20 and tax_free["interval_ok"]
    assert tax_free["clv_net_lo"] <= tax_free["clv_net_mean"] <= tax_free["clv_net_hi"]
    assert stored and {"clv_net", "regime", "strategy"} <= set(stored[0].columns)
    heavy = frame[(frame["regime"] == "tau_0.88") & (frame["n_legs"] == 1)].iloc[0]
    assert heavy["n_bets"] < tax_free["n_bets"]
