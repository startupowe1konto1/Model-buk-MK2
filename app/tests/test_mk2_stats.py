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
import stats_market as mk  # noqa: E402
import stats_players as spl  # noqa: E402


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
             "away_shots": 9.0, "away_sot": 3.0, "away_corners": 3.8, "away_goals": 0.9,
             "home_fouls": 11.0, "home_cards": 1.8, "away_fouls": 12.5, "away_cards": 2.3}
    alphas = {d: (1e-4 if d.endswith("goals") else 0.04) for d in dims}
    out = ss.builders_for_match(m, means, alphas, dims, corr)
    scm = next(b for b in out if b["scope"] == "home" and b["szablon"] == "S+C+M" and b["poziom"] == "pewniej")
    assert scm["p_model"] > scm["p_niezalezne"]
    assert [l["side"] for l in scm["legs"]] == ["over", "over", "more"]
    bold = next(b for b in out if b["scope"] == "home" and b["szablon"] == "S+C+M" and b["poziom"] == "wyzej")
    assert bold["legs"][0]["line"] > scm["legs"][0]["line"] and bold["p_model"] < scm["p_model"]
    whole = next(b for b in out if b["scope"] == "total" and b["szablon"] == "TS+TC+B")
    assert whole["druzyna"] == "Cały mecz" and [l["side"] for l in whole["legs"]] == ["over", "over", "btts"]
    nd = next(b for b in out if b["scope"] == "away" and b["szablon"] == "S+M+ND")
    assert nd["legs"][-1]["side"] == "win_draw" and nd["p_model"] < 0.5   # the weaker team rarely avoids defeat here


def test_pick_builders_keeps_the_likeliest_of_each_kind_and_level():
    def c(fid, tpl, poziom, p):
        return {"fixture_id": fid, "day": "2026-10-27", "szablon": tpl, "poziom": poziom, "p_model": p, "p_niezalezne": p - 0.05,
                "kurs_min_z_podatkiem": round(1 / (p * 0.88), 2),
                "legs": [{"stat": code, "scope": poziom, "side": "over"} for code in tpl.split("+")]}   # distinct events per template and level
    cands = [c(1, "S+C+M", "pewniej", 0.78), c(1, "S+C+G", "pewniej", 0.74), c(1, "S+C+M", "wyzej", 0.55),
             c(1, "S+C+W", "pewniej", 0.60), c(1, "TS+TC", "pewniej", 0.85), c(2, "S+C+M", "wyzej", 0.40)]
    # the same match and kind keeps only the likeliest per level; 0.85 and 0.40 are outside the band
    out = ss.pick_builders(cands)["2026-10-27"]
    assert [(o["szablon"], o["poziom"]) for o in out] == [("S+C+M", "pewniej"), ("S+C+W", "pewniej"), ("S+C+M", "wyzej")]
    assert "wyższymi progami" in out[-1]["why"] and "wyższymi progami" not in out[0]["why"]


def test_deshrink_scales_the_team_effects_only():
    fit = sm.StatFit(stat="sot", b0=1.0, b_home=0.1, b_cup=0.0, b_season=0.0, league_for={1: 0.05}, league_against={1: -0.02},
                     team_for={1: 0.2, 2: -0.1}, team_against={3: -0.1}, alpha=0.03, alpha_total=0.01, n_rows=10)
    fit.deshrink(1.5, 2.0)
    assert fit.team_for == {1: pytest.approx(0.3), 2: pytest.approx(-0.15)} and fit.team_against == {3: pytest.approx(-0.2)}
    assert fit.league_for == {1: 0.05} and fit.b_home == 0.1


