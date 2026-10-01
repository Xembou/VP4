// =====================================================================
//  werkzeuge.js - der Werkzeugkasten: alles, was VP4 4.x konnte, und mehr
// =====================================================================

import { h, ic, knopf, ersetzen, leeren, groesse } from "../dom.js";
import { rufe, auf } from "../bruecke.js";
import { menue, toast, blatt, eingabe, bestaetigen } from "../blaetter.js";

export const WERKZEUGE = [
  { id: "text", titel: "Text verschlüsseln", symbol: "lock", farbe: "#0088FF", info: "18 Verfahren – von AES bis Post-Quanten, und Klassiker zum Spielen." },
  { id: "dateien", titel: "Dateien & Ordner", symbol: "folder-lock", farbe: "#FF8D28", info: "Ganze Ordner in eine .vp4- oder .age-Datei packen, beliebig gross." },
  { id: "schluessel", titel: "Schlüsselbund", symbol: "key-round", farbe: "#34C759", info: "Deine gespeicherten Schlüssel, geschützt durch das Master-Passwort." },
  { id: "signieren", titel: "Signieren & Prüfen", symbol: "signature", farbe: "#6155F5", info: "Beweisen, dass ein Text von dir ist und nicht verändert wurde." },
  { id: "pruefsummen", titel: "Prüfsummen", symbol: "scan-line", farbe: "#00C3D0", info: "Herausfinden, ob zwei Dateien wirklich exakt gleich sind." },
  { id: "obsidian", titel: "Obsidian", symbol: "notebook-pen", farbe: "#CB30E0", info: "Schlüssel in deinen Obsidian-Vault schreiben und zurückholen." },
];

const ARTEN = {
  sicher: ["Sicher", "sicher", "shield-check"],
  pq: ["Post-Quanten", "pq", "atom"],
  spiel: ["Nur zum Spielen", "spiel", "sparkle"],
};

export function werkzeugZeigen(haupt, id) {
  const w = WERKZEUGE.find((x) => x.id === id);
  const seite = h("div.seite");
  ersetzen(haupt, seite);
  if (!w) return uebersicht(seite);
  seite.append(h("div.seite-kopf",
    h("span", { style: { display: "grid", "place-items": "center", width: "44px", height: "44px", "border-radius": "12px", background: w.farbe, color: "#fff" } }, ic(w.symbol, "ic-20")),
    h("div", { style: { flex: "1" } }, h("h1", { text: w.titel }), h("p", { text: w.info }))));
  const inhalt = h("div");
  seite.append(inhalt);
  ({ text: textWerkzeug, dateien: dateiWerkzeug, schluessel: schluesselWerkzeug, signieren: signaturWerkzeug,
     pruefsummen: pruefsummenWerkzeug, obsidian: obsidianWerkzeug })[id](inhalt);
}

function uebersicht(seite) {
  seite.append(h("div.seite-kopf", h("div", h("h1", { text: "Werkzeuge" }), h("p", { text: "Verschlüsseln ohne Chat – für Texte, Dateien und Schlüssel." }))),
    h("div.werkzeug-raster", WERKZEUGE.map((w) => h("button.werkzeug-karte", { type: "button", onclick: () => window.dispatchEvent(new CustomEvent("vp4-werkzeug", { detail: w.id })) },
      h("span.symbol", { style: { background: w.farbe } }, ic(w.symbol, "ic-20")), h("b", { text: w.titel }), h("span", { text: w.info })))));
}

