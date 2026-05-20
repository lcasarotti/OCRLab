"""Finestra principale con wx.Notebook a 3 tab."""

import wx

from app.config import load_config, save_config
from app.panels.ocr_panel import OCRPanel
from app.panels.correction_panel import CorrectionPanel
from app.panels.settings_panel import SettingsPanel

# ID menu operazioni
ID_OPEN_FILE = wx.NewIdRef()
ID_SAVE = wx.NewIdRef()
ID_START = wx.NewIdRef()
ID_STOP = wx.NewIdRef()
ID_VERBOSE_PROGRESS = wx.NewIdRef()
ID_STREAMING_TEXT = wx.NewIdRef()


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="OCR Lab", size=(800, 600))

        self._config = load_config()
        self.verbose_progress = self._config.get("verbose_progress", True)
        self.streaming_text = self._config.get("streaming_text", False)

        self._build_ui()
        self._build_menu()
        self._bind_events()

        self.CreateStatusBar()
        self.SetStatusText("Pronto.")
        self.Centre()

    # ---- Menu ----
    def _build_menu(self):
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(ID_OPEN_FILE, "Apri file\tCtrl+O")
        file_menu.Append(ID_SAVE, "Salva risultato\tCtrl+S")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Esci\tAlt+F4")
        menu_bar.Append(file_menu, "&File")

        ops_menu = wx.Menu()
        ops_menu.Append(ID_START, "Avvia\tF5")
        ops_menu.Append(ID_STOP, "Interrompi\tEsc")
        ops_menu.AppendSeparator()
        self.menu_verbose = ops_menu.AppendCheckItem(
            ID_VERBOSE_PROGRESS, "Annuncia progresso",
        )
        self.menu_verbose.Check(self.verbose_progress)
        self.menu_streaming = ops_menu.AppendCheckItem(
            ID_STREAMING_TEXT, "Aggiorna testo in tempo reale",
        )
        self.menu_streaming.Check(self.streaming_text)
        menu_bar.Append(ops_menu, "&Operazioni")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "&Info")
        menu_bar.Append(help_menu, "&?")

        self.SetMenuBar(menu_bar)

    # ---- UI ----
    def _build_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.notebook = wx.Notebook(panel)

        self.ocr_panel = OCRPanel(self.notebook, self)
        self.correction_panel = CorrectionPanel(self.notebook, self)
        self.settings_panel = SettingsPanel(self.notebook, self)

        self.notebook.AddPage(self.ocr_panel, "Acquisizione")
        self.notebook.AddPage(self.correction_panel, "Correzione")
        self.notebook.AddPage(self.settings_panel, "Impostazioni")

        sizer.Add(self.notebook, 1, wx.EXPAND)
        panel.SetSizer(sizer)

    # ---- Eventi ----
    def _bind_events(self):
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)
        self.Bind(wx.EVT_MENU, self._on_open_file, id=ID_OPEN_FILE)
        self.Bind(wx.EVT_MENU, self._on_save, id=ID_SAVE)
        self.Bind(wx.EVT_MENU, self._on_start, id=ID_START)
        self.Bind(wx.EVT_MENU, self._on_stop, id=ID_STOP)
        self.Bind(wx.EVT_MENU, self._on_toggle_verbose, id=ID_VERBOSE_PROGRESS)
        self.Bind(wx.EVT_MENU, self._on_toggle_streaming, id=ID_STREAMING_TEXT)
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page_changed)

        accel_entries = [
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord("1"), wx.NewIdRef()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord("2"), wx.NewIdRef()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord("3"), wx.NewIdRef()),
        ]
        for i, entry in enumerate(accel_entries):
            self.Bind(wx.EVT_MENU, lambda evt, idx=i: self._switch_tab(idx), id=entry.GetCommand())
        self.SetAcceleratorTable(wx.AcceleratorTable(accel_entries))

    def _get_active_panel(self):
        """Restituisce il pannello attivo (OCR o Correzione), o None se è Impostazioni."""
        page = self.notebook.GetSelection()
        if page == 0:
            return self.ocr_panel
        elif page == 1:
            return self.correction_panel
        return None

    def _on_open_file(self, _event):
        panel = self._get_active_panel()
        if panel:
            panel.btn_open.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.btn_open.GetId())
            )

    def _on_start(self, _event):
        panel = self._get_active_panel()
        if panel:
            panel.btn_start.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.btn_start.GetId())
            )

    def _on_save(self, _event):
        panel = self._get_active_panel()
        if panel and panel.btn_save.IsEnabled():
            panel.btn_save.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.btn_save.GetId())
            )

    def _on_stop(self, _event):
        panel = self._get_active_panel()
        if panel:
            panel.btn_stop.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.btn_stop.GetId())
            )

    def _speak(self, text: str):
        """Annuncia testo tramite screen reader."""
        try:
            import accessible_output2.outputs.auto as ao
            output = ao.Auto()
            output.speak(text)
        except Exception:
            pass

    def _announce_tab(self, index):
        """Annuncia il nome della tab via screen reader."""
        if 0 <= index < self.notebook.GetPageCount():
            name = self.notebook.GetPageText(index)
            self._speak(name)

    def _on_page_changed(self, event):
        """Gestisce il cambio tab (Ctrl+Tab, click, ecc.)."""
        self._announce_tab(event.GetSelection())
        event.Skip()

    def _switch_tab(self, index):
        if 0 <= index < self.notebook.GetPageCount():
            self.notebook.SetSelection(index)
            self._announce_tab(index)

    def _on_toggle_verbose(self, _event):
        self.verbose_progress = self.menu_verbose.IsChecked()
        self._config["verbose_progress"] = self.verbose_progress
        save_config(self._config)
        stato = "attivato" if self.verbose_progress else "disattivato"
        self._speak(f"Annuncio progresso {stato}.")

    def _on_toggle_streaming(self, _event):
        self.streaming_text = self.menu_streaming.IsChecked()
        self._config["streaming_text"] = self.streaming_text
        save_config(self._config)
        stato = "attivato" if self.streaming_text else "disattivato"
        self._speak(f"Testo in tempo reale {stato}.")

    def _on_exit(self, _event):
        self.Close()

    def _on_about(self, _event):
        wx.MessageBox(
            "OCR Lab\n\n"
            "Utility per acquisizione e correzione testi tramite OCR e LLM.\n"
            "Completamente accessibile via screen reader.",
            "Info",
            wx.OK | wx.ICON_INFORMATION,
        )

    def set_status(self, text: str):
        """Aggiorna la status bar (thread-safe via CallAfter)."""
        wx.CallAfter(self.SetStatusText, text)
