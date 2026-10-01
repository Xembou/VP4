// =====================================================================
//  seitenleiste.js - die schwebende Glasleiste links
// =====================================================================
//  Tastatur: Die Liste ist eine Auswahlliste (role=listbox). Nur die
//  gewählte Zeile ist per Tab erreichbar; ↑/↓, Pos1/Ende wandern,
//  Enter/Leertaste öffnet, Esc im Suchfeld leert die Suche.
// =====================================================================

import { h, ic, rundknopf, ersetzen, leeren, avatar, zeitKurz, farbeFuer } from "../dom.js";
import { rufe } from "../bruecke.js";
import { menue, menueAn, toast, bestaetigen } from "../blaetter.js";
import { zustand, setzen } from "../zustand.js";
import { freundHinzufuegen, anfragenBlatt, kontaktBlockieren } from "./kontakte.js";
import { communityErstellen, communityBeitreten, einladungZeigen, kanalAnlegen, communityMenue, gruppeErstellen, kanalMenue, rolle } from "./gemeinschaft.js";
import { WERKZEUGE } from "./werkzeuge.js";

const BEREICHE = [["chats", "Chats", "message-circle"], ["communities", "Communities", "hash"], ["werkzeuge", "Werkzeuge", "wrench"]];

/** "4 Kanäle · 23 aktiv" - nur was Python wirklich geliefert hat. */
export function communityUntertitel(c) {
  const teile = [];
  const n = c.kanaele?.length || 0;
  if (n) teile.push(`${n} ${n === 1 ? "Kanal" : "Kanäle"}`);
  if (Number(c.mitglieder) > 0) teile.push(`${c.mitglieder} aktiv`);
  if (!teile.length) return c.geladen === false ? "Wartet auf die Kanäle …" : "Community";
  return teile.join(" · ");
}

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
    this.plus.setAttribute("aria-haspopup", "menu");
    this.sucheFeld = h("input", { type: "search", placeholder: "Suchen", "aria-label": "Liste durchsuchen", value: zustand.suche, autocomplete: "off" });
    this.sucheFeld.addEventListener("input", () => { setzen({ suche: this.sucheFeld.value }); this.liste(); });
    this.sucheFeld.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.sucheFeld.value) { e.preventDefault(); e.stopPropagation(); this.sucheFeld.value = ""; setzen({ suche: "" }); this.liste(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); this.zeilen()[0]?.focus(); }
      else if (e.key === "Enter") { const erste = this.zeilen()[0]; if (erste) { e.preventDefault(); erste.click(); } }
    });
    this.segment = h("div.segment", { role: "tablist", "aria-label": "Bereich" });
    this.pille = h("span.pille", { "aria-hidden": "true" });
    this.segment.append(this.pille, ...BEREICHE.map(([id, text, symbol]) =>
      h("button", { type: "button", role: "tab", title: text, "aria-label": text, "aria-controls": "leiste-liste", dataset: { bereich: id }, onclick: () => this.bereich(id) },
        h("span.nur-schmal", ic(symbol, "ic-16")), h("span.weg-wenn-schmal", { text }))));
    // Pfeiltasten zwischen den Reitern, wie bei einem echten Segment
    this.segment.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      e.preventDefault();
      const i = BEREICHE.findIndex(([id]) => id === zustand.bereich);
      const j = (i + (e.key === "ArrowRight" ? 1 : -1) + BEREICHE.length) % BEREICHE.length;
      this.bereich(BEREICHE[j][0]);
      this.segment.querySelector(`[data-bereich="${BEREICHE[j][0]}"]`)?.focus();
    });
    this.zusatz = h("div.leiste-zusatz");
    this.listeEl = h("div.leiste-liste", { id: "leiste-liste" });
    this.listeEl.addEventListener("keydown", (e) => this.listenTaste(e));
    this.fuss = h("div.leiste-fuss");

    ersetzen(this.el,
      h("div.leiste-kopf",
        h("div.leiste-titelzeile.weg-wenn-schmal", this.titel, this.plus),
        h("label.suche.weg-wenn-schmal", ic("search", "ic-16"), this.sucheFeld, h("kbd", { text: "Strg K", title: "Schnellwahl: Strg + K" })),
        this.segment),
      this.zusatz, this.listeEl, this.fuss);
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
    for (const b of this.segment.querySelectorAll("button")) {
      const an = b.dataset.bereich === zustand.bereich;
      b.setAttribute("aria-selected", String(an));
      b.tabIndex = an ? 0 : -1;
    }
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
    const punkt = h("i.verbindungspunkt" + (an ? ".an" : fehler ? ".fehler" : ""), { "aria-hidden": "true" });
    ersetzen(this.fuss,
      h("button.ich", { type: "button", title: `${verbindungsText} · Klicken kopiert deine ID`, "aria-label": `${p.name || "Ich"}, ${verbindungsText}. ID kopieren`, onclick: async () => { await navigator.clipboard?.writeText(p.id || ""); toast("Deine ID ist kopiert"); } },
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
      ], { label: "Neu" });
      return;
    }
    const anfragen = zustand.anfragen.filter((a) => a.richtung === "rein").length;
    menueAn(anker, [
      { text: "Freund hinzufügen", symbol: "user-plus", tasten: "Strg N", aktion: () => freundHinzufuegen() },
      { text: "Gruppe erstellen", symbol: "users", aktion: () => gruppeErstellen() },
      { text: "Gruppe beitreten", symbol: "link", aktion: () => communityBeitreten() },
      "-",
      { text: anfragen ? `Anfragen (${anfragen})` : "Anfragen", symbol: "inbox", aktion: () => anfragenBlatt() },
    ], { label: "Neu" });
  }

  /* ------------------------------------------------------- Tastatur */
  zeilen() { return [...this.listeEl.querySelectorAll('[role="option"]')]; }

  listenTaste(e) {
    const zeilen = this.zeilen();
    if (!zeilen.length) return;
    const i = zeilen.indexOf(document.activeElement);
    const geh = (j) => {
      e.preventDefault();
      const ziel = zeilen[Math.max(0, Math.min(zeilen.length - 1, j))];
      for (const z of zeilen) z.tabIndex = z === ziel ? 0 : -1;
      ziel.focus();
      ziel.scrollIntoView({ block: "nearest" });
    };
    if (e.key === "ArrowDown") geh(i + 1);
    else if (e.key === "ArrowUp") { if (i <= 0 && zustand.bereich !== "werkzeuge") { e.preventDefault(); this.sucheFeld.focus(); } else geh(i - 1); }
    else if (e.key === "Home") geh(0);
    else if (e.key === "End") geh(zeilen.length - 1);
    else if ((e.key === "Enter" || e.key === " ") && i >= 0) { e.preventDefault(); zeilen[i].click(); }
    else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      if (i < 0) return;
      e.preventDefault();
      const r = zeilen[i].getBoundingClientRect();
      zeilen[i].dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: r.left + 40, clientY: r.bottom - 6 }));
    }
  }

  /** Eine Zeile der Auswahlliste. Genau eine ist per Tab erreichbar. */
  option(kennung, attribute, ...kinder) {
    return h(kennung, { role: "option", tabindex: "-1", ...attribute }, ...kinder);
  }

  /** Nach dem Zeichnen: die gewählte (oder erste) Zeile bekommt tabindex 0 */
  tabSetzen() {
    const zeilen = this.zeilen();
    const fokusVorher = this.listeEl.contains(document.activeElement) ? document.activeElement?.dataset?.id : null;
    const ziel = (fokusVorher && zeilen.find((z) => z.dataset.id === fokusVorher))
      || zeilen.find((z) => z.getAttribute("aria-selected") === "true") || zeilen[0];
    for (const z of zeilen) z.tabIndex = z === ziel ? 0 : -1;
    if (fokusVorher && ziel && ziel.dataset.id === fokusVorher) ziel.focus({ preventScroll: true });
  }

  listeSetzen(teile, { label, auswahl = true } = {}) {
    if (auswahl) {
      this.listeEl.setAttribute("role", "listbox");
      this.listeEl.setAttribute("aria-label", label || "Liste");
    } else {
      this.listeEl.removeAttribute("role");
      this.listeEl.removeAttribute("aria-label");
    }
    ersetzen(this.listeEl, teile);
    this.tabSetzen();
  }

  abschnitt(titel, zeilen) {
    return h("div", { role: "group", "aria-label": titel },
      h("div.leiste-abschnitt.weg-wenn-schmal", { "aria-hidden": "true", text: titel }), zeilen);
  }

  /* ------------------------------------------------------------- Listen */
  liste() {
    const q = zustand.suche.trim().toLowerCase();
    if (zustand.bereich !== "communities") leeren(this.zusatz);
    if (zustand.bereich === "werkzeuge") return this.werkzeugListe(q);
    if (zustand.bereich === "communities") return this.communityListe(q);

    const reihen = zustand.unterhaltungen
      .filter((u) => u.art !== "kanal")
      .filter((u) => !q || u.titel.toLowerCase().includes(q) || (u.letzte?.text || "").toLowerCase().includes(q));
    const anfragen = zustand.anfragen.filter((a) => a.richtung === "rein");
    const teile = [];
    if (anfragen.length && !q) {
      teile.push(this.option("div.chatzeile.ungelesen", { dataset: { id: "anfragen" }, "aria-label": `Anfragen: ${anfragen.length}`, onclick: () => anfragenBlatt() },
        h("div.avatar", { style: { "--farbe": "var(--akzent)" } }, ic("inbox", "ic-20")),
        h("div.mitte", h("div.oben", h("span.name", { text: "Anfragen" })),
          h("div.unten", h("span.vorschau", { text: anfragen.map((a) => a.name).join(", ") }), h("span.zaehler", { text: String(anfragen.length) })))));
    }
    const angeheftet = reihen.filter((u) => u.angeheftet);
    const rest = reihen.filter((u) => !u.angeheftet);
    if (angeheftet.length) teile.push(this.abschnitt("Angeheftet", angeheftet.map((u) => this.chatZeile(u))));
    if (angeheftet.length && rest.length) teile.push(this.abschnitt("Alle Chats", rest.map((u) => this.chatZeile(u))));
    else teile.push(...rest.map((u) => this.chatZeile(u)));
    if (!reihen.length && !anfragen.length) {
      this.listeSetzen(h("div.leer", { style: { height: "auto", "padding-top": "48px" } },
        h("div.kreis", ic(q ? "search" : "message-circle", "ic-20")),
        h("h3", { text: q ? "Nichts gefunden" : "Noch keine Chats" }),
        q ? h("p", { text: `Kein Chat passt zu „${zustand.suche.trim()}“.` }) : h("p", { text: "Füge einen Freund über seine ID hinzu – oder tritt mit einem Code einer Gruppe bei." }),
        q ? null : h("button.knopf.primaer", { type: "button", onclick: () => freundHinzufuegen() }, ic("user-plus"), "Freund hinzufügen")), { auswahl: false });
      return;
    }
    this.listeSetzen(teile, { label: "Chats" });
  }

  chatZeile(u) {
    const l = u.letzte || {};
    const vorschau = l.text ? (l.ich ? `Du: ${l.text}` : (u.art === "gruppe" && l.von_name ? `${l.von_name}: ${l.text}` : l.text)) : (u.art === "gruppe" ? "Gruppe" : "");
    const ungelesen = u.ungelesen > 0;
    const vorlesen = [u.titel, ungelesen ? `${u.ungelesen} ungelesen` : "", u.stumm ? "stumm" : "", u.schluessel_geaendert ? "Schlüssel geändert" : "", vorschau, zeitKurz(l.ts)].filter(Boolean).join(", ");
    return this.option("div.chatzeile" + (ungelesen && !u.stumm ? ".ungelesen" : ""), {
      dataset: { id: u.id },
      "aria-label": vorlesen,
      "aria-selected": String(zustand.seite === "chat" && zustand.aktiv === u.id),
      onclick: () => this.oeffnen(u.id),
      oncontextmenu: (e) => { e.preventDefault(); this.zeilenMenue(e, u); },
    },
      avatar(u.titel, u.farbe, "", u.art === "dm" && u.online),
      h("div.mitte",
        h("div.oben", h("span.name", { text: u.titel }), h("span.zeit", { text: zeitKurz(l.ts) })),
        h("div.unten",
          u.schluessel_geaendert ? h("span.warnsymbol", { title: "Schlüssel geändert" }, ic("shield-alert", "ic-14")) : null,
          h("span.vorschau", { text: vorschau }),
          u.stumm ? h("span.stumm", { title: "Stumm" }, ic("bell-off", "ic-14")) : null,
          ungelesen ? h("span.zaehler" + (u.stumm ? ".grau" : ""), { text: u.ungelesen > 99 ? "99+" : String(u.ungelesen) }) : null)));
  }

  zeilenMenue(e, u) {
    menue(e.clientX, e.clientY, [
      { text: u.angeheftet ? "Loslösen" : "Anheften", symbol: "pin", aktion: () => rufe("unterhaltung_setzen", u.id, { angeheftet: !u.angeheftet }) },
      { text: u.stumm ? "Ton an" : "Stummschalten", symbol: "bell-off", aktion: () => rufe("unterhaltung_setzen", u.id, { stumm: !u.stumm }) },
      { text: "Als gelesen markieren", symbol: "check-check", aktion: () => rufe("gelesen", u.id) },
      "-",
      u.art === "dm" ? { text: "Kontakt blockieren …", symbol: "circle-alert", gefahr: true, aktion: () => kontaktBlockieren({ id: u.id, name: u.titel }) } : null,
      { text: u.art === "dm" ? "Kontakt entfernen …" : "Gruppe verlassen …", symbol: "trash-2", gefahr: true, aktion: () => this.entfernen(u) },
    ], { label: `Chat ${u.titel}` });
  }

  async entfernen(u) {
    const frage = u.art === "dm"
      ? ["Kontakt entfernen?", `Der Chatverlauf mit ${u.titel} wird auf diesem PC gelöscht.`]
      : ["Gruppe verlassen?", "Du bekommst keine Nachrichten mehr. Mit dem Code kannst du später wieder beitreten."];
    if (!(await bestaetigen(frage[0], frage[1], { ja: "Entfernen", gefahr: true, symbol: "trash-2" }))) return;
    const r = await rufe("unterhaltung_entfernen", u.id);
    if (!r.ok) toast(r.fehler, "fehler");
  }

  communityKopf(c) {
    const farbe = c.farbe || farbeFuer(c.id);
    return h("div.community-held", { style: { "--farbe": farbe } },
      h("div.held-oben",
        h("button.zurueck", { type: "button", "aria-label": "Zurück zu allen Communities", onclick: () => { setzen({ communityOffen: null }); this.liste(); } },
          ic("chevron-left", "ic-16"), h("span.weg-wenn-schmal", { text: "Communities" })),
        h("span.weg-wenn-schmal", rundknopf("ellipsis", `Optionen für ${c.name}`, (e) => communityMenue(e.currentTarget, c), "klein"))),
      h("div.held-mitte",
        h("div.avatar.community", { style: { "--farbe": farbe }, "aria-hidden": "true" }, c.icon || c.name.slice(0, 1)),
        h("div.held-text.weg-wenn-schmal",
          h("b", { text: c.name }),
          h("small", rolle(c) === "besitzer" ? h("span.marke.besitzer", { text: "Besitzer" }) : rolle(c) === "admin" ? h("span.marke", { text: "Admin" }) : null, h("span.einzeilig", { text: communityUntertitel(c) })))),
      h("div.held-knoepfe.weg-wenn-schmal",
        h("button.knopf.primaer", { type: "button", onclick: () => einladungZeigen(c) }, ic("user-plus", "ic-14"), "Einladen"),
        ["besitzer", "admin"].includes(rolle(c)) ? h("button.knopf", { type: "button", onclick: () => kanalAnlegen(c) }, ic("plus", "ic-14"), "Kanal") : null));
  }

  communityListe(q) {
    const offen = zustand.communities.find((c) => c.id === zustand.communityOffen);
    if (offen) {
      ersetzen(this.zusatz, this.communityKopf(offen));
      const kanaele = offen.kanaele.filter((k) => !q || k.name.toLowerCase().includes(q));
      const zeilen = kanaele.map((k) => {
        const uid = k.unterhaltung;
        return this.option("div.chatzeile.kanal" + (k.ungelesen ? ".ungelesen" : ""), {
          dataset: { id: uid },
          title: `#${k.name}`,
          "aria-label": `${k.nur_admins ? "Ankündigungskanal" : "Kanal"} ${k.name}${k.ungelesen ? `, ${k.ungelesen} ungelesen` : ""}`,
          "aria-selected": String(zustand.seite === "chat" && zustand.aktiv === uid),
          onclick: () => this.oeffnen(uid),
          oncontextmenu: (e) => { e.preventDefault(); kanalMenue(e.clientX, e.clientY, offen, k); },
        }, ic(k.nur_admins ? "megaphone" : "hash", "ic-16"), h("span.name", { text: k.name }),
          k.ungelesen ? h("span.zaehler", { text: String(k.ungelesen) }) : null);
      });
      const teile = [h("div.kanal-kopf", { role: "presentation" }, h("span.weg-wenn-schmal", { text: "Kanäle", "aria-hidden": "true" })), ...zeilen];
      if (!offen.kanaele.length) {
        teile.push(h("p.leiste-abschnitt.weg-wenn-schmal", { text: "Die Kanäle erscheinen, sobald jemand aus der Community online ist." }));
      }
      this.listeSetzen(teile, { label: `Kanäle von ${offen.name}` });
      return;
    }
    leeren(this.zusatz);
    const liste = zustand.communities.filter((c) => !c.gruppe && (!q || c.name.toLowerCase().includes(q)));
    if (!liste.length) {
      this.listeSetzen(h("div.leer", { style: { height: "auto", "padding-top": "48px" } },
        h("div.kreis", ic("hash", "ic-20")),
        h("h3", { text: q ? "Nichts gefunden" : "Noch keine Community" }),
        q ? null : h("p", { text: "Eine Community ist wie ein eigener Discord-Server: mehrere Kanäle, alle mit Code dabei." }),
        q ? null : h("div.knoepfe", { style: { display: "flex", gap: "8px", "justify-content": "center" } },
          h("button.knopf.primaer", { type: "button", onclick: () => communityErstellen() }, "Gründen"),
          h("button.knopf", { type: "button", onclick: () => communityBeitreten() }, "Beitreten"))), { auswahl: false });
      return;
    }
    this.listeSetzen(liste.map((c) => {
      const ungelesen = c.kanaele.reduce((s, k) => s + (k.ungelesen || 0), 0);
      const unter = communityUntertitel(c);
      return this.option("div.chatzeile" + (ungelesen ? ".ungelesen" : ""), {
        dataset: { id: c.id },
        "aria-label": `${c.name}, ${unter}${ungelesen ? `, ${ungelesen} ungelesen` : ""}`,
        "aria-selected": "false",
        onclick: () => { setzen({ communityOffen: c.id }); this.liste(); const erster = c.kanaele[0]; if (erster) this.oeffnen(erster.unterhaltung); },
      },
        h("div.avatar.community", { style: { "--farbe": c.farbe || farbeFuer(c.id) } }, c.icon || c.name.slice(0, 1)),
        h("div.mitte",
          h("div.oben", h("span.name", { text: c.name }), h("span.zeit", { text: zeitKurz(c.letzte_ts) })),
          h("div.unten", h("span.vorschau", { text: unter }),
            ungelesen ? h("span.zaehler", { text: String(ungelesen) }) : null)));
    }), { label: "Communities" });
  }

  werkzeugListe(q = "") {
    const liste = WERKZEUGE.filter((w) => !q || w.titel.toLowerCase().includes(q) || w.info.toLowerCase().includes(q));
    this.listeSetzen(liste.map((w) => this.option("div.werkzeugzeile", {
      dataset: { id: w.id },
      title: w.titel,
      "aria-label": w.titel,
      "aria-selected": String(zustand.seite === "werkzeug" && zustand.werkzeug === w.id),
      onclick: () => this.werkzeugOeffnen(w.id),
    }, h("span.symbol", { style: { background: w.farbe } }, ic(w.symbol, "ic-16")), h("span.weg-wenn-schmal", { text: w.titel }))), { label: "Werkzeuge" });
  }
}
