"""
Riassunto didattico delle trascrizioni, indipendente da trascrittore.py.

Cartelle (dentro progetti/<slug>/):
  trascrizioni/trascrizione_X.txt   scritta in tempo reale da trascrittore.py
  storico/trascrizione_X.txt  parte della trascrizione già riassunta (fa da segnalibro:
                              alla ripartenza si riprende esattamente da lì, senza perdere nulla);
                              a sessione finita e riassunta, l'originale viene spostato qui
  storico/Appunti AAAA-MM-GG HH.MM.md     appunti dettagliati blocco per blocco (solo aggiunte, mai riscritti)
  riassunti/Parti AAAA-MM-GG HH.MM/       "Parte NN.md" per ogni ~1-1.5 ore + titolo.txt (modificabile)
  riassunti/Riassunto AAAA-MM-GG HH.MM - <titolo>.md   documento finale: indice + tutte le parti

Uso (dalla cartella del progetto):
  python src/riassunto.py               controlla ogni INTERVALLO minuti finché non premi CTRL+C
  python src/riassunto.py --una-volta   elabora quello che c'è ed esce
  --progetto <slug>                     progetto su cui lavorare (predefinito: generale)
"""

import os
import re
import hashlib
import filecmp
import sys
import time
import datetime
from pathlib import Path

from openai import OpenAI

from comune import argomento_progetto, path_progetto, stop_richiesto
import motori

def _intervallo_minuti():
    try:
        valore = float(os.environ.get("RIASSUNTO_INTERVALLO_MINUTI", "").strip().replace(",", "."))
    except ValueError:
        return 10.0
    return valore if valore > 0 else 10.0


INTERVALLO_MINUTI = _intervallo_minuti()
MIN_NUOVI_CARATTERI = 1500          # sotto questa soglia aspetta altro testo
MAX_CARATTERI_PER_BLOCCO = 15000    # backlog lunghi vengono spezzati
# Il riassunto finale è diviso in parti (~1-1.5 ore di appunti ciascuna): ogni chiamata resta ben sotto
# il limite di output (16k token) e le parti chiuse non vengono più rigenerate.
MAX_CARATTERI_PER_PARTE = 24000
TIMEOUT_SECONDI = 300
# Il servizio gratuito NVIDIA a volte rende un modello non disponibile (404): si passa al successivo.
# Con l'AI locale c'è un solo modello, quello scelto nelle Impostazioni.
MODELLI = motori.modelli_testo([m.strip() for m in os.environ.get(
    "NEMOTRON_MODEL",
    # super-120b non è più disponibile sull'account (404); gpt-oss risponde in pochi secondi
    "nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-20b,nvidia/nemotron-3.5-lightning-30b-a3b",
).split(",") if m.strip()])
MOTORE = motori.nome_testo()

PROGETTO_DIR = path_progetto(argomento_progetto())
TRASCRIZIONI_DIR = PROGETTO_DIR / "trascrizioni"
CONTESTO_FILE = PROGETTO_DIR / "contesto.txt"


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

STORICO_DIR = PROGETTO_DIR / "storico"
RIASSUNTI_DIR = PROGETTO_DIR / "riassunti"

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
  ma elencalo alla fine (solo il rumore vero: un passaggio con contenuto tecnico va negli argomenti, mai qui) in "### Rumore e passaggi non pertinenti", una riga per caso:
  "- [orario] trascritto: «...» → probabilmente: «...» (oppure: non interpretabile)".
- NON inventare nulla, NON aggiungere decisioni o action item, niente frasi introduttive o conclusive.

Trascrizione:
{testo}"""

PROMPT_PARTE = """Qui sotto ci sono gli appunti dettagliati di una PARTE della sessione (dalle {inizio} alle {fine}),
scritti blocco per blocco (quindi lo stesso argomento può comparire più volte).
Riorganizzali in un documento Markdown in italiano che inizia ESATTAMENTE così:

**Panoramica:** breve paragrafo (2-4 righe) con gli argomenti trattati in questa parte.

