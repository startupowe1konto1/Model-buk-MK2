# Jeden punkt wejścia dla zadań MK2 na tym komputerze (odpowiednik
# scripts\run_*.ps1 Oskara i deploy\railway\jobs\*.sh).
#
#   .\scripts\mk2\run_job.ps1 -Job scanner                 (domyślne argumenty zadania)
#   .\scripts\mk2\run_job.ps1 -Job odds_collector --max-requests 10   (dodatkowe argumenty idą do skryptu)
#
# Podział danych (scripts\mk2\env.ps1): paczka Oskara (MODEL_BUK_DATA) jest tylko
# czytana; wszystko, co zadania zbierają i zapisują, ląduje w MODEL_BUK_MK2_OUT:
#   odds_collector, closing_odds -> <MK2>\api-football-odds (nasze archiwum kursów)
#   scanner                      -> scan_mk2.py --live: kursy i kalendarz z <MK2>, historia z paczki,
#                                   rejestr w <MK2>\scanner\live
#   fit_live_models              -> czyta historię z paczki, fit zapisuje w <MK2>\model_cache
# Zadania prematch_collector, refresh_recent, refresh_season (profil "Full" w
# register_tasks.ps1) piszą do paczki (MODEL_BUK_DATA): używać tylko, gdy przestajemy
# przyjmować paczki od Oskara, bo kolejny import by je nadpisał.
#
# Domyślne argumenty są pod WSPÓLNY klucz API (ten sam limit 7 500/dzień co
# kolektory Oskara): rezerwa 3 000 jest wyższa od jego rezerw (1 500 / 2 500),
# więc przy kurczącym się limicie nasze zadania zatrzymują się pierwsze.
#
# Jedna linia na przebieg w <MK2>\logs\<zadanie>.log. Klucz API nie trafia do logu.

param(
    [Parameter(Mandatory = $true)][string]$Job,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "env.ps1")
$out = $env:MODEL_BUK_MK2_OUT
$odds = Join-Path $out "api-football-odds"
$directory = Join-Path $out "fixture-directory"
$history = Join-Path $env:MODEL_BUK_DATA "api-football\tables\history.sqlite"

$jobs = @{
    odds_collector     = @("scripts\collect_odds.py", "--data-dir", $odds, "--days-ahead", "1", "--max-requests", "60", "--reserve", "3000")
    closing_odds       = @("scripts\collect_closing_odds.py", "--data-dir", $odds, "--directory-dir", $directory, "--max-requests", "15", "--reserve", "3000")
    scanner            = @("scripts\mk2\scan_mk2.py", "--live", "--name", "live", "--reserve", "3000")
    fit_live_models    = @("scripts\fit_live_models.py", "--cache-dir", (Join-Path $out "model_cache"), "--history-db", $history, "fit", "--jobs", "3")
    prematch_collector = @("scripts\collect_prematch.py")
    refresh_recent     = @("scripts\refresh_current_season.py", "--mode", "dates")
    refresh_season     = @("scripts\refresh_current_season.py")
}
if (-not $jobs.ContainsKey($Job)) {
    Write-Error ("Nieznane zadanie '{0}'. Dostępne: {1}" -f $Job, (($jobs.Keys | Sort-Object) -join ", "))
    exit 64
}

$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $out "logs"
$log = Join-Path $logDir ("{0}.log" -f $Job)
New-Item -ItemType Directory -Force $logDir, $odds, $directory | Out-Null

$argv = @($jobs[$Job])
if ($Rest) { $argv += $Rest }

Push-Location $appRoot
try {
    if ($Job -eq "fit_live_models") {
        # Fit tylko na świeżej historii: inaczej manifest dostałby dzisiejszą datę, a modele nie znałyby
        # ostatnich wyników i skaner uznałby je za aktualne (scripts\mk2\history_age.py).
        $check = & $python "scripts\mk2\history_age.py" "--history-db" $history "--max-days" "3" 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
            Add-Content -Path $log -Value ("[{0}] exit=0 skipped: {1}; zaimportuj nowszą paczkę (import_data.py)" -f $stamp, $check.Trim()) -Encoding utf8
            exit 0
        }
        # Hiperparametry protokołu bierzemy z paczki Oskara: bez tego pliku fit po cichu wraca do wyborów
        # z baz badawczych (predictions\*.sqlite, live_models.default_hyperparameters) i liczy inne modele.
        $ownCache = Join-Path $out "model_cache"
        New-Item -ItemType Directory -Force $ownCache | Out-Null
        Get-ChildItem (Join-Path $env:MODEL_BUK_DATA "scanner\model_cache") -Filter "hyperparameters_*_protocol_leagues.json" -ErrorAction SilentlyContinue |
            Where-Object { -not (Test-Path (Join-Path $ownCache $_.Name)) } |
            ForEach-Object { Copy-Item $_.FullName (Join-Path $ownCache $_.Name) }
    }
    $output = & $python @argv 2>&1 | Out-String
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $LASTEXITCODE, $output.Trim()) -Encoding utf8
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
