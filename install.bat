@echo off
rem Installation de backup-monitor sur Windows 11 (methode Outlook local).
rem Prerequis : Python 3.10+ installe (https://www.python.org, cocher "Add to PATH").
cd /d "%~dp0"

echo == Creation de l'environnement virtuel ==
py -3 -m venv venv || goto :erreur
venv\Scripts\python -m pip install --upgrade pip --quiet || goto :erreur
venv\Scripts\pip install -r requirements.txt --quiet || goto :erreur

if not exist config.yaml (
  copy config.example.yaml config.yaml >nul
  echo == config.yaml cree -- A ADAPTER avant la premiere execution ==
) else (
  echo == config.yaml existant conserve ==
)

echo.
echo Prochaines etapes :
echo   1. Autotest de l'installation (sans toucher Outlook) :
echo        venv\Scripts\python -m backup_monitor selftest
echo   2. Assistant interactif (scanne Outlook et fait choisir boite/dossiers) :
echo        venv\Scripts\python -m backup_monitor setup
echo   3. Premiere analyse + bilan de calibrage des motifs :
echo        venv\Scripts\python -m backup_monitor run
echo        venv\Scripts\python -m backup_monitor diagnose
echo   4. Declarer taches attendues (expected_jobs) et clients dans config.yaml
echo   5. (Optionnel) Actualisation automatique aux 5 minutes :
echo        powershell -ExecutionPolicy Bypass -File windows\installer-tache.ps1
goto :fin

:erreur
echo ECHEC de l'installation. Verifier que Python 3 est installe (commande "py").
exit /b 1

:fin
