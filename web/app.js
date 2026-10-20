"use strict";

/* ===================================================================
   Utilità
   =================================================================== */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const vista = $("#vista");

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const VUOTO = '<span class="vuoto-val">—</span>';
const enc = encodeURIComponent;

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new Error("La dashboard non risponde: è ancora aperta? Riavviala con avvia_dashboard.bat.");
  }
  if (!r.ok) {
    let msg = `Richiesta non riuscita (${r.status}).`;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(testo, tipo = "") {
  const el = document.createElement("div");
  el.className = `toast ${tipo}`;
  el.textContent = testo;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), tipo === "errore" ? 8000 : 3500);
}

const pad = n => String(n).padStart(2, "0");
function data(iso) {
  if (!iso) return VUOTO;
  const d = new Date(iso);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}
function dataOra(iso) {
  if (!iso) return VUOTO;
  const d = new Date(iso);
  return `${data(iso)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const intero = n => Number(n || 0).toLocaleString("it-IT");
const percento = (a, b) => b ? `${(100 * a / b).toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%` : "—";

/* '20261020_094750' -> ISO, per mostrare la data di una sessione senza titolo */
function sessioneIso(nome) {
  const m = /^(\d{4})(\d\d)(\d\d)_(\d\d)(\d\d)(\d\d)$/.exec(nome) || [];
  return m.length ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}` : null;
}

/* timer della vista corrente: si fermano a ogni cambio pagina */
let pulizie = [];
function ogni(ms, fn) { const id = setInterval(fn, ms); pulizie.push(() => clearInterval(id)); }

/* salvataggio con Ctrl+S nella vista corrente */
let salvaCorrente = null;
/* modifiche non salvate: chiede conferma prima di lasciare la pagina */
let modificheAperte = () => false;

function memoria(chiave, valore) {
  try {
    if (valore === undefined) return localStorage.getItem(chiave);
    localStorage.setItem(chiave, valore);
  } catch (e) { return null; }
}

/* ===================================================================
   Dialogo, tooltip, menu
   =================================================================== */

function dialogo({ titolo, testo = "", campi = [], conferma = "Conferma", pericolo = false, largo = false }) {
  const dlg = $("#dialogo");
  dlg.classList.toggle("largo", largo);
  const form = $("#dialogo-form");
  form.innerHTML = `
    <h3>${esc(titolo)}</h3>
    ${testo ? `<p>${esc(testo)}</p>` : ""}
    ${campi.map(c => `
      <div class="campo">
        <label for="dlg-${c.id}">${esc(c.label)}</label>
        ${c.multiriga
          ? `<textarea id="dlg-${c.id}" rows="${c.righe || 4}" placeholder="${esc(c.segnaposto || "")}">${esc(c.valore || "")}</textarea>`
          : `<input type="text" id="dlg-${c.id}" value="${esc(c.valore || "")}" placeholder="${esc(c.segnaposto || "")}" autocomplete="off">`}
      </div>`).join("")}
    <div class="azioni">
      <button class="btn" type="button" data-annulla>Annulla</button>
      <button class="btn ${pericolo ? "pericolo" : "primario"}" value="ok" type="submit">${esc(conferma)}</button>
    </div>`;
  return new Promise(resolve => {
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const valori = {};
      campi.forEach(c => valori[c.id] = $(`#dlg-${c.id}`).value);
      resolve(valori);
    };
    dlg.returnValue = "";
    // Annulla non è un submit: Invio in un campo conferma, non annulla
    form.querySelector("[data-annulla]").onclick = () => dlg.close("annulla");
    dlg.showModal();
    const primo = form.querySelector("input, textarea");
    if (primo) { primo.focus(); primo.select?.(); }
  });
}

const tooltip = document.createElement("div");
tooltip.className = "tooltip";
tooltip.hidden = true;
document.body.appendChild(tooltip);
document.addEventListener("mousemove", e => {
  const t = e.target.closest?.("[data-tip]");
  if (!t) { tooltip.hidden = true; return; }
  tooltip.innerHTML = t.dataset.tip;
  tooltip.hidden = false;
  const w = tooltip.offsetWidth, h = tooltip.offsetHeight;
  tooltip.style.left = Math.min(e.clientX + 14, innerWidth - w - 8) + "px";
  tooltip.style.top = Math.max(8, e.clientY - h - 10) + "px";
});

/* menu "⋯": si chiude cliccando fuori o con Esc */
document.addEventListener("click", e => {
  $$(".menu-altro-lista").forEach(m => { if (!m.parentElement.contains(e.target)) m.hidden = true; });
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") $$(".menu-altro-lista").forEach(m => m.hidden = true);
});

/* ===================================================================
   Grafici: torta (singolo progetto) e barre (tutti i progetti)
   =================================================================== */

const FETTE = [
  { k: "chiaro", nome: "Chiaro" },
  { k: "incerto", nome: "[INCERTO]" },
  { k: "rumore", nome: "Rumore" },
];

function torta(q) {
  const tot = q?.totale || 0;
  if (!tot) return `<div class="vuoto piccolo">Nessun riassunto ancora</div>`;
  const R = 70, r = 46, C = 80;
  let ang = -Math.PI / 2;
  const punto = (a, rad) => [C + rad * Math.cos(a), C + rad * Math.sin(a)];
  const fette = FETTE.filter(f => q[f.k] > 0).map(f => {
    const quota = q[f.k] / tot;
    const tip = `<strong>${esc(f.nome)}</strong><br>${intero(q[f.k])} voci · ${percento(q[f.k], tot)}`;
    if (quota >= 0.9999) {
      return `<g data-tip="${esc(tip)}"><circle cx="${C}" cy="${C}" r="${(R + r) / 2}" fill="none" stroke="var(--q-${f.k})" stroke-width="${R - r}"/></g>`;
    }
    const a0 = ang, a1 = ang + quota * 2 * Math.PI;
    ang = a1;
    const grande = a1 - a0 > Math.PI ? 1 : 0;
    const [x0, y0] = punto(a0, R), [x1, y1] = punto(a1, R), [x2, y2] = punto(a1, r), [x3, y3] = punto(a0, r);
    return `<path data-tip="${esc(tip)}" class="fetta" fill="var(--q-${f.k})"
      d="M${x0} ${y0} A${R} ${R} 0 ${grande} 1 ${x1} ${y1} L${x2} ${y2} A${r} ${r} 0 ${grande} 0 ${x3} ${y3}Z"/>`;
  }).join("");
  return `<div class="torta">
      <svg viewBox="0 0 160 160" role="img" aria-label="Chiaro ${percento(q.chiaro, tot)}, incerto ${percento(q.incerto, tot)}, rumore ${percento(q.rumore, tot)}">
        ${fette}
        <text x="80" y="80" text-anchor="middle" class="torta-valore">${percento(q.chiaro, tot)}</text>
        <text x="80" y="98" text-anchor="middle" class="torta-etichetta">chiaro</text>
      </svg>
      <table class="legenda">
        <tbody>${FETTE.map(f => `<tr>
          <td><span class="campione" style="background:var(--q-${f.k})"></span>${esc(f.nome)}</td>
          <td class="num">${intero(q[f.k])}</td>
          <td class="num"><strong>${percento(q[f.k], tot)}</strong></td></tr>`).join("")}</tbody>
      </table>
    </div>`;
}

function barraQualita(q) {
  if (!q?.totale) return VUOTO;
  return `<div class="barra" role="img" aria-label="Chiaro ${percento(q.chiaro, q.totale)}, incerto ${percento(q.incerto, q.totale)}, rumore ${percento(q.rumore, q.totale)}">${
    FETTE.filter(f => q[f.k] > 0).map(f => `<span style="width:${100 * q[f.k] / q.totale}%;background:var(--q-${f.k})"
      data-tip="${esc(`<strong>${esc(f.nome)}</strong><br>${intero(q[f.k])} voci · ${percento(q[f.k], q.totale)}`)}"></span>`).join("")}</div>`;
}

const legendaBarre = () => `<div class="legenda-riga">${FETTE.map(f =>
  `<span><span class="campione" style="background:var(--q-${f.k})"></span>${esc(f.nome)}</span>`).join("")}</div>`;

