"""Motore OCR con supporto per immagini e PDF multipagina."""

import html as _html
import os
import threading
from typing import Callable

try:
    import pymupdf as fitz  # PyMuPDF >= 1.23
except ImportError:
    import fitz  # PyMuPDF < 1.23
import pytesseract
from PIL import Image

from app.config import load_config
from app.engine.tesseract_setup import get_tesseract_cmd


def _configure_tesseract():
    """Configura il path di Tesseract da config."""
    config = load_config()
    cmd = get_tesseract_cmd(config.get("tesseract_path", ""))
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _data_to_text_and_blocks(data: dict, width: int, height: int):
    """Da `image_to_data` (DICT) ricostruisce (testo_piano, blocchi_riga).

    Le parole vengono raggruppate per (block, par, line): ogni riga diventa un
    blocco posizionale ``{"label": "Text", "html": <testo>, "bbox": [...]}`` con
    bbox normalizzata in [0,1] (unione delle parole della riga). Questi blocchi
    servono al PDF ricercabile per posizionare il testo OCR invisibile sopra la
    riga corrispondente dell'immagine — stessa corrispondenza topologica dei
    PDF-scansione taggati. Il testo dentra in ``html`` già HTML-escaped, così il
    round-trip di _block_plain_text (unescape) lo restituisce fedele anche con
    ``<``, ``>``, ``&``.

    Ritorna anche il testo piano (righe separate da ``\\n``, blocchi separati da
    riga vuota), equivalente a quello di image_to_string, in un unico passaggio
    OCR (niente doppia esecuzione).
    """
    n = len(data.get("text", []))
    lines: dict = {}
    order: list = []
    for i in range(n):
        word = data["text"][i]
        if not word or not word.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < 0:
            continue  # scarta i livelli non-parola (blocco/paragrafo/riga)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        left, top = data["left"][i], data["top"][i]
        right, bottom = left + data["width"][i], top + data["height"][i]
        rec = lines.get(key)
        if rec is None:
            lines[key] = {"words": [word], "block": data["block_num"][i],
                          "x1": left, "y1": top, "x2": right, "y2": bottom}
            order.append(key)
        else:
            rec["words"].append(word)
            rec["x1"] = min(rec["x1"], left)
            rec["y1"] = min(rec["y1"], top)
            rec["x2"] = max(rec["x2"], right)
            rec["y2"] = max(rec["y2"], bottom)

    blocks: list = []
    text_lines: list = []
    prev_block = None
    for key in order:
        rec = lines[key]
        line_text = " ".join(rec["words"]).strip()
        if not line_text:
            continue
        if prev_block is not None and rec["block"] != prev_block:
            text_lines.append("")  # riga vuota fra blocchi (≈ paragrafi)
        prev_block = rec["block"]
        text_lines.append(line_text)
        bbox = None
        if width and height:
            bbox = [rec["x1"] / width, rec["y1"] / height,
                    rec["x2"] / width, rec["y2"] / height]
        blocks.append({"label": "Text", "html": _html.escape(line_text),
                       "bbox": bbox})

    return "\n".join(text_lines), blocks


class OCREngine:
    """Motore OCR che supporta immagini (JPEG/PNG) e PDF."""

    def __init__(self):
        # Blocchi posizionali riga-per-riga (uno per pagina), consumati dal PDF
        # ricercabile per far combaciare testo invisibile e immagine.
        self._last_line_blocks: list = []

    def process(
        self,
        file_path: str,
        lang: str = "ita",
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        """Esegue l'OCR su un file e restituisce il testo estratto.

        Args:
            file_path: percorso del file da elaborare.
            lang: codice lingua Tesseract.
            on_progress: callback(pagina_corrente, totale_pagine).
            cancel_event: evento di cancellazione; se set, interrompe l'elaborazione.

        Returns:
            Testo OCR risultante.

        Raises:
            InterruptedError: se l'operazione viene interrotta.
        """
        _configure_tesseract()

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._process_pdf(file_path, lang, on_progress, cancel_event, on_partial)
        else:
            return self._process_image(file_path, lang, on_progress)

    def _process_image(
        self,
        file_path: str,
        lang: str,
        on_progress: Callable[[int, int], None] | None,
    ) -> str:
        """OCR su singola immagine."""
        img = Image.open(file_path)
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT)
        text, blocks = _data_to_text_and_blocks(data, img.width, img.height)
        self._last_line_blocks = [blocks]
        if on_progress:
            on_progress(1, 1)
        return text.strip()

    def _process_pdf(
        self,
        file_path: str,
        lang: str,
        on_progress: Callable[[int, int], None] | None,
        cancel_event: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        """OCR su PDF multipagina: converte ogni pagina in immagine e applica OCR."""
        doc = fitz.open(file_path)
        total = len(doc)
        pages_text = []
        pages_blocks = []

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")

            # Renderizza a 300 DPI per qualità OCR migliore
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            data = pytesseract.image_to_data(
                img, lang=lang, output_type=pytesseract.Output.DICT)
            text, blocks = _data_to_text_and_blocks(data, pix.width, pix.height)
            pages_text.append(text.strip())
            pages_blocks.append(blocks)

            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        self._last_line_blocks = pages_blocks
        return "\f".join(pages_text)
