// =====================================================================
//  demo.js - erfundene Daten, NUR für Screenshots und Design-Arbeit
// =====================================================================
//  Wird ausschliesslich mit ?demo=1 geladen. Nichts hiervon erreicht
//  Python, nichts wird gespeichert.
// =====================================================================

const jetzt = Date.now();
const min = 60 * 1000;
const ICH = { id: "7AC5E-HTN4Q", name: "Leon", avatar_farbe: "#0088FF" };
const parameter = new URLSearchParams(location.search);

function bild(farben, text = "") {
  const c = document.createElement("canvas");
  c.width = 480; c.height = 360;
  const g = c.getContext("2d");
  const verlauf = g.createLinearGradient(0, 0, 480, 360);
  farben.forEach((f, i) => verlauf.addColorStop(i / (farben.length - 1), f));
  g.fillStyle = verlauf; g.fillRect(0, 0, 480, 360);
  g.fillStyle = "rgba(255,255,255,.35)";
  g.beginPath(); g.arc(360, 90, 46, 0, Math.PI * 2); g.fill();
  g.fillStyle = "rgba(0,0,0,.18)";
  g.beginPath(); g.moveTo(0, 360); g.lineTo(150, 190); g.lineTo(260, 300); g.lineTo(340, 220); g.lineTo(480, 360); g.fill();
  if (text) { g.fillStyle = "#fff"; g.font = "600 28px Segoe UI, sans-serif"; g.fillText(text, 24, 48); }
  return c.toDataURL("image/jpeg", 0.85);
}

const welle = Array.from({ length: 48 }, (_, i) => Math.round((0.25 + 0.75 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.21))) * 100) / 100);

const unterhaltungen = [
  { id: "MAXX2-0002A", art: "dm", titel: "Max", farbe: "#34C759", online: true, weg: "lan", verifiziert: true, ungelesen: 2, angeheftet: true, letzte: { text: "Hast du das Video gesehen? 😂", ts: jetzt - 3 * min } },
  { id: "G-K7Q2M9PX", art: "gruppe", titel: "Die Jungs", farbe: "#FF8D28", untertitel: "4 Mitglieder", ungelesen: 5, letzte: { text: "Wer ist heute dabei?", ts: jetzt - 12 * min, von_name: "Jonas" } },
  { id: "SOFI3-77B2C", art: "dm", titel: "Sofia", farbe: "#CB30E0", online: false, weg: "discord", zuletzt_aktiv: "vor 20 Min.", letzte: { text: "Danke dir! Bis morgen 👋", ts: jetzt - 64 * min, ich: true } },
  { id: "TIMM4-99KLA", art: "dm", titel: "Tim", farbe: "#00C3D0", online: false, weg: "discord", schluessel_geaendert: true, letzte: { text: "Neues Handy, neue Nummer haha", ts: jetzt - 26 * 60 * min } },
  { id: "EMMA5-1Q2W3", art: "dm", titel: "Emma Schulz", farbe: "#FF2D55", online: true, weg: "discord", stumm: true, ungelesen: 1, letzte: { text: "Hab dir die Notizen geschickt", ts: jetzt - 3 * 24 * 60 * min } },
  { id: "NOAH6-ZZ81P", art: "dm", titel: "Noah", farbe: "#6155F5", letzte: { text: "Sprachnachricht", ts: jetzt - 9 * 24 * 60 * min } },
  // Kanäle der Community
  { id: "K-ALLG", art: "kanal", titel: "allgemein", kanal_name: "allgemein", community_id: "G-10B00001", community_name: "Klasse 10b", farbe: "#0088FF", ungelesen: 3, letzte: { text: "Morgen fällt Mathe aus!!", ts: jetzt - 30 * min } },
  { id: "K-HAUS", art: "kanal", titel: "hausaufgaben", kanal_name: "hausaufgaben", community_id: "G-10B00001", community_name: "Klasse 10b", farbe: "#0088FF", letzte: { text: "S. 84 Nr. 3", ts: jetzt - 5 * 60 * min } },
  { id: "K-MEME", art: "kanal", titel: "memes", kanal_name: "memes", community_id: "G-10B00001", community_name: "Klasse 10b", farbe: "#0088FF", letzte: { text: "💀💀💀", ts: jetzt - 2 * 60 * min } },
  { id: "K-INFO", art: "kanal", titel: "ankündigungen", kanal_name: "ankündigungen", community_id: "G-10B00001", community_name: "Klasse 10b", farbe: "#0088FF", nur_admins: true, darf_schreiben: false, letzte: { text: "Klassenfahrt: Zettel bis Freitag", ts: jetzt - 2 * 24 * 60 * min } },
];

