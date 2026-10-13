"""Export the paper registers and the data snapshot into the JSON files behind the MK2 dashboard.

Reads (never writes) every scanner register we have and the snapshot's databases, and writes one JSON file per
dataset of the dashboard (an array of row objects each) plus ``manifest.json``:

    eksperymenty.json   one row per (source, config): what was run and how it went
    podsumowanie.json   one row per source plus a total row: the headline figures
    przebiegi.json      one row per scan
    kupony.json         every listed (proposed) coupon, first appearance, with its outcome and paper profit
    kupony_nogi.json    the legs of those coupons
    single.json         every paper single: first value observation of each (bookmaker, leg) per config
    wyniki.json         the register's own paper results per (config, tier, tax regime, single/coupon)
    dane.json           what the data snapshot and our own folder hold
    plan.json           project status (config/mk2/status.json)

Sources: Oskar's live and offline registers in the snapshot (``<data>\\scanner``) and every experiment folder of
ours (``<out>\\scanner\\<name>``, folders starting with ``_`` skipped).  Each register is copied to a temporary folder
before it is opened, because ``ScannerRegister`` may migrate a schema on open and the snapshot stays read only.
Settlement and profit come from the register's own helpers (``paper_legs``, ``recent_bets``, ``paper_results``),
so the numbers match the scanner's page.

Usage (from app/):

    .venv\\Scripts\\python scripts\\mk2\\export_dashboard.py            # -> <out>\\dashboard\\
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent
sys.path.insert(0, str(APP / "src"))

from model_buk.scanner import live_models as lm  # noqa: E402
from model_buk.scanner import settlement as st  # noqa: E402
from model_buk.scanner.config import ScanConfig  # noqa: E402
from model_buk.scanner.register import ScannerRegister  # noqa: E402
from model_buk.scanner.report_html import leg_label_pl, outcome_pl  # noqa: E402

DEFAULT_DATA_ROOT = Path(os.environ.get("MODEL_BUK_DATA") or Path.home() / "Documents" / "Model-Buk-Data")
DEFAULT_OUT_ROOT = Path(os.environ.get("MODEL_BUK_MK2_OUT") or Path.home() / "Documents" / "Model-Buk-Data-MK2")
FAR_FUTURE = pd.Timestamp("2100-01-01", tz="UTC")
TAUS = (0.88, 0.94, 1.00)
LABELS = {
    "oskar-live": "Oskar · rejestr na żywo",
    "oskar-offline": "Oskar · badania offline",
    "mk2-replay-oskar-config": "MK2 · powtórka, ustawienia domyślne",
    "mk2-replay-mk2-band": "MK2 · powtórka, 2 nogi do kursu 2,00",
    "mk2-live-smoke": "MK2 · test na żywo (klucz Free)",
    "mk2-live": "MK2 · na żywo",
}
RUN_KIND = {"oskar-live": "na żywo (Oskar)", "oskar-offline": "offline (Oskar)", "mk2-live-smoke": "test na żywo (MK2)",
            "mk2-live": "na żywo (MK2)"}
MODEL_PL = {"ok": "użyty", "fit_stale": "pominięty: fit starszy niż 24 h", "no_fit": "pominięty: brak fitu"}
WARSAW = "Europe/Warsaw"
REGIME_PL = {"t088": "podatek 12%", "t094": "podatek 6%", "t100": "bez podatku"}
SOURCE_PL = {
    "pinnacle": "Pinnacle",
    "pinnacle_derived": "Pinnacle (pochodna)",
    "consensus": "konsensus",
    "betfair": "Betfair",
    "model": "model",
}


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    owner: str
    path: Path


# --------------------------------------------------------------------------- helpers


def iso(value: Any) -> str | None:
    """UTC ISO text to the minute (``2026-09-30T18:00Z``), or None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.strftime("%Y-%m-%dT%H:%MZ")


def pl_time(value: Any) -> str | None:
    """Polish local time for people, e.g. ``30.09.2026, 03:05``."""
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
    return stamp.tz_convert(WARSAW).strftime("%d.%m.%Y, %H:%M")


def pl_int(value: Any) -> str:
    """An integer with non-breaking thousands spaces (``1 811 456``), so it never wraps inside a cell."""
    return f"{int(value):,}".replace(",", "\u00a0")


