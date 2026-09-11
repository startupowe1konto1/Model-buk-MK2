import datetime
import unittest

from backtest.gate import TAU, ev_net
from backtest.loader import Match, season_of
from backtest.metrics import brier, ece, log_loss, outcome_index, rps
from backtest.walkforward import (
    ModelScore,
    Prediction,
    Selection,
    WalkForwardResult,
    features,
    run,
    score_predictions,
    seasons,
    summarize,
)


def _m(year, month, day, home, away, ftr, o_open=(2.0, 3.5, 4.0), o_close=(2.0, 3.5, 4.0)):
    return Match(
        div="E0",
        date=datetime.date(year, month, day),
        home=home,
        away=away,
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


def _dated(n, ftr, probs, start=datetime.date(2000, 1, 1)):
    """n predykcji z tym samym `ftr` i `probs`, na kolejnych, poprawnych
    datach (datetime.timedelta zamiast recznego zliczania dni w miesiacu -
    n moze przekraczac dlugosc jednego miesiaca bez psucia kalendarza)."""
    out = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        m = Match(div="E0", date=d, home="A", away="B", ftr=ftr,
                   o_open=(2.0, 3.5, 4.0), o_close=(2.0, 3.5, 4.0))
        out.append(Prediction(match=m, probs=probs))
    return out


def _synthetic(n_seasons=4):
    """Liga, w ktorej 'Silny' zawsze wygrywa u siebie z 'Slaby'."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 21):
            out.append(_m(year, 9, day, "Silny", "Slaby", "H"))
            out.append(_m(year, 10, day, "Slaby", "Silny", "A"))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


def _synthetic_moving(n_seasons=4):
    """Jak _synthetic, ale linia sie rusza: o_open != o_close na kazdej
    selekcji. Potrzebne wszedzie, gdzie test ma odroznic cene otwarcia od
    ceny zamkniecia - przy rownych kursach taki test nie pilnuje niczego."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 21):
            out.append(_m(year, 9, day, "Silny", "Slaby", "H",
                          o_open=(2.00, 3.50, 4.00), o_close=(1.70, 3.80, 5.00)))
            out.append(_m(year, 10, day, "Slaby", "Silny", "A",
                          o_open=(4.00, 3.50, 2.00), o_close=(5.00, 3.80, 1.70)))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


class TestSeasons(unittest.TestCase):
    def test_sorted_and_unique(self):
        s = seasons(_synthetic(3))
        self.assertEqual(s, ["2014/15", "2015/16", "2016/17"])


class TestFeatures(unittest.TestCase):
    def test_intercept_first_and_scaled_elo(self):
        self.assertEqual(features(400.0), [1.0, 1.0])
        self.assertEqual(features(0.0), [1.0, 0.0])


class TestRun(unittest.TestCase):
    def test_returns_walkforwardresult_of_selections(self):
        result = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertIsInstance(result, WalkForwardResult)
        self.assertTrue(result.selections, "fixture nie wyprodukowal zadnej selekcji")
        self.assertTrue(all(isinstance(s, Selection) for s in result.selections))

    def test_first_seasons_are_never_bet(self):
        """Przy min_train_seasons=2 nic z dwoch pierwszych sezonow nie moze
        trafic do wynikow - to nie sa dane testowe, tylko treningowe."""
        result = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0, min_train_seasons=2)
        self.assertTrue(result.selections, "fixture nie wyprodukowal zadnej selekcji")
        for s in result.selections:
            self.assertGreaterEqual(s.match.date.year, 2016)

    def test_no_selection_uses_closing_odds_as_price(self):
        """Fixture MUSI miec o_open != o_close, inaczej test nie odroznia ceny
        otwarcia od zamkniecia i przechodzi takze wtedy, gdy kod bierze zaklad
        po kursie zamkniecia. Zamkniecie sluzy wylacznie do liczenia CLV."""
        result = run(_synthetic_moving(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertTrue(result.selections, "fixture nie wyprodukowal zadnej selekcji")
        for s in result.selections:
            idx = ("H", "D", "A").index(s.outcome)
            self.assertAlmostEqual(s.odds, s.match.o_open[idx], places=12)
            self.assertNotAlmostEqual(s.odds, s.match.o_close[idx], places=6)

    def test_model_learns_the_synthetic_edge(self):
        """W lidze, gdzie Silny zawsze wygrywa, model musi to zauwazyc."""
        result = run(_synthetic(5), "proportional", TAU["betclic_notax"], ev_min=0.0)
        home_silny = [s for s in result.selections if s.match.home == "Silny" and s.outcome == "H"]
        self.assertTrue(home_silny)
        self.assertGreater(sum(s.p_model for s in home_silny) / len(home_silny), 0.6)

    def test_clv_is_zero_when_line_does_not_move(self):
        result = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertTrue(result.selections, "fixture nie wyprodukowal zadnej selekcji")
        for s in result.selections:
            self.assertAlmostEqual(s.clv, 0.0, places=9)

    def test_clv_is_positive_when_line_moves_toward_the_pick(self):
        """Zamkniecie krotsze na gospodarzu = rynek przyszedl po nas."""
        matches = _synthetic_moving(4)
        result = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.0)
        picks = [s for s in result.selections if s.outcome in ("H", "A")]
        self.assertTrue(picks)
        self.assertGreater(sum(s.clv for s in picks) / len(picks), 0.0)


class TestSelectionFieldsMatchGateInputs(unittest.TestCase):
    """Selection musi ujawniac, co NAPRAWDE dostala bramka - inaczej pomylka
    w okablowaniu wejsc decide() (np. cena zamkniecia zamiast otwarcia, albo
    prawdopodobienstwo rynkowe po kickoffie) jest niewidoczna dla wynikow.
    Fixture MUSI miec o_open != o_close (patrz uwaga przy _synthetic_moving)."""

    def test_ev_net_and_edge_are_consistent_with_recorded_odds_and_market(self):
        tau = TAU["betclic_notax"]
        result = run(_synthetic_moving(4), "proportional", tau, ev_min=0.0)
        self.assertTrue(result.selections, "fixture nie wyprodukowal zadnej selekcji")
        for s in result.selections:
            self.assertAlmostEqual(s.ev_net, ev_net(s.p_model, s.odds, tau), places=12)
            self.assertAlmostEqual(s.edge, s.p_model - s.p_market_open, places=12)


class TestNoOwnResultLeakageIntoPrediction(unittest.TestCase):
    """Predykcja meczu nie moze zalezec od jego wlasnego wyniku. Dwie fixture
    identyczne we wszystkim oprocz ftr JEDNEGO meczu - ostatniego chronologicznie
    w calym zbiorze, wiec ta zmiana nie moze wplynac na nic wczesniejszego w
    sekwencji - musza dac identyczne p_model dla tego meczu."""

    def test_last_match_p_model_is_independent_of_its_own_result(self):
        base = _synthetic_moving(5)
        target = base[-1]
        # Musi byc ostatni chronologicznie mecz calego zbioru, zeby zmiana
        # jego wyniku nie mogla wplynac na cokolwiek wczesniejsze w petli.
        self.assertEqual(target, max(base, key=lambda m: (m.date, m.div, m.home, m.away)))

        other_ftr = "D" if target.ftr != "D" else "H"
        variant = list(base)
        variant[-1] = target._replace(ftr=other_ftr)

        tau = TAU["betclic_notax"]
        sel_base = run(base, "proportional", tau, ev_min=0.0).selections
        sel_variant = run(variant, "proportional", tau, ev_min=0.0).selections

        def _for_target(sel):
            return [
                s for s in sel
                if s.match.home == target.home
                and s.match.away == target.away
                and s.match.date == target.date
            ]

        picks_base = _for_target(sel_base)
        picks_variant = _for_target(sel_variant)
        self.assertTrue(picks_base, "mecz docelowy nie wyprodukowal selekcji w bazowym przebiegu")
        self.assertTrue(picks_variant, "mecz docelowy nie wyprodukowal selekcji w zmienionym przebiegu")
        self.assertEqual(len(picks_base), 1)
        self.assertEqual(len(picks_variant), 1)
        self.assertEqual(picks_base[0].outcome, picks_variant[0].outcome)
        self.assertEqual(picks_base[0].p_model, picks_variant[0].p_model)


class TestSummarize(unittest.TestCase):
    def test_reports_core_fields(self):
        result = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        out = summarize(result.selections)
        for key in ("n_bets", "mean_clv", "median_clv", "beat_close_rate", "hit_rate", "by_season", "by_league"):
            self.assertIn(key, out)

    def test_empty_input_does_not_crash(self):
        out = summarize([])
        self.assertEqual(out["n_bets"], 0)


class TestPredictionsCoverEveryTestSeasonMatchNotJustGatedSelections(unittest.TestCase):
    """Wlasnosc do przypiecia z brief'u task 2: liczba zebranych predykcji ==
    liczba meczow w sezonach testowych, NIE liczba selekcji, ktore przeszly
    bramke. Kalibracja liczona wylacznie na selekcjach jest warunkowana
    bramka - to wlasnie ta rozbieznosc (predictions > selections, bo bramka
    z ev_min=0.05 odrzuca wiekszosc mozliwosci) ma tu byc widoczna."""

    def test_prediction_count_equals_test_season_match_count(self):
        # _synthetic_moving w kazdym sezonie testowym klaruje bramke NA
        # KAZDYM meczu (Silny zawsze wygrywa, model to widzi), wiec sam ten
        # fixture nie odroznia "liczby predykcji" od "liczby selekcji".
        # Dokladamy mecze nowych druzyn (nigdy niewystepujacych gdzie indziej,
        # wiec Elo dla nich jest neutralne) z rynkiem mocno faworyzujacym
        # gospodarza (o_open=(1.05, 10, 20)) - p_model tam wychodzi z modelu
        # wytrenowanego na Silny/Slaby wysokie (~0.99), ale nie na tyle
        # wysokie, zeby ev_net=p*tau*1.05-1 przekroczylo prog 0.05 (wychodzi
        # ok. 0.04) - te mecze MUSZA nie przejsc bramki, niezaleznie od
        # drobnych wahan numerycznych dopasowania.
        matches = _synthetic_moving(4)
        min_train_seasons = 2
        for i in range(4):
            year = 2014 + i
            for day in range(1, 6):
                matches.append(_m(year, 11, day, "Kruche", "Twarde", "H",
                                   o_open=(1.05, 10.0, 20.0), o_close=(1.05, 10.0, 20.0)))
        matches = sorted(matches, key=lambda m: (m.date, m.div, m.home, m.away))

        result = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.05,
                     min_train_seasons=min_train_seasons)
        test_seasons = set(seasons(matches)[min_train_seasons:])
        expected_n = sum(1 for m in matches if season_of(m.date) in test_seasons)

        self.assertTrue(result.predictions)
        self.assertEqual(len(result.predictions), expected_n)
        # I ta rozbieznosc jest wlasnie punktem: predictions != selections,
        # bo bramka odrzucila kazdy mecz Kruche-Twarde (n=10 w sezonach
        # testowych), a predictions je mimo to zebral.
        padding_predictions = [p for p in result.predictions if p.match.home == "Kruche"]
        padding_selections = [s for s in result.selections if s.match.home == "Kruche"]
        self.assertEqual(len(padding_predictions), 10)
        self.assertEqual(len(padding_selections), 0)
        self.assertNotEqual(len(result.predictions), len(result.selections))
        self.assertLess(len(result.selections), len(result.predictions))

    def test_every_prediction_is_a_distinct_match_in_test_seasons(self):
        """Kazdy mecz sezonu testowego wystepuje w predictions DOKLADNIE raz -
        to odroznia 'jedna predykcja na mecz' od 'jedna predykcja na noge
        (H/D/A)', co dawaloby 3x za duzo."""
        matches = _synthetic_moving(4)
        result = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.05, min_train_seasons=2)
        keys = [(p.match.date, p.match.home, p.match.away) for p in result.predictions]
        self.assertEqual(len(keys), len(set(keys)))

    def test_prediction_probs_sum_to_one(self):
        matches = _synthetic_moving(4)
        result = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.05, min_train_seasons=2)
        for p in result.predictions:
            self.assertIsInstance(p, Prediction)
            self.assertAlmostEqual(sum(p.probs), 1.0, places=9)


