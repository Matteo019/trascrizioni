"""
Lettura delle sessioni di un progetto per la dashboard: elenco, minuta, documenti, ricerca.
Tutto viene ricavato dai file a ogni richiesta (nessun indice): i volumi sono solo testo.
"""

import re
import datetime
from pathlib import Path

import markdown

import progetti
from comune import sessione_in_scrittura

RIGA = re.compile(r"^\[(\d\d):(\d\d):(\d\d)( recuperato)?\]\s?(.*)$")
INCERTO = "**[INCERTO]**"
VOCE_ELENCO = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")
TITOLO = re.compile(r"^#{1,6}\s+(.*)$")
SEPARATORE_TABELLA = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def qualita(testo_md: str) -> dict:
    """
    Conta le informazioni del documento e le divide in chiare, con [INCERTO] e rumore
    (sezione "Rumore e passaggi non pertinenti"). Un'informazione è una voce di elenco,
    una riga di tabella (esclusa l'intestazione) o un paragrafo segnato [INCERTO].
    """
    conteggi = {"chiaro": 0, "incerto": 0, "rumore": 0}
    nel_rumore = nell_indice = in_tabella = False
    for riga in testo_md.splitlines():
        titolo = TITOLO.match(riga)
        if titolo:
            nel_rumore = "rumore" in titolo.group(1).lower()
            nell_indice = titolo.group(1).strip().lower() == "indice"
            in_tabella = False
            continue
        if riga.lstrip().startswith("|"):
            if SEPARATORE_TABELLA.match(riga):
                continue
            if not in_tabella:          # prima riga: intestazione
                in_tabella = True
                continue
            voce = True
        else:
            in_tabella = False
            voce = bool(VOCE_ELENCO.match(riga)) or "[INCERTO]" in riga
        if voce and not nell_indice:
            if nel_rumore:
                conteggi["rumore"] += 1
            elif "[INCERTO]" in riga:
                conteggi["incerto"] += 1
            else:
                conteggi["chiaro"] += 1
    return {**conteggi, "totale": sum(conteggi.values())}


def somma_qualita(elenco) -> dict:
    totale = {"chiaro": 0, "incerto": 0, "rumore": 0, "totale": 0}
    for q in elenco:
        for k in totale:
            totale[k] += q[k]
    return totale


def etichetta(nome: str) -> str:
    """'20261020_094750' -> '2026-10-20 09.47' (come in riassunto.py)"""
    return f"{inizio_sessione(nome):%Y-%m-%d %H.%M}"


