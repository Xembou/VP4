// =====================================================================
//  einstellungen.js - wie die Systemeinstellungen: Liste links, Gruppen rechts
// =====================================================================

import { h, ic, knopf, ersetzen, avatar } from "../dom.js";
import { rufe } from "../bruecke.js";
import { toast, bestaetigen, blatt, eingabe } from "../blaetter.js";
import { zustand, setzen, erscheinungAnwenden, AKZENTE, TAPETEN } from "../zustand.js";
import { schalter } from "./chat.js";
import { freundescodeKopieren } from "./kontakte.js";

const BEREICHE = [
  ["profil", "Profil", "circle-user", "#8E8E93"],
  ["erscheinung", "Erscheinungsbild", "palette", "#0088FF"],
  ["chat", "Chat", "message-circle", "#34C759"],
  ["mitteilungen", "Mitteilungen", "bell", "#FF383C"],
  ["discord", "Discord", "globe", "#6155F5"],
  ["sicherheit", "Sicherheit", "shield", "#8E8E93"],
  ["daten", "Daten", "hard-drive", "#FF8D28"],
  ["ueber", "Über VP4", "info", "#00C3D0"],
];

let offen = "profil";

export function einstellungenZeigen(haupt, bereich = offen) {
  offen = bereich;
  const nav = h("nav", h("h1", { text: "Einstellungen" }), BEREICHE.map(([id, titel, symbol, farbe]) =>
    h("button", { type: "button", "aria-selected": String(id === offen), onclick: () => einstellungenZeigen(haupt, id) },
      h("span.symbol", { style: { background: farbe } }, ic(symbol)), titel)));
  const rechts = h("div.rechts");
  ersetzen(haupt, h("section.einstellungen", nav, rechts));
  const inhalt = h("div.inhalt-schmal");
  rechts.append(inhalt);
  SEITEN[offen](inhalt);
}

async function setze(schluessel, wert) {
  const r = await rufe("einstellung_setzen", schluessel, wert);
  if (!r.ok) { toast(r.fehler, "fehler"); return false; }
  setzen({ einstellungen: { ...zustand.einstellungen, [schluessel]: wert } });
  erscheinungAnwenden();
  return true;
}

const zeile = (symbol, titel, unter, rechts, klick) => h("div.eintrag" + (klick ? ".klickbar" : ""), klick ? { onclick: klick } : {},
  symbol ? ic(symbol) : null, h("div.titel", titel, unter ? h("small", { text: unter }) : null), rechts);
const schalterZeile = (symbol, titel, unter, schluessel, nachher) => zeile(symbol, titel, unter,
  schalter(!!zustand.einstellungen[schluessel], async (an) => { await setze(schluessel, an); nachher?.(an); }));

