"""Récupération des courriels directement dans Outlook (Windows, COM/MAPI).

C'est la méthode recommandée quand l'outil tourne sur le poste qui a déjà la
boîte configurée dans Outlook : aucun mot de passe à stocker, aucun accès
serveur direct.

Garanties lecture seule :
- seuls Subject, SenderName/SenderEmailAddress, ReceivedTime et Body sont lus ;
- aucune propriété n'est écrite, aucun .Save(), .Move() ni .Delete() ;
- lire ces propriétés via COM ne marque pas le courriel comme lu et ne le
  déplace pas.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import win32com.client

from . import RawMail
from . import attachments as att_mod

OL_MAIL_ITEM = 43        # olMail
OL_FOLDER_INBOX = 6      # olFolderInbox
# Octets bruts d'une pièce jointe via MAPI — lus en mémoire, jamais sur disque
PR_ATTACH_DATA = "http://schemas.microsoft.com/mapi/proptag/0x37010102"

_INBOX_ALIASES = {"inbox", "boîte de réception", "boite de reception"}


def _namespace():
    try:
        app = win32com.client.Dispatch("Outlook.Application")
        return app.GetNamespace("MAPI")
    except Exception as exc:
        raise RuntimeError(
            "Impossible de contacter Outlook (COM) : Outlook est-il installé "
            f"et le profil configuré sur ce poste ? Détail : {exc}"
        ) from exc


def _resolve_folder(ns, path: str, store: str = ""):
    """Résout un chemin « Boîte de réception/Backups/Macrium »."""
    parts = [p for p in path.split("/") if p.strip()]
    if store:
        # Boîte explicite : marche littérale depuis sa racine (l'alias
        # « Boîte de réception » désignerait la boîte par défaut, pas celle-ci).
        folder = ns.Folders.Item(store)
    elif parts and parts[0].strip().lower() in _INBOX_ALIASES:
        folder = ns.GetDefaultFolder(OL_FOLDER_INBOX)
        parts = parts[1:]
    else:
        folder = ns.GetDefaultFolder(OL_FOLDER_INBOX).Parent
    for part in parts:
        try:
            folder = folder.Folders.Item(part)
        except Exception:
            raise RuntimeError(
                f"Dossier Outlook introuvable : « {part} » dans « {path} ». "
                "Utilisez « python -m backup_monitor folders » pour lister "
                "les chemins exacts."
            )
    return folder


def _walk(folder, prefix, store, default, out):
    try:
        subs = list(folder.Folders)
    except Exception:
        subs = []
    for sub in subs:
        path = f"{prefix}/{sub.Name}" if prefix else sub.Name
        try:
            count = sub.Items.Count
        except Exception:
            count = None
        out.append({"store": store, "path": path, "count": count,
                    "default": default})
        _walk(sub, path, store, default, out)


def folder_tree(cfg: dict, password=None) -> list[dict]:
    """Toutes les boîtes du profil Outlook et leurs dossiers (lecture seule).
    Les chemins sont relatifs à la racine de chaque boîte."""
    ns = _namespace()
    try:
        default_store = ns.GetDefaultFolder(OL_FOLDER_INBOX).Parent.Name
    except Exception:
        default_store = ""
    entries: list[dict] = []
    for store_root in ns.Folders:
        _walk(store_root, "", store_root.Name,
              store_root.Name == default_store, entries)
    return entries


def list_folders(cfg: dict, password=None) -> list[str]:
    lines = []
    for e in folder_tree(cfg, password):
        count = f"  ({e['count']} éléments)" if e["count"] is not None else ""
        lines.append(f"[{e['store']}] {e['path']}{count}")
    return lines


def _read_attachments(item, cfg: dict, sender: str) -> tuple[str, str]:
    conf = cfg.get("attachments") or {}
    try:
        count = item.Attachments.Count
    except Exception:
        count = 0
    allowed, note = att_mod.gate(conf, sender, count > 0)
    if not allowed:
        return "", note
    files = []
    for i in range(1, count + 1):
        att = item.Attachments.Item(i)

        def get_bytes(att=att):
            data = att.PropertyAccessor.GetProperty(PR_ATTACH_DATA)
            return bytes(data)

        files.append((str(att.FileName or ""), int(att.Size or 0), get_bytes))
    return att_mod.extract(files, conf)


def fetch(cfg: dict, password=None) -> tuple[list[RawMail], list[str]]:
    """Retourne (courriels, erreurs). Un dossier illisible n'interrompt pas
    la collecte des autres : l'erreur est rapportée dans la seconde liste."""
    tz = ZoneInfo(cfg["analysis"]["timezone"])
    since = datetime.now(tz) - timedelta(days=int(cfg["analysis"]["days_back"]))
    store = (cfg.get("outlook") or {}).get("store", "")
    ns = _namespace()
    mails: list[RawMail] = []
    errors: list[str] = []
    for product, paths in cfg["folders"].items():
        for path in paths or []:
            try:
                _fetch_folder(cfg, ns, store, product, path, since, tz, mails)
            except Exception as exc:
                errors.append(f"[{product}] {path} : {exc}")
    return mails, errors


def _fetch_folder(cfg, ns, store, product, path, since, tz,
                  mails: list[RawMail]) -> None:
    folder = _resolve_folder(ns, path, store)
    items = folder.Items
    items.Sort("[ReceivedTime]", True)  # du plus récent au plus ancien
    for item in items:
        if getattr(item, "Class", None) != OL_MAIL_ITEM:
            continue
        rt = item.ReceivedTime
        try:
            received = datetime.fromtimestamp(rt.timestamp(), tz)
        except (OSError, OverflowError, ValueError):
            continue
        if received < since:
            break  # items triés : tout le reste est plus ancien
        sender = ""
        try:
            sender = item.SenderEmailAddress or item.SenderName or ""
            if sender.startswith("/O="):  # adresse Exchange interne
                sender = item.SenderName or sender
        except Exception:
            pass
        att_text, att_note = _read_attachments(item, cfg, sender)
        mails.append(
            RawMail(
                subject=item.Subject or "",
                sender=sender,
                received=received,
                body=item.Body or "",
                folder=path,
                product=product,
                attachments_text=att_text,
                attachments_note=att_note,
            )
        )