const communities = [
  { id: "G-10B00001", name: "Klasse 10b", icon: "📚", farbe: "#0088FF", admin: true, besitzer: true, rolle: "besitzer", geladen: true, mitglieder: 23, letzte_ts: jetzt - 30 * min,
    kanaele: [
      { id: "allg", name: "allgemein", unterhaltung: "K-ALLG", ungelesen: 3 },
      { id: "haus", name: "hausaufgaben", unterhaltung: "K-HAUS" },
      { id: "meme", name: "memes", unterhaltung: "K-MEME" },
      { id: "info", name: "ankündigungen", unterhaltung: "K-INFO", nur_admins: true },
    ] },
  { id: "G-GAME0002", name: "Gaming Squad", icon: "🎮", farbe: "#6155F5", rolle: "mitglied", geladen: true, mitglieder: 8, letzte_ts: jetzt - 4 * 60 * min,
    kanaele: [{ id: "a", name: "allgemein", unterhaltung: "K-G1" }, { id: "b", name: "clips", unterhaltung: "K-G2" }] },
];

let zaehler = 100;
const n = (id, von, minuten, text, extra = {}) => {
  const ich = von === "ich";
  const leute = { max: ["Max", "#34C759"], jonas: ["Jonas", "#FF8D28"], lina: ["Lina", "#FF2D55"], ben: ["Ben", "#00C3D0"], sofia: ["Sofia", "#CB30E0"] };
  const [name, farbe] = ich ? [ICH.name, ICH.avatar_farbe] : leute[von] || [von, "#8E8E93"];
  return { id: id || `n${zaehler++}`, von: ich ? ICH.id : von, von_name: name, von_farbe: farbe, ich, ts: jetzt - minuten * min, art: "text", text, status: ich ? "gelesen" : undefined, gelesen_ts: jetzt - 2 * min, weg: "lan", reaktionen: [], ...extra };
};

