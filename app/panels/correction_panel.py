"""Tab Correction: OCR text correction via LLM."""

import threading

import wx

from app.engine.chunker import TextChunker
from app.engine.llm_engine import create_engine
from app.formats.input_reader import read_file
from app.formats.output_writer import write_file
from app.i18n import _
from app.speech import announce


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
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _speak(self, text: str):
        announce(text)

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
                self._speak(_("File loaded: {path}").format(path=self.file_path))
                self.main_frame.set_status(_("File loaded: {path}").format(path=self.file_path))
            except Exception as e:
                wx.MessageBox(
                    _("Error reading: {e}").format(e=e),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
                self.loaded_text = ""
                self.txt_original.SetValue("")
        dlg.Destroy()

    def _on_start_correction(self, _event):
        if self._busy:
            return
        if not self.loaded_text:
            wx.MessageBox(
                _("Please load a text file first."),
                _("Warning"),
                wx.OK | wx.ICON_WARNING,
            )
            return

        self._busy = True
        self._cancel_event.clear()
        self.btn_start.Enable(False)
        self.btn_stop.Enable(True)
        self.btn_save.Enable(False)
        self.txt_result.SetValue("")
        self._stream_display_len = 0
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Starting correction..."))
        self._speak(_("Starting correction."))
        self.main_frame.set_status(_("Correction in progress..."))

        config = self.main_frame.settings_panel.get_config()
        text = self.loaded_text
        cancel = self._cancel_event
        streaming = self.main_frame.streaming_text

        def _run():
            try:
                engine = create_engine(config)
                chunker = TextChunker(
                    max_tokens=config.get("chunk_size", 2000),
                    overlap_tokens=config.get("chunk_overlap", 200),
                )

                def on_progress(current, total):
                    wx.CallAfter(self._update_progress, current, total)

                on_chunk = None
                if streaming:
                    def on_chunk(accumulated):
                        wx.CallAfter(self._stream_correction, accumulated)

                result = engine.correct_document(
                    text, chunker,
                    on_progress=on_progress,
                    cancel_event=cancel,
                    on_chunk=on_chunk,
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

    def _correction_cancelled(self):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Correction cancelled."))
        self.main_frame.set_status(_("Correction cancelled by user."))
        self._speak(_("Correction cancelled."))
        if self.corrected_text:
            self.btn_save.Enable(True)

    def _correction_error(self, error: str):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Error: {error}").format(error=error))
        self.main_frame.set_status(_("Correction error: {error}").format(error=error))
        self._speak(_("Correction error: {error}").format(error=error))
        wx.MessageBox(
            _("Error during correction:\n{error}").format(error=error),
            _("Error"),
            wx.OK | wx.ICON_ERROR,
        )

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
