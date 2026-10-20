# Trascrittore chiamate (Teams & co.)

Trascrive in tempo reale, in italiano, l'audio di una chiamata e genera riassunti in Markdown.
Continui a sentire la chiamata nelle tue cuffie (anche Bluetooth) mentre viene trascritta.

- Trascrizione: Groq Whisper (`whisper-large-v3-turbo`), gratuito
- Riassunti ogni ~15 minuti + finale: NVIDIA Nemotron (facoltativo), gratuito

## Requisiti (una volta sola)

1. **Python 3.10+** da https://www.python.org/downloads/ (spunta "Add python.exe to PATH").
2. **VB-CABLE** (cavo audio virtuale gratuito) da https://vb-audio.com/Cable/:
   estrai lo zip, tasto destro su `VBCABLE_Setup_x64.exe` → *Esegui come amministratore* → Install.
3. **API key**:
   - Groq (obbligatoria): https://console.groq.com/keys
   - NVIDIA (facoltativa, per i riassunti): https://build.nvidia.com

## Configurazione audio

Fai arrivare l'audio della chiamata a **CABLE Input**. Due modi:

- **Consigliato:** in Teams → Impostazioni → Dispositivi → *Altoparlante* = `CABLE Input (VB-Audio Virtual Cable)`.
  Solo Teams passa dal cavo, il resto del PC resta sulle tue cuffie.
- **Oppure:** imposta `CABLE Input` come dispositivo di riproduzione predefinito di Windows (tutto l'audio del PC verrà trascritto).

Non serve attivare "Ascolta questo dispositivo" in Windows: è lo script a rimandarti l'audio nelle cuffie
(se lo hai attivato, disattivalo o sentirai l'audio doppio).

## Avvio

Doppio clic su **`avvia.bat`**:
- al primo avvio crea `.env` e lo apre nel Blocco note: incolla le tue chiavi, salva e chiudi;
- installa le dipendenze (solo la prima volta);
- ti chiede su quali cuffie vuoi sentire la chiamata (oppure imposta `MONITOR_DEVICE` in `.env`).

Per fermare: **CTRL+C** nella finestra (viene generato il riassunto finale).

I file finiscono in `trascrizioni/`:
- `trascrizione_AAAAMMGG_HHMMSS.txt` — testo con orari
- `riassunto_AAAAMMGG_HHMMSS.md` — punti chiave, decisioni, action item

## Note

- Blocchi da 20 secondi: ~1600 richieste in 9 ore, sotto il limite gratuito di Groq (2000/giorno).
- Su alcuni PC aziendali Windows blocca la registrazione "classica": lo script usa WDM-KS per leggere CABLE Output e aggirare il problema.
