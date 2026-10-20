@echo off
cd /d "%~dp0"
chcp 65001 >nul
call :setup || exit /b 1
".venv\Scripts\python.exe" src\riassunto.py
pause
exit /b 0

:setup
where python >nul 2>nul
if errorlevel 1 (
    echo Python non trovato. Installalo da https://www.python.org/downloads/ ^(spunta "Add to PATH"^).
    pause
    exit /b 1
)
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Creato il file .env: inserisci le tue API key, salva e chiudi il Blocco note.
    notepad ".env"
)
if not exist ".venv" (
    echo Prima installazione delle dipendenze...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)
exit /b 0
