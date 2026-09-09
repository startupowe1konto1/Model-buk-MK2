import unittest

from backtest.gate import (
    TAU,
    Decision,
    break_even_po,
    decide,
    ev_gross,
    ev_net,
    threshold_po,
)


class TestTauRegimes(unittest.TestCase):
    def test_three_regimes_with_exact_values(self):
        self.assertEqual(TAU["pl_standard"], 0.88)
        self.assertEqual(TAU["betclic_degraded"], 0.94)
        self.assertEqual(TAU["betclic_notax"], 1.00)


class TestMB0001Regression(unittest.TestCase):
    """MB-0001 byl JEDYNYM kandydatem, ktory przeszedl bramke w calym pilocie
    27.08.2026 - i byl przegrywajacy. Stara regula liczyla EV na kursie brutto
    i dawala +6,15%. Po podatku to -6,59%. Ten test jest po to, zeby ta pomylka
    nie wrocila."""

    P = 0.550
    ODDS = 1.93

    def test_gross_reproduces_the_old_wrong_number(self):
        self.assertAlmostEqual(ev_gross(self.P, self.ODDS), 0.0615, places=6)

    def test_net_is_negative(self):
        self.assertAlmostEqual(
            ev_net(self.P, self.ODDS, TAU["pl_standard"]), -0.06588, places=5
        )

    def test_gate_rejects_it(self):
        d = decide(self.P, self.ODDS, TAU["pl_standard"], p_market=0.476)
        self.assertFalse(d.bet)

    def test_gate_would_have_accepted_it_without_tax(self):
        """Podatek jest jedyna przyczyna, dla ktorej ten bet jest przegrywajacy."""
        d = decide(self.P, self.ODDS, TAU["betclic_notax"], p_market=0.476)
        self.assertTrue(d.bet)


class TestThresholdTable(unittest.TestCase):
    """Tabela z HANDOFF.md sekcja A.1 - kazda wartosc musi sie odtworzyc."""

    def test_break_even(self):
        self.assertAlmostEqual(break_even_po(0.88), 1.13636, places=5)
        self.assertAlmostEqual(break_even_po(0.94), 1.06383, places=5)
        self.assertAlmostEqual(break_even_po(1.00), 1.00000, places=5)

    def test_threshold_at_five_percent(self):
        self.assertAlmostEqual(threshold_po(0.88, 0.05), 1.19318, places=5)
        self.assertAlmostEqual(threshold_po(0.94, 0.05), 1.11702, places=5)
        self.assertAlmostEqual(threshold_po(1.00, 0.05), 1.05000, places=5)


class TestDecide(unittest.TestCase):
    def test_returns_decision_namedtuple(self):
        self.assertIsInstance(decide(0.5, 2.0, 0.88, 0.5), Decision)

    def test_clear_value_passes(self):
        d = decide(0.70, 2.00, TAU["pl_standard"], p_market=0.50)
        self.assertTrue(d.bet)
        self.assertAlmostEqual(d.ev_net, 0.70 * 0.88 * 2.00 - 1, places=12)

    def test_edge_positive_but_ev_net_below_threshold_is_rejected(self):
        """Wlasnie ten przypadek przepuszczala stara regula."""
        d = decide(0.60, 1.90, TAU["pl_standard"], p_market=0.55)
        self.assertGreater(d.edge, 0.0)
        self.assertFalse(d.bet)
        self.assertIn("ev_net", d.reason)

    def test_negative_edge_is_rejected_before_ev(self):
        d = decide(0.40, 3.00, TAU["pl_standard"], p_market=0.50)
        self.assertFalse(d.bet)
        self.assertIn("edge", d.reason)

    def test_edge_is_model_minus_market(self):
        d = decide(0.55, 2.0, TAU["pl_standard"], p_market=0.50)
        self.assertAlmostEqual(d.edge, 0.05, places=12)
