"""
Domande sui documenti dei progetti (riassunti, appunti, trascrizioni),
con la chiave e il modello scelti per la chat nelle Impostazioni (NVIDIA, Groq, OpenAI, Gemini, Claude, AI locale...).
Nessun indice: si scelgono i pezzi più pertinenti con un conteggio di parole, a ogni domanda.
"""

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request


import motori
import progetti
import sessioni

# al modello vanno solo le sezioni che servono: poche e brevi, per non saturare i token al minuto (Groq gratuito: 8.000)
MAX_CARATTERI_CONTESTO = 5000
MAX_SEZIONI = 4
MAX_CARATTERI_SEZIONE = 1500
MAX_CARATTERI_CONTESTO_PROGETTO = 2000
MAX_TOKEN_SALUTO = 120
MAX_TOKEN_RISPOSTA = 800
TIMEOUT_SECONDI = 100

PAROLE_VUOTE = set("""il lo la i gli le un uno una di a da in con su per tra fra e o ma se che chi cui non come
dove quando quale quali quanto cosa cos è sono era del dello della dei degli delle al allo alla ai agli alle
dal dallo dalla dai dagli dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle questo questa
quello quella ci si ne mi ti vi lui lei noi voi loro mio tuo suo anche più molto poi già fare fa ha hanno
essere stato detto dice spiegato spiega parlato""".split())

SEPARATORE_APPUNTI = "===APPUNTI==="

SISTEMA = """Sei l'assistente dedicato a questo corso e ai documenti delle sue sessioni (lezioni, video, riunioni...).
Rispondi in italiano, in Markdown, in modo chiaro, sintetico e pratico. Solo Markdown: mai tag HTML (per andare a capo in una tabella usa ";").

CONTESTO DEL PROGETTO:
{contesto}

REGOLE (obbligatorie):
1. AMBITO: rispondi solo a domande che riguardano gli argomenti delle sessioni e il contesto qui sopra.
2. FUORI TEMA: se la richiesta non c'entra con il corso o il contesto (ricette, meteo, gossip, compiti non correlati,
   cultura generale estranea, codice o argomenti non trattati) non rispondere: rifiuta con una sola frase,
   "Posso rispondere solo a domande relative ai contenuti e al contesto di questo corso."
3. SALUTI: se l'utente saluta o ringrazia, rispondi con cordialità in 1-2 frasi, ricordando a cosa servi.
4. FONTI, da non confondere:
   - i DOCUMENTI delle sessioni (riassunti, appunti, trascrizioni) vengono prima di tutto: citali tra parentesi quadre, es. [2];
   - le PAGINE WEB qui sotto: citale con il link solo se le usi davvero; se non servono, ignorale;
   - quello che l'utente ti dice nella conversazione: è attendibile, anche se corregge i documenti.
5. FEDELTÀ: se la risposta non c'è in nessuna fonte, dillo chiaramente; non inventare mai.
   I passaggi segnati [INCERTO] vanno riportati come incerti. Le righe [aggiunta] le aveva già segnate la chat.

Dopo la risposta, se in questo scambio sono emerse informazioni NUOVE che i documenti non contengono
(qualcosa che l'utente ti ha detto o corretto, o un approfondimento dalle pagine web che hai usato nella risposta),
scrivi una riga con solo {separatore} e sotto una voce per informazione: "- " seguito da una frase breve che si
capisca da sola, chiusa dalla fonte tra parentesi: (detto in chat) oppure (fonte: link della pagina).
Niente voci per cose già nei documenti o per tue deduzioni; se non c'è niente di nuovo, non scrivere la riga {separatore}.
Eccezione: se l'utente corregge la tua risposta o ti chiede di segnare, annotare o aggiungere qualcosa,
scrivi sempre la riga {separatore} con la voce corretta, anche se in parte compare già nei documenti.

DOCUMENTI:
{documenti}

PAGINE WEB:
{web}"""

# approfondimenti da internet: Wikipedia ha un'API libera (i motori di ricerca bloccano le richieste automatiche)
WIKIPEDIA = "https://it.wikipedia.org/w/api.php"
MAX_PAGINE_WEB = 2
MAX_CARATTERI_PAGINA = 800
TIMEOUT_WEB_SECONDI = 8
# internet si consulta solo se i documenti non hanno niente, o se lo chiede l'utente
CHIEDE_WEB = re.compile(r"\b(internet|web|online|wikipedia|google|cerca su|cerca nel web)\b", re.IGNORECASE)

