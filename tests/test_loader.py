import datetime
import unittest

from backtest.loader import Match, load_matches, parse_date, season_of


class TestParseDate(unittest.TestCase):
    def test_four_digit_year(self):
        self.assertEqual(parse_date("09/08/2024"), datetime.date(2024, 8, 9))

    def test_two_digit_year(self):
        self.assertEqual(parse_date("25/07/14"), datetime.date(2014, 7, 25))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date("not a date"))


class TestSeasonOf(unittest.TestCase):
    def test_july_starts_new_season(self):
        self.assertEqual(season_of(datetime.date(2014, 7, 25)), "2014/15")

    def test_june_belongs_to_previous_season(self):
        self.assertEqual(season_of(datetime.date(2015, 6, 30)), "2014/15")

    def test_january_belongs_to_previous_season(self):
        self.assertEqual(season_of(datetime.date(2026, 1, 14)), "2025/26")


class TestLoadMatches(unittest.TestCase):
    """Testy na prawdziwych danych. Liczby pochodza z audytu i musza sie zgadzac
    co do jednego meczu - cicha utrata wierszy jest tu glownym ryzykiem."""

    @classmethod
    def setUpClass(cls):
        cls.matches = load_matches()

    def test_exact_row_count(self):
        self.assertEqual(len(self.matches), 86025)

    def test_exactly_22_leagues_no_empty_div(self):
        divs = {m.div for m in self.matches}
        self.assertNotIn("", divs, "pusty Div = BOM nie zostal usuniety")
        self.assertEqual(len(divs), 22)

    def test_date_range(self):
        self.assertEqual(self.matches[0].date, datetime.date(2014, 7, 25))
        self.assertEqual(self.matches[-1].date, datetime.date(2026, 1, 14))

    def test_sorted_deterministically(self):
        keys = [(m.date, m.div, m.home, m.away) for m in self.matches]
        self.assertEqual(keys, sorted(keys))

    def test_all_odds_above_one(self):
        for m in self.matches:
            self.assertTrue(min(m.o_open) > 1.0)
            self.assertTrue(min(m.o_close) > 1.0)

    def test_ftr_only_hda(self):
        self.assertEqual({m.ftr for m in self.matches}, {"H", "D", "A"})

    def test_is_a_match_namedtuple(self):
        self.assertIsInstance(self.matches[0], Match)