/* --------------------------------------------------------------- Text */
async function textWerkzeug(el) {
  const r = await rufe("verfahren");
  if (!r.ok) { el.append(h("div.banner.gefahr", ic("circle-alert"), r.fehler)); return; }
  const verfahren = r.liste;
  let gewaehlt = verfahren.find((v) => v.name === "AES-256-GCM") || verfahren[0];

  const wahlKnopf = h("button.verfahrenswahl", { type: "button" });
  const eingabeFeld = h("textarea.feld", { rows: 8, placeholder: "Text hier eingeben oder einfügen …" });
  const schluesselFeld = h("textarea.feld.mono", { rows: 2, spellcheck: "false" });
  const schluesselBeschriftung = h("label.beschriftung");
  const erzeugen = knopf("Erzeugen", () => schluesselErzeugen(), "", "sparkles");
  const ausBund = knopf("Aus Schlüsselbund", (e) => bundMenue(e.currentTarget), "", "key-round");
  const hinweis = h("div.banner");
  const ausgabe = h("div.ausgabe.leer-hinweis", { text: "Das Ergebnis erscheint hier." });
  const kopieren = knopf("Kopieren", async () => { await navigator.clipboard?.writeText(ausgabe.textContent); toast("Kopiert"); }, "", "copy");
  const tauschen = knopf("Als Eingabe", () => { eingabeFeld.value = ausgabe.textContent; }, "", "refresh-cw");
  kopieren.disabled = tauschen.disabled = true;

  const zeigen = () => {
    const [titel, klasse, symbol] = ARTEN[gewaehlt.art] || ARTEN.sicher;
    ersetzen(wahlKnopf,
      h("div.titel", h("b", { text: gewaehlt.name }), h("small", { text: gewaehlt.hinweis })),
      h("span.marke." + klasse, ic(symbol, "ic-14"), titel), ic("chevron-down", "ic-16"));
    schluesselBeschriftung.textContent = gewaehlt.schluessel_beschriftung;
    const ohne = gewaehlt.key === "keiner";
    schluesselFeld.disabled = ohne;
    schluesselFeld.placeholder = ohne ? "Kein Schlüssel nötig" : gewaehlt.key === "passwort" ? "Passwort" : "";
    erzeugen.classList.toggle("versteckt", !gewaehlt.erzeugbar);
    ersetzen(hinweis, ic(gewaehlt.art === "spiel" ? "triangle-alert" : gewaehlt.art === "pq" ? "atom" : "shield-check"),
      h("div", gewaehlt.art === "spiel"
        ? [h("b", { text: "Nur zum Spielen. " }), "In Sekunden zu knacken – schütze damit nichts, was geheim bleiben soll."]
        : gewaehlt.art === "pq"
          ? [h("b", { text: "Post-Quanten-Hybrid. " }), "ML-KEM-768 zusammen mit X25519: mindestens so stark wie heute üblich, und auch gegen künftige Quantencomputer gedacht."]
          : [h("b", { text: "Sicher. " }), "Echte, geprüfte Kryptografie aus der Bibliothek „cryptography“."]));
    hinweis.className = "banner" + (gewaehlt.art === "spiel" ? " warnung" : "");
  };

  wahlKnopf.addEventListener("click", () => {
    const r2 = wahlKnopf.getBoundingClientRect();
    const liste = h("div");
    for (const art of ["sicher", "pq", "spiel"]) {
      const gruppe = verfahren.filter((v) => v.art === art);
      if (!gruppe.length) continue;
      const [titel, klasse, symbol] = ARTEN[art];
      liste.append(h("div.gruppe-name", h("span.marke." + klasse, ic(symbol, "ic-14"), titel)));
      for (const v of gruppe) {
        liste.append(h("button", { type: "button", role: "menuitemradio", "aria-checked": String(v === gewaehlt), onclick: () => {
          gewaehlt = v; zeigen(); document.querySelector(".menue")?.remove();
        } }, v.name));
      }
    }
    menue(r2.left, r2.bottom + 6, [liste], { klasse: "verfahrensliste" });
  });

  async function schluesselErzeugen() {
    const x = await rufe("schluessel_erzeugen", gewaehlt.name);
    if (!x.ok) { toast(x.fehler, "fehler"); return; }
    if (x.oeffentlich) {
      blatt((zu) => [
        h("h2", { text: "Neues Schlüsselpaar" }),
        h("p", { text: "Den öffentlichen Schlüssel gibst du weiter – damit kann man dir etwas verschlüsseln. Den privaten behältst du, nur er entschlüsselt." }),
        h("label.beschriftung", { text: "Öffentlich (weitergeben)" }), h("div.ausgabe", { style: { "min-height": "auto" }, text: x.oeffentlich }),
        h("label.beschriftung", { style: { "margin-top": "12px" }, text: "Privat (geheim halten)" }), h("div.ausgabe", { style: { "min-height": "auto" }, text: x.privat }),
        h("div.aktionen",
          knopf("Öffentlich kopieren", () => navigator.clipboard?.writeText(x.oeffentlich)),
          knopf("Im Schlüsselbund speichern", async () => {
            const name = await eingabe("Name für das Schlüsselpaar", "", { platzhalter: "z. B. Mein PQ-Schlüssel", ok: "Speichern" });
            if (!name) return;
            const s = await rufe("schluessel_speichern", name, gewaehlt.key, { privat: x.privat, oeffentlich: x.oeffentlich }, gewaehlt.name);
            if (s.ok) { toast("Gespeichert"); zu(); } else toast(s.fehler, "fehler");
          }, "primaer")),
      ], { breite: 620 });
      schluesselFeld.value = x.oeffentlich;
    } else {
      schluesselFeld.value = x.schluessel;
    }
  }

  async function bundMenue(anker) {
    const b = await rufe("schluesselbund");
    const passend = (b.liste || []).filter((k) => k.typ === gewaehlt.key);
    const r2 = anker.getBoundingClientRect();
    menue(r2.left, r2.bottom + 6, passend.length ? passend.map((k) => ({
      text: k.label, symbol: "key-round", aktion: async () => {
        const w = await rufe("schluessel_wert", k.label, verschluesseln);
        if (w.ok) schluesselFeld.value = w.wert; else toast(w.fehler, "fehler");
      },
    })) : [h("div", { style: { padding: "10px", color: "var(--text-2)", font: "var(--t-hinweis)" }, text: "Kein passender Schlüssel gespeichert." })]);
  }
  let verschluesseln = true;

  const ausfuehren = async (modus) => {
    verschluesseln = modus === "ver";
    const x = await rufe("text_verarbeiten", gewaehlt.name, modus, eingabeFeld.value, schluesselFeld.value);
    ausgabe.classList.toggle("leer-hinweis", !x.ok);
    if (!x.ok) { ausgabe.textContent = x.fehler; ausgabe.style.setProperty("color", "var(--rot)"); kopieren.disabled = tauschen.disabled = true; return; }
    ausgabe.style.removeProperty("color");
    ausgabe.textContent = x.ergebnis;
    kopieren.disabled = tauschen.disabled = false;
  };

  el.append(h("div.werkbank",
    h("div.karte",
      h("h3", ic("pencil", "ic-16"), "Eingabe"),
      wahlKnopf,
      eingabeFeld,
      h("div", { style: { height: "14px" } }),
      schluesselBeschriftung, schluesselFeld,
      h("div.zeile-felder", { style: { "margin-top": "10px" } }, erzeugen, ausBund),
      h("div.zeile-felder", { style: { "margin-top": "18px" } },
        knopf("Verschlüsseln", () => ausfuehren("ver"), "primaer gross", "lock"),
        knopf("Entschlüsseln", () => ausfuehren("ent"), "gross", "lock-open"))),
    h("div.karte",
      h("h3", ic("file-text", "ic-16"), "Ergebnis"),
      ausgabe,
      h("div.zeile-felder", { style: { "margin-top": "12px" } }, kopieren, tauschen),
      h("div", { style: { height: "16px" } }),
      hinweis)));
  zeigen();
}

