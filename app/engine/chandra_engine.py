"""Motore OCR Chandra 2 (datalab-to/chandra-ocr-2).

Chandra è il modello OCR "grande" di Datalab: un VLM (~4B parametri, Chandra 2)
che converte immagini e PDF in Markdown/HTML preservando il layout, con bounding
box per blocco. Supera Surya su tabelle, layout complessi, manoscritto e forme.

Architettura identica a Surya 0.2: ChandraEngine è una sottoclasse di SuryaEngine
che punta al venv `chandra-venv` e al worker `chandra_worker.py`. Il modello gira
in un subprocess persistente (daemon) nel venv isolato, caricato una volta con
warm-up; il protocollo IPC (JSON stdin/stdout) e tutta la macchina di
watchdog/retry sono ereditati da SuryaEngine.

Il backend è HuggingFace (transformers) con quantizzazione 4-bit (bitsandbytes)
per stare entro ~12 GB di VRAM. Nessun server vLLM/WSL.
"""

import threading
from typing import Callable, ClassVar, Optional

from app.engine.surya_engine import SuryaEngine


class ChandraEngine(SuryaEngine):
    """Motore OCR Chandra 2 (backend HuggingFace, 4-bit)."""

    _VENV_NAME = "chandra-venv"
    _WORKER_NAME = "chandra_worker.py"
    # Chandra rasterizza a ~192 DPI (settings.IMAGE_DPI); 200 è vicino e assicura
    # risoluzione sufficiente. load_image nel worker riscala comunque a MIN_IMAGE_DIM.
    _PDF_DPI = 200

    # Un VLM 4B a 4-bit su GPU consumer può impiegare minuti per pagina: budget
    # ampio prima di considerare la GPU bloccata e riavviare il worker.
    _PAGE_TIMEOUT_S = 600
    _MIN_IPC_TIMEOUT_S = 600

    _daemon: ClassVar[Optional["ChandraEngine"]] = None
    _daemon_lock: ClassVar[threading.Lock] = threading.Lock()

    def _build_worker_env(self) -> dict:
        """Env del worker con la configurazione Chandra (quantizzazione, token)."""
        env = super()._build_worker_env()
        try:
            from app.config import load_config
            config = load_config()
        except Exception:
            config = {}
        env["CHANDRA_QUANTIZE"] = str(config.get("chandra_quantize", "4bit"))
        max_tokens = config.get("chandra_max_tokens")
        if max_tokens:
            env["MAX_OUTPUT_TOKENS"] = str(max_tokens)
        return env

    def process(
        self,
        file_path: str,
        on_progress: Optional[Callable] = None,
        cancel_event=None,
        on_partial: Optional[Callable] = None,
    ) -> str:
        """Esegue l'OCR via subprocess nel venv Chandra.

        A differenza di SuryaEngine non esiste una modalità "import diretto":
        Chandra non è mai installato nel venv principale, quindi serve sempre il
        subprocess. Se il venv non è configurato, errore esplicito.
        """
        if not self._python_exe:
            raise RuntimeError(
                "Chandra non è installato.\n"
                "Installalo dalle impostazioni (motore OCR → Chandra → Installa)."
            )
        return self._process_subprocess(file_path, on_progress, cancel_event, on_partial)

    # ------------------------------------------------------------------
    # Daemon (processo persistente, tiene il modello caldo tra le sessioni)
    # ------------------------------------------------------------------

    @classmethod
    def get_daemon(cls) -> Optional["ChandraEngine"]:
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
