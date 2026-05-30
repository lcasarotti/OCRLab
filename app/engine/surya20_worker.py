"""Surya 0.20 OCR worker — script standalone eseguito dal Python esterno.

Protocollo stdin/stdout (una riga JSON per messaggio):
  IN:   {"path": "<path_immagine>", "forced_angle": null | int}
  IN:   {"quit": true}
  OUT:  {"type": "ready"}
  OUT:  {"type": "result", "text": "<testo>", "angle": null | int}
  OUT:  {"type": "error",  "message": "<messaggio>"}

Differenze rispetto a surya_worker.py (0.17.x):
- Usa SuryaInferenceManager + RecognitionPredictor (Surya 0.20 API)
- Usa full_page=True (PROMPT_TYPE_HIGH_ACCURACY_BBOX): unica chiamata VLM
  sull'intera pagina, più accurata del block mode (PROMPT_TYPE_BLOCK).
  I layout vengono pre-calcolati e passati come fallback automatico in caso
  di loop/errore del decoder full-page.
- L'output è pred.blocks (ordinati per reading_order), niente FoundationPredictor
- Nessuna patch transformers5, nessun DetectionPredictor separato
- Estrazione testo via stripping HTML da block.html
"""

import json
import re
import sys
from typing import Optional

from PIL import Image


_SKIP_LABELS = frozenset({"Picture", "Figure"})


def _blocks_to_text(blocks) -> str:
    """Estrae testo plain dai blocchi Surya 0.20 in ordine di lettura."""
    sorted_blocks = sorted(
        (b for b in blocks if not b.skipped and not b.error),
        key=lambda b: b.reading_order,
    )
    parts = []
    for block in sorted_blocks:
        if block.label in _SKIP_LABELS:
            continue
        text = re.sub(r"<[^>]+>", " ", block.html)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _ocr_page(img: Image.Image, rec_pred, layout_pred, forced_angle: Optional[int] = None) -> tuple:
    """Restituisce (text, angle_applied).

    Usa full_page=True (PROMPT_TYPE_HIGH_ACCURACY_BBOX): un'unica chiamata VLM
    sull'intera pagina, più accurata del block mode (PROMPT_TYPE_BLOCK).
    I layout vengono pre-calcolati e passati come fallback automatico in caso
    di loop/errore del decoder full-page.
    Gestisce pagine doppie affiancate (landscape) dividendo a metà.
    """
    # Doppia pagina affiancata (landscape): divide a metà e processa ciascuna metà
    if forced_angle is None and img.size[0] > img.size[1] * 1.2:
        mid = img.size[0] // 2
        left_text,  _ = _ocr_page(img.crop((0, 0, mid, img.size[1])), rec_pred, layout_pred)
        right_text, _ = _ocr_page(img.crop((mid, 0, img.size[0], img.size[1])), rec_pred, layout_pred)
        combined = "\n\n".join(t for t in (left_text, right_text) if t)
        return combined, None

    angle_applied: Optional[int] = None
    if forced_angle is not None and forced_angle != 0:
        img = img.rotate(forced_angle, expand=True)
        angle_applied = forced_angle
        if img.size[0] > img.size[1] * 1.2:
            mid = img.size[0] // 2
            left_text,  _ = _ocr_page(img.crop((0, 0, mid, img.size[1])),
                                      rec_pred, layout_pred, forced_angle=0)
            right_text, _ = _ocr_page(img.crop((mid, 0, img.size[0], img.size[1])),
                                      rec_pred, layout_pred, forced_angle=0)
            combined = "\n\n".join(t for t in (left_text, right_text) if t)
            return combined, angle_applied

    try:
        layouts = layout_pred([img])
        # full_page=True: percorso più accurato; layouts funge da fallback automatico
        preds = rec_pred([img], layouts, full_page=True)
    except Exception:
        preds = rec_pred([img], full_page=True)

    if not preds or not preds[0].blocks:
        return "", angle_applied
    return _blocks_to_text(preds[0].blocks), angle_applied


def main():
    try:
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
        from surya.layout import LayoutPredictor
        manager = SuryaInferenceManager()
        rec_pred = RecognitionPredictor(manager)
        layout_pred = LayoutPredictor(manager)
    except Exception as e:
        print(json.dumps({"type": "error", "message": f"Inizializzazione Surya 0.20 fallita: {e}"}),
              flush=True)
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
        forced_angle = cmd.get("forced_angle")

        try:
            img = Image.open(path).convert("RGB")
            text, angle = _ocr_page(img, rec_pred, layout_pred, forced_angle=forced_angle)
            print(json.dumps({"type": "result", "text": text, "angle": angle}), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)


if __name__ == "__main__":
    main()