/* ------------------------------------------------------------- Dateien */
function dateiWerkzeug(el) {
  let format = "vp4";
  const auftraege = h("div", h("p.auftraege-leer", { style: { color: "var(--text-3)", margin: "8px 0 0", font: "var(--t-hinweis)" }, text: "Noch nichts – oben eine Datei oder einen Ordner wählen." }));
  const segment = h("div.segment", { style: { width: "260px" } });
  const pille = h("span.pille");
  const formate = [["vp4", ".vp4 (VP4)"], ["age", ".age (offen)"]];
  segment.append(pille, ...formate.map(([id, text]) => h("button", { type: "button", "aria-selected": String(id === format), onclick: (e) => {
    format = id;
    for (const b of segment.querySelectorAll("button")) b.setAttribute("aria-selected", String(b === e.currentTarget));
    pilleSetzen(); formatHinweis();
  } }, text)));
  const pilleSetzen = () => { const a = segment.querySelector('[aria-selected="true"]'); if (a) { pille.style.setProperty("width", `${a.offsetWidth}px`); pille.style.setProperty("transform", `translateX(${a.offsetLeft - 3}px)`); } };
  setTimeout(pilleSetzen);
  const hinweisEl = h("p", { style: { color: "var(--text-2)", margin: "10px 0 0" } });
  const formatHinweis = () => { hinweisEl.textContent = format === "vp4"
    ? ".vp4 kann nur VP4 öffnen – dafür steckt auch der Dateiname verschlüsselt drin, und Ordner gehen am Stück."
    : ".age kann auch das offizielle Programm „age“ öffnen (für Linux, Mac, Windows). Ordner werden vorher gepackt."; };
  formatHinweis();

  const starten = async (modus, art) => {
    const passwort = await eingabe(modus === "ver" ? "Passwort für die Datei" : "Passwort zum Öffnen",
      modus === "ver" ? "Ohne dieses Passwort bekommt niemand die Datei wieder auf – auch du nicht." : "", { platzhalter: "Passwort", ok: modus === "ver" ? "Verschlüsseln" : "Entschlüsseln" });
    if (!passwort) return;
    const r = await rufe(modus === "ver" ? "datei_verschluesseln" : "datei_entschluesseln", { format, art, passwort });
    if (!r.ok) { if (r.fehler) toast(r.fehler, "fehler", 5000); return; }
    const balken = h("div");
    const text = h("small", { text: "Startet …" });
    const abbrechen = h("button.rund.klein", { type: "button", title: "Abbrechen", "aria-label": "Abbrechen", onclick: () => rufe("auftrag_abbrechen", r.auftrag) }, ic("x", "ic-14"));
    const zeile = h("div.auftrag", h("div.symbol", ic(modus === "ver" ? "lock" : "lock-open")),
      h("div.mitte", h("b", { text: r.name }), h("div.fortschritt", { style: { margin: "6px 0 4px" } }, balken), text), abbrechen);
    auftraege.querySelector(".auftraege-leer")?.remove();
    auftraege.prepend(zeile);
    const ab = [
      auf("fortschritt", (e) => { if (e.auftrag !== r.auftrag) return; balken.style.setProperty("width", `${Math.round(e.anteil * 100)}%`); text.textContent = `${groesse(e.fertig)} von ${groesse(e.gesamt)}`; }),
      auf("auftrag_fertig", (e) => { if (e.auftrag !== r.auftrag) return; balken.style.setProperty("width", "100%"); text.textContent = `Fertig: ${e.ziel}`; abbrechen.remove(); ab.forEach((f) => f()); toast("Fertig"); }),
      auf("auftrag_fehler", (e) => { if (e.auftrag !== r.auftrag) return; text.textContent = e.fehler; text.style.setProperty("color", "var(--rot)"); abbrechen.remove(); ab.forEach((f) => f()); }),
    ];
  };

  el.append(h("div.inhalt-schmal",
    h("div.karte",
      segment, hinweisEl,
      h("div", { style: { display: "grid", "grid-template-columns": "1fr 1fr", gap: "12px", "margin-top": "18px" } },
        h("div.ablagezone", { onclick: () => starten("ver", "datei") }, ic("lock", "ic-20"), h("b", { text: "Datei verschlüsseln" }), h("small", { text: "Datei auswählen" })),
        h("div.ablagezone", { onclick: () => starten("ver", "ordner") }, ic("folder-lock", "ic-20"), h("b", { text: "Ordner verschlüsseln" }), h("small", { text: "Ordner auswählen" }))),
      h("div", { style: { "margin-top": "12px" } }, knopf("Verschlüsselte Datei öffnen …", () => starten("ent", "datei"), "breit gross", "lock-open"))),
    h("div", { style: { height: "16px" } }),
    h("div.banner", ic("info"), h("div", h("b", { text: "Das Original bleibt liegen. " }), "VP4 löscht von sich aus nichts. Und ehrlich: Auf einer SSD bekommt man Daten mit Löschen ohnehin nicht sicher weg.")),
    h("div", { style: { height: "16px" } }),
    h("div.karte", h("h3", { style: { font: "var(--t-kopf)", margin: "0 0 4px" }, text: "Aufträge" }), auftraege)));
}