function barreProgetti(progetti) {
  const righe = progetti.filter(p => p.qualita.totale)
    .sort((a, b) => b.qualita.incerto / b.qualita.totale - a.qualita.incerto / a.qualita.totale);
  const testa = `<div class="card-testa"><h3 class="card-titolo">Affidabilità</h3>${legendaBarre()}</div>`;
  if (!righe.length) return `<div class="card card-riempi">${testa}<div class="vuoto piccolo">Nessun riassunto ancora</div></div>`;
  return `<div class="card card-riempi">${testa}
    <div class="scorre"><table class="barre-progetti">
      <thead><tr><th>Progetto</th><th></th><th class="num">Incerto</th></tr></thead>
      <tbody>${righe.map(p => `<tr class="cliccabile" data-href="#/p/${enc(p.slug)}">
        <td class="nome">${esc(p.nome)}</td>
        <td class="col-barra">${barraQualita(p.qualita)}</td>
        <td class="num"><strong>${percento(p.qualita.incerto, p.qualita.totale)}</strong></td></tr>`).join("")}</tbody>
    </table></div>
  </div>`;
}

function attivaRigheCliccabili(root = vista) {
  $$("tr[data-href]", root).forEach(tr => {
    tr.tabIndex = 0;
    tr.onclick = e => { if (!e.target.closest("a, button")) location.hash = tr.dataset.href; };
    tr.onkeydown = e => { if (e.key === "Enter") location.hash = tr.dataset.href; };
  });
}

/* ===================================================================
   Markdown: da testo con simboli a testo leggibile (per le righe di ricerca)
   =================================================================== */

function rigaMdLeggibile(riga) {
  if (/^\s*\|?[\s:|-]+\|?\s*$/.test(riga) && riga.includes("-")) return { testo: "", titolo: false };
  let t = riga, titolo = false;
  const h = /^\s*#{1,6}\s+(.*)$/.exec(t);
  if (h) { t = h[1]; titolo = true; }
  t = t.replace(/^\s*[-*+]\s+/, "• ")
       .replace(/^\s*\|(.*)\|\s*$/, (_, c) => c.split("|").map(x => x.trim()).filter(Boolean).join("  ·  "))
       .replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1")
       .replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?!\w)/g, "$1$2").replace(/`([^`]+)`/g, "$1")
       .replace(/^>\s?/, "").replace(/<!--.*?-->/g, "");
  return { testo: t, titolo };
}

/* copia il documento già formattato: incollando in Word/Outlook resta formattato, mai Markdown */
async function copiaFormattato(el) {
  const html = el.innerHTML, testo = el.innerText;
  try {
    await navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }),
      "text/plain": new Blob([testo], { type: "text/plain" }),
    })]);
  } catch (e) {
    const r = document.createRange();
    r.selectNodeContents(el);
    const sel = getSelection();
    sel.removeAllRanges(); sel.addRange(r);
    document.execCommand("copy");
    sel.removeAllRanges();
  }
  toast("Copiato: incollalo dove vuoi, resta formattato", "ok");
}

/* ===================================================================
   Router
   =================================================================== */

function percorso() {
  const [path, query = ""] = location.hash.replace(/^#/, "").split("?");
  return { parti: path.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(query) };
}

let hashPrecedente = location.hash;
async function naviga() {
  if (modificheAperte() && !confirm("Ci sono modifiche non salvate. Uscire senza salvarle?")) {
    history.replaceState(null, "", hashPrecedente || "#/");
    return;
  }
  hashPrecedente = location.hash;
  pulizie.forEach(f => f());
  pulizie = [];
  salvaCorrente = null;
  modificheAperte = () => false;

  const { parti, query } = percorso();
  const sezione = parti[0] || "progetti";
  $$(".topnav a").forEach(a => a.classList.toggle("attivo",
    a.dataset.nav === (sezione === "p" ? "progetti" : sezione)));

  vista.innerHTML = '<div class="caricamento">Caricamento…</div>';
  window.scrollTo(0, 0);
  try {
    if (sezione === "p") await vistaProgetto(parti[1], parti[2] === "s" ? parti[3] : "", query.get("tab") || "riassunto");
    else if (sezione === "cerca") await vistaCerca(query);
    else if (sezione === "chat") await vistaChat(query);
    else if (sezione === "calendario") await vistaCalendario(query);
    else if (sezione === "impostazioni") await vistaImpostazioni(query);
    else await vistaProgetti(query);
  } catch (e) {
    vista.innerHTML = `<div class="sezione-pagina"><div class="vuoto"><strong>Non è stato possibile caricare la pagina</strong>${esc(e.message)}
      <div><a class="btn" href="#/">Torna ai progetti</a></div></div></div>`;
  }
  vista.focus({ preventScroll: true });
}
window.addEventListener("hashchange", naviga);
window.addEventListener("beforeunload", e => { if (modificheAperte()) { e.preventDefault(); e.returnValue = ""; } });

const hero = (titolo, extra = "", sinistra = false) =>
  `<section class="hero"><div class="hero-dentro">${extra}<h1 class="${sinistra ? "sinistra" : ""}">${titolo}</h1></div></section>`;

/* ===================================================================
   Testata: processi attivi, ricerca, tema, scorciatoie
   =================================================================== */

const NOMI_TIPO = { trascrizione: "Trascrizione", riassunto: "Riassunto" };

async function aggiornaHeader() {
  try {
    const processi = (await api("/api/processi")).filter(p => p.attivo);
    $("#processi-attivi").innerHTML = processi.map(p => `
      <a class="tag ${p.in_chiusura ? "avviso" : "successo"} vivo" href="#/p/${enc(p.progetto)}"
         title="${esc(NOMI_TIPO[p.tipo])} in corso su ${esc(p.nome_progetto)}">
        <span class="punto"></span>${esc(NOMI_TIPO[p.tipo])} · ${esc(p.nome_progetto)}</a>`).join("");
  } catch (e) { /* la vista mostrerà l'errore */ }
}
setInterval(aggiornaHeader, 3000);

$("#form-ricerca").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#ricerca-globale").value.trim();
  if (q) location.hash = `#/cerca?q=${enc(q)}`;
});

/* icona del tema: sole nel tema chiaro, luna nello scuro */
function etichettaTema() {
  const scuro = document.documentElement.dataset.theme === "dark";
  const testo = scuro ? "Passa al tema chiaro" : "Passa al tema scuro";
  $("#tema").setAttribute("aria-label", testo);
  $("#tema").title = testo;
}
$("#tema").addEventListener("click", () => {
  const scuro = document.documentElement.dataset.theme !== "dark";
  if (scuro) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
  memoria("tema", scuro ? "scuro" : "chiaro");
  etichettaTema();
});
etichettaTema();

document.addEventListener("keydown", e => {
  const inCampo = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if (e.key === "\\" && !inCampo && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    $("#ricerca-globale").focus();
  } else if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === "ArrowRight" || e.key === "ArrowLeft") && !inCampo) {
    // Ctrl+← / Ctrl+→: sezione precedente / successiva della barra in alto
    e.preventDefault();
    const voci = $$(".topnav a");
    const attuale = Math.max(0, voci.findIndex(a => a.classList.contains("attivo")));
    const prossima = voci[(attuale + (e.key === "ArrowRight" ? 1 : -1) + voci.length) % voci.length];
    location.hash = prossima.getAttribute("href");
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && salvaCorrente) {
    e.preventDefault();
    salvaCorrente();
  }
});

/* ===================================================================
   Home: tutti i progetti
   =================================================================== */

