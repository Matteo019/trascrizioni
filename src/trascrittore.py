"""
Trascrizione real-time dell'audio di una chiamata (es. Microsoft Teams) in italiano.

Flusso audio:
  App (Teams) -> "CABLE Input" (VB-CABLE, dispositivo di riproduzione predefinito)
             -> "CABLE Output" letto da questo script
             -> rimandato alle cuffie scelte (così continui a sentire)
             -> Groq Whisper (trascrizione) -> progetti/<slug>/trascrizioni/trascrizione_X.txt
I riassunti li fa riassunto.py, da avviare separatamente.

Uso (dalla cartella del progetto):
  python src/trascrittore.py                      progetto predefinito (corso-panthera)
  python src/trascrittore.py --progetto <slug>
Di solito lo avvia la dashboard (src/webapp.py).
"""

import os
import io
import sys
import time
import wave
import queue
import shutil
import subprocess
import threading
import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from groq import Groq

from comune import argomento_progetto, path_progetto, stop_richiesto, dispositivi_ascolto, valore_dotenv

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
RIPROVA_ASCOLTO_SECONDI = 10   # cuffie che cambiano modalità o si scollegano: si riprova a riaprirle
# il cavo virtuale manda dati anche nel silenzio: se per qualche secondo non arriva nulla
# la cattura si è bloccata (es. Windows che riconfigura l'audio quando le cuffie Bluetooth cambiano modalità)
SENZA_AUDIO_SECONDI = 5
CUFFIE_MUTE_SECONDI = 15       # cuffie scelte che non suonano da così tanto: errore e riavvio dell'audio
RIAVVIO_CUFFIE_OGNI = 60       # al massimo un riavvio dell'audio al minuto per colpa delle cuffie
# se dal cavo arriva solo silenzio digitale da così tanto, si controlla dove Windows manda l'audio
SILENZIO_CAVO_SECONDI = 20
PROMPT_TAIL_CHARS = 300     # fine del blocco precedente passata a Whisper per continuità
LANGUAGE = "it"
# large-v3 è più preciso sull'italiano; i limiti gratuiti Groq sono per modello,
# quindi se large-v3 fallisce (es. 429) lo stesso blocco passa subito a turbo.
WHISPER_MODELS = [m.strip() for m in os.environ.get(
    "WHISPER_MODEL", "whisper-large-v3,whisper-large-v3-turbo").split(",") if m.strip()]
CAPTURE_DEVICE_HINT = os.environ.get("CAPTURE_DEVICE", "CABLE Output")
MONITOR_DEVICE_HINT = os.environ.get("MONITOR_DEVICE", "")

# Frasi che Whisper "inventa" sul silenzio o sul rumore
HALLUCINATIONS = {
    "grazie.", "grazie", "grazie a tutti.", "grazie per la visione.",
    "sottotitoli creati dalla comunità amara.org", "sottotitoli a cura di qtss",
    "...", "",
}

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
GLOSSARIO_PATH = PROGETTO_DIR / "glossario.txt"
# lanciato dalla dashboard: nessuna domanda a terminale, il dispositivo arriva da MONITOR_DEVICE
DA_DASHBOARD = os.environ.get("DASHBOARD") == "1"

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


def choose_monitor_device():
    """Dispositivo su cui rimandare l'audio per continuare a sentire la chiamata."""
    candidates = [i for i, _ in dispositivi_ascolto()]
    devices = sd.query_devices()

    if MONITOR_DEVICE_HINT:
        for idx in candidates:
            if MONITOR_DEVICE_HINT.lower() in devices[idx]["name"].lower():
                return idx
        if DA_DASHBOARD:
            print(f"[WARN] Dispositivo di ascolto '{MONITOR_DEVICE_HINT}' non trovato: la chiamata non si sentirà in cuffia.")
            return None
        print(f"[WARN] MONITOR_DEVICE '{MONITOR_DEVICE_HINT}' non trovato, scegli dalla lista.")
    elif DA_DASHBOARD:
        return None

    print("\nSu quale dispositivo vuoi SENTIRE la chiamata?")
    print("  0) Nessuno (non sentire l'audio)")
    for n, idx in enumerate(candidates, start=1):
        print(f"  {n}) {devices[idx]['name']}")
    while True:
        choice = input("Numero: ").strip()
        if choice.isdigit() and 0 <= int(choice) <= len(candidates):
            return None if choice == "0" else candidates[int(choice) - 1]
        print("Scelta non valida.")


