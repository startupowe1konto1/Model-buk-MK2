---
tags: [decisions, assumptions, changelog]
---

# Decyzje i założenia

## Przyjęte

- Docelowy Model Buk jest **suchym, matematyczno-statystycznym systemem probabilistycznym**. Decyzja nie może zależeć od subiektywnego „wydaje mi się”.
- Każde `P_model` musi być reprodukowalne z wersji danych, cech, parametrów i kodu/modelu.
- Ręczne pilotaże oznaczamy jako `manual/research`; ich procenty są hipotezami procesu, nie walidacją produkcyjnego modelu.
- Każdy testowy bet musi zostać zamrożony przed kickoffem z co najmniej: timestampem, meczem, rynkiem/linią, kursem, `P_model`, wersją modelu i decyzją.
- Hipotezy bez zamrożonego `P_model` i kursu nie mogą być po fakcie zaliczane jako wygrane predykcje.
- Kanoniczny rejestr eksperymentów jest wersjonowany w `experiments/test-bets.csv`; Excel jest wygodnym widokiem roboczym/dashboardem.
- Premier League jest pierwszym środowiskiem walidacyjnym, nie docelowym ograniczeniem.
- Docelowo system ma obsługiwać szeroki universe lig i europejskich pucharów, a później więcej, jeśli dane/rynek uzasadniają koszt.
- Pierwsze rynki modelowe: 1X2, O/U 2.5, BTTS.
- Exact score jest wyłączony jako rynek docelowy/rekomendacja.
- Rynki zdarzeń shots/SOT/corners/cards/team totals/player props są kandydatami badawczymi.
- Każda para `league×market` przechodzi osobny quality gate.
- Nie wykonujemy pełnego drogiego skanu wszystkich potencjalnych zakładów; stosujemy lejek kosztowy.
- Koszt danych, kredytów API, compute i wykonania jest częścią ekonomiki zakładu.
- `NO BET` i `NO PREDICTION` są pełnoprawnymi wynikami.
- Dane sportowe i rynek pozostają rozdzielone w `PURE` i `MARKET`.
- Wszystkie dane są point-in-time i wersjonowane.
- Walidacja jest chronologiczna.
- Każda grupa cech i interakcja musi udowodnić incremental predictive value.
- H2H ani seria nie są samodzielnym dowodem przewagi.
- Pogoda ma niski priorytet w normalnych warunkach; badamy głównie environment/climate shocks i interakcje.

## Minimalna matematyka decyzji

Dla dziesiętnego kursu `o` i modelowego prawdopodobieństwa `p`:

- `fair_odds = 1 / p`
- `raw_implied_probability = 1 / o`
- `edge_pp = p - p_market_devig`
- `EV_gross = p * o - 1`
- `EV_net = p * (tau * o) - 1`, gdzie `tau` to mnożnik reżimu podatkowego

W produkcji `BET` nie wynika wyłącznie z dodatniego surowego EV. Musi również przejść kalibrację, niepewność, data quality, koszty wykonania i ustalony minimalny margines bezpieczeństwa.

**Decyzja podejmowana jest na `EV_net`, nie na `EV_gross`.** Polski podatek 12% od stawki daje `tau = 0.88`, co przesuwa break-even z `p * o = 1.0` na `1.13636`. Zakład z dodatnim `EV_gross` może być zakładem przegrywającym i w pilocie 27.08.2026 tak właśnie było.

## Ostrożne hipotezy, nie fakty

- niszowe ligi mogą być mniej efektywne, ale równocześnie mieć gorsze dane, wyższą marżę i niższą płynność;
- najlepszym źródłem zysku może być selection engine, a nie modelowanie każdego meczu;
- szeroki screening może być tani, jeśli opiera się na cache, batchowanych endpointach i lokalnych cechach;
- szczegółowe player/lineup/prop data powinny być pobierane dopiero dla shortlisty;
- market movement i exchange liquidity mogą zwiększyć jakość filtra;
- Asian Handicap może być bardziej efektywnym benchmarkiem niż 1X2 i należy go później zbadać jako osobny rynek.

## Otwarte decyzje

- minimalny `League/Market Quality Score`;
- dzienny i miesięczny budget cap na API/data;
- maksymalny koszt danych na jednego actionable candidate;
- dokładny cutoff modeli;
- metoda usuwania vig;
- pierwsze rynki zdarzeń do produkcyjnego testu;
- formalna metoda estymacji `P_model` dla SOT/shots/corners/cards;
- próg edge po uwzględnieniu niepewności i kosztów;
- finalny provider stack i licencje.

Aktualizuj tę notatkę po każdym rozstrzygnięciu z [[09 - Pytania badawcze]].
