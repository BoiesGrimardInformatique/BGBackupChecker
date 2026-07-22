@echo off
rem ============================================================
rem  Installation complete de backup-monitor sur Windows 11
rem  (methode Outlook local).
rem  Prerequis : Python 3.10+ installe (https://www.python.org,
rem  cocher "Add python.exe to PATH").
rem
rem  Cette installation va jusqu'au bout : environnement Python,
rem  dependances, autotest, PUIS l'assistant qui fait choisir la
rem  boite et les dossiers a surveiller, et une premiere analyse.
rem  A la fin, l'outil est deja configure et pret a l'emploi.
rem ============================================================
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto :pas_python

echo == Creation de l'environnement virtuel ==
py -3 -m venv venv || goto :erreur
venv\Scripts\python -m pip install --upgrade pip --quiet || goto :erreur

echo == Installation des dependances ==
if exist wheels\ (
  venv\Scripts\python -m pip install --quiet --no-index --find-links wheels -r requirements.txt || goto :erreur
) else (
  venv\Scripts\python -m pip install --quiet -r requirements.txt || goto :erreur
)

if not exist config.yaml (
  copy config.example.yaml config.yaml >nul
  echo == config.yaml cree ==
) else (
  echo == config.yaml existant conserve ==
)

echo.
echo == Autotest de l'installation (sans toucher a Outlook ni au reseau) ==
venv\Scripts\python -m backup_monitor selftest || goto :erreur

echo.
echo == Configuration : choix de la boite Outlook et des dossiers a surveiller ==
echo    (lecture seule ; Outlook doit etre ouvert et configure sur ce poste)
echo.
venv\Scripts\python -m backup_monitor setup
if errorlevel 1 goto :config_a_finir

echo.
echo == Premiere analyse et generation du tableau ==
venv\Scripts\python -m backup_monitor run
if errorlevel 3 goto :config_a_finir
if errorlevel 1 goto :analyse_echec

echo.
echo == Installation terminee : l'outil est configure et le tableau genere. ==
echo   - Relancer / actualiser le tableau :   lancer.bat
echo   - Reconfigurer les dossiers :          lancer.bat setup
echo   - Bilan de calibrage des motifs :      venv\Scripts\python -m backup_monitor diagnose
echo   - Actualisation auto (5 min) :         powershell -ExecutionPolicy Bypass -File windows\installer-tache.ps1
echo.
echo Pensez a declarer vos taches attendues (expected_jobs) et vos clients
echo dans config.yaml pour la detection des backups manquants.
goto :fin

:config_a_finir
echo.
echo L'environnement est installe et valide, mais la configuration des dossiers
echo n'est pas terminee (Outlook indisponible, ou aucun dossier choisi).
echo Terminez-la lorsque Outlook est pret, avec :   lancer.bat setup
goto :fin

:analyse_echec
echo.
echo Configuration enregistree, mais la premiere analyse n'a pas abouti.
echo Details dans backup-monitor.log ; reessayez avec :  lancer.bat
goto :fin

:pas_python
echo Python 3 est introuvable (commande "py").
echo Installer depuis https://www.python.org en cochant "Add python.exe to PATH",
echo puis relancer install.bat.
goto :fin_echec

:erreur
echo.
echo ECHEC de l'installation. Verifier que Python 3.10+ est installe (commande "py")
echo et la connexion reseau (installation des dependances).

:fin_echec
echo.
pause
exit /b 1

:fin
echo.
pause
exit /b 0
