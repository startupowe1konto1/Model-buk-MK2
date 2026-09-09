import unittest

from backtest.devig import (
    METHODS,
    devig_power,
    devig_proportional,
    devig_shin,
    overround,
)

FAIR = (3.0, 3.0, 3.0)              # ksiazka bez marzy
TYPICAL = (2.00, 3.50, 4.00)        # marza ~3,6%
FAVOURITE = (1.20, 7.00, 15.00)     # silny faworyt + dlugi strzal


class TestOverround(unittest.TestCase):
    def test_fair_book_has_zero_overround(self):
        self.assertAlmostEqual(overround(FAIR), 0.0, places=12)

    def test_typical_book(self):
        self.assertAlmostEqual(overround(TYPICAL), 0.0357142857, places=9)


class TestAllMethodsShareInvariants(unittest.TestCase):
    """Kazda metoda musi zwracac rozklad prawdopodobienstwa."""

    def test_probabilities_sum_to_one(self):
        for name, fn in METHODS.items():
            for book in (FAIR, TYPICAL, FAVOURITE):
                with self.subTest(method=name, book=book):
                    self.assertAlmostEqual(sum(fn(book)), 1.0, places=9)

    def test_probabilities_strictly_inside_unit_interval(self):
        for name, fn in METHODS.items():
            for book in (FAIR, TYPICAL, FAVOURITE):
                with self.subTest(method=name, book=book):
                    for p in fn(book):
                        self.assertGreater(p, 0.0)
                        self.assertLess(p, 1.0)

    def test_fair_book_is_a_fixed_point(self):
        """Ksiazka bez marzy nie powinna byc ruszona przez zadna metode."""
        for name, fn in METHODS.items():
            with self.subTest(method=name):
                for p in fn(FAIR):
                    self.assertAlmostEqual(p, 1.0 / 3.0, places=9)

    def test_order_is_preserved(self):
        """Faworyt zostaje faworytem niezaleznie od metody."""
        for name, fn in METHODS.items():
            with self.subTest(method=name):
                p = fn(FAVOURITE)
                self.assertGreater(p[0], p[1])
                self.assertGreater(p[1], p[2])


class TestProportional(unittest.TestCase):
    def test_known_values(self):
        p = devig_proportional(TYPICAL)
        self.assertAlmostEqual(p[0], 0.4827586207, places=9)
        self.assertAlmostEqual(p[1], 0.2758620690, places=9)
        self.assertAlmostEqual(p[2], 0.2413793103, places=9)


class TestShinVersusProportional(unittest.TestCase):
    """Shin modeluje handel insiderow, wiec zdejmuje wiecej marzy z dlugich
    strzalow niz metoda proporcjonalna. To jest kierunek biasu faworyt-longshot
    i jedyny powod, dla ktorego w ogole trzymamy trzy metody."""

    def test_shin_gives_longshot_less_probability(self):
        prop = devig_proportional(FAVOURITE)
        shin = devig_shin(FAVOURITE)
        self.assertLess(shin[2], prop[2])

    def test_shin_gives_favourite_more_probability(self):
        prop = devig_proportional(FAVOURITE)
        shin = devig_shin(FAVOURITE)
        self.assertGreater(shin[0], prop[0])


class TestPower(unittest.TestCase):
    def test_exponent_above_one_for_positive_overround(self):
        """Przy dodatniej marzy wykladnik musi byc > 1, inaczej suma nie spadnie
        do jedynki. Sprawdzamy posrednio: kazde p mniejsze niz surowe 1/o."""
        p = devig_power(TYPICAL)
        for prob, o in zip(p, TYPICAL):
            self.assertLess(prob, 1.0 / o)


class TestRegistry(unittest.TestCase):
    def test_registry_has_three_methods(self):
        self.assertEqual(set(METHODS), {"proportional", "power", "shin"})
