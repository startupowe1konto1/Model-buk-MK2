"""scripts/mk2 statistics model: NB probabilities, the team-effects fit, market alignment, picks, settlement."""

from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "mk2"))

import stats_daily as sd  # noqa: E402
import stats_model as sm  # noqa: E402
import stats_picks as sp  # noqa: E402


def test_nb_over_matches_scipy_and_handles_whole_lines():
    mu, alpha = 12.3, 0.05
    r = 1 / alpha
    dist = stats.nbinom(r, r / (r + mu))
    over, push, under = sm.nb_over(mu, alpha, 12.5)
    assert push == 0 and over + under == pytest.approx(1.0)
    assert over == pytest.approx(dist.sf(12))
    over, push, under = sm.nb_over(mu, alpha, 12.0)
    assert push == pytest.approx(dist.pmf(12))
    assert over == pytest.approx(dist.sf(12))
    assert over + push + under == pytest.approx(1.0)


def test_missing_shots_stay_missing_but_empty_cards_are_zero():
    df = pd.DataFrame([
        # full block, no cards or offsides reported -> zeros
        {"total_shots": 12, "shots_on_goal": 4, "corners": 5, "fouls": 10, "yellow_cards": None, "red_cards": None, "offsides": None},
        # cup qualifier: corners known, shots unknown -> shots, fouls stay missing; cards zero; offsides unknown
        {"total_shots": None, "shots_on_goal": None, "corners": 3, "fouls": None, "yellow_cards": 2, "red_cards": None, "offsides": None},
        # no statistics block at all -> nothing filled
        {"total_shots": None, "shots_on_goal": None, "corners": None, "fouls": None, "yellow_cards": None, "red_cards": None, "offsides": None},
    ], dtype=float)
    out = sm.fill_known_zeros(df)
    assert out.loc[0, ["yellow_cards", "red_cards", "offsides", "cards"]].tolist() == [0, 0, 0, 0]
    assert out.loc[1, ["total_shots", "shots_on_goal", "fouls", "offsides"]].isna().all()
    assert out.loc[1, "cards"] == 2
    assert out.loc[2].isna().all()


def synthetic_rows(seed: int = 7) -> pd.DataFrame:
    """Ten teams, double round robin over two seasons; team 0 shoots most, team 9 concedes most."""
    rng = np.random.default_rng(seed)
    att = np.linspace(1.5, 0.7, 10)
    deff = np.linspace(0.8, 1.4, 10)
    rows, fid = [], 0
    start = pd.Timestamp("2024-08-01", tz="UTC")
    for season in (2024, 2025):
        day = 0
        for h in range(10):
            for a in range(10):
                if h == a:
                    continue
                fid += 1
                day += 1
                kick = start + pd.Timedelta(days=day + (365 if season == 2025 else 0))
                for team, opp, home in ((h, a, 1.0), (a, h, 0.0)):
                    lam = 12.0 * att[team] * deff[opp] * (1.2 if home else 1.0)
                    rows.append({"fixture_id": fid, "league_id": 1, "season": season, "kickoff": kick,
                                 "team_id": team, "opp_id": opp, "home": home, "cup": 0.0,
                                 "side": "home" if home else "away", "total_shots": rng.poisson(lam)})
    return pd.DataFrame(rows)


def test_fit_stat_recovers_team_strength_and_home_advantage():
    df = synthetic_rows()
    fit = sm.fit_stat(df, "shots", pd.Timestamp("2027-01-01", tz="UTC"), half_life_days=10_000, ridge_team=1.0)
    assert math.exp(fit.b_home) == pytest.approx(1.2, abs=0.05)
    assert fit.mean(0, 9, True, False) > 2.5 * fit.mean(9, 0, True, False)
    att = np.linspace(1.5, 0.7, 10)
    deff = np.linspace(0.8, 1.4, 10)
    errors = [abs(fit.mean(h, a, True, False) / (12.0 * att[h] * deff[a] * 1.2) - 1)
              for h in range(10) for a in range(10) if h != a]
    assert np.median(errors) < 0.05 and max(errors) < 0.15
    assert fit.alpha < 0.02  # the data are Poisson


