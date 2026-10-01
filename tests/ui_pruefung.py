#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 ui_pruefung.py - die Oberfläche wirklich ansehen
=====================================================================
CLAUDE.md, Regel 2: Bei Oberflächenarbeit hinsehen. Zwei überlappende
Beschriftungen sind in 4.x genau so aufgefallen - in keinem Test.

Dieses Skript öffnet jede Ansicht im echten Chromium (Playwright, ohne
Fenster), hell und dunkel, schmal und breit, und

  - speichert Screenshots nach tests/screenshots/
  - meldet Fehler in der Konsole
  - meldet Texte, die sich überlappen
  - meldet waagrechtes Überlaufen
  - prüft, dass eine Nachricht mit HTML darin als TEXT erscheint

    python tests/ui_pruefung.py            # alles
    python tests/ui_pruefung.py chat       # nur Ansichten mit "chat" im Namen

Aus test_vp4.py heraus läuft dasselbe über pruefen_ui(R), sofern
Playwright installiert ist.
=====================================================================
"""

import functools
import http.server
import os
import socketserver
import sys
import threading
from pathlib import Path

ORDNER = Path(__file__).resolve().parent.parent
UI = ORDNER / "ui"
BILDER = Path(__file__).resolve().parent / "screenshots"

# Name -> (Abfrage, Aktion im Browser vor dem Screenshot)
ANSICHTEN = {
    "chat-dm": ("", "chat:MAXX2-0002A"),
    "chat-gruppe": ("", "chat:G-K7Q2M9PX"),
    "chat-kanal": ("", "community:G-10B00001"),
    "chat-schluessel-geaendert": ("", "chat:TIMM4-99KLA"),
    "chat-kontextmenue": ("", "kontextmenue:MAXX2-0002A"),
    "sicherheitsnummer": ("", "sicherheit:MAXX2-0002A"),
    "start-leer": ("", ""),
    "communities": ("", "bereich:communities"),
    "werkzeuge": ("", "werkzeug:text"),
    "werkzeug-dateien": ("", "werkzeug:dateien"),
    "einstellungen": ("", "einstellungen"),
    "einstellungen-erscheinung": ("", "einstellungen:erscheinung"),
    "einrichtung": ("&phase=einrichten", ""),
    "einrichtung-passwort": ("&phase=einrichten", "einrichtung:2"),
    "sperre": ("&phase=gesperrt", ""),
}


class _Leise(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def server_starten():
    handler = functools.partial(_Leise, directory=str(UI))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


AKTION_JS = r"""
async (aktion) => {
  const warte = (ms) => new Promise(r => setTimeout(r, ms));
  const klick = (sel) => { const el = document.querySelector(sel); if (!el) throw new Error('fehlt: ' + sel); el.click(); };
  const [art, wert] = aktion.split(':');
  if (art === 'chat' || art === 'kontextmenue' || art === 'sicherheit') {
    const zeilen = [...document.querySelectorAll('.chatzeile')];
    const ziel = zeilen.find(z => z.textContent.includes({'MAXX2-0002A':'Max','G-K7Q2M9PX':'Die Jungs','TIMM4-99KLA':'Tim'}[wert]));
    ziel.click(); await warte(500);
    if (art === 'kontextmenue') {
      const zeile = [...document.querySelectorAll('.zeile')].find(z => z.textContent.includes('Nächstes Mal'));
      const r = zeile.querySelector('.blase').getBoundingClientRect();
      zeile.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: r.left + 20, clientY: r.top + 10}));
      await warte(400);
    }
    if (art === 'sicherheit') { klick('.schutz'); await warte(600); }
  } else if (art === 'community') {
    klick('.segment [data-bereich="communities"]'); await warte(200);
    document.querySelector('.leiste-liste .chatzeile').click(); await warte(600);
  } else if (art === 'bereich') {
    klick(`.segment [data-bereich="${wert}"]`); await warte(300);
  } else if (art === 'werkzeug') {
    klick('.segment [data-bereich="werkzeuge"]'); await warte(200);
    const zeilen = [...document.querySelectorAll('.werkzeugzeile')];
    zeilen[{text:0, dateien:1, schluessel:2}[wert] ?? 0].click(); await warte(600);
  } else if (art === 'einstellungen') {
    klick('.leiste-fuss .rund'); await warte(300);
    if (wert) { [...document.querySelectorAll('.einstellungen nav button')].find(b => b.textContent.toLowerCase().startsWith(wert.slice(0,5))).click(); await warte(400); }
  } else if (art === 'einrichtung') {
    for (let i = 0; i < Number(wert); i++) {
      const feld = document.querySelector('.einrichtung input.feld');
      if (feld && !feld.value) { feld.value = 'Leon'; feld.dispatchEvent(new Event('input')); }
      [...document.querySelectorAll('.einrichtung .knopf.primaer')].pop().click(); await warte(450);
    }
  }
  await warte(500);
}
"""

PRUEF_JS = r"""
() => {
  const probleme = [];
  // Waagrechtes Überlaufen der ganzen Seite
  if (document.documentElement.scrollWidth > innerWidth + 1) probleme.push('Seite läuft waagrecht über');
  // Überlappende Texte: alle sichtbaren Textblätter (Elemente mit eigenem Text)
  const kaesten = [];
  const istSichtbar = (el) => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.aktionspille, .uhr, .menue, .tapete, svg, .reaktionen, .emojiwahl')) continue;
    const eigenerText = [...el.childNodes].some(k => k.nodeType === 3 && k.textContent.trim());
    if (!eigenerText || !istSichtbar(el)) continue;
    // Nur der Teil, der im scrollenden Vorfahren sichtbar ist
    let r = el.getBoundingClientRect();
    let p = el.parentElement, verdeckt = false;
    while (p) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden)/.test(s.overflow + s.overflowY + s.overflowX)) {
        const q = p.getBoundingClientRect();
        if (r.bottom <= q.top || r.top >= q.bottom || r.right <= q.left || r.left >= q.right) { verdeckt = true; break; }
      }
      p = p.parentElement;
    }
    if (verdeckt) continue;
    kaesten.push({ el, r, ebene: el.closest('.blatt, .abdunkler, .chat-leiste, .eingabe, .seitenleiste, .infobereich') });
  }
  for (let i = 0; i < kaesten.length; i++) {
    for (let j = i + 1; j < kaesten.length; j++) {
      const a = kaesten[i], b = kaesten[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (a.ebene !== b.ebene) continue;   // Glas über Verlauf ist Absicht
      const x = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
      const y = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      if (x > 3 && y > 3) {
        probleme.push(`Text überlappt: "${a.el.textContent.trim().slice(0, 30)}" / "${b.el.textContent.trim().slice(0, 30)}"`);
        if (probleme.length > 12) return probleme;
      }
    }
  }
  // Text, der aus seinem Kasten herausragt (abgeschnitten ohne Ellipse)
  for (const { el } of kaesten) {
    const s = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 2 && s.textOverflow !== 'ellipsis' && s.overflowX === 'visible' && s.whiteSpace === 'nowrap') {
      probleme.push(`Text ragt heraus: "${el.textContent.trim().slice(0, 40)}"`);
    }
  }
  return probleme;
}
"""


def pruefen(filter_text="", ausgeben=print):
    """Läuft alle Ansichten durch. Gibt (anzahl_ok, fehlerliste) zurück."""
    from playwright.sync_api import sync_playwright

    BILDER.mkdir(exist_ok=True)
    httpd = server_starten()
    basis = f"http://127.0.0.1:{httpd.server_address[1]}/index.html?demo=1"
    ok, fehler = 0, []
    pfad = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    try:
        with sync_playwright() as p:
            optionen = {"executable_path": pfad} if os.path.exists(pfad) else {}
            browser = p.chromium.launch(**optionen)
            for name, (abfrage, aktion) in ANSICHTEN.items():
                if filter_text and filter_text not in name:
                    continue
                for design in ("light", "dark"):
                    for breite in (1440, 980):
                        if breite == 980 and design == "dark":
                            continue
                        seite = browser.new_page(viewport={"width": breite, "height": 860}, device_scale_factor=1)
                        meldungen = []
                        seite.on("console", lambda m: m.type == "error" and meldungen.append(m.text))
                        seite.on("pageerror", lambda e: meldungen.append(str(e)))
                        seite.goto(f"{basis}&design={design}{abfrage}")
                        seite.wait_for_timeout(700)
                        if aktion:
                            try:
                                seite.evaluate(AKTION_JS, aktion)
                            except Exception as e:
                                meldungen.append(f"Aktion {aktion}: {e}")
                        seite.wait_for_timeout(300)
                        datei = BILDER / f"{name}-{design}-{breite}.png"
                        seite.screenshot(path=str(datei))
                        probleme = meldungen + seite.evaluate(PRUEF_JS)
                        kennung = f"{name} ({design}, {breite}px)"
                        if probleme:
                            fehler.append((kennung, probleme))
                            ausgeben(f"  [FEHL] {kennung}")
                            for x in probleme[:8]:
                                ausgeben(f"         {x}")
                        else:
                            ok += 1
                            ausgeben(f"  [OK]   {kennung}")
                        seite.close()
            browser.close()
    finally:
        httpd.shutdown()
    return ok, fehler




# =====================================================================
#  Echter Durchlauf: zwei Personen, echte Dienste, echte Oberfläche
# =====================================================================

def zwei_personen(ausgeben=print):
    """Einrichten, Freund hinzufügen, annehmen, schreiben, antworten,
    reagieren - alles durch Klicks in der echten Oberfläche, mit zwei
    echten VP4-Diensten dahinter (verbunden über ein Spielzeug-Netz)."""
    import tempfile
    sys.path.insert(0, str(ORDNER))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from playwright.sync_api import sync_playwright
    import dev_server
    from api import VP4Api
    from dienst import VP4Dienst
    from kern.tresor import DPAPIAttrappe
    from test_dienst import SpielNetz

    BILDER.mkdir(exist_ok=True)
    probleme = []
    with tempfile.TemporaryDirectory() as tmp:
        netz = SpielNetz()
        dienste = [VP4Dienst(Path(tmp) / n, dpapi=DPAPIAttrappe(), netz_fabrik=netz.fabrik, update_pruefen=False)
                   for n in ("lena", "tom")]
        server = [dev_server.starten(VP4Api(d), browser_oeffnen=False) for d in dienste]
        pfad = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(**({"executable_path": pfad} if os.path.exists(pfad) else {}))
                seiten = []
                for (_, adresse), name in zip(server, ("Lena", "Tom")):
                    kontext = browser.new_context(viewport={"width": 1280, "height": 820})
                    kontext.grant_permissions(["clipboard-read", "clipboard-write"])
                    s = kontext.new_page()
                    s.on("pageerror", lambda e, n=name: probleme.append(f"{n}: {e}"))
                    s.on("console", lambda m, n=name: m.type == "error" and probleme.append(f"{n}: {m.text}"))
                    s.goto(adresse)
                    s.wait_for_selector(".einrichtung")
                    s.click("text=Los geht’s")
                    s.fill(".einrichtung input.feld", name)
                    s.click(".einrichtung .knopf.primaer")
                    felder = s.locator(".einrichtung input[type=password]")
                    felder.nth(0).fill("Ein-gutes-Passwort-1")
                    felder.nth(1).fill("Ein-gutes-Passwort-1")
                    s.check(".einrichtung input[type=checkbox] >> nth=0")
                    s.click(".einrichtung .knopf.primaer")
                    s.wait_for_selector(".id-gross")
                    if name == "Lena":
                        s.screenshot(path=str(BILDER / "echt-einrichtung-id.png"))
                    s.click("text=Fertig")
                    s.wait_for_selector(".seitenleiste")
                    seiten.append(s)
                lena, tom = seiten
                tom_id = dienste[1].profil()["id"]
                lena.click(".leiste-titelzeile .rund")
                lena.click("text=Freund hinzufügen")
                lena.fill(".blatt input.feld", tom_id)
                lena.click(".blatt >> text=Anfrage senden")
                tom.wait_for_selector("text=Anfragen", timeout=15000)
                tom.click(".chatzeile >> text=Anfragen")
                tom.click(".blatt >> text=Annehmen")
                tom.keyboard.press("Escape")
                lena.wait_for_selector(".chatzeile >> text=Tom", timeout=15000)
                lena.click(".chatzeile >> text=Tom")
                lena.fill(".eingabe textarea", "Hey Tom! Schon das neue VP4 gesehen? 🔒")
                lena.keyboard.press("Enter")
                tom.wait_for_selector(".chatzeile >> text=Lena", timeout=15000)
                tom.click(".chatzeile >> text=Lena")
                tom.wait_for_selector("text=Schon das neue VP4 gesehen", timeout=15000)
                tom.fill(".eingabe textarea", "Ja, sieht richtig gut aus!")
                tom.keyboard.press("Enter")
                lena.wait_for_selector("text=sieht richtig gut aus", timeout=15000)
                # Reaktion per Doppelklick und eine Antwort
                lena.dblclick(".zeile:not(.ich) .blase >> text=sieht richtig gut aus")
                tom.wait_for_selector(".reaktion", timeout=15000)
                lena.fill(".eingabe textarea", "<img src=x onerror=alert(1)> bleibt Text")
                lena.keyboard.press("Enter")
                tom.wait_for_selector("text=bleibt Text", timeout=15000)
                if tom.locator(".verlauf img[src='x']").count():
                    probleme.append("HTML aus einer Nachricht wurde als HTML eingesetzt!")
                tom.wait_for_timeout(600)
                lena.wait_for_timeout(600)
                lena.screenshot(path=str(BILDER / "echt-chat-lena.png"))
                tom.screenshot(path=str(BILDER / "echt-chat-tom.png"))
                browser.close()
        except Exception as e:
            probleme.append(f"Durchlauf abgebrochen: {type(e).__name__}: {e}")
        finally:
            for srv, _ in server:
                srv.shutdown()
            for d in dienste:
                d.beenden()
    for x in probleme:
        ausgeben(f"  [FEHL] {x}")
    if not probleme:
        ausgeben("  [OK]   Zwei Personen: einrichten, verbinden, schreiben, reagieren - alles über die Oberfläche")
    return probleme


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "echt":
        sys.exit(1 if zwei_personen() else 0)
    anzahl, fehler = pruefen(sys.argv[1] if len(sys.argv) > 1 else "")
    print(f"\n{anzahl} Ansichten ohne Befund, {len(fehler)} mit Befund. Bilder: {BILDER}")
    sys.exit(1 if fehler else 0)
