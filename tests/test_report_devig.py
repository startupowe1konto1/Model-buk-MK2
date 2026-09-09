import datetime
import unittest

from backtest.loader import Match
from backtest.report_devig import MethodScore, best_method, score_method


def _match(o_open, o_close, ftr):
    return Match(
        div="E0",
        date=datetime.date(2020, 1, 1),
        home="A",
        away="B",
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


class TestScoreMethod(unittest.TestCase):
    def test_returns_methodscore_with_n(self):
        matches = [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")] * 10
        s = score_method(matches, "proportional", "close")
        self.assertIsInstance(s, MethodScore)
        self.assertEqual(s.n, 10)
        self.assertEqual(s.method, "proportional")

    def test_perfectly_calibrated_book_scores_near_zero_ece(self):
        """Ksiazka 3.0/3.0/3.0 przy dokladnie 1/3 kazdego wyniku."""
        matches = (
            [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "H")] * 100
            + [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "D")] * 100
            + [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "A")] * 100
        )
        s = score_method(matches, "proportional", "close")
        self.assertLess(s.ece, 1e-9)

    def test_open_and_close_are_read_from_different_fields(self):
        matches = [_match((1.01, 100.0, 100.0), (100.0, 100.0, 1.01), "H")] * 50
        open_score = score_method(matches, "proportional", "open")
        close_score = score_method(matches, "proportional", "close")
        self.assertLess(open_score.log_loss, close_score.log_loss)

    def test_rejects_unknown_which(self):
        with self.assertRaises(ValueError):
            score_method([_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")], "proportional", "kickoff")


class TestBestMethod(unittest.TestCase):
    def test_returns_a_registered_method_name(self):
        matches = (
            [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")] * 60
            + [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "D")] * 25
            + [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "A")] * 15
        )
        self.assertIn(best_method(matches), {"proportional", "power", "shin"})
