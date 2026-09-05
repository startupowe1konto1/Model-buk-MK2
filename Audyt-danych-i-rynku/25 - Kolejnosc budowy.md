---
tags: [roadmap, build-order, mvp]
status: draft
date: 2026-09-05
---

# 25 — Kolejność budowy

> Rozwinięcie [[10 - Następne kroki]] o sekwencję. Kluczowa teza: **fazy 0–2 nie zawierają modelu sportowego i już są produktem.**

## Odwrócenie priorytetu: najpierw rynek, potem model

Drogą i kruchą rzeczą jest dobry model sportowy. Tanią i odporną jest: poprawny de-vig, porównanie tego samego marketu u wielu bukmacherów i oznaczenie rozbieżności.

Większość realnego value w rynkach detalicznych to nie „nasz Poisson coś wie" — to **jeden miękki bukmacher wyceniający linię 8% obok konsensusu**. Do tego nie potrzeba Dixon–Colesa. Potrzeba pokrycia kursów i ostrego benchmarku.

[[08 - Wersje modelu]] traktuje M0 jako baseline do pobicia. **M0 jest pierwszym produktem do wysyłki**; model sportowy to warstwa dokładana tam, gdzie test inkrementalny z [[12 - Ewaluacja i backtesting]] udowodni jej wartość — realistycznie w 2–3 komórkach `league × market`, nie globalnie.

## Faza −1 — audyt rynku (blokuje wszystko)

Jedno popołudnie ręcznej pracy. Dla Superbet / STS / Fortuna / Betclic zanotować per liga:

`czy market istnieje · na ilu meczach · marża · maks. stawka · jak wcześnie przed kickoffem`

dla: team SOT, team shots, team corners, cards, team totals.

Marża liczona **z podatkiem 12%** w środku. Wynik rozstrzyga, czy fazy 1+ mają sens. Szczegóły: [[23 - Krytyczna ocena wykonalności]] §9.

## Faza 0 — snapshotter (tydzień 1)

Odds Snapshotter + Raw Landing Zone + kanoniczne fixtures. **Nic więcej.**

To najwyższa wartość przy najniższym koszcie i jedyna rzecz, która **się kumuluje**. Dane historyczne kursów są drogie — The Odds API liczy 10× za historię. Jeśli snapshotujecie od dziś, za pół roku macie własny point-in-time zbiór z ruchem linii per bukmacher, którego nikt tanio nie kupi. Opóźnienie startu = albo płacenie za to później, albo brak możliwości backtestu CLV w ogóle.

Jeden skrypt + tania baza.

## Faza 1 — M0: de-vig i skaner rozbieżności

Silnik de-vig + skan rozbieżności między bukmacherami. **Wysyłalny bez żadnego modelu sportowego.**

## Faza 2 — frozen store + rozliczenia + CLV

Dopiero teraz da się zmierzyć, czy faza 1 jest prawdziwa.

**CLV jako ground truth, nie P&L.** Przy 4, 40 czy 400 betach zysk nie mówi nic — dominuje wariancja. Closing-line value mówi po ok. 100 betach, czy naprawdę bijecie rynek. Jest jednocześnie uczciwą metryką wewnętrzną i sprzedawalną („nasze typy biją closing line o X%").

## Faza 3 — point-in-time + feature engine + pierwszy model licznikowy

Team corners (kandydat z [[22 - Zasady modelu i rejestr testowych betów]] §8). Kryterium akceptacji: **poprawa CLV ponad M0**, nie sam hit rate.

## Faza 4 — lejek kosztowy + serving

Tiery budżetowe i powierzchnia produktowa z [[24 - Architektura silnika]] L6/L10.

## Dług do spłacenia od razu (30 minut)

- rozliczyć MB-0003 i MB-0004 (mecze z 03.09.2026);
- **unieważnić MB-0002** — wg własnej notatki kurs był nieaktualny w momencie zamrożenia, więc zgodnie z notą 22 nie jest to ważny frozen bet;
- dopisać kolumnę `ev_net_after_tax` do `experiments/test-bets.csv`;
- poprawić próg z [[21 - Pilot live 27-08-2026]] (`EV >= 5%`) na wartość po podatku.
