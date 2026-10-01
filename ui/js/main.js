// =====================================================================
//  main.js - Start, Aufteilung, Tastenkürzel
// =====================================================================

import { h, ic, ersetzen, leeren } from "./dom.js";
import { verbinden, rufe, auf, ereignisseStarten, ereignisseStoppen } from "./bruecke.js";
import { zustand, setzen, erscheinungAnwenden } from "./zustand.js";
import { toast, menueSchliessen, blattOffen } from "./blaetter.js";
import { Seitenleiste } from "./ansichten/seitenleiste.js";
import { chatOeffnen, chatSchliessen, chatAktuell } from "./ansichten/chat.js";
import { werkzeugZeigen } from "./ansichten/werkzeuge.js";
import { einstellungenZeigen } from "./ansichten/einstellungen.js";
import { einrichtungZeigen, sperreZeigen } from "./ansichten/einrichtung.js";
import { freundHinzufuegen } from "./ansichten/kontakte.js";
import { schnellwahlOeffnen, schnellwahlSchliessen } from "./ansichten/schnellwahl.js";

const app = document.getElementById("app");
const vollbild = document.getElementById("vollbild");
let leiste = null;
let haupt = null;

async function start() {
  try {
    await verbinden();
  } catch (e) {
    vollbild.append(h("div.vollbild", h("div.einrichtung.glas", h("h1", { text: "Keine Verbindung" }), h("p.unter", { text: String(e.message || e) }))));
    return;
  }
  const s = await rufe("status");
  if (!s.ok) { toast(s.fehler, "fehler", 8000); return; }
  setzen({ einstellungen: s.einstellungen || {}, profil: s.profil });
  erscheinungAnwenden();
  if (s.phase === "einrichten") einrichtungZeigen(vollbild, s, () => appStarten());
  else if (s.phase === "gesperrt") { sperreSichtbar = true; sperreZeigen(vollbild, s, () => { sperreSichtbar = false; appStarten(); }); }
  else appStarten();
}

async function appStarten() {
  const s = await rufe("status");
  setzen({ einstellungen: s.einstellungen || {}, profil: s.profil, verbindung: s.verbindung || zustand.verbindung });
  erscheinungAnwenden();
  leeren(vollbild);
  app.classList.remove("versteckt");
  const aside = h("aside.seitenleiste.glas.bricht", { "aria-label": "Navigation" });
  haupt = h("main.haupt", { "aria-label": "Inhalt" });
  ersetzen(app, aside, haupt);
  leiste = new Seitenleiste(aside, {
    oeffnen: (id) => unterhaltungOeffnen(id),
    werkzeugOeffnen: (id) => werkzeugOeffnen(id),
    einstellungenOeffnen: () => einstellungenOeffnen(),
  });
  await listenLaden();
  leerZeigen();
  ereignisseStarten();
  if (s.update) updateBanner(s.update);
}

async function listenLaden() {
  const [u, c, a] = await Promise.all([rufe("unterhaltungen"), rufe("communities"), rufe("anfragen")]);
  setzen({
    unterhaltungen: u.ok ? u.liste : zustand.unterhaltungen,
    communities: c.ok ? c.liste : zustand.communities,
    anfragen: a.ok ? a.liste : zustand.anfragen,
  });
  // Die offene Unterhaltung bekommt den frischen Stand für ihren Kopf
  // (Schlüssel geändert, verifiziert, online) - das Ereignis
  // "kontakt_geaendert" trägt nur die ID.
  const offen = chatAktuell();
  const frisch = offen && finden(offen.u.id);
  if (frisch) offen.kopfNeu(frisch);
  leiste?.liste();
  leiste?.fussZeichnen();
  if (zustand.seite === "chat" && !zustand.aktiv) leerZeigen();
}

let neuLadenGeplant = null;
function spaeterNeuLaden(ms = 120) {
  clearTimeout(neuLadenGeplant);
  neuLadenGeplant = setTimeout(listenLaden, ms);
}

