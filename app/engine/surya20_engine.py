"""Motore OCR Surya 0.20.

Sottoclasse di SuryaEngine che punta al venv surya20-venv e al worker
surya20_worker.py. Il protocollo IPC (JSON stdin/stdout) è identico a
SuryaEngine 0.17.x; cambiano solo i percorsi del venv e del worker.

Il daemon (processo persistente) permette di tenere il server di inferenza
attivo tra una sessione OCR e l'altra, evitando il lungo avvio a ogni richiesta.
"""

import threading
from typing import ClassVar, Optional

from app.engine.surya_engine import SuryaEngine


class Surya20Engine(SuryaEngine):
    """Motore OCR Surya 0.20 (VLM + Docker/llama.cpp)."""

    _VENV_NAME = "surya20-venv"
    _WORKER_NAME = "surya20_worker.py"
    _PDF_DPI = 300

    _daemon: ClassVar[Optional["Surya20Engine"]] = None
    _daemon_lock: ClassVar[threading.Lock] = threading.Lock()

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
