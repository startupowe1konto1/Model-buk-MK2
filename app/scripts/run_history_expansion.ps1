# One-off scheduled run (after the 00:00 UTC quota reset): download the history
# of the third-wave leagues and the club cups, then rebuild the history tables
# and the football-data join. Every step appends one line to
# Model-Buk-Data\logs\history_expansion.log; a failed step stops the chain.
# The API key is read from the user-level API_FOOTBALL_KEY variable; nothing
# secret is written to the log.

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$dataRoot = "C:\Users\oskar\Documents\Model-Buk-Data"
$logDir = Join-Path $dataRoot "logs"
$log = Join-Path $logDir "history_expansion.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

if (-not $env:API_FOOTBALL_KEY) {
    $env:API_FOOTBALL_KEY = [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")
}
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Step([string]$name, [string[]]$arguments) {
    $output = & $python @arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $tail = ($output.Trim() -split "`r?`n" | Select-Object -Last 8) -join " | "
    Add-Content -Path $log -Value ("[{0}] step={1} exit={2} {3}" -f $stamp, $name, $code, $tail) -Encoding utf8
    return $code
}

Push-Location $appRoot
try {
    $code = Invoke-Step "download" @("scripts\download_history.py", "--data-dir", (Join-Path $dataRoot "api-football"), "--max-requests", "5000", "--reserve", "1500")
    if ($code -ne 0) { exit $code }
    $code = Invoke-Step "tables" @("scripts\build_history_tables.py")
    if ($code -ne 0) { exit $code }
    $code = Invoke-Step "fd_join" @("scripts\build_fd_af_join.py")
    exit $code
}
finally {
    Pop-Location
}