class Ascolto:
    """
    Rimanda l'audio della chiamata alle cuffie. Usa la frequenza che il dispositivo accetta
    (le cuffie Bluetooth spesso rifiutano 48 kHz) convertendo l'audio al volo; se il dispositivo
    cambia modalità o si scollega, riprova ad aprirlo ogni RIPROVA_ASCOLTO_SECONDI.
    Se dopo CUFFIE_MUTE_SECONDI ancora non suona è un errore vero: lo segnala e chiede di
    riavviare l'audio (serve_riavvio), perché Windows a volte invalida il dispositivo per sempre.
    Se nelle impostazioni l'ascolto viene tolto, smette senza errori.
    """

    def __init__(self, idx):
        self.idx, self.stream = idx, None
        self.nome = sd.query_devices(idx)["name"] if idx is not None else None
        self.rate, self.canali = SAMPLE_RATE, 2
        self.prossimo_tentativo = 0.0
        self.guasto_dal = None          # da quando le cuffie non suonano
        self.errore_dato = False
        self.serve_riavvio = False
        self.apri()

    def disattivato(self) -> bool:
        """Cuffie tolte dalle impostazioni mentre la trascrizione gira: niente più tentativi né avvisi."""
        if self.idx is not None and DA_DASHBOARD and not valore_dotenv("MONITOR_DEVICE"):
            self.chiudi()
            self.idx = None
            self.serve_riavvio = False
            print("[AUDIO] Ascolto in cuffia disattivato dalle impostazioni: la trascrizione continua.")
        return self.idx is None

    def guasto(self, motivo: str):
        if self.guasto_dal is None:
            self.guasto_dal = time.time()
            print(f"[WARN] Ascolto in cuffia interrotto ({motivo[:90]}): riprovo (la trascrizione continua).")
        elif not self.errore_dato and time.time() - self.guasto_dal > CUFFIE_MUTE_SECONDI:
            self.errore_dato = True
            self.serve_riavvio = True
            print(f"[ERRORE] Non senti la chiamata in «{self.nome}» da {CUFFIE_MUTE_SECONDI} s: riavvio l'audio. "
                  "Se continua, togli e rimetti le cuffie oppure scegli «nessuna» nelle impostazioni.")

    def apri(self):
        if self.disattivato():
            return
        self.prossimo_tentativo = time.time() + RIPROVA_ASCOLTO_SECONDI
        try:
            info = sd.query_devices(self.idx)
        except Exception as exc:            # cuffie sparite: la trascrizione non deve fermarsi
            self.guasto(str(exc))
            return
        errore = None
        for rate in dict.fromkeys([SAMPLE_RATE, int(info["default_samplerate"]), 44100, 32000, 16000]):
            for canali in (2, 1):
                try:
                    stream = sd.OutputStream(device=self.idx, samplerate=rate, channels=canali, dtype="int16")
                    stream.start()
                except Exception as exc:
                    errore = exc
                    continue
                self.stream, self.rate, self.canali = stream, rate, canali
                if rate != SAMPLE_RATE or canali != 2 or self.guasto_dal is not None:
                    print(f"[AUDIO] Ascolto su {info['name']} a {rate} Hz, {canali} canali")
                self.guasto_dal, self.errore_dato = None, False
                return
        self.guasto(f"impossibile riprodurre su {info['name']}: {errore}")

    def converti(self, data: np.ndarray) -> np.ndarray:
        if self.canali == 1:
            data = data.mean(axis=1, keepdims=True)
        if self.rate != SAMPLE_RATE:
            n = len(data)
            x = np.linspace(0, n - 1, max(1, round(n * self.rate / SAMPLE_RATE)))
            data = np.stack([np.interp(x, np.arange(n), data[:, c]) for c in range(data.shape[1])], axis=1)
        return np.ascontiguousarray(data.astype(np.int16))

    def scrivi(self, data: np.ndarray):
        if self.idx is None:
            return
        if self.stream is None:
            if time.time() < self.prossimo_tentativo:
                return
            self.apri()
            if self.stream is None:
                return
        try:
            self.stream.write(self.converti(data))
        except Exception as exc:
            self.chiudi()
            self.prossimo_tentativo = time.time() + 2
            self.guasto(str(exc))

    def chiudi(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


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


def riapri_dispositivi(nome_ascolto):
    """
    Riavvia PortAudio (così vede di nuovo i dispositivi come sono adesso) e ritrova
    il cavo virtuale e le cuffie per nome: dopo una riconfigurazione gli indici possono cambiare.
    """
    sd._terminate()
    sd._initialize()
    capture_idx = find_capture_device()
    monitor_idx = None
    if nome_ascolto:
        monitor_idx = next((i for i, n in dispositivi_ascolto() if n == nome_ascolto), None)
        if monitor_idx is None:
            print(f"[WARN] Cuffie «{nome_ascolto}» non trovate: la trascrizione continua senza ascolto.")
    return capture_idx, monitor_idx


ultimo_suono = time.time()   # ultimo blocco non del tutto silenzioso arrivato dal cavo


def uscita_predefinita() -> str:
    """
    Nome dell'uscita audio predefinita di Windows. Si legge in un processo a parte:
    PortAudio qui dentro vede i dispositivi com'erano all'avvio, non i cambi successivi.
    """
    codice = "import sounddevice as sd; print(sd.query_devices(kind='output')['name'])"
    r = subprocess.run([sys.executable, "-c", codice], capture_output=True, text=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return r.stdout.strip()


def controlla_uscita():
    """
    Caso «io sento ma la trascrizione no»: quando si collegano le cuffie Windows spesso le rende
    uscita predefinita, e Teams suona lì invece che su CABLE Input. Il cavo allora manda solo
    silenzio. Se succede, si controlla l'uscita predefinita e lo si segnala.
    """
    segnalato = False
    while not stop_event.wait(SILENZIO_CAVO_SECONDI):
        if time.time() - ultimo_suono < SILENZIO_CAVO_SECONDI:
            if segnalato:
                print("[AUDIO] L'audio della chiamata arriva di nuovo alla trascrizione.")
                segnalato = False
            continue
        try:
            nome = uscita_predefinita()
        except Exception:
            continue
        if nome and "cable" not in nome.lower() and not segnalato:
            segnalato = True
            print(f"[ERRORE] Windows manda l'audio a «{nome}» invece che a «CABLE Input»: tu senti la chiamata "
                  "ma la trascrizione no. Rimetti CABLE Input come uscita (icona del volume di Windows, "
                  "o altoparlante in Teams): la trascrizione riprende da sola.")


def capture_audio(capture_idx: int, monitor_idx):
    global ultimo_suono
    monitor = Ascolto(monitor_idx)
    ultimo_riavvio_cuffie = 0.0
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

    bloccata = False
    while not stop_event.is_set():
        if bloccata:
            # l'audio non arrivava più: si riaprono cattura e cuffie, l'audio già raccolto resta
            monitor.chiudi()
            try:
                capture_idx, monitor_idx = riapri_dispositivi(monitor.nome if monitor.idx is not None else None)
            except BaseException as exc:       # anche SystemExit: cavo non trovato, si riprova
                print(f"[ERRORE] Cattura audio non disponibile ({str(exc).splitlines()[0][:100]}): riprovo tra 3 s.")
                stop_event.wait(3)
                continue
            monitor = Ascolto(monitor_idx)
        try:
            stream = sd.InputStream(device=capture_idx, samplerate=SAMPLE_RATE, channels=2,
                                    dtype="int16", blocksize=BLOCK, callback=on_audio)
            stream.start()
        except Exception as exc:
            print(f"[ERRORE] Impossibile aprire la cattura audio ({str(exc)[:100]}): riprovo tra 3 s.")
            bloccata = True
            stop_event.wait(3)
            continue
        if bloccata:
            print("[AUDIO] Cattura audio ripristinata: la trascrizione riprende.")
        bloccata = False
        ultimo_audio = time.time()
        try:
            while not stop_event.is_set():
                try:
                    data = raw.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - ultimo_audio > SENZA_AUDIO_SECONDI:
                        print(f"[ERRORE] L'audio della chiamata non arriva più da {SENZA_AUDIO_SECONDI} s "
                              "(cattura bloccata): la riapro.")
                        bloccata = True
                        break
                    continue
                ultimo_audio = time.time()
                if data.any():
                    ultimo_suono = ultimo_audio
                monitor.scrivi(data)
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

                # cuffie che non tornano da sole: Windows le ha invalidate, serve riaprire tutto l'audio
                if monitor.serve_riavvio and time.time() - ultimo_riavvio_cuffie > RIAVVIO_CUFFIE_OGNI:
                    ultimo_riavvio_cuffie = time.time()
                    bloccata = True
                    break
        finally:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass

    if blocks:
        emit(len(blocks))
    monitor.chiudi()


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
    """
    Restituisce il testo ("" se silenzio/allucinazione) oppure None se Groq non risponde.
    A ogni tentativo prova i modelli in ordine: se il primo fallisce passa subito al successivo.
    """
    for attempt in range(attempts):
        for model in WHISPER_MODELS:
            try:
                result = groq_client.audio.transcriptions.create(
                    file=("chunk.wav", wav_bytes),
                    model=model,
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
                print(f"[ERRORE Groq] {model}: {str(exc)[:200]}")
        if attempt + 1 < attempts:
            wait = 5 * (attempt + 1)
            print(f"[INFO] Nessun modello Whisper disponibile, riprovo tra {wait}s")
            time.sleep(wait)
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    groq_client = Groq(api_key=GROQ_API_KEY)
    retry_failed(groq_client)

    capture_idx = find_capture_device()
    monitor_idx = choose_monitor_device()
    print(f"\n[AUDIO] Cattura da: {sd.query_devices(capture_idx)['name']}")
    if monitor_idx is not None:
        print(f"[AUDIO] Ascolto su: {sd.query_devices(monitor_idx)['name']}")
    print(f"[INFO] Trascrizione: {TRANSCRIPT_PATH}")
    print("[INFO] Per i riassunti avvia a parte riassunto.py. Premi CTRL+C per fermare.\n")

    cattura = threading.Thread(target=capture_audio, args=(capture_idx, monitor_idx), daemon=True)
    cattura.start()
    threading.Thread(target=controlla_uscita, daemon=True).start()

    previous_text = ""

    def trascrivi(chunk, start, transcript, attempts):
        nonlocal previous_text
        if is_silent(chunk):
            return
        wav_bytes = to_whisper_wav(chunk)
        text = transcribe_wav(groq_client, wav_bytes, build_prompt(previous_text), attempts=attempts)
        if text is None:
            path = save_failed(wav_bytes, start)
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
            print(f"\n[INFO] Fermato. Trascrizione salvata in: {TRANSCRIPT_PATH}")
            if FAILED_DIR.exists() and any(FAILED_DIR.glob("*.wav")):
                print(f"[INFO] Blocchi non trascritti in {FAILED_DIR}: verranno recuperati al prossimo avvio.")


if __name__ == "__main__":
    main()