function leerZeigen() {
  if (!haupt || zustand.seite !== "chat" || zustand.aktiv) return;
  chatSchliessen();
  const hatChats = zustand.unterhaltungen.length > 0;
  ersetzen(haupt, h("div.leer.gross",
    h("div.kreis", ic("message-circle", "ic-36")),
    h("h3", { text: hatChats ? "Wähle einen Chat" : "Willkommen bei VP4" }),
    h("p", { text: hatChats ? "Links stehen deine Chats, Gruppen und Communities." : "Füge Freunde über ihre ID hinzu. Eure Nachrichten sind Ende-zu-Ende verschlüsselt." }),
    h("div.knoepfe",
      h("button.knopf.primaer", { type: "button", onclick: () => freundHinzufuegen() }, ic("user-plus"), "Freund hinzufügen"),
      h("button.knopf", { type: "button", onclick: async () => { await navigator.clipboard?.writeText(zustand.profil?.id || ""); toast("Deine ID ist kopiert"); } }, ic("copy"), "Meine ID kopieren")),
    h("p.tipp", "Schnell zu jedem Chat: ", h("kbd", { text: "Strg" }), "+", h("kbd", { text: "K" }))));
}

function finden(id) {
  return zustand.unterhaltungen.find((u) => u.id === id);
}

async function unterhaltungOeffnen(id) {
  let u = finden(id);
  if (!u) { await listenLaden(); u = finden(id); }
  if (!u) return;
  const teil = { seite: "chat", aktiv: id };
  // Ein Kanal (z. B. aus der Schnellwahl): die Leiste zeigt seine Community
  if (u.art === "kanal" && u.community_id) Object.assign(teil, { bereich: "communities", communityOffen: u.community_id });
  else if (u.art !== "kanal" && zustand.bereich === "werkzeuge") teil.bereich = "chats";
  setzen(teil);
  chatOeffnen(haupt, u);
  // Die Ansicht hat sich gemerkt, wie viel ungelesen war ("Neue Nachrichten")
  u.ungelesen = 0;
  leiste.alles();
}

function communityOeffnen(cid) {
  const c = zustand.communities.find((x) => x.id === cid);
  setzen({ bereich: "communities", communityOffen: cid });
  leiste.alles();
  const erster = c?.kanaele?.[0];
  if (erster) unterhaltungOeffnen(erster.unterhaltung);
}

function werkzeugOeffnen(id) {
  chatSchliessen();
  setzen({ seite: "werkzeug", werkzeug: id, aktiv: null, bereich: "werkzeuge" });
  werkzeugZeigen(haupt, id);
  leiste.alles();
}

function einstellungenOeffnen() {
  chatSchliessen();
  setzen({ seite: "einstellungen", aktiv: null });
  einstellungenZeigen(haupt);
  leiste.liste();
  leiste.fussZeichnen();
}

function schnellwahl() {
  menueSchliessen();
  schnellwahlOeffnen({
    chat: (id) => unterhaltungOeffnen(id),
    community: (id) => communityOeffnen(id),
    werkzeug: (id) => werkzeugOeffnen(id),
    freund: () => freundHinzufuegen(),
    einstellungen: () => einstellungenOeffnen(),
    idKopieren: async () => { await navigator.clipboard?.writeText(zustand.profil?.id || ""); toast("Deine ID ist kopiert"); },
    sperren: () => sperren(),
  });
}

window.addEventListener("vp4-werkzeug", (e) => werkzeugOeffnen(e.detail));
window.addEventListener("vp4-sperren", () => sperren());
window.addEventListener("vp4-bereich", () => leiste?.alles());

/* ------------------------------------------------------------- Sperren */
async function sperren() {
  const r = await rufe("sperren");
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  await sperreAnzeigen();
}

// Nur die Anzeige: Python hat schon gesperrt (automatische Sperre). Hier
// darf NIE noch einmal rufe("sperren") stehen - sonst holt die erste
// Abfrage nach dem Entsperren ein altes "gesperrt" ab und sperrt sofort
// wieder (Befund aus dem Code-Review).
let sperreSichtbar = false;
async function sperreAnzeigen() {
  if (sperreSichtbar) return;
  sperreSichtbar = true;
  ereignisseStoppen();
  chatSchliessen();
  menueSchliessen();
  schnellwahlSchliessen();
  setzen({ aktiv: null, seite: "chat" });
  app.classList.add("versteckt");
  const s = await rufe("status");
  sperreZeigen(vollbild, s, () => { sperreSichtbar = false; appStarten(); });
}

