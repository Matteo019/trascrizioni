@echo off
cd /d "%~dp0"
chcp 65001 >nul

where python >nul 2>nul
if errorlevel 1 (
    echo Python non trovato. Installalo da https://www.python.org/downloads/ ^(spunta "Add to PATH"^).
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Creato il file .env: inserisci la tua GROQ_API_KEY ^(e opzionalmente NVIDIA_API_KEY^), salva e chiudi.
    notepad ".env"
)

if not exist ".venv" (
    echo Prima installazione delle dipendenze...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

".venv\Scripts\python.exe" trascrittore.py
pause
