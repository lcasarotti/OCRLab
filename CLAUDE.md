# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OCRLab is a cross-platform OCR desktop application (wxPython) with multi-engine support (Tesseract, Surya, Windows OCR, Apple Vision, Ollama VLM, Chandra, Google Gemini) and accessibility features (TTS, screen readers). Entry point: `run.py`.

## Running and Building

```bash
# Run from source
python run.py

# Build Windows .exe
.venv\Scripts\pyinstaller.exe -y ocr_accessible_windows.spec

# Build macOS .app
.venv/bin/pyinstaller ocr_accessible_mac.spec
```

Install dependencies: `pip install -r requirements.txt`

## Code Style

- **No formatter is used** — match the existing style of the file you're editing.
- Italian comments and Italian variable names are normal in this codebase; do not translate them.
- No linters are configured; do not introduce linting config.

## Architecture Notes

**Surya isolation:** `SuryaEngine` spawns a subprocess inside a separate Python venv (at `%APPDATA%\OCRLab\surya-venv` on Windows, `~/Library/Application Support/OCRLab/surya-venv` on macOS). Never import Surya/PyTorch directly in the main process.

**PyMuPDF compatibility:** The codebase handles both old (`import fitz`) and new (`import pymupdf as fitz`) import styles — do not consolidate them to one style without testing both `fitz` and `pymupdf` package versions.

**Page separation:** Multi-page OCR results use `\f` (form feed `\x0c`) as the page separator.

**Threading pattern:** Long-running OCR operations run in a `threading.Thread` with a `threading.Event` for cancellation. Always pass `cancel_event` through the chain and check it inside loops.

**Callback pattern:** Engines emit progress via `on_progress(text)` and partial results via `on_partial(text)` callbacks. Keep this interface consistent when adding engines.

## Platform-Specific Code

- **Windows only:** `winrt-*` packages (native WinRT OCR), `accessible_output2` (screen readers), SAPI5 TTS
- **macOS only:** `apple_vision_engine.py` (Vision framework via Swift subprocess), MPS for Surya
- Guard platform-specific imports with `sys.platform` checks (`"win32"` / `"darwin"`).

## Internationalization

All UI strings must go through `app/i18n.py`'s `_("string")` function. Translation catalogs are at `locale/en.json` and `locale/it.json`. Add new strings to both files.

## Config & Secrets

`config.json` at the project root stores user settings and **may contain API keys** (Google Gemini, Ollama URLs). It is excluded from git. Never hardcode keys; always read from config via `app/config.py`.

## External Dependencies Not in requirements.txt

- **Tesseract OCR** — must be installed separately by the user (Windows: Tesseract installer; macOS: `brew install tesseract`). Auto-detected at runtime via `pytesseract.get_tesseract_version()`.
- **Ollama** — must be running locally on port 11434 to use VLM/LLM engines.
