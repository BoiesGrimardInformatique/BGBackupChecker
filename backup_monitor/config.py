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
    "imap": {"server": "", "port": 993, "ssl": True},
    "folders": {"macrium": [], "retrospect": []},
    "analysis": {"days_back": 14, "timezone": "America/Toronto"},
    "attachments": {
        "enabled": False,
        "allowed_senders": [],
        "allowed_extensions": [".txt", ".log", ".htm", ".html", ".pdf"],
        "max_kb": 4096,
    },
    "expected_jobs": [],
    "clients": [],
    "report": {
        "output": "tableau-backups.html",
        "refresh_seconds": 300,
        "max_rows": 100,
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
    if require_folders and not any(
            cfg["folders"].get(p) for p in ("macrium", "retrospect")):
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
