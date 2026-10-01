# Model Buk na Railway

Wszystkie zadania, które dziś uruchamia Harmonogram zadań Windows (folder `\ModelBuk\`), przenosimy do **jednej
usługi Railway** z wolumenem. Strona skanera jest dostępna przez HTTPS pod adresem Railway, chroniona hasłem.
Komputer przestaje być potrzebny do zbierania danych; zostaje miejscem na pełną historię i badania.

Wszystkie polecenia poniżej są dla PowerShell, uruchamiane z katalogu repozytorium
`C:\Users\oskar\Documents\Model-Buk-Merged`.

---

## 1. Jak to działa

```
            Railway, projekt "model-buk"                                   PC (Windows)
 ┌───────────────────────────────────────────────────────┐        ┌──────────────────────────┐
 │ usługa "model-buk" (kontener Docker, 1 vCPU / 3 GB)    │        │ Model-Buk-Data (pełne)   │
 │  tini ─ entrypoint.py ─┬─ serwer strony :8080 ──HTTPS──┼──► przeglądarka (login+hasło)│
 │                        └─ supercronic (crontab) ──┐    │        │                          │
 │                             jobs/*.sh → python ... │    │        │ pull_archive.py ◄──────┐ │
 │  wolumen 5 GB  /data  ◄────────────────────────────┘    │        └────────────────────────┼─┘
 └──────────────┬────────────────────────────────────────┘                                  │
                │ archive_to_bucket.py (codziennie)                                         │
                ▼                                                                           │
        Storage Bucket "model-buk-archive" (S3) ───────────────────────────────────────────┘
```

* **Jeden kontener, jeden wolumen.** Wolumen na Railway można podpiąć tylko do jednej usługi, więc wszystkie
  zadania działają w jednym kontenerze. Harmonogramem jest `supercronic` (cron dla kontenerów), uruchamiany
  przez `entrypoint.py`, który dodatkowo serwuje stronę skanera i pilnuje, żeby harmonogram działał.
* **Czas polski.** Plik `crontab` ma `CRON_TZ=Europe/Warsaw`, a obraz ma `TZ=Europe/Warsaw`, więc godziny
  w harmonogramie i w logach są polskie (z automatyczną zmianą czasu).
* **Każde zadanie** idzie przez `jobs/run_job.sh`: jedna linia na uruchomienie w `/data/logs/<zadanie>.log`
  (tak jak robiły to skrypty `.ps1`), zadanie nigdy nie startuje drugi raz, gdy poprzednie jeszcze trwa,
  ma limit czasu, a po zakończeniu kontener zwalnia pamięć podręczną plików (patrz koszty).
* **Strona**: `https://<domena>.up.railway.app/` (ostatni skan), `/html/` (strony dzienne), `/csv/`,
  `/status` (wolne miejsce, ostatnia linia każdego logu). Login `SCANNER_PAGE_USER`, hasło
  `SCANNER_PAGE_PASSWORD`. Bez ustawionego hasła strona nie pokazuje niczego (503). `/healthz` jest bez hasła
  i nic nie zdradza (używa go Railway przy wdrożeniu).

### Zadania (czas polski)

| zadanie | kiedy | co robi | na PC było |
|---|---|---|---|
| `odds_collector` | co godzinę :05 | kursy przedmeczowe wszystkich lig (~30–65 zapytań) | OddsCollector |
| `prematch_collector` | :05, :25, :45 | mecze, składy, kontuzje, predykcje API | PrematchCollector |
| `closing_odds` | :12, :27, :42, :57 | kursy tuż przed meczem (`--max-requests 40 --reserve 2500`) | ClosingOdds |
| `scanner` | :15 i :45 | skaner value (tryb papierowy), strona i CSV | ValueScanner |
| `refresh_recent` | co godzinę :35 | zakończone mecze z ostatnich dni (`--mode dates`) | RefreshRecentMatches |
| `refresh_season` | 01:45 | pełny przegląd bieżącego sezonu (tryb lig) | RefreshCurrentSeason (przy logowaniu) |
| `history_build` | 02:15 | brakująca historia + przebudowa tabel **w wersji slim**; bez łączenia z football-data (to tylko badania, zostaje na PC) | NightlyHistoryBuild |
| `fit_live_models` | 03:00 | nocne dopasowanie modeli, **1 proces** (na PC 3) | FitLiveModels |
| `compact_odds` | 04:30 | retencja kursów (niżej) | — nowe |
| `compact_register` | 04:50 | retencja rejestru skanera + cotygodniowa kopia rejestru | — nowe |
| `archive_upload` | 05:20 | wysyłka archiwum do bucketu | — nowe |

Limit API-Football się nie zmienia: te same zadania, te same limity zapytań i rezerwy co na PC.

---

## 2. Co gdzie leży

| miejsce | co | rozmiar |
|---|---|---|
| **wolumen** `/data` (5 GB) | wszystko, czego zadania potrzebują na bieżąco: `api-football/raw` + `state.sqlite3` (surowa historia – z niej co noc budują się tabele), `api-football/tables/history.sqlite` **slim** (bez `lineup_players` i `player_match_stats`, których żadne zadanie na serwerze nie czyta), `api-football-odds/odds.sqlite3` (pełna historia zmian kursów meczów jeszcze nierozegranych i rozegranych w ostatnich 7 dniach), `odds_snapshots.sqlite3` (otwarcie / 72h…1h / zamknięcie, 30 dni), prematch, katalog meczów, rejestr skanera, cache modeli, `predictions/model_comparison.sqlite` (wagi puli), logi | start ~1,4 GB, docelowo ~3–3,5 GB |
| **bucket** `model-buk-archive` | to, co wyjeżdża z wolumenu: eksporty starej historii kursów (`archive/odds/exports/...`, gzip JSONL + manifest z liczbą wierszy i SHA-256), stare surowe skany kursów, stare legi „near-miss” rejestru, stary log prognoz, cotygodniowa kopia `scanner.sqlite` | ~6 MB dziennie (~2,5 GB rocznie) |
| **PC** `Model-Buk-Data` | pełna historia: `pull_archive.py` ściąga archiwum z bucketu i dokleja je do `odds.sqlite3` na PC (bez duplikatów) oraz do `archive/scanner/register_archive.sqlite`; tabela snapshotów kursów na PC jest pełna (bez kasowania); pełne `history.sqlite` z tabelami zawodników; wszystkie dane badawcze | jak dziś |

**Retencja (żeby zmieścić się w 5 GB):**

* `compact_odds_archive.py` – mecze, które zaczęły się ponad **7 dni** temu: ich wiersze `quotes` /
  `observations` idą do skompresowanego eksportu, liczby wierszy są sprawdzane po zapisie, dopiero potem wiersze
  znikają z bazy. Skaner potrzebuje pełnej historii z 7 dni (CLV i codzienny test typów zakładów) – krótsza
  retencja jest zablokowana w kodzie. Raz zrobiony eksport się nie powtarza; przerwanie w dowolnym momencie
  niczego nie gubi.
* `compact_scanner_register.py` – z rejestru znikają (do archiwum) tylko legi rozegranych ponad 7 dni temu
  meczów, które **nie** były value (w żadnym reżimie podatkowym), nie są w kuponie, nie mają CLV i są rozliczone
  (albo starsze niż 30 dni). Rozliczenia, CLV, kupony, legi value, skany i konfiguracje zostają na zawsze.
  Raz w tygodniu powstaje kopia całego rejestru (`archive/scanner/backups/`).
* `archive_to_bucket.py` – wysyła nowe pliki archiwum, sprawdza każdy (rozmiar i SHA-256), a lokalną kopię
  kasuje dopiero po 14 dniach i tylko wtedy, gdy plik na pewno jest w buckecie.

---

## 3. Koszty

Pomiary z 1.10.2026 na PC (czas CPU, szczytowa pamięć), przeliczone przez `deploy/railway/cost_estimate.py`
(ceny Hobby: RAM $10/GB-miesiąc, CPU $20/vCPU-miesiąc, naliczane co minutę; wolumen $0,15/GB; bucket $0,015/GB;
transfer $0,05/GB). Założenia celowo pesymistyczne: CPU na Railway 1,5× wolniejszy niż PC, każde
uruchomienie płaci pełne minuty w szczytowej pamięci; wariant B dolicza pamięć podręczną plików w czasie zadania.

| zadanie | uruchomień / mies. | zmierzone (PC) | $ CPU | $ RAM (A / B) |
|---|---:|---|---:|---:|
| scanner | 1440 | 28–43 s CPU, **1,2 GB** | 0,72 | 0,83 / 1,23 |
| odds_collector | 720 | ~1 s na 16 meczów (≈7 s / 21 s), 0,1–0,2 GB | 0,08 | 0,03 / 0,09 |
| closing_odds | 2880 | 0,6 s, 60 MB | 0,05 | 0,07 / 0,30 |
| prematch_collector | 2160 | 0,7 s, 60 MB | 0,03 | 0,04 |
| refresh_recent / refresh_season | 720 / 30 | 0,7 s / ~5 s | 0,01 | 0,01 |
| history_build (slim) | 30 | 116 s, 0,53 GB | 0,04 | 0,02 / 0,03 |
| fit_live_models (1 proces) | 30 | 21 s (75 s raz na 28 dni, gdy uczy boostery), 0,97 GB | 0,01 | 0,01 |
| compact_odds / compact_register / archive_upload | 30 każde | 10 s / 0,6 s / ~3 s | 0,00 | 0,00 |
| czyszczenie pamięci podręcznej po zadaniu | 8100 | ~0,15 s | 0,01 | 0,04 |
| stała obecność (strona + supercronic, ~45 MB) | cały czas | | | 0,45 |
| wolumen (~3,6 GB zajęte) | | | | 0,54 |
| bucket (~2,5 GB po roku) + transfer | | | | 0,07 |
| **razem** | | | **0,95** | **A: $3,05 / B: $3,76 miesięcznie** |

Przy założeniu, że Railway liczy 2,5× wolniej niż PC: $3,70–4,43. Z drugim projektem (~$1,1) cały
workspace wychodzi ~$4,2–4,9 miesięcznie (w najgorszym wariancie ~$5,5 – wtedy przyjdzie e-mail z alertem
$5); twardy limit $10 jest daleko. Prawdziwy koszt widać po kilku dniach: `& $rw usage`.

**Bezpieczniki (żeby nic nie przepaliło limitu $10):**

1. **Limity usługi**: `railway.json` ustawia 1 vCPU i 3 GB RAM (`limitOverride`). Zadania są
   jednowątkowe (`OMP_NUM_THREADS=1` itd.), więc 1 vCPU wystarcza; 3 GB mieści skaner (1,2 GB) równolegle
   z nocnym fitem (1 GB). Sprawdź po wdrożeniu w panelu: *Settings → Deploy → Replica Limits*.
2. **Limit czasu każdego zadania** (`timeout`): skaner 10 min (zwykle ~1 min), fit 30 min, historia 60 min,
   kolektory 10–15 min.
3. **Dzienny budżet czasu zadań**: gdy suma czasu wszystkich zadań w danym dniu przekroczy 4 h
   (`DAILY_JOB_BUDGET_SECONDS=14400`, normalnie ~1,5 h), kolejne uruchomienia są pomijane do północy. Nawet
   gdyby każde zadanie się zawieszało, koszt zadań nie przekroczy ~$6 miesięcznie.
4. **Pamięć podręczna plików**: Railway liczy pamięć kontenera razem z cache plików; po każdym zadaniu
   `drop_page_cache.py` zwalnia cache baz danych, więc między zadaniami płacimy tylko za ~45 MB.

Gdyby rzeczywiste koszty (`railway usage`) przekroczyły $4, kolejność cięć:
skaner w nocy (00:00–07:00) raz na godzinę zamiast co 30 min (−17 % kosztu skanera, ~$0,25);
`closing_odds` w nocy co 30 min; `prematch_collector` co 30 min zamiast co 20 min. Nocnego fitu nie trzeba
ograniczać (to ~$0,02 miesięcznie).

---

## 4. Wdrożenie krok po kroku

Wymagania: Railway CLI zainstalowane i zalogowane (`railway whoami`), Python z `app\.venv`.

```powershell
$rw = "$env:APPDATA\npm\railway.cmd"
cd C:\Users\oskar\Documents\Model-Buk-Merged
& $rw whoami
& $rw usage limit status          # twardy limit $10 i alert $5 powinny być widoczne
```

### 4.1 Projekt, usługa, wolumen

```powershell
& $rw init --name model-buk                         # nowy projekt, katalog repo zostaje z nim połączony
& $rw add --service model-buk                       # pusta usługa
& $rw service link model-buk                        # dalsze polecenia trafiają do tej usługi
& $rw volume add --service model-buk --mount-path /data
& $rw volume list                                   # zanotuj nazwę wolumenu (np. model-buk-volume) i rozmiar 5 GB
```

### 4.2 Bucket na archiwum

```powershell
& $rw bucket create model-buk-archive --region ams  # EU West (Amsterdam)
```

Podpięcie danych dostępowych bucketu do usługi – **najprościej w panelu**: projekt → bucket
`model-buk-archive` → *Credentials / Connect* → dodaj do usługi `model-buk` z presetem **AWS SDK**
(utworzy `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`,
`AWS_DEFAULT_REGION`). Albo z CLI, bez wyświetlania kluczy:

```powershell
$c = & $rw bucket credentials --bucket model-buk-archive --json | ConvertFrom-Json
$c.endpoint        | & $rw variable set AWS_ENDPOINT_URL      --stdin --service model-buk --skip-deploys
$c.accessKeyId     | & $rw variable set AWS_ACCESS_KEY_ID     --stdin --service model-buk --skip-deploys
$c.secretAccessKey | & $rw variable set AWS_SECRET_ACCESS_KEY --stdin --service model-buk --skip-deploys
$c.bucketName      | & $rw variable set AWS_S3_BUCKET_NAME    --stdin --service model-buk --skip-deploys
$c.region          | & $rw variable set AWS_DEFAULT_REGION    --stdin --service model-buk --skip-deploys
$c.urlStyle        | & $rw variable set AWS_S3_URL_STYLE      --stdin --service model-buk --skip-deploys
Remove-Variable c
```

### 4.3 Zmienne bez sekretów

```powershell
& $rw variable set MODEL_BUK_DATA=/data TZ=Europe/Warsaw SCANNER_PAGE_USER=oskar PORT=8080 --service model-buk --skip-deploys
```

(`MODEL_BUK_DATA`, `TZ` i `PORT` są też w obrazie; ustawione jawnie są widoczne w panelu.)

### 4.4 Sekrety – patrz rozdział 5 („Co musi zrobić Oskar”), krok 1 i 2.

### 4.5 Domena (HTTPS)

```powershell
& $rw domain --service model-buk --port 8080        # wypisze adres https://....up.railway.app
```

### 4.6 Wdrożenie kodu

Ścieżki danych: zadania dostają ścieżki jawnie (`--data-dir /data/...`), więc kontener działa także bez
łatki; po scaleniu pracy nad skanerem warto jednak nałożyć `deploy/railway/paths_refactor.patch`
(`git apply --3way deploy/railway/paths_refactor.patch`), żeby domyślne ścieżki we wszystkich skryptach
brały się z `MODEL_BUK_DATA`.

```powershell
app\.venv\Scripts\python deploy\railway\stage_context.py
#   -> staged ... in C:\Users\oskar\Documents\Model-Buk-Data\deploy\context (kilka MB zamiast całego repo)
& $rw up "C:\Users\oskar\Documents\Model-Buk-Data\deploy\context" --path-as-root --service model-buk --detach
& $rw logs --service model-buk --build             # budowa obrazu (~3-5 min)
& $rw logs --service model-buk                     # powinno być: "entrypoint: waiting for /data/seed.tar.gz ..."
```

Kontener czeka na dane: dopóki na wolumenie nie ma rozpakowanej paczki, **żadne zadanie nie startuje**
(nic nie utworzy pustych baz).

### 4.7 Przełączenie: wyłączenie zadań na PC

```powershell
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName OddsCollector
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName PrematchCollector
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName ClosingOdds
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName ValueScanner
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName RefreshRecentMatches
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName RefreshCurrentSeason
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName NightlyHistoryBuild
Disable-ScheduledTask -TaskPath "\ModelBuk\" -TaskName FitLiveModels
Get-ScheduledTask -TaskPath "\ModelBuk\" | Select-Object TaskName, State   # wszystkie: Disabled
```

Od tej chwili do startu serwera (ok. 20–30 min) nic nie zbiera kursów – dlatego paczkę budujemy dopiero teraz.

### 4.8 Paczka danych i wysyłka na wolumen

```powershell
app\.venv\Scripts\python deploy\railway\build_slim_dataset.py
#   -> C:\Users\oskar\Documents\Model-Buk-Data\deploy\seed.tar.gz (+ .sha256, seed_manifest.json)
#   wypisuje rozmiary i sprawdza, że na 5 GB zostaje >= 1,5 GB wolnego
& $rw volume files --volume <NAZWA-WOLUMENU> upload "C:\Users\oskar\Documents\Model-Buk-Data\deploy\seed.tar.gz" /seed.tar.gz
& $rw volume files --volume <NAZWA-WOLUMENU> upload "C:\Users\oskar\Documents\Model-Buk-Data\deploy\seed.tar.gz.sha256" /seed.tar.gz.sha256
& $rw volume files --volume <NAZWA-WOLUMENU> list /
```

Paczka zbudowana 1.10.2026: **0,75 GB** do wysłania, **1,45 GB** po rozpakowaniu (surowa historia 0,53 GB,
tabele slim 0,38 GB, kursy 0,31 GB, extras 0,09 GB, cache modeli 0,07 GB, reszta < 0,05 GB). Na wolumenie 5 GB
zostaje 3,55 GB wolnego (2,8 GB w trakcie rozpakowywania). Budowa trwa ~2,5 min.

**Kolejność ma znaczenie**: najpierw archiwum, potem plik `.sha256`. Kontener co 30 s sprawdza, czy oba pliki
są na miejscu i czy suma SHA-256 się zgadza (czyli wysyłka się skończyła), rozpakowuje paczkę do `/data`,
usuwa archiwum, zapisuje `/data/.seeded`, uruchamia harmonogram i od razu raz dopasowuje modele
(`fit_live_models` – skaner przyjmuje dopasowanie najwyżej 24-godzinne, więc nie czeka do 03:00). W logach:
`entrypoint: seed extracted {...}`, `supercronic started`, `initial fit_live_models started`.

Sprawdzone 1.10.2026 na kopii: paczka rozpakowana przez ten sam kod co w kontenerze, potem skaner, fit,
retencje i przebudowa historii slim z rozpakowanych surowych plików – wszystko bez błędów.

`railway volume files` działa na wolumenie, nie na kontenerze, więc `/seed.tar.gz` to korzeń wolumenu, czyli
`/data/seed.tar.gz` w kontenerze (tego nie dało się sprawdzić przed utworzeniem wolumenu – sprawdź po wysyłce:
`& $rw ssh --service model-buk -- ls -la /data`; gdyby plik był gdzie indziej, przenieś go:
`& $rw ssh --service model-buk -- mv <ścieżka>/seed.tar.gz <ścieżka>/seed.tar.gz.sha256 /data/`, najpierw archiwum,
potem sumę). Zapasowa droga wysyłki:
`railway ssh` + `scp` (klucz SSH: `& $rw ssh keys --help`), np.
`scp seed.tar.gz <domena>@ssh.railway.com:/data/seed.tar.gz`.

### 4.9 Sprawdzenie

```powershell
& $rw logs --service model-buk                      # linie zadań: "scanner [2026-..] exit=0 dur=..s scan=..."
& $rw ssh --service model-buk -- tail -n 2 /data/logs/scanner.log /data/logs/odds_collector.log
```

* w przeglądarce `https://<domena>/status` (login + hasło) – wolne miejsce na wolumenie i ostatnia linia
  każdego logu; po :15 / :45 pierwszy skan, po :05 kursy;
* `https://<domena>/` – strona skanera;
* następnego dnia rano: `history_build`, `fit_live_models`, `compact_odds`, `compact_register`,
  `archive_upload` w `/status` z `exit=0`; w buckecie pojawia się `archive/`;
* po kilku dniach: `& $rw usage` – rzeczywisty koszt.

### 4.10 Wycofanie (gdyby coś nie działało)

```powershell
Get-ScheduledTask -TaskPath "\ModelBuk\" | Enable-ScheduledTask    # PC znowu zbiera dane
& $rw down --service model-buk --yes                                # zatrzymuje wdrożenie (dane na wolumenie zostają)
```

---

## 5. Co musi zrobić Oskar

Tylko te kroki wymagają Ciebie. **Kluczy i haseł nie wklejaj do żadnego czatu** – poniższe polecenia
przekazują je prosto z Twojego komputera do Railway.

1. **Klucz API-Football** (czytany ze zmiennej użytkownika Windows, nie pojawia się na ekranie):

   ```powershell
   $rw = "$env:APPDATA\npm\railway.cmd"
   [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User") | & $rw variable set API_FOOTBALL_KEY --stdin --service model-buk --skip-deploys
   ```

2. **Hasło do strony** – losowe, zapisane też jako Twoja zmienna użytkownika, żebyś mógł je sobie odczytać:

   ```powershell
   $bytes = New-Object byte[] 24
   [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
   $pw = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
   [Environment]::SetEnvironmentVariable("SCANNER_PAGE_PASSWORD", $pw, "User")
   $pw | & $rw variable set SCANNER_PAGE_PASSWORD --stdin --service model-buk --skip-deploys
   Remove-Variable pw, bytes
   ```

   Odczyt hasła później (tylko u siebie): `[Environment]::GetEnvironmentVariable("SCANNER_PAGE_PASSWORD", "User")`.
   Login: `oskar`.

3. **Bucket** – kliknij w panelu preset **AWS SDK** dla usługi `model-buk` (krok 4.2), chyba że użyjesz
   wersji z CLI.

4. **Sprawdź limity**: `& $rw usage limit status` (twardy $10, alert $5) i w panelu usługi
   *Settings → Deploy → Replica Limits*: 1 vCPU / 3 GB.

5. **(Opcjonalnie) archiwum na PC** – żeby PC miał całą historię:

   ```powershell
   app\.venv\Scripts\python -m pip install boto3
   $c = & $rw bucket credentials --bucket model-buk-archive --json | ConvertFrom-Json
   [Environment]::SetEnvironmentVariable("AWS_ENDPOINT_URL", $c.endpoint, "User")
   [Environment]::SetEnvironmentVariable("AWS_ACCESS_KEY_ID", $c.accessKeyId, "User")
   [Environment]::SetEnvironmentVariable("AWS_SECRET_ACCESS_KEY", $c.secretAccessKey, "User")
   [Environment]::SetEnvironmentVariable("AWS_S3_BUCKET_NAME", $c.bucketName, "User")
   [Environment]::SetEnvironmentVariable("AWS_DEFAULT_REGION", $c.region, "User")
   [Environment]::SetEnvironmentVariable("AWS_S3_URL_STYLE", $c.urlStyle, "User")
   Remove-Variable c
   # potem, np. raz w tygodniu:
   cd C:\Users\oskar\Documents\Model-Buk-Merged\app
   .venv\Scripts\python scripts\pull_archive.py
   ```

   `pull_archive.py` niczego w buckecie nie zmienia ani nie kasuje; można go uruchamiać dowolnie często.
   Doklejanie do bazy rozdmuchuje `odds.sqlite3` na PC o ~0,1 GB dziennie (~35 GB rocznie; skompresowane
   eksporty to tylko ~1 GB rocznie). Jeśli to za dużo: `pull_archive.py --no-merge` (tylko pliki) i doklejenie
   później, kiedy będzie potrzebne (`pull_archive.py --no-download`).

---

## 6. Na co uważać (otwarte sprawy)

* **Nowy sezon 2027**: modele na żywo używają pliku `scanner/model_cache/hyperparameters_<sezon>_protocol_leagues.json`.
  Wybór hiperparametrów (`fit_live_models.py select`, 20–40 min) robimy na PC, a gotowy plik wgrywamy na wolumen
  (`railway volume files upload ... /scanner/model_cache/<plik>`); serwer tego nie liczy.
* **Świeżość modeli**: skaner przyjmuje dopasowanie modeli najwyżej 24-godzinne (ustawienie skanera). Fit
  działa o 03:00; jeśli się nie uda, do następnej nocy skaner pracuje bez modeli (`model=fit_stale` w logu
  skanera) – wtedy ręcznie: `& $rw ssh --service model-buk -- /opt/model-buk/deploy/railway/jobs/fit_live_models.sh`.
* **Rośnięcie danych**: `/status` pokazuje wolne miejsce; `compact_odds` codziennie wypisuje też `disk_free`.
  Największa pozycja to okno 13 dni pełnej historii kursów (~1–1,5 GB). Gdyby wolne miejsce spadło poniżej
  1 GB: zmniejsz `--snapshot-keep-days` w `jobs/compact_odds.sh` albo powiększ wolumen.
* **`api-football-extras`** jest w paczce na życzenie, ale żadne zadanie na serwerze go nie czyta – można go
  pominąć (`build_slim_dataset.py --no-extras`, −89 MB).
* **Pobieranie historii (`download_history.py`)** ma, jak na PC, limit 5000 zapytań na noc; gdy wszystko jest
  pobrane, kosztuje 0 zapytań.
* Strona i `/status` są za hasłem; nie ma panelu do zmiany ustawień ani do uruchamiania zadań z przeglądarki.
  Zadanie można uruchomić ręcznie: `& $rw ssh --service model-buk -- /opt/model-buk/deploy/railway/jobs/scanner.sh`.

---

## 7. Pliki

| plik | rola |
|---|---|
| `Dockerfile` | obraz: python 3.12-slim, tini, supercronic (wersja przypięta, SHA-256), pakiety z `app/requirements-server.txt` + catboost bez zależności graficznych |
| `railway.json` | budowa z Dockerfile, healthcheck `/healthz`, restart przy awarii, wymagany wolumen `/data`, limity 1 vCPU / 3 GB |
| `.dockerignore`, `.gitattributes` | co nie trafia do obrazu; skrypty `.sh` i `crontab` zawsze z końcami linii LF |
| `crontab` | harmonogram (czas polski) |
| `jobs/run_job.sh` | wspólny opakowujący skrypt: log, blokady, limit czasu, dzienny budżet |
| `jobs/*.sh` | po jednym na zadanie |
| `jobs/drop_page_cache.py` | zwalnia cache plików po zadaniu |
| `entrypoint.py` | serwer strony z hasłem, oczekiwanie na paczkę danych, nadzór nad supercronic |
| `stage_context.py` | mały katalog do `railway up` |
| `build_slim_dataset.py` | paczka danych startowych dla wolumenu |
| `cost_estimate.py` | szacunek kosztów z pomiarów |
| `paths_refactor.patch` | łatka: domyślne ścieżki we wszystkich skryptach z `MODEL_BUK_DATA` |
| `app/scripts/compact_odds_archive.py`, `compact_scanner_register.py`, `archive_to_bucket.py`, `pull_archive.py` | retencja i archiwum (logika w `app/src/model_buk/maintenance/`) |