def test_builder_analysis_names_the_failed_legs_and_reasons():
    def row(day, won, legs, rodzaj="bezpieczny", poziom="pewniej", p=0.7):
        return {"day": day, "start": day + "T18:00:00+00:00", "wynik": won, "p_model": p, "legs": legs,
                "payload": {"rodzaj": rodzaj, "poziom": poziom, "mecz": "A – B", "druzyna": "A", "kurs_min_z_podatkiem": round(1 / (p * 0.88), 2)}}
    leg = lambda stat, side, line, wynik, wartosc, scope="home", p=0.85: {"stat": stat, "side": side, "line": line, "scope": scope,  # noqa: E731
                                                                            "opis": f"A: {stat} {line}", "wynik": wynik, "wartosc": wartosc, "p_model": p}
    rows = [row("2026-10-20", "wygrana", [leg("shots", "over", 11.5, "wygrana", 14), leg("goals", "over", 0.5, "wygrana", 2)]),
            row("2026-10-21", "przegrana", [leg("shots", "over", 11.5, "przegrana", 10), leg("goals", "over", 0.5, "wygrana", 1)]),
            row("2026-10-22", "przegrana", [leg("shots", "over", 11.5, "wygrana", 15), leg("goals", "more", None, "przegrana", 0)], "z_wynikiem"),
            row("2026-10-23", "przegrana", [leg("shots", "over", 11.5, "przegrana", 6)], "bezpieczny", "wyzej", 0.5)]
    a = sd.builder_analysis_of(rows)
    assert a["n"] == 4 and a["trafione"] == 1
    assert {g["przyczyna"]: g["n"] for g in a["przyczyny"]} == {"o włos (zabrakło 1–2)": 1, "wynik meczu": 1, "daleko (zabrakło 3+)": 1}
    lost = a["przegrane"]
    assert lost[0]["dzien"] == "2026-10-23" and lost[0]["nogi"][0]["zabraklo"] == 6 and lost[0]["poziom"] == "wyzej"
    assert [g["typ"] for g in a["nogi"]][0] == "strzały" and next(g for g in a["nogi"] if g["typ"] == "wygrana")["trafione"] == 0
    assert [(g["rodzaj"], g["poziom"], g["n"]) for g in a["grupy"]] == [("bezpieczny", "pewniej", 2), ("z_wynikiem", "pewniej", 1), ("bezpieczny", "wyzej", 1)]


def test_fixture_row_matches_the_fixtures_table():
    f = {"fixture": {"id": 7, "date": "2026-10-27T17:30:00+00:00", "status": {"short": "FT"}, "referee": "R", "venue": {"name": "V"}},
         "league": {"id": 135, "season": 2026, "round": "Regular Season - 9"},
         "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2, "name": "A"}}, "goals": {"home": 2, "away": 6}}
    row = sd.fixture_row(f, "2026-10-29T14:00:00Z")
    assert len(row) == 15 and row[:2] == (7, 135) and row[5] == "FT" and row[11:13] == (2, 6) and row[-1] == "2026-10-29T14:00:00Z"


def test_pick_builders_limits_repeated_templates():
    def c(fid, tpl, p):
        return {"fixture_id": fid, "day": "2026-10-30", "szablon": tpl, "poziom": "pewniej", "p_model": p, "p_niezalezne": p - 0.05,
                "kurs_min_z_podatkiem": round(1 / (p * 0.88), 2), "legs": [{}, {}, {}]}
    cands = [c(1, "S+C+M", 0.78), c(2, "S+C+M", 0.76), c(3, "S+C+M", 0.74), c(4, "S+C+M", 0.72), c(5, "S+C+G", 0.70), c(6, "S+R", 0.69)]
    out = ss.pick_builders(cands)["2026-10-30"]
    assert [o["szablon"] for o in out if o["rodzaj"] == "bezpieczny"] == ["S+C+M", "S+C+M", "S+C+G"]
    assert [o["szablon"] for o in out if o["rodzaj"] == "mieszany"] == ["S+R"]


def test_market_means_follow_the_bookmakers_lines_and_split():
    dims, corr = ss.load_corr()
    model = {d: v for d, v in zip(dims, (14.0, 4.8, 5.0, 1.4, 11.0, 1.9, 12.0, 4.2, 4.6, 1.1, 12.0, 2.1))}
    alphas = {d: (1e-4 if d.endswith("goals") else 0.04) for d in dims}
    alphas.update({f"total_{s}": 0.01 for s in ("shots", "sot", "corners", "fouls", "cards")})
    rows = []
    for line, o, u in ((28.5, 1.80, 1.95), (29.5, 2.05, 1.72)):        # match shots: the market expects ~29
        rows += [{"stat": "shots", "scope": "total", "line": line, "side": "over", "bookmaker": "B", "odd": o},
                 {"stat": "shots", "scope": "total", "line": line, "side": "under", "bookmaker": "B", "odd": u}]
    rows += [{"stat": "more_shots", "scope": "match", "line": 0.0, "side": s, "bookmaker": "B", "odd": o}
             for s, o in (("home", 1.25), ("draw", 12.0), ("away", 4.5))]  # and the home side to shoot much more
    rows += [{"stat": "corners", "scope": "home", "line": 6.5, "side": "over", "bookmaker": "B", "odd": 1.9},
             {"stat": "corners", "scope": "home", "line": 6.5, "side": "under", "bookmaker": "B", "odd": 1.9}]
    mkt, src = mk.market_means(model, alphas, rows, dims, corr)
    assert 28.0 < mkt["home_shots"] + mkt["away_shots"] < 30.5 and mkt["home_shots"] / (mkt["home_shots"] + mkt["away_shots"]) > 0.56
    assert src["home_shots"] == "mecz" and src["home_corners"] == "linia" and 6.6 < mkt["home_corners"] < 7.6
    assert src["home_sot"] == "model" and mkt["home_sot"] == 4.8
    assert mk.estimated_price(0.5, 0.3) == 1.7 and mk.estimated_price(0.8, 0.3) == round(1 + 0.25 * 0.7, 2)
    cut, n = mk.calibrate_cut([(1.55, 0.5)])         # 1.55 at a fair 2.0: the bookmaker kept 45% of the net winnings
    assert n == 1 and cut == round((5 * 0.30 + 0.45) / 6, 10)


def test_extra_odds_are_parsed_by_name():
    payload = {"response": [{"bookmakers": [{"name": "B", "bets": [
        {"id": 340, "name": "Shots.1x2", "values": [{"value": "Home", "odd": "1.3"}, {"value": "Draw", "odd": "12"}, {"value": "Away", "odd": "4"}]},
        {"id": 8, "name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.7"}, {"value": "No", "odd": "2.1"}]},
        {"id": 999, "name": "Something Else", "values": [{"value": "Yes", "odd": "1.7"}]}]}]}]}
    rows = mk.parse_extra_odds(payload)
    assert {(r["stat"], r["side"]) for r in rows} == {("more_shots", "home"), ("more_shots", "draw"), ("more_shots", "away"), ("btts", "yes"), ("btts", "no")}


