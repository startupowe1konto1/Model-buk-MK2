# Scheduled entry point for the value scanner (paper mode).
# Appends exactly one line per run (the summary, or the error) to Model-Buk-Data\logs\scanner.log.
# Arguments are passed through to run_scanner.py (e.g. -- --no-api).
# The API key is read from the user-level API_FOOTBALL_KEY variable; nothing
# secret is written to the log.

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = "C:\Users\oskar\Documents\Model-Buk-Data\logs"
$log = Join-Path $logDir "scanner.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

if (-not $env:API_FOOTBALL_KEY) {
    $env:API_FOOTBALL_KEY = [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")
}
$env:PYTHONIOENCODING = "utf-8"

Push-Location $appRoot
try {
    $output = & $python "scripts\run_scanner.py" @args 2>&1 | Out-String
    $code = $LASTEXITCODE
    $line = (($output -split "\r?\n") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join " | "
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $code, $line) -Encoding utf8
    exit $code
}
finally {
    Pop-Location
}