def analysis_with_market(k_market: float) -> dict:
    """Analysis whose market expects k_market times the model's counts on every line."""
    alpha = 0.05
    mecze = []
    for i, mu in enumerate((10.0, 12.0, 14.0, 16.0)):
        lines = []
        for line in (mu - 2.5, mu - 0.5, mu + 1.5):
            q = sm.nb_over(mu * k_market, alpha, line)[0]
            p = sm.nb_over(mu, alpha, line)
            lines.append({"stat": "shots", "scope": "home", "line": line, "p_over": p[0], "p_under": p[2], "rynek_over": q,
                          "kursy_over": {"A": round(0.95 / q, 2)}, "kursy_under": {"A": round(0.95 / (1 - q), 2)}})
        mecze.append({"fixture_id": i, "gospodarz": f"H{i}", "gosc": f"A{i}",
                      "srednie": {"shots": {"home": mu, "away": 10.0, "total": mu + 10.0}}, "linie": lines})
    return {"mecze": mecze, "model": {"shots": {"alpha": alpha, "alpha_total": 0.01}}}


def test_level_factors_find_the_market_level():
    assert sp.level_factors(analysis_with_market(1.0))[("shots", "home")] == pytest.approx(1.0, abs=0.011)
    assert sp.level_factors(analysis_with_market(1.1))[("shots", "home")] == pytest.approx(1.1, abs=0.011)


def test_candidates_ignore_outlier_prices_and_flag_tail_lines():
    a = analysis_with_market(1.0)
    line = a["mecze"][0]["linie"][1]
    line["kursy_over"] = {"A": 1.90, "B": 1.92, "C": 2.60}  # C is far above the others
    rows = sp.candidates(a, {("shots", "home"): 1.0})
    r = next(x for x in rows if x["fixture_id"] == 0 and x["line"] == line["line"] and x["side"] == "over")
    assert r["kurs"] == 1.92 and r["bukmacher"] == "B"
    tail = next(x for x in rows if x["fixture_id"] == 0 and x["line"] == a["mecze"][0]["linie"][2]["line"]
                and x["side"] == "over")
    assert (0.30 <= tail["p_rynek"] <= 0.80 and tail["kurs"] <= sp.MAX_PICK_ODDS) == tail["do_typow"]


def test_candidates_follow_the_users_rules():
    """Only "over" bets, only X.5 lines, and a pick priced at most about 1.8."""
    a = analysis_with_market(1.0)
    m = a["mecze"][0]
    m["linie"] += [{**m["linie"][1], "line": 9.0}, {**m["linie"][1], "line": 9.25}]  # whole and quarter lines
    rows = sp.candidates(a, {("shots", "home"): 1.0})
    assert rows and all(r["side"] == "over" and sp.is_half_line(r["line"]) for r in rows)
    assert all(not r["do_typow"] for r in rows if r["kurs"] > sp.MAX_PICK_ODDS)
    assert any(r["do_typow"] for r in rows)


def test_descriptions_say_at_least_n_in_polish():
    m = {"gospodarz": "Aston Villa", "gosc": "Fenerbahçe"}
    assert sp.describe(m, "shots", "home", "over", 13.5) == "Aston Villa: co najmniej 14 strzałów"
    assert sp.describe(m, "corners", "total", "over", 9.5) == "W meczu: co najmniej 10 rzutów rożnych"
    assert sp.describe(m, "sot", "away", "over", 3.5) == "Fenerbahçe: co najmniej 4 celne strzały"
    assert sp.describe(m, "corners", "away", "over", 0.5) == "Fenerbahçe: co najmniej 1 rzut rożny"
    assert sp.plural(22, "shots") == "strzały" and sp.plural(12, "shots") == "strzałów"


def test_coupons_take_legs_from_different_matches_within_the_odds_band():
    picks = [{"fixture_id": f, "opis": f"leg{f}{s}", "kurs": o, "bukmacher": "A", "p_model": p, "ev": p * o - 1}
             for f, s, o, p in ((1, "a", 1.40, 0.75), (1, "b", 1.45, 0.72), (2, "a", 1.42, 0.74), (3, "a", 3.0, 0.4))]
    out = sp.coupons(picks, 2)
    assert out and all(1.8 <= c["kurs"] <= 2.5 for c in out)
    assert not any("leg1a" in " ".join(c["nogi"]) and "leg1b" in " ".join(c["nogi"]) for c in out)


