"""The bookmakers' view of a match, used to estimate what a bet builder costs at a bookmaker.

Bet-builder prices are in no odds feed (no Polish bookmaker has an API and the builder engines are sold
to operators only), so a builder's price is estimated the way bookmakers build it: from their own view of
every leg.  Each count of the copula gets a market mean from the bookmakers' lines in the API-Football
feed: team and match lines of shots, shots on target, corners, fouls and cards; when only the match line
is offered, the split between the teams follows the "more shots / on target / corners" 1x2 markets
(else the model's split).  The builder's chance under those means comes from the same copula and the same
random draws as the model's chance, and the estimated price is

with the bookmaker's cut of the fair net winnings:

    price = 1 + (1 / market chance - 1) x (1 - cut)

(bookmakers keep proportionally more on long prices than on short ones; prior cut 30%, re-estimated from
the prices the user types in the app).  It is an estimate (about ±15%): it tells whether a builder is likely to be priced near its break-even
price, it does not replace the bookmaker's quote.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import optimize, stats

COUNT_STATS = ("shots", "sot", "corners", "fouls", "cards")
MORE_MARKET = {"shots": "more_shots", "sot": "more_sot", "corners": "more_corners"}
CUT_PRIOR = 0.30             # the bookmaker's cut of a builder's fair net winnings before any typed price is known
CUT_WEIGHT = 5.0             # the prior counts as this many typed prices
CUT_RANGE = (0.05, 0.60)

# bookmaker markets read besides the over/under lines (stats_today.parse_odds): name -> (stat, scope)
EXTRA_MARKETS = {
    "Shots.1x2": ("more_shots", "match"), "ShotOnTarget 1x2": ("more_sot", "match"), "Corners 1x2": ("more_corners", "match"),
    "Home Team Score a Goal": ("scores", "home"), "Away Team Score a Goal": ("scores", "away"),
    "Both Teams Score": ("btts", "match"),
}
EXTRA_SIDES = {"Home": "home", "Draw": "draw", "Away": "away", "Yes": "yes", "No": "no"}


def parse_extra_odds(payload: dict) -> list[dict]:
    """The 1x2 markets of shots, shots on target and corners (which team has more), each team scoring and
    both teams scoring, as rows like the over/under ones (line 0.0, side home/draw/away or yes/no)."""
    rows = []
    for block in payload.get("response", []):
        for bm in block.get("bookmakers", []):
            for bet in bm.get("bets", []):
                key = EXTRA_MARKETS.get(str(bet.get("name", "")).strip())
                if not key:
                    continue
                for v in bet.get("values", []):
                    side = EXTRA_SIDES.get(str(v.get("value", "")).strip())
                    try:
                        odd = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if side and odd > 1.0:
                        rows.append({"stat": key[0], "scope": key[1], "line": 0.0, "side": side, "bookmaker": bm["name"], "odd": odd})
    return rows


# ---------------------------------------------------------------------------- margin-free chances

def fair_lines(rows: list[dict], stat: str, scope: str) -> dict[float, float]:
    """Line -> the median over bookmakers of the margin-free chance of 'over' (both sides of a line needed)."""
    pairs: dict[tuple, dict] = {}
    for r in rows:
        if r["stat"] == stat and r["scope"] == scope and r["side"] in ("over", "under"):
            pairs.setdefault((float(r["line"]), r["bookmaker"]), {})[r["side"]] = float(r["odd"])
    by_line: dict[float, list[float]] = {}
    for (line, _), d in pairs.items():
        if "over" in d and "under" in d:
            io, iu = 1 / d["over"], 1 / d["under"]
            by_line.setdefault(line, []).append(io / (io + iu))
    return {line: float(np.median(v)) for line, v in by_line.items()}


def fair_outcomes(rows: list[dict], stat: str, scope: str, sides: tuple[str, ...]) -> tuple[float, ...] | None:
    """Margin-free chances of a market's outcomes (median over bookmakers quoting all of them)."""
    books: dict[str, dict] = {}
    for r in rows:
        if r["stat"] == stat and r["scope"] == scope and r["side"] in sides:
            books.setdefault(r["bookmaker"], {})[r["side"]] = float(r["odd"])
    fair = []
    for d in books.values():
        if all(s in d for s in sides):
            inv = [1 / d[s] for s in sides]
            fair.append([x / sum(inv) for x in inv])
    if not fair:
        return None
    med = np.median(np.array(fair), axis=0)
    return tuple(float(x / med.sum()) for x in med)


def q_over(mu: float, alpha: float, line: float) -> float:
    """Chance of 'over line' given that the bet is not void (whole lines push on the line itself)."""
    r = 1.0 / max(alpha, 1e-6)
    p = r / (r + mu)
    if abs(line - round(line)) < 1e-9:
        over = float(stats.nbinom.sf(int(round(line)), r, p))
        under = float(stats.nbinom.cdf(int(round(line)) - 1, r, p))
        return over / max(over + under, 1e-12)
    return float(stats.nbinom.sf(math.floor(line), r, p))


def fit_mean(lines: dict[float, float], alpha: float, start: float) -> float | None:
    """The NB2 mean whose chances of 'over' best match the bookmakers' margin-free chances."""
    if not lines:
        return None

    def loss(log_mu: float) -> float:
        mu = math.exp(log_mu)
        return sum((q_over(mu, alpha, line) - q) ** 2 for line, q in lines.items())

    lo, hi = math.log(max(start / 4, 0.05)), math.log(max(start * 4, 1.0))
    res = optimize.minimize_scalar(loss, bounds=(lo, hi), method="bounded", options={"xatol": 1e-4})
    return float(math.exp(res.x))


