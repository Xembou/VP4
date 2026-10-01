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
// Apples Systemfarben aus iOS/macOS 26: [hell, dunkel, Text darauf]
// Die Textfarbe steht ausdrücklich dabei: auf Mint und Orange ist Weiss
// grenzwertig, deshalb bekommen sie dunklen Text.
export const AKZENTE = {
  blau:   ["#0088FF", "#0091FF", "#FFFFFF", "Blau"],
  indigo: ["#6155F5", "#6D7CFF", "#FFFFFF", "Indigo"],
  lila:   ["#CB30E0", "#DB34F2", "#FFFFFF", "Lila"],
  pink:   ["#FF2D55", "#FF375F", "#FFFFFF", "Pink"],
  rot:    ["#FF383C", "#FF4245", "#FFFFFF", "Rot"],
  orange: ["#FF8D28", "#FF9230", "#1C1C1E", "Orange"],
  gruen:  ["#34C759", "#30D158", "#FFFFFF", "Grün"],
  mint:   ["#00C8B3", "#00DAC3", "#0B2E2A", "Mint"],
};

export const TAPETEN = {
  tahoe: "Tahoe", abend: "Abend", wald: "Wald", mitternacht: "Mitternacht", graphit: "Graphit",
};

const dunkelAbfrage = window.matchMedia("(prefers-color-scheme: dark)");

export function istDunkel() {
  return document.documentElement.dataset.theme === "dark";
}

function hexZuRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function erscheinungAnwenden(e = zustand.einstellungen) {
  const wurzel = document.documentElement;
  const design = e.design || "system";
  const dunkel = design === "dark" || (design === "system" && dunkelAbfrage.matches);
  wurzel.dataset.theme = dunkel ? "dark" : "light";

  const [hell, dunkelFarbe, textDarauf] = AKZENTE[e.farbe] || AKZENTE.blau;
  const farbe = dunkel ? dunkelFarbe : hell;
  const [r, g, b] = hexZuRgb(farbe);
  wurzel.style.setProperty("--akzent", farbe);
  wurzel.style.setProperty("--akzent-text", textDarauf);
  wurzel.style.setProperty("--akzent-weich", `rgba(${r}, ${g}, ${b}, ${dunkel ? 0.22 : 0.13})`);
  wurzel.style.setProperty("--akzent-ring", `rgba(${r}, ${g}, ${b}, .32)`);

  const tapete = e.tapete || "tahoe";
  if (tapete === "tahoe") delete wurzel.dataset.tapete;
  else wurzel.dataset.tapete = tapete;
  wurzel.dataset.transparenz = e.transparenz_reduzieren ? "aus" : "an";
  wurzel.dataset.bewegung = e.bewegung_reduzieren ? "aus" : "an";
  wurzel.dataset.glas = e.glas_stark ? "stark" : "normal";
}

dunkelAbfrage.addEventListener("change", () => erscheinungAnwenden());
