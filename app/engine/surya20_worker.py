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
            # Label camelCase di Surya 2 → classe CSS kebab-case
            # (es. "SectionHeader" → "section-header").
            css = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", block.label).lower()
            parts.append(f'<div class="block {css}">{html}</div>')
    return "\n".join(parts)


def _norm_bbox(block, img_size) -> Optional[list]:
    """Bbox del blocco normalizzata in [0,1] rispetto a img_size (w, h).

    Restituisce [x1, y1, x2, y2] frazionari, o None se bbox/dimensioni non
    disponibili. Normalizzando qui (nello spazio pixel dell'immagine data a
    Surya) il writer del PDF può riposizionare il testo su qualsiasi pagina
    senza conoscere il DPI di rasterizzazione.
    """
    bbox = getattr(block, "bbox", None)
    if not bbox or not img_size:
        return None
    w, h = img_size
    if not w or not h:
        return None
    x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
    return [x1 / w, y1 / h, x2 / w, y2 / h]


def _blocks_to_structs(blocks, img_size=None) -> list:
    """Lista di {label, html, bbox} per ogni blocco (export DOCX e PDF).

    I blocchi immagine (_SKIP_LABELS) sono inclusi come segnaposto con html
    vuoto: il writer DOCX li rende come marcatore testuale così gli utenti non
    vedenti si orientano nel layout originale. Il testo plain (_blocks_to_text)
    continua invece a ignorarli.

    `bbox` (normalizzata in [0,1], vedi _norm_bbox) serve al PDF ricercabile per
    posizionare il testo OCR invisibile sopra la regione corrispondente
    dell'immagine; è None se le coordinate non sono disponibili.
    """
    sorted_blocks = sorted(
        (b for b in blocks if not b.skipped and not b.error),
        key=lambda b: b.reading_order,
    )
    structs = []
    for block in sorted_blocks:
        bbox = _norm_bbox(block, img_size)
        if block.label in _SKIP_LABELS:
            structs.append({"label": block.label, "html": "", "bbox": bbox})
        elif block.html.strip():
            structs.append({"label": block.label, "html": block.html.strip(),
                            "bbox": bbox})
    return structs


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
    """Combina i risultati (text, html, structs, suspect) di 1 o 2 sotto-immagini.

    subresults è una lista di tuple (text, html, structs, suspect) nell'ordine
    sinistra→destra. Restituisce (text, angle, html, structs, suspect) per la
    pagina; suspect è True solo se la pagina risulta interamente vuota ma almeno
    una sotto-immagine aveva layout di testo (degrado GPU).
    """
    texts = [t for t, _, _, _ in subresults if t]
    htmls = [h for _, h, _, _ in subresults if h]
    structs = []
    for _, _, s, _ in subresults:
        structs.extend(s)
    # Pagina divisa (doppia pagina landscape): le bbox sono normalizzate sulle
    # sotto-immagini sinistra/destra, non sulla pagina intera, quindi non sono
    # utilizzabili per posizionare il testo. Le azzeriamo → il PDF ricercabile
    # ripiega sul layout a flusso per queste pagine.
    if len(subresults) > 1:
        for st in structs:
            st["bbox"] = None
    combined_text = "\n\n".join(texts)
    suspect = (not combined_text.strip()) and any(s for _, _, _, s in subresults)
    return combined_text, angle_applied, "\n".join(htmls), structs, suspect


def _pred_to_result(pred, img_size=None) -> tuple:
    """Estrae (text, html, structs) da una predizione full_page (o pred vuota).

    img_size (w, h) è la dimensione in pixel dell'immagine passata a Surya:
    serve a normalizzare le bbox dei blocchi (vedi _blocks_to_structs).
    """
    if pred is None or not getattr(pred, "blocks", None):
        return "", "", []
    blocks = pred.blocks
    return (_blocks_to_text(blocks), _blocks_to_html(blocks),
            _blocks_to_structs(blocks, img_size))


def _layout_has_text(layout_result) -> bool:
    """True se il layout ha rilevato almeno una regione portatrice di testo.

    Ignora le regioni immagine (_SKIP_LABELS): una pagina di sole illustrazioni
    non è "portatrice di testo". Serve a distinguere una pagina degradata dalla
    GPU (layout con testo ma riconoscimento vuoto) da una pagina legittimamente
    senza testo (bianca o solo tavole).
    """
    bboxes = getattr(layout_result, "bboxes", None) or []
    for b in bboxes:
        if getattr(b, "label", "") not in _SKIP_LABELS:
            return True
    return False


