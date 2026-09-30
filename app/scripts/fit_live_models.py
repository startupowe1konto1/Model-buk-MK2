"""Fit the live models for upcoming fixtures, choose their hyper-parameters, and print the forecasts.

Offline apart from ``forecast --source api`` (one ``/fixtures?date=`` request per day).  Reads the
history database read-only (a nightly job rebuilds it, so it is opened briefly and closed) and writes only
under ``Model-Buk-Data/scanner/model_cache``.  The models, the pool weights and the protocol are those of
:mod:`model_buk.scanner.live_models`; nothing is retuned here.

Commands (from the ``app`` directory)::

    .venv\\Scripts\\python scripts\\fit_live_models.py select --season 2026 --jobs 3   # once per season (about 20-40 minutes)
    .venv\\Scripts\\python scripts\\fit_live_models.py fit --jobs 3                    # nightly: refit every cache (minutes)
    .venv\\Scripts\\python scripts\\fit_live_models.py forecast --days 3               # forecasts of the next 3 days
    .venv\\Scripts\\python scripts\\fit_live_models.py forecast --days 3 --source api  # fixtures of all leagues from the API
    .venv\\Scripts\\python scripts\\fit_live_models.py check-weights --season 2026     # recompute the pool weights, compare

``fit`` trains on everything that kicked off before ``as-of - 3 h`` (default: now) and caches the state per
(model, league, date); ``forecast`` only predicts from that cache.  Schedule ``fit`` nightly, after the history
tables have been rebuilt.  The API key of ``forecast --source api`` comes from the ``API_FOOTBALL_KEY``
environment variable and is never printed; the run stops when the remaining daily quota reaches the reserve.
"""

from __future__ import annotations

import os

# One BLAS thread per process: the worker processes are the parallelism (the machine is shared).
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_buk.scanner.live_models as lm  # noqa: E402

MAX_JOBS = 3
QUOTA_RESERVE = 1500
MAX_API_DAYS = 14
FIXTURE_COLUMNS = [
    "fixture_id", "league_id", "league_name", "country", "season", "round", "kickoff_utc", "status_short",
    "home_team_id", "home_team", "away_team_id", "away_team", "home_goals", "away_goals",
]  # fmt: skip


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _config(args: argparse.Namespace) -> lm.LiveConfig:
    kwargs = {}
    if args.cache_dir:
        kwargs["cache_dir"] = Path(args.cache_dir)
    if args.history_db:
        kwargs["history_db"] = Path(args.history_db)
    return lm.LiveConfig(**kwargs)


def _jobs(value: int) -> int:
    if value > MAX_JOBS:
        raise SystemExit(f"at most {MAX_JOBS} worker processes on the shared machine")
    return max(1, value)


def _print(text: str) -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# select / fit
# ---------------------------------------------------------------------------


def cmd_select(args: argparse.Namespace) -> None:
    config = _config(args)
    season = args.season if args.season is not None else lm.season_label(_now())
    started = time.perf_counter()
    matches = lm.load_live_matches(config.history_db)
    stats = lm.load_team_stats(config.history_db)
    _print(f"loaded {len(matches)} matches in {time.perf_counter() - started:.1f}s")
    models = tuple(args.models) if args.models else None
    out = lm.select_hyperparameters(
        season, matches, stats, config=config, models=models, n_jobs=_jobs(args.jobs), log=_print
    )
    _print(f"done in {out['total_seconds']}s")
    for name in ("dixon_coles", "nb2", "elo_davidson", "pi_gbm"):
        if name in out:
            _print(f"{name}: {out[name]}")
    _print(f"count: {out['count']}")


def cmd_fit(args: argparse.Namespace) -> None:
    config = _config(args)
    as_of = lm.utc(args.as_of) if args.as_of else _now()
    families = () if args.no_counts else ("corners", "yellow")
    manifest = lm.fit_live_models(
        as_of,
        config=config,
        leagues=args.leagues or None,
        families=families,
        n_jobs=_jobs(args.jobs),
        log=_print,
    )
    _print(f"leagues fitted: {len(manifest['leagues'])}; total {manifest['seconds']['total']}s ({manifest['seconds']})")
    _print(f"weights: season {manifest['weights_meta'].get('season_used')} ({manifest['weights_meta'].get('source')})")
    _print(f"hyper-parameters: {manifest['hyperparameters'].get('source')}")