const SEITEN = {
  profil(el) {
    const p = zustand.profil || {};
    const name = h("input.feld", { value: p.name || "", maxlength: "64", style: { "max-width": "260px" } });
    name.addEventListener("change", async () => {
      const r = await rufe("profil_setzen", { name: name.value.trim() });
      if (r.ok) { setzen({ profil: r.profil }); toast("Gespeichert – deine Kontakte sehen den neuen Namen"); } else toast(r.fehler, "fehler");
    });
    const farben = h("div.farbwahl", Object.entries(AKZENTE).map(([id, [hell]]) => h("button", {
      type: "button", "aria-label": id, role: "radio", "aria-checked": String(p.avatar_farbe === hell), style: { "--f": hell },
      onclick: async () => { const r = await rufe("profil_setzen", { avatar_farbe: hell }); if (r.ok) { setzen({ profil: r.profil }); einstellungenZeigen(el.closest(".haupt"), "profil"); } },
    })));
    el.append(h("h2", { text: "Profil" }),
      h("div.gruppe", h("div.eintrag", { style: { padding: "18px 16px" } }, avatar(p.name || "?", p.avatar_farbe, "gross"),
        h("div.titel", h("b", { style: { font: "var(--t-titel3)" }, text: p.name }), h("small.mono", { text: p.id })),
        knopf("ID kopieren", async () => { await navigator.clipboard?.writeText(p.id); toast("Kopiert"); }, "", "copy"))),
      h("div.gruppe",
        zeile(null, "Name", "So sehen dich deine Kontakte", name),
        zeile(null, "Farbe", null, farben),
        zeile("qr-code", "Freundescode", "Enthält deine ID und deine öffentlichen Schlüssel", knopf("Kopieren", () => freundescodeKopieren()))),
    );
  },

  erscheinung(el) {
    const e = zustand.einstellungen;
    const design = h("div.segment", { style: { width: "260px" } });
    const pille = h("span.pille");
    const optionen = [["system", "Automatisch"], ["light", "Hell"], ["dark", "Dunkel"]];
    design.append(pille, ...optionen.map(([id, t]) => h("button", { type: "button", "aria-selected": String((e.design || "system") === id), onclick: async (ev) => {
      for (const b of design.querySelectorAll("button")) b.setAttribute("aria-selected", String(b === ev.currentTarget));
      pilleSetzen(); await setze("design", id);
    } }, t)));
    const pilleSetzen = () => { const a = design.querySelector('[aria-selected="true"]'); if (a) { pille.style.setProperty("width", `${a.offsetWidth}px`); pille.style.setProperty("transform", `translateX(${a.offsetLeft - 3}px)`); } };
    setTimeout(pilleSetzen);

    const akzent = h("div.farbwahl", Object.entries(AKZENTE).map(([id, [hell, , , name]]) => h("button", {
      type: "button", title: name, "aria-label": name, role: "radio", "aria-checked": String((e.farbe || "blau") === id), style: { "--f": hell },
      onclick: async (ev) => { await setze("farbe", id); for (const b of akzent.children) b.setAttribute("aria-checked", String(b === ev.currentTarget)); },
    })));

    const tapeten = h("div.tapetenwahl", Object.entries(TAPETEN).map(([id, name]) => {
      const vorschau = getComputedStyle(document.documentElement).getPropertyValue("--tapete");
      return h("div", h("button", {
        type: "button", "aria-label": name, role: "radio", "aria-checked": String((e.tapete || "tahoe") === id), dataset: { tapete: id },
        onclick: async (ev) => { await setze("tapete", id); for (const b of tapeten.querySelectorAll("button")) b.setAttribute("aria-checked", String(b === ev.currentTarget)); },
      }), h("small", { text: name }));
    }));
    // Vorschaubilder: jede Kachel bekommt die Tapete ihrer eigenen Variante
    requestAnimationFrame(() => {
      const wurzel = document.documentElement;
      const vorher = wurzel.dataset.tapete;
      for (const b of tapeten.querySelectorAll("button")) {
        if (b.dataset.tapete === "tahoe") delete wurzel.dataset.tapete; else wurzel.dataset.tapete = b.dataset.tapete;
        b.style.setProperty("--vorschau", getComputedStyle(wurzel).getPropertyValue("--tapete"));
      }
      if (vorher) wurzel.dataset.tapete = vorher; else delete wurzel.dataset.tapete;
    });

    el.append(h("h2", { text: "Erscheinungsbild" }),
      h("div.gruppe", zeile(null, "Aussehen", null, design), zeile(null, "Akzentfarbe", null, akzent)),
      h("div.gruppe-titel", { text: "Hintergrund" }), h("div.gruppe", tapeten),
      h("div.gruppe",
        schalterZeile("layers", "Glas stark", "Echte Lichtbrechung wie bei Apple – braucht mehr Leistung", "glas_stark"),
        schalterZeile("eye-off", "Transparenz reduzieren", "Massive Flächen statt Glas, besser lesbar", "transparenz_reduzieren"),
        schalterZeile("zap", "Bewegung reduzieren", "Keine Animationen, nur Überblenden", "bewegung_reduzieren")));
  },

  chat(el) {
    const e = zustand.einstellungen;
    const wege = [["beide", "WLAN und Discord", "Im selben WLAN direkt, sonst über Discord"], ["lan", "Nur WLAN", "Nichts verlässt das Haus – geht nur im selben Netz"], ["discord", "Nur Discord", "Immer über den Discord-Kanal"]];
    el.append(h("h2", { text: "Chat" }),
      h("div.gruppe-titel", { text: "Weg der Nachrichten" }),
      h("div.gruppe", wege.map(([id, titel, unter]) => zeile(id === "lan" ? "wifi" : id === "discord" ? "globe" : "layers", titel, unter,
        (e.transport_modus || "beide") === id ? ic("check") : null,
        async () => { if (await setze("transport_modus", id)) einstellungenZeigen(el.closest(".haupt"), "chat"); }))),
      h("div.gruppe-fuss", { text: "Über Discord geht ausschliesslich Verschlüsseltes. Im WLAN hat der direkte Weg Vorrang." }),
      h("div.gruppe",
        schalterZeile("check-check", "Lesebestätigungen", "Aus heisst auch: du siehst sie bei anderen nicht", "lesebestaetigungen"),
        schalterZeile("message-circle", "„schreibt …“ anzeigen", "Nur im WLAN – über Discord würde es das gemeinsame Limit aufbrauchen", "tippanzeige"),
        zeile("image", "Medien automatisch laden", "Bis zu dieser Grösse ohne Nachfrage", h("select.feld", { style: { width: "120px" }, onchange: (ev) => setze("medien_auto_mb", Number(ev.target.value)) },
          [0, 5, 20, 100].map((mb) => h("option", { value: mb, selected: (e.medien_auto_mb ?? 20) === mb, text: mb ? `${mb} MB` : "Nie" }))))));
  },

  mitteilungen(el) {
    el.append(h("h2", { text: "Mitteilungen" }),
      h("div.gruppe",
        schalterZeile("bell", "Mitteilungen", "Hinweis, wenn eine Nachricht kommt und VP4 nicht vorne ist", "mitteilungen"),
        schalterZeile("eye", "Vorschau zeigen", "Aus: nur „Neue Nachricht“, ohne Inhalt", "mitteilung_vorschau"),
        schalterZeile("bell-off", "Ton", null, "mitteilung_ton")));
  },

  async discord(el) {
    const r = await rufe("discord_status");
    const s = r.ok ? r : {};
    const token = h("input.feld.mono", { type: "password", placeholder: s.eingebaut ? "Eingebauter Zugang wird benutzt" : "Bot-Token", autocomplete: "off" });
    const kanaele = h("input.feld.mono", { placeholder: "Kanal-IDs, mit Komma getrennt", value: s.eigene_kanaele || "" });
    el.append(h("h2", { text: "Discord" }),
      h("div.gruppe",
        zeile("globe", "Status", s.meldung || "", h("span.verbindung" + (s.verbunden ? ".an" : s.fehler ? ".fehler" : ""), h("i"), s.verbunden ? "Verbunden" : s.fehler ? "Fehler" : "Getrennt")),
        zeile("hash", "Kanäle", null, h("span.wert", { text: String(s.anzahl_kanaele || 0) }))),
      h("div.gruppe-titel", { text: "Eigener Bot (optional)" }),
      h("div.gruppe", h("div.eintrag", h("div.titel", token, h("div", { style: { height: "8px" } }), kanaele,
        h("div.zeile-felder", { style: { "margin-top": "10px" } },
          knopf("Speichern", async () => { const x = await rufe("discord_setzen", token.value.trim(), kanaele.value.trim()); x.ok ? toast("Gespeichert – verbinde neu") : toast(x.fehler, "fehler"); }, "primaer"),
          knopf("Eingebauten benutzen", async () => { await rufe("discord_setzen", "", ""); einstellungenZeigen(el.closest(".haupt"), "discord"); }))))),
      h("div.gruppe-fuss", { text: "Ein eigener Bot geht dem eingebauten immer vor. Wie man einen anlegt, steht in der README." }),
      h("div.banner.warnung", ic("triangle-alert"), h("div", h("b", { text: "Ehrlich: " }),
        "Der eingebaute Bot-Zugang steckt in der VP4.exe und lässt sich herausholen. Deine Nachrichten bleiben trotzdem geheim (Ende-zu-Ende), aber jemand könnte den Bot stören. Dann hilft eine neue VP4-Version von GitHub.")));
  },

  async sicherheit(el) {
    const r = await rufe("sicherheit_status");
    const s = r.ok ? r : {};
    el.append(h("h2", { text: "Sicherheit" }),
      h("div.gruppe",
        zeile("key-round", "Master-Passwort ändern", "Schlüsselt deinen Tresor neu – Inhalt bleibt", ic("chevron-right", "ic-16"), () => passwortAendern()),
        zeile("lock", "Bei jedem Start fragen", s.windows_verfuegbar === false ? "Auf diesem System ohnehin immer" : "Sonst merkt sich Windows das Passwort für dein Benutzerkonto",
          schalter(!!zustand.einstellungen.bei_start_fragen, async (an) => { await setze("bei_start_fragen", an); })),
        zeile("refresh-cw", "Automatisch sperren", "Nach so vielen Minuten ohne Eingabe", h("select.feld", { style: { width: "120px" }, onchange: (ev) => setze("auto_sperre_min", Number(ev.target.value)) },
          [0, 5, 15, 60].map((m) => h("option", { value: m, selected: (zustand.einstellungen.auto_sperre_min || 0) === m, text: m ? `${m} Min.` : "Nie" })))),
        zeile("lock", "Jetzt sperren", "Strg L", ic("chevron-right", "ic-16"), () => window.dispatchEvent(new Event("vp4-sperren")))),
      h("div.gruppe-titel", { text: "Was VP4 schützt – und was nicht" }),
      h("div.gruppe", h("div.eintrag", h("div.titel", { style: { font: "var(--t-hinweis)", color: "var(--text-2)" } },
        h("p", { style: { margin: "4px 0 8px" } }, h("b", { style: { color: "var(--text)" }, text: "Geschützt: " }), "der Inhalt jeder Nachricht, jedes Bilds, jeder Datei – Ende-zu-Ende mit AES-256-GCM. Discord sieht nur Geheimtext."),
        h("p", { style: { margin: "0 0 8px" } }, h("b", { style: { color: "var(--text)" }, text: "Nicht geschützt: " }), "wer wann wem schreibt (die Absender- und Empfänger-ID stehen offen im Kanal). Noch keine Forward Secrecy."),
        h("p", { style: { margin: "0 0 4px" } }, h("b", { style: { color: "var(--text)" }, text: "Und: " }), "VP4 ist ein Hobby-Projekt, kein geprüftes Sicherheitsprodukt.")))));
  },

  async daten(el) {
    const s = await rufe("daten_status");
    el.append(h("h2", { text: "Daten" }),
      h("div.gruppe",
        zeile("folder", "Speicherort", s.ordner || "", knopf("Öffnen", () => rufe("ordner_oeffnen"))),
        zeile("hard-drive", "Belegt", null, h("span.wert", { text: s.belegt || "–" }))),
      h("div.gruppe",
        zeile("trash-2", "Alles löschen und neu einrichten", "Löscht Tresor, Chats und Schlüssel auf diesem PC", knopf("Löschen …", () => allesLoeschen(), "gefahr"))));
  },

  async ueber(el) {
    const s = await rufe("status");
    el.append(h("h2", { text: "Über VP4" }),
      h("div.gruppe",
        zeile("info", "Version", null, h("span.wert", { text: s.version || "" })),
        zeile("refresh-cw", "Nach Updates suchen", null, knopf("Prüfen", async () => {
          const r = await rufe("update_pruefen");
          if (!r.ok) toast(r.fehler, "fehler"); else if (r.neu) toast(`Version ${r.version} ist da`, "info"); else toast("Du hast die neueste Version");
        })),
        zeile("external-link", "Quelltext auf GitHub", null, ic("chevron-right", "ic-16"), () => rufe("link_oeffnen", s.repo_url))),
      h("div.gruppe-titel", { text: "Mitgeliefert" }),
      h("div.gruppe",
        zeile(null, "Lucide Icons", "ISC-Lizenz", null),
        zeile(null, "Inter", "SIL Open Font License 1.1", null),
        zeile(null, "Emoji-Namen: emojibase", "MIT-Lizenz", null)),
      h("div.gruppe-fuss", { text: "VP4 ist ein privates Hobby-Projekt. Die Kryptografie kommt aus geprüften Bibliotheken, das Programm drumherum hat niemand fachlich geprüft." }));
  },
};

