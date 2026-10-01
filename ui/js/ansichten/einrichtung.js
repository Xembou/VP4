// =====================================================================
//  einrichtung.js - der erste Start und der Sperrbildschirm
// =====================================================================

import { h, ic, knopf, ersetzen, avatar } from "../dom.js";
import { rufe } from "../bruecke.js";
import { toast } from "../blaetter.js";
import { AKZENTE } from "../zustand.js";

const FARBEN = Object.values(AKZENTE).map(([hell]) => hell);

/** Zeigt die Einrichtung. Ruft fertig(profil) auf, wenn alles steht. */
export function einrichtungZeigen(ziel, status, fertig) {
  const daten = { name: "", farbe: FARBEN[Math.floor(Math.random() * FARBEN.length)], passwort: "", windows: status.windows_verfuegbar !== false, altPasswort: "" };
  const alteDaten = status.alte_daten;     // VP4 4.x gefunden?
  const schritte = ["willkommen", "profil", "passwort", ...(alteDaten ? ["uebernahme"] : []), "id"];
  let i = 0;
  let profil = null;

  const karte = h("div.einrichtung.glas.glas-stark", { role: "dialog", "aria-label": "VP4 einrichten" });
  ersetzen(ziel, h("div.vollbild", karte));

  const punkte = () => h("div.punkte", schritte.map((_, j) => h("i" + (j === i ? ".an" : ""))));
  const zeigen = () => {
    const name = schritte[i];
    ersetzen(karte, h("div.schritt", SCHRITTE[name]()), punkte());
    setTimeout(() => karte.querySelector("input")?.focus(), 80);
  };
  const weiter = () => { i += 1; zeigen(); };
  const zurueck = () => { i = Math.max(0, i - 1); zeigen(); };

  const SCHRITTE = {
    willkommen: () => [
      h("div.logo", ic("message-circle")),
      h("h1", { text: "Willkommen bei VP4" }),
      h("p.unter", { text: "Chatten mit Freunden – Ende-zu-Ende verschlüsselt. Ohne Konto, ohne Telefonnummer." }),
      h("ul.punktliste",
        h("li", h("span.symbol", { style: { background: "#0088FF" } }, ic("lock", "ic-16")), h("div", h("b", { text: "Nur ihr könnt mitlesen" }), h("span", { text: "Die Nachrichten laufen über Discord, aber Discord sieht nur Geheimtext." }))),
        h("li", h("span.symbol", { style: { background: "#34C759" } }, ic("users", "ic-16")), h("div", h("b", { text: "Gruppen und Communities" }), h("span", { text: "Mit Kanälen wie bei Discord – beitreten per Code." }))),
        h("li", h("span.symbol", { style: { background: "#FF8D28" } }, ic("wrench", "ic-16")), h("div", h("b", { text: "Werkzeuge" }), h("span", { text: "Texte, Dateien und Ordner verschlüsseln, auch Post-Quanten." })))),
      h("div.knoepfe", knopf("Los geht’s", weiter, "primaer gross")),
    ],

    profil: () => {
      const feld = h("input.feld", { placeholder: "Dein Name", value: daten.name, maxlength: "64" });
      const vorschau = h("div", { style: { display: "flex", "justify-content": "center", "margin-bottom": "18px" } }, avatar(daten.name || "?", daten.farbe, "gross"));
      const neuZeichnen = () => ersetzen(vorschau, avatar(daten.name || "?", daten.farbe, "gross"));
      feld.addEventListener("input", () => { daten.name = feld.value; neuZeichnen(); });
      const farben = h("div.farbwahl", { style: { "justify-content": "center", "margin-top": "16px" } }, FARBEN.map((f) => h("button", {
        type: "button", role: "radio", "aria-label": "Farbe", "aria-checked": String(f === daten.farbe), style: { "--f": f },
        onclick: (e) => { daten.farbe = f; for (const b of farben.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); neuZeichnen(); },
      })));
      const los = () => { if (!daten.name.trim()) { feld.classList.add("fehler"); feld.focus(); return; } weiter(); };
      feld.addEventListener("keydown", (e) => e.key === "Enter" && los());
      return [
        h("h1", { text: "Wie heisst du?" }),
        h("p.unter", { text: "So sehen dich deine Freunde. Du kannst das später ändern." }),
        vorschau, feld, farben,
        h("div.knoepfe", knopf("Zurück", zurueck, "gross"), knopf("Weiter", los, "primaer gross")),
      ];
    },

    passwort: () => {
      const pw = h("input.feld", { type: "password", placeholder: "Master-Passwort", autocomplete: "new-password" });
      const pw2 = h("input.feld", { type: "password", placeholder: "Wiederholen", autocomplete: "new-password", style: { "margin-top": "8px" } });
      const balken = h("div.staerke", h("span"), h("span"), h("span"), h("span"));
      const staerkeText = h("div.beschriftung", { style: { margin: "6px 0 0 2px", "min-height": "16px" } });
      const aufgeschrieben = h("input", { type: "checkbox" });
      const windows = h("input", { type: "checkbox", checked: daten.windows, disabled: status.windows_verfuegbar === false });
      const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "8px" } });
      let warte;
      pw.addEventListener("input", () => {
        clearTimeout(warte);
        warte = setTimeout(async () => {
          const r = await rufe("passwort_staerke", pw.value);
          const stufe = r.ok ? r.stufe : 0;
          const farben = ["var(--rot)", "var(--orange)", "var(--gelb)", "var(--gruen)"];
          [...balken.children].forEach((s, j) => s.style.setProperty("background", j < stufe ? farben[Math.max(0, stufe - 1)] : "var(--feld)"));
          staerkeText.textContent = pw.value ? (r.text || "") : "";
        }, 120);
      });
      const los = async () => {
        fehler.textContent = "";
        if (pw.value.length < 8) { fehler.textContent = "Mindestens 8 Zeichen."; return; }
        if (pw.value !== pw2.value) { fehler.textContent = "Die beiden Passwörter sind nicht gleich."; return; }
        if (!aufgeschrieben.checked) { fehler.textContent = "Bitte bestätige, dass du es dir aufgeschrieben hast."; return; }
        daten.passwort = pw.value;
        daten.windows = windows.checked;
        const knopfEl = karte.querySelector(".knopf.primaer");
        knopfEl.disabled = true;
        knopfEl.textContent = "Wird eingerichtet …";
        const r = await rufe("einrichten", { name: daten.name.trim(), avatar_farbe: daten.farbe, passwort: daten.passwort, windows_merken: daten.windows });
        if (!r.ok) { fehler.textContent = r.fehler; knopfEl.disabled = false; knopfEl.textContent = "Einrichten"; return; }
        profil = r.profil;
        weiter();
      };
      pw2.addEventListener("keydown", (e) => e.key === "Enter" && los());
      return [
        h("h1", { text: "Master-Passwort" }),
        h("p.unter", { text: "Es schützt deinen Tresor: deine Schlüssel, Kontakte und Chats auf diesem PC." }),
        pw, balken, staerkeText, pw2,
        h("div.banner.gefahr", { style: { "margin-top": "14px" } }, ic("triangle-alert"), h("div", h("b", { text: "Es gibt keine Wiederherstellung. " }), "Vergisst du es, ist alles auf diesem PC weg. Niemand kann es zurückholen – auch VP4 nicht.")),
        h("label.ankreuzen", aufgeschrieben, h("span", { text: "Ich habe mir das Passwort aufgeschrieben." })),
        h("label.ankreuzen", windows, h("span", h("b", { style: { color: "var(--text)" }, text: "Windows merkt sich das Passwort. " }),
          status.windows_verfuegbar === false ? "Geht auf diesem System nicht – du gibst es bei jedem Start ein." : "Dann musst du es nicht bei jedem Start eingeben. Nur dein Windows-Konto kann es entsperren.")),
        fehler,
        h("div.knoepfe", knopf("Zurück", zurueck, "gross"), knopf("Einrichten", los, "primaer gross")),
      ];
    },

    uebernahme: () => {
      const pw = h("input.feld", { type: "password", placeholder: "Altes Master-Passwort" });
      const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "8px" } });
      const los = async () => {
        const r = await rufe("migrieren", pw.value);
        if (!r.ok) { fehler.textContent = r.fehler; return; }
        toast(`${r.schluessel} Schlüssel übernommen`);
        daten.alteKontakte = r.alte_kontakte || [];
        weiter();
      };
      pw.addEventListener("keydown", (e) => e.key === "Enter" && los());
      return [
        h("div.logo", { style: { background: "linear-gradient(160deg,#FFB86B,#FF8D28)" } }, ic("download")),
        h("h1", { text: "VP4 4 gefunden" }),
        h("p.unter", { text: `Neben dem Programm liegen Daten der alten Version (${alteDaten.schluessel ?? 0} Schlüssel). Mit dem alten Master-Passwort holst du deinen Schlüsselbund und deine Einstellungen herüber.` }),
        pw, fehler,
        h("div.banner", { style: { "margin-top": "6px" } }, ic("info"), h("div", "Kontakte und Gruppen lassen sich nicht übernehmen – VP4 5 verschlüsselt den Chat ganz neu. Du siehst danach eine Liste, wen du neu hinzufügen solltest.")),
        h("div.knoepfe", knopf("Überspringen", weiter, "gross"), knopf("Übernehmen", los, "primaer gross")),
      ];
    },

    id: () => [
      h("div", { style: { display: "flex", "justify-content": "center", "margin-bottom": "14px" } }, avatar(profil?.name || daten.name, profil?.avatar_farbe || daten.farbe, "gross")),
      h("h1", { text: "Das ist deine ID" }),
      h("p.unter", { text: "Gib sie Freunden, damit sie dich hinzufügen können. Sie gehört fest zu deinen Schlüsseln." }),
      h("div.id-gross", { text: profil?.id || "" }),
      h("div", { style: { display: "flex", gap: "10px" } },
        knopf("ID kopieren", async () => { await navigator.clipboard?.writeText(profil.id); toast("Kopiert"); }, "breit", "copy"),
        knopf("Freundescode kopieren", async () => { const r = await rufe("freundescode"); if (r.ok) { await navigator.clipboard?.writeText(r.code); toast("Freundescode kopiert"); } }, "breit", "qr-code")),
      daten.alteKontakte?.length ? h("div.banner", { style: { "margin-top": "14px" } }, ic("users"), h("div", h("b", { text: "Bitte neu hinzufügen: " }), daten.alteKontakte.map((k) => k.name || k.id).join(", "))) : null,
      h("div.knoepfe", knopf("Fertig", () => fertig(profil), "primaer gross")),
    ],
  };
  zeigen();
}