def _suspect_empty(text: str, layout_result) -> bool:
    """True se il testo è vuoto ma il layout aveva regioni di testo (degrado GPU)."""
    return (not text.strip()) and layout_result is not None \
        and _layout_has_text(layout_result)


def _infer_subimage(img: Image.Image, rec_pred, layout_pred) -> tuple:
    """Esegue l'inferenza full_page su una singola sotto-immagine.

    Restituisce (text, html, structs, suspect). `suspect` è True se il
    riconoscimento è vuoto benché il layout abbia rilevato testo: sintomo di
    degrado/throttling della GPU (non di pagina realmente bianca).
    """
    layouts = None
    try:
        layouts = layout_pred([img])
        # full_page=True: percorso più accurato; layouts funge da fallback automatico
        preds = rec_pred([img], layouts, full_page=True)
    except Exception:
        preds = rec_pred([img], full_page=True)

    text, html, structs = _pred_to_result(preds[0] if preds else None, img.size)
    layout0 = layouts[0] if layouts else None
    return text, html, structs, _suspect_empty(text, layout0)


def _infer_batch(subimgs: list, rec_pred, layout_pred) -> list:
    """Esegue l'inferenza full_page su una lista di sotto-immagini in un solo
    batch (una chiamata layout + una chiamata recognition).

    Restituisce una lista di (text, html, structs, suspect) allineata a subimgs
    (suspect: vedi _infer_subimage). In caso di errore dell'intero batch,
    ripiega sull'inferenza per singola sotto-immagine così un solo elemento
    problematico non fa fallire tutte le pagine del chunk. Se però TUTTE le
    sotto-immagini falliscono, è un guasto sistemico (server d'inferenza morto
    per crash/throttling della GPU): in tal caso solleva un'eccezione invece di
    restituire pagine vuote, così l'app se ne accorge (vedi main) e può
    riavviare il worker anziché proseguire a vuoto.
    """
    if not subimgs:
        return []
    layouts = None
    try:
        layouts = layout_pred(subimgs)
        preds = rec_pred(subimgs, layouts, full_page=True)
    except Exception:
        try:
            preds = rec_pred(subimgs, full_page=True)
            layouts = None
        except Exception:
            return _infer_per_subimage_or_raise(subimgs, rec_pred, layout_pred)
    if not preds or len(preds) != len(subimgs):
        return _infer_per_subimage_or_raise(subimgs, rec_pred, layout_pred)
    results = []
    for idx, (p, s) in enumerate(zip(preds, subimgs)):
        text, html, structs = _pred_to_result(p, s.size)
        layout_i = layouts[idx] if layouts else None
        results.append((text, html, structs, _suspect_empty(text, layout_i)))
    return results


def _infer_per_subimage_or_raise(subimgs: list, rec_pred, layout_pred) -> list:
    """Inferenza per singola sotto-immagine con rilevamento guasto sistemico.

    Ritenta ogni sotto-immagine isolatamente. Le pagine che falliscono restano
    vuote (una pagina illeggibile non deve bloccare il chunk), ma se falliscono
    TUTTE solleva RuntimeError: significa che il server d'inferenza è caduto,
    non che le pagine siano bianche.
    """
    results = []
    errors = 0
    for s in subimgs:
        try:
            results.append(_infer_subimage(s, rec_pred, layout_pred))
        except Exception:
            results.append(("", "", [], False))
            errors += 1
    if errors == len(subimgs):
        raise RuntimeError(
            "Il server d'inferenza Surya non risponde "
            "(possibile crash o throttling della GPU)."
        )
    return results


def _ocr_batch(pages: list, rec_pred, layout_pred) -> list:
    """OCR di più pagine in un unico batch.

    pages è una lista di (img, forced_angle). Restituisce una lista di
    (text, angle, html, structs, suspect) nello stesso ordine delle pagine.

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
    """Restituisce (text, angle_applied, html, structs, suspect).

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
                for text, angle, html, structs, suspect in results:
                    items.append({
                        "text": text,
                        "angle": angle,
                        "html": html,
                        "blocks": structs,
                        "suspect": suspect,
                    })
                print(json.dumps({"type": "results", "items": items}), flush=True)
            except Exception as e:
                print(json.dumps({"type": "error", "message": str(e)}), flush=True)
            continue

        path = cmd.get("path", "")
        forced_angle = cmd.get("forced_angle")

        try:
            img = Image.open(path).convert("RGB")
            text, angle, html, structs, suspect = _ocr_page(
                img, rec_pred, layout_pred, forced_angle=forced_angle)
            print(json.dumps({
                "type": "result",
                "text": text,
                "angle": angle,
                "html": html,
                "blocks": structs,
                "suspect": suspect,
            }), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)


if __name__ == "__main__":
    main()