const verlaeufe = {
  "MAXX2-0002A": [
    n("m1", "max", 26 * 60, "Bist du morgen beim Training?"),
    n("m2", "ich", 26 * 60 - 2, "Ja klar, 17 Uhr wie immer"),
    n("m3", "max", 26 * 60 - 3, "Perfekt 👍"),
    n("m4", "max", 42, "Schau mal, das war gestern am See"),
    n("m5", "max", 41, "", { art: "bild", datei: { name: "see.jpg", vorschau: bild(["#2E7CF6", "#7FD3E6", "#FFC9B8"]), url: bild(["#2E7CF6", "#7FD3E6", "#FFC9B8"]), breite: 320, hoehe: 240 } }),
    n("m6", "ich", 38, "Wie schön ist das bitte 😍", { reaktionen: [{ emoji: "❤️", anzahl: 1 }] }),
    n("m7", "ich", 37, "Nächstes Mal komm ich mit"),
    n("m8", "max", 20, "Unbedingt! Ich schick dir noch die Datei vom Projekt", { antwort_auf: { id: "m7", von_name: "Du", text: "Nächstes Mal komm ich mit" } }),
    n("m9", "max", 19, "", { art: "datei", datei: { name: "Projekt_Präsentation.pdf", groesse: 2_480_000, mime: "application/pdf" } }),
    n("m10", "ich", 8, "", { art: "sprache", datei: { dauer_ms: 14000, wellenform: welle, url: "" } }),
    n("m11", "max", 4, "Hast du das Video gesehen? 😂"),
    n("m12", "max", 3, "https://example.com/video – der Typ am Ende 💀"),
    n("m13", "ich", 1, "😂😂😂"),
    n("m14", "ich", 0.5, "Ja, ich konnte nicht mehr", { status: "zugestellt", reaktionen: [{ emoji: "😂", anzahl: 2, meine: false }, { emoji: "🔥", anzahl: 1, meine: true }] }),
  ],
  "G-K7Q2M9PX": [
    n("g1", "jonas", 70, "Leute, Samstag Kino?"),
    n("g2", "lina", 66, "Bin dabei!"),
    n("g3", "ben", 65, "Welcher Film?"),
    n("g4", "jonas", 60, "Der neue mit dem Weltraum 🚀"),
    n("g5", "ich", 55, "Ich auch, wenn es nach 18 Uhr ist"),
    n("g6", "lina", 30, "19:30 passt allen?", { reaktionen: [{ emoji: "👍", anzahl: 3, meine: true }] }),
    n("g7", "jonas", 12, "Wer ist heute dabei?"),
    // Noch unterwegs: ein Bild ohne Vorschau und eine halbe Datei
    n("g8", "lina", 9, "", { art: "bild", datei: { name: "kinoplakat.jpg", breite: 400, hoehe: 300, groesse: 1_850_000, fortschritt: 0.42 } }),
    n("g9", "ben", 8, "", { art: "datei", datei: { name: "Kinoprogramm_Samstag_und_Sonntag.pdf", groesse: 1_240_000, fortschritt: 0.65 } }),
    n("g10", "ben", 7.5, "", { art: "bild", datei: { name: "kaputt.jpg", breite: 300, hoehe: 200, kaputt: true } }),
  ],
  "K-ALLG": [
    n("k1", "lina", 120, "Hat jemand die Folien von Bio?"),
    n("k2", "ben", 118, "Hier 👇"),
    n("k3", "ben", 117, "", { art: "datei", datei: { name: "Bio_Zellteilung.pptx", groesse: 8_100_000 } }),
    n("k4", "ich", 90, "Danke Ben!"),
    n("k5", "jonas", 30, "Morgen fällt Mathe aus!!", { reaktionen: [{ emoji: "🔥", anzahl: 9 }, { emoji: "😮", anzahl: 2 }] }),
  ],
  "TIMM4-99KLA": [
    n("t0", "TIMM4-99KLA", 3 * 24 * 60, "Bis Montag!"),
    { id: "t1", art: "system", ts: jetzt - 27 * 60 * min, text: "Tims Schlüssel hat sich geändert. Vergleicht die Sicherheitsnummer." },
    n("t2", "TIMM4-99KLA", 26 * 60, "Neues Handy, neue Nummer haha"),
    n("t3", "TIMM4-99KLA", 26 * 60 - 1, "", { art: "datei", datei: { name: "Setup_NeuesSpiel.exe", groesse: 48_200_000, mime: "application/x-msdownload", url: "blob:demo" } }),
  ],
};

const SCHLUESSELBUND = [
  { label: "Für Max", typ: "aes", verfahren: "AES-256-GCM", meta: "Erstellt am 12. Sep." },
  { label: "Mein PQ-Schlüssel", typ: "pq", verfahren: "Post-Quanten (Hybrid)", meta: "Schlüsselpaar" },
  { label: "Familienfotos", typ: "passwort", verfahren: "Passwort (AES-256)", meta: "" },
];

