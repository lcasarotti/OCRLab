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
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
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

REM ---- Rilevamento GPU NVIDIA ----
set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu"
set "TORCH_LABEL=CPU only"
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo GPU NVIDIA non rilevata ^(nvidia-smi non trovato^) — uso PyTorch CPU only.
) else (
    echo GPU NVIDIA rilevata. Lettura versione CUDA...
    for /f "tokens=*" %%L in ('nvidia-smi 2^>^&1') do (
        echo %%L | findstr /i "CUDA Version" >nul 2>&1
        if not errorlevel 1 (
            for /f "tokens=3" %%V in ("%%L") do set "CUDA_VER=%%V"
        )
    )
    if defined CUDA_VER (
        echo Versione CUDA rilevata: !CUDA_VER!
        for /f "tokens=1 delims=." %%M in ("!CUDA_VER!") do set "CUDA_MAJOR=%%M"
        if !CUDA_MAJOR! GEQ 13 (
            set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu130"
            set "TORCH_LABEL=CUDA 13.0 (Blackwell)"
        ) else (
            set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126"
            set "TORCH_LABEL=CUDA 12.6"
        )
    ) else (
        set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126"
        set "TORCH_LABEL=CUDA 12.6 (default GPU)"
    )
)
echo Backend selezionato: !TORCH_LABEL!
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

"%SURYA_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo Installazione PyTorch (!TORCH_LABEL!)...
"%SURYA_DIR%\Scripts\pip" install torch torchvision --index-url !TORCH_INDEX_URL!

echo Installazione Surya OCR e dipendenze...
"%SURYA_DIR%\Scripts\pip" install "surya-ocr" "transformers>=4.40,<5" PyMuPDF Pillow requests

echo.
echo Verifica installazione Surya...
echo import importlib.metadata > "%TEMP%\ocrlap_check.py"
echo import torch >> "%TEMP%\ocrlap_check.py"
echo import surya >> "%TEMP%\ocrlap_check.py"
echo print("  torch:", importlib.metadata.version("torch")) >> "%TEMP%\ocrlap_check.py"
echo print("  surya:", importlib.metadata.version("surya-ocr")) >> "%TEMP%\ocrlap_check.py"
echo cuda_ok = False >> "%TEMP%\ocrlap_check.py"
echo if torch.cuda.is_available(): >> "%TEMP%\ocrlap_check.py"
echo     try: >> "%TEMP%\ocrlap_check.py"
echo         t = torch.ones(4, 4).cuda() >> "%TEMP%\ocrlap_check.py"
echo         _ = torch.matmul(t, t) >> "%TEMP%\ocrlap_check.py"
echo         cuda_ok = True >> "%TEMP%\ocrlap_check.py"
echo     except Exception as e: >> "%TEMP%\ocrlap_check.py"
echo         print("  ATTENZIONE: GPU presente ma non compatibile con questa build PyTorch:", e) >> "%TEMP%\ocrlap_check.py"
echo         print("  Reinstalla Surya scegliendo la variante CUDA corretta per la tua GPU.") >> "%TEMP%\ocrlap_check.py"
echo print("  CUDA funzionante:", cuda_ok) >> "%TEMP%\ocrlap_check.py"
"%SURYA_DIR%\Scripts\python.exe" "%TEMP%\ocrlap_check.py"
if errorlevel 1 (
    echo ATTENZIONE: verifica fallita. Controlla l'output sopra.
) else (
    echo Surya installato correttamente.
)
del "%TEMP%\ocrlap_check.py" 2>nul
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
