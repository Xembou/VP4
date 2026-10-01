// =====================================================================
//  blaetter.js - Dialoge (Blätter), Kontextmenüs, Hinweise
// =====================================================================
//  Tastatur: Esc schliesst Blatt und Menü, Tab bleibt im Blatt, im Menü
//  gehen ↑/↓/Pos1/Ende. Nach dem Schliessen kommt der Fokus dorthin
//  zurück, wo er vorher war.
// =====================================================================

import { h, ic, knopf } from "./dom.js";

/* ------------------------------------------------------------- Hinweise */
/**
 * toast("Text") - kurzer Hinweis unten in der Mitte.
 * Mit {aktion} wird er anklickbar (z. B. "Neue Nachricht von Max").
 */
export function toast(text, art = "ok", dauerMs = 3200, { titel = null, aktion = null } = {}) {
  if (!text) return;   // z. B. ein abgebrochener Dateidialog
  const symbol = art === "fehler" ? "circle-alert" : art === "info" ? "info" : art === "nachricht" ? "message-circle" : "circle-check";
  const inhalt = [ic(symbol), h("span", titel ? [h("b", { text: titel }), " ", text] : text)];
  const el = aktion
    ? h("button.toast.glas.glas-stark.klickbar." + art, { type: "button", role: "status" }, inhalt)
    : h("div.toast.glas.glas-stark." + art, { role: art === "fehler" ? "alert" : "status" }, inhalt);
  const weg = () => {
    if (el.classList.contains("weg")) return;
    el.classList.add("weg");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 600);
  };
  if (aktion) el.addEventListener("click", () => { weg(); aktion(); });
  const ablage = document.getElementById("toasts");
  // Nicht mehr als drei auf einmal - sonst stapeln sie sich über den Chat
  while (ablage.children.length >= 3) ablage.firstElementChild.remove();
  ablage.append(el);
  setTimeout(weg, dauerMs);
  return el;
}

/* ---------------------------------------------------------------- Blätter */
let offenesBlatt = null;
let blattZaehler = 0;

const FOKUSSIERBAR = 'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select, [tabindex]:not([tabindex="-1"])';

/**
 * Öffnet ein Blatt. inhalt(schliessen) liefert die Kinder.
 * Esc und Klick daneben schliessen es (ausser bei zwingend=true).
 */
export function blatt(inhalt, { breite = null, zwingend = false, beimSchliessen = null, klasse = "", oben = false, label = null } = {}) {
  if (offenesBlatt) offenesBlatt.schliessen();
  const vorherFokus = document.activeElement;
  const dunkel = h("div.abdunkler" + (oben ? ".oben" : ""));
  const kennung = `blatt-titel-${++blattZaehler}`;
  const karte = h("div.blatt" + (klasse ? "." + klasse.split(" ").join(".") : ""), { role: "dialog", "aria-modal": "true" });
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
    if (vorherFokus?.isConnected && typeof vorherFokus.focus === "function") vorherFokus.focus({ preventScroll: true });
    beimSchliessen?.(wert);
  };
  const taste = (e) => {
    // Ein Menü über dem Blatt (z. B. "⋯" einer Zeile) schliesst Esc zuerst
    if (offenesMenue) return;
    if (e.key === "Escape" && !zwingend) { e.stopPropagation(); e.preventDefault(); schliessen(null); return; }
    if (e.key === "Tab") {
      // Den Fokus im Blatt halten
      const ziele = [...karte.querySelectorAll(FOKUSSIERBAR)].filter((x) => x.offsetParent !== null);
      if (!ziele.length) return;
      const erstes = ziele[0], letztes = ziele[ziele.length - 1];
      if (e.shiftKey && (document.activeElement === erstes || !karte.contains(document.activeElement))) { e.preventDefault(); letztes.focus(); }
      else if (!e.shiftKey && (document.activeElement === letztes || !karte.contains(document.activeElement))) { e.preventDefault(); erstes.focus(); }
    }
  };
  document.addEventListener("keydown", taste, true);
  dunkel.addEventListener("mousedown", (e) => { if (e.target === dunkel && !zwingend) schliessen(null); });
  const kinder = inhalt(schliessen);
  karte.append(...[kinder].flat(Infinity).filter(Boolean));
  const titel = karte.querySelector("h2");
  if (label) karte.setAttribute("aria-label", label);
  else if (titel) { titel.id = kennung; karte.setAttribute("aria-labelledby", kennung); }
  dunkel.append(karte);
  document.body.append(dunkel);
  offenesBlatt = { karte, schliessen };
  setTimeout(() => {
    const ziel = karte.querySelector("[data-fokus], input, textarea") || karte.querySelector(".aktionen .knopf.primaer") || karte.querySelector(FOKUSSIERBAR);
    ziel?.focus();
  }, 60);
  return { karte, schliessen };
}

