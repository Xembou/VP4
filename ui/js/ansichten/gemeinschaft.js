// =====================================================================
//  gemeinschaft.js - Communities, Kanäle und kleine Gruppen
// =====================================================================
//  Eine Gruppe ist technisch eine Community mit genau einem Kanal und
//  ohne Kanal-Oberfläche. Beides tritt man mit demselben Code bei.
// =====================================================================

import { h, ic, knopf, rundknopf, ersetzen, avatar, farbeFuer, zeitKurz } from "../dom.js";
import { rufe } from "../bruecke.js";
import { blatt, toast, menue, menueAn, eingabe, bestaetigen } from "../blaetter.js";
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
    const name = h("input.feld", { placeholder: gruppe ? "z. B. Die Jungs" : "z. B. Klasse 10b", maxlength: "48", "aria-label": "Name" });
    const wahl = h("div", { role: "group", "aria-label": "Symbol", style: { display: "flex", gap: "6px", "flex-wrap": "wrap", "margin-top": "10px" } },
      ICONS.map((e) => h("button.rund", { type: "button", "aria-label": e, "aria-pressed": String(e === icon), style: { "font-size": "20px", width: "38px", height: "38px", background: e === icon ? "var(--akzent-weich)" : "var(--feld)" },
        onclick: (ev) => { icon = e; for (const b of wahl.children) { const an = b === ev.currentTarget; b.style.setProperty("background", an ? "var(--akzent-weich)" : "var(--feld)"); b.setAttribute("aria-pressed", String(an)); } } }, e)));
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
    const feld = h("textarea.feld.mono", { rows: 2, placeholder: "VP4G2-…", spellcheck: "false", "aria-label": "Einladungscode" });
    const fehler = h("div.fehlertext", { role: "alert" });
    const los = async () => {
      const r = await rufe("community_beitreten", feld.value.trim());
      if (!r.ok) { fehler.textContent = r.fehler; feld.classList.add("fehler"); return; }
      zu();
      // Name und Kanäle kommen erst mit dem Manifest - bis dahin heisst die
      // Community vielleicht noch "Neue Community". Also neutral bleiben.
      const bekannt = r.name && r.name !== "Neue Community";
      if (r.gruppe) {
        toast(bekannt ? `Du bist in der Gruppe „${r.name}“` : "Du bist in der Gruppe", "ok", 4000);
      } else {
        toast(bekannt ? `Beigetreten: ${r.name}` : "Beigetreten – die Kanäle erscheinen, sobald jemand aus der Gruppe online ist.", "ok", 5000);
        setzen({ bereich: "communities", communityOffen: r.id });
        window.dispatchEvent(new CustomEvent("vp4-bereich"));
      }
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

/** Rolle aus dem Eintrag - ältere Antworten kennen nur admin/besitzer. */
export function rolle(c) {
  return c.rolle || (c.besitzer ? "besitzer" : c.admin ? "admin" : "mitglied");
}
const darfVerwalten = (c) => ["besitzer", "admin"].includes(rolle(c));
const kanalName = (v) => v.trim().replace(/\s+/g, "-").toLowerCase();
const kanalPruefen = (v) => (/^[a-z0-9äöüß_-]{1,32}$/.test(kanalName(v)) ? null : "Nur Buchstaben, Ziffern und Bindestriche, höchstens 32 Zeichen.");

/** Eine Antwort von Python zeigen: Fehler als Hinweis, sonst still. */
function antwort(r, ok = null) {
  if (!r.ok) { toast(r.fehler, "fehler", 5000); return false; }
  if (ok) toast(ok);
  return true;
}

export async function kanalAnlegen(c, { nurAdmins = false } = {}) {
  const name = await eingabe(nurAdmins ? "Ankündigungskanal" : "Neuer Kanal",
    nurAdmins ? "Hier können nur Admins schreiben, alle anderen lesen." : "Kleinbuchstaben, ohne Leerzeichen – so wie #allgemein.", {
      platzhalter: nurAdmins ? "z. B. ankündigungen" : "z. B. hausaufgaben", ok: "Anlegen", pruefen: kanalPruefen,
    });
  if (!name) return;
  antwort(await rufe("kanal_anlegen", c.id, kanalName(name), nurAdmins), `#${kanalName(name)} angelegt`);
}

/* ------------------------------------------------- Kanäle verwalten */
async function kanalUmbenennen(c, k) {
  const name = await eingabe("Kanal umbenennen", `Neuer Name für #${k.name}.`, { wert: k.name, ok: "Umbenennen", pruefen: kanalPruefen });
  if (!name || kanalName(name) === k.name) return;
  antwort(await rufe("kanal_umbenennen", c.id, k.id, kanalName(name)), "Umbenannt");
}

async function kanalLoeschen(c, k) {
  if (!(await bestaetigen(`#${k.name} löschen?`,
    "Der Kanal verschwindet bei allen Mitgliedern. Was darin steht, ist danach auch auf diesem PC weg.",
    { ja: "Löschen", gefahr: true, symbol: "trash-2" }))) return false;
  return antwort(await rufe("kanal_loeschen", c.id, k.id), `#${k.name} gelöscht`);
}

/** Menüeinträge für einen Kanal - im Blatt und per Rechtsklick in der Leiste. */
export function kanalEintraege(c, k, nachher = () => {}) {
  if (!darfVerwalten(c)) return [];
  const i = c.kanaele.findIndex((x) => x.id === k.id);
  const standard = i === 0;      // der Standardkanal ("allgemein") bleibt
  return [
    { text: "Umbenennen …", symbol: "pencil", aktion: async () => { await kanalUmbenennen(c, k); nachher(); } },
    i > 0 ? { text: "Nach oben", symbol: "arrow-up", aktion: async () => { antwort(await rufe("kanal_verschieben", c.id, k.id, i - 1)); nachher(); } } : null,
    i < c.kanaele.length - 1 ? { text: "Nach unten", symbol: "arrow-down", aktion: async () => { antwort(await rufe("kanal_verschieben", c.id, k.id, i + 1)); nachher(); } } : null,
    standard ? null : { text: k.nur_admins ? "Alle dürfen schreiben" : "Nur Admins schreiben", symbol: "megaphone", aktion: async () => {
      antwort(await rufe("kanal_nur_admins_setzen", c.id, k.id, !k.nur_admins), k.nur_admins ? `In #${k.name} dürfen jetzt alle schreiben` : `#${k.name} ist jetzt ein Ankündigungskanal`);
      nachher();
    } },
    standard ? null : "-",
    standard ? null : { text: "Kanal löschen …", symbol: "trash-2", gefahr: true, aktion: async () => { await kanalLoeschen(c, k); nachher(); } },
  ];
}

/** Rechtsklick auf einen Kanal in der Seitenleiste */
export function kanalMenue(x, y, c, k) {
  const eintraege = kanalEintraege(c, k, () => {});
  if (!eintraege.length) return false;
  menue(x, y, eintraege, { label: `Kanal ${k.name}` });
  return true;
}

/* ----------------------------------------------- Mitglieder verwalten */
async function mitgliedEntfernen(c, m) {
  if (!(await bestaetigen(`${m.name} entfernen?`,
    "Die Community bekommt dafür einen neuen Schlüssel und Code. Das stoppt nur NEUE Nachrichten – was schon da war, hat er oder sie weiterhin. Mitglieder, die nicht in deinen Kontakten sind, brauchen den neuen Code.",
    { ja: "Entfernen", gefahr: true, symbol: "log-out" }))) return false;
  const r = await rufe("mitglied_entfernen", c.id, m.id);
  if (!antwort(r)) return false;
  blatt((zu) => [
    h("div.kopfsymbol", ic("rotate-ccw", "ic-20")),
    h("h2", { text: `${m.name} ist entfernt` }),
    h("p", { text: "Deine Kontakte in der Community bekommen den neuen Schlüssel automatisch. Allen anderen schickst du diesen neuen Code – der alte gilt für neue Nachrichten nicht mehr." }),
    h("div.ausgabe.mono", { style: { "min-height": "auto", "user-select": "all" }, text: r.code || "" }),
    h("div.banner.warnung", { style: { "margin-top": "12px" } }, ic("triangle-alert"), h("div",
      h("b", { text: "Ehrlich gesagt: " }), "Was vor dem Entfernen geschrieben wurde, bleibt lesbar – auch für die entfernte Person, wenn sie es schon hat.")),
    h("div.aktionen",
      knopf("Fertig", () => zu()),
      knopf("Code kopieren", async () => { await navigator.clipboard?.writeText(r.code || ""); toast("Code kopiert"); }, "primaer", "copy")),
  ], { breite: 520 });
  return true;
}

function mitgliedEintraege(c, m, nachher) {
  if (rolle(c) !== "besitzer" || m.ich || m.besitzer) return [];
  return [
    m.admin
      ? { text: "Admin entfernen", symbol: "shield", aktion: async () => { antwort(await rufe("admin_setzen", c.id, m.id, false), `${m.name} ist kein Admin mehr`); nachher(); } }
      : { text: "Zum Admin machen", symbol: "crown", aktion: async () => { antwort(await rufe("admin_setzen", c.id, m.id, true), `${m.name} ist jetzt Admin`); nachher(); } },
    "-",
    { text: "Aus der Community entfernen …", symbol: "log-out", gefahr: true, aktion: async () => { if (!(await mitgliedEntfernen(c, m))) nachher(); } },
  ];
}

/* ------------------------------------------ Das Einstellungs-Blatt */
export function communityEinstellungen(start) {
  const cid = start.id;
  let ab = null;
  blatt((zu) => {
    const inhalt = h("div.community-einstellungen");
    let c = start;
    let icon = c.icon || "";

    const neuLaden = async () => {
      const [rc, rm] = await Promise.all([rufe("communities"), rufe("community_mitglieder", cid)]);
      const frisch = rc.ok ? (rc.liste || []).find((x) => x.id === cid) : null;
      if (rc.ok && !frisch) { zu(); return; }          // verlassen oder gelöscht
      if (frisch) c = frisch;
      if (inhalt.isConnected) zeichnen(rm.ok ? rm.liste || [] : null, rm.ok ? null : rm.fehler);
    };
    // Nach einer Aktion: Rückfragen (Umbenennen, Löschen) sind eigene
    // Blätter und haben dieses geschlossen - dann kommt es wieder.
    const spaeter = () => setTimeout(() => { if (inhalt.isConnected) neuLaden(); else communityEinstellungen(c); }, 150);

    const zeichnen = (mitglieder, fehler) => {
      inhalt.dataset.gezeichnet = "1";
      const verwalten = darfVerwalten(c);
      const farbe = c.farbe || farbeFuer(c.id);

      // Name und Symbol
      const name = h("input.feld", { value: c.name, maxlength: "48", "aria-label": "Name der Community", disabled: !verwalten });
      const symbole = h("div.symbolwahl", { role: "radiogroup", "aria-label": "Symbol" },
        [...new Set([icon, ...ICONS].filter(Boolean))].slice(0, 13).map((e) => h("button", {
          type: "button", role: "radio", "aria-label": e, "aria-checked": String(e === icon), disabled: !verwalten,
          onclick: (ev) => { icon = e; for (const b of symbole.children) b.setAttribute("aria-checked", String(b === ev.currentTarget)); },
        }, e)));
      const speichern = async () => {
        const n = name.value.trim();
        if (!n) { name.classList.add("fehler"); return; }
        if (antwort(await rufe("community_umbenennen", c.id, n, icon || null), "Gespeichert")) spaeter();
      };
      name.addEventListener("keydown", (e) => e.key === "Enter" && speichern());

      const kanalZeilen = c.kanaele.map((k, i) => h("div.eintrag",
        h("span.kanal-symbol", ic(k.nur_admins ? "megaphone" : "hash", "ic-16")),
        h("div.titel", h("span.einzeilig", { text: k.name }),
          i === 0 ? h("small", { text: "Standardkanal – hier meldet sich jeder Neue" }) : k.nur_admins ? h("small", { text: "Nur Admins schreiben" }) : null),
        verwalten ? rundknopf("ellipsis", `Optionen für #${k.name}`, (e) => menueAn(e.currentTarget, kanalEintraege(c, k, spaeter), { label: `Kanal ${k.name}` }), "klein") : null));

      const mitgliedZeilen = mitglieder === null
        ? [h("div.eintrag", h("div.titel", { style: { color: "var(--text-2)" }, text: fehler || "Mitglieder lassen sich gerade nicht laden." }))]
        : mitglieder.length
          ? mitglieder.map((m) => {
              const aktionen = mitgliedEintraege(c, m, spaeter);
              return h("div.eintrag",
                avatar(m.name, m.farbe, "mittel"),
                h("div.titel",
                  h("span.einzeilig", m.name, m.ich ? h("span.leise-text", { text: " (du)" }) : null),
                  h("small.mono", { text: m.ts ? `${m.id} · zuletzt ${zeitKurz(m.ts)}` : m.id })),
                m.besitzer ? h("span.marke.besitzer", ic("crown", "ic-14"), "Besitzer") : m.admin ? h("span.marke", ic("shield", "ic-14"), "Admin") : null,
                aktionen.length ? rundknopf("ellipsis", `Optionen für ${m.name}`, (e) => menueAn(e.currentTarget, aktionen, { label: m.name }), "klein") : null);
            })
          : [h("div.eintrag", h("div.titel", { style: { color: "var(--text-2)" }, text: "Noch niemand gesehen. Wer schreibt, erscheint hier." }))];

      ersetzen(inhalt,
        h("div.ce-kopf", { style: { "--farbe": farbe } },
          h("div.avatar.community.gross", { style: { "--farbe": farbe }, "aria-hidden": "true" }, icon || c.name.slice(0, 1)),
          h("div", { style: { "min-width": "0" } },
            h("h2", { text: "Community-Einstellungen" }),
            h("p", { text: rolle(c) === "besitzer" ? "Du bist Besitzer: Du bestimmst die Admins." : rolle(c) === "admin" ? "Du bist Admin: Du darfst Kanäle und Namen ändern." : "Nur Besitzer und Admins können hier etwas ändern." }))),
        h("div.gruppe-titel", { text: "Name und Symbol" }),
        h("div.gruppe",
          h("div.eintrag", name, verwalten ? knopf("Sichern", speichern, "primaer") : null),
          h("div.eintrag", symbole)),
        h("div.gruppe-titel", { text: `Kanäle (${c.kanaele.length})` }),
        h("div.gruppe", kanalZeilen,
          verwalten ? h("div.eintrag.klickbar", { role: "button", tabindex: "0", onclick: async () => { await kanalAnlegen(c); spaeter(); },
            onkeydown: (e) => e.key === "Enter" && e.currentTarget.click() },
            h("span.kanal-symbol.akzent", ic("plus", "ic-16")), h("div.titel", { style: { color: "var(--akzent-schrift)" }, text: "Kanal anlegen …" })) : null),
        h("div.gruppe-titel", { text: mitglieder ? `Zuletzt aktiv (${mitglieder.length})` : "Mitglieder" }),
        h("div.gruppe", mitgliedZeilen),
        h("p.gruppe-fuss", { style: { margin: "-12px 16px 0" }, text: "Eine Mitgliederliste gibt es bewusst nicht – wer den Code hat, ist dabei. Hier steht, wer zuletzt geschrieben hat." }),
        h("div.aktionen", knopf("Fertig", () => zu(), "primaer")));
    };

    zeichnen(null, "Wird geladen …");
    neuLaden();
    // Änderungen von anderen (neues Manifest) kommen als Ereignis - neu zeichnen
    ab = () => { if (inhalt.isConnected) neuLaden(); };
    window.addEventListener("vp4-community-geaendert", ab);
    return inhalt;
  }, { breite: 560, label: `Einstellungen für ${start.name}`,
       beimSchliessen: () => window.removeEventListener("vp4-community-geaendert", ab) });
}

export function communityMenue(anker, c) {
  const verwalten = darfVerwalten(c);
  menueAn(anker, [
    { text: "Leute einladen", symbol: "user-plus", aktion: () => einladungZeigen(c) },
    { text: verwalten ? "Einstellungen …" : "Mitglieder und Kanäle …", symbol: "settings", aktion: () => communityEinstellungen(c) },
    verwalten ? "-" : null,
    verwalten ? { text: "Kanal anlegen …", symbol: "hash", aktion: () => kanalAnlegen(c) } : null,
    verwalten ? { text: "Ankündigungskanal anlegen …", symbol: "megaphone", aktion: () => kanalAnlegen(c, { nurAdmins: true }) } : null,
    rolle(c) === "besitzer" ? { text: "Neuen Code erstellen …", symbol: "rotate-ccw", aktion: () => codeErneuern(c) } : null,
    "-",
    { text: "Community verlassen …", symbol: "log-out", gefahr: true, aktion: async () => {
      if (!(await bestaetigen(`„${c.name}“ verlassen?`, "Du bekommst keine Nachrichten mehr. Mit dem Code kannst du wieder beitreten.", { ja: "Verlassen", gefahr: true, symbol: "log-out" }))) return;
      const r = await rufe("community_verlassen", c.id);
      if (!r.ok) toast(r.fehler, "fehler"); else setzen({ communityOffen: null });
    } },
  ], { label: `Community ${c.name}` });
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
