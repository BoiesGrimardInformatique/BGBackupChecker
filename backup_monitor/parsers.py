"""Analyse du contenu des courriels : classement erreur / avertissement /
succès / inconnu, extraction machine + tâche, détection des backups manquants."""

import re
from datetime import datetime, timedelta

from . import (
    BackupEvent,
    load_timezone,
    JobState,
    RawMail,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    STATUS_WARNING,
)

# Motifs par défaut, remplaçables/complétables via config.yaml (section parsers).
DEFAULT_PATTERNS = {
    "macrium": {
        "failure": [
            r"(?i)backup aborted", r"(?i)clone aborted", r"(?i)\bfailed\b",
            r"(?i)échou", r"(?i)errors?\s*[:=]\s*[1-9]",
        ],
        "warning": [
            r"(?i)completed with warnings", r"(?i)warnings?\s*[:=]\s*[1-9]",
            r"(?i)avertissement",
        ],
        "success": [
            r"(?i)completed successfully", r"(?i)backup completed",
            r"(?i)réussi", r"(?i)succès",
        ],
        "extract": {
            "machine": [r"(?i)computer[\s:]+([A-Za-z0-9._-]+)",
                        r"(?i)ordinateur[\s:]+([A-Za-z0-9._-]+)"],
            "job": [r"(?i)backup definition[\s:'\"]+([^'\"\r\n]+)"],
        },
    },
    "retrospect": {
        "failure": [
            r"(?i)\bfailed\b", r"(?i)[ée]chec", r"(?i)\berror -?\d+",
            r"(?i)\berreur -?\d+", r"(?im)^!",
        ],
        "warning": [r"(?i)with warnings", r"(?i)avertissement"],
        "success": [
            r"(?i)completed successfully", r"(?i)terminé(e)? avec succès",
            r"(?i)normal execution", r"(?i)exécution normale",
        ],
        "extract": {
            "machine": [r"(?i)from ([A-Za-z0-9._-]+)", r"(?i)de ([A-Za-z0-9._-]+)"],
            "job": [r"[Ss]cript\s+[«\"']([^»\"']+)"],
        },
    },
}

_ORDER = [("failure", STATUS_ERROR), ("warning", STATUS_WARNING),
          ("success", STATUS_SUCCESS)]


def _patterns_for(cfg: dict, product: str) -> dict:
    user = (cfg.get("parsers") or {}).get(product) or {}
    base = DEFAULT_PATTERNS.get(product, {})
    merged = {}
    for key in ("failure", "warning", "success"):
        merged[key] = user.get(key) if user.get(key) else base.get(key, [])
    extract = dict(base.get("extract", {}))
    extract.update(user.get("extract") or {})
    merged["extract"] = extract
    return merged


def _first_match(patterns: list[str], text: str) -> str | None:
    for pat in patterns or []:
        try:
            if re.search(pat, text):
                return pat
        except re.error:
            continue
    return None


def _extract(patterns: list[str], *texts: str) -> str:
    for text in texts:
        for pat in patterns or []:
            try:
                m = re.search(pat, text)
            except re.error:
                continue
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
    return ""


def _known_products(cfg: dict) -> list[str]:
    names = list(DEFAULT_PATTERNS.keys())
    for p in (cfg.get("parsers") or {}):
        if p not in names:
            names.append(p)
    return names


def _detect_product(cfg: dict, text: str) -> str:
    """Devine le produit d'un courriel issu d'un dossier « auto » (produits
    mixtes). D'abord le nom du produit dans le texte ; sinon le jeu de motifs
    qui parvient à classer le courriel ; sinon « macrium » par défaut."""
    low = text.lower()
    if "retrospect" in low:
        return "retrospect"
    if "macrium" in low or "reflect" in low:
        return "macrium"
    for product in _known_products(cfg):
        pats = _patterns_for(cfg, product)
        for key, _ in _ORDER:
            if _first_match(pats.get(key), text):
                return product
    return "macrium"


