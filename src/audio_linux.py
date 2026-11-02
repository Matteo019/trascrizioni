"""
Cattura audio su Linux (Fedora: PipeWire con compatibilità PulseAudio) tramite `pactl` e `parec`.

  microfono  -> una sorgente di ingresso (non «.monitor»)
  audio PC   -> il «monitor» di un'uscita (casse, cuffie): registra quello che esce, senza cavi virtuali

Nell'elenco della dashboard si mostrano le descrizioni leggibili («Audio interno Stereo analogico»);
l'utente sceglie l'uscita dove ascolta e il programma legge il monitor corrispondente.
Su Fedora i programmi vengono dal pacchetto `pulseaudio-utils` (sudo dnf install pulseaudio-utils).
"""

import json
import os
import shutil
import subprocess
import threading
import time

import numpy as np

FREQUENZA = 48000
CANALI = 2
BLOCCO_AUDIO = 1024
MONITOR = ".monitor"
INSTALLA = "Manca «{}»: su Fedora installa con  sudo dnf install pulseaudio-utils"


def _esegui(*args) -> str:
    if not shutil.which("pactl"):
        raise RuntimeError(INSTALLA.format("pactl"))
    try:
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10,
                           env={**os.environ, "LC_ALL": "C"})
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Il server audio non risponde (pactl).") from exc
    if r.returncode != 0:
        raise RuntimeError(f"Il server audio non è raggiungibile: {r.stderr.strip()[:200] or 'pactl ha fallito'}")
    return r.stdout


def _lista(tipo: str) -> list:
    """[{name, description}] di 'sources' o 'sinks'. JSON se pactl lo supporta, altrimenti il formato corto."""
    try:
        dati = json.loads(_esegui("--format=json", "list", tipo))
        return [{"name": d["name"], "description": d.get("description") or d["name"]} for d in dati]
    except (RuntimeError, ValueError, KeyError):
        pass
    righe = _esegui("list", "short", tipo).splitlines()
    return [{"name": p[1], "description": p[1]} for p in (r.split("\t") for r in righe) if len(p) > 1]


def _predefinito(cosa: str) -> str:
    try:
        return _esegui(cosa).strip()
    except RuntimeError:
        return ""


def _elenco(sorgente: str) -> tuple:
    """([{name, description}], descrizione del predefinito) per «microfono» o «pc»."""
    sorgenti = _lista("sources")
    if sorgente == "microfono":
        lista = [s for s in sorgenti if not s["name"].endswith(MONITOR)]
        predefinito = _predefinito("get-default-source")
        trovato = next((s for s in lista if s["name"] == predefinito), None)
        return lista, (trovato["description"] if trovato else "")
    # audio del PC: ogni uscita ha un monitor; il nome mostrato è quello dell'uscita
    uscite = {s["name"]: s["description"] for s in _lista("sinks")}
    lista = []
    for s in sorgenti:
        if s["name"].endswith(MONITOR):
            sink = s["name"][:-len(MONITOR)]
            lista.append({"name": s["name"], "description": uscite.get(sink, s["description"])})
    predefinito = _predefinito("get-default-sink")
    return lista, uscite.get(predefinito, "")


def dispositivi(sorgente: str) -> dict:
    lista, predefinito = _elenco(sorgente)
    return {"nomi": sorted({d["description"] for d in lista}), "predefinito": predefinito}


class _Stream:
    def __init__(self, proc, thread):
        self.proc, self.thread = proc, thread

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()
        finally:
            self.thread.join(timeout=2)


def _apri(sorgente: str, hint: str, callback):
    """Come _apri di comune.py su Windows: callback(blocco int16 [campioni, canali], frequenza)."""
    if not shutil.which("parec"):
        raise RuntimeError(INSTALLA.format("parec"))
    cosa = "il microfono" if sorgente == "microfono" else "l'audio del PC da"
    lista, predefinito = _elenco(sorgente)
    cercato = (hint or predefinito).lower()
    info = (next((d for d in lista if d["description"].lower() == cercato or d["name"].lower() == cercato), None)
            or next((d for d in lista if cercato and (cercato in d["description"].lower()
                                                      or cercato in d["name"].lower())), None))
    if info is None and not hint and lista:
        # nessun predefinito dichiarato dal server: si prende il primo disponibile
        info = lista[0]
    if info is None:
        raise RuntimeError(f"Non trovo {cosa} «{hint or predefinito or 'predefinito'}»: è collegato? "
                           "Puoi sceglierne un altro nella dashboard.")
    proc = subprocess.Popen(
        ["parec", f"--device={info['name']}", "--format=s16le", f"--rate={FREQUENZA}",
         f"--channels={CANALI}", "--latency-msec=40"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
    time.sleep(0.4)
    if proc.poll() is not None:
        errore = proc.stderr.read().decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(f"Impossibile aprire {cosa} «{info['description']}» ({errore or 'parec si è chiuso'}). "
                           "Controlla che PipeWire/PulseAudio sia attivo e che nessun'altra app blocchi il dispositivo.")

    dimensione = BLOCCO_AUDIO * CANALI * 2

    def giro():
        resto = b""
        while True:
            pezzo = proc.stdout.read(dimensione)
            if not pezzo:
                return                       # parec terminato (chiusura o dispositivo sparito)
            resto += pezzo
            utili = len(resto) - len(resto) % (CANALI * 2)
            if utili:
                dati = np.frombuffer(resto[:utili], dtype=np.int16).reshape(-1, CANALI).copy()
                resto = resto[utili:]
                callback(dati, FREQUENZA)

    thread = threading.Thread(target=giro, daemon=True)
    thread.start()
    return _Stream(proc, thread), info["description"]
