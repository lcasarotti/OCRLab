"""Motore OCR tramite Chandra (datalab-to/chandra-ocr-2).

Chandra è un modello VLM che converte immagini e PDF in Markdown/HTML/JSON
preservando il layout. Supera Surya su layout complessi, tabelle, testo
multi-colonna e scrittura a mano.

Due modalità operative:
  - "hf"   : esegue la CLI di Chandra in un subprocess Windows (lento, carica
             il modello ogni volta). Richiede chandra-ocr[hf] nel venv Windows.
  - "vllm" : usa il server vLLM già avviato in WSL (veloce, modello caricato
             una volta, API OpenAI-compatible su http://localhost:8000).
             Avviare il server con: bash /root/vllm/start_chandra.sh

Installazione:
  Windows (hf):  pip install chandra-ocr[hf]
  WSL    (vllm): vedi /root/vllm/start_chandra.sh
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Optional

import pymupdf as fitz  # PyMuPDF
import requests
from PIL import Image


# ---------------------------------------------------------------------------
# Utilità comuni
# ---------------------------------------------------------------------------

def _strip_chandra_metadata(text: str) -> str:
    """Rimuove frontmatter YAML e whitespace ridondante."""
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    return text.strip()


def _image_to_base64(img: Image.Image, fmt: str = "PNG") -> str:
    """Converte un'immagine PIL in stringa base64."""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ---------------------------------------------------------------------------
# Modalità vLLM (server WSL)
# ---------------------------------------------------------------------------

