"""Rapport diagnostic à transmettre pour faire ajuster l'outil.

La commande « rapport » (double-clic sur rapport-diagnostic.bat) rassemble
dans UN fichier texte (rapport-diagnostic.txt) tout ce qu'il faut pour
calibrer les motifs et vérifier le comportement sans accès au poste :
environnement, résultat de l'autotest, configuration surveillée, comptes par
produit et par état, taux d'extraction, exemples par produit, cas d'échec
des systèmes aux motifs encore déduits (SQL Server Agent, Proxmox), liste
des courriels NON RECONNUS avec extrait, état des tâches attendues et queue
des journaux.

Le fichier contient des noms de dossiers/clients/machines et des extraits de
courriels de sauvegarde : l'en-tête invite à le RELIRE avant de l'envoyer.
Aucun envoi automatique — le fichier reste local.
"""

import os
import platform
import sys
from datetime import datetime

from . import __version__, STATUS_ERROR, STATUS_SUCCESS, STATUS_UNKNOWN
from . import STATUS_WARNING, load_timezone
from . import history as history_mod
from . import mailcache
from .parsers import DEFAULT_PATTERNS, analyze, job_states

RAPPORT_FILE = "rapport-diagnostic.txt"
# Systèmes dont les motifs d'échec sont déduits du modèle standard, jamais
# confirmés sur de vrais courriels — leurs cas d'erreur observés sont donc
# la section la plus précieuse du rapport.
PRODUITS_A_CONFIRMER = ("sqlagent", "pbs", "script", "cobian", "backupexec")


def _tail(path: str, lines: int) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh.readlines()[-lines:]]
    except OSError:
        return []


