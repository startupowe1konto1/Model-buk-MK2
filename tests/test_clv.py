import unittest
from typing import NamedTuple

from backtest.clv import beat_close, clv_from_fair_odds, clv_from_probs, group_by


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


class _Item(NamedTuple):
    key: str
    p_open: float
    p_close: float
    clv: float


class TestGroupBy(unittest.TestCase):
    """group_by() to szkielet wspolny dla walkforward.summarize i
    baseline.summarize (obie liczyly to samo dla roznych pol p_open/p_close -
    patrz konsolidacja w task 2)."""

    def test_groups_and_counts(self):
        items = [
            _Item("A", 0.5, 0.5, 0.0),
            _Item("A", 0.5, 0.6, 0.2),
            _Item("B", 0.5, 0.4, -0.2),
        ]
        out = group_by(items, lambda x: x.key, lambda x: x.p_open, lambda x: x.p_close)
        self.assertEqual(set(out), {"A", "B"})
        self.assertEqual(out["A"]["n"], 2)
        self.assertEqual(out["B"]["n"], 1)

    def test_mean_clv_is_arithmetic_mean_of_group(self):
        items = [_Item("A", 0.5, 0.5, 0.1), _Item("A", 0.5, 0.5, 0.3)]
        out = group_by(items, lambda x: x.key, lambda x: x.p_open, lambda x: x.p_close)
        self.assertAlmostEqual(out["A"]["mean_clv"], 0.2, places=12)

    def test_beat_close_rate_uses_the_supplied_accessors(self):
        """p_open/p_close nazwane inaczej niz na NamedTuple (tu 'key' zamiast
        klucza grupujacego) - group_by nie moze zakladac konkretnych nazw pol,
        musi uzyc DOKLADNIE tych funkcji, ktore dostal, W TEJ kolejnosci.

        Zestaw celowo NIESYMETRYCZNY (2 x True, 1 x False): gdyby ktos
        zamienil kolejnosc argumentow przekazywanych do beat_close (p_open
        <-> p_close), kazdy wynik odwracalby sie na przeciwny, a przy 2:1
        zamiana zmienia zmierzona stope z 2/3 na 1/3 - przy symetrycznym
        zestawie taka pomylka jest niewidoczna, bo srednia wychodzi ta sama."""
        items = [
            _Item("A", 0.5, 0.6, 0.2),   # beat_close(0.5, 0.6): True
            _Item("A", 0.5, 0.55, 0.1),  # beat_close(0.5, 0.55): True
            _Item("A", 0.5, 0.4, -0.2),  # beat_close(0.5, 0.4): False
        ]
        out = group_by(items, lambda x: x.key, lambda x: x.p_open, lambda x: x.p_close)
        self.assertAlmostEqual(out["A"]["beat_close_rate"], 2.0 / 3.0, places=12)

    def test_groups_are_sorted_by_key(self):
        items = [_Item("Z", 0.5, 0.5, 0.0), _Item("A", 0.5, 0.5, 0.0)]
        out = group_by(items, lambda x: x.key, lambda x: x.p_open, lambda x: x.p_close)
        self.assertEqual(list(out.keys()), ["A", "Z"])
