"""Tab Acquisition: OCR on images and PDFs."""

import re
import threading

import wx

from app.engine.ocr_engine import OCREngine
from app.engine.vlm_engine import VLMEngine, UnslothVLMEngine
from app.engine.windows_ocr_engine import WindowsOCREngine
from app.engine.apple_vision_engine import AppleVisionEngine
from app.engine.surya_engine import SuryaEngine
from app.engine.surya20_engine import Surya20Engine
from app.engine.chandra_engine import ChandraEngine
from app.formats.output_writer import strip_markup, write_file
from app.i18n import _
from app.speech import announce


def _join_hyphenated(text: str) -> str:
    """Join words broken by a hyphen at line end.

    E.g.: 'pa-\nrola' → 'parola', 'pa-<br>\nrola' → 'parola'
    Skips inline tags (e.g. <br>, </b>) that Surya inserts near the hyphen.
    """
    return re.sub(r'(\w)-(?:<[^>]+>)*\n(?:<[^>]+>)*(\w)', r'\1\2', text)


class OCRPanel(wx.Panel):
    def __init__(self, parent, main_frame):
        super().__init__(parent)
        self.main_frame = main_frame
        self.file_path = ""
        self.ocr_result = ""
        self._busy = False
        self._cancel_event = threading.Event()
        self._stream_display_len = 0
        self._last_html: str = ""
        self._last_blocks: list = []
        self._last_line_blocks: list = []
        self._last_angles: list = []

        self._build_ui()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        row_file = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label=_("Open file"))
        row_file.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.txt_path = wx.TextCtrl(self, style=wx.TE_READONLY, size=(500, -1))
        row_file.Add(self.txt_path, 1, wx.EXPAND)
        sizer.Add(row_file, 0, wx.EXPAND | wx.ALL, 5)

        row_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(self, label=_("Start OCR"))
        row_btns.Add(self.btn_start, 0, wx.RIGHT, 5)
        self.btn_stop = wx.Button(self, label=_("Stop"))
        self.btn_stop.Enable(False)
        row_btns.Add(self.btn_stop, 0)
        sizer.Add(row_btns, 0, wx.ALL, 5)

        self.progress = wx.Gauge(self, range=100)
        self.lbl_progress = wx.StaticText(self, label="")
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        sizer.Add(self.lbl_progress, 0, wx.LEFT | wx.BOTTOM, 5)

        sizer.Add(wx.StaticText(self, label=_("OCR Result:")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_result = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 250))
        sizer.Add(self.txt_result, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label=_("Save result"))
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)

        self.btn_open.Bind(wx.EVT_BUTTON, self._on_open)
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start_ocr)
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _speak(self, text: str):
        announce(text)

    def _on_open(self, _event):
        dlg = wx.FileDialog(
            self,
            _("Open file for OCR"),
            wildcard=_("Images and PDF (*.jpg;*.jpeg;*.png;*.pdf)|*.jpg;*.jpeg;*.png;*.pdf"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path = dlg.GetPath()
            self.txt_path.SetValue(self.file_path)
            self._speak(_("File selected: {path}").format(path=self.file_path))
            self.main_frame.set_status(_("File selected: {path}").format(path=self.file_path))
        dlg.Destroy()

    def _on_start_ocr(self, _event):
        if self._busy:
            return
        if not self.file_path:
            wx.MessageBox(_("Please select a file first."), _("Warning"), wx.OK | wx.ICON_WARNING)
            return

        self._busy = True
        self._cancel_event.clear()
        self.btn_start.Enable(False)
        self.btn_stop.Enable(True)
        self.btn_save.Enable(False)
        self.txt_result.SetValue("")
        self._stream_display_len = 0
        self._last_html = ""
        self._last_blocks = []
        self._last_line_blocks = []
        self._last_angles = []
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Starting OCR..."))
        self._speak(_("Starting OCR."))
        self.main_frame.set_status(_("OCR in progress..."))

        file_path = self.file_path
        cancel = self._cancel_event

        config = self.main_frame.settings_panel.get_config()
        ocr_engine = config.get("ocr_engine", "tesseract")
        lang = config.get("ocr_lang", "ita")
        streaming = self.main_frame.streaming_text

        def _run():
            try:
                def on_progress(current, total):
                    wx.CallAfter(self._update_progress, current, total)

                on_partial = None
                if streaming:
                    def on_partial(accumulated):
                        wx.CallAfter(self._stream_ocr, accumulated)

                if ocr_engine == "vlm":
                    engine = VLMEngine(
                        url=config.get("ollama_url", "http://localhost:11434"),
                        model=config.get("vlm_model", ""),
                        api_key=config.get("ollama_api_key", ""),
                        cloud=config.get("ollama_cloud", False),
                    )
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                elif ocr_engine == "unsloth_vlm":
                    engine = UnslothVLMEngine(
                        url=config.get("unsloth_url", "http://127.0.0.1:8888"),
                        model=config.get("unsloth_vlm_model", ""),
                        api_key=config.get("unsloth_api_key", ""),
                    )
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                elif ocr_engine == "windows":
                    engine = WindowsOCREngine()
                    result = engine.process(
                        file_path,
                        lang_tag=config.get("windows_ocr_lang", "it-IT"),
                        on_progress=on_progress,
                        cancel_event=cancel,
                        on_partial=on_partial,
                    )
                elif ocr_engine == "apple_vision":
                    engine = AppleVisionEngine()
                    result = engine.process(
                        file_path,
                        lang=lang,
                        on_progress=on_progress,
                        cancel_event=cancel,
                        on_partial=on_partial,
                        use_language_correction=config.get(
                            "apple_vision_language_correction", True
                        ),
                    )
                elif ocr_engine == "surya":
                    engine = SuryaEngine(python_exe=config.get("surya_python", ""))
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                elif ocr_engine == "surya20":
                    engine = Surya20Engine.get_daemon() or Surya20Engine(python_exe=config.get("surya20_python", ""))
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                elif ocr_engine == "chandra":
                    engine = ChandraEngine.get_daemon() or ChandraEngine(python_exe=config.get("chandra_python", ""))
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                else:
                    engine = OCREngine()
                    result = engine.process(file_path, lang=lang, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)

                last_html = getattr(engine, '_last_html', '')
                last_blocks = getattr(engine, '_last_blocks', [])
                last_line_blocks = getattr(engine, '_last_line_blocks', [])
                last_angles = getattr(engine, '_last_angles', [])
                wx.CallAfter(self._ocr_done, result, last_html, last_blocks,
                             last_line_blocks, last_angles)
            except InterruptedError:
                wx.CallAfter(self._ocr_cancelled)
            except Exception as e:
                wx.CallAfter(self._ocr_error, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _update_progress(self, current, total):
        pct = int(current * 100 / total) if total else 0
        self.progress.SetValue(pct)
        msg = _("Page {current} of {total}").format(current=current, total=total)
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

    def _stream_ocr(self, accumulated: str):
        self.ocr_result = accumulated
        display = strip_markup(accumulated)
        delta = display[self._stream_display_len:]
        if delta:
            self.txt_result.AppendText(delta)
            self._stream_display_len = len(display)

    def _ocr_done(self, result: str, last_html: str = "", last_blocks=None,
                  last_line_blocks=None, last_angles=None):
        self._busy = False
        config = self.main_frame.settings_panel.get_config()
        if config.get("join_hyphenated", False):
            result = _join_hyphenated(result)
        self.ocr_result = result
        self._last_html = last_html
        self._last_blocks = last_blocks if last_blocks is not None else []
        self._last_line_blocks = (
            last_line_blocks if last_line_blocks is not None else [])
        self._last_angles = last_angles if last_angles is not None else []
        self.txt_result.SetValue(strip_markup(result))
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.btn_save.Enable(True)
        self.progress.SetValue(100)
        self.lbl_progress.SetLabel(_("OCR completed."))
        self.main_frame.set_status(_("OCR completed."))
        self._speak(_("OCR completed."))

    def _ocr_cancelled(self):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("OCR cancelled."))
        self.main_frame.set_status(_("OCR cancelled by user."))
        self._speak(_("OCR cancelled."))
        if self.ocr_result:
            self.btn_save.Enable(True)

    def _ocr_error(self, error: str):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(_("Error: {error}").format(error=error))
        self.main_frame.set_status(_("OCR error: {error}").format(error=error))
        self._speak(_("OCR error: {error}").format(error=error))
        wx.MessageBox(
            _("Error during OCR:\n{error}").format(error=error),
            _("Error"),
            wx.OK | wx.ICON_ERROR,
        )

    def _on_save(self, _event):
        if not self.ocr_result:
            return

        if self._last_html:
            wildcard = _("Text file (*.txt)|*.txt|Word document (*.docx)|*.docx|HTML document (*.html)|*.html|Searchable PDF (*.pdf)|*.pdf")
        else:
            wildcard = _("Text file (*.txt)|*.txt|Word document (*.docx)|*.docx|Searchable PDF (*.pdf)|*.pdf")

        dlg = wx.FileDialog(
            self,
            _("Save OCR result"),
            wildcard=wildcard,
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        result = dlg.ShowModal()
        path = dlg.GetPath()
        dlg.Destroy()
        if result != wx.ID_OK:
            return

        ocr_result = self.ocr_result
        source_path = self.file_path
        blocks = self._last_blocks or None
        line_blocks = self._last_line_blocks or None
        html = self._last_html
        angles = self._last_angles or None

        self.btn_save.Enable(False)
        self.main_frame.set_status(_("Saving: {path}").format(path=path))

        def _run():
            try:
                write_file(
                    ocr_result, path,
                    source_path=source_path,
                    blocks=blocks,
                    html=html,
                    line_blocks=line_blocks,
                    angles=angles,
                )
                wx.CallAfter(self._save_done, path)
            except Exception as e:
                wx.CallAfter(self._save_error, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _save_done(self, path):
        self.btn_save.Enable(True)
        self.main_frame.set_status(_("Saved: {path}").format(path=path))
        self._speak(_("File saved."))

    def _save_error(self, error: str):
        self.btn_save.Enable(True)
        self.main_frame.set_status(_("Error saving: {e}").format(e=error))
        wx.MessageBox(
            _("Error saving: {e}").format(e=error),
            _("Error"),
            wx.OK | wx.ICON_ERROR,
        )
