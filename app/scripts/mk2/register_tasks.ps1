# Rejestruje (albo usuwa) zadania MK2 w Harmonogramie zadań Windows, w folderze
# \ModelBukMK2\. Każde zadanie woła run_job.ps1 -Job <nazwa>; zadanie, które
# jeszcze trwa, nie jest uruchamiane ponownie (IgnoreNew). Zadania działają,
# gdy użytkownik jest zalogowany (Interactive): bez hasła w harmonogramie, a
# klucz API czytany jest z profilu użytkownika.
#
#   .\scripts\mk2\register_tasks.ps1                 profil Shared (wspólny klucz API)
#   .\scripts\mk2\register_tasks.ps1 -Profile Full   własny klucz Pro: także kolektor
#                                                    przedmeczowy, odświeżanie sezonu i fit modeli
#   .\scripts\mk2\register_tasks.ps1 -Unregister     usuwa wszystkie zadania MK2
#
# Harmonogram (czas lokalny), wzorowany na zadaniach Oskara (\ModelBuk\):
#   Shared: OddsCollector co 2 h o :05, ClosingOdds co 15 min od :12, ValueScanner co 30 min od :15
#   Full:   + PrematchCollector co 20 min od :05, RefreshRecent co godzinę o :35,
#           RefreshSeason 01:45, FitLiveModels 03:00

param(
    [ValidateSet("Shared", "Full")][string]$Profile = "Shared",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$folder = "\ModelBukMK2\"
$runner = Join-Path $PSScriptRoot "run_job.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Repeat-Trigger([int]$startMinute, [int]$everyMinutes) {
    $start = (Get-Date).Date.AddMinutes($startMinute)
    New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes $everyMinutes) `
        -RepetitionDuration ([TimeSpan]::MaxValue)
}
function Daily-Trigger([string]$at) {
    New-ScheduledTaskTrigger -Daily -At $at
}

# nazwa zadania, zadanie run_job.ps1, wyzwalacz, limit czasu jednego przebiegu (min)
$plan = @(
    @{ Name = "OddsCollector"; Job = "odds_collector"; Trigger = (Repeat-Trigger 5 120); Limit = 20 },
    @{ Name = "ClosingOdds";   Job = "closing_odds";   Trigger = (Repeat-Trigger 12 15); Limit = 10 },
    @{ Name = "ValueScanner";  Job = "scanner";        Trigger = (Repeat-Trigger 15 30); Limit = 15 }
)
if ($Profile -eq "Full") {
    $plan += @(
        @{ Name = "PrematchCollector"; Job = "prematch_collector"; Trigger = (Repeat-Trigger 5 20); Limit = 10 },
        @{ Name = "RefreshRecent";     Job = "refresh_recent";     Trigger = (Repeat-Trigger 35 60); Limit = 15 },
        @{ Name = "RefreshSeason";     Job = "refresh_season";     Trigger = (Daily-Trigger "01:45"); Limit = 30 },
        @{ Name = "FitLiveModels";     Job = "fit_live_models";    Trigger = (Daily-Trigger "03:00"); Limit = 60 }
    )
}

$existing = Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue
if ($Unregister) {
    if ($existing) {
        $existing | Unregister-ScheduledTask -Confirm:$false
        Write-Host ("Usunięto {0} zadań z {1}" -f @($existing).Count, $folder)
    } else {
        Write-Host "Brak zadań MK2 do usunięcia."
    }
    exit 0
}

if (-not (Test-Path $runner)) { throw "Nie ma $runner" }
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

foreach ($task in $plan) {
    $action = New-ScheduledTaskAction -Execute $powershell `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Job {1}' -f $runner, $task.Job) `
        -WorkingDirectory (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $task.Limit)
    Register-ScheduledTask -TaskName $task.Name -TaskPath $folder -Action $action -Trigger $task.Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host ("Zarejestrowano {0}{1} -> run_job.ps1 -Job {2}" -f $folder, $task.Name, $task.Job)
}
Write-Host ("Profil {0}: {1} zadań. Podgląd: Get-ScheduledTask -TaskPath '{2}'" -f $Profile, $plan.Count, $folder)
