"""The Excel workbook of the bet builders, written by the pipeline (user, 2026-10-27: "pamiętaj też, żeby
aktualizować wyniki w Excelu") to a path such as the OneDrive desktop, so it is always current.

Built in the style of the user's own experiment sheet (Arial, yellow input cells, dark header row, PLN
formats): every proposed bet builder with its chance, fair price, break-even price, estimated bookmaker
price, main or test, the app's settlement and the result at the estimated price, plus yellow columns for the user's own price, stake and status
with the same formulas as the user's sheet (payout after the 12% tax, profit, break-even chance, edge,
EV).  The yellow cells survive a refresh: they are read back from the previous file (matched by date,
match and events) and, where empty, prefilled from the app's ledger (bets and typed prices).  A sheet
"Skuteczność" carries the analysis of what came in and what did not, "Symulacja" the binomial sheet and
"Jak korzystać" a short how-to.  Formulas are written as formulas (fullCalcOnLoad).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ARIAL = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="17324D")
HEAD_FONT = Font(name=ARIAL, bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
BLOCK_FILL = PatternFill("solid", fgColor="EAF2F8")
PLN = '#,##0.00\\ "PLN";[Red]\\(#,##0.00\\ "PLN"\\)'
PCT = "0.0%"
DEC = "0.00"
FIRST_ROW = 10          # first data row of the "Zestawy" table
LAST_ROW = 5000         # formulas cover this many rows, so the user can append
WARSAW = dt.timezone(dt.timedelta(hours=1))
KIND = {"bezpieczny": "bezpieczniejszy", "z_wynikiem": "z wynikiem meczu", "meczowy": "cały mecz", "mieszany": "mieszany (rożne, faule, kartki)",
        "obie": "obie drużyny / drużyna + mecz"}
STATUS = {"W": "Wygrany", "P": "Przegrany", "Z": "Zwrot", "": "Oczekuje"}


def _font(bold: bool = False, size: int = 10, color: str | None = None) -> Font:
    return Font(name=ARIAL, bold=bold, size=size, color=color)


def _local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WARSAW)


def _leg_text(leg: dict) -> str:
    s = leg.get("opis", "")
    i = s.find(": ")
    return s[i + 2:] if i >= 0 else s


def _came(leg: dict) -> str:
    v = leg.get("wartosc")
    if v is None:
        return ""
    if leg.get("side") == "more":
        return f"{_leg_text(leg)}: {int(v)}" + (" goli" if leg.get("stat") == "goals" else "")
    if isinstance(v, str):
        return f"{_leg_text(leg)}: {v}"
    need = int(round(leg["line"] + 0.5)) if leg.get("line") is not None else None
    return f"{_leg_text(leg)}: {int(v)}" + (f" (potrzeba {need})" if need else "")


def kind_text(b: dict) -> str:
    return KIND.get(b.get("rodzaj"), "bezpieczniejszy") + (" · wyższe progi" if b.get("poziom") == "wyzej" else "")


def builder_key(b: dict) -> str:
    """The app's key of a builder (the ledger and the typed prices refer to it)."""
    return b.get("klucz") or f"{b['day']}|{b['fixture_id']}|{b.get('rodzaj', '')}" + ("|wyzej" if b.get("poziom") == "wyzej" else "")


def _row_key(date: object, match: object, events: object) -> tuple:
    d = date.date() if isinstance(date, dt.datetime) else date
    return (str(d), str(match or "").strip(), str(events or "").strip())


def carry_over(path: Path) -> dict[tuple, tuple]:
    """The yellow cells (price, stake, status) of the previous workbook, by date, match and events."""
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb["Zestawy"]
    except Exception:
        return {}
    out: dict[tuple, tuple] = {}
    for row in ws.iter_rows(min_row=FIRST_ROW, max_col=16, values_only=True):
        if not row or row[3] is None:
            continue
        price, stake, status = row[13], row[14], row[15]
        if price is None and stake is None and status is None:
            continue
        out[_row_key(row[1], row[3], row[5])] = (price, stake, status)
    return out


