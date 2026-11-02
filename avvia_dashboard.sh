#!/usr/bin/env bash
# Linux (Fedora): apre la dashboard nel browser. Equivalente di avvia_dashboard.bat.
#   ./avvia_dashboard.sh            avvia in background e apre il browser
#   ./avvia_dashboard.sh --terminale  resta in primo piano (log a schermo, Ctrl+C per chiudere)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v python3 >/dev/null; then
    echo "Python non trovato. Su Fedora: sudo dnf install python3" >&2; exit 1
fi
for prog in parec pactl; do
    command -v "$prog" >/dev/null || { echo "Manca «$prog»: sudo dnf install pulseaudio-utils" >&2; exit 1; }
done
[ -f .env ] || cp .env.example .env
if [ ! -d .venv ]; then
    echo "Prima installazione delle dipendenze..."
    python3 -m venv .venv || { echo "Su Fedora serve: sudo dnf install python3-pip" >&2; exit 1; }
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, markdown, xhtml2pdf, soundfile, groq, openai, faster_whisper, av" 2>/dev/null; then
    echo "Installazione delle dipendenze della dashboard..."
    .venv/bin/python -m pip install -q -r requirements.txt || { echo "Installazione non riuscita: controlla la connessione e riprova." >&2; exit 1; }
fi
if [ "${1:-}" = "--terminale" ]; then
    exec .venv/bin/python src/webapp.py
fi
nohup .venv/bin/python src/webapp.py >>dashboard.log 2>&1 &
disown
echo "Dashboard avviata: http://127.0.0.1:8765 (log: dashboard.log)"