def _size_kb(path: str) -> int | None:
    try:
        return max(1, os.path.getsize(path) // 1024)
    except OSError:
        return None


def build(cfg: dict, events, states, fetch_errors, autotest_result,
          now: datetime) -> str:
    """Construit le texte du rapport (pur : aucune lecture réseau/Outlook)."""
    L: list[str] = []
    add = L.append
    add("=" * 72)
    add(f"RAPPORT DIAGNOSTIC backup-monitor v{__version__} — "
        f"{now:%Y-%m-%d %H:%M}")
    add("À joindre à une demande d'ajustement de l'outil.")
    add("AVERTISSEMENT : contient des noms de dossiers/clients/machines et")
    add("des extraits de courriels de sauvegarde. RELIRE avant d'envoyer.")
    add("=" * 72)

    add("")
    add("-- Environnement --")
    add(f"Outil v{__version__} · Python {sys.version.split()[0]} · "
        f"{platform.platform()}")
    add(f"Méthode : {cfg['exchange']['method']}"
        + (f" (boîte : {cfg['outlook']['store']})"
           if (cfg.get('outlook') or {}).get('store') else ""))
    add(f"Fuseau : {cfg['analysis']['timezone']} · fenêtre : "
        f"{cfg['analysis']['days_back']} jours")
    conf_n = cfg.get("notifications") or {}
    conf_a = cfg.get("attachments") or {}
    conf_c = cfg.get("cache") or {}
    cache_file = mailcache.cache_path(cfg)
    cache_kb = _size_kb(cache_file)
    notif = ("activées " + str(conf_n.get("methods"))
             if conf_n.get("enabled") else "désactivées")
    pj = "activées" if conf_a.get("enabled") else "désactivées"
    cache_txt = ("désactivé" if not conf_c.get("enabled", True)
                 else (f"présent ({cache_kb} Ko)" if cache_kb
                       else "pas encore créé"))
    add(f"Notifications : {notif} · pièces jointes : {pj} · "
        f"cache : {cache_txt}")
    hist = history_mod.load(os.path.join(cfg["_dir"],
                                         history_mod.HISTORY_FILE))
    jours = sorted({d for t in hist.get("taches", {}).values()
                    for d in (t.get("jours") or {})})
    add(f"Historique : {len(hist.get('taches', {}))} tâche(s) suivie(s)"
        + (f", du {jours[0]} au {jours[-1]}" if jours else " (vide)"))

    add("")
    add("-- Autotest --")
    failed, total, details = autotest_result
    add(f"{total - failed}/{total} vérifications "
        + ("OK" if not failed else "EN ÉCHEC"))
    for d in details[:10]:
        add(f"  ÉCHEC : {d}")

    add("")
    add("-- Configuration surveillée --")
    for prod, paths in (cfg.get("folders") or {}).items():
        if paths:
            add(f"folders.{prod} : {paths}")
    if cfg.get("client_folders"):
        add(f"client_folders : {cfg['client_folders']}")
    add(f"expected_jobs : {len(cfg.get('expected_jobs') or [])} tâche(s) — "
        + ", ".join(str(j.get("name")) for j in
                    (cfg.get("expected_jobs") or [])[:20]))
    add(f"clients (regex) : {len(cfg.get('clients') or [])}")
    parsers_perso = sorted((cfg.get("parsers") or {}).keys())
    add(f"parsers personnalisés : {parsers_perso or 'aucun'}")

    add("")
    add("-- Collecte --")
    add(f"{len(events)} courriel(s) dans la fenêtre.")
    for err in fetch_errors:
        add(f"ILLISIBLE : {err}")
    folders_seen: dict[str, int] = {}
    for e in events:
        folders_seen[e.folder] = folders_seen.get(e.folder, 0) + 1
    for f in sorted(folders_seen):
        add(f"  {folders_seen[f]:>5}  {f}")

    add("")
    add("-- Classement par produit --")
    seen = {e.product for e in events}
    ordered = [p for p in DEFAULT_PATTERNS if p in seen]
    ordered += sorted(seen - set(ordered))
    for product in ordered:
        evs = [e for e in events if e.product == product]
        line = f"{product} : {len(evs)} courriels — "
        line += ", ".join(
            f"{st} {n}" for st, n in
            ((s, sum(1 for e in evs if e.status == s))
             for s in (STATUS_ERROR, STATUS_WARNING, STATUS_SUCCESS,
                       STATUS_UNKNOWN)) if n)
        add(line)
        add(f"  extraction : machine {sum(1 for e in evs if e.machine)}"
            f"/{len(evs)}, tâche {sum(1 for e in evs if e.job)}/{len(evs)}, "
            f"client {sum(1 for e in evs if e.client)}/{len(evs)}")
        recent = evs[0]  # events déjà triés du plus récent au plus ancien
        add(f"  exemple : « {recent.subject[:90]} » → {recent.status}"
            + (f", machine={recent.machine}" if recent.machine else "")
            + (f", tâche={recent.job}" if recent.job else "")
            + (f", motif={recent.matched_pattern}"
               if recent.matched_pattern else ""))

    add("")
    add("-- Cas d'échec à confirmer (motifs déduits, jamais vus en vrai) --")
    any_case = False
    for product in PRODUITS_A_CONFIRMER:
        cases = [e for e in events if e.product == product
                 and e.status in (STATUS_ERROR, STATUS_WARNING)]
        for e in cases[:5]:
            any_case = True
            add(f"[{product}] {e.received:%Y-%m-%d} {e.status} — "
                f"« {e.subject[:90]} » motif={e.matched_pattern}")
            if e.excerpt:
                add(f"    extrait : {e.excerpt[:160]}")
    if not any_case:
        add("Aucun échec observé pour SQL Server Agent / Proxmox / scripts —")
        add("les motifs d'échec de ces systèmes restent à confirmer.")

    add("")
    unknown = [e for e in events if e.status == STATUS_UNKNOWN]
    add(f"-- Courriels NON RECONNUS ({len(unknown)}) — à couvrir --")
    for e in unknown[:30]:
        add(f"{e.received:%Y-%m-%d %H:%M} [{e.product}] "
            f"({e.folder}) {e.subject[:90]}")
        if e.excerpt:
            add(f"    extrait : {e.excerpt[:160]}")
    if len(unknown) > 30:
        add(f"… et {len(unknown) - 30} autres (voir diagnose).")

    add("")
    add("-- Tâches attendues (état courant) --")
    if not states:
        add("Aucune expected_job configurée (suggest-jobs peut en proposer).")
    for s in states:
        last = (f"{s.last_event.received:%Y-%m-%d %H:%M}"
                if s.last_event else "—")
        add(f"{s.status:<13} {s.name}  [dernier : {last}]"
            + (f"  {s.due_note}" if s.due_note else ""))

    add("")
    add("-- Journal backup-monitor.log (20 dernières lignes) --")
    L.extend(_tail(os.path.join(cfg["_dir"], "backup-monitor.log"), 20)
             or ["(vide ou absent)"])
    add("")
    add("-- Journal autotest.log (10 dernières lignes) --")
    L.extend(_tail(os.path.join(cfg["_dir"], "autotest.log"), 10)
             or ["(vide ou absent)"])
    add("")
    add("=== fin du rapport ===")
    return "\n".join(L) + "\n"


def generate(cfg: dict, password, fetcher, autotest_result=None) -> str:
    """Collecte, construit et écrit le rapport ; retourne son chemin.
    Une collecte impossible (Outlook absent…) n'empêche pas le rapport :
    l'erreur y est consignée à la place des courriels."""
    tz = load_timezone(cfg["analysis"]["timezone"])
    now = datetime.now(tz)
    if autotest_result is None:
        from . import selftest
        try:
            autotest_result = selftest.run_quiet()
        except Exception as exc:
            autotest_result = (1, 1, [f"autotest impossible : {exc}"])
    try:
        mails, fetch_errors = fetcher.fetch(cfg, password)
    except Exception as exc:
        mails, fetch_errors = [], [f"collecte impossible : {exc}"]
    events = analyze(cfg, mails)
    states = job_states(cfg, events)
    text = build(cfg, events, states, fetch_errors, autotest_result, now)
    path = os.path.join(cfg["_dir"], RAPPORT_FILE)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