/** Sperrbildschirm: Master-Passwort eingeben. */
export function sperreZeigen(ziel, status, entsperrt) {
  const pw = h("input.feld", { type: "password", placeholder: "Master-Passwort", autocomplete: "current-password" });
  const fehler = h("div.beschriftung", { style: { color: "var(--rot)", "min-height": "16px", "margin-top": "8px", "text-align": "center" } });
  const los = async () => {
    if (!pw.value) return;
    const knopfEl = karte.querySelector(".knopf.primaer");
    knopfEl.disabled = true;
    const r = await rufe("entsperren", pw.value);
    knopfEl.disabled = false;
    if (!r.ok) {
      fehler.textContent = r.fehler;
      pw.value = "";
      karte.animate([{ transform: "translateX(0)" }, { transform: "translateX(-10px)" }, { transform: "translateX(10px)" }, { transform: "translateX(-6px)" }, { transform: "translateX(0)" }], { duration: 360, easing: "ease-out" });
      pw.focus();
      return;
    }
    entsperrt(r);
  };
  pw.addEventListener("keydown", (e) => e.key === "Enter" && los());
  const p = status.profil || {};
  const karte = h("div.einrichtung.glas.glas-stark", { style: { width: "min(380px, 100%)", "text-align": "center" } },
    h("div", { style: { display: "flex", "justify-content": "center", "margin-bottom": "14px" } }, avatar(p.name || "?", p.avatar_farbe, "gross")),
    h("h1", { style: { "font-size": "22px" }, text: p.name || "VP4" }),
    h("p.unter", { style: { "margin-bottom": "18px" }, text: "VP4 ist gesperrt." }),
    pw, fehler,
    h("div.knoepfe", knopf("Entsperren", los, "primaer gross", "lock-open")),
    h("p", { style: { font: "var(--t-klein)", color: "var(--text-3)", margin: "16px 0 0" }, text: "Passwort vergessen? Dann bleibt nur „neu einrichten“ – das Passwort lässt sich nicht wiederherstellen." }));
  ersetzen(ziel, h("div.vollbild", karte));
  setTimeout(() => pw.focus(), 80);
}
