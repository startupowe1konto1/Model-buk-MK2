"""Calibration of the scanner's half-split goal model on history (``model_buk.scanner.halves``).

For every finished match with Pinnacle closing prices in football-data (1X2, over/under 2.5 and, where present,
the closing Asian handicap; ``joined/fd_af_matches.sqlite``) the full-time score matrix is fitted to those prices
exactly as the scanner fits it to the live board, split into halves with several share variants, and scored
against what happened (``api-football/tables/history.sqlite``: half-time and full-time score, goal events for the
first and last goal):

* ``naive``: half the goals in each half (``s = 0.5``), the baseline;
* ``global``: the global first-half share of the window before the month of the match;
* ``league``: the league share shrunk to the global one (the scanner's default without first-half prices);
* ``league_raw``: the league share without shrinkage;
* ``league_w2`` / ``league_all``: the league share with a two-year window / all history before the month.

Shares are estimated from the matches that kicked off before the first day of the month of the evaluated match
(walk-forward, nothing at or after the cutoff).  Output: mean log-loss per market and variant, the paired
difference against ``naive`` with its standard error, the conditional log-loss of the half-time score given the
full-time score (which isolates the split from the full-time matrix), and calibration in the large (mean
predicted against observed frequency) of the ``league`` variant.

Usage (from app/)::

    .venv\\Scripts\\python scripts\\measure_half_model.py --start 2021-01-01 --end 2026-01-01 --out half_model.json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from model_buk.markets.pricing import devig_probabilities  # noqa: E402
from model_buk.scanner.fair import FitTarget, fit_score_matrix  # noqa: E402
from model_buk.scanner.halves import HalfSplit, half_leg_probability, shares_from_matches  # noqa: E402

DATA = Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
HISTORY = DATA / "api-football" / "tables" / "history.sqlite"
JOINED = DATA / "joined" / "fd_af_matches.sqlite"

MARKETS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "1H 1X2": ("1x2", "1h", ("home", "draw", "away")),
    "HT/FT": ("ht_ft", "ft", tuple(f"{h}/{f}" for h in ("home", "draw", "away") for f in ("home", "draw", "away"))),
    "highest scoring half": ("highest_half", "ft", ("first_half", "second_half", "equal")),
    "highest half home": ("highest_half_home", "ft", ("first_half", "second_half", "equal")),
    "highest half away": ("highest_half_away", "ft", ("first_half", "second_half", "equal")),
    "score in both halves": ("score_both_halves", "ft", ("yes", "no")),
    "home scores in both halves": ("score_both_halves_home", "ft", ("yes", "no")),
    "away scores in both halves": ("score_both_halves_away", "ft", ("yes", "no")),
    "btts in both halves": ("btts_both_halves", "ft", ("yes", "no")),
    "home wins both halves": ("win_both_halves_home", "ft", ("yes", "no")),
    "away wins both halves": ("win_both_halves_away", "ft", ("yes", "no")),
    "home wins either half": ("win_either_half_home", "ft", ("yes", "no")),
    "first team to score": ("first_goal", "ft", ("home", "away", "none")),
    "last team to score": ("last_goal", "ft", ("home", "away", "none")),
    "first team to score 1H": ("first_goal", "1h", ("home", "away", "none")),
    "1H odd/even": ("odd_even", "1h", ("odd", "even")),
    "2H odd/even": ("odd_even", "2h", ("odd", "even")),
    "1H over 0.5": ("ou", "1h", ("over", "under")),
    "1H over 1.5": ("ou", "1h", ("over", "under")),
    "2H over 1.5": ("ou", "2h", ("over", "under")),
    # full-time markets of the score matrix (the split does not change them)
    "odd/even": ("odd_even", "ft", ("odd", "even")),
    "home odd/even": ("odd_even_home", "ft", ("odd", "even")),
    "European handicap home -1": ("eh", "ft", ("home", "draw", "away")),
    "European handicap home +1": ("eh", "ft", ("home", "draw", "away")),
    "home wins to nil": ("win_to_nil_home", "ft", ("yes", "no")),
    "away wins to nil": ("win_to_nil_away", "ft", ("yes", "no")),
}
LINES = {"1H over 0.5": 0.5, "1H over 1.5": 1.5, "2H over 1.5": 1.5, "European handicap home -1": -1.0, "European handicap home +1": 1.0}


def _read(path: Path, sql: str) -> pd.DataFrame:
    with closing(sqlite3.connect("file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True)) as con:
        return pd.read_sql_query(sql, con)


def goal_order(start: str, end: str) -> pd.DataFrame:
    """First and last scoring side (1 home, 2 away) per match, where the goal events add up to the score."""
    events = _read(
        HISTORY,
        "SELECT e.fixture_id, e.seq, e.elapsed, e.extra, e.team_id, e.detail, m.home_team_id, m.home_goals, m.away_goals "
        "FROM events e JOIN matches m USING (fixture_id) WHERE e.type = 'Goal' AND m.status_short = 'FT' "
        f"AND m.kickoff_utc >= '{start}' AND m.kickoff_utc < '{end}'",
    )
    events = events[(events["detail"] != "Missed Penalty") & (events["elapsed"] <= 90)]
    events = events.sort_values(["fixture_id", "elapsed", "extra", "seq"], na_position="first")
    events["home"] = events["team_id"] == events["home_team_id"]
    grouped = events.groupby("fixture_id")
    table = pd.DataFrame(
        {
            "n_home": grouped["home"].sum(),
            "n_away": grouped["home"].size() - grouped["home"].sum(),
            "first": grouped["home"].first().map({True: 1, False: 2}),
            "last": grouped["home"].last().map({True: 1, False: 2}),
            "home_goals": grouped["home_goals"].first(),
            "away_goals": grouped["away_goals"].first(),
        }
    )
    ok = (table["n_home"] == table["home_goals"]) & (table["n_away"] == table["away_goals"])
    return table.loc[ok, ["first", "last"]]


def load_matches(start: str, end: str) -> pd.DataFrame:
    odds = _read(
        JOINED,
        'SELECT fixture_id, PSCH, PSCD, PSCA, "PC>2.5" AS pco, "PC<2.5" AS pcu, AHCh, PCAHH, PCAHA FROM fd_af_matches',
    )
    matches = _read(
        HISTORY,
        "SELECT fixture_id, league_id, kickoff_utc, home_goals, away_goals, ht_home_goals, ht_away_goals FROM matches "
        f"WHERE status_short = 'FT' AND kickoff_utc >= '{start}' AND kickoff_utc < '{end}' "
        "AND ht_home_goals IS NOT NULL AND home_goals IS NOT NULL",
    )
    frame = matches.merge(odds, on="fixture_id", how="inner")
    frame = frame.dropna(subset=["PSCH", "PSCD", "PSCA", "pco", "pcu"])
    order = goal_order(start, end)
    frame = frame.merge(order, left_on="fixture_id", right_index=True, how="left")
    return frame.reset_index(drop=True)


def fit_matrix(row: pd.Series) -> np.ndarray | None:
    targets = []
    home, draw, away = devig_probabilities([row.PSCH, row.PSCD, row.PSCA], "power")
    targets += [FitTarget("1x2", "home", float("nan"), home), FitTarget("1x2", "draw", float("nan"), draw), FitTarget("1x2", "away", float("nan"), away)]
    over, _ = devig_probabilities([row.pco, row.pcu], "power")
    targets.append(FitTarget("ou", "over", 2.5, over))
    if np.isfinite(row.AHCh) and np.isfinite(row.PCAHH) and np.isfinite(row.PCAHA) and row.PCAHH > 1 and row.PCAHA > 1:
        # football-data's AHh / AHCh is the handicap of the home team
        ah_home, _ = devig_probabilities([row.PCAHH, row.PCAHA], "power")
        targets.append(FitTarget("ah", "home", float(row.AHCh), ah_home))
    fit = fit_score_matrix(targets, model="dc")
    return None if fit is None else fit.matrix


def outcomes(row: pd.Series) -> dict[str, str | None]:
    h1, a1 = int(row.ht_home_goals), int(row.ht_away_goals)
    h, a = int(row.home_goals), int(row.away_goals)
    h2, a2 = h - h1, a - a1

    def res(x: int, y: int) -> str:
        return "home" if x > y else "draw" if x == y else "away"

    def higher(x: int, y: int) -> str:
        return "first_half" if x > y else "second_half" if y > x else "equal"

    def yn(flag: bool) -> str:
        return "yes" if flag else "no"

    side = {1.0: "home", 2.0: "away"}
    first = "none" if h + a == 0 else side.get(row.first) if np.isfinite(row.first) else None
    last = "none" if h + a == 0 else side.get(row.last) if np.isfinite(row.last) else None
    first_1h = "none" if h1 + a1 == 0 else first
    return {
        "1H 1X2": res(h1, a1),
        "HT/FT": f"{res(h1, a1)}/{res(h, a)}",
        "highest scoring half": higher(h1 + a1, h2 + a2),
        "highest half home": higher(h1, h2),
        "highest half away": higher(a1, a2),
        "score in both halves": yn(h1 + a1 > 0 and h2 + a2 > 0),
        "home scores in both halves": yn(h1 > 0 and h2 > 0),
        "away scores in both halves": yn(a1 > 0 and a2 > 0),
        "btts in both halves": yn(h1 > 0 and a1 > 0 and h2 > 0 and a2 > 0),
        "home wins both halves": yn(h1 > a1 and h2 > a2),
        "away wins both halves": yn(a1 > h1 and a2 > h2),
        "home wins either half": yn(h1 > a1 or h2 > a2),
        "first team to score": first,
        "last team to score": last,
        "first team to score 1H": first_1h,
        "1H odd/even": "odd" if (h1 + a1) % 2 else "even",
        "2H odd/even": "odd" if (h2 + a2) % 2 else "even",
        "1H over 0.5": "over" if h1 + a1 > 0.5 else "under",
        "1H over 1.5": "over" if h1 + a1 > 1.5 else "under",
        "2H over 1.5": "over" if h2 + a2 > 1.5 else "under",
        "odd/even": "odd" if (h + a) % 2 else "even",
        "home odd/even": "odd" if h % 2 else "even",
        "European handicap home -1": res(h - 1, a),
        "European handicap home +1": res(h + 1, a),
        "home wins to nil": yn(h > a and a == 0),
        "away wins to nil": yn(a > h and h == 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-01-01")
    parser.add_argument("--sample", type=int, default=0, help="evaluate a random sample of this many matches (0 = all)")
    parser.add_argument("--out", default=None, help="write the results as JSON here")
    args = parser.parse_args()
    started = time.time()
    frame = load_matches(args.start, args.end)
    if args.sample and len(frame) > args.sample:
        frame = frame.sample(args.sample, random_state=7).sort_values("kickoff_utc").reset_index(drop=True)
    print(f"matches: {len(frame)} ({frame['league_id'].nunique()} leagues), goal order known for {frame['first'].notna().sum()}", flush=True)
    history = _read(
        HISTORY,
        "SELECT league_id, kickoff_utc, home_goals, away_goals, ht_home_goals, ht_away_goals FROM matches WHERE status_short = 'FT'",
    )
    months = pd.to_datetime(frame["kickoff_utc"], utc=True, format="ISO8601").dt.tz_localize(None).dt.to_period("M").dt.start_time.dt.tz_localize("UTC")
    frame["month"] = months
    variants: dict[str, dict[pd.Timestamp, object]] = {k: {} for k in ("global", "league", "league_raw", "league_w2", "league_all")}
    for month in sorted(months.unique()):
        four = shares_from_matches(history, month, window_years=4.0)
        variants["league"][month] = four
        variants["global"][month] = four
        variants["league_raw"][month] = shares_from_matches(history, month, window_years=4.0, prior_strength=1e-9)
        variants["league_w2"][month] = shares_from_matches(history, month, window_years=2.0)
        variants["league_all"][month] = shares_from_matches(history, month, window_years=None)
    names = ["naive", *variants]
    losses: dict[str, dict[str, list[float]]] = {n: {m: [] for m in [*MARKETS, "HT score | FT score"]} for n in names}
    predicted: dict[str, dict[str, float]] = {m: {} for m in MARKETS}
    observed: dict[str, dict[str, float]] = {m: {} for m in MARKETS}
    counts: dict[str, int] = {m: 0 for m in MARKETS}
    shares_used: list[tuple[float, float]] = []
    skipped = 0
    for i, row in enumerate(frame.itertuples(index=False)):
        matrix = fit_matrix(row)  # type: ignore[arg-type]
        if matrix is None:
            skipped += 1
            continue
        result = outcomes(row)  # type: ignore[arg-type]
        for name in names:
            if name == "naive":
                s_home, s_away = 0.5, 0.5
            else:
                table = variants[name][row.month]
                s_home, s_away, source = table.for_league(row.league_id)
                if name == "global":
                    s_home, s_away = table.global_home, table.global_away
            if name == "league":
                shares_used.append((s_home, s_away))
            split = HalfSplit(matrix, s_home, s_away)
            for market, (canon, period, selections) in MARKETS.items():
                outcome = result[market]
                if outcome is None:
                    continue
                line = LINES.get(market, float("nan"))
                p = half_leg_probability(split, canon, period, outcome, line)
                losses[name][market].append(-math.log(max(p, 1e-12)))
                if name == "league":
                    counts[market] += 1
                    for selection in selections:
                        q = half_leg_probability(split, canon, period, selection, line)
                        predicted[market][selection] = predicted[market].get(selection, 0.0) + q
                        observed[market][selection] = observed[market].get(selection, 0.0) + float(selection == outcome)
            # conditional log-loss of the half-time score given the full-time score
            h, a, h1, a1 = int(row.home_goals), int(row.away_goals), int(row.ht_home_goals), int(row.ht_away_goals)
            logp = (
                math.lgamma(h + 1) - math.lgamma(h1 + 1) - math.lgamma(h - h1 + 1) + h1 * math.log(s_home) + (h - h1) * math.log(1 - s_home)
                + math.lgamma(a + 1) - math.lgamma(a1 + 1) - math.lgamma(a - a1 + 1) + a1 * math.log(s_away) + (a - a1) * math.log(1 - s_away)
            )  # fmt: skip
            losses[name]["HT score | FT score"].append(-logp)
        if (i + 1) % 2000 == 0:
            print(f"  {i + 1} matches, {time.time() - started:.0f} s", flush=True)
    report: dict[str, object] = {
        "start": args.start,
        "end": args.end,
        "matches": int(len(frame) - skipped),
        "skipped_no_fit": skipped,
        "seconds": round(time.time() - started, 1),
        "league_shares_used": {
            "home_mean": float(np.mean([s[0] for s in shares_used])),
            "away_mean": float(np.mean([s[1] for s in shares_used])),
            "home_p5_p95": [float(np.percentile([s[0] for s in shares_used], q)) for q in (5, 95)],
        },
        "markets": {},
    }
    for market in [*MARKETS, "HT score | FT score"]:
        base = np.array(losses["naive"][market])
        entry: dict[str, object] = {"n": int(len(base))}
        for name in names:
            values = np.array(losses[name][market])
            diff = values - base
            entry[name] = {
                "log_loss": round(float(values.mean()), 5),
                "diff_vs_naive": round(float(diff.mean()), 5),
                "se": round(float(diff.std(ddof=1) / math.sqrt(len(diff))), 5) if len(diff) > 1 else None,
            }
        if market in MARKETS:
            n = counts[market]
            entry["calibration_league"] = {
                s: {"predicted": round(predicted[market][s] / n, 4), "observed": round(observed[market][s] / n, 4)} for s in predicted[market]
            }
        report["markets"][market] = entry  # type: ignore[index]
    text = json.dumps(report, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