async function vistaProgetti(query) {
  const [progetti, eventi] = await Promise.all([api("/api/progetti"), api("/api/calendario")]);
  const archiviati = query.get("archiviati") === "1";
  const visibili = progetti
    .filter(p => archiviati || p.stato !== "archiviato")
    .sort((a, b) => (b.ultima_attivita || "").localeCompare(a.ultima_attivita || ""));
  const recenti = eventi.sort((a, b) => b.inizio.localeCompare(a.inizio)).slice(0, 6);
  const nArchiviati = progetti.filter(p => p.stato === "archiviato").length;

  vista.innerHTML = `
    ${hero("I tuoi progetti")}
    <div class="sotto-hero">
      <div class="pannello riempi">
        <div class="riquadri scorre">
          ${visibili.map(p => {
            const attivi = p.processi.filter(x => x.attivo);
            return `<a class="riquadro" href="#/p/${enc(p.slug)}">
              <div class="riquadro-testa"><span class="riquadro-nome">${esc(p.nome)}</span>
                ${attivi.length ? `<span class="vivo-punto" title="${attivi.map(x => NOMI_TIPO[x.tipo]).join(" e ")} in corso"></span>` : ""}
                ${p.stato === "archiviato" ? '<span class="tag">Archiviato</span>' : ""}</div>
              <div class="riquadro-numeri">
                <div><span class="grande">${intero(p.sessioni)}</span><span class="secondario">sessioni</span></div>
                <div><span class="grande">${p.qualita.totale ? percento(p.qualita.incerto, p.qualita.totale) : "—"}</span><span class="secondario">incerto</span></div>
              </div>
              ${p.qualita.totale ? barraQualita(p.qualita) : '<div class="barra vuota"></div>'}
              <div class="secondario piccolo">${p.ultima_attivita ? dataOra(p.ultima_attivita) : "Mai usato"}</div>
            </a>`;
          }).join("")}
          <button class="riquadro nuovo" id="nuovo"><span class="piu">+</span>Nuovo progetto</button>
        </div>
        ${nArchiviati ? `<div class="pannello-piede" style="justify-content:flex-end">
          <label class="check"><input type="checkbox" id="archiviati" ${archiviati ? "checked" : ""}> Mostra archiviati (${nArchiviati})</label></div>` : ""}
      </div>
      <div class="griglia home-sotto">
        <div class="c6">${barreProgetti(visibili)}</div>
        <div class="c6"><div class="card card-riempi">
          <div class="card-testa"><h3 class="card-titolo">Ultime sessioni</h3></div>
          ${recenti.length ? `<div class="elenco scorre">${recenti.map(s => `
            <a class="elenco-voce" href="#/p/${enc(s.progetto)}/s/${s.nome}">
              <span class="elenco-data">${data(s.inizio)}</span>
              <span class="elenco-titolo">${esc(s.titolo || "Senza titolo")}</span>
              <span class="secondario">${esc(s.nome_progetto)}</span></a>`).join("")}</div>`
            : `<div class="vuoto piccolo">Nessuna sessione</div>`}
        </div></div>
      </div>
    </div>`;

  attivaRigheCliccabili();
  $("#nuovo").onclick = nuovoProgetto;
  $("#archiviati")?.addEventListener("change", e => { location.hash = e.target.checked ? "#/?archiviati=1" : "#/"; });
}

async function nuovoProgetto(valori = {}) {
  const v = await dialogo({
    titolo: "Nuovo progetto", conferma: "Crea progetto", largo: true,
    campi: [
      { id: "nome", label: "Nome", valore: valori.nome, segnaposto: "Es. Cliente Rossi, Corso Panthera, Team interno" },
      { id: "contesto", label: "Contesto (obbligatorio)", multiriga: true, righe: 7, valore: valori.contesto,
        segnaposto: "Di cosa parlano queste chiamate, chi partecipa, cosa conta e cosa è rumore. Serve all'AI per i riassunti." },
      { id: "glossario", label: "Glossario (facoltativo)", multiriga: true, righe: 3, valore: valori.glossario,
        segnaposto: "Nomi, sigle e termini tecnici da scrivere giusti, separati da virgola" },
    ],
  });
  if (!v) return;
  if (!v.nome.trim() || !v.contesto.trim()) {
    toast(!v.nome.trim() ? "Scrivi il nome del progetto" : "Scrivi il contesto: è obbligatorio", "errore");
    return nuovoProgetto(v);
  }
  try {
    const { slug } = await api("/api/progetti", { method: "POST", body: v });
    toast("Progetto creato", "ok");
    location.hash = `#/p/${enc(slug)}`;
  } catch (e) { toast(e.message, "errore"); nuovoProgetto(v); }
}

/* ===================================================================
   Progetto: comandi espandibili + riassunto al centro
   =================================================================== */

const SCHEDE_SESSIONE = [["riassunto", "Riassunto"], ["trascrizione", "Trascrizione"], ["appunti", "Appunti"]];

async function vistaProgetto(slug, nomeSessione, tab) {
  const p = await api(`/api/progetti/${enc(slug)}`);
  const sessioni = p.elenco_sessioni;
  const nome = nomeSessione || sessioni[0]?.nome || "";
  const s = sessioni.find(x => x.nome === nome);
  if (nomeSessione && !s) throw new Error("Sessione non trovata.");
  const link = (t, n = nome) => `#/p/${enc(slug)}${n ? `/s/${n}` : ""}?tab=${t}`;
  if (!SCHEDE_SESSIONE.some(([k]) => k === tab)) tab = "riassunto";
  const diSessione = true;
  const pdf = s && (s.ha_riassunto || s.ha_appunti);

  vista.innerHTML = `
    <section class="hero hero-progetto"><div class="hero-dentro">
      <div class="breadcrumb"><a href="#/">Progetti</a> / ${esc(p.nome)}</div>
      <div class="azioni" style="justify-content:space-between;align-items:center;margin-bottom:16px">
        <h1 class="sinistra" style="margin:0">${esc(p.nome)}${p.stato === "archiviato" ? ' <span class="tag">Archiviato</span>' : ""}</h1>
        <div class="azioni">
          <a class="btn verde" href="#/chat?progetto=${enc(slug)}">Chiedi</a>
          <div class="menu-altro">
            <button class="btn icona" id="altro" aria-label="Altre azioni" title="Altre azioni" aria-haspopup="true">⋯</button>
            <div class="menu-altro-lista" hidden>
              <button id="apri">Apri cartella</button>
              <button id="rinomina">Rinomina</button>
              <button id="contesto">Modifica contesto</button>
              <button id="archivia">${p.stato === "archiviato" ? "Ripristina" : "Archivia"}</button>
            </div>
          </div>
        </div>
      </div>
      <div class="comandi-progetto">
        <div class="processo" id="proc-trascrizione"></div>
        <div class="processo" id="proc-riassunto"></div>
      </div>
    </div></section>

    <div class="sotto-hero">
      <div class="pannello riempi">
        <nav class="tabs">
          ${SCHEDE_SESSIONE.map(([k, n]) => `<a href="${link(k)}" class="${tab === k ? "attivo" : ""}">${n}</a>`).join("")}
          <span class="spazio"></span>
          <a href="#/impostazioni?progetto=${enc(slug)}" class="secondario" title="Il contesto si modifica in Impostazioni">Contesto</a>
        </nav>
        ${diSessione ? `
          <div class="lavoro">
            <aside class="lato">
              <div>
                <div class="etichetta-lato">Sessioni</div>
                ${sessioni.length ? `<div class="sessioni-lista">${sessioni.map(x => `
                  <a class="sessione-voce ${x.nome === nome ? "attiva" : ""}" href="${link(tab, x.nome)}">
                    <span class="t">${esc(x.titolo || "Senza titolo")}</span>
                    <span class="d">${data(x.inizio)}${x.ora_inizio ? ` · ${x.ora_inizio}` : ""}
                      ${x.in_corso ? '<span class="tag attesa vivo"><span class="punto"></span>in corso</span>' : ""}
                      ${x.qualita.totale ? `<span class="num" style="margin-left:auto" title="Incerto">${percento(x.qualita.incerto, x.qualita.totale)}</span>` : ""}</span>
                  </a>`).join("")}</div>`
                : `<div class="secondario piccolo">Nessuna sessione: avvia la trascrizione qui sopra.</div>`}
              </div>
              <div id="incerti-box"></div>
              <div>
                <div class="etichetta-lato">Affidabilità del progetto</div>
                ${torta(p.qualita)}
              </div>
            </aside>
            <div class="centro" id="scheda"></div>
          </div>
          ${s ? `<div class="pannello-piede">
            ${pdf ? `<a class="btn primario grande" href="/api/progetti/${enc(slug)}/sessioni/${nome}/pdf" target="_blank" rel="noopener">Scarica PDF</a>`
                  : `<button class="btn primario grande" disabled title="Serve almeno il primo giro di appunti">Scarica PDF</button>`}
            <button class="btn grande" id="copia" ${tab === "trascrizione" ? "" : ""}>Copia testo</button>
            <a class="btn verde grande" href="#/chat?progetto=${enc(slug)}&sessione=${nome}">Chiedi su questa sessione</a>
          </div>` : ""}`
        : `<div class="pannello-corpo scorre" id="scheda"></div>`}
      </div>
    </div>`;

  $("#altro").onclick = () => { const m = $(".menu-altro-lista"); m.hidden = !m.hidden; };
  $("#apri").onclick = () => api(`/api/progetti/${enc(slug)}/apri-cartella`, { method: "POST" }).catch(e => toast(e.message, "errore"));
  $("#rinomina").onclick = async () => {
    const v = await dialogo({ titolo: "Rinomina progetto", conferma: "Rinomina", campi: [{ id: "nome", label: "Nome", valore: p.nome }] });
    if (!v) return;
    try { await api(`/api/progetti/${enc(slug)}`, { method: "PATCH", body: { nome: v.nome } }); naviga(); }
    catch (e) { toast(e.message, "errore"); }
  };
  $("#archivia").onclick = async () => {
    const archivia = p.stato !== "archiviato";
    if (archivia && !await dialogo({ titolo: "Archiviare il progetto?", testo: "I file restano dove sono.", conferma: "Archivia" })) return;
    try {
      await api(`/api/progetti/${enc(slug)}`, { method: "PATCH", body: { stato: archivia ? "archiviato" : "attivo" } });
      toast(archivia ? "Progetto archiviato" : "Progetto ripristinato", "ok");
      naviga();
    } catch (e) { toast(e.message, "errore"); }
  };
  $("#contesto").onclick = () => { location.hash = `#/impostazioni?progetto=${enc(slug)}`; };
  $("#copia")?.addEventListener("click", () => {
    const el = $("#scheda .documento, #scheda .minuta");
    if (el) copiaFormattato(el); else toast("Niente da copiare in questa sessione");
  });

  pannelloProcesso(slug, "trascrizione", $("#proc-trascrizione"));
  pannelloProcesso(slug, "riassunto", $("#proc-riassunto"));

  if (!s) {
    $("#scheda").innerHTML = `<div class="vuoto"><strong>Ancora nessuna sessione</strong>Apri «Trascrizione» qui sopra e premi Avvia quando inizia la chiamata.</div>`;
    return;
  }
  if (tab === "trascrizione") await schedaMinuta(slug, nome, s);
  else await schedaDocumento(slug, nome, tab, s);

  // sessione in corso: il riassunto si aggiorna da solo quando il riassuntore lo riscrive
  if (s.in_corso && tab !== "trascrizione") {
    let ultima = s.ultima_modifica;
    ogni(30000, async () => {
      try {
        const n = await api(`/api/progetti/${enc(slug)}/sessioni/${enc(nome)}`);
        if (n.ultima_modifica !== ultima) { ultima = n.ultima_modifica; await schedaDocumento(slug, nome, tab, n); }
      } catch (e) {}
    });
  }
}