# saluti e convenevoli: niente documenti, niente internet, risposta breve
SALUTI = re.compile(
    r"^\s*(?:ciao|buongiorno|buonasera|salve|buond[iì]|ehi|hey|hello|hi|"
    r"grazie(?: mille)?|ti ringrazio|molte grazie|perfetto|ok|ottimo|va bene|"
    r"chi sei(?:\s+tu)?|come ti chiami|cosa puoi fare|come funzioni|"
    r"arrivederci|a presto|buona giornata|buon lavoro)(?:\s+(?:a tutti|tutti|grazie))?[.!?\s]*$",
    re.IGNORECASE)


def e_saluto(testo: str) -> bool:
    """True se il messaggio è solo un saluto, un ringraziamento o un convenevole."""
    return bool(SALUTI.match(testo))


def contesto_progetto(slug: str = "") -> str:
    """Il contesto.txt del progetto scelto, o quelli dei progetti attivi se non se n'è scelto uno."""
    def leggi(progetto):
        f = progetti.cartella(progetto) / "contesto.txt"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""

    if slug:
        testo = leggi(slug)
    else:
        testo = "\n\n".join(f"Progetto «{info['nome']}»:\n{t}" for info, t in
                            ((progetti.leggi_info(p), leggi(p)) for p in progetti.elenco())
                            if info["stato"] != "archiviato" and t)
    return testo[:MAX_CARATTERI_CONTESTO_PROGETTO] or "(nessun contesto specifico impostato)"


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


# --- classificazione automatica delle parole per la ricerca Wikipedia ---
_NUMERI_WEB = {"due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci",
               "primo", "secondo", "terzo", "quarto", "quinto", "sesto", "settimo",
               "undici", "dodici", "tredici", "cento", "mille"}
_SUFFISSI_VERBALI_WEB = ("ano", "ono", "are", "ere", "ire", "ato", "uto", "ito", "ando", "endo",
                         "isce", "iscono", "iona", "enta", "ura", "izza")
_SUFFISSI_GENERICI_WEB = ("mente", "zione", "zioni", "aggio", "ezza")
_VERBI_DOMANDA_WEB = {"significa", "significano", "rappresenta", "rappresentano", "funziona", "funzionano",
                      "prevede", "prevedono", "consiste", "consistono", "descrive", "descrivono",
                      "indica", "indicano", "serve", "servono", "contiene", "contengono",
                      "riguarda", "riguardano", "chiama", "chiamano", "spiega", "spiegami",
                      "spiegano", "configura", "definisce", "differenza", "differenze",
                      "vantaggi", "svantaggi", "caratteristiche"}


def _specificita_web(parola: str, sigle: set) -> int:
    """Punteggio di specificità: più alto → più utile per cercare su Wikipedia."""
    p = parola
    if p in _NUMERI_WEB:
        return -20
    if p in _VERBI_DOMANDA_WEB:
        return -10
    s = 0
    if p.upper() in sigle:          # sigla maiuscola nel testo originale (EDI, MRP...)
        s += 25
    if re.search(r"[kwxyj]", p):    # lettera rara in italiano → termine straniero/tecnico
        s += 20
    if re.search(r"\d", p):          # contiene cifre → codice
        s += 15
    for suf in _SUFFISSI_VERBALI_WEB:
        if p.endswith(suf) and len(p) > len(suf) + 2:
            s -= 8
            break
    for suf in _SUFFISSI_GENERICI_WEB:
        if p.endswith(suf) and len(p) > len(suf) + 2:
            s -= 5
            break
    if len(p) <= 3:
        s -= 5
    return s


def cerca_web(domanda: str) -> list:
    """Le pagine di Wikipedia più pertinenti alla domanda: [{titolo, link, testo}]; [] se internet non risponde."""
    # Wikipedia cerca pagine con TUTTE le parole: si prova la frase intera, poi meno parole.
    # Le parole vanno ordinate per specificità (termini tecnici e sigle prima, verbi generici dopo),
    # così i fallback con meno parole cercano quelle che contano davvero.
    sigle = set(re.findall(r"\b[A-Z]{2,}\b", domanda))
    domande = re.findall(r"[^.!?\n]*\?", domanda)
    testo_ricerca = (domande[-1] if domande else domanda).casefold()
    parole = [p for p in re.findall(r"[\wà-ù'-]{3,}", testo_ricerca)
              if p not in PAROLE_VUOTE]
    if not parole:
        return []
    parole = sorted(parole, key=lambda p: _specificita_web(p, sigle), reverse=True)

    def chiedi(parametri):
        url = f"{WIKIPEDIA}?{urllib.parse.urlencode({**parametri, 'format': 'json', 'formatversion': 2})}"
        richiesta = urllib.request.Request(url, headers={"User-Agent": "trascrizioni/1.0 (chat sui documenti)"})
        with urllib.request.urlopen(richiesta, timeout=TIMEOUT_WEB_SECONDI) as r:
            return json.load(r)

    try:
        trovate = []
        for cercate in dict.fromkeys((" ".join(parole), " ".join(parole[:4]), " ".join(parole[:2]))):
            trovate = chiedi({"action": "query", "list": "search", "srsearch": cercate,
                              "srlimit": MAX_PAGINE_WEB})["query"]["search"]
            if trovate:
                break
        if not trovate:
            return []
        pagine = chiedi({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                         "titles": "|".join(t["title"] for t in trovate)})["query"]["pages"]
    except Exception as exc:
        print(f"[CHAT] Ricerca su Wikipedia non riuscita ({type(exc).__name__}): rispondo solo dai documenti", flush=True)
        return []
    return [{"titolo": p["title"], "link": "https://it.wikipedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_")),
             "testo": p.get("extract", "")[:MAX_CARATTERI_PAGINA]} for p in pagine if p.get("extract")]


