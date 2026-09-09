import math
import unittest

from backtest.model import fit, predict_proba, softmax


class TestSoftmax(unittest.TestCase):
    def test_sums_to_one(self):
        self.assertAlmostEqual(sum(softmax([1.0, 2.0, 3.0])), 1.0, places=12)

    def test_uniform_scores_give_uniform_probabilities(self):
        for p in softmax([0.0, 0.0, 0.0]):
            self.assertAlmostEqual(p, 1.0 / 3.0, places=12)

    def test_monotonic_in_score(self):
        p = softmax([1.0, 2.0, 3.0])
        self.assertLess(p[0], p[1])
        self.assertLess(p[1], p[2])

    def test_numerically_stable_for_large_scores(self):
        p = softmax([1000.0, 1000.0, 1000.0])
        self.assertTrue(all(math.isfinite(x) for x in p))
        self.assertAlmostEqual(sum(p), 1.0, places=12)


class TestFit(unittest.TestCase):
    def test_recovers_base_rates_from_intercept_only(self):
        """Bez cech model musi odtworzyc czestosci bazowe."""
        X = [[1.0]] * 100
        y = [0] * 60 + [1] * 25 + [2] * 15
        w = fit(X, y, n_classes=3, epochs=3000, lr=0.5, l2=0.0)
        p = predict_proba(w, [1.0])
        self.assertAlmostEqual(p[0], 0.60, places=2)
        self.assertAlmostEqual(p[1], 0.25, places=2)
        self.assertAlmostEqual(p[2], 0.15, places=2)

    def test_learns_a_separating_feature(self):
        X = [[1.0, -1.0]] * 50 + [[1.0, 1.0]] * 50
        y = [2] * 50 + [0] * 50
        w = fit(X, y, n_classes=3, epochs=2000, lr=0.5, l2=0.0)
        self.assertGreater(predict_proba(w, [1.0, 1.0])[0], 0.9)
        self.assertGreater(predict_proba(w, [1.0, -1.0])[2], 0.9)

    def test_weight_matrix_shape(self):
        w = fit([[1.0, 0.5]] * 10, [0] * 10, n_classes=3, epochs=10)
        self.assertEqual(len(w), 3)
        self.assertEqual(len(w[0]), 2)

    def test_l2_shrinks_weights(self):
        X = [[1.0, -1.0]] * 50 + [[1.0, 1.0]] * 50
        y = [2] * 50 + [0] * 50
        loose = fit(X, y, n_classes=3, epochs=500, lr=0.5, l2=0.0)
        tight = fit(X, y, n_classes=3, epochs=500, lr=0.5, l2=1.0)
        self.assertLess(
            sum(abs(v) for row in tight for v in row),
            sum(abs(v) for row in loose for v in row),
        )


class TestPredictProba(unittest.TestCase):
    def test_sums_to_one(self):
        w = [[0.1, 0.2], [0.3, -0.1], [-0.2, 0.05]]
        self.assertAlmostEqual(sum(predict_proba(w, [1.0, 0.7])), 1.0, places=12)
