"""
Domande sui documenti dei progetti (riassunti, appunti, trascrizioni) con un modello NVIDIA a scelta
o con il fornitore scelto nelle Impostazioni (OpenAI, Gemini, Claude, AI locale...).
Nessun indice: si scelgono i pezzi più pertinenti con un conteggio di parole, a ogni domanda.
"""

import math
from concurrent.futures import ThreadPoolExecutor
import os
import re
import time


import motori
import progetti
import sessioni

# modelli gratuiti dell'endpoint NVIDIA tra cui si sceglie nella chat
MODELLI = [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b", "nome": "Nemotron 3 Ultra", "autore": "NVIDIA",
     "descrizione": "Il più potente: ragionamento profondo e contesto lunghissimo, per domande complesse. Più lento, e condivide i limiti con i riassunti.",
     "punti": ["Massima qualità", "Più lento"]},
]
MODELLO_PREDEFINITO = MODELLI[0]["id"]


def modelli() -> list:
    """Modelli tra cui scegliere: quelli NVIDIA, oppure solo quello del fornitore scelto nelle Impostazioni."""
    if motori.testo_nvidia():
        return MODELLI
    nome = motori.modello_testo()
    if motori.testo_locale():
        descrizione, punti = f"Gira sul tuo PC ({motori.url_testo()}): i documenti non escono dal computer.", ["Locale", "Privato"]
    else:
        descrizione, punti = f"Scelto nelle Impostazioni: risponde {motori.nome_testo()} con la tua chiave.", [motori.nome_testo()]
    return [{"id": nome, "nome": nome, "autore": motori.nome_testo(), "descrizione": descrizione, "punti": punti}]


def predefinito() -> str:
    return modelli()[0]["id"]
# al modello vanno solo le sezioni che servono: poche, brevi, e la risposta arriva in fretta
MAX_CARATTERI_CONTESTO = 12000
MAX_SEZIONI = 8
MAX_CARATTERI_SEZIONE = 3000
# un modello gratuito che non risponde in 40 s di solito è fermo: si passa al successivo
TIMEOUT_SECONDI = 40
TEMPO_MASSIMO_SECONDI = 100   # oltre questo non si provano altri modelli: meglio un errore chiaro che un'attesa infinita
TIMEOUT_PROVA_SECONDI = 25
DURATA_STATO_SECONDI = 180
_stato_modelli = {"quando": 0.0, "stato": {}}

PAROLE_VUOTE = set("""il lo la i gli le un uno una di a da in con su per tra fra e o ma se che chi cui non come
dove quando quale quali quanto cosa cos è sono era del dello della dei degli delle al allo alla ai agli alle
dal dallo dalla dai dagli dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle questo questa
quello quella ci si ne mi ti vi lui lei noi voi loro mio tuo suo anche più molto poi già fare fa ha hanno
essere stato detto dice spiegato spiega parlato""".split())

SISTEMA = """Sei l'assistente di chi segue queste sessioni (lezioni, video, corsi, riunioni...).
Rispondi in italiano, in Markdown, in modo chiaro e pratico. Solo Markdown: mai tag HTML (per andare a capo in una tabella usa ";").
Usa SOLO le informazioni nei documenti qui sotto (riassunti, appunti e trascrizioni delle sessioni).
Se la risposta non c'è, dillo chiaramente e suggerisci cosa cercare; non inventare mai.
Quando usi un'informazione, indica tra parentesi quadre la fonte, es. [2]. I passaggi segnati [INCERTO] vanno riportati come incerti.

DOCUMENTI:
{documenti}"""


def radici(testo: str) -> set:
    """Parole significative ridotte alla radice (articolo/articoli -> artic), per confronti tolleranti."""
    return {p[:5] if len(p) > 5 else p
            for p in re.findall(r"[a-zà-ù0-9]{3,}", testo.casefold()) if p not in PAROLE_VUOTE}


