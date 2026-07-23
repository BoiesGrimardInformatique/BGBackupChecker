"""Récupération des courriels via EWS (exchangelib) — STRICTEMENT en lecture.

Garanties lecture seule :
- accès DELEGATE, aucune méthode d'écriture appelée (pas de .save(), .move(),
  .delete(), ni modification de is_read) ;
- la simple lecture EWS ne marque pas les courriels comme lus.
"""

from datetime import datetime, timedelta

from exchangelib import (
    Account,
    Configuration,
    Credentials,
    DELEGATE,
    EWSTimeZone,
    NTLM,
    BASIC,
)
from exchangelib.attachments import FileAttachment
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

from . import RawMail
from . import attachments as att_mod


def _connect(cfg: dict, password: str) -> Account:
    ex = cfg["exchange"]
    if not ex["verify_ssl"]:
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
    creds = Credentials(username=ex["username"] or ex["email"], password=password)
    auth_type = NTLM if ex["auth"].lower() == "ntlm" else BASIC
    if ex["ews_url"]:
        conf = Configuration(
            service_endpoint=ex["ews_url"], credentials=creds, auth_type=auth_type
        )
        return Account(
            primary_smtp_address=ex["email"],
            config=conf,
            autodiscover=False,
            access_type=DELEGATE,
        )
    return Account(
        primary_smtp_address=ex["email"],
        credentials=creds,
        autodiscover=True,
        access_type=DELEGATE,
    )


def _resolve_folder(account: Account, path: str):
    """Résout un chemin « A/B/C » depuis la racine des dossiers de la boîte."""
    node = account.msg_folder_root
    parts = [p for p in path.split("/") if p.strip()]
    # Tolère un premier segment « Boîte de réception » / « Inbox »
    aliases = {"inbox", "boîte de réception", "boite de reception"}
    for i, part in enumerate(parts):
        if i == 0 and part.strip().lower() in aliases:
            node = account.inbox
            continue
        node = node / part
    return node


def folder_tree(cfg: dict, password: str) -> list[dict]:
    """Dossiers de la boîte, chemins relatifs à la racine des dossiers."""
    account = _connect(cfg, password)
    entries: list[dict] = []

    def walk(folder, prefix):
        try:
            children = list(folder.children)
        except Exception:
            children = []
        for child in children:
            path = f"{prefix}/{child.name}" if prefix else child.name
            try:
                count = child.total_count
            except Exception:
                count = None
            entries.append({"store": cfg["exchange"]["email"], "path": path,
                            "count": count, "default": True})
            walk(child, path)

    walk(account.msg_folder_root, "")
    return entries


def list_folders(cfg: dict, password: str) -> list[str]:
    lines = []
    for e in folder_tree(cfg, password):
        count = f"  ({e['count']} courriels)" if e["count"] is not None else ""
        lines.append(f"{e['path']}{count}")
    return lines


def fetch(cfg: dict, password: str) -> tuple[list[RawMail], list[str]]:
    """Retourne (courriels, erreurs). Un dossier illisible n'interrompt pas
    la collecte des autres : l'erreur est rapportée dans la seconde liste."""
    account = _connect(cfg, password)
    tz = EWSTimeZone(cfg["analysis"]["timezone"])
    since = datetime.now(tz) - timedelta(days=int(cfg["analysis"]["days_back"]))
    att_conf = cfg.get("attachments") or {}
    fields = ["subject", "sender", "datetime_received", "text_body"]
    if att_conf.get("enabled"):
        fields.append("attachments")
    mails: list[RawMail] = []
    errors: list[str] = []
    for product, paths in cfg["folders"].items():
        for path in paths or []:
            try:
                skipped = _fetch_folder(cfg, account, product, path, since,
                                        tz, fields, att_conf, mails)
                if skipped:
                    errors.append(f"[{product}] {path} : {skipped} "
                                  "courriel(s) illisible(s) ignoré(s)")
            except Exception as exc:
                errors.append(f"[{product}] {path} : {exc}")
    return mails, errors


def _fetch_folder(cfg, account, product, path, since, tz, fields, att_conf,
                  mails: list[RawMail]) -> int:
    folder = _resolve_folder(account, path)
    qs = (
        folder.filter(datetime_received__gte=since)
        .only(*fields)
        .order_by("-datetime_received")
    )
    skipped = 0
    for item in qs:
        # Isolation PAR COURRIEL : un élément corrompu est ignoré et compté,
        # sans faire classer tout le dossier « illisible ».
        try:
            sender = ""
            if item.sender:
                sender = item.sender.email_address or item.sender.name or ""
            att_text, att_note = "", ""
            atts = [a for a in (item.attachments or [])
                    if isinstance(a, FileAttachment)]
            allowed, att_note = att_mod.gate(att_conf, sender, bool(atts))
            if allowed:
                files = [(a.name or "", a.size or 0,
                          lambda a=a: a.content) for a in atts]
                att_text, att_note = att_mod.extract(files, att_conf)
            mails.append(
                RawMail(
                    subject=item.subject or "",
                    sender=sender,
                    received=item.datetime_received.astimezone(tz),
                    body=item.text_body or "",
                    folder=path,
                    product=product,
                    attachments_text=att_text,
                    attachments_note=att_note,
                )
            )
        except Exception:
            skipped += 1
    return skipped
