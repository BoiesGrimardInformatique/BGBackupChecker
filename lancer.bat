@echo off
setlocal
rem ============================================================
rem  Lanceur portable (cle USB) de backup-monitor.
rem  - Le code, config.yaml, le tableau HTML et le journal restent
rem    sur la cle (dossier de ce script, quelle que soit la lettre).
rem  - L'environnement Python est cree PAR POSTE dans
rem    %LOCALAPPDATA%\backup-monitor\venv (un venv n'est pas portable).
rem  - Sans argument : analyse puis ouvre le tableau.
rem    Avec arguments : passes tels quels (setup, diagnose, selftest...).
rem ============================================================
set "PROJET=%~dp0"
set "VENVDIR=%LOCALAPPDATA%\backup-monitor\venv"
set "PY=%VENVDIR%\Scripts\python.exe"

where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 est introuvable sur ce poste.
  echo Installer depuis https://www.python.org en cochant "Add python.exe to PATH",
  echo puis relancer ce script.
  pause
  exit /b 1
)

if not exist "%PY%" (
  echo Premiere utilisation sur ce poste : creation de l'environnement...
  py -3 -m venv "%VENVDIR%" || goto :erreur
)

rem Dependances : reinstallees seulement si requirements.txt a change.
fc /b "%PROJET%requirements.txt" "%VENVDIR%\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo Installation des dependances...
  if exist "%PROJET%wheels\" (
    "%PY%" -m pip install --quiet --no-index --find-links "%PROJET%wheels" -r "%PROJET%requirements.txt" || goto :erreur
  ) else (
    "%PY%" -m pip install --quiet -r "%PROJET%requirements.txt" || goto :erreur
  )
  copy /y "%PROJET%requirements.txt" "%VENVDIR%\requirements.installed" >nul
)

cd /d "%PROJET%"
if "%~1"=="" (
  "%PY%" -m backup_monitor run
  if errorlevel 1 goto :erreur
  if exist "%PROJET%tableau-backups.html" start "" "%PROJET%tableau-backups.html"
) else (
  "%PY%" -m backup_monitor %*
  if errorlevel 1 goto :erreur
)
endlocal
exit /b 0

:erreur
echo.
echo Une erreur est survenue (details ci-dessus, et dans backup-monitor.log).
pause
exit /b 1
