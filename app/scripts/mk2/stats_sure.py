"""Near-certain events ("pewniaczki"), a coupon built from them, and same-match bet builders.

What the 2025/26 walk-forward backtests say:

* eval_sure.py: events the model rates at 85-97% came in about one point less often than it said
  (their miss chance was about 10% higher), so those chances are corrected by TAIL_MISS.  A team's
  record in its last 10 matches added almost nothing to the model's chance, so it is shown, not
  used; a 10-of-10 streak on its own held in about 93% of next matches, 9 of 10 in 85-87%.
  Five events of about 90% from different matches all came in on 57% of days.
* eval_builder.py: a match's counts are strongly related (shots and shots on target, shots on
  target and goals), so a bet builder's chance comes from a Gaussian copula with the correlations
  in config/mk2/bb_korelacje.json.  Its chances matched reality within one or two points, while
  multiplying the legs' chances underrated three-leg builders by 7-9 points.

A single near-certain event never pays after the 12% stake tax at the prices bookmakers give
(at 1.10 it would need a 103% chance); in a coupon or a bet builder the tax is paid once.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
from scipy import optimize, stats

import stats_model as sm
import stats_picks as sp

TAX = 0.88
TAIL_MISS = 1.10            # miss chance x 1.10 for single events rated above 0.5 (eval_sure.py)
BB_MISS = 1.03              # bet builders were slightly better calibrated (eval_builder.py)
SURE_P = 0.85               # a "pewniaczek": at least this chance after the correction
SURE_STATS = ("shots", "sot", "corners")
# Teams expected to make fewer than this missed their near-certain thresholds 3-5 points more often
# than even the corrected model said (eval_sure.py by expected count), so they are left out.
MIN_MEAN = {"shots": 8.0, "sot": 2.5, "corners": 3.5}
LAST_N = 10
SURE_PER_DAY = 12
SURE_PER_MATCH = 2
COUPON_BAND = (1.8, 2.6)    # total odds of the coupon made of near-certain events
COUPON_LEGS = (3, 6)
COUPON_POOL = 12
BB_LEG_P = 0.80             # each count leg of a builder at least this likely on its own
BB_BAND = (0.45, 0.80)      # joint chance of a builder worth showing
BB_PER_DAY = 8
# S shots, C shots on target, M more shots than the opponent, G scores, W wins (90 minutes)
SAFE_TEMPLATES = ("SCM", "SCG", "SCMG")
RESULT_TEMPLATES = ("SCW", "SMW")
N_SIM = 20000
# Goals need far less shrinkage than shots: with the shots' team ridge (300) a 58% win came in 81% of
# the time; ridge 10 and a one-year half-life were calibrated (eval_builder.py) and had the best log-loss.
GOALS_FIT = {"half_life_days": 365.0, "ridge_team": 10.0}
CORR_FILE = Path(__file__).resolve().parents[2] / "config" / "mk2" / "bb_korelacje.json"
K_MAX = {"shots": 70, "sot": 35, "corners": 35, "goals": 15}
COUNTS = ("shots", "sot", "corners", "goals")


def corrected(p: float, miss: float = TAIL_MISS) -> float:
    """The model's chance with the backtest's correction (only above 0.5)."""
    return 1.0 - (1.0 - p) * miss if p > 0.5 else p


def sf_table(mu: float, alpha: float, kmax: int) -> np.ndarray:
    """P(X >= k) for k = 0..kmax under NB2(mu, alpha)."""
    r = 1.0 / max(alpha, 1e-6)
    k = np.arange(kmax + 1)
    return stats.nbinom.sf(k - 1, r, r / (r + mu))


def highest_k(mu: float, alpha: float, level: float, kmax: int = 70, miss: float = TAIL_MISS) -> tuple[int, float]:
    """The highest k >= 1 whose corrected chance P(X >= k) is at least ``level`` (0 when none)."""
    sf = sf_table(mu, alpha, kmax)
    best = (0, float("nan"))
    for k in range(1, kmax + 1):
        p = corrected(float(sf[k]), miss)
        if p < level:
            break
        best = (k, p)
    return best


def price_for(m: dict, stat: str, scope: str, k: int) -> tuple[float | None, str | None, float | None]:
    """Best price of 'over k-0.5' in the odds feed (stale outliers dropped as in stats_picks)."""
    for l in m.get("linie", []):
        if l["stat"] == stat and l["scope"] == scope and abs(l["line"] - (k - 0.5)) < 1e-9:
            prices = l.get("kursy_over") or {}
            if not prices:
                return None, None, l.get("rynek_over")
            median = float(np.median(list(prices.values())))
            fair = {bm: o for bm, o in prices.items() if o <= median * sp.MAX_PRICE_OVER_MEDIAN}
            bm = max(fair, key=fair.get)
            return float(fair[bm]), bm, l.get("rynek_over")
    return None, None, None


def team_history(df, stats_: tuple[str, ...] = SURE_STATS, n: int = LAST_N) -> dict:
    """Per team: its last n values of each statistic and of its matches' totals (oldest first)."""
    out: dict[int, dict[str, list[float]]] = {}
    for stat in stats_:
        col = sm.STATS[stat]
        d = df[df[col].notna()].sort_values("kickoff")
        both = d.groupby("fixture_id")[col].transform("count") == 2
        tot = d.groupby("fixture_id")[col].transform("sum").where(both)
        d = d.assign(_tot=tot)
        for team, g in d.groupby("team_id"):
            rec = out.setdefault(int(team), {})
            rec[stat] = [float(x) for x in g[col].tail(n)]
            rec[f"{stat}_total"] = [float(x) for x in g["_tot"].dropna().tail(n)]
    return out


