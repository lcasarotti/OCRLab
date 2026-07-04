"""Finestra principale con wx.Notebook a 3 tab."""

import os
import sys

import wx

from app.config import load_config, save_config, ENABLE_CHANDRA
from app.i18n import _, get_language, AVAILABLE_LANGUAGES
from app.panels.ocr_panel import OCRPanel
from app.panels.correction_panel import CorrectionPanel
from app.panels.settings_panel import SettingsPanel
from app.speech import announce, announce_focus, set_voice_announcements_enabled

_IS_MAC = sys.platform == "darwin"

# ID menu operazioni
ID_OPEN_FILE = wx.NewIdRef()
ID_SAVE = wx.NewIdRef()
ID_START = wx.NewIdRef()
ID_STOP = wx.NewIdRef()
ID_VERBOSE_PROGRESS = wx.NewIdRef()
ID_STREAMING_TEXT = wx.NewIdRef()
ID_VOICE_ANNOUNCEMENTS = wx.NewIdRef()
ID_INSTALL_SURYA = wx.NewIdRef()
ID_UNINSTALL_SURYA = wx.NewIdRef()
ID_INSTALL_SURYA20 = wx.NewIdRef()
ID_UNINSTALL_SURYA20 = wx.NewIdRef()
ID_INSTALL_CHANDRA = wx.NewIdRef()
ID_UNINSTALL_CHANDRA = wx.NewIdRef()
ID_INSTALL_TESSERACT = wx.NewIdRef()
ID_TESSERACT_LANGS = wx.NewIdRef()
ID_CHECK_UPDATES = wx.NewIdRef()
ID_AUTO_UPDATES = wx.NewIdRef()

