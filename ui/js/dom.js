// =====================================================================
//  dom.js - Elemente bauen, OHNE innerHTML
// =====================================================================
//  Alles, was aus einer Nachricht, einem Namen oder einer Datei kommt,
//  wird hier als Text eingesetzt, nie als HTML. Hinter der Oberfläche
//  hängt Python mit allen Schlüsseln; ein "<img onerror=...>" in einer
//  Chatnachricht darf nur als Text erscheinen. innerHTML gibt es im
//  ganzen Projekt nicht - ein Test sucht danach.
// =====================================================================

const SVG = "http://www.w3.org/2000/svg";

/**
 * h("div.klasse.zweite", {attribute}, ...kinder)
 *   - on...: Ereignis (onclick, oninput ...)
 *   - style: Objekt, wird per CSSOM gesetzt (erlaubt die strenge CSP)
 *   - dataset, aria-*, alles andere: setAttribute
 *   - kinder: Elemente, Texte (werden zu Textknoten), Arrays, null/false
 */
export function h(kennung, attribute = {}, ...kinder) {
  const [tag, ...klassen] = kennung.split(".");
  const el = document.createElement(tag || "div");
  if (klassen.length) el.classList.add(...klassen);
  // Die Attribute dürfen fehlen: h("div", kind1, kind2) geht genauso.
  const istAttribute = attribute !== null && typeof attribute === "object"
    && !(attribute instanceof Node) && !Array.isArray(attribute);
  if (istAttribute) setzen(el, attribute);
  else kinder.unshift(attribute);
  anhaengen(el, kinder);
  return el;
}

export function setzen(el, attribute) {
  if (!attribute) return el;
  for (const [k, v] of Object.entries(attribute)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2), v);
    } else if (k === "style") {
      for (const [s, w] of Object.entries(v)) el.style.setProperty(s, w);
    } else if (k === "dataset") {
      Object.assign(el.dataset, v);
    } else if (k === "klasse") {
      for (const c of String(v).split(" ").filter(Boolean)) el.classList.add(c);
    } else if (k === "text") {
      el.textContent = String(v);
    } else if (k === "value") {
      el.value = v;
    } else if (v === true) {
      el.setAttribute(k, "");
    } else {
      el.setAttribute(k, String(v));
    }
  }
  return el;
}

export function anhaengen(el, kinder) {
  for (const k of kinder.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

/** Ein Symbol aus dem Lucide-Sprite. */
export function ic(name, klasse = "") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", ("ic " + klasse).trim());
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", `icons/sprite.svg#${name}`);
  svg.append(use);
  return svg;
}

/** Fortschrittsring (0..1). ring.setzen(anteil) ändert ihn später. */
export function ring(anteil = 0, klasse = "") {
  const r = 17, umfang = 2 * Math.PI * r;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", ("ring " + klasse).trim());
  svg.setAttribute("viewBox", "0 0 40 40");
  svg.setAttribute("role", "progressbar");
  svg.setAttribute("aria-valuemin", "0");
  svg.setAttribute("aria-valuemax", "100");
  const kreis = (k) => {
    const c = document.createElementNS(SVG, "circle");
    c.setAttribute("class", k);
    c.setAttribute("cx", "20"); c.setAttribute("cy", "20"); c.setAttribute("r", String(r));
    return c;
  };
  const spur = kreis("spur");
  const wert = kreis("wert");
  wert.setAttribute("stroke-dasharray", String(umfang));
  svg.append(spur, wert);
  svg.setzen = (a) => {
    const x = Math.max(0, Math.min(1, Number(a) || 0));
    // ein winziger Rest bleibt sichtbar, damit man sieht, dass etwas läuft
    wert.setAttribute("stroke-dashoffset", String(umfang * (1 - Math.max(x, 0.03))));
    svg.setAttribute("aria-valuenow", String(Math.round(x * 100)));
  };
  svg.setzen(anteil);
  return svg;
}

/** Text mit hervorgehobenem Treffer (für die Schnellwahl) - als Knoten. */
export function hervorheben(text, suche) {
  const t = String(text || "");
  const q = (suche || "").trim().toLowerCase();
  const i = q ? t.toLowerCase().indexOf(q) : -1;
  if (i < 0) return [t];
  return [t.slice(0, i), h("mark", { text: t.slice(i, i + q.length) }), t.slice(i + q.length)];
}

export function leeren(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export function ersetzen(el, ...kinder) {
  leeren(el);
  return anhaengen(el, kinder);
}

/** Knopf mit Symbol, z. B. rundknopf("send", "Senden", fn) */
export function rundknopf(symbol, titel, aktion, klasse = "") {
  return h("button.rund" + (klasse ? "." + klasse.split(" ").join(".") : ""),
    { type: "button", title: titel, "aria-label": titel, onclick: aktion }, ic(symbol));
}

export function knopf(text, aktion, art = "", symbol = null) {
  const klassen = ["knopf", ...art.split(" ").filter(Boolean)].join(".");
  return h("button." + klassen, { type: "button", onclick: aktion }, symbol ? ic(symbol) : null, text);
}

/** Text mit erkannten Links - Links werden als echte <a>-Knoten gebaut. */
const LINK = /\bhttps?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]]/g;
export function textMitLinks(text) {
  const teile = [];
  let letzte = 0;
  for (const treffer of text.matchAll(LINK)) {
    if (treffer.index > letzte) teile.push(text.slice(letzte, treffer.index));
    const url = treffer[0];
    teile.push(h("a", { href: url, "data-extern": "1", rel: "noreferrer", text: url }));
    letzte = treffer.index + url.length;
  }
  if (letzte < text.length) teile.push(text.slice(letzte));
  return teile;
}

