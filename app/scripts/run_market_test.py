"""Model versus market after tax (plan days 4-5): forecasts against Pinnacle, pooling, strategies, report.

Offline.  Reads the football-data odds joined to API-Football fixtures (``joined/fd_af_matches.sqlite``), the stored
forecasts of the goal protocol (``predictions/*.sqlite``) and the API-Football history (only for the form features of the
market-residual model); the live odds archive is read for the margins of the soft books.  Nothing there is modified.
Everything is written to ``Model-Buk-Data/market_test/`` (``market_test.sqlite`` holds every table, ``cache_*.pkl`` the
intermediate arrays, ``cells/`` one file per finished decision setup so an interrupted run resumes) and the Polish report to
``reports/Model kontra rynek - pytanie 2.md``.

Stages (``all`` runs them in this order):

- ``panel``       odds of 1X2 and over/under 2.5, de-vigged under three methods, overround tables (cached).
- ``forecasts``   ex-ante model per season, pool with the market (weight fitted on earlier seasons), market-residual model.
- ``loss``        log-loss of every model, pool and residual model versus the Pinnacle opening and closing line, per league.
- ``strategies``  every decision setup of ``market_test.cell_plan`` for the three tax regimes, singles and accumulators of
                  2 to 5 legs, with bootstrap intervals, random baseline, verdicts and the family-wise correction.
- ``margins``     overround of the bookmakers of the live archive.
- ``report``      the Polish report from the stored tables.

Usage (from the app/ directory; at most 3 worker processes)::

    .venv\\Scripts\\python scripts\\run_market_test.py --stage all --workers 3 > runtime\\run_market_test.log
    .venv\\Scripts\\python scripts\\run_market_test.py --stage report
"""

from __future__ import annotations

import os

# Small array work in worker processes: extra BLAS threads only fight the other jobs for cores.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import pickle  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from contextlib import closing  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_buk.evaluation import market_test as mt  # noqa: E402
from model_buk.evaluation.significance import holm_adjust  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = APP_DIR.parent
REPORT_PATH = REPO_DIR / "reports" / "Model kontra rynek - pytanie 2.md"
OUT = mt.OUTPUT_DIR
CELLS_DIR = OUT / "cells"
MAX_WORKERS = 3
ALL_SEASONS = (2022, 2023, 2024, 2025)
LOAD_FROM = 2016  # seasons before 2022 only feed the drift scale of the haircut and the residual model's training


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def write_table(name: str, frame: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].map(lambda v: isinstance(v, (tuple, list))).any():
            frame[column] = frame[column].map(lambda v: "|".join(str(x) for x in v) if isinstance(v, (tuple, list)) else v)
    with closing(sqlite3.connect(mt.RESULTS_DB)) as connection:
        frame.to_sql(name, connection, if_exists="replace", index=False)
        connection.commit()


def read_table(name: str) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{mt.RESULTS_DB.as_posix()}?mode=ro", uri=True)) as connection:
        return pd.read_sql_query(f"SELECT * FROM {name}", connection)


def pickle_dump(obj: object, path: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        pickle.dump(obj, handle, protocol=pickle.HIGHEST_PROTOCOL)


def pickle_load(path: Path):
    with open(path, "rb") as handle:
        return pickle.load(handle)


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #


def stage_panel(force: bool) -> None:
    say("panel: reading joined odds")
    odds = mt.load_joined_odds(seasons=range(LOAD_FROM, 2026))
    overrounds = []
    for name in mt.MARKET_ORDER:
        target = OUT / f"cache_panel_{name}.pkl"
        spec = mt.MARKET_SPECS[name]
        if target.exists() and not force:
            panel = mt.load_panel_cache(target)
        else:
            protocol = mt.load_protocol_frame(spec)
            panel = mt.build_market_panel(odds, spec, matchday=dict(zip(protocol["fixture_id"], protocol["matchday"])))
            OUT.mkdir(parents=True, exist_ok=True)
            mt.save_panel_cache(panel, target)
        say(f"panel {name}: {len(panel)} fixtures")
        overrounds.append(mt.overround_table(panel))
    write_table("overround", pd.concat(overrounds, ignore_index=True))


def _load_panels() -> dict[str, mt.MarketPanel]:
    return {name: mt.load_panel_cache(OUT / f"cache_panel_{name}.pkl") for name in mt.MARKET_ORDER}


def stage_forecasts(force: bool) -> None:
    panels = _load_panels()
    features_path = OUT / "cache_features.pkl"
    if features_path.exists() and not force:
        features = pd.read_pickle(features_path)
    else:
        say("forecasts: building point-in-time form features")
        features = mt.load_form_features()
        features.to_pickle(features_path)
    weights, residual_rows = [], []
    for name, panel in panels.items():
        target = OUT / f"cache_forecasts_{name}.pkl"
        if target.exists() and not force:
            say(f"forecasts {name}: cached")
            continue
        say(f"forecasts {name}: loading stored models")
        arrays = mt.load_model_arrays(panel, mt.BASE_MODELS + mt.DERIVED_MODELS + (mt.BASELINE_MODEL,))
        chosen = {s: m for s, m in mt.selected_model_by_season(panel.spec).items() if s in mt.SELECTION_SEASONS}
        pool_selected, pool_of_pool, resid = {}, {}, {}
        for method in mt.DEVIG_ORDER:
            pool_selected[method], table = mt.pooled_with_market(panel, method, arrays, chosen)
            table["variant"] = "selected_best"
            weights.append(table.assign(market=name))
            if "pool_models" in arrays:
                pool_of_pool[method], table = mt.pooled_with_market(
                    panel, method, arrays, {s: "pool_models" for s in mt.SELECTION_SEASONS}
                )
                table["variant"] = "pool_models"
                weights.append(table.assign(market=name))
            if name == "1x2":
                say(f"forecasts {name}: market-residual model, de-vig {method}")
                resid[method], table = mt.residual_forecasts_1x2(panel, method, features, ALL_SEASONS)
                residual_rows.append(table.assign(market=name))
        pickle_dump(
            {"arrays": arrays, "chosen": chosen, "pool_selected": pool_selected, "pool_of_pool": pool_of_pool, "resid": resid},
            target,
        )
    if weights:
        write_table("pool_weights", pd.concat(weights, ignore_index=True))
    if residual_rows:
        write_table("residual_selection", pd.concat(residual_rows, ignore_index=True))


def _finite(*matrices: np.ndarray) -> np.ndarray:
    return mt._finite_rows(*matrices)


def stage_loss(n_resamples: int) -> None:
    panels = _load_panels()
    league_tables, season_tables, pooled_rows = [], [], []
    for name, panel in panels.items():
        state = pickle_load(OUT / f"cache_forecasts_{name}.pkl")
        arrays = state["arrays"]
        season = panel.season
        market_ok = _finite(panel.fair[("ps_open", "power")], panel.fair[("ps_close", "power")], panel.y)

        def sample(models, seasons):
            mask = market_ok & np.isin(season, seasons)
            for model in models:
                mask &= _finite(arrays[model])
            return mask

        base = [m for m in (mt.BASELINE_MODEL, *mt.BASE_MODELS) if m in arrays]
        everything = base + list(mt.DERIVED_MODELS)
        say(f"loss {name}: sample A (2023-2025, all models)")
        mask_a = sample(everything, (2023, 2024, 2025))
        league_tables.append(
            mt.model_vs_market(panel, {m: arrays[m] for m in everything}, mask=mask_a, sample="A_2023_2025_all_models", n_resamples=n_resamples)
        )
        season_tables.append(
            mt.loss_by_season(panel, {m: arrays[m] for m in everything}, mask=mask_a).assign(sample="A_2023_2025_all_models")
        )
        say(f"loss {name}: sample B (2022-2025, base models)")
        mask_b = sample(base, ALL_SEASONS)
        league_tables.append(
            mt.model_vs_market(panel, {m: arrays[m] for m in base}, mask=mask_b, sample="B_2022_2025_base_models", n_resamples=n_resamples)
        )
        season_tables.append(
            mt.loss_by_season(panel, {m: arrays[m] for m in base}, mask=mask_b).assign(sample="B_2022_2025_base_models")
        )
        for method in mt.DEVIG_ORDER:
            say(f"loss {name}: pool with market and residual, de-vig {method}")
            forecasts = {"selected_best": arrays["selected_best"], "pool_with_market": state["pool_selected"][method]}
            if name == "1x2":
                forecasts["residual"] = state["resid"][method]
            mask_c = market_ok & np.isin(season, (2023, 2024, 2025))
            for matrix in forecasts.values():
                mask_c &= _finite(matrix)
            league_tables.append(
                mt.model_vs_market(
                    panel, forecasts, mask=mask_c, sample="C_2023_2025_pool_and_residual", methods=(method,), n_resamples=n_resamples
                )
            )
            season_tables.append(
                mt.loss_by_season(panel, forecasts, mask=mask_c, methods=(method,)).assign(sample="C_2023_2025_pool_and_residual")
            )
            if state["pool_of_pool"]:
                forecasts_d = {"pool_models": arrays["pool_models"], "pool_with_market_pool_models": state["pool_of_pool"][method]}
                mask_d = market_ok & np.isin(season, (2024, 2025))
                for matrix in forecasts_d.values():
                    mask_d &= _finite(matrix)
                league_tables.append(
                    mt.model_vs_market(
                        panel, forecasts_d, mask=mask_d, sample="D_2024_2025_pool_models", methods=(method,), n_resamples=n_resamples
                    )
                )
    league = pd.concat(league_tables, ignore_index=True)
    pooled = league[league["league_code"] == mt.POOLED_LABEL].copy()
    pooled["p_holm_pooled_family"] = np.nan
    for _, index in pooled.groupby(["market", "sample"]).groups.items():
        pooled.loc[index, "p_holm_pooled_family"] = holm_adjust(pooled.loc[index, "p_value"].to_numpy(dtype=float))
    write_table("loss_by_league", league)
    write_table("loss_pooled", pooled)
    write_table("loss_by_season", pd.concat(season_tables, ignore_index=True))
    say(f"loss: {len(league)} league rows written")


def _legs_for(polish_overround: float) -> mt.LegTable:
    tables = []
    features = None
    for name, panel in _load_panels().items():
        state = pickle_load(OUT / f"cache_forecasts_{name}.pkl")
        base = {k: v for k, v in state["arrays"].items() if k in mt.BASE_MODELS}
        tables.append(
            mt.build_leg_table(
                panel,
                seasons=ALL_SEASONS,
                model_base=base,
                chosen=state["chosen"],
                pool=state["pool_selected"],
                resid=state["resid"] or None,
                polish_overround=polish_overround,
            )
        )
    return mt.concat_leg_tables(tables)


def _cell_id(family: str, cell: mt.CellSpec, overround: float) -> str:
    text = repr((family, sorted(cell.key().items()), round(overround, 4)))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _run_one(args):
    legs, family, cell, overround, n_resamples, n_replicates = args
    target = CELLS_DIR / f"{_cell_id(family, cell, overround)}.pkl"
    if target.exists():
        return pickle_load(target)
    tags = {"family": family}
    if cell.book in ("pl_ps", "pl_b365"):
        tags["polish_overround"] = overround
    rows, bets = mt.evaluate_cell(legs, cell, n_resamples=n_resamples, n_replicates=n_replicates, tags=tags)
    result = (rows, bets)
    pickle_dump(result, target)
    return result


def stage_strategies(workers: int, n_resamples: int, n_replicates: int) -> None:
    from joblib import Parallel, delayed

    workers = max(1, min(int(workers), MAX_WORKERS))
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    say("strategies: building the leg tables")
    tasks = []
    legs_main = _legs_for(mt.DEFAULT_POLISH_OVERROUND)
    for family, cell in mt.cell_plan():
        tasks.append((legs_main, family, cell, mt.DEFAULT_POLISH_OVERROUND, n_resamples, n_replicates))
    for overround in (1.04, 1.08):
        legs_alt = _legs_for(overround)
        for book in ("pl_ps", "pl_b365"):
            for strategy in ("reference", "pool", "residual"):
                for z in (mt.DEFAULT_Z, 0.0):
                    for method in mt.DEVIG_ORDER:
                        cell = mt.CellSpec(strategy=strategy, book=book, method=method, z=z)
                        tasks.append((legs_alt, "polish_margin", cell, overround, n_resamples, n_replicates))
    say(f"strategies: {len(tasks)} setups, {workers} workers")
    started = time.time()
    results = Parallel(n_jobs=workers, backend="loky", verbose=0)(delayed(_run_one)(t) for t in tasks)
    say(f"strategies: done in {time.time() - started:.0f} s")
    rows = [row for r, _ in results for row in r]
    bets = [b for _, stored in results for b in stored]
    summary = pd.DataFrame(rows)
    write_table("strategy_summary", summary)
    if bets:
        all_bets = pd.concat(bets, ignore_index=True)
        write_table("strategy_bets", all_bets)
    verdicts = mt.combine_devig_verdicts(summary)
    write_table("strategy_verdicts", mt.apply_family_correction(verdicts, mt.plan_family_masks(verdicts)))
    veto_rows = []
    for book in ("bet365", "max", "avg"):
        for method in mt.DEVIG_ORDER:
            for z in (mt.DEFAULT_Z, 0.0):
                cell = mt.CellSpec(strategy="reference", book=book, method=method, z=z)
                veto_rows.append({**cell.key(), **mt.veto_effect(legs_main, cell, n_resamples=n_resamples)})
    write_table("veto_effect", pd.DataFrame(veto_rows))
    say(f"strategies: veto effect on {len(veto_rows)} setups")


def stage_margins() -> None:
    table = mt.measure_live_margins()
    write_table("live_margins", table)
    say("live margins:\n" + table.to_string())


# --------------------------------------------------------------------------- #
# Polish report
# --------------------------------------------------------------------------- #

MINUS = "−"
NBSP = " "
REGIME_LABEL = {"tau_0.88": "τ = 0,88", "tau_0.94": "τ = 0,94", "tau_1.00": "τ = 1,00"}
STRATEGY_LABEL = {
    "reference": "czysta referencja",
    "reference_veto": "referencja z wetem modelu",
    "pool": "pula z rynkiem",
    "residual": "model residualny",
    "model": "sam model (kontrola)",
}
BOOK_LABEL = {
    "bet365": "Bet365",
    "max": "Max (najlepsza cena rynku)",
    "avg": "Avg (średnia rynku)",
    "pl_ps": "polska cena z Pinnacle",
    "pl_b365": "polska cena z Bet365",
}
BOOK_SHORT = {"bet365": "Bet365", "max": "Max", "avg": "Avg", "pl_ps": "PL z Pinnacle", "pl_b365": "PL z Bet365"}
STRUCTURE_LABEL = {1: "single", 2: "AKO 2", 3: "AKO 3", 4: "AKO 4", 5: "AKO 5"}
MODEL_LABEL = {
    "baseline_league_freq": "częstości ligowe (baseline)",
    "dixon_coles": "Dixon–Coles",
    "nb2": "NB2",
    "gas_poisson": "GAS-Poisson",
    "elo_davidson": "Elo–Davidson",
    "pi_gbm": "pi-ratingi + GBM",
    "pi_ratings": "pi-ratingi",
    "selected_best": "wybór ex ante",
    "pool_models": "pula modeli",
}
MARKET_LABEL_PL = {"1x2": "1X2", "over_under_2.5": "over/under 2,5", "all": "1X2 i over/under 2,5"}
SEL_SEASONS = "-".join(str(s) for s in mt.SELECTION_SEASONS)
HEADLINE = ("reference", "reference_veto", "pool", "residual")


def _num(x, digits: int = 2, sign: bool = False) -> str:
    if x is None or not np.isfinite(x):
        return "n/d"
    text = f"{abs(x):.{digits}f}".replace(".", ",")
    zero = float(text.replace(",", ".")) == 0.0
    if x < 0 and not zero:
        return MINUS + text
    return "+" + text if sign and not zero else text


def _pct(x, digits: int = 1, sign: bool = True) -> str:
    if x is None or not np.isfinite(x):
        return "n/d"
    return _num(100.0 * x, digits, sign) + "%"


def _int(n) -> str:
    if n is None or not np.isfinite(n):
        return "n/d"
    return f"{int(round(n)):,}".replace(",", NBSP)


def _ci(low, high, digits: int = 4, sign: bool = True) -> str:
    return f"[{_num(low, digits, sign)}; {_num(high, digits, sign)}]"


def _pci(mean, low, high, digits: int = 1) -> str:
    return f"{_pct(mean, digits)} [{_pct(low, digits)}; {_pct(high, digits)}]"


def _pval(p) -> str:
    if p is None or not np.isfinite(p):
        return "n/d"
    return "< 0,001" if p < 0.001 else _num(p, 3)


def _table(header, rows, align=None) -> list[str]:
    align = align or ["l"] + ["r"] * (len(header) - 1)
    marks = {"l": ":---", "r": "---:", "c": ":---:"}
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(marks[a] for a in align) + "|"]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return lines


