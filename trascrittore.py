"""
Trascrizione real-time dell'audio di una chiamata (es. Microsoft Teams) in italiano.

Flusso audio:
  App (Teams) -> "CABLE Input" (VB-CABLE, dispositivo di riproduzione predefinito)
             -> "CABLE Output" letto da questo script
             -> rimandato alle cuffie scelte (così continui a sentire)
             -> Groq Whisper (trascrizione) -> trascrizioni/trascrizione_X.txt
I riassunti li fa riassunto.py, da avviare separatamente.

Uso:
  python trascrittore.py
"""

import os
import io
import sys
import time
import wave
import queue
import threading
import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from groq import Groq

BASE_DIR = Path(__file__).parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv(BASE_DIR / ".env")

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
SILENCE_RMS = 300
PROMPT_TAIL_CHARS = 300     # fine del blocco precedente passata a Whisper per continuità
LANGUAGE = "it"
CAPTURE_DEVICE_HINT = os.environ.get("CAPTURE_DEVICE", "CABLE Output")
MONITOR_DEVICE_HINT = os.environ.get("MONITOR_DEVICE", "")

# Frasi che Whisper "inventa" sul silenzio o sul rumore
HALLUCINATIONS = {
    "grazie.", "grazie", "grazie a tutti.", "grazie per la visione.",
    "sottotitoli creati dalla comunità amara.org", "sottotitoli a cura di qtss",
    "...", "",
}

OUT_DIR = BASE_DIR / "trascrizioni"
OUT_DIR.mkdir(exist_ok=True)
SESSION_NAME = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
TRANSCRIPT_PATH = OUT_DIR / f"trascrizione_{SESSION_NAME}.txt"
FAILED_DIR = OUT_DIR / "da_ritrascrivere"
GLOSSARIO_PATH = BASE_DIR / "glossario.txt"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Dispositivi audio
# ---------------------------------------------------------------------------

def host_api_name(device: dict) -> str:
    return sd.query_hostapis(device["hostapi"])["name"]


def find_capture_device() -> int:
    """
    CABLE Output va letto tramite WDM-KS: su alcuni PC (es. aziendali con software di
    sicurezza) MME/DirectSound/WASAPI rifiutano la registrazione, WDM-KS no.
    Si provano comunque tutte le API, WDM-KS per prima.
    """
    order = ["Windows WDM-KS", "Windows WASAPI", "Windows DirectSound", "MME"]
    devices = sd.query_devices()
    candidates = [
        (order.index(host_api_name(d)) if host_api_name(d) in order else 99, i)
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0 and CAPTURE_DEVICE_HINT.lower() in d["name"].lower()
    ]
    for _, idx in sorted(candidates):
        try:
            with sd.InputStream(device=idx, samplerate=SAMPLE_RATE, channels=2, dtype="int16",
                                callback=lambda *args: None):
                pass
            return idx
        except Exception:
            continue
    raise SystemExit(
        f"Nessun dispositivo di cattura '{CAPTURE_DEVICE_HINT}' utilizzabile.\n"
        "Installa VB-CABLE (https://vb-audio.com/Cable/) e imposta 'CABLE Input' come "
        "dispositivo di riproduzione predefinito di Windows."
    )


def list_monitor_devices() -> list:
    devices = sd.query_devices()
    return [
        i for i, d in enumerate(devices)
        if d["max_output_channels"] > 0
        and host_api_name(d) == "Windows WASAPI"
        and "cable" not in d["name"].lower()
    ]


