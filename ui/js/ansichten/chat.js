// =====================================================================
//  chat.js - eine offene Unterhaltung: Verlauf, Leiste, Eingabezeile
// =====================================================================
//  Der Verlauf wird NICHT bei jeder Nachricht neu gezeichnet (das war die
//  bekannte Schwäche von 4.x). Neue Nachrichten werden angehängt, und nur
//  die Nachbarzeile bekommt ihre Lauf-Klassen neu; Änderungen (Reaktion,
//  Status, Bearbeiten) tauschen genau eine Zeile aus.
// =====================================================================

import { h, ic, rundknopf, knopf, ersetzen, leeren, avatar, textMitLinks, uhrzeit,
         tagTitel, gleicherTag, groesse, dauer, farbeFuer, ring } from "../dom.js";
import { rufe, auf } from "../bruecke.js";
import { blatt, menue, menueAn, menueSchliessen, toast, bestaetigen } from "../blaetter.js";
import { scrollArt } from "../zustand.js";
import { emojiWahl } from "./emoji.js";
import { sicherheitBlatt } from "./kontakte.js";

const SCHNELL = ["❤️", "👍", "😂", "😮", "😢", "🔥"];
const LAUF_PAUSE = 5 * 60 * 1000;       // neuer Lauf nach 5 Minuten Pause
const ZEIT_PAUSE = 15 * 60 * 1000;      // Uhrzeit über der Nachricht nach 15 Minuten

let aktuell = null;                      // die eine offene Ansicht

export function chatSchliessen() {
  aktuell?.abbauen();
  aktuell = null;
}

export function chatAktuell() { return aktuell; }

export function chatOeffnen(haupt, unterhaltung) {
  chatSchliessen();
  aktuell = new ChatAnsicht(haupt, unterhaltung);
  return aktuell;
}

const nurEmoji = (t) => {
  const s = (t || "").trim();
  if (!s || s.length > 24) return false;
  const teile = [...new Intl.Segmenter("de", { granularity: "grapheme" }).segment(s)].map((x) => x.segment).filter((x) => x.trim());
  return teile.length <= 3 && teile.every((x) => /\p{Extended_Pictographic}/u.test(x));
};

class ChatAnsicht {
  constructor(haupt, u) {
    this.haupt = haupt;
    this.u = u;
    this.nachrichten = [];
    this.zeilen = new Map();
    this.mehr = false;
    this.bezug = null;            // {art: "antwort"|"bearbeiten", n}
    this.abmelden = [];
    this.zuletztGetippt = 0;
    this.tippZeit = null;
    // Wie viel ungelesen war, als der Chat aufging - dafür steht über der
    // ersten davon "Neue Nachrichten". main.js setzt den Zähler gleich auf 0.
    this.ungelesenStart = Math.max(0, Number(u.ungelesen) || 0);
    this.neuSeitUnten = 0;
    this.bauen();
    this.laden();
    // Den Kopf aktualisiert main.js nach jedem Nachladen der Liste
    // (kontakt_geaendert trägt nur die ID, nicht die Unterhaltung).
    this.abmelden.push(
      auf("nachricht_neu", (e) => e.unterhaltung === this.u.id && this.neu(e.nachricht)),
      auf("nachricht_geaendert", (e) => e.unterhaltung === this.u.id && this.geaendert(e.nachricht)),
      auf("tippt", (e) => e.unterhaltung === this.u.id && this.tipptAnzeigen(e.name)),
    );
    // Esc schliesst den Infobereich (Blätter und Menüs fangen Esc vorher ab)
    const esc = (e) => { if (e.key === "Escape" && this.info && !e.defaultPrevented) { e.preventDefault(); this.infoUmschalten(); } };
    document.addEventListener("keydown", esc);
    this.abmelden.push(() => document.removeEventListener("keydown", esc));
  }

  abbauen() {
    for (const ab of this.abmelden) ab();
    this.aufnahmeAbbrechen?.();
    leeren(this.haupt);
  }

