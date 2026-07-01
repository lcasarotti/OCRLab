"""Surya 0.20 OCR worker — script standalone eseguito dal Python esterno.

Protocollo stdin/stdout (una riga JSON per messaggio):
  IN:   {"path": "<path_immagine>", "forced_angle": null | int}
  IN:   {"quit": true}
  OUT:  {"type": "ready"}
  OUT:  {"type": "result", "text": "<testo>", "angle": null | int,
          "html": "<html_pagina>", "blocks": [{"label": ..., "html": ...}, ...]}
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
- Restituisce anche l'HTML strutturato della pagina e la lista blocchi
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


def _blocks_to_html(blocks) -> str:
    """Assembla l'HTML della pagina dai blocchi Surya 0.20."""
    sorted_blocks = sorted(
        (b for b in blocks if not b.skipped and not b.error),
        key=lambda b: b.reading_order,
    )
    parts = []
    for block in sorted_blocks:
        if block.label in _SKIP_LABELS:
            continue
        html = block.html.strip()
        if html:
            css = block.label.lower().replace(" ", "-").replace("_", "-")
            parts.append(f'<div class="block {css}">{html}</div>')
    return "\n".join(parts)


def _blocks_to_structs(blocks) -> list:
    """Lista di {label, html} per ogni blocco (per export strutturato DOCX)."""
    sorted_blocks = sorted(
        (b for b in blocks if not b.skipped and not b.error),
        key=lambda b: b.reading_order,
    )
    return [
        {"label": block.label, "html": block.html.strip()}
        for block in sorted_blocks
        if block.label not in _SKIP_LABELS and block.html.strip()
    ]


def _is_landscape(img: Image.Image) -> bool:
    """True se l'immagine è chiaramente landscape (doppia pagina affiancata)."""
    return img.size[0] > img.size[1] * 1.2


def _split_subimages(img: Image.Image, forced_angle: Optional[int] = None) -> tuple:
    """Applica rotazione e split doppia-pagina, restituendo le sotto-immagini.

    Restituisce (subimgs, angle_applied) dove subimgs è una lista di 1 o 2
    immagini portrait pronte per l'inferenza (metà sinistra/destra per le
    pagine landscape affiancate). Centralizza la logica di split/rotazione
    così da poterla riusare sia nel percorso singolo che in quello batch.
    """
    angle_applied: Optional[int] = None

    if forced_angle is not None and forced_angle != 0:
        img = img.rotate(forced_angle, expand=True)
        angle_applied = forced_angle

    # Doppia pagina affiancata (landscape): divide a metà.
    # Con forced_angle==None lo split è sempre ammesso; dopo una rotazione
    # esplicita lo split resta ammesso (le due metà si processano dritte).
    if _is_landscape(img):
        mid = img.size[0] // 2
        left = img.crop((0, 0, mid, img.size[1]))
        right = img.crop((mid, 0, img.size[0], img.size[1]))
        return [left, right], angle_applied

    return [img], angle_applied


def _combine_subresults(subresults: list, angle_applied: Optional[int]) -> tuple:
    """Combina i risultati (text, html, structs) di 1 o 2 sotto-immagini.

    subresults è una lista di tuple (text, html, structs) nell'ordine
    sinistra→destra. Restituisce (text, angle, html, structs) per la pagina.
    """
    texts = [t for t, _, _ in subresults if t]
    htmls = [h for _, h, _ in subresults if h]
    structs = []
    for _, _, s in subresults:
        structs.extend(s)
    return "\n\n".join(texts), angle_applied, "\n".join(htmls), structs


def _pred_to_result(pred) -> tuple:
    """Estrae (text, html, structs) da una predizione full_page (o pred vuota)."""
    if pred is None or not getattr(pred, "blocks", None):
        return "", "", []
    blocks = pred.blocks
    return _blocks_to_text(blocks), _blocks_to_html(blocks), _blocks_to_structs(blocks)


def _infer_subimage(img: Image.Image, rec_pred, layout_pred) -> tuple:
    """Esegue l'inferenza full_page su una singola sotto-immagine.

    Restituisce (text, html, structs).
    """
    try:
        layouts = layout_pred([img])
        # full_page=True: percorso più accurato; layouts funge da fallback automatico
        preds = rec_pred([img], layouts, full_page=True)
    except Exception:
        preds = rec_pred([img], full_page=True)

    return _pred_to_result(preds[0] if preds else None)


