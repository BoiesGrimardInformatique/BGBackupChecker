"""backup-monitor — analyse en lecture seule des courriels de résultats
Macrium Reflect et Retrospect reçus dans une boîte Exchange, et génération
d'un tableau de bord HTML local. Aucun courriel n'est modifié, déplacé ni
marqué comme lu."""

from dataclasses import dataclass, field
from datetime import datetime

__version__ = "0.1.0"

KEYRING_SERVICE = "backup-monitor-exchange"

# Statuts possibles d'un événement de sauvegarde
STATUS_ERROR = "erreur"
STATUS_WARNING = "avertissement"
STATUS_SUCCESS = "succes"
STATUS_UNKNOWN = "inconnu"
STATUS_MISSING = "manquant"


@dataclass
class RawMail:
    """Courriel brut récupéré de la boîte (lecture seule)."""
    subject: str
    sender: str
    received: datetime
    body: str
    folder: str
    product: str  # "macrium" ou "retrospect"
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
