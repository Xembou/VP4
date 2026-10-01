// =====================================================================
//  gemeinschaft.js - Communities, Kanäle und kleine Gruppen
// =====================================================================
//  Eine Gruppe ist technisch eine Community mit genau einem Kanal und
//  ohne Kanal-Oberfläche. Beides tritt man mit demselben Code bei.
// =====================================================================

import { h, ic, knopf } from "../dom.js";
import { rufe } from "../bruecke.js";
import { blatt, toast, menueAn, eingabe, bestaetigen } from "../blaetter.js";
import { setzen } from "../zustand.js";

const ICONS = ["🎮", "📚", "⚽", "🎵", "🍕", "🚀", "🎨", "💬", "🔥", "🌙", "🏝️", "🧪"];

function ehrlicherHinweis() {
  return h("div.banner.warnung", { style: { "margin-top": "4px" } }, ic("triangle-alert"), h("div",
    h("b", { text: "Wer den Code hat, ist dabei. " },),
    "Ein weitergegebener Code lässt sich nicht zurückholen, und wer beitritt, kann auch Älteres lesen, das noch im Kanal steht. Soll jemand raus, erstellst du einen neuen Code."));
}

export function communityErstellen({ gruppe = false } = {}) {
  blatt((zu) => {
    let icon = gruppe ? "💬" : "🚀";
    const name = h("input.feld", { placeholder: gruppe ? "z. B. Die Jungs" : "z. B. Klasse 10b", maxlength: "48" });
    const wahl = h("div", { style: { display: "flex", gap: "6px", "flex-wrap": "wrap", "margin-top": "10px" } },
      ICONS.map((e) => h("button.rund", { type: "button", "aria-label": e, "aria-pressed": String(e === icon), style: { "font-size": "20px", width: "38px", height: "38px", background: e === icon ? "var(--akzent-weich)" : "var(--feld)" },
        onclick: (ev) => { icon = e; for (const b of wahl.children) { b.style.setProperty("background", b === ev.currentTarget ? "var(--akzent-weich)" : "var(--feld)"); } } }, e)));
    const los = async () => {
      if (!name.value.trim()) { name.classList.add("fehler"); return; }
      const r = await rufe(gruppe ? "gruppe_erstellen" : "community_erstellen", name.value.trim(), icon);
      if (!r.ok) { toast(r.fehler, "fehler"); return; }
      zu();
      if (!gruppe) setzen({ bereich: "communities", communityOffen: r.id });
      toast(gruppe ? "Gruppe erstellt" : "Community gegründet");
      einladungZeigen({ id: r.id, name: name.value.trim(), gruppe });
    };
    name.addEventListener("keydown", (e) => e.key === "Enter" && los());
    return [
      h("div.kopfsymbol", ic(gruppe ? "users" : "sparkles", "ic-20")),
      h("h2", { text: gruppe ? "Gruppe erstellen" : "Community gründen" }),
      h("p", { text: gruppe
        ? "Eine Gruppe für ein paar Freunde. Du bekommst gleich einen Code zum Weitergeben."
        : "Wie ein eigener Discord-Server: ein Name, mehrere Kanäle. Du bist Besitzer und kannst Kanäle anlegen und Admins bestimmen." }),
      h("label.beschriftung", { text: "Name" }), name,
      h("label.beschriftung", { style: { "margin-top": "14px" }, text: "Symbol" }), wahl,
      h("div.aktionen", knopf("Abbrechen", () => zu()), knopf(gruppe ? "Erstellen" : "Gründen", los, "primaer")),
    ];
  }, { breite: 480 });
}

export function gruppeErstellen() { communityErstellen({ gruppe: true }); }

