# Gabarits de courriels des produits de sauvegarde (référence de calibrage)

Synthèse d'une recherche documentaire (2026-07-24) dans la documentation
officielle et le code source des produits, pour asseoir les motifs de
`parsers.DEFAULT_PATTERNS` sur des formes documentées plutôt que déduites.
Confiance : **[DOC]** documentation/code officiel · **[FORUM]** exemple réel
vu en forum · **[SUPP]** supposé, à confirmer sur courriels réels.

## Macrium Reflect (v7.2/v8/LTSC/X)

- Sujets et corps **personnalisables par définition de sauvegarde** ; nos
  motifs couvrent les défauts d'usine (« `<machine>` Macrium Reflect -
  Backup Failed/Success/with Warnings », « Failed :( », « Success :) »,
  « Warning :| », « Computer Name: », « Backup definition: »). [DOC pour le
  mécanisme, défauts = observés]
- `{BACKUPSTATUS}` s'évalue en littéraux anglais `Success`/`Warning`/
  `Failure`. Le statut Warning existe depuis v7.2. [DOC]
- Les annulations partent dans le courriel *Failure* ; Reflect X peut aussi
  envoyer un *Failure* pour une sauvegarde planifiée **sautée** (option
  « Include skipped scheduled backups »). [DOC]
- Clonage et image disque utilisent les trois mêmes courriels. [DOC]
- Aucune localisation française des défauts trouvée. [SUPP]

## Macrium Site Manager (v8)

- Sujet = gabarit unique configurable (`$notificationtype`, `$agent`, …) ;
  défaut observé « Site Manager Notification Email ».
- Liste **fermée** des types [DOC] : Backup Successful / Failed / Started,
  Intra-daily Backup Successful, Update Found, Testing Slack / Email,
  Remote Synchronization Started / Successful / Failed, Restore Started /
  Successful / Failed, Disk Space Low, Repository Uncontactable, Backup
  Summary (digest quotidien en tableau HTML).
- Pas de notification « agent offline » ni « licence » en v8. [DOC]

## Macrium Image Guardian

- Courriel envoyé **uniquement** pour un blocage (événement 320, sévérité
  Warning) : « `Blocked unauthorised process (x.exe) accessing file
  (…\…​.mrimg)` ». [DOC] Sujet observé : « Macrium Image Guardian - Event -
  `<machine>` ». Tout courriel MIG = alerte, jamais un succès.

## Retrospect (Windows/Mac, v18+)

- Trois courriels d'échec [DOC] : « `Notification - Retrospect` »,
  « `<script> - Error Notification - Retrospect` », « `<script> - N errors,
  M warnings - Retrospect` » ; et « `Execution stopped by operator -
  Retrospect` » (arrêt manuel). Formes françaises observées : « Notification
  d'erreur », « N erreurs, M avertissements ».
- Le journal d'exécution est inclus **dans le corps** : lignes `Completed:`,
  `Performance: X MB/minute`, `Duration: hh:mm:ss` (FR : Durée,
  Performance, Erreurs, Avertissements). [DOC]
- Digest quotidien [CAPTURE] : section « Recent Backups », ligne « X backups
  for Y sources, Z GB backed up », tableau Source/Backup Date/Set/Size/
  Files/Script, mention « (Not backed up) ». Gabarit HTML personnalisable
  (htmlEmailHeader.txt/htmlEmailFooter.txt). FR observé : « Retrospect :
  état pour `<date>` », « Sauvegardes interrompues par l'opérateur: N ».
- Management Console (console.retrospect.com) : rapports quotidiens/hebdo
  avec TSV joint ; champs Errors/Warnings/Duration/Files/… [DOC] ; sujet
  observé « `<script>` - Notification - Retrospect », récapitulatif
  « Erreurs : N ».

## Veritas Backup Exec (20+/21/22)

- Sujet : « `Backup Exec Alert: <catégorie> (Server: "X")[ (Job: "Y")]` ».
  Le nom de job peut contenir guillemets/parenthèses → extraction greedy.
- Sévérités officielles par catégorie [DOC guide 22.1 + BEMCLI] :
  - **Erreur** : Job Failed, Job Cancellation, Catalog Error, Database
    Maintenance Failure, Storage/Media/Tape Alert/Software Update Error,
    SDR Copy Failed.
  - **Avertissement** : Job Warning, Job Completed with Exceptions, Backup
    job contains no data, Install/Storage/Media/Tape Alert/Software Update
    Warning, License and Maintenance Warning ; « Attention required » :
    Media Intervention / Insert / Overwrite / Remove, Library Insert.
  - **Information** : Job Success, Job Start, General Information, Database
    Maintenance Information, Install/Storage/Media/Tape Alert/Software
    Update Information, Service Start/Stop.
- **Piège documenté** : le courriel « exceptions » commence par « `The job
  completed successfully.  However, the following conditions were
  encountered:` » — tester ce marqueur AVANT le succès. [FORUM verbatim]
- Corps : « `<job> -- The job failed with the following error: …` »,
  « `The job was canceled because …` », codes « `V-79-…` ».
