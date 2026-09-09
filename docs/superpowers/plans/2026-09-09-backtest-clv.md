# Backtest CLV — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Przepuścić 86 024 mecze z kursami Pinnacle open+close i znanym wynikiem przez pełny łańcuch de-vig → P_model point-in-time → bramka EV_net → CLV → walk-forward i odpowiedzieć liczbą, czy w tym pipelinie jest przewaga.

**Architecture:** Dziewięć modułów stdlib w `backtest/`, każdy z jedną odpowiedzialnością i własnym plikiem testów. Dane wchodzą raz przez `loader.py` i dalej krążą jako niemutowalny `Match`. Każdy etap jest testowalny osobno, więc porażka lokalizuje się do jednego modułu, a nie do „backtestu". Orkiestracja walk-forward jest ostatnia i tylko skleja gotowe, przetestowane kawałki.

**Tech Stack:** Python 3.9.6, wyłącznie biblioteka standardowa (`csv`, `math`, `statistics`, `json`, `argparse`, `datetime`, `collections`, `unittest`). Brak numpy, pandas, scikit-learn i pytest — i tak ma zostać.

## Global Constraints

- **Interpreter:** `/usr/bin/python3`, wersja 3.9.6. Bez f-string `=`, bez `match`, bez `list[int]` w adnotacjach runtime.
- **Tylko stdlib.** Żadnego `pip install`. Jeśli jakiś krok wydaje się wymagać numpy — napisz pętlę.
- **Testy:** `unittest` ze stdlib, uruchamiane z korzenia repo jako `python3 -m unittest tests.test_X -v`. `pytest` NIE jest zainstalowany.
- **Korzeń repo:** `/Users/wojciechrudnicki/Code_projects/Model-buk-2.0` (git, gałąź `main`).
- **Dane (poza repo, nie commitować):** `/Users/wojciechrudnicki/Code_projects/hazard_saas/data/raw/football-data-uk/main`
- **Kodowanie plików CSV jest niejednolite.** Część ma BOM UTF-8, część jest latin-1. Czytanie jako utf-8 wywala `UnicodeDecodeError` na `0xf6`; czytanie jako latin-1 zamienia BOM w `ï»¿` i psuje nazwę pierwszej kolumny (`Div` → `ï»¿Div`, co daje 11 658 wierszy z pustą ligą i 23 ligi zamiast 22). Jedyna poprawna procedura jest w Task 1 i wszystko inne musi z niej korzystać.
- **τ (mnożnik reżimu podatkowego), za [[22 - Zasady modelu]] §4:** `0.88` standard PL (12% od stawki), `0.94` Betclic po degradacji promocji, `1.00` Betclic bez podatku na SOLO.
- **`EV_net = p_model × (τ × o) − 1`.** Bramka obowiązuje na `EV_net`, **nigdy** na `EV_gross` — [[22]] §5.
- **`EV_min = 0.05`** dla pilotażu.
- **Zero leakage** — [[11 - Czas predykcji i leakage]]: każda cecha dla meczu z datą `d` liczy się wyłącznie z meczów o dacie `< d`. Zakaz losowego train/test splitu. Zakaz kursu zamknięcia w cechach modelu.
- **Walk-forward, nigdy jeden podział** — [[12 - Ewaluacja i backtesting]].
- **Determinizm:** przy równych datach sortuj po `(date, div, home, away)`. Żadnego `set` tam, gdzie kolejność wpływa na wynik. Bez `random` bez ziarna.
- **Zastrzeżenie do nazwania w raporcie, nie do przemilczenia:** kolumny `PSH/PSD/PSA` w football-data.co.uk nie są kursem otwarcia rynku, tylko zdjęciem robionym przy budowie cotygodniowego pliku (zwykle piątek dla weekendu). Mierzymy więc ruch *piątek → zamknięcie*, co jest konserwatywnym podzbiorem prawdziwego CLV. Raport ma to pisać wprost, a nie nazywać „opening".

---

## Struktura plików

```
Model-buk-2.0/
├── backtest/
│   ├── __init__.py          pusty, czyni pakiet importowalnym
│   ├── loader.py            czytanie CSV, kodowania, typ Match, sezony
│   ├── devig.py             trzy metody zdejmowania marży
│   ├── metrics.py           Brier, log-loss, RPS, ECE, koszyki kalibracji
│   ├── elo.py               Elo liczone wyłącznie do przodu
│   ├── model.py             wieloklasowa regresja logistyczna (ręczny gradient)
│   ├── gate.py              τ, EV_net, reguła decyzyjna
│   ├── clv.py               closing line value
│   ├── walkforward.py       podział na sezony, pętla train→predict
│   └── run_backtest.py      CLI, raport JSON + markdown
├── tests/
│   ├── __init__.py
│   ├── test_loader.py  test_devig.py  test_metrics.py  test_elo.py
│   └── test_model.py   test_gate.py   test_clv.py      test_walkforward.py
└── reports/
    └── (generowane: backtest-clv-<data>.md, backtest-clv-<data>.json)
```

Podział jest po odpowiedzialności, nie po warstwie. `devig.py` nie wie nic o Elo, `elo.py` nie wie nic o kursach, `gate.py` nie wie, skąd wzięło się `p`. Dzięki temu Task 4 (kalibracja rynku) daje wynik zanim powstanie jakikolwiek model — i jeśli rynek po de-vigu nie jest skalibrowany, dowiadujemy się o tym przed napisaniem 300 linii modelu.

---

### Task 1: Loader — wczytanie 86 025 meczów bez cichych strat

**Files:**
- Create: `backtest/__init__.py`
- Create: `backtest/loader.py`
- Create: `tests/__init__.py`
- Create: `tests/test_loader.py`

**Interfaces:**
- Consumes: nic (pierwszy task)
- Produces:
  - `Match` — `NamedTuple` z polami: `div: str`, `date: datetime.date`, `home: str`, `away: str`, `ftr: str` (`"H"`/`"D"`/`"A"`), `o_open: Tuple[float,float,float]`, `o_close: Tuple[float,float,float]` (kolejność zawsze H, D, A)
  - `read_rows(path: str) -> Iterator[Dict[str,str]]`
  - `parse_date(s: str) -> Optional[datetime.date]`
  - `season_of(d: datetime.date) -> str` — zwraca np. `"2014/15"`, granica 1 lipca
  - `load_matches(root: str = DEFAULT_DATA_ROOT) -> List[Match]` — posortowane po `(date, div, home, away)`
  - `DEFAULT_DATA_ROOT: str`

- [ ] **Step 1: Napisz test, który ma nie przejść**

Utwórz `tests/__init__.py` jako pusty plik, potem `tests/test_loader.py`:

```python
import datetime
import unittest

from backtest.loader import Match, load_matches, parse_date, season_of


class TestParseDate(unittest.TestCase):
    def test_four_digit_year(self):
        self.assertEqual(parse_date("09/08/2024"), datetime.date(2024, 8, 9))

    def test_two_digit_year(self):
        self.assertEqual(parse_date("25/07/14"), datetime.date(2014, 7, 25))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date("not a date"))


class TestSeasonOf(unittest.TestCase):
    def test_july_starts_new_season(self):
        self.assertEqual(season_of(datetime.date(2014, 7, 25)), "2014/15")

    def test_june_belongs_to_previous_season(self):
        self.assertEqual(season_of(datetime.date(2015, 6, 30)), "2014/15")

    def test_january_belongs_to_previous_season(self):
        self.assertEqual(season_of(datetime.date(2026, 1, 14)), "2025/26")


class TestLoadMatches(unittest.TestCase):
    """Testy na prawdziwych danych. Liczby pochodza z audytu i musza sie zgadzac
    co do jednego meczu - cicha utrata wierszy jest tu glownym ryzykiem."""

    @classmethod
    def setUpClass(cls):
        cls.matches = load_matches()

    def test_exact_row_count(self):
        """86 025 wierszy ma komplet kursow Pinnacle (tak podaje audyt), ale
        jeden z nich - Panathinaikos vs Olympiakos, 17/03/2019, derby przerwane
        przy 0:3 - nie ma wyniku koncowego. Mecz przerwany to VOID, zwrot stawki,
        wiec do backtestu nadaje sie 86 024."""
        self.assertEqual(len(self.matches), 86024)

    def test_exactly_22_leagues_no_empty_div(self):
        divs = {m.div for m in self.matches}
        self.assertNotIn("", divs, "pusty Div = BOM nie zostal usuniety")
        self.assertEqual(len(divs), 22)

    def test_date_range(self):
        self.assertEqual(self.matches[0].date, datetime.date(2014, 7, 25))
        self.assertEqual(self.matches[-1].date, datetime.date(2026, 1, 14))

    def test_sorted_deterministically(self):
        keys = [(m.date, m.div, m.home, m.away) for m in self.matches]
        self.assertEqual(keys, sorted(keys))

    def test_all_odds_above_one(self):
        for m in self.matches:
            self.assertTrue(min(m.o_open) > 1.0)
            self.assertTrue(min(m.o_close) > 1.0)

    def test_ftr_only_hda(self):
        self.assertEqual({m.ftr for m in self.matches}, {"H", "D", "A"})

    def test_is_a_match_namedtuple(self):
        self.assertIsInstance(self.matches[0], Match)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_loader -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest'`

- [ ] **Step 3: Napisz implementację**

Utwórz `backtest/__init__.py` jako pusty plik, potem `backtest/loader.py`:

```python
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
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_loader -v
```

Oczekiwane: `OK`. Jeśli `test_exact_row_count` daje inną liczbę niż 86024 — **zatrzymaj się i znajdź przyczynę**, nie poprawiaj liczby w teście. Kontekst: audyt (`Audyt-danych-i-rynku/README.md:29`) podaje 86 025 i ma rację — tyle wierszy ma komplet kursów. Jeden z nich (Panathinaikos–Olympiakos, 17/03/2019, mecz przerwany przy 0:3) nie ma wyniku końcowego, więc odpada na filtrze `FTR`. **Nie dopisuj fallbacku na `HTR`** — stan z przerwy nie jest wynikiem meczu, a mecz przerwany to VOID.

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/__init__.py backtest/loader.py tests/__init__.py tests/test_loader.py && git commit -m "backtest: loader odporny na mieszane kodowania