def inizio_sessione(nome: str) -> datetime.datetime:
    return datetime.datetime.strptime(nome, "%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Elenco sessioni
# ---------------------------------------------------------------------------

def nomi_sessioni(base: Path) -> list:
    nomi = set()
    for cartella in (base / "trascrizioni", base / "storico"):
        if cartella.exists():
            for f in cartella.glob("trascrizione_*.txt"):
                nome = f.stem.removeprefix("trascrizione_")
                try:
                    inizio_sessione(nome)
                    nomi.add(nome)
                except ValueError:
                    pass
    return sorted(nomi, reverse=True)


def file_sessione(base: Path, nome: str) -> dict:
    """
    in_corso: la trascrizione sta scrivendo proprio questa sessione (al massimo una per progetto).
    da_riassumere: la registrazione è finita ma il file è ancora in trascrizioni/, finché il riassunto
    non l'ha elaborata tutta e spostata in storico/.
    """
    et = etichetta(nome)
    in_corso = base / "trascrizioni" / f"trascrizione_{nome}.txt"
    in_scrittura = in_corso.exists() and sessione_in_scrittura(base) == nome
    riassunti = sorted((base / "riassunti").glob(f"Riassunto {et} - *.md")) if (base / "riassunti").exists() else []
    cartella_parti = base / "riassunti" / f"Parti {et}"
    return {
        "trascrizione": in_corso if in_corso.exists() else base / "storico" / f"trascrizione_{nome}.txt",
        "in_corso": in_scrittura,
        "da_riassumere": in_corso.exists() and not in_scrittura,
        "appunti": base / "storico" / f"Appunti {et}.md",
        "riassunto": riassunti[0] if riassunti else None,
        "parti": sorted(cartella_parti.glob("Parte *.md")) if cartella_parti.exists() else [],
    }


def elimina_sessione(slug: str, nome: str) -> Path:
    """Trascrizione, appunti, riassunto, parti, audio registrato e non trascritto della sessione vanno nel cestino."""
    base = progetti.cartella(slug)
    f = file_sessione(base, nome)
    percorsi = [base / "trascrizioni" / f"trascrizione_{nome}.txt", base / "storico" / f"trascrizione_{nome}.txt",
                f["appunti"], f["riassunto"], base / "riassunti" / f"Parti {etichetta(nome)}"]
    if (base / "da_ritrascrivere").exists():
        percorsi += sorted((base / "da_ritrascrivere").glob(f"{nome}__*.wav"))
    if (base / "audio").exists():
        percorsi += sorted((base / "audio").glob(f"{nome}_*.ogg"))
    return progetti.nel_cestino(*[p for p in percorsi if p and p.exists()], nome=f"{slug} {etichetta(nome)}")


def leggi(path) -> str:
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""


def info_sessione(slug: str, nome: str) -> dict:
    base = progetti.cartella(slug)
    f = file_sessione(base, nome)
    righe = [m for m in (RIGA.match(r) for r in leggi(f["trascrizione"]).splitlines()) if m]
    riassunto, appunti = leggi(f["riassunto"]), leggi(f["appunti"])
    modifiche = [p.stat().st_mtime for p in (f["trascrizione"], f["appunti"], f["riassunto"]) if p and p.exists()]
    titolo = f["riassunto"].stem.split(" - ", 1)[1] if f["riassunto"] else ""
    return {
        "nome": nome,
        "progetto": slug,
        "inizio": inizio_sessione(nome).isoformat(),
        "titolo": titolo,
        "ora_inizio": f"{righe[0].group(1)}:{righe[0].group(2)}" if righe else None,
        "ora_fine": f"{righe[-1].group(1)}:{righe[-1].group(2)}" if righe else None,
        "blocchi": len(righe),
        "non_trascritti": sum(1 for r in righe if r.group(5).startswith("[AUDIO NON TRASCRITTO")),
        "in_corso": f["in_corso"],
        "da_riassumere": f["da_riassumere"],
        "ha_appunti": bool(appunti),
        "ha_riassunto": bool(riassunto),
        "parti": len(f["parti"]),
        "incerti_riassunto": riassunto.count(INCERTO),
        "incerti_appunti": appunti.count(INCERTO),
        # dal riassunto (riorganizzato e senza ripetizioni), altrimenti dagli appunti
        "qualita": qualita(riassunto or appunti),
        "ultima_modifica": datetime.datetime.fromtimestamp(max(modifiche)).isoformat(timespec="seconds") if modifiche else None,
    }


def elenco_sessioni(slug: str) -> list:
    return [info_sessione(slug, nome) for nome in nomi_sessioni(progetti.cartella(slug))]


# ---------------------------------------------------------------------------
# Documenti leggibili
# ---------------------------------------------------------------------------

TAG_AMMESSI = re.compile(r"&lt;(/?)(br|b|i|u|em|strong|sub|sup)\s*/?\s*(?:>|&gt;)", re.I)


def md_in_html(testo: str) -> str:
    # il testo viene da trascrizioni e modelli AI: niente HTML grezzo nella pagina
    testo = testo.replace("&", "&amp;").replace("<", "&lt;")
    # alcuni modelli ripetono i # del titolo ("### ### Argomento"): resterebbero visibili come testo
    testo = re.sub(r"^(#{1,6})(?:[ \t]+#{1,6})+[ \t]+", r"\1 ", testo, flags=re.M)
    testo = re.sub(r"^(\s*[-*+]\s+)#{1,6}[ \t]+", r"\1", testo, flags=re.M)   # e nelle voci dell'indice
    # i modelli rientrano i sotto-elenchi di 2 spazi, Markdown ne vuole 4
    if re.search(r"^(?:    )*  (?:[-*+]|\d+\.)\s", testo, flags=re.M):
        testo = re.sub(r"^( +)(?=(?:[-*+]|\d+\.)\s)", lambda m: m.group(1) * 2, testo, flags=re.M)
    corpo = markdown.markdown(testo, extensions=["tables", "sane_lists", "fenced_code"])
    # i modelli a volte usano qualche tag di formattazione (es. <br> nelle tabelle): si accettano
    # solo questi, e solo senza attributi; qualsiasi altro HTML resta testo
    corpo = TAG_AMMESSI.sub(lambda m: "<br>" if m.group(2) == "br" else f"<{m.group(1)}{m.group(2)}>", corpo)
    return corpo.replace("<strong>[INCERTO]</strong>", '<mark class="incerto">INCERTO</mark>')


def documento(slug: str, nome: str, tipo: str) -> dict:
    f = file_sessione(progetti.cartella(slug), nome)
    if tipo == "riassunto":
        testo = leggi(f["riassunto"])
    elif tipo == "appunti":
        testo = leggi(f["appunti"])
    else:
        raise ValueError(tipo)
    return {"html": md_in_html(testo) if testo else "", "incerti": testo.count(INCERTO)}


# ---------------------------------------------------------------------------
# Trascrizione come minuta
# ---------------------------------------------------------------------------

def minuta(slug: str, nome: str, pausa_minuti: float = 3, sezione_minuti: float = 15,
           righe_per_paragrafo: int = 4) -> dict:
    """
    Raggruppa le righe [HH:MM:SS] in sezioni (nuova sezione dopo una pausa lunga o ogni
    sezione_minuti) e unisce il testo in paragrafi. Il file .txt non viene toccato.
    """
    f = file_sessione(progetti.cartella(slug), nome)
    inizio = inizio_sessione(nome)
    secondi_inizio = inizio.hour * 3600 + inizio.minute * 60 + inizio.second

    righe = []
    for ordine, testo in enumerate(leggi(f["trascrizione"]).splitlines()):
        m = RIGA.match(testo.strip())
        if not m:
            continue
        hh, mm, ss, recuperato, contenuto = m.groups()
        t = int(hh) * 3600 + int(mm) * 60 + int(ss)
        if t < secondi_inizio - 60:     # sessione a cavallo della mezzanotte
            t += 86400
        tipo = "recuperato" if recuperato else (
            "mancante" if contenuto.startswith("[AUDIO NON TRASCRITTO") else "testo")
        righe.append((t, ordine, tipo, contenuto, f"{hh}:{mm}"))
    # i blocchi recuperati vengono scritti in coda: si rimettono al loro orario
    righe.sort(key=lambda r: (r[0], r[1]))

    sezioni = []
    precedente = None
    for t, _, tipo, contenuto, hhmm in righe:
        sezione = sezioni[-1] if sezioni else None
        if (sezione is None or t - precedente > pausa_minuti * 60
                or t - sezione["_t"] >= sezione_minuti * 60):
            sezione = {"_t": t, "inizio": hhmm, "fine": hhmm, "paragrafi": []}
            sezioni.append(sezione)
        sezione["fine"] = hhmm
        paragrafi = sezione["paragrafi"]
        ultimo = paragrafi[-1] if paragrafi else None
        if (tipo == "testo" and ultimo and ultimo["tipo"] == "testo"
                and ultimo["_righe"] < righe_per_paragrafo):
            ultimo["testo"] += " " + contenuto
            ultimo["_righe"] += 1
        else:
            paragrafi.append({"ora": hhmm, "tipo": tipo, "testo": contenuto, "_righe": 1})
        precedente = t

    for sezione in sezioni:
        del sezione["_t"]
        for p in sezione["paragrafi"]:
            del p["_righe"]
    return {"sezioni": sezioni, "blocchi": len(righe), "in_corso": f["in_corso"]}


# ---------------------------------------------------------------------------
# Ricerca
# ---------------------------------------------------------------------------

def file_ricercabili(slug: str):
    """(tipo, sessione, path) di trascrizioni, appunti e riassunti (le Parti sono già nel riassunto)."""
    base = progetti.cartella(slug)
    for nome in nomi_sessioni(base):
        f = file_sessione(base, nome)
        yield "trascrizione", nome, f["trascrizione"]
        if f["appunti"].exists():
            yield "appunti", nome, f["appunti"]
        if f["riassunto"]:
            yield "riassunto", nome, f["riassunto"]


def termini(query: str) -> list:
    """Parole separate da spazi; "frase esatta" tra virgolette."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', query) if (a or b).strip()]


def cerca(query: str, slug: str = "", tipi=("trascrizione", "appunti", "riassunto"),
          contesto: int = 1, limite: int = 400) -> dict:
    parole = [t.casefold() for t in termini(query)]
    if not parole:
        return {"risultati": [], "totale": 0, "termini": [], "troncato": False}
    risultati, totale = [], 0
    for progetto in ([slug] if slug else progetti.elenco()):
        nome_progetto = progetti.leggi_info(progetto)["nome"]
        for tipo, sessione, path in file_ricercabili(progetto):
            if tipo not in tipi or not path.exists():
                continue
            righe = leggi(path).splitlines()
            trovate = []
            for i, riga in enumerate(righe):
                if all(p in riga.casefold() for p in parole):
                    trovate.append({
                        "riga": i + 1,
                        "prima": righe[max(0, i - contesto):i],
                        "testo": riga,
                        "dopo": righe[i + 1:i + 1 + contesto],
                    })
            if trovate:
                totale += len(trovate)
                risultati.append({
                    "progetto": progetto, "nome_progetto": nome_progetto, "sessione": sessione,
                    "tipo": tipo, "file": path.name, "occorrenze": trovate[:50],
                    "altre": max(0, len(trovate) - 50),
                })
            if totale >= limite:
                return {"risultati": risultati, "totale": totale, "termini": termini(query), "troncato": True}
    return {"risultati": risultati, "totale": totale, "termini": termini(query), "troncato": False}


def testo_per_pdf(slug: str, nome: str) -> tuple:
    """(titolo, markdown) del riassunto, o degli appunti se il riassunto non c'è ancora."""
    f = file_sessione(progetti.cartella(slug), nome)
    if f["riassunto"]:
        return f["riassunto"].stem, leggi(f["riassunto"])
    if f["appunti"].exists():
        return f["appunti"].stem, leggi(f["appunti"])
    return "", ""
