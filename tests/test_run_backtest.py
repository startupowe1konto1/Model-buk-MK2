import datetime
import unittest

from backtest.loader import Match
from backtest.report_devig import MethodScore
from backtest.run_backtest import _markdown, distinct_match_count
from backtest.walkforward import ModelScore, Selection


def _match(home, away, ftr="H", day=1, div="E0"):
    return Match(
        div=div,
        date=datetime.date(2020, 1, day),
        home=home,
        away=away,
        ftr=ftr,
        o_open=(2.0, 3.5, 4.0),
        o_close=(1.9, 3.6, 4.2),
    )


def _selection(match, outcome="H"):
    return Selection(
        match=match,
        outcome=outcome,
        p_model=0.6,
        p_market_open=0.5,
        p_market_close=0.55,
        odds=2.0,
        ev_net=0.1,
        edge=0.1,
        clv=0.05,
    )


class TestDistinctMatchCount(unittest.TestCase):
    """Wlasnosc z brief'u task 3: liczba odrebnych meczow <= liczba selekcji,
    i SCISLE mniejsza, gdy ktorykolwiek mecz dal dwie selekcje. Selection
    dwoch roznych wynikow tego samego meczu niesie ten sam obiekt `.match`,
    wiec `set()` po `.match` musi je scalic w jeden."""

    def test_empty_input_is_zero(self):
        self.assertEqual(distinct_match_count([]), 0)

    def test_equals_bet_count_when_every_match_has_one_selection(self):
        m1, m2, m3 = _match("A", "B", day=1), _match("C", "D", day=2), _match("E", "F", day=3)
        sels = [_selection(m1), _selection(m2), _selection(m3)]
        self.assertEqual(len(sels), 3)
        self.assertEqual(distinct_match_count(sels), 3)
        self.assertEqual(distinct_match_count(sels), len(sels))

    def test_strictly_less_than_bet_count_when_one_match_has_two_selections(self):
        m1 = _match("A", "B", day=1)
        m2 = _match("C", "D", day=2)
        sels = [
            _selection(m1, outcome="H"),
            _selection(m1, outcome="A"),  # druga noga TEGO SAMEGO meczu
            _selection(m2, outcome="D"),
        ]
        self.assertEqual(len(sels), 3)
        self.assertEqual(distinct_match_count(sels), 2)
        self.assertLess(distinct_match_count(sels), len(sels))

    def test_repeated_match_object_collapses_regardless_of_dict_order(self):
        """Nie ma znaczenia, w ktorym miejscu listy siedzi powtorzony mecz -
        `set()` jest nieuporzadkowany, wiec to pilnuje, ze liczenie nie
        zalezy przypadkiem od kolejnosci wejscia."""
        m = _match("A", "B", day=1)
        other = _match("C", "D", day=2)
        sels = [_selection(other), _selection(m, "H"), _selection(m, "D"), _selection(m, "A")]
        self.assertEqual(len(sels), 4)
        self.assertEqual(distinct_match_count(sels), 2)


