"""
Trascrizione real-time dal microfono (lezione dal vivo) o dall'audio del PC (YouTube, qualsiasi video).

Sorgenti (SORGENTE):
  microfono  il microfono scelto (DISPOSITIVO=<parte del nome>, vuoto = predefinito di Windows).
             L'audio intero si salva anche in progetti/<slug>/audio/ (Opus, ~14 MB l'ora): la lezione non si ripete.
  pc         quello che esce dalle casse o dalle cuffie scelte (WASAPI loopback, senza cavi virtuali):
             guardi il video normalmente e intanto viene trascritto.
Lingua (LINGUA): it (predefinita, va bene anche con termini inglesi), en, oppure auto.

Flusso: sorgente -> blocchi tagliati nelle pause -> Groq Whisper -> progetti/<slug>/trascrizioni/trascrizione_X.txt
I riassunti li fa riassunto.py, da avviare separatamente.

Uso (dalla cartella del progetto):
  python src/trascrittore.py --progetto <slug>
Di solito lo avvia la dashboard (src/webapp.py).
"""

import os
import io
import sys
import time
import wave
import queue
import re
import shutil
import threading
import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from comune import argomento_progetto, path_progetto, stop_richiesto, apri_microfono, apri_audio_pc
import motori

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

# Blocchi tagliati nelle pause: dopo MIN_CHUNK_SECONDS si taglia alla prima pausa di PAUSE_SECONDS;
# se nessuno si ferma, a MAX_CHUNK_SECONDS si taglia nel punto più silenzioso e il resto passa al blocco dopo.
# ~45-60s -> ~600 richieste in 9 ore (limite free Groq: 2000/giorno)
MIN_CHUNK_SECONDS = 45
MAX_CHUNK_SECONDS = 60
PAUSE_SECONDS = 0.4
SAMPLE_RATE = 48000
BLOCK = 1024
WHISPER_RATE = 16000
# l'audio del PC (digitale) e il microfono col docente lontano hanno volumi molto diversi
SILENCE_RMS = {"pc": 300, "microfono": 120}
GUADAGNO_MAX = 8.0          # amplificazione massima dei blocchi a basso volume prima di Whisper
SENZA_AUDIO_SECONDI = 5     # il microfono manda dati anche nel silenzio: se smette, la cattura è bloccata
RIPROVA_SECONDI = 3
OPUS_COMPRESSIONE = 0.9     # 0-1 in libsndfile: 0.9 ≈ 32 kbps, voce chiara e ~14 MB l'ora
PROMPT_TAIL_CHARS = 300     # fine del blocco precedente passata a Whisper per continuità

SORGENTE = os.environ.get("SORGENTE", "pc").strip().lower()
if SORGENTE not in SILENCE_RMS:
    SORGENTE = "pc"
DA_MICROFONO = SORGENTE == "microfono"
DISPOSITIVO_HINT = os.environ.get("DISPOSITIVO", "").strip()
# "it" anche per l'italiano pieno di termini inglesi: con "auto" Whisper sceglie una lingua per blocco
# e a volte prende per inglese un blocco italiano, traducendolo.
LINGUA = os.environ.get("LINGUA", "it").strip().lower() or "it"
LANGUAGE = None if LINGUA == "auto" else LINGUA
# l'audio del microfono si salva sempre; quello del PC solo se richiesto
SALVA_AUDIO = DA_MICROFONO or os.environ.get("SALVA_AUDIO") == "1"
SOGLIA_SILENZIO = SILENCE_RMS[SORGENTE]

# large-v3 è più preciso sull'italiano; i limiti gratuiti Groq sono per modello,
# quindi se large-v3 fallisce (es. 429) lo stesso blocco passa subito a turbo.
# Con il Whisper locale c'è un solo modello, quello scelto nelle Impostazioni.
WHISPER_MODELS = motori.modelli_trascrizione([m.strip() for m in os.environ.get(
    "WHISPER_MODEL", "whisper-large-v3,whisper-large-v3-turbo").split(",") if m.strip()])
MOTORE = motori.nome_trascrizione()