def _z_text(z: float) -> str:
    return "0" if not z else _num(z, 3).rstrip("0").rstrip(",")


def _flag(row, key: str) -> bool:
    value = row[key]
    return bool(value) if value == value else False


def _query(sql: str, params=()) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{mt.RESULTS_DB.as_posix()}?mode=ro", uri=True)) as connection:
        return pd.read_sql_query(sql, connection, params=params)


def _cell_text(row) -> str:
    """``n; lower bound`` of a verdict row, bold when the lower bound is positive under every de-vig method."""
    n = row["n_bets_min"]
    if not n or not np.isfinite(n):
        return "0"
    if not _flag(row, "interval_ok"):
        return f"{_int(n)} (za mało)"
    text = f"{_int(n)}; {_pct(row['lo_min'], 1)}"
    return f"**{text}**" if _flag(row, "positive_lb") else text


def _verdict_frame(verdicts: pd.DataFrame, *, book: str, z: float, overround: float = 0.0, market_set: str = "all",
                   seasons: str = SEL_SEASONS) -> pd.DataFrame:
    keep = (
        (verdicts["market_set"] == market_set) & (verdicts["book"] == book) & np.isclose(verdicts["z"], z)
        & (verdicts["seasons"] == seasons) & np.isclose(verdicts["polish_overround"], overround)
    )
    return verdicts[keep]


def _structure_table(frame: pd.DataFrame, strategies) -> list[str]:
    rows = []
    for strategy in strategies:
        for regime in mt.TAX_REGIMES:
            line = [STRATEGY_LABEL[strategy], REGIME_LABEL[regime]]
            for n_legs in range(1, mt.MAX_LEGS + 1):
                part = frame[(frame["strategy"] == strategy) & (frame["regime"] == regime) & (frame["n_legs"] == n_legs)]
                line.append(_cell_text(part.iloc[0]) if len(part) else "n/d")
            rows.append(line)
    header = ["Strategia", "Reżim"] + [STRUCTURE_LABEL[n] for n in range(1, mt.MAX_LEGS + 1)]
    return _table(header, rows, ["l", "l", "r", "r", "r", "r", "r"])


def _family_rows(verdicts: pd.DataFrame, family: str, strategies) -> tuple[list[dict], dict[str, int]]:
    frame = verdicts[mt.plan_family_masks(verdicts)[family]]
    rows = []
    for strategy in strategies:
        for regime in mt.TAX_REGIMES:
            part = frame[(frame["strategy"] == strategy) & (frame["regime"] == regime)]
            readable = part[part["interval_ok"].astype(bool)]
            best = readable.sort_values("lo_min", ascending=False).head(1)
            rows.append(
                {
                    "strategy": strategy, "regime": regime, "n": len(part), "readable": len(readable),
                    "positive": int(part["positive_lb"].astype(bool).sum()),
                    "signal": int(part[f"signal_{family}"].astype(bool).sum()),
                    "bets_max": int(part["n_bets_max"].max()) if len(part) else 0,
                    "best": best.iloc[0] if len(best) else None,
                }
            )
    counts = {"n": len(frame), "readable": int(frame["interval_ok"].astype(bool).sum()), "positive": int(frame["positive_lb"].astype(bool).sum()),
              "signal": int(frame[f"signal_{family}"].astype(bool).sum())}
    return rows, counts


def _answer(row: dict) -> str:
    if row["signal"]:
        return "**TAK**"
    if row["positive"]:
        return "tak, ale nie po korekcie"
    if row["readable"]:
        return "NIE"
    return "za mało zakładów"


def _best_text(row: dict) -> str:
    b = row["best"]
    if b is None:
        return "brak ocenialnego wariantu"
    return f"{_pct(b['lo_min'], 1)} ({BOOK_SHORT[b['book']]}, z = {_z_text(b['z'])}, {STRUCTURE_LABEL[int(b['n_legs'])]}, n = {_int(b['n_bets_min'])})"


