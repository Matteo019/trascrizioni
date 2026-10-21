# Trascrizioni — lezioni e video (v1.1)

Trascrive in tempo reale **dal microfono del PC** (una lezione dal vivo) o **dall'audio del PC**
(YouTube, qualsiasi video), in italiano o in inglese, e ne genera un riassunto in Markdown.
Tutto si gestisce da una dashboard nel browser, in locale: niente terminale, niente database.
Progetto personale, fornito così com'è (licenza MIT), senza garanzie né supporto.

- Trascrizione: Groq Whisper (`whisper-large-v3`, con passaggio automatico a `turbo`), gratuito, oppure OpenAI o un Whisper locale
- Riassunti e chat: modelli NVIDIA gratuiti, oppure OpenAI, Gemini, Claude, DeepSeek, Mistral, OpenRouter, un'AI locale o qualsiasi endpoint compatibile OpenAI

## Le due modalità

| | Microfono | Audio del PC |
|---|---|---|
| Per | lezione dal vivo, chi parla nella stanza | YouTube, video, qualsiasi audio che il PC riproduce |
| Dispositivo | il microfono scelto (interno, cuffie, USB…) | le casse o le cuffie da cui esce l'audio |
| Audio salvato | sempre, compresso Opus (~14 MB l'ora) | solo se lo chiedi |

L'audio del PC si cattura direttamente (WASAPI loopback): **non serve nessun cavo virtuale**,
guardi il video normalmente e intanto viene trascritto.

**Lingua:** *Italiano* va bene anche quando si parla italiano con molti termini inglesi
(scrivili nel glossario del progetto perché Whisper li scriva giusti). *Inglese* per video o
lezioni interamente in inglese. *Automatica* solo per contenuti in lingue diverse ma non mescolate.
Il riassunto è sempre in italiano.

## Struttura

```
avvia_dashboard.bat      doppio clic: apre la dashboard (punto di ingresso principale)
terminale/               avvio senza dashboard, da terminale (progetto "Generale")
.env                     le tue API key e impostazioni (si modificano dalla dashboard, mai su git)
config/                  contesto e glossario di partenza; contesti/ ha i modelli per lezione, video, riunione
src/                     codice (webapp.py è la dashboard)
web/                     pagine della dashboard
progetti/<progetto>/     output, un progetto per corso o serie di video (mai su git)
  contesto.txt           contesto per l'AI dei riassunti
  glossario.txt          termini tecnici che Whisper deve scrivere giusti
  trascrizioni/          trascrizione_X.txt in scrittura
  riassunti/             "Riassunto <data> - <titolo>.md" e le sue parti
  storico/               appunti e parte già riassunta (segnalibro per riprendere senza perdere nulla)
  audio/                 registrazioni intere delle sessioni da microfono (.ogg)
  da_ritrascrivere/      audio dei blocchi non trascritti, recuperati appena possibile
```

## Requisiti (una volta sola)

1. **Python 3.10+** da https://www.python.org/downloads/ (spunta "Add python.exe to PATH").
2. **API key** (si inseriscono in dashboard → Impostazioni):
   - Groq (trascrizione): https://console.groq.com/keys
   - NVIDIA (riassunti e chat): https://build.nvidia.com
3. Per il microfono: Windows → Impostazioni → Privacy e sicurezza → Microfono → consenti alle app desktop.

## Uso

1. Doppio clic su **`avvia_dashboard.bat`**: al primo avvio installa le dipendenze, poi apre
   http://127.0.0.1:8765 nel browser. Le chiavi (o un altro fornitore) si inseriscono in **Impostazioni**.
2. **Home, niente in corso:** al centro ci sono i tasti **Microfono** e **Audio del PC**. Il pannello che si apre chiede:
   - il **dispositivo** (il predefinito di Windows è già selezionato) e la **lingua**;
   - il **progetto**: uno esistente dalla combobox, oppure *+ Nuovo progetto…* per crearlo al volo
     (il contesto «lezione» o «video» è già compilato).

   *Avvia* apre una **sessione nuova**, con il suo riassunto.
3. **Home, trascrizione in corso:** i due tasti lasciano il posto alla barra della chat. A destra il riquadro
   del log mostra le ultime frasi e gli eventuali errori, con **Ferma tutto** (trascrizione e riassunti, in modo ordinato).
4. **Progetto e sessioni:** il **nome del progetto** lo scegli tu (un corso, una materia, una serie di video);
   il **titolo di ogni sessione** lo genera l'AI dal riassunto e si può correggere in `riassunti/Parti …/titolo.txt`.
5. Nel progetto, il pannello **Trascrizione** ha le stesse scelte (valgono dal prossimo avvio),
   *Continua sessione* / *Nuova sessione* e il log («Mostra log»). Apri **Riassunto** e premi *Avvia* per i riassunti.
6. Al centro del progetto c'è il riassunto della sessione scelta; puoi passare a Trascrizione o Appunti,
   scorrere i passaggi **[INCERTO]**, scaricare il **PDF** o il **Markdown** (*Scarica MD*, comodo da importare
   in Notion) o copiare il testo formattato. «in corso» compare solo sulla sessione che si sta registrando;
   «da riassumere» sulle sessioni finite che il riassunto sta ancora completando.
7. **Ferma** chiude in modo ordinato; **Chiudi subito** interrompe senza perdere testo già scritto.
8. **Archiviare:** nel progetto, menu **⋯** → *Archivia* (i file restano dove sono; *Ripristina* lo riporta).
   Gli archiviati si vedono spuntando **archiviati (N)** nella riga dei progetti in home; non compaiono nella combobox di avvio.
9. **Chat**: domande sulle tue sessioni; risponde solo dai tuoi documenti e cita le fonti.

## Altri fornitori e AI locale (facoltativo)

Di base si usano Groq e NVIDIA, con chiavi gratuite. In **Impostazioni** scegli, separatamente per
trascrizione e per riassunti e chat, un altro fornitore. Per ognuno ci sono **indirizzo (endpoint), API key e modello**:
quelli dei fornitori pronti sono già compilati e si possono correggere.

| Riassunti e chat | Trascrizione |
|---|---|
| NVIDIA (predefinito), OpenAI, Google Gemini, Anthropic Claude, DeepSeek, Mistral, OpenRouter | Groq (predefinito), OpenAI |
| AI locale: Ollama (`http://localhost:11434/v1`), LM Studio (`http://localhost:1234/v1`) | Whisper locale (es. Speaches, `http://localhost:8000/v1`) |
| Personalizzato: qualsiasi indirizzo compatibile OpenAI | |

- Gemini, Claude e DeepSeek non offrono la trascrizione audio: per quella resta Groq, OpenAI o un Whisper locale.
- I nomi dei modelli cambiano spesso: se un fornitore risponde «modello non trovato», correggilo nelle Impostazioni.
- AI locale: niente esce dal PC e la chiave non serve. Per i riassunti lunghi alza il contesto del modello ad
  almeno 16k token (Ollama: `OLLAMA_CONTEXT_LENGTH=16384`). Sul solo processore conviene un Whisper piccolo (`small`, `medium`).
- Le chiavi restano in `.env` sul tuo PC (mai su git, mai rimandate al browser).

## Note

- L'audio viene tagliato nelle pause (blocchi da 45-60 secondi): ~600 richieste in 9 ore, sotto il limite
  gratuito di Groq (20/minuto, 2000/giorno per modello).
- I blocchi a basso volume (docente lontano) vengono amplificati e il silenzio saltato, così Whisper non inventa frasi.
- Se rete o Groq cadono, i blocchi restano in `da_ritrascrivere/` e vengono recuperati da soli.
- Se un dispositivo non si apre (scollegato, privacy del microfono, antivirus o software aziendale che blocca
  la registrazione) il log lo dice chiaramente e la trascrizione riprova ogni 3 secondi.
- Una sola trascrizione alla volta; i riassunti possono girare su più progetti.
- Log: `dashboard.log` per la dashboard, `progetti/<progetto>/<tipo>.log` per ogni processo.