class TestScorePredictions(unittest.TestCase):
    """score_predictions() ma liczyc log-loss/Brier/RPS/ECE UZYWAJAC funkcji z
    backtest.metrics, nie wlasnej, rownoleglej arytmetyki. Testy pokazuja to
    wprost: przeliczaja recznie te same wzory na tym samym fixture i
    porownuja liczba do liczby."""

    def test_empty_input_does_not_crash(self):
        s = score_predictions([])
        self.assertEqual(s, ModelScore(0, 0.0, 0.0, 0.0, 0.0))

    def test_matches_manual_computation_via_metrics_module(self):
        preds = [
            Prediction(match=_m(2020, 1, 1, "A", "B", "H"), probs=(0.5, 0.3, 0.2)),
            Prediction(match=_m(2020, 1, 2, "C", "D", "D"), probs=(0.2, 0.5, 0.3)),
            Prediction(match=_m(2020, 1, 3, "E", "F", "A"), probs=(0.1, 0.2, 0.7)),
        ]
        want_ll = sum(log_loss(p.probs, p.match.ftr) for p in preds) / len(preds)
        want_br = sum(brier(p.probs, p.match.ftr) for p in preds) / len(preds)
        want_rp = sum(rps(p.probs, p.match.ftr) for p in preds) / len(preds)
        want_pairs = []
        for p in preds:
            k = outcome_index(p.match.ftr)
            want_pairs.extend((prob, 1 if i == k else 0) for i, prob in enumerate(p.probs))
        want_ece = ece(want_pairs)

        got = score_predictions(preds)
        self.assertEqual(got.n, 3)
        self.assertAlmostEqual(got.log_loss, want_ll, places=12)
        self.assertAlmostEqual(got.brier, want_br, places=12)
        self.assertAlmostEqual(got.rps, want_rp, places=12)
        self.assertAlmostEqual(got.ece, want_ece, places=12)

    def test_perfectly_calibrated_predictions_score_near_zero_ece(self):
        third = (1 / 3, 1 / 3, 1 / 3)
        preds = (
            _dated(100, "H", third, start=datetime.date(2020, 1, 1))
            + _dated(100, "D", third, start=datetime.date(2020, 6, 1))
            + _dated(100, "A", third, start=datetime.date(2020, 11, 1))
        )
        got = score_predictions(preds)
        self.assertLess(got.ece, 1e-9)

    def test_confidently_wrong_predictions_score_high_ece(self):
        # probs=(0.9, 0.05, 0.05) ale wynik zawsze "A": bucket 0.90 ma
        # mean_obs=0 (odchylenie 0.9), bucket 0.05 dzieli 100 par (D i A) po
        # polowie: 50 trafien / 50 pudel (odchylenie 0.45). Wazona srednia:
        # (50*0.9 + 100*0.45) / 150 = 0.6.
        preds = _dated(50, "A", (0.9, 0.05, 0.05))
        got = score_predictions(preds)
        self.assertAlmostEqual(got.ece, 0.6, places=9)
        self.assertGreater(got.ece, 0.3)


if __name__ == "__main__":
    unittest.main()