  /* ------------------------------------------------------------ Aufbau */
  bauen() {
    const u = this.u;
    this.verlauf = h("div.verlauf.ruhig", { onscroll: () => this.gescrollt(), role: "log", "aria-label": `Verlauf mit ${u.titel}`, tabindex: "0" });
    this.innen = h("div.verlauf-innen");
    this.klebtUnten = true;
    // Bilder laden nach dem ersten Zeichnen und schieben alles nach oben -
    // wer unten war, soll unten bleiben.
    this.innen.addEventListener("load", () => { if (this.klebtUnten) this.verlauf.scrollTop = this.verlauf.scrollHeight; }, true);
    this.verlauf.append(this.innen);

    this.leiste = h("header.chat-leiste.glas");
    this.kopfNeu(u);

    this.eingabeBauen();
    this.chat = h("section.chat", { "aria-label": `Chat mit ${u.titel}` },
      this.verlauf, this.leiste, this.eingabe);
    ersetzen(this.haupt, this.chat);

    // Dateien ablegen
    let tiefe = 0;
    this.chat.addEventListener("dragenter", (e) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      e.preventDefault();
      if (tiefe++ === 0 && this.darfSchreiben()) {
        this.ablage = h("div.ablegen.glas", ic("upload", "ic-20"), "Zum Senden loslassen");
        this.chat.append(this.ablage);
      }
    });
    this.chat.addEventListener("dragover", (e) => e.preventDefault());
    this.chat.addEventListener("dragleave", () => { if (--tiefe <= 0) { tiefe = 0; this.ablage?.remove(); } });
    this.chat.addEventListener("drop", (e) => {
      e.preventDefault();
      tiefe = 0;
      this.ablage?.remove();
      if (this.darfSchreiben()) this.dateienSenden([...e.dataTransfer.files]);
    });
  }

  darfSchreiben() { return this.u.darf_schreiben !== false; }

  kopfNeu(u = this.u) {
    this.u = { ...this.u, ...u };
    u = this.u;
    let unter;
    if (u.art === "kanal") {
      unter = [ic("hash", "ic-14"), u.community_name || "Community"];
    } else if (u.art === "gruppe") {
      unter = [ic("users", "ic-14"), u.untertitel || "Gruppe"];
    } else if (u.online) {
      unter = [h("span.online", { text: "online" }), u.weg === "lan" ? " · über WLAN" : u.weg === "discord" ? " · über Discord" : ""];
    } else {
      unter = [u.zuletzt_aktiv ? `zuletzt aktiv ${u.zuletzt_aktiv}` : (u.weg === "discord" ? "über Discord" : "offline")];
    }

    let schutz;
    if (u.schluessel_geaendert) {
      schutz = h("button.schutz.warnung", { type: "button", onclick: () => sicherheitBlatt(u.id) }, ic("shield-alert", "ic-16"), "Schlüssel geändert");
    } else if (u.art === "dm" && u.verifiziert) {
      schutz = h("button.schutz.verifiziert", { type: "button", onclick: () => sicherheitBlatt(u.id), title: "Ende-zu-Ende verschlüsselt, Sicherheitsnummer verglichen" }, ic("shield-check", "ic-16"), "Verifiziert");
    } else {
      schutz = h("button.schutz", { type: "button", onclick: () => u.art === "dm" ? sicherheitBlatt(u.id) : this.gruppenSchutz(), title: "Ende-zu-Ende verschlüsselt" }, ic("lock", "ic-14"), "Verschlüsselt");
    }

    ersetzen(this.leiste,
      h("button.wer", { type: "button", onclick: () => this.infoUmschalten(), title: "Infos", "aria-label": `${u.art === "kanal" ? "#" + (u.kanal_name || u.titel) : u.titel} – Infos zeigen`, "aria-expanded": String(!!this.info) },
        u.art === "kanal" ? h("div.avatar.mittel.community", { style: { "--farbe": u.farbe || farbeFuer(u.id) } }, ic("hash", "ic-16"))
          : avatar(u.titel, u.farbe, "mittel", u.art === "dm" && u.online),
        h("div", { style: { "min-width": "0" } }, h("b", { text: u.art === "kanal" ? u.kanal_name || u.titel : u.titel }), h("small", unter))),
      schutz,
      rundknopf("search", "Im Chat suchen (Strg F)", () => this.suchen()),
      rundknopf("info", "Infos", () => this.infoUmschalten()),
    );
  }

  gruppenSchutz() {
    blatt((zu) => [
      h("div.kopfsymbol", ic("lock", "ic-20")),
      h("h2", { text: "Wie diese Unterhaltung geschützt ist" }),
      h("p", { text: "Nachrichten hier sind mit dem Schlüssel aus dem Einladungscode verschlüsselt. Discord sieht nur Geheimtext. Jede Nachricht ist zusätzlich vom Absender signiert, ein Name lässt sich also nicht fälschen." }),
      h("div.banner.warnung", ic("triangle-alert"), h("div",
        h("b", { text: "Was das nicht leistet: " }),
        "Wer den Code hat, liest alles mit – auch ältere Nachrichten, die noch im Kanal stehen. Ein weitergegebener Code lässt sich nicht zurückholen. Wer wann schreibt, sieht man im Kanal trotzdem.")),
      h("div.aktionen", knopf("Verstanden", () => zu(), "primaer")),
    ], { breite: 480 });
  }

  /* ------------------------------------------------------------ Laden */
  async laden(vorTs = null, vorId = null) {
    // Beim Nachladen auch die ID der ältesten geladenen Nachricht: sonst
    // fallen Nachrichten mit genau derselben Zeit an der Seitengrenze weg.
    const r = vorTs === null ? await rufe("nachrichten", this.u.id) : await rufe("nachrichten", this.u.id, vorTs, vorId);
    if (!r.ok) { toast(r.fehler, "fehler"); return; }
    this.mehr = !!r.mehr;
    const liste = r.liste || [];
    if (vorTs === null) {
      this.nachrichten = liste;
      this.allesZeichnen();
      this.verlauf.scrollTop = this.verlauf.scrollHeight;
      this.klebtUnten = true;
      // Mit "Neue Nachrichten": dorthin, wenn alles Neue nicht auf einmal passt
      if (this.neuTrenner) {
        const oben = this.neuTrenner.offsetTop - 84;
        if (oben < this.verlauf.scrollTop) { this.verlauf.scrollTop = Math.max(0, oben); this.klebtUnten = false; }
      }
      requestAnimationFrame(() => { this.verlauf.classList.remove("ruhig"); this.gescrollt(); });
      rufe("gelesen", this.u.id);
    } else if (liste.length) {
      const hoehe = this.verlauf.scrollHeight;
      this.nachrichten = [...liste, ...this.nachrichten];
      this.allesZeichnen();
      this.verlauf.scrollTop = this.verlauf.scrollHeight - hoehe;
    }
  }

  gescrollt() {
    const v = this.verlauf;
    if (v.scrollTop < 120 && this.mehr && !this.laedt) {
      this.laedt = true;
      this.laden(this.nachrichten[0]?.ts, this.nachrichten[0]?.id).finally(() => { this.laedt = false; });
    }
    const unten = v.scrollHeight - v.scrollTop - v.clientHeight < 140;
    this.klebtUnten = unten;
    if (unten) this.neuSeitUnten = 0;
    this.knopfUntenSetzen(!unten);
  }

  /** Der runde Knopf "nach unten" - mit Zähler, wenn seitdem Neues kam */
  knopfUntenSetzen(zeigen) {
    if (zeigen && !this.knopfUnten) {
      this.knopfUnten = h("button.nach-unten.glas.glas-stark", { type: "button", "aria-label": "Zur neuesten Nachricht", title: "Zur neuesten Nachricht", onclick: () => this.nachUnten(true) }, ic("arrow-down"));
      this.chat.append(this.knopfUnten);
    } else if (!zeigen && this.knopfUnten) {
      const k = this.knopfUnten;
      this.knopfUnten = null;
      k.classList.add("weg");
      setTimeout(() => k.remove(), 200);
    }
    if (this.knopfUnten) {
      this.knopfUnten.querySelector(".zaehler")?.remove();
      if (this.neuSeitUnten > 0) {
        this.knopfUnten.append(h("span.zaehler", { text: this.neuSeitUnten > 99 ? "99+" : String(this.neuSeitUnten) }));
        this.knopfUnten.setAttribute("aria-label", `Zur neuesten Nachricht, ${this.neuSeitUnten} neu`);
      }
    }
  }

  nachUnten(weich) {
    this.klebtUnten = true;
    this.neuSeitUnten = 0;
    requestAnimationFrame(() => this.verlauf.scrollTo({ top: this.verlauf.scrollHeight, behavior: weich ? scrollArt() : "auto" }));
  }

  istUnten() {
    const v = this.verlauf;
    return v.scrollHeight - v.scrollTop - v.clientHeight < 160;
  }

  /* ---------------------------------------------------------- Zeichnen */
  allesZeichnen() {
    leeren(this.innen);
    this.zeilen.clear();
    if (!this.nachrichten.length) {
      this.innen.append(h("div.leer",
        h("div.kreis", ic(this.u.art === "kanal" ? "hash" : "message-circle", "ic-20")),
        h("h3", { text: this.u.art === "kanal" ? `Willkommen in #${this.u.kanal_name || this.u.titel}` : "Noch keine Nachrichten" }),
        h("p", { text: "Alles, was ihr hier schreibt, ist Ende-zu-Ende verschlüsselt. Discord bekommt nur Geheimtext zu sehen." })));
      return;
    }
    // Wo fängt das Ungelesene an? Die letzten N Nachrichten der anderen.
    this.neuAb = null;
    this.neuTrenner = null;
    if (this.ungelesenStart > 0) {
      let rest = this.ungelesenStart;
      for (let i = this.nachrichten.length - 1; i >= 0; i--) {
        const n = this.nachrichten[i];
        if (n.ich || n.art === "system") continue;
        this.neuAb = n.id;
        if (--rest === 0) break;
      }
    }
    if (this.mehr) this.innen.append(h("div.mehr-laden", { "aria-hidden": "true" }, ic("arrow-up", "ic-14"), "Nach oben scrollen für Älteres"));
    this.nachrichten.forEach((n, i) => this.zeileEinfuegen(n, i));
    this.statusZeileSetzen();
  }

  /** "Heute 14:02" - der Tag fett, die Uhrzeit normal, wie in Nachrichten */
  trenner(ts, pause = false) {
    return h("div.tag-trenner" + (pause ? ".pause" : ""), { role: "separator", "aria-label": `${tagTitel(ts)}, ${uhrzeit(ts)}` },
      pause ? null : h("b", { text: tagTitel(ts) }), pause ? uhrzeit(ts) : ` ${uhrzeit(ts)}`);
  }

  zeileEinfuegen(n, i) {
    const vorher = this.nachrichten[i - 1];
    if (!vorher || !gleicherTag(vorher.ts, n.ts)) {
      this.innen.append(this.trenner(n.ts));
    } else if (n.ts - vorher.ts > ZEIT_PAUSE) {
      this.innen.append(this.trenner(n.ts, true));
    }
    if (n.id === this.neuAb && !this.neuTrenner) {
      this.neuTrenner = h("div.neu-trenner", { role: "separator", text: this.ungelesenStart === 1 ? "Neue Nachricht" : "Neue Nachrichten" });
      this.innen.append(this.neuTrenner);
    }
    const zeile = this.zeileBauen(n);
    this.zeilen.set(n.id, zeile);
    this.innen.append(zeile);
    this.laufKlassen(i);
    if (i > 0) this.laufKlassen(i - 1);
  }

  gehoertZuLauf(a, b) {
    // Der "Neue Nachrichten"-Trenner unterbricht einen Lauf
    return a && b && a.art !== "system" && b.art !== "system" && a.von === b.von
      && b.ts - a.ts < LAUF_PAUSE && gleicherTag(a.ts, b.ts) && b.id !== this.neuAb;
  }

  laufKlassen(i) {
    const n = this.nachrichten[i];
    const zeile = n && this.zeilen.get(n.id);
    if (!zeile || n.art === "system") return;
    const weiter = this.gehoertZuLauf(this.nachrichten[i - 1], n);
    const folgt = this.gehoertZuLauf(n, this.nachrichten[i + 1]);
    zeile.classList.toggle("lauf-weiter", weiter);
    zeile.classList.toggle("lauf-folgt", folgt);
    zeile.classList.toggle("neuer-lauf", !weiter);
    const ohneBlase = n.art === "bild" || n.art === "video" || n.art === "datei" || (n.art === "text" && nurEmoji(n.text) && !n.geloescht);
    zeile.classList.toggle("schwanz", !folgt && !ohneBlase && !n.reaktionen?.length);
    // Name und Bild in Gruppen nur einmal pro Lauf
    const name = zeile.querySelector(".absender");
    if (name) name.classList.toggle("versteckt", weiter);
    const platz = zeile.querySelector(".avatar-platz");
    if (platz) {
      leeren(platz);
      if (!folgt) platz.append(avatar(n.von_name, n.von_farbe, "klein"));
    }
  }

  zeileBauen(n) {
    if (n.art === "system") {
      return h("div.system-zeile", { dataset: { id: n.id } }, ic("info", "ic-14"), n.text);
    }
    const ich = !!n.ich;
    const gruppe = this.u.art !== "dm";
    const zeile = h("div.zeile" + (ich ? ".ich" : ""), { dataset: { id: n.id } });
    if (gruppe && !ich) zeile.append(h("div.avatar-platz"));

    const spalte = h("div.spalte");
    if (gruppe && !ich) spalte.append(h("div.absender", { text: n.von_name }));
    spalte.append(this.inhaltBauen(n));

    if (n.reaktionen?.length) {
      spalte.append(h("div.reaktionen", n.reaktionen.map((r) =>
        h("button.reaktion" + (r.meine ? ".meine" : ""), {
          type: "button", title: r.namen?.join(", ") || "",
          "aria-label": `${r.emoji} ${r.anzahl}${r.namen?.length ? ": " + r.namen.join(", ") : ""}`, "aria-pressed": String(!!r.meine),
          onclick: () => this.reagieren(n, r.emoji),
        }, h("span.emoji", { "aria-hidden": "true", text: r.emoji }), r.anzahl > 1 ? h("span", { "aria-hidden": "true", text: String(r.anzahl) }) : null))));
    }
    zeile.append(spalte);
    zeile.append(h("span.uhr", { "aria-hidden": "true", text: uhrzeit(n.ts) }));

    if (!n.geloescht) {
      spalte.append(h("div.aktionspille.glas.glas-stark", { role: "toolbar", "aria-label": "Aktionen" },
        rundknopf("smile-plus", "Reagieren", (e) => this.reaktionsMenue(e.currentTarget, n), "klein"),
        this.darfSchreiben() ? rundknopf("reply", "Antworten", () => this.antworten(n), "klein") : null,
        rundknopf("ellipsis", "Mehr", (e) => this.kontextMenue(e.currentTarget.getBoundingClientRect().left, e.currentTarget.getBoundingClientRect().bottom + 4, n), "klein")));
      zeile.addEventListener("contextmenu", (e) => { e.preventDefault(); this.kontextMenue(e.clientX, e.clientY, n); });
      zeile.addEventListener("dblclick", (e) => {
        if (e.target.closest(".medium, .dateikarte, .sprache, a, .reaktion")) return;
        this.reagieren(n, "❤️");
      });
    }
    return zeile;
  }

  inhaltBauen(n) {
    if (n.geloescht) {
      return h("div.blase.geloescht", { text: n.ich ? "Du hast diese Nachricht gelöscht" : "Diese Nachricht wurde gelöscht" });
    }
    const d = n.datei;
    if ((n.art === "bild" || n.art === "video") && d) {
      const teile = [this.mediumBauen(n, d)];
      if (n.text) teile.push(h("div.blase.medium-text", textMitLinks(n.text)));
      return h("div", teile);
    }
    if (n.art === "datei" && d) return this.dateiBauen(n, d);
    if (n.art === "sprache" && d) {
      return h("div.blase", { style: { padding: "0" } }, spracheBauen(n));
    }
    // Text
    const t = n.text || "";
    if (nurEmoji(t)) return h("div.blase.nur-emoji", { text: t.trim() });
    const blase = h("div.blase");
    if (n.antwort_auf) {
      blase.append(h("span.zitat", { onclick: () => this.springen(n.antwort_auf.id) },
        h("b", { text: n.antwort_auf.von_name }), h("span", { text: n.antwort_auf.text || "Anhang" })));
    }
    blase.append(...textMitLinks(t));
    if (n.bearbeitet) blase.append(h("span.bearbeitet", { text: "bearbeitet" }));
    return blase;
  }

  /** Bild oder Video - mit Platzhalter, solange es noch lädt. */
  mediumBauen(n, d) {
    const laedt = !d.url && !d.kaputt;
    const zeile = h("div.medium" + (laedt ? ".laedt" : ""), {
      role: "button", tabindex: "0",
      "aria-label": d.kaputt ? `${n.art === "video" ? "Video" : "Bild"} kaputt` : laedt ? `${n.art === "video" ? "Video" : "Bild"} wird geladen` : `${n.art === "video" ? "Video" : "Bild"} ${d.name || ""} öffnen`,
      onclick: () => { if (d.url) leuchtkasten(n); },
      onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && d.url) { e.preventDefault(); leuchtkasten(n); } },
    });
    // Platz freihalten, damit nichts springt, wenn das Bild kommt
    const b = Number(d.breite) || 0, hh = Number(d.hoehe) || 0;
    if (b > 0 && hh > 0) {
      const max = 320;
      const f = Math.min(1, max / b, max / hh);
      zeile.classList.add("mit-mass");
      zeile.style.setProperty("width", `${Math.max(120, Math.round(b * f))}px`);
      zeile.style.setProperty("height", `${Math.max(90, Math.round(hh * f))}px`);
    } else if (laedt || d.kaputt) {
      zeile.style.setProperty("width", "240px");
      zeile.style.setProperty("height", "180px");
    }
    if (d.kaputt) {
      zeile.classList.add("kaputt");
      zeile.append(ic("triangle-alert", "ic-28"), h("span", { text: n.art === "video" ? "Das Video kam beschädigt an." : "Das Bild kam beschädigt an." }));
      return zeile;
    }
    if (n.art === "bild") {
      const quelle = d.url || d.vorschau;
      if (quelle) {
        const img = h("img", { src: quelle, alt: d.name || "Bild", decoding: "async", draggable: "false" });
        if (!d.url) img.classList.add("unscharf");                 // nur die kleine Vorschau
        else if (d.vorschau && d.vorschau !== d.url) {
          // Erst die Vorschau, dann weich das ganze Bild darüber
          zeile.style.setProperty("background-image", `url("${d.vorschau}")`);
          zeile.style.setProperty("background-size", "cover");
        }
        zeile.append(img);
      } else zeile.classList.add("schimmer");
    } else if (d.url) {
      zeile.append(h("video", { src: d.url, preload: "metadata", muted: true, playsinline: true }), h("span.spielen", ic("play", "ic-20")));
    } else if (d.vorschau) {
      zeile.append(h("img.unscharf", { src: d.vorschau, alt: "" }));
    } else zeile.classList.add("schimmer");
    if (laedt) {
      const anteil = typeof d.fortschritt === "number" ? d.fortschritt : null;
      const r = ring(anteil ?? 0);
      zeile._ring = r;
      zeile.append(h("div.ueber",
        h("div.kreis", anteil === null ? ic("download", "ic-20") : r),
        h("small", { text: anteil === null ? "Wartet …" : `${Math.round(anteil * 100)} %${d.groesse ? " von " + groesse(d.groesse) : ""}` })));
    }
    return zeile;
  }

  /** Dateikarte - der Ring zeigt, wie weit sie schon da ist. */
  dateiBauen(n, d) {
    const laedt = !d.url && !d.kaputt && typeof d.fortschritt === "number" && d.fortschritt < 1;
    let symbol;
    if (d.kaputt) symbol = h("div.dsymbol.kaputt", ic("triangle-alert", "ic-20"));
    else if (laedt) {
      const r = ring(d.fortschritt);
      symbol = h("div.dsymbol.laedt", r, ic("download", "ic-16"));
    } else symbol = h("div.dsymbol", ic("file-text", "ic-20"));
    const unter = d.kaputt ? "Beschädigt angekommen"
      : laedt ? `${Math.round(d.fortschritt * 100)} % von ${groesse(d.groesse || 0)}`
      : d.groesse ? groesse(d.groesse) : "";
    const karte = h("button.dateikarte", {
      type: "button", disabled: laedt || d.kaputt,
      "aria-label": `Datei ${d.name || ""}${unter ? ", " + unter : ""}`,
      onclick: () => this.dateiOeffnen(n),
    }, symbol, h("div.dtext", h("b", { text: d.name || "Datei" }), unter ? h("small", { text: unter }) : null));
    karte._ring = symbol.querySelector(".ring");
    return karte;
  }

  /** Riskante Dateien (.exe, .bat …) fragt Python zurück - dann hier nachfragen */
  async dateiOeffnen(n) {
    const r = await rufe("datei_oeffnen", n.id);
    if (r.ok) return;
    if (r.warnung) {
      const ja = await bestaetigen("Diese Datei wirklich öffnen?", r.warnung,
        { ja: "Trotzdem öffnen", gefahr: true, symbol: "triangle-alert" });
      if (!ja) return;
      const r2 = await rufe("datei_oeffnen", n.id, true);
      if (!r2.ok) toast(r2.fehler, "fehler", 5000);
      return;
    }
    toast(r.fehler, "fehler", 5000);
  }

  statusZeileSetzen() {
    this.innen.querySelectorAll(".status-zeile").forEach((x) => x.remove());
    const letzte = [...this.nachrichten].reverse().find((n) => n.ich && n.art !== "system");
    if (!letzte || letzte.geloescht) return;
    // "Gelesen" ergibt nur unter zwei Leuten Sinn - in einer Gruppe weiss
    // niemand, wer alles mitliest.
    if (this.u.art !== "dm" && !["senden", "fehler"].includes(letzte.status)) return;
    const zeile = this.zeilen.get(letzte.id);
    if (!zeile) return;
    const texte = { senden: "Wird gesendet …", gesendet: "Gesendet", zugestellt: "Zugestellt", gelesen: "Gelesen", fehler: "Nicht gesendet – nochmal versuchen" };
    const el = h("div.status-zeile" + (letzte.status === "fehler" ? ".fehler" : ""),
      letzte.status === "fehler" ? { onclick: () => this.erneut(letzte) } : {},
      letzte.status === "fehler" ? ic("circle-alert", "ic-14") : null,
      texte[letzte.status] || "",
      letzte.status === "gelesen" && letzte.gelesen_ts ? ` ${uhrzeit(letzte.gelesen_ts)}` : "",
      letzte.weg === "discord" ? h("span.weg", { text: " · über Discord" }) : letzte.weg === "lan" ? h("span.weg", { text: " · über WLAN" }) : null);
    zeile.querySelector(".spalte").append(el);
  }

  /* ---------------------------------------------------------- Ereignisse */
  neu(n) {
    if (this.zeilen.has(n.id)) { this.geaendert(n); return; }
    const unten = this.istUnten() || n.ich;
    if (!this.nachrichten.length) leeren(this.innen);
    this.tipptWeg();
    // Wer selbst schreibt, hat das Neue gesehen
    if (n.ich && this.neuTrenner) { this.neuTrenner.remove(); this.neuTrenner = null; this.ungelesenStart = 0; }
    this.nachrichten.push(n);
    this.zeileEinfuegen(n, this.nachrichten.length - 1);
    this.statusZeileSetzen();
    if (unten) this.nachUnten(true);
    else if (!n.ich) { this.neuSeitUnten += 1; this.knopfUntenSetzen(true); }
    if (!n.ich && document.hasFocus()) rufe("gelesen", this.u.id);
  }

  geaendert(n) {
    const i = this.nachrichten.findIndex((x) => x.id === n.id);
    if (i < 0) return;
    const vorher = this.nachrichten[i];
    this.nachrichten[i] = { ...vorher, ...n };
    const alt = this.zeilen.get(n.id);
    // Nur der Fortschritt einer Datei: den Ring weiterdrehen statt die
    // ganze Zeile neu zu bauen (sonst flackert der Platzhalter)
    const d = this.nachrichten[i].datei;
    const ringEl = alt?.querySelector(".ring");
    if (ringEl?.setzen && d && !d.url && !d.kaputt && typeof d.fortschritt === "number" && d.fortschritt < 1
        && vorher.datei && !vorher.datei.url && typeof vorher.datei.fortschritt === "number") {
      ringEl.setzen(d.fortschritt);
      const text = `${Math.round(d.fortschritt * 100)} %${d.groesse ? " von " + groesse(d.groesse) : ""}`;
      const klein = alt.querySelector(".ueber small, .dateikarte small");
      if (klein) klein.textContent = text;
      return;
    }
    const neu = this.zeileBauen(this.nachrichten[i]);
    neu.classList.add(...[...alt.classList].filter((c) => c.startsWith("lauf") || c === "neuer-lauf" || c === "schwanz"));
    alt.replaceWith(neu);
    this.zeilen.set(n.id, neu);
    this.laufKlassen(i);
    this.statusZeileSetzen();
  }

  tipptAnzeigen(name) {
    clearTimeout(this.tippZeit);
    if (!this.tipptZeile) {
      this.tipptZeile = h("div.zeile.tippt.neuer-lauf", { title: `${name} schreibt …` }, h("div.spalte", h("div.blase", h("i"), h("i"), h("i"))));
      this.innen.append(this.tipptZeile);
      if (this.istUnten()) this.nachUnten(true);
    }
    this.tippZeit = setTimeout(() => this.tipptWeg(), 6000);
  }

  tipptWeg() { this.tipptZeile?.remove(); this.tipptZeile = null; }

  springen(id) {
    const z = this.zeilen.get(id);
    if (!z) { toast("Die Nachricht ist nicht mehr geladen.", "info"); return; }
    z.scrollIntoView({ behavior: scrollArt(), block: "center" });
    z.classList.remove("blitz");
    void z.offsetWidth;
    z.classList.add("blitz");
  }

  /* ------------------------------------------------------------ Aktionen */
  async reagieren(n, emoji) {
    const r = await rufe("reagieren", this.u.id, n.id, emoji);
    if (!r.ok) toast(r.fehler, "fehler");
  }

  reaktionsMenue(anker, n) {
    const r = anker.getBoundingClientRect();
    menue(r.left - 120, r.bottom + 6, [
      h("div.reaktionsleiste", { role: "group", "aria-label": "Reagieren" }, SCHNELL.map((e) => h("button", { type: "button", role: "menuitem", "aria-label": `Mit ${e} reagieren`, onclick: () => { menueSchliessenUndReagieren(this, n, e); } }, e)),
        h("button", { type: "button", role: "menuitem", title: "Mehr", "aria-label": "Anderes Emoji", onclick: () => { emojiWahl(anker, (e) => this.reagieren(n, e)); } }, ic("plus"))),
    ], { label: "Reagieren" });
  }

  kontextMenue(x, y, n) {
    const eintraege = [
      h("div.reaktionsleiste", { role: "group", "aria-label": "Reagieren" }, SCHNELL.map((e) => h("button", { type: "button", role: "menuitem", "aria-label": `Mit ${e} reagieren`, onclick: () => menueSchliessenUndReagieren(this, n, e) }, e))),
      this.darfSchreiben() ? { text: "Antworten", symbol: "reply", aktion: () => this.antworten(n) } : null,
      n.text ? { text: "Kopieren", symbol: "copy", aktion: () => { navigator.clipboard?.writeText(n.text); toast("Kopiert"); } } : null,
      n.ich && n.art === "text" ? { text: "Bearbeiten", symbol: "pencil", aktion: () => this.bearbeiten(n) } : null,
      n.datei?.url ? { text: "Öffnen", symbol: "external-link", aktion: () => (n.art === "bild" || n.art === "video") ? leuchtkasten(n) : this.dateiOeffnen(n) } : null,
      n.datei?.url ? { text: "Speichern unter …", symbol: "download", aktion: () => rufe("datei_speichern", n.id) } : null,
      { text: "Infos", symbol: "info", aktion: () => nachrichtInfo(n) },
      "-",
      { text: n.ich ? "Löschen …" : "Für mich löschen", symbol: "trash-2", gefahr: true, aktion: () => this.loeschen(n) },
    ];
    menue(x, y, eintraege, { label: "Nachricht" });
  }

  antworten(n) {
    this.bezug = { art: "antwort", n };
    this.bezugZeigen();
    this.feld.focus();
  }

  bearbeiten(n) {
    this.bezug = { art: "bearbeiten", n };
    this.feld.value = n.text;
    this.bezugZeigen();
    this.feldAnpassen();
    this.feld.focus();
  }

  bezugZeigen() {
    this.bezugEl?.remove();
    this.bezugEl = null;
    if (!this.bezug) return;
    const { art, n } = this.bezug;
    this.bezugEl = h("div.bezug",
      ic(art === "antwort" ? "reply" : "pencil", "ic-16"),
      h("div", h("b", { text: art === "antwort" ? `Antwort an ${n.ich ? "dich" : n.von_name}` : "Nachricht bearbeiten" }), "  ", n.text || (n.datei?.name ?? "Anhang")),
      rundknopf("x", "Abbrechen", () => this.bezugWeg(), "klein"));
    this.eingabe.prepend(this.bezugEl);
  }

  bezugWeg() {
    if (this.bezug?.art === "bearbeiten") { this.feld.value = ""; this.feldAnpassen(); }
    this.bezug = null;
    this.bezugZeigen();
  }

  async loeschen(n) {
    if (n.ich) {
      blatt((zu) => [
        h("div.kopfsymbol.gefahr", ic("trash-2", "ic-20")),
        h("h2", { text: "Nachricht löschen?" }),
        h("p", { text: "„Für alle“ entfernt sie auch bei den anderen und den Geheimtext aus Discord. Wer sie schon gelesen hat, hat sie gelesen." }),
        h("div.aktionen",
          knopf("Abbrechen", () => zu()),
          knopf("Für mich", async () => { zu(); this.loeschAntwort(await rufe("loeschen", this.u.id, n.id, false)); }),
          knopf("Für alle", async () => { zu(); this.loeschAntwort(await rufe("loeschen", this.u.id, n.id, true)); }, "gefahr")),
      ], { breite: 440 });
    } else if (await bestaetigen("Für dich löschen?", "Die anderen sehen die Nachricht weiterhin.", { ja: "Löschen", gefahr: true, symbol: "trash-2" })) {
      this.loeschAntwort(await rufe("loeschen", this.u.id, n.id, false));
    }
  }

  loeschAntwort(r) { if (!r.ok) toast(r.fehler, "fehler"); }

  async erneut(n) {
    const r = await rufe("erneut_senden", this.u.id, n.id);
    if (!r.ok) toast(r.fehler, "fehler");
  }

  suchen() {
    blatt((zu) => {
      const feld = h("input.feld", { placeholder: "Suchen …", "aria-label": "Im Chat suchen" });
      const liste = h("div", { style: { "max-height": "50vh", overflow: "auto", "margin-top": "12px" } });
      let warte;
      feld.addEventListener("input", () => {
        clearTimeout(warte);
        warte = setTimeout(async () => {
          const r = await rufe("suchen", this.u.id, feld.value);
          ersetzen(liste, (r.liste || []).map((n) => h("div.eintrag.klickbar", { role: "button", tabindex: "0", onclick: () => { zu(); this.springen(n.id); }, onkeydown: (e) => e.key === "Enter" && e.currentTarget.click() },
            h("div.titel", n.text, h("small", { text: `${n.ich ? "Du" : n.von_name} · ${tagTitel(n.ts)} ${uhrzeit(n.ts)}` })))));
          if (feld.value && !(r.liste || []).length) liste.append(h("p", { text: "Nichts gefunden." }));
        }, 180);
      });
      return [h("h2", { text: `Suchen in „${this.u.titel}“` }), feld, liste];
    }, { breite: 520 });
  }

  infoUmschalten() {
    const wer = this.leiste.querySelector(".wer");
    if (this.info) { this.info.remove(); this.info = null; wer?.setAttribute("aria-expanded", "false"); return; }
    this.info = h("aside.infobereich.glas.glas-stark", { "aria-label": "Infos" });
    this.chat.append(this.info);
    wer?.setAttribute("aria-expanded", "true");
    this.infoFuellen();
  }

  async infoFuellen() {
    const r = await rufe("unterhaltung_info", this.u.id);
    if (!this.info) return;
    const u = this.u;
    const info = r.ok ? r : {};
    ersetzen(this.info,
      h("div.mitte",
        u.art === "kanal" ? h("div.avatar.gross.community", { style: { "--farbe": u.farbe || farbeFuer(u.id) } }, ic("hash", "ic-20")) : avatar(u.titel, u.farbe, "gross"),
        h("b", { text: u.art === "kanal" ? `#${u.kanal_name}` : u.titel }),
        u.art === "dm" ? h("span.mono", { text: u.id }) : null),
      h("div.gruppe",
        h("div.eintrag", ic("bell-off"), h("div.titel", { text: "Stummschalten" }),
          schalter(u.stumm, async (an) => { await rufe("unterhaltung_setzen", u.id, { stumm: an }); })),
        h("div.eintrag", ic("pin"), h("div.titel", { text: "Oben anheften" }),
          schalter(u.angeheftet, async (an) => { await rufe("unterhaltung_setzen", u.id, { angeheftet: an }); })),
        u.art === "dm" ? h("div.eintrag.klickbar", { onclick: () => sicherheitBlatt(u.id) }, ic("fingerprint"), h("div.titel", { text: "Sicherheitsnummer" }), ic("chevron-right", "ic-16")) : null),
      (info.medien || []).length ? [h("div.gruppe-titel", { text: "Fotos & Videos" }),
        h("div.mediengitter", info.medien.map((m) => h("img", { src: m.vorschau, alt: "Bild öffnen", tabindex: "0", role: "button", onclick: () => leuchtkasten(m), onkeydown: (e) => e.key === "Enter" && leuchtkasten(m) })))] : null,
      (info.mitglieder || []).length ? [h("div.gruppe-titel", { style: { "margin-top": "16px" }, text: "Zuletzt aktiv" }),
        h("div.gruppe", info.mitglieder.map((m) => h("div.eintrag", avatar(m.name, m.farbe, "klein"), h("div.titel", m.name, h("small.mono", { text: m.id })))))] : null,
    );
  }

  /* ---------------------------------------------------------- Eingabe */
  eingabeBauen() {
    this.feld = h("textarea", { rows: 1, placeholder: this.darfSchreiben() ? (this.u.art === "kanal" ? `Nachricht an #${this.u.kanal_name || this.u.titel}` : "Nachricht") : "Nur Admins können hier schreiben", disabled: !this.darfSchreiben(), "aria-label": "Nachricht", spellcheck: "true" });
    this.feld.value = this.u.entwurf || "";
    this.feld.addEventListener("input", () => { this.feldAnpassen(); this.getippt(); });
    this.feld.addEventListener("keydown", (e) => this.taste(e));
    this.feld.addEventListener("paste", (e) => {
      const dateien = [...(e.clipboardData?.files || [])];
      if (dateien.length) { e.preventDefault(); this.dateienSenden(dateien); }
    });
    this.feld.addEventListener("blur", () => rufe("entwurf_setzen", this.u.id, this.bezug?.art === "bearbeiten" ? "" : this.feld.value));

    this.knopfAnhang = rundknopf("plus", "Anhängen", (e) => this.anhangMenue(e.currentTarget));
    this.knopfEmoji = rundknopf("smile", "Emoji", (e) => emojiWahl(e.currentTarget, (x) => this.einfuegen(x)));
    this.knopfSenden = h("button.rund.gefuellt.senden", { type: "button", title: "Senden (Enter)", "aria-label": "Senden", onclick: () => this.abschicken() }, ic("arrow-up"));
    this.knopfMikro = rundknopf("mic", "Sprachnachricht", () => this.aufnehmen());
    this.reihe = h("div.reihe", this.knopfAnhang, this.feld, this.knopfEmoji, this.knopfMikro, this.knopfSenden);
    this.eingabe = h("div.eingabe.glas.bricht", this.reihe);
    if (!this.darfSchreiben()) { this.knopfAnhang.disabled = true; this.knopfMikro.disabled = true; this.knopfEmoji.disabled = true; }
    this.knoepfeUmschalten();
    setTimeout(() => this.feldAnpassen());
  }

  knoepfeUmschalten() {
    const text = this.feld.value.trim().length > 0;
    const vorher = !this.knopfSenden.classList.contains("versteckt");
    this.knopfSenden.classList.toggle("versteckt", !text);
    this.knopfMikro.classList.toggle("versteckt", text);
    if (text && !vorher) { this.knopfSenden.classList.remove("rein"); void this.knopfSenden.offsetWidth; this.knopfSenden.classList.add("rein"); }
  }

  feldAnpassen() {
    this.feld.style.setProperty("height", "auto");
    this.feld.style.setProperty("height", `${Math.min(this.feld.scrollHeight, 140)}px`);
    this.knoepfeUmschalten();
  }

  einfuegen(text) {
    const f = this.feld;
    const a = f.selectionStart ?? f.value.length;
    f.value = f.value.slice(0, a) + text + f.value.slice(f.selectionEnd ?? a);
    f.selectionStart = f.selectionEnd = a + text.length;
    f.focus();
    this.feldAnpassen();
  }

  getippt() {
    const jetzt = Date.now();
    if (jetzt - this.zuletztGetippt > 3000 && this.feld.value) {
      this.zuletztGetippt = jetzt;
      rufe("tippt", this.u.id);
    }
  }

  taste(e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.abschicken(); }
    else if (e.key === "Escape" && this.bezug) { e.preventDefault(); this.bezugWeg(); }
    else if (e.key === "ArrowUp" && !this.feld.value) {
      const letzte = [...this.nachrichten].reverse().find((n) => n.ich && n.art === "text" && !n.geloescht);
      if (letzte) { e.preventDefault(); this.bearbeiten(letzte); }
    }
  }

  async abschicken() {
    const text = this.feld.value.trim();
    if (!text || !this.darfSchreiben()) return;
    const bezug = this.bezug;
    this.feld.value = "";
    this.bezug = null;
    this.bezugZeigen();
    this.feldAnpassen();
    let r;
    if (bezug?.art === "bearbeiten") {
      if (text === bezug.n.text) return;
      r = await rufe("bearbeiten", this.u.id, bezug.n.id, text);
    } else {
      r = await rufe("senden", this.u.id, { text, antwort_auf: bezug?.n.id || null });
    }
    if (!r.ok) {
      toast(r.fehler, "fehler", 5000);
      if (!this.feld.value) { this.feld.value = text; this.feldAnpassen(); }
    }
    rufe("entwurf_setzen", this.u.id, "");
  }

  anhangMenue(anker) {
    menueAn(anker, [
      { text: "Foto oder Video", symbol: "image", aktion: () => this.dateiDialog("medien") },
      { text: "Datei", symbol: "paperclip", aktion: () => this.dateiDialog("datei") },
      { text: "Sprachnachricht", symbol: "mic", aktion: () => this.aufnehmen() },
    ]);
  }

  async dateiDialog(art) {
    const r = await rufe("datei_senden", this.u.id, null, art);
    if (!r.ok && r.fehler) toast(r.fehler, "fehler", 5000);
  }

  async dateienSenden(dateien) {
    for (const d of dateien) {
      // pywebview liefert beim Ablegen den echten Pfad mit - dann muss die
      // Datei nicht erst durch JavaScript wandern.
      if (d.pywebviewFullPath) {
        const r = await rufe("datei_senden", this.u.id, d.pywebviewFullPath, "auto");
        if (!r.ok) toast(r.fehler, "fehler", 5000);
        continue;
      }
      if (d.size > 25 * 1024 * 1024) { toast(`„${d.name}“ ist zu gross zum Hineinziehen – nimm „+ → Datei“.`, "fehler", 5000); continue; }
      const daten = await zuBase64(d);
      const r = await rufe("datei_daten_senden", this.u.id, d.name, d.type, daten);
      if (!r.ok) toast(r.fehler, "fehler", 5000);
    }
  }

  /* ----------------------------------------------------- Sprachnachricht */
  async aufnehmen() {
    if (!this.darfSchreiben()) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      toast("Aufnehmen geht hier nicht – das Mikrofon ist für VP4 nicht freigegeben.", "fehler", 5000);
      return;
    }
    let strom;
    try {
      strom = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      toast("Kein Zugriff aufs Mikrofon. Prüfe in Windows unter Datenschutz → Mikrofon.", "fehler", 6000);
      return;
    }
    const typ = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    const rec = new MediaRecorder(strom, { mimeType: typ, audioBitsPerSecond: 32000 });
    const teile = [];
    const start = Date.now();
    rec.ondataavailable = (e) => e.data.size && teile.push(e.data);

    // Pegel für die Wellenform mitschreiben
    const ctx = new AudioContext();
    const quelle = ctx.createMediaStreamSource(strom);
    const analyse = ctx.createAnalyser();
    analyse.fftSize = 512;
    quelle.connect(analyse);
    const puffer = new Uint8Array(analyse.fftSize);
    const pegel = [];
    const messen = setInterval(() => {
      analyse.getByteTimeDomainData(puffer);
      let summe = 0;
      for (const v of puffer) summe += ((v - 128) / 128) ** 2;
      pegel.push(Math.sqrt(summe / puffer.length));
    }, 60);

    const uhr = h("span.zeit", { text: "0:00", "aria-live": "off" });
    const ticken = setInterval(() => { uhr.textContent = dauer(Date.now() - start); }, 250);
    let abbrechen = false;
    const ende = () => { clearInterval(messen); clearInterval(ticken); strom.getTracks().forEach((t) => t.stop()); ctx.close(); ersetzen(this.reihe, this.knopfAnhang, this.feld, this.knopfEmoji, this.knopfMikro, this.knopfSenden); this.knoepfeUmschalten(); this.aufnahmeAbbrechen = null; };
    this.aufnahmeAbbrechen = () => { abbrechen = true; if (rec.state !== "inactive") rec.stop(); else ende(); };

    rec.onstop = async () => {
      ende();
      if (abbrechen) return;
      const ms = Date.now() - start;
      if (ms < 700) { toast("Zu kurz – zum Aufnehmen etwas länger sprechen.", "info"); return; }
      const blob = new Blob(teile, { type: typ });
      const r = await rufe("sprachnachricht_senden", this.u.id, await zuBase64(blob), ms, wellenformAus(pegel));
      if (!r.ok) toast(r.fehler, "fehler", 5000);
    };
    rec.start(250);
    ersetzen(this.reihe,
      rundknopf("trash-2", "Verwerfen", () => this.aufnahmeAbbrechen()),
      h("div.aufnahme", h("i.rotpunkt"), "Aufnahme läuft", uhr),
      h("button.rund.gefuellt", { type: "button", "aria-label": "Senden", title: "Senden", onclick: () => rec.stop() }, ic("arrow-up")));
  }
}