def cmd_check_weights(args: argparse.Namespace) -> None:
    config = _config(args)
    season = args.season if args.season is not None else lm.season_label(_now())
    stored, meta = lm.load_pool_weights(season, config.comparison_db)
    started = time.perf_counter()
    computed = lm.compute_pool_weights(season, config.predictions_dir)
    worst = 0.0
    for market, weights in stored.items():
        for model in sorted(set(weights) | set(computed.get(market, {}))):
            a, b = weights.get(model, 0.0), computed.get(market, {}).get(model, 0.0)
            worst = max(worst, abs(a - b))
    _print(f"stored weights of season {meta['season_used']} vs recomputed from the stored forecasts: max |diff| = {worst:.2e} ({time.perf_counter() - started:.0f}s)")


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------


def fixtures_from_api(as_of: pd.Timestamp, days: int) -> pd.DataFrame:
    """Fixtures kicking off in ``[as_of, as_of + days)`` of every league, one request per UTC day."""
    from download_history import RealHistoryClient
    from model_buk.ingestion.api_football_history import RequestBudget
    from model_buk.ingestion.api_football_prematch import parse_listed_fixture

    if days > MAX_API_DAYS:
        raise SystemExit(f"--source api lists one day per request: at most {MAX_API_DAYS} days")
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise SystemExit("API_FOOTBALL_KEY is not set (User environment variable); cannot list fixtures from the API")
    client = RealHistoryClient(key)
    budget = RequestBudget(max_requests=MAX_API_DAYS + 1, reserve=QUOTA_RESERVE)
    end = as_of + pd.Timedelta(days=days)
    rows = []
    day = as_of.normalize()
    while day < end:
        if not budget.allow():
            _print(f"stopping: {budget.remaining_quota} API requests left today (reserve {QUOTA_RESERVE})")
            break
        payload, headers = client.get("/fixtures", {"date": day.strftime("%Y-%m-%d")})
        budget.record(headers)
        for obj in payload.get("response") or []:
            row = parse_listed_fixture(obj)
            if row is None:
                continue
            league = obj.get("league") or {}
            row["league_name"] = league.get("name")
            row["country"] = league.get("country")
            row["home_goals"] = np.nan
            row["away_goals"] = np.nan
            rows.append(row)
        day += pd.Timedelta(days=1)
    _print(f"fixtures from the API: {len(rows)} in {budget.spent} request(s); quota left {budget.remaining_quota}")
    frame = pd.DataFrame(rows, columns=FIXTURE_COLUMNS if not rows else None)
    stamp = pd.to_datetime(frame["kickoff_utc"], utc=True, format="mixed")
    return frame[(stamp >= as_of) & (stamp < end)].reset_index(drop=True)


def _fixtures_and_forecast(args: argparse.Namespace, *, components: bool):
    config = _config(args)
    as_of = lm.utc(args.as_of) if args.as_of else _now()
    end = as_of + pd.Timedelta(days=args.days)
    if args.fixtures_file:
        fixtures = pd.read_csv(args.fixtures_file)
        stamp = pd.to_datetime(fixtures["kickoff_utc"], utc=True, format="mixed")
        fixtures = fixtures[(stamp >= as_of) & (stamp < end)].reset_index(drop=True)
    elif args.source == "api":
        fixtures = fixtures_from_api(as_of, args.days)
    else:
        fixtures = lm.load_prematch_fixtures(args.prematch_db, as_of, end)
    if args.save_fixtures:
        Path(args.save_fixtures).parent.mkdir(parents=True, exist_ok=True)
        fixtures.to_csv(args.save_fixtures, index=False)
    _print(f"{len(fixtures)} fixtures in [{as_of.isoformat(timespec='minutes')}, {end.isoformat(timespec='minutes')})")
    started = time.perf_counter()
    frame, report = lm.forecast_with_report(
        fixtures, as_of, config=config, include_components=components, include_counts=not args.no_counts
    )
    seconds = time.perf_counter() - started
    _print(f"forecast: {len(frame)} rows for {int((report['n_rows'] > 0).sum())} of {len(report)} fixtures in {seconds:.2f}s")
    return fixtures, frame, report, seconds