def classify(cfg: dict, mail: RawMail) -> BackupEvent:
    text = f"{mail.subject}\n{mail.body}\n{mail.attachments_text}"
    # Dossiers « auto » (mode client_folders) : le produit n'est pas connu du
    # dossier, on le détecte au contenu. Sinon on garde celui du dossier.
    product = mail.product
    if product not in _known_products(cfg):
        product = _detect_product(cfg, text)
    pats = _patterns_for(cfg, product)
    status, matched = STATUS_UNKNOWN, ""
    for key, st in _ORDER:
        hit = _first_match(pats.get(key), text)
        if hit:
            status, matched = st, hit
            break
    return BackupEvent(
        product=product,
        status=status,
        subject=mail.subject,
        sender=mail.sender,
        received=mail.received,
        folder=mail.folder,
        machine=_extract(pats["extract"].get("machine"), mail.subject,
                         mail.body, mail.attachments_text),
        job=_extract(pats["extract"].get("job"), mail.subject, mail.body,
                     mail.attachments_text),
        matched_pattern=matched,
        excerpt=re.sub(r"\s+", " ", mail.body).strip()[:500],
        attachments_note=mail.attachments_note,
        client=mail.client,
    )


def _client_matchers(cfg: dict) -> list[tuple[str, list]]:
    matchers = []
    for c in cfg.get("clients") or []:
        pats = []
        for p in c.get("machines") or []:
            try:
                pats.append(re.compile(str(p), re.IGNORECASE))
            except re.error:
                continue
        if c.get("name") and pats:
            matchers.append((c["name"], pats))
    return matchers


def _assign_client(matchers, ev: BackupEvent) -> str:
    hay = f"{ev.machine} {ev.subject} {ev.job}"
    for name, pats in matchers:
        if any(p.search(hay) for p in pats):
            return name
    return ""


def analyze(cfg: dict, mails: list[RawMail]) -> list[BackupEvent]:
    events = [classify(cfg, m) for m in mails]
    matchers = _client_matchers(cfg)
    for ev in events:
        # Client déjà fixé par le dossier (mode auto) : on ne l'écrase pas ;
        # sinon on tente l'association par motifs (section « clients »).
        if not ev.client:
            ev.client = _assign_client(matchers, ev)
    events.sort(key=lambda e: e.received, reverse=True)
    return events


def job_states(cfg: dict, events: list[BackupEvent]) -> list[JobState]:
    """Croise les événements avec les tâches attendues (expected_jobs) pour
    produire l'état courant de chaque tâche, y compris « manquant »."""
    tz = load_timezone(cfg["analysis"]["timezone"])
    now = datetime.now(tz)
    states: list[JobState] = []
    for job in cfg.get("expected_jobs") or []:
        try:
            rx = re.compile(job.get("match", ""), re.IGNORECASE)
        except re.error:
            rx = None
        last = None
        for ev in events:  # déjà triés du plus récent au plus ancien
            if ev.product != job.get("product"):
                continue
            hay = f"{ev.subject} {ev.machine} {ev.job}"
            if rx is None or rx.search(hay):
                last = ev
                break
        deadline = timedelta(hours=float(job.get("every_hours", 24))
                             + float(job.get("grace_hours", 0)))
        client = job.get("client") or (last.client if last else "")
        if last is None:
            states.append(JobState(
                name=job["name"], product=job.get("product", "?"),
                status=STATUS_MISSING, client=client,
                due_note="aucun courriel dans la fenêtre d'analyse"))
        elif now - last.received > deadline:
            hours = (now - last.received).total_seconds() / 3600
            states.append(JobState(
                name=job["name"], product=job.get("product", "?"),
                status=STATUS_MISSING, last_event=last, client=client,
                due_note=f"dernier courriel il y a {hours:.0f} h "
                         f"(attendu toutes les {job.get('every_hours', 24)} h)"))
        else:
            states.append(JobState(
                name=job["name"], product=job.get("product", "?"),
                status=last.status, last_event=last, client=client))
    return states
