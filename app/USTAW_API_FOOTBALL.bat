@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Model Buk - konfiguracja API-Football

echo ================================================
echo       MODEL BUK - API-FOOTBALL KEY
echo ================================================
echo.
echo Klucz jest zapisywany jako zmienna uzytkownika Windows.
echo Nie jest dodawany do projektu ani GitHuba.
echo.
set /p "MBKEY=Wklej API_FOOTBALL_KEY i nacisnij Enter: "
if not defined MBKEY (
  echo Nie podano klucza.
  pause
  exit /b 1
)
setx API_FOOTBALL_KEY "%MBKEY%" >nul
if errorlevel 1 (
  echo [BLAD] Nie udalo sie zapisac zmiennej.
  pause
  exit /b 1
)
echo.
echo [OK] Klucz zapisany. Zamknij uruchomiona aplikacje i odpal ponownie URUCHOM_MODEL_BUK.bat.
pause
