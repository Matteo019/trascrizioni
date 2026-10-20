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
  window.effetti?.toast(el);
  setTimeout(() => (window.effetti?.via(el) || Promise.resolve()).then(() => el.remove()), tipo === "errore" ? 8000 : 3500);
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
    window.effetti?.dialogo(dlg);
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
  document.body.classList.toggle("home", sezione === "progetti");
  $$(".topnav a").forEach(a => a.classList.toggle("attivo",
    a.dataset.nav === (sezione === "p" ? "progetti" : sezione)));
  // il link "Progetti" nella topnav punta all'ultimo progetto aperto
  const ultimoSlug = memoria("ultimo-progetto");
  const navProgetti = $(".topnav a[data-nav=progetti]");
  if (navProgetti && ultimoSlug) navProgetti.href = `#/p/${enc(ultimoSlug)}`;

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
  window.effetti?.dopoPagina(vista);
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
        <span class="punto"></span><span class="tag-testo">${esc(NOMI_TIPO[p.tipo])} · ${esc(p.nome_progetto)}</span></a>`).join("");
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
  } else if (e.altKey && !e.ctrlKey && /^[1-9]$/.test(e.key) && $$(".topnav a")[+e.key - 1]) {
    // Alt+1..4: sezione diretta
    e.preventDefault();
    location.hash = $$(".topnav a")[+e.key - 1].getAttribute("href");
  } else if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")
             && (!inCampo || !document.activeElement.value)) {
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
   Home: cruscotto e menu (senza la barra di navigazione in alto)
   =================================================================== */

const ICONE_MENU = {
  chat: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>`,
  calendario: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>`,
  impostazioni: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>`,
  pdf: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>`,
  cerca: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>`,
};

/*
 * Visualizzatore audio sotto il saluto: barre verticali come il segnale di una voce.
 * Ogni barra sale e scende piano; il ritardo cresce verso destra, così il picco scorre a destra
 * all'infinito. Altezze e opacità variano (tre livelli) per sembrare un audio vero.
 */
function ondeAudio(n = 56) {
  // pseudo-casuale ma sempre uguale: il disegno non cambia a ogni visita
  let seme = 7;
  const caso = () => (seme = (seme * 9301 + 49297) % 233280) / 233280;
  const opacita = [1, .6, .32];
  return `<div class="audio-barre" aria-hidden="true">${Array.from({ length: n }, (_, i) => {
    const inviluppo = .35 + .65 * Math.sin(Math.PI * (i + .5) / n);        // più alte al centro
    const altezza = Math.round(28 + 72 * inviluppo * (.55 + .45 * caso()));
    return `<i style="--a:${altezza}%;--o:${opacita[i % 3]};--r:${(-(n - i) * 0.09).toFixed(2)}s;--d:${(1.6 + caso() * 1.2).toFixed(2)}s"></i>`;
  }).join("")}</div>`;
}

function saluto() {
  const h = new Date().getHours();
  return h < 13 ? "Buongiorno" : h < 18 ? "Buon pomeriggio" : "Buonasera";
}

/* minuti tra "HH:MM" e "HH:MM" (sessioni a cavallo della mezzanotte comprese) */
function minutiSessione(s) {
  if (!s.ora_inizio || !s.ora_fine) return 0;
  const m = t => +t.slice(0, 2) * 60 + +t.slice(3, 5);
  return (m(s.ora_fine) - m(s.ora_inizio) + 1440) % 1440;
}
const durata = minuti => minuti >= 60 ? `${Math.floor(minuti / 60)} h ${pad(minuti % 60)}` : `${minuti} min`;

