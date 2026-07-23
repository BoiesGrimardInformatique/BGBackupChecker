# Cree une tache planifiee Windows qui regenere le tableau toutes les 5 minutes.
# La tache s'execute dans la session de l'utilisateur courant (necessaire pour
# parler au Outlook local) et n'a aucun privilege eleve.
$ErrorActionPreference = "Stop"

$projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonw = Join-Path $projet "venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Error "venv introuvable — lancer install.bat d'abord."
}

# --fail-on-error : le « Dernier resultat » de la tache devient exploitable
# (0x0 = OK, 0x2 = backups en erreur/manquants, 0x4 = collecte partielle,
# 0x1 = panne de l'outil) — visible dans le Planificateur et par un RMM.
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m backup_monitor run --fail-on-error" -WorkingDirectory $projet
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "BackupMonitor" -Action $action `
    -Trigger $trigger -Settings $settings -RunLevel Limited -Force `
    -Description "Analyse lecture seule des courriels de backup Macrium/Retrospect -> tableau HTML local" | Out-Null

Write-Host "Tache « BackupMonitor » creee : tableau regenere toutes les 5 minutes"
Write-Host "tant que la session est ouverte (requis pour lire Outlook)."
Write-Host "Suppression : Unregister-ScheduledTask -TaskName BackupMonitor"
