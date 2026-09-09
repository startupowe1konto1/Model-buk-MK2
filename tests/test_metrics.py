import math
import unittest

from backtest.metrics import (
    Bucket,
    brier,
    calibration_buckets,
    ece,
    log_loss,
    outcome_index,
    rps,
)

P = (0.5, 0.3, 0.2)


class TestOutcomeIndex(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(outcome_index("H"), 0)
        self.assertEqual(outcome_index("D"), 1)
        self.assertEqual(outcome_index("A"), 2)

    def test_rejects_unknown(self):
        with self.assertRaises(ValueError):
            outcome_index("X")


class TestBrier(unittest.TestCase):
    def test_known_value(self):
        # (0.5-1)^2 + (0.3-0)^2 + (0.2-0)^2 = 0.25 + 0.09 + 0.04
        self.assertAlmostEqual(brier(P, "H"), 0.38, places=12)

    def test_perfect_prediction_scores_zero(self):
        self.assertAlmostEqual(brier((1.0, 0.0, 0.0), "H"), 0.0, places=12)


class TestLogLoss(unittest.TestCase):
    def test_known_value(self):
        self.assertAlmostEqual(log_loss(P, "H"), -math.log(0.5), places=12)

    def test_zero_probability_is_clipped_not_infinite(self):
        self.assertTrue(math.isfinite(log_loss((0.0, 0.5, 0.5), "H")))


class TestRPS(unittest.TestCase):
    def test_known_value(self):
        # CP = (0.5, 0.8), CA = (1, 1) -> ((0.5-1)^2 + (0.8-1)^2) / 2
        self.assertAlmostEqual(rps(P, "H"), 0.145, places=12)

    def test_draw_outcome(self):
        # CP = (0.5, 0.8), CA = (0, 1) -> (0.25 + 0.04) / 2
        self.assertAlmostEqual(rps(P, "D"), 0.145, places=12)

    def test_ordinal_penalty_is_asymmetric(self):
        """RPS karze mocniej pomylke H<->A niz H<->D, bo remis lezy miedzy nimi.
        Brier tej roznicy nie widzi - dlatego trzymamy obie metryki."""
        confident_home = (0.8, 0.1, 0.1)
        self.assertGreater(rps(confident_home, "A"), rps(confident_home, "D"))
        self.assertAlmostEqual(
            brier(confident_home, "A"), brier(confident_home, "D"), places=12
        )


class TestCalibrationBuckets(unittest.TestCase):
    def test_perfectly_calibrated_input(self):
        pairs = [(0.5, 1)] * 50 + [(0.5, 0)] * 50
        buckets = [b for b in calibration_buckets(pairs, n_buckets=10) if b.n]
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].n, 100)
        self.assertAlmostEqual(buckets[0].mean_pred, 0.5, places=12)
        self.assertAlmostEqual(buckets[0].mean_obs, 0.5, places=12)

    def test_returns_bucket_namedtuple(self):
        buckets = calibration_buckets([(0.5, 1)], n_buckets=10)
        self.assertIsInstance(buckets[0], Bucket)

    def test_probability_of_one_lands_in_last_bucket(self):
        buckets = calibration_buckets([(1.0, 1)], n_buckets=10)
        self.assertEqual(buckets[-1].n, 1)

    def test_negative_probability_does_not_land_in_last_bucket(self):
        """int(-0.1 * 10) == -1, ktore bez clampu Python odczytuje jako
        ostatni koszyk. Ujemne p nie powinno wygladac jak p ~ 1."""
        buckets = calibration_buckets([(-0.1, 0)], n_buckets=10)
        self.assertEqual(buckets[0].n, 1)
        self.assertEqual(buckets[-1].n, 0)


class TestECE(unittest.TestCase):
    def test_perfect_calibration_is_zero(self):
        pairs = [(0.5, 1)] * 50 + [(0.5, 0)] * 50
        self.assertAlmostEqual(ece(pairs, n_buckets=10), 0.0, places=12)

    def test_maximally_wrong_is_one(self):
        pairs = [(1.0, 0)] * 10
        self.assertAlmostEqual(ece(pairs, n_buckets=10), 1.0, places=12)
