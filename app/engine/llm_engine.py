"""Motori LLM per la correzione del testo OCR."""

import abc
import threading
from typing import Callable

import requests
from google import genai

from app.engine.chunker import TextChunker

SYSTEM_PROMPT = (
    "Sei un correttore OCR. Il testo che ricevi è stato acquisito tramite OCR e contiene "
    "errori di riconoscimento. Il tuo compito è ESCLUSIVAMENTE correggere gli errori OCR. "
    "NON devi riassumere, commentare, parafrasare o modificare il contenuto. "
    "NON devi aggiungere testo che non sia presente nell'originale. "
    "Mantieni esattamente la formattazione, i capoversi e la struttura del testo. "
    "Restituisci SOLO il testo corretto, senza premesse né commenti."
)


class LLMEngine(abc.ABC):
    """Classe base astratta per i motori LLM."""

    @abc.abstractmethod
    def correct(self, text_chunk: str) -> str:
        """Corregge un singolo chunk di testo."""
        ...

    def correct_document(
        self,
        full_text: str,
        chunker: TextChunker,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Corregge un documento intero con chunking.

        Args:
            full_text: testo completo da correggere.
            chunker: istanza di TextChunker.
            on_progress: callback(chunk_corrente, totale_chunk).
            cancel_event: evento di cancellazione; se set, interrompe l'elaborazione.

        Returns:
            Testo corretto riassemblato.

        Raises:
            InterruptedError: se l'operazione viene interrotta.
        """
        chunks = chunker.split(full_text)
        total = len(chunks)
        corrected_parts = []

        for i, chunk in enumerate(chunks, 1):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Correzione interrotta dall'utente.")
            corrected = self.correct(chunk)
            corrected_parts.append(corrected)
            if on_chunk:
                on_chunk("\n\n".join(corrected_parts))
            if on_progress:
                on_progress(i, total)

        return "\n\n".join(corrected_parts)


OLLAMA_CLOUD_URL = "https://ollama.com"


class OllamaEngine(LLMEngine):
    """Motore LLM che usa Ollama (locale o cloud)."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "",
                 api_key: str = "", cloud: bool = False):
        self.url = OLLAMA_CLOUD_URL if cloud else url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.cloud = cloud

    def correct(self, text_chunk: str) -> str:
        headers = {}
        if self.cloud and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Tetto di sicurezza per la generazione: l'output corretto ha circa la
        # lunghezza dell'input, quindi limitiamo i token per evitare che un
        # eventuale loop di ripetizione giri fino al timeout.
        max_tokens = max(256, len(text_chunk) // 2 + 256)

        if self.cloud:
            # Cloud: usa endpoint OpenAI-compatible
            resp = requests.post(
                f"{self.url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text_chunk},
                    ],
                    "stream": False,
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                },
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            # Locale: usa /api/generate con prompt diretto.
            # think=False disattiva il "thinking mode" dei modelli reasoner
            # (es. Gemma 4): senza, il modello ragiona all'infinito e non
            # emette mai il testo finale entro il timeout.
            payload = {
                "model": self.model,
                "system": SYSTEM_PROMPT,
                "prompt": text_chunk,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.2,
                    "repeat_penalty": 1.2,
                    "num_predict": max_tokens,
                },
            }
            resp = requests.post(
                f"{self.url}/api/generate",
                headers=headers,
                json=payload,
                timeout=300,
            )
            # I modelli senza thinking possono rifiutare il campo think:
            # in quel caso ripetiamo la richiesta senza di esso.
            if resp.status_code == 400 and "think" in resp.text.lower():
                payload.pop("think", None)
                resp = requests.post(
                    f"{self.url}/api/generate",
                    headers=headers,
                    json=payload,
                    timeout=300,
                )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()


class GeminiEngine(LLMEngine):
    """Motore LLM che usa Google Gemini."""

    def __init__(self, api_key: str = "", model: str = "gemini-2.0-flash"):
        self.model = model
        self.client = genai.Client(api_key=api_key)

    def correct(self, text_chunk: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=text_chunk,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
            ),
        )
        return response.text.strip()


def create_engine(config: dict) -> LLMEngine:
    """Factory: crea il motore LLM in base alla configurazione."""
    provider = config.get("llm_provider", "ollama")
    if provider == "gemini":
        return GeminiEngine(
            api_key=config.get("gemini_api_key", ""),
            model=config.get("gemini_model", "gemini-2.0-flash"),
        )
    else:
        return OllamaEngine(
            url=config.get("ollama_url", "http://localhost:11434"),
            model=config.get("ollama_model", ""),
            api_key=config.get("ollama_api_key", ""),
            cloud=config.get("ollama_cloud", False),
        )
