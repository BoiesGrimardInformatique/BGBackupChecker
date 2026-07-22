"""Autotest hors-ligne : valide l'installation (analyseurs, verrous des
pièces jointes, génération du tableau) SANS toucher à Outlook ni au réseau.
À lancer après install.bat :  python -m backup_monitor selftest"""

import os
import tempfile
from datetime import datetime, timedelta

from . import (RawMail, STATUS_ERROR, STATUS_MISSING, STATUS_SUCCESS,
               STATUS_UNKNOWN, STATUS_WARNING, load_timezone)
from .attachments import extract, gate, looks_like_text, sender_allowed
from .parsers import analyze, job_states
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