def _vllm_ocr_page(image_b64: str, vllm_url: str, max_tokens: int = 4096) -> str:
    """Invia una singola pagina (base64 PNG) al server vLLM e restituisce il testo."""
    prompt = (
        "Convert the image to markdown. "
        "Preserve the document layout and reading order. "
        "Output only the markdown text, no explanations."
    )
    payload = {
        "model": "chandra",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    # Disabilita i proxy di sistema: l'IP WSL non deve passare per proxy Windows
    session = requests.Session()
    session.trust_env = False
    resp = session.post(
        f"{vllm_url}/v1/chat/completions",
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Modalità hf (subprocess Windows)
# ---------------------------------------------------------------------------

def _chandra_exe(python_exe: str) -> str:
    """Ricava il percorso dell'eseguibile 'chandra' dalla cartella Scripts del venv."""
    if not python_exe:
        return "chandra"
    scripts_dir = os.path.dirname(python_exe)
    for name in ("chandra.exe", "chandra"):
        candidate = os.path.join(scripts_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return "chandra"


def _find_md_output(output_dir: str, stem: str) -> Optional[str]:
    """Trova il file .md prodotto da Chandra nella sottocartella output."""
    candidate = os.path.join(output_dir, stem, f"{stem}.md")
    if os.path.isfile(candidate):
        return candidate
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".md"):
                return os.path.join(root, f)
    return None


def is_available_hf(python_exe: str = "") -> bool:
    """Verifica se chandra-ocr è installato nel Python Windows indicato."""
    exe = _chandra_exe(python_exe)
    try:
        result = subprocess.run(
            [exe, "--help"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def is_available_vllm(vllm_url: str = "http://localhost:8000") -> bool:
    """Verifica se il server vLLM è raggiungibile."""
    try:
        resp = requests.get(f"{vllm_url}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Engine principale
# ---------------------------------------------------------------------------

class ChandraEngine:
    """Motore OCR Chandra.

    Args:
        method:     "vllm" (server WSL, raccomandato) o "hf" (subprocess Windows).
        python_exe: percorso di python.exe del venv Windows con chandra[hf].
                    Usato solo con method="hf".
        vllm_url:   URL del server vLLM. Default: http://localhost:8000.
        max_tokens: token massimi per pagina (vllm). Default: 4096.
    """

    def __init__(
        self,
        method: str = "vllm",
        python_exe: str = "",
        vllm_url: str = "http://localhost:8000",
        max_tokens: int = 4096,
    ):
        self._method = method
        self._python_exe = python_exe or "python"
        self._vllm_url = vllm_url.rstrip("/")
        self._max_tokens = max_tokens

    def process(
        self,
        file_path: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        on_partial: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Esegue l'OCR su un file immagine o PDF.

        Returns:
            Testo Markdown estratto.

        Raises:
            InterruptedError: se cancel_event viene impostato.
            RuntimeError: per errori del motore.
        """
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("OCR interrotto dall'utente.")

        if self._method == "vllm":
            return self._process_vllm(file_path, on_progress, cancel_event, on_partial)
        else:
            return self._process_hf(file_path, on_progress, cancel_event)

    # ------------------------------------------------------------------
    # Modalità vLLM
    # ------------------------------------------------------------------

    def _process_vllm(self, file_path, on_progress, cancel_event, on_partial=None) -> str:
        """OCR via server vLLM: converte ogni pagina in immagine e la invia all'API."""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._vllm_pdf(file_path, on_progress, cancel_event, on_partial)
        else:
            return self._vllm_image(file_path, on_progress)

    def _vllm_image(self, file_path, on_progress) -> str:
        img = Image.open(file_path).convert("RGB")
        b64 = _image_to_base64(img)
        if on_progress:
            on_progress(0, 1)
        text = _vllm_ocr_page(b64, self._vllm_url, self._max_tokens)
        if on_progress:
            on_progress(1, 1)
        return _strip_chandra_metadata(text)

    def _vllm_pdf(self, file_path, on_progress, cancel_event, on_partial=None) -> str:
        doc = fitz.open(file_path)
        total = len(doc)
        pages_text = []

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")

            if on_progress:
                on_progress(i, total)

            pix = page.get_pixmap(dpi=150)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            b64 = _image_to_base64(img)

            text = _vllm_ocr_page(b64, self._vllm_url, self._max_tokens)
            pages_text.append(_strip_chandra_metadata(text))

            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        return "\f".join(pages_text)

    # ------------------------------------------------------------------
    # Modalità hf (subprocess Windows)
    # ------------------------------------------------------------------

    def _process_hf(self, file_path, on_progress, cancel_event) -> str:
        """OCR via CLI di Chandra in subprocess Windows."""
        if on_progress:
            on_progress(0, 1)

        tmp_dir = tempfile.mkdtemp(prefix="chandra_ocr_")
        try:
            result_text = self._run_hf_subprocess(
                file_path, tmp_dir, cancel_event, _chandra_exe(self._python_exe)
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if on_progress:
            on_progress(1, 1)

        return result_text

    def _run_hf_subprocess(self, file_path, output_dir, cancel_event, chandra_exe) -> str:
        cmd = [
            chandra_exe,
            file_path,
            output_dir,
            "--method", "hf",
            "--no-images",
            "--no-headers-footers",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Eseguibile Chandra non trovato: {chandra_exe}\n"
                "Verifica il percorso Python nelle impostazioni.\n"
                "Installazione: pip install chandra-ocr[hf]"
            )

        stderr_lines: list[str] = []

        def _drain():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except Exception:
                pass

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()

        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_event and cancel_event.is_set():
                    proc.terminate()
                    drain_thread.join(timeout=2)
                    raise InterruptedError("OCR interrotto dall'utente.")

        drain_thread.join(timeout=5)

        if proc.returncode != 0:
            stderr = "".join(stderr_lines)
            if "No module named chandra" in stderr:
                raise RuntimeError(
                    "Chandra non trovato nel Python selezionato.\n"
                    "Installa con: pip install chandra-ocr[hf]"
                )
            raise RuntimeError(
                f"Chandra ha restituito un errore (codice {proc.returncode}).\n"
                f"{stderr[:500]}"
            )

        stem = os.path.splitext(os.path.basename(file_path))[0]
        md_path = _find_md_output(output_dir, stem)

        if not md_path:
            raise FileNotFoundError(
                "Chandra non ha prodotto output.\n"
                "Controlla che il file sia un'immagine o PDF valido."
            )

        with open(md_path, "r", encoding="utf-8") as f:
            raw = f.read()

        return _strip_chandra_metadata(raw)
