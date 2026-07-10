"""Lettura file di testo (TXT, DOCX, PDF, HTML) per la correzione."""

import os
import re
from html.parser import HTMLParser

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


def read_pdf_file(path: str) -> str:
    """Estrae il testo da un .pdf, con le pagine separate da form feed (\\f).

    Legge il layer di testo del PDF (es. i PDF ricercabili prodotti da OCRLab o
    qualsiasi PDF con testo selezionabile). Usa PyMuPDF, già dipendenza del
    progetto. Un PDF di sole immagini senza layer di testo restituisce stringa
    vuota: va prima passato per l'OCR, non per la correzione.
    """
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(path)
    try:
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()
    return "\f".join(pages)


class _HTMLTextExtractor(HTMLParser):
    """Estrae il testo piano da un HTML, inserendo a capo sui blocchi.

    Salta il contenuto di <script>/<style> e converte i tag di blocco (p, div,
    li, tr, heading…) e <br> in interruzioni di riga, così l'impaginazione
    logica del documento sopravvive nel testo passato al correttore.
    """

    _BLOCK_TAGS = frozenset({
        "p", "div", "li", "tr", "table", "ul", "ol", "hr", "section",
        "article", "header", "footer", "blockquote", "pre", "figure",
        "figcaption", "caption", "h1", "h2", "h3", "h4", "h5", "h6",
    })
    _SKIP_TAGS = frozenset({"script", "style", "head", "title"})

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip += 1
        elif tag == "br":
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Normalizza gli spazi orizzontali e comprime le righe vuote in eccesso.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def read_html_file(path: str) -> str:
    """Legge un .html/.htm e restituisce il testo piano, tag rimossi."""
    for encoding in ("utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                markup = f.read()
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Impossibile leggere il file con encoding UTF-8 o Latin-1: {path}")
    parser = _HTMLTextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.get_text()


def read_file(path: str) -> str:
    """Legge un file in base all'estensione."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return read_docx_file(path)
    elif ext == ".txt":
        return read_text_file(path)
    elif ext == ".pdf":
        return read_pdf_file(path)
    elif ext in (".html", ".htm"):
        return read_html_file(path)
    else:
        raise ValueError(f"Formato non supportato: {ext}")