def num(value: Any, digits: int = 6) -> float | None:
    """A finite float rounded to ``digits``, else None (JSON has no NaN)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def integer(value: Any) -> int | None:
    f = num(value)
    return None if f is None else int(round(f))


def text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value)
    return s if s and s.lower() != "nan" else None


def open_state(kickoff: str | None, now: pd.Timestamp) -> tuple[str, str]:
    """Label of an unsettled bet: its match has started but the data hold no result yet, or it is still ahead."""
    if kickoff and pd.Timestamp(kickoff) <= now:
        return "brak wyniku", "no_result"
    return "przed meczem", "open"


def money_sum(rows: list[dict[str, Any]], field: str) -> float | None:
    """Sum of a profit field over settled rows; None when nothing is settled (never a fake 0.0)."""
    values = [r[field] for r in rows if r.get(field) is not None]
    return num(sum(values), 4) if values else None


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)


def discover(data_root: Path, out_root: Path) -> list[Source]:
    found: list[Source] = []
    for sid, rel in (("oskar-live", "scanner/scanner.sqlite"), ("oskar-offline", "scanner/offline/scanner.sqlite")):
        path = data_root / rel
        if path.exists():
            found.append(Source(sid, LABELS[sid], "Oskar", path))
    scanner = out_root / "scanner"
    if scanner.is_dir():
        for folder in sorted(p for p in scanner.iterdir() if p.is_dir() and not p.name.startswith("_")):
            path = folder / "scanner.sqlite"
            if path.exists():
                sid = f"mk2-{folder.name}"
                found.append(Source(sid, LABELS.get(sid, f"MK2 · {folder.name}"), "MK2", path))
    return found


def open_copy(source: Source, workdir: Path) -> ScannerRegister:
    """Copy a register (with its -wal, if any) and open the copy, so the original is never written."""
    target = workdir / source.id / "scanner.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.path, target)
    for suffix in ("-wal",):
        extra = source.path.with_name(source.path.name + suffix)
        if extra.exists() and extra.stat().st_size > 0:
            shutil.copy2(extra, target.with_name(target.name + suffix))
    return ScannerRegister(target)


# --------------------------------------------------------------------------- names


class Names:
    """Team and league names per fixture id, from the fixture directories and the match history."""

    def __init__(self, directories: list[Path], history: Path | None):
        self.directories = [d for d in directories if d.exists()]
        self.history = history if history and history.exists() else None
        self.table: dict[int, dict[str, str | None]] = {}

    def load(self, fixture_ids: set[int]) -> None:
        missing = sorted(i for i in fixture_ids if i not in self.table)
        for db in self.directories:
            self._fill(db, missing, "SELECT fixture_id, home_team, away_team, league_name, country FROM fixtures WHERE fixture_id IN ({})")
            missing = [i for i in missing if i not in self.table]
        if self.history and missing:
            self._fill(self.history, missing, "SELECT fixture_id, home_team, away_team, league_code, NULL FROM matches WHERE fixture_id IN ({})")

    def _fill(self, db: Path, ids: list[int], sql: str) -> None:
        if not ids:
            return
        con = ro(db)
        try:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                for fid, home, away, league, country in con.execute(sql.format(",".join("?" * len(chunk))), chunk):
                    if int(fid) not in self.table:
                        league_text = " · ".join(v for v in (country, league) if isinstance(v, str) and v) or None
                        self.table[int(fid)] = {"home": home, "away": away, "league": league_text}
        finally:
            con.close()

    def get(self, fixture_id: Any) -> dict[str, str | None]:
        try:
            return self.table.get(int(fixture_id), {"home": None, "away": None, "league": None})
        except (TypeError, ValueError):
            return {"home": None, "away": None, "league": None}


def match_text(n: dict[str, str | None], fixture_id: Any) -> str:
    if n.get("home") and n.get("away"):
        return f"{n['home']} – {n['away']}"
    return f"mecz #{fixture_id}"


def leg_text(row: Any, n: dict[str, str | None]) -> str:
    try:
        return leg_label_pl(
            str(row["market"]), str(row.get("period") or "ft"), row.get("line"), str(row["selection"]),
            n.get("home") or "gospodarze", n.get("away") or "goście",
        )
    except Exception:  # noqa: BLE001 - a label must never stop the export
        return f"{row.get('market')} {row.get('period') or ''} {row.get('line') or ''} {row.get('selection')}".strip()


# --------------------------------------------------------------------------- configs


def config_note(config_json: str | None) -> tuple[str, int]:
    """Short text of what a stored config changes against today's ScanConfig defaults, and how many fields it lacks."""
    try:
        stored = json.loads(config_json or "{}")
    except json.JSONDecodeError:
        return ("nieczytelna konfiguracja", 0)
    current = {f.name: getattr(ScanConfig(), f.name) for f in fields(ScanConfig)}
    for name, value in list(current.items()):
        if isinstance(value, tuple):
            current[name] = list(value)
        elif hasattr(value, "__dataclass_fields__"):
            current[name] = None  # nested settings (fair_se_model) are not compared
    diffs = []
    for name, value in stored.items():
        if name not in current or current[name] is None:
            continue
        cur = current[name]
        if isinstance(value, list) and isinstance(cur, list):
            same = [float(a) if isinstance(a, (int, float)) else a for a in value] == [float(b) if isinstance(b, (int, float)) else b for b in cur]
        elif isinstance(value, (int, float)) and isinstance(cur, (int, float)) and not isinstance(value, bool):
            same = math.isclose(float(value), float(cur), rel_tol=1e-9, abs_tol=1e-12)
        else:
            same = value == cur
        if not same:
            diffs.append(f"{name}={value}")
    missing = [name for name in current if name not in stored]
    parts = []
    if diffs:
        parts.append("inne niż dziś: " + ", ".join(diffs[:6]) + (" …" if len(diffs) > 6 else ""))
    if missing:
        parts.append(f"starsza wersja skanera (brak {len(missing)} ustawień)")
    return ("; ".join(parts) if parts else "ustawienia jak dziś domyślne", len(missing))


# --------------------------------------------------------------------------- per source


