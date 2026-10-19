"""Turn an analysis file (stats_today.py) into candidate bets, top picks and 2-3 leg coupons.

The model's level is first aligned with the market: for each statistic and scope (home, away,
total) one factor k scales the model's expected counts so that, averaged over all offered lines
of the day, the model's over-probabilities match the market's (margin-free) ones.  What is left
is match-specific: a line is a candidate when the aligned model still disagrees with the market
and the best price has a positive expected value.  This guards against a model whose overall
level is off (e.g. a provider counting fouls differently from the bookmakers' source) while
keeping what it knows about the particular teams.

Usage: python stats_picks.py <analysis.json>   (writes <analysis>_picks.json next to it)
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats_model as sm  # noqa: E402

TAX = 0.88
MIN_EDGE = 0.04      # aligned model probability above the market's
MIN_EV = 0.02        # expected value per unit stake at the best price, before tax
# Statistics whose level and team effects held up against the market; fouls, cards and offsides
# depend on the referee and on how each provider counts them, so they are shown but not picked yet.
PICK_STATS = {"shots", "sot", "corners"}
MARKET_BAND = (0.30, 0.80)   # lines far in the tails are where a count model is least reliable
# A price far above the other bookmakers' is more likely stale than a gift; v5 looks for prices above
# the market on purpose, so the cap is looser than before (1.08), but 15% above the median is still
# treated as an error.
MAX_PRICE_OVER_MEDIAN = 1.15
# v5 (user, 2026-10-16): the chance is 80% the bookmakers' (margin removed) and 20% the model's: on
# 2029 settled lines the market forecast better than the model on every statistic and this mix was
# the best of all (scripts/mk2/eval_live.py).
MODEL_WEIGHT = 0.2
# The day's picks are the prices furthest above the market's fair price (best price x market chance
# - 1 at least this much), from at least two bookmakers quoting the line.
MIN_PRICE_EDGE = 0.02
MIN_BOOKS = 2
# A model that disagrees with the whole market by more than this is far more often wrong than right
# (lopsided cup ties between clubs of very different leagues are where it extrapolates most).
MAX_EDGE = 0.15
# Teams with fewer matches with statistics in the last year are mostly guesses from their league.
MIN_TEAM_MATCHES = 10
# The user's rules: only "over" bets, only on X.5 lines (never quarter or whole lines) (2026-10-14);
# a single event priced from about 1.25-1.30 up to 2.0, looked at in three odds bands (2026-10-15).
ONLY_OVER = True
HALF_LINES_ONLY = True
MIN_PICK_ODDS = 1.25
MAX_PICK_ODDS = 2.00
ODDS_BANDS = ((1.25, 1.45), (1.46, 1.65), (1.66, 2.00))
# v4 (2026-10-16): one bet per market in a match, match-result bets (win, win or draw) added
# v5 (2026-10-16): chance 80% market / 20% model, picks by the price's edge over the market, CLV
RULES = "v5"


def blend(p_model: float, q_market: float) -> float:
    return MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * q_market


def value_fields(p_model: float, q: float, odd: float, n_books: int) -> dict:
    """The fields every bet carries under v5: the blended chance, the price's edge over the market's
    fair price, expected profit before and after the 12% tax."""
    p = blend(p_model, q)
    return {"p_model": round(p, 4), "p_model_czysty": round(p_model, 4), "p_rynek": round(q, 4),
            "przewaga": round(p - q, 4), "przewaga_modelu": round(p_model - q, 4),
            "przewaga_kursu": round(odd * q - 1, 4), "n_buk": n_books,
            "kurs_uczciwy": round(1 / p, 2) if p > 0 else None,
            "kurs_min_z_podatkiem": round(1 / (p * TAX), 2) if p > 0 else None,
            "ev": round(p * odd - 1, 4), "ev_podatek": round(p * odd * TAX - 1, 4)}


def band_of(odd: float | None) -> int | None:
    """The odds band (0, 1 or 2) a price falls in; None outside 1.25-2.00."""
    if odd is None:
        return None
    for i, (lo, hi) in enumerate(ODDS_BANDS):
        if lo - 1e-9 <= odd <= hi + 1e-9:
            return i
    return None

# Polish forms for 1, for 2-4 (not 12-14) and for the rest
FORMS = {
    "shots": ("strzał", "strzały", "strzałów"), "sot": ("celny strzał", "celne strzały", "celnych strzałów"),
    "corners": ("rzut rożny", "rzuty rożne", "rzutów rożnych"), "fouls": ("faul", "faule", "fauli"),
    "cards": ("kartka", "kartki", "kartek"), "yellow": ("żółta kartka", "żółte kartki", "żółtych kartek"),
    "offsides": ("spalony", "spalone", "spalonych"),
}


def plural(n: int, stat: str) -> str:
    one, few, many = FORMS[stat]
    if n == 1:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def is_half_line(line: float) -> bool:
    return abs(line - int(line) - 0.5) < 1e-9


SCOPE_PL = {"home": "gospodarz", "away": "gość", "total": "łącznie"}


def alpha_for(model: dict, stat: str, scope: str) -> float:
    m = model[stat]
    return m["alpha_total"] if scope == "total" else m["alpha"]


def level_factors(analysis: dict) -> dict[tuple[str, str], float]:
    """Per (stat, scope): the k in [0.75, 1.35] that best matches the market on average."""
    groups: dict[tuple[str, str], list[tuple[float, float, float, float]]] = {}
    for m in analysis["mecze"]:
        for l in m["linie"]:
            if l.get("rynek_over") is None:
                continue
            mu = m["srednie"][l["stat"]][l["scope"]]
            groups.setdefault((l["stat"], l["scope"]), []).append(
                (mu, alpha_for(analysis["model"], l["stat"], l["scope"]), l["line"], l["rynek_over"]))
    out = {}
    grid = np.arange(0.75, 1.3501, 0.01)
    for key, items in groups.items():
        if len(items) < 6:
            out[key] = 1.0
            continue
        # P(X > line) = sf(floor(line)) for whole and half lines alike (sm.nb_over), for all lines at once
        mu, a, line, q = (np.array(c, dtype=float) for c in zip(*items))
        r, fl = 1.0 / a, np.floor(line)
        err = [float(np.sum((stats.nbinom.sf(fl, r, r / (r + mu * k)) - q) ** 2)) for k in grid]
        out[key] = float(grid[int(np.argmin(err))])
    return out


def candidates(analysis: dict, factors: dict) -> list[dict]:
    rows = []
    for m in analysis["mecze"]:
        for l in m["linie"]:
            q_over = l.get("rynek_over")
            if q_over is None:
                continue
            k = factors.get((l["stat"], l["scope"]), 1.0)
            mu = m["srednie"][l["stat"]][l["scope"]] * k
            p_over, p_push, p_under = sm.nb_over(mu, alpha_for(analysis["model"], l["stat"], l["scope"]), l["line"])
            if HALF_LINES_ONLY and not is_half_line(l["line"]):
                continue
            sides = (("over", p_over, q_over),) if ONLY_OVER else (("over", p_over, q_over), ("under", p_under, 1 - q_over))
            for side, p, q in sides:
                prices = l.get(f"kursy_{side}") or {}
                if not prices:
                    continue
                median = float(np.median(list(prices.values())))
                fair_prices = {bm: o for bm, o in prices.items() if o <= median * MAX_PRICE_OVER_MEDIAN}
                bm_best = max(fair_prices, key=fair_prices.get)
                odd = fair_prices[bm_best]
                rows.append({
                    "fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}",
                    "stat": l["stat"], "scope": l["scope"], "line": l["line"], "side": side,
                    "opis": describe(m, l["stat"], l["scope"], side, l["line"]),
                    "zaklad": f"{'powyżej' if side == 'over' else 'poniżej'} {l['line']:.1f}".replace(".", ","),
                    "zasady": RULES,
                    "p_model_surowe": round(l[f"p_{side}"], 4),
                    "kurs": odd, "bukmacher": bm_best, "kurs_mediana": round(median, 2),
                    "do_typow": l["stat"] in PICK_STATS and MARKET_BAND[0] <= q <= MARKET_BAND[1]
                                and m.get("min_n", MIN_TEAM_MATCHES) >= MIN_TEAM_MATCHES
                                and MIN_PICK_ODDS <= odd <= MAX_PICK_ODDS,
                    **value_fields(p, q, odd, len(prices)),
                    "oczekiwane": round(mu, 2), "k": k,
                })
    return rows


def describe(m: dict, stat: str, scope: str, side: str, line: float) -> str:
    """'Aston Villa: co najmniej 14 strzałów' for an over X.5 bet; the bookmaker's wording otherwise.
    Match-result bets (stat 'wynik'): 'Aston Villa: wygra mecz' or 'Aston Villa: wygra albo zremisuje'."""
    who = {"home": m["gospodarz"], "away": m["gosc"], "total": "W meczu"}[scope]
    if stat == "wynik":
        return f"{who}: " + ("wygra mecz" if side == "win" else "wygra albo zremisuje")
    if side == "over" and is_half_line(line):
        k = int(line + 0.5)
        return f"{who}: co najmniej {k} {plural(k, stat)}"
    word = "powyżej" if side == "over" else "poniżej"
    return f"{who}: {sm.STAT_PL[stat]} {word} {str(line).replace('.', ',')}"


def bookie_wording(stat: str, scope: str, side: str, line: float | None) -> str:
    """The bet as the bookmaker names it: 'powyżej 13,5', or '1', '2', '1X', 'X2' for the match result."""
    if stat == "wynik":
        return {("home", "win"): "1", ("away", "win"): "2", ("home", "win_draw"): "1X",
                ("away", "win_draw"): "X2"}[(scope, side)]
    return f"{'powyżej' if side == 'over' else 'poniżej'} {line:.1f}".replace(".", ",")


def market_key(c: dict) -> tuple:
    """The market a bet belongs to: two lines of the same market in one match are the same bet
    twice (e.g. at least 8 and at least 9 corners), so a match gets at most one bet per market;
    all match-result bets (win, win or draw, either team) count as one market."""
    return (c["fixture_id"], "wynik") if c["stat"] == "wynik" else (c["fixture_id"], c["stat"], c["scope"])


LEG_KEYS = ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis", "zaklad", "kurs", "bukmacher", "p_model",
            "p_rynek")


def coupons(picks: list[dict], legs: int, lo: float = 1.8, hi: float = 2.5, top: int = 5) -> list[dict]:
    """Best combinations of legs from different matches with total odds in [lo, hi]."""
    pool = [p for p in picks if p.get("przewaga_kursu", p["ev"]) > 0 and p["p_model"] >= 0.55][:40]
    out = []
    for combo in itertools.combinations(pool, legs):
        if len({c["fixture_id"] for c in combo}) < legs:
            continue
        odd = math.prod(c["kurs"] for c in combo)
        if not lo <= odd <= hi:
            continue
        p = math.prod(c["p_model"] for c in combo)
        out.append({"nogi": [c["opis"] + f" @ {c['kurs']:.2f} ({c['bukmacher']})" for c in combo],
                    "legs": [{k: c.get(k) for k in LEG_KEYS} for c in combo],
                    "kurs": round(odd, 2), "p_model": round(p, 4), "ev": round(p * odd - 1, 4),
                    "ev_podatek": round(p * odd * TAX - 1, 4)})
    out.sort(key=lambda c: -c["ev"])
    for c in out:
        c["n_kombinacji"] = len(out)
    return out[:top]



# Ready coupons (user, 2026-10-19: "I jak z gotowymi kuponami?"): every day a coupon of about 2.0 (two
# events, three only when no pair fits) and one of about 4 (three or four events), each event a bet the
# singles' rules allow (at least ..., odds 1.25-2.00, two bookmakers or more), one per match. For a given
# total price the likeliest coupon is also the best priced one (its chance is the product of chance x odds
# over the total odds), so the coupon most likely to come in is chosen; one that pays after the 12% tax
# comes first when there is any. A coupon without value is shown with a mark, as single bets are.
READY = {"2": {"sizes": ((2,), (3,)), "kurs": (1.80, 2.50), "nazwa": "ok. 2,0"},
         "4": {"sizes": ((3, 4),), "kurs": (3.80, 5.00), "nazwa": "ok. 4"}}
READY_MIN_P = 0.55                   # each event likelier than not, with some room
READY_POOL = {2: 60, 3: 40, 4: 26}   # events tried per coupon size: the best priced (chance x odds)
READY_PER_MATCH = 2                  # lines of one match among them


def ready_coupons(pool: list[dict], kind: str, top: int = 5) -> list[dict]:
    """The best coupons of one kind (READY) from the day's allowed bets, legs from different matches:
    those that pay after the 12% tax first, then the likeliest; ties by the expected value."""
    spec = READY[kind]
    lo, hi = spec["kurs"]
    legs, per_match = [], {}
    for c in sorted(pool, key=lambda c: -c["p_model"] * c["kurs"]):
        if c["p_model"] < READY_MIN_P or per_match.get(c["fixture_id"], 0) >= READY_PER_MATCH:
            continue
        per_match[c["fixture_id"]] = per_match.get(c["fixture_id"], 0) + 1
        legs.append(c)
    found = []
    for sizes in spec["sizes"]:
        for n in sizes:
            for combo in itertools.combinations(legs[:READY_POOL[n]], n):
                if len({c["fixture_id"] for c in combo}) < n:
                    continue
                odd = math.prod(c["kurs"] for c in combo)
                if lo <= odd <= hi:
                    found.append((combo, odd, math.prod(c["p_model"] for c in combo)))
        if found:
            break
    out = [{"nogi": [c["opis"] + f" @ {c['kurs']:.2f} ({c['bukmacher']})" for c in combo],
            "legs": [{k: c.get(k) for k in LEG_KEYS} for c in combo],
            "kurs": round(odd, 2), "p_model": round(p, 4),
            "p_rynek": round(math.prod(c["p_rynek"] for c in combo), 4),
            "ev": round(p * odd - 1, 4), "ev_podatek": round(p * odd * TAX - 1, 4), "rodzaj": kind}
           for combo, odd, p in found]
    out.sort(key=lambda k: (k["ev_podatek"] >= 0, k["p_model"], k["ev"]), reverse=True)
    for k in out:
        k["z_wartoscia"] = k["ev_podatek"] >= 0
        k["n_kombinacji"] = len(out)
    return out[:top]


def main() -> None:
    path = Path(sys.argv[1])
    analysis = json.loads(path.read_text(encoding="utf-8"))
    factors = level_factors(analysis)
    rows = candidates(analysis, factors)
    good = [r for r in rows if r["do_typow"] and r["przewaga_modelu"] <= MAX_EDGE and r["przewaga_kursu"] >= MIN_PRICE_EDGE]
    good.sort(key=lambda r: -r["ev"])
    # one pick per (match, statistic, scope): the best line
    seen, top = set(), []
    for r in good:
        key = (r["fixture_id"], r["stat"], r["scope"])
        if key not in seen:
            seen.add(key)
            top.append(r)
    result = {"czynniki": {f"{s}|{sc}": k for (s, sc), k in factors.items()}, "kandydaci": top,
              "kupony_2": coupons(top, 2), "kupony_3": coupons(top, 3)}
    out = path.with_name(path.stem + "_picks.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("level factors (market / model):", {f"{s}|{sc}": k for (s, sc), k in sorted(factors.items())})
    for r in top[:15]:
        print(f"{r['mecz'][:34]:34s} {r['opis'][:44]:44s} kurs {r['kurs']:.2f} {r['bukmacher']:11s} "
              f"p {r['p_model']:.2f} rynek {r['p_rynek']:.2f} ev {r['ev']:+.3f} z podatkiem {r['ev_podatek']:+.3f}")
    for n in (2, 3):
        for c in result[f"kupony_{n}"][:3]:
            print(f"kupon {n}: kurs {c['kurs']}, p {c['p_model']:.2f}, ev {c['ev']:+.3f}, z podatkiem {c['ev_podatek']:+.3f} | " + " + ".join(c["nogi"]))
    print("written", out)


if __name__ == "__main__":
    main()