def write_workbook(path: Path, picks_out: dict, made: str, analysis: dict | None = None, ledger: dict | None = None) -> int:
    rows = []
    for day in sorted(picks_out):
        for b in picks_out[day].get("bb", []):
            rows.append(b)
    rows.sort(key=lambda b: (b["start"], b["mecz"], b.get("rodzaj", ""), b.get("poziom", "")))
    ledger = ledger or {}
    prices: dict[str, float] = ledger.get("kursy", {})
    bets: dict[str, dict] = {z["bb"]: z for z in ledger.get("zaklady", []) if z.get("bb")}
    kept = carry_over(path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Zestawy"
    ws["A1"] = "Typometr: bet buildery"
    ws["A1"].font = _font(True, 14)
    ws["A2"] = (f"Stan z {_local(made).strftime('%d.%m.%Y %H:%M')}; plik odświeża się przy każdym przebiegu. Żółte pola są Twoje "
                "(kurs z Superbet, stawka, status) i zostają po odświeżeniu; kursy i zakłady wpisane w aplikacji wchodzą tu same.")
    ws["A2"].font = _font()
    for addr, text in (("A4", "Założenia"), ("F4", "Zestawy z aplikacji"), ("I4", "Twoje zakłady")):
        ws[addr] = text
        ws[addr].font = _font(True)
        ws[addr].fill = BLOCK_FILL
    ws["A5"], ws["C5"] = "Podatek od stawki", 0.12
    ws["A6"], ws["C6"] = "Stawka na papierze (zł)", 100
    for addr, fmt in (("C5", PCT), ("C6", '#,##0.00\\ "PLN"')):
        ws[addr].fill = INPUT_FILL
        ws[addr].number_format = fmt
    K, M, N, O, P, R, V = (f"{c}{FIRST_ROW}:{c}{LAST_ROW}" for c in ("K", "M", "N", "O", "P", "R", "V"))
    summary = [
        ("F5", "Główne rozliczone", "G5", f'=COUNTIFS({K},"Weszło",{V},"główny")+COUNTIFS({K},"Nie weszło",{V},"główny")', None),
        ("F6", "Główne: weszło", "G6", f'=COUNTIFS({K},"Weszło",{V},"główny")', None),
        ("F7", "Skuteczność głównych", "G7", '=IFERROR(G6/G5,"")', PCT),
        ("F8", "Główne przy szac. kursach", "G8", f"=SUMIFS({M},{V},\"główny\")", PLN),
        ("I5", "Rozliczone", "J5", f'=COUNTIF({P},"Wygrany")+COUNTIF({P},"Przegrany")+COUNTIF({P},"Zwrot")', None),
        ("I6", "Postawione", "J6", f'=SUMIF({P},"Wygrany",{O})+SUMIF({P},"Przegrany",{O})+SUMIF({P},"Zwrot",{O})', PLN),
        ("I7", "Zysk / strata", "J7", f"=SUM({R})", PLN),
        ("I8", "ROI", "J8", '=IFERROR(J7/J6,"")', PCT),
    ]
    for la, label, va, formula, fmt in summary:
        ws[la] = label
        ws[la].font = _font()
        ws[va] = formula
        ws[va].font = _font(True)
        if fmt:
            ws[va].number_format = fmt
    headers = ["ID", "Data", "Godzina", "Mecz", "Drużyna", "Zdarzenia", "Rodzaj", "Szansa (model)", "Uczciwy kurs", "Opłaca się od",
               "Wynik wg aplikacji", "Co padło", "Przy szac. kursie", "Twój kurs", "Stawka", "Status", "Zwrot", "Zysk / strata",
               "Próg BE", "Przewaga", "EV", "Typ", "Szac. kurs"]
    hr = FIRST_ROW - 1
    for i, h in enumerate(headers, 1):
        c = ws.cell(hr, i, h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in range(FIRST_ROW, LAST_ROW + 1):
        ws[f"M{r}"] = f'=IF(OR(W{r}="",K{r}=""),"",IF(K{r}="Weszło",$C$6*((1-$C$5)*W{r}-1),-$C$6))'
        ws[f"Q{r}"] = f'=IF(OR(P{r}="",P{r}="Oczekuje",O{r}=""),"",IF(P{r}="Wygrany",O{r}*(1-$C$5)*N{r},IF(P{r}="Zwrot",O{r},0)))'
        ws[f"R{r}"] = f'=IF(Q{r}="","",Q{r}-O{r})'
        ws[f"S{r}"] = f'=IFERROR(1/((1-$C$5)*N{r}),"")'
        ws[f"T{r}"] = f'=IF(OR(N{r}="",H{r}=""),"",H{r}-S{r})'
        ws[f"U{r}"] = f'=IF(OR(N{r}="",O{r}=""),"",O{r}*(H{r}*(1-$C$5)*N{r}-1))'
        for col, fmt in (("M", PLN), ("Q", PLN), ("R", PLN), ("U", PLN), ("S", PCT), ("T", PCT), ("N", DEC), ("O", '#,##0.00\\ "PLN"')):
            ws[f"{col}{r}"].number_format = fmt
        for col in ("N", "O", "P"):
            ws[f"{col}{r}"].fill = INPUT_FILL
        for col in "ABCDEFGHIJKLMNOPQRSTUVW":
            ws[f"{col}{r}"].font = _font()
    for i, b in enumerate(rows, 1):
        r = FIRST_ROW + i - 1
        start = _local(b["start"])
        won = b.get("wynik")
        events = " + ".join(_leg_text(l) for l in b["legs"])
        values = [i, start.date(), start.strftime("%H:%M"), b["mecz"], b["druzyna"], events, kind_text(b), b["p_model"],
                  b.get("kurs_uczciwy"), b.get("kurs_min_z_podatkiem"),
                  "Weszło" if won == "wygrana" else "Nie weszło" if won == "przegrana" else "",
                  " · ".join(x for x in (_came(l) for l in b["legs"]) if x)]
        for j, v in enumerate(values, 1):
            ws.cell(r, j, v)
        # the yellow cells: what the user had in the previous file, else the app's bet, else the typed price
        key = builder_key(b)
        price, stake, status = kept.get(_row_key(start.date(), b["mecz"], events), (None, None, None))
        bet = bets.get(key)
        if price is None and bet:
            price, stake, status = bet.get("kurs"), bet.get("stawka"), STATUS.get(bet.get("wynik") or "", "Oczekuje")
        if price is None and prices.get(key):
            price = prices[key]
        for col, v in (("N", price), ("O", stake), ("P", status)):
            if v is not None:
                ws[f"{col}{r}"] = v
        ws[f"V{r}"] = "główny" if b.get("glowny", True) else "testowy"
        if b.get("kurs_szac"):
            ws[f"W{r}"] = b["kurs_szac"]
            ws[f"W{r}"].number_format = DEC
        ws[f"B{r}"].number_format = "DD.MM.YYYY"
        ws[f"H{r}"].number_format = PCT
        ws[f"I{r}"].number_format = DEC
        ws[f"J{r}"].number_format = DEC
    dv_status = DataValidation(type="list", formula1='"Oczekuje,Wygrany,Przegrany,Zwrot"', allow_blank=True)
    dv_odds = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="1.01", allow_blank=True)
    dv_stake = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0.01", allow_blank=True)
    for dv, col in ((dv_status, "P"), (dv_odds, "N"), (dv_stake, "O")):
        dv.add(f"{col}{FIRST_ROW}:{col}{LAST_ROW}")
        ws.add_data_validation(dv)
    widths = {"A": 5, "B": 11, "C": 8, "D": 30, "E": 20, "F": 60, "G": 24, "H": 11, "I": 10, "J": 9, "K": 13, "L": 50, "M": 15,
              "N": 10, "O": 11, "P": 11, "Q": 13, "R": 13, "S": 10, "T": 10, "U": 13, "V": 10, "W": 10}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = f"D{FIRST_ROW}"
    ws.auto_filter.ref = f"A{hr}:W{max(FIRST_ROW, FIRST_ROW + len(rows) - 1)}"

    # ---- Skuteczność: what came in and what did not, from the pipeline's analysis
    sk = wb.create_sheet("Skuteczność")
    sk["A1"] = "Skuteczność zestawów i co nie weszło"
    sk["A1"].font = _font(True, 14)
    a = analysis or {}
    sk["A2"] = (f"Rozliczone zestawy: {a.get('n', 0)}, weszło {a.get('trafione', 0)}, model zakładał {a.get('oczekiwane', 0)}."
                if a.get("n") else "Pierwsze wnioski po rozliczonych zestawach.")
    sk["A2"].font = _font()
    r = 4

    def head(cells: list[str]) -> None:
        nonlocal r
        for j, h in enumerate(cells, 1):
            c = sk.cell(r, j, h)
            c.font = HEAD_FONT
            c.fill = HEAD_FILL
        r += 1

    def line(cells: list, fmts: dict[int, str] | None = None) -> None:
        nonlocal r
        for j, v in enumerate(cells, 1):
            c = sk.cell(r, j, v)
            c.font = _font()
            if fmts and j in fmts:
                c.number_format = fmts[j]
        r += 1

    if a.get("grupy"):
        head(["Rodzaj (główne)", "Zestawy", "Weszło", "Skuteczność", "Model zakładał", "Przy szac. kursach"])
        for g in a["grupy"]:
            line([kind_text(g), g["n"], g["trafione"], g["trafione"] / g["n"], g["oczekiwane"] / g["n"], g.get("zysk_szac")],
                 {4: PCT, 5: PCT, 6: PLN})
        r += 1
    if a.get("nogi"):
        head(["Typ zdarzenia", "Nogi", "Weszło", "Skuteczność", "Model zakładał"])
        for g in a["nogi"]:
            line([g["typ"], g["n"], g["trafione"], g["trafione"] / g["n"], g["oczekiwane"] / g["n"]], {4: PCT, 5: PCT})
        r += 1
    if a.get("przyczyny"):
        head(["Dlaczego nie weszło", "Zestawów"])
        for g in a["przyczyny"]:
            line([g["przyczyna"], g["n"]])
        r += 1
    if a.get("wnioski"):
        head(["Wnioski"])
        for w in a["wnioski"]:
            line([w])
        r += 1
    if a.get("przegrane"):
        head(["Dzień", "Mecz", "Drużyna", "Rodzaj", "Szansa", "Co zawiodło", "Przyczyna"])
        for x in a["przegrane"]:
            what = "; ".join(f"{n.get('opis')}: padło {n.get('wartosc')}" + (f" (potrzeba {n['potrzeba']}, zabrakło {n['zabraklo']})" if "zabraklo" in n else "")
                             for n in x.get("nogi", []))
            line([x.get("dzien"), x.get("mecz"), x.get("druzyna"), kind_text(x), x.get("p_model"), what, x.get("przyczyna")], {5: PCT})
    for col, w in (("A", 26), ("B", 30), ("C", 18), ("D", 24), ("E", 14), ("F", 70), ("G", 24)):
        sk.column_dimensions[col].width = w

    # ---- Symulacja: the user's binomial sheet, pointed at this strategy
    sim = wb.create_sheet("Symulacja")
    sim["A1"] = "Symulacja strategii"
    sim["A1"].font = _font(True, 14)
    sim["A2"] = "Ile daje stała stawka na N zestawów przy danym kursie i skuteczności. Zmień żółte założenia."
    sim["A2"].font = _font()
    assumptions = [("Budżet", "B4", 1000, '#,##0.00\\ "PLN"', True), ("Liczba zestawów", "B5", 10, "0", True),
                   ("Stawka na zestaw", "B6", "=B4/B5", '#,##0.00\\ "PLN"', False), ("Średni kurs", "B7", 1.55, DEC, True),
                   ("Podatek od stawki", "B8", "=Zestawy!C5", PCT, False), ("Skuteczność (szacunek)", "B9", 0.76, PCT, True)]
    for i, (label, addr, val, fmt, inp) in enumerate(assumptions):
        sim[f"A{4 + i}"] = label
        sim[f"A{4 + i}"].font = _font()
        sim[addr] = val
        sim[addr].number_format = fmt
        sim[addr].font = _font()
        if inp:
            sim[addr].fill = INPUT_FILL
    outputs = [("Próg skuteczności (zero po podatku)", "=1/((1-B8)*B7)", PCT), ("Zysk na wygranym zestawie", "=B6*((1-B8)*B7-1)", PLN),
               ("Strata na przegranym", "=-B6", PLN), ("Minimalna liczba wygranych, żeby nie stracić", "=ROUNDUP(B5/((1-B8)*B7),0)", "0"),
               ("Oczekiwany wynik", "=B4*(B9*(1-B8)*B7-1)", PLN), ("Oczekiwane ROI", "=F8/B4", PCT),
               ("Odchylenie standardowe wyniku", "=B6*(1-B8)*B7*SQRT(B5*B9*(1-B9))", PLN), ("Szansa zakończenia na plusie", '=SUMIF(C15:C65,">0",B15:B65)', PCT)]
    for i, (label, formula, fmt) in enumerate(outputs):
        sim[f"E{4 + i}"] = label
        sim[f"E{4 + i}"].font = _font()
        sim[f"F{4 + i}"] = formula
        sim[f"F{4 + i}"].number_format = fmt
        sim[f"F{4 + i}"].font = _font(True)
    for j, h in enumerate(("Wygrane zestawy", "Prawdopodobieństwo", "Wynik całkowity"), 1):
        c = sim.cell(14, j, h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
    for k in range(0, 51):
        rr = 15 + k
        sim[f"A{rr}"] = k
        sim[f"B{rr}"] = f'=IF(A{rr}<=$B$5,COMBIN($B$5,A{rr})*$B$9^A{rr}*(1-$B$9)^($B$5-A{rr}),"")'
        sim[f"C{rr}"] = f'=IF(A{rr}<=$B$5,A{rr}*$B$6*(1-$B$8)*$B$7-$B$4,"")'
        sim[f"B{rr}"].number_format = "0.00%"
        sim[f"C{rr}"].number_format = PLN
        for col in "ABC":
            sim[f"{col}{rr}"].font = _font()
    for col, w in (("A", 34), ("B", 18), ("C", 18), ("E", 40), ("F", 16)):
        sim.column_dimensions[col].width = w

    # ---- Jak korzystać
    how = wb.create_sheet("Jak korzystać")
    lines = [
        "Jak korzystać z arkusza",
        "",
        "1. Arkusz „Zestawy” to wszystkie bet buildery zaproponowane przez Typometr, od najstarszego. Szansa, uczciwy kurs i „opłaca się od” są z modelu;",
        "   „Wynik wg aplikacji” i „Co padło” wypełniają się po meczu. Plik odświeża się sam przy każdym przebiegu programu.",
        "2. Gdy zagrasz zestaw w Superbet, wpisz w jego wierszu: Twój kurs, Stawka, Status (Oczekuje, a po meczu Wygrany / Przegrany / Zwrot).",
        "   Zwrot, Zysk, Próg BE, Przewaga i EV policzą się same. Te żółte pola zostają po odświeżeniu (dopasowanie po dacie, meczu i zdarzeniach).",
        "   Kursy wpisane w aplikacji i zakłady z aplikacji pojawiają się tu same, jeśli w pliku nie masz jeszcze własnego wpisu.",
        "3. Przewaga = szansa z modelu minus próg BE przy Twoim kursie. Dodatnia znaczy, że kurs jest co najmniej „opłaca się od”.",
        "4. Podsumowanie u góry: ile zestawów weszło i wynik teoretyczny „gdyby kurs = próg opłacalności” (100 zł każdy), a obok wynik Twoich zakładów.",
        "5. „Skuteczność” to analiza: które rodzaje i które zdarzenia wchodzą, dlaczego zestawy przegrywają, i wnioski.",
        "6. „Symulacja” pokazuje, czego spodziewać się po N zestawach przy stałej stawce: próg skuteczności, oczekiwany wynik i rozrzut.",
        "",
        f"Stan z {_local(made).strftime('%d.%m.%Y %H:%M')}. Podatek 12% od stawki (ustawa o grach hazardowych, art. 73–74).",
    ]
    for i, text in enumerate(lines, 1):
        how[f"A{i}"] = text
        how[f"A{i}"].font = _font(i == 1, 14 if i == 1 else 10)
    how.column_dimensions["A"].width = 130
    wb.calculation.fullCalcOnLoad = True
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return len(rows)