/** Die Farbe zu einer ID - immer gleich für dieselbe ID. */
const AVATAR_FARBEN = ["#0088FF", "#6155F5", "#CB30E0", "#FF2D55", "#FF8D28",
  "#34C759", "#00C3D0", "#00C8B3", "#AC7F5E", "#8E8E93"];
export function farbeFuer(id = "") {
  let x = 0;
  for (const z of id) x = (x * 31 + z.charCodeAt(0)) >>> 0;
  return AVATAR_FARBEN[x % AVATAR_FARBEN.length];
}

export function initialen(name = "?") {
  const teile = name.trim().split(/\s+/).filter(Boolean);
  if (!teile.length) return "?";
  const erste = [...teile[0]][0] || "?";
  const zweite = teile.length > 1 ? [...teile[teile.length - 1]][0] : "";
  return (erste + zweite).toUpperCase();
}

export function avatar(name, farbe, groesse = "", online = false, bild = null, community = false) {
  const el = h("div.avatar" + (groesse ? "." + groesse : "") + (community ? ".community" : ""),
    { style: { "--farbe": farbe || farbeFuer(name) }, "aria-hidden": "true" });
  if (bild) el.append(h("img", { src: bild, alt: "" }));
  else el.textContent = community && /\p{Extended_Pictographic}/u.test(name) ? name : initialen(name);
  if (online) el.append(h("i.punkt"));
  return el;
}

/* ------------------------------------------------------------------ Zeit */
const UHR = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const WOCHENTAG = new Intl.DateTimeFormat("de-DE", { weekday: "short" });
const DATUM = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short" });
const DATUM_LANG = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "short" });

function tagesbeginn(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x.getTime(); }

export function uhrzeit(ms) { return UHR.format(new Date(ms)); }

/** Kurze Zeit für die Chatliste: 14:02 / Gestern / Mo. / 12. Sep. */
export function zeitKurz(ms) {
  if (!ms) return "";
  const heute = tagesbeginn(Date.now());
  const tag = tagesbeginn(ms);
  const tage = Math.round((heute - tag) / 86400000);
  if (tage === 0) return uhrzeit(ms);
  if (tage === 1) return "Gestern";
  if (tage < 7) return WOCHENTAG.format(new Date(ms));
  return DATUM.format(new Date(ms));
}

export function tagTitel(ms) {
  const heute = tagesbeginn(Date.now());
  const tage = Math.round((heute - tagesbeginn(ms)) / 86400000);
  if (tage === 0) return "Heute";
  if (tage === 1) return "Gestern";
  return DATUM_LANG.format(new Date(ms));
}

export function gleicherTag(a, b) { return tagesbeginn(a) === tagesbeginn(b); }

export function groesse(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2).replace(".", ",")} GB`;
}

export function dauer(ms) {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
