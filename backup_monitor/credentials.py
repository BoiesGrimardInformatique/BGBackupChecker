"""Gestion sécuritaire du mot de passe : trousseau système (KWallet /
Secret Service) via keyring, avec repli sur la variable d'environnement
BACKUP_MONITOR_PASSWORD. Jamais de mot de passe dans config.yaml."""

import getpass
import os
import sys

import keyring

from . import KEYRING_SERVICE


def account_key(cfg: dict) -> str:
    return cfg["exchange"]["username"] or cfg["exchange"]["email"]


def get_password(cfg: dict) -> str:
    env = os.environ.get("BACKUP_MONITOR_PASSWORD")
    if env:
        return env
    pw = keyring.get_password(KEYRING_SERVICE, account_key(cfg))
    if not pw:
        sys.exit(
            "Aucun mot de passe trouvé dans le trousseau.\n"
            "Enregistrez-le une fois avec :  python -m backup_monitor set-password"
        )
    return pw


def set_password(cfg: dict) -> None:
    key = account_key(cfg)
    pw = getpass.getpass(f"Mot de passe pour {key} (stocké dans le trousseau) : ")
    if not pw:
        sys.exit("Mot de passe vide, abandon.")
    keyring.set_password(KEYRING_SERVICE, key, pw)
    print(f"Mot de passe enregistré dans le trousseau ({KEYRING_SERVICE} / {key}).")
