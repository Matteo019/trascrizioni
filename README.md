# Trascrittore chiamate (Teams & co.)

Trascrive in tempo reale, in italiano, l'audio di una chiamata e ne genera un riassunto didattico in Markdown.
Continui a sentire la chiamata nelle tue cuffie (anche Bluetooth) mentre viene trascritta.

- Trascrizione: Groq Whisper (`whisper-large-v3`, con passaggio automatico a `turbo`), gratuito
- Riassunto: NVIDIA Nemotron (facoltativo), gratuito

## Struttura

```
avvia_trascrizione.bat   doppio clic: avvia la trascrizione
avvia_riassunto.bat      doppio clic: avvia il riassunto (in parallelo, quando vuoi)
.env                     le tue API key (creato al primo avvio, mai su git)
config/
  glossario.txt          termini tecnici che Whisper deve scrivere giusti
  contesto.txt           contesto della sessione per l'AI dei riassunti (cosa è contenuto, cosa è rumore)
src/                     codice
trascrizioni/            output (mai su git)
  trascrizione_X.txt     testo con orari, scritto in tempo reale
  riassunti/             riassunto_X.md (per argomento) e appunti_X.md (blocco per blocco)
  storico/               parte di trascrizione già riassunta: segnalibro per riprendere senza perdere nulla
  da_ritrascrivere/      audio dei blocchi che Groq non ha trascritto, recuperati appena possibile
```

## Requisiti (una volta sola)

1. **Python 3.10+** da https://www.python.org/downloads/ (spunta "Add python.exe to PATH").
2. **VB-CABLE** (cavo audio virtuale gratuito) da https://vb-audio.com/Cable/:
   estrai lo zip, tasto destro su `VBCABLE_Setup_x64.exe` → *Esegui come amministratore* → Install.
3. **API key**:
   - Groq (obbligatoria): https://console.groq.com/keys
   - NVIDIA (per i riassunti): https://build.nvidia.com

## Configurazione audio

Fai arrivare l'audio della chiamata a **CABLE Input**. Due modi:

- **Consigliato:** in Teams → Impostazioni → Dispositivi → *Altoparlante* = `CABLE Input (VB-Audio Virtual Cable)`.
  Solo Teams passa dal cavo, il resto del PC resta sulle tue cuffie.
- **Oppure:** imposta `CABLE Input` come dispositivo di riproduzione predefinito di Windows (tutto l'audio del PC verrà trascritto).

Non serve attivare "Ascolta questo dispositivo" in Windows: è lo script a rimandarti l'audio nelle cuffie
(se lo hai attivato, disattivalo o sentirai l'audio doppio).

## Uso

1. Doppio clic su **`avvia_trascrizione.bat`**. Al primo avvio crea `.env` e lo apre nel Blocco note:
   incolla le chiavi, salva e chiudi. Poi installa le dipendenze e ti chiede su quali cuffie sentire la chiamata
   (oppure imposta `MONITOR_DEVICE` in `.env`).
2. Quando vuoi, doppio clic su **`avvia_riassunto.bat`**: ogni 10 minuti riassume il testo nuovo.
   Puoi fermarlo e riavviarlo quando vuoi: riprende da dove era arrivato.
3. A fine chiamata premi **CTRL+C** in entrambe le finestre (il riassunto elabora l'ultimo pezzo prima di uscire).

## Note

- L'audio viene tagliato nelle pause (blocchi da 45-60 secondi): ~600 richieste in 9 ore, sotto il limite
  gratuito di Groq (20/minuto, 2000/giorno per modello).
- `config/glossario.txt` e `config/contesto.txt` vengono riletti a ogni blocco/giro: puoi modificarli durante la chiamata.
- Su alcuni PC aziendali Windows blocca la registrazione "classica": lo script usa WDM-KS per leggere CABLE Output.
