"""
Riassunto didattico delle trascrizioni, indipendente da trascrittore.py.

Cartelle (dentro trascrizioni/):
  trascrizione_X.txt          scritta in tempo reale da trascrittore.py
  storico/trascrizione_X.txt  parte della trascrizione già riassunta (fa da segnalibro:
                              alla ripartenza si riprende esattamente da lì, senza perdere nulla)
  riassunti/appunti_X.md      appunti dettagliati blocco per blocco (solo aggiunte, mai riscritti)
  riassunti/riassunto_X.md    riassunto finale per argomento, rigenerato dagli appunti

Uso:
  python riassunto.py          controlla ogni INTERVALLO minuti finché non premi CTRL+C
  python riassunto.py --una-volta   elabora quello che c'è ed esce
"""

import os
import re
import sys
import time
import datetime
from pathlib import Path

from openai import OpenAI

BASE_DIR = Path(__file__).parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv(BASE_DIR / ".env")

INTERVALLO_MINUTI = float(os.environ.get("RIASSUNTO_INTERVALLO_MINUTI", "10"))
MIN_NUOVI_CARATTERI = 1500          # sotto questa soglia aspetta altro testo
MAX_CARATTERI_PER_BLOCCO = 15000    # backlog lunghi vengono spezzati
TIMEOUT_SECONDI = 300
# Il servizio gratuito NVIDIA a volte rende un modello non disponibile (404): si passa al successivo.
MODELLI = [m.strip() for m in os.environ.get(
    "NEMOTRON_MODEL",
    "nvidia/nemotron-3-ultra-550b-a55b,nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-super-120b-a12b",
).split(",") if m.strip()]

CONTESTO_FILE = BASE_DIR / "contesto.txt"


def contesto() -> str:
    """Riletto a ogni giro: puoi modificare contesto.txt mentre lo script gira."""
    if CONTESTO_FILE.exists():
        return CONTESTO_FILE.read_text(encoding="utf-8").strip()
    return "Sessione di formazione su un software gestionale."


SYSTEM_PROMPT = """Sei un redattore tecnico esperto di software gestionali ERP e di formazione aziendale.
Trasformi trascrizioni automatiche di corsi in dispense di studio chiare, complete e fedeli.
Scrivi sempre in italiano, in Markdown. Non inventi mai informazioni che non sono nella trascrizione.

CONTESTO DELLA SESSIONE:
{contesto}"""

TRASCRIZIONI_DIR = BASE_DIR / "trascrizioni"
STORICO_DIR = TRASCRIZIONI_DIR / "storico"
RIASSUNTI_DIR = TRASCRIZIONI_DIR / "riassunti"

PROMPT_APPUNTI = """Ti do un pezzo di trascrizione automatica della sessione.
Scrivi APPUNTI DIDATTICI che non perdano nessun contenuto tecnico, tenendo separato ciò che il contesto definisce rumore.

Regole:
- Raggruppa per argomento con titoli "### <Argomento>" (es. "### Anagrafica articoli", "### Parametri di personalizzazione").
- Sotto ogni argomento elenchi puntati con TUTTO ciò che il formatore spiega: concetti, schermate e menu,
  nomi di campi, valori, lunghezze, obblighi, stati, procedure passo-passo (elenco numerato), esempi,
  consigli pratici, avvertenze ("attenzione: ...").
- Domande dei partecipanti e risposte del formatore: sezione "### Domande e risposte" se presenti.
- Correggi gli errori evidenti di trascrizione quando il significato è chiaro (es. nomi del software).
- Passaggi tecnici poco chiari: tienili nella loro sezione con
  "**[INCERTO]** trascritto: «...» → probabilmente: «...»".
- Rumore (frasi di servizio, chiacchiere, frammenti senza senso, allucinazioni): NON metterlo negli argomenti,
  ma elencalo alla fine in "### Rumore e passaggi non pertinenti", una riga per caso:
  "- [orario] trascritto: «...» → probabilmente: «...» (oppure: non interpretabile)".
- NON inventare nulla, NON aggiungere decisioni o action item, niente frasi introduttive o conclusive.

Trascrizione:
{testo}"""

PROMPT_RIASSUNTO = """Qui sotto ci sono gli appunti dettagliati di tutta la sessione finora, scritti blocco per blocco
(quindi lo stesso argomento può comparire più volte). Riorganizzali in UN unico documento Markdown in italiano.

Struttura:
# Riassunto della sessione
## Panoramica
Breve paragrafo (3-5 righe) con gli argomenti trattati.
## Indice
Elenco degli argomenti.
Poi una sezione "## <Argomento>" per ogni argomento, nell'ordine in cui sono stati trattati,
con eventuali sottosezioni "###", elenchi puntati e procedure numerate. Usa **grassetto** per nomi di campi,
menu e parametri. Una tabella solo se aiuta davvero (es. elenco di campi con caratteristiche).
In fondo "## Domande e risposte" se presenti, e per ultima "## Rumore e passaggi non pertinenti"
con TUTTE le righe di rumore degli appunti in ordine di orario (orario, trascritto, interpretazione probabile).
Mantieni i marcatori **[INCERTO]** dentro le sezioni.

Regole fondamentali:
- Unisci i contenuti dello stesso argomento ed elimina solo le ripetizioni identiche.
- NON perdere nessun dettaglio tecnico presente negli appunti (campi, valori, passaggi, avvertenze, esempi).
- NON inventare nulla, NON aggiungere decisioni o action item.
- Rispondi solo con il documento.

Appunti:
{appunti}"""