/* ----- pulsante espandibile Trascrizione / Riassunto, log solo a richiesta ----- */

function classeRiga(r) {
  if (/\[ERRORE|Traceback|Error\b|Exception|non disponibile/.test(r)) return "r-errore";
  if (/\[WARN|\[AUDIO NON TRASCRITTO|Manca /.test(r)) return "r-avviso";
  if (/Fatto:|ok con |archiviata/.test(r)) return "r-ok";
  if (/^\[INFO\]|^\s*$/.test(r)) return "r-info";
  return "";
}

function pannelloProcesso(slug, tipo, box) {
  let offset = -1, stato = null, errori = 0, inCorso = false;
  const aperto = memoria(`aperto-${tipo}`) === "1";
  const logVisibile = memoria(`log-${tipo}`) === "1";
  box.classList.toggle("aperto", aperto);
  box.innerHTML = `
    <button class="processo-testa" aria-expanded="${aperto}">
      <span class="processo-nome">${NOMI_TIPO[tipo]}</span><span class="processo-stato"></span>
      <svg class="freccia" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>
    </button>
    <div class="processo-corpo" ${aperto ? "" : "hidden"}>
      <div class="processo-azioni">
        <span class="bottoni"></span>
        <span class="info-extra"></span>
        <span class="spazio"></span>
        <span class="err"></span>
        <button class="btn piccolo fantasma mostra-log">${logVisibile ? "Nascondi log" : "Mostra log"}</button>
      </div>
      <div class="log" role="log" aria-label="Log ${NOMI_TIPO[tipo]}" ${logVisibile ? "" : "hidden"}></div>
    </div>`;
  const log = $(".log", box);
  const corpo = $(".processo-corpo", box);
  const url = `/api/progetti/${enc(slug)}/processi/${tipo}`;

  $(".processo-testa", box).onclick = () => {
    const ora = corpo.hidden;
    corpo.hidden = !ora;
    box.classList.toggle("aperto", ora);
    $(".processo-testa", box).setAttribute("aria-expanded", ora);
    memoria(`aperto-${tipo}`, ora ? "1" : "0");
  };
  $(".mostra-log", box).onclick = e => {
    log.hidden = !log.hidden;
    e.target.textContent = log.hidden ? "Mostra log" : "Nascondi log";
    memoria(`log-${tipo}`, log.hidden ? "0" : "1");
    if (!log.hidden) log.scrollTop = log.scrollHeight;
  };

  if (tipo === "trascrizione") {
    api("/api/impostazioni").then(i => {
      const disp = i.valori.MONITOR_DEVICE;
      $(".info-extra", box).innerHTML = `Cuffie: ${disp ? esc(disp) : "nessuna"} · <a href="#/impostazioni">cambia</a>`;
    }).catch(() => {});
  }

  function disegnaStato() {
    const s = stato;
    let tag;
    if (s.in_chiusura) tag = '<span class="tag avviso vivo"><span class="punto"></span>In chiusura</span>';
    else if (s.attivo) tag = '<span class="tag successo vivo"><span class="punto"></span>Attivo</span>';
    else if (s.codice_uscita) tag = '<span class="tag pericolo">Errore</span>';
    else tag = '<span class="tag">Fermo</span>';
    $(".processo-stato", box).innerHTML = tag;

    const bottoni = $(".bottoni", box);
    const firma = `${s.attivo}|${s.in_chiusura}`;
    if (bottoni.dataset.firma !== firma) {
      bottoni.dataset.firma = firma;
      if (!s.attivo) bottoni.innerHTML = `<button class="btn primario" data-a="avvia">Avvia</button>`;
      else if (!s.in_chiusura) bottoni.innerHTML = `<button class="btn" data-a="ferma">Ferma</button>`;
      else bottoni.innerHTML = `<button class="btn pericolo" data-a="subito" title="Il pezzo in lavorazione si riprende al prossimo avvio">Chiudi subito</button>`;
    }
    $(".err", box).innerHTML = errori ? `<span class="tag pericolo">${errori} errori</span>` : "";
  }

  $(".bottoni", box).addEventListener("click", async e => {
    const a = e.target.closest("button")?.dataset.a;
    if (!a) return;
    e.target.disabled = true;
    try {
      if (a === "avvia") {
        stato = await api(`${url}/avvia`, { method: "POST" });
        offset = -1; errori = 0; log.innerHTML = "";
      } else if (a === "ferma") {
        stato = await api(`${url}/ferma`, { method: "POST" });
        toast(tipo === "riassunto" ? "Il riassunto elabora l'ultimo pezzo e poi si ferma" : "La trascrizione si ferma a fine blocco");
      } else if (a === "subito") {
        if (!await dialogo({ titolo: `Chiudere subito ${NOMI_TIPO[tipo].toLowerCase()}?`, testo: "Nessun testo già scritto va perso: il pezzo interrotto viene ripreso al prossimo avvio.", conferma: "Chiudi subito", pericolo: true })) { e.target.disabled = false; return; }
        stato = await api(`${url}/ferma?subito=true`, { method: "POST" });
      }
      disegnaStato();
      aggiornaHeader();
      aggiorna();
    } catch (err) {
      toast(err.message, "errore");
      e.target.disabled = false;
    }
  });

  async function aggiorna() {
    if (inCorso) return;
    inCorso = true;
    try {
      const r = await api(`${url}/log?da=${offset}`);
      const inFondo = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      if (offset === -1 || r.offset < offset) { log.innerHTML = ""; errori = 0; }
      if (r.righe.length) {
        const frag = document.createDocumentFragment();
        r.righe.forEach(riga => {
          const div = document.createElement("div");
          div.className = classeRiga(riga);
          if (div.className === "r-errore") errori++;
          div.textContent = riga || " ";
          frag.appendChild(div);
        });
        log.appendChild(frag);
        while (log.childElementCount > 2000) log.firstChild.remove();
        if (inFondo || offset === -1) log.scrollTop = log.scrollHeight;
      }
      if (!log.childElementCount) log.innerHTML = `<div class="r-info">Nessun log</div>`;
      offset = r.offset;
      const eraAttivo = stato?.attivo;
      stato = r;
      disegnaStato();
      if (eraAttivo && !r.attivo) {
        toast(r.codice_uscita ? `${NOMI_TIPO[tipo]} terminato con errore: apri il log` : `${NOMI_TIPO[tipo]} fermato`, r.codice_uscita ? "errore" : "ok");
        aggiornaHeader();
      }
    } catch (e) { /* riprova al prossimo giro */ }
    inCorso = false;
  }
  aggiorna();
  ogni(1500, aggiorna);
}

/* ----- riassunto / appunti: Markdown mostrato già formattato ----- */

async function schedaDocumento(slug, nome, tipo, s) {
  const doc = await api(`/api/progetti/${enc(slug)}/sessioni/${enc(nome)}/documento/${tipo}`);
  const box = $("#scheda");
  if (!box) return;
  if (!doc.html) {
    box.innerHTML = `<div class="vuoto"><strong>${tipo === "riassunto" ? "Riassunto non ancora pronto" : "Appunti non ancora pronti"}</strong>
      Apri «Riassunto» qui sopra e premi Avvia.</div>`;
    $("#incerti-box").innerHTML = "";
    return;
  }
  box.innerHTML = `<article class="documento">${doc.html}</article>`;
  $("#incerti-box").innerHTML = `
    <div class="etichetta-lato">Passaggi [INCERTO]: ${intero(doc.incerti)}</div>
    ${doc.incerti ? `<div class="azioni"><button class="btn piccolo" id="inc-prec" aria-label="Precedente">←</button>
      <button class="btn piccolo" id="inc-succ" aria-label="Successivo">→</button>
      <span class="secondario piccolo num" id="inc-pos"></span></div>` : ""}`;
  const marchi = $$("mark.incerto", box);
  let i = -1;
  const vai = d => {
    marchi[i]?.classList.remove("corrente");
    i = (i + d + marchi.length) % marchi.length;
    marchi[i].classList.add("corrente");
    marchi[i].scrollIntoView({ behavior: "smooth", block: "center" });
    $("#inc-pos").textContent = `${i + 1} di ${marchi.length}`;
  };
  if (marchi.length) { $("#inc-succ").onclick = () => vai(1); $("#inc-prec").onclick = () => vai(-1); }
}

/* ----- trascrizione come minuta ----- */

async function schedaMinuta(slug, nome, s) {
  let pausa = +(memoria("minuta-pausa") || 3), sezione = +(memoria("minuta-sezione") || 15);
  $("#incerti-box").innerHTML = "";
  $("#scheda").innerHTML = `
    <div class="toolbar">
      <label class="check" title="Nuova sezione dopo una pausa di questi minuti">Pausa
        <input type="number" id="pausa" min="0.5" step="0.5" value="${pausa}" style="width:76px"> min</label>
      <label class="check" title="Nuova sezione al massimo ogni questi minuti">Sezione
        <input type="number" id="sezione" min="1" step="1" value="${sezione}" style="width:76px"> min</label>
      <span class="spazio"></span>
      ${s.in_corso ? '<span class="tag attesa vivo"><span class="punto"></span>In diretta</span>' : ""}
    </div>
    <div class="minuta" id="minuta"></div>`;

  async function carica() {
    const m = await api(`/api/progetti/${enc(slug)}/trascrizione/${enc(nome)}?pausa=${pausa}&sezione=${sezione}`);
    const box = $("#minuta");
    if (!box) return;
    if (!m.sezioni.length) { box.innerHTML = `<div class="vuoto">Trascrizione vuota</div>`; return; }
    box.innerHTML = m.sezioni.map(sz => `
      <section class="sezione">
        <div class="sezione-testa"><span class="ore">${sz.inizio} — ${sz.fine}</span></div>
        ${sz.paragrafi.map(pg => `
          <div class="paragrafo ${pg.tipo}">
            <div class="ora">${pg.ora}</div>
            <div>${pg.tipo === "recuperato" ? '<span class="tag info">Recuperato</span>' : ""}
              ${pg.tipo === "mancante" ? '<span class="tag pericolo">Audio non trascritto</span>' : ""}
              <p>${esc(pg.testo)}</p></div>
          </div>`).join("")}
      </section>`).join("");
  }
  const ricarica = () => {
    pausa = Math.max(0.5, +$("#pausa").value || 3);
    sezione = Math.max(1, +$("#sezione").value || 15);
    memoria("minuta-pausa", pausa);
    memoria("minuta-sezione", sezione);
    carica();
  };
  $("#pausa").addEventListener("change", ricarica);
  $("#sezione").addEventListener("change", ricarica);
  await carica();
  if (s.in_corso) ogni(20000, () => carica().catch(() => {}));
}

/* ----- contesto e glossario ----- */

async function schedaContesto(slug, box) {
  const url = t => `/api/progetti/${enc(slug)}/config/${t}`;
  const [contesto, glossario] = await Promise.all([api(url("contesto")), api(url("glossario"))]);
  const file = {
    contesto: { titolo: "Contesto", testo: contesto.testo, aiuto: "per i riassunti" },
    glossario: { titolo: "Glossario", testo: glossario.testo, aiuto: "per la trascrizione" },
  };
  box.innerHTML = `
    <div class="griglia contesto-griglia">${Object.entries(file).map(([k, f]) => `
      <div class="c6">
        <div class="card-testa"><strong>${f.titolo}</strong><span class="secondario piccolo">${f.aiuto}</span>
          <span class="sporco" id="sporco-${k}"></span>
          <div class="azioni"><button class="btn piccolo primario" id="salva-${k}">Salva</button></div></div>
        <textarea id="testo-${k}" spellcheck="false" aria-label="${f.titolo}">${esc(f.testo)}</textarea>
      </div>`).join("")}
    </div>`;

  const salvato = { contesto: contesto.testo, glossario: glossario.testo };
  const sporco = k => $(`#testo-${k}`).value !== salvato[k];
  const segna = k => $(`#sporco-${k}`).textContent = sporco(k) ? "Non salvato" : "";
  async function salva(k) {
    try {
      const testo = $(`#testo-${k}`).value;
      await api(url(k), { method: "PUT", body: { testo } });
      salvato[k] = testo;
      segna(k);
      toast(`${file[k].titolo} salvato`, "ok");
    } catch (e) { toast(e.message, "errore"); }
  }
  for (const k of Object.keys(file)) {
    $(`#testo-${k}`).addEventListener("input", () => segna(k));
    $(`#salva-${k}`).onclick = () => salva(k);
  }
  return { salva: () => Object.keys(file).filter(sporco).forEach(salva), sporco: () => Object.keys(file).some(sporco) };
}

/* ===================================================================
   Cerca
   =================================================================== */

const NOMI_FILE = { trascrizione: "Trascrizione", appunti: "Appunti", riassunto: "Riassunto" };

async function vistaCerca(query) {
  const progetti = await api("/api/progetti");
  const q = query.get("q") || "";
  const prog = query.get("progetto") || "";
  const tipi = (query.get("tipi") || "trascrizione,appunti,riassunto").split(",");
  $("#ricerca-globale").value = q;

  // si cerca dalla barra in alto: qui solo i risultati, con i filtri compatti
  vista.innerHTML = `
    ${hero(q ? `Risultati per «${esc(q)}»` : "Cerca dalla barra in alto")}
    <div class="sotto-hero">
      <div class="pannello riempi">
        <div class="chat-barra" id="filtri-cerca">
          <select id="progetto" aria-label="Progetto">
            <option value="">Tutti i progetti</option>
            ${progetti.map(p => `<option value="${esc(p.slug)}" ${p.slug === prog ? "selected" : ""}>${esc(p.nome)}</option>`).join("")}
          </select>
          ${Object.entries(NOMI_FILE).map(([k, n]) => `<label class="check"><input type="checkbox" name="tipo" value="${k}" ${tipi.includes(k) ? "checked" : ""}> ${n}</label>`).join("")}
        </div>
        <div id="risultati" class="scorre"></div>
      </div>
    </div>`;

  // cambiare un filtro rifà subito la ricerca
  $("#filtri-cerca").onchange = () => {
    const p = new URLSearchParams({ q });
    if ($("#progetto").value) p.set("progetto", $("#progetto").value);
    const t = $$("input[name=tipo]:checked").map(x => x.value);
    if (t.length < 3) p.set("tipi", t.join(","));
    location.hash = `#/cerca?${p}`;
  };
  if (!q) {
    $("#ricerca-globale").focus();
    $("#risultati").innerHTML = `<div class="vuoto piccolo">Scrivi nella barra in alto e premi Invio. Più parole = righe che le contengono tutte; "tra virgolette" = frase esatta.</div>`;
    return;
  }

  const r = await api(`/api/cerca?${new URLSearchParams({ q, progetto: prog, tipi: tipi.join(",") })}`);
  const regex = new RegExp(`(${r.termini.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const riga = (testo, md, classe) => {
    const { testo: t, titolo } = md ? rigaMdLeggibile(testo) : { testo, titolo: false };
    if (!t.trim()) return "";
    return `<div class="${classe}${titolo ? " titolo-md" : ""}">${esc(t).replace(regex, '<mark class="trovato">$1</mark>')}</div>`;
  };

  $("#risultati").innerHTML = !r.risultati.length
    ? `<div class="vuoto">Nessun risultato per «${esc(q)}»</div>`
    : `<p class="secondario piccolo conteggio">${intero(r.totale)} righe in ${intero(r.risultati.length)} file${r.troncato ? " (solo le prime)" : ""}</p>
      <div>${r.risultati.map(f => {
        const md = f.tipo !== "trascrizione";
        return `<div class="risultato">
          <div class="risultato-testa">
            <span class="tag">${NOMI_FILE[f.tipo]}</span>
            <a href="#/p/${enc(f.progetto)}/s/${f.sessione}?tab=${f.tipo}"><strong>${esc(f.file.replace(/\.(md|txt)$/, ""))}</strong></a>
            <span class="secondario">${esc(f.nome_progetto)}</span>
            <span class="secondario piccolo" style="margin-left:auto">${intero(f.occorrenze.length + f.altre)} righe</span>
          </div>
          ${f.occorrenze.map(o => `
            <div class="occorrenza"><div class="nr">${o.riga}</div><div>
              ${o.prima.map(t => riga(t, md, "ctx")).join("")}
              ${riga(o.testo, md, "hit")}
              ${o.dopo.map(t => riga(t, md, "ctx")).join("")}
            </div></div>`).join("")}
          ${f.altre ? `<div class="secondario piccolo" style="padding-top:8px">e altre ${intero(f.altre)} righe</div>` : ""}
        </div>`;
      }).join("")}</div>`;
}

/* ===================================================================
   Chiedi
   =================================================================== */

const ESEMPI_CHAT = [
  "Quali sono gli argomenti principali trattati?",
  "Cosa è stato chiesto o promesso?",
  "Riassumi i passaggi segnati come incerti",
];

async function vistaChat(query) {
  const [progetti, catalogo] = await Promise.all([api("/api/progetti"), api("/api/chat/modelli")]);
  let modello = memoria("chat-modello") || catalogo.predefinito;
  if (!catalogo.modelli.some(m => m.id === modello)) modello = catalogo.predefinito;
  const prog = query.get("progetto") || "";
  const sess = query.get("sessione") || "";
  const progetto = prog ? await api(`/api/progetti/${enc(prog)}`) : null;
  const chiave = `chat:${prog}:${sess}`;
  let storico = [];
  try { storico = JSON.parse(sessionStorage.getItem(chiave) || "[]"); } catch (e) {}
  const memorizza = () => { try { sessionStorage.setItem(chiave, JSON.stringify(storico)); } catch (e) {} };
  const linkAmbito = (p, s) => `#/chat?${new URLSearchParams({ ...(p ? { progetto: p } : {}), ...(s ? { sessione: s } : {}) })}`;

  vista.innerHTML = `
    ${hero("Chiedi ai tuoi progetti")}
    <div class="sotto-hero">
      <div class="pannello riempi">
        <div class="chat-barra">
          <select id="ambito-progetto" aria-label="Progetto">
            <option value="">Tutti i progetti attivi</option>
            ${progetti.map(p => `<option value="${esc(p.slug)}" ${p.slug === prog ? "selected" : ""}>${esc(p.nome)}</option>`).join("")}
          </select>
          <select id="ambito-sessione" aria-label="Sessione" ${progetto ? "" : "disabled"}>
            <option value="">${progetto ? "Tutte le sessioni" : "Tutte le sessioni"}</option>
            ${(progetto?.elenco_sessioni || []).map(s => `<option value="${s.nome}" ${s.nome === sess ? "selected" : ""}>${data(s.inizio)} — ${esc(s.titolo || "Senza titolo")}</option>`).join("")}
          </select>
          <span class="spazio"></span>
          <label class="check" for="modello">Modello
            <select id="modello">
              ${catalogo.modelli.map(m => `<option value="${esc(m.id)}" ${m.id === modello ? "selected" : ""}>${esc(m.nome)} · ${esc(m.autore)}</option>`).join("")}
            </select></label>
          <button class="btn fantasma" id="nuova">Nuova conversazione</button>
        </div>
        <div class="modello-info" id="modello-info"></div>
        <div class="messaggi scorre" id="messaggi" aria-live="polite"></div>
        <div class="pannello-piede">
          <form class="composer" id="composer">
            <textarea id="domanda" rows="2" placeholder="Fai una domanda su chiamate, riunioni e corsi" aria-label="Domanda"></textarea>
            <button class="btn primario grande" id="invia">Invia</button>
          </form>
        </div>
      </div>
    </div>`;

  const box = $("#messaggi");
  function fontiHtml(fonti) {
    if (!fonti?.length) return "";
    return `<div class="fonti"><strong>Fonti</strong>${fonti.map((f, i) =>
      `<a href="#/p/${enc(f.progetto)}/s/${f.sessione}?tab=${f.tipo}">[${i + 1}] ${esc(f.nome_progetto)} · ${esc(f.titolo || data(sessioneIso(f.sessione)))}</a>`).join("")}</div>`;
  }
  function disegna(attesa = false) {
    if (!storico.length && !attesa) {
      box.innerHTML = `<div class="esempi">${ESEMPI_CHAT.map(e => `<button class="btn verde" type="button" data-esempio="${esc(e)}">${esc(e)}</button>`).join("")}</div>`;
      $$("[data-esempio]", box).forEach(b => b.onclick = () => { $("#domanda").value = b.dataset.esempio; invia(); });
      return;
    }
    box.innerHTML = storico.map(m => m.ruolo === "utente"
      ? `<div class="msg utente"><div class="bolla">${esc(m.testo)}</div></div>`
      : `<div class="msg ${m.errore ? "errore" : "assistente"}"><div class="bolla documento">${m.errore ? esc(m.testo) : m.html}${fontiHtml(m.fonti)}${m.modello ? `<div class="secondario piccolo modello-firma">${esc(m.modello)} · ${m.secondi}s</div>` : ""}</div></div>`
    ).join("") + (attesa ? `<div class="msg assistente"><div class="bolla secondario">Sto cercando…</div></div>` : "");
    box.scrollTop = box.scrollHeight;
  }

  let occupato = false;
  async function invia() {
    const domanda = $("#domanda").value.trim();
    if (!domanda || occupato) return;
    occupato = true;
    $("#invia").disabled = true;
    const precedente = storico.filter(m => !m.errore).map(m => ({ ruolo: m.ruolo, testo: m.testo }));
    storico.push({ ruolo: "utente", testo: domanda });
    $("#domanda").value = "";
    disegna(true);
    try {
      const r = await api("/api/chat", { method: "POST", body: { domanda, storico: precedente, progetto: prog, sessione: sess, modello } });
      storico.push({ ruolo: "assistente", testo: r.risposta, html: r.html, fonti: r.fonti, modello: r.nome_modello, secondi: r.secondi });
    } catch (e) {
      storico.push({ ruolo: "assistente", testo: e.message, errore: true });
    }
    memorizza();
    occupato = false;
    if (!document.body.contains(box)) return;     // nel frattempo si è cambiata pagina
    $("#invia").disabled = false;
    disegna();
    $("#domanda").focus();
  }

  $("#composer").onsubmit = e => { e.preventDefault(); invia(); };
  $("#domanda").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); invia(); }
  });
  const descriviModello = () => {
    const m = catalogo.modelli.find(x => x.id === modello);
    $("#modello-info").innerHTML = `${m.punti.map(p => `<span class="tag successo">${esc(p)}</span>`).join("")} <span>${esc(m.descrizione)}</span>`;
  };
  $("#modello").onchange = e => { modello = e.target.value; memoria("chat-modello", modello); descriviModello(); };
  descriviModello();
  $("#nuova").onclick = () => { storico = []; memorizza(); disegna(); $("#domanda").focus(); };
  $("#ambito-progetto").onchange = e => location.hash = linkAmbito(e.target.value, "");
  $("#ambito-sessione").onchange = e => location.hash = linkAmbito(prog, e.target.value);
  disegna();
  $("#domanda").focus();
}

