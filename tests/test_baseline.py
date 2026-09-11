import datetime
import unittest

from backtest.baseline import (
    Leg,
    betting_seasons,
    legs,
    restrict_to_betting_seasons,
    summarize,
)
from backtest.loader import Match, load_matches
from backtest.walkforward import seasons as all_seasons


def _m(year, month, day, home, away, ftr, o_open=(2.0, 3.5, 4.0), o_close=(2.0, 3.5, 4.0), div="E0"):
    return Match(
        div=div,
        date=datetime.date(year, month, day),
        home=home,
        away=away,
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


def _synthetic_moving(n_seasons=4):
    """n_seasons sezonow, jedna liga, linia sie rusza (o_open != o_close) na
    kazdym meczu - zeby CLV nie bylo trywialnie zerowe."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 6):
            out.append(_m(year, 9, day, "A", "B", "H",
                           o_open=(2.00, 3.50, 4.00), o_close=(1.90, 3.60, 4.20)))
            out.append(_m(year, 10, day, "B", "A", "A",
                           o_open=(4.00, 3.50, 2.00), o_close=(4.20, 3.60, 1.90)))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


class TestBettingSeasons(unittest.TestCase):
    """Zakres sezonow testowych ma wynikac z walkforward.seasons(), nie z
    literalu wpisanego na sztywno w baseline.py."""

    def test_matches_walkforward_tail(self):
        matches = _synthetic_moving(4)
        got = betting_seasons(matches, min_train_seasons=2)
        want = all_seasons(matches)[2:]
        self.assertEqual(got, want)
        self.assertEqual(got, ["2016/17", "2017/18"])

    def test_min_train_seasons_zero_keeps_everything(self):
        matches = _synthetic_moving(3)
        self.assertEqual(betting_seasons(matches, 0), all_seasons(matches))


class TestRestrictToBettingSeasons(unittest.TestCase):
    def test_drops_pure_training_seasons(self):
        matches = _synthetic_moving(4)  # 4 sezony x 10 meczow = 40
        restricted = restrict_to_betting_seasons(matches, min_train_seasons=2)
        # 2 sezony treningowe odpadaja, zostaja 2 sezony x 10 meczow.
        self.assertEqual(len(restricted), 20)
        kept_seasons = {"2016/17", "2017/18"}
        from backtest.loader import season_of
        self.assertTrue(all(season_of(m.date) in kept_seasons for m in restricted))

    def test_min_train_seasons_larger_than_available_yields_empty(self):
        matches = _synthetic_moving(2)
        self.assertEqual(restrict_to_betting_seasons(matches, min_train_seasons=5), [])


class TestLegs(unittest.TestCase):
    def test_three_legs_per_match(self):
        matches = _synthetic_moving(1)  # 10 meczow
        out = legs(matches, "proportional")
        self.assertEqual(len(out), 3 * len(matches))

    def test_outcomes_are_h_d_a_in_order_per_match(self):
        matches = [_m(2020, 1, 1, "A", "B", "H")]
        out = legs(matches, "proportional")
        self.assertEqual([leg.outcome for leg in out], ["H", "D", "A"])

    def test_clv_matches_clv_from_probs(self):
        from backtest.clv import clv_from_probs
        from backtest.devig import METHODS
        matches = [_m(2020, 1, 1, "A", "B", "H",
                       o_open=(2.0, 3.5, 4.0), o_close=(1.9, 3.6, 4.2))]
        out = legs(matches, "power")
        devig = METHODS["power"]
        p_open = devig(matches[0].o_open)
        p_close = devig(matches[0].o_close)
        for idx, leg in enumerate(out):
            self.assertAlmostEqual(leg.clv, clv_from_probs(p_open[idx], p_close[idx]))

    def test_unknown_method_raises(self):
        with self.assertRaises(KeyError):
            legs([_m(2020, 1, 1, "A", "B", "H")], "no-such-method")


class TestSummarize(unittest.TestCase):
    def test_empty_input(self):
        s = summarize([])
        self.assertEqual(s["n_legs"], 0)
        self.assertEqual(s["by_season"], {})
        self.assertEqual(s["by_league"], {})

    def test_groups_by_season_and_league(self):
        matches = _synthetic_moving(2)  # sezony 2014/15, 2015/16, jedna liga E0
        out = legs(matches, "proportional")
        s = summarize(out)
        self.assertEqual(s["n_legs"], 3 * len(matches))
        self.assertEqual(set(s["by_season"].keys()), {"2014/15", "2015/16"})
        self.assertEqual(set(s["by_league"].keys()), {"E0"})
        total_in_seasons = sum(v["n"] for v in s["by_season"].values())
        self.assertEqual(total_in_seasons, s["n_legs"])


class TestBaselineOnRealData(unittest.TestCase):
    """Liczby pochodza z HANDOFF.md sekcja B i z brief'u task 1. Ograniczone do
    sezonow testowych walk-forward (min_train_seasons=2, od 2016/17)."""

    @classmethod
    def setUpClass(cls):
        cls.matches = load_matches()
        cls.betting_seasons = betting_seasons(cls.matches, min_train_seasons=2)
        cls.test_matches = restrict_to_betting_seasons(cls.matches, min_train_seasons=2)

    def test_betting_seasons_start_at_2016_17(self):
        self.assertEqual(self.betting_seasons[0], "2016/17")

    def test_test_match_count_is_71264(self):
        # Wlasnosc: liczenie na WSZYSTKICH meczach (86 024) jest bledem - dokłada
        # dwa najwczesniejsze sezony. Ten test pada, jesli restrict_to_betting_seasons
        # przestanie filtrowac (patrz dowod mutacyjny w raporcie).
        self.assertEqual(len(self.test_matches), 71264)
        self.assertNotEqual(len(self.test_matches), len(self.matches))

    def test_leg_count_is_triple_match_count_for_every_method(self):
        for name in ("proportional", "power", "shin"):
            out = legs(self.test_matches, name)
            self.assertEqual(len(out), 3 * 71264)
            self.assertEqual(len(out), 213792)

    def test_beat_close_rate_near_50_percent_for_every_method(self):
        # Bez informacji baseline MUSI pobijac zamkniecie w ok. 50% przypadkow -
        # to wlasnosc modelu braku informacji, nie odkrycie. Tolerancja 0.3pp:
        # najwieksze zmierzone odchylenie (proportional, 49.84%) to 0.16pp, wiec
        # 0.3pp lapie realny blad w lancuchu (np. odwrocenie open/close), a nie
        # szum tej metryki.
        expected = {
            "proportional": 0.4984,
            "power": 0.5008,
            "shin": 0.5001,
        }
        for name, want in expected.items():
            s = summarize(legs(self.test_matches, name))
            self.assertAlmostEqual(s["beat_close_rate"], 0.5, delta=0.003)
            self.assertAlmostEqual(s["beat_close_rate"], want, delta=0.001)

    def test_power_method_reproduces_measured_table(self):
        s = summarize(legs(self.test_matches, "power"))
        self.assertAlmostEqual(s["mean_clv"], 0.001059, delta=0.000005)
        self.assertAlmostEqual(s["median_clv"], 0.000084, delta=0.000005)
        self.assertAlmostEqual(s["beat_close_rate"], 0.5008, delta=0.0001)

    def test_all_three_methods_reproduce_measured_table(self):
        expected = {
            "proportional": (0.000119, 0.000000, 0.4984),
            "power": (0.001059, 0.000084, 0.5008),
            "shin": (0.000769, 0.000018, 0.5001),
        }
        for name, (mean_clv, median_clv, beat) in expected.items():
            s = summarize(legs(self.test_matches, name))
            self.assertAlmostEqual(s["mean_clv"], mean_clv, delta=0.00001)
            self.assertAlmostEqual(s["median_clv"], median_clv, delta=0.00001)
            self.assertAlmostEqual(s["beat_close_rate"], beat, delta=0.0001)

    def test_mean_positive_median_near_zero_right_skew(self):
        # Srednia CLV jest dodatnia, mediana bliska zeru: to prawoskosnosc
        # metryki ilorazowej (p_close/p_open - 1), nie przewaga informacyjna.
        # Bez tego testu latwo przeczytac dodatnia srednia jako sukces.
        for name in ("proportional", "power", "shin"):
            s = summarize(legs(self.test_matches, name))
            self.assertGreater(s["mean_clv"], 0.0)
            self.assertLess(abs(s["median_clv"]), 0.0005)
            self.assertGreater(s["mean_clv"], abs(s["median_clv"]))


if __name__ == "__main__":
    unittest.main()
