"""Lettura file di testo (TXT, DOCX) per la correzione."""

import os

import docx


def read_text_file(path: str) -> str:
    """Legge un file .txt con fallback encoding UTF-8 → Latin-1."""
    for encoding in ("utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Impossibile leggere il file con encoding UTF-8 o Latin-1: {path}")


def read_docx_file(path: str) -> str:
    """Legge un file .docx e restituisce il testo con separatori di paragrafo."""
    doc = docx.Document(path)
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n\n".join(paragraphs)


def read_file(path: str) -> str:
    """Legge un file in base all'estensione."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return read_docx_file(path)
    elif ext == ".txt":
        return read_text_file(path)
    else:
        raise ValueError(f"Formato non supportato: {ext}")
