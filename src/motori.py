"""
Motori AI, in due passi:
  1. le CHIAVI (account): fornitore, un nome a scelta e la chiave. Anche più chiavi dello stesso fornitore
     (es. due account Groq), e i servizi senza chiave (AI locale, Whisper su questo PC).
  2. gli USI: trascrizione, riassunti e chat. Ognuno sceglie una delle chiavi e il modello.
Una chiave può servire tutti e tre gli usi, oppure ognuno ha la sua (e i suoi limiti).

Elenco e usi stanno in ai.json (fuori da git); il valore di ogni chiave sta in .env, nella variabile
indicata da "var" (CHIAVE_<ID> per quelle nuove). Senza ai.json si parte dalle vecchie impostazioni di .env.
"""

import json
import os
import re

from comune import ROOT, ENV_PATH, scrivi_dotenv

FILE_AI = ROOT / "ai.json"

# fornitore -> nome, indirizzo, sito della chiave, modelli consigliati per testo e audio (None = non lo fa),
# chiave: "si" | "no" | "facoltativa"; url_libero: l'indirizzo si scrive a mano (AI locale, personalizzato).
# I nomi dei modelli cambiano spesso: quello consigliato è solo il valore di partenza.
FORNITORI = {
    "groq": {"nome": "Groq", "url": "https://api.groq.com/openai/v1", "sito": "https://console.groq.com/keys",
             "testo": "openai/gpt-oss-120b", "audio": "whisper-large-v3-turbo", "chiave": "si"},
    "nvidia": {"nome": "NVIDIA", "url": "https://integrate.api.nvidia.com/v1", "sito": "https://build.nvidia.com",
               "testo": "nvidia/nemotron-3-ultra-550b-a55b", "audio": None, "chiave": "si"},
    "openai": {"nome": "OpenAI", "url": "https://api.openai.com/v1", "sito": "https://platform.openai.com/api-keys",
               "testo": "gpt-4.1-mini", "audio": "whisper-1", "chiave": "si"},
    "gemini": {"nome": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "sito": "https://aistudio.google.com/apikey", "testo": "gemini-2.5-flash", "audio": None, "chiave": "si"},
    "claude": {"nome": "Anthropic Claude", "url": "https://api.anthropic.com/v1/", "sito": "https://console.anthropic.com/settings/keys",
               "testo": "claude-sonnet-5-5", "audio": None, "chiave": "si"},
    "deepseek": {"nome": "DeepSeek", "url": "https://api.deepseek.com/v1", "sito": "https://platform.deepseek.com/api_keys",
                 "testo": "deepseek-chat", "audio": None, "chiave": "si", "max_token": 8192},
    "mistral": {"nome": "Mistral", "url": "https://api.mistral.ai/v1", "sito": "https://console.mistral.ai/api-keys",
                "testo": "mistral-small-latest", "audio": None, "chiave": "si"},
    "openrouter": {"nome": "OpenRouter", "url": "https://openrouter.ai/api/v1", "sito": "https://openrouter.ai/keys",
                   "testo": "google/gemini-2.5-flash", "audio": None, "chiave": "si"},
    "locale": {"nome": "AI locale (Ollama, LM Studio)", "url": "http://localhost:11434/v1",
               "testo": "qwen2.5:7b", "audio": None, "chiave": "no", "url_libero": True},
    "whisper_locale": {"nome": "Whisper locale (server)", "url": "http://localhost:8000/v1",
                       "testo": None, "audio": "Systran/faster-whisper-large-v3", "chiave": "no", "url_libero": True},
    # dentro il programma (faster-whisper su CPU): nessun server da avviare, l'audio non esce dal PC
    "pc": {"nome": "Whisper su questo PC", "url": "", "testo": None, "audio": "large-v3-turbo", "chiave": "no"},
    "personalizzato": {"nome": "Personalizzato (compatibile OpenAI)", "url": "", "testo": "", "audio": "",
                       "chiave": "facoltativa", "url_libero": True},
}
# large-v3-turbo è il più preciso in italiano ma su CPU sta al limite (RTF ~0.6); small è ~2x più veloce
MODELLI_PC = ("large-v3-turbo", "small")
USI = {"trascrizione": "audio", "riassunti": "testo", "chat": "testo"}
NOMI_USI = {"trascrizione": "Trascrizione", "riassunti": "Riassunti", "chat": "Chat"}


