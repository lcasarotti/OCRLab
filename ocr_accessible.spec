# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for OCR Lab."""

import os
import sys

block_cipher = None

# Percorsi
BASE = os.path.abspath(".")
VENV_SP = os.path.join(BASE, "venv", "Lib", "site-packages")

# --- Dati da includere ---
datas = []

# 0. surya_worker.py: script standalone eseguito dal Python esterno per l'OCR Surya
datas.append(("app/engine/surya_worker.py", "."))

# 1. accessible_output2: intero pacchetto (contiene DLL per screen reader)
ao2_path = os.path.join(VENV_SP, "accessible_output2")
datas.append((ao2_path, "accessible_output2"))

# 2. tiktoken: dati encoding dalla cache
tiktoken_cache = os.path.join(os.environ.get("TEMP", ""), "data-gym-cache")
if os.path.isdir(tiktoken_cache):
    datas.append((tiktoken_cache, "tiktoken_cache"))

# 3. tiktoken_ext: plugin registrato
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
        # Surya e le sue dipendenze (PyTorch, Transformers) NON vengono
        # incluse nel build standalone: dipendono dalla GPU/CUDA della macchina
        # dell'utente. Chi vuole usare Surya deve installare i pacchetti
        # separatamente e avviare l'app dall'ambiente Python.
        "surya",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tokenizers",
        "safetensors",
        "huggingface_hub",
        "diffusers",
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
    console=False,  # Nessuna finestra console
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
    name="OCR Lab",
)