# radici delle domande "di panoramica": per queste servono indice e panoramiche, non una sezione
GENERICHE = radici("argomenti principali trattati tutto riassumi riassunto panoramica sintesi generale "
                   "sessione sessioni chiamata chiamate riunione corso parlato discusso fatto oggi ieri")

TITOLO_MD = re.compile(r"^(#{1,6})\s+(.*)$")


def sezioni_documento(testo: str, tipo: str):
    """
    Spezza riassunto o appunti nelle loro sezioni (### e ####), ognuna col percorso dei titoli
    (es. "Parte 2 (10:35-11:20) › Codifica articolo"). Indice e rumore restano fuori.
    La trascrizione, che non ha titoli, va a pezzi di lunghezza fissa.
    """
    if tipo == "trascrizione":
        righe = testo.splitlines()
        pezzo = []
        for riga in righe:
            pezzo.append(riga)
            if sum(map(len, pezzo)) > MAX_CARATTERI_SEZIONE:
                yield f"Trascrizione {pezzo[0][:10]}", "\n".join(pezzo)
                pezzo = []
        if pezzo:
            yield f"Trascrizione {pezzo[0][:10]}", "\n".join(pezzo)
        return

    percorso = {}          # livello -> titolo
    corpo, titolo_corrente = [], ""

    def chiudi():
        testo_sezione = "\n".join(corpo).strip()
        escluso = any("indice" == t.strip().lower() or "rumore" in t.lower() for t in percorso.values())
        if testo_sezione and not escluso:
            pezzi = [testo_sezione[i:i + MAX_CARATTERI_SEZIONE] for i in range(0, len(testo_sezione), MAX_CARATTERI_SEZIONE)]
            if len(pezzi) > 1 and len(pezzi[-1]) < 400:      # niente spezzoni di poche parole
                ultimo = pezzi.pop()
                pezzi[-1] += ultimo
            for pezzo in pezzi:
                yield titolo_corrente, pezzo

    for riga in testo.splitlines():
        m = TITOLO_MD.match(riga)
        if m and len(m.group(1)) >= 2:
            yield from chiudi()
            livello = len(m.group(1))
            percorso = {k: v for k, v in percorso.items() if k < livello}
            percorso[livello] = m.group(2).strip()
            titolo_corrente = " › ".join(percorso[k] for k in sorted(percorso))
            corpo = []
        elif not (m and len(m.group(1)) == 1):       # il titolo del documento non è una sezione
            corpo.append(riga)
    yield from chiudi()


def panoramica(testo: str) -> str:
    """Indice e righe di panoramica del riassunto: bastano per le domande generali."""
    righe, nell_indice = [], False
    for riga in testo.splitlines():
        titolo = TITOLO_MD.match(riga)
        if titolo:
            nell_indice = titolo.group(2).strip().lower() == "indice"
            if len(titolo.group(1)) <= 2:
                righe.append(riga)
        elif (nell_indice and riga.lstrip().startswith("- ")) or riga.lstrip().startswith("**Panoramica"):
            righe.append(riga)
    return "\n".join(righe)[:MAX_CARATTERI_SEZIONE * 2]


def fonti(slug: str = "", sessione: str = ""):
    """Per ogni sessione il documento migliore disponibile: riassunto, poi appunti, poi trascrizione."""
    for progetto in ([slug] if slug else progetti.elenco()):
        info = progetti.leggi_info(progetto)
        if not slug and info["stato"] == "archiviato":
            continue
        base = progetti.cartella(progetto)
        for nome in sessioni.nomi_sessioni(base):
            if sessione and nome != sessione:
                continue
            f = sessioni.file_sessione(base, nome)
            for tipo in ("riassunto", "appunti", "trascrizione"):
                testo = sessioni.leggi(f[tipo])
                if testo.strip():
                    titolo = f["riassunto"].stem.split(" - ", 1)[1] if f["riassunto"] else ""
                    yield {"progetto": progetto, "nome_progetto": info["nome"], "sessione": nome,
                           "titolo": titolo, "tipo": tipo, "testo": testo}
                    break


