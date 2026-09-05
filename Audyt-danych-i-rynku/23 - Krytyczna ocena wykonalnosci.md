---
tags: [critique, feasibility, economics, product]
status: draft
date: 2026-09-05
author: external review (Claude), na podstawie vaultu + transkryptu 02.09.2026
---

# 23 — Krytyczna ocena wykonalności

> Notatka zewnętrznej oceny. Nie zmienia ustaleń z [[14 - Decyzje i założenia]] — zgłasza problemy do rozstrzygnięcia.

## 1. Podatek 12% nie występuje w modelu ekonomicznym

`ev_gross` w `experiments/test-bets.csv`, wzory w [[06 - Matematyka bukmacherska]] §Fair odds i [[22 - Zasady modelu i rejestr testowych betów]] §4 są **przed podatkiem**. Przy polskim bukmacherze stawiasz 100, do gry idzie 88.

```text
EV_net   = p * o * 0.88 - 1
p_breakeven = 1.1364 / o
```

| bet | p_model | kurs | ev_gross (CSV) | EV po 12% |
|---|---|---|---|---|
| MB-0001 Monaco BTTS | 0.55 | 1.93 | +6.2% | **−6.6%** |
| MB-0002 Toulouse corners | 0.70 | 1.61 | +12.7% | **−0.8%** |
| MB-0003 Celta corners | 0.61 | 1.81 | +10.4% | **−2.9%** |
| MB-0004 Jagiellonia corners | 0.66 | 1.70 | +12.2% | **−1.3%** |

**Wszystkie cztery zamrożone testowe bety są ujemne po podatku.**

Konsekwencje:
- próg z [[21 - Pilot live 27-08-2026]] (`EV >= ok. 5%`) jest **poniżej break-even**; realny minimalny `EV_gross` to ok. **+13.6%**;
- przy kursie 1.90 potrzeba `p >= 59.8%` zamiast 52.6% — **+7.2 pp** dodatkowego edge;
- przy kursie 1.30 potrzeba `p >= 87.4%` — gałąź „faworyt na płasko" (Barcelona z transkryptu 1:28–1:31) jest **matematycznie martwa**, niezależnie od skali;
- podatek jest większym składnikiem niż wszystkie koszty danych z [[18 - Ekonomika pipeline i cost-aware scanning]] razem wzięte.

Do dopisania: 10% PIT od pojedynczej wygranej powyżej 2280 PLN (istotne dopiero przy większym banku).

## 2. Nieefektywność i jakość danych są antyskorelowane

Tam gdzie rynek jest miękki (Ekstraklasa corners, niższe ligi, niszowe propsy) — dane są rzadkie, definicje niestabilne, brak wiarygodnego SOT. Tam gdzie dane są świetne (PL 1X2) — rynek jest bliski efektywności, co potwierdza literatura z [[02 - Literatura i publikacje]].

[[19 - Globalny zakres lig i quality gate]] traktuje to jako problem selekcji. To jest **strukturalne zaciśnięcie**, nie tylko wybór — pasmo opłacalne jest wąskie, a nie „do znalezienia".

## 3. Limitowanie kont — brak w całym vaulcie i w transkrypcie

Superbet, STS, Fortuna i Betclic agresywnie limitują. Konsekwentne bicie rynków team-props kończy się obcięciem maks. stawki lub zamknięciem konta w tygodniach.

To nie jest ryzyko — to **pewność warunkowa na sukcesie**: im lepiej model działa, tym szybciej zostaje odcięty. Cała sekcja Kelly w [[06 - Matematyka bukmacherska]] jest fikcją, dopóki limity nie są zamodelowane jako ograniczenie decyzyjne.

## 4. Paradoks typsterski — produkt niszczy własną wartość

Rynki team-props są płytkie. 300 klientów grających „Toulouse corners 3.5+" w jednej godzinie rusza linię albo powoduje jej zdjęcie. Pojemność edge'u liczy się w tysiącach PLN; pojemność SaaS w tysiącach użytkowników. Te liczby się nie spotykają.

Transkrypt zawiera **oba modele biznesowe i nie zauważa, że są sprzeczne**:
- 1:27 — „gdyby to działało na 75%, to byśmy nie musieli tego nikomu sprzedawać";
- 1:25 — „jaka jest ta persona?" (pytanie zadane dwa razy, bez odpowiedzi).

