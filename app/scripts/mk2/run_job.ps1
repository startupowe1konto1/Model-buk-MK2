# Jeden punkt wejścia dla zadań MK2 na tym komputerze (odpowiednik
# scripts\run_*.ps1 Oskara i deploy\railway\jobs\*.sh), z katalogiem danych i
# logiem pod MODEL_BUK_DATA (scripts\mk2\env.ps1), nie pod jego ścieżką.
#
#   .\scripts\mk2\run_job.ps1 -Job scanner                 (domyślne argumenty zadania)
#   .\scripts\mk2\run_job.ps1 -Job scanner --no-api        (dodatkowe argumenty idą do skryptu)
#   .\scripts\mk2\run_job.ps1 -Job odds_collector --max-requests 10
#
# Domyślne argumenty są pod WSPÓLNY klucz API (ten sam limit 7 500/dzień co
# kolektory Oskara): rezerwa 3 000 jest wyższa od jego rezerw (1 500 / 2 500),
# więc przy wyczerpującym się limicie nasze zadania zatrzymują się pierwsze.
# Zadania refresh_*, history_build i prematch_collector są w mapie dla
# kompletności (profil "Full" w register_tasks.ps1), ale na wspólnym kluczu
# nie uruchamiamy ich: te dane utrzymuje instancja Oskara.
#
# Jedna linia na przebieg w <MODEL_BUK_DATA>\logs\<zadanie>.log. Klucz API nie
# trafia do logu.

param(
    [Parameter(Mandatory = $true)][string]$Job,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "env.ps1")

$jobs = @{
    odds_collector     = @("scripts\collect_odds.py", "--days-ahead", "1", "--max-requests", "60", "--reserve", "3000")
    closing_odds       = @("scripts\collect_closing_odds.py", "--max-requests", "15", "--reserve", "3000")
    scanner            = @("scripts\run_scanner.py", "--reserve", "3000")
    fit_live_models    = @("scripts\fit_live_models.py", "fit", "--jobs", "3")
    prematch_collector = @("scripts\collect_prematch.py")
    refresh_recent     = @("scripts\refresh_current_season.py", "--mode", "dates")
    refresh_season     = @("scripts\refresh_current_season.py")
}
if (-not $jobs.ContainsKey($Job)) {
    Write-Error ("Nieznane zadanie '{0}'. Dostępne: {1}" -f $Job, (($jobs.Keys | Sort-Object) -join ", "))
    exit 64
}

$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $env:MODEL_BUK_DATA "logs"
$log = Join-Path $logDir ("{0}.log" -f $Job)
New-Item -ItemType Directory -Force $logDir | Out-Null

$argv = @($jobs[$Job])
if ($Rest) { $argv += $Rest }

Push-Location $appRoot
try {
    $output = & $python @argv 2>&1 | Out-String
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $LASTEXITCODE, $output.Trim()) -Encoding utf8
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