def pagine_web_utili(risposta: str, pagine: list) -> list:
    """Solo le pagine che la risposta cita davvero (link, titolo o tutte le parole del titolo): le altre non si mostrano."""
    testo = risposta.casefold()
    utili = []
    for p in pagine:
        link, titolo = p.get("link", "").casefold(), p.get("titolo", "").casefold()
        parole = [w for w in re.findall(r"[\wà-ù'-]{4,}", titolo) if w not in PAROLE_VUOTE]
        citata = (link and (link in testo or urllib.parse.unquote(link) in testo)) or (titolo and titolo in testo)
        if citata or (len(parole) >= 2 and all(w in testo for w in parole)):
            utili.append(p)
    return utili


def separa_aggiunte(testo: str) -> tuple:
    """(risposta da mostrare, [voci nuove da segnare]) dalla risposta del modello."""
    risposta, _, coda = testo.partition(SEPARATORE_APPUNTI)
    voci = [re.sub(r"^\s*[-*]\s*", "", r).strip() for r in coda.splitlines() if re.match(r"^\s*[-*]\s+\S", r)]
    return risposta.strip(), voci


def sessione_per_aggiunte(slug: str, sessione: str, pezzi: list):
    """Dove segnare le aggiunte: la sessione scelta, altrimenti quella della fonte più pertinente, o l'ultima del progetto."""
    if sessione:
        return slug, sessione
    if pezzi:
        return pezzi[0]["progetto"], pezzi[0]["sessione"]
    nomi = sessioni.nomi_sessioni(progetti.cartella(slug)) if slug else []
    return (slug, max(nomi)) if nomi else (None, None)


def fonti(slug: str = "", sessione: str = ""):
    """
    Per ogni sessione riassunto e appunti (il riassunto sintetizza e può saltare dettagli che gli appunti hanno);
    la trascrizione solo se mancano entrambi. Il primo documento della sessione porta le aggiunte e fa la panoramica.
    """
    for progetto in ([slug] if slug else progetti.elenco()):
        info = progetti.leggi_info(progetto)
        if not slug and info["stato"] == "archiviato":
            continue
        base = progetti.cartella(progetto)
        for nome in sessioni.nomi_sessioni(base):
            if sessione and nome != sessione:
                continue
            f = sessioni.file_sessione(base, nome)
            aggiunte = sessioni.leggi(f["aggiunte"])
            titolo = f["riassunto"].stem.split(" - ", 1)[1] if f["riassunto"] else ""
            primo = True
            for tipo in ("riassunto", "appunti", "trascrizione"):
                testo = sessioni.leggi(f[tipo])
                if not testo.strip() or (tipo == "trascrizione" and not primo):
                    continue
                yield {"progetto": progetto, "nome_progetto": info["nome"], "sessione": nome, "titolo": titolo,
                       "tipo": tipo, "testo": sessioni.con_aggiunte(testo, aggiunte) if primo else testo,
                       "principale": primo}
                primo = False


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
    # domanda generale (panoramica, sintesi): la mappa di ogni sessione invece di sezioni a caso.
    # Serve almeno una parola "generale" nella domanda: "ciao" o parole che non compaiono in nessun documento non lo sono.
    if any(r in GENERICHE for r in cercate) and not specifiche and not cerca_incerti:
        scelti, totale = [], 0
        for d in (d for d in documenti if d["principale"]):
            pezzo = panoramica(d["testo"]) if d["tipo"] != "trascrizione" else d["testo"][:MAX_CARATTERI_SEZIONE]
            if totale + len(pezzo) > MAX_CARATTERI_CONTESTO:
                break
            scelti.append({**d, "sezione": "Indice e panoramica", "pezzo": pezzo})
            totale += len(pezzo)
        return scelti

    scelti, totale = [], 0      # nessuna sezione con punteggio > 0: nessun documento pertinente
    for p, _, (d, titolo, corpo, _, _) in candidati:
        if p <= 0 or len(scelti) >= MAX_SEZIONI:
            break
        if totale + len(corpo) > MAX_CARATTERI_CONTESTO:
            continue
        scelti.append({**d, "sezione": titolo, "pezzo": corpo})
        totale += len(corpo)
    return scelti


