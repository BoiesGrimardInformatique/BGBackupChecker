@echo off
rem A lancer UNE FOIS sur un poste avec internet : telecharge les paquets
rem Python dans wheels\ sur la cle, pour que lancer.bat fonctionne ensuite
rem HORS-LIGNE sur les postes clients.
rem Note : les paquets telecharges correspondent a la version de Python du
rem poste de preparation — utiliser la meme version majeure (ex. 3.12) que
rem sur les postes cibles.
cd /d "%~dp0"
py -3 -m pip download -r requirements.txt -d wheels || goto :erreur
echo.
echo Paquets telecharges dans wheels\ — la cle est utilisable hors-ligne.
pause
exit /b 0
:erreur
echo Echec du telechargement (internet requis sur ce poste).
pause
exit /b 1
