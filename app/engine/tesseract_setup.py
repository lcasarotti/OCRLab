"""Auto-rilevamento e setup di Tesseract OCR."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading

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

# Directory tessdata utente: le lingue aggiuntive vengono salvate qui.
if _IS_WINDOWS:
    _APP_SUPPORT_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OCRLab")
else:
    _APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/OCRLab")
USER_TESSDATA_DIR = os.path.join(_APP_SUPPORT_DIR, "tessdata")


def _bundled_tesseract_path() -> str | None:
    """Restituisce il path del Tesseract incluso nel bundle PyInstaller, se presente."""
    if not getattr(sys, "frozen", False):
        return None
    candidate = os.path.join(sys._MEIPASS, "tesseract")
    return candidate if os.path.isfile(candidate) else None


def get_tessdata_dir() -> str | None:
    """Restituisce la directory tessdata da usare, o None per usare il default di Tesseract.

    Quando l'app è bundled e USER_TESSDATA_DIR contiene file restituisce
    USER_TESSDATA_DIR, che contiene le lingue di base (copiate da init_tessdata
    o da _init_tessdata_windows) più quelle aggiuntive scaricate dall'utente.
    Restituisce None se non siamo in un bundle o se la directory è vuota (in
    quel caso Tesseract usa il proprio tessdata di sistema).
    """
    if not getattr(sys, "frozen", False):
        return None
    if os.path.isdir(USER_TESSDATA_DIR) and any(
        f.endswith(".traineddata") for f in os.listdir(USER_TESSDATA_DIR)
    ):
        return USER_TESSDATA_DIR
    return None


def init_tessdata() -> None:
    """Copia i traineddata bundled in Application Support se non già presenti.

    Usata su macOS dove il bundle .app include una directory tessdata.
    Su Windows la directory tessdata non è bundled; usare _init_tessdata_windows().
    """
    if not getattr(sys, "frozen", False):
        return
    bundle_tessdata = os.path.join(sys._MEIPASS, "tessdata")
    if not os.path.isdir(bundle_tessdata):
        return
    os.makedirs(USER_TESSDATA_DIR, exist_ok=True)
    for fname in os.listdir(bundle_tessdata):
        dest = os.path.join(USER_TESSDATA_DIR, fname)
        if not os.path.isfile(dest):
            shutil.copy2(os.path.join(bundle_tessdata, fname), dest)


def _init_tessdata_windows(tesseract_cmd: str) -> None:
    """Popola USER_TESSDATA_DIR dai traineddata dell'installazione Tesseract di sistema.

    Chiamata al primo avvio su Windows (frozen) quando USER_TESSDATA_DIR è vuota.
    Copia tutti i .traineddata dalla directory tessdata accanto all'eseguibile
    Tesseract in USER_TESSDATA_DIR, così le lingue extra scaricate dall'utente
    convivono con le lingue standard sotto un unico TESSDATA_PREFIX.
    File già presenti in USER_TESSDATA_DIR vengono lasciati intatti.
    """
    if not _IS_WINDOWS or not getattr(sys, "frozen", False):
        return
    if os.path.isdir(USER_TESSDATA_DIR) and any(
        f.endswith(".traineddata") for f in os.listdir(USER_TESSDATA_DIR)
    ):
        return
    system_tessdata = os.path.join(os.path.dirname(tesseract_cmd), "tessdata")
    if not os.path.isdir(system_tessdata):
        return
    os.makedirs(USER_TESSDATA_DIR, exist_ok=True)
    for fname in os.listdir(system_tessdata):
        dest = os.path.join(USER_TESSDATA_DIR, fname)
        if not os.path.isfile(dest):
            shutil.copy2(os.path.join(system_tessdata, fname), dest)


def get_tesseract_cmd(configured_path: str = "") -> str | None:
    """Restituisce il path dell'eseguibile Tesseract se trovato, altrimenti None."""
    # 0. Tesseract bundled nel .app (priorità massima quando frozen)
    bundled = _bundled_tesseract_path()
    if bundled:
        return bundled

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


def _download_and_install(parent: wx.Window) -> None:
    """Scarica l'installer Tesseract in background e lo avvia con privilegi elevati."""
    # Dialogo modale con gauge — il download gira in un thread separato
    dlg = wx.Dialog(parent, title="Download Tesseract",
                    style=wx.DEFAULT_DIALOG_STYLE & ~wx.CLOSE_BOX)
    sizer = wx.BoxSizer(wx.VERTICAL)
    label = wx.StaticText(dlg, label="Download in corso...")
    gauge = wx.Gauge(dlg, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH, size=(350, -1))
    sizer.Add(label, 0, wx.ALL, 12)
    sizer.Add(gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
    dlg.SetSizer(sizer)
    dlg.Fit()

    result: list = [None]

    def _do_download():
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
                        wx.CallAfter(label.SetLabel,
                                     f"Download: {downloaded // 1024} / {total // 1024} KB")
                        wx.CallAfter(gauge.SetValue, pct)
            result[0] = tmp_path
        except Exception as e:
            result[0] = e
        wx.CallAfter(dlg.EndModal, wx.ID_OK)

    threading.Thread(target=_do_download, daemon=True).start()
    dlg.ShowModal()
    dlg.Destroy()

    if isinstance(result[0], Exception):
        wx.MessageBox(
            f"Errore durante il download: {result[0]}", "Errore", wx.OK | wx.ICON_ERROR, parent
        )
        return

    tmp_path = result[0]
    wx.MessageBox(
        "Premi OK per avviare l'installer di Tesseract con privilegi di amministratore.",
        "Installazione Tesseract",
        wx.OK | wx.ICON_INFORMATION,
        parent,
    )
    import ctypes
    ctypes.windll.shell32.ShellExecuteW(None, "runas", tmp_path, None, None, 1)


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


def install_tesseract_interactive(parent: wx.Window) -> None:
    """Avvia il download/installazione di Tesseract su richiesta esplicita dal menu."""
    config = load_config()
    cmd = get_tesseract_cmd(config.get("tesseract_path", ""))
    if cmd and _verify_tesseract(cmd):
        wx.MessageBox(
            "Tesseract OCR è già installato.",
            "Tesseract OCR",
            wx.OK | wx.ICON_INFORMATION,
            parent,
        )
        return
    _download_and_install(parent)


def ensure_tesseract(parent: wx.Window) -> str | None:
    """Controlla la disponibilità di Tesseract e guida l'utente se mancante."""
    config = load_config()
    cmd = get_tesseract_cmd(config.get("tesseract_path", ""))

    # Inizializza tessdata in Application Support.
    # macOS: init_tessdata() copia dal bundle .app.
    # Windows: _init_tessdata_windows() copia dall'installazione di sistema
    #          (solo al primo avvio, quando USER_TESSDATA_DIR è vuota).
    if _IS_WINDOWS and cmd:
        _init_tessdata_windows(cmd)
    else:
        init_tessdata()

    tessdata = get_tessdata_dir()
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = tessdata

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

    return None
