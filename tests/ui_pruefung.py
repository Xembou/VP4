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
  - misst den Kontrast jedes sichtbaren Textes auf seinem echten
    Hintergrund (4.5:1, grosser Text 3:1) - wo der Hintergrund Glas, ein
    Bild oder ein Verlauf ist, wird NICHT geraten, sondern übersprungen
  - misst Füllung/Schrift jeder Akzentfarbe in Hell und Dunkel
  - sucht Farbwerte, Radien und Dauern ausserhalb von tokens.css und
    Symbole, die es im Sprite nicht gibt
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
# Eine Aktion darf aus mehreren Schritten bestehen, getrennt mit "|".
# Schritte, die echte Maus/Tastatur brauchen (Hover, Fokusring), macht
# Python: "hover:<css>", "taste:<Taste>", "tippen:<Text>".
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
    # Neu im Feinschliff
    "chat-hover": ("", "chat:MAXX2-0002A|hover:.zeile:not(.ich) .blase >> text=Hast du das Video"),
    "chat-mitteilung": ("", "chat:MAXX2-0002A|mitteilung"),
    "schnellwahl": ("", "schnellwahl:"),
    "schnellwahl-suche": ("", "schnellwahl:ma|taste:ArrowDown"),
    "community-einstellungen": ("", "community-einstellungen"),
    "leiste-tastatur": ("", "suchfeld|taste:ArrowDown|taste:ArrowDown"),
    "sperre-automatisch": ("", "autosperre"),
    "chat-dm-akzent-gruen": ("&farbe=gruen", "chat:MAXX2-0002A"),
    "chat-dm-akzent-orange": ("&farbe=orange", "chat:MAXX2-0002A"),
    "bewegung-reduziert": ("", "chat:MAXX2-0002A|ruhig"),
    # Ohne Transparenz ist das Glas massiv - dann lässt sich auch der Text
    # in Seitenleiste, Leisten und Menüs messen (sonst übersprungen)
    "chat-nach-unten": ("", "chat:MAXX2-0002A|nach-unten"),
    "datei-warnung": ("", "chat:TIMM4-99KLA|datei-warnung"),
    "kanal-kontextmenue": ("", "community:G-10B00001|kanal-menue"),
    "solide-chat": ("", "solide|chat:MAXX2-0002A"),
    "solide-kontextmenue": ("", "solide|kontextmenue:MAXX2-0002A"),
    "solide-community": ("", "solide|community:G-10B00001"),
}

