# Piano Implementativo: Refactoring Chat, Guardrail e Ottimizzazione Token

Documento di specifiche tecniche e implementazione per **Claude**.
Contiene tutte le richieste pendenti relative al modulo di chat (`src/chat.py`), alla gestione delle fonti, ai guardrail di contesto e alla risoluzione del problema di saturazione dei token.

---

## 1. Problemi Rilevati e Obiettivi

1. **Saluti e chit-chat ("ciao", "buongiorno", ecc.)**:
   - Attualmente consultano sia i documenti interni (caricando fino a 12.000 caratteri di panoramica a causa del bug `candidati[0][0] == 0`) sia Wikipedia (cercando pagine come "Bella ciao", "Ciao Ciao").
   - Mostrano chip di fonti e link internet sotto un semplice saluto.
   - **Obiettivo**: Per saluti e convenevoli non consultare né documenti né internet; rispondere in modo cordiale, breve e senza mostrare fonti.

2. **Priorità Documenti Interni rispetto a Internet (Documenti -> Web)**:
   - Attualmente Wikipedia viene interrogata incondizionatamente ad ogni messaggio, anche quando i documenti interni hanno già la risposta esatta (es. completamento frasi sui fornitori ha prodotto link a *Basilica di Superga*, *Alfabeto thailandese*, *Velocità della luce*).
   - **Obiettivo**: Cercare PRIMA nei documenti interni. Se i documenti contengono sezioni pertinenti (punteggio > 0), NON consultare internet. Andare su internet solo se i documenti non trovano nulla (o se l'utente richiede esplicitamente una ricerca web).

3. **Filtro Bibliografia Web ("Serve? Mostra <-> Non serve? Non mostra")**:
   - Attualmente tutti i risultati restituiti da Wikipedia vengono passati al frontend e mostrati nella riga `Internet: [...]`, anche se totalmente irrilevanti o non utilizzati dal modello.
   - **Obiettivo**: Filtrare l'elenco `web` post-generazione: se il modello nella risposta non ha effettivamente citato o utilizzato la pagina web (link o titolo), la pagina non deve comparire nell'array `web` restituito al frontend.

4. **Guardrail Generali basati sul Contesto del Progetto**:
   - Attualmente il prompt di sistema della chat non include il `contesto.txt` del progetto e non impedisce al modello di rispondere a domande fuori tema.
   - **Obiettivo**: Iniettare `contesto.txt` nel prompt di sistema di `src/chat.py` e definire una regola inderogabile: se la richiesta è fuori dall'ambito del progetto (ricette, meteo, compiti scolastici, cultura generale estranea), il modello deve rifiutare cortesemente ("Posso rispondere solo a domande relative ai contenuti e al contesto di questo corso.").

5. **Saturazione Token (Richiesta sempre al limite massimo per domande e saluti)**:
   - Con il modello `openai/gpt-oss-120b` su Groq (limite gratuito di 8.000 TPM), le chiamate arrivano costantemente al limite di token per due motivi:
     - `MAX_CARATTERI_CONTESTO = 12000` + Wikipedia (4.500 car.) + prompt di sistema: ~4.500 token in input.
     - `max_tokens=motori.max_token("chat", 3000)`: prenota 3.000 token in output. Totale riservato per chiamata: ~7.500 token, saturando istantaneamente il TPM.
     - Il bug `candidati[0][0] == 0` faceva scattare questo carico anche per la parola "ciao".
   - **Obiettivo**:
     - Ridurre `MAX_CARATTERI_CONTESTO` a 5.000 car., `MAX_SEZIONI` a 4, `MAX_CARATTERI_SEZIONE` a 1.500 car.
     - Ridurre Wikipedia a max 2 pagine di 800 caratteri.
     - Risolvere il bug `candidati[0][0] == 0` (se il punteggio è 0 e non è richiesta una panoramica, restituire `[]`).
     - Modulare `max_tokens`: 120 token per saluti/convenevoli, `min(800, motori.max_token("chat", 800))` per risposte normali.

---

## 2. Modifiche Dettagliate nel Codice (`src/chat.py`)

### A. Parametri di Contesto e Token (Righe 20-30 e 55-60)

```python
# Prima:
MAX_CARATTERI_CONTESTO = 12000
MAX_SEZIONI = 8
MAX_CARATTERI_SEZIONE = 3000
...
MAX_PAGINE_WEB = 3
MAX_CARATTERI_PAGINA = 1500

# Dopo:
MAX_CARATTERI_CONTESTO = 5000       # dimezzato per non saturare i token TPM
MAX_SEZIONI = 4
MAX_CARATTERI_SEZIONE = 1500
...
MAX_PAGINE_WEB = 2
MAX_CARATTERI_PAGINA = 800
```

---

### B. Prompt di Sistema con Guardrail e Iniezione Contesto

Aggiornare `SISTEMA` per includere `{contesto}` e i guardrail obbligatori:

```python
SISTEMA = """Sei l'assistente dedicato a questo corso e ai documenti delle sue sessioni.
Rispondi in italiano, in Markdown, in modo chiaro, sintetico e pratico. Solo Markdown: mai tag HTML.

CONTESTO DEL PROGETTO:
{contesto}

GUARDRAIL E AMBITO (STRETTAMENTE OBBLIGATORI):
1. AMBITO: Rispondi SOLO a domande pertinenti agli argomenti delle sessioni e al contesto sopra descritto.
2. FUORI SCOPE: Se la richiesta dell'utente è estranea al corso o al contesto (es. ricette, gossip, meteo, compiti non correlati, cultura generale estranea, codice o argomenti non trattati): NON RISPONDERE alla richiesta. Rifiuta cortesemente con una sola frase breve: "Posso rispondere solo a domande relative ai contenuti e al contesto di questo corso."
3. SALUTI: Se l'utente ti saluta o ringrazia, rispondi con cordialità e brevità (1-2 frasi), ricordando il tuo ruolo.
4. FONTI: Basati prima di tutto sui DOCUMENTI interni: citali tra parentesi quadre, es. [1].
   Se le PAGINE WEB sono presenti e utili, citale con il link. Se non sono utili o non servono, non citarle affatto.
5. FEDELTÀ: Se la risposta non c'è nei documenti né nel contesto, dillo chiaramente; non inventare mai. I passaggi segnati [INCERTO] vanno riportati come incerti.

Dopo la risposta, se in questo scambio sono emerse informazioni NUOVE che i documenti non contengono
(qualcosa che l'utente ti ha detto o corretto, o un approfondimento reale dalle pagine web che hai usato nella risposta),
scrivi una riga con solo {separatore} e sotto una voce per informazione: "- " seguito da una frase breve che si
capisca da sola, chiusa dalla fonte tra parentesi: (detto in chat) oppure (fonte: link della pagina).
Niente voci per cose già nei documenti o per tue deduzioni; se non c'è niente di nuovo, non scrivere la riga {separatore}.

DOCUMENTI:
{documenti}

PAGINE WEB:
{web}"""
```

---

### C. Helper: Lettura Contesto e Rilevamento Saluti

Aggiungere le seguenti funzioni di supporto in `src/chat.py`:

```python
SALUTI_CONVERSAZIONE = re.compile(
    r"^\s*(?:ciao|buongiorno|buonasera|salve|buond[iì]|ehi|hey|hello|hi|"
    r"grazie(?: mille)?|ti ringrazio|molte grazie|perfetto|ok|ottimo|va bene|"
    r"chi sei(?:\s+tu)?|come ti chiami|cosa puoi fare|come funzioni|"
    r"arrivederci|a presto|buona giornata|buon lavoro)(?:\s+(?:a tutti|tutti|grazie))?[.!?\s]*$",
    re.IGNORECASE
)

def e_saluto(testo: str) -> bool:
    """True se il messaggio è solo un saluto, ringraziamento o convenevole senza domande tecniche."""
    return bool(SALUTI_CONVERSAZIONE.match(testo.strip()))


def leggi_contesto_progetto(slug: str = "") -> str:
    """Legge il contesto del progetto specificato o aggrega i contesti dei progetti attivi."""
    if slug:
        f = progetti.cartella(slug) / "contesto.txt"
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    contesti = []
    for p in progetti.elenco():
        info = progetti.leggi_info(p)
        if info.get("stato") != "archiviato":
            f = progetti.cartella(p) / "contesto.txt"
            if f.exists():
                t = f.read_text(encoding="utf-8").strip()
                if t:
                    contesti.append(f"Progetto «{info.get('nome', p)}»:\n{t}")
    return "\n\n".join(contesti) if contesti else "Formazione e sessioni aziendali."


def pagine_web_utili(testo_risposta: str, pagine: list) -> list:
    """Mantiene solo le pagine web effettivamente citate (link o titolo) nella risposta del modello."""
    if not pagine or not testo_risposta:
        return []
    utili = []
    t_lower = testo_risposta.casefold()
    for p in pagine:
        link = p.get("link", "").casefold()
        titolo = p.get("titolo", "").casefold()
        if link and link in t_lower:
            utili.append(p)
            continue
        if titolo and titolo in t_lower:
            utili.append(p)
            continue
        parole_titolo = [w for w in re.findall(r"[\wà-ù'-]{4,}", titolo) if w not in PAROLE_VUOTE]
        if len(parole_titolo) >= 2 and all(w in t_lower for w in parole_titolo):
            utili.append(p)
    return utili
```

---

### D. Correzione Bug `candidati[0][0] == 0` in `scegli_pezzi`

Modificare la sezione finale di `scegli_pezzi` (intorno alla riga 280):

```python
    specifiche = {r for r in peso if r not in GENERICHE}
    candidati = sorted(((punteggio(s), i, s) for i, s in enumerate(sezioni)), key=lambda c: (-c[0], c[1]))

    # Domanda generale (panoramica/sintesi): solo se l'utente richiede espressamente una panoramica
    domanda_generale = any(r in GENERICHE for r in cercate) and not specifiche
    if domanda_generale and not cerca_incerti:
        scelti, totale = [], 0
        for d in documenti:
            pezzo = panoramica(d["testo"]) if d["tipo"] != "trascrizione" else d["testo"][:MAX_CARATTERI_SEZIONE]
            if totale + len(pezzo) > MAX_CARATTERI_CONTESTO:
                break
            scelti.append({**d, "sezione": "Indice e panoramica", "pezzo": pezzo})
            totale += len(pezzo)
        return scelti

    # Se non ci sono candidati con punteggio strettamente positivo, nessun documento è pertinente
    if not candidati or candidati[0][0] <= 0:
        return []

    scelti, totale = [], 0
    for p, _, (d, titolo, corpo, _, _) in candidati:
        if p <= 0 or len(scelti) >= MAX_SEZIONI:
            break
        if totale + len(corpo) > MAX_CARATTERI_CONTESTO:
            continue
        scelti.append({**d, "sezione": titolo, "pezzo": corpo})
        totale += len(corpo)
    return scelti
```

---

### E. Flusso e Logica di Ricerca in `rispondi`

Ristrutturare la funzione `rispondi(...)`:

```python
def rispondi(domanda: str, storico: list, slug: str = "", sessione: str = "") -> dict:
    modello = motori.modello("chat")
    saluto = e_saluto(domanda)

    if saluto:
        # Per saluti e convenevoli: nessun documento, nessun accesso a Wikipedia
        pezzi = []
        web = []
    else:
        # 1. Ricerca PRIMA nei documenti interni
        testo_ricerca = " ".join([m["testo"] for m in storico[-4:] if m.get("ruolo") == "utente"] + [domanda])
        pezzi = scegli_pezzi(testo_ricerca, slug, sessione)

        # 2. Internet viene consultato SOLO SE i documenti interni non hanno trovato nulla
        #    oppure se l'utente richiede esplicitamente una ricerca web
        chiede_web = bool(re.search(r"\b(internet|web|online|wikipedia|google|cerca su|cerca nel web)\b", domanda.casefold()))
        if not pezzi or chiede_web:
            web = cerca_web(domanda)
        else:
            web = []

    if not pezzi and not web and not slug and not saluto:
        return {"risposta": "Non ho trovato nei documenti né su internet informazioni su questa domanda. "
                            "Prova con altre parole o seleziona un progetto.", "fonti": []}

    # Formattazione fonti numerate (solo se presenti sezioni pertinenti)
    elenco_fonti, numeri = [], {}
    for p in pezzi:
        chiave = (p["progetto"], p["sessione"])
        if chiave not in numeri:
            numeri[chiave] = len(elenco_fonti) + 1
            elenco_fonti.append({k: p[k] for k in ("progetto", "nome_progetto", "sessione", "titolo", "tipo")})

    documenti = "\n\n".join(
        f"[{numeri[(p['progetto'], p['sessione'])]}] {p['nome_progetto']} — sessione {sessioni.etichetta(p['sessione'])}"
        f"{' — ' + p['titolo'] if p['titolo'] else ''} — {p['sezione']}\n{p['pezzo']}" for p in pezzi)

    pagine = "\n\n".join(f"{p['titolo']} ({p['link']})\n{p['testo']}" for p in web)
    contesto_progetto = leggi_contesto_progetto(slug)

    messaggi = [{"role": "system", "content": SISTEMA.format(
        contesto=contesto_progetto or "(Nessun contesto specifico impostato)",
        documenti=documenti or "(nessun documento pertinente)",
        web=pagine or "(nessuna pagina consultata)",
        separatore=SEPARATORE_APPUNTI)}]

    for m in storico[-4:]:
        messaggi.append({"role": "user" if m.get("ruolo") == "utente" else "assistant",
                         "content": str(m.get("testo", ""))[:1500]})
    messaggi.append({"role": "user", "content": domanda})

    # Token dinamici: 120 per saluti, max 800 per risposte ordinarie (evita saturazione TPM Groq)
    token_max = 120 if saluto else min(800, motori.max_token("chat", 800))

    inizio = time.time()
    try:
        r = motori.client("chat", TIMEOUT_SECONDI).chat.completions.create(
            model=modello, messages=messaggi, temperature=0.2, max_tokens=token_max)
        testo = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[CHAT] {modello} non ha risposto in {time.time() - inizio:.0f}s ({type(exc).__name__}: {str(exc)[:200]})", flush=True)
        raise RuntimeError(f"{motori.nome('chat')} non ha risposto ({type(exc).__name__}: {str(exc)[:160]}). "
                           f"Riprova tra poco, o controlla chiave e modello «{modello}» in Impostazioni.")
    if not testo:
        raise RuntimeError(f"{motori.nome('chat')} ha dato una risposta vuota: riprova tra poco.")

    # 3. Filtro bibliografia web: mostra i link internet SOLO se sono stati effettivamente citati o usati
    web = pagine_web_utili(testo, web)

    testo, voci = separa_aggiunte(testo)
    segnate = None
    if voci and pezzi:
        dove, nome = sessione_per_aggiunte(slug, sessione, pezzi)
        segnate = {"voci": voci, "nuove": 0}
        if nome:
            segnate.update(progetto=dove, sessione=nome, nuove=sessioni.aggiungi(dove, nome, voci))

    return {"risposta": testo, "fonti": elenco_fonti, "nome_modello": modello,
            "secondi": round(time.time() - inizio), "web": web, "segnate": segnate}
```

---

## 3. Matrice dei Casi di Test di Verifica

| Caso di Test | Input Utente | Risultato Atteso |
|---|---|---|
| **Saluto semplice** | "ciao", "buongiorno" | Risposta cordiale breve, `fonti = []`, `web = []`, consumo token ~150 |
| **Completamento frase coperto da appunti** | "I materiali sono offerti da..." | Usa documenti [1], `web = []` (nessun accesso a Wikipedia, zero link tipo Superga) |
| **Domanda fuori scope** | "Come fare la carbonara?", "Meteo Roma" | Il modello rifiuta cortesemente di rispondere ("Posso rispondere solo..."), `web = []` |
| **Richiesta panoramica generale** | "Riassumi i temi principali del corso" | Carica `panoramica` documenti (<=5.000 car.), `web = []` |
| **Domanda tecnica assente da documenti** | "Cos'è la matrice Kraljic?" (se non nei doc) | `pezzi = []`, cerca su Wikipedia, se cita Kraljic mostra il link, se non usata `web = []` |
| **Consumo token** | Qualsiasi messaggio | `max_tokens` limitato a 120 (saluti) o 800 (domande), zero superamento TPM Groq |

---

## 4. Istruzioni per l'Esecutore (Claude)

1. Aprire `src/chat.py` ed effettuare le modifiche indicate nelle sezioni A, B, C, D, E.
2. Verificare la sintassi Python con `python -m py_compile src/chat.py`.
3. Testare le funzioni con un runner di prova:
   - Verifica `e_saluto(...)` su stringhe di test.
   - Verifica `pagine_web_utili(...)`.
   - Verifica `scegli_pezzi(...)` con query a punteggio 0 (deve restituire `[]`).
4. Eseguire `git add src/chat.py` e commit con messaggio descrittivo.
5. Eseguire `git push`.