Pliki football-data.co.uk maja niejednolite kodowanie - czesc UTF-8 z BOM,
czesc latin-1. Wykrywanie BOM na bajtach przed dekodowaniem, bo latin-1
zamienia BOM w widoczne znaki i psuje pierwsza kolumne (11 658 wierszy
traci Div, 23 ligi zamiast 22). Test przypina liczbe 86 025 z audytu."
```

---

### Task 2: De-vig — trzy metody zdejmowania marży

**Files:**
- Create: `backtest/devig.py`
- Create: `tests/test_devig.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `devig_proportional(odds: Sequence[float]) -> Tuple[float, ...]`
  - `devig_power(odds: Sequence[float]) -> Tuple[float, ...]`
  - `devig_shin(odds: Sequence[float]) -> Tuple[float, ...]`
  - `overround(odds: Sequence[float]) -> float` — zwraca nadwyżkę, np. `0.0344` dla 3,44%
  - `METHODS: Dict[str, Callable]` — `{"proportional": ..., "power": ..., "shin": ...}`

Trzy metody, bo wybór metody de-vigu jest sam w sobie hipotezą i [[12]] wymaga „sensitivity na metodę usunięcia marży". Task 4 rozstrzygnie empirycznie, która wygrywa — nie zgadujemy tego teraz.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_devig.py`:

```python
import unittest

from backtest.devig import (
    METHODS,
    devig_power,
    devig_proportional,
    devig_shin,
    overround,
)

FAIR = (3.0, 3.0, 3.0)              # ksiazka bez marzy
TYPICAL = (2.00, 3.50, 4.00)        # marza ~3,6%
FAVOURITE = (1.20, 7.00, 15.00)     # silny faworyt + dlugi strzal


class TestOverround(unittest.TestCase):
    def test_fair_book_has_zero_overround(self):
        self.assertAlmostEqual(overround(FAIR), 0.0, places=12)

    def test_typical_book(self):
        self.assertAlmostEqual(overround(TYPICAL), 0.0357142857, places=9)


class TestAllMethodsShareInvariants(unittest.TestCase):
    """Kazda metoda musi zwracac rozklad prawdopodobienstwa."""

    def test_probabilities_sum_to_one(self):
        for name, fn in METHODS.items():
            for book in (FAIR, TYPICAL, FAVOURITE):
                with self.subTest(method=name, book=book):
                    self.assertAlmostEqual(sum(fn(book)), 1.0, places=9)

    def test_probabilities_strictly_inside_unit_interval(self):
        for name, fn in METHODS.items():
            for book in (FAIR, TYPICAL, FAVOURITE):
                with self.subTest(method=name, book=book):
                    for p in fn(book):
                        self.assertGreater(p, 0.0)
                        self.assertLess(p, 1.0)

    def test_fair_book_is_a_fixed_point(self):
        """Ksiazka bez marzy nie powinna byc ruszona przez zadna metode."""
        for name, fn in METHODS.items():
            with self.subTest(method=name):
                for p in fn(FAIR):
                    self.assertAlmostEqual(p, 1.0 / 3.0, places=9)

    def test_order_is_preserved(self):
        """Faworyt zostaje faworytem niezaleznie od metody."""
        for name, fn in METHODS.items():
            with self.subTest(method=name):
                p = fn(FAVOURITE)
                self.assertGreater(p[0], p[1])
                self.assertGreater(p[1], p[2])


class TestProportional(unittest.TestCase):
    def test_known_values(self):
        p = devig_proportional(TYPICAL)
        self.assertAlmostEqual(p[0], 0.4827586207, places=9)
        self.assertAlmostEqual(p[1], 0.2758620690, places=9)
        self.assertAlmostEqual(p[2], 0.2413793103, places=9)


class TestShinVersusProportional(unittest.TestCase):
    """Shin modeluje handel insiderow, wiec zdejmuje wiecej marzy z dlugich
    strzalow niz metoda proporcjonalna. To jest kierunek biasu faworyt-longshot
    i jedyny powod, dla ktorego w ogole trzymamy trzy metody."""

    def test_shin_gives_longshot_less_probability(self):
        prop = devig_proportional(FAVOURITE)
        shin = devig_shin(FAVOURITE)
        self.assertLess(shin[2], prop[2])

    def test_shin_gives_favourite_more_probability(self):
        prop = devig_proportional(FAVOURITE)
        shin = devig_shin(FAVOURITE)
        self.assertGreater(shin[0], prop[0])


class TestPower(unittest.TestCase):
    def test_exponent_above_one_for_positive_overround(self):
        """Przy dodatniej marzy wykladnik musi byc > 1, inaczej suma nie spadnie
        do jedynki. Sprawdzamy posrednio: kazde p mniejsze niz surowe 1/o."""
        p = devig_power(TYPICAL)
        for prob, o in zip(p, TYPICAL):
            self.assertLess(prob, 1.0 / o)


class TestRegistry(unittest.TestCase):
    def test_registry_has_three_methods(self):
        self.assertEqual(set(METHODS), {"proportional", "power", "shin"})
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_devig -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.devig'`

- [ ] **Step 3: Napisz implementację**

`backtest/devig.py`:

```python
"""Zdejmowanie marzy bukmacherskiej z kursow.

Trzy metody, bo wybor jest hipoteza badawcza, nie detalem technicznym:

- proportional: p_i = (1/o_i) / suma(1/o). Najprostsza, zaklada ze marza jest
  rozlozona rownomiernie. Zawyza dlugie strzaly.
- power:        p_i = (1/o_i)^k, gdzie k dobrane tak, ze suma = 1. Zdejmuje
  wiecej z dlugich strzalow.
- shin:         model Shina (1993) z handlem insiderow. Parametr z to ulamek
  obrotu pochodzacy od poinformowanych; wiekszy z = wiecej marzy zdjete
  z dlugich strzalow.

Oba nieliniowe rozwiazania idziemy bisekcja - 60 iteracji daje precyzje
ponizej 1e-15 i nie wymaga pochodnych ani numpy.
"""
import math
from typing import Callable, Dict, Sequence, Tuple

_BISECT_ITERATIONS = 100


def overround(odds: Sequence[float]) -> float:
    """Nadwyzka ksiazki ponad 1. Dla 3,44% zwraca 0.0344."""
    return sum(1.0 / o for o in odds) - 1.0


def devig_proportional(odds: Sequence[float]) -> Tuple[float, ...]:
    q = [1.0 / o for o in odds]
    s = sum(q)
    return tuple(x / s for x in q)


def devig_power(odds: Sequence[float]) -> Tuple[float, ...]:
    """Szuka k takiego, ze suma (1/o_i)^k == 1."""
    q = [1.0 / o for o in odds]
    if abs(sum(q) - 1.0) < 1e-12:
        return tuple(q)

    def total(k: float) -> float:
        return sum(x ** k for x in q)

    # Suma jest malejaca w k (kazde q_i < 1). Znajdz gorna granice.
    lo, hi = 0.0, 1.0
    while total(hi) > 1.0:
        hi *= 2.0
        if hi > 1e6:  # nie powinno wystapic dla realnych kursow
            raise ValueError("nie znaleziono wykladnika dla odds={0}".format(tuple(odds)))
    for _ in range(_BISECT_ITERATIONS):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    p = [x ** k for x in q]
    s = sum(p)
    return tuple(x / s for x in p)  # domkniecie bledu numerycznego


def devig_shin(odds: Sequence[float]) -> Tuple[float, ...]:
    """Model Shina. Szuka z w [0,1) takiego, ze suma prawdopodobienstw == 1."""
    q = [1.0 / o for o in odds]
    s = sum(q)
    if abs(s - 1.0) < 1e-12:
        return tuple(q)

    def probs(z: float) -> Tuple[float, ...]:
        if z <= 0.0:
            return tuple(x / math.sqrt(s) for x in q)
        return tuple(
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (x * x) / s) - z) / (2.0 * (1.0 - z))
            for x in q
        )

    # Suma maleje wraz ze wzrostem z. Przy z=0 wynosi sqrt(s) > 1.
    lo, hi = 0.0, 0.5
    while sum(probs(hi)) > 1.0:
        hi = (hi + 1.0) / 2.0
        if hi > 1.0 - 1e-12:
            break
    for _ in range(_BISECT_ITERATIONS):
        mid = (lo + hi) / 2.0
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2.0)
    total = sum(p)
    return tuple(x / total for x in p)


METHODS: Dict[str, Callable[[Sequence[float]], Tuple[float, ...]]] = {
    "proportional": devig_proportional,
    "power": devig_power,
    "shin": devig_shin,
}
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_devig -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/devig.py tests/test_devig.py && git commit -m "backtest: trzy metody de-vigu z testem kierunku biasu

Proportional, power i Shin. Trzymamy trzy, bo nota 12 wymaga sensitivity
na metode zdjecia marzy, a wybor rozstrzygnie sie empirycznie na kalibracji
zamkniecia, nie z gory. Test przypina kierunek: Shin musi dawac dlugiemu
strzalowi mniej niz metoda proporcjonalna."
```

---

### Task 3: Metryki probabilistyczne

**Files:**
- Create: `backtest/metrics.py`
- Create: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `OUTCOMES: Tuple[str, str, str]` — `("H", "D", "A")`, kolejność obowiązująca wszędzie
  - `outcome_index(ftr: str) -> int`
  - `brier(probs: Sequence[float], ftr: str) -> float`
  - `log_loss(probs: Sequence[float], ftr: str) -> float`
  - `rps(probs: Sequence[float], ftr: str) -> float`
  - `Bucket` — `NamedTuple(lo: float, hi: float, n: int, mean_pred: float, mean_obs: float)`
  - `calibration_buckets(pairs: Sequence[Tuple[float, int]], n_buckets: int = 20) -> List[Bucket]` — `pairs` to `(przewidziane p, 1 jeśli zdarzenie zaszło)`
  - `ece(pairs: Sequence[Tuple[float, int]], n_buckets: int = 20) -> float`

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_metrics.py`:

```python
import math
import unittest

from backtest.metrics import (
    Bucket,
    brier,
    calibration_buckets,
    ece,
    log_loss,
    outcome_index,
    rps,
)

P = (0.5, 0.3, 0.2)


