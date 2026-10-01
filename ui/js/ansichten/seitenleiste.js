// =====================================================================
//  seitenleiste.js - die schwebende Glasleiste links
// =====================================================================

import { h, ic, rundknopf, ersetzen, avatar, zeitKurz, farbeFuer } from "../dom.js";
import { rufe } from "../bruecke.js";
import { menueAn, toast } from "../blaetter.js";
import { zustand, setzen } from "../zustand.js";
import { freundHinzufuegen, anfragenBlatt } from "./kontakte.js";
import { communityErstellen, communityBeitreten, einladungZeigen, kanalAnlegen, communityMenue, gruppeErstellen } from "./gemeinschaft.js";
import { WERKZEUGE } from "./werkzeuge.js";

const BEREICHE = [["chats", "Chats", "message-circle"], ["communities", "Communities", "hash"], ["werkzeuge", "Werkzeuge", "wrench"]];

export class Seitenleiste {
  constructor(el, { oeffnen, werkzeugOeffnen, einstellungenOeffnen }) {
    this.el = el;
    this.oeffnen = oeffnen;
    this.werkzeugOeffnen = werkzeugOeffnen;
    this.einstellungenOeffnen = einstellungenOeffnen;
    this.bauen();
  }

  bauen() {
    this.titel = h("h1");
    this.plus = rundknopf("plus", "Neu", (e) => this.neuMenue(e.currentTarget));
    this.sucheFeld = h("input", { placeholder: "Suchen", "aria-label": "Suchen", value: zustand.suche });
    this.sucheFeld.addEventListener("input", () => { setzen({ suche: this.sucheFeld.value }); this.liste(); });
    this.segment = h("div.segment", { role: "tablist" });
    this.pille = h("span.pille");
    this.segment.append(this.pille, ...BEREICHE.map(([id, text, symbol]) =>
      h("button", { type: "button", role: "tab", title: text, "aria-label": text, dataset: { bereich: id }, onclick: () => this.bereich(id) },
        h("span.nur-schmal", ic(symbol, "ic-16")), h("span.weg-wenn-schmal", { text }))));
    this.listeEl = h("div.leiste-liste", { role: "list" });
    this.fuss = h("div.leiste-fuss");

    ersetzen(this.el,
      h("div.leiste-kopf",
        h("div.leiste-titelzeile.weg-wenn-schmal", this.titel, this.plus),
        h("label.suche.weg-wenn-schmal", ic("search", "ic-16"), this.sucheFeld, h("kbd", { text: "Strg K" })),
        this.segment),
      this.listeEl, this.fuss);
    this.alles();
    new ResizeObserver(() => this.pilleSetzen()).observe(this.segment);
  }

  bereich(id) {
    setzen({ bereich: id, communityOffen: id === "communities" ? zustand.communityOffen : null });
    this.alles();
  }

