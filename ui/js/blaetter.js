// =====================================================================
//  blaetter.js - Dialoge (Blätter), Kontextmenüs, Hinweise
// =====================================================================

import { h, ic, knopf } from "./dom.js";

/* ------------------------------------------------------------- Hinweise */
export function toast(text, art = "ok", dauerMs = 3200) {
  if (!text) return;   // z. B. ein abgebrochener Dateidialog
  const symbol = art === "fehler" ? "circle-alert" : art === "info" ? "info" : "circle-check";
  const el = h("div.toast.glas." + art, { role: "status" }, ic(symbol), h("span", { text }));
  document.getElementById("toasts").append(el);
  setTimeout(() => {
    el.classList.add("weg");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, dauerMs);
}

/* ---------------------------------------------------------------- Blätter */
let offenesBlatt = null;

/**
 * Öffnet ein Blatt. inhalt(schliessen) liefert die Kinder.
 * Esc und Klick daneben schliessen es (ausser bei zwingend=true).
 */
export function blatt(inhalt, { breite = null, zwingend = false, beimSchliessen = null } = {}) {
  if (offenesBlatt) offenesBlatt.schliessen();
  const dunkel = h("div.abdunkler");
  const karte = h("div.blatt", { role: "dialog", "aria-modal": "true" });
  if (breite) karte.style.setProperty("width", `min(${breite}px, calc(100vw - 48px))`);
  let zu = false;
  const schliessen = (wert) => {
    if (zu) return;
    zu = true;
    karte.classList.add("weg");
    dunkel.classList.add("weg");
    document.removeEventListener("keydown", taste, true);
    setTimeout(() => dunkel.remove(), 260);
    if (offenesBlatt?.karte === karte) offenesBlatt = null;
    beimSchliessen?.(wert);
  };
  const taste = (e) => {
    if (e.key === "Escape" && !zwingend) { e.stopPropagation(); schliessen(null); }
  };
  document.addEventListener("keydown", taste, true);
  dunkel.addEventListener("mousedown", (e) => { if (e.target === dunkel && !zwingend) schliessen(null); });
  const kinder = inhalt(schliessen);
  karte.append(...[kinder].flat().filter(Boolean));
  dunkel.append(karte);
  document.body.append(dunkel);
  offenesBlatt = { karte, schliessen };
  setTimeout(() => karte.querySelector("input, textarea, [data-fokus]")?.focus(), 60);
  return { karte, schliessen };
}

/** Ja/Nein-Frage. Gibt ein Promise mit true/false. */
export function bestaetigen(titel, text, { ja = "OK", nein = "Abbrechen", gefahr = false, symbol = "info" } = {}) {
  return new Promise((fertig) => {
    blatt((schliessen) => [
      h("div.kopfsymbol", { style: gefahr ? { background: "rgba(255,56,60,.12)", color: "var(--rot)" } : {} }, ic(symbol, "ic-20")),
      h("h2", { text: titel }),
      text ? h("p", { text }) : null,
      h("div.aktionen",
        knopf(nein, () => schliessen(false)),
        knopf(ja, () => schliessen(true), gefahr ? "gefahr" : "primaer")),
    ], { breite: 420, beimSchliessen: (w) => fertig(!!w) });
  });
}

/** Ein Textfeld abfragen. */
export function eingabe(titel, text, { platzhalter = "", wert = "", ok = "OK", mono = false, mehrzeilig = false, pruefen = null } = {}) {
  return new Promise((fertig) => {
    blatt((schliessen) => {
      const feld = h(mehrzeilig ? "textarea.feld" : "input.feld" + (mono ? ".mono" : ""),
        { placeholder: platzhalter, value: wert, rows: mehrzeilig ? 4 : undefined, spellcheck: "false" });
      const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "6px" } });
      const absenden = async () => {
        const v = feld.value.trim();
        if (!v) return;
        if (pruefen) {
          const meldung = await pruefen(v);
          if (meldung) { fehler.textContent = meldung; feld.classList.add("fehler"); return; }
        }
        schliessen(v);
      };
      feld.addEventListener("keydown", (e) => { if (e.key === "Enter" && !mehrzeilig) absenden(); });
      return [
        h("h2", { text: titel }),
        text ? h("p", { text }) : null,
        feld, fehler,
        h("div.aktionen", knopf("Abbrechen", () => schliessen(null)), knopf(ok, absenden, "primaer")),
      ];
    }, { breite: 460, beimSchliessen: (w) => fertig(w ?? null) });
  });
}

/* ---------------------------------------------------------- Kontextmenü */
let offenesMenue = null;

export function menueSchliessen() {
  if (!offenesMenue) return;
  offenesMenue.remove();
  offenesMenue = null;
  document.removeEventListener("mousedown", aussenKlick, true);
  document.removeEventListener("keydown", menueTaste, true);
}
function aussenKlick(e) { if (offenesMenue && !offenesMenue.contains(e.target)) menueSchliessen(); }
function menueTaste(e) {
  if (!offenesMenue) return;
  const knoepfe = [...offenesMenue.querySelectorAll("button")];
  const i = knoepfe.indexOf(document.activeElement);
  if (e.key === "Escape") { e.stopPropagation(); menueSchliessen(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); knoepfe[(i + 1) % knoepfe.length]?.focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); knoepfe[(i - 1 + knoepfe.length) % knoepfe.length]?.focus(); }
}

/**
 * Menü an einer Stelle öffnen.
 * eintraege: [{text, symbol, aktion, gefahr}] | "-" | Element
 */
export function menue(x, y, eintraege, { klasse = "" } = {}) {
  menueSchliessen();
  const el = h("div.menue.glas.glas-stark" + (klasse ? "." + klasse : ""), { role: "menu" });
  for (const e of eintraege) {
    if (!e) continue;
    if (e === "-") { el.append(h("hr")); continue; }
    if (e instanceof Node) { el.append(e); continue; }
    el.append(h("button" + (e.gefahr ? ".gefahr" : ""), {
      type: "button", role: "menuitem",
      onclick: () => { menueSchliessen(); e.aktion?.(); },
    }, e.symbol ? ic(e.symbol, "ic-16") : null, e.text));
  }
  document.body.append(el);
  // Im Fenster halten
  // offsetWidth statt getBoundingClientRect: die Einblend-Animation
  // skaliert das Menü gerade, die echte Grösse ist grösser.
  const breite = el.offsetWidth, hoehe = el.offsetHeight;
  const links = Math.min(x, innerWidth - breite - 10);
  const oben = y + hoehe > innerHeight - 10 ? Math.max(10, y - hoehe) : y;
  el.style.setProperty("left", `${Math.max(10, links)}px`);
  el.style.setProperty("top", `${oben}px`);
  el.style.setProperty("--ursprung", `${x - links}px ${y - oben}px`);
  offenesMenue = el;
  setTimeout(() => {
    document.addEventListener("mousedown", aussenKlick, true);
    document.addEventListener("keydown", menueTaste, true);
  });
  return el;
}

/** Menü unter einem Element öffnen */
export function menueAn(anker, eintraege, optionen) {
  const r = anker.getBoundingClientRect();
  return menue(r.left, r.bottom + 6, eintraege, optionen);
}
