#!/usr/bin/env bash
# Linux: avvia riassunto da terminale (progetto "Generale"), senza dashboard.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
[ -f .env ] || { cp .env.example .env; echo "Creato .env: inserisci le API key e rilancia."; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -c "import fastapi, soundfile, groq, openai, faster_whisper, av" 2>/dev/null || .venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python src/riassunto.py "$@"
