"""Motore OCR tramite Windows.Media.Ocr (WinRT nativo)."""

import asyncio
import io
import threading
from typing import Callable

import pymupdf as fitz  # PyMuPDF
from PIL import Image


# ── Loop asyncio persistente per WinRT ───────────────────────────────────────
# asyncio.run() crea e distrugge il loop ad ogni chiamata. Le API WinRT
# usano callback interni che presuppongono un loop stabile: ricreare il loop
# per ogni pagina lascia strutture WinRT in stato inconsistente → garbage.
# Un loop dedicato con COM inizializzato una volta sola risolve il problema.

_winrt_loop: asyncio.AbstractEventLoop | None = None
_winrt_loop_ready = threading.Event()
_winrt_start_lock = threading.Lock()


def _ensure_winrt_loop() -> None:
    """Avvia il loop WinRT dedicato se non è già in esecuzione."""
    global _winrt_loop

    if _winrt_loop_ready.is_set():
        return

    with _winrt_start_lock:
        if _winrt_loop_ready.is_set():
            return

        def _thread_proc():
            global _winrt_loop
            import ctypes
            # COM STA richiesto da Windows.Media.Ocr nel thread del loop
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _winrt_loop = loop
            _winrt_loop_ready.set()
            try:
                loop.run_forever()
            finally:
                ctypes.windll.ole32.CoUninitialize()

        t = threading.Thread(target=_thread_proc, daemon=True, name="WinRT-OCR-Loop")
        t.start()

    _winrt_loop_ready.wait(timeout=5)


def _run_winrt(coro):
    """Invia una coroutine WinRT al loop dedicato e attende il risultato (bloccante)."""
    _ensure_winrt_loop()
    if _winrt_loop is None or not _winrt_loop.is_running():
        raise RuntimeError("Il loop WinRT non è disponibile.")
    future = asyncio.run_coroutine_threadsafe(coro, _winrt_loop)
    return future.result(timeout=120)


# ─────────────────────────────────────────────────────────────────────────────


def _check_winrt():
    """Verifica che i pacchetti winrt necessari siano installati."""
    try:
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.globalization  # noqa: F401
        import winrt.windows.graphics.imaging  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Pacchetti winrt non trovati. Installa con:\n"
            "  pip install winrt-Windows.Media.Ocr winrt-Windows.Globalization "
            "winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams "
            "winrt-Windows.Foundation"
        ) from e


def get_available_languages() -> list[tuple[str, str]]:
    """Restituisce le lingue OCR installate nel sistema come lista di (tag, nome).

    Returns:
        Lista di (language_tag, display_name), es. [("it-IT", "Italiano (Italia)"), ...]

    Raises:
        ImportError: se i pacchetti winrt non sono installati.
    """
    _check_winrt()
    from winrt.windows.media.ocr import OcrEngine
    return [
        (lang.language_tag, lang.display_name)
        for lang in OcrEngine.available_recognizer_languages
    ]


async def _pil_to_software_bitmap(image: Image.Image):
    """Converte una PIL Image in SoftwareBitmap tramite file temporaneo PNG."""
    import os
    import tempfile
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import StorageFile, FileAccessMode

    buf = io.BytesIO()
    image.save(buf, format="PNG")

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    try:
        os.write(fd, buf.getvalue())
        os.close(fd)

        sf = await StorageFile.get_file_from_path_async(tmp_path)
        stream = await sf.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        return bitmap
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def _recognize_async(image: Image.Image, lang_tag: str) -> str:
    """Esegue il riconoscimento OCR su una PIL Image."""
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language

    language = Language(lang_tag)
    if not OcrEngine.is_language_supported(language):
        available = ", ".join(t for t, _ in get_available_languages())
        raise ValueError(
            f"Lingua '{lang_tag}' non disponibile per Windows OCR.\n"
            f"Lingue installate: {available or 'nessuna'}\n"
            "Installa il pacchetto lingua da: Impostazioni → Ora e lingua → Lingua."
        )

    engine = OcrEngine.try_create_from_language(language)
    if engine is None:
        raise RuntimeError(f"Impossibile creare il motore OCR per la lingua '{lang_tag}'.")

    bitmap = await _pil_to_software_bitmap(image)
    result = await engine.recognize_async(bitmap)

    lines = list(result.lines)
    if not lines:
        return ""

    # OcrLine non espone bounding_rect; la geometria è disponibile sulle OcrWord.
    # Calcoliamo la rect di ogni riga dalla prima e ultima parola.
    def _line_top_bottom(line):
        words = list(line.words)
        if not words:
            return None, None
        top = min(w.bounding_rect.y for w in words)
        bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        return top, bottom

    tops_bottoms = [_line_top_bottom(ln) for ln in lines]

    # Altezza media delle righe con geometria valida
    heights = [b - t for t, b in tops_bottoms if t is not None]
    if not heights:
        # Fallback: semplice join con a capo
        return "\n".join(ln.text for ln in lines)

    avg_height = sum(heights) / len(heights)
    threshold = avg_height * 1.5

    parts = [lines[0].text]
    for idx in range(1, len(lines)):
        prev_top, prev_bottom = tops_bottoms[idx - 1]
        curr_top, _ = tops_bottoms[idx]
        if prev_bottom is not None and curr_top is not None:
            gap = curr_top - prev_bottom
            separator = "\n\n" if gap > threshold else "\n"
        else:
            separator = "\n"
        parts.append(separator + lines[idx].text)

    return "".join(parts)


class WindowsOCREngine:
    """Motore OCR che usa Windows.Media.Ocr (nativo Windows 10/11)."""

    def process(
        self,
        file_path: str,
        lang_tag: str = "it-IT",
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        """Esegue OCR su un file immagine o PDF.

        Args:
            file_path: percorso del file da elaborare.
            lang_tag: tag lingua BCP-47 (es. "it-IT", "en-US").
            on_progress: callback(pagina_corrente, totale_pagine).
            cancel_event: evento di cancellazione.

        Returns:
            Testo riconosciuto.

        Raises:
            ImportError: se i pacchetti winrt non sono installati.
            ValueError: se la lingua non è installata in Windows.
            InterruptedError: se l'operazione viene interrotta.
        """
        _check_winrt()

        import os
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._process_pdf(file_path, lang_tag, on_progress, cancel_event, on_partial)
        else:
            return self._process_image(file_path, lang_tag, on_progress)

    def _process_image(
        self,
        file_path: str,
        lang_tag: str,
        on_progress: Callable[[int, int], None] | None,
    ) -> str:
        """OCR su singola immagine."""
        img = Image.open(file_path).convert("RGB")
        text = _run_winrt(_recognize_async(img, lang_tag))
        if on_progress:
            on_progress(1, 1)
        return text.strip()

    def _process_pdf(
        self,
        file_path: str,
        lang_tag: str,
        on_progress: Callable[[int, int], None] | None,
        cancel_event: threading.Event | None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        """OCR su PDF multipagina: converte ogni pagina in immagine PIL e applica Windows OCR."""
        doc = fitz.open(file_path)
        total = len(doc)
        pages_text = []

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")

            # Renderizza a 300 DPI come Tesseract, converti in RGB sicuro
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            text = _run_winrt(_recognize_async(img, lang_tag))
            pages_text.append(text.strip())

            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        return "\f".join(pages_text)
