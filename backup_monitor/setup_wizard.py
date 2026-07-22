"""Assistant interactif de première configuration : scanne les boîtes et
dossiers disponibles (lecture seule), fait choisir ceux à surveiller pour
Macrium et Retrospect, puis enregistre la sélection dans config.yaml."""

import os
import re
import sys
from datetime import date

import yaml

PRODUCTS = [("macrium", "Macrium"), ("retrospect", "Retrospect")]


def _ask(prompt: str, default: str = "") -> str:
    try:
        val = input(prompt).strip()
    except EOFError:
        sys.exit("\nEntrée interrompue — configuration non modifiée.")
    return val or default


def _parse_nums(raw: str, maxi: int) -> list[int]:
    nums = []
    for tok in re.split(r"[,\s]+", raw.strip()):
        if not tok:
            continue
        if not tok.isdigit() or not (1 <= int(tok) <= maxi):
            raise ValueError(f"« {tok} » n'est pas un numéro valide (1–{maxi}).")
        n = int(tok)
        if n not in nums:
            nums.append(n)
    return nums


def _pick_store(stores: list[dict]) -> str:
    """Choisit UNE boîte parmi une liste de {name, default}. Retour anticipé
    (sans question) s'il n'y en a qu'une."""
    names: list[str] = []
    for s in stores:
        if s["name"] not in names:
            names.append(s["name"])
    if len(names) <= 1:
        return names[0] if names else ""
    print("\n== Boîtes (magasins) visibles dans Outlook ==")
    default_idx = 1
    for i, name in enumerate(names, 1):
        tag = ""
        if any(s["name"] == name and s.get("default") for s in stores):
            tag = "  (boîte par défaut du profil)"
            default_idx = i
        print(f"  {i}. {name}{tag}")
    while True:
        raw = _ask(f"Boîte à surveiller [{default_idx}] : ", str(default_idx))
        try:
            n = _parse_nums(raw, len(names))
        except ValueError as exc:
            print(f"  {exc}")
            continue
        if len(n) == 1:
            return names[n[0] - 1]
        print("  Choisir UNE seule boîte.")


def _pick_folders(entries: list[dict], store: str) -> dict:
    folders = [e for e in entries if e["store"] == store]
    if not folders:
        sys.exit(f"Aucun dossier trouvé dans la boîte « {store} ».")
    print(f"\n== Dossiers de « {store} » ==")
    for i, f in enumerate(folders, 1):
        count = f.get("count")
        count_s = f"  ({count} éléments)" if count is not None else ""
        hints = [label for key, label in PRODUCTS if key in f["path"].lower()]
        hint_s = f"   ← suggestion {', '.join(hints)}" if hints else ""
        print(f"  {i:3}. {f['path']}{count_s}{hint_s}")

    selection: dict[str, list[str]] = {}
    for key, label in PRODUCTS:
        suggested = [i + 1 for i, f in enumerate(folders)
                     if key in f["path"].lower()]
        default = ",".join(str(n) for n in suggested)
        shown = f" [{default}]" if default else " [aucun]"
        while True:
            raw = _ask(
                f"\nDossier(s) pour {label} — numéros séparés par des "
                f"virgules, vide = suggestion, 0 = aucun{shown} : ", default)
            if raw.strip() == "0" or (not raw.strip() and not default):
                selection[key] = []
                break
            try:
                nums = _parse_nums(raw, len(folders))
            except ValueError as exc:
                print(f"  {exc}")
                continue
            if nums:
                selection[key] = [folders[n - 1]["path"] for n in nums]
                break
            print("  Entrer au moins un numéro, ou 0 pour aucun.")
    if not any(selection.values()):
        sys.exit("Aucun dossier choisi — configuration non modifiée.")
    return selection


def _pick_client_parents(entries: list[dict], store: str) -> list[str]:
    """Mode auto : choisit un ou plusieurs dossiers PARENTS dont chaque
    sous-dossier est un client (le nom du sous-dossier)."""
    folders = [e for e in entries if e["store"] == store]
    if not folders:
        sys.exit(f"Aucun dossier trouvé dans la boîte « {store} ».")
    print(f"\n== Dossiers de « {store} » ==")
    for i, f in enumerate(folders, 1):
        print(f"  {i:3}. {f['path']}")
    while True:
        raw = _ask(
            "\nDossier(s) PARENT dont chaque sous-dossier est un client — "
            "numéros séparés par des virgules : ", "")
        try:
            nums = _parse_nums(raw, len(folders))
        except ValueError as exc:
            print(f"  {exc}")
            continue
        if nums:
            return [folders[n - 1]["path"] for n in nums]
        print("  Entrer au moins un numéro.")


