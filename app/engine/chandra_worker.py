"""Chandra 2 OCR worker — script standalone eseguito dal Python di chandra-venv.

Chandra è un VLM (Datalab, ~4B parametri) che converte immagini/PDF in HTML/
Markdown preservando il layout, con bounding box per blocco. Qui gira in
locale via il backend HuggingFace (transformers), con quantizzazione 4-bit
(bitsandbytes/NF4) per stare entro ~12 GB di VRAM.

Protocollo stdin/stdout (una riga JSON per messaggio) — identico al worker
Surya 0.2, così ChandraEngine riusa tutta la macchina IPC/watchdog di SuryaEngine:
  IN:   {"path": "<path_immagine>", "forced_angle": <ignorato>}
  IN:   {"paths": [<path>, ...]}          (opzionale, processati in sequenza)
  IN:   {"quit": true}
  OUT:  {"type": "ready"}
  OUT:  {"type": "result", "text": "<markdown>", "angle": null,
          "html": "<html_pagina>", "blocks": [{"label", "html", "bbox"}, ...],
          "suspect": <bool>}
  OUT:  {"type": "error",  "message": "<messaggio>"}

Configurazione via variabili d'ambiente (impostate da ChandraEngine):
  CHANDRA_QUANTIZE   "4bit" (default) | "none"
  MAX_OUTPUT_TOKENS  token massimi di output per pagina (default: settings Chandra)
  MODEL_CHECKPOINT   checkpoint HF (default: datalab-to/chandra-ocr-2)
"""

import json
import os
import sys

from PIL import Image

# Blocchi non testuali: inclusi nell'output strutturato come segnaposto a html
# vuoto (orientamento per screen reader), ma ignorati dal testo/suspect.
_SKIP_LABELS = frozenset({"Image", "Figure"})