def var_chiave(voce: dict) -> str:
    return voce.get("var") or f"CHIAVE_{voce['id'].upper()}"


# ---------------------------------------------------------------------------
# Lettura e salvataggio
# ---------------------------------------------------------------------------

def _dalle_vecchie_impostazioni() -> dict:
    """La configurazione equivalente a quella scritta in .env dalle versioni precedenti."""
    env = lambda k, d="": os.environ.get(k, "").strip() or d
    chiavi = []

    def voce(fornitore, var, nome=None, url=""):
        if not any(c.get("var") == var and c["fornitore"] == fornitore for c in chiavi):
            chiavi.append({"id": f"k{len(chiavi) + 1}", "fornitore": fornitore,
                           "nome": nome or FORNITORI[fornitore]["nome"], "var": var, "url": url})
        return next(c["id"] for c in chiavi if c.get("var") == var and c["fornitore"] == fornitore)

    # trascrizione
    t = env("AI_TRASCRIZIONE", "groq")
    if t == "pc":
        trascrizione = {"chiave": voce("pc", ""), "modello": env("WHISPER_PC_MODELLO", "large-v3-turbo")}
    elif t == "locale":
        trascrizione = {"chiave": voce("whisper_locale", "", url=env("WHISPER_LOCALE_URL", "http://localhost:8000/v1")),
                        "modello": env("WHISPER_LOCALE_MODELLO", "Systran/faster-whisper-large-v3")}
    elif t == "openai":
        trascrizione = {"chiave": voce("openai", "OPENAI_API_KEY"), "modello": env("MODELLO_TRASCRIZIONE_OPENAI", "whisper-1")}
    else:
        trascrizione = {"chiave": voce("groq", "GROQ_API_KEY"),
                        "modello": env("WHISPER_MODEL", "whisper-large-v3").split(",")[0].strip()}

    # riassunti (e chat, che prima li condivideva)
    f = env("AI_TESTO", "nvidia")
    if f == "locale":
        testo = {"chiave": voce("locale", "", url=env("LLM_LOCALE_URL", "http://localhost:11434/v1")),
                 "modello": env("LLM_LOCALE_MODELLO", "qwen2.5:7b")}
    elif f == "personalizzato":
        testo = {"chiave": voce("personalizzato", "PERSONALIZZATO_API_KEY", url=env("PERSONALIZZATO_URL")),
                 "modello": env("MODELLO_PERSONALIZZATO")}
    elif f in FORNITORI and FORNITORI[f]["testo"] is not None:
        var = {"groq": "GROQ_TESTO_API_KEY", "claude": "ANTHROPIC_API_KEY"}.get(f, f"{f.upper()}_API_KEY")
        testo = {"chiave": voce(f, var, "Groq (riassunti)" if f == "groq" else None),
                 "modello": env(f"MODELLO_{f.upper()}", FORNITORI[f]["testo"])}
    else:
        testo = {"chiave": voce("nvidia", "NVIDIA_API_KEY"), "modello": FORNITORI["nvidia"]["testo"]}
    # anche le altre chiavi già scritte in .env, pronte da assegnare
    for f, var, nome in (("groq", "GROQ_API_KEY", None), ("groq", "GROQ_TESTO_API_KEY", "Groq (2)"),
                         ("nvidia", "NVIDIA_API_KEY", None), ("openai", "OPENAI_API_KEY", None),
                         ("gemini", "GEMINI_API_KEY", None), ("claude", "ANTHROPIC_API_KEY", None),
                         ("deepseek", "DEEPSEEK_API_KEY", None), ("mistral", "MISTRAL_API_KEY", None),
                         ("openrouter", "OPENROUTER_API_KEY", None)):
        if env(var):
            voce(f, var, nome)
    return {"chiavi": chiavi, "usi": {"trascrizione": trascrizione, "riassunti": testo, "chat": dict(testo)}}


