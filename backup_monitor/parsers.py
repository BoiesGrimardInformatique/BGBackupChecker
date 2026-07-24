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
            r"(?i)completed with errors", r"(?i)cancell?ed", r"(?i)annulé",
            # Modèle de notification intégré de Macrium Reflect (« <machine>
            # Macrium Reflect - Backup Failure » / « Failure Notification »,
            # corps « Failed :( »). Motifs ancrés : un « Failure count: 0 »
            # dans un rapport de succès ne doit pas passer pour une erreur.
            r"(?i)backup\s+failure", r"(?i)clone\s+failure",
            r"(?i)failure\s+notification", r"(?i)failed\s*:\(",
        ],
        "warning": [
            r"(?i)completed with warnings", r"(?i)warnings?\s*[:=]\s*[1-9]",
            r"(?i)avertissement",
            r"(?i)backup\s+warning", r"(?i)warning\s+notification",
        ],
        "success": [
            r"(?i)completed successfully", r"(?i)backup completed",
            r"(?i)réussi", r"(?i)succès",
            # Idem, pendant succès : sujet « ... Backup Success » / « Success
            # Notification », corps « Success :) ».
            r"(?i)backup\s+success", r"(?i)success\s+notification",
            r"(?i)success\s*:\)",
        ],
        "extract": {
            # « (?:\s+name)? » : sur « Computer Name: SRV-X », capturer
            # SRV-X et non « Name ».
            "machine": [r"(?i)computer(?:\s+name)?\s*:?\s+([A-Za-z0-9._-]+)",
                        r"(?i)ordinateur\s*:?\s+([A-Za-z0-9._-]+)",
                        # Sujet type « SRV1(Backups) Macrium Reflect - ... » :
                        # le nom de machine précède « Macrium Reflect ».
                        r"^([A-Za-z0-9._+-]+)\s*(?:\([^)]*\))?\s*Macrium Reflect"],
            "job": [r"(?i)backup definition[\s:'\"]+([^'\"\r\n]+)"],
        },
    },
    "retrospect": {
        "failure": [
            r"(?i)\bfailed\b", r"(?i)[ée]chec", r"(?i)\berror -?\d+",
            r"(?i)\berreur -?\d+", r"(?im)^!",
            # Nomenclature « ProActive - Remote - <Compagnie> - N erreurs » :
            # le compte d'erreurs du sujet donne le statut. « \b » : pas de
            # frontière entre « 1 » et « 0 », donc « 10 erreurs » ne peut pas
            # passer pour « 0 erreurs » (et réciproquement).
            r"(?i)\b[1-9]\d*\s*(?:erreurs?|errors?)\b",
        ],
        "warning": [r"(?i)with warnings", r"(?i)avertissement"],
        "success": [
            r"(?i)completed successfully", r"(?i)terminé(e)? avec succès",
            r"(?i)normal execution", r"(?i)exécution normale",
            r"(?i)\b0\s*(?:erreurs?|errors?)\b",
        ],
        "extract": {
            # « \bfrom\s+ » ancré ; l'ancien « de (…) » capturait n'importe
            # quel mot après « de » dans un corps français (« de sauvegarde »,
            # « de fichiers »…) et polluait l'association client.
            "machine": [r"(?i)\bfrom\s+([A-Za-z0-9._-]+)",
                        r"(?i)ordinateur\s+([A-Za-z0-9._-]+)"],
            "job": [r"[Ss]cript\s+[«\"']([^»\"']+)"],
            # Nomenclature « ProActive - Remote - <Compagnie> - N erreurs » :
            # le CLIENT est le nom de compagnie du sujet. Groupe paresseux +
            # fin ancrée sur « - N erreurs » : un nom à trait d'union
            # (« Ste-Foy Dentaire ») reste entier. Préfixes RE:/TR: tolérés.
            "client": [r"(?i)^\s*(?:(?:re|tr|fwd?)\s*:\s*)*proactive\s*-\s*"
                       r"remote\s*-\s*(.+?)\s*-\s*\d+\s*"
                       r"(?:erreurs?|errors?)\s*$"],
        },
    },
    # Systèmes fréquemment mélangés aux courriels Macrium/Retrospect dans une
    # boîte partagée (mode client_folders) : reconnus pour un statut ET un
    # libellé de produit exacts, plutôt qu'un classement générique « macrium ».
    "sqlagent": {
        # Modèle standard des notifications d'opérateur SQL Server Agent :
        # « [The job succeeded.] SQL Server Job System: '<job>' completed/
        # failed on <serveur>. »
        "failure": [r"(?i)\[the job failed\]", r"(?i)\bjob failed\b"],
        "warning": [r"(?i)\[the job succeeded with warning"],
        # Gardes anti-négation : « was not succeeded » ne doit pas passer
        # pour un succès (voir la même précaution sur GENERIC_PATTERNS).
        "success": [r"(?i)\[the job succeeded\]",
                    r"(?i)(?<!not )(?<!non )\bjob succeeded\b"],
        "extract": {
            "machine": [r"(?i)completed on\s+\\{1,2}([A-Za-z0-9_-]+)",
                        r"(?i)failed on\s+\\{1,2}([A-Za-z0-9_-]+)"],
            "job": [r"SQL Server Job System:\s*'([^']+)'"],
        },
    },
    "pbs": {
        # Proxmox Backup Server / vzdump : sauvegardes VM (vzdump), purge
        # (Garbage Collect), rétention (Pruning) et réplication (Sync remote).
        "failure": [r"(?i)\bbackup failed\b", r"(?i)\btask error\b",
                    r"(?i)\bfailed\b"],
        "warning": [],
        # Garde anti-négation : « was not successful » ne doit pas passer
        # pour un succès (voir la même précaution sur GENERIC_PATTERNS).
        "success": [r"(?i)(?<!not )(?<!non )(?<!sans )(?<!without )\bsuccessful\b"],
        "extract": {
            "machine": [r"\(([A-Za-z0-9_.-]+)\)"],
            "job": [r"[Dd]atastore\s+'([^']+)'"],
        },
    },
    "script": {
        # Scripts maison à convention « [Success]/[Failed]/[Warning] » en
        # préfixe de sujet (ex. rapports générés par un outil interne).
        "failure": [r"(?i)\[failed\]", r"(?i)\[error\]"],
        "warning": [r"(?i)\[warning\]"],
        "success": [r"(?i)\[success\]", r"(?i)\[ok\]"],
        "extract": {"machine": [], "job": []},
    },
}