def picks_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""create table picks (made_at text, day text, fixture_id integer, stat text, scope text, line real,
        side text, p_model real, p_market real, odd real, bookmaker text, ev real, ev_tax real, rank integer,
        primary key (day, fixture_id, stat, scope, line, side))""")
    return con


def test_settle_picks_win_loss_and_push():
    con = picks_db()
    for rank, (scope, line, side) in enumerate((("total", 25.5, "under"), ("home", 13.5, "over"), ("away", 12.0, "over")), 1):
        con.execute("insert into picks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("2026-10-14T18:00:00Z", "2026-10-14", 99, "shots", scope, line, side, 0.5, 0.45, 2.0, "X", 0.0, 0.0, rank))
    df = pd.DataFrame([
        {"fixture_id": 99, "side": "home", "total_shots": 14},
        {"fixture_id": 99, "side": "away", "total_shots": 12},
    ])
    res = {p["scope"]: p for p in sd.settle_picks(con, df)}
    assert res["total"]["wynik"] == "przegrana" and res["total"]["wartosc"] == 26  # 26 shots, under 25.5 loses
    assert res["home"]["wynik"] == "wygrana" and res["home"]["zysk"] == pytest.approx(1.0)
    assert res["home"]["zysk_podatek"] == pytest.approx(2.0 * 0.88 - 1)
    assert res["away"]["wynik"] == "zwrot" and res["away"]["zysk"] == 0.0


def coupons_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""create table coupons (made_at text, day text, kind text, legs text, odd real, p_model real,
        p_market real, ev real, ev_tax real, why text, primary key (day, kind))""")
    return con


def test_settle_coupons_needs_every_leg_and_handles_a_push():
    import json
    con = coupons_db()
    legs = [{"fixture_id": 1, "stat": "corners", "scope": "away", "line": 5.5, "side": "under", "kurs": 1.8},
            {"fixture_id": 2, "stat": "corners", "scope": "total", "line": 10.0, "side": "under", "kurs": 1.3}]
    con.execute("insert into coupons values (?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-14T18:00:00Z", "2026-10-14", "2", json.dumps(legs), 2.34, 0.55, 0.41, 0.3, 0.15, "x"))
    rows = lambda h1, a1, h2, a2: pd.DataFrame([  # noqa: E731
        {"fixture_id": 1, "side": "home", "corners": h1}, {"fixture_id": 1, "side": "away", "corners": a1},
        {"fixture_id": 2, "side": "home", "corners": h2}, {"fixture_id": 2, "side": "away", "corners": a2}])
    won = sd.settle_coupons(con, rows(6, 4, 3, 5))[0]          # 4 < 5.5 and 8 < 10
    assert won["wynik"] == "wygrana" and won["zysk"] == pytest.approx(1.8 * 1.3 - 1, abs=1e-3)
    pushed = sd.settle_coupons(con, rows(6, 4, 5, 5))[0]       # second leg exactly 10: returned, odds 1.8 remain
    assert pushed["wynik"] == "wygrana" and pushed["zysk"] == pytest.approx(0.8)
    lost = sd.settle_coupons(con, rows(6, 7, None, None))[0]   # first leg lost, second unknown: lost anyway
    assert lost["wynik"] == "przegrana" and lost["zysk_podatek"] == -1.0
    waiting = sd.settle_coupons(con, rows(6, 4, None, None))[0]  # one leg won, one unknown: wait
    assert waiting["wynik"] is None


def test_rank_phrase_reads_from_the_nearer_end():
    assert sd.rank_phrase(2, 20) == "2. najwięcej w lidze"
    assert sd.rank_phrase(19, 20) == "2. najmniej w lidze"


def test_settle_picks_waits_for_statistics():
    con = picks_db()
    con.execute("insert into picks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-14T18:00:00Z", "2026-10-14", 5, "corners", "total", 9.5, "over", 0.5, 0.45, 2.0, "X", 0.0, 0.0, 1))
    out = sd.settle_picks(con, pd.DataFrame(columns=["fixture_id", "side", "corners"]))
    assert out[0]["wynik"] is None and "zysk" not in out[0]


# ---------------------------------------------------------------------------- near-certain events and builders

import stats_sure as ss  # noqa: E402


def test_corrected_chance_and_highest_threshold():
    assert ss.corrected(0.90) == pytest.approx(0.89)
    assert ss.corrected(0.40) == 0.40
    k, p = ss.highest_k(14.0, 0.04, 0.85)
    r = 1 / 0.04
    raw = stats.nbinom.sf(k - 1, r, r / (r + 14.0))
    assert p == pytest.approx(ss.corrected(raw)) and p >= 0.85
    assert ss.corrected(stats.nbinom.sf(k, r, r / (r + 14.0))) < 0.85   # one more would be below the level