def test_choose_mains_takes_the_likeliest_and_falls_back_when_a_template_is_used_up():
    def c(fid, tpl, p, kind="bezpieczny", est=1.6):
        return {"fixture_id": fid, "szablon": tpl, "p_model": p, "ev_szac": p * 0.88 * est - 1, "rodzaj": kind, "kurs_szac": est}
    lst = [c(1, "S+C+M", 0.70), c(2, "S+C+M", 0.71), c(3, "S+C+M", 0.69), c(3, "S+C+G", 0.66), c(7, "S+C+M", 0.72),
           c(4, "TR+TK", 0.80, "mieszany"), c(4, "TS+TC", 0.74, "meczowy"), c(5, "S+C+M", 0.50),
           c(6, "hS+aS", 0.85, "obie", 1.15), c(6, "hC+aC", 0.62, "obie", 1.7)]
    mains = ss.choose_mains(lst)
    # the likeliest first; 5 is too unlikely, 6's near-certain combo is priced below 1.35, the mixed kind stays a test
    # kind, and S+C+M is used three times, so 3 takes its next likeliest
    assert [(m["fixture_id"], m["szablon"]) for m in mains] == [(4, "TS+TC"), (7, "S+C+M"), (2, "S+C+M"), (1, "S+C+M"),
                                                                (3, "S+C+G"), (6, "hC+aC")]


