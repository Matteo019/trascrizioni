# Trascrittore chiamate (Teams & co.)

Trascrive in tempo reale, in italiano, l'audio di una chiamata (clienti, colleghi, corsi: qualsiasi chiamata) e ne genera un riassunto in Markdown.
Continui a sentire la chiamata nelle tue cuffie (anche Bluetooth) mentre viene trascritta.
Tutto si gestisce da una dashboard nel browser, in locale: niente terminale, niente database.

- Trascrizione: Groq Whisper (`whisper-large-v3`, con passaggio automatico a `turbo`), gratuito
- Riassunto e «Chiedi»: modelli NVIDIA gratuiti

## Struttura

```
avvia_dashboard.bat      doppio clic: apre la dashboard (punto di ingresso principale)
terminale/               avvio senza dashboard, da terminale (progetto "Corso Panthera")
  avvia_trascrizione.bat
  avvia_riassunto.bat
.env                     le tue API key e impostazioni (si modificano dalla dashboard, mai su git)
config/                  modelli di contesto.txt e glossario.txt copiati in ogni nuovo progetto
src/                     codice (webapp.py è la dashboard)
web/                     pagine della dashboard
progetti/<progetto>/     output, un progetto per cliente, corso o gruppo di chiamate (mai su git)
  contesto.txt           contesto per l'AI dei riassunti
  glossario.txt          termini tecnici che Whisper deve scrivere giusti
  trascrizioni/          trascrizione_X.txt in scrittura
  riassunti/             "Riassunto <data> - <titolo>.md" e le sue parti
  storico/               appunti e parte già riassunta (segnalibro per riprendere senza perdere nulla)
  da_ritrascrivere/      audio dei blocchi non trascritti, recuperati appena possibile
```

Al primo avvio la vecchia cartella `trascrizioni/` viene spostata da sola in `progetti/corso-panthera/` (progetto «Corso Panthera»)
(se un file è stato toccato negli ultimi 15 minuti lo spostamento aspetta: una sessione in corso non viene spezzata).

## Requisiti (una volta sola)

1. **Python 3.10+** da https://www.python.org/downloads/ (spunta "Add python.exe to PATH").
2. **VB-CABLE** (cavo audio virtuale gratuito) da https://vb-audio.com/Cable/:
   estrai lo zip, tasto destro su `VBCABLE_Setup_x64.exe` → *Esegui come amministratore* → Install.
3. **API key** (si inseriscono in dashboard → Impostazioni):
   - Groq (trascrizione): https://console.groq.com/keys
   - NVIDIA (riassunti e Chiedi): https://build.nvidia.com

## Configurazione audio

Fai arrivare l'audio della chiamata a **CABLE Input**. Due modi:

- **Consigliato:** in Teams → Impostazioni → Dispositivi → *Altoparlante* = `CABLE Input (VB-Audio Virtual Cable)`.
  Solo Teams passa dal cavo, il resto del PC resta sulle tue cuffie.
- **Oppure:** imposta `CABLE Input` come dispositivo di riproduzione predefinito di Windows (tutto l'audio del PC verrà trascritto).

Non serve attivare "Ascolta questo dispositivo" in Windows: è lo script a rimandarti l'audio nelle cuffie
(se lo hai attivato, disattivalo o sentirai l'audio doppio).

## Uso

1. Doppio clic su **`avvia_dashboard.bat`**: al primo avvio installa le dipendenze, poi apre
   http://127.0.0.1:8765 nel browser. Se è già aperta, riapre solo la pagina.
2. **Impostazioni**: chiavi Groq e NVIDIA, cuffie su cui sentire la chiamata, ogni quanti minuti riassumere,
   contesto e glossario di ogni progetto.
3. **Progetti**: la pagina iniziale mostra tutti i progetti. Per crearne uno (un cliente, un corso, il tuo team...)
   scrivi nome e **contesto** (obbligatorio) e, se vuoi, il glossario. Nel progetto apri **Trascrizione** e premi *Avvia*; quando vuoi
   apri **Riassunto** e premi *Avvia*. Il log si vede solo se lo chiedi («Mostra log»).
4. Al centro del progetto c'è il riassunto della sessione scelta, già formattato; puoi passare a
   Trascrizione (divisa in sezioni con orari) o Appunti, scorrere i passaggi **[INCERTO]**, scaricare il PDF
   o copiare il testo formattato.
5. **Ferma** chiude in modo ordinato (il riassunto elabora l'ultimo pezzo); **Chiudi subito** interrompe
   senza perdere testo già scritto.
6. **Chiedi**: domande sulle tue chiamate, con il modello NVIDIA che scegli; risponde solo dai tuoi documenti e cita le fonti.
7. La **barra di ricerca** in alto (tasto `\`) e il **Calendario** lavorano su tutti i progetti; cliccando un giorno del calendario vedi le sue sessioni in colonne per stato. Per spegnere: Impostazioni → *Chiudi dashboard*.

## Note

- L'audio viene tagliato nelle pause (blocchi da 45-60 secondi): ~600 richieste in 9 ore, sotto il limite
  gratuito di Groq (20/minuto, 2000/giorno per modello).
- Contesto e glossario vengono riletti a ogni blocco/giro: puoi modificarli durante la chiamata.
- Una sola trascrizione alla volta (l'audio della chiamata è uno); i riassunti possono girare su più progetti.
- La dashboard scrive il suo log in `dashboard.log`; ogni processo in `progetti/<progetto>/<tipo>.log`.
- Su alcuni PC aziendali Windows blocca la registrazione "classica": lo script usa WDM-KS per leggere CABLE Output.