export function blattOffen() { return !!offenesBlatt; }

/** Ja/Nein-Frage. Gibt ein Promise mit true/false. */
export function bestaetigen(titel, text, { ja = "OK", nein = "Abbrechen", gefahr = false, symbol = "info" } = {}) {
  return new Promise((fertig) => {
    blatt((schliessen) => [
      h("div.kopfsymbol" + (gefahr ? ".gefahr" : ""), ic(symbol, "ic-20")),
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
      const fehler = h("div.fehlertext", { role: "alert" });
      const feld = h(mehrzeilig ? "textarea.feld" : "input.feld" + (mono ? ".mono" : ""),
        { placeholder: platzhalter, value: wert, rows: mehrzeilig ? 4 : undefined, spellcheck: "false", "aria-label": titel });
      const absenden = async () => {
        const v = feld.value.trim();
        if (!v) return;
        if (pruefen) {
          const meldung = await pruefen(v);
          if (meldung) { fehler.textContent = meldung; feld.classList.add("fehler"); feld.setAttribute("aria-invalid", "true"); return; }
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
let menueVorherFokus = null;

export function menueSchliessen({ fokusZurueck = false } = {}) {
  if (!offenesMenue) return;
  offenesMenue.remove();
  offenesMenue = null;
  document.removeEventListener("mousedown", aussenKlick, true);
  document.removeEventListener("keydown", menueTaste, true);
  if (fokusZurueck && menueVorherFokus?.isConnected) menueVorherFokus.focus({ preventScroll: true });
  menueVorherFokus = null;
}
function aussenKlick(e) { if (offenesMenue && !offenesMenue.contains(e.target)) menueSchliessen(); }
function menueTaste(e) {
  if (!offenesMenue) return;
  if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); menueSchliessen({ fokusZurueck: true }); return; }
  // In einem Suchfeld im Menü (Emoji-Auswahl) gehören die Tasten dem Feld
  if (e.target.matches?.("input, textarea") && !["ArrowDown", "ArrowUp"].includes(e.key)) return;
  const knoepfe = [...offenesMenue.querySelectorAll("button")];
  const i = knoepfe.indexOf(document.activeElement);
  const geh = (j) => { e.preventDefault(); knoepfe[(j + knoepfe.length) % knoepfe.length]?.focus(); };
  if (e.key === "ArrowDown") geh(i + 1);
  else if (e.key === "ArrowUp") geh(i < 0 ? -1 : i - 1);
  else if (e.key === "Home") geh(0);
  else if (e.key === "End") geh(-1);
  else if (e.key === "Tab") { e.preventDefault(); menueSchliessen({ fokusZurueck: true }); }
}

/**
 * Menü an einer Stelle öffnen.
 * eintraege: [{text, symbol, aktion, gefahr, tasten}] | "-" | Element
 */
export function menue(x, y, eintraege, { klasse = "", label = "Menü" } = {}) {
  const vorher = document.activeElement;
  menueSchliessen();
  menueVorherFokus = vorher;
  const el = h("div.menue.glas.glas-stark" + (klasse ? "." + klasse : ""), { role: "menu", "aria-label": label, tabindex: "-1" });
  for (const e of eintraege) {
    if (!e) continue;
    if (e === "-") { el.append(h("hr", { role: "separator" })); continue; }
    if (e instanceof Node) { el.append(e); continue; }
    el.append(h("button" + (e.gefahr ? ".gefahr" : ""), {
      type: "button", role: "menuitem",
      onclick: () => { menueSchliessen({ fokusZurueck: true }); e.aktion?.(); },
    }, e.symbol ? ic(e.symbol, "ic-16") : null, h("span", { text: e.text }), e.tasten ? h("span.tasten", { text: e.tasten }) : null));
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
  // Fokus ins Menü, damit die Pfeiltasten sofort gehen. Mit der Maus
  // geöffnet sieht man keinen Ring (focus-visible), mit der Tastatur schon.
  el.focus({ preventScroll: true });
  setTimeout(() => {
    document.addEventListener("mousedown", aussenKlick, true);
    document.addEventListener("keydown", menueTaste, true);
  });
  return el;
}

/** Menü unter einem Element öffnen */
export function menueAn(anker, eintraege, optionen) {
  const r = anker.getBoundingClientRect();
  const el = menue(r.left, r.bottom + 6, eintraege, optionen);
  anker.setAttribute?.("aria-expanded", "true");
  const beobachter = new MutationObserver(() => {
    if (!el.isConnected) { anker.setAttribute?.("aria-expanded", "false"); beobachter.disconnect(); }
  });
  beobachter.observe(document.body, { childList: true });
  return el;
}