def llm(client: OpenAI, prompt: str, max_tokens: int) -> str:
    sistema = SYSTEM_PROMPT.format(contesto=contesto())
    for giro in range(3):
        for modello in MODELLI:
            inizio = time.time()
            try:
                risposta = client.chat.completions.create(
                    model=modello,
                    messages=[
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
                testo = risposta.choices[0].message.content or ""
                testo = re.sub(r"<think>.*?</think>", "", testo, flags=re.S).strip()
                if testo:
                    print(f"           ok con {modello} in {time.time() - inizio:.0f}s")
                    return testo
                print(f"[NVIDIA] {modello}: risposta vuota")
            except Exception as exc:
                print(f"[NVIDIA] {modello} non disponibile ({time.time() - inizio:.0f}s): {str(exc)[:150]}")
        time.sleep(30 * (giro + 1))
    raise RuntimeError("nessun modello NVIDIA ha risposto")


def blocchi(testo: str):
    """Spezza su fine riga, senza superare MAX_CARATTERI_PER_BLOCCO."""
    righe, corrente = [], ""
    for riga in testo.splitlines(keepends=True):
        if corrente and len(corrente) + len(riga) > MAX_CARATTERI_PER_BLOCCO:
            righe.append(corrente)
            corrente = ""
        corrente += riga
    if corrente:
        righe.append(corrente)
    return righe


def elabora(client: OpenAI, trascrizione: Path, forza: bool) -> bool:
    nome = trascrizione.stem.removeprefix("trascrizione_")
    storico = STORICO_DIR / trascrizione.name
    appunti = RIASSUNTI_DIR / f"appunti_{nome}.md"
    riassunto = RIASSUNTI_DIR / f"riassunto_{nome}.md"

    gia_fatto = storico.stat().st_size if storico.exists() else 0
    with open(trascrizione, "rb") as f:
        f.seek(gia_fatto)
        nuovo = f.read()
    # solo righe complete: l'ultima potrebbe essere ancora in scrittura
    nuovo = nuovo[: nuovo.rfind(b"\n") + 1]
    testo = nuovo.decode("utf-8", errors="replace")

    if not testo.strip() or (len(testo) < MIN_NUOVI_CARATTERI and not forza):
        return False

    for blocco in blocchi(testo):
        prima, ultima = blocco.strip().splitlines()[0][:10], blocco.strip().splitlines()[-1][:10]
        print(f"[{datetime.datetime.now():%H:%M:%S}] {trascrizione.name}: appunti {prima}-{ultima} ...")
        note = llm(client, PROMPT_APPUNTI.format(testo=blocco), 6000)
        with open(appunti, "a", encoding="utf-8") as f:
            f.write(f"\n## Blocco {prima} - {ultima}\n\n{note}\n")
        with open(storico, "ab") as f:
            f.write(blocco.encode("utf-8"))

    print(f"[{datetime.datetime.now():%H:%M:%S}] Aggiorno {riassunto.name} ...")
    documento = llm(
        client,
        PROMPT_RIASSUNTO.format(appunti=appunti.read_text(encoding="utf-8")),
        16000,
    )
    tmp = riassunto.with_suffix(".tmp")
    tmp.write_text(documento + "\n", encoding="utf-8")
    tmp.replace(riassunto)
    print(f"[{datetime.datetime.now():%H:%M:%S}] Fatto: {riassunto}")
    return True


def ciclo(client: OpenAI, forza: bool):
    for trascrizione in sorted(TRASCRIZIONI_DIR.glob("trascrizione_*.txt")):
        try:
            elabora(client, trascrizione, forza)
        except Exception as exc:
            print(f"[ERRORE] {trascrizione.name}: {exc} (riprovo al prossimo giro, nulla è perso)")


def main():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        sys.exit("Manca NVIDIA_API_KEY nel file .env (https://build.nvidia.com).")
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key,
                    timeout=TIMEOUT_SECONDI, max_retries=0)

    STORICO_DIR.mkdir(parents=True, exist_ok=True)
    RIASSUNTI_DIR.mkdir(parents=True, exist_ok=True)

    if "--una-volta" in sys.argv:
        ciclo(client, forza=True)
        return

    print(f"[INFO] Riassunti in: {RIASSUNTI_DIR}")
    print(f"[INFO] Controllo ogni {INTERVALLO_MINUTI:g} minuti. CTRL+C per fermare (elabora l'ultimo pezzo).\n")
    try:
        while True:
            ciclo(client, forza=False)
            time.sleep(INTERVALLO_MINUTI * 60)
    except KeyboardInterrupt:
        print("\n[INFO] Elaboro l'ultimo pezzo prima di uscire...")
        ciclo(client, forza=True)


if __name__ == "__main__":
    main()
