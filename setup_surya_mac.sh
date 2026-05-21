#!/usr/bin/env bash
# Installa il venv Surya per OCRLab in ~/Library/Application Support/OCRLab/surya-venv/
# Richiede: macOS con Apple Silicon (M1/M2/M3), Python 3.12+ o uv installato.
#
# Uso: bash setup_surya_mac.sh
# Al termine non è necessaria nessuna configurazione: OCRLab rileva il venv automaticamente.

set -euo pipefail

VENV_DIR="$HOME/Library/Application Support/OCRLab/surya-venv"
PYTHON_EXE="$VENV_DIR/bin/python"

echo "=== Setup Surya per OCRLab ==="
echo "Destinazione: $VENV_DIR"
echo ""

# Verifica architettura
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "ATTENZIONE: Questo script è ottimizzato per Apple Silicon (arm64)."
    echo "Architettura rilevata: $ARCH. Si procede comunque, ma MPS non sarà disponibile."
    echo ""
fi

# Crea la directory padre se non esiste
mkdir -p "$HOME/Library/Application Support/OCRLab"

# Crea venv con uv (preferito) o python3
if command -v uv &>/dev/null; then
    echo "uv trovato — uso uv per creare il venv con Python 3.12..."
    uv venv --python 3.12 "$VENV_DIR"
    PIP="uv pip install --python $PYTHON_EXE"
else
    echo "uv non trovato — uso python3 di sistema..."
    if ! command -v python3 &>/dev/null; then
        echo "ERRORE: python3 non trovato. Installa Python 3.12+ o uv e riprova."
        exit 1
    fi
    python3 -m venv "$VENV_DIR"
    PIP="$VENV_DIR/bin/pip install"
fi

echo ""
echo "Installazione PyTorch (con supporto MPS)..."
$PIP torch torchvision

echo ""
echo "Installazione Surya OCR e dipendenze..."
# transformers<5: la 5.x rompe SuryaModel (all_tied_weights_keys mancante in 0.17.x)
$PIP "surya-ocr" "transformers>=4.40,<5" PyMuPDF Pillow requests

echo ""
echo "Verifica installazione..."
"$PYTHON_EXE" -c "
import torch, surya
mps = torch.backends.mps.is_available()
print(f'  torch {torch.__version__}')
print(f'  surya {surya.__version__}')
print(f'  MPS disponibile: {mps}')
if not mps:
    print('  ATTENZIONE: MPS non disponibile, si userà CPU.')
"

echo ""
echo "=== Installazione completata ==="
echo "OCRLab userà automaticamente $VENV_DIR"
echo "Non è necessaria nessuna configurazione aggiuntiva."
