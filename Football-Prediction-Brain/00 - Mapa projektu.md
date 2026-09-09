---
tags: [moc, projekt, football-model]
status: active
---

# Mapa projektu

## Cel

Zbudować oparty na matematyce, statystyce i danych **globalny silnik wyszukiwania value w piłce nożnej**. System nie ma być ograniczony do jednej ligi ani jednego rynku. Premier League pozostaje środowiskiem walidacyjnym, ale architektura od początku ma obsługiwać wiele lig i wybierać mecze/rynki według jakości danych, przewidywalności, ceny i oczekiwanej wartości netto.

Pierwszy zakres modelowania: 1X2, Over/Under 2.5 i BTTS. **Exact score nie jest rynkiem docelowym.** Rozkład goli może pozostać wewnętrznym narzędziem matematycznym do agregacji innych rynków.

Równolegle badamy rynki zdarzeń o wysokiej powtarzalności, m.in. shots, shots on target, corners, cards, team totals i wybrane player props — wyłącznie tam, gdzie dane i kursy są wystarczająco dobre.

## Zasada nadrzędna: model suchy i reprodukowalny

Docelowy Model Buk **nie podejmuje decyzji na podstawie opinii, narracji ani „wydaje mi się”**. Każde `P_model`, fair odds, edge, EV i `BET/NO BET/NO PREDICTION` musi wynikać z:

- konkretnego zbioru danych dostępnego w cutoffie;
- jawnej transformacji cech i interakcji;
- wersjonowanego modelu matematycznego/statystycznego;
- ustalonej reguły niepewności i quality gate;
- ustalonego progu decyzyjnego, liczonego na `EV_net` po podatku ([[22 - Zasady modelu i rejestr testowych betów]] §4–5; korekta 09.09.2026 w [[21 - Pilot live 27-08-2026]]).

Ręczne pilotaże służą wyłącznie do odkrywania braków danych, hipotez i wymagań systemu. Nie są traktowane jako wynik produkcyjnego modelu.

## Przepływ wiedzy

`Globalny universe meczów → Data Quality Gate → tani screening → stan obu drużyn → kontekst → interakcje → modele zdarzeń → market scanner → fair odds/edge/EV → cost/risk filter → BET / NO BET / NO PREDICTION`

## Główne zasady

- nie skanujemy wszystkiego drogimi źródłami;
- koszt danych i predykcji jest częścią decyzji ekonomicznej;
- najpierw używamy danych cache/historycznych i tanich endpointów, a dopiero później płatnych szczegółów dla shortlisty;
- wszystkie informacje są point-in-time;
- każda cecha i interakcja musi przeżyć test OOS;
- `NO BET` i `NO PREDICTION` są pełnoprawnymi wynikami;
- pogoda jest przede wszystkim cechą wyjątków/extreme-environment, a nie stałym filarem każdego meczu;
- nie zaliczamy jako „trafionego modelu” hipotezy, dla której przed kickoffem nie zamroziliśmy rynku, kursu i prawdopodobieństwa.

## Rdzeń

- [[01 - Fundamenty matematyczne]]
- [[03 - Architektura modelu]]
- [[04 - Rejestr cech]]
- [[17 - Interakcje i wzorce powtarzalne]]
- [[11 - Czas predykcji i leakage]]
- [[12 - Ewaluacja i backtesting]]
- [[22 - Zasady modelu i rejestr testowych betów]]

## Dane, rynek i ekonomika

- [[05 - Źródła danych]]
- [[16 - Rejestr źródeł danych]]
- [[18 - Ekonomika pipeline i cost-aware scanning]]
- [[19 - Globalny zakres lig i quality gate]]
- [[20 - Global Data Coverage Matrix v1]]
- [[07 - Plan danych historycznych]]
- [[06 - Matematyka bukmacherska]]

## Pilotaże i eksperymenty

- [[21 - Pilot live 27-08-2026]] — pierwszy ręczny test procesu.
- `experiments/test-bets.csv` — kanoniczny, wersjonowany rejestr zamrożonych testowych betów.
- Excel `Model_Buk_Test_Bets_Tracker.xlsx` — roboczy dashboard i widok do ręcznego rozliczania wyników.

## Zarządzanie badaniami

- [[08 - Wersje modelu]]
- [[09 - Pytania badawcze]]
- [[10 - Następne kroki]]
- [[13 - Schemat danych]]
- [[14 - Decyzje i założenia]]
- [[15 - Teaser projektu]]