class TestOutcomeIndex(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(outcome_index("H"), 0)
        self.assertEqual(outcome_index("D"), 1)
        self.assertEqual(outcome_index("A"), 2)

    def test_rejects_unknown(self):
        with self.assertRaises(ValueError):
            outcome_index("X")


class TestBrier(unittest.TestCase):
    def test_known_value(self):
        # (0.5-1)^2 + (0.3-0)^2 + (0.2-0)^2 = 0.25 + 0.09 + 0.04
        self.assertAlmostEqual(brier(P, "H"), 0.38, places=12)

    def test_perfect_prediction_scores_zero(self):
        self.assertAlmostEqual(brier((1.0, 0.0, 0.0), "H"), 0.0, places=12)


class TestLogLoss(unittest.TestCase):
    def test_known_value(self):
        self.assertAlmostEqual(log_loss(P, "H"), -math.log(0.5), places=12)

    def test_zero_probability_is_clipped_not_infinite(self):
        self.assertTrue(math.isfinite(log_loss((0.0, 0.5, 0.5), "H")))


class TestRPS(unittest.TestCase):
    def test_known_value(self):
        # CP = (0.5, 0.8), CA = (1, 1) -> ((0.5-1)^2 + (0.8-1)^2) / 2
        self.assertAlmostEqual(rps(P, "H"), 0.145, places=12)

    def test_draw_outcome(self):
        # CP = (0.5, 0.8), CA = (0, 1) -> (0.25 + 0.04) / 2
        self.assertAlmostEqual(rps(P, "D"), 0.145, places=12)

    def test_ordinal_penalty_is_asymmetric(self):
        """RPS karze mocniej pomylke H<->A niz H<->D, bo remis lezy miedzy nimi.
        Brier tej roznicy nie widzi - dlatego trzymamy obie metryki."""
        confident_home = (0.8, 0.1, 0.1)
        self.assertGreater(rps(confident_home, "A"), rps(confident_home, "D"))
        self.assertAlmostEqual(
            brier(confident_home, "A"), brier(confident_home, "D"), places=12
        )


class TestCalibrationBuckets(unittest.TestCase):
    def test_perfectly_calibrated_input(self):
        pairs = [(0.5, 1)] * 50 + [(0.5, 0)] * 50
        buckets = [b for b in calibration_buckets(pairs, n_buckets=10) if b.n]
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].n, 100)
        self.assertAlmostEqual(buckets[0].mean_pred, 0.5, places=12)
        self.assertAlmostEqual(buckets[0].mean_obs, 0.5, places=12)

    def test_returns_bucket_namedtuple(self):
        buckets = calibration_buckets([(0.5, 1)], n_buckets=10)
        self.assertIsInstance(buckets[0], Bucket)

    def test_probability_of_one_lands_in_last_bucket(self):
        buckets = calibration_buckets([(1.0, 1)], n_buckets=10)
        self.assertEqual(buckets[-1].n, 1)


class TestECE(unittest.TestCase):
    def test_perfect_calibration_is_zero(self):
        pairs = [(0.5, 1)] * 50 + [(0.5, 0)] * 50
        self.assertAlmostEqual(ece(pairs, n_buckets=10), 0.0, places=12)

    def test_maximally_wrong_is_one(self):
        pairs = [(1.0, 0)] * 10
        self.assertAlmostEqual(ece(pairs, n_buckets=10), 1.0, places=12)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_metrics -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.metrics'`

- [ ] **Step 3: Napisz implementację**

`backtest/metrics.py`:

```python
"""Metryki probabilistyczne wymagane przez [[12 - Ewaluacja i backtesting]].

Trzymamy i Briera, i RPS, bo mierza rozne rzeczy. Brier traktuje H, D, A jako
trzy niezalezne etykiety. RPS wie, ze remis lezy miedzy zwyciestwem gospodarza
a zwyciestwem goscia, wiec karze mocniej za pomylke H<->A niz H<->D. Dla 1X2
RPS jest metryka wlasciwa, Brier - pomocnicza.
"""
import math
from typing import List, NamedTuple, Sequence, Tuple

OUTCOMES: Tuple[str, str, str] = ("H", "D", "A")

_EPS = 1e-15


def outcome_index(ftr: str) -> int:
    try:
        return OUTCOMES.index(ftr)
    except ValueError:
        raise ValueError("nieznany wynik: {0!r}".format(ftr))


def brier(probs: Sequence[float], ftr: str) -> float:
    k = outcome_index(ftr)
    return sum((p - (1.0 if i == k else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss(probs: Sequence[float], ftr: str) -> float:
    k = outcome_index(ftr)
    return -math.log(max(probs[k], _EPS))


def rps(probs: Sequence[float], ftr: str) -> float:
    """Ranked Probability Score dla uporzadkowanych wynikow H < D < A."""
    k = outcome_index(ftr)
    cum_p = 0.0
    cum_a = 0.0
    total = 0.0
    for i in range(len(probs) - 1):
        cum_p += probs[i]
        cum_a += 1.0 if i == k else 0.0
        total += (cum_p - cum_a) ** 2
    return total / (len(probs) - 1)


class Bucket(NamedTuple):
    lo: float
    hi: float
    n: int
    mean_pred: float
    mean_obs: float


def calibration_buckets(
    pairs: Sequence[Tuple[float, int]], n_buckets: int = 20
) -> List[Bucket]:
    """pairs to (przewidziane prawdopodobienstwo, 1 jesli zdarzenie zaszlo)."""
    sums = [0.0] * n_buckets
    hits = [0] * n_buckets
    counts = [0] * n_buckets
    for p, y in pairs:
        idx = min(int(p * n_buckets), n_buckets - 1)  # p == 1.0 do ostatniego
        sums[idx] += p
        hits[idx] += y
        counts[idx] += 1
    out: List[Bucket] = []
    for i in range(n_buckets):
        n = counts[i]
        out.append(
            Bucket(
                lo=i / n_buckets,
                hi=(i + 1) / n_buckets,
                n=n,
                mean_pred=(sums[i] / n) if n else 0.0,
                mean_obs=(hits[i] / n) if n else 0.0,
            )
        )
    return out


def ece(pairs: Sequence[Tuple[float, int]], n_buckets: int = 20) -> float:
    """Expected Calibration Error - srednia wazona odchylen w koszykach."""
    buckets = calibration_buckets(pairs, n_buckets)
    total = sum(b.n for b in buckets)
    if not total:
        return 0.0
    return sum(b.n * abs(b.mean_pred - b.mean_obs) for b in buckets) / total
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_metrics -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/metrics.py tests/test_metrics.py && git commit -m "backtest: Brier, log-loss, RPS, ECE i koszyki kalibracji

RPS obok Briera, bo dla 1X2 remis lezy miedzy H i A - Brier tej ordynalnosci
nie widzi, a pomylka H<->A jest gorsza niz H<->D. Test przypina te asymetrie."
```

---

### Task 4: Kalibracja rynku po de-vigu — pierwszy wynik empiryczny

**Files:**
- Create: `backtest/report_devig.py`
- Create: `tests/test_report_devig.py`
- Create: `reports/.gitkeep`

**Interfaces:**
- Consumes: `loader.load_matches`, `loader.Match`, `devig.METHODS`, `metrics.{brier,log_loss,rps,ece,calibration_buckets,OUTCOMES}`
- Produces:
  - `MethodScore` — `NamedTuple(method: str, n: int, log_loss: float, brier: float, rps: float, ece: float)`
  - `score_method(matches: Sequence[Match], method: str, which: str) -> MethodScore` — `which` to `"open"` albo `"close"`
  - `best_method(matches: Sequence[Match]) -> str`
  - `main(argv=None) -> int`

**Dlaczego ten task jest tu, a nie na końcu:** to jedyny etap, który daje wynik bez żadnego modelu. Jeśli zdevigowane zamknięcie Pinnacle nie jest skalibrowane, to albo de-vig jest zepsuty, albo loader kłamie — i lepiej się o tym dowiedzieć teraz niż po napisaniu modelu. Zamknięcie Pinnacle jest w literaturze najostrzejszą dostępną publicznie ceną, więc **oczekujemy ECE poniżej 0,01**. Wynik wyraźnie gorszy to sygnał błędu implementacji, nie odkrycie.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_report_devig.py`:

```python
import datetime
import unittest

from backtest.loader import Match
from backtest.report_devig import MethodScore, best_method, score_method


def _match(o_open, o_close, ftr):
    return Match(
        div="E0",
        date=datetime.date(2020, 1, 1),
        home="A",
        away="B",
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


class TestScoreMethod(unittest.TestCase):
    def test_returns_methodscore_with_n(self):
        matches = [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")] * 10
        s = score_method(matches, "proportional", "close")
        self.assertIsInstance(s, MethodScore)
        self.assertEqual(s.n, 10)
        self.assertEqual(s.method, "proportional")

    def test_perfectly_calibrated_book_scores_near_zero_ece(self):
        """Ksiazka 3.0/3.0/3.0 przy dokladnie 1/3 kazdego wyniku."""
        matches = (
            [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "H")] * 100
            + [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "D")] * 100
            + [_match((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), "A")] * 100
        )
        s = score_method(matches, "proportional", "close")
        self.assertLess(s.ece, 1e-9)

    def test_open_and_close_are_read_from_different_fields(self):
        matches = [_match((1.01, 100.0, 100.0), (100.0, 100.0, 1.01), "H")] * 50
        open_score = score_method(matches, "proportional", "open")
        close_score = score_method(matches, "proportional", "close")
        self.assertLess(open_score.log_loss, close_score.log_loss)

    def test_rejects_unknown_which(self):
        with self.assertRaises(ValueError):
            score_method([_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")], "proportional", "kickoff")


class TestBestMethod(unittest.TestCase):
    def test_returns_a_registered_method_name(self):
        matches = (
            [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "H")] * 60
            + [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "D")] * 25
            + [_match((2.0, 3.5, 4.0), (2.0, 3.5, 4.0), "A")] * 15
        )
        self.assertIn(best_method(matches), {"proportional", "power", "shin"})
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_report_devig -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.report_devig'`

- [ ] **Step 3: Napisz implementację**

Utwórz pusty `reports/.gitkeep`, potem `backtest/report_devig.py`:

