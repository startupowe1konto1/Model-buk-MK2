#!/usr/bin/env python3
"""
M0 - czy rozbieznosc miedzy bukmacherami daje sie zarobic?

Nota 25 stawia teze: "Wiekszosc realnego value w rynkach detalicznych to nie
'nasz Poisson cos wie' - to jeden miekki bukmacher wyceniajacy linie 8% obok
konsensusu." Faza 1 z tamtej noty to skaner takich rozbieznosci, wysylalny
BEZ zadnego modelu sportowego. Ten skrypt ja mierzy.

Pomiar idzie w dwoch krokach, bo pierwszy bez drugiego nic nie znaczy:

1. MECHANIZM - czy odchylenie bukmachera od konsensusu przewiduje ruch jego
   WLASNEJ ceny do zamkniecia. Odpowiada na pytanie "czy miekka cena wraca".
2. EKONOMIA - czy obstawianie tej miekkiej ceny zarabia po podatku. Odpowiada
   na pytanie, ktore jedyne ma znaczenie.

Krok 1 moze wyjsc mocno dodatnio i nic nie znaczyc: jesli cena otwarcia niesie
szum, to ruch liczony jako (zamkniecie - otwarcie) koreluje z tym szumem
UJEMNIE z samej konstrukcji. Dlatego werdykt zapada w kroku 2, na
zrealizowanych wynikach, a nie na ruchu ceny.

DWA SZCZEGOLY METODYCZNE, bez ktorych wynik bylby falszywy:

- Konsensus liczony LEAVE-ONE-OUT. Bukmacher nie moze byc czescia wlasnego
  punktu odniesienia, bo wtedy korelacja wychodzi mechanicznie.
- Marza zdejmowana OSOBNO dla kazdego bukmachera, na jego wlasnej trojce
  H/D/A. Porownywanie surowych kursow miedzy ksiegami o roznej marzy mierzy
  glownie marze.

Wejscie: tabela mozliwosci z `model_buk prepare-football-data-replay`.
Agregaty `market_average` i `market_max` sa wykluczane - to nie sa ksiegi,
u ktorych da sie postawic.

Uzycie:
    python3 10_m0_bookmaker_dispersion.py --input runtime/replay/opportunities-all.csv
"""
import argparse

import numpy as np
import pandas as pd

AGGREGATES = {"market_average", "market_max"}
TAX_REGIMES = {"tau=0.88 (PL)": 0.88, "tau=1.00 (bez podatku)": 1.00}
MIN_BOOKS_PER_LEG = 3


def load(path):
    """Nogi z pelna trojka H/D/A u danego bukmachera i z rozliczonym wynikiem."""
    d = pd.read_csv(path, usecols=[
        "fixture_id", "selection", "bookmaker", "decimal_odds",
        "closing_odds", "stratum_id", "settlement"])
    d = d[~d["bookmaker"].isin(AGGREGATES)]
    d = d.dropna(subset=["decimal_odds", "closing_odds"])
    d = d[(d["decimal_odds"] > 1.0) & (d["closing_odds"] > 1.0)]
    d = d[d["settlement"].isin(["win", "loss"])]
    # de-vig wymaga kompletu trzech nog jednej ksiegi
    counts = d.groupby(["fixture_id", "bookmaker"])["selection"].nunique()
    keep = counts[counts == 3].index
    return d.set_index(["fixture_id", "bookmaker"]).loc[keep].reset_index()


def devig_and_consensus(d):
    """Prawdopodobienstwa po zdjeciu marzy plus konsensus leave-one-out."""
    for kind, column in (("open", "decimal_odds"), ("close", "closing_odds")):
        inverse = 1.0 / d[column]
        total = inverse.groupby([d["fixture_id"], d["bookmaker"]]).transform("sum")
        d[f"p_{kind}"] = inverse / total
        d[f"overround_{kind}"] = total - 1.0

    leg = d.groupby(["fixture_id", "selection"])["p_open"]
    d["n_books"] = leg.transform("count")
    d = d[d["n_books"] >= MIN_BOOKS_PER_LEG].copy()
    d["consensus"] = (leg.transform("sum") - d["p_open"]) / (d["n_books"] - 1)

    d["deviation"] = d["p_open"] - d["consensus"]   # ujemne = kurs DLUZSZY niz konsensus
    d["move"] = d["p_close"] - d["p_open"]          # dodatnie = cena sie skrocila
    d["win"] = (d["settlement"] == "win").astype(int)
    return d