function menueSchliessenUndReagieren(ansicht, n, e) {
  menueSchliessen({ fokusZurueck: true });
  ansicht.reagieren(n, e);
}

function schalter(an, aendern) {
  const s = h("button.schalter", { type: "button", role: "switch", "aria-checked": String(!!an) });
  s.addEventListener("click", () => {
    const neu = s.getAttribute("aria-checked") !== "true";
    s.setAttribute("aria-checked", String(neu));
    aendern(neu);
  });
  return s;
}
export { schalter };

function wellenformAus(pegel, balken = 48) {
  if (!pegel.length) return Array(balken).fill(0.2);
  const max = Math.max(...pegel, 0.01);
  const out = [];
  for (let i = 0; i < balken; i++) {
    const a = Math.floor((i * pegel.length) / balken);
    const b = Math.max(a + 1, Math.floor(((i + 1) * pegel.length) / balken));
    const stueck = pegel.slice(a, b);
    out.push(Math.max(0.08, Math.min(1, (stueck.reduce((x, y) => x + y, 0) / stueck.length) / max)));
  }
  return out.map((x) => Math.round(x * 100) / 100);
}

function zuBase64(blob) {
  return new Promise((fertig, fehler) => {
    const leser = new FileReader();
    leser.onload = () => fertig(String(leser.result).split(",", 2)[1] || "");
    leser.onerror = () => fehler(leser.error);
    leser.readAsDataURL(blob);
  });
}