def _infer_batch(subimgs: list, rec_pred, layout_pred) -> list:
    """Esegue l'inferenza full_page su una lista di sotto-immagini in un solo
    batch (una chiamata layout + una chiamata recognition).

    Restituisce una lista di (text, html, structs) allineata a subimgs.
    In caso di errore dell'intero batch, ripiega sull'inferenza per singola
    sotto-immagine (con la propria gestione errori) così un solo elemento
    problematico non fa fallire tutte le pagine del chunk.
    """
    if not subimgs:
        return []
    try:
        layouts = layout_pred(subimgs)
        preds = rec_pred(subimgs, layouts, full_page=True)
    except Exception:
        try:
            preds = rec_pred(subimgs, full_page=True)
        except Exception:
            return [_safe_infer_subimage(s, rec_pred, layout_pred) for s in subimgs]
    if not preds or len(preds) != len(subimgs):
        return [_safe_infer_subimage(s, rec_pred, layout_pred) for s in subimgs]
    return [_pred_to_result(p) for p in preds]


def _safe_infer_subimage(img: Image.Image, rec_pred, layout_pred) -> tuple:
    """_infer_subimage che non solleva mai: in caso di errore restituisce vuoto."""
    try:
        return _infer_subimage(img, rec_pred, layout_pred)
    except Exception:
        return "", "", []


def _ocr_batch(pages: list, rec_pred, layout_pred) -> list:
    """OCR di più pagine in un unico batch.

    pages è una lista di (img, forced_angle). Restituisce una lista di
    (text, angle, html, structs) nello stesso ordine delle pagine in input.

    Le pagine vengono appiattite in sotto-immagini (split doppia-pagina),
    processate tutte insieme in un solo forward pass, poi ri-raggruppate.
    """
    sub_imgs: list = []
    page_of: list = []          # per ogni sotto-immagine: indice pagina
    angles: list = [None] * len(pages)
    for pi, (img, forced_angle) in enumerate(pages):
        subs, angle = _split_subimages(img, forced_angle)
        angles[pi] = angle
        for sub in subs:
            sub_imgs.append(sub)
            page_of.append(pi)

    sub_results = _infer_batch(sub_imgs, rec_pred, layout_pred)

    grouped: list = [[] for _ in pages]
    for j, res in enumerate(sub_results):
        grouped[page_of[j]].append(res)

    return [_combine_subresults(grouped[pi], angles[pi]) for pi in range(len(pages))]


def _ocr_page(img: Image.Image, rec_pred, layout_pred,
              forced_angle: Optional[int] = None) -> tuple:
    """Restituisce (text, angle_applied, html, structs).

    Usa full_page=True (PROMPT_TYPE_HIGH_ACCURACY_BBOX): un'unica chiamata VLM
    sull'intera pagina, più accurata del block mode (PROMPT_TYPE_BLOCK).
    I layout vengono pre-calcolati e passati come fallback automatico in caso
    di loop/errore del decoder full-page.
    Gestisce pagine doppie affiancate (landscape) dividendo a metà.
    """
    subimgs, angle_applied = _split_subimages(img, forced_angle)
    subresults = [_infer_subimage(sub, rec_pred, layout_pred) for sub in subimgs]
    return _combine_subresults(subresults, angle_applied)


def _warmup(rec_pred, layout_pred) -> None:
    """Esegue un'inferenza fittizia per caricare i modelli in memoria prima di ready."""
    dummy = Image.new("RGB", (64, 64), color=(255, 255, 255))
    try:
        layouts = layout_pred([dummy])
        rec_pred([dummy], layouts, full_page=True)
    except Exception:
        try:
            rec_pred([dummy], full_page=True)
        except Exception:
            pass


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

    _warmup(rec_pred, layout_pred)

    print(json.dumps({"type": "ready", "batch": True}), flush=True)

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

        # Comando batch: {"paths": [...], "forced_angle": null | int}
        paths = cmd.get("paths")
        if isinstance(paths, list):
            forced_angle = cmd.get("forced_angle")
            items = []
            try:
                pages = [
                    (Image.open(p).convert("RGB"), forced_angle) for p in paths
                ]
                results = _ocr_batch(pages, rec_pred, layout_pred)
                for text, angle, html, structs in results:
                    items.append({
                        "text": text,
                        "angle": angle,
                        "html": html,
                        "blocks": structs,
                    })
                print(json.dumps({"type": "results", "items": items}), flush=True)
            except Exception as e:
                print(json.dumps({"type": "error", "message": str(e)}), flush=True)
            continue

        path = cmd.get("path", "")
        forced_angle = cmd.get("forced_angle")

        try:
            img = Image.open(path).convert("RGB")
            text, angle, html, structs = _ocr_page(
                img, rec_pred, layout_pred, forced_angle=forced_angle)
            print(json.dumps({
                "type": "result",
                "text": text,
                "angle": angle,
                "html": html,
                "blocks": structs,
            }), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)


if __name__ == "__main__":
    main()
