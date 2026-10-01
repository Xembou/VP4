// =====================================================================
//  kontakte.js - Freunde hinzufügen, Anfragen, Sicherheitsnummer
// =====================================================================

import { h, ic, knopf, avatar, ersetzen } from "../dom.js";
import { rufe } from "../bruecke.js";
import { blatt, toast, bestaetigen } from "../blaetter.js";
import { zustand } from "../zustand.js";

export function freundHinzufuegen() {
  blatt((zu) => {
    const feld = h("input.feld.mono", { placeholder: "XXXXX-XXXXX oder VP4C1-…", spellcheck: "false", autocomplete: "off", "aria-label": "ID oder Freundescode" });
    const fehler = h("div.fehlertext", { role: "alert" });
    const los = async () => {
      if (!feld.value.trim()) return;
      const r = await rufe("kontakt_hinzufuegen", feld.value.trim());
      if (!r.ok) { fehler.textContent = r.fehler; feld.classList.add("fehler"); return; }
      zu();
      toast(r.meldung || "Anfrage geschickt");
    };
    feld.addEventListener("keydown", (e) => e.key === "Enter" && los());
    feld.addEventListener("input", () => { feld.classList.remove("fehler"); fehler.textContent = ""; });
    return [
      h("div.kopfsymbol", ic("user-plus", "ic-20")),
      h("h2", { text: "Freund hinzufügen" }),
      h("p", { text: "Gib die ID deines Freundes ein oder füge seinen Freundescode ein. Er bekommt eine Anfrage und muss sie annehmen – erst dann tauscht ihr automatisch Schlüssel aus." }),
      feld, fehler,
      h("div.banner", { style: { "margin-top": "6px" } }, ic("info"),
        h("div", "Deine eigene ID: ", h("b.mono", { text: zustand.profil?.id || "" }),
          " · ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); freundescodeKopieren(); } , text: "Freundescode kopieren" }))),
      h("div.aktionen", knopf("Abbrechen", () => zu()), knopf("Anfrage senden", los, "primaer")),
    ];
  }, { breite: 500 });
}

export async function freundescodeKopieren() {
  const r = await rufe("freundescode");
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  await navigator.clipboard?.writeText(r.code);
  toast("Freundescode kopiert – schick ihn deinem Freund");
}

/**
 * Kontakt blockieren - mit Rückfrage. Gibt true zurück, wenn es geklappt hat.
 * Aufgehoben wird es, indem man die ID wieder hinzufügt (kern/nachrichten.py).
 */
export async function kontaktBlockieren(k, { nachfragen = true } = {}) {
  if (nachfragen && !(await bestaetigen(`${k.name || k.id} blockieren?`,
    "Du bekommst keine Nachrichten und keine Anfragen mehr von dieser ID; er oder sie erfährt davon nichts. Aufheben: die ID einfach wieder als Freund hinzufügen.",
    { ja: "Blockieren", gefahr: true, symbol: "circle-alert" }))) return false;
  const r = await rufe("kontakt_blockieren", k.id);
  if (!r.ok) { toast(r.fehler, "fehler"); return false; }
  toast(`${k.name || k.id} ist blockiert`);
  return true;
}

export async function anfragenBlatt() {
  const r = await rufe("anfragen");
  const liste = r.ok ? r.liste : [];
  blatt((zu) => {
    const inhalt = h("div.gruppe");
    const zeichnen = (l) => {
      ersetzen(inhalt, l.length ? l.map((a) => h("div.eintrag",
        avatar(a.name, a.farbe, "mittel"),
        h("div.titel", h("b", { text: a.name }), h("small.mono", { text: a.id })),
        a.richtung === "raus"
          ? h("span.wert", { text: "wartet …" })
          : h("div", { style: { display: "flex", gap: "6px", "flex-wrap": "wrap", "justify-content": "flex-end" } },
              // Die Rückfrage ist ein eigenes Blatt - danach kommt die Liste wieder
              knopf("Blockieren", async () => { await kontaktBlockieren(a); anfragenBlatt(); }, "leise gefahr-schrift"),
              knopf("Ablehnen", () => antwort(a, false)),
              knopf("Annehmen", () => antwort(a, true), "primaer"))))
        : h("div.eintrag", h("div.titel", { style: { color: "var(--text-2)" }, text: "Keine offenen Anfragen." })));
    };
    const antwort = async (a, ja) => {
      const x = await rufe("anfrage_beantworten", a.id, ja);
      if (!x.ok) { toast(x.fehler, "fehler"); return; }
      toast(ja ? `${a.name} ist jetzt dein Kontakt` : "Abgelehnt");
      zeichnen((await rufe("anfragen")).liste || []);
    };
    zeichnen(liste);
    return [h("h2", { text: "Anfragen" }),
      h("p", { text: "Erst wenn du annimmst, kann jemand dir schreiben." }),
      inhalt, h("div.aktionen", knopf("Fertig", () => zu(), "primaer"))];
  }, { breite: 520 });
}

/** Sicherheitsnummer vergleichen - der Schutz gegen einen Mitleser im Kanal. */
export async function sicherheitBlatt(id) {
  const r = await rufe("kontakt_info", id);
  if (!r.ok) { toast(r.fehler, "fehler"); return; }
  const k = r.kontakt;
  blatt((zu) => {
    const bloecke = (r.sicherheitsnummer || "").split(" ");
    const verifiziertKnopf = knopf(k.verifiziert ? "Verifizierung aufheben" : "Als verifiziert markieren", async () => {
      const x = await rufe("verifizieren", id, !k.verifiziert);
      if (!x.ok) { toast(x.fehler, "fehler"); return; }
      zu();
      toast(k.verifiziert ? "Verifizierung aufgehoben" : `${k.name} ist verifiziert`);
    }, k.verifiziert ? "" : "primaer");
    return [
      k.schluessel_geaendert
        ? h("div.banner.gefahr", { style: { "margin-bottom": "16px" } }, ic("shield-alert"),
            h("div", h("b", { text: `Der Schlüssel von ${k.name} hat sich geändert. ` }),
              "Das passiert, wenn VP4 neu eingerichtet wurde – oder wenn sich jemand dazwischenschalten will. Vergleicht die Nummer, bevor ihr weiterschreibt."))
        : null,
      h("div", { style: { display: "flex", "align-items": "center", gap: "14px", "margin-bottom": "14px" } },
        avatar(k.name, k.farbe, "gross"),
        h("div", h("h2", { style: { margin: "0" }, text: k.name }), h("div.mono", { style: { color: "var(--text-2)" }, text: k.id, "aria-label": `ID ${k.id}` }))),
      h("p", { text: `Vergleicht diese Nummer – am besten am Telefon oder nebeneinander. Steht bei ${k.name} genau dieselbe, liest niemand mit.` }),
      h("div.sicherheitsnummer", bloecke.map((b) => h("span", { text: b }))),
      h("div.banner", ic("lock"), h("div",
        h("b", { text: "Ende-zu-Ende verschlüsselt. " }),
        "Nur ihr zwei könnt die Nachrichten lesen, Discord sieht nur Geheimtext. ",
        h("b", { text: "Nicht geschützt: " }),
        "wer wann wem schreibt. Und noch ohne „Forward Secrecy“ – wer später dein Gerät knackt, kann auch alte Nachrichten öffnen.")),
      h("div.aktionen",
        k.schluessel_geaendert ? knopf("Neuen Schlüssel annehmen", async () => { await rufe("schluessel_annehmen", id); zu(); }, "gefahr") : null,
        knopf("Schliessen", () => zu()), verifiziertKnopf),
    ];
  }, { breite: 520 });
}
