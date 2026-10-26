"""
Benchmark Whisper locale (faster-whisper, int8 su CPU) per decidere se affiancare Groq.

Per ogni modello misura su spezzoni da 10 s e 30 s di un WAV reale:
  - tempo di caricamento del modello
  - tempo di trascrizione (media su più ripetizioni, dopo un giro di riscaldamento)
  - RTF = tempo / durata audio (deve stare sotto 1, meglio sotto 0.5)
  - RAM di picco del processo
e stampa il testo, per giudicare la qualità in italiano.

Uso:
  pip install faster-whisper psutil
  python strumenti/benchmark_whisper.py percorso\\audio.wav
  python strumenti/benchmark_whisper.py audio.wav --modelli small large-v3-turbo --ripetizioni 3 --thread 4
"""

import argparse
import os
import sys
import threading
import time
import wave

import numpy as np
import psutil
from faster_whisper import WhisperModel

WHISPER_RATE = 16000
MODELLI = ["small", "medium", "large-v3-turbo"]
DURATE = [10, 30]


def carica_wav(path: str) -> np.ndarray:
    """WAV qualsiasi (int16, mono/stereo, qualsiasi rate) -> float32 mono 16 kHz."""
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            sys.exit("Serve un WAV PCM a 16 bit.")
        rate, canali = wf.getframerate(), wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    audio = pcm.reshape(-1, canali).astype(np.float32).mean(axis=1) / 32768.0
    if rate != WHISPER_RATE:
        n = round(len(audio) * WHISPER_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


class PiccoRam:
    """Campiona la RAM (RSS) del processo ogni 50 ms e tiene il massimo."""

    def __init__(self):
        self.proc = psutil.Process(os.getpid())
        self.picco = 0
        self._stop = threading.Event()

    def __enter__(self):
        self.picco = self.proc.memory_info().rss
        self._t = threading.Thread(target=self._giro, daemon=True)
        self._t.start()
        return self

    def _giro(self):
        while not self._stop.wait(0.05):
            self.picco = max(self.picco, self.proc.memory_info().rss)

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.picco = max(self.picco, self.proc.memory_info().rss)


def trascrivi(modello: WhisperModel, audio: np.ndarray, prompt: str) -> str:
    # stessi parametri previsti per l'integrazione: italiano fisso, beam 1, VAD, niente loop sul testo precedente
    segmenti, _ = modello.transcribe(
        audio,
        language="it",
        beam_size=1,
        temperature=0.0,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
    )
    return " ".join(s.text.strip() for s in segmenti)   # transcribe è lazy: il lavoro avviene qui


def mb(byte: int) -> str:
    return f"{byte / 2**20:,.0f} MB"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav", help="WAV con parlato reale (almeno 30 s), es. un file di da_ritrascrivere/")
    ap.add_argument("--modelli", nargs="+", default=MODELLI)
    ap.add_argument("--ripetizioni", type=int, default=2)
    ap.add_argument("--thread", type=int, default=max(1, (psutil.cpu_count(logical=False) or 2) - 1),
                    help="thread CPU (default: core fisici - 1, per lasciare spazio alla chiamata)")
    ap.add_argument("--prompt", default="", help="es. termini del glossario")
    args = ap.parse_args()

    audio = carica_wav(args.wav)
    spezzoni = {d: audio[: d * WHISPER_RATE] for d in DURATE if len(audio) >= d * WHISPER_RATE}
    if not spezzoni:
        sys.exit(f"Audio troppo corto ({len(audio) / WHISPER_RATE:.1f} s): servono almeno {DURATE[0]} s.")

    vm = psutil.virtual_memory()
    print(f"CPU: {psutil.cpu_count(logical=False)} core fisici / {psutil.cpu_count()} logici, thread usati: {args.thread}")
    print(f"RAM: {mb(vm.total)} totali, {mb(vm.available)} libere\n")

    risultati = []
    for nome in args.modelli:
        print(f"=== {nome} (int8) ===")
        base = psutil.Process(os.getpid()).memory_info().rss
        with PiccoRam() as ram:
            t0 = time.perf_counter()
            # al primo uso scarica il modello da Hugging Face (small ~0.5 GB, medium ~1.5 GB, turbo ~1.6 GB)
            modello = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=args.thread)
            caricamento = time.perf_counter() - t0
            trascrivi(modello, spezzoni[min(spezzoni)], args.prompt)    # riscaldamento
            for durata, pezzo in spezzoni.items():
                tempi = []
                for _ in range(args.ripetizioni):
                    t0 = time.perf_counter()
                    testo = trascrivi(modello, pezzo, args.prompt)
                    tempi.append(time.perf_counter() - t0)
                media = sum(tempi) / len(tempi)
                risultati.append((nome, durata, media, media / durata))
                print(f"  {durata:>2} s di audio -> {media:5.2f} s  (RTF {media / durata:.2f})")
                print(f"     «{testo}»")
        print(f"  caricamento {caricamento:.1f} s, RAM di picco +{mb(ram.picco - base)} (processo {mb(ram.picco)})\n")
        risultati_ram = ram.picco - base
        risultati[-len(spezzoni):] = [r + (risultati_ram,) for r in risultati[-len(spezzoni):]]
        del modello

    print("Riepilogo")
    print(f"{'modello':<16}{'audio':>6}{'tempo':>9}{'RTF':>7}{'RAM':>10}  esito")
    for nome, durata, media, rtf, ram in risultati:
        esito = "ottimo" if rtf < 0.5 else "al limite" if rtf < 1 else "TROPPO LENTO"
        print(f"{nome:<16}{durata:>5}s{media:>8.2f}s{rtf:>7.2f}{mb(ram):>10}  {esito}")


if __name__ == "__main__":
    main()
