"""Static Polish HTML page of the scanner: status, what pays after the 12% tax, what pays only under a lower tax,
the thin markets as an experiment, and the paper results (which proposals won and which lost).

The page is one self-contained file (inline CSS, one tiny inline script that only sorts tables; no external
fonts, scripts or images) that reads well on a phone.  :func:`render_page` builds it from a
:class:`model_buk.scanner.engine.ScanResult`; :func:`write_page` writes ``latest.html`` and ``html/YYYY-MM-DD.html``.
All text that comes from data is escaped.  User-facing text is Polish.
"""

from __future__ import annotations

import html as _html
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from model_buk.scanner.settlement import tau_label

if TYPE_CHECKING:  # pragma: no cover
    from model_buk.scanner.engine import ScanResult

PAGE_COUPONS = 15
"""Coupons shown on the page (the register holds every coupon that passed)."""
TIMEZONE = "Europe/Warsaw"
_WEEKDAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
_WEEKDAYS_SHORT = ("pn", "wt", "śr", "czw", "pt", "sob", "nd")
_DASH = "—"


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def esc(value: object) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def fmt_num(value: object, digits: int = 2) -> str:
    """Number with a Polish decimal comma; an em dash when missing."""
    if not _finite(value):
        return _DASH
    return f"{float(value):.{digits}f}".replace(".", ",")


def fmt_int(value: object) -> str:
    """Whole number with a thin space between thousands (``45113`` -> ``45 113``)."""
    if not _finite(value):
        return _DASH
    return f"{int(value):,}".replace(",", " ")  # type: ignore[call-overload]


def fmt_pct(value: object, digits: int = 1, *, signed: bool = False) -> str:
    """Fraction as a percentage (``0.052`` -> ``5,2%``), with an explicit plus sign when ``signed``."""
    if not _finite(value):
        return _DASH
    number = float(value) * 100.0
    text = f"{number:+.{digits}f}" if signed else f"{number:.{digits}f}"
    return text.replace(".", ",") + "%"


def fmt_line(value: object) -> str:
    """Line without trailing zeros: ``2.5`` -> ``2,5``, ``2.0`` -> ``2``, ``-0.25`` -> ``-0,25``."""
    if not _finite(value):
        return ""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _signed_line(value: float) -> str:
    text = fmt_line(value)
    return text if text.startswith("-") or text in ("", "0") else "+" + text


def regime_label(tau: float) -> str:
    """Short Polish name of a stake-tax regime."""
    if math.isclose(tau, 0.88, abs_tol=1e-9):
        return "podatek 12%"
    if math.isclose(tau, 1.0, abs_tol=1e-9):
        return "bez podatku"
    return f"τ {fmt_num(tau, 2)}"


def local_time(value: object) -> pd.Timestamp | None:
    """A timestamp in Polish time; ``None`` when missing."""
    if value is None or value is pd.NaT:
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
    try:
        return stamp.tz_convert(TIMEZONE)
    except Exception:  # noqa: BLE001 - a missing time zone database must not break the page
        return stamp.tz_convert("UTC")


def _clock(value: object) -> str:
    stamp = local_time(value)
    return _DASH if stamp is None else stamp.strftime("%H:%M")


def _date_short(stamp: pd.Timestamp) -> str:
    return f"{_WEEKDAYS_SHORT[stamp.weekday()]} {stamp.strftime('%d.%m')}"


def _when(value: object) -> str:
    stamp = local_time(value)
    return _DASH if stamp is None else f"{_date_short(stamp)} {stamp.strftime('%H:%M')}"


def _win_share(p_fair: object, refund: object) -> float:
    """Probability that a leg wins outright: the break-even probability less the share that comes back on a push."""
    if not _finite(p_fair):
        return float("nan")
    return float(p_fair) * (1.0 - (float(refund) if _finite(refund) else 0.0))


def _ev_class(value: object) -> str:
    if not _finite(value):
        return "na"
    return "pos" if float(value) > 0 else "neg"


# --------------------------------------------------------------------------- #
# Plain Polish description of a leg
# --------------------------------------------------------------------------- #

_TOTAL_WORD = {"over": "powyżej", "under": "poniżej"}
_PERIOD_PREFIX = {"ft": "", "1h": "1. połowa: ", "2h": "2. połowa: "}


def leg_label_pl(market: str, period: str, line: object, selection: str, home: str, away: str) -> str:
    """Plain Polish text of a canonical leg, e.g. ``Gole: powyżej 2,5`` or ``Arsenal wygra``."""
    market = str(market)
    selection = str(selection)
    prefix = _PERIOD_PREFIX.get(str(period), "")
    line_text = fmt_line(line)
    word = _TOTAL_WORD.get(selection, selection)

    def handicap(team_side: str) -> str:
        value = float(line) if _finite(line) else float("nan")
        applied = value if team_side == "home" else -value
        team = home if team_side == "home" else away
        if applied == 0:
            return f"handicap {team} 0 (zwrot przy remisie)"
        return f"handicap {team} {_signed_line(applied)}"

    if market == "1x2":
        text = {"home": f"{home} wygra", "draw": "Remis", "away": f"{away} wygra"}.get(selection, selection)
    elif market == "dc":
        text = {"1x": f"{home} lub remis", "x2": f"{away} lub remis", "12": f"{home} lub {away} (bez remisu)"}.get(selection, selection)
    elif market == "dnb":
        text = {"home": f"{home} (remis = zwrot)", "away": f"{away} (remis = zwrot)"}.get(selection, selection)
    elif market == "ah":
        text = "Handicap" + handicap(selection)[len("handicap") :]
    elif market == "ou":
        text = f"Gole: {word} {line_text}"
    elif market == "team_ou_home":
        text = f"Gole {home}: {word} {line_text}"
    elif market == "team_ou_away":
        text = f"Gole {away}: {word} {line_text}"
    elif market == "btts":
        text = "Obie strzelą: " + {"yes": "tak", "no": "nie"}.get(selection, selection)
    elif market == "cs":
        text = f"Wynik dokładny {selection}"
    elif market == "corners_ou":
        text = f"Rożne: {word} {line_text}"
    elif market == "corners_home_ou":
        text = f"Rożne {home}: {word} {line_text}"
    elif market == "corners_away_ou":
        text = f"Rożne {away}: {word} {line_text}"
    elif market == "corners_ah":
        text = "Rożne, " + handicap(selection)
    elif market == "corners_1x2":
        text = "Rożne: " + {"home": f"więcej ma {home}", "draw": "po równo", "away": f"więcej ma {away}"}.get(selection, selection)
    elif market == "cards_ou":
        text = f"Kartki: {word} {line_text}"
    elif market == "cards_home_ou":
        text = f"Kartki {home}: {word} {line_text}"
    elif market == "cards_away_ou":
        text = f"Kartki {away}: {word} {line_text}"
    elif market == "cards_ah":
        text = "Kartki, " + handicap(selection)
    else:
        text = f"{market} {selection}"
    return prefix + text


