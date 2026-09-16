@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Model Buk v0.5 Server - PORT 8010 - NIE ZAMYKAJ

echo ================================================
echo          MODEL BUK v0.5 - START APLIKACJI
echo ================================================
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)

if not defined PY (
  echo [BLAD] Python nie jest widoczny w systemie.
  echo Zamknij to okno, otworz nowe CMD i sprawdz: python --version
  echo.
  pause
  exit /b 1
)

echo [1/6] Python:
%PY% --version
if errorlevel 1 goto :error

echo.
echo [2/6] Przygotowanie srodowiska .venv...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 goto :error
) else (
  echo Srodowisko juz istnieje - pomijam tworzenie.
)

echo.
echo [3/6] Instalacja / aktualizacja bibliotek...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e .
if errorlevel 1 goto :error

echo.
echo [4/6] Sprawdzam live data...
if defined API_FOOTBALL_KEY (
  echo API-Football: PODLACZONE
) else (
  echo API-Football: brak klucza - aplikacja wystartuje w trybie historycznym/manualnym.
  echo Aby wlaczyc live data, uruchom USTAW_API_FOOTBALL.bat.
)

echo.
echo [5/6] Uruchamiam serwer Model Buk...
echo Serwer bedzie dzialal w TYM oknie. Nie zamykaj go podczas korzystania z aplikacji.

echo.
echo [6/6] Przegladarka otworzy sie automatycznie za kilka sekund...
set "MODEL_BUK_PORT=8010"
start "" /b .venv\Scripts\python.exe -c "import time,webbrowser; time.sleep(4); webbrowser.open('http://127.0.0.1:8010/?v=05')"

".venv\Scripts\python.exe" -m model_buk.web.api
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo ================================================
echo [BLAD] Nie udalo sie uruchomic Model Buk.
echo Zrob screenshot tego okna i wyslij mi go w ChatGPT.
echo ================================================
pause
exit /b 1
