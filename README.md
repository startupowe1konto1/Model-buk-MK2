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

## Następny kamień milowy

Najpierw należy wdrożyć wersjonowany kontrakt `event-time/knowledge-time`,
rolling-origin backtest i klasyczne baseline'y Poisson/Dixon–Coles. TimesFM 2.5
ma być challengerem prognozującym statystyki drużyny, a nie bezpośrednim
generatorem zakładów.
