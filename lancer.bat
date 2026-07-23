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

rem Avec arguments : passes tels quels (setup, diagnose, suggest-jobs...).
if "%~1"=="" goto :sansargs
"%PY%" -m backup_monitor %*
set "RC=%errorlevel%"
rem  3 = pas configure ; 2/4 (--fail-on-error) et 1 = voir :erreur.
rem  Comparaison EXACTE : "if errorlevel 3" voudrait dire "3 ou plus"
rem  et confondrait le code 4 (collecte partielle) avec "a configurer".
if "%RC%"=="3" goto :config_a_finir
if not "%RC%"=="0" goto :erreur
goto :fin
:sansargs

rem Sans argument : analyse puis ouverture du tableau.
"%PY%" -m backup_monitor run
set "RC=%errorlevel%"
if "%RC%"=="3" goto :configurer
if not "%RC%"=="0" goto :erreur
goto :ouvrir

:configurer
rem Code 3 = premiere utilisation : aucun dossier a surveiller n'est defini.
rem Ce n'est pas une panne -- on lance l'assistant puis on relance l'analyse.
echo.
echo Premiere utilisation : aucun dossier a surveiller n'est encore defini.
echo Lancement de l'assistant de configuration...
echo.
"%PY%" -m backup_monitor setup
if errorlevel 1 goto :config_a_finir
echo.
echo Configuration enregistree. Nouvelle analyse...
"%PY%" -m backup_monitor run
set "RC=%errorlevel%"
if "%RC%"=="3" goto :config_a_finir
if not "%RC%"=="0" goto :erreur

:ouvrir
if exist "%PROJET%tableau-backups.html" start "" "%PROJET%tableau-backups.html"

:fin
endlocal
exit /b 0

:config_a_finir
echo.
echo Configuration non terminee. Relancez ce programme pour reessayer,
echo ou lancez directement l'assistant :  lancer.bat setup
echo.
pause
exit /b 0

:erreur
echo.
echo Une erreur est survenue (details ci-dessus, et dans backup-monitor.log).
pause
exit /b 1