```python
"""Etap 1 backtestu: czy zdevigowane kursy Pinnacle sa skalibrowane?

Ten krok nie uzywa zadnego modelu. Sprawdza tylko, czy rynek po zdjeciu marzy
mowi prawde o czestosci zdarzen. Zamkniecie Pinnacle jest najostrzejsza
publicznie dostepna cena, wiec ECE powinno wyjsc ponizej 0,01. Wyraznie gorszy
wynik oznacza blad w loaderze albo w de-vigu, a nie odkrycie naukowe.

Uzycie:
    python3 -m backtest.report_devig
    python3 -m backtest.report_devig --out reports/devig-2026-09-09.md
"""
import argparse
import sys
from typing import List, NamedTuple, Optional, Sequence

from backtest.devig import METHODS, overround
from backtest.loader import Match, load_matches
from backtest.metrics import (
    brier,
    calibration_buckets,
    ece,
    log_loss,
    outcome_index,
    rps,
)


class MethodScore(NamedTuple):
    method: str
    n: int
    log_loss: float
    brier: float
    rps: float
    ece: float


def _odds_for(m: Match, which: str):
    if which == "open":
        return m.o_open
    if which == "close":
        return m.o_close
    raise ValueError("which musi byc 'open' albo 'close', dostalem {0!r}".format(which))


def score_method(matches: Sequence[Match], method: str, which: str) -> MethodScore:
    fn = METHODS[method]
    ll = br = rp = 0.0
    pairs = []
    for m in matches:
        probs = fn(_odds_for(m, which))
        ll += log_loss(probs, m.ftr)
        br += brier(probs, m.ftr)
        rp += rps(probs, m.ftr)
        k = outcome_index(m.ftr)
        for i, p in enumerate(probs):
            pairs.append((p, 1 if i == k else 0))
    n = len(matches)
    if not n:
        return MethodScore(method, 0, 0.0, 0.0, 0.0, 0.0)
    return MethodScore(method, n, ll / n, br / n, rp / n, ece(pairs))


def best_method(matches: Sequence[Match]) -> str:
    """Metoda o najnizszym log-lossie na zamknieciu."""
    scored = [score_method(matches, name, "close") for name in sorted(METHODS)]
    return min(scored, key=lambda s: s.log_loss).method


def _render(matches: Sequence[Match]) -> str:
    lines: List[str] = []
    lines.append("# Kalibracja rynku po de-vigu\n")
    lines.append("Mecze: **{0}**\n".format(len(matches)))
    oo = sum(overround(m.o_open) for m in matches) / len(matches)
    oc = sum(overround(m.o_close) for m in matches) / len(matches)
    lines.append("Overround: {0:.2f}% na PS* -> {1:.2f}% na zamknieciu\n".format(oo * 100, oc * 100))
    lines.append(
        "> `PSH/PSD/PSA` to zdjecie z budowy pliku tygodniowego (zwykle piatek),\n"
        "> nie kurs otwarcia rynku. Ponizsze 'open' czytaj jako 'piatek'.\n"
    )
    for which in ("open", "close"):
        lines.append("\n## {0}\n".format(which))
        lines.append("| metoda | log-loss | Brier | RPS | ECE |")
        lines.append("|---|---:|---:|---:|---:|")
        for name in sorted(METHODS):
            s = score_method(matches, name, which)
            lines.append(
                "| {0} | {1:.5f} | {2:.5f} | {3:.5f} | {4:.5f} |".format(
                    s.method, s.log_loss, s.brier, s.rps, s.ece
                )
            )
    winner = best_method(matches)
    lines.append("\n**Metoda wybrana do dalszych etapow: `{0}`** (najnizszy log-loss na zamknieciu).\n".format(winner))
    lines.append("\n## Krzywa kalibracji zamkniecia ({0})\n".format(winner))
    fn = METHODS[winner]
    pairs = []
    for m in matches:
        probs = fn(m.o_close)
        k = outcome_index(m.ftr)
        for i, p in enumerate(probs):
            pairs.append((p, 1 if i == k else 0))
    lines.append("| przedzial | n | sr. przewidziane | sr. zaobserwowane | roznica |")
    lines.append("|---|---:|---:|---:|---:|")
    for b in calibration_buckets(pairs, n_buckets=20):
        if not b.n:
            continue
        lines.append(
            "| {0:.2f}-{1:.2f} | {2} | {3:.4f} | {4:.4f} | {5:+.4f} |".format(
                b.lo, b.hi, b.n, b.mean_pred, b.mean_obs, b.mean_obs - b.mean_pred
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kalibracja rynku po de-vigu")
    ap.add_argument("--out", default=None, help="sciezka pliku markdown")
    args = ap.parse_args(argv)
    matches = load_matches()
    text = _render(matches)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("zapisano {0}".format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_report_devig -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Uruchom raport na prawdziwych danych i przeczytaj wynik**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m backtest.report_devig --out reports/devig-calibration.md && head -30 reports/devig-calibration.md
```

Oczekiwane: overround `3,44% → 3,10%`, ECE na zamknięciu **poniżej 0,01** dla najlepszej metody. **Jeśli ECE przekracza 0,02 — zatrzymaj się.** To nie jest wynik do zaraportowania, tylko objaw błędu: sprawdź kolejność H/D/A w `_odds_for` i w `outcome_index`, bo przestawienie remisu z gościem daje dokładnie taki obraz.

- [ ] **Step 6: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/report_devig.py tests/test_report_devig.py reports/ && git commit -m "backtest: kalibracja rynku po de-vigu jako pierwszy wynik

Etap testowalny bez modelu - jesli zdevigowane zamkniecie Pinnacle nie jest
skalibrowane, to blad implementacji, a nie odkrycie. Wybiera tez metode
de-vigu do dalszych etapow po log-lossie zamkniecia, zamiast zgadywac ja
z gory."
```

---

### Task 5: Elo liczone wyłącznie do przodu

**Files:**
- Create: `backtest/elo.py`
- Create: `tests/test_elo.py`

**Interfaces:**
- Consumes: `loader.Match`
- Produces:
  - `EloTable` z metodami:
    - `__init__(self, k: float = 20.0, home_adv: float = 60.0, start: float = 1500.0)`
    - `rating(self, team: str) -> float`
    - `expected_home(self, home: str, away: str) -> float`
    - `diff(self, home: str, away: str) -> float` — `rating(home) + home_adv - rating(away)`
    - `update(self, home: str, away: str, ftr: str) -> None`
  - `SCORE: Dict[str, float]` — `{"H": 1.0, "D": 0.5, "A": 0.0}`

**Kontrakt point-in-time:** wywołujący **musi** odczytać `diff`/`expected_home` **przed** `update` dla każdego meczu, idąc chronologicznie. `EloTable` nie zna dat i nie może tego wymusić — pilnuje tego `walkforward.py` (Task 9) i test leakage poniżej.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_elo.py`:

```python
import unittest

from backtest.elo import SCORE, EloTable


class TestFreshTable(unittest.TestCase):
    def test_unknown_team_starts_at_default(self):
        t = EloTable()
        self.assertEqual(t.rating("Nowy Klub"), 1500.0)

    def test_reading_a_rating_does_not_create_history(self):
        t = EloTable()
        t.rating("A")
        t.rating("B")
        self.assertEqual(t.rating("A"), 1500.0)


class TestExpectation(unittest.TestCase):
    def test_home_advantage_tilts_expectation_above_half(self):
        t = EloTable(home_adv=60.0)
        self.assertAlmostEqual(t.expected_home("A", "B"), 0.585498, places=5)

    def test_no_home_advantage_gives_exactly_half(self):
        t = EloTable(home_adv=0.0)
        self.assertAlmostEqual(t.expected_home("A", "B"), 0.5, places=12)

    def test_diff_includes_home_advantage(self):
        t = EloTable(home_adv=60.0)
        self.assertAlmostEqual(t.diff("A", "B"), 60.0, places=12)


class TestUpdate(unittest.TestCase):
    def test_home_win_moves_ratings_by_known_amount(self):
        t = EloTable(k=20.0, home_adv=60.0)
        t.update("A", "B", "H")
        self.assertAlmostEqual(t.rating("A"), 1508.2900, places=4)
        self.assertAlmostEqual(t.rating("B"), 1491.7100, places=4)

    def test_update_is_zero_sum(self):
        t = EloTable()
        t.update("A", "B", "A")
        self.assertAlmostEqual(t.rating("A") + t.rating("B"), 3000.0, places=9)

    def test_draw_between_equals_still_moves_because_of_home_advantage(self):
        """Remis u siebie jest wynikiem gorszym od oczekiwanego, wiec gospodarz traci."""
        t = EloTable(k=20.0, home_adv=60.0)
        t.update("A", "B", "D")
        self.assertLess(t.rating("A"), 1500.0)
        self.assertGreater(t.rating("B"), 1500.0)

    def test_score_mapping(self):
        self.assertEqual(SCORE["H"], 1.0)
        self.assertEqual(SCORE["D"], 0.5)
        self.assertEqual(SCORE["A"], 0.0)


class TestNoLeakage(unittest.TestCase):
    """Rdzen dyscypliny z [[11 - Czas predykcji i leakage]]: predykcja dla meczu
    nie moze zalezec od jego wyniku ani od zadnego pozniejszego meczu."""

    def test_first_ever_match_is_predicted_from_default_ratings(self):
        t = EloTable(home_adv=0.0)
        before = t.expected_home("A", "B")
        t.update("A", "B", "H")
        self.assertAlmostEqual(before, 0.5, places=12)

    def test_prediction_depends_only_on_earlier_updates(self):
        a = EloTable()
        b = EloTable()
        for table in (a, b):
            table.update("A", "B", "H")
            table.update("A", "C", "H")
        # b dostaje dodatkowy, pozniejszy mecz - nie moze zmienic tego,
        # co bylo znane wczesniej
        expected_before_extra = b.diff("A", "D")
        b.update("A", "D", "H")
        self.assertAlmostEqual(a.diff("A", "D"), expected_before_extra, places=12)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_elo -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.elo'`

- [ ] **Step 3: Napisz implementację**

`backtest/elo.py`:

