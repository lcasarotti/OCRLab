"""Tab Acquisizione: OCR su immagini e PDF."""

import re
import threading

import wx

from app.engine.ocr_engine import OCREngine
from app.engine.vlm_engine import VLMEngine
from app.engine.windows_ocr_engine import WindowsOCREngine
from app.engine.surya_engine import SuryaEngine
from app.engine.chandra_engine import ChandraEngine
from app.formats.output_writer import strip_markup, write_file
from app.speech import announce


def _join_hyphenated(text: str) -> str:
    """Unisce le parole spezzate da un trattino a fine riga.

    Es.: 'pa-\nrola' → 'parola', 'pa-<br>\nrola' → 'parola'
    Salta i tag inline (es. <br>, </b>) che Surya inserisce vicino al trattino.
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

        self._build_ui()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Selezione file ----
        row_file = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label="Apri file")
        row_file.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.txt_path = wx.TextCtrl(self, style=wx.TE_READONLY, size=(500, -1))
        row_file.Add(self.txt_path, 1, wx.EXPAND)
        sizer.Add(row_file, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Pulsanti Avvia / Interrompi ----
        row_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(self, label="Avvia OCR")
        row_btns.Add(self.btn_start, 0, wx.RIGHT, 5)
        self.btn_stop = wx.Button(self, label="Interrompi")
        self.btn_stop.Enable(False)
        row_btns.Add(self.btn_stop, 0)
        sizer.Add(row_btns, 0, wx.ALL, 5)

        # ---- Progress bar ----
        self.progress = wx.Gauge(self, range=100)
        self.lbl_progress = wx.StaticText(self, label="")
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        sizer.Add(self.lbl_progress, 0, wx.LEFT | wx.BOTTOM, 5)

        # ---- Anteprima risultato ----
        sizer.Add(wx.StaticText(self, label="Risultato OCR:"), 0, wx.LEFT | wx.TOP, 5)
        self.txt_result = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 250))
        sizer.Add(self.txt_result, 1, wx.EXPAND | wx.ALL, 5)

        # ---- Salva ----
        self.btn_save = wx.Button(self, label="Salva risultato")
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)

        # ---- Bind ----
        self.btn_open.Bind(wx.EVT_BUTTON, self._on_open)
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start_ocr)
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _speak(self, text: str):
        """Annuncia testo (eventi di background). Vedi app/speech.py."""
        announce(text)

    def _on_open(self, _event):
        dlg = wx.FileDialog(
            self,
            "Seleziona file per OCR",
            wildcard="Immagini e PDF (*.jpg;*.jpeg;*.png;*.pdf)|*.jpg;*.jpeg;*.png;*.pdf",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path = dlg.GetPath()
            self.txt_path.SetValue(self.file_path)
            self._speak(f"File selezionato: {self.file_path}")
            self.main_frame.set_status(f"File selezionato: {self.file_path}")
        dlg.Destroy()

    def _on_start_ocr(self, _event):
        if self._busy:
            return
        if not self.file_path:
            wx.MessageBox("Seleziona prima un file.", "Attenzione", wx.OK | wx.ICON_WARNING)
            return

        self._busy = True
        self._cancel_event.clear()
        self.btn_start.Enable(False)
        self.btn_stop.Enable(True)
        self.btn_save.Enable(False)
        self.txt_result.SetValue("")
        self._stream_display_len = 0
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel("Avvio OCR...")
        self._speak("Avvio OCR.")
        self.main_frame.set_status("OCR in corso...")

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
                elif ocr_engine == "windows":
                    engine = WindowsOCREngine()
                    result = engine.process(
                        file_path,
                        lang_tag=config.get("windows_ocr_lang", "it-IT"),
                        on_progress=on_progress,
                        cancel_event=cancel,
                        on_partial=on_partial,
                    )
                elif ocr_engine == "surya":
                    engine = SuryaEngine(python_exe=config.get("surya_python", ""))
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                elif ocr_engine == "chandra":
                    engine = ChandraEngine(
                        method=config.get("chandra_method", "vllm"),
                        python_exe=config.get("chandra_python", ""),
                        vllm_url=config.get("chandra_vllm_url", "http://localhost:8000"),
                    )
                    result = engine.process(file_path, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)
                else:
                    engine = OCREngine()
                    result = engine.process(file_path, lang=lang, on_progress=on_progress,
                                            cancel_event=cancel, on_partial=on_partial)

                wx.CallAfter(self._ocr_done, result)
            except InterruptedError:
                wx.CallAfter(self._ocr_cancelled)
            except Exception as e:
                wx.CallAfter(self._ocr_error, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _update_progress(self, current, total):
        pct = int(current * 100 / total) if total else 0
        self.progress.SetValue(pct)
        msg = f"Pagina {current} di {total}"
        self.lbl_progress.SetLabel(msg)
        self.main_frame.set_status(msg)
        if self.main_frame.verbose_progress:
            self._speak(msg)

    def _on_stop(self, _event):
        if self._busy:
            self._cancel_event.set()
            self.btn_stop.Enable(False)
            self.lbl_progress.SetLabel("Interruzione in corso...")
            self._speak("Interruzione in corso.")

    def _stream_ocr(self, accumulated: str):
        """Aggiunge al campo di testo solo il delta nuovo (streaming senza reset cursore)."""
        self.ocr_result = accumulated
        display = strip_markup(accumulated)
        delta = display[self._stream_display_len:]
        if delta:
            self.txt_result.AppendText(delta)
            self._stream_display_len = len(display)

    def _ocr_done(self, result: str):
        self._busy = False
        config = self.main_frame.settings_panel.get_config()
        if config.get("join_hyphenated", False):
            result = _join_hyphenated(result)
        self.ocr_result = result          # testo grezzo con tag (usato al salvataggio)
        self.txt_result.SetValue(strip_markup(result))  # anteprima senza tag
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.btn_save.Enable(True)
        self.progress.SetValue(100)
        self.lbl_progress.SetLabel("OCR completato.")
        self.main_frame.set_status("OCR completato.")
        self._speak("OCR completato.")

    def _ocr_cancelled(self):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel("OCR interrotto.")
        self.main_frame.set_status("OCR interrotto dall'utente.")
        self._speak("OCR interrotto.")
        if self.ocr_result:
            self.btn_save.Enable(True)

    def _ocr_error(self, error: str):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(f"Errore: {error}")
        self.main_frame.set_status(f"Errore OCR: {error}")
        self._speak(f"Errore OCR: {error}")
        wx.MessageBox(f"Errore durante l'OCR:\n{error}", "Errore", wx.OK | wx.ICON_ERROR)

    def _on_save(self, _event):
        if not self.ocr_result:
            return

        dlg = wx.FileDialog(
            self,
            "Salva risultato OCR",
            wildcard=(
                "File di testo (*.txt)|*.txt"
                "|Documento Word (*.docx)|*.docx"
                "|PDF ricercabile (*.pdf)|*.pdf"
            ),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                write_file(self.ocr_result, path, source_path=self.file_path)
                self.main_frame.set_status(f"Salvato: {path}")
                self._speak("File salvato.")
            except Exception as e:
                wx.MessageBox(f"Errore nel salvataggio: {e}", "Errore", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