const VERFAHREN = [
  ["AES-256-GCM", "sicher", "aes", "Der Standard. Wenn du dich nicht entscheiden willst: nimm das."],
  ["ChaCha20-Poly1305", "sicher", "chacha", "Gleichwertig zu AES, auf älteren Geräten schneller."],
  ["XChaCha20-Poly1305", "sicher", "chacha", "Wie ChaCha20, aber mit 24-Byte-Nonce."],
  ["AES-256-GCM-SIV", "sicher", "aes", "Verzeiht ein versehentlich doppelt benutztes Nonce."],
  ["Passwort (AES-256)", "sicher", "passwort", "Ein Passwort statt eines langen Schlüssels."],
  ["age (Passwort)", "sicher", "passwort", "Kann auch das offizielle Programm age öffnen."],
  ["age (Schlüsselpaar)", "sicher", "age", "age mit öffentlichem Schlüssel."],
  ["RSA-2048", "sicher", "rsa", "Zwei Schlüssel, nur für kurze Texte."],
  ["Post-Quanten (Hybrid)", "pq", "pq", "ML-KEM-768 + X25519 – auch gegen künftige Quantencomputer."],
  ["Caesar", "spiel", "zahl", "Jeder Buchstabe um eine feste Zahl verschoben."],
  ["Vigenère", "spiel", "wort", "Caesar mit wechselnder Verschiebung."],
  ["Playfair", "spiel", "wort", "Buchstabenpaare über eine 5×5-Tabelle."],
  ["Rail-Fence", "spiel", "zahl", "Zickzack über mehrere Zeilen."],
  ["ROT13", "spiel", "keiner", "Caesar mit 13 – zweimal ist wieder Klartext."],
  ["Atbash", "spiel", "keiner", "A wird zu Z, B zu Y."],
  ["Morse", "spiel", "keiner", "Keine Verschlüsselung, eine Schreibweise."],
  ["XOR", "spiel", "wort", "Byte für Byte mit dem Schlüssel verrechnet."],
  ["Base64", "spiel", "keiner", "Nur eine andere Schreibweise."],
].map(([name, art, key, hinweis]) => ({ name, art, key, hinweis,
  schluessel_beschriftung: { aes: "Schlüssel (Base64, 44 Zeichen)", chacha: "Schlüssel (Base64, 44 Zeichen)", passwort: "Passwort", rsa: "Öffentlicher bzw. privater Schlüssel", pq: "Öffentlicher bzw. privater Schlüssel", age: "age-Empfänger bzw. Identität", zahl: "Eine Zahl, z. B. 3", wort: "Schlüsselwort", keiner: "Kein Schlüssel nötig" }[key],
  erzeugbar: ["aes", "chacha", "rsa", "pq", "age"].includes(key) }));

const einstellungen = { design: parameter.get("design") || "system", farbe: parameter.get("farbe") || "blau", tapete: parameter.get("tapete") || "tahoe", transport_modus: "beide", lesebestaetigungen: true, tippanzeige: true, mitteilungen: true, mitteilung_vorschau: true };
const ereignisse = [];
const ok = (x = {}) => ({ ok: true, ...x });
// Für ui_pruefung.py: Ereignisse einspeisen und mitzählen, was gerufen wurde
const aufrufe = {};
if (parameter.has("demo")) {
  window.vp4Demo = { ereignis: (e) => ereignisse.push(e), aufrufe };
}
const MITGLIEDER = [
  { id: "7AC5E-HTN4Q", name: "Leon", farbe: "#0088FF", ts: jetzt - 1 * min, besitzer: true, admin: true, ich: true },
  { id: "JONA8-77QWE", name: "Jonas", farbe: "#FF8D28", ts: jetzt - 30 * min, admin: true },
  { id: "LINA2-K4M9P", name: "Lina", farbe: "#FF2D55", ts: jetzt - 118 * min },
  { id: "BENN5-3XZ8A", name: "Ben", farbe: "#00C3D0", ts: jetzt - 117 * min },
  { id: "SOFI3-77B2C", name: "Sofia Maria von der Heide-Hohenstein", farbe: "#CB30E0", ts: jetzt - 26 * 60 * min },
];

const kanalFinden = (cid, kid) => (communities.find((c) => c.id === cid)?.kanaele || []).find((k) => k.id === kid);
const geaendert = (cid) => { ereignisse.push({ typ: "community_geaendert", id: cid }); return ok(); };

