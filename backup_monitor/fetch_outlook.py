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
from . import mailcache

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
    days = int(cfg["analysis"]["days_back"])
    # days_back <= 0 : AUCUNE limite de date — tout le dossier est analysé.
    since = (datetime.now(tz) - timedelta(days=days)) if days > 0 else None
    store = (cfg.get("outlook") or {}).get("store", "")
    ns = _namespace()
    cache = mailcache.open_cache(cfg)
    mails: list[RawMail] = []
    errors: list[str] = []
    # Dossiers par produit (macrium/retrospect explicites) : le dossier
    # choisi ET ses sous-dossiers (analysis.include_subfolders).
    for product, paths in cfg["folders"].items():
        for path in paths or []:
            try:
                folder = _resolve_folder(ns, path, store)
                _walk_product(cfg, folder, path, product, since, tz,
                              mails, errors, cache)
            except Exception as exc:
                errors.append(f"[{product}] {path} : {exc}")
    # Dossiers parents « auto » : chaque sous-dossier est un client.
    for parent in cfg.get("client_folders") or []:
        try:
            _fetch_client_parent(cfg, ns, store, parent, since, tz,
                                 mails, errors, cache)
        except Exception as exc:
            errors.append(f"[auto] {parent} : {exc}")
    cache.save()
    return mails, errors


def _walk_product(cfg, folder, path, product, since, tz,
                  mails: list[RawMail], errors: list[str], cache) -> None:
    """Lit un dossier produit ET ses sous-dossiers : un tri par sous-dossier
    (par machine, par année…) ne cache plus rien. Désactivable avec
    analysis.include_subfolders: false."""
    skipped = _read_items(cfg, folder, path, product, "", since, tz,
                          mails, cache)
    if skipped:
        errors.append(f"[{product}] {path} : {skipped} "
                      "courriel(s) illisible(s) ignoré(s)")
    if not (cfg.get("analysis") or {}).get("include_subfolders", True):
        return
    try:
        subs = list(folder.Folders)
    except Exception:
        subs = []
    for sub in subs:
        try:
            name = sub.Name
        except Exception:
            continue
        _walk_product(cfg, sub, f"{path}/{name}", product, since, tz,
                      mails, errors, cache)


def _fetch_client_parent(cfg, ns, store, parent_path, since, tz,
                         mails: list[RawMail], errors: list[str],
                         cache) -> None:
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
                     since, tz, mails, errors, cache)


def _walk_client(cfg, folder, path, client, since, tz,
                 mails: list[RawMail], errors: list[str], cache) -> None:
    """Lit un dossier client et ses éventuels sous-dossiers, tous rattachés au
    même client (le sous-dossier de premier niveau)."""
    skipped = _read_items(cfg, folder, path, "auto", client, since, tz,
                          mails, cache)
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
                     mails, errors, cache)


def _recent_items(folder, since):
    """Éléments du dossier, filtrés par date côté MAPI quand c'est possible.

    L'ancienne approche « trier puis s'arrêter au premier élément trop
    vieux » reposait sur le tri COM : s'il échoue SILENCIEUSEMENT (types
    mixtes, dossier particulier), l'ordre naturel — souvent du plus ancien
    au plus récent — faisait couper la lecture immédiatement : dossier
    perçu comme vide, sans aucune erreur signalée. Plus aucune coupe ne
    dépend du tri : Restrict filtre côté MAPI, et s'il échoue on parcourt
    TOUT le dossier (le filtre par date est réappliqué courriel par
    courriel dans l'appelant, dans tous les cas)."""
    items = folder.Items
    if since is not None:
        try:
            # Format de date américain : le plus largement accepté par le
            # moteur JET d'Outlook, indépendamment de la locale du poste.
            return items.Restrict(
                "[ReceivedTime] >= '" + since.strftime("%m/%d/%Y %H:%M") + "'")
        except Exception:
            pass  # repli : parcours complet ci-dessous
    return items


def _read_items(cfg, folder, path, product, client, since, tz,
                mails: list[RawMail], cache) -> int:
    """Lit les courriels d'UN dossier (déjà résolu) et les ajoute à `mails`.
    `product` peut être « auto » (détecté ensuite au contenu) et `client` le
    nom du dossier client (vide pour les dossiers par produit). `since` à
    None = aucune limite de date (analysis.days_back: 0).
    Retourne le nombre de courriels illisibles ignorés."""
    items = _recent_items(folder, since)
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
            if since is not None and received < since:
                continue  # jamais de break : l'ordre n'est pas garanti
            # Cache : un courriel déjà lu lors d'un cycle précédent ne coûte
            # que trois propriétés légères (Class, ReceivedTime, EntryID) —
            # c'est la lecture du corps qui force Outlook à charger l'élément
            # complet, et c'est elle qu'on évite. Dossier/produit/client
            # viennent toujours du parcours COURANT : un courriel déplacé
            # d'un dossier client à un autre reste bien attribué.
            entry_id = ""
            try:
                entry_id = str(item.EntryID or "")
            except Exception:
                pass
            cached = cache.get(entry_id) if entry_id else None
            if cached is not None:
                mails.append(
                    RawMail(
                        subject=cached.get("sujet", ""),
                        sender=cached.get("expediteur", ""),
                        received=received,
                        body=cached.get("corps", ""),
                        folder=path,
                        product=product,
                        client=client,
                        attachments_text=cached.get("pj_texte", ""),
                        attachments_note=cached.get("pj_note", ""),
                    )
                )
                continue
            sender = ""
            try:
                sender = item.SenderEmailAddress or item.SenderName or ""
                if sender.startswith("/O="):  # adresse Exchange interne
                    sender = item.SenderName or sender
            except Exception:
                pass
            att_text, att_note = _read_attachments(item, cfg, sender)
            subject = item.Subject or ""
            body = item.Body or ""
            mails.append(
                RawMail(
                    subject=subject,
                    sender=sender,
                    received=received,
                    body=body,
                    folder=path,
                    product=product,
                    client=client,
                    attachments_text=att_text,
                    attachments_note=att_note,
                )
            )
            cache.put(entry_id, subject, sender, body, att_text, att_note)
        except Exception:
            skipped += 1
    return skipped
