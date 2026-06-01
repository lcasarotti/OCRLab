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
import subprocess
import sys
import tempfile
import threading
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

    def __init__(self, python_exe: str = ""):
        self._python_exe = _resolve_python_exe(python_exe, self._VENV_NAME)
        self._proc = None
        self._stderr_lines: list[str] = []
        self._last_html: str = ""
        self._last_blocks: list = []

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

        def _drain():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except Exception:
                pass

        threading.Thread(target=_drain, daemon=True).start()

        msg = None
        while msg is None:
            ready_line = proc.stdout.readline()
            if not ready_line:
                proc.wait()
                stderr = "".join(self._stderr_lines)
                raise RuntimeError(f"Il worker Surya non ha risposto.\n{stderr[:500]}")
            ready_line = ready_line.strip()
            if not ready_line:
                continue
            try:
                msg = json.loads(ready_line)
            except json.JSONDecodeError:
                continue
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "Errore sconosciuto nel worker"))
        self._proc = proc

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

        Restituisce (text, angle, html, blocks). html e blocks sono stringhe/liste
        vuote per worker 0.17.x che non li producono.
        """
        cmd = json.dumps({"path": img_path, "forced_angle": forced_angle})
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        msg = None
        while msg is None:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Il worker Surya ha smesso di rispondere.")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "Errore nel worker"))
        return msg.get("text", ""), msg.get("angle"), msg.get("html", ""), msg.get("blocks", [])

    def _subprocess_image(self, file_path, proc, on_progress) -> str:
        text, _, html, blocks = self._send_image(proc, file_path, None)
        self._last_html = html
        self._last_blocks = [blocks] if blocks else []
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
        doc_angle: Optional[int] = None

        try:
            for i, page in enumerate(doc):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("OCR interrotto dall'utente.")

                pix = page.get_pixmap(dpi=self._PDF_DPI)
                img_path = os.path.join(tmp_dir, f"page_{i:04d}.png")
                pix.save(img_path)

                text, angle, html, blocks = self._send_image(proc, img_path, doc_angle)
                if doc_angle is None and angle is not None:
                    doc_angle = angle
                pages_text.append(text.strip())
                pages_html.append(html)
                pages_blocks.append(blocks)

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
