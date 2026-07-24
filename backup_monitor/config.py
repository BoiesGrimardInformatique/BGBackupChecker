"""Chargement et validation de config.yaml."""

import copy
import os
import sys

import yaml

from . import EXIT_NOT_CONFIGURED

DEFAULTS = {
    "exchange": {
        "method": "outlook",
        "email": "",
        "username": "",
        "auth": "ntlm",
        "ews_url": "",
        "verify_ssl": True,
    },
    "outlook": {"store": ""},
    # Cache local des courriels déjà lus (mode Outlook) : seuls les nouveaux
    # courriels sont lus en entier à chaque cycle. Fichier PAR POSTE (profil
    # local, jamais sur la clé USB) ; dir vide = emplacement par défaut.
    "cache": {"enabled": True, "dir": ""},
    "imap": {"server": "", "port": 993, "ssl": True},
    "folders": {"macrium": [], "retrospect": []},
    # Dossiers parents explorés récursivement : chaque sous-dossier est un
    # client (son nom), le produit est détecté au contenu. Mode Outlook.
    "client_folders": [],
    # days_back: 0 = AUCUNE limite de date (tout le dossier est analysé).
    # include_subfolders : les dossiers choisis (folders.*) incluent leurs
    # sous-dossiers — un tri par machine/année ne cache rien.
    "analysis": {"days_back": 14, "timezone": "America/Toronto",
                 "include_subfolders": True},
    "attachments": {
        "enabled": False,
        "allowed_senders": [],
        "allowed_extensions": [".txt", ".log", ".htm", ".html", ".pdf"],
        "max_kb": 4096,
    },
    # Phase de rodage : autotest complet à chaque utilisation, journalisé
    # dans autotest.log. Passer on_each_run à false à la version finale.
    "autotest": {"on_each_run": True},
    # Historique quotidien (historique.json) : pire état de chaque jour par
    # tâche suivie, affiché dans le tableau (bande des derniers jours + taux
    # de réussite sur 30 jours). Seule la commande « run » l'écrit.
    "history": {"enabled": True, "keep_days": 90, "show_days": 14},
    "expected_jobs": [],
    "clients": [],
    # Notifications sortantes sur TRANSITION d'état (jamais à chaque cycle).
    # Désactivées par défaut — voir config.example.yaml pour la doc complète.
    "notifications": {
        "enabled": False,
        "methods": [],            # "toast" (Windows) et/ou "webhook"
        "webhook_url": "",
        "webhook_format": "text",  # "text" (ntfy) ou "json" (Teams/Slack)
        "notify_on": ["erreur", "manquant"],
        "notify_recovery": True,
    },
    "report": {
        "output": "tableau-backups.html",
        "refresh_seconds": 300,
        "max_rows": 100,
        # Titre du tableau (en-tête et onglet du navigateur) — pratique pour
        # le personnaliser par site ou par entreprise.
        "title": "État des sauvegardes",
    },
    "parsers": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str, require_folders: bool = True) -> dict:
    if not os.path.exists(path):
        print(
            f"Configuration introuvable : {path}\n"
            "Lancez « python -m backup_monitor setup » pour la créer en "
            "choisissant la boîte et les dossiers à surveiller.",
            file=sys.stderr,
        )
        sys.exit(EXIT_NOT_CONFIGURED)
    if os.name == "posix":  # sous Windows/FAT32 le mode est artificiel
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o077:
            print(
                f"AVERTISSEMENT : {path} est lisible par d'autres comptes "
                f"(permissions {oct(mode)[2:]}). Recommandé : chmod 600 {path}",
                file=sys.stderr,
            )
    with open(path, encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    cfg = _deep_merge(DEFAULTS, user_cfg)

    method = str(cfg["exchange"]["method"]).lower()
    if method not in ("outlook", "ews", "imap"):
        sys.exit(f"config.yaml : méthode inconnue « {method} » "
                 "(valeurs possibles : outlook, ews, imap).")
    if method != "outlook" and not cfg["exchange"]["email"]:
        sys.exit("config.yaml : exchange.email est requis pour ews/imap.")
    if method != "outlook" and not cfg["exchange"].get("verify_ssl", True):
        print(
            "AVERTISSEMENT : exchange.verify_ssl est désactivé — le serveur "
            "ne sera pas authentifié (mot de passe exposé à un intercepteur). "
            "Réservé aux tests.",
            file=sys.stderr,
        )
    if require_folders and not (
            any(cfg["folders"].get(p) for p in ("macrium", "retrospect"))
            or cfg.get("client_folders")):
        print(
            "config.yaml : aucun dossier à surveiller n'est défini.\n"
            "Lancez « python -m backup_monitor setup » pour scanner la boîte "
            "et choisir les dossiers.",
            file=sys.stderr,
        )
        sys.exit(EXIT_NOT_CONFIGURED)
    cfg["_path"] = os.path.abspath(path)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg
