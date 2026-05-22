@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Disinstallazione OCRLab

echo ============================================================
echo  Disinstallazione OCRLab
echo ============================================================
echo.
echo Questo script rimuove i venv e i file di configurazione creati
echo dall'installer. Il codice sorgente NON viene eliminato.
echo.
echo Verranno rimossi:
echo   - venv\              (dipendenze principali)
echo   - %APPDATA%\OCRLab\ (venv Surya e dati app)
echo   - avvia_ocrlap.bat  (launcher)
echo   - config.json       (impostazioni salvate)
echo.
set "CONFIRM=n"
set /p CONFIRM="Procedere con la disinstallazione? [s/N]: "
if /i "!CONFIRM!" NEQ "s" goto :annullato

echo.

REM ---- Venv principale ----
if exist venv\ (
    echo Rimozione venv principale...
    rmdir /s /q venv
    echo Fatto.
) else (
    echo Venv principale non trovato, salto.
)

REM ---- Cartella OCRLab in APPDATA (venv Surya + eventuali dati) ----
if exist "%APPDATA%\OCRLab\" (
    echo Rimozione %APPDATA%\OCRLab\ ...
    rmdir /s /q "%APPDATA%\OCRLab"
    echo Fatto.
) else (
    echo Cartella %APPDATA%\OCRLab non trovata, salto.
)

REM ---- Launcher ----
if exist avvia_ocrlap.bat (
    echo Rimozione avvia_ocrlap.bat...
    del /q avvia_ocrlap.bat
    echo Fatto.
)

REM ---- Configurazione ----
if exist config.json (
    set "DEL_CONFIG=n"
    set /p DEL_CONFIG="Eliminare anche config.json (impostazioni e API key)? [s/N]: "
    if /i "!DEL_CONFIG!"=="s" (
        del /q config.json
        echo config.json eliminato.
    ) else (
        echo config.json conservato.
    )
)

echo.
echo ============================================================
echo  Disinstallazione completata.
echo ============================================================
echo Il codice sorgente e' ancora presente in questa cartella.
echo Puoi eliminarla manualmente se non ti serve piu'.
echo.
pause
exit /b 0

:annullato
echo.
echo Disinstallazione annullata.
echo.
pause