def choose_monitor_device():
    """Dispositivo su cui rimandare l'audio per continuare a sentire la chiamata."""
    candidates = list_monitor_devices()
    devices = sd.query_devices()

    if MONITOR_DEVICE_HINT:
        for idx in candidates:
            if MONITOR_DEVICE_HINT.lower() in devices[idx]["name"].lower():
                return idx
        print(f"[WARN] MONITOR_DEVICE '{MONITOR_DEVICE_HINT}' non trovato, scegli dalla lista.")

    print("\nSu quale dispositivo vuoi SENTIRE la chiamata?")
    print("  0) Nessuno (non sentire l'audio)")
    for n, idx in enumerate(candidates, start=1):
        print(f"  {n}) {devices[idx]['name']}")
    while True:
        choice = input("Numero: ").strip()
        if choice.isdigit() and 0 <= int(choice) <= len(candidates):
            return None if choice == "0" else candidates[int(choice) - 1]
        print("Scelta non valida.")


def open_monitor_stream(idx):
    if idx is None:
        return None
    try:
        stream = sd.OutputStream(device=idx, samplerate=SAMPLE_RATE, channels=2, dtype="int16")
        stream.start()
        return stream
    except Exception as exc:
        print(f"[WARN] Impossibile riprodurre su {sd.query_devices(idx)['name']}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Cattura + ascolto
# ---------------------------------------------------------------------------

def block_rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) if data.size else 0.0


def find_cut(rms: list, min_blocks: int) -> int:
    """Indice del punto più silenzioso (media mobile ~0.2s) tra MIN e la fine del buffer."""
    window = max(1, int(0.2 * SAMPLE_RATE / BLOCK))
    values = np.convolve(np.array(rms), np.ones(window) / window, mode="same")
    return min_blocks + int(np.argmin(values[min_blocks:]))


