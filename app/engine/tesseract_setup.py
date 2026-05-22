"""Auto-rilevamento e setup di Tesseract OCR."""

import os
import shutil
import subprocess
import sys
import tempfile

import wx
import requests

from app.config import load_config, save_config

_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

# URL installer Tesseract UB Mannheim (release Windows)
TESSERACT_INSTALLER_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)

STANDARD_PATH_WINDOWS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# Path comuni su macOS (Homebrew apple silicon, Homebrew intel, MacPorts).
STANDARD_PATHS_MAC = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/local/bin/tesseract",
)


def get_tesseract_cmd(configured_path: str = "") -> str | None:
    """Restituisce il path dell'eseguibile Tesseract se trovato, altrimenti None."""
    # 1. Path configurato
    if configured_path and os.path.isfile(configured_path):
        return configured_path

    # 2. Path standard per piattaforma
    if _IS_WINDOWS and os.path.isfile(STANDARD_PATH_WINDOWS):
        return STANDARD_PATH_WINDOWS
    if _IS_MAC:
        for p in STANDARD_PATHS_MAC:
            if os.path.isfile(p):
                return p

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
    """Dialogo 'Tesseract non trovato' con checkbox 'non mostrare più'.

    Su Windows propone il download automatico dell'installer; su Mac mostra
    invece le istruzioni per `brew install tesseract`.
    """

    def __init__(self, parent):
        super().__init__(parent, title="Tesseract non trovato",
                         style=wx.DEFAULT_DIALOG_STYLE)
        sizer = wx.BoxSizer(wx.VERTICAL)

        if _IS_WINDOWS:
            label = (
                "Tesseract OCR non è stato trovato.\n\n"
                "Vuoi scaricarlo e installarlo automaticamente?"
            )
        elif _IS_MAC:
            label = (
                "Tesseract OCR non è stato trovato.\n\n"
                "Su macOS installa Tesseract con Homebrew:\n"
                "  brew install tesseract\n"
                "  brew install tesseract-lang   (per le lingue aggiuntive)\n\n"
                "Vuoi indicare manualmente il percorso dell'eseguibile?"
            )
        else:
            label = (
                "Tesseract OCR non è stato trovato.\n\n"
                "Installalo con il package manager della tua distribuzione, "
                "poi premi OK.\n\n"
                "Vuoi indicare manualmente il percorso dell'eseguibile?"
            )
        msg = wx.StaticText(self, label=label)
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


def _manual_pick_tesseract(parent: wx.Window, config: dict) -> str | None:
    """Apre un file dialog per selezionare l'eseguibile Tesseract manualmente."""
    if _IS_WINDOWS:
        wildcard = "Eseguibili (*.exe)|*.exe"
        title = "Seleziona tesseract.exe"
    else:
        wildcard = "Tutti i file (*)|*"
        title = "Seleziona l'eseguibile tesseract"

    file_dlg = wx.FileDialog(
        parent, title,
        wildcard=wildcard,
        style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
    )
    result = None
    if file_dlg.ShowModal() == wx.ID_OK:
        path = file_dlg.GetPath()
        if _verify_tesseract(path):
            config["tesseract_path"] = path
            save_config(config)
            try:
                parent.settings_panel.txt_tesseract_path.SetValue(path)
            except Exception:
                pass
            result = path
        else:
            wx.MessageBox(
                "Il file selezionato non sembra essere un Tesseract valido.",
                "Errore",
                wx.OK | wx.ICON_ERROR,
            )
    file_dlg.Destroy()
    return result


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

    # Su Windows, "Si'" = download automatico; altrove = selezione manuale.
    if _IS_WINDOWS and choice == wx.ID_YES:
        _download_and_install(parent)
        cmd = get_tesseract_cmd("")
        if cmd and _verify_tesseract(cmd):
            config["tesseract_path"] = cmd
            save_config(config)
            return cmd
    elif choice == wx.ID_YES:
        # Mac / Linux: selezione manuale.
        return _manual_pick_tesseract(parent, config)
    elif choice == wx.ID_NO and _IS_WINDOWS:
        # Su Windows ricalca il comportamento storico: il "No" apre comunque
        # il selettore manuale.
        return _manual_pick_tesseract(parent, config)

    return None