def _load_all() -> dict[str, pd.DataFrame]:
    tables = {}
    for name in ("overround", "pool_weights", "residual_selection", "loss_by_league", "loss_pooled", "loss_by_season", "strategy_summary",
                 "strategy_verdicts", "veto_effect", "live_margins"):
        tables[name] = read_table(name)
    return tables


def _gap_frequency() -> pd.DataFrame:
    """Share of matches with a leg whose price beats the de-vigged Pinnacle opening line by a threshold (test seasons)."""
    rows = []
    for name, panel in _load_panels().items():
        keep = np.isin(panel.season, mt.SELECTION_SEASONS)
        fair = panel.fair[("ps_open", "power")]
        for book in ("bet365", "avg", "max"):
            odds = panel.odds[book]
            ok = keep & np.isfinite(fair).all(axis=1) & np.isfinite(odds).all(axis=1)
            edge = np.max(odds[ok] * fair[ok] - 1.0, axis=1)
            rows.append({"market": name, "book": book, "n": int(ok.sum()), "ge0": float((edge >= 0.0).mean()),
                         "ge64": float((edge >= 0.064).mean()), "ge136": float((edge >= 0.136).mean()), "n136": int((edge >= 0.136).sum())})
    return pd.DataFrame(rows)


def _drift_scales() -> dict[str, list[float]]:
    return {
        name: [mt.drift_scale(p.fair[("ps_open", "power")], p.fair[("ps_close", "power")], p.season, s) for s in mt.SELECTION_SEASONS]
        for name, p in _load_panels().items()
    }


def _pooled_row(loss_pooled, market, sample, model, reference, method="power"):
    part = loss_pooled[(loss_pooled["market"] == market) & (loss_pooled["sample"] == sample) & (loss_pooled["model"] == model)
                       & (loss_pooled["reference"] == reference) & (loss_pooled["method"] == method)]
    return part.iloc[0]


def _loss_table(tables, market: str, sample: str, order) -> list[str]:
    league, pooled = tables["loss_by_league"], tables["loss_pooled"]
    frame = league[(league["market"] == market) & (league["sample"] == sample) & (league["method"] == "power")]
    leagues = frame[frame["league_code"] != mt.POOLED_LABEL]
    rows = []
    first = _pooled_row(pooled, market, sample, order[0], "open")
    rows.append(["Pinnacle, otwarcie (rynek)", _num(first["market_loss"], 4), "—", "—", "—", "—"])
    rows.append(["Pinnacle, zamknięcie (rynek)", _num(_pooled_row(pooled, market, sample, order[0], "close")["market_loss"], 4), "—", "—", "—", "—"])
    for model in order:
        cells = [MODEL_LABEL.get(model, model)]
        for reference in ("open", "close"):
            row = _pooled_row(pooled, market, sample, model, reference)
            sub = leagues[(leagues["model"] == model) & (leagues["reference"] == reference)]
            better = int(((sub["mean_diff"] > 0) & (sub["p_holm"] < 0.05)).sum())
            worse = int(((sub["mean_diff"] < 0) & (sub["p_holm"] < 0.05)).sum())
            if reference == "open":
                cells.append(_num(row["model_loss"], 4))
            cells.append(f"{_num(row['mean_diff'], 4, True)} {_ci(row['ci_lo'], row['ci_hi'])}")
            cells.append(f"{better} / {worse}")
        rows.append(cells)
    header = ["Model", "Log-loss", "Względem otwarcia (różnica [95% CI])", "Ligi lepsze / gorsze (Holm)",
              "Względem zamknięcia (różnica [95% CI])", "Ligi lepsze / gorsze (Holm)"]
    return _table(header, rows, ["l", "r", "r", "r", "r", "r"])


def _method_spread(tables, market, sample, models) -> float:
    pooled = tables["loss_pooled"]
    frame = pooled[(pooled["market"] == market) & (pooled["sample"] == sample) & pooled["model"].isin(models)]
    spread = frame.groupby(["model", "reference"])["mean_diff"].agg(lambda s: s.max() - s.min())
    return float(spread.max()) if len(spread) else float("nan")


def _summary_row(summary, **kw):
    frame = summary
    for key, value in kw.items():
        frame = frame[np.isclose(frame[key], value)] if isinstance(value, float) else frame[frame[key] == value]
    return frame.iloc[0] if len(frame) else None


def _annex_table(summary: pd.DataFrame, *, book: str, strategy: str, z: float) -> list[str]:
    rows = []
    for regime in mt.TAX_REGIMES:
        for n_legs in range(1, mt.MAX_LEGS + 1):
            r = _summary_row(summary, book=book, strategy=strategy, method="power", z=float(z), regime=regime, n_legs=n_legs,
                             market_set="all", seasons=SEL_SEASONS)
            if r is None or not r["n_bets"]:
                rows.append([REGIME_LABEL[regime], STRUCTURE_LABEL[n_legs], "0", "", "", "", "", "", "", ""])
                continue
            rows.append([
                REGIME_LABEL[regime], STRUCTURE_LABEL[n_legs], _int(r["n_bets"]),
                _pct(r["clv_net_median"], 2), _pct(r["share_beat_raw"], 1, False),
                _pci(r["clv_net_mean"], r["clv_net_lo"], r["clv_net_hi"], 2) if r["n_clv"] else "n/d",
                _pci(r["yield_flat"], r["yield_flat_lo"], r["yield_flat_hi"], 1),
                _pct(r["yield_kelly"], 1), _num(r["max_drawdown_flat_units"], 1), _pct(r["max_drawdown_kelly"], 1, False),
            ])
    header = ["Reżim", "Struktura", "Zakładów / kuponów", "Mediana CLV po podatku", "Pobite zamknięcia (przed podatkiem)",
              "Średnie CLV po podatku [95% CI]", "Yield płaski [95% CI]", "Yield ¼ Kelly", "Obsunięcie płaskie (jedn. 100 zł)", "Obsunięcie ¼ Kelly"]
    return _table(header, rows, ["l", "l", "r", "r", "r", "r", "r", "r", "r", "r"])


def _median_share_table(summary: pd.DataFrame) -> list[str]:
    """Median post-tax CLV and share of closing lines beaten (with the random-betting share) per strategy and regime.

    Singles, no haircut (z = 0: with the full haircut almost no bet survives), Bet365 and the best market price.  These two
    numbers are read before the mean (HANDOFF, sections B and C: the mean CLV has a floor at -100% and no ceiling).
    """
    rows = []
    for strategy in HEADLINE:
        for regime in mt.TAX_REGIMES:
            line = [STRATEGY_LABEL[strategy], REGIME_LABEL[regime]]
            for book in ("bet365", "max"):
                r = _summary_row(summary, book=book, strategy=strategy, method="power", z=0.0, regime=regime, n_legs=1,
                                 market_set="all", seasons=SEL_SEASONS)
                if r is None or not r["n_clv"]:
                    line += ["0", "", ""]
                    continue
                count = _int(r["n_bets"]) + ("" if r["interval_ok"] else " (za mało)")
                line += [count, _pct(r["clv_net_median"], 2), f"{_pct(r['share_beat_raw'], 1, False)} (los {_pct(r['random_share_raw_mean'], 1, False)})"]
            rows.append(line)
    header = ["Strategia", "Reżim", "Bet365: zakładów", "Bet365: mediana CLV po podatku", "Bet365: pobite zamknięcia (los)",
              "Max: zakładów", "Max: mediana CLV po podatku", "Max: pobite zamknięcia (los)"]
    return _table(header, rows, ["l", "l", "r", "r", "r", "r", "r", "r"])


def _random_table(summary: pd.DataFrame, *, book: str, strategy: str = "reference", z: float = 0.0) -> list[str]:
    rows = []
    for regime in mt.TAX_REGIMES:
        for n_legs in range(1, mt.MAX_LEGS + 1):
            r = _summary_row(summary, book=book, strategy=strategy, method="power", z=float(z), regime=regime, n_legs=n_legs,
                             market_set="all", seasons=SEL_SEASONS)
            if r is None or not r["n_clv"]:
                continue
            rows.append([
                REGIME_LABEL[regime], STRUCTURE_LABEL[n_legs], _int(r["n_clv"]),
                _pct(r["share_beat_raw"], 1, False),
                f"{_pct(r['random_share_raw_mean'], 1, False)} [{_pct(r['random_share_raw_p05'], 1, False)}; {_pct(r['random_share_raw_p95'], 1, False)}]",
                _pval(r["p_random_share_raw"]),
                _pct(r["clv_net_median"], 2), _pct(r["random_median_net_mean"], 2),
                _pct(r["clv_net_mean"], 2), _pct(r["random_mean_net_mean"], 2),
            ])
    header = ["Reżim", "Struktura", "Zakładów", "Pobite zamknięcia", "Losowo, ta sama liczba i pasmo kursów [5%; 95%]", "p (losowo ≥ strategia)",
              "Mediana CLV", "Losowo: mediana", "Średnie CLV", "Losowo: średnia"]
    return _table(header, rows, ["l", "l", "r", "r", "r", "r", "r", "r", "r", "r"])


def _margin_sensitivity_table(verdicts: pd.DataFrame) -> list[str]:
    rows = []
    for book in ("pl_b365", "pl_ps"):
        for overround in (1.04, 1.06, 1.08):
            for regime in mt.TAX_REGIMES:
                line = [BOOK_LABEL[book], f"{_num((overround - 1) * 100, 0)}%", REGIME_LABEL[regime]]
                for strategy in ("reference", "pool"):
                    for n_legs in (1, 5):
                        frame = _verdict_frame(verdicts, book=book, z=0.0, overround=overround)
                        part = frame[(frame["strategy"] == strategy) & (frame["regime"] == regime) & (frame["n_legs"] == n_legs)]
                        if part.empty:
                            line.append("n/d")
                            continue
                        r = part.iloc[0]
                        if not r["n_bets_min"]:
                            line.append("0")
                        elif not _flag(r, "interval_ok"):
                            line.append(f"{_int(r['n_bets_min'])} (za mało)")
                        else:
                            text = f"{_int(r['n_bets_min'])}; {_pct(r['mean_min'], 1)} [dolna {_pct(r['lo_min'], 1)}]"
                            line.append(f"**{text}**" if _flag(r, "positive_lb") else text)
                rows.append(line)
    header = ["Cena", "Marża", "Reżim", "Referencja, single", "Referencja, AKO 5", "Pula z rynkiem, single", "Pula z rynkiem, AKO 5"]
    return _table(header, rows, ["l", "r", "l", "r", "r", "r", "r"])


