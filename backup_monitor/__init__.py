"""backup-monitor — analyse en lecture seule des courriels de résultats de
sauvegarde (Macrium Reflect, Retrospect, SQL Server Agent, Proxmox Backup
Server, scripts personnalisés) reçus dans une boîte Exchange, et génération
d'un tableau de bord HTML local. Aucun courriel n'est modifié, déplacé ni
marqué comme lu."""

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__version__ = "0.1.0"

KEYRING_SERVICE = "backup-monitor-exchange"

# Statuts possibles d'un événement de sauvegarde
STATUS_ERROR = "erreur"
STATUS_WARNING = "avertissement"
STATUS_SUCCESS = "succes"
STATUS_UNKNOWN = "inconnu"
STATUS_MISSING = "manquant"

# Du plus grave au moins grave — ordre partagé par la vue par client du
# tableau et par l'agrégation « pire état du jour » de l'historique.
SEVERITY = [STATUS_ERROR, STATUS_MISSING, STATUS_WARNING, STATUS_UNKNOWN,
            STATUS_SUCCESS]

# Code de sortie signalant « pas encore configuré » : fichier config.yaml
# absent, ou aucun dossier à surveiller choisi. Distinct d'une vraie panne
# (code 1) pour que les lanceurs (lancer.bat, install.bat) puissent enchaîner
# automatiquement sur l'assistant « setup » plutôt que d'afficher une erreur.
EXIT_NOT_CONFIGURED = 3


def fold_text(s: str) -> str:
    """Minuscules sans accents (É→e) — base commune de la recherche par
    mots-clés (commande find et champ du tableau) : « echec » trouve
    « Échec » et inversement."""
    return "".join(c.lower()
                   for c in unicodedata.normalize("NFD", str(s or ""))
                   if not unicodedata.combining(c))


def load_timezone(name: str) -> ZoneInfo:
    """Charge un fuseau horaire IANA (ex. « America/Toronto »).

    zoneinfo n'embarque pas la base des fuseaux horaires : sous Windows, où le
    système n'en fournit aucune, le paquet « tzdata » (déclaré dans
    requirements.txt) est requis. En son absence — ou si le nom est invalide —
    on émet un message clair et actionnable plutôt qu'une trace d'exécution."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise SystemExit(
            f"Fuseau horaire « {name} » introuvable : la base des fuseaux "
            "horaires (paquet « tzdata ») est manquante, ou le nom est "
            "invalide.\n"
            "Corrigez en relançant install.bat, ou installez la base :\n"
            "  venv\\Scripts\\python -m pip install tzdata"
        )


@dataclass
class RawMail:
    """Courriel brut récupéré de la boîte (lecture seule)."""
    subject: str
    sender: str
    received: datetime
    body: str
    folder: str
    product: str  # "macrium", "retrospect", ou "auto" (à détecter au contenu)
    client: str = ""  # rempli quand le dossier désigne le client (mode auto)
    attachments_text: str = ""  # texte des pièces jointes autorisées
    attachments_note: str = ""  # résumé (analysées / ignorées et pourquoi)


@dataclass
class BackupEvent:
    """Résultat de l'analyse d'un courriel de backup."""
    product: str
    status: str
    subject: str
    sender: str
    received: datetime
    folder: str
    machine: str = ""
    job: str = ""
    matched_pattern: str = ""
    excerpt: str = ""  # début du corps, pour le panneau de détail du tableau
    attachments_note: str = ""
    client: str = ""  # client associé via la section « clients » de config.yaml


@dataclass
class JobState:
    """État courant d'une tâche de sauvegarde attendue."""
    name: str
    product: str
    status: str  # statut du dernier événement, ou STATUS_MISSING
    last_event: BackupEvent | None = None
    due_note: str = ""
    client: str = ""