/* -------------------------------------------------------- Schlüsselbund */
async function schluesselWerkzeug(el) {
  const zeichnen = async () => {
    const r = await rufe("schluesselbund");
    leeren(el);
    const liste = r.liste || [];
    el.append(h("div.inhalt-schmal",
      h("div.gruppe", liste.length ? liste.map((k) => h("div.schluesselzeile",
        h("span.symbol", { style: { display: "grid", "place-items": "center", width: "32px", height: "32px", "border-radius": "9px", background: "var(--feld)" } }, ic("key-round", "ic-16")),
        h("div.mitte", h("b", { text: k.label }), h("small", { text: [k.verfahren || k.typ, k.meta].filter(Boolean).join(" · ") })),
        h("button.rund", { type: "button", title: "Kopieren", "aria-label": "Kopieren", onclick: async () => { const w = await rufe("schluessel_wert", k.label, true); if (w.ok) { await navigator.clipboard?.writeText(w.wert); toast("Kopiert"); } } }, ic("copy", "ic-16")),
        h("button.rund", { type: "button", title: "Löschen", "aria-label": "Löschen", onclick: async () => {
          if (await bestaetigen(`„${k.label}“ löschen?`, "Was damit verschlüsselt wurde, bekommst du ohne den Schlüssel nicht mehr auf.", { ja: "Löschen", gefahr: true, symbol: "trash-2" })) {
            await rufe("schluessel_loeschen", k.label); zeichnen();
          } } }, ic("trash-2", "ic-16"))))
        : h("div.eintrag", h("div.titel", { style: { color: "var(--text-2)" }, text: "Noch keine Schlüssel gespeichert. Erzeuge einen im Werkzeug „Text verschlüsseln“." }))),
      h("div.banner", ic("lock"), h("div", "Der Schlüsselbund liegt verschlüsselt auf deinem PC – mit Argon2id aus deinem Master-Passwort abgeleitet."))));
  };
  zeichnen();
}

