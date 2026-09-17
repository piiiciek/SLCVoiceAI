@echo off
REM Same panel, but with a console window kept open so startup errors are
REM visible. Use this one if the silent launcher does nothing.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment is missing - see README.md, Install.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m slcvoiceai --gui
echo.
echo  --- the panel has closed ---
pause