def test_sure_coupon_takes_fair_legs_from_different_matches():
    def ev(fid, p, odd, q=None):
        return {"fixture_id": fid, "mecz": f"M{fid}", "stat": "corners", "scope": "total", "line": 7.5, "side": "over",
                "opis": f"leg{fid}", "zaklad": "powyżej 7,5", "kurs": odd, "bukmacher": "A", "p_model": p,
                "p_rynek": q if q is not None else 1 / odd - 0.02, "p_razy_kurs": round(p * odd, 4)}
    events = [ev(1, 0.82, 1.30), ev(1, 0.85, 1.25), ev(2, 0.80, 1.32), ev(3, 0.81, 1.30), ev(4, 0.83, 1.28),
              ev(5, 0.70, 1.30), ev(6, 0.95, 1.40, q=0.70), ev(7, 0.97, 1.10)]
    # 5 is below the fair price, 6 too far from the market, 7 below the user's minimum odds of 1.25
    k = ss.sure_coupon(events)
    assert k is not None and 1.8 <= k["kurs"] <= 2.6 and k["ev_podatek"] > 0
    fids = [l["fixture_id"] for l in k["legs"]]
    assert len(fids) == len(set(fids)) and not {5, 6, 7} & set(fids)


def test_odds_bands_cover_the_users_range():
    assert [sp.band_of(o) for o in (1.24, 1.25, 1.45, 1.46, 1.65, 1.66, 2.0, 2.01, None)] ==         [None, 0, 0, 1, 1, 2, 2, None, None]


def test_goal_means_reproduce_the_market_winner_chances():
    lh, la = ss.goals_from_market(1.5, 1.1, 0.62, 0.15)
    k = np.arange(16)
    j = np.outer(stats.poisson.pmf(k, lh), stats.poisson.pmf(k, la))
    assert np.tril(j, -1).sum() == pytest.approx(0.62, abs=2e-3)
    assert np.triu(j, 1).sum() == pytest.approx(0.15, abs=2e-3)


def test_builder_chance_is_above_the_product_for_related_legs():
    dims, corr = ss.load_corr()
    m = {"fixture_id": 7, "gospodarz": "H", "gosc": "A", "start": "2026-10-20T19:00:00+00:00"}
    means = {"home_shots": 17.0, "home_sot": 6.0, "home_corners": 6.5, "home_goals": 2.0,
             "away_shots": 9.0, "away_sot": 3.0, "away_corners": 3.8, "away_goals": 0.9}
    alphas = {d: (1e-4 if d.endswith("goals") else 0.04) for d in dims}
    out = ss.builders_for_match(m, means, alphas, dims, corr)
    scm = next(b for b in out if b["scope"] == "home" and b["szablon"] == "SCM")
    assert scm["p_model"] > scm["p_niezalezne"]
    assert [l["side"] for l in scm["legs"]] == ["over", "over", "more"]


def test_settle_leg_more_than_the_opponent_and_goals():
    by_fix = {9: pd.DataFrame([{"fixture_id": 9, "side": "home", "total_shots": 14, "goals": 2.0},
                               {"fixture_id": 9, "side": "away", "total_shots": 14, "goals": 1.0}])}
    assert sd.settle_leg({"fixture_id": 9, "stat": "shots", "scope": "home", "line": None, "side": "more"}, by_fix)[0] == "przegrana"
    assert sd.settle_leg({"fixture_id": 9, "stat": "goals", "scope": "home", "line": None, "side": "more"}, by_fix)[0] == "wygrana"
    assert sd.settle_leg({"fixture_id": 9, "stat": "goals", "scope": "away", "line": 0.5, "side": "over"}, by_fix)[0] == "wygrana"


def test_result_odds_become_margin_free_chances():
    payload = {"response": [{"bookmakers": [
        {"name": "A", "bets": [{"id": 1, "values": [{"value": "Home", "odd": "1.50"}, {"value": "Draw", "odd": "4.00"},
                                                     {"value": "Away", "odd": "6.50"}]}]},
        {"name": "B", "bets": [{"id": 1, "values": [{"value": "Home", "odd": "1.55"}, {"value": "Draw", "odd": "4.20"},
                                                     {"value": "Away", "odd": "6.00"}]}]}]}]}
    rows = sd.parse_result_odds(payload)
    assert len(rows) == 6 and {r["side"] for r in rows} == {"home", "draw", "away"}
    h, d, a = sd.result_probs(rows)
    assert h + d + a == pytest.approx(1.0) and 0.6 < h < 0.65 and a < d