function spracheBauen(n) {
  const d = n.datei;
  const welle = h("div.welle", (d.wellenform || Array(48).fill(0.3)).map((x) => h("i", { style: { height: `${Math.round(6 + x * 20)}px` } })));
  const zeit = h("small", { text: dauer(d.dauer_ms || 0) });
  const knopfEl = h("button.abspielen", { type: "button", "aria-label": "Abspielen" }, ic("play", "ic-16"));
  let audio = null;
  knopfEl.addEventListener("click", () => {
    if (!audio) {
      audio = new Audio(d.url);
      audio.addEventListener("timeupdate", () => {
        const anteil = audio.duration ? audio.currentTime / audio.duration : 0;
        const balken = welle.children;
        for (let i = 0; i < balken.length; i++) balken[i].classList.toggle("gespielt", i / balken.length < anteil);
        zeit.textContent = dauer(audio.currentTime * 1000);
      });
      audio.addEventListener("ended", () => { ersetzen(knopfEl, ic("play", "ic-16")); zeit.textContent = dauer(d.dauer_ms || 0); });
    }
    if (audio.paused) { audio.play(); ersetzen(knopfEl, ic("pause", "ic-16")); }
    else { audio.pause(); ersetzen(knopfEl, ic("play", "ic-16")); }
  });
  welle.addEventListener("click", (e) => {
    if (!audio?.duration) return;
    const r = welle.getBoundingClientRect();
    audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
  });
  return h("div.sprache", knopfEl, welle, zeit);
}