_ORDER = [("failure", STATUS_ERROR), ("warning", STATUS_WARNING),
          ("success", STATUS_SUCCESS)]

# Filet de sécurité générique : appliqué en dernier recours (après les motifs
# Macrium/Retrospect) pour les courriels d'AUTRES systèmes qui atterrissent
# dans les mêmes dossiers clients (mode client_folders) — jobs SQL Server
# Agent, Proxmox Backup Server (vzdump), scripts maison à convention
# « [Success]/[Failed] ». Les gardes « (?<!not )/(?<!non ) » évitent de
# classer « not successful »/« non réussi » comme un succès.
GENERIC_PATTERNS = {
    "failure": [
        r"(?i)\bjob failed\b", r"(?i)\[failed\]", r"(?i)\btask error\b",
    ],
    "warning": [
        r"(?i)\[warning\]",
    ],
    "success": [
        r"(?i)(?<!not )(?<!non )(?<!sans )(?<!without )\bsuccessful\b",
        r"(?i)(?<!not )(?<!non )\bsucceeded\b",
        r"(?i)\[success\]",
    ],
}


def _generic_compiled() -> dict:
    global _GENERIC_COMPILED
    if _GENERIC_COMPILED is None:
        _GENERIC_COMPILED = {k: _compile_all(v)
                             for k, v in GENERIC_PATTERNS.items()}
    return _GENERIC_COMPILED


_GENERIC_COMPILED = None


def _compile_all(patterns: list) -> list:
    """Compile une liste de motifs ; un motif invalide est simplement ignoré
    (il ne matcherait jamais — même comportement qu'avant, sans le coût d'une
    exception à chaque courriel)."""
    out = []
    for pat in patterns or []:
        try:
            out.append(re.compile(pat))
        except re.error:
            continue
    return out