def test_double_chance_odds_are_read_and_match_result_bets_settle_after_90_minutes():
    payload = {"response": [{"bookmakers": [{"name": "A", "bets": [
        {"id": 12, "values": [{"value": "Home/Draw", "odd": "1.30"}, {"value": "Home/Away", "odd": "1.25"},
                              {"value": "Draw/Away", "odd": "2.40"}]}]}]}]}
    assert {r["side"] for r in sd.parse_result_odds(payload)} == {"home_draw", "draw_away"}
    by_fix = {9: pd.DataFrame([{"fixture_id": 9, "side": "home", "goals": 1.0},
                               {"fixture_id": 9, "side": "away", "goals": 1.0}])}
    leg = {"fixture_id": 9, "stat": "wynik", "line": 0.0}
    assert sd.settle_leg({**leg, "scope": "home", "side": "win"}, by_fix) == ("przegrana", "1:1")
    assert sd.settle_leg({**leg, "scope": "away", "side": "win_draw"}, by_fix) == ("wygrana", "1:1")
    m = {"gospodarz": "Bayern", "gosc": "Inter"}
    assert sp.describe(m, "wynik", "away", "win_draw", 0.0) == "Inter: wygra albo zremisuje"
    assert sp.bookie_wording("wynik", "away", "win_draw", 0.0) == "X2"


def test_one_bet_per_market_in_a_match():
    a = {"fixture_id": 1, "stat": "corners", "scope": "total"}
    assert sp.market_key(a) == sp.market_key({**a, "line": 9.5})
    assert sp.market_key(a) != sp.market_key({**a, "scope": "home"})
    assert sp.market_key({"fixture_id": 1, "stat": "wynik", "scope": "home"}) == sp.market_key(
        {"fixture_id": 1, "stat": "wynik", "scope": "away"})


def test_v5_chance_is_mostly_the_market_and_picks_need_a_price_above_it():
    v = sp.value_fields(0.80, 0.60, 1.80, 3)
    assert v["p_model"] == pytest.approx(0.64) and v["p_model_czysty"] == 0.8
    assert v["przewaga_kursu"] == pytest.approx(0.08)
    assert v["ev_podatek"] == pytest.approx(0.64 * 1.8 * 0.88 - 1, abs=1e-4)
    assert sd.strict_pick(v) and not sd.strict_pick({**v, "n_buk": 1})
    assert not sd.strict_pick(sp.value_fields(0.90, 0.60, 1.65, 3))   # the model alone is not enough


def test_ready_coupons_take_value_first_then_the_likeliest():
    legs = [{"fixture_id": f, "opis": f"leg{f}", "kurs": o, "bukmacher": "A", "p_model": p, "p_rynek": p}
            for f, o, p in ((1, 1.55, 0.75), (2, 1.40, 0.72), (3, 1.30, 0.80), (4, 1.40, 0.78))]
    out = sp.ready_coupons(legs, "2")
    assert all(1.8 <= k["kurs"] <= 2.5 and len(k["legs"]) == 2 for k in out)
    assert {l["fixture_id"] for l in out[0]["legs"]} == {1, 3} and out[0]["z_wartoscia"]
    assert any(not k["z_wartoscia"] and k["p_model"] > out[0]["p_model"] for k in out)   # likelier, but no value
    four = sp.ready_coupons(legs, "4")
    assert four and all(3.8 <= k["kurs"] <= 5.0 and len(k["legs"]) in (3, 4) for k in four)


def test_miss_analysis_counts_near_misses_and_explains_no_value_losses():
    def bet(won: bool, came: int) -> dict:
        return {"stat": "corners", "scope": "total", "side": "over", "line": 9.5, "kurs": 1.5, "p_model": 0.6,
                "p_rynek": 0.62, "p_model_czysty": 0.55, "wynik": "wygrana" if won else "przegrana", "wartosc": came,
                "z_wartoscia": False, "ev_podatek": 0.6 * 1.5 * 0.88 - 1, "zysk_zl": 32.0 if won else -100.0}
    a = sd.miss_analysis([bet(i % 5 < 3, 11 if i % 5 < 3 else 9 if i % 5 == 3 else 6) for i in range(40)])
    assert a["n"] == 40 and a["trafione"] == 24
    assert a["zabraklo"] == {"n": 16, "1": 8, "2": 0, "3+": 8, "wynik": 0}
    assert a["wartosc"]["bez_wartosci"]["potrzebna"] == pytest.approx(1 / (0.88 * 1.5), abs=1e-4)
    assert any("bez wartości" in w for w in a["wnioski"]) and a["prognozy"]["n"] == 40
