"""Surya OCR worker — script standalone eseguito dal Python esterno.

Protocollo stdin/stdout (una riga JSON per messaggio):
  IN:   {"path": "<path_immagine>", "forced_angle": null | int}
  IN:   {"quit": true}
  OUT:  {"type": "ready"}
  OUT:  {"type": "result", "text": "<testo>", "angle": null | int}
  OUT:  {"type": "error",  "message": "<messaggio>"}
"""

import io
import json
import os
import sys
from typing import Optional

from PIL import Image


# ---------------------------------------------------------------------------
# Patch compatibilità surya 0.17.x con transformers 5.x
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


# ---------------------------------------------------------------------------
# Utilità geometriche
# ---------------------------------------------------------------------------

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
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or
                0x3400 <= cp <= 0x4DBF or
                0xAC00 <= cp <= 0xD7AF or
                0x3040 <= cp <= 0x30FF or
                0x0600 <= cp <= 0x06FF or
                0x0590 <= cp <= 0x05FF):
            return True
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


# ---------------------------------------------------------------------------
# OCR di una singola immagine
# ---------------------------------------------------------------------------

def _ocr_page(
    img: Image.Image,
    det_pred,
    rec_pred,
    layout_pred,
    forced_angle: Optional[int] = None,
) -> tuple:
    """Restituisce (text, angle_applied)."""
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
        if layout_preds and layout_preds[0].bboxes
        else []
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
                if layout_preds and layout_preds[0].bboxes
                else []
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
                if layout_preds and layout_preds[0].bboxes
                else []
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
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Sceglie il device: MPS su Apple Silicon, CUDA se disponibile E funzionante, CPU altrimenti.
    try:
        import torch
        if torch.backends.mps.is_available():
            os.environ.setdefault("TORCH_DEVICE", "mps")
        elif torch.cuda.is_available():
            try:
                t = torch.ones(4, 4).cuda()
                torch.matmul(t, t)  # esegue un kernel reale per verificare la compatibilità
                os.environ.setdefault("TORCH_DEVICE", "cuda")
            except Exception:
                os.environ.setdefault("TORCH_DEVICE", "cpu")
        else:
            os.environ.setdefault("TORCH_DEVICE", "cpu")
    except Exception:
        os.environ.setdefault("TORCH_DEVICE", "cpu")

    _patch_surya_transformers5()

    try:
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.layout import LayoutPredictor
        from surya.recognition import RecognitionPredictor
        from surya.settings import settings

        foundation_rec = FoundationPredictor(checkpoint=settings.FOUNDATION_MODEL_CHECKPOINT)
        foundation_layout = FoundationPredictor(checkpoint=settings.LAYOUT_MODEL_CHECKPOINT)
        det_pred = DetectionPredictor()
        rec_pred = RecognitionPredictor(foundation_rec)
        layout_pred = LayoutPredictor(foundation_layout)
    except Exception as e:
        print(json.dumps({"type": "error", "message": f"Inizializzazione Surya fallita: {e}"}), flush=True)
        sys.exit(1)

    print(json.dumps({"type": "ready"}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue

        if cmd.get("quit"):
            break

        path = cmd.get("path", "")
        forced_angle = cmd.get("forced_angle")  # None o int

        try:
            img = Image.open(path).convert("RGB")
            text, angle = _ocr_page(img, det_pred, rec_pred, layout_pred,
                                    forced_angle=forced_angle)
            print(json.dumps({"type": "result", "text": text, "angle": angle}), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)


if __name__ == "__main__":
    main()
