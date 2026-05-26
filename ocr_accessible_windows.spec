# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - OCR Lab Windows (.exe + COLLECT directory).

Surya NON è bundled: gira nel venv esterno (%APPDATA%\OCRLab\surya-venv)
e viene invocato come sottoprocesso. Tesseract viene rilevato/installato
a runtime dall'app (vedi app/engine/tesseract_setup.py).

Per la build, eseguire dal venv Windows del progetto:
  .venv\\Scripts\\pyinstaller.exe -y ocr_accessible_windows.spec
"""

import glob as _glob
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

# --- PyMuPDF >= 1.24: gestione manuale dei binari.
#
# NON aggiungere la directory pymupdf a datas: PyInstaller archivierebbe i
# .py nel PYZ ma lascerebbe __init__.py su disco, rendendo pymupdf un package
# filesystem. I successivi "from . import extra" fallirebbero perché extra.py
# è nell'archivio ma non nel filesystem. Lasciamo che l'analisi di PyInstaller
# gestisca tutti i .py (via hiddenimports sotto), e aggiungiamo manualmente
# solo .pyd e .dll come binari.
_pymupdf_datas = []
_pymupdf_binaries = []
if VENV_SP:
    for _pkg_name in ("pymupdf", "fitz"):
        _pkg_dir = os.path.join(VENV_SP, _pkg_name)
        if os.path.isdir(_pkg_dir):
            for _pyd in _glob.glob(os.path.join(_pkg_dir, "*.pyd")):
                _pymupdf_binaries.append((_pyd, _pkg_name))
            for _dll in _glob.glob(os.path.join(_pkg_dir, "*.dll")):
                _pymupdf_binaries.append((_dll, _pkg_name))
            break

# --- Dati da includere ---
datas = list(_pymupdf_datas)

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
    "pytesseract",
    # pymupdf >= 1.24: submoduli espliciti perché PyInstaller non li trova
    # sempre via analisi statica (il try/except in ocr_engine.py li oscura).
    # extra.py è SWIG-generated e importa _extra.pyd; entrambi devono essere
    # nel bundle. NON usare pymupdf._pymupdf (non esiste in >= 1.24).
    "pymupdf",
    "pymupdf.extra",
    "pymupdf._extra",
    "pymupdf._mupdf",
    "pymupdf.mupdf",
    "pymupdf.table",
    "pymupdf.utils",
    # fitz: shim di compatibilità (fallback import in ocr_engine.py)
    "fitz",
    "fitz.table",
    "fitz.utils",
    "tiktoken",
    "tiktoken.core",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "google.genai",
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
    pathex=[BASE, VENV_SP] if VENV_SP else [BASE],
    binaries=_pymupdf_binaries,
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
    # Forza pymupdf e fitz a rimanere come .py su disco invece di essere
    # archiviati nel PYZ. Necessario perché pymupdf ha estensioni C (.pyd)
    # che costringono PyInstaller a creare _internal/pymupdf/ come directory
    # reale; se i .py finiscono nel PYZ, "from . import extra" dentro
    # __init__.py non riesce a trovare extra.py (FrozenImporter non serve
    # submoduli PYZ per package filesystem-based).
    module_collection_mode={
        "pymupdf": "py",
        "fitz": "py",
    },
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
