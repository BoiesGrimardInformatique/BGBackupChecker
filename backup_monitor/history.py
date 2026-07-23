"""Historique des états de sauvegarde au fil des jours.

Le tableau de bord ne montre que la fenêtre d'analyse courante (days_back) :
sans mémoire, impossible de voir qu'une tâche échoue une nuit sur trois ou
qu'un client se dégrade depuis une semaine. Ce module mémorise, à chaque
exécution de « run », le PIRE état de chaque JOUR pour chaque tâche suivie
dans historique.json (à côté de config.yaml) ; le tableau affiche ensuite une
bande des derniers jours et le taux de réussite sur 30 jours.

Qui est suivi, et quel jour compte :
- avec expected_jobs : chaque courriel correspondant compte sur SON jour de
  réception (le premier passage remplit donc l'historique sur toute la
  fenêtre d'analyse), et l'état courant de la tâche — y compris « manquant »,
  qui n'a pas de courriel — compte sur le jour de l'exécution ;
- sans expected_jobs : chaque couple machine/tâche observé dans les courriels
  est suivi de la même façon (mêmes clés stables que les notifications).

Fichier local, écrit atomiquement, taillé automatiquement (history.keep_days).
Seule la commande « run » écrit l'historique — diagnose/test n'y touchent pas.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta

from . import SEVERITY, STATUS_SUCCESS

HISTORY_FILE = "historique.json"


def _worst(a: str | None, b: str) -> str:
    """Le plus grave des deux états (ordre SEVERITY) ; un état inconnu du
    référentiel n'écrase jamais un état connu."""
    if a not in SEVERITY:
        return b if b in SEVERITY else (a or b)
    if b not in SEVERITY:
        return a
    return a if SEVERITY.index(a) <= SEVERITY.index(b) else b


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "taches": {}}
    if not isinstance(data, dict) or not isinstance(data.get("taches"), dict):
        return {"version": 1, "taches": {}}
    return data


def _entry(taches: dict, key: str, nom: str, client: str, produit: str) -> dict:
    t = taches.setdefault(key, {"jours": {}})
    t["nom"] = nom
    if client:  # ne pas effacer un client connu par un passage sans client
        t["client"] = client
    t.setdefault("client", "")
    t["produit"] = produit
    t.setdefault("jours", {})
    return t


def update(cfg: dict, states, events, now: datetime) -> dict:
    """Fusionne l'état courant dans l'historique (pire état par jour),
    enregistre le fichier et retourne les données à jour pour le rendu."""
    from .parsers import job_matches
    conf = cfg.get("history") or {}
    path = os.path.join(cfg["_dir"], HISTORY_FILE)
    data = load(path)
    taches = data["taches"]
    today = now.strftime("%Y-%m-%d")
    if states:
        matches = job_matches(cfg, events)
        for s in states:
            t = _entry(taches, f"tache:{s.name}", s.name, s.client, s.product)
            for ev in matches.get(s.name, []):
                day = ev.received.strftime("%Y-%m-%d")
                t["jours"][day] = _worst(t["jours"].get(day), ev.status)
            # L'état courant (dont « manquant », sans courriel) compte sur le
            # jour de l'exécution : une tâche muette laisse une trace datée.
            t["jours"][today] = _worst(t["jours"].get(today), s.status)
    else:
        for ev in events:
            who = ev.client or ev.machine or ev.folder
            what = ev.job or ev.machine or "courriels"
            nom = f"{who} — {what}" if what != who else who
            t = _entry(taches, f"{ev.product}:{who}:{what}", nom,
                       ev.client, ev.product)
            day = ev.received.strftime("%Y-%m-%d")
            t["jours"][day] = _worst(t["jours"].get(day), ev.status)
    _prune(data, now, int(conf.get("keep_days", 90)))
    _save(path, data)
    return data


def _prune(data: dict, now: datetime, keep_days: int) -> None:
    floor = (now - timedelta(days=max(1, keep_days))).strftime("%Y-%m-%d")
    for key in list(data["taches"]):
        jours = data["taches"][key].get("jours") or {}
        for day in [d for d in jours if d < floor]:  # dates ISO : tri lexical
            del jours[day]
        if not jours:
            del data["taches"][key]


def _save(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".hist-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def success_rate(jours: dict, now: datetime, days: int = 30) -> tuple[int, int]:
    """(jours en succès, jours renseignés) sur les N derniers jours."""
    floor = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    seen = [s for d, s in jours.items() if d >= floor]
    return sum(1 for s in seen if s == STATUS_SUCCESS), len(seen)