def _patterns_for(cfg: dict, product: str) -> dict:
    """Motifs COMPILÉS d'un produit (défauts fusionnés avec config.yaml).
    Compilés une seule fois par exécution — sur ~1300 courriels × ~30 motifs,
    recompiler à chaque courriel dominait le temps d'analyse. Le cache est
    invalidé si la section parsers change d'objet (autre config)."""
    src = cfg.get("parsers")
    cache = cfg.get("_compiled_patterns")
    if cache is None or cache.get("_src") is not src:
        cache = {"_src": src}
        cfg["_compiled_patterns"] = cache
    if product in cache:
        return cache[product]
    user = (src or {}).get(product) or {}
    base = DEFAULT_PATTERNS.get(product, {})
    merged = {}
    for key in ("failure", "warning", "success"):
        merged[key] = _compile_all(user.get(key) if user.get(key)
                                   else base.get(key, []))
    extract = dict(base.get("extract", {}))
    extract.update(user.get("extract") or {})
    merged["extract"] = {k: _compile_all(v) for k, v in extract.items()}
    cache[product] = merged
    return merged


def _first_match(patterns: list, text: str) -> str | None:
    for rx in patterns or []:
        if rx.search(text):
            return rx.pattern
    return None


def _extract(patterns: list, *texts: str) -> str:
    for text in texts:
        for rx in patterns or []:
            m = rx.search(text)
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
    mixtes). D'abord une signature de produit connu dans le texte ; sinon le
    jeu de motifs qui parvient à classer le courriel ; sinon « macrium » par
    défaut."""
    low = text.lower()
    if "retrospect" in low:
        return "retrospect"
    # « ProActive » (sauvegarde proactive Retrospect) — nomenclature
    # « ProActive - Remote - <Compagnie> - N erreurs » sans le mot
    # « Retrospect » dans le texte.
    if re.search(r"\bproactive\b", low):
        return "retrospect"
    if "macrium" in low or "reflect" in low:
        return "macrium"
    if "sql server job system" in low:
        return "sqlagent"
    if ("vzdump" in low or "garbage collect datastore" in low
            or "pruning datastore" in low
            or ("sync remote" in low and "datastore" in low)):
        return "pbs"
    if re.search(r"\[(success|failed|warning|error|ok)\]", low):
        return "script"
    for product in _known_products(cfg):
        pats = _patterns_for(cfg, product)
        for key, _ in _ORDER:
            if _first_match(pats.get(key), text):
                return product
    return "macrium"


def _classify_text(pats: dict, text: str) -> tuple[str, str]:
    for key, st in _ORDER:
        hit = _first_match(pats.get(key), text)
        if hit:
            return st, hit
    return STATUS_UNKNOWN, ""


def classify(cfg: dict, mail: RawMail) -> BackupEvent:
    text_mail = f"{mail.subject}\n{mail.body}"
    # Dossiers « auto » (mode client_folders) : le produit n'est pas connu du
    # dossier, on le détecte au contenu. Sinon on garde celui du dossier.
    product = mail.product
    if product not in _known_products(cfg):
        product = _detect_product(cfg, f"{text_mail}\n{mail.attachments_text}")
    pats = _patterns_for(cfg, product)
    # Étage 1 — sujet + corps seulement : un « failed » historique dans un
    # journal joint (« failed to read sector, retry ok ») ne doit pas
    # renverser le verdict du courriel lui-même. Le filet générique couvre
    # les courriels d'autres systèmes (mode client_folders).
    status, matched = _classify_text(pats, text_mail)
    if status == STATUS_UNKNOWN:
        status, matched = _classify_text(_generic_compiled(), text_mail)
    # Étage 2 — pièces jointes, seulement si le courriel seul reste inconnu
    # (rapports dont tout le contenu utile est dans la pièce jointe).
    if status == STATUS_UNKNOWN and mail.attachments_text:
        status, matched = _classify_text(pats, mail.attachments_text)
        if status == STATUS_UNKNOWN:
            status, matched = _classify_text(_generic_compiled(),
                                             mail.attachments_text)
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
        # Borné à 5000 caractères AVANT la normalisation : l'extrait affiché
        # fait 500 caractères, inutile de normaliser un corps de 200 Ko.
        excerpt=re.sub(r"\s+", " ", mail.body[:5000]).strip()[:500],
        attachments_note=mail.attachments_note,
        # Priorité : dossier client (mode client_folders) > client extrait du
        # sujet/corps (ex. nomenclature ProActive de Retrospect) > section
        # « clients » de config.yaml (appliquée ensuite par analyze()).
        client=mail.client or _extract(pats["extract"].get("client"),
                                       mail.subject, mail.body),
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
        name = str(job.get("name") or job.get("match") or "(tâche sans nom)")
        try:
            rx = re.compile(job.get("match", ""), re.IGNORECASE)
        except re.error as exc:
            # Regex invalide : surtout ne PAS retomber sur « matche tout »
            # (n'importe quel courriel du produit passerait pour la preuve
            # que la tâche tourne = faux vert permanent). La tâche est
            # affichée en erreur tant que la config n'est pas corrigée.
            states.append(JobState(
                name=name, product=job.get("product", "?"),
                status=STATUS_ERROR, client=job.get("client", ""),
                due_note=f"expected_jobs.match invalide ({exc}) — "
                         "tâche NON surveillée, corriger config.yaml"))
            continue
        last = None
        for ev in events:  # déjà triés du plus récent au plus ancien
            if ev.product != job.get("product"):
                continue
            hay = f"{ev.subject} {ev.machine} {ev.job}"
            if rx.search(hay):
                last = ev
                break
        deadline = timedelta(hours=float(job.get("every_hours", 24))
                             + float(job.get("grace_hours", 0)))
        client = job.get("client") or (last.client if last else "")
        if last is None:
            states.append(JobState(
                name=name, product=job.get("product", "?"),
                status=STATUS_MISSING, client=client,
                due_note="aucun courriel dans la fenêtre d'analyse"))
        elif now - last.received > deadline:
            hours = (now - last.received).total_seconds() / 3600
            states.append(JobState(
                name=name, product=job.get("product", "?"),
                status=STATUS_MISSING, last_event=last, client=client,
                due_note=f"dernier courriel il y a {hours:.0f} h "
                         f"(attendu toutes les {job.get('every_hours', 24)} h)"))
        else:
            states.append(JobState(
                name=name, product=job.get("product", "?"),
                status=last.status, last_event=last, client=client))
    return states


def job_matches(cfg: dict, events: list[BackupEvent]) -> dict[str, list[BackupEvent]]:
    """Tous les événements correspondant à chaque tâche attendue — même
    critère que job_states (qui ne retient que le plus récent) ; sert à
    l'historique quotidien pour dater chaque résultat sur SON jour de
    réception. Les regex invalides sont ignorées ici : job_states les
    signale déjà comme erreur de configuration."""
    out: dict[str, list[BackupEvent]] = {}
    for job in cfg.get("expected_jobs") or []:
        name = str(job.get("name") or job.get("match") or "(tâche sans nom)")
        try:
            rx = re.compile(job.get("match", ""), re.IGNORECASE)
        except re.error:
            continue
        matched = [ev for ev in events
                   if ev.product == job.get("product")
                   and rx.search(f"{ev.subject} {ev.machine} {ev.job}")]
        if matched:
            out[name] = matched
    return out


def suggest_jobs(events: list[BackupEvent]) -> list[dict]:
    """Propose des expected_jobs à partir des courriels observés : regroupe
    par (produit, machine/client, tâche), estime la fréquence d'envoi
    (médiane des intervalles) et construit une regex de correspondance
    précise. Toutes les données viennent des BackupEvent déjà analysés —
    c'est la commande « suggest-jobs » qui met ça en YAML."""
    groups: dict = {}
    for ev in events:
        who = ev.machine or ev.client
        what = ev.job
        if not who and not what:
            continue  # rien d'assez stable pour construire une correspondance
        groups.setdefault((ev.product, who, what), []).append(ev)
    out: list[dict] = []
    for (product, who, what), evs in sorted(groups.items()):
        times = sorted(e.received for e in evs)
        if len(times) >= 3:
            deltas = sorted((b - a).total_seconds() / 3600
                            for a, b in zip(times, times[1:]))
            every = max(1, round(deltas[len(deltas) // 2]))
            # Coller aux fréquences usuelles quand on en est proche.
            for std in (1, 2, 4, 6, 8, 12, 24, 48, 72, 168):
                if abs(every - std) <= std * 0.25:
                    every = std
                    break
        else:
            every = 24  # trop peu de courriels : valeur à ajuster à la main
        if who and what:
            # Les deux critères doivent matcher (lookaheads) : deux tâches
            # d'une même machine ne se masquent pas mutuellement.
            match = f"(?=.*{re.escape(who)})(?=.*{re.escape(what)})"
        else:
            match = re.escape(who or what)
        sug = {
            "name": f"{who} — {what}" if who and what else (who or what),
            "product": product,
            "match": match,
            "every_hours": every,
            "grace_hours": max(2, round(every * 0.25)),
            "samples": len(evs),
        }
        client = next((e.client for e in reversed(evs) if e.client), "")
        if client:
            sug["client"] = client
        out.append(sug)
    return out
