"""Gestione del server vLLM per Chandra OCR.

Avvia e monitora il processo wsl.exe che esegue vLLM all'interno di WSL2.
Il server espone un'API OpenAI-compatible su http://localhost:<porta>.

Ciclo di vita:
  start(config)        → avvia wsl.exe in background (non-blocking)
  wait_ready(url, ...) → sonda /health finché il server risponde (blocking)
  stop()               → termina il processo
  is_alive()           → True se il processo è ancora in esecuzione
"""

import collections
import subprocess
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Stato globale (singleton)
# ---------------------------------------------------------------------------

_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()
_log: collections.deque = collections.deque(maxlen=200)  # ultime 200 righe di output
_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Costruzione del comando vLLM
# ---------------------------------------------------------------------------

_QUANT_KEYS = ["float16", "fp8", "awq"]


def build_vllm_command(config: dict) -> list[str]:
    """Costruisce il comando wsl.exe per avviare il server vLLM."""
    quantization  = config.get("vllm_quantization", "fp8")
    max_tokens    = int(config.get("vllm_max_tokens", 3072))
    gpu_pct       = int(config.get("vllm_gpu_memory", 88))
    gpu_util      = max(0.5, min(1.0, gpu_pct / 100.0))
    enforce_eager = config.get("vllm_enforce_eager", True)
    distro        = config.get("vllm_wsl_distro", "").strip()
    hf_model      = config.get("vllm_hf_model", "datalab-to/chandra-ocr-2").strip()
    port          = _extract_port(config.get("chandra_vllm_url", "http://localhost:8000"))

    parts = [
        "source ~/vllm-env/bin/activate",
        "&&",
        "python -m vllm.entrypoints.openai.api_server",
        f"--model {hf_model}",
        "--served-model-name chandra",
        "--trust-remote-code",
        f"--max-model-len {max_tokens}",
        f"--gpu-memory-utilization {gpu_util:.2f}",
    ]

    if quantization == "fp8":
        parts += ["--quantization fp8", "--kv-cache-dtype fp8"]
    elif quantization == "awq":
        parts.append("--quantization awq")
    # float16: nessun flag aggiuntivo

    if enforce_eager:
        parts.append("--enforce-eager")

    parts += [f"--port {port}", "--host 0.0.0.0"]

    extra = config.get("vllm_extra_args", "").strip()
    if extra:
        parts.append(extra)

    bash_cmd = " ".join(parts)

    wsl_cmd = ["wsl.exe"]
    if distro:
        wsl_cmd += ["-d", distro]
    wsl_cmd += ["bash", "-c", bash_cmd]
    return wsl_cmd


def _extract_port(url: str) -> int:
    try:
        return urlparse(url).port or 8000
    except Exception:
        return 8000


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def start(config: dict) -> None:
    """Avvia il server vLLM (non-blocking). Ignora la chiamata se già attivo."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return
        cmd = build_vllm_command(config)
        _proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    with _log_lock:
        _log.clear()

    def _drain():
        try:
            for line in _proc.stdout:
                with _log_lock:
                    _log.append(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()


def stop() -> None:
    """Termina il processo vLLM."""
    global _proc
    with _lock:
        if _proc is not None:
            try:
                _proc.terminate()
            except Exception:
                pass
            _proc = None


def is_alive() -> bool:
    """True se il processo WSL è ancora in esecuzione."""
    with _lock:
        return _proc is not None and _proc.poll() is None


def wait_ready(
    url: str,
    timeout: int = 300,
    on_tick: Optional[Callable[[int], None]] = None,
) -> bool:
    """Sonda /health finché il server risponde 200 o scade il timeout.

    Args:
        url:     URL base del server (es. 'http://localhost:8000').
        timeout: Secondi massimi di attesa (default 300 — caricamento modello).
        on_tick: Callback(secondi_trascorsi) chiamata ogni 2 secondi.

    Returns:
        True se il server è pronto, False altrimenti.
    """
    health_url = url.rstrip("/") + "/health"
    deadline = time.time() + timeout
    elapsed = 0

    while time.time() < deadline:
        if not is_alive():
            return False
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
        elapsed += 2
        if on_tick:
            on_tick(elapsed)

    return False


def get_log() -> str:
    """Restituisce le ultime righe di output del processo vLLM."""
    with _log_lock:
        return "\n".join(_log)


def list_wsl_distros() -> list[str]:
    """Elenca le distro WSL2 installate sul sistema."""
    try:
        result = subprocess.run(
            ["wsl.exe", "--list", "--quiet"],
            capture_output=True,
            timeout=10,
            text=False,  # output è UTF-16LE su Windows
        )
        # wsl.exe --list --quiet usa UTF-16LE (con possibile BOM)
        raw = result.stdout.decode("utf-16-le", errors="replace")
        distros = []
        for line in raw.splitlines():
            name = line.strip().rstrip("\x00").strip()
            if name:
                distros.append(name)
        return distros
    except Exception:
        return []
