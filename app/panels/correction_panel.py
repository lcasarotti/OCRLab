"""Tab Correction: OCR text correction via LLM."""

import hashlib
import json
import os
import threading

import wx

from app.engine.chunker import TextChunker
from app.engine.llm_engine import create_engine
from app.formats.input_reader import read_file
from app.formats.output_writer import write_file
from app.i18n import _
from app.speech import announce

RESUME_SUFFIX = ".ocrlab-resume.json"
RESUME_VERSION = 1


class CorrectionPanel(wx.Panel):
    def __init__(self, parent, main_frame):
        super().__init__(parent)
        self.main_frame = main_frame
        self.file_path = ""
        self.loaded_text = ""
        self.corrected_text = ""
        self._busy = False
        self._cancel_event = threading.Event()
        self._stream_display_len = 0

        # Stato di ripresa (resume) sui rate limit / interruzioni.
        self._corrected_parts = []
        self._resume_path = ""
        # Parametri "attivi" del turno in corso, usati per persistere lo stato.
        self._active_source_hash = ""
        self._active_chunk_size = 2000
        self._active_chunk_overlap = 200
        self._active_provider = ""
        self._active_model = ""

        self._build_ui()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        row_file = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label=_("Open file"))
        row_file.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.txt_path = wx.TextCtrl(self, style=wx.TE_READONLY, size=(500, -1))
        row_file.Add(self.txt_path, 1, wx.EXPAND)
        sizer.Add(row_file, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Original text:")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_original = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        sizer.Add(self.txt_original, 1, wx.EXPAND | wx.ALL, 5)

        row_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(self, label=_("Start correction"))
        row_btns.Add(self.btn_start, 0, wx.RIGHT, 5)
        self.btn_resume = wx.Button(self, label=_("Resume correction"))
        self.btn_resume.Enable(False)
        row_btns.Add(self.btn_resume, 0, wx.RIGHT, 5)
        self.btn_stop = wx.Button(self, label=_("Stop"))
        self.btn_stop.Enable(False)
        row_btns.Add(self.btn_stop, 0)
        sizer.Add(row_btns, 0, wx.ALL, 5)

        self.progress = wx.Gauge(self, range=100)
        self.lbl_progress = wx.StaticText(self, label="")
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        sizer.Add(self.lbl_progress, 0, wx.LEFT | wx.BOTTOM, 5)

        sizer.Add(wx.StaticText(self, label=_("Corrected text:")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_result = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        sizer.Add(self.txt_result, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label=_("Save result"))
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)

        self.btn_open.Bind(wx.EVT_BUTTON, self._on_open)
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start_correction)
        self.btn_resume.Bind(wx.EVT_BUTTON, self._on_resume_correction)
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _speak(self, text: str):
        announce(text)

    # ------------------------------------------------------------------
    # Persistenza dello stato di ripresa
    # ------------------------------------------------------------------
    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _resume_path_for(self, path: str) -> str:
        return path + RESUME_SUFFIX if path else ""

    def _write_resume_state(self, corrected_parts, total):
        """Persiste su disco i chunk gia' corretti (chiamato dal worker)."""
        if not self._resume_path:
            return
        state = {
            "version": RESUME_VERSION,
            "source_hash": self._active_source_hash,
            "chunk_size": self._active_chunk_size,
            "chunk_overlap": self._active_chunk_overlap,
            "total_chunks": total,
            "provider": self._active_provider,
            "model": self._active_model,
            # Copia difensiva: la lista viene mutata dal loop di correzione.
            "corrected_parts": list(corrected_parts),
        }
        try:
            tmp = self._resume_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, self._resume_path)
        except OSError:
            # La ripresa è un extra: se il disco non è scrivibile, si prosegue.
            pass

    def _delete_resume_state(self):
        if self._resume_path and os.path.exists(self._resume_path):
            try:
                os.remove(self._resume_path)
            except OSError:
                pass
        self._corrected_parts = []
        self.btn_resume.Enable(False)

    def _load_resume_state(self):
        """Carica lo stato di ripresa se compatibile col file corrente.

        Ritorna la tupla (corrected_parts, total) se c'è del lavoro da riprendere,
        altrimenti None.
        """
        path = self._resume_path
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return None
        if state.get("version") != RESUME_VERSION:
            return None
        if state.get("source_hash") != self._hash_text(self.loaded_text):
            # Il documento è cambiato: lo stato non è più valido.
            return None
        parts = state.get("corrected_parts") or []
        total = state.get("total_chunks") or 0
        if not parts or total <= 0 or len(parts) >= total:
            return None
        return parts, total, state

    def _check_resume_available(self):
        """Dopo l'apertura di un file, valuta se offrire la ripresa."""
        loaded = self._load_resume_state()
        if not loaded:
            self._corrected_parts = []
            self.btn_resume.Enable(False)
            return
        parts, total, state = loaded
        self._corrected_parts = list(parts)
        self.btn_resume.Enable(True)
        msg = _("Resume available: {done} of {total} chunks already corrected.").format(
            done=len(parts), total=total
        )
        self.lbl_progress.SetLabel(msg)
        self._speak(msg)
        self.main_frame.set_status(msg)

    # ------------------------------------------------------------------
    def _on_open(self, _event):
        dlg = wx.FileDialog(
            self,
            _("Open file to correct"),
            wildcard=_("Text file (*.txt)|*.txt|Word document (*.docx)|*.docx"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path = dlg.GetPath()
            self.txt_path.SetValue(self.file_path)
            try:
                self.loaded_text = read_file(self.file_path)
                self.txt_original.SetValue(self.loaded_text)
                self.corrected_text = ""
                self.txt_result.SetValue("")
                self._stream_display_len = 0
                self._resume_path = self._resume_path_for(self.file_path)
                self._speak(_("File loaded: {path}").format(path=self.file_path))
                self.main_frame.set_status(_("File loaded: {path}").format(path=self.file_path))
                self._check_resume_available()
            except Exception as e:
                wx.MessageBox(
                    _("Error reading: {e}").format(e=e),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
                self.loaded_text = ""
                self.txt_original.SetValue("")
                self._resume_path = ""
                self._corrected_parts = []
                self.btn_resume.Enable(False)
        dlg.Destroy()

    def _on_start_correction(self, _event):
        # Avvio da capo: scarta qualsiasi stato di ripresa precedente.
        self._delete_resume_state()
        self._start_correction(resume=False)

    def _on_resume_correction(self, _event):
        self._start_correction(resume=True)

    def _start_correction(self, resume: bool):
        if self._busy:
            return
        if not self.loaded_text:
            wx.MessageBox(
                _("Please load a text file first."),
                _("Warning"),
                wx.OK | wx.ICON_WARNING,
            )
            return

        config = self.main_frame.settings_panel.get_config()

        if resume:
            corrected_parts = self._corrected_parts
            # In ripresa usiamo il chunking ORIGINALE, così i chunk coincidono.
            chunk_size = self._active_chunk_size
            chunk_overlap = self._active_chunk_overlap
            self.corrected_text = "\n\n".join(corrected_parts)
            self.txt_result.SetValue(self.corrected_text)
            self._stream_display_len = len(self.corrected_text)
        else:
            corrected_parts = []
            self._corrected_parts = corrected_parts
            chunk_size = config.get("chunk_size", 2000)
            chunk_overlap = config.get("chunk_overlap", 200)
            self.corrected_text = ""
            self.txt_result.SetValue("")
            self._stream_display_len = 0

        # Parametri attivi per la persistenza dello stato.
        self._active_source_hash = self._hash_text(self.loaded_text)
        self._active_chunk_size = chunk_size
        self._active_chunk_overlap = chunk_overlap
        self._active_provider = config.get("llm_provider", "ollama")
        if self._active_provider == "gemini":
            self._active_model = config.get("gemini_model", "")
        else:
            self._active_model = config.get("ollama_model", "")

        self._busy = True
        self._cancel_event.clear()
        self.btn_start.Enable(False)
        self.btn_resume.Enable(False)
        self.btn_stop.Enable(True)
        self.btn_save.Enable(False)
        self.progress.SetValue(0)
        if resume:
            self.lbl_progress.SetLabel(_("Resuming correction..."))
            self._speak(_("Resuming correction."))
        else:
            self.lbl_progress.SetLabel(_("Starting correction..."))
            self._speak(_("Starting correction."))
        self.main_frame.set_status(_("Correction in progress..."))

        text = self.loaded_text
        cancel = self._cancel_event
        streaming = self.main_frame.streaming_text

        def _run():
            try:
                engine = create_engine(config)
                chunker = TextChunker(
                    max_tokens=chunk_size,
                    overlap_tokens=chunk_overlap,
                )

                def on_progress(current, total):
                    wx.CallAfter(self._update_progress, current, total)

                def on_retry(chunk_num, attempt, wait_s):
                    wx.CallAfter(self._on_retry_notice, chunk_num, attempt, wait_s)

                on_chunk = None
                if streaming:
                    def on_chunk(accumulated):
                        wx.CallAfter(self._stream_correction, accumulated)

                result = engine.correct_document(
                    text, chunker,
                    on_progress=on_progress,
                    cancel_event=cancel,
                    on_chunk=on_chunk,
                    corrected_parts=corrected_parts,
                    on_retry=on_retry,
                    on_checkpoint=self._write_resume_state,
                )
                wx.CallAfter(self._correction_done, result)
            except InterruptedError:
                wx.CallAfter(self._correction_cancelled)
            except Exception as e:
                wx.CallAfter(self._correction_error, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _update_progress(self, current, total):
        pct = int(current * 100 / total) if total else 0
        self.progress.SetValue(pct)
        msg = _("Chunk {current} of {total}").format(current=current, total=total)
        self.lbl_progress.SetLabel(msg)
        self.main_frame.set_status(msg)
        if self.main_frame.verbose_progress:
            self._speak(msg)

    def _on_retry_notice(self, chunk_num, attempt, wait_s):
        msg = _(
            "Rate limit reached on chunk {chunk} (attempt {attempt}). "
            "Retrying in {seconds} s..."
        ).format(chunk=chunk_num, attempt=attempt, seconds=int(wait_s))
        self.lbl_progress.SetLabel(msg)
        self.main_frame.set_status(msg)
        self._speak(msg)

    def _on_stop(self, _event):
        if self._busy:
            self._cancel_event.set()
            self.btn_stop.Enable(False)
            self.lbl_progress.SetLabel(_("Cancellation in progress..."))
            self._speak(_("Cancellation in progress."))

    def _stream_correction(self, accumulated: str):
        self.corrected_text = accumulated
        delta = accumulated[self._stream_display_len:]
        if delta:
            self.txt_result.AppendText(delta)
            self._stream_display_len = len(accumulated)

    def _correction_done(self, result: str):
        self._busy = False
        self.corrected_text = result
        self.txt_result.SetValue(result)
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.btn_save.Enable(True)
        self.progress.SetValue(100)
        self.lbl_progress.SetLabel(_("Correction completed."))
        self.main_frame.set_status(_("Correction completed."))
        self._speak(_("Correction completed."))
        # Documento completato: lo stato di ripresa non serve più.
        self._delete_resume_state()

    def _show_partial_result(self):
        """Mostra nel riquadro i chunk già corretti (utile senza streaming)."""
        if self._corrected_parts and not self.corrected_text:
            self.corrected_text = "\n\n".join(self._corrected_parts)
            self.txt_result.SetValue(self.corrected_text)
            self._stream_display_len = len(self.corrected_text)

    def _correction_cancelled(self):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Correction cancelled."))
        self.main_frame.set_status(_("Correction cancelled by user."))
        self._speak(_("Correction cancelled."))
        self._show_partial_result()
        if self.corrected_text:
            self.btn_save.Enable(True)
        self._offer_resume_after_stop()

    def _correction_error(self, error: str):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Error: {error}").format(error=error))
        self.main_frame.set_status(_("Correction error: {error}").format(error=error))
        self._show_partial_result()
        if self.corrected_text:
            self.btn_save.Enable(True)
        resumable = self._offer_resume_after_stop()
        if resumable:
            wx.MessageBox(
                _(
                    "Correction interrupted:\n{error}\n\n"
                    "{done} of {total} chunks were saved. "
                    "Use 'Resume correction' to continue from where it stopped."
                ).format(
                    error=error,
                    done=len(self._corrected_parts),
                    total=self._active_total_or_unknown(),
                ),
                _("Correction interrupted"),
                wx.OK | wx.ICON_WARNING,
            )
            self._speak(_("Correction interrupted. Resume available."))
        else:
            self._speak(_("Correction error: {error}").format(error=error))
            wx.MessageBox(
                _("Error during correction:\n{error}").format(error=error),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )

    def _active_total_or_unknown(self):
        loaded = self._load_resume_state()
        if loaded:
            return loaded[1]
        return "?"

    def _offer_resume_after_stop(self) -> bool:
        """Abilita la ripresa se ci sono chunk corretti persistiti. Ritorna True se sì."""
        if self._corrected_parts and self._resume_path and os.path.exists(self._resume_path):
            self.btn_resume.Enable(True)
            return True
        self.btn_resume.Enable(False)
        return False

    def _on_save(self, _event):
        if not self.corrected_text:
            return

        dlg = wx.FileDialog(
            self,
            _("Save corrected text"),
            wildcard=_("Text file (*.txt)|*.txt|Word document (*.docx)|*.docx"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                write_file(self.corrected_text, path)
                self.main_frame.set_status(_("Saved: {path}").format(path=path))
                self._speak(_("File saved."))
            except Exception as e:
                wx.MessageBox(
                    _("Error saving: {e}").format(e=e),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
        dlg.Destroy()
