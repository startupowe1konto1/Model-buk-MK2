"""Static Polish HTML page of the scanner: best coupons, value legs, paper results.

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


def _header(result: ScanResult, names: _Names, n_matches: int) -> str:
    diag = result.diagnostics
    stamp = local_time(result.as_of)
    when = "brak czasu" if stamp is None else stamp.strftime("%d.%m.%Y, %H:%M")
    coupons = result.coupons
    n_top = int(len(coupons.top)) if len(coupons.coupons) else 0
    stats = [
        (str(n_matches), "meczów ocenionych"),
        (f"{diag.n_bookmakers}", "bukmacherów (bez Pinnacle i Betfair)"),
        (fmt_int(diag.n_legs), "ocenionych kursów"),
        (fmt_int(diag.n_value), "nóg z wartością"),
        (f"{n_top}", "proponowanych kuponów"),
    ]
    if diag.n_suspect:
        stats.append((f"{diag.n_suspect}", "podejrzanych kursów (pominięte)"))
    cards = "".join(f'<div class="stat"><b>{esc(v)}</b><span>{esc(label)}</span></div>' for v, label in stats)
    return (
        "<header>"
        "<h1>Skaner wartości</h1>"
        f'<p class="sub">Stan na {esc(when)} (czas polski) <span class="badge">tryb papierowy: zakłady są tylko zapisywane</span></p>'
        f'<div class="stats">{cards}</div>'
        "</header>"
    )


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
    return (
        '<article class="coupon">'
        f'<header><span class="book">{esc(record.bookmaker)}</span><span class="odds">{esc(fmt_num(record.odds_total))}</span></header>'
        f"<ol>{''.join(items)}</ol>"
        f'<div class="metrics">{"".join(metrics)}</div>'
        "</article>"
    )


def _coupons_section(result: ScanResult, names: _Names) -> str:
    coupon_set = result.coupons
    config = result.config
    if len(coupon_set.coupons) == 0:
        keep = regime_label(config.coupon_keep_tau)
        return (
            "<h2>Najlepsze kupony</h2>"
            '<div class="empty">Brak kuponów, które spełniają warunki: 2–3 nogi z różnych meczów u jednego bukmachera, '
            f"dodatnie EV dolnej granicy przy założeniu „{esc(keep)}” i szansa trafienia co najmniej {esc(fmt_pct(config.min_hit, 0))}. "
            "Nic nie zostało zaproponowane.</div>"
        )
    keep_label = tau_label(config.coupon_keep_tau)
    top = coupon_set.top.sort_values([f"ev_net_lower_{keep_label}", "p_hit"], ascending=[False, False], kind="mergesort")
    cards = []
    for record in top.head(PAGE_COUPONS).itertuples(index=False):
        legs = coupon_set.legs_of(record.coupon_id)
        cards.append(_coupon_card(record, legs, names, config))
    shown = min(len(top), PAGE_COUPONS)
    return (
        "<h2>Najlepsze kupony</h2>"
        f'<p class="sub">{shown} z {fmt_int(coupon_set.n_passing)} kuponów spełniających warunki. '
        "EV to oczekiwany zysk na złotówkę stawki po podatku; „dolna” to wartość przy ostrożnym oszacowaniu szans.</p>"
        f'<div class="coupons">{"".join(cards)}</div>'
    )


def _day_heading(day: pd.Timestamp, today: pd.Timestamp) -> str:
    delta = (day.normalize() - today.normalize()).days
    if delta == 0:
        return f"Dziś, {_WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"
    if delta == 1:
        return f"Jutro, {_WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"
    return f"{_WEEKDAYS[day.weekday()].capitalize()} {day.strftime('%d.%m')}"


def _value_table(frame: pd.DataFrame, names: _Names, config: Any) -> str:
    regimes = [(tau, tau_label(tau)) for tau in config.taus]
    head = (
        '<th class="l">Godz.</th><th class="l">Mecz</th><th class="l">Rynek</th><th class="l">Bukmacher</th>'
        "<th>Kurs</th><th>Uczciwy kurs</th><th>Szansa</th><th>Przewaga</th><th>Zwrot stawki</th>"
        + "".join(f"<th>EV, {esc(regime_label(t))}</th>" for t, _ in regimes)
        + '<th class="l">Źródło</th><th>Model</th>'
    )
    rows = []
    for row in frame.itertuples(index=False):
        home, away = names.teams(row.fixture_id)
        label = leg_label_pl(row.market, row.period, row.line, row.selection, home, away)
        agrees = row.model_agrees
        model = _DASH if not _finite(agrees) else ("zgodny" if float(agrees) >= 0.5 else "przeciw")
        cells = [
            f'<td class="l" data-v="{esc(row.kickoff_utc.timestamp() if hasattr(row.kickoff_utc, "timestamp") else 0)}">{esc(_clock(row.kickoff_utc))}</td>',
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
        cells.append(f'<td class="l"><span class="tag">{esc(row.fair_source)}</span></td>')
        cells.append(f"<td>{esc(model)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table class="sortable"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _value_section(result: ScanResult, names: _Names) -> str:
    picks = result.picks
    config = result.config
    if picks is None or len(picks) == 0:
        return (
            "<h2>Nogi z wartością</h2>"
            '<div class="empty">W tym skanie żaden kurs nie przebił uczciwej ceny z wystarczającą pewnością.</div>'
        )
    shown = picks.head(config.top_value_rows).copy()
    local = shown["kickoff_utc"].map(local_time)
    shown["_day"] = [stamp.normalize() if stamp is not None else pd.NaT for stamp in local]
    now_local = local_time(result.as_of) or pd.Timestamp.now(tz="UTC")
    parts = [
        "<h2>Nogi z wartością</h2>",
        f'<p class="sub">Kurs u bukmachera wyższy od uczciwego kursu z rynku, także po odjęciu marginesu ostrożności. '
        f"Pokazano {len(shown)} z {len(picks)}, jedna noga na mecz, bukmachera i rodzaj rynku. Kliknij nagłówek, żeby posortować.</p>",
    ]
    for day, group in shown.groupby("_day", sort=True, dropna=False):
        heading = "Bez daty" if pd.isna(day) else _day_heading(day, now_local)
        parts.append(f"<h3>{esc(heading)} <span class=\"na\">({len(group)})</span></h3>")
        parts.append(_value_table(group.sort_values("rank"), names, config))
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


def _paper_section(result: ScanResult, summary: pd.DataFrame | None) -> str:
    config = result.config
    title = "<h2>Wyniki papierowe</h2>"
    if summary is None or len(summary) == 0 or int(summary["n_bets"].sum()) == 0:
        return (
            title
            + '<div class="empty">Jeszcze nic do podsumowania: zapisane nogi i kupony czekają na mecze. '
            "Wyniki pojawią się po rozegraniu pierwszych spotkań.</div>"
        )
    labels = [tau_label(t) for t in config.taus]
    head = (
        '<th class="l">Okres</th><th class="l">Rodzaj</th><th>Zakładów</th><th>Rozliczone</th><th>Trafienia</th>'
        + "".join(f"<th>Zysk na stawkę, {esc(regime_label(t))}</th>" for t in config.taus)
        + "<th>CLV: liczba</th><th>Mediana CLV (kurs vs zamknięcie)</th>"
        + f"<th>Mediana CLV po podatku, {esc(regime_label(config.taus[0]))}</th><th>Pobite zamknięcia</th>"
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
        net = row.get(f"median_clv_net_{labels[0]}")
        cells.append(f'<td class="{_ev_class(net)}">{esc(fmt_pct(net, 2, signed=True))}</td>')
        cells.append(f'<td>{esc(fmt_pct(row["share_beaten"], 0))}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        title
        + '<p class="sub">Liczone od pierwszego momentu, w którym noga (albo kupon) stała się propozycją, po kursie z tej chwili, '
        f"tylko dla bieżącej konfiguracji ({esc(config.config_id)}): zmiana ustawień zaczyna nowe liczenie. "
        "Mały wynik to jeszcze nie wniosek: potrzeba setek rozliczonych zakładów.</p>"
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _howto(config: Any) -> str:
    return (
        "<h2>Jak czytać</h2>"
        '<div class="note"><ul>'
        "<li><b>Wartość to cena kontra uczciwa szansa.</b> Uczciwą szansę bierzemy z rynku (Pinnacle po odjęciu marży, "
        "a gdy go brak: Betfair albo zgodna średnia bukmacherów). Noga ma wartość, gdy kurs u bukmachera jest wyższy od uczciwego "
        f"nawet po odjęciu marginesu ostrożności ({fmt_num(config.z, 3)} błędu standardowego).</li>"
        "<li><b>Kupon liczy podatek raz.</b> Podatek 12% od stawki płaci się od całego kuponu, więc kilka nóg z niewielką przewagą "
        "może dać dodatnie EV, choć pojedynczy zakład na takiej nodze traci po podatku.</li>"
        "<li><b>Podejrzane kursy są pomijane.</b> Przewaga większa niż "
        f"{fmt_pct(config.max_edge, 0)}, bardzo niepewna uczciwa cena albo cała para kursów bukmachera leżąca daleko od ceny rynkowej "
        f"(przesunięcie o więcej niż {fmt_num(config.max_book_shift * 100, 0)} p.p.: stara tablica albo błędnie opisana linia) "
        "to zwykle błąd danych lub kurs, którego nie da się zagrać. Takie nogi trafiają tylko do zapisu badawczego.</li>"
        "<li><b>Zwrot stawki.</b> Na liniach całkowitych i ćwierćlinach (handicapy, sumy) stawka może wrócić przy trafieniu w linię. "
        "EV i szansa trafienia są wtedy skorygowane o szansę zwrotu odczytaną z sąsiednich linii; nogi, dla których nie da się jej ustalić, "
        "nie wchodzą do kuponów.</li>"
        "<li><b>Model statystyczny jest tylko dodatkiem.</b> Nasze modele wypadły gorzej niż Pinnacle, więc nigdy nie zmieniają ceny rynkowej.</li>"
        "<li><b>To tryb papierowy.</b> Nic tu nie jest zakładem: skaner zapisuje kursy, propozycje i wyniki, żeby później ocenić, czy przewaga jest prawdziwa.</li>"
        "<li><b>Zagraniczni bukmacherzy to zastępstwo.</b> Do czasu, aż będą kursy polskich bukmacherów, propozycje dotyczą zagranicznych "
        "domów. Pinnacle i Betfair służą tylko jako źródło ceny i nigdy nie są proponowane.</li>"
        "</ul></div>"
    )


def render_page(result: ScanResult, fixtures: pd.DataFrame | None = None, summary: pd.DataFrame | None = None) -> str:
    """The full page as text.  Works with empty results (every section then explains that it is empty)."""
    names = _Names(fixtures)
    legs = result.legs
    n_matches = int(legs["fixture_id"].nunique()) if legs is not None and len(legs) else 0
    body = "".join(
        [
            _header(result, names, n_matches),
            _coupons_section(result, names),
            _value_section(result, names),
            _model_only_section(result, names),
            _paper_section(result, summary),
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
