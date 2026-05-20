"""Banco di prova wxPython per valutare l'accessibilita' VoiceOver su macOS.

Rispecchia la struttura di OCR Lab (3 tab + menu + status bar + accelerators)
ma senza logica OCR. Sostituisce accessible_output2 con il comando macOS `say`.

Eseguire con:
    cd ~/projects/OCRLab/mac-port-test
    .venv/bin/python voiceover_test.py
"""

import subprocess

import wx


ID_OPEN_FILE = wx.NewIdRef()
ID_SAVE = wx.NewIdRef()
ID_START = wx.NewIdRef()
ID_STOP = wx.NewIdRef()
ID_VERBOSE = wx.NewIdRef()
ID_STREAM = wx.NewIdRef()


def speak(text: str) -> None:
    """Annuncio vocale via comando macOS `say` (non bloccante)."""
    try:
        subprocess.Popen(["say", text])
    except Exception:
        pass


class OCRPanelStub(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        row_file = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label="Apri file")
        row_file.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.txt_path = wx.TextCtrl(self, style=wx.TE_READONLY, size=(500, -1))
        row_file.Add(self.txt_path, 1, wx.EXPAND)
        sizer.Add(row_file, 0, wx.EXPAND | wx.ALL, 5)

        row_lang = wx.BoxSizer(wx.HORIZONTAL)
        row_lang.Add(wx.StaticText(self, label="Lingua:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_lang = wx.ComboBox(
            self,
            choices=["Italiano", "Inglese", "Francese", "Tedesco", "Spagnolo"],
            style=wx.CB_READONLY,
        )
        self.cmb_lang.SetSelection(0)
        row_lang.Add(self.cmb_lang, 0)
        row_lang.Add(wx.StaticText(self, label="  Motore:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 5)
        self.cmb_engine = wx.ComboBox(
            self,
            choices=["Tesseract", "Surya", "Apple Vision"],
            style=wx.CB_READONLY,
        )
        self.cmb_engine.SetSelection(0)
        row_lang.Add(self.cmb_engine, 0)
        sizer.Add(row_lang, 0, wx.EXPAND | wx.ALL, 5)

        row_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(self, label="Avvia OCR")
        row_btns.Add(self.btn_start, 0, wx.RIGHT, 5)
        self.btn_stop = wx.Button(self, label="Interrompi")
        self.btn_stop.Enable(False)
        row_btns.Add(self.btn_stop, 0)
        sizer.Add(row_btns, 0, wx.ALL, 5)

        self.progress = wx.Gauge(self, range=100)
        self.lbl_progress = wx.StaticText(self, label="In attesa.")
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        sizer.Add(self.lbl_progress, 0, wx.LEFT | wx.BOTTOM, 5)

        sizer.Add(wx.StaticText(self, label="Risultato OCR:"), 0, wx.LEFT | wx.TOP, 5)
        self.txt_result = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(-1, 200),
            value="(Qui apparirebbe il testo riconosciuto.)",
        )
        sizer.Add(self.txt_result, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label="Salva risultato")
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)

        self.btn_open.Bind(wx.EVT_BUTTON, self._on_open)
        self.btn_start.Bind(wx.EVT_BUTTON, self._on_start)

    def _on_open(self, _event):
        speak("Pulsante Apri file premuto.")
        self.txt_path.SetValue("/percorso/di/esempio.pdf")

    def _on_start(self, _event):
        speak("Avvio simulato dell'OCR.")
        self.btn_stop.Enable(True)
        self.lbl_progress.SetLabel("Elaborazione pagina 1 di 1.")
        self.progress.SetValue(100)
        self.txt_result.SetValue("Testo riconosciuto di esempio.\nSeconda riga.")
        self.btn_save.Enable(True)


class CorrectionPanelStub(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label="Testo da correggere:"), 0, wx.ALL, 5)
        self.txt_input = wx.TextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 150))
        sizer.Add(self.txt_input, 1, wx.EXPAND | wx.ALL, 5)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="Provider LLM:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_provider = wx.ComboBox(
            self,
            choices=["Ollama (locale)", "Gemini"],
            style=wx.CB_READONLY,
        )
        self.cmb_provider.SetSelection(0)
        row.Add(self.cmb_provider, 0)
        sizer.Add(row, 0, wx.ALL, 5)

        row_btn = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self, label="Apri file")
        row_btn.Add(self.btn_open, 0, wx.RIGHT, 5)
        self.btn_start = wx.Button(self, label="Correggi")
        row_btn.Add(self.btn_start, 0, wx.RIGHT, 5)
        self.btn_stop = wx.Button(self, label="Interrompi")
        self.btn_stop.Enable(False)
        row_btn.Add(self.btn_stop, 0)
        sizer.Add(row_btn, 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label="Testo corretto:"), 0, wx.ALL, 5)
        self.txt_output = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        sizer.Add(self.txt_output, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label="Salva risultato")
        self.btn_save.Enable(False)
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)