def export_source(source: Source, reg: ScannerRegister, names: Names, now: pd.Timestamp) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ("przebiegi", "kupony", "kupony_nogi", "single", "wyniki", "eksperymenty")}
    scans = reg.read("SELECT * FROM scans ORDER BY as_of, started_at")
    if scans.empty:
        return out
    settled = reg.latest_settlements()[["leg_key", "outcome"]].drop_duplicates("leg_key")
    clv = reg.latest_clv()

    # fixture names for everything this source mentions
    ids: set[int] = set()
    for sql in ("SELECT DISTINCT fixture_id FROM legs WHERE is_value = 1", "SELECT DISTINCT fixture_id FROM coupon_legs"):
        frame = reg.read(sql)
        ids |= set(pd.to_numeric(frame["fixture_id"], errors="coerce").dropna().astype(int).tolist())
    names.load(ids)

    # scans
    listed = reg.read("SELECT scan_id, COALESCE(regime, 't088') AS regime, COUNT(*) AS n FROM coupons WHERE is_top = 1 GROUP BY 1, 2")
    listed_by = {(r.scan_id, r.regime): int(r.n) for r in listed.itertuples(index=False)}
    old_value = reg.read(
        "SELECT scan_id, COUNT(*) AS n, SUM(value_t088) AS t088, SUM(value_t094) AS t094 FROM legs WHERE is_value = 1 GROUP BY scan_id"
    ) if {"value_t088", "value_t094"} <= set(reg.read("SELECT * FROM legs LIMIT 0").columns) else pd.DataFrame()
    old_value_by = {r.scan_id: r for r in old_value.itertuples(index=False)} if not old_value.empty else {}
    for row in scans.itertuples(index=False):
        diag = json.loads(getattr(row, "diagnostics_json", None) or "{}")
        pipe = diag.get("pipeline") or {}
        by_tau = dict(pipe.get("value_by_tau") or {})
        if not by_tau:
            # an old scanner wrote no per-tax counts; take them from the leg log when every value leg was logged
            logged = old_value_by.get(row.scan_id)
            if logged is not None and int(logged.n) == int(getattr(row, "n_value", -1) or -1):
                by_tau = {"t088": logged.t088, "t094": logged.t094}
        model = pipe.get("model") or {}
        model_code = text(model.get("status")) or ("brak danych" if not pipe else "nie użyto")
        cfg_json = json.loads(getattr(row, "config_json", None) or "{}")
        edge_cap = num(cfg_json.get("max_edge"))
        kind = RUN_KIND.get(source.id) or ("powtórka (MK2)" if source.id.startswith("mk2-replay") else source.label)
        out["przebiegi"].append({
            "zrodlo_id": source.id,
            "zrodlo": source.label,
            "scan_id": f"{source.id}:{row.scan_id}",
            "skan": row.scan_id,
            "config_id": row.config_id,
            "as_of": iso(row.as_of),
            "mecze": integer(diag.get("n_fixtures", getattr(row, "n_fixtures", None))),
            "kursy": integer(getattr(row, "n_quotes", None)),
            "nogi_t088": integer(by_tau.get("t088")),
            "nogi_t094": integer(by_tau.get("t094")),
            "nogi_t100": integer(by_tau.get("t100", getattr(row, "n_value", None))),
            "kupony_top": integer(getattr(row, "n_coupons_top", None)),
            "kombinacje_ok_t088": integer(getattr(row, "n_coupons_passing", None)),
            "kupony_t088": listed_by.get((row.scan_id, "t088"), 0),
            "kupony_t094": listed_by.get((row.scan_id, "t094"), 0),
            "kupony_t100": listed_by.get((row.scan_id, "t100"), 0),
            "limit_przewagi": f"{edge_cap:.0%}" if edge_cap is not None else None,
            "luzne": bool(edge_cap is not None and edge_cap > 0.10 + 1e-9),
            "rodzaj": kind,
            "uruchomiony": pl_time(getattr(row, "started_at", None)),
            "model": MODEL_PL.get(model_code, "brak danych (stara wersja)" if model_code == "brak danych" else model_code),
            "model_kod": model_code,
        })

    configs = scans.drop_duplicates("config_id", keep="first")[["config_id", "config_json"]]
    clv_keep = ["scan_id", "bookmaker", "leg_key", "clv_raw", "closing_p_fair"]
    clv_small = clv[[c for c in clv_keep if c in clv.columns]] if not clv.empty else pd.DataFrame(columns=clv_keep)
    clv_by_leg = clv_small.drop_duplicates(["bookmaker", "leg_key"], keep="first") if not clv_small.empty else clv_small

    for cfg in configs.itertuples(index=False):
        note, n_missing = config_note(cfg.config_json)
        stored = json.loads(cfg.config_json or "{}")
        max_edge = num(stored.get("max_edge"))
        loose = max_edge is not None and max_edge > 0.10 + 1e-9
        cscans = scans[scans["config_id"] == cfg.config_id]

        # singles: the first value observation of every (bookmaker, leg) under this config
        legs = reg.paper_legs(cfg.config_id)
        if not legs.empty:
            legs = legs.merge(settled, on="leg_key", how="left")
            if not clv_small.empty:
                legs = legs.merge(clv_small[["scan_id", "bookmaker", "leg_key", "clv_raw"]], on=["scan_id", "bookmaker", "leg_key"], how="left")
            else:
                legs["clv_raw"] = np.nan
        for leg in (legs.to_dict("records") if not legs.empty else []):
            n = names.get(leg.get("fixture_id"))
            outcome = text(leg.get("outcome"))
            pending = open_state(iso(leg.get("kickoff_utc")), now)
            row = {
                "zrodlo_id": source.id,
                "zrodlo": source.label,
                "config_id": cfg.config_id,
                "noga_id": f"{source.id}:{cfg.config_id}:{leg.get('bookmaker')}:{leg.get('leg_key')}",
                "leg_key": text(leg.get("leg_key")),
                "as_of": iso(leg.get("as_of")),
                "bukmacher": text(leg.get("bookmaker")),
                "mecz": match_text(n, leg.get("fixture_id")),
                "liga": n.get("league"),
                "start": iso(leg.get("kickoff_utc")),
                "zaklad": leg_text(leg, n),
                "rynek": text(leg.get("market")),
                "kurs": num(leg.get("odd"), 4),
                "kurs_uczciwy": num(leg.get("fair_odd"), 4),
                "przewaga": num(leg.get("edge_raw"), 5),
                "przewaga_dolna": num(leg.get("edge_raw_lower"), 5),
                "p_uczciwe": num(leg.get("p_fair"), 5),
                "cena_z": SOURCE_PL.get(str(leg.get("fair_source")), text(leg.get("fair_source"))),
                "rynek_klasa": text(leg.get("tier")),
                "value_t088": integer(leg.get("value_t088")) or 0,
                "value_t094": integer(leg.get("value_t094")) or 0,
                "wynik": outcome_pl(outcome) if outcome else pending[0],
                "wynik_kod": outcome or pending[1],
                "clv": num(leg.get("clv_raw"), 5),
            }
            for tau in TAUS:
                label = st.tau_label(tau)
                row[f"zysk_{label}"] = num(st.single_profit(outcome, float(leg.get("odd")), tau), 5) if outcome else None
            out["single"].append(row)

        # coupons: every listed coupon, first appearance, with outcome and profit (the register's own helper)
        bets = reg.recent_bets(now=FAR_FUTURE, days=365 * 80, config_id=cfg.config_id, taus=TAUS)
        coupons, clegs = bets["coupons"], bets["coupon_legs"]
        if not clegs.empty and not clv_by_leg.empty:
            clegs = clegs.merge(clv_by_leg[["bookmaker", "leg_key", "closing_p_fair"]], on=["bookmaker", "leg_key"], how="left")
        elif not clegs.empty:
            clegs["closing_p_fair"] = np.nan
        for coupon in (coupons.to_dict("records") if not coupons.empty else []):
            group = clegs[(clegs["coupon_id"] == coupon["coupon_id"]) & (clegs["scan_id"] == coupon["scan_id"])] if not clegs.empty else clegs
            closing = group["closing_p_fair"].to_numpy(dtype=float) if len(group) else np.array([])
            coupon_clv = float(np.prod(group["odd"].to_numpy(dtype=float) * closing)) - 1.0 if len(group) and np.isfinite(closing).all() else None
            outcome = text(coupon.get("outcome"))
            key = f"{source.id}:{cfg.config_id}:{coupon['coupon_id']}"
            pending = open_state(iso(coupon.get("kickoff_last")), now)
            odds_total = num(coupon.get("odds_total"), 4)
            row = {
                "zrodlo_id": source.id,
                "zrodlo": source.label,
                "config_id": cfg.config_id,
                "kupon_id": key,
                "kupon": text(coupon.get("coupon_id")),
                "as_of": iso(coupon.get("as_of")),
                "bukmacher": text(coupon.get("bookmaker")),
                "nogi": integer(coupon.get("n_legs")),
                "kurs": odds_total,
                "zysk_mozliwy_t088": bool(odds_total is not None and odds_total * 0.88 > 1.0),
                "szansa": num(coupon.get("p_hit"), 5),
                "ev_dolne_t088": num(coupon.get("ev_net_lower_t088"), 5),
                "ev_dolne_t094": num(coupon.get("ev_net_lower_t094"), 5),
                "ev_dolne_t100": num(coupon.get("ev_net_lower_t100"), 5),
                "przechodzi": REGIME_PL.get(str(coupon.get("regime")), "podatek 12%" if coupon.get("regime") is None else text(coupon.get("regime"))),
                "przechodzi_kod": text(coupon.get("regime")) or "t088",
                "rynek_klasa": text(coupon.get("tier")),
                "start": iso(coupon.get("kickoff_first")),
                "koniec": iso(coupon.get("kickoff_last")),
                "mecze": " + ".join(match_text(names.get(f), f) for f in group["fixture_id"].tolist()) if len(group) else None,
                "wynik": outcome_pl(outcome) if outcome else pending[0],
                "wynik_kod": outcome or pending[1],
                "clv": num(coupon_clv, 5),
            }
            for tau in TAUS:
                label = st.tau_label(tau)
                row[f"zysk_{label}"] = num(coupon.get(f"profit_{label}"), 5)
            out["kupony"].append(row)
            for leg in group.to_dict("records"):
                n = names.get(leg.get("fixture_id"))
                lo = text(leg.get("outcome"))
                leg_pending = open_state(iso(leg.get("kickoff_utc")), now)
                out["kupony_nogi"].append({
                    "zrodlo_id": source.id,
                    "config_id": cfg.config_id,
                    "kupon_id": key,
                    "noga": f"{key}:{integer(leg.get('leg_no'))}",
                    "nr": integer(leg.get("leg_no")),
                    "mecz": match_text(n, leg.get("fixture_id")),
                    "liga": n.get("league"),
                    "start": iso(leg.get("kickoff_utc")),
                    "zaklad": leg_text(leg, n),
                    "kurs": num(leg.get("odd"), 4),
                    "p_uczciwe": num(leg.get("p_fair"), 5),
                    "cena_z": SOURCE_PL.get(str(leg.get("fair_source")), text(leg.get("fair_source"))),
                    "wynik": outcome_pl(lo) if lo else leg_pending[0],
                    "wynik_kod": lo or leg_pending[1],
                })

        # the register's own results table (all time)
        results = reg.paper_results(now=FAR_FUTURE, windows=(365 * 80,), taus=TAUS, config_id=cfg.config_id)
        for r in results.to_dict("records"):
            out["wyniki"].append({
                "wynik_id": f"{source.id}:{cfg.config_id}:{r['tier']}:{r['regime']}:{r['kind']}",
                "zrodlo_id": source.id,
                "zrodlo": source.label,
                "config_id": cfg.config_id,
                "rynek_klasa": r["tier"],
                "rynki": {"liquid": "płynne", "thin": "słabe"}.get(r["tier"], r["tier"]),
                "podatek": REGIME_PL.get(r["regime"], r["regime"]),
                "podatek_kod": r["regime"],
                "rodzaj": "kupon" if r["kind"] == "coupon" else "single",
                "zaklady": integer(r["n_bets"]),
                "rozliczone": integer(r["n_settled"]),
                "trafienia": num(r["hit_rate"], 5),
                "zysk_na_stawke": num(r["yield"], 5),
                "clv_pomiary": integer(r["n_clv"]),
                "clv_mediana": num(r["median_clv_raw"], 5),
                "clv_po_podatku": num(r["median_clv_net"], 5),
                "pobite_zamkniecia": num(r["share_beaten"], 5),
            })

        # one experiment row
        sc = [c for c in out["kupony"] if c["config_id"] == cfg.config_id]
        sg = [s for s in out["single"] if s["config_id"] == cfg.config_id]
        decided = [c for c in sc if c["wynik_kod"] not in ("open", "no_result")]
        out["eksperymenty"].append({
            "eksperyment_id": f"{source.id}:{cfg.config_id}",
            "zrodlo_id": source.id,
            "zrodlo": source.label,
            "config_id": cfg.config_id,
            "opis": note,
            "stara_wersja": n_missing > 0,
            "zabezpieczenia": (f"luźne: przewaga do {max_edge:.0%}" if loose else
                               f"pełne: przewaga do {max_edge:.0%}" if max_edge is not None else "brak limitu przewagi"),
            "luzne": loose,
            "max_przewaga": max_edge,
            "nogi_w_kuponie": ", ".join(str(int(n)) for n in (stored.get("coupon_sizes") or [])) or None,
            "kurs_kuponu_max": num(stored.get("max_coupon_odds")),
            "przebiegi": int(len(cscans)),
            "od": iso(cscans["as_of"].min()),
            "do": iso(cscans["as_of"].max()),
            "single": len(sg),
            "single_rozliczone": sum(1 for s in sg if s["wynik_kod"] not in ("open", "no_result")),
            "kupony": len(sc),
            "kupony_ok_t088": sum(1 for c in sc if c["przechodzi_kod"] == "t088"),
            "kupony_ok_t094": sum(1 for c in sc if c["przechodzi_kod"] in ("t088", "t094")),
            "kupony_za_niski_kurs": sum(1 for c in sc if not c["zysk_mozliwy_t088"]),
            "kupony_rozliczone": len(decided),
            "kupony_wygrane": sum(1 for c in sc if c["wynik_kod"] == "win"),
            "kupony_przegrane": sum(1 for c in sc if c["wynik_kod"] in ("loss", "half_loss")),
            "kupony_bez_wyniku": sum(1 for c in sc if c["wynik_kod"] == "no_result"),
            "kupony_przed_meczem": sum(1 for c in sc if c["wynik_kod"] == "open"),
            "zysk_kupony_t088": money_sum(sc, "zysk_t088"),
            "zysk_kupony_t100": money_sum(sc, "zysk_t100"),
            "zysk_single_t088": money_sum(sg, "zysk_t088"),
            "zysk_single_t100": money_sum(sg, "zysk_t100"),
        })
    return out


