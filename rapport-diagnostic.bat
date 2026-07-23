@echo off
setlocal
rem ============================================================
rem  Genere rapport-diagnostic.txt : tout ce qu'il faut pour faire
rem  ajuster l'outil sans acces au poste (autotest, comptes par
rem  produit, courriels non reconnus avec extraits, cas d'echec a
rem  confirmer, etat des taches, journaux).
rem  AUCUN envoi automatique : le fichier reste local. Il contient
rem  des noms de clients/machines et des extraits de courriels --
rem  RELIRE avant de le transmettre.
rem ============================================================
call "%~dp0lancer.bat" rapport
if exist "%~dp0rapport-diagnostic.txt" start "" notepad "%~dp0rapport-diagnostic.txt"
echo.
echo Le rapport est dans : %~dp0rapport-diagnostic.txt
echo Relisez-le, puis joignez-le a votre demande d'ajustement.
pause
