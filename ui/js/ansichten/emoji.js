// =====================================================================
//  emoji.js - Emoji-Auswahl mit deutscher Suche (Daten liegen lokal)
// =====================================================================

import { h, ic, leeren } from "../dom.js";
import { menue, menueSchliessen } from "../blaetter.js";

let daten = null;
const ZULETZT = "vp4.emoji.zuletzt";

async function laden() {
  if (!daten) daten = await (await fetch("emoji/emoji.json")).json();
  return daten;
}

function zuletzt() {
  try { return JSON.parse(localStorage.getItem(ZULETZT) || "[]"); } catch { return []; }
}
function merken(e) {
  try {
    const liste = [e, ...zuletzt().filter((x) => x !== e)].slice(0, 24);
    localStorage.setItem(ZULETZT, JSON.stringify(liste));
  } catch { /* privater Modus: egal */ }
}

export async function emojiWahl(anker, gewaehlt) {
  const d = await laden();
  const suche = h("input", { placeholder: "Emoji suchen", "aria-label": "Emoji suchen" });
  const gitter = h("div.gitter");
  const waehlen = (e) => { merken(e); menueSchliessen(); gewaehlt(e); };
  const knopfFuer = ([e, name]) => h("button", { type: "button", title: name, "aria-label": name, onclick: () => waehlen(e) }, e);

  const fuellen = () => {
    leeren(gitter);
    const q = suche.value.trim().toLowerCase();
    if (q) {
      const treffer = d.gruppen.flatMap((g) => g.emoji).filter(([, n]) => n.toLowerCase().includes(q)).slice(0, 160);
      gitter.append(...treffer.map(knopfFuer));
      if (!treffer.length) gitter.append(h("div.gruppe-name", { text: "Nichts gefunden" }));
      return;
    }
    const z = zuletzt();
    if (z.length) {
      gitter.append(h("div.gruppe-name", { text: "Zuletzt benutzt" }));
      gitter.append(...z.map((e) => knopfFuer([e, e])));
    }
    for (const g of d.gruppen) {
      gitter.append(h("div.gruppe-name", { text: g.name }));
      gitter.append(...g.emoji.map(knopfFuer));
    }
  };
  suche.addEventListener("input", fuellen);
  fuellen();

  const r = anker.getBoundingClientRect();
  const el = menue(r.left - 150, r.top - 350, [
    h("div.suche", ic("search", "ic-16"), suche),
    gitter,
  ], { klasse: "emojiwahl" });
  setTimeout(() => suche.focus(), 30);
  return el;
}