def rispondi(domanda: str, storico: list, slug: str = "", sessione: str = "") -> dict:
    modello = motori.modello("chat")
    saluto = e_saluto(domanda)
    pezzi, web = [], []
    if not saluto:
        # prima i documenti; le domande precedenti aiutano a capire di cosa si sta parlando ("e il secondo?")
        testo_ricerca = " ".join([m["testo"] for m in storico[-4:] if m.get("ruolo") == "utente"] + [domanda])
        pezzi = scegli_pezzi(testo_ricerca, slug, sessione)
        # internet solo se i documenti non hanno niente, o se l'utente lo chiede
        if not pezzi or CHIEDE_WEB.search(domanda):
            web = cerca_web(domanda)
    if not pezzi and not web and not slug and not saluto:
        return {"risposta": "Non ho trovato nei documenti né su internet niente che riguardi questa domanda. "
                            "Prova con altre parole, o scegli un progetto.", "fonti": []}

    # una fonte numerata per documento (riassunto e appunti della stessa sessione sono due fonti: il link apre quello giusto)
    elenco_fonti, numeri = [], {}
    for p in pezzi:
        chiave = (p["progetto"], p["sessione"], p["tipo"])
        if chiave not in numeri:
            numeri[chiave] = len(elenco_fonti) + 1
            elenco_fonti.append({k: p[k] for k in ("progetto", "nome_progetto", "sessione", "titolo", "tipo")})
    documenti = "\n\n".join(
        f"[{numeri[(p['progetto'], p['sessione'], p['tipo'])]}] {p['nome_progetto']} — sessione {sessioni.etichetta(p['sessione'])}"
        f"{' — ' + p['titolo'] if p['titolo'] else ''} — {p['tipo']} — {p['sezione']}\n{p['pezzo']}" for p in pezzi)

    pagine = "\n\n".join(f"{p['titolo']} ({p['link']})\n{p['testo']}" for p in web)
    messaggi = [{"role": "system", "content": SISTEMA.format(
        contesto=contesto_progetto(slug), documenti=documenti or "(nessun documento pertinente)",
        web=pagine or "(nessuna pagina consultata)", separatore=SEPARATORE_APPUNTI)}]
    # solo gli ultimi scambi, accorciati: servono a capire "e il secondo?", non a rimandare tutto
    for m in storico[-4:]:
        messaggi.append({"role": "user" if m.get("ruolo") == "utente" else "assistant",
                         "content": str(m.get("testo", ""))[:1500]})
    messaggi.append({"role": "user", "content": domanda})

    # pochi token riservati per la risposta: con 3000 una sola chiamata esauriva i token al minuto di Groq
    token_max = MAX_TOKEN_SALUTO if saluto else min(MAX_TOKEN_RISPOSTA, motori.max_token("chat", MAX_TOKEN_RISPOSTA))
    inizio = time.time()
    try:
        r = motori.client("chat", TIMEOUT_SECONDI).chat.completions.create(
            model=modello, messages=messaggi, temperature=0.2, max_tokens=token_max)
        testo = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
    except RuntimeError:
        raise
    except Exception as exc:
        # finisce nel log della dashboard: si vede perché il modello non ha risposto
        print(f"[CHAT] {modello} non ha risposto in {time.time() - inizio:.0f}s ({type(exc).__name__}: {str(exc)[:200]})", flush=True)
        raise RuntimeError(f"{motori.nome('chat')} non ha risposto ({type(exc).__name__}: {str(exc)[:160]}). "
                           f"Riprova tra poco, o controlla chiave e modello «{modello}» in Impostazioni.")
    if not testo:
        raise RuntimeError(f"{motori.nome('chat')} ha dato una risposta vuota: riprova tra poco.")

    web = pagine_web_utili(testo, web)      # sotto la risposta solo le pagine che ha usato (anche solo negli appunti)
    testo, voci = separa_aggiunte(testo)
    segnate = None
    if voci:
        dove, nome = sessione_per_aggiunte(slug, sessione, pezzi)
        segnate = {"voci": voci, "nuove": 0}
        if nome:
            segnate.update(progetto=dove, sessione=nome, nuove=sessioni.aggiungi(dove, nome, voci))
    return {"risposta": testo, "fonti": elenco_fonti, "nome_modello": modello,
            "secondi": round(time.time() - inizio), "web": web, "segnate": segnate}
