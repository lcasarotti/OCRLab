#!/usr/bin/env bash
# Rimuove i venv e i file creati dall'installer di OCRLab.
# Il codice sorgente NON viene eliminato.
# Uso: bash uninstall_mac.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
OCRLAP_DATA="$HOME/Library/Application Support/OCRLab"
LAUNCHER="$APP_DIR/avvia_ocrlap.command"
CONFIG="$APP_DIR/config.json"

echo "============================================================"
echo " Disinstallazione OCRLab"
echo "============================================================"
echo ""
echo "Questo script rimuove i venv e i file di configurazione creati"
echo "dall'installer. Il codice sorgente NON viene eliminato."
echo ""
echo "Verranno rimossi:"
echo "  - $VENV_DIR"
echo "  - $OCRLAP_DATA  (venv Surya e dati app)"
echo "  - $LAUNCHER"
echo "  - $CONFIG  (impostazioni salvate)"
echo ""
read -r -p "Procedere con la disinstallazione? [s/N]: " CONFIRM
if [[ "${CONFIRM,,}" != "s" ]]; then
    echo ""
    echo "Disinstallazione annullata."
    exit 0
fi
echo ""

# ---- Venv principale ----
if [ -d "$VENV_DIR" ]; then
    echo "Rimozione venv principale..."
    rm -rf "$VENV_DIR"
    echo "Fatto."
else
    echo "Venv principale non trovato, salto."
fi

# ---- Cartella dati OCRLab (venv Surya + tiktoken cache) ----
if [ -d "$OCRLAP_DATA" ]; then
    echo "Rimozione $OCRLAP_DATA ..."
    rm -rf "$OCRLAP_DATA"
    echo "Fatto."
else
    echo "Cartella dati OCRLab non trovata, salto."
fi

# ---- Launcher ----
if [ -f "$LAUNCHER" ]; then
    echo "Rimozione launcher..."
    rm -f "$LAUNCHER"
    echo "Fatto."
fi

# ---- Configurazione ----
if [ -f "$CONFIG" ]; then
    read -r -p "Eliminare anche config.json (impostazioni e API key)? [s/N]: " DEL_CONFIG
    if [[ "${DEL_CONFIG,,}" == "s" ]]; then
        rm -f "$CONFIG"
        echo "config.json eliminato."
    else
        echo "config.json conservato."
    fi
fi

echo ""
echo "============================================================"
echo " Disinstallazione completata."
echo "============================================================"
echo "Il codice sorgente è ancora presente in:"
echo "  $APP_DIR"
echo "Puoi eliminare la cartella manualmente se non ti serve più."
echo ""