```python
"""Elo liczone wylacznie do przodu.

Budujemy wlasne zamiast brac gotowe HomeElo/AwayElo z club-football z dwoch
powodow. Po pierwsze pokrycie: cudze Elo jest przy 57 344 z naszych 86 025
meczow (66,7%) i konczy sie w czerwcu 2025, wiec traci caly sezon 25/26.
Po drugie audytowalnosc: przy wlasnym Elo widac w kodzie, ze rating pochodzi
wylacznie z meczow wczesniejszych. Przy cudzym trzeba w to uwierzyc, a
[[11 - Czas predykcji i leakage]] kaze takie zalozenia sprawdzac, nie zakladac.

KONTRAKT: wolajacy odczytuje diff()/expected_home() PRZED update() dla kazdego
meczu, idac chronologicznie. Ta klasa nie zna dat i nie moze tego wymusic.
"""
from typing import Dict

SCORE: Dict[str, float] = {"H": 1.0, "D": 0.5, "A": 0.0}


class EloTable:
    def __init__(self, k: float = 20.0, home_adv: float = 60.0, start: float = 1500.0):
        self.k = k
        self.home_adv = home_adv
        self.start = start
        self._ratings: Dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self._ratings.get(team, self.start)

    def diff(self, home: str, away: str) -> float:
        """Przewaga gospodarza w punktach Elo, z bonusem za gre u siebie."""
        return self.rating(home) + self.home_adv - self.rating(away)

    def expected_home(self, home: str, away: str) -> float:
        return 1.0 / (1.0 + 10.0 ** (-self.diff(home, away) / 400.0))

    def update(self, home: str, away: str, ftr: str) -> None:
        expected = self.expected_home(home, away)
        actual = SCORE[ftr]
        delta = self.k * (actual - expected)
        self._ratings[home] = self.rating(home) + delta
        self._ratings[away] = self.rating(away) - delta
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_elo -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/elo.py tests/test_elo.py && git commit -m "backtest: wlasne Elo liczone do przodu zamiast gotowego z club-football

Cudze Elo pokrywa 57 344 z 86 025 meczow i konczy sie 2025-06, wiec traci
caly sezon 25/26. Wazniejsze: przy wlasnym widac w kodzie, ze rating pochodzi
tylko z wczesniejszych meczow - przy cudzym trzeba w to uwierzyc. Test
leakage przypina, ze pozniejszy mecz nie zmienia wczesniejszej predykcji."
```

---

### Task 6: Wieloklasowa regresja logistyczna

**Files:**
- Create: `backtest/model.py`
- Create: `tests/test_model.py`

**Interfaces:**
- Consumes: nic (czysta matematyka, żeby dało się testować bez danych)
- Produces:
  - `softmax(scores: Sequence[float]) -> Tuple[float, ...]`
  - `fit(X: Sequence[Sequence[float]], y: Sequence[int], n_classes: int = 3, lr: float = 0.5, epochs: int = 300, l2: float = 1e-4) -> List[List[float]]` — zwraca macierz wag `n_classes × n_features`
  - `predict_proba(weights: Sequence[Sequence[float]], x: Sequence[float]) -> Tuple[float, ...]`

Model celowo minimalny: cechy to `[1.0, elo_diff/400]`. Dokładanie cech to osobny przyrost, który [[12]] każe testować jako `Model_base` vs `Model_base+grupa` — nie wrzucamy wszystkiego naraz do pierwszego przebiegu.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_model.py`:

```python
import math
import unittest

from backtest.model import fit, predict_proba, softmax


class TestSoftmax(unittest.TestCase):
    def test_sums_to_one(self):
        self.assertAlmostEqual(sum(softmax([1.0, 2.0, 3.0])), 1.0, places=12)

    def test_uniform_scores_give_uniform_probabilities(self):
        for p in softmax([0.0, 0.0, 0.0]):
            self.assertAlmostEqual(p, 1.0 / 3.0, places=12)

    def test_monotonic_in_score(self):
        p = softmax([1.0, 2.0, 3.0])
        self.assertLess(p[0], p[1])
        self.assertLess(p[1], p[2])

    def test_numerically_stable_for_large_scores(self):
        p = softmax([1000.0, 1000.0, 1000.0])
        self.assertTrue(all(math.isfinite(x) for x in p))
        self.assertAlmostEqual(sum(p), 1.0, places=12)


class TestFit(unittest.TestCase):
    def test_recovers_base_rates_from_intercept_only(self):
        """Bez cech model musi odtworzyc czestosci bazowe."""
        X = [[1.0]] * 100
        y = [0] * 60 + [1] * 25 + [2] * 15
        w = fit(X, y, n_classes=3, epochs=3000, lr=0.5, l2=0.0)
        p = predict_proba(w, [1.0])
        self.assertAlmostEqual(p[0], 0.60, places=2)
        self.assertAlmostEqual(p[1], 0.25, places=2)
        self.assertAlmostEqual(p[2], 0.15, places=2)

    def test_learns_a_separating_feature(self):
        X = [[1.0, -1.0]] * 50 + [[1.0, 1.0]] * 50
        y = [2] * 50 + [0] * 50
        w = fit(X, y, n_classes=3, epochs=2000, lr=0.5, l2=0.0)
        self.assertGreater(predict_proba(w, [1.0, 1.0])[0], 0.9)
        self.assertGreater(predict_proba(w, [1.0, -1.0])[2], 0.9)

    def test_weight_matrix_shape(self):
        w = fit([[1.0, 0.5]] * 10, [0] * 10, n_classes=3, epochs=10)
        self.assertEqual(len(w), 3)
        self.assertEqual(len(w[0]), 2)

    def test_l2_shrinks_weights(self):
        X = [[1.0, -1.0]] * 50 + [[1.0, 1.0]] * 50
        y = [2] * 50 + [0] * 50
        loose = fit(X, y, n_classes=3, epochs=500, lr=0.5, l2=0.0)
        tight = fit(X, y, n_classes=3, epochs=500, lr=0.5, l2=1.0)
        self.assertLess(
            sum(abs(v) for row in tight for v in row),
            sum(abs(v) for row in loose for v in row),
        )


class TestPredictProba(unittest.TestCase):
    def test_sums_to_one(self):
        w = [[0.1, 0.2], [0.3, -0.1], [-0.2, 0.05]]
        self.assertAlmostEqual(sum(predict_proba(w, [1.0, 0.7])), 1.0, places=12)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_model -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.model'`

- [ ] **Step 3: Napisz implementację**

`backtest/model.py`:

```python
"""Wieloklasowa regresja logistyczna na czystym stdlib.

Pelny gradient (batch), bo 86 tysiecy wierszy i dwie cechy to dla Pythona
sekundy, a batch jest deterministyczny - zaden SGD, zadne tasowanie, zadnego
ziarna do pilnowania.

Model celowo ubogi: [1.0, elo_diff/400]. Dokladanie cech to osobny przyrost,
ktory [[12 - Ewaluacja i backtesting]] kaze prowadzic jako Model_base vs
Model_base+grupa z hipoteza postawiona z gory.
"""
import math
from typing import List, Sequence, Tuple


def softmax(scores: Sequence[float]) -> Tuple[float, ...]:
    top = max(scores)  # stabilnosc numeryczna
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return tuple(e / total for e in exps)


def predict_proba(weights: Sequence[Sequence[float]], x: Sequence[float]) -> Tuple[float, ...]:
    return softmax([sum(w * v for w, v in zip(row, x)) for row in weights])


def fit(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    n_classes: int = 3,
    lr: float = 0.5,
    epochs: int = 300,
    l2: float = 1e-4,
) -> List[List[float]]:
    """Zwraca macierz wag n_classes x n_features."""
    n = len(X)
    if n == 0:
        raise ValueError("pusty zbior treningowy")
    n_features = len(X[0])
    weights = [[0.0] * n_features for _ in range(n_classes)]

    for _ in range(epochs):
        grads = [[0.0] * n_features for _ in range(n_classes)]
        for xi, yi in zip(X, y):
            probs = predict_proba(weights, xi)
            for c in range(n_classes):
                err = probs[c] - (1.0 if c == yi else 0.0)
                if err == 0.0:
                    continue
                grow = grads[c]
                for j, v in enumerate(xi):
                    grow[j] += err * v
        for c in range(n_classes):
            for j in range(n_features):
                g = grads[c][j] / n
                if j > 0:  # nie regularyzujemy wyrazu wolnego
                    g += l2 * weights[c][j]
                weights[c][j] -= lr * g
    return weights
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_model -v
```

Oczekiwane: `OK`. Jeśli `test_recovers_base_rates_from_intercept_only` nie schodzi do 2 miejsc — zwiększ `epochs`, nie rozluźniaj `places`.

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/model.py tests/test_model.py && git commit -m "backtest: wieloklasowa regresja logistyczna na stdlib

Pelny gradient zamiast SGD - deterministyczny, bez ziarna do pilnowania,
a 86k wierszy przy dwoch cechach liczy sie w sekundach. Test przypina, ze
model bez cech odtwarza czestosci bazowe."
```

---

### Task 7: Bramka decyzyjna na EV_net

**Files:**
- Create: `backtest/gate.py`
- Create: `tests/test_gate.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `TAU: Dict[str, float]` — `{"pl_standard": 0.88, "betclic_degraded": 0.94, "betclic_notax": 1.00}`
  - `ev_gross(p: float, odds: float) -> float`
  - `ev_net(p: float, odds: float, tau: float) -> float`
  - `break_even_po(tau: float) -> float` — wymagany iloczyn `p × o` dla `EV_net = 0`
  - `threshold_po(tau: float, ev_min: float = 0.05) -> float` — wymagany `p × o` dla progu
  - `Decision` — `NamedTuple(bet: bool, ev_net: float, edge: float, reason: str)`
  - `decide(p_model: float, odds: float, tau: float, p_market: float, ev_min: float = 0.05, edge_min: float = 0.0) -> Decision`

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_gate.py`:

```python
import unittest

from backtest.gate import (
    TAU,
    Decision,
    break_even_po,
    decide,
    ev_gross,
    ev_net,
    threshold_po,
)


class TestTauRegimes(unittest.TestCase):
    def test_three_regimes_with_exact_values(self):
        self.assertEqual(TAU["pl_standard"], 0.88)
        self.assertEqual(TAU["betclic_degraded"], 0.94)
        self.assertEqual(TAU["betclic_notax"], 1.00)


