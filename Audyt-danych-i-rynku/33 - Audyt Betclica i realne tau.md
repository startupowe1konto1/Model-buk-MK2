---
tags: [betclic, tax, audit, phase-minus-1, decision]
status: draft
date: 2026-09-25
---

# 33 — Audyt Betclica i realne τ

> Zamyka dług otwarty od 09.09 („audyt Betclica") i pytanie rozstrzygające
> z [[23 - Krytyczna ocena wykonalnosci]] §9. Źródło pierwotne: strona akcji
> specjalnej Betclic, zweryfikowana 25.09.2026.

## 0. Jedno zdanie

Promocja „Gra bez podatku" jest prawdziwa, ale **bezwarunkowa tylko do 1 000 zł
łącznych stawek**; powyżej trzeba ją **kupić obrotem na AKO, który kosztuje
dwa razy więcej niż sam podatek**. Realne τ dla operacji opartej na SOLO wynosi
więc **0,94, nie 1,00** — a przy 0,94 najlepszy decyl M0 daje **−9,71%**.

## 1. Warunki, dosłownie

| co | wartość |
|---|---|
| próg bezwarunkowy | **1 000 PLN** łącznej sumy stawek od startu akcji |
| kwalifikujący się AKO | **≥ 2 zdarzenia, każde o kursie ≥ 1,20** |
| wymagany udział | **≥ 50% WARTOŚCI STAWEK** (nie liczby kuponów) |
| okres rozliczeniowy | **12 miesięcy kroczących lub 5 000 ostatnich zakładów** |
| gdy warunek niespełniony | SOLO rozliczane **wskaźnikiem 0,94 (6%)** |
| wykluczenia | systemy, sporty wirtualne, symulowane gry karciane |
| wersja | „Bez Podatku 2.0", obowiązuje od **19.05.2026** |

To **potwierdza** przypuszczenie z [[21 - Pilot live 27-08-2026]] co do kształtu
(1 000 zł bez warunków, potem 50% AKO albo 6%). Nowe jest to, że udział liczy
się od **wartości stawek**, a nie od liczby kuponów — co przesądza arytmetykę
poniżej.

## 2. Ile kosztuje kupno τ = 1,00

Zmierzone na naszych danych (500 tys. nóg, 10 ksiąg, trójki 1X2):

| | |
|---|---:|
| marża (overround) na trójce 1X2 | **6,81%** (mediana 6,18%) |
| strata na jednej nodze (vig) | **6,38%** |
| **strata na AKO z 2 nóg** | **12,35%** |
| strata na AKO z 3 nóg | 17,94% |

Marża składa się na akumulatorze, więc **minimalny kwalifikujący się AKO
(2 zdarzenia) traci 12,35% stawki**.

Dwie strategie po przekroczeniu 1 000 zł:

| strategia | co się dzieje | koszt na złotówkę SOLO |
|---|---|---:|
| **A — same SOLO, τ=0,94** | 6% podatku od stawki | **6,00%** |
| **B — 50/50 SOLO/AKO, τ=1,00** | zero podatku, ale równa stawka na AKO traci swoją marżę | **~12,35%** |

> **Kupowanie zwolnienia z podatku kosztuje około dwa razy więcej niż sam
> podatek.** I to przy założeniu optymistycznym: minimalne AKO z dwóch nóg,
> dokładnie 50% udziału, brak marginesu bezpieczeństwa.

Strategia B miałaby sens wyłącznie wtedy, gdyby nogi AKO były same w sobie
dodatnio wartościowe. Nasze pomiary ([[32 - M0 i granica podatkowa]]) mówią,
że nie są.

**Wniosek: dla operacji opartej na zakładach pojedynczych realne τ = 0,94.**

## 3. Co to robi z wynikiem M0

Najlepszy decyl odchylenia od konsensusu, 50 060 zakładów:

| reżim | yield | 95% CI |
|---|---:|---|
| τ = 0,88 — standard PL | −15,47% | [−16,69%, −14,26%] |
| **τ = 0,94 — Betclic realnie** | **−9,71%** | **[−11,01%, −8,41%]** |
| τ = 1,00 — tylko pierwsze 1 000 zł | −3,95% | [−5,33%, −2,57%] |

**Wszystkie trzy istotnie ujemne.** Nawet scenariusz teoretycznie najlepszy —
τ=1,00, dostępny bezwarunkowo tylko na pierwszy tysiąc złotych — przegrywa
z przedziałem ufności całkowicie poniżej zera.

## 4. Werdykt

Pytanie rozstrzygające z [[23 - Krytyczna ocena wykonalnosci]] §9 brzmiało, czy
da się realnie postawić przy marży pozostawiającej osiągalne EV. **Odpowiedź
brzmi nie**, i to z trzech niezależnych powodów, z których każdy wystarcza:

1. **Zwolnienie podatkowe jest droższe od podatku** (12,35% wobec 6,00%).
2. **Przy realnym τ=0,94 najlepszy dostępny sygnał daje −9,71%** z przedziałem
   ufności daleko poniżej zera.
3. **Nawet przy τ=1,00 jest −3,95%**, też istotnie.

To zamyka ostatnią dźwignię, jaka została po [[32 - M0 i granica podatkowa]].
Nie ma reżimu podatkowego, w którym zmierzona nieefektywność tego rynku staje
się dodatnia.

### Konsekwencje dla reszty repozytorium

- `TAU["betclic_notax"] = 1.00` w `backtest/gate.py` należy czytać jako
  **„pierwsze 1 000 zł"**, a nie jako reżim operacyjny. Wariant `betclic_degraded`
  (0,94) jest tym realnym.
- Wiersz τ=1,00 w tabeli progów w [[21 - Pilot live 27-08-2026]] wymaga tego
  samego zastrzeżenia.
- Raporty CLV liczone przy τ=1,00 opisują scenariusz nieosiągalny w praktyce
  poza krótkim startem.

## 5. Czego ten audyt NIE rozstrzyga

- **Limitowanie kont** — nadal nieobecne w całym vaulcie ([[23]] §3). Audyt
  dotyczył podatku, nie tego, jak długo konto przeżyje.
- **Rynki inne niż 1X2.** Marża na rożnych u STS to 13,74% ([[27 - Faza -1
  wyniki empiryczne]]), czyli dwukrotność 1X2 — tam arytmetyka jest gorsza,
  nie lepsza.
- **Oferta Betclica na rynkach zdarzeniowych** i realne limity stawek.
  Do sprawdzenia, jeśli ktoś kiedyś wróci do rynków team props.

## Źródła

- betclic.pl — strona akcji specjalnej „Bez podatku" (regulamin akcji)
- igamingpolska.pl — „Betclic modyfikuje flagową promocję"
- jakdorobic.pl — „Gra Bez Podatku 2.0 w Betclic" (wrzesień 2026)
- legalsport.pl, meczyki.pl, bet.pl — potwierdzenie warunków i okresu rozliczeniowego