const echteApi = {
  async status() {
    const phase = parameter.get("phase") || "bereit";
    return ok({ phase, version: "5.0.0", repo_url: "https://github.com/Xembou/VP4", profil: phase === "einrichten" ? null : ICH, einstellungen, verbindung: { lan: "an", discord: "an" }, windows_verfuegbar: true,
      alte_daten: parameter.has("alt") ? { schluessel: 3 } : null });
  },
  async ereignisse_holen() { return ok({ liste: ereignisse.splice(0) }); },
  async einrichten(d) { Object.assign(ICH, { name: d.name, avatar_farbe: d.avatar_farbe }); return ok({ profil: ICH }); },
  async entsperren(pw) { return pw === "falsch" ? { ok: false, fehler: "Das Passwort stimmt nicht." } : ok(); },
  async sperren() { return ok(); },
  async kontakt_blockieren() { return ok(); },
  async datei_oeffnen(id, bestaetigt = false) {
    const x = Object.values(verlaeufe).flat().find((m) => m.id === id);
    if (/\.(exe|bat|cmd|lnk|ps1|msi|scr)$/i.test(x?.datei?.name || "") && !bestaetigt) {
      return { ok: false, warnung: `„${x.datei.name}“ ist ein Programm. Programme aus dem Chat können deinen PC übernehmen – öffne es nur, wenn du sicher weisst, von wem es ist und was es tut.` };
    }
    return ok();
  },
  async datei_speichern() { return ok(); },
  async community_mitglieder() { return ok({ liste: MITGLIEDER }); },
  async community_umbenennen(cid, name, icon) { const c = communities.find((x) => x.id === cid); Object.assign(c, { name, icon: icon || c.icon }); return geaendert(cid); },
  async kanal_anlegen(cid, name, nurAdmins) { const c = communities.find((x) => x.id === cid); c.kanaele.push({ id: `k-${name}`, name, unterhaltung: `k-${name}`, nur_admins: !!nurAdmins }); return geaendert(cid); },
  async kanal_umbenennen(cid, kid, name) { kanalFinden(cid, kid).name = name; return geaendert(cid); },
  async kanal_loeschen(cid, kid) { const c = communities.find((x) => x.id === cid); c.kanaele = c.kanaele.filter((k) => k.id !== kid); return geaendert(cid); },
  async kanal_verschieben(cid, kid, pos) { const c = communities.find((x) => x.id === cid); const k = kanalFinden(cid, kid); c.kanaele = c.kanaele.filter((x) => x !== k); c.kanaele.splice(pos, 0, k); return geaendert(cid); },
  async kanal_nur_admins_setzen(cid, kid, ja) { kanalFinden(cid, kid).nur_admins = !!ja; return geaendert(cid); },
  async admin_setzen(cid, nid, ja) { const m = MITGLIEDER.find((x) => x.id === nid); if (m) m.admin = !!ja; return geaendert(cid); },
  async mitglied_entfernen(cid, nid) { const i = MITGLIEDER.findIndex((x) => x.id === nid); if (i >= 0) MITGLIEDER.splice(i, 1); geaendert(cid); return ok({ code: "VP4G2-NEUERcodeNEUERcodeNEUERcodeNEUERcodeNEUERcodeNEUERcode" }); },
  async community_verlassen() { return ok(); },
  async community_code_erneuern() { return ok({ code: "VP4G2-NEUERcodeNEUERcodeNEUERcodeNEUERcode" }); },
  async passwort_staerke(pw) { const s = Math.min(4, Math.floor(pw.length / 4)); return ok({ stufe: s, text: ["Sehr schwach", "Schwach", "Mittel", "Gut", "Stark"][s] }); },
  async unterhaltungen() { return ok({ liste: unterhaltungen }); },
  async communities() { return ok({ liste: communities }); },
  async anfragen() { return ok({ liste: [{ id: "LUKA7-3PQ9R", name: "Lukas", farbe: "#FF8D28", richtung: "rein" }] }); },
  async nachrichten(id) { return ok({ liste: verlaeufe[id] || [], mehr: false }); },
  async senden(uid, { text, antwort_auf }) {
    const nachricht = n(null, "ich", 0, text, { status: "senden", weg: "discord" });
    if (antwort_auf) { const bezug = (verlaeufe[uid] || []).find((x) => x.id === antwort_auf); if (bezug) nachricht.antwort_auf = { id: bezug.id, von_name: bezug.ich ? "Du" : bezug.von_name, text: bezug.text }; }
    (verlaeufe[uid] ||= []).push(nachricht);
    ereignisse.push({ typ: "nachricht_neu", unterhaltung: uid, nachricht });
    setTimeout(() => ereignisse.push({ typ: "nachricht_geaendert", unterhaltung: uid, nachricht: { id: nachricht.id, status: "zugestellt" } }), 700);
    return ok({ id: nachricht.id });
  },
  async reagieren(uid, nid, emoji) {
    const x = (verlaeufe[uid] || []).find((m) => m.id === nid);
    if (!x) return ok();
    const r = x.reaktionen.find((y) => y.emoji === emoji);
    if (r?.meine) { r.anzahl -= 1; r.meine = false; if (!r.anzahl) x.reaktionen = x.reaktionen.filter((y) => y !== r); }
    else if (r) { r.anzahl += 1; r.meine = true; }
    else x.reaktionen.push({ emoji, anzahl: 1, meine: true });
    ereignisse.push({ typ: "nachricht_geaendert", unterhaltung: uid, nachricht: { ...x } });
    return ok();
  },
  async bearbeiten(uid, nid, text) { const x = (verlaeufe[uid] || []).find((m) => m.id === nid); Object.assign(x, { text, bearbeitet: true }); ereignisse.push({ typ: "nachricht_geaendert", unterhaltung: uid, nachricht: { ...x } }); return ok(); },
  async loeschen(uid, nid) { const x = (verlaeufe[uid] || []).find((m) => m.id === nid); Object.assign(x, { geloescht: true }); ereignisse.push({ typ: "nachricht_geaendert", unterhaltung: uid, nachricht: { ...x } }); return ok(); },
  async gelesen() { return ok(); },
  async tippt() { return ok(); },
  async entwurf_setzen() { return ok(); },
  async unterhaltung_info() { return ok({ medien: [{ vorschau: bild(["#2E7CF6", "#7FD3E6", "#FFC9B8"]) }, { vorschau: bild(["#FF8D28", "#FF2D55"]) }, { vorschau: bild(["#34C759", "#00C3D0"]) }], mitglieder: [] }); },
  async unterhaltung_setzen() { return ok(); },
  async suchen(uid, q) { return ok({ liste: (verlaeufe[uid] || []).filter((m) => m.text && m.text.toLowerCase().includes(q.toLowerCase())) }); },
  async kontakt_info(id) { const u = unterhaltungen.find((x) => x.id === id) || {}; return ok({ kontakt: { id, name: u.titel, farbe: u.farbe, verifiziert: !!u.verifiziert, schluessel_geaendert: !!u.schluessel_geaendert }, sicherheitsnummer: "05182 33910 77421 90264 11873 54602 82219 04471 39958 61027 44813 20596" }); },
  async kontakt_hinzufuegen(x) { return x.length < 10 ? { ok: false, fehler: "Das ist keine gültige ID." } : ok({ meldung: "Anfrage geschickt – sobald Lukas annimmt, könnt ihr schreiben." }); },
  async freundescode() { return ok({ code: "VP4C1-eyJ2IjoxLCJpZCI6IjdBQzVFLUhUTjRRIiwibmFtZSI6Ikxlb24ifQ" }); },
  async einladung() { return ok({ code: "VP4G2-AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywt" }); },
  async verfahren() { return ok({ liste: VERFAHREN }); },
  async text_verarbeiten(name, modus, text) { return ok({ ergebnis: modus === "ver" ? btoa(unescape(encodeURIComponent(text))).replace(/(.{64})/g, "$1\n") : text }); },
  async schluessel_erzeugen() { return ok({ schluessel: "q3J8yZ0b9mXo2N4wE1rT6uV7sP5kL0aH8gF3dS2cQ1w=" }); },
  async schluesselbund() { return ok({ liste: SCHLUESSELBUND }); },
  async obsidian_status() { return ok({ ordner: "C:\\Users\\Leon\\Documents\\Obsidian\\Claude", notiz: "VP4 Schlüssel.md" }); },
  async discord_status() { return ok({ verbunden: true, eingebaut: true, anzahl_kanaele: 3, meldung: "Eingebauter Zugang · Encrypt-Relay#4140" }); },
  async sicherheit_status() { return ok({ windows_verfuegbar: true }); },
  async daten_status() { return ok({ ordner: "C:\\Users\\Leon\\Downloads\\vp4_daten", belegt: "184 MB" }); },
  async einstellung_setzen(k, v) { einstellungen[k] = v; return ok(); },
  async profil_setzen(p) { Object.assign(ICH, p); return ok({ profil: ICH }); },
};

// Jeder Aufruf wird gezählt - so prüft ui_pruefung.py z. B., dass ein
// "gesperrt"-Ereignis die Oberfläche NICHT noch einmal sperren() rufen lässt.
export const api = new Proxy(echteApi, {
  get(ziel, name) {
    const fn = ziel[name];
    if (typeof fn !== "function") return fn;
    return (...args) => { aufrufe[name] = (aufrufe[name] || 0) + 1; return fn(...args); };
  },
});