Granie własnym kapitałem chce tajności i małej pojemności. Sprzedaż typów chce zasięgu i wolumenu. Wybór jednej z tych dróg zmienia architekturę.

## 5. Reframe: nie sprzedajecie alfy

Diagnoza Mateusza (49:40) jest trafna: „prawie w ogóle nie ma analizy". Gracze nie cierpią na brak predykcji — toną w nich. Brakuje im procesu, który mówi **nie**.

`NO BET / NO PREDICTION` z [[22 - Zasady modelu i rejestr testowych betów]] jest traktowane jako wewnętrzna zasada bezpieczeństwa. **To jest produkt.** Cała konkurencja z analizy Mateusza (52:06) mówi BET na wszystko.

Poprawka do wniosku konkurencyjnego: te serwisy nie są słabe dlatego, że biorą 15 czynników zamiast 200. Są słabe, bo **w ogóle nie porównują swojego prawdopodobieństwa z kursem**. To jest cała luka i jest tania do zbudowania.

Co realnie się sprzedaje bez konieczności bicia closing line:
- **kompresja researchu** — „z 40 meczów te 5 zasługuje na uwagę, i dlaczego";
- **weryfikowalne receipts** — publiczny, timestampowany, nieedytowalny rejestr z uczciwym rozliczeniem, łącznie z przegranymi (pomysł Oskara 1:34:01; protokół z noty 22 już to jest);
- **dyscyplina i staking** — nudne, i to decyduje czy gracz jest −5% czy −40%.

## 6. Uśrednianie kursów jest błędem

Transkrypt 1:56:17: „zbiór, zebrać kilku, zrobić średnią". Value wymaga **najlepszej ceny możliwej do zagrania** vs **ostrego benchmarku** (exchange/Pinnacle) do de-vigu. Uśrednianie miękkich booków miesza sygnał z tym, co próbujecie pobić.

[[12 - Ewaluacja i backtesting]] wspomina „kurs możliwy do realizacji", ale nigdzie nie ma tego jako zasady.

## 7. Legalność

- Scraping Flashscore jest niezgodny z ich ToS. Pytanie padło (2:04:38) i pozostało bez odpowiedzi. Do prywatnego backtestu — niski problem. Jako produkcyjna warstwa danych produktu na sprzedaż — realna ekspozycja.
- RapidAPI `flashscore4` to odsprzedawca tych samych danych; zmienia komu płacisz, nie to czy dane były licencjonowane.
- Sprzedaż predykcji bukmacherskich konsumentom w PL jest blisko regulacji reklamy hazardu. Warte 30 minut z kimś, kto zna temat, przed budową lejka.

## 8. Stan faktyczny projektu

25 plików, ~70 KB markdownu, 4 wiersze CSV, zero kodu, ~15 niezaznaczonych pozycji w [[10 - Następne kroki]].

- MB-0002/3/4 mają status `PENDING` mimo że mecze rozegrano 03.09.2026.
- MB-0002 wg własnej notatki miał nieaktualny kurs w momencie zamrożenia → zgodnie z notą 22 powinien być **unieważniony**, nie rozliczony.

Ryzykiem nie jest błędny model. Ryzykiem jest vault, który staje się coraz ładniejszy, podczas gdy jedyne pytanie rozstrzygające pozostaje niesprawdzone.

## 9. Pytanie rozstrzygające (blokuje wszystko inne)

> Czy da się realnie postawić team corners / SOT u polskiego bukmachera, na wystarczającej liczbie meczów, przy stawce która ma znaczenie, przy marży pozostawiającej osiągalne +14% EV — zanim konto zostanie zlimitowane?

Niezaznaczone w [[10 - Następne kroki]] jako „audit polskich bookmakerów". To jedno popołudnie ręcznej pracy w aplikacjach Superbet / STS / Fortuna / Betclic. Kosztuje zero. Wszystko z [[03 - Architektura modelu]], [[04 - Rejestr cech]], [[12 - Ewaluacja i backtesting]] i [[17 - Interakcje i wzorce powtarzalne]] jest w dół strumienia od tej odpowiedzi.
