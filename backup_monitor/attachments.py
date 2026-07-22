"""Lecture SÉCURISÉE des pièces jointes — texte, HTML et PDF.

Modèle de menace : un expéditeur malveillant dépose un courriel piégé dans un
dossier surveillé. Défenses, dans l'ordre :
  1. liste blanche d'expéditeurs (attachments.allowed_senders) ;
  2. liste blanche d'extensions (texte/HTML/PDF ; jamais d'exécutable,
     d'archive ni d'Office) ;
  3. plafond de taille (attachments.max_kb) ;
  4. inspection du contenu : un binaire renommé .txt/.html est rejeté
     (signatures MZ, PK, octets nuls…) ; un .pdf doit réellement commencer
     par %PDF.
Traitement par type, jamais d'exécution ni de rendu :
  - texte : décodé et parcouru par regex, rien d'autre ;
  - HTML : jamais rendu — scripts et balises supprimés, seul le texte reste ;
  - PDF : jamais ouvert par un lecteur — texte extrait par pypdf (Python pur,
    sans moteur de rendu ni JavaScript) dans un SOUS-PROCESSUS à délai
    maximal : un PDF conçu pour faire boucler l'analyseur est abandonné.
Rien n'est écrit sur disque ; tout ce qui est affiché passe par html.escape.
"""

import html as html_mod
import os
import re
import subprocess
import sys

DEFAULT_EXTENSIONS = [".txt", ".log", ".htm", ".html", ".pdf"]
PDF_TIMEOUT_S = 20
PDF_MAX_CHARS = 300_000

# Signatures de formats binaires/exécutables/conteneurs — rejet immédiat.
_MAGIC = [
    b"MZ", b"ZM",                # exécutables Windows
    b"\x7fELF",                  # exécutables Linux
    b"PK\x03\x04", b"PK\x05\x06",  # zip (docx/xlsx inclus)
    b"%PDF",                     # PDF
    b"\xd0\xcf\x11\xe0",         # OLE (doc/xls anciens, msi)
    b"\x1f\x8b", b"BZh", b"\xfd7zXZ", b"7z\xbc\xaf",  # gzip/bzip2/xz/7z
    b"Rar!",                     # rar
    b"\x89PNG", b"GIF8", b"\xff\xd8", b"BM",  # images
    b"\xca\xfe\xba\xbe",         # Java/Mach-O
]


def sender_allowed(sender: str, allowlist: list[str] | None) -> bool:
    """Adresse exacte, ou domaine si l'entrée commence par « @ »."""
    s = (sender or "").strip().lower()
    if not s:
        return False
    for entry in allowlist or []:
        e = str(entry).strip().lower()
        if not e:
            continue
        if e.startswith("@"):
            if s.endswith(e):
                return True
        elif s == e:
            return True
    return False


def looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    head = data[:8]
    for magic in _MAGIC:
        if head.startswith(magic):
            return False
    if b"\x00" in data:
        return False
    return True


def decode_text(data: bytes) -> str:
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def html_to_text(text: str) -> str:
    """Réduit du HTML à son texte — jamais rendu, jamais exécuté."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text,
                  flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</tr>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_mod.unescape(text)


def _pdf_text(data: bytes) -> tuple[str | None, str]:
    """Extraction du texte d'un PDF dans un sous-processus à délai maximal."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pdf_extract.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, script], input=data, capture_output=True,
            timeout=PDF_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return None, "extraction PDF interrompue (délai dépassé)"
    except Exception:
        return None, "extraction PDF impossible"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        if "pypdf" in err and "ModuleNotFound" in err:
            return None, "pypdf non installé (pip install pypdf)"
        return None, "PDF illisible"
    text = proc.stdout.decode("utf-8", errors="replace")[:PDF_MAX_CHARS]
    return text, ""


def extract(files: list[tuple], conf: dict) -> tuple[str, str]:
    """files : liste de (nom, taille_octets, callable → bytes).
    Retourne (texte_analysable, note_pour_le_tableau)."""
    allowed = tuple(e.lower() for e in
                    (conf.get("allowed_extensions") or DEFAULT_EXTENSIONS))
    max_bytes = int(conf.get("max_kb", 2048)) * 1024
    texts: list[str] = []
    notes: list[str] = []
    for name, size, get_bytes in files:
        label = name or "(sans nom)"
        if not (name or "").lower().endswith(allowed):
            notes.append(f"{label} : type non autorisé — ignorée")
            continue
        if size and size > max_bytes:
            notes.append(f"{label} : trop volumineuse ({size // 1024} Ko) — ignorée")
            continue
        try:
            data = get_bytes()
        except Exception:
            notes.append(f"{label} : lecture impossible — ignorée")
            continue
        if data is None or len(data) > max_bytes:
            notes.append(f"{label} : trop volumineuse — ignorée")
            continue
        data = bytes(data)
        kb = max(1, len(data) // 1024)
        lower = (name or "").lower()
        if lower.endswith(".pdf"):
            if not data.startswith(b"%PDF"):
                notes.append(f"{label} : contenu non conforme à un PDF — ignorée")
                continue
            text, err = _pdf_text(data)
            if text is None:
                notes.append(f"{label} : {err} — ignorée")
                continue
            texts.append(text)
            notes.append(f"{label} : analysée (PDF, {kb} Ko)")
            continue
        if not looks_like_text(data):
            notes.append(f"{label} : contenu binaire — ignorée")
            continue
        text = decode_text(data)
        if lower.endswith((".htm", ".html")):
            text = html_to_text(text)
            notes.append(f"{label} : analysée (HTML converti en texte, {kb} Ko)")
        else:
            notes.append(f"{label} : analysée ({kb} Ko)")
        texts.append(text)
    return "\n".join(texts), " · ".join(notes)


def gate(conf: dict, sender: str, has_attachments: bool) -> tuple[bool, str]:
    """Premier verrou : fonctionnalité activée + expéditeur de confiance.
    Retourne (autorisé, note)."""
    if not conf or not conf.get("enabled"):
        return False, ""
    if not has_attachments:
        return False, ""
    if not sender_allowed(sender, conf.get("allowed_senders")):
        return False, ("pièces jointes ignorées : expéditeur hors liste de "
                       "confiance (attachments.allowed_senders)")
    return True, ""
