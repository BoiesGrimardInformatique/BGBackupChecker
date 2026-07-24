"""Autotest hors-ligne : valide l'installation (analyseurs, verrous des
pièces jointes, génération du tableau) SANS toucher à Outlook ni au réseau.
À lancer après install.bat :  python -m backup_monitor selftest"""

import os
import re
import tempfile
from datetime import datetime, timedelta

from . import (JobState, RawMail, STATUS_ERROR, STATUS_MISSING,
               STATUS_SUCCESS, STATUS_UNKNOWN, STATUS_WARNING, load_timezone)
from .attachments import extract, gate, looks_like_text, sender_allowed
from .history import HISTORY_FILE, streak, success_rate
from .history import update as hist_update
from .mailcache import MailCache, fingerprint, open_cache
from .notify import check_and_notify, transitions
from .parsers import (analyze, classify, current_states, job_states,
                      suggest_jobs)
from .report import render, write

TZ = load_timezone("America/Toronto")


def _cfg(tmpdir: str) -> dict:
    return {
        "analysis": {"days_back": 14, "timezone": "America/Toronto"},
        "parsers": {},
        "clients": [{"name": "Client Test", "machines": ["SRV-TEST"]}],
        "expected_jobs": [
            {"name": "Tâche présente", "product": "macrium",
             "match": "SRV-TEST", "every_hours": 24, "grace_hours": 6},
            {"name": "Tâche disparue", "product": "retrospect",
             "match": "FANTOME", "every_hours": 24, "grace_hours": 6},
        ],
        "report": {"output": "selftest.html", "refresh_seconds": 300,
                   "max_rows": 50},
        "_dir": tmpdir,
    }


def _mails(now: datetime) -> list[RawMail]:
    return [
        RawMail("Macrium Reflect Backup - SRV-TEST", "backup@test.local",
                now - timedelta(hours=2),
                "Computer: SRV-TEST\nBackup completed successfully\nErrors: 0",
                "Backups/Macrium", "macrium"),
        RawMail("Macrium Reflect Backup - SRV-TEST", "backup@test.local",
                now - timedelta(hours=26),
                "Computer: SRV-TEST\nBackup aborted\nErrors: 1",
                "Backups/Macrium", "macrium"),
        RawMail("Retrospect notification", "retrospect@test.local",
                now - timedelta(hours=3),
                'Script "Poste" completed with warnings.',
                "Backups/Retrospect", "retrospect"),
        RawMail("Message sans mots-clés", "inconnu@test.local",
                now - timedelta(hours=1), "Contenu quelconque.",
                "Backups/Retrospect", "retrospect"),
    ]


