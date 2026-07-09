"""Motore OCR tramite Surya (PyTorch).

Quando l'app gira come eseguibile PyInstaller, Surya non è bundled.
SuryaEngine usa un subprocess nel Python esterno (configurato nelle
impostazioni) per evitare conflitti tra i due ambienti Python.

Quando l'app gira da sorgente con surya già installato nello stesso venv,
SuryaEngine usa l'import diretto (modalità senza subprocess).
"""

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Optional

try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image

_predictors: dict = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Utilità per la modalità diretta (import surya nello stesso processo)
# ---------------------------------------------------------------------------

def _patch_surya_transformers5():
    try:
        import transformers
        if int(transformers.__version__.split(".")[0]) < 5:
            return
    except Exception:
        return

    if getattr(_patch_surya_transformers5, "_applied", False):
        return
    _patch_surya_transformers5._applied = True

    try:
        import torch
        from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS
        if "default" not in ROPE_INIT_FUNCTIONS:
            def _default_rope_init(config, device=None, seq_len=None, **kwargs):
                base = getattr(config, "rope_theta", 10000.0)
                partial = getattr(config, "partial_rotary_factor", 1.0)
                head_dim = getattr(
                    config, "head_dim",
                    config.hidden_size // config.num_attention_heads,
                )
                dim = int(head_dim * partial)
                inv_freq = 1.0 / (
                    base ** (
                        torch.arange(0, dim, 2, dtype=torch.int64)
                        .float()
                        .to(device) / dim
                    )
                )
                return inv_freq, 1.0
            ROPE_INIT_FUNCTIONS["default"] = _default_rope_init
    except Exception:
        pass

    try:
        from surya.common.surya.decoder.config import SuryaDecoderConfig
        from surya.common.surya.config import SuryaModelConfig

        _orig_dec_init = SuryaDecoderConfig.__init__

        def _dec_init(self, *args, **kwargs):
            _orig_dec_init(self, *args, **kwargs)
            if not hasattr(self, "pad_token_id"):
                self.pad_token_id = None

        SuryaDecoderConfig.__init__ = _dec_init

        _orig_mc_init = SuryaModelConfig.__init__

        def _mc_init(self, *args, **kwargs):
            _orig_mc_init(self, *args, **kwargs)
            if hasattr(self, "decoder") and hasattr(self, "pad_token_id"):
                self.decoder.pad_token_id = self.pad_token_id

        SuryaModelConfig.__init__ = _mc_init
    except Exception:
        pass


def is_available() -> bool:
    try:
        import surya  # noqa: F401
        return True
    except ImportError:
        return False


def _check():
    if not is_available():
        raise ImportError(
            "Il motore Surya non è disponibile in questa versione dell'applicazione.\n"
            "Surya richiede PyTorch, che dipende dalla scheda grafica del computer.\n"
            "Per usare Surya, consulta il manuale per le istruzioni di installazione."
        )


def _get_predictors() -> tuple:
    # Sceglie il device: MPS su Apple Silicon, CUDA se disponibile, CPU altrimenti.
    try:
        import torch
        if torch.backends.mps.is_available():
            os.environ.setdefault("TORCH_DEVICE", "mps")
        elif not torch.cuda.is_available():
            os.environ.setdefault("TORCH_DEVICE", "cpu")
    except Exception:
        os.environ.setdefault("TORCH_DEVICE", "cpu")

    _patch_surya_transformers5()
    with _lock:
        if not _predictors:
            from surya.detection import DetectionPredictor
            from surya.foundation import FoundationPredictor
            from surya.layout import LayoutPredictor
            from surya.recognition import RecognitionPredictor
            from surya.settings import settings

            foundation_rec = FoundationPredictor(
                checkpoint=settings.FOUNDATION_MODEL_CHECKPOINT
            )
            foundation_layout = FoundationPredictor(
                checkpoint=settings.LAYOUT_MODEL_CHECKPOINT
            )
            _predictors["det"] = DetectionPredictor()
            _predictors["rec"] = RecognitionPredictor(foundation_rec)
            _predictors["layout"] = LayoutPredictor(foundation_layout)
    return _predictors["det"], _predictors["rec"], _predictors["layout"]