function passwortAendern() {
  blatt((zu) => {
    const alt = h("input.feld", { type: "password", placeholder: "Aktuelles Passwort" });
    const neu = h("input.feld", { type: "password", placeholder: "Neues Passwort" });
    const neu2 = h("input.feld", { type: "password", placeholder: "Neues Passwort wiederholen" });
    const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "6px" } });
    return [h("h2", { text: "Master-Passwort ändern" }),
      h("p", { text: "Es gibt weiterhin keine Wiederherstellung. Schreib dir das neue Passwort auf." }),
      alt, h("div", { style: { height: "8px" } }), neu, h("div", { style: { height: "8px" } }), neu2, fehler,
      h("div.aktionen", knopf("Abbrechen", () => zu()), knopf("Ändern", async () => {
        if (neu.value !== neu2.value) { fehler.textContent = "Die neuen Passwörter sind nicht gleich."; return; }
        const r = await rufe("passwort_aendern", alt.value, neu.value);
        if (!r.ok) { fehler.textContent = r.fehler; return; }
        zu(); toast("Master-Passwort geändert");
      }, "primaer"))];
  }, { breite: 440 });
}

async function allesLoeschen() {
  const wort = await eingabe("Wirklich alles löschen?", "Tresor, Chatverläufe, Schlüssel und empfangene Dateien auf diesem PC sind dann weg – unwiderruflich. Tippe LÖSCHEN, um es zu bestätigen.",
    { platzhalter: "LÖSCHEN", ok: "Alles löschen", pruefen: (v) => (v === "LÖSCHEN" ? null : "Bitte genau LÖSCHEN eintippen.") });
  if (wort !== "LÖSCHEN") return;
  const r = await rufe("alles_loeschen");
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  location.reload();
}