- `Job completion status:` (si journal joint) : Successful, Completed with
  exceptions, Failed over, Resumed, Canceled, Canceled timed out, Failed,
  Recovered, **Missed**. [DOC]

## Proxmox VE (vzdump) / Proxmox Backup Server

- vzdump [DOC code source] : sujet « `vzdump backup status (<fqdn>):
  backup successful|backup failed[: <erreur>]` » — **ancien système (≤8.0)
  avec espace avant le deux-points** (« `) : backup …` »), nouveau (8.1+)
  sans. Jamais de « warning » ni « partial » dans le sujet : un seul invité
  en échec ⇒ `backup failed`. Corps : tableau VMID/Name/Status (ok, err,
  aborted), `Total running time:`, section `Logs`.
- PBS [DOC] : « `Garbage Collect Datastore '<ds>'` », « `Verify Datastore
  '<ds>'` », « `Pruning datastore '<ds>'` », « `Sync remote '<r>' datastore
  '<ds>'` », « `Tape Backup … datastore '<ds>'` » + ` successful` |
  ` failed` (binaire). Corps : `Job ID:`, `Datastore:`, `Remote:`,
  `Remote Store:`, « `Synchronization failed: <erreur>` ».
- proxmox-backup-client via timer systemd : **aucun gabarit intégré**
  (scripts maison, ex. « Timer service … pbs.sh ») — format non garanti.

## Cobian Backup 11 / Cobian Reflector

- **Sujet libre** (défini par l'admin ; variables `%ERRORS`, `%TASKNAME`,
  `%COMPUTERNAME`… sous Reflector) → classifier sur le corps. [DOC]
- Corps : « `This is an automatic mail message from Cobian Reflector. You
  will find the log files below this text.` » + journal.
- Marqueur de statut fiable [FORUM/GitHub] : « `** Backup done for the task
  "X". Errors: N. Processed files: N. … **` » (CB 9/10 : « `*** Backup
  finished. … Errors: N ***` ») → succès si N=0. Lignes d'erreur : préfixe
  « `ERR ` » en tête de ligne. Pas de niveau avertissement natif.
- Pièges : langue du journal configurable (chaînes ci-dessus = anglais),
  séparateur décimal localisé (« 30,08 GB »).

## SQL Server Agent (Database Mail)

- Sujet [DOC Microsoft] : « `SQL Server Job System: '<job>' completed on
  \\<serveur>` » — **toujours « completed on », quel que soit le statut**.
  (Alertes : « `SQL Server Alert System: '<alerte>' occurred on …` ».)
- Le statut est dans le corps : `STATUS:  Succeeded|Failed` et
  `MESSAGES:  The job succeeded|failed. …` ; arrêt manuel : « `The job was
  stopped prior to completion by <X>` ». Pas de niveau warning dans ce
  mécanisme. [DOC/FORUM]
- Les préfixes de sujet « `[The job succeeded.]` / `[The job failed.]` »
  observés dans notre corpus viennent probablement du canal *pager* —
  gardés en motifs, mais ne pas compter dessus seuls. [SUPP]

## Produits pas encore rencontrés (préparation)

- **Veeam B&R** [DOC] : sujet « `[Success|Warning|Failed] <job>
  (<N> objects) <issues>` » — déjà couvert par le produit « script »
  (`[Success]`/`[Failed]`/`[Warning]`) ; créer un produit « veeam » à la
  première rencontre pour un libellé exact.
- **Synology** : « `<événement> on <hostname> has failed / has successfully
  completed` » (gabarits éditables) ; Active Backup : « has been
  completed. » / « was completed. » / « was successfully completed. »
- **QNAP HBS 3** : sujet « `[Info|Warning|Error][<NAS>] Hybrid Backup
  Sync` » ; corps « `Finished Backup job '<x>' with errors` », « `Failed to
  complete Backup job …` ».
- **TrueNAS** : sujet unique « `TrueNAS <hostname>: Alerts` » (détail dans
  le corps ; en-tête `X-TrueNAS-Host:`).

## Sources principales

- kbx.macrium.com (Email Settings and Defaults, Image Guardian) ;
  knowledgebase.macrium.com (MSM8 Configuration and Security, KNOW72/80)
- docs.retrospect.com (troubleshooting-email-notifications, management,
  release-notes, security-reporting, how-to-customize-the-retrospect-email-
  template) ; blog retrospect.com (daily_backup_report_email)
- veritas.com guide 22.1 (v53901270 et suivants) ; BEMCLI (Get-BEAlert) ;
  KB 100055475 / 100015358 / 100020543
- github.com/proxmox : pve-manager (templates, VZDump.pm), proxmox-backup
  (templates, email_notifications.rs) ; pve.proxmox.com/pve-docs
- cobiansoft.com/crHelp (options, parameters) ;
  github.com/edvler/check_mk-cobian_reflector
- learn.microsoft.com (ms132934, notify-an-operator-of-job-status,
  dbo-sysjobhistory)