def test_likely_events_are_the_likeliest_of_each_side():
    dims, corr = ss.load_corr()
    base = {"shots": (16.0, 8.0), "sot": (6.0, 2.5), "corners": (7.0, 3.0), "goals": (2.0, 0.7), "fouls": (11.0, 12.0),
            "cards": (2.0, 2.2), "offsides": (2.0, 1.5)}
    means, alphas = {}, {}
    for d in dims:
        side, stat = d.split("_", 1)
        means[d] = base[stat][0 if side == "home" else 1]
        alphas[d] = 1e-4 if stat == "goals" else 0.05
        alphas[f"total_{stat}"] = 0.03
    m = {"fixture_id": 1, "gospodarz": "H", "gosc": "A", "start": "2026-11-02T18:00:00+00:00"}
    X = ss.simulate(means, alphas, dims, corr, seed=1)
    ev = ss.likely_events(m, means, alphas, X)
    for side, lst in ev.items():
        assert len(lst) <= ss.LIKELY_PER_SIDE
        assert all(ss.LIKELY_MIN_P <= e["p_model"] <= ss.LIKELY_MAX_P for e in lst)
        assert [e["p_model"] for e in lst] == sorted((e["p_model"] for e in lst), reverse=True)
    counts = lambda lst: sum(e["side"] == "over" and e["stat"] not in ("goals", "player_sot") for e in lst)  # noqa: E731
    assert all(counts(lst) <= ss.LIKELY_COUNTS for lst in ev.values())
    assert not any(e["stat"] in ss.LIKELY_SKIP for lst in ev.values() for e in lst)
    # the stronger side's list is not only 'at least k': it does not lose, scores, has more shots...
    assert any(e["side"] in ("more", "win_draw") or e["stat"] == "goals" for e in ev["home"])
    day = ss.day_likely([m], {1: ev})["2026-11-02"]
    assert {e["strona"] for e in day} == {side for side, lst in ev.items() if lst} and all(e["mecz"] == "H – A" for e in day)


def test_player_leg_chance_and_settlement():
    q = spl.at_least_one(0.25, 8.0, 10)
    assert q[0] == 0 and 0 < q[1] < q[5] < q[10] < 1 and abs(q[1] - 0.25) < 1e-9
    rows = pd.DataFrame({"fixture_id": [1, 1], "side": ["home", "away"], "goals": [1.0, 0.0], "total_shots": [12.0, 5.0], "shots_on_goal": [5.0, 1.0]})
    by_fix = {1: rows}
    sd.PLAYERS.clear()
    sd.PLAYERS.update({(1, 10): (90.0, 4.0, 2.0), (1, 11): (0.0, 0.0, 0.0)})
    leg = {"fixture_id": 1, "stat": "player_sot", "scope": "home", "line": 0.5, "side": "over", "player_id": 10}
    assert sd.settle_leg(leg, by_fix) == ("wygrana", 2.0)
    assert sd.settle_leg(dict(leg, player_id=11), by_fix)[0] == "zwrot"     # did not play
    assert sd.settle_leg(dict(leg, player_id=12), by_fix)[0] == "zwrot"     # not in the squad
    assert ss.kind_of("hP1+hS") == "zawodnik" and ss.kind_of("hP1+aP1") == "zawodnik" and ss.kind_of("aP1+TS") == "zawodnik"


def test_prune_drops_cheap_and_duplicate_tests():
    def c(fid, p, est, legs, main=False):
        return {"fixture_id": fid, "p_model": p, "kurs_szac": est, "glowny": main,
                "legs": [{"stat": s, "scope": sc, "side": "over", "line": ln} for s, sc, ln in legs]}
    main = c(1, 0.6, 1.5, [("shots", "total", 23.5), ("sot", "total", 7.5)], True)
    same = c(1, 0.75, 1.36, [("shots", "total", 21.5), ("sot", "total", 5.5)])      # the main's events, lower thresholds
    cheap = c(1, 0.8, 1.13, [("shots", "home", 11.5)])                              # below 1.35
    other = [c(1, 0.7 - i / 100, 1.4, [("corners", "total", 7.5 + i)]) for i in range(1)] + [
        c(1, 0.65, 1.45, [("shots", "home", 12.5), ("sot", "home", 3.5)]), c(1, 0.64, 1.5, [("shots", "away", 9.5)]),
        c(1, 0.63, 1.5, [("sot", "away", 3.5)])]
    out = ss.prune([main, same, cheap] + other)
    assert out[0] is main and same not in out and cheap not in out
    assert len([x for x in out if not x["glowny"]]) == 3          # at most three tests a match


def test_settle_leg_both_score_and_double_chance():
    rows = pd.DataFrame({"fixture_id": [1, 1], "side": ["home", "away"], "goals": [1.0, 1.0], "total_shots": [10.0, 8.0]})
    by_fix = {1: rows}
    assert sd.settle_leg({"fixture_id": 1, "stat": "goals", "scope": "total", "side": "btts"}, by_fix) == ("wygrana", "1:1")
    assert sd.settle_leg({"fixture_id": 1, "stat": "goals", "scope": "away", "side": "win_draw"}, by_fix) == ("wygrana", "1:1")
    rows.loc[1, "goals"] = 0.0
    assert sd.settle_leg({"fixture_id": 1, "stat": "goals", "scope": "total", "side": "btts"}, by_fix)[0] == "przegrana"
    assert sd.settle_leg({"fixture_id": 1, "stat": "goals", "scope": "away", "side": "win_draw"}, by_fix)[0] == "przegrana"


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