# Frasi che Whisper "inventa" sul silenzio o sul rumore
HALLUCINATIONS = {
    "grazie.", "grazie", "grazie a tutti.", "grazie per la visione.",
    "sottotitoli creati dalla comunità amara.org", "sottotitoli a cura di qtss",
    "thank you.", "thank you", "thanks for watching.", "thank you for watching.", "you", "bye.",
    "subtitles by the amara.org community",
    "...", "",
}


def inventato(text: str) -> bool:
    """Solo frasi inventate, anche ripetute (es. «Grazie a tutti. Grazie a tutti.»)."""
    frasi = re.findall(r"[^.!?]+[.!?]*", text.lower())
    return all(re.sub(r"[!?]+$", ".", f.strip()) in HALLUCINATIONS for f in frasi)


PROGETTO_DIR = path_progetto(argomento_progetto())
OUT_DIR = PROGETTO_DIR / "trascrizioni"
STORICO_DIR = PROGETTO_DIR / "storico"


def sessione_da_usare() -> str:
    """
    Una sessione nuova nasce SOLO se chiesta (NUOVA_SESSIONE=1, dal bottone "Nuova sessione")
    o se il progetto non ne ha ancora; altrimenti si continua l'ultima, anche dopo ore.
    Se l'ultima era già stata archiviata in storico/ se ne rimette una copia in trascrizioni/:
    la copia in storico resta come segnalibro del riassunto, che riparte dal testo nuovo.
    """
    if os.environ.get("NUOVA_SESSIONE") != "1":
        nomi = {f.stem.removeprefix("trascrizione_")
                for cartella in (OUT_DIR, STORICO_DIR) if cartella.exists()
                for f in cartella.glob("trascrizione_*.txt")}
        if nomi:
            ultima = max(nomi)               # il nome è la data-ora d'inizio: il massimo è la più recente
            aperta, archiviata = OUT_DIR / f"trascrizione_{ultima}.txt", STORICO_DIR / f"trascrizione_{ultima}.txt"
            if not aperta.exists():
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archiviata, aperta)
            return ultima
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


SESSION_NAME = sessione_da_usare()
TRANSCRIPT_PATH = OUT_DIR / f"trascrizione_{SESSION_NAME}.txt"
FAILED_DIR = PROGETTO_DIR / "da_ritrascrivere"
AUDIO_DIR = PROGETTO_DIR / "audio"
GLOSSARIO_PATH = PROGETTO_DIR / "glossario.txt"

audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Cattura
# ---------------------------------------------------------------------------

def in_stereo_48k(data: np.ndarray, rate: int) -> np.ndarray:
    """Qualsiasi formato del dispositivo (mono, 4 canali, 44,1 kHz...) -> int16 stereo a SAMPLE_RATE."""
    data = data.astype(np.float32)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if rate != SAMPLE_RATE and len(data):
        n = len(data)
        x = np.linspace(0, n - 1, max(1, round(n * SAMPLE_RATE / rate)))
        data = np.stack([np.interp(x, np.arange(n), data[:, c]) for c in range(2)], axis=1)
    return np.clip(data, -32768, 32767).astype(np.int16)


def apri_sorgente(raw: "queue.Queue[np.ndarray]"):
    """
    Apre microfono o audio del PC; ogni blocco arriva in raw già in stereo a 48 kHz.
    Restituisce (stream, nome). Gli errori di apertura (dispositivo scollegato, bloccato da
    Windows o da un software di sicurezza) salgono a chi chiama con un messaggio leggibile.
    """
    apri = apri_microfono if DA_MICROFONO else apri_audio_pc
    return apri(DISPOSITIVO_HINT, lambda data, rate: raw.put(in_stereo_48k(data, rate)))


def block_rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) if data.size else 0.0


def find_cut(rms: list, min_blocks: int) -> int:
    """Indice del punto più silenzioso (media mobile ~0.2s) tra MIN e la fine del buffer."""
    window = max(1, int(0.2 * SAMPLE_RATE / BLOCK))
    values = np.convolve(np.array(rms), np.ones(window) / window, mode="same")
    return min_blocks + int(np.argmin(values[min_blocks:]))