class SettingsPanelStub(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        row1 = wx.BoxSizer(wx.HORIZONTAL)
        row1.Add(wx.StaticText(self, label="URL Ollama:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_ollama_url = wx.TextCtrl(self, value="http://localhost:11434", size=(300, -1))
        row1.Add(self.txt_ollama_url, 0)
        sizer.Add(row1, 0, wx.ALL, 5)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        row2.Add(wx.StaticText(self, label="API key Gemini:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_gemini = wx.TextCtrl(self, value="", size=(300, -1), style=wx.TE_PASSWORD)
        row2.Add(self.txt_gemini, 0)
        sizer.Add(row2, 0, wx.ALL, 5)

        self.chk_verbose = wx.CheckBox(self, label="Annuncia progresso")
        self.chk_verbose.SetValue(True)
        sizer.Add(self.chk_verbose, 0, wx.ALL, 5)

        self.chk_stream = wx.CheckBox(self, label="Aggiorna testo in tempo reale")
        sizer.Add(self.chk_stream, 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label="Modelli disponibili:"), 0, wx.LEFT | wx.TOP, 5)
        self.lst_models = wx.ListBox(self, choices=["llama3.1:8b", "qwen2.5:7b", "mistral:7b"])
        sizer.Add(self.lst_models, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label="Salva impostazioni")
        sizer.Add(self.btn_save, 0, wx.ALL, 5)

        self.SetSizer(sizer)


class TestFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="OCR Lab (Mac VoiceOver test)", size=(820, 620))

        self._build_menu()
        self._build_ui()
        self._bind_events()

        self.CreateStatusBar()
        self.SetStatusText("Pronto.")
        self.Centre()

    def _build_menu(self):
        bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(ID_OPEN_FILE, "Apri file\tCtrl+O")
        file_menu.Append(ID_SAVE, "Salva risultato\tCtrl+S")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Esci")
        bar.Append(file_menu, "&File")

        ops_menu = wx.Menu()
        ops_menu.Append(ID_START, "Avvia\tF5")
        ops_menu.Append(ID_STOP, "Interrompi\tEsc")
        ops_menu.AppendSeparator()
        self.mi_verbose = ops_menu.AppendCheckItem(ID_VERBOSE, "Annuncia progresso")
        self.mi_verbose.Check(True)
        self.mi_stream = ops_menu.AppendCheckItem(ID_STREAM, "Aggiorna testo in tempo reale")
        bar.Append(ops_menu, "&Operazioni")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "&Info")
        bar.Append(help_menu, "&?")

        self.SetMenuBar(bar)

    def _build_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.notebook = wx.Notebook(panel)
        self.ocr = OCRPanelStub(self.notebook)
        self.correction = CorrectionPanelStub(self.notebook)
        self.settings = SettingsPanelStub(self.notebook)
        self.notebook.AddPage(self.ocr, "Acquisizione")
        self.notebook.AddPage(self.correction, "Correzione")
        self.notebook.AddPage(self.settings, "Impostazioni")

        sizer.Add(self.notebook, 1, wx.EXPAND)
        panel.SetSizer(sizer)

    def _bind_events(self):
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page_changed)

        entries = []
        for key in ("1", "2", "3"):
            ref = wx.NewIdRef()
            entries.append(wx.AcceleratorEntry(wx.ACCEL_ALT, ord(key), ref))
            idx = int(key) - 1
            self.Bind(wx.EVT_MENU, lambda evt, i=idx: self._switch_tab(i), id=ref)
        self.SetAcceleratorTable(wx.AcceleratorTable(entries))

    def _switch_tab(self, index):
        if 0 <= index < self.notebook.GetPageCount():
            self.notebook.SetSelection(index)
            speak(self.notebook.GetPageText(index))

    def _on_page_changed(self, event):
        speak(self.notebook.GetPageText(event.GetSelection()))
        event.Skip()

    def _on_about(self, _event):
        wx.MessageBox(
            "OCR Lab (test VoiceOver su macOS).\n"
            "Banco di prova per valutare l'accessibilita' di wxPython su Mac.",
            "Info",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_exit(self, _event):
        self.Close()


def main():
    app = wx.App()
    frame = TestFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
