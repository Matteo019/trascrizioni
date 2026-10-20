"""
Dashboard locale: un solo processo che serve le API e i file di web/.
trascrittore.py e riassunto.py girano come processi figli, uno per progetto; il loro output
va in progetti/<slug>/<tipo>.log, che la dashboard legge a ogni aggiornamento.

Uso:
  pythonw src/webapp.py      (lo fa avvia_dashboard.bat) -> http://127.0.0.1:8765
"""

import os
import sys
import json
import time
import socket
import datetime
import threading
import subprocess
import webbrowser
from pathlib import Path

from comune import ROOT, ENV_PATH, scrivi_dotenv, dispositivi_ascolto

HOST, PORT = "127.0.0.1", int(os.environ.get("DASHBOARD_PORT", "8765"))
URL = f"http://{HOST}:{PORT}"

# con pythonw non c'è console: l'output della dashboard finisce in dashboard.log
if sys.stdout is None or sys.stderr is None:
    _log = open(ROOT / "dashboard.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import progetti
import sessioni
import chat
from export_pdf import markdown_in_pdf

SCRIPT = {"trascrizione": ROOT / "src" / "trascrittore.py", "riassunto": ROOT / "src" / "riassunto.py"}
CHIAVI = ("GROQ_API_KEY", "NVIDIA_API_KEY")
IMPOSTAZIONI = ("MONITOR_DEVICE", "RIASSUNTO_INTERVALLO_MINUTI")
TIPI_CONFIG = ("contesto", "glossario")
CODA_LOG_BYTE = 64 * 1024

app = FastAPI(title="Trascrizioni")


# ---------------------------------------------------------------------------
# Processi figli
# ---------------------------------------------------------------------------

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


class Processo:
    def __init__(self, slug: str, tipo: str):
        self.slug, self.tipo = slug, tipo
        base = progetti.cartella(slug)
        self.log_path = base / f"{tipo}.log"
        self.stop_file = base / f".stop_{tipo}"
        # il PID resta su disco: una dashboard riavviata ritrova i processi ancora in corso
        self.pid_file = base / f".pid_{tipo}"
        self.popen = None
        self.pid = None
        self.avviato = None
        self.stop_chiesto = self.ucciso = False
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
            except ValueError:
                pid = 0
            if pid and pid_vivo(pid):
                self.pid = pid
                self.avviato = datetime.datetime.fromtimestamp(self.pid_file.stat().st_mtime)
                self.stop_chiesto = self.stop_file.exists()
            else:
                self.pid_file.unlink(missing_ok=True)

    def avvia(self, env_extra: dict):
        self.stop_file.unlink(missing_ok=True)
        python = Path(sys.executable).with_name("python.exe")
        env = {**os.environ, **env_extra, "DASHBOARD": "1", "STOP_FILE": str(self.stop_file),
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        log = open(self.log_path, "w", encoding="utf-8")
        log.write(f"[{datetime.datetime.now():%H:%M:%S}] Avvio {self.tipo}\n")
        log.flush()
        self.popen = subprocess.Popen(
            [str(python if python.exists() else sys.executable), "-u", str(SCRIPT[self.tipo]),
             "--progetto", self.slug],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        log.close()
        self.pid = self.popen.pid
        self.pid_file.write_text(str(self.pid))
        self.avviato = datetime.datetime.now()
        self.stop_chiesto = self.ucciso = False

    @property
    def attivo(self) -> bool:
        if self.popen is not None:
            vivo = self.popen.poll() is None
        else:
            vivo = self.pid is not None and pid_vivo(self.pid)
        if not vivo:
            self.pid_file.unlink(missing_ok=True)
        return vivo

    def ferma(self, subito: bool):
        if not self.attivo:
            return
        if subito:
            # il python del venv avvia a sua volta l'interprete vero: si chiude tutto l'albero
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            elif self.popen is not None:
                self.popen.kill()
            self.ucciso = True
        else:
            # chiusura ordinata: lo script finisce il pezzo in corso (il riassunto elabora l'ultimo testo)
            self.stop_file.touch()
            self.stop_chiesto = True

    def stato(self) -> dict:
        attivo = self.attivo
        if not attivo:
            self.stop_file.unlink(missing_ok=True)
        return {
            "progetto": self.slug, "tipo": self.tipo, "attivo": attivo,
            "avviato": self.avviato.isoformat(timespec="seconds") if self.avviato else None,
            "in_chiusura": attivo and self.stop_chiesto,
            # chiuso a mano con "Chiudi subito": non è un errore dello script
            "codice_uscita": None if attivo or self.popen is None or self.ucciso else self.popen.poll(),
        }


PROCESSI: dict = {}
_lock = threading.Lock()


def processo(slug: str, tipo: str) -> Processo:
    if tipo not in SCRIPT:
        raise HTTPException(404, "tipo di processo sconosciuto")
    with _lock:
        return PROCESSI.setdefault((slug, tipo), Processo(slug, tipo))


def ritrova_processi():
    """All'avvio: i processi lanciati da una dashboard precedente e ancora vivi tornano sotto controllo."""
    for slug in progetti.elenco():
        for tipo in SCRIPT:
            if (progetti.cartella(slug) / f".pid_{tipo}").exists():
                p = processo(slug, tipo)
                if not p.attivo:
                    PROCESSI.pop((slug, tipo), None)


def stato_processi(slug: str = "") -> list:
    return [{**p.stato(), "nome_progetto": progetti.leggi_info(s)["nome"]}
            for (s, _), p in PROCESSI.items() if not slug or s == slug]


def leggi_log(path: Path, da: int) -> dict:
    """Testo del log dall'offset `da`; con da < 0 solo la coda. Restituisce le righe e il nuovo offset."""
    if not path.exists():
        return {"righe": [], "offset": 0, "inizio": True}
    dimensione = path.stat().st_size
    if da < 0 or da > dimensione:
        da = max(0, dimensione - CODA_LOG_BYTE)
    with open(path, "rb") as f:
        f.seek(da)
        dati = f.read()
    completo = dati[: dati.rfind(b"\n") + 1]    # l'ultima riga può essere a metà
    righe = completo.decode("utf-8", errors="replace").splitlines()
    if da > 0 and da == max(0, dimensione - CODA_LOG_BYTE) and righe:
        righe = righe[1:]                      # prima riga della coda probabilmente tagliata
    return {"righe": righe, "offset": da + len(completo), "inizio": da == 0}


# ---------------------------------------------------------------------------
# Progetti
# ---------------------------------------------------------------------------

def verifica_slug(slug: str):
    try:
        base = progetti.cartella(slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not base.exists():
        raise HTTPException(404, "progetto non trovato")
    return base


def riepilogo(slug: str) -> dict:
    info = progetti.leggi_info(slug)
    elenco = sessioni.elenco_sessioni(slug)
    modifiche = [s["ultima_modifica"] for s in elenco if s["ultima_modifica"]]
    return {
        "slug": slug, **info,
        "sessioni": len(elenco),
        "ultima_attivita": max(modifiche) if modifiche else None,
        "incerti": sum(s["incerti_riassunto"] for s in elenco),
        "qualita": sessioni.somma_qualita(s["qualita"] for s in elenco),
        "processi": stato_processi(slug),
    }


@app.get("/api/progetti")
def api_progetti():
    return [riepilogo(slug) for slug in progetti.elenco()]


@app.post("/api/progetti")
def api_crea(dati: dict = Body(...)):
    nome = str(dati.get("nome", "")).strip()
    contesto = str(dati.get("contesto", "")).strip()
    if not nome:
        raise HTTPException(400, "Scrivi il nome del progetto.")
    if not contesto:
        raise HTTPException(400, "Scrivi il contesto: di cosa parlano le chiamate di questo progetto.")
    return {"slug": progetti.crea(nome, contesto, str(dati.get("glossario", "")))}


@app.patch("/api/progetti/{slug}")
def api_aggiorna(slug: str, dati: dict = Body(...)):
    verifica_slug(slug)
    nome = dati.get("nome")
    stato = dati.get("stato")
    if nome is not None and not str(nome).strip():
        raise HTTPException(400, "Il nome non può essere vuoto.")
    if stato not in (None, "attivo", "archiviato"):
        raise HTTPException(400, "stato non valido")
    return progetti.aggiorna(slug, nome=str(nome).strip() if nome else None, stato=stato)


@app.get("/api/progetti/{slug}")
def api_progetto(slug: str):
    verifica_slug(slug)
    return {**riepilogo(slug), "elenco_sessioni": sessioni.elenco_sessioni(slug)}


@app.post("/api/progetti/{slug}/apri-cartella")
def api_apri_cartella(slug: str):
    base = verifica_slug(slug)
    # explorer.exe porta la finestra in primo piano (os.startfile da pythonw la apre dietro al browser)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(base.resolve())])
    return {"ok": True}


@app.get("/api/progetti/{slug}/config/{tipo}")
def api_leggi_config(slug: str, tipo: str):
    base = verifica_slug(slug)
    if tipo not in TIPI_CONFIG:
        raise HTTPException(404)
    path = base / f"{tipo}.txt"
    return {"testo": path.read_text(encoding="utf-8") if path.exists() else ""}


@app.put("/api/progetti/{slug}/config/{tipo}")
def api_scrivi_config(slug: str, tipo: str, dati: dict = Body(...)):
    base = verifica_slug(slug)
    if tipo not in TIPI_CONFIG:
        raise HTTPException(404)
    (base / f"{tipo}.txt").write_text(str(dati.get("testo", "")), encoding="utf-8")
    return {"salvato": datetime.datetime.now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# Sessioni
# ---------------------------------------------------------------------------

def verifica_sessione(slug: str, nome: str):
    base = verifica_slug(slug)
    if nome not in sessioni.nomi_sessioni(base):
        raise HTTPException(404, "sessione non trovata")


@app.get("/api/progetti/{slug}/sessioni/{nome}")
def api_sessione(slug: str, nome: str):
    verifica_sessione(slug, nome)
    return sessioni.info_sessione(slug, nome)


@app.get("/api/progetti/{slug}/sessioni/{nome}/documento/{tipo}")
def api_documento(slug: str, nome: str, tipo: str):
    verifica_sessione(slug, nome)
    if tipo not in ("riassunto", "appunti"):
        raise HTTPException(404)
    return sessioni.documento(slug, nome, tipo)


@app.get("/api/progetti/{slug}/trascrizione/{nome}")
def api_minuta(slug: str, nome: str, pausa: float = 3, sezione: float = 15):
    verifica_sessione(slug, nome)
    return sessioni.minuta(slug, nome, pausa_minuti=max(0.5, pausa), sezione_minuti=max(1, sezione))


@app.get("/api/progetti/{slug}/sessioni/{nome}/pdf")
def api_pdf(slug: str, nome: str):
    verifica_sessione(slug, nome)
    titolo, testo = sessioni.testo_per_pdf(slug, nome)
    if not testo:
        raise HTTPException(404, "Questa sessione non ha ancora né riassunto né appunti.")
    pdf = markdown_in_pdf(testo, titolo)
    nome_file = titolo.encode("ascii", "ignore").decode().replace('"', "") or nome
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nome_file}.pdf"'})


@app.get("/api/calendario")
def api_calendario():
    eventi = []
    for slug in progetti.elenco():
        info = progetti.leggi_info(slug)
        for s in sessioni.elenco_sessioni(slug):
            eventi.append({**s, "nome_progetto": info["nome"], "stato_progetto": info["stato"]})
    return eventi


@app.get("/api/cerca")
def api_cerca(q: str = "", progetto: str = "", tipi: str = "trascrizione,appunti,riassunto"):
    if progetto:
        verifica_slug(progetto)
    return sessioni.cerca(q, progetto, tuple(t for t in tipi.split(",") if t))


@app.get("/api/chat/modelli")
def api_chat_modelli():
    return {"modelli": chat.MODELLI, "predefinito": chat.MODELLO_PREDEFINITO}


@app.get("/api/chat/stato")
def api_chat_stato():
    return chat.stato_modelli()


@app.post("/api/chat")
def api_chat(dati: dict = Body(...)):
    domanda = str(dati.get("domanda", "")).strip()
    if not domanda:
        raise HTTPException(400, "Scrivi una domanda.")
    progetto, sessione = str(dati.get("progetto") or ""), str(dati.get("sessione") or "")
    if progetto:
        verifica_slug(progetto)
    if sessione:
        verifica_sessione(progetto, sessione)
    storico = [m for m in dati.get("storico", []) if isinstance(m, dict)]
    try:
        r = chat.rispondi(domanda, storico, progetto, sessione,
                          str(dati.get("modello") or chat.MODELLO_PREDEFINITO))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return {**r, "html": sessioni.md_in_html(r["risposta"])}


# ---------------------------------------------------------------------------
# Processi
# ---------------------------------------------------------------------------

@app.get("/api/processi")
def api_processi():
    return stato_processi()


@app.post("/api/progetti/{slug}/processi/{tipo}/avvia")
def api_avvia(slug: str, tipo: str):
    verifica_slug(slug)
    p = processo(slug, tipo)
    if p.attivo:
        raise HTTPException(409, "È già in esecuzione.")
    if tipo == "trascrizione":
        altri = [q for q in PROCESSI.values() if q.tipo == "trascrizione" and q.attivo]
        if altri:
            nome = progetti.leggi_info(altri[0].slug)["nome"]
            raise HTTPException(409, f"C'è già una trascrizione in corso su «{nome}»: fermala prima "
                                     "di avviarne un'altra (l'audio della chiamata è uno solo).")
        if not os.environ.get("GROQ_API_KEY"):
            raise HTTPException(400, "Manca la chiave Groq: inseriscila in Impostazioni.")
    elif not os.environ.get("NVIDIA_API_KEY"):
        raise HTTPException(400, "Manca la chiave NVIDIA: inseriscila in Impostazioni.")
    p.avvia({})
    return p.stato()


@app.post("/api/progetti/{slug}/processi/{tipo}/ferma")
def api_ferma(slug: str, tipo: str, subito: bool = False):
    verifica_slug(slug)
    p = processo(slug, tipo)
    p.ferma(subito)
    return p.stato()


@app.get("/api/progetti/{slug}/processi/{tipo}/log")
def api_log(slug: str, tipo: str, da: int = -1):
    verifica_slug(slug)
    p = processo(slug, tipo)
    return {**leggi_log(p.log_path, da), **p.stato()}


# ---------------------------------------------------------------------------
# Impostazioni
# ---------------------------------------------------------------------------

@app.get("/api/impostazioni")
def api_impostazioni():
    try:
        import sounddevice as sd
        sd._terminate()          # rilegge i dispositivi (es. cuffie collegate dopo l'avvio)
        sd._initialize()
        dispositivi = [nome for _, nome in dispositivi_ascolto()]
        errore_audio = None
    except Exception as exc:
        dispositivi, errore_audio = [], str(exc)
    return {
        "chiavi": {k: bool(os.environ.get(k)) for k in CHIAVI},
        "valori": {k: os.environ.get(k, "") for k in IMPOSTAZIONI},
        "dispositivi": sorted(set(dispositivi)),
        "errore_audio": errore_audio,
        "env_esiste": ENV_PATH.exists(),
    }


@app.put("/api/impostazioni")
def api_salva_impostazioni(dati: dict = Body(...)):
    nuovi = {}
    for k in CHIAVI:
        valore = str(dati.get("chiavi", {}).get(k, "")).strip()
        if valore:                  # vuoto = lascia la chiave com'è
            nuovi[k] = valore
    for k in IMPOSTAZIONI:
        if k in dati.get("valori", {}):
            nuovi[k] = str(dati["valori"][k]).strip()
    if "RIASSUNTO_INTERVALLO_MINUTI" in nuovi and nuovi["RIASSUNTO_INTERVALLO_MINUTI"]:
        try:
            if float(nuovi["RIASSUNTO_INTERVALLO_MINUTI"].replace(",", ".")) <= 0:
                raise ValueError
            nuovi["RIASSUNTO_INTERVALLO_MINUTI"] = nuovi["RIASSUNTO_INTERVALLO_MINUTI"].replace(",", ".")
        except ValueError:
            raise HTTPException(400, "L'intervallo dei riassunti deve essere un numero di minuti maggiore di zero.")
    if not ENV_PATH.exists() and (ROOT / ".env.example").exists():
        ENV_PATH.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    scrivi_dotenv(nuovi)
    for k, v in nuovi.items():      # i processi avviati da qui in poi vedono i valori nuovi
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    return api_impostazioni()


@app.post("/api/chiudi")
def api_chiudi():
    """Ferma in modo ordinato i processi (al massimo 20 secondi, poi li chiude) e spegne la dashboard."""
    def spegni():
        for p in PROCESSI.values():
            p.ferma(subito=False)
        limite = time.time() + 20
        while time.time() < limite and any(p.attivo for p in PROCESSI.values()):
            time.sleep(0.5)
        for p in PROCESSI.values():
            p.ferma(subito=True)
            p.stop_file.unlink(missing_ok=True)
        os._exit(0)
    threading.Thread(target=spegni, daemon=True).start()
    return {"ok": True}


@app.middleware("http")
async def senza_cache(request, call_next):
    """Dopo un aggiornamento del codice il browser deve ricaricare app.js e style.css."""
    risposta = await call_next(request)
    risposta.headers["Cache-Control"] = "no-cache"
    return risposta


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")


def gia_attiva() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def main():
    if gia_attiva():
        webbrowser.open(URL)
        return
    progetti.migra_vecchia_struttura()
    ritrova_processi()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, webbrowser.open, args=(URL,)).start()
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
