# Kolektor kursów (scripts\collect_odds.py) na tym komputerze: odpowiednik
# scripts\run_odds_collector.ps1 Oskara, ale z katalogiem danych i logiem pod
# MODEL_BUK_DATA (scripts\mk2\env.ps1), nie pod jego ścieżką. Argumenty idą
# dalej do collect_odds.py, np.:
#   .\scripts\mk2\run_odds_collector.ps1 --max-requests 25 --reserve 15   (plan Free, testy)
#   .\scripts\mk2\run_odds_collector.ps1                                 (plan Pro: domyślne 150 / 1500)
# Log: <MODEL_BUK_DATA>\logs\odds_collector.log, jedna linia na przebieg. Klucz
# API nie trafia do logu.

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "env.ps1")

$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $env:MODEL_BUK_DATA "logs"
$log = Join-Path $logDir "odds_collector.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

Push-Location $appRoot
try {
    $output = & $python "scripts\collect_odds.py" @args 2>&1 | Out-String
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $LASTEXITCODE, $output.Trim()) -Encoding utf8
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
