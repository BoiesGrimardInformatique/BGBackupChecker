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


def _pick_store(entries: list[dict]) -> str:
    stores = []
    for e in entries:
        if e["store"] not in stores:
            stores.append(e["store"])
    if len(stores) <= 1:
        return stores[0] if stores else ""
    print("\n== Boîtes (magasins) visibles dans Outlook ==")
    default_idx = 1
    for i, s in enumerate(stores, 1):
        tag = ""
        if any(e["store"] == s and e.get("default") for e in entries):
            tag = "  (boîte par défaut du profil)"
            default_idx = i
        print(f"  {i}. {s}{tag}")
    while True:
        raw = _ask(f"Boîte à surveiller [{default_idx}] : ", str(default_idx))
        try:
            n = _parse_nums(raw, len(stores))
        except ValueError as exc:
            print(f"  {exc}")
            continue
        if len(n) == 1:
            return stores[n[0] - 1]
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


def _save(cfg_path: str, store: str, selection: dict, multi_store: bool) -> None:
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("outlook", {})
    if multi_store and str(data.get("exchange", {}).get("method",
                                                        "outlook")).lower() == "outlook":
        data["outlook"]["store"] = store
    data["folders"] = {key: selection.get(key, []) for key, _ in PRODUCTS}
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
    print("Analyse des boîtes et dossiers disponibles (lecture seule)…")
    entries = fetcher.folder_tree(cfg, password)
    if not entries:
        sys.exit("Aucun dossier trouvé — Outlook est-il configuré sur ce poste ?")

    stores = {e["store"] for e in entries}
    store = _pick_store(entries)
    selection = _pick_folders(entries, store)

    print("\n== Récapitulatif ==")
    print(f"  Boîte : {store}")
    for key, label in PRODUCTS:
        for path in selection.get(key) or ["(aucun)"]:
            print(f"  {label:<11}: {path}")
    if _ask("\nEnregistrer dans config.yaml ? [O/n] : ", "o").lower() not in ("o", "oui", "y", "yes"):
        print("Abandon — configuration non modifiée.")
        return

    _save(cfg["_path"], store, selection, multi_store=len(stores) > 1)
    print(f"\nConfiguration enregistrée : {cfg['_path']}")
    print("Pensez à déclarer vos tâches attendues (expected_jobs) pour la "
          "détection des backups manquants, puis lancez :")
    print("  python -m backup_monitor run")
