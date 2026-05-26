"""Motore OCR basato su Vision Language Model (Ollama)."""

import base64
import io
import os
import threading
from typing import Callable

try:
    import pymupdf as fitz  # PyMuPDF >= 1.23
except ImportError:
    import fitz  # PyMuPDF < 1.23
import requests
from PIL import Image

from app.engine.llm_engine import OLLAMA_CLOUD_URL

VLM_PROMPT = (
    "Estrai tutto il testo presente in questa immagine. "
    "Restituisci SOLO il testo, senza commenti né descrizioni."
)


class VLMEngine:
    """Motore OCR che usa un Vision Language Model tramite Ollama."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "",
                 api_key: str = "", cloud: bool = False):
        self.url = OLLAMA_CLOUD_URL if cloud else url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.cloud = cloud

    def process(
        self,
        file_path: str,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._process_pdf(file_path, on_progress, cancel_event, on_partial)
        else:
            return self._process_image(file_path, on_progress)

    def _process_image(
        self,
        file_path: str,
        on_progress: Callable[[int, int], None] | None,
    ) -> str:
        with open(file_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        result = self._call_vlm(image_b64)
        if on_progress:
            on_progress(1, 1)
        return result

    def _process_pdf(
        self,
        file_path: str,
        on_progress: Callable[[int, int], None] | None,
        cancel_event: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        doc = fitz.open(file_path)
        total = len(doc)
        pages_text = []

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")

            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            text = self._call_vlm(image_b64)
            pages_text.append(text.strip())

            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        return "\f".join(pages_text)

    def _call_vlm(self, image_b64: str) -> str:
        headers = {}
        if self.cloud and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self.cloud:
            resp = requests.post(
                f"{self.url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": VLM_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}",
                                    },
                                },
                            ],
                        },
                    ],
                    "stream": False,
                },
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            resp = requests.post(
                f"{self.url}/api/generate",
                headers=headers,
                json={
                    "model": self.model,
                    "prompt": VLM_PROMPT,
                    "images": [image_b64],
                    "stream": False,
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
