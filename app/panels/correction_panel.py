"""Tab Correzione: correzione testo OCR tramite LLM."""

import threading

import wx

from app.engine.chunker import TextChunker
from app.engine.llm_engine import create_engine
from app.formats.input_reader import read_file
from app.formats.output_writer import write_file


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

        # ---- Selezione file ----
        row_file = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label="Apri file")
        row_file.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.txt_path = wx.TextCtrl(self, style=wx.TE_READONLY, size=(500, -1))
        row_file.Add(self.txt_path, 1, wx.EXPAND)
        sizer.Add(row_file, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Anteprima testo caricato ----
        sizer.Add(wx.StaticText(self, label="Testo originale:"), 0, wx.LEFT | wx.TOP, 5)
        self.txt_original = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        sizer.Add(self.txt_original, 1, wx.EXPAND | wx.ALL, 5)

        # ---- Pulsanti Avvia / Interrompi ----
        row_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(self, label="Avvia correzione")
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

        # ---- Risultato corretto ----
        sizer.Add(wx.StaticText(self, label="Testo corretto:"), 0, wx.LEFT | wx.TOP, 5)
        self.txt_result = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        sizer.Add(self.txt_result, 1, wx.EXPAND | wx.ALL, 5)

        # ---- Salva ----
        self.btn_save = wx.Button(self, label="Salva risultato")
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)

        # ---- Bind ----
        self.btn_open.Bind(wx.EVT_BUTTON, self._on_open)
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start_correction)
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _speak(self, text: str):
        """Annuncia testo tramite screen reader."""
        try:
            import accessible_output2.outputs.auto as ao
            output = ao.Auto()
            output.speak(text)
        except Exception:
            pass

    def _on_open(self, _event):
        dlg = wx.FileDialog(
            self,
            "Seleziona file da correggere",
            wildcard="File di testo (*.txt)|*.txt|Documento Word (*.docx)|*.docx",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path = dlg.GetPath()
            self.txt_path.SetValue(self.file_path)
            try:
                self.loaded_text = read_file(self.file_path)
                self.txt_original.SetValue(self.loaded_text)
                self._speak(f"File caricato: {self.file_path}")
                self.main_frame.set_status(f"File caricato: {self.file_path}")
            except Exception as e:
                wx.MessageBox(f"Errore nella lettura: {e}", "Errore", wx.OK | wx.ICON_ERROR)
                self.loaded_text = ""
                self.txt_original.SetValue("")
        dlg.Destroy()

    def _on_start_correction(self, _event):
        if self._busy:
            return
        if not self.loaded_text:
            wx.MessageBox("Carica prima un file di testo.", "Attenzione", wx.OK | wx.ICON_WARNING)
            return

        self._busy = True
        self._cancel_event.clear()
        self.btn_start.Enable(False)
        self.btn_stop.Enable(True)
        self.btn_save.Enable(False)
        self.txt_result.SetValue("")
        self._stream_display_len = 0
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel("Avvio correzione...")
        self._speak("Avvio correzione.")
        self.main_frame.set_status("Correzione in corso...")

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
        msg = f"Chunk {current} di {total}"
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

    def _stream_correction(self, accumulated: str):
        """Aggiunge al campo di testo solo il delta nuovo (streaming senza reset cursore)."""
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
        self.lbl_progress.SetLabel("Correzione completata.")
        self.main_frame.set_status("Correzione completata.")
        self._speak("Correzione completata.")

    def _correction_cancelled(self):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel("Correzione interrotta.")
        self.main_frame.set_status("Correzione interrotta dall'utente.")
        self._speak("Correzione interrotta.")
        if self.corrected_text:
            self.btn_save.Enable(True)

    def _correction_error(self, error: str):
        self._busy = False
        self.btn_start.Enable(True)
        self.btn_stop.Enable(False)
        self.progress.SetValue(0)
        self.lbl_progress.SetLabel(f"Errore: {error}")
        self.main_frame.set_status(f"Errore correzione: {error}")
        self._speak(f"Errore correzione: {error}")
        wx.MessageBox(f"Errore durante la correzione:\n{error}", "Errore", wx.OK | wx.ICON_ERROR)

    def _on_save(self, _event):
        if not self.corrected_text:
            return

        dlg = wx.FileDialog(
            self,
            "Salva testo corretto",
            wildcard="File di testo (*.txt)|*.txt|Documento Word (*.docx)|*.docx",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                write_file(self.corrected_text, path)
                self.main_frame.set_status(f"Salvato: {path}")
                self._speak("File salvato.")
            except Exception as e:
                wx.MessageBox(f"Errore nel salvataggio: {e}", "Errore", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()
