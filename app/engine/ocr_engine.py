"""Motore OCR con supporto per immagini e PDF multipagina."""

import os
import threading
from typing import Callable

import pymupdf as fitz  # PyMuPDF
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


class OCREngine:
    """Motore OCR che supporta immagini (JPEG/PNG) e PDF."""

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
        text = pytesseract.image_to_string(img, lang=lang)
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

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")

            # Renderizza a 300 DPI per qualità OCR migliore
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang=lang)
            pages_text.append(text.strip())

            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        return "\f".join(pages_text)