def _checks() -> list[tuple[str, bool, str]]:
    now = datetime.now(TZ)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = ""):
        results.append((name, bool(condition), detail))

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _cfg(tmpdir)
        events = analyze(cfg, _mails(now))
        by_status = [e.status for e in events]
        check("Classement succès/erreur/avertissement/inconnu",
              sorted(by_status) == sorted([STATUS_SUCCESS, STATUS_ERROR,
                                           STATUS_WARNING, STATUS_UNKNOWN]),
              f"obtenu : {by_status}")
        check("Extraction de la machine",
              any(e.machine == "SRV-TEST" for e in events))
        check("Association client",
              any(e.client == "Client Test" for e in events))

        states = job_states(cfg, events)
        st = {s.name: s.status for s in states}
        check("Tâche attendue au dernier état (succès)",
              st.get("Tâche présente") == STATUS_SUCCESS, str(st))
        check("Détection de tâche manquante",
              st.get("Tâche disparue") == STATUS_MISSING, str(st))

        # Mode auto (client_folders) : produit détecté au contenu, client = dossier
        auto_mails = [
            RawMail("Macrium Reflect Backup", "b@test.local", now,
                    "Backup completed successfully\nErrors: 0",
                    "Sauvegardes/Client Alpha", "auto", client="Client Alpha"),
            RawMail("Retrospect notification", "r@test.local", now,
                    'Script "Postes" failed\nerror -1101',
                    "Sauvegardes/Client Alpha", "auto", client="Client Alpha"),
        ]
        auto_ev = analyze(cfg, auto_mails)
        check("Mode auto : produit Macrium détecté au contenu",
              any(e.product == "macrium" and e.status == STATUS_SUCCESS
                  for e in auto_ev),
              str([(e.product, e.status) for e in auto_ev]))
        check("Mode auto : produit Retrospect détecté au contenu",
              any(e.product == "retrospect" and e.status == STATUS_ERROR
                  for e in auto_ev),
              str([(e.product, e.status) for e in auto_ev]))
        check("Mode auto : client = nom du sous-dossier",
              all(e.client == "Client Alpha" for e in auto_ev))

        # Mode auto : systèmes tiers fréquents (mélangés aux courriels
        # Macrium/Retrospect dans une boîte partagée) reconnus par produit ET
        # statut. Sujets réels de notification standard de chaque système.
        other_mails = [
            RawMail("[The job succeeded.] SQL Server Job System: 'Nightly Full' "
                    "completed on \\\\SRV1.", "b@test.local", now,
                    "JOB RUN: 'Nightly Full' was run.", "Sauvegardes/Client Beta",
                    "auto", client="Client Beta"),
            RawMail("vzdump backup status (HOST.local): backup successful",
                    "b@test.local", now, "", "Sauvegardes/Client Beta", "auto",
                    client="Client Beta"),
            RawMail("TOOLX - [Success] rapport quotidien", "b@test.local", now,
                    "", "Sauvegardes/Client Beta", "auto", client="Client Beta"),
        ]
        other_ev = analyze(cfg, other_mails)
        check("Mode auto : SQL Server Agent reconnu (produit + succès)",
              any(e.product == "sqlagent" and e.status == STATUS_SUCCESS
                  for e in other_ev),
              str([(e.product, e.status) for e in other_ev]))
        check("Mode auto : Proxmox Backup Server reconnu (produit + succès)",
              any(e.product == "pbs" and e.status == STATUS_SUCCESS
                  for e in other_ev),
              str([(e.product, e.status) for e in other_ev]))
        check("Mode auto : script personnalisé [Success] reconnu",
              any(e.product == "script" and e.status == STATUS_SUCCESS
                  for e in other_ev),
              str([(e.product, e.status) for e in other_ev]))

        # Correctifs de fiabilité du classement
        fiab_mails = [
            RawMail("Macrium Reflect - Backup Completed with Errors",
                    "b@test.local", now,
                    "Image of C: completed with errors.",
                    "Backups/Macrium", "macrium"),
            RawMail("SRV9 Macrium Reflect - Backup Success", "b@test.local",
                    now, "Success :)\nFailure count: 0",
                    "Backups/Macrium", "macrium"),
            RawMail("Macrium Reflect Backup - avec journal joint",
                    "b@test.local", now, "Backup completed successfully",
                    "Backups/Macrium", "macrium",
                    attachments_text="failed to read sector (retry ok)"),
            RawMail("Rapport quotidien", "b@test.local", now,
                    "Voir la pièce jointe.", "Backups/Macrium", "macrium",
                    attachments_text="[Failed] job de nuit"),
            RawMail("Macrium Reflect Backup - extraction", "b@test.local",
                    now, "Computer Name: SRV-X\nBackup completed successfully",
                    "Backups/Macrium", "macrium"),
        ]
        fiab_ev = {e.subject: e for e in analyze(cfg, fiab_mails)}
        check("« Completed with Errors » classé en erreur",
              fiab_ev["Macrium Reflect - Backup Completed with Errors"]
              .status == STATUS_ERROR)
        check("« Failure count: 0 » ne crée pas de fausse erreur",
              fiab_ev["SRV9 Macrium Reflect - Backup Success"]
              .status == STATUS_SUCCESS)
        check("Un « failed » dans la pièce jointe ne renverse pas un succès",
              fiab_ev["Macrium Reflect Backup - avec journal joint"]
              .status == STATUS_SUCCESS)
        check("Pièce jointe seule source : classement au 2e étage",
              fiab_ev["Rapport quotidien"].status == STATUS_ERROR)
        check("« Computer Name: X » extrait X (pas « Name »)",
              fiab_ev["Macrium Reflect Backup - extraction"]
              .machine == "SRV-X",
              fiab_ev["Macrium Reflect Backup - extraction"].machine)

        # Couverture d'affichage : un problème ancien ne doit JAMAIS être
        # invisible — ni tronqué par max_rows, ni masqué par une base
        # « dernières 24 h » quand aucune expected_job n'est configurée.
        cov_cfg = {**cfg, "expected_jobs": [], "clients": [],
                   "report": {"output": "cov.html", "refresh_seconds": 300,
                              "max_rows": 2}}
        cov_mails = [
            RawMail("Macrium OK récent A", "b@test.local",
                    now - timedelta(hours=1),
                    "Computer: SRV-A\nBackup completed successfully",
                    "Sauvegardes/Client Sain", "auto", client="Client Sain"),
            RawMail("Macrium OK récent B", "b@test.local",
                    now - timedelta(hours=2),
                    "Computer: SRV-B\nBackup completed successfully",
                    "Sauvegardes/Client Sain", "auto", client="Client Sain"),
            RawMail("Macrium Reflect - Backup Failure vieille de 3 jours",
                    "b@test.local", now - timedelta(days=3),
                    "Computer: SRV-RUSCIO\nBackup aborted",
                    "Sauvegardes/Ruscio Studio", "auto",
                    client="Ruscio Studio"),
            RawMail("Macrium OK ancien C", "b@test.local",
                    now - timedelta(days=4),
                    "Computer: SRV-C\nBackup completed successfully",
                    "Sauvegardes/Client Sain", "auto", client="Client Sain"),
        ]
        cov_ev = analyze(cov_cfg, cov_mails)
        cov_page = render(cov_cfg, cov_ev, [], None)
        check("Affichage : une erreur plus vieille que max_rows reste listée",
              "Backup Failure vieille de 3 jours" in cov_page
              and "OK ancien C" not in cov_page)
        check("Affichage : sans expected_jobs, un échec de 3 jours compte "
              "dans les tuiles (dernier état par tâche, pas 24 h)",
              'class="tile alert"' in cov_page)
        check("Affichage : le client en échec ressort dans la vue par client",
              "Ruscio Studio" in cov_page
              and 'data-client="Ruscio Studio"' in cov_page)

        # Nomenclature Retrospect « ProActive - Remote - <Compagnie> -
        # N erreurs » : client extrait du sujet, statut selon le compte.
        pa_mails = [
            RawMail("ProActive - Remote - Clinique Vertika - 0 erreurs",
                    "retro@test.local", now, "", "Backups/Retrospect",
                    "retrospect"),
            RawMail("RE: ProActive - Remote - Boies-Grimard Informatique - "
                    "3 erreurs", "retro@test.local", now, "",
                    "Backups/Retrospect", "retrospect"),
            RawMail("ProActive - Remote - Compagnie Auto - 10 erreurs",
                    "retro@test.local", now, "", "Sauvegardes/Divers",
                    "auto"),
        ]
        pa_ev = {e.subject: e for e in analyze(cfg, pa_mails)}
        ok0 = pa_ev["ProActive - Remote - Clinique Vertika - 0 erreurs"]
        check("ProActive : client extrait du sujet",
              ok0.client == "Clinique Vertika", ok0.client)
        check("ProActive : « 0 erreurs » = succès",
              ok0.status == STATUS_SUCCESS, ok0.status)
        ko3 = pa_ev["RE: ProActive - Remote - Boies-Grimard Informatique - "
                    "3 erreurs"]
        check("ProActive : « 3 erreurs » = erreur, RE: toléré, "
              "trait d'union du nom conservé",
              ko3.status == STATUS_ERROR
              and ko3.client == "Boies-Grimard Informatique",
              f"{ko3.status} / {ko3.client}")
        ko10 = pa_ev["ProActive - Remote - Compagnie Auto - 10 erreurs"]
        check("ProActive : « 10 erreurs » ne passe pas pour « 0 erreurs », "
              "produit Retrospect détecté en mode auto",
              ko10.status == STATUS_ERROR and ko10.product == "retrospect"
              and ko10.client == "Compagnie Auto",
              f"{ko10.status} / {ko10.product} / {ko10.client}")
        pa_dossier = analyze(cfg, [RawMail(
            "ProActive - Remote - Autre Nom - 0 erreurs", "r@test.local",
            now, "", "Sauvegardes/Dossier Client", "auto",
            client="Dossier Client")])
        check("ProActive : le client du dossier reste prioritaire",
              pa_dossier[0].client == "Dossier Client",
              pa_dossier[0].client)

        # Systèmes découverts dans un rapport diagnostic réel : Cobian
        # Reflector (passait pour du Macrium à cause de « Reflector »),
        # Backup Exec, proxmox-backup-client (Timer service), digest
        # quotidien Retrospect, alerte d'espace disque Site Manager.
        reels = [
            RawMail("Backup Summum (SERVEUR-PC)", "b@test.local",
                    now - timedelta(minutes=1),
                    "This is an automatic mail message from Cobian "
                    "Reflector. You will find the log files below this "
                    "text. ** The backup is done. Errors: 0 **",
                    "Sauvegardes/Fenêtres Summum", "auto",
                    client="Fenêtres Summum"),
            RawMail('Backup Exec Alert: Job Completed (Server: "SEA-MAIL") '
                    '(Job: "Sauvegarde quotidienne")', "b@test.local",
                    now - timedelta(minutes=2),
                    "Job Completion Status: Successful",
                    "Sauvegardes/Seanautic Marine", "auto",
                    client="Seanautic Marine"),
            RawMail("Timer service <root@JancorSV> bash /root/pbs.sh",
                    "b@test.local", now - timedelta(minutes=3),
                    "===== Starting backup: host/JancorSV =====\n"
                    "Client name: JancorSV\nStarting backup protocol: Fri\n"
                    "Duration: 42s\nEnd Time: Fri Jul 24 07:01:02 2026",
                    "Sauvegardes/Jancor", "auto", client="Jancor"),
            RawMail("Retrospect : état pour 24/07/2026", "b@test.local",
                    now - timedelta(minutes=4),
                    "Retrospect : état pour 24/07/2026 Sauvegardes "
                    "interrompues par l'opérateur: 1",
                    "Sauvegardes/Acco-Loisirs", "auto",
                    client="Acco-Loisirs"),
            RawMail("Sauvegarde: Site Manager Notification Email",
                    "b@test.local", now - timedelta(minutes=5),
                    "Disk Space Low Disk space on repository "
                    "\\\\Sauvegarde\\Historiques\\MacRium has fallen below "
                    "50 GB", "Sauvegardes/Hogue", "auto", client="Hogue"),
            # Alertes informatives Backup Exec (rapport diagnostic réel) :
            # catégorie « … Information » = sévérité minimale, pas un échec.
            RawMail('Backup Exec Alert: General Information '
                    '(Server: "SEA-MAIL")', "b@test.local",
                    now - timedelta(minutes=6),
                    '(Server: "SEA-MAIL") The following backup jobs are '
                    "using the Forever Incremental backup method.",
                    "Sauvegardes/Seanautic Marine", "auto",
                    client="Seanautic Marine"),
            RawMail('Backup Exec Alert: Database Maintenance Information '
                    '(Server: "SEA-MAIL") (Job: "Database Maintenance")',
                    "b@test.local", now - timedelta(minutes=7),
                    '(Server: "SEA-MAIL") (Job: "Database Maintenance") '
                    "Maintenance of application databases on server "
                    "SEA-MAIL\\BkupExec has started.",
                    "Sauvegardes/Seanautic Marine", "auto",
                    client="Seanautic Marine"),
            # 2e rapport diagnostic réel : « Job Success » (le corps dit
            # « Completed Successfully. », pas « Job Completion Status »),
            # « Backup with Warnings » (émoticône « Warning :| »),
            # sauvegarde infonuagique S3 en erreur, notification console
            # Retrospect avec récapitulatif « Erreurs : 0 ».
            RawMail('Backup Exec Alert: Job Success (Server: "SEA-MAIL") '
                    '(Job: "Backup all stations-Incremental")',
                    "b@test.local", now - timedelta(minutes=8),
                    '(Server: "SEA-MAIL") (Job: "Backup all '
                    'stations-Incremental") Completed Successfully.',
                    "Sauvegardes/Seanautic Marine", "auto",
                    client="Seanautic Marine"),
            RawMail("Production3-Planning Macrium Reflect - Backup with "
                    "Warnings", "b@test.local", now - timedelta(minutes=9),
                    "Warning :|", "Sauvegardes/Patt Technologies", "auto",
                    client="Patt Technologies"),
            RawMail("Backups S3 Errors occurred", "b@test.local",
                    now - timedelta(minutes=10),
                    '"Backups S3" Report Backup to: S3 Compatible Bucket '
                    'Backup type Cloud backup "Backups S3" Errors occurred',
                    "Sauvegardes/Seanautic Marine", "auto",
                    client="Seanautic Marine"),
            RawMail("ProActive - Remote - Ruscio Studio - Notification - "
                    "Retrospect", "b@test.local", now - timedelta(minutes=11),
                    "Management Console Script : ProActive - Remote - "
                    "Ruscio Studio Client : SBSSERVER * Erreurs : 0 *",
                    "Sauvegardes/Ruscio studio", "auto",
                    client="Ruscio studio"),
            # 3e rapport diagnostic réel : dépôt Site Manager injoignable,
            # opération bloquée par Macrium Image Guardian.
            RawMail("Win-Backups: Site Manager Notification Email",
                    "b@test.local", now - timedelta(minutes=12),
                    "Repository Uncontactable Repository NAS-JANCOR has "
                    "become uncontactable Please check the Site Manager "
                    "for further information.",
                    "Sauvegardes/Jancor", "auto", client="Jancor"),
            RawMail("Macrium Image Guardian - Event - SAUVEGARDE",
                    "b@test.local", now - timedelta(minutes=13),
                    "Blocked file operation File: E:\\MacRium\\SRV-X\\"
                    "DF367FE9701DF709-31-31.mrimg Process: Blocked file "
                    "operation", "Sauvegardes/Hogue", "auto", client="Hogue"),
        ]
        rev = {e.subject: e for e in analyze(cfg, reels)}
        cob = rev["Backup Summum (SERVEUR-PC)"]
        check("Cobian Reflector : produit reconnu (plus « macrium »), "
              "Errors: 0 = succès, machine et tâche extraites",
              cob.product == "cobian" and cob.status == STATUS_SUCCESS
              and cob.machine == "SERVEUR-PC" and cob.job == "Summum",
              f"{cob.product}/{cob.status}/{cob.machine}/{cob.job}")
        bex = rev['Backup Exec Alert: Job Completed (Server: "SEA-MAIL") '
                  '(Job: "Sauvegarde quotidienne")']
        check("Backup Exec : produit + succès + machine/tâche",
              bex.product == "backupexec" and bex.status == STATUS_SUCCESS
              and bex.machine == "SEA-MAIL"
              and bex.job == "Sauvegarde quotidienne",
              f"{bex.product}/{bex.status}/{bex.machine}/{bex.job}")
        pbc = rev["Timer service <root@JancorSV> bash /root/pbs.sh"]
        check("proxmox-backup-client : produit pbs, End Time = terminé, "
              "machine extraite",
              pbc.product == "pbs" and pbc.status == STATUS_SUCCESS
              and pbc.machine == "JancorSV",
              f"{pbc.product}/{pbc.status}/{pbc.machine}")
        dig = rev["Retrospect : état pour 24/07/2026"]
        check("Digest Retrospect : interrompue par l'opérateur = "
              "avertissement",
              dig.product == "retrospect"
              and dig.status == STATUS_WARNING, f"{dig.product}/{dig.status}")
        sml = rev["Sauvegarde: Site Manager Notification Email"]
        check("Site Manager : Disk Space Low = avertissement Macrium",
              sml.product == "macrium" and sml.status == STATUS_WARNING,
              f"{sml.product}/{sml.status}")
        gen = rev['Backup Exec Alert: General Information '
                  '(Server: "SEA-MAIL")']
        check("Backup Exec : alerte « General Information » = succès "
              "informatif (plus « inconnu »)",
              gen.product == "backupexec" and gen.status == STATUS_SUCCESS
              and gen.machine == "SEA-MAIL", f"{gen.product}/{gen.status}")
        dbm = rev['Backup Exec Alert: Database Maintenance Information '
                  '(Server: "SEA-MAIL") (Job: "Database Maintenance")']
        check("Backup Exec : « Database Maintenance Information » = succès, "
              "tâche extraite",
              dbm.product == "backupexec" and dbm.status == STATUS_SUCCESS
              and dbm.job == "Database Maintenance",
              f"{dbm.product}/{dbm.status}/{dbm.job}")
        bjs = rev['Backup Exec Alert: Job Success (Server: "SEA-MAIL") '
                  '(Job: "Backup all stations-Incremental")']
        check("Backup Exec : « Job Success »/« Completed Successfully » = "
              "succès, tâche extraite",
              bjs.product == "backupexec" and bjs.status == STATUS_SUCCESS
              and bjs.job == "Backup all stations-Incremental",
              f"{bjs.product}/{bjs.status}/{bjs.job}")
        mww = rev["Production3-Planning Macrium Reflect - Backup with "
                  "Warnings"]
        check("Macrium : « Backup with Warnings »/« Warning :| » = "
              "avertissement, machine du sujet",
              mww.product == "macrium" and mww.status == STATUS_WARNING
              and mww.machine == "Production3-Planning",
              f"{mww.product}/{mww.status}/{mww.machine}")
        s3e = rev["Backups S3 Errors occurred"]
        check("Macrium infonuagique : « Errors occurred » = erreur",
              s3e.product == "macrium" and s3e.status == STATUS_ERROR,
              f"{s3e.product}/{s3e.status}")
        rcn = rev["ProActive - Remote - Ruscio Studio - Notification - "
                  "Retrospect"]
        check("Console Retrospect : « - Notification - » avec "
              "« Erreurs : 0 » = succès, machine via « Client : »",
              rcn.product == "retrospect" and rcn.status == STATUS_SUCCESS
              and rcn.machine == "SBSSERVER",
              f"{rcn.product}/{rcn.status}/{rcn.machine}")
        unc = rev["Win-Backups: Site Manager Notification Email"]
        check("Site Manager : « Repository Uncontactable » = avertissement",
              unc.product == "macrium" and unc.status == STATUS_WARNING,
              f"{unc.product}/{unc.status}")
        mig = rev["Macrium Image Guardian - Event - SAUVEGARDE"]
        check("Image Guardian : opération bloquée = avertissement, machine "
              "du sujet",
              mig.product == "macrium" and mig.status == STATUS_WARNING
              and mig.machine == "SAUVEGARDE",
              f"{mig.product}/{mig.status}/{mig.machine}")

        # Nomenclature ProActive complète (capture réelle) : suffixes
        # « , M avertissements - Retrospect » et « Notification d'erreur »,
        # machine via « Client : X », détail du problème extrait.
        rus_mails = [
            RawMail("ProActive - Remote - Ruscio Studio - 2 erreurs, "
                    "4 avertissements - Retrospect", "retro@test.local",
                    now - timedelta(minutes=1),
                    "--Récapitulatif du script-- * Script: ProActive - "
                    "Remote - Ruscio Studio * Date: 2026-07-20 19:11 * "
                    "Erreurs: 2 * Avertissements: 4 * Performance: "
                    "20.6 Mo/min * Serveur: pve-Srv-Sauvegarde",
                    "Sauvegardes/Ruscio studio", "auto",
                    client="Ruscio studio"),
            RawMail("ProActive - Remote - Ruscio Studio - Notification "
                    "d'erreur - Retrospect", "retro@test.local",
                    now - timedelta(minutes=2),
                    "Script : ProActive - Remote - Ruscio Studio Client : "
                    "SBSSERVER (Ruscio Studio) Problème de lecture des "
                    "fichiers, erreur -519 (échec de la communication "
                    "réseau)", "Sauvegardes/Ruscio studio", "auto",
                    client="Ruscio studio"),
        ]
        rus = analyze(cfg, rus_mails)
        recap, notif = rus[0], rus[1]
        check("ProActive réel : « 2 erreurs, 4 avertissements » = erreur, "
              "détail extrait autour du compte d'erreurs",
              recap.status == STATUS_ERROR and "Erreurs: 2" in recap.problem,
              f"{recap.status} / {recap.problem[:80]}")
        check("ProActive réel : sujet complet → client extrait malgré les "
              "suffixes",
              classify({**cfg, "clients": []}, RawMail(
                  "ProActive - Remote - Boies-Grimard - 2 erreurs, "
                  "4 avertissements - Retrospect", "r@t.local", now, "",
                  "Backups/Retrospect", "retrospect")).client
              == "Boies-Grimard")
        check("ProActive réel : notification d'erreur = erreur, machine "
              "SBSSERVER via « Client : », erreur -519 dans le détail",
              notif.status == STATUS_ERROR and notif.machine == "SBSSERVER"
              and "-519" in notif.problem,
              f"{notif.status}/{notif.machine}/{notif.problem[:60]}")

        # Formes ANGLAISES documentées (docs.retrospect.com) : « N errors,
        # M warnings », « Error Notification », « Execution stopped by
        # operator », récapitulatif « Errors: 0 » ; et texte documenté du
        # blocage Image Guardian (« Blocked unauthorised process »).
        en_mails = [
            RawMail("ProActive - Remote - Acme Corp - 2 errors, 1 warnings "
                    "- Retrospect", "r@t.local", now - timedelta(minutes=1),
                    "* Errors: 2 * Warnings: 1 *", "Backups/Retrospect",
                    "retrospect"),
            RawMail("ProActive - Remote - Acme Corp - Error Notification - "
                    "Retrospect", "r@t.local", now - timedelta(minutes=2),
                    "Script: ProActive - Remote - Acme Corp",
                    "Backups/Retrospect", "retrospect"),
            RawMail("Execution stopped by operator - Retrospect",
                    "r@t.local", now - timedelta(minutes=3),
                    "Script stopped by operator", "Backups/Retrospect",
                    "retrospect"),
            RawMail("ProActive - Remote - Acme Corp - Notification - "
                    "Retrospect", "r@t.local", now - timedelta(minutes=4),
                    "Backup report * Errors: 0 * Duration: 00:12:34",
                    "Backups/Retrospect", "retrospect"),
            RawMail("Macrium Image Guardian - Event - SRV1", "m@t.local",
                    now - timedelta(minutes=5),
                    "Blocked unauthorised process (evil.exe) accessing "
                    "file (D:\\Backups\\img.mrimg)", "Backups/Macrium",
                    "auto"),
        ]
        en = analyze({**cfg, "clients": []}, en_mails)
        check("Retrospect EN : « 2 errors, 1 warnings » = erreur, client "
              "extrait du sujet",
              en[0].status == STATUS_ERROR and en[0].client == "Acme Corp",
              f"{en[0].status}/{en[0].client}")
        check("Retrospect EN : « Error Notification » = erreur",
              en[1].status == STATUS_ERROR, en[1].status)
        check("Retrospect EN : « Execution stopped by operator » = "
              "avertissement",
              en[2].status == STATUS_WARNING, en[2].status)
        check("Retrospect EN : console « Errors: 0 » = succès, client "
              "extrait aussi sans « d'erreur »",
              en[3].status == STATUS_SUCCESS and en[3].client == "Acme Corp",
              f"{en[3].status}/{en[3].client}")
        check("Image Guardian : « Blocked unauthorised process » (texte "
              "documenté) = avertissement",
              en[4].product == "macrium" and en[4].status == STATUS_WARNING,
              f"{en[4].product}/{en[4].status}")

        # Section « Problèmes en cours » : triage sans rien déplier —
        # client, tâche, depuis quand (historique), détail du problème.
        prob_hist = {"taches": {
            "retrospect:Ruscio studio:SBSSERVER": {"jours": {
                (now - timedelta(days=d)).strftime("%Y-%m-%d"): STATUS_ERROR
                for d in range(3)}}}}
        prob_page = render({**cfg, "expected_jobs": [], "clients": []},
                           rus, [], None, prob_hist)
        check("Problèmes en cours : section présente avec client et détail",
              'id="problemes"' in prob_page and "Ruscio studio" in prob_page
              and "-519" in prob_page)
        check("Problèmes en cours : détail sous le sujet dans les courriels",
              'class="prob prob-erreur"' in prob_page)
        all_green = render({**cfg, "expected_jobs": [], "clients": []},
                           analyze(cfg, [RawMail(
                               "Macrium Reflect Backup", "b@test.local", now,
                               "Backup completed successfully",
                               "Backups/Macrium", "macrium")]), [], None)
        check("Problèmes en cours : tout au vert = message rassurant",
              "Aucun problème en cours" in all_green)
        n_streak, d_streak = streak(
            {(now - timedelta(days=d)).strftime("%Y-%m-%d"): STATUS_ERROR
             for d in range(4)}, now)
        check("Historique : « depuis N jours » (série d'échecs consécutifs)",
              n_streak == 4
              and d_streak == (now - timedelta(days=3)).strftime("%Y-%m-%d"),
              f"{n_streak} / {d_streak}")

        # État unifié : une expected_job d'exemple (qui ne matche rien) ne
        # masque plus les problèmes des tâches observées — c'était le cas
        # réel : 2 tâches d'exemple « manquantes » et 271 erreurs invisibles
        # dans les tuiles et la vue par client.
        uni_cfg = {**cov_cfg, "expected_jobs": [
            {"name": "Tâche exemple fantôme", "product": "macrium",
             "match": "FANTOME-INEXISTANT", "every_hours": 24,
             "grace_hours": 6}]}
        uni_states = job_states(uni_cfg, cov_ev)
        uni_page = render(uni_cfg, cov_ev, uni_states, None)
        check("État unifié : une expected_job d'exemple ne masque plus les "
              "erreurs des tâches observées (tuiles + vue client)",
              'class="tile alert"' in uni_page
              and 'data-client="Ruscio Studio"' in uni_page)
        uni = current_states(uni_cfg, cov_ev, uni_states)
        check("État unifié : tâches attendues ET observées présentes",
              any(t["key"].startswith("tache:") for t in uni)
              and any(not t["key"].startswith("tache:") for t in uni)
              and any(t["etat"] == STATUS_ERROR for t in uni))

        # Motifs précompilés : une regex utilisateur invalide est ignorée
        # sans planter (les motifs valides continuent de classer), et une
        # section parsers différente invalide bien le cache de compilation.
        pc_cfg = {**cfg, "parsers": {"macrium": {
            "failure": ["(", "(?i)backup aborted"]}}}
        pc_ev = analyze(pc_cfg, [RawMail(
            "Macrium Reflect Backup", "b@test.local", now,
            "Backup aborted", "Backups/Macrium", "macrium")])
        check("Motifs : regex invalide ignorée, les valides classent",
              pc_ev[0].status == STATUS_ERROR, str(pc_ev[0]))
        pc_ev2 = analyze(cfg, [RawMail(
            "Macrium Reflect Backup", "b@test.local", now,
            "Backup completed successfully", "Backups/Macrium", "macrium")])
        check("Motifs : cache de compilation invalidé entre configs",
              pc_ev2[0].status == STATUS_SUCCESS, str(pc_ev2[0]))

        # Regex invalide dans expected_jobs : la tâche doit apparaître en
        # erreur de configuration, jamais en faux vert (« matche tout »).
        bad_cfg = {**cfg, "expected_jobs": [
            {"name": "Regex cassée", "product": "macrium",
             "match": "SRV-(", "every_hours": 24}]}
        bad_states = job_states(bad_cfg, events)
        check("Regex expected_jobs invalide → tâche en erreur de config",
              len(bad_states) == 1
              and bad_states[0].status == STATUS_ERROR
              and "invalide" in bad_states[0].due_note,
              str([(s.status, s.due_note) for s in bad_states]))

        # Notifications : uniquement sur transition d'état
        watch = {STATUS_ERROR, STATUS_MISSING}
        check("Notification : passage en erreur détecté",
              transitions({"tache:Image C": STATUS_SUCCESS},
                          {"tache:Image C": STATUS_ERROR}, watch) != [])
        check("Notification : état inchangé = silence",
              transitions({"tache:Image C": STATUS_ERROR},
                          {"tache:Image C": STATUS_ERROR}, watch) == [])
        check("Notification : retour au succès signalé",
              any("rétabli" in m for m in transitions(
                  {"tache:Image C": STATUS_ERROR},
                  {"tache:Image C": STATUS_SUCCESS}, watch)))
        check("Notification : nouvel état non surveillé = silence",
              transitions({}, {"tache:Image C": STATUS_WARNING}, watch) == [])
        # check_and_notify désactivé : mémorise l'état, n'envoie rien.
        n_sent, n_warn = check_and_notify(
            {**cfg, "notifications": {"enabled": False}}, events, states)
        check("Notification désactivée : état mémorisé, aucun envoi",
              n_sent == 0 and n_warn == []
              and os.path.exists(os.path.join(tmpdir, "dernier-etat.json")))

        # Historique quotidien : pire état par jour, mémorisé entre les runs
        hcfg = {**cfg, "history": {"enabled": True, "keep_days": 90,
                                   "show_days": 14},
                "expected_jobs": [
                    {"name": "Tâche H", "product": "macrium",
                     "match": "SRV-H", "every_hours": 24, "grace_hours": 6}]}
        hist_mails = [
            RawMail("Macrium Reflect Backup - SRV-H", "b@test.local",
                    now - timedelta(hours=2),
                    "Computer: SRV-H\nBackup completed successfully",
                    "Backups/Macrium", "macrium"),
            RawMail("Macrium Reflect Backup - SRV-H", "b@test.local",
                    now - timedelta(hours=26),
                    "Computer: SRV-H\nBackup aborted",
                    "Backups/Macrium", "macrium"),
        ]
        h_ev = analyze(hcfg, hist_mails)
        h = hist_update(hcfg, job_states(hcfg, h_ev), h_ev, now)
        jours = h["taches"]["tache:Tâche H"]["jours"]
        d_old = (now - timedelta(hours=26)).strftime("%Y-%m-%d")
        d_new = (now - timedelta(hours=2)).strftime("%Y-%m-%d")
        check("Historique : chaque courriel compte sur SON jour",
              jours.get(d_old) == STATUS_ERROR
              and jours.get(d_new) == STATUS_SUCCESS, str(jours))
        # Aggravation le même jour : le pire état du jour est retenu, et un
        # succès ultérieur du même jour ne l'efface pas.
        bad = [JobState(name="Tâche H", product="macrium",
                        status=STATUS_ERROR)]
        good = [JobState(name="Tâche H", product="macrium",
                         status=STATUS_SUCCESS)]
        h = hist_update(hcfg, bad, [], now)
        h = hist_update(hcfg, good, [], now)
        today = now.strftime("%Y-%m-%d")
        check("Historique : le pire état du jour est retenu",
              h["taches"]["tache:Tâche H"]["jours"][today] == STATUS_ERROR,
              str(h["taches"]["tache:Tâche H"]["jours"]))
        check("Historique : fichier écrit",
              os.path.exists(os.path.join(tmpdir, HISTORY_FILE)))
        # Taille automatique : un jour au-delà de keep_days disparaît.
        hist_update(hcfg, bad, [], now - timedelta(days=200))
        h = hist_update(hcfg, good, [], now)
        old_day = (now - timedelta(days=200)).strftime("%Y-%m-%d")
        check("Historique : jours au-delà de keep_days élagués",
              old_day not in h["taches"]["tache:Tâche H"]["jours"])
        ok30, seen30 = success_rate(
            {today: STATUS_SUCCESS, d_old: STATUS_ERROR,
             "2000-01-01": STATUS_SUCCESS}, now)
        check("Historique : taux de réussite limité aux 30 derniers jours",
              ok30 == 1 and seen30 == 2, f"{ok30}/{seen30}")
        # Sans expected_jobs : suivi par couple machine/tâche observé.
        h_free = hist_update({**hcfg, "expected_jobs": []}, [], h_ev, now)
        check("Historique sans expected_jobs : couple machine/tâche suivi",
              any(j.get("nom", "").startswith("SRV-H")
                  for j in h_free["taches"].values()),
              str(list(h_free["taches"])))
        # Rendu : section présente avec historique, absente sans.
        page_h = render(hcfg, h_ev, job_states(hcfg, h_ev), None, h)
        check("Tableau : section Historique (bande des jours + taux)",
              'id="historique"' in page_h and "Taux 30 j" in page_h)

        # suggest-jobs : fréquence estimée à partir des courriels observés
        sj_mails = [
            RawMail("Macrium Reflect Backup - SRV-TEST", "b@test.local",
                    now - timedelta(hours=h),
                    "Computer: SRV-TEST\nBackup Definition: 'Image C'\n"
                    "Backup completed successfully",
                    "Backups/Macrium", "macrium")
            for h in (2, 26, 50, 74)
        ]
        sugs = suggest_jobs(analyze(cfg, sj_mails))
        check("suggest-jobs : tâche quotidienne détectée (24 h)",
              len(sugs) == 1 and sugs[0]["every_hours"] == 24
              and re.search(sugs[0]["match"],
                            "Backup SRV-TEST Image C") is not None,
              str(sugs))

        # Cache de collecte : contenu réutilisé entre les cycles, élagage des
        # courriels disparus, invalidation sur changement de configuration.
        cpath = os.path.join(tmpdir, "cache", "cache-test.json")
        c1 = MailCache(cpath, "fp1").load()
        check("Cache : courriel inconnu au premier passage",
              c1.get("id-1") is None)
        c1.put("id-1", "Sujet", "exp@test.local", "corps X", "", "note")
        c1.save()
        c2 = MailCache(cpath, "fp1").load()
        got = c2.get("id-1")
        check("Cache : contenu retrouvé au cycle suivant",
              got is not None and got["corps"] == "corps X"
              and got["sujet"] == "Sujet")
        c2.put("id-2", "S2", "e@test.local", "c2", "", "")
        c2.save()  # id-1 revu (get) et id-2 ajouté : les deux gardés
        c3 = MailCache(cpath, "fp1").load()
        check("Cache : persistance entre exécutions",
              c3.get("id-2") is not None and "id-1" in c3.entries)
        c3.save()  # id-1 jamais revu pendant ce « run » → élagué
        c4 = MailCache(cpath, "fp1").load()
        check("Cache : courriel disparu (supprimé/déplacé) élagué",
              "id-1" not in c4.entries and "id-2" in c4.entries)
        c5 = MailCache(cpath, "fp2").load()
        check("Cache : empreinte différente (pièces jointes) = cache vidé",
              c5.get("id-2") is None)
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write("{corrompu")
        c6 = MailCache(cpath, "fp1").load()
        check("Cache : fichier corrompu = reparti à vide, sans erreur",
              c6.entries == {})
        cdis = MailCache(cpath, "fp1", enabled=False).load()
        check("Cache désactivé : fichier purgé, aucune réutilisation",
              not os.path.exists(cpath) and cdis.get("id-2") is None)
        # Option --no-cache (_refresh) : contenu existant ignoré au
        # chargement, mais le cache est reconstruit par save().
        ccfg = {"cache": {"enabled": True, "dir": "cache"}, "attachments": {},
                "_dir": tmpdir, "_path": os.path.join(tmpdir, "c.yaml")}
        cr = open_cache(ccfg)
        cr.put("id-R", "S", "e@test.local", "corps", "", "")
        cr.save()
        refreshed = open_cache({**ccfg,
                                "cache": {"enabled": True, "dir": "cache",
                                          "_refresh": True}})
        ignore_ok = refreshed.get("id-R") is None  # relecture forcée
        refreshed.put("id-R", "S", "e@test.local", "corps", "", "")
        refreshed.save()
        rebuilt_ok = open_cache(ccfg).get("id-R") is not None
        check("Option --no-cache : contenu ignoré mais cache reconstruit",
              ignore_ok and rebuilt_ok)
        check("Cache : l'empreinte suit la config des pièces jointes",
              fingerprint({"attachments": {"enabled": True}})
              == fingerprint({"attachments": {"enabled": True}})
              and fingerprint({"attachments": {"enabled": True}})
              != fingerprint({"attachments": {"enabled": False}}))

        # Commande find : extrait de contexte autour du mot-clé
        from . import fold_text
        from .__main__ import _context_excerpt
        ctx = _context_excerpt(
            "Journal de la nuit.\nErreur VSS 0x8004231f pendant l'image du "
            "volume C: — nouvelle tentative planifiée.", "vss")
        check("find : extrait de contexte autour du mot-clé",
              "VSS" in ctx and "0x8004231f" in ctx and "\n" not in ctx, ctx)
        # Recherche insensible aux accents : « echec » trouve « Échec », et
        # l'extrait affiché garde le texte original (accents compris).
        check("Recherche : pliage accents/casse (fold_text)",
              fold_text("Échec RÉUSSI à Montréal") == "echec reussi a montreal",
              fold_text("Échec RÉUSSI à Montréal"))
        ctx2 = _context_excerpt("Sauvegarde terminée : Échec du script.",
                                "echec")
        check("find : « echec » trouve « Échec », extrait original conservé",
              "Échec" in ctx2, ctx2)

        # Commande preload : bilan pur (comptes par dossier, période, états).
        from .__main__ import _preload_bilan
        pl_mails = [
            RawMail("A Macrium Reflect - Backup Success", "a@t.l",
                    now - timedelta(days=400), "Success :)",
                    "Sauvegardes/Alpha", "macrium"),
            RawMail("B Macrium Reflect - Backup Failed", "b@t.l",
                    now, "Failed :(", "Sauvegardes/Beta", "macrium"),
            RawMail("C Macrium Reflect - Backup Success", "c@t.l",
                    now, "Success :)", "Sauvegardes/Beta", "macrium"),
        ]
        bilan = "\n".join(_preload_bilan(pl_mails, analyze(cfg, pl_mails)))
        check("preload : bilan — comptes par dossier, période profonde, "
              "états",
              "     2  Sauvegardes/Beta" in bilan
              and "     1  Sauvegardes/Alpha" in bilan
              and f"du {(now - timedelta(days=400)):%Y-%m-%d}" in bilan
              and "erreur       : 1" in bilan
              and "succès       : 2" in bilan, bilan)

        # Rapport diagnostic : généré hors-ligne avec un faux connecteur,
        # sections clés présentes (inconnus avec extrait, cas à confirmer).
        import types
        from .rapport import RAPPORT_FILE, generate as rapport_generate
        r_mails = [
            RawMail("Macrium Reflect Backup - SRV-R", "b@test.local", now,
                    "Computer: SRV-R\nBackup completed successfully",
                    "Backups/Macrium", "macrium"),
            RawMail("Bulletin mensuel du fournisseur", "info@news.local",
                    now, "Promotion sur les licences.",
                    "Backups/Macrium", "macrium"),
            RawMail("vzdump backup status (h1.local): backup failed",
                    "pve@test.local", now, "TASK ERROR: job failed",
                    "Sauvegardes/Client PBS", "auto", client="Client PBS"),
        ]
        r_cfg = {**cfg,
                 "exchange": {"method": "outlook"}, "outlook": {"store": ""},
                 "client_folders": [], "folders": {"macrium": ["B/M"]}}
        fake_fetch = types.SimpleNamespace(
            fetch=lambda c, p: (r_mails, ["[macrium] B/M : 1 courriel(s) "
                                          "illisible(s) ignoré(s)"]))
        r_path = rapport_generate(r_cfg, None, fake_fetch,
                                  autotest_result=(0, 1, []))
        r_text = open(r_path, encoding="utf-8").read()
        check("Rapport : fichier généré (commande rapport)",
              r_path.endswith(RAPPORT_FILE) and os.path.exists(r_path))
        check("Rapport : courriel non reconnu listé avec extrait",
              "NON RECONNUS (1)" in r_text
              and "Bulletin mensuel du fournisseur" in r_text
              and "Promotion sur les licences" in r_text)
        check("Rapport : cas d'échec PBS à confirmer présent",
              "[pbs]" in r_text and "backup failed" in r_text)
        check("Rapport : sections environnement/autotest/collecte/journaux",
              all(s in r_text for s in
                  ("-- Environnement --", "-- Autotest --", "-- Collecte --",
                   "ILLISIBLE", "-- Journal backup-monitor.log")))

        # Verrous des pièces jointes
        check("Verrou expéditeur (rejet)",
              not sender_allowed("pirate@evil.com", ["backup@test.local"]))
        check("Verrou expéditeur (domaine accepté)",
              sender_allowed("x@ok.local", ["@ok.local"]))
        check("Rejet binaire déguisé en .txt",
              not looks_like_text(b"MZ\x90\x00"))
        conf = {"enabled": True, "allowed_senders": ["backup@test.local"],
                "allowed_extensions": [".txt", ".html"], "max_kb": 64}
        ok, _ = gate(conf, "pirate@evil.com", True)
        check("Verrou global expéditeur", not ok)
        text, note = extract(
            [("a.txt", 10, lambda: b"Backup failed"),
             ("b.exe", 10, lambda: b"MZ"),
             ("c.html", 30, lambda: b"<script>x()</script>Errors: 1")], conf)
        check("Pièces jointes : texte lu, exécutable rejeté, HTML nettoyé",
              "failed" in text and "Errors: 1" in text
              and "script" not in text and "type non autorisé" in note, note)

        # Génération du tableau
        html_text = render(cfg, events, states,
                           fetch_errors=["[macrium] Dossier/Test : erreur simulée"])
        out = write(cfg, html_text)
        page = open(out, encoding="utf-8").read()
        for needle, label in [
                ("clients-resume", "vue par client"),
                ("Collecte partielle", "bandeau d'erreur de collecte"),
                ("filtre-client", "filtre par client"),
                ("data-ts", "heures relatives"),
                ("Inconnus", "tuile des inconnus"),
                ("expander", "détail dépliable")]:
            check(f"Tableau : {label}", needle in page)
        check("Tableau : aucun script externe",
              "http://" not in page and "https://" not in page)
        check("Tableau : pas de section Historique sans données",
              'id="historique"' not in page)
        # La recherche du tableau couvre aussi l'extrait du contenu : un mot
        # présent seulement dans le CORPS doit être dans data-texte.
        row_attrs = re.findall(r'data-texte="([^"]*)"', page)
        check("Tableau : recherche par mot-clé du contenu (data-texte)",
              any("contenu quelconque" in a for a in row_attrs),
              str(row_attrs)[:200])
        check("Tableau : data-texte plié (sans accents) + requête pliée en JS",
              any("message sans mots-cles" in a for a in row_attrs)
              and "normalize('NFD')" in page,
              str(row_attrs)[:200])
        # Titre configurable (report.title), échappé
        page_t = render({**cfg, "report": {**cfg["report"],
                                           "title": "Sauvegardes <BG & Cie>"}},
                        events, states)
        check("Tableau : titre personnalisé (report.title) échappé",
              "Sauvegardes &lt;BG &amp; Cie&gt;</h1>" in page_t)

    # Configuration d'exemple (si PyYAML est présent — toujours le cas
    # après install.bat ; peut manquer sur un poste de développement)
    try:
        import yaml  # noqa: F401
        from .config import load
        example = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.example.yaml")
        if os.path.exists(example):
            cfg2 = load(example, require_folders=False)
            check("config.example.yaml valide",
                  cfg2["exchange"]["method"] == "outlook")
        # Les tâches d'EXEMPLE laissées dans un config.yaml copié sont
        # ignorées au chargement (elles créaient 2 faux « Manquant »).
        with tempfile.TemporaryDirectory() as tmp2:
            cfg_yaml = os.path.join(tmp2, "c.yaml")
            with open(cfg_yaml, "w", encoding="utf-8") as fh:
                fh.write(
                    'expected_jobs:\n'
                    '  - name: "SRV-FICHIERS — Image Macrium quotidienne"\n'
                    '    product: macrium\n'
                    '    match: "SRV-FICHIERS"\n'
                    '  - name: "Retrospect — Sauvegarde postes"\n'
                    '    product: retrospect\n'
                    '    match: "Sauvegarde postes"\n'
                    '  - name: "Ma vraie tâche"\n'
                    '    product: macrium\n'
                    '    match: "SRV-PROD"\n')
            if os.name == "posix":
                os.chmod(cfg_yaml, 0o600)
            cfg3 = load(cfg_yaml, require_folders=False)
            check("config : tâches d'exemple ignorées, les vraies gardées",
                  [j["name"] for j in cfg3["expected_jobs"]]
                  == ["Ma vraie tâche"],
                  str([j.get("name") for j in cfg3["expected_jobs"]]))
    except ImportError:
        check("config.example.yaml valide", True, "ignoré (PyYAML absent)")

    return results


def run() -> int:
    print("Autotest hors-ligne (sans Outlook ni réseau)…\n")
    results = _checks()
    failed = 0
    for name, ok, detail in results:
        mark = "✓" if ok else "✕"
        line = f"  {mark} {name}"
        if detail and not ok:
            line += f"  — {detail}"
        elif detail:
            line += f"  ({detail})"
        print(line)
        if not ok:
            failed += 1
    print()
    if failed:
        print(f"ÉCHEC : {failed}/{len(results)} vérifications en erreur.")
        return 1
    print(f"OK : {len(results)}/{len(results)} vérifications réussies. "
          "L'installation est fonctionnelle (hors connexion Outlook).")
    return 0


def run_quiet() -> tuple[int, int, list[str]]:
    """Variante silencieuse pour l'autotest à chaque utilisation (phase de
    rodage) : retourne (échecs, total, détail des échecs) sans rien imprimer."""
    results = _checks()
    failed = [f"{name}" + (f" — {detail}" if detail else "")
              for name, ok, detail in results if not ok]
    return len(failed), len(results), failed