def _season_table() -> list[str]:
    rows = []
    for label, book, overround in (("Max, czysta referencja, z = 0", "max", None), ("polska cena z Bet365 (marża 4%), czysta referencja, z = 0", "pl_b365", 1.04),
                                   ("Bet365, czysta referencja, z = 0", "bet365", None)):
        extra = "" if overround is None else f" AND polish_overround = {overround}"
        bets = _query(
            "SELECT season, clv_net, clv_raw FROM strategy_bets WHERE strategy = 'reference' AND book = ? AND method = 'power' AND z = 0 "
            "AND market_set = 'all' AND seasons = ? AND n_legs = 1 AND regime = 'tau_1.00'" + extra, (book, SEL_SEASONS))
        for season, part in bets.groupby("season"):
            part = part.dropna(subset=["clv_net"])
            rows.append([label, str(int(season)), _int(len(part)), _pct(part["clv_net"].mean(), 2), _pct(part["clv_net"].median(), 2),
                         _pct((part["clv_raw"] > 0).mean(), 1, False)])
    return _table(["Wariant (τ = 1,00, single)", "Sezon", "Zakładów", "Średnie CLV", "Mediana CLV", "Pobite zamknięcia"], rows, ["l", "r", "r", "r", "r", "r"])


def _selection_edge(book: str, strategy: str = "reference") -> tuple[float, float, int]:
    bets = _query(
        "SELECT p, odds, clv_raw FROM strategy_bets WHERE strategy = ? AND book = ? AND method = 'power' AND z = 0 "
        "AND market_set = 'all' AND seasons = ? AND n_legs = 1 AND regime = 'tau_1.00'", (strategy, book, SEL_SEASONS))
    return float((bets["p"] * bets["odds"] - 1).mean()), float(bets["clv_raw"].mean()), int(len(bets))


