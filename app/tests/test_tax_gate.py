"""Testy poprawki podatkowej w bramce EV_net (patrz app/POCHODZENIE.md).

Celowo stdlib unittest, nie pytest: w tym srodowisku nie ma zainstalowanego
pytest/pandas/catboost, wiec reszta app/tests/ nie moze tu chodzic. Ale
pricing.py i decision.py uzywaja wylacznie `math`/`dataclasses`, a config.py
tylko `tomllib`/`dataclasses`/`pathlib` - zaden z tych trzech modulow nie
potrzebuje pandas ani catboost, wiec da sie je przetestowac bez nich.

Jedno zastrzezenie: config.py robi `import tomllib`, ktory jest w stdlib
dopiero od Pythona 3.11. /usr/bin/python3 w tym srodowisku to 3.9.6 (nie ma
tomllib), wiec ten plik trzeba odpalac interpreterem >=3.11. Uzyty tu byl:
    ~/.pyenv/versions/3.12.8/bin/python3.12

Uruchomienie (z katalogu app/):
    PYTHONPATH=src ~/.pyenv/versions/3.12.8/bin/python3.12 -m unittest tests.test_tax_gate -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model_buk.config import load_corner_v02_config
from model_buk.decision import price_two_way_market
from model_buk.markets.pricing import expected_value, expected_value_net

VALID_TOML = """
[model]
version = "test"
train_start = "2015-01-01"
max_count = 40
random_seed = 42

[catboost]
loss_function = "Poisson"

[strengths]
shrinkage_games = 8.0
min_rate = 0.35
max_rate = 2.50

[betting]
edge_threshold = 0.05
ev_threshold = 0.05
max_bets_per_match = 1
{betting_extra}

[validation]
folds = [["2022-08-01", "2023-08-01", "2022/23"]]
reference_start = "2025-08-01"
"""


def _write_toml(tmp_path: Path, betting_extra: str) -> Path:
    path = tmp_path / "cfg.toml"
    path.write_text(VALID_TOML.format(betting_extra=betting_extra), encoding="utf-8")
    return path


class TestPilotRegressionMB0001(unittest.TestCase):
    """MB-0001 z pilotu 27.08.2026: jedyny kandydat, ktory przeszedl przez
    stara bramke (liczaca EV brutto) w calym pilocie - i byl zakladem
    przegrywajacym. EV brutto +6,15%, EV netto (podatek 12% od stawki,
    tax_multiplier=0.88) -6,59%. Ten test ma pilnowac, zeby te dwie liczby
    sie nie zlaly w jedna, tak jak zlaly sie w oryginalnym bledzie."""

    P = 0.550
    ODDS = 1.93

    def test_gross_ev_is_the_old_wrong_positive_number(self) -> None:
        self.assertAlmostEqual(expected_value(self.P, self.ODDS), 0.0615, places=4)

    def test_net_ev_at_polish_standard_is_negative(self) -> None:
        self.assertAlmostEqual(
            expected_value_net(self.P, self.ODDS, 0.88), -0.06588, places=5
        )


class TestGateDecidesOnNetNotGrossEv(unittest.TestCase):
    """Odtwarza MB-0001 przez pelna sciezke price_two_way_market (edge z
    devigu, nie podany recznie): edge_over i EV_gross_over oba przechodza
    stary prog 0.05, ale EV_net_over jest ujemne. Bramka MUSI zwrocic
    NO BET - stara wersja kodu (gating na EV brutto) zwracalaby tu BET."""

    def test_edge_and_gross_ev_clear_the_bar_but_net_ev_rejects_it(self) -> None:
        over, _under = price_two_way_market(
            p_over=0.55,
            over_odds=1.93,
            under_odds=1.75,
            edge_threshold=0.05,
            ev_threshold=0.05,
            tax_multiplier=0.88,
        )
        self.assertGreaterEqual(over.edge, 0.05)
        self.assertGreaterEqual(over.ev_gross, 0.05)
        self.assertLess(over.ev_net, 0.05)
        self.assertEqual(over.decision, "NO BET")

    def test_gate_would_have_accepted_it_without_tax(self) -> None:
        """Podatek jest jedyna przyczyna odrzucenia - dowod, ze to wlasnie
        on zmienia decyzje, a nie inny parametr."""
        over, _under = price_two_way_market(
            p_over=0.55,
            over_odds=1.93,
            under_odds=1.75,
            edge_threshold=0.05,
            ev_threshold=0.05,
            tax_multiplier=1.00,
        )
        self.assertEqual(over.decision, "BET")


class TestConfigRequiresExplicitTaxMultiplier(unittest.TestCase):
    """Brak jawnego tax_multiplier w [betting] MUSI wywalic config na
    starcie, a nie po cichu zalozyc 1.0 albo 0.88 - dokladnie tak, jak
    powstal oryginalny blad (nota projektowa uzywala nazwy EV_net, ale
    nigdy jej nie zdefiniowala, wiec ktos podstawil pod nia EV brutto)."""

    def test_missing_tax_multiplier_raises_with_key_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_toml(Path(tmp), betting_extra="")
            with self.assertRaises(ValueError) as ctx:
                load_corner_v02_config(path)
            self.assertIn("tax_multiplier", str(ctx.exception))

    def test_present_tax_multiplier_loads_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_toml(Path(tmp), betting_extra="tax_multiplier = 0.88")
            cfg = load_corner_v02_config(path)
            self.assertEqual(cfg.tax_multiplier, 0.88)

    def test_real_project_config_has_tax_multiplier(self) -> None:
        real_config = Path(__file__).resolve().parents[1] / "config" / "corners_v02.toml"
        cfg = load_corner_v02_config(real_config)
        self.assertEqual(cfg.tax_multiplier, 0.88)


if __name__ == "__main__":
    unittest.main()
