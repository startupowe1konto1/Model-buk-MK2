from pathlib import Path

from model_buk.live_adjustment import apply_current_observations
from model_buk.live_provider import ApiFootballClient
from model_buk.multimarket import MultiMarketEngine, rebuild_markets_from_expected
from model_buk.odds_parser import normalize_odds, rank_opportunities


def test_multimarket_prices_multiple_groups(multileague_history_path):
    engine = MultiMarketEngine(multileague_history_path)
    analysis = engine.analyze("EPL", "2026-09-15 20:00", "Arsenal", "Chelsea")
    groups = {m["group"] for m in analysis["markets"]}
    assert {"result", "btts", "goals", "corners", "shots", "sot", "cards"}.issubset(groups)
    assert analysis["expected"]["goals"]["total"] > 0
    assert analysis["expected"]["corners"]["total"] > 0
    for row in analysis["markets"]:
        assert 0 < row["probability"] < 1
        assert row["fair_odds"] > 1


def test_current_observations_change_expected_counts_reproducibly(multileague_history_path):
    engine = MultiMarketEngine(multileague_history_path)
    analysis = engine.analyze("EPL", "2026-09-15", "Arsenal", "Chelsea")
    home = [{"goals_for": 3, "goals_against": 0, "shots_for": 18, "shots_against": 8, "sot_for": 7, "sot_against": 2, "corners_for": 8, "corners_against": 3, "cards_for": 1, "cards_against": 2}] * 5
    away = [{"goals_for": 1, "goals_against": 2, "shots_for": 9, "shots_against": 16, "sot_for": 2, "sot_against": 6, "corners_for": 3, "corners_against": 7, "cards_for": 2, "cards_against": 3}] * 5
    adjusted = apply_current_observations(analysis, home, away)
    adjusted["markets"] = rebuild_markets_from_expected(adjusted)
    assert adjusted["current_data"]["used_in_model"] is True
    assert adjusted["expected"]["goals"]["home"] > analysis["expected"]["goals"]["home"]
    assert len(adjusted["markets"]) == len(analysis["markets"])


def test_api_football_offline_mode_is_explicit(monkeypatch):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    client = ApiFootballClient(api_key="")
    assert client.connected is False
    assert client.status()["connected"] is False


def test_odds_parser_and_value_ranking():
    model = [
        {"market_key": "goals.total.over.2.5", "label": "Powyżej 2.5", "group": "goals", "threshold": 2.5, "side": "over", "probability": 0.60, "fair_odds": 1/0.60},
        {"market_key": "goals.total.under.2.5", "label": "Poniżej 2.5", "group": "goals", "threshold": 2.5, "side": "under", "probability": 0.40, "fair_odds": 2.5},
    ]
    raw = [
        {"bookmaker_id": 1, "bookmaker": "TestBook", "bet_id": 5, "bet": "Goals Over/Under", "value": "Over 2.5", "odd": 2.00, "update": "x"},
        {"bookmaker_id": 1, "bookmaker": "TestBook", "bet_id": 5, "bet": "Goals Over/Under", "value": "Under 2.5", "odd": 1.80, "update": "x"},
    ]
    assert len(normalize_odds(raw)) == 2
    opportunities, compared = rank_opportunities(
        model, raw, min_edge=0.01, min_ev=0.01, prefer_polish=False,
        tax_multiplier=1.0, allow_bets=True,
    )
    assert compared
    over = next(x for x in compared if x["market_key"] == "goals.total.over.2.5")
    assert over["devig_available"] is True
    assert over["ev"] > 0
    assert opportunities


def test_three_way_result_devig():
    model = [
        {"market_key": "result.home", "label": "Home", "group": "result", "threshold": None, "side": "home", "probability": 0.50, "fair_odds": 2.0},
        {"market_key": "result.draw", "label": "Draw", "group": "result", "threshold": None, "side": "draw", "probability": 0.25, "fair_odds": 4.0},
        {"market_key": "result.away", "label": "Away", "group": "result", "threshold": None, "side": "away", "probability": 0.25, "fair_odds": 4.0},
    ]
    raw = [
        {"bookmaker_id": 1, "bookmaker": "Book", "bet_id": 1, "bet": "Match Winner", "value": "Home", "odd": 2.20, "update": "x"},
        {"bookmaker_id": 1, "bookmaker": "Book", "bet_id": 1, "bet": "Match Winner", "value": "Draw", "odd": 3.50, "update": "x"},
        {"bookmaker_id": 1, "bookmaker": "Book", "bet_id": 1, "bet": "Match Winner", "value": "Away", "odd": 3.40, "update": "x"},
    ]
    _, compared = rank_opportunities(
        model, raw, min_edge=1.0, min_ev=1.0, prefer_polish=False,
        tax_multiplier=1.0, allow_bets=True,
    )
    assert len(compared) == 3
    assert all(x["devig_available"] for x in compared)
    assert abs(sum(x["market_probability"] for x in compared) - 1.0) < 1e-9


def test_unvalidated_multimarket_never_emits_bet_by_default():
    model = [{"market_key": "btts.yes", "label": "BTTS", "group": "btts", "threshold": None, "side": "yes", "probability": 0.90, "fair_odds": 1 / 0.90}]
    raw = [{"bookmaker_id": 1, "bookmaker": "Book", "bet_id": 8, "bet": "BTTS", "value": "Yes", "odd": 2.0, "update": "x"}]
    opportunities, compared = rank_opportunities(model, raw, tax_multiplier=0.88)
    assert opportunities == []
    assert compared[0]["decision"] == "NO PREDICTION"
