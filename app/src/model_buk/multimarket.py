from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from model_buk.catalog import DIVISION_TO_LEAGUE, LEAGUES
from model_buk.distributions import nb_over_probability, nb_pmf
from model_buk.team_names import resolve_team_name


METRICS: dict[str, tuple[str, str]] = {
    "goals": ("FTHome", "FTAway"),
    "shots": ("HomeShots", "AwayShots"),
    "sot": ("HomeTarget", "AwayTarget"),
    "corners": ("HomeCorners", "AwayCorners"),
    "cards": ("HomeCards", "AwayCards"),
}

MARKET_LINES: dict[str, list[float]] = {
    "goals_total": [1.5, 2.5, 3.5, 4.5],
    "goals_team": [0.5, 1.5, 2.5, 3.5],
    "corners_total": [7.5, 8.5, 9.5, 10.5, 11.5],
    "corners_team": [2.5, 3.5, 4.5, 5.5, 6.5, 7.5],
    "shots_total": [19.5, 21.5, 23.5, 25.5, 27.5],
    "shots_team": [7.5, 9.5, 11.5, 13.5, 15.5],
    "sot_total": [5.5, 6.5, 7.5, 8.5, 9.5, 10.5],
    "sot_team": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
    "cards_total": [2.5, 3.5, 4.5, 5.5, 6.5],
    "cards_team": [0.5, 1.5, 2.5, 3.5],
}


@dataclass(frozen=True)
class MetricEstimate:
    metric: str
    expected_home: float
    expected_away: float
    alpha_home: float
    alpha_away: float
    home_for_rate: float
    home_against_rate: float
    away_for_rate: float
    away_against_rate: float
    league_home_rate: float
    league_away_rate: float
    home_sample: int
    away_sample: int
    source: str

    @property
    def expected_total(self) -> float:
        return self.expected_home + self.expected_away


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["MatchDate"] = pd.to_datetime(out["MatchDate"], errors="coerce")
    hy = pd.to_numeric(out.get("HomeYellow"), errors="coerce")
    hr = pd.to_numeric(out.get("HomeRed"), errors="coerce")
    ay = pd.to_numeric(out.get("AwayYellow"), errors="coerce")
    ar = pd.to_numeric(out.get("AwayRed"), errors="coerce")
    out["HomeCards"] = hy.fillna(0) + 2.0 * hr.fillna(0)
    out["AwayCards"] = ay.fillna(0) + 2.0 * ar.fillna(0)
    out.loc[hy.isna() & hr.isna(), "HomeCards"] = np.nan
    out.loc[ay.isna() & ar.isna(), "AwayCards"] = np.nan
    for home_col, away_col in METRICS.values():
        out[home_col] = pd.to_numeric(out.get(home_col), errors="coerce")
        out[away_col] = pd.to_numeric(out.get(away_col), errors="coerce")
    for col in ["HomeElo", "AwayElo", "Form3Home", "Form5Home", "Form3Away", "Form5Away"]:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[out["MatchDate"].notna()].sort_values("MatchDate").reset_index(drop=True)
    return out


@lru_cache(maxsize=4)
def load_multileague_history(path: str | Path) -> pd.DataFrame:
    return _clean_frame(pd.read_csv(path))


def _exp_weights(dates: pd.Series, as_of: pd.Timestamp, half_life_days: float = 180.0) -> np.ndarray:
    age = (as_of - pd.to_datetime(dates)).dt.total_seconds().clip(lower=0) / 86400.0
    return np.exp(-np.log(2.0) * age.to_numpy(dtype=float) / max(1.0, half_life_days))


def _weighted_rate(values: pd.Series, dates: pd.Series, as_of: pd.Timestamp, prior: float, prior_games: float = 6.0) -> tuple[float, int]:
    values = pd.to_numeric(values, errors="coerce")
    mask = values.notna() & pd.to_datetime(dates).notna()
    if not mask.any():
        return float(prior), 0
    vals = values.loc[mask].to_numpy(dtype=float)
    w = _exp_weights(pd.to_datetime(dates.loc[mask]), as_of)
    weighted_sum = float(np.sum(w * vals))
    weight = float(np.sum(w))
    rate = (weighted_sum + prior * prior_games) / (weight + prior_games)
    return float(rate), int(mask.sum())


