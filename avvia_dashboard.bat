@echo off
rem Unico punto di ingresso: apre la dashboard nel browser, senza finestre di terminale.
cd /d "%~dp0"
chcp 65001 >nul
where python >nul 2>nul
if errorlevel 1 (
    echo Python non trovato. Installalo da https://www.python.org/downloads/ ^(spunta "Add to PATH"^).
    pause
    exit /b 1
)
if not exist ".env" copy ".env.example" ".env" >nul
if not exist ".venv" (
    echo Prima installazione delle dipendenze...
    python -m venv .venv
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, markdown, xhtml2pdf, pyaudiowpatch, soundfile, sounddevice, groq, openai, faster_whisper, av" >nul 2>nul
if errorlevel 1 (
    echo Installazione delle dipendenze della dashboard...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo Installazione non riuscita: controlla la connessione e riprova.
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" src\webapp.py
exit /b 0
