"""
Progetti: ogni corso, lezione o serie di video ha la sua cartella in progetti/<slug>/ con contesto, glossario e output.

  progetto.json        nome, data di creazione, stato, sorgente audio e lingua
  contesto.txt         contesto per l'AI dei riassunti
  glossario.txt        termini tecnici per Whisper
  trascrizioni/        trascrizione_X.txt in scrittura
  riassunti/           Riassunto ... .md e Parti .../
  storico/             parte già riassunta, appunti, trascrizioni finite
  da_ritrascrivere/    audio dei blocchi non trascritti
  audio/               registrazione intera delle sessioni da microfono (Opus)
  kanban.json          card del progetto (facoltativo)
"""

import json
import re
import time
import shutil
import datetime
import unicodedata
from pathlib import Path

from comune import ROOT, CONFIG_DIR, PROGETTI_DIR, PROGETTO_PREDEFINITO

SOTTOCARTELLE = ("trascrizioni", "riassunti", "storico", "da_ritrascrivere")
FILE_CONFIG = ("contesto.txt", "glossario.txt")
# sorgente audio e lingua della trascrizione, scelte per progetto
SORGENTI = ("microfono", "pc")
LINGUE = ("it", "en", "auto")
TIPI_SESSIONE = ("riunione", "lezione", "video")
SLUG_VALIDO = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
MIGRAZIONE_ATTESA_SECONDI = 15 * 60
CESTINO_DIR = ROOT / "cestino"


def slugify(nome: str) -> str:
    testo = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", testo.lower()).strip("-")[:60] or "progetto"


def cartella(slug: str) -> Path:
    if not SLUG_VALIDO.match(slug):
        raise ValueError(f"nome di progetto non valido: {slug!r}")
    return PROGETTI_DIR / slug


def leggi_info(slug: str) -> dict:
    file_info = cartella(slug) / "progetto.json"
    info = json.loads(file_info.read_text(encoding="utf-8")) if file_info.exists() else {}
    info.setdefault("nome", "Generale" if slug == PROGETTO_PREDEFINITO else slug)
    info.setdefault("creato", datetime.datetime.now().isoformat(timespec="seconds"))
    info.setdefault("stato", "attivo")
    info.setdefault("sorgente", "pc")
    info.setdefault("dispositivo", "")       # vuoto = predefinito di Windows
    info.setdefault("lingua", "it")
    info.setdefault("salva_audio", False)
    return info


def scrivi_info(slug: str, info: dict):
    (cartella(slug) / "progetto.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assicura_progetto(slug: str, nome: str = "") -> Path:
    """Crea la cartella del progetto se manca, con contesto e glossario copiati da config/."""
    migra_vecchia_struttura()
    base = cartella(slug)
    for sotto in SOTTOCARTELLE:
        (base / sotto).mkdir(parents=True, exist_ok=True)
    for nome_file in FILE_CONFIG:
        if not (base / nome_file).exists() and (CONFIG_DIR / nome_file).exists():
            shutil.copyfile(CONFIG_DIR / nome_file, base / nome_file)
    if not (base / "progetto.json").exists():
        info = leggi_info(slug)
        if nome:
            info["nome"] = nome
        scrivi_info(slug, info)
    return base


def modello_contesto(tipo: str) -> str:
    """Contesto di partenza per tipo di sessione (config/contesti/<tipo>.txt), da adattare."""
    if tipo not in TIPI_SESSIONE:
        return ""
    path = CONFIG_DIR / "contesti" / f"{tipo}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def crea(nome: str, contesto: str, glossario: str = "", **audio) -> str:
    """Il contesto lo scrive sempre chi crea il progetto; glossario e impostazioni audio sono facoltativi."""
    base = slugify(nome)
    slug, n = base, 2
    while (PROGETTI_DIR / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    cartella_progetto = assicura_progetto(slug, nome.strip())
    (cartella_progetto / "contesto.txt").write_text(contesto.strip() + "\n", encoding="utf-8")
    (cartella_progetto / "glossario.txt").write_text(glossario.strip() + "\n" if glossario.strip() else "", encoding="utf-8")
    if audio:
        aggiorna(slug, **audio)
    return slug


def aggiorna(slug: str, **campi) -> dict:
    info = leggi_info(slug)
    info.update({k: v for k, v in campi.items() if v is not None})
    scrivi_info(slug, info)
    return info


def elenco() -> list:
    migra_vecchia_struttura()
    if not PROGETTI_DIR.exists():
        return []
    return sorted(p.name for p in PROGETTI_DIR.iterdir()
                  if p.is_dir() and SLUG_VALIDO.match(p.name))


def nel_cestino(*percorsi: Path, nome: str) -> Path:
    """
    Sposta file e cartelle in cestino/<data-ora> <nome>/ (fuori da progetti/, quindi non più visibili).
    Niente viene cancellato davvero: per recuperarli basta rimetterli dov'erano.
    """
    destinazione = CESTINO_DIR / f"{datetime.datetime.now():%Y%m%d_%H%M%S} {nome}"
    destinazione.mkdir(parents=True, exist_ok=True)
    for p in percorsi:
        shutil.move(str(p), str(destinazione / p.name))
    return destinazione


def elimina(slug: str) -> Path:
    return nel_cestino(cartella(slug), nome=f"progetto {slug}")


def migra_vecchia_struttura():
    """
    La vecchia cartella piatta trascrizioni/ viene spostata nel progetto predefinito, file per file:
    un file tenuto aperto da uno script ancora in esecuzione resta dov'è e viene spostato al giro dopo.
    """
    vecchia = ROOT / "trascrizioni"
    if not vecchia.exists():
        return
    # una sessione ancora in corso con i vecchi script (file toccati di recente) non va spezzata
    if any(time.time() - f.stat().st_mtime < MIGRAZIONE_ATTESA_SECONDI
           for f in vecchia.rglob("*") if f.is_file()):
        return
    dest = PROGETTI_DIR / PROGETTO_PREDEFINITO
    destinazioni = {"riassunti": dest / "riassunti", "storico": dest / "storico",
                    "da_ritrascrivere": dest / "da_ritrascrivere"}
    for elemento in list(vecchia.iterdir()):
        if elemento.is_dir() and elemento.name in destinazioni:
            for figlio in list(elemento.iterdir()):
                _sposta(figlio, destinazioni[elemento.name] / figlio.name)
            _rimuovi_se_vuota(elemento)
        else:
            _sposta(elemento, dest / "trascrizioni" / elemento.name)
    _rimuovi_se_vuota(vecchia)
    if dest.exists():
        for nome_file in FILE_CONFIG:
            if not (dest / nome_file).exists() and (CONFIG_DIR / nome_file).exists():
                shutil.copyfile(CONFIG_DIR / nome_file, dest / nome_file)
        if not (dest / "progetto.json").exists():
            scrivi_info(PROGETTO_PREDEFINITO, leggi_info(PROGETTO_PREDEFINITO))


def _sposta(sorgente: Path, destinazione: Path):
    if destinazione.exists():
        return
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(sorgente), str(destinazione))
    except OSError:
        pass


def _rimuovi_se_vuota(path: Path):
    try:
        path.rmdir()
    except OSError:
        pass
