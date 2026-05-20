"""Auto-rilevamento e setup di Tesseract OCR."""

import os
import shutil
import subprocess
import tempfile

import wx
import requests

from app.config import load_config, save_config

# URL installer Tesseract UB Mannheim (release Windows)
TESSERACT_INSTALLER_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)

STANDARD_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def get_tesseract_cmd(configured_path: str = "") -> str | None:
    """Restituisce il path dell'eseguibile Tesseract se trovato, altrimenti None."""
    # 1. Path configurato
    if configured_path and os.path.isfile(configured_path):
        return configured_path

    # 2. Path standard Windows
    if os.path.isfile(STANDARD_PATH):
        return STANDARD_PATH

    # 3. PATH di sistema
    found = shutil.which("tesseract")
    if found:
        return found

    return None


def _verify_tesseract(cmd: str) -> bool:
    """Verifica che Tesseract funzioni."""
    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _download_and_install(parent: wx.Window) -> str | None:
    """Scarica e avvia l'installer di Tesseract. Restituisce il path se installato."""
    dlg = wx.ProgressDialog(
        "Download Tesseract",
        "Download in corso...",
        maximum=100,
        parent=parent,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
    )
    try:
        resp = requests.get(TESSERACT_INSTALLER_URL, stream=True, timeout=60)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        tmp_path = os.path.join(tempfile.gettempdir(), "tesseract_installer.exe")

        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = min(int(downloaded * 100 / total), 100)
                    dlg.Update(pct, f"Download: {downloaded // 1024} / {total // 1024} KB")

        dlg.Update(100, "Download completato. Avvio installer...")
        dlg.Destroy()

        # Avvia l'installer con privilegi elevati (richiede UAC)
        wx.MessageBox(
            "L'installer di Tesseract verrà avviato con privilegi di amministratore.\n"
            "Completa l'installazione e poi premi OK.",
            "Installazione Tesseract",
            wx.OK | wx.ICON_INFORMATION,
        )
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", tmp_path, None, None, 1)
        return None  # L'utente deve completare l'installazione manualmente

    except Exception as e:
        dlg.Destroy()
        wx.MessageBox(f"Errore durante il download: {e}", "Errore", wx.OK | wx.ICON_ERROR)
        return None


class _TesseractNotFoundDlg(wx.Dialog):
    """Dialogo 'Tesseract non trovato' con checkbox 'non mostrare più'."""

    def __init__(self, parent):
        super().__init__(parent, title="Tesseract non trovato",
                         style=wx.DEFAULT_DIALOG_STYLE)
        sizer = wx.BoxSizer(wx.VERTICAL)

        msg = wx.StaticText(
            self,
            label="Tesseract OCR non è stato trovato.\n\n"
                  "Vuoi scaricarlo e installarlo automaticamente?",
        )
        sizer.Add(msg, 0, wx.ALL, 12)

        self.chk_skip = wx.CheckBox(self, label="Non mostrare più questo messaggio")
        sizer.Add(self.chk_skip, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_yes = wx.Button(self, wx.ID_YES, "Sì")
        btn_no = wx.Button(self, wx.ID_NO, "No")
        btn_sizer.Add(btn_yes, 0, wx.RIGHT, 8)
        btn_sizer.Add(btn_no, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 12)

        self.SetSizer(sizer)
        self.Fit()

        btn_yes.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_YES))
        btn_no.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_NO))

    @property
    def skip(self) -> bool:
        return self.chk_skip.IsChecked()


def ensure_tesseract(parent: wx.Window) -> str | None:
    """Controlla la disponibilità di Tesseract e guida l'utente se mancante."""
    config = load_config()
    cmd = get_tesseract_cmd(config.get("tesseract_path", ""))

    if cmd and _verify_tesseract(cmd):
        # Salva il path trovato nella configurazione
        if config.get("tesseract_path", "") != cmd:
            config["tesseract_path"] = cmd
            save_config(config)
            # Aggiorna anche il campo nel pannello impostazioni se esiste
            try:
                main_frame = parent
                main_frame.settings_panel.txt_tesseract_path.SetValue(cmd)
            except Exception:
                pass
        return cmd

    # L'utente ha scelto di non essere più avvisato
    if config.get("skip_tesseract_check", False):
        return None

    # Tesseract non trovato: chiedi all'utente
    dlg = _TesseractNotFoundDlg(parent)
    choice = dlg.ShowModal()
    skip = dlg.skip
    dlg.Destroy()

    if skip:
        config["skip_tesseract_check"] = True
        save_config(config)

    if choice == wx.ID_YES:
        _download_and_install(parent)
        # Dopo l'installazione, riprova
        cmd = get_tesseract_cmd("")
        if cmd and _verify_tesseract(cmd):
            config["tesseract_path"] = cmd
            save_config(config)
            return cmd
    else:
        # Selezione manuale
        file_dlg = wx.FileDialog(
            parent,
            "Seleziona tesseract.exe",
            wildcard="Eseguibili (*.exe)|*.exe",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if file_dlg.ShowModal() == wx.ID_OK:
            path = file_dlg.GetPath()
            if _verify_tesseract(path):
                config["tesseract_path"] = path
                save_config(config)
                try:
                    parent.settings_panel.txt_tesseract_path.SetValue(path)
                except Exception:
                    pass
                return path
            else:
                wx.MessageBox(
                    "Il file selezionato non sembra essere un Tesseract valido.",
                    "Errore",
                    wx.OK | wx.ICON_ERROR,
                )
        file_dlg.Destroy()

    return None
