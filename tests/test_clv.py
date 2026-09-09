import unittest

from backtest.clv import beat_close, clv_from_fair_odds, clv_from_probs


class TestClvFromProbs(unittest.TestCase):
    def test_known_value(self):
        self.assertAlmostEqual(clv_from_probs(0.50, 0.55), 0.10, places=12)

    def test_no_movement_is_zero(self):
        self.assertAlmostEqual(clv_from_probs(0.42, 0.42), 0.0, places=12)

    def test_market_moving_against_you_is_negative(self):
        self.assertLess(clv_from_probs(0.50, 0.45), 0.0)


class TestClvFromFairOdds(unittest.TestCase):
    def test_known_value(self):
        # p 0.50 -> 2.00, p 0.55 -> 1.8181...
        self.assertAlmostEqual(clv_from_fair_odds(2.0, 1.0 / 0.55), 0.10, places=12)


class TestBothFormsAgree(unittest.TestCase):
    """Pomylenie kierunku odwraca wniosek calego backtestu i jest niewidoczne
    golym okiem. Obie postacie musza dac identyczna liczbe."""

    def test_agreement_across_a_grid(self):
        for p_open in (0.05, 0.2, 0.35, 0.5, 0.65, 0.9):
            for p_close in (0.05, 0.2, 0.35, 0.5, 0.65, 0.9):
                with self.subTest(p_open=p_open, p_close=p_close):
                    self.assertAlmostEqual(
                        clv_from_probs(p_open, p_close),
                        clv_from_fair_odds(1.0 / p_open, 1.0 / p_close),
                        places=12,
                    )


class TestBeatClose(unittest.TestCase):
    def test_true_when_market_moved_your_way(self):
        self.assertTrue(beat_close(0.50, 0.55))

    def test_false_on_no_movement(self):
        self.assertFalse(beat_close(0.50, 0.50))


class TestRejectsInvalidInput(unittest.TestCase):
    def test_zero_probability_raises(self):
        with self.assertRaises(ValueError):
            clv_from_probs(0.0, 0.5)