export function leuchtkasten(n) {
  const d = n.datei || n;
  const medium = (n.art === "video" || d.mime?.startsWith("video/"))
    ? h("video", { src: d.url, controls: true, autoplay: true })
    : h("img", { src: d.url || d.vorschau, alt: d.name || "" });
  const vorher = document.activeElement;
  const el = h("div.leuchtkasten", { role: "dialog", "aria-modal": "true", "aria-label": d.name || "Bild", onclick: (e) => { if (e.target === el) zu(); } },
    medium,
    h("div.leiste.glas",
      n.id ? rundknopf("download", "Speichern", () => rufe("datei_speichern", n.id)) : null,
      rundknopf("x", "Schliessen", () => zu())));
  const taste = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); zu(); } };
  function zu() { el.remove(); document.removeEventListener("keydown", taste, true); if (vorher?.isConnected) vorher.focus({ preventScroll: true }); }
  document.addEventListener("keydown", taste, true);
  document.body.append(el);
  el.querySelector(".leiste .rund:last-child")?.focus();
}

function nachrichtInfo(n) {
  const wege = { lan: "Direkt im WLAN", discord: "Über Discord (verschlüsselt)" };
  blatt((zu) => [
    h("h2", { text: "Nachricht" }),
    h("div.gruppe",
      h("div.eintrag", h("div.titel", { text: "Gesendet" }), h("span.wert", { text: `${tagTitel(n.ts)}, ${uhrzeit(n.ts)}` })),
      n.weg ? h("div.eintrag", h("div.titel", { text: "Weg" }), h("span.wert", { text: wege[n.weg] || n.weg })) : null,
      n.ich ? h("div.eintrag", h("div.titel", { text: "Status" }), h("span.wert", { text: { senden: "Wird gesendet", gesendet: "Gesendet", zugestellt: "Zugestellt", gelesen: "Gelesen", fehler: "Fehlgeschlagen" }[n.status] || "–" })) : null,
      h("div.eintrag", h("div.titel", { text: "Verschlüsselung" }), h("span.wert", { text: "AES-256-GCM, Ende-zu-Ende" }))),
    h("div.aktionen", knopf("Fertig", () => zu(), "primaer")),
  ], { breite: 420 });
}