import datetime as dt  # noqa: E402
import json  # noqa: E402
import stats_prematch as pm  # noqa: E402


def test_injury_reasons_read_in_polish():
    assert sd.injury_pl("Knee Injury") == "uraz kolana" and sd.injury_pl("Yellow Cards") == "pauza za kartki"
    assert sd.injury_pl("Elbow Injury") == "kontuzja" and sd.injury_pl("Broken arm") == "złamanie"
    assert sd.injury_pl(None) == "nieobecność"


def test_absences_put_the_regulars_first_and_mark_doubtful_ones():
    mecze = [{"fixture_id": 1, "home_id": 10, "away_id": 20}]
    absent = {1: {100: {"team_id": 10, "player": "A", "type": "Missing Fixture", "reason": "Red Card"},
                  101: {"team_id": 10, "player": "B", "type": "Questionable", "reason": "Knee Injury"},
                  200: {"team_id": 20, "player": "C", "type": "Missing Fixture", "reason": "Injury"}}}
    kadry = {1: {"home": [{"id": 101, "s5": 5, "s_shots": 0.2}], "away": []}}
    out = sd.absences(mecze, absent, kadry)[1]
    assert [x["n"] for x in out["home"]] == ["B", "A"] and out["home"][0]["niepewny"] and out["home"][0]["pow"] == "uraz kolana"
    assert out["home"][1]["s5"] == 0 and out["away"][0]["pow"] == "kontuzja"


def test_squad_and_likely_starters_leave_out_the_injured():
    ko = pd.Timestamp("2026-10-01", tz="UTC")
    teams = pd.DataFrame({"fixture_id": range(1, 6), "team_id": 7, "kickoff": [ko + pd.Timedelta(days=7 * i) for i in range(5)],
                          "total_shots": 12.0, "shots_on_goal": 4.0})
    rows = []
    for f in range(1, 6):
        rows.append({"fixture_id": f, "team_id": 7, "player_id": 1, "player_name": "Striker", "minutes": 90, "started": True,
                     "position": "F", "shots_total": 4.0, "shots_on": 2.0})
        rows.append({"fixture_id": f, "team_id": 7, "player_id": 2, "player_name": "Winger", "minutes": 80, "started": True,
                     "position": "M", "shots_total": 2.0, "shots_on": 1.0})
        if f >= 4:
            rows.append({"fixture_id": f, "team_id": 7, "player_id": 3, "player_name": "Sub", "minutes": 15, "started": False,
                         "position": "F", "shots_total": 1.0, "shots_on": 0.0})
    players = pd.DataFrame(rows)
    before = ko + pd.Timedelta(days=60)
    sq = spl.squad(players, teams, 7, before)
    assert [p["id"] for p in sq] == [1, 2, 3] and sq[0]["s5"] == 5 and sq[2]["s5"] == 0 and sq[2]["m5"] == 6.0
    assert sq[0]["s_shots"] == pytest.approx(20 / 60, abs=1e-3)
    assert [p["id"] for p in spl.profiles(players, teams, 7, before)] == [1, 2]
    assert [p["id"] for p in spl.profiles(players, teams, 7, before, {1})] == [2]


def test_prematch_checks_come_about_an_hour_and_again_shortly_before_kick_off():
    start = dt.datetime(2026, 11, 1, 15, 0, tzinfo=dt.timezone.utc)
    assert pm.when_due(start, None) == start - dt.timedelta(minutes=65)
    first = {"sklad_znany": False, "druga": False, "czas": "2026-11-01T13:57:00Z"}
    assert pm.when_due(start, first) == start - dt.timedelta(minutes=40)
    late = dict(first, czas="2026-11-01T14:30:00Z")          # first check only 30 minutes before: 15 minutes later
    assert pm.when_due(start, late) == start - dt.timedelta(minutes=15)
    assert pm.when_due(start, {"sklad_znany": True}) is None and pm.when_due(start, {"sklad_znany": False, "druga": True}) is None
    now = start - dt.timedelta(hours=2)
    rows = [{"due": start - dt.timedelta(minutes=65)}, {"due": None}, {"due": start + dt.timedelta(hours=1)}]
    assert pm.next_check(rows, now) == start - dt.timedelta(minutes=65)
    assert pm.next_check(rows[1:], start) == start + dt.timedelta(hours=1)
    # a check overdue (the app was closed): as soon as a run can start, never in the past
    assert pm.next_check(rows[:1], start - dt.timedelta(minutes=30)) == start - dt.timedelta(minutes=28)
    assert pm.next_check([{"due": None}], now) is None


