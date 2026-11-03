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

import functools
import itertools
import json
import math
from pathlib import Path

import numpy as np
from scipy import optimize, stats

import stats_market as mk
import stats_players as spl
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
SURE_PER_DAY = 0               # near-certain events are not shown any more (user, 2026-10-26)
SURE_PER_MATCH = 2
COUPON_BAND = (1.8, 2.6)    # total odds of the coupon made of near-certain events
COUPON_LEGS = (3, 6)
COUPON_POOL = 12
BB_LEG_P = 0.80             # each count leg of a builder at least this likely on its own
BB_LEG_P_BOLD = 0.65        # the "wyzej" variant: higher thresholds, each count leg at least this likely
BB_LEVELS = (("pewniej", BB_LEG_P), ("wyzej", BB_LEG_P_BOLD))
BB_BAND = (0.45, 0.80)      # joint chance of a builder worth showing
BB_PER_DAY = 12
# S shots, C shots on target, M more shots than the opponent, G scores, W wins (90 minutes)
# leg codes: S shots, C shots on target, M more shots than the opponent, G scores, W wins, ND does not lose
# (double chance), TS / TC match totals of shots / shots on target, B both teams score; R team corners,
# F team fouls, K team cards, TR / TF / TK their match totals (the mixed kind, when the copula knows them)
SAFE_TEMPLATES = (("S", "C", "M"), ("S", "C", "G"), ("S", "C", "M", "G"))
RESULT_TEMPLATES = (("S", "C", "W"), ("S", "M", "W"), ("S", "C", "ND"), ("S", "M", "ND"))
MATCH_TEMPLATES = (("TS", "TC"), ("TS", "TC", "B"), ("TS", "B"), ("TC", "B"))
MIX_TEAM = (("S", "R"), ("S", "C", "R"), ("S", "C", "K"), ("S", "F"))
MIX_MATCH = (("TS", "TR"), ("TR", "TK"), ("TF", "TK"), ("TS", "TK"))
MIX_TEMPLATES = MIX_TEAM + MIX_MATCH
# across the teams (user, 2026-10-29: "PSG co najmniej 14 strzałów, a Le Havre 8", "PSG 15 strzałów, a w meczu 25"):
# h / a prefix the home / away team's leg; x stands for either team
CROSS_TEMPLATES = (("hS", "aS"), ("hC", "aC"), ("hS", "aC"), ("aS", "hC"))
TEAM_MATCH_TEMPLATES = (("xS", "TS"), ("xC", "TC"), ("xS", "TC"))
# a likely starter's shot on target (user, 2026-10-29: "Lautaro celne"): P1 / P2 the team's first / second shooter
PLAYER_TEMPLATES = (("xP1",), ("xP1", "xS"), ("xP1", "xW"), ("xP1", "xND"), ("xP1", "TS"), ("xP1", "xP2"), ("hP1", "aP1"))
PLAYER_RANGE = (0.35, 0.90)    # a player leg is offered when its own chance is within this range
KIND_OF = {**{t: "bezpieczny" for t in SAFE_TEMPLATES}, **{t: "z_wynikiem" for t in RESULT_TEMPLATES},
           **{t: "meczowy" for t in MATCH_TEMPLATES}, **{t: "mieszany" for t in MIX_TEMPLATES},
           **{t: "obie" for t in CROSS_TEMPLATES + TEAM_MATCH_TEMPLATES}, **{t: "zawodnik" for t in PLAYER_TEMPLATES}}
