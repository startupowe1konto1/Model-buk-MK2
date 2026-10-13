# Model Buk MK2 — dane, powtórki skanera i panel

Stan z 13.10.2026. Opisuje, skąd mamy dane, jak je przyjmujemy, co z nich wynika
i jak działa panel. Plan i decyzje: [MK2-KONCEPT.md](MK2-KONCEPT.md).

## 1. Dwa foldery danych

| Folder | Co w nim jest | Kto pisze |
|---|---|---|
| `Documents\Model-Buk-Data` | paczka danych od Oskara: historia meczów, archiwum kursów, kalendarz, rejestr skanera, modele, wyniki badań | nikt; podmienia ją tylko `import_data.py` |
| `Documents\Model-Buk-Data-MK2` | nasze: rejestry powtórek i przebiegów na żywo, nasze archiwum kursów i kalendarz, fit modeli, logi, eksport panelu | skrypty z `app/scripts/mk2/` |

Zasada: paczka jest tylko do odczytu, więc nowa paczka nie nadpisze naszych
wyników, a nasze przebiegi nie mieszają się z rejestrem Oskara. Bazy paczki
otwieramy z `mode=ro&immutable=1`.

## 2. Co jest w paczce z 12.10 (zweryfikowane)

Zip `Model-Buk-Data.zip`: 18 729 plików, 7,25 GB po rozpakowaniu, sha256
`bf7418ea…37ab`. Dane kończą się **1.10.2026 o 17:42 UTC**; jedyny nowszy plik
(4.10) to indeks `-shm` rejestru, bez danych. Wszystkie bazy przechodzą test
spójności.

| Obszar | Zawartość | Potrzebne skanerowi? |
|---|---|---|
| `api-football/tables/history.sqlite` | 221 644 mecze, 89 rozgrywek, sezony 2015–2026; ostatni zakończony mecz 30.09 01:05 UTC | tak (fit modeli, rozliczenia, podział na połowy) |
| `api-football-odds/odds.sqlite3` | 36 pobrań 28.09–1.10, 907 meczów ze 166 rozgrywek, 1,81 mln zmian kursów, 11 bukmacherów, Pinnacle dla 594 meczów | tak (kursy, uczciwa cena, CLV) |
| `fixture-directory/directory.sqlite3` | 4 537 meczów 27.09–8.10, 1 416 z wynikiem; statystyki tylko 32 meczów | tak (nazwy, wyniki, rozliczenia) |
| `api-football-prematch/` | 24 mecze, kontuzje 3, składy 2 | marginalnie |
| `scanner/` | rejestr Oskara (13 przebiegów, 5 konfiguracji), rejestr offline (8 przebiegów), fit modeli z 30.09 16:01 UTC | rejestry: do panelu; fit: do powtórek |
| `predictions/model_comparison.sqlite` | wagi puli modeli | tak (nocny fit) |
| `predictions/*.sqlite` (pozostałe), `market_test/`, `api-football-extras/` | wyniki badań (raporty „Modele 1X2 i gole”, „Model kontra rynek”), kontuzje i transfery | nie; tylko gdy brakuje pliku hiperparametrów |
| `deploy/seed.tar.gz` | paczka startowa na Railway z 1.10 17:42 | nie; w całości zawarta w paczce |

Najważniejsze ograniczenia:

- Komputer Oskara zbierał dane tylko ok. 15–21 UTC; przerwy po 18–20 h.
  Mało kursów tuż przed meczem, więc CLV jest słabe (w 263 z 455 pomiarów kurs
  zamknięcia równa się cenie z chwili decyzji).
- 1.10 o 17:42 Oskar zbudował paczkę startową serwera (Railway) i zatrzymał
  zadania na PC. Nowsze dane są najpewniej na serwerze.
- Model: jeden fit (30.09 16:01 UTC); skaner używa fitu do 24 h.
- Statystyk meczów jest za mało, by rozliczać rożne i kartki.
- 5 meczów z 30.09–1.10 jest w surowych plikach, ale nie w tabeli historii; brak
  J1 2026/27; football-data bez sezonu 2026/27 dla głównych lig (brak u źródła).

## 3. Nowa paczka od Oskara