def config() -> dict:
    """{"chiavi": [{id, fornitore, nome, var, url}], "usi": {uso: {chiave, modello}}}, letta a ogni uso."""
    try:
        dati = json.loads(FILE_AI.read_text(encoding="utf-8"))
        if isinstance(dati, dict) and isinstance(dati.get("chiavi"), list):
            dati.setdefault("usi", {})
            return dati
    except (OSError, ValueError):
        pass
    return _dalle_vecchie_impostazioni()


def salva(dati: dict, valori_chiavi: dict):
    """
    Controlla e salva elenco e usi; valori_chiavi = {id: chiave scritta ora} (vuota = lascia com'è).
    ValueError con un messaggio leggibile se manca qualcosa.
    """
    vecchie = {c["id"]: c for c in config()["chiavi"]}
    chiavi, ids = [], set()
    for c in dati.get("chiavi", []):
        fornitore = str(c.get("fornitore", ""))
        if fornitore not in FORNITORI:
            raise ValueError("Fornitore non valido.")
        id_ = re.sub(r"[^a-z0-9]", "", str(c.get("id", "")).lower())
        if not id_ or id_ in ids:
            id_ = next(f"k{n}" for n in range(1, 1000) if f"k{n}" not in ids and f"k{n}" not in vecchie)
        ids.add(id_)
        voce = {"id": id_, "fornitore": fornitore, "nome": str(c.get("nome", "")).strip() or FORNITORI[fornitore]["nome"],
                "url": str(c.get("url", "")).strip() if FORNITORI[fornitore].get("url_libero") else ""}
        if id_ in vecchie and vecchie[id_].get("var") and vecchie[id_]["fornitore"] == fornitore:
            voce["var"] = vecchie[id_]["var"]          # chiave già in .env con il vecchio nome: resta lì
        if voce["url"] and not voce["url"].startswith(("http://", "https://")):
            raise ValueError(f"«{voce['nome']}»: l'indirizzo deve iniziare con http:// o https://")
        if FORNITORI[fornitore].get("url_libero") and not voce["url"]:
            raise ValueError(f"«{voce['nome']}»: scrivi l'indirizzo (endpoint).")
        chiavi.append(voce)
        c["_id"] = id_

    # le chiavi scritte ora vanno in .env; le voci tolte non cancellano nulla da .env
    nuovi = {}
    for c in dati.get("chiavi", []):
        valore = str(valori_chiavi.get(str(c.get("id", "")), "")).strip()
        if valore:
            nuovi[var_chiave(next(v for v in chiavi if v["id"] == c["_id"]))] = valore
    rinomina = {str(c.get("id", "")): c["_id"] for c in dati.get("chiavi", [])}

    usi = {}
    for uso, tipo in USI.items():
        u = dati.get("usi", {}).get(uso, {})
        id_ = rinomina.get(str(u.get("chiave", "")), str(u.get("chiave", "")))
        voce = next((c for c in chiavi if c["id"] == id_), None)
        if not voce:
            raise ValueError(f"{NOMI_USI[uso]}: scegli quale chiave usare.")
        if FORNITORI[voce["fornitore"]][tipo] is None:
            raise ValueError(f"{NOMI_USI[uso]}: «{voce['nome']}» non fa la {'trascrizione' if tipo == 'audio' else 'parte di testo'}.")
        modello = str(u.get("modello", "")).strip()
        if not modello:
            raise ValueError(f"{NOMI_USI[uso]}: scrivi il modello da usare con «{voce['nome']}».")
        if voce["fornitore"] == "pc" and modello not in MODELLI_PC:
            raise ValueError("Whisper su questo PC: il modello è large-v3-turbo oppure small.")
        usi[uso] = {"chiave": voce["id"], "modello": modello}

    if nuovi:
        scrivi_dotenv(nuovi)
        os.environ.update(nuovi)        # i processi avviati da qui in poi vedono le chiavi nuove
    tmp = FILE_AI.with_suffix(".tmp")
    tmp.write_text(json.dumps({"chiavi": chiavi, "usi": usi}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE_AI)


# ---------------------------------------------------------------------------
# Uso
# ---------------------------------------------------------------------------

def uso(nome_uso: str) -> dict:
    """Quello che serve per chiamare l'AI di un uso: fornitore, nome, indirizzo, chiave, modello."""
    dati = config()
    u = dati["usi"].get(nome_uso, {})
    voce = next((c for c in dati["chiavi"] if c["id"] == u.get("chiave")), None)
    if not voce:
        return {"uso": nome_uso, "fornitore": "", "nome": "", "url": "", "api_key": "", "modello": "", "voce": None}
    f = FORNITORI[voce["fornitore"]]
    return {"uso": nome_uso, "fornitore": voce["fornitore"], "nome": voce["nome"], "voce": voce,
            "url": voce.get("url") or f["url"], "api_key": os.environ.get(var_chiave(voce), "").strip(),
            "modello": u.get("modello", "").strip(), "max_token": f.get("max_token")}


def problema(nome_uso: str) -> str:
    """Cosa manca per usare questo uso ("" = pronto)."""
    u = uso(nome_uso)
    if not u["voce"]:
        return f"{NOMI_USI[nome_uso]}: scegli una chiave in Impostazioni."
    f = FORNITORI[u["fornitore"]]
    if not u["modello"]:
        return f"{NOMI_USI[nome_uso]}: scrivi il modello in Impostazioni."
    if f["chiave"] == "si" and not u["api_key"]:
        return (f"Manca la chiave «{u['nome']}» ({NOMI_USI[nome_uso].lower()}): inseriscila in Impostazioni"
                f"{' (' + f['sito'] + ')' if f.get('sito') else ''}.")
    if u["fornitore"] != "pc" and not u["url"]:
        return f"«{u['nome']}»: manca l'indirizzo (endpoint) in Impostazioni."
    return ""


def pronti() -> bool:
    """Per l'allarme in home: tutti e tre gli usi hanno chiave e modello."""
    return not any(problema(u) for u in USI)


def nome(nome_uso: str) -> str:
    return uso(nome_uso)["nome"]


def modello(nome_uso: str) -> str:
    return uso(nome_uso)["modello"]


def locale(nome_uso: str) -> bool:
    return uso(nome_uso)["fornitore"] in ("locale", "whisper_locale", "pc")


def max_token(nome_uso: str, richiesti: int) -> int:
    """Alcuni fornitori rifiutano richieste oltre il loro massimo di token in uscita."""
    return min(richiesti, uso(nome_uso).get("max_token") or richiesti)


def client(nome_uso: str, timeout: float):
    """Client OpenAI per riassunti o chat. RuntimeError con un messaggio leggibile se manca qualcosa."""
    from openai import OpenAI
    errore = problema(nome_uso)
    if errore:
        raise RuntimeError(errore)
    u = uso(nome_uso)
    # i server locali non controllano la chiave, ma la libreria ne vuole una
    return OpenAI(base_url=u["url"], api_key=u["api_key"] or "nessuna", timeout=timeout, max_retries=0)


def client_trascrizione():
    """Client con .audio.transcriptions.create(): Groq, OpenAI, un server Whisper o Whisper su questo PC."""
    errore = problema("trascrizione")
    if errore:
        raise RuntimeError(errore)
    u = uso("trascrizione")
    if u["fornitore"] == "pc":
        return WhisperPC(u["modello"])
    if u["fornitore"] == "groq":
        from groq import Groq
        return Groq(api_key=u["api_key"])
    from openai import OpenAI
    # un blocco da 60 s su un server locale senza scheda video può richiedere parecchio: timeout largo
    return OpenAI(base_url=u["url"], api_key=u["api_key"] or "nessuna",
                  timeout=600 if locale("trascrizione") else 120, max_retries=0)


def catalogo() -> dict:
    """Per la pagina Impostazioni: fornitori, chiavi (mai i valori: solo se ci sono) e usi."""
    dati = config()
    return {
        "fornitori": [{"id": k, **f} for k, f in FORNITORI.items()],
        "chiavi": [{**c, "ha_chiave": bool(os.environ.get(var_chiave(c), "").strip())} for c in dati["chiavi"]],
        "usi": dati["usi"],
        "nomi_usi": NOMI_USI,
        "tipi_usi": USI,
        "modelli_pc": MODELLI_PC,
        "salvato": FILE_AI.exists(),
    }


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


