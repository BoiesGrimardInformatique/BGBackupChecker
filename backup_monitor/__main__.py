"""Point d'entrée en ligne de commande.

  python -m backup_monitor selftest       # autotest hors-ligne de l'installation
  python -m backup_monitor setup          # scanner les boîtes/dossiers et choisir
                                          # interactivement quoi surveiller
  python -m backup_monitor run            # une analyse + génération du tableau
  python -m backup_monitor run --watch 300  # boucle : analyse toutes les 300 s
  python -m backup_monitor diagnose       # bilan de calibrage (motifs, extraction)
  python -m backup_monitor suggest-jobs   # propose un bloc expected_jobs prêt
                                          # à coller, déduit des courriels vus
  python -m backup_monitor find MOT [MOT…]  # ressortir les courriels
                                          # contenant ces mots (sujet, corps,
                                          # pièces jointes), avec extrait
  python -m backup_monitor folders        # liste les dossiers de la boîte
  python -m backup_monitor test           # teste la connexion
  python -m backup_monitor set-password   # mot de passe (modes ews/imap seulement)

Options utiles : --days N (fenêtre d'analyse ponctuelle, sans toucher
config.yaml) ; --open (ouvrir le tableau après l'analyse) ; --no-cache (tout
relire, cache reconstruit) ; --fail-on-error avec --fail-on-unknown et/ou
--fail-on-warning (codes de sortie pour RMM/Planificateur).
"""

import argparse
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime

from . import (STATUS_ERROR, STATUS_MISSING, STATUS_UNKNOWN, STATUS_WARNING,
               load_timezone)
from . import history
from . import notify
from .parsers import DEFAULT_PATTERNS, analyze, job_states, suggest_jobs
from .report import render, write

# Codes de sortie du mode run (exploitables par la tâche planifiée, un RMM,
# systemd) : 0 = tout va bien ; 1 = panne de l'outil ; 2 = backups en erreur
# ou manquants (--fail-on-error) ; 3 = pas encore configuré (EXIT_NOT_...) ;
# 4 = collecte partielle (dossiers/courriels illisibles, --fail-on-error).
EXIT_BUSINESS = 2
EXIT_PARTIAL = 4


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