Poi una sezione "### <Argomento>" per ogni argomento, nell'ordine in cui sono stati trattati,
con eventuali sottosezioni "####", elenchi puntati e procedure numerate. Usa **grassetto** per nomi di campi,
menu e parametri. Una tabella solo se aiuta davvero (es. elenco di campi con caratteristiche).
In fondo "### Domande e risposte" se presenti, e per ultima "### Rumore e passaggi non pertinenti"
con TUTTE le righe di rumore degli appunti in ordine di orario (orario, trascritto, interpretazione probabile).
Nel rumore va solo ciò che non ha contenuto tecnico: un passaggio già riportato negli argomenti NON si ripete lì.
Mantieni i marcatori **[INCERTO]** dentro le sezioni.

Regole fondamentali:
- Usa solo titoli di livello ### e ####: niente # o ##, e un solo gruppo di # per titolo (mai "### ### ...").
- Unisci i contenuti dello stesso argomento ed elimina solo le ripetizioni identiche.
- NON perdere nessun dettaglio tecnico presente negli appunti (campi, valori, passaggi, avvertenze, esempi).
- NON inventare nulla, NON aggiungere decisioni o action item.
- Rispondi solo con il documento.

Appunti:
{appunti}"""


def titoli_puliti(testo: str) -> str:
    """'### ### Argomento' -> '### Argomento': alcuni modelli ripetono i # del titolo."""
    return re.sub(r"^(#{1,6})(?:[ \t]+#{1,6})+[ \t]+", r"\1 ", testo, flags=re.M)


class RispostaTroncata(Exception):
    pass


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
                scelta = risposta.choices[0]
                if scelta.finish_reason == "length":
                    # risposta tagliata a max_tokens: salvarla vorrebbe dire perdere la parte finale
                    raise RispostaTroncata(f"risposta troncata a {max_tokens} token")
                testo = scelta.message.content or ""
                testo = re.sub(r"<think>.*?</think>", "", testo, flags=re.S).strip()
                if testo:
                    print(f"           ok con {modello} in {time.time() - inizio:.0f}s")
                    return testo
                print(f"[{MOTORE}] {modello}: risposta vuota")
            except Exception as exc:
                print(f"[{MOTORE}] {modello} non disponibile ({time.time() - inizio:.0f}s): {str(exc)[:150]}")
        time.sleep(30 * (giro + 1))
    raise RuntimeError(f"nessun modello {MOTORE} ha risposto")


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


def ora() -> str:
    return f"{datetime.datetime.now():%H:%M:%S}"


