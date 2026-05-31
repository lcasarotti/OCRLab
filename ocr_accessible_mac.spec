# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - OCR Lab macOS (.app bundle).

Bundla l'app principale con tutti i motori tranne Surya, che gira nel
venv esterno (~/.../surya-venv/) e viene invocato come sottoprocesso.
Apple Vision usa lo script Swift a runtime (richiede Xcode CLT installato).
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

BASE = os.path.abspath(".")

def _find_venv_site_packages(base: str) -> str | None:
    """Trova site-packages nel venv locale, indipendentemente da nome/versione Python."""
    for venv_name in (".venv", "venv"):
        lib_dir = os.path.join(base, venv_name, "lib")
        if not os.path.isdir(lib_dir):
            continue
        for entry in os.listdir(lib_dir):
            if entry.startswith("python"):
                sp = os.path.join(lib_dir, entry, "site-packages")
                if os.path.isdir(sp):
                    return sp
    return None

VENV_SP = _find_venv_site_packages(BASE) or ""

# --- PyMuPDF: raccoglie DLL native, datas e hiddenimports in un colpo.
#     Prova prima "pymupdf" (>= 1.23), poi "fitz" (< 1.23). ---
try:
    _pymupdf_datas, _pymupdf_binaries, _pymupdf_hidden = collect_all("pymupdf")
except Exception:
    _pymupdf_datas, _pymupdf_binaries, _pymupdf_hidden = collect_all("fitz")

# --- Tesseract bundled ---
_TESSERACT_BIN = "/opt/homebrew/bin/tesseract"
binaries = list(_pymupdf_binaries) + ([(_TESSERACT_BIN, ".")] if os.path.isfile(_TESSERACT_BIN) else [])

_TESSDATA_SRC = "/opt/homebrew/share/tessdata"
_BUNDLED_LANGS = ("eng", "ita")

# --- Dati da includere ---
datas = list(_pymupdf_datas)

# tessdata: eng + ita nel bundle; l'utente può aggiungere lingue extra in
# ~/Library/Application Support/OCRLab/tessdata/ dall'app
for _lang in _BUNDLED_LANGS:
    _f = os.path.join(_TESSDATA_SRC, f"{_lang}.traineddata")
    if os.path.isfile(_f):
        datas.append((_f, "tessdata"))

# locale/: file di traduzione UI (en, it, ...)
if os.path.isdir(os.path.join(BASE, "locale")):
    datas.append(("locale", "locale"))

# surya_worker.py / surya20_worker.py: eseguiti dal Python dei venv esterni
datas.append(("app/engine/surya_worker.py", "."))
datas.append(("app/engine/surya20_worker.py", "."))

# apple_vision_helper.swift: avviato a runtime tramite `swift`
datas.append(("app/engine/apple_vision_helper.swift", "."))

# tiktoken: encoding BPE scaricato nella cache locale
_tiktoken_cache = os.path.join(os.path.expanduser("~"), ".tiktoken")
if os.path.isdir(_tiktoken_cache):
    datas.append((_tiktoken_cache, "tiktoken_cache"))

# tiktoken_ext: plugin encoding registrato
if VENV_SP:
    tiktoken_ext_path = os.path.join(VENV_SP, "tiktoken_ext")
    if os.path.isdir(tiktoken_ext_path):
        datas.append((tiktoken_ext_path, "tiktoken_ext"))

# --- Hidden imports ---
hiddenimports = _pymupdf_hidden + [
    "tiktoken",
    "tiktoken.core",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "google.genai",
    "PIL._imagingtk",
]

a = Analysis(
    ["run.py"],
    pathex=[BASE],
    binaries=binaries,
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
        # Moduli Windows-only
        "accessible_output2",
        "winrt",
        "win32api",
        "win32con",
        "pywintypes",
    ],
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
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="OCR Lab",
)

app = BUNDLE(
    coll,
    name="OCR Lab.app",
    icon=None,          # sostituire con "app/resources/ocrlab.icns" quando disponibile
    bundle_identifier="it.lucacasarotti.ocrlab",
    info_plist={
        "CFBundleShortVersionString": "0.1.1",
        "CFBundleVersion": "2",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Non richiesto.",
        # Permesso per leggere file scelti dall'utente tramite dialogo
        "NSDocumentsFolderUsageDescription": "Accesso ai documenti per l'OCR.",
    },
)
