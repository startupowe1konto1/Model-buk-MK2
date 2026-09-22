# Model Buk Merged

Połączone repozytorium badawcze backendu predykcji piłkarskich. Zachowuje historię
`startupowe1konto1/Model-buk-2.0` i `startupowe1konto1/Model-BUK-MK` oraz łączy:

- wieloligowy backtesting 1X2, Elo, de-vig i CLV;
- silnik rożnych EPL z walidacją chronologiczną;
- wieloligowy, wielorynkowy baseline v0.5 i integrację API-Football;
- jawny profil podatkowy, oddzielenie PURE probability od kursów i bezpieczną
  bramkę decyzji.

## Ważny status

Projekt jest przeznaczony wyłącznie do badań i paper bettingu. Nie ma obecnie
dowodu trwałej przewagi nad rynkiem. Szerokie rynki v0.5 domyślnie zwracają
`NO PREDICTION`; etykiety `BET` można włączyć tylko świadomie zmienną
`MODEL_BUK_ALLOW_RESEARCH_BETS=1` po przeprowadzeniu walidacji OOS.

Domyślny mnożnik podatkowy to `0.88`, czyli polski podatek 12% od stawki.
Można go jawnie zmienić przez `MODEL_BUK_TAX_MULTIPLIER`.

## Struktura

- `app/` — backend FastAPI, modele rogów, baseline wielorynkowy i dane live;
- `backtest/` — niezależny pipeline 1X2/Elo/CLV;
- `Football-Prediction-Brain/` — baza metodologiczna;
- `Audyt-danych-i-rynku/` — audyt źródeł danych i skrypty sond;
- `docs/AUDIT-2026-09-16.md` — wynik audytu obu repozytoriów;
- `docs/TIMESFM-IMPLEMENTATION-PLAN.md` — decyzja i plan eksperymentu TimesFM;
- `docs/TARGET-BACKEND-ARCHITECTURE.md` — architektura docelowa.
- `docs/CURRENT-DATA-PIPELINE.md` — kontrakt cyklicznej analizy danych bieżących.

## Uruchomienie testów aplikacji

```powershell
cd app
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m pytest -q
```

Dane i artefakty modeli nie są publikowane w Git ze względu na rozmiar i
licencje źródeł. Brak artefaktów nie powinien powodować awarii podczas importu:
API uruchamia się w stanie `NOT_READY`, dopóki wymagane wersjonowane zasoby nie
zostaną dostarczone.

## Zaimplementowany fundament danych bieżących

Backend zawiera teraz:

- point-in-time builder cech `build_asof_features`, który odcina informacje
  opublikowane po chwili decyzji;
- `CurrentDataPipeline`, spinający snapshoty, kontrolę świeżości, predyktor,
  selekcję maksymalnie N okazji oraz audytowalny manifest przebiegu;
- bramkę promocji modelu opartą o wyniki OOS względem rynku, kalibrację oraz
  przedziały ufności dla log-loss i rentowności netto.
- kanoniczny kontrakt importu meczów i kursów: stabilne identyfikatory,
  timestampy UTC, lineage, kontrolę leakage oraz kwarantannę błędnych rekordów.

Pipeline nie wymusza dziesięciu rekomendacji. Może zwrócić mniej albo zero,
jeżeli konserwatywny EV po podatku i kosztach wykonania nie przechodzi progu.

## Benchmark matematyczny goli

Warstwa badawcza zawiera obecnie trzy porównywalne modele count-data:

- Dixon–Coles z decay i korektą niskich wyników;
- dynamiczny GAS-Poisson aktualizowany dopiero po poznaniu wyniku;
- właściwy NB2 z efektami attack/defence, home advantage, L2 i uczoną
  dyspersją.

Modele są oceniane na przecięciu dokładnie tych samych spotkań za pomocą
log-loss, Brier, RPS i coverage. Nested walk-forward wybiera rodzinę i parametry
wyłącznie na wewnętrznych predykcjach OOF; w zewnętrznym foldzie uruchamiany jest
tylko zwycięzca. Kalibracja temperatury dla outer fold jest uczona wyłącznie na
inner OOF tego zwycięzcy. Surowe prawdopodobieństwa pozostają zapisane obok
skalibrowanych. Przedziały ufności mogą resamplować całe bloki, np. kolejkę,
dzień meczowy albo liga×sezon, zamiast zakładać niezależność każdego zakładu.

Skan paper/shadow można uruchomić z katalogu `app`:

```powershell
.venv\Scripts\python -m model_buk scan-current --config config/paper_scan.toml --mode live
```

Klucz jest pobierany wyłącznie z `API_FOOTBALL_KEY`. Ustawienia quota, podatku,
świeżości kursu i limitu rekomendacji znajdują się w
`app/config/paper_scan.toml`.

## Następny kamień milowy

Po dostarczeniu danych należy uruchomić nested benchmark per liga i rynek,
wybierać de-vig, próg EV oraz regułę top-N wewnątrz foldów i rozliczać ROI tylko
po wykonalnych kursach znanych przed cutoffem. Closing odds pozostają wyłącznie
benchmarkiem CLV. Potem model przechodzi do paper/shadow. TimesFM 2.5 pozostaje
challengerem prognozującym statystyki drużyny, a nie bezpośrednim generatorem
zakładów.
