"""Autotest hors-ligne : valide l'installation (analyseurs, verrous des
pièces jointes, génération du tableau) SANS toucher à Outlook ni au réseau.
À lancer après install.bat :  python -m backup_monitor selftest"""

import os
import re
import tempfile
from datetime import datetime, timedelta

from . import (RawMail, STATUS_ERROR, STATUS_MISSING, STATUS_SUCCESS,
               STATUS_UNKNOWN, STATUS_WARNING, load_timezone)
from .attachments import extract, gate, looks_like_text, sender_allowed
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