class _Names:
    """Team and league names of the fixtures frame, with a fallback for unknown matches."""

    def __init__(self, fixtures: pd.DataFrame | None) -> None:
        self._rows: dict[int, Mapping[str, Any]] = {}
        if fixtures is not None and len(fixtures) and "fixture_id" in fixtures.columns:
            table = fixtures.drop_duplicates("fixture_id", keep="last")
            for record in table.to_dict(orient="records"):
                try:
                    self._rows[int(record["fixture_id"])] = record
                except (TypeError, ValueError):
                    continue

    def teams(self, fixture_id: object) -> tuple[str, str]:
        record = self._rows.get(int(fixture_id)) if _finite(fixture_id) else None
        home = str(record.get("home_team")) if record and record.get("home_team") not in (None, "") and not _is_nan(record.get("home_team")) else None
        away = str(record.get("away_team")) if record and record.get("away_team") not in (None, "") and not _is_nan(record.get("away_team")) else None
        if home is None or away is None:
            return f"Gospodarz #{int(fixture_id)}", "Gość"
        return home, away

    def match(self, fixture_id: object) -> str:
        home, away = self.teams(fixture_id)
        return f"{home} – {away}"

    def league(self, fixture_id: object) -> str:
        record = self._rows.get(int(fixture_id)) if _finite(fixture_id) else None
        if not record:
            return ""
        parts = [record.get("country"), record.get("league_name")]
        return " · ".join(str(p) for p in parts if p not in (None, "") and not _is_nan(p))


def _is_nan(value: object) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

_CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--ink:#14181f;--muted:#5a6472;--line:#dfe3e8;--accent:#0b6b57;
--accent-soft:#e2f3ee;--pos:#0b7a3b;--neg:#b3261e;--warn:#8a5a00;--warn-soft:#fdf1d8;--radius:12px}
@media (prefers-color-scheme:dark){:root{--bg:#101318;--card:#181c23;--ink:#e8ebf0;--muted:#9aa4b2;--line:#2a303a;
--accent:#4cc3a4;--accent-soft:#16302a;--pos:#5fd08a;--neg:#ff8a80;--warn:#f2c14e;--warn-soft:#2d2610}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1120px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2rem 0 .1rem}h2{font-size:1.2rem;margin:2rem 0 .6rem}h3{font-size:1rem;margin:1.2rem 0 .4rem}
.sub{color:var(--muted);margin:0}
.badge{display:inline-block;padding:.1rem .55rem;border-radius:999px;background:var(--warn-soft);color:var(--warn);font-size:.8rem;font-weight:600}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px}
.stat b{display:block;font-size:1.4rem;line-height:1.2}.stat span{color:var(--muted);font-size:.85rem}
.coupons{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px}
.coupon{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.coupon header{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:6px}
.coupon .book{font-weight:700}.coupon .odds{font-size:1.25rem;font-weight:700}
.coupon ol{margin:0;padding:0;list-style:none}
.coupon li{padding:6px 0;border-top:1px solid var(--line)}
.coupon li small{display:block;color:var(--muted)}
.leg-line{display:flex;justify-content:space-between;gap:8px}
.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 10px;margin-top:8px;padding-top:8px;border-top:1px solid var(--line);font-size:.9rem}
.metrics div span{display:block;color:var(--muted);font-size:.78rem}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:7px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th:first-child,td:first-child,td.l,th.l{text-align:left}
td.wrap{white-space:normal;min-width:220px}
th{position:sticky;top:0;background:var(--card);color:var(--muted);font-weight:600;cursor:pointer;user-select:none}
th.nosort{cursor:default}
tr:last-child td{border-bottom:none}
td small{display:block;color:var(--muted);font-size:.75rem}
.pos{color:var(--pos)}.neg{color:var(--neg)}.na{color:var(--muted)}
.tag{display:inline-block;padding:0 .4rem;border-radius:6px;background:var(--accent-soft);color:var(--accent);font-size:.75rem;font-weight:600}
.note{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 16px}
.note li{margin:.3rem 0}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:var(--radius);padding:14px 16px;color:var(--muted)}
.note.warn{background:var(--warn-soft);color:var(--ink);border-color:var(--warn);margin-bottom:10px}
details.note{margin:10px 0}details.note summary{cursor:pointer;font-weight:600}
footer{margin:2rem 0 1rem;color:var(--muted);font-size:.8rem}
@media (max-width:520px){main{padding:12px 16px}h1{font-size:1.25rem}th,td{padding:6px 8px}}
"""

_SCRIPT = """
document.querySelectorAll('table.sortable').forEach(function(t){
  t.querySelectorAll('th:not(.nosort)').forEach(function(th,i){
    th.addEventListener('click',function(){
      var b=t.tBodies[0],rows=Array.prototype.slice.call(b.rows),dir=th.dataset.dir==='asc'?-1:1;
      th.dataset.dir=dir===1?'asc':'desc';
      rows.sort(function(x,y){
        var a=x.cells[i],c=y.cells[i],p=a.dataset.v!==undefined?parseFloat(a.dataset.v):a.textContent,
            q=c.dataset.v!==undefined?parseFloat(c.dataset.v):c.textContent;
        if(typeof p==='number'&&typeof q==='number'){return (p-q)*dir}
        return String(p).localeCompare(String(q),'pl')*dir});
      rows.forEach(function(r){b.appendChild(r)});
    });
  });
});
"""


_TIER_REASON_PL = {
    "women": "kobiety",
    "youth": "młodzież",
    "friendly": "towarzyski",
    "reserve": "rezerwy / amatorzy",
    "no_pinnacle": "brak Pinnacle",
    "pinnacle_margin": "wysoka marża Pinnacle",
    "few_bookmakers": "mało bukmacherów",
    "soft_fair_price": "cena bez Pinnacle",
    "unknown": "nieznany",
}
_REGIME_PL = {"t088": "podatek 12%", "t094": "podatek 6% (τ 0,94)", "t100": "bez podatku"}
_OUTCOME_PL = {
    "win": "wygrany",
    "loss": "przegrany",
    "push": "zwrot",
    "half_win": "pół-wygrany",
    "half_loss": "pół-przegrany",
    "void": "unieważniony",
}
RECENT_SINGLES_ROWS = 150
"""Value singles of the last seven days shown on the page (the register and the CSV hold all of them)."""


def tier_reason_pl(reason: object) -> str:
    """Polish text of a ``tier_reason`` (``youth+few_bookmakers`` -> ``młodzież, mało bukmacherów``)."""
    if not isinstance(reason, str) or not reason:
        return ""
    return ", ".join(_TIER_REASON_PL.get(part, part) for part in reason.split("+"))


def regime_name(label: object) -> str:
    return _REGIME_PL.get(str(label), str(label))


def outcome_pl(outcome: object) -> str:
    return _OUTCOME_PL.get(outcome, "oczekuje") if isinstance(outcome, str) else "oczekuje"


def _outcome_class(outcome: object) -> str:
    if outcome in ("win", "half_win"):
        return "pos"
    if outcome in ("loss", "half_loss"):
        return "neg"
    return "na"


def _flag(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or len(frame) == 0 or column not in frame.columns:
        return pd.Series(False, index=frame.index if frame is not None else None, dtype=bool)
    return frame[column].fillna(False).astype(bool)


def _tier_mask(frame: pd.DataFrame, tier: str) -> pd.Series:
    if frame is None or len(frame) == 0:
        return pd.Series(dtype=bool)
    if "tier" not in frame.columns:
        return pd.Series(tier == "liquid", index=frame.index)
    return frame["tier"].fillna("thin") == tier


# --------------------------------------------------------------------------- #
# (a) status box
# --------------------------------------------------------------------------- #


def _status_box(result: ScanResult, status: Mapping[str, Any] | None) -> str:
    diag = result.diagnostics
    config = result.config
    status = status or {}
    counts = status.get("counts", {}) or {}
    stamp = local_time(result.as_of)
    when = "brak czasu" if stamp is None else stamp.strftime("%d.%m.%Y, %H:%M")
    legs = result.legs
    labels = config.labels()

    def value_count(tier: str, label: str) -> int:
        if legs is None or len(legs) == 0:
            return 0
        mask = _flag(legs, "is_value") & _flag(legs, f"value_{label}") & _tier_mask(legs, tier)
        return int(mask.sum())

    fixtures_all = counts.get("fixtures", int(legs["fixture_id"].nunique()) if legs is not None and len(legs) else 0)
    cards = [
        (fmt_int(fixtures_all), "meczów z kursami"),
        (f"{diag.n_fixtures_liquid} / {diag.n_fixtures_thin}", "ocenionych: płynne / słabe"),
        (fmt_int(counts.get("quotes", diag.n_quotes)), "kursów w archiwum"),
        (fmt_int(diag.n_legs), "ocenionych kursów"),
    ]
    for label in labels:
        cards.append((f"{value_count('liquid', label)} / {value_count('thin', label)}", f"nóg z wartością, {regime_name(label)} (płynne / słabe)"))
    coupons = result.coupons
    if len(coupons.coupons):
        cards.append((str(len(coupons.proposed("liquid"))), "kuponów przy podatku 12% (płynne)"))
    else:
        cards.append(("0", "kuponów przy podatku 12% (płynne)"))
    model = status.get("model") or {}
    model_text = {
        "ok": f"działa ({model.get('fixtures_forecast', 0)} meczów)",
        "disabled": "wyłączony",
        "no_fit": "brak treningu",
        "fit_stale": "trening za stary",
        "import_error": "błąd importu",
        "error": "błąd",
        "no_fixtures": "brak meczów",
    }.get(str(model.get("status")), "nie użyto" if not model else str(model.get("status")))
    cards.append((model_text, "modele (tylko dodatek)"))
    if "api_requests" in status:
        api = status.get("api") or {}
        quota = api.get("quota_remaining")
        cards.append((str(status.get("api_requests", 0)), "zapytań do API" + (f" (zostało {fmt_int(quota)})" if quota is not None else "")))
    grid = "".join(f'<div class="stat"><b>{esc(v)}</b><span>{esc(label)}</span></div>' for v, label in cards)

    skipped = []
    for count, text in (
        (diag.n_stale, "kursów za starych (tablica bukmachera widziana zbyt dawno)"),
        (diag.n_started, "kursów meczów, które już się zaczęły albo zaczną się za chwilę"),
        (diag.n_unpriced, "kursów bez uczciwej ceny (brak Pinnacle i konsensusu)"),
        (diag.n_suspect, f"podejrzanych kursów (przewaga powyżej {fmt_pct(config.max_edge, 0)}, niepewna cena albo para kursów daleko od rynku)"),
        (diag.n_unsynced, "kursów niezsynchronizowanych z uczciwą ceną (tablice z różnych chwil dostawcy)"),
        (diag.n_reference_dropped, "kursów Pinnacle i Betfair (tylko źródło ceny, nigdy propozycja)"),
    ):
        if count:
            skipped.append(f"<li><b>{esc(fmt_int(count))}</b> {esc(text)}</li>")
    bet_types = status.get("bet_types") or {}
    excluded = bet_types.get("excluded") or []
    if excluded:
        dropped = bet_types.get("quote_rows_dropped")
        skipped.append(
            "<li><b>Wykluczone rodzaje zakładów</b> (ceny systematycznie odbiegają od Pinnacle): "
            + esc(", ".join(excluded))
            + (f"; pominięto {esc(fmt_int(dropped))} kursów" if dropped else "")
            + "</li>"
        )
    elif bet_types.get("status") == "error":
        skipped.append("<li>Sprawdzenie rodzajów zakładów nie powiodło się: nic nie wykluczono.</li>")
    no_calendar = counts.get("fixtures_without_calendar")
    if no_calendar:
        skipped.append(f"<li><b>{esc(fmt_int(no_calendar))}</b> meczów bez nazw w kalendarzu (ocenione tylko regułami danych)</li>")
    for warning in status.get("warnings") or []:
        skipped.append(f'<li class="na">{esc(warning)}</li>')
    skipped_html = (
        '<details class="note"><summary>Co pominięto i dlaczego</summary><ul>' + "".join(skipped) + "</ul></details>" if skipped else ""
    )
    return (
        "<header>"
        "<h1>Skaner wartości</h1>"
        f'<p class="sub">Stan na {esc(when)} (czas polski) <span class="badge">tryb papierowy: zakłady są tylko zapisywane</span></p>'
        f'<div class="stats">{grid}</div>'
        f"{skipped_html}"
        "</header>"
    )


# --------------------------------------------------------------------------- #
# Leg tables and coupon cards
# --------------------------------------------------------------------------- #


def _coupon_card(record: Any, legs: pd.DataFrame, names: _Names, config: Any) -> str:
    items = []
    for leg in legs.itertuples(index=False):
        home, away = names.teams(leg.fixture_id)
        label = leg_label_pl(leg.market, leg.period, leg.line, leg.selection, home, away)
        items.append(
            "<li>"
            f'<div class="leg-line"><span>{esc(label)}</span><b>{esc(fmt_num(leg.odd))}</b></div>'
            f"<small>{esc(names.match(leg.fixture_id))} · {esc(_when(leg.kickoff_utc))}"
            f" · szansa {esc(fmt_pct(_win_share(leg.p_fair, getattr(leg, 'refund_share', 0.0)), 0))}</small>"
            "</li>"
        )
    metrics = [f"<div><span>Szansa trafienia</span><b>{esc(fmt_pct(record.p_hit, 1))}</b></div>"]
    for tau in config.taus:
        label = tau_label(tau)
        ev = getattr(record, f"ev_net_{label}", float("nan"))
        lower = getattr(record, f"ev_net_lower_{label}", float("nan"))
        metrics.append(
            f'<div><span>EV, {esc(regime_label(tau))}</span><b class="{_ev_class(lower)}">{esc(fmt_pct(ev, 1, signed=True))}</b>'
            f" <small class=\"na\">dolna {esc(fmt_pct(lower, 1, signed=True))}</small></div>"
        )
    regime = getattr(record, "regime", None)
    tag = f' <span class="tag">od: {esc(regime_name(regime))}</span>' if isinstance(regime, str) else ""
    return (
        '<article class="coupon">'
        f'<header><span class="book">{esc(record.bookmaker)}{tag}</span><span class="odds">{esc(fmt_num(record.odds_total))}</span></header>'
        f"<ol>{''.join(items)}</ol>"
        f'<div class="metrics">{"".join(metrics)}</div>'
        "</article>"
    )


def _coupon_cards(result: ScanResult, frame: pd.DataFrame, names: _Names) -> str:
    config = result.config
    keep_label = tau_label(config.coupon_keep_tau)
    order = frame.sort_values([f"ev_net_lower_{keep_label}", f"ev_net_lower_{tau_label(max(config.taus))}", "p_hit"], ascending=[False, False, False], kind="mergesort")
    cards = [_coupon_card(record, result.coupons.legs_of(record.coupon_id), names, config) for record in order.head(PAGE_COUPONS).itertuples(index=False)]
    shown = min(len(order), PAGE_COUPONS)
    note = f'<p class="sub">Pokazano {shown} z {len(order)}.</p>' if len(order) > shown else ""
    return f'{note}<div class="coupons">{"".join(cards)}</div>'


def _day_heading(day: pd.Timestamp, today: pd.Timestamp) -> str:
    delta = (day.normalize() - today.normalize()).days
    if delta == 0:
        return f"Dziś, {_WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"
    if delta == 1:
        return f"Jutro, {_WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"
    return f"{_WEEKDAYS[day.weekday()].capitalize()} {day.strftime('%d.%m')}"


def _value_table(frame: pd.DataFrame, names: _Names, config: Any, *, extra: str | None = None) -> str:
    """A table of value legs; ``extra`` adds a column: ``regime`` (the strictest tax regime of value) or
    ``tier_reason`` (why the market is thin)."""
    regimes = [(tau, tau_label(tau)) for tau in config.taus]
    extra_head = {"regime": '<th class="l">Opłaca się od</th>', "tier_reason": '<th class="l">Dlaczego słaby</th>'}.get(extra or "", "")
    head = (
        '<th class="l">Godz.</th><th class="l">Mecz</th><th class="l">Rynek</th><th class="l">Bukmacher</th>'
        "<th>Kurs</th><th>Uczciwy kurs</th><th>Szansa</th><th>Przewaga</th><th>Zwrot stawki</th>"
        + "".join(f"<th>EV, {esc(regime_label(t))}</th>" for t, _ in regimes)
        + extra_head
        + '<th class="l">Źródło</th><th>Model</th>'
    )
    rows = []
    for row in frame.itertuples(index=False):
        home, away = names.teams(row.fixture_id)
        label = leg_label_pl(row.market, row.period, row.line, row.selection, home, away)
        agrees = row.model_agrees
        model = _DASH if not _finite(agrees) else ("zgodny" if float(agrees) >= 0.5 else "przeciw")
        kickoff = pd.Timestamp(row.kickoff_utc) if row.kickoff_utc is not None and not _is_nan(row.kickoff_utc) else None
        cells = [
            f'<td class="l" data-v="{esc(kickoff.timestamp() if kickoff is not None else 0)}">{esc(_clock(row.kickoff_utc))}</td>',
            f'<td class="l wrap">{esc(names.match(row.fixture_id))}<small>{esc(names.league(row.fixture_id))}</small></td>',
            f'<td class="l wrap">{esc(label)}</td>',
            f'<td class="l">{esc(row.bookmaker)}</td>',
            f'<td data-v="{float(row.odd):.4f}"><b>{esc(fmt_num(row.odd))}</b></td>',
            f'<td data-v="{float(row.fair_odd):.4f}">{esc(fmt_num(row.fair_odd))}</td>',
            f'<td data-v="{float(row.p_used):.4f}">{esc(fmt_pct(row.p_used, 1))}</td>',
            f'<td data-v="{float(row.edge_raw):.4f}" class="{_ev_class(row.edge_raw)}">{esc(fmt_pct(row.edge_raw, 1, signed=True))}</td>',
            f'<td data-v="{float(row.refund_share) if _finite(row.refund_share) else -1:.4f}">{esc(fmt_pct(row.refund_share, 0)) if _finite(row.refund_share) else "?"}</td>',
        ]
        for tau, lbl in regimes:
            ev = getattr(row, f"ev_net_{lbl}")
            lower = getattr(row, f"ev_net_lower_{lbl}")
            value = float(ev) if _finite(ev) else float("-inf")
            cells.append(
                f'<td data-v="{value if math.isfinite(value) else -9}" class="{_ev_class(lower)}">'
                f"{esc(fmt_pct(ev, 1, signed=True))}<small>dolna {esc(fmt_pct(lower, 1, signed=True))}</small></td>"
            )
        if extra == "regime":
            strictest = next((lbl for tau, lbl in sorted(regimes) if bool(getattr(row, f"value_{lbl}", False))), None)
            cells.append(f'<td class="l">{esc(regime_name(strictest) if strictest else _DASH)}</td>')
        elif extra == "tier_reason":
            cells.append(f'<td class="l wrap">{esc(tier_reason_pl(getattr(row, "tier_reason", "")))}</td>')
        cells.append(f'<td class="l"><span class="tag">{esc(row.fair_source)}</span></td>')
        cells.append(f"<td>{esc(model)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table class="sortable"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _legs_by_day(frame: pd.DataFrame, result: ScanResult, names: _Names, *, extra: str | None = None, limit: int | None = None) -> str:
    shown = frame if limit is None else frame.head(limit)
    shown = shown.copy()
    local = shown["kickoff_utc"].map(local_time)
    shown["_day"] = [stamp.normalize() if stamp is not None else pd.NaT for stamp in local]
    now_local = local_time(result.as_of) or pd.Timestamp.now(tz="UTC")
    parts = []
    if limit is not None and len(frame) > limit:
        parts.append(f'<p class="sub">Pokazano {limit} z {len(frame)} (wszystkie są w pliku CSV).</p>')
    for day, group in shown.groupby("_day", sort=True, dropna=False):
        heading = "Bez daty" if pd.isna(day) else _day_heading(day, now_local)
        parts.append(f"<h3>{esc(heading)} <span class=\"na\">({len(group)})</span></h3>")
        order = "rank" if "rank" in group.columns else "kickoff_utc"
        parts.append(_value_table(group.sort_values(order), names, result.config, extra=extra))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# (b) - (e)
# --------------------------------------------------------------------------- #


def _singles_section(result: ScanResult, names: _Names) -> str:
    config = result.config
    keep = tau_label(config.coupon_keep_tau)
    picks = result.picks
    title = "<h2>Single opłacalne same w sobie przy podatku 12%</h2>"
    chosen = picks[_tier_mask(picks, "liquid") & _flag(picks, f"value_{keep}")] if picks is not None and len(picks) else pd.DataFrame()
    if len(chosen) == 0:
        return title + (
            '<div class="empty">Brak. Żaden pojedynczy kurs na płynnym rynku nie jest opłacalny po podatku 12%. '
            "Pojedynczy zakład potrzebuje przewagi co najmniej 13,6% nad uczciwą ceną, a przewagi powyżej "
            f"{esc(fmt_pct(config.max_edge, 0))} traktujemy jako podejrzane (prawie zawsze błąd danych), więc przy tych "
            "ustawieniach singiel prawie nigdy nie przejdzie. Po podatku mogą się opłacać tylko kupony.</div>"
        )
    return title + '<p class="sub">Płynne rynki, dolna granica EV dodatnia przy pełnym podatku 12%.</p>' + _legs_by_day(chosen, result, names)


def _coupons_full_tax_section(result: ScanResult, names: _Names) -> str:
    config = result.config
    title = "<h2>Kupony 2–3 nogi przy podatku 12%</h2>"
    proposed = result.coupons.proposed("liquid") if len(result.coupons.coupons) else pd.DataFrame()
    if len(proposed) == 0:
        return title + (
            '<div class="empty"><b>Dziś nic nie opłaca się po podatku 12%.</b> Żaden kupon z płynnych rynków '
            "(2–3 nogi z różnych meczów u jednego bukmachera) nie ma dodatniej dolnej granicy EV przy pełnym podatku "
            f"i szansy trafienia co najmniej {esc(fmt_pct(config.min_hit, 0))}. Nic nie zostało zaproponowane.</div>"
        )
    passing = result.coupons.by_tier.get("liquid", {}).get("passing", len(proposed)) if result.coupons.by_tier else len(proposed)
    return (
        title
        + f'<p class="sub">Płynne rynki. {len(proposed)} proponowanych z {fmt_int(passing)} kuponów spełniających warunki. '
        "EV to oczekiwany zysk na złotówkę stawki po podatku; „dolna” to wartość przy ostrożnym oszacowaniu szans.</p>"
        + _coupon_cards(result, proposed, names)
    )


def _lower_tax_section(result: ScanResult, names: _Names) -> str:
    config = result.config
    keep = tau_label(config.coupon_keep_tau)
    title = "<h2>Tylko przy niższym podatku (np. promocje, Betclic)</h2>"
    note = (
        '<p class="sub"><span class="badge">nie opłaca się przy podatku 12%</span> Płynne rynki. Te nogi i kupony mają '
        "dodatnią dolną granicę EV dopiero przy podatku 6% (τ 0,94) albo bez podatku. Zagrane ze zwykłym podatkiem 12% "
        "tracą.</p>"
    )
    picks = result.picks
    legs = pd.DataFrame()
    if picks is not None and len(picks):
        lower = pd.Series(False, index=picks.index)
        for tau in config.taus:
            if tau > config.coupon_keep_tau:
                lower |= _flag(picks, f"value_{tau_label(tau)}")
        legs = picks[_tier_mask(picks, "liquid") & lower & ~_flag(picks, f"value_{keep}")]
    coupons = result.coupons.proposed("liquid", lower_tax=True) if len(result.coupons.coupons) else pd.DataFrame()
    if len(legs) == 0 and len(coupons) == 0:
        return title + '<div class="empty">Nic: także przy niższym podatku żaden kurs płynnego rynku nie przebija uczciwej ceny z zapasem.</div>'
    parts = [title, note]
    if len(coupons):
        parts.append(f"<h3>Kupony ({len(coupons)})</h3>")
        parts.append(_coupon_cards(result, coupons, names))
    if len(legs):
        parts.append(f"<h3>Nogi z wartością ({len(legs)})</h3>")
        parts.append(_legs_by_day(legs, result, names, extra="regime", limit=config.top_value_rows))
    return "".join(parts)


def _thin_section(result: ScanResult, names: _Names) -> str:
    config = result.config
    title = "<h2>Eksperymentalne: rynki słabe</h2>"
    note = (
        '<div class="note warn"><b>Niewiarygodne.</b> Mecze towarzyskie, kobiece, młodzieżowe, rezerwy i niższe ligi '
        "oraz mecze, w których Pinnacle ma wysoką marżę, nie wystawia 1X2 albo kursy podaje niewielu bukmacherów. "
        "Cena Pinnacle nie jest tu dobrym oszacowaniem prawdy, więc „przewaga” to częściej błąd niż okazja. "
        "Pokazane tylko do obserwacji; wyniki papierowe liczone są osobno.</div>"
    )
    picks = result.picks
    legs = picks[_tier_mask(picks, "thin")] if picks is not None and len(picks) else pd.DataFrame()
    coupons = pd.DataFrame()
    if len(result.coupons.coupons):
        coupons = pd.concat([result.coupons.proposed("thin"), result.coupons.proposed("thin", lower_tax=True)])
    if len(legs) == 0 and len(coupons) == 0:
        return title + '<div class="empty">Na słabych rynkach nie ma dziś nóg z wartością.</div>'
    parts = [title, note]
    if len(coupons):
        parts.append(f"<h3>Kupony ({len(coupons)})</h3>")
        parts.append(_coupon_cards(result, coupons, names))
    if len(legs):
        parts.append(f"<h3>Nogi z wartością ({len(legs)})</h3>")
        parts.append(_legs_by_day(legs, result, names, extra="tier_reason", limit=config.top_value_rows))
    return "".join(parts)


def _model_only_section(result: ScanResult, names: _Names) -> str:
    frame = result.model_only
    if frame is None or len(frame) == 0:
        return ""
    rows = []
    for row in frame.head(10).itertuples(index=False):
        home, away = names.teams(row.fixture_id)
        rows.append(
            "<tr>"
            f'<td class="l">{esc(_when(row.kickoff_utc))}</td>'
            f'<td class="l wrap">{esc(names.match(row.fixture_id))}</td>'
            f'<td class="l wrap">{esc(leg_label_pl(row.market, row.period, row.line, row.selection, home, away))}</td>'
            f'<td class="l">{esc(row.bookmaker)}</td>'
            f"<td>{esc(fmt_num(row.odd))}</td><td>{esc(fmt_pct(row.p_used, 1))}</td>"
            "</tr>"
        )
    return (
        "<h2>Tylko model (poza kuponami)</h2>"
        '<p class="sub">Dla tych rynków nie ma ceny Pinnacle ani innej ceny rynkowej, więc szansę podaje wyłącznie model. '
        "Model jest gorszy od rynku, dlatego te nogi są tylko zapisywane do badań i nigdy nie trafiają do kuponów.</p>"
        '<div class="scroll"><table><thead><tr><th class="l">Godz.</th><th class="l">Mecz</th><th class="l">Rynek</th>'
        '<th class="l">Bukmacher</th><th>Kurs</th><th>Szansa modelu</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


# --------------------------------------------------------------------------- #
# (f) paper results
# --------------------------------------------------------------------------- #


def _results_table(results: pd.DataFrame, tier: str) -> str:
    rows = []
    frame = results[results["tier"] == tier]
    for row in frame.to_dict(orient="records"):
        kind = "pojedyncze" if row["kind"] == "single" else "kupony"
        rows.append(
            "<tr>"
            f'<td class="l">{esc(regime_name(row["regime"]))}</td>'
            f'<td class="l">{esc(kind)}</td>'
            f'<td class="l">ostatnie {int(row["window_days"])} dni</td>'
            f'<td>{int(row["n_bets"])}</td>'
            f'<td>{int(row["n_settled"])}</td>'
            f'<td>{esc(fmt_pct(row["hit_rate"], 1))}</td>'
            f'<td class="{_ev_class(row["yield"])}">{esc(fmt_pct(row["yield"], 1, signed=True))}</td>'
            f'<td>{int(row["n_clv"])}</td>'
            f'<td class="{_ev_class(row["median_clv_raw"])}">{esc(fmt_pct(row["median_clv_raw"], 2, signed=True))}</td>'
            f'<td>{esc(fmt_pct(row["share_beaten"], 0))}</td>'
            "</tr>"
        )
    head = (
        '<th class="l">Podatek</th><th class="l">Rodzaj</th><th class="l">Okres</th><th>Zakładów</th><th>Rozliczone</th>'
        "<th>Trafienia</th><th>Zysk na stawkę</th><th>CLV: liczba</th><th>Mediana CLV</th><th>Pobite zamknięcia</th>"
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _recent_tables(recent: Mapping[str, pd.DataFrame], names: _Names, config: Any) -> str:
    keep = tau_label(config.coupon_keep_tau)
    coupons = recent.get("coupons")
    coupon_legs = recent.get("coupon_legs")
    singles = recent.get("singles")
    parts = ["<h3>Ostatnie 7 dni: propozycje i ich wynik</h3>"]
    if (coupons is None or len(coupons) == 0) and (singles is None or len(singles) == 0):
        parts.append('<div class="empty">W ostatnich 7 dniach ta konfiguracja niczego nie zaproponowała.</div>')
        return "".join(parts)
    if coupons is not None and len(coupons):
        rows = []
        for record in coupons.to_dict(orient="records"):
            legs = coupon_legs[coupon_legs["coupon_id"] == record["coupon_id"]] if coupon_legs is not None and len(coupon_legs) else pd.DataFrame()
            items = []
            for leg in legs.sort_values("leg_no").to_dict(orient="records"):
                home, away = names.teams(leg["fixture_id"])
                label = leg_label_pl(leg["market"], leg["period"], leg["line"], leg["selection"], home, away)
                items.append(
                    f'<div>{esc(names.match(leg["fixture_id"]))}: {esc(label)} @ {esc(fmt_num(leg["odd"]))} '
                    f'<span class="{_outcome_class(leg.get("outcome"))}">({esc(outcome_pl(leg.get("outcome")))})</span></div>'
                )
            outcome = record.get("outcome")
            rows.append(
                "<tr>"
                f'<td class="l">{esc(_when(record.get("kickoff_last")))}</td>'
                f'<td class="l">{esc("płynny" if record.get("tier") == "liquid" else "słaby")}</td>'
                f'<td class="l">{esc(regime_name(record.get("regime") or keep))}</td>'
                f'<td class="l">{esc(record.get("bookmaker"))}</td>'
                f'<td class="l wrap">{"".join(items)}</td>'
                f'<td>{esc(fmt_num(record.get("odds_total")))}</td>'
                f'<td class="{_outcome_class(outcome)}"><b>{esc(outcome_pl(outcome))}</b></td>'
                f'<td class="{_ev_class(record.get(f"profit_{keep}"))}">{esc(fmt_pct(record.get(f"profit_{keep}"), 1, signed=True))}</td>'
                "</tr>"
            )
        head = (
            '<th class="l">Ostatni mecz</th><th class="l">Rynek</th><th class="l">Opłacalny od</th><th class="l">Bukmacher</th>'
            '<th class="l">Nogi</th><th>Kurs</th><th>Wynik</th><th>Zysk, podatek 12%</th>'
        )
        parts.append(f"<p class=\"sub\">Kupony ({len(coupons)}):</p>")
        parts.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    if singles is not None and len(singles):
        rows = []
        for record in singles.head(RECENT_SINGLES_ROWS).to_dict(orient="records"):
            home, away = names.teams(record["fixture_id"])
            label = leg_label_pl(record["market"], record["period"], record["line"], record["selection"], home, away)
            strictest = next((tau_label(t) for t in sorted(config.taus) if bool(record.get(f"value_{tau_label(t)}"))), None)
            outcome = record.get("outcome")
            rows.append(
                "<tr>"
                f'<td class="l">{esc(_when(record.get("kickoff_utc")))}</td>'
                f'<td class="l">{esc("płynny" if record.get("tier") == "liquid" else "słaby")}</td>'
                f'<td class="l wrap">{esc(names.match(record["fixture_id"]))}<small>{esc(label)}</small></td>'
                f'<td class="l">{esc(record.get("bookmaker"))}</td>'
                f'<td>{esc(fmt_num(record.get("odd")))}</td>'
                f'<td class="l">{esc(regime_name(strictest) if strictest else _DASH)}</td>'
                f'<td class="{_outcome_class(outcome)}"><b>{esc(outcome_pl(outcome))}</b></td>'
                f'<td class="{_ev_class(record.get(f"profit_{keep}"))}">{esc(fmt_pct(record.get(f"profit_{keep}"), 1, signed=True))}</td>'
                "</tr>"
            )
        head = (
            '<th class="l">Mecz</th><th class="l">Rynek</th><th class="l">Zakład</th><th class="l">Bukmacher</th><th>Kurs</th>'
            '<th class="l">Opłacalny od</th><th>Wynik</th><th>Zysk, podatek 12%</th>'
        )
        more = f" (pokazano {RECENT_SINGLES_ROWS} najnowszych)" if len(singles) > RECENT_SINGLES_ROWS else ""
        parts.append(f'<p class="sub">Pojedyncze nogi z wartością ({len(singles)}){more}:</p>')
        parts.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    return "".join(parts)


def _paper_section(
    result: ScanResult,
    summary: pd.DataFrame | None,
    results: pd.DataFrame | None = None,
    recent: Mapping[str, pd.DataFrame] | None = None,
    names: _Names | None = None,
) -> str:
    config = result.config
    names = names or _Names(None)
    title = "<h2>Wyniki papierowe</h2>"
    has_summary = summary is not None and len(summary) and int(summary["n_bets"].sum()) > 0
    has_results = results is not None and len(results) and int(results["n_bets"].sum()) > 0
    parts = [title]
    if not has_summary and not has_results:
        parts.append(
            '<div class="empty">Jeszcze nic do podsumowania: zapisane nogi i kupony czekają na mecze. '
            "Wyniki pojawią się po rozegraniu pierwszych spotkań.</div>"
        )
    else:
        parts.append(
            '<p class="sub">Liczone od pierwszego momentu, w którym noga (albo kupon) stała się propozycją, po kursie z tej chwili, '
            f"tylko dla bieżącej konfiguracji ({esc(config.config_id)}): zmiana ustawień zaczyna nowe liczenie. Każdy wiersz to zakłady, "
            "które były opłacalne przy danym podatku, z zyskiem przy tym podatku. Mały wynik to jeszcze nie wniosek: potrzeba setek "
            "rozliczonych zakładów.</p>"
        )
        if has_results:
            for tier, name in (("liquid", "Rynki płynne"), ("thin", "Rynki słabe (eksperyment)")):
                parts.append(f"<h3>{esc(name)}</h3>")
                parts.append(_results_table(results, tier))
        else:
            parts.append(_legacy_summary(summary, config))
    if recent is not None:
        parts.append(_recent_tables(recent, names, config))
    return "".join(parts)


def _legacy_summary(summary: pd.DataFrame, config: Any) -> str:
    labels = [tau_label(t) for t in config.taus]
    head = (
        '<th class="l">Okres</th><th class="l">Rodzaj</th><th>Zakładów</th><th>Rozliczone</th><th>Trafienia</th>'
        + "".join(f"<th>Zysk na stawkę, {esc(regime_label(t))}</th>" for t in config.taus)
        + "<th>CLV: liczba</th><th>Mediana CLV (kurs vs zamknięcie)</th><th>Pobite zamknięcia</th>"
    )
    rows = []
    for row in summary.to_dict(orient="records"):
        kind = "pojedyncze" if row["kind"] == "single" else "kupony"
        cells = [
            f'<td class="l">ostatnie {int(row["window_days"])} dni</td>',
            f'<td class="l">{esc(kind)}</td>',
            f'<td>{int(row["n_bets"])}</td>',
            f'<td>{int(row["n_settled"])}</td>',
            f'<td>{esc(fmt_pct(row["hit_rate"], 1))}</td>',
        ]
        for label in labels:
            value = row.get(f"yield_{label}")
            cells.append(f'<td class="{_ev_class(value)}">{esc(fmt_pct(value, 1, signed=True))}</td>')
        cells.append(f'<td>{int(row["n_clv"])}</td>')
        cells.append(f'<td class="{_ev_class(row["median_clv_raw"])}">{esc(fmt_pct(row["median_clv_raw"], 2, signed=True))}</td>')
        cells.append(f'<td>{esc(fmt_pct(row["share_beaten"], 0))}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _howto(config: Any) -> str:
    return (
        "<h2>Jak czytać</h2>"
        '<div class="note"><ul>'
        "<li><b>Wartość to cena kontra uczciwa szansa.</b> Uczciwą szansę bierzemy z rynku (Pinnacle po odjęciu marży, "
        "a gdy go brak: Betfair albo zgodna średnia bukmacherów). Noga ma wartość, gdy kurs u bukmachera jest wyższy od uczciwego "
        f"nawet po odjęciu marginesu ostrożności ({fmt_num(config.z, 3)} błędu standardowego).</li>"
        "<li><b>Kupon liczy podatek raz.</b> Podatek 12% od stawki płaci się od całego kuponu, więc kilka nóg z niewielką przewagą "
        "może dać dodatnie EV, choć pojedynczy zakład na takiej nodze traci po podatku.</li>"
        "<li><b>Co znaczą klasy rynków.</b> <i>Płynny</i>: mecz ligi lub turnieju, w którym Pinnacle przyjmuje duże stawki "
        f"(marża 1X2 Pinnacle najwyżej {fmt_num((config.tier_max_pinnacle_overround - 1) * 100, 0)}%, co najmniej "
        f"{config.tier_min_bookmakers} bukmacherów, nie kobiecy, nie młodzieżowy, nie towarzyski, nie rezerwy). Tylko te mecze "
        "trafiają do głównych sekcji. <i>Słaby</i>: wszystko inne; tam cena Pinnacle jest zgadywaniem, więc propozycje są tylko "
        "eksperymentem.</li>"
        "<li><b>Podejrzane i niezsynchronizowane kursy są pomijane.</b> Przewaga większa niż "
        f"{fmt_pct(config.max_edge, 0)}, bardzo niepewna uczciwa cena, cała para kursów bukmachera daleko od ceny rynkowej "
        f"(przesunięcie o więcej niż {fmt_num(config.max_book_shift * 100, 0)} p.p.) albo kurs z innej chwili dostawcy niż uczciwa "
        f"cena (różnica ponad {fmt_num(config.sync_window_minutes, 0)} min) to zwykle błąd danych lub kurs, którego nie da się "
        "zagrać. Takie nogi trafiają tylko do zapisu badawczego. Rodzaje zakładów, których ceny systematycznie odbiegają od "
        "Pinnacle, są wykluczane w całości (lista w polu statusu).</li>"
        "<li><b>Zwrot stawki.</b> Na liniach całkowitych i ćwierćlinach (handicapy, sumy) stawka może wrócić przy trafieniu w linię. "
        "EV i szansa trafienia są wtedy skorygowane o szansę zwrotu odczytaną z sąsiednich linii; nogi, dla których nie da się jej ustalić, "
        "nie wchodzą do kuponów.</li>"
        "<li><b>Mecze odwołane i przełożone.</b> Nogi meczów odwołanych, przerwanych i walkowerów są unieważniane (zwrot stawki) od razu, "
        "przełożonych i zawieszonych po 48 godzinach od planowanego początku. Unieważniona noga wypada z kuponu jak zwrot.</li>"
        "<li><b>Model statystyczny jest tylko dodatkiem.</b> Nasze modele wypadły gorzej niż Pinnacle, więc nigdy nie zmieniają ceny rynkowej.</li>"
        "<li><b>To tryb papierowy.</b> Nic tu nie jest zakładem: skaner zapisuje kursy, propozycje i wyniki, żeby później ocenić, czy przewaga jest prawdziwa.</li>"
        "<li><b>Zagraniczni bukmacherzy to zastępstwo.</b> Do czasu, aż będą kursy polskich bukmacherów, propozycje dotyczą zagranicznych "
        "domów. Pinnacle i Betfair służą tylko jako źródło ceny i nigdy nie są proponowane.</li>"
        "</ul></div>"
    )


def render_page(
    result: ScanResult,
    fixtures: pd.DataFrame | None = None,
    summary: pd.DataFrame | None = None,
    *,
    status: Mapping[str, Any] | None = None,
    results: pd.DataFrame | None = None,
    recent: Mapping[str, pd.DataFrame] | None = None,
) -> str:
    """The full page as text.  Works with empty results (every section then explains that it is empty).

    Sections in order: (a) the status box, (b) singles that pay the 12% tax (liquid markets), (c) coupons that pay
    the 12% tax (liquid), (d) legs and coupons that pay only under a lower tax (liquid), (e) the thin markets as an
    experiment, (f) paper results by tier and regime and the proposals of the last seven days with their outcome;
    then the model-only legs and how to read the page.  ``status`` carries what the pipeline did (counts, model,
    API, excluded bet types, warnings), ``results`` is :meth:`ScannerRegister.paper_results`, ``recent``
    :meth:`ScannerRegister.recent_bets` and ``summary`` the older :meth:`ScannerRegister.paper_summary` (shown
    only when ``results`` is absent).
    """
    names = _Names(fixtures)
    body = "".join(
        [
            _status_box(result, status),
            _singles_section(result, names),
            _coupons_full_tax_section(result, names),
            _lower_tax_section(result, names),
            _thin_section(result, names),
            _paper_section(result, summary, results, recent, names),
            _model_only_section(result, names),
            _howto(result.config),
            (
                f"<footer>Skan {esc(result.scan_id)} · konfiguracja {esc(result.config.config_id)} · "
                "kursy: API-Football, uczciwe szanse: Pinnacle bez marży.</footer>"
            ),
        ]
    )
    return (
        "<!doctype html>"
        '<html lang="pl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Skaner wartości</title>"
        f"<style>{_CSS}</style></head><body><main>{body}</main>"
        f"<script>{_SCRIPT}</script></body></html>"
    )


def write_page(page: str, out_dir: str | Path, as_of: object) -> list[Path]:
    """Write ``latest.html`` and ``html/YYYY-MM-DD.html`` (date in Polish time) atomically."""
    out = Path(out_dir)
    stamp = local_time(as_of) or pd.Timestamp.now(tz="UTC")
    targets = [out / "latest.html", out / "html" / f"{stamp.strftime('%Y-%m-%d')}.html"]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as tmp:
                tmp.write(page)
            os.replace(tmp_name, target)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    return targets