/* ---------------------------------------------------------- Signieren */
function signaturWerkzeug(el) {
  const text = h("textarea.feld", { rows: 6, placeholder: "Der Text, um den es geht" });
  const privat = h("textarea.feld.mono", { rows: 2, placeholder: "Privater Schlüssel (zum Signieren)" });
  const oeffentlich = h("textarea.feld.mono", { rows: 2, placeholder: "Öffentlicher Schlüssel (zum Prüfen)" });
  const signatur = h("textarea.feld.mono", { rows: 3, placeholder: "Signatur" });
  const ergebnis = h("div");
  el.append(h("div.werkbank",
    h("div.karte", h("h3", ic("pencil", "ic-16"), "Text"), text,
      h("div.banner", { style: { "margin-top": "14px" } }, ic("info"), h("div", "Eine Signatur ", h("b", { text: "verschlüsselt nichts" }), ". Der Text bleibt lesbar – man kann nur beweisen, dass er von dir ist und nicht verändert wurde."))),
    h("div.karte",
      h("h3", ic("key-round", "ic-16"), "Schlüssel und Signatur"),
      h("div.zeile-felder", { style: { "margin-bottom": "10px" } }, knopf("Neues Schlüsselpaar", async () => {
        const r = await rufe("signatur_paar"); if (r.ok) { privat.value = r.privat; oeffentlich.value = r.oeffentlich; }
      }, "", "sparkles")),
      privat, h("div", { style: { height: "8px" } }), oeffentlich, h("div", { style: { height: "8px" } }), signatur,
      h("div.zeile-felder", { style: { "margin-top": "14px" } },
        knopf("Signieren", async () => { const r = await rufe("signieren", text.value, privat.value); if (r.ok) { signatur.value = r.signatur; ersetzen(ergebnis); } else toast(r.fehler, "fehler"); }, "primaer", "signature"),
        knopf("Prüfen", async () => {
          const r = await rufe("signatur_pruefen", text.value, signatur.value, oeffentlich.value);
          ersetzen(ergebnis, r.ok && r.gueltig
            ? h("div.banner", { style: { "margin-top": "14px", background: "rgba(52,199,89,.14)" } }, ic("circle-check"), h("div", h("b", { text: "Echt. " }), "Der Text stammt vom Besitzer dieses Schlüssels und ist unverändert."))
            : h("div.banner.gefahr", { style: { "margin-top": "14px" } }, ic("circle-alert"), h("div", h("b", { text: "Ungültig. " }), r.fehler || "Text, Signatur oder Schlüssel passen nicht zusammen.")));
        }, "", "check")),
      ergebnis)));
}