def cmd_forecast(args: argparse.Namespace) -> None:
    fixtures, frame, report, _ = _fixtures_and_forecast(args, components=args.components)

    names = fixtures.drop_duplicates("fixture_id").set_index("fixture_id")
    report = report.assign(
        league=report["fixture_id"].map(names["league_name"]) if "league_name" in names else None,
        country=report["fixture_id"].map(names["country"]) if "country" in names else None,
    )
    by_league = (
        report.assign(covered=report["n_rows"] > 0)
        .groupby(["league_id", "league", "country"], dropna=False)
        .agg(fixtures=("fixture_id", "size"), covered=("covered", "sum"))
        .reset_index()
        .sort_values(["covered", "fixtures"], ascending=False)
    )
    _print("\nfixtures covered per competition (forecast rows / listed):")
    _print(by_league.to_string(index=False))
    _print("\nstatus: " + ", ".join(f"{k}={v}" for k, v in report["status"].value_counts().items()))

    if len(frame):
        pooled = frame[frame["model_name"] == lm.MODEL_NAME_POOL]

        def pick(market: str, selection: str, line: float | None = None) -> pd.Series:
            sub = pooled[(pooled["market"] == market) & (pooled["selection"] == selection)]
            sub = sub[sub["line"].isna()] if line is None else sub[sub["line"] == line]
            return sub.set_index("fixture_id")["p_model"]

        table = pd.DataFrame(
            {
                "home": pick("1x2", "home"),
                "draw": pick("1x2", "draw"),
                "away": pick("1x2", "away"),
                "over2.5": pick("ou", "over", 2.5),
                "btts": pick("btts", "yes"),
            }
        )
        info = fixtures.drop_duplicates("fixture_id").set_index("fixture_id")
        table.insert(0, "match", info["home_team"].astype(str).str.slice(0, 18) + " - " + info["away_team"].astype(str).str.slice(0, 18))
        table.insert(0, "kickoff", pd.to_datetime(info["kickoff_utc"], utc=True, format="mixed").dt.strftime("%m-%d %H:%M"))
        _print("\npooled 1X2 / over 2.5 / BTTS (first 25 fixtures):")
        _print(table.head(25).round(3).to_string())
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(out, index=False)
        report.to_csv(out.with_name(out.stem + "_report.csv"), index=False)
        _print(f"wrote {out}")


