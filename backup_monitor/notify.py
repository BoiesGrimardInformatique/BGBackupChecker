"""Notifications sortantes — UNIQUEMENT sur transition d'état.

Désactivé par défaut (opt-in). L'outil mémorise l'état de chaque tâche dans
dernier-etat.json (à côté de config.yaml) et ne notifie que quand une tâche
CHANGE d'état vers un état surveillé (erreur/manquant par défaut) — jamais à
chaque cycle de 5 minutes. Le retour au succès d'une tâche précédemment en
alerte est aussi notifié (désactivable).

Canaux :
- toast   : notification Windows native (PowerShell/WinRT, aucune dépendance) ;
- webhook : POST HTTP vers une URL (ntfy en texte brut, Teams/Slack en JSON).
  Attention : c'est le SEUL endroit où l'outil émet du trafic réseau sortant —
  opt-in explicite, URL choisie par l'administrateur (ex. ntfy auto-hébergé).

Les échecs d'envoi ne cassent jamais l'analyse : ils sont retournés comme
avertissements et journalisés par l'appelant.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from . import STATUS_ERROR, STATUS_MISSING, STATUS_SUCCESS

STATE_FILE = "dernier-etat.json"
_LABELS = {
    STATUS_ERROR: "ERREUR", STATUS_MISSING: "MANQUANT",
    STATUS_SUCCESS: "rétabli", "avertissement": "AVERTISSEMENT",
    "inconnu": "inconnu",
}


def _current_states(cfg, states, events) -> dict:
    """État courant par tâche — état UNIFIÉ (parsers.current_states) : les
    tâches attendues ET chaque couple machine/tâche observé non couvert par
    une expected_job. Une liste expected_jobs partielle ne rend plus les
    autres tâches muettes."""
    from .parsers import current_states
    return {t["key"]: t["etat"] for t in current_states(cfg, events, states)}


def transitions(prev: dict, cur: dict, watch: set,
                notify_recovery: bool = True) -> list[str]:
    """Messages à envoyer : passage VERS un état surveillé (ou première
    apparition dans un état surveillé), et retour au succès d'une tâche qui
    était surveillée. Un état inchangé ne renotifie jamais."""
    msgs = []
    for key, status in sorted(cur.items()):
        before = prev.get(key)
        if status == before:
            continue
        name = key.split(":", 1)[1] if key.startswith("tache:") else key
        if status in watch:
            avant = _LABELS.get(before, before) if before else "nouveau"
            msgs.append(f"{name} : {avant} → {_LABELS.get(status, status)}")
        elif (notify_recovery and status == STATUS_SUCCESS
              and before in watch):
            msgs.append(f"{name} : {_LABELS.get(before, before)} → rétabli ✓")
    return msgs


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, cur: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".etat-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _toast(title: str, body: str) -> None:
    """Notification Windows native via WinRT — pas de module tiers. Le texte
    passe en littéral PowerShell simple quote (' doublé = échappé)."""
    t = title.replace("'", "''")
    b = body.replace("'", "''")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::"
        "ToastText02);"
        "$t = $x.GetElementsByTagName('text');"
        f"$t.Item(0).AppendChild($x.CreateTextNode('{t}')) | Out-Null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode('{b}')) | Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier('BackupMonitor').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=20, capture_output=True, check=True,
    )


def _webhook(conf: dict, title: str, body: str) -> None:
    url = conf.get("webhook_url", "")
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("notifications.webhook_url absente ou invalide")
    if conf.get("webhook_format", "text") == "json":
        # Teams / Slack / Discord-compatibles : {"text": "..."}
        data = json.dumps({"text": f"{title}\n{body}"}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:
        # ntfy : corps en texte brut, titre dans l'en-tête.
        data = body.encode("utf-8")
        headers = {"Title": title.encode("ascii", "replace").decode("ascii"),
                   "Priority": "high"}
    req = urllib.request.Request(url, data=data, headers=headers)
    urllib.request.urlopen(req, timeout=15).read(0)


def check_and_notify(cfg: dict, events, states) -> tuple[int, list[str]]:
    """Compare avec l'état du dernier run, envoie les notifications dues et
    mémorise l'état courant. Retourne (nb envoyées, avertissements)."""
    conf = cfg.get("notifications") or {}
    path = os.path.join(cfg["_dir"], STATE_FILE)
    cur = _current_states(cfg, states, events)
    if not conf.get("enabled"):
        # Mémoriser quand même : le jour où on active, seules les VRAIES
        # transitions futures notifient (pas tout l'historique d'un coup).
        _save_state(path, cur)
        return 0, []
    watch = set(conf.get("notify_on") or [STATUS_ERROR, STATUS_MISSING])
    msgs = transitions(_load_state(path), cur, watch,
                       conf.get("notify_recovery", True))
    warnings: list[str] = []
    sent = 0
    if msgs:
        title = f"BackupMonitor — {len(msgs)} changement(s) d'état"
        body = "\n".join(msgs[:12])
        if len(msgs) > 12:
            body += f"\n… et {len(msgs) - 12} autre(s) — voir le tableau."
        for method in conf.get("methods") or []:
            try:
                if method == "toast":
                    if sys.platform != "win32":
                        raise RuntimeError("toast : Windows seulement")
                    _toast(title, body)
                elif method == "webhook":
                    _webhook(conf, title, body)
                else:
                    raise ValueError(f"méthode inconnue « {method} »")
                sent += 1
            except Exception as exc:
                warnings.append(f"notification {method} : {exc}")
    # L'état n'est mémorisé qu'APRÈS tentative d'envoi : si le poste était
    # éteint ou l'envoi impossible, la transition renotifiera au prochain
    # run réussi plutôt que d'être perdue.
    if not msgs or sent or not (conf.get("methods") or []):
        _save_state(path, cur)
    return sent, warnings
