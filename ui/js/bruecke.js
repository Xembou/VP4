// =====================================================================
//  bruecke.js - der einzige Weg von der Oberfläche zu Python
// =====================================================================
//  Drei Betriebsarten, eine Schnittstelle:
//    - im Programm:         window.pywebview.api (pywebview)
//    - python VP4.py --browser:  HTTP an 127.0.0.1 (dev_server.py)
//    - ?demo=1:             erfundene Daten (nur für Screenshots)
//
//  Ereignisse kommen in allen drei Fällen auf dieselbe Art: die Seite
//  fragt alle 200 ms nach (ereignisse_holen). Kein evaluate_js aus
//  fremden Threads - das war in 4.x die Regel "nur der Hauptthread fasst
//  Widgets an", und hier gilt sie sinngemäss weiter.
// =====================================================================

const parameter = new URLSearchParams(location.search);
export const DEMO = parameter.has("demo");

let api = null;
const zuhoerer = new Map();
let abfrage = null;

function httpApi() {
  const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
  return new Proxy({}, {
    get: (_, name) => async (...args) => {
      const antwort = await fetch(`/api/${String(name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-VP4-Token": token },
        body: JSON.stringify(args),
      });
      if (!antwort.ok) return { ok: false, fehler: `Verbindung zu Python: HTTP ${antwort.status}` };
      return antwort.json();
    },
  });
}

export async function verbinden() {
  if (DEMO) {
    const demo = await import("./demo.js");
    api = demo.api;
    return;
  }
  if (window.pywebview?.api) { api = window.pywebview.api; return; }
  if (location.protocol.startsWith("http") && location.hash.includes("token=")) {
    api = httpApi();
    return;
  }
  // pywebview meldet sich, sobald die Brücke steht
  await new Promise((fertig) => {
    window.addEventListener("pywebviewready", fertig, { once: true });
    setTimeout(fertig, 4000);
  });
  if (window.pywebview?.api) { api = window.pywebview.api; return; }
  throw new Error("Keine Verbindung zu Python gefunden.");
}

/** Ruft eine Python-Funktion auf. Gibt IMMER ein Objekt mit ok zurück. */
export async function rufe(name, ...args) {
  try {
    const fn = api?.[name];
    if (typeof fn !== "function") return { ok: false, fehler: `Unbekannte Funktion: ${name}` };
    const ergebnis = await fn(...args);
    return ergebnis ?? { ok: false, fehler: "Keine Antwort." };
  } catch (e) {
    return { ok: false, fehler: String(e?.message || e) };
  }
}

export function auf(typ, fn) {
  if (!zuhoerer.has(typ)) zuhoerer.set(typ, new Set());
  zuhoerer.get(typ).add(fn);
  return () => zuhoerer.get(typ).delete(fn);
}

function verteilen(ereignis) {
  for (const fn of zuhoerer.get(ereignis.typ) || []) {
    try { fn(ereignis); } catch (e) { console.error(e); }
  }
  for (const fn of zuhoerer.get("*") || []) {
    try { fn(ereignis); } catch (e) { console.error(e); }
  }
}

export function ereignisseStarten() {
  if (abfrage) return;
  const runde = async () => {
    const r = await rufe("ereignisse_holen");
    if (r.ok) for (const e of r.liste || []) verteilen(e);
    abfrage = setTimeout(runde, 200);
  };
  runde();
}

export function ereignisseStoppen() {
  clearTimeout(abfrage);
  abfrage = null;
}
