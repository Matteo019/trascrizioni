# Piano Implementativo: Istruzioni persistenti, conversazioni salvate e rollover

Specifiche per **Claude**. Riguarda `src/chat.py`, `src/webapp.py`, `web/app.js` (funzione `creaChat`).

---

## 1. Problemi rilevati

1. **Vincoli ripetuti a ogni domanda.** In modalità quiz l'utente ha scritto "1 parola" più di dieci volte. Ogni
   domanda arriva al modello senza memoria del vincolo, perché `rispondi()` manda solo gli ultimi 4 messaggi
   accorciati e nessuna "istruzione di conversazione".
2. **I seguiti sporcano la ricerca.** "1 parola", "riprova", "non è regolari, riprova" finiscono in `testo_ricerca`
   e pescano sezioni a caso (es. "richieste" dai documenti di un altro corso). "Riprova" restituisce la stessa risposta.
3. **Le conversazioni non si salvano.** Lo storico sta in `sessionStorage` (`chat:<prog>:<sess>`): si perde
   chiudendo la scheda, non c'è un elenco di chat passate e non si può esportare.
4. **Le chat lunghe non hanno limite.** Non c'è un tetto: lo storico cresce senza che nulla lo chiuda o lo riassuma.

## 2. Obiettivi

- Le **istruzioni della chat** ("rispondi con 1 parola", "solo le parole mancanti") valgono per tutta la conversazione
  finché l'utente non le cambia.
- Ogni conversazione è salvata **su disco in JSON** ed elencata nella pagina Chat.
- Quando una conversazione supera un tetto, se ne apre **automaticamente una nuova** collegata alla precedente,
  che eredita istruzioni e un breve riassunto.
- I messaggi di seguito ("riprova", "1 parola") usano la domanda precedente per la ricerca.

---

## 3. Formato dei file

### 3.1 Dove

```
<cartella progetto>/chat/<id>.json     chat legate a un progetto
<ROOT>/chat/<id>.json                  chat su "Tutti i progetti attivi"
```

`id` = `AAAAMMGG-HHMMSS-<4 caratteri casuali>` (ordinabile per data, senza collisioni).

### 3.2 Schema di una conversazione

```json
{
  "id": "20261030-130812-k3f9",
  "titolo": "Quiz compravendita e somministrazione",
  "progetto": "ufficio-acquisti",
  "sessione": "",
  "creata": "2026-10-30T13:08:12",
  "aggiornata": "2026-10-30T14:16:40",
  "istruzioni": ["Rispondi con 1 sola parola per ogni spazio vuoto."],
  "continua_da": "20261030-104401-a1b2",
  "continua_in": null,
  "riassunto_precedente": "Quiz sul contratto di compravendita: art. 1490 vizi, 1177 custodia, termine essenziale...",
  "chiusa": false,
  "messaggi": [
    {"ruolo": "utente", "testo": "...", "ora": "2026-10-30T13:08:12"},
    {"ruolo": "assistente", "testo": "...", "ora": "...", "fonti": [], "web": [],
     "segnate": null, "modello": "openai/gpt-oss-120b", "secondi": 1}
  ]
}
```

### 3.3 Esportazione

`GET /api/chat/esporta?progetto=<slug>` restituisce un unico JSON, scaricabile:

```json
{"esportato": "2026-10-30T14:20:00", "progetto": "ufficio-acquisti", "conversazioni": [ { ...schema 3.2... } ]}
```

Con `progetto` vuoto esporta tutte le chat (globali e di ogni progetto).

---

## 4. Istruzioni della chat

### 4.1 Come nascono

Due modi, entrambi scrivono in `istruzioni`:

1. **Esplicite:** campo "Istruzioni per questa chat" (testo libero, un'istruzione per riga) nella barra della chat.
2. **Rilevate:** in `chat.py`, una regex riconosce i vincoli di formato nel messaggio dell'utente e li salva:

```python
VINCOLI = re.compile(
    r"\b(?:(?:in|con|rispondi(?:mi)? (?:in|con))\s+)?(\d+|una|un|due|tre)\s+parol[ae](?:\s+per\s+(?:campo|spazio))?\b"
    r"|\bsolo (?:la|le) parol[ae] mancant[ei]\b|\bsenza spiegazion[ei]\b|\bsolo (?:il )?numero\b",
    re.IGNORECASE)
```

   - Se il messaggio è **solo** il vincolo ("1 parola"), non è una domanda: si applica alla domanda precedente
     (vedi §5) e diventa istruzione.
   - Se c'è anche una domanda ("1 parola: cosa deve giungere al proponente?"), si risponde e il vincolo resta.
   - Un vincolo nuovo dello stesso tipo sostituisce il precedente (non si accumulano "1 parola" e "2 parole").
   - Frasi come "ora rispondi normalmente" o "togli le istruzioni" svuotano `istruzioni`.

La risposta indica il vincolo attivo, con una riga discreta sotto la bolla:
"Istruzione attiva: 1 parola · modifica".

### 4.2 Come arrivano al modello

Nuovo blocco nel prompt `SISTEMA`, prima di `DOCUMENTI`:

```
ISTRUZIONI DELL'UTENTE PER QUESTA CHAT (valgono per ogni risposta finché non le cambia):
{istruzioni}
Se un'istruzione chiede N parole, rispondi con esattamente N parole: niente fonti tra parentesi, niente spiegazioni.
Se la domanda elenca delle opzioni, scegli una di quelle.
```

Con `istruzioni` vuote: `(nessuna)`.

Le istruzioni con vincolo di parole abbassano anche `max_tokens` a 60, per avere risposte più rapide e meno
sforamenti sul limite TPM di Groq.

### 4.3 Modalità quiz

Se la domanda contiene "Completa la frase", spazi vuoti (righe interrotte, `___`, `…`) o un elenco di opzioni,
si aggiunge al prompt: "Domanda da quiz: rispondi solo con le parole mancanti, nell'ordine, separate da ' · '."

---

## 5. Seguiti: "riprova", "1 parola", "non è X"

In `rispondi()`, prima di `scegli_pezzi`:

```python
SEGUITO = re.compile(r"^\s*(riprova\w*|ancora|di nuovo|non è\b.*|no\b.*|sbagliat\w*.*|1 parola.*|una parola.*)\s*$", re.I)
```

- Se il messaggio è un seguito, la **domanda effettiva** è l'ultima domanda dell'utente che non era un seguito.
  `testo_ricerca` usa quella, non "riprova".
- Si raccolgono le **risposte già scartate**: le risposte dell'assistente a quella domanda, più le parole citate
  in "non è X" / "no X". Vanno nel prompt:
  `RISPOSTE GIÀ DATE E SBAGLIATE (non ripeterle): 1510, 1470, 1490`.
- Per un "riprova" si alza `temperature` a 0.7, così la risposta cambia davvero.
- Il messaggio inviato al modello diventa:
  `"<domanda effettiva>\n\n(L'utente chiede di riprovare: la risposta precedente era sbagliata.)"`

---

## 6. Rollover: nuova chat quando la conversazione è troppo lunga

### 6.1 Soglia

```python
MAX_MESSAGGI_CONVERSAZIONE = 40          # 20 scambi
MAX_CARATTERI_CONVERSAZIONE = 30000
```

Si controlla **dopo** aver salvato la risposta: se una delle due soglie è superata, la conversazione si chiude.

### 6.2 Cosa succede

1. Si genera un **riassunto** della conversazione (max 600 caratteri) con lo stesso modello della chat,
   `max_tokens=250`. Contiene argomenti, risposte confermate dall'utente e correzioni.
   Se la chiamata fallisce, il riassunto ripiega sulle ultime 5 domande dell'utente, concatenate.
2. La vecchia conversazione prende `chiusa: true` e `continua_in: <nuovo id>`.
3. Si crea la nuova conversazione con `continua_da`, le stesse `istruzioni`, lo stesso progetto e la stessa sessione,
   e `riassunto_precedente`.
4. La risposta API porta `{"nuova_conversazione": "<id>"}`. Il frontend mostra un separatore:
   "Conversazione lunga: continuo in una nuova chat (istruzioni e riassunto riportati) · apri la precedente".

### 6.3 Nel prompt

Se `riassunto_precedente` c'è, entra nel prompt come blocco `CONVERSAZIONE PRECEDENTE (riassunto):`
(max 600 caratteri). Lo storico che arriva al modello resta di **4 messaggi**: il rollover serve a ordinare e a
tenere le conversazioni leggibili, non ad allargare il contesto.

---

## 7. API (in `src/webapp.py`)

| Metodo | Percorso | Cosa fa |
|---|---|---|
| `GET` | `/api/chat/conversazioni?progetto=&sessione=` | elenco: id, titolo, aggiornata, n. messaggi, chiusa, istruzioni |
| `GET` | `/api/chat/conversazioni/{id}?progetto=` | conversazione completa |
| `POST` | `/api/chat/conversazioni` | nuova conversazione vuota (`progetto`, `sessione`, `istruzioni`) |
| `PATCH` | `/api/chat/conversazioni/{id}` | cambia `titolo` o `istruzioni` |
| `DELETE` | `/api/chat/conversazioni/{id}` | sposta il file nel `cestino` (come per i progetti, mai cancellato) |
| `POST` | `/api/chat` | ora accetta `id_conversazione`; se manca, ne crea una. Lo storico lo legge il server dal file, non lo manda più il browser |
| `GET` | `/api/chat/esporta?progetto=` | JSON con tutte le conversazioni (§3.3) |

Validazione `id`: regex `^\d{8}-\d{6}-[a-z0-9]{4}$`, per evitare path traversal.

Il titolo si genera dalla prima domanda dell'utente (primi 60 caratteri, senza chiamare il modello).

---

## 8. Moduli

### 8.1 Nuovo `src/conversazioni.py`

- `cartella(slug) -> Path`
- `nuova(slug, sessione, istruzioni=()) -> dict`
- `leggi(slug, id) -> dict`
- `salva(conv)`: scrittura atomica (file `.tmp` + `os.replace`), così un crash non corrompe il JSON
- `elenco(slug, sessione="") -> list`
- `aggiungi_messaggi(conv, *messaggi)`
- `da_chiudere(conv) -> bool`
- `rollover(conv, riassunto) -> dict`, che restituisce la nuova
- `esporta(slug="") -> dict`
- `nel_cestino(slug, id)`

### 8.2 `src/chat.py`

- `rispondi(domanda, storico, slug, sessione, istruzioni=(), riassunto_precedente="", scartate=())`
- nuove funzioni: `estrai_vincoli(testo)`, `e_seguito(testo)`, `domanda_effettiva(storico, domanda)`,
  `riassumi_conversazione(messaggi)`
- blocchi nuovi in `SISTEMA`: istruzioni, riassunto precedente, risposte scartate

### 8.3 `web/app.js` (`creaChat`)

- `sessionStorage` sostituito dall'API. In `sessionStorage` resta solo l'id della conversazione aperta per ogni ambito.
- **Elenco conversazioni** a sinistra (o a tendina nella versione compatta): titolo, data, icona di quelle chiuse
  per rollover.
- Campo **Istruzioni** comprimibile nella `chat-barra`, con chip delle istruzioni attive (× per toglierle).
- Il tasto "Nuova" crea una conversazione vuota invece di svuotare lo storico.
- Tasto **Esporta JSON** che scarica `/api/chat/esporta`.
- Separatore di rollover (§6.2) e passaggio automatico alla nuova conversazione.

---

## 9. Ordine di lavoro

1. `conversazioni.py` + API di lettura e scrittura, con `/api/chat` che salva su file (lo storico non dipende più dal browser).
2. Istruzioni: campo, regex dei vincoli, blocco nel prompt.
3. Seguiti ("riprova", "1 parola", "non è X").
4. Rollover con riassunto.
5. Frontend: elenco, istruzioni, esporta.
6. Migrazione: alla prima apertura, lo storico presente in `sessionStorage` diventa una conversazione salvata.

## 10. Verifica

Rifare la sequenza della conversazione del 30/10:

- "1 parola" detto una volta vale per tutte le domande successive.
- "riprova" non ripete la risposta precedente.
- "non è regolari, riprova" non pesca documenti di altri corsi.
- Dopo 40 messaggi si apre una nuova chat con le istruzioni ancora attive.
- `/api/chat/esporta` restituisce un JSON valido con entrambe le conversazioni, collegate da `continua_da` e `continua_in`.
- Chiudere e riaprire il browser non fa perdere niente.
