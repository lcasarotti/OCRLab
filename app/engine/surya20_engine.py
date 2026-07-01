"""Motore OCR Surya 0.20.

Sottoclasse di SuryaEngine che punta al venv surya20-venv e al worker
surya20_worker.py. Il protocollo IPC (JSON stdin/stdout) è identico a
SuryaEngine 0.17.x; cambiano solo i percorsi del venv e del worker.

Il daemon (processo persistente) permette di tenere il server di inferenza
attivo tra una sessione OCR e l'altra, evitando il lungo avvio a ogni richiesta.
"""

import os
import tempfile
import threading
from typing import ClassVar, Optional

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.engine.surya_engine import SuryaEngine


class Surya20Engine(SuryaEngine):
    """Motore OCR Surya 0.20 (VLM + Docker/llama.cpp)."""

    _VENV_NAME = "surya20-venv"
    _WORKER_NAME = "surya20_worker.py"
    _PDF_DPI = 300

    _daemon: ClassVar[Optional["Surya20Engine"]] = None
    _daemon_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def _batch_size() -> int:
        """Numero di pagine per batch, da config (default 4, minimo 1)."""
        try:
            from app.config import load_config
            val = int(load_config().get("surya20_batch_size", 4))
            return max(1, val)
        except Exception:
            return 4

    @staticmethod
    def _parallel() -> int:
        """Slot paralleli del server di inferenza, da config (default 8, minimo 1)."""
        try:
            from app.config import load_config
            val = int(load_config().get("surya20_parallel", 8))
            return max(1, val)
        except Exception:
            return 8

    def _build_worker_env(self) -> dict:
        """Env del worker con SURYA_INFERENCE_PARALLEL impostato da config.

        Il valore è letto dal server di inferenza (llama.cpp/vllm) al momento
        dello spawn: controlla sia gli slot --parallel del server sia i thread
        client concorrenti. Va impostato prima dell'avvio del daemon; per
        cambiarlo serve riavviare il server Surya 0.2.
        """
        env = super()._build_worker_env()
        env["SURYA_INFERENCE_PARALLEL"] = str(self._parallel())
        return env

    def _subprocess_pdf(self, file_path, proc, on_progress, cancel_event, on_partial=None) -> str:
        """OCR di un PDF processando le pagine a batch.

        Se il worker non supporta il batch (bundle vecchio) o il batch size è 1,
        delega all'implementazione pagina-per-pagina della classe base.
        """
        batch_size = self._batch_size()
        if not self._supports_batch or batch_size <= 1:
            return super()._subprocess_pdf(
                file_path, proc, on_progress, cancel_event, on_partial
            )

        doc = fitz.open(file_path)
        total = len(doc)
        tmp_dir = tempfile.mkdtemp(prefix="surya_ocr_")
        pages_text: list = []
        pages_html: list = []
        pages_blocks: list = []

        try:
            for start in range(0, total, batch_size):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("OCR interrotto dall'utente.")

                chunk = range(start, min(start + batch_size, total))
                paths = []
                for i in chunk:
                    pix = doc[i].get_pixmap(dpi=self._PDF_DPI)
                    img_path = os.path.join(tmp_dir, f"page_{i:04d}.png")
                    pix.save(img_path)
                    paths.append(img_path)

                # forced_angle sempre None in 0.20 (nessuna auto-rotazione)
                results = self._send_batch(proc, paths, None)
                for text, _angle, html, blocks in results:
                    pages_text.append(text.strip())
                    pages_html.append(html)
                    pages_blocks.append(blocks)

                if on_partial:
                    on_partial("\f".join(pages_text))
                if on_progress:
                    on_progress(min(start + batch_size, total), total)
        finally:
            doc.close()
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        self._last_html = '<hr class="page-break">\n'.join(pages_html)
        self._last_blocks = pages_blocks
        return "\f".join(pages_text)

    @classmethod
    def get_daemon(cls) -> Optional["Surya20Engine"]:
        """Restituisce il daemon attivo, o None se non è in esecuzione."""
        with cls._daemon_lock:
            if cls._daemon is not None and not cls._daemon.is_running():
                cls._daemon = None
            return cls._daemon

    @classmethod
    def is_daemon_running(cls) -> bool:
        return cls.get_daemon() is not None

    @classmethod
    def start_daemon(cls, python_exe: str = "") -> None:
        """Avvia il daemon (blocca fino a "ready"). Idempotente se già attivo."""
        with cls._daemon_lock:
            if cls._daemon is not None and cls._daemon.is_running():
                return
            inst = cls(python_exe=python_exe)
            inst.start()
            cls._daemon = inst

    @classmethod
    def stop_daemon(cls) -> None:
        """Ferma il daemon."""
        with cls._daemon_lock:
            if cls._daemon is not None:
                cls._daemon.stop()
                cls._daemon = None
