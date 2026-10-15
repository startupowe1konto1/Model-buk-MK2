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
MAX_PRICE_OVER_MEDIAN = 1.08  # a price far above the other bookmakers' is more likely stale than a gift
# A model that disagrees with the whole market by more than this is far more often wrong than right
# (lopsided cup ties between clubs of very different leagues are where it extrapolates most).
MAX_EDGE = 0.15
# Teams with fewer matches with statistics in the last year are mostly guesses from their league.
MIN_TEAM_MATCHES = 10
# The user's rules (2026-10-14): only "over" bets, only on X.5 lines (never quarter or whole lines),
# and a single pick priced at most about 1.8.
ONLY_OVER = True
HALF_LINES_ONLY = True
MAX_PICK_ODDS = 1.85
RULES = "v2"

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
                ev = p * odd + p_push - 1
                rows.append({
                    "fixture_id": m["fixture_id"], "mecz": f"{m['gospodarz']} – {m['gosc']}",
                    "stat": l["stat"], "scope": l["scope"], "line": l["line"], "side": side,
                    "opis": describe(m, l["stat"], l["scope"], side, l["line"]),
                    "zaklad": f"{'powyżej' if side == 'over' else 'poniżej'} {l['line']:.1f}".replace(".", ","),
                    "zasady": RULES,
                    "p_model": round(p, 4), "p_model_surowe": round(l[f"p_{side}"], 4), "p_rynek": round(q, 4),
                    "kurs": odd, "bukmacher": bm_best, "kurs_mediana": round(median, 2),
                    "do_typow": l["stat"] in PICK_STATS and MARKET_BAND[0] <= q <= MARKET_BAND[1]
                                and m.get("min_n", MIN_TEAM_MATCHES) >= MIN_TEAM_MATCHES and odd <= MAX_PICK_ODDS,
                    "kurs_uczciwy": round(1 / p, 2) if p > 0 else None,
                    "kurs_min_z_podatkiem": round(1 / (p * TAX), 2) if p > 0 else None,
                    "ev": round(ev, 4), "ev_podatek": round(p * odd * TAX + p_push * TAX - 1, 4),
                    "przewaga": round(p - q, 4), "oczekiwane": round(mu, 2), "k": k,
                })
    return rows


def describe(m: dict, stat: str, scope: str, side: str, line: float) -> str:
    """'Aston Villa: co najmniej 14 strzałów' for an over X.5 bet; the bookmaker's wording otherwise."""
    who = {"home": m["gospodarz"], "away": m["gosc"], "total": "W meczu"}[scope]
    if side == "over" and is_half_line(line):
        k = int(line + 0.5)
        return f"{who}: co najmniej {k} {plural(k, stat)}"
    word = "powyżej" if side == "over" else "poniżej"
    return f"{who}: {sm.STAT_PL[stat]} {word} {str(line).replace('.', ',')}"


LEG_KEYS = ("fixture_id", "mecz", "stat", "scope", "line", "side", "opis", "zaklad", "kurs", "bukmacher", "p_model",
            "p_rynek")


def coupons(picks: list[dict], legs: int, lo: float = 1.8, hi: float = 2.5, top: int = 5) -> list[dict]:
    """Best combinations of legs from different matches with total odds in [lo, hi]."""
    pool = [p for p in picks if p["ev"] > 0 and p["p_model"] >= 0.55][:40]
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


def main() -> None:
    path = Path(sys.argv[1])
    analysis = json.loads(path.read_text(encoding="utf-8"))
    factors = level_factors(analysis)
    rows = candidates(analysis, factors)
    good = [r for r in rows if r["do_typow"] and MIN_EDGE <= r["przewaga"] <= MAX_EDGE and r["ev"] >= MIN_EV]
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