def _league_rates(history: pd.DataFrame, metric: str, as_of: pd.Timestamp, lookback_days: int = 730) -> tuple[float, float, float]:
    home_col, away_col = METRICS[metric]
    recent = history[(history.MatchDate < as_of) & (history.MatchDate >= as_of - pd.Timedelta(days=lookback_days))]
    if len(recent) < 100:
        recent = history[history.MatchDate < as_of].tail(3000)
    h = float(pd.to_numeric(recent[home_col], errors="coerce").mean())
    a = float(pd.to_numeric(recent[away_col], errors="coerce").mean())
    if not np.isfinite(h):
        h = 1.0
    if not np.isfinite(a):
        a = 1.0
    return max(h, 0.05), max(a, 0.05), max((h + a) / 2.0, 0.05)


def _estimate_alpha(history: pd.DataFrame, metric: str, as_of: pd.Timestamp) -> tuple[float, float]:
    home_col, away_col = METRICS[metric]
    recent = history[(history.MatchDate < as_of) & (history.MatchDate >= as_of - pd.Timedelta(days=1460))]
    alphas: list[float] = []
    for col in (home_col, away_col):
        x = pd.to_numeric(recent[col], errors="coerce").dropna().to_numpy(dtype=float)
        if len(x) < 100:
            alphas.append(0.05)
            continue
        mu = max(float(np.mean(x)), 1e-6)
        var = float(np.var(x, ddof=1))
        alpha = max(0.001, (var - mu) / max(mu * mu, 1e-6))
        alphas.append(min(alpha, 1.5))
    return alphas[0], alphas[1]