def _save(cfg_path: str, store: str, selection: dict, multi_store: bool,
          client_folders: list[str] | None = None) -> None:
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("outlook", {})
    if multi_store and str(data.get("exchange", {}).get("method",
                                                        "outlook")).lower() == "outlook":
        data["outlook"]["store"] = store
    if client_folders is not None:
        # Mode auto : dossiers parents (clients = sous-dossiers). On vide les
        # dossiers par produit pour éviter tout double comptage.
        data["client_folders"] = client_folders
        data["folders"] = {key: [] for key, _ in PRODUCTS}
    else:
        data["folders"] = {key: selection.get(key, []) for key, _ in PRODUCTS}
        data["client_folders"] = []
    header = (
        f"# Mis à jour par « python -m backup_monitor setup » le {date.today()}.\n"
        "# Documentation complète des options : config.example.yaml\n"
    )
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + body)
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, cfg_path)


def run(cfg: dict, password, fetcher) -> None:
    # Étape 1 — choisir la boîte AVANT de balayer ses dossiers. Sur Outlook,
    # énumérer toutes les boîtes du profil (partagées, archives en ligne…) et
    # compter leurs éléments fige l'application : on ne scanne donc que la
    # boîte retenue. Les autres méthodes (imap/ews) n'ont qu'un seul compte.
    list_stores = getattr(fetcher, "list_stores", None)
    if list_stores is not None:
        stores = list_stores(cfg, password)
        if not stores:
            sys.exit("Aucune boîte trouvée — Outlook est-il configuré sur ce poste ?")
        store = _pick_store(stores)
        print(f"\nAnalyse des dossiers de « {store} » (lecture seule)…")
        entries = fetcher.folder_tree(cfg, password, store=store)
    else:
        print("Analyse des dossiers disponibles (lecture seule)…")
        entries = fetcher.folder_tree(cfg, password)
        names: list[str] = []
        for e in entries:
            if e["store"] not in names:
                names.append(e["store"])
        stores = [{"name": n, "default": True} for n in names]
        store = _pick_store(stores)

    if not entries:
        sys.exit(f"Aucun dossier trouvé dans « {store or 'la boîte'} » — "
                 "vérifier que la boîte est bien configurée dans Outlook.")

    print("\n== Organisation des dossiers de sauvegarde ==")
    print("  1. Dossiers séparés par produit (un pour Macrium, un pour Retrospect)")
    print("  2. Un dossier PARENT dont chaque sous-dossier est un client")
    print("     (Macrium/Retrospect mélangés ; produit détecté automatiquement)")
    auto = _ask("Votre choix [1] : ", "1").strip() == "2"

    parents: list[str] = []
    selection: dict = {}
    if auto:
        parents = _pick_client_parents(entries, store)
    else:
        selection = _pick_folders(entries, store)

    print("\n== Récapitulatif ==")
    print(f"  Boîte : {store}")
    if auto:
        print("  Mode : un client par sous-dossier (produit détecté au contenu)")
        for path in parents:
            print(f"  Dossier parent : {path}")
    else:
        for key, label in PRODUCTS:
            for path in selection.get(key) or ["(aucun)"]:
                print(f"  {label:<11}: {path}")
    if _ask("\nEnregistrer dans config.yaml ? [O/n] : ", "o").lower() not in ("o", "oui", "y", "yes"):
        print("Abandon — configuration non modifiée.")
        return

    _save(cfg["_path"], store, selection, multi_store=len(stores) > 1,
          client_folders=parents if auto else None)
    print(f"\nConfiguration enregistrée : {cfg['_path']}")
    print("Pensez à déclarer vos tâches attendues (expected_jobs) pour la "
          "détection des backups manquants, puis lancez :")
    print("  python -m backup_monitor run")
