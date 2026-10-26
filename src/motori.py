"""
Motori AI. Di base: Groq per la trascrizione, NVIDIA per riassunti e chat (API key gratuite).
In alternativa, dalle Impostazioni, un altro fornitore con API compatibile OpenAI:
OpenAI, Google Gemini, Anthropic Claude, DeepSeek, Mistral, OpenRouter, un'AI locale
(Ollama, LM Studio: niente esce dal PC) o un indirizzo personalizzato.

Le scelte stanno in .env e si leggono a ogni uso:
  AI_TRASCRIZIONE=groq|openai|locale|pc   WHISPER_LOCALE_URL  WHISPER_LOCALE_MODELLO  WHISPER_PC_MODELLO  MODELLO_TRASCRIZIONE_OPENAI
  AI_TESTO=<id del fornitore, sotto>      <CHIAVE>_API_KEY    URL_<ID>  MODELLO_<ID>  (locale: LLM_LOCALE_URL/MODELLO)
Ogni fornitore ha indirizzo (endpoint), chiave e modello: quelli già pronti si possono correggere,
«Personalizzato» li lascia scegliere tutti.
"""

import os

# id -> nome, indirizzo (o variabile che lo contiene), chiave, modello consigliato, massimo di token in uscita.
# I nomi dei modelli cambiano spesso: quello consigliato si può sempre correggere dalle Impostazioni.
FORNITORI = {
    "nvidia": {"nome": "NVIDIA (gratuito)", "url": "https://integrate.api.nvidia.com/v1", "chiave": "NVIDIA_API_KEY",
               "modello": "", "sito": "https://build.nvidia.com"},
    "openai": {"nome": "OpenAI (GPT)", "url": "https://api.openai.com/v1", "chiave": "OPENAI_API_KEY",
               "modello": "gpt-4.1-mini", "sito": "https://platform.openai.com/api-keys"},
    "gemini": {"nome": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "chiave": "GEMINI_API_KEY", "modello": "gemini-2.5-flash", "sito": "https://aistudio.google.com/apikey"},
    "claude": {"nome": "Anthropic Claude", "url": "https://api.anthropic.com/v1/", "chiave": "ANTHROPIC_API_KEY",
               "modello": "claude-sonnet-5-5", "sito": "https://console.anthropic.com/settings/keys"},
    "deepseek": {"nome": "DeepSeek", "url": "https://api.deepseek.com/v1", "chiave": "DEEPSEEK_API_KEY",
                 "modello": "deepseek-chat", "sito": "https://platform.deepseek.com/api_keys", "max_token": 8192},
    "mistral": {"nome": "Mistral", "url": "https://api.mistral.ai/v1", "chiave": "MISTRAL_API_KEY",
                "modello": "mistral-small-latest", "sito": "https://console.mistral.ai/api-keys"},
    "openrouter": {"nome": "OpenRouter (tanti modelli, una chiave)", "url": "https://openrouter.ai/api/v1",
                   "chiave": "OPENROUTER_API_KEY", "modello": "google/gemini-2.5-flash", "sito": "https://openrouter.ai/keys"},
    "locale": {"nome": "AI locale (Ollama, LM Studio…)", "url_da": "LLM_LOCALE_URL", "chiave": "",
               "modello_da": "LLM_LOCALE_MODELLO", "modello": "qwen2.5:7b"},
    "personalizzato": {"nome": "Personalizzato (compatibile OpenAI)", "url_da": "PERSONALIZZATO_URL",
                       "chiave": "PERSONALIZZATO_API_KEY", "chiave_facoltativa": True, "modello": ""},
}
TRASCRIZIONE = {
    "groq": {"nome": "Groq (gratuito)", "chiave": "GROQ_API_KEY", "sito": "https://console.groq.com/keys"},
    "openai": {"nome": "OpenAI", "chiave": "OPENAI_API_KEY", "sito": "https://platform.openai.com/api-keys"},
    "locale": {"nome": "Whisper locale", "chiave": ""},
    # dentro il programma (faster-whisper su CPU): nessun server da avviare, l'audio non esce dal PC
    "pc": {"nome": "Whisper su questo PC", "chiave": ""},
}
MODELLI_PC = ("large-v3-turbo", "small")
PREDEFINITI = {
    "AI_TRASCRIZIONE": "groq",
    "WHISPER_LOCALE_URL": "http://localhost:8000/v1",
    "WHISPER_LOCALE_MODELLO": "Systran/faster-whisper-large-v3",
    # large-v3-turbo è il più preciso in italiano ma su CPU sta al limite (RTF ~0.6); small è ~2x più veloce
    "WHISPER_PC_MODELLO": "large-v3-turbo",
    "MODELLO_TRASCRIZIONE_OPENAI": "whisper-1",
    "AI_TESTO": "nvidia",
    "LLM_LOCALE_URL": "http://localhost:11434/v1",
    "LLM_LOCALE_MODELLO": "qwen2.5:7b",
}