def sure_events(analysis: dict, factors_of, alpha_of, history: dict) -> list[dict]:
    """Every match's near-certain events: per team and for the match, the highest 'at least k'
    of shots, shots on target and corners with a corrected chance of at least SURE_P."""
    out = []
    for m in analysis["mecze"]:
        if m.get("min_n", 0) < sp.MIN_TEAM_MATCHES:
            continue
        f = factors_of(m)
        for scope in ("home", "away", "total"):
            for stat in SURE_STATS:
                mu = m["srednie"][stat][scope] * f.get((stat, scope), 1.0)
                if scope != "total" and mu < MIN_MEAN[stat]:
                    continue
                k, p = highest_k(mu, alpha_of(m, stat, scope), SURE_P)
                if k < 1:
                    continue
                if scope == "total":
                    hs = [history.get(int(m["home_id"]), {}).get(f"{stat}_total", []),
                          history.get(int(m["away_id"]), {}).get(f"{stat}_total", [])]
                    hits = min(sum(x >= k for x in h) for h in hs)
                    n = min(len(h) for h in hs)
                    last = []
                else:
                    last = history.get(int(m["home_id" if scope == "home" else "away_id"]), {}).get(stat, [])
                    hits, n = sum(x >= k for x in last), len(last)
                odd, bm, q = price_for(m, stat, scope, k)
                out.append({
                    "fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", "start": m["start"],
                    "day": m["start"][:10], "stat": stat, "scope": scope, "line": k - 0.5, "side": "over", "k": k,
                    "opis": sp.describe(m, stat, scope, "over", k - 0.5),
                    "zaklad": f"powyżej {k - 0.5:.1f}".replace(".", ","),
                    "p_model": round(p, 4), "kurs": odd, "bukmacher": bm,
                    "p_rynek": None if q is None else round(q, 4), "kurs_uczciwy": round(1 / p, 3),
                    "p_razy_kurs": None if odd is None else round(p * odd, 4),
                    "rekord": [int(hits), int(n)], "ostatnie": [int(x) for x in last], "oczekiwane": round(mu, 2),
                })
    # how demanding the threshold is: k against the day's average expectation for that count
    by_key: dict[tuple, list[float]] = {}
    for e in out:
        by_key.setdefault((e["day"], e["stat"], e["scope"]), []).append(e["oczekiwane"])
    for e in out:
        avg = float(np.mean(by_key[(e["day"], e["stat"], e["scope"])]))
        e["sila"] = round(e["k"] / avg, 3) if avg > 0 else 0.0
    return out


