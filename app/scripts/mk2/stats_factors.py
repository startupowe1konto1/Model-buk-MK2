"""Point-in-time factors on top of the team model: recent form, form at the venue, what the opponent
has been conceding lately, the referee (fouls and cards).

The walk-forward test (eval_factors.py, 2025-08..2026-10) found that these add to the model for shots,
shots on target, fouls and cards, and nothing for corners; line-ups and congestion were too small to
use.  Coefficients, means and scales are in config/mk2/czynniki.json.  Each upcoming match gets its
means multiplied by exp(sum b_f * z_f), capped at max_mult either way, and a plain-language record of
the factors (``czynniki``) for the app.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import stats_model as sm

CONFIG = Path(__file__).resolve().parents[2] / "config" / "mk2" / "czynniki.json"
LAST = 5
REF_SHRINK = 20.0


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def mu_all(fit: sm.StatFit, d: pd.DataFrame) -> np.ndarray:
    """The fit's expected count for every row of d, with the level of each row's own half-year."""
    per = sm.period_of(d["kickoff"]).to_numpy()
    keys = [f"{int(p) // 2}H{int(p) % 2 + 1}" for p in per]
    pe = np.array([fit.period_effects.get(k, fit.b_season) for k in keys])
    lt = d["team_id"].map(lambda t: fit.league_for.get(fit.leagues.get(int(t), 0), 0.0)).to_numpy(float)
    lo = d["opp_id"].map(lambda t: fit.league_against.get(fit.leagues.get(int(t), 0), 0.0)).to_numpy(float)
    tf = d["team_id"].map(lambda t: fit.team_for.get(int(t), 0.0)).to_numpy(float)
    ta = d["opp_id"].map(lambda t: fit.team_against.get(int(t), 0.0)).to_numpy(float)
    home = d["home"].to_numpy(float)
    cup = d["cup"].to_numpy(float)
    return np.exp(fit.b0 + fit.b_home * home + fit.b_cup * cup + fit.b_home_cup * home * cup + pe + lt + lo + tf + ta)


class Factors:
    """Residual histories per team, per (team, venue), per opponent and per referee, for one statistic."""

    def __init__(self, df: pd.DataFrame, fit: sm.StatFit, stat: str):
        col = sm.STATS[stat]
        d = df[df[col].notna()].sort_values("kickoff")
        r = np.log((d[col].to_numpy(float) + 0.5) / (mu_all(fit, d) + 0.5))
        self.team: dict[int, list[float]] = defaultdict(list)
        self.venue: dict[tuple[int, bool], list[float]] = defaultdict(list)
        self.conceded: dict[int, list[float]] = defaultdict(list)
        for t, o, h, x in zip(d["team_id"].to_numpy(), d["opp_id"].to_numpy(), d["home"].to_numpy(), r):
            self.team[int(t)].append(float(x))
            self.venue[(int(t), bool(h))].append(float(x))
            self.conceded[int(o)].append(float(x))
        m = (d.assign(r=r).groupby("fixture_id", sort=False)
             .agg(kickoff=("kickoff", "first"), referee=("referee", "first"), r=("r", "mean")).sort_values("kickoff"))
        self.ref: dict[str, tuple[float, int]] = {}
        for name, x in zip(m["referee"].to_numpy(), m["r"].to_numpy()):
            if name:
                s, n = self.ref.get(name, (0.0, 0))
                self.ref[name] = (s + float(x), n + 1)

    def values(self, team: int, opp: int, home: bool, referee: str | None) -> dict[str, float | None]:
        last = lambda lst: float(np.mean(lst[-LAST:])) if len(lst) >= 3 else None  # noqa: E731
        s, n = self.ref.get(referee or "", (0.0, 0))
        return {"form5": last(self.team.get(team, [])), "venue5": last(self.venue.get((team, home), [])),
                "oppc5": last(self.conceded.get(opp, [])), "ref": (s / (n + REF_SHRINK)) if n else None, "ref_n": n}


def multiplier(cfg: dict, vals: dict) -> float:
    z = 0.0
    for f, c in cfg.items():
        x = vals.get(f)
        if x is None:
            continue
        z += c["b"] * (x - c["mean"]) / c["sd"]
    return float(np.clip(math.exp(z), 1 / MAX_MULT, MAX_MULT))


MAX_MULT = 1.15


def apply(analysis: dict, fits: dict, df: pd.DataFrame, referees: dict[int, str | None]) -> None:
    """Adjust the means of every upcoming match in place and record the factors (``czynniki``)."""
    global MAX_MULT
    cfg = load_config()
    MAX_MULT = float(cfg.get("max_mult", 1.15))
    stats_ = [s for s in cfg if s in fits and isinstance(cfg[s], dict) and "b" in next(iter(cfg[s].values()), {})]
    hist = {s: Factors(df, fits[s], s) for s in stats_}
    # percentages against the typical team (the factor's mean residual), so 0 reads as "as usual"; a factor
    # the statistic does not use for the adjustment is still shown, against the typical residual
    typical = {"shots": -0.066, "sot": -0.132, "corners": -0.154, "fouls": -0.051, "cards": -0.193}

    def pct(x, s, f):
        if x is None:
            return None
        mean = (cfg[s].get(f) or {}).get("mean", typical.get(s, 0.0))
        return round((math.exp(x - mean) - 1) * 100)
    for m in analysis["mecze"]:
        ref = referees.get(m["fixture_id"])
        rec: dict = {"sedzia": ref}
        for side, team, opp, home in (("home", m["home_id"], m["away_id"], True), ("away", m["away_id"], m["home_id"], False)):
            rec[side] = {}
            for s in stats_:
                v = hist[s].values(int(team), int(opp), home, ref)
                mult = multiplier(cfg[s], v)
                m["srednie"][s][side] *= mult
                entry = {"mult": round(mult, 3), "forma": pct(v["form5"], s, "form5"), "forma_tu": pct(v["venue5"], s, "venue5"),
                         "rywal_dopuszcza": pct(v["oppc5"], s, "oppc5")}
                if "ref" in cfg[s]:
                    entry["sedzia"] = pct(v["ref"], s, "ref")
                    entry["sedzia_n"] = v["ref_n"]
                rec[side][s] = entry
        for s in stats_:
            m["srednie"][s]["total"] = m["srednie"][s]["home"] + m["srednie"][s]["away"]
        m["czynniki"] = rec