def capture_audio():
    raw: "queue.Queue[np.ndarray]" = queue.Queue()
    block_seconds = BLOCK / SAMPLE_RATE
    min_blocks = int(MIN_CHUNK_SECONDS / block_seconds)
    max_blocks = int(MAX_CHUNK_SECONDS / block_seconds)
    pause_blocks = int(PAUSE_SECONDS / block_seconds)
    blocks, rms = [], []
    quiet_run = 0
    resto = np.zeros((0, 2), dtype=np.int16)   # i dispositivi mandano blocchi di misura qualsiasi
    # l'audio del PC non manda nulla quando niente suona: quel tempo conta come silenzio
    silenzio = np.zeros((BLOCK, 2), dtype=np.int16)

    def emit(cut: int):
        nonlocal blocks, rms
        start = datetime.datetime.now() - datetime.timedelta(seconds=len(blocks) * block_seconds)
        audio_queue.put((np.concatenate(blocks[:cut]), start))
        blocks, rms = blocks[cut:], rms[cut:]

    def aggiungi(data: np.ndarray):
        nonlocal quiet_run
        blocks.append(data)
        rms.append(block_rms(data))
        # pausa = volume molto sotto il livello medio del parlato di questo blocco
        threshold = max(SOGLIA_SILENZIO, 0.2 * float(np.mean(rms)))
        quiet_run = quiet_run + 1 if rms[-1] < threshold else 0
        if len(blocks) >= min_blocks and quiet_run >= pause_blocks:
            emit(len(blocks) - quiet_run // 2)
            quiet_run = 0
        elif len(blocks) >= max_blocks:
            emit(find_cut(rms, min_blocks))
            quiet_run = 0

    primo = True
    while not stop_event.is_set():
        try:
            stream, nome = apri_sorgente(raw)
        except Exception as exc:
            print(f"[ERRORE] {str(exc).splitlines()[0][:600]} Riprovo tra {RIPROVA_SECONDI} s.")
            stop_event.wait(RIPROVA_SECONDI)
            continue
        print(f"[AUDIO] {'Cattura da' if primo else 'Cattura ripristinata:'} {nome}"
              f" ({'microfono' if DA_MICROFONO else 'audio del PC'}) · lingua: {LINGUA}")
        primo = False
        ultimo_audio = time.time()
        try:
            while not stop_event.is_set():
                try:
                    data = raw.get(timeout=0.5)
                except queue.Empty:
                    if DA_MICROFONO:
                        if time.time() - ultimo_audio > SENZA_AUDIO_SECONDI:
                            print(f"[ERRORE] Dal microfono non arriva più audio da {SENZA_AUDIO_SECONDI} s "
                                  "(scollegato?): lo riapro.")
                            break
                    else:
                        for _ in range(int(0.5 * SAMPLE_RATE / BLOCK)):
                            aggiungi(silenzio)
                    continue
                ultimo_audio = time.time()
                resto = np.concatenate([resto, data])
                while len(resto) >= BLOCK:
                    aggiungi(resto[:BLOCK])
                    resto = resto[BLOCK:]
        finally:
            try:
                stream.close()
            except Exception:
                pass

    if blocks:
        emit(len(blocks))


# ---------------------------------------------------------------------------
# Trascrizione (Groq Whisper)
# ---------------------------------------------------------------------------

def mono_16k(stereo: np.ndarray) -> np.ndarray:
    mono = stereo.astype(np.float32).mean(axis=1)
    factor = SAMPLE_RATE // WHISPER_RATE
    return mono[: len(mono) // factor * factor].reshape(-1, factor).mean(axis=1)


def normalizza(mono: np.ndarray) -> np.ndarray:
    """Blocchi a basso volume (docente lontano dal microfono) alzati fino a ~-4 dB, al massimo GUADAGNO_MAX volte."""
    picco = float(np.percentile(np.abs(mono), 99.9)) if mono.size else 0.0
    if picco <= 0:
        return mono
    return mono * min(GUADAGNO_MAX, max(1.0, 20000 / picco))


def wav_bytes(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(WHISPER_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def to_whisper_wav(stereo: np.ndarray) -> bytes:
    return wav_bytes(np.clip(normalizza(mono_16k(stereo)), -32768, 32767).astype(np.int16))


def is_silent(stereo: np.ndarray) -> bool:
    return block_rms(stereo) < SOGLIA_SILENZIO


class Registrazione:
    """
    Audio intero della sessione in audio/<sessione>_<ora>.ogg, silenzi compresi: se rete o Groq
    cadono durante una lezione, l'audio resta. Opus a ~32 kbps (16 kHz mono) basta per la voce:
    ~14 MB l'ora invece dei ~115 di un WAV. Un file per avvio.
    """

    def __init__(self):
        self.file = None
        if not SALVA_AUDIO:
            return
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        path = AUDIO_DIR / f"{SESSION_NAME}_{datetime.datetime.now():%H%M%S}.ogg"
        try:
            self.file = sf.SoundFile(str(path), "w", samplerate=WHISPER_RATE, channels=1,
                                     format="OGG", subtype="OPUS", compression_level=OPUS_COMPRESSIONE)
            print(f"[INFO] Audio salvato in: {path}")
        except Exception as exc:     # la trascrizione conta più della registrazione: si va avanti senza
            print(f"[WARN] Impossibile salvare l'audio in {path} ({str(exc)[:150]}): continuo senza.")

    def scrivi(self, stereo: np.ndarray):
        if self.file is not None:
            try:
                self.file.write(np.clip(mono_16k(stereo), -32768, 32767).astype(np.int16))
            except Exception as exc:
                print(f"[WARN] Salvataggio dell'audio interrotto ({str(exc)[:150]}): la trascrizione continua.")
                self.chiudi()

    def chiudi(self):
        if self.file is not None:
            try:
                self.file.close()
            except Exception:
                pass
            self.file = None


def load_glossary() -> str:
    """Termini tecnici da glossario.txt (uno per riga o separati da virgola), riletti a ogni blocco."""
    if not GLOSSARIO_PATH.exists():
        return ""
    terms = []
    for line in GLOSSARIO_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.extend(t.strip() for t in line.split(",") if t.strip())
    return ", ".join(terms)


def build_prompt(previous_text: str) -> str:
    # Whisper usa al massimo ~224 token di prompt: glossario + fine del blocco precedente
    glossary = load_glossary()[:500]
    tail = previous_text[-PROMPT_TAIL_CHARS:]
    return " ".join(p for p in (f"Termini: {glossary}." if glossary else "", tail) if p)


def transcribe_wav(groq_client, wav: bytes, prompt: str, attempts: int):
    """
    Restituisce il testo ("" se silenzio/allucinazione) oppure None se Groq (o il Whisper locale) non risponde.
    A ogni tentativo prova i modelli in ordine: se il primo fallisce passa subito al successivo.
    """
    # con LINGUA=auto la lingua non si passa: Whisper la riconosce da solo
    lingua = {"language": LANGUAGE} if LANGUAGE else {}
    # il prompt si manda solo se c'è: i server locali non sempre accettano un campo vuoto
    extra = {**lingua, **({"prompt": prompt} if prompt else {})}
    for attempt in range(attempts):
        for model in WHISPER_MODELS:
            try:
                result = groq_client.audio.transcriptions.create(
                    file=("chunk.wav", wav),
                    model=model,
                    response_format="text",
                    temperature=0.0,
                    **extra,
                )
                text = str(result).strip()
                if inventato(text) or (prompt and text in prompt):
                    return ""
                return text
            except Exception as exc:
                print(f"[ERRORE {MOTORE}] {model}: {str(exc)[:200]}")
        if attempt + 1 < attempts:
            wait = 5 * (attempt + 1)
            print(f"[INFO] Nessun modello Whisper disponibile, riprovo tra {wait}s")
            time.sleep(wait)
    return None


# ---------------------------------------------------------------------------
# Blocchi non trascritti: salvati su disco e recuperati appena Groq risponde
# ---------------------------------------------------------------------------

def save_failed(wav: bytes, start: datetime.datetime) -> Path:
    FAILED_DIR.mkdir(exist_ok=True)
    path = FAILED_DIR / f"{SESSION_NAME}__{start:%H%M%S}.wav"
    path.write_bytes(wav)
    return path


def retry_failed(groq_client, transcript=None):
    """
    Ritrascrive i file in da_ritrascrivere/. Quelli di questa sessione finiscono nel file aperto,
    quelli di sessioni precedenti in coda alla loro trascrizione (riassunto.py li vedrà come testo nuovo).
    """
    if not FAILED_DIR.exists():
        return
    for path in sorted(FAILED_DIR.glob("*.wav")):
        session, _, hhmmss = path.stem.partition("__")
        text = transcribe_wav(groq_client, path.read_bytes(), "", attempts=1)
        if text is None:
            return
        if text:
            line = f"[{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]} recuperato] {text}\n"
            if session == SESSION_NAME and transcript is not None:
                transcript.write(line)
                transcript.flush()
            else:
                with open(OUT_DIR / f"trascrizione_{session}.txt", "a", encoding="utf-8") as f:
                    f.write(line)
            print(line.rstrip())
        path.unlink()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        groq_client = motori.client_trascrizione()
    except RuntimeError as exc:
        sys.exit(str(exc))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if motori.trascrizione_locale():
        print(f"[INFO] Whisper locale: {WHISPER_MODELS[0]} su {motori.valore('WHISPER_LOCALE_URL')}")
    retry_failed(groq_client)

    print(f"[INFO] Trascrizione: {TRANSCRIPT_PATH}")
    print("[INFO] Per i riassunti avvia a parte riassunto.py. Premi CTRL+C per fermare.\n")
    registrazione = Registrazione()

    cattura = threading.Thread(target=capture_audio, daemon=True)
    cattura.start()

    previous_text = ""

    def trascrivi(chunk, start, transcript, attempts):
        nonlocal previous_text
        registrazione.scrivi(chunk)
        if is_silent(chunk):
            return
        wav = to_whisper_wav(chunk)
        text = transcribe_wav(groq_client, wav, build_prompt(previous_text), attempts=attempts)
        if text is None:
            path = save_failed(wav, start)
            line = f"[{start:%H:%M:%S}] [AUDIO NON TRASCRITTO: salvato in {path.name}, verrà recuperato]"
        elif text:
            line = f"[{start:%H:%M:%S}] {text}"
            previous_text = text
        else:
            return
        print(line)
        transcript.write(line + "\n")
        transcript.flush()

    with open(TRANSCRIPT_PATH, "a", encoding="utf-8") as transcript:
        try:
            while True:
                if stop_richiesto():
                    raise KeyboardInterrupt
                try:
                    chunk, start = audio_queue.get(timeout=1)
                except queue.Empty:
                    if not cattura.is_alive():
                        # errore imprevisto nella cattura: si esce con errore, così la dashboard lo mostra
                        print("[ERRORE] La cattura audio si è fermata per un errore: riavvia la trascrizione.")
                        sys.exit(1)
                    continue
                trascrivi(chunk, start, transcript, attempts=4)
                retry_failed(groq_client, transcript)
        except KeyboardInterrupt:
            # chiusura ordinata: si trascrive anche il blocco in corso, non si perde l'ultimo minuto
            stop_event.set()
            cattura.join(timeout=5)
            while not audio_queue.empty():
                chunk, start = audio_queue.get()
                trascrivi(chunk, start, transcript, attempts=2)
            registrazione.chiudi()
            print(f"\n[INFO] Fermato. Trascrizione salvata in: {TRANSCRIPT_PATH}")
            if FAILED_DIR.exists() and any(FAILED_DIR.glob("*.wav")):
                print(f"[INFO] Blocchi non trascritti in {FAILED_DIR}: verranno recuperati al prossimo avvio.")


if __name__ == "__main__":
    main()