def test_prematch_check_flags_a_benched_player_and_a_rotated_team():
    start = dt.datetime(2026, 11, 1, 15, 0, tzinfo=dt.timezone.utc)
    legs = [{"stat": "player_sot", "scope": "home", "line": 0.5, "side": "over", "player_id": 9, "player": "Nine"},
            {"stat": "shots", "scope": "total", "line": 22.5, "side": "over"}]
    item = {"start": start, "legs": legs, "p": {"p_model": 0.55, "p_rynek": 0.5, "mecz": "H – A"}}
    home_xi = [{"player": {"id": i, "name": f"h{i}"}} for i in range(100, 110)] + [{"player": {"id": None, "name": "no id"}}]
    away_xi = [{"player": {"id": i, "name": f"a{i}"}} for i in range(200, 205)] + [{"player": {"id": i, "name": f"x{i}"}} for i in range(300, 306)]
    fixture = {"fixture": {"status": {"short": "NS"}},
               "lineups": [{"team": {"id": 1}, "startXI": home_xi, "substitutes": [{"player": {"id": 9, "name": "Nine"}}]},
                           {"team": {"id": 2}, "startXI": away_xi, "substitutes": []}]}
    names = {"home_id": 1, "home": "H", "away_id": 2, "away": "A"}
    kadry = {"home": [{"id": i, "n": f"h{i}", "s5": 5, "m5": 90.0} for i in range(100, 111)],
             "away": [{"id": i, "n": f"a{i}", "s5": 5, "m5": 90.0} for i in range(200, 211)]}
    now = start - dt.timedelta(minutes=40)
    k = pm.check(item, fixture, names, kadry, {}, {"p_model": 0.52, "p_rynek": 0.5, "kurs_szac": 1.9}, now)
    assert k["werdykt"] == "nie_graj" and k["sklad_znany"] and k["minut_przed"] == 40
    assert any("Nine zaczyna na ławce" in r for r in k["powody"]) and any("A: w składzie tylko 5 z 11" in r for r in k["powody"])
    assert k["sklady"]["home"]["w_skladzie"] == 10 and k["sklady"]["home"]["brak"] == ["h110"]
    assert k["zawodnicy"] == [{"n": "Nine", "stan": "lawka"}]
    calm = pm.check(item, {"fixture": {"status": {"short": "NS"}}}, names, kadry, {}, {"p_model": 0.54}, now)
    assert calm["werdykt"] == "ok" and not calm["sklad_znany"] and any("Składów jeszcze nie ma" in t for t in calm["info"])
    hurt = pm.check(item, None, names, kadry, {9: {"type": "Missing Fixture", "reason": "Knee Injury"}}, None, now, fetched=False)
    assert hurt["werdykt"] == "nie_graj" and any("uraz kolana" in r for r in hurt["powody"])


def test_builders_checked_before_kick_off_are_kept_by_later_runs():
    pr = sqlite3.connect(":memory:")
    pr.row_factory = sqlite3.Row
    pr.execute("create table extras (made_at text, day text, kind text, key text, fixture_id integer, start text, legs text, "
               "p_model real, odd real, payload text, rules text, primary key (day, kind, key))")
    pr.execute("create table coupons (made_at text, day text, kind text, legs text, odd real, p_model real, p_market real, "
               "ev real, ev_tax real, why text, rules text, primary key (day, kind))")
    day, start = "2026-11-01", "2026-11-01T15:00:00+00:00"
    for fid, payload in ((1, {"glowny": True, "kontrola": {"werdykt": "ok"}}), (2, {"glowny": True})):
        pr.execute("insert into extras values (?,?,?,?,?,?,?,?,?,?,?)", ("2026-11-01T08:00:00Z", day, "bb", f"{fid}:old", fid, start,
                                                                       "[]", 0.6, None, json.dumps(payload), "v5"))
    new = [{"fixture_id": f, "start": start, "rodzaj": "bezpieczny", "poziom": "pewniej", "szablon": "S+C", "legs": [], "p_model": 0.6}
           for f in (1, 2)]
    sd.log_extras(pr, {"pewne": {}, "bb": {day: new}, "kupon_pewne": {}}, {1: start, 2: start}, "2026-11-01T13:00:00Z")
    keys = sorted(r["key"] for r in pr.execute("select key from extras"))
    assert keys == ["1:old", "2:bezpieczny:pewniej:S+C"]