# --------------------------------------------------------------------------- the snapshot and our folder


def snapshot_rows(data_root: Path, out_root: Path, now: pd.Timestamp) -> list[dict[str, Any]]:
    """What the snapshot and our own folder hold, computed from the databases (read only).  Times are Polish."""
    rows: list[dict[str, Any]] = []
    stale_after = pd.Timedelta(days=3)

    def add(sekcja: str, pozycja: str, wartosc: Any, stan: str = "ok", uwagi: str | None = None) -> None:
        rows.append({"dane_id": f"{sekcja}:{pozycja}", "sekcja": sekcja, "pozycja": pozycja,
                     "wartosc": None if wartosc is None else str(wartosc), "stan": stan, "uwagi": uwagi})

    def fresh(stamp: Any) -> str:
        return "ok" if stamp and now - pd.Timestamp(stamp).tz_convert("UTC") <= stale_after else "uwaga"

    data_end = None
    odds = data_root / "api-football-odds" / "odds.sqlite3"
    if odds.exists():
        con = ro(odds)
        try:
            data_end = con.execute("SELECT MAX(fetched_at) FROM observations").fetchone()[0]
        finally:
            con.close()

    imports = sorted((data_root / "_import").glob("import_*.json")) if (data_root / "_import").exists() else []
    if imports:
        rec = json.loads(imports[-1].read_text(encoding="utf-8"))
        add("Paczka od Oskara", "Plik", Path(str(rec.get("source_zip") or rec.get("source") or "")).name)
        add("Paczka od Oskara", "Dane kończą się", pl_time(data_end), fresh(data_end),
            "najnowsze kursy w paczce; potem zbieranie przeszło na serwer Oskara")
        unpacked = rec.get("imported_at_local")
        try:
            unpacked = datetime.strptime(unpacked, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y, %H:%M")
        except (TypeError, ValueError):
            pass
        add("Paczka od Oskara", "Rozpakowana u nas", unpacked)
        if rec.get("files"):
            add("Paczka od Oskara", "Pliki", pl_int(rec["files"]))
        if rec.get("extracted_bytes"):
            size = rec["extracted_bytes"]
            add("Paczka od Oskara", "Rozmiar", f"{size / 1e9:.2f} GB ({size / 2**30:.2f} GiB)".replace(".", ","))
        if rec.get("zip_sha256") or rec.get("source_sha256"):
            add("Paczka od Oskara", "Suma kontrolna (sha256)", (rec.get("zip_sha256") or rec.get("source_sha256"))[:16] + "…")

    history = data_root / "api-football" / "tables" / "history.sqlite"
    if history.exists():
        con = ro(history)
        try:
            n, leagues, s0, s1 = con.execute("SELECT COUNT(*), COUNT(DISTINCT league_id), MIN(season), MAX(season) FROM matches").fetchone()
            last = con.execute("SELECT MAX(kickoff_utc) FROM matches WHERE status_short IN ('FT','AET','PEN')").fetchone()[0]
            built = con.execute("SELECT built_at FROM build_info").fetchone()
        finally:
            con.close()
        add("Historia meczów", "Mecze", pl_int(n))
        add("Historia meczów", "Rozgrywki", leagues, uwagi=f"z modelami: {len(lm.MODELLED_LEAGUE_IDS)}")
        add("Historia meczów", "Sezony", f"{s0}–{s1}")
        add("Historia meczów", "Ostatni zakończony mecz", pl_time(last), "problem" if fresh(last) != "ok" else "ok",
            "nasz nocny fit modeli pomija historię starszą niż 3 dni, więc modele stoją do nowszej paczki")
        add("Historia meczów", "Tabele zbudowane", pl_time(built[0]) if built else None, fresh(built[0]) if built else "uwaga")

    if odds.exists():
        con = ro(odds)
        try:
            runs, stored, first, last = con.execute(
                "SELECT COUNT(*), SUM(quotes_changed > 0), MIN(started_at), MAX(started_at) FROM scans").fetchone()
            hours = [int(h) for (h,) in con.execute("SELECT DISTINCT CAST(strftime('%H', started_at) AS INT) FROM scans WHERE quotes_changed > 0")]
            fixtures, leagues = con.execute("SELECT COUNT(*), COUNT(DISTINCT league_id) FROM fixtures").fetchone()
            modelled = con.execute(
                f"SELECT COUNT(*) FROM fixtures WHERE league_id IN ({','.join(str(int(i)) for i in lm.MODELLED_LEAGUE_IDS)})").fetchone()[0]
            books = con.execute("SELECT bookmaker_id, COUNT(*) FROM quotes GROUP BY bookmaker_id").fetchall()
            pinnacle = con.execute("SELECT COUNT(DISTINCT fixture_id) FROM observations WHERE bookmaker_id = 4").fetchone()[0]
            quotes = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM quotes").fetchone()[0]
            lead = pd.read_sql_query(
                "SELECT f.fixture_id, f.kickoff_utc, MAX(o.fetched_at) AS last_seen FROM fixtures f "
                "JOIN observations o ON o.fixture_id = f.fixture_id AND o.fetched_at < f.kickoff_utc "
                "WHERE f.kickoff_utc <= (SELECT MAX(fetched_at) FROM observations) GROUP BY f.fixture_id", con)
        finally:
            con.close()
        add("Kursy (archiwum Oskara)", "Uruchomienia kolektora", runs, uwagi=f"{int(stored or 0)} zapisało kursy; {pl_time(first)} – {pl_time(last)}")
        if hours:
            add("Kursy (archiwum Oskara)", "Godziny pracy kolektora (UTC)", f"{min(hours)}–{max(hours)}", "problem",
                "komputer Oskara nie chodził całą dobę: brak kursów w nocy i rano")
        add("Kursy (archiwum Oskara)", "Mecze z kursami", pl_int(fixtures),
            uwagi=f"{leagues} rozgrywek; w ligach z modelami {modelled}")
        add("Kursy (archiwum Oskara)", "Zapisane zmiany kursów", pl_int(quotes))
        real = sum(1 for _, n in books if n >= 1000)
        add("Kursy (archiwum Oskara)", "Bukmacherzy z danymi", real, uwagi=f"{len(books)} z co najmniej jednym kursem")
        add("Kursy (archiwum Oskara)", "Mecze z Pinnacle", pinnacle, uwagi="Pinnacle wyznacza uczciwą cenę")
        if len(lead):
            gap = (pd.to_datetime(lead["kickoff_utc"], utc=True) - pd.to_datetime(lead["last_seen"], utc=True)).dt.total_seconds() / 3600
            add("Kursy (archiwum Oskara)", "Kurs w ostatniej godzinie przed meczem", f"{(gap <= 1).mean():.0%}",
                "uwaga" if (gap <= 1).mean() < 0.9 else "ok",
                f"{len(lead)} meczów rozegranych w czasie zbierania; {(gap > 12).mean():.0%} miało ostatni kurs ponad 12 h przed startem")

    for label, directory in (("Kalendarz i wyniki (Oskar)", data_root / "fixture-directory" / "directory.sqlite3"),
                             ("Kalendarz i wyniki (nasz)", out_root / "fixture-directory" / "directory.sqlite3")):
        if not directory.exists():
            continue
        con = ro(directory)
        try:
            n, finished, fetched, k0, k1 = con.execute(
                "SELECT COUNT(*), SUM(status_short IN ('FT','AET','PEN')), MAX(fetched_at), MIN(kickoff_utc), MAX(kickoff_utc) FROM fixtures"
            ).fetchone()
            stats = con.execute("SELECT COUNT(DISTINCT fixture_id) FROM fixture_stats").fetchone()[0]
        finally:
            con.close()
        add(label, "Mecze w kalendarzu", pl_int(n), fresh(fetched),
            f"starty {pl_time(k0)} – {pl_time(k1)}; ostatnie odświeżenie {pl_time(fetched)}")
        add(label, "Mecze z wynikiem", pl_int(int(finished or 0)))
        add(label, "Mecze ze statystykami", stats, "problem" if stats < 100 else "ok",
            "bez statystyk nie da się rozliczyć rożnych i kartek")

    fits = sorted((data_root / "scanner" / "model_cache" / "fits").glob("*/manifest.json"))
    if fits:
        manifest = json.loads(fits[-1].read_text(encoding="utf-8"))
        fit_as_of = pd.Timestamp(manifest.get("fit_as_of"))
        add("Modele", "Ostatni fit", pl_time(fit_as_of), fresh(fit_as_of),
            f"skaner używa fitu najwyżej 24 h, czyli do {pl_time(fit_as_of + pd.Timedelta(hours=24))}")
        add("Modele", "Mecze w treningu", pl_int(int(manifest.get("n_train_matches") or 0)),
            uwagi=f"{len(manifest.get('leagues') or {})} lig")

    own_odds = out_root / "api-football-odds" / "odds.sqlite3"
    if own_odds.exists():
        con = ro(own_odds)
        try:
            scans, last = con.execute("SELECT COUNT(*), MAX(started_at) FROM scans").fetchone()
            fixtures = con.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        finally:
            con.close()
        add("Nasze dane (MK2)", "Pobrania kursów", scans, "uwaga" if scans < 10 else "ok",
            f"ostatnie {pl_time(last)}; na kluczu Free tylko test")
        add("Nasze dane (MK2)", "Mecze z kursami", fixtures)
    own_cache = out_root / "model_cache" / "fits"
    has_fit = any(own_cache.glob("*/manifest.json"))
    add("Nasze dane (MK2)", "Własny fit modeli", "jest" if has_fit else "brak", "ok" if has_fit else "uwaga",
        None if has_fit else "czeka na historię nie starszą niż 3 dni")
    return rows


# --------------------------------------------------------------------------- summary of distinct bets

SETTLED_FIRST = {"no_result": 1, "open": 2}


def _best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Of several copies of one bet (other configs or registers), the one with a result if any has it."""
    return min(rows, key=lambda r: SETTLED_FIRST.get(r["wynik_kod"], 0))


def summarize(source: Source | None, tables: dict[str, list[dict[str, Any]]], loose_keys: set) -> dict[str, Any]:
    """Headline figures of one source (or of all, ``source=None``) counted over DISTINCT bets.

    The same bet appears under several configs of one register and in several registers (Oskar's live and
    offline registers share a scan, both MK2 replays share their singles), so a plain sum of the experiment rows
    would count it more than once.  A single is one (bookmaker, leg); a coupon is one coupon id; a scan is one
    (scan id, config).  Of the copies of one bet, a settled one wins.
    """
    sid = source.id if source else "razem"
    pick = (lambda r: r["zrodlo_id"] == sid) if source else (lambda r: True)
    scans = {(s["skan"], s["config_id"]): s for s in tables["przebiegi"] if pick(s)}
    singles: dict[tuple, list[dict[str, Any]]] = {}
    for s in tables["single"]:
        if pick(s):
            singles.setdefault((s["bukmacher"], s["leg_key"]), []).append(s)
    coupons: dict[str, list[dict[str, Any]]] = {}
    for c in tables["kupony"]:
        if pick(c):
            coupons.setdefault(c["kupon"], []).append(c)
    one_single = [_best(v) for v in singles.values()]
    one_coupon = [_best(v) for v in coupons.values()]

    def passes(rows: list[dict[str, Any]], codes: tuple[str, ...], loose: bool | None) -> bool:
        return any(r["przechodzi_kod"] in codes and (loose is None or ((r["zrodlo_id"], r["config_id"]) in loose_keys) == loose)
                   for r in rows)

    times = [s["as_of"] for s in scans.values() if s["as_of"]]
    return {
        "zrodlo_id": sid,
        "zrodlo": source.label if source else "Wszystkie źródła",
        "kto": source.owner if source else "Oskar + MK2",
        "przebiegi": len(scans),
        "przebiegi_pelne": sum(1 for k in scans if (scans[k]["zrodlo_id"], k[1]) not in loose_keys),
        "przebiegi_luzne": sum(1 for k in scans if (scans[k]["zrodlo_id"], k[1]) in loose_keys),
        "eksperymenty": sum(1 for e in tables["eksperymenty"] if pick(e)),
        "konfiguracje": len({e["config_id"] for e in tables["eksperymenty"] if pick(e)}),
        "od": min(times, default=None),
        "do": max(times, default=None),
        "single": len(one_single),
        "single_rozliczone": sum(1 for s in one_single if s["wynik_kod"] not in ("open", "no_result")),
        "single_bez_wyniku": sum(1 for s in one_single if s["wynik_kod"] == "no_result"),
        "kupony": len(one_coupon),
        "kupony_ok_t088_pelne": sum(1 for v in coupons.values() if passes(v, ("t088",), False)),
        "kupony_ok_t088_luzne": sum(1 for v in coupons.values() if passes(v, ("t088",), True) and not passes(v, ("t088",), False)),
        "kupony_ok_t094": sum(1 for v in coupons.values() if passes(v, ("t088", "t094"), None)),
        "kupony_za_niski_kurs": sum(1 for c in one_coupon if not c["zysk_mozliwy_t088"]),
        "kupony_rozliczone": sum(1 for c in one_coupon if c["wynik_kod"] not in ("open", "no_result")),
        "kupony_wygrane": sum(1 for c in one_coupon if c["wynik_kod"] == "win"),
        "kupony_przegrane": sum(1 for c in one_coupon if c["wynik_kod"] in ("loss", "half_loss")),
        "kupony_bez_wyniku": sum(1 for c in one_coupon if c["wynik_kod"] == "no_result"),
        "kupony_przed_meczem": sum(1 for c in one_coupon if c["wynik_kod"] == "open"),
    }


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--dest", default=None, help="output folder (default: <out-root>\\dashboard)")
    parser.add_argument("--status", default=str(APP / "config" / "mk2" / "status.json"))
    args = parser.parse_args(argv)
    data_root, out_root = Path(args.data_root), Path(args.out_root)
    dest = Path(args.dest) if args.dest else out_root / "dashboard"
    dest.mkdir(parents=True, exist_ok=True)

    now = pd.Timestamp.now(tz="UTC")
    sources = discover(data_root, out_root)
    names = Names(
        [out_root / "fixture-directory" / "directory.sqlite3", data_root / "fixture-directory" / "directory.sqlite3"],
        data_root / "api-football" / "tables" / "history.sqlite",
    )
    tables: dict[str, list[dict[str, Any]]] = {k: [] for k in ("eksperymenty", "przebiegi", "kupony", "kupony_nogi", "single", "wyniki")}
    with tempfile.TemporaryDirectory(prefix="mk2-dash-") as tmp:
        for source in sources:
            reg = open_copy(source, Path(tmp))
            part = export_source(source, reg, names, now)
            for key, rows in part.items():
                tables[key].extend(rows)
            print(f"{source.id}: scans {len(part['przebiegi'])}, configs {len(part['eksperymenty'])}, "
                  f"singles {len(part['single'])}, coupons {len(part['kupony'])}")

    loose_keys = {(e["zrodlo_id"], e["config_id"]) for e in tables["eksperymenty"] if e["luzne"]}
    summary = [summarize(source, tables, loose_keys) for source in [*sources, None]]
    status = json.loads(Path(args.status).read_text(encoding="utf-8"))
    plan = [{k: v for k, v in item.items()} | {"status_pl": {
        "gotowe": "gotowe", "w_toku": "w toku", "brakuje": "brakuje", "decyzja": "do decyzji", "problem": "problem"}.get(item["status"], item["status"])}
        for item in status["items"]]
    datasets = {
        **tables,
        "podsumowanie": summary,
        "dane": snapshot_rows(data_root, out_root, now),
        "plan": plan,
    }
    manifest = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status_updated": status.get("updated"),
                "sources": [{"id": s.id, "label": s.label, "owner": s.owner, "register": str(s.path)} for s in sources], "files": {}}
    for name, rows in datasets.items():
        path = dest / f"{name}.json"
        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        path.write_text(payload, encoding="utf-8")
        manifest["files"][name] = {"rows": len(rows), "bytes": path.stat().st_size,
                                   "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    # what the published app may show: no local paths, only times and the sources' names
    data_end = next((r["wartosc"] for r in datasets["dane"] if r["pozycja"] == "Dane kończą się"), None)
    meta = {"eksport": pl_time(now), "eksport_utc": manifest["generated_at"], "plan_z_dnia": status.get("updated"),
            "dane_do": data_end, "zrodla": [{"id": s.id, "nazwa": s.label, "kto": s.owner} for s in sources]}
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, info in manifest["files"].items():
        print(f"  {name}.json: {info['rows']} rows, {info['bytes'] / 1024:.0f} KB")
    print(f"written to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
