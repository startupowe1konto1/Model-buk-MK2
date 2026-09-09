# Football Prediction Brain

Vault badawczy do budowy globalnego systemu predykcyjnego i selection engine dla rynków piłkarskich.

## Start

Otwórz ten folder jako vault w Obsidianie, a następnie zacznij od [[00 - Mapa projektu]]. Krótki stan projektu: [[15 - Teaser projektu]].

## Zasady projektu

- system szuka value, nie typuje każdego meczu;
- Premier League jest benchmarkiem startowym, nie ograniczeniem docelowym;
- exact score nie jest rynkiem docelowym;
- każda nowa cecha/interakcja musi poprawić wynik poza próbą;
- każda informacja ma znacznik czasu;
- model sportowy, benchmark rynkowy i decyzja bettingowa są oddzielnymi warstwami;
- częsty wzorzec nie jest przewagą bez kursu i holdoutu;
- koszt danych i API jest częścią wyniku ekonomicznego;
- decyzja liczona jest na `EV_net` po podatku, nie na `EV_gross`;
- szeroki screening jest tani, deep enrichment tylko dla shortlisty;
- pogoda jest głównie cechą wyjątków/extreme environment;
- prostszy model pozostaje, jeśli złożony nie daje mierzalnej poprawy.

## Główne mapy treści

- [[00 - Mapa projektu]]
- [[01 - Fundamenty matematyczne]]
- [[02 - Literatura i publikacje]]
- [[03 - Architektura modelu]]
- [[04 - Rejestr cech]]
- [[05 - Źródła danych]]
- [[06 - Matematyka bukmacherska]]
- [[07 - Plan danych historycznych]]
- [[08 - Wersje modelu]]
- [[09 - Pytania badawcze]]
- [[10 - Następne kroki]]
- [[11 - Czas predykcji i leakage]]
- [[12 - Ewaluacja i backtesting]]
- [[13 - Schemat danych]]
- [[14 - Decyzje i założenia]]
- [[15 - Teaser projektu]]
- [[16 - Rejestr źródeł danych]]
- [[17 - Interakcje i wzorce powtarzalne]]
- [[18 - Ekonomika pipeline i cost-aware scanning]]
- [[19 - Globalny zakres lig i quality gate]]

> [!warning] Odpowiedzialność
> Model ma służyć do badań probabilistycznych. Dodatnia wartość oczekiwana w backteście nie gwarantuje zysku, a zakłady wiążą się z ryzykiem utraty kapitału.