function updateBanner(u) {
  toast(`VP4 ${u.version} ist da – unter Einstellungen → Über VP4`, "info", 8000);
}

/* --------------------------------------------------------- Ereignisse */
auf("nachricht_neu", (e) => {
  const u = finden(e.unterhaltung);
  if (u) {
    u.letzte = { text: e.vorschau ?? e.nachricht.text ?? "", ts: e.nachricht.ts, ich: e.nachricht.ich, von_name: e.nachricht.von_name };
    const offen = zustand.seite === "chat" && zustand.aktiv === u.id && document.hasFocus();
    if (!e.nachricht.ich && !offen) u.ungelesen = (u.ungelesen || 0) + 1;
    zustand.unterhaltungen = [u, ...zustand.unterhaltungen.filter((x) => x !== u)];
    leiste?.liste();
  } else spaeterNeuLaden();
});
for (const typ of ["unterhaltungen_geaendert", "kontakt_anfrage", "community_geaendert", "kontakt_geaendert"]) auf(typ, () => spaeterNeuLaden());
auf("community_geaendert", () => window.dispatchEvent(new CustomEvent("vp4-community-geaendert")));
// Wer online ist, ändert sich oft und in Schüben - gesammelt nachladen
auf("online", () => spaeterNeuLaden(600));
auf("verbindung", (e) => { setzen({ verbindung: { ...zustand.verbindung, ...e.zustand } }); leiste?.fussZeichnen(); });
auf("fehler", (e) => toast(e.text, "fehler", 6000));
auf("hinweis", (e) => toast(e.text, e.art || "info", 5000));
auf("update_verfuegbar", (e) => updateBanner(e));
auf("gesperrt", () => sperreAnzeigen());
auf("gelesen", (e) => { const u = finden(e.unterhaltung); if (u) { u.ungelesen = 0; leiste?.liste(); } });
// Mitteilung: nur, wenn man den Chat nicht gerade vor sich hat
auf("mitteilung", (e) => {
  const vorAugen = e.unterhaltung && zustand.seite === "chat" && zustand.aktiv === e.unterhaltung && document.hasFocus();
  if (vorAugen) return;
  toast(e.text || "", "nachricht", 5000, {
    titel: e.titel || null,
    aktion: e.unterhaltung ? () => unterhaltungOeffnen(e.unterhaltung) : null,
  });
});

/* ------------------------------------------------------ Tastenkürzel */
document.addEventListener("keydown", (e) => {
  if (!leiste || app.classList.contains("versteckt")) return;
  const strg = e.ctrlKey || e.metaKey;
  const taste = e.key.toLowerCase();
  if (strg && taste === "k") { e.preventDefault(); schnellwahl(); }
  else if (blattOffen()) return;       // in einem Blatt gelten nur seine Tasten
  else if (strg && taste === "n") { e.preventDefault(); freundHinzufuegen(); }
  else if (strg && e.key === ",") { e.preventDefault(); einstellungenOeffnen(); }
  else if (strg && taste === "l") { e.preventDefault(); sperren(); }
  else if (strg && taste === "f" && zustand.seite === "chat" && chatAktuell()) { e.preventDefault(); chatAktuell().suchen(); }
  else if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const liste = zustand.unterhaltungen.filter((u) => u.art !== "kanal");
    const i = liste.findIndex((u) => u.id === zustand.aktiv);
    const naechste = liste[(i + (e.key === "ArrowDown" ? 1 : -1) + liste.length) % liste.length];
    if (naechste) unterhaltungOeffnen(naechste.id);
  }
});

// Links aus Nachrichten öffnen im echten Browser, nie im Programmfenster
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-extern]");
  if (!a) return;
  e.preventDefault();
  rufe("link_oeffnen", a.getAttribute("href"));
});

// Gelesen melden, wenn das Fenster wieder nach vorne kommt
window.addEventListener("focus", () => {
  const c = chatAktuell();
  if (c && zustand.seite === "chat") rufe("gelesen", c.u.id);
});

// Rechtsklick nur dort, wo wir ein eigenes Menü haben
document.addEventListener("contextmenu", (e) => {
  if (!e.target.closest("input, textarea, .waehlbar, .verlauf")) e.preventDefault();
});
window.addEventListener("blur", () => menueSchliessen());

start();
