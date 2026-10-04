# Środowisko MK2 na tym komputerze. Ładowanie w PowerShell (kropka i spacja):
#   . .\scripts\mk2\env.ps1
# Ustawia katalog danych na Documents\Model-Buk-Data tego użytkownika (skrypty
# Oskara bez MODEL_BUK_DATA wskazywałyby na jego dysk) i dociąga klucz API ze
# zmiennej użytkownika Windows (setx API_FOOTBALL_KEY "..."). Nic nie zapisuje.

if (-not $env:MODEL_BUK_DATA) {
    $env:MODEL_BUK_DATA = Join-Path $HOME "Documents\Model-Buk-Data"
}
# Zmienna użytkownika (rejestr) ma pierwszeństwo: po `setx` procesy uruchomione
# wcześniej (np. aplikacja Claude) trzymają starą wartość w swoim środowisku.
$userKey = [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")
if ($userKey) {
    $env:API_FOOTBALL_KEY = $userKey
}
$env:PYTHONIOENCODING = "utf-8"
