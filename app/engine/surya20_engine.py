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

    # Pagine per batch quando il batch è attivo: oltre questo limite Surya non
    # regge su GPU consumer (throttling/degrado VRAM).
    _BATCH_SIZE = 4

    _daemon: ClassVar[Optional["Surya20Engine"]] = None
    _daemon_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def _batch_enabled() -> bool:
        """True se l'utente ha attivato il processamento a batch (default True)."""
        try:
            from app.config import load_config
            return bool(load_config().get("surya20_batch", True))
        except Exception:
            return True

    @classmethod
    def _batch_size(cls) -> int:
        """Pagine per batch: _BATCH_SIZE se il batch è attivo, altrimenti 1."""
        return cls._BATCH_SIZE if cls._batch_enabled() else 1

    @classmethod
    def _parallel(cls) -> int:
        """Slot paralleli del server di inferenza: allineati al batch size."""
        return cls._batch_size()

    @staticmethod
    def _detect_forced_angle(pix) -> Optional[int]:
        """Angolo ANTIORARIO (convenzione PIL/worker) per raddrizzare la pagina.

        La 0.20 non auto-rileva la rotazione (a differenza della 0.17), quindi la
        rileviamo qui riusando l'OSD del writer del PDF (Tesseract) sul pixmap già
        rasterizzato e la passiamo al worker: così le scansioni di lato — es. gli
        spread doppia-pagina ruotati — arrivano dritte al modello e lo split a
        metà scatta (altrimenti le due pagine restano fuse). Restituisce None se
        non c'è rotazione affidabile. L'OSD restituisce gradi ORARI; il worker
        ruota in senso antiorario (PIL), da cui la conversione.
        """
        try:
            from app.formats.output_writer import _detect_rotation
            rot = _detect_rotation(pix)  # 0/90/180/270 ORARI
        except Exception:
            return None
        if rot in (90, 180, 270):
            return (360 - rot) % 360
        return None

    def _build_worker_env(self) -> dict:
        """Env del worker con SURYA_INFERENCE_PARALLEL allineato al batch.

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

        Se il worker non supporta il batch (bundle vecchio) delega
        all'implementazione pagina-per-pagina della classe base. Col batch
        disattivato (`batch_size==1`) NON deleghiamo: usiamo comunque questo loop
        con chunk da una pagina, così la rilevazione di rotazione per pagina
        (necessaria alla 0.20 per lo split degli spread ruotati) resta attiva —
        un solo path per chunk rispetta comunque il tetto di una pagina in volo.
        """
        batch_size = self._batch_size()
        if not self._supports_batch:
            return super()._subprocess_pdf(
                file_path, proc, on_progress, cancel_event, on_partial
            )

        doc = fitz.open(file_path)
        total = len(doc)
        tmp_dir = tempfile.mkdtemp(prefix="surya_ocr_")
        pages_text: list = []
        pages_html: list = []
        pages_blocks: list = []
        pages_angles: list = []
        deg_state = {"suspect": 0, "empty": 0, "restarts": 0, "seen_text": False}

        try:
            for start in range(0, total, batch_size):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("OCR interrotto dall'utente.")

                chunk = range(start, min(start + batch_size, total))
                paths = []
                angles = []
                for i in chunk:
                    pix = doc[i].get_pixmap(dpi=self._PDF_DPI)
                    img_path = os.path.join(tmp_dir, f"page_{i:04d}.png")
                    pix.save(img_path)
                    paths.append(img_path)
                    # Rotazione rilevata per pagina (la 0.20 non la auto-rileva):
                    # raddrizza le scansioni di lato prima dello split doppia-pagina.
                    angles.append(self._detect_forced_angle(pix))

                # _send_with_retry riavvia il worker e ritenta una volta se il
                # server d'inferenza crasha o si blocca (throttling GPU);
                # _maybe_recover_degraded gestisce il degrado "silenzioso"
                # (pagine vuote pur con layout di testo).
                send = lambda p, pp=list(paths), aa=list(angles): self._send_batch(
                    p, pp, None, forced_angles=aa)
                results = self._send_with_retry(send, cancel_event)
                results = self._maybe_recover_degraded(
                    results, send, cancel_event, deg_state, len(pages_text)
                )
                for text, _angle, html, blocks, _suspect in results:
                    pages_text.append(text.strip())
                    pages_html.append(html)
                    pages_blocks.append(blocks)
                    # Angolo (ORARIO) con cui il writer del PDF deve raddrizzare
                    # l'immagine per farla combaciare con le bbox già dritte del
                    # worker. Il worker ruota in senso ANTIORARIO (PIL) di `_angle`;
                    # per il writer serve l'equivalente orario. None se la pagina
                    # non è stata ruotata (il writer si autorileva come prima).
                    pages_angles.append(
                        (360 - _angle) % 360 if _angle else None)

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
        self._last_angles = pages_angles
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
