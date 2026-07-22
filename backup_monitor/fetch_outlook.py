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

import win32com.client

from . import RawMail, load_timezone
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
        try:
            name = sub.Name
        except Exception:
            continue
        path = f"{prefix}/{name}" if prefix else name
        # Le nombre d'éléments n'est PAS lu : le compter obligerait Outlook à
        # énumérer chaque dossier, ce qui fige l'application sur les grosses
        # boîtes. Le dossier se choisit par son nom.
        out.append({"store": store, "path": path, "count": None,
                    "default": default})
        _walk(sub, path, store, default, out)


def list_stores(cfg: dict, password=None) -> list[dict]:
    """Noms des boîtes (magasins) du profil Outlook, SANS balayer les dossiers.
    Opération légère : elle permet de choisir la boîte AVANT le scan complet,
    pour ne pas figer Outlook en énumérant tout le profil (boîtes partagées,
    archives en ligne…)."""
    ns = _namespace()
    try:
        default_store = ns.GetDefaultFolder(OL_FOLDER_INBOX).Parent.Name
    except Exception:
        default_store = ""
    stores: list[dict] = []
    for root in ns.Folders:
        try:
            name = root.Name
        except Exception:
            continue
        stores.append({"name": name, "default": name == default_store})
    return stores


def folder_tree(cfg: dict, password=None, store: str = "") -> list[dict]:
    """Dossiers d'une boîte (celle nommée par `store`, ou toutes si vide), en
    lecture seule. Les chemins sont relatifs à la racine de chaque boîte."""
    ns = _namespace()
    try:
        default_store = ns.GetDefaultFolder(OL_FOLDER_INBOX).Parent.Name
    except Exception:
        default_store = ""
    entries: list[dict] = []
    for store_root in ns.Folders:
        try:
            name = store_root.Name
        except Exception:
            continue
        if store and name != store:
            continue
        _walk(store_root, "", name, name == default_store, entries)
    return entries


def list_folders(cfg: dict, password=None) -> list[str]:
    # Limité à la boîte configurée (outlook.store) quand elle est connue :
    # énumérer tout le profil (boîtes partagées, archives en ligne…) fige
    # Outlook — même raison que le choix de boîte préalable du setup.
    store = (cfg.get("outlook") or {}).get("store", "")
    return [f"[{e['store']}] {e['path']}"
            for e in folder_tree(cfg, password, store=store)]


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
    tz = load_timezone(cfg["analysis"]["timezone"])
    since = datetime.now(tz) - timedelta(days=int(cfg["analysis"]["days_back"]))
    store = (cfg.get("outlook") or {}).get("store", "")
    ns = _namespace()
    mails: list[RawMail] = []
    errors: list[str] = []
    # Dossiers par produit (macrium/retrospect explicites).
    for product, paths in cfg["folders"].items():
        for path in paths or []:
            try:
                folder = _resolve_folder(ns, path, store)
                skipped = _read_items(cfg, folder, path, product, "",
                                      since, tz, mails)
                if skipped:
                    errors.append(f"[{product}] {path} : {skipped} "
                                  "courriel(s) illisible(s) ignoré(s)")
            except Exception as exc:
                errors.append(f"[{product}] {path} : {exc}")
    # Dossiers parents « auto » : chaque sous-dossier est un client.
    for parent in cfg.get("client_folders") or []:
        try:
            _fetch_client_parent(cfg, ns, store, parent, since, tz,
                                 mails, errors)
        except Exception as exc:
            errors.append(f"[auto] {parent} : {exc}")
    return mails, errors


def _fetch_client_parent(cfg, ns, store, parent_path, since, tz,
                         mails: list[RawMail], errors: list[str]) -> None:
    """Explore un dossier parent : chaque sous-dossier DIRECT est un client
    (son nom) ; le produit est détecté au contenu de chaque courriel."""
    parent = _resolve_folder(ns, parent_path, store)
    try:
        subs = list(parent.Folders)
    except Exception:
        subs = []
    if not subs:
        raise RuntimeError("aucun sous-dossier (client) à explorer")
    for sub in subs:
        try:
            client = sub.Name
        except Exception:
            continue
        _walk_client(cfg, sub, f"{parent_path}/{client}", client,
                     since, tz, mails, errors)


def _walk_client(cfg, folder, path, client, since, tz,
                 mails: list[RawMail], errors: list[str]) -> None:
    """Lit un dossier client et ses éventuels sous-dossiers, tous rattachés au
    même client (le sous-dossier de premier niveau)."""
    skipped = _read_items(cfg, folder, path, "auto", client, since, tz, mails)
    if skipped:
        errors.append(f"[auto] {path} : {skipped} "
                      "courriel(s) illisible(s) ignoré(s)")
    try:
        subs = list(folder.Folders)
    except Exception:
        subs = []
    for sub in subs:
        try:
            name = sub.Name
        except Exception:
            continue
        _walk_client(cfg, sub, f"{path}/{name}", client, since, tz,
                     mails, errors)


def _read_items(cfg, folder, path, product, client, since, tz,
                mails: list[RawMail]) -> int:
    """Lit les courriels récents d'UN dossier (déjà résolu) et les ajoute à
    `mails`. `product` peut être « auto » (détecté ensuite au contenu) et
    `client` le nom du dossier client (vide pour les dossiers par produit).
    Retourne le nombre de courriels illisibles ignorés."""
    items = folder.Items
    items.Sort("[ReceivedTime]", True)  # du plus récent au plus ancien
    skipped = 0
    for item in items:
        # Isolation PAR COURRIEL : un seul élément corrompu (corps non
        # téléchargé, propriété MAPI illisible…) ne doit pas faire classer
        # tout le dossier « illisible » — ce qui créerait de faux
        # « Manquants ». L'élément est ignoré et compté.
        try:
            if getattr(item, "Class", None) != OL_MAIL_ITEM:
                continue
            rt = item.ReceivedTime
            try:
                received = datetime.fromtimestamp(rt.timestamp(), tz)
            except (OSError, OverflowError, ValueError):
                skipped += 1
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
                    client=client,
                    attachments_text=att_text,
                    attachments_note=att_note,
                )
            )
        except Exception:
            skipped += 1
    return skipped