# ID per le voci del sottomenù lingua (uno per lingua disponibile)
_LANG_IDS: dict[str, wx.WindowIDRef] = {
    lang: wx.NewIdRef() for lang in AVAILABLE_LANGUAGES
}


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="OCR Lab", size=(800, 600))

        self._config = load_config()
        self.verbose_progress = self._config.get("verbose_progress", True)
        self.streaming_text = self._config.get("streaming_text", False)
        self.voice_announcements = self._config.get("voice_announcements", True)
        set_voice_announcements_enabled(self.voice_announcements)

        self._build_ui()
        self._build_menu()
        self._bind_events()

        self.CreateStatusBar()
        self.SetStatusText(_("Ready."))
        self.Centre()

        if self._config.get("auto_check_updates", True):
            wx.CallLater(3000, self._auto_check_updates)

    # ---- Menu ----
    def _build_menu(self):
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(ID_OPEN_FILE, _("Open file") + "\tCtrl+O")
        file_menu.Append(ID_SAVE, _("Save result") + "\tCtrl+S")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, _("Exit") + "\tAlt+F4")
        menu_bar.Append(file_menu, _("&File"))

        ops_menu = wx.Menu()
        ops_menu.Append(ID_START, _("Start") + "\tF5")
        ops_menu.Append(ID_STOP, _("Stop") + "\tEsc")
        ops_menu.AppendSeparator()
        self.menu_verbose = ops_menu.AppendCheckItem(
            ID_VERBOSE_PROGRESS, _("Announce progress"),
        )
        self.menu_verbose.Check(self.verbose_progress)
        self.menu_streaming = ops_menu.AppendCheckItem(
            ID_STREAMING_TEXT, _("Update text in real-time"),
        )
        self.menu_streaming.Check(self.streaming_text)
        if _IS_MAC:
            self.menu_voice = ops_menu.AppendCheckItem(
                ID_VOICE_ANNOUNCEMENTS, _("System voice announcements"),
            )
            self.menu_voice.Check(self.voice_announcements)
        menu_bar.Append(ops_menu, _("&Operations"))

        tools_menu = wx.Menu()
        tools_menu.Append(ID_INSTALL_SURYA, _("Install Surya 0.1..."))
        tools_menu.Append(ID_UNINSTALL_SURYA, _("Uninstall Surya 0.1..."))
        tools_menu.AppendSeparator()
        tools_menu.Append(ID_INSTALL_SURYA20, _("Install Surya 0.2 (requires Docker / llama.cpp)..."))
        tools_menu.Append(ID_UNINSTALL_SURYA20, _("Uninstall Surya 0.2..."))
        tools_menu.AppendSeparator()
        if ENABLE_CHANDRA:
            tools_menu.Append(ID_INSTALL_CHANDRA, _("Install Chandra..."))
            tools_menu.Append(ID_UNINSTALL_CHANDRA, _("Uninstall Chandra..."))
            tools_menu.AppendSeparator()
        if not _IS_MAC:
            tools_menu.Append(ID_INSTALL_TESSERACT, _("Install Tesseract OCR..."))
        tools_menu.Append(ID_TESSERACT_LANGS, _("Tesseract OCR languages..."))
        tools_menu.AppendSeparator()

        # Sottomenù lingua interfaccia
        lang_menu = wx.Menu()
        current_lang = get_language()
        for lang_code, lang_label in AVAILABLE_LANGUAGES.items():
            item = lang_menu.AppendRadioItem(_LANG_IDS[lang_code], lang_label)
            if lang_code == current_lang:
                item.Check(True)
        tools_menu.AppendSubMenu(lang_menu, _("Interface language"))

        menu_bar.Append(tools_menu, _("&Tools"))

        help_menu = wx.Menu()
        help_menu.Append(ID_CHECK_UPDATES, _("Check for updates..."))
        self.menu_auto_updates = help_menu.AppendCheckItem(
            ID_AUTO_UPDATES, _("Check for updates at startup")
        )
        self.menu_auto_updates.Check(self._config.get("auto_check_updates", True))
        help_menu.AppendSeparator()
        help_menu.Append(wx.ID_ABOUT, _("&About"))
        menu_bar.Append(help_menu, _("&Help"))

        self.SetMenuBar(menu_bar)

    # ---- UI ----
    def _build_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.notebook = wx.Notebook(panel)

        self.ocr_panel = OCRPanel(self.notebook, self)
        self.correction_panel = CorrectionPanel(self.notebook, self)
        self.settings_panel = SettingsPanel(self.notebook, self)

        self.notebook.AddPage(self.ocr_panel, _("Acquisition"))
        self.notebook.AddPage(self.correction_panel, _("Correction"))
        self.notebook.AddPage(self.settings_panel, _("Settings"))

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
        if _IS_MAC:
            self.Bind(wx.EVT_MENU, self._on_toggle_voice, id=ID_VOICE_ANNOUNCEMENTS)
        self.Bind(wx.EVT_MENU, self._on_install_surya, id=ID_INSTALL_SURYA)
        self.Bind(wx.EVT_MENU, self._on_uninstall_surya, id=ID_UNINSTALL_SURYA)
        self.Bind(wx.EVT_MENU, self._on_install_surya20, id=ID_INSTALL_SURYA20)
        self.Bind(wx.EVT_MENU, self._on_uninstall_surya20, id=ID_UNINSTALL_SURYA20)
        if ENABLE_CHANDRA:
            self.Bind(wx.EVT_MENU, self._on_install_chandra, id=ID_INSTALL_CHANDRA)
            self.Bind(wx.EVT_MENU, self._on_uninstall_chandra, id=ID_UNINSTALL_CHANDRA)
        if not _IS_MAC:
            self.Bind(wx.EVT_MENU, self._on_install_tesseract, id=ID_INSTALL_TESSERACT)
        self.Bind(wx.EVT_MENU, self._on_tesseract_langs, id=ID_TESSERACT_LANGS)
        self.Bind(wx.EVT_MENU, self._on_check_updates, id=ID_CHECK_UPDATES)
        self.Bind(wx.EVT_MENU, self._on_toggle_auto_updates, id=ID_AUTO_UPDATES)
        for lang_code, lang_id in _LANG_IDS.items():
            self.Bind(
                wx.EVT_MENU,
                lambda evt, lc=lang_code: self._on_set_language(lc),
                id=lang_id,
            )
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
        announce(text)

    def _announce_tab(self, index):
        if 0 <= index < self.notebook.GetPageCount():
            name = self.notebook.GetPageText(index)
            announce_focus(name)

    def _on_page_changed(self, event):
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
        state = _("enabled") if self.verbose_progress else _("disabled")
        announce_focus(_("Progress announced {state}.").format(state=state))

    def _on_toggle_streaming(self, _event):
        self.streaming_text = self.menu_streaming.IsChecked()
        self._config["streaming_text"] = self.streaming_text
        save_config(self._config)
        state = _("enabled") if self.streaming_text else _("disabled")
        announce_focus(_("Real-time text {state}.").format(state=state))

    def _on_toggle_voice(self, _event):
        self.voice_announcements = self.menu_voice.IsChecked()
        self._config["voice_announcements"] = self.voice_announcements
        save_config(self._config)
        set_voice_announcements_enabled(self.voice_announcements)
        # Lo stato del check item viene letto da VoiceOver via accessibilita'
        # Cocoa, quindi qui non serve un announce esplicito (sarebbe rumore).
        state = _("enabled (f)") if self.voice_announcements else _("disabled (f)")
        announce_focus(_("System voice announcements {state}.").format(state=state))

    def _on_set_language(self, lang_code: str):
        if lang_code == get_language():
            return
        self._config["ui_language"] = lang_code
        save_config(self._config)
        wx.MessageBox(
            _("Language will be applied after restarting the application."),
            _("Restart required"),
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_install_surya(self, _event):
        from app.engine.surya_installer import is_surya_installed, SuryaInstallDialog
        if is_surya_installed():
            from app.engine.surya_installer import _SURYA_VENV_DIR
            venv_path = _SURYA_VENV_DIR
            wx.MessageBox(
                _("Surya 0.1 is already installed.\n\nVenv: {path}").format(path=venv_path),
                _("Surya 0.1"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        dlg = SuryaInstallDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_install_surya20(self, _event):
        from app.engine.surya20_installer import is_surya20_installed, Surya20InstallDialog
        from app.engine.surya20_installer import _SURYA20_VENV_DIR
        if is_surya20_installed():
            wx.MessageBox(
                _("Surya 0.2 is already installed.\n\nVenv: {path}").format(path=_SURYA20_VENV_DIR),
                _("Surya 0.2"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        dlg = Surya20InstallDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_uninstall_surya20(self, _event):
        from app.engine.surya20_installer import uninstall_surya20, _SURYA20_VENV_DIR
        if not os.path.isdir(_SURYA20_VENV_DIR):
            wx.MessageBox(
                _("Surya 0.2 is not installed."),
                _("Uninstall Surya 0.2"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        with wx.MessageDialog(
            self,
            _("The Surya 0.2 venv directory will be deleted.\nContinue?"),
            _("Uninstall Surya 0.2"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        ) as dlg:
            dlg.SetYesNoLabels(_("Yes"), _("No"))
            if dlg.ShowModal() != wx.ID_YES:
                return
        ok, msg = uninstall_surya20()
        if ok:
            wx.MessageBox(
                _("Surya 0.2 uninstalled successfully."),
                _("Uninstall complete"),
                wx.OK | wx.ICON_INFORMATION,
            )
        else:
            wx.MessageBox(
                _("Error during uninstallation:\n{msg}").format(msg=msg),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )

    def _on_install_chandra(self, _event):
        from app.engine.chandra_installer import is_chandra_installed, ChandraInstallDialog
        from app.engine.chandra_installer import _CHANDRA_VENV_DIR
        if is_chandra_installed():
            wx.MessageBox(
                _("Chandra is already installed.\n\nVenv: {path}").format(path=_CHANDRA_VENV_DIR),
                _("Chandra"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        dlg = ChandraInstallDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_uninstall_chandra(self, _event):
        from app.engine.chandra_installer import uninstall_chandra, _CHANDRA_VENV_DIR
        if not os.path.isdir(_CHANDRA_VENV_DIR):
            wx.MessageBox(
                _("Chandra is not installed."),
                _("Uninstall Chandra"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        with wx.MessageDialog(
            self,
            _("The Chandra venv directory will be deleted.\nContinue?"),
            _("Uninstall Chandra"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        ) as dlg:
            dlg.SetYesNoLabels(_("Yes"), _("No"))
            if dlg.ShowModal() != wx.ID_YES:
                return
        ok, msg = uninstall_chandra()
        if ok:
            wx.MessageBox(
                _("Chandra uninstalled successfully."),
                _("Uninstall complete"),
                wx.OK | wx.ICON_INFORMATION,
            )
        else:
            wx.MessageBox(
                _("Error during uninstallation:\n{msg}").format(msg=msg),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )

    def _on_install_tesseract(self, _event):
        from app.engine.tesseract_setup import install_tesseract_interactive
        install_tesseract_interactive(self)

    def _on_tesseract_langs(self, _event):
        from app.engine.tesseract_lang_installer import TesseractLangDialog
        dlg = TesseractLangDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_uninstall_surya(self, _event):
        from app.engine.surya_installer import uninstall_surya, _SURYA_VENV_DIR
        if not os.path.isdir(_SURYA_VENV_DIR):
            wx.MessageBox(
                _("Surya 0.1 is not installed."),
                _("Uninstall Surya 0.1"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        with wx.MessageDialog(
            self,
            _("The Surya 0.1 venv directory will be deleted.\nContinue?"),
            _("Uninstall Surya 0.1"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        ) as dlg:
            dlg.SetYesNoLabels(_("Yes"), _("No"))
            if dlg.ShowModal() != wx.ID_YES:
                return
        ok, msg = uninstall_surya()
        if ok:
            wx.MessageBox(
                _("Surya 0.1 uninstalled successfully."),
                _("Uninstall complete"),
                wx.OK | wx.ICON_INFORMATION,
            )
        else:
            wx.MessageBox(
                _("Error during uninstallation:\n{msg}").format(msg=msg),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )

    def _on_exit(self, _event):
        self.Close()

    def _on_about(self, _event):
        from app.version import __version__
        wx.MessageBox(
            _("OCR Lab {version}\n\nUtility for text acquisition and correction via OCR and LLM.\nFully accessible via screen reader.").format(version=__version__),
            _("About"),
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_toggle_auto_updates(self, _event):
        enabled = self.menu_auto_updates.IsChecked()
        self._config["auto_check_updates"] = enabled
        save_config(self._config)

    def _on_check_updates(self, _event):
        from app.updater import check_for_updates_async
        from app.version import __version__
        self.set_status(_("Checking for updates..."))

        def on_found(info):
            self.set_status(_("Ready."))
            self._show_update_dialog(info)

        def on_none():
            self.set_status(_("Ready."))
            wx.MessageBox(
                _("OCR Lab is up to date."),
                _("No updates"),
                wx.OK | wx.ICON_INFORMATION,
            )

        def on_error():
            self.set_status(_("Ready."))
            wx.MessageBox(
                _("Could not check for updates.\nVerify your internet connection."),
                _("Connection error"),
                wx.OK | wx.ICON_WARNING,
            )

        check_for_updates_async(__version__, on_found, on_none, on_error)

    def _auto_check_updates(self):
        from app.updater import check_for_updates_async
        from app.version import __version__
        check_for_updates_async(__version__, self._show_update_dialog)

    def _show_update_dialog(self, info):
        import webbrowser
        notes = info.body.strip()
        msg = _("Version {version} available.\n\n{notes}\n\nOpen the download page?").format(
            version=info.tag, notes=notes if notes else _("(no release notes)")
        )
        with wx.MessageDialog(
            self, msg, _("Update available"), wx.YES_NO | wx.YES_DEFAULT | wx.ICON_INFORMATION
        ) as dlg:
            dlg.SetYesNoLabels(_("Open"), _("Later"))
            if dlg.ShowModal() == wx.ID_YES:
                webbrowser.open(info.url)

    def set_status(self, text: str):
        wx.CallAfter(self.SetStatusText, text)