def _autotest_guard(cfg, command: str) -> None:
    """Phase de rodage : l'autotest complet (celui de « selftest ») tourne à
    CHAQUE utilisation de l'outil et son résultat est journalisé dans
    autotest.log — une régression se voit donc dès la commande suivante,
    pas au prochain lancement manuel de selftest. À la version finale,
    désactiver avec « autotest: { on_each_run: false } » dans config.yaml."""
    from . import __version__, selftest
    t0 = time.monotonic()
    try:
        failed, total, details = selftest.run_quiet()
    except Exception as exc:  # l'autotest lui-même ne doit jamais bloquer
        failed, total, details = 1, 1, [f"autotest impossible : {exc}"]
    line = (f"{datetime.now():%Y-%m-%d %H:%M:%S} v{__version__} "
            f"commande={command} autotest {total - failed}/{total} "
            f"{'OK' if not failed else 'ÉCHEC'} "
            f"[{time.monotonic() - t0:.1f} s]")
    path = os.path.join(cfg["_dir"], "autotest.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            with open(path, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-200:]
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            for d in details:
                fh.write(f"    ÉCHEC : {d}\n")
    except OSError:
        pass
    if failed:
        print(f"AVERTISSEMENT : autotest {failed}/{total} vérification(s) "
              "en échec — détails dans autotest.log. L'analyse continue, "
              "mais les résultats sont suspects.", file=sys.stderr)
        _log(cfg, f"AUTOTEST EN ÉCHEC ({failed}/{total}) — voir autotest.log")


def _run_once(cfg, password, strict_unknown: bool = False,
              strict_warning: bool = False) -> tuple[int, int, str]:
    """Une analyse complète. Retourne (problèmes métier, dossiers illisibles,
    chemin du tableau) : le premier compte les erreurs/manquants (+ inconnus
    si strict_unknown, + avertissements si strict_warning), le deuxième les
    dossiers ou courriels que la collecte a dû ignorer."""
    t0 = time.monotonic()
    mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
    events = analyze(cfg, mails)
    states = job_states(cfg, events)
    hist = None
    if (cfg.get("history") or {}).get("enabled", True):
        tz = load_timezone(cfg["analysis"]["timezone"])
        hist = history.update(cfg, states, events, datetime.now(tz))
    out = write(cfg, render(cfg, events, states, fetch_errors, hist))
    sent, notif_warnings = notify.check_and_notify(cfg, events, states)
    errors = sum(1 for e in events if e.status == STATUS_ERROR)
    warns = sum(1 for e in events if e.status == STATUS_WARNING)
    unknown = sum(1 for e in events if e.status == STATUS_UNKNOWN)
    missing = sum(1 for s in states if s.status == STATUS_MISSING)
    summary = (f"{len(events)} courriels analysés — {errors} erreur(s), "
               f"{warns} avertissement(s), {unknown} inconnu(s), "
               f"{missing} tâche(s) manquante(s) "
               f"[{time.monotonic() - t0:.1f} s]")
    if sent:
        summary += f" | {sent} notification(s) envoyée(s)"
    print(summary)
    for err in fetch_errors:
        print(f"AVERTISSEMENT — dossier illisible : {err}", file=sys.stderr)
    for warn in notif_warnings:
        print(f"AVERTISSEMENT — {warn}", file=sys.stderr)
    print(f"Tableau : file://{out}")
    # Détail des dossiers en erreur AUSSI dans le journal : sous pythonw
    # (tâche planifiée), stderr n'est visible nulle part.
    _log(cfg, summary
         + "".join(f" | ILLISIBLE : {e}" for e in fetch_errors)
         + "".join(f" | NOTIF : {w}" for w in notif_warnings))
    business = (errors + missing + (unknown if strict_unknown else 0)
                + (warns if strict_warning else 0))
    return business, len(fetch_errors), out


def _context_excerpt(text: str, term: str, width: int = 110) -> str:
    """Extrait du texte autour de la première occurrence de `term`
    (insensible à la casse), espaces normalisés — pour situer le mot-clé
    sans imprimer tout le corps."""
    i = text.lower().find(term.lower())
    if i < 0:
        return ""
    start = max(0, i - width // 2)
    frag = text[start:start + width + len(term)]
    return re.sub(r"\s+", " ", frag).strip()


def _find(cfg, password, terms: list[str]) -> None:
    """Ressort les courriels de la fenêtre d'analyse contenant TOUS les
    mots-clés donnés (insensible à la casse) — dans le sujet, le corps, le
    texte des pièces jointes, le dossier, le client ou l'expéditeur — avec
    un extrait autour du premier mot. « find VSS "Comptable Plus" » cible un
    mot chez un client ; --days 60 élargit la fenêtre."""
    mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
    for err in fetch_errors:
        print(f"AVERTISSEMENT — dossier illisible : {err}", file=sys.stderr)
    lows = [t.lower() for t in terms]
    hits = []
    for m in sorted(mails, key=lambda m: m.received, reverse=True):
        hay = (f"{m.subject}\n{m.body}\n{m.attachments_text}\n"
               f"{m.folder}\n{m.client}\n{m.sender}").lower()
        if all(t in hay for t in lows):
            hits.append(m)
    quoted = ", ".join(f"« {t} »" for t in terms)
    if not hits:
        print(f"Aucun courriel ne contient {quoted} dans la fenêtre de "
              f"{cfg['analysis']['days_back']} jours "
              "(élargir au besoin avec --days N).")
        return
    print(f"{len(hits)} courriel(s) contenant {quoted} :\n")
    for m in hits[:100]:
        print(f"{m.received:%Y-%m-%d %Hh%M}  [{m.folder}]  {m.subject}")
        ctx = (_context_excerpt(m.body, terms[0])
               or _context_excerpt(m.attachments_text, terms[0]))
        if ctx:
            print(f"    … {ctx} …")
    if len(hits) > 100:
        print(f"\n… et {len(hits) - 100} autre(s) plus ancien(s) — "
              "préciser avec un 2e mot-clé.")


def _open_report(path: str) -> None:
    """Ouvre le tableau dans le navigateur par défaut (option --open) — un
    échec d'ouverture ne doit jamais faire échouer l'analyse elle-même."""
    try:
        import webbrowser
        webbrowser.open(path)
    except Exception as exc:
        print(f"AVERTISSEMENT — ouverture du tableau impossible : {exc}",
              file=sys.stderr)


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


def _suggest_jobs(cfg, password) -> None:
    """Imprime un bloc expected_jobs prêt à coller dans config.yaml, déduit
    des courriels observés — supprime la friction principale de la détection
    des backups manquants."""
    mails, fetch_errors = _fetcher(cfg).fetch(cfg, password)
    for err in fetch_errors:
        print(f"AVERTISSEMENT — dossier illisible : {err}", file=sys.stderr)
    sugs = suggest_jobs(analyze(cfg, mails))
    if not sugs:
        print("Rien à proposer : aucune machine ni tâche extraite des "
              "courriels de la fenêtre d'analyse.\n"
              "Lancer d'abord « diagnose » pour calibrer l'extraction "
              "(section parsers de config.yaml).")
        return
    print("# Suggestion générée par « suggest-jobs » à partir des "
          f"{len(sugs)} couples machine/tâche observés.")
    print("# À COLLER dans config.yaml puis AJUSTER : noms, fréquences "
          "(every_hours) et tolérances (grace_hours).")
    print("expected_jobs:")
    for s in sugs:
        def q(v: str) -> str:
            return '"' + str(v).replace('"', "'") + '"'
        print(f"  - name: {q(s['name'])}")
        print(f"    product: {s['product']}")
        print(f"    match: {q(s['match'])}")
        print(f"    every_hours: {s['every_hours']}"
              f"  # estimé sur {s['samples']} courriel(s)")
        print(f"    grace_hours: {s['grace_hours']}")
        if s.get("client"):
            print(f"    client: {q(s['client'])}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="backup_monitor",
                                     description=__doc__)
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "setup", "diagnose", "suggest-jobs",
                                 "find", "selftest", "set-password",
                                 "folders", "test"])
    parser.add_argument("terms", nargs="*", metavar="MOT",
                        help="mots-clés de la commande find (tous requis, "
                             "insensible à la casse)")
    parser.add_argument("--config", default=None,
                        help="chemin de config.yaml (défaut : à côté du paquet)")
    parser.add_argument("--watch", type=int, metavar="SECONDES", default=0,
                        help="boucle continue, une analyse toutes les N secondes")
    parser.add_argument("--days", type=int, metavar="N", default=0,
                        help="fenêtre d'analyse pour CETTE exécution (jours), "
                             "sans modifier config.yaml — pratique pour un "
                             "diagnose ou un run ponctuel plus profond")
    parser.add_argument("--open", action="store_true",
                        help="ouvrir le tableau dans le navigateur après "
                             "l'analyse (run)")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignorer le cache de collecte et tout relire "
                             "depuis Outlook (le cache est reconstruit)")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="code de sortie 1 si erreurs ou tâches manquantes")
    parser.add_argument("--fail-on-unknown", action="store_true",
                        help="avec --fail-on-error : les courriels non "
                             "reconnus comptent aussi comme un problème")
    parser.add_argument("--fail-on-warning", action="store_true",
                        help="avec --fail-on-error : les avertissements "
                             "comptent aussi comme un problème")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days doit être un nombre de jours ≥ 1")
    if args.command == "find" and not args.terms:
        parser.error("find : indiquer au moins un mot-clé — "
                     "ex. « python -m backup_monitor find VSS »")
    if args.command != "find" and args.terms:
        parser.error(f"arguments inattendus : {' '.join(args.terms)} "
                     "(les mots-clés ne servent qu'à la commande find)")

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
                      require_folders=(args.command in
                                       ("run", "diagnose", "suggest-jobs",
                                        "find")))
    if args.days:
        # Fenêtre ponctuelle pour cette exécution seulement — config.yaml
        # n'est pas modifié (s'applique à run, diagnose, suggest-jobs, test).
        cfg["analysis"]["days_back"] = args.days
    if args.no_cache:
        # Relecture complète forcée : le cache est ignoré au chargement mais
        # reconstruit en fin de collecte (voir mailcache.open_cache).
        (cfg.setdefault("cache", {}))["_refresh"] = True

    # Phase de rodage : autotest journalisé à chaque utilisation (voir
    # _autotest_guard). La commande selftest est déjà l'autotest lui-même.
    if (cfg.get("autotest") or {}).get("on_each_run", True):
        _autotest_guard(cfg, args.command)

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

    if args.command == "suggest-jobs":
        _suggest_jobs(cfg, password)
        return

    if args.command == "find":
        _find(cfg, password, args.terms)
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
        first = True
        while True:
            try:
                _, _, out = _run_once(cfg, password, args.fail_on_unknown,
                                      args.fail_on_warning)
                if first and args.open:
                    _open_report(out)
                first = False
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
            business, partial, out = _run_once(cfg, password,
                                               args.fail_on_unknown,
                                               args.fail_on_warning)
        except Exception:
            _log(cfg, "ERREUR : " + traceback.format_exc(limit=2)
                 .strip().replace("\n", " | "))
            raise
        if args.open:
            _open_report(out)
        if args.fail_on_error:
            if business:
                sys.exit(EXIT_BUSINESS)
            if partial:
                sys.exit(EXIT_PARTIAL)


if __name__ == "__main__":
    main()
