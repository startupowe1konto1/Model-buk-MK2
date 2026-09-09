"""Walk-forward: trenuj na przeszlosci, przewiduj kolejny sezon, przesun okno.

Tu wchodzi albo nie wchodzi leakage, wiec kolejnosc operacji jest scisla:

1. Jedna tabela Elo na caly przebieg, karmiona chronologicznie.
2. Dla sezonu S: trenuj na WSZYSTKICH meczach wczesniejszych niz start S.
3. W sezonie S, mecz po meczu: NAJPIERW odczytaj elo.diff i policz P_model,
   POTEM wywolaj elo.update.
4. Model jest zamrozony na caly sezon S. Douczanie w trakcie sezonu oznaczaloby,
   ze mecz z 15 maja wplywa na predykcje meczu z 20 sierpnia tylko dlatego,
   ze petla akurat tak poszla.

Zaklad bierzemy ZAWSZE po cenie 'open'. Zamkniecie sluzy wylacznie do liczenia
CLV - uzycie go jako ceny transakcji to leakage wymieniony wprost w [[11]].
"""
import statistics
from collections import defaultdict
from typing import Dict, List, NamedTuple, Sequence

from backtest.clv import beat_close, clv_from_probs
from backtest.devig import METHODS
from backtest.elo import EloTable
from backtest.gate import decide
from backtest.loader import Match, season_of
from backtest.metrics import OUTCOMES, outcome_index
from backtest.model import fit, predict_proba


class Selection(NamedTuple):
    match: Match
    outcome: str
    p_model: float
    p_market_open: float
    p_market_close: float
    odds: float
    ev_net: float
    edge: float
    clv: float


def seasons(matches: Sequence[Match]) -> List[str]:
    return sorted({season_of(m.date) for m in matches})


def features(elo_diff: float) -> List[float]:
    return [1.0, elo_diff / 400.0]


def run(
    matches: Sequence[Match],
    devig_method: str,
    tau: float,
    ev_min: float = 0.05,
    min_train_seasons: int = 2,
) -> List[Selection]:
    devig = METHODS[devig_method]
    all_seasons = seasons(matches)
    by_season: Dict[str, List[Match]] = defaultdict(list)
    for m in matches:
        by_season[season_of(m.date)].append(m)

    elo = EloTable()
    train_X: List[List[float]] = []
    train_y: List[int] = []
    out: List[Selection] = []

    for i, season in enumerate(all_seasons):
        season_matches = by_season[season]
        if i < min_train_seasons:
            # sezon czysto treningowy: tylko karmimy Elo i zbior uczacy
            for m in season_matches:
                train_X.append(features(elo.diff(m.home, m.away)))
                train_y.append(outcome_index(m.ftr))
                elo.update(m.home, m.away, m.ftr)
            continue

        weights = fit(train_X, train_y, n_classes=3)  # zamrozony na caly sezon

        for m in season_matches:
            x = features(elo.diff(m.home, m.away))
            probs = predict_proba(weights, x)
            p_open = devig(m.o_open)
            p_close = devig(m.o_close)

            for idx, outcome in enumerate(OUTCOMES):
                d = decide(probs[idx], m.o_open[idx], tau, p_open[idx], ev_min=ev_min)
                if not d.bet:
                    continue
                out.append(
                    Selection(
                        match=m,
                        outcome=outcome,
                        p_model=probs[idx],
                        p_market_open=p_open[idx],
                        p_market_close=p_close[idx],
                        odds=m.o_open[idx],
                        ev_net=d.ev_net,
                        edge=d.edge,
                        clv=clv_from_probs(p_open[idx], p_close[idx]),
                    )
                )

            # dopiero teraz mecz staje sie przeszloscia
            train_X.append(x)
            train_y.append(outcome_index(m.ftr))
            elo.update(m.home, m.away, m.ftr)

    return out


def summarize(selections: Sequence[Selection]) -> Dict[str, object]:
    if not selections:
        return {
            "n_bets": 0,
            "mean_clv": 0.0,
            "median_clv": 0.0,
            "beat_close_rate": 0.0,
            "hit_rate": 0.0,
            "by_season": {},
            "by_league": {},
        }
    clvs = [s.clv for s in selections]
    wins = sum(1 for s in selections if s.match.ftr == s.outcome)

    def _group(key_fn):
        buckets: Dict[str, List[Selection]] = defaultdict(list)
        for s in selections:
            buckets[key_fn(s)].append(s)
        return {
            k: {
                "n": len(v),
                "mean_clv": statistics.mean(x.clv for x in v),
                "beat_close_rate": sum(1 for x in v if beat_close(x.p_market_open, x.p_market_close)) / len(v),
            }
            for k, v in sorted(buckets.items())
        }

    return {
        "n_bets": len(selections),
        "mean_clv": statistics.mean(clvs),
        "median_clv": statistics.median(clvs),
        "beat_close_rate": sum(1 for s in selections if beat_close(s.p_market_open, s.p_market_close)) / len(selections),
        "hit_rate": wins / len(selections),
        "by_season": _group(lambda s: season_of(s.match.date)),
        "by_league": _group(lambda s: s.match.div),
    }
