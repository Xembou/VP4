// =====================================================================
//  main.js - Start, Aufteilung, Tastenkürzel
// =====================================================================

import { h, ic, ersetzen, leeren } from "./dom.js";
import { verbinden, rufe, auf, ereignisseStarten, ereignisseStoppen } from "./bruecke.js";
import { zustand, setzen, erscheinungAnwenden } from "./zustand.js";
import { toast, blatt, menueSchliessen } from "./blaetter.js";
import { Seitenleiste } from "./ansichten/seitenleiste.js";
import { chatOeffnen, chatSchliessen, chatAktuell } from "./ansichten/chat.js";
import { werkzeugZeigen } from "./ansichten/werkzeuge.js";
import { einstellungenZeigen } from "./ansichten/einstellungen.js";
import { einrichtungZeigen, sperreZeigen } from "./ansichten/einrichtung.js";
import { freundHinzufuegen } from "./ansichten/kontakte.js";

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
  else if (s.phase === "gesperrt") sperreZeigen(vollbild, s, () => appStarten());
  else appStarten();
}

async function appStarten() {
  const s = await rufe("status");
  setzen({ einstellungen: s.einstellungen || {}, profil: s.profil, verbindung: s.verbindung || zustand.verbindung });
  erscheinungAnwenden();
  leeren(vollbild);
  app.classList.remove("versteckt");
  const aside = h("aside.seitenleiste.glas.bricht", { "aria-label": "Navigation" });
  haupt = h("main.haupt");
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
    unterhaltungen: u.ok ? u.liste : [],
    communities: c.ok ? c.liste : [],
    anfragen: a.ok ? a.liste : [],
  });
  leiste?.liste();
  leiste?.fussZeichnen();
}

let neuLadenGeplant = null;
function spaeterNeuLaden() {
  clearTimeout(neuLadenGeplant);
  neuLadenGeplant = setTimeout(listenLaden, 120);
}

function leerZeigen() {
  if (zustand.seite !== "chat" || zustand.aktiv) return;
  chatSchliessen();
  ersetzen(haupt, h("div.leer",
    h("div.kreis", ic("message-circle", "ic-20")),
    h("h3", { text: zustand.unterhaltungen.length ? "Wähle einen Chat" : "Willkommen bei VP4" }),
    h("p", { text: zustand.unterhaltungen.length ? "Links stehen deine Chats, Gruppen und Communities." : "Füge Freunde über ihre ID hinzu. Eure Nachrichten sind Ende-zu-Ende verschlüsselt." }),
    h("div", { style: { display: "flex", gap: "8px", "justify-content": "center", "margin-top": "6px" } },
      h("button.knopf.primaer", { type: "button", onclick: () => freundHinzufuegen() }, ic("user-plus"), "Freund hinzufügen"),
      h("button.knopf", { type: "button", onclick: async () => { await navigator.clipboard?.writeText(zustand.profil?.id || ""); toast("Deine ID ist kopiert"); } }, ic("copy"), "Meine ID kopieren"))));
}

function finden(id) {
  return zustand.unterhaltungen.find((u) => u.id === id);
}

async function unterhaltungOeffnen(id) {
  let u = finden(id);
  if (!u) { await listenLaden(); u = finden(id); }
  if (!u) return;
  setzen({ seite: "chat", aktiv: id });
  chatOeffnen(haupt, u);
  leiste.liste();
  leiste.fussZeichnen();
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

window.addEventListener("vp4-werkzeug", (e) => werkzeugOeffnen(e.detail));
window.addEventListener("vp4-sperren", () => sperren());

async function sperren() {
  const r = await rufe("sperren");
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  ereignisseStoppen();
  chatSchliessen();
  app.classList.add("versteckt");
  const s = await rufe("status");
  sperreZeigen(vollbild, s, () => appStarten());
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
for (const typ of ["unterhaltungen_geaendert", "kontakt_anfrage", "community_geaendert", "kontakt_geaendert"]) auf(typ, spaeterNeuLaden);
auf("verbindung", (e) => { setzen({ verbindung: { ...zustand.verbindung, ...e.zustand } }); leiste?.fussZeichnen(); });
auf("fehler", (e) => toast(e.text, "fehler", 6000));
auf("hinweis", (e) => toast(e.text, e.art || "info", 5000));
auf("update_verfuegbar", (e) => updateBanner(e));
auf("gesperrt", () => sperren());
auf("gelesen", (e) => { const u = finden(e.unterhaltung); if (u) { u.ungelesen = 0; leiste?.liste(); } });

/* ------------------------------------------------------ Tastenkürzel */
document.addEventListener("keydown", (e) => {
  if (!leiste) return;
  const strg = e.ctrlKey || e.metaKey;
  if (strg && e.key.toLowerCase() === "k") { e.preventDefault(); leiste.sucheFeld.focus(); leiste.sucheFeld.select(); }
  else if (strg && e.key.toLowerCase() === "n") { e.preventDefault(); freundHinzufuegen(); }
  else if (strg && e.key === ",") { e.preventDefault(); einstellungenOeffnen(); }
  else if (strg && e.key.toLowerCase() === "l") { e.preventDefault(); sperren(); }
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
window.addEventListener("blur", menueSchliessen);

start();
