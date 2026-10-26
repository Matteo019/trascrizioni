import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"          # modelli di contesto/glossario copiati nei nuovi progetti
PROGETTI_DIR = ROOT / "progetti"
ENV_PATH = ROOT / ".env"
# progetto usato quando gli script partono senza --progetto (riga di comando, .bat)
PROGETTO_PREDEFINITO = "generale"

# con pythonw stdout/stderr non esistono
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path: Path = ENV_PATH):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def scrivi_dotenv(valori: dict, path: Path = ENV_PATH):
    """Aggiorna le chiavi indicate mantenendo commenti e ordine del file; quelle nuove vanno in coda."""
    righe = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    restanti = dict(valori)
    for i, line in enumerate(righe):
        key = line.partition("=")[0].strip()
        if "=" in line and not line.strip().startswith("#") and key in restanti:
            righe[i] = f"{key}={restanti.pop(key)}"
    righe += [f"{k}={v}" for k, v in restanti.items()]
    # file temporaneo + sostituzione: chi rilegge .env (il trascrittore, ogni 3 s) non lo trova mai vuoto
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(righe) + "\n", encoding="utf-8")
    for tentativo in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:     # Windows: .env aperto in lettura in quell'istante, si riprova
            if tentativo == 19:
                raise
            time.sleep(0.05)


def valore_dotenv(chiave: str, path: Path = ENV_PATH) -> str:
    """Valore attuale nel file .env (non quello letto all'avvio): cambia se lo modifichi dalle impostazioni."""
    testo = ""
    for tentativo in range(20):
        try:
            testo = path.read_text(encoding="utf-8") if path.exists() else ""
            break
        except PermissionError:     # Windows: scrivi_dotenv lo sta sostituendo proprio ora
            if tentativo == 19:
                raise
            time.sleep(0.05)
    if testo:
        for line in testo.splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.strip().startswith("#") and key.strip() == chiave:
                return value.strip()
    return ""


def argomento_progetto() -> str:
    """Valore di --progetto <slug> dalla riga di comando, altrimenti il progetto predefinito."""
    if "--progetto" in sys.argv:
        i = sys.argv.index("--progetto")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return PROGETTO_PREDEFINITO


def path_progetto(slug: str) -> Path:
    """Cartella del progetto, creata (con le sotto-cartelle) se non esiste."""
    from progetti import assicura_progetto
    return assicura_progetto(slug)


