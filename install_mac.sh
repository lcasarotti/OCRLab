#!/usr/bin/env bash
# Installa OCRLab su macOS: venv principale + venv Surya opzionale.
# Uso: bash install_mac.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
SURYA_VENV="$HOME/Library/Application Support/OCRLab/surya-venv"
SURYA_PYTHON="$SURYA_VENV/bin/python"

# Funzioni pip che gestiscono correttamente i percorsi con spazi
pip_main() { "$VENV_DIR/bin/python" -m pip "$@"; }
pip_surya() { "$SURYA_PYTHON" -m pip "$@"; }

echo "============================================================"
echo " Installazione OCRLab"
echo "============================================================"
echo ""

# ---- Python ----
if ! command -v python3 &>/dev/null; then
    echo "ERRORE: python3 non trovato."
    echo "Installa Python 3.10+ con Homebrew:"
    echo "  brew install python@3.12"
    exit 1
fi
echo "Python trovato: $(python3 --version)"
echo ""

# ---- Venv principale ----
echo "Creazione venv principale..."
if [ -d "$VENV_DIR" ]; then
    echo "Venv principale già esistente, salto la creazione."
else
    if command -v uv &>/dev/null; then
        uv venv --python 3.12 "$VENV_DIR"
    else
        python3 -m venv "$VENV_DIR"
    fi
fi

echo "Installazione dipendenze principali..."
pip_main install -r "$APP_DIR/requirements.txt" --quiet
echo "Dipendenze principali installate."
echo ""

# ---- Venv Surya (opzionale) ----
echo "============================================================"
echo " Installazione Surya OCR (opzionale)"
echo "============================================================"
echo "Surya è un motore OCR avanzato che richiede PyTorch (~2-5 GB)."
echo "Puoi saltare e installarlo in seguito rieseguendo questo script."
echo ""
read -r -p "Installare Surya? [s/N]: " INSTALL_SURYA
if [[ "${INSTALL_SURYA,,}" == "s" ]]; then
    mkdir -p "$HOME/Library/Application Support/OCRLab"

    if [ -d "$SURYA_VENV" ]; then
        echo "Venv Surya già esistente, salto la creazione."
    else
        echo "Destinazione: $SURYA_VENV"
        if command -v uv &>/dev/null; then
            uv venv --python 3.12 "$SURYA_VENV"
        else
            python3 -m venv "$SURYA_VENV"
        fi
    fi

    echo "Installazione PyTorch (con supporto MPS per Apple Silicon)..."
    pip_surya install torch torchvision --quiet

    echo "Installazione Surya OCR e dipendenze..."
    pip_surya install "surya-ocr" "transformers>=4.40,<5" PyMuPDF Pillow requests --quiet

    echo "Verifica installazione Surya..."
    "$SURYA_PYTHON" -c "
import importlib.metadata, torch
mps = torch.backends.mps.is_available()
print('  torch:', importlib.metadata.version('torch'))
print('  surya:', importlib.metadata.version('surya-ocr'))
print('  MPS disponibile:', mps)
if not mps:
    print('  ATTENZIONE: MPS non disponibile, si userà CPU.')
"
    echo "Surya installato."
else
    echo "Surya saltato. Riesegui questo script per installarlo in seguito."
fi
echo ""

# ---- Launcher ----
LAUNCHER="$APP_DIR/avvia_ocrlap.command"
echo "Creazione launcher avvia_ocrlap.command..."
cat > "$LAUNCHER" <<LAUNCHEOF
#!/bin/bash
cd "$APP_DIR"
"$VENV_DIR/bin/python" run.py
LAUNCHEOF
chmod +x "$LAUNCHER"
echo "Launcher creato: avvia_ocrlap.command"
echo ""

# ---- Istruzioni Tesseract ----
echo "============================================================"
echo " Tesseract OCR (opzionale)"
echo "============================================================"
echo "Se vuoi usare il motore Tesseract, installalo con Homebrew:"
echo ""
echo "  brew install tesseract tesseract-lang"
echo ""
echo "OCRLab lo rileva automaticamente dopo l'installazione."
echo ""

echo "============================================================"
echo " Installazione completata!"
echo "============================================================"
echo "Per avviare OCRLab: doppio clic su avvia_ocrlap.command"
echo "oppure:  bash \"$LAUNCHER\""
echo ""
