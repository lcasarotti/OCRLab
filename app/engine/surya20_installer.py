"""Installazione guidata del venv Surya 0.20 per OCRLab."""

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request

import wx

from app.i18n import _

import sys as _sys

if _sys.platform == "win32":
    _APP_SUPPORT = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OCRLab")
    _SURYA20_VENV_DIR = os.path.join(_APP_SUPPORT, "surya20-venv")
    _SURYA20_PYTHON = os.path.join(_SURYA20_VENV_DIR, "Scripts", "python.exe")
    _UV_LOCAL = os.path.join(_APP_SUPPORT, "bin", "uv.exe")
else:
    _APP_SUPPORT = os.path.expanduser("~/Library/Application Support/OCRLab")
    _SURYA20_VENV_DIR = os.path.join(_APP_SUPPORT, "surya20-venv")
    _SURYA20_PYTHON = os.path.join(_SURYA20_VENV_DIR, "bin", "python")
    _UV_LOCAL = os.path.join(_APP_SUPPORT, "bin", "uv")


def is_surya20_installed() -> bool:
    """True se il venv Surya 0.20 esiste e contiene surya."""
    if not os.path.isfile(_SURYA20_PYTHON):
        return False
    try:
        result = subprocess.run(
            [_SURYA20_PYTHON, "-c", "import surya"],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


class _Surya20NotFoundDlg(wx.Dialog):
    """Dialogo 'Surya 0.2 non trovato' con checkbox 'non mostrare più'."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Surya 0.2 not found"),
                         style=wx.DEFAULT_DIALOG_STYLE)
        sizer = wx.BoxSizer(wx.VERTICAL)

        label = _(
            "Surya 0.2 is not installed.\n\n"
            "Do you want to install it now?\n"
            "Installation requires an internet connection and may take several minutes.\n\n"
            "Note: Surya 0.2 requires Docker Desktop (NVIDIA GPU) or llama.cpp (CPU/Mac)."
        )
        msg = wx.StaticText(self, label=label)
        sizer.Add(msg, 0, wx.ALL, 12)

        self.chk_skip = wx.CheckBox(self, label=_("Do not show this message again"))
        sizer.Add(self.chk_skip, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_yes = wx.Button(self, wx.ID_YES, _("Yes"))
        btn_no = wx.Button(self, wx.ID_NO, _("No"))
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


def _show_surya20_prompt(parent: wx.Window) -> None:
    from app.config import load_config, save_config
    config = load_config()
    if config.get("skip_surya20_check", False):
        return

    dlg = _Surya20NotFoundDlg(parent)
    choice = dlg.ShowModal()
    skip = dlg.skip
    dlg.Destroy()

    if skip:
        config["skip_surya20_check"] = True
        save_config(config)

    if choice == wx.ID_YES:
        install_dlg = Surya20InstallDialog(parent)
        install_dlg.ShowModal()
        install_dlg.Destroy()


def ensure_surya20(parent: wx.Window) -> None:
    """Controlla la disponibilità di Surya 0.20 in background e offre di installarlo."""
    import threading
    from app.config import load_config
    config = load_config()
    if config.get("skip_surya20_check", False):
        return

    def _check():
        if not is_surya20_installed():
            wx.CallAfter(_show_surya20_prompt, parent)

    threading.Thread(target=_check, daemon=True).start()


def uninstall_surya20() -> tuple[bool, str]:
    """Rimuove il venv Surya 0.20. Restituisce (ok, messaggio_errore)."""
    try:
        shutil.rmtree(_SURYA20_VENV_DIR)
        return True, ""
    except Exception as e:
        return False, str(e)


class Surya20InstallDialog(wx.Dialog):
    """Dialog con log in streaming per l'installazione di Surya 0.20."""

    def __init__(self, parent):
        super().__init__(
            parent, title=_("Install Surya 0.2"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            size=(600, 480),
        )
        self._build_ui()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        warning = wx.StaticText(
            self,
            label=_(
                "Surya 0.2 requires an external inference backend:\n"
                "  - NVIDIA GPU: Docker Desktop + NVIDIA Container Toolkit\n"
                "  - CPU / Mac: llama.cpp (brew install llama.cpp on Mac)\n\n"
                "Recommended for advanced users. Make sure the backend is "
                "installed before running OCR."
            ),
        )
        warning.SetForegroundColour(wx.Colour(160, 60, 0))
        sizer.Add(warning, 0, wx.ALL, 12)

        intro = wx.StaticText(
            self,
            label=_("Surya 0.2 will be installed in a dedicated venv:\n"
                    "{path}\n\n"
                    "This will take a few minutes and requires an internet connection.").format(
                path=_SURYA20_VENV_DIR
            ),
        )
        sizer.Add(intro, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.log = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
        )
        self.log.SetFont(wx.Font(
            10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
        ))
        sizer.Add(self.log, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        self.progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.ALL, 12)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_install = wx.Button(self, label=_("Install"))
        self.btn_close = wx.Button(self, wx.ID_CLOSE, _("Close"))
        btn_sizer.Add(self.btn_install, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.btn_close, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 12)

        self.SetSizer(sizer)

        self.btn_install.Bind(wx.EVT_BUTTON, self._on_install)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _log(self, text: str):
        wx.CallAfter(self.log.AppendText, text)

    @staticmethod
    def _clean_env() -> dict:
        env = os.environ.copy()
        for _var in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
            env.pop(_var, None)
        if hasattr(_sys, "_MEIPASS"):
            _mei = os.path.normcase(_sys._MEIPASS)
            _parts = [
                p for p in env.get("PATH", "").split(os.pathsep)
                if os.path.normcase(p) != _mei
            ]
            env["PATH"] = os.pathsep.join(_parts)
        return env

    def _run_step(self, args: list, label: str) -> bool:
        self._log(f"\n--- {label} ---\n")
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self._clean_env(),
                creationflags=(subprocess.CREATE_NO_WINDOW if _sys.platform == "win32" else 0),
            )
            for line in proc.stdout:
                self._log(line)
                wx.CallAfter(self.progress.Pulse)
            proc.wait()
            return proc.returncode == 0
        except Exception as e:
            self._log(f"Errore: {e}\n")
            return False

    @staticmethod
    def _find_uv() -> str:
        found = shutil.which("uv")
        if found:
            return found
        for candidate in (
            _UV_LOCAL,
            os.path.expanduser("~/.local/bin/uv"),
            os.path.expanduser("~/.cargo/bin/uv"),
            "/opt/homebrew/bin/uv",
            "/usr/local/bin/uv",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return ""

    def _download_uv(self) -> str:
        machine = platform.machine().lower()
        is_win = _sys.platform == "win32"

        if is_win:
            arch_str = "x86_64-pc-windows-msvc" if "64" in machine else "i686-pc-windows-msvc"
            archive_name = f"uv-{arch_str}.zip"
        elif _sys.platform == "darwin":
            arch_str = "aarch64-apple-darwin" if machine == "arm64" else "x86_64-apple-darwin"
            archive_name = f"uv-{arch_str}.tar.gz"
        else:
            self._log(f"Piattaforma non supportata per il download di uv: {_sys.platform}\n")
            return ""

        url = f"https://github.com/astral-sh/uv/releases/latest/download/{archive_name}"
        self._log(f"Scarico uv da GitHub releases ({arch_str})...\n")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive_path = os.path.join(tmp, archive_name)
                urllib.request.urlretrieve(url, archive_path)
                os.makedirs(os.path.dirname(_UV_LOCAL), exist_ok=True)

                if is_win:
                    import zipfile
                    with zipfile.ZipFile(archive_path) as zf:
                        uv_entry = next(
                            n for n in zf.namelist()
                            if n.endswith("uv.exe") and "uvx" not in n
                        )
                        with zf.open(uv_entry) as src, open(_UV_LOCAL, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                else:
                    with tarfile.open(archive_path, "r:gz") as tf:
                        uv_member = next(
                            m for m in tf.getmembers()
                            if m.name.endswith("/uv") and not m.name.endswith("/uvx")
                        )
                        uv_member.name = "uv"
                        tf.extract(uv_member, tmp, filter="data")
                        shutil.copy2(os.path.join(tmp, "uv"), _UV_LOCAL)
                        os.chmod(_UV_LOCAL, 0o755)

            self._log(f"uv scaricato e salvato in {_UV_LOCAL}\n")
            return _UV_LOCAL
        except Exception as e:
            self._log(f"Download uv fallito: {e}\n")
            return ""

    @staticmethod
    def _detect_torch_index_url() -> str:
        if _sys.platform != "win32":
            return ""
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return ""
        try:
            cc_result = subprocess.run(
                [nvidia_smi, "--query-gpu=compute_cap", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if cc_result.returncode == 0:
                cc_str = cc_result.stdout.strip().splitlines()[0].strip()
                cc_major = int(float(cc_str))
                if cc_major >= 10:
                    return "https://download.pytorch.org/whl/cu130"
        except Exception:
            pass
        try:
            result = subprocess.run(
                [nvidia_smi],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output = result.stdout
            for line in output.splitlines():
                if "CUDA Version:" in line:
                    parts = line.split("CUDA Version:")
                    if len(parts) > 1:
                        ver_str = parts[1].strip().split()[0]
                        major = int(ver_str.split(".")[0])
                        minor = int(ver_str.split(".")[1]) if "." in ver_str else 0
                        if major >= 13:
                            return "https://download.pytorch.org/whl/cu130"
                        if major == 12 and minor >= 6:
                            return "https://download.pytorch.org/whl/cu126"
                        return "https://download.pytorch.org/whl/cu126"
        except Exception:
            pass
        return "https://download.pytorch.org/whl/cu126"

    @staticmethod
    def _find_python3() -> str:
        if _sys.platform == "win32":
            for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
                found = shutil.which(name)
                if found:
                    return found
            for ver in ("313", "312", "311", "310"):
                for base in (
                    os.path.expanduser(f"~\\AppData\\Local\\Programs\\Python\\Python{ver}"),
                    f"C:\\Python{ver}",
                ):
                    candidate = os.path.join(base, "python.exe")
                    if os.path.isfile(candidate):
                        return candidate
            return ""
        for name in ("python3.13", "python3.12", "python3.11", "python3.10"):
            found = shutil.which(name)
            if found:
                return found
            for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
                candidate = os.path.join(prefix, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
        return ""

    def _install_thread(self):
        os.makedirs(os.path.dirname(_SURYA20_VENV_DIR), exist_ok=True)

        if os.path.isdir(_SURYA20_VENV_DIR) and not is_surya20_installed():
            self._log("Venv parziale trovato — rimozione in corso...\n")
            shutil.rmtree(_SURYA20_VENV_DIR, ignore_errors=True)

        uv = self._find_uv()
        if not uv:
            self._log("uv non trovato — tento il download automatico...\n")
            uv = self._download_uv()

        if uv:
            self._log(f"uv: {uv} — creazione venv con Python 3.12...\n")
            ok = self._run_step(
                [uv, "venv", "--python", "3.12", _SURYA20_VENV_DIR],
                "Creazione venv",
            )
            pip_base = [uv, "pip", "install", "--python", _SURYA20_PYTHON]
        else:
            python3 = self._find_python3()
            if not python3:
                self._log(
                    "Errore: uv non scaricabile e Python 3.10+ non trovato.\n"
                    "Installa Python 3.12 e riprova.\n"
                )
                wx.CallAfter(self._on_finished, False)
                return
            self._log(f"uv non disponibile — uso {python3}...\n")
            ok = self._run_step(
                [python3, "-m", "venv", _SURYA20_VENV_DIR],
                "Creazione venv",
            )
            pip_exe = (
                os.path.join(_SURYA20_VENV_DIR, "Scripts", "pip.exe")
                if _sys.platform == "win32"
                else os.path.join(_SURYA20_VENV_DIR, "bin", "pip")
            )
            pip_base = [pip_exe, "install"]

        if not ok:
            wx.CallAfter(self._on_finished, False)
            return

        # PyTorch: ancora necessario per i componenti torch-only di surya (es. DetectionPredictor)
        torch_index_url = self._detect_torch_index_url()
        if torch_index_url:
            self._log(f"GPU NVIDIA rilevata — backend: {torch_index_url.split('/')[-1]}\n")
            torch_args = ["torch", "torchvision", "--index-url", torch_index_url]
            step_label = f"Installazione PyTorch ({torch_index_url.split('/')[-1]})"
        elif _sys.platform == "win32":
            self._log("GPU NVIDIA non rilevata — installazione PyTorch CPU only.\n")
            torch_args = ["torch", "torchvision",
                          "--index-url", "https://download.pytorch.org/whl/cpu"]
            step_label = "Installazione PyTorch (CPU only)"
        else:
            torch_args = ["torch", "torchvision"]
            step_label = "Installazione PyTorch (con supporto MPS)"

        ok = self._run_step(pip_base + torch_args, step_label)
        if not ok:
            wx.CallAfter(self._on_finished, False)
            return

        # Surya 0.20: nessun vincolo su transformers (gestito dal server di inferenza)
        ok = self._run_step(
            pip_base + ["surya-ocr>=0.20", "PyMuPDF", "Pillow", "requests"],
            "Installazione Surya 0.2 e dipendenze",
        )
        if not ok:
            wx.CallAfter(self._on_finished, False)
            return

        self._log("\n--- Verifica ---\n")
        verify_script = (
            "import importlib.metadata, surya; "
            "v = importlib.metadata.version('surya-ocr'); "
            "print(f'  surya-ocr {v}')"
        )
        try:
            result = subprocess.run(
                [_SURYA20_PYTHON, "-c", verify_script],
                capture_output=True, text=True, timeout=60,
                env=self._clean_env(),
                creationflags=(subprocess.CREATE_NO_WINDOW if _sys.platform == "win32" else 0),
            )
            self._log(result.stdout)
            if result.returncode != 0:
                self._log(result.stderr)
                wx.CallAfter(self._on_finished, False)
                return
        except Exception as e:
            self._log(f"Errore verifica: {e}\n")
            wx.CallAfter(self._on_finished, False)
            return

        if _sys.platform == "darwin":
            self._log(
                "\nInstallazione completata.\n"
                "Per usare Surya 0.2 su Mac, installa llama.cpp:\n"
                "  brew install llama.cpp\n"
            )
        elif _sys.platform == "win32":
            self._log(
                "\nInstallazione completata.\n"
                "Per GPU NVIDIA: installa Docker Desktop e NVIDIA Container Toolkit.\n"
                "Per CPU: llama-server.exe da https://github.com/ggml-org/llama.cpp/releases\n"
            )

        wx.CallAfter(self._on_finished, True)

    def _on_finished(self, success: bool):
        self.progress.SetValue(100 if success else 0)
        self.btn_install.Disable()
        self.btn_close.Enable()
        self.btn_close.SetFocus()
        if success:
            wx.MessageBox(
                _("Surya 0.2 installed successfully.\nRestart OCR Lab to use it."),
                _("Installation complete"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
        else:
            wx.MessageBox(
                _("Installation failed. Check the log for details."),
                _("Installation error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_install(self, _event):
        self.btn_install.Disable()
        self.btn_close.Disable()
        threading.Thread(target=self._install_thread, daemon=True).start()

    def _on_close(self, event):
        if self.btn_close.IsEnabled():
            event.Skip()
