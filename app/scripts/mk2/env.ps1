# Środowisko MK2 na tym komputerze. Ładowanie w PowerShell (kropka i spacja):
#   . .\scripts\mk2\env.ps1
# MODEL_BUK_DATA      paczka danych od Oskara (Documents\Model-Buk-Data): tylko do odczytu,
#                     podmieniana przez scripts\mk2\import_data.py, gdy przyjdzie nowsza.
# MODEL_BUK_MK2_OUT   nasze dane (Documents\Model-Buk-Data-MK2): archiwum kursów, katalog meczów,
#                     rejestr skanera, fit modeli, logi. Nowa paczka Oskara ich nie dotyka.
# Klucz API dociągany jest ze zmiennej użytkownika Windows (setx API_FOOTBALL_KEY "...").
# Nic nie zapisuje.

if (-not $env:MODEL_BUK_DATA) {
    $env:MODEL_BUK_DATA = Join-Path $HOME "Documents\Model-Buk-Data"
}
if (-not $env:MODEL_BUK_MK2_OUT) {
    $env:MODEL_BUK_MK2_OUT = Join-Path $HOME "Documents\Model-Buk-Data-MK2"
}
# Zmienna użytkownika (rejestr) ma pierwszeństwo: po `setx` procesy uruchomione
# wcześniej (np. aplikacja Claude) trzymają starą wartość w swoim środowisku.
$userKey = [Environment]::GetEnvironmentVariable("API_FOOTBALL_KEY", "User")
if ($userKey) {
    $env:API_FOOTBALL_KEY = $userKey
}
$env:PYTHONIOENCODING = "utf-8"
