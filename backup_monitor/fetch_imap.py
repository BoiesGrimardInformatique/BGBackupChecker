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
import ssl
from datetime import datetime, timedelta

from . import RawMail, load_timezone
from . import attachments as att_mod

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _connect(cfg: dict, password: str) -> imaplib.IMAP4:
    im = cfg["imap"]
    user = cfg["exchange"]["username"] or cfg["exchange"]["email"]
    # Contexte TLS explicite : sans lui, imaplib N'AUTHENTIFIE PAS le serveur
    # (verify_mode=CERT_NONE) et le mot de passe partirait vers n'importe qui.
    # verify_ssl: false (labo seulement) désactive la vérification, avec
    # l'avertissement émis par config.load.
    ctx = ssl.create_default_context()
    if not cfg["exchange"].get("verify_ssl", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    # timeout : un serveur muet ne doit pas geler la tâche planifiée à vie.
    if im.get("ssl", True):
        conn = imaplib.IMAP4_SSL(im["server"], int(im.get("port", 993)),
                                 ssl_context=ctx, timeout=60)
    else:
        conn = imaplib.IMAP4(im["server"], int(im.get("port", 143)),
                             timeout=60)
        conn.starttls(ssl_context=ctx)
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
    tz = load_timezone(cfg["analysis"]["timezone"])
    days = int(cfg["analysis"]["days_back"])
    # days_back <= 0 : aucune limite de date — tout le dossier est analysé.
    since = (datetime.now(tz) - timedelta(days=days)) if days > 0 else None
    since_imap = (f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}"
                  if since else "")
    conn = _connect(cfg, password)
    mails: list[RawMail] = []
    errors: list[str] = []
    try:
        for product, paths in cfg["folders"].items():
            for path in paths or []:
                try:
                    skipped = _fetch_folder(cfg, conn, product, path, since,
                                            since_imap, tz, mails)
                    if skipped:
                        errors.append(f"[{product}] {path} : {skipped} "
                                      "courriel(s) illisible(s) ignoré(s)")
                except Exception as exc:
                    errors.append(f"[{product}] {path} : {exc}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass  # la déconnexion qui échoue ne doit pas masquer la collecte
    return mails, errors


def _fetch_folder(cfg, conn, product, path, since, since_imap, tz,
                  mails: list[RawMail]) -> int:
    status, _ = conn.select(f'"{path}"', readonly=True)
    if status != "OK":
        raise RuntimeError(f"Dossier IMAP introuvable : {path}")
    critere = f"(SINCE {since_imap})" if since_imap else "ALL"
    status, data = conn.search(None, critere)
    if status != "OK":
        return 0
    skipped = 0
    for num in data[0].split():
        # Isolation PAR COURRIEL : un message malformé (charset inconnu,
        # en-têtes corrompus…) est ignoré et compté, sans faire classer tout
        # le dossier « illisible ».
        try:
            status, parts = conn.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not parts or parts[0] is None:
                skipped += 1
                continue
            msg = email.message_from_bytes(
                parts[0][1], policy=email.policy.default
            )
            try:
                received = email.utils.parsedate_to_datetime(msg["Date"])
                received = received.astimezone(tz)
            except Exception:
                # Date illisible : surtout ne PAS dater le courriel de
                # « maintenant » (un vieux message malformé paraîtrait tout
                # juste reçu et masquerait un backup manquant) — on l'ignore.
                skipped += 1
                continue
            if since is not None and received < since:
                continue
            sender_addr = email.utils.parseaddr(msg.get("From", ""))[1]
            att_conf = cfg.get("attachments") or {}
            parts = list(msg.iter_attachments())
            allowed, att_note = att_mod.gate(att_conf, sender_addr,
                                             bool(parts))
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
        except Exception:
            skipped += 1
    return skipped