/* -------------------------------------------------------- Prüfsummen */
function pruefsummenWerkzeug(el) {
  const ausgabe = h("div.gruppe");
  const zeigen = (r) => {
    if (!r.ok) { toast(r.fehler, "fehler"); return; }
    ersetzen(ausgabe, h("div.eintrag", h("div.titel", h("b", { text: r.name || "Text" }), r.groesse ? h("small", { text: groesse(r.groesse) }) : null)),
      Object.entries(r.summen).map(([alg, wert]) => h("div.eintrag", h("div.titel", h("small", { text: alg }), h("span.mono.waehlbar", { style: { "overflow-wrap": "anywhere", font: "13px/18px var(--schrift-mono)" }, text: wert })),
        h("button.rund", { type: "button", title: "Kopieren", "aria-label": "Kopieren", onclick: () => navigator.clipboard?.writeText(wert) }, ic("copy", "ic-16")))));
  };
  const text = h("textarea.feld", { rows: 4, placeholder: "Text – oder unten eine Datei wählen" });
  el.append(h("div.inhalt-schmal",
    h("div.karte", text,
      h("div.zeile-felder", { style: { "margin-top": "12px" } },
        knopf("Für Text berechnen", async () => zeigen(await rufe("pruefsumme_text", text.value)), "primaer"),
        knopf("Datei wählen …", async () => zeigen(await rufe("pruefsumme_datei")), "", "file"))),
    h("div", { style: { height: "16px" } }), ausgabe));
}

/* ----------------------------------------------------------- Obsidian */
async function obsidianWerkzeug(el) {
  const zeichnen = async () => {
    const s = await rufe("obsidian_status");
    leeren(el);
    el.append(h("div.inhalt-schmal",
      h("div.gruppe",
        h("div.eintrag", ic("folder"), h("div.titel", "Vault-Ordner", h("small", { text: s.ordner || "Noch nicht gewählt" })),
          knopf("Wählen …", async () => { const r = await rufe("obsidian_ordner_waehlen"); if (r.ok) zeichnen(); })),
        h("div.eintrag", ic("file-text"), h("div.titel", "Notiz", h("small", { text: s.notiz || "–" })))),
      h("div.zeile-felder",
        knopf("Als Klartext exportieren", async () => {
          if (!(await bestaetigen("Im Klartext exportieren?", "In der Notiz stehen deine Schlüssel dann lesbar. Wird der Vault synchronisiert (Obsidian Sync, iCloud, Dropbox), wandern sie mit.", { ja: "Trotzdem exportieren", symbol: "triangle-alert" }))) return;
          const r = await rufe("obsidian_export", null); r.ok ? toast(`${r.anzahl} Schlüssel exportiert`) : toast(r.fehler, "fehler");
        }, "", "upload"),
        knopf("Verschlüsselt exportieren (age)", async () => {
          const pw = await eingabe("Passwort für die Notiz", "Die Notiz wird mit age verschlüsselt – lesbar nur mit diesem Passwort.", { platzhalter: "Passwort", ok: "Exportieren" });
          if (!pw) return;
          const r = await rufe("obsidian_export", pw); r.ok ? toast(`${r.anzahl} Schlüssel verschlüsselt exportiert`) : toast(r.fehler, "fehler");
        }, "primaer", "lock"),
        knopf("Importieren", async () => {
          const r = await rufe("obsidian_import", null);
          if (r.ok) { toast(`${r.anzahl} Schlüssel übernommen`); return; }
          if (r.passwort_noetig) {
            const pw = await eingabe("Passwort der Notiz", "", { platzhalter: "Passwort", ok: "Importieren" });
            if (!pw) return;
            const r2 = await rufe("obsidian_import", pw); r2.ok ? toast(`${r2.anzahl} Schlüssel übernommen`) : toast(r2.fehler, "fehler");
          } else toast(r.fehler, "fehler");
        }, "", "download")),
      h("div", { style: { height: "16px" } }),
      h("div.banner.warnung", ic("triangle-alert"), h("div", h("b", { text: "Klartext heisst Klartext. " }), "Nimm lieber den verschlüsselten Export, wenn dein Vault irgendwo synchronisiert wird."))));
  };
  zeichnen();
}