BB_PER_KIND = {"bezpieczny": 5, "z_wynikiem": 4, "meczowy": 3, "mieszany": 3, "obie": 3, "zawodnik": 4}
BB_SAME_TEMPLATE = 2        # a day's list of one kind and level repeats a template at most this often
# Main proposals (user, 2026-10-29: near-identical builders of one match are one bet, not several; 2026-11-02:
# "najważniejsze, żeby były rzeczy, które siadają", the chance matters more than the price): one per match, its
# LIKELIEST builder among those at least MAIN_MIN_P likely and estimated within MAIN_PRICE; the other variants are
# test builders, logged and settled for learning only.
MAIN_MIN_P = 0.55
# the floor is the user's own (2026-10-29: nothing below 1.35, "za 1,13 weszło, ale -1 zł: bez sensu"); without it the
# likeliest builders would be near-certain combos at about 1.15 that nobody offers or plays
MAIN_PRICE = (1.35, 2.60)
# corners, fouls and cards are new in builders and the model has shown no edge on corners (eval_factors.py,
# the singles): the mixed kind stays a test kind until its own record says otherwise
MAIN_KINDS = {"bezpieczny", "z_wynikiem", "meczowy", "obie", "zawodnik"}
MAIN_PER_DAY = 12
# what is shown at all (user, 2026-10-29: "nie stawiamy singli poniżej 1.35"; "22, potem 23, potem 24 strzały jako osobne
# typy to praktycznie to samo"): no builder estimated below MIN_PRICE, no test with the same events as the match's main or
# another of its tests (other thresholds of the same bet), at most TEST_PER_MATCH tests a match
MIN_PRICE = 1.35
TEST_PER_MATCH = 3
MAIN_SAME_TEMPLATE = 3
# The likeliest single events of every match per side (home, away, whole match), for the user's own builders and
# coupons: 'at least k' at the highest k still LIKELY_LEVEL likely (legs rated 0.85-0.95 came in 92-94% to 1.11), more
# shots / shots on target / corners than the opponent, wins, does not lose, scores, both score, a player's shot on
# target; shown from LIKELY_MIN_P, not above LIKELY_MAX_P (no bookmaker prices a sure thing)
LIKELY_LEVEL = 0.85
LIKELY_MIN_P = 0.75
LIKELY_MAX_P = 0.97
LIKELY_PER_SIDE = 4
LIKELY_COUNTS = 2           # 'at least k' events of a side at most (user, 2026-10-29: not only "co najmniej k strzałów")
LIKELY_SKIP = ("fouls", "cards")    # no record behind them yet
TEAM_STATS = (("shots", "S"), ("sot", "C"), ("corners", "R"), ("fouls", "F"), ("cards", "K"))
MATCH_STATS = (("shots", "TS"), ("sot", "TC"), ("corners", "TR"), ("fouls", "TF"), ("cards", "TK"))
N_SIM = 20000
# Goals need far less shrinkage than shots: with the shots' team ridge (300) a 58% win came in 81% of
# the time; ridge 10 and a one-year half-life were calibrated (eval_builder.py) and had the best log-loss.
GOALS_FIT = {"half_life_days": 365.0, "ridge_team": 10.0}
CORR_FILE = Path(__file__).resolve().parents[2] / "config" / "mk2" / "bb_korelacje.json"
K_MAX = {"shots": 70, "sot": 35, "corners": 35, "goals": 15, "fouls": 60, "cards": 20}
COUNTS = ("shots", "sot", "corners", "goals")


SEL_FILE = sm.OUT / "stats" / "selekcja.json"   # written by stats_daily.selection_check after each settlement