def _more_chance(total: float, share: float, a_h: float, a_a: float, rho: float, z: np.ndarray) -> float:
    """P(home count > away count) for a split of the total, under the copula's correlation."""
    u1 = stats.norm.cdf(z[:, 0])
    u2 = stats.norm.cdf(rho * z[:, 0] + math.sqrt(max(1 - rho * rho, 1e-9)) * z[:, 1])
    rh, ra = 1 / max(a_h, 1e-6), 1 / max(a_a, 1e-6)
    mh, ma = total * share, total * (1 - share)
    xh = stats.nbinom.ppf(u1, rh, rh / (rh + mh))
    xa = stats.nbinom.ppf(u2, ra, ra / (ra + ma))
    return float(np.mean(xh > xa))


def share_from_more(p_home_more: float, total: float, a_h: float, a_a: float, rho: float, start: float) -> float:
    """The home team's share of a total that gives the market's chance of 'home has more'."""
    z = np.random.default_rng(7).standard_normal((6000, 2))
    lo, hi = 0.05, 0.95
    for _ in range(18):
        mid = (lo + hi) / 2
        if _more_chance(total, mid, a_h, a_a, rho, z) < p_home_more:
            lo = mid
        else:
            hi = mid
    s = (lo + hi) / 2
    return s if 0.06 < s < 0.94 else start


# ---------------------------------------------------------------------------- market means

def market_means(model: dict[str, float], alphas: dict[str, float], rows: list[dict], dims: list[str],
                 corr: np.ndarray) -> tuple[dict[str, float], dict[str, str]]:
    """Market means of every count of the copula (goals stay as given: they already follow the match odds)
    and where each came from: 'linia' (the team's own lines), 'mecz' (the match line split between the
    teams), 'model' (no bookmaker line)."""
    out = dict(model)
    src = {d: "model" for d in model}
    idx = {d: i for i, d in enumerate(dims)}
    for s in COUNT_STATS:
        hk, ak = f"home_{s}", f"away_{s}"
        if hk not in model or ak not in model:
            continue
        mh, ma = model[hk], model[ak]
        th = fit_mean(fair_lines(rows, s, "home"), alphas[hk], mh)
        ta = fit_mean(fair_lines(rows, s, "away"), alphas[ak], ma)
        tt = fit_mean(fair_lines(rows, s, "total"), alphas.get(f"total_{s}", alphas[hk]), mh + ma)
        if th is not None:
            out[hk], src[hk] = th, "linia"
        if ta is not None:
            out[ak], src[ak] = ta, "linia"
        if tt is None or (th is not None and ta is not None):
            continue
        if th is not None:
            out[ak], src[ak] = max(tt - th, 0.15 * tt), "mecz"
        elif ta is not None:
            out[hk], src[hk] = max(tt - ta, 0.15 * tt), "mecz"
        else:
            share = mh / (mh + ma) if mh + ma > 0 else 0.5
            more = fair_outcomes(rows, MORE_MARKET[s], "match", ("home", "draw", "away")) if s in MORE_MARKET else None
            if more is not None and hk in idx and ak in idx:
                share = share_from_more(more[0], tt, alphas[hk], alphas[ak], float(corr[idx[hk], idx[ak]]), share)
            out[hk], out[ak] = tt * share, tt * (1 - share)
            src[hk] = src[ak] = "mecz"
    return out, src


# ---------------------------------------------------------------------------- legs and prices

def leg_mask(X: dict[str, np.ndarray], leg: dict) -> np.ndarray:
    """Which simulated matches a leg wins in (the same rules as the settlement)."""
    st, sc, side, line = leg["stat"], leg.get("scope"), leg["side"], leg.get("line")
    if st == "player_sot":
        return X[f"pl{leg['player_id']}_sot"] > line
    if side == "btts":
        return (X["home_goals"] >= 1) & (X["away_goals"] >= 1)
    if st in ("goals", "wynik"):
        h, a = X["home_goals"], X["away_goals"]
        mine, theirs = (h, a) if sc == "home" else (a, h)
        if side in ("more", "win"):
            return mine > theirs
        if side == "win_draw":
            return mine >= theirs
        val = h + a if sc == "total" else mine
        return val > line if side == "over" else val < line
    if side == "more":
        h, a = X[f"home_{st}"], X[f"away_{st}"]
        return h > a if sc == "home" else a > h
    val = X[f"home_{st}"] + X[f"away_{st}"] if sc == "total" else X[f"{sc}_{st}"]
    return val > line if side == "over" else val < line


def joint_chance(X: dict[str, np.ndarray], legs: list[dict]) -> float:
    return float(np.logical_and.reduce([leg_mask(X, l) for l in legs]).mean())


def estimated_price(p_market: float | None, cut: float) -> float | None:
    """The bookmaker's likely price: the fair net winnings (1/p - 1) less the cut."""
    if not p_market or not 0 < p_market < 1:
        return None
    return round(1.0 + (1.0 / p_market - 1.0) * (1.0 - cut), 2)


def calibrate_cut(pairs: list[tuple[float, float]]) -> tuple[float, int]:
    """The cut from typed prices: pairs of (typed price, market chance); each says cut = 1 - (price - 1) /
    (1/p - 1); the cuts are averaged with the prior (weight CUT_WEIGHT) and kept within CUT_RANGE."""
    cuts = []
    for price, p_mkt in pairs:
        if price and price > 1 and p_mkt and 0 < p_mkt < 1:
            cuts.append(min(max(1.0 - (price - 1.0) / (1.0 / p_mkt - 1.0), 0.0), 0.9))
    cut = (CUT_WEIGHT * CUT_PRIOR + sum(cuts)) / (CUT_WEIGHT + len(cuts))
    return float(min(max(cut, CUT_RANGE[0]), CUT_RANGE[1])), len(cuts)
