#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 VP4 5 - verschlüsselter Chat mit Werkzeugkasten
=====================================================================
Starten mit:      python VP4.py
Im Browser:       python VP4.py --browser    (zum Entwickeln)

Was das Programm kann:

  1) Chatten mit Freunden - Ende-zu-Ende verschlüsselt. Jede
     Installation hat eine ID, die aus ihren eigenen Schlüsseln
     berechnet wird. Freund hinzufügen, er nimmt an, fertig: Die
     Schlüssel tauschen sich von selbst aus.
  2) Gruppen und Communities mit Kanälen wie bei Discord - beitreten
     per Code.
  3) Bilder, Videos, Dateien und Sprachnachrichten, Antworten,
     Reaktionen, Bearbeiten und Löschen.
  4) Der Weg: im selben WLAN direkt, sonst über einen Discord-Kanal.
     Discord bekommt dabei nur Geheimtext zu sehen.
  5) Werkzeuge: Texte, Dateien und Ordner verschlüsseln (auch
     Post-Quanten und age), Schlüsselbund, Signaturen, Prüfsummen,
     Obsidian.

Zur Laufzeit wird keine KI gebraucht, und es gibt keinen eigenen Server.


DIE DATEIEN
------------
  VP4.py         diese Datei - prüft die Pakete und öffnet das Fenster
  api.py         was die Oberfläche von Python wollen darf
  dienst.py      Tresor, Datenbank, Bote, Netz, Versand
  ereignisse.py  Warteschlange zur Oberfläche, Zeitplaner
  kern/          die reine Logik (Verschlüsselung, Tresor, Nachrichten)
  netz/          WLAN, Discord, Update-Prüfung
  ui/            die Oberfläche (HTML/CSS/JavaScript, ohne Build-Schritt)
  test_vp4.py    Selbsttest - nach Änderungen ausführen!


EHRLICHE HINWEISE
------------------
 - Ein privates Hobby-Projekt, kein geprüftes Sicherheitsprodukt.
 - Geschützt ist der INHALT jeder Nachricht. Wer wann wem schreibt,
   steht offen im Discord-Kanal. Und noch ohne Forward Secrecy: Wer
   später dein Gerät knackt, kann auch alte Nachrichten öffnen.
 - Das Master-Passwort hat keine Wiederherstellung. Windows kann es
   sich auf Wunsch merken (an dein Benutzerkonto gebunden).
 - Der eingebaute Discord-Zugang steckt in der .exe und lässt sich
   herausholen. Nachrichten bleiben trotzdem geheim; stören könnte man
   den Bot aber. Dann hilft eine neue Version von GitHub.
=====================================================================
"""

import logging
import os
import sys
from pathlib import Path


def _meldung(titel: str, text: str):
    """Eine Meldung, die man auch ohne Konsole sieht (die .exe hat keine)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, titel, 0x10)
            return
        except Exception:
            pass
    print(f"{titel}\n\n{text}", file=sys.stderr)


def _pakete_pruefen():
    fehlt = []
    for modul, paket in (("cryptography", "cryptography"), ("argon2", "argon2-cffi"),
                         ("webview", "pywebview")):
        try:
            __import__(modul)
        except ImportError:
            fehlt.append(paket)
    if fehlt:
        _meldung("Ein Paket fehlt",
                 "VP4 braucht noch: " + ", ".join(fehlt) + "\n\nBitte einmalig ausführen:\n\n"
                 "    pip install -r requirements.txt")
        sys.exit(1)


def _ui_ordner() -> Path:
    # In der .exe entpackt PyInstaller alles nach sys._MEIPASS
    basis = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return basis / "ui"


def _protokoll_einrichten():
    from kern import speicher
    try:
        speicher.DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(speicher.DATA_DIR / "vp4.log"), level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
    except OSError:
        logging.basicConfig(level=logging.INFO)


def _fenster_stil(fenster, dunkel: bool):
    """Windows 11: dunkle Titelleiste und Mica-Hintergrund passend zur Seite."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = fenster.native.Handle.ToInt64()
        wert = ctypes.c_int(1 if dunkel else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(wert), ctypes.sizeof(wert))
        mica = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(mica), ctypes.sizeof(mica))
    except Exception:
        logging.getLogger("vp4").debug("Fensterstil ging nicht", exc_info=True)


def _selbsttest(fenster):
    """Für bauen.py und den Windows-Workflow: Fenster öffnen, nachsehen, ob die
    Oberfläche wirklich steht, Ergebnis in eine Datei schreiben, schliessen.

    "Die .exe läuft noch nach 9 Sekunden" sagt nicht, ob man etwas sieht -
    eine weisse Seite mit einem Skriptfehler läuft auch."""
    import json
    import threading
    import time

    def pruefen():
        ergebnis = {"ok": False, "fehler": "Zeit abgelaufen"}
        for _ in range(30):
            time.sleep(0.5)
            try:
                if fenster.evaluate_js("document.querySelector('.einrichtung, .seitenleiste, .vollbild .einrichtung') !== null"):
                    ergebnis = {"ok": True}
                    break
            except Exception as e:
                ergebnis = {"ok": False, "fehler": f"{type(e).__name__}: {e}"}
        ziel = os.environ.get("VP4_SELBSTTEST_DATEI")
        if ziel:
            Path(ziel).write_text(json.dumps(ergebnis, ensure_ascii=False), encoding="utf-8")
        fenster.destroy()

    threading.Thread(target=pruefen, daemon=True).start()


def main():
    _pakete_pruefen()
    _protokoll_einrichten()

    from api import VP4Api
    from dienst import VP4Dienst

    dienst = VP4Dienst()
    dienst.start()
    fenster_ref = {}
    api = VP4Api(dienst, lambda: fenster_ref.get("fenster"))

    if "--browser" in sys.argv:
        import time
        import dev_server
        _, adresse = dev_server.starten(api)
        print(f"VP4 läuft im Browser: {adresse}\nBeenden mit Strg+C.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            dienst.beenden()
        return

    import webview
    dunkel = dienst.einst.get("design") == "dark"
    try:
        fenster = webview.create_window(
            "VP4", url=str(_ui_ordner() / "index.html"), js_api=api,
            width=1280, height=820, min_size=(960, 620),
            background_color="#0B0D12" if dunkel else "#EEF1F8", text_select=True)
    except Exception as e:
        _meldung("VP4 startet nicht", f"Das Fenster ließ sich nicht öffnen:\n\n{e}")
        raise
    fenster_ref["fenster"] = fenster
    fenster.events.shown += lambda: _fenster_stil(fenster, dunkel)
    if os.environ.get("VP4_SELBSTTEST_START"):
        fenster.events.shown += lambda: _selbsttest(fenster)
    fenster.events.closed += lambda: dienst.beenden()
    try:
        webview.start(gui="edgechromium" if sys.platform == "win32" else None,
                      private_mode=False, storage_path=str(dienst.ordner / "webview"),
                      debug=bool(os.environ.get("VP4_DEBUG")))
    except Exception as e:
        _meldung("VP4 startet nicht",
                 "Das Fenster braucht die Microsoft Edge WebView2-Laufzeit. Auf Windows 11 ist sie "
                 "eingebaut, auf manchen Windows-10-PCs fehlt sie. Hier gibt es sie kostenlos:\n\n"
                 "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                 f"(Technischer Fehler: {e})")
        raise
    finally:
        dienst.beenden()


if __name__ == "__main__":
    main()
