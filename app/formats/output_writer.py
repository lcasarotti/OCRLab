"""Scrittura file di output (TXT, DOCX, HTML) con gestione dei tag markup di Surya.

Tag supportati (prodotti da Surya):
  <i>…</i>     → corsivo
  <sup>N</sup>  → apice (numero di nota)

Per .txt: <sup> → apice Unicode (¹²³…), <i> → testo nudo.
Per .docx: formattazione Word nativa (corsivo e apice reali).
Per .docx (Surya 0.20): rendering strutturato con heading, tabelle, caption, note.
Per .html (Surya 0.20): HTML completo con CSS pronto per la visualizzazione.
"""

import os
import re
from html.parser import HTMLParser

import docx

from app.i18n import _

# Mappa cifre ASCII → apici Unicode (U+2070 … U+2079)
_SUPERSCRIPT_MAP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# Pattern per i tag di primo livello (non-greedy, DOTALL per contenuti multiriga)
_TAG_RE = re.compile(r"(<i>.*?</i>|<sup>\d+</sup>|<math>.*?</math>)", re.DOTALL)
# Pattern per <sup> eventualmente annidato dentro <i>
_SUP_RE = re.compile(r"(<sup>\d+</sup>)")

# Stili Word per etichette Surya 0.20.
# NB: Surya 2 usa label canoniche in camelCase (SectionHeader, PageHeader…),
# non i vecchi nomi con trattino. Surya 2 non ha una label "Title": il livello
# di intestazione più alto è "SectionHeader".
_BLOCK_STYLE = {
    "SectionHeader": "Heading 1",
    "Caption": "Caption",
    "PageHeader": "Header",
    "PageFooter": "Footer",
}

# Blocchi puramente visivi: nel DOCX diventano un segnaposto testuale, così gli
# utenti non vedenti sanno dove si trovavano le immagini nel layout originale.
_IMAGE_PLACEHOLDER = {
    "Picture": "[Image]",
    "Figure": "[Figure]",
}

# Etichette da saltare nel DOCX: solo le pagine vuote (nessun contenuto utile).
_SKIP_DOCX_LABELS = frozenset({"BlankPage"})


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


# ---------------------------------------------------------------------------
# Funzioni per l'export strutturato Surya 0.20
# ---------------------------------------------------------------------------

