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
