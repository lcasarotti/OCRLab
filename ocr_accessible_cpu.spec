# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file per OCR Lab — build CPU (Surya con PyTorch CPU-only).

Richiede un venv separato (venv_cpu/) con:
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  pip install surya-ocr transformers==4.57.6
  pip install wxPython pytesseract pymupdf pillow python-docx requests tiktoken
       accessible_output2 google-genai winrt-Windows.Media.Ocr
       winrt-Windows.Globalization winrt-Windows.Graphics.Imaging
       winrt-Windows.Storage.Streams winrt-Windows.Foundation
       winrt-Windows.Foundation.Collections winrt-Windows.Storage
       pyinstaller

Per la build eseguire da venv_cpu:
  venv_cpu\\Scripts\\pyinstaller.exe ocr_accessible_cpu.spec --clean
"""

import os
import sys

block_cipher = None

# Percorsi — punta al venv CPU separato
BASE    = os.path.abspath(".")
VENV_SP = os.path.join(BASE, "ocr-lab-cpu", "Lib", "site-packages")

# --- Dati da includere ---
datas = []

# 0. surya_worker.py
datas.append(("app/engine/surya_worker.py", "."))

# 1. accessible_output2
ao2_path = os.path.join(VENV_SP, "accessible_output2")
datas.append((ao2_path, "accessible_output2"))

# 2. tiktoken: dati encoding dalla cache
tiktoken_cache = os.path.join(os.environ.get("TEMP", ""), "data-gym-cache")
if os.path.isdir(tiktoken_cache):
    datas.append((tiktoken_cache, "tiktoken_cache"))

# 3. tiktoken_ext
tiktoken_ext_path = os.path.join(VENV_SP, "tiktoken_ext")
datas.append((tiktoken_ext_path, "tiktoken_ext"))

# --- Hidden imports ---
hiddenimports = [
    "accessible_output2",
    "accessible_output2.outputs",
    "accessible_output2.outputs.auto",
    "accessible_output2.outputs.nvda",
    "accessible_output2.outputs.jaws",
    "accessible_output2.outputs.sapi5",
    "accessible_output2.outputs.window_eyes",
    "accessible_output2.outputs.system_access",
    "accessible_output2.outputs.dolphin",
    "tiktoken",
    "tiktoken.core",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "google.genai",
    # Surya
    "surya",
    "surya.detection",
    "surya.recognition",
    "surya.layout",
    "surya.foundation",
    "surya.settings",
    "transformers",
    "tokenizers",
    "safetensors",
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
    excludes=[],
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OCR Lab CPU",
)
