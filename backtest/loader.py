"""Wczytywanie meczow z football-data.co.uk z kursami Pinnacle open i close.

Zwraca 86 024 mecze. Audyt podaje 86 025 wierszy z kompletem kursow i ma racje -
roznica to jeden mecz przerwany (Panathinaikos vs Olympiakos, 17/03/2019, 0:3
w przerwie), ktory nie ma wyniku koncowego. Mecz przerwany to VOID, zwrot stawki,
wiec nie jest obserwacja backtestu. NIE domykac tej luki fallbackiem na HTR -
stan z przerwy nie jest wynikiem meczu.

Pulapka, ktora kosztowala godzine: pliki maja niejednolite kodowanie. Czesc jest
UTF-8 z BOM, czesc latin-1. Czytanie wszystkiego jako utf-8 wywala sie na 0xf6
(niemieckie o umlaut w nazwiskach sedziow). Czytanie wszystkiego jako latin-1
zamienia BOM w widoczne "i>>?" i psuje nazwe pierwszej kolumny, przez co Div
robi sie pusty w 11 658 wierszach. Trzeba sprawdzic BOM na bajtach PRZED
dekodowaniem.
"""
import csv
import datetime
import glob
import io
import os
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

DEFAULT_DATA_ROOT = (
    "/Users/wojciechrudnicki/Code_projects/hazard_saas/"
    "data/raw/football-data-uk/main"
)

# Kolumny Pinnacle. PS* to zdjecie z budowy pliku tygodniowego (zwykle piatek),
# PSC* to zamkniecie. PS* NIE jest prawdziwym otwarciem rynku - patrz Global
# Constraints w planie.
ODDS_OPEN = ("PSH", "PSD", "PSA")
ODDS_CLOSE = ("PSCH", "PSCD", "PSCA")
REQUIRED = ODDS_OPEN + ODDS_CLOSE

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d")


class Match(NamedTuple):
    div: str
    date: datetime.date
    home: str
    away: str
    ftr: str
    o_open: Tuple[float, float, float]
    o_close: Tuple[float, float, float]


def read_rows(path: str) -> Iterator[Dict[str, str]]:
    """Czyta CSV odporny na mieszane kodowania i BOM."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    # Pas bezpieczenstwa: gdyby BOM przeszedl jako znak U+FEFF.
    if text.startswith("﻿"):
        text = text[1:]
    for row in csv.DictReader(io.StringIO(text)):
        yield row


def parse_date(s: str) -> Optional[datetime.date]:
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def season_of(d: datetime.date) -> str:
    """Sezon pilkarski zaczyna sie 1 lipca. 2014-07-25 -> '2014/15'."""
    start = d.year if d.month >= 7 else d.year - 1
    return "{0}/{1:02d}".format(start, (start + 1) % 100)


def _odds(row: Dict[str, str], cols: Tuple[str, str, str]) -> Optional[Tuple[float, float, float]]:
    try:
        vals = tuple(float(row[c]) for c in cols)
    except (KeyError, TypeError, ValueError):
        return None
    if min(vals) <= 1.0:
        return None
    return vals  # type: ignore[return-value]


def load_matches(root: str = DEFAULT_DATA_ROOT) -> List[Match]:
    """Zwraca mecze majace KOMPLET szesciu kursow Pinnacle, posortowane."""
    out: List[Match] = []
    for path in sorted(glob.glob(os.path.join(root, "*.csv"))):
        for row in read_rows(path):
            if not (row.get("HomeTeam") or "").strip():
                continue  # stopka pliku / pusty wiersz
            d = parse_date(row.get("Date", ""))
            if d is None:
                continue
            ftr = (row.get("FTR") or "").strip()
            if ftr not in ("H", "D", "A"):
                continue
            o_open = _odds(row, ODDS_OPEN)
            o_close = _odds(row, ODDS_CLOSE)
            if o_open is None or o_close is None:
                continue
            out.append(
                Match(
                    div=(row.get("Div") or "").strip(),
                    date=d,
                    home=row["HomeTeam"].strip(),
                    away=(row.get("AwayTeam") or "").strip(),
                    ftr=ftr,
                    o_open=o_open,
                    o_close=o_close,
                )
            )
    out.sort(key=lambda m: (m.date, m.div, m.home, m.away))
    return out
