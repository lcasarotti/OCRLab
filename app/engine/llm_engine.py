"""Motori LLM per la correzione del testo OCR."""

import abc
import re
import threading
import time
from typing import Callable

import requests
from google import genai

from app.engine.chunker import TextChunker


def is_rate_limit_error(exc: Exception) -> bool:
    """Riconosce gli errori di rate limit / quota esaurita dei provider cloud.

    Gemini solleva eccezioni con status 429 / RESOURCE_EXHAUSTED, Ollama cloud
    restituisce HTTP 429. Ci basiamo sia sul codice sia sul testo dell'errore
    per coprire entrambe le librerie.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "resource exhausted" in msg
        or "quota" in msg
        or "too many requests" in msg
    )

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
        corrected_parts: list[str] | None = None,
        on_retry: Callable[[int, int, float], None] | None = None,
        on_checkpoint: Callable[[list[str], int], None] | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 5.0,
    ) -> str:
        """Corregge un documento intero con chunking, retry e ripresa.

        Args:
            full_text: testo completo da correggere.
            chunker: istanza di TextChunker.
            on_progress: callback(chunk_corrente, totale_chunk).
            cancel_event: evento di cancellazione; se set, interrompe l'elaborazione.
            on_chunk: callback(testo_accumulato) per lo streaming.
            corrected_parts: lista (mutabile) dei chunk gia' corretti. Se fornita,
                la correzione RIPRENDE dal primo chunk non ancora presente e la
                lista viene aggiornata in-place cosi' che il chiamante conservi i
                progressi anche in caso di eccezione.
            on_retry: callback(chunk_1based, tentativo, attesa_sec) sui rate limit.
            on_checkpoint: callback(corrected_parts, totale) dopo ogni chunk
                completato, per persistere lo stato su disco.
            max_retries: numero massimo di ritentativi automatici sui rate limit.
            retry_base_delay: attesa base (s) del backoff esponenziale.

        Returns:
            Testo corretto riassemblato.

        Raises:
            InterruptedError: se l'operazione viene interrotta.
        """
        chunks = chunker.split(full_text)
        total = len(chunks)
        if corrected_parts is None:
            corrected_parts = []

        # Ripresa: salta i chunk gia' corretti in un turno precedente.
        start = min(len(corrected_parts), total)
        if start and on_progress:
            on_progress(start, total)
        if start and on_chunk:
            on_chunk("\n\n".join(corrected_parts))

        for i in range(start, total):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Correzione interrotta dall'utente.")
            corrected = self._correct_with_retry(
                chunks[i], i + 1, cancel_event, on_retry, max_retries, retry_base_delay
            )
            corrected_parts.append(corrected)
            if on_checkpoint:
                on_checkpoint(corrected_parts, total)
            if on_chunk:
                on_chunk("\n\n".join(corrected_parts))
            if on_progress:
                on_progress(i + 1, total)

        return "\n\n".join(corrected_parts)

    def _correct_with_retry(
        self,
        chunk: str,
        chunk_num: int,
        cancel_event: threading.Event | None,
        on_retry: Callable[[int, int, float], None] | None,
        max_retries: int,
        base_delay: float,
    ) -> str:
        """Corregge un chunk ritentando con backoff sui soli errori di rate limit."""
        attempt = 0
        while True:
            try:
                return self.correct(chunk)
            except Exception as exc:
                if attempt >= max_retries or not is_rate_limit_error(exc):
                    raise
                attempt += 1
                delay = base_delay * (2 ** (attempt - 1))
                if on_retry:
                    on_retry(chunk_num, attempt, delay)
                # Attesa interrompibile: non blocchiamo la cancellazione.
                waited = 0.0
                while waited < delay:
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError("Correzione interrotta dall'utente.")
                    step = min(0.5, delay - waited)
                    time.sleep(step)
                    waited += step


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


class UnslothEngine(LLMEngine):
    """Motore LLM che usa Unsloth Studio (server locale OpenAI-compatible).

    Unsloth Studio espone un backend FastAPI su http://127.0.0.1:8888 con
    endpoint OpenAI-compatible (/v1/chat/completions) protetto da Bearer token
    (chiavi nella forma ``sk-unsloth-...`` create dall'interfaccia di Studio).
    """

    def __init__(self, url: str = "http://127.0.0.1:8888", model: str = "",
                 api_key: str = "", reasoning: bool = False,
                 web_search: bool = False):
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.reasoning = reasoning
        self.web_search = web_search

    def correct(self, text_chunk: str) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Tetto di sicurezza per la generazione: l'output corretto ha circa la
        # lunghezza dell'input, quindi limitiamo i token per evitare che un
        # eventuale loop di ripetizione giri fino al timeout.
        max_tokens = max(256, len(text_chunk) // 2 + 256)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text_chunk},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            # Con reasoning OFF il modello non "pensa": evita che i modelli
            # reasoner girino a lungo senza emettere il testo entro il timeout
            # (analogo a think=False di Ollama). Attivabile per sperimentare.
            "enable_thinking": self.reasoning,
        }
        if self.web_search:
            payload["enable_tools"] = True
            payload["enabled_tools"] = ["web_search"]
        resp = requests.post(
            f"{self.url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        # Con reasoning attivo alcuni modelli inglobano il ragionamento in
        # blocchi <think>...</think> dentro il contenuto: li rimuoviamo perche'
        # il testo corretto non deve contenerli.
        if self.reasoning and "<think>" in content:
            content = re.sub(r"<think>.*?</think>", "", content,
                             flags=re.DOTALL).strip()
        return content


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
    elif provider == "unsloth":
        return UnslothEngine(
            url=config.get("unsloth_url", "http://127.0.0.1:8888"),
            model=config.get("unsloth_model", ""),
            api_key=config.get("unsloth_api_key", ""),
            reasoning=config.get("unsloth_reasoning", False),
            web_search=config.get("unsloth_web_search", False),
        )
    else:
        return OllamaEngine(
            url=config.get("ollama_url", "http://localhost:11434"),
            model=config.get("ollama_model", ""),
            api_key=config.get("ollama_api_key", ""),
            cloud=config.get("ollama_cloud", False),
        )