def scrivi_atomico(path: Path, testo: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(testo, encoding="utf-8")
    tmp.replace(path)


def parti_da_appunti(appunti: str) -> list:
    """
    Raggruppa i blocchi degli appunti in parti di al massimo MAX_CARATTERI_PER_PARTE.
    Il raggruppamento è deterministico e i blocchi vengono solo aggiunti in coda,
    quindi una parte chiusa non cambia più e non va rigenerata.
    """
    blocchi_appunti = [b for b in re.split(r"\n(?=## Blocco )", appunti) if b.strip().startswith("## Blocco")]
    parti, corrente = [], []
    for blocco in blocchi_appunti:
        if corrente and sum(map(len, corrente)) + len(blocco) > MAX_CARATTERI_PER_PARTE:
            parti.append(corrente)
            corrente = []
        corrente.append(blocco)
    if corrente:
        parti.append(corrente)
    return parti


def orari_parte(blocchi_parte: list):
    orari = re.findall(r"\[(\d\d:\d\d)(?::\d\d)?", "\n".join(b.splitlines()[0] for b in blocchi_parte))
    return (orari[0], orari[-1]) if orari else ("?", "?")


PROMPT_TITOLO = """Questo è l'indice degli argomenti di una sessione di formazione.
Scrivi un titolo breve e parlante (massimo 8 parole) che descriva la sessione, da usare come nome di file.
Rispondi solo con il titolo, senza virgolette e senza punto finale.

Indice:
{indice}"""


def titolo_sessione(client: OpenAI, cartella_parti: Path, indice: str) -> str:
    """Generato una sola volta e salvato in titolo.txt (modificabile a mano): il nome del file resta stabile."""
    file_titolo = cartella_parti / "titolo.txt"
    if file_titolo.exists() and file_titolo.read_text(encoding="utf-8").strip():
        titolo = file_titolo.read_text(encoding="utf-8").strip()
    else:
        titolo = llm(client, PROMPT_TITOLO.format(indice=indice), 2000).splitlines()[0]
        file_titolo.write_text(titolo + "\n", encoding="utf-8")
    titolo = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", titolo).strip(" .")
    return re.sub(r"\s+", " ", titolo)[:80] or "Sessione"


def etichetta(nome: str) -> str:
    """'20261020_094750' -> '2026-10-20 09.47'"""
    return f"{datetime.datetime.strptime(nome, '%Y%m%d_%H%M%S'):%Y-%m-%d %H.%M}"


def prefisso_file(nome: str) -> str:
    return f"Riassunto {etichetta(nome)}"


def file_appunti(nome: str) -> Path:
    # dati di lavoro: stanno in storico/, in riassunti/ restano solo riassunto e parti
    return STORICO_DIR / f"Appunti {etichetta(nome)}.md"


def cartella_parti_di(nome: str) -> Path:
    return RIASSUNTI_DIR / f"Parti {etichetta(nome)}"


def migra_nomi_vecchi(nome: str):
    """Sposta/rinomina i file creati con i nomi e le posizioni precedenti."""
    for vecchi_appunti in (RIASSUNTI_DIR / f"appunti_{nome}.md", RIASSUNTI_DIR / f"Appunti {etichetta(nome)}.md"):
        if vecchi_appunti.exists() and not file_appunti(nome).exists():
            vecchi_appunti.rename(file_appunti(nome))
    vecchie_parti = RIASSUNTI_DIR / f"parti_{nome}"
    if vecchie_parti.exists() and not cartella_parti_di(nome).exists():
        vecchie_parti.rename(cartella_parti_di(nome))
    cartella = cartella_parti_di(nome)
    if cartella.exists():
        for vecchia in cartella.glob("parte_*.md"):
            nuova = cartella / vecchia.name.replace("parte_", "Parte ")
            if not nuova.exists():
                vecchia.rename(nuova)


def aggiorna_riassunto(client: OpenAI, nome: str):
    """Rigenera solo le parti i cui appunti sono cambiati, poi ricompone il riassunto."""
    appunti = file_appunti(nome)
    cartella_parti = cartella_parti_di(nome)
    if not appunti.exists():
        return
    cartella_parti.mkdir(exist_ok=True)

    parti = parti_da_appunti(appunti.read_text(encoding="utf-8"))
    cambiato = False
    testi_parti = []
    for numero, blocchi_parte in enumerate(parti, start=1):
        sorgente = "\n".join(blocchi_parte)
        impronta = hashlib.sha256(sorgente.encode("utf-8")).hexdigest()[:16]
        file_parte = cartella_parti / f"Parte {numero:02d}.md"
        inizio, fine = orari_parte(blocchi_parte)
        intestazione = f"<!-- appunti: {impronta} -->\n"

        if file_parte.exists() and file_parte.read_text(encoding="utf-8").startswith(intestazione):
            letto = file_parte.read_text(encoding="utf-8")[len(intestazione):]
            corpo = titoli_puliti(letto)
            if corpo != letto:                  # parte salvata con titoli sporchi: si sistema senza rigenerarla
                scrivi_atomico(file_parte, intestazione + corpo)
                cambiato = True
        else:
            print(f"[{ora()}] Riassumo parte {numero} ({inizio}-{fine}) ...")
            corpo = titoli_puliti(llm(client, PROMPT_PARTE.format(inizio=inizio, fine=fine, appunti=sorgente), 16000))
            scrivi_atomico(file_parte, intestazione + corpo + "\n")
            cambiato = True
        testi_parti.append((numero, inizio, fine, corpo.strip()))

    indice = []
    for numero, inizio, fine, corpo in testi_parti:
        indice.append(f"- **Parte {numero} ({inizio}-{fine})**")
        for argomento in re.findall(r"^### (.+)$", corpo, flags=re.M):
            indice.append(f"  - {argomento.strip()}")

    titolo = titolo_sessione(client, cartella_parti, "\n".join(indice))
    prefisso = prefisso_file(nome)
    riassunto = RIASSUNTI_DIR / f"{prefisso} - {titolo}.md"
    # vecchi nomi della stessa sessione (es. titolo cambiato a mano, o formato riassunto_X.md)
    for vecchio in [*RIASSUNTI_DIR.glob(f"{prefisso} - *.md"), RIASSUNTI_DIR / f"riassunto_{nome}.md"]:
        if vecchio.exists() and vecchio != riassunto:
            vecchio.unlink()
            cambiato = True
    if not cambiato and riassunto.exists():
        return

    data = datetime.datetime.strptime(nome, "%Y%m%d_%H%M%S")
    testa = [f"# {titolo}", "", f"*Sessione del {data:%d/%m/%Y}, iniziata alle {data:%H:%M}*", "", "## Indice", ""]
    sezioni = [f"## Parte {numero} ({inizio}-{fine})\n\n{corpo}" for numero, inizio, fine, corpo in testi_parti]
    scrivi_atomico(riassunto, "\n".join(testa + indice) + "\n\n---\n\n" + "\n\n---\n\n".join(sezioni) + "\n")
    print(f"[{ora()}] Fatto: {riassunto}")


def elabora(client: OpenAI, trascrizione: Path, forza: bool):
    nome = trascrizione.stem.removeprefix("trascrizione_")
    storico = STORICO_DIR / trascrizione.name
    migra_nomi_vecchi(nome)
    appunti = file_appunti(nome)

    gia_fatto = storico.stat().st_size if storico.exists() else 0
    with open(trascrizione, "rb") as f:
        f.seek(gia_fatto)
        nuovo = f.read()
    # solo righe complete: l'ultima potrebbe essere ancora in scrittura
    nuovo = nuovo[: nuovo.rfind(b"\n") + 1]
    testo = nuovo.decode("utf-8", errors="replace")

    if testo.strip() and (len(testo) >= MIN_NUOVI_CARATTERI or forza):
        for blocco in blocchi(testo):
            prima, ultima = blocco.strip().splitlines()[0][:10], blocco.strip().splitlines()[-1][:10]
            print(f"[{ora()}] {trascrizione.name}: appunti {prima}-{ultima} ...")
            # i modelli che "ragionano" consumano token prima di rispondere: margine ampio
            note = llm(client, PROMPT_APPUNTI.format(testo=blocco), 12000)
            with open(appunti, "a", encoding="utf-8") as f:
                f.write(f"\n## Blocco {prima} - {ultima}\n\n{note}\n")
            with open(storico, "ab") as f:
                f.write(blocco.encode("utf-8"))

    aggiorna_riassunto(client, nome)
    archivia_se_finita(trascrizione, storico)


def archivia_se_finita(trascrizione: Path, storico: Path):
    """
    Una trascrizione riassunta per intero e non più in scrittura viene spostata in storico/
    (al posto della copia identica). Windows non permette di spostare un file che trascrittore.py
    tiene ancora aperto: in quel caso si riprova al giro successivo.
    """
    try:
        if trascrizione.stat().st_size == 0:
            trascrizione.unlink()
            print(f"[{ora()}] Eliminata trascrizione vuota {trascrizione.name}")
            return
        if storico.exists() and filecmp.cmp(trascrizione, storico, shallow=False):
            os.replace(trascrizione, storico)
            print(f"[{ora()}] Trascrizione completa archiviata in {storico}")
    except PermissionError:
        pass


def ciclo(client: OpenAI, forza: bool):
    for trascrizione in sorted(TRASCRIZIONI_DIR.glob("trascrizione_*.txt")):
        try:
            elabora(client, trascrizione, forza)
        except Exception as exc:
            print(f"[ERRORE] {trascrizione.name}: {exc} (riprovo al prossimo giro, nulla è perso)")


def main():
    try:
        client = motori.client_testo(TIMEOUT_SECONDI)
    except RuntimeError as exc:
        sys.exit(str(exc))
    if motori.testo_locale():
        print(f"[INFO] AI locale: {motori.valore('LLM_LOCALE_MODELLO')} su {motori.valore('LLM_LOCALE_URL')}")

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
            fine_attesa = time.time() + INTERVALLO_MINUTI * 60
            while time.time() < fine_attesa:
                if stop_richiesto():
                    raise KeyboardInterrupt
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Elaboro l'ultimo pezzo prima di uscire...")
        ciclo(client, forza=True)


if __name__ == "__main__":
    main()
