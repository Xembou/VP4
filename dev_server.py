#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 dev_server.py - VP4 im normalen Browser (zum Entwickeln und Testen)
=====================================================================
    python VP4.py --browser

startet denselben Dienst und dieselbe VP4Api wie das Programmfenster,
nur dass die Seite im normalen Browser läuft und über HTTP mit Python
spricht. Gedacht für die Arbeit an der Oberfläche und für die
UI-Prüfung (tests/ui_pruefung.py) gegen echte Daten.

Sicherheit: Der Server hört nur auf 127.0.0.1, und jede Anfrage an
/api/ braucht ein zufälliges Token (steht nach # in der Adresse, wird
also nie an den Server geschickt, sondern von bruecke.js als Header
mitgegeben). Andere Webseiten im selben Browser kommen damit nicht an
die Schnittstelle - sie kennen das Token nicht, und ohne den Header
gibt es nur 403.
=====================================================================
"""

import functools
import http.server
import json
import secrets
import socketserver
import threading
import webbrowser
from pathlib import Path

UI = Path(__file__).resolve().parent / "ui"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, api=None, token="", **kwargs):
        self.api = api
        self.token = token
        super().__init__(*args, directory=str(UI), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        if not secrets.compare_digest(self.headers.get("X-VP4-Token", ""), self.token):
            self.send_error(403)
            return
        name = self.path[5:].split("?", 1)[0]
        laenge = min(int(self.headers.get("Content-Length") or 0), 64 * 1024 * 1024)
        try:
            args = json.loads(self.rfile.read(laenge) or b"[]")
        except ValueError:
            self.send_error(400)
            return
        funktion = getattr(self.api, name, None) if not name.startswith("_") else None
        if not callable(funktion):
            antwort = {"ok": False, "fehler": f"Unbekannte Funktion: {name}"}
        else:
            antwort = funktion(*args)
        roh = json.dumps(antwort, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def starten(api, port: int = 0, browser_oeffnen: bool = True):
    """Startet den Server; gibt (server, adresse) zurück."""
    token = secrets.token_urlsafe(24)
    handler = functools.partial(_Handler, api=api, token=token)
    server = _Server(("127.0.0.1", port), handler)
    adresse = f"http://127.0.0.1:{server.server_address[1]}/index.html#token={token}"
    threading.Thread(target=server.serve_forever, name="vp4-dev", daemon=True).start()
    if browser_oeffnen:
        webbrowser.open(adresse)
    return server, adresse