def pick_sure(events: list[dict]) -> dict[str, list[dict]]:
    """The day's list: events with a price at or above the fair one first (best value first), then
    the most demanding ones; at most SURE_PER_MATCH per match and SURE_PER_DAY per day."""
    days: dict[str, list[dict]] = {}
    for e in events:
        days.setdefault(e["day"], []).append(e)
    out = {}
    for day, lst in days.items():
        lst = sorted(lst, key=lambda e: (-(e["p_razy_kurs"] or 0) if (e["p_razy_kurs"] or 0) >= 1.0 else 0, -e["sila"]))
        chosen, per = [], {}
        for e in lst:
            if per.get(e["fixture_id"], 0) >= SURE_PER_MATCH:
                continue
            per[e["fixture_id"]] = per.get(e["fixture_id"], 0) + 1
            chosen.append(e)
            if len(chosen) >= SURE_PER_DAY:
                break
        out[day] = chosen
    return out


LEG_KEYS = ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis", "zaklad", "kurs", "bukmacher", "p_model",
            "p_rynek")


def sure_coupon(events: list[dict], band: tuple[float, float] = COUPON_BAND) -> dict | None:
    """The best coupon of near-certain events from different matches: total odds within ``band``,
    every leg priced at least fairly, the most expected profit after tax (which must be positive)."""
    best_per_match: dict[int, dict] = {}
    for e in events:
        # priced at least fairly, and not further from the market than the picks may be (stats_picks.MAX_EDGE)
        if e["kurs"] and (e["p_razy_kurs"] or 0) >= 1.0 and (e["p_rynek"] is None or e["p_model"] - e["p_rynek"] <= sp.MAX_EDGE):
            cur = best_per_match.get(e["fixture_id"])
            if cur is None or e["p_razy_kurs"] > cur["p_razy_kurs"]:
                best_per_match[e["fixture_id"]] = e
    pool = sorted(best_per_match.values(), key=lambda e: -e["p_razy_kurs"])[:COUPON_POOL]
    best, n_ok = None, 0
    for n in range(COUPON_LEGS[0], COUPON_LEGS[1] + 1):
        for combo in itertools.combinations(pool, n):
            odd = math.prod(e["kurs"] for e in combo)
            if not band[0] <= odd <= band[1]:
                continue
            p = math.prod(e["p_model"] for e in combo)
            ev_tax = p * odd * TAX - 1
            if ev_tax <= 0:
                continue
            n_ok += 1
            if best is None or ev_tax > best["ev_podatek"]:
                best = {"legs": [{k: e.get(k) for k in LEG_KEYS} for e in combo], "kurs": round(odd, 2),
                        "p_model": round(p, 4), "p_rynek": round(math.prod((e["p_rynek"] or 1 / e["kurs"]) for e in combo), 4),
                        "ev": round(p * odd - 1, 4), "ev_podatek": round(ev_tax, 4)}
    if best:
        best["n_kombinacji"] = n_ok
        n_legs = len(best["legs"])
        best["why"] = (f"{n_legs} {'prawie pewne zdarzenia' if n_legs < 5 else 'prawie pewnych zdarzeń'} z różnych meczów, "
                       f"łączny kurs {_pl2(best['kurs'])}. "
                       f"Każde ma wg modelu co najmniej {round(SURE_P * 100)}% (po poprawce z historii) i kurs nie niższy niż "
                       f"uczciwy. Szansa całego kuponu wg modelu {round(best['p_model'] * 100)}%, oczekiwany zysk z podatkiem "
                       f"{round(best['ev_podatek'] * 100):+d}% stawki. Największy oczekiwany zysk spośród {n_ok} takich kuponów "
                       f"o łącznym kursie {_pl1(band[0])}–{_pl1(band[1])} tego dnia.")
    return best