def _html_to_markup(html: str) -> str:
    """Converte l'HTML di un blocco Surya nel markup semplificato (<i>, <sup>)."""
    html = re.sub(r'<em[^>]*>', '<i>', html, flags=re.IGNORECASE)
    html = re.sub(r'</em>', '</i>', html, flags=re.IGNORECASE)
    # Grassetto: rimuovi i tag wrappanti (nessun grassetto nel markup)
    html = re.sub(r'<b[^>]*>(.*?)</b>', r'\1', html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'\1', html, flags=re.IGNORECASE | re.DOTALL)
    # Interruzioni di riga e separatori paragrafo
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>\s*<p[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<p[^>]*>|</p>', '', html, flags=re.IGNORECASE)
    # Liste: converti <li> in righe
    html = re.sub(r'<li[^>]*>', '• ', html, flags=re.IGNORECASE)
    html = re.sub(r'</li>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[ou]l[^>]*>|</[ou]l>', '', html, flags=re.IGNORECASE)
    # Math: mantieni solo il contenuto
    html = re.sub(r'<math[^>]*>(.*?)</math>', r'\1', html, flags=re.IGNORECASE | re.DOTALL)
    # Preserva <i>, </i>, <sup>N</sup> tramite placeholder prima di strippare
    html = html.replace('<i>', '\x00ITAG\x00')
    html = html.replace('</i>', '\x00EITAG\x00')
    html = re.sub(r'<sup>(\d+)</sup>', '\x00SUP\\1\x00', html)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('\x00ITAG\x00', '<i>')
    html = html.replace('\x00EITAG\x00', '</i>')
    html = re.sub(r'\x00SUP(\d+)\x00', r'<sup>\1</sup>', html)
    return html.strip()


class _TableParser(HTMLParser):
    """Parser minimale per estrarre righe e celle da un frammento HTML di tabella."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._in_row = False
        self._in_cell = False
        self._cell_buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_row = True
            self.rows.append([])
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self.rows[-1].append("".join(self._cell_buf).strip())
            self._in_cell = False
            self._cell_buf = []
        elif tag == "tr":
            self._in_row = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)


def _add_html_table(doc, html: str) -> None:
    """Aggiunge una tabella Word da un frammento HTML <table>."""
    parser = _TableParser()
    parser.feed(html)
    rows = [r for r in parser.rows if r]
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    if ncols == 0:
        return
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Table Grid"
    for r_idx, row_data in enumerate(rows):
        for c_idx, cell_text in enumerate(row_data):
            if c_idx < ncols:
                table.rows[r_idx].cells[c_idx].text = cell_text


def _add_block_to_docx(doc, block: dict) -> None:
    """Aggiunge un blocco Surya 0.20 al documento Word."""
    label = block.get("label", "Text")
    html = block.get("html", "")

    if label in _SKIP_DOCX_LABELS:
        return

    if label in _IMAGE_PLACEHOLDER:
        # Blocco immagine: nessun testo, ma resta in reading order come
        # marcatore così l'utente si orienta nel layout originale.
        doc.add_paragraph(style="Caption").add_run(
            _(_IMAGE_PLACEHOLDER[label])).italic = True
        return

    if not html:
        return

    if label == "Table":
        _add_html_table(doc, html)
        return

    markup = _html_to_markup(html)
    if not markup:
        return

    if label == "ListGroup":
        # Un blocco lista contiene più voci separate da newline (dai <li>).
        for line in markup.split("\n"):
            item = line.lstrip("• ").strip()
            if item:
                _add_markup_runs(doc.add_paragraph(style="List Bullet"), item)
        return

    if label == "Footnote":
        para = doc.add_paragraph(style="Normal")
        para.add_run("— ").italic = True
    else:
        style = _BLOCK_STYLE.get(label, "Normal")
        para = doc.add_paragraph(style=style)

    _add_markup_runs(para, markup)


# ---------------------------------------------------------------------------
# Funzioni di scrittura file
# ---------------------------------------------------------------------------

def write_txt(text: str, path: str) -> None:
    """Salva il testo come file UTF-8, convertendo i tag in Unicode."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(_convert_markup(text))


def write_docx(text: str, path: str, blocks=None) -> None:
    """Salva il testo come .docx.

    Se blocks è fornito (list[list[dict]] da Surya 0.20), usa il rendering
    strutturato con heading, tabelle e stili semantici. Altrimenti usa il
    rendering flat con corsivo, apici e interruzioni di pagina Word nativi.
    """
    doc = docx.Document()
    if blocks:
        for page_idx, page_blocks in enumerate(blocks):
            if page_idx > 0:
                doc.add_page_break()
            for block in page_blocks:
                _add_block_to_docx(doc, block)
    else:
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


def write_html(html: str, path: str) -> None:
    """Salva l'HTML strutturato Surya 0.20 come file .html con wrapper CSS."""
    content = (
        '<!DOCTYPE html>\n<html lang="it">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<title>OCR Lab — risultato</title>\n'
        '<style>\n'
        '  body { font-family: Georgia, serif; max-width: 900px; margin: 2em auto;'
        ' padding: 0 1em; line-height: 1.5; }\n'
        '  .title { font-size: 1.6em; font-weight: bold; margin: 0.8em 0 0.4em; }\n'
        '  .section-header { font-size: 1.25em; font-weight: bold; margin: 1em 0 0.4em; }\n'
        '  .caption { font-size: 0.9em; font-style: italic; color: #555; }\n'
        '  .footnote { font-size: 0.85em; border-top: 1px solid #ccc;'
        ' margin-top: 1.5em; padding-top: 0.5em; color: #444; }\n'
        '  .page-header, .page-footer { font-size: 0.8em; color: #888; }\n'
        '  .equation { font-style: italic; }\n'
        '  table { border-collapse: collapse; width: 100%; margin: 1em 0; }\n'
        '  td, th { border: 1px solid #bbb; padding: 4px 8px; }\n'
        '  th { background: #f0f0f0; font-weight: bold; }\n'
        '  hr.page-break { border: none; border-top: 2px dashed #aaa; margin: 2em 0; }\n'
        '</style>\n</head>\n<body>\n'
        + html
        + '\n</body>\n</html>'
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


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
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

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


def write_file(text: str, path: str, source_path: str = "",
               blocks=None, html: str = "") -> None:
    """Salva il testo in base all'estensione del file.

    blocks: list[list[dict]] da Surya 0.20 (passato a write_docx per layout strutturato).
    html:   HTML grezzo da Surya 0.20 (necessario per l'esportazione .html).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        write_docx(text, path, blocks=blocks)
    elif ext == ".txt":
        write_txt(text, path)
    elif ext == ".pdf":
        if not source_path:
            raise ValueError(
                "Per il PDF ricercabile è necessario il percorso del file sorgente."
            )
        write_searchable_pdf(source_path, text, path)
    elif ext == ".html":
        if not html:
            raise ValueError(
                "L'esportazione HTML è disponibile solo con il motore Surya 0.2."
            )
        write_html(html, path)
    else:
        raise ValueError(f"Formato non supportato: {ext}")