```
cd app
.venv\Scripts\python scripts\mk2\import_data.py C:\Users\mkoni\Downloads\Model-Buk-Data.zip            # plan: co się zmieni
.venv\Scripts\python scripts\mk2\import_data.py C:\Users\mkoni\Downloads\Model-Buk-Data.zip --apply    # import
```

Plan pokazuje pliki nowe, zmienione i tylko lokalne, a dla zmienionych baz
liczbę wierszy w każdej tabeli. `--apply` rozpakowuje zmienione pliki do folderu
tymczasowego, sprawdza rozmiary i sumy CRC, odkłada zastępowane pliki do
`Model-Buk-Data._prev_<czas>` (zostaje ostatnia kopia), nie kasuje plików
spoza paczki i odmawia, gdy baza jest otwarta. Zapis importu:
`Model-Buk-Data\_import\import_<czas>.json`. Indeks `-shm` jest pomijany.

## 4. Skaner na tych danych

`scripts/mk2/scan_mk2.py` uruchamia skaner Oskara z zapisem do folderu MK2:

```
.venv\Scripts\python scripts\mk2\scan_mk2.py --as-of 2026-09-30T18:00:00Z                       # powtórka, ustawienia Oskara
.venv\Scripts\python scripts\mk2\scan_mk2.py --as-of 2026-09-30T18:00:00Z --profile config\mk2\profile_band_2leg.json
.venv\Scripts\python scripts\mk2\compare_replays.py <MK2>\scanner\<folder>\scanner.sqlite        # tabela porównawcza
```

Okna powtórek w tej paczce (kursy muszą mieć najwyżej 3 h, a 8 h dla meczów
odległych o ponad 48 h):

| Okno (UTC) | Modele |
|---|---|
| 29.09 15:00 – ok. 30.09 00:00 | brak (fit jest późniejszy) |
| 30.09 16:10 – 23:05 | tak |
| 1.10 16:12 – ok. 21:00 | nie (fit starszy niż 24 h) |

Rejestr przyjmuje jeden przebieg na moment decyzji, dlatego każda konfiguracja
ma swój folder (domyślnie `replay-<config_id>`).

### Wyniki powtórek (30.09 16:30, 18:00, 20:30, 22:30, 1.10 17:30, rozliczenie 1.10 19:40)

| | Ustawienia Oskara | 2 nogi, kurs łączny ≤ 2,00 |
|---|---:|---:|
| Nogi z wartością bez podatku (na przebieg) | 84 / 60 / 51 / 51 / 91 | to samo |
| Nogi z wartością przy podatku 12% | 0 | 0 |
| Kupony zaproponowane | 106 | 104 |
| Kupony przechodzące przy 12% | 0 | 0 |
| Rozliczone kupony: wygrane / przegrane | 6 / 26 | 8 / 19 |
| Mediana przewagi wiarygodnych nóg | 4,2% | 4,2% |
| Nogi z przewagą ≥ 6,6% (próg zera kuponu z 2 nóg) | 16% | 16% |
| … na dolnej granicy przedziału | 0% | 0% |

Wniosek: na tych danych skaner nie znajduje kuponu, który by się opłacał przy
podatku 12%. Ograniczenie kuponu do 2 nóg i kursu 2,00 tego nie zmienia; daje
kupony bardziej prawdopodobne (mediana szansy 46–60% zamiast 39–54%). W
rejestrze Oskara 37 kuponów przechodzi przy 12%, ale wszystkie pochodzą z
pierwszej konfiguracji z limitem przewagi 20% (później 10%).

## 5. Panel

Panel „Model Buk MK2” (link w rozmowie z Claude; prywatny, udostępnianie z menu
Udostępnij) pokazuje: przegląd z wnioskiem, kupony z nogami i wynikami, single,
przebiegi, eksperymenty z tabelą wyników skanera, stan danych i plan. Każda
liczba ma źródło w jednym z 9 zbiorów danych.

Odświeżenie po nowych przebiegach albo nowej paczce:

```
.venv\Scripts\python scripts\mk2\export_dashboard.py       # -> Documents\Model-Buk-Data-MK2\dashboard\*.json
```

potem napisz do Claude „odśwież panel”: wgra nowe pliki do tego samego linku.
Treść zakładki Plan to `app/config/mk2/status.json`. Źródło strony:
`app/dashboard_mk2/index.html`.