def build_report(tables: dict[str, pd.DataFrame], gaps: pd.DataFrame) -> str:
    verdicts, summary = tables["strategy_verdicts"], tables["strategy_summary"]
    loss_pooled, loss_league = tables["loss_pooled"], tables["loss_by_league"]
    veto, margins, weights, overround = tables["veto_effect"], tables["live_margins"], tables["pool_weights"], tables["overround"]
    out: list[str] = []
    add = out.append

    A = "A_2023_2025_all_models"
    C = "C_2023_2025_pool_and_residual"
    best_open = _pooled_row(loss_pooled, "1x2", A, "pool_models", "open")
    best_close = _pooled_row(loss_pooled, "1x2", A, "pool_models", "close")
    ou_open = _pooled_row(loss_pooled, "over_under_2.5", A, "pool_models", "open")
    ou_close = _pooled_row(loss_pooled, "over_under_2.5", A, "pool_models", "close")
    pool_open = _pooled_row(loss_pooled, "1x2", C, "pool_with_market", "open")
    pool_close = _pooled_row(loss_pooled, "1x2", C, "pool_with_market", "close")
    res_open = _pooled_row(loss_pooled, "1x2", C, "residual", "open")
    res_close = _pooled_row(loss_pooled, "1x2", C, "residual", "close")
    poolou_open = _pooled_row(loss_pooled, "over_under_2.5", C, "pool_with_market", "open")
    poolou_close = _pooled_row(loss_pooled, "over_under_2.5", C, "pool_with_market", "close")
    sample_a = loss_pooled[(loss_pooled["sample"] == A) & (loss_pooled["method"] == "power")]
    max_p_a = float(sample_a["p_value"].max())
    lg = loss_league[(loss_league["league_code"] != mt.POOLED_LABEL) & (loss_league["sample"] == A) & (loss_league["method"] == "power")]
    n_leagues = int(lg["league_code"].nunique())
    better_anywhere = int(((lg["mean_diff"] > 0) & (lg["p_holm"] < 0.05)).sum())
    w_1x2 = weights[(weights["market"] == "1x2") & (weights["variant"] == "selected_best") & (weights["method"] == "power")].sort_values("season")
    w_ou = weights[(weights["market"] == "over_under_2.5") & (weights["variant"] == "selected_best") & (weights["method"] == "power")].sort_values("season")

    masks = mt.plan_family_masks(verdicts)
    plan_rows, plan = _family_rows(verdicts, "plan", HEADLINE)
    shop_rows, shop = _family_rows(verdicts, "shopping", HEADLINE)
    head = verdicts[masks["headline"]]
    n_all = len(verdicts)
    all_signal = int(verdicts["signal_all"].astype(bool).sum())
    signals = verdicts[verdicts["signal_all"].astype(bool)]
    signal_regimes = sorted(set(signals["regime"]))
    avg_positive = int(verdicts[verdicts["book"] == "avg"]["positive_lb"].astype(bool).sum())
    scales = _drift_scales()
    scale_lo = min(min(v) for v in scales.values())
    scale_hi = max(max(v) for v in scales.values())

    one = lambda **kw: _summary_row(summary, market_set="all", seasons=SEL_SEASONS, method="power", **kw)  # noqa: E731
    b_ref_100 = one(book="bet365", strategy="reference", z=0.0, regime="tau_1.00", n_legs=1)
    b_ref_088 = one(book="bet365", strategy="reference", z=0.0, regime="tau_0.88", n_legs=1)
    b_ref_088z = one(book="bet365", strategy="reference", z=mt.DEFAULT_Z, regime="tau_0.88", n_legs=1)
    m_ref_100 = one(book="max", strategy="reference", z=0.0, regime="tau_1.00", n_legs=1)
    m_ref_100_5 = one(book="max", strategy="reference", z=0.0, regime="tau_1.00", n_legs=5)
    m_ref_094_5 = one(book="max", strategy="reference", z=0.0, regime="tau_0.94", n_legs=5)
    m_ref_088 = one(book="max", strategy="reference", z=0.0, regime="tau_0.88", n_legs=1)
    ctrl_088 = one(book="bet365", strategy="model", z=0.0, regime="tau_0.88", n_legs=1)
    ctrl_100 = one(book="bet365", strategy="model", z=0.0, regime="tau_1.00", n_legs=1)
    pl4 = _verdict_frame(verdicts, book="pl_b365", z=0.0, overround=1.04)
    pl4 = pl4[(pl4["strategy"] == "reference") & (pl4["regime"] == "tau_1.00")].set_index("n_legs")
    pl4_neg = _verdict_frame(verdicts, book="pl_b365", z=0.0, overround=1.04)
    pl4_neg = pl4_neg[(pl4_neg["strategy"] == "reference") & (pl4_neg["regime"] != "tau_1.00")]
    edge_b365, clv_b365, n_b365 = _selection_edge("bet365")
    edge_max, clv_max, n_max = _selection_edge("max")
    veto_max = veto[(veto["book"] == "max") & (veto["z"] == 0.0)]
    veto_b365 = veto[(veto["book"] == "bet365") & (veto["z"] == 0.0)]
    ps_2025 = overround[(overround["market"] == "1x2") & (overround["season"] == 2025)]
    coverage_2025 = float(ps_2025[ps_2025["source"] == "ps_open"]["n"].iloc[0] / ps_2025[ps_2025["source"] == "avg"]["n"].iloc[0])

    # ------------------------------------------------------------------ top: title, summary table, answers
    add("# Model kontra rynek po podatku (pytanie 2 i przybliżenie pytania 3)")
    add("")
    add("Plan, dni 4–5. Sprawdzamy dwie rzeczy. Czy model wie więcej niż rynek (kursy Pinnacle)? Czy da się na tym zarobić po podatku? "
        "Dane: kursy football-data połączone z meczami API-Football (1X2 i over/under 2,5) oraz prognozy z protokołu porównania modeli. "
        "Sezony testowe: 2023–2025. Kursy „otwarcia” to zdjęcie z piątku lub wtorku przed meczem i służą do decyzji. Kursy zamknięcia służą tylko do CLV.")
    add("")
    add("## Tabela zbiorcza")
    add("")
    add("**Najpierw mediana CLV i odsetek pobitych zamknięć.** Średnie CLV jest zwodnicze: ma dolną granicę −100% i nie ma górnej, więc kilka dużych ruchów podciąga średnią, "
        "choć typowy zakład traci (HANDOFF, sekcje B i C). Dlatego pierwsza tabela pokazuje medianę CLV po podatku i odsetek zakładów, w których cena wzięta była lepsza od uczciwej ceny zamknięcia Pinnacle (przed podatkiem). "
        "W nawiasie „los”: ten sam odsetek dla losowego obstawiania z tą samą liczbą zakładów, w tych samych oknach zdjęcia kursów, na tym samym rynku i w tych samych pasmach kursów. "
        "Single, bez marginesu ostrożności (z = 0), potęgowe de-vig. Strategia musi pobić „los”, a nie 50%.")
    add("")
    out.extend(_median_share_table(summary))
    add("")
    add("Pytanie z kryterium planu: czy dolna granica 95% przedziału ufności średniego CLV po podatku jest dodatnia? "
        "Odpowiedź „TAK” wymaga dodatniej dolnej granicy przy każdej z trzech metod de-vig i istotności po korekcie Holma. "
        f"Każdy wiersz zbiera {plan['n'] // 12} wariantów: 3 ceny (Bet365 oraz dwie syntetyczne polskie ceny z marżą 6%), 2 progi ostrożności (z = 0 i z = 1,645) "
        "i 5 struktur (single, AKO 2–5). Ostatnia kolumna to ta sama odpowiedź dla ceny Max, czyli najwyższego kursu z kilkudziesięciu bukmacherów. Tej ceny nie da się postawić u jednego bukmachera.")
    add("")
    rows = []
    for a, b in zip(plan_rows, shop_rows):
        shop_answer = _answer(b)
        rows.append([
            STRATEGY_LABEL[a["strategy"]], REGIME_LABEL[a["regime"]], _answer(a), f"{a['readable']} z {a['n']}", _best_text(a),
            f"{shop_answer} ({b['positive']} z {b['readable']})" if b["readable"] else shop_answer,
        ])
    out.extend(_table(["Strategia", "Reżim", "Bet365 i ceny polskie: dolna granica > 0?", "Warianty z przedziałem ufności",
                       "Największa dolna granica (najgorsza z 3 metod de-vig)", "Cena Max: dolna granica > 0? (dodatnich z ocenialnych)"],
                      rows, ["l", "l", "l", "r", "l", "l"]))
    add("")
    add("„Za mało zakładów”: przy tym reżimie strategia nie znajduje 20 zakładów w trzech sezonach, więc przedziału nie da się policzyć. Taki wariant wchodzi do korekty z p = 1.")
    add("")
    add("## Wynik w skrócie")
    add("")
    add(f"1. **Model nie wie więcej niż rynek.** Log-loss w 1X2: model (pula modeli) {_num(best_open['model_loss'], 4)}, rynek {_num(best_open['market_loss'], 4)} na otwarciu i {_num(best_close['market_loss'], 4)} na zamknięciu "
        f"({_int(best_open['n'])} meczów, {n_leagues} lig). Różnica względem otwarcia to {_num(best_open['mean_diff'], 4, True)} {_ci(best_open['ci_lo'], best_open['ci_hi'])}; ujemna znaczy: model gorszy. "
        f"W żadnej lidze żaden model nie jest lepszy od rynku (Holm: {better_anywhere} z {n_leagues}). Over/under 2,5: {_num(ou_open['model_loss'], 4)} wobec {_num(ou_open['market_loss'], 4)} i {_num(ou_close['market_loss'], 4)}.")
    add(f"2. **Pula z rynkiem i model residualny nic nie dodają.** Waga modelu w puli to {_pct(w_1x2['w_model'].iloc[0], 1, False)}, {_pct(w_1x2['w_model'].iloc[1], 1, False)} i {_pct(w_1x2['w_model'].iloc[2], 1, False)} "
        f"(sezony 2023, 2024, 2025), w over/under 2,5 prawie zero. Log-loss puli różni się od otwarcia o {_num(pool_open['mean_diff'], 4, True)} (p = {_pval(pool_open['p_value'])}), "
        f"model residualny o {_num(res_open['mean_diff'], 4, True)} {_ci(res_open['ci_lo'], res_open['ci_hi'])}. Względem zamknięcia oba są gorsze o {_num(-pool_close['mean_diff'], 4)}.")
    add(f"3. **Na cenach, które przybliżają polskiego bukmachera, żadna strategia nie ma dodatniej dolnej granicy CLV po podatku.** "
        f"Z {plan['n']} wariantów przedział da się policzyć w {plan['readable']}, dodatni jest w {plan['positive']}. Najlepszy przypadek to Bet365 bez podatku (τ = 1,00, single, {_int(b_ref_100['n_bets'])} zakładów): "
        f"mediana CLV {_pct(b_ref_100['clv_net_median'], 2)}, pobitych zamknięć {_pct(b_ref_100['share_beat_raw'], 1, False)} (losowo {_pct(b_ref_100['random_share_raw_mean'], 1, False)}), "
        f"ale średnie CLV po podatku {_pci(b_ref_100['clv_net_mean'], b_ref_100['clv_net_lo'], b_ref_100['clv_net_hi'], 2)}, czyli zero: przewaga widoczna na otwarciu znika do zamknięcia. "
        f"Przy τ = 0,88 skaner prawie nie znajduje zakładów: single z Bet365 bez marginesu ostrożności to {_int(b_ref_088['n_bets'])} zakładów w trzech sezonach, z marginesem {_int(b_ref_088z['n_bets'])}.")
    add(f"4. **Dodatnia dolna granica pojawia się dopiero przy cenie Max.** Przy τ = 1,00, single: mediana CLV {_pct(m_ref_100['clv_net_median'], 2)}, pobitych zamknięć {_pct(m_ref_100['share_beat_raw'], 1, False)} "
        f"(losowo {_pct(m_ref_100['random_share_raw_mean'], 1, False)}), średnie CLV {_pci(m_ref_100['clv_net_mean'], m_ref_100['clv_net_lo'], m_ref_100['clv_net_hi'], 2)} (n = {_int(m_ref_100['n_bets'])}). "
        f"AKO z 5 nóg: mediana {_pct(m_ref_100_5['clv_net_median'], 1)}, średnie {_pct(m_ref_100_5['clv_net_mean'], 1)} (dolna granica {_pct(m_ref_100_5['clv_net_lo'], 1)}). "
        f"Przy τ = 0,94 tylko AKO z 4–5 nóg (5 nóg: mediana {_pct(m_ref_094_5['clv_net_median'], 1)}, średnie {_pct(m_ref_094_5['clv_net_mean'], 1)}, dolna granica {_pct(m_ref_094_5['clv_net_lo'], 1)}). "
        f"Przy τ = 0,88 żaden wariant nie przechodzi korekty (single: {_int(m_ref_088['n_bets'])} zakładów, mediana CLV {_pct(m_ref_088['clv_net_median'], 1)}, średnie {_pct(m_ref_088['clv_net_mean'], 1)}, dolna granica ujemna). "
        f"Po korekcie Holma na {n_all} sprawdzonych wariantów istotnych jest {all_signal}; wszystkie przy {' i '.join(REGIME_LABEL[r] for r in signal_regimes)}.")
    add(f"5. **Wynik zależy od marży polskiego bukmachera, której nie znamy.** Przy założonych 6% polskie ceny nie dają nic. Przy 4% (to marża samego Pinnacle) i szumie cen jak u Bet365, "
        f"bez podatku (τ = 1,00), single mają medianę CLV {_pct(pl4.loc[1, 'median_min'], 2)}, pobitych zamknięć {_pct(pl4.loc[1, 'share_beat_raw_min'], 1, False)} i średnie CLV {_pct(pl4.loc[1, 'mean_min'], 1)} "
        f"(dolna granica {_pct(pl4.loc[1, 'lo_min'], 1)}, n = {_int(pl4.loc[1, 'n_bets_min'])}; najgorsza z trzech metod de-vig). "
        f"Przy τ = 0,94 i 0,88 taki scenariusz też nie ma dodatniej dolnej granicy dla singli.")
    add(f"6. **Model nie poprawia strategii cenowej.** Czysta referencja, referencja z wetem i pula dają podobne CLV. Nogi zgodne z modelem mają wyższe CLV o "
        f"{_num(100 * veto_max['diff_strat'].min(), 1)}–{_num(100 * veto_max['diff_strat'].max(), 1)} pp przy cenie Max, a przy Bet365 różnica jest nieistotna w dwóch z trzech metod de-vig (sekcja 3.6). "
        f"Sam model przeciw cenom Bet365 traci: CLV po podatku {_pct(ctrl_088['clv_net_mean'], 1)} przy τ = 0,88 (n = {_int(ctrl_088['n_bets'])}) i {_pct(ctrl_100['clv_net_mean'], 1)} przy τ = 1,00.")
    add("")
    add("## Gałąź kryterium planu")
    add("")
    add("Plan: jeśli jakakolwiek strategia (single albo AKO, w jakimkolwiek reżimie podatku) ma dodatnią dolną granicę CLV po podatku poza próbą, kupujemy feed polskich kursów. "
        "Jeśli żadna, ale modele rożnych i kartek są dobrze skalibrowane, czekamy na kursy z kolektora i powtarzamy test dla tych rynków. "
        "Jeśli nic nie działa, skaner zostaje narzędziem pomiarowym.")
    add("")
    pos088 = verdicts[(verdicts["regime"] == "tau_0.88") & verdicts["positive_lb"].astype(bool)]
    if pos088.empty:
        pos088_text = "żaden wariant nie ma dodatniej dolnej granicy, żadna cena, żadna marża."
    else:
        first = pos088.sort_values("lo_min", ascending=False).iloc[0]
        pos088_text = (
            f"żaden wariant nie przechodzi korekty na wielokrotne testowanie. Bez korekty dodatnią dolną granicę ma {len(pos088)} z {int((verdicts['regime'] == 'tau_0.88').sum())} wariantów "
            f"(ceny: {', '.join(BOOK_SHORT[b] for b in sorted(set(pos088['book'])))}; np. {STRATEGY_LABEL[first['strategy']]}, {STRUCTURE_LABEL[int(first['n_legs'])]}, {_int(first['n_bets_min'])} zakładów: "
            f"średnie CLV {_pct(first['mean_min'], 1)}, dolna granica {_pct(first['lo_min'], 1)}, p po korekcie = {_num(first['p_holm_all'], 2)})."
        )
    add(f"- **Bet365 i ceny z marżą 6%:** 0 z {plan['n']} wariantów ma dodatnią dolną granicę. Kryterium nie jest spełnione.")
    add(f"- **Cena Max oraz marża 4% przy τ = 1,00:** dodatnia dolna granica, istotna po korekcie na {n_all} wariantów. Wymaga ceny, której nie ma u jednego bukmachera, albo założenia o marży.")
    add(f"- **τ = 0,88 (standardowy podatek):** {pos088_text}")
    add("")
    add("**Werdykt.** (1) Na cenach, które da się postawić u jednego bukmachera (Bet365, ceny polskie przy marży 6% i 8%), kryterium pierwszej gałęzi nie jest spełnione w żadnym reżimie podatku. "
        "Dla standardowego podatku (τ = 0,88) obowiązuje więc gałąź trzecia: skaner zostaje narzędziem pomiarowym, a zakłady ograniczają się do reżimów bez podatku, jeśli na prawdziwych polskich cenach dadzą dodatni CLV. "
        "(2) Pierwsza gałąź (zakup feedu polskich kursów) jest spełniona tylko warunkowo: na cenie Max, której nie da się postawić u jednego bukmachera, albo przy założeniu, że polski bukmacher ma marżę 4% i szum cen jak Bet365. "
        "Dotyczy to wyłącznie τ = 1,00 (single i AKO) oraz τ = 0,94 (AKO z 4–5 nóg, cena Max). To powód, żeby zmierzyć prawdziwe polskie ceny, a nie dowód, że przewaga istnieje. "
        "Cały sygnał to porównanie ceny z odmarżowanym Pinnacle; model do tego nie jest potrzebny. Decyzja o zakupie należy do Ciebie. "
        "(3) Druga gałąź (rożne i kartki): modele liczbowe biją częstość ligową w 34 z 34 linii i odróżniają mecze (DSC większe niż MCB w 1206 z 1296 par liga-linia), "
        "ale są trochę gorzej skalibrowane niż stała częstość, a miskalibracja jest wykrywalna w 13 z 34 linii (raport pytania 1, sekcja Kalibracja). "
        "Kursów historycznych dla tych rynków nie ma, więc gałąź druga czeka na dane z kolektora niezależnie od wyniku tego testu.")
    add("")
    # ------------------------------------------------------------------ data and method
    add("## Dane i założenia")
    add("")
    add("- **CLV po podatku** = τ · kurs wzięty / uczciwy kurs zamknięcia Pinnacle − 1. Przy nieobciążonym zamknięciu to oczekiwany zwrot na złotówkę stawki po podatku. "
        "Liczymy go na cenie faktycznie wziętej (nie na ruchu rynku) i podajemy średnią, medianę oraz odsetek pobitych zamknięć.")
    add(f"- **Mecze.** Mecze z kursami Pinnacle na otwarciu i zamknięciu: 1X2 {_int(best_open['n'])} w sezonach 2023–2025 ({n_leagues} lig), over/under 2,5 {_int(ou_open['n'])}. "
        f"Ligi bez kursów Pinnacle w football-data (Ekstraklasa, ligi skandynawskie) nie wchodzą. W sezonie 2025/26 Pinnacle jest w football-data tylko do stycznia 2026 (ok. {_pct(coverage_2025, 0, False)} meczów).")
    add("- **Kursy „otwarcia”.** Zdjęcie robione przy budowie tygodniowego pliku: piątek dla meczów od piątku do poniedziałku, wtorek dla wtorku–czwartku "
        "(średni ruch Pinnacle do zamknięcia jest najmniejszy dla meczów w piątek i we wtorek). Nogi jednego AKO pochodzą z jednego takiego okna, z różnych meczów, u jednego bukmachera. "
        "Marża w kolumnach Pinnacle zmieniła się (3,2% w 2022, 4,8% w 2024), więc CLV z różnych sezonów nie jest porównywalny; w obrębie sezonu tak.")
    add("- **De-vig.** Trzy metody: proporcjonalna, potęgowa, Shin. Wariant jest „dodatni”, tylko gdy dolna granica jest dodatnia przy każdej z trzech.")
    add(f"- **Decyzja.** Cena bukmachera (Bet365; Max i Avg jako czułość) musi przebić dolną granicę uczciwej ceny: `p − z·SE(p)`, gdzie `SE = c·√(p(1−p))`. "
        f"`c` to zmierzony na trzech poprzednich sezonach ruch Pinnacle od zdjęcia do zamknięcia ({_num(scale_lo, 3)}–{_num(scale_hi, 3)}); z = 1,645 albo z = 0 (bez marginesu). "
        "Potem bramka EV z niepewnością (`assess_bet`), jeden typ na mecz (`select_opportunities`) i, dla AKO, dodatnie EV kuponu z dolnych granic (`accumulator_ev_net`, podatek raz na kupon).")
    add("- **Zakłady.** Płaska stawka 100 zł (obcięta poniżej progu PIT 2 280 zł wypłaty) albo ¼ Kelly'ego z podatkiem na dolnej granicy prawdopodobieństwa, "
        "z bankrollu 10 000 zł, najwyżej 2% na zakład.")
    add("- **Statystyka.** Przedziały: bootstrap po kolejkach (single) albo po oknach zdjęcia kursów (AKO), 4 000 losowań. "
        "P-wartość wariantu to największa z trzech metod de-vig (test przecięcia). Korekta Holma i Benjaminiego–Hochberga na rodzinie wariantów. "
        "Wariant z mniej niż 20 zakładami albo 10 klastrami wchodzi do rodziny z p = 1.")
    add(f"- **Liczba wariantów.** Sprawdzono {n_all} wariantów (strategia × cena × próg × rynki × sezony × reżim × struktura); każdy pod trzema metodami de-vig. "
        f"Rodzina „plan” (Bet365 i polskie ceny) ma {plan['n']} wariantów, „rynek” (Max i Avg) {shop['n']}, „nagłówkowa” (Bet365, z = 1,645) {len(head)}. "
        "Tabele podają pełną listę; wnioski o „TAK” wymagają istotności po korekcie na rodzinie.")
    add("- **Nie zrobiono:** handicapu azjatyckiego. Linia handicapu zmienia się między otwarciem a zamknięciem, a ćwierćlinie zwracają połowę stawki; "
        "wymaga to osobnego rozliczania i dopasowania linii do CLV, więc nie jest to „proste”. Zostaje na później.")
    add("")
    # ------------------------------------------------------------------ 1. loss
    add("## 1. Czy model wie więcej niż rynek? (log-loss)")
    add("")
    spread = max(_method_spread(tables, "over_under_2.5", A, ["pi_gbm", "pool_models"]), _method_spread(tables, "1x2", A, ["pi_gbm", "pool_models"]))
    add("Ta sama próba dla wszystkich: mecze z kursami Pinnacle na otwarciu i zamknięciu oraz prognozami wszystkich modeli. Różnica = strata rynku minus strata modelu; "
        "dodatnia znaczy: model lepszy. Test Diebolda–Mariano z wariancją HAC po kolejkach, Holm na ligi. Tabela dla potęgowej metody de-vig; "
        f"proporcjonalna i Shin zmieniają różnicę o najwyżej {_num(spread, 4)}.")
    add("")
    add("### 1X2")
    add("")
    order = ["baseline_league_freq", "dixon_coles", "nb2", "gas_poisson", "elo_davidson", "pi_ratings", "pi_gbm", "selected_best", "pool_models"]
    out.extend(_loss_table(tables, "1x2", A, order))
    add("")
    add("### Over/under 2,5")
    add("")
    out.extend(_loss_table(tables, "over_under_2.5", A, [m for m in order if m != "elo_davidson"]))
    add("")
    add(f"Wszystkie różnice łączne są ujemne, największe p to {_pval(max_p_a)}. Wybór ex ante i pula modeli używają tylko wyników poprzedniego sezonu. "
        f"Lepszy model zmniejsza dystans do rynku, ale go nie domyka. Zamknięcie jest lepsze od otwarcia o {_num(best_open['market_loss'] - best_close['market_loss'], 4)} w 1X2 "
        f"i {_num(ou_open['market_loss'] - ou_close['market_loss'], 4)} w over/under, więc z zamknięciem model przegrywa mocniej. "
        "Wyniki dla modeli bazowych na sezonach 2022–2025 (próba B) są w `market_test.sqlite`, tabela `loss_by_league`.")
    add("")
    # ------------------------------------------------------------------ 2. pool
    add("## 2. Pula z rynkiem i model residualny")
    add("")
    add("Pula: log-liniowa mieszanka odmarżowanego otwarcia Pinnacle i modelu wybranego ex ante. Waga dopasowana na wcześniejszych sezonach (od 2022, bez sezonu prognozowanego). "
        "Model residualny (`market_residual.py`, tylko 1X2): ograniczone korekty logitów wokół otwarcia, sterowane formą drużyn z okien 5 i 10 meczów "
        "(gole, strzały, strzały celne, rożne, punkty), odpoczynkiem i kształtem otwarcia. Siła kary wybrana na sezonie poprzedzającym, dopasowanie na wcześniejszych sezonach (od 2015).")
    add("")
    rows = []
    for label, row_o, row_c in (("pula z rynkiem, 1X2", pool_open, pool_close), ("model residualny, 1X2", res_open, res_close),
                                ("pula z rynkiem, over/under 2,5", poolou_open, poolou_close)):
        rows.append([label, _num(row_o["model_loss"], 4), f"{_num(row_o['mean_diff'], 4, True)} {_ci(row_o['ci_lo'], row_o['ci_hi'])}", _pval(row_o["p_value"]),
                     f"{_num(row_c['mean_diff'], 4, True)} {_ci(row_c['ci_lo'], row_c['ci_hi'])}", _pval(row_c["p_value"])])
    out.extend(_table(["Prognoza", "Log-loss", "Względem otwarcia [95% CI]", "p", "Względem zamknięcia [95% CI]", "p"], rows, ["l", "r", "r", "r", "r", "r"]))
    add("")
    add(f"Waga modelu w puli (potęgowe de-vig): 1X2 {', '.join(_pct(v, 1, False) for v in w_1x2['w_model'])} w sezonach 2023, 2024, 2025 "
        f"(model: {', '.join(w_1x2['model'])}); over/under 2,5 {', '.join(_pct(v, 2, False) for v in w_ou['w_model'])} (model: {', '.join(w_ou['model'])}). "
        "Pula i model residualny są nierozróżnialne od otwarcia i gorsze od zamknięcia. To, co model wie, rynek na otwarciu już ma. To, czego rynek dowiaduje się do zamknięcia, jest poza modelem. "
        "Pula zbudowana z puli modeli (2024–2025) daje wagę modelu równą zero.")
    add("")
    # ------------------------------------------------------------------ 3. strategies
    add("## 3. Strategie na cenach otwarcia")
    add("")
    add("### 3.1 Jak często cena bukmachera przebija odmarżowane otwarcie Pinnacle")
    add("")
    add("Odsetek meczów (sezony 2023–2025), w których choć jedna noga ma cenę o co najmniej tyle powyżej uczciwej (potęgowe de-vig otwarcia Pinnacle). "
        "0% to próg przy τ = 1,00, 6,4% próg singla przy τ = 0,94, 13,6% przy τ = 0,88.")
    add("")
    rows = []
    for r in gaps.itertuples(index=False):
        rows.append([MARKET_LABEL_PL[r.market], BOOK_LABEL[r.book], _int(r.n), _pct(r.ge0, 1, False), _pct(r.ge64, 1, False), _pct(r.ge136, 2, False), _int(r.n136)])
    out.extend(_table(["Rynek", "Cena", "Mecze", "≥ 0%", "≥ 6,4%", "≥ 13,6%", "Mecze ≥ 13,6%"], rows, ["l", "l", "r", "r", "r", "r", "r"]))
    add("")
    b1x2 = gaps[(gaps["market"] == "1x2") & (gaps["book"] == "bet365")].iloc[0]
    m1x2 = gaps[(gaps["market"] == "1x2") & (gaps["book"] == "max")].iloc[0]
    add(f"Przy progu 13,6% (τ = 0,88) Bet365 przebija Pinnacle w {_int(b1x2['n136'])} meczach na {_int(b1x2['n'])} w 1X2, a najlepsza cena rynku w {_int(m1x2['n136'])}. "
        "To powód, dla którego przy standardowym podatku nie ma czego testować.")
    add("")
    add("### 3.2 Bet365, bez marginesu ostrożności (z = 0): dolna granica CLV po podatku")
    add("")
    add("Komórka: liczba zakładów lub kuponów; dolna granica 95% przedziału średniego CLV po podatku (najgorsza z trzech metod de-vig). "
        "Pogrubienie: dodatnia przy każdej z trzech metod. „za mało”: mniej niż 20 zakładów albo 10 klastrów, przedziału nie czytamy.")
    add("")
    out.extend(_structure_table(_verdict_frame(verdicts, book="bet365", z=0.0), HEADLINE))
    add("")
    add("### 3.3 Bet365, z marginesem ostrożności (z = 1,645, wariant nagłówkowy)")
    add("")
    out.extend(_structure_table(_verdict_frame(verdicts, book="bet365", z=mt.DEFAULT_Z), HEADLINE))
    add("")
    add(f"Margines `z·SE` to przy tych danych 4–5 punktów procentowych prawdopodobieństwa: `c` wynosi {_num(scale_lo, 3)}–{_num(scale_hi, 3)}, bo zdjęcie kursów jest 1–4 dni przed meczem. "
        f"Wariantów z przedziałem ufności jest tylko {int(head['interval_ok'].astype(bool).sum())} z {len(head)}. "
        "Plan zakładał 1–2 punkty dla skanu tuż przed meczem; takich danych nie mamy.")
    add("")
    add("### 3.4 Losowa linia bazowa (ta sama liczba zakładów, rynek i pasmo kursów)")
    add("")
    add("Każda noga zakładu jest zamieniana na losową nogę z tego samego okna zdjęcia kursów, z tego samego rynku (1X2 albo over/under 2,5) i z tego samego pasma kursów (do 1,5; 1,5–2; 2–3; 3–5; powyżej 5), "
        "którą strategia mogła wziąć. Liczba zakładów, ich czas, mieszanka rynków i mieszanka kursów są więc identyczne; kupony dostają różne mecze. 200 powtórzeń. "
        "W HANDOFF losowe obstawianie biło zamknięcie w 50,08% przypadków, ale tam CLV był ruchem uczciwej ceny Pinnacle. "
        f"Tutaj CLV liczymy na cenie miękkiego bukmachera, która ma marżę, więc losowa noga bije uczciwe zamknięcie rzadziej: dla singli ok. {_pct(b_ref_100['random_share_raw_mean'], 0, False)} u Bet365 i ok. {_pct(m_ref_100['random_share_raw_mean'], 0, False)} przy cenie Max. "
        "Strategia musi pobić tę linię, nie 50%. Metoda de-vig: potęgowa. p to odsetek powtórzeń losowych, które są co najmniej tak dobre jak strategia (najmniejsza możliwa wartość to 1/201).")
    add("")
    add("**Bet365, czysta referencja, z = 0**")
    add("")
    out.extend(_random_table(summary, book="bet365"))
    add("")
    add(f"Strategia bije los pod względem odsetka pobitych zamknięć i mediany, bo wybiera nogi z ceną powyżej uczciwej ceny otwarcia. Ale średnie CLV nie jest dodatnie. "
        f"Dla singli przy τ = 1,00 średnia przewaga na otwarciu (p · kurs − 1) to {_pct(edge_b365, 1)}, a średnie CLV (po zamknięciu) {_pct(clv_b365, 1)}: przewaga widoczna na otwarciu znika do zamknięcia. "
        "Mediana i odsetek pobitych zamknięć mówią, czy przewaga jest typowa. Średnie CLV mówi, ile zostaje po uwzględnieniu ogona rozkładu. "
        "Zysk wymaga obu: mediany i odsetka powyżej losowego oraz dodatniej dolnej granicy średniej (HANDOFF, sekcje B i C). Tu jest tylko pierwsze.")
    add("")
    add("### 3.5 Yield po podatku, ¼ Kelly i obsunięcie")
    add("")
    add("Bet365, czysta referencja, z = 0, potęgowe de-vig. Przedziały yieldu: bootstrap po kolejkach albo oknach. Przy tej liczbie zakładów yield jest zbyt niepewny, żeby cokolwiek rozstrzygać; "
        "CLV ma znacznie mniejszy rozrzut i dlatego jest miarą decyzyjną. Przedziały yieldu długich AKO są dodatkowo nierzetelne: kilka trafień z kursem rzędu 10–200 decyduje o wyniku, a bootstrap zaniża ogon. "
        "Dolna granica yieldu powyżej zera (np. τ = 1,00, AKO 3) przy średnim CLV bliskim zera to szczęście, nie przewaga.")
    add("")
    out.extend(_annex_table(summary, book="bet365", strategy="reference", z=0.0))
    add("")
    add("### 3.6 Czy model porządkuje nogi, które przechodzą sito cenowe? (efekt weta)")
    add("")
    add("Bierzemy wszystkie nogi, które przechodzą sito cenowe (dolna granica uczciwej ceny poniżej ceny bukmachera), i dzielimy je na dwie grupy. "
        "Pierwsza: model uważa wynik za co najmniej tak prawdopodobny jak referencja (weto zostawia). Druga: model uważa go za mniej prawdopodobny. "
        "Porównujemy surowe CLV (przy τ = 1,00 równe CLV po podatku). Wariant „warstwowy” robi to samo osobno dla każdej kombinacji rynku, wyniku i pasma kursów.")
    add("")
    rows = []
    for r in veto[(veto["z"] == 0.0)].sort_values(["book", "method"]).itertuples(index=False):
        rows.append([BOOK_LABEL[r.book], r.method, _int(r.n_keep), _int(r.n_veto), _pct(r.clv_keep, 2), _pct(r.clv_veto, 2),
                     f"{_num(100 * r.diff, 2, True)} [{_num(100 * r.lo, 2, True)}; {_num(100 * r.hi, 2, True)}] pp",
                     f"{_num(100 * r.diff_strat, 2, True)} [{_num(100 * r.lo_strat, 2, True)}; {_num(100 * r.hi_strat, 2, True)}] pp"])
    out.extend(_table(["Cena", "De-vig", "Nogi zostawione", "Nogi odrzucone", "CLV zostawionych", "CLV odrzuconych", "Różnica [95% CI]", "Różnica warstwowa [95% CI]"], rows,
                      ["l", "l", "r", "r", "r", "r", "r", "r"]))
    add("")
    add(f"Przy cenie Max nogi zgodne z modelem mają wyższe CLV o {_num(100 * veto_max['diff'].min(), 1)}–{_num(100 * veto_max['diff'].max(), 1)} pp, "
        f"a po kontroli rynku, wyniku i pasma kursów o {_num(100 * veto_max['diff_strat'].min(), 1)}–{_num(100 * veto_max['diff_strat'].max(), 1)} pp; dolna granica jest dodatnia przy każdej metodzie. "
        f"Przy Bet365 różnica jest istotna tylko dla de-vig proporcjonalnego. Przy Avg próby są małe. "
        "Wygląda to tak, jakby model niósł odrobinę informacji o tym, dokąd Pinnacle przesunie się od otwarcia do zamknięcia. "
        "Efekt to jednostki punktu procentowego, a podatek to 12 punktów: wniosku o zyskowności to nie zmienia.")
    add("")
    add("### 3.7 Cena Max i Avg: górna granica tego, co daje porównywanie cen")
    add("")
    add("Max to najwyższy kurs spośród kilkudziesięciu bukmacherów z football-data w chwili zdjęcia, Avg to średnia. Nie da się ich postawić u jednego bukmachera. "
        "Pokazują, ile daje samo porównywanie cen z odmarżowanym Pinnacle.")
    add("")
    add("**Max, z = 0**")
    add("")
    out.extend(_structure_table(_verdict_frame(verdicts, book="max", z=0.0), HEADLINE))
    add("")
    add("**Max, z = 1,645**")
    add("")
    out.extend(_structure_table(_verdict_frame(verdicts, book="max", z=mt.DEFAULT_Z), HEADLINE))
    add("")
    add("**Max, czysta referencja, z = 0: CLV, yield i obsunięcie (potęgowe de-vig)**")
    add("")
    out.extend(_annex_table(summary, book="max", strategy="reference", z=0.0))
    add("")
    add("**Losowa linia bazowa dla Max, czysta referencja, z = 0**")
    add("")
    out.extend(_random_table(summary, book="max"))
    add("")
    add(f"Dla singli przy τ = 1,00 średnia przewaga na otwarciu to {_pct(edge_max, 1)}, a średnie CLV {_pct(clv_max, 1)}: mniej niż połowa przewagi przetrwała do zamknięcia, ale reszta jest dodatnia i pokonuje los. "
        f"Cena Avg (średnia rynku) nie ma dodatniej dolnej granicy w żadnym z {int((verdicts['book'] == 'avg').sum())} wariantów ({avg_positive} dodatnich).")
    add("")
    add("**Stabilność w sezonach (τ = 1,00, single, potęgowe de-vig)**")
    add("")
    out.extend(_season_table())
    add("")
    add("Tabela sprawdza tylko, czy znak wyniku powtarza się w każdym sezonie. Nie czytać jej jako trendu skuteczności rynku: marża w kolumnach Pinnacle zmieniła się między sezonami (3,2% w 2022, 4,8% w 2024), "
        "więc CLV z różnych sezonów nie jest porównywalny (HANDOFF, sekcja C, punkt 3).")
    add("")
    add("### 3.8 Kontrola: sam model")
    add("")
    rows = []
    for regime in mt.TAX_REGIMES:
        for z in (mt.DEFAULT_Z, 0.0):
            r = _summary_row(summary, book="bet365", strategy="model", method="power", z=float(z), regime=regime, n_legs=1, market_set="all", seasons=SEL_SEASONS)
            rows.append([REGIME_LABEL[regime], _z_text(z), _int(r["n_bets"]), _pci(r["clv_net_mean"], r["clv_net_lo"], r["clv_net_hi"], 1), _pct(r["share_beat_raw"], 1, False),
                         _pci(r["yield_flat"], r["yield_flat_lo"], r["yield_flat_hi"], 1)])
    out.extend(_table(["Reżim", "z", "Single: zakładów", "CLV po podatku [95% CI]", "Pobite zamknięcia", "Yield płaski [95% CI]"], rows, ["l", "r", "r", "r", "r", "r"]))
    add("")
    add("Sam model, bez ceny referencyjnej, wybiera zakłady, na których rynek się z nim nie zgadza, i na tych zakładach przegrywa z zamknięciem. To ta sama diagnoza co w HANDOFF: model mało wie.")
    add("")
    add("### 3.9 Czułość: rynki osobno i sezon 2022")
    add("")
    split = summary[(summary["family"] == "split") & (summary["method"] == "power") & (summary["book"] == "bet365") & np.isclose(summary["z"], 0.0)
                    & (summary["regime"] == "tau_1.00") & (summary["n_legs"] == 1)]
    rows = [[MARKET_LABEL_PL[r.market_set], STRATEGY_LABEL[r.strategy], "sezony 2023–2025", _int(r.n_bets), _pci(r.clv_net_mean, r.clv_net_lo, r.clv_net_hi, 2)] for r in split.itertuples(index=False)]
    allseasons = summary[(summary["family"] == "seasons_all") & (summary["method"] == "power") & np.isclose(summary["z"], 0.0) & (summary["regime"] == "tau_1.00") & (summary["n_legs"] == 1)]
    rows += [[MARKET_LABEL_PL["all"], STRATEGY_LABEL[r.strategy], "sezony 2022–2025", _int(r.n_bets), _pci(r.clv_net_mean, r.clv_net_lo, r.clv_net_hi, 2)] for r in allseasons.itertuples(index=False)]
    out.extend(_table(["Rynek", "Strategia (Bet365, z = 0, τ = 1,00, single)", "Sezony", "Zakładów", "CLV po podatku [95% CI]"], rows, ["l", "l", "l", "r", "r"]))
    add("")
    add("Osobne rynki i dodatkowy sezon 2022 (strategie bez ex ante modelu) nie zmieniają obrazu. Model residualny przegrywa z zamknięciem o ok. 3 punkty CLV: jego wybór nie przebija ceny zamknięcia. "
        "Wszystkie warianty są w tabeli `strategy_summary`.")
    add("")
    # ------------------------------------------------------------------ 4. Polish approximation
    add("## 4. Przybliżenie polskiego bukmachera")
    add("")
    add("**Marża.** W archiwum kursów na żywo (`api-football-odds`) nie ma żadnego polskiego bukmachera (ani Superbetu, ani STS, Fortuny, Betclica). "
        "Poniżej marże innych bukmacherów z archiwum na meczach, które kwotuje też Pinnacle. Marże w archiwum są wyższe niż w football-data, bo obejmują wszystkie ligi i mecze do 6 dni naprzód; "
        "porównywać trzeba różnicę do Pinnacle.")
    add("")
    rows = []
    if len(margins):
        for r in margins[(margins["market"] == "1x2") & (margins["n"] >= 20)].itertuples(index=False):
            rows.append([r.bookmaker, _int(r.n), _pct(r.mean_overround, 1, False), _pct(r.median_overround, 1, False), _int(r.n_with_pinnacle),
                         _num(r.gap_to_pinnacle_pp, 1, True) + " pp" if np.isfinite(r.gap_to_pinnacle_pp) else "n/d"])
    out.extend(_table(["Bukmacher (1X2, wrzesień 2026)", "Mecze", "Marża średnia", "Marża mediana", "Mecze z Pinnacle", "Różnica do Pinnacle"], rows, ["l", "r", "r", "r", "r", "r"]))
    add("")
    keep = overround[(overround["season"].isin([2023, 2024, 2025])) & overround["source"].isin(["ps_open", "bet365", "avg", "max"])]
    rows = []
    for market in ("1x2", "over_under_2.5"):
        for source in ("ps_open", "bet365", "avg", "max"):
            part = keep[(keep["market"] == market) & (keep["source"] == source)]
            rows.append([MARKET_LABEL_PL[market], {"ps_open": "Pinnacle, otwarcie", "bet365": "Bet365, otwarcie", "avg": "średnia rynku, otwarcie", "max": "najlepsza cena, otwarcie"}[source],
                         *[_pct(float(part[part["season"] == s]["mean_overround"].iloc[0]), 1, False) if (part["season"] == s).any() else "n/d" for s in (2023, 2024, 2025)]])
    add("Marża (nadwyżka sumy odwrotności kursów nad 1) w football-data, średnia po sezonach. Bet365 leży 1,6–2,7 punktu powyżej Pinnacle w 1X2:")
    add("")
    out.extend(_table(["Rynek", "Źródło", "2023", "2024", "2025"], rows, ["l", "l", "r", "r", "r"]))
    add("")
    add("**Założenie:** marża polskiego bukmachera 6% (`DEFAULT_POLISH_OVERROUND = 1,06`). To poziom Bet365 (5,7–7,3% w 1X2) i mediany dziesięciu bukmacherów z football-data (6,2%, Audyt 33), "
        "czyli ok. 2 punkty powyżej Pinnacle, jak zmierzone różnice miękkich bukmacherów. Jedyny zmierzony mecz STS (1X2 na Ekstraklasie) miał 3,5% (Audyt 27, po korekcie sumy). "
        "Dlatego sprawdzamy też 4% (tyle ma sam Pinnacle: scenariusz optymistyczny) i 8%. **To założenie, nie pomiar.**")
    add("")
    add("**Dwie syntetyczne polskie ceny.** „Polska cena z Pinnacle”: cena Pinnacle z otwarcia z marżą podmienioną na polską. Uczciwy rozkład jest Pinnacle, więc wartość może dać tylko prawdziwa informacja modelu. "
        "„Polska cena z Bet365”: cena Bet365 z marżą sprowadzoną do polskiej; zachowuje odchylenia miękkiego bukmachera od Pinnacle. Pierwsza prawie nigdy nie przebija uczciwej ceny. Druga zależy od marży:")
    add("")
    out.extend(_margin_sensitivity_table(verdicts))
    add("")
    add("Komórka: liczba zakładów; średnie CLV po podatku (najgorsza z trzech metod de-vig); w nawiasie dolna granica. Pogrubienie: dolna granica dodatnia przy wszystkich trzech metodach. "
        "Przy „polskiej cenie z Pinnacle” referencja nie znajduje zakładów, a pula prawie ich nie znajduje: model nie ma dość informacji, żeby przebić marżę. "
        "Dodatni wynik ma tylko cena z szumem Bet365, marża 4% i τ = 1,00. Przy marży 6% lub 8% wyniku dodatniego nie ma nawet bez podatku, a przy τ = 0,94 i 0,88 nie ma go dla singli w żadnym scenariuszu.")
    add("")
    # ------------------------------------------------------------------ 5. limits
    add("## 5. Ograniczenia")
    add("")
    add("- Kursy „otwarcia” to jedno zdjęcie tygodniowe, nie cena w chwili, gdy skaner by działał. CLV liczymy do zamknięcia Pinnacle. Nie wiemy, czy cena była jeszcze dostępna po kilku minutach.")
    add(f"- Sezon 2025/26 ma kursy Pinnacle tylko do stycznia 2026 (ok. {_pct(coverage_2025, 0, False)} meczów), więc rok 2025 wnosi mniej niż 2023 i 2024.")
    add("- Polskie ceny to przybliżenia z założoną marżą. Nie znamy ich rozrzutu względem Pinnacle, opóźnienia ani limitów stawek.")
    add("- Max i Avg to ceny, których nie da się postawić u jednego bukmachera. Dodatnie CLV przy Max mierzy rozrzut cen, nie zysk do zrealizowania.")
    add("- Model wybrany ex ante (`selected_best`) to `pi_gbm` w 1X2 i w over/under 2,5 (w 2025 `gas_poisson`). Wybór zależy od poprzedniego sezonu, ale sam zestaw modeli był dobierany na tych samych sezonach testowych (raport pytania 1).")
    add("- Podatek to mnożnik wypłaty τ. PIT od wygranej powyżej 2 280 zł obcina stawkę płaską i Kelly'ego, więc długie AKO mają stawkę mniejszą niż 100 zł.")
    add("- Handicap azjatycki nie był testowany. Rynki rożnych, kartek i strzałów nie mają kursów historycznych.")
    add("- Wielokrotne testowanie: trzy metody de-vig i wiele wariantów. Korekta Holma działa na rodzinie i na wszystkich wariantach; wariant z małą próbą wchodzi z p = 1, więc korekta nie jest łagodna. "
        "P-wartości wariantów z dużą próbą pochodzą z przybliżenia normalnego z błędem standardowym z bootstrapu (bootstrap sam nie zejdzie poniżej 1/4 001).")
    add("")
    add("## 6. Pliki")
    add("")
    add("- Kod: `app/src/model_buk/evaluation/market_test.py`, `app/scripts/run_market_test.py`, testy `app/tests/test_market_test.py`.")
    add("- Wyniki: `Model-Buk-Data/market_test/market_test.sqlite` (tabele: `loss_by_league`, `loss_pooled`, `loss_by_season`, `pool_weights`, `residual_selection`, `strategy_summary`, `strategy_verdicts`, `strategy_bets`, `veto_effect`, `live_margins`, `overround`).")
    add("- Odtworzenie: `.venv\\Scripts\\python scripts\\run_market_test.py --stage all --workers 3` (kilkanaście minut, do 3 procesów). Etap `panel` czyta bazę `fd_af_matches.sqlite` w całości; po dołączeniu do niej nowych lig i pucharów wyniki policzone od nowa obejmą także te mecze.")
    add("- Kontrola niezależna (recenzja): Bet365, z = 0, single przy τ = 1,00 oraz AKO 2, 3 i 5 przy τ = 1,00 i 0,88, a także Max, z = 0, single przy τ = 1,00 zostały przeliczone od zera z surowej bazy `fd_af_matches.sqlite` innym kodem. "
        "Te same zakłady, te same CLV. Log-loss w 1X2 (otwarcie, zamknięcie, pula modeli, 15 875 meczów) też się zgadza. "
        "Test przecieku na prawdziwych danych: po losowym przemieszaniu kursów zamknięcia sezonu podejmującego decyzję żadna z 30 kombinacji strategii, ceny i progu nie zmienia typów.")
    add("")
    return "\n".join(out)


def write_report(path: Path) -> None:
    tables = _load_all()
    gaps = _gap_frequency()
    text = build_report(tables, gaps)
    path.write_text(text, encoding="utf-8")
    say(f"report written: {path} ({len(text.splitlines())} lines)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", default="all", choices=["panel", "forecasts", "loss", "strategies", "margins", "report", "all"])
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--resamples", type=int, default=4000, help="bootstrap resamples per statistic")
    parser.add_argument("--loss-resamples", type=int, default=1000, help="bootstrap resamples of the log-loss comparisons")
    parser.add_argument("--replicates", type=int, default=200, help="random-betting replicates per setup")
    parser.add_argument("--force", action="store_true", help="rebuild cached panels and forecasts")
    args = parser.parse_args()
    stage = args.stage
    if stage in ("panel", "all"):
        stage_panel(args.force)
    if stage in ("forecasts", "all"):
        stage_forecasts(args.force)
    if stage in ("loss", "all"):
        stage_loss(args.loss_resamples)
    if stage in ("strategies", "all"):
        stage_strategies(args.workers, args.resamples, args.replicates)
    if stage in ("margins", "all"):
        stage_margins()
    if stage in ("report", "all"):
        write_report(REPORT_PATH)


if __name__ == "__main__":
    main()