def _pl1(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _pl2(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


# ---------------------------------------------------------------------------- bet builders

def load_corr() -> tuple[list[str], np.ndarray]:
    d = json.loads(CORR_FILE.read_text(encoding="utf-8"))
    return d["wymiary"], np.array(d["korelacje"], dtype=float)


def _cdf(mu: float, alpha: float, kmax: int) -> np.ndarray:
    r = 1.0 / max(alpha, 1e-6)
    c = stats.nbinom.cdf(np.arange(kmax + 1), r, r / (r + mu))
    c[-1] = 1.0
    return c


def simulate(means: dict[str, float], alphas: dict[str, float], dims: list[str], corr: np.ndarray,
             n: int = N_SIM, seed: int = 0) -> dict[str, np.ndarray]:
    """Joint draws of a match's counts: Gaussian copula ``corr`` over NB2 margins."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, len(dims))) @ np.linalg.cholesky(corr).T
    u = stats.norm.cdf(z)
    return {d: np.searchsorted(_cdf(means[d], alphas[d], K_MAX[d.split("_", 1)[1]]), u[:, i]) for i, d in enumerate(dims)}


def goals_from_market(mu_h: float, mu_a: float, p_home: float, p_away: float) -> tuple[float, float]:
    """Goal means whose Poisson scores give the bookmakers' home-win and away-win chances (the match
    winner market), starting from the model's; the draw follows from the two."""
    k = np.arange(16)

    def loss(x: np.ndarray) -> float:
        j = np.outer(stats.poisson.pmf(k, math.exp(x[0])), stats.poisson.pmf(k, math.exp(x[1])))
        return (np.tril(j, -1).sum() - p_home) ** 2 + (np.triu(j, 1).sum() - p_away) ** 2

    res = optimize.minimize(loss, [math.log(max(mu_h, 0.05)), math.log(max(mu_a, 0.05))], method="Nelder-Mead",
                            options={"xatol": 1e-4, "fatol": 1e-12, "maxiter": 400})
    return float(math.exp(res.x[0])), float(math.exp(res.x[1]))


def builders_for_match(m: dict, means: dict[str, float], alphas: dict[str, float], dims: list[str],
                       corr: np.ndarray) -> list[dict]:
    """Bet builders for both teams of a match: shots, shots on target, more shots than the opponent,
    scoring, winning (90 minutes), each count leg at least BB_LEG_P likely on its own."""
    X = simulate(means, alphas, dims, corr, seed=int(m["fixture_id"]) % (2 ** 31))
    out = []
    for team, opp in (("home", "away"), ("away", "home")):
        tn = m["gospodarz"] if team == "home" else m["gosc"]
        on = m["gosc"] if team == "home" else m["gospodarz"]
        if means[f"{team}_shots"] < MIN_MEAN["shots"] or means[f"{team}_sot"] < MIN_MEAN["sot"]:
            continue
        a, _ = highest_k(means[f"{team}_shots"], alphas[f"{team}_shots"], BB_LEG_P, K_MAX["shots"], BB_MISS)
        b, _ = highest_k(means[f"{team}_sot"], alphas[f"{team}_sot"], BB_LEG_P, K_MAX["sot"], BB_MISS)
        if a < 1 or b < 1:
            continue
        legs = {
            "S": (X[f"{team}_shots"] >= a, {"stat": "shots", "scope": team, "line": a - 0.5, "side": "over",
                                            "opis": f"{tn}: co najmniej {a} {sp.plural(a, 'shots')}"}),
            "C": (X[f"{team}_sot"] >= b, {"stat": "sot", "scope": team, "line": b - 0.5, "side": "over",
                                          "opis": f"{tn}: co najmniej {b} {sp.plural(b, 'sot')}"}),
            "M": (X[f"{team}_shots"] > X[f"{opp}_shots"], {"stat": "shots", "scope": team, "line": None, "side": "more",
                                                           "opis": f"{tn}: więcej strzałów niż {on}"}),
            "G": (X[f"{team}_goals"] >= 1, {"stat": "goals", "scope": team, "line": 0.5, "side": "over",
                                            "opis": f"{tn} strzeli gola"}),
            "W": (X[f"{team}_goals"] > X[f"{opp}_goals"], {"stat": "goals", "scope": team, "line": None, "side": "more",
                                                           "opis": f"{tn} wygra mecz (90 minut)"}),
        }
        for tpl in SAFE_TEMPLATES + RESULT_TEMPLATES:
            hit = np.logical_and.reduce([legs[c][0] for c in tpl])
            p = corrected(float(hit.mean()), BB_MISS)
            indep = math.prod(corrected(float(legs[c][0].mean()), BB_MISS) for c in tpl)
            out.append({"fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", "start": m["start"],
                        "day": m["start"][:10], "druzyna": tn, "scope": team, "szablon": tpl,
                        "legs": [{"fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", **legs[c][1],
                                  "p_model": round(corrected(float(legs[c][0].mean()), BB_MISS), 4)} for c in tpl],
                        "p_model": round(p, 4), "p_niezalezne": round(indep, 4),
                        "kurs_uczciwy": round(1 / p, 2) if p > 0 else None,
                        "kurs_min_z_podatkiem": round(1 / (p * TAX), 2) if p > 0 else None})
    return out


def pick_builders(cands: list[dict]) -> dict[str, list[dict]]:
    """Per day, within BB_BAND: the likeliest 'safe' builder of each match (counts, more shots,
    scoring) and the likeliest one with a win; the day's five likeliest safe ones and three
    likeliest with a win."""
    days: dict[str, list[dict]] = {}
    for c in cands:
        if BB_BAND[0] <= c["p_model"] <= BB_BAND[1]:
            c["rodzaj"] = "bezpieczny" if c["szablon"] in SAFE_TEMPLATES else "z_wynikiem"
            days.setdefault(c["day"], []).append(c)
    out = {}
    for day, lst in days.items():
        best: dict[tuple, dict] = {}
        for c in lst:
            key = (c["fixture_id"], c["rodzaj"])
            if key not in best or (c["p_model"], len(c["legs"])) > (best[key]["p_model"], len(best[key]["legs"])):
                best[key] = c
        ranked = sorted(best.values(), key=lambda c: -c["p_model"])
        chosen = ([c for c in ranked if c["rodzaj"] == "bezpieczny"][:5]
                  + [c for c in ranked if c["rodzaj"] == "z_wynikiem"][:BB_PER_DAY - 5])
        for c in chosen:
            c["why"] = why_builder(c)
        out[day] = chosen
    return out


def why_builder(c: dict) -> str:
    gap = round((c["p_model"] - c["p_niezalezne"]) * 100)
    return (f"Szansa wszystkich {len(c['legs'])} zdarzeń razem wg modelu: {round(c['p_model'] * 100)}%. Zdarzenia w jednym meczu "
            f"są powiązane (więcej strzałów to zwykle więcej celnych, więcej celnych to częściej gol), więc razem wchodzą "
            f"częściej niż wynikałoby z pomnożenia ich szans ({round(c['p_niezalezne'] * 100)}%, różnica {gap:+d} pkt). "
            f"Opłaca się od kursu {_pl2(c['kurs_min_z_podatkiem'])} (z podatkiem 12%); kurs sprawdź u bukmachera w opcji "
            f"„zbuduj zakład”.")


def run(analysis: dict, factors_of, fits: dict, goals_fit, df, leagues_shown: set[int], cups: set[int],
        now_iso: str) -> dict:
    """Everything for the app: near-certain events, their coupon and bet builders, per day, for the
    shown competitions' matches that have not started (started ones keep what was logged)."""
    def alpha_of(m: dict, stat: str, scope: str) -> float:
        return fits[stat].dispersion(scope, m["liga_id"] in cups)

    shown = {"mecze": [m for m in analysis["mecze"] if m["liga_id"] in leagues_shown and m["start"] > now_iso]}
    events = sure_events(shown, factors_of, alpha_of, team_history(df))
    by_day: dict[str, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["day"], []).append(e)
    coupons = {day: sure_coupon(lst) for day, lst in by_day.items()}

    dims, corr = load_corr()
    cands = []
    for m in shown["mecze"]:
        if m.get("min_n", 0) < sp.MIN_TEAM_MATCHES:
            continue
        cup = m["liga_id"] in cups
        f = factors_of(m)
        means, alphas = {}, {}
        for side, team, opp, home in (("home", m["home_id"], m["away_id"], True), ("away", m["away_id"], m["home_id"], False)):
            for s in ("shots", "sot", "corners"):
                means[f"{side}_{s}"] = m["srednie"][s][side] * f.get((s, side), 1.0)
                alphas[f"{side}_{s}"] = fits[s].dispersion(side, cup)
            means[f"{side}_goals"] = goals_fit.mean(int(team), int(opp), home, cup)
            alphas[f"{side}_goals"] = goals_fit.dispersion(side, cup)
        # the bookmakers' match-winner chances, when there are any, set the goal means
        if m.get("wynik_rynek"):
            p_h, _, p_a = m["wynik_rynek"]
            means["home_goals"], means["away_goals"] = goals_from_market(means["home_goals"], means["away_goals"], p_h, p_a)
            alphas["home_goals"] = alphas["away_goals"] = 1e-4
        for b in builders_for_match(m, means, alphas, dims, corr):
            b["gole_z_rynku"] = bool(m.get("wynik_rynek"))
            cands.append(b)
    return {"pewne": pick_sure(events), "kupon_pewne": {d: k for d, k in coupons.items() if k},
            "bb": pick_builders(cands)}