def test_extra_time_and_impossible_zeros_leave_counts_missing():
    df = pd.DataFrame([
        # a match that went to extra time: the provider's counts cover 120 minutes -> no counts at all
        {"fixture_id": 1, "side": "home", "status_short": "AET", "total_shots": 20, "shots_on_goal": 7, "corners": 9, "fouls": 15, "yellow_cards": 3, "red_cards": 0, "offsides": 2},
        {"fixture_id": 1, "side": "away", "status_short": "AET", "total_shots": 9, "shots_on_goal": 2, "corners": 3, "fouls": 18, "yellow_cards": 4, "red_cards": 0, "offsides": 1},
        # both teams at 0 fouls and 0 shots: not reported, so missing; one team at 0 corners is real
        {"fixture_id": 2, "side": "home", "status_short": "FT", "total_shots": 0, "shots_on_goal": 0, "corners": 0, "fouls": 0, "yellow_cards": 1, "red_cards": 0, "offsides": 0},
        {"fixture_id": 2, "side": "away", "status_short": "FT", "total_shots": 0, "shots_on_goal": 0, "corners": 4, "fouls": 0, "yellow_cards": 2, "red_cards": 0, "offsides": 0},
        # one team at 0 fouls is a gap too; the rival's 11 stay
        {"fixture_id": 3, "side": "home", "status_short": "FT", "total_shots": 14, "shots_on_goal": 5, "corners": 6, "fouls": 0, "yellow_cards": 1, "red_cards": 0, "offsides": 1},
        {"fixture_id": 3, "side": "away", "status_short": "FT", "total_shots": 8, "shots_on_goal": 2, "corners": 0, "fouls": 11, "yellow_cards": 2, "red_cards": 0, "offsides": 3},
    ])
    out = sm.clean_counts(df)
    assert out.loc[out["fixture_id"] == 1, sm.COLUMNS].isna().all().all()
    two = out[out["fixture_id"] == 2]
    assert two[["total_shots", "shots_on_goal", "fouls"]].isna().all().all()
    assert two["corners"].tolist() == [0, 4]
    three = out[out["fixture_id"] == 3].set_index("side")
    assert np.isnan(three.loc["home", "fouls"]) and three.loc["away", "fouls"] == 11
    assert three.loc["away", "corners"] == 0


def test_cards_stay_missing_in_a_league_season_that_reports_none():
    rows = []
    for i in range(10):     # league 1 reports cards (one empty count = a zero); league 2 never does
        rows.append({"league_id": 1, "season": 2025, "total_shots": 10, "shots_on_goal": 3, "corners": 4, "fouls": 10,
                     "yellow_cards": None if i == 0 else 2, "red_cards": None, "offsides": 1})
        rows.append({"league_id": 2, "season": 2025, "total_shots": 10, "shots_on_goal": 3, "corners": 4, "fouls": 10,
                     "yellow_cards": None, "red_cards": None, "offsides": 1})
    out = sm.fill_known_zeros(pd.DataFrame(rows).astype({"yellow_cards": float, "red_cards": float}))
    assert out.loc[out["league_id"] == 1, "cards"].min() == 0
    assert out.loc[out["league_id"] == 2, "cards"].isna().all()


def test_extra_time_matches_are_not_settled_on_120_minute_counts():
    by_fix = {5: pd.DataFrame([{"fixture_id": 5, "side": "home", "status_short": "AET", "total_shots": 25.0, "goals": float("nan")},
                               {"fixture_id": 5, "side": "away", "status_short": "AET", "total_shots": 9.0, "goals": float("nan")}])}
    assert sd.settle_leg({"fixture_id": 5, "stat": "shots", "scope": "home", "line": 13.5, "side": "over"}, by_fix) == ("zwrot", None)
    assert sd.settle_leg({"fixture_id": 5, "stat": "wynik", "scope": "home", "line": None, "side": "win"}, by_fix) == ("zwrot", None)


