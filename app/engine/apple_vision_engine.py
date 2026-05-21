"""Motore OCR tramite Apple Vision (macOS).

Usa il framework Vision di macOS via uno script Swift long-running che parla
con il processo Python su stdin/stdout (una riga JSON per richiesta).

Richiede macOS 10.15+ e Xcode Command Line Tools (per `swift`).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable, Optional

import fitz  # PyMuPDF

# Mappa codici Tesseract -> codici BCP-47 supportati da Vision.
# Tutto il resto viene passato così com'è (l'utente può inserire un BCP-47 a mano).
_TESS_TO_VISION = {
    "ita": "it-IT",
    "eng": "en-US",
    "fra": "fr-FR",
    "deu": "de-DE",
    "spa": "es-ES",
    "por": "pt-BR",
    "chi_sim": "zh-Hans",
    "chi_tra": "zh-Hant",
    "jpn": "ja-JP",
    "kor": "ko-KR",
    "rus": "ru-RU",
    "ukr": "uk-UA",
    "ara": "ar-SA",
    "tha": "th-TH",
    "vie": "vi-VT",
}


def _helper_script_path() -> str:
    """Percorso dello script Swift helper."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "apple_vision_helper.swift")
    return os.path.join(os.path.dirname(__file__), "apple_vision_helper.swift")


def _swift_cmd() -> Optional[str]:
    """Restituisce il path dell'eseguibile swift, o None se non trovato."""
    return shutil.which("swift")


def is_available() -> bool:
    """True solo su macOS con swift installato."""
    if sys.platform != "darwin":
        return False
    return _swift_cmd() is not None


def _to_vision_lang(lang: str) -> str:
    if not lang:
        return "it-IT"
    return _TESS_TO_VISION.get(lang, lang)


class AppleVisionEngine:
    """Motore OCR via framework Vision di macOS."""

    def process(
        self,
        file_path: str,
        lang: str = "ita",
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        use_language_correction: bool = True,
    ) -> str:
        if sys.platform != "darwin":
            raise RuntimeError(
                "Apple Vision è disponibile solo su macOS."
            )
        swift = _swift_cmd()
        if not swift:
            raise RuntimeError(
                "Swift non trovato. Installa gli Xcode Command Line Tools con:\n"
                "  xcode-select --install"
            )
        helper = _helper_script_path()
        if not os.path.isfile(helper):
            raise FileNotFoundError(f"Helper Apple Vision non trovato: {helper}")

        vision_lang = _to_vision_lang(lang)

        proc = subprocess.Popen(
            [swift, helper],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

        # Drena stderr per evitare deadlock se Swift stampa avvisi.
        stderr_lines: list[str] = []

        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except Exception:
                pass

        threading.Thread(target=_drain_stderr, daemon=True).start()

        try:
            ready = proc.stdout.readline()
            if not ready:
                proc.wait()
                err = "".join(stderr_lines)
                raise RuntimeError(
                    f"Helper Apple Vision non risponde.\n{err[:500]}"
                )
            msg = json.loads(ready)
            if msg.get("type") == "error":
                raise RuntimeError(msg.get("message", "errore sconosciuto nel helper"))

            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".pdf":
                result = self._process_pdf(
                    file_path, proc, vision_lang,
                    on_progress, cancel_event, on_partial,
                    use_language_correction,
                )
            else:
                result = self._process_image(
                    file_path, proc, vision_lang, on_progress,
                    use_language_correction,
                )
        except InterruptedError:
            proc.terminate()
            raise
        except Exception:
            proc.terminate()
            raise
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()

        return result

    def _send(self, proc, img_path: str, lang: str,
              use_language_correction: bool) -> str:
        req = json.dumps({
            "path": img_path,
            "lang": lang,
            "useLanguageCorrection": use_language_correction,
        })
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Il helper Apple Vision ha smesso di rispondere.")
        msg = json.loads(line)
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "errore nel helper"))
        return msg.get("text", "")

    def _process_image(self, file_path, proc, lang, on_progress,
                       use_language_correction) -> str:
        text = self._send(proc, file_path, lang, use_language_correction)
        if on_progress:
            on_progress(1, 1)
        return text.strip()

    def _process_pdf(self, file_path, proc, lang,
                     on_progress, cancel_event, on_partial,
                     use_language_correction) -> str:
        doc = fitz.open(file_path)
        total = len(doc)
        tmp_dir = tempfile.mkdtemp(prefix="apple_vision_ocr_")
        pages_text: list[str] = []

        try:
            for i, page in enumerate(doc):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("OCR interrotto dall'utente.")
                pix = page.get_pixmap(dpi=300)
                img_path = os.path.join(tmp_dir, f"page_{i:04d}.png")
                pix.save(img_path)

                text = self._send(proc, img_path, lang, use_language_correction)
                pages_text.append(text.strip())

                if on_partial:
                    on_partial("\f".join(pages_text))
                if on_progress:
                    on_progress(i + 1, total)
        finally:
            doc.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return "\f".join(pages_text)