/* ===================================================================
   Calendario
   =================================================================== */

const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];

async function vistaCalendario(query) {
  const eventi = await api("/api/calendario");
  const oggi = new Date();
  const [anno, mese] = (query.get("mese") || `${oggi.getFullYear()}-${oggi.getMonth() + 1}`).split("-").map(Number);
  const prog = query.get("progetto") || "";
  const progetti = [...new Map(eventi.map(e => [e.progetto, e.nome_progetto])).entries()];
  const filtrati = eventi.filter(e => !prog || e.progetto === prog);
  const chiave = d => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const perGiorno = {};
  filtrati.forEach(e => (perGiorno[chiave(new Date(e.inizio))] ||= []).push(e));
  Object.values(perGiorno).forEach(l => l.sort((a, b) => a.inizio.localeCompare(b.inizio)));

  const primo = new Date(anno, mese - 1, 1);
  const inizio = new Date(primo);
  inizio.setDate(1 - ((primo.getDay() + 6) % 7));       // lunedì
  const giorni = Array.from({ length: 42 }, (_, i) => { const d = new Date(inizio); d.setDate(inizio.getDate() + i); return d; });
  const sposta = n => { const d = new Date(anno, mese - 1 + n, 1); return `${d.getFullYear()}-${d.getMonth() + 1}`; };
  const link = m => `#/calendario?${new URLSearchParams({ mese: m, ...(prog ? { progetto: prog } : {}) })}`;
  const evento = e => `<a class="evento" href="#/p/${enc(e.progetto)}/s/${e.nome}"
      data-tip="${esc(`<strong>${esc(e.titolo || "Senza titolo")}</strong><br>${esc(e.nome_progetto)} · ${e.ora_inizio || ""}–${e.ora_fine || ""}`)}">
      <span class="h">${e.ora_inizio || ""}</span>${esc(e.titolo || e.nome_progetto)}</a>`;
  const delMese = filtrati.filter(e => { const d = new Date(e.inizio); return d.getFullYear() === anno && d.getMonth() === mese - 1; })
    .sort((a, b) => a.inizio.localeCompare(b.inizio));
  const isoGiorno = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const linkGiorno = d => `#/calendario?${new URLSearchParams({ mese: `${d.getFullYear()}-${d.getMonth() + 1}`, giorno: isoGiorno(d), ...(prog ? { progetto: prog } : {}) })}`;

  if (query.get("giorno")) return vistaGiorno(query.get("giorno"));

  vista.innerHTML = `
    ${hero("Calendario")}
    <div class="sotto-hero">
      <div class="pannello riempi">
        <div class="chat-barra">
          <a class="btn icona" href="${link(sposta(-1))}" aria-label="Mese precedente" title="Mese precedente">←</a>
          <span class="mese">${MESI[mese - 1]} ${anno}</span>
          <a class="btn icona" href="${link(sposta(1))}" aria-label="Mese successivo" title="Mese successivo">→</a>
          <a class="btn piccolo verde" href="${link(`${oggi.getFullYear()}-${oggi.getMonth() + 1}`)}">Oggi</a>
          <span class="spazio"></span>
          <select id="cal-progetto" aria-label="Progetto">
            <option value="">Tutti i progetti</option>
            ${progetti.map(([s, n]) => `<option value="${esc(s)}" ${s === prog ? "selected" : ""}>${esc(n)}</option>`).join("")}
          </select>
        </div>
        <div class="calendario">
          ${["lun", "mar", "mer", "gio", "ven", "sab", "dom"].map(g => `<div class="gs">${g}</div>`).join("")}
          ${giorni.map(d => `<div class="giorno ${d.getMonth() !== mese - 1 ? "fuori" : ""} ${chiave(d) === chiave(oggi) ? "oggi" : ""}"
              data-href="${linkGiorno(d)}" tabindex="0" role="button" aria-label="Apri il ${d.getDate()} ${MESI[d.getMonth()]}: ${(perGiorno[chiave(d)] || []).length} sessioni">
            <div class="n">${d.getDate()}</div>${(perGiorno[chiave(d)] || []).map(evento).join("")}</div>`).join("")}
        </div>
        <div class="cal-lista scorre">${delMese.length ? delMese.map(e => `<div style="margin-bottom:12px">
            <div class="secondario piccolo">${data(e.inizio)}</div>${evento(e)}</div>`).join("")
          : `<div class="vuoto">Nessuna sessione</div>`}</div>
      </div>
    </div>`;

  $("#cal-progetto").onchange = e => {
    location.hash = `#/calendario?${new URLSearchParams({ mese: `${anno}-${mese}`, ...(e.target.value ? { progetto: e.target.value } : {}) })}`;
  };
  // clic su un giorno (non su una sessione): kanban di quel giorno
  $$(".giorno[data-href]").forEach(g => {
    g.onclick = e => { if (!e.target.closest("a")) location.hash = g.dataset.href; };
    g.onkeydown = e => { if (e.key === "Enter" && !e.target.closest("a")) location.hash = g.dataset.href; };
  });

  /* ----- un giorno: kanban delle sessioni per stato ----- */
  function vistaGiorno(giorno) {
    const d = new Date(`${giorno}T00:00:00`);
    const sessioni = (perGiorno[chiave(d)] || []);
    const spostaGiorno = n => { const x = new Date(d); x.setDate(d.getDate() + n); return linkGiorno(x); };
    const colonne = [
      ["In corso", sessioni.filter(x => x.in_corso)],
      ["Da riassumere", sessioni.filter(x => !x.in_corso && !x.ha_riassunto)],
      ["Riassunte", sessioni.filter(x => !x.in_corso && x.ha_riassunto)],
    ];
    const nomeGiorno = d.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    vista.innerHTML = `
      ${hero("Calendario")}
      <div class="sotto-hero">
        <div class="pannello riempi">
          <div class="chat-barra">
            <a class="btn" href="${link(`${d.getFullYear()}-${d.getMonth() + 1}`)}">← Mese</a>
            <a class="btn icona" href="${spostaGiorno(-1)}" aria-label="Giorno precedente" title="Giorno precedente">←</a>
            <span class="mese">${nomeGiorno}</span>
            <a class="btn icona" href="${spostaGiorno(1)}" aria-label="Giorno successivo" title="Giorno successivo">→</a>
            <a class="btn piccolo verde" href="${linkGiorno(oggi)}">Oggi</a>
            <span class="spazio"></span>
            <select id="cal-progetto" aria-label="Progetto">
              <option value="">Tutti i progetti</option>
              ${progetti.map(([s, n]) => `<option value="${esc(s)}" ${s === prog ? "selected" : ""}>${esc(n)}</option>`).join("")}
            </select>
          </div>
          <div class="pannello-corpo scorre">
            ${sessioni.length ? `<div class="kanban">${colonne.map(([nome, lista]) => `
              <section class="colonna" aria-label="${nome}">
                <div class="colonna-testa">${nome}<span class="secondario num">${lista.length}</span></div>
                ${lista.map(x => `<a class="kcard kcard-sessione" href="#/p/${enc(x.progetto)}/s/${x.nome}">
                  <span class="secondario piccolo">${x.ora_inizio || ""}${x.ora_fine ? `–${x.ora_fine}` : ""} · ${esc(x.nome_progetto)}</span>
                  <span class="testo">${esc(x.titolo || "Senza titolo")}</span>
                  ${x.qualita.totale ? barraQualita(x.qualita) : ""}
                  <span class="secondario piccolo">${x.qualita.totale ? `${percento(x.qualita.incerto, x.qualita.totale)} incerto` : `${intero(x.blocchi)} blocchi trascritti`}</span>
                </a>`).join("")}
              </section>`).join("")}</div>`
            : `<div class="vuoto"><strong>Nessuna sessione</strong>${nomeGiorno}</div>`}
          </div>
        </div>
      </div>`;
    $("#cal-progetto").onchange = e => {
      location.hash = `#/calendario?${new URLSearchParams({ mese: `${d.getFullYear()}-${d.getMonth() + 1}`, giorno, ...(e.target.value ? { progetto: e.target.value } : {}) })}`;
    };
  }
}

