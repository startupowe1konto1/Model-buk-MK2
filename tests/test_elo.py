import unittest

from backtest.elo import SCORE, EloTable


class TestFreshTable(unittest.TestCase):
    def test_unknown_team_starts_at_default(self):
        t = EloTable()
        self.assertEqual(t.rating("Nowy Klub"), 1500.0)

    def test_reading_a_rating_does_not_create_history(self):
        t = EloTable()
        t.rating("A")
        t.rating("B")
        self.assertEqual(t.rating("A"), 1500.0)


class TestExpectation(unittest.TestCase):
    def test_home_advantage_tilts_expectation_above_half(self):
        t = EloTable(home_adv=60.0)
        self.assertAlmostEqual(t.expected_home("A", "B"), 0.585498, places=5)

    def test_no_home_advantage_gives_exactly_half(self):
        t = EloTable(home_adv=0.0)
        self.assertAlmostEqual(t.expected_home("A", "B"), 0.5, places=12)

    def test_diff_includes_home_advantage(self):
        t = EloTable(home_adv=60.0)
        self.assertAlmostEqual(t.diff("A", "B"), 60.0, places=12)


class TestUpdate(unittest.TestCase):
    def test_home_win_moves_ratings_by_known_amount(self):
        t = EloTable(k=20.0, home_adv=60.0)
        t.update("A", "B", "H")
        self.assertAlmostEqual(t.rating("A"), 1508.2900, places=4)
        self.assertAlmostEqual(t.rating("B"), 1491.7100, places=4)

    def test_update_is_zero_sum(self):
        t = EloTable()
        t.update("A", "B", "A")
        self.assertAlmostEqual(t.rating("A") + t.rating("B"), 3000.0, places=9)

    def test_draw_between_equals_still_moves_because_of_home_advantage(self):
        """Remis u siebie jest wynikiem gorszym od oczekiwanego, wiec gospodarz traci."""
        t = EloTable(k=20.0, home_adv=60.0)
        t.update("A", "B", "D")
        self.assertLess(t.rating("A"), 1500.0)
        self.assertGreater(t.rating("B"), 1500.0)

    def test_score_mapping(self):
        self.assertEqual(SCORE["H"], 1.0)
        self.assertEqual(SCORE["D"], 0.5)
        self.assertEqual(SCORE["A"], 0.0)


class TestNoLeakage(unittest.TestCase):
    """Rdzen dyscypliny z [[11 - Czas predykcji i leakage]]: predykcja dla meczu
    nie moze zalezec od jego wyniku ani od zadnego pozniejszego meczu."""

    def test_first_ever_match_is_predicted_from_default_ratings(self):
        t = EloTable(home_adv=0.0)
        before = t.expected_home("A", "B")
        t.update("A", "B", "H")
        self.assertAlmostEqual(before, 0.5, places=12)

    def test_prediction_depends_only_on_earlier_updates(self):
        a = EloTable()
        b = EloTable()
        for table in (a, b):
            table.update("A", "B", "H")
            table.update("A", "C", "H")
        # b dostaje dodatkowy, pozniejszy mecz - nie moze zmienic tego,
        # co bylo znane wczesniej
        expected_before_extra = b.diff("A", "D")
        b.update("A", "D", "H")
        self.assertAlmostEqual(a.diff("A", "D"), expected_before_extra, places=12)