def _poly_bbox(polygon) -> tuple:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _sort_blocks_reading_order(blocks, img_width: int):
    """Ordina layout block in ordine di lettura con rilevamento colonne.

    Caso normale (colonna singola o layout non riconoscibile): restituisce
    i blocchi nell'ordine di lettura predetto da Surya (b.position), che è
    affidabile per la grande maggioranza dei documenti.

    Layout bicolonna reale: se esistono almeno 2 blocchi su ciascun lato
    del centro, c'è un gap orizzontale ≥ 4% della larghezza immagine tra
    le due colonne, E i blocchi sono mediamente stretti (< 55% della
    larghezza pagina), allora: prima tutti i blocchi sinistri (per y) poi
    quelli destri (per y). Questo sovrascrive b.position, che Surya
    calcola male sui layout bicolonna di certi documenti scansionati.
    """
    if not blocks:
        return []
    bboxes = [_poly_bbox(b.polygon) for b in blocks]
    n = len(bboxes)
    mid = img_width / 2
    x_centers = [(bx1 + bx2) / 2 for bx1, _, bx2, _ in bboxes]
    left_idx  = [i for i in range(n) if x_centers[i] < mid]
    right_idx = [i for i in range(n) if x_centers[i] >= mid]
    if len(left_idx) >= 2 and len(right_idx) >= 2:
        left_max_x  = max(bboxes[i][2] for i in left_idx)
        right_min_x = min(bboxes[i][0] for i in right_idx)
        gap_ok = right_min_x - left_max_x > img_width * 0.04
        avg_width = sum(bboxes[i][2] - bboxes[i][0] for i in range(n)) / n
        narrow_ok = avg_width < img_width * 0.55
        if gap_ok and narrow_ok:
            left_sorted  = sorted(left_idx,  key=lambda i: bboxes[i][1])
            right_sorted = sorted(right_idx, key=lambda i: bboxes[i][1])
            return [blocks[i] for i in left_sorted + right_sorted]
    # Non è bicolonna: rispetta l'ordinamento di lettura predetto da Surya.
    return sorted(blocks, key=lambda b: b.position)


# Parole inglesi che non compaiono mai nel testo italiano/francese/tedesco accademico.
# Usate per rilevare righe-rumore (allucinazioni di Surya su zone bianche o grafiche).
_EN_ONLY_WORDS = frozenset({
    'the', 'and', 'or', 'for', 'nor',
    'her', 'his', 'their', 'your', 'its',
    'see', 'continued', 'continue',
    'street', 'avenue', 'road', 'lane', 'court',
    'property', 'compression', 'personal',
    'section', 'chapter', 'public', 'private',
    'department', 'building',
})


