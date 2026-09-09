import datetime
import unittest

from backtest.gate import TAU
from backtest.loader import Match
from backtest.walkforward import Selection, features, run, seasons, summarize


def _m(year, month, day, home, away, ftr, o_open=(2.0, 3.5, 4.0), o_close=(2.0, 3.5, 4.0)):
    return Match(
        div="E0",
        date=datetime.date(year, month, day),
        home=home,
        away=away,
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


def _synthetic(n_seasons=4):
    """Liga, w ktorej 'Silny' zawsze wygrywa u siebie z 'Slaby'."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 21):
            out.append(_m(year, 9, day, "Silny", "Slaby", "H"))
            out.append(_m(year, 10, day, "Slaby", "Silny", "A"))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


def _synthetic_moving(n_seasons=4):
    """Jak _synthetic, ale linia sie rusza: o_open != o_close na kazdej
    selekcji. Potrzebne wszedzie, gdzie test ma odroznic cene otwarcia od
    ceny zamkniecia - przy rownych kursach taki test nie pilnuje niczego."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 21):
            out.append(_m(year, 9, day, "Silny", "Slaby", "H",
                          o_open=(2.00, 3.50, 4.00), o_close=(1.70, 3.80, 5.00)))
            out.append(_m(year, 10, day, "Slaby", "Silny", "A",
                          o_open=(4.00, 3.50, 2.00), o_close=(5.00, 3.80, 1.70)))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


class TestSeasons(unittest.TestCase):
    def test_sorted_and_unique(self):
        s = seasons(_synthetic(3))
        self.assertEqual(s, ["2014/15", "2015/16", "2016/17"])


class TestFeatures(unittest.TestCase):
    def test_intercept_first_and_scaled_elo(self):
        self.assertEqual(features(400.0), [1.0, 1.0])
        self.assertEqual(features(0.0), [1.0, 0.0])


class TestRun(unittest.TestCase):
    def test_returns_selections(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertTrue(all(isinstance(s, Selection) for s in sel))

    def test_first_seasons_are_never_bet(self):
        """Przy min_train_seasons=2 nic z dwoch pierwszych sezonow nie moze
        trafic do wynikow - to nie sa dane testowe, tylko treningowe."""
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0, min_train_seasons=2)
        for s in sel:
            self.assertGreaterEqual(s.match.date.year, 2016)

    def test_no_selection_uses_closing_odds_as_price(self):
        """Fixture MUSI miec o_open != o_close, inaczej test nie odroznia ceny
        otwarcia od zamkniecia i przechodzi takze wtedy, gdy kod bierze zaklad
        po kursie zamkniecia. Zamkniecie sluzy wylacznie do liczenia CLV."""
        sel = run(_synthetic_moving(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertTrue(sel, "fixture nie wyprodukowal zadnej selekcji")
        for s in sel:
            idx = ("H", "D", "A").index(s.outcome)
            self.assertAlmostEqual(s.odds, s.match.o_open[idx], places=12)
            self.assertNotAlmostEqual(s.odds, s.match.o_close[idx], places=6)

    def test_model_learns_the_synthetic_edge(self):
        """W lidze, gdzie Silny zawsze wygrywa, model musi to zauwazyc."""
        sel = run(_synthetic(5), "proportional", TAU["betclic_notax"], ev_min=0.0)
        home_silny = [s for s in sel if s.match.home == "Silny" and s.outcome == "H"]
        self.assertTrue(home_silny)
        self.assertGreater(sum(s.p_model for s in home_silny) / len(home_silny), 0.6)

    def test_clv_is_zero_when_line_does_not_move(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        for s in sel:
            self.assertAlmostEqual(s.clv, 0.0, places=9)

    def test_clv_is_positive_when_line_moves_toward_the_pick(self):
        """Zamkniecie krotsze na gospodarzu = rynek przyszedl po nas."""
        matches = _synthetic_moving(4)
        sel = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.0)
        picks = [s for s in sel if s.outcome in ("H", "A")]
        self.assertTrue(picks)
        self.assertGreater(sum(s.clv for s in picks) / len(picks), 0.0)


class TestSummarize(unittest.TestCase):
    def test_reports_core_fields(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        out = summarize(sel)
        for key in ("n_bets", "mean_clv", "median_clv", "beat_close_rate", "hit_rate", "by_season", "by_league"):
            self.assertIn(key, out)

    def test_empty_input_does_not_crash(self):
        out = summarize([])
        self.assertEqual(out["n_bets"], 0)