# Diese Ansichten nur hell und breit - sie prüfen Verhalten, nicht Optik
NUR_BREIT = {"solide-chat", "solide-kontextmenue", "solide-community"}
NUR_EINMAL = {"chat-nach-unten", "datei-warnung", "sperre-automatisch", "chat-mitteilung", "chat-dm-akzent-gruen", "chat-dm-akzent-orange", "bewegung-reduziert", "leiste-tastatur"}


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
  } else if (art === 'schnellwahl') {
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true}));
    await warte(250);
    const feld = document.querySelector('.schnellwahl input');
    if (!feld) throw new Error('Schnellwahl ging nicht auf');
    if (wert) { feld.value = wert; feld.dispatchEvent(new Event('input')); }
    if (document.activeElement !== feld) throw new Error('Schnellwahl: Fokus nicht im Suchfeld');
  } else if (art === 'community-einstellungen') {
    klick('.segment [data-bereich="communities"]'); await warte(200);
    document.querySelector('.leiste-liste .chatzeile').click(); await warte(500);
    klick('.community-held .rund'); await warte(250);
    const eintrag = [...document.querySelectorAll('.menue button')].find(b => b.textContent.includes('Einstellungen'));
    if (!eintrag) throw new Error('Menüeintrag "Einstellungen …" fehlt');
    eintrag.click(); await warte(700);
    if (!document.querySelector('.community-einstellungen .eintrag .avatar')) throw new Error('Mitglieder fehlen im Blatt');
  } else if (art === 'nach-unten') {
    // Hochscrollen, dann kommt etwas Neues: der Knopf zeigt, wie viel
    const v = document.querySelector('.verlauf');
    v.scrollTop = 0; v.dispatchEvent(new Event('scroll')); await warte(300);
    window.vp4Demo.ereignis({ typ: 'nachricht_neu', unterhaltung: 'MAXX2-0002A', vorschau: 'Bist du noch da?',
      nachricht: { id: 'neu-1', von: 'max', von_name: 'Max', von_farbe: '#34C759', ich: false, ts: Date.now(), art: 'text', text: 'Bist du noch da?', reaktionen: [] } });
    await warte(700);
    const zahl = document.querySelector('.nach-unten .zaehler');
    if (!zahl || zahl.textContent !== '1') throw new Error('Nach-unten-Knopf zeigt die neue Nachricht nicht an');
  } else if (art === 'datei-warnung') {
    const karte = [...document.querySelectorAll('.dateikarte')].find(k => k.textContent.includes('.exe'));
    karte.click(); await warte(600);
    if (!document.querySelector('.blatt h2')?.textContent.includes('wirklich öffnen')) throw new Error('Keine Rückfrage vor einem Programm');
  } else if (art === 'kanal-menue') {
    const zeile = [...document.querySelectorAll('.chatzeile.kanal')].find(z => z.textContent.includes('memes'));
    const r = zeile.getBoundingClientRect();
    zeile.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: r.left + 60, clientY: r.top + 20 }));
    await warte(400);
    const texte = [...document.querySelectorAll('.menue button')].map(b => b.textContent);
    if (!texte.some(t => t.includes('Umbenennen'))) throw new Error('Kanal-Menü für Admins fehlt');
  } else if (art === 'solide') {
    document.documentElement.dataset.transparenz = 'aus';
  } else if (art === 'suchfeld') {
    document.querySelector('.suche input').focus();
  } else if (art === 'mitteilung') {
    window.vp4Demo.ereignis({ typ: 'mitteilung', titel: 'Die Jungs', text: 'Jonas: Wer ist heute dabei?', unterhaltung: 'G-K7Q2M9PX' });
    // eine Mitteilung für den offenen Chat darf KEINEN Hinweis machen
    window.vp4Demo.ereignis({ typ: 'mitteilung', titel: 'Max', text: 'nicht anzeigen', unterhaltung: 'MAXX2-0002A' });
    await warte(700);
    const texte = [...document.querySelectorAll('.toast')].map(t => t.textContent);
    if (!texte.some(t => t.includes('Wer ist heute dabei'))) throw new Error('Mitteilung kam nicht als Hinweis');
    if (document.hasFocus() && texte.some(t => t.includes('nicht anzeigen'))) throw new Error('Mitteilung für den offenen Chat wurde angezeigt');
  } else if (art === 'autosperre') {
    // Befund aus dem Code-Review: "gesperrt" rief sperren() noch einmal -
    // nach dem Entsperren sperrte sich VP4 sofort wieder selbst.
    window.vp4Demo.ereignis({ typ: 'gesperrt' });
    await warte(700);
    if (!document.querySelector('.einrichtung.sperre')) throw new Error('Sperrbildschirm kam nicht');
    const feld = document.querySelector('.einrichtung input[type=password]');
    feld.value = 'richtig'; feld.dispatchEvent(new Event('input'));
    document.querySelector('.einrichtung .knopf.primaer').click();
    await warte(1500);
    if (document.querySelector('.einrichtung.sperre')) throw new Error('Nach dem Entsperren wieder gesperrt');
    if (document.getElementById('app').classList.contains('versteckt')) throw new Error('Oberfläche nach dem Entsperren nicht da');
    if (window.vp4Demo.aufrufe.sperren) throw new Error(`"gesperrt" hat sperren() gerufen (${window.vp4Demo.aufrufe.sperren}x)`);
  } else if (art === 'ruhig') {
    document.documentElement.dataset.bewegung = 'aus';
    const s = getComputedStyle(document.querySelector('.blase'));
    if (s.animationName !== 'einblenden') throw new Error('Bewegung reduzieren: Blasen bewegen sich noch (' + s.animationName + ')');
    if (getComputedStyle(document.querySelector('.tapete')).animationName !== 'none') throw new Error('Bewegung reduzieren: Tapete treibt noch');
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
  probleme.push(...window.__vp4Kontrast());
  // Bedienelemente ohne Namen: ein Screenreader sagt dann nur "Schalter"
  for (const el of document.querySelectorAll('button, [role="button"], [role="option"], [role="switch"], [role="menuitem"], [role="tab"], input, textarea')) {
    if (!istSichtbar(el) || el.type === 'checkbox' && el.closest('label')) continue;
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || el.getAttribute('placeholder') || '').trim()
      || (el.getAttribute('aria-labelledby') && document.getElementById(el.getAttribute('aria-labelledby'))?.textContent.trim())
      || (el.closest('label') && el.closest('label').textContent.trim());
    if (!name) probleme.push(`Bedienelement ohne Namen: <${el.tagName.toLowerCase()} class="${el.className}">`);
  }
  return probleme;
}
"""


# ---------------------------------------------------------------------
#  Kontrast (WCAG 2): Text gegen seinen ECHTEN Hintergrund
# ---------------------------------------------------------------------
# Der Hintergrund wird nicht über die Vorfahren geraten, sondern an der
# Stelle des Textes abgefragt (elementsFromPoint) - so zählen auch
# Flächen, die nur darunter liegen. Wo darunter Glas (backdrop-filter),
# ein Bild, ein Verlauf oder ein Filter liegt, lässt sich die Farbe nicht
# bestimmen: dann wird übersprungen, nie geraten. Halbdurchsichtige
# Flächen werden übereinander gerechnet; bleibt am Ende mehr als 20 %
# unbestimmt (die Tapete unter der Hauptfläche), wird ebenfalls
# übersprungen, bis 20 % zählt der Seitengrund als Näherung.
KONTRAST_JS = r"""
() => {
  const zahl = (s) => {
    if (!s) return null;
    let m = s.match(/^rgba?\(([^)]+)\)/);
    if (m) {
      const t = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
      return [t[0], t[1], t[2], t.length > 3 ? t[3] : 1];
    }
    m = s.match(/^color\(srgb ([^)]+)\)/);
    if (m) {
      const t = m[1].split(/[\s\/]+/).filter(Boolean).map(Number);
      return [t[0] * 255, t[1] * 255, t[2] * 255, t.length > 3 ? t[3] : 1];
    }
    return undefined;     // eine Farbe, die wir nicht lesen können
  };
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const hell = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ueber = (oben, unten) => { const a = oben[3]; return [0, 1, 2].map(i => oben[i] * a + unten[i] * (1 - a)).concat(1); };
  const verhaeltnis = (a, b) => { const x = hell(a), y = hell(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => '#' + c.slice(0, 3).map(x => Math.round(x).toString(16).padStart(2, '0')).join('');

  const grund = zahl(getComputedStyle(document.body).backgroundColor);
  window.__vp4Grund = (el) => {
    const r = el.getBoundingClientRect();
    const x = Math.min(innerWidth - 1, Math.max(0, r.left + Math.min(r.width / 2, 24)));
    const y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
    const stapel = document.elementsFromPoint(x, y).filter(e => !el.contains(e) || e === el);
    if (!stapel.length) return null;
    // Liegt etwas Fremdes ÜBER dem Text (z. B. Glas, unter dem der Verlauf
    // durchscrollt), ist der Text an dieser Stelle gar nicht zu sehen.
    if (!(stapel[0] === el || stapel[0].contains(el))) return null;
    // Abgeschnitten (z. B. unten aus einer scrollenden Liste heraus): dann
    // liegt an der Stelle gar nicht der Text, sondern was dahinter ist.
    if (!stapel.includes(el) && getComputedStyle(el).pointerEvents !== 'none') return null;
    const schichten = [];
    let rest = 1;                 // was von unten noch durchscheint
    for (const e of stapel) {
      if (e.contains(el) && e !== el && e.closest('svg')) continue;
      const s = getComputedStyle(e);
      const unklar = ['IMG', 'VIDEO', 'CANVAS', 'IFRAME'].includes(e.tagName)
        || (s.backdropFilter && s.backdropFilter !== 'none') || (s.webkitBackdropFilter && s.webkitBackdropFilter !== 'none')
        || s.backgroundImage !== 'none' || s.filter !== 'none' || s.mixBlendMode !== 'normal'
        || (Number(s.opacity) < 1 && !e.contains(el));
      // Glas, Bild, Verlauf: nur wenn darüber schon fast alles deckt (die
      // Hauptfläche über der Tapete), zählt der Rest als Seitengrund.
      if (unklar) { if (rest <= 0.2 + 1e-6) break; return null; }
      const c = zahl(s.backgroundColor);
      if (c === undefined) return null;
      if (c && c[3] > 0) { schichten.push(c); rest *= (1 - c[3]); if (c[3] >= 0.999) break; }
    }
    if (rest > 0.2 + 1e-6) return null;
    let farbe = schichten.length && schichten[schichten.length - 1][3] >= 0.999 ? schichten.pop() : grund;
    if (!farbe || farbe[3] < 0.999) return null;
    for (let i = schichten.length - 1; i >= 0; i--) farbe = ueber(schichten[i], farbe);
    return farbe;
  };

  window.__vp4Kontrast = () => {
    const probleme = [];
    const gesehen = new Set();
    const sichtbar = (el) => {
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none') return false;
      const r = el.getBoundingClientRect();
      return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
    };
    const elemente = [...document.querySelectorAll('body *')].filter(el =>
      [...el.childNodes].some(k => k.nodeType === 3 && k.textContent.trim()) && sichtbar(el));
    for (const el of elemente) {
      if (el.closest('.tapete, svg, .nur-vorlesen, [aria-hidden="true"] .avatar, .avatar, .nur-emoji, .reaktion .emoji, kbd.deko')) continue;
      if (el.closest(':disabled, [aria-disabled="true"]')) continue;     // WCAG: abgeschaltetes zählt nicht
      const text = [...el.childNodes].filter(k => k.nodeType === 3).map(k => k.textContent).join('').trim();
      if (!/[\p{L}\p{N}]/u.test(text)) continue;                           // nur Emoji, Pfeile, Punkte
      const s = getComputedStyle(el);
      let vorne = zahl(s.color);
      if (!vorne) continue;
      // Deckkraft der Vorfahren bis zur nächsten Fläche wirkt auf den Text
      let deck = 1;
      for (let e = el; e; e = e.parentElement) deck *= Number(getComputedStyle(e).opacity);
      if (deck < 0.999) { if (deck < 0.3) continue; vorne = [vorne[0], vorne[1], vorne[2], vorne[3] * deck]; }
      const hinten = window.__vp4Grund(el);
      if (!hinten) continue;
      const wirklich = ueber(vorne, hinten);
      const k = verhaeltnis(wirklich, hinten);
      const groesse = parseFloat(s.fontSize), dick = Number(s.fontWeight) >= 700;
      const grenze = (groesse >= 24 || (groesse >= 18.66 && dick)) ? 3 : 4.5;
      if (k + 0.005 < grenze) {
        const kennung = `${text.slice(0, 28)}|${hex(wirklich)}|${hex(hinten)}`;
        if (gesehen.has(kennung)) continue;
        gesehen.add(kennung);
        probleme.push(`Kontrast ${k.toFixed(2)}:1 < ${grenze}:1: "${text.slice(0, 28)}" (${hex(wirklich)} auf ${hex(hinten)}, ${el.className || el.tagName.toLowerCase()})`);
        if (probleme.length > 10) break;
      }
    }
    return probleme;
  };
}
"""

# Jede Akzentfarbe in Hell und Dunkel: Schrift auf der Füllung, der
# Akzent als Schrift auf den Flächen, eigene Sprechblase.
AKZENT_JS = r"""
async () => {
  const { AKZENTE } = await import('./js/zustand.js');
  const wurzel = document.documentElement;
  const vorher = { theme: wurzel.dataset.theme, akzent: wurzel.dataset.akzent };
  const probe = (vorne, hinten, unter = null) => {
    const aussen = document.createElement('div');
    for (const [k, v] of [['position', 'fixed'], ['left', '0'], ['top', '0'], ['z-index', '9999'], ['padding', '8px'], ['font', '20px sans-serif']]) aussen.style.setProperty(k, v);
    aussen.style.setProperty('background-color', unter || 'var(--flaeche)');
    const innen = document.createElement('span');
    innen.style.setProperty('color', vorne);
    innen.style.setProperty('background-color', hinten);
    innen.textContent = 'Probe';
    aussen.append(innen);
    document.body.append(aussen);
    return [aussen, innen];
  };
  const paare = [
    ['var(--akzent-text)', 'var(--akzent-fuellung)', 'Schrift auf Füllung'],
    ['var(--akzent-text-weich)', 'var(--akzent-fuellung)', 'Nebenschrift auf Füllung'],
    ['var(--blase-ich-text)', 'var(--blase-ich)', 'eigene Sprechblase'],
    ['var(--akzent-schrift)', 'var(--flaeche)', 'Akzentschrift auf Fläche'],
    ['var(--akzent-schrift)', 'var(--grund)', 'Akzentschrift auf Grund'],
    ['var(--akzent-schrift)', 'var(--akzent-weich)', 'Akzentschrift auf Akzent-Tönung'],
  ];
  const probleme = [];
  for (const theme of ['light', 'dark']) {
    for (const id of Object.keys(AKZENTE)) {
      wurzel.dataset.theme = theme;
      wurzel.dataset.akzent = id;
      for (const [vorne, hinten, name] of paare) {
        const [aussen, innen] = probe(vorne, hinten);
        const hintergrund = window.__vp4Grund(innen);
        const s = getComputedStyle(innen);
        const m = s.color.match(/[\d.]+/g).map(Number);
        const v = s.color.startsWith('color(') ? [m[0] * 255, m[1] * 255, m[2] * 255, m[3] ?? 1] : [m[0], m[1], m[2], m[3] ?? 1];
        aussen.remove();
        if (!hintergrund) { probleme.push(`Akzent ${id} (${theme}): ${name} nicht messbar`); continue; }
        const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
        const L = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
        const echt = [0, 1, 2].map(i => v[i] * v[3] + hintergrund[i] * (1 - v[3]));
        const a = L(echt), b = L(hintergrund);
        const k = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        if (k < 4.5) probleme.push(`Akzent ${id} (${theme}): ${name} nur ${k.toFixed(2)}:1`);
      }
    }
  }
  wurzel.dataset.theme = vorher.theme;
  wurzel.dataset.akzent = vorher.akzent;
  return probleme;
}
"""


def statisch_pruefen():
    """Werte ausserhalb von tokens.css, unbekannte Symbole."""
    import re
    probleme = []
    farbe = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")
    radius = re.compile(r"border(?:-[a-z]+)*-radius\s*:\s*[^;]*\b\d+(?:\.\d+)?px")
    dauer = re.compile(r"(?:transition|animation)[a-z-]*\s*:[^;]*?(?<![\w-])\.?\d+(?:\.\d+)?m?s\b")
    for datei in sorted((UI / "css").glob("*.css")):
        if datei.name == "tokens.css":
            continue
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            ohne_url = re.sub(r'url\("[^"]*"\)', "", zeile)
            if farbe.search(ohne_url):
                probleme.append(f"{datei.name}:{nr}: Farbwert ausserhalb von tokens.css")
            if radius.search(ohne_url):
                probleme.append(f"{datei.name}:{nr}: Radius ausserhalb von tokens.css")
            if dauer.search(ohne_url):
                probleme.append(f"{datei.name}:{nr}: Dauer ausserhalb von tokens.css")
    sprite = (UI / "icons" / "sprite.svg").read_text(encoding="utf-8")
    vorhanden = set(re.findall(r'id="([a-z0-9-]+)"', sprite))
    benutzt = set()
    for datei in (UI / "js").rglob("*.js"):
        text = datei.read_text(encoding="utf-8")
        benutzt |= set(re.findall(r'\bic\("([a-z0-9-]+)"', text))
        benutzt |= set(re.findall(r'\bsymbol:\s*"([a-z0-9-]+)"', text))
        benutzt |= set(re.findall(r'\brundknopf\("([a-z0-9-]+)"', text))
    for name in sorted(benutzt - vorhanden):
        probleme.append(f"Symbol \"{name}\" fehlt in ui/icons/sprite.svg")
    return probleme


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
            if not filter_text or filter_text == "statisch":
                befunde = statisch_pruefen()
                if befunde:
                    fehler.append(("statisch", befunde))
                    ausgeben("  [FEHL] Werte und Symbole")
                    for x in befunde[:12]:
                        ausgeben(f"         {x}")
                else:
                    ok += 1
                    ausgeben("  [OK]   Werte nur in tokens.css, alle Symbole im Sprite")
            if not filter_text or filter_text == "akzente":
                seite = browser.new_page(viewport={"width": 1000, "height": 700})
                seite.goto(f"{basis}&design=light")
                seite.wait_for_timeout(600)
                seite.evaluate(KONTRAST_JS)
                befunde = seite.evaluate(AKZENT_JS)
                seite.close()
                if befunde:
                    fehler.append(("akzente", befunde))
                    ausgeben("  [FEHL] Akzentfarben")
                    for x in befunde[:12]:
                        ausgeben(f"         {x}")
                else:
                    ok += 1
                    ausgeben("  [OK]   Alle 8 Akzentfarben schaffen 4.5:1 (hell und dunkel)")
            for name, (abfrage, aktion) in ANSICHTEN.items():
                if filter_text and filter_text not in name:
                    continue
                for design in ("light", "dark"):
                    for breite in (1440, 980):
                        if breite == 980 and design == "dark":
                            continue
                        if name in NUR_EINMAL and (design, breite) != ("light", 1440):
                            continue
                        if name in NUR_BREIT and breite != 1440:
                            continue
                        seite = browser.new_page(viewport={"width": breite, "height": 860}, device_scale_factor=1)
                        meldungen = []
                        seite.on("console", lambda m: m.type == "error" and meldungen.append(m.text))
                        seite.on("pageerror", lambda e: meldungen.append(str(e)))
                        seite.goto(f"{basis}&design={design}{abfrage}")
                        seite.wait_for_timeout(700)
                        for schritt in [x for x in aktion.split("|") if x]:
                            try:
                                art, _, wert = schritt.partition(":")
                                if art == "hover":
                                    seite.hover(wert)
                                elif art == "taste":
                                    seite.keyboard.press(wert)
                                elif art == "tippen":
                                    seite.keyboard.type(wert)
                                else:
                                    seite.evaluate(AKTION_JS, schritt)
                            except Exception as e:
                                meldungen.append(f"Aktion {schritt}: {e}")
                        seite.wait_for_timeout(300)
                        datei = BILDER / f"{name}-{design}-{breite}.png"
                        seite.screenshot(path=str(datei))
                        seite.evaluate(KONTRAST_JS)
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
