"""
Trascrizione real-time dell'audio di una chiamata (es. Microsoft Teams) in italiano.

Flusso audio:
  App (Teams) -> "CABLE Input" (VB-CABLE, dispositivo di riproduzione predefinito)
             -> "CABLE Output" letto da questo script
             -> rimandato alle cuffie scelte (così continui a sentire)
             -> Groq Whisper (trascrizione) -> NVIDIA Nemotron (riassunto .md)

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
from openai import OpenAI

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

CHUNK_SECONDS = 20          # 20s -> ~1600 richieste in 9 ore (limite free Groq: 2000/giorno)
SUMMARY_EVERY_CHUNKS = 45   # ~15 minuti
SAMPLE_RATE = 48000
WHISPER_RATE = 16000
SILENCE_RMS = 300
LANGUAGE = "it"
NEMOTRON_MODEL = os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-super-120b-a12b")
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
SUMMARY_PATH = OUT_DIR / f"riassunto_{SESSION_NAME}.md"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

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

def capture_audio(capture_idx: int, monitor_idx):
    samples_per_chunk = SAMPLE_RATE * CHUNK_SECONDS
    monitor = open_monitor_stream(monitor_idx)
    raw: "queue.Queue[np.ndarray]" = queue.Queue()
    buffer = []
    collected = 0

    # WDM-KS supporta solo la modalità a callback
    def on_audio(indata, frames, time_info, status):
        raw.put(indata.copy())

    with sd.InputStream(device=capture_idx, samplerate=SAMPLE_RATE, channels=2,
                        dtype="int16", blocksize=1024, callback=on_audio):
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
            buffer.append(data.copy())
            collected += len(data)
            if collected >= samples_per_chunk:
                audio_queue.put(np.concatenate(buffer))
                buffer, collected = [], 0

    if buffer:
        audio_queue.put(np.concatenate(buffer))
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


def transcribe_chunk(groq_client: Groq, stereo: np.ndarray) -> str:
    if is_silent(stereo):
        return ""
    wav_bytes = to_whisper_wav(stereo)
    for attempt in range(4):
        try:
            result = groq_client.audio.transcriptions.create(
                file=("chunk.wav", wav_bytes),
                model="whisper-large-v3-turbo",
                language=LANGUAGE,
                response_format="text",
                temperature=0.0,
            )
            text = str(result).strip()
            return "" if text.lower() in HALLUCINATIONS else text
        except Exception as exc:
            wait = 5 * (attempt + 1)
            print(f"[ERRORE Groq] {exc} - riprovo tra {wait}s")
            time.sleep(wait)
    return ""


# ---------------------------------------------------------------------------
# Riassunto (NVIDIA Nemotron)
# ---------------------------------------------------------------------------

def summarize(client, text: str) -> str:
    try:
        response = client.chat.completions.create(
            model=NEMOTRON_MODEL,
            messages=[
                {"role": "system", "content": "detailed thinking off"},
                {
                    "role": "user",
                    "content": (
                        "Riassumi in italiano questa parte di trascrizione di una riunione. "
                        "Rispondi SOLO in Markdown con questa struttura:\n"
                        "## Punti chiave\n- ...\n\n## Decisioni prese\n- ...\n\n"
                        "## Action item\n- [ ] responsabile: compito\n\n"
                        "Se una sezione è vuota scrivi '- Nessuno'. Sii conciso.\n\n"
                        f"Trascrizione:\n{text}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[ERRORE Nemotron] {exc}")
        return ""


def write_summary(client, pending: list, title: str):
    if client is None or not pending:
        return
    print(f"\n[INFO] Genero riassunto ({title})...")
    summary = summarize(client, " ".join(pending))
    if summary:
        with open(SUMMARY_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n# {title}\n\n{summary}\n")
        print(summary + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not GROQ_API_KEY:
        sys.exit("Manca GROQ_API_KEY: copia .env.example in .env e inserisci la tua chiave.")

    groq_client = Groq(api_key=GROQ_API_KEY)
    nemotron_client = None
    if NVIDIA_API_KEY:
        nemotron_client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    else:
        print("[WARN] NVIDIA_API_KEY non impostata: solo trascrizione, nessun riassunto.")

    capture_idx = find_capture_device()
    monitor_idx = choose_monitor_device()
    print(f"\n[AUDIO] Cattura da: {sd.query_devices(capture_idx)['name']}")
    if monitor_idx is not None:
        print(f"[AUDIO] Ascolto su: {sd.query_devices(monitor_idx)['name']}")
    print(f"[INFO] Trascrizione: {TRANSCRIPT_PATH}")
    print(f"[INFO] Riassunto:    {SUMMARY_PATH}")
    print("[INFO] Premi CTRL+C per fermare.\n")

    threading.Thread(target=capture_audio, args=(capture_idx, monitor_idx), daemon=True).start()

    chunk_count = 0
    pending = []
    with open(TRANSCRIPT_PATH, "a", encoding="utf-8") as transcript:
        try:
            while True:
                try:
                    chunk = audio_queue.get(timeout=1)
                except queue.Empty:
                    continue
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                text = transcribe_chunk(groq_client, chunk)
                if text:
                    line = f"[{timestamp}] {text}"
                    print(line)
                    transcript.write(line + "\n")
                    transcript.flush()
                    pending.append(text)
                chunk_count += 1
                if chunk_count % SUMMARY_EVERY_CHUNKS == 0:
                    write_summary(nemotron_client, pending, f"Riassunto ore {timestamp}")
                    pending = []
        except KeyboardInterrupt:
            print("\n[INFO] Fermato. Genero riassunto finale...")
            stop_event.set()
            write_summary(nemotron_client, pending, "Riassunto finale")
            print(f"[INFO] Trascrizione salvata in: {TRANSCRIPT_PATH}")


if __name__ == "__main__":
    main()
