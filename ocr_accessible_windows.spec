# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - OCR Lab Windows (.exe + COLLECT directory).

Surya NON è bundled: gira nel venv esterno (%APPDATA%\OCRLab\surya-venv)
e viene invocato come sottoprocesso. Tesseract viene rilevato/installato
a runtime dall'app (vedi app/engine/tesseract_setup.py).

Per la build, eseguire dal venv Windows del progetto:
  .venv\\Scripts\\pyinstaller.exe -y ocr_accessible_windows.spec
"""

import os
import sys

block_cipher = None

BASE = os.path.abspath(".")


def _find_venv_site_packages(base: str) -> str | None:
    """Trova site-packages nel venv locale su Windows."""
    for venv_name in (".venv", "venv"):
        sp = os.path.join(base, venv_name, "Lib", "site-packages")
        if os.path.isdir(sp):
            return sp
    return None


VENV_SP = _find_venv_site_packages(BASE) or ""

# --- Dati da includere ---
datas = []

# locale/: file di traduzione UI (en, it, ...) — necessario per i18n nel bundle
if os.path.isdir(os.path.join(BASE, "locale")):
    datas.append(("locale", "locale"))

# surya_worker.py: eseguito dal Python del surya-venv esterno
datas.append(("app/engine/surya_worker.py", "."))

# accessible_output2: contiene le DLL per i vari screen reader Windows
if VENV_SP:
    ao2_path = os.path.join(VENV_SP, "accessible_output2")
    if os.path.isdir(ao2_path):
        datas.append((ao2_path, "accessible_output2"))

# tiktoken: encoding BPE dalla cache locale
_tiktoken_cache = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "tiktoken"
)
if os.path.isdir(_tiktoken_cache):
    datas.append((_tiktoken_cache, "tiktoken_cache"))

# tiktoken_ext: plugin encoding registrato
if VENV_SP:
    tiktoken_ext_path = os.path.join(VENV_SP, "tiktoken_ext")
    if os.path.isdir(tiktoken_ext_path):
        datas.append((tiktoken_ext_path, "tiktoken_ext"))

# --- Hidden imports ---
hiddenimports = [
    "tiktoken",
    "tiktoken.core",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "google.genai",
    "fitz",       # PyMuPDF
    "PIL._imagingtk",
    # accessible_output2
    "accessible_output2",
    "accessible_output2.outputs",
    "accessible_output2.outputs.auto",
    "accessible_output2.outputs.nvda",
    "accessible_output2.outputs.jaws",
    "accessible_output2.outputs.sapi5",
    "accessible_output2.outputs.window_eyes",
    "accessible_output2.outputs.system_access",
    "accessible_output2.outputs.dolphin",
    # Windows OCR (winrt)
    "winrt.windows.media.ocr",
    "winrt.windows.globalization",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.storage",
]

a = Analysis(
    ["run.py"],
    pathex=[BASE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["hook_tiktoken_cache.py"],
    excludes=[
        # Surya e dipendenze: girano nel venv esterno, non nel bundle
        "surya",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tokenizers",
        "safetensors",
        "huggingface_hub",
        "diffusers",
        # Moduli macOS-only
        "wx.lib.analogclock",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OCR Lab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=None,  # sostituire con "app/resources/ocrlab.ico" quando disponibile
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OCR Lab",
)