def scegli_pezzi(domanda: str, slug: str, sessione: str) -> list:
    """
    Le sezioni più pertinenti alla domanda, mai il documento intero:
    punteggio = parole della domanda presenti nella sezione, pesate per rarità (una parola che compare
    ovunque, come il nome del software, conta poco) e triplicate se stanno nel titolo della sezione.
    Per le domande generali si mandano indice e panoramiche di ogni sessione.
    """
    documenti = list(fonti(slug, sessione))
    cercate = radici(domanda)
    sezioni = [(d, titolo, corpo, radici(titolo), radici(corpo))
               for d in documenti for titolo, corpo in sezioni_documento(d["testo"], d["tipo"])]
    if not sezioni:
        return []

    frequenza = {r: sum(1 for s in sezioni if r in s[3] or r in s[4]) for r in cercate}
    peso = {r: math.log(1 + len(sezioni) / f) for r, f in frequenza.items() if f}
    cerca_incerti = "incer" in cercate

    def punteggio(sezione):
        _, _, corpo, nel_titolo, nel_corpo = sezione
        p = sum(peso[r] * (3 if r in nel_titolo else 1) for r in peso if r in nel_titolo or r in nel_corpo)
        if cerca_incerti and "[INCERTO]" in corpo:
            p += 5
        return p

    specifiche = {r for r in peso if r not in GENERICHE}
    candidati = sorted(((punteggio(s), i, s) for i, s in enumerate(sezioni)), key=lambda c: (-c[0], c[1]))
    if not specifiche and not cerca_incerti or not candidati or candidati[0][0] == 0:
        # domanda generale: la mappa di ogni sessione invece di sezioni a caso
        scelti, totale = [], 0
        for d in documenti:
            pezzo = panoramica(d["testo"]) if d["tipo"] != "trascrizione" else d["testo"][:MAX_CARATTERI_SEZIONE]
            if totale + len(pezzo) > MAX_CARATTERI_CONTESTO:
                break
            scelti.append({**d, "sezione": "Indice e panoramica", "pezzo": pezzo})
            totale += len(pezzo)
        return scelti

    scelti, totale = [], 0
    for p, _, (d, titolo, corpo, _, _) in candidati:
        if p <= 0 or len(scelti) >= MAX_SEZIONI:
            break
        if totale + len(corpo) > MAX_CARATTERI_CONTESTO:
            continue
        scelti.append({**d, "sezione": titolo, "pezzo": corpo})
        totale += len(corpo)
    return scelti