class TestMB0001Regression(unittest.TestCase):
    """MB-0001 byl JEDYNYM kandydatem, ktory przeszedl bramke w calym pilocie
    27.08.2026 - i byl przegrywajacy. Stara regula liczyla EV na kursie brutto
    i dawala +6,15%. Po podatku to -6,59%. Ten test jest po to, zeby ta pomylka
    nie wrocila."""

    P = 0.550
    ODDS = 1.93

    def test_gross_reproduces_the_old_wrong_number(self):
        self.assertAlmostEqual(ev_gross(self.P, self.ODDS), 0.0615, places=6)

    def test_net_is_negative(self):
        self.assertAlmostEqual(
            ev_net(self.P, self.ODDS, TAU["pl_standard"]), -0.06588, places=5
        )

    def test_gate_rejects_it(self):
        d = decide(self.P, self.ODDS, TAU["pl_standard"], p_market=0.476)
        self.assertFalse(d.bet)

    def test_gate_would_have_accepted_it_without_tax(self):
        """Podatek jest jedyna przyczyna, dla ktorej ten bet jest przegrywajacy."""
        d = decide(self.P, self.ODDS, TAU["betclic_notax"], p_market=0.476)
        self.assertTrue(d.bet)


class TestThresholdTable(unittest.TestCase):
    """Tabela z HANDOFF.md sekcja A.1 - kazda wartosc musi sie odtworzyc."""

    def test_break_even(self):
        self.assertAlmostEqual(break_even_po(0.88), 1.13636, places=5)
        self.assertAlmostEqual(break_even_po(0.94), 1.06383, places=5)
        self.assertAlmostEqual(break_even_po(1.00), 1.00000, places=5)

    def test_threshold_at_five_percent(self):
        self.assertAlmostEqual(threshold_po(0.88, 0.05), 1.19318, places=5)
        self.assertAlmostEqual(threshold_po(0.94, 0.05), 1.11702, places=5)
        self.assertAlmostEqual(threshold_po(1.00, 0.05), 1.05000, places=5)


class TestDecide(unittest.TestCase):
    def test_returns_decision_namedtuple(self):
        self.assertIsInstance(decide(0.5, 2.0, 0.88, 0.5), Decision)

    def test_clear_value_passes(self):
        d = decide(0.70, 2.00, TAU["pl_standard"], p_market=0.50)
        self.assertTrue(d.bet)
        self.assertAlmostEqual(d.ev_net, 0.70 * 0.88 * 2.00 - 1, places=12)

    def test_edge_positive_but_ev_net_below_threshold_is_rejected(self):
        """Wlasnie ten przypadek przepuszczala stara regula."""
        d = decide(0.60, 1.90, TAU["pl_standard"], p_market=0.55)
        self.assertGreater(d.edge, 0.0)
        self.assertFalse(d.bet)
        self.assertIn("ev_net", d.reason)

    def test_negative_edge_is_rejected_before_ev(self):
        d = decide(0.40, 3.00, TAU["pl_standard"], p_market=0.50)
        self.assertFalse(d.bet)
        self.assertIn("edge", d.reason)

    def test_edge_is_model_minus_market(self):
        d = decide(0.55, 2.0, TAU["pl_standard"], p_market=0.50)
        self.assertAlmostEqual(d.edge, 0.05, places=12)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_gate -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.gate'`

- [ ] **Step 3: Napisz implementację**

`backtest/gate.py`:

```python
"""Bramka decyzyjna z [[22 - Zasady modelu]] paragraf 4-5.

Polski podatek 12% pobierany jest OD STAWKI, wiec wyswietlany kurs jest brutto:
100 zl po kursie 2.00 zwraca 176 zl, nie 200. Konsekwencja: bramka MUSI
dzialac na EV_net, nigdy na EV_gross.

Historia tej pomylki: MB-0001 byl jedynym kandydatem, ktory przeszedl przez
bramke w calym pilocie 27.08.2026. Mial EV_gross +6,15% i EV_net -6,59%.
Luka strukturalna polegala na tym, ze nota 22 paragraf 5 uzywala terminu
EV_net, ktorego paragraf 4 nigdzie nie definiowal - implementujacy musial
sobie dopowiedziec i dopowiedzial EV_gross.
"""
from typing import Dict, NamedTuple

TAU: Dict[str, float] = {
    "pl_standard": 0.88,      # Polska, podatek 12% od stawki
    "betclic_degraded": 0.94, # Betclic po degradacji promocji
    "betclic_notax": 1.00,    # Betclic bez podatku na SOLO
}


def ev_gross(p: float, odds: float) -> float:
    return p * odds - 1.0


def ev_net(p: float, odds: float, tau: float) -> float:
    return p * (tau * odds) - 1.0


def break_even_po(tau: float) -> float:
    """Iloczyn p*o, przy ktorym EV_net wynosi zero."""
    return 1.0 / tau


def threshold_po(tau: float, ev_min: float = 0.05) -> float:
    """Iloczyn p*o wymagany, zeby EV_net osiagnal prog."""
    return (1.0 + ev_min) / tau


class Decision(NamedTuple):
    bet: bool
    ev_net: float
    edge: float
    reason: str


def decide(
    p_model: float,
    odds: float,
    tau: float,
    p_market: float,
    ev_min: float = 0.05,
    edge_min: float = 0.0,
) -> Decision:
    edge = p_model - p_market
    value = ev_net(p_model, odds, tau)
    if edge <= edge_min:
        return Decision(False, value, edge, "edge {0:.4f} <= edge_min {1:.4f}".format(edge, edge_min))
    if value < ev_min:
        return Decision(False, value, edge, "ev_net {0:.4f} < ev_min {1:.4f}".format(value, ev_min))
    return Decision(True, value, edge, "ok")
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_gate -v
```

Oczekiwane: `OK`, w tym cała tabela progów z `HANDOFF.md` A.1.

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/gate.py tests/test_gate.py && git commit -m "backtest: bramka na EV_net z regresyjnym testem MB-0001

Podatek 12% idzie od stawki, wiec kurs z tabeli jest brutto. MB-0001 mial
EV_gross +6,15% i EV_net -6,59%, a mimo to jako jedyny przeszedl bramke
w pilocie 27.08. Test przypina te liczbe i cala tabele progow, zeby pomylka
nie wrocila."
```

---

### Task 8: CLV

**Files:**
- Create: `backtest/clv.py`
- Create: `tests/test_clv.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `clv_from_probs(p_open: float, p_close: float) -> float`
  - `clv_from_fair_odds(fair_open: float, fair_close: float) -> float`
  - `beat_close(p_open: float, p_close: float) -> bool`

**Uwaga na kierunek:** handoff definiuje `CLV = fair_open / fair_close − 1` na *cenach* po zdjęciu marży. Ponieważ `fair_odds = 1/p`, to jest równoważne `p_close / p_open − 1`. Dodatni CLV = rynek ruszył się w Twoją stronę. Obie funkcje muszą dawać identyczny wynik i test to przypina — pomylenie kierunku odwraca wniosek całego backtestu, a jest niewidoczne gołym okiem.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_clv.py`:

```python
import unittest

from backtest.clv import beat_close, clv_from_fair_odds, clv_from_probs


class TestClvFromProbs(unittest.TestCase):
    def test_known_value(self):
        self.assertAlmostEqual(clv_from_probs(0.50, 0.55), 0.10, places=12)

    def test_no_movement_is_zero(self):
        self.assertAlmostEqual(clv_from_probs(0.42, 0.42), 0.0, places=12)

    def test_market_moving_against_you_is_negative(self):
        self.assertLess(clv_from_probs(0.50, 0.45), 0.0)


class TestClvFromFairOdds(unittest.TestCase):
    def test_known_value(self):
        # p 0.50 -> 2.00, p 0.55 -> 1.8181...
        self.assertAlmostEqual(clv_from_fair_odds(2.0, 1.0 / 0.55), 0.10, places=12)


class TestBothFormsAgree(unittest.TestCase):
    """Pomylenie kierunku odwraca wniosek calego backtestu i jest niewidoczne
    golym okiem. Obie postacie musza dac identyczna liczbe."""

    def test_agreement_across_a_grid(self):
        for p_open in (0.05, 0.2, 0.35, 0.5, 0.65, 0.9):
            for p_close in (0.05, 0.2, 0.35, 0.5, 0.65, 0.9):
                with self.subTest(p_open=p_open, p_close=p_close):
                    self.assertAlmostEqual(
                        clv_from_probs(p_open, p_close),
                        clv_from_fair_odds(1.0 / p_open, 1.0 / p_close),
                        places=12,
                    )


class TestBeatClose(unittest.TestCase):
    def test_true_when_market_moved_your_way(self):
        self.assertTrue(beat_close(0.50, 0.55))

    def test_false_on_no_movement(self):
        self.assertFalse(beat_close(0.50, 0.50))


class TestRejectsInvalidInput(unittest.TestCase):
    def test_zero_probability_raises(self):
        with self.assertRaises(ValueError):
            clv_from_probs(0.0, 0.5)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_clv -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.clv'`

- [ ] **Step 3: Napisz implementację**

`backtest/clv.py`:

```python
"""Closing Line Value.

Definicja z handoffu: CLV = fair_open / fair_close - 1, liczone na CENACH po
zdjeciu marzy. Poniewaz fair_odds = 1/p, jest to rownowazne p_close / p_open - 1.
Dodatni CLV znaczy, ze rynek ruszyl sie w strone selekcji, ktora wzielismy.

CLV, a nie zysk, jest metryka pierwszorzedna, bo przy kilkuset zakladach zysk
jest zdominowany przez wariancje, a CLV mierzy jakosc ceny natychmiast.
"""


def _check(p: float, name: str) -> None:
    if not (0.0 < p < 1.0):
        raise ValueError("{0} musi lezec w (0,1), dostalem {1!r}".format(name, p))


def clv_from_probs(p_open: float, p_close: float) -> float:
    _check(p_open, "p_open")
    _check(p_close, "p_close")
    return p_close / p_open - 1.0


def clv_from_fair_odds(fair_open: float, fair_close: float) -> float:
    if fair_open <= 1.0 or fair_close <= 1.0:
        raise ValueError("fair odds musza byc > 1")
    return fair_open / fair_close - 1.0


def beat_close(p_open: float, p_close: float) -> bool:
    return clv_from_probs(p_open, p_close) > 0.0
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_clv -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/clv.py tests/test_clv.py && git commit -m "backtest: CLV w dwoch rownowaznych postaciach z testem zgodnosci

Pomylenie kierunku CLV odwraca wniosek calego backtestu i jest niewidoczne
golym okiem, wiec obie postacie - na prawdopodobienstwach i na fair odds -
sa zaimplementowane osobno i porownywane na siatce wartosci."
```

