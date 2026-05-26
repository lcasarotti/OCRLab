"""Scrittura file di output (TXT, DOCX) con gestione dei tag markup di Surya.

Tag supportati (prodotti da Surya):
  <i>…</i>     → corsivo
  <sup>N</sup>  → apice (numero di nota)

Per .txt: <sup> → apice Unicode (¹²³…), <i> → testo nudo.
Per .docx: formattazione Word nativa (corsivo e apice reali).
"""

import os
import re

import docx

# Mappa cifre ASCII → apici Unicode (U+2070 … U+2079)
_SUPERSCRIPT_MAP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# Pattern per i tag di primo livello (non-greedy, DOTALL per contenuti multiriga)
_TAG_RE = re.compile(r"(<i>.*?</i>|<sup>\d+</sup>|<math>.*?</math>)", re.DOTALL)
# Pattern per <sup> eventualmente annidato dentro <i>
_SUP_RE = re.compile(r"(<sup>\d+</sup>)")


def strip_markup(text: str) -> str:
    """Rimuove tutti i tag HTML-like (usato per la visualizzazione in anteprima)."""
    text = text.replace("\f", "\n\n")
    return re.sub(r"<[^>]+>", "", text)


def _convert_markup(text: str) -> str:
    """Converte i tag in equivalenti plain-text/Unicode (per .txt).

    - <sup>N</sup>  → apice Unicode (¹, ², ¹²…)
    - <i>…</i>     → solo il contenuto
    - <br>          → newline
    - altri tag     → rimossi
    """
    text = text.replace("\f", "\n\n")
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(
        r"<sup>(\d+)</sup>",
        lambda m: m.group(1).translate(_SUPERSCRIPT_MAP),
        text,
    )
    text = re.sub(r"<i>(.*?)</i>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<math>(.*?)</math>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _add_markup_runs(para, text: str) -> None:
    """Aggiunge runs con formattazione Word nativa al paragrafo.

    Gestisce:
    - testo normale → run standard
    - <i>…</i>     → run corsivo (con eventuale <sup> annidato)
    - <sup>N</sup>  → run apice
    - \\n interno   → a capo morbido (Shift+Enter in Word)
    """

    def _flush(content: str, italic: bool = False) -> None:
        """Aggiunge i run per `content`, gestendo <sup> interni e \\n."""
        for seg in _SUP_RE.split(content):
            if not seg:
                continue
            is_sup = seg.startswith("<sup>") and seg.endswith("</sup>")
            if is_sup:
                inner = seg[5:-6]
            else:
                inner = re.sub(r'<br\s*/?>', '\n', seg, flags=re.IGNORECASE)
                inner = re.sub(r'<[^>]+>', '', inner)
            lines = inner.split("\n")
            for j, line in enumerate(lines):
                if j > 0:
                    para.add_run().add_break()
                if line:
                    run = para.add_run(line)
                    if italic:
                        run.italic = True
                    if is_sup:
                        run.font.superscript = True

    for segment in _TAG_RE.split(text):
        if not segment:
            continue
        if segment.startswith("<i>") and segment.endswith("</i>"):
            _flush(segment[3:-4], italic=True)
        elif segment.startswith("<sup>") and segment.endswith("</sup>"):
            run = para.add_run(segment[5:-6])
            run.font.superscript = True
        elif segment.startswith("<math>") and segment.endswith("</math>"):
            _flush(segment[6:-7])   # testo normale, tag rimossi
        else:
            _flush(segment)


def write_txt(text: str, path: str) -> None:
    """Salva il testo come file UTF-8, convertendo i tag in Unicode."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(_convert_markup(text))


def write_docx(text: str, path: str) -> None:
    """Salva il testo come .docx con corsivo, apici e interruzioni di pagina Word nativi."""
    doc = docx.Document()
    pages = text.split("\f")
    for page_idx, page_text in enumerate(pages):
        if page_idx > 0:
            doc.add_page_break()
        for para_text in page_text.split("\n\n"):
            para_text = para_text.strip()
            if not para_text:
                continue
            para = doc.add_paragraph()
            _add_markup_runs(para, para_text)
    doc.save(path)


def _utf16be_hex(text: str) -> str:
    """Codifica `text` come stringa PDF UTF-16BE con BOM (usata per /ActualText)."""
    return (b"\xfe\xff" + text.encode("utf-16-be")).hex().upper()


def _add_tagged_structure(doc: "fitz.Document", page_data: list) -> None:
    """Aggiunge MarkInfo e structure tree al PDF per accessibilità tagged PDF.

    page_data: lista di (page_xref: int, has_text: bool, text: str).
    Ogni pagina con testo ha già MCID 0 nel content stream (elemento /P).
    Le immagini sono già marcate /Artifact (nessun MCID, nessun StructElem).

    Ogni elemento /P riceve /ActualText con il testo OCR UTF-16BE: Acrobat
    e gli screen reader leggono il testo direttamente dalla struttura, senza
    dover estrarre i glyph dal content stream (che usa render_mode=3).
    """
    pages_with_text = [
        (px, i, txt)
        for i, (px, ht, txt) in enumerate(page_data)
        if ht
    ]
    if not pages_with_text:
        return

    catalog_xref     = doc.pdf_catalog()
    struct_root_xref = doc.get_new_xref()
    doc_elem_xref    = doc.get_new_xref()
    parent_tree_xref = doc.get_new_xref()

    # Un elemento /P per ogni pagina con testo, con /ActualText
    p_xrefs = []
    for page_xref, sp_idx, txt in pages_with_text:
        px              = doc.get_new_xref()
        actual_text_hex = _utf16be_hex(txt)
        doc.update_object(px, (
            f"<< /Type /StructElem /S /P "
            f"/P {doc_elem_xref} 0 R "
            f"/Pg {page_xref} 0 R "
            f"/K 0 "
            f"/ActualText <{actual_text_hex}> >>"
        ))
        p_xrefs.append((page_xref, sp_idx, px))

    # Elemento /Document: radice logica del documento
    kids_str = " ".join(f"{px} 0 R" for _, _, px in p_xrefs)
    doc.update_object(doc_elem_xref, (
        f"<< /Type /StructElem /S /Document "
        f"/P {struct_root_xref} 0 R "
        f"/Kids [{kids_str}] >>"
    ))

    # ParentTree: mappa StructParents-index → [StructElem per MCID 0]
    nums_parts = []
    for page_xref, sp_idx, px in p_xrefs:
        nums_parts.append(f"{sp_idx} [{px} 0 R]")
        doc.xref_set_key(page_xref, "StructParents", str(sp_idx))
        doc.xref_set_key(page_xref, "Tabs", "/S")   # lettura in ordine strutturale
    doc.update_object(parent_tree_xref,
                      f"<< /Nums [{' '.join(nums_parts)}] >>")

    # StructTreeRoot
    doc.update_object(struct_root_xref, (
        f"<< /Type /StructTreeRoot "
        f"/K [{doc_elem_xref} 0 R] "
        f"/ParentTree {parent_tree_xref} 0 R >>"
    ))

    # Aggiorna il catalogo
    doc.xref_set_key(catalog_xref, "MarkInfo", "<< /Marked true >>")
    doc.xref_set_key(catalog_xref, "StructTreeRoot",
                     f"{struct_root_xref} 0 R")
    doc.xref_set_key(catalog_xref, "Lang", "(it-IT)")


def write_searchable_pdf(source_path: str, ocr_text: str, out_path: str) -> None:
    """PDF ricercabile tagged: immagine /Artifact + testo OCR invisibile in elementi /P.

    Struttura di ogni pagina (ordine nel content stream):
    1. Immagine rasterizzata sorgente: disegnata per prima (sfondo visivo),
       poi marcata /Artifact → gli screen reader la ignorano.
    2. Testo OCR invisibile (render_mode=3): disegnato sopra l'immagine,
       in blocco /P <</MCID 0>> BDC…EMC → referenziato dalla structure tree.

    L'approccio render_mode=3 + immagine-come-sfondo è lo standard dei PDF
    ricercabili prodotti da strumenti OCR (ocrmypdf, ABBYY, …).  Acrobat e gli
    screen reader leggono il testo dallo strato invisibile tramite il ToUnicode
    CMap e/o l'ActualText nella structure tree.

    Nota implementativa: TextWriter aggiunge content stream in modo "lazy"
    (non accessibili via xref_stream() finché il doc non viene serializzato).
    Perciò si fa un tobytes() intermedio prima di applicare i marker BDC/EMC.

    Args:
        source_path: percorso del file sorgente (PDF o immagine).
        ocr_text:    testo OCR con pagine separate da \\f.
        out_path:    percorso del file PDF di output.
    """
    import pymupdf as fitz

    pages_text = [strip_markup(t).strip() for t in ocr_text.split("\f")]

    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".pdf":
        src = fitz.open(source_path)
    else:
        img_doc = fitz.open(source_path)
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()
        src = fitz.open("pdf", pdf_bytes)

    out_doc = fitz.open()
    text_pages: set[int] = set()   # indici pagina con testo
    page_texts: list[str] = []     # testo per pagina (per ActualText corretto in Fase 4)

    try:
        # ── Fase 1: costruisce le pagine (immagine prima, testo invisibile sopra) ─
        # Ordine fondamentale: prima l'immagine (sfondo visivo), poi il testo OCR
        # con render_mode=3 (invisibile, font Type1/WinAnsiEncoding).
        # WinAnsiEncoding è il formato più compatibile con AT/screen reader:
        # ogni byte del content stream mappa direttamente a un carattere Unicode
        # senza bisogno di CMap aggiuntive (a differenza di CIDFont/Identity-H).
        for i in range(len(src)):
            src_page = src[i]
            rect     = src_page.rect

            out_page = out_doc.new_page(width=rect.width, height=rect.height)

            # 1. Immagine sorgente: disegnata per prima → sarà marcata /Artifact
            pix = src_page.get_pixmap(dpi=200)
            out_page.insert_image(rect, pixmap=pix)

            # 2. Testo OCR invisibile (render_mode=3) sopra l'immagine
            text = pages_text[i] if i < len(pages_text) else ""
            page_texts.append(text)
            if text:
                # Fontsize piccolo (6pt) per far stare più testo nella pagina;
                # l'invisibilità lo rende irrilevante visivamente.
                text_rect = fitz.Rect(
                    rect.x0 + 10, rect.y0 + 10,
                    rect.x1 - 10, rect.y1 - 10,
                )
                out_page.insert_textbox(
                    text_rect, text,
                    fontname="helv", fontsize=6,
                    render_mode=3,
                )
                text_pages.add(i)

        # ── Fase 2: serializza in buffer per rendere leggibili gli xref ───────
        buf = out_doc.tobytes()
    finally:
        src.close()
        out_doc.close()

    # ── Fase 3: riapre dal buffer e applica i marker BDC/EMC ─────────────────
    out_doc = fitz.open("pdf", buf)
    try:
        page_data = []
        for i in range(len(out_doc)):
            out_page = out_doc[i]
            has_text = (i in text_pages)
            # Usa il testo della pagina corrente (non la variabile dell'ultimo ciclo)
            page_text = page_texts[i] if i < len(page_texts) else ""

            for xref in out_page.get_contents():
                raw = out_doc.xref_stream(xref)
                if not raw:
                    continue
                cs = raw.decode("latin-1", errors="replace")
                if "BT" in cs:
                    # Stream di testo → elemento /P con MCID 0
                    out_doc.update_stream(
                        xref,
                        ("/P <</MCID 0>> BDC\n" + cs + "\nEMC\n").encode("latin-1"),
                    )
                else:
                    # Stream immagine → /Artifact (ignorato dagli screen reader)
                    out_doc.update_stream(
                        xref,
                        ("/Artifact BMC\n" + cs + "\nEMC\n").encode("latin-1"),
                    )

            page_data.append((out_page.xref, has_text, page_text if has_text else ""))

        # ── Fase 4: aggiunge structure tree per tagged PDF ────────────────────
        _add_tagged_structure(out_doc, page_data)

        out_doc.save(out_path, garbage=4, deflate=True)
    finally:
        out_doc.close()


def write_file(text: str, path: str, source_path: str = "") -> None:
    """Salva il testo in base all'estensione del file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        write_docx(text, path)
    elif ext == ".txt":
        write_txt(text, path)
    elif ext == ".pdf":
        if not source_path:
            raise ValueError(
                "Per il PDF ricercabile è necessario il percorso del file sorgente."
            )
        write_searchable_pdf(source_path, text, path)
    else:
        raise ValueError(f"Formato non supportato: {ext}")