def _max_output_tokens():
    """Token massimi di output per pagina (env MAX_OUTPUT_TOKENS, o None = default)."""
    raw = os.environ.get("MAX_OUTPUT_TOKENS", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_model():
    """Carica il modello Chandra. 4-bit NF4 di default, bf16 se CHANDRA_QUANTIZE=none.

    Chandra `load_model()` forza bf16 e non accetta una quant config, quindi per la
    modalità quantizzata costruiamo il modello a mano replicando ciò che fa
    `chandra.model.hf.load_model` (dtype, device_map, padding_side, .processor).
    """
    from chandra.settings import settings

    quant = os.environ.get("CHANDRA_QUANTIZE", "4bit").strip().lower()

    if quant == "none":
        from chandra.model.hf import load_model
        return load_model()

    try:
        import torch
        from transformers import (
            AutoModelForImageTextToText,
            AutoProcessor,
            BitsAndBytesConfig,
        )

        qcfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        kwargs = {
            "quantization_config": qcfg,
            # device_map="auto": accelerate offloada su CPU/RAM ciò che non entra in
            # VRAM (fallback lento ma funzionante sotto i 12 GB al limite).
            "device_map": "auto",
            "dtype": torch.bfloat16,
        }
        if settings.TORCH_ATTN:
            kwargs["attn_implementation"] = settings.TORCH_ATTN

        model = AutoModelForImageTextToText.from_pretrained(
            settings.MODEL_CHECKPOINT, **kwargs
        ).eval()
        processor = AutoProcessor.from_pretrained(settings.MODEL_CHECKPOINT)
        processor.tokenizer.padding_side = "left"
        model.processor = processor
        return model
    except Exception as e:
        # bitsandbytes assente/non supportato (es. GPU troppo recente, macOS):
        # ripiega su bf16 pieno. Richiede più VRAM ma evita un fallimento totale.
        print(f"Quantizzazione 4-bit non disponibile ({e}); uso bf16 pieno.",
              file=sys.stderr, flush=True)
        from chandra.model.hf import load_model
        return load_model()


def _norm_bbox(bbox, img_size):
    """Normalizza una bbox pixel [x0,y0,x1,y1] in [0,1] rispetto a img_size (w, h).

    parse_chunks restituisce già le bbox in pixel sull'immagine passata; qui le
    normalizziamo così il writer del PDF ricercabile può riposizionare il testo
    su qualsiasi pagina senza conoscere il DPI di rasterizzazione (come in
    surya20_worker._norm_bbox).
    """
    if not bbox or not img_size:
        return None
    w, h = img_size
    if not w or not h:
        return None
    try:
        x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
    except (TypeError, IndexError, ValueError):
        return None
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def _chunks_to_blocks(chunks, img_size):
    """Converte i chunk Chandra in blocchi {label, html, bbox} normalizzati.

    I blocchi immagine (_SKIP_LABELS) sono inclusi come segnaposto a html vuoto:
    l'export DOCX li rende come marcatore testuale per orientare chi usa uno
    screen reader; il testo plain e l'euristica suspect li ignorano.
    """
    blocks = []
    for ch in chunks:
        label = ch.get("label") or "block"
        bbox = _norm_bbox(ch.get("bbox"), img_size)
        if label in _SKIP_LABELS:
            blocks.append({"label": label, "html": "", "bbox": bbox})
            continue
        content = (ch.get("content") or "").strip()
        if content:
            blocks.append({"label": label, "html": content, "bbox": bbox})
    return blocks


def _chunks_have_text(chunks):
    """True se almeno un chunk è una regione portatrice di testo (non immagine)."""
    for ch in chunks:
        label = ch.get("label") or "block"
        if label in _SKIP_LABELS:
            continue
        if (ch.get("content") or "").strip():
            return True
    return False


def _ocr_page(path, model, max_tokens):
    """OCR di una pagina. Restituisce (text, html, blocks, suspect).

    `suspect` è True se il riconoscimento è vuoto benché il layout abbia rilevato
    regioni di testo: sintomo di degrado/throttling GPU (non di pagina bianca).
    Riusa l'euristica anti-degrado di Surya via ChandraEngine._maybe_recover_degraded.
    """
    from chandra.input import load_image
    from chandra.model.hf import generate_hf
    from chandra.model.schema import BatchInputItem
    from chandra.output import parse_markdown, parse_html, parse_chunks

    img = load_image(path)
    batch = [BatchInputItem(image=img, prompt_type="ocr_layout")]
    raw = generate_hf(batch, model, max_output_tokens=max_tokens)[0].raw

    text = parse_markdown(raw, include_headers_footers=False, include_images=False)
    html = parse_html(raw, include_headers_footers=False, include_images=False)
    chunks = parse_chunks(raw, img)
    blocks = _chunks_to_blocks(chunks, img.size)
    suspect = (not text.strip()) and _chunks_have_text(chunks)
    return text, html, blocks, suspect


def _warmup(model):
    """Inferenza fittizia per portare i pesi in VRAM prima di "ready"."""
    from chandra.model.hf import generate_hf
    from chandra.model.schema import BatchInputItem
    dummy = Image.new("RGB", (512, 512), color=(255, 255, 255))
    try:
        generate_hf([BatchInputItem(image=dummy, prompt_type="ocr")],
                    model, max_output_tokens=16)
    except Exception:
        pass


def _emit(obj):
    print(json.dumps(obj), flush=True)


def main():
    try:
        model = _load_model()
    except Exception as e:
        _emit({"type": "error",
               "message": f"Inizializzazione Chandra fallita: {e}"})
        sys.exit(1)

    max_tokens = _max_output_tokens()

    _warmup(model)
    _emit({"type": "ready"})

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

        # Comando batch: processato in sequenza (VRAM 12 GB → niente batch reale).
        paths = cmd.get("paths")
        if isinstance(paths, list):
            items = []
            try:
                for p in paths:
                    text, html, blocks, suspect = _ocr_page(p, model, max_tokens)
                    items.append({"text": text, "angle": None, "html": html,
                                  "blocks": blocks, "suspect": suspect})
                _emit({"type": "results", "items": items})
            except Exception as e:
                _emit({"type": "error", "message": str(e)})
            continue

        path = cmd.get("path", "")
        try:
            text, html, blocks, suspect = _ocr_page(path, model, max_tokens)
            _emit({"type": "result", "text": text, "angle": None, "html": html,
                   "blocks": blocks, "suspect": suspect})
        except Exception as e:
            _emit({"type": "error", "message": str(e)})


if __name__ == "__main__":
    main()