def rispondi(domanda: str, storico: list, slug: str = "", sessione: str = "",
             modello: str = "") -> dict:
    catalogo = modelli()
    if not motori.testo_nvidia():
        modello = catalogo[0]["id"]        # una chat aperta prima del cambio può ancora chiedere un modello NVIDIA
    modello = modello or catalogo[0]["id"]
    if modello not in {m["id"] for m in catalogo}:
        raise RuntimeError("Modello non disponibile: scegline uno dall'elenco.")
    # le domande precedenti aiutano a capire di cosa si sta parlando ("e il secondo?")
    testo_ricerca = " ".join([m["testo"] for m in storico[-4:] if m.get("ruolo") == "utente"] + [domanda])
    pezzi = scegli_pezzi(testo_ricerca, slug, sessione)
    if not pezzi:
        return {"risposta": "Non ho trovato nei documenti niente che riguardi questa domanda. "
                            "Prova con altre parole, o allarga l'ambito a tutti i progetti.", "fonti": []}

    # una fonte numerata per sessione
    elenco_fonti, numeri = [], {}
    for p in pezzi:
        chiave = (p["progetto"], p["sessione"])
        if chiave not in numeri:
            numeri[chiave] = len(elenco_fonti) + 1
            elenco_fonti.append({k: p[k] for k in ("progetto", "nome_progetto", "sessione", "titolo", "tipo")})
    documenti = "\n\n".join(
        f"[{numeri[(p['progetto'], p['sessione'])]}] {p['nome_progetto']} — sessione {sessioni.etichetta(p['sessione'])}"
        f"{' — ' + p['titolo'] if p['titolo'] else ''} — {p['sezione']}\n{p['pezzo']}" for p in pezzi)

    messaggi = [{"role": "system", "content": SISTEMA.format(documenti=documenti)}]
    # solo gli ultimi scambi, accorciati: servono a capire "e il secondo?", non a rimandare tutto
    for m in storico[-4:]:
        messaggi.append({"role": "user" if m.get("ruolo") == "utente" else "assistant",
                         "content": str(m.get("testo", ""))[:1500]})
    messaggi.append({"role": "user", "content": domanda})

    # un modello locale sul solo processore può essere lento: gli si lascia tutto il tempo massimo
    timeout = TEMPO_MASSIMO_SECONDI if motori.testo_locale() else TIMEOUT_SECONDI
    client = motori.client_testo(timeout)
    # prima il modello scelto, poi gli altri: se uno dei gratuiti è fermo risponde il successivo
    ordine = [modello] + [m["id"] for m in catalogo if m["id"] != modello]
    nomi = {m["id"]: m["nome"] for m in catalogo}
    saltati, motivi = [], []
    avvio = time.time()
    for prova in ordine:
        rimasti = TEMPO_MASSIMO_SECONDI - (time.time() - avvio)
        if rimasti < 10:
            break
        inizio = time.time()
        try:
            r = client.with_options(timeout=min(timeout, rimasti)).chat.completions.create(
                model=prova, messages=messaggi, temperature=0.2, max_tokens=3000)
            testo = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
            motivo = "risposta vuota"
        except Exception as exc:
            testo = ""
            motivo = f"{type(exc).__name__}: {str(exc)[:120]}"
        if not testo:
            # finisce nel log della dashboard: si vede quale modello è fermo e perché
            print(f"[CHAT] {prova} non ha risposto in {time.time() - inizio:.0f}s ({motivo})", flush=True)
            motivi.append(f"{nomi[prova]}: {motivo}")
        if testo:
            _stato_modelli["stato"][prova] = True
            return {"risposta": testo, "fonti": elenco_fonti, "modello": prova, "nome_modello": nomi[prova],
                    "secondi": round(time.time() - inizio), "saltati": saltati}
        _stato_modelli["stato"][prova] = False
        saltati.append(nomi[prova])
    if motori.testo_locale():
        raise RuntimeError(f"L'AI locale ({motori.url_testo()}) non ha risposto: è avviata e il modello "
                           f"«{modello}» è scaricato? Dettagli: " + "; ".join(motivi))
    if not motori.testo_nvidia():
        raise RuntimeError(f"{motori.nome_testo()} non ha risposto: controlla la chiave e il nome del modello "
                           f"«{modello}» in Impostazioni. Dettagli: " + "; ".join(motivi))
    raise RuntimeError(f"Nessun modello NVIDIA ha risposto in {time.time() - avvio:.0f} s. Riprova tra poco. "
                       "Dettagli: " + "; ".join(motivi))


def stato_modelli() -> dict:
    """Prova veloce di tutti i modelli in parallelo (risultato tenuto qualche minuto): {id: risponde?}."""
    if not motori.testo_nvidia():
        return {}                # un solo modello: niente da scegliere, e la prova costerebbe una richiesta
    if time.time() - _stato_modelli["quando"] < DURATA_STATO_SECONDI and _stato_modelli["stato"]:
        return _stato_modelli["stato"]
    try:
        client = motori.client_testo(TIMEOUT_PROVA_SECONDI)
    except RuntimeError:
        return {}

    def prova(mid):
        try:
            client.chat.completions.create(model=mid, messages=[{"role": "user", "content": "ok"}], max_tokens=5)
            return mid, True
        except Exception:
            return mid, False

    with ThreadPoolExecutor(len(MODELLI)) as pool:
        stato = dict(pool.map(prova, [m["id"] for m in MODELLI]))
    _stato_modelli.update(quando=time.time(), stato=stato)
    return stato
