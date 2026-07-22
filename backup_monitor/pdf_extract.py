"""Extraction du texte d'un PDF — exécuté comme SOUS-PROCESSUS isolé par
attachments.py (octets sur stdin, texte sur stdout, délai maximal imposé par
le parent). pypdf est du Python pur : pas de moteur de rendu, pas de
JavaScript, pas d'exécution de contenu — au pire un plantage ou une boucle,
que l'isolation en sous-processus rend inoffensifs pour l'outil."""

import sys
from io import BytesIO

MAX_PAGES = 100
MAX_CHARS = 300_000


def main() -> None:
    data = sys.stdin.buffer.read()
    from pypdf import PdfReader  # import ici : l'erreur remonte sur stderr
    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        if i >= MAX_PAGES or total > MAX_CHARS:
            break
        text = page.extract_text() or ""
        chunks.append(text)
        total += len(text)
    sys.stdout.write("\n".join(chunks)[:MAX_CHARS])


if __name__ == "__main__":
    main()