def _pinnacle_probabilities(odds_db: str, fixture_ids: list[int]) -> pd.DataFrame:
    """Devigged (proportional) Pinnacle 1X2 and total-goals 2.5 probabilities of the latest quotes, per fixture."""
    import sqlite3
    from contextlib import closing

    from model_buk.markets.pricing import devig_proportional

    with closing(sqlite3.connect(f"file:{Path(odds_db).as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT bookmaker_id FROM quotes WHERE bookmaker = 'Pinnacle' LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("no Pinnacle quotes in the odds archive")
        quotes = pd.read_sql_query(
            "SELECT fixture_id, bet_id, selection, odd FROM quote_state WHERE bookmaker_id = ? AND bet_id IN (1, 5)",
            connection,
            params=(int(row[0]),),
        )
    quotes = quotes[quotes["fixture_id"].isin(fixture_ids)]
    out = []
    for fixture, group in quotes.groupby("fixture_id"):
        wide = group.set_index(["bet_id", "selection"])["odd"]
        try:
            h, d, a = devig_proportional([wide[(1, "Home")], wide[(1, "Draw")], wide[(1, "Away")]])
            entry = {"fixture_id": int(fixture), "home": h, "draw": d, "away": a}
        except KeyError:
            continue
        try:
            over, under = devig_proportional([wide[(5, "Over 2.5")], wide[(5, "Under 2.5")]])
            entry["over2.5"] = over
        except KeyError:
            entry["over2.5"] = np.nan
        out.append(entry)
    return pd.DataFrame(out).set_index("fixture_id")


def cmd_compare(args: argparse.Namespace) -> None:
    """Sanity check: the pooled forecasts against devigged Pinnacle prices (they are not used anywhere else)."""
    fixtures, frame, report, _ = _fixtures_and_forecast(args, components=True)
    if frame.empty:
        _print("no forecasts to compare")
        return
    market = _pinnacle_probabilities(args.odds_db, sorted(frame["fixture_id"].unique().tolist()))
    _print(f"{len(market)} forecast fixtures have a Pinnacle 1X2 quote")

    def model_table(name: str) -> pd.DataFrame:
        part = frame[frame["model_name"] == name]

        def pick(m: str, s: str, line: float | None = None) -> pd.Series:
            sub = part[(part["market"] == m) & (part["selection"] == s)]
            sub = sub[sub["line"].isna()] if line is None else sub[sub["line"] == line]
            return sub.set_index("fixture_id")["p_model"]

        return pd.DataFrame({"home": pick("1x2", "home"), "draw": pick("1x2", "draw"), "away": pick("1x2", "away"), "over2.5": pick("ou", "over", 2.5)})

    valid = report.set_index("fixture_id")["validated"]
    rows = []
    for name in [lm.MODEL_NAME_POOL, *sorted(n for n in frame["model_name"].unique() if n not in (lm.MODEL_NAME_POOL, lm.MODEL_NAME_COUNT))]:
        model = model_table(name)
        joined = model[["home", "draw", "away"]].join(market[["home", "draw", "away"]], lsuffix="_m", rsuffix="_p", how="inner").dropna()
        if joined.empty:
            continue
        diff = pd.DataFrame({c: joined[f"{c}_m"] - joined[f"{c}_p"] for c in ("home", "draw", "away")})
        both = model[["over2.5"]].join(market[["over2.5"]], lsuffix="_m", rsuffix="_p", how="inner").dropna()
        rows.append(
            {
                "model": name,
                "n": len(joined),
                "MAD 1x2 (mean over 3 outcomes)": float(diff.abs().to_numpy().mean()),
                "MAD home": float(diff["home"].abs().mean()),
                "bias home": float(diff["home"].mean()),
                "bias draw": float(diff["draw"].mean()),
                "MAD over2.5": float((both["over2.5_m"] - both["over2.5_p"]).abs().mean()) if len(both) else np.nan,
                "n over2.5": len(both),
            }
        )
    table = pd.DataFrame(rows)
    _print(table.round(4).to_string(index=False))
    pooled = model_table(lm.MODEL_NAME_POOL).join(market, lsuffix="_m", rsuffix="_p", how="inner").dropna(subset=["home_m", "home_p"])
    pooled["validated"] = pooled.index.map(valid)
    pooled["mad"] = sum((pooled[f"{c}_m"] - pooled[f"{c}_p"]).abs() for c in ("home", "draw", "away")) / 3
    _print("\npooled MAD by league scope: " + str(pooled.groupby("validated")["mad"].agg(["count", "mean"]).round(4).to_dict("index")))
    _print(f"row-sum check: max |sum(1x2) - 1| = {float((pooled[['home_m', 'draw_m', 'away_m']].sum(axis=1) - 1).abs().max()):.2e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", default=None, help=f"default: {lm.DEFAULT_CACHE_DIR}")
    parser.add_argument("--history-db", default=None, help=f"default: {lm.HISTORY_DB}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("select", help="choose the hyper-parameters for a season (the protocol's inner fold)")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--models", nargs="*", default=None, help="default: the models with pool weight")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("fit", help="refit and cache every model (the nightly job)")
    p.add_argument("--as-of", default=None, help="UTC timestamp; default: now")
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--leagues", nargs="*", default=None, help="league codes; default: every league with enough history")
    p.add_argument("--no-counts", action="store_true", help="skip the corner / card regressions")
    p.set_defaults(func=cmd_fit)

    p = sub.add_parser("forecast", help="forecast the fixtures of the next days from the cached fit")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--as-of", default=None)
    p.add_argument("--source", choices=("prematch", "api"), default="prematch")
    p.add_argument("--prematch-db", default=str(lm.DATA_ROOT / "api-football-prematch" / "prematch.sqlite3"))
    p.add_argument("--fixtures-file", default=None, help="a saved fixtures CSV instead of --source (no API requests)")
    p.add_argument("--save-fixtures", default=None, help="save the fixtures that were listed as CSV")
    p.add_argument("--components", action="store_true", help="also emit the rows of every pool component")
    p.add_argument("--no-counts", action="store_true")
    p.add_argument("--out", default=None, help="write the model frame (and a _report file) as CSV")
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("compare", help="compare the pooled 1X2 / over 2.5 with devigged Pinnacle prices (sanity check)")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--as-of", default=None)
    p.add_argument("--source", choices=("prematch", "api"), default="api")
    p.add_argument("--prematch-db", default=str(lm.DATA_ROOT / "api-football-prematch" / "prematch.sqlite3"))
    p.add_argument("--fixtures-file", default=None, help="a saved fixtures CSV instead of --source (no API requests)")
    p.add_argument("--save-fixtures", default=None, help="save the fixtures that were listed as CSV")
    p.add_argument("--odds-db", default=str(lm.DATA_ROOT / "api-football-odds" / "odds.sqlite3"))
    p.add_argument("--no-counts", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("check-weights", help="recompute the pool weights from the stored forecasts and compare")
    p.add_argument("--season", type=int, default=None)
    p.set_defaults(func=cmd_check_weights)

    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