---

### Task 9: Walk-forward i raport końcowy

**Files:**
- Create: `backtest/walkforward.py`
- Create: `backtest/run_backtest.py`
- Create: `tests/test_walkforward.py`

**Interfaces:**
- Consumes: wszystko powyżej
- Produces:
  - `Selection` — `NamedTuple(match: Match, outcome: str, p_model: float, p_market_open: float, p_market_close: float, odds: float, ev_net: float, edge: float, clv: float)`
  - `seasons(matches: Sequence[Match]) -> List[str]` — posortowane etykiety
  - `features(elo_diff: float) -> List[float]` — `[1.0, elo_diff / 400.0]`
  - `run(matches: Sequence[Match], devig_method: str, tau: float, ev_min: float = 0.05, min_train_seasons: int = 2) -> List[Selection]`
  - `summarize(selections: Sequence[Selection]) -> Dict[str, object]`

**Struktura pętli — to jest miejsce, w którym leakage albo nie wchodzi, albo wchodzi:**

1. Jedna tabela Elo na cały przebieg, karmiona chronologicznie.
2. Dla sezonu docelowego `S`: zbierz cechy i etykiety **wszystkich** meczów wcześniejszych niż start `S`, wytrenuj model.
3. Idź przez mecze sezonu `S` chronologicznie. Dla każdego: **najpierw** odczytaj `elo.diff`, policz `P_model`, sprawdź bramkę na kursie „open"; **potem** wywołaj `elo.update`.
4. Model dla sezonu `S` jest zamrożony na czas całego `S` — nie douczamy go w trakcie sezonu, bo to byłoby użycie meczu z 15 maja do predykcji meczu z 20 sierpnia tylko przy przypadkowej kolejności.

- [ ] **Step 1: Napisz test, który ma nie przejść**

`tests/test_walkforward.py`:

```python
import datetime
import unittest

from backtest.gate import TAU
from backtest.loader import Match
from backtest.walkforward import Selection, features, run, seasons, summarize


def _m(year, month, day, home, away, ftr, o_open=(2.0, 3.5, 4.0), o_close=(2.0, 3.5, 4.0)):
    return Match(
        div="E0",
        date=datetime.date(year, month, day),
        home=home,
        away=away,
        ftr=ftr,
        o_open=o_open,
        o_close=o_close,
    )


def _synthetic(n_seasons=4):
    """Liga, w ktorej 'Silny' zawsze wygrywa u siebie z 'Slaby'."""
    out = []
    for i in range(n_seasons):
        year = 2014 + i
        for day in range(1, 21):
            out.append(_m(year, 9, day, "Silny", "Slaby", "H"))
            out.append(_m(year, 10, day, "Slaby", "Silny", "A"))
    return sorted(out, key=lambda m: (m.date, m.div, m.home, m.away))


class TestSeasons(unittest.TestCase):
    def test_sorted_and_unique(self):
        s = seasons(_synthetic(3))
        self.assertEqual(s, ["2014/15", "2015/16", "2016/17"])


class TestFeatures(unittest.TestCase):
    def test_intercept_first_and_scaled_elo(self):
        self.assertEqual(features(400.0), [1.0, 1.0])
        self.assertEqual(features(0.0), [1.0, 0.0])


class TestRun(unittest.TestCase):
    def test_returns_selections(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        self.assertTrue(all(isinstance(s, Selection) for s in sel))

    def test_first_seasons_are_never_bet(self):
        """Przy min_train_seasons=2 nic z dwoch pierwszych sezonow nie moze
        trafic do wynikow - to nie sa dane testowe, tylko treningowe."""
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0, min_train_seasons=2)
        for s in sel:
            self.assertGreaterEqual(s.match.date.year, 2016)

    def test_no_selection_uses_closing_odds_as_price(self):
        """Zaklad zawsze bierzemy po cenie 'open'. Zamkniecie sluzy WYLACZNIE
        do liczenia CLV - uzycie go jako ceny to leakage z [[11]]."""
        matches = _synthetic(4)
        sel = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.0)
        for s in sel:
            idx = ("H", "D", "A").index(s.outcome)
            self.assertAlmostEqual(s.odds, s.match.o_open[idx], places=12)

    def test_model_learns_the_synthetic_edge(self):
        """W lidze, gdzie Silny zawsze wygrywa, model musi to zauwazyc."""
        sel = run(_synthetic(5), "proportional", TAU["betclic_notax"], ev_min=0.0)
        home_silny = [s for s in sel if s.match.home == "Silny" and s.outcome == "H"]
        self.assertTrue(home_silny)
        self.assertGreater(sum(s.p_model for s in home_silny) / len(home_silny), 0.6)

    def test_clv_is_zero_when_line_does_not_move(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        for s in sel:
            self.assertAlmostEqual(s.clv, 0.0, places=9)

    def test_clv_is_positive_when_line_moves_toward_the_pick(self):
        """Zamkniecie krotsze na gospodarzu = rynek przyszedl po nas."""
        matches = []
        for i in range(4):
            year = 2014 + i
            for day in range(1, 21):
                matches.append(_m(year, 9, day, "Silny", "Slaby", "H",
                                  o_open=(2.00, 3.50, 4.00), o_close=(1.70, 3.80, 5.00)))
                matches.append(_m(year, 10, day, "Slaby", "Silny", "A",
                                  o_open=(4.00, 3.50, 2.00), o_close=(5.00, 3.80, 1.70)))
        matches.sort(key=lambda m: (m.date, m.div, m.home, m.away))
        sel = run(matches, "proportional", TAU["betclic_notax"], ev_min=0.0)
        picks = [s for s in sel if s.outcome in ("H", "A")]
        self.assertTrue(picks)
        self.assertGreater(sum(s.clv for s in picks) / len(picks), 0.0)


class TestSummarize(unittest.TestCase):
    def test_reports_core_fields(self):
        sel = run(_synthetic(4), "proportional", TAU["betclic_notax"], ev_min=0.0)
        out = summarize(sel)
        for key in ("n_bets", "mean_clv", "median_clv", "beat_close_rate", "by_season"):
            self.assertIn(key, out)

    def test_empty_input_does_not_crash(self):
        out = summarize([])
        self.assertEqual(out["n_bets"], 0)
```

- [ ] **Step 2: Uruchom test i potwierdź, że pada**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_walkforward -v
```

Oczekiwane: `ModuleNotFoundError: No module named 'backtest.walkforward'`

- [ ] **Step 3: Napisz implementację**

`backtest/walkforward.py`:

```python
"""Walk-forward: trenuj na przeszlosci, przewiduj kolejny sezon, przesun okno.

Tu wchodzi albo nie wchodzi leakage, wiec kolejnosc operacji jest scisla:

1. Jedna tabela Elo na caly przebieg, karmiona chronologicznie.
2. Dla sezonu S: trenuj na WSZYSTKICH meczach wczesniejszych niz start S.
3. W sezonie S, mecz po meczu: NAJPIERW odczytaj elo.diff i policz P_model,
   POTEM wywolaj elo.update.
4. Model jest zamrozony na caly sezon S. Douczanie w trakcie sezonu oznaczaloby,
   ze mecz z 15 maja wplywa na predykcje meczu z 20 sierpnia tylko dlatego,
   ze petla akurat tak poszla.

Zaklad bierzemy ZAWSZE po cenie 'open'. Zamkniecie sluzy wylacznie do liczenia
CLV - uzycie go jako ceny transakcji to leakage wymieniony wprost w [[11]].
"""
import statistics
from collections import defaultdict
from typing import Dict, List, NamedTuple, Sequence

from backtest.clv import clv_from_probs
from backtest.devig import METHODS
from backtest.elo import EloTable
from backtest.gate import decide
from backtest.loader import Match, season_of
from backtest.metrics import OUTCOMES, outcome_index
from backtest.model import fit, predict_proba


class Selection(NamedTuple):
    match: Match
    outcome: str
    p_model: float
    p_market_open: float
    p_market_close: float
    odds: float
    ev_net: float
    edge: float
    clv: float


def seasons(matches: Sequence[Match]) -> List[str]:
    return sorted({season_of(m.date) for m in matches})


def features(elo_diff: float) -> List[float]:
    return [1.0, elo_diff / 400.0]


def run(
    matches: Sequence[Match],
    devig_method: str,
    tau: float,
    ev_min: float = 0.05,
    min_train_seasons: int = 2,
) -> List[Selection]:
    devig = METHODS[devig_method]
    all_seasons = seasons(matches)
    by_season: Dict[str, List[Match]] = defaultdict(list)
    for m in matches:
        by_season[season_of(m.date)].append(m)

    elo = EloTable()
    train_X: List[List[float]] = []
    train_y: List[int] = []
    out: List[Selection] = []

    for i, season in enumerate(all_seasons):
        season_matches = by_season[season]
        if i < min_train_seasons:
            # sezon czysto treningowy: tylko karmimy Elo i zbior uczacy
            for m in season_matches:
                train_X.append(features(elo.diff(m.home, m.away)))
                train_y.append(outcome_index(m.ftr))
                elo.update(m.home, m.away, m.ftr)
            continue

        weights = fit(train_X, train_y, n_classes=3)  # zamrozony na caly sezon

        for m in season_matches:
            x = features(elo.diff(m.home, m.away))
            probs = predict_proba(weights, x)
            p_open = devig(m.o_open)
            p_close = devig(m.o_close)

            for idx, outcome in enumerate(OUTCOMES):
                d = decide(probs[idx], m.o_open[idx], tau, p_open[idx], ev_min=ev_min)
                if not d.bet:
                    continue
                out.append(
                    Selection(
                        match=m,
                        outcome=outcome,
                        p_model=probs[idx],
                        p_market_open=p_open[idx],
                        p_market_close=p_close[idx],
                        odds=m.o_open[idx],
                        ev_net=d.ev_net,
                        edge=d.edge,
                        clv=clv_from_probs(p_open[idx], p_close[idx]),
                    )
                )

            # dopiero teraz mecz staje sie przeszloscia
            train_X.append(x)
            train_y.append(outcome_index(m.ftr))
            elo.update(m.home, m.away, m.ftr)

    return out


