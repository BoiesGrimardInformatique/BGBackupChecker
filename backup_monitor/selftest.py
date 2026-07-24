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
from .history import HISTORY_FILE, success_rate
from .history import update as hist_update
from .mailcache import MailCache, fingerprint, open_cache
from .notify import check_and_notify, transitions
from .parsers import analyze, job_states, suggest_jobs
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
