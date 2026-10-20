"use strict";

/*
 * Animazioni dell'interfaccia: onda al clic su tutto ciò che si preme, indicatori che scorrono
 * sotto la voce attiva, entrate a cascata, numeri che salgono, riflesso "liquid glass" che segue
 * il mouse, messaggi della chat che compaiono a poco a poco, domande d'esempio che galleggiano.
 * Con "riduci animazioni" del sistema operativo non si anima niente.
 */
(() => {
  const ridotte = matchMedia("(prefers-reduced-motion: reduce)");
  const fermo = () => ridotte.matches;
  const MORBIDO = "cubic-bezier(.22, 1, .36, 1)";
  const MOLLA = "cubic-bezier(.34, 1.56, .64, 1)";

  /* ---------- onda al clic ---------- */
  const PREMIBILI = [
    ".btn", ".topnav a", ".tabs a", ".tabs button", ".riquadro", ".sessione-voce", ".elenco-voce",
    ".switch-processi button", ".switch-schede a", ".menu-altro-lista button", ".evento", ".giorno[data-href]", ".kcard-sessione",
    ".chip", ".fonte", ".processo-testa", ".brand", "tr.cliccabile", ".risultato a", ".tile-cliccabile", ".cr-pillola",
  ].join(",");

  document.addEventListener("pointerdown", e => {
    if (fermo() || e.button !== 0) return;
    const el = e.target.closest(PREMIBILI);
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
    const r = el.getBoundingClientRect();
    const lato = Math.max(r.width, r.height) * 2.2;
    const onda = document.createElement("span");
    onda.className = "onda";
    onda.style.cssText = `width:${lato}px;height:${lato}px;left:${e.clientX - r.left - lato / 2}px;top:${e.clientY - r.top - lato / 2}px`;
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    el.classList.add("con-onda");
    el.appendChild(onda);
    onda.animate([{ transform: "scale(0)", opacity: .38 }, { transform: "scale(1)", opacity: 0 }],
      { duration: 650, easing: MORBIDO }).onfinish = () => onda.remove();
  }, { passive: true });

  /* ---------- riflesso di luce che segue il mouse sulle superfici di vetro ---------- */
  document.addEventListener("pointermove", e => {
    const el = e.target.closest?.(".riquadro, .processi-card, .blocco-sx, .glass, .chip, .btn.primario");
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    el.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  }, { passive: true });

  /* ---------- indicatore che scorre sotto la voce attiva ---------- */
  const GRUPPI = [
    { contenitore: ".topnav", voce: "a", attiva: ".attivo", classe: "indicatore-linea" },
    { contenitore: ".tabs", voce: "a, button", attiva: ".attivo", classe: "indicatore-linea" },
    { contenitore: ".switch-processi", voce: "button", attiva: ".attivo", classe: "indicatore-pillola" },
    { contenitore: ".switch-schede", voce: "a", attiva: ".attivo", classe: "indicatore-pillola" },
  ];

  function indicatori() {
    for (const g of GRUPPI) {
      document.querySelectorAll(g.contenitore).forEach(box => {
        const attiva = box.querySelector(`:scope > ${g.voce.split(",").map(v => v.trim() + g.attiva).join(", :scope > ")}`);
        let ind = box.querySelector(":scope > .indicatore");
        if (!attiva) { ind?.remove(); return; }
        const nuovo = !ind;
        if (nuovo) {
          ind = document.createElement("span");
          ind.className = `indicatore ${g.classe}`;
          box.appendChild(ind);
          box.classList.add("con-indicatore");
        }
        const x = attiva.offsetLeft, w = attiva.offsetWidth;
        if (nuovo || fermo()) {
          ind.style.transition = "none";
          ind.style.transform = `translateX(${x}px)`;
          ind.style.width = `${w}px`;
          if (!fermo() && nuovo) ind.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300 });
          requestAnimationFrame(() => { ind.style.transition = ""; });
        } else {
          ind.style.transform = `translateX(${x}px)`;
          ind.style.width = `${w}px`;
        }
      });
    }
  }
  addEventListener("resize", () => requestAnimationFrame(indicatori));

  /* ---------- entrate a cascata ---------- */
  function entrata(elementi, { passo = 35, max = 14 } = {}) {
    if (fermo()) return;
    [...elementi].forEach((el, i) => {
      el.animate([{ opacity: 0, transform: "translateY(10px) scale(.985)" }, { opacity: 1, transform: "none" }],
        { duration: 420, delay: Math.min(i, max) * passo, easing: MORBIDO, fill: "backwards" });
    });
  }

  /* ---------- numeri che salgono (sessioni, percentuali) ---------- */
  function numeri(radice) {
    if (fermo()) return;
    radice.querySelectorAll(".grande, .torta-valore").forEach(el => {
      const testo = el.textContent.trim();
      const m = /^(\d+(?:,\d+)?)(%?)$/.exec(testo);
      if (!m) return;
      const fine = parseFloat(m[1].replace(",", "."));
      const decimali = m[1].includes(",") ? m[1].split(",")[1].length : 0;
      const inizio = performance.now(), durata = 800;
      const passo = t => {
        const k = Math.min(1, (t - inizio) / durata);
        const v = fine * (1 - Math.pow(1 - k, 3));
        el.textContent = v.toLocaleString("it-IT", { minimumFractionDigits: decimali, maximumFractionDigits: decimali }) + m[2];
        if (k < 1) requestAnimationFrame(passo); else el.textContent = testo;
      };
      requestAnimationFrame(passo);
    });
  }

  /* ---------- domande d'esempio che galleggiano (animazione infinita) ---------- */
  function galleggia(elementi) {
    if (fermo()) return;
    [...elementi].forEach((el, i) => {
      const ampiezza = 4 + (i % 3);
      el.animate([
        { transform: "translateY(0)" },
        { transform: `translateY(-${ampiezza}px)` },
        { transform: "translateY(0)" },
      ], { duration: 2800 + i * 450, delay: i * 260, iterations: Infinity, easing: "ease-in-out" });
    });
  }

  /* ---------- dopo ogni cambio pagina ---------- */
  function dopoPagina(vista) {
    indicatori();
    if (fermo()) return;
    vista.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
      { duration: 320, easing: MORBIDO });
    entrata(vista.querySelectorAll(".riquadro, .processi-card, .blocco-sx, .sessione-voce, .elenco-voce, .card, .risultato, .kcard-sessione, .colonna"));
    numeri(vista);
  }

  /* ---------- chat ---------- */
  function messaggio(el, rivela = false) {
    if (fermo() || !el) return;
    const utente = el.classList.contains("utente");
    el.animate([
      { opacity: 0, transform: `translate(${utente ? 14 : -14}px, 8px) scale(.96)` },
      { opacity: 1, transform: "none" },
    ], { duration: 380, easing: MOLLA });
    if (!rivela) return;
    // la risposta compare un pezzo alla volta, come se venisse scritta
    const pezzi = el.querySelectorAll(".bolla > *, .bolla li, .bolla tr, .fonti .fonte, .msg-meta");
    pezzi.forEach((p, i) => p.animate(
      [{ opacity: 0, transform: "translateY(6px)", filter: "blur(3px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }],
      { duration: 360, delay: 120 + Math.min(i, 40) * 45, easing: MORBIDO, fill: "backwards" }));
  }

  /* ---------- notifiche, dialoghi ---------- */
  function toast(el) {
    if (fermo()) return;
    el.animate([{ opacity: 0, transform: "translateY(16px) scale(.95)" }, { opacity: 1, transform: "none" }],
      { duration: 380, easing: MOLLA });
  }
  function via(el) {
    if (fermo()) return Promise.resolve();
    return el.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(24px) scale(.96)" }],
      { duration: 260, easing: "ease-in", fill: "forwards" }).finished.catch(() => {});
  }
  function dialogo(dlg) {
    if (fermo()) return;
    dlg.animate([{ opacity: 0, transform: "translateY(14px) scale(.95)" }, { opacity: 1, transform: "none" }],
      { duration: 340, easing: MOLLA });
  }

  /* ---------- visualizzatore audio ----------
   * Le barre salgono e scendono di continuo, il picco scorre verso destra. */
  function audioVivo(box) {
    if (!box) return;
    const barre = [...box.querySelectorAll("i")];
    const n = barre.length;
    // opacità di base, molto tenue, e sfumata verso i lati: trasparente a sinistra e a destra, piena al centro
    const bordo = i => Math.pow(Math.sin(Math.PI * (i + .5) / n), 1.6);
    const base = barre.map((b, i) => [.36, .24, .14][i % 3] * bordo(i));
    const forma = barre.map(b => parseFloat(b.style.getPropertyValue("--a")) / 100 || .6);
    if (fermo()) {
      barre.forEach((b, i) => { b.style.transform = `scaleY(${forma[i] * .5})`; b.style.opacity = base[i]; });
      return;
    }
    // sempre in movimento: niente pause né dissolvenze
    function passo(t) {
      if (!box.isConnected) return;     // pagina cambiata: l'animazione si ferma
      const s = t / 1000;
      barre.forEach((b, i) => {
        // onda che scorre verso destra + un po' di irregolarità come una voce vera
        const onda = .5 + .5 * Math.sin(s * 3.1 - i * 0.32);
        const vibrato = .5 + .5 * Math.sin(s * 7.3 + i * 1.7) * Math.sin(s * 2.2 + i * .45);
        const ampiezza = forma[i] * (.25 + .75 * onda * (.55 + .45 * vibrato));
        b.style.transform = `scaleY(${(.045 + ampiezza).toFixed(3)})`;
        b.style.opacity = base[i].toFixed(3);
      });
      requestAnimationFrame(passo);
    }
    requestAnimationFrame(passo);
  }

  window.effetti = { indicatori, entrata, numeri, galleggia, dopoPagina, messaggio, toast, via, dialogo, audioVivo };
})();