class TestMarkdownReport(unittest.TestCase):
    """Testy _markdown() na w pelni syntetycznym, recznie skonstruowanym
    wejsciu - bez uruchamiania backtestu. Pina jednoczesnie: (1) nowy wiersz
    z liczba odrebnych meczow, (2) nowy wiersz/tabele z baseline obok modelu,
    (3) jawne wytlumaczenie artefaktu srednia/mediana, (4) nieobecnosc tezy
    "wyzszy prog porzadkuje selekcje", (5) ze istniejace sekcje (Po sezonach/
    ligach, kalibracja) przetrwaly bez zmian tresci."""

    def setUp(self):
        self.summary = {
            "n_bets": 3,
            "mean_clv": 0.01,
            "median_clv": -0.02,
            "beat_close_rate": 0.4,
            "hit_rate": 0.5,
            "by_season": {
                "2020/21": {"n": 3, "mean_clv": 0.01, "beat_close_rate": 0.4},
            },
            "by_league": {
                "E0": {"n": 3, "mean_clv": 0.01, "beat_close_rate": 0.4},
            },
        }
        self.model_score = ModelScore(n=10, log_loss=1.0, brier=0.5, rps=0.2, ece=0.01)
        self.market_score = MethodScore(method="power", n=10, log_loss=0.9, brier=0.4, rps=0.1, ece=0.005)
        self.baseline_summary = {
            "n_legs": 30,
            "mean_clv": 0.001,
            "median_clv": 0.0001,
            "beat_close_rate": 0.5,
            "by_season": {},
            "by_league": {},
        }

    def _render(self, n_matches=2):
        return _markdown(
            self.summary,
            "pl_standard",
            "power",
            0.05,
            self.model_score,
            self.market_score,
            n_matches,
            self.baseline_summary,
        )

    def test_distinct_match_row_present_next_to_bet_count(self):
        text = self._render(n_matches=2)
        lines = text.splitlines()
        idx_bets = lines.index("| zakladow | 3 |")
        self.assertEqual(lines[idx_bets + 1], "| meczow (odrebnych) | 2 |")

    def test_baseline_row_alongside_model_row_in_same_table(self):
        text = self._render()
        self.assertIn(
            "| model | 3 | +1.0000% | -2.0000% | 40.00% |", text,
            "wiersz modelu w tabeli porownawczej ma odpowiadac summary",
        )
        self.assertIn(
            "| baseline (bez modelu) | 30 | +0.1000% | +0.0100% | 50.00% |", text,
            "wiersz baseline w TEJ SAMEJ tabeli co model",
        )
        # obie linie musza wystapic pod WSPOLNYM naglowkiem tabeli, nie w
        # dwoch osobnych tabelach - inaczej "w tej samej tabeli" z briefu
        # nie jest spelnione.
        header_idx = text.index("| kto | n (zakladow / nog) |")
        model_idx = text.index("| model | 3 |")
        baseline_idx = text.index("| baseline (bez modelu) | 30 |")
        self.assertLess(header_idx, model_idx)
        self.assertLess(model_idx, baseline_idx)
        # nic nie moze wstawic sie miedzy naglowek/separator i te dwa wiersze
        between = text[header_idx:baseline_idx].splitlines()
        self.assertEqual(len(between), 3)  # naglowek, separator, wiersz modelu

    def test_mean_median_divergence_explained_as_ratio_artifact(self):
        text = self._render()
        self.assertIn("-100%", text)
        self.assertIn("nie ma gornej granicy", text)
        self.assertIn("nie dowod przewagi", text)

    def test_no_wording_implying_stricter_threshold_orders_selections_better(self):
        text = self._render()
        forbidden = (
            "porzadkuje selekcje",
            "porzadkuje selekcje sensownie",
            "lepsze porzadkowanie",
            "wyzszy prog daje lepszy",
            "EV_net koreluje z jakoscia ceny",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, text)

    def test_baseline_table_has_explicit_verdict_pointing_at_median_and_beat_close(self):
        """Brief taska: tabela porownawcza (model vs baseline) sama w sobie nie
        moze prowadzic czytelnika skanujacego tylko kolumne `sredni CLV` do
        blednego wniosku. Raport musi jawnie powiedziec, ktore miary
        rozstrzygaja (mediana CLV i pobicie zamkniecia, NIE srednia) i w ktora
        strone wypada porownanie na tych dwoch miarach."""
        text = self._render()
        header_idx = text.index("| kto | n (zakladow / nog) |")
        verdict_markers = ("mediana CLV", "pobito zamkniecie", "gorzej niz baseline")
        # werdykt musi wskazywac WLASNIE te dwie miary, a nie `sredni CLV`
        for marker in verdict_markers:
            self.assertIn(marker, text)
        # werdykt musi siedziec w sekcji "Model kontra baseline", przed
        # tabela (czytelnik skanujacy kolumne `sredni CLV` musi go minac
        # zanim dotrze do liczb) - nie gdzies indziej w dokumencie.
        section_idx = text.index("## Model kontra baseline")
        verdict_idx = text.index("gorzej niz baseline")
        self.assertLess(section_idx, verdict_idx)
        self.assertLess(verdict_idx, header_idx)

    def test_season_table_has_margin_drift_caveat_before_table(self):
        """Nowa notka: spadek sredniego CLV w tabeli "Po sezonach" nie jest
        dowodem, ze rynek sie wyostrzyl - to artefakt dryfu marzy w kolumnach
        `PSH/PSD/PSA` (2.46% -> 5.08%, zamkniecie stabilne ~3.9%). Notka musi
        stac PRZED tabela "Po sezonach" (czytelnik trafia na nia zanim
        zobaczy liczby) i NIE moze pojawic sie przy tabeli "Po ligach", ktora
        tego problemu nie ma."""
        text = self._render()
        self.assertIn("2.46%", text)
        self.assertIn("5.08%", text)
        self.assertIn("3.9%", text)
        self.assertIn("wniosek bledny", text)
        self.assertIn("nie wolno czytac jako trendu", text)
        self.assertIn("PSH/PSD/PSA", text)
        # przyczyna musi byc nazwana wprost jako dryf marzy w tych kolumnach,
        # nie cokolwiek innego (np. rzekoma poprawa modelu/typera) - to jest
        # dokladnie ta fraza, ktora wiaze liczby 2.46%/5.08% z marza, a nie
        # z jakims innym mechanizmem.
        self.assertIn("marza wliczona w te kolumny", text)

        section_idx = text.index("\n## Po sezonach\n")
        caveat_idx = text.index("2.46%")
        table_idx = text.index("| klucz | n | sredni CLV | pobito zamkniecie |")
        self.assertLess(section_idx, caveat_idx)
        self.assertLess(caveat_idx, table_idx)

        # notka jest specyficzna dla sezonow - nie ma jej przy "Po ligach"
        leagues_idx = text.index("\n## Po ligach\n")
        after_leagues = text[leagues_idx:]
        self.assertNotIn("2.46%", after_leagues)
        self.assertNotIn("5.08%", after_leagues)

    def test_pre_existing_sections_survive_with_unchanged_content(self):
        text = self._render()
        self.assertIn("\n## Po sezonach\n", text)
        self.assertIn("\n## Po ligach\n", text)
        self.assertIn("\n## Kalibracja: model kontra rynek (zamkniecie)\n", text)
        self.assertIn("| 2020/21 | 3 | +1.0000% | 40.00% |", text)
        self.assertIn("| E0 | 3 | +1.0000% | 40.00% |", text)
        self.assertIn(
            "| model | 10 | 1.00000 | 0.50000 | 0.20000 | 0.01000 |", text
        )
        self.assertIn(
            "| rynek (`power`, zamkniecie) | 10 | 0.90000 | 0.40000 | 0.10000 | 0.00500 |", text
        )


if __name__ == "__main__":
    unittest.main()
