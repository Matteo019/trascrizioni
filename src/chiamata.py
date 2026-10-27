"""
Sorgente «chiamata»: audio di Teams (o altro) che passa dal cavo virtuale VB-CABLE, per i PC
aziendali dove la cattura diretta (loopback) delle casse è bloccata.

  Teams -> altoparlante «CABLE Input» -> letto qui da «CABLE Output» -> trascrizione
                                                    \\-> rimandato alle cuffie scelte (così si sente)

CABLE Output si legge tramite WDM-KS per primo: su alcuni PC aziendali i software di sicurezza
fanno rifiutare la registrazione a MME/DirectSound/WASAPI, a WDM-KS no.
"""

import queue
import threading
import time

import numpy as np

CAVO = "CABLE Output"
FREQUENZA = 48000
RIPROVA_CUFFIE_SECONDI = 10


def _sd():
    import sounddevice as sd
    return sd


def cuffie() -> dict:
    """Dispositivi su cui sentire la chiamata (uscite WASAPI, senza il cavo) per la combobox."""
    sd = _sd()
    nomi = sorted({d["name"] for d in sd.query_devices()
                   if d["max_output_channels"] > 0
                   and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
                   and "cable" not in d["name"].lower()})
    try:
        predefinito = sd.query_devices(kind="output")["name"]
    except Exception:
        predefinito = ""
    return {"nomi": nomi, "predefinito": "" if "cable" in predefinito.lower() else predefinito}


def _trova_cavo(sd) -> int:
    ordine = ["Windows WDM-KS", "Windows WASAPI", "Windows DirectSound", "MME"]
    candidati = sorted(
        (ordine.index(api) if api in ordine else 99, i)
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0 and CAVO.lower() in d["name"].lower()
        for api in [sd.query_hostapis(d["hostapi"])["name"]])
    for _, idx in candidati:
        try:
            with sd.InputStream(device=idx, samplerate=FREQUENZA, channels=2, dtype="int16",
                                callback=lambda *args: None):
                pass
            return idx
        except Exception:
            continue
    raise RuntimeError(f"Non trovo «{CAVO}» utilizzabile: installa VB-CABLE (https://vb-audio.com/Cable/) "
                       "e in Teams imposta l'altoparlante su «CABLE Input».")


def _trova_cuffie(sd, hint: str):
    if not hint:
        return None
    for i, d in enumerate(sd.query_devices()):
        if (d["max_output_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
                and hint.lower() in d["name"].lower() and "cable" not in d["name"].lower()):
            return i
    return None


class Ascolto:
    """
    Rimanda l'audio alle cuffie con la frequenza che accettano (le Bluetooth spesso rifiutano 48 kHz).
    Se le cuffie cambiano modalità o si scollegano riprova ogni RIPROVA_CUFFIE_SECONDI:
    la trascrizione non si ferma mai per colpa delle cuffie.
    """

    def __init__(self, sd, hint: str):
        self.sd, self.hint = sd, hint
        self.stream, self.nome = None, ""
        self.rate, self.canali = FREQUENZA, 2
        self.prossimo_tentativo = 0.0
        self.avvisato = False
        self.apri()

    def apri(self):
        if not self.hint:                  # nessuna cuffia scelta: solo trascrizione, nessun avviso
            return
        self.prossimo_tentativo = time.time() + RIPROVA_CUFFIE_SECONDI
        idx = _trova_cuffie(self.sd, self.hint)
        if idx is None:
            if not self.avvisato:
                print(f"[WARN] Cuffie «{self.hint}» non trovate: la chiamata non si sente, la trascrizione continua.")
                self.avvisato = True
            return
        info = self.sd.query_devices(idx)
        for rate in dict.fromkeys([FREQUENZA, int(info["default_samplerate"]), 44100, 32000, 16000]):
            for canali in (2, 1):
                try:
                    stream = self.sd.OutputStream(device=idx, samplerate=rate, channels=canali, dtype="int16")
                    stream.start()
                except Exception:
                    continue
                self.stream, self.rate, self.canali, self.nome = stream, rate, canali, info["name"]
                print(f"[AUDIO] Ascolto su: {info['name']}" + (f" ({rate} Hz)" if rate != FREQUENZA else ""))
                self.avvisato = False
                return
        if not self.avvisato:
            print(f"[WARN] Non riesco a suonare su «{info['name']}»: riprovo (la trascrizione continua).")
            self.avvisato = True

    def scrivi(self, data: np.ndarray):
        if not self.hint:
            return
        if self.stream is None:
            if time.time() >= self.prossimo_tentativo:
                self.apri()
            if self.stream is None:
                return
        if self.canali == 1:
            data = data.mean(axis=1, keepdims=True)
        if self.rate != FREQUENZA:
            n = len(data)
            x = np.linspace(0, n - 1, max(1, round(n * self.rate / FREQUENZA)))
            data = np.stack([np.interp(x, np.arange(n), data[:, c]) for c in range(data.shape[1])], axis=1)
        try:
            self.stream.write(np.ascontiguousarray(data.astype(np.int16)))
        except Exception as exc:
            print(f"[WARN] Ascolto in cuffia interrotto ({str(exc)[:90]}): riprovo (la trascrizione continua).")
            self.chiudi()
            self.prossimo_tentativo = time.time() + 2

    def chiudi(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class _Chiamata:
    """Stream del cavo + thread che passa ogni blocco alle cuffie e alla trascrizione."""

    def __init__(self, sd, idx: int, cuffie_hint: str, callback):
        self.coda: "queue.Queue[np.ndarray]" = queue.Queue()
        self.fermo = threading.Event()
        self.ascolto = Ascolto(sd, cuffie_hint)
        self.callback = callback
        # WDM-KS funziona solo a callback: qui si copia il blocco e basta, il resto lo fa il thread
        self.stream = sd.InputStream(device=idx, samplerate=FREQUENZA, channels=2, dtype="int16",
                                     blocksize=1024, callback=lambda d, *_: self.coda.put(d.copy()))
        self.thread = threading.Thread(target=self._giro, daemon=True)
        self.stream.start()
        self.thread.start()

    def _giro(self):
        while not self.fermo.is_set():
            try:
                data = self.coda.get(timeout=0.5)
            except queue.Empty:
                continue
            self.ascolto.scrivi(data)
            self.callback(data, FREQUENZA)

    def close(self):
        self.fermo.set()
        try:
            self.stream.stop()
            self.stream.close()
        finally:
            self.thread.join(timeout=2)
            self.ascolto.chiudi()


def apri_chiamata(cuffie_hint: str, callback):
    """Come apri_microfono / apri_audio_pc: restituisce (stream, nome). Errori come RuntimeError leggibili."""
    sd = _sd()
    idx = _trova_cavo(sd)
    return _Chiamata(sd, idx, cuffie_hint, callback), sd.query_devices(idx)["name"]
