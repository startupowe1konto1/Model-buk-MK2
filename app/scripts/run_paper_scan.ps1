param(
    [string]$Config = "config/paper_scan.toml",
    [ValidateSet("live", "offline")]
    [string]$Mode = "live",
    [string]$Snapshots = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$appRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $appRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing app/.venv. Run URUCHOM_MODEL_BUK.bat once to create it."
}

# Make the src-layout package importable even when the virtual environment was
# created before the project was installed in editable mode.  This only changes
# the environment of this scheduler process; no system setting is modified.
$srcRoot = Join-Path $appRoot "src"
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) {
    "$srcRoot;$previousPythonPath"
} else {
    $srcRoot
}

$arguments = @(
    "-m", "model_buk", "scan-current",
    "--config", $Config,
    "--mode", $Mode
)
if ($Mode -eq "offline") {
    if (-not $Snapshots) {
        throw "Offline mode requires -Snapshots pointing to JSON or JSONL."
    }
    $arguments += @("--snapshots", $Snapshots)
}
if ($DryRun) {
    $arguments += "--dry-run"
}

Push-Location $appRoot
try {
    & $python @arguments
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
