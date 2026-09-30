# Scheduled entry point for the nightly refit of the live models used by the scanner.
# Appends exactly one line per run (the summary, or the error) to Model-Buk-Data\logs\fit_live_models.log.
# Arguments are passed through to fit_live_models.py (default: fit --jobs 3).
# Makes no API requests; it reads the local history tables only.

$ErrorActionPreference = "Continue"
$appRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $appRoot ".venv\Scripts\python.exe"
$logDir = "C:\Users\oskar\Documents\Model-Buk-Data\logs"
$log = Join-Path $logDir "fit_live_models.log"
New-Item -ItemType Directory -Force $logDir | Out-Null
$env:PYTHONIOENCODING = "utf-8"

$fitArgs = if ($args.Count -gt 0) { $args } else { @("fit", "--jobs", "3") }

Push-Location $appRoot
try {
    $output = & $python "scripts\fit_live_models.py" @fitArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
    $line = (($output -split "\r?\n") | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Last 12) -join " | "
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value ("[{0}] exit={1} {2}" -f $stamp, $code, $line) -Encoding utf8
    exit $code
}
finally {
    Pop-Location
}
