// =====================================================================
//  schnellwahl.js - Strg K: zu jedem Chat, Kanal und Werkzeug springen
// =====================================================================
//  Sucht nur in dem, was die Oberfläche schon weiss (zustand.js und die
//  Werkzeugliste) - keine Anfrage an Python, also auch nichts Neues an
//  der Schnittstelle. Tastatur: ↑/↓ wählt, Enter öffnet, Esc schliesst.
//  Aufgebaut wie eine Combobox: Fokus bleibt im Feld, die gewählte Zeile
//  steht in aria-activedescendant.
// =====================================================================

import { h, ic, ersetzen, avatar, farbeFuer, hervorheben } from "../dom.js";
import { blatt } from "../blaetter.js";
import { zustand } from "../zustand.js";
import { WERKZEUGE } from "./werkzeuge.js";
import { communityUntertitel } from "./seitenleiste.js";

let offen = null;

export function schnellwahlSchliessen() { offen?.schliessen(); offen = null; }
export function schnellwahlOffen() { return !!offen; }

/** Wie gut passt text zur Suche? 0 = gar nicht. */
function punkte(text, q) {
  const t = (text || "").toLowerCase();
  if (!q) return 1;
  if (t === q) return 100;
  if (t.startsWith(q)) return 60;
  if (t.split(/[\s#\-_.,:/]+/).some((w) => w.startsWith(q))) return 40;
  if (t.includes(q)) return 20;
  return 0;
}

function eintraegeBauen(q, ziele) {
  const chats = zustand.unterhaltungen.filter((u) => u.art !== "kanal").map((u) => ({
    abschnitt: "Chats", titel: u.titel,
    unter: u.letzte?.text ? (u.letzte.ich ? `Du: ${u.letzte.text}` : u.letzte.text) : (u.art === "gruppe" ? "Gruppe" : "Noch keine Nachrichten"),
    art: u.art === "gruppe" ? "Gruppe" : "Chat",
    bild: () => avatar(u.titel, u.farbe, "mittel", u.art === "dm" && u.online),
    ungelesen: u.ungelesen || 0,
    los: () => ziele.chat(u.id),
  }));
  const kanaele = zustand.unterhaltungen.filter((u) => u.art === "kanal").map((u) => ({
    abschnitt: "Kanäle", titel: `#${u.kanal_name || u.titel}`, suchtext: u.kanal_name || u.titel,
    unter: u.community_name || "Community", art: "Kanal",
    bild: () => h("span.kachel", { style: { "--farbe": u.farbe || farbeFuer(u.community_id || u.id) } }, ic(u.nur_admins ? "megaphone" : "hash", "ic-16")),
    ungelesen: u.ungelesen || 0,
    los: () => ziele.chat(u.id),
  }));
  const communities = zustand.communities.filter((c) => !c.gruppe).map((c) => ({
    abschnitt: "Communities", titel: c.name, unter: communityUntertitel(c), art: "Community",
    bild: () => h("div.avatar.mittel.community", { style: { "--farbe": c.farbe || farbeFuer(c.id) } }, c.icon || c.name.slice(0, 1)),
    los: () => ziele.community(c.id),
  }));
  const werkzeuge = WERKZEUGE.map((w) => ({
    abschnitt: "Werkzeuge", titel: w.titel, unter: w.info, art: "Werkzeug",
    bild: () => h("span.kachel", { style: { "--farbe": w.farbe } }, ic(w.symbol, "ic-16")),
    los: () => ziele.werkzeug(w.id),
  }));
  const befehle = [
    { titel: "Freund hinzufügen", unter: "Über seine ID oder seinen Freundescode", symbol: "user-plus", tasten: "Strg N", los: ziele.freund },
    { titel: "Einstellungen", unter: "Erscheinungsbild, Chat, Sicherheit …", symbol: "settings", tasten: "Strg ,", los: ziele.einstellungen },
    { titel: "Meine ID kopieren", unter: zustand.profil?.id || "", symbol: "copy", los: ziele.idKopieren },
    { titel: "VP4 sperren", unter: "Bis zur Eingabe des Master-Passworts", symbol: "lock", tasten: "Strg L", los: ziele.sperren },
  ].filter((b) => b.los).map((b) => ({
    abschnitt: "Befehle", titel: b.titel, unter: b.unter, art: b.tasten || "",
    bild: () => h("span.kachel.hell", ic(b.symbol, "ic-16")), los: b.los,
  }));

  if (!q) {
    // Ohne Suche: die letzten Chats, ungelesene Kanäle, alle Werkzeuge
    return [...chats.slice(0, 5), ...kanaele.filter((k) => k.ungelesen).slice(0, 3), ...werkzeuge, ...befehle];
  }
  const passend = (liste) => liste
    .map((e) => ({ e, p: Math.max(punkte(e.suchtext || e.titel, q), punkte(e.unter, q) / 4) }))
    .filter((x) => x.p > 0)
    .sort((a, b) => b.p - a.p)
    .map((x) => x.e);
  return [...passend(chats), ...passend(kanaele), ...passend(communities), ...passend(werkzeuge), ...passend(befehle)].slice(0, 40);
}

/**
 * ziele: { chat(id), community(id), werkzeug(id), freund(), einstellungen(),
 *          idKopieren(), sperren() }
 */
export function schnellwahlOeffnen(ziele, { suche = "" } = {}) {
  if (offen) { offen.feld?.focus(); offen.feld?.select(); return offen; }
  let eintraege = [];
  let aktiv = 0;
  const feld = h("input", {
    type: "text", value: suche, placeholder: "Chats, Kanäle und Werkzeuge suchen",
    role: "combobox", "aria-expanded": "true", "aria-controls": "schnellwahl-liste",
    "aria-autocomplete": "list", "aria-label": "Schnellwahl", autocomplete: "off", spellcheck: "false", "data-fokus": "1",
  });
  const liste = h("div.schnellwahl-liste", { id: "schnellwahl-liste", role: "listbox", "aria-label": "Treffer" });
  const ansage = h("div.nur-vorlesen", { "aria-live": "polite" });

  const markieren = (i, scrollen = true) => {
    const zeilen = [...liste.querySelectorAll('[role="option"]')];
    if (!zeilen.length) { feld.removeAttribute("aria-activedescendant"); return; }
    aktiv = (i + zeilen.length) % zeilen.length;
    zeilen.forEach((z, j) => z.setAttribute("aria-selected", String(j === aktiv)));
    feld.setAttribute("aria-activedescendant", zeilen[aktiv].id);
    if (scrollen) zeilen[aktiv].scrollIntoView({ block: "nearest" });
  };
  const waehlen = (i) => {
    const e = eintraege[i];
    if (!e) return;
    schnellwahlSchliessen();
    e.los();
  };
  const zeichnen = () => {
    const q = feld.value.trim().toLowerCase();
    eintraege = eintraegeBauen(q, ziele);
    const teile = [];
    let abschnitt = null;
    eintraege.forEach((e, i) => {
      if (e.abschnitt !== abschnitt) {
        abschnitt = e.abschnitt;
        teile.push(h("div.abschnitt", { role: "presentation", text: abschnitt }));
      }
      teile.push(h("div.schnellwahl-zeile", {
        id: `schnellwahl-${i}`, role: "option", "aria-selected": "false",
        "aria-label": `${e.titel}, ${e.art || e.abschnitt}${e.ungelesen ? `, ${e.ungelesen} ungelesen` : ""}`,
        onmousemove: () => { if (aktiv !== i) markieren(i, false); },
        onclick: () => waehlen(i),
      },
        e.bild(),
        h("div.mitte", h("b", hervorheben(e.titel, q)), e.unter ? h("small", { text: e.unter }) : null),
        e.ungelesen ? h("span.zaehler", { text: e.ungelesen > 99 ? "99+" : String(e.ungelesen) }) : null,
        e.art ? h("span.art", { text: e.art }) : null));
    });
    if (!eintraege.length) teile.push(h("div.leer-zeile", { role: "presentation", text: `Nichts gefunden für „${feld.value.trim()}“.` }));
    ersetzen(liste, teile);
    ansage.textContent = eintraege.length ? `${eintraege.length} Treffer` : "Keine Treffer";
    markieren(0);
  };

  feld.addEventListener("input", zeichnen);
  feld.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); markieren(aktiv + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); markieren(aktiv - 1); }
    else if (e.key === "PageDown") { e.preventDefault(); markieren(Math.min(eintraege.length - 1, aktiv + 6)); }
    else if (e.key === "PageUp") { e.preventDefault(); markieren(Math.max(0, aktiv - 6)); }
    else if (e.key === "Enter") { e.preventDefault(); waehlen(aktiv); }
  });

  const b = blatt(() => [
    h("div.suchzeile", ic("search", "ic-20"), feld, h("kbd", { text: "Esc" })),
    liste,
    ansage,
    h("div.fuss", { "aria-hidden": "true" },
      h("span", h("kbd", { text: "↑" }), h("kbd", { text: "↓" }), "auswählen"),
      h("span", h("kbd", { text: "Enter" }), "öffnen"),
      h("span", h("kbd", { text: "Esc" }), "schliessen")),
  ], { breite: 600, klasse: "schnellwahl", oben: true, label: "Schnellwahl", beimSchliessen: () => { offen = null; } });
  offen = { ...b, feld };
  zeichnen();
  setTimeout(() => { feld.focus(); feld.select(); }, 20);
  return offen;
}
