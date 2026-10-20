import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"          # modelli di contesto/glossario copiati nei nuovi progetti
PROGETTI_DIR = ROOT / "progetti"
ENV_PATH = ROOT / ".env"
# progetto usato quando gli script partono senza --progetto (vecchi .bat, riga di comando)
PROGETTO_PREDEFINITO = "corso-panthera"

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
    path.write_text("\n".join(righe) + "\n", encoding="utf-8")


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


def stop_richiesto() -> bool:
    """La dashboard chiede la chiusura ordinata creando il file indicato in STOP_FILE."""
    stop_file = os.environ.get("STOP_FILE")
    return bool(stop_file) and Path(stop_file).exists()


def dispositivi_ascolto() -> list:
    """Uscite audio (WASAPI, escluso VB-CABLE) su cui rimandare la chiamata: [(indice, nome)]."""
    import sounddevice as sd
    return [
        (i, d["name"]) for i, d in enumerate(sd.query_devices())
        if d["max_output_channels"] > 0
        and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
        and "cable" not in d["name"].lower()
    ]


load_dotenv()