def summarize(selections: Sequence[Selection]) -> Dict[str, object]:
    if not selections:
        return {
            "n_bets": 0,
            "mean_clv": 0.0,
            "median_clv": 0.0,
            "beat_close_rate": 0.0,
            "hit_rate": 0.0,
            "by_season": {},
            "by_league": {},
        }
    clvs = [s.clv for s in selections]
    wins = sum(1 for s in selections if s.match.ftr == s.outcome)

    def _group(key_fn):
        buckets: Dict[str, List[Selection]] = defaultdict(list)
        for s in selections:
            buckets[key_fn(s)].append(s)
        return {
            k: {
                "n": len(v),
                "mean_clv": statistics.mean(x.clv for x in v),
                "beat_close_rate": sum(1 for x in v if x.clv > 0) / len(v),
            }
            for k, v in sorted(buckets.items())
        }

    return {
        "n_bets": len(selections),
        "mean_clv": statistics.mean(clvs),
        "median_clv": statistics.median(clvs),
        "beat_close_rate": sum(1 for c in clvs if c > 0) / len(clvs),
        "hit_rate": wins / len(selections),
        "by_season": _group(lambda s: season_of(s.match.date)),
        "by_league": _group(lambda s: s.match.div),
    }
```

`backtest/run_backtest.py`:

```python
"""CLI backtestu CLV.

Uzycie:
    python3 -m backtest.run_backtest
    python3 -m backtest.run_backtest --tau betclic_notax --ev-min 0.05
    python3 -m backtest.run_backtest --devig shin --out reports/backtest-clv
"""
import argparse
import json
from typing import Optional, Sequence

from backtest.devig import METHODS
from backtest.gate import TAU
from backtest.loader import load_matches
from backtest.report_devig import best_method
from backtest.walkforward import run, summarize


def _markdown(summary, tau_name, devig_method, ev_min) -> str:
    lines = ["# Backtest CLV\n"]
    lines.append(
        "Rezim `{0}` (tau={1}), de-vig `{2}`, prog EV_net >= {3:.2f}\n".format(
            tau_name, TAU[tau_name], devig_method, ev_min
        )
    )
    lines.append(
        "> Cena wejscia to kolumny `PSH/PSD/PSA`, czyli zdjecie z budowy pliku\n"
        "> tygodniowego (zwykle piatek), a nie kurs otwarcia rynku. Mierzony CLV\n"
        "> to ruch piatek -> zamkniecie: konserwatywny podzbior prawdziwego CLV.\n"
    )
    lines.append("\n## Wynik zbiorczy\n")
    lines.append("| miara | wartosc |")
    lines.append("|---|---:|")
    lines.append("| zakladow | {0} |".format(summary["n_bets"]))
    lines.append("| sredni CLV | {0:+.4%} |".format(summary["mean_clv"]))
    lines.append("| mediana CLV | {0:+.4%} |".format(summary["median_clv"]))
    lines.append("| pobito zamkniecie | {0:.2%} |".format(summary["beat_close_rate"]))
    lines.append("| trafien | {0:.2%} |".format(summary["hit_rate"]))
    for title, key in (("sezonach", "by_season"), ("ligach", "by_league")):
        lines.append("\n## Po {0}\n".format(title))
        lines.append("| klucz | n | sredni CLV | pobito zamkniecie |")
        lines.append("|---|---:|---:|---:|")
        for k, v in summary[key].items():
            lines.append(
                "| {0} | {1} | {2:+.4%} | {3:.2%} |".format(
                    k, v["n"], v["mean_clv"], v["beat_close_rate"]
                )
            )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest CLV na 1X2")
    ap.add_argument("--tau", default="pl_standard", choices=sorted(TAU))
    ap.add_argument("--devig", default=None, choices=sorted(METHODS),
                    help="domyslnie metoda o najnizszym log-lossie na zamknieciu")
    ap.add_argument("--ev-min", type=float, default=0.05)
    ap.add_argument("--out", default=None, help="prefiks sciezki, bez rozszerzenia")
    args = ap.parse_args(argv)

    matches = load_matches()
    devig_method = args.devig or best_method(matches)
    print("mecze: {0} | de-vig: {1} | tau: {2}".format(len(matches), devig_method, args.tau))

    selections = run(matches, devig_method, TAU[args.tau], ev_min=args.ev_min)
    summary = summarize(selections)
    text = _markdown(summary, args.tau, devig_method, args.ev_min)

    if args.out:
        with open(args.out + ".md", "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(args.out + ".json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
        print("zapisano {0}.md i {0}.json".format(args.out))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Uruchom testy i potwierdź, że przechodzą**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest tests.test_walkforward -v
```

Oczekiwane: `OK`

- [ ] **Step 5: Uruchom cały pakiet testów**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && python3 -m unittest discover -s tests -v
```

Oczekiwane: wszystko `OK`, zero błędów.

- [ ] **Step 6: Uruchom backtest w trzech reżimach podatkowych**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && for t in pl_standard betclic_degraded betclic_notax; do python3 -m backtest.run_backtest --tau $t --out reports/backtest-clv-$t; done
```

Oczekiwane: trzy pary plików w `reports/`. Spodziewaj się **bardzo małej liczby zakładów przy `pl_standard`** — próg `p × o ≥ 1,19318` jest wysoki, a model ma dwie cechy. To nie jest usterka, tylko wynik: pokazuje, ile podatek zjada z przestrzeni decyzyjnej.

**Czas wykonania — zmierzony, nie zgadnięty:** pełny gradient kosztuje ok. 3,3 µs
na parę (wiersz × epoka). Przy 300 epokach i rosnącym zbiorze treningowym
(20 tys. wierszy w sezonie 2016/17 → 80 tys. w 2025/26) jeden przebieg zajmuje
**około 7–8 minut**, z czego ostatni sezon sam bierze ~80 s. To jest normalne
i nie ma czego optymalizować. Jeśli przebieg trwa wielokrotnie dłużej, sprawdź,
czy `fit` nie jest wywoływany wewnątrz pętli po meczach zamiast raz na sezon.

- [ ] **Step 7: Commit**

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0 && git add backtest/walkforward.py backtest/run_backtest.py tests/test_walkforward.py reports/ && git commit -m "backtest: walk-forward po sezonach i raport CLV

Model zamrozony na caly sezon docelowy, Elo aktualizowane dopiero po
odczytaniu predykcji, zaklad zawsze po cenie open. Test przypina, ze
zamkniecie nie trafia do ceny transakcji - to leakage wymieniony wprost
w nocie 11."
```

---

### Task 10 (opcjonalny): Kontrola własnego Elo względem club-football

**Files:**
- Create: `backtest/crosscheck_elo.py`
- Create: `tests/test_crosscheck_elo.py`

**Interfaces:**
- Consumes: `loader.{Match, read_rows, parse_date}`, `elo.EloTable`
- Produces:
  - `load_reference_elo(path: str) -> Dict[Tuple[str, str, str], Tuple[float, float]]` — klucz `(iso data, gospodarz, gość)`
  - `compare(matches: Sequence[Match], reference: Dict) -> Dict[str, float]` — zwraca `n_overlap`, `correlation`, `mean_abs_diff`

Wykonaj **tylko jeśli** wynik z Task 9 jest zaskakujący i chcesz wykluczyć własne Elo jako przyczynę. Na wspólnym podzbiorze 57 344 meczów porównuje własne `elo.diff` z `HomeElo − AwayElo` z `club-football-2000-2025/Matches.csv`. Korelacja poniżej ~0,8 oznacza, że jedno z dwóch Elo jest zepsute; korelacja bliska 1 przy wyraźnie lepszej predykcyjności cudzego jest sygnałem, że cudze widziało przyszłość.

Ścieżka pliku referencyjnego: `/Users/wojciechrudnicki/Code_projects/hazard_saas/data/raw/club-football-2000-2025/Matches.csv`, kolumny `MatchDate`, `HomeTeam`, `AwayTeam`, `HomeElo`, `AwayElo`. Join po `(data, gospodarz, gość)` trafia w 94,4% naszych meczów, ale Elo obu stron jest tylko przy 66,7%.

---

## Kryteria akceptacji

**Techniczne** (bramkują ukończenie):
- `python3 -m unittest discover -s tests` → wszystko `OK`
- `load_matches()` zwraca dokładnie 86 024 mecze, 22 ligi, `2014-07-25` → `2026-01-14`
- ECE zdevigowanego zamknięcia poniżej 0,01
- Test regresyjny MB-0001 daje `EV_net = −6,59%`
- Żadna selekcja nie używa kursu zamknięcia jako ceny wejścia

**Empiryczne** (to jest wynik, nie warunek zaliczenia):
- średni i medianowy CLV, ogółem i per sezon
- odsetek zakładów, które pobiły zamknięcie
- liczba zakładów w każdym z trzech reżimów τ

**Jak czytać wynik — ustalone z góry, żeby nie dopasować interpretacji do liczby:**

Dodatni CLV stabilny w większości sezonów = łańcuch działa i warto go rozszerzać na rożne. CLV wokół zera = model z dwiema cechami nie ma przewagi nad Pinnacle, co jest wynikiem oczekiwanym i mówi, że kolejny krok to cechy, a nie inny rynek. CLV wyraźnie ujemny i stabilny = model systematycznie wybiera złą stronę ruchu, co jest informacją mocniejszą niż zero i wymaga sprawdzenia znaku, zanim uzna się to za wynik.

**Czego ten backtest NIE rozstrzyga:** czy da się na tym zarobić. Mierzy jakość ceny, nie zysk. Zysk przy tej liczbie zakładów byłby zdominowany przez wariancję, a przy `pl_standard` prawdopodobnie w ogóle nie będzie zakładów do policzenia.
