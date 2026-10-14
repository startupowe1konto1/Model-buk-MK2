"""Readable thresholds per team for an analysis file: "at least k shots with probability p".

For every team and statistic it lists the highest threshold reached with at least 70% and 55%
probability, using the market-aligned level of the model (stats_picks.level_factors), the fair
odds and the minimum odds that pay off under the Polish 12% stake tax.

Usage: python stats_report.py <analysis.json>  (writes <analysis>_progi.json next to it)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats_model as sm  # noqa: E402
from stats_picks import TAX, alpha_for, level_factors  # noqa: E402

LEVELS = (0.70, 0.55)
STATS = ("shots", "sot", "corners", "fouls", "cards")


def thresholds(mu: float, alpha: float, level: float) -> tuple[int, float]:
    """Highest k with P(X >= k) >= level, and that probability."""
    best = (0, 1.0)
    for k in range(1, 60):
        p = sm.nb_over(mu, alpha, k - 0.5)[0]
        if p >= level:
            best = (k, p)
        else:
            break
    return best


def main() -> None:
    path = Path(sys.argv[1])
    a = json.loads(path.read_text(encoding="utf-8"))
    factors = level_factors(a)
    out = []
    for m in a["mecze"]:
        rows = []
        for scope, name in (("home", m["gospodarz"]), ("away", m["gosc"]), ("total", "łącznie")):
            for stat in STATS:
                k_level = factors.get((stat, scope), factors.get((stat, "total"), 1.0))
                mu = m["srednie"][stat][scope] * k_level
                alpha = alpha_for(a["model"], stat, scope)
                for level in LEVELS:
                    k, p = thresholds(mu, alpha, level)
                    rows.append({"kto": name, "scope": scope, "stat": stat, "oczekiwane": round(mu, 1),
                                 "poziom": level, "co_najmniej": k, "p": round(p, 3),
                                 "kurs_uczciwy": round(1 / p, 2), "kurs_min_z_podatkiem": round(1 / (p * TAX), 2)})
        out.append({"mecz": f"{m['gospodarz']} – {m['gosc']}", "start": m["start"], "progi": rows})
        print(f"\n{m['gospodarz']} – {m['gosc']}  ({m['start'][11:16]})")
        for stat in ("shots", "sot", "corners"):
            parts = []
            for scope in ("home", "away", "total"):
                r70 = next(r for r in rows if r["stat"] == stat and r["scope"] == scope and r["poziom"] == 0.70)
                parts.append(f"{r70['kto'][:14]} śr. {r70['oczekiwane']:.1f}, ≥{r70['co_najmniej']} ({r70['p']:.0%}, min. kurs {r70['kurs_min_z_podatkiem']:.2f})")
            print(f"  {sm.STAT_PL[stat]:13s} | " + " | ".join(parts))
    dest = path.with_name(path.stem + "_progi.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nwritten", dest)


if __name__ == "__main__":
    main()