/* ===================================================================
   Impostazioni
   =================================================================== */

async function vistaImpostazioni(query) {
  const [i, progetti] = await Promise.all([api("/api/impostazioni"), api("/api/progetti")]);
  const progScelto = progetti.some(p => p.slug === query.get("progetto")) ? query.get("progetto") : (progetti[0]?.slug || "");
  const disp = i.valori.MONITOR_DEVICE;
  const opzioni = [...i.dispositivi];
  if (disp && !opzioni.some(d => d === disp)) opzioni.unshift(disp);
  const chiave = (k, nome, link) => `
    <div class="campo">
      <label for="${k}">${nome} ${i.chiavi[k] ? '<span class="tag successo">Impostata</span>' : '<span class="tag pericolo">Mancante</span>'}</label>
      <input type="password" id="${k}" autocomplete="off" placeholder="${i.chiavi[k] ? "••••••••  vuoto = non cambiare" : "Incolla la chiave"}">
      <div class="aiuto"><a href="${link}" target="_blank" rel="noopener">${link.replace("https://", "")}</a></div>
    </div>`;

  vista.innerHTML = `
    ${hero("Impostazioni")}
    <div class="sotto-hero">
      <div class="pannello riempi-max">
        <div class="pannello-corpo griglia scorre">
          <div class="c6">
            <h3 class="card-titolo" style="margin-bottom:16px">Chiavi</h3>
            ${chiave("GROQ_API_KEY", "Groq — trascrizione", "https://console.groq.com/keys")}
            ${chiave("NVIDIA_API_KEY", "NVIDIA — riassunti e chat", "https://build.nvidia.com")}
          </div>
          <div class="c6">
            <h3 class="card-titolo" style="margin-bottom:16px">Audio e riassunti</h3>
            <div class="campo">
              <label for="MONITOR_DEVICE">Cuffie</label>
              <div class="campo-riga">
                <select id="MONITOR_DEVICE">
                  <option value="">Nessuno (non sentire la chiamata)</option>
                  ${opzioni.map(d => `<option ${d === disp ? "selected" : ""}>${esc(d)}</option>`).join("")}
                </select>
                <button class="btn" id="ricarica-disp" title="Rileggi i dispositivi collegati">Aggiorna</button>
              </div>
              ${i.errore_audio ? `<div class="aiuto" style="color:var(--danger-text)">Non riesco a leggere i dispositivi audio: ${esc(i.errore_audio)}</div>` : ""}
              ${disp && !i.dispositivi.includes(disp) ? `<div class="aiuto" style="color:var(--warning-text)">«${esc(disp)}» ora non è collegato.</div>` : ""}
            </div>
            <div class="campo">
              <label for="RIASSUNTO_INTERVALLO_MINUTI">Riassunto ogni (minuti)</label>
              <input type="text" inputmode="decimal" id="RIASSUNTO_INTERVALLO_MINUTI" value="${esc(i.valori.RIASSUNTO_INTERVALLO_MINUTI)}" placeholder="10" style="max-width:120px">
            </div>
          </div>
          <div class="c12" id="sezione-contesto">
            <div class="card-testa">
              <h3 class="card-titolo">Contesto dei progetti</h3>
              <select id="contesto-progetto" aria-label="Progetto">
                ${progetti.map(p => `<option value="${esc(p.slug)}" ${p.slug === progScelto ? "selected" : ""}>${esc(p.nome)}</option>`).join("")}
              </select>
            </div>
            <div id="contesto-box">${progetti.length ? "" : '<div class="secondario piccolo">Nessun progetto</div>'}</div>
          </div>
        </div>
        <div class="pannello-piede">
          <button class="btn primario grande" id="salva">Salva</button>
          <span class="spazio"></span>
          <button class="btn pericolo" id="chiudi">Chiudi dashboard</button>
        </div>
      </div>
    </div>`;

  $("#ricarica-disp").onclick = () => naviga();
  const salva = async () => {
    const body = {
      chiavi: { GROQ_API_KEY: $("#GROQ_API_KEY").value, NVIDIA_API_KEY: $("#NVIDIA_API_KEY").value },
      valori: { MONITOR_DEVICE: $("#MONITOR_DEVICE").value, RIASSUNTO_INTERVALLO_MINUTI: $("#RIASSUNTO_INTERVALLO_MINUTI").value },
    };
    try {
      await api("/api/impostazioni", { method: "PUT", body });
      toast("Salvato", "ok");
      naviga();
    } catch (e) { toast(e.message, "errore"); }
  };
  $("#salva").onclick = salva;
  let contesto = null;
  const caricaContesto = async slug => {
    if (contesto?.sporco() && !confirm("Il contesto ha modifiche non salvate. Cambiare progetto senza salvarle?")) {
      $("#contesto-progetto").value = progScelto; return;
    }
    contesto = await schedaContesto(slug, $("#contesto-box"));
    history.replaceState(null, "", `#/impostazioni?progetto=${enc(slug)}`);
    hashPrecedente = location.hash;
  };
  if (progScelto) await caricaContesto(progScelto);
  $("#contesto-progetto")?.addEventListener("change", e => caricaContesto(e.target.value));
  // Ctrl+S: salva il contesto se modificato, altrimenti le impostazioni
  salvaCorrente = () => (contesto?.sporco() ? contesto.salva() : salva());
  modificheAperte = () => !!contesto?.sporco();
  if (query.get("progetto")) $("#sezione-contesto").scrollIntoView({ block: "start" });
  $("#chiudi").onclick = async () => {
    if (!await dialogo({ titolo: "Chiudere la dashboard?", testo: "Trascrizioni e riassunti in corso vengono fermati.", conferma: "Chiudi", pericolo: true })) return;
    await api("/api/chiudi", { method: "POST" }).catch(() => {});
    document.body.innerHTML = `<div class="vuoto" style="padding-top:20vh"><strong>Dashboard chiusa</strong>Per riaprirla: avvia_dashboard.bat</div>`;
  };
}

/* ===================================================================
   Avvio
   =================================================================== */

aggiornaHeader();
naviga();
