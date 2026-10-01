// =====================================================================
//  zustand.js - was die Oberfläche gerade weiss, und das Erscheinungsbild
// =====================================================================

const abonnenten = new Set();

export const zustand = {
  profil: null,              // {id, name, avatar_farbe}
  einstellungen: {},
  bereich: "chats",          // "chats" | "communities" | "werkzeuge"
  seite: "chat",             // "chat" | "werkzeug" | "einstellungen"
  aktiv: null,               // id der offenen Unterhaltung
  werkzeug: null,            // id des offenen Werkzeugs
  communityOffen: null,      // id der Community, deren Kanäle die Leiste zeigt
  unterhaltungen: [],
  communities: [],
  anfragen: [],
  verbindung: { lan: "aus", discord: "aus" },
  suche: "",
};

export function setzen(teil) {
  Object.assign(zustand, teil);
  for (const fn of abonnenten) {
    try { fn(teil); } catch (e) { console.error(e); }
  }
}

export function abonnieren(fn) {
  abonnenten.add(fn);
  return () => abonnenten.delete(fn);
}

/* -------------------------------------------------------- Akzentfarben */
// Apples Systemfarben aus iOS/macOS 26: [hell, dunkel, Name]. Die Werte
// hier sind nur für die Farbtupfer in den Einstellungen und für Avatare.
// Was auf dem Bildschirm wirklich steht - Füllung, Schrift darauf, Akzent
// als Textfarbe -, steht in tokens.css unter [data-akzent], damit der
// Kontrast an EINER Stelle stimmt (ui_pruefung.py misst ihn).
export const AKZENTE = {
  blau:   ["#0088FF", "#0091FF", "Blau"],
  indigo: ["#6155F5", "#6D7CFF", "Indigo"],
  lila:   ["#CB30E0", "#DB34F2", "Lila"],
  pink:   ["#FF2D55", "#FF375F", "Pink"],
  rot:    ["#FF383C", "#FF4245", "Rot"],
  orange: ["#FF8D28", "#FF9230", "Orange"],
  gruen:  ["#34C759", "#30D158", "Grün"],
  mint:   ["#00C8B3", "#00DAC3", "Mint"],
};

export const TAPETEN = {
  tahoe: "Tahoe", abend: "Abend", wald: "Wald", mitternacht: "Mitternacht", graphit: "Graphit",
};

const dunkelAbfrage = window.matchMedia("(prefers-color-scheme: dark)");
const ruhigAbfrage = window.matchMedia("(prefers-reduced-motion: reduce)");

export function istDunkel() {
  return document.documentElement.dataset.theme === "dark";
}

/** Bewegung reduziert - vom System ODER in den Einstellungen. */
export function bewegungReduziert() {
  return ruhigAbfrage.matches || document.documentElement.dataset.bewegung === "aus";
}

/** "smooth" oder "auto" für scrollTo/scrollIntoView. */
export function scrollArt() {
  return bewegungReduziert() ? "auto" : "smooth";
}

export function erscheinungAnwenden(e = zustand.einstellungen) {
  const wurzel = document.documentElement;
  const design = e.design || "system";
  const dunkel = design === "dark" || (design === "system" && dunkelAbfrage.matches);
  wurzel.dataset.theme = dunkel ? "dark" : "light";
  wurzel.dataset.akzent = AKZENTE[e.farbe] ? e.farbe : "blau";

  const tapete = e.tapete || "tahoe";
  if (tapete === "tahoe") delete wurzel.dataset.tapete;
  else wurzel.dataset.tapete = tapete;
  wurzel.dataset.transparenz = e.transparenz_reduzieren ? "aus" : "an";
  wurzel.dataset.bewegung = e.bewegung_reduzieren ? "aus" : "an";
  wurzel.dataset.glas = e.glas_stark ? "stark" : "normal";
}

dunkelAbfrage.addEventListener("change", () => erscheinungAnwenden());