def var_url(id_: str) -> str:
    return FORNITORI[id_].get("url_da") or f"URL_{id_.upper()}"


def var_modello(id_: str) -> str:
    """NVIDIA non ha un modello solo: usa l'elenco con i ripieghi (vuoto = nessuna variabile)."""
    return "" if id_ == "nvidia" else (FORNITORI[id_].get("modello_da") or f"MODELLO_{id_.upper()}")


PREDEFINITI.update({var_url(k): f.get("url", "") for k, f in FORNITORI.items()})
PREDEFINITI.update({var_modello(k): f["modello"] for k, f in FORNITORI.items() if var_modello(k)})
PREDEFINITI["LLM_LOCALE_URL"] = "http://localhost:11434/v1"
IMPOSTAZIONI = tuple(PREDEFINITI)
CHIAVI = tuple(dict.fromkeys(f["chiave"] for f in (*TRASCRIZIONE.values(), *FORNITORI.values()) if f["chiave"]))


def valore(chiave: str) -> str:
    return os.environ.get(chiave, "").strip() or PREDEFINITI.get(chiave, "")


# ---------------------------------------------------------------------------
# Riassunti e chat
# ---------------------------------------------------------------------------

def fornitore_testo() -> str:
    scelto = valore("AI_TESTO")
    return scelto if scelto in FORNITORI else "nvidia"


def testo_locale() -> bool:
    return fornitore_testo() == "locale"


def testo_nvidia() -> bool:
    """Solo con NVIDIA ci sono più modelli gratuiti tra cui ripiegare se uno è fermo."""
    return fornitore_testo() == "nvidia"


def nome_testo() -> str:
    return FORNITORI[fornitore_testo()]["nome"].split(" (")[0]


def url_testo() -> str:
    return valore(var_url(fornitore_testo()))


def modello_testo() -> str:
    """Il modello scelto per il fornitore attuale (NVIDIA ne usa diversi: qui vuoto)."""
    var = var_modello(fornitore_testo())
    return valore(var) if var else ""


def max_token(richiesti: int) -> int:
    """Alcuni fornitori rifiutano richieste oltre il loro massimo di token in uscita."""
    return min(richiesti, FORNITORI[fornitore_testo()].get("max_token", richiesti))


def client_testo(timeout: float):
    """Client OpenAI per riassunti e chat. RuntimeError con un messaggio leggibile se manca qualcosa."""
    from openai import OpenAI
    id_ = fornitore_testo()
    f = FORNITORI[id_]
    url = url_testo()
    if not url:
        raise RuntimeError(f"Manca l'indirizzo per «{f['nome']}»: scrivilo in Impostazioni.")
    if id_ != "nvidia" and not modello_testo():
        raise RuntimeError(f"Manca il nome del modello per «{f['nome']}»: scrivilo in Impostazioni.")
    api_key = os.environ.get(f["chiave"], "") if f["chiave"] else ""
    if f["chiave"] and not api_key and not f.get("chiave_facoltativa"):
        raise RuntimeError(f"Manca la chiave di {nome_testo()}: inseriscila in Impostazioni"
                           f"{' (' + f['sito'] + ')' if f.get('sito') else ''}.")
    # i server locali non controllano la chiave, ma la libreria ne vuole una
    return OpenAI(base_url=url, api_key=api_key or "nessuna", timeout=timeout, max_retries=0)


def modelli_testo(nvidia: list) -> list:
    """NVIDIA: l'elenco con i ripieghi. Gli altri fornitori: il solo modello scelto nelle Impostazioni."""
    return nvidia if testo_nvidia() else [modello_testo()]


# ---------------------------------------------------------------------------
# Trascrizione
# ---------------------------------------------------------------------------

def fornitore_trascrizione() -> str:
    scelto = valore("AI_TRASCRIZIONE")
    return scelto if scelto in TRASCRIZIONE else "groq"


def trascrizione_locale() -> bool:
    return fornitore_trascrizione() in ("locale", "pc")


def nome_trascrizione() -> str:
    return TRASCRIZIONE[fornitore_trascrizione()]["nome"].split(" (")[0]


