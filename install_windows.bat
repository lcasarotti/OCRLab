@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Installazione OCRLab

echo ============================================================
echo  Installazione OCRLab
echo ============================================================
echo.

REM ---- Verifica Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato nel PATH.
    echo Installa Python 3.10+ da https://python.org oppure:
    echo   winget install Python.Python.3.12
    echo Durante l'installazione spunta "Add Python to PATH".
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python trovato: %PYVER%
echo.

REM ---- Venv principale ----
echo Creazione venv principale...
if exist venv\ goto :venv_exists
python -m venv venv
if errorlevel 1 (
    echo ERRORE: impossibile creare il venv principale.
    pause
    exit /b 1
)
:venv_exists
echo Installazione dipendenze principali...
venv\Scripts\pip install --upgrade pip --quiet
venv\Scripts\pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERRORE: installazione dipendenze fallita.
    pause
    exit /b 1
)
echo Dipendenze principali installate.
echo.

REM ---- Venv Surya (opzionale) ----
echo ============================================================
echo  Installazione Surya OCR (opzionale)
echo ============================================================
echo Surya e' un motore OCR avanzato che richiede PyTorch (~2-5 GB).
echo Puoi saltare questo passo e installarlo in seguito rieseguendo
echo questo script.
echo.
set "INSTALL_SURYA=n"
set /p INSTALL_SURYA="Installare Surya? [s/N]: "
if /i "!INSTALL_SURYA!" NEQ "s" goto :skip_surya

echo.
echo Scegli la variante PyTorch:
echo   1) GPU NVIDIA  (CUDA 12.6+, compatibile con CUDA 13.x)
echo   2) CPU only    (qualsiasi PC, piu' lento)
echo.
echo Nota: per schede con CUDA 11.x scegli CPU e poi installa
echo PyTorch manualmente da https://pytorch.org
echo.
set "TORCH_VARIANT=2"
set /p TORCH_VARIANT="Scelta [1/2]: "
echo.

set "SURYA_DIR=%APPDATA%\OCRLab\surya-venv"
echo Destinazione: %SURYA_DIR%

if exist "%SURYA_DIR%\" goto :surya_venv_exists
python -m venv "%SURYA_DIR%"
if errorlevel 1 (
    echo ERRORE: impossibile creare il venv Surya.
    pause
    exit /b 1
)
:surya_venv_exists

"%SURYA_DIR%\Scripts\pip" install --upgrade pip --quiet

if "!TORCH_VARIANT!" NEQ "1" goto :torch_cpu
echo Installazione PyTorch con supporto GPU (CUDA 12.6+)...
"%SURYA_DIR%\Scripts\pip" install torch torchvision --index-url https://download.pytorch.org/whl/cu126
goto :torch_done
:torch_cpu
echo Installazione PyTorch CPU only...
"%SURYA_DIR%\Scripts\pip" install torch torchvision --index-url https://download.pytorch.org/whl/cpu
:torch_done

echo Installazione Surya OCR e dipendenze...
"%SURYA_DIR%\Scripts\pip" install "surya-ocr" "transformers>=4.40,<5" PyMuPDF Pillow requests

echo.
echo Verifica installazione Surya...
"%SURYA_DIR%\Scripts\python" -c "import torch, surya; print('  torch', torch.__version__); print('  surya', surya.__version__); print('  CUDA:', torch.cuda.is_available())"
if errorlevel 1 (
    echo ATTENZIONE: verifica fallita. Controlla l'output sopra.
) else (
    echo Surya installato correttamente.
)
goto :after_surya

:skip_surya
echo Surya saltato. Riesegui questo script per installarlo in seguito.

:after_surya
echo.

REM ---- Launcher ----
echo Creazione launcher avvia_ocrlap.bat...
set "APP_DIR=%~dp0"
(
    echo @echo off
    echo cd /d "%APP_DIR%"
    echo start "" "%APP_DIR%venv\Scripts\pythonw.exe" run.py
) > avvia_ocrlap.bat
echo Launcher creato: avvia_ocrlap.bat
echo.

REM ---- Istruzioni Tesseract ----
echo ============================================================
echo  Tesseract OCR (opzionale)
echo ============================================================
echo Se vuoi usare il motore Tesseract, installalo separatamente:
echo.
echo   winget install UB-Mannheim.TesseractOCR
echo.
echo oppure scaricalo da:
echo   https://github.com/UB-Mannheim/tesseract/wiki
echo.
echo Dopo l'installazione imposta il percorso in:
echo   OCRLab ^-^> Impostazioni ^-^> Acquisizione ^-^> Tesseract
echo.

echo ============================================================
echo  Installazione completata!
echo ============================================================
echo Per avviare OCRLab fai doppio clic su avvia_ocrlap.bat
echo.
pause
