"""Cache local des courriels déjà lus (mode Outlook).

Relire ~1300 corps de courriels via COM toutes les 5 minutes est le poste de
coût principal d'un cycle : c'est la lecture du corps qui force Outlook à
charger l'élément complet. Ce cache mémorise le contenu déjà lu de chaque
courriel (clé : EntryID) ; au cycle suivant, seuls les courriels jamais vus
sont lus en entier — les autres ne coûtent que trois propriétés légères
(Class, ReceivedTime, EntryID).

Sûreté :
- un courriel supprimé, déplacé ou sorti de la fenêtre d'analyse disparaît du
  cache au premier cycle qui ne le rencontre plus (save() ne garde que les
  EntryID revus pendant le run) ;
- changer les réglages des pièces jointes vide le cache entier (empreinte) —
  le texte extrait en dépend ; recalibrer « parsers » n'a pas besoin de le
  vider, le classement se refait à chaque exécution sur le contenu brut ;
- fichier illisible ou corrompu = cache vide, jamais d'erreur ; le supprimer
  est toujours sans risque, il se reconstruit.

Confidentialité : le fichier contient sujet/expéditeur/corps des courriels de
la fenêtre d'analyse. Il vit donc PAR POSTE dans le profil local
(%LOCALAPPDATA%\\backup-monitor sous Windows, ~/.cache/backup-monitor
ailleurs) — jamais sur la clé USB, qui continue de ne porter aucun contenu de
courriel. Créé en 600 sous POSIX. « cache.enabled: false » le désactive et
supprime le fichier existant au passage.
"""

import hashlib
import json
import os
import tempfile

VERSION = 1
# Un corps de notification fait quelques Ko ; borne dure par champ pour que le
# fichier reste petit même si un courriel inattendu traîne un corps énorme.
MAX_FIELD = 100_000


def fingerprint(cfg: dict) -> str:
    """Empreinte des réglages qui changent le CONTENU mis en cache : la
    section attachments (verrous, extensions, taille plafond)."""
    src = json.dumps(cfg.get("attachments") or {}, sort_keys=True,
                     ensure_ascii=False, default=str)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]


def cache_path(cfg: dict) -> str:
    """Chemin du fichier cache : dossier du profil du poste (ou cache.dir),
    nom dérivé du chemin de config + boîte pour que deux configurations ne se
    marchent pas dessus."""
    conf = cfg.get("cache") or {}
    base = str(conf.get("dir") or "")
    if not base:
        if os.name == "nt":
            base = os.path.join(os.environ.get("LOCALAPPDATA")
                                or os.path.expanduser("~"), "backup-monitor")
        else:
            base = os.path.join(os.path.expanduser("~"), ".cache",
                                "backup-monitor")
    elif not os.path.isabs(base):
        base = os.path.join(cfg.get("_dir", "."), base)
    ident = hashlib.sha256(
        f"{cfg.get('_path', '')}|{(cfg.get('outlook') or {}).get('store', '')}"
        .encode("utf-8")).hexdigest()[:12]
    return os.path.join(base, f"cache-courriels-{ident}.json")


class MailCache:
    """dict persistant EntryID → contenu, avec suivi des clés revues."""

    def __init__(self, path: str, fp: str, enabled: bool = True):
        self.path = path
        self.fp = fp
        self.enabled = enabled
        self.entries: dict = {}
        self.seen: set = set()
        self.hits = 0
        self.misses = 0

    def load(self) -> "MailCache":
        if not self.enabled:
            # Désactivé : purger un éventuel fichier existant — il contient du
            # contenu de courriels que l'utilisateur ne veut plus voir stocké.
            try:
                os.unlink(self.path)
            except OSError:
                pass
            return self
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (isinstance(data, dict) and data.get("version") == VERSION
                    and data.get("empreinte") == self.fp
                    and isinstance(data.get("courriels"), dict)):
                self.entries = data["courriels"]
        except (OSError, ValueError):
            pass
        return self

    def get(self, key: str) -> dict | None:
        entry = self.entries.get(key) if self.enabled else None
        if entry is not None:
            self.seen.add(key)
            self.hits += 1
        else:
            self.misses += 1
        return entry

    def put(self, key: str, sujet: str, expediteur: str, corps: str,
            pj_texte: str, pj_note: str) -> None:
        if not self.enabled or not key:
            return
        self.entries[key] = {
            "sujet": sujet[:MAX_FIELD], "expediteur": expediteur,
            "corps": corps[:MAX_FIELD],
            "pj_texte": pj_texte[:MAX_FIELD], "pj_note": pj_note,
        }
        self.seen.add(key)

    def save(self) -> None:
        if not self.enabled:
            return
        kept = {k: v for k, v in self.entries.items() if k in self.seen}
        data = {"version": VERSION, "empreinte": self.fp, "courriels": kept}
        try:
            # mode= ne s'applique qu'à la création : un cache.dir existant
            # choisi par l'utilisateur n'est pas re-permissionné.
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path),
                                       prefix=".cache-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                if os.name == "posix":
                    os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            pass  # un cache qui ne s'écrit pas ne doit jamais casser l'analyse


def open_cache(cfg: dict) -> MailCache:
    conf = cfg.get("cache") or {}
    return MailCache(cache_path(cfg), fingerprint(cfg),
                     enabled=bool(conf.get("enabled", True))).load()