def client_trascrizione():
    """Client con .audio.transcriptions.create(): Groq, OpenAI o un server Whisper locale (API OpenAI)."""
    id_ = fornitore_trascrizione()
    t = TRASCRIZIONE[id_]
    api_key = os.environ.get(t["chiave"], "") if t["chiave"] else ""
    if t["chiave"] and not api_key:
        raise RuntimeError(f"Manca la chiave di {nome_trascrizione()}: inseriscila in Impostazioni ({t['sito']}), "
                           "oppure scegli un'altra trascrizione.")
    if id_ == "groq":
        from groq import Groq
        return Groq(api_key=api_key)
    if id_ == "pc":
        return WhisperPC(valore("WHISPER_PC_MODELLO"))
    from openai import OpenAI
    if id_ == "openai":
        return OpenAI(base_url=FORNITORI["openai"]["url"], api_key=api_key, timeout=120, max_retries=0)
    # un blocco da 60 s sul solo processore può richiedere parecchio: timeout largo
    return OpenAI(base_url=valore("WHISPER_LOCALE_URL"), api_key="nessuna", timeout=600, max_retries=0)


def modelli_trascrizione(groq: list) -> list:
    id_ = fornitore_trascrizione()
    if id_ == "openai":
        return [valore("MODELLO_TRASCRIZIONE_OPENAI")]
    if id_ == "locale":
        return [valore("WHISPER_LOCALE_MODELLO")]
    if id_ == "pc":
        return [valore("WHISPER_PC_MODELLO")]
    return groq


class WhisperPC:
    """
    Whisper dentro il programma (faster-whisper, int8 su CPU), con la stessa interfaccia del client Groq:
    client.audio.transcriptions.create(file=(nome, wav), model=..., language=..., prompt=...).
    Il modello si carica al primo blocco (al primo uso viene scaricato: small ~0,5 GB, turbo ~1,6 GB).
    """

    def __init__(self, modello: str):
        try:
            import faster_whisper  # noqa: F401  (solo per un errore chiaro all'avvio)
        except ImportError:
            raise RuntimeError("Whisper su questo PC: manca faster-whisper. Riavvia avvia_dashboard.bat "
                               "(lo installa) oppure: pip install -r requirements.txt")
        self.nome_modello = modello if modello in MODELLI_PC else MODELLI_PC[0]
        self.modello = None
        self.audio = self
        self.transcriptions = self

    def create(self, file, model=None, language=None, prompt=None, temperature=0.0, **_):
        import io
        import time
        import wave
        import numpy as np
        if self.modello is None:
            from faster_whisper import WhisperModel
            thread = max(1, (os.cpu_count() or 4) // 2 - 1)    # ~core fisici - 1: resta CPU per il resto
            print(f"[INFO] Carico Whisper «{self.nome_modello}» su questo PC (al primo uso lo scarica)...")
            self.modello = WhisperModel(self.nome_modello, device="cpu", compute_type="int8", cpu_threads=thread)
        with wave.open(io.BytesIO(file[1])) as wf:
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        inizio = time.time()
        segmenti, _ = self.modello.transcribe(
            audio, language=language, beam_size=1, temperature=temperature,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False, initial_prompt=prompt or None)
        testo = " ".join(s.text.strip() for s in segmenti).strip()
        rtf = (time.time() - inizio) / max(0.1, len(audio) / 16000)
        if rtf > 0.9:
            print(f"[AVVISO] Whisper su questo PC è lento (RTF {rtf:.2f}): la coda può accumularsi, "
                  "prova il modello small.")
        return testo


# ---------------------------------------------------------------------------

def chiavi_necessarie() -> list:
    """Le chiavi che servono con le scelte attuali (con il locale nessuna)."""
    t = TRASCRIZIONE[fornitore_trascrizione()]["chiave"]
    f = FORNITORI[fornitore_testo()]
    testo = f["chiave"] if f["chiave"] and not f.get("chiave_facoltativa") else ""
    return list(dict.fromkeys(k for k in (t, testo) if k))


def catalogo() -> dict:
    """Per la pagina Impostazioni: fornitori, nomi, chiavi e predefiniti (mai i valori delle chiavi)."""
    return {
        "testo": [{"id": k, **{c: f.get(c) for c in ("nome", "chiave", "sito", "chiave_facoltativa")},
                   "var_url": var_url(k), "var_modello": var_modello(k)}
                  for k, f in FORNITORI.items()],
        "trascrizione": [{"id": k, **t} for k, t in TRASCRIZIONE.items()],
        "predefiniti": PREDEFINITI,
    }
