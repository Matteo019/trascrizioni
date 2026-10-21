"""
Motori AI: cloud (Groq per la trascrizione, NVIDIA per riassunti e chat) oppure locali sul PC.

Locale = qualsiasi server sul PC con un'API compatibile OpenAI:
  testo        Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1), LocalAI...
  trascrizione Speaches / faster-whisper-server (http://localhost:8000/v1), LocalAI, whisper.cpp...
Con il locale niente esce dal computer e le chiavi non servono.

Le scelte stanno in .env (si cambiano dalle Impostazioni) e si leggono a ogni uso:
  AI_TRASCRIZIONE=groq|locale   WHISPER_LOCALE_URL   WHISPER_LOCALE_MODELLO
  AI_TESTO=nvidia|locale        LLM_LOCALE_URL       LLM_LOCALE_MODELLO
"""

import os

NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
PREDEFINITI = {
    "AI_TRASCRIZIONE": "groq",
    "WHISPER_LOCALE_URL": "http://localhost:8000/v1",
    "WHISPER_LOCALE_MODELLO": "Systran/faster-whisper-large-v3",
    "AI_TESTO": "nvidia",
    "LLM_LOCALE_URL": "http://localhost:11434/v1",
    "LLM_LOCALE_MODELLO": "qwen2.5:7b",
}
IMPOSTAZIONI = tuple(PREDEFINITI)


def valore(chiave: str) -> str:
    return os.environ.get(chiave, "").strip() or PREDEFINITI[chiave]


def trascrizione_locale() -> bool:
    return valore("AI_TRASCRIZIONE") == "locale"


def testo_locale() -> bool:
    return valore("AI_TESTO") == "locale"


def chiavi_necessarie() -> list:
    """Le chiavi cloud che servono con le scelte attuali: con il locale non ne serve nessuna."""
    return ([] if trascrizione_locale() else ["GROQ_API_KEY"]) + ([] if testo_locale() else ["NVIDIA_API_KEY"])


def nome_testo() -> str:
    return "AI locale" if testo_locale() else "NVIDIA"


def nome_trascrizione() -> str:
    return "Whisper locale" if trascrizione_locale() else "Groq"


def client_testo(timeout: float):
    """Client OpenAI per riassunti e chat. RuntimeError con un messaggio leggibile se manca la chiave."""
    from openai import OpenAI
    if testo_locale():
        # i server locali non controllano la chiave, ma la libreria ne vuole una
        return OpenAI(base_url=valore("LLM_LOCALE_URL"), api_key="locale", timeout=timeout, max_retries=0)
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        raise RuntimeError("Manca la chiave NVIDIA: inseriscila in Impostazioni (https://build.nvidia.com), "
                           "oppure usa un'AI locale.")
    return OpenAI(base_url=NVIDIA_URL, api_key=api_key, timeout=timeout, max_retries=0)


def modelli_testo(cloud: list) -> list:
    """Con il locale c'è un solo modello, quello scelto nelle Impostazioni."""
    return [valore("LLM_LOCALE_MODELLO")] if testo_locale() else cloud


def client_trascrizione():
    """Client con .audio.transcriptions.create(): Groq o un server Whisper locale (API OpenAI)."""
    if trascrizione_locale():
        from openai import OpenAI
        # un blocco da 60 s sul solo processore può richiedere parecchio: timeout largo
        return OpenAI(base_url=valore("WHISPER_LOCALE_URL"), api_key="locale", timeout=600, max_retries=0)
    from groq import Groq
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("Manca la chiave Groq: inseriscila in Impostazioni (https://console.groq.com/keys), "
                           "oppure usa un Whisper locale.")
    return Groq(api_key=api_key)


def modelli_trascrizione(cloud: list) -> list:
    return [valore("WHISPER_LOCALE_MODELLO")] if trascrizione_locale() else cloud
