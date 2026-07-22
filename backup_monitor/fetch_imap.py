"""Récupération des courriels via IMAP — repli si EWS n'est pas disponible.

Garanties lecture seule :
- le dossier est ouvert avec select(readonly=True) : le serveur refuse toute
  modification de drapeaux ;
- le corps est lu avec BODY.PEEK[] : le courriel reste NON LU.
"""

import email
import email.policy
import imaplib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import RawMail
from . import attachments as att_mod

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _connect(cfg: dict, password: str) -> imaplib.IMAP4:
    im = cfg["imap"]
    user = cfg["exchange"]["username"] or cfg["exchange"]["email"]
    if im.get("ssl", True):
        conn = imaplib.IMAP4_SSL(im["server"], int(im.get("port", 993)))
    else:
        conn = imaplib.IMAP4(im["server"], int(im.get("port", 143)))
        conn.starttls()
    conn.login(user, password)
    return conn


def _body_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = att_mod.html_to_text(text)
    return text


def list_folders(cfg: dict, password: str) -> list[str]:
    conn = _connect(cfg, password)
    try:
        status, boxes = conn.list()
        return [b.decode("utf-8", "replace") for b in boxes or []]
    finally:
        conn.logout()


def folder_tree(cfg: dict, password: str) -> list[dict]:
    """Noms de dossiers IMAP (le dernier champ entre guillemets de LIST)."""
    entries: list[dict] = []
    for line in list_folders(cfg, password):
        m = re.findall(r'"([^"]+)"', line)
        name = m[-1] if m else line.rsplit(" ", 1)[-1]
        if name in ("/", "."):
            continue
        # Nom IMAP conservé tel quel : c'est lui que select() attend.
        entries.append({"store": cfg["exchange"]["email"], "path": name,
                        "count": None, "default": True})
    return entries


def fetch(cfg: dict, password: str) -> tuple[list[RawMail], list[str]]:
    """Retourne (courriels, erreurs). Un dossier illisible n'interrompt pas
    la collecte des autres : l'erreur est rapportée dans la seconde liste."""
    tz = ZoneInfo(cfg["analysis"]["timezone"])
    since = datetime.now(tz) - timedelta(days=int(cfg["analysis"]["days_back"]))
    since_imap = f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}"
    conn = _connect(cfg, password)
    mails: list[RawMail] = []
    errors: list[str] = []
    try:
        for product, paths in cfg["folders"].items():
            for path in paths or []:
                try:
                    _fetch_folder(cfg, conn, product, path, since, since_imap,
                                  tz, mails)
                except Exception as exc:
                    errors.append(f"[{product}] {path} : {exc}")
    finally:
        conn.logout()
    return mails, errors


def _fetch_folder(cfg, conn, product, path, since, since_imap, tz,
                  mails: list[RawMail]) -> None:
    status, _ = conn.select(f'"{path}"', readonly=True)
    if status != "OK":
        raise RuntimeError(f"Dossier IMAP introuvable : {path}")
    status, data = conn.search(None, f"(SINCE {since_imap})")
    if status != "OK":
        return
    for num in data[0].split():
        status, parts = conn.fetch(num, "(BODY.PEEK[])")
        if status != "OK" or not parts or parts[0] is None:
            continue
        msg = email.message_from_bytes(
            parts[0][1], policy=email.policy.default
        )
        try:
            received = email.utils.parsedate_to_datetime(msg["Date"])
            received = received.astimezone(tz)
        except Exception:
            received = datetime.now(tz)
        if received < since:
            continue
        sender_addr = email.utils.parseaddr(msg.get("From", ""))[1]
        att_conf = cfg.get("attachments") or {}
        parts = list(msg.iter_attachments())
        allowed, att_note = att_mod.gate(att_conf, sender_addr, bool(parts))
        att_text = ""
        if allowed:
            files = []
            for part in parts:
                payload = part.get_payload(decode=True) or b""
                files.append((part.get_filename() or "", len(payload),
                              lambda p=payload: p))
            att_text, att_note = att_mod.extract(files, att_conf)
        mails.append(
            RawMail(
                subject=msg.get("Subject", ""),
                sender=msg.get("From", ""),
                received=received,
                body=_body_text(msg),
                folder=path,
                product=product,
                attachments_text=att_text,
                attachments_note=att_note,
            )
        )