def capture_audio(capture_idx: int, monitor_idx):
    monitor = open_monitor_stream(monitor_idx)
    raw: "queue.Queue[np.ndarray]" = queue.Queue()
    block_seconds = BLOCK / SAMPLE_RATE
    min_blocks = int(MIN_CHUNK_SECONDS / block_seconds)
    max_blocks = int(MAX_CHUNK_SECONDS / block_seconds)
    pause_blocks = int(PAUSE_SECONDS / block_seconds)
    blocks, rms = [], []
    quiet_run = 0

    def emit(cut: int):
        nonlocal blocks, rms
        start = datetime.datetime.now() - datetime.timedelta(seconds=len(blocks) * block_seconds)
        audio_queue.put((np.concatenate(blocks[:cut]), start))
        blocks, rms = blocks[cut:], rms[cut:]

    # WDM-KS supporta solo la modalità a callback
    def on_audio(indata, frames, time_info, status):
        raw.put(indata.copy())

    with sd.InputStream(device=capture_idx, samplerate=SAMPLE_RATE, channels=2,
                        dtype="int16", blocksize=BLOCK, callback=on_audio):
        while not stop_event.is_set():
            try:
                data = raw.get(timeout=0.5)
            except queue.Empty:
                continue
            if monitor is not None:
                try:
                    monitor.write(data)
                except Exception:
                    pass
            blocks.append(data)
            rms.append(block_rms(data))

            # pausa = volume molto sotto il livello medio del parlato di questo blocco
            threshold = max(SILENCE_RMS, 0.2 * float(np.mean(rms)))
            quiet_run = quiet_run + 1 if rms[-1] < threshold else 0

            if len(blocks) >= min_blocks and quiet_run >= pause_blocks:
                emit(len(blocks) - quiet_run // 2)
                quiet_run = 0
            elif len(blocks) >= max_blocks:
                emit(find_cut(rms, min_blocks))
                quiet_run = 0

    if blocks:
        emit(len(blocks))
    if monitor is not None:
        monitor.stop()
        monitor.close()


# ---------------------------------------------------------------------------
# Trascrizione (Groq Whisper)
# ---------------------------------------------------------------------------

def to_whisper_wav(stereo: np.ndarray) -> bytes:
    mono = stereo.astype(np.float32).mean(axis=1)
    factor = SAMPLE_RATE // WHISPER_RATE
    mono = mono[: len(mono) // factor * factor].reshape(-1, factor).mean(axis=1)
    pcm = np.clip(mono, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(WHISPER_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def is_silent(stereo: np.ndarray) -> bool:
    if stereo.size == 0:
        return True
    rms = float(np.sqrt(np.mean(stereo.astype(np.float32) ** 2)))
    return rms < SILENCE_RMS


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


def transcribe_wav(groq_client: Groq, wav_bytes: bytes, prompt: str, attempts: int):
    """Restituisce il testo ("" se silenzio/allucinazione) oppure None se Groq non risponde."""
    for attempt in range(attempts):
        try:
            result = groq_client.audio.transcriptions.create(
                file=("chunk.wav", wav_bytes),
                model="whisper-large-v3-turbo",
                language=LANGUAGE,
                response_format="text",
                temperature=0.0,
                prompt=prompt or None,
            )
            text = str(result).strip()
            if text.lower() in HALLUCINATIONS or (prompt and text in prompt):
                return ""
            return text
        except Exception as exc:
            if attempt + 1 < attempts:
                wait = 5 * (attempt + 1)
                print(f"[ERRORE Groq] {exc} - riprovo tra {wait}s")
                time.sleep(wait)
            else:
                print(f"[ERRORE Groq] {exc}")
    return None


# ---------------------------------------------------------------------------
# Blocchi non trascritti: salvati su disco e recuperati appena Groq risponde
# ---------------------------------------------------------------------------

def save_failed(wav_bytes: bytes, start: datetime.datetime) -> Path:
    FAILED_DIR.mkdir(exist_ok=True)
    path = FAILED_DIR / f"{SESSION_NAME}__{start:%H%M%S}.wav"
    path.write_bytes(wav_bytes)
    return path


def retry_failed(groq_client: Groq, transcript=None):
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
    if not GROQ_API_KEY:
        sys.exit("Manca GROQ_API_KEY: copia .env.example in .env e inserisci la tua chiave.")

    groq_client = Groq(api_key=GROQ_API_KEY)
    retry_failed(groq_client)

    capture_idx = find_capture_device()
    monitor_idx = choose_monitor_device()
    print(f"\n[AUDIO] Cattura da: {sd.query_devices(capture_idx)['name']}")
    if monitor_idx is not None:
        print(f"[AUDIO] Ascolto su: {sd.query_devices(monitor_idx)['name']}")
    print(f"[INFO] Trascrizione: {TRANSCRIPT_PATH}")
    print("[INFO] Per i riassunti avvia a parte riassunto.py. Premi CTRL+C per fermare.\n")

    threading.Thread(target=capture_audio, args=(capture_idx, monitor_idx), daemon=True).start()

    previous_text = ""
    with open(TRANSCRIPT_PATH, "a", encoding="utf-8") as transcript:
        try:
            while True:
                try:
                    chunk, start = audio_queue.get(timeout=1)
                except queue.Empty:
                    continue
                if is_silent(chunk):
                    continue
                wav_bytes = to_whisper_wav(chunk)
                text = transcribe_wav(groq_client, wav_bytes, build_prompt(previous_text), attempts=4)
                if text is None:
                    path = save_failed(wav_bytes, start)
                    line = f"[{start:%H:%M:%S}] [AUDIO NON TRASCRITTO: salvato in {path.name}, verrà recuperato]"
                    print(line)
                    transcript.write(line + "\n")
                    transcript.flush()
                    continue
                if text:
                    line = f"[{start:%H:%M:%S}] {text}"
                    print(line)
                    transcript.write(line + "\n")
                    transcript.flush()
                    previous_text = text
                retry_failed(groq_client, transcript)
        except KeyboardInterrupt:
            stop_event.set()
            print(f"\n[INFO] Fermato. Trascrizione salvata in: {TRANSCRIPT_PATH}")
            if FAILED_DIR.exists() and any(FAILED_DIR.glob("*.wav")):
                print(f"[INFO] Blocchi non trascritti in {FAILED_DIR}: verranno recuperati al prossimo avvio.")


if __name__ == "__main__":
    main()
