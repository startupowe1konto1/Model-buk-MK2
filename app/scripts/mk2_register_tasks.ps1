# MK2: zakłada zadania w Harmonogramie zadań Windows (folder \ModelBukMK2\).
#
#   OddsCollector  co godzinę o :05   kalendarz + kursy wybranych lig
#   ClosingOdds    co 15 minut        kursy meczów startujących w ciągu 2 h (CLV)
#
# Uruchom z katalogu repozytorium:
#   powershell -ExecutionPolicy Bypass -File app\scripts\mk2_register_tasks.ps1
# Usunięcie zadań:
#   powershell -ExecutionPolicy Bypass -File app\scripts\mk2_register_tasks.ps1 -Remove
#
# Klucz API nie jest tu zapisywany. Zadania czytają zmienną użytkownika
# API_FOOTBALL_KEY (ustawiona wcześniej przez: setx API_FOOTBALL_KEY "...").

param(
    [string]$DataDir = (Join-Path $HOME "Documents\Model-Buk-MK2-Data"),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskPath = "\ModelBukMK2\"

if ($Remove) {
    Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Write-Host "Usunięto zadania z $TaskPath"
    exit 0
}

if (-not [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")) {
    Write-Error "Brak zmiennej API_FOOTBALL_KEY. Najpierw: setx API_FOOTBALL_KEY `"twoj_klucz`" i nowe okno PowerShell."
    exit 1
}

$Script = Join-Path $PSScriptRoot "mk2_odds_collector.py"
if (-not (Test-Path $Script)) { Write-Error "Nie znaleziono $Script"; exit 1 }

# pyw/pythonw = bez migającego okna konsoli; wynik i tak idzie do logu
$exe = $null; $prefix = ""
foreach ($candidate in @("pyw", "pythonw", "py", "python")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $exe = $cmd.Source
        if ($candidate -in @("pyw", "py")) { $prefix = "-3 " }
        break
    }
}
if (-not $exe) { Write-Error "Nie znaleziono Pythona (py / python)."; exit 1 }

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$forever = New-TimeSpan -Days 3650
$start = (Get-Date).Date.AddMinutes(5)

$hourlyAction = New-ScheduledTaskAction -Execute $exe `
    -Argument "$prefix`"$Script`" all --data-dir `"$DataDir`"" -WorkingDirectory $PSScriptRoot
$hourlyTrigger = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration $forever
Register-ScheduledTask -TaskPath $TaskPath -TaskName "OddsCollector" -Action $hourlyAction `
    -Trigger $hourlyTrigger -Settings $settings -Description "Model Buk MK2: kalendarz + kursy co godzine" -Force | Out-Null

$closingAction = New-ScheduledTaskAction -Execute $exe `
    -Argument "$prefix`"$Script`" closing --data-dir `"$DataDir`"" -WorkingDirectory $PSScriptRoot
$closingTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(7) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration $forever
Register-ScheduledTask -TaskPath $TaskPath -TaskName "ClosingOdds" -Action $closingAction `
    -Trigger $closingTrigger -Settings $settings -Description "Model Buk MK2: kursy zamkniecia co 15 min" -Force | Out-Null

Write-Host "Założono zadania w $TaskPath (Python: $exe)"
Write-Host "Dane: $DataDir"
Write-Host "Log:  $DataDir\logs\collector.jsonl"
Get-ScheduledTask -TaskPath $TaskPath | Select-Object TaskName, State | Format-Table -AutoSize