export function communityBeitreten() {
  blatt((zu) => {
    const feld = h("textarea.feld.mono", { rows: 2, placeholder: "VP4G2-…", spellcheck: "false" });
    const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "6px" } });
    const los = async () => {
      const r = await rufe("community_beitreten", feld.value.trim());
      if (!r.ok) { fehler.textContent = r.fehler; feld.classList.add("fehler"); return; }
      zu();
      toast(r.gruppe ? "Du bist in der Gruppe" : `Willkommen in ${r.name}`);
      if (!r.gruppe) setzen({ bereich: "communities", communityOffen: r.id });
    };
    return [
      h("div.kopfsymbol", ic("link", "ic-20")),
      h("h2", { text: "Mit Code beitreten" }),
      h("p", { text: "Füge den Einladungscode ein, den du bekommen hast – für eine Gruppe oder eine Community." }),
      feld, fehler,
      h("div.aktionen", knopf("Abbrechen", () => zu()), knopf("Beitreten", los, "primaer")),
    ];
  }, { breite: 480 });
}

export async function einladungZeigen(c) {
  const r = await rufe("einladung", c.id);
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  blatt((zu) => [
    h("div.kopfsymbol", ic("user-plus", "ic-20")),
    h("h2", { text: `Leute in „${c.name}“ einladen` }),
    h("p", { text: "Schick diesen Code an die, die dabei sein sollen – am besten direkt, nicht öffentlich posten." }),
    h("div.ausgabe.mono", { style: { "min-height": "auto", "user-select": "all" }, text: r.code }),
    h("div", { style: { height: "12px" } }),
    ehrlicherHinweis(),
    h("div.aktionen",
      knopf("Fertig", () => zu()),
      knopf("Code kopieren", async () => { await navigator.clipboard?.writeText(r.code); toast("Code kopiert"); }, "primaer", "copy")),
  ], { breite: 520 });
}

export async function kanalAnlegen(c) {
  const name = await eingabe("Neuer Kanal", "Kleinbuchstaben, ohne Leerzeichen – so wie #allgemein.", {
    platzhalter: "z. B. hausaufgaben", ok: "Anlegen",
    pruefen: (v) => (/^[a-z0-9äöüß-]{1,32}$/.test(v.replace(/\s+/g, "-").toLowerCase()) ? null : "Nur Buchstaben, Ziffern und Bindestriche."),
  });
  if (!name) return;
  const r = await rufe("kanal_anlegen", c.id, name.replace(/\s+/g, "-").toLowerCase(), false);
  if (!r.ok) toast(r.fehler, "fehler"); else toast(`#${name} angelegt`);
}

export function communityMenue(anker, c) {
  menueAn(anker, [
    { text: "Leute einladen", symbol: "user-plus", aktion: () => einladungZeigen(c) },
    c.admin ? { text: "Kanal anlegen", symbol: "hash", aktion: () => kanalAnlegen(c) } : null,
    c.admin ? { text: "Ankündigungskanal anlegen", symbol: "megaphone", aktion: async () => {
      const name = await eingabe("Ankündigungskanal", "Hier können nur Admins schreiben.", { platzhalter: "ankuendigungen", ok: "Anlegen" });
      if (name) { const r = await rufe("kanal_anlegen", c.id, name.replace(/\s+/g, "-").toLowerCase(), true); if (!r.ok) toast(r.fehler, "fehler"); }
    } } : null,
    c.besitzer ? { text: "Neuen Code erstellen …", symbol: "rotate-ccw", aktion: () => codeErneuern(c) } : null,
    "-",
    { text: "Community verlassen …", symbol: "log-out", gefahr: true, aktion: async () => {
      if (!(await bestaetigen(`„${c.name}“ verlassen?`, "Du bekommst keine Nachrichten mehr. Mit dem Code kannst du wieder beitreten.", { ja: "Verlassen", gefahr: true, symbol: "log-out" }))) return;
      const r = await rufe("community_verlassen", c.id);
      if (!r.ok) toast(r.fehler, "fehler"); else setzen({ communityOffen: null });
    } },
  ]);
}

async function codeErneuern(c) {
  const ja = await bestaetigen("Neuen Code erstellen?",
    "Die Community bekommt einen neuen Schlüssel. Deine Kontakte unter den Mitgliedern bekommen ihn automatisch; alle anderen brauchen den neuen Code. Wer den alten hat, liest ab jetzt nichts Neues mehr.",
    { ja: "Neuen Code erstellen", symbol: "rotate-ccw" });
  if (!ja) return;
  const r = await rufe("community_code_erneuern", c.id);
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  einladungZeigen(c);
}
