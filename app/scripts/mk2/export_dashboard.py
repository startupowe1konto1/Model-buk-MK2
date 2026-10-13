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
    "mk2-replay-oskar-config": "MK2 · powtórka, ustawienia Oskara",
    "mk2-replay-mk2-band": "MK2 · powtórka, 2 nogi do kursu 2,00",
    "mk2-live-smoke": "MK2 · test na żywo (klucz Free)",
    "mk2-live": "MK2 · na żywo",
}
KINDS = {"oskar-live": "Oskar", "oskar-offline": "Oskar"}
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


def export_source(source: Source, reg: ScannerRegister, names: Names) -> dict[str, list[dict[str, Any]]]:
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
    for row in scans.itertuples(index=False):
        diag = json.loads(getattr(row, "diagnostics_json", None) or "{}")
        pipe = diag.get("pipeline") or {}
        by_tau = pipe.get("value_by_tau") or {}
        model = pipe.get("model") or {}
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
            "kupony_ok_t088": integer(getattr(row, "n_coupons_passing", None)),
            "model": text(model.get("status")) or ("brak danych" if not pipe else "nie użyto"),
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
            row = {
                "zrodlo_id": source.id,
                "zrodlo": source.label,
                "config_id": cfg.config_id,
                "noga_id": f"{source.id}:{cfg.config_id}:{leg.get('bookmaker')}:{leg.get('leg_key')}",
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
                "wynik": outcome_pl(outcome) if outcome else "otwarty",
                "wynik_kod": outcome or "open",
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
            key = f"{source.id}:{coupon['coupon_id']}"
            row = {
                "zrodlo_id": source.id,
                "zrodlo": source.label,
                "config_id": cfg.config_id,
                "kupon_id": key,
                "as_of": iso(coupon.get("as_of")),
                "bukmacher": text(coupon.get("bookmaker")),
                "nogi": integer(coupon.get("n_legs")),
                "kurs": num(coupon.get("odds_total"), 4),
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
                "wynik": outcome_pl(outcome) if outcome else "otwarty",
                "wynik_kod": outcome or "open",
                "clv": num(coupon_clv, 5),
            }
            for tau in TAUS:
                label = st.tau_label(tau)
                row[f"zysk_{label}"] = num(coupon.get(f"profit_{label}"), 5)
            out["kupony"].append(row)
            for leg in group.to_dict("records"):
                n = names.get(leg.get("fixture_id"))
                lo = text(leg.get("outcome"))
                out["kupony_nogi"].append({
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
                    "wynik": outcome_pl(lo) if lo else "otwarty",
                    "wynik_kod": lo or "open",
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
        decided = [c for c in sc if c["wynik_kod"] in ("win", "loss", "half_loss", "push", "void")]
        out["eksperymenty"].append({
            "eksperyment_id": f"{source.id}:{cfg.config_id}",
            "zrodlo_id": source.id,
            "zrodlo": source.label,
            "config_id": cfg.config_id,
            "opis": note,
            "stara_wersja": n_missing > 0,
            "zabezpieczenia": "luźne: przewaga do 20%" if loose else "pełne: przewaga do 10%",
            "luzne": loose,
            "max_przewaga": max_edge,
            "nogi_w_kuponie": ", ".join(str(int(n)) for n in (stored.get("coupon_sizes") or [])) or None,
            "kurs_kuponu_max": num(stored.get("max_coupon_odds")),
            "przebiegi": int(len(cscans)),
            "od": iso(cscans["as_of"].min()),
            "do": iso(cscans["as_of"].max()),
            "single": len(sg),
            "single_rozliczone": sum(1 for s in sg if s["wynik_kod"] != "open"),
            "kupony": len(sc),
            "kupony_ok_t088": sum(1 for c in sc if c["przechodzi_kod"] == "t088"),
            "kupony_ok_t094": sum(1 for c in sc if c["przechodzi_kod"] == "t094"),
            "kupony_rozliczone": len(decided),
            "kupony_wygrane": sum(1 for c in sc if c["wynik_kod"] == "win"),
            "kupony_przegrane": sum(1 for c in sc if c["wynik_kod"] in ("loss", "half_loss")),
            "kupony_otwarte": sum(1 for c in sc if c["wynik_kod"] == "open"),
            "zysk_kupony_t088": num(sum(c["zysk_t088"] for c in sc if c["zysk_t088"] is not None), 4),
            "zysk_kupony_t100": num(sum(c["zysk_t100"] for c in sc if c["zysk_t100"] is not None), 4),
            "zysk_single_t088": num(sum(s["zysk_t088"] for s in sg if s["zysk_t088"] is not None), 4),
            "zysk_single_t100": num(sum(s["zysk_t100"] for s in sg if s["zysk_t100"] is not None), 4),
        })
    return out


# --------------------------------------------------------------------------- the snapshot and our folder


def snapshot_rows(data_root: Path, out_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(sekcja: str, pozycja: str, wartosc: Any, stan: str = "ok", uwagi: str | None = None) -> None:
        rows.append({"dane_id": f"{sekcja}:{pozycja}", "sekcja": sekcja, "pozycja": pozycja,
                     "wartosc": None if wartosc is None else str(wartosc), "stan": stan, "uwagi": uwagi})

    imports = sorted((data_root / "_import").glob("import_*.json")) if (data_root / "_import").exists() else []
    if imports:
        rec = json.loads(imports[-1].read_text(encoding="utf-8"))
        add("Paczka od Oskara", "Plik", Path(str(rec.get("source_zip") or rec.get("source") or "")).name)
        add("Paczka od Oskara", "Przyjęta", rec.get("imported_at_local"))
        if rec.get("files"):
            add("Paczka od Oskara", "Pliki", f"{int(rec['files']):,}".replace(",", " "))
        if rec.get("extracted_bytes"):
            add("Paczka od Oskara", "Rozmiar", f"{rec['extracted_bytes'] / 1e9:.2f} GB".replace(".", ","))
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
        age = (datetime.now(timezone.utc) - pd.Timestamp(last).to_pydatetime()).days if last else None
        add("Historia meczów", "Mecze", f"{n:,}".replace(",", " "))
        add("Historia meczów", "Rozgrywki", leagues, uwagi="47 lig europejskich, 26 spoza Europy, 16 pucharów")
        add("Historia meczów", "Sezony", f"{s0}–{s1}")
        add("Historia meczów", "Ostatni zakończony mecz", iso(last), "problem" if age is not None and age > 3 else "ok",
            f"{age} dni temu; fit modeli wymaga historii nie starszej niż 3 dni" if age is not None else None)
        add("Historia meczów", "Tabele zbudowane", iso(built[0]) if built else None)

    odds = data_root / "api-football-odds" / "odds.sqlite3"
    if odds.exists():
        con = ro(odds)
        try:
            scans, first, last = con.execute("SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM scans").fetchone()
            fixtures, leagues = con.execute("SELECT COUNT(*), COUNT(DISTINCT league_id) FROM fixtures").fetchone()
            books = con.execute("SELECT COUNT(DISTINCT bookmaker_id) FROM observations").fetchone()[0]
            pinnacle = con.execute("SELECT COUNT(DISTINCT fixture_id) FROM observations WHERE bookmaker_id = 4").fetchone()[0]
            quotes = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM quotes").fetchone()[0]
        finally:
            con.close()
        add("Kursy (archiwum Oskara)", "Pobrania", scans, uwagi=f"{iso(first)} – {iso(last)}")
        add("Kursy (archiwum Oskara)", "Mecze z kursami", f"{fixtures:,}".replace(",", " "), uwagi=f"{leagues} rozgrywek")
        add("Kursy (archiwum Oskara)", "Zapisane zmiany kursów", f"{quotes:,}".replace(",", " "))
        add("Kursy (archiwum Oskara)", "Bukmacherzy", books)
        add("Kursy (archiwum Oskara)", "Mecze z Pinnacle", pinnacle, uwagi="Pinnacle wyznacza uczciwą cenę")
        add("Kursy (archiwum Oskara)", "Godziny pracy kolektora", "ok. 15–21 UTC", "problem",
            "komputer Oskara nie chodził całą dobę: przerwy po 18–20 h, mało kursów tuż przed meczem")

    directory = data_root / "fixture-directory" / "directory.sqlite3"
    if directory.exists():
        con = ro(directory)
        try:
            n, finished, fetched = con.execute(
                "SELECT COUNT(*), SUM(status_short IN ('FT','AET','PEN')), MAX(fetched_at) FROM fixtures"
            ).fetchone()
            stats = con.execute("SELECT COUNT(DISTINCT fixture_id) FROM fixture_stats").fetchone()[0]
        finally:
            con.close()
        add("Kalendarz i wyniki", "Mecze w kalendarzu", f"{n:,}".replace(",", " "), uwagi=f"ostatnie odświeżenie {iso(fetched)}")
        add("Kalendarz i wyniki", "Mecze z wynikiem", f"{int(finished or 0):,}".replace(",", " "))
        add("Kalendarz i wyniki", "Mecze ze statystykami", stats, "problem" if stats < 100 else "ok",
            "bez statystyk nie da się rozliczyć rożnych i kartek")

    fits = sorted((data_root / "scanner" / "model_cache" / "fits").glob("*/manifest.json"))
    if fits:
        manifest = json.loads(fits[-1].read_text(encoding="utf-8"))
        fit_as_of = pd.Timestamp(manifest.get("fit_as_of"))
        add("Modele", "Ostatni fit", iso(fit_as_of), "problem",
            f"skaner używa fitu do 24 h, czyli do {iso(fit_as_of + pd.Timedelta(hours=24))}")
        add("Modele", "Mecze w treningu", f"{int(manifest.get('n_train_matches') or 0):,}".replace(",", " "),
            uwagi=f"{len(manifest.get('leagues') or {})} lig")

    own_odds = out_root / "api-football-odds" / "odds.sqlite3"
    if own_odds.exists():
        con = ro(own_odds)
        try:
            scans, last = con.execute("SELECT COUNT(*), MAX(started_at) FROM scans").fetchone()
            fixtures = con.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        finally:
            con.close()
        add("Nasze dane (MK2)", "Pobrania kursów", scans, "uwaga" if scans < 10 else "ok", f"ostatnie {iso(last)}; na kluczu Free tylko test")
        add("Nasze dane (MK2)", "Mecze z kursami", fixtures)
    own_cache = out_root / "model_cache" / "fits"
    add("Nasze dane (MK2)", "Własny fit modeli", "jest" if any(own_cache.glob("*/manifest.json")) else "brak",
        "ok" if any(own_cache.glob("*/manifest.json")) else "uwaga", "czeka na świeżą historię")
    return rows


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

    sources = discover(data_root, out_root)
    names = Names(
        [out_root / "fixture-directory" / "directory.sqlite3", data_root / "fixture-directory" / "directory.sqlite3"],
        data_root / "api-football" / "tables" / "history.sqlite",
    )
    tables: dict[str, list[dict[str, Any]]] = {k: [] for k in ("eksperymenty", "przebiegi", "kupony", "kupony_nogi", "single", "wyniki")}
    with tempfile.TemporaryDirectory(prefix="mk2-dash-") as tmp:
        for source in sources:
            reg = open_copy(source, Path(tmp))
            part = export_source(source, reg, names)
            for key, rows in part.items():
                tables[key].extend(rows)
            print(f"{source.id}: scans {len(part['przebiegi'])}, configs {len(part['eksperymenty'])}, "
                  f"singles {len(part['single'])}, coupons {len(part['kupony'])}")

    loose_keys = {(e["zrodlo_id"], e["config_id"]) for e in tables["eksperymenty"] if e["luzne"]}
    summary = []
    for source in [*sources, None]:
        sid = source.id if source else "razem"
        ex = [e for e in tables["eksperymenty"] if source is None or e["zrodlo_id"] == sid]
        sc = [s for s in tables["przebiegi"] if source is None or s["zrodlo_id"] == sid]
        summary.append({
            "zrodlo_id": sid,
            "zrodlo": source.label if source else "Wszystkie źródła",
            "kto": source.owner if source else "Oskar + MK2",
            "przebiegi": len(sc),
            "eksperymenty": len(ex),
            "od": min((s["as_of"] for s in sc if s["as_of"]), default=None),
            "do": max((s["as_of"] for s in sc if s["as_of"]), default=None),
            "single": sum(e["single"] for e in ex),
            "single_rozliczone": sum(e["single_rozliczone"] for e in ex),
            "kupony": sum(e["kupony"] for e in ex),
            "kupony_ok_t088": sum(e["kupony_ok_t088"] for e in ex),
            "kupony_ok_t088_pelne": sum(e["kupony_ok_t088"] for e in ex if not e["luzne"]),
            "kupony_ok_t088_luzne": sum(e["kupony_ok_t088"] for e in ex if e["luzne"]),
            "kupony_rozliczone": sum(e["kupony_rozliczone"] for e in ex),
            "kupony_wygrane": sum(e["kupony_wygrane"] for e in ex),
            "kupony_przegrane": sum(e["kupony_przegrane"] for e in ex),
            "kupony_otwarte": sum(e["kupony_otwarte"] for e in ex),
            "przebiegi_z_kuponem_t088": sum(1 for s in sc if (s["kupony_ok_t088"] or 0) > 0),
            "przebiegi_pelne": sum(1 for s in sc if (s["zrodlo_id"], s["config_id"]) not in loose_keys),
            "przebiegi_luzne": sum(1 for s in sc if (s["zrodlo_id"], s["config_id"]) in loose_keys),
        })
    status = json.loads(Path(args.status).read_text(encoding="utf-8"))
    plan = [{k: v for k, v in item.items()} | {"status_pl": {
        "gotowe": "gotowe", "w_toku": "w toku", "brakuje": "brakuje", "decyzja": "do decyzji", "problem": "problem"}.get(item["status"], item["status"])}
        for item in status["items"]]
    datasets = {
        **tables,
        "podsumowanie": summary,
        "dane": snapshot_rows(data_root, out_root),
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
    for name, info in manifest["files"].items():
        print(f"  {name}.json: {info['rows']} rows, {info['bytes'] / 1024:.0f} KB")
    print(f"written to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