def test_selection_check_lowers_chances_only_when_picks_come_in_less_than_promised():
    def rows(n, p, hits, main=True, p0=None):
        return [{"wynik": "wygrana" if i < hits else "przegrana", "p_model": p,
                 "payload": {"glowny": main, "p_rynek": p - 0.05, **({"p_bez_selekcji": p0} if p0 else {})}} for i in range(n)]
    events = [{"wynik": "wygrana" if i < 34 else "przegrana", "p_model": 0.9, "payload": {}} for i in range(38)]
    ok = sd.selection_check(rows(80, 0.6, 49), rows(100, 0.6, 61, main=False), events)
    assert ok["mnozniki"] == {"bb": 1.0, "zdarzenie": 1.0}
    assert [g["grupa"] for g in ok["grupy"]] == ["glowne", "testowe", "zdarzenia"]
    assert ok["grupy"][0]["korekta"] == "bez zmian" and "nie zawyża" in ok["wniosek"]
    bad = sd.selection_check(rows(200, 0.7, 110), [], [])
    assert bad["mnozniki"]["bb"] > 1.2        # 90 misses against 60 promised
    assert "niżej" in bad["grupy"][0]["korekta"]
    # chances already lowered are judged as they were before the correction: the same record, the same multiplier
    again = sd.selection_check(rows(200, 0.6, 110, p0=0.7), [], [])
    assert again["mnozniki"]["bb"] == bad["mnozniki"]["bb"]


def test_selection_multiplier_is_read_from_the_record(tmp_path, monkeypatch):
    import json
    import stats_sure as ss
    f = tmp_path / "selekcja.json"
    monkeypatch.setattr(ss, "SEL_FILE", f)
    ss.selection_miss.cache_clear()
    assert ss.selection_miss("bb") == 1.0                      # no record yet
    f.write_text(json.dumps({"bb": 1.2}), encoding="utf-8")
    ss.selection_miss.cache_clear()
    assert ss.selection_miss("bb") == 1.2 and ss.selection_miss("zdarzenie") == 1.0
    assert ss.corrected(0.7, ss.BB_MISS * ss.selection_miss("bb")) < ss.corrected(0.7, ss.BB_MISS)
    ss.selection_miss.cache_clear()


def test_level_multiplier_compares_counts_with_uncorrected_forecasts():
    import datetime as dt
    pr = sqlite3.connect(":memory:")
    pr.execute("create table predictions (made_at text, fixture_id integer, stat text, mu_home real, mu_away real, alpha real, "
               "alpha_total real, k_home real, k_away real, k_total real, poziom real)")
    now = dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone.utc)
    rows, preds = [], []
    for i in range(50):
        ko = now - dt.timedelta(days=2 + i % 20)
        for side, y in (("home", 2.0), ("away", 1.0)):
            rows.append({"fixture_id": i, "side": side, "kickoff": pd.Timestamp(ko), "cards": y})
        # 2 + 2 cards forecast, logged before kick-off with an earlier correction of 0.9 folded into k
        preds.append(((ko - dt.timedelta(hours=5)).isoformat().replace("+00:00", "Z"), i, "cards", 2.0, 2.0, 0.1, 0.1, 0.9, 0.9, 0.9, 0.9))
    old = now - dt.timedelta(days=40)       # outside the window: ignored
    rows += [{"fixture_id": 99, "side": s, "kickoff": pd.Timestamp(old), "cards": 9.0} for s in ("home", "away")]
    preds.append(((old - dt.timedelta(hours=5)).isoformat().replace("+00:00", "Z"), 99, "cards", 2.0, 2.0, 0.1, 0.1, 1.0, 1.0, 1.0, 1.0))
    pr.executemany("insert into predictions values (?,?,?,?,?,?,?,?,?,?,?)", preds)
    cfg = {"statystyki": ["cards"], "okno_dni": 28, "n0": 200}
    out = sd.level_multipliers(pr, pd.DataFrame(rows), now, cfg)
    # 150 cards against 200 forecast (the stored 0.9 taken out), 100 team-matches shrunk with 200
    assert out["cards"] == pytest.approx(math.exp(100 / 300 * math.log(0.75)), abs=1e-4)
    assert sd.level_multipliers(pr, pd.DataFrame(rows), now, {"statystyki": []}) == {}