async function vistaProgetti(query) {
  const [progetti, eventi, impostazioni] = await Promise.all([
    api("/api/progetti"), api("/api/calendario"), api("/api/impostazioni").catch(() => null)]);
  const archiviati = query.get("archiviati") === "1";
  const visibili = progetti
    .filter(p => archiviati || p.stato !== "archiviato")
    .sort((a, b) => (b.ultima_attivita || "").localeCompare(a.ultima_attivita || ""));
  const nArchiviati = progetti.filter(p => p.stato === "archiviato").length;
  const sessioni = eventi.filter(s => archiviati || s.stato_progetto !== "archiviato")
    .sort((a, b) => b.inizio.localeCompare(a.inizio));
  const conPdf = sessioni.filter(s => s.ha_riassunto || s.ha_appunti);
  const ultima = conPdf.find(s => s.ha_riassunto) || conPdf[0];
  const chiaviOk = impostazioni ? Object.values(impostazioni.chiavi).every(Boolean) : true;
  const ultimoProgetto = visibili[0];

  // ultimi 7 giorni: minuti trascritti al giorno
  const giorni = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - (6 - i));
    const lista = sessioni.filter(s => new Date(s.inizio).toDateString() === d.toDateString());
    return { d, minuti: lista.reduce((t, s) => t + minutiSessione(s), 0), sessioni: lista.length };
  });
  const maxMinuti = Math.max(60, ...giorni.map(g => g.minuti));
  const settimana = giorni.reduce((t, g) => ({ minuti: t.minuti + g.minuti, sessioni: t.sessioni + g.sessioni }), { minuti: 0, sessioni: 0 });
  const qualita = visibili.reduce((t, p) => {
    ["chiaro", "incerto", "rumore", "totale"].forEach(k => t[k] += p.qualita[k]);
    return t;
  }, { chiaro: 0, incerto: 0, rumore: 0, totale: 0 });
  const oggi = giorni[giorni.length - 1];
  const inCorso = progetti.some(p => p.processi.some(x => x.attivo));
  const riepilogoOggi = [
    oggi.sessioni ? `${oggi.sessioni} ${oggi.sessioni === 1 ? "sessione" : "sessioni"} oggi` : "Nessuna sessione oggi",
    oggi.minuti ? `${durata(oggi.minuti)} trascritte` : "",
    inCorso ? "una chiamata in corso" : "",
  ].filter(Boolean).join(" · ");

  vista.innerHTML = `
    <div class="cruscotto bento-pagina">
      <header class="cr-testa">
        <div class="cr-saluto">
          <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg></span>
          <div><h1 class="cr-nome">trascrizioni</h1><span class="secondario">${new Date().toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" })}</span></div>
        </div>
        <form class="cr-cerca" id="cr-cerca" role="search">
          ${ICONE_MENU.cerca}
          <input type="search" id="cr-cerca-testo" placeholder="Cerca in trascrizioni, appunti e riassunti" aria-label="Cerca">
        </form>
        <div class="cr-icone">
          <a class="btn icona fantasma cr-imp" href="#/impostazioni" title="${chiaviOk ? "Impostazioni" : "Impostazioni: manca una chiave"}" aria-label="Impostazioni">
            ${ICONE_MENU.impostazioni}${chiaviOk ? "" : '<span class="cr-allarme"></span>'}</a>
          <button class="btn icona fantasma" id="cr-tema" aria-label="Cambia tema" title="Tema chiaro/scuro">
            <svg class="icona-sole" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg><svg class="icona-luna" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>
          </button>
        </div>
      </header>

      <nav class="cr-progetti-fila" aria-label="Progetti">
        ${visibili.map(p => {
          const attivo = p.processi.some(x => x.attivo);
          return `<a class="cr-pillola" href="#/p/${enc(p.slug)}" title="${intero(p.sessioni)} sessioni">
            ${attivo ? '<span class="vivo-punto"></span>' : ""}${esc(p.nome)}<span class="cr-conta">${intero(p.sessioni)}</span></a>`;
        }).join("")}
        <button class="cr-pillola cr-nuovo" id="nuovo">+ Nuovo progetto</button>
        ${nArchiviati ? `<label class="check piccolo"><input type="checkbox" id="archiviati" ${archiviati ? "checked" : ""}> archiviati (${nArchiviati})</label>` : ""}
      </nav>

      <section class="bento">
        <article class="bento-tile b-ultimo">
          ${ultima ? `
            <div class="b-testa">
              <span class="etichetta-lato">Ultimo riassunto</span>
              <a class="b-titolo" href="#/p/${enc(ultima.progetto)}/s/${ultima.nome}">${esc(ultima.titolo || "Senza titolo")}</a>
              <span class="secondario piccolo">${esc(ultima.nome_progetto)} · ${data(ultima.inizio)}${ultima.ora_inizio ? ` · ${ultima.ora_inizio}–${ultima.ora_fine}` : ""}${ultima.in_corso ? " · in corso" : ""}</span>
            </div>
            <div class="b-anteprima scorre"><div class="documento b-documento" id="b-anteprima"><span class="secondario">Carico il riassunto…</span></div></div>
            <div class="b-azioni">
              <a class="btn primario piccolo" href="#/p/${enc(ultima.progetto)}/s/${ultima.nome}">Apri</a>
              <a class="btn piccolo" href="/api/progetti/${enc(ultima.progetto)}/sessioni/${ultima.nome}/pdf" target="_blank" rel="noopener">${ICONE_MENU.pdf} PDF</a>
              <a class="btn verde piccolo" href="#/chat?progetto=${enc(ultima.progetto)}&sessione=${ultima.nome}">Chat su questa sessione</a>
              ${ultima.qualita.totale ? `<span class="b-chiaro" title="Voci chiare nel riassunto"><strong>${percento(ultima.qualita.chiaro, ultima.qualita.totale)}</strong> chiaro</span>` : ""}
            </div>`
          : `<div class="vuoto"><strong>Ancora nessun riassunto</strong>Qui comparirà l'anteprima dell'ultimo riassunto.</div>`}
        </article>

        <article class="bento-tile b-live" id="cr-live"></article>

        <section class="b-chat" aria-label="Chiedi alla chat">
          <div class="b-saluto">
            <h2 class="b-saluto-testo">${saluto()}</h2>
            <p class="b-saluto-riga">${riepilogoOggi}</p>
            <form class="b-aiuto" id="cr-chiedi">
              <input type="text" id="cr-domanda" placeholder="Come ti posso aiutare?" aria-label="Come ti posso aiutare? Scrivi una domanda per la chat" autocomplete="off">
              <button class="btn primario icona" aria-label="Invia alla chat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M13 6l6 6-6 6"/></svg></button>
            </form>
            ${ondeAudio()}
          </div>
        </section>

        <section class="b-pdf" aria-label="Riassunti e PDF">
          <div class="card-testa"><h3 class="card-titolo">Riassunti e PDF</h3><span class="secondario num">${intero(conPdf.length)}</span></div>
          <div class="scorre">${conPdf.slice(0, 3).map(s => `
            <div class="cr-pdf">
              <a class="cr-pdf-testo" href="#/p/${enc(s.progetto)}/s/${s.nome}">
                <strong>${esc(s.titolo || "Senza titolo")}</strong>
                <span class="secondario piccolo">${esc(s.nome_progetto)} · ${data(s.inizio)}${s.ora_inizio ? ` · ${s.ora_inizio}` : ""}</span>
              </a>
              <a class="btn icona piccolo verde" href="/api/progetti/${enc(s.progetto)}/sessioni/${s.nome}/pdf" target="_blank" rel="noopener"
                 title="Scarica PDF" aria-label="Scarica il PDF di ${esc(s.titolo || "questa sessione")}">${ICONE_MENU.pdf}</a>
            </div>`).join("") || `<div class="vuoto piccolo">I riassunti compaiono qui appena pronti.</div>`}</div>
        </section>

        <article class="bento-tile b-settimana">
          <div class="card-testa"><a href="#/calendario"><h3 class="card-titolo">Questa settimana →</h3></a></div>
          <div class="b-kpi">
            <div><span class="grande">${intero(settimana.sessioni)}</span><span class="secondario piccolo">sessioni</span></div>
            <div><span class="cr-grande-testo">${durata(settimana.minuti)}</span><span class="secondario piccolo">trascritte</span></div>
            <div><span class="grande">${qualita.totale ? percento(qualita.chiaro, qualita.totale) : "—"}</span><span class="secondario piccolo">chiaro</span></div>
          </div>
          <div class="b-grafico" role="img" aria-label="Minuti trascritti negli ultimi 7 giorni">
            ${giorni.map(g => `<a class="b-colonna ${g.d.toDateString() === new Date().toDateString() ? "oggi" : ""}"
                href="#/calendario?${new URLSearchParams({ mese: `${g.d.getFullYear()}-${g.d.getMonth() + 1}`, giorno: `${g.d.getFullYear()}-${pad(g.d.getMonth() + 1)}-${pad(g.d.getDate())}` })}"
                data-tip="${esc(`<strong>${g.d.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" })}</strong><br>${g.sessioni} sessioni · ${durata(g.minuti)}`)}">
                <span class="b-barra" style="--h:${g.minuti ? Math.max(6, 100 * g.minuti / maxMinuti) : 0}%"></span>
                <span class="b-giorno">${g.d.toLocaleDateString("it-IT", { weekday: "short" }).slice(0, 3)}</span>
              </a>`).join("")}
          </div>
        </article>
      </section>
    </div>`;

  $("#cr-chiedi").onsubmit = e => {
    e.preventDefault();
    const d = $("#cr-domanda").value.trim();
    location.hash = d ? `#/chat?${new URLSearchParams({ domanda: d })}` : "#/chat";
  };
  window.effetti?.audioVivo($(".audio-barre"));
  $("#cr-cerca").onsubmit = e => {
    e.preventDefault();
    const q = $("#cr-cerca-testo").value.trim();
    if (q) location.hash = `#/cerca?q=${enc(q)}`;
  };
  $("#cr-tema").onclick = () => $("#tema").click();
  $("#nuovo").onclick = () => nuovoProgetto();
  $("#archiviati")?.addEventListener("change", e => { location.hash = e.target.checked ? "#/?archiviati=1" : "#/"; });

  // ogni riquadro intero è cliccabile; link, bottoni e campi dentro mantengono la loro funzione
  const sessioneLink = s => s ? `#/p/${enc(s.progetto)}/s/${s.nome}` : "";
  const destinazioni = {
    ".b-ultimo": sessioneLink(ultima),
    ".b-live": ultimoProgetto ? `#/p/${enc(ultimoProgetto.slug)}` : "",
    ".b-settimana": "#/calendario",
  };
  for (const [sel, href] of Object.entries(destinazioni)) {
    const tile = $(sel);
    if (!tile || !href) continue;
    tile.dataset.href = href;
    tile.classList.add("tile-cliccabile");
    tile.tabIndex = 0;
    tile.setAttribute("role", "link");
    tile.onclick = e => { if (!e.target.closest("a, button, input, form, label, select, textarea")) location.hash = tile.dataset.href; };
    tile.onkeydown = e => { if (e.key === "Enter" && e.target === tile) location.hash = tile.dataset.href; };
  }

  // anteprima dell'ultimo riassunto
  if (ultima) {
    api(`/api/progetti/${enc(ultima.progetto)}/sessioni/${enc(ultima.nome)}/documento/${ultima.ha_riassunto ? "riassunto" : "appunti"}`)
      .then(doc => {
        const box = $("#b-anteprima");
        if (!box) return;
        box.innerHTML = doc.html || `<span class="secondario">Riassunto non ancora pronto</span>`;
        // titolo e indice sono già in testa al riquadro: si parte dal contenuto
        box.querySelector("h1")?.remove();
        box.querySelector("h1 + p, p:first-child em")?.closest("p")?.remove();
        window.effetti?.entrata(box.querySelectorAll(":scope > *"), { passo: 25, max: 10 });
      }).catch(() => {});
  }

  // "In corso ora": ultime frasi trascritte in diretta
  let ultimaFirma = "";
  async function aggiornaLive() {
    const box = $("#cr-live");
    if (!box) return;
    const processi = (await api("/api/processi").catch(() => [])).filter(p => p.attivo);
    const trascr = processi.find(p => p.tipo === "trascrizione");
    let html;
    if (!processi.length) {
      html = `
        <div class="cr-tasto-testa"><span class="cr-icona b-spento">●</span><strong>Niente in corso</strong></div>
        <p class="secondario">Quando parte una chiamata, qui vedi la trascrizione in diretta.</p>
        ${ultimoProgetto ? `<div class="b-azioni"><button class="btn primario" id="cr-avvia" title="Continua l'ultima sessione del progetto">Continua trascrizione</button>
          <span class="secondario piccolo">su ${esc(ultimoProgetto.nome)}</span></div>` : ""}`;
    } else {
      const p = trascr || processi[0];
      let frasi = [], avviso = "";
      if (trascr) {
        const log = await api(`/api/progetti/${enc(trascr.progetto)}/processi/trascrizione/log?da=-1`).catch(() => null);
        const righe = log?.righe || [];
        frasi = righe.filter(r => /^\[\d\d:\d\d:\d\d/.test(r)).slice(-6);
        // ultimo messaggio di stato: un errore non seguito da "ripristinata" è un problema in corso
        const stato = righe.filter(r => /^\[(ERRORE|AUDIO|WARN)\]/.test(r)).pop() || "";
        if (/^\[(ERRORE|WARN)\]/.test(stato)) avviso = stato.replace(/^\[\w+\]\s*/, "");
        // nessuna frase nuova da un po': pausa lunga oppure l'audio non arriva al cavo virtuale
        const ultima = /^\[(\d\d):(\d\d):(\d\d)/.exec(frasi[frasi.length - 1] || "");
        const daQuando = new Date(Math.max(new Date(trascr.avviato), ultima
          ? new Date().setHours(+ultima[1], +ultima[2], +ultima[3], 0) : 0));
        const minuti = Math.floor((Date.now() - daQuando) / 60000);
        if (!avviso && minuti >= 3) avviso = `Nessuna frase trascritta da ${minuti} min: è una pausa o l'audio non arriva? Se non senti la chiamata in cuffia, riavvia la trascrizione.`;
      }
      const minutiAttivo = p.avviato ? Math.max(0, Math.round((Date.now() - new Date(p.avviato)) / 60000)) : 0;
      html = `
        <div class="cr-tasto-testa"><span class="tag successo vivo"><span class="punto"></span>Log</span>
          <strong>${esc(p.nome_progetto)}</strong><span class="secondario piccolo" style="margin-left:auto">${durata(minutiAttivo)} · ${processi.map(x => NOMI_TIPO[x.tipo]).join(" + ")}</span></div>
        <div class="b-frasi">${frasi.map(f => {
          const [, ora, testo] = /^\[(\d\d:\d\d):\d\d[^\]]*\]\s*(.*)$/.exec(f) || [, "", f];
          return `<div class="b-frase"><span class="b-ora">${ora}</span><span>${esc(testo)}</span></div>`;
        }).join("") || `<span class="secondario">In attesa del primo blocco…</span>`}</div>
        ${avviso ? `<div class="b-avviso" role="alert">${esc(avviso)}</div>` : ""}
        <div class="b-azioni">
          <a class="btn piccolo" href="#/p/${enc(p.progetto)}">Apri</a>
          ${trascr ? `<button class="btn piccolo pericolo" id="cr-ferma">Ferma trascrizione</button>` : ""}
        </div>`;
    }
    if (html === ultimaFirma) return;        // niente cambiato: nessun ridisegno
    const nuove = ultimaFirma !== "";
    ultimaFirma = html;
    box.classList.toggle("acceso", processi.length > 0);
    // in diretta il riquadro porta al progetto in corso
    if (processi.length) box.dataset.href = `#/p/${enc((trascr || processi[0]).progetto)}`;
    box.innerHTML = html;
    if (nuove) window.effetti?.entrata(box.querySelectorAll(".b-frase:last-child"));
    $("#cr-avvia")?.addEventListener("click", async () => {
      try {
        await api(`/api/progetti/${enc(ultimoProgetto.slug)}/processi/trascrizione/avvia`, { method: "POST" });
        toast(`Trascrizione ripresa su ${ultimoProgetto.nome}`, "ok");
        aggiornaHeader(); aggiornaLive();
      } catch (e) { toast(e.message, "errore"); }
    });
    $("#cr-ferma")?.addEventListener("click", async () => {
      if (!await dialogo({ titolo: "Fermare la trascrizione?", testo: "Viene trascritto anche il blocco in corso, poi si ferma.", conferma: "Ferma" })) return;
      try {
        await api(`/api/progetti/${enc(trascr.progetto)}/processi/trascrizione/ferma`, { method: "POST" });
        toast("La trascrizione si ferma a fine blocco");
        aggiornaLive();
      } catch (e) { toast(e.message, "errore"); }
    });
  }
  await aggiornaLive();
  ogni(4000, aggiornaLive);
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
  memoria("ultimo-progetto", slug);
  const p = await api(`/api/progetti/${enc(slug)}`);
  const sessioni = p.elenco_sessioni;
  const nome = nomeSessione || sessioni[0]?.nome || "";
  const s = sessioni.find(x => x.nome === nome);
  if (nomeSessione && !s) throw new Error("Sessione non trovata.");
  const link = (t, n = nome) => `#/p/${enc(slug)}${n ? `/s/${n}` : ""}?tab=${t}`;
  if (!SCHEDE_SESSIONE.some(([k]) => k === tab)) tab = "riassunto";
  const pdf = s && (s.ha_riassunto || s.ha_appunti);

  // a sinistra (30%) tutto quello che serve a guidare, a destra (70%) il documento
  vista.innerHTML = `
    <div class="progetto-layout">
      <aside class="progetto-sx">
        <div class="breadcrumb"><a href="#/">Progetti</a> / ${esc(p.nome)}</div>
        <div class="progetto-titolo">
          <h1>${esc(p.nome)}${p.stato === "archiviato" ? ' <span class="tag">Archiviato</span>' : ""}</h1>
          <div class="menu-altro">
            <button class="btn icona piccolo" id="altro" aria-label="Altre azioni" title="Altre azioni" aria-haspopup="true">⋯</button>
            <div class="menu-altro-lista" hidden>
              <button id="apri">Apri cartella</button>
              <button id="rinomina">Rinomina</button>
              <button id="contesto">Modifica contesto</button>
              <button id="archivia">${p.stato === "archiviato" ? "Ripristina" : "Archivia"}</button>
            </div>
          </div>
        </div>
        <div class="processi-card">
          <div class="switch-processi" role="tablist" aria-label="Trascrizione o riassunto">
            ${["trascrizione", "riassunto"].map(t => `<button role="tab" data-proc="${t}">${NOMI_TIPO[t]}<span class="sw-stato"></span></button>`).join("")}
          </div>
          <div class="processo" id="proc-trascrizione"></div>
          <div class="processo" id="proc-riassunto"></div>
        </div>
        <div class="blocco-sx">
          <div class="etichetta-lato">Sessioni</div>
          ${sessioni.length ? `<div class="sessioni-lista">${sessioni.map(x => `
            <div class="sessione-riga">
            <a class="sessione-voce ${x.nome === nome ? "attiva" : ""}" href="${link(tab, x.nome)}">
              <span class="t">${esc(x.titolo || "Senza titolo")}</span>
              <span class="d">${data(x.inizio)}${x.ora_inizio ? ` · ${x.ora_inizio}` : ""}
                ${x.in_corso ? '<span class="tag attesa vivo"><span class="punto"></span>in corso</span>' : ""}
                ${x.qualita.totale ? `<span class="num" style="margin-left:auto" title="Incerto">${percento(x.qualita.incerto, x.qualita.totale)}</span>` : ""}</span>
            </a>
            ${x.ha_riassunto || x.ha_appunti
              ? `<a class="btn icona piccolo verde" href="/api/progetti/${enc(slug)}/sessioni/${x.nome}/pdf" target="_blank" rel="noopener"
                   title="Scarica PDF" aria-label="Scarica il PDF di ${esc(x.titolo || "questa sessione")}">${ICONE_MENU.pdf}</a>`
              : `<span class="btn icona piccolo" aria-hidden="true" style="visibility:hidden"></span>`}
            </div>`).join("")}</div>`
          : `<div class="secondario piccolo">Nessuna sessione: avvia la trascrizione qui sopra.</div>`}
        </div>
        <div class="blocco-sx" id="incerti-box"></div>
        <div class="blocco-sx">
          ${s
            ? `<div class="etichetta-lato">Affidabilità della sessione</div>${torta(s.qualita)}`
            : `<div class="etichetta-lato">Affidabilità del progetto</div>${torta(p.qualita)}`}
        </div>
      </aside>

      <section class="pannello progetto-dx">
        <div class="centro" id="scheda"></div>
        ${s ? `<div class="pannello-piede">
          <div class="piede-sx">
            <div class="switch-schede" role="tablist" aria-label="Tipo di contenuto">
              ${SCHEDE_SESSIONE.map(([k, n]) => `<a href="${link(k)}" role="tab" class="${tab === k ? "attivo" : ""}" aria-selected="${tab === k}">${n}</a>`).join("")}
            </div>
          </div>
          <div class="piede-dx">
            ${pdf ? `<a class="btn primario grande" href="/api/progetti/${enc(slug)}/sessioni/${nome}/pdf" target="_blank" rel="noopener">Scarica PDF</a>`
                  : `<button class="btn primario grande" disabled title="Serve almeno il primo giro di appunti">Scarica PDF</button>`}
            <button class="btn grande" id="copia">Copia testo</button>
            <a class="btn verde grande" href="#/chat?progetto=${enc(slug)}&sessione=${nome}">Chat su questa sessione</a>
          </div>
        </div>` : ""}
      </section>
    </div>`;

  $("#altro").onclick = () => { const m = $(".menu-altro-lista"); m.hidden = !m.hidden; };
  $("#apri").onclick = () => {
    $(".menu-altro-lista").hidden = true;
    api(`/api/progetti/${enc(slug)}/apri-cartella`, { method: "POST" })
      .then(() => toast("Cartella aperta in Esplora file", "ok")).catch(e => toast(e.message, "errore"));
  };
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
  // un'unica card: il selettore sceglie quale dei due processi mostrare
  const mostraProcesso = tipo => {
    $$(".switch-processi button").forEach(b => {
      const scelto = b.dataset.proc === tipo;
      b.classList.toggle("attivo", scelto);
      b.setAttribute("aria-selected", scelto);
    });
    $("#proc-trascrizione").hidden = tipo !== "trascrizione";
    $("#proc-riassunto").hidden = tipo !== "riassunto";
    memoria("processo-scelto", tipo);
    window.effetti?.indicatori();
  };
  $$(".switch-processi button").forEach(b => b.onclick = () => mostraProcesso(b.dataset.proc));
  mostraProcesso(memoria("processo-scelto") === "riassunto" ? "riassunto" : "trascrizione");

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
    const sw = $(`.switch-processi [data-proc="${tipo}"] .sw-stato`);
    if (sw) sw.innerHTML = tag;

    const bottoni = $(".bottoni", box);
    const firma = `${s.attivo}|${s.in_chiusura}`;
    if (bottoni.dataset.firma !== firma) {
      bottoni.dataset.firma = firma;
      if (!s.attivo) bottoni.innerHTML = tipo === "trascrizione"
        ? `<button class="btn primario" data-a="avvia" title="Continua l'ultima sessione">Continua sessione</button>
           <button class="btn" data-a="nuova" title="Inizia una sessione nuova con un riassunto suo">Nuova sessione</button>`
        : `<button class="btn primario" data-a="avvia">Avvia</button>`;
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
      if (a === "avvia" || a === "nuova") {
        stato = await api(`${url}/avvia${a === "nuova" ? "?nuova=true" : ""}`, { method: "POST" });
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
          <span class="sporco" id="sporco-${k}"></span></div>
        <textarea id="testo-${k}" spellcheck="false" aria-label="${f.titolo}">${esc(f.testo)}</textarea>
      </div>`).join("")}
    </div>`;

  const salvato = { contesto: contesto.testo, glossario: glossario.testo };
  const sporco = k => $(`#testo-${k}`).value !== salvato[k];
  const segna = k => $(`#sporco-${k}`).textContent = sporco(k) ? "Non salvato" : "";
  // si salva col bottone "Salva" delle impostazioni (o Ctrl+S), insieme a tutto il resto
  async function salva(k) {
    const testo = $(`#testo-${k}`).value;
    await api(url(k), { method: "PUT", body: { testo } });
    salvato[k] = testo;
    segna(k);
  }
  for (const k of Object.keys(file)) $(`#testo-${k}`).addEventListener("input", () => segna(k));
  return {
    salva: () => Promise.all(Object.keys(file).filter(sporco).map(salva)),
    sporco: () => Object.keys(file).some(sporco),
  };
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
   Chat
   =================================================================== */

const ESEMPI_CHAT = [
  "Quali sono gli argomenti principali trattati?",
  "Cosa è stato chiesto o promesso?",
  "Riassumi i passaggi segnati come incerti",
];

const ICONA_AI = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>`;
const ICONA_INVIA = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M13 6l6 6-6 6"/></svg>`;

const oraBreve = iso => { const d = iso ? new Date(iso) : new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/*
 * Chat sui documenti dei progetti (pagina «Chat»).
 * La conversazione resta salvata per ambito (progetto + sessione) finché il browser è aperto.
 */
async function creaChat(box, { prog = "", sess = "", compatta = false, cambiaAmbito, domanda = "" }) {
  const [progetti, catalogo] = await Promise.all([api("/api/progetti"), api("/api/chat/modelli")]);
  let modello = memoria("chat-modello") || catalogo.predefinito;
  if (!catalogo.modelli.some(m => m.id === modello)) modello = catalogo.predefinito;
  const progetto = prog ? await api(`/api/progetti/${enc(prog)}`).catch(() => null) : null;
  const chiave = `chat:${prog}:${sess}`;
  let storico = [];
  try { storico = JSON.parse(sessionStorage.getItem(chiave) || "[]"); } catch (e) {}
  const memorizza = () => { try { sessionStorage.setItem(chiave, JSON.stringify(storico)); } catch (e) {} };

  box.innerHTML = `
    <div class="chat ${compatta ? "chat-compatta" : ""}">
      <div class="chat-barra">
        <select class="ambito-progetto" aria-label="Progetto">
          <option value="">Tutti i progetti attivi</option>
          ${progetti.map(p => `<option value="${esc(p.slug)}" ${p.slug === prog ? "selected" : ""}>${esc(p.nome)}</option>`).join("")}
        </select>
        <select class="ambito-sessione" aria-label="Sessione" ${progetto ? "" : "disabled"}>
          <option value="">Tutte le sessioni</option>
          ${(progetto?.elenco_sessioni || []).map(s => `<option value="${s.nome}" ${s.nome === sess ? "selected" : ""}>${data(s.inizio)} — ${esc(s.titolo || "Senza titolo")}</option>`).join("")}
        </select>
        <span class="spazio"></span>
        <div class="gruppo-modello">
          <select class="scelta-modello" aria-label="Modello che risponde">
            ${catalogo.modelli.map(m => `<option value="${esc(m.id)}" ${m.id === modello ? "selected" : ""}>${esc(m.nome)} · ${esc(m.autore)}</option>`).join("")}
          </select>
          ${compatta ? "" : `<div class="modello-info"></div>`}
        </div>
        <button class="btn fantasma piccolo nuova" type="button" title="Nuova conversazione">Nuova</button>
      </div>
      <div class="messaggi" aria-live="polite"></div>
      <form class="composer">
        <textarea rows="1" placeholder="Scrivi una domanda…" aria-label="Domanda"></textarea>
        <button class="btn primario invia" aria-label="Invia" title="Invia (Invio)">${ICONA_INVIA}</button>
      </form>
    </div>`;

  const q = sel => box.querySelector(sel);
  const lista = q(".messaggi"), campo = q("textarea"), bottone = q(".invia");

  const fontiHtml = fonti => !fonti?.length ? "" : `<div class="fonti">${fonti.map((f, i) =>
    `<a class="fonte" href="#/p/${enc(f.progetto)}/s/${f.sessione}?tab=${f.tipo}"><b>${i + 1}</b>${esc(f.titolo || data(sessioneIso(f.sessione)))}</a>`).join("")}</div>`;

  const htmlMessaggio = m => m.ruolo === "utente"
    ? `<div class="msg utente"><div class="bolla">${esc(m.testo)}</div><div class="msg-meta">${oraBreve(m.ora)}</div></div>`
    : `<div class="msg ${m.errore ? "errore" : "assistente"}">
        <div class="avatar-ai" aria-hidden="true">${ICONA_AI}</div>
        <div class="msg-corpo">
          <div class="bolla documento">${m.errore ? esc(m.testo) : m.html}${fontiHtml(m.fonti)}</div>
          <div class="msg-meta">${oraBreve(m.ora)}${m.modello ? ` · Ha risposto <strong>${esc(m.modello)}</strong> in ${m.secondi} s` : ""}${m.saltati?.length ? ` · ${esc(m.saltati.join(", "))} non ha risposto, saltato` : ""}</div>
        </div>
      </div>`;

  function disegnaTutto() {
    if (!storico.length) {
      lista.innerHTML = `<div class="chat-vuota">
          <div class="avatar-ai grande-ai" aria-hidden="true">${ICONA_AI}</div>
          <strong>Chat con i tuoi progetti</strong>
          <span class="secondario">${prog ? esc(progetto?.nome || "") + (sess ? " · una sessione" : "") : "Tutti i progetti attivi"}</span>
          <div class="esempi">${ESEMPI_CHAT.map(e => `<button class="chip" type="button" data-esempio="${esc(e)}">${esc(e)}</button>`).join("")}</div>
        </div>`;
      lista.querySelectorAll("[data-esempio]").forEach(b => b.onclick = () => { campo.value = b.dataset.esempio; invia(); });
      window.effetti?.entrata(lista.querySelectorAll(".chat-vuota > *"));
      window.effetti?.galleggia(lista.querySelectorAll(".chip"));
      return;
    }
    lista.innerHTML = storico.map(htmlMessaggio).join("");
    lista.scrollTop = lista.scrollHeight;
  }

  function aggiungi(m, rivela = false) {
    lista.querySelector(".chat-vuota")?.remove();
    lista.insertAdjacentHTML("beforeend", htmlMessaggio(m));
    const el = lista.lastElementChild;
    window.effetti?.messaggio(el, rivela);
    lista.scrollTo({ top: lista.scrollHeight, behavior: "smooth" });
    return el;
  }

  let occupato = false;
  async function invia() {
    const domanda = campo.value.trim();
    if (!domanda || occupato) return;
    occupato = true;
    bottone.disabled = true;
    const precedente = storico.filter(m => !m.errore).map(m => ({ ruolo: m.ruolo, testo: m.testo }));
    const msgUtente = { ruolo: "utente", testo: domanda, ora: new Date().toISOString() };
    storico.push(msgUtente);
    campo.value = "";
    adattaAltezza();
    aggiungi(msgUtente);
    // "sta scrivendo…": tre puntini come nelle chat online
    lista.insertAdjacentHTML("beforeend", `<div class="msg assistente digitando"><div class="avatar-ai" aria-hidden="true">${ICONA_AI}</div>
      <div class="bolla"><span class="puntini"><i></i><i></i><i></i></span><span class="secondario piccolo stato-attesa">Cerco nei tuoi documenti…</span></div></div>`);
    const digitando = lista.lastElementChild;
    // attesa visibile: secondi che passano e cosa sta succedendo (il modello lento viene saltato da solo)
    const partenza = Date.now();
    const contatore = setInterval(() => {
      const s = Math.round((Date.now() - partenza) / 1000);
      const el = digitando.querySelector(".stato-attesa");
      if (!el) return clearInterval(contatore);
      el.textContent = s < 15 ? `Cerco nei tuoi documenti… ${s} s`
        : s < 40 ? `Il modello ci sta mettendo un po'… ${s} s`
        : `Il modello non risponde: provo con un altro… ${s} s`;
    }, 1000);
    window.effetti?.messaggio(digitando);
    lista.scrollTo({ top: lista.scrollHeight, behavior: "smooth" });
    let risposta;
    try {
      const r = await api("/api/chat", { method: "POST", body: { domanda, storico: precedente, progetto: prog, sessione: sess, modello } });
      risposta = { ruolo: "assistente", testo: r.risposta, html: r.html, fonti: r.fonti, modello: r.nome_modello, secondi: r.secondi, saltati: r.saltati, ora: new Date().toISOString() };
    } catch (e) {
      risposta = { ruolo: "assistente", testo: e.message, errore: true, ora: new Date().toISOString() };
    }
    clearInterval(contatore);
    storico.push(risposta);
    memorizza();
    occupato = false;
    if (!document.body.contains(lista)) return;      // nel frattempo la chat è stata chiusa
    digitando.remove();
    aggiungi(risposta, true);
    bottone.disabled = false;
    campo.focus();
  }

  function adattaAltezza() {
    campo.style.height = "auto";
    campo.style.height = Math.min(campo.scrollHeight, 160) + "px";
  }

  q(".composer").onsubmit = e => { e.preventDefault(); invia(); };
  campo.addEventListener("input", adattaAltezza);
  campo.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); invia(); }
  });
  const descriviModello = () => {
    const info = q(".modello-info");
    if (!info) return;
    const m = catalogo.modelli.find(x => x.id === modello);
    info.innerHTML = m.punti.map(p => `<span class="tag successo">${esc(p)}</span>`).join("");
  };
  q(".scelta-modello").onchange = e => { modello = e.target.value; memoria("chat-modello", modello); descriviModello(); };
  descriviModello();
  api("/api/chat/stato").then(stato => {
    if (!document.body.contains(lista)) return;
    for (const o of q(".scelta-modello").options) {
      const m = catalogo.modelli.find(x => x.id === o.value);
      o.textContent = `${m.nome} · ${m.autore}${stato[o.value] === false ? " — lento ora" : ""}`;
    }
    if (stato[modello] === false) {
      const buono = catalogo.modelli.find(x => stato[x.id]);
      if (buono) { modello = buono.id; q(".scelta-modello").value = modello; descriviModello(); }
    }
  }).catch(() => {});
  q(".nuova").onclick = () => { storico = []; memorizza(); disegnaTutto(); campo.focus(); };
  q(".ambito-progetto").onchange = e => cambiaAmbito(e.target.value, "");
  q(".ambito-sessione").onchange = e => cambiaAmbito(prog, e.target.value);
  disegnaTutto();
  campo.focus();
  if (domanda) { campo.value = domanda; invia(); }
}

async function vistaChat(query) {
  vista.innerHTML = `
    ${hero("Chat")}
    <div class="sotto-hero"><div class="pannello riempi chat-pagina"></div></div>`;
  const domanda = query.get("domanda") || "";
  if (domanda) {
    // la domanda arriva dalla home: la si invia una volta sola, senza lasciarla nell'indirizzo
    const pulito = new URLSearchParams(query);
    pulito.delete("domanda");
    history.replaceState(null, "", `#/chat${pulito.toString() ? `?${pulito}` : ""}`);
    hashPrecedente = location.hash;
  }
  await creaChat($(".chat-pagina"), {
    prog: query.get("progetto") || "", sess: query.get("sessione") || "", domanda,
    cambiaAmbito: (p, s) => { location.hash = `#/chat?${new URLSearchParams({ ...(p ? { progetto: p } : {}), ...(s ? { sessione: s } : {}) })}`; },
  });
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
  // come la trascrizione: il dispositivo va bene se il suo nome CONTIENE quanto salvato (es. "AirPods")
  const disp = i.valori.MONITOR_DEVICE;
  const trovato = disp ? i.dispositivi.find(d => d.toLowerCase().includes(disp.toLowerCase())) : "";
  const opzioni = [...i.dispositivi];
  if (disp && !trovato) opzioni.unshift(disp);
  const scelto = trovato || disp;
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
                  ${opzioni.map(d => `<option ${d === scelto ? "selected" : ""}>${esc(d)}</option>`).join("")}
                </select>
                <button class="btn" id="ricarica-disp" title="Rileggi i dispositivi collegati">Aggiorna</button>
              </div>
              ${i.errore_audio ? `<div class="aiuto" style="color:var(--danger-text)">Non riesco a leggere i dispositivi audio: ${esc(i.errore_audio)}</div>` : ""}
              ${disp && !trovato ? `<div class="aiuto" style="color:var(--warning-text)">«${esc(disp)}» ora non è collegato.</div>` : ""}
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
      // un solo Salva per tutto: impostazioni, contesto e glossario del progetto scelto
      await contesto?.salva();
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
  // Ctrl+S: come il bottone Salva, salva tutto
  salvaCorrente = salva;
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
