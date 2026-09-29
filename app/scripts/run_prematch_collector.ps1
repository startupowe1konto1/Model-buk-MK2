# Scheduled entry point for the hourly pre-match collector (fixtures, line-ups,
# injuries, API predictions).
# Appends one line per run (or the error) to Model-Buk-Data\logs\prematch_collector.log.
# The API key is read from the user-level API_FOOTBALL_KEY variable; nothing
# secret is written to the log.

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = "C:\Users\oskar\Documents\Model-Buk-Data\logs"
$log = Join-Path $logDir "prematch_collector.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

if (-not $env:API_FOOTBALL_KEY) {
    $env:API_FOOTBALL_KEY = [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")
}
$env:PYTHONIOENCODING = "utf-8"

Push-Location $appRoot
try {
    $output = & $python "scripts\collect_prematch.py" @args 2>&1 | Out-String
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $LASTEXITCODE, $output.Trim()) -Encoding utf8
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