def _team_rows(history: pd.DataFrame, team: str, venue: str, as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    before = history[history.MatchDate < as_of]
    if venue == "home":
        own = before[before.HomeTeam.eq(team)].copy()
        other = before[before.AwayTeam.eq(team)].copy()
    else:
        own = before[before.AwayTeam.eq(team)].copy()
        other = before[before.HomeTeam.eq(team)].copy()
    return own, other


def _blend_venue_and_all(venue_rate: float, venue_n: int, all_rate: float, all_n: int) -> float:
    if venue_n >= 8:
        return 0.72 * venue_rate + 0.28 * all_rate
    if venue_n >= 4:
        return 0.55 * venue_rate + 0.45 * all_rate
    return 0.35 * venue_rate + 0.65 * all_rate


def _elo_adjustment(history: pd.DataFrame, home_team: str, away_team: str, as_of: pd.Timestamp) -> tuple[float, float, dict[str, Any]]:
    before = history[history.MatchDate < as_of]
    home_last = pd.concat([
        before[before.HomeTeam.eq(home_team)][["MatchDate", "HomeElo"]].rename(columns={"HomeElo": "elo"}),
        before[before.AwayTeam.eq(home_team)][["MatchDate", "AwayElo"]].rename(columns={"AwayElo": "elo"}),
    ]).sort_values("MatchDate")
    away_last = pd.concat([
        before[before.HomeTeam.eq(away_team)][["MatchDate", "HomeElo"]].rename(columns={"HomeElo": "elo"}),
        before[before.AwayTeam.eq(away_team)][["MatchDate", "AwayElo"]].rename(columns={"AwayElo": "elo"}),
    ]).sort_values("MatchDate")
    h = float(home_last.elo.dropna().iloc[-1]) if home_last.elo.notna().any() else np.nan
    a = float(away_last.elo.dropna().iloc[-1]) if away_last.elo.notna().any() else np.nan
    if not np.isfinite(h) or not np.isfinite(a):
        return 1.0, 1.0, {"home_elo": None, "away_elo": None, "elo_diff": None}
    diff = np.clip(h - a, -400, 400)
    # Deliberately mild: ~ +/- 12% at a 200 Elo gap. This is a feature, not a subjective override.
    home_adj = float(np.exp(diff / 1700.0))
    away_adj = float(np.exp(-diff / 1700.0))
    return home_adj, away_adj, {"home_elo": round(h, 1), "away_elo": round(a, 1), "elo_diff": round(float(diff), 1)}


def _metric_estimate(history: pd.DataFrame, metric: str, home_team: str, away_team: str, as_of: pd.Timestamp) -> MetricEstimate:
    home_col, away_col = METRICS[metric]
    league_home, league_away, league_all = _league_rates(history, metric, as_of)

    h_home, h_away = _team_rows(history, home_team, "home", as_of)
    a_away, a_home = _team_rows(history, away_team, "away", as_of)

    # Home team: production at home and overall; concession at home and overall.
    h_for_home, h_for_home_n = _weighted_rate(h_home[home_col], h_home.MatchDate, as_of, league_home)
    h_for_away, h_for_away_n = _weighted_rate(h_away[away_col], h_away.MatchDate, as_of, league_all)
    h_against_home, h_against_home_n = _weighted_rate(h_home[away_col], h_home.MatchDate, as_of, league_away)
    h_against_away, h_against_away_n = _weighted_rate(h_away[home_col], h_away.MatchDate, as_of, league_all)

    # Away team: production away and overall; concession away and overall.
    a_for_away, a_for_away_n = _weighted_rate(a_away[away_col], a_away.MatchDate, as_of, league_away)
    a_for_home, a_for_home_n = _weighted_rate(a_home[home_col], a_home.MatchDate, as_of, league_all)
    a_against_away, a_against_away_n = _weighted_rate(a_away[home_col], a_away.MatchDate, as_of, league_home)
    a_against_home, a_against_home_n = _weighted_rate(a_home[away_col], a_home.MatchDate, as_of, league_all)

    h_for = _blend_venue_and_all(h_for_home, h_for_home_n, h_for_away, h_for_away_n)
    h_against = _blend_venue_and_all(h_against_home, h_against_home_n, h_against_away, h_against_away_n)
    a_for = _blend_venue_and_all(a_for_away, a_for_away_n, a_for_home, a_for_home_n)
    a_against = _blend_venue_and_all(a_against_away, a_against_away_n, a_against_home, a_against_home_n)

    # Geometric attack x opponent-suppression structure. Shrinkage above protects small samples.
    home_mu = league_home * np.sqrt(max(h_for / league_home, 0.05) * max(a_against / league_home, 0.05))
    away_mu = league_away * np.sqrt(max(a_for / league_away, 0.05) * max(h_against / league_away, 0.05))

    home_elo_adj, away_elo_adj, _ = _elo_adjustment(history, home_team, away_team, as_of)
    if metric in {"goals", "shots", "sot"}:
        home_mu *= home_elo_adj
        away_mu *= away_elo_adj
    elif metric == "corners":
        home_mu *= home_elo_adj ** 0.35
        away_mu *= away_elo_adj ** 0.35

    # Keep estimates in physically plausible ranges while remaining reproducible.
    caps = {"goals": 5.0, "shots": 30.0, "sot": 12.0, "corners": 14.0, "cards": 8.0}
    home_mu = float(np.clip(home_mu, 0.05, caps[metric]))
    away_mu = float(np.clip(away_mu, 0.05, caps[metric]))
    alpha_h, alpha_a = _estimate_alpha(history, metric, as_of)

    return MetricEstimate(
        metric=metric,
        expected_home=home_mu,
        expected_away=away_mu,
        alpha_home=alpha_h,
        alpha_away=alpha_a,
        home_for_rate=float(h_for),
        home_against_rate=float(h_against),
        away_for_rate=float(a_for),
        away_against_rate=float(a_against),
        league_home_rate=league_home,
        league_away_rate=league_away,
        home_sample=int(h_for_home_n + h_for_away_n),
        away_sample=int(a_for_away_n + a_for_home_n),
        source="historical_time_decay",
    )


def _convolved_over(home: MetricEstimate, line: float, max_count: int = 80) -> float:
    hp = nb_pmf(home.expected_home, home.alpha_home, max_count=max_count)
    ap = nb_pmf(home.expected_away, home.alpha_away, max_count=max_count)
    total = np.convolve(hp, ap)
    k = int(np.floor(line))
    return float(total[k + 1 :].sum())


def _market_row(key: str, label: str, group: str, line: float | None, side: str, p: float) -> dict[str, Any]:
    p = float(np.clip(p, 1e-8, 1 - 1e-8))
    return {
        "market_key": key,
        "label": label,
        "group": group,
        "threshold": line,
        "side": side,
        "probability": p,
        "fair_odds": 1.0 / p,
    }


def _count_markets(metric: str, estimate: MetricEstimate, home_team: str, away_team: str) -> list[dict[str, Any]]:
    total_key = f"{metric}_total"
    team_key = f"{metric}_team"
    rows: list[dict[str, Any]] = []
    for line in MARKET_LINES[total_key]:
        p_over = _convolved_over(estimate, line)
        rows.extend([
            _market_row(f"{metric}.total.over.{line}", f"Powyżej {line}", metric, line, "over", p_over),
            _market_row(f"{metric}.total.under.{line}", f"Poniżej {line}", metric, line, "under", 1.0 - p_over),
        ])
    for team_side, team, mu, alpha in (
        ("home", home_team, estimate.expected_home, estimate.alpha_home),
        ("away", away_team, estimate.expected_away, estimate.alpha_away),
    ):
        for line in MARKET_LINES[team_key]:
            p_over = nb_over_probability(mu, line, alpha)
            rows.extend([
                _market_row(f"{metric}.{team_side}.over.{line}", f"{team}: powyżej {line}", metric, line, "over", p_over),
                _market_row(f"{metric}.{team_side}.under.{line}", f"{team}: poniżej {line}", metric, line, "under", 1.0 - p_over),
            ])
    return rows


def _goal_special_markets(est: MetricEstimate, home_team: str, away_team: str) -> list[dict[str, Any]]:
    hp = nb_pmf(est.expected_home, est.alpha_home, max_count=12)
    ap = nb_pmf(est.expected_away, est.alpha_away, max_count=12)
    matrix = np.outer(hp, ap)
    p_home = float(np.tril(matrix, k=-1).sum())  # home goals index > away goals index after transpose logic below
    # np.outer rows=home, cols=away; lower triangle rows>cols = home win.
    p_draw = float(np.trace(matrix))
    p_away = float(np.triu(matrix, k=1).sum())
    p_btts = float(1.0 - hp[0] - ap[0] + hp[0] * ap[0])
    return [
        _market_row("result.home", f"{home_team} wygra", "result", None, "home", p_home),
        _market_row("result.draw", "Remis", "result", None, "draw", p_draw),
        _market_row("result.away", f"{away_team} wygra", "result", None, "away", p_away),
        _market_row("btts.yes", "Obie strzelą: TAK", "btts", None, "yes", p_btts),
        _market_row("btts.no", "Obie strzelą: NIE", "btts", None, "no", 1.0 - p_btts),
    ]


def _last_match_date(history: pd.DataFrame, team: str, as_of: pd.Timestamp) -> pd.Timestamp | None:
    rows = history[(history.MatchDate < as_of) & (history.HomeTeam.eq(team) | history.AwayTeam.eq(team))]
    return pd.Timestamp(rows.MatchDate.max()) if len(rows) else None


def _recent_points(history: pd.DataFrame, team: str, as_of: pd.Timestamp, n: int = 5) -> tuple[float, list[str]]:
    rows = history[(history.MatchDate < as_of) & (history.HomeTeam.eq(team) | history.AwayTeam.eq(team))].tail(n)
    points = 0
    form: list[str] = []
    for _, row in rows.iterrows():
        hg, ag = row.get("FTHome"), row.get("FTAway")
        if pd.isna(hg) or pd.isna(ag):
            continue
        home = row.HomeTeam == team
        gf, ga = (hg, ag) if home else (ag, hg)
        if gf > ga:
            points += 3; form.append("W")
        elif gf == ga:
            points += 1; form.append("D")
        else:
            form.append("L")
    ppg = points / max(len(form), 1)
    return float(ppg), form


class MultiMarketEngine:
    """Transparent multi-league baseline for broad market coverage.

    It is intentionally simpler than the trained EPL Corner champion. Its job in
    v0.5 is to provide reproducible, point-in-time prices across goals, corners,
    shots, SOT and cards while live/current context adapters are being added.
    """

    def __init__(self, history_path: str | Path):
        self.history_path = Path(history_path)
        self.history = load_multileague_history(self.history_path)

    def leagues(self) -> list[dict[str, Any]]:
        output = []
        for spec in LEAGUES.values():
            rows = self.history[self.history.Division.eq(spec.historical_division)]
            output.append({
                "code": spec.code,
                "name": spec.name,
                "country": spec.country,
                "matches": int(len(rows)),
                "history_from": str(rows.MatchDate.min().date()) if len(rows) else None,
                "history_through": str(rows.MatchDate.max().date()) if len(rows) else None,
            })
        return output

    def teams(self, league_code: str) -> list[str]:
        spec = LEAGUES[league_code]
        rows = self.history[self.history.Division.eq(spec.historical_division)]
        return sorted(set(rows.HomeTeam.dropna().astype(str)) | set(rows.AwayTeam.dropna().astype(str)))

    def resolve_team(self, league_code: str, name: str) -> tuple[str | None, float]:
        return resolve_team_name(name, self.teams(league_code))

    def analyze(self, league_code: str, date: str | pd.Timestamp, home_team: str, away_team: str) -> dict[str, Any]:
        if league_code not in LEAGUES:
            raise ValueError(f"Unsupported league: {league_code}")
        spec = LEAGUES[league_code]
        league = self.history[self.history.Division.eq(spec.historical_division)].copy()
        if league.empty:
            raise ValueError(f"No historical data for {spec.name}")
        when = pd.Timestamp(date)
        if when.tzinfo is not None:
            when = when.tz_convert("UTC").tz_localize(None)
        h, hs = resolve_team_name(home_team, self.teams(league_code))
        a, as_ = resolve_team_name(away_team, self.teams(league_code))
        if h is None:
            raise ValueError(f"Nie rozpoznano gospodarza '{home_team}' w {spec.name}")
        if a is None:
            raise ValueError(f"Nie rozpoznano gościa '{away_team}' w {spec.name}")
        if h == a:
            raise ValueError("Drużyny muszą być różne")

        estimates = {metric: _metric_estimate(league, metric, h, a, when) for metric in METRICS}
        markets: list[dict[str, Any]] = []
        markets.extend(_goal_special_markets(estimates["goals"], h, a))
        for metric in ("goals", "corners", "shots", "sot", "cards"):
            markets.extend(_count_markets(metric, estimates[metric], h, a))

        h_ppg, h_form = _recent_points(league, h, when)
        a_ppg, a_form = _recent_points(league, a, when)
        h_last = _last_match_date(league, h, when)
        a_last = _last_match_date(league, a, when)
        h_rest = (when.normalize() - h_last.normalize()).days if h_last is not None else None
        a_rest = (when.normalize() - a_last.normalize()).days if a_last is not None else None
        _, _, elo = _elo_adjustment(league, h, a, when)

        factors: list[dict[str, Any]] = [
            {
                "key": "elo",
                "label": "Siła długoterminowa (Elo)",
                "home": elo.get("home_elo"),
                "away": elo.get("away_elo"),
                "difference": elo.get("elo_diff"),
                "status": "model_input",
                "explanation": "Elo wpływa łagodnie na gole, strzały, SOT i częściowo rożne.",
            },
            {
                "key": "recent_form",
                "label": "Ostatnie wyniki",
                "home": {"ppg": round(h_ppg, 2), "form": h_form},
                "away": {"ppg": round(a_ppg, 2), "form": a_form},
                "status": "context",
                "explanation": "Forma jest pokazywana jako kontekst; count-rates są już ważone czasem w modelu.",
            },
            {
                "key": "rest",
                "label": "Odpoczynek",
                "home": h_rest,
                "away": a_rest,
                "unit": "dni",
                "status": "context",
                "explanation": "Na razie kontrola kontekstu; wpływ na probability zostanie włączony po walidacji OOS.",
            },
        ]

        for metric, est in estimates.items():
            factors.append({
                "key": f"{metric}_rates",
                "label": {
                    "goals": "Gole: tworzenie / dopuszczanie",
                    "shots": "Strzały: tworzenie / dopuszczanie",
                    "sot": "SOT: tworzenie / dopuszczanie",
                    "corners": "Rożne: tworzenie / dopuszczanie",
                    "cards": "Kartki: własne / przeciwnika",
                }[metric],
                "home": {"for": round(est.home_for_rate, 2), "against": round(est.home_against_rate, 2)},
                "away": {"for": round(est.away_for_rate, 2), "against": round(est.away_against_rate, 2)},
                "league": {"home": round(est.league_home_rate, 2), "away": round(est.league_away_rate, 2)},
                "status": "model_input",
                "explanation": "Time-decay + shrinkage + venue split + opponent concession.",
            })

        history_through = pd.Timestamp(league.MatchDate.max())
        staleness = max(0, int((when.normalize() - history_through.normalize()).days))
        min_sample = min(estimates["goals"].home_sample, estimates["goals"].away_sample)
        completeness = float(league[[x for pair in METRICS.values() for x in pair]].notna().mean().mean())
        quality = float(np.clip(55 + 20 * min(completeness, 1.0) + 20 * min(min_sample / 30, 1.0) - min(staleness, 180) * 0.08, 20, 98))

        return {
            "engine_version": "multimarket-baseline-v0.5",
            "league": {"code": spec.code, "name": spec.name, "country": spec.country},
            "fixture": {
                "date": str(when),
                "home_team": h,
                "away_team": a,
                "home_name_match": round(hs, 3),
                "away_name_match": round(as_, 3),
            },
            "data_quality": {
                "score": round(quality, 1),
                "history_through": str(history_through.date()),
                "staleness_days_vs_fixture": staleness,
                "historical_metric_completeness": round(completeness, 3),
                "warning": "Historical baseline is stale for current fixtures until the live provider contributes current-season observations." if staleness > 30 else None,
            },
            "expected": {
                metric: {
                    "home": round(est.expected_home, 3),
                    "away": round(est.expected_away, 3),
                    "total": round(est.expected_total, 3),
                    "alpha_home": round(est.alpha_home, 4),
                    "alpha_away": round(est.alpha_away, 4),
                }
                for metric, est in estimates.items()
            },
            "markets": markets,
            "factors": factors,
        }


def rebuild_markets_from_expected(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Reprice all supported markets after deterministic current-data adjustments."""
    expected = analysis["expected"]
    home_team = analysis["fixture"]["home_team"]
    away_team = analysis["fixture"]["away_team"]
    estimates: dict[str, MetricEstimate] = {}
    for metric, values in expected.items():
        estimates[metric] = MetricEstimate(
            metric=metric,
            expected_home=float(values["home"]),
            expected_away=float(values["away"]),
            alpha_home=float(values.get("alpha_home", 0.05)),
            alpha_away=float(values.get("alpha_away", 0.05)),
            home_for_rate=float(values.get("home", 0)),
            home_against_rate=float(values.get("away", 0)),
            away_for_rate=float(values.get("away", 0)),
            away_against_rate=float(values.get("home", 0)),
            league_home_rate=float(values.get("home", 0)),
            league_away_rate=float(values.get("away", 0)),
            home_sample=0,
            away_sample=0,
            source=str(values.get("source", "rebuild")),
        )
    markets: list[dict[str, Any]] = []
    markets.extend(_goal_special_markets(estimates["goals"], home_team, away_team))
    for metric in ("goals", "corners", "shots", "sot", "cards"):
        markets.extend(_count_markets(metric, estimates[metric], home_team, away_team))
    return markets
