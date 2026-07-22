"""Point d'entrée en ligne de commande.

  python -m backup_monitor selftest       # autotest hors-ligne de l'installation
  python -m backup_monitor setup          # scanner les boîtes/dossiers et choisir
                                          # interactivement quoi surveiller
  python -m backup_monitor run            # une analyse + génération du tableau
  python -m backup_monitor run --watch 300  # boucle : analyse toutes les 300 s
  python -m backup_monitor diagnose       # bilan de calibrage (motifs, extraction)
  python -m backup_monitor folders        # liste les dossiers de la boîte
  python -m backup_monitor test           # teste la connexion
  python -m backup_monitor set-password   # mot de passe (modes ews/imap seulement)
"""

import argparse
import os
import shutil
import sys
import time
import traceback
from datetime import datetime

from . import STATUS_ERROR, STATUS_MISSING, STATUS_UNKNOWN, STATUS_WARNING
from .parsers import DEFAULT_PATTERNS, analyze, job_states
from .report import render, write


def _fetcher(cfg):
    method = cfg["exchange"]["method"].lower()
    if method == "outlook":
        from . import fetch_outlook as mod
    elif method == "imap":
        from . import fetch_imap as mod
    else:
        from . import fetch_ews as mod
    return mod


def _log(cfg, message: str) -> None:
    """Journal d'exécution (backup-monitor.log) — essentiel pour la tâche
    planifiée qui tourne sans console. Tronqué au-delà de 1 Mo."""
    path = os.path.join(cfg["_dir"], "backup-monitor.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            with open(path, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-200:]
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except OSError:
        pass


def _run_once(cfg, password, strict_unknown: bool = False) -> int:
    mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
    events = analyze(cfg, mails)
    states = job_states(cfg, events)
    out = write(cfg, render(cfg, events, states, fetch_errors))
    errors = sum(1 for e in events if e.status == STATUS_ERROR)
    warns = sum(1 for e in events if e.status == STATUS_WARNING)
    unknown = sum(1 for e in events if e.status == STATUS_UNKNOWN)
    missing = sum(1 for s in states if s.status == STATUS_MISSING)
    summary = (f"{len(events)} courriels analysés — {errors} erreur(s), "
               f"{warns} avertissement(s), {unknown} inconnu(s), "
               f"{missing} tâche(s) manquante(s).")
    print(summary)
    for err in fetch_errors:
        print(f"AVERTISSEMENT — dossier illisible : {err}", file=sys.stderr)
    print(f"Tableau : file://{out}")
    # Détail des dossiers en erreur AUSSI dans le journal : sous pythonw
    # (tâche planifiée), stderr n'est visible nulle part.
    _log(cfg, summary + ("".join(f" | ILLISIBLE : {e}" for e in fetch_errors)
                         if fetch_errors else ""))
    problems = errors + missing + len(fetch_errors)
    if strict_unknown:
        problems += unknown
    return problems


def _diagnose(cfg, password) -> None:
    """Bilan de calibrage : aide à ajuster les regex avec les vrais courriels."""
    mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
    events = analyze(cfg, mails)
    for err in fetch_errors:
        print(f"DOSSIER ILLISIBLE : {err}")
    if not events:
        print("Aucun courriel dans la fenêtre d'analyse — vérifier les "
              "dossiers (commande folders) et analysis.days_back.")
        return
    print(f"{len(events)} courriels analysés.\n")
    # Produits connus d'abord (ordre stable), puis tout produit détecté mais
    # non listé dans parsers.DEFAULT_PATTERNS (ex. via config.yaml : parsers).
    seen = {e.product for e in events}
    ordered = [p for p in DEFAULT_PATTERNS if p in seen]
    ordered += sorted(seen - set(ordered))
    for product in ordered:
        evs = [e for e in events if e.product == product]
        if not evs:
            continue
        print(f"== {product} ({len(evs)} courriels) ==")
        for status in ("erreur", "avertissement", "succes", "inconnu"):
            n = sum(1 for e in evs if e.status == status)
            if n:
                print(f"  {status:<13}: {n}")
        machine_ok = sum(1 for e in evs if e.machine)
        job_ok = sum(1 for e in evs if e.job)
        client_ok = sum(1 for e in evs if e.client)
        print(f"  extraction    : machine {machine_ok}/{len(evs)}, "
              f"tâche {job_ok}/{len(evs)}, client {client_ok}/{len(evs)}")
    unknown = [e for e in events if e.status == "inconnu"]
    if unknown:
        print(f"\n== {len(unknown)} courriels NON RECONNUS "
              "(à couvrir dans parsers.*) ==")
        for e in unknown[:20]:
            print(f"  {e.received:%Y-%m-%d %H:%M} [{e.product}] {e.subject}")
            if e.excerpt:
                print(f"      extrait : {e.excerpt[:120]}")
        if len(unknown) > 20:
            print(f"  … et {len(unknown) - 20} autres.")
    else:
        print("\nAucun courriel non reconnu : les motifs couvrent tout. ✓")


def main() -> None:
    parser = argparse.ArgumentParser(prog="backup_monitor",
                                     description=__doc__)
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "setup", "diagnose", "selftest",
                                 "set-password", "folders", "test"])
    parser.add_argument("--config", default=None,
                        help="chemin de config.yaml (défaut : à côté du paquet)")
    parser.add_argument("--watch", type=int, metavar="SECONDES", default=0,
                        help="boucle continue, une analyse toutes les N secondes")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="code de sortie 1 si erreurs ou tâches manquantes")
    parser.add_argument("--fail-on-unknown", action="store_true",
                        help="avec --fail-on-error : les courriels non "
                             "reconnus comptent aussi comme un problème")
    args = parser.parse_args()

    if args.command == "selftest":
        from . import selftest
        sys.exit(selftest.run())

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = args.config or os.path.join(project_dir, "config.yaml")

    if args.command == "setup" and not os.path.exists(cfg_path):
        example = os.path.join(project_dir, "config.example.yaml")
        shutil.copy(example, cfg_path)
        if os.name == "posix":
            os.chmod(cfg_path, 0o600)
        print(f"config.yaml créé à partir de config.example.yaml : {cfg_path}")

    from .config import load as load_config  # paresseux : requiert PyYAML
    cfg = load_config(cfg_path,
                      require_folders=(args.command in ("run", "diagnose")))

    method = cfg["exchange"]["method"].lower()

    if args.command == "set-password":
        if method == "outlook":
            sys.exit("Méthode « outlook » : aucun mot de passe nécessaire, "
                     "l'outil lit le Outlook local du poste.")
        from . import credentials
        credentials.set_password(cfg)
        return

    password = None
    if method != "outlook":
        from . import credentials
        password = credentials.get_password(cfg)

    if args.command == "setup":
        from . import setup_wizard
        setup_wizard.run(cfg, password, _fetcher(cfg))
        return

    if args.command == "folders":
        for line in _fetcher(cfg).list_folders(cfg, password):
            print(line)
        return

    if args.command == "diagnose":
        _diagnose(cfg, password)
        return

    if args.command == "test":
        mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
        print(f"Connexion OK ({cfg['exchange']['method']}) — "
              f"{len(mails)} courriels dans la fenêtre d'analyse.")
        for err in fetch_errors:
            print(f"DOSSIER ILLISIBLE : {err}")
        for m in mails[:5]:
            print(f"  [{m.product}] {m.received:%Y-%m-%d %H:%M}  {m.subject}")
        return

    # run
    if args.watch > 0:
        print(f"Mode continu : analyse toutes les {args.watch} s (Ctrl+C pour arrêter).")
        while True:
            try:
                _run_once(cfg, password, args.fail_on_unknown)
            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                _log(cfg, "ERREUR : " + traceback.format_exc(limit=2)
                     .strip().replace("\n", " | "))
                print("Nouvelle tentative au prochain cycle.", file=sys.stderr)
            try:
                time.sleep(args.watch)
            except KeyboardInterrupt:
                print("\nArrêt.")
                return
    else:
        try:
            problems = _run_once(cfg, password, args.fail_on_unknown)
        except Exception:
            _log(cfg, "ERREUR : " + traceback.format_exc(limit=2)
                 .strip().replace("\n", " | "))
            raise
        if args.fail_on_error and problems:
            sys.exit(1)


if __name__ == "__main__":
    main()