def _is_noise_line(text: str) -> bool:
    """True se la riga è probabilmente un'allucinazione di Surya.

    Filtra due categorie:
    1. Righe con caratteri di script non-europeo (CJK, arabo, ebraico, ecc.)
    2. Righe brevi (≤6 parole), tutte maiuscole, senza caratteri accentati,
       contenenti almeno una parola esclusivamente inglese.
    """
    # Categoria 1: script non-europeo
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
                0x3400 <= cp <= 0x4DBF or   # CJK Extension A
                0xAC00 <= cp <= 0xD7AF or   # Hangul
                0x3040 <= cp <= 0x30FF or   # Hiragana + Katakana
                0x0600 <= cp <= 0x06FF or   # Arabo
                0x0590 <= cp <= 0x05FF):    # Ebraico
            return True

    # Categoria 2: breve, ALL-CAPS, nessun accento, parola inglese "esclusiva"
    words = text.split()
    if 1 <= len(words) <= 6:
        letters = [c for c in text if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            if not any(c in 'àáâãäèéêëìíîïòóôõöùúûüýÿçñ' for c in text.lower()):
                tokens = {w.strip('.,;:!?()[]«»"\'').lower() for w in words}
                if tokens & _EN_ONLY_WORDS:
                    return True

    return False


def _center_in_bbox(line_poly, block_poly) -> bool:
    lx1, ly1, lx2, ly2 = _poly_bbox(line_poly)
    bx1, by1, bx2, by2 = _poly_bbox(block_poly)
    cx = (lx1 + lx2) / 2
    cy = (ly1 + ly2) / 2
    return bx1 <= cx <= bx2 and by1 <= cy <= by2


def _ocr_page(img, det_pred, rec_pred, layout_pred,
              forced_angle: Optional[int] = None) -> tuple:
    # Doppia pagina affiancata (landscape): divide a metà e processa ciascuna metà
    # in modo indipendente, così Surya riceve ogni pagina come immagine portrait.
    if forced_angle is None and img.size[0] > img.size[1] * 1.2:
        mid = img.size[0] // 2
        left_text,  _ = _ocr_page(img.crop((0, 0, mid, img.size[1])),
                                   det_pred, rec_pred, layout_pred)
        right_text, _ = _ocr_page(img.crop((mid, 0, img.size[0], img.size[1])),
                                   det_pred, rec_pred, layout_pred)
        combined = "\n\n".join(t for t in (left_text, right_text) if t)
        return combined, None

    layout_preds = layout_pred([img])
    layout_blocks = (
        _sort_blocks_reading_order(layout_preds[0].bboxes, img.size[0])
        if layout_preds and layout_preds[0].bboxes else []
    )

    angle_applied: Optional[int] = None

    if forced_angle is not None:
        if forced_angle != 0:
            img = img.rotate(forced_angle, expand=True)
            angle_applied = forced_angle
            # Dopo rotazione, se l'immagine è diventata landscape è un doppio foglio
            # affiancato: divide a metà e processa ciascuna metà in modo indipendente.
            if img.size[0] > img.size[1] * 1.2:
                mid = img.size[0] // 2
                left_text,  _ = _ocr_page(img.crop((0, 0, mid, img.size[1])),
                                           det_pred, rec_pred, layout_pred, forced_angle=0)
                right_text, _ = _ocr_page(img.crop((mid, 0, img.size[0], img.size[1])),
                                           det_pred, rec_pred, layout_pred, forced_angle=0)
                combined = "\n\n".join(t for t in (left_text, right_text) if t)
                return combined, angle_applied
            layout_preds = layout_pred([img])
            layout_blocks = (
                sorted(layout_preds[0].bboxes, key=lambda b: b.position)
                if layout_preds and layout_preds[0].bboxes else []
            )
    else:
        if layout_blocks:
            bboxes = [_poly_bbox(b.polygon) for b in layout_blocks]
            avg_bxe = sum(bx2 - bx1 for bx1, _, bx2, _ in bboxes) / len(bboxes)
            avg_bye = sum(by2 - by1 for _, by1, _, by2 in bboxes) / len(bboxes)
            text_is_rotated = avg_bxe < avg_bye * 0.5
        else:
            text_is_rotated = False

        if text_is_rotated:
            x_ascending = False
            if len(layout_blocks) >= 2:
                xs = [_poly_bbox(b.polygon)[0] for b in layout_blocks]
                n = len(xs)
                mean_i = (n - 1) / 2.0
                mean_x = sum(xs) / n
                cov = sum((i - mean_i) * (xs[i] - mean_x) for i in range(n))
                x_ascending = cov > 0
            angle = 90 if not x_ascending else -90
            img = img.rotate(angle, expand=True)
            angle_applied = angle
            # Dopo rotazione, se l'immagine è diventata landscape è un doppio foglio
            # affiancato: divide a metà e processa ciascuna metà in modo indipendente.
            if img.size[0] > img.size[1] * 1.2:
                mid = img.size[0] // 2
                left_text,  _ = _ocr_page(img.crop((0, 0, mid, img.size[1])),
                                           det_pred, rec_pred, layout_pred, forced_angle=0)
                right_text, _ = _ocr_page(img.crop((mid, 0, img.size[0], img.size[1])),
                                           det_pred, rec_pred, layout_pred, forced_angle=0)
                combined = "\n\n".join(t for t in (left_text, right_text) if t)
                return combined, angle_applied
            layout_preds = layout_pred([img])
            layout_blocks = (
                sorted(layout_preds[0].bboxes, key=lambda b: b.position)
                if layout_preds and layout_preds[0].bboxes else []
            )

    rec_preds = rec_pred([img], det_predictor=det_pred)
    text_lines = [l for l in (rec_preds[0].text_lines if rec_preds else []) if l.text.strip()]
    text_lines = [l for l in text_lines if not _is_noise_line(l.text)]

    if not text_lines:
        return "", angle_applied

    y_extents = [_poly_bbox(l.polygon)[3] - _poly_bbox(l.polygon)[1] for l in text_lines]
    avg_h = (sum(y_extents) / len(y_extents)) if y_extents else 20
    if avg_h <= 0:
        avg_h = 20
    sort_key = lambda l: (_poly_bbox(l.polygon)[1], _poly_bbox(l.polygon)[0])

    if angle_applied is not None and len(text_lines) >= 4:
        W_img = img.size[0]
        contained = sum(
            1 for l in text_lines
            if _poly_bbox(l.polygon)[2] < W_img / 2
            or _poly_bbox(l.polygon)[0] >= W_img / 2
        )
        if contained / len(text_lines) > 0.75:
            def _page_text(lines):
                if not lines:
                    return ""
                lines = sorted(lines, key=sort_key)
                parts = [lines[0].text]
                for i in range(1, len(lines)):
                    _, _, _, ly2_p = _poly_bbox(lines[i - 1].polygon)
                    _, ly1_c, _, _ = _poly_bbox(lines[i].polygon)
                    gap = ly1_c - ly2_p
                    sep = "\n\n" if gap > avg_h * 1.5 else "\n"
                    parts.append(sep + lines[i].text)
                return "".join(parts)

            left  = [l for l in text_lines
                     if (_poly_bbox(l.polygon)[0] + _poly_bbox(l.polygon)[2]) / 2 < W_img / 2]
            right = [l for l in text_lines
                     if (_poly_bbox(l.polygon)[0] + _poly_bbox(l.polygon)[2]) / 2 >= W_img / 2]
            combined = "\n\n".join(t for t in (_page_text(left), _page_text(right)) if t)
            return combined, angle_applied

    if not layout_blocks:
        text_lines.sort(key=sort_key)
        parts = [text_lines[0].text]
        for i in range(1, len(text_lines)):
            _, _, _, ly2_p = _poly_bbox(text_lines[i - 1].polygon)
            _, ly1_c, _, _ = _poly_bbox(text_lines[i].polygon)
            gap = ly1_c - ly2_p
            sep = "\n\n" if gap > avg_h * 1.5 else "\n"
            parts.append(sep + text_lines[i].text)
        return "".join(parts), angle_applied

    block_lines: dict = {i: [] for i in range(len(layout_blocks))}
    unassigned = []
    for line in text_lines:
        matched = False
        for i, block in enumerate(layout_blocks):
            if _center_in_bbox(line.polygon, block.polygon):
                block_lines[i].append(line)
                matched = True
                break
        if not matched:
            unassigned.append(line)

    parts = []
    prev_y2: Optional[float] = None

    for i in range(len(layout_blocks)):
        lines = sorted(block_lines[i], key=sort_key)
        for line in lines:
            text = line.text.strip()
            if not text:
                continue
            _, ly1, _, ly2 = _poly_bbox(line.polygon)
            if parts and prev_y2 is not None:
                gap = ly1 - prev_y2
                sep = "\n\n" if gap > avg_h * 1.5 else "\n"
                parts.append(sep + text)
            else:
                parts.append(text)
            prev_y2 = ly2

    for line in sorted(unassigned, key=sort_key):
        parts.append("\n" + line.text.strip())

    return "".join(parts), angle_applied


# ---------------------------------------------------------------------------
# Motore principale
# ---------------------------------------------------------------------------

def _resolve_python_exe(python_exe: str, venv_name: str = "surya-venv") -> str:
    """Risolve il percorso Python da usare per il worker Surya.

    Priorità: 1) percorso esplicito in config, 2) venv standard per piattaforma,
    3) stringa vuota (import diretto, solo se surya è nello stesso venv).
    """
    if python_exe:
        return python_exe
    mac_path = os.path.expanduser(
        f"~/Library/Application Support/OCRLab/{venv_name}/bin/python"
    )
    win_path = os.path.join(
        os.environ.get("APPDATA", ""), "OCRLab", venv_name, "Scripts", "python.exe"
    )
    if sys.platform == "darwin" and os.path.isfile(mac_path):
        return mac_path
    if sys.platform == "win32" and os.path.isfile(win_path):
        return win_path
    return ""


class SuryaEngine:
    """Motore OCR Surya.

    Se python_exe è fornito (o rilevato automaticamente nel percorso standard),
    esegue il worker Surya in un subprocess separato (evita conflitti tra ambienti
    Python diversi). Altrimenti, importa surya direttamente (modalità sorgente).

    Le sottoclassi possono sovrascrivere _VENV_NAME e _WORKER_NAME per puntare a
    venv e worker diversi (es. Surya 0.20).
    """

    _VENV_NAME = "surya-venv"
    _WORKER_NAME = "surya_worker.py"
    _PDF_DPI = 150

    # Watchdog: budget di tempo (secondi) per la risposta del worker.
    # _READY_TIMEOUT_S copre l'avvio (caricamento modelli + warm-up).
    # _PAGE_TIMEOUT_S è il budget per pagina; il budget totale di un batch
    # è max(_MIN_IPC_TIMEOUT_S, _PAGE_TIMEOUT_S * n_pagine). Se un throttling
    # o un crash della GPU blocca il server d'inferenza, allo scadere il worker
    # viene riavviato e il batch ritentato una volta (vedi _send_with_retry).
    _READY_TIMEOUT_S = 600
    _PAGE_TIMEOUT_S = 120
    _MIN_IPC_TIMEOUT_S = 240

    # Rilevamento degrado GPU, a due livelli:
    #  - _EMPTY_STALL_PAGES: pagine "sospette vuote" consecutive (layout con
    #    testo ma riconoscimento vuoto). Segnale preciso e veloce; le pagine
    #    legittimamente senza testo (bianche o sole illustrazioni) NON sono
    #    sospette e non contano, quindi niente falsi allarmi.
    #  - _EMPTY_STALL_HARD: pagine interamente vuote consecutive (a prescindere
    #    da suspect), dopo aver già prodotto testo. Rete di sicurezza per il
    #    degrado totale in cui anche il layout tace: soglia alta perché una lunga
    #    sequenza di sole tavole è rara ma possibile.
    # In entrambi i casi: riavvio del worker e, se il degrado persiste oltre
    # _MAX_RESTARTS riavvii, notifica all'utente e interruzione.
    _EMPTY_STALL_PAGES = 3
    _EMPTY_STALL_HARD = 30
    _MAX_RESTARTS = 3

    def __init__(self, python_exe: str = ""):
        self._python_exe = _resolve_python_exe(python_exe, self._VENV_NAME)
        self._proc = None
        self._stderr_lines: list[str] = []
        self._stdout_q: "queue.Queue" = queue.Queue()
        self._last_html: str = ""
        self._last_blocks: list = []
        self._last_angles: list = []
        self._supports_batch: bool = False

    def _ipc_timeout(self, n_pages: int) -> float:
        """Budget di tempo per la risposta del worker a un batch di n pagine."""
        return max(self._MIN_IPC_TIMEOUT_S, self._PAGE_TIMEOUT_S * max(1, n_pages))

    def _get_worker_path(self) -> str:
        """Percorso del worker script per questo engine."""
        if getattr(sys, "frozen", False):
            return os.path.join(sys._MEIPASS, self._WORKER_NAME)
        return os.path.join(os.path.dirname(__file__), self._WORKER_NAME)

    def _build_worker_env(self) -> dict:
        worker_env = os.environ.copy()
        for _var in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
            worker_env.pop(_var, None)
        if hasattr(sys, "_MEIPASS"):
            _mei = os.path.normcase(sys._MEIPASS)
            _path_parts = [
                p for p in worker_env.get("PATH", "").split(os.pathsep)
                if os.path.normcase(p) != _mei
            ]
            worker_env["PATH"] = os.pathsep.join(_path_parts)
        if sys.platform == "darwin":
            _extra = ["/opt/homebrew/bin", "/usr/local/bin"]
            _current = worker_env.get("PATH", "").split(os.pathsep)
            _additions = [p for p in _extra if p not in _current]
            if _additions:
                worker_env["PATH"] = os.pathsep.join(_additions + _current)
        return worker_env

    def start(self) -> None:
        """Avvia il subprocess worker e attende "ready". Idempotente se già in esecuzione."""
        if self.is_running():
            return
        worker = self._get_worker_path()
        if not os.path.isfile(worker):
            raise FileNotFoundError(f"Worker Surya non trovato: {worker}")

        self._stderr_lines = []
        self._stdout_q = queue.Queue()
        proc = subprocess.Popen(
            [self._python_exe, worker],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=self._build_worker_env(),
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        )

        stderr_lines = self._stderr_lines
        stdout_q = self._stdout_q

        def _drain():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except Exception:
                pass

        def _read_stdout():
            # Legge lo stdout del worker in background e accoda le righe.
            # Un sentinella None segnala la chiusura dello stream (EOF).
            try:
                for line in proc.stdout:
                    stdout_q.put(line)
            except Exception:
                pass
            finally:
                stdout_q.put(None)

        threading.Thread(target=_drain, daemon=True).start()
        threading.Thread(target=_read_stdout, daemon=True).start()

        # L'assegnazione a self._proc è necessaria perché _next_message legga
        # dalla coda di questo proc; se il ready non arriva, azzeriamo di nuovo.
        self._proc = proc
        try:
            msg = self._next_message(self._READY_TIMEOUT_S)
        except RuntimeError:
            self._proc = None
            try:
                proc.kill()
            except Exception:
                pass
            raise
        if msg.get("type") == "error":
            self._proc = None
            raise RuntimeError(msg.get("message", "Errore sconosciuto nel worker"))
        self._supports_batch = bool(msg.get("batch"))

    def _next_message(self, timeout: float) -> dict:
        """Legge il prossimo messaggio JSON dallo stdout del worker.

        Attende al massimo `timeout` secondi complessivi. Le righe vuote o non
        JSON (es. warning stampati dal server d'inferenza) vengono ignorate
        senza azzerare il budget. Solleva RuntimeError se il worker chiude lo
        stream (EOF/crash) o non risponde entro il timeout (blocco della GPU):
        in entrambi i casi il messaggio include la coda dello stderr per la
        diagnosi.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stderr = "".join(self._stderr_lines[-20:])
                raise RuntimeError(
                    "Il worker Surya non risponde da "
                    f"{int(timeout)} s (possibile blocco della GPU).\n{stderr[-500:]}"
                )
            try:
                line = self._stdout_q.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                stderr = "".join(self._stderr_lines[-20:])
                raise RuntimeError(
                    f"Il worker Surya ha smesso di rispondere.\n{stderr[-500:]}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def _restart_worker(self) -> None:
        """Riavvia il worker dopo un crash/blocco del server d'inferenza.

        stop() + start() ricaricano i modelli (costoso) ma è l'unico modo per
        recuperare da un crash della GPU. Aggiorna self._proc.
        """
        self.stop()
        self.start()

    def _send_with_retry(self, send_fn, cancel_event):
        """Esegue send_fn(self._proc); se il worker crasha o va in timeout,
        riavvia il worker e ritenta una sola volta.

        send_fn riceve il proc corrente e deve sollevare RuntimeError/OSError se
        la comunicazione fallisce. Un secondo fallimento propaga l'eccezione.
        """
        try:
            return send_fn(self._proc)
        except (RuntimeError, OSError):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("OCR interrotto dall'utente.")
            self._restart_worker()
            return send_fn(self._proc)

    def stop(self) -> None:
        """Ferma il subprocess worker."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.stdin.write(json.dumps({"quit": True}) + "\n")
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def is_running(self) -> bool:
        """True se il subprocess worker è attivo."""
        return self._proc is not None and self._proc.poll() is None

    def _run_ocr(self, proc, file_path: str, on_progress, cancel_event, on_partial=None) -> str:
        """Esegue OCR su file usando il proc già avviato."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._subprocess_pdf(file_path, proc, on_progress, cancel_event, on_partial)
        return self._subprocess_image(file_path, proc, on_progress)

    def process(
        self,
        file_path: str,
        on_progress: Optional[Callable] = None,
        cancel_event=None,
        on_partial: Optional[Callable] = None,
    ) -> str:
        if self._python_exe:
            return self._process_subprocess(file_path, on_progress, cancel_event, on_partial)
        _check()
        det_pred, rec_pred, layout_pred = _get_predictors()
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._process_pdf_direct(
                file_path, det_pred, rec_pred, layout_pred, on_progress, cancel_event, on_partial
            )
        return self._process_image_direct(
            file_path, det_pred, rec_pred, layout_pred, on_progress
        )

    # ------------------------------------------------------------------
    # Modalità subprocess
    # ------------------------------------------------------------------

    def _process_subprocess(self, file_path, on_progress, cancel_event, on_partial=None) -> str:
        """Esegue l'OCR nel Python esterno tramite il worker script.

        Se il subprocess è già in esecuzione (daemon), lo riusa senza fermarlo.
        Se lo avvia qui, lo ferma al termine (anche in caso di eccezione).
        """
        owns_proc = not self.is_running()
        if owns_proc:
            self.start()
        try:
            result = self._run_ocr(self._proc, file_path, on_progress, cancel_event, on_partial)
        except Exception:
            self.stop()
            raise
        if owns_proc:
            self.stop()
        return result

    def _send_image(self, proc, img_path: str, forced_angle) -> tuple:
        """Invia un'immagine al worker e attende il risultato.

        Restituisce (text, angle, html, blocks, suspect). html/blocks sono
        vuoti per worker 0.17.x; suspect (pagina vuota ma layout con testo →
        degrado GPU) è False se il worker non lo emette.
        """
        cmd = json.dumps({"path": img_path, "forced_angle": forced_angle})
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        msg = self._next_message(self._ipc_timeout(1))
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "Errore nel worker"))
        return (msg.get("text", ""), msg.get("angle"), msg.get("html", ""),
                msg.get("blocks", []), bool(msg.get("suspect", False)))

    def _send_batch(self, proc, paths: list, forced_angle,
                    forced_angles: Optional[list] = None) -> list:
        """Invia un batch di immagini al worker e attende i risultati.

        Restituisce una lista di (text, angle, html, blocks, suspect), una per
        path, nello stesso ordine di input. `forced_angles`, se fornito, dà un
        angolo di rotazione per pagina (allineato a `paths`); ha precedenza su
        `forced_angle`, usato come valore unico per tutte le pagine.
        """
        paths = list(paths)
        payload = {"paths": paths, "forced_angle": forced_angle}
        if forced_angles is not None:
            payload["forced_angles"] = list(forced_angles)
        cmd = json.dumps(payload)
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        msg = self._next_message(self._ipc_timeout(len(paths)))
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "Errore nel worker"))
        items = msg.get("items", [])
        return [
            (it.get("text", ""), it.get("angle"), it.get("html", ""),
             it.get("blocks", []), bool(it.get("suspect", False)))
            for it in items
        ]

    def _maybe_recover_degraded(self, results, resend_fn, cancel_event, state,
                                pages_done):
        """Rileva il degrado della GPU (pagine vuote in serie) e tenta il recupero.

        `results` è la lista di tuple con `suspect` (bool) come ultimo elemento e
        `text` come primo. `resend_fn(proc)` ri-invia lo stesso batch. `state` è
        un dict con chiavi "suspect", "empty", "restarts", "seen_text" mantenute
        tra le chiamate.

        Due livelli (vedi costanti di classe): un batch interamente "sospetto
        vuoto" (soglia bassa, preciso) oppure una lunga serie di pagine vuote pur
        avendo già prodotto testo (soglia alta, catch-all). Al superamento di una
        soglia riavvia il worker e ritenta il batch; se il degrado persiste oltre
        _MAX_RESTARTS riavvii solleva RuntimeError, così l'utente viene avvisato
        invece di ricevere pagine vuote in silenzio. Restituisce i risultati
        (eventualmente sostituiti dal retry andato a buon fine).
        """
        texts = [r[0].strip() for r in results]
        if any(texts):
            state["seen_text"] = True

        if results and all(r[-1] for r in results):
            state["suspect"] += len(results)
        else:
            state["suspect"] = 0
        if results and state.get("seen_text") and not any(texts):
            state["empty"] += len(results)
        else:
            state["empty"] = 0

        tripped = (state["suspect"] >= self._EMPTY_STALL_PAGES
                   or state["empty"] >= self._EMPTY_STALL_HARD)
        if not tripped:
            return results

        if state["restarts"] >= self._MAX_RESTARTS:
            raise RuntimeError(
                "Il motore Surya continua a restituire pagine vuote dopo "
                f"{state['restarts']} riavvii (pagine completate: {pages_done}): "
                "probabile degrado o throttling della GPU. Interrompi e riprova; "
                "se il problema persiste riduci il batch o riavvia l'applicazione."
            )
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("OCR interrotto dall'utente.")
        state["restarts"] += 1
        self._restart_worker()
        results = resend_fn(self._proc)
        if any(r[0].strip() for r in results):
            state["suspect"] = 0
            state["empty"] = 0
            state["seen_text"] = True
        return results

    def _subprocess_image(self, file_path, proc, on_progress) -> str:
        text, angle, html, blocks, _suspect = self._send_image(proc, file_path, None)
        self._last_html = html
        self._last_blocks = [blocks] if blocks else []
        # Angolo ORARIO con cui il writer del PDF deve raddrizzare l'immagine per
        # combaciare col testo OCR. Il worker ruota in senso ANTIORARIO (PIL) di
        # `angle`; il writer usa la convenzione oraria. None se non ruotata (il
        # writer si autorileva via OSD, come prima).
        self._last_angles = [(360 - angle) % 360 if angle else None]
        if on_progress:
            on_progress(1, 1)
        return text.strip()

    def _subprocess_pdf(self, file_path, proc, on_progress, cancel_event, on_partial=None) -> str:
        doc = fitz.open(file_path)
        total = len(doc)
        tmp_dir = tempfile.mkdtemp(prefix="surya_ocr_")
        pages_text = []
        pages_html = []
        pages_blocks = []
        pages_angles: list = []
        doc_angle: Optional[int] = None
        deg_state = {"suspect": 0, "empty": 0, "restarts": 0, "seen_text": False}

        try:
            for i, page in enumerate(doc):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("OCR interrotto dall'utente.")

                pix = page.get_pixmap(dpi=self._PDF_DPI)
                img_path = os.path.join(tmp_dir, f"page_{i:04d}.png")
                pix.save(img_path)

                send_one = lambda p, ip=img_path, da=doc_angle: [
                    self._send_image(p, ip, da)
                ]
                results = self._send_with_retry(send_one, cancel_event)
                results = self._maybe_recover_degraded(
                    results, send_one, cancel_event, deg_state, len(pages_text)
                )
                text, angle, html, blocks, _suspect = results[0]
                if doc_angle is None and angle is not None:
                    doc_angle = angle
                pages_text.append(text.strip())
                pages_html.append(html)
                pages_blocks.append(blocks)
                # Angolo ORARIO per il writer (conversione dall'antiorario PIL
                # del worker); None sulle pagine non ruotate → autorilevazione OSD.
                pages_angles.append((360 - angle) % 360 if angle else None)

                if on_partial:
                    on_partial("\f".join(pages_text))
                if on_progress:
                    on_progress(i + 1, total)
        finally:
            doc.close()
            # Pulizia file temporanei
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        self._last_html = '<hr class="page-break">\n'.join(pages_html)
        self._last_blocks = pages_blocks
        self._last_angles = pages_angles
        return "\f".join(pages_text)

    # ------------------------------------------------------------------
    # Modalità diretta (stesso processo, surya nello stesso venv)
    # ------------------------------------------------------------------

    def _process_image_direct(self, file_path, det_pred, rec_pred, layout_pred, on_progress):
        img = Image.open(file_path).convert("RGB")
        text, _ = _ocr_page(img, det_pred, rec_pred, layout_pred)
        if on_progress:
            on_progress(1, 1)
        return text.strip()

    def _process_pdf_direct(self, file_path, det_pred, rec_pred, layout_pred,
                            on_progress, cancel_event, on_partial=None):
        doc = fitz.open(file_path)
        total = len(doc)
        pages_text = []
        doc_angle: Optional[int] = None

        for i, page in enumerate(doc):
            if cancel_event and cancel_event.is_set():
                doc.close()
                raise InterruptedError("OCR interrotto dall'utente.")
            pix = page.get_pixmap(dpi=150)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            text, angle = _ocr_page(img, det_pred, rec_pred, layout_pred,
                                    forced_angle=doc_angle)
            if doc_angle is None and angle is not None:
                doc_angle = angle
            pages_text.append(text.strip())
            if on_partial:
                on_partial("\f".join(pages_text))
            if on_progress:
                on_progress(i + 1, total)

        doc.close()

        return "\f".join(pages_text)