def net_return(d, tau):
    """Zwrot na jednostke stawki. Podatek schodzi OD STAWKI, wiec mnozy kurs."""
    return np.where(d["win"] == 1, d["decimal_odds"] * tau - 1.0, -1.0)


def confidence_interval(values):
    values = np.asarray(values, dtype=float)
    mean = values.mean()
    half = 1.96 * values.std(ddof=1) / np.sqrt(len(values))
    return mean, mean - half, mean + half


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="opportunities CSV")
    parser.add_argument("--deciles", type=int, default=10)
    args = parser.parse_args()

    d = devig_and_consensus(load(args.input))
    print(f"nog: {len(d)} | meczow: {d['fixture_id'].nunique()} | "
          f"bukmacherow: {d['bookmaker'].nunique()}")
    print(f"marza otwarcia {d['overround_open'].mean():.4f} | "
          f"zamkniecia {d['overround_close'].mean():.4f}")

    print("\n=== KONTROLA: ruch ceny bez warunkowania ===")
    print(f"  sredni ruch {d['move'].mean():+.5f} | mediana {d['move'].median():+.5f}")
    print("  (blisko zera znaczy, ze nie ma dryfu, ktory mozna pomylic z sygnalem)")

    d["decile"] = pd.qcut(d["deviation"], args.deciles, labels=False, duplicates="drop")

    print("\n=== KROK 1: MECHANIZM ===")
    print("decyl 0 = kurs najdluzszy wzgledem konsensusu")
    header = f"{'dec':>3s} {'n':>7s} {'odchylenie':>11s} {'ruch':>10s} {'trafien':>8s} {'konsensus':>10s}"
    print(header)
    for decile, group in d.groupby("decile"):
        print(f"{int(decile):3d} {len(group):7d} {group['deviation'].mean():+11.5f} "
              f"{group['move'].mean():+10.5f} {group['win'].mean():8.4f} "
              f"{group['consensus'].mean():10.4f}")
    print(f"\nkorelacja odchylenie ~ ruch: {d[['deviation', 'move']].corr().iloc[0, 1]:+.4f}")
    print("(ujemna = miekka cena wraca do konsensusu, zgodnie z teza noty 25)")

    print("\n=== KROK 2: EKONOMIA - najbardziej odstajacy decyl ===")
    top = d[d["decile"] == 0]
    for label, tau in TAX_REGIMES.items():
        mean, low, high = confidence_interval(net_return(top, tau))
        print(f"  {label:24s} {mean:+7.2%}  95% CI [{low:+7.2%}, {high:+7.2%}]")

    print("\n=== PO BUKMACHERACH (n >= 500, najbardziej odstajacy decyl) ===")
    print(f"{'bukmacher':20s} {'n':>6s} " +
          " ".join(f"{label:>26s}" for label in TAX_REGIMES))
    for bookmaker, group in top.groupby("bookmaker"):
        if len(group) < 500:
            continue
        cells = []
        for tau in TAX_REGIMES.values():
            mean, low, high = confidence_interval(net_return(group, tau))
            cells.append(f"{mean:+7.2%} [{low:+6.2%},{high:+6.2%}]")
        print(f"{bookmaker:20s} {len(group):6d} " + " ".join(cells))

    print("\n=== CZY OSTRZEJSZY PROG POMAGA? ===")
    print("(gdyby pod spodem byl prawdziwy sygnal, ostrzejszy filtr by go wzmocnil)")
    print(f"{'prog':>16s} {'n':>7s} {'sr.kurs':>8s} " +
          " ".join(f"{label:>26s}" for label in TAX_REGIMES))
    for quantile in (0.10, 0.05, 0.02, 0.01, 0.005):
        subset = d[d["deviation"] <= d["deviation"].quantile(quantile)]
        cells = []
        for tau in TAX_REGIMES.values():
            mean, low, high = confidence_interval(net_return(subset, tau))
            cells.append(f"{mean:+7.2%} [{low:+6.2%},{high:+6.2%}]")
        print(f"{'najnizsze ' + format(quantile * 100, 'g') + '%':>16s} {len(subset):7d} "
              f"{subset['decimal_odds'].mean():8.3f} " + " ".join(cells))


if __name__ == "__main__":
    main()