  alles() {
    const [, text] = BEREICHE.find(([id]) => id === zustand.bereich);
    this.titel.textContent = text;
    this.plus.classList.toggle("versteckt", zustand.bereich === "werkzeuge");
    for (const b of this.segment.querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.bereich === zustand.bereich));
    this.pilleSetzen();
    this.liste();
    this.fussZeichnen();
  }

  pilleSetzen() {
    const aktiv = this.segment.querySelector('[aria-selected="true"]');
    if (!aktiv) return;
    this.pille.style.setProperty("width", `${aktiv.offsetWidth}px`);
    this.pille.style.setProperty("transform", `translateX(${aktiv.offsetLeft - 3}px)`);
  }

  fussZeichnen() {
    const p = zustand.profil || {};
    const v = zustand.verbindung;
    const an = v.lan === "an" || v.discord === "an";
    const fehler = v.discord === "fehler" || v.lan === "fehler";
    const verbindungsText = v.discord === "an" && v.lan === "an" ? "WLAN + Discord" : v.discord === "an" ? "Discord verbunden" : v.lan === "an" ? "Nur WLAN" : fehler ? "Keine Verbindung" : "Verbinde …";
    const punkt = h("i.verbindungspunkt" + (an ? ".an" : fehler ? ".fehler" : ""));
    ersetzen(this.fuss,
      h("div.ich", { title: `${verbindungsText} · Klicken kopiert deine ID`, onclick: async () => { await navigator.clipboard?.writeText(p.id || ""); toast("Deine ID ist kopiert"); } },
        h("div", { style: { position: "relative" } }, avatar(p.name || "?", p.avatar_farbe, "mittel"), punkt),
        h("div.weg-wenn-schmal", { style: { "min-width": "0" } },
          h("b", { text: p.name || "Ich" }),
          h("span", { text: p.id || "" }))),
      rundknopf("settings", "Einstellungen (Strg ,)", () => this.einstellungenOeffnen(), zustand.seite === "einstellungen" ? "an" : ""));
  }

  neuMenue(anker) {
    if (zustand.bereich === "communities") {
      menueAn(anker, [
        { text: "Community gründen", symbol: "sparkles", aktion: () => communityErstellen() },
        { text: "Mit Code beitreten", symbol: "link", aktion: () => communityBeitreten() },
      ]);
      return;
    }
    const anfragen = zustand.anfragen.filter((a) => a.richtung === "rein").length;
    menueAn(anker, [
      { text: "Freund hinzufügen", symbol: "user-plus", aktion: () => freundHinzufuegen() },
      { text: "Gruppe erstellen", symbol: "users", aktion: () => gruppeErstellen() },
      { text: "Gruppe beitreten", symbol: "link", aktion: () => communityBeitreten() },
      "-",
      { text: anfragen ? `Anfragen (${anfragen})` : "Anfragen", symbol: "inbox", aktion: () => anfragenBlatt() },
    ]);
  }

  /* ------------------------------------------------------------- Listen */
  liste() {
    const q = zustand.suche.trim().toLowerCase();
    if (zustand.bereich === "werkzeuge") return this.werkzeugListe();
    if (zustand.bereich === "communities") return this.communityListe(q);

    const reihen = zustand.unterhaltungen
      .filter((u) => u.art !== "kanal")
      .filter((u) => !q || u.titel.toLowerCase().includes(q) || (u.letzte?.text || "").toLowerCase().includes(q));
    const anfragen = zustand.anfragen.filter((a) => a.richtung === "rein");
    const teile = [];
    if (anfragen.length && !q) {
      teile.push(h("div.chatzeile", { role: "listitem", onclick: () => anfragenBlatt() },
        h("div.avatar", { style: { "--farbe": "var(--akzent)" } }, ic("inbox", "ic-20")),
        h("div.mitte", h("div.oben", h("span.name", { text: "Anfragen" })),
          h("div.unten", h("span.vorschau", { text: anfragen.map((a) => a.name).join(", ") }), h("span.zaehler", { text: String(anfragen.length) })))));
    }
    const angeheftet = reihen.filter((u) => u.angeheftet);
    const rest = reihen.filter((u) => !u.angeheftet);
    if (angeheftet.length) teile.push(h("div.leiste-abschnitt.weg-wenn-schmal", { text: "Angeheftet" }), ...angeheftet.map((u) => this.chatZeile(u)));
    if (angeheftet.length && rest.length) teile.push(h("div.leiste-abschnitt.weg-wenn-schmal", { text: "Alle Chats" }));
    teile.push(...rest.map((u) => this.chatZeile(u)));
    if (!reihen.length && !anfragen.length) {
      teile.push(h("div.leer", { style: { height: "auto", "padding-top": "48px" } },
        h("div.kreis", ic(q ? "search" : "message-circle", "ic-20")),
        h("h3", { text: q ? "Nichts gefunden" : "Noch keine Chats" }),
        q ? null : h("p", { text: "Füge einen Freund über seine ID hinzu – oder tritt mit einem Code einer Gruppe bei." }),
        q ? null : h("button.knopf.primaer", { type: "button", onclick: () => freundHinzufuegen() }, ic("user-plus"), "Freund hinzufügen")));
    }
    ersetzen(this.listeEl, teile);
  }

  chatZeile(u) {
    const l = u.letzte || {};
    const vorschau = l.text ? (l.ich ? `Du: ${l.text}` : (u.art === "gruppe" && l.von_name ? `${l.von_name}: ${l.text}` : l.text)) : (u.art === "gruppe" ? "Gruppe" : "");
    return h("div.chatzeile", {
      role: "listitem", tabindex: "0",
      "aria-selected": String(zustand.seite === "chat" && zustand.aktiv === u.id),
      onclick: () => this.oeffnen(u.id),
      onkeydown: (e) => e.key === "Enter" && this.oeffnen(u.id),
      oncontextmenu: (e) => { e.preventDefault(); this.zeilenMenue(e, u); },
    },
      avatar(u.titel, u.farbe, "", u.art === "dm" && u.online),
      h("div.mitte",
        h("div.oben", h("span.name", { text: u.titel }), h("span.zeit", { text: zeitKurz(l.ts) })),
        h("div.unten",
          u.schluessel_geaendert ? ic("shield-alert", "ic-14") : null,
          h("span.vorschau", { text: vorschau }),
          u.stumm ? h("span.stumm", ic("bell-off", "ic-14")) : null,
          u.ungelesen ? h("span.zaehler" + (u.stumm ? ".grau" : ""), { text: u.ungelesen > 99 ? "99+" : String(u.ungelesen) }) : null)));
  }

  zeilenMenue(e, u) {
    import("../blaetter.js").then(({ menue }) => menue(e.clientX, e.clientY, [
      { text: u.angeheftet ? "Loslösen" : "Anheften", symbol: "pin", aktion: () => rufe("unterhaltung_setzen", u.id, { angeheftet: !u.angeheftet }) },
      { text: u.stumm ? "Ton an" : "Stummschalten", symbol: "bell-off", aktion: () => rufe("unterhaltung_setzen", u.id, { stumm: !u.stumm }) },
      { text: "Als gelesen markieren", symbol: "check-check", aktion: () => rufe("gelesen", u.id) },
      "-",
      { text: u.art === "dm" ? "Kontakt entfernen …" : "Gruppe verlassen …", symbol: "trash-2", gefahr: true, aktion: () => this.entfernen(u) },
    ]));
  }

  async entfernen(u) {
    const { bestaetigen } = await import("../blaetter.js");
    const frage = u.art === "dm"
      ? ["Kontakt entfernen?", `Der Chatverlauf mit ${u.titel} wird auf diesem PC gelöscht.`]
      : ["Gruppe verlassen?", "Du bekommst keine Nachrichten mehr. Mit dem Code kannst du später wieder beitreten."];
    if (!(await bestaetigen(frage[0], frage[1], { ja: "Entfernen", gefahr: true, symbol: "trash-2" }))) return;
    const r = await rufe("unterhaltung_entfernen", u.id);
    if (!r.ok) toast(r.fehler, "fehler");
  }

  communityListe(q) {
    const offen = zustand.communities.find((c) => c.id === zustand.communityOffen);
    if (offen) {
      const kanaele = offen.kanaele.filter((k) => !q || k.name.includes(q));
      ersetzen(this.listeEl,
        h("div.community-kopf",
          rundknopf("chevron-left", "Zurück", () => { setzen({ communityOffen: null }); this.liste(); }, "klein"),
          h("div.avatar.mittel.community", { style: { "--farbe": offen.farbe || farbeFuer(offen.id) } }, offen.icon || offen.name.slice(0, 1)),
          h("span.name.weg-wenn-schmal", { text: offen.name }),
          rundknopf("ellipsis", "Community", (e) => communityMenue(e.currentTarget, offen), "klein")),
        h("div.leiste-abschnitt.weg-wenn-schmal", { style: { display: "flex", "align-items": "center" } },
          h("span", { style: { flex: "1" }, text: "Kanäle" }),
          offen.admin ? h("button.rund.klein", { type: "button", title: "Kanal anlegen", "aria-label": "Kanal anlegen", onclick: () => kanalAnlegen(offen) }, ic("plus", "ic-14")) : null),
        kanaele.map((k) => {
          const uid = k.unterhaltung;
          return h("div.chatzeile.kanal" + (k.ungelesen ? ".ungelesen" : ""), {
            role: "listitem", tabindex: "0",
            "aria-selected": String(zustand.seite === "chat" && zustand.aktiv === uid),
            onclick: () => this.oeffnen(uid),
          }, ic(k.nur_admins ? "megaphone" : "hash", "ic-16"), h("span.name", { text: k.name }),
            k.ungelesen ? h("span.zaehler", { text: String(k.ungelesen) }) : null);
        }),
        h("div", { style: { padding: "14px 6px" } },
          h("button.knopf.breit.weg-wenn-schmal", { type: "button", onclick: () => einladungZeigen(offen) }, ic("user-plus"), "Leute einladen")));
      return;
    }
    const liste = zustand.communities.filter((c) => !c.gruppe && (!q || c.name.toLowerCase().includes(q)));
    if (!liste.length) {
      ersetzen(this.listeEl, h("div.leer", { style: { height: "auto", "padding-top": "48px" } },
        h("div.kreis", ic("hash", "ic-20")),
        h("h3", { text: q ? "Nichts gefunden" : "Noch keine Community" }),
        q ? null : h("p", { text: "Eine Community ist wie ein eigener Discord-Server: mehrere Kanäle, alle mit Code dabei." }),
        q ? null : h("div", { style: { display: "flex", gap: "8px", "justify-content": "center" } },
          h("button.knopf.primaer", { type: "button", onclick: () => communityErstellen() }, "Gründen"),
          h("button.knopf", { type: "button", onclick: () => communityBeitreten() }, "Beitreten"))));
      return;
    }
    ersetzen(this.listeEl, liste.map((c) => {
      const ungelesen = c.kanaele.reduce((s, k) => s + (k.ungelesen || 0), 0);
      return h("div.chatzeile", { role: "listitem", tabindex: "0", onclick: () => { setzen({ communityOffen: c.id }); this.liste(); const erster = c.kanaele[0]; if (erster) this.oeffnen(erster.unterhaltung); } },
        h("div.avatar.community", { style: { "--farbe": c.farbe || farbeFuer(c.id) } }, c.icon || c.name.slice(0, 1)),
        h("div.mitte",
          h("div.oben", h("span.name", { text: c.name }), h("span.zeit", { text: zeitKurz(c.letzte_ts) })),
          h("div.unten", h("span.vorschau", { text: `${c.kanaele.length} ${c.kanaele.length === 1 ? "Kanal" : "Kanäle"} · ${c.mitglieder || 1} aktiv` }),
            ungelesen ? h("span.zaehler", { text: String(ungelesen) }) : null)));
    }));
  }

  werkzeugListe() {
    ersetzen(this.listeEl, WERKZEUGE.map((w) => h("div.werkzeugzeile", {
      role: "listitem", tabindex: "0",
      "aria-selected": String(zustand.seite === "werkzeug" && zustand.werkzeug === w.id),
      onclick: () => this.werkzeugOeffnen(w.id),
    }, h("span.symbol", { style: { background: w.farbe } }, ic(w.symbol, "ic-16")), h("span.weg-wenn-schmal", { text: w.titel }))));
  }
}