def pid_vivo(pid: int) -> bool:
    """Il processo esiste ancora? (su Windows senza ucciderlo: os.kill(pid, 0) lo termina)"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    codice = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(codice))
    kernel32.CloseHandle(handle)
    return codice.value == 259                                # STILL_ACTIVE


# sessione che la trascrizione sta scrivendo: la scrive trascrittore.py all'avvio e la toglie all'uscita
FILE_SESSIONE_IN_SCRITTURA = ".sessione_trascrizione"


def sessione_in_scrittura(base: Path) -> str:
    """
    Nome della sessione che si sta registrando in questo progetto, "" se nessuna.
    Il segnalibro da solo non basta (resta se il processo viene chiuso di colpo): con la dashboard
    vale solo se il processo della trascrizione (.pid_trascrizione) è ancora vivo.
    """
    segnalibro, pid_file = base / FILE_SESSIONE_IN_SCRITTURA, base / ".pid_trascrizione"
    try:
        nome = segnalibro.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if pid_file.exists():
        try:
            return nome if pid_vivo(int(pid_file.read_text().strip())) else ""
        except (OSError, ValueError):
            return ""
    return nome          # avviato da terminale, senza dashboard: ci si fida del segnalibro


def stop_richiesto() -> bool:
    """La dashboard chiede la chiusura ordinata creando il file indicato in STOP_FILE."""
    stop_file = os.environ.get("STOP_FILE")
    return bool(stop_file) and Path(stop_file).exists()


# ---------------------------------------------------------------------------
# Dispositivi audio (WASAPI tramite PyAudioWPatch: microfoni e "loopback" di casse e cuffie)
# ---------------------------------------------------------------------------

LOOPBACK = " [Loopback]"
BLOCCO_AUDIO = 1024


def _elenco(p, pa, sorgente: str) -> tuple:
    """([info dei dispositivi], nome del predefinito di Windows) per «microfono» o «pc»."""
    wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
    if sorgente == "microfono":
        lista = [d for d in (p.get_device_info_by_index(i) for i in range(p.get_device_count()))
                 if d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
        predefinito = wasapi["defaultInputDevice"]
    else:
        # il loopback registra quello che esce da casse o cuffie: si cattura il video senza cavi virtuali
        lista = list(p.get_loopback_device_info_generator())
        predefinito = wasapi["defaultOutputDevice"]
    nome_predefinito = p.get_device_info_by_index(predefinito)["name"] if predefinito >= 0 else ""
    return lista, nome_predefinito


def nome_dispositivo(info: dict) -> str:
    return info["name"].removesuffix(LOOPBACK)


def dispositivi(sorgente: str) -> dict:
    """Nomi dei dispositivi per la combobox della dashboard e quello predefinito di Windows."""
    if sorgente == "chiamata":           # qui si sceglie dove SENTIRE la chiamata
        from chiamata import cuffie
        return cuffie()
    import pyaudiowpatch as pa
    p = pa.PyAudio()                # un'istanza nuova vede anche i dispositivi appena collegati
    try:
        lista, predefinito = _elenco(p, pa, sorgente)
        return {"nomi": sorted({nome_dispositivo(d) for d in lista}), "predefinito": predefinito}
    finally:
        p.terminate()


class _Stream:
    def __init__(self, p, stream):
        self.p, self.stream = p, stream

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        finally:
            self.p.terminate()


def _apri(sorgente: str, hint: str, callback):
    """
    Apre il dispositivo scelto (hint = parte del nome, vuoto = predefinito di Windows) e passa a
    callback(blocco int16 [campioni, canali], frequenza) l'audio che arriva. Restituisce (stream, nome).
    Ogni errore diventa un RuntimeError con un messaggio da mostrare così com'è nel log.
    """
    import numpy as np
    import pyaudiowpatch as pa
    cosa = "il microfono" if sorgente == "microfono" else "l'audio del PC da"
    p = pa.PyAudio()
    try:
        lista, predefinito = _elenco(p, pa, sorgente)
        cercato = (hint or predefinito).lower()
        info = (next((d for d in lista if nome_dispositivo(d).lower() == cercato), None)
                or next((d for d in lista if cercato and cercato in d["name"].lower()), None))
        if info is None:
            raise RuntimeError(f"Non trovo {cosa} «{hint or predefinito or 'predefinito'}»: è collegato? "
                               "Puoi sceglierne un altro nella dashboard.")
        canali, rate = int(info["maxInputChannels"]), int(info["defaultSampleRate"])

        def on_audio(in_data, frame_count, time_info, status):
            callback(np.frombuffer(in_data, dtype=np.int16).reshape(-1, canali), rate)
            return (None, pa.paContinue)

        try:
            stream = p.open(format=pa.paInt16, channels=canali, rate=rate, input=True,
                            input_device_index=info["index"], frames_per_buffer=BLOCCO_AUDIO,
                            stream_callback=on_audio)
        except Exception as exc:
            blocco = ("Windows > Impostazioni > Privacy e sicurezza > Microfono (consenti alle app desktop), "
                      "oppure un antivirus o software aziendale che impedisce la registrazione"
                      if sorgente == "microfono" else "un antivirus o un software aziendale che impedisce la registrazione")
            raise RuntimeError(f"Impossibile aprire {cosa} «{nome_dispositivo(info)}» ({exc}). "
                               f"Se è bloccato controlla {blocco}.") from exc
        return _Stream(p, stream), nome_dispositivo(info)
    except BaseException:
        p.terminate()
        raise


def apri_microfono(hint: str, callback):
    return _apri("microfono", hint, callback)


def apri_audio_pc(hint: str, callback):
    return _apri("pc", hint, callback)


load_dotenv()