@functools.lru_cache(maxsize=None)
def selection_miss(kind: str) -> float:
    """The extra miss multiplier the record of our picks asks for ('bb' builders, 'zdarzenie' single events): 1.0
    until the picks come in clearly less often than promised (stats_daily.selection_check)."""
    try:
        v = float(json.loads(SEL_FILE.read_text(encoding="utf-8")).get(kind) or 1.0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0
    return v if 1.0 <= v <= 2.0 else 1.0


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
                    "kurs_w_zasadach": odd is not None and sp.MIN_PICK_ODDS <= odd <= sp.MAX_PICK_ODDS,
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
    if SURE_PER_DAY <= 0:
        return {}
    for e in events:
        days.setdefault(e["day"], []).append(e)
    out = {}
    for day, lst in days.items():
        lst = sorted(lst, key=lambda e: (-(e["p_razy_kurs"] or 0) if e["kurs_w_zasadach"] and (e["p_razy_kurs"] or 0) >= 1.0
                                         else 0, -e["sila"]))
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
        # within the user's odds range, priced at least fairly, and not further from the market than the
        # picks may be (stats_picks.MIN_PICK_ODDS, MAX_EDGE)
        if (e["kurs"] and sp.MIN_PICK_ODDS <= e["kurs"] <= sp.MAX_PICK_ODDS and (e["p_razy_kurs"] or 0) >= 1.0
                and (e["p_rynek"] is None or e["p_model"] - e["p_rynek"] <= sp.MAX_EDGE)):
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


def add_players(X: dict[str, np.ndarray], players: dict | None, seed: int) -> None:
    """Each likely starter's '>= 1 shot on target' in every draw: a Bernoulli with P(>= 1 | the team's draw),
    the same uniform numbers for the model's and the market's draws (common random numbers)."""
    for side in ("home", "away"):
        for i, p in enumerate((players or {}).get(side, [])[:2]):
            q = spl.at_least_one(p["s_sot"], spl.KAPPA_SOT, 60)
            n = np.minimum(X[f"{side}_sot"], 60)
            u = np.random.default_rng(seed + 7919 * (i + 1) + (0 if side == "home" else 104729)).random(len(n))
            X[f"pl{p['id']}_sot"] = (u < q[n]).astype(np.int16)


def match_leg_set(m: dict, means: dict[str, float], alphas: dict[str, float], X: dict[str, np.ndarray],
                  level: float, players: dict | None = None) -> dict[str, tuple[np.ndarray, dict]]:
    """Every leg of a match at one level of thresholds: h / a + S C R F K (counts at least ``level`` likely
    on their own), M (more shots), G (scores), W (wins), ND (does not lose); TS TC TR TF TK match totals;
    B both teams score.  Counts the copula does not have are left out."""
    names = {"home": m["gospodarz"], "away": m["gosc"]}
    legs: dict[str, tuple[np.ndarray, dict]] = {}
    for stat, code in MATCH_STATS:
        if f"home_{stat}" not in X:
            continue
        mu_t = means[f"home_{stat}"] + means[f"away_{stat}"]
        k, _ = highest_k(mu_t, alphas.get(f"total_{stat}", alphas[f"home_{stat}"]), level, K_MAX[stat], BB_MISS)
        if k >= 1:
            legs[code] = (X[f"home_{stat}"] + X[f"away_{stat}"] >= k,
                          {"stat": stat, "scope": "total", "line": k - 0.5, "side": "over",
                           "opis": f"W meczu: co najmniej {k} {sp.plural(k, stat)}"})
    legs["B"] = ((X["home_goals"] >= 1) & (X["away_goals"] >= 1),
                 {"stat": "goals", "scope": "total", "line": None, "side": "btts", "opis": "Obie drużyny strzelą gola"})
    for team, opp, pre in (("home", "away", "h"), ("away", "home", "a")):
        tn, on = names[team], names[opp]
        for stat, code in TEAM_STATS:
            if f"{team}_{stat}" not in X or means[f"{team}_{stat}"] < MIN_MEAN.get(stat, 0.0):
                continue
            k, _ = highest_k(means[f"{team}_{stat}"], alphas[f"{team}_{stat}"], level, K_MAX[stat], BB_MISS)
            if k >= 1:
                legs[pre + code] = (X[f"{team}_{stat}"] >= k, {"stat": stat, "scope": team, "line": k - 0.5, "side": "over",
                                                               "opis": f"{tn}: co najmniej {k} {sp.plural(k, stat)}"})
        legs[pre + "M"] = (X[f"{team}_shots"] > X[f"{opp}_shots"],
                           {"stat": "shots", "scope": team, "line": None, "side": "more", "opis": f"{tn}: więcej strzałów niż {on}"})
        legs[pre + "G"] = (X[f"{team}_goals"] >= 1, {"stat": "goals", "scope": team, "line": 0.5, "side": "over", "opis": f"{tn} strzeli gola"})
        legs[pre + "W"] = (X[f"{team}_goals"] > X[f"{opp}_goals"],
                           {"stat": "goals", "scope": team, "line": None, "side": "more", "opis": f"{tn} wygra mecz (90 minut)"})
        legs[pre + "ND"] = (X[f"{team}_goals"] >= X[f"{opp}_goals"],
                            {"stat": "goals", "scope": team, "line": None, "side": "win_draw", "opis": f"{tn} nie przegra (wygra albo remis)"})
        for i, p in enumerate((players or {}).get(team, [])[:2]):
            key = f"pl{p['id']}_sot"
            if key in X and PLAYER_RANGE[0] <= float(X[key].mean()) <= PLAYER_RANGE[1]:
                legs[f"{pre}P{i + 1}"] = (X[key] >= 1, {"stat": "player_sot", "scope": team, "line": 0.5, "side": "over",
                                                         "player_id": p["id"], "player": p["name"], "udzial": p["s_sot"],
                                                         "opis": f"{p['name']} ({tn}): co najmniej 1 celny strzał"})
    return legs


MORE_EXTRA = (("sot", "MC", "więcej celnych strzałów niż"), ("corners", "MR", "więcej rzutów rożnych niż"))


def likely_events(m: dict, means: dict[str, float], alphas: dict[str, float], X: dict[str, np.ndarray],
                  players: dict | None = None) -> dict[str, list[dict]]:
    """The match's likeliest single events per side ('home', 'away', 'total'): the leg set at LIKELY_LEVEL plus more
    shots on target / corners than the opponent, each with its chance (as builder legs are corrected), those within
    LIKELY_MIN_P..LIKELY_MAX_P, the likeliest LIKELY_PER_SIDE of each side with at most LIKELY_COUNTS 'at least k'."""
    legs = match_leg_set(m, means, alphas, X, LIKELY_LEVEL, players)
    names = {"home": m["gospodarz"], "away": m["gosc"]}
    for team, opp, pre in (("home", "away", "h"), ("away", "home", "a")):
        for stat, code, what in MORE_EXTRA:
            if f"{team}_{stat}" in X:
                legs[pre + code] = (X[f"{team}_{stat}"] > X[f"{opp}_{stat}"],
                                    {"stat": stat, "scope": team, "line": None, "side": "more", "opis": f"{names[team]}: {what} {names[opp]}"})
    cands: dict[str, list[dict]] = {"home": [], "away": [], "total": []}
    for code, (mask, info) in legs.items():
        raw = float(mask.mean())
        p = corrected(raw, BB_MISS * selection_miss("zdarzenie"))
        if info["stat"] in LIKELY_SKIP or not LIKELY_MIN_P <= p <= LIKELY_MAX_P:
            continue
        side = "total" if info["scope"] == "total" or info["side"] == "btts" else info["scope"]
        cands[side].append({**info, "kod": code, "p_model": round(p, 4), "p_bez_selekcji": round(corrected(raw, BB_MISS), 4)})
    out: dict[str, list[dict]] = {}
    for side, lst in cands.items():
        take, counts = [], 0
        for e in sorted(lst, key=lambda e: -e["p_model"]):
            count = e["side"] == "over" and e["stat"] not in ("goals", "player_sot")
            if count and counts >= LIKELY_COUNTS:
                continue
            counts += count
            take.append(e)
            if len(take) == LIKELY_PER_SIDE:
                break
        out[side] = take
    return out


def day_likely(matches: list[dict], events: dict[int, dict]) -> dict[str, list[dict]]:
    """Per day the likeliest event of each side of every match, logged and settled to show how often they come in."""
    out: dict[str, list[dict]] = {}
    for m in matches:
        ev = events.get(m["fixture_id"])
        if not ev:
            continue
        for side in ("home", "away", "total"):
            if ev[side]:
                e = ev[side][0]
                out.setdefault(m["start"][:10], []).append({
                    **{k: v for k, v in e.items() if k != "kod"}, "fixture_id": m["fixture_id"], "start": m["start"],
                    "day": m["start"][:10], "mecz": f"{m['gospodarz']} – {m['gosc']}", "strona": side})
    return out


def _builder(m: dict, title: str, scope: str, szablon: str, keys: tuple, legs: dict, poziom: str,
             X_mkt: dict | None = None, cut: float = mk.CUT_PRIOR, sources: dict | None = None) -> dict:
    hit = np.logical_and.reduce([legs[c][0] for c in keys])
    raw = float(hit.mean())
    p = corrected(raw, BB_MISS * selection_miss("bb"))
    indep = math.prod(corrected(float(legs[c][0].mean()), BB_MISS) for c in keys)
    infos = [legs[c][1] for c in keys]
    out = {"fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", "start": m["start"],
           "day": m["start"][:10], "druzyna": title, "scope": scope, "szablon": szablon, "poziom": poziom,
           "legs": [{"fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}", **legs[c][1],
                     "p_model": round(corrected(float(legs[c][0].mean()), BB_MISS), 4)} for c in keys],
           "p_model": round(p, 4), "p_bez_selekcji": round(corrected(raw, BB_MISS), 4), "p_niezalezne": round(indep, 4),
           "kurs_uczciwy": round(1 / p, 2) if p > 0 else None,
           "kurs_min_z_podatkiem": round(1 / (p * TAX), 2) if p > 0 else None}
    if X_mkt is not None:
        p_mkt = mk.joint_chance(X_mkt, infos)
        est = mk.estimated_price(p_mkt, cut)
        out["p_rynek"] = round(p_mkt, 4)
        out["kurs_szac"] = est
        out["ev_szac"] = round(p * TAX * est - 1, 4) if est else None
        src = sources or {}
        with_market = 0
        for l in infos:
            if l["stat"] == "goals":
                with_market += bool(src.get("goals"))
            elif l["side"] == "more" or l["scope"] == "total":
                with_market += src.get(f"home_{l['stat']}", "model") != "model"
            else:
                with_market += src.get(f"{l['scope']}_{l['stat']}", "model") != "model"
        out["rynek_nogi"] = with_market
    return out


def builders_for_match(m: dict, means: dict[str, float], alphas: dict[str, float], dims: list[str],
                       corr: np.ndarray, market: tuple[dict, dict] | None = None,
                       cut: float = mk.CUT_PRIOR, players: dict | None = None) -> list[dict]:
    """Bet builders of a match at two levels of thresholds (BB_LEVELS): one team's events, the whole
    match's, mixed (corners, fouls, cards) and across the teams (both teams' shots or shots on target, a
    team and the match total).  With the market's means (stats_market.market_means) every builder also
    gets the market's joint chance and an estimated bookmaker price from the same random draws."""
    seed = int(m["fixture_id"]) % (2 ** 31)
    X = simulate(means, alphas, dims, corr, seed=seed)
    X_mkt = simulate(market[0], alphas, dims, corr, seed=seed) if market else None
    add_players(X, players, seed)
    if X_mkt is not None:
        add_players(X_mkt, players, seed)
    sources = dict(market[1]) if market else {}
    sources["goals"] = "1x2" if m.get("wynik_rynek") else None
    names = {"home": m["gospodarz"], "away": m["gosc"]}
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(title: str, scope: str, szablon: str, keys: tuple, legs: dict, poziom: str) -> None:
        if not all(k in legs for k in keys):
            return
        sig = (szablon, scope, tuple(legs[k][1].get("line") for k in keys))
        if sig in seen:
            return
        seen.add(sig)
        out.append(_builder(m, title, scope, szablon, keys, legs, poziom, X_mkt, cut, sources))

    shots_ok = means["home_shots"] + means["away_shots"] >= 2 * MIN_MEAN["shots"]
    for poziom, level in BB_LEVELS:
        legs = match_leg_set(m, means, alphas, X, level, players)
        if shots_ok:
            for tpl in MATCH_TEMPLATES + MIX_MATCH:
                add("Cały mecz", "total", "+".join(tpl), tpl, legs, poziom)
        for team, pre in (("home", "h"), ("away", "a")):
            if f"{pre}S" not in legs or f"{pre}C" not in legs:
                continue
            for tpl in SAFE_TEMPLATES + RESULT_TEMPLATES + MIX_TEAM:
                add(names[team], team, "+".join(tpl), tuple(pre + c for c in tpl), legs, poziom)
            for tpl in TEAM_MATCH_TEMPLATES:
                keys = tuple(pre + c[1:] if c.startswith("x") else c for c in tpl)
                add(f"{names[team]} i cały mecz", "both", "+".join(keys), keys, legs, poziom)
        for tpl in CROSS_TEMPLATES:
            add("Obie drużyny", "both", "+".join(tpl), tpl, legs, poziom)
        for tpl in PLAYER_TEMPLATES:
            for team, pre in (("home", "h"), ("away", "a")):
                keys = tuple(pre + c[1:] if c.startswith("x") else c for c in tpl)
                if keys != tpl or team == "home":       # templates naming both teams are added once
                    title = ("Obie drużyny" if keys == ("hP1", "aP1") else
                             f"{names[team]} i cały mecz" if "TS" in keys else names[team])
                    add(title, "both" if keys == ("hP1", "aP1") or "TS" in keys else team, "+".join(keys), keys, legs, poziom)
    return out


def kind_of(szablon: str) -> str:
    codes = tuple(szablon.split("+"))
    if codes in KIND_OF:
        return KIND_OF[codes]
    generic = tuple("x" + c[1:] if c[:1] in ("h", "a") and c[1:] in ("S", "C", "W", "ND", "P1", "P2") else c for c in codes)
    return KIND_OF.get(generic, "obie")


def builder_key(c: dict) -> str:
    """The builder's key in the app and the ledger: day | match | kind | level | template."""
    return f"{c['day']}|{c['fixture_id']}|{c['rodzaj']}|{c.get('poziom', 'pewniej')}|{c['szablon']}"


def _value(c: dict) -> float:
    ev = c.get("ev_szac")
    return ev if ev is not None else c["p_model"] - 1.0


def choose_mains(lst: list[dict]) -> list[dict]:
    """The main proposals of one day: one per match, from the kinds with a track record (MAIN_KINDS), at least
    MAIN_MIN_P likely and with an estimated price within MAIN_PRICE; the likeliest first, a template at most
    MAIN_SAME_TEMPLATE times (a match whose likeliest template is used up takes its next), at most MAIN_PER_DAY in all."""
    per_fix: dict[int, list[dict]] = {}
    for c in lst:
        est = c.get("kurs_szac")
        if (c["p_model"] >= MAIN_MIN_P and c.get("rodzaj", kind_of(c["szablon"])) in MAIN_KINDS
                and est is not None and MAIN_PRICE[0] <= est <= MAIN_PRICE[1]):
            per_fix.setdefault(c["fixture_id"], []).append(c)
    rank = lambda c: (c["p_model"], c.get("kurs_szac") or 0.0)  # noqa: E731 - the likeliest, then the better paid
    for v in per_fix.values():
        v.sort(key=rank, reverse=True)
    mains: list[dict] = []
    used: dict[str, int] = {}
    while per_fix and len(mains) < MAIN_PER_DAY:
        best = None
        for v in per_fix.values():
            head = next((c for c in v if used.get(c["szablon"], 0) < MAIN_SAME_TEMPLATE), None)
            if head is not None and (best is None or rank(head) > rank(best)):
                best = head
        if best is None:
            break
        mains.append(best)
        used[best["szablon"]] = used.get(best["szablon"], 0) + 1
        del per_fix[best["fixture_id"]]
    return mains


def signature(c: dict) -> frozenset:
    """The events of a builder without their thresholds: two builders with the same signature are one bet."""
    return frozenset((l.get("stat"), l.get("scope"), l.get("side"), l.get("player_id")) for l in c.get("legs") or [])


def prune(lst: list[dict]) -> list[dict]:
    """One day's builders as shown: every main; a test only when estimated at MIN_PRICE or more, with events that are not
    the main's or another test's of the match, at most TEST_PER_MATCH a match (the likeliest first)."""
    out: list[dict] = []
    seen: dict[int, set] = {}
    count: dict[int, int] = {}
    for c in sorted(lst, key=lambda c: (not c.get("glowny"), -c["p_model"])):
        fid, sig, est = c["fixture_id"], signature(c), c.get("kurs_szac")
        if not c.get("glowny"):
            if (est is not None and est < MIN_PRICE) or sig in seen.get(fid, set()) or count.get(fid, 0) >= TEST_PER_MATCH:
                continue
            count[fid] = count.get(fid, 0) + 1
        seen.setdefault(fid, set()).add(sig)
        out.append(c)
    return out


def pick_builders(cands: list[dict]) -> dict[str, list[dict]]:
    """Per day, within BB_BAND: the main proposals and the test variants.

    Main: one per match, the builder with the best estimated value (the model's chance at the estimated
    bookmaker price) among those at least MAIN_MIN_P likely; across the day at most MAIN_SAME_TEMPLATE of a
    template and MAIN_PER_DAY in all, best value first.  Test: the likeliest builder of each kind and level
    in each match, then the day's likeliest of each kind per level (BB_PER_KIND, a template at most
    BB_SAME_TEMPLATE times), without the main ones."""
    days: dict[str, list[dict]] = {}
    for c in cands:
        if BB_BAND[0] <= c["p_model"] <= BB_BAND[1]:
            c["rodzaj"] = kind_of(c["szablon"])
            c["glowny"] = False
            days.setdefault(c["day"], []).append(c)
    out = {}
    for day, lst in days.items():
        mains = choose_mains(lst)
        for c in mains:
            c["glowny"] = True
        best: dict[tuple, dict] = {}
        for c in lst:
            key = (c["fixture_id"], c["rodzaj"], c.get("poziom", "pewniej"))
            if key not in best or (c["p_model"], len(c["legs"])) > (best[key]["p_model"], len(best[key]["legs"])):
                best[key] = c
        ranked = sorted(best.values(), key=lambda c: -c["p_model"])
        tests = []
        for poziom, _ in BB_LEVELS:
            for kind, n in BB_PER_KIND.items():
                take: list[dict] = []
                seen: dict[str, int] = {}
                for x in ranked:        # the likeliest first, but no template more than BB_SAME_TEMPLATE times
                    if x["rodzaj"] != kind or x.get("poziom", "pewniej") != poziom or seen.get(x["szablon"], 0) >= BB_SAME_TEMPLATE:
                        continue
                    take.append(x)
                    seen[x["szablon"]] = seen.get(x["szablon"], 0) + 1
                    if len(take) == n:
                        break
                tests += [x for x in take if not x["glowny"]]
        chosen = prune(mains + tests)
        for c in chosen:
            c["klucz"] = builder_key(c)
            c["why"] = why_builder(c)
        out[day] = chosen
    return out


def why_builder(c: dict) -> str:
    gap = round((c["p_model"] - c["p_niezalezne"]) * 100)
    bold = (" To wersja z wyższymi progami: każde zdarzenie liczone jest na co najmniej 65% zamiast 80%, więc "
            "wchodzi rzadziej, ale kurs powinien być wyraźnie wyższy." if c.get("poziom") == "wyzej" else "")
    return (f"Szansa wszystkich {len(c['legs'])} zdarzeń razem wg modelu: {round(c['p_model'] * 100)}%. Zdarzenia w jednym meczu "
            f"są powiązane (więcej strzałów to zwykle więcej celnych, więcej celnych to częściej gol), więc razem wchodzą "
            f"częściej niż wynikałoby z pomnożenia ich szans ({round(c['p_niezalezne'] * 100)}%, różnica {gap:+d} pkt). "
            f"Opłaca się od kursu {_pl2(c['kurs_min_z_podatkiem'])} (z podatkiem 12%); kurs sprawdź u bukmachera w opcji "
            f"„zbuduj zakład”." + bold)


def run(analysis: dict, factors_of, fits: dict, goals_fit, df, leagues_shown: set[int], cups: set[int],
        now_iso: str, odds: dict[int, list[dict]] | None = None, cut: float = mk.CUT_PRIOR,
        players: dict[int, dict] | None = None) -> dict:
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
    likely: dict[int, dict] = {}
    gole: dict[int, dict] = {}
    rynek: dict[int, dict] = {}        # the market's means of every count, for the app's calculator
    for m in shown["mecze"]:
        if m.get("min_n", 0) < sp.MIN_TEAM_MATCHES:
            continue
        cup = m["liga_id"] in cups
        f = factors_of(m)
        means, alphas = {}, {}
        for side, team, opp, home in (("home", m["home_id"], m["away_id"], True), ("away", m["away_id"], m["home_id"], False)):
            for s in [x for x in dict.fromkeys(d.split("_", 1)[1] for d in dims) if x != "goals"]:
                means[f"{side}_{s}"] = m["srednie"][s][side] * f.get((s, side), 1.0)
                alphas[f"{side}_{s}"] = fits[s].dispersion(side, cup)
                alphas[f"total_{s}"] = fits[s].dispersion("total", cup)
            means[f"{side}_goals"] = goals_fit.mean(int(team), int(opp), home, cup)
            alphas[f"{side}_goals"] = goals_fit.dispersion(side, cup)
        # the bookmakers' match-winner chances, when there are any, set the goal means
        if m.get("wynik_rynek"):
            p_h, _, p_a = m["wynik_rynek"]
            means["home_goals"], means["away_goals"] = goals_from_market(means["home_goals"], means["away_goals"], p_h, p_a)
            alphas["home_goals"] = alphas["away_goals"] = 1e-4
        gole[m["fixture_id"]] = {"srednie": [round(means["home_goals"], 2), round(means["away_goals"], 2)],
                                 "z_rynku": bool(m.get("wynik_rynek"))}
        market = mk.market_means(means, alphas, (odds or {}).get(m["fixture_id"], []), dims, corr)
        rynek[m["fixture_id"]] = {d: round(float(v), 3) for d, v in market[0].items() if d in dims}
        seed = int(m["fixture_id"]) % (2 ** 31)
        Xl = simulate(means, alphas, dims, corr, seed=seed)
        add_players(Xl, (players or {}).get(m["fixture_id"]), seed)
        likely[m["fixture_id"]] = likely_events(m, means, alphas, Xl, (players or {}).get(m["fixture_id"]))
        for b in builders_for_match(m, means, alphas, dims, corr, market, cut, (players or {}).get(m["fixture_id"])):
            b["gole_z_rynku"] = bool(m.get("wynik_rynek"))
            cands.append(b)
    return {"pewne": pick_sure(events), "kupon_pewne": {d: k for d, k in coupons.items() if k},
            "bb": pick_builders(cands), "gole": gole, "rynek": rynek,
            "najpewniejsze": likely, "zdarzenia": day_likely(shown["mecze"], likely),
            "zawodnicy": {fid: {side: [{"id": p["id"], "n": p["name"], "s": p["s_sot"]} for p in lst[:2]] for side, lst in z.items()}
                          for fid, z in (players or {}).items()},
            "bb_wymiary": dims, "bb_korelacje": np.round(corr, 4).tolist()}
