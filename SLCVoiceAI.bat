@echo off
REM Double-click launcher for the SLCVoiceAI control panel.
REM Uses pythonw.exe so no console window appears; anything that goes wrong
REM still lands in slcvoiceai.log next to this file.

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo   The virtual environment is missing.
    echo.
    echo   Run this once, from this folder:
    echo.
    echo       python -m venv .venv
    echo       .venv\Scripts\activate
    echo       pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m slcvoiceai --gui
