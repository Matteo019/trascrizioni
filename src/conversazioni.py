"""
Conversazioni della chat salvate su disco, una per file JSON:

  progetti/<slug>/chat/<id>.json    chat su un progetto (o su una sua sessione)
  chat/<id>.json                    chat su tutti i progetti attivi

Ogni conversazione tiene le istruzioni dell'utente (es. "1 parola"), che valgono finché non le cambia.
Quando diventa troppo lunga si chiude e ne continua una nuova, collegata con continua_da / continua_in.
"""

import datetime
import json
import os
import random
import re
import string
from pathlib import Path

from comune import ROOT
import progetti

CHAT_GLOBALI_DIR = ROOT / "chat"
ID_VALIDO = re.compile(r"^\d{8}-\d{6}-[a-z0-9]{4}$")
MAX_MESSAGGI_CONVERSAZIONE = 40          # 20 scambi
MAX_CARATTERI_CONVERSAZIONE = 30000
MAX_CARATTERI_TITOLO = 60


def adesso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def cartella(slug: str = "") -> Path:
    return progetti.cartella(slug) / "chat" if slug else CHAT_GLOBALI_DIR


def percorso(slug: str, id_: str) -> Path:
    if not ID_VALIDO.match(id_ or ""):
        raise ValueError(f"conversazione non valida: {id_!r}")
    return cartella(slug) / f"{id_}.json"


def nuova(slug: str = "", sessione: str = "", istruzioni=(), **altro) -> dict:
    id_ = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    conv = {"id": id_, "titolo": "", "progetto": slug, "sessione": sessione, "creata": adesso(), "aggiornata": adesso(),
            "istruzioni": list(istruzioni), "continua_da": None, "continua_in": None,
            "riassunto_precedente": "", "chiusa": False, "messaggi": [], **altro}
    salva(conv)
    return conv


def leggi(slug: str, id_: str) -> dict:
    f = percorso(slug, id_)
    if not f.exists():
        raise FileNotFoundError(id_)
    return json.loads(f.read_text(encoding="utf-8"))


def salva(conv: dict):
    """Scrittura atomica: un'interruzione a metà non lascia un JSON rotto."""
    f = percorso(conv["progetto"], conv["id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def elenco(slug: str = "", sessione: str = "") -> list:
    """Le conversazioni dell'ambito, dalla più recente: solo i dati per l'elenco, senza i messaggi."""
    voci = []
    for f in cartella(slug).glob("*.json"):
        try:
            conv = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if conv.get("sessione", "") != sessione:
            continue
        voci.append({k: conv.get(k) for k in ("id", "titolo", "creata", "aggiornata", "chiusa", "istruzioni",
                                              "continua_da", "continua_in")} | {"messaggi": len(conv.get("messaggi", []))})
    return sorted(voci, key=lambda v: v["aggiornata"] or "", reverse=True)


def aggiungi_messaggi(conv: dict, *messaggi: dict):
    conv["messaggi"].extend(messaggi)
    conv["aggiornata"] = adesso()
    if not conv["titolo"]:
        prima = next((m["testo"] for m in conv["messaggi"] if m.get("ruolo") == "utente"), "")
        prima = " ".join(prima.split())
        conv["titolo"] = prima[:MAX_CARATTERI_TITOLO] + ("…" if len(prima) > MAX_CARATTERI_TITOLO else "")
    salva(conv)


def da_chiudere(conv: dict) -> bool:
    messaggi = conv["messaggi"]
    return (len(messaggi) >= MAX_MESSAGGI_CONVERSAZIONE
            or sum(len(str(m.get("testo", ""))) for m in messaggi) >= MAX_CARATTERI_CONVERSAZIONE)


def rollover(conv: dict, riassunto: str) -> dict:
    """Chiude la conversazione e ne apre una nuova che ne eredita istruzioni, ambito e riassunto."""
    seguito = nuova(conv["progetto"], conv["sessione"], conv["istruzioni"],
                    continua_da=conv["id"], riassunto_precedente=riassunto,
                    titolo=re.sub(r"\s*\(\d+\)$", "", conv["titolo"]) + f" ({conta_catena(conv) + 1})")
    conv.update(chiusa=True, continua_in=seguito["id"], aggiornata=adesso())
    salva(conv)
    return seguito


def conta_catena(conv: dict) -> int:
    """Quante conversazioni ci sono nella catena fino a questa (compresa)."""
    n, da = 1, conv.get("continua_da")
    while da:
        try:
            da = leggi(conv["progetto"], da).get("continua_da")
        except (OSError, ValueError):
            break
        n += 1
    return n


def esporta(slug: str = "") -> dict:
    """Tutte le conversazioni (complete) del progetto, o di tutto se il progetto è vuoto."""
    cartelle = [cartella(slug)] if slug else [CHAT_GLOBALI_DIR] + [cartella(p) for p in progetti.elenco()]
    conversazioni = []
    for c in cartelle:
        for f in sorted(c.glob("*.json")):
            try:
                conversazioni.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return {"esportato": adesso(), "progetto": slug, "conversazioni": conversazioni}


def nel_cestino(slug: str, id_: str):
    f = percorso(slug, id_)
    if not f.exists():
        raise FileNotFoundError(id_)
    progetti.nel_cestino(f, nome=f"chat {id_}")
